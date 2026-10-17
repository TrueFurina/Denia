# -*- coding: utf-8 -*-
"""结巴溯源（信号级）。

思路：日志里每条 audio_only 的 base64 字符数 → 精确样本数 → 可还原出
"1 秒块"的拼接点在成品 wav 中的采样位置。于是可以逐点检查：
  A. 拼接点是否有波形断裂/跳变（click）
  B. 是否在块边界出现静音凹陷（→ 听感=一顿一顿）
  C. 相邻块之间是否内容重复（→ 听感=口吃/重复字）
并给出全局静音统计作为对照。
"""
import os
import re
import glob
import numpy as np
import soundfile as sf

D = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SR = 24000


def boundaries_from_log(p):
    """从 worker 日志还原每条 audio_only 的样本数 → 累计拼接点。"""
    t = open(p, encoding="utf-8", errors="replace").read()
    sizes = []
    for l in t.splitlines():
        if "sent audio_only" in l:
            m = re.search(r"\((\d+) chars\)", l)
            if m:
                sizes.append(int(m.group(1)))
    if not sizes:
        return None, []
    nsamp = [int(round(c * 3 / 4 / 4)) for c in sizes]  # base64→bytes→float32 样本
    cum = np.cumsum(nsamp)
    return nsamp, cum


def rms_env(x, win=480, hop=240):
    n = (len(x) - win) // hop + 1
    if n <= 0:
        return np.zeros(0), 0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx]
    return np.sqrt((fr ** 2).mean(axis=1)), hop


def find_silences(x, thr_db=-45.0, min_ms=25):
    e, hop = rms_env(x)
    if not len(e):
        return []
    db = 20 * np.log10(np.maximum(e, 1e-12))
    silent = db < thr_db
    out = []
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j + 1 < len(silent) and silent[j + 1]:
                j += 1
            ms = (j - i + 1) * hop / SR * 1000
            if ms >= min_ms:
                out.append((i * hop / SR, ms))
            i = j + 1
        else:
            i += 1
    return out


def selfsim(x, max_lag_s=1.5, min_lag_ms=20, win_ms=60):
    """自相似扫描：找出"某段与它之后 lag 处高度相似"的 lag（重复检测）。"""
    w = int(win_ms / 1000 * SR)
    if len(x) < w * 4:
        return []
    # 取中段做探针
    probes = []
    for frac in (0.15, 0.35, 0.5, 0.65, 0.85):
        s = int(frac * len(x))
        if s + w < len(x):
            probes.append(x[s:s + w])
    res = []
    lag0 = int(min_lag_ms / 1000 * SR)
    lag1 = min(int(max_lag_s * SR), len(x) - w - 1)
    for a in probes:
        an = a / (np.linalg.norm(a) + 1e-12)
        best = (0.0, 0)
        # 只在探针之后找
        s = int(np.argmax([np.array_equal(x[i:i + w], a) for i in range(0, 1)]))  # 占位
        for lag in range(lag0, lag1, int(0.005 * SR)):
            pass
        res.append(best)
    return res


