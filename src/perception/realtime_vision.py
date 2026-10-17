"""分层视觉·感知层：轻量视觉模型持续看屏（接近实时）。

与主视觉（VisionAnalyzer）分工：
- 本模块用轻量模型（如 openbmb/minicpm-o2.6）每 realtime_interval_s 秒分析一帧，
  产出"画面感知"摘要（用户在做什么 / 游戏局势），持续更新对话上下文与主动话术场景；
- 关键时刻（击杀/死亡/胜负）仍由 OCR 引信 + 大模型（minicpm-o2.6）裁决，保证精度不降。

模型不可用 / 连续失败达到上限时自动禁用本层，不影响主视觉链路。
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import time
from collections import deque
from typing import Any, Deque, Optional

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from ..llm.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

REALTIME_PROMPT = (
    "用一句简短中文（不超过30字）描述屏幕上的画面：用户正在做什么？"
    "如果是游戏画面，说清模式/局势/当前状态，例如'在三角洲行动里战斗，残血，还剩2队存活'。"
    "三角洲行动里队友名字是蓝色、敌人名字是红色。"
    "画面出现击杀播报或敌人倒下→必须写'用户击杀了一名敌人'；"
    "队友头像变灰或带❌→必须写'队伍有队友阵亡'；"
    "**只有画面明确显示玩家倒地/死亡时才写'用户已阵亡正在观战'**：死亡结算页、灰屏死亡视角、"
    "救援倒计时、HUD上的'等待救援'文字、观战标记。"
    "**玩家只是站立、在室内待着、蹲守、躲藏、静止时，【绝对不算】阵亡/等待救援**"
    "——画面安静不代表死了，别脑补'等待救援'；也**严禁写'可能阵亡/疑似阵亡'**"
    "这类不确定描述，开局/载具/过场画面都不是阵亡。"
    "**模式区分（别分析错）**：烽火地带（撤离玩法）撤离成功后的直升机/飞机接应"
    "（玩家抓绳索登机/跑向飞机）或乘车驶离=撤离演出动画，不是战斗——别写'攀爬攻击直升机/"
    "用绳索打飞机'（直升机只能用枪/机炮打）；**全面战场能开载具**（坦克/装甲车/直升机），"
    "画面里的载具/直升机是正常战斗内容，绝不是撤离动画。只有'撤离成功'提示或撤离点登机/上车才是撤离动画。"
    "**开局进场≠撤离**：玩家从运输机/直升机空降、跳伞、下飞机刚进入地图=开局进场，"
    "不是撤离，别写'撤离'；只有画面出现'撤离成功'或玩家在撤离点登机/上车准备离开地图才是撤离。"
    "看不清游戏名不要瞎猜（三角洲/使命召唤/和平精英很容易认错），统一写'射击游戏'；"
    "看不清具体模式也不要编造，写'撤离玩法'或'大战场'或'对局中'。"
    "如果是工作或浏览，直接说在做什么。"
    "只输出这一句话，不要任何【】标注，不要其他内容。"
)

# 轻量模型偶尔也会贴【胜利】之类的标注，但时刻判定统一交给大模型+OCR，这里剥掉
_LABEL_RE = re.compile(r"【[^】]{1,8}】")


def _is_garbage_summary(s: str) -> bool:
    """轻量模型输出质量过滤：纯符号/噪声（如模型损坏时输出一串 @）视为垃圾。

    垃圾摘要不进对话上下文（否则 AI 拿"当前画面=@@@@@"只能瞎编）；
    连续垃圾按失败计，达上限自动禁用本层。
    """
    t = s.strip()
    if not t or len(t) < 2:
        return True
    han = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", t))
    return han < max(1, len(t) * 0.3)


class RealtimeVision:
    """轻量视觉模型持续看屏循环（分层视觉感知层）。"""

    def __init__(self, bus: EventBus, cfg: Config, monitor: Optional[Any] = None) -> None:
        self._bus = bus
        self._cfg = cfg
        self._monitor = monitor  # ForegroundMonitor（可选）：游戏时裁剪抓屏窗口区域，避免串行
        v = cfg.ns.perception.vision
        self._enabled: bool = getattr(v, "realtime_enabled", False)
        self._model: str = str(getattr(v, "realtime_model", "") or "").strip()
        self._interval: float = float(getattr(v, "realtime_interval_s", 5))
        self._jitter: float = float(getattr(v, "realtime_jitter", 0.3))
        self._timeout_s: float = float(getattr(v, "realtime_timeout_s", 20))
        self._fail_limit: int = int(getattr(v, "realtime_fail_limit", 5))
        sc = cfg.ns.perception.screenshot
        self._monitor_index: int = sc.monitor_index
        self._downscale: int = getattr(sc, "downscale", 64)
        # 送轻量模型前把截图缩到最大宽度（全分辨率截图 token 数会超 3B 模型上下文）
        self._max_width: int = int(getattr(v, "realtime_max_width", 960))

        self._running: bool = True
        self._llm: Optional[OllamaClient] = None
        self._fail_count: int = 0
        self._disabled: bool = not self._enabled or not self._model
        self._last_hash: Any = None
        self._last_ts: float = 0.0

        # 调试统计
        self._call_count: int = 0
        self._skip_count: int = 0
        self._recent: Deque[dict] = deque(maxlen=20)

    @property
    def disabled(self) -> bool:
        return self._disabled

    def stop(self) -> None:
        self._running = False

    def _next_interval(self) -> float:
        return max(0.5, self._interval * random.uniform(1.0 - self._jitter, 1.0 + self._jitter))

    async def _verify_model(self) -> bool:
        """检查轻量模型是否已 pull（拉取失败/不存在则本层禁用）。"""
        try:
            import ollama
            client = ollama.Client(host=self._cfg.ns.ollama.host)
            models = [m.get("name") or m.get("model") for m in (client.list() or {}).get("models", [])]
            base = self._model.split(":")[0]
            for name in models:
                if name and (name == self._model or name.startswith(base + ":")):
                    return True
            logger.warning("分层视觉模型未安装: %s（先执行 ollama pull %s）", self._model, self._model)
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("分层视觉模型检查失败: %s", e)
            return False

    async def _describe_once(self) -> None:
        """抓一帧并让轻量模型描述（失败累计，达上限禁用本层）。"""
        if self._disabled or self._llm is None:
            return
        import mss
        import imagehash
        from PIL import Image

        try:
            sct = mss.mss()
            try:
                monitors = sct.monitors
                idx = min(self._monitor_index, len(monitors) - 1)
                region = monitors[idx]
                # 前台是游戏且窗口化时，只抓游戏窗口区域——避免桌面图标/任务栏/
                # 其他窗口文字混进视觉摘要（把画面误认成别的游戏，上下文串行）
                if self._monitor is not None and self._monitor.is_game_foreground():
                    rect = self._monitor.foreground_rect()
                    if rect:
                        left, top, w, h = rect
                        m = monitors[idx]
                        x0 = max(left, m["left"])
                        y0 = max(top, m["top"])
                        x1 = min(left + w, m["left"] + m["width"])
                        y1 = min(top + h, m["top"] + m["height"])
                        if x1 > x0 and y1 > y0:
                            region = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
                raw = await asyncio.to_thread(sct.grab, region)
            finally:
                sct.close()
            img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")

            # 缩小截图再送模型：全分辨率 token 数会超 3B 模型上下文（4096）
            if img.width > self._max_width:
                ratio = self._max_width / img.width
                img = img.resize((self._max_width, max(1, int(img.height * ratio))))

            # pHash 变化检测：画面几乎没变就跳过，省 GPU
            small = img.resize((self._downscale, self._downscale))
            h = imagehash.phash(small)
            if self._last_hash is not None and (h - self._last_hash) < 3:
                self._skip_count += 1
                return
            self._last_hash = h

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
        except Exception as e:  # noqa: BLE001
            logger.debug("分层视觉抓屏失败: %s", e)
            return

        self._last_ts = time.time()
        try:
            summary = await asyncio.wait_for(
                self._llm.vision_describe(buf.getvalue(), REALTIME_PROMPT),
                timeout=self._timeout_s,
            )
        except Exception as e:  # noqa: BLE001
            self._fail_count += 1
            logger.warning("分层视觉分析失败 (%d/%d): %s", self._fail_count, self._fail_limit, e)
            if self._fail_count >= self._fail_limit:
                self._disabled = True
                logger.warning("分层视觉连续失败达上限，已禁用（不影响主视觉）")
                await self._bus.publish(Event(EventType.ERROR, {
                    "code": "REALTIME_VISION_DISABLED",
                    "message": f"分层视觉连续失败 {self._fail_count} 次，已禁用",
                }, "realtime_vision"))
            return

        summary = (summary or "").strip()
        if not summary:
            return
        # 剥掉模型可能贴的时刻标注（时刻判定统一走大模型+OCR）
        summary = _LABEL_RE.sub("", summary).strip()
        if not summary:
            return

        # 质量过滤：模型输出纯噪声（如 qwen2.5vl:3b 损坏时输出一串 @）时丢弃，
        # 不注入对话上下文；连续垃圾按失败计，达上限禁用本层（避免 AI 拿垃圾画面瞎编）
        if _is_garbage_summary(summary):
            self._fail_count += 1
            logger.warning("分层视觉输出噪声(%r)，丢弃 (%d/%d)",
                           summary[:20], self._fail_count, self._fail_limit)
            if self._fail_count >= self._fail_limit:
                self._disabled = True
                logger.warning("分层视觉连续输出噪声达上限，已禁用（不影响主视觉）")
                await self._bus.publish(Event(EventType.ERROR, {
                    "code": "REALTIME_VISION_DISABLED",
                    "message": f"分层视觉连续输出噪声 {self._fail_count} 次，已禁用（模型可能损坏）",
                }, "realtime_vision"))
            return

        self._fail_count = 0  # 只有拿到有效摘要才清零失败计数
        self._call_count += 1
        self._recent.append({"ts": self._last_ts, "summary": summary})
        logger.debug("分层视觉感知: %s", summary)
        # 前台实时判定兜底：摘要像游戏但前台窗口不是游戏（如挂机游戏在后台、
        # 前台在看游戏视频/直播）→ 不判 game，避免按"用户在打游戏"互动乱说
        game_hint = _looks_like_game(summary)
        if game_hint and self._monitor is not None and not self._monitor.is_game_foreground():
            game_hint = False

        await self._bus.publish(Event(EventType.SCREEN_ACTIVITY, {
            "summary": summary,
            "app_hint": "game" if game_hint else "",
            "confidence": 0.6,
            "moment": None,
            "source": "realtime",
        }, "realtime_vision"))

    async def run(self) -> None:
        """持续看屏主循环。"""
        if self._disabled:
            logger.info("分层视觉已禁用（realtime_enabled=false 或未配置模型）")
            return
        self._llm = OllamaClient(self._cfg, model=self._model)
        if not self._llm.available:
            self._disabled = True
            return
        # 模型存在性检查失败时不要立即放弃：Ollama 可能是用户后手动启动的，
        # 周期性重试（间隔 30s）直到连上才开始看屏，启动时序问题可自愈
        if not await self._verify_model():
            logger.warning("分层视觉模型检查失败，30s 后自动重试（Ollama 可能未就绪）")
            while self._running:
                await asyncio.sleep(30)
                if await self._verify_model():
                    logger.info("分层视觉模型检查通过，开始持续看屏")
                    break
            else:
                return
        logger.info(
            "分层视觉启动: 模型=%s 间隔=%ss(±%d%%) 监视器=%s",
            self._model, self._interval, int(self._jitter * 100), self._monitor_index,
        )
        while self._running:
            try:
                await self._describe_once()
            except Exception:  # noqa: BLE001
                logger.exception("分层视觉循环异常")
            await asyncio.sleep(self._next_interval())

    def snapshot(self) -> dict:
        """运行状态快照（供调试台轮询，线程安全）。"""
        return {
            "enabled": self._enabled,
            "disabled": self._disabled,
            "model": self._model,
            "interval_s": self._interval,
            "call_count": self._call_count,
            "skip_count": self._skip_count,
            "fail_count": self._fail_count,
            "fail_limit": self._fail_limit,
            "last_ts": self._last_ts,
            "recent": list(self._recent),
        }


def _looks_like_game(summary: str) -> bool:
    """从轻量摘要粗判是否游戏画面（喂给主动话术/对话做 app_hint）。

    看视频/直播类场景（抖音/B站/主播打游戏）即使画面内容是游戏也不算——
    用户此刻在"看"不在"玩"（治"挂机游戏在后台、前台看游戏视频却乱说"）。
    """
    if any(k in summary for k in ("看视频", "观看", "直播", "主播", "抖音", "短视频", "视频")):
        return False
    return any(k in summary for k in ("游戏", "对局", "战斗", "击杀", "存活", "撤离", "模式", "战场", "小队", "排名"))
