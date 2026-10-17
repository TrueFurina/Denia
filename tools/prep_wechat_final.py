# -*- coding: utf-8 -*-
"""【达妮娅音色克隆 · 步骤 1/6｜微信纯源版】生成最终参考音。

素材裁决（2026-09-21 实测，证据在 _wechat_inventory.json / _bandwidth_audit.py）：
  微信源 vs 录屏源的客观对比
                        3.4-6k    6-8k     roll99%
    微信消息             -8.4dB  -12.3dB    6386Hz
    录屏原48k           -15.1dB  -19.7dB    1825Hz
  ⇒ 微信源不但内容最纯（无战斗BGM、无杂人声），频谱还比录屏更亮。

  🔴 关键机制：extract_prompt_bundle.py 的 mel 滤波器 fmax=8000，
     语音 token 与说话人 embedding 又都从 16kHz 版本算
     ⇒ 烘焙链路在设计上只"看得见" 0~8000Hz。
     录屏的 48kHz 宽频对本地克隆零增益，只带来 BGM 污染风险。

本步骤只修两个客观缺陷（素材本身已由 Qwen 本地/百炼云端克隆验证合格，
故不做任何"美化"式干预）：
  1) 裁掉首尾全零静音   （原 0.45s + 0.60s 为精确数字零，属拼接静音，
                        会被声码器当成条件的一部分，稀释音色）
  2) 电平归一到 -20 dBFS RMS（原 -28.8dBFS，偏低 8.8dB；
                        本链路 prompt_mel 取 log(幅度) 绝对值、不做归一化，
                        低电平会让条件贴近 log 截断地板）

产出：
  ref_wechat_final/daniya_ref_wechat_16k.wav  ← 送 extract（原生 16k，零重采样）
  ref_wechat_final/daniya_ref_wechat_24k.wav  ← 覆盖 bundle/default_ref_audio.wav
"""
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

SRC = r"H:\daniya_voice_tools\wechat_ref.wav"
OUT_DIR = r"G:\ai对话\11\data\native_audio_test\ref_wechat_final"
OUT16 = os.path.join(OUT_DIR, "daniya_ref_wechat_16k.wav")
OUT24 = os.path.join(OUT_DIR, "daniya_ref_wechat_24k.wav")

EDGE_THR = 0.005          # 首尾静音阈值（绝对值；原文件静音为精确 0，很安全）
MARGIN_S = 0.030          # 首尾各留 30ms 余量，不吃掉弱起音
TARGET_DBFS = -20.0       # 归一化目标
PEAK_CEIL = 0.95


def f0_median(x, sr, fmin=70, fmax=500, frame=1024, hop=256):
    x = x - x.mean()
    out = []
    for i in range(max(0, (len(x) - frame) // hop)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < 0.01:
            continue
        ac = np.correlate(s, s, "full")[frame - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lo, hi = int(sr / fmax), min(int(sr / fmin), len(ac) - 1)
        if hi <= lo:
            continue
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] < 0.3:
            continue
        out.append(sr / k)
    return float(np.median(out)) if out else 0.0


def report(path, label):
    i = sf.info(path)
    x, _ = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    sr = i.samplerate
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = X.sum() + 1e-12
    w = max(1, int(sr * 0.01))
    m = n // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > EDGE_THR)[0]
    lead = idx[0] * 0.01 if len(idx) else i.duration
    tail = (m - 1 - idx[-1]) * 0.01 if len(idx) else i.duration
    voiced = float((e > max(e.max() * 0.05, EDGE_THR)).mean() * 100)
    rms = float(np.sqrt(np.mean(x ** 2)))
    print(f"  {label:24s} {i.duration:6.2f}s sr={sr:5d} rms={rms:.4f}({20*np.log10(max(rms,1e-9)):6.1f}dBFS) "
          f"pk={np.abs(x).max():.3f} 浊音={voiced:5.1f}% >4k={X[f>4000].sum()/tot*100:5.2f}% "
          f"F0={f0_median(x, sr):6.1f}Hz 首静={lead:.3f}s 尾静={tail:.3f}s")


def main():
    print("=" * 118)
    print("【步骤 1/6】微信纯源 → 最终参考音")
    print("=" * 118)
    if not os.path.exists(SRC):
        print("!! 源缺失:", SRC); sys.exit(1)

    x, sr = sf.read(SRC, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    print("源:", SRC)
    report(SRC, "源(微信原文件)")

    # ---- 1. 裁首尾静音 ----
    w = max(1, int(sr * 0.01))
    m = len(x) // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > EDGE_THR)[0]
    if len(idx) == 0:
        print("!! 整段低于阈值，放弃裁剪"); y = x
    else:
        a = max(0, int((idx[0] * 0.01 - MARGIN_S) * sr))
        b = min(len(x), int(((idx[-1] + 1) * 0.01 + MARGIN_S) * sr))
        y = x[a:b]
        print(f"\n[1] 裁静音: {len(x)/sr:.3f}s -> {len(y)/sr:.3f}s "
              f"(去前 {a/sr:.3f}s / 去后 {(len(x)-b)/sr:.3f}s)")

    # ---- 2. 电平归一 ----
    rms0 = float(np.sqrt(np.mean(y ** 2)))
    g = (10 ** (TARGET_DBFS / 20)) / max(rms0, 1e-12)
    y2 = y * g
    pk = float(np.abs(y2).max())
    if pk > PEAK_CEIL:
        y2 *= PEAK_CEIL / pk
        print(f"[2] 归一化: 增益 {20*np.log10(g):+.1f}dB 后峰值 {pk:.3f} 超限，回退至 {PEAK_CEIL}")
    else:
        print(f"[2] 归一化: 增益 {20*np.log10(g):+.1f}dB "
              f"({20*np.log10(max(rms0,1e-9)):.1f} -> {TARGET_DBFS:.1f} dBFS)，峰值 {pk:.3f}")
    y = y2.astype(np.float32)

    # ---- 3. 硬性校验 ----
    dur = len(y) / sr
    pk = float(np.abs(y).max())
    errs = []
    if not (6.0 <= dur <= 10.0):
        errs.append(f"时长 {dur:.2f}s 不在 6~10s")
    if pk >= 0.999:
        errs.append(f"peak {pk:.3f} 削波")
    if errs:
        print("!! 校验未通过: " + "; ".join(errs)); sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    sf.write(OUT16, y, sr, subtype="PCM_16")
    print(f"\n写出 16k: {OUT16}")

    # ---- 4. 重采样到 24k（给 bundle）----
    from scipy.signal import resample_poly
    from math import gcd
    g2 = gcd(24000, sr)
    y24 = resample_poly(y, 24000 // g2, sr // g2).astype(np.float32)
    sf.write(OUT24, y24, 24000, subtype="PCM_16")
    print(f"写出 24k: {OUT24}")

    print()
    print("=" * 118)
    print("最终校验")
    print("=" * 118)
    report(SRC, "原文件(对照)")
    report(OUT16, "最终 16k (送extract)")
    report(OUT24, "最终 24k (给bundle)")
    print()
    print(f"F0 保持: 原 {f0_median(x, sr):.1f}Hz -> 16k {f0_median(y, sr):.1f}Hz "
          f"-> 24k {f0_median(y24, 24000):.1f}Hz   (变调会毁音色，此处须一致)")
    print()
    print("PASS 参考音就绪。")
    print(f"  EXTRACT_REF = {OUT16}")
    print(f"  BUNDLE_WAV  = {OUT24}")


if __name__ == "__main__":
    main()
