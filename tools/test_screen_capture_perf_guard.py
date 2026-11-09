# -*- coding: utf-8 -*-
"""看屏管线「不放性能闸」回归测试（2026-10-13）。

背景（开发者要求）：
  「放开 throttle 让云端 VLM 在游戏/锁分期间继续看屏」这件事，**不能**顺带把
  采样频率也放开 —— 否则画面静止时 static 加速会把间隔打到 ~1s，每帧全屏
  抓屏 + resize + pHash + JPEG 编码，正是 2026-08-25 修掉的「抽帧/核显本卡炸」。
  同时，回退本地那条路也不能把采样循环卡死。

本测试用桩替身（不依赖 mss / 网络）验证五件事：
  A. `_next_interval(low_rate=True)` 固定走 game_interval_s 档、且**不受静态加速**
     影响；对照组 low_rate=False 在长时间静止时确实会缩短（证明测的不是死代码）。
  B. `capture_loop` 接线：throttle=False（云端 VLM 接管）但 low_rate=True（游戏/锁）
     → 循环真实使用的间隔是低频档，而不是 interval_s 档。
  C. `_grab_and_analyze` 的视觉分析**不持有抓屏锁**：分析在途时，采样循环仍能拿到
     `_grab_lock`（治「单次分析最长 150s 把看屏链路拖停」）。
  D. main 接线真值表（【2026-10-13 升级】）：真 import main，逐组合验证
     `_cloud_vision_available` / `_game_cloud_watch_active`；AST 校验每处
     `_screen_throttle_check` 逻辑形状一致、每处 `ScreenCapture(...)` 都带
     `low_rate_check`（防两处接线漂移）；并对**真实 config.yaml** 现值做一致性判定。
  E. 让位 vs 云端接管（【2026-10-13 升级】）：throttle=True → **零**视觉分析；
     throttle=False（游戏中）→ 照样分析；两种形态采样都守低频档。
  F. `capture_now(force=True)` 事件驱动路径（前台切游戏触发）走通并触发分析。

跑法：
  python tools/test_screen_capture_perf_guard.py
退出码 0 = 全绿。
"""
from __future__ import annotations

import asyncio
import importlib
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
class FakeCfg:
    """对齐 config.yaml 的真实量级：interval_s=2 / game_interval_s=6 /
    static_streak_s=30 / static_interval_scale=0.5（这样两档间隔差得开、好断言）。"""

    def __init__(self, vision_enabled: bool = True) -> None:
        self.runtime_status = SimpleNamespace(vision_enabled=vision_enabled, degradations=[])
        self.ns = SimpleNamespace(
            perception=SimpleNamespace(
                screenshot=SimpleNamespace(
                    interval_s=2.0, monitor_index=0, downscale=8,
                    change_threshold=5, game_interval_s=6.0,
                    interval_jitter=0.0, static_streak_s=0.0,
                    static_interval_scale=0.5, static_analyze_every=1,
                ),
            ),
        )


class RaisingGrab:
    """grab 直接抛错 → 循环走最后一行的 `_next_interval(low_rate=low_rate)`，
    于是可以直接读 cap._last_interval 断言「循环真的用了哪一档间隔」。"""

    def __init__(self) -> None:
        self.calls = 0
        self.monitors = [{"left": 0, "top": 0, "width": 8, "height": 8},
                         {"left": 0, "top": 0, "width": 8, "height": 8}]

    def grab(self, mon):  # noqa: ANN001
        self.calls += 1
        raise RuntimeError("stop-here")


class TinyGrab:
    """返回一张 4x4 的假屏幕：够 PIL 编码成 JPEG（测 _grab_and_analyze 用）。"""

    def __init__(self) -> None:
        self.monitors = [{"left": 0, "top": 0, "width": 4, "height": 4},
                         {"left": 0, "top": 0, "width": 4, "height": 4}]

    def grab(self, mon):  # noqa: ANN001
        return SimpleNamespace(width=4, height=4, bgra=bytes(4 * 4 * 4))


