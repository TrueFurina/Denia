"""情绪 → 语音表达 中心表（F 步·2026-10-01）。

为什么要有这一层：
- 决策侧产出的 emotion 标签不统一：对话 LLM 给的是「表情名」(gentle/smile/curious/
  surprised/neutral…)，双工 `_duplex_text_emotion` 给的是「情绪名」(happy/negative/
  curious/surprised/neutral)。两者都要翻译成引擎能懂的语音表达。
- 不同引擎表达方式不同：CosyVoice 走自然语言 `instruct`(inference_instruct2)；VoxCPM 走
  非语言标签(`apply_emotion_tags`，由角色 emotion_tags 配置)。本模块只负责「标签归一 +
  CosyVoice instruct 文案」，VoxCPM 标签壳仍由 tts_backends.apply_emotion_tags 负责。

关键事实（决定 F 该改哪条链路）：
- 聊天回复 与 「默认双工」(`duplex_native_audio=false`) 走的是**同一条本地 TTS 管道**
  （webapp→AI_DECISION→tts.speak(text,emotion)）。所以把 emotion 接进 TTS 后端，
  **聊天 + 默认双工语音会同时获得情绪表达**，一次改两处体验。
- 只有当 `duplex_native_audio=true`（模型原生嗓：云端网关克隆 / 本地 omni 声码器）时，
  语音才绕开本地 TTS、由双工模型自己出声——那条是「模型原生语音」独立通道，本模块的
  TTS 情绪注入对它无效（只能靠双工模型自身的情绪能力，属另一条 track）。
"""
from __future__ import annotations

# ---- 规范情绪集（决策标签归一后的目标）----
EMOTION_CANON = ("happy", "negative", "curious", "surprised", "gentle", "neutral")

# ---- 任意标签/中文 → 规范情绪 ----
_NORMALIZE: dict[str, str] = {
    # happy
    "happy": "happy", "开心": "happy", "高兴": "happy", "喜悦": "happy",
    "笑": "happy", "smile": "happy", "positive": "happy", "excited": "happy",
    "joy": "happy", "cheerful": "happy", "乐": "happy",
    # negative（含安抚/安慰意图）
    "negative": "negative", "sad": "negative", "难受": "negative", "难过": "negative",
    "伤心": "negative", "烦": "negative", "烦躁": "negative", "生气": "negative",
    "angry": "negative", "沮丧": "negative", "低落": "negative", "委屈": "negative",
    "comfort": "gentle", "安抚": "gentle", "安慰": "gentle", "sigh": "gentle",
    # curious
    "curious": "curious", "好奇": "curious", "疑问": "curious", "疑惑": "curious",
    "探究": "curious", "wonder": "curious", "think": "curious", "思考": "curious",
    # surprised
    "surprised": "surprised", "惊讶": "surprised", "震惊": "surprised", "诧异": "surprised",
    "wow": "surprised", "惊喜": "surprised", "shock": "surprised",
    # gentle（温柔/柔和，与 negative 区分：安抚而非低落）
    "gentle": "gentle", "温柔": "gentle", "柔和": "gentle", "soft": "gentle",
    "温婉": "gentle", "体贴": "gentle", "warm": "gentle",
    # neutral
    "neutral": "neutral", "中性": "neutral", "平静": "neutral", "calm": "neutral",
    "normal": "neutral", "idle": "neutral",
}


def normalize_emotion(label) -> str:
    """把任意情绪标签/中文归一为规范集；无法识别回退 neutral。"""
    if not label:
        return "neutral"
    key = str(label).strip().lower()
    if key in EMOTION_CANON:
        return key
    canon = _NORMALIZE.get(key)
    if canon:
        return canon
    # 再试一次：去掉常见前缀/标点
    _k = key.strip("[]（）()_- ")
    return _NORMALIZE.get(_k, "neutral")


# ---- CosyVoice 自然语言指令文案（inference_instruct2 用）----
# neutral 给空串 ⇒ 不注入 instruct，走零样本克隆原声（最自然）。
_COSY_INSTRUCT: dict[str, str] = {
    "happy": "用开心、轻快、上扬的语气说，带一点笑意",
    "negative": "用温柔、放缓、安抚的语气说，像在安慰人",
    "curious": "用好奇、俏皮、带点探究劲儿的语气说",
    "surprised": "用惊讶、兴奋、语速稍快的语气说",
    "gentle": "用温柔、柔和、放慢一点的语气说",
    "neutral": "",
}


def emotion_to_instruct(label) -> str:
    """emotion 标签 → CosyVoice 自然语言 instruct；neutral/未知返回空串（不注入）。"""
    canon = normalize_emotion(label)
    return _COSY_INSTRUCT.get(canon, "")
