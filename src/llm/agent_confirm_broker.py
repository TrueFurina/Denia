# -*- coding: utf-8 -*-
"""【AGENT·2026-09-28】电脑操作代理的"逐步确认"代理（聊天工具循环 / 独立 agent 模式共用）。

为什么需要它
------------
让达妮娅在聊天里"点按钮 / 打字 / 按按键 / 跑命令"属于高风险操作，必须先弹确认框让用户
允许/拒绝，否则她会不经同意就动鼠标键盘。这条确认链路横跨前后端：

  后端工具循环（dialogue / agent_brain）
        │  request_confirm(desc)  ← 协程：生成 key、登记 future、广播给前端、await
        ▼
  前端弹窗（index.html 的 showAgentConfirm）
        │  用户点"允许/拒绝" → Backend.resolveAgentConfirm(key, allow)
        ▼
  后端 RPC（webapp.resolve_agent_confirm）→ resolve_confirm(key, allow) 唤醒 future

设计要点
--------
- 进程内共享一个 `{(key): (future, loop)}` 字典，请求方（await）与回执方（set_result）解耦。
- 广播器（broadcaster）由 webapp 在启动时注入（set_broadcaster(_wv2_broadcast)），
  本模块不依赖 webapp，避免循环 import。
- **fail-closed**：超时（默认 60s）或广播器缺失 → 默认拒绝，绝不静默放行。
- **线程安全**：回执可能来自 GUI 线程（pywebview RPC），用 loop.call_soon_threadsafe
  唤醒 future，避免跨线程 set_result 导致事件循环错乱。
- 每个工具返回说明字符串而非抛异常（与 agent_tools / agent_brain 一致）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid

logger = logging.getLogger(__name__)

# key -> (asyncio.Future, asyncio.AbstractEventLoop)
_PENDING: dict[str, tuple] = {}
_BROADCASTER = None
CONFIRM_TIMEOUT_S = 60.0


def set_broadcaster(fn) -> None:
    """注入广播函数（如 webapp._wv2_broadcast），把确认请求推到前端。"""
    global _BROADCASTER
    _BROADCASTER = fn


async def request_confirm(desc: str) -> bool:
    """请求用户确认一个高危操作。返回 True=允许，False=拒绝/超时。

    内部：生成 key → 登记 future → 广播 {type:'agent_confirm', key, desc} → 等待回执。
    超时或广播器缺失 → False（fail-closed）。
    """
    key = uuid.uuid4().hex
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("agent_confirm: 无运行事件循环，默认拒绝 %s", desc)
        return False
    fut: asyncio.Future = loop.create_future()
    _PENDING[key] = (fut, loop)
    try:
        if _BROADCASTER is not None:
            try:
                _BROADCASTER({"type": "agent_confirm", "key": key, "desc": str(desc)})
            except Exception as e:  # noqa: BLE001
                logger.warning("agent_confirm: 广播失败，默认拒绝 %s: %s", desc, e)
                return False
        else:
            logger.warning("agent_confirm: 未注册广播器，默认拒绝 %s", desc)
            return False
        try:
            return bool(await asyncio.wait_for(fut, timeout=CONFIRM_TIMEOUT_S))
        except asyncio.TimeoutError:
            logger.warning("agent_confirm: 超时（%ss）默认拒绝 %s", CONFIRM_TIMEOUT_S, desc)
            return False
    finally:
        _PENDING.pop(key, None)


def resolve_confirm(key: str, allow: bool) -> bool:
    """前端回执：唤醒对应 future（线程安全）。返回是否找到了等待中的确认。"""
    entry = _PENDING.get(key)
    if not entry:
        return False
    fut, loop = entry
    if fut.done():
        return False
    try:
        loop.call_soon_threadsafe(fut.set_result, bool(allow))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_confirm: 唤醒失败 %s: %s", key, e)
        return False
