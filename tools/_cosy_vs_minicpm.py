# -*- coding: utf-8 -*-
"""同一把尺子：MiniCPM-o 原生 TTS vs CosyVoice2 零样本克隆。

三个维度一次性量完：
  A. 说话人几何 —— campplus 余弦（↔达妮娅真源 / ↔MiniCPM原生签名）+ 线性混合最优 alpha + 拟合度
  B. 频段剖面   —— 相对本样本 300-3400Hz 的 dB（3.4-6k / 6-8k / 8-12k）+ 8kHz 以上能量占比
  C. 流畅度     —— 自适应阈值(自身RMS-25dB)下的浊音占比 / 停顿≥0.15s 次数每秒 / 最长停顿

刻度锚点（已标定）：
  同人自比 0.7280 | 异人 0.1947 | MiniCPM APP_A 0.7103
"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
P = r"G:\ai对话\11"
FS = os.path.join(P, "data", "native_audio_test", "final_sample")
CZ = os.path.join(P, "data", "native_audio_test", "cosy_clone")
SRC = os.path.join(P, "data", "native_audio_test", "ref_v2", "mp3_8s_24k.wav")
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
SR = 24000


def to16k(x, sr):
    if sr == 16000:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", "16000", "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def to_sr(x, a, b):
    if a == b:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(a), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", str(b), "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def load24(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return to_sr(x, sr, SR)


def f0_median(x, sr, fmin=70, fmax=500, frame=1024, hop=256, thr=0.01):
    x = x - x.mean()
    out = []
    for i in range(max(0, (len(x) - frame) // hop)):
        s = x[i * hop:i * hop + frame]
        if np.sqrt(np.mean(s ** 2)) < thr:
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


def ltas(x, nfft=2048, hop=512):
    if len(x) < nfft:
        return None, None
    acc = np.zeros(nfft // 2 + 1)
    n = 0
    for i in range(0, len(x) - nfft, hop):
        s = x[i:i + nfft] * np.hanning(nfft)
        if np.sqrt(np.mean(s ** 2)) < 0.005:
            continue
        acc += np.abs(np.fft.rfft(s))
        n += 1
    if n == 0:
        return None, None
    f = np.fft.rfftfreq(nfft, 1 / SR)
    return f, 20 * np.log10(acc / n + 1e-9)


def band_db(f, L, lo, hi):
    m = (f >= lo) & (f < hi)
    return float(L[m].mean()) if m.any() else -99.0


def band_profile(x):
    """(b34_6, b6_8, b8_12, >8k能量占比%)，均相对本样本 300-3400Hz"""
    f, L = ltas(x)
    if f is None:
        return None
    ref = band_db(f, L, 300, 3400)
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    ff = np.fft.rfftfreq(len(x), 1 / SR)
    tot = S.sum() + 1e-12
    return (band_db(f, L, 3400, 6000) - ref,
            band_db(f, L, 6000, 8000) - ref,
            band_db(f, L, 8000, 12000) - ref,
            float(S[ff >= 8000].sum() / tot * 100))


def burst(x, thr_off=-25.0, hop=120, win=480):
    """自适应阈值下的碎块指标（还原到 24kHz 网格）"""
    if len(x) < win * 3:
        return None
    n = (len(x) - win) // hop + 1
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    e = np.sqrt((x[idx] ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(e, 1e-12))
    rms_db = 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)
    lab = db >= (rms_db + thr_off)
    runs, i = [], 0
    while i < len(lab):
        j = i
        while j + 1 < len(lab) and lab[j + 1] == lab[i]:
            j += 1
        runs.append(("V" if lab[i] else "P", i * hop / SR, (j - i + 1) * hop / SR))
        i = j + 1
    v = [d for k, s, d in runs if k == "V"]
    pp = [d for k, s, d in runs if k == "P"]
    dur = len(x) / SR
    return dict(dur=dur, n_v=len(v), v_med=float(np.median(v)) if v else 0.0,
                v_frac=sum(v) / dur,
                p_ge15_per_s=sum(1 for d in pp if d >= 0.15) / dur,
                p_max=max(pp) if pp else 0.0,
                vlt02=sum(1 for d in v if d < 0.2))


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

    def mix_alpha(e, se, ge):
        best = (0.0, -2.0)
        for a in np.arange(0.0, 1.001, 0.01):
            v = a * se + (1 - a) * ge
            n = np.linalg.norm(v)
            if n <= 0:
                continue
            c = float(e @ (v / n))
            if c > best[1]:
                best = (float(a), c)
        return best

    xsrc, rs = sf.read(SRC, dtype="float32")
    if xsrc.ndim > 1:
        xsrc = xsrc.mean(axis=1)
    xsig, gs = sf.read(SIG, dtype="float32")
    if xsig.ndim > 1:
        xsig = xsig.mean(axis=1)
    se = spk(to16k(xsrc, rs))
    ge = spk(to16k(xsig, gs))

    rows = [
        ("[输入] 达妮娅真源(宽频基准)", SRC),
        ("[输入] MiniCPM 原生签名", SIG),
        ("[A] MiniCPM-o 克隆输出(现状)", os.path.join(FS, "APP_A.wav")),
        ("[A+] MiniCPM + 停顿规整", os.path.join(FS, "APP_A_smooth.wav")),
        ("[B] CosyVoice2 ref8s", os.path.join(CZ, "COSY_1_ref8s.wav")),
        ("[C] CosyVoice2 ref48k", os.path.join(CZ, "COSY_2_ref48k.wav")),
        ("[D] CosyVoice2 ref5s", os.path.join(CZ, "COSY_3_ref5s.wav")),
        ("[E] CosyVoice2 + 风格指令", os.path.join(CZ, "COSY_4_instruct.wav")),
    ]

    print("=" * 150)
    print("A. 说话人几何 + B. 频段剖面 + C. 流畅度  （同一把尺子）")
    print("=" * 150)
    print("刻度: 同人自比 0.7280 | 异人 0.1947 | 早前 MiniCPM APP_A 0.7103")
    print()
    hdr = ("%-30s%7s%7s%11s%11s%9s%8s%9s%8s%8s%11s%9s%10s%9s" %
           ("样本", "时长", "F0", "cos↔达妮娅", "cos↔原生", "a_原生", "拟合度",
            "3.4-6k", "6-8k", "8-12k", ">8k占比", "浊音%", "停≥.15/s", "最长停"))
    print(hdr)
    print("-" * 150)
    for lab, p in rows:
        if not os.path.exists(p):
            print("%-30s MISSING %s" % (lab, os.path.basename(p)))
            continue
        x = load24(p)
        e = spk(to16k(x, SR))
        f0 = f0_median(x, SR)
        a, fit = mix_alpha(e, se, ge)
        bp = band_profile(x)
        bu = burst(x)
        print("%-30s%7.2f%7.1f%11.4f%11.4f%9.2f%8.4f%9.1f%8.1f%8.1f%10.2f%%%8.0f%%%10.2f%9.3f"
              % (lab, len(x) / SR, f0, float(se @ e), float(ge @ e), a, fit,
                 bp[0], bp[1], bp[2], bp[3],
                 bu["v_frac"] * 100, bu["p_ge15_per_s"], bu["p_max"]))
    print()
    print("判读：")
    print("  · cos↔达妮娅 越高越像本人；0.7280 是'同人不同段'的天然上限参照")
    print("  · 8-12k / >8k占比 = 高频质感来源；真源是宽频基准，越接近越不像'模型味'")
    print("  · 浊音% 越高、停≥.15/s 越少、最长停越短 => 越流畅")


if __name__ == "__main__":
    main()
