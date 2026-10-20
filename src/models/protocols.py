"""引擎/厂商元数据：字段契约与端点形状（**纯数据**，无任何逻辑）。

为什么单独一层
--------------
"最大兼容"的难点不在抽象，而在**不能让任何一个字段名漂移**——
`tts_backends.py` 里 10 个 Backend 各用 `getattr(config, "<字段名>", 默认值)` 读
同一个 voice_cfg，字段名不统一（mimo 读 `mimo_api_key`、openai_tts 读
`api_url`+`api_key`、dashscope 读 `voice_id`…）。
一旦有谁"顺手统一一下字段名"，就会静默改掉某条链路的行为。

所以这里把字段契约**如实记录下来当作文档+校验依据**：
  · 每个字段都在 tts_backends.py / webapp.py 有出处（注释标注行号）；
  · catalog 用它来判断"这个音色档配全了没有"，但**绝不用它去改字段名**。

⚠️ 本文件是**只读的元数据**。凡涉及"写入"的逻辑一律不在这里。
"""
from __future__ import annotations

# ============================================================ TTS 引擎
# 来源：src/ui/webapp.py:4511-4514（_VOICE_PROFILE_ENGINES）
#       src/voice/tts_backends.py:1339-1354（create_backend 的 engine_map）

TTS_ENGINES: dict[str, dict] = {
    # 【2026-10-21 修·引擎表漂移】原来这里还列着 `edge-tts`（微软在线音色），
    # 而 webapp 侧 2026-10-16 已按发布口径**下架**它（用户要求不使用微软在线音色，
    # 见 webapp.py 的 `_VOICE_PROFILE_ENGINES` 与"不再出现在可选清单里"那段）。
    # 结果：models 层（catalog 的引擎清单）还在推荐一个已下架的引擎，
    # `tools/check_models_layer.py` 报 `[FAIL] 引擎表漂移: 仅我有={'edge-tts'}`。
    # 注意：**后端没删**——`tts_backends.EdgeTTSBackend` 仍保留，老配置里写着
    # `engine: edge-tts` 的用户照样能出声（`_TTS_ENGINE_ALIASES["edge"]` 也保留）。
    # 这里只把它从「推荐/可选清单」里摘掉。
    "dashscope_cosy": {
        "name": "阿里云百炼 CosyVoice / Qwen-Audio-TTS",
        "local": False, "cloud": True, "free": False, "clone": True,
        # 来源 tts_backends.py:909-935 DashScopeCosyBackend
        "required_fields": ["api_key", "model"],
        "either_or": [["voice_id", "reference_audio"]],   # 已克隆音色 id 或参考音二选一
        "optional_fields": ["api_url", "reference_text", "group_id"],
        # 来源 tts_backends.py:924-928（两个 HTTP 端点 + 一个 WS 实时端点）
        "endpoint": "https://dashscope.aliyuncs.com/api/v1/services/"
                    "aigc/multimodal-generation/generation",
        "endpoint_alts": [
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
            "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        ],
        "hint": "云端克隆，音色像；合成 model 必须与控制台克隆时一致，否则报 Model not exist",
    },
    "qwen3tts": {
        "name": "本地 Qwen3-TTS",
        "local": True, "cloud": False, "free": True, "clone": True,
        # 来源 tts_backends.py:505-510
        "required_fields": ["reference_audio"],
        "optional_fields": ["api_url", "reference_text", "language", "timeout_s"],
        "default_endpoint": "http://127.0.0.1:9882",
        "hint": "本地零样本克隆，约 2.5GB 显存；服务没起会降级 edge-tts",
    },
    "gpt-sovits": {
        "name": "本地 GPT-SoVITS",
        "local": True, "cloud": False, "free": True, "clone": True,
        # 来源 tts_backends.py:99-112
        "required_fields": ["reference_audio"],
        "optional_fields": ["api_url", "reference_text", "language", "voice",
                            "temperature", "top_k", "top_p", "timeout_s",
                            "aux_reference_audio_paths"],
        "default_endpoint": "http://127.0.0.1:9880",
        "hint": "本地克隆老牌方案；需另装服务",
    },
    "voxcpm": {
        "name": "本地 VoxCPM2",
        "local": True, "cloud": False, "free": True, "clone": True,
        # 来源 tts_backends.py:381-389
        "required_fields": ["reference_audio"],
        "optional_fields": ["api_url", "reference_text", "voice", "timeout_s",
                            "emotion_tags", "style_instruction"],
        "default_endpoint": "http://127.0.0.1:9881",
        "hint": "终极克隆音色；参考音 3~10 秒最清晰",
    },
    "cosyvoice": {
        "name": "本地 CosyVoice2",
        "local": True, "cloud": False, "free": True, "clone": True,
        # 来源 tts_backends.py:665-670
        "required_fields": ["reference_audio"],
        "optional_fields": ["api_url", "reference_text", "voice", "timeout_s"],
        "default_endpoint": "http://127.0.0.1:9883",
        "stream": True,     # tts_backends.py:677-679 supports_stream
        "hint": "本地零样本克隆，原生支持流式",
    },
    "minimax": {
        "name": "MiniMax 云端 TTS",
        "local": False, "cloud": True, "free": False, "clone": True,
        # 来源 tts_backends.py:180-193 文档串 + _to_minimax_pitch
        "required_fields": ["api_key", "group_id"],
        "optional_fields": ["voice_id", "model", "pitch", "volume", "speed"],
        "endpoint": "https://api.minimaxi.com/v1/t2a_v2?GroupId={group_id}",
        "hint": "⚠️ group_id 必填（拼在 URL 上）；缺了它档存下来也发不出声",
        "key_prefix": "sk-api-",     # webapp.py:188 扩散时按此跳过
    },
    "mimo_tts": {
        "name": "小米 MiMo-V2.5-TTS",
        "local": False, "cloud": True, "free": True, "clone": True,
        # 来源 tts_backends.py:569-574（注意读的是 mimo_api_key，不是 api_key）
        "required_fields": ["mimo_api_key", "reference_audio"],
        "optional_fields": ["voice", "fallback_voice", "timeout_s"],
        "endpoint": "（小米官方 OpenAI 兼容 API）",
        "hint": "限时免费、中文顶级；⚠️ 字段名是 mimo_api_key（与别家不同）",
    },
    "openai_tts": {
        "name": "OpenAI 兼容 TTS",
        "local": False, "cloud": True, "free": False, "clone": True,
        # 来源 tts_backends.py:835-845
        "required_fields": ["api_url"],
        "either_or": [["api_key", "reference_text"]],
        "optional_fields": ["model", "voice", "reference_audio", "clone_mode"],
        "endpoint": "{api_url}/audio/speech",
        "hint": "标准 OpenAI /audio/speech 协议，可接任意兼容端点",
    },
    "pyttsx3": {
        "name": "系统离线 TTS（SAPI5）",
        "local": True, "cloud": False, "free": True, "clone": False,
        # 来源 tts_backends.py:772-774
        "required_fields": [],
        "optional_fields": ["voice", "rate"],
        "hint": "最后兜底：保证至少能出声，无网络也活",
    },
    "fish_audio": {
        "name": "Fish Audio 云端 TTS",
        "local": False, "cloud": True, "free": False, "clone": True,
        # 来源 tts_backends.py:1339-1384 FishAudioBackend
        #（POST https://api.fish.audio/v1/tts；Body.reference_id = 控制台克隆出的音色模型 _id）
        "required_fields": ["api_key", "voice_id"],
        "optional_fields": ["model", "timeout_s"],
        "endpoint": "https://api.fish.audio/v1/tts",
        "hint": "克隆音色填控制台克隆出的模型 _id（voice_id）；模型名默认 s2-pro；无系统预设音",
    },
}

# 与 tts_backends.create_backend 的 engine_map 完全一致（防漂移）
_TTS_ENGINE_ALIASES = {
    "dashscope_cosyvoice": "dashscope_cosy",
    "bailian": "dashscope_cosy",
    "local_qwen_tts": "qwen3tts",
    "custom_tts": "openai_tts",
    "edge": "edge-tts",
}

# ============================================================ LLM / 视觉 provider
# 来源：src/llm/text_chat_router.py:62-69 与 src/llm/vision_router.py:126-133
#       （两处 _PROV_ALIAS 内容一致，但各自定义了一份——P3 可考虑归一）

LLM_PROVIDERS: dict[str, dict] = {
    "openai": {
        "name": "OpenAI 兼容端点",
        "client": "OpenAIClient",
        "need_key": True,
        "endpoint": "{base_url}/chat/completions",
        "aliases": ["openai", "openai_compatible", "openai-compatible",
                    "dashscope", "aliyun", "bailian"],
        "hint": "覆盖绝大多数云端（百炼/智谱/DeepSeek/硅基/OpenAI…）",
    },
    "ollama": {
        "name": "本地 Ollama",
        "client": "OllamaClient",
        "need_key": False,
        "endpoint": "{ollama.host}（默认 http://127.0.0.1:11434）",
        "aliases": ["ollama"],
        "hint": "完全离线免费；需先 pull 对应模型",
    },
    "minicpm": {
        "name": "MiniCPM-o 实时接口",
        "client": "MiniCPMClient",
        "need_key": False,
        "endpoint": "{endpoint}（WebSocket，必须自配）",
        "aliases": ["minicpm"],
        "hint": "⚠️ 官方免费网关已废弃；DEFAULT_ENDPOINT 已置空，无自配端点直接抛错",
    },
}


# ============================================================ 查询函数

def field_contract_for(engine: str) -> dict:
    """取某引擎的字段契约；未知引擎返回一个空的保守契约。"""
    key = _TTS_ENGINE_ALIASES.get(str(engine or "").strip().lower(),
                                 str(engine or "").strip().lower())
    meta = TTS_ENGINES.get(key)
    if meta is None:
        return {"known": False, "engine": engine,
                "required_fields": [], "optional_fields": [],
                "hint": "未知引擎（不在 _VOICE_PROFILE_ENGINES 白名单内）"}
    out = dict(meta)
    out["known"] = True
    out["engine"] = key
    return out


def engine_of_provider(provider: str) -> str:
    """把「云端 TTS 厂商名」映射成 create_backend 认的 engine 名。

    来源：src/ui/webapp.py:13252-13254（ensure_cloud_voice_profile 的 engine_map）。
    与那份映射**保持逐字一致**，避免两处口径漂移。
    """
    m = {
        "dashscope_cosyvoice": "dashscope_cosy",
        "dashscope_cosy": "dashscope_cosy",
        "bailian": "dashscope_cosy",
        "minimax": "minimax",
        "openai_tts": "openai_tts",
    }
    return m.get(str(provider or "").strip(), str(provider or "").strip())
