# -*- coding: utf-8 -*-
"""【步骤 0/6】微信源变体处方：把"电平"和"碎片"两个假设做定量验证。

产出 4 个变体，逐个算 campplus 说话人 embedding + s3 语音 token，
再做两两余弦比对 —— 若归一化/去静音后 embedding 明显位移，说明这两步
真正改变 prompt 条件；若纹丝不动，说明该假设无效，别浪费一次烘焙。
"""
import os, sys
import numpy as np
import soundfile as sf

TOOLS = r"H:\daniya_voice_tools"
SRC = os.path.join(TOOLS, "wechat_ref.wav")
OUT = r"G:\ai对话\11\data\native_audio_test\ref_wechat"
os.makedirs(OUT, exist_ok=True)

TARGET_RMS_DBFS = -20.0
LONG_GAP_KEEP = 0.200      # >240ms 的静音压缩到 200ms
CF_MS = 25                 # 拼接交叉淡化
EDGE_THR = 0.005


def f0_median(x, sr, fmin=70, fmax=500, frame=1024, hop=256):
    x = x - x.mean()
    out = []
    for i in range(max(0, (len(x) - frame) // hop)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < 0.01:
            continue
        ac = np.correlate(s, s, "full")[frame - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lo, hi = int(sr / fmax), min(int(sr / fmin), len(ac) - 1)
        if hi <= lo:
            continue
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] < 0.3:
            continue
        out.append(sr / k)
    return float(np.median(out)) if out else 0.0


def frames(x, sr, ms=10):
    w = max(1, int(sr * ms / 1000))
    m = len(x) // w
    return np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1)), w


def trim_edges(x, sr):
    e, w = frames(x, sr)
    idx = np.where(e > EDGE_THR)[0]
    if len(idx) == 0:
        return x
    a = max(0, idx[0] * w - int(0.03 * sr))
    b = min(len(x), (idx[-1] + 1) * w + int(0.03 * sr))
    return x[a:b]


def compress_gaps(x, sr):
    """把长静音压到 LONG_GAP_KEEP，接缝做交叉淡化。"""
    e, w = frames(x, sr)
    voiced = e > max(e.max() * 0.05, EDGE_THR)
    keep = int(LONG_GAP_KEEP * 1000 / 10)
    cf = int(CF_MS * sr / 1000)
    pieces = []
    i, n = 0, len(voiced)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            pieces.append(x[i * w:min(len(x), j * w)])
            i = j
        else:
            j = i
            while j < n and not voiced[j]:
                j += 1
            if (j - i) > keep:
                # 保留前半段静音（自然衰减），砍掉多余
                pieces.append(np.zeros(keep * w, np.float32))
            else:
                pieces.append(x[i * w:j * w])
            i = j
    out = pieces[0]
    for p in pieces[1:]:
        if len(out) < cf or len(p) < cf:
            out = np.concatenate([out, p]); continue
        a = np.linspace(0, 1, cf, dtype=np.float32)
        head = out[-cf:] * (1 - a) + p[:cf] * a
        out = np.concatenate([out[:-cf], head, p[cf:]])
    return out.astype(np.float32)


def norm_rms(x, target_dbfs=TARGET_RMS_DBFS, peak_ceiling=0.95):
    r = np.sqrt(np.mean(x ** 2))
    if r <= 0:
        return x
    g = (10 ** (target_dbfs / 20)) / r
    y = x * g
    pk = np.abs(y).max()
    if pk > peak_ceiling:
        y = y * (peak_ceiling / pk)
    return y.astype(np.float32)


def stat(x, sr, tag):
    e, w = frames(x, sr)
    v = e > max(e.max() * 0.05, EDGE_THR)
    r = float(np.sqrt(np.mean(x ** 2)))
    return dict(tag=tag, dur=len(x) / sr, sr=sr,
                rms=r, dbfs=20 * np.log10(max(r, 1e-9)),
                peak=float(np.abs(x).max()), voiced=float(v.mean() * 100),
                f0=f0_median(x, sr))


