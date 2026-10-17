"""诊断：客户端送 TTS 的文本 vs 引擎录像里的模型原文，查丢字/截断/错序。

方法：
  1) 服务端录像按「连续 SPEAK 段」聚合成 utterance（引擎原文，地面真值）
  2) 客户端埋点取 speak_out / tts_begin（实际送去合成的文本）
  3) 逐条比对：客户端文本是否是引擎原文的子串 / 前缀丢失 / 含 </think>
"""
import io, os, json, glob, re

SESS = r'H:/Comni/_internal/resources/apps/server/data/sessions/20260924_005838_adx_1790175322099'
if not os.path.isdir(SESS):
    cands = sorted(glob.glob(r'H:/Comni/_internal/resources/apps/server/data/sessions/20260924_005838*'))
    SESS = cands[0] if cands else None
TRACE = r'G:\ai对话\11\data\call_trace_omni_20260924_005833.jsonl'
CLEAN = re.compile(r'<[^>]{1,40}>')  # <think> </think> 等标签


def clean(s):
    return CLEAN.sub('', s or '').strip()


def engine_utterances():
    d = json.load(io.open(os.path.join(SESS, 'recording.json'), encoding='utf-8', errors='replace'))
    ch = d.get('chunks') or []
    utter, cur = [], ''
    for c in ch:
        r = c.get('result') or {}
        txt = r.get('text') or ''
        if isinstance(txt, list):
            txt = ''.join(txt)
        if r.get('mode') == 'SPEAK':
            cur += txt
        else:
            if cur.strip():
                utter.append(clean(cur))
            cur = ''
    if cur.strip():
        utter.append(clean(cur))
    return utter


def client_texts():
    ev = []
    for line in io.open(TRACE, encoding='utf-8', errors='replace'):
        line = line.strip()
        if line:
            try:
                ev.append(json.loads(line))
            except Exception:
                pass
    outs = [(e.get('ts'), e.get('text', ''), 'speak_out') for e in ev if e.get('ev') == 'speak_out']
    begs = [(e.get('ts'), e.get('chars'), 'tts_begin') for e in ev if e.get('ev') == 'tts_begin']
    frags = [(e.get('ts'), e.get('text', ''), e.get('ev')) for e in ev
             if e.get('ev') in ('speak_frag', 'speak_drop', 'speak_defer')]
    return ev, outs, begs, frags


def main():
    utt = engine_utterances()
    ev, outs, begs, frags = client_texts()
    print('=' * 78)
    print('文本完整性诊断  会话 %s' % os.path.basename(SESS))
    print('=' * 78)
    print('引擎原文 utterance 数：%d' % len(utt))
    print('客户端 speak_out 数：%d   tts_begin 数：%d' % (len(outs), len(begs)))
    print()
    print('=== 引擎原文（去掉 think 标签后的完整句）===')
    allsrc = ''
    for i, u in enumerate(utt, 1):
        print('  %2d. [%d字] %s' % (i, len(u), u[:70]))
        allsrc += u
    print()
    print('=== 客户端实际送 TTS（speak_out）===')
    allcli = ''
    for i, (ts, t, k) in enumerate(outs, 1):
        print('  %2d. [%d字] %s' % (i, len(t), t[:70]))
        allcli += t
    print()
    print('=== 完整性检查 ===')
    print('  引擎总字数 %d   客户端总字数 %d   差 %+d'
          % (len(allsrc), len(allcli), len(allcli) - len(allsrc)))
    # 逐条：客户端文本能否在引擎原文里找到
    notfound = []
    for t in [x[1] for x in outs]:
        tt = clean(t)
        if not tt:
            continue
        if tt not in allsrc:
            # 找最长前缀匹配，判断丢了多少
            best = 0
            for i in range(len(allsrc)):
                j = 0
                while j < len(tt) and i + j < len(allsrc) and allsrc[i + j] == tt[j]:
                    j += 1
                if j > best:
                    best = j
            notfound.append((tt, best))
    print('  客户端文本在引擎原文里找不到的：%d / %d' % (len(notfound), len(outs)))
    for tt, best in notfound[:20]:
        print('    ✗ %r  最长连续匹配 %d/%d 字' % (tt[:40], best, len(tt)))
    print()
    print('=== 残留 think 标签 ===')
    for i, (ts, t, k) in enumerate(outs, 1):
        if '<' in t and '>' in t:
            print('  %2d. %r' % (i, t[:80]))
    print()
    print('=== 被丢弃/延迟的片段 ===')
    for ts, t, k in frags:
        print('  %-12s %r' % (k, str(t)[:60]))


if __name__ == '__main__':
    main()
