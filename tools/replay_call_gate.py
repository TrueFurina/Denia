# -*- coding: utf-8 -*-
"""通话档闸门离线回放：统计拦截/裁剪/误伤。

两路数据源
----------
1. **引擎录像**（`H:\\Comni\\...\\sessions\\*\\recording.json`，947 通）
   回放 `self_echo`（自复读）—— 录像里带逐块 `result.text` + `result.mode`，
   覆盖面远大于客户端 trace，且是**服务端地面真值**。
   状态演变按真实实现模拟：
       放行出口 → `_recent_messages`(5) + `_duplex_ai_lines`(12)
       被拦出口 → **只** `_recent_messages`(5)
2. **客户端 trace**（`data/call_trace_*.jsonl`）
   回放 `user_echo`（复读用户话）—— 需要用户 ASR 文本，只有 trace 里有。

跑法：
    H:/daniya_voice_tools/client_venv/Scripts/python.exe tools/replay_call_gate.py
    加参数 --only-trace 可只跑 trace 那一路。
"""
import ast
import collections
import importlib.util
import io
import json
import os
import sys
import time

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(WS, 'src', 'companion', 'call_gate.py')
_spec = importlib.util.spec_from_file_location("call_gate_rp", SPEC)
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)


def _extract_method(src_path, cls_name, fn_name):
    src = io.open(src_path, encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == fn_name:
                    mod = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(mod)
                    ns = {'re': __import__('re'), 'time': time}
                    exec(compile(mod, src_path, 'exec'), ns)  # noqa: S102
                    fn = ns[fn_name]
                    return fn.__func__ if isinstance(fn, staticmethod) else fn
    raise AssertionError('未找到 %s.%s' % (cls_name, fn_name))


_PROACTIVE = os.path.join(WS, 'src', 'companion', 'proactive.py')
_dup_sim = _extract_method(_PROACTIVE, 'ProactiveStrategy', '_dup_sim')


class FakeST:
    """桩对象：提供 call_gate + self_echo 判据需要的全部成员。

    `_dup_sim` 绑成**实例属性**：`getattr(st, '_dup_sim')` 拿到裸函数，
    `sim(text, prev)` 两参正好（绑成类属性会变 bound method 多一个 self）。
    """

    def __init__(self, user_text='', user_ts=0.0):
        self._duplex_last_user_text = user_text
        self._duplex_last_user_speech_ts = user_ts
        self._recent_messages = []
        self._duplex_ai_lines = []
        self._dup_sim = _dup_sim

    def _is_format_corrupted(self, t):
        return False

    def _is_garbled_speech(self, t):
        return False

    def find_echo(self, text):
        """返回命中的历史句（用于人工判定"第一次就被拦"＝误伤，还是"第 N 遍"＝正确）。"""
        try:
            for prev in (list(self._recent_messages) + list(self._duplex_ai_lines)):
                if prev and self._dup_sim(text, prev):
                    return prev
        except Exception:
            pass
        return None

    def remember(self, text, blocked):
        """真实两个出口的行为（proactive.py:2558 vs 2574-2575）。"""
        self._recent_messages.append(text)
        if len(self._recent_messages) > 5:
            self._recent_messages.pop(0)
        if not blocked:
            self._duplex_ai_lines.append(text)
            if len(self._duplex_ai_lines) > 12:
                self._duplex_ai_lines.pop(0)


# ============================================================ ① 录像回放
def replay_recordings():
    ROOT = r'H:\Comni\_internal\resources\apps\server\data\sessions'
    if not os.path.isdir(ROOT):
        print('跳过录像回放：目录不存在', ROOT)
        return

    tot = collections.Counter()
    blocked_texts = collections.Counter()
    blocked_by = collections.defaultdict(collections.Counter)
    per_session_before_after = []
    sessions = sorted(os.listdir(ROOT), key=lambda x: os.path.getmtime(os.path.join(ROOT, x)))

    for d in sessions:
        p = os.path.join(ROOT, d, 'recording.json')
        if not os.path.exists(p):
            continue
        try:
            rec = json.loads(io.open(p, encoding='utf-8', errors='replace').read())
        except Exception:
            continue
        ch = rec.get('chunks') or []
        if len(ch) < 5:
            continue
        # 【关键】按"连续 SPEAK 段"聚合文本，模拟客户端的 frag 拼接。
        # 录像的 result.text 是**逐块增量碎片**（"你的名" / "字叫什么？"），
        # 直接逐块送闸门会被 too_short 大量误判（实测假象 6567 次），
        # 而客户端是攒够一个完整句才送闸门（settle + min_chars）。
        units = []
        buf = []
        for c in ch:
            r = c.get('result')
            m = r.get('mode') if isinstance(r, dict) else None
            txt = (r.get('text') or '') if isinstance(r, dict) else ''
            if m == 'SPEAK':
                if txt:
                    buf.append(txt)
            else:
                if buf:
                    units.append(''.join(buf).strip())
                    buf = []
        if buf:
            units.append(''.join(buf).strip())
        units = [u for u in units if u]

        st = FakeST()
        n_speak = 0
        n_block = 0
        for txt in units:
            n_speak += 1
            tot['emit'] += 1
            ok, why, new = cg.call_gate_check(st, txt, True)
            if not ok:
                n_block += 1
                tot['block'] += 1
                tot[why] += 1
                blocked_texts[txt[:24]] += 1
                blocked_by[why][txt[:24]] += 1
            st.remember(txt, blocked=not ok)
        if n_speak >= 8:
            per_session_before_after.append((d, n_speak, n_block))

    print('=' * 78)
    print('① 引擎录像回放（%d 通 / 聚合后发言单元 %d 个）' % (len(sessions), tot['emit']))
    print('  拦截   %d  (%.2f%%)' % (tot['block'], 100.0 * tot['block'] / max(1, tot['emit'])))
    for k in ('self_echo', 'user_echo', 'too_short', 'format_corrupted', 'garbled'):
        if tot[k]:
            print('    - %-16s %d' % (k, tot[k]))
    print('  放行   %d' % (tot['emit'] - tot['block']))
    for why in ('self_echo', 'too_short'):
        if not blocked_by[why]:
            continue
        print()
        print('  --- %s 拦截 TOP12（self_echo 出现真回答即误伤）---' % why)
        for t, n in blocked_by[why].most_common(12):
            print('    %4d×  %s' % (n, t))
    print()
    print('  --- 单通拦截力度 TOP8（拦截数/产出数）---')
    per_session_before_after.sort(key=lambda x: -x[2])
    for d, ns, nb in per_session_before_after[:8]:
        print('    %-42s %3d 产出 → 拦 %2d (%.0f%%)' % (d, ns, nb, 100.0 * nb / ns))
    return tot


# ============================================================ ② trace 回放
def replay_traces():
    D = os.path.join(WS, 'data')
    fs = sorted([f for f in os.listdir(D) if f.startswith('call_trace_')],
                key=lambda f: os.path.getmtime(os.path.join(D, f)))
    BASE = time.time()
    tot = collections.Counter()
    detail = []
    for f in fs:
        p = os.path.join(D, f)
        try:
            ev = [json.loads(l) for l in io.open(p, encoding='utf-8', errors='replace') if l.strip()]
        except Exception:
            continue
        if not ev:
            continue
        last_user, last_ts = '', 0.0
        # 【关键】每通电话一个桩对象，**跨 emit 保持状态** —— 否则 `_dup_prefix_repeat`
        # 读到的历史永远为空，self_echo 永不触发（初版就踩了这个：跑出 self_echo=0）。
        st = FakeST()
        for r in ev:
            if r.get('ev') == 'asr' and r.get('clean'):
                last_user, last_ts = r['clean'], r['ts']
                st._duplex_last_user_text = last_user
            elif r.get('ev') == 'emit' and r.get('text'):
                now = r['ts']
                # 让判据内部的 time.time()-ts == now-last_ts（模拟"用户刚说完"）
                st._duplex_last_user_speech_ts = (BASE + last_ts - now) if last_ts else 0.0
                ok, why, new = cg.call_gate_check(st, r['text'], True)
                tot['emit'] += 1
                if not ok:
                    tot['block'] += 1
                    tot[why] += 1
                elif new is not None:
                    tot['trim'] += 1
                if not ok or new is not None:
                    hit = st.find_echo(r['text']) if why == 'self_echo' else None
                    detail.append((f[11:26], now, ok, why, new, r['text'], last_user,
                                   now - last_ts if last_ts else -1, hit))
                st.remember(r['text'], blocked=not ok)

    print()
    print('=' * 78)
    print('② 客户端 trace 回放（%d 个文件 / %d 次 emit）' % (len(fs), tot['emit']))
    print('  拦截             %d  (%.2f%%)' % (tot['block'], 100.0 * tot['block'] / max(1, tot['emit'])))
    print('  裁剪             %d  (%.2f%%)' % (tot['trim'], 100.0 * tot['trim'] / max(1, tot['emit'])))
    for k in ('self_echo', 'user_echo', 'too_short', 'format_corrupted', 'garbled'):
        if tot[k]:
            print('    - %-16s %d' % (k, tot[k]))
    print()
    print('  --- 每条被拦/被裁明细（人工复核）---')
    if not detail:
        print('    （无）')
    for tag, now, ok, why, new, txt, user, dt, hit in detail:
        print('  [%s] t=%6.2f %-11s AI=%r' % (tag, now, why or 'trim', txt[:46]))
        if user:
            print('              用户刚说=%r (Δ%.1fs)' % (user[:36], dt))
        if hit:
            print('              命中历史=%r' % hit[:46])
        if new is not None:
            print('              裁剪后=%r' % new[:46])


if __name__ == '__main__':
    if '--only-trace' not in sys.argv:
        replay_recordings()
    replay_traces()
