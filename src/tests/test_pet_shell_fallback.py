"""壳桌宠的"回退契约"测试（不需要 Electron，很快）。

为什么单独测这个：壳只在"RPC 已就绪 + Electron 可用"时才该启用。
任何一条不满足，`create_pet_shell()` 必须返回 None，让主流程回退到 pywebview 桌宠——
**这个回退不成立的话，用户在没有 node_modules 的机器上会连人都看不到**。

运行：.venv/Scripts/python.exe src/tests/test_pet_shell_fallback.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui import pet_shell  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def main() -> int:
    print("[1] 未注入 RPC 时必须回退")
    pet_shell._RPC_INFO.clear()
    check(pet_shell.create_pet_shell() is None,
          "没有 RPC 信息 → create_pet_shell() 返回 None（回退 pywebview）")

    print("[2] 缺令牌也必须回退")
    pet_shell.configure({"url": "http://127.0.0.1:1/rpc"})
    c = pet_shell.create_pet_shell()
    check(c is None, "只有 url 没有 token → 仍然回退（不会起一个调不通后端的窗口）")
    pet_shell._RPC_INFO.clear()

    print("[3] Electron 定位")
    exe = pet_shell.electron_binary()
    print(f"    electron_binary() = {exe}")
    check(exe is None or os.path.isfile(exe), "返回的路径要么是 None，要么是真文件")

    old = os.environ.get("DANIYA_ELECTRON")
    try:
        os.environ["DANIYA_ELECTRON"] = os.path.join(ROOT, "不存在的文件.exe")
        check(pet_shell.electron_binary() != os.environ["DANIYA_ELECTRON"],
              "环境变量指向不存在的文件时被忽略（不盲目相信配置）")
    finally:
        if old is None:
            os.environ.pop("DANIYA_ELECTRON", None)
        else:
            os.environ["DANIYA_ELECTRON"] = old

    print("[4] 鸭子类型契约")
    required = ["show", "hide", "destroy", "move", "resize", "is_visible",
                "alive", "close", "evaluate_js", "wait_ready"]
    missing = [n for n in required if not hasattr(pet_shell.PetShellClient, n)]
    check(not missing, f"公开成员齐全（缺 {missing}）")
    check(isinstance(getattr(pet_shell.PetShellClient, "on_top", None), property),
          "on_top 是可读写的 property（api_pet 会读也会写）")

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        return 1
    print("PASS：回退契约成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
