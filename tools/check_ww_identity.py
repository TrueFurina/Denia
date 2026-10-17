# -*- coding: utf-8 -*-
"""核验：daniya_dataset\ww.wav (16kHz, 26.97s) 是否就是那份 48kHz 录屏的音轨？
（两份都是 26.97s，时长完全相同，很可疑）"""
import os, sys, time
import numpy as np
sys.path.insert(0, r"G:\ai对话\11\tools")
import _audit_assets as A

WW = r"H:\daniya_voice_tools\daniya_dataset\ww.wav"
VID = r"G:\视频录制\Wuthering Waves\Wuthering Waves 2026.08.27 - 06.59.23.04.mp4"
DS = r"H:\daniya_voice_tools\daniya_dataset"

print("=== 文件时间戳（判断先后顺序）===")
for p in [WW, VID] + [os.path.join(DS, f) for f in ["2026-08-13 01-25-38.wav", "2026-08-13 01-43-36.wav", "8月13日.wav"]]:
    if os.path.exists(p):
        st = os.stat(p)
        print("  %-42s 改=%s  建=%s  %.1fKB" %
              (os.path.basename(p), time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
               time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_ctime)), st.st_size / 1024))

print("\n=== 内容比对：ww.wav vs 录屏音轨（都降到 16k，算包络相关 + 逐段 RMS 相关）===")
a = A.decode(WW, 16000)
b = A.decode(VID, 16000)
n = min(len(a), len(b))
a, b = a[:n], b[:n]
print("  长度: ww=%.2fs  录屏=%.2fs  取min=%.2fs" % (len(a) / 16000, len(b) / 16000, n / 16000))
# 帧级 RMS 序列互相关（找最佳偏移，检验是不是同一段内容）
def rms_seq(x, sr=16000, win=1600, hop=800):
    m = len(x) // hop - 1
    return np.array([np.sqrt((x[i * hop:i * hop + win] ** 2).mean()) for i in range(max(0, m))])
ra, rb = rms_seq(a), rms_seq(b)
m = min(len(ra), len(rb))
print("  RMS 帧数: ww=%d 录屏=%d" % (len(ra), len(rb)))
r0 = float(np.corrcoef(ra[:m], rb[:m])[0, 1])
print("  零偏移 RMS 相关 = %.4f  (接近 1 => 同一段内容)" % r0)
best = max(((float(np.corrcoef(ra[:m], np.roll(rb, k)[:m])[0, 1]), k) for k in range(-6, 7)))
print("  最佳偏移 %d 帧 相关 = %.4f" % (best[1], best[0]))
print("  峰值/RMS: ww peak=%.3f rms=%.4f | 录屏 peak=%.3f rms=%.4f" %
      (np.abs(a).max(), np.sqrt((a ** 2).mean()), np.abs(b).max(), np.sqrt((b ** 2).mean())))
