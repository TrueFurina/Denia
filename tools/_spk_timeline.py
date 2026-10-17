# -*- coding: utf-8 -*-
"""逐窗（2s/0.5s hop）算 campplus 说话人嵌入，与旧参考音比对 —— 得到"谁在说话"的时间线。
必须在 H:\\daniya_voice_tools\\client_venv 下运行。
"""
import os, sys, subprocess
import numpy as np

sys.path.insert(0, r"H:\daniya_voice_tools")

import onnxruntime
import torch
import torchaudio.compliance.kaldi as kaldi

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
VID = r"G:\视频录制\Wuthering Waves\Wuthering Waves 2026.08.27 - 06.59.23.04.mp4"
MODEL_DIR = r"H:\daniya_voice_tools"
REF_SPK = r"H:\daniya_voice_tools\tmp_probe_old_ref\spk_f32.bin"


def decode(path, sr):
    cmd = [FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


opt = onnxruntime.SessionOptions()
opt.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
opt.intra_op_num_threads = 1
sess = onnxruntime.InferenceSession(os.path.join(MODEL_DIR, "campplus.onnx"),
                                    sess_options=opt, providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name


def embed(x16):
    t = torch.from_numpy(x16).float().unsqueeze(0)
    f = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
    f = f - f.mean(dim=0, keepdim=True)
    out = sess.run(None, {iname: f.unsqueeze(0).cpu().numpy()})[0]
    return np.asarray(out).reshape(-1)


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def f0(x, sr, fmin=80, fmax=520):
    n = int(sr * 0.04); hop = int(sr * 0.02); v = []
    for i in range(0, max(1, len(x) - n), hop):
        s = x[i:i + n]
        if np.sqrt((s ** 2).mean()) < 0.012: continue
        s = s - s.mean()
        ac = np.correlate(s, s, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo: continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35: v.append(sr / k)
    return float(np.median(v)) if v else 0.0


def band(x, sr):
    n = 8192
    if len(x) < n: n = 1 << int(np.log2(max(256, len(x))))
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1); c = 0
    for i in range(0, max(1, len(x) - n), n // 2):
        s = x[i:i + n]
        if len(s) < n: break
        psd += np.abs(np.fft.rfft(s * win)) ** 2; c += 1
    psd /= max(1, c)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    tot = psd.sum() or 1e-20
    return (float(psd[(f >= 300) & (f < 3400)].sum() / tot),
            float(psd[(f >= 0) & (f < 120)].sum() / tot))


ref_spk = np.fromfile(REF_SPK, dtype=np.float32)
x48 = decode(VID, 48000)
x16 = decode(VID, 16000)
sr = 48000
W, HOP = 2.0, 0.5
print("窗长%.1fs 步长%.1fs  旧参考音嵌入对照" % (W, HOP))
print("  t(s)   RMS     语音带  <120Hz   F0    与旧参考cos  判定")
rows = []
for t in np.arange(0, len(x48) / sr - W, HOP):
    a, b = int(t * sr), int((t + W) * sr)
    seg = x48[a:b]
    rms = float(np.sqrt((seg ** 2).mean()))
    sp, lo = band(seg, sr)
    f0v = f0(seg, sr)
    a16, b16 = int(t * 16000), int((t + W) * 16000)
    e = embed(x16[a16:b16])
    c = cos(e, ref_spk)
    verd = "★她" if c > 0.70 else ("?疑" if c > 0.45 else "非她")
    rows.append((t, rms, sp, lo, f0v, c, verd))
    print("  %5.1f  %.4f  %5.1f%%  %5.2f%%  %6.1f     %.3f     %s" %
          (t, rms, sp * 100, lo * 100, f0v, c, verd))

print()
print("== 汇总：cos>0.70 且 语音带>0.70 且 <120Hz<0.09 的窗 ==")
good = [r for r in rows if r[5] > 0.70 and r[2] > 0.70 and r[3] < 0.09 and r[1] > 0.015]
for r in good:
    print("   t=%.1f  cos=%.3f  F0=%.1f" % (r[0], r[5], r[4]))
print("   共 %d 窗 / %.1fs" % (len(good), len(good) * HOP))

print()
print("== 汇总：cos>0.70（她）但语音带<0.70（混背景）==")
mix = [r for r in rows if r[5] > 0.70 and r[2] <= 0.70 and r[1] > 0.015]
for r in mix:
    print("   t=%.1f  cos=%.3f  语音带=%.1f%%  F0=%.1f" % (r[0], r[5], r[2] * 100, r[4]))
print("   共 %d 窗 / %.1fs" % (len(mix), len(mix) * HOP))
