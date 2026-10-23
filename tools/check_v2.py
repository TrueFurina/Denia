# -*- coding: utf-8 -*-
"""v2 客观对照：同一把尺子（与 check_sample.py 完全一致）+ 新增频段剖面。
不进闸门：一次性研究脚本——基准文件全在开发机 G:/H: 盘，换机即废；判据未进
自动闸门（2026-10-22 普查定性，见 tools/check_gate_registry.py）。

基准：
  SRC  = 真源 mp3 24k（7.04s 精修版）—— 目标音色
  SIG  = MiniCPM 原生签名音
判读：
  ΔF0 越小越好；cos↔SRC 越大越好；8-12k 越接近 -7.8dB 越好（这是"清晰度"的量化）
"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
OLD_REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav.bak_before_wechat_20260921_213100"


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
    se = spk(to16k(sx, ssr))
    ge = spk(to16k(gx, gsr))
    sf0 = f0_median(sx, ssr)
    sb, sf99 = bands(sx, ssr)
    print("=" * 118)
    print("基准")
    print("=" * 118)
    print("  SRC 真源 mp3(24k精修)  %.2fs F0=%.1f  3.4-6k/6-8k/8-12k = [%.1f %.1f %.1f]  f99=%.0f"
          % (len(sx) / ssr, sf0, sb[0], sb[1], sb[2], sf99))
    gb, gf99 = bands(gx, gsr)
    print("  SIG MiniCPM原生签名     %.2fs F0=%.1f  3.4-6k/6-8k/8-12k = [%.1f %.1f %.1f]  f99=%.0f"
          % (len(gx) / gsr, f0_median(gx, gsr), gb[0], gb[1], gb[2], gf99))
    print()
    print("=" * 118)
    print("产出对照")
    print("=" * 118)
    print("%-24s%8s%9s%9s%24s%9s%10s%10s"
          % ("样本", "时长", "F0", "ΔF0↔SRC", "3.4-6k/6-8k/8-12k", "f99", "cos↔SRC", "cos↔SIG"))
    print("-" * 118)
    rows = [("A_FIXED 线上旧版", os.path.join(FS, "A_FIXED.wav")),
            ("B_NATIVE 原生底色", os.path.join(FS, "B_NATIVE.wav")),
            ("v2a 真源+电平标定", os.path.join(FS, "A_FIXED_v2a.wav")),
            ("v2b 台词9.5s", os.path.join(FS, "A_FIXED_v2b.wav")),
            ("EQ1_v2a 高频补偿", os.path.join(FS, "hf", "EQ1_v2a.wav")),
            ("EQ2_v2a 高频补偿SBR", os.path.join(FS, "hf", "EQ2_v2a.wav")),
            ("EQ1_线上旧版", os.path.join(FS, "hf", "EQ1_A_FIXED.wav"))]
    for lab, p in rows:
        if not os.path.exists(p):
            print("%-24s MISSING" % lab); continue
        x, sr = load(p)
        f0 = f0_median(x, sr)
        b, f99 = bands(x, sr)
        e = spk(to16k(x, sr))
        print("%-24s%8.2f%9.1f%+9.1f%8s[%5.1f %5.1f %5.1f]%9.0f%10.4f%10.4f"
              % (lab, len(x) / sr, f0, f0 - sf0, "", b[0], b[1], b[2], f99,
                 float(se @ e), float(ge @ e)))
    print()
    print("判读：")
    print("  · ΔF0↔SRC 越小 = 音高越像；cos↔SRC 越大 = 越像本人；cos↔SIG 越大 = 越沾原生底")
    print("  · 8-12k 列越接近 %.1f 越'清晰'；越接近 -20 以下越'闷'" % sb[2])


if __name__ == "__main__":
    main()
