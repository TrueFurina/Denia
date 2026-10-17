# -*- coding: utf-8 -*-
"""定位 CosyVoice2 产出"纯哼鸣(flat=1e-4, f95=820Hz)"的根因。

假设（按可能性排序）：
  H1. 输出实际是"参考音的某种变形"（如只跑了 tokenizer/声码器，没跑 LLM）
  H2. flow / hift 的参数不匹配 ⇒ 谱被严重压缩（820Hz 天花板很像 n_fft 与 sr 配错）
  H3. speech_token 全 0 或常量 ⇒ 模型只输出稳定元音
  H4. 参考音文本/embedding 异常 ⇒ 模型读不出内容

做法：直接调用 CosyVoice2 内部 API（不走 HTTP），逐段 dump 中间量。
"""
import os
import sys

CV_REPO = r"G:\ai对话\11\cosyvoice_repo"
MODEL_DIR = os.environ.get(
    "COSYVOICE_MODEL_DIR",
    r"G:\ai对话\11\cosyvoice_models\models\iic--CosyVoice2-0.5B\snapshots\master",
)
sys.path.insert(0, CV_REPO)
sys.path.insert(0, os.path.join(CV_REPO, "third_party", "Matcha-TTS"))

REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
OUT = r"G:\ai对话\11\data\_diag_cv2"
TEXT = "今天过得怎么样？"


def main():
    import torch
    import numpy as np
    import soundfile as sf

    os.makedirs(OUT, exist_ok=True)
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
    print("MODEL_DIR exists:", os.path.isdir(MODEL_DIR))

    from cosyvoice.cli.cosyvoice import CosyVoice2
    from cosyvoice.utils.file_utils import load_wav

    tts = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=False)
    print("sample_rate:", tts.sample_rate)
    print("frontend.sample_rate:", getattr(tts.frontend, "sample_rate", "?"))

    # ---- 1) 参考音三条通道各自实际拿到什么分辨率 ----
    print()
    print("=" * 70)
    print("1) 参考音三条通道")
    print("=" * 70)
    import inspect
    fe = tts.frontend
    for name in ("_extract_speech_token", "_extract_spk_embedding", "_extract_speech_feat"):
        fn = getattr(fe, name, None)
        if fn is None:
            print(name, "NOT FOUND")
            continue
        try:
            src = inspect.getsource(fn)
            print(f"--- {name}")
            print("    " + "\n    ".join(src.strip().split("\n")[:8]))
        except Exception as e:
            print(name, "src err", e)

    token, tok_sr = fe._extract_speech_token(REF)
    print()
    print("speech_token shape", tuple(token.shape))
    t = token.flatten().cpu().numpy()
    print("  token dtype", t.dtype, "min", t.min(), "max", t.max(),
          "uniq", len(np.unique(t)) if t.size else 0)
    top = np.bincount(t.astype(int)).argsort()[::-1][:10] if t.size else []
    print("  top10 ids", top.tolist())
    print("  首 40 个:", t[:40].tolist())

    spk = fe._extract_spk_embedding(REF)
    print("spk_embedding shape", tuple(spk.shape), "norm", float(spk.norm()))

    feat, feat_len = fe._extract_speech_feat(REF)
    print("speech_feat shape", tuple(feat.shape))
    f = feat.squeeze().cpu().numpy()
    print("  mel min %.3f max %.3f mean %.3f" % (f.min(), f.max(), f.mean()))

    # ---- 2) 直接跑 inference_zero_shot，dump 每步 ----
    print()
    print("=" * 70)
    print("2) inference_zero_shot 逐步")
    print("=" * 70)
    chunks = []
    for i, res in enumerate(tts.inference_zero_shot(
            TEXT, "今天过得怎么样", REF, stream=False)):
        a = res["tts_speech"]
        print(f"  chunk{i} shape {tuple(a.shape)} "
              f"dur={a.shape[-1]/tts.sample_rate:.2f}s")
        chunks.append(a.cpu().numpy().flatten())
    if chunks:
        y = np.concatenate(chunks)
        p = os.path.join(OUT, "direct_zero_shot.wav")
        sf.write(p, y, tts.sample_rate)
        print("wrote", p, len(y) / tts.sample_rate, "s")

    # ---- 3) 同样体检 ----
    def stat(x, sr):
        n = 1024
        if len(x) < n:
            return None
        idx = np.arange(0, len(x) - n, 256)
        F = np.stack([x[i:i + n] for i in idx]) * np.hanning(n)[None, :]
        P = np.abs(np.fft.rfft(F, axis=1)) ** 2
        fr = np.fft.rfftfreq(n, 1.0 / sr)
        cum = np.cumsum(P, axis=1) / (P.sum(axis=1, keepdims=True) + 1e-12)
        return dict(
            cen=float(np.median((P * fr).sum(axis=1) / (P.sum(axis=1) + 1e-12))),
            flat=float(np.median(np.exp(np.mean(np.log(P + 1e-12), axis=1)) /
                                 (np.mean(P, axis=1) + 1e-12))),
            f95=float(np.median(fr[(cum >= 0.95).argmax(axis=1)])),
        )

    print()
    for p in [REF, os.path.join(OUT, "direct_zero_shot.wav")]:
        if os.path.isfile(p):
            x, sr = sf.read(p, always_2d=False)
            if x.ndim > 1:
                x = x.mean(axis=1)
            st = stat(x.astype(np.float64), sr)
            print(f"{os.path.basename(p):<28} sr={sr} {st}")


if __name__ == "__main__":
    main()
