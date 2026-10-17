# -*- coding: utf-8 -*-
"""生成 22:55 通话的诊断报告：延迟三段账 + 人格（产出内容）实录。"""
import io
import json
import os

ROOT = r'G:\ai对话\11'
os.chdir(ROOT)

TRACE = 'data/call_trace_omni_20260923_225519.jsonl'
SESS = (r'H:\Comni\_internal\resources\app\server\sessions').replace(
    'app\\server', '_internal\\resources\\apps\\server\\data')
SESS = r'H:/Comni/_internal/resources/apps/server/data/sessions/20260923_225525_adx_1790175322099'

ev = [json.loads(l) for l in io.open(TRACE, encoding='utf-8') if l.strip()]
t0 = ev[0]['ts']


def rel(ts):
    return round(ts - t0, 2)


# ---------- 用户"说完"时刻：ASR 落定 - sl（静音时长） ----------
speech_end = []
for e in ev:
    if e.get('ev') == 'asr':
        speech_end.append((rel(e['ts']), e.get('clean') or e.get('raw'), e.get('sl')))

# ---------- 引擎视角：模型每帧（listen / 文本） ----------
frames = [(rel(e['ts']), bool(e.get('listen')), (e.get('text') or ''))
          for e in ev if e.get('ev') == 'model']

# ---------- 出声时刻 ----------
first_audio = [rel(e['ts']) for e in ev if e.get('ev') == 'tts_first_audio']
turn_end = [(rel(e['ts']), e.get('text'), e.get('action')) for e in ev
            if e.get('ev') == 'turn_end' and e.get('action') == 'emit']
emits = [(rel(e['ts']), e.get('text')) for e in ev if e.get('ev') == 'emit']


def first_frame_at_or_after(t, need_text=True):
    for ft, lis, tx in frames:
        if ft >= t and (not need_text or tx):
            return ft
    return None


def listen_after(t):
    for ft, lis, tx in frames:
        if ft > t and lis:
            return ft
    return None


rows = []
for i, (eat, ec, esl) in enumerate(speech_end):
    if esl is None:
        continue
    speak_end = round(eat - esl, 2)          # 用户最后一个字
    # 该轮对应的 emit：emit 时间 > speak_end 的第一个
    cand = [x for x in turn_end if x[0] > speak_end]
    if not cand:
        continue
    et, etext = cand[0][0], cand[0][1]
    # 模型首字
    tstart = None
    for ft, lis, tx in frames:
        if ft > speak_end and not lis and tx:
            tstart = ft
            break
    if tstart is None:
        continue
    ta = next((x for x in first_audio if x > et - 0.01), None)
    rows.append({
        'user_text': ec, 'user_end': speak_end,
        'model_first_token': tstart,
        'engine_hesitate': round(tstart - speak_end, 2),
        'wait_full': round(et - tstart, 2),
        'tts_first': (round(ta - et, 2) if ta else None),
        'audio_at': ta,
        'total': (round(ta - speak_end, 2) if ta else None),
        'ai_text': etext,
    })

# ---------- 服务端录像 ----------
rec = json.load(io.open(os.path.join(SESS, 'recording.json'), encoding='utf-8', errors='replace'))
ch = rec['chunks']
n_speak = sum(1 for c in ch if (c.get('result') or {}).get('mode') == 'SPEAK')
sp_texts = []
for c in ch:
    r = c.get('result') or {}
    if r.get('mode') == 'SPEAK':
        t = r.get('text') or ''
        if isinstance(t, list):
            t = ''.join(t)
        if t.strip():
            sp_texts.append((c['receive_ts_ms'] / 1000.0, t.strip()))

out = {
    'rows': rows,
    'server': {
        'chunks': len(ch), 'speak_blocks': n_speak,
        'speak_pct': round(100.0 * n_speak / len(ch), 1),
        'span_s': round((ch[-1]['receive_ts_ms'] - ch[0]['receive_ts_ms']) / 1000.0, 1),
        'sp_texts': sp_texts,
        'lps': rec['config'].get('listen_prob_scale'),
        'fi': 0.5,
        'prompt_len': len(rec['config'].get('system_prompt') or ''),
        'ref_audio': rec['config'].get('ref_audio'),
        'force_listen': rec['config'].get('force_listen_count'),
    },
    'client': {
        'emit': len(emits), 'audio': len(first_audio),
        'trace_s': round(ev[-1]['ts'] - t0, 1),
    },
}
io.open('data/diag_call_2255.json', 'w', encoding='utf-8').write(
    json.dumps(out, ensure_ascii=False, indent=1))

print('用户说完 → 出声  逐回合')
print('%-26s %7s %7s %7s %7s | %s' % ('用户说', '引擎起手', '等整句', 'TTS首包', '合计', '她答'))
for r in rows:
    print('%-26s %6.2fs %6.2fs %6.2fs %6.2fs | %s'
          % (r['user_text'][:24], r['engine_hesitate'], r['wait_full'],
             r['tts_first'] or 0, r['total'] or 0, r['ai_text'][:26]))
vals = [r for r in rows if r['total']]
if vals:
    n = len(vals)
    print('\n均值：引擎起手 %.2fs / 等整句 %.2fs / TTS首包 %.2fs / 合计 %.2fs'
          % (sum(v['engine_hesitate'] for v in vals) / n,
             sum(v['wait_full'] for v in vals) / n,
             sum(v['tts_first'] or 0 for v in vals) / n,
             sum(v['total'] for v in vals) / n))
print('\n服务端：%d 块 %.1fs，SPEAK %d 块 = %.1f%%（lps=%s / 保护期 %s 帧）'
      % (out['server']['chunks'], out['server']['span_s'], out['server']['speak_blocks'],
         out['server']['speak_pct'], out['server']['lps'], out['server']['force_listen']))
print('客户端：上屏 %d 次 → 真出声 %d 次' % (out['client']['emit'], out['client']['audio']))
print('参考音（引擎实际用的）：%s' % out['server']['ref_audio'])
print('\n她这 47 秒说过的全部内容：')
for t, s in sp_texts:
    print('   %6.1fs  %s' % (t, s))
