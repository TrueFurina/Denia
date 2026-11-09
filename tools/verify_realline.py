# -*- coding: utf-8 -*-
"""真机三项待办的自动判据（按需诊断工具）。

不进闸门：人工诊断工具——必须喂「修复后真机复跑」的日志才有意义。源码仓存量日志
全是修复前的（最新 run 日志仍有 3625 次「主循环疑似卡死」），放进 GATES 只会造出
一道要么永远假绿、要么永远假红的门（2026-11-09）。

为什么需要它
------------
三项待办都卡在真机/GUI，人工照清单做完容易「看着没问题」就过了。这个脚本把能从
日志自动判的部分变成三态判据（PASS / FAIL / UNKNOWN），并强制列出脚本判不了、
必须人眼/人耳确认的「未验项」——把清单抹掉，脚本自己会判红。

判据设计（踩过的坑）
--------------------
① **反假绿**：「0 条告警」不等于「修好了」。空日志、程序没真跑、只跑了 10 秒，
   都是 0 条告警。所以每条判据都先要「启动横幅 + 业务活动 + 时间跨度」三件证据齐，
   缺一件就返回 UNKNOWN，绝不返回 PASS。
② **哨兵法**：--selftest 往合成日志里注入假告警/假判死，证明判据真能抓到，
   而不是「文件存在就判绿」。
③ **loop_freeze 文件存在 ≠ 卡死**：main.py:1659 在 watchdog 启动处无条件 open() 一份
   loop_freeze_<ts>.log，即使从未卡死也会有空文件。判红必须看内容里的
   `[loop_freeze] lag=` 行，不能看文件是否存在。
④ **形态 caveat**：源码包复跑 ≠ 安装版；日志能判代码路径，判不了产品形态。

用法
----
    .venv/Scripts/python.exe tools/verify_realline.py --selftest
    .venv/Scripts/python.exe tools/verify_realline.py ^
        --run-log logs/run_YYYYMMDD_HHMMSS.log ^
        --ui-log logs/launch_runN.log ^
        --voice-log logs/voice_playback.log --min-minutes 30

退出码：0=全部真验证通过 / 1=有 FAIL / 77=有 UNKNOWN（未验证，**不算通过**）。
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")

# 日志行首形如 "21:06:44 [INFO] __main__: ..."
TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s")
BIZ = re.compile(r"\[(?:INFO|WARNING|DEBUG)\]\s+(?:src\.|channels:|companion:)")

BANNER = "达妮娅 AI 桌面伴侣"
FREEZE_ALERT = "主循环疑似卡死"
FREEZE_DUMP = "[loop_freeze] lag="

# 与 tools/probe_a5_env.py::CLASSIFY 同口径（dead/recover 是隐藏窗口长跑不许出现的）
DEAD_KEYS = ("判定 UI 卡死", "前端无响应", "尝试恢复", "重载排队",
             "Reload 无效", "尝试重载窗口恢复", "自动 Reload")
SLOW_KEYS = ("不重载", "判为后台定时器节流", "心跳间隔")
RECOVERED_KEY = "心跳已恢复"

# 脚本判不了、必须在真机上人眼/人耳确认的项。抹掉清单 → 自检判红。
UNVERIFIED = [
    "关 App 后 ffplay 是否继续出声（需人耳；日志只能证明播放路径走了 Popen 且正常完成）",
    "窗口是否真的【隐藏】了 ≥30min（脚本只能量日志时间跨度，看不到窗口可见性）",
    "复跑用的是安装版还是源码包（日志能判代码路径，判不了产品形态）",
    "真机 GPU/声卡/网络与开发机不同，screen_capture 与 TTS 的实际表现仍需人工观察",
]

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


def _read(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            return f.read().splitlines()
    except OSError:
        return None


def _span_minutes(lines):
    """日志时间跨度（分钟）：单调累积，容忍乱序行、正确处理跨午夜。

    不能用「首末相减」：① 合成样本/追加日志可能有乱序时间戳，首末相减会算短甚至
    提前判 UNKNOWN 走不到判红分支；② 跨午夜（23:18 → 03:08）首末相减是负数。
    逐段累加：大幅回退视为跨午夜 +24h，小幅回退视为乱序行跳过（不倒扣）。
    """
    secs = []
    for ln in lines:
        m = TS.match(ln)
        if m:
            h, mi, s = (int(x) for x in m.groups())
            secs.append(h * 3600 + mi * 60 + s)
    if len(secs) < 2:
        return None
    # 单调时钟：只累加「相对当前最大时刻」的正向增量。
    # 不能用逐段 b-a 累加——午夜后出现一条早间乱序行（0:05 之后又来 23:30）
    # 会产生 +23h 的假增量，把跨度虚增成一天。
    cur = secs[0]
    total = 0
    for s in secs[1:]:
        v = s
        if v < cur - 12 * 3600:      # 大幅回退 = 跨午夜，映射到次日
            v += 86400
        if v > cur:
            total += v - cur
            cur = v
        # v <= cur：乱序/重复行，忽略
    return total / 60.0


def _biz_count(lines):
    return sum(1 for ln in lines
               if BIZ.search(ln) and FREEZE_ALERT not in ln and "[UI心跳]" not in ln)


def _has_banner(lines):
    return any(BANNER in ln for ln in lines)


def _preflight(lines, path, min_minutes, min_biz=20):
    """三件证据齐才允许判 PASS，否则 UNKNOWN（反假绿）。"""
    if lines is None:
        return "日志不存在或不可读：%s" % path
    if not lines:
        return "日志为空：%s" % path
    if not _has_banner(lines):
        return "缺启动横幅，无法确认这是【本次复跑】的日志（可能拿到了旧日志）"
    span = _span_minutes(lines)
    if span is None:
        return "日志时间戳不足 2 条，无法确认长跑时长"
    if span < min_minutes:
        return "日志时长 %.1fmin < %dmin（0 条告警可能只是没跑够）" % (span, min_minutes)
    biz = _biz_count(lines)
    if biz < min_biz:
        return "业务日志仅 %d 条 < %d，程序可能没真跑起来（0 条告警会假绿）" % (biz, min_biz)
    return None


def check_loop_freeze(run_log, min_minutes):
    """待办③：course_recorder 自死锁修好后，看门狗不应再报「主循环疑似卡死」。"""
    lines = _read(run_log)
    why = _preflight(lines, run_log, min_minutes)
    if why:
        return (UNKNOWN, why)
    alerts = sum(1 for ln in lines if FREEZE_ALERT in ln)
    span, biz = _span_minutes(lines), _biz_count(lines)
    # 冻结快照：文件本身是启动时无条件创建的，只看内容里的 dump 行
    since = os.path.getmtime(run_log) - 60
    dumps = []
    for fp in glob.glob(os.path.join(LOGS, "loop_freeze_*.log")):
        if "TEST" in os.path.basename(fp) or os.path.getmtime(fp) < since:
            continue
        n = sum(1 for ln in (_read(fp) or []) if FREEZE_DUMP in ln)
        if n:
            dumps.append("%s(%d)" % (os.path.basename(fp), n))
    if alerts or dumps:
        return (FAIL, "仍有 %d 次卡死告警 / 冻结快照 %s" % (alerts, dumps or "无"))
    return (PASS, "覆盖 %.0fmin、%d 条业务日志、0 次卡死告警、无冻结 dump" % (span, biz))


def check_hidden_heartbeat(ui_log, min_minutes):
    """待办②：隐藏窗口 ≥30min，两阶段心跳只出 slow/grace，不判死、不重载。"""
    lines = _read(ui_log)
    why = _preflight(lines, ui_log, min_minutes, min_biz=0)
    if why:
        return (UNKNOWN, why)
    hb = [ln for ln in lines if "[UI心跳]" in ln]
    if not hb:
        return (UNKNOWN, "日志里没有 [UI心跳] 行，无法判定两阶段心跳是否生效")
    dead = [ln for ln in hb
            if any(k in ln for k in DEAD_KEYS) and RECOVERED_KEY not in ln]
    slow = [ln for ln in hb if any(k in ln for k in SLOW_KEYS)]
    span = _span_minutes(lines)
    if dead:
        return (FAIL, "出现 %d 条判死/重载心跳，首条：%s" % (len(dead), dead[0].strip()[:110]))
    return (PASS, "覆盖 %.0fmin、%d 条心跳、判死/重载 0 条、slow %d 条"
            % (span, len(hb), len(slow)))


def check_voice_playback(voice_log):
    """待办①的可自动判部分：试听确实走了新的 Popen 路径且正常播放完成。"""
    lines = _read(voice_log)
    if lines is None or not lines:
        return (UNKNOWN, "voice_playback.log 不存在或为空（本机尚未真机试听）")
    started = [ln for ln in lines if "ffplay 独立进程播放" in ln]
    finished = [ln for ln in lines if "ffplay 播放完成" in ln]
    if not started:
        return (UNKNOWN, "没有「ffplay 独立进程播放」记录，无法确认走了 Popen 路径")
    if not finished:
        return (FAIL, "有 %d 次播放启动但无「ffplay 播放完成」，可能卡住或被抢占异常" % len(started))
    return (PASS, "%d 次播放启动 / %d 次正常完成（关 App 是否继续响仍需人耳确认）"
            % (len(started), len(finished)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-log", help="本次真机复跑的 logs/run_YYYYMMDD_HHMMSS.log")
    ap.add_argument("--ui-log", help="隐藏窗口长跑的 logs/launch_runN.log")
    ap.add_argument("--voice-log", help="试听播放日志 logs/voice_playback.log")
    ap.add_argument("--min-minutes", type=float, default=30.0)
    ap.add_argument("--selftest", action="store_true", help="用合成日志验证判据本身有效")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    results = [
        ("① 试听播放生命周期（自动可判部分）", check_voice_playback(args.voice_log)),
        ("② 隐藏窗口 ≥30min 两阶段心跳", check_hidden_heartbeat(args.ui_log, args.min_minutes)),
        ("③ 主循环冻结根因已除", check_loop_freeze(args.run_log, args.min_minutes)),
    ]
    print("=== 真机三项待办 · 自动判据 ===")
    for name, (state, why) in results:
        print("  [%s] %s\n         %s" % (state, name, why))

    print("\n=== 未验项（脚本判不了，必须真机人工确认）===")
    assert UNVERIFIED, "未验项清单被抹掉：诚实边界必须判据化，不许空清单"
    for i, item in enumerate(UNVERIFIED, 1):
        print("  %d. %s" % (i, item))

    states = [s for _, (s, _) in results]
    print("\n汇总：%d PASS / %d FAIL / %d UNKNOWN"
          % (states.count(PASS), states.count(FAIL), states.count(UNKNOWN)))
    if FAIL in states:
        return 1
    if UNKNOWN in states:
        return 77          # 与 check_all 同口径：跳过不算通过
    return 0


def _selftest():
    """哨兵法：合成日志 + 注入缺陷，验证判据不是永真。"""
    import tempfile

    def fake_log(lines):
        fd, p = tempfile.mkstemp(prefix="qc_realline_", suffix=".log")
        os.close(fd)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        return p

    def ts(h, m=0, s=0):
        return "%02d:%02d:%02d " % (h, m, s)

    # 干净样本：31 分钟、启动横幅、25 条业务日志、0 告警
    good = [ts(10, 0) + "[INFO] __main__: " + BANNER + " 0.2.0 启动"]
    good += [ts(10, 0, i) + "[INFO] src.companion.proactive: 业务活动 %d" % i
             for i in range(1, 26)]
    good += [ts(10, 5) + "[INFO] src.ui.webapp: [UI心跳] 看门狗已就绪（前端开始上报存活）",
             ts(10, 8) + "[INFO] src.ui.webapp: [UI心跳] 心跳间隔 60s（>30s 但 <180s）："
             "判为后台定时器节流，不算 UI 卡死；不重载",
             ts(10, 31) + "[INFO] src.companion.proactive: 长跑末条业务活动"]
    p_good = fake_log(good)

    def inject(line):
        """插在末条业务行之前（贴近真实日志的时间顺序），不追加到末尾制造乱序。"""
        return fake_log(good[:-1] + [line] + good[-1:])

    # 注入缺陷：一条卡死告警 / 一条判死心跳
    p_alert = inject(ts(10, 20) + "[ERROR] __main__: 【主循环看门狗】"
                     "asyncio 主循环疑似卡死 37.7s，dump 调用栈定位卡死行：")
    p_dead = inject(ts(10, 20) + "[WARNING] src.ui.webapp: [UI心跳] ⚠️ "
                    "前端无响应（心跳中断 30s） — 判定为 UI 卡死，尝试重载窗口恢复")
    p_empty = fake_log([])
    # 跨午夜 + 乱序行：跨度必须仍 >= 30min（防止 _span_minutes 被改回首末相减）
    midnight_lines = [
        ts(23, 18) + "[INFO] __main__: " + BANNER + " 0.2.0 启动",
        ts(23, 20) + "[INFO] src.companion.proactive: 业务活动",
        ts(0, 5) + "[INFO] src.companion.proactive: 跨午夜业务活动",
        ts(23, 30) + "[INFO] src.companion.proactive: 乱序补写行",
        ts(0, 20) + "[INFO] src.companion.proactive: 末条业务活动",
    ]
    p_midnight = fake_log(midnight_lines)
    voice = fake_log(["[系统音频播放] ffplay 独立进程播放 x.wav (预估 8.0s)",
                      "[系统音频播放] ffplay 播放完成"])

    cases = [
        ("干净样本 A 主循环", check_loop_freeze(p_good, 30)[0] == PASS),
        ("干净样本 B 心跳", check_hidden_heartbeat(p_good, 30)[0] == PASS),
        ("干净样本 C 试听", check_voice_playback(voice)[0] == PASS),
        ("注入 1 条卡死告警 → A 判红", check_loop_freeze(p_alert, 30)[0] == FAIL),
        ("注入 1 条判死心跳 → B 判红", check_hidden_heartbeat(p_dead, 30)[0] == FAIL),
        ("空日志 → A 为 UNKNOWN 而非 PASS", check_loop_freeze(p_empty, 30)[0] == UNKNOWN),
        ("未喂 voice-log → C 为 UNKNOWN", check_voice_playback(None)[0] == UNKNOWN),
        ("跨午夜+乱序行：跨度算成 62min（既 >=30 也不虚增成 24h）",
         55.0 <= (_span_minutes(midnight_lines) or 0) <= 70.0),
        ("未验项清单非空", bool(UNVERIFIED)),
    ]
    print("=== --selftest：判据有效性（哨兵对照）===")
    bad = 0
    for name, ok in cases:
        print("  [%s] %s" % ("OK" if ok else "BROKEN", name))
        if not ok:
            bad += 1
    for p in (p_good, p_alert, p_dead, p_empty, voice):
        try:
            os.unlink(p)
        except OSError:
            pass
    print("\n结果：%d 项，%d 项不符" % (len(cases), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
