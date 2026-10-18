# -*- coding: utf-8 -*-
"""静默异常扫描：找出 `except ...: pass` 的位置，并区分「有注释的降级」与「真空吞」。

为什么需要区分
--------------
本仓 657 处 except 块的 body 只有 pass。其中大量带 `# noqa: BLE001` 和说明注释，
属于有意识的降级保护（可选设备、托盘、贴纸…），不该一刀切改。

真正危险的是**主链路上的真静默**：通话 / 对话 / TTS 里 `except: pass`，
NameError 和「设备不存在」长得一模一样，日志里一条都看不到——
2026-10-17 抓到两个 NameError（`return out`、缺 logger）就是这么被藏住的。

判定规则
--------
- body 只有 pass/continue/return None → 静默
- 该 except 块范围内出现 `#` 注释 → 记为「有注释」（多半是有意识降级）
- 否则 → 记为「真静默」，主链路里出现即视为需要处理

用法
----
    python tools/check_silent_except.py --all src
    python tools/check_silent_except.py --main            # 只扫三条主链路
    python tools/check_silent_except.py --main --strict   # 主链路真静默 → exit 1
"""
from __future__ import annotations

import argparse
import ast
import os

# 主链路：用户能直接感知「没反应」的三条路径
MAIN_CHAIN = [
    "src/llm/cloud_omni_realtime.py",
    "src/companion/dialogue.py",
    "src/voice/tts.py",
]


def _is_silent(handler: ast.ExceptHandler) -> bool:
    if not handler.body:
        return False
    for stmt in handler.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Continue):
            continue
        if isinstance(stmt, ast.Return) and (stmt.value is None or
                                            (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)):
            continue
        return False
    return True


def scan(path: str) -> tuple[list[dict], str | None]:
    """返回 (命中行, 解析错误说明)。

    解析失败必须**上报**而不是返回空：否则主链路文件被改坏（语法错）时，
    闸门反而更"绿"——少扫一个文件、总数还变小。2026-10-18 实测踩到：
    往 tts.py 注入一段语法错误，闸门 0 处真静默、退出码 0。
    """
    src = open(path, encoding="utf-8", errors="replace").read()
    lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [], f"SyntaxError: {e.msg}（第 {e.lineno} 行）"

    funcs: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                funcs.setdefault(ln, node.name)

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not _is_silent(node):
            continue
        # 注解：块范围内有没有注释
        start = node.lineno
        end = node.body[-1].end_lineno or node.body[-1].lineno
        has_comment = any("#" in (lines[i - 1] or "") for i in range(start, end + 1))
        exc = ast.unparse(node.type) if node.type else "bare except"
        out.append({
            "line": node.lineno,
            "func": funcs.get(node.lineno, "<module>"),
            "exc": exc,
            "comment": has_comment,
        })
    return out, None


def iter_py(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv", "python", ".git", "tests")]
        for f in filenames:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", help="文件或目录")
    ap.add_argument("--all", metavar="DIR", help="递归扫描目录")
    ap.add_argument("--main", action="store_true", help="只扫三条主链路")
    ap.add_argument("--strict", action="store_true", help="主链路存在真静默即 exit 1")
    ap.add_argument("--list", action="store_true", help="列出每一处")
    args = ap.parse_args()

    paths: list[str] = []
    if args.main:
        paths = MAIN_CHAIN
    elif args.all:
        paths = list(iter_py(args.all))
    else:
        paths = args.targets
    if not paths:
        print(__doc__)
        return 2

    total = silent_nc = 0
    per_file: list[tuple[int, int, str]] = []
    errors: list[tuple[str, str]] = []
    for p in paths:
        if not os.path.exists(p):
            errors.append((p, "文件不存在"))
            continue
        rows, err = scan(p)
        if err:
            errors.append((p, err))
            continue
        if not rows:
            continue
        real = [r for r in rows if not r["comment"]]
        total += len(rows)
        silent_nc += len(real)
        per_file.append((len(real), len(rows), p))
        if args.list:
            for r in real:
                print(f"  {p}:{r['line']}  {r['func']}()  {r['exc']} -> pass（无注释）")

    per_file.sort(reverse=True)
    print(f"{'真静默':>6} {'总计':>6}  文件")
    for real, allc, p in per_file[:25]:
        print(f"{real:6d} {allc:6d}  {p}")
    print(f"\n合计：{silent_nc} 处真静默 / {total} 处静默（含带注释的降级）")

    if errors:
        print("\n✗ 有文件没能扫（不能当「没问题」）：", file=__import__("sys").stderr)
        for p, e in errors:
            print(f"  {p}: {e}", file=__import__("sys").stderr)

    if args.strict and args.main and (silent_nc or errors):
        if silent_nc:
            print(f"\n✗ 主链路存在 {silent_nc} 处真静默：至少补 logger.debug(..., exc_info=True)",
                  file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
