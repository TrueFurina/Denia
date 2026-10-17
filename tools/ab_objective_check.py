# -*- coding: utf-8 -*-
"""三方产出的客观对照 + 快速试听切片。

客观指标（都对齐参考音 daniya_ref_wechat_16k.wav）：
  - campplus 说话人余弦  → 音色像不像
  - F0 中位              → 音高对不对（参考 238.8Hz；偏离=音色失真）
  - >4k 能量占比         → 亮不亮（糊不糊）
  - RMS / 首尾静音        → 响度与切齐
试听切片：每方取前 14s，另出"等电平"版本（都归到 -20dBFS，保证响度不干扰判断）。
"""
import hashlib
import os
import numpy as np
import soundfile as sf

D = r"G:\ai对话\11\data\native_audio_test\verify_wechat"
REF = r"G:\ai对话\11\data\native_audio_test\ref_wechat_final\daniya_ref_wechat_16k.wav"
ARMS = [("S1_new", "微信纯源精修（本次烘焙）"),
        ("S2_hist", "历史长期在线 247.82MB"),
        ("S3_rec", "录屏衍生 332MB")]
CACHE = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf\prompt_cache.gguf"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def to16k(x, sr):
    import subprocess
    FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
    if sr == 16000:
        return x
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", "16000", "-ac", "1", "-"],
                       input=x.astype("<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def f0_median(x, sr, fmin=70, fmax=500, frame=1024, hop=256, thr=0.01):
    x = x - x.mean()
    out = []
    for i in range(max(0, (len(x) - frame) // hop)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < thr:
            continue
        ac = np.correlate(s, s, "full")[frame - 1:]
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


def band4k(x, sr):
    n = min(len(x), sr * 10)
    s = x[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(s))
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = S.sum() + 1e-12
    return float(S[f > 4000].sum() / tot * 100)


def edges(x, sr, thr=0.005):
    w = int(sr * 0.01); m = len(x) // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > max(thr, e.max() * 0.03))[0]
    return (idx[0] * 0.01, (m - 1 - idx[-1]) * 0.01) if len(idx) else (0, 0)


def main():
    print("=" * 112)
    print("live cache 状态（A/B 结束后应已还原为本次微信版）")
    print("=" * 112)
    print("  md5=%s  %d B" % (md5(CACHE)[:16], os.path.getsize(CACHE)))
    print("  期望 = e6bbe6c37205966c... 242251232 B")
    print()

    import onnxruntime
    import torch
    import torchaudio
    import torchaudio.compliance.kaldi as kaldi
    sess = onnxruntime.InferenceSession(r"H:\daniya_voice_tools\campplus.onnx",
                                        providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    def spk(x16):
        t = torch.from_numpy(x16).float().unsqueeze(0)
        fb = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
        fb = fb - fb.mean(dim=0, keepdim=True)
        e = sess.run(None, {iname: fb.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
        return e / (np.linalg.norm(e) + 1e-9)

    rx, rsr = sf.read(REF, dtype="float32")
    if rx.ndim > 1:
        rx = rx.mean(axis=1)
    ref_e = spk(to16k(rx, rsr))

    print("=" * 112)
    print("参考音（基准）")
    print("=" * 112)
    print("  daniya_ref_wechat_16k.wav  %.2fs  F0=%.1fHz  >4k=%.2f%%  rms=%.4f"
          % (len(rx) / rsr, f0_median(rx, rsr), band4k(rx, rsr), float(np.sqrt(np.mean(rx ** 2)))))
    print()
    print("=" * 112)
    print("三方产出客观对照")
    print("=" * 112)
    print(f"{'臂':<10}{'时长':>8}{'F0':>8}{'ΔF0':>8}{'>4k%':>8}{'rms':>9}{'dBFS':>8}"
          f"{'首静':>7}{'尾静':>7}{'与参考余弦':>11}")
    print("-" * 112)

    rows = []
    for tag, desc in ARMS:
        p = os.path.join(D, tag + ".wav")
        if not os.path.exists(p):
            print(f"{tag:<10} MISSING"); continue
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        f0 = f0_median(x, sr)
        a, b = edges(x, sr)
        r = float(np.sqrt(np.mean(x ** 2)))
        cos = float(ref_e @ spk(to16k(x, sr)))
        print(f"{tag:<10}{len(x)/sr:>8.2f}{f0:>8.1f}{f0-238.8:>+8.1f}{band4k(x, sr):>8.2f}"
              f"{r:>9.4f}{20*np.log10(max(r,1e-9)):>8.1f}{a:>7.3f}{b:>7.3f}{cos:>11.4f}")
        rows.append((tag, desc, x, sr))

    print()
    print("判读要点：")
    print("  · ΔF0 越接近 0 越好（参考 238.8Hz）；|ΔF0|>15Hz 基本可判定音色被扭曲")
    print("  · >4k 越接近参考 28.70% 越好；明显偏低=发闷（即用户说的'糊'）")
    print("  · 与参考余弦越接近 1 越好（同一说话人）")
    print()

    # ---- 快速试听切片 ----
    SEC = 14.0
    raw, eq = [], []
    for tag, desc, x, sr in rows:
        n = min(len(x), int(sr * SEC))
        seg = x[:n]
        raw.append(seg)
        y = seg / (np.sqrt(np.mean(seg ** 2)) + 1e-12) * 0.1
        pk = np.abs(y).max()
        if pk > 0.95:
            y *= 0.95 / pk
        eq.append(y.astype(np.float32))
    gap = np.zeros(int(24000 * 1.2), dtype=np.float32)

    for name, arr in [("AB_快听_原电平", raw), ("AB_快听_等电平", eq)]:
        seq = []
        for i, a in enumerate(arr):
            if i:
                seq.append(gap)
            seq.append(a)
        out = os.path.join(D, name + ".wav")
        sf.write(out, np.concatenate(seq), 24000)
        print("写出 %s  (%.1fs)" % (out, sum(len(a) for a in arr) / 24000 + 1.2 * (len(arr) - 1)))

    print()
    print("播放顺序：S1 微信纯源精修（新）→ S2 历史长期在线（你一直在听的）→ S3 录屏衍生")
    print("目录: %s" % D)


if __name__ == "__main__":
    main()
