# -*- coding: utf-8 -*-
"""修复后试听样本的客观体检。
不进闸门：一次性研究脚本——基准文件全在开发机 G:/H: 盘，换机即废；口径留档，
判据未进自动闸门（2026-10-22 普查定性，见 tools/check_gate_registry.py）。

口径与 ab_objective_check.py 完全一致（同一 f0_median / band4k / campplus 说话人余弦），
以保证数字可与历史 A/B 直接比较。

两个基准：
  REF = 达妮娅参考音（目标音色）
  SIG = ref_minicpm_signature.wav（MiniCPM 原生签名音，即修复前 LLM 层实际用的）

判读：
  A_FIXED  应显著靠近 REF、远离 SIG
  B_NATIVE 应显著靠近 SIG（证明'不传参考音就回落原生'这条因果成立）
"""
import os
import numpy as np
import soundfile as sf

D = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"


def to16k(x, sr):
    import subprocess
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
    if m < 2:
        return (0.0, 0.0)
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > max(thr, e.max() * 0.03))[0]
    return (idx[0] * 0.01, (m - 1 - idx[-1]) * 0.01) if len(idx) else (0.0, 0.0)


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

    rx, rsr = load(REF)
    sx, ssr = load(SIG)
    ref_e = spk(to16k(rx, rsr))
    sig_e = spk(to16k(sx, ssr))

    ref_f0 = f0_median(rx, rsr)
    sig_f0 = f0_median(sx, ssr)

    print("=" * 108)
    print("两个基准")
    print("=" * 108)
    print("  REF 达妮娅参考音            %.3fs  F0=%6.1fHz  >4k=%5.2f%%  rms=%.4f  (%.1f dBFS)"
          % (len(rx) / rsr, ref_f0, band4k(rx, rsr), float(np.sqrt(np.mean(rx ** 2))),
             20 * np.log10(max(np.sqrt(np.mean(rx ** 2)), 1e-9))))
    print("  SIG MiniCPM 原生签名音      %.3fs  F0=%6.1fHz  >4k=%5.2f%%  rms=%.4f  (%.1f dBFS)"
          % (len(sx) / ssr, sig_f0, band4k(sx, ssr), float(np.sqrt(np.mean(sx ** 2))),
             20 * np.log10(max(np.sqrt(np.mean(sx ** 2)), 1e-9))))
    print("  原生 - 达妮娅 ΔF0 = %+.1f Hz" % (sig_f0 - ref_f0))
    print()

    print("=" * 108)
    print("产出对照")
    print("=" * 108)
    print(f"{'臂':<12}{'时长':>8}{'F0':>9}{'ΔF0↔REF':>10}{'>4k%':>8}{'dBFS':>8}"
          f"{'首静':>7}{'尾静':>7}{'cos↔REF':>10}{'cos↔SIG':>10}")
    print("-" * 108)
    for tag in ("A_FIXED", "A_FIXED_r2", "B_NATIVE"):
        p = os.path.join(D, tag + ".wav")
        if not os.path.exists(p):
            print(f"{tag:<12} MISSING")
            continue
        x, sr = load(p)
        f0 = f0_median(x, sr)
        a, b = edges(x, sr)
        r = float(np.sqrt(np.mean(x ** 2)))
        e = spk(to16k(x, sr))
        print(f"{tag:<12}{len(x)/sr:>8.2f}{f0:>9.1f}{f0-ref_f0:>+10.1f}{band4k(x, sr):>8.2f}"
              f"{20*np.log10(max(r,1e-9)):>8.1f}{a:>7.3f}{b:>7.3f}"
              f"{float(ref_e @ e):>10.4f}{float(sig_e @ e):>10.4f}")
    print()
    print("判读：")
    print("  · A_FIXED 的 cos↔REF 应 > cos↔SIG，且 ΔF0 明显小于 B_NATIVE")
    print("  · B_NATIVE 的 cos↔SIG 应 > cos↔REF（证明它落在了原生签名音上）")


if __name__ == "__main__":
    main()
