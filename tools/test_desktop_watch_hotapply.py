# -*- coding: utf-8 -*-
"""看屏开关「热生效」回归测试（2026-10-09）。

背景（用户实锤）：在设置页关掉「看屏互动」后，模型还在挂载、截图/分析管线还在跑，
必须重启才停。根因是两个**构造期快照**，此后永不再读：

  1) ScreenCapture._enabled   ← cfg.runtime_status.vision_enabled（构造时读一次）
  2) VisionAnalyzer._disabled ← 同上；且其 AUTO_RECOVER_S 自动恢复**不看用户开关**，
     过 60s 会自行把 _disabled 翻回 False ⇒ 用户关了，分析线自己复活。

本测试用桩替身（不依赖 mss/PIL/网络）验证：
  A. ScreenCapture.enabled 是**实时**属性：改 cfg 后立刻反映，不缓存。
  B. capture_loop 在 while 内**每轮**重读开关：中途关掉 → 不再抓屏。
  C. capture_now 走实时开关。
  D. VisionAnalyzer.analyze 硬闸：总闸关 → 直接 None（dry，不碰网络）。
  E. 自动恢复**不**推翻用户关闸（_disabled_at==0 不被 AUTO_RECOVER 唤醒）。
  F. 用户重新开闸后，_disabled 能被解除（不会永久哑火）。

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
    def __init__(self, vision_enabled: bool = True) -> None:
        self.runtime_status = FakeRuntimeStatus(vision_enabled)
        # ScreenCapture.__init__ 读 cfg.ns.perception.screenshot.*
        self.ns = SimpleNamespace(
            perception=SimpleNamespace(
                screenshot=SimpleNamespace(
                    interval_s=3.0, monitor_index=0, downscale=768,
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


class GrabRecorder:
    """记录被调用的次数：用来断言"关了之后不再抓屏"。"""

    def __init__(self) -> None:
        self.calls = 0
        self.monitors = [{}, {"left": 0, "top": 0, "width": 100, "height": 100}]

    def grab(self, mon):  # noqa: ANN001
        self.calls += 1
        raise RuntimeError("stop-here")  # 立即打断，只为计数


def _load_screen_capture():
    """导入 ScreenCapture —— 其模块顶部 import 了 vision_analyzer / privacy。

    privacy_mode 与 VisionAnalyzer 都是外部依赖，注桩避免拉进 mss/PIL。
    """
    import importlib

    # 桩掉 privacy（避免拉进真实实现）
    fake_privacy = types.ModuleType("src.perception.privacy")
    fake_privacy.privacy_mode = lambda: False
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
    import src.perception.vision_analyzer as va  # noqa: PLC0415

    importlib.reload(va)
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
    cfg = FakeCfg(vision_enabled=False)

    class RecVA:
        def __init__(self): self.calls = 0
        async def analyze(self, *a, **k):
            self.calls += 1
            return {}

    va = RecVA()
    cap = mod.ScreenCapture(bus=None, analyzer=va, cfg=cfg)
    # 关闸时 capture_now 应立即 return，不产生抓屏
    asyncio.get_event_loop().run_until_complete(cap.capture_now(force=True))
    ok("C1 关闸时 capture_now 不分析", va.calls == 0)


async def _loop_until_disabled(mod, cfg) -> int:
    """起 capture_loop，跑一会儿后把开关关掉，返回关闸后 grab 是否被叫。

    断言方式：关闸后 loop 里应走 `if not self.enabled: sleep; continue`，
    即 grab 计数不再增长。
    """
    grabber = GrabRecorder()

    class A:
        async def analyze(self, *a, **k): return {}

    cap = mod.ScreenCapture(bus=None, analyzer=A(), cfg=cfg)
    # 用 recorder 替换 mss 实例，并让 _ensure_sct 返回它
    cap._ensure_sct = lambda: grabber  # type: ignore[assignment]

    task = asyncio.create_task(cap.capture_loop())
    await asyncio.sleep(0.25)
    before = grabber.calls
    cfg.runtime_status.vision_enabled = False       # ← 用户关掉
    await asyncio.sleep(0.6)
    after = grabber.calls
    cap.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    return before, after


def test_capture_loop_rechecks_each_iteration() -> None:
    print("\n[B] capture_loop 在 while 内每轮重读开关")
    mod = _load_screen_capture()
    cfg = FakeCfg(vision_enabled=True)
    try:
        before, after = asyncio.new_event_loop().run_until_complete(
            _loop_until_disabled(mod, cfg))
    except Exception as e:  # noqa: BLE001
        ok("B1 loop 可运行", False, f"异常: {type(e).__name__}: {e}")
        return
    ok("B1 开闸期间确有抓屏尝试（基线非零）", before > 0, f"before={before}")
    ok("B2 关闸后不再抓屏（热生效）", after == before,
       f"before={before} after={after}")


def test_vision_analyzer_hard_gate() -> None:
    print("\n[D] VisionAnalyzer.analyze 硬闸（总闸关 → None）")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)

    class LLM:
        vision_available = True

    an = va.VisionAnalyzer(LLM(), bus=None, cfg=cfg)
    ok("D1 构造：开闸时未禁用", an.disabled is False)

    cfg.runtime_status.vision_enabled = False
    r = asyncio.new_event_loop().run_until_complete(an.analyze(b"x"))
    ok("D2 关闸后 analyze 返回 None（不发调用）", r is None)
    ok("D3 关闸后 disabled=True", an.disabled is True)
    ok("D4 关闸造成的禁用 _disabled_at==0（非失败熔断）", an._disabled_at == 0.0)


def test_auto_recover_does_not_override_user_switch() -> None:
    print("\n[E] 自动恢复不推翻用户关闸")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)

    class LLM:
        vision_available = True

    an = va.VisionAnalyzer(LLM(), bus=None, cfg=cfg)
    cfg.runtime_status.vision_enabled = False
    asyncio.new_event_loop().run_until_complete(an.analyze(b"x"))
    # 造出"已过自动恢复窗口"的假象
    an._disabled_at = 0.0
    # 直接模拟时间流逝：即便过了 AUTO_RECOVER_S，也不该被唤醒
    import time as _t
    _orig = _t.time
    try:
        _t.time = lambda: _orig() + va.AUTO_RECOVER_S + 10  # type: ignore[assignment]
        r = asyncio.new_event_loop().run_until_complete(an.analyze(b"x"))
    finally:
        _t.time = _orig  # type: ignore[assignment]
    ok("E1 过了自动恢复窗口仍被硬闸挡住", r is None)
    ok("E2 disabled 仍为 True（没被自己复活）", an.disabled is True)


def test_reopen_clears_user_gate() -> None:
    print("\n[F] 重新开闸后解除禁用（不永久哑火）")
    va = _load_vision_analyzer_real()
    cfg = FakeCfg(vision_enabled=True)

    class LLM:
        vision_available = True

    an = va.VisionAnalyzer(LLM(), bus=None, cfg=cfg)
    cfg.runtime_status.vision_enabled = False
    asyncio.new_event_loop().run_until_complete(an.analyze(b"x"))
    ok("F1 关闸后 disabled=True", an.disabled is True)

    cfg.runtime_status.vision_enabled = True
    # 走一次 analyze 应解除（vision_available=True → 继续往下，但会触网）
    # 故只验证"闸门放行"：调用后 disabled 应被清掉
    try:
        asyncio.new_event_loop().run_until_complete(an.analyze(b"x"))
    except Exception:  # noqa: BLE001
        pass
    ok("F2 重开后 disabled 被解除", an.disabled is False,
       "原实现 _disabled_at==0 永远进不了恢复分支 → 永久哑火")


def main() -> int:
    print("=" * 68)
    print("看屏开关热生效 回归测试")
    print("=" * 68)
    for fn in (test_screen_capture_live_enabled,
               test_capture_now_respects_live_switch,
               test_capture_loop_rechecks_each_iteration,
               test_vision_analyzer_hard_gate,
               test_auto_recover_does_not_override_user_switch,
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
