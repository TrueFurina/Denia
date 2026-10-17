# -*- coding: utf-8 -*-
"""质检参考音的拼接构成：逐段说话人一致性 + 段边界韵律痕迹。

目的：确认拼进 cache 的每一段都是"她本人"，且段间静音不过长（避免合成语音学到
频繁停顿的韵律）。用 campplus 说话人向量做两两余弦 + 与全局向量比对。
"""
import os
import sys

import numpy as np
import soundfile as sf
import onnxruntime
import torch
import torchaudio
import torchaudio.compliance.kaldi as kaldi

TOOLS = r"H:\daniya_voice_tools"
CAMPPLUS = os.path.join(TOOLS, "campplus.onnx")


def load(p):
    x, sr = sf.read(p, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def resample(x, sr_in, sr_out):
    if sr_in == sr_out:
        return x
    t = torch.from_numpy(x).float()
    return torchaudio.transforms.Resample(sr_in, sr_out)(t).numpy()


_sess = None


def embed(x16):
    global _sess
    if _sess is None:
        o = onnxruntime.SessionOptions()
        o.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        o.intra_op_num_threads = 1
        _sess = onnxruntime.InferenceSession(CAMPPLUS, sess_options=o,
                                             providers=["CPUExecutionProvider"])
    t = torch.from_numpy(x16).float().unsqueeze(0)
    f = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
    f = f - f.mean(dim=0, keepdim=True)
    name = _sess.get_inputs()[0].name
    return np.asarray(_sess.run(None, {name: f.unsqueeze(0).numpy()})[0]).reshape(-1)


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def split_segments(x, sr, thr=0.006, min_gap=0.06, min_seg=0.25):
    w = max(1, int(sr * 0.01))
    n = len(x) // w
    e = np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1))
    v = e > thr
    segs = []
    i = 0
    while i < n:
        if v[i]:
            j = i
            while j < n and v[j]:
                j += 1
            if (j - i) * 0.01 >= min_seg:
                segs.append((i * w, j * w))
            i = j
        else:
            i += 1
    return segs


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        r"G:\ai对话\11\data\native_audio_test\ref_final\daniya_ref_final_48k.wav"
    whole_ref = sys.argv[2] if len(sys.argv) > 2 else \
        r"G:\ai对话\11\data\native_audio_test\src_candidates\V0827_全片27.0s_48k.wav"

    x, sr = load(path)
    print("=" * 100)
    print(f"参考音: {os.path.basename(path)}   {len(x)/sr:.2f}s @{sr}Hz")
    print("=" * 100)

    segs = split_segments(x, sr)
    print(f"检测到 {len(segs)} 个有声段：\n")
    print(f"{'#':>2} {'区间':>16} {'时长':>6} {'到下一段静音':>12}  与全局余弦  与均值余弦")
    print("-" * 100)

    gw, gr = load(whole_ref)
    gw16 = resample(gw, gr, 16000)
    gemb = embed(gw16)

    embs = []
    prev_end = None
    rows = []
    for k, (a, b) in enumerate(segs):
        seg = x[a:b]
        s16 = resample(seg, sr, 16000)
        if len(s16) < 16000 * 0.15:
            continue
        e = embed(s16)
        embs.append(e)
        gap = (a - prev_end) / sr if prev_end is not None else 0.0
        rows.append((k, a / sr, b / sr, (b - a) / sr, gap, e))
        prev_end = b

    mean_e = np.mean(embs, axis=0) if embs else None
    for k, a, b, d, gap, e in rows:
        print(f"{k:>2} {a:6.2f}-{b:6.2f}s {d:5.2f}s {gap:11.3f}s      "
              f"{cos(e, gemb):7.3f}     {cos(e, mean_e):7.3f}")

    print()
    if len(embs) > 1:
        M = np.array([[cos(embs[i], embs[j]) for j in range(len(embs))] for i in range(len(embs))])
        off = M[~np.eye(len(embs), dtype=bool)]
        print(f"段间两两余弦: 均值={off.mean():.3f}  最小={off.min():.3f}  最大={off.max():.3f}")
        bad = [(i, j, M[i, j]) for i in range(len(embs)) for j in range(i + 1, len(embs)) if M[i, j] < 0.55]
        if bad:
            print("  ⚠ 低相似度段对（疑似混入他人语音）:")
            for i, j, c in bad:
                print(f"     seg{i} <-> seg{j}: {c:.3f}")
        else:
            print("  ✅ 所有段两两相似度均 >= 0.55 —— 同一声源")
    print()
    print(f"与全片（{os.path.basename(whole_ref)}）余弦: "
          f"{np.mean([cos(e, gemb) for e in embs]):.3f}（均值）")
    print("标定参考：同一人自比通常 > 0.75；异人通常 < 0.40")


if __name__ == "__main__":
    main()
