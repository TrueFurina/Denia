# -*- coding: utf-8 -*-
"""决定性对照：定位"纯哼鸣(flat=1e-4, f95=820Hz)"的出在哪一步。

对照组（同一参考音、注意只改一个变量）：
  A. zero_shot + 正确 ref_text      <- 当前服务用法
  B. zero_shot + 空 ref_text        <- cross_lingual 等价
  C. 不带参考音（用内置 spk）        <- 只看文本通道能不能出正常语音
  D. A 但把 prompt_wav 换成 24k 真源

同时 dump speech_token / mel / text_token 的关键统计。
注意：需临时绕过 torchcodec，把 load_wav 替换为 soundfile 实现（不改仓库文件）。
"""
import os
import sys
import types

CV_REPO = r"G:\ai对话\11\cosyvoice_repo"
MODEL_DIR = os.environ.get(
    "COSYVOICE_MODEL_DIR",
    r"G:\ai对话\11\cosyvoice_models\models\iic--CosyVoice2-0.5B\snapshots\master",
)
sys.path.insert(0, CV_REPO)
sys.path.insert(0, os.path.join(CV_REPO, "third_party", "Matcha-TTS"))

# ---- 打补丁：用 soundfile 替代 torchcodec 版 load_wav ----
import torch
import torchaudio
import soundfile as sf


def _load_wav_patch(wav, target_sr, min_sr=16000):
    data, sr = sf.read(wav, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    speech = torch.from_numpy(data).unsqueeze(0)
    if sr != target_sr:
        assert sr >= min_sr, f"wav sample rate {sr} must be >= {min_sr}"
        speech = torchaudio.functional.resample(speech, orig_freq=sr, new_freq=target_sr)
    return speech


import cosyvoice.utils.file_utils as fu
fu.load_wav = _load_wav_patch

REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
OUT = r"G:\ai对话\11\data\_diag_cv2b"
TEXT = "今天过得怎么样？"


def stat(x, sr):
    import numpy as np
    n = 1024
    if len(x) < n:
        return None
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
    import numpy as np
    import soundfile as sfw

    os.makedirs(OUT, exist_ok=True)
    from cosyvoice.cli.cosyvoice import CosyVoice2
    tts = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=False)
    fe = tts.frontend

    print("=" * 76)
    print("0) 前端配置")
    print("=" * 76)
    print("text_frontend =", repr(fe.text_frontend))
    print("tokenizer:", type(fe.tokenizer).__name__,
          "vocab", getattr(fe.tokenizer, "vocab_size", "?"),
          "special", [fe.tokenizer.decode([i]) for i in list(fe.allowed_special)[:8]]
          if isinstance(fe.allowed_special, (set, list)) else fe.allowed_special)

    # 文本 token 化检查
    tt, tl = fe._extract_text_token(TEXT)
    print(f"\ntext={TEXT!r}")
    print("  text_token shape", tuple(tt.shape), "len", int(tl))
    print("  ids", tt.flatten().cpu().numpy()[:30].tolist())
    print("  解码回来:", repr(fe.tokenizer.decode(tt.flatten().cpu().numpy().tolist())))

    # 参考音三通道
    print()
    print("=" * 76)
    print("1) 参考音三通道")
    print("=" * 76)
    tok = fe._extract_speech_token(REF)[0]
    t = tok.flatten().cpu().numpy().astype(int)
    print("speech_token", t.shape, "uniq", len(np.unique(t)),
          "min", t.min(), "max", t.max())
    if t.size:
        bc = np.bincount(t)
        print("  top10:", bc.argsort()[::-1][:10].tolist(),
              "counts", bc[bc.argsort()[::-1][:10]].tolist())
    spk = fe._extract_spk_embedding(REF)
    print("spk_embedding", tuple(spk.shape), "norm %.3f" % float(spk.norm()))
    feat, feat_len = fe._extract_speech_feat(REF)
    f = feat.squeeze().cpu().numpy()
    print("speech_feat(mel)", f.shape, "min %.2f max %.2f mean %.2f std %.2f"
          % (f.min(), f.max(), f.mean(), f.std()))

    # ---- 四组对照 ----
    print()
    print("=" * 76)
    print("2) 四组对照生成")
    print("=" * 76)
    n = 0
    cases = [
        ("A_zero_shot_正确ref_text", dict(kind="zero", ref_text="今天过得怎么样")),
        ("B_zero_shot_空ref_text", dict(kind="zero", ref_text="")),
        ("C_无参考音_内置spk", dict(kind="sft", ref_text=None)),
        ("D_zero_shot_流式", dict(kind="zero_stream", ref_text="今天过得怎么样")),
    ]
    for name, cfg in cases:
        try:
            chunks = []
            if cfg["kind"] == "zero":
                gen = tts.inference_zero_shot(TEXT, cfg["ref_text"], REF, stream=False)
            elif cfg["kind"] == "zero_stream":
                gen = tts.inference_zero_shot(TEXT, cfg["ref_text"], REF, stream=True)
            else:
                gen = tts.inference_sft(TEXT, "中文女", stream=False)
            for res in gen:
                chunks.append(res["tts_speech"].cpu().numpy().flatten())
            y = np.concatenate(chunks) if chunks else np.zeros(1)
            p = os.path.join(OUT, f"{name}.wav")
            sfw.write(p, y, tts.sample_rate)
            st = stat(y.astype(np.float64), tts.sample_rate)
            n += 1
            print(f"[{name}] dur={len(y)/tts.sample_rate:5.2f}s  "
                  f"cen={st[0]:6.0f} flat={st[1]:.4f} f95={st[2]:6.0f}  -> {os.path.basename(p)}")
        except Exception as e:
            print(f"[{name}] FAIL {type(e).__name__}: {e}")

    print()
    print("参考: 真源", stat(sfw.read(REF, always_2d=False)[0].astype(np.float64), 16000))


if __name__ == "__main__":
    main()
