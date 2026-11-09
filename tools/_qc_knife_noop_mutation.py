# -*- coding: utf-8 -*-
"""`tools/check_knife_noop.py` 的变异刀：证明它真的能认出「空变异下还全绿」的刀。

为什么需要这组刀
----------------
空变异体检的结论会是一串「N 把刀 CERTIFIED」——**0 命中是最危险的读数**。
如果工具本身坏了（永远说 CERTIFIED），那所有 CERTIFIED 一个都不值。

所以这里的刀全是**人造的刀**：造一个"不管变异改什么都 exit 0"的，看工具会不会
把它判成 VACUOUS；再造一个"老实检查"的，看工具会不会误判它。

样本全建在 tempfile，每个刀一套独立小仓，**不碰仓库一个字节**。

七刀 + 对照 + 三把额度刀
-------------------------
  N1 刀恒 exit 0（不看变异）      → VACUOUS
  N2 刀检查变异是否真的改了东西    → CERTIFIED（不许误判）
  N3 刀起点就红                   → ERROR（无法判定）
  N4 刀没有 MUTS                  → SKIP（不算通过）
  N5 嵌套形状 ("名字", [(old,new)]) → CERTIFIED（形状二也要认）
  N6 全部 SKIP                    → exit 2（一把都没跑成 ≠ 通过）
  N8 额度指向不存在的刀            → exit 2 并点名（额度对着空气）
  N9 额度指向已经不空转的刀        → exit 2 并点名（表在说谎）
  N10 限位：额度指向真的空转的刀   → exit 0（不许把额度表判成恒错）
  N7 对照：真实仓                 → exit 码与"有没有**新**空转"一致

用法：python tools/_qc_knife_noop_mutation.py
退出码 0 = 七刀 + 对照全部符合预期。
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
TOOL = os.path.join(HERE, "check_knife_noop.py")

# ---------------------------------------------------------------- 桩刀

# N1：根本不看变异改了什么，永远说"0 处异常"
KNIFE_VACUOUS = '''\
MUTS = [("A 假装改了一处", b"aaa", b"bbb")]


def main() -> int:
    print("变异结果：0 处异常")
    return 0
'''

# N2：老老实实检查"这条变异到底有没有改东西"
KNIFE_SOUND = '''\
MUTS = [("A 改一处", b"aaa", b"bbb")]


def main() -> int:
    bad = 0
    for name, old, new in MUTS:
        if old == new:
            print("%s：没抓住（变异没生效）" % name)
            bad += 1
    print("一刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0
'''

# N3：起点就红
KNIFE_RED = '''\
MUTS = [("A", b"aaa", b"bbb")]


def main() -> int:
    print("FAIL 我本来就红")
    return 1
'''

# N4：没有 MUTS
KNIFE_NO_MUTS = '''\
def main() -> int:
    print("我不用 MUTS")
    return 0
'''

# N5：嵌套形状 ("名字", [(old, new), ...])
KNIFE_NESTED = '''\
MUTS = [("A 两处", [(b"aaa", b"bbb"), (b"ccc", b"ddd")])]


def main() -> int:
    bad = 0
    for name, subs in MUTS:
        if all(old == new for old, new in subs):
            print("%s：没抓住" % name)
            bad += 1
    print("一刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0
'''


def make_repo(tools: dict) -> str:
    d = tempfile.mkdtemp(prefix="qc_noop_mut_")
    t = os.path.join(d, "tools")
    os.makedirs(t)
    for name, src in tools.items():
        io.open(os.path.join(t, name), "w", encoding="utf-8", newline="\n").write(src)
    return d


def run_tool(root: str, *extra: str) -> tuple[int, str]:
    argv = [sys.executable, TOOL, "--root", root] + list(extra)
    r = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kind_of(out: str) -> str:
    found = ""
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("→ "):
            found = s[2:].strip()
    return found


# ---------------------------------------------------------------- 额度对账（N8–N10）
#
# `KNOWN_VACUOUS` 是"允许空转"的额度。这三刀打的是额度本身：
# 名字指向不存在的刀（额度对着空气）、名字指向已经修好的刀（额度在说谎）、
# 名字指向真的还空转的刀（额度该放行 —— 限位，否则"把表清空"也能让前两刀过）。
#
# 它们是**改工具文件**的刀（不是造桩仓），所以必须字节还原。
ANCHOR = "KNOWN_VACUOUS = {\n}"


def _patched_tool(entries: dict) -> bytes:
    """返回一份把 KNOWN_VACUOUS 换成 `entries` 的工具源码。"""
    src = io.open(TOOL, encoding="utf-8").read()
    n = src.count(ANCHOR)
    if n != 1:
        raise RuntimeError("锚点 `KNOWN_VACUOUS = {\n}` 命中 %d 次（期望 1）" % n)
    body = "".join('    "%s": "空转额度对账用",\n' % k for k in entries)
    return src.replace(ANCHOR, "KNOWN_VACUOUS = {\n" + body + "}", 1).encode("utf-8")


def _run_with_table(entries: dict, root: str) -> tuple[int, str]:
    orig = io.open(TOOL, "rb").read()
    # 【2026-11-08 踩的坑·先开文件后算内容会清空源文件】
    # 原来写的是 `io.open(TOOL, "wb").write(_patched_tool(entries))`。
    # Python 先求值 `io.open(TOOL, "wb")` —— **那一刻文件就被截断成 0 字节** ——
    # 然后才去算 `_patched_tool(entries)`；而它一抛（锚点没命中），finally 里的还原
    # 拿到的 `orig` 已经是空的了……实际上更糟：`orig` 是在前面读的，所以还原能成功，
    # 但**这一版把 orig 读在了 truncate 之前**纯属运气。真正发生的是：锚点不匹配时
    # 文件被清空、异常直接冒出去，`tools/check_knife_noop.py` 变成 0 字节。
    # 教训：**先算好要写什么，再打开文件**。
    patched = _patched_tool(entries)
    io.open(TOOL, "wb").write(patched)
    try:
        return run_tool(root)
    finally:
        io.open(TOOL, "wb").write(orig)


def check(tag: str, got: tuple[int, str], want_kind: str | None,
          want_rc: int | None) -> int:
    rc, out = got
    kind = kind_of(out)
    bad = 0
    if want_kind and kind != want_kind:
        print("  FAIL %s：判成 %r（期望 %r）" % (tag, kind, want_kind))
        for l in out.strip().splitlines()[-8:]:
            print("    " + l[:150])
        bad += 1
    if want_rc is not None and rc != want_rc:
        print("  FAIL %s：exit %d（期望 %d）" % (tag, rc, want_rc))
        bad += 1
    if not bad:
        print("  PASS %s：%s / exit %d" % (tag, kind or "-", rc))
    return bad


def main() -> int:
    bad = 0

    # ---- N1 空转的刀 → VACUOUS ----
    d = make_repo({"_qc_vacuous_mutation.py": KNIFE_VACUOUS})
    try:
        bad += check("N1 不看变异的刀 → VACUOUS",
                     run_tool(d, "--knife", "_qc_vacuous_mutation.py"), "VACUOUS", 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N2 老实的刀 → CERTIFIED（不许误判成空转）----
    d = make_repo({"_qc_sound_mutation.py": KNIFE_SOUND})
    try:
        bad += check("N2 老实检查的刀 → CERTIFIED",
                     run_tool(d, "--knife", "_qc_sound_mutation.py"), "CERTIFIED", 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N3 起点红 → ERROR ----
    d = make_repo({"_qc_red_mutation.py": KNIFE_RED})
    try:
        bad += check("N3 起点就红 → ERROR",
                     run_tool(d, "--knife", "_qc_red_mutation.py"), "ERROR", 2)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N4 没有 MUTS → SKIP ----
    d = make_repo({"_qc_nomuts_mutation.py": KNIFE_NO_MUTS})
    try:
        bad += check("N4 没有 MUTS → SKIP",
                     run_tool(d, "--knife", "_qc_nomuts_mutation.py"), "SKIP", 2)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N5 嵌套形状 → CERTIFIED ----
    d = make_repo({"_qc_nested_mutation.py": KNIFE_NESTED})
    try:
        bad += check("N5 嵌套形状 → CERTIFIED",
                     run_tool(d, "--knife", "_qc_nested_mutation.py"), "CERTIFIED", 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N6 全部 SKIP → exit 2 ----
    d = make_repo({"_qc_nomuts_mutation.py": KNIFE_NO_MUTS})
    try:
        rc, out = run_tool(d)
        if rc != 2:
            print("  FAIL N6 全 SKIP：exit %d（期望 2）" % rc)
            for l in out.strip().splitlines()[-6:]:
                print("    " + l[:150])
            bad += 1
        else:
            print("  PASS N6 全 SKIP：exit 2（一把都没跑成不算通过）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N8 额度指向不存在的刀 → exit 2 并点名 ----
    d = make_repo({"_qc_sound_mutation.py": KNIFE_SOUND})
    try:
        rc, out = _run_with_table({"_qc_ghost_mutation.py": 1}, d)
        ok8 = rc == 2 and "_qc_ghost_mutation.py" in out
        print("  %s N8 额度指向不存在的刀：exit %d%s"
              % ("PASS" if ok8 else "FAIL", rc,
                 "" if ok8 else "（期望 2 且点名 _qc_ghost_mutation.py）"))
        if not ok8:
            for l in out.strip().splitlines()[-5:]:
                print("    " + l[:150])
            bad += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N9 额度指向已经修好的刀 → exit 2（表在说谎）----
    d = make_repo({"_qc_sound_mutation.py": KNIFE_SOUND})
    try:
        rc, out = _run_with_table({"_qc_sound_mutation.py": 1}, d)
        ok9 = rc == 2 and "_qc_sound_mutation.py" in out
        print("  %s N9 额度指向已经不空转的刀：exit %d%s"
              % ("PASS" if ok9 else "FAIL", rc,
                 "" if ok9 else "（期望 2 且点名 _qc_sound_mutation.py）"))
        if not ok9:
            for l in out.strip().splitlines()[-5:]:
                print("    " + l[:150])
            bad += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N10 限位：额度指向真的还空转的刀 → 放行 exit 0 ----
    # 没有这一刀，N8/N9 可以靠"永远 exit 2"满足 —— 那就是把额度表判成恒错，
    # 结果是没人再敢往表里写东西，空转又变回没人管。
    d = make_repo({"_qc_vacuous_mutation.py": KNIFE_VACUOUS})
    try:
        rc, out = _run_with_table({"_qc_vacuous_mutation.py": 1}, d)
        ok10 = rc == 0 and "已知空转" in out
        print("  %s N10 限位：额度指向真的空转的刀 → 放行：exit %d%s"
              % ("PASS" if ok10 else "FAIL", rc,
                 "" if ok10 else "（期望 0 且打出「已知空转」）"))
        if not ok10:
            for l in out.strip().splitlines()[-5:]:
                print("    " + l[:150])
            bad += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- N7 对照：真实仓 ----
    rc, out = run_tool(ROOT)
    n_vac = 0
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("结果："):
            for part in s[3:].split("/"):
                bits = part.split()
                if len(bits) == 2 and bits[0] == "VACUOUS":
                    n_vac = int(bits[1])
    # 不断言"恰好几把"：真仓的空转数量会随修复变化。只断言**退出码和它一致**——
    # 【2026-11-08 修】第一版写的是 `want = 1 if n_vac else 0`，但 KNOWN_VACUOUS 里的
    # 空转**不算失败**，于是本机（1 把已知空转）会 exit 0 而断言要求 exit 1 —— 对照自己
    # 先红了。现在改成看**工具自己打的失败标记**：有"新出现的空转刀"必须 exit 1，
    # 没有必须 exit 0。这条在"修完 / 没修完 / 新冒出来"三种状态下都成立。
    has_new = "新出现的空转刀" in out
    want = 1 if has_new else 0
    if rc != want:
        print("  FAIL N7 对照：VACUOUS %d 把、新空转 %s，却 exit %d（期望 %d）"
              % (n_vac, "有" if has_new else "无", rc, want))
        for l in out.strip().splitlines()[-8:]:
            print("    " + l[:150])
        bad += 1
    else:
        print("  PASS N7 对照：真实仓 VACUOUS %d 把（新空转 %s），exit %d 与之一致"
              % (n_vac, "有" if has_new else "无", rc))

    print("\n结果：%s" % ("0 处异常" if bad == 0 else "%d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
