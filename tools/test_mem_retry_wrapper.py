# -*- coding: utf-8 -*-
"""run_test_with_mem_retry 的自测（2026-10-24）。

守三条（三条都红过或差一点红过，见各 fixture 注释）：
  [1] 首跑 OpenBLAS 失败、串行能过 → 必须救回并印 [串行复核]（否则修法没生效）
  [2] 真缺陷（文案不是 OpenBLAS）→ **不许重试**、立即红（否则重试掩盖真缺陷——
      这是整个修法最大的失败模式）
  [3] 每次都报 OpenBLAS → 串行复核后**仍然红**且带「串行复核仍失败」痕迹
      （否则"一律判过"比不复核更糟）

用临时目录里的假测试，不碰真仓库。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(ROOT, "tools", "run_test_with_mem_retry.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

OK = FAIL = 0
_MEM_ERR = "OpenBLAS error: Memory allocation still failed after 10 retries, giving up."


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def make(base: str, name: str, body: str) -> str:
    p = os.path.join(base, name)
    io.open(p, "w", encoding="utf-8", newline="\n").write(body)
    return p


def run_wrapper(test: str) -> tuple[int, str]:
    p = subprocess.run([PY, "-u", WRAPPER, test], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    base = tempfile.mkdtemp(prefix="daniya_mem_retry_")

    # [1] 首次 OpenBLAS 失败、第二次成功（靠 .seen 标记区分两次）
    flaky = make(base, "flaky_test.py",
                 "import os\n"
                 "marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.seen')\n"
                 "if not os.path.exists(marker):\n"
                 "    open(marker, 'w').write('1')\n"
                 "    import sys\n"
                 "    print(%r, file=sys.stderr)\n"
                 "    raise SystemExit(1)\n"
                 "print('flaky OK')\n" % _MEM_ERR)
    code, out = run_wrapper(flaky)
    print("[1] 并发抢内存的抖动：串行复核必须救回")
    chk("退出码 0", code == 0, code)
    chk("印了 [串行复核] 且点名该测试", "[串行复核]" in out and "flaky_test.py" in out)

    # [2] 真缺陷：文案不是 OpenBLAS → 不许重试
    real = make(base, "real_test.py",
                "import sys\n"
                "print('AssertionError: 真缺陷', file=sys.stderr)\n"
                "raise SystemExit(1)\n")
    code, out = run_wrapper(real)
    print("[2] 真缺陷：绝不重试（重试会掩盖真缺陷）")
    chk("退出码 1", code == 1, code)
    chk("没有 [串行复核] 痕迹", "[串行复核]" not in out)
    chk("原样转发了失败输出", "真缺陷" in out)

    # [3] 每次都 OpenBLAS 失败 → 复核后仍红，且留"真复核过"的痕迹
    memfail = make(base, "memfail_test.py",
                   "import sys\n"
                   "print(%r, file=sys.stderr)\n"
                   "raise SystemExit(1)\n" % _MEM_ERR)
    code, out = run_wrapper(memfail)
    print("[3] 复核救不回来的：仍然红且留痕")
    chk("退出码 1", code == 1, code)
    chk("带「串行复核仍失败」痕迹（说明真复核过）", "串行复核仍失败" in out)

    print()
    print("结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
