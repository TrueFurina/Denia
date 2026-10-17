# -*- coding: utf-8 -*-
"""客观判定'达妮娅但扭曲'：对比云端 TTS 产物与本地双工原生标本的频谱特征。

判据：本地双工原生音频被砍高频(f95≈1.2k)，8k+ 能量占比极低；云端清晰达妮娅是全频段(8k+ 占比高)。
若 chat_audio 里混入低频残缺片段 -> 云端失败回退到本地原生音频。
"""
import os, glob, datetime
import numpy as np, soundfile as sf

def analyze(path):
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x[:, 0]
    n = len(x)
    dur = n / sr
    peak = float(np.max(np.abs(x))) if n else 0.0
    rms = float(np.sqrt(np.mean(x**2))) if n else 0.0
    # 分段 FFT 求 LTAS（长时平均频谱）
    win = np.hanning(2048)
    hop = 1024
    bands = [(0,1000),(1000,2000),(2000,4000),(4000,8000),(8000,12000),(12000,min(sr//2,20000))]
    acc = np.zeros(len(bands))
    cnt = 0
    for s in range(0, max(1,n-len(win)), hop):
        seg = x[s:s+len(win)] * win
        if np.max(np.abs(seg)) < 1e-4:
            continue
        sp = np.abs(np.fft.rfft(seg))
        fr = np.fft.rfftfreq(len(win), 1.0/sr)
        cnt += 1
        for i,(lo,hi) in enumerate(bands):
            mask = (fr>=lo)&(fr<hi)
            acc[i] += np.sum(sp[mask]**2)
    total = acc.sum()
    if total <= 0:
        return sr, dur, peak, rms, [0.0]*len(bands), 0.0
    frac = acc/total
    hi = float(frac[4]+frac[5])  # 8k+ 占比
    return sr, dur, peak, rms, [float(f) for f in frac], hi

def tag(hi):
    # 高频占比判据
    if hi >= 0.15: return "全频(云端清晰)"
    if hi >= 0.08: return "中频"
    return "低频残缺(本地双工漏出?)"

rows = []
# 1) chat_audio 最近
ca = sorted(glob.glob("G:/ai对话/11/src/ui/web/assets/chat_audio/*.wav"), key=os.path.getmtime)[-8:]
print("==== chat_audio（云端 TTS 实际出声产物，最近 8 个）====")
for p in ca:
    sr,dur,peak,rms,frac,hi = analyze(p)
    clip = "⚠削波" if peak>0.99 else ""
    rows.append((os.path.basename(p), hi, peak))
    print("  %s dur=%.2fs sr=%d peak=%.2f rms=%.3f 8k+占比=%.1f%% %s %s"
          % (os.path.basename(p)[-22:], dur, sr, peak, rms, hi*100, tag(hi), clip))
print("  频段能量占比 [0-1k,1-2k,2-4k,4-8k,8-12k,12k+]:")
for p in ca:
    sr,dur,peak,rms,frac,hi = analyze(p)
    print("    %s  %s" % (os.path.basename(p)[-22:], " ".join("%.1f%%"%(f*100) for f in frac)))

# 2) duplex_out 标本（本地双工原生）
print("\n==== duplex_out（本地双工原生标本）====")
dsubs = sorted(glob.glob("G:/ai对话/11/data/duplex_out/*/"), key=os.path.getmtime)
if dsubs:
    ddir = dsubs[-1]
    fs = sorted(glob.glob(os.path.join(ddir,"*.wav")), key=os.path.getmtime)
    print("  子目录", os.path.basename(ddir.rstrip('/')), "共", len(fs), "段")
    for p in fs[:5]:
        sr,dur,peak,rms,frac,hi = analyze(p)
        print("  %s dur=%.2fs sr=%d peak=%.2f 8k+占比=%.1f%% %s"
              % (os.path.basename(p), dur, sr, peak, hi*100, tag(hi)))
    # 该目录高频占比分布
    his = [analyze(p)[5] for p in fs]
    print("  该目录 8k+占比 min=%.1f%% median=%.1f%% max=%.1f%%"
          % (min(his)*100, sorted(his)[len(his)//2]*100, max(his)*100))
