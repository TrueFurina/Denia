# -*- coding: utf-8 -*-
"""通话健康体检：一条命令判断"她说了但你听不到"这类问题是否复发。

用法：
    python tools/call_health.py            # 自动取最新一次运行的日志
    python tools/call_health.py <log 路径>

背景（2026-09-22 实战教训）：
  客户端有一道「通话生命周期闸门」（`proactive._flush_duplex_emit`），目的是
  杜绝她"静默自言自语"。但它和内建的「哑火看门狗」会互相咬死：

      用户说话 → 她生成回答 → 被闸门丢弃（用户听不到）
              → 4s 后看门狗哑火 → 播兜底句"嗯？我在听呢" → **刷新「她上次开口时刻」**
              → 她真正的回答反而满足不了「用户发声晚于她上次开口」→ 又被丢 → 死循环

  指纹：**能听见兜底句，但任何真实回答都听不到**。
  日志指纹：`通话静音自言自语拦截(距用户发声 X / 距本方开口 Y)` 密集出现，
            且**每句都是 Y < X**（"她上次开口"比"用户发声"更晚）。

  修复方式：兜底发声打 `sys_fallback` 标记 → 放行但**不刷新**她上次开口时刻。
  修复后日志里应出现 `通话系统兜底发声(不占回应用户配额)`。
"""
import os
import re
import sys
import glob


def latest_log() -> str:
    fs = []
    for p in glob.glob(os.path.join('logs', 'run_*.log')):
        fs.append((os.path.getmtime(p), p))
    if not fs:
        return ''
    fs.sort()
    return fs[-1][1]


def analyse(path: str) -> int:
    if not path or not os.path.isfile(path):
        print('❌ 找不到日志：%r' % path)
        return 2
    t = open(path, encoding='utf-8', errors='replace').read()
    print('日志：%s（%.1f KB）' % (path, len(t) / 1024))
    print('=' * 72)

    # ---- 1) 闸门丢弃：解析「距用户发声 X / 距本方开口 Y」----
    drops = []
    for m in re.finditer(
            r'通话静音自言自语拦截\(距用户发声 ([\d.]+)s / 距本方开口 ([\d.]+)s\)：丢弃 (.{0,34})', t):
        x, y, txt = float(m.group(1)), float(m.group(2)), m.group(3)
        drops.append((x, y, txt))
    # 荒谬值（初值 0 导致）单列：此时丢弃是正确的
    absurd = [d for d in drops if d[0] > 1e6]
    drops = [d for d in drops if d[0] <= 1e6]
    susp = [d for d in drops if d[1] < d[0]]      # ← 死锁指纹

    # ---- 2) 兜底 / 哑火 / 原生 / TTS ----
    n_fb = len(re.findall(r'通话系统兜底发声', t))          # 修复后（不占配额）
    n_dead = len(re.findall(r'通话哑火: 用户说完', t))
    n_dead_play = len(re.findall(r'通话哑火兜底: 播', t))
    n_native = re.findall(r'双工原生语音随句出声: ([\d.]+)s', t)
    n_tts = len(re.findall(r'走 TTS 兜底', t))
    n_cont = len(re.findall(r'合并出声', t))

    print('【出声统计】')
    print('  她开口（合并出声）        %d 次' % n_cont)
    print('  原生双工语音送出          %d 次，共 %.1fs' %
          (len(n_native), sum(float(x) for x in n_native)))
    print('  原生被丢→改走 TTS         %d 次' % n_tts)
    print('  哑火告警 / 兜底播报       %d / %d 次' % (n_dead, n_dead_play))
    print('  系统兜底发声（修复后路径）%d 次' % n_fb)
    print()

    print('【闸门丢弃】')
    print('  丢弃总数                  %d 次' % (len(drops) + len(absurd)))
    print('  ├ 其中 Y<X（死锁指纹）    %d 次  %s' %
          (len(susp), '⚠️' if susp else '✅'))
    print('  └ 用户未开口前丢弃        %d 次（正确行为，她在自言自语）' % len(absurd))
    if drops[:6]:
        print('  明细（距用户发声 X / 距本方开口 Y）：')
        for x, y, txt in drops[:6]:
            flag = '  ⚠️死锁指纹' if y < x else ''
            print('     X=%6.1fs  Y=%6.1fs  %s%s' % (x, y, txt, flag))
    print()

    # ---- 3) 判定 ----
    print('【判定】')
    bad = False
    if susp and len(susp) >= 3 and n_fb == 0:
        print('  ❌ 疑似「闸门自造死锁」复发：')
        print('     %d 次丢弃中 %d 次是 Y<X（她上次开口比用户发声更晚）' % (len(drops), len(susp)))
        print('     且日志里**没有** `通话系统兜底发声` ⇒ 修复版可能没生效（需重启 companion）')
        bad = True
    elif susp:
        print('  🟡 仍有 %d 次 Y<X 丢弃，但兜底发声路径已在（修复生效中）' % len(susp))
        print('     若体感仍差，看是否集中在通话开头（开场白/稳定期闸门问题）')
    else:
        print('  ✅ 未见死锁指纹')
    if n_tts >= 3:
        print('  🟡 有 %d 次「原生被丢→走 TTS 兜底」⇒ 那几句的音色是外挂 TTS 的（音色会与原生不一致）' % n_tts)
    if n_native:
        tot = sum(float(x) for x in n_native)
        print('  ℹ️ 原生语音共 %.1fs —— 语音标定（EQ 曲线）需要 ≥6s 且通过质检，参考 tools/voice_eq_calib.py' % tot)
    print()
    print('=' * 72)
    print('提示：`high-Y<X` 一律先去查 `proactive.py _flush_duplex_emit` 的闸门，'
          '不要去调 listen_prob_scale / barge 阈值。')
    return 0 if not bad else 1


if __name__ == '__main__':
    log = sys.argv[1] if len(sys.argv) > 1 else latest_log()
    sys.exit(analyse(log))
