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


def _collect_bound(tree) -> set[str]:
    """收集「模块顶层真正绑定」的名字。

    不能用 symtable 的 get_symbols()：对 `_u32.Attr = 1` 这种属性赋值，
    symtable 会把 _u32 也记成模块级符号，于是"其实没定义"被误判成"已定义"。
    这里只认 Name 的 Store/Del、import、def/class —— 属性赋值里的对象是 Load。
    """
    ast_mod = __import__("ast")
    out: set[str] = set()
    stack = [tree]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef, ast_mod.ClassDef)):
            out.add(n.name)
            continue        # 不深入函数体：里面的赋值是局部绑定
        if isinstance(n, ast_mod.Name) and isinstance(n.ctx, (ast_mod.Store, ast_mod.Del)):
            out.add(n.id)
        elif isinstance(n, ast_mod.ExceptHandler) and n.name:
            out.add(n.name)      # `except X as e:` 的 e 是绑定，不是 Name 节点
        elif isinstance(n, (ast_mod.Import, ast_mod.ImportFrom)):
            for a in n.names:
                if a.name == "*":
                    continue
                out.add((a.asname or a.name).split(".")[0])
        stack.extend(ast_mod.iter_child_nodes(n))
    return out


def check_file(path: str) -> list[str]:
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        top = symtable.symtable(src, path, "exec")
        tree = __import__("ast").parse(src)
    except SyntaxError as e:
        return [f"{path}: 语法错误 {e}"]
    defined = _collect_bound(tree)
    problems: list[str] = []

    # 检查 A：`global X` 声明了 X，但模块顶层根本没定义 X。
    # 这种写法在「只做下标赋值」(d[k]=v) 时必然 NameError，且常被 except 吞掉。
    # 2026-10-17 拆 webapp → wv2_env 时就是这样丢了 WebView2 控件注册。
    for node in __import__("ast").walk(tree):
        if isinstance(node, __import__("ast").Global):
            for name in node.names:
                if name not in defined:
                    problems.append(f"{path}:{node.lineno} global 声明了未定义的模块级变量 {name!r}")

    # 检查 C：模块顶层「直接执行」的语句里引用了未定义名字。
    # 与 A 不同，这类问题在 import 瞬间就 NameError，连启动都过不去。
    # 2026-10-17 拆桌宠域时，webapp 末尾的 argtypes 声明引用了已迁走的 _u32 就是这么炸的。
    #
    # 只走"直接"层：进入 def/class/lambda 就停（那属于 A 的检查范围，且有自己的局部变量）；
    # 跳过 if __name__ == "__main__" 自测块（import 时不执行）。
    ast_mod = __import__("ast")

    def _is_main_guard(node) -> bool:
        if not isinstance(node, ast_mod.If):
            return False
        t = node.test
        return (
            isinstance(t, ast_mod.Compare)
            and isinstance(t.left, ast_mod.Name) and t.left.id == "__name__"
            and any(isinstance(c, ast_mod.Constant) and c.value == "__main__" for c in t.comparators)
        )

    def _direct_loads(root):
        found, stack = [], [root]
        while stack:
            n = stack.pop()
            if isinstance(n, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef,
                              ast_mod.ClassDef, ast_mod.Lambda, ast_mod.Import, ast_mod.ImportFrom)):
                continue
            if isinstance(n, ast_mod.Name) and isinstance(n.ctx, ast_mod.Load):
                found.append(n)
            stack.extend(ast_mod.iter_child_nodes(n))
        return found

    for node in tree.body:
        if isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef,
                             ast_mod.ClassDef, ast_mod.Import, ast_mod.ImportFrom)):
            continue
        if _is_main_guard(node):
            continue
        for x in _direct_loads(node):
            if x.id not in defined and x.id not in ALLOWED:
                problems.append(
                    f"{path}:{x.lineno} 模块顶层引用了未定义名字 {x.id!r}（import 时即 NameError）")

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
