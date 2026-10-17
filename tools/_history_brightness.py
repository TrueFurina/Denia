# -*- coding: utf-8 -*-
"""横向体检：H:\\daniya_voice_tools 下所有历史生成样本的频谱亮度，判断"能否靠换参考音改善"。"""
import os, glob, subprocess
import numpy as np

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
BANDS = [(0, 120), (120, 300), (300, 1000), (1000, 3400), (3400, 6000), (6000, 8000),
         (8000, 12000), (12000, 24000)]


def decode(path, sr=48000):
    r = subprocess.run([FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def rep(x, sr=48000):
    n = 16384
    if len(x) < n: n = 1 << int(np.log2(max(256, len(x))))
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1); c = 0
    for i in range(0, max(1, len(x) - n), n // 2):
        s = x[i:i + n]
        if len(s) < n: break
        psd += np.abs(np.fft.rfft(s * win)) ** 2; c += 1
    psd /= max(1, c)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    tot = psd.sum() or 1e-20
    rat = [float(psd[(f >= a) & (f < b)].sum() / tot * 100) for a, b in BANDS]
    thr = psd.max() * 1e-4
    idx = np.where(psd > thr)[0]
    cut = f[idx[-1]] if len(idx) else 0.0
    return rat, cut


files = []
for pat in ["verify_*.wav", "_ab*.wav", "_cmp*.wav", "_cosy_*.wav", "_shell_*.wav",
            "qwen3_*.wav", "daniya_*.wav", "t1.wav", "t2.wav", "chunk_*.wav",
            "_diag_*.wav", "wechat_ref*.wav"]:
    files += glob.glob(os.path.join(r"H:\daniya_voice_tools", pat))
files = sorted(set(files))

print("=" * 132)
print("%-34s %7s %8s %8s %7s %7s %7s  %s" %
      ("文件", "时长", "120-300", "300-1k", "1-3.4k", "3.4-6k", "6-8k", "-40dB"))
print("=" * 132)
rows = []
for p in files:
    try:
        x = decode(p)
        if len(x) < 4096: continue
        rat, cut = rep(x)
        rows.append((os.path.basename(p), len(x) / 48000, rat, cut))
    except Exception as e:
        print("  %-34s ERROR %s" % (os.path.basename(p), e))

# 按 3.4-8k 亮度排序
rows.sort(key=lambda r: -(r[2][4] + r[2][5]))
for name, dur, rat, cut in rows:
    print("%-34s %6.2fs %7.1f%% %7.1f%% %6.2f%% %6.2f%% %6.2f%%  %6.0fHz" %
          (name, dur, rat[1], rat[2], rat[3], rat[4], rat[5], cut))

print()
print("参考基准:")
for lab, p in [("旧参考音 wechat_ref.wav(16k)", r"H:\daniya_voice_tools\wechat_ref.wav"),
               ("干净窗 t=11.5 (真声 48k)", None),
               ("daniya_clean_10s.wav", r"H:\daniya_voice_tools\daniya_clean_10s.wav")]:
    if p and os.path.exists(p):
        x = decode(p)
        rat, cut = rep(x)
        print("  %-32s 120-300=%5.1f%% 300-1k=%5.1f%% 1-3.4k=%5.2f%% 3.4-6k=%5.2f%% 6-8k=%5.2f%% -40dB=%5.0fHz" %
              (lab, rat[1], rat[2], rat[3], rat[4], rat[5], cut))
