# -*- coding: utf-8 -*-
"""诊断验收音频：是不是"噢哦啊哦"式非语言发声。

判据思路：
  1) 时长与文本是否匹配（中文 ~4-5 字/秒）——过长 => 拖长/重复
  2) 能量包络的分段数（speech burst 数）—— 文本 6-10 字应 1-3 段，若几十段 => 一顿一顿
  3) 过零率 / 谱平坦度 / 谱质心 —— 元音式"噢哦啊"谱质心低、平坦度高
  4) 与目标参考音的共振峰分布对比
"""
import os
import sys
import numpy as np
import soundfile as sf

try:
    import librosa
except Exception:
    librosa = None

PKG = r"G:\ai对话\11\data\验收试听包_24k"
ACC = r"G:\ai对话\11\data\acceptance_24k"


def load(p):
    x, sr = sf.read(p, always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x.astype(np.float64), sr


def frames(x, n=1024, hop=256, win=True):
    if len(x) < n:
        return np.zeros((0, n))
    idx = np.arange(0, len(x) - n, hop)
    F = np.stack([x[i:i + n] for i in idx])
    if win:
        F = F * np.hanning(n)[None, :]
    return F


def rms_env(x, sr, win_ms=20, hop_ms=10):
    w = int(sr * win_ms / 1000)
    h = int(sr * hop_ms / 1000)
    if len(x) < w:
        return np.array([0.0])
    idx = np.arange(0, len(x) - w, max(h, 1))
    return np.array([np.sqrt(np.mean(x[i:i + w] ** 2) + 1e-12) for i in idx])


def count_bursts(env, sr, hop_ms=10, thresh_ratio=0.10, min_gap_ms=120, min_len_ms=60):
    """数"有声段"个数：能量超过峰值 10% 视为有声，间隔 >120ms 视为新段。"""
    if len(env) == 0:
        return 0, 0.0
    peak = env.max()
    if peak <= 0:
        return 0, 0.0
    act = env > peak * thresh_ratio
    segs = []
    i = 0
    while i < len(act):
        if act[i]:
            j = i
            while j < len(act) and act[j]:
                j += 1
            segs.append((i, j))
            i = j
        else:
            i += 1
    # 合并间隔过短的
    merged = []
    for s in segs:
        if merged and (s[0] - merged[-1][1]) * hop_ms < min_gap_ms:
            merged[-1] = (merged[-1][0], s[1])
        else:
            merged.append(list(s))
    merged = [m for m in merged if (m[1] - m[0]) * hop_ms >= min_len_ms]
    voiced = sum((m[1] - m[0]) for m in merged) * hop_ms / 1000.0
    return len(merged), voiced


def zcr(x):
    return float(np.mean(np.abs(np.diff(np.sign(x))) > 0))


def spec_stats(x, sr):
    F = frames(x)
    if len(F) == 0:
        return 0.0, 0.0, 0.0
    P = np.abs(np.fft.rfft(F, axis=1)) ** 2
    freqs = np.fft.rfftfreq(1024, 1.0 / sr)
    tot = P.sum(axis=1) + 1e-12
    cen = (P * freqs).sum(axis=1) / tot
    flat = np.exp(np.mean(np.log(P + 1e-12), axis=1)) / (np.mean(P, axis=1) + 1e-12)
    roll = np.array([freqs[np.searchsorted(np.cumsum(p) / (p.sum() + 1e-12), 0.95)]
                     for p in P])
    return float(np.median(cen)), float(np.median(flat)), float(np.median(roll))


def band_rel(x, sr, bands=((300, 3400), (3400, 6000), (6000, 8000), (8000, 12000))):
    F = frames(x)
    if len(F) == 0:
        return {}
    P = (np.abs(np.fft.rfft(F, axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(1024, 1.0 / sr)
    out = {}
    ref = None
    for lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        v = P[m].sum()
        if lo == 300:
            ref = v
        out[(lo, hi)] = v
    if not ref:
        return {}
    return {k: 10 * np.log10(v / (ref + 1e-20) + 1e-20) for k, v in out.items()}


def f0_med(x, sr):
    if librosa is None:
        return float("nan")
    try:
        f0 = librosa.yin(x.astype(np.float32), fmin=70, fmax=400, sr=sr,
                         frame_length=2048)
        f0 = f0[np.isfinite(f0)]
        f0 = f0[f0 > 0]
        return float(np.median(f0)) if len(f0) else float("nan")
    except Exception:
        return float("nan")


def report(path, label):
    x, sr = load(path)
    dur = len(x) / sr
    env = rms_env(x, sr)
    nseg, voiced = count_bursts(env, sr)
    cen, flat, roll = spec_stats(x, sr)
    br = band_rel(x, sr)
    f0 = f0_med(x, sr)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x ** 2) + 1e-20))
    print(f"--- {label}")
    print(f"    文件 {os.path.basename(path)}")
    print(f"    时长 {dur:6.2f}s  sr {sr}  峰值 {peak:.3f}  RMS {20*np.log10(rms+1e-20):6.1f}dB")
    print(f"    有声段数 {nseg:3d}  有声合计 {voiced:6.2f}s  占空比 {voiced/max(dur,1e-6)*100:5.1f}%")
    print(f"    谱质心 {cen:7.0f}Hz  谱平坦度 {flat:.4f}  f95 {roll:7.0f}Hz  F0中位 {f0:6.1f}Hz")
    if br:
        s = "  ".join(f"{k[0]}-{k[1]}k:{v:6.1f}" for k, v in br.items())
        print(f"    频段(相对0.3-3.4k) {s}")
    return dict(dur=dur, nseg=nseg, voiced=voiced, cen=cen, flat=flat, roll=roll, f0=f0,
                peak=peak, rms=rms, bands=br)


def main():
    print("=" * 78)
    print("A. 目标音色参考音（应当像人说话）")
    print("=" * 78)
    ref = report(os.path.join(PKG, "00_目标音色_参考音.wav"), "REF 参考音")

    print()
    print("=" * 78)
    print("B. 五段克隆产出（用户听感：像'噢哦啊哦'）")
    print("=" * 78)
    outs = []
    for f in sorted(os.listdir(PKG)):
        if f.startswith(("01_", "02_", "03_", "04_", "05_")):
            outs.append((f, report(os.path.join(PKG, f), "CLONE")))

    print()
    print("=" * 78)
    print("C. A/B 对照（同句同参考音，唯一变量=tsr）")
    print("=" * 78)
    report(os.path.join(PKG, "99_对照_修复前16k链路.wav"), "A/B 修复前 16k")
    report(os.path.join(PKG, "99b_对照_修复后24k链路.wav"), "A/B 修复后 24k")

    print()
    print("=" * 78)
    print("D. 判据汇总")
    print("=" * 78)
    print(f"{'样本':<28}{'段数':>5}{'占空比':>8}{'谱质心':>9}{'平坦度':>9}{'F0':>8}")
    print(f"{'REF 参考音':<28}{ref['nseg']:>5}{ref['voiced']/ref['dur']*100:>7.1f}%{ref['cen']:>9.0f}{ref['flat']:>9.4f}{ref['f0']:>8.1f}")
    for f, r in outs:
        print(f"{f[:26]:<28}{r['nseg']:>5}{r['voiced']/r['dur']*100:>7.1f}%{r['cen']:>9.0f}{r['flat']:>9.4f}{r['f0']:>8.1f}")


if __name__ == "__main__":
    main()
