# -*- coding: utf-8 -*-
"""工作区边界（文件读写沙箱）的判级测试。

守什么
------
1. 相对路径落在工作区内（相对**工作区**解析，不是进程 CWD）；
2. `..` 越界、绝对路径越界、跨盘符 → 一律 `WorkspaceEscape`；
3. Windows 大小写不敏感：工作区路径写成大写也能过；
4. **越界时不写文件**（这是本轮修的核心：原来会悄悄改名写进沙箱，
   还回报「已写入」—— 用户以为改的是那个文件）；
5. 绝对路径落在工作区内 → 允许（"帮我改 D:/proj/x.py" 才有意义）。

跑法：.venv/Scripts/python.exe -u tools/test_workspace_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile

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


def main() -> int:
    ws = os.path.abspath(tempfile.mkdtemp(prefix="ws_guard_"))
    outside = os.path.abspath(tempfile.mkdtemp(prefix="ws_outside_"))

    print("1) 相对路径落在工作区内")
    p = T._safe_path("a.txt", root=ws)
    chk("a.txt → 工作区/a.txt", os.path.normcase(p) == os.path.normcase(os.path.join(ws, "a.txt")), p)
    p = T._safe_path("sub/b.txt", root=ws)
    chk("子目录也归一", os.path.normcase(p) == os.path.normcase(os.path.join(ws, "sub", "b.txt")), p)

    print("\n2) 越界必须抛 WorkspaceEscape")
    for bad in ("../escape.txt", "..\\..\\escape.txt", "../../a/b.txt"):
        try:
            T._safe_path(bad, root=ws)
            chk("%-22s 被拒" % bad, False, "竟然通过了")
        except T.WorkspaceEscape:
            chk("%-22s 被拒" % bad, True)

    print("\n3) 绝对路径：区内放行、区外拒绝")
    inside_abs = os.path.join(ws, "ok.txt")
    chk("区内绝对路径放行", os.path.normcase(T._safe_path(inside_abs, root=ws))
        == os.path.normcase(inside_abs))
    outside_abs = os.path.join(outside, "nope.txt")
    try:
        T._safe_path(outside_abs, root=ws)
        chk("区外绝对路径被拒", False, "竟然通过了")
    except T.WorkspaceEscape:
        chk("区外绝对路径被拒", True)

    print("\n4) 空路径被拒（不许「没给名字」就落到工作区根）")
    try:
        T._safe_path("", root=ws)
        chk("空路径被拒", False, "竟然通过了")
    except T.WorkspaceEscape:
        chk("空路径被拒", True)

    print("\n5) 大小写不敏感（Windows）")
    upper = ws.upper()
    try:
        T._safe_path(os.path.join(upper, "case.txt"), root=ws)
        chk("大写形式的区内路径放行", True)
    except T.WorkspaceEscape:
        chk("大写形式的区内路径放行", False, "被误判为越界")

    print("\n5b) 符号链接指向区外 → 必须拒绝（realpath 的牙齿）")
    link = os.path.join(ws, "sneaky")
    made = False
    try:
        os.symlink(outside, link, target_is_directory=True)
        made = True
    except (OSError, NotImplementedError, AttributeError):
        print("  SKIP 本机不允许建符号链接（需要开发者模式/管理员），跳过这一条")
    if made:
        try:
            T._safe_path(os.path.join("sneaky", "x.txt"), root=ws)
            chk("经符号链接逃逸被拒", False, "竟然通过了")
        except T.WorkspaceEscape:
            chk("经符号链接逃逸被拒", True)

    print("\n6) 越界时**不写文件**（本轮修的核心）")
    T.configure_workspace(ws)
    try:
        target = os.path.join(outside, "must_not_exist.txt")
        msg = T._write_file("../" + os.path.basename(outside) + "/must_not_exist.txt", "x")
        chk("返回里说明越界", "越界" in msg, msg[:60])
        chk("区外文件没有被创建", not os.path.isfile(target), target)
        # 老实现会把它改名写进沙箱 —— 那个也算"假绿"，必须没有
        chk("沙箱里也没多出这个文件",
            not os.path.isfile(os.path.join(ws, "must_not_exist.txt")))
    finally:
        T.configure_workspace("")

    print("\n7) 区内写入仍然正常")
    T.configure_workspace(ws)
    try:
        msg = T._write_file("hello.txt", "hi")
        chk("返回已写入", "已写入" in msg, msg[:40])
        chk("文件真的在工作区里", os.path.isfile(os.path.join(ws, "hello.txt")))
    finally:
        T.configure_workspace("")

    import shutil
    shutil.rmtree(ws, ignore_errors=True)
    shutil.rmtree(outside, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
