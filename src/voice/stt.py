"""语音转文字（STT）入口：把一段 int16 PCM 音频转写成文字。

设计取舍（2026-09-01 语音模式落地）：
- 项目已有 sherpa-onnx 流式双语识别（Comni 本地双工就在用），质量远好于 whisper-tiny，
  且 CPU 实时、零 GPU。因此**不引入 whisper**，直接复用 SherpaRecognizer。
- 浏览器麦克风把 16k 单声道 int16 PCM 以 base64 传上来，这里喂给一个模块级缓存的
  识别器（避免每次请求都重载 ONNX 模型），流式喂帧、结尾 finalize 强制收尾。
- 懒加载：首次调用才 import sherpa_onnx 并建识别器，不拖慢 App 启动。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# 模块级单例：整个进程共用一个识别器（sherpa 非线程安全，加锁串行化喂帧）。
_rec: Optional["_RecognizerLike"] = None
_rec_lock = threading.Lock()
_TARGET_SR = 16000


def _get_recognizer():
    """懒加载并返回单例识别器（首次调用建实例，之后复用）。"""
    global _rec
    if _rec is not None:
        return _rec
    with _rec_lock:
        if _rec is None:
            from .sherpa_recognizer import SherpaRecognizer

            _rec = SherpaRecognizer()
    return _rec


def transcribe_pcm(pcm_int16: bytes, sample_rate: int = _TARGET_SR) -> str:
    """把一段 16k 单声道 int16 PCM 转写成文字。

    - pcm_int16: 原始 int16 小端字节（浏览器 AudioContext 以 16k 采集）。
    - 若实际采样率不是 16k，调用方应先重采样（本项目浏览器端已按 16k 采集）。
    返回转写文本（可能为空）。
    """
    if not pcm_int16 or len(pcm_int16) < 1000:
        return ""
    rec = _get_recognizer()
    # 按 ~0.4s 一帧喂入，对齐 sherpa 端点检测的静音窗口。
    step = int(sample_rate * 0.4) * 2
    parts: list[str] = []
    with _rec_lock:
        for i in range(0, len(pcm_int16), step):
            seg = pcm_int16[i : i + step]
            try:
                t = rec.feed(seg)
            except Exception:  # noqa: BLE001
                logger.debug("sherpa feed 异常", exc_info=True)
                t = ""
            if t:
                parts.append(t)
        # 结尾强制收尾：取出“已说但未触达静音端点”的尾巴，避免整段录音丢字。
        tail = rec.finalize()
    if tail:
        parts.append(tail)
    return "".join(parts).strip()