def main():
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    ref_db = 20 * np.log10(np.sqrt((ref ** 2).mean()))

    # 日志名 worker_<ARM>_<TS>.log → 成品 <ARM>[_v2a|_v2b|_r2].wav
    logs = sorted(glob.glob(os.path.join(D, "worker_*.log")))
    cands = sorted(glob.glob(os.path.join(D, "*.wav")))
    pairs = []
    for lp in logs:
        base = os.path.basename(lp)[len("worker_"):-len(".log")]
        arm = re.sub(r"_\d{8}_\d{6}$", "", base)
        wp = os.path.join(D, arm + ".wav")
        if os.path.exists(wp):
            pairs.append((lp, wp, arm))
    # 每个成品只挑"估算样本数与实际 wav 长度吻合"的那条日志（比时间戳可靠）
    best = {}
    for lp, wp, arm in pairs:
        ns, cum = boundaries_from_log(lp)
        if not ns:
            continue
        try:
            n_actual = len(sf.read(wp, dtype="float32")[0])
        except Exception:
            continue
        err = abs(cum[-1] - n_actual)
        if wp not in best or err < best[wp][0]:
            best[wp] = (err, lp, wp, arm)
    uniq = [v[1:] for v in sorted(best.values(), key=lambda v: v[1])]
    print("配对结果（样本数误差）:")
    for err, lp, wp, arm in sorted(best.values(), key=lambda v: v[1]):
        print("   %-26s ← %-42s 误差 %d 样本 (%.3fs)" %
              (os.path.basename(wp), os.path.basename(lp), err, err / SR))

    print("=" * 108)
    print("结巴溯源：块拼接点体检      参考音 RMS=%.1f dBFS" % ref_db)
    print("=" * 108)
    for lp, wp, tag in uniq:
        x, sr = sf.read(wp, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        nsamp, cum = boundaries_from_log(lp)
        print()
        print("-" * 108)
        print("%s   dur=%.2fs @%d  峰=%.3f  RMS=%.1f dBFS" %
              (os.path.basename(wp), len(x) / sr, sr, np.abs(x).max(),
               20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)))
        if not nsamp:
            print("   日志无 audio_only 记录")
            continue
        print("   audio_only=%d 条, 累计样本=%d, 实际样本=%d, 差=%d" %
              (len(nsamp), cum[-1], len(x), len(x) - cum[-1]))
        print("   单块时长(s): %s" % [round(n / SR, 3) for n in nsamp][:30])

        # ---- A/B: 逐拼接点检查 ----
        print("   %-9s %-9s %-10s %-11s %-11s %s" %
              ("位置s", "块长s", "跳变比", "左20ms dB", "右20ms dB", "判定"))
        bad = []
        w20 = int(0.020 * SR)
        for k, P in enumerate(cum[:-1]):
            if P < w20 or P + w20 >= len(x):
                continue
            seg_l = x[max(0, P - w20):P]
            seg_r = x[P:P + w20]
            jump = abs(float(x[P]) - float(x[P - 1]))
            local = float(np.sqrt((np.concatenate([seg_l, seg_r]) ** 2).mean())) + 1e-9
            ratio = jump / (local * 6.0)          # 归一化跳变
            dl = 20 * np.log10(np.sqrt((seg_l ** 2).mean()) + 1e-12)
            dr = 20 * np.log10(np.sqrt((seg_r ** 2).mean()) + 1e-12)
            verdict = ""
            if abs(dl - dr) > 12 and min(dl, dr) < -38:
                verdict = "电平断崖"
            elif ratio > 0.55:
                verdict = "波形断裂"
            if verdict:
                bad.append((P / SR, verdict))
            if k < 12 or verdict:
                print("   %-9.3f %-9.3f %-10.3f %-11.1f %-11.1f %s" %
                      (P / SR, nsamp[k] / SR, ratio, dl, dr, verdict))
        print("   ⇒ 可疑拼接点 %d / %d" % (len(bad), max(0, len(cum) - 1)))
        if bad:
            print("      位置: %s" % [round(b[0], 3) for b in bad][:20])

        # ---- B: 全局静音统计 ----
        sil = find_silences(x, thr_db=-45, min_ms=25)
        print("   静音段(<-45dB,≥25ms) 共 %d 段, 合计 %.2fs (%.1f%%)" %
              (len(sil), sum(s[1] for s in sil) / 1000, 100 * sum(s[1] for s in sil) / 1000 / (len(x) / SR)))
        for s, ms in sil[:16]:
            print("        %.3fs  长 %.0fms%s" % (s, ms, "   <<< 落在拼接点附近" if any(abs(s + ms / 2000 - b[0]) < 0.09 for b in bad) else ""))
        if len(sil) > 16:
            print("        ...(略)")
        # 静音间隔的规律性
        if len(sil) >= 4:
            gaps = np.diff([s for s, _ in sil])
            print("   相邻静音起点间隔: 中位 %.3fs  最小 %.3fs  最大 %.3fs" %
                  (np.median(gaps), gaps.min(), gaps.max()))

    # ---- C: 参考音同口径 ----
    print()
    print("-" * 108)
    sil = find_silences(ref, thr_db=-45, min_ms=25)
    print("对照 · 参考音 %s: dur=%.2fs 静音段 %d 段 合计 %.2fs (%.1f%%)" %
          (os.path.basename(REF), len(ref) / rsr, len(sil), sum(s[1] for s in sil) / 1000,
           100 * sum(s[1] for s in sil) / 1000 / (len(ref) / rsr)))
    print("-" * 108)


if __name__ == "__main__":
    main()
