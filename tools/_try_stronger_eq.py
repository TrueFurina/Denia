# -*- coding: utf-8 -*-
"""挑更好的亮度基准 + 生成更强的补偿版本，解决"模糊/不通透"。

思路：
  1) 把所有达妮娅候选素材量一遍（f95 / 8k+ / 分段 dB），挑**带宽最足**的当亮度基准。
     现在用的是 7s 旧 ref（f95 6453）；若有 48k 无损版，它的高频更真实 ⇒ 目标更"通透"。
  2) 生成三档给耳朵挑：
     V1 静态 100%（更猛）
     V2 静态 70% + SBR（高频跟辅音包络走，不是一片静态噪声）
     V3 静态 100% + SBR（最猛且带包络）
⚠️ SBR 目前**只存在于离线 hf_restore**，线上 hf_eq.py 没有 ⇒ 若选中带 SBR 的档，
   需要我把 SBR 加进 hf_eq.py（改动很小，只加一段折叠逻辑）。
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

OUTDIR = os.path.join(ROOT, "data", "duplex_out")
PKG = os.path.join(ROOT, "data", "高频补偿_更强档_20260923")
SR = 24000

CANDIDATES = [
    ("旧ref_7s", os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")),
    ("新素材_0813", os.path.join(ROOT, "assets", "voices", "daniya_ref_0813_24k.wav")),
    ("ref_final_48k", os.path.join(ROOT, "data", "native_audio_test", "ref_final",
                                   "daniya_ref_final_48k.wav")),
    ("ref_final_24k", os.path.join(ROOT, "data", "native_audio_test", "ref_final",
                                   "daniya_ref_final_24k.wav")),
    ("ref_v2_8s", os.path.join(ROOT, "data", "native_audio_test", "ref_v2",
                               "mp3_8s_24k.wav")),
]


def load(p):
    try:
        a, sr = sf.read(p, dtype="float32")
    except Exception:
        with wave.open(p, "rb") as w:
            nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if nch > 1:
            a = a.reshape(-1, nch).mean(axis=1)
    if getattr(a, "ndim", 1) > 1:
        a = a.mean(axis=1)
    return np.asarray(a, dtype=np.float32), sr


def spectrum(a, sr):
    n = int(sr * 0.032)
    hop = int(sr * 0.016)
    if len(a) < n:
        return None, None
    win = np.hanning(n)
    acc, cnt = None, 0
    for s in range(0, len(a) - n, hop):
        sp = np.abs(np.fft.rfft(a[s:s + n] * win, n=n * 2)) ** 2
        acc = sp if acc is None else acc + sp
        cnt += 1
    f = np.fft.rfftfreq(n * 2, 1.0 / sr)
    return f, acc / max(cnt, 1)


def m(p):
    a, sr = load(p)
    f, P = spectrum(a, sr)
    tot = P.sum()
    cum = np.cumsum(P) / tot
    f95 = float(f[np.searchsorted(cum, 0.95)])
    a8 = float(P[f >= 8000].sum() / tot * 100) if (f >= 8000).any() else float("nan")

    def bd(lo, hi):
        msk = (f >= lo) & (f < min(hi, sr / 2))
        return float(10 * np.log10(max(P[msk].sum(), 1e-20) / tot + 1e-20)) if msk.any() else float("nan")
    return dict(sr=sr, dur=len(a) / sr, f95=f95, a8=a8,
                b=[bd(3400, 6000), bd(6000, 8000), bd(8000, 12000)])


def build_output():
    subs = sorted(os.listdir(OUTDIR))
    latest = os.path.join(OUTDIR, subs[-1])
    ws = sorted(glob.glob(os.path.join(latest, "*.wav")))
    parts, total = [], 0.0
    gap = np.zeros(int(SR * 0.35), np.float32)
    for p in ws:
        a, sr = load(p)
        if sr != SR or len(a) / sr < 1.2:
            continue
        if total + len(a) / sr > 26.0:
            break
        parts.append(a)
        parts.append(gap)
        total += len(a) / sr + 0.35
    return np.concatenate(parts).astype(np.float32) if parts else None


def main():
    os.makedirs(PKG, exist_ok=True)
    print("① 候选亮度基准（挑带宽最足的）")
    print("   %-16s %6s %6s %8s %8s  %s" % ("素材", "sr", "时长", "f95", "8k+", "3.4-6k/6-8k/8-12k"))
    print("   " + "-" * 88)
    best, bestv = None, -1
    for tag, p in CANDIDATES:
        if not os.path.isfile(p):
            print("   %-16s （缺）" % tag)
            continue
        d = m(p)
        print("   %-16s %6d %5.1fs %8.0f %7.2f%%  %s"
              % (tag, d["sr"], d["dur"], d["f95"], d["a8"], np.round(d["b"], 1)))
        if d["f95"] > bestv:
            best, bestv = p, d["f95"]
    print("\n   ⇒ 亮度基准改用：%s（f95=%.0f）" % (os.path.basename(best), bestv))

    out = build_output()
    if out is None:
        print("没有产出样本")
        return 2
    ob, of99 = HF.bandprof(out)
    print("\n② 待补偿侧：f99=%.0f  3.4-6k/6-8k/8-12k = %s" % (of99, np.round(ob, 1)))

    ref, rsr = load(best)
    if rsr != SR:
        print("   基准 sr=%d ≠ 24000，bandprof/LTAS 仍可按其自身 sr 计算" % rsr)

    print("\n③ 生成三档")
    variants = []
    g1, _ = HF.build_gain(ref, out, scale=1.0)
    y1 = HF.apply_gain(out, g1)
    variants.append(("V1_静态100%", y1))
    g2, _ = HF.build_gain(ref, out, scale=0.7)
    y2 = HF.apply_gain(out, g2, sbr=True, sbr_db=-6.0)
    variants.append(("V2_70%+SBR", y2))
    g3, _ = HF.build_gain(ref, out, scale=1.0)
    y3 = HF.apply_gain(out, g3, sbr=True, sbr_db=-6.0)
    variants.append(("V3_100%+SBR", y3))

    # 原始
    p0 = os.path.join(PKG, "00_原始.wav")
    sf.write(p0, out, SR, subtype="PCM_16")
    print("   00_原始        %s  f99=%.0f" % (np.round(ob, 1), of99))
    for tag, y in variants:
        b, f99 = HF.bandprof(y)
        fl = HF.flatness(y)
        op = os.path.join(PKG, "%s.wav" % tag)
        sf.write(op, y, SR, subtype="PCM_16")
        print("   %-14s %s  f99=%.0f  8-12k平坦度=%.3f" % (tag, np.round(b, 1), f99, fl))

    print("\n输出：%s" % PKG)
    print("\n判读：")
    print("  · 8-12k 越接近基准越好；平坦度越接近 1 = 越像噪声 ⇒ 太高说明在灌嘶声")
    print("  · 若 V2/V3 明显更'通透' ⇒ 需要把 SBR 加进 hf_eq.py（小改动）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
