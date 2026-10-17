"""LLM 客户端统一接口。

所有 provider（Ollama / OpenAI 兼容 / MiniCPM Realtime）实现同一套鸭子类型接口，
上层（Dialogue / VisionAnalyzer / Proactive）无需感知具体实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class LLMClient(ABC):
    """大模型客户端抽象基类。"""

    name: str = "base"

    @property
    @abstractmethod
    def available(self) -> bool:
        """是否可用（已配置且能提供服务）。"""
        ...

    @property
    @abstractmethod
    def vision_available(self) -> bool:
        """视觉能力是否可用。"""
        ...

    @property
    def model_name(self) -> Optional[str]:
        """当前使用的模型名（供日志/UI 显示）。"""
        return None

    @abstractmethod
    async def chat(self, messages: list[dict], options: Optional[dict] = None) -> str:
        """多轮对话。messages: [{role, content, images?}]，返回纯文本。"""
        ...

    @abstractmethod
    async def vision_describe(self, image_bytes: bytes, prompt: str) -> str:
        """单图视觉描述。视觉不可用时返回空串。"""
        ...

    def supports_stream(self) -> bool:
        """是否支持真正的流式输出。默认 False（chat_stream 退化为一次性 chat）。"""
        return False

    async def chat_stream(self, messages: list[dict], options: Optional[dict] = None):
        """流式对话（可选能力）：逐 token 产出文本片段。

        默认实现退化为非流式 chat（一次性产出整段），供不支持流式的后端使用。
        支持流式的子类（如 Ollama）应覆写 supports_stream() 与 chat_stream()。
        """
        text = await self.chat(messages, options)
        if text:
            yield text
