# -*- coding: utf-8 -*-
"""判定当前 live prompt_cache.gguf 烤的是不是达妮娅。

方法：
  - 从 cache 抽 spk_cb（192 f32 @ offset 736），归一化。
  - 用 campplus.onnx 对达妮娅真源 / MiniCPM 原生签名 / 所有候选 cache 抽说话人向量。
  - 两两余弦：接近"同人上限"=达妮娅；接近"异人下限"=原厂。
"""
import os
import struct
import glob
import subprocess
import numpy as np
import soundfile as sf
import onnxruntime as ort

CACHE_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"
LIVE = os.path.join(CACHE_DIR, "prompt_cache.gguf")
DANIYA = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
NATIVE = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
CAMP = r"H:\daniya_voice_tools\campplus.onnx"
FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"


def to16k(x, sr):
    if sr == 16000:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", "16000", "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def load(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def wav_spk(p):
    import torch
    import torchaudio.compliance.kaldi as kaldi
    x, sr = load(p)
    x16 = to16k(x, sr)
    t = torch.from_numpy(np.asarray(x16)).float().unsqueeze(0)
    fb = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
    fb = fb - fb.mean(dim=0, keepdim=True)
    e = sess.run(None, {iname: fb.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
    return e / (np.linalg.norm(e) + 1e-9)


def cache_spk(path):
    with open(path, "rb") as f:
        f.seek(736)
        b = f.read(768)
    v = np.frombuffer(b, dtype="<f4").astype(np.float64)
    return v / (np.linalg.norm(v) + 1e-9)


def cos(a, b):
    return float(np.dot(a, b))


sess = ort.InferenceSession(CAMP, providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name

print("=" * 96)
print("LIVE cache spk_cb 对照")
print("=" * 96)

live = cache_spk(LIVE)
print("\n[LIVE] prompt_cache.gguf  (size=%s)" % format(os.path.getsize(LIVE), ","))
print("   spk_cb 前6值 = %s" % ", ".join("%+.5f" % x for x in live[:6]))

dan = wav_spk(DANIYA)
nat = wav_spk(NATIVE)

print("\n[DANIYA 真源] %s" % os.path.basename(DANIYA))
print("[NATIVE 原厂] %s" % os.path.basename(NATIVE))

c_live_dan = cos(live, dan)
c_live_nat = cos(live, nat)
c_dan_nat = cos(dan, nat)

print("\n=== 关键余弦 ===")
print("  LIVE vs 达妮娅真源   cos = %+.4f" % c_live_dan)
print("  LIVE vs MiniCPM原生  cos = %+.4f" % c_live_nat)
print("  (参照) 达妮娅 vs 原生  cos = %+.4f   <- 异人下限" % c_dan_nat)

# 同人上限：达妮娅真源劈两半
x, sr = load(DANIYA)
x16 = to16k(x, sr)
h = len(x16) // 2
import torch, torchaudio.compliance.kaldi as kaldi

def emb(fr):
    t = torch.from_numpy(np.asarray(fr)).float().unsqueeze(0)
    fb = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
    fb = fb - fb.mean(dim=0, keepdim=True)
    e = sess.run(None, {iname: fb.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
    return e / (np.linalg.norm(e) + 1e-9)

e1, e2 = emb(x16[:h]), emb(x16[h:])
print("  (参照) 达妮娅同人上限 cos = %+.4f   <- 同人上限" % cos(e1, e2))

print("\n=== 候选 cache 全对照（找真正的达妮娅 cache）===")
cands = [("LIVE", LIVE)]
for p in sorted(glob.glob(os.path.join(CACHE_DIR, "prompt_cache.gguf*"))):
    if os.path.abspath(p) == os.path.abspath(LIVE):
        continue
    if os.path.isfile(p):
        cands.append((os.path.basename(p), p))
for name, p in cands:
    try:
        s = cache_spk(p)
        print("  %-54s cos_vs_达妮娅=%+.4f  cos_vs_原生=%+.4f  size=%s"
              % (name[:54], cos(s, dan), cos(s, nat), format(os.path.getsize(p), ",")))
    except Exception as e:
        print("  %-54s [失败] %r" % (name[:54], e))

print("\n判读：")
if c_live_dan > c_live_nat + 0.05:
    print("  ⇒ LIVE 更接近达妮娅 ⇒ 声码器烤的是达妮娅，本地克隆配置正确。")
elif c_live_nat > c_live_dan + 0.05:
    print("  ⇒ LIVE 更接近原生 ⇒ 声码器仍是原厂，需要换成达妮娅 cache。")
else:
    print("  ⇒ 两者接近，需用候选 cache 对照挑出真正的达妮娅 cache。")
