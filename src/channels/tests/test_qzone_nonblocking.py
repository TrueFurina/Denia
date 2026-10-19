"""QQ空间发布器：发说说不能卡住事件循环（与 gewechat 回发同一个坑）。

`QzoneClient.publish` 是同步 urllib（超时 15s）。原来 `_on_decision` 直接调它，
事件循环线程被占住 → TTS、对话、桌宠动画一起停，最长 15 秒。
本测试把 publish 换成 sleep 0.4s 的桩，同时跑 10ms 心跳，量最大抖动。

运行：python src/channels/tests/test_qzone_nonblocking.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels.qzone import QzonePublisher  # noqa: E402


async def main() -> None:
    cfg = SimpleNamespace(cookie="p_skey=abc; o_uin=12345", uin="12345")
    pub = QzonePublisher(bus=None, dialogue=None, llm=None, cfg_section=cfg)

    called: list[str] = []

    def slow_publish(text: str) -> dict:
        called.append(text)
        time.sleep(0.4)          # 模拟空间接口慢（真实超时 15s）
        return {"code": 0}

    pub._client.publish = slow_publish  # type: ignore[assignment]

    ev = SimpleNamespace(data={"channel": "qzone", "text": "今天天气真好"})
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    await pub._on_decision(ev)
    stop.set()
    await t

    worst = max(gaps)
    assert called == ["今天天气真好"], f"发说说没被调用：{called}"
    assert worst < 0.15, f"事件循环被同步 HTTP 卡住 {worst * 1000:.0f}ms（应走 to_thread）"
    print(f"✓ 发说说真的调用了，且不阻塞事件循环（最大抖动 {worst * 1000:.0f}ms）")
    print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
