"""OneBot 通道单测（无 QQ / 无 LLM / 无 websockets 真实连接）。

验证：
  1) OneBot 消息解析（CQ 图片 + 链接 + 纯文本）
  2) 私聊消息 → 喂大脑 → 带 channel="qq" 的 AI_DECISION → 回发 send_msg 到正确 peer
  3) 桌面决策(channel="")被本通道忽略；其它通道决策被忽略
  4) 群消息仅 @ 才回复；黑名单被忽略
  5) 媒体摄入（ingest_images/urls=false 时纯文本路径不触网）

运行：python src/channels/tests/test_onebot.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

# 把仓库根加入 path，使 src.* 相对导入可用
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels.media_ingest import parse_onebot_message  # noqa: E402
from src.channels.onebot import OneBotChannel  # noqa: E402
from src.event_bus import Event, EventType  # noqa: E402


class FakeBus:
    def __init__(self) -> None:
        self._h: dict = {}

    def subscribe(self, et, h) -> None:
        self._h.setdefault(et, []).append(h)

    async def publish(self, ev: Event) -> None:
        for h in list(self._h.get(ev.type, [])):
            await h(ev)


class FakeLLM:
    def vision_available(self) -> bool:
        return False

    async def vision_describe(self, image_bytes, prompt) -> str:  # noqa: D401
        return ""


class FakeDialogue:
    """模拟大脑：收到文本立即回一条带 channel/reply_target 的 AI_DECISION。"""

    def __init__(self, bus: FakeBus) -> None:
        self._bus = bus
        self.last_call = None

    async def respond(self, text, source="", channel="", reply_target=None) -> None:
        self.last_call = (text, source, channel, reply_target)
        await self._bus.publish(Event(EventType.AI_DECISION, {
            "channel": channel,
            "reply_target": reply_target,
            "text": f"回复：{text}",
            "failed": False,
        }, "dialogue"))


def make_cfg(**overrides) -> SimpleNamespace:
    media = SimpleNamespace(
        ingest_images=overrides.pop("ingest_images", False),
        ingest_urls=overrides.pop("ingest_urls", False),
        store_to_memory=overrides.pop("store_to_memory", False),
    )
    onebot = SimpleNamespace(
        mode=overrides.pop("mode", "forward"),
        ws_url=overrides.pop("ws_url", "ws://127.0.0.1:3001"),
        listen_host="0.0.0.0",
        listen_port=3002,
        access_token="",
        http_url="http://127.0.0.1:3000",
        reply_in_group=overrides.pop("reply_in_group", True),
        allow_users=overrides.pop("allow_users", []),
        block_users=overrides.pop("block_users", []),
        allow_groups=overrides.pop("allow_groups", []),
        media=media,
    )
    return SimpleNamespace(enabled=True, onebot=onebot)


async def build_channel(cfg, bus, dialogue) -> OneBotChannel:
    ch = OneBotChannel(bus=bus, dialogue=dialogue, llm=FakeLLM(), cfg_section=cfg)
    captures: list = []
    async def fake_send(action, params):
        captures.append((action, params))
    ch._send_action = fake_send  # 拦截真实发送，改为记录
    bus.subscribe(EventType.AI_DECISION, ch._on_decision)
    ch._captures = captures
    return ch


async def test_parse():
    text, media = parse_onebot_message(
        "你好[CQ:image,file=base64://AAAA,url=http://x/y.jpg] 看这张图 http://example.com/a")
    assert "你好" in text, text
    assert "http://example.com/a" in text, text
    imgs = [m for m in media if m["type"] == "image"]
    assert imgs and imgs[0]["file"].startswith("base64://"), media
    print("✓ parse_onebot_message: 文本/图片/链接拆分正确")


async def test_private_roundtrip():
    bus, dialogue = FakeBus(), FakeDialogue(FakeBus())
    bus2 = bus
    dialogue._bus = bus2
    cfg = make_cfg()
    ch = await build_channel(cfg, bus2, dialogue)
    data = {
        "post_type": "message", "message_type": "private",
        "user_id": 12345, "self_id": 999,
        "message": "在吗",
    }
    await ch._on_message_event(data)
    # 大脑应被喂入
    assert dialogue.last_call is not None, "大脑未被喂入"
    assert dialogue.last_call[2] == "qq", dialogue.last_call
    assert dialogue.last_call[3]["user_id"] == 12345
    # 应产生一条 send_msg 回发
    assert ch._captures, "未产生回发"
    action, params = ch._captures[0]
    assert action == "send_msg", action
    assert params["message_type"] == "private"
    assert params["user_id"] == 12345
    assert params["message"].startswith("回复："), params
    print("✓ 私聊消息 → 大脑 → QQ 回发 路由正确")


async def test_channel_isolation():
    bus = FakeBus()
    dialogue = FakeDialogue(bus)
    cfg = make_cfg()
    ch = await build_channel(cfg, bus, dialogue)
    # 桌面决策（channel=""）不应触发 QQ 发送
    await bus.publish(Event(EventType.AI_DECISION, {
        "channel": "", "reply_target": {"message_type": "private", "user_id": 1},
        "text": "桌面说的话", "failed": False}, "dialogue"))
    assert not ch._captures, "桌面决策被误发到 QQ"
    # 其它通道决策被忽略
    await bus.publish(Event(EventType.AI_DECISION, {
        "channel": "wechat", "reply_target": {"message_type": "private", "user_id": 1},
        "text": "微信的话", "failed": False}, "dialogue"))
    assert not ch._captures, "其它通道决策被误发到 QQ"
    # 失败气泡不发送
    await bus.publish(Event(EventType.AI_DECISION, {
        "channel": "qq", "reply_target": {"message_type": "private", "user_id": 1},
        "text": "x", "failed": True}, "dialogue"))
    assert not ch._captures, "失败气泡被发到 QQ"
    print("✓ 通道隔离：桌面/其它通道/失败气泡均不触达 QQ")


async def test_group_at_only():
    bus = FakeBus()
    dialogue = FakeDialogue(bus)
    cfg = make_cfg(reply_in_group=True)
    ch = await build_channel(cfg, bus, dialogue)
    # 群消息未 @ 本机器人 → 忽略
    await ch._on_message_event({"post_type": "message", "message_type": "group",
                                "group_id": 555, "user_id": 222, "self_id": 999,
                                "message": "大家好"})
    assert dialogue.last_call is None, "未 @ 的群消息不应触发回复"
    # 群消息 @ 本机器人 → 回复
    await ch._on_message_event({"post_type": "message", "message_type": "group",
                                "group_id": 555, "user_id": 222, "self_id": 999,
                                "message": "[CQ:at,qq=999] 你好"})
    assert dialogue.last_call is not None, "已 @ 的群消息应触发回复"
    assert ch._captures[0][1]["group_id"] == 555
    print("✓ 群消息仅 @ 本机器人时回复，且回复发往正确群")


async def test_blocklist():
    bus = FakeBus()
    dialogue = FakeDialogue(bus)
    cfg = make_cfg(block_users=[222])
    ch = await build_channel(cfg, bus, dialogue)
    await ch._on_message_event({"post_type": "message", "message_type": "private",
                                "user_id": 222, "self_id": 999, "message": "hi"})
    assert dialogue.last_call is None, "黑名单用户不应触发回复"
    print("✓ 黑名单用户被忽略")


async def main() -> None:
    await test_parse()
    await test_private_roundtrip()
    await test_channel_isolation()
    await test_group_at_only()
    await test_blocklist()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
