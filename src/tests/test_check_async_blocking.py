"""tools/check_async_blocking.py 的自测（正例 + 反例）。

闸门如果只会打印不会判红，比没有更危险（本项目吃过"文本编码闸门永远 exit 0"的亏）。

覆盖：
  * 正例：async 里 time.sleep / urllib.urlopen / subprocess.run → 必须报
  * 反例：to_thread 包裹、嵌套同步 worker（交给 to_thread）、同步函数里的 sleep → 不许报

运行：python src/tests/test_check_async_blocking.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import check_async_blocking as C  # noqa: E402

CASES: list[tuple[str, str, int]] = [
    ("async 里 time.sleep", """
import time
async def f():
    time.sleep(1)
""", 1),
    ("to_thread 包裹（正常）", """
import asyncio, time
async def f():
    await asyncio.to_thread(time.sleep, 1)
""", 0),
    ("嵌套 worker + to_thread（正常）", """
import asyncio, urllib.request
async def f():
    def _get():
        return urllib.request.urlopen("http://x", timeout=5).read()
    return await asyncio.to_thread(_get)
""", 0),
    ("同步函数里 sleep（正常）", """
import time
def f():
    time.sleep(1)
""", 0),
    ("async 里 urlopen", """
import urllib.request
async def f():
    return urllib.request.urlopen("http://x", timeout=5).read()
""", 1),
    ("async 里 subprocess.run", """
import subprocess
async def f():
    subprocess.run(["x"])
""", 1),
    ("run_in_executor 包裹（正常）", """
import time
async def f(loop):
    return await loop.run_in_executor(None, time.sleep, 1)
""", 0),
]


def main() -> int:
    bad = 0
    d = tempfile.mkdtemp()
    for i, (name, code, want) in enumerate(CASES):
        p = os.path.join(d, f"case{i}.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(code.lstrip())
        hits = C.scan(p)
        ok = len(hits) == want
        print(f"{'✓' if ok else '✗'} {name}: 期望 {want} 处，实际 {len(hits)} 处")
        if not ok:
            bad += 1
            for h in hits:
                print("   ", h)
    print(f"\n{'全部通过 ✅' if not bad else f'❌ {bad} 项未通过'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
