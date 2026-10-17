# -*- coding: utf-8 -*-
"""生成最终试听集。

三点要求：
  1. 先给"目标"——达妮娅本人参考音（判断基准），再给修复后、修复前。
  2. 全部归一到同一响度（-20 dBFS RMS），否则"更响"会被误听成"更好"。
  3. 每段前留 0.4s 静音、段间 1.5s，便于逐段对比。
"""
import os
import subprocess

import numpy as np
import soundfile as sf

D = r"G:\ai对话\11\data\native_audio_test\final_sample"
R1 = os.path.join(D, "run1")
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
OUT = r"G:\ai对话\11\data\native_audio_test\final_sample\聆听"
SR = 24000


def to_sr(x, sr, target=SR):
    if sr == target:
        return x
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", str(target), "-ac", "1", "-"],
                       input=x.astype("<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def trim(x, sr, thr_db=-45.0):
    """裁掉首尾静音（按 20ms 帧 RMS）。"""
    w = int(sr * 0.02)
    m = len(x) // w
    if m < 2:
        return x
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    ref = 20 * np.log10(max(e.max(), 1e-9))
    idx = np.where(20 * np.log10(np.maximum(e, 1e-9)) > ref + thr_db)[0]
    return x[idx[0] * w:(idx[-1] + 1) * w] if len(idx) else x


def best_window(x, sr, sec=12.0):
    """取语音最密集的 sec 秒窗口（按 0.5s 块 RMS 滑动）。"""
    need = int(sr * sec)
    if len(x) <= need:
        return x
    b = int(sr * 0.5)
    m = len(x) // b
    e = np.sqrt(np.mean(x[:m * b].reshape(m, b) ** 2, axis=1))
    k = max(1, int(sec / 0.5))
    # 用能量 "有声中位" 打分：既能过滤气泡音，也避免选到纯静音
    sc = np.array([np.median(np.sort(e[i:i + k])[max(0, k // 3):]) for i in range(m - k + 1)])
    j = int(np.argmax(sc))
    return x[j * b:j * b + need]


def norm(x, sr, dbfs=-20.0):
    r = float(np.sqrt(np.mean(x ** 2))) or 1e-9
    y = x * (10 ** (dbfs / 20.0) / r)
    pk = float(np.max(np.abs(y)))
    if pk > 0.99:
        y = y * (0.99 / pk)
    return y.astype(np.float32)


def main():
    os.makedirs(OUT, exist_ok=True)
    items = []

    x, sr = sf.read(REF, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    items.append(("01_目标参考音_达妮娅本人", norm(trim(to_sr(x, sr), SR), SR)))

    for tag, label in [("A_FIXED", "02_修复后_个性化音色"),
                       ("B_NATIVE", "03_修复前_原生底色"),
                       ("A_FIXED_r2", "04_修复后_第二次(复现)")]:
        p = os.path.join(R1, tag + ".wav")
        if not os.path.exists(p):
            p = os.path.join(D, tag + ".wav")
        if not os.path.exists(p):
            print("  缺失 %s" % p); continue
        y, s = sf.read(p, dtype="float32")
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = to_sr(y, s)
        y = best_window(trim(y, SR), SR, 12.0)
        items.append((label, norm(y, SR)))

    gap = np.zeros(int(SR * 1.5), dtype=np.float32)
    lead = np.zeros(int(SR * 0.4), dtype=np.float32)

    print("=" * 96)
    print("单段文件")
    print("=" * 96)
    for name, y in items:
        p = os.path.join(OUT, name + ".wav")
        sf.write(p, y, SR)
        r = float(np.sqrt(np.mean(y ** 2)))
        print("  %-30s %6.2fs  rms=%.4f (%.1f dBFS)  peak=%.3f   -> %s"
              % (name, len(y) / SR, r, 20 * np.log10(max(r, 1e-9)), float(np.max(np.abs(y))), p))

    seq = []
    for i, (_, y) in enumerate(items):
        if i:
            seq.append(gap)
        else:
            seq.append(lead)
        seq.append(y)
    out = os.path.join(OUT, "试听_参考音_修复后_修复前.wav")
    sf.write(out, np.concatenate(seq), SR)
    print()
    print("=" * 96)
    print("连续试听（顺序：目标参考音 → 修复后 → 修复前；段间 1.5s 静音）")
    print("=" * 96)
    print("  %s   总长 %.2fs" % (out, sum(len(a) for a in seq) / SR))
    print()
    print("判读方法：先记住第 1 段（达妮娅本人）的质感/音高/亮度，")
    print("          然后听第 2 段是否在同一个人上；第 3 段应能听出更亮更高、偏向另一人。")


if __name__ == "__main__":
    main()
