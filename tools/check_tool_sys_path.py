# -*- coding: utf-8 -*-
"""闸门的兄弟 import：必须先保证 `tools/` 在 sys.path 上，除非显式降级。

背景（同一族故障第二次复发，2026-11-09 实测）
------------------------------------------------
交付包自带的是**可嵌入运行时**，`python/python312._pth` 把 sys.path 写死成

    .  /  DLLs  /  Lib  /  Lib\\site-packages  /  …  /  ..

最后那个 `..` 是仓根 —— 所以 `main.py` 顶部的 `from src.xxx import` 能用，
但 **`tools/` 不在里面**。普通 CPython 会把「脚本所在目录」放进 sys.path[0]，
可嵌入运行时不补这一条。于是同一份代码：

    python  tools/check_all.py                     → 能跑（sys.path[0] == tools/）
    .venv/Scripts/python.exe tools/check_all.py    → ModuleNotFoundError: _gate_lock

而 `.venv` 恰恰是本项目推荐解释器（要三方依赖）。2026-11-09 就是这么崩的：
`check_all.py` 尾巴那句 `from _gate_lock import acquire, release` 在 .venv 下
**一行闸门都没跑就退出**，报 ModuleNotFoundError —— 看起来像环境坏了，本质是
「这道门在推荐解释器下从来起不来」。同一族还有 PR #17 的 check_web_js_syntax
（只把仓根塞进 sys.path，而 check_bridge_methods 在 tools/ 下）。

为什么不用「跑一遍试试」当判据
--------------------------------
那要求每道闸门都真跑起来，而闸门里有一半要声卡 / 模型 / 14 分钟；更关键的是
**跑得起来的那个解释器会掩盖跑不起来的那个**（上面第一行就是），试出来的绿
是假绿。所以这里只做静态判定。

判据（三条）
------------
  1.【正向】`check_*` / `test_*` / `_qc_*` 脚本里，凡 import 了兄弟模块
     （`tools/<名>.py` 存在），就必须在**模块层**有一次 sys.path 改写
     （模块层的改写在任何函数被调用之前就执行了），或在该 import 之前、
     同一/更外层作用域里有一次改写。
  2.【自洽】那次改写的实参必须由本脚本路径推导（`__file__` 或它的派生名，
     含 `for p in (ROOT, …)` 这种一层中转），不许写死盘符路径 ——
     写死的换台机器等于没写，闸门照样起不来。
  3.【覆盖】必须真的扫到兄弟 import。扫到 0 个 = 判据自己瞎了，不算通过。

不计分的两类（会打印出来，但不许当成通过）
------------------------------------------
  · 降级导入：写在 `try:` 里且该 try 接了 ImportError / 裸 except —— 这种
    写法自带退路（如 check_package_secrets 找不到 check_web_assets 就转警告），
    不属于「起不来」。
  · 免登脚本：docstring 里带「不进闸门：<原因>」的，整份跳过（沿用
    check_gate_registry 的约定），名单与原因列在末尾，不许静默少扫。

只管 `check_*` / `test_*` / `_qc_*`：`tools/` 下还有几十个 `_` 开头的一次性
研究脚本，它们本来就跑不到别的机器上，纳入进来只会把这道门变成噪音。

退出码：0 = 全部有着落；1 = 有闸门漏补或改写不合法；2 = 闸门自身坏。
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOOLS = HERE
MARKER = "不进闸门"
SCOPE_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
PATH_ATTRS = ("insert", "append", "extend")
IN_SCOPE = re.compile(r"^(?:check|test|_qc_)")

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  <- " + detail) if detail else ""))


def _sibling_names(tools_dir: str) -> set:
    return {fn[:-3] for fn in os.listdir(tools_dir) if fn.endswith(".py")}


def _is_path_mut(call: ast.Call) -> bool:
    f = call.func
    if not isinstance(f, ast.Attribute) or f.attr not in PATH_ATTRS:
        return False
    v = f.value
    return (isinstance(v, ast.Attribute) and v.attr == "path"
            and isinstance(v.value, ast.Name) and v.value.id == "sys")


def _mentions(node: ast.AST, names: set) -> bool:
    return any(isinstance(x, ast.Name) and x.id in names for x in ast.walk(node))


def _targets(node: ast.AST) -> list:
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.target]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [it.optional_vars for it in node.items if it.optional_vars]
    return []


def _tainted(tree: ast.AST) -> set:
    """由 `__file__` 派生的名字（含一层中转，如 `for p in (ROOT, …)`）。

    只是够用的一遍不动点：赋值 / for / with 的目标，只要右侧提到已知的
    派生名，就也算派生。三轮足够覆盖本仓实际写法，多了也不会更准。
    """
    names = {"__file__"}
    for _ in range(3):
        grew = False
        for node in ast.walk(tree):
            targets = _targets(node)
            if not targets:
                continue
            src = node.value if isinstance(node, ast.Assign) else getattr(
                node, "iter", None) or getattr(node, "context_expr", None)
            if src is None or not _mentions(src, names):
                continue
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name) and n.id not in names:
                        names.add(n.id)
                        grew = True
        if not grew:
            break
    return names


def _excepts_importerror(node: ast.Try) -> bool:
    for h in node.handlers:
        if h.type is None:
            return True
        for n in ast.walk(h.type):
            if isinstance(n, ast.Name) and n.id == "ImportError":
                return True
    return False


def _collect(tree: ast.AST) -> tuple:
    """返回 (imports, muts, guarded)。

    imports / muts 元素是 (node, 作用域链, 是否在降级 try 里)。
    """
    imports: list = []
    muts: list = []

    def walk(node: ast.AST, scopes: tuple, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            cur = scopes
            g = guarded
            if isinstance(child, SCOPE_NODES):
                cur = scopes + (id(child),)
            if isinstance(child, ast.Try):
                g = guarded or _excepts_importerror(child)
            if isinstance(child, ast.Call) and _is_path_mut(child):
                muts.append((child, cur, guarded))
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                imports.append((child, cur, g))
            walk(child, cur, g)

    walk(tree, (id(tree),), False)
    return imports, muts


def _sibling_hits(node: ast.AST, sibs: set) -> list:
    hits = []
    if isinstance(node, ast.Import):
        for a in node.names:
            top = a.name.split(".")[0]
            if top in sibs:
                hits.append(top)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        top = node.module.split(".")[0]
        if top in sibs:
            hits.append(top)
    return hits


def _justifies(mut: tuple, imp_scopes: tuple, imp_line: int) -> bool:
    """这次 sys.path 改写，是否真的会在该 import 之前执行。"""
    call, mut_scopes, _g = mut
    call_line = getattr(call, "lineno", 0)
    if len(imp_scopes) == 1:
        # import 本身在模块层：改写也必须在它前面（模块层也是自上而下执行的，
        # 「先 import 后补路径」照样起不来 —— 这一条踩过，别放宽）
        return call_line < imp_line
    if len(mut_scopes) == 1:
        # 函数里的 import：模块层的改写在任何函数被调用之前就执行完了，
        # 写在第几行都行
        return True
    return call_line < imp_line and all(s in imp_scopes for s in mut_scopes)


def _provenance_ok(mut: tuple, tainted: set) -> bool:
    call = mut[0]
    try:
        text = " ".join(ast.unparse(a) for a in call.args)
    except Exception:  # noqa: BLE001  老 ast 没有 unparse
        return False
    return any(n in text for n in tainted if n != "__file__") \
        or "__file__" in text


def _marker_reason(src: str) -> str:
    for ln in src.splitlines():
        s = ln.strip()
        for sep in ("：", ":"):
            if s.startswith(MARKER + sep):
                reason = s[len(MARKER) + 1:].strip()
                if reason:
                    return reason
    return ""


def _scan_one(path: str, sibs: set) -> dict:
    with io.open(path, encoding="utf-8") as f:
        src = f.read()
    r = {"name": os.path.basename(path), "src": src, "sibs": [],
         "missing": [], "bad_prov": [], "guarded": [], "parse": ""}
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as exc:
        r["parse"] = "第 %s 行：%s" % (exc.lineno, exc.msg)
        return r
    imports, muts = _collect(tree)
    tainted = _tainted(tree)
    for node, imp_scopes, guarded in imports:
        hits = _sibling_hits(node, sibs)
        if not hits:
            continue
        label = "%s（第 %d 行）" % (",".join(hits), getattr(node, "lineno", 0))
        if guarded:
            r["guarded"].append(label)
            continue
        r["sibs"].extend(hits)
        usable = [m for m in muts if _justifies(m, imp_scopes,
                                               getattr(node, "lineno", 0))]
        if not usable:
            r["missing"].append(label)
        elif not any(_provenance_ok(m, tainted) for m in usable):
            r["bad_prov"].append(label)
    return r


def main(argv: list[str]) -> int:
    tools_dir = DEFAULT_TOOLS
    if "--tools" in argv:
        i = argv.index("--tools")
        if i + 1 >= len(argv):
            print("FAIL --tools 后面没给目录")
            return 2
        tools_dir = os.path.abspath(argv[i + 1])
    if not os.path.isdir(tools_dir):
        print("FAIL 目录不存在：%s" % tools_dir)
        return 2

    files = sorted(fn for fn in os.listdir(tools_dir)
                   if fn.endswith(".py") and IN_SCOPE.match(fn))
    if not files:
        print("FAIL %s 下一个 check_/test_/_qc_ 脚本都没有 —— 判据自己瞎了" % tools_dir)
        return 2
    sibs = _sibling_names(tools_dir)

    scanned, skipped, judged = 0, [], []
    missing, bad_prov, guarded, bad_parse = [], [], [], []
    for fn in files:
        path = os.path.join(tools_dir, fn)
        try:
            r = _scan_one(path, sibs)
        except (OSError, UnicodeDecodeError) as exc:
            bad_parse.append("%s（读不了：%s）" % (fn, exc))
            continue
        if r["parse"]:
            bad_parse.append("%s（%s）" % (fn, r["parse"]))
            continue
        if not (r["sibs"] or r["guarded"]):
            scanned += 1
            continue
        reason = _marker_reason(r["src"])
        if reason:
            skipped.append((fn, reason))
            continue
        scanned += 1
        if r["sibs"]:
            judged.append((fn, len(r["sibs"])))
        if r["missing"]:
            missing.append("%s ← %s" % (fn, "；".join(r["missing"])))
        if r["bad_prov"]:
            bad_prov.append("%s ← %s" % (fn, "；".join(r["bad_prov"])))
        if r["guarded"]:
            guarded.append("%s ← %s" % (fn, "；".join(r["guarded"])))

    print("1) 兄弟 import 之前有没有把 tools/ 放进 sys.path")
    for fn, n in judged:
        miss = [x for x in missing if x.startswith(fn + " ←")]
        chk("%s（%d 处兄弟 import）" % (fn, n), not miss,
            "没有先补 sys.path，或补在了 import 之后 / 另一个函数里")

    print("\n2) 补的那个路径是不是由本脚本推导（不许写死盘符路径）")
    for fn, _n in judged:
        bad = [x for x in bad_prov if x.startswith(fn + " ←")]
        chk("%s 的 sys.path 指向本脚本目录" % fn, not bad, "；".join(bad))

    print("\n3) 覆盖")
    chk("扫到 %d 个脚本" % scanned, scanned > 0)
    chk("其中 %d 个真 import 了兄弟模块（>0 才算真判过）" % len(judged),
        len(judged) > 0, "一个都没扫到 = 判据自己瞎了")
    chk("全部可解析", not bad_parse, "；".join(bad_parse))

    if guarded:
        print("\n降级导入 %d 个（写在接了 ImportError 的 try 里，自带退路，不计分）："
              % len(guarded))
        for line in guarded:
            print("  · %s" % line)
    if skipped:
        print("\n跳过 %d 个（带「%s」标记，未验证，不是通过）："
              % (len(skipped), MARKER))
        for fn, reason in skipped:
            print("  ↷ %s：%s" % (fn, reason))

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
