# -*- coding: utf-8 -*-
"""带宽体检：回答"到底是不是 8k 输出/架构问题"。

量的东西（都是客观的、不靠耳朵）：
  f95      = 95% 能量截止频率（人耳"亮不亮、像不像本人"主要由 2~6kHz 决定）
  8k+ 占比 = 8kHz 以上能量占总能量比例
  分段能量 = 0-2k / 2-4k / 4-8k / 8-12k（dB，相对总能量）
  flat     = 谱平坦度（<0.0005 ⇒ 纯音调/坏产出；正常语音 0.002~0.025）

关键判据（本项目已标定）：
  真人/真源      f95 ≈ 1828~1992？  ← 注意：这是**达妮娅真源**实测 6445，见下方对照
  双工产出       f95 1000~1430 / flat 0.002~0.008
  CosyVoice2坏产出 f95 516~867 / flat ≈0.0001  ⇒ 纯嗡鸣

⚠️ 本脚本的价值：把"真源 / 原生产出 / 当前产出"放在同一把尺子上量，
   若当前产出 f95 只有真源的 1/3 ⇒ 确认是带宽（架构）问题；
   若三者接近 ⇒ 带宽不是主因，得回头查说话人/条件链路。
"""
import os
import sys
import glob
import wave

import numpy as np

ROOT = r"G:\ai对话\11"
OUTDIR = os.path.join(ROOT, "data", "duplex_out")

TARGETS = [
    ("★真源_达妮娅", os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")),
    ("原生底色B", os.path.join(ROOT, "data", "native_audio_test", "final_sample", "B_NATIVE.wav")),
    ("原生签名", os.path.join(ROOT, "voice_baseline", "20260922_144014",
                             "02_ref_minicpm_signature.wav")),
]


def load(p):
    with wave.open(p, "rb") as w:
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a, sr


def spectrum(a, sr):
    """Welch 式平均功率谱。"""
    n = int(sr * 0.032)          # 32ms
    hop = int(sr * 0.016)
    if len(a) < n:
        return None, None
    win = np.hanning(n)
    acc = None
    for s in range(0, len(a) - n, hop):
        seg = a[s:s + n] * win
        sp = np.abs(np.fft.rfft(seg, n=n * 2)) ** 2
        acc = sp if acc is None else acc + sp
    acc = acc / max(1, len(range(0, len(a) - n, hop)))
    freqs = np.fft.rfftfreq(n * 2, 1.0 / sr)
    return freqs, acc


def metrics(p):
    a, sr = load(p)
    f, P = spectrum(a, sr)
    if f is None:
        return None
    tot = P.sum()
    if tot <= 0:
        return None
    # f95：累计能量达 95% 的频率
    cum = np.cumsum(P) / tot
    f95 = float(f[np.searchsorted(cum, 0.95)])
    nyq = sr / 2.0

    def band(lo, hi):
        m = (f >= lo) & (f < min(hi, nyq))
        if not m.any():
            return float("nan")
        return float(10 * np.log10(max(P[m].sum(), 1e-20) / tot + 1e-20))

    above8 = float(P[f >= 8000].sum() / tot * 100) if (f >= 8000).any() else float("nan")
    # 谱平坦度：几何均值/算术均值
    Pn = P + 1e-20
    flat = float(np.exp(np.log(Pn).mean()) / Pn.mean())
    return dict(sr=sr, dur=len(a) / sr, f95=f95,
                b02=band(0, 2000), b24=band(2000, 4000),
                b48=band(4000, 8000), b812=band(8000, 12000),
                above8=above8, flat=flat)


def main():
    rows = []
    for tag, p in TARGETS:
        if os.path.isfile(p):
            m = metrics(p)
            if m:
                rows.append((tag, m))
        else:
            print("缺 %s" % p)

    subs = sorted(os.listdir(OUTDIR))
    d = os.path.join(OUTDIR, subs[-1])
    ws = sorted(glob.glob(os.path.join(d, "*.wav")))
    print("产出会话：%s（%d 句），取前 8 句" % (subs[-1], len(ws)))
    for w in ws[:8]:
        m = metrics(w)
        if m:
            rows.append(("产出_" + os.path.basename(w)[:16], m))

    print("\n%-24s %6s %7s %8s %8s %8s %8s %8s %8s"
          % ("样本", "sr", "时长", "f95", "0-2k", "2-4k", "4-8k", "8-12k", "8k+%" ))
    print("-" * 104)
    for tag, m in rows:
        print("%-24s %6d %6.1fs %8.0f %7.1fdB %7.1fdB %7.1fdB %7.1fdB %7.2f%%"
              % (tag, m["sr"], m["dur"], m["f95"], m["b02"], m["b24"],
                 m["b48"], m["b812"], m["above8"]))
    print("-" * 104)

    def pick(prefix):
        xs = [m for t, m in rows if t.startswith(prefix)]
        return xs

    src = pick("★真源")
    out = pick("产出_")
    nat = [m for t, m in rows if t.startswith("原生")]
    if src and out:
        s = src[0]
        of = sum(x["f95"] for x in out) / len(out)
        oa = sum(x["above8"] for x in out) / len(out)
        print("\n【对照结论】")
        print("  真源 f95 = %.0f Hz   8k+占比 = %.2f%%" % (s["f95"], s["above8"]))
        print("  产出 f95 = %.0f Hz   8k+占比 = %.2f%%   (n=%d 句)" % (of, oa, len(out)))
        print("  ⇒ 产出带宽是真源的 **%.0f%%**" % (of / s["f95"] * 100))
        if nat:
            nf = sum(x["f95"] for x in nat) / len(nat)
            print("  原生产出 f95 = %.0f Hz ⇒ 与当前产出 %s"
                  % (nf, "接近（说明带宽损失与克隆无关，是链路/架构共性）"
                     if abs(nf - of) / max(nf, 1) < 0.35 else "不同"))
        print("\n  8-12k 段：真源 %.1fdB / 产出 %.1fdB ⇒ 差 %.1f dB"
              % (s["b812"], sum(x["b812"] for x in out) / len(out),
                 (sum(x["b812"] for x in out) / len(out)) - s["b812"]))
        print("  注：若 8-12k 只是被压低（如 -20~-30dB，仍有能量）⇒ EQ/高频补偿**有效**；")
        print("      若接近 -80dB（真零）⇒ EQ 无用，那是架构硬上限。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
