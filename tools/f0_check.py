# -*- coding: utf-8 -*-
"""F0 估计器校验：HPS(谐波积谱) vs 自相关。用 daniya_ref.wav 做基准。"""
import subprocess
import numpy as np

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"


def decode(path, sr):
    r = subprocess.run([FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def f0_ac(x, sr, fmin=80, fmax=520):
    n = int(sr * 0.04); hop = int(sr * 0.02); v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012: continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo: continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35: v.append(sr / k)
    return float(np.median(v)) if v else 0.0


def f0_hps(x, sr, fmin=80, fmax=520):
    n = 4096
    hop = n // 2
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
            d = sp[::h]
            hps[:len(d)] *= d
        seg_band = hps[lo:hi]
        if not len(seg_band): continue
        k = lo + int(np.argmax(seg_band))
        v.append(freqs[k])
    return float(np.median(v)) if v else 0.0


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else r"G:\ai对话\11\assets\voices\daniya_ref.wav"
    for sr in (16000, 48000):
        try:
            x = decode(p, sr)
        except Exception as e:
            print(e); continue
        print("sr=%d  时长=%.2fs  AC=%.1f Hz  HPS=%.1f Hz" % (sr, len(x) / sr, f0_ac(x, sr), f0_hps(x, sr)))
