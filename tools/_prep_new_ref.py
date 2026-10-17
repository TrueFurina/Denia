# -*- coding: utf-8 -*-
"""准备新参考音：去首尾静音 + 增益归一化 → assets/voices/daniya_ref_0813_24k.wav

为什么必须归一化：新素材 RMS 仅 0.033 / 峰值 0.215（原 ref 是 0.136 / 0.776）。
电平过低会让声码器条件偏弱、克隆不稳定。

⚠️ 不覆盖原 daniya_ref.wav（保留原文件，config 改指向新文件即可，随时可回退）。
"""
import os
import sys
import wave
import shutil
from datetime import datetime

import numpy as np

ROOT = r"G:\ai对话\11"
SRC = os.path.join(ROOT, "data", "_newref_probe", "newref_24k.wav")
DST = os.path.join(ROOT, "assets", "voices", "daniya_ref_0813_24k.wav")

TARGET_PEAK = 0.89
TARGET_RMS = 0.10
SIL_DB = -45.0      # 低于此视为静音
PAD_S = 0.15        # 首尾保留的静音


def load(p):
    with wave.open(p, "rb") as w:
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a, sr


def save(p, a, sr):
    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def trim(a, sr):
    win = int(sr * 0.02)
    if len(a) < win * 2:
        return a
    n = len(a) // win
    e = np.array([np.sqrt((a[i * win:(i + 1) * win] ** 2).mean()) for i in range(n)])
    thr = 10 ** (SIL_DB / 20.0)
    voiced = np.where(e > thr)[0]
    if len(voiced) == 0:
        return a
    s = max(0, (voiced[0] - 1) * win)
    t = min(len(a), (voiced[-1] + 2) * win)
    pad = int(sr * PAD_S)
    s = max(0, s - pad)
    t = min(len(a), t + pad)
    return a[s:t]


def stats(tag, a, sr):
    rms = float(np.sqrt((a ** 2).mean()))
    pk = float(np.abs(a).max())
    head = float(np.abs(a[:int(sr * 0.2)]).max())
    print("%-12s 时长%6.2fs  RMS=%.4f  峰值=%.4f  首0.2s峰值=%.4f"
          % (tag, len(a) / sr, rms, pk, head))
    return rms, pk


def main():
    if not os.path.isfile(SRC):
        print("缺源文件 %s" % SRC)
        return 2
    a, sr = load(SRC)
    print("① 原始")
    r0, p0 = stats("转码后", a, sr)

    print("\n② 去首尾静音")
    b = trim(a, sr)
    stats("去静音后", b, sr)

    print("\n③ 增益归一化（先按 RMS，再限峰值）")
    rms = float(np.sqrt((b ** 2).mean()))
    if rms > 1e-6:
        b = b * (TARGET_RMS / rms)
    pk = float(np.abs(b).max())
    if pk > TARGET_PEAK:
        b = b * (TARGET_PEAK / pk)
    r1, p1 = stats("归一化后", b, sr)

    print("\n④ 落盘")
    if os.path.isfile(DST):
        bak = DST + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(DST, bak)
        print("   已存在的目标先备份：%s" % os.path.basename(bak))
    save(DST, b, sr)
    print("   ✅ %s  (%.1f KB)" % (DST, os.path.getsize(DST) / 1024))
    print("\n   RMS %.4f → %.4f ；峰值 %.4f → %.4f" % (r0, r1, p0, p1))
    print("   原 daniya_ref.wav 未改动（保留，便于回退）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
