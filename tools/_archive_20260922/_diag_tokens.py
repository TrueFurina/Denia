# -*- coding: utf-8 -*-
"""终极诊断：打印 LLM 实际生成的 speech token，看内容是否为空/常量。

判断依据：
  - 正常：token 序列多样（uniq 高），长度 ≈ 文本字数 × 25 token/s × 0.5
  - 坏：token 极短（如只有几个）、或反复出现同一批 id、或全落在 silent_tokens

做法：不调 inference_zero_shot，直接手工走 llm_job 的前半段，
捕获 tts_speech_token_dict，拿到 token 后**手动喂给 token2wav**，
并对比"只用 prompt token"（即模拟"LLM 没产出"）的结果。
"""
import os
import sys
import uuid as _uuid

CV_REPO = r"G:\ai对话\11\cosyvoice_repo"
MODEL_DIR = os.environ.get(
    "COSYVOICE_MODEL_DIR",
    r"G:\ai对话\11\cosyvoice_models\models\iic--CosyVoice2-0.5B\snapshots\master",
)
sys.path.insert(0, CV_REPO)
sys.path.insert(0, os.path.join(CV_REPO, "third_party", "Matcha-TTS"))

import torch
import torchaudio


def _ta_load(path, backend=None):
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32")
    data = data[None, :] if data.ndim == 1 else data.T
    return torch.from_numpy(data), sr


torchaudio.load = _ta_load

import numpy as np
import soundfile as sfw

REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
OUT = r"G:\ai对话\11\data\_diag_tokens"
TEXT = "今天过得怎么样？"
REF_TEXT = "我讨厌吃生的东西特别是那种摆好盘的切片肉类"


def stat(x, sr):
    n = 1024
    if len(x) < n:
        return (0, 0, 0)
    idx = np.arange(0, len(x) - n, 256)
    F = np.stack([x[i:i + n] for i in idx]) * np.hanning(n)[None, :]
    P = np.abs(np.fft.rfft(F, axis=1)) ** 2
    fr = np.fft.rfftfreq(n, 1.0 / sr)
    cum = np.cumsum(P, axis=1) / (P.sum(axis=1, keepdims=True) + 1e-12)
    return (float(np.median((P * fr).sum(axis=1) / (P.sum(axis=1) + 1e-12))),
            float(np.median(np.exp(np.mean(np.log(P + 1e-12), axis=1)) /
                            (np.mean(P, axis=1) + 1e-12))),
            float(np.median(fr[(cum >= 0.95).argmax(axis=1)])))


def main():
    os.makedirs(OUT, exist_ok=True)
    from cosyvoice.cli.cosyvoice import CosyVoice2
    tts = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=True)
    fe = tts.frontend
    model = tts.model

    print("silent_tokens:", getattr(tts, "silent_tokens", "?"),
          "| model.silent_tokens:", sorted(model.silent_tokens)[:10])

    # 构造 model_input（等价 frontend_zero_shot）
    prompt_text = fe.text_normalize(REF_TEXT, split=False, text_frontend=False)
    tt = fe.text_normalize(TEXT, split=True, text_frontend=False)
    print("prompt_text normalized:", repr(prompt_text))
    for i in tt:
        print("tts_text normalized:", repr(i))
        mi = fe.frontend_zero_shot(i, prompt_text, REF, tts.sample_rate, "")
        print("  model_input keys:", sorted(mi.keys()))
        print("  text_token:", tuple(mi["text"].shape),
              "prompt_text_token:", tuple(mi["prompt_text"].shape))
        print("  llm_prompt_speech_token:", tuple(mi["llm_prompt_speech_token"].shape),
              "flow_prompt:", tuple(mi["flow_prompt_speech_token"].shape))
        print("  prompt_speech_feat:", tuple(mi["prompt_speech_feat"].shape))

        u = _uuid.uuid1().hex
        model.tts_speech_token_dict[u] = []
        model.llm_end_dict[u] = False

        # --- 只跑 LLM，捕获 token ---
        model.llm_job(mi["text"], mi["prompt_text"], mi["llm_prompt_speech_token"],
                      mi["llm_embedding"], u)
        toks = model.tts_speech_token_dict[u]
        arr = np.asarray(toks, dtype=int) if toks else np.zeros(0, dtype=int)
        print()
        print("  >>> LLM 生成 token 数:", len(arr))
        if len(arr):
            bc = np.bincount(arr)
            print("      uniq:", len(np.unique(arr)),
                  "top5 ids:", bc.argsort()[::-1][:5].tolist(),
                  "top5 cnt:", bc[bc.argsort()[::-1][:5]].tolist())
            print("      前40:", arr[:40].tolist())
        print("  prompt token 数:", mi["llm_prompt_speech_token"].shape[1])
        break

    # --- 对比：只用 prompt token（模拟 LLM 没产出） ---
    print()
    print("=" * 70)
    print("对比 A：只用 prompt token（=LLM 无产出）")
    print("=" * 70)
    u2 = _uuid.uuid1().hex
    model.flow_cache_dict[u2] = None
    model.mel_overlap_dict[u2] = None
    model.hift_cache_dict[u2] = None
    pt = mi["llm_prompt_speech_token"]
    try:
        out = model.token2wav(pt, mi["flow_prompt_speech_token"],
                              mi["prompt_speech_feat"], mi["flow_embedding"],
                              u2, finalize=True, speed=1.0)
        y = out["tts_speech"].cpu().numpy().flatten() if isinstance(out, dict) else np.asarray(out).flatten()
        p = os.path.join(OUT, "A_only_prompt.wav")
        sfw.write(p, y, tts.sample_rate)
        c, f, f95 = stat(y.astype(np.float64), tts.sample_rate)
        print(f"  dur={len(y)/tts.sample_rate:.2f}s cen={c:.0f} flat={f:.4f} f95={f95:.0f}")
    except Exception as e:
        print("  FAIL:", type(e).__name__, e)

    # --- 对比 B：正常完整流程 ---
    print()
    print("=" * 70)
    print("对比 B：完整 inference_zero_shot")
    print("=" * 70)
    try:
        chunks = [r["tts_speech"].cpu().numpy().flatten()
                  for r in tts.inference_zero_shot(TEXT, REF_TEXT, REF, stream=False,
                                                   speed=1.0, text_frontend=False)]
        y = np.concatenate(chunks)
        p = os.path.join(OUT, "B_full.wav")
        sfw.write(p, y, tts.sample_rate)
        c, f, f95 = stat(y.astype(np.float64), tts.sample_rate)
        print(f"  dur={len(y)/tts.sample_rate:.2f}s cen={c:.0f} flat={f:.4f} f95={f95:.0f}")
    except Exception as e:
        print("  FAIL:", type(e).__name__, e)


if __name__ == "__main__":
    main()
