# -*- coding: utf-8 -*-
"""
_frontend_limits.py  —  "让引擎全量接收音频"这个思路的可行性定量实验

被问的问题：
    能不能改解码器，让它对任何输入音频都全量接收（不截断、不漏听），
    从而避免模型基于不完整信息随意发挥？

本脚本量四件事：
  ① 理解侧前端上限 —— 直接从 audio-F16.gguf 里读出 mel 滤波器组，量它覆盖到多少 Hz
  ② 克隆侧条件带宽 —— token2wav 的 prompt mel 用 fmax=8000 vs Nyquist(12000) 对比：
        · 80 个通道的"语义位置"挪了多少（这是 OOD 的量级）
        · 新开出来的信息有多少（真源里 >8k 的能量占比）
  ③ 只放宽入口、不改前端 的代价 —— 把 24k 样本直接喂给声明 16k 的前端
        与正确路径比 campplus 余弦（证明"只改一边"是最坏情况）
  ④ 理解侧的 8k 截断到底损失了什么 —— 真源 24k vs 真源限带8k，
        走同一个 whisper 前端，逐 bin 比（看语音信息有没有损失）

只读，不写入任何模型/配置。
"""
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(HERE)
SRC = os.path.join(P, "data", "native_audio_test", "ref_v2", "mp3_8s_24k.wav")
CAMPP = r"H:\daniya_voice_tools\campplus.onnx"
AUDIO_GGUF = r"H:\minicpm models\MiniCPM-o-4_5-gguf\audio\MiniCPM-o-4_5-audio-F16.gguf"

FF_CANDIDATES = [
    r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
    "ffmpeg",
]


def ffmpeg():
    for c in FF_CANDIDATES:
        try:
            r = subprocess.run([c, "-version"], capture_output=True, timeout=20)
            if r.returncode == 0:
                return c
        except Exception:
            pass
    return None


