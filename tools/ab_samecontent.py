# -*- coding: utf-8 -*-
"""同内容对照：三方开头都说了「你好呀，我是达妮娅」，只比这一句。

上一轮三方生成的文本长度差异巨大（S1/S3 跑飞 142s，S2 21s），
F0 / >4k 会被"内容与情绪"混淆。这里只取开头同句，做 apples-to-apples 比较。
"""
import os
import numpy as np
import soundfile as sf

D = r"G:\ai对话\11\data\native_audio_test\verify_wechat"
REF = r"G:\ai对话\11\data\native_audio_test\ref_wechat_final\daniya_ref_wechat_16k.wav"
ARMS = [("S1_new", "微信纯源精修（本次烘焙）"),
        ("S2_hist", "历史长期在线 247.82MB"),
        ("S3_rec", "录屏衍生 332MB")]


def f0_track(x, sr, fmin=70, fmax=500, frame=1024, hop=256, thr=0.008):
    x = x - x.mean()
    res = []
    for i in range(max(0, (len(x) - frame) // hop)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < thr:
            continue
        ac = np.correlate(s, s, "full")[frame - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lo, hi = int(sr / fmax), min(int(sr / fmin), len(ac) - 1)
        if hi <= lo:
            continue
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] < 0.35:
            continue
        res.append((i * hop / sr, sr / k, np.sqrt(np.mean(s ** 2))))
    return res


def seg_stats(x, sr, t0, t1, tag):
    a, b = int(t0 * sr), min(len(x), int(t1 * sr))
    y = x[a:b]
    if len(y) < 1024:
        return None
    n = len(y)
    S = np.abs(np.fft.rfft(y * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / sr)
    hi = S[(f > 4000) & (f < 8000)].sum() / (S[(f > 300) & (f < 8000)].sum() + 1e-12) * 100
    tr = f0_track(y, sr)
    f0 = float(np.median([t[1] for t in tr])) if tr else 0.0
    return dict(tag=tag, dur=(b - a) / sr, f0=f0, hi4k=hi,
                rms=float(np.sqrt(np.mean(y ** 2))), nframes=len(tr))


def main():
    rx, rsr = sf.read(REF, dtype="float32")
    if rx.ndim > 1:
        rx = rx.mean(axis=1)

    print("=" * 108)
    print("同内容对照：开头同句「你好呀，我是达妮娅」")
    print("=" * 108)
    print(f"{'臂':<10}{'窗口':>14}{'F0':>8}{'4-8k占(0.3-8k)%':>16}{'rms':>9}{'浊音帧':>8}")
    print("-" * 108)

    for t0, t1 in [(0.0, 2.0), (0.0, 3.0)]:
        for tag, desc in ARMS:
            p = os.path.join(D, tag + ".wav")
            if not os.path.exists(p):
                continue
            x, sr = sf.read(p, dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            s = seg_stats(x, sr, t0, t1, tag)
            if s:
                print(f"{s['tag']:<10}{'%.1f-%.1fs'%(t0,t1):>14}{s['f0']:>8.1f}"
                      f"{s['hi4k']:>16.2f}{s['rms']:>9.4f}{s['nframes']:>8d}")
        print("-" * 108)

    print()
    print("参考音自身（整段 7.04s）", "F0=%.1fHz" % float(np.median([t[1] for t in f0_track(rx, rsr)])),
          " | 参考音 >4k 占比另计 §见上表 28.70%")
    print()
    print("=" * 108)
    print("F0 逐帧轨迹（前 3.0s，看音高走向是否与参考一致）")
    print("=" * 108)
    for tag, desc in ARMS:
        p = os.path.join(D, tag + ".wav")
        if not os.path.exists(p):
            continue
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        tr = [t for t in f0_track(x, sr) if t[0] < 3.0]
        line = " ".join("%3.0f" % t[1] for t in tr[::4])
        print(f"  {tag:<9} {line[:104]}")
    tr = [t for t in f0_track(rx, rsr) if t[0] < 3.0]
    print(f"  {'参考':<9} " + " ".join("%3.0f" % t[1] for t in tr[::4])[:104])
    print()
    print("目录: %s" % D)


if __name__ == "__main__":
    main()
