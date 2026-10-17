# -*- coding: utf-8 -*-
"""结巴定量：语音"碎块"结构 + 模型实际文本。

判据：自然语流的浊音连续段(run)通常 0.5~2s，停顿 0.1~0.4s 且稀疏。
若 run 普遍 < 0.4s、停顿 > 0.25s 且密集 → 听感即"结巴/一顿一顿"。
"""
import os
import re
import glob
import numpy as np
import soundfile as sf

D = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SR = 24000


def env_db(x, win=480, hop=120):
    n = (len(x) - win) // hop + 1
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    e = np.sqrt((x[idx] ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(e, 1e-12)), hop


def runs(db, hop, thr=-40.0):
    """返回 [(kind, start_s, dur_s)]，kind ∈ {voiced, pause}"""
    out = []
    lab = db >= thr
    i = 0
    while i < len(lab):
        j = i
        while j + 1 < len(lab) and lab[j + 1] == lab[i]:
            j += 1
        out.append(("voiced" if lab[i] else "pause", i * hop / SR, (j - i + 1) * hop / SR))
        i = j + 1
    return out


def report(name, x, sr):
    db, hop = env_db(x)
    r = runs(db, hop)
    voiced = [d for k, s, d in r if k == "voiced"]
    pause = [d for k, s, d in r if k == "pause"]
    dur = len(x) / sr
    vt = sum(voiced)
    print()
    print("=" * 104)
    print("%s   dur=%.2fs   RMS=%.1f dBFS  峰值=%.3f"
          % (name, dur, 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12), np.abs(x).max()))
    print("  浊音段 %d 个 (合计 %.2fs, 占 %.1f%%)   静音段 %d 个 (合计 %.2fs, 占 %.1f%%)"
          % (len(voiced), vt, 100 * vt / dur, len(pause), dur - vt, 100 * (dur - vt) / dur))
    if voiced:
        v = np.array(sorted(voiced, reverse=True))
        print("  浊音段时长 中位=%.3fs 均值=%.3fs 最长=%.3fs" % (np.median(v), v.mean(), v.max()))
        print("  浊音段时长 分布: <0.2s=%d, 0.2~0.4s=%d, 0.4~0.8s=%d, 0.8~1.5s=%d, >1.5s=%d"
              % (sum(1 for d in v if d < 0.2), sum(1 for d in v if 0.2 <= d < 0.4),
                 sum(1 for d in v if 0.4 <= d < 0.8), sum(1 for d in v if 0.8 <= d < 1.5),
                 sum(1 for d in v if d >= 1.5)))
    if pause:
        p = np.array(pause)
        sig = p[p >= 0.15]
        print("  静音段(≥0.15s) %d 个, 中位=%.3fs 最长=%.3fs, 密度=%.2f 个/秒"
              % (len(sig), np.median(sig) if len(sig) else 0, p.max(), len(sig) / dur))
    # 碎块指数：每个浊音段平均被多少静音隔开
    print("  碎块指数 = 浊音段数/秒 = %.2f  (自然语音参考通常 1.5~3)" % (len(voiced) / dur))
    return dict(n_v=len(voiced), vt=vt, dur=dur, n_p=len(pause), pause=pause)


def main():
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    report("【对照】参考音 %s" % os.path.basename(REF), ref, rsr)

    for w in ["A_FIXED.wav", "A_FIXED_r2.wav", "A_FIXED_v2a.wav", "A_FIXED_v2b.wav", "B_NATIVE.wav"]:
        p = os.path.join(D, w)
        if os.path.exists(p):
            x, sr = sf.read(p, dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            report(w, x, sr)

    print()
    print("=" * 104)
    print("模型实际文本（从 worker 日志抓）")
    print("=" * 104)
    for lp in sorted(glob.glob(os.path.join(D, "worker_*.log")))[-3:]:
        t = open(lp, encoding="utf-8", errors="replace").read()
        zh = []
        for l in t.splitlines():
            if re.search(r"[\u4e00-\u9fff]", l) and "Duplex" not in l:
                zh.append(l.strip()[:170])
        print()
        print("--- %s  中文行 %d 条" % (os.path.basename(lp), len(zh)))
        for l in zh[:24]:
            print("   " + l)
        if len(zh) > 24:
            print("   ...(略)")


if __name__ == "__main__":
    main()
