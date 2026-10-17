# -*- coding: utf-8 -*-
"""对比「原生 Comni 会话」与「我们接入的会话」的 system_prompt 与逐块决策。

筛选逻辑：prompt 长度 < 200 字的 = 原生 Comni app 自己跑的（它用内置极简 prompt）。
            我们的 = 含 <behavior> 包裹、长度 400~4000。
"""
import json, io, os, glob, collections

BASE = r'H:/Comni/_internal/resources/apps/server/data/sessions'


def load_all():
    recs = []
    for d in glob.glob(os.path.join(BASE, '*')):
        rp = os.path.join(d, 'recording.json')
        if not os.path.isfile(rp):
            continue
        try:
            j = json.load(io.open(rp, encoding='utf-8', errors='replace'))
        except Exception:
            continue
        cfg = j.get('config') or {}
        sp = cfg.get('system_prompt') or ''
        recs.append({'dir': os.path.basename(d), 'mtime': os.path.getmtime(d),
                     'sp': sp, 'chunks': j.get('chunks') or [], 'cfg': cfg})
    return recs


def mode_stats(chunks):
    c = collections.Counter()
    seq = []
    for ch in chunks:
        r = ch.get('result') or {}
        m = (r.get('mode') or 'None')
        c[m] += 1
        seq.append(m)
    runs, cur = [], 0
    for m in seq:
        if m == 'SPEAK':
            cur += 1
        else:
            if cur:
                runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    ts = [x.get('receive_ts_ms') for x in chunks if x.get('receive_ts_ms')]
    gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:])]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0
    return c, seq, runs, med


recs = load_all()
native = [r for r in recs if 0 < len(r['sp']) < 200 and r['chunks']]
ours = [r for r in recs if '<behavior>' in r['sp']]
native.sort(key=lambda r: r['mtime'])
ours.sort(key=lambda r: r['mtime'])

out = []
P = out.append

P('#' * 78)
P('# 一、原生 Comni app 自己跑的会话（system_prompt < 200 字）')
P('#' * 78)
P('共 %d 个' % len(native))
for r in native:
    c, seq, runs, med = mode_stats(r['chunks'])
    n = len(r['chunks'])
    sp_ = c.get('SPEAK', 0)
    P('')
    P('  %s  块=%-4d SPEAK=%-4d(%.1f%%) 间隔中位=%.0fms' %
      (r['dir'], n, sp_, 100.0 * sp_ / n if n else 0, med))
    P('  SPEAK游程: %s' % runs[:40])
    P('  gen_audio=%s' % r['cfg'].get('generate_audio'))
    P('  --- prompt 全文（%d字）---' % len(r['sp']))
    for ln in r['sp'].split(chr(10)):
        P('  | ' + ln)
    P('  --- 结束 ---')

P('')
P('#' * 78)
P('# 二、我们接入的会话（含 <behavior> 包裹）')
P('#' * 78)
P('共 %d 个，取最近 5 个' % len(ours))
for r in ours[-5:]:
    c, seq, runs, med = mode_stats(r['chunks'])
    n = len(r['chunks'])
    sp_ = c.get('SPEAK', 0)
    P('  %-46s 块=%-4d SPEAK=%-4d(%.1f%%) prompt=%d字' %
      (r['dir'], n, sp_, 100.0 * sp_ / n if n else 0, len(r['sp'])))
    P('     SPEAK游程: %s' % runs[:40])

io.open('G:/ai对话/11/data/_cmp_native.txt', 'w', encoding='utf-8').write(chr(10).join(out))
print(chr(10).join(out[:120]))
