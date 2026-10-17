#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
通话质量六维评分器（自定义标尺，非业界标准）

用法:
    python tools/rate_call_quality.py [会话目录名]
    不带参数 → 自动取最新一通 adx（通话档）

六维（每项 0~100，加权总分）:
    S1 人设稳定   不在不当场合背设定
    S2 语义正确   话说得通、答得对
    S3 情绪回应   接住用户情绪而非只处理字面
    S4 节奏自然   反问率 / 碎片率 / 句长
    S5 记忆连贯   能引用刚才说过的事
    S6 响应速度   端到端延迟
"""
import io
import os
import re
import sys
import json
import glob
import collections

BASE = r'H:/Comni/_internal/resources/apps/server/data/sessions'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 取会话
def pick_session(arg):
    if arg:
        cand = glob.glob(os.path.join(BASE, arg + '*'))
        if cand:
            return os.path.basename(cand[0])
        return arg
    rows = []
    for d in glob.glob(os.path.join(BASE, '*')):
        n = os.path.basename(d)
        if '_adx_' in n:
            rows.append((os.path.getmtime(d), n))
    rows.sort(reverse=True)
    return rows[0][1] if rows else None


def sess_path(sid):
    p = os.path.join(BASE, sid, 'recording.json')
    return p if os.path.isfile(p) else None


def pick_trace(sid):
    """按时间戳前缀匹配客户端埋点"""
    m = re.match(r'(\d{8})_(\d{6})_', sid or '')
    if not m:
        return None
    pat = 'data/call_trace_*_%s_%s.jsonl' % (m.group(1), m.group(2))
    cand = glob.glob(os.path.join(ROOT, pat))
    if cand:
        return cand[0]
    # 宽容匹配：同一分钟前后 90 秒
    import datetime
    dt = datetime.datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
    best, bestd = None, 1e9
    for f in glob.glob(os.path.join(ROOT, 'data/call_trace_*.jsonl')):
        mm = re.search(r'(\d{8})_(\d{6})', os.path.basename(f))
        if not mm:
            continue
        d2 = datetime.datetime.strptime(mm.group(1) + mm.group(2), '%Y%m%d%H%M%S')
        delta = abs((d2 - dt).total_seconds())
        if delta < bestd:
            best, bestd = f, delta
    return best if bestd < 120 else None


# ---------------------------------------------------------------- 判据词表
# 设定词：命中即视为"背设定"
IDN_WORDS = ['达妮娅', '星炬', '虚质', '学院']
# 明确在报身份的句式
IDN_PAT = re.compile(r'(我叫|我的名字|我是.{0,6}(学生|助手|AI|人工智能))')
# 反问句标志
Q_PAT = re.compile(r'[？?]$')
# 语气/情绪承接词（说明她接住了情绪而不是只处理字面）
EMO_WORDS = ['累', '休息', '难受', '失望', '磨人', '闷', '辛苦', '心疼',
             '扛得住', '挺住', '暖和', '夸', '陪着', '懂']
# 明显语病/截断标志
BROKEN_PAT = re.compile(r'(</?think|并不太喜|独立又。|来有点闷|时候想)')


def load(sid):
    p = sess_path(sid)
    if not p:
        return None, None
    j = json.load(io.open(p, encoding='utf-8', errors='replace'))
    ch = j.get('chunks') or []
    # 按连续 SPEAK 段聚合（引擎 text 是逐块碎片）
    segs, cur, buf = [], None, ''
    for c in ch:
        r = c.get('result') or {}
        mode = r.get('mode') or ''
        txt = r.get('text') or ''
        if isinstance(txt, list):
            txt = ''.join(txt)
        if mode == 'SPEAK':
            if cur is None:
                cur = c.get('receive_ts_ms')
            buf += txt
        else:
            if buf.strip():
                segs.append((cur, buf.strip()))
            cur, buf = None, ''
    if buf.strip():
        segs.append((cur, buf.strip()))
    return j, segs


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    sid = pick_session(arg)
    print('=' * 70)
    print('通话质量评分  会话 %s' % sid)
    print('=' * 70)
    j, segs = load(sid)
    if j is None:
        print('找不到会话录像')
        return 1

    sp = (j.get('config') or {}).get('system_prompt') or ''
    print('system_prompt %d 字，含常驻块=%s' % (len(sp), '【接话方式' in sp))
    ch = j.get('chunks') or []
    n_speak = sum(1 for c in ch if ((c.get('result') or {}).get('mode')) == 'SPEAK')
    print('引擎块 %d，SPEAK %d (%.1f%%)' % (len(ch), n_speak,
                                            100.0 * n_speak / len(ch) if ch else 0))
    print()

    # 客户端埋点
    tp = pick_trace(sid)
    ev = []
    if tp:
        for line in io.open(tp, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line:
                try:
                    ev.append(json.loads(line))
                except Exception:
                    pass
        print('埋点 %s  %d 事件' % (os.path.basename(tp), len(ev)))
    else:
        print('埋点 (未找到)')
    print()

    # 实际播出的句子（speak_out = 送 TTS）
    spoken = []
    for e in ev:
        if e.get('ev') == 'speak_out':
            t = (e.get('text') or '').strip()
            if t:
                spoken.append(t)
    # 去重保序
    seen = set()
    uniq = []
    for s in spoken:
        if s not in seen:
            seen.add(s)
            uniq.append(s)

    asr = [(e.get('clean') or e.get('raw') or '') for e in ev if e.get('ev') == 'asr']
    asr = [a for a in asr if a]

    # ---------------- S1 人设稳定 ----------------
    idn = []
    for s in uniq:
        hit = [w for w in IDN_WORDS if w in s]
        if hit and (IDN_PAT.search(s) or len(hit) >= 2):
            idn.append(s)
    n_idn = len(idn)
    s1 = 100.0 * (1 - n_idn / float(len(uniq))) if uniq else 0

    # ---------------- S2 语义正确 ----------------
    broken = [s for s in uniq if BROKEN_PAT.search(s)]
    n_broken = len(broken)
    s2 = 100.0 * (1 - n_broken / float(len(uniq))) if uniq else 0

    # ---------------- S3 情绪回应 ----------------
    emo = [s for s in uniq if any(w in s for w in EMO_WORDS)]
    s3 = min(100.0, 100.0 * len(emo) / max(1, int(len(uniq) * 0.35)))

    # ---------------- S4 节奏自然 ----------------
    n_q = sum(1 for s in uniq if Q_PAT.search(s))
    q_rate = 100.0 * n_q / len(uniq) if uniq else 0
    frag = [s for s in uniq if len(re.sub(r'[^\u4e00-\u9fa5]', '', s)) <= 6]
    frag_rate = 100.0 * len(frag) / len(uniq) if uniq else 0
    lens = [len(re.sub(r'[^\u4e00-\u9fa5]', '', s)) for s in uniq]
    avg_len = sum(lens) / float(len(lens)) if lens else 0
    # 反问率目标 15~25%，碎片目标 <10%，句长目标 15~25 字
    s4 = 100.0
    s4 -= max(0, q_rate - 25) * 1.8          # 每超 1 个百分点扣 1.8
    s4 -= max(0, frag_rate - 10) * 2.0
    s4 -= max(0, 15 - avg_len) * 3.0         # 句长不足每字扣 3
    s4 = max(0, min(100, s4))

    # ---------------- S5 记忆连贯 ----------------
    # 判据：用户问"我刚才说啥"类问题时，她的回答是否引用了先前内容
    recall_q = [a for a in asr if re.search(r'(刚才|之前|前面|说啥|说什么来着|还记得)', a)]
    recall_ok = 0
    if recall_q:
        for s in uniq:
            if re.search(r'(你说过|你刚才|你提过|记得你|你说的是)', s):
                recall_ok += 1
    s5 = 100.0 if (recall_q and recall_ok) else (50.0 if not recall_q else 0.0)

    # ---------------- S6 响应速度 ----------------
    lat = []
    for e in ev:
        if e.get('ev') in ('tts_first_audio', 'tts_begin'):
            v = e.get('se')
            if isinstance(v, (int, float)):
                lat.append(v)
    med = sorted(lat)[len(lat) // 2] if lat else 0
    # 端到端中位（复用 turn_end 的 sl 字段：用户说完→她说完）
    e2e = []
    for e in ev:
        if e.get('ev') == 'turn_end' and isinstance(e.get('sl'), (int, float)):
            e2e.append(e['sl'])
    e2e.sort()
    e2e_med = e2e[len(e2e) // 2] if e2e else 0
    # 标尺对齐业界：≤1.0s 满分（级联优化水位 0.4~0.8s），≥4.0s 零分
    s6 = max(0, min(100, 100.0 * (4.0 - e2e_med) / 3.0))

    # ---------------- 输出 ----------------
    print('-' * 70)
    print('S1 人设稳定   背设定 %d/%d 句            → %5.1f' % (n_idn, len(uniq), s1))
    for s in idn:
        print('      背设定句: %s' % s[:50])
    print('S2 语义正确   语病/截断 %d/%d 句         → %5.1f' % (n_broken, len(uniq), s2))
    for s in broken:
        print('      不通顺: %s' % s[:50])
    print('S3 情绪回应   含承接词 %d 句（目标≥%d）  → %5.1f'
          % (len(emo), int(len(uniq) * 0.35), s3))
    print('S4 节奏自然   反问 %.1f%%  碎片 %.1f%%  均长 %.1f 字 → %5.1f'
          % (q_rate, frag_rate, avg_len, s4))
    print('S5 记忆连贯   用户回问 %d 次，接住 %d 次   → %5.1f'
          % (len(recall_q), recall_ok, s5))
    for a in recall_q:
        print('      用户回问: %s' % a[:40])
    print('S6 响应速度   端到端中位 %.2fs            → %5.1f' % (e2e_med, s6))
    print('-' * 70)

    W = {'S1': 0.20, 'S2': 0.20, 'S3': 0.15, 'S4': 0.15, 'S5': 0.10, 'S6': 0.20}
    sc = {'S1': s1, 'S2': s2, 'S3': s3, 'S4': s4, 'S5': s5, 'S6': s6}
    total = sum(sc[k] * W[k] for k in W)
    for k in sorted(W):
        print('  %s  %.1f  权重 %.0f%%' % (k, sc[k], W[k] * 100))
    print('-' * 70)
    print('  加权总分  %.1f / 100' % total)

    lv = ('不可用' if total < 40 else
          '能对话但不自然' if total < 60 else
          '接近可接受' if total < 75 else
          '可用' if total < 88 else '接近真人')
    print('  档位      %s' % lv)
    print('=' * 70)

    # ASR 噪声率
    noise = [a for a in asr if len(re.sub(r'[^\u4e00-\u9fa5]', '', a)) <= 2]
    print()
    print('ASR 转写 %d 条，其中疑似噪声(≤2字) %d 条 = %.0f%%'
          % (len(asr), len(noise), 100.0 * len(noise) / len(asr) if asr else 0))
    print('  噪声样本: %s' % noise[:10])

    # 落盘
    out = {
        'sid': sid, 'scores': sc, 'total': round(total, 1), 'level': lv,
        'uniq': uniq, 'asr': asr, 'q_rate': round(q_rate, 1),
        'frag_rate': round(frag_rate, 1), 'avg_len': round(avg_len, 1),
        'e2e_med': round(e2e_med, 2), 'n_idn': n_idn, 'n_broken': n_broken,
    }
    od = os.path.join(ROOT, 'data', 'quality')
    if not os.path.isdir(od):
        os.makedirs(od)
    with open(os.path.join(od, sid + '.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print('已落盘 data/quality/%s.json' % sid)
    return 0


if __name__ == '__main__':
    sys.exit(main())
