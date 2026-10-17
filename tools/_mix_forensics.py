# -*- coding: utf-8 -*-
"""混合音色取证：判断输出是「独立的一种音色」还是「达妮娅×原生 的线性混合」。

核心量：
  cos↔SRC / cos↔SIG   : 与达妮娅真源 / MiniCPM 原生签名的说话人余弦
  a_native            : 把输出嵌入投影到 (SRC,SIG) 张成的线性混合轴上，
                        求最优 alpha，使 cos(out, normalize(alpha*SRC+(1-alpha)*SIG)) 最大
                        => 输出"含多少原生"
  r (声道长度比)       : 包络对齐法，r≈1.00=同一人 / r≈1.6=整体重采样
  F0                  : 音高，说话人强相关
判读：
  · a_native ≈ 0   -> 没有原生污染，用户听到的"混合"另有来源
  · a_native ≈ 0.5 -> 真的是一半一半，两种条件在打架
  · a_native ≈ 1   -> 原生完全接管，个性化音色没进去
"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
BUNDLE = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio\default_ref_audio.wav"
APPDEF = r"H:\Comni\_internal\resources\apps\assets\ref_audio\daniya_ref.wav"


def to16k(x, sr):
    if sr == 16000:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", "16000", "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


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


def env(x, sr, nfft=2048, hop=512):
    """长时平均谱包络（对数域），返回 (freqs, log_env)"""
    x = x - x.mean()
    if len(x) < nfft:
        return None, None
    S = np.zeros(nfft // 2 + 1)
    cnt = 0
    for i in range(0, len(x) - nfft, hop):
        s = x[i:i + nfft] * np.hanning(nfft)
        if np.sqrt(np.mean(s ** 2)) < 0.005:
            continue
        S += np.abs(np.fft.rfft(s))
        cnt += 1
    if cnt == 0:
        return None, None
    S /= cnt
    f = np.fft.rfftfreq(nfft, 1 / sr)
    return f, 20 * np.log10(S + 1e-9)


def vocal_tract_ratio(env_a, env_b, f, lo=150.0, hi=7500.0):
    """声道长度比：找 r 使 env_b(f*r) 与 env_a(f) 最相关（对数频率网格上）"""
    g = np.logspace(np.log10(lo), np.log10(hi), 220)
    a = np.interp(g, f, env_a)
    a = a - a.mean()
    best = (0.0, 0.0)
    for r in np.arange(0.60, 1.65, 0.005):
        b = np.interp(g, f, env_b, left=np.nan, right=np.nan) if False else None
        braw = np.interp(g / r, f, env_b)
        b = braw - braw.mean()
        den = np.linalg.norm(a) * np.linalg.norm(b)
        if den <= 0:
            continue
        c = float(a @ b / den)
        if c > best[1]:
            best = (float(r), c)
    return best


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

    def mix_alpha(e, se, ge):
        """最优 alpha: out ~ normalize(a*SRC + (1-a)*SIG)"""
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

    xsrc, rs = load(SRC)
    xsig, gs = load(SIG)
    se = spk(to16k(xsrc, rs))
    ge = spk(to16k(xsig, gs))
    fs_, es_ = env(xsrc, rs)
    fg_, eg_ = env(xsig, gs)

    print("=" * 126)
    print("基准锚点")
    print("=" * 126)
    print("  cos(达妮娅真源, MiniCPM原生) = %.4f   <- 两者本来就差多远（越小越好区分）" % float(se @ ge))
    print("  SRC 达妮娅真源 %.2fs F0=%.1f" % (len(xsrc) / rs, f0_median(xsrc, rs)))
    print("  SIG 原生签名   %.2fs F0=%.1f" % (len(xsig) / gs, f0_median(xsig, gs)))
    print()
    print("=" * 126)
    print("候选输入/产出取证")
    print("=" * 126)
    print("%-30s%8s%8s%10s%10s%10s%10s%9s"
          % ("样本", "时长", "F0", "cos↔达妮娅", "cos↔原生", "a_原生", "拟合度", "包络r↔达妮娅"))
    print("-" * 126)
    rows = [
        ("[输入] 达妮娅真源 mp3_8s_24k", SRC),
        ("[输入] 原生签名 ref_minicpm", SIG),
        ("[输入] bundle default_ref", BUNDLE),
        ("[输入] apps/assets/daniya_ref", APPDEF),
        ("[消融①] 达妮娅缓存+传参考音 APP_A", os.path.join(FS, "APP_A.wav")),
        ("[消融②] 达妮娅缓存+不传参考音", os.path.join(FS, "ABL_daniacache_noref.wav")),
        ("[消融③] 原生缓存+传参考音", os.path.join(FS, "ABL_stock_ourref.wav")),
        ("[消融④] 原生缓存+不传参考音", os.path.join(FS, "ABL_stock_noref.wav")),
        ("[旧] B_NATIVE 完全没传参考音", os.path.join(FS, "B_NATIVE.wav")),
        ("[旧] A_FIXED 旧测试台", os.path.join(FS, "A_FIXED.wav")),
        ("[旧] A_FIXED_v2a 真源缓存", os.path.join(FS, "A_FIXED_v2a.wav")),
        ("[旧] APP_A_smooth", os.path.join(FS, "APP_A_smooth.wav")),
    ]
    for lab, p in rows:
        if not os.path.exists(p):
            print("%-30s MISSING" % lab)
            continue
        x, sr = load(p)
        e = spk(to16k(x, sr))
        f0 = f0_median(x, sr)
        a, fit = mix_alpha(e, se, ge)
        f_, e_ = env(x, sr)
        rr, rc = vocal_tract_ratio(fs_, e_, f_) if f_ is not None else (0, 0)
        print("%-30s%8.2f%8.1f%10.4f%10.4f%10.2f%10.4f%9.2f"
              % (lab, len(x) / sr, f0, float(se @ e), float(ge @ e), a, fit, rr))
    print()
    print("判读：")
    print("  · a_原生 0=纯达妮娅 / 0.5=一半一半 / 1=原生接管")
    print("  · 拟合度 = 用「达妮娅与原生线性混合」解释输出的吻合程度；>0.9 说明确实像混合，")
    print("    <0.8 说明输出是第三种音色（不是简单混合）")
    print("  · 包络r↔达妮娅 ≈1.00 表示声道长度与达妮娅一致（没被整体变调/重采样）")


if __name__ == "__main__":
    main()
