# -*- coding: utf-8 -*-
"""检验 F0 估计是否可信：①跨采样率稳定性 ②帧级 F0 分布是否单峰（排除倍频误判）"""
import os, sys, subprocess, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pick_clean_voice import decode, f0_ac, REF

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
D = r"G:\ai对话\11\data\native_audio_test\src_candidates"


def resample(path, sr, out):
    subprocess.run([FFMPEG, "-v", "quiet", "-y", "-i", path, "-ac", "1", "-ar", str(sr), out], capture_output=True)
    return out


def f0_frames(x, sr, fmin=80, fmax=520):
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
    return v


def report(tag, path):
    print("\n=== %s ===" % tag)
    for sr in (48000, 24000, 16000):
        tmp = os.path.join(tempfile.gettempdir(), "f0chk_%d.wav" % sr)
        p = path if os.path.splitext(path)[1] == ".wav" else path
        if sr != 48000:
            tmpw = tmp
            # 先统一解成 48k 临时 wav 再降
            base = os.path.join(tempfile.gettempdir(), "f0chk_base.wav")
            subprocess.run([FFMPEG, "-v", "quiet", "-y", "-i", path, "-ac", "1", "-ar", "48000", base], capture_output=True)
            resample(base, sr, tmpw)
            x = decode(tmpw, sr)
        else:
            base = os.path.join(tempfile.gettempdir(), "f0chk_base.wav")
            subprocess.run([FFMPEG, "-v", "quiet", "-y", "-i", path, "-ac", "1", "-ar", "48000", base], capture_output=True)
            x = decode(base, 48000)
        v = f0_frames(x, sr)
        if not v:
            print("  sr=%5d  无有声帧" % sr); continue
        a = np.array(v)
        hist, edges = np.histogram(a, bins=np.arange(80, 560, 40))
        top = np.argsort(-hist)[:3]
        print("  sr=%5d  帧数=%3d  中位=%.1f  均值=%.1f  主峰箱=%s" % (
            sr, len(a), np.median(a), a.mean(),
            ", ".join("%d-%dHz:%d" % (edges[t], edges[t + 1], hist[t]) for t in top)))


if __name__ == "__main__":
    report("达妮娅参考音 daniya_ref.wav", REF)
    report("V0827 r07 (16.0-17.1s, 本片最干净段)", os.path.join(D, "V0827_r07_16.0-17.1s_F0390.wav"))
    report("旧候选 01_Δ9 (2026.05.26 6s)", os.path.join(D, "01_Δ9_鸣潮2026.05.26_6s.wav"))
    report("SAPI 中性女声（完全不同的声源）", r"G:\ai对话\11\data\native_audio_test\INPUT_sapi_neutral.wav")
