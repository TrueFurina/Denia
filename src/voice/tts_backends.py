"""TTS 后端抽象层。

定义 TTSBackend 抽象基类，支持多种 TTS 引擎：
- EdgeTTSBackend: Microsoft edge-tts 在线语音
- GPTSoVITSBackend: GPT-SoVITS 本地声音克隆（占位）
- Pyttsx3Backend: pyttsx3 离线降级方案
"""
from __future__ import annotations

import abc
import asyncio
import base64
import logging
import os
import tempfile
from types import SimpleNamespace
from typing import Optional

logger = logging.getLogger(__name__)

from .emotion_styles import emotion_to_instruct  # 【2026-10-01 F】emotion → CosyVoice instruct


class TTSBackend(abc.ABC):
    """TTS 后端抽象基类。"""

    @abc.abstractmethod
    async def synthesize(self, text: str, output_path: str) -> None:
        """合成语音到指定文件。"""
        ...

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """后端名称。"""
        ...

    @abc.abstractmethod
    def update_config(self, config: SimpleNamespace) -> None:
        """运行时更新配置。"""
        ...

    @property
    def output_ext(self) -> str:
        """合成输出文件扩展名（TTS 层用它选临时文件后缀）。"""
        return ".mp3"

    def ensure_playable(self, path: str) -> str:
        """返回可被 pygame 稳定播放的路径；默认原样返回。

        某些后端（如 MiniMax）产出的 mp3 对 pygame 的 drmp3 解码器不友好，
        可在子类覆写：先转成标准 WAV 再交给播放层。"""
        return path


class EdgeTTSBackend(TTSBackend):
    """Microsoft edge-tts 在线 TTS 后端。"""

    def __init__(self, config: SimpleNamespace) -> None:
        self._voice: str = config.voice
        self._rate: str = config.rate
        self._pitch: str = config.pitch
        self._volume: str = config.volume
        self._fallback_pyttsx3: bool = config.fallback_pyttsx3

    @property
    def name(self) -> str:
        return "edge-tts"

    def update_config(self, config: SimpleNamespace) -> None:
        self._voice = config.voice
        self._rate = config.rate
        self._pitch = config.pitch
        self._volume = config.volume
        self._fallback_pyttsx3 = config.fallback_pyttsx3

    async def synthesize(self, text: str, output_path: str) -> None:
        import edge_tts
        communicate = edge_tts.Communicate(
            text, self._voice,
            rate=self._rate, pitch=self._pitch, volume=self._volume,
        )
        await communicate.save(output_path)

    @property
    def voice(self) -> str:
        return self._voice


class GPTSoVITSBackend(TTSBackend):
    """GPT-SoVITS 本地声音克隆后端。

    通过本地 GPT-SoVITS HTTP API（默认 http://127.0.0.1:9880）合成语音：
    - 走 v2 API（POST /tts）
    - 需要角色配置提供参考音频 reference_audio + 对应文本 reference_text
    - 服务未部署/不可用时，由 TTS 层降级到 edge-tts 兜底（fallback_voice）
    """

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_url: str = (getattr(config, "api_url", "http://127.0.0.1:9880") or "http://127.0.0.1:9880").rstrip("/")
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._reference_text: str = getattr(config, "reference_text", "") or ""
        self._language: str = getattr(config, "language", "zh") or "zh"
        self._fallback_voice: str = getattr(config, "voice", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"
        self._timeout: float = float(getattr(config, "timeout_s", 30) or 30)
        # 辅助参考音频（多段融合，提升语气还原度）；空列表则不传
        raw_aux = getattr(config, "aux_reference_audio_paths", None) or []
        self._aux_reference_audio_paths: list[str] = [p for p in raw_aux if p]
        # 推理参数：温度越低音色越贴参考音频（不像/漂移时往低调）
        self._temperature: float = float(getattr(config, "temperature", 0.7) or 0.7)
        self._top_k: int = int(getattr(config, "top_k", 15) or 15)
        self._top_p: float = float(getattr(config, "top_p", 0.9) or 0.9)

    @property
    def name(self) -> str:
        return "gpt-sovits"

    @property
    def output_ext(self) -> str:
        return ".wav"

    @property
    def fallback_voice(self) -> str:
        """服务不可用时的 edge-tts 兜底音色。"""
        return self._fallback_voice

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_url = (getattr(config, "api_url", self._api_url) or self._api_url).rstrip("/")
        self._reference_audio = getattr(config, "reference_audio", "") or ""
        self._reference_text = getattr(config, "reference_text", "") or ""
        self._language = getattr(config, "language", self._language) or self._language
        self._fallback_voice = getattr(config, "voice", self._fallback_voice) or self._fallback_voice

    async def synthesize(self, text: str, output_path: str) -> None:
        """通过 GPT-SoVITS HTTP API 合成语音到指定文件。"""
        if not self._reference_audio:
            raise RuntimeError("GPT-SoVITS 未配置参考音频 reference_audio")
        if not self._reference_text:
            raise RuntimeError("GPT-SoVITS 未配置参考音频对应文本 reference_text")

        # v2 API 统一走 POST /tts（老版本根路径 POST / 已废弃，直接调 /tts 避免 404 重试）
        v2_payload = {
            "text": text,
            "text_lang": self._language,
            "ref_audio_path": self._reference_audio,
            "aux_ref_audio_paths": self._aux_reference_audio_paths,
            "prompt_text": self._reference_text,
            "prompt_lang": self._language,
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            # 推理参数：温度/采样控制在合理范围，避免音色漂移不像参考音频
            "temperature": self._temperature,
            "top_k": self._top_k,
            "top_p": self._top_p,
        }

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._api_url + "/tts", json=v2_payload)
            if resp.status_code >= 400:
                # 把 GPT-SoVITS 返回的具体原因透传出来，方便定位（如路径不存在/文本缺失）
                try:
                    detail = resp.json().get("message") or ""
                    exc = resp.json().get("Exception") or ""
                except Exception:
                    detail = resp.text[:300]
                    exc = ""
                raise RuntimeError(
                    f"GPT-SoVITS 合成失败 (HTTP {resp.status_code}): {detail} {exc}".strip()
                )
            resp.raise_for_status()
            if not resp.content:
                raise RuntimeError("GPT-SoVITS 返回空音频")
            with open(output_path, "wb") as f:
                f.write(resp.content)


