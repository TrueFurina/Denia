# -*- coding: utf-8 -*-
"""服务端地面真值分析：引擎实际收到了什么节奏的块、每块裁决 LISTEN/SPEAK、KV cache 状态。"""
import os, io, json, collections

SESS = r'H:\Comni\_internal\resources\apps\server\data\sessions\20260923_200856_adx_1790165322046'
j = json.loads(io.open(os.path.join(SESS, 'recording.json'), encoding='utf-8', errors='replace').read())
ch = j['chunks']
print('=' * 76)
print('A) 服务端收到的块数 / 裁决分布')
print('=' * 76)
print('chunks n =', len(ch))
mode = collections.Counter()
for c in ch:
    mode[(c.get('result') or {}).get('mode')] += 1
for k, v in mode.most_common():
    print('   mode=%-8s %d  (%.1f%%)' % (k, v, 100.0 * v / len(ch)))

print()
print('=' * 76)
print('B) 块到达间隔分布（receive_ts_ms，引擎视角地面真值）')
print('=' * 76)
ts = [c.get('receive_ts_ms') for c in ch]
gaps = []
for a, b in zip(ts, ts[1:]):
    if a is None or b is None:
        continue
    gaps.append(round(b - a, 1))
print('   总时长 %.1f ms = %.1f s' % (ts[-1] - ts[0], (ts[-1] - ts[0]) / 1000.0))
bucket = collections.Counter()
for g in gaps:
    if g < 100:
        bucket['<100ms（积压一次性冲刷）'] += 1
    elif g < 200:
        bucket['100~200ms'] += 1
    elif g < 400:
        bucket['200~400ms'] += 1
    elif g < 600:
        bucket['400~600ms（≈0.5s 节拍）'] += 1
    elif g < 1200:
        bucket['600~1200ms'] += 1
    else:
        bucket['>1.2s（空转/异常）'] += 1
for k in ['<100ms（积压一次性冲刷）', '100~200ms', '200~400ms',
          '400~600ms（≈0.5s 节拍）', '600~1200ms', '>1.2s（空转/异常）']:
    if bucket[k]:
        print('   %-24s %4d  (%.1f%%)' % (k, bucket[k], 100.0 * bucket[k] / len(gaps)))
g2 = sorted(gaps)
print('   中位间隔 %.1f ms ⇒ 引擎裁决频率 %.2f Hz（官方 chunk_ms=1000 ⇒ 1.00 Hz）'
      % (g2[len(g2) // 2], 1000.0 / g2[len(g2) // 2]))

print()
print('=' * 76)
print('C) SPEAK 块明细（引擎自己产出的文字）')
print('=' * 76)
sp = []
for i, c in enumerate(ch):
    r = c.get('result') or {}
    if r.get('mode') != 'SPEAK':
        continue
    txt = r.get('text') or ''
    if isinstance(txt, list):
        txt = ''.join(txt)
    sp.append((i, c.get('receive_ts_ms'), txt))
print('   SPEAK 块数 %d' % len(sp))
tot = sum(len(t) for _, _, t in sp)
if sp:
    print('   SPEAK 总字数 %d，跨 %.1fs ⇒ 引擎产出速率 %.1f 字/秒（真人语速 ≈4~5 字/秒）'
          % (tot, (sp[-1][1] - sp[0][1]) / 1000.0, tot / max(0.001, (sp[-1][1] - sp[0][1]) / 1000.0)))
    # 峰值：连续 SPEAK 段
    print()
    print('   —— 井喷段（连续 SPEAK）——')
    run = []
    prev_i = -99
    segs = []
    for i, t, tx in sp:
        if i - prev_i == 1:
            run.append((i, t, tx))
        else:
            if len(run) >= 2:
                segs.append(run)
            run = [(i, t, tx)]
        prev_i = i
    if len(run) >= 2:
        segs.append(run)
    for s in segs[-4:]:
        dur = (s[-1][1] - s[0][1]) / 1000.0
        chars = sum(len(x[2]) for x in s)
        print('   [chunk %d~%d] %d 块 / %.1fs / %d 字 ⇒ %.1f 字/秒'
              % (s[0][0], s[-1][0], len(s), dur, chars, chars / max(0.001, dur)))
        for i, t, tx in s:
            print('        chunk %-4d t=%-9.1f %s' % (i, t, tx[:50]))

print()
print('=' * 76)
print('D) KV cache 状态（=0 说明模型没有上下文记忆）')
print('=' * 76)
kv = [(c.get('result') or {}).get('timing', {}).get('kv_cache_length') for c in ch]
kc = collections.Counter(kv)
for k, v in kc.most_common(10):
    print('   kv_cache_length=%-8s %d' % (k, v))
print()
print('=' * 76)
print('E) 引擎单块耗时（prefill_ms / all_ms / wall_clock_ms）')
print('=' * 76)
for f in ('prefill_ms', 'all_ms', 'wall_clock_ms'):
    v = [(c.get('result') or {}).get('timing', {}).get(f) for c in ch]
    v = [x for x in v if isinstance(x, (int, float))]
    if v:
        v2 = sorted(v)
        print('   %-14s 中位 %.1f ms  p90 %.1f  max %.1f' % (f, v2[len(v2) // 2], v2[int(len(v2) * .9)], v2[-1]))
