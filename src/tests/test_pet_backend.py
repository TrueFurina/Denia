"""桌宠渲染宿主的决策测试（src/ui/pet_backend.py）——不需要 Electron，很快。

为什么要测这个：决策错了不会报错，只会静默用错后端。
真实事故就在这里——`main.py --pet-shell` 曾经只开 RPC，桌宠窗仍硬编码走 pywebview，
开关看着能用、实际没通电。所以这里把"优先级、非法值、回退、RPC 门控"全钉住。

运行：.venv/Scripts/python.exe src/tests/test_pet_backend.py
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui import pet_backend, pet_shell  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _cfg(pet_backend_value):
    """造一个只有 ui.pet_backend 的假配置，模拟 cfg.ns.ui.pet_backend。"""
    if pet_backend_value is None:
        return types.SimpleNamespace(ns=types.SimpleNamespace())
    return types.SimpleNamespace(
        ns=types.SimpleNamespace(ui=types.SimpleNamespace(pet_backend=pet_backend_value)))


def main() -> int:
    print("[1] 取值归一化")
    check(pet_backend.normalize("auto") == "auto", "auto 原样保留")
    check(pet_backend.normalize("SHELL") == "shell", "大小写不敏感")
    check(pet_backend.normalize("  webview  ") == "webview", "首尾空白被剥掉")
    check(pet_backend.normalize("electron") == pet_backend.DEFAULT,
          f"非法值 → 默认值（{pet_backend.DEFAULT}），不抛异常")
    check(pet_backend.normalize(None) == pet_backend.DEFAULT, "None → 默认值")

    print("[1b] 默认值必须是 auto（先试壳，起不来静默回退 webview）")
    check(pet_backend.DEFAULT == pet_backend.AUTO,
          f"默认后端 = {pet_backend.DEFAULT}")
    check(pet_backend.resolve(None, None) == pet_backend.AUTO,
          "什么都没配 → auto（壳能起来就用真透明）")
    # auto 的代价：必须先起 RPC，否则壳连不上后端 → 起不来再回退，等于白花时间
    check(pet_backend.wants_rpc(pet_backend.DEFAULT) is True,
          "默认后端要起 RPC（壳靠它连后端）")

    print("[2] 优先级：命令行 > 配置 > 默认")
    check(pet_backend.resolve("shell", _cfg("webview")) == "shell",
          "命令行覆盖配置")
    check(pet_backend.resolve(None, _cfg("webview")) == "webview",
          "无命令行时取配置")
    check(pet_backend.resolve(None, _cfg(None)) == pet_backend.DEFAULT,
          "配置缺 ui 段 → 默认值")
    check(pet_backend.resolve(None, None) == pet_backend.DEFAULT, "无配置 → 默认值")

    print("[3] argparse 命名空间兼容")
    a = types.SimpleNamespace(pet_backend="webview", pet_shell=False)
    check(pet_backend.resolve_from_args(a, _cfg("shell")) == "webview",
          "--pet-backend 优先")
    b = types.SimpleNamespace(pet_backend=None, pet_shell=True)
    check(pet_backend.resolve_from_args(b, _cfg("webview")) == "shell",
          "历史开关 --pet-shell 等价 shell 且优先于配置")
    c = types.SimpleNamespace(pet_backend=None, pet_shell=False)
    check(pet_backend.resolve_from_args(c, _cfg("auto")) == "auto",
          "都没传 → 回落配置")

    print("[4] RPC 门控")
    check(pet_backend.wants_rpc("shell") is True, "shell 需要 RPC")
    check(pet_backend.wants_rpc("auto") is True, "auto 需要 RPC（才能判断壳能不能用）")
    check(pet_backend.wants_rpc("webview") is False, "webview 不起 RPC")
    check(pet_backend.wants_rpc("webview", explicit_rpc=True) is True,
          "webview + 显式 --rpc 时仍然要起（外部壳要用）")
    check(pet_backend.wants_rpc("webview", handshake_path="/tmp/h.json") is True,
          "webview + --rpc-handshake 时也要起")

    print("[5] 建窗：webview 明确不走壳")
    old = os.environ.get("DANIYA_ELECTRON")
    pet_shell._RPC_INFO.clear()
    try:
        os.environ.pop("DANIYA_ELECTRON", None)
        # 真 pywebview 此时没起循环，不能真建窗；只验证「走的是 pywebview 那条路」。
        # _create_webview_pet 是**函数内**延迟 import，所以改模块属性即可生效。
        called = {"v": False}
        import src.ui.main_window as _mw

        _orig = _mw.create_desktop_companion

        def _fake_create(api=None):
            called["v"] = True
            return "WEBVIEW_WINDOW"

        _mw.create_desktop_companion = _fake_create
        try:
            win, used, why = pet_backend.create_pet_window("webview", api=object())
        finally:
            _mw.create_desktop_companion = _orig
        check(called["v"], "backend=webview 时确实调用了 create_desktop_companion")
        check(used == "webview", "返回的实际后端是 webview")
        check(win == "WEBVIEW_WINDOW", "返回的是 pywebview 窗口")
        check(bool(why), f"回退原因非空（{why}）")
    finally:
        if old is not None:
            os.environ["DANIYA_ELECTRON"] = old

    print("[6] 建窗：shell 但壳不可用 → 必须回退，不能返回 None")
    pet_shell._RPC_INFO.clear()
    _orig_bin = pet_shell.electron_binary
    pet_shell.electron_binary = lambda: None   # 模拟"没装 electron"
    try:
        import src.ui.main_window as _mw

        _orig2 = _mw.create_desktop_companion

        def _fake_fallback(api=None):
            return "FALLBACK_WINDOW"

        _mw.create_desktop_companion = _fake_fallback
        try:
            win2, used2, why2 = pet_backend.create_pet_window("shell", api=object())
        finally:
            _mw.create_desktop_companion = _orig2
        check(win2 == "FALLBACK_WINDOW", "壳起不来时回退出 pywebview 窗口（不会连人都没有）")
        check(used2 == "webview", "实际后端记为 webview")
        check("Electron" in (why2 or ""), f"回退原因写明缺什么：{why2}")
    finally:
        pet_shell.electron_binary = _orig_bin
    pet_shell._RPC_INFO.clear()

    print("[7] shell_readiness 的三态")
    pet_shell._RPC_INFO.clear()
    try:
        pet_shell.electron_binary = lambda: None
        ok, why = pet_shell.shell_readiness()
        check(not ok and "Electron" in why, f"无 electron → False 且原因提到 Electron（{why}）")

        pet_shell.electron_binary = lambda: os.path.join(ROOT, "shell", "node_modules",
                                                         "electron", "dist", "electron.exe")
        pet_shell.configure({"url": "http://127.0.0.1:1/rpc"})
        ok, why = pet_shell.shell_readiness()
        check(not ok and "RPC" in why, f"有 electron 缺 token → False 且原因提到 RPC（{why}）")

        pet_shell.configure({"url": "http://127.0.0.1:1/rpc", "token": "t"})
        ok, why = pet_shell.shell_readiness()
        check(ok and not why, "url + token 齐全 → True")
    finally:
        pet_shell.electron_binary = _orig_bin
    pet_shell._RPC_INFO.clear()

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        return 1
    print("PASS：桌宠后端决策成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
