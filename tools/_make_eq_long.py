# -*- coding: utf-8 -*-
"""长样本补偿对比：把同一通电话的多句话拼成一段，做「原始 vs 补偿」完整 A/B。

为什么要用长样本：短句只有 2~3 秒，耳朵还没适应就已经结束了，容易误判。
拼成 15~25 秒的连续语流，音色质地更容易听准。

用法（须 client_venv）：
    H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe tools/_make_eq_long.py
"""
import os
import sys
import glob
import wave

import numpy as np
import soundfile as sf

ROOT = r"G:\ai对话\11"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hf_restore as HF  # noqa: E402

REF = os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")
OUTDIR = os.path.join(ROOT, "data", "duplex_out")
PKG = os.path.join(ROOT, "data", "高频补偿试听_长样本_20260923")

SR = 24000
GAP_S = 0.35          # 句间静音
TARGET_RMS = 0.09     # 统一响度，避免句子忽大忽小
MIN_DUR = 1.2         # 太短的句子跳过
MAX_TOTAL = 26.0      # 总长上限（秒）


def load_wav(p):
    with wave.open(p, "rb") as w:
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a, sr


def main():
    os.makedirs(PKG, exist_ok=True)
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    rb, rf99 = HF.bandprof(ref)
    print("参考（达妮娅真源）3.4-6k/6-8k/8-12k = %s  f99=%.0f"
          % (np.round(rb, 1), rf99))

    subs = sorted(os.listdir(OUTDIR))
    latest = os.path.join(OUTDIR, subs[-1])
    ws = sorted(glob.glob(os.path.join(latest, "*.wav")))
    print("会话：%s（%d 句）\n" % (subs[-1], len(ws)))

    gap = np.zeros(int(SR * GAP_S), np.float64)
    parts, total, used = [], 0.0, []
    for p in ws:
        a, sr = load_wav(p)
        if sr != SR:
            continue
        d = len(a) / sr
        if d < MIN_DUR:
            continue
        if total + d > MAX_TOTAL:
            break
        rms = np.sqrt((a ** 2).mean())
        if rms > 1e-6:
            a = a * (TARGET_RMS / rms)
            a = a / max(1.0, np.abs(a).max() / 0.95)   # 防削顶
        parts.append(a)
        parts.append(gap)
        total += d + GAP_S
        used.append((os.path.basename(p), d))

    if not parts:
        print("没有可用句子")
        return 1
    x = np.concatenate(parts).astype(np.float32)
    print("拼接到 %.1f 秒，用了 %d 句：" % (len(x) / SR, len(used)))
    for n, d in used:
        print("   %-46s %.1fs" % (n[:44], d))
    print()

    b0, f0 = HF.bandprof(x)
    print("原始      3.4-6k/6-8k/8-12k = %s  f99=%.0f  8-12k平坦度=%.4f"
          % (np.round(b0, 1), f0, HF.flatness(x)))

    outs = []
    # 原始
    p0 = os.path.join(PKG, "00_原始_长样本.wav")
    sf.write(p0, x, SR, subtype="PCM_16")
    outs.append(p0)

    # EQ3（温和 70%）—— 上次推荐档
    g3, _ = HF.build_gain(ref, x, scale=0.7)
    y3 = HF.apply_gain(x, g3)
    b3, f3 = HF.bandprof(y3)
    p3 = os.path.join(PKG, "01_补偿后_EQ3温和70%_长样本.wav")
    sf.write(p3, y3, SR, subtype="PCM_16")
    outs.append(p3)
    print("EQ3温和70%%  %s  f99=%.0f  平坦度=%.4f"
          % (np.round(b3, 1), f3, HF.flatness(y3)))

    # EQ2（60% + SBR，高频跟辅音包络走）
    g2, _ = HF.build_gain(ref, x, scale=0.6)
    y2 = HF.apply_gain(x, g2, sbr=True, sbr_db=-6.0)
    b2, f2 = HF.bandprof(y2)
    p2 = os.path.join(PKG, "02_补偿后_EQ2_60%+SBR_长样本.wav")
    sf.write(p2, y2, SR, subtype="PCM_16")
    outs.append(p2)
    print("EQ2 60%%+SBR %s  f99=%.0f  平坦度=%.4f"
          % (np.round(b2, 1), f2, HF.flatness(y2)))

    print("\n输出目录：%s" % PKG)
    for o in outs:
        print("   %-46s %.1f KB" % (os.path.basename(o), os.path.getsize(o) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
