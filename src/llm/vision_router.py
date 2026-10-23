"""视觉模型路由层：云端优先、本地兜底，支持运行时切换对比。

背景：用户要求"去掉 AI 游戏识别、回归纯全局屏幕识别"后，视觉链路只剩
"多模态模型看整张屏幕 → 一句话描述用户在做什么"。识别质量直接取决于模型。
本模块把多个视觉模型（云端免费接口 + 本地 Ollama 模型）收进一个列表，
由 config.yaml 的 vision_models 配置：

  vision_models:
    active: glm-4v-flash          # 当前启用（默认云端优先）
    models:
      - id: glm-4v-flash
        name: 智谱 GLM-4V-Flash（云端免费）
        provider: openai
        base_url: https://open.bigmodel.cn/api/paas/v4/
        api_key: ''
        model: glm-4v-flash
        timeout_s: 30
      - id: minicpm-o2.6
        name: 本地 MiniCPM-o 2.6
        provider: ollama
        model: openbmb/minicpm-o2.6:q4_K_M

行为：
- 视觉模型切换（webapp「模型设置」下拉）→ set_active()，热生效可直观对比。
- 云端优先、本地兜底：vision_describe 从 active 开始依次尝试，单个模型失败
  进入 60s 冷却（避免每次都先撞坏掉的云端等超时），全部失败才抛错。
- 实现了 LLMClient 的鸭子接口（vision_available / model_name / vision_describe），
  VisionAnalyzer 把它当普通 LLM 用，无需改动。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Callable, Optional

from ..config import Config
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

logger = logging.getLogger(__name__)


def _val(v):
    """兼容 property / method 双形态取值【2026-10-04 修 snapshot 每 2s 刷 TypeError】。

    视觉客户端的 vision_available / model_name 在不同实现里一会是 @property
    （本文件 VisionRouter 自己、部分 Client），一会是普通方法（旧接口约定）。
    snapshot() 原来写死成 `vision_available()` 调用 → 遇到 property 实现就是
    TypeError('bool' object is not callable)，webapp 的 get_vision_models /
    get_vision_settings 每 2 秒轮询每 2 秒炸一次（05:18 会话日志实锤）。
    与 2026-10-03 在 video_watch.py / media_ingest.py 修的是同一类 bug。
    """
    try:
        return v() if callable(v) else v
    except Exception:  # noqa: BLE001
        return None


# 单模型失败冷却：冷却期间跳过该模型（治"每次先试坏掉的云端等超时"）。
FAIL_COOLDOWN_S = 60.0

# 【2026-10-02】免费额度耗尽专用冷却：配额类失败不是"抖一下就好"，60s 后重试只会
# 反复撞墙刷屏（实测：qwen-vl 额度耗尽后每 60s 报一次"视觉模型失败"，日志被刷爆）。
# 这类失败冷却一段时间，并提醒用户去控制台处理 / 询问是否切换到还有额度的模型。
# ⚠️【2026-10-02 由 1800s(30min) 降到 300s(5min)】实测同一 Key 下文本模型全部可用、
# 只有视觉模型 FreeTierOnly（阿里云**按模型独立计额**），而额度实际重置比想象的快。
# 锁 30 分钟代价过大：期间**整个视觉能力全废**（看图/看视频/桌面看屏），用户只能
# 手动点「再试一次」或去控制台充值。5 分钟既能避免每分钟撞墙，又能让额度恢复后自动归队。
QUOTA_EXHAUSTED_COOLDOWN_S = 300.0

# ---- 错误分类已抽到共享模块 error_classify（聊天链路 dialogue 也用），这里 re-import 保持兼容 ----
from .error_classify import (  # noqa: E402,F401  # 共享分类：quota/auth/rate/timeout/other
    KIND_AUTH, KIND_QUOTA, KIND_RATE, KIND_TIMEOUT, KIND_OTHER,
    is_quota_exhausted, classify_error,
)


# ============================================================
# 失败分类 + 差异化冷却（2026-10-02，借鉴 LiteLLM RetryPolicy / allowed_fails_policy
# 与 One API「特定错误类型自动禁用渠道」）
# 核心思想：**不是所有失败都一样**——
#   auth/quota 是"确定性失败"，不会自愈，立刻长冷却、不重试；
#   rate/timeout 是"瞬时失败"，会自愈，需要连续失败若干次才冷却，且用指数退避。
# 此前一律 60s 冷却：401 无效 Key 也被反复重试，429 刚限流完又被撞。
# （类别常量与 classify_error 来自共享模块 error_classify，见文件头部 import）
# ============================================================

# 每类失败的冷却时长（秒）
COOLDOWN_BY_KIND = {
    KIND_AUTH: 3600.0,     # 1 小时：Key 无效不会自己变有效
    KIND_QUOTA: QUOTA_EXHAUSTED_COOLDOWN_S,  # 30 分钟，等用户去控制台处理
    KIND_RATE: 60.0,
    KIND_TIMEOUT: 60.0,
    KIND_OTHER: FAIL_COOLDOWN_S,
}
# 确定性失败（一次即冷却）；瞬时失败需累计到阈值才冷却（防单次抖动误杀）
IMMEDIATE_KINDS = (KIND_AUTH, KIND_QUOTA)
# 瞬时失败的连续失败阈值（达到才冷却）
ALLOWED_FAILS_TRANSIENT = 2
# 瞬时失败指数退避上限
MAX_TRANSIENT_COOLDOWN_S = 600.0

# 云端候选（并行竞速）；本地 ollama 仅作最后兜底（避免抢 GPU 显存）。
CLOUD_PROVIDERS = ("minicpm", "openai")

# 【历史说明·20-55 已移除】曾有 `_CLOUD_RACE_MAX = 3`（云端竞速限流）用于缓解
# "20 个云端条目同时并发把账号打爆"。**根因已从设计上解决**：视觉改为**单模型串行**
# ——就用用户在设置里绑定的那个（或我们预设的阿里云），失败才顺序试下一个，
# 任何时刻**只有一个请求**在飞。竞速限流因此不再需要，保留常量只会误导后来人。
# 若将来要加"按 key 去重/按厂商去重"（同一 key 的 20 个条目其实是同一服务），
# 应在**构建 _entries 阶段**去重，而不是靠竞速兜底。

# 1x1 红色像素 PNG：连接测试用的最小图片
_TEST_IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


# 【2026-10-04 18:55 破耦合·provider 别名归一（模块级，供构造与回填共用）】
# 此前 provider 必须精确等于 "openai"，否则 `_build_client` 抛错；而 webapp 的
# 视觉模型校验却把 `openai_compatible` / `dashscope` 也当"可用云端视觉"
# ⇒ 两侧口径不一致：用户按 UI 提示填了别名，整个 VisionRouter 构造失败、
# 所有视觉模型全灭。现统一归一。
_PROV_ALIAS = {
    "openai": "openai",
    "openai_compatible": "openai",
    "openai-compatible": "openai",
    "dashscope": "openai",
    "aliyun": "openai",
    "bailian": "openai",
}


def _build_client(cfg: Config, item: Any) -> Any:
    """按条目 provider 构造对应客户端。

    - ollama：OllamaClient(cfg, model=条目.model) 直接指定模型。
    - openai：OpenAIClient(cfg, base_url/api_key/model/timeout_s 覆盖条目值)。
    - minicpm：MiniCPMClient(cfg)（官方免费实时接口，无需 key）。
    """
    provider = str(getattr(item, "provider", "") or "").strip().lower()
    provider = _PROV_ALIAS.get(provider, provider)   # 归一，供下方回填与 CLOUD_PROVIDERS 判断
    model = str(getattr(item, "model", "") or "").strip()
    if provider == "openai":
        return OpenAIClient(
            cfg,
            base_url=str(getattr(item, "base_url", "") or ""),
            api_key=str(getattr(item, "api_key", "") or ""),
            model=model,
            timeout_s=float(getattr(item, "timeout_s", 0) or 0) or None,
            # 【2026-10-04 18:55 破耦合】显式给 vision=True：视觉路由里的条目**按定义
            # 就是能看图的**，不该继承 `llm.openai.vision`（那是给纯文本核心 LLM 的开关）。
            # 此前不传 ⇒ 改核心 LLM 的 vision 开关会连坐关掉全部 20 个视觉模型。
            vision=True,
        )
    if provider == "ollama":
        if not model:
            raise ValueError("ollama 视觉模型条目缺少 model")
        return OllamaClient(cfg, model=model)
    if provider == "minicpm":
        from .minicpm_client import MiniCPMClient
        return MiniCPMClient(
            cfg, endpoint=str(getattr(item, "endpoint", "") or "").strip())
    raise ValueError(f"未知视觉模型 provider: {provider!r}")


def create_vision_router(cfg: Config) -> "VisionRouter":
    """按 config.yaml 的 vision_models 构建视觉模型路由。"""
    return VisionRouter(cfg)


class VisionRouter:
    """视觉模型路由：多候选 + 失败冷却 + 运行时切换。"""

    name = "vision-router"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg  # 保留引用：运行时读 vision_models.enabled（视觉感知总开关）
        self._entries: list[dict[str, Any]] = []
        self._active_id: str = ""
        self._cooldown_until: dict[str, float] = {}  # id -> 冷却截止时间戳
        self._last_errors: dict[str, str] = {}       # id -> 最近错误
        self._last_used_id: str = ""                 # 最近一次实际成功调用者
        # 【2026-10-02】额度耗尽追踪：id -> 判定时间戳。用于长冷却 + 提醒去重
        # （同一模型只在首次判定 / 冷却结束后再次判定时提醒一次，不刷屏）。
        self._quota_exhausted: dict[str, float] = {}
        self._quota_alerted: dict[str, float] = {}   # id -> 上次提醒时间戳
        self._quota_alert_cb: Optional[Callable[[dict], None]] = None
        # 瞬时失败连续计数（id -> 次数）：达到 ALLOWED_FAILS_TRANSIENT 才冷却，
        # 避免单次网络抖动就把模型踢出路由（借鉴 LiteLLM allowed_fails）。
        self._fail_streak: dict[str, int] = {}
        # 游戏进行中检测回调（main.py 注入 ForegroundMonitor.is_game_foreground）：
        # 返回 True 时跳过所有本地 ollama 模型——用户在玩游戏时绝不加载本地大模型
        # 抢 GPU 显存卡顿（实锤：本地 minicpm-o2.6 4.8GB 100% GPU 导致三角洲卡死）。
        self._game_check: Optional[Callable[[], bool]] = None
        vm = getattr(cfg.ns, "vision_models", None)
        if vm is None:
            return
        self._active_id = str(getattr(vm, "active", "") or "")
        models = getattr(vm, "models", None) or []
        for item in models:
            try:
                client = _build_client(cfg, item)
            except Exception as e:  # noqa: BLE001 单条目构造失败不拖垮整个路由
                logger.warning("视觉模型条目构造失败: %s", e)
                continue
            self._entries.append({
                "id": str(getattr(item, "id", "") or ""),
                "name": str(getattr(item, "name", "") or getattr(item, "id", "")),
                # 【2026-10-04】回填**归一后**的 provider：否则别名列（如
                # `openai_compatible`）会在 CLOUD_PROVIDERS 判断（并行竞速 vs 顺序、
                # 云端/本地分类）里被判成"未知" → 该条目被当作本地模型处理，行为错乱。
                "provider": _PROV_ALIAS.get(
                    str(getattr(item, "provider", "") or "").strip().lower(),
                    str(getattr(item, "provider", "") or "").strip().lower()),
                "model": str(getattr(item, "model", "") or ""),
                "client": client,
            })
        if self._active_id and self._active_id not in {e["id"] for e in self._entries}:
            logger.warning("vision_models.active=%s 不在候选列表，回退第一个", self._active_id)
            self._active_id = ""

    def set_quota_alert_callback(self, fn: Optional[Callable[[dict], None]]) -> None:
        """注入"免费额度耗尽"提醒回调（webapp 用它弹前端询问浮层）。

        回调参数：{"model_id","model_name","candidates":[{"id","name"}],"error"}
        candidates = 当前还没被判定额度耗尽的其它候选，供用户选择切换目标。
        """
        self._quota_alert_cb = fn

    def set_game_check(self, fn: Callable[[], bool]) -> None:
        """注入"当前是否在玩游戏"回调：True 时跳过本地 ollama 模型（防抢 GPU）。"""
        self._game_check = fn

    # ==================== LLMClient 鸭子接口 ====================
    @property
    def available(self) -> bool:
        return bool(self._entries)

    def _perception_enabled(self) -> bool:
        """【2026-10-04 重构·按用户语义拆分】看图能力是否可用。

        此前这里叫"视觉感知总开关"，一个 `vision_models.enabled` 同时把守三条
        语义完全不同的通路（发图看图 / 桌面看屏 / agent 操作屏幕），导致：
          · 想让"发图能用"必须连带打开"后台常驻看屏"（白烧额度、隐私上不可接受）；
          · 关掉后整条视觉全死，用户以为是"模型没绑/坏了"。
        用户明确要求：**VLM 只要绑定了就默认可用**，不该再要一个总闸；
        只有「桌面看屏」这种后台常驻行为才需要单独的、默认关闭的开关。

        现在的语义：
          · `vision_models.enabled` 只保留**看图能力总可用性**（绑定了模型即 true），
            保留此键是为了兼容既有配置；未显式关闭时**默认 true**。
          · 桌面/后台看屏请用 `vision_models.screen_watch_enabled`（默认 false），
            由 `_screen_watch_enabled()` 判定。
        """
        try:
            raw = getattr(self.cfg, "raw", None) or {}
            vm = raw.get("vision_models") or {}
            return bool(vm.get("enabled", True))
        except Exception:  # noqa: BLE001
            return True

    def _screen_watch_enabled(self) -> bool:
        """桌面/后台看屏开关（权威：`perception.vision.desktop_watch_enabled`）。

        【2026-10-04 19:55 消除"两个看屏开关各说各话"】本项目历史上存在**两个**都叫
        "看屏开关"的键，管辖重叠、名字不同，用户在设置页只看得见其中一个：

          · `perception.vision.desktop_watch_enabled` —— **真正生效的那个**
            （`config.desktop_watch_effective()` 读它 → `runtime_status.vision_enabled`
            → `screen_capture` 桌面截图循环的开停）；
          · `vision_models.screen_watch_enabled` —— 10-04 拆分语义时新建的键，
            原本只被本函数读、**没有任何调用方**，等于"配了不起作用"。

        修法：以 `desktop_watch_enabled` 为权威，本键只作**补充位**（任一为真即开），
        两者都不写时按云端/本地档给默认值（云端关、本地开，与
        `config.desktop_watch_effective()` 的口径一致）。
        ⇒ 用户在设置页看到的那个开关，**现在真的控制着后台看屏**。
        """
        try:
            raw = getattr(self.cfg, "raw", None) or {}
            vm = raw.get("vision_models") or {}
            per_v = ((raw.get("perception") or {}).get("vision") or {})
            dw = per_v.get("desktop_watch_enabled")
            if dw is not None:
                return bool(dw)
            if "screen_watch_enabled" in vm:
                return bool(vm.get("screen_watch_enabled"))
            if "game_cloud_watch" in vm:
                return bool(vm.get("game_cloud_watch", False))
            # 两键都没写：沿用 config 的档位默认（云端关 / 本地开）
            return bool(getattr(self.cfg, "desktop_watch_effective", lambda: False)())
        except Exception:  # noqa: BLE001
            return False

    @property
    def vision_available(self) -> bool:
        """有任一候选可服务（可用且不在冷却）即视为视觉可用。

        【2026-10-04 修】原来写的是 `if not self._perception_enabled:` —— 少了
        一对括号，取到的是**方法对象**、恒为真 → 这个检查从来没生效过，于是
        "关掉视觉开关"时部分通路仍在偷偷调 VLM（还白烧额度）。改为带括号调用。
        注意：这里判的是**看图能力**（发图/看图），"桌面看屏"另有独立开关
        `_screen_watch_enabled()`，两者语义已解耦。
        """
        if not self._perception_enabled():
            return False
        return any(
            e["client"].vision_available and not self._in_cooldown(e["id"])
            for e in self._entries
        )

    @property
    def model_name(self) -> Optional[str]:
        e = self._active_entry()
        return e["client"].model_name if e else None

    async def vision_describe(self, image_bytes: bytes, prompt: str) -> str:
        """云端并行竞速 + 本地兜底。

        active 为云端模型时：所有云端候选（MiniCPM / 智谱等）同时发起
        请求，谁先成功用谁——避免单个云端排队超时卡住整条链路；云端全
        失败后才依次试本地 ollama（游戏进行中则跳过本地，防抢 GPU 显存）。
        active 为本地模型时：保持"active 优先、失败兜底其它"的顺序（手动
        切换本地对比效果时云端不抢）。失败的候选进冷却。
        """
        if not self._entries:
            raise RuntimeError("未配置任何视觉模型（config.yaml vision_models.models 为空）")
        errors: list[str] = []
        in_game = self._game_check is not None and self._game_check()
        active_e = self._active_entry()
        active_is_cloud = active_e is not None and active_e["provider"] in CLOUD_PROVIDERS

        # ---- 1) 云端不再并行竞速（【2026-10-04 20:55】用户明确要求去掉）----
        # 现在用的是**专业商业 API**（百炼等正式接口，有 SLA），不是当年"多个不确定的
        # 公开网关赛跑"；用户只绑定了**一个**模型（设置里选的，或我们预设的阿里云），
        # 20 个条目其实指向同一家服务 ⇒ 竞速等于对同一账号并发打 20 次 → 触发限流、
        # 全部 ReadTimeout（20:39-20:40 实锤），且桌面看屏循环每 2s 一张图更易打爆。
        # ⇒ 统一走下面第 2 步的**串行**顺序：active 优先，失败才试下一个，一次只发一个。
        if active_is_cloud and active_e is not None and not self._in_cooldown(active_e["id"]) \
                and active_e["client"].vision_available:
            try:
                text = (await active_e["client"].vision_describe(image_bytes, prompt)) or ""
                text = text.strip()
                if text:
                    self._clear_cooldown(active_e["id"])
                    self._last_used_id = active_e["id"]
                    return text
                errors.append(f"{active_e['name']}: 返回空")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                kind, cd = self._mark_fail(active_e["id"], str(exc))
                errors.append(f"{active_e['name']}: {exc}")
                if cd > 0:
                    logger.warning("视觉模型 %s 失败（%s，冷却 %.0fs 后重试）: %s",
                                   active_e["id"], kind, cd, exc)

        # ---- 2) 顺序兜底：active 优先逐个试，一次只发一个（不再并发） ----
        for e in self._ordered():
            # 云端竞速已失败过时不再重复；非竞速路径（active 本地）可顺序尝试云端
            if active_is_cloud and e["provider"] in CLOUD_PROVIDERS:
                continue
            # 游戏进行中禁用本地模型：用户在玩游戏时绝不加载本地大模型
            # 抢 GPU（实锤卡顿），宁可视觉暂时不可用等云端恢复。
            # 注意：跳过是"环境限制"不是"模型失败"，不计入 errors——
            # 否则云端瞬时抖一次 + 本地跳过两次就凑满失败阈值触发全局
            # 禁用（2026-08-24 实锤：打游戏时视觉被 VISION_DISABLED 哑火）。
            if e["provider"] == "ollama" and in_game:
                logger.info("游戏进行中，跳过本地视觉模型 %s（防抢 GPU 卡顿）", e["id"])
                continue
            if not e["client"].vision_available:
                errors.append(f"{e['name']}: 不可用")
                continue
            if self._in_cooldown(e["id"]):
                continue
            try:
                text = (await e["client"].vision_describe(image_bytes, prompt)) or ""
                text = text.strip()
                if not text:
                    errors.append(f"{e['name']}: 返回空")
                    continue
                self._clear_cooldown(e["id"])
                self._last_used_id = e["id"]
                return text
            except Exception as exc:  # noqa: BLE001
                kind, cd = self._mark_fail(e["id"], str(exc))
                errors.append(f"{e['name']}: {exc}")
                if cd > 0:
                    logger.warning("视觉模型 %s 失败（%s，冷却 %.0fs 后重试）: %s",
                                   e["id"], kind, cd, exc)
                else:
                    logger.warning("视觉模型 %s 失败（未达冷却阈值，继续重试）: %s",
                                   e["id"], exc)
        if not errors:
            # 无真实失败（云端全在冷却/本地被环境跳过）→ 返回空而非抛异常，
            # 让调用方按"本次无结果"处理，不计入失败计数（防误触发全局禁用）。
            return ""
        detail = " | ".join(errors)
        raise RuntimeError(self._explain_all_failed(detail))

    def _explain_all_cooling(self) -> str:
        """所有云端 VL 都在冷却中 —— 区分「额度耗尽」与「瞬时抖动」给不同说法。

        【2026-10-02】这是**用户主动发图时最常走**的一条路径（此前只抛
        "当前没有可用的云端视觉模型（可能都在冷却/额度耗尽）"——用户既不知道
        是自己额度问题、也不知道文字聊天还能用，更不知道要等多久）。
        """
        cooled = [e for e in self._entries if e["id"] in self._cooldown_until]
        # 冷却中的条目里，有多少是因为额度耗尽（vs 限流/超时等瞬时失败）
        _q = [e for e in cooled if e["id"] in self._quota_exhausted]
        if _q and len(_q) == len(cooled):
            left = max(0.0, max(self._cooldown_until.get(e["id"], 0) for e in cooled) - time.time())
            return ("我看图的模型（VLM）免费额度用完了，暂时看不了图。"
                    "文字聊天不受影响，照常可以用；"
                    f"额度类冷却约 {int(left // 60) + 1} 分钟，"
                    "也可以去阿里云控制台充值，或关闭「仅免费额度」模式改为按量计费。")
        return ("视觉模型都在冷却中（刚失败过），稍等一两分钟会自动恢复。"
                "文字聊天不受影响。")

    def _explain_all_failed(self, detail: str) -> str:
        """把"所有视觉模型都失败"翻译成一句**用户能据此行动**的话。

        【2026-10-02】原实现直接把各模型原始错误 ` | ` 拼起来甩给用户
        （"百炼 Qwen-VL-OCR（…）: ... 403 Forbidden [服务端:
        AllocationQuota.FreeTierOnly ...] | ..." × 5 个模型），又长又全是术语，
        还把"视觉额度用完"说成了"所有候选都不可用"——实测同一 Key 下**文本模型
        全部可用**（用户实锤：明明文字聊天正常，却被告知"其它候选暂不可用"）。

        这里按**失败类别**归纳，给出三件事：① 到底哪类能力挂了 ② 不受影响的部分
        ③ 下一步怎么办。原始错误压到最短，不刷屏。
        """
        low = str(detail or "").lower()
        if is_quota_exhausted(low):
            return ("我看图的模型（VLM）免费额度用完了，所以这次没能看图。"
                    "文字聊天不受影响，照常可以用；"
                    "想恢复看图/看视频/桌面看屏，去阿里云控制台给视觉模型充值，"
                    "或关闭「仅免费额度」模式改为按量计费（很便宜）。")
        if any(m in low for m in ("401", "unauthorized", "invalid", "forbidden", "无权限")):
            return ("视觉模型的 API Key 无效或没开通该模型权限，所以看不了图。"
                    "文字聊天不受影响。请到设置里检查视觉模型那一栏的 Key。")
        if any(m in low for m in ("429", "rate limit", "限流")):
            return ("视觉模型被限流了（请求太频繁），稍等一两分钟会自动恢复。"
                    "文字聊天不受影响。")
        if any(m in low for m in ("timeout", "timed out", "超时", "connection", "连接")):
            return ("连不上视觉模型（网络超时），可能是网络抖动，稍后会自动重试。"
                    "文字聊天不受影响。")
        # 非典型错误：保留原文但截断，避免甩一整屏
        return f"看图失败：{str(detail)[:120]}"

    async def vision_chat(self, images: list, text: str, system_prompt: str = "") -> str:
        """多模态对话（聊天发图/看图）：把图片字节 + 文本组成多模态消息，发给云端 VL 模型，
        返回自然语言回答（不走决策 JSON，直接当 AI_DECISION.text 播）。

        与 vision_describe（单图→一句描述）不同：这里是「图 + 文 → 对话式回答」，
        更贴近用户「发张图让达妮娅看并聊」。复用视觉路由的云端竞速 + 冷却/额度逻辑；
        仅 openai provider 的云端 VL 模型参与（qwen3-vl 等），本地 ollama/minicpm 不参与
        图片对话（避免格式不兼容）。全部失败才抛错（由 dialogue 兜底回文本或失败气泡）。
        """
        if not self._perception_enabled():
            # 【2026-10-23 修】10-04 那条注释写"改为动态取值"，但 `_vm = ...` 算出来后
            # **从未被使用**，抛出的仍是写死的那句——注释描述了一件没发生的事（死变量）。
            # 现状核实：`_perception_enabled()` 只读 `vision_models.enabled`（缺省 true），
            # 所以这句话目前**恰好是对的**。但不要让读者误以为它是动态的：将来
            # `_perception_enabled()` 若再加条件（桌面看屏/额度之类），这句提示会把用户
            # 引到错误的开关上。⇒ 删掉死变量，并把"只有这一个条件"写成显式断言式的注释。
            raise RuntimeError(
                "看图能力已关闭（视觉模型总开关 vision_models.enabled=false），"
                "请在设置里开启；已绑定视觉模型时默认是可用的。")
        if not self._entries:
            raise RuntimeError("未配置任何视觉模型（config.yaml vision_models.models 为空）")
        # 组装多模态消息：系统人设 + 用户(图 + 文)
        msgs: list[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        user_content = (text or "").strip() or "请描述这张图片里有什么。"
        msgs.append({"role": "user", "content": user_content,
                     "images": [bytes(x) for x in (images or [])]})
        errors: list[str] = []
        # 仅云端 openai VL 模型可用（qwen3-vl 等；minicpm/ollama 不参与图片对话）
        #
        # ⭐⭐⭐【2026-10-04 20:55 去掉"云端并行竞速"·改单模型串行】用户明确要求：
        # 现在用的是**专业商业 API**（百炼等，有 SLA、有免费额度但仍是正式接口），
        # **不是当年那种"多个不确定的公开网关赛跑"** —— 竞速既无收益又有害：
        #   · 用户只在设置里绑定了**一个**模型（或用我们预设的阿里云，同样只需一个 key），
        #     20 个条目其实指向同一家服务，竞速等于对同一账号并发打 20 次 → 触发限流；
        #   · 20:39-20:40 实锤因此全部 `httpx.ReadTimeout`、视觉对话整体超时，
        #     用户体感是"发了图没反应、消息也不回了"。
        # ⇒ 改为：**就用 active（设置/预设里选定的那个）单模型跑**；
        #   它失败才**串行**试下一个候选（不是并发），成功后立刻返回。
        #   行为上与"聊天里切换模型"的语义一致：用户选哪个就用哪个。
        cloud = [
            e for e in self._ordered()
            if e["provider"] == "openai"
            and e["client"].vision_available
            and not self._in_cooldown(e["id"])
        ]
        if not cloud:
            raise RuntimeError(self._explain_all_cooling())
        for e in cloud:
            try:
                ans = (await e["client"].chat(msgs) or "").strip()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                kind, cd = self._mark_fail(e["id"], str(exc))
                errors.append(f"{e['name']}: {exc}")
                if cd > 0:
                    logger.warning("视觉对话 %s 失败（%s，冷却 %.0fs）: %s",
                                   e["id"], kind, cd, exc)
                continue
            if not ans:
                errors.append(f"{e['name']}: 返回空")
                continue
            self._clear_cooldown(e["id"])
            self._last_used_id = e["id"]
            return ans
        detail = " | ".join(errors)
        raise RuntimeError(self._explain_all_failed(detail))


    async def test_model(self, model_id: str = "") -> tuple[bool, str]:
        """强制测试指定模型（绕过冷却）：用小图调一次 vision_describe。"""
        eid = model_id or self._active_id
        e = next((x for x in self._entries if x["id"] == eid), None)
        if e is None:
            return False, f"未找到视觉模型: {eid}"
        if not e["client"].vision_available:
            return False, f"{e['name']} 不可用（未配置/未 pull 模型）"
        try:
            text = await e["client"].vision_describe(
                _TEST_IMAGE, "用一句话描述这张图片是什么颜色。")
            self._clear_cooldown(eid)
            return True, f"{e['name']} 连接正常: {text.strip()[:80]}"
        except Exception as exc:  # noqa: BLE001
            self._mark_fail(eid, str(exc))
            return False, f"{e['name']} 调用失败: {exc}"

    # ==================== 切换 / 快照 ====================
    def set_active(self, model_id: str) -> tuple[bool, str]:
        """切换当前视觉模型（热生效，UI 用）。

        用户主动切到某个模型 = 想再给它一次机会，故清掉该模型的冷却与"额度耗尽"
        标记；真再次耗尽时会重新判定并再次提醒（受 30 分钟提醒去重约束）。
        """
        if model_id not in {e["id"] for e in self._entries}:
            return False, f"未找到视觉模型: {model_id}"
        self._active_id = model_id
        self._clear_cooldown(model_id)
        logger.info("视觉模型已切换为: %s（已清除冷却/额度标记）", model_id)
        return True, ""

    async def probe_model(self, model_id: str) -> dict:
        """健康探测（借鉴 LiteLLM health check）：绕过冷却发一次最小视觉请求。

        用 1x1 像素测试图 + 几个 token，成本可忽略。用途：用户在阿里云控制台
        充值/关掉"仅免费额度"模式后，点一下"再试一次"即可确认恢复并立即归队，
        不必等 30 分钟冷却自然过期。

        成功 → 清冷却/额度/提醒标记（走 _clear_cooldown），立即恢复路由；
        失败 → 按错误分类重新计冷却（quota 会续上 30 分钟），返回分类供提示。
        """
        e = next((x for x in self._entries if x["id"] == model_id), None)
        if e is None:
            return {"ok": False, "error": f"未找到视觉模型: {model_id}"}
        if not e["client"].vision_available:
            return {"ok": False, "error": f"{e['name']} 当前不可用（未启用或缺配置）"}
        try:
            text = (await e["client"].vision_describe(
                _TEST_IMAGE, "连接测试：请回复 ok")) or ""
            ok = bool(text.strip())
            err = "" if ok else "返回空"
        except Exception as exc:  # noqa: BLE001 探测失败按分类处理
            ok, err = False, str(exc)
        if ok:
            self._clear_cooldown(model_id)
            self._last_errors.pop(model_id, None)
            logger.info("视觉模型 %s 探测成功，已恢复路由", model_id)
            return {"ok": True, "reply": text.strip()[:60]}
        self._mark_fail(model_id, err)
        kind = classify_error(err)
        logger.info("视觉模型 %s 探测失败（%s）：%s", model_id, kind, err[:160])
        hint = {
            KIND_QUOTA: "免费额度已耗尽：去阿里云控制台充值或关闭「仅免费额度」模式",
            KIND_AUTH: "Key 无效或无权限：检查 Key 是否填对、是否开通该模型",
            KIND_RATE: "被限流：稍等片刻再试",
            KIND_TIMEOUT: "网络超时：检查网络后重试",
        }.get(kind, "调用失败，稍后重试")
        return {"ok": False, "kind": kind, "hint": hint, "error": err[:200]}

    def set_model_api_key(self, model_id: str, api_key: str) -> tuple[bool, str]:
        """热更新 OpenAI 兼容云端条目（智谱等）的 api_key，清除冷却与错误。"""
        api_key = (api_key or "").strip()
        for e in self._entries:
            if e["id"] != model_id:
                continue
            if e["provider"] != "openai":
                return False, f"{e['name']} 不是 OpenAI 兼容云端条目，无需 key"
            try:
                e["client"].set_api_key(api_key)
            except AttributeError:
                return False, f"{e['name']} 客户端不支持更新 api_key"
            self._clear_cooldown(model_id)
            self._last_errors.pop(model_id, None)
            logger.info("视觉模型 %s api_key 已热更新", model_id)
            return True, ""
        return False, f"未找到视觉模型: {model_id}"

    @property
    def active_id(self) -> str:
        return self._active_id

    def snapshot(self) -> dict:
        """运行状态快照（供 webapp 模型设置下拉显示，线程安全只读）。"""
        now = time.time()
        return {
            "active": self._active_id,
            "last_used": self._last_used_id,
            # 【2026-10-04 新增】两个开关各自独立上报，供设置页分别展示与控制：
            #   vision_enabled     → 看图能力（发图/看图），绑定模型即默认可用
            #   screen_watch_enabled → 桌面/后台看屏，默认关闭
            "vision_enabled": self._perception_enabled(),
            "screen_watch_enabled": self._screen_watch_enabled(),
            "models": [
                {
                    "id": e["id"],
                    "name": e["name"],
                    "provider": e["provider"],
                    "model": e["model"],
                    # 【2026-10-04 修】原来写 `self._perception_enabled`（方法对象、恒为真）
                    # → 逐模型的"可用"永远显示可用。补上括号才是真实判定。
                    "vision_available": (bool(_val(e["client"].vision_available))
                                         and self._perception_enabled()),
                    "model_name": _val(e["client"].model_name),
                    "api_key": getattr(e["client"], "api_key", "") or "",
                    "cooldown_until": self._cooldown_until.get(e["id"]),
                    "cooldown_left": max(0.0, self._cooldown_until.get(e["id"], 0) - now),
                    "last_error": self._last_errors.get(e["id"], ""),
                    # 免费额度是否已耗尽（供设置页/开发者页显示与"是否切换"判断）
                    "quota_exhausted": e["id"] in self._quota_exhausted,
                }
                for e in self._entries
            ],
        }

    # ==================== 内部 ====================
    def _active_entry(self) -> Optional[dict]:
        return next((e for e in self._entries if e["id"] == self._active_id), None)

    def _ordered(self) -> list[dict]:
        """active 优先，其余按配置顺序。"""
        if not self._active_id:
            return list(self._entries)
        return sorted(self._entries, key=lambda e: e["id"] != self._active_id)

    def _in_cooldown(self, eid: str) -> bool:
        return self._cooldown_until.get(eid, 0) > time.time()

    def _mark_fail(self, eid: str, err: str) -> tuple[str, float]:
        """记录一次失败并按错误分类施加冷却。

        返回 (错误类别, 实际冷却秒数)；冷却秒数 0 表示未达阈值不冷却（瞬时抖动）。
        供调用方打准确日志——此前调用方硬编码"冷却 60s"，quota 长冷却后
        又打一条 60s，日志自相矛盾误导排查（2026-10-02 实锤）。
        """
        now = time.time()
        self._last_errors[eid] = err[:200]
        kind = classify_error(err)

        if kind == KIND_QUOTA:
            # 额度耗尽：长冷却（不再每 60s 撞墙）+ 提醒用户（去重，不刷屏）
            self._cooldown_until[eid] = now + QUOTA_EXHAUSTED_COOLDOWN_S
            self._quota_exhausted[eid] = now
            self._fail_streak.pop(eid, None)
            logger.warning(
                "视觉模型 %s 免费额度已耗尽（%.0f 分钟内不再重试）：%s",
                eid, QUOTA_EXHAUSTED_COOLDOWN_S / 60.0, err[:160])
            self._fire_quota_alert(eid, err)
            return kind, QUOTA_EXHAUSTED_COOLDOWN_S

        if kind == KIND_AUTH:
            # Key 无效/无权限：不会自愈，长冷却且不重试（此前 401 也被 60s 反复重试）
            self._cooldown_until[eid] = now + COOLDOWN_BY_KIND[KIND_AUTH]
            self._fail_streak.pop(eid, None)
            logger.warning(
                "视觉模型 %s 认证失败（Key 无效或无权限），%.0f 小时内不再重试：%s",
                eid, COOLDOWN_BY_KIND[KIND_AUTH] / 3600.0, err[:160])
            return kind, COOLDOWN_BY_KIND[KIND_AUTH]

        # 瞬时失败（429 限流 / 超时 / 网络）：累计连续失败，达到阈值才冷却，指数退避
        streak = self._fail_streak.get(eid, 0) + 1
        self._fail_streak[eid] = streak
        if streak < ALLOWED_FAILS_TRANSIENT:
            logger.debug("视觉模型 %s 瞬时失败 %d/%d（未达冷却阈值）: %s",
                         eid, streak, ALLOWED_FAILS_TRANSIENT, (err or "")[:120])
            return kind, 0.0
        base = COOLDOWN_BY_KIND.get(kind, FAIL_COOLDOWN_S)
        # 指数退避：连续失败越多冷却越长（429/超时适用），封顶 10 分钟
        cd = min(base * (2 ** max(0, streak - ALLOWED_FAILS_TRANSIENT)),
                 MAX_TRANSIENT_COOLDOWN_S)
        self._cooldown_until[eid] = now + cd
        logger.warning("视觉模型 %s 连续失败 %d 次（%s），冷却 %.0fs：%s",
                       eid, streak, kind, cd, (err or "")[:120])
        return kind, cd

    def _fire_quota_alert(self, eid: str, err: str) -> None:
        """额度耗尽：**只记状态，不主动弹窗**（2026-10-02 用户决策）。

        【为什么停掉主动弹窗】额度耗尽是一个**持续状态**，不是一次性事件：
        只要没充值/没关「仅免费额度」，它会一直为真。而失败可能发生在
        桌面看屏、陪你看视频、聊天发图、视觉分析等**多条链路**上，各自有各自
        的重试与冷却 → 主动弹窗会：
          ① 在用户没做任何事时突然弹出（用户实锤：什么都没干就弹额度浮层）；
          ② 反复弹（各链路各判一次，去重键还各不相同）；
          ③ 措辞必然含糊——弹窗时用户不知道"这对我有什么影响"，只能说成
             "其它候选也暂不可用"，实际是"只有视觉类模型不可用，文字聊天没事"。

        【改成什么】额度信息只在**用户真的用到视觉、且失败的那一刻**，随那次
        失败消息一起给出（见 vision_describe / vision_chat 抛错文案）：
        说明"视觉模型免费额度用完了 + 文字聊天不受影响 + 怎么办"。
        失败消息天然带上下文、只出现一次、用户刚有感知时看到——不需要额外频次控制。

        本方法保留（不删）：`_quota_exhausted` 状态与冷却照旧生效，只是不再
        主动打扰。`quota_state()` 供将来"用户主动点『检测额度』"这类显式查询。
        """
        now = time.time()
        last = self._quota_alerted.get(eid, 0)
        if now - last < QUOTA_EXHAUSTED_COOLDOWN_S:
            return
        self._quota_alerted[eid] = now
        logger.info('[额度耗尽] %s 免费额度用尽（不主动弹窗；等用户用到视觉失败时'
                    '随失败消息提示）', eid)

    def quota_state(self) -> dict:
        """当前额度状态（供"用户显式点『检测额度』"这类按需查询，不主动推送）。"""
        return {
            "exhausted": [
                {"id": e["id"], "name": e["name"]}
                for e in self._entries if e["id"] in self._quota_exhausted
            ],
        }

    def _clear_cooldown(self, eid: str) -> None:
        self._cooldown_until.pop(eid, None)
        self._fail_streak.pop(eid, None)
        # 额度标记一并清除：调用点都是"模型实际成功 / 用户主动切回 /
        # 探测恢复"，都意味着给这个模型恢复机会（否则手动切回后浮层
        # 依然把它当"额度耗尽"排除出候选，永远切不回来）。
        self._quota_exhausted.pop(eid, None)
        self._quota_alerted.pop(eid, None)
