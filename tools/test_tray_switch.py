# -*- coding: utf-8 -*-
"""托盘「静音 / 看屏」两个开关的判级测试。

守什么
------
1. 菜单文案跟着**真实状态**走（静音↔取消静音、开启看屏↔关闭看屏）；
2. 点击后状态以**回调返回值**为准，不是本地取反——写盘失败/模式冲突时取反会让
   菜单和实际状态错位，而那种错位用户根本查不出来；
3. `toggle_mute` 只翻 `voice.muted`，**不动** `voice.mode`（静音不是换音色方案）；
4. 但 `voice.mode == "mute"` 时取消静音必须把 mode 放回 clone，否则
   `tts.is_muted()` 两个条件取或，muted=False 也照样没声；
5. `toggle_desktop_watch` 真的翻转 `perception.vision.desktop_watch_enabled`。

跑法：.venv/Scripts/python.exe -u tools/test_tray_switch.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.system.tray_manager import TrayManager, PYSTRAY_AVAILABLE  # noqa: E402

OK = FAIL = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS " + name + ((" :: " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL " + name + ((" :: " + extra) if extra else ""))


def menu_texts(tm: TrayManager) -> list:
    """把菜单文案取出来（MenuItem.text 对 callable 会求值）。"""
    out = []
    for it in tm.create_menu().items:
        t = getattr(it, "text", "")
        out.append(t() if callable(t) else str(t))
    return out


def main() -> int:
    if not PYSTRAY_AVAILABLE:
        print("[SKIP] pystray 不可用（本机没装），托盘项无法验证")
        return 77

    print("1) 菜单项齐全（显示/隐藏/直达入口/静音/看屏/最小化/退出）")
    tm = TrayManager()
    tm.pet_visible = True
    texts = menu_texts(tm)
    chk("含「静音」", "静音" in texts, str(texts))
    chk("含「开启看屏」", "开启看屏" in texts, str(texts))
    chk("含退出", "退出" in texts)
    chk("含三个直达入口", all(k in texts for k in ("记忆库", "关系档案", "设置")), str(texts))
    chk("项数 = 13（10 项 + 3 个分隔符）", len(texts) == 13, str(len(texts)))

    print("\n2) 文案跟状态走")
    tm.set_mute_state(True)
    chk("静音态 → 取消静音", "取消静音" in menu_texts(tm))
    tm.set_mute_state(False)
    chk("非静音态 → 静音", "静音" in menu_texts(tm))
    tm.set_watch_state(True)
    chk("看屏开 → 关闭看屏", "关闭看屏" in menu_texts(tm))
    tm.set_watch_state(False)
    chk("看屏关 → 开启看屏", "开启看屏" in menu_texts(tm))

    print("\n2b) 实时状态优先于缓存（设置页改过也不会显示旧状态）")
    tm4 = TrayManager()
    tm4.set_mute_state(False)                     # 缓存说"没静音"
    tm4.state_provider = lambda: (True, False)    # 但配置里其实是静音中
    chk("菜单显示「取消静音」", "取消静音" in menu_texts(tm4), str(menu_texts(tm4)))
    tm4.state_provider = lambda: (False, True)    # 看屏开着
    chk("看屏实时状态生效", "关闭看屏" in menu_texts(tm4), str(menu_texts(tm4)))
    tm4.state_provider = None                     # 没有 provider 时退回缓存
    chk("无 provider 时用缓存", "静音" in menu_texts(tm4))

    print("\n3) 点击以回调返回值为准（不本地取反）")
    tm2 = TrayManager()
    # 从"已静音"开始，回调也说"仍然是静音"（模拟写盘失败/状态没变）：
    # 实现若在本地取反，这里会翻成 False —— 菜单就会显示"静音"而实际静音中。
    tm2.set_mute_state(True)
    tm2.on_toggle_mute_callback = lambda: {"success": True, "muted": True}
    tm2._on_toggle_mute(None, None)
    chk("状态跟随返回值（不是本地取反）", tm2.muted is True)
    tm2.set_watch_state(True)
    tm2.on_toggle_watch_callback = lambda: {"success": True, "watch_enabled": True}
    tm2._on_toggle_watch(None, None)
    chk("看屏状态跟随返回值", tm2.watch_enabled is True)

    print("\n4) 回调抛异常不许把托盘带崩")
    tm3 = TrayManager()

    def _boom():
        raise RuntimeError("boom")

    tm3.on_toggle_mute_callback = _boom
    try:
        tm3._on_toggle_mute(None, None)
        chk("异常被吞掉、托盘存活", True)
    except Exception as e:  # noqa: BLE001
        chk("异常被吞掉、托盘存活", False, type(e).__name__)

    # ---- 后端开关 ----
    from src.config import load_config
    from src.ui.webapp import CharacterAPI

    tmp = tempfile.mkdtemp(prefix="tray_sw_")
    cfg_copy = os.path.join(tmp, "config.yaml")
    # 【2026-11-08 修·判据依赖 gitignore 里的运行时文件】原来一律 copy `ROOT/config.yaml`，
    # 而 `config.yaml` 在 `.gitignore` 里（它由首启/CI 的 prepare 步骤生成）。
    # **新克隆的树上没有这个文件** → FileNotFoundError → 整套自测一进来就崩，
    # 这道登记在 check_all 里的门对每个新人恒红，而红的原因和 `toggle_mute` 毫无关系
    # （空变异体检里 `_qc_tray_switch_mutation` 一直是 ERROR，就是这么来的）。
    # 本文件测的是 `toggle_mute` 的状态机，不测"用户的配置长什么样"——
    # 所以运行时配置不在时，用**在 git 里的出厂模板**当夹具，并在输出里说明。
    src_cfg = os.path.join(ROOT, "config.yaml")
    if not os.path.isfile(src_cfg):
        src_cfg = os.path.join(ROOT, "config.release.yaml")
        print("  （本机没有 config.yaml，用出厂模板 config.release.yaml 当夹具）")
    shutil.copy2(src_cfg, cfg_copy)
    api = CharacterAPI(cfg_copy)
    api._cfg = load_config(cfg_copy)
    v = api._cfg.raw.setdefault("voice", {})
    v["muted"] = False
    v["mode"] = "clone"

    print("\n5) toggle_mute：只翻 muted，不动 mode")
    r = api.toggle_mute()
    chk("返回 muted=True", r.get("muted") is True, str(r))
    chk("写进 raw", api._cfg.raw["voice"]["muted"] is True)
    chk("mode 仍是 clone", api._cfg.raw["voice"]["mode"] == "clone")
    r = api.toggle_mute()
    chk("再点一次回到 False", r.get("muted") is False)

    print("\n6) mode=mute 时取消静音要把 mode 放回 clone")
    api._cfg.raw["voice"]["mode"] = "mute"
    api._cfg.raw["voice"]["muted"] = False
    r = api.toggle_mute()
    chk("返回 muted=False", r.get("muted") is False, str(r))
    chk("mode 已放回 clone（否则 is_muted 仍为真）",
        api._cfg.raw["voice"]["mode"] == "clone", str(api._cfg.raw["voice"].get("mode")))

    print("6b) 三个直达入口把正确的 page key 转给回调")
    tm5 = TrayManager()
    calls = []
    tm5.on_open_page_callback = lambda p: calls.append(p)
    tm5._on_open_memory(None, None)
    tm5._on_open_relation(None, None)
    tm5._on_open_settings(None, None)
    chk("按顺序 memory/relation/settings", calls == ["memory", "relation", "settings"], str(calls))

    print("6c) open_page 路由表与失败路径")
    chk("关系档案没有独立页 → 落到对话页",
        api._PAGE_ROUTES.get("relation") == "/index.html", str(api._PAGE_ROUTES.get("relation")))
    chk("记忆库路由正确",
        str(api._PAGE_ROUTES.get("memory", "")).endswith("ui_redesign_memory.html"))
    chk("设置路由正确",
        str(api._PAGE_ROUTES.get("settings", "")).endswith("ui_redesign_settings.html"))
    chk("未知页面被拒（不是静默成功）",
        api.open_page("nope").get("success") is False, str(api.open_page("nope")))
    _r = api.open_page("memory")
    chk("无窗口时明确报错（不是静默成功）",
        _r.get("success") is False and "窗口" in str(_r.get("error")), str(_r))

    print("\n7) toggle_desktop_watch：真的翻转配置")
    cur = bool(((api._cfg.raw.get("perception") or {}).get("vision") or {})
               .get("desktop_watch_enabled", False))
    r = api.toggle_desktop_watch()
    new = bool(((api._cfg.raw.get("perception") or {}).get("vision") or {})
               .get("desktop_watch_enabled", False))
    chk("返回 watch_enabled", r.get("watch_enabled") is (not cur), str(r))
    chk("配置真的翻了", new is (not cur), "%s → %s" % (cur, new))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
