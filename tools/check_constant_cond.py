# -*- coding: utf-8 -*-
"""恒真/恒假条件扫描：条件里出现字面量，让整个判断恒定。

为什么这是缺陷而不是风格
------------------------
恒真的条件有两个后果，都不出声：

1. **一半代码是死的**。`if False:` 后面的块永远不执行；`if True: ... else: ...` 的 else
   永远不执行。编译不报错、测试不报错、跑起来也看不出来——只有人眼读才可能发现。
2. **判据是假的**。`assert x or True`、`check(x or True, "…")` 永远通过，
   于是「有测试守着」变成一句空话。本仓实测两条（`test_doctor_llm_channel.py`），
   其中一条还和下面几行的判据互相矛盾——因为它永远通过，矛盾一直没暴露。

本仓实锤（2026-11-08，7 处）：
  · `main_window.py` 的 `if False:` 关着 13 行 WebView2 滑窗逻辑，**里面调的
    `mode_notify._notify_slide_if` 根本不存在**（全仓只有这里两处引用 + 一处注释）
    —— 谁把 False 改成 True，第一句就是 AttributeError，还被 except 吞掉。
  · `agent_pro/__init__.py` 的 `if k in DEFAULTS or True` 让「只收认识的键」这层
    过滤从写下那天起没生效过。
  · `test_doctor_llm_channel.py` 两条 `... or True` 判据永远通过。

判定规则
--------
只看**条件位置**——`if` / `while` / 三元 / `assert` / 推导式的 `if` / `match` 的 guard，
外加本仓的断言原语 `check(<cond>, msg)` 的第一个实参。

为什么不查所有位置：`v = a or True` 在**值**位置不是常量（a 为真时返回 a），
把它当缺陷报是误报。只在条件位置它才必然恒真。这条限定用 M9 刀钉着。

退出码：0 干净 / 1 有命中 / 2 无法判定（解析失败、路径不存在、一个文件都没扫到）
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

MARKER = "const-cond-ok"


def _exempt(lines: list[str], lineno: int) -> bool:
    """本行或上方连续注释块里是否有豁免标记。空行终止（防止借来上一段的标记）。"""
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


def const_value(expr: ast.expr):
    """表达式是否恒为 True / False。返回 True/False；不是常量返回 None。"""
    if isinstance(expr, ast.Constant):
        return bool(expr.value)
    if isinstance(expr, ast.BoolOp):
        vals = [const_value(v) for v in expr.values]
        if isinstance(expr.op, ast.Or):
            if any(v is True for v in vals):
                return True          # a or True  → 恒真
            if vals and all(v is False for v in vals):
                return False
        else:                        # And
            if any(v is False for v in vals):
                return False         # a and False → 恒假
            if vals and all(v is True for v in vals):
                return True
        return None
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        v = const_value(expr.operand)
        return None if v is None else (not v)
    return None


def _conditions(tree: ast.AST):
    """产出 (表达式, 行号, 说明, 死掉的语句数 or None)。"""
    for n in ast.walk(tree):
        if isinstance(n, (ast.If, ast.While)):
            dead = None
            v = const_value(n.test)
            if v is False:
                dead = sum((s.end_lineno or s.lineno) - s.lineno + 1 for s in n.body)
            elif v is True and n.orelse:
                dead = sum((s.end_lineno or s.lineno) - s.lineno + 1 for s in n.orelse)
            # `while True:` 是惯用法（无限循环 + break），不是缺陷；只有带 else 时才报
            # （那种 else 永远不执行）。这条限定用 M8 刀钉着。
            if isinstance(n, ast.While) and v is True and not n.orelse:
                continue
            yield n.test, n.test.lineno, ("if" if isinstance(n, ast.If) else "while"), dead
        elif isinstance(n, ast.IfExp):
            yield n.test, n.test.lineno, "三元", None
        elif isinstance(n, ast.Assert):
            yield n.test, n.test.lineno, "assert", None
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                for cond in gen.ifs:
                    yield cond, cond.lineno, "推导式 if", None
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "check":
            # 只认 `x or True` / `x and False` 这种**布尔运算**形态。
            # 不能把「第一个实参是字面量」当缺陷：本仓的 check 有好几种签名
            # （`check(cond, msg)` / `check("说明", cond)` / `check("说明")`），
            # 第一种以外都会把字符串当成"恒真条件"报出来——一上来就是几百条噪声。
            if n.args and isinstance(n.args[0], (ast.BoolOp, ast.UnaryOp)):
                yield n.args[0], n.args[0].lineno, "check() 判据", None
        elif isinstance(n, ast.Match):
            for case in n.cases:
                if case.guard is not None:
                    yield case.guard, case.guard.lineno, "match guard", None


def scan_file(path: str) -> tuple[list[str], str]:
    try:
        src = io.open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        return [], "读不了：%s" % e
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [], "解析失败：第 %s 行 %s" % (e.lineno, e.msg)
    lines = src.split("\n")

    out = []
    for expr, lineno, where, dead in _conditions(tree):
        v = const_value(expr)
        if v is None:
            continue
        if _exempt(lines, lineno):
            continue
        if v is False:
            tail = "，其下 %d 行永不执行" % dead if dead else "，永远不成立"
            out.append("%s:%d  %s 条件恒假%s" % (path.replace("\\", "/"), lineno, where, tail))
        else:
            tail = "，else 里 %d 行永不执行" % dead if dead else "，这层判断是多余的"
            out.append("%s:%d  %s 条件恒真%s" % (path.replace("\\", "/"), lineno, where, tail))
    return out, ""


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
    ap = argparse.ArgumentParser(description="恒真/恒假条件扫描")
    ap.add_argument("paths", nargs="*", default=[], help="文件或目录")
    ap.add_argument("--all", action="store_true", help="按目录递归（闸门里用这个）")
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
        rows, err = scan_file(p)
        if err:
            errors.append("%s：%s" % (p, err))
            continue
        for r in rows:
            total += 1
            print(r)

    print("\n扫描 %d 个文件 / 命中 %d 处" % (len(files), total))

    if errors:
        print("\n✗ 有文件没能判定（不能当「没问题」）：", file=sys.stderr)
        for e in errors:
            print("  %s" % e, file=sys.stderr)
        return 2
    if total:
        print("\n✗ 条件不许恒真/恒假。确属有意请加 `# %s: <原因>` 豁免。" % MARKER,
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
