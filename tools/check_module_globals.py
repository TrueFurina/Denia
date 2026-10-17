# -*- coding: utf-8 -*-
"""模块级名字完整性检查（pyflakes 的极简替身，零依赖）。

用 symtable 找出「函数/类作用域里引用、但所在模块顶层没有定义」的全局名字。
拆分模块后最容易引入的就是这种 NameError——它不会在 import 时暴露，
只在执行到那一行时炸，而且往往在 GUI 回调里，很难复现。

用法：
    python tools/check_module_globals.py src/ui/webapp.py src/ui/wv2_env.py ...
    python tools/check_module_globals.py --all src   # 递归检查目录
退出码：0 = 全部有定义；1 = 存在未定义引用。
"""
from __future__ import annotations

import builtins
import os
import symtable
import sys

ALLOWED = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__"}


def check_file(path: str) -> list[str]:
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        top = symtable.symtable(src, path, "exec")
    except SyntaxError as e:
        return [f"{path}: 语法错误 {e}"]
    defined = {s.get_name() for s in top.get_symbols()}
    problems: list[str] = []

    def walk(table, path_names: list[str]) -> None:
        for sym in table.get_symbols():
            name = sym.get_name()
            if sym.is_global() and sym.is_referenced() and name not in defined and name not in ALLOWED:
                scope = ".".join(path_names + [table.get_name()])
                problems.append(f"{path}: {scope} 引用了未定义的全局 {name!r}")
        for child in table.get_children():
            walk(child, path_names + [table.get_name()])

    walk(top, [])
    # 去重（同名在不同作用域重复报没有意义）
    seen = set()
    out = []
    for p in problems:
        key = p.split("引用了未定义的全局")[-1]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def iter_py(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv", "python", ".git")]
        for f in filenames:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    paths: list[str] = []
    if args[0] == "--all":
        for root in args[1:]:
            paths += list(iter_py(root))
    else:
        paths = args

    bad = 0
    for p in paths:
        problems = check_file(p)
        if problems:
            bad += 1
            for msg in problems:
                print(f"✗ {msg}")
    if bad:
        print(f"\n✗ {bad} 个文件存在未定义全局引用")
        return 1
    print(f"✓ {len(paths)} 个文件：所有全局引用都有定义")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
