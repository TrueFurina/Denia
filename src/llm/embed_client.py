# -*- coding: utf-8 -*-
"""记忆语义检索的向量来源：本地 Ollama 优先，云端兜底。

为什么要有这个模块（2026-11-04）
--------------------------------
`memory.semantic_search: true` 默认开着，但向量来源只有一处：
`ollama_client.make_embed_fn`（本地 Ollama 的 `nomic-embed-text`）。
实测开发机没装 Ollama → 它返回 None → **语义检索静默降级为关键词召回**，
整条链路只留一行 warning。而"她记得你"完全靠召回质量撑着：
关键词召回对"我上次说我最怕什么"这类改写式提问基本无效——用户看到的是
"她忘了"，归因却会落在"这个产品没记忆"上。

云端兜底用阿里云百炼的 OpenAI 兼容 `/embeddings`（`text-embedding-v3`，1024 维），
Key 复用 `${DASHSCOPE_API_KEY}`。实测 HTTP 200。

设计取舍
--------
· **构造时探测一次**，不通就返回 None：宁可整条链路退回关键词，也不要每次召回
  都发一次注定失败的请求（这个坑 `ollama_client` 已经踩过，注释里记着）。
· 只做同步调用：`MemoryStore` 的 `embed_fn` 契约就是同步 `list[float] | None`。
  单次 200ms 左右，只在写入/召回时发生，不在每帧路径上。
· 失败一律返回 None（调用方本来就按"没有向量"处理），不抛异常、不重试。
"""
from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-v3"


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    """POST JSON 并解析响应。失败抛异常，由调用方决定降级。

    单独抽出来是为了让测试能替换掉传输层（不起真网络、不进闸门的慢路径）。
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_embedding(resp: dict):
    """从 OpenAI 兼容响应里取第一条向量；结构不对就返回 None。"""
    try:
        data = (resp or {}).get("data") or []
        if not data:
            return None
        vec = data[0].get("embedding")
        return list(vec) if vec else None
    except Exception:  # noqa: BLE001  结构异常按"没有向量"处理
        return None


def make_cloud_embed_fn(base_url: str, api_key: str,
                        model: str = DEFAULT_MODEL, timeout: float = 8.0):
    """构造云端嵌入函数；探测失败返回 None（调用方降级为关键词召回）。

    返回 `_embed(text) -> list[float] | None`。
    """
    base_url = (base_url or "").strip()
    api_key = (api_key or "").strip()
    model = (model or DEFAULT_MODEL).strip()
    if not base_url or not api_key:
        logger.info("云端嵌入未配置（base_url/api_key 为空），语义检索保持关键词召回")
        return None
    url = base_url.rstrip("/") + "/embeddings"

    try:
        probe = _extract_embedding(_post_json(
            url, {"model": model, "input": "探测"}, api_key, timeout))
        if not probe:
            logger.warning("云端嵌入探测返回空向量（%s / %s），语义检索降级为关键词召回",
                           base_url, model)
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning("云端嵌入不可用（%s / %s），语义检索降级为关键词召回: %s",
                       base_url, model, e)
        return None

    def _embed(text: str):
        try:
            return _extract_embedding(_post_json(
                url, {"model": model, "input": text}, api_key, timeout))
        except Exception:  # noqa: BLE001  单次失败只丢这一次的向量
            logger.debug("云端嵌入失败", exc_info=True)
            return None

    logger.info("记忆语义检索已启用云端嵌入: %s / %s", base_url, model)
    return _embed


def make_embed_fn_auto(cfg):
    """按 `memory.embed.provider` 选向量来源。

    provider:
      · auto（默认）= 先试本地 Ollama，不可用再试云端；
      · ollama     = 只用本地，失败就降级（保持老行为）；
      · dashscope  = 只用云端；
      · off        = 显式关闭语义检索（等同没有 embed_fn）。

    任何分支失败都返回 None，调用方按"没有向量"处理（关键词召回）。
    """
    raw = {}
    try:
        raw = ((cfg.raw or {}).get("memory") or {}).get("embed") or {}
    except Exception:  # noqa: BLE001
        raw = {}
    provider = str(raw.get("provider") or "auto").strip().lower()

    if provider in ("off", "none", "disabled", "false"):
        return None

    if provider in ("auto", "ollama"):
        fn = None
        try:
            from .ollama_client import make_embed_fn
            host = getattr(getattr(cfg, "ns", None), "ollama", None)
            fn = make_embed_fn(getattr(host, "host", "http://localhost:11434"))
        except Exception:  # noqa: BLE001  没装 ollama 库等，一律当"本地不可用"
            fn = None
        if fn is not None:
            return fn
        if provider == "ollama":
            return None

    if provider in ("auto", "dashscope", "cloud"):
        return make_cloud_embed_fn(
            str(raw.get("base_url") or ""),
            str(raw.get("api_key") or ""),
            str(raw.get("model") or DEFAULT_MODEL),
        )

    return None
