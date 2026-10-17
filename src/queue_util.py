"""有界队列的「丢最旧」投递器。

为什么需要
----------
麦克风回调、播放投递、IM 回调这些生产端**都不能阻塞**（PortAudio 回调里阻塞会
直接卡死音频线程），所以只能用 put_nowait。但 put_nowait 遇到无界队列永远不会
满 —— 消费端一旦卡住（模型推理、网络重连、GUI 线程忙），队列就会一直涨。

对实时流来说，积压的旧数据本来就没有价值：滞后的 30 秒麦克风音频对语音识别是
噪声，迟到的气泡文本也不该再弹。所以正确的策略是「有界 + 满则丢最旧」，
而不是「无界等着」或「满了丢新」——后者会让最新一句话永远进不来。

用法
----
    q = queue.Queue(maxsize=32)
    put_drop_oldest(q, frame)                      # 满了自动丢一条最旧的

    aq = asyncio.Queue(maxsize=200)
    put_drop_oldest_async(aq, msg)                 # 供 call_soon_threadsafe 用

回调里请务必用这两个函数，不要用 put()：put() 满时会阻塞，在音频回调里等于死锁。
"""
from __future__ import annotations

import asyncio
import queue
from typing import Any, Callable

__all__ = ["put_drop_oldest", "put_drop_oldest_async"]


def put_drop_oldest(q: "queue.Queue[Any]", item: Any,
                    *, on_drop: Callable[[], None] | None = None) -> bool:
    """向 queue.Queue 投递；满了就丢一条最旧的再投。返回是否发生过丢弃。

    循环而非单次重试：多个生产者并发时，get_nowait 拿到的空位可能立刻被别的
    线程抢走，需要继续尝试。理论上不会无限循环（每次丢弃都在腾位置）。
    """
    dropped = False
    while True:
        try:
            q.put_nowait(item)
            return dropped
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                # 队列被消费者清空但 put 仍失败（极窄竞态）：直接放弃本次投递
                return dropped
            dropped = True
            if on_drop is not None:
                try:
                    on_drop()
                except Exception:  # noqa: BLE001  统计回调不该影响投递
                    pass


def put_drop_oldest_async(q: "asyncio.Queue[Any]", item: Any,
                          *, on_drop: Callable[[], None] | None = None) -> bool:
    """asyncio.Queue 版本。只做非阻塞投递，供跨线程 call_soon_threadsafe 调度。"""
    dropped = False
    while True:
        try:
            q.put_nowait(item)
            return dropped
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            dropped = True
            if on_drop is not None:
                try:
                    on_drop()
                except Exception:  # noqa: BLE001
                    pass
