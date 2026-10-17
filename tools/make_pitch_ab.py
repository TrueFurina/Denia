# -*- coding: utf-8 -*-
"""生成诊断样本：把参考音升调后与录屏人声放在同一音高上对比。"""
import os, subprocess
import numpy as np
FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
D = r"G:\ai对话\11\data\native_audio_test\src_candidates"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"

R = 390.2 / 238.8   # 倍数
tmp = r"G:\ai对话\11\data\native_audio_test\_ref_up.wav"

# 参考音升调：asetrate 提音高，atempo 还原时长
subprocess.run([FFMPEG, "-v", "quiet", "-y", "-i", REF,
                "-af", "asetrate=16000*%.4f,aresample=16000,atempo=%.4f" % (R, 1.0 / R),
                "-ac", "1", "-ar", "48000", tmp], capture_output=True)
print("升调参考音已生成，倍数 %.4f" % R)


def rd(p):
    r = subprocess.run([FFMPEG, "-v", "quiet", "-i", p, "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def peak_norm(x, target=0.25):
    return x * (target / (np.abs(x).max() + 1e-9))


a = peak_norm(rd(tmp))                     # 升调后的参考音
b = peak_norm(rd(os.path.join(D, "V0827_r07_16.0-17.1s_F0390.wav")))  # 本片最干净段
common = min(len(a), len(b))
gap = np.zeros(int(48000 * 0.8), np.float32)
out = np.concatenate([a[:common], gap, b[:common]])
p = os.path.join(D, "_诊断_同音高对比_升调参考音_再_本片人声.wav")
d = (np.clip(out, -1, 1) * 32767).astype("<i2").tobytes()
hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
       (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
       (48000).to_bytes(4, "little") + (96000).to_bytes(4, "little") + (2).to_bytes(2, "little") +
       (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
open(p, "wb").write(hdr + d)
print("->", os.path.basename(p), " %.2fs" % (len(out) / 48000))
os.remove(tmp)
