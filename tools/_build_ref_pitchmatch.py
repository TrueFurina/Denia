# -*- coding: utf-8 -*-
"""把 FINAL2 用「保共振峰」方式降调对齐到 ~246Hz（对齐原厂/现网目标音高），
只改带宽不改音高。用 venv 运行。"""
import os, subprocess, numpy as np, soundfile as sf, librosa

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"
DST48 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL3_daniya_ref_48k.wav"
DST16 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL3_daniya_ref_16k.wav"
TARGET_F0 = 246.0

print("ffmpeg exists:", os.path.exists(FFMPEG))


def f0_of(p):
    y, sr = sf.read(p, dtype="float32", always_2d=False)
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    if sr != 16000:
        y = librosa.resample(y, orig_sr=sr, target_sr=16000); sr = 16000
    f0 = librosa.yin(y, fmin=80, fmax=420, sr=sr, frame_length=2048)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    n = min(len(f0), len(rms))
    m = rms[:n] > np.percentile(rms[:n], 60)
    v = f0[:n][m]; v = v[np.isfinite(v)]
    return float(np.median(v))


def bands(p):
    y, sr = sf.read(p, dtype="float32", always_2d=False)
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    Y = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.fft.rfftfreq(len(y), 1 / sr)
    tot = (Y ** 2).sum() + 1e-12

    def e(a, b):
        m = (f >= a) & (f < b)
        return (Y[m] ** 2).sum() / tot * 100

    return e(120, 300), e(300, 1000), e(3400, 6000), e(6000, 8000), e(8000, 12000)


src_f0 = f0_of(SRC)
ratio = TARGET_F0 / src_f0
print("FINAL2 F0=%.1fHz -> 目标 %.1fHz, rubberband pitch=%.4f" % (src_f0, TARGET_F0, ratio))

cmd = [FFMPEG, "-y", "-v", "error", "-i", SRC,
       "-af", "rubberband=pitch=%.4f:formant=preserved" % ratio,
       "-ar", "48000", "-ac", "1", DST48]
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("ffmpeg rc=%s %s" % (r.returncode, (r.stderr or "").strip()[:400]))
if r.returncode != 0:
    raise SystemExit(1)

r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", DST48, "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", DST16],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("ffmpeg 16k rc=%s %s" % (r.returncode, (r.stderr or "").strip()[:300]))

for tag, p in [("FINAL2", SRC), ("FINAL3", DST48)]:
    b = bands(p)
    print("%-8s F0=%6.1fHz  bands[120-300,300-1k,3.4-6k,6-8k,8-12k]=%.1f%% %.1f%% %.2f%% %.2f%% %.2f%%"
          % (tag, f0_of(p), *b))
print("saved:", DST48, os.path.getsize(DST48), "|", DST16, os.path.getsize(DST16))
