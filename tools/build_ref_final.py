# -*- coding: utf-8 -*-
"""最终参考音：从 48kHz 视频里挑「她本人 + 干净 + 自然音域(230-310Hz)」的段。
在 H:\\daniya_voice_tools\\client_venv 下运行。
"""
import os, sys, subprocess
import numpy as np

sys.path.insert(0, r"H:\daniya_voice_tools")
import onnxruntime
import torch
import torchaudio.compliance.kaldi as kaldi

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
VID = r"G:\视频录制\Wuthering Waves\Wuthering Waves 2026.08.27 - 06.59.23.04.mp4"
OUTDIR = r"G:\ai对话\11\data\native_audio_test\ref_build"
SR = 48000
W, HOP = 1.5, 0.5
BANDS = [(0, 120), (120, 300), (300, 1000), (1000, 3400), (3400, 6000), (6000, 8000),
         (8000, 12000), (12000, 20000)]

F0_LO, F0_HI = 228.0, 312.0
SP_MIN = 20.0
COS_MIN = 0.60
GAP = 0.15
TARGET_S = 9.0


def decode(path, sr):
    r = subprocess.run([FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def wav_write(path, x, sr):
    x = np.clip(x, -1.0, 1.0)
    d = (x * 32767.0).astype("<i2").tobytes()
    hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
           (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
           sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") +
           (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
    open(path, "wb").write(hdr + d)


def bands_of(x, sr, n=16384):
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
    return [float(psd[(f >= a) & (f < b)].sum() / tot * 100) for a, b in BANDS]


def f0_of(x, sr, fmin=80, fmax=520):
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


opt = onnxruntime.SessionOptions()
opt.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
opt.intra_op_num_threads = 1
sess = onnxruntime.InferenceSession(os.path.join(r"H:\daniya_voice_tools", "campplus.onnx"),
                                    sess_options=opt, providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name
ref_spk = np.fromfile(r"H:\daniya_voice_tools\tmp_probe_old_ref\spk_f32.bin", dtype=np.float32)


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def embed(x16):
    t = torch.from_numpy(x16).float().unsqueeze(0)
    f = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
    f = f - f.mean(dim=0, keepdim=True)
    return np.asarray(sess.run(None, {iname: f.unsqueeze(0).numpy()})[0]).reshape(-1)


x48 = decode(VID, SR)
x16 = decode(VID, 16000)

wins = []
for t in np.arange(0, len(x48) / SR - W, HOP):
    a, b = int(t * SR), int((t + W) * SR)
    seg = x48[a:b]
    rat = bands_of(seg, SR)
    e = embed(x16[int(t * 16000):int((t + W) * 16000)])
    wins.append(dict(t=t, rms=float(np.sqrt((seg ** 2).mean())), sp=rat[2] + rat[3],
                     lo=rat[0], hi=rat[4] + rat[5], f0=f0_of(seg, SR), cos=cos(e, ref_spk)))

print("=" * 112)
print("候选窗（cos>%.2f 且 rms>0.03 且 语音带>%.0f%%）— 按综合分排序" % (COS_MIN, SP_MIN))
print("=" * 112)
cand = [w for w in wins if w["cos"] > COS_MIN and w["rms"] > 0.03 and w["sp"] > SP_MIN]
cand.sort(key=lambda w: -(w["cos"] * 0.5 + w["sp"] / 100 * 0.3 + w["hi"] / 10 * 0.2))
print("  t(s)    F0    cos   语音带  3.4-8k   <120Hz   综合命中自然音域?")
for w in cand:
    print("  %5.1f  %6.1f  %.3f  %5.1f%%  %5.2f%%  %5.2f%%   %s" %
          (w["t"], w["f0"], w["cos"], w["sp"], w["hi"], w["lo"],
           "★" if F0_LO <= w["f0"] <= F0_HI else "(音域外)"))

pool = [w for w in cand if F0_LO <= w["f0"] <= F0_HI]
print()
print("落在自然音域 %.0f-%.0fHz 的窗: %s" % (F0_LO, F0_HI, ["%.1f" % w["t"] for w in pool]))

keep = []
for w in sorted(pool, key=lambda w: -w["cos"]):
    if all(abs(w["t"] - k["t"]) >= 1.0 for k in keep):
        keep.append(w)
keep.sort(key=lambda w: w["t"])
print("NMS 后保留 %d 段: %s" % (len(keep), ["%.1f" % w["t"] for w in keep]))

buf = []
for w in keep:
    a = int(w["t"] * SR); b = int((w["t"] + W) * SR)
    buf.append(x48[a:b].copy()); buf.append(np.zeros(int(SR * GAP), np.float32))
if not buf:
    print("!! 无可用段")
    sys.exit(1)
y = np.concatenate(buf)[:int(TARGET_S * SR)]
# 段间 RMS 一致化：整体峰值归一
pk = float(np.abs(y).max())
y = y * (0.70 / pk) if pk > 0 else y

os.makedirs(OUTDIR, exist_ok=True)
p48 = os.path.join(OUTDIR, "FINAL_daniya_ref_48k.wav")
wav_write(p48, y, SR)

# 16k 版（供 LLM 层 daniya_ref.wav）
r = subprocess.run([FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "-",
                    "-f", "f32le", "-ar", "16000", "-ac", "1", "-"], input=y.astype("<f4").tobytes(),
                   capture_output=True)
y16 = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
pk16 = float(np.abs(y16).max())
y16 = y16 * (0.70 / pk16) if pk16 > 0 else y16
p16 = os.path.join(OUTDIR, "FINAL_daniya_ref_16k.wav")
wav_write(p16, y16, 16000)

rat = bands_of(y, SR)
print()
print("=" * 112)
print("产出 FINAL: %.2fs @48k  F0=%.1fHz  (旧参考 238.8 / 视频全片 263.7 / 视频干净段 318)" %
      (len(y) / SR, f0_of(y, SR)))
print("  频带: " + "  ".join("%s=%.2f%%" % (n, v) for n, v in zip(
    ["0-120", "120-300", "300-1k", "1-3.4k", "3.4-6k", "6-8k", "8-12k", "12-20k"], rat) if v > 0.01))
print("  对比旧参考音: 120-300=70.5%% 300-1k=15.9%% 1-3.4k=7.99%% 3.4-6k=3.81%% 6-8k=1.66%%")
print("  %s" % p48)
print("  %s" % p16)
print("=" * 112)
