"""诊断：通话语速（字/秒）与音色变化。

思路：埋点 speak_out 带 chars，落盘 wav 带时长。按时间窗配对：
  - 取某通通话时间窗内的所有落盘 wav → 总时长
  - 取同窗口内所有 speak_out 的 chars → 总字数
  - 字/秒 = 总字数 / 总时长
再与历史通话对比，判断"语速慢"是 TTS 侧变化还是文本侧变化。
"""
import io, os, glob, json, wave, time, datetime, collections

BASE = r'G:\ai对话\11'
AUDIO = os.path.join(BASE, 'src', 'ui', 'web', 'assets', 'chat_audio')
DATA = os.path.join(BASE, 'data')


def hms(t):
    return time.strftime('%H:%M:%S', time.localtime(t))


def parse_wall(s):
    """埋点里的 't' 字段是 HH:MM:SS.mmm，转成当天的秒数。"""
    try:
        hh, mm, rest = s.split(':')
        return int(hh) * 3600 + int(mm) * 60 + float(rest)
    except Exception:
        return None


def load_trace(path):
    ev = []
    for line in io.open(path, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            ev.append(json.loads(line))
        except Exception:
            pass
    return ev


def window_of(ev):
    ts = [e['ts'] for e in ev if 'ts' in e]
    return min(ts), max(ts)


def seg_stats(trace_path, label):
    ev = load_trace(trace_path)
    # 用第一条/最后一条的绝对 ts 定位时间窗（time.time 时间戳）
    t0, t1 = window_of(ev)
    # 落盘 wav 的 mtime 与 trace 的 ts 同属系统时钟
    wavs = []
    for f in glob.glob(os.path.join(AUDIO, '*.wav')):
        mt = os.path.getmtime(f)
        if t0 - 5 <= mt <= t1 + 20:
            wavs.append((mt, f))
    wavs.sort()
    total_dur = 0.0
    n_ok = 0
    for mt, f in wavs:
        try:
            w = wave.open(f, 'rb')
            n = w.getnframes()
            sr = w.getframerate()
            w.close()
            total_dur += n / float(sr)
            n_ok += 1
        except Exception:
            pass
    # 文本字数：只统计真正送进 TTS 的
    chars = 0
    n_emit = 0
    for e in ev:
        if e.get('ev') == 'tts_begin':
            chars += int(e.get('chars') or 0)
            n_emit += 1
    return {
        'label': label,
        'wav_n': n_ok,
        'dur': total_dur,
        'chars': chars,
        'n_emit': n_emit,
        'cps': (chars / total_dur) if total_dur else 0.0,
        't0': t0, 't1': t1,
    }


def main():
    out = []
    out.append('=' * 72)
    out.append('语速诊断（字/秒）：落盘音频时长 vs 埋点文本字数')
    out.append('=' * 72)
    traces = [
        ('call_trace_omni_20260924_005833.jsonl', '本次 P0 后 (00:58)'),
        ('call_trace_omni_20260923_225519.jsonl', 'P0 前基线 (22:55)'),
        ('call_trace_omni_20260923_222955.jsonl', '更早 (22:30)'),
    ]
    res = []
    for fn, label in traces:
        p = os.path.join(DATA, fn)
        if not os.path.isfile(p):
            out.append('  [缺失] %s' % fn)
            continue
        r = seg_stats(p, label)
        res.append(r)
        out.append('')
        out.append('--- %s  [%s]' % (label, fn))
        out.append('    时间窗 %s ~ %s' % (hms(r['t0']), hms(r['t1'])))
        out.append('    落盘音频 %d 段，总时长 %.1fs' % (r['wav_n'], r['dur']))
        out.append('    送 TTS 文本 %d 次，共 %d 字' % (r['n_emit'], r['chars']))
        out.append('    >>> 语速 %.2f 字/秒' % r['cps'])
    out.append('')
    out.append('=' * 72)
    out.append('对比：')
    for r in res:
        out.append('  %-22s %.2f 字/秒   (%d 字 / %.1fs)'
                   % (r['label'], r['cps'], r['chars'], r['dur']))
    s = chr(10).join(out)
    io.open(os.path.join(DATA, '_speed_diag.txt'), 'w', encoding='utf-8').write(s)
    print(s)


if __name__ == '__main__':
    main()