def _load_screen_capture():
    """导入 ScreenCapture；桩掉 privacy / vision_analyzer / imagehash / own_window。

    own_window 是真实现（要碰 Win32 窗口枚举），测试里没必要跑，桩掉更快更稳。

    【2026-10-21 修·桩缺导出名】原桩只塞了 `privacy_mode`，而 screen_capture.py 的
    是 `from .privacy import privacy_mode, sensitive_skip_reason` —— 产品后来给 privacy
    加了 `sensitive_skip_reason`（敏感窗口整帧不采集），桩没跟上，于是整个 C 段
    （3 项）被一条 ImportError 连坐跳过。桩的正确写法是**先复制真模块的公开名字、
    再覆盖需要替换的那几个**，这样产品侧新增导出名时桩自动跟上。
    """
    import src.perception.privacy as _real_privacy
    m = types.ModuleType("src.perception.privacy")
    for _n in dir(_real_privacy):
        if not _n.startswith("__"):
            setattr(m, _n, getattr(_real_privacy, _n))
    m.privacy_mode = lambda: False          # 测试里恒定"不在隐私模式"
    m.sensitive_skip_reason = lambda *a, **k: ""   # 恒定"不是敏感窗口"
    sys.modules["src.perception.privacy"] = m

    va = types.ModuleType("src.perception.vision_analyzer")
    va.VisionAnalyzer = type("_VA", (), {"__init__": lambda self, *a, **k: None,
                                        "analyze": lambda self, *a, **k: None})
    sys.modules["src.perception.vision_analyzer"] = va

    ow = types.ModuleType("src.perception.own_window")
    ow.paint_own_windows = lambda *a, **k: None
    ow.paint_ime_windows = lambda *a, **k: None
    sys.modules["src.perception.own_window"] = ow

    if "imagehash" not in sys.modules:
        ih = types.ModuleType("imagehash")
        ih.phash = staticmethod(lambda img: 0)
        sys.modules["imagehash"] = ih

    return importlib.import_module("src.perception.screen_capture")


# ---------------------------------------------------------------- A
def test_next_interval_low_rate() -> None:
    print("\n[A] _next_interval：low_rate 档守住低频底线")
    mod = _load_screen_capture()
    cap = mod.ScreenCapture(bus=None, analyzer=None, cfg=FakeCfg())

    # 模拟「画面已静止很久」——正是会触发 static 加速的场景
    import time as _t
    cap._unchanged_since = _t.time() - 9999

    lows = [cap._next_interval(low_rate=True) for _ in range(50)]
    ok("A1 low_rate=True 恒等于 game_interval_s 档（6.0±20%）",
       all(4.8 - 1e-6 <= v <= 7.2 + 1e-6 for v in lows),
       f"min={min(lows):.3f} max={max(lows):.3f}")
    ok("A2 low_rate=True 绝不被 static 加速打到 1s 级",
       all(v > 4.0 for v in lows), f"min={min(lows):.3f}")

    normals = [cap._next_interval(low_rate=False) for _ in range(50)]
    ok("A3 对照：low_rate=False 在静止时确实缩短（2.0×0.5=1.0）",
       all(abs(v - 1.0) < 1e-6 for v in normals),
       f"min={min(normals):.3f} max={max(normals):.3f}")
    ok("A4 两档确有区别（测的不是死代码）",
       min(lows) > max(normals) * 3)


# ---------------------------------------------------------------- B
async def _run_loop_briefly(mod, cfg, throttle: bool, low_rate: bool) -> float:
    """起 capture_loop 跑一小会儿，返回循环实际写入的 _last_interval。"""
    grab = RaisingGrab()
    cap = mod.ScreenCapture(bus=None, analyzer=None, cfg=cfg,
                            throttle_check=(lambda: throttle),
                            low_rate_check=(lambda: low_rate))
    cap._ensure_sct = lambda: grab  # type: ignore[assignment]
    task = asyncio.create_task(cap.capture_loop())
    await asyncio.sleep(0.15)
    val = cap._last_interval
    cap.stop()
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001
        pass
    return val


