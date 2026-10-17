# -*- coding: utf-8 -*-
"""带对照组的音色判定：产出到底更像「达妮娅」还是更像「MiniCPM 原生嗓」。

为什么必须做对照组
------------------
只算「产出 ↔ 达妮娅 = 0.59」无法下结论：本项目产出被 8kHz 带宽卡住，说话人
空间被压缩，导致**原生嗓对着达妮娅也能拿到 ~0.69**（早先标定 B_NATIVE cos↔SRC
=0.6891，而应用产出 0.7103，仅差 0.02）⇒ 这个指标单独用几乎没有分辨力。

正确做法：同一条流水线里同时量
    Z = 原生签名 ↔ 达妮娅        ← 对照组基线（异人参照应落在这里）
    X = 产出 ↔ 达妮娅
    Y = 产出 ↔ 原生签名
判读：X 明显 > Y ⇒ 偏达妮娅；Y 明显 > X ⇒ 偏原生；X≈Y≈Z ⇒ 指标无分辨力（不能下结论）。

用法（须 client_venv：onnxruntime/torchaudio）：
    H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe tools/_measure_ab_native.py
"""
import os
import sys
import glob

import numpy as np

ROOT = r"G:\ai对话\11"
OUTDIR = os.path.join(ROOT, "data", "duplex_out")
CAMPPLUS = r"H:\daniya_voice_tools\campplus.onnx"

REFS = [
    ("达妮娅真源", os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")),
    ("原生签名", os.path.join(ROOT, "voice_baseline", "20260922_144014",
                             "02_ref_minicpm_signature.wav")),
    ("原生底色B", os.path.join(ROOT, "data", "native_audio_test", "final_sample",
                             "B_NATIVE.wav")),
]


def build():
    import wave as _wave
    import onnxruntime as ort
    import torch
    import torchaudio
    import torchaudio.compliance.kaldi as kaldi
    sess = ort.InferenceSession(CAMPPLUS, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name

    def _load_mono_16k(p):
        with _wave.open(p, "rb") as w:
            nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            raise RuntimeError("非 16bit PCM: %s (sampwidth=%d)" % (p, sw))
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if nch > 1:
            a = a.reshape(-1, nch).mean(axis=1)
        t = torch.from_numpy(a).unsqueeze(0)
        if sr != 16000:
            t = torchaudio.transforms.Resample(sr, 16000)(t)
        return t

    def spk(p):
        wav = _load_mono_16k(p)
        feat = kaldi.fbank(wav, num_mel_bins=80, dither=0, sample_frequency=16000)
        feat = feat - feat.mean(0, keepdim=True)
        v = np.asarray(sess.run(None, {name: feat.unsqueeze(0).numpy()})[0]).reshape(-1)
        v = v.astype(np.float64)
        return v / (np.linalg.norm(v) + 1e-9)

    return spk


def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main():
    spk = build()
    ref = {}
    print("① 载入基准（同一条流水线，参数完全一致）")
    for tag, p in REFS:
        if os.path.isfile(p):
            ref[tag] = spk(p)
            print("   ✅ %-10s %s" % (tag, os.path.basename(p)))
        else:
            print("   ⚠️ 缺 %-10s %s" % (tag, p))
    if "达妮娅真源" not in ref:
        return 2

    print("\n② 【对照组】基准之间互测 —— 这一步决定指标有没有分辨力")
    tags = list(ref.keys())
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            print("   %-10s ↔ %-10s = %.4f" % (tags[i], tags[j], cos(ref[tags[i]], ref[tags[j]])))
    if "原生签名" in ref:
        z = cos(ref["达妮娅真源"], ref["原生签名"])
        print("\n   ⇒ 异人基线 Z（达妮娅 ↔ 原生签名）= **%.4f**" % z)
        print("     若产出 ↔达妮娅 只是略高于 Z，说明分辨力不足，不能判'是达妮娅'。")
    else:
        z = None

    subs = sorted(os.listdir(OUTDIR))
    d = os.path.join(OUTDIR, subs[-1])
    ws = sorted(glob.glob(os.path.join(d, "*.wav")))
    print("\n③ 产出 vs 双基准（会话 %s，%d 句）" % (subs[-1], len(ws)))
    print("   %-38s %9s %9s   %s" % ("文件", "↔达妮娅", "↔原生签名", "偏向"))
    print("   " + "-" * 80)
    xs, ys = [], []
    for w in ws:
        try:
            v = spk(w)
        except Exception as e:
            print("   %-38s 抽取失败 %r" % (os.path.basename(w)[:36], e))
            continue
        x = cos(v, ref["达妮娅真源"])
        xs.append(x)
        y = cos(v, ref["原生签名"]) if "原生签名" in ref else float("nan")
        ys.append(y)
        if np.isnan(y):
            verdict = "（无对照）"
        elif x - y > 0.05:
            verdict = "→ 达妮娅"
        elif y - x > 0.05:
            verdict = "→ 原生嗓"
        else:
            verdict = "→ 难分(差<0.05)"
        print("   %-38s %9.4f %9.4f   %s"
              % (os.path.basename(w)[:36], x, y, verdict))
    print("   " + "-" * 80)
    if xs:
        mx = sum(xs) / len(xs)
        print("   均值 ↔达妮娅 = %.4f" % mx)
        if ys and not np.isnan(ys[0]):
            my = sum(ys) / len(ys)
            print("   均值 ↔原生签名 = %.4f" % my)
            print("\n④ 判定")
            if z is not None:
                print("   异人基线 Z = %.4f ｜ 产出↔达妮娅 = %.4f ｜ 产出↔原生 = %.4f" % (z, mx, my))
                if mx - my > 0.05:
                    print("   ⇒ 产出**更像达妮娅**（差距 %.4f）" % (mx - my))
                elif my - mx > 0.05:
                    print("   ⇒ 产出**更像 MiniCPM 原生嗓**（差距 %.4f）—— 你的耳朵是对的" % (my - mx))
                else:
                    print("   ⇒ 差距仅 %.4f ⇒ **该指标分辨力不足，无法判定**，需换判据" % abs(mx - my))
    return 0


if __name__ == "__main__":
    sys.exit(main())
