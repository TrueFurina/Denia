"""屏幕截图循环：随机节奏采样，pHash 检测变化，变化/静态确认时送视觉分析。

采样节奏设计（优化）：
  1. 随机抖动：基础间隔 ±40% 随机，看屏幕的节奏不机械
  2. 静态加速：画面长时间无变化（static_streak_s）→ 采样间隔缩短
     （更频繁确认画面，避免错过微小变化），并周期性补视觉分析
  3. 三层节流兜底：截图层 → pHash 变化检测层 → vision_analyzer 内部（20s + 去重）
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from typing import Callable, Optional

from ..config import Config
from ..event_bus import EventBus
from .privacy import privacy_mode, sensitive_skip_reason
from .vision_analyzer import VisionAnalyzer

logger = logging.getLogger(__name__)


class ScreenCapture:
    def __init__(self, bus: EventBus, analyzer: VisionAnalyzer, cfg: Config,
                 throttle_check: Optional[Callable[[], bool]] = None,
                 low_rate_check: Optional[Callable[[], bool]] = None,
                 foreground_check: Optional[Callable[[], tuple]] = None) -> None:
        self._bus = bus
        self._analyzer = analyzer
        sc = cfg.ns.perception.screenshot
        self._interval: float = sc.interval_s
        # 【敏感窗口跳过（2026-10-20）】取「前台进程名 + 窗口标题」的回调。
        # 采集前先问一句"用户现在是不是在网银/密码管理器里"，是就整帧不抓。
        # 回调由 main.py 装配时注入（monitor.foreground_process / foreground_title）；
        # 没注入时本功能静默不生效，所以 tools/check_all.py 有一条闸门守"两处装配都传了"。
        self._foreground_check = foreground_check
        _skip_raw = getattr(sc, "skip_apps", None)
        # None（字段缺失）→ 用默认清单；[]（用户显式清空）→ 关闭本功能
        self._skip_apps = tuple(_skip_raw) if _skip_raw is not None else None
        # 【游戏前台/锁快照 让位回调（2026-08-25 修复抽帧/核显本卡炸）】
        self._throttle_check = throttle_check
        # 【低频档回调（2026-10-13）】与 throttle_check 解耦：throttle 决定"要不要
        # 送云端视觉分析"，low_rate 决定"用多快的节奏抓屏"。云端 VLM 接管看屏时
        # （throttle=False）仍须走低频档，否则画面静止会触发 static 加速到 ~1s，
        # 每帧全屏抓屏+缩放+pHash+JPEG 编码 = 2026-08-25 修掉的"抽帧/核显本卡炸"。
        self._low_rate_check = low_rate_check
        self._game_interval_s = float(getattr(sc, "game_interval_s", 6.0))
        self._jitter: float = getattr(sc, "interval_jitter", 0.4)
        self._static_streak_s: float = getattr(sc, "static_streak_s", 90)
        self._static_scale: float = getattr(sc, "static_interval_scale", 0.5)
        self._static_analyze_every: int = getattr(sc, "static_analyze_every", 2)
        self._monitor_index: int = sc.monitor_index
        self._downscale: int = sc.downscale
        self._threshold: int = sc.change_threshold
        # 【2026-10-09 热生效修复】原实现把开关**在构造期快照**成 bool，此后永不再读：
        # 用户在设置页关掉「看屏互动」后，runtime_status.vision_enabled 已被
        # webapp.set_desktop_watch_enabled 改成 False，但 capture_loop 的 while 里
        # 从不重读 ⇒ 截图/分析管线照旧运行（实锤："关了看屏，它还在跑"）。
        # 现改为持有 Config、每次实时求值；保留 self._enabled 作为只读快照供调试台展示。
        self._cfg = cfg
        self._enabled: bool = cfg.runtime_status.vision_enabled
        self._last_hash = None
        self._unchanged_since: Optional[float] = None   # 画面开始无变化的时刻
        self._static_sample_count: int = 0              # 静态期采样计数（周期性补分析）
        self._running: bool = True
        self._sct = None                                # mss 实例（懒加载）
        self._grab_lock = asyncio.Lock()                # 串行抓屏，避免并发抢同一实例
        # 调试统计
        self._last_ts: float = 0.0                      # 最近一次采样时刻
        self._last_interval: float = self._interval     # 最近一次实际采样间隔
        self._sample_count: int = 0                     # 累计采样次数
        self._change_count: int = 0                     # 累计变化次数
        self._analyze_count: int = 0                    # 累计送分析次数
        self._event_trigger_count: int = 0              # 事件驱动抓屏次数
        self._analyze_task: Optional[asyncio.Task] = None  # 后台视觉分析任务
        self._analyze_drop: int = 0                     # 后台分析忙时丢帧计数
        # 敏感窗口跳过统计（可审计：用户能查"到底因为什么没看"）
        self._skip_count: int = 0                       # 累计因敏感窗口跳过次数
        self._skip_last: str = ""                       # 最近一次命中的清单条目
        self._skip_last_ts: float = 0.0                 # 最近一次跳过时刻

    def stop(self) -> None:
        self._running = False
        if self._analyze_task is not None and not self._analyze_task.done():
            self._analyze_task.cancel()
        self._analyze_task = None
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
            self._sct = None

    @property
    def enabled(self) -> bool:
        """实时开关（每次求值，不缓存）。

        【2026-10-09】构造期快照改为实时读取：`cfg.runtime_status.vision_enabled` 由
        webapp.set_desktop_watch_enabled / set_vision_enabled / proactive 切档时原地
        改写，实时读才能让设置页开关"点了就停"，不必重启。
        """
        try:
            live = bool(self._cfg.runtime_status.vision_enabled)
        except Exception:  # noqa: BLE001
            live = self._enabled
        self._enabled = live          # 顺带刷新调试台快照
        return live

    def _low_rate(self) -> bool:
        """是否切低频采样档（游戏前台/锁快照）：保护 CPU/GPU，防游戏抽帧。

        与 throttle_check 解耦但同源（都读 `_game_check_or_locked`）：
          - throttle=True  → 低频抓屏 + **不做**视觉分析（原让位行为）
          - throttle=False & low_rate=True → 低频抓屏 + **照做**视觉分析
            （云端 VLM 接管看屏时的形态：看得见，但不抢性能）
        回调异常一律当 False（宁可按正常节奏跑，不因判定异常而静默降级）。
        """
        try:
            return bool(self._low_rate_check()) if callable(self._low_rate_check) else False
        except Exception:  # noqa: BLE001
            logger.debug("低频档判定异常，按正常节奏处理", exc_info=True)
            return False

    def _next_interval(self, low_rate: bool = False) -> float:
        """随机化采样间隔：基础间隔 ±抖动；静态画面时再缩短，高频采样确认。

        low_rate=True 时**固定走 game_interval_s 低频档，并关闭静态加速**：
        游戏/通话期间画面常长时间静止（大厅、HUD、后台窗口），static 加速会把
        间隔打到 ~1s，每帧全屏抓屏 + resize + pHash + JPEG 编码 —— 这正是
        2026-08-25 修掉的"抽帧/核显本卡炸"。低频底线必须守住。
        """
        if low_rate:
            return max(0.5, self._game_interval_s * random.uniform(0.8, 1.2))
        interval = self._interval * random.uniform(1.0 - self._jitter, 1.0 + self._jitter)
        if self._unchanged_since is not None:
            static_s = time.time() - self._unchanged_since
            if static_s >= self._static_streak_s:
                interval *= self._static_scale
        return max(0.5, interval)

    def _sensitive_reason(self) -> str:
        """当前前台是否属于「不看」清单；命中返回命中的那条，否则空串。

        【2026-10-20 立】为什么必须在抓屏**之前**问：
        抓完再丢等于图已经进过内存、进过 JPEG 编码、进过日志，只是没送给模型。
        "没送给模型"不等于"没采集"——网银页面停留 10 分钟，按 interval_s=2
        就是 300 帧原始画面被生成过。真正的隐私保护是**不生成**。

        回调异常时返回空（继续采集）并记 debug：判定器坏了不该让用户觉得
        "她突然瞎了"。但装配缺失有闸门守着（tools/check_all.py 的
        「敏感窗口跳过」一项），不会静默失效。
        """
        if not self._foreground_check:
            return ""
        try:
            proc, title = self._foreground_check()
        except Exception:  # noqa: BLE001
            logger.debug("前台判定失败，本轮按正常采集处理", exc_info=True)
            return ""
        return sensitive_skip_reason(proc, title, self._skip_apps)

    def _mark_skip(self, reason: str) -> None:
        """记一次跳过（次数 + 命中条目 + 时刻），供调试台/审计查看。"""
        prev = self._skip_last          # 先存旧值：赋值后再比较就是自己跟自己比
        self._skip_count += 1
        self._skip_last = reason
        self._skip_last_ts = time.time()
        # 只在**换了原因**时打一条 info，避免网银页面停留 10 分钟刷 300 行日志。
        if reason != prev:
            logger.info("敏感窗口跳过屏幕采集: 命中「%s」", reason)

    def snapshot(self) -> dict:
        """运行状态快照（供调试台轮询，线程安全）。"""
        static_s = 0.0
        if self._unchanged_since is not None:
            static_s = time.time() - self._unchanged_since
        return {
            "running": self._running,
            "enabled": self._enabled,
            "interval_s": self._interval,
            "jitter": self._jitter,
            "static_streak_s": self._static_streak_s,
            "static_interval_scale": self._static_scale,
            "static_analyze_every": self._static_analyze_every,
            "static_seconds": round(static_s, 1),
            "last_ts": self._last_ts,
            "last_interval": round(self._last_interval, 2),
            "sample_count": self._sample_count,
            "change_count": self._change_count,
            "analyze_count": self._analyze_count,
            "event_trigger_count": self._event_trigger_count,
            # 敏感窗口跳过（2026-10-20）：用户能查"她为什么没看见"
            "skip_count": self._skip_count,
            "skip_last": self._skip_last,
            "skip_last_ts": self._skip_last_ts,
            "skip_apps": list(self._skip_apps) if self._skip_apps is not None else [],
            "analyzing": self._analyze_task is not None and not self._analyze_task.done(),
            "analyze_drop": self._analyze_drop,
        }

    def _ensure_sct(self):
        """懒加载 mss 实例。"""
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def _spawn_analyze(self, jpeg: bytes) -> None:
        """把视觉分析丢到后台跑，采样循环立刻继续（2026-08-31）。

        云端视觉是排队制，单次可能等几十秒到两分钟。原先 `await analyze()`
        会把整个截图循环拖停——期间不采样、不更新状态，调试台看起来像
        "感知卡死"，时间线也长时间空着。改成后台任务后：采样按原节奏继续，
        视觉结果出来后才进时间线。忙时（上一帧还没分析完）直接丢帧——
        采样任务里最新帧才有价值，积压旧帧没有意义。
        """
        task = self._analyze_task
        if task is not None and not task.done():
            self._analyze_drop += 1
            return
        self._analyze_task = asyncio.create_task(self._analyze_safe(jpeg))

    async def _analyze_safe(self, jpeg: bytes) -> None:
        """后台分析包装：吞掉异常避免"从不检索的 task 异常"刷屏。"""
        try:
            await self._analyzer.analyze(jpeg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("后台视觉分析异常")

    async def _grab_and_analyze(self, force: bool = False) -> None:
        """抓一帧主屏并送视觉分析（抓屏段串行，分析段**不持锁**）。

        事件驱动路径（前台切换等）保持 await：这类场景就是要立刻拿到结果，
        且频率极低，不会拖慢采样循环。

        【2026-10-13 性能修复（"回退本地也不能卡死"）】原实现把
        `await self._analyzer.analyze(...)` 也包在 `_grab_lock` 里。而单次视觉
        分析最长可等 `vision.describe_timeout_s`（本机配置 **150s**）——云端挂起
        或回退本地 ollama 冷加载都算在内。这段时间 `capture_loop` 每帧都要抢
        `_grab_lock`，抢不到就整条看屏链路停摆（表现＝"感知卡死"）。更糟的是
        `wire_foreground` 在**每次前台切到游戏**时都会 `capture_now(force=True)`，
        打游戏时来回切窗即反复触发。
        现只在锁内做「抓屏 + 涂窗 + JPEG 编码」，出锁后再 await 分析：采样节奏
        永远不被视觉请求（无论云端还是本地回退）拖住。
        """
        if privacy_mode():
            return
        # 敏感窗口：事件驱动路径（前台切游戏等）同样要拦——否则"切到游戏"的
        # 触发逻辑会把网银窗口顺手抓一遍。
        _skip = self._sensitive_reason()
        if _skip:
            self._mark_skip(_skip)
            return
        async with self._grab_lock:
            sct = self._ensure_sct()
            monitors = sct.monitors
            idx = min(self._monitor_index, len(monitors) - 1)
            _mon = monitors[idx]
            # 【图像级去自身窗（2026-09-26 改·满足"OBS 正常 + AI 不看自身"）】
            # 不再用 WDA(窗口显示亲和性)做系统级隐身——WDA 是系统级开关，会让
            # OBS/录屏/系统共享也看不到本窗口，且每帧开关必然闪烁。改为：抓原始
            # 全屏 → 仅在送给 AI 的这一帧上把本软件窗口涂中性色（图像级）。这样
            # OBS/录屏看到完整正常画面，AI 推流帧里不含软件自身 UI。
            raw = await asyncio.to_thread(sct.grab, monitors[idx])
            from PIL import Image
            img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
            # 图像级涂掉本软件窗口（始终执行；通知窗空闲透明→跳过标题避免常驻灰块）。
            try:
                from .own_window import paint_own_windows
                paint_own_windows(img, _mon["left"], _mon["top"], skip_titles=("达妮娅通知",))
            except Exception:  # noqa: BLE001
                logger.debug("涂掉自身窗口失败", exc_info=True)
            # 【输入法窗口屏蔽（2026-09-04）】输入法候选窗涂中性色，避免看屏感知
            # 把输入法当成"用户在看的内容"去解读。
            try:
                from .own_window import paint_ime_windows
                _mon = monitors[idx]
                paint_ime_windows(img, _mon["left"], _mon["top"])
            except Exception:  # noqa: BLE001
                logger.debug("屏蔽输入法窗口失败", exc_info=True)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            jpeg = buf.getvalue()
        # 出锁再分析：视觉请求（云端挂起 / 本地回退冷加载）绝不占着抓屏锁
        await self._analyzer.analyze(jpeg, force=force)

    async def capture_now(self, force: bool = False) -> None:
        """事件驱动抓屏：立即抓一帧分析（如前台切到游戏窗口时）。"""
        if not self.enabled:
            return
        try:
            self._event_trigger_count += 1
            await self._grab_and_analyze(force=force)
        except Exception:
            logger.exception("事件驱动抓屏失败")

    async def capture_loop(self) -> None:
        if not self.enabled:
            logger.info("屏幕感知已禁用（视觉模型不可用）")
            return
        try:
            from . import phash
        except Exception as e:  # noqa: BLE001
            logger.error("视觉依赖缺失: %s", e)
            return

        idx = min(self._monitor_index, len(self._ensure_sct().monitors) - 1)
        logger.info(
            "屏幕截图循环启动: 基础间隔=%ss(±%d%%) 监视器=%s 静态提速=%ss/%s",
            self._interval, int(self._jitter * 100), idx,
            self._static_streak_s, self._static_scale,
        )

        while self._running:
            try:
                # 【2026-10-09 热生效】每轮重新读开关：用户在设置页关掉「看屏互动」
                # 后，下一轮即不再抓屏/分析（此前只在上面的 while 之前判一次，导致
                # "关了还在跑"）。用短睡眠自旋而非退出循环——重新打开时无需重启任务。
                if not self.enabled:
                    self._last_hash = None
                    self._last_interval = 2.0
                    await asyncio.sleep(2.0)
                    continue
                if privacy_mode():
                    # 隐私模式：不抓屏不分析，模型拿不到新画面；恢复后自动继续
                    self._last_hash = None
                    self._last_interval = self._next_interval()
                    await asyncio.sleep(self._last_interval)
                    continue
                # 【敏感窗口跳过（2026-10-20）】前台命中「不看」清单（密码管理器 /
                # 网银 / 支付）时**整帧不抓**，不是抓完再丢。
                # 与隐私模式的区别：隐私模式让她整体失明；这里只是"这一个窗口没看"，
                # 用户离开该窗口后下一轮自动恢复（_last_hash 保持旧值 → 切回时
                # 画面不同 → 正常判为变化并分析）。
                _skip = self._sensitive_reason()
                if _skip:
                    self._mark_skip(_skip)
                    self._last_interval = max(2.0, self._next_interval())
                    await asyncio.sleep(self._last_interval)
                    continue
                # 【游戏前台/锁快照 让位（2026-08-25 修复抽帧/核显本卡炸）】
                # throttle=True（前台在玩/锁快照）→ 低频抓屏维持最新帧但不触发
                # 云端视觉分析（AI 游戏时互动靠 OCR+事件驱动，符合"锁快照"语义）。
                # 让位游戏，避免每帧抓屏+JPEG+视觉分析占 CPU 造成抽帧，核显本尤甚。
                throttle = bool(self._throttle_check()) if callable(self._throttle_check) else False
                # 【低频档（2026-10-13）】游戏/锁快照期间**无论是否节流**都走低频：
                # 「游戏时云端看屏」把 throttle 放开（云端 VLM 接管持续读屏）后，
                # 采样节奏必须仍是 game_interval_s，否则就是性能回退（见 _next_interval）。
                low_rate = self._low_rate()
                if throttle:
                    try:
                        async with self._grab_lock:
                            sct = self._ensure_sct()
                            raw = await asyncio.to_thread(sct.grab, sct.monitors[idx])
                            from PIL import Image
                            img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
                            small = img.resize((self._downscale, self._downscale))
                            self._last_hash = phash.phash(small)
                    except Exception:  # noqa: BLE001
                        logger.debug("游戏时低频抓屏异常", exc_info=True)
                    self._last_interval = self._next_interval(low_rate=True)
                    await asyncio.sleep(self._last_interval)
                    continue
                self._last_ts = time.time()
                self._sample_count += 1

                # 抓屏 + 计算 pHash（在 _grab_lock 内完成，避免与事件抓屏并发）
                async with self._grab_lock:
                    sct = self._ensure_sct()
                    _mon = sct.monitors[idx]
                    # 【图像级去自身窗（2026-09-26 改）】同 _grab_and_analyze：抓原始屏
                    # → 仅 AI 帧涂掉自身窗口（图像级），不用 WDA 系统级隐身（避免 OBS
                    # 也看不到本窗口 + 每帧开关闪烁）。
                    raw = await asyncio.to_thread(sct.grab, sct.monitors[idx])
                    from PIL import Image
                    img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
                    try:
                        from .own_window import paint_own_windows
                        paint_own_windows(img, _mon["left"], _mon["top"], skip_titles=("达妮娅通知",))
                    except Exception:  # noqa: BLE001
                        logger.debug("涂掉自身窗口失败", exc_info=True)
                    # 【输入法窗口屏蔽（2026-09-04）】涂掉输入法候选窗
                    try:
                        from .own_window import paint_ime_windows
                        _mon = sct.monitors[idx]
                        paint_ime_windows(img, _mon["left"], _mon["top"])
                    except Exception:  # noqa: BLE001
                        logger.debug("屏蔽输入法窗口失败", exc_info=True)
                    small = img.resize((self._downscale, self._downscale))
                    h = phash.phash(small)
                    changed = self._last_hash is None or (h - self._last_hash) >= self._threshold
                    self._last_hash = h
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)

                if changed:
                    # 画面有变化：正常分析一次，并重置静态跟踪
                    self._change_count += 1
                    self._analyze_count += 1
                    self._unchanged_since = None
                    self._static_sample_count = 0
                    # 后台分析：不 await，采样循环按节奏继续（见 _spawn_analyze）
                    self._spawn_analyze(buf.getvalue())
                else:
                    # 画面无变化：跟踪静态时长，长时间同一画面则周期性补分析
                    if self._unchanged_since is None:
                        self._unchanged_since = time.time()
                    static_s = time.time() - self._unchanged_since
                    self._static_sample_count += 1
                    if static_s >= self._static_streak_s and self._static_sample_count >= self._static_analyze_every:
                        self._static_sample_count = 0
                        self._analyze_count += 1
                        self._spawn_analyze(buf.getvalue())
                        logger.debug("静态画面 %s，补一次确认分析", f"{static_s:.0f}s")
                    else:
                        logger.debug("屏幕无变化，跳过分析 (连续无变化 %.0fs)", static_s)
            except Exception:
                logger.exception("截图/分析异常")

            self._last_interval = self._next_interval(low_rate=low_rate)
            await asyncio.sleep(self._last_interval)
