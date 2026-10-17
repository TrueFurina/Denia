# -*- coding: utf-8 -*-
"""F0 权威对照工具（PYIN）。替代自相关法。

为什么必须用 PYIN：
  自相关法把峰值量化到整数 lag（24kHz 下分辨率 ~3Hz），且会在 lag 的整数倍
  上选错峰。实测同一文件 `plus_S2_deadair.wav`：
      自相关 → 266.7Hz      PYIN → 232.7Hz      差 15%
  这个误差足以翻转"换档是否改善音高离散度"的结论，已导致一次误判。
  ⇒ 凡是要下"音高/变声"结论，一律用本工具的 PYIN 读数。

用法：
  python tools/f0_compare.py                       # 默认跑本次换档 A/B
  python tools/f0_compare.py <glob> [<glob> ...]   # 自定义
"""
import os, sys, glob, io, json, argparse
import numpy as np, soundfile as sf
import warnings
warnings.filterwarnings('ignore')
import librosa

BASE_HZ = 270.5   # daniya_ref_0813_24k.wav 完整素材 PYIN 中位


def f0_pyin(path, fmin=70, fmax=500, sr_work=24000):
    """返回 (中位F0, P10, P90, 时长s, 有效帧数)。采样率统一到 24k 再测。"""
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x[:, 0]
    dur = len(x) / sr
    if sr != sr_work:
        x = librosa.resample(x, orig_sr=sr, target_sr=sr_work)
    f0, _, _ = librosa.pyin(x, fmin=fmin, fmax=fmax, sr=sr_work,
                            frame_length=1024, hop_length=256, fill_na=np.nan)
    v = f0[~np.isnan(f0)]
    if len(v) < 5:
        return None, None, None, dur, len(v)
    return (float(np.median(v)), float(np.percentile(v, 10)),
            float(np.percentile(v, 90)), dur, len(v))


def report(paths, title):
    print('=' * 100)
    print(title)
    print('=' * 100)
    print(f"{'文件':<42}{'时长':>7}{'PYIN中位':>10}{'P10~P90':>14}{'n':>6}  偏离基准")
    out = {}
    for p in paths:
        med, p10, p90, dur, n = f0_pyin(p)
        if med is None:
            print(f'{os.path.basename(p):<42}{dur:>6.2f}s{"有效帧不足":>10}({n})')
            continue
        dev = (med - BASE_HZ) / BASE_HZ * 100
        print(f'{os.path.basename(p):<42}{dur:>6.2f}s{med:>9.1f}Hz{p10:>6.0f}~{p90:<6.0f}{n:>6}  {dev:>+6.1f}%')
        out[os.path.basename(p)] = dict(med=med, dur=dur, dev=dev)
    vals = [(k, v['med']) for k, v in out.items() if v['dur'] >= 1.5]
    if len(vals) >= 2:
        vv = [m for _, m in vals]
        print(f'  ▸ 剔除 <1.5s 短句后 {len(vv)} 句：跨度 {max(vv)-min(vv):.1f}Hz  '
              f'(max/min={max(vv)/min(vv):.3f})  σ={np.std(vv):.1f}Hz  '
              f'半音跨度={12*np.log2(max(vv)/min(vv)):.2f}')
    print()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('globs', nargs='*')
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    D = os.path.join(root, 'data', 'voice_switch_plus_20260923')
    CA = os.path.join(root, 'src', 'ui', 'web', 'assets', 'chat_audio')
    REF = os.path.join(root, 'assets', 'voices', 'daniya_ref_0813_24k.wav')

    if a.globs:
        for g in a.globs:
            report(sorted(glob.glob(g)), f'自定义：{g}')
    else:
        def has(f, key):
            return [f] if key in os.path.basename(f) else []
        for tag in ['flash', 'plus']:
            ps = []
            for f in sorted(glob.glob(os.path.join(D, '*.wav'))):
                if os.path.basename(f).startswith(tag + '_'):
                    ps.append(f)
            if ps:
                report(ps, f'探针直出 / {tag} 档（同文本，走生产代码路径）')
        for pat, name in [('20260923-193*.wav', '通话落盘 / 19:30（plus）'),
                          ('20260923-190*.wav', '通话落盘 / 19:05（flash）')]:
            ps = sorted(glob.glob(os.path.join(CA, pat)))
            if ps:
                report(ps, name)
    if os.path.exists(REF):
        report([REF], '基准素材（完整素材，唯一可信基准）')


if __name__ == '__main__':
    main()
