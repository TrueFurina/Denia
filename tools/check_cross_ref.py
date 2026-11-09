# -*- coding: utf-8 -*-
"""跨模块引用对账：`from X import y` 和 `mod.attr` 指向的符号，必须真的存在。

为什么需要这道闸门
------------------
同一个形状在本仓已经出现过**三次**，全部在通知窗那条线上：

  · 第十五轮 `create_notify_window`：`from .mode_notify import NotifyJsBridge` —— 无此类
  · 第十九轮 `main_window.py:248`  ：`_mn._notify_slide_if(...)`        —— 无此函数
  · 第二十一轮 `notify_geom.py:107` ：`_mn._push_cmd({"type": "reset"})` —— 无此函数

三次的根因是同一个：**通知窗从 WebView2 迁到 WPF 原生窗之后，旧 API 的调用点没跟着删。**
`mode_notify._win` 的注释写着「迁移到 WPF 原生窗后已无此对象」，但调用方还在按 WebView2
时代的样子调它。

三次都没被发现，因为三次都被 `except Exception: pass` 或 `if False:` 包着 ——
**AttributeError 不会冒出来，只是那个功能静默地什么都不做。**

判据
----
两层，置信度不同，分开判：

  L1  `from M import y` —— M 能解析到本地模块时，y 必须是 M 的顶层名字，或是 M 的子模块
  L2  `mod.attr`        —— mod 绑定到本地模块时，attr 必须是该模块的顶层名字

四条限定（都是量出来的，缺一条就会变成噪声源）：

  ① 只认**本地**模块：`import requests` / `from yaml import safe_load` 一律不管。
     外部包的符号表不在本仓，判不了就不判——「无法判定 ≠ 通过」在这里的体现是**不报**，
     而不是猜。
  ② 被 `import *` 污染的模块跳过：它的符号表不可静态确定。
  ③ 名字若在同文件里被重新绑定（`va = RecVA()` 覆盖了 `import ... as va`），不再当模块别名。
     实测这一条挡掉了 `test_desktop_watch_hotapply.py` 的假阳性。
  ④ `hasattr(x, "y")` / `getattr(x, "y", 默认)` 里出现过的 (x, y)，`x.y` 允许不存在 ——
     **代码自己声明了「它可能没有」**，闸门不该比代码更确定。
     实测这一条挡掉了 `_smoke_import_20260924.py` 的假阳性（那里是在探测一个未确认的类名）。

豁免
----
确属动态挂载（`globals()["x"] = ...`、`setattr(module, ...)`）而闸门判不了的，写
`# crossref-ok: <原因>`。**不要用它给「忘了删旧调用」开脱**——那种应该去删。

用法
----
    python tools/check_cross_ref.py
    python tools/check_cross_ref.py --root <仓根>

退出码：0 干净 / 1 有命中 / 2 无法判定（扫到 0 个 .py、有文件解析失败）
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

MARKER = "crossref-ok"
# 模块自带的 dunder 属性，AST 里没有定义但一定存在
DUNDER = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
          "__dict__", "__path__", "__builtins__", "__all__", "__cached__", "__annotations__"}
# 【2026-11-09】python/ 是【内置运行时 + 标准库】（本机 933 个 .py），不是项目源码；
# 扫它会把 `from python.Lib.multiprocessing import ...` 这类标准库内部导入当成跨模块
# 引用误报 11 处。交付包里它是被分发物，源码仓里它只是运行时副本，一律跳过。
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", "dist", "build", "python"}


def _collect(root: str) -> list[str]:
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                out.append(os.path.join(dp, f))
    return out


def _modname(path: str, root: str) -> str:
    r = os.path.relpath(path, root).replace("\\", "/")[:-3]
    if r.endswith("/__init__"):
        r = r[: -len("/__init__")]
    return r.replace("/", ".")


def _top_names(tree: ast.Module) -> tuple[set[str], bool]:
    """模块顶层定义的名字；第二个返回值表示是否被 `import *` 污染。"""
    names: set[str] = set()
    star = False

    def take(stmts) -> None:
        nonlocal star          # 少了这句，`star = True` 只是 take 的局部变量，污染标记永远传不出去
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(st.name)
            elif isinstance(st, ast.Assign):
                for t in st.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
                    elif isinstance(t, ast.Tuple):
                        for e in t.elts:
                            if isinstance(e, ast.Name):
                                names.add(e.id)
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                names.add(st.target.id)
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                for a in st.names:
                    if a.name == "*":
                        star = True
                    else:
                        names.add(a.asname or a.name.split(".")[0])
            elif isinstance(st, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                take(st.body)
                for h in getattr(st, "handlers", []) or []:
                    take(h.body)
                take(getattr(st, "orelse", []) or [])
                take(getattr(st, "finalbody", []) or [])

    take(tree.body)
    names |= DUNDER
    return names, star


def _rebound(tree: ast.Module) -> set[str]:
    """文件里任何位置被赋值过的名字——这些名字不是稳定的模块别名。"""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            for e in ast.walk(n.target):
                if isinstance(e, ast.Name):
                    out.add(e.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
    return out


def _declared_optional(tree: ast.Module) -> set[tuple[str, str]]:
    """`hasattr(x, "y")` / `getattr(x, "y", ...)` 声明过的 (x, y)。"""
    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Name) and f.id in ("hasattr", "getattr")):
            continue
        if len(n.args) < 2 or not isinstance(n.args[0], ast.Name):
            continue
        a1 = n.args[1]
        if isinstance(a1, ast.Constant) and isinstance(a1.value, str):
            out.add((n.args[0].id, a1.value))
    return out


def scan(root: str) -> tuple[list[str], list[str]]:
    files = _collect(root)
    trees: dict[str, ast.Module] = {}
    errors: list[str] = []
    for p in files:
        try:
            trees[p] = ast.parse(io.open(p, "r", encoding="utf-8", errors="replace").read(),
                                 filename=p)
        except (OSError, SyntaxError) as e:
            errors.append("%s：解析失败（%s）"
                          % (os.path.relpath(p, root).replace("\\", "/"), e))

    by_mod = {_modname(p, root): p for p in trees}
    pkgs: set[str] = set()
    for m in by_mod:
        parts = m.split(".")
        for i in range(1, len(parts)):
            pkgs.add(".".join(parts[:i]))
    info = {p: _top_names(t) for p, t in trees.items()}

    hits: list[str] = []

    def add(p: str, line: int, msg: str) -> None:
        src = io.open(p, "r", encoding="utf-8", errors="replace").read()
        if MARKER in src:
            return
        hits.append("%-52s :%-6d %s" % (os.path.relpath(p, root).replace("\\", "/"), line, msg))

    # ---- L1 ----
    for p, t in trees.items():
        me = _modname(p, root)
        is_init = os.path.relpath(p, root).replace("\\", "/").endswith("__init__.py")
        # 包内 __init__.py 里的 `.` 指包自己；普通模块指父目录
        pkg = me if is_init else (me.rsplit(".", 1)[0] if "." in me else "")
        for n in ast.walk(t):
            if not isinstance(n, ast.ImportFrom):
                continue
            if n.level:
                base = pkg.split(".") if pkg else []
                if n.level > 1:
                    base = base[: len(base) - (n.level - 1)]
                target = ".".join([x for x in base if x] + ([n.module] if n.module else []))
            else:
                target = n.module or ""
            if not target:
                continue
            tgt = by_mod.get(target)
            if tgt is None:
                if target not in pkgs:
                    continue                      # 外部包，不判
                for a in n.names:
                    if a.name == "*":
                        continue
                    sub = target + "." + a.name
                    if sub not in by_mod and sub not in pkgs:
                        add(p, n.lineno, "from %s import %s —— 包内无此子模块" % (target, a.name))
                continue
            names, star = info[tgt]
            if star:
                continue
            for a in n.names:
                if a.name == "*" or a.name in names:
                    continue
                if (target + "." + a.name) in by_mod:
                    continue
                add(p, n.lineno, "from %s import %s —— 目标模块无此名字" % (target, a.name))

    # ---- L2 ----
    for p, t in trees.items():
        me = _modname(p, root)
        is_init = os.path.relpath(p, root).replace("\\", "/").endswith("__init__.py")
        pkg = me if is_init else (me.rsplit(".", 1)[0] if "." in me else "")
        alias: dict[str, str] = {}
        for n in ast.walk(t):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if not a.asname:
                        continue
                    cand = (n.module + "." + a.name) if n.module else \
                           ((pkg + "." if pkg else "") + a.name if n.level else "")
                    if cand in by_mod:
                        alias[a.asname] = cand
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.asname and a.name in by_mod:
                        alias[a.asname] = a.name
        if not alias:
            continue
        # 【性能】只有确实存在「本地模块别名」的文件才需要这两次全量 walk。
        # 714 个文件里绝大多数一个别名都没有，先算它们等于白扫一遍。
        rebound = _rebound(t)
        optional = _declared_optional(t)
        alias = {k: v for k, v in alias.items() if k not in rebound}
        if not alias:
            continue
        for n in ast.walk(t):
            if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                continue
            tgt_mod = alias.get(n.value.id)
            if not tgt_mod:
                continue
            if (n.value.id, n.attr) in optional:
                continue
            names, star = info[by_mod[tgt_mod]]
            if star or n.attr in names:
                continue
            add(p, n.lineno, "%s.%s —— 模块 %s 无此属性"
                % (n.value.id, n.attr, tgt_mod))

    return hits, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="跨模块引用对账")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    if not os.path.isdir(root):
        print("✗ 根目录不存在：%s，无法判定" % root, file=sys.stderr)
        return 2
    n_files = len(_collect(root))
    if n_files == 0:
        print("✗ 一个 .py 都没扫到，无法判定（0 命中不算通过）", file=sys.stderr)
        return 2

    hits, errors = scan(root)
    for h in hits:
        print(h)
    print("\n扫描 %d 个文件 / 命中 %d 处" % (n_files, len(hits)))

    if errors:
        print("\n✗ 有文件没能判定（不能当「没问题」）：", file=sys.stderr)
        for e in errors:
            print("  %s" % e, file=sys.stderr)
        return 2
    if hits:
        print("\n✗ 引用必须指向真实存在的符号。动态挂载请加 `# %s: <原因>`。" % MARKER,
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