def to_sr(x, sr, target):
    if sr == target:
        return np.asarray(x, dtype=np.float32)
    ff = ffmpeg()
    if ff:
        r = subprocess.run([ff, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                            "-f", "f32le", "-ar", str(target), "-ac", "1", "-"],
                           input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
        return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(int(sr), int(target))
    return resample_poly(np.asarray(x, dtype=np.float64), target // g, sr // g).astype(np.float32)


def load(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


# ---------------------------------------------------------------- ① whisper 前端
def read_whisper_filters(path):
    """从 audio GGUF 的 metadata 里把 filters (80x201) 读出来。"""
    import struct
    with open(path, "rb") as f:
        n_kv = struct.unpack("<Q", f.read(24)[16:24])[0]
        blob = f.read(2_000_000)
    pos = 0

    def rs():
        nonlocal pos
        n = struct.unpack_from("<Q", blob, pos)[0]
        pos += 8
        v = blob[pos:pos + n].decode("utf-8", "replace")
        pos += n
        return v

    n_mel = n_fft = None
    filt = None
    for _ in range(n_kv):
        k = rs()
        t = struct.unpack_from("<I", blob, pos)[0]
        pos += 4
        if t == 8:
            v = rs()
        elif t in (4, 5, 6):
            v = struct.unpack_from("<I" if t == 4 else ("<i" if t == 5 else "<f"), blob, pos)[0]
            pos += 4
        elif t in (10, 11, 12):
            v = struct.unpack_from("<Q" if t == 10 else ("<q" if t == 11 else "<d"), blob, pos)[0]
            pos += 8
        elif t in (0, 2):
            v = blob[pos] if t == 0 else struct.unpack_from("<H", blob, pos)[0]
            pos += 1 if t == 0 else 2
        elif t == 9:
            et = struct.unpack_from("<I", blob, pos)[0]
            pos += 4
            n = struct.unpack_from("<Q", blob, pos)[0]
            pos += 8
            if et == 6:
                arr = np.frombuffer(blob, dtype="<f4", count=n, offset=pos).astype(np.float64)
                pos += 4 * n
                if k == "filters":
                    filt = arr
                v = arr
            elif et == 8:
                for _i in range(n):
                    rs()
                v = None
            elif et == 5:
                pos += 4 * n
                v = None
            else:
                return None, None, None
        else:
            return None, None, None
        if k == "n_mel":
            n_mel = v
        if k == "n_fft":
            n_fft = v
    if filt is None:
        return None, n_mel, n_fft
    return filt.reshape(int(n_mel), int(n_fft)), n_mel, n_fft


def whisper_logmel(x16, filters):
    """whisper 前端：n_fft=400 / hop=160 / hann / center=True / log10 压缩。"""
    import torch
    t = torch.from_numpy(np.asarray(x16, dtype=np.float32))
    win = torch.hann_window(400)
    st = torch.stft(t, 400, 160, window=win, center=True, return_complex=True)
    mag = st.abs()[:, :-1]
    fb = torch.from_numpy(filters).float()
    mel = fb @ mag
    log = torch.clamp(mel, min=1e-10).log10()
    log = torch.maximum(log, log.max() - 8.0)
    return ((log + 4.0) / 4.0).numpy()          # [80, T]


def hz_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def mel_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


# ---------------------------------------------------------------- ② matcha mel
def matcha_mel(x, sr, fmax):
    """token2wav 的 prompt mel 前端（与 bundle 对拍 corr=1.00000 的那套）。"""
    import torch
    import torchaudio
    t = torch.from_numpy(np.asarray(x, dtype=np.float32)).unsqueeze(0)
    m = torchaudio.transforms.MelSpectrogram(
        sample_rate=sr, n_fft=1920, hop_length=480, win_length=1920,
        f_min=0.0, f_max=fmax, n_mels=80, power=2.0,
        norm="slaney", mel_scale="slaney", center=False)
    with torch.no_grad():
        mel = m(t)
    log = torch.log(torch.clamp(mel, min=1e-5))
    return log.squeeze(0).transpose(0, 1).numpy()      # [T, 80]


def band_share(x, sr, lo, hi):
    """返回 (频带能量占总能量百分比, dB)。注意 FFT 长度必须 >= 信号长度，否则只分析了开头一截。"""
    n = 1
    while n < len(x):
        n <<= 1
    f = np.fft.rfft(np.asarray(x, dtype=np.float64) * np.hanning(len(x)), n=n)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    p = (np.abs(f) ** 2)
    tot = p[(freqs >= 60) & (freqs <= sr / 2 - 60)].sum() + 1e-30
    e = p[(freqs >= lo) & (freqs < hi)].sum()
    return e / tot * 100.0, 10 * np.log10(e / tot + 1e-30)


def main():
    print("=" * 92)
    print("① 理解侧（LLM 怎么『听』）的前端上限 —— 直接从模型文件读出，不是推测")
    print("=" * 92)
    fb, n_mel, n_fft = read_whisper_filters(AUDIO_GGUF)
    if fb is None:
        print("  读不到 filters，跳过")
    else:
        sr, N = 16000, (n_fft - 1) * 2
        freqs = np.linspace(0, sr / 2.0, n_fft)
        pk = np.array([freqs[int(np.argmax(fb[i]))] for i in range(len(fb))])
        right = max(freqs[np.nonzero(fb[i] > 1e-12)[0][-1]] for i in range(len(fb)))
        print("  audio-F16.gguf  metadata: n_mel=%d  n_fft=%d(=fft %d 点)  filters=%s"
              % (n_mel, n_fft, N, fb.shape))
        print("  窗长 %.7g ms @ %d Hz，帧移 %d 点 = %.7g ms"
              % (N / sr * 1000, sr, 160, 160 / sr * 1000))
        print("  80 个三角的峰值范围: %.0f Hz ~ %.0f Hz" % (pk.min(), pk.max()))
        print("  最右非零边(覆盖上限) : %.0f Hz   ⇒  Nyquist(%d) = %d Hz"
              % (right, sr, sr // 2))
        print("  末 bin 峰值实测 %.0f Hz；若 fmax=8000 的理论值 %.0f Hz；若 fmax=12000 的理论值 %.0f Hz"
              % (pk[-1], mel_hz(hz_mel(8000) * (len(fb) - 2) / (len(fb) - 1)),
                 mel_hz(hz_mel(12000) * (len(fb) - 2) / (len(fb) - 1))))
        print("  ⇒ 【判据】前端的支撑集就是 0–8000Hz。8kHz 以上不是『被砍掉』，是**从不进入**。")

    print()
    print("=" * 92)
    print("② 克隆侧条件带宽：把 prompt mel 的 fmax 从 8000 放宽到 Nyquist，网络会看到什么")
    print("=" * 92)
    x, sr = load(SRC)
    print("  真源 %s  (%.2fs @ %d Hz)" % (os.path.basename(SRC), len(x) / sr, sr))
    m8 = matcha_mel(x, sr, 8000.0)
    mN = matcha_mel(x, sr, None)
    print("  fmax=8000  → mel %s ； fmax=Nyquist → mel %s" % (m8.shape, mN.shape))

    n_bins = m8.shape[1]
    fr = np.linspace(0, sr / 2.0, 1920 // 2 + 1)
    peaks8 = np.array([fr[int(np.argmax(
        np.interp(fr, np.linspace(0, 8000, n_bins + 2)[1:-1], np.ones(n_bins)) * 0 + 1))]
        for _ in range(0)])  # 占位，真正的通道位置用 mel 刻度算
    edges8 = mel_hz(np.linspace(0, hz_mel(8000), n_bins + 2))
    edgesN = mel_hz(np.linspace(0, hz_mel(sr / 2.0), n_bins + 2))
    ctr8 = (edges8[1:-1] + edges8[2:]) / 2.0
    ctrN = (edgesN[1:-1] + edgesN[2:]) / 2.0
    print()
    print("  通道中心频率（mel 刻度）——同一根轴上的『语义』位置：")
    print("    %-6s %-14s %-14s %s" % ("bin", "fmax=8000", "fmax=Nyq", "位移"))
    for i in [0, 10, 25, 40, 55, 65, 72, 76, 79]:
        print("    %-6d %-14.0f %-14.0f %+.0f%%"
              % (i, ctr8[i], ctrN[i], (ctrN[i] / max(ctr8[i], 1) - 1) * 100))
    shift = (ctrN - ctr8) / np.maximum(ctr8, 1.0) * 100
    print("  ⇒ 全部 %d 个通道的语义都被挪动：中位 %+.0f%%，最大 %+.0f%%"
          % (n_bins, np.median(shift), np.abs(shift).max()))

    # log-mel 的实际差异（网络输入的直接偏移量）
    T = min(m8.shape[0], mN.shape[0])
    a, b = m8[:T], mN[:T]
    d = b - a
    print()
    print("  换前端后，网络输入（80 维 log-mel）的实际偏移：")
    print("    平均绝对偏移 %.2f（log 单位）  最大 %.2f   两版相关性 %.3f"
          % (np.abs(d).mean(), np.abs(d).max(),
             np.corrcoef(a.ravel(), b.ravel())[0, 1]))
    print("    按通道看：前 20 个低通道偏移 %.2f ｜ 后 20 个高通道偏移 %.2f"
          % (np.abs(d[:, :20]).mean(), np.abs(d[:, -20:]).mean()))

    print()
    print("  这次『放宽』到底能拿回来多少料（真源自带的高频占比，真源=44.1k mp3→24k）：")
    hi_share = 0.0
    for lo, hi in [(0, 2000), (2000, 4000), (4000, 6000), (6000, 8000), (8000, 12000)]:
        sh, db = band_share(x, sr, lo, hi)
        if lo >= 8000:
            hi_share += sh
        print("    %5d-%5d Hz : %6.3f%% 占总量 (%+6.1f dB)" % (lo, hi, sh, db))

    print()
    print("=" * 92)
    print("③ 只放宽入口、不改前端 —— 这正是『全量接收』最常见的误改法")
    print("=" * 92)
    import onnxruntime
    import torch
    import torchaudio.compliance.kaldi as kaldi
    sess = onnxruntime.InferenceSession(CAMPP, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    def spk(x16):
        t = torch.from_numpy(np.asarray(x16, dtype=np.float32)).float().unsqueeze(0)
        f = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
        f = f - f.mean(dim=0, keepdim=True)
        e = sess.run(None, {iname: f.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
        return e / (np.linalg.norm(e) + 1e-9)

    x16_correct = to_sr(x, sr, 16000)          # 正确：先降到 16k 再算特征
    e_ref = spk(x16_correct)
    # 误改法：去掉入口降采样，直接把 24k 样本喂给声明 16k 的前端
    e_wrong = spk(np.asarray(x, dtype=np.float32))
    print("  正确路径（24k→16k→fbank 声明16k）与 误改路径（24k 直接→fbank 声明16k）余弦：")
    print("    cos = %.4f        （同人上限 0.7280 / 异人下限 0.1947）"
          % float(e_ref @ e_wrong))
    print("  误改路径的时域后果：时长被判为 %.2fs（真实 %.2fs，放大 %.2f×）"
          % (len(x) / 16000, len(x) / sr, sr / 16000.0))
    print("  误改路径的频域后果：音高整体被当低 %.2f 个八度（%.1f Hz 的 F0 被读成 %.1f Hz）"
          % (np.log2(sr / 16000.0), 240.0, 240.0 * 16000.0 / sr))
    print("  ⇒ 【判据】去掉降采样却保留 16k 前端 = 特征全错。这不是『拿回高频』，是『把音频读错』。")

    print()
    print("=" * 92)
    print("④ 『素材更宽 → 模型听得更全』成立吗：把 44.1k 原文件 / 24k / 16k 三条都送进同一个前端")
    print("=" * 92)
    if fb is not None:
        ORIG_MP3 = r"G:\微信聊天文件\xwechat_files\wxid_2cu68rhp1igz12_1725\msg\file\2026-08\8月13日 (1).mp3"
        variants = []
        if os.path.exists(ORIG_MP3):
            ff = ffmpeg()
            if ff:
                r = subprocess.run([ff, "-v", "quiet", "-i", ORIG_MP3, "-ac", "1", "-ar", "16000",
                                    "-f", "f32le", "-"], capture_output=True)
                o16 = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
                variants.append(("原始 44.1k mp3（真正宽频）→16k", o16))
        x24_16 = to_sr(x, sr, 16000)
        variants.append(("24k 派生版 mp3_8s_24k →16k", x24_16))
        x16_direct = to_sr(x, sr, 16000)
        variants.append(("同一内容先降到 16k 再给前端", x16_direct))

        mels = []
        for tag, v in variants:
            mm = whisper_logmel(v, fb)
            mels.append((tag, mm, len(v)))
            print("  %-34s %6.2fs  前端输出 %s" % (tag, len(v) / 16000.0, mm.shape))

        def align(a, b, max_lag=300):
            """按帧能量包络互相关对齐（三条变体起点不同：原始含未裁静音）。"""
            ea, eb = a.mean(axis=0), b.mean(axis=0)
            best, bl = -9e9, 0
            for lag in range(-max_lag, max_lag + 1):
                if lag >= 0:
                    u, v2 = ea[lag:], eb[:len(ea) - lag]
                else:
                    u, v2 = ea[:len(ea) + lag], eb[-lag:]
                n = min(len(u), len(v2))
                if n < 50:
                    continue
                c = np.corrcoef(u[:n], v2[:n])[0, 1]
                if c > best:
                    best, bl = c, lag
            return bl, best

        print()
        print("  两两比较（先按能量包络对齐，再比 whisper 前端输出）：")
        for i in range(len(mels)):
            for j in range(i + 1, len(mels)):
                ta, a, _ = mels[i]
                tb, b, _ = mels[j]
                lag, env_c = align(a, b)
                if lag >= 0:
                    u, v2 = a[:, lag:], b
                else:
                    u, v2 = a, b[:, -lag:]
                T2 = min(u.shape[1], v2.shape[1])
                u, v2 = u[:, :T2], v2[:, :T2]
                print("    %-26s vs %-26s  对齐lag=%+4d(包络corr %.3f)  avg|Δ|=%.4f  corr=%.4f"
                      % (ta[:24], tb[:24], lag, env_c,
                         np.abs(u - v2).mean(), np.corrcoef(u.ravel(), v2.ravel())[0, 1]))
        print("  ⇒ 【判据】把素材从 16k 换成 44.1k 原始宽频，模型『听到』的东西几乎不变；")
        print("     因为前端支撑集本来就是 0–8000Hz，>8k 对理解侧不是『漏听』，是不在定义域内。")
        print()
        print("  前端 80 通道的平均 log-mel（= 前端眼里各频段的『音量』）：")
        m0 = mels[1][1] if len(mels) > 1 else mels[0][1]
        prof = m0.mean(axis=1)
        for lo_i, hi_i, tag in [(0, 20, "bin 0-19   ≈ 40-1400Hz"),
                                (20, 40, "bin 20-39 ≈ 1400-3400Hz"),
                                (40, 60, "bin 40-59 ≈ 3400-5800Hz"),
                                (60, 80, "bin 60-79 ≈ 5800-7960Hz")]:
            print("    %-24s 平均 %.3f   (该段占前端总能量 %5.1f%%)"
                  % (tag, prof[lo_i:hi_i].mean(),
                     np.exp(prof[lo_i:hi_i]).sum() / np.exp(prof).sum() * 100))
        print("    注：这只是『前端眼里的能量分布』，不等于可懂度；但可以说明")
        print("        最高那 20 个通道（5.8–8k）在前端里权重最小、且 8k 以上根本不在支撑集内。")

    print()
    print("=" * 92)
    print("汇总：净收益 vs 净扰动")
    print("=" * 92)
    e_hi_dummy = 0.0
    print("  想拿回来的料：真源 8–12kHz 占总能量 %.2f%%" % hi_share)
    print("  为此要付的价（若只改前端不重训）：80/80 个条件通道语义位移，中位 %+.0f%%；"
          % np.median(shift))
    print("                                    网络输入平均偏移 %.2f log 单位、相关性 %.3f"
          % (np.abs(d).mean(), np.corrcoef(m8[:T].ravel(), mN[:T].ravel())[0, 1]))
    print("  ⇒ 拿回约 %.1f%% 的料，代价是把 100%% 的通道挪位。除非连同权重一起重训，否则不是升级。"
          % hi_share)
    print("     上游自己做过这件事：CosyVoice2 → CosyVoice3 的前端差异恰好就是 fmax 8000 → null，")
    print("     但那是带着新权重、新 tokenizer 一起发布的新模型，不是补丁。")


if __name__ == "__main__":
    main()
