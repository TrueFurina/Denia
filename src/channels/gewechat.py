"""Gewechat 微信通道（G 脚手架）：通过 Gewechat 网关把微信个人号接入同一个 AI 大脑。

Gewechat 是一个 Docker 服务，对外暴露 REST API（默认 http://<host>:2531/v2/api/<接口>），
所有请求带 X-GEWE-TOKEN 头。登录走 getQr→checkQr 扫码；发消息 postText/postImage/postVideo；
收消息靠在 Gewechat 后台配置的回调 URL（本通道起一个 HTTP 回调服务接收）。

⚠️ 微信个人号无官方 API，Gewechat 属逆向灰色方案，有封号风险——务必用微信小号，且同省部署更稳。
依赖：无新增三方包（urllib + 标准库 http.server 起回调）。
接口具体路径以你部署的 Gewechat 版本为准（本文按社区常见命名，联调时核对）。
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
import urllib.parse
import urllib.request
from typing import Any, Optional

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except Exception:  # noqa: BLE001
    ThreadingHTTPServer = None  # type: ignore[assignment]

from ..event_bus import Event, EventType
from ..queue_util import put_drop_oldest_async
from .base import Channel
from . import media_ingest as media

logger = logging.getLogger("channels.gewechat")

# 入站消息队列上限（条）。群聊刷屏时先丢最旧，保证最新消息仍会被处理。
_INBOX_MAX = 200

# 回环主机名白名单：只有这些值才算"不对外暴露"。
# Gewechat 回调是**无签名**的裸 JSON POST，绑 0.0.0.0 等于让局域网任意主机
# 伪造一条"微信消息"——AI 会当成真人说的去回复、甚至调工具。
# 与 OneBot(reverse) 的边界同一套标准（见 src/channels/onebot.py:32）。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# 拒绝一条回调前最多读掉多少字节的请求体（见 do_POST 里的注释）。
# 目的：读完 body 再回 401，避免 close() 时残留未读数据 → Windows 发 RST →
# 对端拿到 ConnectionResetError 而不是 401（实测 256KB 时 20 次里 8 次）。
# 封顶的原因：未认证对端可以用「声明超大 Content-Length 再滴水」占住一个线程。
# 正常 Gewechat 回调是几 KB 的 JSON，256KB 已经宽裕。
_REJECT_DRAIN_CAP = 256 * 1024


class GeweClient:
    """Gewechat REST 客户端（纯逻辑，可离线 mock）。"""

    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.app_id: Optional[str] = None

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            h["X-GEWE-TOKEN"] = self.token
        return h

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v2/api/{path.lstrip('/')}"

    def _post(self, path: str, params: dict) -> dict:
        url = self._url(path)
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)

    # ---- 登录 ----
    def get_token(self) -> str:
        data = self._post("login/GetToken", {})
        tok = (data.get("data") or {}).get("token") or data.get("token") or ""
        if tok:
            self.token = tok
        return tok

    def get_qr(self, app_id: str = "") -> dict:
        return self._post("login/GetQr", {"appId": app_id})

    def check_qr(self, app_id: str, uuid: str, captch_code: str = "") -> dict:
        return self._post("login/CheckQr", {"appId": app_id, "uuid": uuid, "captchCode": captch_code})

    # ---- 发消息 ----
    def post_text(self, to_wxid: str, content: str, ats: str = "") -> dict:
        return self._post("message/PostText", {"appId": self.app_id, "toWxid": to_wxid, "content": content, "ats": ats})

    def post_image(self, to_wxid: str, img_url: str) -> dict:
        return self._post("message/PostImage", {"appId": self.app_id, "toWxid": to_wxid, "imgUrl": img_url})

    def post_video(self, to_wxid: str, video_url: str, thumb_url: str = "", duration: int = 0) -> dict:
        return self._post("message/PostVideo", {"appId": self.app_id, "toWxid": to_wxid, "videoUrl": video_url, "thumbUrl": thumb_url, "duration": duration})


class GewechatChannel(Channel):
    """微信通道：把微信个人号消息喂给大脑，并把大脑决策发回微信。"""

    name = "wechat"

    def __init__(self, bus, dialogue, llm, cfg_section) -> None:
        super().__init__(bus, dialogue, llm, cfg_section)
        gw = getattr(cfg_section, "gewechat", None)
        if gw is None:
            raise ValueError("config.channels.wechat.gewechat 缺失")
        self._client = GeweClient(
            getattr(gw, "base_url", "") or "http://127.0.0.1:2531",
            getattr(gw, "token", "") or "",
        )
        self._callback_host = getattr(gw, "callback_host", "") or "127.0.0.1"
        self._callback_port = int(getattr(gw, "callback_port", 9912) or 9912)
        # 回调共享令牌：Gewechat 的回调没有签名机制，只能靠 URL 里的共享密钥
        # （后台回调地址写 http://<host>:<port>/?token=xxx）。空 = 仅允许回环监听。
        self._callback_token = str(getattr(gw, "callback_token", "") or "")
        self._allow_users = [str(x) for x in (getattr(gw, "allow_users", []) or [])]
        self._block_users = [str(x) for x in (getattr(gw, "block_users", []) or [])]
        mcfg = getattr(gw, "media", None)
        self._ingest_images = bool(getattr(mcfg, "ingest_images", True)) if mcfg else True
        self._ingest_urls = bool(getattr(mcfg, "ingest_urls", True)) if mcfg else True
        self._watch_videos = bool(getattr(mcfg, "watch_videos", False)) if mcfg else True
        self._store_to_memory = bool(getattr(mcfg, "store_to_memory", True)) if mcfg else True
        # 入站消息上限：回调 HTTP 线程只管往队列塞，主循环每条要走一遍 AI 决策，
        # 高峰期（群聊刷屏）消费必然慢于生产。无界会一路涨；丢最旧可保证
        # 「最新的消息一定被处理」，旧的过期消息本来也没回复价值。
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=_INBOX_MAX)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._httpd = None

    # ---------------- 主循环 ----------------
    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
        if ThreadingHTTPServer is not None:
            self._start_callback_server()
        await self._ensure_login()
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # 队列等待超时=本轮没有待发消息，继续等（循环的正常心跳）
                continue
            await self._handle_inbound(item)

    def _authorized(self, handler) -> bool:
        """回调鉴权：配了 token 就必须带对；没配 token 只允许回环监听（启动时已拦）。

        token 走三处任一：`?token=` / `X-GEWECHAT-TOKEN` 头 / `Authorization: Bearer`。
        用 compare_digest 做定长比较，别给时序侧信道。
        """
        if not self._callback_token:
            return True
        tok = ""
        try:
            qs = urllib.parse.urlsplit(getattr(handler, "path", "") or "").query
            tok = (urllib.parse.parse_qs(qs).get("token") or [""])[0]
        except Exception:  # noqa: BLE001  路径解析失败 → 视为未带 token
            tok = ""
        if not tok:
            tok = handler.headers.get("X-GEWECHAT-TOKEN") or ""
        if not tok:
            auth = handler.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                tok = auth[7:].strip()
        return bool(tok) and hmac.compare_digest(tok, self._callback_token)

    def _start_callback_server(self) -> None:
        channel = self
        # fail-closed：非回环监听却没配令牌 = 把"AI 的耳朵"接到局域网上，拒绝启动。
        if self._callback_host not in _LOOPBACK_HOSTS and not self._callback_token:
            logger.error(
                "Gewechat 回调服务未启动：callback_host=%s（非回环）但 callback_token 为空。"
                "回调是无签名 POST，任何能连到本机 %s 端口的主机都能伪造微信消息。"
                "修法二选一：① 把 callback_host 改回 127.0.0.1（Gewechat 与本程序同机时够用）；"
                "② 配 callback_token，并在 Gewechat 后台把回调地址写成 "
                "http://<本机IP>:%s/?token=<同一个值>。",
                self._callback_host, self._callback_port, self._callback_port)
            return

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if not channel._authorized(self):
                    logger.warning("Gewechat 回调被拒绝（令牌不匹配）：来源 %s",
                                   self.client_address[0] if self.client_address else "?")
                    # 【2026-10-20 修复·拒绝时没读请求体 → 对端收到 RST 而不是 401】
                    # `BaseHTTPRequestHandler` 不替我们消费 body；直接回 401 再 close()，
                    # 发送缓冲里还压着没读的请求体，Windows 会发 RST。实测（256KB body，
                    # 20 次）：**12 次拿到 401、8 次拿到 ConnectionResetError(10054)**。
                    # 危害不止门禁偶发假红（「微信回调边界」满负载时撞到过）：真实 Gewechat
                    # 侧同样分不清「鉴权失败」和「网络断了」，可能无限重投。
                    # 修法：先把这个 body 读掉再回响应 → 优雅关闭，客户端稳定拿到 401。
                    # 但**必须封顶**：这条路径面对的是未认证的对端，无上限 read() 等于给它
                    # 一个「声明 Content-Length: 8GB 再慢慢滴水」就能占住一个线程的口子
                    # （ThreadingHTTPServer 每连接一线程）。超过上限就回 401 直接关，
                    # 那点 RST 是给恶意对端的，正常回调都是几 KB 的 JSON。
                    try:
                        _n = int(self.headers.get("Content-Length", 0) or 0)
                        if 0 < _n <= _REJECT_DRAIN_CAP:
                            self.rfile.read(_n)
                    except Exception:  # noqa: BLE001
                        pass
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error":"unauthorized"}')
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(n) if n else b""
                    data = json.loads(raw.decode("utf-8", "replace")) if raw else {}
                except Exception:  # noqa: BLE001
                    data = {}
                try:
                    if channel._loop is not None:
                        # 有界投递：满了丢最旧，且不阻塞回调线程
                        channel._loop.call_soon_threadsafe(
                            put_drop_oldest_async, channel._queue, data)
                except Exception:  # noqa: BLE001
                    pass
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a) -> None:  # 静默
                pass

        try:
            self._httpd = ThreadingHTTPServer((self._callback_host, self._callback_port), _Handler)
            threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
            logger.info("Gewechat 回调服务已启动 http://%s:%s（鉴权：%s）",
                        self._callback_host, self._callback_port,
                        "已开启 token" if self._callback_token else "仅回环")
        except Exception:  # noqa: BLE001
            logger.exception("Gewechat 回调服务启动失败（不影响登录/发消息）")

    async def _ensure_login(self) -> None:
        try:
            tok = await asyncio.to_thread(self._client.get_token)
            logger.info("Gewechat token 获取%s", "成功" if tok else "失败")
            qr = await asyncio.to_thread(self._client.get_qr, "")
            uuid = (qr.get("data") or {}).get("uuid") or ""
            logger.info("Gewechat 登录二维码已生成（请在 Gewechat 管理页扫码）；uuid=%s", uuid)
            # 轮询 checkQr 直到扫码成功（封顶 ~3 分钟，避免永久阻塞）；成功后缓存 appId。
            for _ in range(60):
                if not self._running:
                    break
                res = await asyncio.to_thread(self._client.check_qr, "", uuid)
                if (res.get("code") == 0 or (res.get("data") or {}).get("code") == 0) and (res.get("data") or {}).get("appId"):
                    self._client.app_id = (res.get("data") or {}).get("appId")
                    logger.info("Gewechat 登录成功 appId=%s", self._client.app_id)
                    return
                await asyncio.sleep(3)
            logger.warning("Gewechat 未在限定时间内扫码登录；回调收消息需先登录成功")
        except Exception:  # noqa: BLE001
            logger.exception("Gewechat 登录初始化失败（可能网关未启动或路径不符）")

    # ---------------- 入站消息 ----------------
    async def _handle_inbound(self, data: dict) -> None:
        # Gewechat 回调结构以部署版本为准；此处做防御式解析。
        payload = data.get("data", data) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            return
        from_wxid = payload.get("fromWxid") or payload.get("fromWxId") or ""
        content = payload.get("content") or payload.get("text") or ""
        msg_type = payload.get("msgType")
        if not from_wxid:
            return
        if self._block_users and str(from_wxid) in self._block_users:
            return
        if self._allow_users and str(from_wxid) not in self._allow_users:
            return

        # 媒体（图片/链接）摄入
        extra_ctx: list[str] = []
        try:
            extra_ctx = await media.ingest_media(
                self._llm, None, str(content), self._extract_media(payload),
                ingest_images=self._ingest_images, ingest_urls=self._ingest_urls,
                store_to_memory=self._store_to_memory,
                watch_videos=self._watch_videos,
            )
        except Exception:  # noqa: BLE001
            logger.exception("微信媒体摄入异常（继续按纯文本）")

        combined = str(content)
        if extra_ctx:
            combined = ((str(content) + "\n\n" + "\n".join(extra_ctx)).strip()
                        if content else "\n".join(extra_ctx))
        if not combined.strip():
            return
        reply_target = {"to_wxid": from_wxid, "msg_type": msg_type}
        try:
            await self._feed_brain(combined, reply_target, source="wechat")
        except Exception:  # noqa: BLE001
            logger.exception("把微信消息喂给大脑失败")

    @staticmethod
    def _extract_media(payload: dict) -> list[dict]:
        out: list[dict] = []
        for img in (payload.get("images") or []):
            if isinstance(img, dict) and img.get("url"):
                out.append({"type": "image", "url": img["url"]})
            elif isinstance(img, str):
                out.append({"type": "image", "url": img})
        return out

    # ---------------- 决策回发 ----------------
    async def _on_decision(self, event: Event) -> None:
        d = event.data or {}
        if d.get("channel") != self.name:
            return
        if d.get("failed"):
            return
        target = d.get("reply_target") or {}
        to_wxid = target.get("to_wxid")
        text = (d.get("text") or "").strip()
        if not to_wxid:
            return
        if text:
            await self._safe_post(self._client.post_text, to_wxid, text)
        for m in (d.get("media") or []):
            await self._send_media_item(to_wxid, m)

    async def _send_media_item(self, to_wxid: str, m: dict) -> None:
        f = m.get("file") or m.get("url") or ""
        if not f:
            return
        if m.get("type") == "video":
            await self._safe_post(self._client.post_video, to_wxid, f)
        else:
            await self._safe_post(self._client.post_image, to_wxid, f)

    async def _safe_post(self, fn, *args) -> None:
        """发消息一律走线程池。

        【2026-10-18 修】两处叠加缺陷：
        ① 原实现是同步 def，调用点却写着 `await self._safe_post(...)` →
           `await None` 抛 TypeError，被事件总线 catch 后只留一行日志，
           **微信回复从来没真正发出去过**（能收不能回，症状像"AI 不理人"）。
        ② 即便去掉 await，urllib 是同步阻塞调用（超时 15s）→ 在事件循环线程里
           直接发，网关慢/挂时整个桌宠一起卡死（TTS、对话、桌宠动画全停）。
        改为 async + asyncio.to_thread：语义正确，且阻塞落在工作线程。
        """
        try:
            await asyncio.to_thread(fn, *args)
        except Exception:  # noqa: BLE001
            logger.exception("Gewechat 发送失败")
