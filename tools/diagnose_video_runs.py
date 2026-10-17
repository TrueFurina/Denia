# -*- coding: utf-8 -*-
"""诊断：全片有声音区逐段列出 F0(AC/HPS) + 与参考音的 LTAS 相关；并自检指标可信度。"""
import os, sys, subprocess
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pick_clean_voice import decode, frame_feat, f0_ac, ltas, cos, write_wav, REF

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"


def f0_hps(x, sr, fmin=80, fmax=520):
    n = 4096; hop = n // 2
    win = np.hanning(n).astype(np.float32)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    lo = int(fmin / (sr / n)); hi = int(fmax / (sr / n))
    v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012: continue
        sp = np.abs(np.fft.rfft(seg * win))
        hps = sp.copy()
        for h in (2, 3, 4, 5):
            d = sp[::h]; hps[:len(d)] *= d
        b = hps[lo:hi]
        if len(b): v.append(freqs[lo + int(np.argmax(b))])
    return float(np.median(v)) if v else 0.0


def main():
    path = sys.argv[1]
    x = decode(path)
    sr = 48000
    ref = decode(REF, 16000)
    ref_l = ltas(ref, 16000)
    ref_f0a, ref_f0h = f0_ac(ref, 16000), f0_hps(ref, 16000)
    print("参考 daniya_ref: F0_AC=%.1f  F0_HPS=%.1f" % (ref_f0a, ref_f0h))

    # ---- 指标自检 ----
    print("\n[指标自检] 用参考音自己切两半 vs 换一个完全不同的声音")
    h = len(ref) // 2
    print("  同人前后半 LTAS余弦 = %.4f  (应接近1)" % cos(ltas(ref[:h], 16000), ltas(ref[h:], 16000)))
    sapi = r"G:\ai对话\11\data\native_audio_test\INPUT_sapi_neutral.wav"
    if os.path.exists(sapi):
        s = decode(sapi, 16000)
        print("  SAPI中文女声 vs 达妮娅 LTAS余弦 = %.4f  (应明显更低)" % cos(ltas(s, 16000), ref_l))

    # ---- 全片有声音区 ----
    feats = frame_feat(x, sr)
    runs = []
    for d in feats:
        if d["rms"] > 0.020 and d["sp"] > 0.55 and d["lo"] < 0.20:
            if runs and d["t"] - runs[-1][1] < 0.10:
                runs[-1][1] = d["t"]
            else:
                runs.append([d["t"], d["t"]])
    runs = [r for r in runs if r[1] - r[0] >= 0.20]
    print("\n[全片有声音区] 共 %d 段" % len(runs))
    print(" idx  起     止    长    RMS     F0_AC  F0_HPS  语音带   <120   8-12k  LTAS余弦")
    for i, (a, b) in enumerate(runs):
        a2 = max(0, a - 0.08); b2 = min(len(x) / sr, b + 0.10)
        seg = x[int(a2 * sr):int(b2 * sr)]
        ff = [d for d in feats if a - 1e-6 <= d["t"] <= b + 1e-6]
        sp = np.mean([d["sp"] for d in ff]) if ff else 0
        lo = np.mean([d["lo"] for d in ff]) if ff else 0
        h8 = np.mean([d["hf8"] for d in ff]) if ff else 0
        f0a, f0h = f0_ac(seg, sr), f0_hps(seg, sr)
        c = cos(ltas(seg, sr), ref_l)
        print("%4d %5.2f %6.2f %5.2f  %.4f  %6.1f  %6.1f  %5.1f%%  %5.1f%%  %5.2f%%  %7.4f" %
              (i, a2, b2, b2 - a2, np.sqrt((seg ** 2).mean()), f0a, f0h, sp * 100, lo * 100, h8 * 100, c))


if __name__ == "__main__":
    main()
