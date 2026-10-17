# -*- coding: utf-8 -*-
"""按句长分组对比亮度：短填充句 vs 长句（解释"听着不像她"）"""
import json, io
import numpy as np

d = json.load(io.open(r'G:\ai对话\11\data\_timbre_session2008.json', encoding='utf-8'))
sess = [r for r in d if r['tag'] == 'session20:08']
ref_plus = [r for r in d if r['tag'] == 'REF_plus']
ref_ts = [r for r in d if r['tag'] == 'REF_truesrc']


def med(rows, k):
    v = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
    return float(np.median(v)) if v else float('nan')


short = [r for r in sess if r['dur'] <= 3.5]
mid = [r for r in sess if 3.5 < r['dur'] <= 10]
long = [r for r in sess if r['dur'] > 10]

print('组            n   dur中位   b4_8k   b8_12k   f0中位  f0跨度(p90-p10)')
for name, rows in [('短句≤3.5s', short), ('中句3.5-10s', mid), ('长句>10s', long),
                   ('REF_plus直出', ref_plus), ('REF_真源', ref_ts)]:
    if not rows:
        continue
    span = med(rows, 'f0_p90') - med(rows, 'f0_p10')
    print('%-13s %2d  %7.2f  %6.2f  %7.2f  %7.1f  %8.1f'
          % (name, len(rows), med(rows, 'dur'), med(rows, 'b4_8k'),
             med(rows, 'b8_12k'), med(rows, 'f0_med'), span))

print()
print('短句 vs 长句：b4_8k 差 %+.2f dB，b8_12k 差 %+.2f dB'
      % (med(short, 'b4_8k') - med(long, 'b4_8k'),
         med(short, 'b8_12k') - med(long, 'b8_12k')))
print('本轮会话 vs plus 直出：b4_8k 差 %+.2f dB，b8_12k 差 %+.2f dB，f0 差 %+.1f Hz'
      % (med(sess, 'b4_8k') - med(ref_plus, 'b4_8k'),
         med(sess, 'b8_12k') - med(ref_plus, 'b8_12k'),
         med(sess, 'f0_med') - med(ref_plus, 'f0_med')))
print('plus 直出 vs 真源：b4_8k 差 %+.2f dB，b8_12k 差 %+.2f dB  （云端克隆本身的天花板）'
      % (med(ref_plus, 'b4_8k') - med(ref_ts, 'b4_8k'),
         med(ref_plus, 'b8_12k') - med(ref_ts, 'b8_12k')))

# 短句逐条
print()
print('短句逐条（时长 / b4_8k / b8_12k）：')
for r in sorted(short, key=lambda x: x['dur']):
    print('  %5.2fs  b4_8k=%6.2f  b8_12k=%7.2f  %s' % (r['dur'], r['b4_8k'], r['b8_12k'], r['file']))
