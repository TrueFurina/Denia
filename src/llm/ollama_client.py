"""Ollama 异步客户端封装。

- 视觉模型可用时：对话+视觉都用 openbmb/minicpm-o2.6
- 视觉模型不可用但降级模型可用：纯文本对话用 qwen3:14b，传入 images 时自动丢弃
- 都不可用：标记不可用，上层进入降级模式
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ollama import AsyncClient

from ..config import Config
from .base import LLMClient

logger = logging.getLogger(__name__)


def make_embed_fn(host: str, model: str = "nomic-embed-text"):
    """构造同步文本嵌入函数（长期记忆语义检索用）。

    构造时先探测嵌入模型是否已安装：不可用直接返回 None（调用方降级为关键词召回），
    避免每次召回都发起失败的 HTTP 请求并刷错误日志。
    返回 _embed(text) -> list[float] | None。
    """
    from ollama import Client

    try:
        client = Client(host=host, timeout=30.0)
        # 探测：模型未 pull 会抛 404，一次失败立即降级
        client.embeddings(model=model, prompt="探测")
    except Exception as e:  # noqa: BLE001
        logger.warning("记忆嵌入模型 %s 不可用，语义检索降级为关键词召回（可 ollama pull %s）: %s",
                       model, model, e)
        return None

    def _embed(text: str):
        try:
            resp = client.embeddings(model=model, prompt=text)
            if isinstance(resp, dict):
                return resp.get("embedding") or None
            return getattr(resp, "embedding", None) or None
        except Exception:  # noqa: BLE001
            logger.debug("记忆嵌入失败", exc_info=True)
            return None

    return _embed


def _extract_content(resp: Any) -> str:
    """兼容 dict / Pydantic 对象两种返回。"""
    if resp is None:
        return ""
    if isinstance(resp, dict):
        msg = resp.get("message") or {}
        return msg.get("content", "") or ""
    msg = getattr(resp, "message", None)
    if msg is not None:
        return getattr(msg, "content", "") or ""
    return ""


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, cfg: Config, model: Optional[str] = None) -> None:
        ollama_cfg = cfg.ns.ollama
        self._host = ollama_cfg.host
        # model 参数用于指定专用模型（如分层视觉的轻量模型 qwen2.5vl:3b）；
        # 不传时按 runtime_status 选择主视觉模型或降级模型
        self._vision_model = model or ollama_cfg.vision_model
        self._fallback_model = ollama_cfg.fallback_model
        self._timeout = ollama_cfg.timeout_s
        # options 是 SimpleNamespace，转 dict
        self._options = vars(ollama_cfg.options) if hasattr(ollama_cfg, "options") else {}
        self._client = AsyncClient(host=self._host)

        if model is not None:
            # 指定模型（如轻量视觉模型）：直接使用，可用性交给调用方验证
            self._chat_model = model
            self._vision_available = True
            return

        status = cfg.runtime_status
        if status.vision_model_ok:
            self._chat_model = self._vision_model
            self._vision_available = True
        elif status.fallback_model_ok:
            self._chat_model = self._fallback_model
            self._vision_available = False
        else:
            self._chat_model = None
            self._vision_available = False

    @property
    def available(self) -> bool:
        return self._chat_model is not None

    @property
    def vision_available(self) -> bool:
        return self._vision_available

    @property
    def model_name(self) -> Optional[str]:
        return self._chat_model

    def unload_current_model(self) -> None:
        """退出时把当前加载的模型从显存卸载，避免关软件后显卡仍被占用。

        用 ollama HTTP API（keep_alive=0）同步执行——不依赖异步事件循环，
        即使在退出/atexit 上下文也能可靠调用。无可用模型或服务不可达时静默跳过。
        """
        import json as _json
        import urllib.request as _urllib
        models = {m for m in (self._chat_model, self._vision_model) if m}
        if not models:
            return
        for mdl in models:
            try:
                body = _json.dumps({"model": mdl, "keep_alive": 0, "prompt": ""}).encode("utf-8")
                req = _urllib.Request(
                    f"{self._host}/api/generate", data=body,
                    headers={"Content-Type": "application/json"}, method="POST")
                with _urllib.urlopen(req, timeout=30) as resp:
                    resp.read()
                logger.info("Ollama 模型已卸载（释放显存）: %s", mdl)
            except Exception as exc:  # noqa: BLE001
                logger.warning("卸载 Ollama 模型失败（可忽略，下次启动会重载）: %s -> %s", mdl, exc)

    async def chat(self, messages: list[dict], options: Optional[dict] = None,
                   keep_alive: Optional[str] = None) -> str:
        """调用模型对话。图片以 raw bytes 形式放在 message 的 'images' 字段。

        视觉不可用时自动剥离 message 中的 images 走纯文本降级。
        options 可覆盖全局采样参数（如主动话术生成临时调高 temperature）。
        返回模型输出的纯文本。
        """
        if not self.available:
            raise RuntimeError("Ollama 无可用模型（视觉与降级模型均未 pull）")

        use_messages = messages
        if not self._vision_available:
            # 视觉不可用：剥离 images，避免纯文本模型报错
            use_messages = []
            stripped = False
            for m in messages:
                m2 = dict(m)
                if "images" in m2:
                    m2.pop("images", None)
                    stripped = True
                use_messages.append(m2)
            if stripped:
                logger.warning("视觉模型不可用，剥离图片走纯文本降级")

        use_options = dict(self._options)
        if options:
            use_options.update(options)

        try:
            chat_kwargs: dict = dict(
                model=self._chat_model,
                messages=use_messages,
                stream=False,
                options=use_options,
            )
            if keep_alive is not None:
                chat_kwargs["keep_alive"] = keep_alive
            resp = await asyncio.wait_for(
                self._client.chat(**chat_kwargs),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Ollama 调用超时 ({self._timeout}s)") from e
        return _extract_content(resp)

    async def chat_stream(self, messages: list[dict], options: Optional[dict] = None,
                           keep_alive: Optional[str] = None):
        """流式对话：逐 token 产出文本片段，实现"边生成边念/边显示"。

        视觉不可用时自动剥离 images 走纯文本降级（同 chat）。
        用法：async for chunk in client.chat_stream(messages): ...
        """
        if not self.available:
            raise RuntimeError("Ollama 无可用模型（视觉与降级模型均未 pull）")

        use_messages = messages
        if not self._vision_available:
            # 视觉不可用：剥离 images，避免纯文本模型报错
            use_messages = []
            stripped = False
            for m in messages:
                m2 = dict(m)
                if "images" in m2:
                    m2.pop("images", None)
                    stripped = True
                use_messages.append(m2)
            if stripped:
                logger.warning("视觉模型不可用，剥离图片走纯文本降级")

        use_options = dict(self._options)
        if options:
            use_options.update(options)

        try:
            stream_kwargs: dict = dict(
                model=self._chat_model,
                messages=use_messages,
                stream=True,
                options=use_options,
            )
            if keep_alive is not None:
                stream_kwargs["keep_alive"] = keep_alive
            stream = await asyncio.wait_for(
                self._client.chat(**stream_kwargs),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Ollama 调用超时 ({self._timeout}s)") from e

        async for chunk in stream:
            content = _extract_content(chunk)
            if content:
                yield content

    def supports_stream(self) -> bool:
        return True

    async def vision_describe(self, image_bytes: bytes, prompt: str) -> str:
        """单图视觉描述（截图分析用）。视觉不可用返回空串。"""
        if not self._vision_available:
            return ""
        return await self.chat(
            messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        )
