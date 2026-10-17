# -*- coding: utf-8 -*-
"""高频恢复（清晰度补偿）。

背景：引擎的 prompt 条件 fmax=8000（已实锤，相关 1.00000），产出 8-12k 只有模型先验。
      实测产出 8-12k 比真源低 14dB ⇒ 听感"闷/糊"。
本工具用两种手段在**输出侧**夺回高频：
  EQ1_static : 静态谱匹配 —— 用真源的长期平均谱(LTAS)对产出做零相位 EQ 掩码
  EQ2_sbr    : 静态补偿(打 60% 折扣) + SBR 谐波折叠（把 4-8k 搬一份到 8-12k，-6dB）
               ——SBR 让高频跟着辅音/气声的时间包络走，而不是一片静态噪声

只做后处理，不动模型；可随时开关。
"""
import os
import sys
import numpy as np
import soundfile as sf

N = 1024
H = 256
W = np.hanning(N).astype(np.float32)


def stft(x):
    pad = N // 2
    xp = np.concatenate([np.zeros(pad, np.float32), x.astype(np.float32),
                         np.zeros(pad + N, np.float32)])
    nf = 1 + (len(xp) - N) // H
    S = np.empty((nf, N // 2 + 1), np.complex64)
    for i in range(nf):
        S[i] = np.fft.rfft(xp[i * H:i * H + N] * W)
    return S, pad, len(x)


def istft(S, pad, length):
    nf = S.shape[0]
    xp = np.zeros((nf - 1) * H + N, np.float32)
    ws = np.zeros_like(xp)
    for i in range(nf):
        seg = np.fft.irfft(S[i], N).astype(np.float32) * W
        xp[i * H:i * H + N] += seg
        ws[i * H:i * H + N] += W * W
    x = xp / np.maximum(ws, 1e-8)
    return x[pad:pad + length]


def ltas(x, label=""):
    """发声帧的长期平均幅度谱（1kHz 归一）。"""
    S, _, _ = stft(x)
    mag = np.abs(S)
    band = mag[:, 3:400]
    e = band.mean(axis=1)
    thr = np.percentile(e, 55)
    sel = e > thr
    if sel.sum() < 8:
        sel = np.ones(len(e), bool)
    L = mag[sel].mean(axis=0)
    f = np.fft.rfftfreq(N, 1 / 24000)
    i1k = int(np.searchsorted(f, 1000))
    L = L / (L[max(0, i1k - 3):i1k + 4].mean() + 1e-12)
    return L, f, sel.sum()


def smooth(v, k=9):
    ker = np.hanning(k)
    ker = ker / ker.sum()
    return np.convolve(v, ker, mode="same")


def flatness(x, lo=8000, hi=12000):
    S, _, _ = stft(x)
    P = (np.abs(S) ** 2)
    f = np.fft.rfftfreq(N, 1 / 24000)
    m = (f >= lo) & (f < hi)
    if m.sum() < 3:
        return float("nan")
    p = P[:, m].mean(axis=0) + 1e-20
    return float(np.exp(np.log(p).mean()) / p.mean())


def bandprof(x, sr=24000, ref_path=None):
    n = min(len(x), sr * 20)
    s = x[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(s)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    r = S[(f >= 300) & (f < 3400)].sum() + 1e-12
    out = []
    for lo, hi in [(3400, 6000), (6000, 8000), (8000, 12000)]:
        out.append(10 * np.log10((S[(f >= lo) & (f < hi)].sum() + 1e-12) / r))
    cs = np.cumsum(S)
    return out, float(f[np.searchsorted(cs, 0.99 * (cs[-1] + 1e-12))])


def build_gain(ref, out, cap_hi=16.0, cap_lo=-6.0, scale=1.0):
    Lr, f, _ = ltas(ref)
    Lo, _, _ = ltas(out)
    g = 20 * np.log10((Lr + 1e-9) / (Lo + 1e-9))
    g = smooth(np.clip(g, cap_lo, cap_hi), 15)
    # 11.5kHz 以上不再抬（双方都是噪声底，抬了只有嘶声）
    f_hi = int(np.searchsorted(f, 11500))
    g[f_hi:] = np.minimum(g[f_hi:], 0.0)
    g = np.clip(g * scale, cap_lo, cap_hi)
    # 直流/极低频不动
    g[:3] = 0.0
    return g, f


def apply_gain(x, g, sbr=False, sbr_db=-6.0):
    S, pad, length = stft(x)
    G = 10 ** (g / 20.0)
    S2 = S * G[None, :]
    if sbr:
        f = np.fft.rfftfreq(N, 1 / 24000)
        src = (f >= 4000) & (f < 7700)
        dst_lo = int(np.searchsorted(f, 8000))
        idx_src = np.where(src)[0]
        idx_dst = idx_src + (dst_lo - int(np.searchsorted(f, 4000)))
        ok = idx_dst < S2.shape[1]
        amp = 10 ** (sbr_db / 20.0)
        S2[:, idx_dst[ok]] += S[:, idx_src[ok]] * amp
    y = istft(S2, pad, length)
    pk = np.abs(y).max()
    if pk > 0.995:
        y = y / pk * 0.99
    return y.astype(np.float32)


def main():
    REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
    OUTROOT = r"G:\ai对话\11\data\native_audio_test\final_sample"
    HF = os.path.join(OUTROOT, "hf")
    os.makedirs(HF, exist_ok=True)
    CAND = [("A_FIXED", os.path.join(OUTROOT, "A_FIXED.wav")),
            ("v2a", os.path.join(OUTROOT, "A_FIXED_v2a.wav")),
            ("v2b", os.path.join(OUTROOT, "A_FIXED_v2b.wav")),
            ("B_NATIVE", os.path.join(OUTROOT, "B_NATIVE.wav"))]
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    rb, rf99 = bandprof(ref)
    print("=" * 112)
    print("目标参考音（真源 8s，24k）  3.4-6k/6-8k/8-12k = %s  f99=%.0f  8-12k 平坦度=%.4f"
          % (np.round(rb, 1), rf99, flatness(ref)))
    print("=" * 112)
    print()
    print("%-14s%9s%26s%26s%10s" % ("样本", "时长", "补偿前 3.4-6k/6-8k/8-12k", "补偿后 3.4-6k/6-8k/8-12k", "8-12k平坦度"))
    print("-" * 112)
    print("%-14s%9s%26s%26s%10s" % ("", "", "".join("%9s" % s for s in ("", "", "")), "", "前→后"))
    made = []
    for tag, p in CAND:
        if not os.path.exists(p):
            print("%-14s MISSING" % tag)
            continue
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != 24000:
            print("%-14s sr=%d 跳过" % (tag, sr)); continue
        b0, f0 = bandprof(x)
        flat0 = flatness(x)
        g, f = build_gain(ref, x, scale=1.0)
        y1 = apply_gain(x, g)
        b1, f1 = bandprof(y1)
        g2, _ = build_gain(ref, x, scale=0.6)
        y2 = apply_gain(x, g2, sbr=True, sbr_db=-6.0)
        b2, f2 = bandprof(y2)
        g3, _ = build_gain(ref, x, scale=0.7)
        y3 = apply_gain(x, g3)
        b3, f3 = bandprof(y3)
        flat1 = flatness(y1)
        flat2 = flatness(y2)
        r = np.sqrt((x ** 2).mean())
        print("%-14s%9.2f  [%6.1f %6.1f %6.1f]  EQ1 [%6.1f %6.1f %6.1f]  %.4f→%.4f"
              % (tag, len(x) / sr, b0[0], b0[1], b0[2], b1[0], b1[1], b1[2], flat0, flat1))
        print("%-14s%9s  %26s  EQ2 [%6.1f %6.1f %6.1f]  →%.4f（含SBR）"
              % ("", "", "", b2[0], b2[1], b2[2], flat2))
        print("%-14s%9s  %26s  EQ3 [%6.1f %6.1f %6.1f]  （温和档 70%%）"
              % ("", "", "", b3[0], b3[1], b3[2]))
        # 增量增益曲线（用于诊断/固化）
        fq = [500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000, 11000]
        gg = [float(g[np.searchsorted(f, q)]) for q in fq]
        print("      EQ 曲线(dB) " + " ".join("%s:%.1f" % (q // 1000 if q >= 1000 else q, v)
                                              for q, v in zip(fq, gg)))
        o1 = os.path.join(HF, "EQ1_%s.wav" % tag)
        o2 = os.path.join(HF, "EQ2_%s.wav" % tag)
        o3 = os.path.join(HF, "EQ3_%s.wav" % tag)
        sf.write(o1, y1, 24000, subtype="PCM_16")
        sf.write(o2, y2, 24000, subtype="PCM_16")
        sf.write(o3, y3, 24000, subtype="PCM_16")
        made += [o1, o2, o3]
        print()
    print("=" * 112)
    print("产出")
    print("=" * 112)
    for o in made:
        print("   %s  (%d B)" % (o, os.path.getsize(o)))
    print()
    print("判读：")
    print("  · 8-12k 平坦度→1 表示该频段是噪声；抬升会把嘶声一起放大（EQ2 的 SBR 就是为此设计）")
    print("  · EQ 曲线里 >8k 的抬升量 = 引擎丢掉、需要'补'的量；它是模型先验，不是本人音色")


if __name__ == "__main__":
    main()
