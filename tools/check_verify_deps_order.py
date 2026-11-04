# -*- coding: utf-8 -*-
"""出包「验收时序」静态闸门：包内 doctor 必须在隔离就绪之后才跑。

背景（2026-11-04 实证，见 docs/deliverables/2026-11-04_当前HEAD出包与验包记录.md §8）：
`install_deps()` 在 :402 就调 `verify_deps()`，而 `_pth` 写入与 pywin32 DLL 铺设
发生在其后。结果验收报 `No module named 'pywintypes'` 的假红，一度被误判为
「出厂模板角色档 engine 配错」，差点白改 config.release.yaml。
修复是 `defer_verify=True` + 在隔离段之后统一验一次（`e44a646`）。
但修复本身没有守卫——只要有人把开关改回去，同一个假红会静默回来。

判据（用 AST 不用正则：注释墓碑里同样写着 defer_verify=True，正则会假通过）：
  1. main() 里 install_deps 的每个调用都必须带 defer_verify=True。
  2. main() 里必须存在 write_isolated_pth 与 bundle_pywin32_dlls 调用。
  3. main() 里每个 verify_deps 调用的行号，都必须严格大于上述三步的行号。
  4. main() 里至少有一次 verify_deps（防「把验收整段删掉照样全绿」）。
  5. install_deps 定义仍保留 defer_verify 关键字参数（防改名后关键字失效）。
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tools", "make_dist.py")

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


def calls_in(fn: ast.AST) -> list[tuple[str, int, dict]]:
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        kws = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        out.append((name, node.lineno, kws))
    return out


def is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def main() -> int:
    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        print("  FAIL make_dist.py 可解析 <- %s" % exc)
        print("\n结果: 0 PASS / 1 FAIL")
        return 1

    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "main" not in fns or "install_deps" not in fns:
        print("  FAIL main/install_deps 函数定义存在")
        print("\n结果: 0 PASS / 1 FAIL")
        return 1
    calls = calls_in(fns["main"])

    print("1) 依赖安装不得提前验收")
    installs = [c for c in calls if c[0] == "install_deps"]
    chk("main() 里有 install_deps 调用", len(installs) >= 1, str(len(installs)))
    chk("每个 install_deps 都带 defer_verify=True",
        bool(installs) and all(is_true(c[2].get("defer_verify")) for c in installs),
        str([(c[1], list(c[2])) for c in installs]))
    kwonly = [a.arg for a in fns["install_deps"].args.kwonlyargs]
    ordinary = [a.arg for a in fns["install_deps"].args.args]
    chk("install_deps 定义保留 defer_verify 参数",
        "defer_verify" in kwonly + ordinary, str(kwonly + ordinary))

    print("\n2) 隔离两步必须存在")
    iso = [c for c in calls if c[0] == "write_isolated_pth"]
    dll = [c for c in calls if c[0] == "bundle_pywin32_dlls"]
    chk("main() 里有 write_isolated_pth 调用", len(iso) >= 1, str(len(iso)))
    chk("main() 里有 bundle_pywin32_dlls 调用", len(dll) >= 1, str(len(dll)))

    print("\n3) 验收必须在隔离就绪之后")
    verifies = [c for c in calls if c[0] == "verify_deps"]
    chk("main() 里至少一次 verify_deps", len(verifies) >= 1, str(len(verifies)))
    if installs and iso and dll and verifies:
        barrier = max(max(c[1] for c in installs),
                      max(c[1] for c in iso),
                      max(c[1] for c in dll))
        lines = [c[1] for c in verifies]
        chk("所有 verify_deps 行号 > %d（安装/隔离/DLL）" % barrier,
            all(line > barrier for line in lines), str(lines))

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
