# -*- coding: utf-8 -*-
"""参考音 v2：从 44.1kHz 真源重建，并按原厂基准标定电平。

三条修正（全部可验证）：
  1. 源换成 44.1kHz 真源 mp3（此前整条链用的是它 16kHz 降采样版，实测 8-12k 差 26dB）
  2. 直接以 24kHz 解码（一次高质量重采样，避免 16k 中转）
  3. 电平标定：prompt_mel = log(clamp(...))，是**绝对尺度**；求解增益使本参考音的
     浊音帧 mel 均值 == 原厂 default_ref_audio 的浊音帧 mel 均值
     （历史版本比原厂低 6.0dB，这是实实在在的条件失配）
"""
import os
import subprocess
import numpy as np
import soundfile as sf
import torch
import librosa

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
WX = r"G:\微信聊天文件\xwechat_files\wxid_2cu68rhp1igz12_1725\msg\file\2026-08"
FAC = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio"
OUT = r"G:\ai对话\11\data\native_audio_test\ref_v2"
os.makedirs(OUT, exist_ok=True)

_basis = {}


def mel_spectrogram(y24, fmax=8000, n_fft=1920, num_mels=80, sr=24000,
                    hop=480, win=1920, fmin=0):
    key = fmax
    if key not in _basis:
        _basis[key] = torch.from_numpy(librosa.filters.mel(
            sr=sr, n_fft=n_fft, n_mels=num_mels, fmin=fmin, fmax=fmax).astype(np.float32))
    mel = _basis[key]
    y = torch.from_numpy(np.asarray(y24, dtype=np.float32)).float()
    if y.dim() == 1:
        y = y.unsqueeze(0)
    y = torch.nn.functional.pad(y.unsqueeze(1),
                                (int((n_fft - hop) / 2), int((n_fft - hop) / 2)),
                                mode="reflect").squeeze(1)
    spec = torch.stft(y, n_fft, hop_length=hop, win_length=win,
                      window=torch.hann_window(win), center=False, pad_mode="reflect",
                      normalized=False, onesided=True, return_complex=True)
    spec = torch.sqrt(torch.view_as_real(spec).pow(2).sum(-1) + 1e-9)
    spec = torch.log(torch.clamp(torch.matmul(mel, spec), min=1e-5))
    return spec.transpose(1, 2).squeeze(0).numpy()   # (T,80)


def voiced_mel_mean(x24):
    m = mel_spectrogram(x24)
    fm = m.mean(axis=1)
    v = fm > (-11.513 + 1.0)
    if v.sum() < 5:
        v = fm > np.percentile(fm, 60)
    return float(np.median(fm[v])), float(fm.mean()), int(v.sum()), len(fm)


def decode(p, sr):
    r = subprocess.run([FF, "-v", "quiet", "-i", p, "-ac", "1",
                        "-f", "f32le", "-ar", str(sr), "-"],
                       capture_output=True)
    x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    return x


def trim(x, sr, thr_rel=0.03, pad=0.02):
    w = int(sr * 0.01)
    m = len(x) // w
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > max(1e-5, e.max() * thr_rel))[0]
    if not len(idx):
        return x
    a = max(0, int((idx[0] * 0.01 - pad) * sr))
    b = min(len(x), int(((idx[-1] + 1) * 0.01 + pad) * sr))
    return x[a:b]


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
    f99 = float(f[np.searchsorted(cs, 0.99 * (cs[-1] + 1e-12))])
    return out, f99


def f0_med(x, sr, fmin=70, fmax=500, frame=1024, hop=256, thr=0.01):
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


print("=" * 104)
print("基准：原厂 default_ref_audio")
print("=" * 104)
fx, fsr = sf.read(os.path.join(FAC, "default_ref_audio.wav"), dtype="float32")
if fx.ndim > 1:
    fx = fx.mean(axis=1)
fmean, fall, fv, ft = voiced_mel_mean(fx)
fb, ff99 = bands(fx, fsr)
print("   sr=%d dur=%.3fs rms=%.4f(%.1f dBFS)" % (fsr, len(fx) / fsr,
      np.sqrt((fx ** 2).mean()), 20 * np.log10(np.sqrt((fx ** 2).mean()))))
print("   mel: 浊音帧中位均值=%.3f  全帧均值=%.3f  浊音帧 %d/%d" % (fmean, fall, fv, ft))
print("   band 3.4-6k/6-8k/8-12k = %s   f99=%.0f" % (np.round(fb, 1), ff99))
TARGET = fmean
print()

print("=" * 104)
print("当前线上参考音（16k 派生 + 归一到 -20dBFS）")
print("=" * 104)
cur = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
cx, csr = sf.read(cur, dtype="float32")
if cx.ndim > 1:
    cx = cx.mean(axis=1)
cmean, call, cv, ct = voiced_mel_mean(cx)
cb, cf99 = bands(cx, csr)
print("   sr=%d dur=%.3fs rms=%.4f(%.1f dBFS)  F0=%.1f" % (
    csr, len(cx) / csr, np.sqrt((cx ** 2).mean()),
    20 * np.log10(np.sqrt((cx ** 2).mean())), f0_med(cx, csr)))
