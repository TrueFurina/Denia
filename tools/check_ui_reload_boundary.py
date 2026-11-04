# -*- coding: utf-8 -*-
"""静态闸门：UI 看门狗绝不能在守护线程上碰 WebView2 COM。

【2026-09-04 16:55:50 冻结实锤】看门狗线程直接 `cv2.Reload()` → 整个进程冻结
4 分钟以上（所有线程无输出）。之后的修复是把所有 COM 访问移进 `_reload` 闭包，
再用 `ctrl.BeginInvoke(Action(...))` 封送到 GUI 线程执行。
【2026-11-04】自动恢复改成默认开启，所以这条边界必须变成**闸门**而不是注释。

守五条：
  1. `_recover_stuck_windows` 里必须有 BeginInvoke；
  2. 全文件每个 `Reload()` 都必须落在 `_recover_stuck_windows` 内的 `_reload` 闭包里；
  3. `_reload` 闭包必须真的被传给 BeginInvoke（定义了却没封送 = 没用）；
  4. `CoreWebView2` / `IsConnected` 这类 COM 访问也只能出现在 `_reload` 里；
  5. `_ui_watchdog_loop`（守护线程）里不许出现任何 COM 访问。

不进闸门：无（已登记 GATES）
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "src", "ui", "api_window.py")
COM_NAMES = {"CoreWebView2", "IsConnected", "Reload"}

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


def enclosing_defs(node, parents):
    out = []
    cur = node
    while cur is not None:
        if isinstance(cur, ast.FunctionDef):
            out.append(cur.name)
        cur = parents.get(cur)
    return out


def main() -> int:
    with io.open(TARGET, "rb") as f:
        src = f.read().decode("utf-8")
    tree = ast.parse(src, filename=TARGET)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    chk("找到 _recover_stuck_windows", "_recover_stuck_windows" in defs)
    chk("找到 _ui_watchdog_loop", "_ui_watchdog_loop" in defs)
    if "_recover_stuck_windows" not in defs or "_ui_watchdog_loop" not in defs:
        print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
        return 1

    recover = defs["_recover_stuck_windows"]
    watchdog = defs["_ui_watchdog_loop"]

    # 1) BeginInvoke 必须存在
    begin = [n for n in ast.walk(recover)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "BeginInvoke"]
    chk("_recover_stuck_windows 里有 BeginInvoke", len(begin) >= 1, str(len(begin)))

    # 2) 每个 Reload() 都在 _reload 闭包里，且闭包嵌在 recover 内
    reload_calls = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "Reload"]
    chk("文件里确实有 Reload（不是空转）", len(reload_calls) >= 1, str(len(reload_calls)))
    bad_reload = [n.lineno for n in reload_calls
                  if not all(x in enclosing_defs(n, parents)
                             for x in ("_reload", "_recover_stuck_windows"))]
    chk("Reload 全部在 recover/_reload 闭包内", not bad_reload, str(bad_reload))

    # 3) _reload 闭包真的被传给 BeginInvoke
    closure = [n for n in ast.walk(recover) if isinstance(n, ast.FunctionDef)
               and n.name == "_reload"]
    chk("找到 _reload 闭包", len(closure) == 1, str(len(closure)))
    passed = False
    for b in begin:
        for arg in ast.walk(b):
            if isinstance(arg, ast.Name) and arg.id == "_reload":
                passed = True
    chk("_reload 被传进 BeginInvoke（不是定义了没用）", passed)

    # 4) 所有 COM 访问都在 _reload 里
    com_nodes = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in COM_NAMES:
            com_nodes.append(n)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in COM_NAMES:
            com_nodes.append(n)
    bad_com = sorted({n.lineno for n in com_nodes
                      if "_reload" not in enclosing_defs(n, parents)})
    chk("所有 COM 访问都在 _reload 闭包里", not bad_com, str(bad_com))

    # 5) 守护线程里零 COM
    wd_com = sorted({n.lineno for n in ast.walk(watchdog)
                     if (isinstance(n, ast.Attribute) and n.attr in COM_NAMES)
                     or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                         and n.func.attr in COM_NAMES)})
    chk("_ui_watchdog_loop 里没有任何 COM 访问", not wd_com, str(wd_com))

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
