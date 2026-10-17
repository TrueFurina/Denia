# -*- coding: utf-8 -*-
"""体检：微信纯净源 vs 旧参考 vs 录屏候选。只读，不改任何文件。"""
import os, sys, json
import numpy as np
import soundfile as sf

CAND = {
    "WECHAT_raw":      r"H:\daniya_voice_tools\wechat_ref.wav",
    "WECHAT_norm":     r"H:\daniya_voice_tools\wechat_ref_norm.wav",
    "ORIG_16k":        r"H:\daniya_voice_tools\daniya_ref_ORIGINAL_16k.wav",
    "OLD_live_ref":    r"G:\ai对话\11\assets\voices\daniya_ref.wav",
    "C2_48k(录屏)":    r"G:\ai对话\11\data\native_audio_test\ref_candidates\C2_calm_natural_48k.wav",
    "REF_FINAL_48k":   r"G:\ai对话\11\data\native_audio_test\ref_final\daniya_ref_final_48k.wav",
    "dataset_8月13日": r"H:\daniya_voice_tools\daniya_dataset\8月13日.wav",
    "dataset_ww":      r"H:\daniya_voice_tools\daniya_dataset\ww.wav",
}

def f0_median(x, sr):
    """numpy 自相关法（YIN 简化），返回浊音帧 F0 中位。"""
    fl = int(0.04 * sr); hop = int(0.01 * sr)
    lo, hi = int(sr / 400), int(sr / 60)
    out = []
    for i in range(0, len(x) - fl, hop):
        f = x[i:i + fl]
        if np.sqrt(np.mean(f ** 2)) < 0.01:
            continue
        f = f - f.mean()
        ac = np.correlate(f, f, 'full')[fl - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if len(seg) == 0:
            continue
        p = lo + int(np.argmax(seg))
        if ac[p] / ac[0] < 0.3:
            continue
        out.append(sr / p)
    return float(np.median(out)) if out else 0.0

def band_energy_share(x, sr, f_lo, f_hi):
    """指定频带能量占比（相对全带）"""
    n = min(len(x), sr * 10)
    seg = x[:n]
    if len(seg) < 2048:
        return 0.0
    w = np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg * w)) ** 2
    freqs = np.fft.rfftfreq(len(seg), 1.0 / sr)
    tot = S.sum()
    if tot <= 0:
        return 0.0
    m = (freqs >= f_lo) & (freqs < f_hi)
    return float(S[m].sum() / tot)

def analyze(tag, path):
    r = {"tag": tag, "path": path}
    if not os.path.exists(path):
        r["error"] = "MISSING"
        return r
    info = sf.info(path)
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    x = x.mean(axis=1)
    r["sr"] = info.samplerate
    r["dur"] = round(len(x) / sr, 3)
    r["ch"] = info.channels
    r["subtype"] = info.subtype
    r["size_mb"] = round(os.path.getsize(path) / 1e6, 2)
    r["rms"] = round(float(np.sqrt(np.mean(x ** 2))), 5)
    r["peak"] = round(float(np.max(np.abs(x))), 4)
    r["clip_pct"] = round(float(np.mean(np.abs(x) > 0.99) * 100), 4)
    # 20ms 帧能量 -> 有效语音占比
    fl = int(0.02 * sr)
    nf = len(x) // fl
    if nf > 0:
        fr = x[:nf * fl].reshape(nf, fl)
        e = np.sqrt(np.mean(fr ** 2, axis=1))
        thr = max(e.max() * 0.06, 1e-4)
        r["voice_pct"] = round(float(np.mean(e > thr) * 100), 1)
        r["floor_rms"] = round(float(np.median(e[e <= np.percentile(e, 20)])), 6)
    r[">8k_pct"] = round(band_energy_share(x, sr, 8000, sr / 2) * 100, 2)
    r["4-8k_pct"] = round(band_energy_share(x, sr, 4000, 8000) * 100, 2)
    r["<300Hz_pct"] = round(band_energy_share(x, sr, 20, 300) * 100, 2)
    r["F0_med"] = round(f0_median(x, sr), 1)
    return r

rows = [analyze(t, p) for t, p in CAND.items()]
keys = ["tag", "sr", "dur", "ch", "subtype", "size_mb", "rms", "peak", "clip_pct",
        "voice_pct", "floor_rms", ">8k_pct", "4-8k_pct", "<300Hz_pct", "F0_med", "error"]
print("=" * 118)
print(f"{'tag':<18}{'sr':>6}{'dur':>8}{'ch':>4}{'size':>8}{'rms':>9}{'peak':>7}{'clip%':>7}{'voice%':>8}{'floor':>9}{'>8k%':>7}{'4-8k%':>7}{'<300%':>7}{'F0':>7}")
print("-" * 118)
for r in rows:
    if r.get("error"):
        print(f"{r['tag']:<18}  !! {r['error']}  {r['path']}")
        continue
    print(f"{r['tag']:<18}{r['sr']:>6}{r['dur']:>8.2f}{r['ch']:>4}{r['size_mb']:>8.2f}{r['rms']:>9.5f}{r['peak']:>7.3f}{r['clip_pct']:>7.3f}{r['voice_pct']:>8.1f}{r['floor_rms']:>9.5f}{r['>8k_pct']:>7.2f}{r['4-8k_pct']:>7.2f}{r['<300Hz_pct']:>7.2f}{r['F0_med']:>7.1f}")
print("=" * 118)
with open(r"G:\ai对话\11\data\native_audio_test\_wechat_inventory.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)
print("JSON -> data/native_audio_test/_wechat_inventory.json")
