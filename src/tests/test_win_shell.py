"""Windows 壳层基础能力的回归测试。

覆盖三件事，每件都必须"实际读取系统状态"来证明，而不是只看函数返回值自报成功：
  1. DPI 感知：调用后系统读回值必须 >=1（0 = 仍在被系统位图拉伸）
  2. 单实例：同名互斥体第二次获取必须失败（跨进程也一样）
  3. 唤起已有窗口：无匹配窗口时必须返回 False 而不是抛异常

运行：.venv/Scripts/python.exe src/tests/test_win_shell.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.win_shell import (  # noqa: E402
    acquire_single_instance,
    activate_existing_window,
    dpi_awareness,
    enable_dpi_awareness,
)

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def test_dpi() -> None:
    print("[1] DPI 感知")
    before = dpi_awareness()
    mode = enable_dpi_awareness()
    after = dpi_awareness()
    print(f"    设置前={before} 方式={mode} 设置后={after}")
    check(after >= 1, f"设置后系统读回 >=1（实际 {after}）")
    # 幂等：重复调用不得降级
    enable_dpi_awareness()
    check(dpi_awareness() >= 1, "重复调用后仍保持感知状态")


def test_single_instance() -> None:
    print("[2] 单实例")
    name = "DaniyaTest.SingleInstance.%d" % os.getpid()
    check(acquire_single_instance(name) is True, "第一次获取锁成功")
    check(acquire_single_instance(name) is False, "同名第二次获取失败（说明锁真的存在）")

    # 跨进程：子进程拿同名锁，必须也失败
    code = (
        "import sys\n"
        "sys.path.insert(0, {root!r})\n"
        "from src.win_shell import acquire_single_instance\n"
        "print('RESULT=' + str(acquire_single_instance({name!r})))\n"
    ).format(root=ROOT, name=name)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=90, encoding="utf-8", errors="replace")
    check("RESULT=False" in (out.stdout or ""),
          f"跨进程同名锁获取失败（子进程输出 {((out.stdout or '').strip().splitlines() or [''])[-1]!r}）")

    other = "DaniyaTest.Other.%d" % os.getpid()
    check(acquire_single_instance(other) is True, "不同名字的锁互不影响")


def test_activate() -> None:
    print("[3] 唤起已有窗口")
    r = activate_existing_window(("__绝对不可能存在的窗口标题__",))
    check(r is False, "无匹配窗口时返回 False（不抛异常）")


def main() -> int:
    print(f"Python {sys.version.split()[0]} on {sys.platform}\n")
    test_dpi()
    test_single_instance()
    test_activate()
    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        for f in _fails:
            print("  -", f)
        return 1
    print("PASS：壳层基础能力全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
