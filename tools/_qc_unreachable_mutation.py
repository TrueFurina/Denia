# -*- coding: utf-8 -*-
"""不可达代码闸门的变异刀（2026-11-08）。

判据是 tools/check_unreachable.py 本身。刀不碰真实源码——闸门吃的是**路径**，
所以在临时目录里造样本喂给它即可。这样刀永远不会把仓库改坏，也不需要"还原校验"。

六刀 + 一组对照：

  M1 死语句（return 之后）          → 必须红，且点名该文件与行号
  M2 同一处加 `# unreachable-ok:`   → 必须绿（证明豁免真的生效，不是摆设）
  M3 标记隔着一个空行（不紧邻）      → 必须红（证明不是"文件里出现过就豁免"，
                                       否则随便一段无关注释就能把闸门弄哑）
  M4 try/except 两条分支都 return    → 必须红（第十五轮 create_notify_window 的真形状）
  M5 循环里 continue 之后的语句      → 必须红（证明递归进了 if/for 体，
                                       只查函数顶层会漏掉这一类）
  M6 有语法错误的文件                → 必须 exit 2，不许静默通过
                                       （无法判定 ≠ 通过；本仓在"检查器默认永远
                                        exit 0"上踩过，那道闸门装饰了两个月）
  对照 干净代码                      → 必须绿，且输出里"不可达 0 处"

断言的是"抓到了注入的那个错"，不是"任意非零退出"——否则分不清闸门在工作、
还是闸门自己崩了（两者都 exit 1）。

用法：python tools/_qc_unreachable_mutation.py
退出码 0 = 六刀 + 对照全部符合预期。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(HERE, "check_unreachable.py")

# 每个样本：文件名 → 源码。行号写死在断言里，改样本时必须同步改（否则断言空转）。
CASE_DEAD = "dead_plain.py"
CASE_DEAD_MARKED = "dead_marked.py"
CASE_DEAD_MARKED_FAR = "dead_marked_far.py"
CASE_TRY_BOTH = "dead_try_both.py"
CASE_LOOP_CONTINUE = "dead_loop_continue.py"
CASE_SYNTAX = "broken_syntax.py"
CASE_CLEAN = "clean_ok.py"

SAMPLES = {
    CASE_DEAD: """def f():
    return 1
    x = 2
""",
    CASE_DEAD_MARKED: """def f():
    return 1
    # unreachable-ok: 样本用——故意不可达，验证豁免生效
    x = 2
""",
    CASE_DEAD_MARKED_FAR: """def f():
    return 1
    # unreachable-ok: 这段说明属于别处
    # 中间空一行，就不该再豁免下面那句

    x = 2
""",
    CASE_TRY_BOTH: """def f():
    try:
        return 1
    except Exception:  # noqa: BLE001
        return 2
    x = 3
""",
    CASE_LOOP_CONTINUE: """def f(items):
    for i in items:
        if i:
            continue
            print("dead")
        print(i)
