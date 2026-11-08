#!/usr/bin/env python
"""check_constant_cond 的变异刀（2026-11-08）。

这道闸门的判据有三处特别容易写成恒真/恒假，不加刀分不清它是在判断还是在装饰：
  ① 「只查条件位置」这条限定——写漏了，`v = a or True` 这种**值位置**写法会被误报，
     而它在值位置不是常量（a 为真时返回 a）；
  ② 「`while True:` 是惯用法」这条限定——写漏了，全仓几十个无限循环立刻变红，
     然后闸门被加进白名单；
  ③ 「`check()` 只看布尔运算形态」这条限定——写漏了，本仓几种 `check()` 签名里
     传字符串的那些会被当成"恒真条件"，一上来几百条噪声。

十三把刀，刀刀只碰 tempfile
--------------------------
闸门接受路径参数，样本全建在临时目录，**不碰仓库一个字节**，也就不存在
「改源码再还原」的失败面（前几轮栽过）。

  M1  if False:                     → 红
  M2  while 0:                      → 红
  M3  if True: … else: …            → 红（else 死）
  M4  if x or True:                 → 红
  M5  if x and False:               → 红
  M6  assert y or True              → 红
  M7  check(a or True, "m")         → 红
  M8  while True: … break           → 绿（惯用法，限位）
  M9  v = a or True（值位置）        → 绿（限位：只查条件位置）
  M10 if x == 0:                    → 绿（限位：比较不是字面量）
  M11 豁免标记 `# const-cond-ok:`   → 绿
  M12 语法错误                      → exit 2（无法判定 ≠ 通过）
  M13 空目录                        → exit 2（0 个文件不算通过）
  M0  对照组（干净代码）             → 绿

用法：python tools/_qc_constant_cond_mutation.py
退出码：0 = 全部符合预期。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "check_constant_cond.py")
PY = sys.executable

CASES = [
    # tag, 文件名, 源码, 期望退出码, 输出里必须出现的片段, 说明
    ("M1", "m1_if_false.py", "def f():\n    if False:\n        print(1)\n", 1,
     "条件恒假", "if False:"),
    ("M2", "m2_while_0.py", "def f():\n    while 0:\n        print(1)\n", 1,
     "条件恒假", "while 0:"),
    ("M3", "m3_if_true_else.py",
     "def f(x):\n    if True:\n        return x\n    else:\n        return 0\n", 1,
     "else 里", "if True: … else:"),
    ("M4", "m4_or_true.py", "def f(x):\n    if x or True:\n        print(1)\n", 1,
     "条件恒真", "if x or True:"),
    ("M5", "m5_and_false.py", "def f(x):\n    if x and False:\n        print(1)\n", 1,
     "条件恒假", "if x and False:"),
    ("M6", "m6_assert.py", "def f(y):\n    assert y or True\n", 1,
     "assert 条件恒真", "assert y or True"),
    ("M7", "m7_check.py",
     "def f(a):\n    check(a or True, 'm')\n", 1,
     "check() 判据 条件恒真", "check(a or True, 'm')"),
    ("M8", "m8_while_true.py",
     "def f():\n    while True:\n        break\n", 0, "", "while True: … break"),
    ("M9", "m9_value_pos.py", "def f(a):\n    v = a or True\n    return v\n", 0, "",
     "v = a or True（值位置）"),
    ("M10", "m10_compare.py", "def f(x):\n    if x == 0:\n        print(1)\n", 0, "",
     "if x == 0:"),
    ("M11", "m11_exempt.py",
     "def f():\n    # const-cond-ok: 故意留着，等硬件到位再打开\n    if False:\n        print(1)\n", 0,
     "", "豁免标记"),
    ("M12", "m12_broken.py", "def broken(:\n    pass\n", 2, "解析失败", "语法错误"),
]


def run_gate(target: str) -> tuple[int, str]:
    p = subprocess.run([PY, TOOL, "--all", target], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="qc_constcond_")
    bad = 0
    try:
        # M13 空目录（先跑：此时还没有任何样本）
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        code, out = run_gate(empty)
        ok = code == 2 and "0 命中不算通过" in out
        print("PASS M13 空目录 -> exit 2 且说明「0 命中不算通过」" if ok
              else "FAIL M13 空目录 -> rc=%d（应 2）" % code)
        bad += 0 if ok else 1
        if not ok:
            print(out[-400:])

        # M0 对照组
        d0 = os.path.join(tmp, "m0")
        os.makedirs(d0)
        with io.open(os.path.join(d0, "m0_clean.py"), "w", encoding="utf-8") as f:
            f.write("def f(x):\n    if x > 0:\n        return 1\n    return 0\n")
        code, out = run_gate(d0)
        ok = code == 0
        print("PASS M0 对照组（干净代码）-> exit 0" if ok else "FAIL M0 对照组 -> rc=%d" % code)
        bad += 0 if ok else 1
        if not ok:
            print(out[-400:])

        for tag, name, src, want, must_in, label in CASES:
            d = os.path.join(tmp, tag.lower())
            os.makedirs(d)
            with io.open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(src)
            code, out = run_gate(d)
            ok = code == want and (must_in in out if must_in else True)
            if ok:
                desc = {0: "exit 0（不误报）", 1: "exit 1 且点名「%s」" % must_in,
                        2: "exit 2 且报「%s」" % must_in}[want]
                print("PASS %s %s -> %s" % (tag, label, desc))
            else:
                print("FAIL %s %s -> rc=%d（应 %d）" % (tag, label, code, want))
                bad += 1
                print(out[-500:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 62)
    print("PASS：13 把刀 + 对照全部符合预期" if not bad else "FAIL：%d 把刀不符预期" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
