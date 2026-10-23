# -*- coding: utf-8 -*-
"""单工轮询（simplex polling）语音会话管线。

架构：ASR(本地 sherpa-onnx) + LLM(项目云端文本端口池) + TTS(阿里云百炼 dashscope_cosy)

与双工通话的本质区别：
  - 双工（MiniCPM-o）：边听边说、可打断，但内置 Token2Wav 声码器偏"糊"，且 9B 模型重。
  - 本管线（单工轮询）：用户说完一段 → 整段送 ASR → 文本送 LLM → 文本送百炼 TTS → 回放。
    串行、可被打断（靠 UI 端 VAD/按钮），但不"边听边说"。优点：轻（CPU ASR + 云端）、
    快、音色走云端克隆（与文字聊天/双工通话完全同一 voice_id），稳定。

TTS 复用 DashScopeCosyBackend（引擎 dashscope_cosy），因此"和双工一样接云端阿里云百炼"——
用的是项目里已配置的同一个百炼克隆音色，无需另行设置。

依赖：
  - ASR：sherpa_onnx（本地，src.voice.stt.transcribe_pcm）
  - LLM：src.llm.text_chat_router（云端 OpenAI 兼容端口池，见 config.yaml text_chat）
  - TTS：src.voice.tts_backends.DashScopeCosyBackend（百炼 dashscope_cosy）
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Optional

logger = logging.getLogger(__name__)

# 必须与浏览器端采集采样率一致
TARGET_SR = 16000


class SimplexSession:
    """一次单工轮询通话的会话对象（按 char_id 或当前激活角色构建）。"""

    def __init__(self, cfg, char_id: Optional[str] = None) -> None:
        self._cfg = cfg
        self._char_id = char_id
        # LLM：复用项目的纯文本聊天路由（云端端口池，轻量稳定）
        from ..llm.text_chat_router import create_text_chat_router

        self._llm = create_text_chat_router(cfg)
        # TTS：百炼 dashscope_cosy，复用当前云端音色配置
        self._tts = self._build_tts(cfg, char_id)
        # 系统提示：取角色人设
        self._system_prompt = self._build_prompt(cfg, char_id)
        # 多轮上下文（仅本会话内）
        self._history: list[dict] = []
        self._turns = 0
        self._cancel = False  # 打断标志：前端发起打断时置 True，流式管线据此中止 TTS 生成

    # ===================== 配置解析 =====================
    def _build_tts(self, cfg, char_id):
        from ..voice.tts_backends import create_backend

        prof = self._resolve_cloud_voice(cfg, char_id)
        if prof is None:
            logger.warning(
                "单工轮询：未找到 dashscope_cosy 云端音色配置，TTS 将失败；"
                "请在设置里添加阿里云百炼音色档（引擎 dashscope_cosy）。")
            ns = SimpleNamespace(
                api_key="", voice_id="", model="qwen-audio-3.0-tts-plus",
                reference_audio="", reference_text="")
        else:
            ns = SimpleNamespace(
                api_key=getattr(prof, "api_key", "") or "",
                voice_id=getattr(prof, "voice_id", "") or "",
                model=getattr(prof, "model", "") or "qwen-audio-3.0-tts-plus",
                reference_audio=getattr(prof, "reference_audio", "") or "",
                reference_text=getattr(prof, "reference_text", "") or "",
            )
        return create_backend("dashscope_cosy", ns)

    def _resolve_cloud_voice(self, cfg, char_id):
        """解析百炼云端音色：优先当前角色引用的 dashscope_cosy 档，否则任一 dashscope_cosy 档。"""
        # 1) 当前激活角色的音色配置
        active = cfg.get_active_voice_config()
        if active is not None and str(getattr(active, "engine", "") or "") == "dashscope_cosy":
            return active
        # 2) 全局音色档里找 dashscope_cosy
        for p in (cfg.list_voice_profiles() or []):
            if str(p.get("engine", "") or "") == "dashscope_cosy":
                return _to_ns_profile(p)
        return None

    def _build_prompt(self, cfg, char_id) -> str:
        char = cfg.get_active_character()
        if char is None and char_id:
            # 指定角色但未激活：从 raw 找
            for c in (cfg.raw.get("characters", []) or []):
                if c.get("id") == char_id:
                    char = SimpleNamespace(**c) if isinstance(c, dict) else c
                    break
        if char is not None:
            sys_p = getattr(char, "system_prompt", None) or getattr(char, "prompt", None) or ""
            name = getattr(char, "name", "") or ""
            if sys_p:
                return f"你是{name}。{sys_p}" if name else str(sys_p)
        return "你是达妮娅，一个温柔的虚拟陪伴助手，用自然口语中文回复，不要长篇大论。"

    # ===================== 核心：处理一回合 =====================
    async def process_turn(self, pcm_int16: bytes, max_history: int = 12) -> dict:
        """跑一回合：ASR → LLM → TTS。

        返回：{user_text, reply_text, audio(bytes|None), audio_ext, asr_ms, llm_ms, tts_ms,
              total_ms, empty, tts_used_stream}
        """
        t0 = time.time()
        # 1) ASR（本地 sherpa-onnx，CPU 实时）
        from ..voice.stt import transcribe_pcm

        user_text = transcribe_pcm(pcm_int16, TARGET_SR).strip()
        asr_ms = int((time.time() - t0) * 1000)
        if not user_text:
            return self._empty_result(asr_ms, t0, reason="asr_empty")

        # 2) LLM（云端文本端口池，带冷却/兜底）
        messages = [{"role": "system", "content": self._system_prompt}]
        # 仅保留最近 max_history 条，避免上下文过长
        for m in self._history[-max_history:]:
            messages.append(m)
        messages.append({"role": "user", "content": user_text})
        t1 = time.time()
        try:
            reply = (await self._llm.chat(messages)) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("单工轮询 LLM 失败: %s", e)
            reply = ""
        llm_ms = int((time.time() - t1) * 1000)
        reply = reply.strip()
        if not reply:
            return self._empty_result(asr_ms, t0, llm_ms=llm_ms, reason="llm_empty",
                                      user_text=user_text)

        # 3) TTS（百炼 dashscope_cosy；优先流式 WS ~300ms 首包，退化整句）
        t2 = time.time()
        audio, audio_ext, used_stream = await self._synthesize(reply)
        tts_ms = int((time.time() - t2) * 1000)
        if audio is None:
            return self._empty_result(asr_ms, t0, llm_ms=llm_ms, tts_ms=tts_ms,
                                      reason="tts_failed", user_text=user_text, reply_text=reply)

        # 记录上下文
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": reply})
        self._turns += 1

        return {
            "user_text": user_text,
            "reply_text": reply,
            "audio": audio,
            "audio_ext": audio_ext,
            "asr_ms": asr_ms,
            "llm_ms": llm_ms,
            "tts_ms": tts_ms,
            "total_ms": int((time.time() - t0) * 1000),
            "empty": False,
            "tts_used_stream": used_stream,
            "turn": self._turns,
        }

    def _empty_result(self, asr_ms, t0, llm_ms=0, tts_ms=0, reason="",
                      user_text="", reply_text="") -> dict:
        return {
            "user_text": user_text, "reply_text": reply_text, "audio": None,
            "audio_ext": None, "asr_ms": asr_ms, "llm_ms": llm_ms, "tts_ms": tts_ms,
            "total_ms": int((time.time() - t0) * 1000), "empty": True, "reason": reason,
            "tts_used_stream": False, "turn": self._turns,
        }

    async def _synthesize(self, text: str):
        """返回 (audio_bytes|None, ext, used_stream)。优先百炼 WS 流式。"""
        if not text:
            return None, None, False
        try:
            if getattr(self._tts, "supports_stream", False):
                import io
                import wave

                buf = io.BytesIO()
                w = wave.open(buf, "wb")
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)  # 百炼实时合成 24k PCM
                async for chunk in self._tts.synthesize_stream(text):
                    if chunk:
                        w.writeframes(chunk)
                w.close()
                data = buf.getvalue()
                if data:
                    return data, "wav", True
            # 退化：整句合成（落临时文件再读）
            import os
            import tempfile

            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            try:
                await self._tts.synthesize(text, path)
                with open(path, "rb") as f:
                    data = f.read()
                if data:
                    return data, "mp3", False
            finally:
                try:
                    os.remove(path)
                except OSError:
                    # 合成用的临时文件删不掉由临时目录回收
                    pass
        except Exception as e:  # noqa: BLE001
            logger.exception("单工轮询 TTS 失败: %s", e)
        return None, None, False

    # ===================== 工具 =====================
    def reset(self) -> None:
        """清空多轮上下文（重新通话时调用）。"""
        self._history.clear()
        self._turns = 0


def _to_ns_profile(p: dict) -> SimpleNamespace:
    """把音色档 dict 转 SimpleNamespace（兼容 dict/list 嵌套）。"""
    if not isinstance(p, dict):
        return p
    from ..config import _to_ns  # noqa: PLC0415

    return _to_ns(p)
