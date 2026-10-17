# -*- coding: utf-8 -*-
"""用同一把尺子量【真实通话】的 AI 音频 vs 我的测试台产出。

自适应阈值 = 该文件自身 RMS - 25dB（消除电平差异带来的假静音）。
"""
import os
import numpy as np
import soundfile as sf

R = r"H:\Comni\_internal\resources\apps\server\data\sessions"
SR = 24000

SESSIONS = [
    "20260921_175639_omni_1789984596058",
    "20260917_210107_omni_1789650057521",
    "20260917_022229_omni_1789582936015",
    "20260915_214011_omni_1789479607582",
]
MINE = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"


def metric(x, thr_off=-25.0, hop=120, win=480):
    if len(x) < win * 3:
        return None
    n = (len(x) - win) // hop + 1
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    e = np.sqrt((x[idx] ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(e, 1e-12))
    rms_db = 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)
    lab = db >= (rms_db + thr_off)
    runs, i = [], 0
    while i < len(lab):
        j = i
        while j + 1 < len(lab) and lab[j + 1] == lab[i]:
            j += 1
        runs.append(("V" if lab[i] else "P", i * hop / SR, (j - i + 1) * hop / SR))
        i = j + 1
    v = [d for k, s, d in runs if k == "V"]
    p = [d for k, s, d in runs if k == "P"]
    dur = len(x) / SR
    return dict(dur=dur, rms=rms_db, n_v=len(v), n_p=len(p),
                v_med=float(np.median(v)) if v else 0.0,
                v_frac=sum(v) / dur,
                p_ge15=sum(1 for d in p if d >= 0.15),
                p_med=float(np.median(p)) if p else 0.0,
                p_max=max(p) if p else 0.0,
                vlt02=sum(1 for d in v if d < 0.2))


def show(tag, x):
    r = metric(x)
    if not r:
        print(f"   {tag:<44s} 太短/空")
        return None
    print(f"   {tag:<40s} {r['dur']:6.2f}s {r['rms']:6.1f}dB | "
          f"浊音{r['n_v']:3d}个 中位{r['v_med']:.3f}s 其中<0.2s有{r['vlt02']:2d}个 占{r['v_frac']*100:.0f}% | "
          f"静音{r['n_p']:3d}个 中位{r['p_med']:.3f}s 最长{r['p_max']:.3f}s ≥0.15s有{r['p_ge15']}个")
    return r


def load_dir(p, limit_s=60.0, pats=(".wav", ".pcm", ".raw")):
    if not os.path.isdir(p):
        return None, 0, 0
    fs = sorted(f for f in os.listdir(p) if f.lower().endswith(pats))
    segs, tot, n, sr0 = [], 0.0, 0, None
    for f in fs:
        q = os.path.join(p, f)
        x = None
        try:
            x, sr = sf.read(q, dtype="float32")
            sr0 = sr or sr0
        except Exception:
            try:
                b = open(q, "rb").read()
                x = np.frombuffer(b, dtype=np.float32)
                sr0 = SR
            except Exception:
                continue
        if x is None or len(x) == 0:
            continue
        if x.ndim > 1:
            x = x.mean(axis=1)
        segs.append(x)
        tot += len(x) / (sr0 or SR)
        n += 1
        if tot >= limit_s:
            break
    if not segs:
        return None, 0, 0
    return np.concatenate(segs), n, (sr0 or SR)


def main():
    print("=" * 118)
    print("自适应阈值（自身 RMS-25dB）下的碎块指标 —— 同一把尺子")
    print("=" * 118)
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    show("【基准】达妮娅参考音(天然语音)", ref)
    for w in ("A_FIXED.wav", "A_FIXED_r2.wav", "A_FIXED_v2a.wav", "B_NATIVE.wav"):
        p = os.path.join(MINE, w)
        if os.path.exists(p):
            x, sr = sf.read(p, dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            show("【我测试台】" + w, x)

    print()
    print("【真机通话】ai_audio 逐块拼接（最多 60s）")
    for d in SESSIONS:
        p = os.path.join(R, d, "ai_audio")
        x, n, sr = load_dir(p, limit_s=60.0)
        if x is None:
            print(f"   {d:<44s} 无 ai_audio")
            continue
        show(f"真机 {d[9:23]} ({n}块,{sr}Hz)", x)
        # 块边界跳变 + 单块时长
        fs = sorted(f for f in os.listdir(p) if f.lower().endswith(".wav"))
        cuts, off, durs = [], 0, []
        for f in fs[:80]:
            try:
                y, s = sf.read(os.path.join(p, f), dtype="float32")
                i2 = sf.info(os.path.join(p, f))
            except Exception:
                continue
            if y.ndim > 1:
                y = y.mean(axis=1)
            off += len(y)
            cuts.append(off)
            durs.append(i2.duration)
        jumps = []
        for c in cuts[:-1]:
            if 20 <= c < len(x) - 20:
                j = abs(float(x[c]) - float(x[c - 1]))
                loc = float(np.sqrt((x[max(0, c - 480):c + 480] ** 2).mean())) + 1e-9
                jumps.append(j / (loc * 6.0))
        if jumps:
            ja = np.array(jumps)
            print(f"        块边界跳变比 中位={np.median(ja):.3f} 最大={ja.max():.3f} 超0.55的={int((ja>0.55).sum())}/{len(ja)}")
        if durs:
            dd = np.array(durs)
            print(f"        单块时长 中位={np.median(dd):.3f}s 最小={dd.min():.3f} 最大={dd.max():.3f} 共{len(dd)}块")
    print("=" * 118)


if __name__ == "__main__":
    main()
