# -*- coding: utf-8 -*-
"""从原始微信参考音 daniya_ref_ORIGINAL_16k.wav 生成清理版：
- 去掉首尾静音（按 RMS 阈值检测语音起止）
- peak 归一化到 0.9
- 保持 16k 单声道（模型 speech tokenizer 输入要求 16k）
输出：daniya_ref_clean_16k.wav
"""
import os, numpy as np, soundfile as sf

SRC = r"H:\daniya_voice_tools\daniya_ref_ORIGINAL_16k.wav"
OUT = r"G:\ai对话\11\data\native_audio_test\ref_build\daniya_ref_clean_16k.wav"
SR_TARGET = 16000

x, sr = sf.read(SRC, dtype="float32")
if x.ndim > 1:
    x = x.mean(axis=1)
print("src sr=%d len=%d dur=%.3fs" % (sr, len(x), len(x)/sr))

# 重采样到 16k（源已是 16k，保险）
if sr != SR_TARGET:
    import subprocess
    r = subprocess.run(
        [r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
         "-v","quiet","-f","f32le","-ar",str(sr),"-ac","1","-i","-",
         "-f","f32le","-ar",str(SR_TARGET),"-ac","1","-"],
        input=x.astype("<f4").tobytes(), capture_output=True)
    x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    sr = SR_TARGET

# RMS 阈值检测语音段
win = int(0.02 * sr)  # 20ms
n = len(x) // win
rms = np.array([np.sqrt(np.mean(x[i*win:(i+1)*win]**2)) if (i+1)*win <= len(x) else 0.0
                for i in range(n)])
thr = max(0.005, rms.max()*0.05)
speech = rms > thr
idx = np.where(speech)[0]
if len(idx) == 0:
    start_f, end_f = 0, len(x)
else:
    start_i = max(0, idx[0]-2)
    end_i = min(n, idx[-1]+2)
    start_f = start_i*win
    end_f = min(len(x), end_i*win)
# 留 100ms 头尾余量
pad = int(0.10*sr)
start_f = max(0, start_f-pad)
end_f = min(len(x), end_f+pad)
seg = x[start_f:end_f]
print("trim: %.3fs -> %.3fs (speech idx %d..%d)" % (len(x)/sr, len(seg)/sr, start_f, end_f))

# peak 归一
pk = np.max(np.abs(seg))
if pk > 0:
    seg = seg * (0.9/pk)
print("after norm peak=%.3f rms=%.4f" % (np.max(np.abs(seg)), np.sqrt(np.mean(seg**2))))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
sf.write(OUT, seg, sr)
print("SAVED", OUT, len(seg)/sr, "s")
