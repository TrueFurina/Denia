# -*- coding: utf-8 -*-
"""8kHz 归属检验：输出的 8-16kHz 到底是"达妮娅的"还是"模型自己长的"。

为什么盯 8k：
  · 参考音进模型前被 librosa.load(sr=16000) 无条件降到 16k  → 模型听不到 8k 以上
  · 声码器条件带宽实测 fmax=8000                          → 条件里也没有 8k 以上
  => 若判断成立：输出的 >8k 应当与"缓存/参考音"几乎无关，
     两个在各频段身份完全不同的输出（① vs ④），其 >8k 频谱应当高度相似。

判读：
  · LTAS 8-12k 两两相关 >0.9 且与缓存无关 -> >8k 由模型先验独占 = 固有
  · LTAS 8-12k 随缓存变化                 -> >8k 仍受条件控制 = 可改
"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SR = 24000


def to_sr(x, a, b):
    if a == b:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(a), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", str(b), "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def load24(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return to_sr(x, sr, SR)


def ltas(x, nfft=2048, hop=512):
    """长时平均幅度谱（dB），只统计有声帧"""
    if len(x) < nfft:
        return None, None
    acc = np.zeros(nfft // 2 + 1)
    n = 0
    for i in range(0, len(x) - nfft, hop):
        s = x[i:i + nfft] * np.hanning(nfft)
        if np.sqrt(np.mean(s ** 2)) < 0.005:
            continue
        acc += np.abs(np.fft.rfft(s))
        n += 1
    if n == 0:
        return None, None
    f = np.fft.rfftfreq(nfft, 1 / SR)
    return f, 20 * np.log10(acc / n + 1e-9)


def band_db(f, L, lo, hi):
    m = (f >= lo) & (f < hi)
    return float(L[m].mean()) if m.any() else -99.0


def main():
    files = [
        ("达妮娅真源(真人24k)", SRC),
        ("① 达妮娅缓存+传参考音", os.path.join(FS, "APP_A.wav")),
        ("② 达妮娅缓存+不传参考音", os.path.join(FS, "ABL_daniacache_noref.wav")),
        ("③ 原生缓存+传参考音", os.path.join(FS, "ABL_stock_ourref.wav")),
        ("④ 原生缓存+不传参考音", os.path.join(FS, "ABL_stock_noref.wav")),
        ("B_NATIVE 旧对照", os.path.join(FS, "B_NATIVE.wav")),
    ]
    data = {}
    print("=" * 112)
    print("各样本的频段剖影（相对本样本 300-3400Hz 的 dB）")
    print("=" * 112)
    print("%-26s%10s%10s%10s%12s" % ("样本", "3.4-6k", "6-8k", "8-12k", "8k以上占比"))
    print("-" * 112)
    for lab, p in files:
        if not os.path.exists(p):
            print("%-26s MISSING" % lab)
            continue
        x = load24(p)
        f, L = ltas(x)
        ref = band_db(f, L, 300, 3400)
        b1 = band_db(f, L, 3400, 6000) - ref
        b2 = band_db(f, L, 6000, 8000) - ref
        b3 = band_db(f, L, 8000, 12000) - ref
        S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        ff = np.fft.rfftfreq(len(x), 1 / SR)
        tot = S.sum() + 1e-12
        hf = float(S[ff >= 8000].sum() / tot * 100)
        data[lab] = (f, L)
        print("%-26s%10.1f%10.1f%10.1f%11.2f%%" % (lab, b1, b2, b3, hf))

    print()
    print("=" * 112)
    print("8-12kHz 谱形两两相关（1.00 = 完全同一种高频质地）")
    print("=" * 112)
    g = np.linspace(8000, 12000, 160)
    keys = [k for k in data]
    curves = {}
    for k in keys:
        f, L = data[k]
        c = np.interp(g, f, L)
        curves[k] = c - c.mean()
    lab0 = keys[0]
    print("%-26s" % "" + "".join("%12s" % k.split(" ")[0][:10] for k in keys))
    for a in keys:
        row = "%-26s" % a[:24]
        for b in keys:
            den = np.linalg.norm(curves[a]) * np.linalg.norm(curves[b])
            row += "%12.3f" % (float(curves[a] @ curves[b] / den) if den > 0 else 0.0)
        print(row)
    print()
    print("判读：若 ①②③④ 之间的 8-12k 相关都 >0.9（而它们在 0-8k 身份上差 0.6 个余弦），")
    print("      说明 8kHz 以上与缓存/参考音无关 —— 完全由模型先验生成 = 模型固有。")


if __name__ == "__main__":
    main()
