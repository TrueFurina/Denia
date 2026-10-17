# -*- coding: utf-8 -*-
"""说话人余弦的刻度标定：先建立"同人到什么分、异人到什么分"的尺子，
再回头判断克隆输出的 0.71 究竟算像还是不像。"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
BUNDLE = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio\default_ref_audio.wav"
WECHAT16 = r"G:\ai对话\11\data\native_audio_test\ref_wechat_final\daniya_ref_wechat_16k.wav"
NEW16 = r"G:\ai对话\11\data\native_audio_test\ref_build\daniya_ref_new_16k.wav"


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


def main():
    import onnxruntime
    import torch
    import torchaudio.compliance.kaldi as kaldi
    sess = onnxruntime.InferenceSession(r"H:\daniya_voice_tools\campplus.onnx",
                                        providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    def spk(x16):
        t = torch.from_numpy(np.asarray(x16)).float().unsqueeze(0)
        fb = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
        fb = fb - fb.mean(dim=0, keepdim=True)
        e = sess.run(None, {iname: fb.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
        return e / (np.linalg.norm(e) + 1e-9)

    xs, ssr = load(SRC)
    xg, gsr = load(SIG)
    se = spk(to16k(xs, ssr))
    ge = spk(to16k(xg, gsr))

    def E(p):
        x, sr = load(p)
        return spk(to16k(x, sr))

    # 自己劈成两半：同人、同录音、不同内容 —— 这是"同人上限"
    h = len(xs) // 2
    e1 = spk(to16k(xs[:h], ssr))
    e2 = spk(to16k(xs[h:], ssr))
    # 16k 窄带版（模拟"降采样一次就永久糊"的旧素材）
    e16 = E(WECHAT16)
    e_new16 = E(NEW16)
    e_bundle = E(BUNDLE)

    print("=" * 100)
    print("说话人余弦刻度（campplus，吃 0-8kHz）")
    print("=" * 100)
    print("  同人-同录音前半 vs 后半        %.4f   <- 同人上限（内容不同）" % float(e1 @ e2))
    print("  同人-真源 vs 真源16k窄带版     %.4f   <- 只降采样，同一句话" % float(se @ e16))
    print("  同人-真源 vs 另一版16k处理      %.4f" % float(se @ e_new16))
    print("  同人-真源 vs bundle default_ref %.4f   <- 另一段达妮娅素材" % float(se @ e_bundle))
    print("  异人-真源 vs MiniCPM原生签名    %.4f   <- 异人下限" % float(se @ ge))
    print()
    print("判读：")
    print("  · 同人上限 ~%.2f 与异人下限 ~%.2f 之间，" % (float(e1 @ e2), float(se @ ge)))
    print("    克隆产出落在 0.71 说明：确实像本人，但没到'同人上限'，中间差的那截就是模型自己的味道。")


if __name__ == "__main__":
    main()
