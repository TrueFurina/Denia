# -*- coding: utf-8 -*-
"""把任意 wav 重采样到指定采样率（单声道 PCM16）。用 venv 解释器运行。"""
import sys, soundfile as sf, torch, torchaudio

src, dst, sr_out = sys.argv[1], sys.argv[2], int(sys.argv[3])
x, sr = sf.read(src, dtype="float32", always_2d=False)
if getattr(x, "ndim", 1) > 1:
    x = x.mean(axis=1)
t = torch.from_numpy(x).float()
if sr != sr_out:
    t = torchaudio.transforms.Resample(sr, sr_out)(t)
sf.write(dst, t.numpy(), sr_out, subtype="PCM_16")
print("resampled %s %dHz -> %s %dHz  %d samples  %.2fs" %
      (src, sr, dst, sr_out, len(t), len(t) / sr_out))
