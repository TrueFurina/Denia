"""能力槽中央配置模型 + 生效状态同步（2026-10-10 改版：LLM+VLM 合并 + 阿里云一键）。

定位
----
这是「能力槽」页在配置层的**单一真相源**。对外只暴露：

  · 每个槽位「当前生效模型 / 健康状态 / 克隆状态」—— 即「生效同步」；
  · 每个槽位「推荐服务商清单（预填 URL+模型+能力标签）」—— 喂 UI 与聊天页；
  · 一个写入口 `set_capability_slot` —— 把用户的选择落回各链路**已有的**配置节点，
    不发明新节点、不动 95 个 BK 桥方法名。

2026-10-10 的两处结构性变化
---------------------------
1. **LLM + VLM 合并为一个 `chat` 槽**。依据：models.dev 全量实测 8,161 个在售模型中
   60% 支持图像输入，新旗舰几乎全多模态 → 分开配等于让用户为同一家、同一个 Key
   填两遍。合并后按**模型能力标签**（视觉/推理/工具/视频/听/PDF）区分。
   ⚠️ 关键：底层 `model_registry` 仍是「chat / vision」两个**独立配置节点**
   （chat→`text_chat.providers`，vision→`vision_models`）。所以写 `chat` 槽时，
   若所选模型带视觉，**必须同时把模型镜像进 `vision_models`**，否则看屏管线读不到。

2. **新增 `aliyun` 一键槽**（第 4 个槽，UI 上做成全宽大横栏、置顶）。一个百炼 Key
   同时把 对话模型 / TTS / 双工 三个槽配齐。

与 model_registry 的关系（承上）
---------------------------------
`llm.model_registry` 是「位置×用途 6 槽位」的底层写真相源（read_slot / set_slot）。
本模块是「用户视角 4 能力槽」的**上层聚合 + 推荐清单 + 生效同步**。

纯模块：不依赖 webapp 重类，可独立单测（见 tools/test_capability_slots.py）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

# 【2026-10-17 修】_set_duplex 的别名归一分支里用了 logger.info，但本模块从未定义
# logger —— 用户从引导页「一键配置」传 "bailian"/"aliyun" 等别名时必然 NameError，
# 表现为一键配置失败、双工槽没配上（拨号才被 no_cloud_duplex 拦住）。
logger = logging.getLogger(__name__)

# 兼容两种导入上下文：app 里 src 是顶层包（用 ..llm）；单测里 src 在 sys.path（用 llm）。
try:
    from ..llm.model_registry import (
        LOC_CLOUD, LOC_LOCAL, SLOT_CHAT, SLOT_VISION,
        read_slot, set_slot, _dig,
    )
except ImportError:  # 单测上下文：src 在 sys.path，llm 为顶层包
    from llm.model_registry import (
        LOC_CLOUD, LOC_LOCAL, SLOT_CHAT, SLOT_VISION,
        read_slot, set_slot, _dig,
    )
from .cloud_tts import _cloud_tts_providers

__all__ = [
    "CAPABILITY_SLOTS",
    "SLOT_ORDER",
    "CHAT_PROVIDERS",
    "ALIYUN_ONECLICK",
    "LLM_PROVIDERS",
    "VLM_PROVIDERS",
    "TTS_PROVIDERS",
    "DUPLEX_PROVIDERS",
    "read_capability_slots",
    "set_capability_slot",
]


# ===== 四能力槽元信息 =====
# width="full" → UI 把它渲染成置顶的全宽大横栏（阿里云一键槽）。
# oneclick=True → 这个槽的写入会一次命中多个下游槽。

CAPABILITY_SLOTS = {
    "aliyun": {
        "title": "阿里云一键",
        "desc": "一个百炼 Key，一次配齐 对话 / 看屏 / 语音合成 / 实时通话",
        "icon": "sparkles",
        "allows_custom": False,
        "clone": None,
        "oneclick": True,
        "width": "full",
        "registry_slot": None,
    },
    "chat": {
        "title": "对话模型",
        "desc": "文字对话 · 看屏 / 图像理解（LLM + VLM 合并）",
        "icon": "brain",
        "allows_custom": True,
        "clone": None,
        "oneclick": False,
        "width": "half",
        "registry_slot": SLOT_CHAT,
    },
    "tts": {
        "title": "TTS 语音合成",
        "desc": "消息播报 · 克隆音色",
        "icon": "volume",
        "allows_custom": True,
        # 双工克隆 ≠ TTS 克隆：TTS 自定义**不能克隆**，只有预设可克隆
        "clone": "split",
        "oneclick": False,
        "width": "half",
        "registry_slot": None,               # TTS 走 cloud_tts 多 provider
    },
    "duplex": {
        "title": "双工 实时通话",
        "desc": "全双工语音 · 角色通话音色",
        "icon": "phone",
        "allows_custom": False,              # 双工不开放自定义（协议各异，须逐家适配）
        "clone": "split",
        "oneclick": False,
        "width": "half",
        "registry_slot": None,               # 双工走 cloud_omni / proactive
    },
}

#: UI 渲染顺序：阿里云一键在最上面（全宽），其余三槽平铺。
SLOT_ORDER = ["aliyun", "chat", "tts", "duplex"]


# ===== 能力标签词汇（模型级，用于合并槽内区分）=====
# caps 取值：视觉 / 视频 / 听 / 说 / PDF / 推理 / 工具
_VISION_CAPS = ("视觉", "视频")   # 带任一即"能看屏/看图"


def _model_caps_has_vision(caps: list) -> bool:
    return any(c in _VISION_CAPS for c in (caps or []))


# ===== 对话模型推荐服务商（合并 LLM + VLM，模型带能力标签）=====
# 真相源口径：models.dev（2026-10-10 实查）。models 元素：
#   {"id","caps":[...],"ctx","note"}   —— note/ctx 可选。

CHAT_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "cn": "深度求索", "logo": "deepseek", "rank": 1,
     "base_url": "https://api.deepseek.com/v1",
     "hint": "现行是 deepseek-flash（=V4.1 Flash，已支持看图）；deepseek-v4-flash / v3.1 已废弃",
     "models": [
         {"id": "deepseek-flash", "caps": ["视觉", "推理", "工具"], "ctx": "1M", "note": "= V4.1 Flash"},
         {"id": "deepseek-v4-pro", "caps": ["推理", "工具"], "ctx": "1M", "note": "纯文本旗舰"},
     ]},
    {"id": "bailian", "name": "阿里云百炼 Qwen", "cn": "通义千问", "logo": "alibaba", "rank": 2,
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "hint": "全能 Key：聊天 / 看屏 / 双工 / 克隆共用一个 Key",
     "models": [
         # 2026-10-12 按百炼官方「文本生成」模型列表实查扩充（help.aliyun.com，2026-09-28 更新版）。
         # 注意：qwen3.8-omni-flash 等 omni 系不入本列表——它们是"能听会说"的多模态模型，
         # 归双工槽（-realtime 变体）；前端 chat 下拉也会过滤 id 含 omni / caps 含「说」的模型。
         {"id": "qwen3.8-max", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M", "note": "旗舰·推荐"},
         {"id": "qwen3.7-max", "caps": ["推理", "工具"], "ctx": "256K"},
         {"id": "qwen3.7-plus", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
         {"id": "qwen3.6-plus", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
         {"id": "qwen3.5-plus", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
         {"id": "qwen-plus", "caps": ["推理", "工具"], "ctx": "—", "note": "经典均衡"},
         {"id": "qwen3.8-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K", "note": "快省·推荐"},
         {"id": "qwen3.7-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K", "note": "快省"},
         {"id": "qwen3.6-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
         {"id": "qwen3.5-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
         {"id": "qwen-flash", "caps": ["推理", "工具"], "ctx": "—", "note": "极速"},
         {"id": "qwen-turbo", "caps": ["工具"], "ctx": "—", "note": "极速（模型存在，仅限流）"},
         {"id": "qwen3-vl-plus", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M", "note": "看屏专用·当前唯一可用视觉模型"},
         # 【2026-10-16 清理·死模型】移除 4 个实测 404 model_not_found 的条目：
         #   qwq-plus / qwen3-vl-flash / qwen-vl-max / qwen-vl-plus
         # 依据：逐个用真实 1-token 请求打 /chat/completions 验证。
         # 注意 qwen-turbo 只返回 429（限流）→ 模型存在，保留；qwen-plus-character
         # 虽不在 /models 列表里但实测 200 可用，同样保留——"不在列表"≠"不能用"。
         # 同一把百炼 Key 可直接调用的第三方模型（百炼平台托管）
         {"id": "deepseek-v4.1-flash", "caps": ["视觉", "推理", "工具"], "ctx": "—", "note": "百炼托管"},
         {"id": "deepseek-v4-pro", "caps": ["推理", "工具"], "ctx": "—", "note": "百炼托管"},
         {"id": "glm-5.2", "caps": ["推理", "工具"], "ctx": "—", "note": "百炼托管"},
     ]},
    {"id": "zhipu", "name": "智谱 AI", "cn": "GLM", "logo": "zhipuai", "rank": 3,
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "hint": "GLM 有免费档（glm-4.7-flash）；5.3 系列带视频/PDF",
     "models": [
         {"id": "glm-5.3-flash", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M"},
         {"id": "glm-5.3-flashx", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M"},
         {"id": "glm-4.7-flash", "caps": ["推理", "工具"], "ctx": "200K", "note": "免费档"},
     ]},
    {"id": "moonshot", "name": "月之暗面", "cn": "Kimi", "logo": "moonshotai", "rank": None,
     "base_url": "https://api.moonshot.cn/v1",
     "hint": "全系多模态；1M 上下文",
     "models": [
         {"id": "kimi-k3", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M"},
         {"id": "kimi-k2.6", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "262K"},
     ]},
    {"id": "volcengine", "name": "火山方舟", "cn": "豆包 Doubao", "logo": "volcengine", "rank": None,
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "hint": "豆包 2.x 全系多模态",
     "models": [
         {"id": "doubao-seed-2-1-pro-260628", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "256K"},
         {"id": "doubao-seed-2-1-turbo-260628", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "256K"},
     ]},
    {"id": "openai", "name": "OpenAI", "cn": "", "logo": "openai", "rank": None,
     "base_url": "https://api.openai.com/v1",
     "hint": "需网络可达；gpt-6 全系看图",
     "models": [
         {"id": "gpt-6.1-sol", "caps": ["视觉", "PDF", "推理", "工具"], "ctx": "1.05M"},
         {"id": "gpt-6-luna", "caps": ["视觉", "PDF", "推理", "工具"], "ctx": "1.05M"},
     ]},
    {"id": "anthropic", "name": "Anthropic", "cn": "Claude", "logo": "anthropic", "rank": None,
     "base_url": "https://api.anthropic.com/v1",
     "hint": "全系视觉 + PDF",
     "models": [
         {"id": "claude-opus-5-5", "caps": ["视觉", "PDF", "推理", "工具"], "ctx": "1M"},
         {"id": "claude-sonnet-5-5", "caps": ["视觉", "PDF", "推理", "工具"], "ctx": "1M"},
     ]},
    {"id": "google", "name": "Google", "cn": "Gemini", "logo": "google", "rank": None,
     "base_url": "https://generativelanguage.googleapis.com/v1beta",
     "hint": "全模态：图 / 视频 / 音频 / PDF",
     "models": [
         {"id": "gemini-3.8-flash", "caps": ["视觉", "视频", "听", "PDF", "推理", "工具"], "ctx": "1M"},
     ]},
    {"id": "xai", "name": "xAI", "cn": "Grok", "logo": "xai", "rank": None,
     "base_url": "https://api.x.ai/v1",
     "hint": "视觉 + PDF；500K 上下文",
     "models": [
         {"id": "grok-4.7", "caps": ["视觉", "PDF", "推理", "工具"], "ctx": "500K"},
     ]},
    {"id": "siliconflow", "name": "硅基流动", "cn": "SiliconFlow", "logo": "siliconflow", "rank": None,
     "base_url": "https://api.siliconflow.cn/v1",
     "hint": "一个 Key 用多家模型，部分有免费额度",
     "models": [
         {"id": "Pro/moonshotai/Kimi-K2.6", "caps": ["视觉", "推理", "工具"], "ctx": "262K"},
         {"id": "Qwen/Qwen3.5-122B-A10B", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "262K"},
     ]},
    {"id": "minimax", "name": "MiniMax", "cn": "", "logo": "minimax", "rank": None,
     "base_url": "https://api.minimaxi.com/v1",
     "hint": "M3 起多模态；域名是 api.minimaxi.com（旧 api.minimax.chat 已停）",
     "models": [
         {"id": "MiniMax-M3", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M"},
         {"id": "MiniMax-M2.7", "caps": ["推理", "工具"], "ctx": "205K"},
     ]},
    {"id": "stepfun", "name": "阶跃星辰", "cn": "StepFun", "logo": "stepfun", "rank": None,
     "base_url": "https://api.stepfun.com/v1",
     "hint": "Step 5 多模态；另有 TTS / ASR 专模",
     "models": [
         {"id": "step-5-preview", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M"},
     ]},
    {"id": "openrouter", "name": "OpenRouter", "cn": "聚合网关", "logo": "openrouter", "rank": None,
     "base_url": "https://openrouter.ai/api/v1",
     "hint": "聚合 390+ 模型；/models 免 Key",
     "models": [
         {"id": "openrouter/auto", "caps": ["视觉", "视频", "听", "PDF", "推理", "工具"], "ctx": "2M"},
     ]},
    {"id": "ollama", "name": "本地 Ollama", "cn": "离线", "logo": "ollama", "rank": None, "local": True,
     "base_url": "http://127.0.0.1:11434",
     "hint": "完全免费离线；需本机已 pull",
     "models": [
         {"id": "qwen3.5:27b", "caps": ["视觉", "推理", "工具"], "ctx": "以本机为准"},
         {"id": "qwen3:14b", "caps": ["推理", "工具"], "ctx": "以本机为准", "note": "纯文本"},
     ]},
]

# 旧名兼容（内容由 CHAT_PROVIDERS 派生，避免第二份真相漂移）：
#   LLM_PROVIDERS = 全部（含把模型名展平，兼容旧 string 形态的消费方）
#   VLM_PROVIDERS = 只含有视觉模型的那些（同样展平）
LLM_PROVIDERS = [
    {"id": p["id"], "name": p["name"], "rank": p.get("rank"), "base_url": p["base_url"],
     "models": [m["id"] for m in p["models"]], "clone": None,
     "local": bool(p.get("local")), "hint": p.get("hint", "")}
    for p in CHAT_PROVIDERS
]
VLM_PROVIDERS = [
    {"id": p["id"], "name": p["name"], "rank": p.get("rank"), "base_url": p["base_url"],
     "models": [m["id"] for m in p["models"] if _model_caps_has_vision(m.get("caps"))],
     "clone": None, "local": bool(p.get("local")), "hint": p.get("hint", "")}
    for p in CHAT_PROVIDERS if any(_model_caps_has_vision(m.get("caps")) for m in p["models"])
]


# ===== 阿里云一键槽 =====
# 一个百炼 Key 覆盖三类下游；模型默认取"能一起干活"的组合（带视觉的对话模型 + 克隆 TTS + 实时双工）。

ALIYUN_ONECLICK = [{
    "id": "bailian",
    "name": "阿里云百炼",
    "cn": "通义千问 Qwen（一个 Key 全通）",
    "logo": "alibaba",
    "rank": 1,
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "hint": "同一把百炼 Key 同时用于：对话/看屏、语音合成(克隆)、实时通话",
    "chat_models": [
        # 2026-10-12 按百炼官方模型列表扩充（与 CHAT_PROVIDERS.bailian 同口径）；
        # omni 系不入 chat（归双工槽 -realtime），与前端下拉过滤规则一致。
        {"id": "qwen3.8-max", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M",
         "note": "旗舰·推荐"},
        {"id": "qwen3.7-max", "caps": ["推理", "工具"], "ctx": "256K"},
        {"id": "qwen3.7-plus", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
        {"id": "qwen3.6-plus", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
        {"id": "qwen3.5-plus", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
        {"id": "qwen-plus", "caps": ["推理", "工具"], "ctx": "—", "note": "经典均衡"},
        {"id": "qwen3.8-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K", "note": "快省·推荐"},
        {"id": "qwen3.7-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K", "note": "快省"},
        {"id": "qwen3.6-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
        {"id": "qwen3.5-flash", "caps": ["视觉", "推理", "工具"], "ctx": "256K"},
        {"id": "qwen-flash", "caps": ["推理", "工具"], "ctx": "—", "note": "极速"},
        {"id": "qwen-turbo", "caps": ["工具"], "ctx": "—", "note": "极速（模型存在，仅限流）"},
        {"id": "qwen3-vl-plus", "caps": ["视觉", "视频", "推理", "工具"], "ctx": "1M", "note": "看屏专用·当前唯一可用视觉模型"},
        # 【2026-10-16 清理·死模型】同上：移除 qwq-plus / qwen3-vl-flash / qwen-vl-max / qwen-vl-plus
        {"id": "deepseek-v4.1-flash", "caps": ["视觉", "推理", "工具"], "ctx": "—", "note": "百炼托管"},
        {"id": "deepseek-v4-pro", "caps": ["推理", "工具"], "ctx": "—", "note": "百炼托管"},
        {"id": "glm-5.2", "caps": ["推理", "工具"], "ctx": "—", "note": "百炼托管"},
    ],
    # 【2026-10-17 实测定稿】逐一实测：仅 qwen3-tts-flash 可用（官方现役，
    # 实测 200）；qwen-audio-3.0-tts-plus/flash 与 cosyvoice 全系列均已 400/418。
    "tts_models": [
        {"id": "qwen3-tts-flash", "caps": ["说"], "ctx": "—", "note": "推荐"},
    ],
    # 【2026-10-17】只列支持**声音克隆**的 omni 系（clone 能力见 TTS_PROVIDERS 的 clone 标记）；
    # qwen-audio-*-realtime 只支持 longan* 预置音色，用不了用户的克隆声，故不列。
    "duplex_models": [
        {"id": "qwen3.8-omni-flash-realtime", "caps": ["听", "说", "视觉", "克隆"], "ctx": "—", "note": "推荐"},
        {"id": "qwen3.5-omni-plus-realtime", "caps": ["听", "说", "视觉", "克隆"], "ctx": "—", "note": "均衡"},
        {"id": "qwen3.5-omni-flash-realtime", "caps": ["听", "说", "视觉", "克隆"], "ctx": "—"},
    ],
}]

# v10：默认对话模型从 qwen3.8-omni-flash 改为 qwen3.8-max——
# omni 系归双工槽，chat 槽（含阿里云一键的对话下拉）不该默认到会被前端过滤掉的模型。
_ALIYUN_DEFAULT_CHAT = "qwen3.8-max"
_ALIYUN_DEFAULT_TTS = "qwen-audio-3.0-tts-plus"
_ALIYUN_DEFAULT_DUPLEX = "qwen3.8-omni-flash-realtime"   # 【2026-10-17】换成实测可克隆+出声的型号


# ===== 推荐服务商清单（TTS / 双工）=====
# clone: 'y' 可克隆 / 'w' 克隆待验证 / 'n' 不可克隆 / None 不适用
# adapted: 双工是否真适配（False = 规划中，UI 置灰不可选）

TTS_PROVIDERS = [
    {"id": "dashscope_cosy", "name": "阿里云 CosyVoice", "logo": "alibaba", "rank": 1, "clone": "y",
     "base_url": "https://dashscope.aliyuncs.com/api-ws/v1/inference",
     "models": [
         {"id": "qwen-audio-3.0-tts-plus", "caps": ["说"], "ctx": "—"},
         {"id": "cosyvoice-v3.5-plus", "caps": ["说"], "ctx": "—"},
         {"id": "cosyvoice-v3.5-flash", "caps": ["说"], "ctx": "—"},
     ],
     "hint": "克隆走 voice-enrollment；复刻音色有有效期"},
    {"id": "minimax", "name": "MiniMax Speech", "logo": "minimax", "rank": 2, "clone": "y",
     "base_url": "https://api.minimaxi.com/v1/t2a_v2",
     "models": [
         {"id": "speech-02-hd", "caps": ["说"], "ctx": "—"},
         {"id": "speech-02-turbo", "caps": ["说"], "ctx": "—"},
     ],
     "needs_group": True,
     "hint": "除 Key 外还需 GroupId；域名 api.minimaxi.com"},
    {"id": "fish_audio", "name": "Fish Audio", "logo": "fishaudio", "rank": 3, "clone": "y",
     "base_url": "https://api.fish.audio/v1/tts",
     "models": [{"id": "(克隆模型 _id)", "caps": ["说"], "ctx": "—"}],
     "hint": "模型名填克隆得到的 _id；GET /v1/model?self=true 查"},
    {"id": "openai_tts", "name": "OpenAI TTS", "logo": "openai", "rank": None, "clone": "n",
     "base_url": "https://api.openai.com/v1/audio/speech",
     "models": [
         {"id": "gpt-4o-mini-tts", "caps": ["说"], "ctx": "—"},
         {"id": "tts-1", "caps": ["说"], "ctx": "—"},
     ],
     "hint": "不可克隆，仅基础合成"},
]

    # 【2026-10-17】只列**支持声音克隆**的 omni 系（qwen-audio-*-realtime 用不了用户克隆声）
DUPLEX_PROVIDERS = [
    {"id": "bailian_qwen_omni", "name": "阿里云 百炼 Qwen-Omni Realtime", "logo": "alibaba", "rank": 1,
     "adapted": True, "clone": "y", "provider_key": "dashscope_cosy",
     "base_url": "https://dashscope.aliyuncs.com",
     "models": [
          {"id": "qwen3.8-omni-flash-realtime", "caps": ["听", "说", "视觉", "克隆"], "ctx": "—"},
          {"id": "qwen3.5-omni-plus-realtime", "caps": ["听", "说", "视觉", "克隆"], "ctx": "—"},
          {"id": "qwen3.5-omni-flash-realtime", "caps": ["听", "说", "视觉", "克隆"], "ctx": "—"},
     ],
     "hint": "实时可克隆（CosyVoice）；一个 Key 下拉切版本"},
    {"id": "local_comni", "name": "本地 Comni（llama.cpp-omni）", "logo": "comni", "rank": 2,
     "adapted": True, "clone": "n", "local": True,
     "base_url": "https://localhost:8006",
     "models": [{"id": "MiniCPM-o 4.5 (Q4_K_M)", "caps": ["听", "说", "视觉"], "ctx": "—"}],
     "hint": "免费离线；克隆音色要另接 TTS 槽"},
    # —— 规划中（置灰，UI 不可选）——
    {"id": "volcano", "name": "火山引擎 豆包 Realtime", "logo": "volcengine", "rank": 3,
     "adapted": False, "clone": "y",
     "base_url": "https://openspeech.bytedance.com",
     "models": [{"id": "doubao-realtime-sc-2.0", "caps": ["听", "说"], "ctx": "—"}],
     "hint": "适配中·暂不可选；需新建 RTC 适配器 + 企业实名"},
    {"id": "zhipu_duplex", "name": "智谱 GLM-Realtime", "logo": "zhipuai", "rank": None,
     "adapted": False, "clone": "w",
     "base_url": "https://open.bigmodel.cn/api/paas/v4/realtime",
     "models": [{"id": "glm-realtime", "caps": ["听", "说"], "ctx": "—"}],
     "hint": "适配中·克隆待验证"},
    {"id": "minimax_duplex", "name": "MiniMax Realtime", "logo": "minimax", "rank": None,
     "adapted": False, "clone": "w",
     "base_url": "https://api.minimaxi.com/v1",
     "models": [{"id": "realtime", "caps": ["听", "说"], "ctx": "—"}],
     "hint": "适配中·自定义音色(训练)"},
]

_PROVIDER_CATALOG = {
    "aliyun": ALIYUN_ONECLICK,
    "chat": CHAT_PROVIDERS,
    "tts": TTS_PROVIDERS,
    "duplex": DUPLEX_PROVIDERS,
}


# ===== 读：计算每个能力槽的生效状态 + 推荐服务商标注 =====

def _annotate_providers(raw: dict, cap: str) -> list[dict]:
    """把推荐服务商清单逐张标注 `configured`（raw 里是否已有 Key）与 `active` 标记。"""
    catalog = _PROVIDER_CATALOG.get(cap, [])
    active = _active_identity(raw, cap)
    out = []
    for p in catalog:
        card = dict(p)
        card["configured"] = _provider_configured(raw, cap, p)
        card["active"] = (active.get("provider") == p["id"])
        out.append(card)
    out.sort(key=lambda c: (0 if c.get("configured") else 1,
                            c.get("rank") if c.get("rank") is not None else 99))
    return out


def _active_identity(raw: dict, cap: str) -> dict:
    """返回当前生效的 {provider, model, loc, ...} —— 仅用于前端高亮，不要求精确 vendor 映射。"""
    meta = CAPABILITY_SLOTS[cap]
    if cap == "chat":
        # 云端优先；没云端再看本地
        for loc in (LOC_CLOUD, LOC_LOCAL):
            s = read_slot(raw, loc, meta["registry_slot"])
            if s.get("ready") and s.get("model"):
                vis = read_slot(raw, LOC_CLOUD, SLOT_VISION) if loc == LOC_CLOUD \
                    else read_slot(raw, LOC_LOCAL, SLOT_VISION)
                return {"provider": _guess_vendor(cap, s), "model": s["model"], "loc": loc,
                        "vision_model": str(vis.get("model") or ""),
                        "vision_ready": bool(vis.get("ready"))}
        return {}
    if cap == "aliyun":
        co = _dig(raw, "cloud_omni", {}) or {}
        chat_ok = False
        for s in (_dig(raw, "text_chat.providers", []) or []):
            if isinstance(s, dict) and "dashscope.aliyuncs.com" in str(s.get("base_url") or "").lower() \
                    and str(s.get("api_key") or "").strip() and str(s.get("model") or "").strip():
                chat_ok = True
        if chat_ok:
            return {"provider": "bailian", "model": str(co.get("model") or "qwen3.8-max"), "loc": LOC_CLOUD}
        return {}
    if cap == "tts":
        sub = str(_dig(raw, "voice.clone.sub", "") or "")
        if sub == "cloud":
            provs = _cloud_tts_providers(raw)
            for pid, cfg in provs.items():
                if str(cfg.get("api_key") or "").strip():
                    return {"provider": pid, "model": str(cfg.get("model_name") or ""), "loc": "cloud"}
        return {}
    if cap == "duplex":
        dm = str(_dig(raw, "proactive.duplex_mode", "") or "")
        if dm in ("local_omni", "local"):
            return {"provider": "local_comni", "model": "MiniCPM-o 4.5", "loc": "local"}
        co = _dig(raw, "cloud_omni", {}) or {}
        if str(co.get("api_key") or "").strip() and str(co.get("model") or "").strip():
            return {"provider": "bailian_qwen_omni", "model": str(co.get("model")), "loc": "cloud"}
        return {}
    return {}


def _guess_vendor(cap: str, slot_state: dict) -> str:
    """从某个 registry 槽位的 base_url 反猜 vendor id。"""
    url = str(slot_state.get("base_url") or "").lower()
    for p in _PROVIDER_CATALOG.get(cap, []):
        if p.get("local"):
            continue
        if p["base_url"].split("://")[-1].split("/")[0] in url:
            return p["id"]
    return ""


def _provider_configured(raw: dict, cap: str, p: dict) -> bool:
    """best-effort：判断 raw 里是否已为该 vendor 配了 Key/引擎。"""
    pid = p["id"]
    if cap == "aliyun":
        co = _dig(raw, "cloud_omni", {}) or {}
        if not str(co.get("api_key") or "").strip():
            return False
        for s in (_dig(raw, "text_chat.providers", []) or []):
            if isinstance(s, dict) and "dashscope.aliyuncs.com" in str(s.get("base_url") or "").lower() \
                    and str(s.get("api_key") or "").strip():
                return True
        return False
    if cap == "chat":
        if p.get("local"):
            return bool(str(_dig(raw, "ollama.model", "") or "").strip())
        providers = _dig(raw, "text_chat.providers", []) or []
        host = p["base_url"].split("://")[-1].split("/")[0]
        for pr in providers:
            if not isinstance(pr, dict):
                continue
            if host in str(pr.get("base_url") or "").lower() and str(pr.get("api_key") or "").strip():
                return True
        # 也被视觉侧配过 → 视为已配（合并槽口径）
        vm = _dig(raw, "vision_models", {}) or {}
        for e in (vm.get("models") or []):
            if isinstance(e, dict) and host in str(e.get("base_url") or "").lower() \
                    and str(e.get("api_key") or "").strip():
                return True
        return False
    if cap == "tts":
        provs = _cloud_tts_providers(raw)
        cfg = provs.get(pid)
        return bool(cfg and str(cfg.get("api_key") or "").strip())
    if cap == "duplex":
        if pid == "local_comni":
            return str(_dig(raw, "proactive.duplex_mode", "") or "") in ("local_omni", "local")
        if pid == "bailian_qwen_omni":
            co = _dig(raw, "cloud_omni", {}) or {}
            return bool(str(co.get("api_key") or "").strip())
        return False
    return False


def _slot_state(raw: dict, cap: str) -> tuple[str, str]:
    """返回 (state, state_text)，state ∈ ok/warn/bad/none。"""
    meta = CAPABILITY_SLOTS[cap]
    if cap == "chat":
        cloud = read_slot(raw, LOC_CLOUD, meta["registry_slot"])
        local = read_slot(raw, LOC_LOCAL, meta["registry_slot"])
        if cloud.get("ready") or local.get("ready"):
            # 顺带看视觉侧：合并槽的"完整度"取决于视觉是否也通
            vis = read_slot(raw, LOC_CLOUD, SLOT_VISION)
            if vis.get("ready") or read_slot(raw, LOC_LOCAL, SLOT_VISION).get("ready"):
                return "ok", "已连通"
            return "warn", "聊天已通·看屏未配"
        if any(_provider_configured(raw, cap, p) for p in _PROVIDER_CATALOG[cap]):
            return "warn", "未激活"
        return "none", "未配置"
    if cap == "aliyun":
        parts = _aliyun_parts(raw)
        n = sum(1 for v in parts.values() if v)
        if n == 3:
            return "ok", "三槽全通"
        if n > 0:
            return "warn", f"已配 {n}/3"
        return "none", "未配置"
    if cap == "tts":
        sub = str(_dig(raw, "voice.clone.sub", "") or "")
        muted = _dig(raw, "voice.muted", False)
        if muted:
            return "bad", "已静音"
        provs = _cloud_tts_providers(raw)
        cloud_keyed = any(str(c.get("api_key") or "").strip() for c in provs.values())
        if sub == "cloud":
            return ("ok", "已连通") if cloud_keyed else ("warn", "已选云端·未配 Key")
        if sub in ("gpt-sovits", "local", "pyttsx3", "mimo_tts", "voxcpm", "cosyvoice", "qwen3tts"):
            return "ok", "本地引擎"
        if cloud_keyed:
            return "warn", "已配云端·未启用"
        return "none", "未配置"
    if cap == "duplex":
        dm = str(_dig(raw, "proactive.duplex_mode", "") or "")
        if dm in ("local_omni", "local"):
            return "ok", "本地引擎"
        co = _dig(raw, "cloud_omni", {}) or {}
        if str(co.get("api_key") or "").strip() and str(co.get("model") or "").strip():
            return "ok", "已连通"
        if str(co.get("api_key") or "").strip():
            return "warn", "未选模型"
        return "none", "未配置"
    return "none", "未配置"


def _aliyun_parts(raw: dict) -> dict:
    """阿里云一键槽的三段状态：chat / tts / duplex 是否已由百炼 Key 打通。"""
    chat_ok = False
    for s in (_dig(raw, "text_chat.providers", []) or []):
        if isinstance(s, dict) and "dashscope.aliyuncs.com" in str(s.get("base_url") or "").lower() \
                and str(s.get("api_key") or "").strip() and str(s.get("model") or "").strip():
            chat_ok = True
    muted = _dig(raw, "voice.muted", False)
    tts_cfg = (_cloud_tts_providers(raw) or {}).get("dashscope_cosy") or {}
    # tts 段「已连通」须同时满足：云端 Key 已配 且 未静音（与单槽 _slot_state 的 muted 闸一致，
    # 否则会出现「阿里云三路通」但独立 TTS 卡显示「已静音」的内部矛盾）。
    tts_ok = bool(str(tts_cfg.get("api_key") or "").strip()) and not muted
    co = _dig(raw, "cloud_omni", {}) or {}
    duplex_ok = bool(str(co.get("api_key") or "").strip() and str(co.get("model") or "").strip())
    return {"chat": chat_ok, "tts": tts_ok, "duplex": duplex_ok}


def _clone_state(raw: dict, cap: str) -> dict:
    """双工/TTS 克隆状态（其它槽返回 supported=False）。"""
    if cap == "tts":
        sub = str(_dig(raw, "voice.clone.sub", "") or "")
        profiles = _dig(raw, "voice_profiles", []) or []
        cloned = [p for p in profiles
                  if isinstance(p, dict) and str(p.get("engine") or "") in
                  ("dashscope_cosy", "minimax", "fish_audio")]
        return {"supported": True, "sub": sub,
                "cloned_count": len(cloned),
                "configured": any(_provider_configured(raw, "tts", p)
                                  for p in TTS_PROVIDERS if p.get("clone") == "y")}
    if cap == "duplex":
        co = _dig(raw, "cloud_omni", {}) or {}
        return {"supported": True,
                "cloned": bool(str(co.get("api_key") or "").strip()),
                "note": "双工克隆=实时通话音色；TTS 克隆=消息播报音色，二者独立"}
    return {"supported": False}


def read_capability_slots(raw: dict) -> dict:
    """计算全部 4 个能力槽的中央状态（生效同步核心入口）。

    返回：
      { "slots": { cap_id: {title,desc,icon,allows_custom,oneclick,width,
                            state,state_text,active,providers[],clone} },
        "order": ["aliyun","chat","tts","duplex"],
        "health": { cap_id: state } }
    """
    slots: dict[str, Any] = {}
    health: dict[str, str] = {}
    for cap, meta in CAPABILITY_SLOTS.items():
        state, state_text = _slot_state(raw, cap)
        slots[cap] = {
            "id": cap,
            "title": meta["title"],
            "desc": meta["desc"],
            "icon": meta["icon"],
            "allows_custom": meta["allows_custom"],
            "oneclick": meta.get("oneclick", False),
            "width": meta.get("width", "half"),
            "state": state,
            "state_text": state_text,
            "active": _active_identity(raw, cap),
            "providers": _annotate_providers(raw, cap),
            "clone": _clone_state(raw, cap),
        }
        if cap == "aliyun":
            slots[cap]["parts"] = _aliyun_parts(raw)
        health[cap] = state
    return {"slots": slots, "order": list(SLOT_ORDER), "health": health}


# ===== 写：把用户选择落回各链路已有配置节点 =====

def set_capability_slot(raw: dict, cap: str, payload: dict) -> dict:
    """写入一个能力槽的选择。

    payload 字段（前端按需传，未传的不改）：
      provider      : 推荐商家 id（或 'custom'）
      model         : 选中的模型名
      api_key       : 该商家的 Key（空字符串=不改）
      base_url      : 自定义/覆盖 URL
      group_id      : MiniMax 等需要
      loc           : 'cloud' | 'local'（chat/duplex 用）
      is_custom     : True 表示走自定义（仅 chat/tts）
      custom_*      : 自定义单模型+Key+URL
      aliyun 槽额外  : chat_model / tts_model / duplex_model（都可缺省，用推荐默认）

    返回 {ok, cap, message, changed}。绝不抛异常。
    """
    try:
        if cap in ("chat", "llm", "vlm"):
            return _set_chat(raw, "chat", payload)
        if cap == "aliyun":
            return _set_aliyun(raw, payload)
        if cap == "tts":
            return _set_tts(raw, payload)
        if cap == "duplex":
            return _set_duplex(raw, payload)
        return {"ok": False, "cap": cap, "message": f"未知能力槽: {cap}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "cap": cap, "message": f"写入失败: {e}"}


def _inherit_key(raw: dict, target: dict) -> str:
    """给一个缺 api_key 的端口，从**同主机**的其它配置里继承一把 Key。

    为什么需要：像「图文模型」这种"只换模型、不重填 Key"的入口（前端只传 provider+model），
    若目标端口还没有 Key，原逻辑会写出一条 `api_key: ""` 的端口 —— 后果是聊天必 400/401，
    而且前端完全看不出原因（经典静默坑）。这里从 text_chat.providers / vision_models 里
    同一 host 的条目继承，用户不必重复粘贴。
    """
    host = str(target.get("base_url") or "").split("://")[-1].split("/")[0].strip().lower()
    if not host:
        return ""
    for pr in (_dig(raw, "text_chat.providers", []) or []):
        if not isinstance(pr, dict) or pr is target:
            continue
        if host in str(pr.get("base_url") or "").lower():
            k = str(pr.get("api_key") or "").strip()
            if k:
                target["api_key"] = k
                return k
    for e in ((_dig(raw, "vision_models", {}) or {}).get("models") or []):
        if isinstance(e, dict) and host in str(e.get("base_url") or "").lower():
            k = str(e.get("api_key") or "").strip()
            if k:
                target["api_key"] = k
                return k
    return ""


def _set_chat(raw: dict, cap: str, payload: dict) -> dict:
    """对话模型槽写入：写 chat 节点；若模型带视觉 → **同时镜像进 vision_models**。

    这条镜像就是"合并 LLM+VLM"在配置层的落地：看屏管线读的是 `vision_models`，
    不镜像的话合并槽选了视觉模型、看屏依然用旧模型（静默不一致）。
    """
    meta = CAPABILITY_SLOTS[cap]
    reg_slot = meta["registry_slot"]
    loc = str(payload.get("loc") or "cloud")
    provider = str(payload.get("provider") or "")
    model = str(payload.get("model") or "").strip()
    api_key = payload.get("api_key")
    base_url = str(payload.get("base_url") or "").strip()

    if payload.get("is_custom"):
        return _upsert_custom_provider(raw, cap, payload)

    if loc == LOC_LOCAL:
        r = set_slot(raw, LOC_LOCAL, reg_slot, model=model or "qwen3:14b",
                     base_url=base_url or "http://127.0.0.1:11434")
        # 合并槽：本地也镜像 vision_model（若该模型带视觉）
        if _looks_vision(provider, model):
            set_slot(raw, LOC_LOCAL, SLOT_VISION, model=model,
                     base_url=base_url or "http://127.0.0.1:11434")
        return r

    # 云端：找/建对应厂商端口，写 model + key
    tc = raw.setdefault("text_chat", {})
    providers = tc.get("providers")
    if not isinstance(providers, list):
        providers = []
        tc["providers"] = providers
    host = ""
    cat_base = ""
    for p in CHAT_PROVIDERS:
        if p["id"] == provider:
            host = p["base_url"].split("://")[-1].split("/")[0]
            cat_base = str(p.get("base_url") or "")
    if not host and base_url:
        host = base_url.split("://")[-1].split("/")[0]
    target = None
    for pr in providers:
        if not isinstance(pr, dict):
            continue
        if host and host in str(pr.get("base_url") or "").lower():
            target = pr
            break
    if target is None:
        pid = "cap_" + (provider or "custom") + "_chat"
        # 【2026-10-16 修复·空 base_url 写出坏端口】
        # 引导页从服务商卡片点「启用」时前端传的是 base_url:""（ui_redesign_modelapi.html），
        # 旧实现直接把这个空值写进新端口 → 端口无地址 → 聊天路由一条客户端都建不出来
        # （实测：端口数=0，日志"openai 文本模型条目缺少 base_url/model"），
        # 但接口回 success、槽位还显示"已连通"，用户看到的是"绑好了却聊不了天"。
        # 正解：新建端口时回退到目录里该服务商已配置的 base_url。
        target = {"id": pid, "provider": "openai", "base_url": (base_url or cat_base),
                  "model": model, "api_key": "", "name": (provider or "自定义") + "（能力槽）",
                  "timeout_s": 60}
        providers.append(target)
    if not str(target.get("base_url") or "").strip():
        # 兜底：既没有用户填的地址、目录里也没有 → 明确报错，不再写出一条静默不可用的端口
        return {"ok": False, "cap": cap,
                "message": f"无法确定 {provider or '该服务商'} 的接口地址（base_url 为空）："
                           f"请填写 Base URL，或改从服务商目录里选一个已带地址的服务商。"}
    if model:
        target["model"] = model
    if base_url:
        target["base_url"] = base_url
    if api_key is not None and str(api_key).strip():
        target["api_key"] = str(api_key).strip()
    # ⭐ 2026-10-13：Key 继承 —— "只换模型、不重填 Key"（图文模型切换）时，
    # 目标端口若还没有 Key，就从同主机的其它配置继承，绝不写出空 Key 端口。
    inherited = ""
    if not str(target.get("api_key") or "").strip():
        inherited = _inherit_key(raw, target)
    tc["manual_provider"] = target.get("id")

    changed = [f"text_chat.providers.{target.get('id')}"]

    # 视觉镜像：模型带视觉才写，避免把纯文本模型塞进看屏管线
    vision_mirrored = False
    if model and _looks_vision(provider, model):
        eff_url = str(target.get("base_url") or base_url)
        eff_key = str(target.get("api_key") or "")
        _mirror_vision(raw, model, eff_url, eff_key)
        vision_mirrored = True
        changed.append("vision_models")

    msg = f"已写入 对话模型 {provider or '自定义'} / {model}"
    if inherited:
        msg += "（Key 已从同厂商既有配置继承）"
    if model and not vision_mirrored:
        msg += "（该模型不支持看图，看屏沿用原模型）"
    return {"ok": True, "cap": "chat", "message": msg,
            "changed": ",".join(changed), "vision_mirrored": vision_mirrored}


def _looks_vision(provider: str, model: str) -> bool:
    """判断一个模型是否带视觉 —— 先查目录 caps，查不到再按名字启发式。"""
    mid = (model or "").strip()
    if not mid:
        return False
    for p in CHAT_PROVIDERS:
        if provider and p["id"] != provider:
            continue
        for m in p["models"]:
            if m["id"] == mid:
                return _model_caps_has_vision(m.get("caps"))
    low = mid.lower()
    hints = ("vl", "omni", "vision", "gemini", "claude", "grok", "gpt-5", "gpt-6", "4o",
             "glm-4v", "glm-5", "qwen3-vl", "seed", "minimax-m3", "step-5", "kimi-k2", "kimi-k3")
    return any(h in low for h in hints)


def _mirror_vision(raw: dict, model: str, base_url: str, api_key: str) -> None:
    """把（带视觉的）对话模型镜像进 vision_models，让看屏管线读得到。"""
    vm = raw.get("vision_models")
    if not isinstance(vm, dict):
        vm = {}
        raw["vision_models"] = vm
    models = vm.get("models")
    if not isinstance(models, list):
        models = []
        vm["models"] = models
    ent = None
    for e in models:
        if isinstance(e, dict) and str(e.get("model") or "") == model:
            ent = e
            break
    if ent is None:
        ent = {"id": "cap-chat-" + model, "name": f"{model}（对话模型槽·看图）",
               "provider": "openai", "enabled": True, "vision": True, "timeout_s": 30}
        models.append(ent)
    ent["model"] = model
    ent["provider"] = "openai"
    ent["enabled"] = True
    ent["vision"] = True
    if base_url:
        ent["base_url"] = base_url
    if api_key:
        ent["api_key"] = api_key
    vm["active"] = ent["id"]
    vm["enabled"] = True


def _upsert_custom_provider(raw: dict, cap: str, payload: dict) -> dict:
    """自定义 OpenAI 兼容接口（单模型+Key+URL）写入 text_chat.providers。"""
    url = str(payload.get("custom_url") or "").strip().rstrip("/")
    model = str(payload.get("custom_model") or "").strip()
    key = str(payload.get("custom_key") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    if not url or not model:
        return {"ok": False, "cap": cap, "message": "自定义需填 Base URL + 单模型名"}
    tc = raw.setdefault("text_chat", {})
    providers = tc.get("providers")
    if not isinstance(providers, list):
        providers = []
        tc["providers"] = providers
    pid = "custom_" + cap
    target = None
    for pr in providers:
        if isinstance(pr, dict) and pr.get("id") == pid:
            target = pr
            break
    if target is None:
        target = {"id": pid, "provider": "openai_compatible", "base_url": url,
                  "model": model, "api_key": key}
        providers.append(target)
    else:
        target["base_url"] = url
        target["model"] = model
        if key:
            target["api_key"] = key
    tc["manual_provider"] = pid
    changed = [f"text_chat.providers.{pid}"]
    # 自定义模型默认按"可能带视觉"处理：镜像进 vision_models（用户自担）
    _mirror_vision(raw, model, url, key)
    changed.append("vision_models")
    return {"ok": True, "cap": "chat", "message": f"已添加自定义 {model}",
            "changed": ",".join(changed)}


def _set_tts(raw: dict, payload: dict) -> dict:
    """TTS 写入：预设商家走 cloud_tts 分桶；自定义走 openai_tts 单模型。"""
    provider = str(payload.get("provider") or "")
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    from .cloud_tts import merge_cloud_tts_provider

    if payload.get("is_custom"):
        # TTS 自定义：仅基础合成、无法克隆 → 落 openai_tts 分桶
        provider = "openai_tts"
        if not model:
            model = "alloy"

    if not provider:
        return {"ok": False, "cap": "tts", "message": "未指定 TTS 服务商"}

    # ⚠️ 必须按关键字传字符串：merge_cloud_tts_provider(raw, provider, api_key=…, model_name=…)
    # （曾误传一个 dict 当 api_key → model_name/group_id 全丢、api_key 变成 dict 被写进配置）
    merge_cloud_tts_provider(raw, provider,
                             api_key=api_key, group_id=group_id,
                             model_name=model, api_url=base_url)
    # 切到云端克隆子档
    vc = raw.setdefault("voice", {})
    vc.setdefault("clone", {})["sub"] = "cloud"
    # 用户刚配了云端 TTS → 解除全局静音。
    # 否则 voice.muted / voice.mode='mute' 仍在 → "配了音色却一直没声"（经典静默坑）。
    unmuted = False
    if vc.get("muted") or str(vc.get("mode") or "").strip().lower() == "mute":
        vc["muted"] = False
        vc["mode"] = "clone"
        unmuted = True
    msg = f"已写入 TTS {provider} / {model}"
    if unmuted:
        msg += "（已自动解除全局静音）"
    return {"ok": True, "cap": "tts", "message": msg,
            "changed": "voice.clone.cloud" + (",voice.muted" if unmuted else "")}


def _set_duplex(raw: dict, payload: dict) -> dict:
    """双工写入：仅已适配商家可选（bailian_qwen_omni / local_comni）。

    【2026-10-17 修复·别名容错】引导页"一键配置"曾对全部槽传 provider="bailian"，
    而双工槽的 provider id 是 `bailian_qwen_omni` → 这里直接拒绝 →
    用户以为配好了、拨号时才被 `no_cloud_duplex` 拦住（语音电话永远打不出去）。
    现对常见别名做归一，避免"同一个意思、不同写法"导致静默失败。
    """
    provider = str(payload.get("provider") or "").strip()
    _alias = {
        "bailian": "bailian_qwen_omni",
        "aliyun": "bailian_qwen_omni",
        "dashscope": "bailian_qwen_omni",
        "qwen_omni": "bailian_qwen_omni",
        "qwen-omni": "bailian_qwen_omni",
        "cloud": "bailian_qwen_omni",
        "comni": "local_comni",
        "local": "local_comni",
    }
    _p0 = provider
    provider = _alias.get(provider.lower(), provider)
    if provider != _p0:
        logger.info("双工槽：provider 别名归一 %r → %r", _p0, provider)
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()

    if provider == "local_comni":
        raw.setdefault("proactive", {})["duplex_mode"] = "local_omni"
        if base_url:
            raw["proactive"]["duplex_local_omni_endpoint"] = base_url
        return {"ok": True, "cap": "duplex", "message": "已切到本地 Comni 双工",
                "changed": "proactive.duplex_mode"}
    if provider == "bailian_qwen_omni":
        co = raw.setdefault("cloud_omni", {})
        co["model"] = model or _ALIYUN_DEFAULT_DUPLEX
        if api_key:
            co["api_key"] = api_key
        if base_url:
            co["base_url"] = base_url
        return {"ok": True, "cap": "duplex", "message": f"已写入阿里云双工 / {co['model']}",
                "changed": "cloud_omni"}
    # 规划中商家：拒绝（UI 本就置灰，这里再兜底）
    return {"ok": False, "cap": "duplex", "message": f"该双工服务商未适配: {provider}"}


def _set_aliyun(raw: dict, payload: dict) -> dict:
    """阿里云一键：一个百炼 Key 同时写 对话模型 + 看屏 + TTS + 双工。"""
    key = str(payload.get("api_key") or "").strip()
    chat_model = str(payload.get("chat_model") or "").strip() or _ALIYUN_DEFAULT_CHAT
    tts_model = str(payload.get("tts_model") or "").strip() or _ALIYUN_DEFAULT_TTS
    duplex_model = str(payload.get("duplex_model") or "").strip() or _ALIYUN_DEFAULT_DUPLEX
    base_url = str(payload.get("base_url") or "").strip() or \
        "https://dashscope.aliyuncs.com/compatible-mode/v1"

    sub: dict[str, Any] = {}
    # 1) 对话模型（+ 视觉镜像）
    sub["chat"] = _set_chat(raw, "chat", {
        "provider": "bailian", "model": chat_model,
        "api_key": key, "base_url": base_url,
    })
    # 2) TTS（克隆子档切 cloud）
    sub["tts"] = _set_tts(raw, {
        "provider": "dashscope_cosy", "model": tts_model, "api_key": key,
    })
    # 3) 双工
    sub["duplex"] = _set_duplex(raw, {
        "provider": "bailian_qwen_omni", "model": duplex_model, "api_key": key,
        "base_url": "https://dashscope.aliyuncs.com",
    })
    ok = all(v.get("ok") for v in sub.values())
    parts = _aliyun_parts(raw)
    done = sum(1 for v in parts.values() if v)
    return {"ok": ok, "cap": "aliyun",
            "message": f"阿里云一键已配置（对话/看屏 + 语音合成 + 实时通话，{done}/3 就绪）",
            "changed": "cloud_omni,voice.clone.cloud,text_chat.providers,vision_models",
            "sub": {k: {"ok": v.get("ok"), "message": v.get("message")} for k, v in sub.items()},
            "parts": parts}
