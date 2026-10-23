# -*- coding: utf-8 -*-
"""通话「轮次控制」体检器 —— 读 Comni 服务端会话录像，量化引擎的 LISTEN/SPEAK 裁决。
不进闸门：一次性研究脚本——要读开发机 H:\Comni 服务端会话目录才能跑，换机即废；
判据未进自动闸门（2026-10-22 普查定性，见 tools/check_gate_registry.py）。

为什么需要它：客户端的 trace 只记录"我们推了什么"，看不到"引擎怎么裁决"。
引擎自己把每块的真实 mode 落盘在：
    H:\\Comni\\_internal\\resources\\apps\\server\\data\\sessions\\<会话>/recording.json
这是 turn-taking 问题的**地面真值**。

健康区间（2026-09-23 实测标定，同一 system_prompt）：
    SPEAK 占比   < 5%   哑火（她不回应，只看得到兜底句）
                20~35%  健康
                 > 50%   井喷（一次发声触发独白）
    裁决频率     1.0 Hz 官方（chunk_ms=1000）；2.0 Hz 说明客户端把 1s 音频拆成两段推
    wall_clock   单块裁决耗时，健康 < 200ms

用法：
    python tools/check_turn_taking.py            # 最近 8 通
    python tools/check_turn_taking.py 12         # 最近 12 通
    python tools/check_turn_taking.py 8 --all    # 含非通话档会话
"""
import io
import json
import os
import sys
import collections
import time

SESS_ROOT = r'H:\Comni\_internal\resources\apps\server\data\sessions'

HEALTH = [
    (0.0, 0.05, '哑火', '她不回应：调小 duplex_call_listen_prob_scale（-0.05 一档）'),
    (0.05, 0.20, '偏沉默', '可接受但偏静；若体感"不理人"再减 0.05'),
    (0.20, 0.35, '健康', ''),
    (0.35, 0.50, '偏话多', '留意是否出现独白；必要时 +0.05'),
    (0.50, 1.01, '井喷', '一次发声触发独白：调大 duplex_call_listen_prob_scale（+0.05）'),
]


def verdict(ratio):
    for lo, hi, name, tip in HEALTH:
        if lo <= ratio < hi:
            return name, tip
    return '?', ''


def load(sess_dir):
    p = os.path.join(sess_dir, 'recording.json')
    if not os.path.exists(p):
        return None
    try:
        rec = json.loads(io.open(p, encoding='utf-8', errors='replace').read())
    except Exception:
        # trace 文件解析失败 → 返回 None（研究脚本，人工诊断用）
        return None
    cfg = rec.get('config') or {}
    ch = rec.get('chunks') or []
    if not ch:
        return None
    ct = collections.Counter()
    speak_at = []
    walls = []
    t0 = ch[0].get('receive_ts_ms')
    for c in ch:
        r = c.get('result') or {}
        if not isinstance(r, dict):
            continue
        m = r.get('mode')
        ct[m] += 1
        if m == 'SPEAK':
            t = c.get('receive_ts_ms')
            if t and t0:
                speak_at.append((t - t0) / 1000.0)
            tm = r.get('timing') or {}
            if tm.get('wall_clock_ms'):
                walls.append(tm['wall_clock_ms'])
    n = sum(ct.values()) or 1
    span = None
    hz = None
    if t0 and ch[-1].get('receive_ts_ms'):
        span = (ch[-1]['receive_ts_ms'] - t0) / 1000.0
        if span > 0:
            hz = (len(ch) - 1) / span
    return {
        'name': os.path.basename(sess_dir),
        'cfg': cfg, 'n': n, 'listen': ct.get('LISTEN', 0), 'speak': ct.get('SPEAK', 0),
        'ratio': ct.get('SPEAK', 0) / float(n),
        'span': span, 'hz': hz, 'speak_at': speak_at,
        'wall_med': (sorted(walls)[len(walls) // 2] if walls else None),
        'mtime': os.path.getmtime(sess_dir),
        'prompt_len': len(cfg.get('system_prompt') or ''),
    }


def main():
    limit = 8
    show_all = False
    for a in sys.argv[1:]:
        if a == '--all':
            show_all = True
        elif a.isdigit():
            limit = int(a)

    if not os.path.isdir(SESS_ROOT):
        print('找不到服务端会话目录:', SESS_ROOT)
        return 2

    dirs = [os.path.join(SESS_ROOT, d) for d in os.listdir(SESS_ROOT)]
    dirs = [d for d in dirs if os.path.isdir(d)]
    dirs.sort(key=lambda d: os.path.getmtime(d), reverse=True)

    rows = []
    for d in dirs:
        r = load(d)
        if r:
            rows.append(r)
        if len(rows) >= limit:
            break
    rows.reverse()  # 时间正序

    if not rows:
        print('没有可解析的会话录像')
        return 1

    print('=' * 104)
    print('通话轮次体检  (源: Comni 服务端录像 recording.json)')
    print('=' * 104)
    hdr = '%-22s %5s %6s %6s %7s %6s %7s %6s %s' % (
        '会话', 'lps', 'mns', 'flc', '裁决Hz', 'SPEAK', '占比', 'wall', '判定')
    print(hdr)
    print('-' * 104)
    for r in rows:
        c = r['cfg']
        v, tip = verdict(r['ratio'])
        print('%-22s %5s %5s %5s %7s %6d %6.1f%% %6s %s%s' % (
            r['name'][-22:],
            c.get('listen_prob_scale'), c.get('max_new_speak_tokens_per_chunk'),
            c.get('force_listen_count'),
            ('%.2f' % r['hz']) if r['hz'] else '-',
            r['speak'], r['ratio'] * 100,
            ('%dms' % r['wall_med']) if r['wall_med'] else '-',
            v, ('  <-- ' + tip) if tip else ''))
    print('-' * 104)

    last = rows[-1]
    print()
    print('最近一通: %s' % last['name'])
    print('  时长 %.1fs / %d 块 / 裁决 %.2f Hz / SPEAK %d 块 (%.1f%%)' % (
        last['span'] or 0, last['n'], last['hz'] or 0, last['speak'], last['ratio'] * 100))
    if last['speak_at']:
        segs = []
        prev = None
        for t in last['speak_at']:
            if prev is None or t - prev > 30:
                segs.append([t, t])
            else:
                segs[-1][1] = t
            prev = t
        print('  开口时段: ' + ' / '.join('+%.0f~%.0fs' % (a, b) for a, b in segs))
        if last['span'] and last['speak_at'] and (last['span'] - last['speak_at'][-1]) > 60:
            print('  !! 最后一次开口 +%.0fs 之后，有 %.0f 秒零开口（典型哑火尾巴）' % (
                last['speak_at'][-1], last['span'] - last['speak_at'][-1]))
    v, tip = verdict(last['ratio'])
    print('  判定: %s%s' % (v, ('  → ' + tip) if tip else ''))
    print('  当前配置 lps=%s mns=%s（config.yaml 的 duplex_call_* 同名键）' % (
        last['cfg'].get('listen_prob_scale'), last['cfg'].get('max_new_speak_tokens_per_chunk')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
