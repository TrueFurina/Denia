# -*- coding: utf-8 -*-
"""回答"8kHz 到底丢了什么"：把真源与克隆都限带到 8k 后逐倍频程比对，
   再把 8-12k 单列，从而量化"失真发生在哪一段"。
"""
import os, subprocess, shutil
import numpy as np
import soundfile as sf

FF = shutil.which("ffmpeg") or r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"

FILES = [
    ("真源·达妮娅本人(24k)", SRC),
    ("克隆 APP_A", os.path.join(FS, "APP_A.wav")),
    ("克隆 + 停顿规整", os.path.join(FS, "APP_A_smooth.wav")),
    ("克隆 + 高频补偿 EQ1", os.path.join(FS, "APP_A_smooth_EQ1.wav")),
    ("原生签名音 ref_minicpm", r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"),
]

BANDS = [(80, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 6000), (6000, 8000)]
BANDS_HF = [(8000, 9000), (9000, 10000), (10000, 11000), (11000, 12000)]


def rs(x, a, b):
    if a == b:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(a), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", str(b), "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def load(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def ltas(x, sr, nfft=2048, hop=256, thr=0.004):
    acc = np.zeros(nfft // 2 + 1); n = 0
    for i in range(0, max(1, len(x) - nfft), hop):
        s = x[i:i + nfft] * np.hanning(nfft)
        if np.sqrt(np.mean(s ** 2)) < thr:
            continue
        acc += np.abs(np.fft.rfft(s)); n += 1
    if n == 0:
        return None, None
    return np.fft.rfftfreq(nfft, 1 / sr), 20 * np.log10(acc / n + 1e-9)


def bdb(f, L, lo, hi):
    m = (f >= lo) & (f < hi)
    return float(L[m].mean()) if m.any() else -99.0


def tilt(f, L, lo=300, hi=8000):
    m = (f >= lo) & (f < hi)
    lo10 = np.log2(f[m]); y = L[m]
    A = np.vstack([lo10, np.ones_like(lo10)]).T
    k = np.linalg.lstsq(A, y, rcond=None)[0][0]
    return float(k)


def f99(x, sr):
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    ff = np.fft.rfftfreq(len(x), 1 / sr)
    c = np.cumsum(S) / (S.sum() + 1e-12)
    return float(ff[np.searchsorted(c, 0.99)])


print("=" * 118)
print("A) 限带到 8kHz 后（统一重采样到 16k，Nyquist=8k）的逐带电平，相对各自 300-3400Hz")
print("=" * 118)
hdr = "%-24s" % "样本" + "".join("%9s" % ("%g-%gk" % (a / 1000, b / 1000)) for a, b in BANDS)
print(hdr); print("-" * 118)
prof = {}
for lab, p in FILES:
    if not os.path.exists(p):
        print("%-24s MISSING" % lab); continue
    x, sr = load(p)
    x16 = rs(x, sr, 16000)
    f, L = ltas(x16, 16000)
    ref = bdb(f, L, 300, 3400)
    prof[lab] = np.array([bdb(f, L, a, b) - ref for a, b in BANDS])
    print("%-24s" % lab + "".join("%9.1f" % v for v in prof[lab]))

print()
print("=" * 118)
print("B) 同一限带条件下：克隆相对真源的逐带偏差（dB），以及 0-8k 总体偏差")
print("=" * 118)
base = "真源·达妮娅本人(24k)"
if base in prof:
    print("%-24s" % "样本" + "".join("%9s" % ("%g-%gk" % (a / 1000, b / 1000)) for a, b in BANDS) + "%11s" % "RMS 偏差")
    for lab in prof:
        if lab == base:
            continue
        d = prof[lab] - prof[base]
        print("%-24s" % lab + "".join("%9.1f" % v for v in d) + "%11.1f" % float(np.sqrt((d ** 2).mean())))

print()
print("=" * 118)
print("C) 全频带（24k）剖面：8kHz 以上单独考察")
print("=" * 118)
print("%-24s%9s%9s%9s%9s%9s%10s%9s" % ("样本", "6-8k", "8-9k", "9-10k", "10-11k", "11-12k", "8k+占比", "f99(Hz)"))
print("-" * 118)
for lab, p in FILES:
    if not os.path.exists(p):
        continue
    x, sr = load(p)
    x24 = rs(x, sr, 24000)
    f, L = ltas(x24, 24000)
    ref = bdb(f, L, 300, 3400)
    v = [bdb(f, L, a, b) - ref for a, b in [(6000, 8000)] + BANDS_HF]
    S = np.abs(np.fft.rfft(x24 * np.hanning(len(x24)))) ** 2
    ff = np.fft.rfftfreq(len(x24), 1 / 24000)
    hf = float(S[ff >= 8000].sum() / (S.sum() + 1e-12) * 100)
    print("%-24s" % lab + "".join("%9.1f" % q for q in v) + "%9.2f%%%10.0f" % (hf, f99(x24, 24000)))

print()
print("=" * 118)
print("D) 谱倾斜（300Hz→8kHz，dB/倍频程；真人通常 -6~-10，越大越亮）")
print("=" * 118)
for lab, p in FILES:
    if not os.path.exists(p):
        continue
    x, sr = load(p)
    for tag, xx, ss in (("全频带24k", rs(x, sr, 24000), 24000),):
        f, L = ltas(xx, ss)
        print("%-24s %s  tilt = %+.2f dB/oct" % (lab, tag, tilt(f, L)))
