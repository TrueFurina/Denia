# -*- coding: utf-8 -*-
"""上限裁决听测包：用耳朵直接裁"8k 条件带宽本身值多少、EQ 补回了多少"。

设计（2×3，内容各自匹配）：
  真源链条（同一段录音，只改 >8k 处理）：
    01 真源 24k                       —— 绝对上限
    02 真源 限带 8k                   —— ★"完美引擎"上限（0-8k 完美，>8k 为空）
    03 真源 限带 8k + EQ1             —— 我们补偿能夺回的
  克隆链条（同一段产出）：
    04 克隆 APP_A（未补偿）
    05 克隆 APP_A + EQ1
  06 顺序连听 01→05

判读：
  · 若 01 与 02 差别不大 ⇒ 8k 条件带宽不是主要敌人（"完美引擎"听感已可接受）
    那么克隆与真源的差距就全在 0-8k 之内 ⇒ 该往中高频/Q 值挖，而不是继续抬 >8k
  · 若 02 明显发闷 ⇒ 带宽本身是硬损失，EQ/VC 是唯一出路
"""
import os
import numpy as np
import soundfile as sf

P = r"G:\ai对话\11"
FS = os.path.join(P, "data", "native_audio_test", "final_sample")
CD = os.path.join(FS, "ceiling")
SRC = os.path.join(P, "data", "native_audio_test", "ref_v2", "mp3_8s_24k.wav")
OUT = os.path.join(FS, "聆听_v6_上限裁决")
os.makedirs(OUT, exist_ok=True)
TARGET_RMS_DB = -20.0


def load(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x.astype(np.float32), sr


def norm(x, sr, tgt_db=TARGET_RMS_DB):
    """按"有声帧 RMS"归一，避免把静音段一起算进去。"""
    frame = int(0.03 * sr)
    hop = int(0.01 * sr)
    rms = []
    for i in range(0, max(1, len(x) - frame), hop):
        s = x[i:i + frame]
        rms.append(np.sqrt((s ** 2).mean() + 1e-20))
    rms = np.array(rms)
    pk = np.abs(x).max() + 1e-12
    act = rms[rms > pk * 0.006]          # 相对峰值的 -44dB
    cur = np.sqrt((act ** 2).mean()) if len(act) > 4 else np.sqrt((x ** 2).mean() + 1e-20)
    g = 10 ** ((tgt_db - 20 * np.log10(cur + 1e-12)) / 20.0)
    y = x * g
    m = np.abs(y).max()
    if m > 0.97:
        y = y / m * 0.97
    return y.astype(np.float32)


def main():
    items = [
        ("01_真源_达妮娅本人_24k全频带.wav", SRC),
        ("02_真源_限带8k_完美引擎上限.wav", os.path.join(CD, "L2_perfect_engine_bandlimit8k.wav")),
        ("03_真源_限带8k+EQ1_补偿夺回.wav", os.path.join(CD, "L3_perfect_engine_plus_EQ1.wav")),
        ("04_克隆_未补偿_实际产出.wav", os.path.join(FS, "APP_A.wav")),
        ("05_克隆+EQ1_实际+补偿.wav", os.path.join(CD, "R2_clone_plus_EQ1.wav")),
    ]
    segs = []
    print("=" * 104)
    print("%-42s%10s%12s%12s" % ("文件", "时长", "有声RMS", "峰值"))
    print("-" * 104)
    for name, p in items:
        if not os.path.exists(p):
            print("%-42s MISSING" % name); continue
        x, sr = load(p)
        y = norm(x, sr)
        sf.write(os.path.join(OUT, name), y, sr, subtype="PCM_16")
        rms = np.sqrt((y ** 2).mean() + 1e-20)
        print("%-42s%9.2fs%11.1f dB%11.3f" % (name, len(y) / sr, 20 * np.log10(rms), np.abs(y).max()))
        segs.append(y)

    gap = np.zeros(int(0.75 * 24000), np.float32)
    seq = []
    for i, s in enumerate(segs):
        seq.append(s)
        if i != len(segs) - 1:
            seq.append(gap)
    seq = np.concatenate(seq)
    sp = os.path.join(OUT, "06_顺序_01到05连听.wav")
    sf.write(sp, seq, 24000, subtype="PCM_16")
    print("-" * 104)
    print("顺序连听  %s  (%.1fs)" % (sp, len(seq) / 24000))
    print()
    print("产出目录：%s" % OUT)
    for f in sorted(os.listdir(OUT)):
        print("   %-46s %8.1f KB" % (f, os.path.getsize(os.path.join(OUT, f)) / 1024))


if __name__ == "__main__":
    main()
