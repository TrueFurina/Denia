"""唤醒词检测：Vosk 语法约束模式。

用 KaldiRecognizer 的 grammar 参数把识别锁死在唤醒词表内，命中 Result 即唤醒，误触极低。
语法必须是 JSON 数组字符串，中文需 ensure_ascii=False。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from vosk import KaldiRecognizer

logger = logging.getLogger(__name__)


class WakeWordDetector:
    def __init__(self, model, sample_rate: int, wake_words: list[str]) -> None:
        self._wake_words = wake_words
        # 语法约束：只识别词表内的词 + [unk]
        grammar = json.dumps(wake_words + ["[unk]"], ensure_ascii=False)
        self._rec = KaldiRecognizer(model, sample_rate, grammar)
        self._rec.SetWords(True)

    def detect(self, data: bytes) -> Optional[str]:
        """返回命中的唤醒词，未命中返回 None。"""
        if self._rec.AcceptWaveform(data):
            text = json.loads(self._rec.Result()).get("text", "")
            for w in self._wake_words:
                if w in text:
                    return w
        return None
