# -*- coding: utf-8 -*-
"""check_gate_registry 的变异刀：四刀注入 + 对照组，证明闸门「能抓错」而不只是恒真。

判据形状（每刀都断言**具体抓到了注入的那个错**，不是任意非零退出——否则分不清
「闸门在工作」和「闸门自己崩了」，两者都 exit 1/2）：
  M1 正向·新文件   ：放一个未登记、无免登标记的 test_*.py → 必须红且点名该文件
  M2 正向·撕标记   ：把某个免登文件的标记行删掉 → 必须红且点名该文件
  M3 反向·登记项失效：把某条登记项指向不存在的脚本 → 必须红且报「不存在」
  M4 去重          ：复制一条登记项 → 必须红且报「重复登记」
  对照             ：原样跑 → 必须绿，且覆盖数里「待定 0」（从输出解析，不写死数字）

实现纪律（都是踩过的坑）：
  - 改源码**二进制模式**读写：文本模式在 Windows 会把每个 \n 写成 \r\n → 全文件
    变 CRLF → 后续 \n 锚点静默失效。锚点按**实测 EOL** 拼。
  - 目标**动态取**：免登文件从「现在真有标记的文件」里现找，登记项从 GATES 里
    现找——不写死文件名（写死的目标被删/改名后刀会静默跳过，而闸门还在）。
  - 找不到目标、还原后字节对不上，一律 FAIL，不许当没发生过。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(HERE, "check_gate_registry.py")
CHECK_ALL = os.path.join(HERE, "check_all.py")
TEMP_UNWIRED = os.path.join(HERE, "test_zzz_unwired_probe.py")
MARKER_RE = re.compile(rb"^\s*" + "不进闸门".encode("utf-8") + rb"[" + "：:".encode("utf-8") + rb"]", re.M)  # 「不进闸门：」


def _eol(b: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in b else b"\n"


def _run_gate() -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _find_exempt_file() -> str:
    """现找：tools/ 下 docstring 里带免登标记的**第一个**文件（按文件名排序）。

    【2026-11-04 修】只认 `check_*` / `test_*`：登记对账闸门的扫描范围就是这两类。
    曾有一个 `_gate_lock.py`（基础设施，不在扫描范围）在文档里写了「不进闸门：…」
    说明句，排序又排在 `check_*` 前面 —— M2 撕了它的标记，闸门当然不红，刀就哑了。
    目标是动态取的，但**动态取也必须先按被测程序的扫描口径过滤**，否则随手一句
    说明就能把刀弄哑。
    """
    for fn in sorted(os.listdir(HERE)):
        if not fn.startswith(("check_", "test_")):
            continue
        if not fn.endswith(".py"):
            continue
        full = os.path.join(HERE, fn)
        if not os.path.isfile(full):
            continue
        with io.open(full, "rb") as f:
            head = f.read(4096)
        if MARKER_RE.search(head):
            return full
    raise RuntimeError("找不到带免登标记的文件——闸门的正向判据已无可保护目标？")


def _find_gates_line() -> tuple[bytes, int, int]:
    """现找：check_all.py 里第一条登记了 tools/ 脚本的 GATES 行。返回 (整行 bytes, 起始, 结束)。"""
    with io.open(CHECK_ALL, "rb") as f:
        src = f.read()
    eol = _eol(src)
    lines = src.split(eol)
    pat = re.compile(rb'\["tools/[^"]+\.py"')
    for i, ln in enumerate(lines):
        if pat.search(ln):
            start = sum(len(x) + len(eol) for x in lines[:i])
            return ln, start, start + len(ln)
    raise RuntimeError("check_all.py 里找不到登记 tools/ 脚本的 GATES 行")


def _knife_new_unwired() -> tuple[bool, str]:
    """M1：放一个未登记、无标记的 test_*.py。"""
    if os.path.exists(TEMP_UNWIRED):
        return False, "临时文件已存在，拒绝覆盖: %s" % TEMP_UNWIRED
    with io.open(TEMP_UNWIRED, "wb") as f:
        f.write(('"""变异刀临时文件：未登记、无免登标记。"""' + chr(10)).encode("utf-8"))
    try:
        code, out = _run_gate()
        name = os.path.basename(TEMP_UNWIRED)
        if code != 1:
            return False, "M1 期望 exit 1，实际 %d" % code
        if name not in out:
            return False, "M1 红了但没点名 %s——可能红在别处" % name
        return True, "M1 抓到未登记新文件"
    finally:
        if os.path.exists(TEMP_UNWIRED):
            os.remove(TEMP_UNWIRED)


def _knife_strip_marker() -> tuple[bool, str]:
    """M2：撕掉某个免登文件的标记行。"""
    target = _find_exempt_file()
    with io.open(target, "rb") as f:
        original = f.read()
    eol = _eol(original)
    lines = original.split(eol)
    idx = [i for i, ln in enumerate(lines) if MARKER_RE.search(ln)]
    if len(idx) != 1:
        return False, "M2 目标标记行命中 %d 次（期望 1）: %s" % (len(idx), target)
    del lines[idx[0]]
    mutated = eol.join(lines)
    try:
        with io.open(target, "wb") as f:
            f.write(mutated)
        code, out = _run_gate()
        rel = "tools/" + os.path.basename(target)
        if code != 1:
            return False, "M2 期望 exit 1，实际 %d" % code
        if rel not in out:
            return False, "M2 红了但没点名 %s" % rel
        return True, "M2 抓到免登标记被撕（%s）" % rel
    finally:
        with io.open(target, "rb") as f:
            now = f.read()
        if now == original:
            pass
        else:
            with io.open(target, "wb") as f:
                f.write(original)
            with io.open(target, "rb") as f:
                if f.read() != original:
                    raise RuntimeError("M2 还原失败: %s" % target)


def _mutate_check_all(mutator) -> tuple[bool, str]:
    """M3/M4 共用：二进制改 check_all.py → 跑闸门 → 无条件还原并核对字节。"""
    with io.open(CHECK_ALL, "rb") as f:
        original = f.read()
    mutated = mutator(original)
    if mutated is None or mutated == original:
        return False, "变异没改变文件内容——锚点没打中"
    try:
        with io.open(CHECK_ALL, "wb") as f:
            f.write(mutated)
        code, out = _run_gate()
        return code, out
    finally:
        with io.open(CHECK_ALL, "wb") as f:
            f.write(original)
        with io.open(CHECK_ALL, "rb") as f:
            if f.read() != original:
                raise RuntimeError("还原后字节对不上: %s" % CHECK_ALL)


def _knife_missing_path() -> tuple[bool, str]:
    """M3：把一条登记项指向不存在的脚本。"""
    def mut(original: bytes) -> bytes:
        eol = _eol(original)
        lines = original.split(eol)
        pat = re.compile(rb'\["tools/[^"]+\.py"')
        for i, ln in enumerate(lines):
            if pat.search(ln):
                new = re.sub(rb'"tools/[^"]+\.py"', b'"tools/_mutant_missing_gate.py"', ln, count=1)
                if new == ln:
                    return None
                lines[i] = new
                return eol.join(lines)
        return None
    code, out = _mutate_check_all(mut)
    if code is True or code is False:
        return code, out
    if code != 1:
        return False, "M3 期望 exit 1，实际 %s" % code
    if "不存在" not in out:
        return False, "M3 红了但没报「不存在」——可能红在别处"
    return True, "M3 抓到登记项指向不存在的脚本"


def _knife_duplicate() -> tuple[bool, str]:
    """M4：复制一条登记项。"""
    def mut(original: bytes) -> bytes:
        eol = _eol(original)
        lines = original.split(eol)
        pat = re.compile(rb'^\s*\(.*\["tools/[^"]+\.py"')
        for i, ln in enumerate(lines):
            if pat.search(ln):
                lines.insert(i + 1, ln)
                return eol.join(lines)
        return None
    code, out = _mutate_check_all(mut)
    if code is True or code is False:
        return code, out
    if code != 1:
        return False, "M4 期望 exit 1，实际 %s" % code
    if "重复登记" not in out:
        return False, "M4 红了但没报「重复登记」——可能红在别处"
    return True, "M4 抓到重复登记"


def _control() -> tuple[bool, str]:
    """对照组：原样必须绿，且覆盖数里「待定 0」（从输出解析，不写死）。"""
    code, out = _run_gate()
    if code != 0:
        return False, "对照组期望 exit 0，实际 %d:\n%s" % (code, out)
    m = re.search(r"待定\s*(\d+)\s*个", out)
    if not m:
        return False, "对照组输出里解析不到待定数:\n%s" % out
    if m.group(1) != "0":
        return False, "对照组待定 %s ≠ 0" % m.group(1)
    return True, "对照组绿，待定 0"


def main() -> int:
    results = []
    for name, fn in (("M1", _knife_new_unwired), ("M2", _knife_strip_marker),
                     ("M3", _knife_missing_path), ("M4", _knife_duplicate),
                     ("对照", _control)):
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, "刀自身异常: %r" % exc
        results.append((name, ok, msg))
        print("%s %s —— %s" % ("✓" if ok else "✗", name, msg))
    # 收尾再跑一次对照组：所有变异都已还原，必须还是绿的
    try:
        ok, msg = _control()
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, "还原后对照异常: %r" % exc
    results.append(("还原后对照", ok, msg))
    print("%s 还原后对照 —— %s" % ("✓" if ok else "✗", msg))
    bad = [r for r in results if not r[1]]
    if bad:
        print("✗ %d 项不符合" % len(bad))
        return 1
    print("✓ 四刀全红 + 对照组绿 + 还原后仍绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
