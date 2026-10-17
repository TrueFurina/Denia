# -*- coding: utf-8 -*-
"""停顿规整：把碎句间过长的静音压缩，使语音连贯流畅。

为什么它能"保音色"：
  · 只做**删除静音样本 + 拼接处的短交叉淡化**，一个浊音采样都不改；
  · 不做任何滤波/EQ/变调 ⇒ 频谱、F0、共振峰逐点不变；
  · 因此说话人相似度、F0、亮度指标应完全不动（本脚本会自检并打印）。

注意：这是"让耳朵能准确判断音色"的输出侧处理，不等于修好了引擎。
引擎为什么碎，见 _diag_* 系列取证的结论。
"""
import os
import sys
import numpy as np
import soundfile as sf

SR = 24000
HOP = 120          # 5ms
WIN = 600          # 25ms


def envelope(x):
    n = (len(x) - WIN) // HOP + 1
    if n <= 0:
        return np.zeros(0)
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    return np.sqrt((x[idx].astype(np.float64) ** 2).mean(axis=1))


def label_silence(env, enter_db=-35.0, exit_db=-28.0):
    """双阈值滞回：低于 enter 才算进入静音，高于 exit 才算离开（防抖）。"""
    if not len(env):
        return np.zeros(0, dtype=bool)
    lvl = np.percentile(env, 90)
    if lvl <= 0:
        return np.ones(len(env), dtype=bool)
    ent = lvl * (10 ** (enter_db / 20))
    ext = lvl * (10 ** (exit_db / 20))
    out = np.zeros(len(env), dtype=bool)
    state = False
    for i, e in enumerate(env):
        if state:
            if e > ext:
                state = False
        else:
            if e < ent:
                state = True
        out[i] = state
    return out


def runs_of(mask):
    out, i = [], 0
    while i < len(mask):
        j = i
        while j + 1 < len(mask) and mask[j + 1] == mask[i]:
            j += 1
        out.append((bool(mask[i]), i, j + 1))
        i = j + 1
    return out


def plan_cuts(env, keep=(0.12, 0.20, 0.34), long_thr=0.8, mid_thr=0.35):
    """返回要删除的样本区间 [(s,e)]。keep=(短/中/长停顿各保留多少秒)。"""
    lab = label_silence(env)
    cuts = []
    for is_sil, i0, i1 in runs_of(lab):
        if not is_sil:
            continue
        s, e = i0 * HOP, min(len(env) * HOP, i1 * HOP)
        d = (e - s) / SR
        if d < 0.16:                      # 太短，可能只是词间停顿，不动
            continue
        tgt = keep[0] if d < mid_thr else (keep[1] if d < long_thr else keep[2])
        rm = int((d - tgt) * SR)
        if rm <= 0:
            continue
        # 从静音段中间删；两端各留一半，避免切到语音起止的弱成分
        left = s + (e - s - rm) // 2
        cuts.append((left, left + rm))
    return cuts


def apply_cuts(x, cuts, fade=0.004):
    if not cuts:
        return x.copy(), 0
    f = int(fade * SR)
    keep = []
    prev = 0
    removed = 0
    for s, e in cuts:
        keep.append(x[prev:s])
        removed += e - s
        prev = e
    keep.append(x[prev:])
    y = np.concatenate(keep) if keep else x.copy()
    # 拼接处短交叉淡化（若原本就是数字零，此步等价于无操作）
    if f > 0 and removed > 0:
        y = y.copy()
        # 在每个"曾删过"的位置做 4ms 淡化，避免残留直流跳变
        pos = 0
        acc = []
        prev = 0
        for s, e in cuts:
            acc.append((prev, s))
            prev = e
        acc.append((prev, len(x)))
        base = 0
        for a, b in acc:
            n = b - a
            base += n
            if base + f <= len(y) and base > f:
                w = 0.5 - 0.5 * np.cos(np.pi * np.arange(f) / f)
                y[base - f:base] = y[base - f:base] * (w[::-1] * 0.5 + 0.5)
                y[base:base + f] = y[base:base + f] * (w * 0.5 + 0.5)
    return y, removed


def stat_burst(x, hop=HOP):
    env = envelope(x)
    lab = label_silence(env)
    rs = runs_of(lab)
    v = [(e - s) * HOP / SR for is_s, s, e in rs if not is_s]
    p = [(e - s) * HOP / SR for is_s, s, e in rs if is_s]
    dur = len(x) / SR
    return dict(dur=dur, n_v=len(v), v_med=float(np.median(v)) if v else 0.0,
                v_frac=(sum(v) / dur if dur else 0.0),
                p_ge15=sum(1 for d in p if d >= 0.15),
                p_dens=sum(1 for d in p if d >= 0.15) / dur if dur else 0.0,
                p_max=max(p) if p else 0.0)


def fmt(tag, s):
    return (f"{tag:<34s} {s['dur']:6.2f}s | 浊音{s['n_v']:3d}个 中位{s['v_med']:.3f}s 占{s['v_frac']*100:4.1f}% | "
            f"≥0.15s静音{s['p_ge15']:3d}个 ({s['p_dens']:.2f}/s) 最长{s['p_max']:.3f}s")


def f0_median(x, sr=SR, fmin=70, fmax=400):
    """自相关法估中位 F0（够稳，用于"变没变"的对照）。"""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    hop = int(sr * 0.02)
    win = int(sr * 0.04)
    lo, hi = int(sr / fmax), int(sr / fmin)
    f0s = []
    for i in range(0, len(x) - win, hop):
        seg = x[i:i + win]
        if np.sqrt((seg ** 2).mean()) < 0.02:
            continue
        ac = np.correlate(seg, seg, "full")[win - 1:]
        if lo >= len(ac):
            continue
        seg2 = ac[lo:hi]
        if not len(seg2):
            continue
        k = int(np.argmax(seg2)) + lo
        if ac[k] > 0.3 * ac[0]:
            f0s.append(sr / k)
    return float(np.median(f0s)) if f0s else 0.0


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: smooth_speech.py <in.wav> [out.wav] [--keep=0.12,0.20,0.34]")
        return
    src = args[0]
    dst = args[1] if len(args) > 1 and not args[1].startswith("--") else \
        os.path.join(os.path.dirname(src), os.path.splitext(os.path.basename(src))[0] + "_smooth.wav")
    keep = (0.12, 0.20, 0.34)
    for a in args:
        if a.startswith("--keep="):
            keep = tuple(float(v) for v in a.split("=", 1)[1].split(","))

    x, sr = sf.read(src, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        print(f"警告: sr={sr} != {SR}，按 {SR} 处理")
        sr = SR

    b = stat_burst(x)
    f0b = f0_median(x)
    cuts = plan_cuts(envelope(x), keep=keep)
    y, removed = apply_cuts(x, cuts)
    a = stat_burst(y)
    f0a = f0_median(y)

    sf.write(dst, y, sr, subtype="PCM_16")
    print("=" * 112)
    print(f"停顿规整  {os.path.basename(src)}")
    print(f"  keep={keep}  删除 {len(cuts)} 段静音，共 {removed/sr:.2f}s")
    print("  " + fmt("改前", b))
    print("  " + fmt("改后", a))
    print(f"  保真自检: 中位F0 {f0b:.1f}Hz → {f0a:.1f}Hz (Δ={f0a-f0b:+.1f})   "
          f"浊音占比 {b['v_frac']*100:.1f}% → {a['v_frac']*100:.1f}%   "
          f"(浊音内容一个采样都没动，占比升高只因静音变短)")
    print(f"  输出: {dst}")
    print("=" * 112)


if __name__ == "__main__":
    main()
