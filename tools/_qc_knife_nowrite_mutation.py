# -*- coding: utf-8 -*-
"""`tools/check_knife_nowrite.py` 的变异刀：证明它真的能认出「封写下还全绿」的刀。

为什么需要这组刀
----------------
封写体检的结论会是一串「N 把刀 CERTIFIED」——**0 命中是最危险的读数**。
如果工具本身坏了（永远说 CERTIFIED，或者把崩溃当成"认出来了"），
那所有 CERTIFIED 一个都不值。

所以这里的样本全是**人造的桩刀**，每把一个独立小仓，**不碰仓库一个字节**。
桩刀只做一件事：写一个文件、再看它有没有真的落地。

十一刀 + 对照
-------------
  H 老实刀：写出去再读回来核对 → CERTIFIED（不许误判成空转）
  V 空转刀：写出去但不核对     → VACUOUS，且工具 exit 1 并点名
  T 文本模式 + fsync 刀        → CERTIFIED
      （守一个实测踩到的坑：第一版写目标是 io.BytesIO，刀往文本模式写直接
        `TypeError: a bytes-like object is required`，交给 os.fsync 又
        `io.UnsupportedOperation: fileno`——7 把刀崩在这上面，差点被记成"认出来了"）
  C 封写就崩的刀（写出模块再 import 它）→ ERROR，**不是** CERTIFIED
  R 起点就红                   → ERROR
  M 有模块级 MUTS              → COVERED_ELSEWHERE（归另一个工具，不重复算）
  S 没有 main() 也没有 exit()  → SKIP
  L 限位：额度指向真的空转的刀  → exit 0（不许把额度表判成恒错）
  K 额度指向不存在的刀          → exit 2 并点名
  X 额度指向已经不空转的刀      → exit 2 并点名
  I1 仪器说谎①：certify 永远返回 CERTIFIED → V 被误判，刀必须红
  I2 仪器说谎②：把崩溃当成认出来了        → C 被误判，刀必须红
  对照：真实仓（--only requirements_syntax）→ exit 0

用法：python tools/_qc_knife_nowrite_mutation.py
退出码 0 = 十一刀 + 对照全部符合预期。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(HERE, "check_knife_nowrite.py")
PY = sys.executable

# ---------------------------------------------------------------- 桩刀

# H：写出去，再读回来核对——这是"有牙"的样子
STUB_HONEST = '''\
import io, os, sys

TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probe.txt")


def main() -> int:
    with io.open(TARGET, "w", encoding="utf-8", newline="\\n") as f:
        f.write("mutated\\n")
    if not os.path.isfile(TARGET):
        print("FAIL 变异没落地（文件都没生成）")
        return 1
    with io.open(TARGET, encoding="utf-8") as f:
        got = f.read()
    if got != "mutated\\n":
        print("FAIL 变异没落地（读到 %r）" % got)
        return 1
    print("PASS 变异落地")
    return 0
'''

# V：只写不核对——典型的空转
STUB_VACUOUS = '''\
import io, os

TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probe.txt")


def main() -> int:
    with io.open(TARGET, "w", encoding="utf-8", newline="\\n") as f:
        f.write("mutated\\n")
    print("PASS 一刀全红")
    return 0
'''

# T：文本模式写 + fsync + 读回核对
STUB_TEXT = '''\
import io, os, sys

TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probe.txt")


def main() -> int:
    with io.open(TARGET, "w", encoding="utf-8", newline="\\n") as f:
        f.write("文本模式\\n")
        f.flush()
        os.fsync(f.fileno())          # 内存 BytesIO 会在这里 fileno() 失败
    if not os.path.isfile(TARGET):
        print("FAIL 变异没落地")
        return 1
    with io.open(TARGET, encoding="utf-8") as f:
        if f.read() != "文本模式\\n":
            print("FAIL 内容不对")
            return 1
    print("PASS 文本模式变异落地")
    return 0
'''

# C：机制本身要求"写出一份模块再 import 它"——封写必然把它弄崩
STUB_CRASH = '''\
import io, os, sys


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    with io.open(os.path.join(here, "gen_mod.py"), "w", encoding="utf-8", newline="\\n") as f:
        f.write("X = 1\\n")
    sys.path.insert(0, here)
    import gen_mod  # noqa: F401  封写下文件不存在 → ModuleNotFoundError
    print("PASS 生成并导入了模块")
    return 0
'''

# R：起点就红
STUB_RED = '''\
def main() -> int:
    print("FAIL 我本来就红")
    return 1
'''

# M：有模块级 MUTS——归 check_knife_noop.py
STUB_MUTS = '''\
MUTS = [("A 改一处", b"aaa", b"bbb")]


def main() -> int:
    print("PASS")
    return 0
'''

# S：既没有 main() 也没有 exit()
STUB_NO_MAIN = '''\
print("我只是个辅助模块")
'''

# E：刀自己举手说"这台机器缺东西，我没验"（例如 jsdom 没装）
#    77 必须是 SKIP，不许记 ERROR（把环境问题算成刀坏了），更不许记 CERTIFIED。
STUB_SKIP77 = '''\
def main() -> int:
    print("装法：npm i jsdom 到仓库 node_modules")
    return 77
'''

STUBS = {
    "H 老实刀（写出去再核对）": STUB_HONEST,
    "V 空转刀（只写不核对）": STUB_VACUOUS,
    "T 文本模式 + fsync 刀": STUB_TEXT,
    "C 封写就崩的刀": STUB_CRASH,
    "R 起点就红": STUB_RED,
    "M 有模块级 MUTS": STUB_MUTS,
    "S 没有 main()": STUB_NO_MAIN,
    "E 缺环境自报 77": STUB_SKIP77,
}

KNIFE_NAME = "_qc_stub_mutation.py"


def _stub_repo(src: str, name: str = KNIFE_NAME) -> str:
    d = tempfile.mkdtemp(prefix="qc_nw_")
    os.makedirs(os.path.join(d, "tools"))
    io.open(os.path.join(d, "tools", name), "w",
            encoding="utf-8", newline="\n").write(src)
    return d


def _run_tool(root: str, *extra: str) -> tuple[int, str]:
    p = subprocess.run([PY, TOOL, "--root", root, *extra], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _kind_of(out: str) -> str:
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("→ "):
            return s[2:].strip()
    return "<无分类>"


def _one(label: str, want_kind: str, want_rc: int | None = None,
         name: str = KNIFE_NAME) -> tuple[bool, str]:
    d = _stub_repo(STUBS[label], name)
    try:
        rc, out = _run_tool(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    kind = _kind_of(out)
    ok = kind == want_kind and (want_rc is None or rc == want_rc)
    return ok, "%s → %s rc=%d（期望 %s%s）" % (
        label, kind, rc, want_kind, "" if want_rc is None else " rc=%d" % want_rc)


# ---------------------------------------------------------------- 额度刀

ANCHOR = "KNOWN_VACUOUS: dict[str, str] = {}"


def _patched_tool(entries: dict[str, str]) -> bytes:
    src = io.open(TOOL, encoding="utf-8").read()
    n = src.count(ANCHOR)
    if n != 1:
        raise RuntimeError("锚点 %r 命中 %d 次（期望 1）" % (ANCHOR, n))
    body = "".join('    "%s": "空转额度对账用",\n' % k for k in entries)
    return src.replace(ANCHOR, "KNOWN_VACUOUS: dict[str, str] = {\n" + body + "}", 1).encode("utf-8")


def _run_with_table(entries: dict[str, str], stub_src: str) -> tuple[int, str]:
    orig = io.open(TOOL, "rb").read()
    patched = _patched_tool(entries)     # ← 先算，再开文件（截断在前、算在后会毁掉工具）
    d = _stub_repo(stub_src)
    io.open(TOOL, "wb").write(patched)
    try:
        return _run_tool(d)
    finally:
        io.open(TOOL, "wb").write(orig)
        shutil.rmtree(d, ignore_errors=True)
        if io.open(TOOL, "rb").read() != orig:
            raise RuntimeError("还原后字节对不上")


# ---------------------------------------------------------------- 仪器说谎

def _patch_once(old: str, new: str) -> bytes:
    src = io.open(TOOL, encoding="utf-8").read()
    n = src.count(old)
    if n != 1:
        raise RuntimeError("锚点命中 %d 次（期望 1）：%r" % (n, old[:60]))
    return src.replace(old, new, 1).encode("utf-8")


def _run_patched(patch: bytes, stub_src: str) -> tuple[int, str]:
    orig = io.open(TOOL, "rb").read()
    d = _stub_repo(stub_src)
    io.open(TOOL, "wb").write(patch)
    try:
        return _run_tool(d)
    finally:
        io.open(TOOL, "wb").write(orig)
        shutil.rmtree(d, ignore_errors=True)
        if io.open(TOOL, "rb").read() != orig:
            raise RuntimeError("还原后字节对不上")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def rec(name: str, ok: bool, msg: str) -> None:
        results.append((name, ok, msg))
        print("%s %s —— %s" % ("✓" if ok else "✗", name, msg))

    # 一、桩刀分类（工具本体没改）
    print("=== 一、桩刀分类 ===")
    # 期望的 exit 码说明：CERTIFIED / VACUOUS 之外一律 exit 2 —— 因为"一把都没
    # 跑成"就是无法判定，不能返回 0 让人读成"体检通过"。这里 4 个桩刀（C/R/M/S）
    # 都跑不出 CERTIFIED，所以都是 2。早先写 0 是把"没有失败"当成了"跑过了"。
    for label, kind, rc in (
        ("H 老实刀（写出去再核对）", "CERTIFIED", 0),
        ("V 空转刀（只写不核对）", "VACUOUS", 1),
        ("T 文本模式 + fsync 刀", "CERTIFIED", 0),
        ("C 封写就崩的刀", "ERROR", 2),
        ("R 起点就红", "ERROR", 2),
        ("M 有模块级 MUTS", "COVERED_ELSEWHERE", 2),
        ("S 没有 main()", "SKIP", 2),
        ("E 缺环境自报 77", "SKIP", 2),
        ("H 老实刀（写出去再核对）", "META", 2),
    ):
        try:
            # 最后一条用 `_qc_knife_` 前缀命名，验的是"仪器自身的刀单列 META"。
            nm = "_qc_knife_stub_mutation.py" if kind == "META" else KNIFE_NAME
            ok, msg = _one(label, kind, rc, nm)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, "刀自身异常: %r" % exc
        rec(label, ok, msg)

    # 二、额度表对账
    print("\n=== 二、额度表（KNOWN_VACUOUS）对账 ===")
    try:
        rc, out = _run_with_table({"_qc_nope_mutation.py": "x"}, STUB_HONEST)
        ok = rc == 2 and "_qc_nope_mutation.py" in out
        rec("K 额度指向不存在的刀", ok, "rc=%d（期望 2 并点名）" % rc)
    except Exception as exc:  # noqa: BLE001
        rec("K 额度指向不存在的刀", False, "刀自身异常: %r" % exc)

    try:
        rc, out = _run_with_table({KNIFE_NAME: "x"}, STUB_HONEST)
        ok = rc == 2 and "已经不坏" in out
        rec("X 额度指向已经不空转的刀", ok, "rc=%d（期望 2 并说「已经不坏了」）" % rc)
    except Exception as exc:  # noqa: BLE001
        rec("X 额度指向已经不空转的刀", False, "刀自身异常: %r" % exc)

    try:
        rc, out = _run_with_table({KNIFE_NAME: "x"}, STUB_VACUOUS)
        ok = rc == 0 and "已知空转" in out
        rec("L 限位：额度指向真的空转的刀", ok, "rc=%d（期望 0，额度表不是恒错）" % rc)
    except Exception as exc:  # noqa: BLE001
        rec("L 限位：额度指向真的空转的刀", False, "刀自身异常: %r" % exc)

    # 三、仪器说谎：工具坏掉时必须被这些刀抓住
    print("\n=== 三、仪器说谎（工具被改坏时这些刀必须红）===")
    try:
        patch = _patch_once("        # ① 起点必须绿",
                            "        return \"CERTIFIED\", [\"假装验过了\"]\n"
                            "        # ① 起点必须绿")
        rc, out = _run_patched(patch, STUB_VACUOUS)
        ok = _kind_of(out) == "CERTIFIED" and rc == 0
        rec("I1 仪器说谎：certify 永远返回 CERTIFIED",
            ok, "空转刀被判 %s rc=%d（工具坏掉时该刀必须能发现）" % (_kind_of(out), rc))
    except Exception as exc:  # noqa: BLE001
        rec("I1 仪器说谎：certify 永远返回 CERTIFIED", False, "刀自身异常: %r" % exc)

    try:
        patch = _patch_once("        if _crashed(out2):", "        if False:")
        rc, out = _run_patched(patch, STUB_CRASH)
        ok = _kind_of(out) == "CERTIFIED"
        rec("I2 仪器说谎：把崩溃当成认出来了",
            ok, "崩溃刀被判 %s（工具坏掉时该刀必须能发现）" % _kind_of(out))
    except Exception as exc:  # noqa: BLE001
        rec("I2 仪器说谎：把崩溃当成认出来了", False, "刀自身异常: %r" % exc)

    try:
        patch = _patch_once("        if code == 77:", "        if False:")
        rc, out = _run_patched(patch, STUB_SKIP77)
        ok = _kind_of(out) == "ERROR"
        rec("I3 仪器说谎：把「缺环境」记成「判据失败」",
            ok, "自报 77 的刀被判 %s（工具坏掉时该刀必须能发现）" % _kind_of(out))
    except Exception as exc:  # noqa: BLE001
        rec("I3 仪器说谎：把「缺环境」记成「判据失败」", False, "刀自身异常: %r" % exc)

    # 四、对照：真实仓
    print("\n=== 四、对照：真实仓 ===")
    try:
        rc, out = _run_tool(ROOT, "--only", "requirements_syntax")
        ok = rc == 0 and "CERTIFIED 1" in out
        rec("对照 真实仓 requirements_syntax", ok,
            "rc=%d（期望 0 且 CERTIFIED 1）" % rc)
    except Exception as exc:  # noqa: BLE001
        rec("对照 真实仓 requirements_syntax", False, "刀自身异常: %r" % exc)

    bad = [r for r in results if not r[1]]
    print("\n" + "=" * 56)
    if bad:
        print("✗ %d 项不符合" % len(bad))
        return 1
    print("✓ %d 项全部符合预期（%d 刀 + 对照）" % (len(results), len(results) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
