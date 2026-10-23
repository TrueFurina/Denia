#!/usr/bin/env python
"""check_requirements「解析失败不许静默跳过」的变异验证（2026-10-24）。

守的历史缺陷（闸门沉默类）：`collect_imports` 原来遇 SyntaxError 直接
`continue`——那个文件的 import 全都扫不到，漏声明会静默发生，而退出码仍 0。
「凡遍历文件的闸门，解析失败必须记 error 并判红」就是这条的纪律。

本脚本验两刀：
  M1 往 src/ 放一个语法坏文件 → 闸门必须红，且输出里点名「解析失败」
     （否则修法没生效：红可能是别的原因，点名证明是这条路红的）
  M2 对照组——不放坏文件 → 必须绿（证明红确实由坏文件引起，不是恒红）

用法：python tools/_qc_requirements_syntax_mutation.py
退出码：0 = 两刀都符合预期且还原干净。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "check_requirements.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
# 探针文件名不带 check_/test_ 前缀：不会被闸门登记对账当成未登记闸门；
# 存活时间以毫秒计（finally 里必删），不会波及其它 AST 闸门。
PROBE = os.path.join(ROOT, "src", "_qc_syntax_probe.py")
BROKEN = b"def broken(:\n"  # 铁定的 SyntaxError


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, TOOL, "--strict"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    bad = 0

    # M2 先跑对照组（此时探针不存在）：必须绿
    assert not os.path.exists(PROBE), "探针文件已存在，先删掉再跑"
    code, out = run_gate()
    ok_control = code == 0
    print("M2 对照组（无坏文件）-> rc=%d %s" % (code, "✓ 绿" if ok_control else "❌ 红"))
    if not ok_control:
        bad += 1
        print(out[-400:])

    # M1：放坏文件 → 必须红且点名「解析失败」
    with io.open(PROBE, "wb") as f:
        f.write(BROKEN)
    try:
        code, out = run_gate()
    finally:
        if os.path.exists(PROBE):
            os.remove(PROBE)
    hit = code == 1 and "解析失败" in out and "_qc_syntax_probe.py" in out
    print("M1 放入语法坏文件 -> rc=%d %s" % (code, "✓ 抓住" if hit else "❌ 未抓住"))
    if not hit:
        bad += 1
        print(out[-400:])

    # 还原校验：探针没了 + 闸门回到绿
    assert not os.path.exists(PROBE), "探针文件没删掉！"
    code, out = run_gate()
    ok_after = code == 0
    print("还原后复核 -> rc=%d %s" % (code, "✓ 绿" if ok_after else "❌ 仍红"))
    if not ok_after:
        bad += 1
        print(out[-400:])

    print("=" * 56)
    print("PASS：两刀符合预期" if not bad else "FAIL：%d 处不符" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
