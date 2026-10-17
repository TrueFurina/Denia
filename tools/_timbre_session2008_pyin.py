# -*- coding: utf-8 -*-
"""本轮音色权威版：PYIN 测 F0（遵守 §2cg 红线）+ FFT 测分带能量

用法：python tools/_timbre_session2008_pyin.py
输出 JSON：data/_timbre_session2008_pyin.json
"""
import os, sys, glob, io, json, time
import numpy as np, soundfile as sf
import warnings
warnings.filterwarnings('ignore')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from f0_compare import f0_pyin, BASE_HZ   # noqa: E402

CA = os.path.join(ROOT, 'src', 'ui', 'web', 'assets', 'chat_audio')
VO = os.path.join(ROOT, 'data', 'voice_switch_plus_20260923')
CUT = time.mktime(time.strptime('2026-09-23 20:08:00', '%Y-%m-%d %H:%M:%S'))


def bands(path):
    x, sr = sf.read(path, dtype='float32')
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != 24000:
        import librosa
        x = librosa.resample(x, orig_sr=sr, target_sr=24000)
        sr = 24000
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = X.sum() + 1e-12
    out = {}
    for nm, (lo, hi) in [('b0_4k', (0, 4000)), ('b4_8k', (4000, 8000)),
                         ('b8_12k', (8000, 12000))]:
        m = (f >= lo) & (f < hi)
        out[nm] = round(10 * np.log10((X[m].sum() + 1e-12) / tot), 2)
    return out, sr


rows = []
targets = []
for f in glob.glob(os.path.join(CA, '*.wav')):
    if os.path.getmtime(f) >= CUT:
        targets.append(('session20:08', f))
for f in sorted(glob.glob(os.path.join(VO, '*.wav'))):
    b = os.path.basename(f)
    tag = 'REF_plus' if b.startswith('plus') else ('REF_flash' if b.startswith('flash') else 'REF_truesrc')
    targets.append((tag, f))

for tag, f in sorted(targets):
    med, p10, p90, dur, n = f0_pyin(f)
    bd, sr = bands(f)
    rows.append(dict(tag=tag, file=os.path.basename(f), mtime=time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(f))),
                     dur=round(dur, 3), sr=sr, f0_med=med, f0_p10=p10, f0_p90=p90, nf0=n, **bd))

hdr = ['tag', 'mtime', 'file', 'sr', 'dur', 'b4_8k', 'b8_12k', 'f0_med', 'f0_p10', 'f0_p90']
print('\t'.join(hdr))
for r in rows:
    print('\t'.join('' if r.get(k) is None else str(r.get(k)) for k in hdr))


def med(rs, k):
    v = [r[k] for r in rs if isinstance(r.get(k), (int, float))]
    return float(np.median(v)) if v else float('nan')


def group(rs, name):
    # 剔除 <1.5s（§2cg 红线）
    ok = [r for r in rs if r['dur'] >= 1.5 and r.get('f0_med')]
    f0s = [r['f0_med'] for r in ok]
    span = (max(f0s) - min(f0s)) if len(f0s) >= 2 else float('nan')
    semi = 12 * np.log2(max(f0s) / min(f0s)) if len(f0s) >= 2 else float('nan')
    print('%-22s n=%-3d dur=%.2f  4-8k=%+.2f  8-12k=%+.2f  F0med=%.1f  p10=%.1f  '
          '句间跨度=%.1fHz(%.2f半音)'
          % (name, len(rs), med(rs, 'dur'), med(rs, 'b4_8k'), med(rs, 'b8_12k'),
             med(rs, 'f0_med'), med(rs, 'f0_p10'), span, semi))


print('\n=== 分组（PYIN，剔除 <1.5s）===')
sess = [r for r in rows if r['tag'] == 'session20:08']
group(sess, '本轮落盘')
group([r for r in rows if r['tag'] == 'REF_plus'], '百炼 plus 直出')
group([r for r in rows if r['tag'] == 'REF_flash'], '百炼 flash 直出')
group([r for r in rows if r['tag'] == 'REF_truesrc'], '真源')
print()
group([r for r in sess if r['dur'] <= 3.5], '本轮·短句<=3.5s')
group([r for r in sess if r['dur'] > 10], '本轮·长句>10s')
print('\n基准（完整素材 PYIN 中位）BASE_HZ=%.1f' % BASE_HZ)
for nm, rs in [('本轮落盘', sess), ('plus', [r for r in rows if r['tag'] == 'REF_plus']),
               ('flash', [r for r in rows if r['tag'] == 'REF_flash'])]:
    m = med(rs, 'f0_med')
    print('  %-8s 中位 %.1fHz 偏离基准 %+.1f%%（%+.2f 半音）'
          % (nm, m, (m - BASE_HZ) / BASE_HZ * 100, 12 * np.log2(m / BASE_HZ)))

json.dump(rows, io.open(os.path.join(ROOT, 'data', '_timbre_session2008_pyin.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1)
print('\nsaved data/_timbre_session2008_pyin.json')
