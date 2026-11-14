"""tools/check_async_blocking.py 的自测（正例 + 反例）。

闸门如果只会打印不会判红，比没有更危险（本项目吃过"文本编码闸门永远 exit 0"的亏）。

覆盖：
  * 正例：async 里 time.sleep / urllib.urlopen / subprocess.run → 必须报
  * 反例：to_thread 包裹、嵌套同步 worker（交给 to_thread）、同步函数里的 sleep → 不许报

运行：python src/tests/test_check_async_blocking.py
"""
from __future__ import annotations

import contextlib
import io
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
    # 【2026-11-09 加·这条是「真阻塞被豁免」的反例】实参在**事件循环里先求值**：
    # `urllib.request.urlopen(...)` 已经把循环卡住了，`to_thread` 拿到的是结果。
    # 原实现的 `WRAPPERS` 豁免把这种写法标成 safe，实测报 0 处 —— 而它正是
    # 2026-10-18 `heavy_apps.pull_cloud` 里 `urlopen(timeout=5)` 卡首启 5s 的形状。
    # 规范用法 `to_thread(time.sleep, 1)` 传的是函数引用，压根没有 Call 节点，
    # 所以这条正例不会把规范用法误伤。
    ("to_thread 实参里的同步调用（真阻塞，必须报）", """
import asyncio, urllib.request
async def f():
    return await asyncio.to_thread(urllib.request.urlopen("http://x").read())
""", 1),
]


def _exit_code_cases() -> int:
    """`main()` 的退出码：有可疑点必须 1，干净必须 0。

    【2026-11-09 加·自测从来没碰过 `main()`】上面七条**全部只调 `C.scan()`**。
    而 `C.main()` 末尾是 `return 1 if all_hits else 0` —— 把这一行改成 `return 0`，
    闸门就退化成「只打印不判红」：可疑点照样打印出来，但全量里永远绿，
    没有任何人会去看那几行。**本文件照样打印「全部通过 ✅」并 exit 0**
    （2026-11-09 实测，不是推测）。

    讽刺的是本文件第 3 行自己写着「闸门如果只会打印不会判红，比没有更危险
    （本项目吃过"文本编码闸门永远 exit 0"的亏）」—— 这句话守住了 `scan()`，
    却没守住 `main()`。判据必须落在真正决定退出码的那个函数上。
    """
    bad = 0
    cases = [
        ("有可疑点 → 1", "import time\nasync def f():\n    time.sleep(1)\n", 1),
        ("干净 → 0", "import time\ndef f():\n    time.sleep(1)\n", 0),
    ]
    for name, code, want in cases:
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as f:
            f.write(code)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main([d])
        ok = rc == want
        print(f"{'✓' if ok else '✗'} main() 退出码 / {name}：期望 {want}，实际 {rc}")
        if not ok:
            bad += 1
    return bad


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
    bad += _exit_code_cases()
    print(f"\n{'全部通过 ✅' if not bad else f'❌ {bad} 项未通过'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