print("   mel: 浊音帧中位均值=%.3f (原厂 %.3f, 差 %+.3f log = %+.1f dB)" % (
    cmean, TARGET, cmean - TARGET, (cmean - TARGET) / (np.log(10) / 20)))
print("   band 3.4-6k/6-8k/8-12k = %s   f99=%.0f" % (np.round(cb, 1), cf99))
print()

print("=" * 104)
print("生成 v2 参考音")
print("=" * 104)
JOBS = [
    ("mp3_8s", os.path.join(WX, "8月13日 (1).mp3"), None, None),
    ("dialogue", os.path.join(WX, "8月13日.mp3"), None, None),
]
results = {}
for tag, src, a, b in JOBS:
    x44 = decode(src, 24000)          # 直接从 44.1k 解码到 24k（一次高质量重采样）
    x44 = trim(x44, 24000)
    dur = len(x44) / 24000
    print("  [%s] 裁静音后 %.3fs" % (tag, dur))
    # 取窗：目标 7~10s
    if dur > 10.5:
        # 用 20ms 能量找最连续的中段
        w = int(24000 * 0.02)
        m = len(x44) // w
        e = np.sqrt(np.mean(x44[:m * w].reshape(m, w) ** 2, axis=1))
        thr = e.max() * 0.035
        v = e > thr
        best = (0, 0, 0.0)
        i = 0
        while i < m:
            if v[i]:
                j = i
                while j < m and v[j]:
                    j += 1
                seg = (j - i) * 0.02
                if seg > best[2]:
                    best = (i, j, seg)
                i = j
            else:
                i += 1
        cen = (best[0] + best[1]) // 2
        need = int(9.5 * 24000)
        a0 = max(0, cen - need // 2)
        a0 = min(a0, max(0, len(x44) - need))
        x24 = x44[a0:a0 + need]
        print("        最长连段 %.2fs，取中心 9.5s 窗 (%.2f→%.2fs)" % (best[2], a0 / 24000, (a0 + need) / 24000))
    else:
        x24 = x44
    # 电平标定：求解增益使浊音帧 mel 中位均值 == 原厂
    m0, _, _, _ = voiced_mel_mean(x24)
    gain = float(np.exp(TARGET - m0))
    gain = min(max(gain, 0.05), 20.0)
    x24c = x24 * gain
    if np.abs(x24c).max() > 0.999:
        x24c = x24c / np.abs(x24c).max() * 0.97
    m1, a1, v1, t1 = voiced_mel_mean(x24c)
    bb, f99 = bands(x24c, 24000)
    f0 = f0_med(x24c, 24000)
    p24 = os.path.join(OUT, "%s_24k.wav" % tag)
    sf.write(p24, x24c, 24000, subtype="PCM_16")
    # 16k 版（仅供分析对照，不进烘焙）
    r = subprocess.run([FF, "-v", "quiet", "-i", p24, "-ac", "1", "-ar", "16000",
                        "-f", "wav", "-y", os.path.join(OUT, "%s_16k.wav" % tag)],
                       capture_output=True)
    print("        增益 x%.3f  mel 浊音帧均值 %.3f → %.3f (目标 %.3f)" % (gain, m0, m1, TARGET))
    print("        dur=%.2fs rms=%.4f(%.1f dBFS) peak=%.3f F0=%.1f  band=%s f99=%.0f"
          % (len(x24c) / 24000, np.sqrt((x24c ** 2).mean()),
             20 * np.log10(np.sqrt((x24c ** 2).mean())), np.abs(x24c).max(),
             f0, np.round(bb, 1), f99))
    results[tag] = p24
    print()

# 说话人校验：v2 与真源、与原厂、与原参考音
print("=" * 104)
print("说话人相似度（campplus, 16k）")
print("=" * 104)
import onnxruntime
import torchaudio.compliance.kaldi as kaldi
sess = onnxruntime.InferenceSession(r"H:\daniya_voice_tools\campplus.onnx",
                                    providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name


def spk(x):
    t = torch.from_numpy(np.asarray(x, dtype=np.float32)).float().unsqueeze(0)
    fb = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
    fb = fb - fb.mean(dim=0, keepdim=True)
    e = sess.run(None, {iname: fb.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
    return e / (np.linalg.norm(e) + 1e-9)


def x16(p):
    x = decode(p, 16000)
    return x / (np.abs(x).max() + 1e-9)


refs = {
    "真源mp3(8s)": x16(os.path.join(WX, "8月13日 (1).mp3")),
    "真源mp3(22s)": x16(os.path.join(WX, "8月13日.mp3")),
    "旧参考(CUR)": x16(cur),
    "原厂default": x16(os.path.join(FAC, "default_ref_audio.wav")),
}
ems = {k: spk(v) for k, v in refs.items()}
print("  基准 = 真源mp3(8s)")
for k, e in ems.items():
    print("     %-16s cos = %.4f" % (k, float(ems["真源mp3(8s)"] @ e)))
print()
for tag, p in results.items():
    e = spk(x16(p))
    print("  v2/%-12s " % tag + "  ".join("%s=%.4f" % (k, float(e2 @ e)) for k, e2 in ems.items()))
print()
print("产出文件：")
for tag, p in results.items():
    print("   %s  (%d B)" % (p, os.path.getsize(p)))