def test_capture_loop_low_rate_wiring() -> None:
    print("\n[B] capture_loop 接线：不节流 ≠ 不降频")
    mod = _load_screen_capture()
    cfg = FakeCfg(vision_enabled=True)

    def _run(**kw):
        return asyncio.new_event_loop().run_until_complete(_run_loop_briefly(mod, cfg, **kw))

    try:
        # 云端 VLM 接管（不节流）+ 游戏/锁（低频）→ 仍须走 6s 档
        v_cloud = _run(throttle=False, low_rate=True)
        # 普通桌面（不节流、不低频）→ 走 2s 档
        v_desk = _run(throttle=False, low_rate=False)
        # 让位（节流）→ 也是 6s 档
        v_thr = _run(throttle=True, low_rate=True)
    except Exception as e:  # noqa: BLE001
        ok("B1 循环可运行", False, f"异常: {type(e).__name__}: {e}")
        return

    ok("B1 云端接管+游戏/锁 → 循环用低频档（约6s）", 4.8 - 1e-6 <= v_cloud <= 7.2 + 1e-6,
       f"_last_interval={v_cloud:.3f}")
    ok("B2 普通桌面 → 循环用正常档（约2s）", 1.9 - 1e-6 <= v_desk <= 2.1 + 1e-6,
       f"_last_interval={v_desk:.3f}")
    ok("B3 让位时也用低频档（行为与改前一致）", 4.8 - 1e-6 <= v_thr <= 7.2 + 1e-6,
       f"_last_interval={v_thr:.3f}")


