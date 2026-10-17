"""QQ空间 发布器（G 脚手架）：仿 astrbot_plugin_qzone / qzone-mcp，用浏览器登录态 cookie 发说说、读说说。

原理：腾讯空间接口需 cookie（p_skey/skey）算 g_tk 防伪 token，调：
  - 读说说：emotion_cgi_msglist_v6（返回 JSONP，需剥壳）
  - 发说说：emotion_cgi_publish_v6
  - 资料：  cgi_get_portrait.fcg
非官方接口，灰色；cookie 从浏览器登录 i.qq.com 后 F12 → document.cookie 取得。
依赖：无新增三方包（urllib）。

⚠️ 发说说的图片发布需先调空间相册上传接口取得 picBo，结构随版本变动，本文先实现纯文本发布，
   图片发布为 TODO（需联调）。读说说/资料已可离线验证。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

from ..event_bus import Event, EventType
from .base import Channel

logger = logging.getLogger("channels.qzone")

_QZONE_HOST = "user.qzone.qq.com"
_REFERER = "https://qzone.qq.com/"


def _gtk(skey: str) -> int:
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


class QzoneClient:
    """QQ空间 HTTP 客户端（纯逻辑，可离线 mock）。"""

    def __init__(self, cookie: str, uin: str) -> None:
        self.cookie = cookie
        self.uin = str(uin)
        self.token = self._compute_token(cookie)

    @staticmethod
    def _compute_token(cookie: str) -> int:
        # 优先 p_skey（空间接口用），回退 skey
        m = re.search(r"(?:^|;\s*)p_skey=([^;]+)", cookie)
        if not m:
            m = re.search(r"(?:^|;\s*)skey=([^;]+)", cookie)
        skey = m.group(1) if m else ""
        return _gtk(skey) if skey else 0

    def _headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": _REFERER,
            "Cookie": self.cookie,
        }

    def _get(self, url: str) -> str:
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return resp.read().decode("utf-8", "replace")

    @staticmethod
    def _strip_jsonp(text: str) -> str:
        text = text.strip()
        # 形如 _preloadCallback({...}); 或 callback({...})
        m = re.match(r"^[A-Za-z0-9_]+\(", text)
        if m:
            text = text[m.end():]
        text = re.sub(r"\);?\s*$", "", text)
        return text

    def get_moments(self, num: int = 20) -> list[dict]:
        base = (f"https://{_QZONE_HOST}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6")
        params = (f"?uin={self.uin}&ftype=0&sort=0&pos=0&num={num}&replynum=100"
                  f"&g_tk={self.token}&code_version=1&format=jsonp&need_private_comment=1")
        try:
            raw = self._get(base + params)
            data = json.loads(self._strip_jsonp(raw))
        except Exception:  # noqa: BLE001
            logger.exception("解析空间说说列表失败")
            return []
        if isinstance(data, dict):
            return data.get("msglist") or []
        return []

    def publish(self, text: str) -> dict:
        url = (f"https://{_QZONE_HOST}/proxy/domain/taotao.qzone.qq.com/cgi-bin/"
               f"emotion_cgi_publish_v6?g_tk={self.token}")
        form = {
            "content": text,
            "richtype": "",
            "richval": "",
            "special_url": "",
            "subrichtype": "",
            "who": "1",  # 1=所有人可见
            "contain_week": "0",
        }
        data = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(self._strip_jsonp(raw)) if raw.strip().startswith("{") else {"raw": raw}
        except Exception:  # noqa: BLE001
            logger.exception("发说说失败")
            return {}


class QzonePublisher(Channel):
    """QQ空间发布器：仅出站（大脑主动/被动触发 channel=qzone 的决策时发说说）。无入站。"""

    name = "qzone"

    def __init__(self, bus, dialogue, llm, cfg_section) -> None:
        super().__init__(bus, dialogue, llm, cfg_section)
        cookie = getattr(cfg_section, "cookie", "") or ""
        uin = getattr(cfg_section, "uin", "") or ""
        if not cookie or not uin:
            raise ValueError("config.channels.qzone 需要 cookie 与 uin")
        self._client = QzoneClient(cookie, uin)

    async def _run(self) -> None:
        self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
        logger.info("QQ空间发布器已就绪（等待大脑触发 channel=qzone 的决策）")
        while self._running:
            await asyncio.sleep(1)

    async def _on_decision(self, event: Event) -> None:
        d = event.data or {}
        if d.get("channel") != self.name:
            return
        if d.get("failed"):
            return
        text = (d.get("text") or "").strip()
        if not text:
            return
        try:
            res = self._client.publish(text)
            code = res.get("code") if isinstance(res, dict) else None
            logger.info("QQ空间发说说结果 code=%s", code)
        except Exception:  # noqa: BLE001
            logger.exception("QQ空间发说说异常")
