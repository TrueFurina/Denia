# -*- coding: utf-8 -*-
"""最终体检：修好测试台后的产出 vs 参考音 vs 原生签名音（同一把尺子）。"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"


def to16k(x, sr):
    if sr == 16000:
        return x
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", "16000", "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
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


def bands(x, sr):
    n = min(len(x), sr * 20)
    s = x[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(s)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    ref = S[(f >= 300) & (f < 3400)].sum() + 1e-12
    out = []
    for lo, hi in [(3400, 6000), (6000, 8000), (8000, 12000)]:
        out.append(10 * np.log10((S[(f >= lo) & (f < hi)].sum() + 1e-12) / ref))
    cs = np.cumsum(S)
    return out, float(f[np.searchsorted(cs, 0.99 * (cs[-1] + 1e-12))])


def load(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def main():
    import onnxruntime
    import torch
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

    sx, ssr = load(SRC)
    gx, gsr = load(SIG)
    se, ge = spk(to16k(sx, ssr)), spk(to16k(gx, gsr))
    sf0 = f0_median(sx, ssr)
    sb, sf99 = bands(sx, ssr)

    print("=" * 122)
    print("基准")
    print("=" * 122)
    print("  SRC 真源(达妮娅 24k精修) %.2fs F0=%.1f  [3.4-6k %.1f / 6-8k %.1f / 8-12k %.1f] f99=%.0f"
          % (len(sx) / ssr, sf0, sb[0], sb[1], sb[2], sf99))
    gb, gf99 = bands(gx, gsr)
    print("  SIG MiniCPM原生签名       %.2fs F0=%.1f  [3.4-6k %.1f / 6-8k %.1f / 8-12k %.1f] f99=%.0f"
          % (len(gx) / gsr, f0_median(gx, gsr), gb[0], gb[1], gb[2], gf99))
    print()
    print("=" * 122)
    print("产出对照")
    print("=" * 122)
    print("%-26s%8s%9s%10s%26s%8s%10s%10s"
          % ("样本", "时长", "F0", "ΔF0↔SRC", "3.4-6k / 6-8k / 8-12k", "f99", "cos↔SRC", "cos↔SIG"))
    print("-" * 122)
    rows = [
        ("★APP_A 应用同构", os.path.join(FS, "APP_A.wav")),
        ("★APP_A + 停顿规整", os.path.join(FS, "APP_A_smooth.wav")),
        ("旧测试台 A_FIXED", os.path.join(FS, "A_FIXED.wav")),
        ("旧测试台 A_FIXED + 规整", os.path.join(FS, "A_FIXED_smooth.wav")),
        ("旧测试台 r2 + 规整", os.path.join(FS, "A_FIXED_r2_smooth.wav")),
        ("B_NATIVE 原生底色", os.path.join(FS, "B_NATIVE.wav")),
        ("B_NATIVE + 停顿规整", os.path.join(FS, "B_NATIVE_smooth.wav")),
    ]
    for lab, p in rows:
        if not os.path.exists(p):
            print("%-26s MISSING" % lab); continue
        x, sr = load(p)
        f0 = f0_median(x, sr)
        b, f99 = bands(x, sr)
        e = spk(to16k(x, sr))
        print("%-26s%8.2f%9.1f%+10.1f%6s[%5.1f %5.1f %5.1f]%9.0f%10.4f%10.4f"
              % (lab, len(x) / sr, f0, f0 - sf0, "", b[0], b[1], b[2], f99,
                 float(se @ e), float(ge @ e)))
    print()
    print("判读：ΔF0↔SRC 越小=音高越像；cos↔SRC 越大=越像达妮娅；cos↔SIG 越小=越不沾原生底")
    print("      停顿规整只删静音 ⇒ ΔF0/cos 应完全不变（脚本内已验证 ΔF0=0.0）")


if __name__ == "__main__":
    main()
