"""在场感增强的判据：全局热键映射 + 独占全屏判定 + 退场迟滞状态机。

三块都是**纯逻辑**（不起键盘钩子、不碰 Win32 探测、不碰窗口），所以可离线跑，
不会因为"当前前台窗口是什么"而偶发假红。

对应：
  src/system/hotkeys.py              → build_hotkey_map / HotkeyManager / start_from_config
  src/system/fullscreen_guard.py     → strict_exclusive_fullscreen / FullscreenGuard

不进闸门：无（已登记 GATES）
"""
from __future__ import annotations

import sys
import types

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.system.fullscreen_guard import FullscreenGuard, strict_exclusive_fullscreen  # noqa: E402
from src.system import hotkeys as HK  # noqa: E402

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  ← " + detail) if detail else ""))


def main() -> int:
    print("A) 全局热键：配置 → 映射")

    calls = []
    acts = {"mute": lambda: calls.append("mute"),
            "pet": lambda: calls.append("pet"),
            "watch": lambda: calls.append("watch")}
    spec = {"mute": "<ctrl>+<alt>+m",
            "pet": "<ctrl>+<alt>+h",
            "watch": "<ctrl>+<alt>+s"}

    m, why = HK.build_hotkey_map(spec, acts)
    chk("三个合法组合键全部绑定", len(m) == 3, str(m.keys()))
    chk("没有误跳过", why == [], str(why))
    for key in ("<ctrl>+<alt>+m", "<ctrl>+<alt>+h", "<ctrl>+<alt>+s"):
        m[key]()
    chk("回调各触发一次", calls == ["mute", "pet", "watch"], str(calls))

    print("\nA2) 关闭 / 未知动作 / 非法组合 / 重复")
    m2, why2 = HK.build_hotkey_map(
        {"mute": "", "pet": None, "watch": "<ctrl>+<alt>+s",
         "ghost": "<ctrl>+<alt>+x"}, acts)
    chk("空值 = 关闭该热键", "<ctrl>+<alt>+s" in m2 and len(m2) == 1, str(m2.keys()))
    chk("未知动作被记下", any("未知动作" in w for w in why2), str(why2))

    m3, why3 = HK.build_hotkey_map({"mute": "<ctrl>+<alt>+<nosuchkey>",
                                    "pet": "<ctrl>+<alt>+h"}, acts)
    chk("非法组合被跳过", "<ctrl>+<alt>+h" in m3, str(m3.keys()))
    chk("非法的不影响其它绑定", any("组合键非法" in w for w in why3), str(why3))

    m4, why4 = HK.build_hotkey_map({"mute": "<ctrl>+<alt>+m",
                                    "pet": "<ctrl>+<alt>+m"}, acts)
    chk("重复组合只保留一条", len(m4) == 1, str(m4.keys()))
    chk("重复被记下", any("重复" in w for w in why4), str(why4))

    print("\nA2b) 没有 pynput 时的兜底校验（键名白名单）")
    # 【2026-11-08 加】这一节直接打 `_fallback_ok`，**不经过 `_validate`**：
    # 装了 pynput 的机器上 `_validate` 走的是 pynput 的 parser，兜底分支根本不执行，
    # 于是"兜底会不会放过拼错的键名"在任何一台装了依赖的机器上都判不到。
    # 而兜底恰恰是"用户机器上没装成 pynput"时唯一还在工作的那道校验 ——
    # 它坏了没人发现，症状是**热键绑不上还一声不响**。
    try:
        fb = HK._fallback_ok
    except AttributeError:
        chk("兜底校验存在（_fallback_ok）", False, "hotkeys 里没有 _fallback_ok")
        fb = None
    if fb is not None:
        chk("兜底：拼错的键名被拒", fb("<ctrl>+<alt>+<nosuchkey>") is False,
            "<nosuchkey> 被放行了")
        chk("兜底：合法单字符键仍放行", fb("<ctrl>+<alt>+m") is True)
        chk("兜底：合法功能键仍放行", fb("<ctrl>+<f12>") is True)
        chk("兜底：合法媒体键仍放行", fb("<media_play_pause>") is True)
        chk("兜底：空格键仍放行", fb("<ctrl>+<space>") is True)
        chk("兜底：空段被拒", fb("<ctrl>++m") is False)
        chk("兜底：非 <> 形态不误判", fb("<ctrl>+7") is True)

    print("\nA3) 回调异常不许弄死 listener 线程")
    def boom():
        raise RuntimeError("boom")
    mx, _ = HK.build_hotkey_map({"mute": "<ctrl>+<alt>+m"}, {"mute": boom})
    try:
        mx["<ctrl>+<alt>+m"]()
        chk("回调抛异常被包住", True)
    except Exception as e:  # noqa: BLE001
        chk("回调抛异常被包住", False, repr(e))

    print("\nA4) 开关与空映射")
    cfg_off = types.SimpleNamespace(raw={"presence": {"hotkeys_enabled": False}})
    chk("配置关闭时返回 None", HK.start_from_config(cfg_off, acts) is None)
    empty = HK.HotkeyManager({}, acts)
    chk("空映射 start() 不抛异常", empty.start() is False)
    chk("空映射给了原因", bool(empty.error), empty.error)

    print("\nB) 独占全屏几何：最大化绝不算全屏")
    mon = (0, 0, 1920, 1080)

    chk("四边贴合 rcMonitor → True",
        strict_exclusive_fullscreen((0, 0, 1920, 1080), mon, False) is True)
    chk("IsZoomed（最大化）即使贴满也算 False",
        strict_exclusive_fullscreen((0, 0, 1920, 1080), mon, True) is False)
    chk("典型最大化（底边差任务栏 40px）→ False",
        strict_exclusive_fullscreen((0, 0, 1920, 1040), mon, False) is False)
    chk("同一窗口换一块更小的显示器才命中 → 说明比的是 rcMonitor 而非 rcWork",
        strict_exclusive_fullscreen((0, 0, 1920, 1040), (0, 0, 1920, 1040), False) is True
        and strict_exclusive_fullscreen((0, 0, 1920, 1040), mon, False) is False)
    chk("容差 8px 内仍算", strict_exclusive_fullscreen((3, 2, 1918, 1077), mon, False) is True)
    chk("超出容差 9px 不算", strict_exclusive_fullscreen((0, 0, 1920, 1071), mon, False) is False)
    chk("无效显示器矩形 → False", strict_exclusive_fullscreen((0, 0, 1, 1), (0, 0, 0, 0), False) is False)
    chk("畸形输入不抛", strict_exclusive_fullscreen((1, 2), mon, False) is False)

    print("\nC) 退场状态机：迟滞 / 不重复 / 尊重用户手动隐藏")
    g = FullscreenGuard(hide_after=2, show_after=3)
    chk("单次全屏不动作（防抖）", g.update(True, True) is None)
    chk("第二次全屏 → hide", g.update(True, True) == "hide")
    chk("再来全屏不重复 hide", g.update(True, False) is None)
    chk("hidden_by_guard 为真", g.hidden_by_guard is True)
    chk("退出全屏一次不立刻还原", g.update(False, False) is None)
    chk("退出两次仍不还原", g.update(False, False) is None)
    chk("退出三次 → show", g.update(False, False) == "show")
    chk("还原后清掉所有权", g.hidden_by_guard is False)

    g2 = FullscreenGuard()
    g2.update(True, False)          # 用户本来就藏了她
    chk("用户手动隐藏时不接管", g2.update(True, False) is None)
    chk("抑制期间用户自己显示她，也不许被藏回去", g2.update(True, True) is None)
    chk("退出时也不把她翻出来", g2.update(False, True) is None
        and g2.update(False, True) is None
        and g2.update(False, True) is None)

    g3 = FullscreenGuard()
    g3.update(True, True)
    g3.update(True, True)           # hide
    chk("全屏中短暂 false 不触发 show", g3.update(False, False) is None
        and g3.update(True, False) is None)
    chk("已藏过不再发 hide", g3.hidden_by_guard is True)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
