# -*- coding: utf-8 -*-
"""看屏开关「热生效」回归测试（2026-10-09）。

背景（用户实锤）：在设置页关掉「看屏互动」后，模型还在挂载、截图/分析管线还在跑，
必须重启才停。根因是两个**构造期快照**，此后永不再读：

  1) ScreenCapture._enabled   ← cfg.runtime_status.vision_enabled（构造时读一次）
  2) VisionAnalyzer._disabled ← 同上；且其 AUTO_RECOVER_S 自动恢复**不看用户开关**，
     过 AUTO_RECOVER_S 会自行把 _disabled 翻回 False ⇒ 用户关了，分析线自己复活。

本测试用桩替身（不依赖 mss/网络）验证：
  A. ScreenCapture.enabled 是**实时**属性：改 cfg 后立刻反映，不缓存。
  B. capture_loop 在 while 内**每轮**重读开关：中途关掉 → 不再抓屏（要跨过采样周期）。
  C. capture_now 走实时开关：关闸不抓也不分析，开闸真抓一帧并送分析。
  D. VisionAnalyzer.analyze 硬闸：总闸关 → 直接 None，且**一次云端调用都不发**。
  E. 自动恢复**不**推翻用户关闸：先熔断（_disabled_at>0）、用户再关闸，
     过了恢复窗口也不许复活。
  F. 用户重新开闸后，_disabled 能被解除（不会永久哑火）。
  G. 失败熔断（连续超时/失败凑满阈值）在 AUTO_RECOVER_S 后**自动恢复**并重试。
  H. 关掉再打开：循环自己活回来（不许退出），且重开后重新分析一帧。

【2026-11-10 判据大修】此前 A–F 六组 14 条断言里有六处零判据、一处判据不精确：
B 组关闸后只等 0.6s（采样周期 3s，一个周期都没走完 → 计数当然不涨）、
E 组断言的那段自动恢复被更前面的硬闸挡住**根本执行不到**、C 组只测「关闸不动」
不测「开闸真抓」、H 组整组不存在。详见 tools/_qc_hotapply_mutation.py 十二刀。

跑法：
  python tools/test_desktop_watch_hotapply.py
退出码 0 = 全绿。
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def ok(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + extra) if extra else ""))


# ---------------------------------------------------------------- 桩
class FakeRuntimeStatus:
    """可变的 runtime_status 替身：随时改 vision_enabled 模拟用户开关。"""

    def __init__(self, vision_enabled: bool) -> None:
        self.vision_enabled = vision_enabled
        self.degradations: list[str] = []


class FakeCfg:
    def __init__(self, vision_enabled: bool = True, interval_s: float = 3.0,
                 downscale: int = 768) -> None:
        self.runtime_status = FakeRuntimeStatus(vision_enabled)
        # ScreenCapture.__init__ 读 cfg.ns.perception.screenshot.*
        # interval_s / downscale 可调：B 组要「关闸后跨过好几个采样周期」才有判据
        # （原用默认 3s，关闸后只等 0.6s —— 一个周期都没到，抓屏计数当然不涨）。
        self.ns = SimpleNamespace(
            perception=SimpleNamespace(
                screenshot=SimpleNamespace(
                    interval_s=interval_s, monitor_index=0, downscale=downscale,
                    change_threshold=6, game_interval_s=6.0,
                ),
                vision=SimpleNamespace(
                    min_interval_s=3.0, cache_ttl_s=20, timeout_fail_limit=3,
                    ocr_text_hint=False, hud_region_hints=[],
                    enabled=True, desktop_watch_enabled=vision_enabled,
                ),
            ),
            vision_models=SimpleNamespace(enabled=True),
        )


class FakeFrameGrabber:
    """记录 grab 次数，并返回一张**合法**的小帧。

    为什么不再用「grab 抛异常」来计数：抛异常会走 `except Exception` 打一条
    Traceback（`logger.exception`），每抓一次刷一屏；而且异常路径让
    `_last_hash`/`_next_interval` 的更新全部跳过，测的就不是正常采样节奏了。
    返回合法帧 = 走完整条抓屏→pHash→分析管线，计数仍然精确。
    """

    def __init__(self, size: int = 8) -> None:
        self.calls = 0
        self._size = size
        self.monitors = [
            {},
            {"left": 0, "top": 0, "width": size, "height": size},
        ]

    def grab(self, mon):  # noqa: ANN001
        self.calls += 1
        n = self._size
        return SimpleNamespace(width=n, height=n, bgra=bytes(n * n * 4))


class FakeBus:
    """只收事件，不派发：用来断言熔断时广播了 VISION_DISABLED。"""

    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, ev, *a, **k):  # noqa: ANN001, ANN002, ANN003
        self.events.append(ev)


class FailLLM:
    """云端视觉每次都失败（非超时）：用来把「连续失败熔断」真的打出来。"""

    vision_available = True

    def __init__(self) -> None:
        self.calls = 0

    async def vision_describe(self, img, prompt=None, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        raise RuntimeError("云端挂了（自测桩）")


class OkLLM:
    """云端视觉正常：用来验证「闸门关着时一次都没发出去」。"""

    vision_available = True

    def __init__(self) -> None:
        self.calls = 0

    async def vision_describe(self, img, prompt=None, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        return "在写代码"


def _load_screen_capture():
    """导入 ScreenCapture —— 其模块顶部 import 了 vision_analyzer / privacy。

    privacy_mode 与 VisionAnalyzer 都是外部依赖，注桩避免拉进 mss/PIL。
    """
    import importlib

    # 桩掉 privacy（避免拉进真实实现）
    # 【2026-10-21 修·桩缺导出名】只塞 privacy_mode，而 screen_capture.py 用的是
    # `from .privacy import privacy_mode, sensitive_skip_reason` —— 产品后来加了后者
    # （敏感窗口整帧不采集），桩没跟上 → 3 项直接 ImportError 连坐。
    # 正确写法：先复制真模块的公开名字，再覆盖需要替换的那几个。
    import src.perception.privacy as _real_privacy
    fake_privacy = types.ModuleType("src.perception.privacy")
    for _n in dir(_real_privacy):
        if not _n.startswith("__"):
            setattr(fake_privacy, _n, getattr(_real_privacy, _n))
    fake_privacy.privacy_mode = lambda: False
    fake_privacy.sensitive_skip_reason = lambda *a, **k: ""
    sys.modules["src.perception.privacy"] = fake_privacy

    # 桩掉 vision_analyzer（ScreenCapture 只用它的 analyze）
    fake_va = types.ModuleType("src.perception.vision_analyzer")

    class _VA:
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            self.seen: list[bytes] = []

        async def analyze(self, b, force=False):  # noqa: ANN001, ANN003
            self.seen.append(b)
            return {"summary": "stub"}

    fake_va.VisionAnalyzer = _VA
    sys.modules["src.perception.vision_analyzer"] = fake_va

    # 桩掉 imagehash（capture_loop 开头 `import imagehash`，缺则直接 return，
    # 会让"逐轮重读"根本走不到 → 测试假阴性）。用假 imagehash 让它进循环。
    if "imagehash" not in sys.modules:
        ih = types.ModuleType("imagehash")
        ih.phash = staticmethod(lambda img: 0)   # int 支持 - 与 >=
        sys.modules["imagehash"] = ih

    return importlib.import_module("src.perception.screen_capture")


def _load_vision_analyzer_real():
    """真实 VisionAnalyzer —— 但只测 analyze() 的闸门分支（不触网）。

    ANCHOR: 硬闸在 `if not self._llm.vision_available` 之前，所以桩 _llm 足够。
    """
    import importlib
    import logging
    import src.perception.vision_analyzer as va  # noqa: PLC0415

    importlib.reload(va)
    # E/F/G 故意让云端失败，`logger.warning("视觉分析失败…")` 会往 stderr 刷，
    # 而 stderr 无缓冲、比 stdout 先出来 —— 读者会以为门崩了。判据在断言里，
    # 日志不需要，压到 CRITICAL。
    logging.getLogger("src.perception.vision_analyzer").setLevel(logging.CRITICAL)
    return va


# ---------------------------------------------------------------- 测试
def test_screen_capture_live_enabled() -> None:
    print("\n[A] ScreenCapture.enabled 是实时属性（不缓存）")
    mod = _load_screen_capture()
    cfg = FakeCfg(vision_enabled=False)
    cap = mod.ScreenCapture(bus=None, analyzer=None, cfg=cfg)
    ok("A1 构造时读到 False", cap.enabled is False)

    cfg.runtime_status.vision_enabled = True
    ok("A2 改 cfg 后立即可见（不缓存）", cap.enabled is True,
       "原实现此处仍是构造期快照 False")

    cfg.runtime_status.vision_enabled = False
    ok("A3 再关回去也立即反映", cap.enabled is False)


def test_capture_now_respects_live_switch() -> None:
    print("\n[C] capture_now 走实时开关")
    mod = _load_screen_capture()
    cfg = FakeCfg(vision_enabled=False, downscale=64)

    class RecVA:
        def __init__(self): self.calls = 0
        async def analyze(self, *a, **k):
            self.calls += 1
            return {}

    va = RecVA()
    grabber = FakeFrameGrabber()
    cap = mod.ScreenCapture(bus=None, analyzer=va, cfg=cfg)
    cap._ensure_sct = lambda: grabber  # type: ignore[assignment]
    # 关闸时 capture_now 应立即 return，不产生抓屏
    asyncio.run(cap.capture_now(force=True))
    ok("C1 关闸时 capture_now 不分析", va.calls == 0)
    ok("C2 关闸时连帧都不抓", grabber.calls == 0, f"grab={grabber.calls}")

    # 【2026-11-10 补】只测「关闸不动」是半个判据：把 capture_now 整条改成
    # `return`（开闸也永不抓屏）照样全绿。必须有「开闸真的抓一帧并送分析」。
    cfg.runtime_status.vision_enabled = True
    asyncio.run(cap.capture_now(force=True))
    ok("C3 开闸后 capture_now 抓一帧并送分析", va.calls == 1 and grabber.calls == 1,
       f"analyze={va.calls} grab={grabber.calls}")
    ok("C4 事件触发计数只在真抓时加", cap._event_trigger_count == 1,
       f"count={cap._event_trigger_count}")


async def _loop_until_disabled(mod, cfg) -> tuple[int, int, int]:
    """起 capture_loop，跑几个周期后关闸，返回 (关闸前, 关闸落定, 再等三周期后) 的抓屏计数。

    断言方式：关闸后应走 `if not self.enabled: sleep(2.0); continue`，
    即 grab 计数不再增长。

    【2026-11-10 修·窗口比周期还短】原版：开闸跑 0.25s → 关闸 → 只等 0.6s 就断言
    `after == before`。而基础间隔是 3.0s（`_next_interval` 下限 0.5s），
    **一个采样周期都没走完**，计数当然不涨 —— 把「每轮重读开关」整段删掉照样绿。
    现改为：间隔调到 0.2s（实际下限 0.5s）、开闸先跑满 1.2s 拿基线、关闸后等 1.5s
    （跨三个周期）。删掉重读那段，关闸后每 0.5s 还会抓一次，`after` 必涨。
    """
    grabber = FakeFrameGrabber()

    class A:
        async def analyze(self, *a, **k): return {}

    cap = mod.ScreenCapture(bus=None, analyzer=A(), cfg=cfg)
    # 用 recorder 替换 mss 实例，并让 _ensure_sct 返回它
    cap._ensure_sct = lambda: grabber  # type: ignore[assignment]

    task = asyncio.create_task(cap.capture_loop())
    await asyncio.sleep(1.2)                        # 开闸：跑满两个多周期
    before = grabber.calls
    cfg.runtime_status.vision_enabled = False       # ← 用户关掉
    await asyncio.sleep(0.15)                       # 让「关闸那一刻在途」的那次落定
    mid = grabber.calls
    await asyncio.sleep(1.5)                        # 关闸后跨三个周期
    after = grabber.calls
    cap.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    return before, mid, after


def test_capture_loop_rechecks_each_iteration() -> None:
    print("\n[B] capture_loop 在 while 内每轮重读开关")
    mod = _load_screen_capture()
    cfg = FakeCfg(vision_enabled=True, interval_s=0.2, downscale=64)
    try:
        before, mid, after = asyncio.new_event_loop().run_until_complete(
            _loop_until_disabled(mod, cfg))
    except Exception as e:  # noqa: BLE001
        ok("B1 loop 可运行", False, f"异常: {type(e).__name__}: {e}")
        return
    ok("B1 开闸期间确有抓屏尝试（且不止一次，证明确在按周期跑）", before >= 2,
       f"before={before}")
    ok("B2 关闸瞬间最多在途一次", mid - before <= 1, f"{before}→{mid}")
    ok("B3 关闸后再跨三个周期仍不抓屏（热生效）", after == mid,
       f"before={before} mid={mid} after={after}")


async def _loop_off_then_on(mod, cfg) -> tuple[int, int, int, int, int]:
    """关闸 → 等一会儿 → 再开闸，返回 (关闸前抓屏, 关闸后抓屏, 重开后抓屏,
    关闸前分析数, 重开后分析数)。

    守的是 `capture_loop` 里那句注释的承诺：
    「用短睡眠自旋而非退出循环——重新打开时无需重启任务」。
    原版没有任何用例关闸后再开闸，所以把 `continue` 改成 `return`（循环退出，
    重开必须重启进程）照样全绿。
    """
    grabber = FakeFrameGrabber()
    calls = {"n": 0}

    class A:
        async def analyze(self, *a, **k):
            calls["n"] += 1
            return {"summary": "stub"}

    cap = mod.ScreenCapture(bus=None, analyzer=A(), cfg=cfg)
    cap._ensure_sct = lambda: grabber  # type: ignore[assignment]

    task = asyncio.create_task(cap.capture_loop())
    await asyncio.sleep(0.7)
    g_before = grabber.calls
    cfg.runtime_status.vision_enabled = False       # ← 关
    await asyncio.sleep(0.7)
    g_off = grabber.calls
    calls_off = calls["n"]
    cfg.runtime_status.vision_enabled = True        # ← 再开（不许重启任务）
    # 关闸分支是 `sleep(2.0)` 自旋，所以必须等超过 2s 才看得到下一轮
    await asyncio.sleep(2.6)
    g_on = grabber.calls
    calls_on = calls["n"]
    cap.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    return g_before, g_off, g_on, calls_off, calls_on


def test_reopen_resumes_without_restart() -> None:
    print("\n[H] 关掉再打开：循环自己活回来，且重新分析一帧")
    mod = _load_screen_capture()
    cfg = FakeCfg(vision_enabled=True, interval_s=0.2, downscale=64)
    try:
        g_before, g_off, g_on, calls_off, calls_on = (
            asyncio.new_event_loop().run_until_complete(_loop_off_then_on(mod, cfg)))
    except Exception as e:  # noqa: BLE001
        ok("H1 loop 可运行", False, f"异常: {type(e).__name__}: {e}")
        return
    ok("H1 关闸期间不抓屏", g_off <= g_before + 1, f"{g_before}→{g_off}")
    ok("H2 重新开闸后循环还活着、继续抓屏（不需重启）", g_on > g_off,
       f"off={g_off} on={g_on}")
    ok("H3 重开后重新分析了一帧（_last_hash 被清，不因同画面跳过）",
       calls_on > calls_off, f"analyze {calls_off}→{calls_on}")


def test_vision_analyzer_hard_gate() -> None:
    print("\n[D] VisionAnalyzer.analyze 硬闸（总闸关 → None）")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)

    # 【2026-11-10 修】原桩没有 vision_describe，闸门被拆掉时 analyze 会在调云端那步
    # 抛 AttributeError，同样返回 None —— 「返回 None」于是分不清「被闸挡住」和
    # 「崩在别处」。改成一个会记账的正常桩：闸门在，就一次都不该被调用。
    llm = OkLLM()
    # bus 不能是 None：闸门万一被拆掉，analyze 会一路走到 publish 才崩，
    # 那时 D2 拿到的是「ERROR」而不是「FAIL D2」，判据就说不清了。
    an = va.VisionAnalyzer(llm, bus=FakeBus(), cfg=cfg)
    ok("D1 构造：开闸时未禁用", an.disabled is False)

    cfg.runtime_status.vision_enabled = False
    r = asyncio.run(an.analyze(b"x"))
    ok("D2 关闸后 analyze 返回 None（不发调用）", r is None and llm.calls == 0,
       f"r={r!r} calls={llm.calls}")
    ok("D3 关闸后 disabled=True", an.disabled is True)
    ok("D4 关闸造成的禁用 _disabled_at==0（非失败熔断）", an._disabled_at == 0.0)


def test_auto_recover_does_not_override_user_switch() -> None:
    print("\n[E] 自动恢复不推翻用户关闸（先熔断，再关闸）")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)
    llm = FailLLM()
    bus = FakeBus()
    an = va.VisionAnalyzer(llm, bus=bus, cfg=cfg)
    loop = asyncio.new_event_loop()

    # 【2026-11-10 重写】原版在这里手工 `an._disabled_at = 0.0` 造「已过恢复窗口」的
    # 假象，然后断言不复活 —— 但 `_disabled_at` 初始就是 0，硬闸在更前面就 return，
    # 自动恢复那段**根本执行不到**。于是：
    #   · 把自动恢复整段删掉（永久哑火）→ 照样绿
    #   · 硬闸不再把 `_disabled_at` 归零（= 2026-10-09 那个原始 bug 回归）→ 照样绿
    # 真正能复现原始 bug 的时序是「先失败熔断（_disabled_at>0）→ 用户再关闸」，
    # 这里就按这个时序造。
    for _ in range(3):
        loop.run_until_complete(an.analyze(b"x", force=True))
    ok("E1 连续失败 3 次触发熔断（_disabled_at>0）",
       an.disabled is True and an._disabled_at > 0, f"disabled_at={an._disabled_at}")
    calls_at_trip = llm.calls

    cfg.runtime_status.vision_enabled = False       # ← 用户这时关掉看屏
    loop.run_until_complete(an.analyze(b"x", force=True))
    ok("E2 关闸后 disabled 仍 True，且被标成「非失败熔断」(_disabled_at==0)",
       an.disabled is True and an._disabled_at == 0.0,
       f"disabled={an.disabled} disabled_at={an._disabled_at}")

    # 时间流逝：越过自动恢复窗口
    import time as _t
    _orig = _t.time
    try:
        _t.time = lambda: _orig() + va.AUTO_RECOVER_S + 10  # type: ignore[assignment]
        r = loop.run_until_complete(an.analyze(b"x", force=True))
    finally:
        _t.time = _orig  # type: ignore[assignment]
    ok("E3 过了自动恢复窗口仍被硬闸挡住", r is None)
    ok("E4 disabled 仍为 True（没被自己复活）", an.disabled is True)
    ok("E5 关闸期间一次云端调用都没发", llm.calls == calls_at_trip,
       f"{calls_at_trip}→{llm.calls}")


def test_failure_breaker_auto_recovers() -> None:
    print("\n[G] 失败熔断 90s 后自动恢复（不永久哑火）")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)
    llm = FailLLM()
    bus = FakeBus()
    an = va.VisionAnalyzer(llm, bus=bus, cfg=cfg)
    loop = asyncio.new_event_loop()

    # 2026-08-24 修的那个 bug：云端瞬时抖动凑满阈值 → `_disabled=True` 后**永不恢复**，
    # 感知线永久哑火，必须重启。全仓只有本门碰 `_fail_count`，但原版从没造过失败熔断，
    # 所以「自动恢复」这条支路一条判据都没有（整段删掉照样全绿）。
    for _ in range(3):
        loop.run_until_complete(an.analyze(b"x", force=True))
    ok("G1 连败 3 次后熔断", an.disabled is True and an._disabled_at > 0,
       f"fail={an._fail_count} disabled_at={an._disabled_at}")
    ok("G2 熔断时广播 VISION_DISABLED（前端/日志要看得到）",
       any(getattr(e, "data", {}).get("code") == "VISION_DISABLED" for e in bus.events),
       f"events={[getattr(e, 'data', {}).get('code') for e in bus.events]}")

    n = llm.calls
    loop.run_until_complete(an.analyze(b"x", force=True))
    ok("G3 熔断期内直接丢帧，不再打云端", llm.calls == n, f"{n}→{llm.calls}")

    import time as _t
    _orig = _t.time
    try:
        _t.time = lambda: _orig() + va.AUTO_RECOVER_S + 1  # type: ignore[assignment]
        loop.run_until_complete(an.analyze(b"x", force=True))
    finally:
        _t.time = _orig  # type: ignore[assignment]
    ok("G4 过 90s 后真的重试了一次（自动恢复生效）", llm.calls == n + 1,
       f"{n}→{llm.calls}")
    ok("G5 恢复后失败计数从 0 重计（不是 4）", an._fail_count == 1,
       f"fail_count={an._fail_count}")
    ok("G6 恢复后 disabled=False / _disabled_at=0", an.disabled is False and an._disabled_at == 0.0,
       f"disabled={an.disabled} at={an._disabled_at}")


def test_reopen_clears_user_gate() -> None:
    print("\n[F] 重新开闸后解除禁用（不永久哑火）")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)
    llm = FailLLM()
    an = va.VisionAnalyzer(llm, bus=FakeBus(), cfg=cfg)
    loop = asyncio.new_event_loop()
    cfg.runtime_status.vision_enabled = False
    loop.run_until_complete(an.analyze(b"x", force=True))
    ok("F1 关闸后 disabled=True", an.disabled is True)
    ok("F1b 关闸时没打云端", llm.calls == 0, f"calls={llm.calls}")

    cfg.runtime_status.vision_enabled = True
    # 重开后走一次 analyze：闸门应放行（真的打了一次云端，失败但不再被"永久哑火"挡住）
    loop.run_until_complete(an.analyze(b"x", force=True))
    ok("F2 重开后 disabled 被解除", an.disabled is False,
       "原实现 _disabled_at==0 永远进不了恢复分支 → 永久哑火")
    ok("F3 重开后确实放行了一次云端调用", llm.calls == 1, f"calls={llm.calls}")


def main() -> int:
    print("=" * 68)
    print("看屏开关热生效 回归测试")
    print("=" * 68)
    for fn in (test_screen_capture_live_enabled,
               test_capture_now_respects_live_switch,
               test_capture_loop_rechecks_each_iteration,
               test_reopen_resumes_without_restart,
               test_vision_analyzer_hard_gate,
               test_auto_recover_does_not_override_user_switch,
               test_failure_breaker_auto_recovers,
               test_reopen_clears_user_gate):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAIL.append(fn.__name__)
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print("\n" + "=" * 68)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗", f)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
