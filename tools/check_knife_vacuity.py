# -*- coding: utf-8 -*-
"""变异刀空转体检：把闸门换成退化实现，看刀套件认不认得出来。

为什么需要
----------
「变异刀全绿」这句话，本会话在每个 PR 里都写过。但它只证明了一件事：
**闸门正常工作时刀会红。** 它没证明：

  · 闸门**永远不红**时刀会不会红 —— 只断言"必须变绿"的套件此时全过
  · 闸门**永远红**时刀会不会红 —— 只断言"必须变红"的套件此时全过

两者缺一个，那个套件就是**空转**的：判据被改松成「一律不报」或改紧成「一律报」，
它都照样通过。它守不住自己声称守住的东西。

做法
----
对每一对 (刀套件, 它验的闸门)，在**仓库副本**里跑三个阶段：

  D0 原样            → 套件必须 exit 0（否则它本来就红，后面的观察没意义）
  D1 闸门 ≡ exit 0   → 套件必须 exit != 0
  D2 闸门 ≡ exit 1   → 套件必须 exit != 0

三个阶段都过，才叫 CERTIFIED。

两个**必须**踩过的坑（都是本工具第一版真踩的，记在这里免得下一个人再踩）
--------------------------------------------------------------------
① **退化桩必须 import-safe。** 第一版写成顶层 `sys.exit(0)`，于是
   `_qc_bridge_guards_mutation.py` 在 `import check_bridge_guards` 时就被
   `SystemExit(0)` 带走 —— 整个套件**一行刀都没跑**，却以 exit 0 收场，
   被本工具判成"空转"。**那是桩的错，不是套件的错。**
   现在桩一律包 `if __name__ == "__main__":`。
   → 一般化：**测别人之前先测自己的仪器**，否则"发现"可能只是仪器坏了。

② **进程内直调闸门的套件，换文件对它无效。** 它 `import check_x as c` 之后用
   `c.scan_files()`；换掉文件只会让它 `AttributeError`。这类套件不是"空转"，
   是**本工具不适用**——所以单列 `SKIP`，不算失败、也不算通过。

分类
----
  CERTIFIED  三阶段全过，且退化是套件**自己报的**失败
  VACUOUS    某个退化方向上套件仍然 exit 0 —— **这就是缺陷**
  WEAK       抓住了，但是靠崩（`Traceback`）而不是靠报失败
             算失败会让工具变噪声源，所以只告警。**已量到**：
             `_qc_broadcast_types_mutation.py` 在闸门零输出时 `IndexError`。
  SKIP       套件进程内 import 了闸门，本工具不覆盖
  ABSENT     本树根本没有这一对（`PAIRS` 是声明式的，可以比本树超前）
             不计失败、也不计通过。它不能用来掩盖删除——`check_gate_registry.py`
             会拦住"登记在 GATES 里的脚本不存在"。
  ERROR      副本建不出来 / 基线本来就红（有但坏了）

已知边界（都是**故意**留的）
----------------------------
① 只覆盖「通过子进程调用闸门文件」的套件。
② 只覆盖登记在 `PAIRS` 里的对，或 `--pair` 显式指定的对。
   全仓 77 个 `_qc_*` 是**两个族**：16 个验 `check_*.py` 闸门，61 个验
   `test_*.py` / 特性判定文件。本工具目前只做前者，且 16 个里还有 1 个
   （`_qc_site_counts_mutation`）是进程内直调、只能 SKIP。
③ **分两层跑**（`--tier`）：`portable` 进 CI，`dev` 需要 `.venv`。
   分层不是"重要性"分层，是"这台机器能不能跑"分层——dev 层的刀把解释器
   硬编码成 `.venv/Scripts/python.exe`，CI 上没有 venv，混进去会让整道门恒红。
④ 每个套件要跑 3 次 + 复制一次仓库副本（无 venv 约 1s，有 venv 约 2s）。
   慢是刻意的：省掉就是省掉证据。

用法
----
    python tools/check_knife_vacuity.py                 # portable 层（CI 跑这个）
    python tools/check_knife_vacuity.py --tier all      # 两层都跑（需要 .venv）
    python tools/check_knife_vacuity.py --only cross    # 名字含 cross 的
    python tools/check_knife_vacuity.py --pair _qc_x_mutation.py=check_x.py
    python tools/check_knife_vacuity.py --root <另一个仓根>

退出码：0 无 VACUOUS / 1 有 VACUOUS / 2 无法判定（一对都没跑成）
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (刀套件, 它验的判定文件, 层, 为什么它必须在名单里)
#
# 层的含义：
#   portable  纯 stdlib、不需要 .venv / 音频设备 / 桌面会话 —— CI 跑这一层
#   dev       刀自己把解释器硬编码成 `.venv/Scripts/python.exe` —— 只有装了 venv
#             的机器能跑。**不是"不重要"，是"这台机器跑不了"**，所以不能混进 CI，
#             否则 CI 上全 ERROR、整道门恒红。
PAIRS = [
    # ---- portable ----
    ("_qc_unreachable_mutation.py", "check_unreachable.py", "portable",
     "第十五/十六轮：M2/M3 是限位刀，必须被 D2 抓住"),
    ("_qc_duplicate_def_mutation.py", "check_duplicate_def.py", "portable",
     "第十七轮：M3/M4 钉住「两边都没装饰器才报」这个限定"),
    ("_qc_finally_jump_mutation.py", "check_finally_jump.py", "portable",
     "第十八轮：八刀里 M4/M5 是限位刀"),
    ("_qc_constant_cond_mutation.py", "check_constant_cond.py", "portable",
     "第十九轮：十三刀里 M8/M9/M10 是限位刀"),
    ("_qc_gate_exit_mutation.py", "check_gate_exit.py", "portable",
     "第二十轮：十五刀里 K4–K10 是限位刀"),
    ("_qc_cross_ref_mutation.py", "check_cross_ref.py", "portable",
     "第二十一轮：十二刀里 K4–K10 是限位刀"),
    ("_qc_web_js_syntax_mutation.py", "check_web_js_syntax.py", "portable",
     "第二十二轮修好 import 路径之后才第一次能跑"),
    # ---- dev（第二十三轮补：这九对的 `TEST` 也指向 check_*.py，是同一族）----
    ("_qc_loop_guard_mutation.py", "check_loop_guard.py", "dev",
     "长任务护栏：刀的 TEST 直接是闸门"),
    ("_qc_loop_guard_e2e_mutation.py", "check_loop_guard_e2e.py", "dev",
     "长任务护栏端到端"),
    ("_qc_partial_reply_mutation.py", "check_partial_reply.py", "dev",
     "半截回复检测"),
    ("_qc_release_numbers_mutation.py", "check_release_numbers.py", "dev",
     "发布口径对账 —— 本轮查出它**没有基线检查**"),
    ("_qc_runtime_audio_mutation.py", "check_runtime_audio_guard.py", "dev",
     "运行时音频守卫 —— 本轮查出同类"),
    ("_qc_site_counts_mutation.py", "check_site_counts.py", "dev",
     "站点计数对账"),
    ("_qc_tool_permission_mutation.py", "check_tool_permission_coverage.py", "dev",
     "工具权限覆盖 —— 本轮查出同类"),
    ("_qc_ui_reload_boundary_mutation.py", "check_ui_reload_boundary.py", "dev",
     "UI 重载边界 —— 本轮查出同类"),
    ("_qc_verify_deps_order_mutation.py", "check_verify_deps_order.py", "dev",
     "出包验收时序 —— 本轮查出同类"),
]

# 【坑①】桩必须 import-safe：顶层 sys.exit 会在 import 时把套件带走，exit 0 假绿。
STUB_ZERO = 'if __name__ == "__main__":\n    raise SystemExit(0)\n'
STUB_ONE = 'if __name__ == "__main__":\n    raise SystemExit(1)\n'

# 套件自己报失败时用的标记；`Traceback` 不算（崩溃 ≠ 识别）
# 套件自己报失败时用的说法。这张表是**实测补出来的**，不是想当然：
# `_qc_release_numbers_mutation` 等五把刀说的是「没抓住（闸门失效）」，
# 而第一版只有「没被抓住」，于是它们被判成 WEAK —— 又一次"仪器太窄"。
FAIL_MARKS = ("✗", "FAIL", "不符合", "没红", "HAS FAIL", "没被抓住", "没抓住",
              "期望 exit", "未抓住")
# 【2026-11-08】`.venv` **不能**排除：dev 层的刀把解释器硬编码成
# `.venv/Scripts/python.exe`，副本里没有它就跑不起来（会全判 ERROR）。
# 代价是副本大一倍（有 venv 的机器约 60MB）；CI 上没有 venv，不受影响。
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "dist", "build"}


def imports_gate(suite_path: str, gate: str) -> bool:
    """套件是否在进程内 import 了闸门模块（换文件对它无效）。"""
    mod = gate[:-3] if gate.endswith(".py") else gate
    try:
        t = ast.parse(io.open(suite_path, encoding="utf-8", errors="replace").read(),
                      filename=suite_path)
    except (OSError, SyntaxError):
        return False
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == mod:
                    return True
        elif isinstance(n, ast.ImportFrom):
            if n.module == mod or (n.module or "").endswith("." + mod):
                return True
    return False


def _copy_repo(root: str) -> str:
    d = tempfile.mkdtemp(prefix="qc_vacuity_")
    dst = os.path.join(d, "repo")
    shutil.copytree(root, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    return dst


def _run(suite_path: str, cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run([sys.executable, suite_path], cwd=cwd,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _is_suite_crash(out: str, suite: str) -> bool:
    """崩溃的是**套件自己**吗？

    【坑③】第一版写成「输出里有 Traceback 就算崩」，是错的：套件会把**闸门**的
    stderr 一起收进自己的输出（`(p.stdout or "") + (p.stderr or "")`），于是闸门在
    退化状态下抛的 Traceback 被误判成"套件崩了"。实测 `_qc_silent_mutation.py`
    明明规规矩矩印了「FAIL：5 刀未抓住」，却被判成 WEAK。
    正确判据：traceback 的帧里必须出现**套件自己的文件名**。
    """
    for block in out.split("Traceback (most recent call last):")[1:]:
        if suite in block.split("\n\n")[0][:4000]:
            return True
    return False


def _degenerate(gate_path: str, pristine: bytes, stub: str, suite_path: str,
                repo: str, suite_name: str) -> tuple[str, str]:
    """返回 (分类, 说明)。分类 ∈ {"ok", "vacuous", "weak"}。"""
    try:
        with io.open(gate_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(stub)
        code, out = _run(suite_path, repo)
    finally:
        with io.open(gate_path, "wb") as f:
            f.write(pristine)

    if code == 0:
        return "vacuous", "套件仍然 exit 0 —— 判据这样退化它发现不了"
    if _is_suite_crash(out, suite_name):
        last = [l for l in out.strip().splitlines() if l.strip()][-1][:140]
        return "weak", "抓住了但靠崩溃（%s）" % last
    if not any(m in out for m in FAIL_MARKS):
        return "weak", "exit %d 但输出里没有失败标记，说不清是识别还是别的原因" % code
    return "ok", "exit %d 且自行报失败" % code


def certify(root: str, suite: str, gate: str) -> tuple[str, list[str]]:
    """返回 (分类, 逐阶段说明)。分类 ∈ CERTIFIED/VACUOUS/WEAK/SKIP/ABSENT/ERROR。"""
    log: list[str] = []
    src_suite = os.path.join(root, "tools", suite)
    src_gate = os.path.join(root, "tools", gate)
    # 【ABSENT 不是 ERROR】`PAIRS` 是**声明式**的：它可以比本树超前（比如某一对
    # 的闸门还在另一个未合的 PR 里）。这种"本树根本没有这一对"和"有但坏了"必须
    # 分开——混在一起，任何一个只 checkout 了部分目录的人都会看到假红。
    # 它不能用来掩盖删除：`check_gate_registry.py` 会拦住"登记在 GATES 里的脚本
    # 不存在"，而 PAIRS 里的套件都在 GATES 里。
    if not os.path.isfile(src_suite) or not os.path.isfile(src_gate):
        missing = [x for x, p in ((suite, src_suite), (gate, src_gate))
                   if not os.path.isfile(p)]
        return "ABSENT", ["本树没有 %s —— 不计失败，也不计通过" % " / ".join(missing)]
    if imports_gate(src_suite, gate):
        return "SKIP", ["套件进程内 import 了 %s —— 换文件对它无效，本工具不覆盖" % gate]

    repo = _copy_repo(root)
    try:
        gate_path = os.path.join(repo, "tools", gate)
        suite_path = os.path.join(repo, "tools", suite)
        if not os.path.isfile(gate_path):
            return "ERROR", ["副本里找不到闸门 %s" % gate]
        pristine = io.open(gate_path, "rb").read()

        code, out = _run(suite_path, repo)
        if code != 0:
            tail = [l for l in out.strip().splitlines() if l.strip()][-1][:140]
            return "ERROR", ["D0 基线本来就红（exit %d：%s）—— 无法判定" % (code, tail)]
        log.append("✓ D0 原样：exit 0")

        k1, w1 = _degenerate(gate_path, pristine, STUB_ZERO, suite_path, repo, suite)
        k2, w2 = _degenerate(gate_path, pristine, STUB_ONE, suite_path, repo, suite)
        log.append(("✓" if k1 == "ok" else "✗") + " D1 闸门≡0：" + w1)
        log.append(("✓" if k2 == "ok" else "✗") + " D2 闸门≡1：" + w2)

        if "vacuous" in (k1, k2):
            return "VACUOUS", log
        if "weak" in (k1, k2):
            return "WEAK", log
        return "CERTIFIED", log
    finally:
        shutil.rmtree(os.path.dirname(repo), ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="变异刀空转体检")
    ap.add_argument("--root", default=ROOT, help="仓根（默认本仓）")
    ap.add_argument("--tier", default="portable", choices=["portable", "dev", "all"],
                    help="跑哪一层（默认 portable；CI 只跑这一层）")
    ap.add_argument("--only", default="", help="只跑套件名包含该子串的对")
    ap.add_argument("--pair", action="append", default=[],
                    help="显式指定 suite.py=gate.py，可重复；给了就不看 PAIRS")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    if not os.path.isdir(os.path.join(root, "tools")):
        print("✗ 根目录下没有 tools/：%s，无法判定" % root, file=sys.stderr)
        return 2

    if args.pair:
        pairs = []
        for spec in args.pair:
            if "=" not in spec:
                print("✗ --pair 需要 suite.py=gate.py：%s" % spec, file=sys.stderr)
                return 2
            s, g = spec.split("=", 1)
            pairs.append((s.strip(), g.strip(), "portable", "命令行指定"))
    else:
        want = {"portable"} if args.tier == "portable" else \
               ({"dev"} if args.tier == "dev" else {"portable", "dev"})
        pairs = [p for p in PAIRS if p[2] in want and args.only in p[0]]

    if not pairs:
        print("✗ 一对都没跑（--only 过滤掉了全部？）无法判定", file=sys.stderr)
        return 2

    print("仓根：%s" % root)
    print("待检 %d 对\n" % len(pairs))

    counts: dict[str, int] = {}
    for suite, gate, _tier, why in pairs:
        print("== %s → %s" % (suite, gate))
        print("   （%s）" % why)
        kind, log = certify(root, suite, gate)
        for line in log:
            print("   " + line)
        print("   → %s\n" % kind)
        counts[kind] = counts.get(kind, 0) + 1

    order = ["CERTIFIED", "VACUOUS", "WEAK", "SKIP", "ABSENT", "ERROR"]
    print("结果：" + " / ".join("%s %d" % (k, counts[k]) for k in order if k in counts))

    ran = sum(counts.get(k, 0) for k in ("CERTIFIED", "VACUOUS", "WEAK"))
    if ran == 0:
        print("✗ 一对都没跑成，无法判定（全部 SKIP/ERROR 不算通过）", file=sys.stderr)
        return 2
    if counts.get("VACUOUS"):
        print("\n✗ 有空转的刀套件：它的判据被改松或改紧，它都发现不了。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
