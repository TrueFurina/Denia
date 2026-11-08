# -*- coding: utf-8 -*-
"""不可达代码闸门：必返回语句之后不许再有语句（2026-11-08）。

为什么需要它：`src/ui/main_window.py` 的 `create_notify_window()` 曾经 try 与 except
**两条分支都 return**，其后 329 行 WebView2 旧实现全部不可达，其中还 `import` 了一个
根本不存在的 `mode_notify.NotifyJsBridge`——谁删掉那两行 return 想"恢复旧实现"，
第一句就是 ImportError。这类缺陷编译不报错、测试不报错、跑起来也看不出来，
只有人眼逐个函数读才可能发现，于是它活了两个月。

判据（只看语法，不做控制流分析，宁漏不误）：
  一条语句「必定离开当前块」= Return / Raise / Continue / Break，或
    · if：有 else 且两支末句都必离开；
    · try：body 末句必离开，**全部** handler 末句必离开（无 handler 时不要求），
      else 末句必离开（若有）；finally 末句必离开也算；
  这样的语句之后若还有语句，就是不可达。

豁免：不可达语句**本身那行**或**它的上一行**含 `unreachable-ok` 注释即放行，
且必须带原因。真实存在的合法写法长这样（src/tests/test_dialogue_voice_chain.py）：

    raise RuntimeError("injected: llm down")
    # unreachable-ok: 故意不可达——这行 yield 只为把函数变成异步生成器
    yield ""

为什么不用行号白名单：行号会随任何编辑漂移，漂移后闸门**静默失效**且没人知道
（本仓在 check_web_js_syntax 的硬编码行号上踩过）。标记跟着代码走，删代码时标记
一起消失，不会留下一条"看起来还在保护、其实早就不指向任何东西"的白名单项。

用法：
  python tools/check_unreachable.py --all src tools main.py
  python tools/check_unreachable.py src/ui            # 只查一个子树

退出码：0 = 干净；1 = 有不可达代码；2 = 闸门自身坏（有文件解析不了，无法判定）。
"""

from __future__ import annotations

import ast
import io
import os
import sys

MARKER = "unreachable-ok"
SKIP_DIRS = {"__pycache__", ".git", "_archive", "node_modules", ".venv", "python"}

_TRY_TYPES = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())


def _always_exits(stmt: ast.stmt) -> bool:
    """这条语句执行完是否**必定**离开当前块。"""
    if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(stmt, ast.If):
        return (bool(stmt.orelse)
                and _always_exits(stmt.body[-1])
                and _always_exits(stmt.orelse[-1]))
    if isinstance(stmt, _TRY_TYPES):
        fin = getattr(stmt, "finalbody", None)
        if fin and _always_exits(fin[-1]):
            return True
        ok = bool(stmt.body) and _always_exits(stmt.body[-1])
        if stmt.handlers:
            ok = ok and all(h.body and _always_exits(h.body[-1]) for h in stmt.handlers)
        if stmt.orelse:
            ok = ok and _always_exits(stmt.orelse[-1])
        return ok
    return False


def _blocks(node: ast.AST):
    """该节点直接拥有的语句块（每块只被拥有者产出一次，不会重复）。"""
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
    """不可达语句那行，或它**紧邻的连续注释块**里带标记即豁免。

    向上扫连续注释行（空行是终止符——否则隔着几段无关注释的标记会误豁免整块）。
    支持"上面三行说明 + 最后一行是代码"的写法，而不是只认紧挨着的一行：
    豁免理由通常需要一句话解释，逼着人把说明塞进同一行会让注释变成一坨。
    """
    if 1 <= lineno <= len(lines) and MARKER in lines[lineno - 1]:
        return True
    n = lineno - 1
    while 1 <= n <= len(lines):
        s = lines[n - 1].strip()
        if not s:
            return False
        if not s.startswith("#"):
            return False
        if MARKER in s:
            return True
        n -= 1
    return False


def scan_file(path: str) -> tuple[list, str | None]:
    """返回 (命中列表, 错误)。命中项 = (行号, 必离开语句行号, 不可达条数)。"""
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
            for i, s in enumerate(block):
                if not _always_exits(s):
                    continue
                rest = block[i + 1:]
                if rest and not _exempt(lines, rest[0].lineno):
                    hits.append((rest[0].lineno, s.lineno, len(rest)))
                break
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
            for lineno, exit_lineno, count in hits:
                n_hits += 1
                print("  %s:%d  第 %d 行的语句必定离开，其后 %d 条语句不可达"
                      % (path.replace(os.sep, "/"), lineno, exit_lineno, count))
                print("    若确实有意（例如靠 yield 让函数成为生成器），在不可达语句那行"
                      "或上一行加 `# %s: <原因>`。" % MARKER)

    print()
    print("扫描 %d 个文件：不可达 %d 处，解析失败 %d 个" % (n_files, n_hits, len(errors)))
    if errors:
        for e in errors[:10]:
            print("  ! " + e[:200])
        print("闸门自身无法判定（有文件解析不了）→ exit 2")
        return 2
    if n_hits:
        print("不可达代码 %d 处 → exit 1" % n_hits)
        return 1
    print("干净：没有语句跟在必返回语句之后")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
