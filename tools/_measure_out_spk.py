# -*- coding: utf-8 -*-
"""量「实际产出」的音色：把 duplex_out 落盘的 wav 用 campplus 比达妮娅/原生签名余弦。

刻度（本项目已标定）：
    达妮娅真源  ↔  同人上限(跨句) ≈ 0.728
    达妮娅  ↔  MiniCPM 原生签名   ≈ 0.195   ← 异人下限
⇒ 产出余弦 ≥0.60 可判"是达妮娅"；≈0.19~0.30 判"就是原生嗓"。

用法（必须用 client_venv，它才有 onnxruntime/torchaudio）：
    H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe tools/_measure_out_spk.py
"""
import os
import sys
import glob

import numpy as np

ROOT = r"G:\ai对话\11"
OUTDIR = os.path.join(ROOT, "data", "duplex_out")
CAMPPLUS = r"H:\daniya_voice_tools\campplus.onnx"
REF_DANIYA = os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")
REF_SIG = os.path.join(ROOT, "assets", "voices", "ref_minicpm_signature.wav")


def build():
    import wave as _wave
    import onnxruntime as ort
    import torch
    import torchaudio
    import torchaudio.compliance.kaldi as kaldi
    sess = ort.InferenceSession(CAMPPLUS, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name

    def _load_mono_16k(p):
        # 绕开 torchcodec：本 venv 的 torchaudio.load 依赖未安装的 torchcodec，
        # 直接用标准库 wave 读 16bit PCM（落盘的 wav 正是 int16）。
        with _wave.open(p, "rb") as w:
            nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            raise RuntimeError("仅支持 16bit PCM: %s (sampwidth=%d)" % (p, sw))
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if nch > 1:
            a = a.reshape(-1, nch).mean(axis=1)
        t = torch.from_numpy(a).unsqueeze(0)          # (1, N)
        if sr != 16000:
            t = torchaudio.transforms.Resample(sr, 16000)(t)
        return t                                       # 16k 单声道

    def wav_spk(p):
        wav = _load_mono_16k(p)
        feat = kaldi.fbank(wav, num_mel_bins=80, dither=0, sample_frequency=16000)
        feat = feat - feat.mean(0, keepdim=True)
        v = np.asarray(sess.run(None, {name: feat.unsqueeze(0).numpy()})[0]).reshape(-1)
        v = v.astype(np.float64)
        return v / (np.linalg.norm(v) + 1e-9)

    return wav_spk


def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main():
    wav_spk = build()
    ref = {}
    for tag, p in (("达妮娅真源", REF_DANIYA), ("原生签名", REF_SIG)):
        if os.path.isfile(p):
            ref[tag] = wav_spk(p)
            print("基准载入：%s  %s" % (tag, os.path.basename(p)))
        else:
            print("⚠️ 缺基准：%s" % p)
    if not ref:
        return 2

    subs = sorted(os.listdir(OUTDIR))
    if not subs:
        print("没有落盘会话")
        return 1
    d = os.path.join(OUTDIR, subs[-1])
    ws = sorted(glob.glob(os.path.join(d, "*.wav")))
    print("\n会话目录 %s（%d 个 wav）" % (subs[-1], len(ws)))
    print("=" * 92)
    print("%-42s %8s %10s %10s  %s" % ("文件", "时长", "↔达妮娅", "↔原生", "判定"))
    print("-" * 92)
    vals = []
    for w in ws:
        try:
            v = wav_spk(w)
        except Exception as e:
            print("  %-40s 抽向量失败 %r" % (os.path.basename(w), e))
            continue
        cd = cos(v, ref.get("达妮娅真源", v))
        cs = cos(v, ref.get("原生签名", v)) if "原生签名" in ref else 0.0
        vals.append(cd)
        dur = os.path.getsize(w) / 2 / 24000.0
        if cd >= 0.60:
            verdict = "✅ 达妮娅"
        elif cd >= 0.45:
            verdict = "🟡 偏达妮娅/有损"
        else:
            verdict = "❌ 原生嗓"
        print("%-42s %7.2fs %10.4f %10.4f  %s"
              % (os.path.basename(w)[:40], dur, cd, cs, verdict))
    print("-" * 92)
    if vals:
        print("均值 ↔达妮娅 = %.4f   最小 %.4f   最大 %.4f"
              % (sum(vals) / len(vals), min(vals), max(vals)))
        print("\n刻度参照：同人上限≈0.728（跨句）  异人下限≈0.195（达妮娅↔原生）")
        print("判读：均值≥0.60 ⇒ 克隆在工作；≈0.19~0.35 ⇒ 跑的就是 MiniCPM 原生嗓。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
