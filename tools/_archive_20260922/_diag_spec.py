# -*- coding: utf-8 -*-
"""频谱诊断：生成的音频 vs 旧参考音 vs 48k 真声，看「糊」出在哪个频段。"""
import os, sys, subprocess
import numpy as np

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
NAMES = ["0-120", "120-300", "300-1k", "1k-3.4k", "3.4k-6k", "6k-8k", "8k-12k", "12k-24k"]


def decode(path, sr=48000):
    cmd = [FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if not r.stdout:
        raise RuntimeError("decode failed: %s %s" % (path, r.stderr.decode('utf-8', 'replace')[:200]))
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def spec(x, sr, n=16384):
    if len(x) < n:
        n = 1 << int(np.log2(max(256, len(x))))
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1)
    c = 0
    for i in range(0, max(1, len(x) - n), n // 2):
        seg = x[i:i + n]
        if len(seg) < n:
            break
        psd += np.abs(np.fft.rfft(seg * win)) ** 2
        c += 1
    psd /= max(1, c)
    return psd, np.fft.rfftfreq(n, 1.0 / sr)


def report(tag, path, sr=48000):
    x = decode(path, sr)
    if len(x) < 512:
        print("  %-40s <too short>" % tag); return
    psd, f = spec(x, sr)
    tot = psd.sum() or 1e-20
    # 累计能量求 -40dB / 99.5% 截止
    cum = np.cumsum(psd) / tot
    c40 = f[np.searchsorted(cum, 0.9999)] if cum[-1] > 0.9999 else f[-1]
    # 真正的 -40dB 逐点：找最后一个 psd > max*1e-4 的频率
    thr = psd.max() * 1e-4
    idx = np.where(psd > thr)[0]
    cut40 = f[idx[-1]] if len(idx) else 0.0
    # 平滑包络后再找（更稳）
    k = 61
    sm = np.convolve(psd, np.ones(k) / k, mode="same")
    idx2 = np.where(sm > sm.max() * 1e-4)[0]
    cut40s = f[idx2[-1]] if len(idx2) else 0.0
    bands = [(0, 120), (120, 300), (300, 1000), (1000, 3400), (3400, 6000),
             (6000, 8000), (8000, 12000), (12000, 24000)]
    rat = []
    for lo, hi in bands:
        m = (f >= lo) & (f < hi)
        rat.append(float(psd[m].sum() / tot * 100) if m.any() else 0.0)
    print("  %-40s  dur=%5.2fs  -40dB截止=%6.0fHz  平滑截止=%6.0fHz  F0=%6.1f" %
          (tag, len(x) / sr, cut40, cut40s, f0(x, sr)))
    print("       频带占比: " + "  ".join("%s=%.2f%%" % (NAMES[i], rat[i]) for i in range(len(bands)) if rat[i] > 0.001))
    return rat


def f0(x, sr, fmin=80, fmax=520):
    n = int(sr * 0.04); hop = int(sr * 0.02); v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo: continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35:
            v.append(sr / k)
    return float(np.median(v)) if v else 0.0


print("=" * 118)
print("项目 / 说明".ljust(42) + "指标")
print("=" * 118)
report("★ 旧参考音 daniya_ref.wav (16k 微信)", r"G:\ai对话\11\assets\voices\daniya_ref.wav")
report("★ 48k 真声 V0827_全片27.0s_48k.wav", r"G:\ai对话\11\data\native_audio_test\src_candidates\V0827_全片27.0s_48k.wav")
print()
print("--- 之前用旧 cache 生成的音频（24kHz）---")
report("  原生音频 daniya_native.wav", r"G:\ai对话\11\data\native_audio_test\daniya_native.wav")
for w in ["wav_3.wav", "wav_6.wav", "wav_10.wav"]:
    p = os.path.join(r"G:\ai对话\11\data\native_audio_test", w)
    if os.path.exists(p):
        report("  " + w, p)
print()
print("--- 变调诊断样本（看保共振峰是否有效）---")
for w in ["_AB1_参考音升调至390_保共振峰.wav", "_AB1_录屏降调至239_保共振峰.wav"]:
    p = os.path.join(r"G:\ai对话\11\data\native_audio_test\src_candidates", w)
    if os.path.exists(p):
        report("  " + w, p)
