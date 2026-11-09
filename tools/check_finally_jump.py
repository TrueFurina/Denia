# -*- coding: utf-8 -*-
"""finally 里跳出：`finally` 块中出现 return / break / continue。

为什么这是缺陷而不是风格
------------------------
`finally` 里的 return 有两条副作用，都**不报错**：

1. **吞异常**。`try` 体（或 except 分支自身）正在抛出异常时，finally 里的 return
   会把这个异常直接丢掉，函数正常返回。上层看到的是「什么都没发生」，
   日志里一个字都没有 —— 这正是「出错零痕迹」里最难查的一类。
2. **跳过其后的清理**。return 退出的是整个函数，finally 里排在它后面的语句
   （关连接、删临时文件、摘任务表…）一条都不执行。作者通常只想着「这里提前收工」，
   没意识到自己同时把后半段清理也删了。

本仓实锤（2026-11-08）：`src/ui/webapp.py` 的 `_run_voice_test` 在 finally 开头写
`if backend is None: return`，于是「没配引擎就点试听」这条早退路径每次都跳过
`_voice_tasks.pop` —— 任务表只进不出。全仓 696 个文件只有这一处，改完即 0。

判定规则
--------
- 只看 `Try.finalbody` 里的语句，不跨函数/类作用域（嵌套 def 里的 return 是它自己的事）
- `break`/`continue` 只有在 finally 内**没有**外层循环时才报（`finally` 里自己写了
  for 然后 break，绑的是那个 for，合法）
- 不 import 被检查文件，纯 AST，可在任何解释器上跑

豁免
----
确属有意（例如刻意用 return 屏蔽某个异常）时，在跳出的那一行，或它上方的
**连续注释块**里写 `# finally-ok: <原因>`。不用行号白名单——行号漂移后闸门
会静默失效（`check_web_js_syntax` 曾经就是这么变成装饰闸门的）。

用法
----
    python tools/check_finally_jump.py --all src tools main.py
    python tools/check_finally_jump.py src/ui/webapp.py     # 单文件

退出码：0 干净 / 1 有命中 / 2 无法判定（解析失败、路径不存在、一个文件都没扫到）
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

MARKER = "finally-ok"

_FUNC_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_TRY = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())


def _exempt(lines: list[str], lineno: int) -> bool:
    """本行或上方连续注释块里是否有豁免标记。空行终止（防止把上一段的标记借过来）。"""
    if 1 <= lineno <= len(lines) and MARKER in lines[lineno - 1]:
        return True
    n = lineno - 1
    while 1 <= n <= len(lines):
        s = lines[n - 1].strip()
        if not s or not s.startswith("#"):
            return False
        if MARKER in s:
            return True
        n -= 1
    return False


def _walk(stmts: list[ast.stmt], in_loop: bool, out: list[tuple[int, str]]) -> None:
    """在 finally 块里找跳出语句。不进入嵌套的函数/类作用域。"""
    for s in stmts:
        if isinstance(s, ast.Return):
            out.append((s.lineno, "return"))
            continue
        if isinstance(s, (ast.Break, ast.Continue)):
            if not in_loop:
                out.append((s.lineno, "break" if isinstance(s, ast.Break) else "continue"))
            continue
        if isinstance(s, _FUNC_SCOPE):
            # 嵌套 def/class/lambda 里的 return 属于它自己，不是本函数的 finally 跳出
            continue
        inner = in_loop or isinstance(s, _LOOPS)
        for field in ("body", "orelse", "finalbody"):
            sub = getattr(s, field, None)
            if isinstance(sub, list):
                _walk(sub, inner, out)
        for h in getattr(s, "handlers", None) or []:
            _walk(h.body, in_loop, out)
        for c in getattr(s, "cases", None) or []:
            _walk(c.body, in_loop, out)


def _scan_file(path: str) -> tuple[list[tuple[int, str, str]], str]:
    """返回 ([(行号, 跳出关键字, 所在函数)], 错误信息)。"""
    try:
        src = io.open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        return [], "读不了：%s" % e
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [], "解析失败：第 %s 行 %s" % (e.lineno, e.msg)
    lines = src.split("\n")

    hits: list[tuple[int, str, str]] = []
    # 逐层记录所在函数名，报错时能直接定位
    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, _TRY):
                found: list[tuple[int, str]] = []
                _walk(child.finalbody, False, found)
                for lineno, kw in found:
                    if not _exempt(lines, lineno):
                        hits.append((lineno, kw, name))
            visit(child, name)

    visit(tree, "<module>")
    return hits, ""


def _collect(paths: list[str]) -> tuple[list[str], list[str]]:
    files: list[str] = []
    errors: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for dp, dn, fn in os.walk(p):
                dn[:] = [d for d in dn if d not in
                         ("__pycache__", ".git", ".venv", "site-packages", "node_modules")]
                files += [os.path.join(dp, f) for f in fn if f.endswith(".py")]
        elif os.path.isfile(p):
            if p.endswith(".py"):
                files.append(p)
        else:
            errors.append("%s：路径不存在" % p)
    return sorted(set(files)), errors


def main() -> int:
    ap = argparse.ArgumentParser(description="finally 里跳出（return/break/continue）扫描")
    ap.add_argument("paths", nargs="*", default=[], help="文件或目录")
    ap.add_argument("--all", action="store_true", help="按目录递归（闸门里用这个）")
    ap.add_argument("--list", action="store_true", help="逐条列出（默认就列）")
    args = ap.parse_args()

    paths = args.paths or ["src"]
    files, errors = _collect(paths)
    if not files:
        print("✗ 一个 .py 都没扫到，无法判定（0 命中不算通过）", file=sys.stderr)
        for e in errors:
            print("  %s" % e, file=sys.stderr)
        return 2

    total = 0
    for p in files:
        hits, err = _scan_file(p)
        if err:
            errors.append("%s：%s" % (p, err))
            continue
        for lineno, kw, func in hits:
            total += 1
            print("%s:%d  %s() 的 finally 里 %s —— 会吞掉正在抛出的异常，并跳过其后的清理"
                  % (p.replace("\\", "/"), lineno, func, kw))

    print("\n扫描 %d 个文件 / 命中 %d 处" % (len(files), total))

    if errors:
        print("\n✗ 有文件没能判定（不能当「没问题」）：", file=sys.stderr)
        for e in errors:
            print("  %s" % e, file=sys.stderr)
        return 2
    if total:
        print("\n✗ finally 里不许跳出。确属有意请加 `# %s: <原因>` 豁免。" % MARKER,
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
