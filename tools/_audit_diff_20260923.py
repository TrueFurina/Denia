# -*- coding: utf-8 -*-
"""差分审计：把「未定义名」检测跑在 补丁后(现文件) 与 补丁前(备份) 上，取差集。

朴素 AST 审计会有大量误报（函数内 import、闭包变量、TYPE_CHECKING 分支）。
所以判据不是"有没有报"，而是**补丁前后报出来的名字集合是否一致** ——
只多出来的那些才是本次改动引入的风险点。
"""
import ast
import builtins
import io
import os
import sys

ROOT = r'G:\ai对话\11'
os.chdir(ROOT)
BAK = '_bak_soul_20260923_2300'
BUILTINS = set(dir(builtins)) | {'__file__', '__name__', '__package__', '__doc__',
                                 'True', 'False', 'None'}


def module_defined(tree):
    names = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
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
        elif isinstance(node, (ast.Try, ast.If, ast.With, ast.For, ast.While)):
            stack.extend(node.body)
            stack.extend(getattr(node, 'orelse', []))
            stack.extend(getattr(node, 'finalbody', []))
            stack.extend(getattr(node, 'handlers', []))
            stack.extend(getattr(node, 'items', []))
    return names


def local_names(fn):
    out = set()
    args = getattr(fn, 'args', None)
    if args is not None:
        for a in list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs):
            out.add(a.arg)
        if args.vararg:
            out.add(args.vararg.arg)
        if args.kwarg:
            out.add(args.kwarg.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add((a.asname or a.name).split('.')[0])
        elif isinstance(n, ast.comprehension):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
    return out


def undefined(path):
    src = io.open(path, encoding='utf-8', errors='replace').read()
    tree = ast.parse(src)
    g = module_defined(tree) | BUILTINS
    bad = {}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        loc = local_names(fn)
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if n.id not in g and n.id not in loc:
                    bad.setdefault(n.id, set()).add(fn.name)
    return bad


FILES = ['src/llm/local_omni_duplex.py', 'src/companion/proactive.py']
ENGLISH = set()

fails = []
for path in FILES:
    bak = os.path.join(BAK, path.replace('/', '__'))
    cur_u = undefined(path)
    bak_u = undefined(bak) if os.path.exists(bak) else {}
    added = {k: v for k, v in cur_u.items() if k not in bak_u}
    print('=' * 72)
    print(path)
    print('   补丁前可疑名 %d 个 / 补丁后 %d 个' % (len(bak_u), len(cur_u)))
    if added:
        for k, v in sorted(added.items()):
            print('   x 新增可疑名 %-24s @ %s' % (k, ','.join(sorted(v))))
            fails.append('%s: 新增可疑名 %s' % (path, k))
    else:
        print('   ✔ 未引入任何新的未定义名（差值 = 0）')

print()
if fails:
    print('[FAIL] %d 项' % len(fails))
    for f in fails:
        print('   x', f)
    sys.exit(1)
print('[PASS] 差分审计通过：改动没引入新的未定义名')
