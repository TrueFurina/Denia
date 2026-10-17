"""LLM 层：客户端工厂、多 provider 客户端、提示词与决策解析。

create_llm(cfg) 返回纯文本聊天路由（TextChatRouter）：
  默认走 config.yaml 的 text_chat 端口池（OpenRouter / 智谱 / 硅基流动 / 本地
  Ollama 等免费文本端口），把"纯聊天"从双工网关摘出来，避免沾满 MiniCPM 网关
  的 14 个共享 worker。只有文本端口全失败时兜底回网关 chat()。

  双工（聊天+看屏幕）与视觉仍由 MiniCPMClient 直接服务于网关，不经过此处。

  另：ollama → 本地 Ollama；openai → 任意 OpenAI 兼容云端接口；
  minicpm → MiniCPM-o 4.5 Realtime 免费云端接口（支持图片）。
"""
from __future__ import annotations

from ..config import Config
from .base import LLMClient
from .minicpm_client import MiniCPMClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient
from .vision_router import VisionRouter, create_vision_router
from .text_chat_router import TextChatRouter, create_text_chat_router

__all__ = ["LLMClient", "OllamaClient", "OpenAIClient", "MiniCPMClient",
           "VisionRouter", "create_vision_router",
           "TextChatRouter", "create_text_chat_router", "create_llm"]


def create_llm(cfg: Config) -> LLMClient:
    """返回纯文本聊天路由（默认走免费文本端口池，见 config.yaml text_chat）。

    双工/视觉仍由 MiniCPMClient 直接服务于网关，不在此处——目的是让纯聊天
    尽量不占双工 worker。若 text_chat 未配置，路由会兜底回网关 chat()。
    """
    return create_text_chat_router(cfg)
