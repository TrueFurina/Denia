# -*- coding: utf-8 -*-
"""判决性测量：平缓窗 vs 干净窗的完整频带构成；并构建 3 种候选参考音。
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
OUT = r"G:\ai对话\11\data\native_audio_test\ref_candidates"
SR = 48000
BANDS = [(0, 120), (120, 300), (300, 1000), (1000, 3400), (3400, 6000), (6000, 8000),
         (8000, 12000), (12000, 20000)]
NAMES = ["0-120", "120-300", "300-1k", "1-3.4k", "3.4-6k", "6-8k", "8-12k", "12-20k"]


def decode(path, sr):
    cmd = [FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def wav_write(path, x, sr):
    x = np.clip(x, -1.0, 1.0)
    d = (x * 32767.0).astype("<i2").tobytes()
    hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
           (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
           sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") +
           (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
    open(path, "wb").write(hdr + d)


def spec_bands(x, sr, n=16384):
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
    rat = [float(psd[(f >= a) & (f < b)].sum() / tot * 100) for a, b in BANDS]
    # 谱平坦度（几何均值/算术均值）：高=噪声，低=谐波/音乐
    m = (f >= 100) & (f <= 7000)
    p = psd[m] + 1e-20
    flat = float(np.exp(np.log(p).mean()) / p.mean())
    return rat, flat


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


def resample(x, sr_in, sr_out):
    cmd = [FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", str(sr_in), "-ac", "1", "-i", "-",
           "-f", "f32le", "-ar", str(sr_out), "-ac", "1", "-"]
    r = subprocess.run(cmd, input=x.astype("<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def pitch_shift(x, sr, semitones, formant=True):
    """rubberband 保共振峰变调（先用 24k 以便 formant 处理）"""
    filt = ("rubberband=pitch=%.6f:formant=%s" % (2 ** (semitones / 12.0),
            "preserved" if formant else "shifted"))
    cmd = [FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
           "-af", filt, "-f", "f32le", "-ar", str(sr), "-ac", "1", "-"]
    r = subprocess.run(cmd, input=x.astype("<f4").tobytes(), capture_output=True)
    if not r.stdout:
        raise RuntimeError("rubberband failed: " + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


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

print("=" * 118)
print("判决性测量：几个代表性 2s 窗的完整频带构成（谱平坦度高=噪声；低=谐波/语音/音乐）")
print("=" * 118)
for t in [2.5, 3.5, 4.5, 7.5, 11.5, 12.5, 15.0, 18.0, 24.5]:
    a = int(t * SR)
    seg = x48[a:a + 2 * SR]
    rat, flat = spec_bands(seg, SR)
    e = embed(x16[int(t * 16000):int((t + 2) * 16000)])
    print("  t=%4.1f cos=%.3f F0=%5.1f 平坦度=%.5f" % (t, cos(e, ref_spk), f0(seg, SR), flat))
    print("        " + "  ".join("%s=%5.2f%%" % (NAMES[i], rat[i]) for i in range(len(BANDS)) if rat[i] > 0.01))

# ---- 构建候选 ----
os.makedirs(OUT, exist_ok=True)
W, HOP = 1.5, 0.5
wins = []
for t in np.arange(0, len(x48) / SR - W, HOP):
    a, b = int(t * SR), int((t + W) * SR)
    seg = x48[a:b]
    rms = float(np.sqrt((seg ** 2).mean()))
    rat, flat = spec_bands(seg, SR)
    sp = rat[2] + rat[3]
    lo = rat[0]
    e = embed(x16[int(t * 16000):int((t + W) * 16000)])
    wins.append(dict(t=t, rms=rms, sp=sp, lo=lo, f0=f0(seg, SR), cos=cos(e, ref_spk)))

# 去重：每 0.5s 一个窗，相邻窗高度重叠 → 用"非极大抑制"式挑不重叠的
def pick(pred, min_gap=1.2):
    cand = [w for w in wins if pred(w)]
    cand.sort(key=lambda w: -w["cos"])
    keep = []
    for w in cand:
        if all(abs(w["t"] - k["t"]) >= min_gap for k in keep):
            keep.append(w)
    return sorted(keep, key=lambda w: w["t"])


def build(keep, gap=0.12, name=""):
    buf = []
    for w in keep:
        a = int(w["t"] * SR); b = int((w["t"] + W) * SR)
        seg = x48[a:b].copy()
        if seg.size == 0: continue
        buf.append(seg); buf.append(np.zeros(int(SR * gap), np.float32))
    if not buf:
        return None
    y = np.concatenate(buf)
    y = y[:int(10.5 * SR)]
    pk = float(np.abs(y).max())
    if pk > 0: y = y * (0.7 / pk)
    wav_write(os.path.join(OUT, name), y, SR)
    return y


her = lambda w: w["cos"] > 0.62 and w["rms"] > 0.03
clean = [w for w in wins if her(w) and w["sp"] > 30]
calm = [w for w in wins if her(w) and w["sp"] <= 30]
print()
print("她+干净(sp>30%%): %d 窗  她+平缓(sp<=30%%): %d 窗" % (len(clean), len(calm)))
print("  干净窗 t:", ["%.1f" % w["t"] for w in clean])
print("  平缓窗 t:", ["%.1f" % w["t"] for w in calm])

for tag, pool in [("C1_clean", clean), ("C2_calm", calm)]:
    keep = pick(lambda w, p=pool: w in p, 1.2)
    y = build(keep, 0.12, tag + "_natural_48k.wav")
    if y is not None:
        rat, flat = spec_bands(y, SR)
        print("  %-12s 用 %d 段 %.2fs F0=%6.1f 平坦度=%.5f  3.4-8k=%.2f%% 8-12k=%.2f%%" %
              (tag, len(keep), len(y) / SR, f0(y, SR), flat, rat[4] + rat[5], rat[6]))

# C3: 干净段降到 250Hz（保共振峰）
keep = pick(lambda w: w in clean, 1.2)
y = build(keep, 0.12, "_tmp_clean48.wav")
if y is not None:
    cur = f0(y, SR)
    st = 12 * np.log2(250.0 / cur) if cur > 0 else 0
    print()
    print("C3: 干净段 F0=%.1f -> 目标 250Hz, 需要 %.2f 半音" % (cur, st))
    y3 = pitch_shift(y, SR, st, formant=True)
    pk = float(np.abs(y3).max())
    if pk > 0: y3 = y3 * (0.7 / pk)
    wav_write(os.path.join(OUT, "C3_clean_shift250_48k.wav"), y3, SR)
    rat, flat = spec_bands(y3, SR)
    print("  C3 得到 F0=%6.1f  平坦度=%.5f  3.4-8k=%.2f%% 8-12k=%.2f%%" %
          (f0(y3, SR), flat, rat[4] + rat[5], rat[6]))
print()
print("产出目录:", OUT)
for f in sorted(os.listdir(OUT)):
    print("   %8.1f KB  %s" % (os.path.getsize(os.path.join(OUT, f)) / 1024, f))
