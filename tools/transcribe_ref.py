# -*- coding: utf-8 -*-
"""转录参考音内容 —— 供云端档（百炼 dashscope_cosy）的 reference_text 使用。

云端克隆只认 reference_audio + reference_text 严格配对；文本错配会被静默吞掉
（HTTP 200 空响应体），因此必须拿到与音频逐句对应的真实文本。

用法: python tools/transcribe_ref.py <wav> [<wav> ...]
"""
import os
import subprocess
import sys

import numpy as np
import sherpa_onnx

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
MODEL = r"G:\ai对话\11\models\sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"


def load16k(path):
    r = subprocess.run([FFMPEG, "-v", "quiet", "-i", path, "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True)
    if not r.stdout:
        raise RuntimeError("ffmpeg 解码失败: " + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout, dtype="<i2")


def build_recognizer(endpoint=False):
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=MODEL + "/tokens.txt",
        encoder=MODEL + "/encoder-epoch-99-avg-1.int8.onnx",
        decoder=MODEL + "/decoder-epoch-99-avg-1.int8.onnx",
        joiner=MODEL + "/joiner-epoch-99-avg-1.int8.onnx",
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        enable_endpoint_detection=endpoint,
    )


def recognize_whole(rec, pcm16):
    s = rec.create_stream()
    wav = pcm16.astype(np.float32) / 32768.0
    s.accept_waveform(sample_rate=16000, waveform=wav)
    # 尾部补静音，帮助收敛
    s.accept_waveform(sample_rate=16000, waveform=np.zeros(16000, np.float32))
    while rec.is_ready(s):
        rec.decode_stream(s)
    res = rec.get_result_all(s)
    return (getattr(res, "text", "") or "").strip()


def recognize_by_segment(rec, pcm16, sil_thr=0.006, min_sil=0.18, min_seg=0.8):
    """按能量静音切分后逐段识别，再拼接（拼接类参考音更准）。"""
    w = 160  # 10ms
    n = len(pcm16) // w
    e = np.sqrt(np.mean((pcm16[:n * w].astype(np.float32) / 32768.0).reshape(n, w) ** 2, axis=1))
    voiced = e > sil_thr
    segs = []
    i = 0
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if (j - i) * 0.01 >= min_seg:
                segs.append((i * w, j * w))
            i = j
        else:
            i += 1
    out = []
    for a, b in segs:
        t = recognize_whole(rec, pcm16[a:b])
        if t:
            out.append(t)
    return out, [(a / 16000, b / 16000) for a, b in segs]


def main():
    paths = sys.argv[1:]
    if not paths:
        print("用法: python tools/transcribe_ref.py <wav> [...]")
        sys.exit(1)
    rec = build_recognizer(endpoint=False)
    for p in paths:
        if not os.path.exists(p):
            print("MISSING", p)
            continue
        pcm = load16k(p)
        dur = len(pcm) / 16000
        print("=" * 96)
        print(f"文件: {p}")
        print(f"时长: {dur:.2f}s  ({len(pcm)} samples @16k)")
        print("-" * 96)
        whole = recognize_whole(rec, pcm)
        print(f"[整段识别] {whole}")
        print()
        segs, spans = recognize_by_segment(rec, pcm)
        print(f"[分段识别] 共 {len(segs)} 段:")
        for (t, (a, b)) in zip(segs, spans):
            print(f"   {a:6.2f}-{b:6.2f}s  {t}")
        print()
        print(f"[拼接文本] {''.join(segs)}")


if __name__ == "__main__":
    main()