class MiniMaxBackend(TTSBackend):
    """MiniMax 语音（云端 T2A v2）后端。

    云端合成，本地零占用（不依赖显卡），低性能设备也能用克隆/自定义音色：
    - 接口：POST https://api.minimaxi.com/v1/t2a_v2?GroupId={group_id}
    - Header: Authorization: Bearer {api_key}
    - 支持系统音色（voice_id，如 female-shaonv）与用户复刻音色（voice_clone 得到）
    - 配置（config.yaml → character.voice）：
      engine: minimax
      api_key: 用户在 platform.minimaxi.com 的接口密钥（自费）
      group_id: 用户组 ID（拼接在 URL 末尾）
      voice_id: 音色编号（系统音色或复刻音色 id）
      model: 模型版本（默认 speech-02-turbo，聊天场景快）
    """

    @staticmethod
    def _to_minimax_pitch(value: Any) -> int:
        """把 edge-tts 的 pitch('+15Hz'/'-5Hz') 或数字安全转成 MiniMax [-12,12] 整数。"""
        if value is None or value == "":
            return 0
        if isinstance(value, (int, float)):
            num = int(value)
        else:
            try:
                num = int(str(value).split("Hz")[0].split("hz")[0].strip())
            except (ValueError, TypeError):
                return 0
        return max(-12, min(12, num))

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_key: str = getattr(config, "api_key", "") or ""
        self._group_id: str = getattr(config, "group_id", "") or ""
        self._voice_id: str = getattr(config, "voice_id", "female-shaonv") or "female-shaonv"
        self._model: str = getattr(config, "model", "speech-02-turbo") or "speech-02-turbo"
        self._speed: float = float(getattr(config, "speed", 1.0) or 1.0)
        self._vol: float = float(getattr(config, "vol", 1.0) or 1.0)
        self._pitch: int = self._to_minimax_pitch(getattr(config, "pitch", 0))
        self._timeout: float = float(getattr(config, "timeout_s", 30) or 30)
        self._fallback_voice: str = getattr(config, "voice", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def fallback_voice(self) -> str:
        return self._fallback_voice

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_key = getattr(config, "api_key", self._api_key) or self._api_key
        self._group_id = getattr(config, "group_id", self._group_id) or self._group_id
        self._voice_id = getattr(config, "voice_id", self._voice_id) or self._voice_id
        self._model = getattr(config, "model", self._model) or self._model
        self._speed = float(getattr(config, "speed", self._speed) or self._speed)
        self._vol = float(getattr(config, "vol", self._vol) or self._vol)
        self._pitch = self._to_minimax_pitch(getattr(config, "pitch", self._pitch))

    async def synthesize(self, text: str, output_path: str) -> None:
        if not self._api_key:
            raise RuntimeError("MiniMax 未配置 API Key（platform.minimaxi.com 注册获取，用户自费）")
        if not self._group_id:
            raise RuntimeError("MiniMax 未配置 GroupId（账户管理 → 基本信息 → 用户组）")

        url = f"https://api.minimaxi.com/v1/t2a_v2?GroupId={self._group_id}"
        payload = {
            "model": self._model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": self._voice_id,
                "speed": self._speed,
                "vol": self._vol,
                "pitch": self._pitch,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("base_resp", {}).get("status_msg", "") or resp.text[:300]
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(f"MiniMax 合成失败 (HTTP {resp.status_code}): {detail}")
            data = resp.json()
            # 业务错误码（如 1004 无权限/1008 额度不足）
            base_resp = data.get("base_resp", {})
            if base_resp.get("status_code", 0) != 0:
                status_code = base_resp.get("status_code", 0)
                status_msg = base_resp.get("status_msg", "") or ""
                if status_code == 1008:
                    raise RuntimeError("MiniMax 余额不足（code=1008）：请在 platform.minimaxi.com 充值后重试")
                raise RuntimeError(f"MiniMax 合成失败: {status_msg} (code={status_code})")
            audio_hex = data.get("data", {}).get("audio", "") or ""
            if not audio_hex:
                raise RuntimeError("MiniMax 返回空音频")
            # MiniMax 实测返回的是 hex 编码的音频字节（曾误当 base64 解码，
            # 导致 pygame 报 "corrupt mp3 file (bad stream)" 无声）。
            # 兼容旧 base64 响应：fromhex 失败则回退 b64decode。
            try:
                audio_bytes = bytes.fromhex(audio_hex)
            except ValueError:
                import base64
                audio_bytes = base64.b64decode(audio_hex)
            with open(output_path, "wb") as f:
                f.write(audio_bytes)

    def ensure_playable(self, path: str) -> str:
        """MiniMax 的 mp3 是 LAME VBR 编码，pygame 的 drmp3 解码不稳定
        （报过 "corrupt mp3 file"，播放时可能有杂音）→ 先转标准 WAV 再播。"""
        try:
            from .audio_utils import convert_mp3_to_wav
            wav_path = path.rsplit(".", 1)[0] + "_pygame.wav"
            if convert_mp3_to_wav(path, wav_path):
                return wav_path
        except Exception:
            logger.exception("MiniMax mp3 转 wav 失败，回退原样播放")
        return path


# ==================== 情绪壳（emotion → 语音表达）====================
# 设计：风格(trait) ≠ 情绪(state)，两层解耦——
#   · 风格/气质(藿藿=胆怯 / 白厄=阳光 / 达妮娅=慵懒) 由克隆参考音自带(trait)，不在此注入；
#   · 情绪(state, happy/sad/...) 由决策 LLM 逐句选择，本函数把它翻译成引擎能懂的表达标记。
# 「怎么表达这个情绪」= 角色自己的数据(emotion_tags)，可替换；代码不认识任何具体角色。
# 内置通用表是终极兜底：角色没配 emotion_tags 时也能有活人感（客制化克隆开箱即用）。
_DEFAULT_EMOTION_TAGS: dict[str, str] = {
    "happy": " [laughing]",
    "shy": "[Uhm] ",
    "surprised": "[Surprise-wa] ",
    "sad": "[sigh] ",
    "sleepy": "[Uhm] ",
    "angry": "[Uhm] ",
    "curious": "",
    "neutral": "",
}


def _as_plain_dict(obj: object) -> dict:
    """把 dict / SimpleNamespace / None 归一为普通 str→str 字典（配置可能是任意一种）。"""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {str(k): ("" if v is None else str(v)) for k, v in obj.items()}
    try:
        return {str(k): ("" if v is None else str(v)) for k, v in vars(obj).items()}
    except TypeError:
        return {}


def apply_emotion_tags(
    text: str,
    emotion: str | None,
    tags: object = None,
    engine: str = "voxcpm",
    style_instruction: str = "",
) -> str:
    """把决策 emotion 按角色「情绪壳」翻译成引擎语音表达标记并注入文本。

    参数：
      tags: 该角色的 emotion→标记 映射（config 的 voice.emotion_tags，可替换数据）；
            缺省项用内置通用表兜底，空/None 则全用内置表。
      engine: 目标引擎。voxcpm 用非语言标签；其余引擎暂原样返回（避免把 [laughing]
              当字念出来）。未来可在各分支加自己的语法（如 edge→SSML）。
      style_instruction: 可选「风格强化」前缀（参考音平淡时才用），留空则不加。
              注意 VoxCPM2 的括号 Control Instruction 属实验特性，实测偶有被当字念出，
              故默认留空、仅在参考音确实平淡时才启用。
    """
    expr = (style_instruction or "").strip()
    if expr and not expr.startswith("("):
        expr = f"({expr})"
    if not emotion or engine != "voxcpm":
        # 无情绪 / 非 voxcpm 引擎：不注入标签（但风格强化前缀仍生效）
        return (expr + text) if expr else text
    merged = {**_DEFAULT_EMOTION_TAGS, **_as_plain_dict(tags)}
    tag = merged.get(str(emotion).strip().lower(), "") or ""
    if tag.endswith(" "):          # 带尾随空格=前置语气词（如 "[Uhm] "）
        return f"{expr}{tag}{text}"
    return f"{expr}{text}{tag}"    # 后置标记（如 " [laughing]"）


class VoxCpmBackend(TTSBackend):
    """VoxCPM2 本地 TTS 后端（终极克隆音色）。

    通过本地 VoxCPM2 HTTP 服务（默认 http://127.0.0.1:9881）合成语音：
    - 终极克隆：参考音频 reference_audio（+ 对应文本 reference_text 增强相似度）
    - 服务未部署/不可用时，由 TTS 层降级到 edge-tts 兜底（fallback_voice）
    - 参考音频应尽量贴近角色音色（3~10 秒清晰语音最佳）
    """

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_url: str = (getattr(config, "api_url", "http://127.0.0.1:9881") or "http://127.0.0.1:9881").rstrip("/")
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._reference_text: str = getattr(config, "reference_text", "") or ""
        self._fallback_voice: str = getattr(config, "voice", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"
        self._timeout: float = float(getattr(config, "timeout_s", 60) or 60)
        # 情绪壳（可替换数据）：角色的 情绪→表达标记 映射 + 可选风格强化前缀
        self._emotion_tags = getattr(config, "emotion_tags", None)
        self._style_instruction: str = getattr(config, "style_instruction", "") or ""

    @property
    def name(self) -> str:
        return "voxcpm"

    @property
    def output_ext(self) -> str:
        return ".wav"

    @property
    def fallback_voice(self) -> str:
        """服务不可用时的 edge-tts 兜底音色。"""
        return self._fallback_voice

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_url = (getattr(config, "api_url", self._api_url) or self._api_url).rstrip("/")
        self._reference_audio = getattr(config, "reference_audio", "") or ""
        self._reference_text = getattr(config, "reference_text", "") or ""
        self._fallback_voice = getattr(config, "voice", self._fallback_voice) or self._fallback_voice
        self._emotion_tags = getattr(config, "emotion_tags", self._emotion_tags)
        self._style_instruction = getattr(config, "style_instruction", self._style_instruction) or self._style_instruction

    async def synthesize(self, text: str, output_path: str, emotion: str | None = None) -> None:
        """通过 VoxCPM2 HTTP 服务合成语音到指定文件。emotion 注入非语言标签。"""
        if not self._reference_audio:
            raise RuntimeError("VoxCPM2 未配置参考音频 reference_audio")

        text = apply_emotion_tags(
            text, emotion, tags=self._emotion_tags, engine="voxcpm",
            style_instruction=self._style_instruction)
        # VoxCPM2 只认 prompt_* 命名，且 prompt_wav_path / prompt_text 必须成对
        payload: dict = {"text": text, "prompt_wav_path": self._reference_audio}
        if self._reference_text:
            payload["prompt_text"] = self._reference_text

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._api_url + "/tts", json=payload)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("error") or ""
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(f"VoxCPM2 合成失败: {detail} (HTTP {resp.status_code})")
            if not resp.content:
                raise RuntimeError("VoxCPM2 返回空音频")
            with open(output_path, "wb") as f:
                f.write(resp.content)


    @property
    def supports_emotion(self) -> bool:
        """支持按情绪注入非语言标签（apply_emotion_tags）。"""
        return True

    @property
    def supports_stream(self) -> bool:
        """VoxCPM2 原生流式（generate_streaming 逐块 yield 音频），首块 ~2s 即可出声。"""
        return True

    @property
    def stream_sample_rate(self) -> int:
        """流式 PCM 采样率（与服务端一致，48kHz）。"""
        return 48000

    async def synthesize_stream(self, text: str, emotion: str | None = None):
        """流式零样本克隆：逐块 yield int16 PCM（48kHz 单声道）。

        服务端 stream=True 走 VoxCPM2 原生流式生成（generate_streaming 逐块出音频），
        客户端（tts.py:_speak_stream）用 sounddevice 边收边播，把通话首句开口延迟
        从整段合成的 7~9s 压到首块 ~2s。emotion 注入非语言标签带活人感。
        """
        if not self._reference_audio:
            raise RuntimeError("VoxCPM2 未配置参考音频 reference_audio")

        text = apply_emotion_tags(
            text, emotion, tags=self._emotion_tags, engine="voxcpm",
            style_instruction=self._style_instruction)
        payload: dict = {"text": text, "prompt_wav_path": self._reference_audio, "stream": True}
        if self._reference_text:
            payload["prompt_text"] = self._reference_text

        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", self._api_url + "/tts", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    raise RuntimeError(f"VoxCPM2 流式合成失败 (HTTP {resp.status_code}): {body[:200]}")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk


class Qwen3TtsBackend(TTSBackend):
    """Qwen3-TTS 本地语音克隆后端（0.6B 轻量，约 2.5GB 显存）。

    通过本地 Qwen3-TTS HTTP 服务（默认 http://127.0.0.1:9882）合成语音：
    - 零样本克隆：参考音频 reference_audio（3 秒+ 清晰语音）+ 对应文本 reference_text
    - 服务未部署/不可用时，由 TTS 层降级到 edge-tts 兜底（fallback_voice）
    """

    # Qwen3-TTS 的 language 参数用语言名（Chinese/English/Auto），配置里常见 zh/en 需要映射
    _LANG_MAP = {
        "zh": "Chinese", "zh-cn": "Chinese", "zh_cn": "Chinese",
        "en": "English", "en-us": "English", "en_us": "English",
    }

    @staticmethod
    def _normalize_language(lang: str) -> str | None:
        if not lang:
            return None
        key = lang.strip().lower().replace("_", "-")
        return Qwen3TtsBackend._LANG_MAP.get(key, lang)

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_url: str = (getattr(config, "api_url", "http://127.0.0.1:9882") or "http://127.0.0.1:9882").rstrip("/")
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._reference_text: str = getattr(config, "reference_text", "") or ""
        self._language: str = self._normalize_language(getattr(config, "language", "zh") or "zh") or "Chinese"
        self._fallback_voice: str = getattr(config, "voice", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"
        self._timeout: float = float(getattr(config, "timeout_s", 60) or 60)

    @property
    def name(self) -> str:
        return "qwen3tts"

    @property
    def output_ext(self) -> str:
        return ".wav"

    @property
    def fallback_voice(self) -> str:
        """服务不可用时的 edge-tts 兜底音色。"""
        return self._fallback_voice

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_url = (getattr(config, "api_url", self._api_url) or self._api_url).rstrip("/")
        self._reference_audio = getattr(config, "reference_audio", "") or ""
        self._reference_text = getattr(config, "reference_text", "") or ""
        lang = getattr(config, "language", None)
        self._language = self._normalize_language(lang) or self._language
        self._fallback_voice = getattr(config, "voice", self._fallback_voice) or self._fallback_voice

    async def synthesize(self, text: str, output_path: str) -> None:
        """通过 Qwen3-TTS HTTP 服务合成语音到指定文件。"""
        if not self._reference_audio:
            raise RuntimeError("Qwen3-TTS 未配置参考音频 reference_audio")

        payload: dict = {"text": text, "ref_wav_path": self._reference_audio}
        if self._reference_text:
            payload["ref_text"] = self._reference_text
        if self._language:
            payload["language"] = self._language

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._api_url + "/tts", json=payload)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("error") or ""
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(f"Qwen3-TTS 合成失败: {detail} (HTTP {resp.status_code})")
            if not resp.content:
                raise RuntimeError("Qwen3-TTS 返回空音频")
            with open(output_path, "wb") as f:
                f.write(resp.content)


class MimoTtsBackend(TTSBackend):
    """小米 MiMo-V2.5-TTS 云端语音克隆后端（限时免费，中文顶级）。

    通过小米官方 OpenAI 兼容 API 合成达娅声：
    - voiceclone：参考音频 reference_audio（本机文件，mp3/wav）+ 要说的文本
    - 用户自带 api_key（mimo_api_key），限时免费，不依赖本机 GPU
    - 服务不可用时由 TTS 层降级 edge-tts 兜底（fallback_voice）
    """

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_key: str = getattr(config, "mimo_api_key", "") or ""
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._voice: str = getattr(config, "voice", "mimo-v2.5-tts-voiceclone") or "mimo-v2.5-tts-voiceclone"
        self._timeout: float = float(getattr(config, "timeout_s", 120) or 120)
        self._fallback_voice: str = getattr(config, "fallback_voice", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"

    @property
    def name(self) -> str:
        return "mimo_tts"

    @property
    def output_ext(self) -> str:
        return ".wav"

    @property
    def fallback_voice(self) -> str:
        return self._fallback_voice

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_key = getattr(config, "mimo_api_key", self._api_key) or self._api_key
        self._reference_audio = getattr(config, "reference_audio", "") or ""
        self._voice = getattr(config, "voice", self._voice) or self._voice
        self._fallback_voice = getattr(config, "fallback_voice", self._fallback_voice) or self._fallback_voice

    async def synthesize(self, text: str, output_path: str) -> None:
        """通过小米 MiMo API 合成达娅声到指定文件。"""
        if not self._api_key:
            raise RuntimeError("MiMo-TTS 未配置 mimo_api_key")
        if not self._reference_audio or not os.path.exists(self._reference_audio):
            raise RuntimeError("MiMo-TTS 未配置参考音频 reference_audio")
        ext = os.path.splitext(self._reference_audio)[1].lower()
        mime = "audio/mpeg" if ext in (".mp3",) else "audio/wav"
        with open(self._reference_audio, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("ascii")
        if len(audio_b64) > 10 * 1024 * 1024:
            raise RuntimeError("MiMo 参考音频 base64 超过 10MB 上限")
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": text},
        ]
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}

        # 官方文档要求 voice 传「音频样本 base64」（无 data: 前缀）；
        # 但部分集群要求 data: URI 形式。先试裸 base64，遇 400 参数错自动重试带前缀格式。
        def _payload(voice_val):
            return {"model": self._voice, "messages": messages,
                    "audio": {"format": "wav", "voice": voice_val}}

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                "https://api.xiaomimimo.com/v1/chat/completions",
                json=_payload(audio_b64), headers=headers)
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = resp.json().get("error") or ""
                except Exception:
                    detail = resp.text[:300]
                # 参数类错误（voice/audio 格式）时，重试 data: URI 形式
                if resp.status_code == 400 and (
                        "param" in str(detail).lower()
                        or "voice" in str(detail).lower()
                        or "audio" in str(detail).lower()
                        or not str(detail).strip()):
                    resp = await client.post(
                        "https://api.xiaomimimo.com/v1/chat/completions",
                        json=_payload(f"data:{mime};base64,{audio_b64}"), headers=headers)
                    if resp.status_code >= 400:
                        try:
                            detail = resp.json().get("error") or ""
                        except Exception:
                            detail = resp.text[:300]
                        raise RuntimeError(f"MiMo-TTS 合成失败: {detail} (HTTP {resp.status_code})")
                else:
                    raise RuntimeError(f"MiMo-TTS 合成失败: {detail} (HTTP {resp.status_code})")
            msg = resp.json()["choices"][0]["message"]
            af = msg.get("audio") or {}
            if not af.get("data"):
                raise RuntimeError(f"MiMo 未返回音频: {msg.get('content') or resp.text[:300]}")
            audio_bytes = base64.b64decode(af["data"])
            if not audio_bytes:
                raise RuntimeError("MiMo 返回空音频")
            with open(output_path, "wb") as f:
                f.write(audio_bytes)


class CosyVoiceBackend(TTSBackend):
    """CosyVoice2-0.5B 本地语音克隆后端。

    通过本地 CosyVoice2 HTTP 服务（默认 http://127.0.0.1:9883）合成语音：
    - 零样本克隆：参考音频 reference_audio（3 秒+ 清晰语音，自动重采样 16k）
    - 服务未部署/不可用时，由 TTS 层降级到 edge-tts 兜底（fallback_voice）
    """

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_url: str = (getattr(config, "api_url", "http://127.0.0.1:9883") or "http://127.0.0.1:9883").rstrip("/")
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._reference_text: str = getattr(config, "reference_text", "") or ""
        self._fallback_voice: str = getattr(config, "voice", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"
        self._timeout: float = float(getattr(config, "timeout_s", 120) or 120)

    @property
    def name(self) -> str:
        return "cosyvoice"

    @property
    def supports_stream(self) -> bool:
        """CosyVoice2 原生支持流式合成（LLM 逐 token + 逐块出音频）。"""
        return True

    @property
    def supports_emotion(self) -> bool:
        """【2026-10-01 F】CosyVoice2 支持 Instruct2 情绪/风格指令，可注入副语言情绪。"""
        return True

    @property
    def output_ext(self) -> str:
        return ".wav"

    @property
    def stream_sample_rate(self) -> int:
        """流式 PCM 采样率（与服务端一致，24kHz）。"""
        return 24000

    @property
    def fallback_voice(self) -> str:
        """服务不可用时的 edge-tts 兜底音色。"""
        return self._fallback_voice

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_url = (getattr(config, "api_url", self._api_url) or self._api_url).rstrip("/")
        self._reference_audio = getattr(config, "reference_audio", "") or ""
        self._reference_text = getattr(config, "reference_text", "") or ""
        self._fallback_voice = getattr(config, "voice", self._fallback_voice) or self._fallback_voice

    async def synthesize(self, text: str, output_path: str, emotion: str | None = None) -> None:
        """通过 CosyVoice2 HTTP 服务合成语音到指定文件。"""
        if not self._reference_audio:
            raise RuntimeError("CosyVoice2 未配置参考音频 reference_audio")
        if not self._reference_text:
            raise RuntimeError(
                "CosyVoice2 未配置参考音频文本 reference_text："
                "必须填参考音频里说的那句话（与音频逐字一致），否则会合成出乱叫的音节")

        # 【2026-10-01 F】emotion → Instruct2 自然语言指令；neutral/未知返回空串不注入，
        # 走零样本克隆原声（最自然），避免无意义指令引发音色漂移。
        instruct = emotion_to_instruct(emotion)
        payload: dict = {"text": text, "ref_wav_path": self._reference_audio, "ref_text": self._reference_text}
        if instruct:
            payload["instruct"] = instruct

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._api_url + "/tts", json=payload)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("error") or ""
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(f"CosyVoice2 合成失败: {detail} (HTTP {resp.status_code})")
            if not resp.content:
                raise RuntimeError("CosyVoice2 返回空音频")
            with open(output_path, "wb") as f:
                f.write(resp.content)

    async def synthesize_stream(self, text: str, emotion: str | None = None):
        """流式零样本克隆：逐块 yield int16 PCM（24000Hz 单声道）。

        服务端 stream=True 走 CosyVoice2 原生流式生成（LLM 逐 token + 逐块
        flow/hift），首块 1~2s 到达，客户端可边收边播降低首句延迟。
        """
        if not self._reference_audio:
            raise RuntimeError("CosyVoice2 未配置参考音频 reference_audio")
        if not self._reference_text:
            raise RuntimeError(
                "CosyVoice2 未配置参考音频文本 reference_text："
                "必须填参考音频里说的那句话（与音频逐字一致），否则会合成出乱叫的音节")

        # 【2026-10-01 F】emotion → Instruct2 指令（见 synthesize）。
        instruct = emotion_to_instruct(emotion)
        import httpx

        payload: dict = {
            "text": text, "ref_wav_path": self._reference_audio,
            "ref_text": self._reference_text, "stream": True,
        }
        if instruct:
            payload["instruct"] = instruct
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", self._api_url + "/tts", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    raise RuntimeError(f"CosyVoice2 流式合成失败 (HTTP {resp.status_code}): {body[:200]}")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk


class Pyttsx3Backend(TTSBackend):
    """pyttsx3 离线 TTS 后端（Windows SAPI5，无需联网，保证至少能出声）。"""

    def __init__(self, config: Optional[SimpleNamespace] = None) -> None:
        self._voice = getattr(config, "voice", "") if config else ""
        self._rate = getattr(config, "rate", "+0%") if config else "+0%"

    @property
    def name(self) -> str:
        return "pyttsx3"

    @property
    def output_ext(self) -> str:
        # SAPI5 合成输出为 WAV，统一走 pygame 播放（避免 edge-tts 的 mp3
        # 在部分环境被 pygame 的 drmp3 解码器判为 corrupt 而无声）。
        return ".wav"

    def update_config(self, config: SimpleNamespace) -> None:
        self._voice = getattr(config, "voice", "") if config else ""
        self._rate = getattr(config, "rate", "+0%") if config else "+0%"

    async def synthesize(self, text: str, output_path: str) -> None:
        """合成语音到 wav 文件（SAPI5 在 MTA 工作线程内可用，稳定可靠）。"""
        def _synth() -> None:
            import pyttsx3
            engine = pyttsx3.init()
            # 选中文嗓：config.voice 可能是 edge 风格名（如 zh-CN-XiaohanNeural），
            # SAPI 不认；匹配不到则回退默认嗓（本机默认即中文 Huihui）。
            want = (self._voice or "").lower()
            if want:
                for v in engine.getProperty("voices"):
                    vid = (v.id or "").lower()
                    vname = (v.name or "").lower()
                    if want in vid or want in vname:
                        engine.setProperty("voice", v.id)
                        break
            # 语速：支持 "+13%" / "-5%" 百分比，或纯数字（直接设值）
            rate = self._rate or "+0%"
            try:
                if "%" in rate:
                    base = engine.getProperty("rate")
                    pct = float(rate.replace("%", "").replace("+", ""))
                    engine.setProperty("rate", int(base * (1 + pct / 100)))
                else:
                    engine.setProperty("rate", int(float(rate)))
            except Exception:
                # 语速微调失败不致命：按默认语速合成（用户听感差异，不出声才是事故）
                pass
            engine.save_to_file(text, output_path)
            engine.runAndWait()

        await asyncio.to_thread(_synth)


class OpenAITTSBackend(TTSBackend):
    """OpenAI 兼容 TTS 后端（自定义 / 第三方语音 API 端口）。

    标准 OpenAI /audio/speech 协议，兼容各家的「OpenAI 兼容」语音端口：
      POST {api_url}/audio/speech
      headers: Authorization: Bearer {api_key}, Content-Type: application/json
      body: {model, input, voice, response_format}
    返回音频二进制写入 output_path。

    克隆模式（clone_mode=True 且提供参考音频）：以 multipart 附带参考音频 +
    参考文本，best-effort 适配支持声音克隆的第三方端点；不开启则走标准请求。
    """

    def __init__(self, config: SimpleNamespace) -> None:
        raw = (getattr(config, "api_url", "") or "").rstrip("/")
        # 兼容用户填 `https://x/v1` 或 `https://x/v1/audio/speech`
        if raw and not raw.endswith("audio/speech"):
            raw = raw + "/audio/speech"
        self._api_url: str = raw
        self._api_key: str = getattr(config, "api_key", "") or ""
        self._model: str = getattr(config, "model", "") or ""
        # voice：优先显式 voice 名；未给则退参考文本（用户填的参考文本常作嗓名）
        self._voice: str = getattr(config, "voice", "") or getattr(config, "reference_text", "") or "alloy"
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._reference_text: str = getattr(config, "reference_text", "") or ""
        self._clone_mode: bool = bool(getattr(config, "clone_mode", False))
        self._timeout: float = float(getattr(config, "timeout_s", 60) or 60)

    @property
    def name(self) -> str:
        return "openai_tts"

    @property
    def output_ext(self) -> str:
        return ".mp3"

    @property
    def fallback_voice(self) -> str:
        return self._voice

    def update_config(self, config: SimpleNamespace) -> None:
        self.__init__(config)

    async def synthesize(self, text: str, output_path: str) -> None:
        if not self._api_url:
            raise RuntimeError("OpenAI 兼容 TTS 未配置服务地址（endpoint）")
        if not self._model:
            raise RuntimeError("OpenAI 兼容 TTS 未配置模型名")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        json_body = {
            "model": self._model,
            "input": text,
            "voice": self._voice,
            "response_format": "mp3",
        }
        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # 克隆模式：参考音频以 multipart 附带（best-effort，适配支持克隆的端点）
            if self._clone_mode and self._reference_audio and os.path.exists(self._reference_audio):
                try:
                    with open(self._reference_audio, "rb") as f:
                        audio_bytes = f.read()
                    data = {k: str(v) for k, v in json_body.items()}
                    data["reference_text"] = self._reference_text
                    files = {"file": (os.path.basename(self._reference_audio), audio_bytes)}
                    resp = await client.post(
                        self._api_url,
                        headers={"Authorization": headers["Authorization"]},
                        data=data, files=files,
                    )
                except Exception:
                    resp = await client.post(self._api_url, headers=headers, json=json_body)
            else:
                resp = await client.post(self._api_url, headers=headers, json=json_body)
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = (resp.json().get("error", {}) or {}).get("message") or resp.text[:400]
                except Exception:
                    detail = resp.text[:400]
                raise RuntimeError(f"OpenAI 兼容 TTS 合成失败 (HTTP {resp.status_code}): {detail}")
            if not resp.content:
                raise RuntimeError("OpenAI 兼容 TTS 返回空音频")
            with open(output_path, "wb") as f:
                f.write(resp.content)


class DashScopeCosyBackend(TTSBackend):
    """阿里云百炼 CosyVoice / Qwen-Audio-TTS 云端克隆后端（dashscope_cosy）。

    走百炼多模态生成接口做声音合成：
      POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
      body: {model:<合成模型>, input:{prompt, tts:{voice:<克隆音色id> 或 audio:...}},
             parameters:{format:"mp3"}}
    返回 output.choices[0].message.content[0].audio.url（临时 OSS 链接）→ 下载到 output_path。

    关键：合成所用的 model 必须与「控制台克隆该音色时用的 model」一致（如 qwen-audio-3.0-tts-plus
    / cosyvoice-v3-plus 等），否则百炼报 'Model not exist'。model 由配置 model 字段决定，默认
    qwen-audio-3.0-tts-plus。优先用控制台已克隆的 voice_id；未提供 voice_id 才回退参考音频即时克隆。
    """

    # 两个端点：Qwen-Audio-TTS 系走语音合成接口（HTTP 合成实际使用）。
    # ⚠️ 2026-10-12 实测：`multimodal-generation/generation` 对 TTS **恒返回
    # 400 InvalidParameter "url error, please check url"**（与请求体无关，是端点本身
    # 不是语音合成口）。CosyVoice / Qwen-Audio 的 HTTP 合成统一走 _ENDPOINT_QWEN。
    # _ENDPOINT_COSY 已无引用，保留仅为文档说明，勿再启用。
    # 另注：百炼 engine 418 = 该 voice_id 不可用（复刻音色过期 / 未生效 / 地域不符），
    # 与 model 匹配无关——排查时先换一个已知可用的 voice_id 交叉验证，别在 model 上打转。
    # 【2026-10-17 实测纠正·端点与模型族的正确对应】
    #  · Qwen-TTS 系（qwen3-tts-flash）→ 多模态生成端点，**官方文档现行用法**；
    #  · CosyVoice 系（cosyvoice-v3.5-flash 等）→ SpeechSynthesizer 端点。
    # 之前把前者标注为 "DEPRECATED" 并把后者当唯一端点 → qwen 系恒 400/411。
    _ENDPOINT_QWEN_TTS = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    _ENDPOINT_COSY = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    # 兼容旧引用（同值不同名，避免别处 import 报错）
    _ENDPOINT_QWEN = _ENDPOINT_COSY
    _ENDPOINT_COSY_DEPRECATED = _ENDPOINT_QWEN_TTS

    # 实时（WebSocket）合成端点：首包 ~300ms，用于双工通话压低首字延迟。
    _REALTIME_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    # 百炼在 voice_id 前缀里编码了「克隆时用的模型」（形如
    # qwen-audio-3.0-tts-plus-bailian-<hash>）。合成必须用同一个 model：
    # 填错（实测 plus 的 voice 配 flash 的 model）服务端直接 400
    # InvalidParameter / [cosyvoice]Engine error [411]，且报错完全不提 model，极难排查。
    _KNOWN_MODELS = (
        # 【2026-10-17】官方现役 Qwen-TTS（实测 200 可用）
        "qwen3-tts-flash",
        # 以下 qwen-audio-3.x-tts 实测**全部 400**（百炼已下线/改名），仅保留用于
        # 识别历史 voice_id 前缀，避免误判成 cosyvoice 族。
        "qwen-audio-3.0-tts-plus", "qwen-audio-3.0-tts-flash",
        "cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash", "cosyvoice-v3-flash",
        "cosyvoice-v2", "cosyvoice-v1",
    )
    # 走 Qwen-TTS（多模态生成）端点的模型前缀
    _QWEN_TTS_PREFIXES = ("qwen3-tts", "qwen-audio-3")

    @classmethod
    def _model_from_voice_id(cls, voice_id: str) -> str:
        """从 voice_id 前缀反推克隆时用的模型；认不出返回空串。"""
        vid = (voice_id or "").strip()
        if not vid:
            return ""
        # 长优先，避免被更短的近似前缀误吃
        for m in sorted(cls._KNOWN_MODELS, key=len, reverse=True):
            if vid == m or vid.startswith(m + "-"):
                return m
        return ""

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_key: str = getattr(config, "api_key", "") or ""
        self._reference_audio: str = getattr(config, "reference_audio", "") or ""
        self._reference_text: str = getattr(config, "reference_text", "") or ""
        self._voice_id: str = getattr(config, "voice_id", "") or ""
        # 合成所用的模型：必须与控制台克隆音色时用的 model 一致，否则报 Model not exist。
        # 默认 qwen-audio-3.0-tts-plus（当前旗舰）；用户克隆在 cosyvoice-v3-plus 等下则相应改。
        # 【2026-10-17 修复·默认模型已下线】qwen-audio-3.0-tts-plus 实测 400，
        # 换成实测可用的 qwen3-tts-flash。
        self._model_declared: str = getattr(config, "model", "") or "qwen3-tts-flash"
        # 以 voice_id 前缀为准（权威）：配置写错时自动纠正并告警，避免整条链路静默降级到
        # edge-tts（用户听到的是微软音色，却完全不知道百炼失败了）。
        self._model: str = self._model_declared
        _derived = self._model_from_voice_id(self._voice_id)
        if _derived and _derived != self._model_declared:
            logger.warning(
                "阿里云百炼: voice_id 属于 %s，与配置 model=%r 不一致（百炼会报 400）→ 以 voice_id 为准",
                _derived, self._model_declared)
            self._model = _derived
        self._timeout: float = float(getattr(config, "timeout_s", 90) or 90)
        # 【2026-09-23 新增】云端音高/语速微调（百炼 parameters.pitch / rate，1.0=原样）。
        # 背景：本账号达妮娅音色（cosyvoice-v3.5-flash-bailian-277c9d79…）实测 F0≈275Hz，
        # 而克隆素材 assets/voices/daniya_ref.wav 的 F0≈242Hz —— **音高天然偏高约 13.5%**
        # （约 2.2 个半音），听感即「是达妮娅但不像达妮娅」（更尖更年轻）。
        # 实测 pitch=0.88 后 F0=242Hz、10~90% 分位 204–343Hz，与真源 242/203–345 完全重合。
        # 只有偏离 1.0 才下发，避免给不支持的模型平添未知参数；字段名刻意避开 edge-tts
        # 的字符串型 pitch/rate（如 "-4Hz"/"+13%"），互不干扰。
        try:
            self._pitch_scale: float = float(getattr(config, "pitch_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            self._pitch_scale = 1.0
        try:
            self._rate_scale: float = float(getattr(config, "rate_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            self._rate_scale = 1.0
        # 【2026-09-23】热备连接：预建一条已过 task-started 的 WS 待命，
        # 下一句直接发文本，省掉 TLS/WS 握手 + run-task 往返（实测 ~0.5s）。
        self._hot_standby = None   # {"ws":..., "task_id":..., "ts":float} 或 None
        self._hot_task = None      # 后台预连接 asyncio.Task

    # —— 热备连接 ——
    _HOT_MAX_AGE_S = 120.0  # 预连接闲置超过该时长即弃用（服务端可能已掐空闲连接）

    def _spawn_hot_standby(self) -> None:
        """后台预建一条已到 task-started 的连接，供下一句合成即取即用。失败静默置空。"""
        import asyncio
        import json as _json
        import time as _time
        import uuid as _uuid

        async def _prep() -> None:
            ws = None
            try:
                import websockets  # noqa: F401  （模块级未导入，此处本地引入）
                headers = {
                    "Authorization": f"bearer {self._api_key}",
                    "X-DashScope-DataInspection": "enable",
                }
                task_id = str(_uuid.uuid4())
                ws = await websockets.connect(
                    self._REALTIME_WS_URL, additional_headers=headers,
                    max_size=None, open_timeout=self._timeout, close_timeout=5,
                )
                await ws.send(_json.dumps({
                    "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {
                        "task_group": "audio", "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": self._model or "qwen-audio-3.0-tts-plus",
                        # 【2026-09-23】热备必须与正式请求同参数（含 pitch/rate），
                        # 否则走热备的那几句音高会与其余句不一致。
                        "parameters": self._ws_params(),
                        "input": {},
                    },
                }))
                # 等到 task-started 才算热备就绪
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                    if isinstance(raw, (bytes, bytearray)):
                        break
                    msg = _json.loads(raw)
                    ev = (msg.get("header") or {}).get("event", "")
                    if ev == "task-started":
                        break
                    if ev in ("task-failed", "error"):
                        raise RuntimeError((msg.get("header") or {}).get("error_message") or str(msg)[:200])
                self._hot_standby = {"ws": ws, "task_id": task_id, "ts": _time.time()}
                logger.info("百炼 TTS 热备连接就绪（下一句免握手）")
            except Exception as e:  # 取不到必须返回 None，绝不静默吞错
                logger.warning("百炼 TTS 热备连接预建失败（回退全新握手）: %s", e)
                self._hot_standby = None
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        # 关闭失败的残留连接由超时/GC 回收（上面已 warning 并回退新握手）
                        pass

        try:
            self._hot_task = asyncio.get_running_loop().create_task(_prep())
        except RuntimeError:
            self._hot_task = None

    async def _take_hot_standby(self):
        """取热备连接（弹出）；过期/不可用返回 None。"""
        hot = getattr(self, "_hot_standby", None)
        self._hot_standby = None
        if hot is None:
            return None
        import time as _time
        if _time.time() - hot["ts"] > self._HOT_MAX_AGE_S:
            try:
                await hot["ws"].close()
            except Exception:
                # 关旧热备失败不影响下面的全新握手（照常重建）
                pass
            logger.info("百炼 TTS 热备连接闲置过久（>%ds），弃用", int(self._HOT_MAX_AGE_S))
            return None
        return hot

    @property
    def name(self) -> str:
        return "dashscope_cosy"

    @property
    def output_ext(self) -> str:
        return ".mp3"

    @property
    def fallback_voice(self) -> str:
        return self._voice_id or "cosyvoice"

    # 【2026-09-15 新增】百炼支持 WebSocket 实时合成：首包 ~300ms，边收 PCM 边播。
    # 非流式 HTTP 路径要等「整句合成 → 返回 OSS 链接 → 整段下载 → ffplay 启动」，
    # 实测 6s 才出声（见 2026-09-15 通话日志）；流式可把首字延迟压到 1s 内。
    # 引擎名仍是 dashscope_cosy，配置 `duplex_tts_stream: false` 可强制退回整句。
    @property
    def supports_stream(self) -> bool:
        return True

    @property
    def stream_sample_rate(self) -> int:
        return 24000

    def _ws_params(self) -> dict:
        """WebSocket 实时合成的 parameters（含可选的音高/语速微调）。

        【2026-09-23】pitch / rate 为百炼 cosyvoice 的可选参数（1.0 = 原样）。
        本账号达妮娅音色天然偏高约 13.5%，靠 pitch_scale=0.88 拉回真源音高。
        **热备连接与正式握手必须共用本方法**，否则走热备的那几句会与其余句音高不一致。
        """
        params: dict = {
            "text_type": "PlainText",
            "voice": self._voice_id,
            "format": "pcm",
            "sample_rate": self.stream_sample_rate,
        }
        if abs(self._pitch_scale - 1.0) > 1e-6:
            params["pitch"] = self._pitch_scale
        if abs(self._rate_scale - 1.0) > 1e-6:
            params["rate"] = self._rate_scale
        return params

    def update_config(self, config: SimpleNamespace) -> None:
        self.__init__(config)

    async def synthesize_stream(self, text: str, emotion: str | None = None):
        """百炼 WebSocket 实时合成：逐块 yield int16 PCM（24000Hz 单声道）。

        协议（北京地域）：
          wss://dashscope.aliyuncs.com/api-ws/v1/inference
          run-task    → payload.model / payload.parameters.voice
          task-started→ continue-task(text) → finish-task
          服务端随后下发 result-generated(JSON) 与二进制音频帧 → task-finished

        格式用 pcm（16bit/24k/mono），二进制帧可**直接**喂 sounddevice 播放，
        无需等待整段 mp3 落盘（这是首字延迟从 6s 降到 <1s 的关键）。
        """
        if not self._api_key:
            raise RuntimeError("阿里云百炼 CosyVoice 未配置 API Key")
        if not self._voice_id:
            raise RuntimeError("阿里云百炼实时合成需填已克隆的 voice_id")

        import asyncio
        import json
        import uuid
        try:
            import websockets
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("缺少 websockets 库，无法走百炼实时合成") from e

        url = self._REALTIME_WS_URL
        headers = {
            "Authorization": f"bearer {self._api_key}",
            "X-DashScope-DataInspection": "enable",
        }
        task_id = str(uuid.uuid4())
        hdr_run = {"action": "run-task", "task_id": task_id, "streaming": "duplex"}
        hdr_cont = {"action": "continue-task", "task_id": task_id, "streaming": "duplex"}
        hdr_fin = {"action": "finish-task", "task_id": task_id, "streaming": "duplex"}
        model = self._model or "qwen-audio-3.0-tts-plus"
        # 【2026-09-23】原两个分支（qwen-audio / 其他）参数完全一致，是历史复制；
        # 统一走 _ws_params()，并顺带带上可选的音高/语速微调。
        params = self._ws_params()
        run_payload = {
            "header": hdr_run,
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": model,
                "parameters": params,
                "input": {},
            },
        }

        ws = None
        via_hot = False
        # —— 热备优先：连接与 run-task→task-started 已就绪，直接发文本 ——
        hot = await self._take_hot_standby()
        if hot is not None:
            try:
                ws = hot["ws"]
                task_id = hot["task_id"]
                hdr_cont["task_id"] = task_id
                hdr_fin["task_id"] = task_id
                await ws.send(json.dumps({"header": hdr_cont, "payload": {"input": {"text": text}}}))
                await ws.send(json.dumps({"header": hdr_fin, "payload": {"input": {}}}))
                via_hot = True
                logger.info("百炼 TTS 走热备连接（免握手）task=%s", task_id)
            except Exception as e:  # 热备失效：关掉并回退全新握手
                logger.warning("百炼 TTS 热备连接发送失败(%s)，回退全新握手", e)
                try:
                    await ws.close()
                except Exception:
                    # 关掉这条失效连接失败就让它留在原地超时（已 warning 并走新握手）
                    pass
                ws = None
                via_hot = False
        if ws is None:
            task_id = str(uuid.uuid4())
            hdr_run = {"action": "run-task", "task_id": task_id, "streaming": "duplex"}
            hdr_cont = {"action": "continue-task", "task_id": task_id, "streaming": "duplex"}
            hdr_fin = {"action": "finish-task", "task_id": task_id, "streaming": "duplex"}
            ws = await websockets.connect(
                url, additional_headers=headers, max_size=None,
                open_timeout=self._timeout, close_timeout=5,
            )
            await ws.send(json.dumps(run_payload))
            started = False
            # 等 task-started（首个非二进制文本帧即 task-started）
            while not started:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                if isinstance(raw, (bytes, bytearray)):
                    # 理论上不会在 task-started 前收到音频；若有则直接透传
                    started = True
                    yield bytes(raw)
                    break
                msg = json.loads(raw)
                ev = (msg.get("header") or {}).get("event", "")
                if ev == "task-started":
                    started = True
                elif ev in ("task-failed", "error"):
                    detail = (msg.get("header") or {}).get("error_message") or msg.get("payload", {}).get("message") or raw
                    raise RuntimeError(f"阿里云百炼实时合成失败: {detail}")
            await ws.send(json.dumps({"header": hdr_cont, "payload": {"input": {"text": text}}}))
            await ws.send(json.dumps({"header": hdr_fin, "payload": {"input": {}}}))

        try:
            finished = False
            while not finished:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                if isinstance(raw, (bytes, bytearray)):
                    if raw:
                        yield bytes(raw)
                    continue
                msg = json.loads(raw)
                ev = (msg.get("header") or {}).get("event", "")
                if ev in ("task-finished", "task-failed", "error"):
                    if ev != "task-finished":
                        detail = (msg.get("header") or {}).get("error_message") or msg.get("payload", {}).get("message") or raw
                        raise RuntimeError(f"阿里云百炼实时合成失败: {detail}")
                    finished = True
                # result-generated / 其他中间事件忽略
        finally:
            # 本次任务结束：关掉用过的连接，并为下一句预建热备
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    # 收尾关闭失败不改变任务结果（音频已播出），残留连接会超时
                    pass
            try:
                self._spawn_hot_standby()
            except Exception as e:
                logger.warning("百炼 TTS 热备预建调度失败: %s", e)

    async def synthesize(self, text: str, output_path: str) -> None:
        if not self._api_key:
            raise RuntimeError("阿里云百炼 CosyVoice 未配置 API Key")
        model = self._model or "qwen3-tts-flash"
        # 【2026-10-17 修复·按模型族选端点】Qwen-TTS 系走多模态生成端点（官方现行），
        # CosyVoice 系走 SpeechSynthesizer。此前一律走后者 → qwen 系恒 400/411。
        is_qwen = any(str(model).startswith(p) for p in self._QWEN_TTS_PREFIXES)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if is_qwen:
            # Qwen-TTS 系：官方形状 = input{text, voice, language_type}
            endpoint = self._ENDPOINT_QWEN_TTS
            if not self._voice_id:
                # 官方支持系统预置音色；未绑音色时用 Cherry（文档示例音色）
                self._voice_id = "Cherry"
            _inp = {"text": text, "voice": self._voice_id, "language_type": "Chinese"}
            body = {"model": model, "input": _inp}
        else:
            # CosyVoice 系：走语音合成端点（SpeechSynthesizer），文本/音色放在 input 内
            # 【2026-09-23 修复】原来用 multimodal-generation 端点 + input.prompt/tts.voice，
            # 实测该组合对本 voice_id **必然 400**（InvalidParameter: url error）。
            # 非流式这条路只在“流式被关/后端不支持流式”时才会走到，一旦走到就静默失败，
            # 进而降级 edge-tts——用户听到的「微软小薇」就是这么来的。
            # 改用实测 200 的组合：SpeechSynthesizer + input={text,voice,format}。
            endpoint = self._ENDPOINT_COSY
            # 【2026-09-23】非流式同样带上音高/语速微调（pitch/rate，1.0=不下发）
            _params: dict = {"format": "mp3"}
            if abs(self._pitch_scale - 1.0) > 1e-6:
                _params["pitch"] = self._pitch_scale
            if abs(self._rate_scale - 1.0) > 1e-6:
                _params["rate"] = self._rate_scale
            if self._voice_id:
                _input = {"text": text, "voice": self._voice_id, "format": "mp3"}
            elif self._reference_audio and os.path.exists(self._reference_audio):
                with open(self._reference_audio, "rb") as f:
                    ref_b64 = base64.b64encode(f.read()).decode("ascii")
                ext = os.path.splitext(self._reference_audio)[1].lower()
                mime = "audio/wav" if ext in (".wav",) else "audio/mpeg"
                # 注意：零样本参考音频走本端点的写法**未实测**，失败会自动落兜底链
                _input = {"text": text, "voice": "克隆音色", "format": "mp3",
                          "audio": f"data:{mime};base64,{ref_b64}"}
            else:
                raise RuntimeError("阿里云百炼（CosyVoice）需提供已克隆的 voice_id，或提供参考音频")
            body = {
                "model": model,
                "input": _input,
                "parameters": _params,
            }
        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(endpoint, headers=headers, json=body)
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = (resp.json().get("message") or str(resp.json().get("code")) or resp.text[:400])
                except Exception:
                    detail = resp.text[:400]
                raise RuntimeError(f"阿里云百炼合成失败 (HTTP {resp.status_code}): {detail}")
            # 解析音频：兼容两种端点响应格式 + 直接返回二进制
            audio_bytes = None
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                try:
                    payload = resp.json()
                    out = payload.get("output") or {}
                    # Qwen-Audio 端点：output.audio.data (base64) 或 output.audio.url
                    audio = out.get("audio") or {}
                    if audio.get("data"):
                        audio_bytes = base64.b64decode(audio["data"])
                    elif audio.get("url"):
                        dl = await client.get(audio["url"]); dl.raise_for_status(); audio_bytes = dl.content
                    # CosyVoice 端点：output.choices[0].message.content[0].audio
                    if not audio_bytes:
                        content = out["choices"][0]["message"]["content"][0]
                        a = content.get("audio", {}) or {}
                        if a.get("data"):
                            audio_bytes = base64.b64decode(a["data"])
                        elif a.get("url"):
                            dl = await client.get(a["url"]); dl.raise_for_status(); audio_bytes = dl.content
                except Exception as e:
                    raise RuntimeError(f"阿里云百炼返回格式无法解析：{e}")
            else:
                # 直接返回音频二进制
                audio_bytes = resp.content
            if not audio_bytes:
                raise RuntimeError("阿里云百炼返回空音频")
            with open(output_path, "wb") as f:
                f.write(audio_bytes)


class FishAudioBackend(TTSBackend):
    """Fish Audio 云端 TTS 后端（2026-10-09 新增，能力槽预设服务商之一）。

    通过 Fish Audio 官方 API 合成语音：
    - 接口：POST https://api.fish.audio/v1/tts
    - Header: Authorization: Bearer {api_key}（可选 model: s2-pro）
    - Body: {text, reference_id, format: "mp3"}
    - reference_id = 用户在 Fish Audio 控制台克隆出的音色模型 id
      （见 src/models/cloud_tts.py 的 Fish Audio 列表分支：
       GET https://api.fish.audio/model?self=true 拿到每个模型的 _id）
    - 返回：直接返回 mp3 二进制；或 JSON {audio_url}（同步任务）时二次下载。
    """

    def __init__(self, config: SimpleNamespace) -> None:
        self._api_key: str = getattr(config, "api_key", "") or ""
        self._reference_id: str = getattr(config, "voice_id", "") or getattr(config, "reference_id", "") or ""
        self._model: str = getattr(config, "model", "s2-pro") or "s2-pro"
        self._timeout: float = float(getattr(config, "timeout_s", 60) or 60)

    @property
    def name(self) -> str:
        return "fish_audio"

    def update_config(self, config: SimpleNamespace) -> None:
        self._api_key = getattr(config, "api_key", self._api_key) or self._api_key
        rid = getattr(config, "voice_id", "") or getattr(config, "reference_id", "") or ""
        if rid:
            self._reference_id = rid
        if getattr(config, "model", ""):
            self._model = getattr(config, "model", self._model)

    async def synthesize(self, text: str, output_path: str) -> None:
        if not self._api_key:
            raise RuntimeError("Fish Audio 未配置 API Key（fish.audio 注册获取，用户自费）")
        if not self._reference_id:
            raise RuntimeError("Fish Audio 未配置 reference_id（音色模型 id，在控制台克隆后获取）")

        url = "https://api.fish.audio/v1/tts"
        payload = {
            "text": text,
            "reference_id": self._reference_id,
            "format": "mp3",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._model:
            headers["model"] = self._model

        import httpx
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("message") or resp.json().get("error") or resp.text[:300]
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(f"Fish Audio 合成失败 (HTTP {resp.status_code}): {detail}")
            ctype = resp.headers.get("content-type", "") or ""
            if "application/json" in ctype:
                data = resp.json() or {}
                audio_url = data.get("audio_url") or (data.get("data") or {}).get("audio_url") or ""
                if audio_url:
                    dl = await client.get(audio_url)
                    audio_bytes = dl.content
                else:
                    raise RuntimeError("Fish Audio 返回 JSON 但缺少 audio_url")
            else:
                audio_bytes = resp.content
            if not audio_bytes:
                raise RuntimeError("Fish Audio 返回空音频")
            with open(output_path, "wb") as f:
                f.write(audio_bytes)


def create_backend(engine: str, config: SimpleNamespace) -> TTSBackend:
    """工厂函数：根据 engine 名称创建对应后端。"""
    engine_map: dict[str, type[TTSBackend]] = {
        # 【2026-10-16 发布口径】edge-tts 已按用户要求**下架**：不再出现在引擎映射里，
        # 于是任何遗留路径（旧配置里存了 edge-tts、或某处默认值写死它）都会安全落到
        # 下面的"未绑定"静音后端，而不是又偷偷用微软在线音色。
        # "edge-tts": EdgeTTSBackend,   ← 已下架（类保留仅供内部文档/回退参考）
        "gpt-sovits": GPTSoVITSBackend,
        "minimax": MiniMaxBackend,
        "voxcpm": VoxCpmBackend,
        "qwen3tts": Qwen3TtsBackend,
        "cosyvoice": CosyVoiceBackend,
        "mimo_tts": MimoTtsBackend,
        "pyttsx3": Pyttsx3Backend,
        "openai_tts": OpenAITTSBackend,
        "dashscope_cosy": DashScopeCosyBackend,
        "fish_audio": FishAudioBackend,
    }
    backend_class = engine_map.get(engine)
    if backend_class is None:
        # 【2026-10-16 发布口径·默认不绑任何声音】引擎为空/未知时**不再默认落到 edge-tts**。
        # 理由（用户明确要求）：① 默认不对声音做任何绑定；② 不再使用 edge-tts；
        # ③ 只允许用户绑定自己的服务商，并用其账号里的声音 ID。
        # 这里返回一个"静音后端"：接口与其它后端一致、但不出声，也不会去碰任何在线服务；
        # 好处是调用方（tts.py / webapp 试听）拿到的仍是对象，不会因 None 而崩。
        class _SilentBackend(TTSBackend):
            """未绑定任何声音时的占位后端：接口齐全、不出声、不联网。"""

            def __init__(self, config=None):     # create_backend 固定以 backend_class(config) 构造
                self._cfg = config

            @property
            def name(self) -> str:
                return "未绑定"

            # 【2026-10-17 修复·我把异步契约写坏了】synthesize 在本项目里是 **async**
            # （如 DashScopeCosyBackend.synthesize 就是协程），调用方走 `await backend.speak(...)`，
            # 而基类 speak 会 `await self.synthesize(...)`。此前我把它写成同步函数（返回 None），
            # 于是出现 `object NoneType can't be used in 'await' expression`（实测日志）。
            # 现在：synthesize 写成 async；**不再覆盖 speak**（覆盖成同步会再次破坏 await）。
            async def synthesize(self, text: str, output_path: str) -> None:  # noqa: D102
                logger.info(
                    "TTS 未绑定音色：本次跳过出声。"
                    "去 设置 → 语音 · 音色，选一个服务商并填入音色 ID；"
                    "若用阿里云百炼，点「阿里云一键」会自动配好 TTS；"
                    "只想先听个声，可在语音页点「试听」用默认音色。")
                return None

            def update_config(self, config: SimpleNamespace = None) -> None:  # noqa: D102
                self._cfg = config

        backend_class = _SilentBackend
    return backend_class(config)
