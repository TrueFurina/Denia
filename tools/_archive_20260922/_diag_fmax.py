# -*- coding: utf-8 -*-
"""fmax 对照实验：feat_extractor.fmax = 8000 vs null（对齐训练）
同时对照 fp16 True/False，四个组合，看哪个能出正常语音。
判据 flat>0.002 且 f95>1200。
"""
import os, sys, io, functools, contextlib

REPO = r"G:\ai对话\11\cosyvoice_repo"
for p in (REPO, os.path.join(REPO, "third_party", "Matcha-TTS")):
    if p not in sys.path:
        sys.path.insert(0, p)

def _taload(path, backend=None):
    import soundfile as sf
    import torch
    data, sr = sf.read(path, dtype="float32")
    data = data[None, :] if data.ndim == 1 else data.T
    return torch.from_numpy(data), sr

import torchaudio
torchaudio.load = _taload

import numpy as np
import soundfile as sf

MODEL_DIR = r"G:\ai对话\11\cosyvoice_models\models\iic--CosyVoice2-0.5B\snapshots\master"
OUT = r"G:\ai对话\11\data\_diag_fmax"
os.makedirs(OUT, exist_ok=True)
os.environ.setdefault("COSYVOICE_MODEL_DIR", MODEL_DIR)

REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
TXT = "今天过得怎么样？"
REFTXT = "我讨厌吃生的东西！特别是那种摆好盘的切片肉类。"


def spec_stats(x, sr, n=1024, hop=256):
    if len(x) < n:
        return None
    nf = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(nf)[:, None]
    fr = x[idx] * np.hanning(n)[None, :]
    P = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    P = P[:, : n // 2 + 1]
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    flat = float(np.median(np.exp(np.mean(np.log(P + 1e-12), axis=1)) / (np.mean(P, axis=1) + 1e-12)))
    cs = np.cumsum(P, axis=1) / (P.sum(axis=1, keepdims=True) + 1e-12)
    f95 = float(np.median(freqs[np.argmax(cs >= 0.95, axis=1)]))
    return flat, f95


def run_case(tag, fmax, fp16, make_feat_extractor):
    """make_feat_extractor(fmax) -> 新的 feat_extractor 对象"""
    from cosyvoice.cli.cosyvoice import CosyVoice2
    print("\n" + "=" * 70, flush=True)
    print("[CASE] %s  fmax=%r fp16=%s" % (tag, fmax, fp16), flush=True)
    tts = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=fp16)
    if fmax is not None:
        pass
    try:
        tts.frontend.feat_extractor = make_feat_extractor(fmax)
        print("  feat_extractor replaced ok", flush=True)
    except Exception as e:
        print("  replace FAILED:", e, flush=True)
        return None
    # 打印实际生效的 fmax
    fe = tts.frontend.feat_extractor
    print("  feat_extractor repr:", repr(fe)[:160], flush=True)

    chunks = []
    try:
        for out in tts.inference_zero_shot(TXT, REFTXT, REF, stream=True, speed=1.0, text_frontend=False):
            sp = out["tts_speech"]
            arr = sp.squeeze().cpu().numpy() if hasattr(sp, "cpu") else np.asarray(sp).squeeze()
            chunks.append(arr)
    except Exception:
        import traceback
        traceback.print_exc()
    if not chunks:
        print("  no chunks", flush=True)
        return None
    full = np.concatenate(chunks)
    st = spec_stats(full, 24000)
    p = os.path.join(OUT, "%s.wav" % tag)
    sf.write(p, full, 24000)
    print("  -> %s  %.2fs  %s  flat=%.5f f95=%.0f" % (
        tag, len(full) / 24000.0, "PASS" if (st[0] > 0.002 and st[1] > 1200) else "FAIL", st[0], st[1]),
        flush=True)
    del tts
    import torch, gc
    gc.collect(); torch.cuda.empty_cache()
    return st


def main():
    import torch
    from matcha.utils.audio import mel_spectrogram

    def mk(fmax):
        return functools.partial(mel_spectrogram, n_fft=1920, num_mels=80, sampling_rate=24000,
                                 hop_size=480, win_size=1920, fmin=0, fmax=fmax, center=False)

    print("baseline feat_extractor 参数:", flush=True)
    # 先从 yaml 读原始
    import yaml
    with open(os.path.join(MODEL_DIR, "cosyvoice2.yaml"), encoding="utf-8") as f:
        cfg = f.read()
    for line in cfg.splitlines():
        if "fmax" in line or "n_fft" in line or "hop_size" in line:
            print("   ", line.rstrip(), flush=True)

    results = []
    # 只做两个关键对照组（省显存/时间）：fmax=8000（现状） vs fmax=null（对齐训练）
    results.append(("A_fmax8000", run_case("A_fmax8000", 8000, True, mk)))
    results.append(("B_fmaxnull", run_case("B_fmaxnull", None, True, mk)))
    results.append(("C_fmax8000_fp32", run_case("C_fmax8000_fp32", 8000, False, mk)))

    print("\n" + "=" * 70)
    print("汇总:")
    for tag, st in results:
        if st:
            print("  %-18s flat=%.5f f95=%.0f  %s" % (
                tag, st[0], st[1], "PASS" if (st[0] > 0.002 and st[1] > 1200) else "FAIL"))
        else:
            print("  %-18s <no result>" % tag)


if __name__ == "__main__":
    main()
