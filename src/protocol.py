"""面向 Unity 的 WebSocket 消息协议。

定义消息类型、情绪/动作/状态枚举，以及消息工厂函数。
所有发往 Unity 的消息统一信封：
{
  "message_type": "...",
  "timestamp": 1722595200.123,
  "session_id": "a1b2c3",
  "payload": { ... }
}
"""
from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from typing import Any, Optional

# 每次 AI 进程启动生成一个会话 ID（连接级别广播都会带上）
SESSION_ID: str = uuid.uuid4().hex[:8]


class MessageType(str, Enum):
    # AI → Unity
    DECISION = "decision"             # 核心：驱动表情+动作+口型+字幕
    STATE_CHANGE = "state_change"     # 状态机切换
    PERCEPTION = "perception"         # 感知事件（用户在做什么）
    PARTIAL_TEXT = "partial_text"     # LLM 流式片段
    HEARTBEAT = "heartbeat"           # 心跳
    TTS_STARTED = "tts_started"       # 开始播放语音（Unity 启动口型）
    TTS_FINISHED = "tts_finished"     # 语音播放完（Unity 停口型）
    CHARACTER_SWITCHED = "character_switched"  # 角色切换
    ERROR = "error"                   # 框架内部错误/降级通知
    CLOTHING = "clothing"             # VRC 衣物/玩具开关（前端按钮/语音/文字触发）
    CLOTHING_STATE = "clothing_state" # Unity → 前端：全量衣物/玩具状态（进入时同步按钮）
    EMOTION = "emotion"               # VRC 表情切换（FaceEmo 预设 blendshape 直控）
    # Unity → AI（双向）
    USER_INPUT = "user_input"         # Unity 点击模型/按钮触发对话
    SET_EMOTION = "set_emotion"       # Unity 强制设置情绪
    SET_CHARACTER = "set_character"   # Unity 请求切换角色
    ANIM_DONE = "anim_done"           # Unity 单次动作播完（服务器转发给前端，供"测试全部动画"推进）


class Emotion(str, Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SHY = "shy"
    SURPRISED = "surprised"
    SAD = "sad"
    ANGRY = "angry"
    SLEEPY = "sleepy"
    CURIOUS = "curious"


class Action(str, Enum):
    IDLE = "idle"
    GREET = "greet"
    LISTEN = "listen"
    THINK = "think"
    TALK = "talk"
    LAUGH = "laugh"
    WAVE = "wave"
    YAWN = "yawn"
    COMFORT = "comfort"


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


# emotion / action 合法值集合，供 LLM 输出校验
EMOTION_VALUES = {e.value for e in Emotion}
ACTION_VALUES = {a.value for a in Action}


def _envelope(msg_type: MessageType, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_type": msg_type.value,
        "timestamp": time.time(),
        "session_id": SESSION_ID,
        "payload": payload,
    }


def _val(v: Any) -> str:
    """枚举或字符串统一转字符串值。"""
    return v.value if isinstance(v, Enum) else str(v)


def make_decision(
    emotion: Any,
    action: Any,
    text: str,
    user_activity: Optional[dict[str, Any]] = None,
    voice: Optional[str] = None,
    live2d_expr: Optional[str] = None,
    intensity: Optional[int] = None,
) -> dict[str, Any]:
    """构造核心决策消息。"""
    payload: dict[str, Any] = {
        "emotion": _val(emotion),
        "action": _val(action),
        "text": text,
    }
    if user_activity is not None:
        payload["user_activity"] = user_activity
    if voice is not None:
        payload["voice"] = voice
    if live2d_expr is not None:
        payload["live2d_expr"] = live2d_expr
    if intensity is not None:
        payload["intensity"] = intensity
    return _envelope(MessageType.DECISION, payload)


def make_state_change(from_state: Any, to_state: Any) -> dict[str, Any]:
    return _envelope(MessageType.STATE_CHANGE, {"from": _val(from_state), "to": _val(to_state)})


def make_perception(activity_summary: str, app_hint: str = "", confidence: float = 0.0) -> dict[str, Any]:
    return _envelope(MessageType.PERCEPTION, {
        "activity_summary": activity_summary,
        "app_hint": app_hint,
        "confidence": confidence,
    })


def make_partial_text(text: str) -> dict[str, Any]:
    return _envelope(MessageType.PARTIAL_TEXT, {"text": text})


def make_heartbeat(uptime_s: float, state: Any) -> dict[str, Any]:
    return _envelope(MessageType.HEARTBEAT, {"uptime_s": uptime_s, "state": _val(state)})


def make_tts_started(text: str) -> dict[str, Any]:
    return _envelope(MessageType.TTS_STARTED, {"text": text})


def make_tts_finished() -> dict[str, Any]:
    return _envelope(MessageType.TTS_FINISHED, {})


def make_character_switched(char_id: str, char_name: str, model_3d: dict = None) -> dict[str, Any]:
    """构造角色切换消息。"""
    payload = {"char_id": char_id, "char_name": char_name}
    if model_3d:
        payload["model_3d"] = model_3d
    return _envelope(MessageType.CHARACTER_SWITCHED, payload)


def make_error(code: str, message: str) -> dict[str, Any]:
    return _envelope(MessageType.ERROR, {"code": code, "message": message})


def make_anim_done(action: str) -> dict[str, Any]:
    """Unity 单次动作播完（服务器把 Unity 的 anim_done 入站消息转发给前端）。"""
    return _envelope(MessageType.ANIM_DONE, {"action": _val(action)})


def make_clothing(item: str, on: bool) -> dict[str, Any]:
    """VRC 衣物/玩具开关消息（前端按钮/语音命令/聊天文字触发，Unity 端 SetActive 直控）。"""
    return _envelope(MessageType.CLOTHING, {"item": item, "on": bool(on)})


def make_clothing_state(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Unity → 前端：全量衣物/玩具状态（进入时前端按此同步按钮高亮）。"""
    return _envelope(MessageType.CLOTHING_STATE, {"states": states})


def make_emotion(name: str, on: bool = True) -> dict[str, Any]:
    """VRC 表情切换消息（FaceEmo 预设，Unity 端按名字应用 blendshape 组合）。"""
    return _envelope(MessageType.EMOTION, {"name": name, "on": bool(on)})


def parse_inbound(raw: str) -> dict[str, Any]:
    """解析 Unity 入站消息，返回 {message_type, payload}。失败抛 ValueError。"""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"非法 JSON: {e}") from e
    if not isinstance(obj, dict) or "message_type" not in obj:
        raise ValueError("缺少 message_type 字段")
    return {"message_type": obj["message_type"], "payload": obj.get("payload", {})}
