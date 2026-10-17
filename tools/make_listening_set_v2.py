# -*- coding: utf-8 -*-
"""v2 试听集：等电平、定长、按顺序排列，供人耳裁决。

顺序（唯一变量逐步推进）：
  01 真源参考音        —— 基准（这是"清晰"的定义）
  02 线上旧版          —— 16k 派生参考音 + 未标定电平（你听到"糊"的那版）
  03 v2a 真源+电平标定 —— 只修工具链
  04 v2b 台词 9.5s     —— 换更长更连续的素材
  05 v2a + EQ1         —— 输出侧静态谱匹配（把 8-12k 补回来）
  06 v2a + EQ2(SBR)    —— 补 60% + 谐波折叠（高频跟着辅音走）
"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
HF = os.path.join(FS, "hf")
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
DST = os.path.join(FS, "聆听_v2")
SR = 24000
TARGET_RMS_DB = -20.0

ITEMS = [
    ("01_真源参考音(清亮度基准)", REF),
    ("02_线上旧版_16k派生参考音", os.path.join(FS, "A_FIXED.wav")),
    ("03_v2a_真源+电平标定", os.path.join(FS, "A_FIXED_v2a.wav")),
    ("04_v2b_台词9.5s_语域偏高", os.path.join(FS, "A_FIXED_v2b.wav")),
    ("05_高频补偿EQ1_全量", os.path.join(HF, "EQ1_v2a.wav")),
    ("06_高频补偿EQ3_温和70%", os.path.join(HF, "EQ3_v2a.wav")),
    ("07_高频补偿EQ2_温和+SBR", os.path.join(HF, "EQ2_v2a.wav")),
    ("08_EQ1加在线上旧版上", os.path.join(HF, "EQ1_A_FIXED.wav")),
]


def to_sr(x, sr, tgt=SR):
    if sr == tgt:
        return x
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", str(tgt), "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def trim(x, sr, thr_rel=0.02, pad=0.03):
    w = int(sr * 0.01)
    m = len(x) // w
    if m < 2:
        return x
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > max(1e-5, e.max() * thr_rel))[0]
    if not len(idx):
        return x
    a = max(0, int((idx[0] * 0.01 - pad) * sr))
    b = min(len(x), int(((idx[-1] + 1) * 0.01 + pad) * sr))
    return x[a:b]


def best_window(x, sr, want):
    if len(x) <= int(want * sr):
        return x
    w = int(sr * 0.05)
    m = len(x) // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    n = int(want * sr)
    step = int(sr * 0.25)
    best, bi = -1.0, 0
    for a in range(0, len(x) - n + 1, step):
        s = e[a // w:(a + n) // w]
        v = float(s.mean()) - float(s.std()) * 0.5
        if v > best:
            best, bi = v, a
    return x[bi:bi + n]


def norm(x, sr, target_db=TARGET_RMS_DB):
    r = float(np.sqrt((x ** 2).mean()))
    if r < 1e-9:
        return x
    g = 10 ** ((target_db - 20 * np.log10(r)) / 20.0)
    y = x * g
    pk = float(np.abs(y).max())
    if pk > 0.97:
        y = y / pk * 0.97
    return y


def main():
    os.makedirs(DST, exist_ok=True)
    seq = []
    files = []
    for label, p in ITEMS:
        if not os.path.exists(p):
            print("  缺失 %s" % p); continue
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        x = to_sr(x, sr)
        x = best_window(trim(x, SR), SR, 12.0)
        x = norm(x, SR)
        # 首尾加 0.15s 静音，避免接缝
        x = np.concatenate([np.zeros(int(SR * 0.15), np.float32), x,
                            np.zeros(int(SR * 0.15), np.float32)])
        out = os.path.join(DST, label + ".wav")
        sf.write(out, x, SR, subtype="PCM_16")
        files.append(out)
        seq.append(x)
        seq.append(np.zeros(int(SR * 1.2), np.float32))
        print("  [%s] %.2fs  rms=%.1f dBFS" % (label, len(x) / SR,
              20 * np.log10(np.sqrt((x ** 2).mean()))))
    if seq:
        p = os.path.join(DST, "试听_顺序_01至07.wav")
        sf.write(p, np.concatenate(seq[:-1]), SR, subtype="PCM_16")
        print("\n  顺序试听文件: %s  (%.1fs)" % (p, os.path.getsize(p) / 2 / SR))
        print("  %d 个单段文件在 %s" % (len(files), DST))
    print()
    print("  判读顺序：先记住 01 的'清亮度与气声'，再看 02→03 是否变化很小（说明工具链不是主因），")
    print("            最后 05/06 是否把 8k 以上的'空气感/齿音'补回来。")


if __name__ == "__main__":
    main()
