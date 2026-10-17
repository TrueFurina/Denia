"""抖音「免登录」解析的**真实浏览器**退路（2026-10-08）。

## 为什么需要它（实测根因）
抖音 2025 起把 `aweme/v1/web/aweme/detail` 挂上了 **ArgusSecurityPlugin**：
- 纯 HTTP（requests）直接打 → `403 Blocked by ArgusSecurityPlugin Uifid Not Found`；
- 自己伪造 `Uifid` 头 → `403 … Signature Not Found`（还要 `a_bogus` 签名）。

也就是说 **Uifid / Signature 都是页面里 JS 现算的**，任何"纯 requests 免登录"方案都必然 403
（这正是用户遇到的"解析抖音失败 → 退回标题级网页摘要"）。而 yt-dlp 的 DouyinIE 也没带匿名身份，
会报 `Fresh cookies … are needed`。

## 做法
用本机 **Edge（CDP 远程调试）** 真开一次视频页：页面自己的 JS 会完成签名与取流，
我们只**旁观**它的结果：
- `document.querySelector('video').currentSrc` → 真实播放直链（douyinvod.com）；
- `document.title` / `document.body.innerText` → 真实标题、时长、评论/弹幕文字；
- Network 事件兜底：`video.currentSrc` 还没就绪时，从响应里捞 `video/*` / `douyinvod` 的 URL。

**不登录、不导出 Cookie、不依赖第三方解析站**；失败（无 Edge / 超时 / 风控）一律返回 None，
由调用方退回 yt-dlp / 网页摘要，保证功能整体不瘫。

⚠️ 必须 `finally` 杀进程 + 删临时 profile：否则每次调用都留一个 Edge 僵尸进程与几百 MB 临时目录。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any, Optional
from urllib.request import urlopen

logger = logging.getLogger(__name__)

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _find_browser() -> str:
    """返回可用的 Edge/Chrome 可执行路径（找不到返回空串）。"""
    for p in _EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    for name in ("msedge", "chrome", "google-chrome"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _wait_devtools(port: int, timeout: float) -> bool:
    """等 DevTools HTTP 端点就绪（Edge 冷启动约 1~3s）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.0) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


def _page_ws_url(port: int) -> str:
    with urlopen(f"http://127.0.0.1:{port}/json", timeout=3.0) as r:
        targets = json.loads(r.read().decode("utf-8", "replace"))
    for t in targets or []:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"])
    return ""


class _CDP:
    """极简 CDP 客户端（同步 websockets）：只要 send/eval/收事件三件事。"""

    def __init__(self, ws_url: str) -> None:
        from websockets.sync.client import connect  # 延迟导入：缺依赖时不炸启动
        self._ws = connect(ws_url, max_size=16 * 1024 * 1024, open_timeout=10)
        self._id = 0
        self.media: list[str] = []      # 旁观到的视频响应 URL（兜底用）

    def send(self, method: str, params: Optional[dict] = None) -> dict:
        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        # 边收边记媒体事件，直到拿回本次响应
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                raw = self._ws.recv(timeout=max(0.1, deadline - time.time()))
            except Exception:  # noqa: BLE001
                break
            try:
                m = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if m.get("id") == mid:
                return m.get("result") or {}
            self._note(m)
        return {}

    def drain(self, seconds: float) -> None:
        """空转收事件（让 Network 事件有机会进来）。"""
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw = self._ws.recv(timeout=max(0.05, end - time.time()))
            except Exception:  # noqa: BLE001
                return
            try:
                self._note(json.loads(raw))
            except Exception:  # noqa: BLE001
                continue

    def _note(self, m: dict) -> None:
        if m.get("method") != "Network.responseReceived":
            return
        try:
            resp = (m.get("params") or {}).get("response") or {}
            url = str(resp.get("url") or "")
            mime = str(resp.get("mimeType") or "").lower()
            if (mime.startswith("video/") or ".mp4" in url
                    or "douyinvod" in url) and "douyinstatic" not in url:
                if url not in self.media:
                    self.media.append(url)
        except Exception:  # noqa: BLE001
            pass

    def eval(self, expr: str) -> Any:
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": False})
        res = (r or {}).get("result") or {}
        return res.get("value")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass


_PROBE_JS = r"""
(function(){
  var v = document.querySelector('video');
  var b = document.body;
  return JSON.stringify({
    href: location.href || '',
    src:  v ? (v.currentSrc || v.src || '') : '',
    dur:  v && isFinite(v.duration) ? v.duration : 0,
    title: (document.title || ''),
    text: (b ? (b.innerText || '') : '').slice(0, 4000)
  });
})()
"""


def resolve(url: str, timeout: float = 32.0) -> Optional[dict]:
    """用真实浏览器解析抖音视频页。

    返回 ``{url, video_id, title, duration, play_url, text}``；任何失败返回 None
    （调用方据此退回 yt-dlp / 网页摘要）。
    """
    browser = _find_browser()
    if not browser or not url:
        logger.info("抖音浏览器解析：无可用浏览器，跳过")
        return None
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="daniya_dy_")
    proc = None
    cdp = None
    try:
        proc = subprocess.Popen(
            [browser, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--mute-audio",
             "--autoplay-policy=no-user-gesture-required",
             f"--user-data-dir={profile}",
             f"--remote-debugging-port={port}",
             "--window-size=1280,900", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_devtools(port, 12.0):
            logger.warning("抖音浏览器解析：DevTools 未就绪（浏览器没起来？）")
            return None
        ws_url = _page_ws_url(port)
        if not ws_url:
            logger.warning("抖音浏览器解析：拿不到页面调试地址")
            return None
        cdp = _CDP(ws_url)
        cdp.send("Network.enable")
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        try:
            cdp.send("Emulation.setUserAgentOverride", {"userAgent": _UA})
        except Exception:  # noqa: BLE001
            pass
        logger.info("抖音浏览器解析：打开 %s", url)
        cdp.send("Page.navigate", {"url": url})

        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            cdp.drain(0.6)
            raw = cdp.eval(_PROBE_JS)
            if raw:
                try:
                    last = json.loads(raw)
                except Exception:  # noqa: BLE001
                    last = {}
            if (last.get("src") or "").startswith("http"):
                break
        play = str(last.get("src") or "").strip()
        if not play and cdp.media:
            play = cdp.media[-1]                      # 兜底：从网络事件里拿
        href = str(last.get("href") or url)
        m = re.search(r"/(?:video|share/video)/(\d+)", href or "")
        out = {
            "url": href,
            "video_id": m.group(1) if m else "",
            "title": str(last.get("title") or "").strip(),
            "duration": float(last.get("dur") or 0.0),
            "play_url": play,
            "text": str(last.get("text") or "").strip(),
        }
        if not play:
            logger.warning("抖音浏览器解析：页面没给出播放直链（可能被风控/需要登录）")
            return None
        logger.info("抖音浏览器解析成功：id=%s 时长=%.1fs 直链=%s…",
                    out["video_id"], out["duration"], play[:70])
        return out
    except Exception:  # noqa: BLE001
        logger.warning("抖音浏览器解析失败", exc_info=True)
        return None
    finally:
        # 必须彻底收尾：否则每次解析都留一个 Edge 进程 + 一个临时 profile
        if cdp is not None:
            cdp.close()
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        shutil.rmtree(profile, ignore_errors=True)
