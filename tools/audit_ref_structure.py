# -*- coding: utf-8 -*-
"""参考音结构体检：找"连续、有声占比高"的候选（优于多段拼接）。

对每个候选给出：时长 / 有声占比 / 段数 / 最长连续段 / 长静音数 / F0 / 带宽 / 削波。
评判口径（llama.cpp-omni 烘焙的实践结论）：
  - 拼接段间静音会让合成学到"频繁停顿"的韵律 → 越连续越好
  - 有声占比 >85% 理想；段数越少越好；最长连续段越长越好
"""
import glob
import os

import numpy as np
import soundfile as sf


def f0_median(x, sr, fmin=80, fmax=500, frame=1024, hop=256):
    x = x - np.mean(x)
    n = (len(x) - frame) // hop
    out = []
    for i in range(max(0, n)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < 0.02:
            continue
        ac = np.correlate(s, s, mode="full")[frame - 1:]
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


def segments(x, sr, thr=0.006, min_seg=0.15):
    w = max(1, int(sr * 0.01))
    n = len(x) // w
    e = np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1))
    v = e > thr
    segs, i = [], 0
    while i < n:
        if v[i]:
            j = i
            while j < n and v[j]:
                j += 1
            if (j - i) * 0.01 >= min_seg:
                segs.append(((i * w) / sr, (j * w) / sr))
            i = j
        else:
            i += 1
    return segs


def audit(p):
    x, sr = sf.read(p, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = X.sum() + 1e-12
    segs = segments(x, sr)
    voiced = sum(b - a for a, b in segs)
    longest = max((b - a for a, b in segs), default=0.0)
    # 长静音（>0.4s）计数
    gaps = []
    for i in range(len(segs) - 1):
        gaps.append(segs[i + 1][0] - segs[i][1])
    long_gaps = sum(1 for g in gaps if g > 0.4)
    return dict(d=n / sr, sr=sr, ratio=voiced / (n / sr) * 100 if n else 0,
                nseg=len(segs), longest=longest, long_gaps=long_gaps,
                f0=f0_median(x, sr), hi8=X[f > 8000].sum() / tot * 100,
                hi4=X[f > 4000].sum() / tot * 100,
                pk=float(np.max(np.abs(x))) if n else 0,
                rms=float(np.sqrt(np.mean(x ** 2))) if n else 0)


CANDS = ([r"G:\ai对话\11\data\native_audio_test\src_candidates\0%d_*.wav" % i for i in range(1, 9)]
         + [r"G:\ai对话\11\data\native_audio_test\ref_candidates\C*.wav",
            r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL*48k.wav",
            r"G:\ai对话\11\data\native_audio_test\ref_build\daniya_ref_new_48k.wav",
            r"G:\ai对话\11\data\native_audio_test\ref_final\daniya_ref_final_48k.wav",
            r"G:\ai对话\11\data\native_audio_test\src_candidates\V0827_全片27.0s_48k.wav"])

files = []
seen = set()
for pat in CANDS:
    for p in sorted(glob.glob(pat)):
        if p not in seen:
            seen.add(p)
            files.append(p)

print("=" * 132)
print("参考音结构体检 —— 目标：时长 6~10s、有声占比高、段数少、最长连续段长")
print("=" * 132)
print(f"{'时长':>6} {'sr':>6} {'有声%':>6} {'段数':>4} {'最长段':>7} {'长静音':>5} "
      f"{'F0':>6} {'>4k%':>6} {'>8k%':>6} {'pk':>6}  文件")
print("-" * 132)
rows = []
for p in files:
    try:
        r = audit(p)
    except Exception as e:
        print(f"  ERR {p} {e}")
        continue
    rows.append((r, p))
    print(f"{r['d']:6.2f} {r['sr']:6d} {r['ratio']:6.1f} {r['nseg']:4d} {r['longest']:6.2f}s "
          f"{r['long_gaps']:5d} {r['f0']:6.1f} {r['hi4']:6.2f} {r['hi8']:6.2f} {r['pk']:6.3f}  "
          f"{os.path.basename(p)}")

print()
print("=" * 132)
print("综合排序（时限 6~10s 内；连续优先；音高贴近基准 265.9Hz）")
print("=" * 132)
ok = [(r, p) for r, p in rows if 6.0 <= r["d"] <= 10.0]
def score(item):
    r, p = item
    return ((r["ratio"] / 100) * 2.0            # 有声占比
            + (r["longest"] / 6.0)              # 最长连续段
            - r["long_gaps"] * 0.25             # 长静音惩罚
            - abs(r["f0"] - 265.9) / 265.9 * 1.5  # 音高偏离
            + r["hi8"] / 100 * 0.8)             # 空气感
for r, p in sorted(ok, key=score, reverse=True):
    print(f"  score={score((r,p)):5.3f}  {r['d']:5.2f}s 有声{r['ratio']:5.1f}% "
          f"段{r['nseg']:2d} 最长{r['longest']:4.2f}s 长静音{r['long_gaps']:2d} "
          f"F0={r['f0']:5.1f} >8k={r['hi8']:5.2f}%  {os.path.basename(p)}")
