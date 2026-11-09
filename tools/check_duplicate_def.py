# -*- coding: utf-8 -*-
"""同名定义静默覆盖闸门（2026-11-08）。

为什么需要它：`src/companion/relationship_v2.py` 里 `relation_block_text_v2()` 被定义了
**两次**（第 399 行与第 702 行，相隔 300 行），后一个在 import 时静默重绑前一个——
那 37 行从写下那天起就没执行过。比"不可达代码"更坑的地方在于：

  · 不可达代码至少还看得见（`return` 后面跟着一堆东西）；
  · 静默覆盖的两份**都长得像活的**。谁去改第 399 行那份，改完跑一遍——毫无效果，
    而且不会有任何报错。真正生效的是 300 行开外的那份，还不一定记得它存在。

判据（只看语法，不做作用域求解）：
  同一作用域（模块 / 函数 / 类）内，同名 `def` / `async def` / `class` 出现两次以上，
  且**前一个与后一个都没有装饰器** → 后一个静默覆盖前一个。

为什么要"两边都没装饰器"这个限定：本仓真实存在的两种合法写法都会被它误伤——
  · `@property def x` + `@x.setter def x`（`src/ui/pet_shell.py`、`src/companion/proactive.py`）；
  · `@_t("用例名") def _(): ...` 连续写几十个（`src/agent_pro/selftest.py`，装饰器即时执行）。
去掉这个限定，全仓 42 处"重复"里 41 处是这两种范式，闸门立刻变成噪声源。
限定之后全仓只剩 1 处真问题（就是上面那个），修完为 0。

已知盲区（宁可漏报不误报）：
  · 前一个带装饰器、后一个不带的覆盖，本闸门不报（无法判断装饰器是否有副作用）；
  · `if/else` 两支各定义一个同名函数不算（分属不同作用域块，本来就是条件定义）；
  · 跨作用域的重名（父函数与子函数）不算。

豁免：后一个定义那行、或它**紧邻的连续注释块**里含 `# duplicate-ok: <原因>` 即放行
（与 `tools/check_unreachable.py` 的 `unreachable-ok` 同构；空行是终止符）。

用法：
  python tools/check_duplicate_def.py --all src tools main.py
  python tools/check_duplicate_def.py src/companion

退出码：0 = 干净；1 = 有静默覆盖；2 = 闸门自身坏（有文件解析不了，无法判定）。
"""

from __future__ import annotations

import ast
import io
import os
import sys

MARKER = "duplicate-ok"
SKIP_DIRS = {"__pycache__", ".git", "_archive", "node_modules", ".venv", "python"}

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _blocks(node: ast.AST):
    """该节点直接拥有的语句块（每块只被拥有者产出一次）。"""
    for name in ("body", "orelse", "finalbody"):
        v = getattr(node, name, None)
        if isinstance(v, list) and v and isinstance(v[0], ast.stmt):
            yield v
    for h in getattr(node, "handlers", None) or []:
        if h.body:
            yield h.body
    for c in getattr(node, "cases", None) or []:
        if c.body:
            yield c.body


def _exempt(lines: list, lineno: int) -> bool:
    """后一个定义那行，或它紧邻的连续注释块里带标记即豁免（空行终止）。"""
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


def scan_file(path: str) -> tuple[list, str | None]:
    """返回 (命中列表, 错误)。命中项 = (后一个定义的行号, 名字, 前一个定义的行号)。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except (SyntaxError, ValueError) as e:
        return [], "%s: %s" % (path, e)

    lines = src.split("\n")
    hits = []
    for node in ast.walk(tree):
        for block in _blocks(node):
            seen: dict = {}
            for s in block:
                if not isinstance(s, _DEFS):
                    continue
                # 任一边带装饰器就放行：property setter / 注册式写法都是合法范式
                if s.decorator_list:
                    continue
                if s.name in seen:
                    if not _exempt(lines, s.lineno):
                        hits.append((s.lineno, s.name, seen[s.name]))
                else:
                    seen[s.name] = s.lineno
    return hits, None


def _iter_py(target: str):
    if os.path.isfile(target):
        if target.endswith(".py"):
            yield target
        return
    for dp, dns, fns in os.walk(target):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in sorted(fns):
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


def main(argv: list) -> int:
    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        paths = ["src", "tools", "main.py"]

    n_files = 0
    n_hits = 0
    errors = []
    for target in paths:
        if not os.path.exists(target):
            errors.append("%s: 路径不存在" % target)
            continue
        for path in _iter_py(target):
            n_files += 1
            hits, err = scan_file(path)
            if err:
                errors.append(err)
                continue
            for lineno, name, first in hits:
                n_hits += 1
                print("  %s:%d  `%s` 与第 %d 行同名——后者静默覆盖前者，前一份是死代码"
                      % (path.replace(os.sep, "/"), lineno, name, first))
                print("    改前一份不会有任何效果。若确属有意（如平台分支重定义），"
                      "在后一行或它上方的注释块里加 `# %s: <原因>`。" % MARKER)

    print()
    print("扫描 %d 个文件：同名覆盖 %d 处，解析失败 %d 个" % (n_files, n_hits, len(errors)))
    if errors:
        for e in errors[:10]:
            print("  ! " + e[:200])
        print("闸门自身无法判定（有文件解析不了）→ exit 2")
        return 2
    if n_hits:
        print("同名定义静默覆盖 %d 处 → exit 1" % n_hits)
        return 1
    print("干净：同一作用域内没有无装饰器的同名定义")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
