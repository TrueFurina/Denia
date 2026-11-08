#!/usr/bin/env python
"""check_finally_jump 的变异刀（2026-11-08）。

为什么要有刀
------------
这道闸门的判据有两处「容易写成恒真」的地方，不加刀分不清它是在判断还是在装饰：
  ① 「finally 里不许 return」若写成「finally 里出现 return 字样」，
     注释里的 `return`（本仓墓碑注释大量出现）就会让它恒红；
  ② 「break/continue 只在 finally 内没有外层循环时才算跳出」这条限定，
     一旦写漏，finally 里自带 for 的合法写法会被判红——闸门立刻变噪声源，
     然后被人加进白名单，从此再没人看它。

八把刀，刀刀只碰 tempfile
------------------------
闸门接受路径参数，所以每个样本都建在临时目录里跑，**不碰仓库一个字节**，
也就不存在「改源码再还原」的失败面（前几轮的刀栽过这个跟头）。

  M1 finally 里 return                     → 必须红
  M2 循环内 try/finally 里 break            → 必须红（break 绑到外层 for，异常被吞）
  M3 循环内 try/finally 里 continue         → 必须红
  M4 finally 内自带 for + break            → 必须绿（限定生效，别误报）
  M5 finally 内嵌套 def 里的 return         → 必须绿（不跨作用域）
  M6 豁免标记 `# finally-ok:`              → 必须绿
  M7 语法错误                              → exit 2（无法判定 ≠ 通过）
  M8 空目录                                → exit 2（0 个文件不算通过）
  M0 对照组（干净代码）                     → 必须绿

用法：python tools/_qc_finally_jump_mutation.py
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
TOOL = os.path.join(ROOT, "tools", "check_finally_jump.py")
PY = sys.executable

SAMPLES = {
    # M1：finally 里 return
    "m1_return.py": (
        "def f(x):\n"
        "    try:\n"
        "        return int(x)\n"
        "    finally:\n"
        "        return 0\n"
    ),
    # M2：break 在 finally 里，绑的是外层 for → 危险
    "m2_break.py": (
        "def f(items):\n"
        "    for it in items:\n"
        "        try:\n"
        "            print(it)\n"
        "        finally:\n"
        "            break\n"
    ),
    # M3：同上，continue
    "m3_continue.py": (
        "def f(items):\n"
        "    for it in items:\n"
        "        try:\n"
        "            print(it)\n"
        "        finally:\n"
        "            continue\n"
    ),
    # M4：finally 自带循环，break 绑的是那个内层 for → 合法，不许报
    "m4_inner_loop.py": (
        "def f(items):\n"
        "    try:\n"
        "        pass\n"
        "    finally:\n"
        "        for it in items:\n"
        "            if it:\n"
        "                break\n"
    ),
    # M5：finally 里嵌套 def 的 return 属于嵌套函数 → 合法，不许报
    "m5_nested_def.py": (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    finally:\n"
        "        def helper():\n"
        "            return 1\n"
        "        helper()\n"
    ),
    # M6：豁免标记
    "m6_exempt.py": (
        "def f(x):\n"
        "    try:\n"
        "        return int(x)\n"
        "    finally:\n"
        "        # finally-ok: 故意屏蔽这条异常，调用方靠返回值判断\n"
        "        return 0\n"
    ),
    # M7：语法错误
    "m7_broken.py": "def broken(:\n    pass\n",
    # M0：干净
    "m0_clean.py": (
        "def f(x):\n"
        "    try:\n"
        "        return int(x)\n"
        "    except ValueError:\n"
        "        return None\n"
        "    finally:\n"
        "        print('done')\n"
    ),
}


def run_gate(target: str) -> tuple[int, str]:
    p = subprocess.run([PY, TOOL, "--all", target], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="qc_finally_")
    bad = 0
    try:
        # M8：空目录（先跑，此时一个样本都还没写）
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        code, out = run_gate(empty)
        ok = code == 2 and "0 命中不算通过" in out
        print("PASS M8 空目录 -> exit 2 且说明「0 命中不算通过」" if ok
              else "FAIL M8 空目录 -> rc=%d（应 2）" % code)
        bad += 0 if ok else 1
        if not ok:
            print(out[-400:])

        # M0 对照组
        d0 = os.path.join(tmp, "m0")
        os.makedirs(d0)
        with io.open(os.path.join(d0, "m0_clean.py"), "w", encoding="utf-8") as f:
            f.write(SAMPLES["m0_clean.py"])
        code, out = run_gate(d0)
        ok = code == 0
        print("PASS M0 对照组（干净代码）-> exit 0" if ok else "FAIL M0 对照组 -> rc=%d" % code)
        bad += 0 if ok else 1
        if not ok:
            print(out[-400:])

        cases = [
            ("M1", "m1_return.py", 1, "m1_return.py:5"),
            ("M2", "m2_break.py", 1, "m2_break.py:6"),
            ("M3", "m3_continue.py", 1, "m3_continue.py:6"),
            ("M4", "m4_inner_loop.py", 0, ""),
            ("M5", "m5_nested_def.py", 0, ""),
            ("M6", "m6_exempt.py", 0, ""),
            ("M7", "m7_broken.py", 2, "解析失败"),
        ]
        for tag, name, want, must_in in cases:
            d = os.path.join(tmp, tag.lower())
            os.makedirs(d)
            with io.open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(SAMPLES[name])
            code, out = run_gate(d)
            ok = code == want and (must_in in out if must_in else True)
            if ok:
                desc = {0: "exit 0（不误报）", 1: "exit 1 且点名 %s" % must_in,
                        2: "exit 2 且报「%s」" % must_in}[want]
                print("PASS %s %s -> %s" % (tag, _label(name), desc))
            else:
                print("FAIL %s %s -> rc=%d（应 %d）" % (tag, _label(name), code, want))
                bad += 1
                print(out[-500:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print("PASS：8 把刀全部符合预期" if not bad else "FAIL：%d 把刀不符预期" % bad)
    return 1 if bad else 0


def _label(name: str) -> str:
    return {
        "m1_return.py": "finally 里 return",
        "m2_break.py": "循环内 finally 里 break",
        "m3_continue.py": "循环内 finally 里 continue",
        "m4_inner_loop.py": "finally 自带循环里 break",
        "m5_nested_def.py": "finally 内嵌套 def 里 return",
        "m6_exempt.py": "豁免标记",
        "m7_broken.py": "语法错误",
    }.get(name, name)


if __name__ == "__main__":
    sys.exit(main())
