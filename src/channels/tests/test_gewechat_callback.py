"""Gewechat 回调边界 + 回复发送单测（不连真实网关）。

锁定的行为（2026-10-18 修复）：
  * 回调默认只听回环；非回环且没配 callback_token ⇒ **拒绝启动**（fail-closed），
    避免局域网任意主机伪造微信消息投毒给 AI；
  * 配了 token ⇒ 校验 ?token= / X-GEWECHAT-TOKEN / Authorization: Bearer，错的一律 401
    且**不得入队**；
  * AI 决策回发真的把文本发出去——原实现 `await self._safe_post(...)` 等的是同步函数，
    `await None` 抛 TypeError 被事件总线吞掉，微信回复从来没发出去过；
  * 回发走线程池，网关慢/挂时不能把事件循环（TTS/对话）一起卡住。

运行：python src/channels/tests/test_gewechat_callback.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels.gewechat import GewechatChannel  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def build(host: str | None = None, port: int = 0, token: str = "") -> GewechatChannel:
    gw = SimpleNamespace(
        base_url="http://127.0.0.1:2531", token="",
        callback_port=port or _free_port(), callback_token=token,
        allow_users=[], block_users=[],
        media=SimpleNamespace(ingest_images=False, ingest_urls=False,
                              watch_videos=False, store_to_memory=False),
    )
    if host is not None:
        gw.callback_host = host
    return GewechatChannel(bus=None, dialogue=None, llm=None,
                           cfg_section=SimpleNamespace(gewechat=gw))


def post(port: int, body: dict, path: str = "/", headers: dict | None = None) -> int:
    """发一条回调，返回 HTTP 状态码（401 会抛 HTTPError，这里转成码）。"""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_default_host_is_loopback() -> None:
    ch = build()   # 不给 callback_host → 走代码默认值
    assert ch._callback_host == "127.0.0.1", f"默认必须只听回环，实际 {ch._callback_host}"
    print("✓ 缺省 callback_host = 127.0.0.1（原来写死 0.0.0.0）")


def test_non_loopback_without_token_refuses() -> None:
    ch = build("0.0.0.0", token="")
    ch._start_callback_server()
    assert ch._httpd is None, "非回环 + 无 token 必须拒绝启动（fail-closed）"
    print("✓ 非回环 + 无 token：拒绝启动")


async def test_loopback_accepts_without_token() -> None:
    port = _free_port()
    ch = build("127.0.0.1", port=port, token="")
    ch._loop = asyncio.get_running_loop()
    ch._start_callback_server()
    assert ch._httpd is not None, "回环监听应正常启动"
    try:
        code = post(port, {"data": {"fromWxid": "wxid_a", "content": "hi"}})
        await asyncio.sleep(0.15)
        assert code == 200, code
        assert not ch._queue.empty(), "回环 + 无 token 的消息应入队"
    finally:
        ch._httpd.shutdown()
        ch._httpd.server_close()
    print("✓ 回环 + 无 token：启动、200、入队")


async def test_token_enforced() -> None:
    port = _free_port()
    ch = build("0.0.0.0", port=port, token="s3cret")
    ch._loop = asyncio.get_running_loop()
    ch._start_callback_server()
    assert ch._httpd is not None, "配了 token 的非回环监听应能启动"
    body = {"data": {"fromWxid": "wxid_a", "content": "hi"}}
    try:
        assert post(port, body) == 401, "无 token 必须 401"
        assert post(port, body, path="/?token=wrong") == 401, "错 token 必须 401"
        await asyncio.sleep(0.1)
        assert ch._queue.empty(), "被拒绝的回调不得入队"
        assert post(port, body, path="/?token=s3cret") == 200, "query token 应放行"
        assert post(port, body, headers={"X-GEWECHAT-TOKEN": "s3cret"}) == 200, "头部 token 应放行"
        assert post(port, body, headers={"Authorization": "Bearer s3cret"}) == 200, "Bearer 应放行"
        await asyncio.sleep(0.15)
        assert ch._queue.qsize() == 3, f"三条合法回调应全部入队，实际 {ch._queue.qsize()}"
    finally:
        ch._httpd.shutdown()
        ch._httpd.server_close()
    print("✓ token：无/错 401 且不入队，query/头/Bearer 三种带法均放行")


def _open_declaring(port: int, n_bytes: int) -> socket.socket:
    """连上回调端口、只发请求头（声明 n_bytes 的 body），body 留着调用方自己补。"""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
              b"Content-Length: %d\r\n\r\n" % n_bytes)
    return s


def _recv_or_none(s: socket.socket, wait: float):
    """在 wait 秒内收一段；超时返回 None（表示服务端还没开口）。"""
    s.settimeout(wait)
    try:
        return s.recv(4096)
    except socket.timeout:
        # 等待超时=对端还没回 → 按没有数据处理
        return None


async def test_reject_drains_body_bounded() -> None:
    """拒绝回调前要先读掉请求体；但**必须封顶**（未认证对端不许占线程）。

    2026-10-20 实测：不读 body 就 close()，Windows 会给对端发 RST —— 256KB body
    20 次里 **8 次**客户端拿到 ConnectionResetError(10054) 而不是 401。危害不止
    门禁偶发假红（「微信回调边界」满负载时撞到过）：真实 Gewechat 侧同样分不清
    「鉴权失败」与「网络断了」，可能无限重投。

    判据用「等不等 body」而不是「拿没拿到 401」：RST 与响应谁先到是竞态的
    （同一份代码 20 次里 8 次 RST、12 次 401），拿它当断言就是看运气；
    「拒之前要不要先把 body 收完」是确定行为，没有竞态。

    两半都要守：
    ① 正常大小（几 KB～几百 KB）→ 先读完再拒（否则 RST）；
    ② 超大（> _REJECT_DRAIN_CAP）→ 立刻拒（否则声明个 8GB 再滴水就占住一个线程）。
    只守 ① 会把「无上限 read」写进去，那是给未认证对端开的 DoS 口子。
    """
    port = _free_port()
    ch = build("0.0.0.0", port=port, token="s3cret")
    ch._start_callback_server()
    assert ch._httpd is not None, "配了 token 的非回环监听应能启动"
    try:
        # ① 声明 64KB 但先不发 body：服务端应该等着（它要先读完再拒）
        s = _open_declaring(port, 64 * 1024)
        try:
            early = _recv_or_none(s, 1.0)
            assert early is None, (
                f"声明 64KB body 却不发，服务端立刻回了 {early!r} → 说明拒绝路径"
                f"没读请求体，对端会收到 RST 而不是 401")
            s.sendall(b"x" * (64 * 1024))          # 把 body 补上，服务端才能读完
            done = _recv_or_none(s, 5.0)
            assert done is not None and b"401" in done, (
                f"body 补齐后应当回 401，实际 {done!r}")
        finally:
            s.close()

        # ② 声明 8MB 却不发：必须立刻拒（封顶），不许陪着等
        s2 = _open_declaring(port, 8 * 1024 * 1024)
        try:
            big = _recv_or_none(s2, 1.0)
            assert big is not None and b"401" in big, (
                f"声明 8MB body 却不发：必须立刻 401（封顶，别等），实际 {big!r}")
        finally:
            s2.close()
    finally:
        ch._httpd.shutdown()
        ch._httpd.server_close()
    print("✓ 拒绝路径：先读 body 再拒（≤256KB），超大 body 立刻拒（不占线程）")


async def test_decision_actually_sends() -> None:
    ch = build("127.0.0.1")
    sent: list[tuple] = []
    ch._client.post_text = lambda *a, **kw: sent.append(a) or {"ret": 0}   # type: ignore[assignment]
    ch._client.post_image = lambda *a, **kw: sent.append(("img",) + a) or {"ret": 0}  # type: ignore[assignment]
    ev = SimpleNamespace(data={"channel": "wechat", "text": "在的",
                               "reply_target": {"to_wxid": "wxid_a"},
                               "media": [{"type": "image", "url": "http://x/a.jpg"}]})
    await ch._on_decision(ev)
    assert sent, "AI 决策回发必须真的调用 post_text（原实现 await None 抛 TypeError，一条都发不出去）"
    assert sent[0] == ("wxid_a", "在的"), sent
    assert any(s[0] == "img" for s in sent), "媒体也要发出去"
    print("✓ 决策回发：文本与图片都真的发出去了")


async def test_send_does_not_block_loop() -> None:
    ch = build("127.0.0.1")

    def slow_post(*a, **kw):
        time.sleep(0.4)      # 模拟网关慢（真实超时 15s）
        return {"ret": 0}

    ch._client.post_text = slow_post  # type: ignore[assignment]
    ev = SimpleNamespace(data={"channel": "wechat", "text": "hi",
                               "reply_target": {"to_wxid": "wxid_a"}})
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
    await ch._on_decision(ev)
    stop.set()
    await t
    worst = max(gaps)
    assert worst < 0.15, f"事件循环被同步 HTTP 卡住 {worst * 1000:.0f}ms（应走 to_thread）"
    print(f"✓ 回发不阻塞事件循环（最大抖动 {worst * 1000:.0f}ms）")


async def main() -> None:
    test_default_host_is_loopback()
    test_non_loopback_without_token_refuses()
    await test_loopback_accepts_without_token()
    await test_token_enforced()
    await test_reject_drains_body_bounded()
    await test_decision_actually_sends()
    await test_send_does_not_block_loop()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
