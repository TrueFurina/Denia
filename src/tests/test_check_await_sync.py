"""tools/check_await_sync.py 的自测（正例 + 反例，防止闸门变成装饰品）。

为什么要给闸门写测试：本项目已经吃过"检查脚本永远 exit 0"的亏（文本编码闸门）。
一个只会打印、不会染红的检查器，比没有检查器更危险——它给人"已经查过了"的错觉。

覆盖：
  * 规则1 正例：`await self.sync_method()` → 必须报
  * 规则1 反例：`await self.async_method()` → 不许报
  * 规则2 正例：`self.async_method()`（没 await/调度）→ 必须报
  * 规则2 反例：await / create_task / asyncio.run / async for 生成器 → 不许报

运行：python src/tests/test_check_await_sync.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import check_await_sync as C  # noqa: E402

CASES: list[tuple[str, str, int]] = [
    ("await 同步方法", """
class A:
    def sync_work(self):
        return 1
    async def go(self):
        await self.sync_work()
""", 1),
    ("await 异步方法（正常）", """
class A:
    async def work(self):
        return 1
    async def go(self):
        await self.work()
""", 0),
    ("async 没调度", """
class A:
    async def work(self):
        return 1
    def go(self):
        self.work()
""", 1),
    ("create_task 调度（正常）", """
import asyncio
class A:
    async def work(self):
        return 1
    def go(self):
        asyncio.create_task(self.work())
""", 0),
    ("asyncio.run 消费（正常）", """
import asyncio
class A:
    async def work(self):
        return 1
    def go(self):
        return asyncio.run(self.work())
""", 0),
    ("异步生成器 async for（正常）", """
class A:
    async def gen(self):
        yield 1
    async def go(self):
        async for x in self.gen():
            pass
""", 0),
    ("gather 消费（正常）", """
import asyncio
class A:
    async def work(self):
        return 1
    async def go(self):
        await asyncio.gather(self.work(), self.work())
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
