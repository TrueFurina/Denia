"""媒体/空间 路由单测（无真实网络）。

验证：
  - OneBot 通道：带 media 的 AI_DECISION → 文本 + 图片(CQ) + 视频(CQ) 各一条 send_msg
  - QzonePublisher：仅 channel=qzone 的决策才发说说，其它通道忽略
运行：python src/channels/tests/test_media_routing.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels.onebot import OneBotChannel  # noqa: E402
from src.channels.qzone import QzonePublisher  # noqa: E402
from src.event_bus import Event, EventType  # noqa: E402


class FakeBus:
    def subscribe(self, et, h) -> None:
        self._h = getattr(self, "_h", {})
        self._h.setdefault(et, []).append(h)


class FakeLLM:
    pass


class FakeDialogue:
    async def respond(self, *a, **k) -> None:  # noqa: D401
        pass


def make_onebot_cfg() -> SimpleNamespace:
    media = SimpleNamespace(ingest_images=False, ingest_urls=False, store_to_memory=False)
    ob = SimpleNamespace(
        mode="forward", ws_url="ws://x", listen_host="0.0.0.0", listen_port=3002,
        access_token="", http_url="http://x", reply_in_group=True, allow_users=[],
        block_users=[], allow_groups=[], media=media,
    )
    return SimpleNamespace(enabled=True, onebot=ob)


async def test_onebot_media() -> None:
    bus = FakeBus()
    ch = OneBotChannel(bus=bus, dialogue=FakeDialogue(), llm=FakeLLM(), cfg_section=make_onebot_cfg())
    caps: list = []
    async def fake_send(action, params):
        caps.append((action, params))
    ch._send_action = fake_send
    await ch._on_decision(Event(EventType.AI_DECISION, {
        "channel": "qq", "failed": False,
        "reply_target": {"message_type": "private", "user_id": 777},
        "text": "看这张图",
        "media": [{"type": "image", "file": "http://img/x.jpg"},
                  {"type": "video", "file": "http://v/x.mp4"}],
    }, "dialogue"))
    assert len(caps) == 3, caps
    kinds = [p["message"] for _, p in caps]
    assert kinds[0] == "看这张图"
    assert kinds[1].startswith("[CQ:image,file=http://img/x.jpg]"), kinds[1]
    assert kinds[2].startswith("[CQ:video,file=http://v/x.mp4]"), kinds[2]
    assert all(a == "send_msg" for a, _ in caps)
    print("✓ OneBot 媒体决策：文本+图片+视频 分三条 CQ 发回正确")


async def test_qzone_publish_route() -> None:
    cfg = SimpleNamespace(enabled=True, cookie="p_skey=abc; o_uin=1", uin="1")
    ch = QzonePublisher(bus=FakeBus(), dialogue=FakeDialogue(), llm=FakeLLM(), cfg_section=cfg)
    published: list = []

    class MockClient:
        def publish(self, text):
            published.append(text)
            return {"code": 0}

    ch._client = MockClient()
    await ch._on_decision(Event(EventType.AI_DECISION, {
        "channel": "qzone", "failed": False, "reply_target": None,
        "text": "今天心情不错~"}, "dialogue"))
    assert published == ["今天心情不错~"], published
    await ch._on_decision(Event(EventType.AI_DECISION, {
        "channel": "qq", "failed": False, "text": "x"}, "dialogue"))
    assert len(published) == 1, published
    print("✓ QzonePublisher：仅 channel=qzone 发说说，其它通道忽略")


async def main() -> None:
    await test_onebot_media()
    await test_qzone_publish_route()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
