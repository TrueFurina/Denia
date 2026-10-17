# -*- coding: utf-8 -*-
"""
ASR 模型 A/B：同一批中文语音，zipformer(transducer, 旧) vs paraformer(新)。

    python tools/ab_asr_models.py

判定看两点：① 谁更少"重复字/乱码" ② 谁的字面更像人话。
"""
from __future__ import annotations

import glob
import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.voice.sherpa_recognizer import SherpaRecognizer  # noqa: E402

WAV_DIR = os.path.join(ROOT, "data", "voice_switch_plus_20260923")


def load_wav_16k(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        n_out = max(1, int(a.size * 16000 / sr))
        idx = np.arange(n_out, dtype=np.float64) * (a.size - 1) / max(n_out - 1, 1)
        lo = idx.astype(np.int64)
        hi = np.minimum(lo + 1, a.size - 1)
        frac = (idx - lo).astype(np.float32)
        a = a[lo] * (1 - frac) + a[hi] * frac
    return a


def transcribe(rec: "SherpaRecognizer", a: np.ndarray, chunk_ms: int = 100) -> str:
    """模拟实时喂流：100ms 一块；喂完再 finalize 兜住尾部。"""
    n = int(16000 * chunk_ms / 1000)
    out = []
    for i in range(0, a.size, n):
        seg = a[i:i + n]
        pcm = np.clip(seg * 32767.0, -32768, 32767).astype(np.int16)
        t = rec.feed(pcm.tobytes())
        if t:
            out.append(t)
    tail = rec.finalize()
    if tail:
        out.append(tail)
    return " ".join(out).strip()


def noise_score(t: str) -> int:
    """粗糙的"病态度"打分：连续重复 / 极短 / 纯英文碎片。"""
    import re
    s = 0
    if not t:
        return 100
    if len(t) <= 1:
        s += 3
    s += len(re.findall(r"(.)\1{1,}", t))          # 叠字
    s += 2 * len(re.findall(r"(.{2})\1{1,}", t))   # 叠词
    if re.fullmatch(r"[A-Za-z\s]{1,4}", t.strip()):
        s += 3                                      # 纯英文碎片
    return s


def main() -> int:
    files = sorted(glob.glob(os.path.join(WAV_DIR, "plus_S*.wav")))
    if not files:
        print("没找到测试音频:", WAV_DIR)
        return 1
    print("=" * 74)
    print(f"ASR 模型 A/B —— {len(files)} 段真实中文语音（达妮娅 TTS 落盘音频）")
    print("=" * 74)

    models = {}
    for name, prefer in (("zipformer(旧/transducer)", "zipformer"),
                         ("paraformer(新/非自回归)", "paraformer")):
        t0 = time.time()
        try:
            r = SherpaRecognizer(num_threads=2, min_trailing_silence=0.6,
                                 short_silence=0.4, prefer=prefer)
            models[name] = r
            print(f"加载 {name}: {getattr(r,'model_type','?')} "
                  f"({time.time()-t0:.1f}s)  dir={os.path.basename(getattr(r,'model_dir',''))}")
        except Exception as e:
            print(f"加载 {name} 失败: {type(e).__name__} {e}")
    print()

    if len(models) < 2:
        print("至少一个模型没加载起来，无法 A/B")
        return 1

    total = {k: 0 for k in models}
    for f in files:
        a = load_wav_16k(f)
        dur = a.size / 16000
        print("-" * 74)
        print(f"{os.path.basename(f)}  ({dur:.1f}s)")
        for name, rec in models.items():
            rec.reset()
            t0 = time.time()
            txt = transcribe(rec, a)
            cost = time.time() - t0
            sc = noise_score(txt)
            total[name] += sc
            rtf = f"{cost/dur:.2f}x" if dur > 0 else "-"
            print(f"  {name:<26} {rtf:>7}  病态度{sc:>3}  | {txt}")

    print("=" * 74)
    print("病态度总分（越低越干净）:")
    for k, v in sorted(total.items(), key=lambda x: x[1]):
        print(f"  {k:<26} {v}")
    best = min(total, key=total.get)
    print(f"\n→ 更干净: {best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
