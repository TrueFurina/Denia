"""OneBot v11 通道（G 脚手架）：通过 NapCat 把 QQ 接入同一个 AI 大脑。

传输两种模式（config.channels.qq.onebot.mode）：
  - forward（默认）：本程序作为 WS 客户端，连接 NapCat 提供的正向 WS 地址
    （如 ws://127.0.0.1:3001）。自托管推荐。
  - reverse：本程序作为 WS 服务端监听 listen_host:listen_port，NapCat 反向连入
    （适合内网穿透 / 远端部署）。多连接时取第一个可用连接发 API。

发消息：优先走当前 WS 连接发送 OneBot action（send_msg）；
兜底走 NapCat HTTP API（http_url，默认 http://127.0.0.1:3000）。

依赖：websockets（项目已依赖，ws_server 同款）。不引入新三方包。
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Optional

from ..event_bus import Event, EventType
from .base import Channel
from . import media_ingest as media

logger = logging.getLogger("channels.onebot")

# 【2026-10-17 安全修复】reverse 模式原来默认监听 0.0.0.0 且完全不校验连入方——
# 同局域网任何人都能连上 WS 伪造 QQ 消息，而消息会进聊天上下文、进而写入长期记忆
# （等于可以往记忆库投毒）。现在：默认只听回环；非回环监听必须配 access_token，
# 否则一律拒绝并打明确日志。access_token 走 Authorization: Bearer 或 ?access_token=。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


class OneBotChannel(Channel):
    name = "qq"

    def __init__(self, bus, dialogue, llm, cfg_section) -> None:
        super().__init__(bus, dialogue, llm, cfg_section)
        ob = getattr(cfg_section, "onebot", None)
        if ob is None:
            raise ValueError("config.channels.qq.onebot 缺失")
        self._mode = (getattr(ob, "mode", "forward") or "forward").lower()
        self._ws_url = getattr(ob, "ws_url", "") or "ws://127.0.0.1:3001"
        self._listen_host = getattr(ob, "listen_host", "") or "127.0.0.1"
        self._listen_port = int(getattr(ob, "listen_port", 3002) or 3002)
        self._token = getattr(ob, "access_token", "") or ""
        self._http_url = getattr(ob, "http_url", "") or "http://127.0.0.1:3000"
        self._reply_in_group = bool(getattr(ob, "reply_in_group", True))
        self._allow_users = [str(x) for x in (getattr(ob, "allow_users", []) or [])]
        self._block_users = [str(x) for x in (getattr(ob, "block_users", []) or [])]
        self._allow_groups = [str(x) for x in (getattr(ob, "allow_groups", []) or [])]
        mcfg = getattr(ob, "media", None)
        self._ingest_images = bool(getattr(mcfg, "ingest_images", True)) if mcfg else True
        self._ingest_urls = bool(getattr(mcfg, "ingest_urls", True)) if mcfg else True
        self._watch_videos = bool(getattr(mcfg, "watch_videos", True)) if mcfg else True
        self._store_to_memory = bool(getattr(mcfg, "store_to_memory", True)) if mcfg else True

        self._conns: set = set()      # reverse 模式下活动的 NapCat 连接
        self._send_ws: Any = None     # forward 模式下发 API 用的 ws
        self._echo = 0

    # ---------------- 主循环 ----------------
    async def _run(self) -> None:
        self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
        logger.info("OneBot 通道启动（mode=%s）", self._mode)
        try:
            if self._mode == "reverse":
                await self._serve_reverse()
            else:
                await self._connect_forward()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("OneBot 通道主循环异常退出")
        finally:
            logger.info("OneBot 通道主循环结束")

    async def _connect_forward(self) -> None:
        # 延迟导入：websockets 仅在真正建立连接时才需要，避免包导入硬依赖缺失时崩溃
        from websockets.asyncio.client import connect as ws_connect
        while self._running:
            try:
                logger.info("OneBot(forward) 连接 %s", self._ws_url)
                async with ws_connect(self._ws_url, additional_headers=self._auth_headers()) as ws:
                    self._send_ws = ws
                    logger.info("OneBot(forward) 已连接")
                    async for raw in ws:
                        try:
                            await self._handle_raw(raw)
                        except Exception:  # noqa: BLE001
                            logger.exception("处理 OneBot 消息异常")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("OneBot(forward) 连接断开，5s 后重连: %s", self._ws_url)
                self._send_ws = None
                if self._running:
                    await asyncio.sleep(5)
            finally:
                self._send_ws = None

    async def _serve_reverse(self) -> None:
        from websockets.asyncio.server import serve as ws_serve
        if self._listen_host not in _LOOPBACK_HOSTS and not self._token:
            logger.error(
                "OneBot(reverse) 监听 %s 但未配置 access_token：所有连入都会被拒绝。"
                "要么把 channels.qq.onebot.listen_host 改回 127.0.0.1，"
                "要么配置 access_token（NapCat 侧同时带同一个令牌）。",
                self._listen_host,
            )
        logger.info("OneBot(reverse) 监听 ws://%s:%s", self._listen_host, self._listen_port)
        async with ws_serve(self._on_reverse_conn, self._listen_host, self._listen_port):
            while self._running:
                await asyncio.sleep(1)

    @staticmethod
    def _handshake(ws) -> tuple[Any, str]:
        """取握手头与请求路径（兼容 websockets 新老版本的属性名）。"""
        req = getattr(ws, "request", None)
        if req is not None:
            return getattr(req, "headers", None), getattr(req, "path", "") or ""
        return getattr(ws, "request_headers", None), ""

    def _inbound_authorized(self, ws) -> bool:
        """入站鉴权：无 token 时只允许本机回环；配了 token 则校验 Bearer / query。"""
        if not self._token:
            return self._listen_host in _LOOPBACK_HOSTS
        headers, path = self._handshake(ws)
        auth = ""
        if headers is not None:
            try:
                auth = headers.get("Authorization") or ""
            except Exception:  # noqa: BLE001  某些实现返回非 mapping，按未授权处理
                auth = ""
            if not auth and hasattr(headers, "items"):
                # dict / 普通 mapping 不区分大小写，补一次小写匹配
                for k, v in headers.items():
                    if str(k).lower() == "authorization":
                        auth = v or ""
                        break
        if auth.lower().startswith("bearer "):
            if hmac.compare_digest(auth[7:].strip(), self._token):
                return True
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            tok = (qs.get("access_token") or [""])[0]
            if tok and hmac.compare_digest(tok, self._token):
                return True
        except Exception:  # noqa: BLE001  路径解析失败 → 视为未带 token
            pass
        return False

    async def _on_reverse_conn(self, ws) -> None:
        if not self._inbound_authorized(ws):
            logger.warning(
                "OneBot(reverse) 拒绝未授权连入（来源 %s，token=%s）",
                getattr(ws, "remote_address", "?"),
                "已配置" if self._token else "未配置且非回环监听",
            )
            try:
                await ws.close(code=1008, reason="unauthorized")
            except Exception:  # noqa: BLE001
                pass
            return
        self._conns.add(ws)
        logger.info("OneBot(reverse) 收到 NapCat 连入（当前 %d 个）", len(self._conns))
        try:
            async for raw in ws:
                try:
                    await self._handle_raw(raw)
                except Exception:  # noqa: BLE001
                    logger.exception("处理反向 WS 消息异常")
        finally:
            self._conns.discard(ws)

    # ---------------- 消息处理 ----------------
    async def _handle_raw(self, raw: Any) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except Exception:
            # 不是合法 JSON 的帧 → 丢弃（协议容错，不拦连接）
            return
        if not isinstance(data, dict):
            return
        # OneBot 响应回执（带 echo、无 post_type）忽略
        if "post_type" not in data:
            return
        if data.get("post_type") == "meta_event":
            return  # lifecycle / heartbeat
        if data.get("post_type") == "message":
            await self._on_message_event(data)

    async def _on_message_event(self, data: dict) -> None:
        msg_type = data.get("message_type")
        sender = data.get("sender") or {}
        user_id = data.get("user_id")
        group_id = data.get("group_id")
        self_id = data.get("self_id")

        # 过滤自己发出的（回声）
        if self_id is not None and user_id is not None and str(user_id) == str(self_id):
            return
        # 黑名单 / 白名单
        if self._block_users and str(user_id) in self._block_users:
            return
        if self._allow_users and str(user_id) not in self._allow_users:
            return
        if msg_type == "group":
            if not self._reply_in_group:
                return  # 群消息整体关闭
            if self._allow_groups and str(group_id) not in self._allow_groups:
                return
            if not self._is_at_me(data):  # 群里仅响应 @ 本机器人
                return

        # 解析文本 + 媒体
        raw_msg = data.get("message", "")
        text, mlist = media.parse_onebot_message(raw_msg)
        text = media.strip_at_me(text, self_id)

        # 媒体摄入（图片 / 链接）
        extra_ctx = []
        try:
            extra_ctx = await media.ingest_media(
                self._llm, None, text, mlist,
                ingest_images=self._ingest_images,
                ingest_urls=self._ingest_urls,
                store_to_memory=self._store_to_memory,
                watch_videos=self._watch_videos,
            )
        except Exception:  # noqa: BLE001
            logger.exception("媒体摄入异常（继续按纯文本处理）")

        if not text and not extra_ctx:
            return
        combined = text
        if extra_ctx:
            combined = ((text + "\n\n" + "\n".join(extra_ctx)).strip()
                        if text else "\n".join(extra_ctx))

        reply_target = {
            "message_type": msg_type,
            "user_id": user_id,
            "group_id": group_id if msg_type == "group" else None,
        }
        try:
            await self._feed_brain(combined, reply_target, source="qq")
        except Exception:  # noqa: BLE001
            logger.exception("把 QQ 消息喂给大脑失败")
            await self._send_reply(reply_target, "（我这边有点晕，稍后再试）")

    def _is_at_me(self, data: dict) -> bool:
        msg = data.get("message", "")
        self_id = data.get("self_id")
        if isinstance(msg, str) and self_id is not None:
            if f"[CQ:at,qq={self_id}]" in msg:
                return True
        return False

    # ---------------- 决策回发 ----------------
    async def _on_decision(self, event: Event) -> None:
        d = event.data or {}
        if d.get("channel") != self.name:
            return  # 不是本通道的决策（桌面等），忽略
        if d.get("failed"):
            return  # 失败气泡不必发到 QQ
        target = d.get("reply_target")
        text = (d.get("text") or "").strip()
        media_list = d.get("media") or []
        if not target:
            return
        if not text and not media_list:
            return
        if text:
            await self._send_reply(target, text)
        if media_list:
            await self._send_media(target, media_list)

    async def _send_reply(self, target: dict, text: str) -> None:
        msg_type = target.get("message_type")
        params: dict[str, Any] = {"message_type": msg_type, "message": text}
        if msg_type == "group":
            params["group_id"] = target.get("group_id")
        else:
            params["user_id"] = target.get("user_id")
        await self._send_action("send_msg", params)

    async def _send_media(self, target: dict, media_list: list) -> None:
        """把决策里的 media（图片/视频）作为独立消息发回 QQ（CQ 码）。"""
        for m in media_list:
            f = m.get("file") or m.get("url") or ""
            if not f:
                continue
            if m.get("type") == "video":
                cq = f"[CQ:video,file={f}]"
            else:
                cq = f"[CQ:image,file={f}]"
            params: dict[str, Any] = {"message_type": target.get("message_type"), "message": cq}
            if target.get("message_type") == "group":
                params["group_id"] = target.get("group_id")
            else:
                params["user_id"] = target.get("user_id")
            await self._send_action("send_msg", params)

    async def _send_action(self, action: str, params: dict) -> None:
        self._echo += 1
        payload = {"action": action, "params": params, "echo": str(self._echo)}
        sent = False
        # 1) forward 模式：用已建立的 WS 发
        if self._mode == "forward" and self._send_ws is not None:
            try:
                await self._send_ws.send(json.dumps(payload, ensure_ascii=False))
                sent = True
            except Exception:  # noqa: BLE001
                logger.exception("OneBot 经 WS 发送失败，尝试 HTTP 兜底")
        # 2) reverse 模式：在已连入的 NapCat 连接上发
        if not sent and self._conns:
            for ws in list(self._conns):
                try:
                    await ws.send(json.dumps(payload, ensure_ascii=False))
                    sent = True
                    break
                except Exception:  # noqa: BLE001
                    logger.exception("反向 WS 发送失败")
        # 3) HTTP API 兜底（NapCat 默认 http://127.0.0.1:3000）
        if not sent:
            await self._send_http(action, params)

    async def _send_http(self, action: str, params: dict) -> None:
        url = f"{self._http_url.rstrip('/')}/{action}"
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        def _post() -> None:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                resp.read()

        try:
            await asyncio.to_thread(_post)
        except Exception:  # noqa: BLE001
            logger.exception("OneBot HTTP API 兜底发送失败: %s", url)

    # ---------------- 工具 ----------------
    def _auth_headers(self) -> dict:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}
