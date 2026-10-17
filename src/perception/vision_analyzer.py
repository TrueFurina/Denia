"""视觉分析器：调用 MiniCPM-o 分析截图 → 屏幕活动摘要（纯感知）。

三层节流：asyncio.Lock 串行 + 最小调用间隔 + ActivityCache 去重。
连续超时达上限则全局禁用视觉，避免卡顿。
本模块只做"感知"（给 AI 一句话描述屏幕当上下文）；游戏关键时刻
（击杀/阵亡/胜负）由 GameOcrMonitor 驱动（OCR 引信 → 多模态一次裁决）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Optional

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from ..llm.base import LLMClient
from .activity_cache import ActivityCache
from ..companion.game_knowledge import SCREEN_TEXT_FRESH_S

logger = logging.getLogger(__name__)

VISION_PROMPT = (
    "仔细看这张屏幕截图，用一句简短的中文（不超过35字）描述用户当前正在做什么。"
    "例如：'在写代码''在看视频''在浏览网页''在聊天''在打游戏'。\n"
    "只看用户当前的动作和画面内容，不要猜测画面外的信息，不要评价，不要脑补。\n"
    "**忽略**环境装饰与背景（墙壁/建筑/植被/光线色调/天气/远景）。\n"
    "**最后只输出一行 JSON，不要任何其他内容（不要```代码块标记、不要解释）：**\n"
    '{"desc": "一句话描述", "mode": "其他", "scene": "未知", "moment": "无", '
    '"player_alive": true, "confirmed": true}\n'
    "字段说明：\n"
    "- desc：不超过35字的中文描述（就是之前的摘要内容）。\n"
    "- mode/scene/moment：一律写'其他'/'未知'/'无'（保留字段兼容旧协议，不再"
    "做任何游戏专项判定）。\n"
    "- player_alive：一律 true（只描述用户在做什么，不做存活判断）。\n"
    "- confirmed：你对画面描述的整体把握，画面模糊/看不清就 false。\n"
    "看不懂或看不清画面时输出："
    '{"desc": "画面不清晰，看不太清用户在做什么", "mode": "其他", "scene": "未知", '
    '"moment": "无", "player_alive": true, "confirmed": false}\n'
)

# 视觉结构化输出枚举（必须与 VISION_PROMPT 里的枚举一致）
# 全局禁用后的自动恢复间隔（秒）：连续失败超限禁用后，隔这么久自动重置
# 失败计数重试一次（2026-08-24 修复永久哑火）。
AUTO_RECOVER_S = 90
_VISION_MODES = {"红鼠窝", "全面战场", "烽火地带", "其他", "未知"}
_VISION_SCENES = {
    "大厅", "加载", "开局", "战斗", "搜刮", "撤离点",
    "登机", "结算", "观战", "死亡", "仓库", "未知",
}
_VISION_MOMENTS = {"击杀", "阵亡", "胜利", "失败", "队友全灭", "无"}


def _parse_vision_output(raw: str) -> tuple[str, Optional[dict]]:
    """解析视觉模型输出：尽量提取 JSON 结构化字段（方向②：结构化枚举）。

    返回 (desc, structured)。
    - desc：纯文本摘要（模型没输出 JSON 或解析失败时，用原文当摘要，保持旧行为）。
    - structured：{mode, scene, moment, player_alive, confirmed} 或 None。
    结构化字段是"视觉自己声明的枚举判断"，比事后从自由文本猜更可靠：
    识别率提升不靠让模型"描述更准"，而是让它在枚举里选——枚举外的
    脑补（'坚守阵地/护送博士'这类编造模式）直接落回'未知'，不污染下游。
    """
    text = (raw or "").strip()
    if not text:
        return "", None
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            obj = None
        if isinstance(obj, dict):
            desc = str(obj.get("desc") or "").strip()
            mode = str(obj.get("mode") or "")
            scene = str(obj.get("scene") or "")
            moment = str(obj.get("moment") or "无")
            structured = {
                "mode": mode if mode in _VISION_MODES else "未知",
                "scene": scene if scene in _VISION_SCENES else "未知",
                "moment": moment if moment in _VISION_MOMENTS else "无",
                "player_alive": bool(obj.get("player_alive", True)),
                "confirmed": bool(obj.get("confirmed", True)),
            }
            # desc 缺失时回退原文（模型有时把描述写 JSON 外）
            return (desc or text), structured
    return text, None

# ============================================================
# 注：本模块只做"感知"（把屏幕概括成一句话给 AI 当上下文）。
# 游戏关键时刻（击杀/阵亡/胜负）全部由 GameOcrMonitor 驱动：
# OCR 扫文字发现疑似状态 → 截图交给多模态一次裁决 → 确认后发布 moment。
# 这里不再独立判定 moment（避免视觉模型在模糊画面上脑补击杀/胜负，
# 也避免与 OCR 裁决重复触发）。


class VisionAnalyzer:
    def __init__(self, llm: LLMClient, bus: EventBus, cfg: Config,
                 monitor: Any = None, game_knowledge: Any = None) -> None:
        self._llm = llm
        self._bus = bus
        self._monitor = monitor  # 可选前台监听：摘要像游戏但前台不是游戏时降级 app_hint
        self._game_knowledge = game_knowledge  # 可选游戏知识库：OCR 真实文字喂给视觉模型
        # 可选游戏状态提供者（proactive 策略层）：给视觉 prompt 注入"上一帧画面/
        # 当前场景/本局战绩"上下文。视觉模型每帧独立看图、没有对局上下文才会
        # 在同一画面反复脑补（战斗/交火/阵亡），规则守卫越堆越多、误伤互动；
        # 把连续信息喂进去让模型"对比上一帧变化"判断，幻觉大降（治本方案）。
        self._state_provider = None
        self._last_desc: str = ""  # 上一帧画面摘要（连续帧对比的基准）
        # 游戏状态/连续帧上下文注入开关（config.yaml proactive.game_ai_interaction）：
        # 用户要求去掉 AI 游戏识别、回归纯全局屏幕识别后（game_ai_interaction=false），
        # 视觉模型每帧独立看图、不注入任何游戏提示/上一帧画面——上一帧描述可能
        # 携带游戏内容传染给下一帧（"在桌面说红薯窝打的怎么样"的根源之一：上一帧
        # 在游戏画面，切到桌面后描述仍带红鼠窝字样，视觉模型跟着继续脑补游戏）。
        self._state_hint_enabled: bool = self._read_game_ai_interaction(cfg)
        vision_cfg = cfg.ns.perception.vision
        self._min_interval: float = vision_cfg.min_interval_s
        self._cache = ActivityCache(vision_cfg.cache_ttl_s)
        # 单次视觉分析整体超时（2026-08-31 修复：MiniCPM 云端 WS 会挂起 60~180s
        # 不返回——不抛异常、fail_count 不涨、capture 循环被单次 analyze 卡死，
        # 画面感知停摆、时间线视觉条目全空。加短超时快速失败进 fail_count，
        # 连续超限触发禁用+自动恢复，capture 不再被卡住。）
        self._describe_timeout_s: float = float(
            getattr(vision_cfg, "describe_timeout_s", 15) or 15)
        self._lock = asyncio.Lock()
        self._last_call: float = 0.0
        self._fail_count: int = 0
        self._fail_limit: int = vision_cfg.timeout_fail_limit
        # 【2026-10-09 热生效修复】原实现把总闸在构造期快照成 bool，且 `_disabled=True`
        # 的自动恢复（见 analyze()）**不看用户开关**：用户在设置页关掉「看屏/视觉」后，
        # 只要过了 AUTO_RECOVER_S，这个标志就会被自行翻回 False，分析管线"自己复活"。
        # 现拆成两个来源：`_cfg` 实时读总闸（硬闸，用户意图）+ `_disabled`/`_disabled_at`
        # 只表达"因连续失败被临时熔断"（软闸，可自动恢复）。
        self._cfg = cfg
        self._disabled: bool = not cfg.runtime_status.vision_enabled
        # 禁用时间戳（自动恢复用）：0 = 未禁用/已恢复
        self._disabled_at: float = 0.0
        # OCR 文字注入视觉模型开关（config.yaml vision.ocr_text_hint）：把游戏
        # 画面真实文字（回合 X/7、撤离倒计时、"撤离成功"大字）作为上下文喂给
        # 视觉模型，文字与画面结合判断（真文字 > 画面脑补）。效果不好设 false
        # 关掉（回退纯视觉）。
        self._ocr_text_hint: bool = bool(
            getattr(vision_cfg, "ocr_text_hint", True))
        # 屏幕区域布局提示（config.yaml vision.hud_region_hints）：
        # "区域位置 → 语义"知识注入视觉 prompt，让模型看截图时对照区域理解
        # （红鼠窝顶部ABC小队/左上队伍HUD蓝己方红敌方/右上温度下方击杀播报栏），
        # 识别游戏局势不跑偏。可配置，按用户实际玩的游戏自定义。
        hints = list(getattr(vision_cfg, "hud_region_hints", None) or [])
        self._hud_hints_prompt = (
            "**屏幕区域布局速查（若画面是战术射击游戏如三角洲行动则适用，"
            "对照区域理解画面，别跑偏；不是此类游戏请忽略）：**\n"
            + "\n".join(f"- {h}" for h in hints)
            if hints else ""
        )
        # 最近分析结果（调试台时间线，最多 20 条）
        self._recent: deque = deque(maxlen=20)
        # 统计
        self._call_count: int = 0
        self._dedup_skip: int = 0
        self._busy_skip: int = 0      # 单飞丢帧次数（有请求在途时跳过）
        self._unavail_skip: int = 0   # 视觉不可用而跳过的次数（网关拥堵/冷却）

    @property
    def disabled(self) -> bool:
        return self._disabled

    @staticmethod
    def _read_game_ai_interaction(cfg: Config) -> bool:
        """读取 proactive.game_ai_interaction 开关（是否注入游戏状态上下文）。

        默认关闭（用户要求去掉 AI 游戏识别、回归纯全局屏幕识别）：不注入
        上一帧画面/游戏状态，视觉模型每帧独立看图、不给任何游戏提示。
        读取失败静默回退关闭（宁可不注入，不引入游戏脑补来源）。
        """
        try:
            pro_cfg = getattr(cfg.ns, "proactive", None)
            if pro_cfg is None:
                return False
            return bool(getattr(pro_cfg, "game_ai_interaction", False))
        except Exception:  # noqa: BLE001
            return False

    def bind_game_knowledge(self, knowledge: Any) -> None:
        """运行时绑定游戏知识库（OCR 真实文字注入视觉模型的数据源）。

        analyzer 在 knowledge 创建前实例化（main.py 组件顺序），知识库
        就绪后调用本方法绑定。未绑定或开关关闭时 OCR 提示不注入（纯视觉）。
        """
        self._game_knowledge = knowledge

    def bind_state_provider(self, provider: Any) -> None:
        """运行时绑定游戏状态提供者（proactive 策略层，见 vision_context）。

        analyzer 在 proactive 创建前实例化（main.py 组件顺序），策略层
        就绪后调用本方法绑定。提供者给视觉 prompt 注入"当前场景/本局战绩/
        最近时刻"上下文，让模型基于连续信息判断画面变化（治无上下文瞎猜）。
        """
        self._state_provider = provider

    def _build_state_hint(self) -> str:
        """游戏对局上下文注入：上一帧画面 + 策略层场景/战绩状态。

        视觉模型每帧独立看一张图、没有任何对局上下文，才会在同一画面反复
        脑补"战斗/交火/阵亡"（幻觉根源），结果规则守卫越堆越多、误伤互动。
        把上一帧摘要和当前场景/本局战绩/最近时刻喂进去，让模型"对比上一帧
        的变化"来判断：画面连续就保持描述、真有状态转折才写场景变化/时刻。
        【开关关闭（game_ai_interaction=false，纯全局识别）时返回空】——
        用户要求不给 AI 任何游戏提示；上一帧描述可能携带游戏内容传染给
        下一帧（"桌面说红薯窝"实锤），一律不注入。
        """
        if not self._state_hint_enabled:
            return ""
        if self._state_provider is None and not self._last_desc:
            return ""
        parts = []
        if self._last_desc:
            parts.append(f"上一帧画面: {self._last_desc[:120]}")
        if self._state_provider is not None:
            ctx = getattr(self._state_provider, "vision_context", None)
            try:
                s = ctx() if callable(ctx) else ""
            except Exception:  # noqa: BLE001 提供者异常时静默回退纯视觉
                s = ""
            if s:
                parts.append(s)
        if not parts:
            return ""
        return ("\n**游戏对局上下文（上一帧画面/当前场景/本局战绩，用于对比判断"
                "画面变化；与当前画面内容矛盾时以当前画面为准）：**\n"
                + "\n".join(f"- {p}" for p in parts))

    def _build_ocr_hint(self) -> str:
        """从游戏知识库取最近一帧 OCR 真实文字，拼成给视觉模型的上下文提示。

        OCR 是游戏自己渲染的文字（顶部"回合 X/7"、撤离倒计时、底部击杀播报、
        "撤离成功"大字等），比视觉模型在模糊画面上脑补可靠。取各区域文字、
        清洗（去空白）后拼一段，让模型"看图 + 看字"结合判断。
        任一条件不满足返回空串（不注入）：
          - 开关关闭 / 未绑定知识库；
          - OCR 文字过旧（超过 SCREEN_TEXT_FRESH_S，防旧画面快照残留污染）；
          - 所有区域文字都太短（无信息量 / 手机共享下 OCR 扫不到）。
        """
        if not self._ocr_text_hint or self._game_knowledge is None:
            return ""
        try:
            age = self._game_knowledge.screen_text_age()
            if age is None or age > SCREEN_TEXT_FRESH_S:
                return ""
            texts = self._game_knowledge.screen_texts
        except Exception:  # noqa: BLE001 知识库未初始化/接口缺失时静默回退纯视觉
            return ""
        parts = []
        for area, label in (("top", "顶部HUD"), ("center", "中央"),
                            ("bottom", "底部"), ("corner", "左上角")):
            t = re.sub(r"\s+", "", str(texts.get(area) or ""))
            if len(t) >= 2:
                parts.append(f"{label}:{t}")
        if not parts:
            return ""
        return "；".join(parts)[:200]

    def _game_hint(self, summary: str) -> str:
        """计算 app_hint，并把"前台不是游戏却像游戏"的场景降级。

        用户挂着游戏在后台、前台在看抖音/B站里的游戏视频或直播时，
        摘要会像游戏（如"在抖音观看游戏视频"），但不能判 game——
        否则会按"用户在打游戏"互动乱说。看视频类摘要由 _guess_app_hint
        优先判 browser；这里再以前台实时判定兜底一层。
        """
        hint = _guess_app_hint(summary)
        if hint == "game" and self._monitor is not None and not self._monitor.is_game_foreground():
            return ""
        return hint

    async def analyze(self, img_bytes: bytes, force: bool = False) -> Optional[dict]:
        """分析截图，返回 {summary, app_hint, confidence, moment?} 或 None（被节流/禁用）。

        force=True 时跳过最小调用间隔（用于前台切换等事件驱动场景），
        但依旧受锁与 ActivityCache 去重约束，避免无效调用。
        """
        # 【2026-10-09 热生效】硬闸（用户开关）优先：关掉看屏/视觉后立即拒绝分析，
        # 且不受下面 AUTO_RECOVER_S 自动恢复影响——自动恢复只该治"连续失败熔断"，
        # 不该推翻用户在设置页的选择。
        # 本方法**只被 screen_capture 调用**（桌面截图循环），发图走
        # VisionRouter.vision_describe，另一条路，故这里用总闸不会误伤「看图」。
        try:
            if not bool(self._cfg.runtime_status.vision_enabled):
                self._disabled = True
                self._disabled_at = 0.0      # 0 表示"非失败熔断"，自动恢复不会动它
                return None
            # 开关重新打开 → 解除"因用户关闸"造成的禁用（否则 _disabled_at==0
            # 永远进不了自动恢复分支，重开后分析线永久哑火）。
            if self._disabled and self._disabled_at == 0.0:
                self._disabled = False
                self._fail_count = 0
        except Exception:  # noqa: BLE001
            pass
        # 全局禁用自动恢复：连续失败超限后每 AUTO_RECOVER_S 尝试重置重试一次
        # （2026-08-24 修复：原逻辑 _disabled=True 后永不恢复，云端瞬时抖动/
        # 网络恢复后感知线仍永久哑火，必须重启才活）。
        if self._disabled:
            # 仅在"因失败熔断"（_disabled_at > 0）时才允许自动恢复；`_disabled_at == 0`
            # 说明是用户开关关掉的，必须等用户自己开（否则"关了又自己活"）。
            if self._disabled_at > 0 and time.time() - self._disabled_at >= AUTO_RECOVER_S:
                self._disabled = False
                self._fail_count = 0
                self._disabled_at = 0.0
                logger.warning("视觉分析: 禁用超 %ds，自动重置失败计数并恢复重试",
                               AUTO_RECOVER_S)
            else:
                return None
        if not self._llm.vision_available:
            # 云端不可用（未配置 / 网关无空闲 worker / 连续超时冷却中）：静默跳过，
            # **不计入失败计数**——这是"环境不具备条件"而非"模型出错"。
            # 若计失败，网关短暂拥堵就会凑满阈值把视觉全局禁用，恢复后还得等
            # AUTO_RECOVER_S 才活（2026-08-31：网关 worker 被僵尸会话占死数小时，
            # 期间每次尝试都失败 → 视觉反复被禁用，时间线全空）。
            self._unavail_skip += 1
            return None

        # Unity 伴侣窗口（本软件 3D 模型）在前台：画面是伴侣自身，不是用户
        # 活动。跳过分析——否则视觉模型会把二次元模型画面脑补成"打游戏/
        # 看视频/直播"，摘要注入对话上下文导致 AI 乱聊游戏、抖音
        # （用户实锤："搁那说游戏的""又说抖音"）。不在感知能力范围内。
        if self._monitor is not None and self._monitor.is_unity_foreground():
            logger.debug("Unity 伴侣窗口在前台，跳过视觉分析")
            return None

        # 【单飞（2026-08-31）】已有请求在途就直接丢帧，绝不排队等锁。
        # 原实现用 `async with self._lock` 让后来的调用阻塞等待：云端视觉慢/卡时
        # 截图循环被整体拖住（画面感知停摆），且解锁后会把积压的旧帧连着发一遍
        # （旧帧已无意义，还额外占队列名额）。视觉是"最新帧才有价值"的采样任务，
        # 忙时丢帧是正确的取舍。
        if self._lock.locked():
            self._busy_skip += 1
            return None
        async with self._lock:
            now = time.time()
            if not force and now - self._last_call < self._min_interval:
                return None  # 最小间隔节流
            self._last_call = now
            self._call_count += 1

            # OCR 真实文字注入：与画面内容一起给模型，文字是游戏自己渲染的
            # （回合 X/7、撤离倒计时、"撤离成功"大字），比模糊画面上脑补可靠。
            ocr_hint = self._build_ocr_hint()
            ocr_prompt = (
                f"\n**屏幕真实OCR文字（最近一帧，辅助判断，与画面内容矛盾时"
                f"以OCR文字为准）：** {ocr_hint}"
                if ocr_hint else ""
            )
            # 游戏对局上下文注入：上一帧画面 + 当前场景/本局战绩，让模型对比
            # 连续帧判断（治"每帧独立看图瞎猜 + 规则堆叠"的恶性循环）。
            state_prompt = self._build_state_hint()
            try:
                summary = await asyncio.wait_for(
                    self._llm.vision_describe(
                        img_bytes, VISION_PROMPT + self._hud_hints_prompt
                        + state_prompt + ocr_prompt),
                    timeout=self._describe_timeout_s)
            except asyncio.TimeoutError:
                self._fail_count += 1
                logger.warning(
                    "视觉分析超时 (%.0fs 无响应，云端视觉可能挂起): %d/%d",
                    self._describe_timeout_s, self._fail_count, self._fail_limit)
                if self._fail_count >= self._fail_limit:
                    self._disabled = True
                    self._disabled_at = time.time()
                    await self._bus.publish(Event(EventType.ERROR, {
                        "code": "VISION_DISABLED",
                        "message": f"视觉连续超时 {self._fail_count} 次，已全局禁用"
                                   f"（{AUTO_RECOVER_S}s 后自动恢复重试）",
                    }, "vision"))
                return None
            except Exception as e:  # noqa: BLE001
                self._fail_count += 1
                logger.warning("视觉分析失败 (%d/%d): %s", self._fail_count, self._fail_limit, e)
                if self._fail_count >= self._fail_limit:
                    self._disabled = True
                    self._disabled_at = time.time()
                    await self._bus.publish(Event(EventType.ERROR, {
                        "code": "VISION_DISABLED",
                        "message": f"视觉连续失败 {self._fail_count} 次，已全局禁用"
                                   f"（{AUTO_RECOVER_S}s 后自动恢复重试）",
                    }, "vision"))
                return None

            summary = (summary or "").strip()
            if not summary:
                return None
            # 方向②：视觉输出结构化枚举（mode/scene/player_alive/confirmed）。
            # 解析失败时回退纯文本摘要（保持旧行为，不阻断感知链路）。
            summary, structured = _parse_vision_output(summary)
            if not summary:
                return None
            # 记录上一帧摘要：供下一帧的"对局上下文"注入（连续帧对比的基准）
            self._last_desc = summary
            self._fail_count = 0
            self._disabled_at = 0.0

            # 感知纯输出摘要，不再解析/判定 moment——游戏关键时刻全部由
            # GameOcrMonitor（OCR 引信 → 多模态裁决）驱动，这里只提供画面
            # 上下文给 AI（"在玩三角洲行动的撤离玩法，正在搜物资"等）
            moment = ""

            app_hint = self._game_hint(summary)

            # 去重：相同活动 TTL 内不重复广播
            cache_key = summary
            if self._cache.should_skip(cache_key):
                self._dedup_skip += 1
                logger.debug("活动未变化，跳过: %s", summary)
                return None
            self._cache.set(cache_key)

            result = {
                "summary": summary,
                "app_hint": app_hint,
                "confidence": 0.8,
                "moment": None,
                # 方向②：结构化枚举判断（视觉直接声明，下游做模式锁定/场景/
                # 置信度分层时优先用它，替代从自由文本猜）。解析失败时为 None。
                "structured": structured,
            }
            self._recent.append({
                "ts": now,
                "summary": summary,
                "app_hint": result["app_hint"],
                "confidence": result["confidence"],
                "moment": result["moment"],
                # 来源戳：这条描述实际由哪路模型产出。逐事件标注而不是只在
                # snapshot 里给一个"当前值"——VisionRouter 云端失败会兜底到
                # 本地，兜底前后的事件来源不同，混用一个"当前链路"就是说谎。
                "source": self._vision_source(),
            })
            logger.info(
                "屏幕活动: %s%s", summary,
                f" [时刻:{moment}]" if moment else "",
            )
            await self._bus.publish(Event(EventType.SCREEN_ACTIVITY, result, "vision"))
            return result

    # 看屏链路来源归类：provider → 云端/本地。口径与 vision_router 的
    # CLOUD_PROVIDERS 一致——openai 兼容云端（智谱/百炼/dashscope 等，_PROV_ALIAS
    # 已归一）与 minicpm 官方实时接口都是云端；ollama 是本地。
    _CLOUD_PROVIDERS = {"openai", "openai_compatible", "openai-compatible",
                        "dashscope", "aliyun", "bailian", "minicpm"}
    _LOCAL_PROVIDERS = {"ollama", "local", "llama", "vllm"}

    def _vision_source(self) -> dict:
        """当前看屏链路来源：哪路模型、云端还是本地（供「感知活动」标注来源）。

        真相源是注入的 LLM——main.py 传的是 VisionRouter（云端优先、本地兜底）：
        - router：读 snapshot() 的 last_used（最近一次**实际成功**的调用者；
          云端失败兜底到本地时它才是真相，active 只是"当前选定"），回退 active。
        - 普通 LLMClient（测试桩/未来直连模式）：退化为 name / model_name。

        全程 getattr + try 防御：任何一环缺失都返回空字段，绝不让本方法抛错——
        它会被 snapshot()（get_vision_debug 每 1s 轮询）和每次 _recent.append
        调用，抛错会同时干掉调试台和感知记录。
        """
        out = {"provider": "", "model": "", "label": "", "local": None,
               "active_id": "", "last_used_id": ""}
        llm = self._llm
        snap = getattr(llm, "snapshot", None)
        if callable(snap):
            try:
                vs = snap() or {}
            except Exception:  # noqa: BLE001
                vs = {}
            models = vs.get("models") or []
            active = str(vs.get("active") or "")
            used = str(vs.get("last_used") or "")
            out["active_id"] = active
            out["last_used_id"] = used
            entry = None
            for want in (used, active):
                if want:
                    entry = next((m for m in models
                                  if str(m.get("id") or "") == want), None)
                    if entry is not None:
                        break
            if entry is None and models:
                entry = models[0]
            if entry is not None:
                out["provider"] = str(entry.get("provider") or "")
                out["model"] = str(entry.get("model") or entry.get("model_name") or "")
                out["label"] = str(entry.get("name") or "")
        else:
            out["provider"] = str(getattr(llm, "name", "") or "")
            try:
                mn = getattr(llm, "model_name", "")
                out["model"] = str(mn() if callable(mn) else (mn or ""))
            except Exception:  # noqa: BLE001
                out["model"] = ""
            out["label"] = out["model"] or out["provider"]
        prov = out["provider"].strip().lower()
        if prov in self._CLOUD_PROVIDERS:
            out["local"] = False
        elif prov in self._LOCAL_PROVIDERS:
            out["local"] = True
        return out

    def snapshot(self) -> dict:
        """运行状态快照（供调试台轮询，线程安全）。"""
        return {
            "disabled": self._disabled,
            "vision_available": bool(getattr(self._llm, "vision_available", False)),
            # 看屏链路来源（云端/本地 + 模型）：开发者页「感知活动」标注用
            "vision_source": self._vision_source(),
            "min_interval_s": self._min_interval,
            "cache_ttl_s": getattr(self._cache, "_ttl", None),
            "fail_count": self._fail_count,
            "fail_limit": self._fail_limit,
            "call_count": self._call_count,
            "dedup_skip": self._dedup_skip,
            "busy_skip": self._busy_skip,
            "vision_unavailable_skip": self._unavail_skip,
            "last_call_ts": self._last_call,
            "recent": list(self._recent),
        }


def _guess_app_hint(summary: str) -> str:
    """从摘要简单推测应用类型（供 Unity 显示气泡）。

    注意：看视频/直播类场景（抖音/B站/主播打游戏）优先判 browser——
    即使画面内容是游戏（如主播打三角洲），用户此刻是在"看"不是"玩"，
    不能按游戏互动（治"挂机游戏在后台、前台看游戏视频却乱说"）。
    """
    s = summary.lower()
    if any(k in summary for k in ("代码", "IDE", "编辑器", "编程")) or "vscode" in s:
        return "ide"
    if any(k in summary for k in ("抖音", "直播", "主播", "看视频", "短视频",
                                  "浏览器", "网页", "网站", "B站", "bilibili")):
        return "browser"
    if any(k in summary for k in ("游戏",)):
        return "game"
    if any(k in summary for k in ("文档", "阅读", "PDF", "word")):
        return "document"
    if any(k in summary for k in ("聊天", "微信", "QQ")):
        return "chat"
    return ""
