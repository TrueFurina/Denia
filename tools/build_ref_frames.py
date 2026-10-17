# -*- coding: utf-8 -*-
"""最终参考音 v2：帧级掩码（1.5s 窗 / 0.25s 步），取全部"她本人+干净+自然音域"的素材。
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
W, HOP = 1.5, 0.25
BANDS = [(0, 120), (120, 300), (300, 1000), (1000, 3400), (3400, 6000), (6000, 8000),
         (8000, 12000), (12000, 20000)]
F0_LO, F0_HI = 228.0, 312.0
SP_MIN = 20.0
COS_MIN = 0.60
RMS_MIN = 0.030
RUN_MIN_S = 0.40
GAP_S = 0.18
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


def bands_of(x, sr, n=8192):
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
N = len(x48)
d = SR * 1000  # 不可用
mask = np.zeros(N, dtype=bool)
diag = []
for t in np.arange(0, N / SR - W, HOP):
    a, b = int(t * SR), int((t + W) * SR)
    seg = x48[a:b]
    rms = float(np.sqrt((seg ** 2).mean()))
    rat = bands_of(seg, SR)
    sp = rat[2] + rat[3]
    f0v = f0_of(seg, SR)
    c = cos(embed(x16[int(t * 16000):int((t + W) * 16000)]), ref_spk)
    good = (rms > RMS_MIN and sp > SP_MIN and F0_LO <= f0v <= F0_HI and c > COS_MIN)
    diag.append((t, rms, sp, f0v, c, good))
    if good:
        # 只标记窗的"核心"（1.5s 窗中心 ±0.4s），避免边界混入
        ca, cb = int((t + 0.35) * SR), int((t + W - 0.35) * SR)
        mask[ca:cb] = True

print("=" * 110)
print("帧级诊断（每 0.25s）— 只打印 good=True 的区段")
print("=" * 110)
runs = []
i = 0
while i < N:
    if mask[i]:
        j = i
        while j < N and mask[j]:
            j += 1
        runs.append((i / SR, j / SR))
        i = j
    else:
        i += 1
# 合并小间隙
merged = []
for a, b in runs:
    if merged and a - merged[-1][1] <= 0.35:
        merged[-1][1] = b
    else:
        merged.append([a, b])
merged = [m for m in merged if m[1] - m[0] >= RUN_MIN_S]
for a, b in merged:
    seg = x48[int(a * SR):int(b * SR)]
    rat = bands_of(seg, SR)
    print("  %6.2f-%6.2f (%4.2fs)  F0=%6.1f  语音带=%5.1f%%  3.4-8k=%5.2f%%" %
          (a, b, b - a, f0_of(seg, SR), rat[2] + rat[3], rat[4] + rat[5]))
print("  合计 %.2fs / %d 段" % (sum(b - a for a, b in merged), len(merged)))

buf = []
for a, b in merged:
    buf.append(x48[int(a * SR):int(b * SR)].copy())
    buf.append(np.zeros(int(SR * GAP_S), np.float32))
y = np.concatenate(buf)[:int(TARGET_S * SR)]
pk = float(np.abs(y).max())
y = y * (0.70 / pk) if pk > 0 else y

os.makedirs(OUTDIR, exist_ok=True)
p48 = os.path.join(OUTDIR, "FINAL2_daniya_ref_48k.wav")
wav_write(p48, y, SR)
r = subprocess.run([FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "-",
                    "-f", "f32le", "-ar", "16000", "-ac", "1", "-"], input=y.astype("<f4").tobytes(),
                   capture_output=True)
y16 = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
pk16 = float(np.abs(y16).max())
y16 = y16 * (0.70 / pk16) if pk16 > 0 else y16
p16 = os.path.join(OUTDIR, "FINAL2_daniya_ref_16k.wav")
wav_write(p16, y16, 16000)

rat = bands_of(y, SR)
print()
print("=" * 110)
print("产出: %.2fs @48k  F0=%.1fHz" % (len(y) / SR, f0_of(y, SR)))
print("  频带: " + "  ".join("%s=%.2f%%" % (n, v) for n, v in zip(
    ["0-120", "120-300", "300-1k", "1-3.4k", "3.4-6k", "6-8k", "8-12k", "12-20k"], rat) if v > 0.01))
print("  旧参考对比: 120-300=70.5  300-1k=15.9  1-3.4k=7.99  3.4-6k=3.81  6-8k=1.66  8-12k=0.01")
print("  " + p48)
print("=" * 110)
