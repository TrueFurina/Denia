"""run_command 白名单闸单测（纯函数，不联网、不依赖 pytest）。

背景：2026-10-17 审计发现旧实现有两个缺口叠加后可被绕过——
  * `if allow and head not in ...`：白名单漏配时直接全放行（fail-open）；
  * 只校验首个词 + shell=True：`git status; del /s /q ...` 首词合法即执行。
本测试锁死修复后的行为，防止回归。

运行：python src/llm/tests/test_run_command_whitelist.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.llm.agent_tools import _run_command  # noqa: E402

BASE = {"allow_cmd": True, "allow_cmds": ["whoami", "echo", "dir", "git"]}


def _denied(out: str) -> bool:
    return ("已拒绝" in out) or ("白名单为空" in out) or ("未启用" in out)


def test_gate_disabled() -> None:
    ctx = {"allow_cmd": False, "allow_cmds": ["whoami"]}
    assert "未启用" in _run_command("whoami", ctx), "allow_command=false 时必须拒绝"
    print("✓ 总开关关闭时拒绝")


def test_empty_allowlist_fail_closed() -> None:
    for cmds in ([], None, ["", "  "]):
        ctx = {"allow_cmd": True, "allow_cmds": cmds}
        out = _run_command("whoami", ctx)
        assert "白名单为空" in out, f"白名单={cmds!r} 时必须 fail-closed，实际：{out!r}"
    print("✓ 白名单为空时 fail-closed（旧实现会全放行）")


def test_semicolon_bypass_blocked() -> None:
    """核心回归用例：首词合法 + 分号串联。"""
    out = _run_command("git status; whoami", BASE)
    assert _denied(out), f"分号串联必须拒绝，实际：{out!r}"
    out = _run_command("git status && del /s /q C:\\Windows\\Temp", BASE)
    assert _denied(out), f"&& 串联必须拒绝，实际：{out!r}"
    print("✓ 分号 / && 串联被拦住")


def test_pipe_redirect_vars_blocked() -> None:
    cases = [
        "dir | findstr secret",
        "dir > out.txt",
        "dir < in.txt",
        "echo %USERPROFILE%",
        "echo ^& whoami",
        "whoami\nwhoami",
        "whoami `id`",
        "echo $(id)",
    ]
    for c in cases:
        out = _run_command(c, BASE)
        assert _denied(out), f"应拒绝 {c!r}，实际：{out!r}"
    print(f"✓ 管道/重定向/变量展开/换行 共 {len(cases)} 例全部拒绝")


def test_not_in_allowlist() -> None:
    out = _run_command("calc.exe", BASE)
    assert "不在白名单" in out, f"白名单外命令必须拒绝，实际：{out!r}"
    print("✓ 白名单外的可执行文件被拒绝")


def test_allowed_exe_runs() -> None:
    out = _run_command("whoami", BASE)
    assert out.startswith("[exit "), f"白名单内命令应真正执行，实际：{out[:120]!r}"
    assert "[exit 0]" in out or "[exit 1]" in out, f"应拿到真实退出码，实际：{out[:120]!r}"
    print(f"✓ 白名单内命令正常执行（{out.splitlines()[0]}）")


def test_builtin_cmd_runs() -> None:
    """cmd 内建命令没有 exe，必须走 cmd /d /c，不能 FileNotFoundError。"""
    out = _run_command("echo danya-ok", BASE)
    assert "danya-ok" in out, f"内建命令 echo 应正常输出，实际：{out!r}"
    out = _run_command("dir /b", BASE)
    assert out.startswith("[exit "), f"内建命令 dir 应正常执行，实际：{out[:120]!r}"
    print("✓ cmd 内建命令（echo/dir）正常执行")


def test_quoted_path_token() -> None:
    """带引号的首 token 不能被引号污染导致白名单误判。"""
    out = _run_command('"whoami" /all', BASE)
    assert not out.startswith("命令 'whoami'"), f"引号被当成命令名的一部分，实际：{out!r}"
    print("✓ 首 token 带引号时解析正确")


def test_exe_suffix_normalized() -> None:
    """.exe 后缀不应导致白名单误判（配置写 whoami.exe / 调用写 whoami 都算命中）。"""
    out = _run_command("whoami", {"allow_cmd": True, "allow_cmds": ["whoami.exe"]})
    assert "不在白名单" not in out, f"配置带 .exe 时应命中，实际：{out!r}"
    out2 = _run_command("whoami.exe", {"allow_cmd": True, "allow_cmds": ["whoami"]})
    assert "不在白名单" not in out2, f"调用带 .exe 时应命中，实际：{out2!r}"
    print("✓ .exe 后缀归一（配置/调用两侧）")


def main() -> None:
    test_gate_disabled()
    test_empty_allowlist_fail_closed()
    test_semicolon_bypass_blocked()
    test_pipe_redirect_vars_blocked()
    test_not_in_allowlist()
    test_allowed_exe_runs()
    test_builtin_cmd_runs()
    test_quoted_path_token()
    test_exe_suffix_normalized()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
