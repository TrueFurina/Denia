# -*- coding: utf-8 -*-
"""井喷根因量化：决策频率 / 生成速率 vs 播放速率 / 回声检查 / barge 生效情况。"""
import json, io, os, collections, datetime

F = r'G:\ai对话\11\data\call_trace_omni_20260923_200843.jsonl'
rows = []
for ln in io.open(F, encoding='utf-8', errors='replace'):
    ln = ln.strip()
    if not ln:
        continue
    try:
        e = json.loads(ln)
    except Exception:
        continue
    rows.append(e)


def ts(s):
    try:
        h, m, sec = s.split(':')
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except Exception:
        return None


fr = [e for e in rows if e.get('ev') == 'frame']
print('=' * 74)
print('1) 决策频率（客户端推 audio_chunk 的实际节奏）')
print('=' * 74)
dts = []
prev = None
for e in fr:
    t = ts(e.get('t', ''))
    if t is None:
        continue
    if prev is not None:
        dts.append(round(t - prev, 3))
    prev = t
c = collections.Counter(dts)
tot = len(dts)
for v, n in sorted(c.items()):
    print('   间隔 %.2fs  ×%-4d  %5.1f%%' % (v, n, 100.0 * n / tot))
avg = sum(dts) / len(dts)
print('   → 平均间隔 %.3fs  ⇒ 推流频率 %.2f Hz' % (avg, 1.0 / avg))
print('   官方 chunk_ms=1000 ⇒ 1.00 Hz')

print()
print('=' * 74)
print('2) 井喷时段（20:10:16 起）逐句时间线')
print('=' * 74)
t0 = ts('20:10:10')
seq = []
for e in rows:
    ev = e.get('ev')
    if ev not in ('emit', 'tts_recv', 'tts_pick', 'tts_begin', 'tts_first_audio',
                  'tts_drop', 'speak_drop', 'speak_defer', 'model', 'user_voice', 'barge',
                  'tts_interrupt', 'asr', 'speak_pass', 'turn_end'):
        continue
    t = ts(e.get('t', ''))
    if t is None or t < t0:
        continue
    seq.append((t, ev, e))
seq.sort(key=lambda x: x[0])
for t, ev, e in seq:
    txt = e.get('text', '')
    extra = ''
    for k in ('reason', 'chars', 'n', 'waited', 'since_user', 'since_ai',
              'duration', 'flag', 'rms', 'thr', 'pk', 'is_listen', 'len'):
        if k in e and k != 'text':
            extra += ' %s=%s' % (k, e[k])
    st = e.get('t', '')
    tag = '%s' % st
    print('%-13s %-15s %-4s %s' % (tag, ev, len(txt) if txt else '', (txt[:60] + extra)[:150]))

print()
print('=' * 74)
print('3) 生成速率 vs 播放速率（emit 字数 / tts_begin 音频时长）')
print('=' * 74)
emits = [(ts(e.get('t', '')), len(e.get('text', '') or ''), e.get('text', ''))
         for e in rows if e.get('ev') == 'emit']
emits = [x for x in emits if x[0] is not None]
if len(emits) >= 2:
    span = emits[-1][0] - emits[0][0]
    tot_chars = sum(x[1] for x in emits)
    print('   emit 句数 %d，总字数 %d，跨度 %.1fs ⇒ 生成速率 %.2f 字/秒'
          % (len(emits), tot_chars, span, tot_chars / span if span else 0))
b = [(ts(e.get('t', '')), e.get('duration') or e.get('dur') or e.get('len'))
     for e in rows if e.get('ev') == 'tts_begin']
print('   tts_begin 明细:', b)
# 排空时间：从第一句 tts_begin 到最后一帧
if b and fr:
    print('   第一句 tts_begin %s → 会话末帧 %s' % (b[0][0], fr[-1].get('t')))
# 竞品基准
print('   真人/竞品基准：中文口语 ≈ 4~5 字/秒；官方 max_new_speak_tokens_per_chunk=20')

print()
print('=' * 74)
print('4) 回声检查：她说话时麦克风电平是否把她的声音收进去')
print('=' * 74)
spk_windows = []
for e in rows:
    if e.get('ev') == 'tts_first_audio':
        t = ts(e.get('t', ''))
        if t:
            spk_windows.append(t)
print('   她出声时刻:', [x for x in spk_windows])
pk_her, pk_quiet = [], []
for e in fr:
    t = ts(e.get('t', ''))
    if t is None:
        continue
    pk = e.get('pk')
    if pk is None:
        continue
    near = any(abs(t - s) < 3.0 for s in spk_windows)
    (pk_her if near else pk_quiet).append(pk)


def stat(a, name):
    if not a:
        print('   %-16s (无)' % name)
        return
    a2 = sorted(a)
    print('   %-16s n=%-4d 中位 %.5f  p90 %.5f  max %.5f'
          % (name, len(a2), a2[len(a2) // 2], a2[int(len(a2) * 0.9)], a2[-1]))


stat(pk_quiet, '她没出声时 mic')
stat(pk_her, '她出声±3s 时 mic')
print('   （底噪基准 6e-5；若"她出声时"显著接近说话电平 0.018~0.074 ⇒ 有回声串入）')

print()
print('=' * 74)
print('5) 打断机制是否生效')
print('=' * 74)
for e in rows:
    if e.get('ev') in ('barge', 'tts_interrupt'):
        print('   %s %s %s' % (e.get('t'), e.get('ev'),
                               {k: v for k, v in e.items() if k not in ('t', 'ev')}))
print('   历史对照：19:46 通 0 次、19:48 通 0 次（barge_min_rms=0.07 太高）')
print('   本轮 barge_min_rms=0.03 已生效')

print()
print('=' * 74)
print('6) force_listen 实际下发情况')
print('=' * 74)
fl_true = sum(1 for e in fr if e.get('fl') is True)
fl_false = sum(1 for e in fr if e.get('fl') is False)
print('   帧总数 %d：fl=True %d 帧（%.1f%%），fl=False %d 帧'
      % (len(fr), fl_true, 100.0 * fl_true / max(1, len(fr)), fl_false))

print()
print('=' * 74)
print('7) 模型侧：model 事件里的 is_listen / 产出统计')
print('=' * 74)
mdl = [e for e in rows if e.get('ev') == 'model']
print('   model 事件 %d 个，字段样例： %s' % (len(mdl), sorted(mdl[0].keys()) if mdl else '-'))
lis = collections.Counter()
for e in mdl:
    for k in ('is_listen', 'listen', 'speak'):
        if k in e:
            lis[(k, e[k])] += 1
            break
for k, v in lis.most_common():
    print('   %s' % (k,), v)
