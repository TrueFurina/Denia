"""通道基类（G 脚手架）。

所有外部 IM 通道的统一骨架：启动后台连接循环、订阅 AI_DECISION、把大脑决策发回对应 peer。
大脑 Dialogue 与通道解耦——通道只负责"搬运"文本，不碰任何 TTS / 情绪 / 记忆逻辑。
"""
from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("channels")


class ChannelError(Exception):
    """通道配置或运行错误。"""


class Channel(abc.ABC):
    """外部通道基类。

    子类约定：
      - name：通道唯一名（会写入 AI_DECISION.channel，用于路由回发）
      - _run()：连接/监听主循环，由 start() 在独立后台 task 中调用
      - 在 _run() 中 self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
      - _on_decision(event)：仅处理 channel==self.name 的决策并发回对应 peer
    """

    name: str = "base"

    def __init__(self, bus, dialogue, llm, cfg_section) -> None:
        self._bus = bus
        self._dialogue = dialogue
        self._llm = llm
        self._cfg = cfg_section  # config.channels.<name> 子节点（SimpleNamespace）
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ---- 生命周期 ----
    @abc.abstractmethod
    async def _run(self) -> None:
        """连接 / 监听主循环（子类实现）。由 start() 在后台 task 中调用。"""

    async def start(self) -> Optional[asyncio.Task]:
        """启动通道后台任务；已启动则直接返回既有 task。"""
        if self._running:
            return self._task
        self._running = True
        self._task = asyncio.create_task(self._run(), name=f"channel:{self.name}")
        logger.info("社交通道 %s 后台任务已创建", self.name)
        return self._task

    async def stop(self) -> None:
        """停止通道（取消后台任务）。"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info("社交通道 %s 已停止", self.name)

    # ---- 与大脑对接 ----
    async def _feed_brain(self, text: str, reply_target: dict, source: Optional[str] = None) -> None:
        """把一条用户消息喂给同一个 AI 大脑，并标记回复去向与来源通道。

        dialogue.respond 会发布带 channel/reply_target 的 AI_DECISION，
        本通道的 _on_decision 据此把回复发回正确 peer。
        """
        src = source or self.name
        await self._dialogue.respond(
            text, source=src, channel=self.name, reply_target=reply_target
        )

    async def _on_decision(self, event: Any) -> None:
        """订阅 AI_DECISION 的回调（子类按需在 _run 中订阅）。基类空实现。"""
