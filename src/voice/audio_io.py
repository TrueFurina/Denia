"""麦克风音频采集：sounddevice RawInputStream → queue.Queue。

Vosk 要求 16kHz / 16-bit / 单声道 PCM。sounddevice 在 callback 里把原始字节入队，
识别线程从队列取数据喂给 KaldiRecognizer。
"""
from __future__ import annotations

import logging
import queue

import sounddevice as sd

from ..queue_util import put_drop_oldest

logger = logging.getLogger(__name__)

# 采集缓冲上限（帧数）。blocksize=8000 @16kHz 时一帧 0.5s，32 帧 ≈ 16 秒。
# 识别线程卡住超过这个时长，丢掉最旧的音频比无限涨内存合理 —— 迟到的语音
# 本来也不该再被识别。回调线程绝不能 put() 阻塞，只能用 put_nowait 丢最旧。
_MAX_QUEUE_FRAMES = 32


class AudioIO:
    def __init__(self, sample_rate: int = 16000, blocksize: int = 8000,
                 device=None) -> None:
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._device = device  # None=系统默认输入设备；int=指定索引
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=_MAX_QUEUE_FRAMES)
        self._dropped = 0
        self._stream = None

    def _callback(self, indata, frames, time, status) -> None:  # noqa: A002
        if status:
            logger.debug("音频输入状态: %s", status)
        if put_drop_oldest(self._queue, bytes(indata)):
            self._dropped += 1
            # 每 100 帧报一次，避免回调里刷日志反过来拖慢音频线程
            if self._dropped % 100 == 1:
                logger.warning("麦克风缓冲已满，累计丢弃 %d 帧（识别线程跟不上采集）",
                               self._dropped)

    def start(self) -> None:
        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            blocksize=self._blocksize,
            dtype="int16",
            channels=1,
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()
        logger.info("麦克风采集启动: %sHz 单声道 16-bit", self._sample_rate)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def get(self, timeout: float = 0.1) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
