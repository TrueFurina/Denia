# -*- coding: utf-8 -*-
"""【达妮娅音色克隆 · 步骤 1/6】生成最终参考音。

规范（依据 llama.cpp-omni / Comni 声码器烘焙的硬性要求）：
  - 单声道 wav
  - 时长 6~10s
  - 首尾无静音
  - 无削波（peak < 1.0）
  - bundle 的 default_ref_audio.wav 必须 24kHz；48k 会静默失败
  - 送 extract_prompt_bundle 的源用 48kHz（内部自行重采样 16k/24k）

选材依据（客观指标，2026-09-21 实测）：
  C2_calm_natural_48k.wav  9.72s | F0 246.2Hz | >4k 41.90% | >8k 27.16% | peak 0.700 | 首/尾静音 0.04/0.12s
  本人全片 F0 基准 = 265.9Hz（V0827 录屏 27s，2132 帧）≈ ww.wav 262.3Hz
  对比：旧 live 参考 16k >8k=0.00%（结构性闷）；09-21 重建版 F0=342.9Hz（偏高 29%，尖）

产出（严格同源，仅重采样差异）：
  data/native_audio_test/ref_final/daniya_ref_final_48k.wav   ← 送 extract 生成 bundle bin
  data/native_audio_test/ref_final/daniya_ref_final_24k.wav   ← 覆盖 bundle/default_ref_audio.wav
"""
import os
import subprocess
import sys
import numpy as np
import soundfile as sf

PY_VENV = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
RESAMPLE = r"H:\daniya_voice_tools\_resample_wav.py"

SRC = r"G:\ai对话\11\data\native_audio_test\ref_candidates\C2_calm_natural_48k.wav"
OUT_DIR = r"G:\ai对话\11\data\native_audio_test\ref_final"
OUT48 = os.path.join(OUT_DIR, "daniya_ref_final_48k.wav")
OUT24 = os.path.join(OUT_DIR, "daniya_ref_final_24k.wav")

EDGE_THR = 0.005      # 首尾静音判定阈值（绝对值，保守：不裁掉弱起音）
MARGIN_S = 0.030      # 前后保留余量
F0_REF = 265.9        # 本人全片 F0 基准


def f0_median(x, sr, fmin=80, fmax=500, frame=1024, hop=256):
    x = x - np.mean(x)
    n = (len(x) - frame) // hop
    out = []
    for i in range(max(0, n)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < 0.02:
            continue
        ac = np.correlate(s, s, mode="full")[frame - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lo, hi = int(sr / fmax), min(int(sr / fmin), len(ac) - 1)
        if hi <= lo:
            continue
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] < 0.3:
            continue
        out.append(sr / k)
    return float(np.median(out)) if out else 0.0


def report(path, label):
    i = sf.info(path)
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = X.sum() + 1e-12
    w = max(1, int(sr * 0.01))
    m = n // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > EDGE_THR)[0]
    lead = idx[0] * 0.01 if len(idx) else i.duration
    tail = (m - 1 - idx[-1]) * 0.01 if len(idx) else i.duration
    print(f"  {label:26s} {i.duration:6.2f}s sr={sr:6d} ch={i.channels} "
          f">4k={X[f>4000].sum()/tot*100:5.2f}% >8k={X[f>8000].sum()/tot*100:5.2f}% "
          f"rms={float(np.sqrt(np.mean(x**2))):.4f} pk={float(np.max(np.abs(x))):.3f} "
          f"F0={f0_median(x, sr):6.1f}Hz 首静={lead:.2f}s 尾静={tail:.2f}s")


def main():
    print("=" * 104)
    print("【步骤 1/6】生成最终参考音")
    print("=" * 104)
    if not os.path.exists(SRC):
        print("!! 源文件缺失:", SRC)
        sys.exit(1)

    x, sr = sf.read(SRC, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    print(f"源: {SRC}")
    report(SRC, "源(C2_calm)")

    # 1. 裁首尾静音
    w = max(1, int(sr * 0.01))
    m = len(x) // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > EDGE_THR)[0]
    if len(idx) == 0:
        print("!! 整段低于阈值，放弃裁剪")
        y = x
    else:
        a = max(0, int((idx[0] * 0.01 - MARGIN_S) * sr))
        b = min(len(x), int(((idx[-1] + 1) * 0.01 + MARGIN_S) * sr))
        y = x[a:b]
        print(f"\n裁剪: 原 {len(x)/sr:.2f}s -> {len(y)/sr:.2f}s "
              f"(去掉前 {a/sr:.3f}s / 后 {(len(x)-b)/sr:.3f}s)")

    # 2. 硬性校验（不满足则中止，避免烤出废 cache）
    dur = len(y) / sr
    pk = float(np.max(np.abs(y)))
    errs = []
    if not (6.0 <= dur <= 10.0):
        errs.append(f"时长 {dur:.2f}s 不在 6~10s")
    if pk >= 0.999:
        errs.append(f"peak {pk:.3f} 削波")
    if errs:
        print("!! 校验未通过: " + "; ".join(errs))
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    sf.write(OUT48, y, sr, subtype="PCM_16")
    print(f"\n写出 48k: {OUT48}")

    # 3. 严格同源重采样到 24k
    r = subprocess.run([PY_VENV, RESAMPLE, OUT48, OUT24, "24000"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("  " + (r.stdout or "").strip())
    if r.returncode != 0 or not os.path.exists(OUT24):
        print("!! 重采样失败:", (r.stderr or "")[-300:])
        sys.exit(1)

    # 4. 验证两版
    print()
    print("=" * 104)
    print("双规格校验（两者除采样率外应完全同源）")
    print("=" * 104)
    report(OUT48, "最终 48k")
    report(OUT24, "最终 24k（给bundle）")

    x24, sr24 = sf.read(OUT24, dtype="float32")
    if x24.ndim > 1:
        x24 = x24.mean(axis=1)
    f0_24 = f0_median(x24, sr24)
    x48, sr48 = sf.read(OUT48, dtype="float32")
    if x48.ndim > 1:
        x48 = x48.mean(axis=1)
    f0_48 = f0_median(x48, sr48)

    print()
    print(f"F0 偏差: 48k={f0_48:.1f}Hz / 24k={f0_24:.1f}Hz  vs 本人全片基准 {F0_REF}Hz "
          f"→ {(f0_24/F0_REF-1)*100:+.1f}%")
    print(f"24k 文件中 >12k 恒为 0% 属正常（Nyquist=12k），非缺陷。")
    print()
    print("PASS 参考音就绪。")
    print(f"  48K = {OUT48}")
    print(f"  24K = {OUT24}")


if __name__ == "__main__":
    main()
