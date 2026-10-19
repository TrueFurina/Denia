"""queue_util 的回归测试：有界队列「丢最旧」语义。

这几条语义是音频/IM 链路的正确性前提，改错了表现是「最新一句话永远进不来」
或「回调线程被阻塞导致音频卡死」，都不容易在日志里看出来。

运行：.venv/Scripts/python.exe src/tests/test_queue_util.py
"""
from __future__ import annotations

import asyncio
import queue
import sys
import threading

sys.path.insert(0, __file__.rsplit("src", 1)[0])

from src.queue_util import put_drop_oldest, put_drop_oldest_async  # noqa: E402

_fail = 0


def check(name: str, cond: bool) -> None:
    global _fail
    print(f"  {'✓' if cond else '✗'} {name}")
    if not cond:
        _fail += 1


def test_queue_basic() -> None:
    print("\n[queue.Queue]")
    q: queue.Queue[int] = queue.Queue(maxsize=3)
    dropped = [put_drop_oldest(q, i) for i in range(3)]
    check("未满时不丢弃", dropped == [False, False, False])
    check("顺序保持", list(q.queue) == [0, 1, 2])

    check("第 4 条触发丢弃", put_drop_oldest(q, 3) is True)
    check("丢的是最旧（0 消失，3 在队尾）", list(q.queue) == [1, 2, 3])

    # 连续投递超过容量：只保留最新 N 条
    for i in range(10):
        put_drop_oldest(q, i)
    check("超量投递后保留最新 3 条", list(q.queue) == [7, 8, 9])

    # on_drop 回调
    hits = []
    q2: queue.Queue[int] = queue.Queue(maxsize=1)
    put_drop_oldest(q2, 1)
    put_drop_oldest(q2, 2, on_drop=lambda: hits.append(1))
    check("on_drop 被调用一次", hits == [1])

    # 回调抛异常不能影响投递
    q3: queue.Queue[int] = queue.Queue(maxsize=1)
    put_drop_oldest(q3, 1)

    def _boom() -> None:
        raise RuntimeError("boom")

    ok = put_drop_oldest(q3, 2, on_drop=_boom)
    check("on_drop 抛异常仍完成投递", ok is True and list(q3.queue) == [2])


def test_queue_consumer() -> None:
    print("\n[消费侧配合]")
    q: queue.Queue[int] = queue.Queue(maxsize=2)
    for i in range(5):
        put_drop_oldest(q, i)
    check("消费端拿到的都是新数据", [q.get_nowait() for _ in range(2)] == [3, 4])
    check("消费后可继续投递", put_drop_oldest(q, 99) is False)


def test_queue_threads() -> None:
    print("\n[多线程投递不抛异常]")
    q: queue.Queue[int] = queue.Queue(maxsize=8)
    errs: list[BaseException] = []

    def worker(base: int) -> None:
        try:
            for i in range(500):
                put_drop_oldest(q, base + i)
        except BaseException as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(i * 1000,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("4 线程 × 500 次投递无异常", not errs)
    check("队列不超容量", q.qsize() <= 8)


def test_async() -> None:
    print("\n[asyncio.Queue]")

    async def _run() -> None:
        q: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
        check("未满不丢弃", put_drop_oldest_async(q, 1) is False)
        put_drop_oldest_async(q, 2)
        check("满则丢最旧", put_drop_oldest_async(q, 3) is True)
        check("保留最新 2 条", [q.get_nowait(), q.get_nowait()] == [2, 3])

    asyncio.run(_run())


def main() -> int:
    test_queue_basic()
    test_queue_consumer()
    test_queue_threads()
    test_async()
    print()
    if _fail:
        print(f"✗ {_fail} 项失败")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
