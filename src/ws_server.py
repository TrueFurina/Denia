"""WebSocket 服务器：向 Unity 广播 AI 决策，并接收 Unity 入站消息。

订阅事件总线上的事件，转换为协议消息后广播给所有连接的 Unity 客户端。
Unity 也可反向发消息（user_input / set_emotion）触发对话。
"""
from __future__ import annotations

import asyncio
import json
import logging

import websockets
from websockets.asyncio.server import broadcast, serve

from .event_bus import EventBus, Event, EventType
from .protocol import (
    make_anim_done,
    make_character_switched,
    make_clothing,
    make_clothing_state,
    make_decision,
    make_emotion,
    make_error,
    make_partial_text,
    make_perception,
    make_state_change,
    make_tts_finished,
    make_tts_started,
    parse_inbound,
)

# 【AGENT·2026-09-27】电脑操作代理：让达妮娅能真正操作这台电脑。
from .llm.agent_brain import run_agent_task, agent_route_chat

logger = logging.getLogger(__name__)


class WebSocketServer:
    def __init__(self, bus: EventBus, host: str, port: int) -> None:
        self._bus = bus
        self._host = host
        self._port = port
        self._connections: set = set()
        self._server = None

    async def start(self) -> None:
        self._subscribe_events()
        self._server = await serve(self._handler, self._host, self._port)
        logger.info("WebSocket 服务启动 ws://%s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _subscribe_events(self) -> None:
        """订阅需要广播给 Unity 的事件。"""
        self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
        self._bus.subscribe(EventType.STATE_CHANGE, self._on_state_change)
        self._bus.subscribe(EventType.SCREEN_ACTIVITY, self._on_perception)
        self._bus.subscribe(EventType.TTS_STARTED, self._on_tts_started)
        self._bus.subscribe(EventType.TTS_DONE, self._on_tts_done)
        self._bus.subscribe(EventType.PARTIAL_TEXT, self._on_partial)
        self._bus.subscribe(EventType.CHARACTER_SWITCHED, self._on_character_switched)
        self._bus.subscribe(EventType.ERROR, self._on_error)
        self._bus.subscribe(EventType.VRC_CLOTHING, self._on_vrc_clothing)
        self._bus.subscribe(EventType.VRC_EMOTION, self._on_vrc_emotion)

    async def broadcast(self, msg: dict) -> None:
        """广播给所有连接（fire-and-forget，单连接异常不影响其他）。"""
        if not self._connections:
            return
        try:
            broadcast(self._connections, json.dumps(msg, ensure_ascii=False))
        except Exception:
            logger.exception("广播失败")

    # ---- 事件 → 协议消息 ----
    async def _on_decision(self, e: Event) -> None:
        d = e.data or {}
        # 【G 社交通道】带非桌面 channel 标记的决策（如 QQ 回复发往手机）不要广播到
        # 桌面 Unity/WebView 客户端，避免桌面 TTS 把 QQ 私聊念出来。桌面决策 channel
        # 为空（默认），正常广播；桌面自身若显式标 channel="desktop" 也照常广播。
        if d.get("channel") and d.get("channel") != "desktop":
            return
        if d.get("blocked"):
            # 质量门拦截的乱语：不广播给 Unity（没真开口，不驱动口型）
            return
        await self.broadcast(make_decision(
            d.get("emotion", "neutral"),
            d.get("action", "idle"),
            d.get("text", ""),
            d.get("user_activity"),
            d.get("voice"),
            d.get("live2d_expr"),
            d.get("intensity"),
        ))

    async def _on_state_change(self, e: Event) -> None:
        d = e.data or {}
        await self.broadcast(make_state_change(d.get("from", "idle"), d.get("to", "idle")))

    async def _on_perception(self, e: Event) -> None:
        d = e.data or {}
        # 只广播带 summary 的感知结果（截图原始事件不含 summary 不广播）
        if isinstance(d, dict) and d.get("summary"):
            await self.broadcast(make_perception(
                d.get("summary", ""),
                d.get("app_hint", ""),
                d.get("confidence", 0.0),
            ))

    async def _on_tts_started(self, e: Event) -> None:
        d = e.data or {}
        await self.broadcast(make_tts_started(d.get("text", "")))

    async def _on_tts_done(self, e: Event) -> None:
        await self.broadcast(make_tts_finished())

    async def _on_partial(self, e: Event) -> None:
        d = e.data or {}
        # 【G 社交通道】带非桌面 channel（如 "qq"）的流式句子不广播到桌面客户端，
        # 避免桌面 Unity/WebView 把 QQ 私聊逐句念出来/打字机显示。
        if d.get("channel") and d.get("channel") != "desktop":
            return
        await self.broadcast(make_partial_text(d.get("text", "")))

    async def _on_character_switched(self, e: Event) -> None:
        d = e.data or {}
        await self.broadcast(make_character_switched(
            d.get("char_id", ""),
            d.get("char_name", ""),
            d.get("model_3d"),
        ))

    async def _on_error(self, e: Event) -> None:
        d = e.data or {}
        await self.broadcast(make_error(d.get("code", "UNKNOWN"), d.get("message", "")))

    async def _on_vrc_clothing(self, e: Event) -> None:
        d = e.data or {}
        item = (d.get("item") or "").strip()
        if not item:
            return
        await self.broadcast(make_clothing(item, bool(d.get("on", True))))

    async def _on_vrc_emotion(self, e: Event) -> None:
        d = e.data or {}
        name = (d.get("name") or "").strip()
        if not name:
            return
        await self.broadcast(make_emotion(name, bool(d.get("on", True))))

    # ---- 电脑操作代理（AGENT）----
    async def _agent_status(self, text) -> None:
        """每步进度广播给前端（UI agent 可消费 message_type=agent_status 展示）。

        兼容纯文本与结构化 dict（来自 agent_brain.run 的 {phase,text,step,...}）。
        """
        try:
            if isinstance(text, dict):
                payload = dict(text)
                if "text" not in payload:
                    payload["text"] = (payload.get("raw") or
                                       (f"步骤 {payload.get('step')}/{payload.get('total')} · "
                                        f"{payload.get('tool', '')}") or "正在操作")
            else:
                payload = {"text": str(text)}
            await self.broadcast({"message_type": "agent_status", "payload": payload})
        except Exception:  # noqa: BLE001
            pass

    async def _agent_speak(self, text: str) -> None:
        """LLM 在操作轮次说出的话（口头确认/进度）经 TTS 念出来（复用现有 speak 路径）。"""
        text = (text or "").strip()
        if not text:
            return
        try:
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "neutral",
                "action": "talk",
                "text": text,
                "is_proactive": True,
                "source": "agent",
            }, "ws_server"))
        except Exception:  # noqa: BLE001
            pass

    async def _run_agent_task(self, instruction: str) -> None:
        """后台执行电脑操作代理：进度广播 agent_status，最终结果念出来并广播 agent_result。

        结果无论成功与否都通过 AI_DECISION 让达妮娅念出来；代理未启用时 result 本身就是说明文案。
        """
        await self._agent_status(f"收到指令：{instruction}")
        try:
            result = await run_agent_task(instruction, on_status=self._agent_status,
                                      on_speak=self._agent_speak,
                                      on_confirm=self._agent_confirm)
        except Exception as e:  # noqa: BLE001
            result = f"电脑代理执行异常：{e}"
        result = str(result).strip() or "（代理未返回结果）"
        await self.broadcast({"message_type": "agent_result", "payload": {"text": result}})
        # 让达妮娅把结果念出来（复用现有 speak 路径；is_proactive 避免被当成用户话再回复）
        await self._bus.publish(Event(EventType.AI_DECISION, {
            "emotion": "neutral",
            "action": "talk",
            "text": result,
            "is_proactive": True,
            "source": "agent",
        }, "ws_server"))

    # 安全确认：require_confirm=true 时，破坏性工具执行前先弹前端确认框，等回执。
    # 超时/前端不在线 → 默认拒绝（fail-closed），绝不静默执行。
    AGENT_CONFIRM_TIMEOUT_S = 60.0

    async def _agent_confirm(self, desc: str) -> bool:
        if not hasattr(self, "_agent_confirms"):
            self._agent_confirms = {}
        import uuid as _uuid
        key = _uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._agent_confirms[key] = fut
        await self.broadcast({"message_type": "agent_confirm",
                              "payload": {"key": key, "desc": str(desc)}})
        try:
            return bool(await asyncio.wait_for(fut, timeout=self.AGENT_CONFIRM_TIMEOUT_S))
        except asyncio.TimeoutError:
            logger.warning("电脑代理确认超时（%ss），默认拒绝：%s", self.AGENT_CONFIRM_TIMEOUT_S, desc)
            return False
        finally:
            self._agent_confirms.pop(key, None)

    # ---- 入站 ----
    async def _handler(self, websocket) -> None:
        self._connections.add(websocket)
        logger.info("Unity 客户端连接: %s", getattr(websocket, "remote_address", None))
        try:
            async for message in websocket:
                try:
                    parsed = parse_inbound(message)
                except ValueError as e:
                    logger.warning("入站消息解析失败: %s", e)
                    continue
                logger.info("Unity 入站: %s", parsed.get("message_type"))
                # anim_done：单次动作播完，直接广播给前端（供"测试全部动画"逐动画推进），
                # 不 publish 到事件总线，避免 dialogue/webapp 误当对话消息处理。
                if parsed.get("message_type") == "anim_done":
                    await self.broadcast(make_anim_done(
                        (parsed.get("payload") or {}).get("action", "")))
                    continue
                # clothing_state：Unity 推送全量衣物/玩具状态，直接转发前端同步按钮高亮。
                if parsed.get("message_type") == "clothing_state":
                    states = (parsed.get("payload") or {}).get("states") or []
                    await self.broadcast(make_clothing_state(states))
                    continue
                # agent_task：电脑操作代理指令。纯后端能力，不依赖 UI——执行结果通过
                # AI_DECISION 让达妮娅念出来，并广播 agent_status / agent_result 供前端展示。
                # 后台 fire-and-forget，避免阻塞本连接继续收消息。
                if parsed.get("message_type") == "agent_task":
                    pl = parsed.get("payload") or {}
                    instruction = str(pl.get("text") or pl.get("instruction") or "").strip()
                    if instruction:
                        asyncio.create_task(self._run_agent_task(instruction))
                    else:
                        await self.broadcast({"message_type": "agent_result",
                                              "payload": {"text": "没有收到要执行的指令"}})
                    continue
                # agent_confirm_reply：前端确认框回执（允许/拒绝）。解析后唤醒等待中的 future。
                if parsed.get("message_type") == "agent_confirm_reply":
                    pl = parsed.get("payload") or {}
                    key = pl.get("key")
                    allow = bool(pl.get("allow", False))
                    waiters = getattr(self, "_agent_confirms", None)
                    fut = waiters.get(key) if waiters else None
                    if fut is not None and not fut.done():
                        fut.set_result(allow)
                    continue
                # 聊天框输入（user_input）自动路由到 agent：开 route_chat 后，打字聊天即自动操作+念结果。
                # 不改变其它消息类型；route_chat 关时走原对话路径。
                if parsed.get("message_type") == "user_input" and agent_route_chat():
                    pl = parsed.get("payload") or {}
                    text = str(pl.get("text") or "").strip()
                    if text:
                        asyncio.create_task(self._run_agent_task(text))
                    continue
                await self._bus.publish(Event(EventType.UNITY_INBOUND, parsed, "websocket"))
        except Exception:
            logger.debug("Unity 连接异常关闭", exc_info=True)
        finally:
            self._connections.discard(websocket)
            logger.info("Unity 客户端断开")
