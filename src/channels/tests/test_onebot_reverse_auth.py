"""OneBot reverse 模式入站鉴权单测（不建真实连接）。

锁定的行为（2026-10-17 修复）：
  * 默认监听回环，未配 token 时只允许本机连入；
  * 监听非回环（0.0.0.0 等）且未配 token ⇒ 一律拒绝，避免局域网任意主机投递伪造 QQ 消息；
  * 配了 token ⇒ 校验 Authorization: Bearer 或 ?access_token=，用常时比较；
  * 被拒绝的连接必须 close(1008)，不能只是"不处理消息"。

运行：python src/channels/tests/test_onebot_reverse_auth.py
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


class FakeHeaders(dict):
    """模拟 websockets 的 Headers（这里只需要 get/items）。"""


class FakeRequest:
    def __init__(self, headers: dict | None = None, path: str = "/") -> None:
        self.headers = FakeHeaders(headers or {})
        self.path = path


class FakeWS:
    def __init__(self, headers: dict | None = None, path: str = "/", host: str = "10.0.0.9") -> None:
        self.request = FakeRequest(headers, path)
        self.remote_address = (host, 54321)
        self.closed: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


def build(listen_host: str, token: str = "") -> OneBotChannel:
    ob = SimpleNamespace(
        mode="reverse",
        listen_host=listen_host,
        listen_port=3002,
        access_token=token,
    )
    return OneBotChannel(bus=None, dialogue=None, llm=None, cfg_section=SimpleNamespace(onebot=ob))


def test_loopback_without_token() -> None:
    ch = build("127.0.0.1")
    assert ch._inbound_authorized(FakeWS()), "回环监听、无 token 的本机连入应放行"
    print("✓ 回环 + 无 token：放行")


def test_public_bind_without_token_denied() -> None:
    for host in ("0.0.0.0", "::", "192.168.1.20"):
        ch = build(host)
        assert not ch._inbound_authorized(FakeWS()), f"{host} + 无 token 必须拒绝"
    print("✓ 非回环 + 无 token：一律拒绝（旧实现会全放行）")


def test_token_bearer() -> None:
    ch = build("0.0.0.0", token="s3cret-token")
    assert ch._inbound_authorized(FakeWS({"Authorization": "Bearer s3cret-token"})), "正确 Bearer 应放行"
    assert not ch._inbound_authorized(FakeWS({"Authorization": "Bearer wrong"})), "错误 Bearer 必须拒绝"
    # HTTP 规范里 auth-scheme 大小写不敏感，"bearer" 应等价于 "Bearer"
    assert ch._inbound_authorized(FakeWS({"authorization": "bearer s3cret-token"})), "scheme 大小写不敏感，应放行"
    # 小写头名 + 正确 scheme（dict 不敏感匹配的兜底路径）
    assert ch._inbound_authorized(FakeWS({"authorization": "Bearer s3cret-token"})), "小写头名应被兜底识别"
    print("✓ token + Authorization: Bearer 校验正确")


def test_token_query() -> None:
    ch = build("0.0.0.0", token="s3cret-token")
    assert ch._inbound_authorized(FakeWS(path="/?access_token=s3cret-token")), "query token 应放行"
    assert not ch._inbound_authorized(FakeWS(path="/?access_token=nope")), "错误 query token 必须拒绝"
    assert not ch._inbound_authorized(FakeWS(path="/")), "无凭据必须拒绝"
    print("✓ token + ?access_token= 校验正确")


def test_rejected_connection_is_closed() -> None:
    ch = build("0.0.0.0")
    ws = FakeWS()
    asyncio.run(ch._on_reverse_conn(ws))
    assert ws.closed is not None, "被拒绝的连接必须 close"
    assert ws.closed[0] == 1008, f"应使用 1008 policy violation，实际 {ws.closed}"
    assert ws not in ch._conns, "被拒绝的连接不得进入活动连接集合"
    print("✓ 非法连入被 close(1008) 且未登记")


def test_authorized_connection_registered() -> None:
    ch = build("127.0.0.1")

    class IdleWS(FakeWS):
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    ws = IdleWS()
    asyncio.run(ch._on_reverse_conn(ws))
    assert ws.closed is None, "合法连入不应被关闭"
    print("✓ 合法连入正常进入消息循环")


def main() -> None:
    test_loopback_without_token()
    test_public_bind_without_token_denied()
    test_token_bearer()
    test_token_query()
    test_rejected_connection_is_closed()
    test_authorized_connection_registered()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