def main():
    x, sr = sf.read(SRC, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    print("=" * 100)
    print("微信源变体处方")
    print("=" * 100)

    v1 = trim_edges(x, sr)
    v2 = norm_rms(v1)
    v3t = compress_gaps(v1, sr)
    v3 = norm_rms(v3t)

    variants = [("V0_raw", x), ("V1_trim", v1), ("V2_trim_norm", v2),
                ("V3_trim_degap_norm", v3)]

    print(f"{'变体':<22}{'时长':>8}{'rms':>9}{'dBFS':>9}{'peak':>8}{'浊音%':>8}{'F0':>8}")
    print("-" * 100)
    rows = []
    for tag, y in variants:
        s = stat(y, sr, tag)
        rows.append((tag, y, s))
        print(f"{tag:<22}{s['dur']:>8.2f}{s['rms']:>9.5f}{s['dbfs']:>9.1f}"
              f"{s['peak']:>8.3f}{s['voiced']:>8.1f}{s['f0']:>8.1f}")

    # ---- 写出 16k 变体，供 extract 使用 ----
    print()
    for tag, y, s in rows:
        p = os.path.join(OUT, "wechat_%s_16k.wav" % tag.split("_", 1)[1])
        sf.write(p, y, sr, subtype="PCM_16")
        print("  写出: %s" % p)

    # ---- campplus 说话人 embedding 比对 ----
    print()
    print("=" * 100)
    print("campplus 说话人 embedding 余弦比对（>0.6 同人；同一人内部差异看位移量）")
    print("=" * 100)
    try:
        import torch, onnxruntime
        import torchaudio.compliance.kaldi as kaldi
    except Exception as e:
        print("  !! 缺依赖:", e); return

    sess = onnxruntime.InferenceSession(os.path.join(TOOLS, "campplus.onnx"),
                                        providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    embs = {}
    for tag, y, s in rows:
        t = torch.from_numpy(y).float().unsqueeze(0)
        if sr != 16000:
            import torchaudio
            t = torchaudio.transforms.Resample(sr, 16000)(t)
        f = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
        f = f - f.mean(dim=0, keepdim=True)
        e = sess.run(None, {name: f.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
        embs[tag] = e / (np.linalg.norm(e) + 1e-9)

    tags = [t for t, _, _ in rows]
    print("             " + "".join(f"{t[:12]:>13}" for t in tags))
    for a in tags:
        line = f"{a[:12]:>13}"
        for b in tags:
            line += f"{float(embs[a] @ embs[b]):>13.4f}"
        print(line)

    print()
    print("参照系：V0_raw 是现存线上缓存(247.82MB/md5 146fdbe7)的输入。")
    print("  1 - cos(V0, Vx) 越大 => 该处理对 prompt 的改动越大，值得重烤。")

    # ---- s3 语音 token 数 ----
    print()
    print("=" * 100)
    print("s3 语音 token 数（决定 cache 帧数/大小）")
    print("=" * 100)
    try:
        import s3tokenizer
        tok = s3tokenizer.load_model(os.path.join(TOOLS, "speech_tokenizer_v2_25hz.onnx"))
        tok.eval()
        for tag, y, s in rows:
            t = torch.from_numpy(y).float()
            if sr != 16000:
                import torchaudio
                t = torchaudio.transforms.Resample(sr, 16000)(t)
            ml = s3tokenizer.log_mel_spectrogram(t)
            ml, ln = s3tokenizer.padding([ml])
            tk, _ = tok.quantize(ml, ln)
            print(f"  {tag:<22} T_token={tk.shape[1]:5d}  "
                  f"(对应 prompt_mel≈{(tk.shape[1]-3)*2} 帧 ≈ {(tk.shape[1]-3)*2*0.02:.2f}s)")
    except Exception as e:
        print("  !! s3tokenizer 不可用:", e)

    print()
    print("=" * 100)
    print("输出目录:", OUT)


if __name__ == "__main__":
    main()
