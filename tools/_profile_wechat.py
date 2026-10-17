# -*- coding: utf-8 -*-
"""微信纯净源深度体检 + live cache 现状清点（只读）"""
import os, glob, hashlib
import numpy as np
import soundfile as sf

W = r"H:\daniya_voice_tools\wechat_ref.wav"
WN = r"H:\daniya_voice_tools\wechat_ref_norm.wav"
CACHE_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"

print("=" * 100)
print("【A】prompt_cache.gguf 家谱（大小 / 时间 / 部分 md5）")
print("=" * 100)
files = sorted(glob.glob(os.path.join(CACHE_DIR, "prompt_cache.gguf*")),
               key=lambda p: os.path.getmtime(p))
KEY = ("prompt_cache.gguf", "bak_before_final_20260921_211602",
       "bak_prev_20260921_090130", "swapbak_20260921_094240")
for p in files:
    sz = os.path.getsize(p) / 1e6
    import time as _t
    ts = _t.strftime("%m-%d %H:%M", _t.localtime(os.path.getmtime(p)))
    tag = ""
    if os.path.basename(p) == "prompt_cache.gguf":
        tag = "  <== LIVE"
    h = ""
    if any(k in os.path.basename(p) for k in KEY):
        m = hashlib.md5()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                m.update(b)
        h = " md5=" + m.hexdigest()[:12]
    print(f"  {sz:9.2f}MB  {ts}  {os.path.basename(p):<52}{h}{tag}")

# ---------------------------------------------------------------- 微信源
print()
print("=" * 100)
print("【B】微信纯净源 8.03s 内部结构（10ms 帧能量）")
print("=" * 100)
x, sr = sf.read(W, dtype="float32")
print(f"  文件: {W}\n  采样率={sr}  时长={len(x)/sr:.3f}s  peak={np.abs(x).max():.4f}  "
      f"rms={np.sqrt(np.mean(x**2)):.5f}")

w = int(sr * 0.01)
m = len(x) // w
e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
db = 20 * np.log10(np.maximum(e, 1e-8))

# 静音 gap 检测（用于找天然句界 + 首尾裁剪）
thr = e.max() * 0.05
voiced = e > thr
print(f"  能量阈值 = max*0.05 = {thr:.5f}  浊音帧占比 = {voiced.mean()*100:.1f}%")

# 首尾静音
idx = np.where(voiced)[0]
lead = idx[0] * 0.01 if len(idx) else 0
tail = (m - 1 - idx[-1]) * 0.01 if len(idx) else 0
print(f"  首静音 = {lead:.3f}s   尾静音 = {tail:.3f}s")

# 内部 gap
gaps = []
i = 0
while i < m:
    if not voiced[i]:
        j = i
        while j < m and not voiced[j]:
            j += 1
        if (j - i) * 0.01 >= 0.06:
            gaps.append((i * 0.01, j * 0.01, (j - i) * 0.01))
        i = j
    else:
        i += 1
print(f"  内部静音段(>=60ms): {len(gaps)} 处")
for a, b, d in gaps:
    print(f"     {a:6.2f} - {b:6.2f}s   ({d*1000:5.0f} ms)")

# 连续浊音段
segs = []
i = 0
while i < m:
    if voiced[i]:
        j = i
        while j < m and voiced[j]:
            j += 1
        if (j - i) * 0.01 >= 0.10:
            segs.append((i, j))
        i = j
    else:
        i += 1
print(f"  连续发声段(>=100ms): {len(segs)} 段")
sq = []
for a, b in segs:
    seg = x[a * w:b * w]
    r = float(np.sqrt(np.mean(seg ** 2)))
    pk = float(np.abs(seg).max())
    sq.append((a * 0.01, b * 0.01, r))
    print(f"     {a*0.01:6.2f} - {b*0.01:6.2f}s  dur={((b-a)*0.01):5.2f}s  rms={r:.4f}  pk={pk:.3f}")

# 电平分布（判断归一化必要性）
print()
print(f"  浊音段 RMS 中位 = {np.median([s[2] for s in sq]):.5f}  "
      f"=> {20*np.log10(np.median([s[2] for s in sq])):.1f} dBFS")
print(f"  整段 RMS = {np.sqrt(np.mean(x**2)):.5f} => {20*np.log10(np.sqrt(np.mean(x**2))):.1f} dBFS")
print(f"  TTS 训练数据典型电平 ≈ -20 dBFS (rms 0.1)  ⇒ 当前偏低约 "
      f"{-20 - 20*np.log10(np.sqrt(np.mean(x**2))):.1f} dB")

# ---------------------------------------------------------------- norm 版差异
print()
print("=" * 100)
print("【C】wechat_ref_norm.wav 与 raw 的关系")
print("=" * 100)
if os.path.exists(WN):
    y, sr2 = sf.read(WN, dtype="float32")
    print(f"  norm: sr={sr2} dur={len(y)/sr2:.3f}s peak={np.abs(y).max():.4f} "
          f"rms={np.sqrt(np.mean(y**2)):.5f}")
    n = min(len(x), len(y))
    if n > 0:
        a = x[:n] - x[:n].mean()
        b = y[:n] - y[:n].mean()
        den = np.sqrt((a**2).sum() * (b**2).sum())
        cos = float((a * b).sum() / den) if den > 0 else 0.0
        print(f"  与 raw 的波形余弦相似度 = {cos:.6f}  （≈1.000000 即纯增益，无波形改动）")
        print(f"  增益比 = {np.sqrt(np.mean(y**2))/np.sqrt(np.mean(x**2)):.3f}x "
              f"= {20*np.log10(np.sqrt(np.mean(y**2))/np.sqrt(np.mean(x**2))):+.1f} dB")
else:
    print("  MISSING")
print("=" * 100)
