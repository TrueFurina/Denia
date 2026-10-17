# -*- coding: utf-8 -*-
"""量化各参考音素材的基频(F0)，厘清"原厂目标音高"与本次自然音高的差异。用 venv 运行。"""
import os
import numpy as np
import soundfile as sf
import librosa

FILES = [
    ("原厂bundle(08-28/09-13快照)", r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio.bak_20260913\default_ref_audio.wav"),
    ("旧参考 daniya_ref(现网)", r"G:\ai对话\11\assets\voices\daniya_ref.wav"),
    ("旧参考(上上次 bundle)", r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio.bak_prev_20260921_090130\default_ref_audio.wav"),
    ("FINAL2 48k", r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"),
    ("FINAL2 16k", r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_16k.wav"),
    ("视频原始(wuthering 27s)", r"G:\视频录制\Wuthering Waves\Wuthering Waves 2026.08.27 - 06.59.23.04.mp4"),
]

for name, p in FILES:
    if not os.path.exists(p):
        print("%-30s MISSING %s" % (name, p))
        continue
    try:
        if p.lower().endswith(".mp4"):
            y, sr = librosa.load(p, sr=16000, mono=True)
        else:
            y, sr = sf.read(p, dtype="float32", always_2d=False)
            if getattr(y, "ndim", 1) > 1:
                y = y.mean(axis=1)
            if sr != 16000:
                y = librosa.resample(y, orig_sr=sr, target_sr=16000)
                sr = 16000
        # 只取有声片段估 F0
        f0 = librosa.yin(y, fmin=80, fmax=400, sr=sr, frame_length=2048)
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        n = min(len(f0), len(rms))
        m = rms[:n] > np.percentile(rms[:n], 60)
        f0v = f0[:n][m]
        f0v = f0v[np.isfinite(f0v)]
        print("%-30s dur=%5.2fs sr=%d  F0 中位=%6.1fHz  均值=%6.1f  p25=%6.1f p75=%6.1f  (有声帧 %d)"
              % (name, len(y) / sr, sr, np.median(f0v), f0v.mean(),
                 np.percentile(f0v, 25), np.percentile(f0v, 75), len(f0v)))
    except Exception as e:
        print("%-30s ERR %r" % (name, e))
