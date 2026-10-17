"""异步事件总线。

模块间解耦的核心：所有模块只通过事件通信，不直接互调。
内部用 asyncio.Queue 串行派发，避免并发竞态。
Vosk 等独立线程通过 publish_threadsafe 把事件投回主循环。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class EventType(Enum):
    """事件类型枚举。"""
    SCREEN_ACTIVITY = "screen_activity"   # 屏幕活动分析结果 {summary, app_hint, confidence}
    WAKE_WORD = "wake_word"               # 唤醒词命中 {word}
    USER_TEXT = "user_text"               # 用户输入文本 {text, source}
    AI_DECISION = "ai_decision"           # LLM 决策 {emotion, action, text, ...}
    STATE_CHANGE = "state_change"         # 状态机切换 {from, to}
    TTS_STARTED = "tts_started"           # TTS 开始播放 {text}
    TTS_DONE = "tts_done"             # TTS 播放完成
    TTS_INTERRUPT = "tts_interrupt"    # 打断当前/排队中的语音（通话 barge-in 专用）{reason}
    TTS_SAVED = "tts_saved"               # 语音已存档供回放 {url, text}
    PARTIAL_TEXT = "partial_text"         # LLM 流式输出片段 {text}
    UNITY_INBOUND = "unity_inbound"       # Unity 入站消息 {message_type, payload}
    CHARACTER_SWITCHED = "character_switched"  # 角色切换 {char_id, char_name}
    TTS_LOADING = "tts_loading"       # 本地 TTS 服务模型加载中 {engine, name}
    TTS_LOADED = "tts_loaded"         # 本地 TTS 服务模型加载完成 {engine, name}
    TTS_LOAD_ERROR = "tts_load_error" # 本地 TTS 服务模型加载失败 {engine, name, error}
    FOREGROUND_CHANGED = "foreground_changed"  # 前台窗口切换 {title, app_hint, prev_title}
    SET_PLAYER_NAME = "set_player_name"   # 设置游戏玩家名 {name}（OCR 订阅，用于击杀归属判断）
    VRC_CLOTHING = "vrc_clothing"         # VRC 衣物/玩具开关 {item, on}
    VRC_EMOTION = "vrc_emotion"           # VRC 表情切换 {name, on}
    ERROR = "error"                       # 框架内部错误 {code, message}
    ONBOARDING_DONE = "onboarding_done"  # 首启引导完成，触发重型子系统延迟启动
    VIDEO_WATCH_PROGRESS = "video_watch_progress"  # 陪你看视频阶段进度 {stage, message, done, pct}
    AGENT_STATUS = "agent_status"         # Agent 执行状态（UI 展示，不念）{text, phase}
    # 【2026-10-02 新增】双工通话记录：与聊天流**彻底隔离**。
    # 通话时双方说的话（文本 + 语音存档）只进这里，由独立的「通话记录」UI 展示，
    # 不再混进聊天页（此前用户说的话被当达妮娅的话显示在聊天里，且语音无回放）。
    # {speaker: "user"|"assistant", text, audio_url, ts, engine}
    CALL_SPEECH = "call_speech"


@dataclass
class Event:
    type: EventType
    data: Any = None
    source: str = ""


Handler = Callable[["Event"], Awaitable[None]]


class EventBus:
    """发布订阅 + 串行派发的事件总线。"""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: bool = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定主事件循环，供跨线程投递使用。"""
        self._loop = loop

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """订阅某类事件。"""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        """主循环内发布事件。"""
        await self._queue.put(event)

    def publish_threadsafe(self, event: Event) -> None:
        """从其他线程安全地把事件投回主循环。

        用于 Vosk 识别线程把 WAKE_WORD / USER_TEXT 事件送回 asyncio 主循环。
        """
        if self._loop is None:
            logger.warning("event bus 未绑定 loop，丢弃事件 %s", event.type)
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def run(self) -> None:
        """事件消费循环，常驻主任务。"""
        self._running = True
        logger.info("事件总线启动")
        while self._running:
            event = await self._queue.get()
            handlers = self._handlers.get(event.type, [])
            for h in handlers:
                try:
                    await h(event)
                except Exception:
                    logger.exception("处理事件 %s 时出错 (source=%s)", event.type, event.source)
            self._queue.task_done()

    def stop(self) -> None:
        self._running = False
