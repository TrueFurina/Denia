# -*- coding: utf-8 -*-
"""P0 验收工具：打完一通电话后跑这条命令，自动判四项指标是否达标。

用法：
    python tools/verify_p0_call.py              # 自动取最新的一通「我方」通话
    python tools/verify_p0_call.py <会话目录名>   # 指定某一通

判定口径全部来自服务端录像（H:/Comni .../recording.json）+ 客户端埋点
（data/call_trace_*.jsonl），不靠人工回忆。

达标线（基线来自 2026-09-23 22:55 那通）：
    [0] 改动生效     录像 system_prompt 含【接话方式       必须 YES
    [1] 人格         非身份证句占比 ≥ 55%                基线 37.5%
    [2] 开口率       SPEAK 占比落在 30%~55%              基线 26%（<30 哑火，>60 井喷）
    [3] 出声率       emit→真出声 ≥ 80%                   波动大，仅设下限
    [4] 延迟         端到端中位数 ≤ 4.0s                 基线 4.78s（顺带项）
"""
import glob
import io
import json
import os
import sys

ROOT = r'G:\ai对话\11'
SESS_BASE = r'H:/Comni/_internal/resources/apps/server/data/sessions'
TRACE_DIR = os.path.join(ROOT, 'data')

# 基线：均为 2026-09-23 22:55 那通（P0 改前）的实测值，且与本工具同一口径。
# 注意：此前口头说的「非身份证 14%」是另一种计数口径（只数"完整自我介绍句"），
# 本工具按"去重句是否含设定词"计，22:55 实测为 37.5%，以本口径为准。
BASE = {'persona': 37.5, 'speak': 26.0, 'played': 100.0, 'lat': 4.78}
# 出声率波动大（22:30 那通 50%，22:55 那通 100%），单通不可靠，故只设下限。
THRESH = {'persona': 55.0, 'played': 80.0, 'lat': 4.0}
SPEAK_OK = (30.0, 55.0)

# 「背设定」判据：句中出现任一词即算身份证句
ID_MARKERS = ['星炬', '学院', '虚质', '我叫', '我的名字', '我是达妮娅']


def load_json(p):
    return json.load(io.open(p, encoding='utf-8', errors='replace'))


def pick_session(arg):
    """选会话：指定则用指定的，否则取最新的『我方』通话（adx + 提示词非空）。"""
    if arg:
        cand = [d for d in glob.glob(os.path.join(SESS_BASE, '*'))
                if os.path.basename(d).startswith(arg) or os.path.basename(d) == arg]
        if not cand:
            cand = [arg] if os.path.isdir(arg) else []
        if not cand:
            print('找不到会话：%s' % arg)
            sys.exit(2)
        return cand[0]
    rows = []
    for d in glob.glob(os.path.join(SESS_BASE, '*')):
        rp = os.path.join(d, 'recording.json')
        if not os.path.isfile(rp):
            continue
        bid = os.path.basename(d)
        if '_adx_' not in bid:
            continue
        try:
            j = load_json(rp)
        except Exception:
            continue
        sp = (j.get('config') or {}).get('system_prompt') or ''
        if len(sp) < 200:      # 过短＝原生 Comni 手动跑的，不是我方客户端
            continue
        rows.append((os.path.getmtime(d), d))
    if not rows:
        print('没有找到我方通话录像')
        sys.exit(2)
    rows.sort()
    return rows[-1][1]


def pick_trace(sess_dir):
    """按 mtime 找与该会话最接近的客户端埋点。"""
    mt = os.path.getmtime(sess_dir)
    trs = glob.glob(os.path.join(TRACE_DIR, 'call_trace_*.jsonl'))
    if not trs:
        return None
    trs.sort(key=lambda p: abs(os.path.getmtime(p) - mt))
    return trs[0]


