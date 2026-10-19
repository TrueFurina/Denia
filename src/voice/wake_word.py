"""唤醒词检测：Vosk 语法约束模式 + 拼音容错匹配。

两段式设计（缺一段都醒不过来，2026-10-19 实测定稿）：

① 语法 = 唤醒字的【全部同音字】（BMP 内）+ [unk]。
   为什么不能只放唤醒字：small-cn 模型对她名字的解码是"达尼亚"——
   娅和亚同音，模型 Acoustically 更偏向亚。语法里只放"娅"时解码器被
   逼着在允许集里挑，实测吐出来是"达 妮 达"（da-ni-da），连拼音
   容错都救不回来。放进同音字后解码器能吐出真正的音（鞑妮呀/达妮娅），
   容错匹配才有得配。
   为什么不是无语法全词表：唤醒阶段的约束本身就是防误触的第一道闸，
   误触极低的特性靠它（负面长句实测两种语法下都是 [unk]）。

② 匹配 = 音节序列连续匹配，不是整字子串。
   small 模型的同音字替换是常态（娅→亚、薇→微），整字比对注定时灵
   时不灵；按 pypinyin 归一化后的音节序列比对，同音不同字也算命中，
   而"大南苑"（da-nan-yuan）这类近音词不会误中。

pypinyin 缺失时降级为整字子串匹配（启动不受影响，但同音字配不上）——
tools/doctor.py 的 check_wake_words 会显式报出该装包，不装也能跑只是
识别率打折，属"用户自己能修"的 WARN 级。
"""
from __future__ import annotations

import json
import logging
import unicodedata
from typing import Optional

from vosk import KaldiRecognizer

logger = logging.getLogger(__name__)

try:  # pypinyin 是"重要"级依赖：缺了不崩，退化见 docstring
    from pypinyin import Style, pinyin
    from pypinyin.pinyin_dict import pinyin_dict as _PINYIN_DICT_RAW
    _HAS_PINYIN = True
except Exception:  # noqa: BLE001
    _HAS_PINYIN = False
    logger.warning("pypinyin 未安装：唤醒词退化为整字精确匹配，同音字（娅/亚 类）将配不上")


def _strip_tone(s: str) -> str:
    """去掉拼音里的声调符号（pypinyin 原表是带调的 líng）。"""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _homophones() -> dict[str, set[str]]:
    """音节 -> 同音字集合。懒构造、缓存挂在函数属性上（不污染模块全局）。"""
    cached = getattr(_homophones, "_cache", None)
    if cached is not None:
        return cached
    idx: dict[str, set[str]] = {}
    if _HAS_PINYIN:
        for cp, ps in _PINYIN_DICT_RAW.items():
            if cp > 0xFFFF:  # BMP 之外的同音字不进语法：解码无收益、JSON 更干净
                continue
            ch = chr(cp)
            for p in ps.split(","):
                idx.setdefault(_strip_tone(p), set()).add(ch)
    _homophones._cache = idx
    return idx


def _syllables(text: str) -> list[str]:
    """文本 → 音节序列（无声调）。非汉字逐字原样保留（英文唤醒词也能配）。"""
    if not _HAS_PINYIN:
        return list(text)
    return [p[0] for p in pinyin(
        text, style=Style.NORMAL,
        errors=lambda chars: [c for c in chars])]


class WakeWordDetector:
    def __init__(self, model, sample_rate: int, wake_words: list[str]) -> None:
        # 拷贝 + 去空：外部（配置对象）随后改动列表不影响已建好的检测器
        self._wake_words: list[str] = [w for w in list(wake_words or []) if w]
        chars = {c for w in self._wake_words for c in w}
        if _HAS_PINYIN:
            # 把每个唤醒字的同音字一起放进语法，解码器才吐得出真正的音
            table = _homophones()
            for c in list(chars):
                for syl in _syllables(c):
                    chars |= table.get(syl, set())
        # [unk] 是"非唤醒语音"的唯一出路：没有它，任何声音都被逼成唤醒字
        grammar = json.dumps(sorted(chars) + ["[unk]"], ensure_ascii=False)
        self._rec = KaldiRecognizer(model, sample_rate, grammar)
        self._rec.SetWords(True)

    def detect(self, data: bytes) -> Optional[str]:
        """返回命中的唤醒词，未命中返回 None。

        长词优先：用户说"你好达妮娅"时返回更具体的那个别名。
        """
        if self._rec.AcceptWaveform(data):
            # 约束解码是按字吐符号的，字间可能带空格；归一化后再比
            text = json.loads(self._rec.Result()).get("text", "").replace(" ", "")
            if not text or text == "[unk]":
                return None
            for w in sorted(self._wake_words, key=len, reverse=True):
                if _contains_syllables(text, w):
                    return w
        return None


def _contains_syllables(text: str, word: str) -> bool:
    """text 的音节序列里是否【连续】出现 word 的音节序列。

    拼音不可用时退化为整字子串（宁可少醒，不要按音节乱醒）。
    """
    if not _HAS_PINYIN:
        return word in text
    t, w = _syllables(text), _syllables(word)
    n = len(w)
    if n == 0:
        return False
    return any(t[i:i + n] == w for i in range(len(t) - n + 1))
