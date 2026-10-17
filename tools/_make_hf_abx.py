# -*- coding: utf-8 -*-
"""高频归属听测包：「8k 以上该补还是该砍」

背景：参考音进模型前被降到 16k，声码器条件带宽只有 8k ⇒ 输出里的 8~12kHz
      完全由声码器解码器先验生成（原生签名音自身 >8k 为 -62.5dB，本就没东西）。
      EQ 只能把这段的「电平与形状」拉向真源（差 0.76dB / 相关 0.979），
      但改不了「纹理来源」。于是只剩一个可耳听回答的问题：

        这段引擎自产的 >8k，到底是让声音更接近达妮娅，还是更像 MiniCPM？

本包给出三段等响度对照，唯一变量就是 >8kHz：
  A 克隆·未补偿      >8k 弱（-15.3dB，原生先验被压着）
  B 克隆·EQ1 补偿    >8k 电平/形状对齐真源（-4.2dB）
  C 克隆·限带 8k     >8k 彻底删除（0dB，模型先验无处可藏）

判读：
  · 若 C 听起来最「达妮娅」 ⇒ >8k 是污染源，应停止补高频，改做限带。
  · 若 B 最好            ⇒ EQ 方向正确，应把它做成常驻后处理。
  · 若 A 最好            ⇒ 两者都不必，保持原样。
"""
import os
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

SR = 24000
P = r"G:\ai对话\11"
FS = os.path.join(P, "data", "native_audio_test", "final_sample")
OUT = os.path.join(FS, "聆听_v5_高频归属")
TARGET_DBFS = -20.0


def load24(p):
    x, sr = sf.read(p, always_2d=True)
    x = x.mean(axis=1).astype(np.float64)
    if sr != SR:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g)
    return x


def voiced_rms(x):
    n, h = int(0.04 * SR), int(0.02 * SR)
    fr = [x[i:i + n] for i in range(0, len(x) - n, h)]
    r = np.array([np.sqrt((f ** 2).mean() + 1e-20) for f in fr])
    thr = np.percentile(r, 90) * 10 ** (-25 / 20.0)
    sel = r[r > thr]
    return float(np.sqrt((sel ** 2).mean())) if len(sel) else float(np.sqrt((x ** 2).mean()))


def eq_loud(x, target_db=TARGET_DBFS):
    cur = voiced_rms(x)
    if cur <= 1e-9:
        return x
    g = 10 ** ((target_db - 20 * np.log10(cur)) / 20.0)
    y = x * g
    peak = np.abs(y).max()
    if peak > 0.99:
        y *= 0.99 / peak
    return y


def save(path, x):
    sf.write(path, np.clip(x, -1.0, 1.0), SR, subtype="PCM_16")


def lowpass(x, fc=8000, order=12):
    sos = butter(order, fc / (SR / 2.0), btype="low", output="sos")
    return sosfiltfilt(sos, x)


def main():
    os.makedirs(OUT, exist_ok=True)
    ref = os.path.join(P, "data", "native_audio_test", "ref_v2", "mp3_8s_24k.wav")
    base = os.path.join(FS, "APP_A_smooth.wav")
    eq1 = os.path.join(FS, "APP_A_smooth_EQ1.wav")
    for p in (ref, base, eq1):
        if not os.path.exists(p):
            raise SystemExit("缺文件: %s" % p)

    xr = load24(ref)
    xb = load24(base)
    xe = load24(eq1)
    xc = lowpass(xb)

    items = [
        ("00_真源_达妮娅本人_24k全频带.wav", xr),
        ("01_克隆_未补偿_8k以上弱.wav", xb),
        ("02_克隆_EQ1_8k以上对齐真源.wav", xe),
        ("03_克隆_限带8k_彻底删除模型先验.wav", xc),
    ]
    report = []
    for name, x in items:
        y = eq_loud(x)
        save(os.path.join(OUT, name), y)
        # 高频带电平（归一化口径与 _hf_whodunit 一致）
        n, h = int(0.04 * SR), int(0.02 * SR)
        F = np.array([y[i:i + n] * np.hanning(n) for i in range(0, len(y) - n, h)])
        r = np.sqrt((F ** 2).mean(axis=1) + 1e-20)
        sel = F[r > np.percentile(r, 90) * 10 ** (-25 / 20.0)]
        sp = 10 * np.log10((np.abs(np.fft.rfft(sel, axis=1)) ** 2).mean(axis=0) + 1e-20)
        fq = np.fft.rfftfreq(n, 1.0 / SR)
        m0 = (fq >= 200) & (fq <= 4000)
        norm = sp - sp[m0].mean()
        m1 = (fq >= 8200) & (fq <= 11800)
        save(os.path.join(OUT, name), y)
        report.append((name, len(y) / SR, float(np.median(norm[m1]))))

    # 顺序连听：真源 → 未补偿 → EQ1 → 限带
    gap = np.zeros(int(0.7 * SR))
    seq = []
    for _, x in items:
        seq.append(eq_loud(x))
        seq.append(gap)
    sf.write(os.path.join(OUT, "04_顺序_真人_未补偿_EQ1_限带.wav"),
             np.clip(np.concatenate(seq), -1, 1), SR, subtype="PCM_16")

    print("=" * 78)
    print("高频归属听测包 → %s" % OUT)
    print("=" * 78)
    for name, dur, hf in report:
        print("  %-46s %5.2fs   8-12kHz 中位 %6.1f dB" % (name, dur, hf))
    print("  %-46s %5.2fs" % ("04_顺序_真人_未补偿_EQ1_限带.wav",
                               sum(d for _, d, _ in report) + 0.7 * 4))
    print()
    print("  等响度口径：有声帧 RMS = %.0f dBFS" % TARGET_DBFS)
    print("  唯一变量：>8kHz 的处理方式（无 / 弱 / 补偿到真源 / 全删）")


if __name__ == "__main__":
    main()
