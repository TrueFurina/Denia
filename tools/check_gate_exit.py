# -*- coding: utf-8 -*-
"""闸门自检：**登记在 GATES 里的脚本，必须有一条能红的退出路径**。

为什么需要这道闸门
------------------
`check_all.py` 的 GATES 是「本仓有哪些判据在守」的唯一清单，它靠**退出码**判定
每一项是通过还是失败。于是一个只 `print("[FAIL] …")` 却始终 `exit 0` 的脚本，
在清单里占着一行、每次全量闸门都跑一遍、永远显示 ✓ —— 它不守任何东西，
但它的存在让人以为「这件事有判据」。

这正是本仓反复踩的「装饰闸门」：
  · `check_web_js_syntax` 曾经打印完就 exit 0（第十五轮记录）
  · `check_requirements` 遇 SyntaxError 静默跳过、退出码仍 0
  · 唤醒词、桥接、广播各自都出过「看着绿、其实整文件没被扫」
区别在于：前几次是**判据写错**，这次是**根本没写判据**。

本仓实锤（2026-11-08，3 处）：`test_judge_confirm_clause.py` 逐条打印
`[PASS]/[FAIL]` 然后 `exit 0`；`test_prompt_companion_clause.py` 算出了 `allok`
并打印 `RESULT: ALL PASS / HAS FAILURE`，然后 `exit 0`；`test_fabrication_guard.py`
算出了 `ok` 并打印 `PROOF ALL_PASS / HAS_FAIL`，然后 `exit 0`。
三个都登记在 GATES 里，三个都不可能红。

判定规则
--------
只查 GATES 里 `argv[0]` 以 `.py` 结尾的项（`-m compileall` 这类外部命令不归本闸门管）。
脚本里出现下面任一形态即算「能红」：

  · `sys.exit(x)` / `os._exit(x)`，且 x 不是常量 0 / None（`sys.exit(main())` 也算）
  · `unittest.main(...)` / `pytest.main(...)` —— 它们自己会按结果非零退出
  · 任何 `raise`（含 `raise SystemExit(1)`）
  · 任何 `assert`

豁免
----
确属「只出报告、不判通过/失败」的脚本，在文件里写 `# gate-exit-ok: <原因>`。
**不要用它来给「忘了写退出码」开脱**——那种情况应该去补退出码。

量过的两条边界（都是**故意**留的，不是没想到）
----------------------------------------------
① `raise` / `assert` 只认「写了」，不认「跑得到」：
   `try: raise X` + `except Exception: pass` 这种把失败吞掉的写法仍判绿。
   要判它得做控制流分析（还要判 handler 是否全 pass），误报风险大于收益；
   何况作者写下 raise 本身就说明意图是「失败」。变异刀 K14 钉住这个口径，
   将来谁收紧这里，K14 会红，逼一次有意识的决定。
② 不认 `sys.exit()` 之外的退出方式：`exit()` 内置、`os.abort()`、C 层崩溃都不算。
   本仓没有这些写法；真出现了，`# gate-exit-ok:` 也救不了它——那正是要红的时候。

用法
----
    python tools/check_gate_exit.py                       # 默认读 tools/check_all.py
    python tools/check_gate_exit.py --registry x.py --root .

退出码：0 干净 / 1 有命中 / 2 无法判定（登记表解析失败、脚本不存在、脚本解析失败、
扫到 0 个 .py 脚本）
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

MARKER = "gate-exit-ok"
_EXIT_FUNCS = ("exit", "_exit")
_RUNNER_MODULES = ("unittest", "pytest")


def _load_gates(path: str) -> tuple[list[tuple[str, str]], str]:
    """从登记表里取 (闸门名, 脚本路径)。用 AST 解析，不 import。"""
    try:
        src = io.open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        return [], "读不了登记表：%s" % e
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [], "登记表解析失败：第 %s 行 %s" % (e.lineno, e.msg)

    for node in tree.body:
        target = getattr(node, "target", None)
        if not isinstance(node, ast.AnnAssign) or getattr(target, "id", "") != "GATES":
            continue
        out = []
        for el in getattr(node.value, "elts", []):
            if not isinstance(el, ast.Tuple) or len(el.elts) < 3:
                continue
            name = getattr(el.elts[0], "value", "")
            argv = el.elts[2]
            if not isinstance(argv, ast.List):
                continue
            vals = [a.value for a in argv.elts if isinstance(a, ast.Constant)]
            if vals:
                out.append((str(name), str(vals[0])))
        return out, ""
    return [], "登记表里找不到 `GATES: list[...] = [...]` 这一段"


def _can_fail(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            f = n.func
            # sys.exit(x) / os._exit(x)
            if f.attr in _EXIT_FUNCS and isinstance(f.value, ast.Name) and f.value.id in ("sys", "os"):
                arg = n.args[0] if n.args else None
                if arg is None:
                    continue
                if isinstance(arg, ast.Constant) and arg.value in (0, None):
                    continue          # exit(0) 不是失败路径
                return True
            # unittest.main() / pytest.main()
            if f.attr == "main" and isinstance(f.value, ast.Name) and f.value.id in _RUNNER_MODULES:
                return True
        if isinstance(n, (ast.Raise, ast.Assert)):
            return True
    return False


def _exempt(src: str) -> bool:
    return any(MARKER in ln for ln in src.split("\n"))


def main() -> int:
    ap = argparse.ArgumentParser(description="GATES 里的脚本必须有能红的退出路径")
    ap.add_argument("--registry", default=os.path.join("tools", "check_all.py"))
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    registry = args.registry if os.path.isabs(args.registry) else os.path.join(args.root, args.registry)
    gates, err = _load_gates(registry)
    if err:
        print("✗ %s，无法判定" % err, file=sys.stderr)
        return 2
    if not gates:
        print("✗ 登记表里一项都没解析出来，无法判定（0 项不算通过）", file=sys.stderr)
        return 2

    checked = skipped = 0
    hits: list[str] = []
    errors: list[str] = []
    for name, script in gates:
        if not script.endswith(".py"):
            skipped += 1               # -m compileall 这类外部命令
            continue
        path = script if os.path.isabs(script) else os.path.join(args.root, script)
        if not os.path.isfile(path):
            errors.append("%s：登记的脚本不存在（%s）" % (name, script))
            continue
        try:
            src = io.open(path, "r", encoding="utf-8", errors="replace").read()
            tree = ast.parse(src, filename=path)
        except (OSError, SyntaxError) as e:
            errors.append("%s：%s 解析失败（%s）" % (name, script, e))
            continue
        checked += 1
        if _can_fail(tree) or _exempt(src):
            continue
        hits.append("%-16s %s —— 登记在 GATES 里，却没有任何非零退出路径（永远绿）"
                    % (name, script.replace("\\", "/")))

    for h in hits:
        print(h)
    print("\n检查 %d 个登记脚本 / 跳过 %d 项非 .py / 命中 %d 处"
          % (checked, skipped, len(hits)))

    if errors:
        print("\n✗ 有项没能判定（不能当「没问题」）：", file=sys.stderr)
        for e in errors:
            print("  %s" % e, file=sys.stderr)
        return 2
    # 【2026-11-08 补】扫到 0 个 .py 也是「无法判定」：登记表只剩 `-m compileall`
    # 这类外部命令时，上面会算出 checked=0、hits=[] 然后 exit 0 —— 又一次
    # 「零覆盖当通过」。本仓这条纪律写在 check_all.py 的 --strict 注释里，
    # 新闸门必须自带。
    if checked == 0:
        print("\n✗ 一个 .py 登记脚本都没扫到（跳过 %d 项非 .py），无法判定" % skipped,
              file=sys.stderr)
        return 2
    if hits:
        print("\n✗ 登记了就要能红：补 `sys.exit(0 if ok else 1)`，或写 `# %s: <原因>`。"
              % MARKER, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
