"""解析 LLM 输出为结构化决策 {emotion, action, text}。

鲁棒处理：正则提取 JSON、字段校验、缺失填默认、失败兜底为纯文本。
"""
from __future__ import annotations

import json
import logging
import re

from ..protocol import ACTION_VALUES, EMOTION_VALUES

logger = logging.getLogger(__name__)


# 模型（尤其陪伴场景）常把"关心"直接写成 comfort/concerned 这类**非法情绪词**，
#   被 _normalize 打回 neutral 后桌宠表情就"没情绪"。这里做近义归一，把常见的
#   越界情绪/动作词映射回合法枚举，避免白丢一次情绪/动作。
_EMOTION_SYNONYM = {
    "comfort": "sad", "concerned": "sad", "caring": "sad", "sympathetic": "sad",
    "worried": "sad", "sorrow": "sad", "upset": "sad", "down": "sad",
    "gentle": "happy", "warm": "happy", "love": "happy", "loving": "happy",
    "playful": "happy", "teasing": "happy", "proud": "happy", "smug": "happy",
    "excited": "happy", "cheerful": "happy", "content": "happy", "glad": "happy",
    "annoyed": "angry", "irritated": "angry", "grumpy": "angry", "mad": "angry",
    "frustrated": "angry", "pout": "angry",
    "tired": "sleepy", "drowsy": "sleepy", "weary": "sleepy",
    "nervous": "surprised", "anxious": "surprised", "shocked": "surprised",
    "amazed": "surprised", "confused": "curious", "wondering": "curious",
    "calm": "neutral", "relaxed": "neutral", "peaceful": "neutral",
}
_ACTION_SYNONYM = {
    "nod": "listen", "hello": "greet", "hi": "greet", "wave_hand": "wave",
    "speak": "talk", "say": "talk", "reply": "talk", "respond": "talk",
    "giggle": "laugh", "chuckle": "laugh", "ponder": "think",
    "care": "comfort", "console": "comfort", "soothe": "comfort", "hug": "comfort",
    "rest": "yawn", "sleep": "yawn",
}


def _normalize(obj: dict) -> dict:
    emotion = str(obj.get("emotion", "neutral")).strip().lower()
    action = str(obj.get("action", "idle")).strip().lower()
    text = str(obj.get("text", "")).strip()
    intensity = _clamp_intensity(obj.get("intensity"))
    if emotion not in EMOTION_VALUES:
        emotion = _EMOTION_SYNONYM.get(emotion, "neutral")
    if action not in ACTION_VALUES:
        action = _ACTION_SYNONYM.get(action, "idle")
    return {"emotion": emotion, "action": action, "intensity": intensity, "text": text}


def _clamp_intensity(v) -> int:
    """情绪强度 1~5 归一化（缺失/非法 → 3）。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, n))


def _extract_partial(raw: str) -> dict:
    """从不完整/损坏的 JSON 文本中尽力提取 emotion/action/text 字段值。

    LLM 偶发输出被截断（缺右括号）时，json.loads 必然失败；但键值对通常是
    完整的，这里用正则兜底提取，避免把整段 JSON（含 emotion/action/text 英文
    键名）当作说话文本念出来。
    """
    out = {"emotion": "neutral", "action": "idle", "intensity": 3, "text": ""}
    m = re.search(r'"emotion"\s*:\s*"(\w+)"', raw)
    if m and m.group(1).lower() in EMOTION_VALUES:
        out["emotion"] = m.group(1).lower()
    m = re.search(r'"action"\s*:\s*"(\w+)"', raw)
    if m and m.group(1).lower() in ACTION_VALUES:
        out["action"] = m.group(1).lower()
    m = re.search(r'"intensity"\s*:\s*(\d+)', raw)
    if m:
        out["intensity"] = _clamp_intensity(m.group(1))
    m = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
    if m:
        try:
            out["text"] = json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            out["text"] = m.group(1).replace("\\n", "\n")
    return out


def had_json(raw: str) -> bool:
    """raw 里是否真的含可解析的 JSON 对象（而非模型只吐了纯文本台词）。

    用途：当模型"忘了套 JSON 外壳"时，emotion/action 在源头就丢了——parse 只能
    回退成纯文本 + 默认 neutral/idle，桌面表情/动作不跟随。调用方可据此触发
    「回退重试」把结构补回来（见 dialogue 的 JSON 兜底）。
    """
    if not raw:
        return False
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    candidates: list[str] = [cleaned]
    for m in re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL):
        candidates.append(m)
    candidates.extend(re.findall(r"\{.*?\}", cleaned, re.DOTALL))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            # 这个候选片段不是 JSON，试下一个
            continue
        if isinstance(obj, dict):
            return True
    return False


def parse(raw: str) -> dict:
    """解析 LLM 输出。始终返回合法 dict，不抛异常。"""
    if not raw:
        return {"emotion": "neutral", "action": "idle", "intensity": 3, "text": ""}

    cleaned = raw.strip()
    # 去掉 markdown 代码块围栏
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    candidates: list[str] = []
    # 直接整体
    candidates.append(cleaned)
    # 提取所有 {...} 片段（贪婪到最外层）
    for m in re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL):
        candidates.append(m)
    # 最宽匹配兜底
    simple = re.findall(r"\{.*?\}", cleaned, re.DOTALL)
    candidates.extend(simple)

    # 优先尝试最长的候选（最可能是完整 JSON）
    for cand in sorted(set(candidates), key=len, reverse=True):
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            # 同上：候选逐个试，直到解析出合法 JSON
            continue
        if isinstance(obj, dict):
            return _normalize(obj)

    # 整体/片段 JSON 全部解析失败 → 先从残损 JSON 中提取字段，避免把英文键名当说话文本
    partial = _extract_partial(cleaned)
    if partial["text"]:
        logger.warning("决策 JSON 不完整，按字段提取兜底: %r", cleaned[:120])
        return partial
    logger.warning("决策解析失败，兜底为纯文本: %r", raw[:120])
    return {"emotion": "neutral", "action": "idle", "intensity": 3, "text": raw.strip()[:200]}
