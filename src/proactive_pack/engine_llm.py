# -*- coding: utf-8 -*-
"""本地模型引擎（llm/vlm 档）聊天客户端。

为什么单独一个模块：
  · 软件的"模型调用引擎"是**一体四档**（llm / vlm / tts / 双工），由
    `src/engine/*` 托管（Comni / llama.cpp-omni）。主动包只需要 **llm/vlm 那一档**
    （纯文本问答 / 看屏描述），用来既"主动发言"又"审查"，且不碰用户聊天上下文。
  · 实测确认：引擎网关 **不是** OpenAI 风格端点。`gateway.py` 暴露的是
    `POST /api/chat`（另有 `/ws/chat`、`/ws/duplex/...`），请求体是 Comni 自己的
    `ChatRequest` schema（见 Comni `core/schemas/chat.py`）：
        {"messages":[{"role":"user","content":"..."}],
         "generation":{"max_new_tokens":N,"temperature":T},
         "enable_thinking":false}
    响应 `ChatResponse`：{"text": "...", "success": true, "error": null, ...}
  · 所以不能拿项目里那个 OpenAI 客户端去套；这里按真实 schema 包一个极小的客户端。
    依赖只用标准库（urllib + ssl + asyncio.to_thread），保持本包"零第三方依赖"。

安全 / 兼容：
  · 本客户端**只连本地回环端点**（默认 https://localhost:8006）；网关用自签/过期证书，
    故默认不校验证书（与项目 `minicpm_client.py` 同口径）；同时**屏蔽系统代理**
    （Windows 上 Clash/V2Ray 会把 loopback 请求劫持走代理 → 502）。
  · `engine_client_for_host(host)` 从宿主解析引擎端点，**解析不到就返回 None**
    （主动包据此回落到宿主聊天路由，绝不静默改行为）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request as _url
from typing import Any, Optional

logger = logging.getLogger("proactive_pack")

# 引擎网关默认端点（config.yaml: proactive.duplex_local_omni_endpoint）
DEFAULT_ENGINE_ENDPOINT = "https://localhost:8006"


class EngineChatClient:
    """把本地模型引擎的 llm/vlm 档（网关 `/api/chat`）包成聊天客户端。

    与 `judge.MiniOpenAIClient` 的区别：后者走 OpenAI 兼容协议（给审查器换云端强模型用），
    本类走 Comni 原生 schema（给"同一个模型走引擎既发言又审查"用）。
    """

    name = "engine-llm"

    def __init__(self, *, base_url: str = "", timeout: float = 60.0,
                 max_tokens: int = 256, verify_tls: bool = False) -> None:
        self._base = (base_url or "").rstrip("/")
        self._timeout = float(timeout or 60.0)
        self._max_tokens = int(max_tokens)
        self._verify = bool(verify_tls)

    @property
    def available(self) -> bool:
        return bool(self._base)

    @property
    def endpoint(self) -> str:
        return self._base

    def __repr__(self) -> str:  # 排障友好
        return "EngineChatClient(@ %s)" % (self._base or "(未配置)")

    def _opener(self):
        """构造 urllib opener：绕过自签证书校验 + 屏蔽系统代理（仅本地回环）。"""
        if self._verify:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl._create_unverified_context()
        return _url.build_opener(_url.HTTPSHandler(context=ctx), _url.ProxyHandler({}))

    async def chat(self, messages: list, options: Optional[dict] = None) -> str:
        """一次无状态问答；返回模型文本。异常向上抛（由调用方决定 fail-open / 回落）。"""
        if not self.available:
            raise RuntimeError("引擎端点未配置（endpoint 为空）")
        opts = options or {}
        msgs = [{"role": str(m.get("role") or "user"),
                 "content": str(m.get("content") or "")}
                for m in (messages or [])]
        gen: dict = {"max_new_tokens": int(opts.get("max_tokens") or self._max_tokens)}
        if "temperature" in opts:
            try:
                gen["temperature"] = float(opts["temperature"])
            except (TypeError, ValueError):
                pass
        body_d = {
            "messages": msgs,
            "generation": gen,
            # 主动包是"纯 chat"：显式关思考，避免思维链漏进台词。
            "enable_thinking": bool(opts.get("enable_thinking", False)),
        }
        body = json.dumps(body_d, ensure_ascii=False).encode("utf-8")
        url = self._base + "/api/chat"

        def _call() -> str:
            opener = self._opener()
            req = _url.Request(url, data=body,
                               headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=self._timeout) as resp:
                return resp.read().decode("utf-8", "replace")

        raw = await asyncio.to_thread(_call)
        try:
            data = json.loads(raw or "{}")
        except (ValueError, TypeError):
            raise RuntimeError("引擎返回非 JSON：%s" % (raw or "")[:200])
        if not data.get("success", True):
            raise RuntimeError("引擎返回失败：%s" % (data.get("error") or "unknown"))
        return str(data.get("text") or "")


def engine_client_for_host(host: Any, *, base_url: str = "",
                           probe: bool = False) -> Optional[EngineChatClient]:
    """从宿主解析本地模型引擎端点并构造客户端；拿不到 → None。

    `probe=True` 时额外用引擎对象的 `is_model_loaded()` 确认已挂载
    （会触发一次本地健康检查，**不要在 UI/status 路径用**，避免卡顿）。
    """
    ep = (base_url or "").strip()
    eng = getattr(host, "_local_omni_engine", None) or getattr(host, "_engine", None)
    if not ep:
        try:
            ep = str(getattr(eng, "endpoint", "") or "")
        except Exception:  # noqa: BLE001
            ep = ""
    if not ep:
        ep = str(getattr(host, "_duplex_local_omni_endpoint", "") or "")
    if not ep:
        return None
    if probe and eng is not None:
        try:
            if hasattr(eng, "is_model_loaded") and not eng.is_model_loaded():
                return None
        except Exception:  # noqa: BLE001
            return None
    return EngineChatClient(base_url=ep)