""",
    CASE_SYNTAX: """def f(:
    pass
""",
    CASE_CLEAN: """def f(c):
    try:
        if c:
            return 1
        return 2
    except Exception:  # noqa: BLE001
        return 3


def g(items):
    for i in items:
        if i:
            continue
        print(i)
    return None
""",
}


def _run_gate(target: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE, target], cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _make_dir(exclude=()) -> str:
    d = tempfile.mkdtemp(prefix="qc_unreachable_")
    for name, src in SAMPLES.items():
        if name in exclude:
            continue
        with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
    return d


def _expect(d: str, name: str, want_code: int, want_line: int | None,
            want_green_word: str | None, tag: str, why: str) -> int:
    """跑闸门并断言：退出码 + 点名到具体文件（+ 行号）。返回 1 表示不符合预期。"""
    code, out = _run_gate(d)
    if code != want_code:
        print("  FAIL %s（%s）：期望 exit %d，实际 %d" % (tag, why, want_code, code))
        for line in out.splitlines()[:6]:
            print("    " + line[:160])
        return 1

    if want_code == 0:
        if want_green_word and want_green_word not in out:
            print("  FAIL %s：exit 0 但输出里没有「%s」——可能根本没扫到文件" % (tag, want_green_word))
            return 1
        print("  PASS %s（%s）：exit 0，%s" % (tag, why, want_green_word or "绿"))
        return 0

    if want_code == 2:
        if "解析失败" not in out and "无法判定" not in out:
            print("  FAIL %s：exit 2 但没说清是解析失败——分不清闸门坏了还是文件坏了" % tag)
            return 1
        print("  PASS %s（%s）：exit 2 且明确报「无法判定」" % (tag, why))
        return 0

    # want_code == 1：必须点名到具体文件
    if name not in out:
        print("  FAIL %s：exit 1 但没点名 %s（可能红在别的样本上）" % (tag, name))
        for line in out.splitlines()[:6]:
            print("    " + line[:160])
        return 1
    if want_line is not None:
        pat = re.escape(name) + r":%d" % want_line
        if not re.search(pat, out):
            print("  FAIL %s：点名了 %s 但不是第 %d 行（行号漂了？）" % (tag, name, want_line))
            for line in out.splitlines()[:6]:
                print("    " + line[:160])
            return 1
    print("  PASS %s（%s）：exit 1 且点名 %s:%s" % (tag, why, name, want_line if want_line else "?"))
    return 0


def main() -> int:
    if not os.path.isfile(GATE):
        print("  FAIL 闸门不存在：%s" % GATE)
        return 1

    d = _make_dir(exclude=(CASE_SYNTAX,))
    bad = 0
    try:
        # 先证明"整批里有 4 处该红的地方"——一次跑，确认总数，防止某个样本
        # 因为写错而悄悄不触发（单跑时它会被 M2/M6 的期望掩盖）。
        # 语法错样本故意排除：闸门遇到解析失败会返回 2（无法判定），
        # 那会把基线的退出码从 1 变成 2，掩盖"到底抓到几处"这件事。
        code, out = _run_gate(d)
        m = re.search(r"不可达 (\d+) 处", out)
        got = int(m.group(1)) if m else -1
        if code != 1 or got != 4:
            print("  FAIL 基线：期望 exit 1 / 不可达 4 处（M1+M3+M4+M5），实际 exit %d / %s 处"
                  % (code, got if got >= 0 else "?"))
            for line in out.splitlines()[:10]:
                print("    " + line[:160])
            bad += 1
        else:
            print("  基线：6 个样本里 4 处不可达（M1/M3/M4/M5 各 1），1 处豁免")

        # 单样本逐个断言：把其他样本挪走，只留一个
        for name, code_want, line_want, green_word, tag, why in [
            (CASE_DEAD, 1, 3, None, "M1", "return 之后的死语句"),
            (CASE_DEAD_MARKED, 0, None, "不可达 0 处", "M2", "加 unreachable-ok 后豁免"),
            (CASE_DEAD_MARKED_FAR, 1, 6, None, "M3", "标记隔着空行不算豁免"),
            (CASE_TRY_BOTH, 1, 6, None, "M4", "try/except 双分支 return 之后"),
            (CASE_LOOP_CONTINUE, 1, 5, None, "M5", "循环体里 continue 之后"),
            (CASE_SYNTAX, 2, None, None, "M6", "语法错误不许静默通过"),
            (CASE_CLEAN, 0, None, "不可达 0 处", "对照", "干净代码"),
        ]:
            solo = tempfile.mkdtemp(prefix="qc_unreach_%s_" % tag)
            try:
                # 从 SAMPLES 直接写：基线的 d 里故意排除了语法错样本，
                # 从 d 拷会 FileNotFoundError（第一版就这么挂的）。
                with io.open(os.path.join(solo, name), "w",
                             encoding="utf-8", newline="\n") as f:
                    f.write(SAMPLES[name])
                bad += _expect(solo, name, code_want, line_want, green_word, tag, why)
            finally:
                shutil.rmtree(solo, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("=" * 60)
    if bad:
        print("变异刀发现 %d 处问题" % bad)
        return 1
    print("六刀 + 对照组全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
