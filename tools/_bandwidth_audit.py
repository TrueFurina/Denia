# -*- coding: utf-8 -*-
"""判断微信语音消息的真实有效带宽（编解码器是否已经把高频砍掉了）。

对照三方：
  A wechat_ref.wav            微信语音消息（16k 容器）
  B daniya_dataset/ww.wav     48k 录屏降采样到 16k —— 0~8k 带内容是"真宽带"
  C s3tokenizer/assets/...    干净 16k 朗读语音（AISHELL 系）
若 A 的 4~8k 占比显著低于 B/C，说明微信编解码器已把高频砍掉，
则"换更好的处理"救不回来，必须换素材源。
"""
import os
import numpy as np
import soundfile as sf

CASES = [
    ("A 微信消息",   r"H:\daniya_voice_tools\wechat_ref.wav"),
    ("B 录屏降16k",  r"H:\daniya_voice_tools\daniya_dataset\ww.wav"),
    ("C 干净朗读16k", r"H:\daniya_voice_tools\client_venv\Lib\site-packages\s3tokenizer\assets\BAC009S0764W0121.wav"),
    ("D 录屏原48k",  r"G:\ai对话\11\data\native_audio_test\ref_candidates\C2_calm_natural_48k.wav"),
]
TARGET = 16000


def load16k(path):
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != TARGET:
        import subprocess
        FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
        r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                            "-f", "f32le", "-ar", str(TARGET), "-ac", "1", "-"],
                           input=x.astype("<f4").tobytes(), capture_output=True)
        x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    return x


def band_share_db(x, sr, flo, fhi, ref_lo=300, ref_hi=3400):
    """以 300~3400Hz（语音主能量带）为基准，返回该频带的相对 dB。"""
    # 只统计浊音帧，避免静音拉低
    w = 320
    n = len(x) // w
    e = np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1))
    thr = np.percentile(e, 60)
    keep = e > max(thr, 1e-5)
    seg = x[:n * w].reshape(n, w)[keep].flatten()
    if len(seg) < 4096:
        seg = x
    seg = seg * np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg)) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / sr)
    def band(a, b):
        m = (f >= a) & (f < b)
        return S[m].mean() if m.sum() else 0.0
    ref = band(ref_lo, ref_hi)
    if ref <= 0:
        return -99.0
    return 10 * np.log10(max(band(flo, fhi), 1e-20) / ref)


def rolloff(x, sr, pct, ref_lo=300, ref_hi=3400):
    w = 320
    n = len(x) // w
    e = np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1))
    keep = e > max(np.percentile(e, 60), 1e-5)
    seg = x[:n * w].reshape(n, w)[keep].flatten()
    if len(seg) < 4096:
        seg = x
    seg = seg * np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg)) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / sr)
    c = np.cumsum(S)
    if c[-1] <= 0:
        return 0
    return float(f[np.searchsorted(c, c[-1] * pct / 100.0)])


print("=" * 104)
print("有效带宽体检（浊音帧，以 300~3400Hz 为零 dB 基准）")
print("=" * 104)
print(f"{'样本':<16}{'0.3-3.4k':>10}{'3.4-6k':>10}{'6-8k':>9}{'8-12k':>9}{'roll95%':>10}{'roll99%':>10}")
print("-" * 104)
for tag, p in CASES:
    if not os.path.exists(p):
        print(f"{tag:<16}  MISSING {p}")
        continue
    x = load16k(p)
    if p.endswith("48k.wav"):
        pass
    # 3.4-6k 等用 16k 采样也能算到 8k；8-12k 只有原 48k 源有效（降采样后恒 -inf）
    r95 = rolloff(x, 16000, 95)
    r99 = rolloff(x, 16000, 99)
    print(f"{tag:<16}{0.0:>10.1f}{band_share_db(x, 16000, 3400, 6000):>10.1f}"
          f"{band_share_db(x, 16000, 6000, 8000):>9.1f}"
          f"{band_share_db(x, 16000, 8000, 12000):>9.1f}{r95:>10.0f}{r99:>10.0f}")

print()
print("判读：")
print("  · 若 A(微信) 的 6-8k 比 B/C 低 6dB 以上 ⇒ 微信编解码器已砍掉高频，源素材上限如此")
print("  · roll99% 是 99% 能量所在的截止频率；干净 16k 语音通常在 6~8kHz")
print("  · 8-12k 一列：16k 素材 Nyquist=8k，必为 -inf，属正常，不代表缺陷")
print("=" * 104)

# ---- 逐帧高频能量时间线：看微信是否只在某些片段被砍 ----
print()
print("=" * 104)
print("微信源逐 0.5s 的 6-8k 相对能量（看高频是否全程缺失还是一段一段）")
print("=" * 104)
x = load16k(CASES[0][1])
sr = 16000
hop = sr // 2
vals = []
for i in range(0, len(x) - hop, hop):
    s = x[i:i + hop] * np.hanning(hop)
    S = np.abs(np.fft.rfft(s)) ** 2
    f = np.fft.rfftfreq(hop, 1 / sr)
    low = S[(f >= 300) & (f < 3400)].mean()
    hi = S[(f >= 6000) & (f < 8000)].mean()
    if low > 1e-12:
        vals.append(10 * np.log10(max(hi, 1e-20) / low))
    else:
        vals.append(-99)
for i in range(0, len(vals), 16):
    chunk = vals[i:i + 16]
    print(f"  {i*0.5:5.1f}s  " + " ".join(f"{v:6.1f}" for v in chunk))
print()
print(f"  中位 6-8k 相对能量 = {np.median([v for v in vals if v > -90]):.1f} dB")
