"""Sherpa-ONNX 流式中文识别封装。

【2026-09-24 换模型】此前用 2023 款 zipformer-transducer，中文口语有严重
"重复字"病征（实测 47 句里 44.7% 被打结：'今天今天好累累啊'、
'喂喂喂喂喂你听到听得见得见吗'、'自我介绍介绍'）—— 自回归 transducer 的通病。
现首选阿里 DAMO **Paraformer 流式**（非自回归，天然不复读）；
模型缺失或加载失败时自动回退旧 transducer，不会让 ASR 整体失效。

用法：
    rec = SherpaRecognizer()                     # 自动选最好的可用模型
    rec = SherpaRecognizer(prefer="zipformer")   # 强制回退旧模型（回滚用）
    while True:
        text = rec.feed(audio_int16_bytes)   # 一句话结束返回文本，否则空串
"""
from __future__ import annotations

import glob as _glob
import logging
import os
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = "models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
MODEL_DIR_PARAFORMER = "models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"


def _repo_root() -> str:
    """src/voice/sherpa_recognizer.py -> 仓库根。

    【2026-09-24】模型目录此前是相对 cwd 解析的，进程 cwd 不在仓库根时整段读不到
    （与 §2ct「人格被提示词删掉」同源）。这里按仓库根解析，读不到再退回原行为。
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve(d: str) -> str:
    if not d:
        return ""
    if os.path.isabs(d):
        return d
    cand = os.path.join(_repo_root(), d)
    return cand if os.path.isdir(cand) else d


def _detect_type(d: str) -> str:
    """按目录内容判断模型类型：'transducer' / 'paraformer' / ''（不可用）。"""
    if not d or not os.path.isdir(d):
        return ""
    fs = set(os.listdir(d))
    if any(f.startswith("joiner") and f.endswith(".onnx") for f in fs):
        return "transducer"
    if "encoder.int8.onnx" in fs or "encoder.onnx" in fs:
        return "paraformer"
    return ""


class SherpaRecognizer:
    """流式在线识别器：喂 int16 PCM，一句话结束返回文本。"""

    def __init__(self, model_dir: str = "", num_threads: int = 2,
                 min_trailing_silence: float = 2.4,
                 short_silence: float = 1.2,
                 min_utterance_length: float = 20.0,
                 prefer: str = "paraformer",
                 sil_threshold: float = 0.006,
                 max_utt_s: float = 5.0) -> None:
        import numpy as np  # noqa: F401  确保依赖可用
        import sherpa_onnx  # noqa: F401

        # 【2026-09-24 修 Paraformer 碎片化】sherpa 内置 endpoint 对 Paraformer 流式会
        # 过早触发（真机切出"你的名""叫什"）。Paraformer 改用「真实静音时长」自管分段：
        #   sil_threshold = 判定为静音的 RMS 门限（语音 ~0.02~0.3，房间底噪 ~0.001~0.005）
        #   max_utt_s     = 单句最长缓冲秒数兜底，防「永不Flush」把整句憋到通话结束
        self._sil_threshold = float(sil_threshold)
        self._max_utt_s = float(max_utt_s)
        self._trailing_sil = 0.0
        self._utt_start_ts = 0.0

        order = [MODEL_DIR_PARAFORMER, MODEL_DIR]
        if str(prefer).strip().lower().startswith("zip"):
            order = [MODEL_DIR, MODEL_DIR_PARAFORMER]
        if model_dir:
            order = [model_dir] + [m for m in order if m != model_dir]

        last_err = None
        for d in order:
            rd = _resolve(d)
            kind = _detect_type(rd)
            if not kind:
                logger.debug("Sherpa-ONNX 跳过（目录不可用）: %s", rd)
                continue
            try:
                self._build(rd, kind, num_threads, min_trailing_silence,
                            short_silence, min_utterance_length)
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("Sherpa-ONNX 模型加载失败，回退下一个: %s (%s)", rd, e)
                continue
            self._model_dir = rd
            self.model_type = kind
            # transducer 会复读 → 需要激进去重；paraformer 不需要
            #（激进去重会误伤正确文本里的叠字，如"好好""累累"）
            self.aggressive_dedup = (kind == "transducer")
            self._stream = self._recognizer.create_stream()
            logger.info("Sherpa-ONNX 识别器就绪: %s [%s]", rd, kind)
            return
        raise RuntimeError(f"没有可用的 Sherpa-ONNX 模型（试过 {order}），最后错误: {last_err}")

    # ---------- 构建 ----------
    def _build(self, d: str, kind: str, num_threads: int,
               t1: float, t2: float, t3: float) -> None:
        import sherpa_onnx

        common = dict(
            num_threads=num_threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            # 【2026-09-24】Paraformer 流式用自管分段（见 feed），sherpa 内置 endpoint
            # 对它会过早触发 → 关掉；transducer 仍用 sherpa endpoint（老行为不变）。
            enable_endpoint_detection=(kind == "transducer"),
            rule1_min_trailing_silence=t1,
            rule2_min_trailing_silence=t2,
            rule3_min_utterance_length=t3,
        )
        tokens = os.path.join(d, "tokens.txt")
        if kind == "paraformer":
            enc = os.path.join(d, "encoder.int8.onnx")
            dec = os.path.join(d, "decoder.int8.onnx")
            if not os.path.exists(enc):
                enc = os.path.join(d, "encoder.onnx")
            if not os.path.exists(dec):
                dec = os.path.join(d, "decoder.onnx")
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
                tokens=tokens, encoder=enc, decoder=dec, **common)
        else:
            def _pick(pat):
                g = (_glob.glob(os.path.join(d, pat + ".int8.onnx"))
                     or _glob.glob(os.path.join(d, pat + ".onnx")))
                return g[0] if g else ""

            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=tokens,
                encoder=_pick("encoder-epoch-*"),
                decoder=_pick("decoder-epoch-*"),
                joiner=_pick("joiner-epoch-*"),
                **common)

    # ---------- 属性 ----------
    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def model_dir(self) -> str:
        return getattr(self, "_model_dir", "")

    # ---------- 识别 ----------
    def feed(self, pcm_int16: bytes) -> str:
        """喂一段 int16 PCM 音频。一句话识别完成返回文本（去掉两端空白），否则返回空串。

        Paraformer：sherpa 内置 endpoint 会过早触发 → 改用「真实静音时长」自管分段
        （见 _feed_paraformer）。transducer：沿用 sherpa 内置 endpoint（老行为不变）。
        """
        wav = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        if wav.size == 0:
            return ""
        self._stream.accept_waveform(sample_rate=self.sample_rate, waveform=wav)
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        if self.model_type == "paraformer":
            return self._feed_paraformer(wav)
        # transducer：沿用 sherpa 内置 endpoint
        if self._recognizer.is_endpoint(self._stream):
            result = self._recognizer.get_result_all(self._stream)
            self._recognizer.reset(self._stream)
            self._trailing_sil = 0.0
            self._utt_start_ts = 0.0
            text = getattr(result, "text", "") or ""
            return text.strip()
        return ""

    # ---------- Paraformer 自管分段 ----------
    @staticmethod
    def _para_should_flush(trailing_sil: float, has_text: bool,
                           age: float, min_trailing: float,
                           max_utt: float) -> bool:
        """是否该把当前累积文本作为一句吐出。纯函数，便于单测。"""
        if not has_text:
            return False
        if trailing_sil >= min_trailing:
            return True
        if age >= max_utt:
            return True
        return False

    def _feed_paraformer(self, wav: "np.ndarray") -> str:
        """Paraformer 流式：靠真实静音时长判句尾，禁用 sherpa 内置 endpoint。

        根因：sherpa 内置 endpoint 对 Paraformer 会在短语停顿处误触发，把一句话切成
        "你的名""叫什" 这种 2~3 字碎片。这里改为——累计尾部静音时长 ≥ 阈值（默认 0.6s）
        就吐句；再加 max_utt_s 兜底，防止连续说话永不 flush 把整句憋到通话结束。
        """
        dur = wav.size / self.sample_rate
        rms = float(np.sqrt(np.mean(wav * wav))) if wav.size else 0.0
        if rms < self._sil_threshold:
            self._trailing_sil += dur
        else:
            self._trailing_sil = 0.0
            if self._utt_start_ts <= 0.0:
                self._utt_start_ts = time.time()
        res = self._recognizer.get_result_all(self._stream)
        text = (getattr(res, "text", "") or "").strip()
        if not text:
            return ""
        age = (time.time() - self._utt_start_ts) if self._utt_start_ts > 0 else 0.0
        if self._para_should_flush(self._trailing_sil, True, age,
                                   self.min_trailing_silence, self._max_utt_s):
            self._recognizer.reset(self._stream)
            self._trailing_sil = 0.0
            self._utt_start_ts = 0.0
            return text
        return ""

    def reset(self) -> None:
        """丢弃当前未完成的识别状态（如 AI 开始说话时）。"""
        self._recognizer.reset(self._stream)
        self._trailing_sil = 0.0
        self._utt_start_ts = 0.0

    def finalize(self) -> str:
        """强制取出当前已识别但未触达端点的文本（一句话录音结束时调用），并重置流。"""
        text = ""
        try:
            res = self._recognizer.get_result_all(self._stream)
            text = (getattr(res, "text", "") or "").strip()
        except Exception:  # noqa: BLE001
            logger.debug("Sherpa finalize 取结果异常", exc_info=True)
        try:
            self._recognizer.reset(self._stream)
        except Exception:  # noqa: BLE001
            pass
        self._trailing_sil = 0.0
        self._utt_start_ts = 0.0
        return text