# ---------------------------------------------------------------- C
class SlowAnalyzer:
    """analyze 进入后挂住，直到外部放行 —— 用来测「分析期间抓屏锁是否被占用」。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def analyze(self, img_bytes, force=False):  # noqa: ANN001, ANN003
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return {}


async def _lock_free_during_analyze(mod, cfg) -> tuple[bool, bool]:
    """返回 (分析是否已在途, 期间是否仍能拿到抓屏锁)。"""
    va = SlowAnalyzer()
    cap = mod.ScreenCapture(bus=None, analyzer=va, cfg=cfg, low_rate_check=lambda: True)
    cap._ensure_sct = lambda: TinyGrab()  # type: ignore[assignment]

    task = asyncio.create_task(cap._grab_and_analyze())
    try:
        await asyncio.wait_for(va.entered.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()
        return False, False

    got = False
    try:
        await asyncio.wait_for(cap._grab_lock.acquire(), timeout=0.5)
        got = True
    except asyncio.TimeoutError:
        got = False
    finally:
        if got:
            cap._grab_lock.release()
        va.release.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except BaseException:  # noqa: BLE001
            task.cancel()
    return True, got


def test_grab_lock_not_held_during_analyze() -> None:
    print("\n[C] _grab_and_analyze：分析在途时不占抓屏锁")
    try:
        mod = _load_screen_capture()
    except Exception as e:  # noqa: BLE001
        ok("C0 PIL 可用（编码 JPEG 需要）", False, f"{type(e).__name__}: {e}")
        return
    try:
        entered, got = asyncio.new_event_loop().run_until_complete(
            _lock_free_during_analyze(mod, FakeCfg()))
    except Exception as e:  # noqa: BLE001
        ok("C1 场景可运行", False, f"异常: {type(e).__name__}: {e}")
        return
    ok("C1 视觉分析确已在途（不是空跑）", entered)
    ok("C2 分析在途时采样循环仍能拿到 _grab_lock", got,
       "原实现锁住整段 analyze：云端挂起/本地冷加载最长 150s ⇒ 看屏链路停摆")
    ok("C3 PIL 编码路径可用（TinyGrab 4x4 JPEG）", entered and got)


# ---------------------------------------------------------------- D
class _RawCfg(SimpleNamespace):
    """main 模块级判定函数只读 cfg.raw —— 用 SimpleNamespace(raw=...) 即可真测。"""


def test_main_wiring_truth_table() -> None:
    print("\n[D] main 接线真值表：_game_cloud_watch_active / _screen_throttle_check")
    import ast as _ast
    # A-C 会往 sys.modules 塞桩模块（privacy/vision_analyzer 无 set_privacy_mode 等），
    # 直接 import main 会撞到被污染的缓存 → 先清掉相关条目再导入真身。
    for name in ("main", "src.perception.privacy", "src.perception.vision_analyzer",
                 "src.perception.own_window", "src.perception.screen_capture"):
        sys.modules.pop(name, None)
    try:
        import main as main_mod  # 顶层无副作用（ast 已核），导入安全
    except Exception as e:  # noqa: BLE001
        ok("D0 main 可导入", False, f"{type(e).__name__}: {e}")
        return
    ok("D0 main 可导入（顶层无副作用）", True)

    def C(raw):
        return _RawCfg(raw=raw)

    oai_key = {"provider": "openai", "api_key": "sk-test"}
    oai_nok = {"provider": "openai", "api_key": "   "}
    mini = {"provider": "minicpm"}
    ok("D1 openai+key → 云端视觉可用",
       main_mod._cloud_vision_available(C({"vision_models": {"models": [oai_key]}})))
    ok("D2 openai 无 key → 不可用",
       not main_mod._cloud_vision_available(C({"vision_models": {"models": [oai_nok]}})))
    ok("D3 minicpm 免费网关（无 key）→ 可用",
       main_mod._cloud_vision_available(C({"vision_models": {"models": [mini]}})))
    ok("D4 模型清单为空 → 不可用",
       not main_mod._cloud_vision_available(C({"vision_models": {"models": []}})))
    ok("D5 开关开 + 可用模型 → 云端看屏生效",
       main_mod._game_cloud_watch_active(
           C({"vision_models": {"game_cloud_watch": True, "models": [oai_key]}})))
    ok("D6 开关关 → 不生效（模型可用也不接管）",
       not main_mod._game_cloud_watch_active(
           C({"vision_models": {"game_cloud_watch": False, "models": [oai_key]}})))
    ok("D7 开关开但无可用模型 → 不生效（空转防线）",
       not main_mod._game_cloud_watch_active(
           C({"vision_models": {"game_cloud_watch": True, "models": [oai_nok]}})))

    # D8：对真实 config.yaml 的判定必须与配置原文一致（接线读的是真配置）
    try:
        import yaml
        # 【2026-11-10】config.yaml 在 .gitignore 里，刚克隆的树上没有 ——
        # 原来直接 read_text 抛 FileNotFoundError，判据记 False，
        # 于是这道门在**任何新克隆**上都是红的（本机实测 1 FAIL）。
        # CI 之所以绿，是因为 self-check job 先「prepare standard first-run layout」造了它。
        # 与 `test_tray_switch` / `test_clone_ref_audio` 同款修法：缺文件回落出厂模板。
        _cfg_path = ROOT / "config.yaml"
        if not _cfg_path.is_file():
            _cfg_path = ROOT / "config.release.yaml"
        cfg_real = yaml.safe_load(_cfg_path.read_text(encoding="utf-8"))
        vm = (cfg_real or {}).get("vision_models") or {}
        models = vm.get("models") or []
        expect = bool(vm.get("game_cloud_watch")) and any(
            isinstance(m, dict) and (
                str(m.get("provider", "")).strip().lower() == "minicpm"
                or (str(m.get("provider", "")).strip().lower() == "openai"
                    and bool(str(m.get("api_key", "") or "").strip())))
            for m in models)
        got = main_mod._game_cloud_watch_active(_RawCfg(raw=cfg_real))
        ok(f"D8 真实配置判定与原文一致（源={_cfg_path.name}）", got == expect,
           f"game_cloud_watch={vm.get('game_cloud_watch')} expect={expect} got={got}")
    except Exception as e:  # noqa: BLE001
        ok("D8 真实 config.yaml 判定与原文一致", False, f"{type(e).__name__}: {e}")

    # D9-D11：AST 校验 main.py 两处接线不漂移
    tree = _ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    throttles = [n for n in _ast.walk(tree)
                 if isinstance(n, _ast.FunctionDef) and n.name == "_screen_throttle_check"]
    ok("D9 _screen_throttle_check 定义存在", len(throttles) >= 1, f"count={len(throttles)}")
    shape_ok = bool(throttles)
    for fn in throttles:
        s = _ast.unparse(fn)
        if "if not _game_check_or_locked():" not in s or \
           "return not _game_cloud_watch_active(cfg)" not in s:
            shape_ok = False
    ok("D10 每处闭包都是「非游戏→不节流；游戏→看云端开关」同形", shape_ok)
    caps = [n for n in _ast.walk(tree)
            if isinstance(n, _ast.Call) and getattr(n.func, "id", "") == "ScreenCapture"]
    lr_ok = bool(caps) and all(
        any(kw.arg == "low_rate_check" for kw in n.keywords) for n in caps)
    ok("D11 每处 ScreenCapture 构造都带 low_rate_check（性能闸不漂移）",
       lr_ok, f"构造数={len(caps)}")


# ---------------------------------------------------------------- E
class RecordingAnalyzer:
    """计数型分析器：capture_loop 是否真的把帧送进了视觉分析。"""

    def __init__(self) -> None:
        self.calls = 0
        self.event = asyncio.Event()

    async def analyze(self, jpeg_bytes, force=False):  # noqa: ANN001, ANN003
        self.calls += 1
        self.event.set()
        return {}


async def _loop_throttle_behavior(mod, cfg, throttle: bool):
    """跑 capture_loop，返回 (是否触发过分析, 最近采样间隔, 分析次数)。"""
    va = RecordingAnalyzer()
    cap = mod.ScreenCapture(bus=None, analyzer=va, cfg=cfg,
                            throttle_check=(lambda: throttle),
                            low_rate_check=(lambda: True))
    cap._ensure_sct = lambda: TinyGrab()  # type: ignore[assignment]
    task = asyncio.create_task(cap.capture_loop())
    called = False
    try:
        await asyncio.wait_for(va.event.wait(), timeout=2.0)
        called = True
    except asyncio.TimeoutError:
        called = False
    await asyncio.sleep(0.05)
    val = cap._last_interval
    cap.stop()
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001
        pass
    return called, val, va.calls


def test_throttle_vs_takeover() -> None:
    print("\n[E] 让位 vs 云端接管：让位零分析，接管照看，两者都守低频")
    mod = _load_screen_capture()
    cfg = FakeCfg()

    def _run(throttle: bool):
        return asyncio.new_event_loop().run_until_complete(
            _loop_throttle_behavior(mod, cfg, throttle))

    called_t, val_t, n_t = _run(True)
    called_c, val_c, n_c = _run(False)
    ok("E1 让位(throttle=True) → 不触发任何视觉分析", (not called_t) and n_t == 0,
       f"analyze次数={n_t}")
    ok("E2 云端接管(throttle=False, 游戏中) → 照样分析（看得见）",
       called_c and n_c >= 1, f"analyze次数={n_c}")
    ok("E3 让位时采样走低频档（~6s）", 4.8 - 1e-6 <= val_t <= 7.2 + 1e-6,
       f"{val_t:.2f}s")
    ok("E4 云端接管时采样同样走低频档（不节流≠不降频）",
       4.8 - 1e-6 <= val_c <= 7.2 + 1e-6, f"{val_c:.2f}s")


# ---------------------------------------------------------------- F
def test_capture_now_event_path() -> None:
    print("\n[F] capture_now(force=True)：前台切游戏的事件驱动路径")
    mod = _load_screen_capture()
    va = RecordingAnalyzer()
    cap = mod.ScreenCapture(bus=None, analyzer=va, cfg=FakeCfg(),
                            low_rate_check=lambda: True)
    cap._ensure_sct = lambda: TinyGrab()  # type: ignore[assignment]

    async def run() -> int:
        await cap.capture_now(force=True)
        return va.calls

    try:
        n = asyncio.new_event_loop().run_until_complete(run())
    except Exception as e:  # noqa: BLE001
        ok("F1 capture_now 走通并触发分析", False, f"异常: {type(e).__name__}: {e}")
        return
    ok("F1 capture_now(force=True) 触发一次视觉分析", n == 1, f"calls={n}")


def main() -> int:
    # RaisingGrab 会故意抛错 → 循环会 logger.exception 打一堆「截图/分析异常」，
    # 那是被测行为本身造成的预期噪音，测试输出里压掉。
    import logging
    logging.disable(logging.CRITICAL)
    print("=" * 68)
    print("看屏管线「不放性能闸」回归测试（2026-10-13 升级版）")
    print("=" * 68)
    for fn in (test_next_interval_low_rate,
               test_capture_loop_low_rate_wiring,
               test_grab_lock_not_held_during_analyze,
               test_main_wiring_truth_table,
               test_throttle_vs_takeover,
               test_capture_now_event_path):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAIL.append(fn.__name__)
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print("\n" + "=" * 68)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    for f in FAIL:
        print("  x", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
