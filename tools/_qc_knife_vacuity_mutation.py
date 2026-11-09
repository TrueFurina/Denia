# -*- coding: utf-8 -*-
"""`tools/check_knife_vacuity.py` 的变异刀：证明它真的能认出空转的刀套件。

为什么需要这组刀
----------------
空转体检的结论是「6 对全部 CERTIFIED」——**0 命中是最危险的读数**。
如果体检工具本身坏了（永远说 CERTIFIED），那 7 个 CERTIFIED 一个都不值。
所以这里的刀全部是**人造的空转套件**：造一个只断言"必须红"的、再造一个只断言
"必须绿"的，看体检工具会不会分别把它们判成 VACUOUS。

样本全建在 tempfile，每个刀一套独立的小仓（`tools/` 里只有桩闸门 + 桩套件），
**不碰仓库一个字节**，所以没有"改完再还原"的失败面。

十刀 + 对照
-----------
  K1 只判退出码的正向套件        → VACUOUS（D2 方向空转：闸门恒红它也全绿）
  K2 只断言"必须绿"的套件        → VACUOUS（D1 方向空转：闸门恒绿它也全绿）
  K3 两个方向都断言的套件        → CERTIFIED（正面：不许把好套件误判成空转）
  K4 退化时崩溃的套件            → WEAK（不算 VACUOUS，也不算 CERTIFIED）
  K5 进程内 import 闸门的套件    → SKIP（本工具不覆盖，不能算通过）
  K6 基线本来就红的套件          → ERROR（无法判定）
  K7 **套件本身**不存在          → ABSENT / exit 2（本树没有这一对 ≠ 通过）
  K12 声明了 TEST 却没登记的刀    → exit 2 并点名（零覆盖不许长得像全覆盖）
  K13 套件在、判定不在            → ERROR（不是 ABSENT：那是 PAIRS 声明错了）
  K8 全部 SKIP                   → exit 2（一对都没跑成 ≠ 通过）
  K9 **输出里有闸门的 Traceback，但套件自己报了失败** → 仍判 CERTIFIED
     这一刀钉的是本工具真踩的坑③：第一版把"输出里有 Traceback"当成套件崩溃，
     于是 `_qc_silent_mutation.py` 明明印了「FAIL：5 刀未抓住」却被判 WEAK。
  K11 **分层不许漏**：PAIRS 里 portable 恰好 8 对、dev 恰好 21 对，且两层互不混入
     分层是"这台机器能不能跑"的分层，最怕的是某一层被悄悄漏掉（少跑几对没人发现），
     或者 dev 的对混进 portable（CI 上全 ERROR，把整道门变成恒红）。
  K10 对照：真实仓的 PAIRS       → exit 0 且 CERTIFIED 7

用法：python tools/_qc_knife_vacuity_mutation.py
退出码 0 = 十一刀 + 对照全部符合预期。
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(HERE, "check_knife_vacuity.py")

# ---------------------------------------------------------------- 桩闸门 / 桩套件

GATE_HIT = '''\
import sys
print("命中 1 处")
raise SystemExit(1)
'''

GATE_OK = '''\
import sys
print("干净")
raise SystemExit(0)
'''

GATE_DUAL = '''\
import sys
if "--clean" in sys.argv:
    print("干净")
    raise SystemExit(0)
print("命中 1 处")
raise SystemExit(1)
'''

HEAD = '''\
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "%s")


def run(*args):
    p = subprocess.run([sys.executable, GATE, *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")
'''

# 只断言"必须红"、且**只判退出码**（不判点名）——
# 闸门恒 exit 1 时它仍然全绿：这正是 `_qc_unreachable` docstring 里警告的那个
# 反模式「断言的是任意非零退出，而不是抓到了注入的那个错」。
SUITE_POSITIVE_ONLY = HEAD % "check_hit.py" + '''

code, out = run()
ok = code != 0                 # 没有"点名"断言 → 闸门恒红时它分辨不出来
print("✓ K1" if ok else "✗ K1 期望非零退出")
sys.exit(0 if ok else 1)
'''

# 只断言"必须绿" —— 闸门恒 exit 0 时它仍然全绿（空转）
SUITE_LIMIT_ONLY = HEAD % "check_ok.py" + '''

code, out = run()
ok = code == 0
print("✓ K2" if ok else "✗ K2 期望 exit 0")
sys.exit(0 if ok else 1)
'''

# 两个方向都断言 —— 这才是合格套件
SUITE_BOTH = HEAD % "check_dual.py" + '''

bad = 0
code, out = run()
if not (code == 1 and "命中" in out):
    print("✗ K3 正向：期望 exit 1 且点名")
    bad += 1
else:
    print("✓ K3 正向")
code, out = run("--clean")
if code != 0:
    print("✗ K3 限位：期望 exit 0")
    bad += 1
else:
    print("✓ K3 限位")
sys.exit(1 if bad else 0)
'''

# 退化时崩溃（闸门零输出 → 取第一行 IndexError）
SUITE_CRASH = HEAD % "check_hit.py" + '''

code, out = run()
first = out.strip().splitlines()[0]      # 闸门零输出时这里 IndexError
ok = code == 1 and "命中" in first
print("✓ K4" if ok else "✗ K4")
sys.exit(0 if ok else 1)
'''

# 进程内 import 闸门 —— 换文件对它无效
SUITE_INPROC = '''\
import sys
import check_hit
print("✓ K5 进程内")
sys.exit(0)
'''

# 基线本来就红
SUITE_ALWAYS_RED = '''\
import sys
print("✗ K6 我永远红")
sys.exit(1)
'''

# 输出里带闸门的 Traceback，但自己规规矩矩报失败（坑③的钉子）
SUITE_NOISY = HEAD % "check_hit.py" + '''

code, out = run()
if code == 1 and "命中" in out:
    print("✓ K10 正向")
    sys.exit(0)
print("Traceback (most recent call last):")
print('  File "%s", line 1, in <module>' % GATE)
print("ValueError: 这是闸门抛的，不是套件崩了")
print("✗ K10 期望 exit 1 且点名")
sys.exit(1)
'''


def make_repo(gates: dict, suites: dict) -> str:
    d = tempfile.mkdtemp(prefix="qc_kv_")
    t = os.path.join(d, "tools")
    os.makedirs(t)
    for name, src in gates.items():
        io.open(os.path.join(t, name), "w", encoding="utf-8", newline="\n").write(src)
    for name, src in suites.items():
        io.open(os.path.join(t, name), "w", encoding="utf-8", newline="\n").write(src)
    return d


def run_tool(root: str, *pairs: str) -> tuple[int, str]:
    argv = [sys.executable, TOOL, "--root", root]
    for p in pairs:
        argv += ["--pair", p]
    r = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kind_of(out: str) -> str:
    """取最后一个 `→ KIND` 行的 KIND。"""
    found = ""
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("→ "):
            found = s[2:].strip()
    return found


def check(tag: str, got: tuple[int, str], want_kind: str, want_rc: int | None) -> int:
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
    if not os.path.isfile(TOOL):
        print("  FAIL 体检工具不存在：%s" % TOOL)
        return 1

    bad = 0

    # ---- K1 只正向 → VACUOUS ----
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_pos_only.py": SUITE_POSITIVE_ONLY})
    try:
        bad += check("K1 只断言必须红 → VACUOUS",
                     run_tool(d, "_qc_pos_only.py=check_hit.py"), "VACUOUS", 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K2 只限位 → VACUOUS ----
    d = make_repo({"check_ok.py": GATE_OK},
                  {"_qc_limit_only.py": SUITE_LIMIT_ONLY})
    try:
        bad += check("K2 只断言必须绿 → VACUOUS",
                     run_tool(d, "_qc_limit_only.py=check_ok.py"), "VACUOUS", 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K3 两个方向都有 → CERTIFIED ----
    d = make_repo({"check_dual.py": GATE_DUAL},
                  {"_qc_both.py": SUITE_BOTH})
    try:
        bad += check("K3 两个方向都有 → CERTIFIED",
                     run_tool(d, "_qc_both.py=check_dual.py"), "CERTIFIED", 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K4 退化时崩溃 → WEAK（不算空转，也不算通过）----
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_crash.py": SUITE_CRASH})
    try:
        bad += check("K4 退化时崩溃 → WEAK",
                     run_tool(d, "_qc_crash.py=check_hit.py"), "WEAK", 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K5 进程内 import → SKIP ----
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_inproc.py": SUITE_INPROC})
    try:
        bad += check("K5 进程内 import → SKIP",
                     run_tool(d, "_qc_inproc.py=check_hit.py"), "SKIP", None)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K6 基线本来就红 → ERROR ----
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_red.py": SUITE_ALWAYS_RED})
    try:
        bad += check("K6 基线本来就红 → ERROR",
                     run_tool(d, "_qc_red.py=check_hit.py"), "ERROR", None)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K7 **套件本身**不存在 → ABSENT（本树没有这一对，不是"有但坏了"）----
    # 单独一对 ABSENT 时 ran==0，所以还必须是 exit 2：ABSENT 不能被当成通过。
    # 【2026-11-08 改】原来这一刀造的是"套件在、判定不在"，而那个口径在第二十四轮
    # 被改正了：套件在、判定不在 = **PAIRS 声明错了**，是 ERROR（见 K13）。
    # ABSENT 只剩一种情况：套件都不在本树。K7 现在钉这一种。
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_both2.py": SUITE_BOTH})
    try:
        bad += check("K7 套件不存在 → ABSENT / exit 2",
                     run_tool(d, "_qc_nope.py=check_hit.py"), "ABSENT", 2)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K8 全部 SKIP → exit 2（一对都没跑成 ≠ 通过）----
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_inproc2.py": SUITE_INPROC})
    try:
        rc, out = run_tool(d, "_qc_inproc2.py=check_hit.py")
        if rc != 2:
            print("  FAIL K8 全 SKIP：exit %d（期望 2）" % rc)
            for l in out.strip().splitlines()[-6:]:
                print("    " + l[:150])
            bad += 1
        else:
            print("  PASS K8 全 SKIP：exit 2（0 对跑成不算通过）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K9 输出里有闸门的 Traceback，但套件自己报了失败 → 仍 CERTIFIED ----
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_noisy.py": SUITE_NOISY})
    try:
        bad += check("K9 闸门 Traceback 不算套件崩溃 → CERTIFIED",
                     run_tool(d, "_qc_noisy.py=check_hit.py"), "CERTIFIED", 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K11 分层不许漏 ----
    # 分层最怕的不是跑错，是**某一层被悄悄漏掉**：少跑几对没人发现，而
    # 「结果：CERTIFIED 7」和「跑全了」长得一模一样。
    # 这里查三件事，都便宜：
    #   ① PAIRS 的静态计数（portable 8 / dev 23，合计 31）—— 直接读，不跑
    #   ② `--tier` 的过滤真的生效（dev 层挑一对，看「待检」行是 1）
    #   ③ 不该进 portable 的对，portable 真的不选（`--only release_numbers`
    #      在 portable 层应为 0 对 → exit 2 且没有「待检」行）
    # 为什么不用 `--tier all` 数「待检」：那会把 9 对 dev 全跑一遍（每对一次仓副本
    # + 3 次套件），CI 上还没有 `.venv`，白等十几秒换一屏 ERROR。计数要便宜才对。
    spec = importlib.util.spec_from_file_location("_kv_under_test", TOOL)
    kv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kv)
    tiers: dict[str, int] = {}
    for _p in kv.PAIRS:
        tiers[_p[2]] = tiers.get(_p[2], 0) + 1

    def picked(*extra) -> int | None:
        r = subprocess.run([sys.executable, TOOL, *extra], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        for line in (r.stdout or "").splitlines():
            if line.startswith("待检 ") and line.endswith(" 对"):
                return int(line[3:].split()[0])
        return None

    n_dev = picked("--tier", "dev", "--only", "release_numbers")
    n_leak = picked("--tier", "portable", "--only", "release_numbers")
    if tiers.get("portable") != 8 or tiers.get("dev") != 24:
        print("  FAIL K11 分层计数：portable %r / dev %r（期望 8 / 24）"
              % (tiers.get("portable"), tiers.get("dev")))
        bad += 1
    elif sum(tiers.values()) != 32:
        print("  FAIL K11 总对数 %d（期望 32；PAIRS 里有未知层 %r）"
              % (sum(tiers.values()), sorted(tiers)))
        bad += 1
    elif n_dev != 1:
        print("  FAIL K11 dev 层选择：%r 对（期望 1，dev 过滤没生效）" % n_dev)
        bad += 1
    elif n_leak is not None:
        print("  FAIL K11 dev 的对混进了 portable 层：%r 对（期望 0 对 → exit 2）" % n_leak)
        bad += 1
    else:
        print("  PASS K11 分层：portable %d / dev %d，互不混入"
              % (tiers["portable"], tiers["dev"]))

    # ---- K12 完整性：声明了 TEST 却没登记的刀，必须被点出来 ----
    # 这一刀钉的是"零覆盖长得像全覆盖"：新写一把带 `TEST` 的刀、忘了加进 PAIRS，
    # 它就从没被验过，而结果行照样是绿的。
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_orphan.py": 'TEST = "check_hit.py"\n'})
    try:
        rc, out = run_tool(d)          # 不带 --pair，才走完整性检查
        if rc != 2 or "_qc_orphan.py" not in out:
            print("  FAIL K12 未登记的刀：exit %d，输出%s点名" % (rc, "有" if "_qc_orphan.py" in out else "没"))
            for l in out.strip().splitlines()[-8:]:
                print("    " + l[:150])
            bad += 1
        else:
            print("  PASS K12 未登记的刀 → exit 2 并点名：_qc_orphan.py")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K13 套件在、判定不在 = 声明错了，不是「本树没有」 ----
    # 第一版把这两种情况都判 ABSENT，而 ABSENT「不计失败也不计通过」。
    # 实测代价：13 个特性族的对按 `src/tests/test_*.py` 写进 PAIRS 后全部 ABSENT，
    # `结果：ABSENT 13`、exit 0 —— 一个都没验，却什么都没说。
    d = make_repo({"check_hit.py": GATE_HIT},
                  {"_qc_ghost.py": SUITE_NOISY})
    try:
        bad += check("K13 判定路径拼错 → ERROR（不是 ABSENT）",
                     run_tool(d, "_qc_ghost.py=src/tests/nope.py"), "ERROR", 2)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- K10 对照：真实仓 ----
    p = subprocess.run([sys.executable, TOOL], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    # 不断言"恰好 7 对"：PAIRS 可以比本树超前（ABSENT 是合法状态），
    # 所以只钉「没有空转、没有未判定、且至少 6 对真的被证明过」。
    cert = 0
    for line in out.splitlines():
        if line.strip().startswith("结果："):
            for part in line.strip()[3:].split("/"):
                bits = part.split()
                if len(bits) == 2 and bits[0] == "CERTIFIED":
                    cert = int(bits[1])
    ok = (p.returncode == 0 and "VACUOUS" not in out and cert >= 6)
    if ok:
        print("  PASS K10 对照：真实仓 %d 对 CERTIFIED，无空转，exit 0" % cert)
    else:
        print("  FAIL K10 对照：exit %d" % p.returncode)
        for l in out.strip().splitlines()[-8:]:
            print("    " + l[:150])
        bad += 1

    print("\n结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
