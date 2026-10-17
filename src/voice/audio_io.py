"""麦克风音频采集：sounddevice RawInputStream → queue.Queue。

Vosk 要求 16kHz / 16-bit / 单声道 PCM。sounddevice 在 callback 里把原始字节入队，
识别线程从队列取数据喂给 KaldiRecognizer。
"""
from __future__ import annotations

import logging
import queue

import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioIO:
    def __init__(self, sample_rate: int = 16000, blocksize: int = 8000,
                 device=None) -> None:
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._device = device  # None=系统默认输入设备；int=指定索引
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stream = None

    def _callback(self, indata, frames, time, status) -> None:  # noqa: A002
        if status:
            logger.debug("音频输入状态: %s", status)
        self._queue.put(bytes(indata))

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
