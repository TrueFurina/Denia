"""媒体摄入（G 脚手架）：把 QQ 消息里的图片 / 链接变成大脑能吃的上下文。

纯函数 + 少量 async 封装，便于无 QQ / 无 LLM 环境下单测。依赖仅标准库
（urllib / base64 / re）+ 可选注入的 llm.vision_describe。不引入新三方依赖。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import urllib.request
from typing import Any, Optional

logger = logging.getLogger("channels.media")

from .video_watch import is_video_url, watch_video  # 懒加载依赖在 video_watch 内

# OneBot / 通用 http(s) 链接
_URL_RE = re.compile(r"https?://[^\s，。？！]+", re.IGNORECASE)
# OneBot v11 CQ 码： [CQ:type,key=value,key=value]
_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_]+)([^]]*)\]")


def parse_onebot_message(message: Any) -> tuple[str, list[dict]]:
    """解析 OneBot message → (纯文本, 媒体段列表)。

    - message 为字符串（v11）：拆出 [CQ:image ...] 等媒体段，其余作为纯文本。
    - message 已结构化（v12 list / dict）：尝试 json 化后当字符串处理。
    返回 (text, [{"type":"image","file":...,"url":...}, ...])。
    """
    if not isinstance(message, str):
        try:
            message = json.dumps(message, ensure_ascii=False)
        except Exception:
            return "", []
    media: list[dict] = []
    for m in _CQ_RE.finditer(message):
        ctype = m.group(1)
        params: dict[str, str] = {}
        for seg in m.group(2).split(","):
            if "=" in seg:
                k, v = seg.split("=", 1)
                params[k] = v
        if ctype == "image":
            media.append({
                "type": "image",
                "file": params.get("file", ""),
                "url": params.get("url", ""),
            })
        elif ctype == "text":
            # CQ:text 段里的文本（极少用，但规范允许）
            message = message[:m.start()] + params.get("text", "") + message[m.end():]
    # 去掉所有 CQ 段后剩下的纯文本
    plain = _CQ_RE.sub("", message).strip()
    return plain, media


def strip_at_me(text: str, self_id: Any) -> str:
    """群消息里去掉 "@机器人" 片段，避免回带 @。"""
    if self_id is None:
        return text
    return re.sub(rf"\[CQ:at,qq={re.escape(str(self_id))}\]", "", text).strip()


async def ingest_media(
    llm, store, text: str, media: list[dict], *,
    ingest_images: bool, ingest_urls: bool, store_to_memory: bool,
    watch_videos: bool = False, on_progress=None,
) -> list[str]:
    """对一条消息做媒体摄入，返回追加到用户文本后的上下文片段列表。

    - 图片：下载/解码 → llm.vision_describe → 【图片】描述
    - 链接：抓取页面 → 截断摘要 → 【链接 url】摘要
    - 视频链接（watch_videos 开启且命中视频平台）：取原视频 → 抽帧 → VLM 看画面 → 【视频观看】上下文
    可选把内容写入长期记忆(type="world", source=qq_media/wechat_media)。
    """
    ctx: list[str] = []
    if ingest_images:
        for m in media:
            if m.get("type") == "image":
                desc = await _describe_image(llm, m)
                if desc:
                    ctx.append(f"【图片】{desc}")
                    if store_to_memory:
                        await _store_world(store, f"用户通过 QQ 发来图片：{desc}")
    # 链接处理：视频观看独立于网页摘要开关（watch_videos 可单独开，不影响 ingest_urls）。
    # 这样桌面"只看视频不抓网页"场景（ingest_urls=False, watch_videos=True）也能命中。
    if ingest_urls or watch_videos:
        for u in _URL_RE.findall(text or ""):
            _video_failed = False   # 每个链接独立：本链接是否"视频没看成、退回读文字"
            if watch_videos and is_video_url(u):
                # 视频链接：尝试"陪你看视频"（取原视频→抽帧→VLM）
                logger.info("[媒体摄入] 命中视频链接 %s，开始观看", u)
                vctx = await watch_video(llm, u, frames=4, on_progress=on_progress)
                if vctx:
                    ctx.append(vctx)
                    logger.info("[媒体摄入] 观看成功，上下文 %d 字", len(vctx))
                    if store_to_memory:
                        await _store_world(
                            store, f"用户分享视频链接 {u}（{vctx[:200]}）")
                    continue  # 观看成功，不再走网页摘要
                # 观看不可用/失败：若开启网页摘要则退回，否则跳过
                _video_failed = True
                logger.warning("[媒体摄入] 观看未产出上下文 url=%s（%s）", u,
                               "将退回网页摘要" if ingest_urls else "网页摘要也关闭，按普通文本处理")
            if ingest_urls:
                # 【2026-10-03】图文链接（公众号文章/微博/普通网页）也给"正在看"进度：
                # 前端气泡靠 on_progress 事件驱动，没有事件就整段静默 8 秒，用户以为卡死。
                # 【2026-10-08 修·终态自相矛盾】视频失败后退回读文字时，文案**不能说"看完了"**
                #   —— 实测日志同一秒先 `stage=error`（这个视频暂时看不了）又 `stage=done`
                #   （看完了，这就和你聊～），卡片最终显示"看完了"，等于对用户撒谎；且 kind 被
                #   从 video 刷成 link。这里按 _video_failed 选诚实的文案。
                if on_progress:
                    try:
                        on_progress("page",
                                    "视频没看不成，改读链接文字…" if _video_failed
                                    else "达妮娅正在读这篇链接…",
                                    False, None,
                                    {"kind": "link", "title": None, "url": u})
                    except Exception:  # noqa: BLE001
                        pass
                snippet = await _fetch_page(u)
                if on_progress:
                    try:
                        on_progress("done",
                                    "这个视频暂时看不了，我按链接文字和你聊～" if _video_failed
                                    else ("看完了，这就和你聊～" if snippet
                                          else "这篇链接读不到正文，我按你说的聊～"),
                                    done=True, pct=None,
                                    meta={"kind": "link", "title": None, "url": u})
                    except Exception:  # noqa: BLE001
                        pass
                if snippet:
                    ctx.append(f"【链接 {u}】{snippet[:2000]}")
                    if store_to_memory:
                        await _store_world(
                            store, f"用户通过 QQ 分享链接 {u}（摘要：{snippet[:200]}）")
    return ctx


# ----------------- 内部实现 -----------------
async def _describe_image(llm, m: dict) -> Optional[str]:
    try:
        data = await _load_image(m)
        if not data:
            return None
        # 【2026-10-03 修】vision_available 是 property(bool)，不是方法；同 video_watch。
        _va = getattr(llm, "vision_available", False)
        if not (bool(_va() if callable(_va) else _va)):
            logger.debug("llm 不支持视觉，跳过图片描述")
            return None
        prompt = ("用一句话描述这张图片里有什么；如果是截图/表情包/带文字的图片，"
                  "请点出关键信息。不要过度解读或编造。")
        desc = await llm.vision_describe(data, prompt)
        return (desc or "").strip() or None
    except Exception:  # noqa: BLE001
        logger.exception("图片描述失败")
        return None


async def _load_image(m: dict) -> Optional[bytes]:
    f = (m.get("file") or "")
    u = (m.get("url") or "")
    if f.startswith("base64://"):
        try:
            return base64.b64decode(f[len("base64://"):])
        except Exception:
            return None
    if u:
        return await _http_get_bytes(u)
    if f.startswith("http://") or f.startswith("https://"):
        return await _http_get_bytes(f)
    # 本地路径（NapCat 有时给绝对路径或 file://）
    if f and (f.startswith("file://") or f.startswith("/") or ":" in f):
        path = f.replace("file://", "")
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except Exception:
            return None
    return None


async def _fetch_page(url: str) -> Optional[str]:
    def _get() -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "replace")
    try:
        return await asyncio.to_thread(_get)
    except Exception:  # noqa: BLE001
        logger.exception("抓取链接失败: %s", url)
        return None


async def _http_get_bytes(url: str) -> Optional[bytes]:
    def _get() -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    try:
        return await asyncio.to_thread(_get)
    except Exception:  # noqa: BLE001
        logger.exception("下载资源失败: %s", url)
        return None


async def _store_world(store, text: str) -> None:
    """把媒体内容写入长期记忆（world 类，来源 qq_media）。失败静默忽略。"""
    try:
        if store is None:
            from ..companion.memory_store import get_memory_store
            store = get_memory_store()
        if store is not None:
            store.add(text, type="world", source="qq_media")
    except Exception:  # noqa: BLE001
        logger.debug("写入媒体记忆失败（忽略）")