def aggregate_speaks(chunks):
    """引擎 result.text 是逐块碎片，必须按连续 SPEAK 段聚合成完整句子。"""
    out, buf = [], []
    for c in chunks:
        r = c.get('result') or {}
        mode = r.get('mode')
        tx = r.get('text') or ''
        if isinstance(tx, list):
            tx = ''.join(tx)
        if mode == 'SPEAK' and tx:
            buf.append(tx)
        else:
            if buf:
                out.append(''.join(buf))
                buf = []
    if buf:
        out.append(''.join(buf))
    return [s for s in (x.strip() for x in out) if s]


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    sess = pick_session(arg)
    bid = os.path.basename(sess)
    j = load_json(os.path.join(sess, 'recording.json'))
    cfg = j.get('config') or {}
    sp = cfg.get('system_prompt') or ''
    chunks = j.get('chunks') or []
    trace = pick_trace(sess)

    print('=' * 68)
    print('P0 验收  会话 %s' % bid)
    print('=' * 68)

    fails = []

    # ---------- [0] 改动是否生效 ----------
    applied = '【接话方式' in sp
    print('[0] 改动生效')
    print('    录像 system_prompt %d 字，含常驻块：%s' % (len(sp), 'YES' if applied else 'NO'))
    if not applied:
        fails.append('本通未吃到 P0 改动（prompt 里没有常驻块）——请重启 app 再测')
    print('    %s' % ('PASS' if applied else 'FAIL'))
    print()

    # ---------- [1] 人格：非身份证句占比 ----------
    sents = aggregate_speaks(chunks)
    uniq = []
    for s in sents:
        if s not in uniq:
            uniq.append(s)
    idn = [s for s in uniq if any(m in s for m in ID_MARKERS)]
    ratio = 100.0 * (len(uniq) - len(idn)) / len(uniq) if uniq else 0.0
    ok1 = ratio >= THRESH['persona']
    print('[1] 人格：非身份证句占比')
    print('    去重 %d 句，其中背设定 %d 句 → %.1f%%  （基线 %.1f%%  达标 ≥%.0f%%）'
          % (len(uniq), len(idn), ratio, BASE['persona'], THRESH['persona']))
    print('    %s' % ('PASS' if ok1 else 'FAIL'))
    if not ok1:
        fails.append('人格未达标')
    print('    她这通说的全部去重句：')
    for i, s in enumerate(uniq, 1):
        flag = '  ←背设定' if s in idn else ''
        print('      %2d. %s%s' % (i, s[:60], flag))
    # 「同一件事反复答」比单句背设定更能说明人格缺失：整通只在答一个问题
    if len(idn) >= 3 and len(uniq) <= 12:
        print('    注意：%d/%d 句都在答同一件事 —— 反复背设定，比单句更说明问题'
              % (len(idn), len(uniq)))
        if ok1:
            print('    （占比虽达标，但仍见重复应答，建议再打一通确认）')
    print()

    # ---------- [2] 开口率 ----------
    n = len(chunks)
    nspeak = sum(1 for c in chunks if (c.get('result') or {}).get('mode') == 'SPEAK')
    sp_ratio = 100.0 * nspeak / n if n else 0.0
    ok2 = SPEAK_OK[0] <= sp_ratio <= SPEAK_OK[1]
    print('[2] 开口率：SPEAK 占比')
    print('    %d/%d 块 = %.1f%%  （基线 %.1f%%  健康区 %.0f~%.0f%%）'
          % (nspeak, n, sp_ratio, BASE['speak'], SPEAK_OK[0], SPEAK_OK[1]))
    print('    %s' % ('PASS' if ok2 else 'FAIL'))
    if not ok2:
        hint = '偏低＝哑火（lps 偏大）' if sp_ratio < SPEAK_OK[0] else '偏高＝井喷（lps 偏小）'
        fails.append('开口率未达标（%s）' % hint)
        print('    提示：%s' % hint)
    print()

    # ---------- [3][4] 出声率与延迟（依赖客户端埋点）----------
    print('[3] 出声率  [4] 延迟')
    if not trace:
        print('    未找到对应客户端埋点，两项跳过')
        print()
    else:
        ev = []
        for line in io.open(trace, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line:
                try:
                    ev.append(json.loads(line))
                except Exception:
                    pass
        if not ev:
            print('    埋点为空，两项跳过')
            print()
        else:
            t0 = ev[0]['ts']
            rel = lambda ts: ts - t0
            # 【2026-09-24 口径修正】原实现用 30s 窗口做"有音频就算出声"：
            # 被打断没播的句子会被后面句子的音频认领 ⇒ 恒定虚高 100%
            # （00:58 那通实测 30 句只真出声 24 句，旧口径却报 100%）。
            # 现改为三段计数，一眼看出卡在"没进合成"还是"合成没出声"。
            n_out = sum(1 for e in ev if e.get('ev') == 'speak_out')
            n_begin = sum(1 for e in ev if e.get('ev') == 'tts_begin')
            n_audio = sum(1 for e in ev if e.get('ev') == 'tts_first_audio')
            n_barge = sum(1 for e in ev if e.get('ev') == 'tts_interrupt')
            audios = [rel(e['ts']) for e in ev if e.get('ev') == 'tts_first_audio']
            played = n_audio
            emits = list(range(n_out))
            pr = 100.0 * n_audio / n_out if n_out else 0.0
            ok3 = pr >= THRESH['played']
            print('    埋点 %s' % os.path.basename(trace))
            print('    送TTS %d → 进合成 %d → 真出声 %d   打断杀掉 %d 句'
                  % (n_out, n_begin, n_audio, n_barge))
            print('    出声率 %d/%d = %.1f%%  （达标 ≥%.0f%%）  %s'
                  % (played, len(emits), pr, THRESH['played'],
                     'PASS' if ok3 else 'FAIL'))
            if not ok3:
                fails.append('出声率未达标（闸门吞句或 TTS 串行或打断过多）')

            ends = []
            for e in ev:
                if e.get('ev') == 'asr' and e.get('sl') is not None:
                    ends.append(rel(e['ts']) - float(e['sl']))
            lats = []
            for se in ends:
                cand = [a for a in audios if a > se]
                if cand:
                    lats.append(min(cand) - se)
            if lats:
                lats.sort()
                med = lats[len(lats) // 2]
                ok4 = med <= THRESH['lat']
                print('    延迟中位 %.2fs  （基线 %.2fs  达标 ≤%.1fs）  %s'
                      % (med, BASE['lat'], THRESH['lat'], 'PASS' if ok4 else 'FAIL'))
                print('    逐轮：%s' % ', '.join('%.2f' % x for x in lats))
                if not ok4:
                    fails.append('延迟未达标')
            else:
                print('    延迟：样本不足（需要 asr + tts_first_audio 成对）')
    print()

    print('=' * 68)
    if fails:
        print('未达标项：')
        for f in fails:
            print('  - %s' % f)
    else:
        print('全部达标')
    print('=' * 68)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
