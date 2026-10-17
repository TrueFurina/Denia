# -*- coding: utf-8 -*-
"""诊断 CosyVoice2 的缺失属性 / 流式拼接逻辑。
不猜，全部实测打印。
"""
import os, sys, io, glob, json

REPO = r"G:\ai对话\11\cosyvoice_repo"
MATCHA = os.path.join(REPO, "third_party", "Matcha-TTS")
for p in (REPO, MATCHA):
    if p not in sys.path:
        sys.path.insert(0, p)

# torchaudio 2.11 monkey-patch（同 cosyvoice_server.py）
def _taload(path, backend=None):
    import soundfile as sf
    import torch
    data, sr = sf.read(path, dtype="float32")
    data = data[None, :] if data.ndim == 1 else data.T
    return torch.from_numpy(data), sr

import torchaudio
torchaudio.load = _taload

MODEL_DIR = r"G:\ai对话\11\cosyvoice_models\models\iic--CosyVoice2-0.5B\snapshots\master"

from cosyvoice.cli.cosyvoice import CosyVoice2

print("=== 加载模型 ===", flush=True)
tts = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=True)
m = tts.model
print("model class:", type(m).__mro__[:3], flush=True)

print("\n=== 属性实测（子类 __init__ 后）===", flush=True)
attrs = ["mel_overlap_len", "mel_window", "mel_cache_len", "source_cache_len",
         "speech_window", "mel_overlap_dict", "flow_cache_dict", "hift_cache_dict",
         "silent_tokens", "token_hop_len", "token_max_hop_len", "token_min_hop_len",
         "token_overlap_len", "stream_scale_factor", "flow_cache_dict"]
for a in attrs:
    if hasattr(m, a):
        v = getattr(m, a)
        if isinstance(v, dict):
            print("  OK   %-22s dict(len=%d)" % (a, len(v)))
        elif hasattr(v, "shape"):
            print("  OK   %-22s %s" % (a, v.shape))
        else:
            print("  OK   %-22s %r" % (a, v))
    else:
        print("  MISS %-22s <-- 不存在" % a)

print("\n=== flow 配置关键值 ===", flush=True)
f = m.flow
for a in ["input_frame_rate", "token_mel_ratio", "pre_lookahead_len", "output_size", "input_size"]:
    print("  flow.%-20s %r" % (a, getattr(f, a, "<MISSING>")))
print("  feat_extractor fmax:", getattr(tts.frontend.feat_extractor, "fmax", "<MISSING>"))
print("  feat_extractor sr  :", getattr(tts.frontend.feat_extractor, "sampling_rate", "<MISSING>"))

print("\n=== 试跑一次 zero_shot（stream=True），逐块体检 ===", flush=True)
import numpy as np

def spec_stats(x, sr):
    n, hop = 1024, 256
    if len(x) < n:
        return None
    nf = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(nf)[:, None]
    fr = x[idx] * np.hanning(n)[None, :]
    P = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    P = P[:, : n // 2 + 1]
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    flat = np.median(np.exp(np.mean(np.log(P + 1e-12), axis=1)) / (np.mean(P, axis=1) + 1e-12))
    cs = np.cumsum(P, axis=1) / (P.sum(axis=1, keepdims=True) + 1e-12)
    f95 = np.median(freqs[np.argmax(cs >= 0.95, axis=1)])
    return flat, f95

ref = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
print("  ref exists:", os.path.exists(ref), os.path.getsize(ref) if os.path.exists(ref) else 0)

txt = "今天过得怎么样？"
reftxt = "我讨厌吃生的东西！特别是那种摆好盘的切片肉类。"

try:
    chunks = []
    for i, out in enumerate(tts.inference_zero_shot(txt, reftxt, ref, stream=True, speed=1.0, text_frontend=False)):
        sp = out["tts_speech"]
        arr = sp.squeeze().cpu().numpy() if hasattr(sp, "cpu") else np.asarray(sp).squeeze()
        chunks.append(arr)
        st = spec_stats(arr, 24000)
        print("  chunk %d: %d samples (%.2fs) %s" % (
            i, len(arr), len(arr) / 24000.0,
            ("flat=%.5f f95=%.0f" % st) if st else "too short"))
    if chunks:
        full = np.concatenate(chunks)
        st = spec_stats(full, 24000)
        print("  FULL: %d samples (%.2fs) flat=%.5f f95=%.0f" % (
            len(full), len(full) / 24000.0, st[0], st[1]))
        import soundfile as sf
        od = r"G:\ai对话\11\data\_diag_attr"
        os.makedirs(od, exist_ok=True)
        sf.write(os.path.join(od, "stream_chunks.wav"), full, 24000)
        print("  saved ->", os.path.join(od, "stream_chunks.wav"))
except Exception:
    import traceback
    traceback.print_exc()
