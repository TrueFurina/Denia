# -*- coding: utf-8 -*-
"""`run_command` 的守卫判级测试（全离线，替换 subprocess，不真执行命令）。

守什么
------
1. **fail-closed**：`allow_command=false` 或白名单为空 → 一律拒绝（不许"默认全开"）；
2. 白名单按**可执行文件名**判定，不在名单里就拒；
3. 串联/管道/重定向/变量展开一律拒绝（只许单条命令）；
4. **cwd 固定在工作区** —— 与文件类工具同一套边界，相对路径不能落到仓库根。

第 4 条是 2026-11-04 补的：此前 subprocess.run 不传 cwd，继承的是进程 CWD（仓库根），
于是 `run_command` 里的相对路径和 `write_file` 的落点不是同一个地方 —— 同一个"工作区"
在两个工具里含义不同，这种不一致迟早咬人。

跑法：.venv/Scripts/python.exe -u tools/test_run_command_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.llm import agent_tools as T  # noqa: E402

OK = FAIL = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS " + name + ((" :: " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL " + name + ((" :: " + extra) if extra else ""))


class _FakeProc:
    returncode = 0
    stdout = "ok"
    stderr = ""


CAPTURED: dict = {}


def _fake_run(argv, **kw):
    CAPTURED.clear()
    CAPTURED["argv"] = argv
    CAPTURED.update(kw)
    return _FakeProc()


def main() -> int:
    ws = os.path.abspath(tempfile.mkdtemp(prefix="cmd_ws_"))
    T.configure_workspace(ws)
    real_subprocess = T.subprocess
    T.subprocess = SimpleNamespace(run=_fake_run)
    try:
        print("1) fail-closed：没开 / 白名单空 → 拒绝")
        r = T._run_command("echo hi", {"allow_cmd": False})
        chk("allow_cmd=false 拒绝", "未启用" in r, r[:40])
        r = T._run_command("echo hi", {"allow_cmd": True, "allow_cmds": []})
        chk("白名单为空拒绝", "白名单为空" in r, r[:40])
        chk("被拒时根本没起进程", CAPTURED == {}, str(CAPTURED.get("argv")))

        print("\n2) 白名单按可执行文件名判定")
        r = T._run_command("curl http://x", {"allow_cmd": True, "allow_cmds": ["echo"]})
        chk("不在名单 → 拒绝", "不在白名单" in r, r[:40])

        print("\n3) 只许单条命令")
        for bad in ("echo a && echo b", "echo a | findstr a", "echo a > x.txt", "echo %PATH%"):
            r = T._run_command(bad, {"allow_cmd": True, "allow_cmds": ["echo"]})
            chk("%-22s 被拒" % bad[:22], "拒绝" in r, r[:36])

        print("\n4) cwd 固定在工作区")
        r = T._run_command("echo hi", {"allow_cmd": True, "allow_cmds": ["echo"]})
        chk("命令被执行", "ok" in r, r[:30])
        chk("传了 cwd", "cwd" in CAPTURED, str(sorted(CAPTURED)))
        chk("cwd == 工作区",
            os.path.normcase(str(CAPTURED.get("cwd"))) == os.path.normcase(ws),
            "%r vs %r" % (CAPTURED.get("cwd"), ws))
        chk("cwd 不是进程 CWD", os.path.normcase(str(CAPTURED.get("cwd")))
            != os.path.normcase(os.getcwd()), os.getcwd())

        print("\n5) shell=False（不经过 shell 解释）")
        chk("shell=False", CAPTURED.get("shell") is False, str(CAPTURED.get("shell")))
    finally:
        T.subprocess = real_subprocess
        T.configure_workspace("")

    import shutil
    shutil.rmtree(ws, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
