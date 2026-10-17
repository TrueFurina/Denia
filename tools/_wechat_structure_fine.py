# -*- coding: utf-8 -*-
"""微信源细粒度结构检查：判断"碎"是真实停顿，还是掉样/拼接痕迹。"""
import os
import numpy as np
import soundfile as sf

W = r"H:\daniya_voice_tools\wechat_ref.wav"
x, sr = sf.read(W, dtype="float32")
N = len(x)
print("=" * 100)
print("微信源细粒度结构检查")
print("=" * 100)
print(f"  sr={sr}  n={N}  dur={N/sr:.3f}s  DC偏置={x.mean():+.2e}  peak={np.abs(x).max():.4f}")

# 1. 精确零值（掉样/数字静音痕迹）
z = np.abs(x) < 1e-7
print(f"\n[1] 近似零值样本: {z.sum()} ({z.mean()*100:.4f}%)")
if z.sum():
    runs = []
    i = 0
    while i < N:
        if z[i]:
            j = i
            while j < N and z[j]:
                j += 1
            runs.append((i / sr, (j - i) / sr))
            i = j
        else:
            i += 1
    print(f"    零值游程 {len(runs)} 段，最长 {max(r[1] for r in runs)*1000:.1f}ms")

# 2. 样点间突变（爆音/拼接点）
d = np.abs(np.diff(x))
jump = d > 0.25
print(f"\n[2] 相邻样点跳变 >0.25 的次数: {jump.sum()}  (自然语音极少 >5)")
if jump.sum():
    idx = np.where(jump)[0]
    for i in idx[:20]:
        print(f"     t={i/sr:.4f}s  jump={d[i]:.3f}  x={x[i]:+.3f} -> {x[i+1]:+.3f}")
    if len(idx) > 20:
        print(f"     ... 共 {len(idx)} 处")

# 3. 5ms 分辨率能量轮廓（看停顿是"渐落"还是"齐切"）
print("\n[3] 5ms 分辨率能量轮廓（dBFS，每行 40 帧 = 200ms）")
w = int(sr * 0.005)
m = N // w
e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
db = 20 * np.log10(np.maximum(e, 1e-9))
for i in range(0, m, 40):
    row = db[i:i + 40]
    bar = "".join(" .:-=+*#@"[min(8, max(0, int((v + 60) / 6)))] for v in row)
    print(f"  {i*5/1000:6.3f}s |{bar}|")

# 4. 静音前后的衰减斜率：真实停顿应平滑下落
print("\n[4] 长静音段前后的电平过渡")
thr = e.max() * 0.02
voiced = e > thr
i = 0
gaps = []
while i < m:
    if not voiced[i]:
        j = i
        while j < m and not voiced[j]:
            j += 1
        if (j - i) * 0.005 >= 0.15:
            gaps.append((i, j))
        i = j
    else:
        i += 1
for a, b in gaps:
    pre = db[max(0, a - 6):a]
    post = db[b:min(m, b + 6)]
    print(f"     gap {a*0.005:6.2f}-{b*0.005:6.2f}s ({(b-a)*0.005*1000:5.0f}ms)  "
          f"前 30ms 均值={pre.mean():6.1f}dB  后 30ms 均值={post.mean() if len(post) else float('nan'):6.1f}dB  "
          f"gap 内均值={db[a:b].mean():6.1f}dB")

print()
print("=" * 100)
print("判读：若 gap 内均值明显高于 -80dB（即不是数字静音），说明是真实录音底噪/房间声，")
print("      属正常停顿；若相邻样点跳变很多，则说明音频被拼接/重编码过。")
print("=" * 100)
