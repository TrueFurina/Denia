# -*- coding: utf-8 -*-
"""跑一个测试；只在「并发抢内存的 OpenBLAS 失败」这一个文案上串行复核一次。

为什么需要它（2026-10-24）：全量门禁连跑时，`test_phash` / `test_set_player_name_wiring`
这类 import numpy 的测试间歇性报
`OpenBLAS error: Memory allocation still failed after 10 retries, giving up.`
——**产品没有任何问题，单独跑就过**（本机 16G 内存，跑闸高峰分配失败）。红成噪音之后，
真红就没人看了（smoke_import 2026-10-21 的原话）。

修法与 smoke_import 完全同构，纪律也一样：
  - **只认这一种错误文案**（它来自 OpenBLAS 而不是产品代码，有外部证据：
    同一测试单独跑就过）。别的错误没有这个证据，一律不重试——重试会掩盖真缺陷。
  - **只串行复核一次**。复核后仍带同一文案 → 仍然红（那是真·内存不足）。
  - 复核救回来 → 印 `[串行复核] <测试名>` 并 exit 0。

用法：python tools/run_test_with_mem_retry.py src/tests/test_xxx.py
退出码：被跑测试的退出码（复核救回则为 0）。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

# 与 tools/smoke_import.py 的 _MEM_FAIL_MARK 同一定语
MEM_FAIL_MARK = "Memory allocation still failed"


def _run(test: str) -> tuple[int, str]:
    p = subprocess.run([PY, "-u", test], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: run_test_with_mem_retry.py <测试脚本路径>")
        return 2
    test = sys.argv[1]
    code, out = _run(test)
    if code == 0:
        return 0
    if MEM_FAIL_MARK not in out:
        # 不是这一种错：原样转发退出码，绝不重试（重试会掩盖真缺陷）
        sys.stdout.write(out[-2000:])
        return code
    # 唯一允许的重试：串行再跑一次
    code2, out2 = _run(test)
    name = os.path.basename(test)
    if code2 == 0:
        print("[串行复核] %s 首次因 OpenBLAS 内存分配失败，串行复核通过" % name)
        return 0
    # 复核仍失败：仍然红，并留下"真复核过"的痕迹
    print("[串行复核仍失败] %s（真·内存不足或真缺陷，保持红）" % name)
    sys.stdout.write(out2[-2000:])
    return code2


if __name__ == "__main__":
    sys.exit(main())
