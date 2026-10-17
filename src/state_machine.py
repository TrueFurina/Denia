"""AI 状态机。

四态：idle / listening / thinking / speaking
所有切换走事件总线发 STATE_CHANGE，同时广播给 Unity 同步 3D 模型状态。
speaking 期间 ASR 暂停，避免自循环拾取 TTS 声音。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .event_bus import EventBus, Event, EventType
from .protocol import State

logger = logging.getLogger(__name__)

# 合法状态迁移表
_TRANSITIONS: dict[State, set[State]] = {
    # 【2026-09-23 修复】允许 IDLE→SPEAKING：桌宠主动搭话、通话开场白等场景
    # 本就在 idle 态直接触发 TTS（src/voice/tts.py:_speak_flow 不走 listening/thinking）。
    # 旧表不允许该迁移 ⇒ transition() 返回 False 但调用方不检查，音频照播、状态却停在
    # idle ⇒ speaking 期间的 ASR 暂停与 3D 模型"正在说话"状态全部失效（自拾音/动画不触发）。
    State.IDLE: {State.LISTENING, State.THINKING, State.SPEAKING},
    State.LISTENING: {State.THINKING, State.IDLE},
    State.THINKING: {State.SPEAKING, State.IDLE},
    State.SPEAKING: {State.IDLE},
}


class StateMachine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._state: State = State.IDLE
        self._history: list[tuple[State, State, float]] = []

    @property
    def state(self) -> State:
        return self._state

    def can(self, to: State) -> bool:
        return to in _TRANSITIONS.get(self._state, set())

    async def transition(self, to: State, source: str = "") -> bool:
        """切换状态。非法迁移会被拒绝并记录。"""
        if to == self._state:
            return True
        if not self.can(to):
            logger.warning("非法状态迁移 %s → %s (source=%s)", self._state.value, to.value, source)
            return False
        old = self._state
        self._state = to
        self._history.append((old, to, time.time()))
        logger.info("状态切换 %s → %s (source=%s)", old.value, to.value, source)
        await self._bus.publish(Event(EventType.STATE_CHANGE, {"from": old.value, "to": to.value}, source))
        return True

    async def to_idle(self, source: str = "") -> None:
        """从任意可回 idle 的状态回到 idle。"""
        await self.transition(State.IDLE, source)
