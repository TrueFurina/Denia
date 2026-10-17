# -*- coding: utf-8 -*-
"""合成参考音对比试听：1ding=旧参考(daniya_ref)  2dings=FINAL2自然287Hz  3dings=FINAL3降调241Hz。
用 venv 运行。"""
import os, numpy as np, soundfile as sf, librosa

SR = 48000
OLD = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
F2 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"
F3 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL3_daniya_ref_48k.wav"
OUT = r"G:\ai对话\11\data\native_audio_test\ref_build\_AB_参考音对比_1旧_2自然287_3降调241.wav"


def load48(p):
    y, sr = sf.read(p, dtype="float32", always_2d=False)
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    peak = np.abs(y).max() + 1e-9
    return (y / peak * 0.89).astype(np.float32)


def ding(n):
    seg = []
    for _ in range(n):
        t = np.arange(int(0.11 * SR)) / SR
        d = (np.sin(2 * np.pi * 880 * t) * np.hanning(len(t)) * 0.35).astype(np.float32)
        seg += [d, np.zeros(int(0.10 * SR), np.float32)]
    return np.concatenate(seg) if seg else np.zeros(0, np.float32)


gap = np.zeros(int(0.55 * SR), np.float32)
parts = [gap]
for i, (p, label) in enumerate([(OLD, "旧参考(16k/微信源/243.6Hz)"),
                                (F2, "FINAL2 自然音高(48k原生/287.5Hz)"),
                                (F3, "FINAL3 降调对齐(48k/保共振峰/241.2Hz)")], start=1):
    y = load48(p)
    parts += [ding(i), y, gap]
    print("seg%d %-34s %.2fs" % (i, label, len(y) / SR))

out = np.concatenate(parts)
sf.write(OUT, out, SR, subtype="PCM_16")
print("SAVED", OUT, "%.2fs" % (len(out) / SR), os.path.getsize(OUT), "B")
