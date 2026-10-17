# -*- coding: utf-8 -*-
"""在候选克隆音里找出「最像达妮娅」的那个。
判据两把尺子：
  ① 说话人余弦 cos（campplus，吃 0-8kHz）—— 前面已标定：
     异人下限 0.1947 / 同人上限 0.7280 / 只降采样 0.9957
  ② F0 中位（目标 237.6Hz）+ 频带（flat>0.002, f95>1200 为合格）
注意：评测锚点用「另一段达妮娅」（daniya_ref_final_24k），避免与参考音同段自比。"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
D = r'G:\ai对话\11\data\native_audio_test'
FS = os.path.join(D, 'final_sample')

REFLIVE = r'G:\ai对话\11\assets\voices\daniya_ref.wav'
REFOTHER = os.path.join(D, 'ref_final', 'daniya_ref_final_24k.wav')
SIG = r'H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav'

CANDS = [
    # ---- 双工原生路径（MiniCPM）----
    ('聆听 v4', '03_克隆输出_全频带.wav', os.path.join(FS, '聆听_v4_频段归属')),
    ('聆听 v4', '05_原生签名音_限带8k.wav', os.path.join(FS, '聆听_v4_频段归属')),
    ('聆听 v5', '01_克隆_未补偿_8k以上弱.wav', os.path.join(FS, '聆听_v5_高频归属')),
    ('聆听 v5', '02_克隆_EQ1_8k以上对齐真源.wav', os.path.join(FS, '聆听_v5_高频归属')),
    ('聆听 v6', '04_克隆_未补偿_实际产出.wav', os.path.join(FS, '聆听_v6_上限裁决')),
    ('聆听 v6', '05_克隆+EQ1_实际+补偿.wav', os.path.join(FS, '聆听_v6_上限裁决')),
    ('ceiling', 'R2_clone_plus_EQ1.wav', os.path.join(FS, 'ceiling')),
    ('ABL 消融', 'ABL_daniacache_noref.wav', FS),
    ('ABL 消融', 'ABL_stock_ourref.wav', FS),
    ('ABL 消融', 'ABL_stock_noref.wav', FS),
    ('应用同构', 'APP_A.wav', FS),
    ('应用同构', 'APP_A_smooth.wav', FS),
    ('应用同构', 'APP_A_smooth_EQ1.wav', FS),
    ('修复前', 'B_NATIVE.wav', FS),
    ('修复后', 'A_FIXED.wav', FS),
    ('修复后', 'A_FIXED_r2.wav', FS),
    ('修复后', 'A_FIXED_v2a.wav', FS),
    ('修复后', 'A_FIXED_v2b.wav', FS),
    ('聆听 v1', '02_修复后_个性化音色.wav', os.path.join(FS, '聆听')),
    ('聆听 v1', '03_修复前_原生底色.wav', os.path.join(FS, '聆听')),
    ('聆听 v3', '02_应用同构_引擎自然节奏.wav', os.path.join(FS, '聆听_v3')),
    ('聆听 v3', '03_应用同构_停顿规整_最流畅.wav', os.path.join(FS, '聆听_v3')),
    ('聆听 v3', '08_原生底色对照_不传参考音.wav', os.path.join(FS, '聆听_v3')),
]

CANDS_EXTRA = [
    ('外挂Cosy', r'G:\ai对话\11\data\验收试听包_24k\01_克隆_今天过得怎么样.wav'),
    ('外挂Cosy', r'G:\ai对话\11\data\acceptance_24k\clone_01_24000.wav'),
]


def to16k(x, sr):
    if sr == 16000:
        return np.asarray(x, dtype=np.float32)
    r = subprocess.run([FF, '-v', 'quiet', '-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', '-',
                        '-f', 'f32le', '-ar', '16000', '-ac', '1', '-'],
                       input=np.asarray(x, dtype='<f4').tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype='<f4').astype(np.float32)


def load(p):
    x, sr = sf.read(p, dtype='float32')
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def f0_med(x, sr):
    fl = int(0.04 * sr)
    hop = int(0.01 * sr)
    out = []
    for i in range(0, max(1, len(x) - fl), hop):
        fr = x[i:i + fl]
        if fr.size < fl or np.sqrt((fr ** 2).mean()) < 0.01:
            continue
        fr = fr - fr.mean()
        c = np.correlate(fr, fr, 'full')[fl - 1:]
        lo, hi = int(sr / 400), min(int(sr / 70), len(c) - 1)
        if hi <= lo:
            continue
        p = c[lo:hi].argmax() + lo
        if c[0] > 0 and c[p] / c[0] > 0.3:
            out.append(sr / p)
    return float(np.median(out)) if out else 0.0


def band(x, sr):
    n, hop = 1024, 256
    w = np.hanning(n)
    P = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if seg.size < n:
            break
        P.append(np.abs(np.fft.rfft(seg * w)) ** 2)
    if not P:
        return 0.0, 0.0, {}
    P = np.mean(P, 0)
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = P.sum()
    flat = float(np.exp(np.mean(np.log(P + 1e-12))) / (np.mean(P) + 1e-12))
    cs = np.cumsum(P)
    f95 = float(f[np.searchsorted(cs, 0.95 * tot)])
    seg = {}
    for lo, hi in [(3400, 6000), (6000, 8000), (8000, 12000)]:
        m = (f >= lo) & (f < hi)
        seg[f'{lo // 1000}-{hi // 1000}k'] = round(
            10 * np.log10((P[m].sum() / max(m.sum(), 1)) / (tot / len(P)) + 1e-12), 1)
    return flat, f95, seg


def main():
    import onnxruntime
    import torch
    import torchaudio.compliance.kaldi as kaldi
    sess = onnxruntime.InferenceSession(r'H:\daniya_voice_tools\campplus.onnx',
                                        providers=['CPUExecutionProvider'])
    iname = sess.get_inputs()[0].name

    def spk(x16):
        t = torch.from_numpy(np.asarray(x16)).float().unsqueeze(0)
        fb = kaldi.fbank(t, num_mel_bins=80, dither=0, sample_frequency=16000)
        fb = fb - fb.mean(dim=0, keepdim=True)
        e = sess.run(None, {iname: fb.unsqueeze(0).cpu().numpy()})[0].reshape(-1)
        return e / (np.linalg.norm(e) + 1e-9)

    xl, lr = load(REFLIVE)
    xo, orr = load(REFOTHER)
    xg, grr = load(SIG)
    e_live = spk(to16k(xl, lr))
    e_other = spk(to16k(xo, orr))
    e_sig = spk(to16k(xg, grr))

    print('=' * 108)
    print('锚点')
    print('=' * 108)
    print('  live 参考音 daniya_ref.wav        F0=%.1f  cos(↔另一段达妮娅)=%.4f'
          % (f0_med(xl, lr), float(e_live @ e_other)))
    print('  另一段达妮娅 final_24k            F0=%.1f'
          % f0_med(xo, orr))
    print('  MiniCPM 原生签名音                 F0=%.1f  cos(↔达妮娅)=%.4f'
          % (f0_med(xg, grr), float(e_other @ e_sig)))
    print('  刻度：异人下限 ≈0.1947 ｜ 同人上限 ≈0.7280 ｜ 只降采样 ≈0.9957')
    print()

    rows = []
    for tag, name, d in CANDS:
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        x, sr = load(p)
        e = spk(to16k(x, sr))
        fl, f95, seg = band(x, sr)
        ok = 'OK ' if (fl > 0.002 and f95 > 1200) else 'BAD'
        rows.append((float(e @ e_other), tag, name, sr, len(x) / sr, f0_med(x, sr),
                     fl, f95, ok, float(e @ e_live), seg))

    print('=' * 108)
    print('候选按「像达妮娅」排序（cos 锚点 = 另一段达妮娅，非参考音本身）')
    print('=' * 108)
    print('%-9s %-34s %6s %6s %7s %9s %6s %7s %7s' %
          ('分组', '文件', 'sr', 'dur', 'F0', 'flat', 'f95', 'cos', 'cos(live)'))
    for cos, tag, name, sr, dur, f0, fl, f95, ok, cl, seg in sorted(rows, reverse=True):
        print('%-9s %-34s %6d %6.1f %7.1f %9.5f %6.0f %7.4f %7.4f %s' %
              (tag, name[:34], sr, dur, f0, fl, f95, cos, cl, ok))

    print()
    print('--- 外挂 CosyVoice 路径（已知坏，作对照）---')
    for tag, p in CANDS_EXTRA:
        if not os.path.isfile(p):
            continue
        x, sr = load(p)
        e = spk(to16k(x, sr))
        fl, f95, _ = band(x, sr)
        print('%-9s %-34s sr=%d F0=%.1f flat=%.5f f95=%.0f cos=%.4f %s' %
              (tag, os.path.basename(p), sr, f0_med(x, sr), fl, f95,
               float(e @ e_other), 'BAD' if not (fl > 0.002 and f95 > 1200) else 'OK'))

    # 顺便把 top3 的频带贴出来
    print()
    print('--- Top5 频带明细 ---')
    for cos, tag, name, sr, dur, f0, fl, f95, ok, cl, seg in sorted(rows, reverse=True)[:5]:
        print('  %-34s cos=%.4f F0=%.1f  %s' % (name[:34], cos, f0, seg))


if __name__ == '__main__':
    main()
