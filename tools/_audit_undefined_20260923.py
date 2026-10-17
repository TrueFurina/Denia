# -*- coding: utf-8 -*-
"""交付前体检：在**无法真 import**（缺 ollama/torch 等重依赖）的情况下，
用 AST 找出「引用了但从未定义」的全局名 —— 这类 bug 会静默让整段功能失效。

同时专项确认本次新增的 _EARLY_SENTENCE_* 确实是模块级常量。
"""
import ast
import builtins
import io
import os
import sys

ROOT = r'G:\ai对话\11'
os.chdir(ROOT)

BUILTINS = set(dir(builtins)) | {'__file__', '__name__', '__package__', '__doc__',
                                 'self', 'cls', 'True', 'False', 'None'}


def module_defined(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in ast.walk(node):
                if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Try, ast.If)):
            for sub in node.body + getattr(node, 'orelse', []) + getattr(node, 'finalbody', []):
                names |= module_defined(ast.Module(body=[sub], type_ignores=[]))
    return names


def local_names(fn):
    out = set()
    for a in list(fn.args.args) + list(fn.args.kwonlyargs) + list(fn.args.posonlyargs):
        out.add(a.arg)
    if fn.args.vararg:
        out.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        out.add(fn.args.kwarg.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.comprehension,)):
            pass
    for n in ast.walk(fn):
        if isinstance(n, ast.comprehension):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


fails = []
ALL = ['src/llm/local_omni_duplex.py', 'src/companion/proactive.py']

for path in ALL:
    src = io.open(path, encoding='utf-8', errors='replace').read()
    tree = ast.parse(src)
    globals_ = module_defined(tree) | BUILTINS
    bad = {}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        loc = local_names(fn)
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if n.id not in globals_ and n.id not in loc:
                    bad.setdefault(n.id, []).append('%s:%d' % (fn.name, n.lineno))
    print('=' * 72)
    print('%s   函数 %d 个 / 模块级名 %d 个' % (path, sum(
        1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
        len(globals_)))
    if bad:
        for k, v in sorted(bad.items()):
            print('   x 未定义名 %-22s @ %s' % (k, ', '.join(v[:4])))
            fails.append('%s: 未定义名 %s' % (path, k))
    else:
        print('   ✔ 无未定义全局名')

# 专项：本次新增常量必须在模块级
src = io.open('src/llm/local_omni_duplex.py', encoding='utf-8', errors='replace').read()
tree = ast.parse(src)
g = module_defined(tree)
print()
for name in ('_EARLY_SENTENCE_TERM', '_EARLY_SENTENCE_MIN_CHARS'):
    ok = name in g
    print('   %s %s 是模块级常量' % ('✔' if ok else 'x', name))
    if not ok:
        fails.append('新增常量未在模块级定义：%s' % name)

print()
if fails:
    print('[FAIL] %d 项' % len(fails))
    for f in fails:
        print('   x', f)
    sys.exit(1)
print('[PASS] 审计通过')
