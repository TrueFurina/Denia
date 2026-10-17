# -*- coding: utf-8 -*-
"""通话追踪分析器：把 data/call_trace_*.jsonl 拆成"延迟四段"并给出归因。

用法：
    python tools/analyze_call_trace.py                # 分析最新一份
    python tools/analyze_call_trace.py <文件路径>      # 分析指定文件
    python tools/analyze_call_trace.py --list          # 列出全部追踪文件

输出分七块：
    ① 总览            时长 / 各事件条数 / GPU 采样数
    ② 采集层          mic 电平分档、峰值、增益、闸门与静音命中
    ③ 推流层          送给模型的每帧音频峰值、静音填充率、队列积压、force_listen
    ④ 裁决层          模型每帧 is_listen 占比、最长连续 LISTEN 时长（=她在等的时长）
    ⑤ 逐轮延迟拆解    ← 核心表：用户说完 → 她出声 的四段耗时
    ⑤-c 复读检测      她是否把用户刚说的话原样说回去 + 输出多样性
    ⑥ 门控损耗        每一道闸门拦掉了多少句（speak_drop 归因）
    ⑥-b TTS 层账目     speak_out 之后到出声之前：入队 / 取走 / 丢弃 / 打断清队列
    ⑦ GPU            利用率 / 显存 / 分进程占用

四段定义（都以秒计）：
    等待 = 模型首个出字时刻 − 用户最后有声时刻       ← 双工"判不准轮次"的代价
    生成 = 回合结束(turn_end) − 首个出字时刻          ← 模型吐完一句的耗时
    门控 = speak_out − emit                          ← 客户端发射到上层放行
    合成 = 首音频 − speak_out                          ← TTS 合成到入设备
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def newest(pattern: str = "call_trace_*.jsonl") -> str | None:
    files = sorted(glob.glob(os.path.join(DATA, pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def load(path: str):
    recs = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass
    recs.sort(key=lambda r: r.get("ts", 0.0))
    return recs


def hhmmss(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def pct(a: float, b: float) -> str:
    return "%.0f%%" % (100.0 * a / b) if b else "-"


def fnum(v, nd=3, dash="-"):
    return dash if v is None else ("%%.%df" % nd) % v


def sec_fmt(v):
    return "-" if v is None else ("%.2fs" % v)


def T(r, n=12):
    """安全取事件的人类可读时刻。

    【2026-09-18】原先一律写 r["t"]，只要有一条事件缺 t 字段（老版本埋点、
    手写/合成的追踪文件）就会 KeyError 把整篇分析打断——诊断工具崩掉等于
    什么信息都没拿到。这里退化为占位符。
    """
    v = r.get("t")
    return str(v)[:n] if v else "--:--:--"


# ================================================================
def main():
    args = [a for a in sys.argv[1:]]
    if "--list" in args or "-l" in args:
        for f in sorted(glob.glob(os.path.join(DATA, "call_trace_*.jsonl")), key=os.path.getmtime):
            print("  %s  %.1f KB  %s" % (os.path.basename(f),
                                         os.path.getsize(f) / 1024,
                                         datetime.datetime.fromtimestamp(
                                             os.path.getmtime(f)).strftime("%m-%d %H:%M:%S")))
        return
    path = args[0] if args else newest()
    if not path or not os.path.exists(path):
        print("没有找到 call_trace_*.jsonl。确认：① 通话前已重启 companion ② config.yaml 里 "
              "duplex_call_trace: true ③ 确实打过一通电话（追踪在切到麦克风采集时开启）")
        return

    R = load(path)
    if not R:
        print("文件为空：", path)
        return
    ev = Counter(r["ev"] for r in R)
    t0, t1 = R[0].get("ts", 0), R[-1].get("ts", 0)

    print("=" * 78)
    print("通话追踪分析  %s" % os.path.basename(path))
    print("  时间窗 %s → %s（%.1f 分钟，共 %d 条记录）"
          % (hhmmss(t0), hhmmss(t1), (t1 - t0) / 60.0, len(R)))
    print("  事件计数 " + "  ".join("%s=%d" % (k, v) for k, v in ev.most_common()))
    print("=" * 78)

    # ---------- ② 采集层 ----------
    print("\n【② 采集层 · 麦克风】")
    mic = [r for r in R if r["ev"] == "mic"]
    if mic:
        pks = [r.get("pk", 0.0) for r in mic]
        pks_sorted = sorted(pks)
        voice_n = sum(1 for r in mic if r.get("voice"))
        gate_n = sum(1 for r in mic if r.get("gate"))
        muted_n = sum(1 for r in mic if r.get("muted"))
        gains = sorted(set(r.get("gain") for r in mic))
        print("  采样 %d 块（约 2Hz）  峰值 max=%.4f  p90=%.4f  中位=%.4f"
              % (len(mic), max(pks), pks_sorted[int(len(pks) * 0.9)], pks_sorted[len(pks) // 2]))
        print("  分档：人声(>0.05) %d 条（%s） | 疑似说话(>0.01) %d | 有信号(>0.005) %d | 近零(<1e-4) %d"
              % (voice_n, pct(voice_n, len(mic)),
                 sum(1 for r in mic if r.get("vlo")),
                 sum(1 for p in pks if p > 0.005), sum(1 for p in pks if p < 1e-4)))
        print("  增益=%s  自播闸门清零 %d 条  通话静音 %d 条" % (gains, gate_n, muted_n))

        # 【2026-09-17】死流判定：只看"峰值是否为 0"是不够的 —— 设备开着但没灌数据时，
        # 驱动会给出恒定 1 个 int16 LSB(≈3.05e-05) 的抖动，既不是 0 也远够不上任何档位。
        # 死流的判定特征不是"低"，而是"**零方差**"：真安静的房间里底噪也在抖。
        # 老追踪文件没有 dm 字段，回退按峰值判（保证对历史文件也能给结论）
        dead_n = sum(1 for r in mic if (r["dm"] if "dm" in r else r.get("pk", 0.0) <= 1e-4))
        uniq_pk = len(set(round(p, 7) for p in pks))
        span = (max(pks) - min(pks)) if pks else 0.0
        # 判据：块峰值**逐块位相同**（唯一值 <=2）。用绝对极差 Δ 会误判——
        # 活麦的极安静房间 Δ≈8e-06，同样很小。
        if len(mic) >= 5 and max(pks) <= 1e-4 and uniq_pk <= 2:
            print("  ❌ 【麦克风是死的】%d/%d 块峰值 <= 1e-4 且**零方差**"
                  "（唯一值 %d 个，Δ=%.3e）" % (dead_n, len(mic), uniq_pk, span))
            print("     → 设备已打开但驱动一个字节都没灌进来。这不是增益/距离问题，"
                  "调 gain 无效。")
            print("     → 去查：耳机是否开机 / 是否被硬件静音键静音 / "
                  "系统默认输入是否被虚拟声卡(ToDesk、NVIDIA Broadcast、VB-Audio)顶替。")
        elif voice_n == 0:
            print("  ⚠️ 全程没有任何一块达到人声档(>0.05) → 输入太弱，"
                  "模型判不准{'用户说完没有'}的假设成立")
            if sum(1 for r in mic if r.get("vlo")):
                print("     但 >0.01 的块有 %d 条 → 麦是通的，只是电平低（可靠增益/系统音量救）"
                      % sum(1 for r in mic if r.get("vlo")))
            else:
                print("     ⚠️ 连 >0.01 都没有 → 更像设备没灌声"
                  "（蓝牙未进 HFP / 默认输入设备不对）"
                  "；若同时唯一值仅 %d 个（Δ=%.3e）→ 可直接判死流" % (uniq_pk, span))
        else:
            print("  ✅ 出现过人声档 %d 次 → 输入电平不是（唯一）瓶颈" % voice_n)
    else:
        print("  （无 mic 记录：追踪可能没在通话态开启）")

    # ---------- ③ 推流层 ----------
    print("\n【③ 推流层 · 送进模型的音频帧】")
    fr = [r for r in R if r["ev"] == "frame"]
    if fr:
        sil = sum(1 for r in fr if r.get("silence"))
        back = sum(1 for r in fr if (r.get("q") or 0) > 0)
        fl = sum(1 for r in fr if r.get("fl"))
        hold = sum(1 for r in fr if (r.get("hold") or 0) > 0)
        dts = [r["dt"] for r in fr if r.get("dt") is not None]
        fpks = [r.get("pk", 0.0) for r in fr]
        print("  帧数 %d   静音帧 %d(%s)   有队列积压 %d(%s)   force_listen %d(%s)"
              % (len(fr), sil, pct(sil, len(fr)), back, pct(back, len(fr)), fl, pct(fl, len(fr))))
        print("  帧内音频峰值 max=%.4f 中位=%.4f（0 = 模型什么都没听到）" %
              (max(fpks), sorted(fpks)[len(fpks) // 2]))
        if hold:
            print("  barge 抢话权保持中 %d 帧（模型被强制闭嘴）" % hold)
        if dts:
            ds = sorted(dts)
            print("  推流周期 dt 中位=%.3fs  p90=%.3fs  max=%.3fs（正常 1.000）"
                  % (ds[len(ds) // 2], ds[int(len(ds) * 0.9)], ds[-1]))
        if fl:
            print("  ⚠️ 存在被强制 LISTEN 的帧：模型没有开口资格，延迟与它无关")
        if back:
            print("  ⚠️ 存在队列积压帧：模型在消费旧音频，响应对不上此刻")
    else:
        print("  （无 frame 记录）")

    # ---------- ④ 裁决层 ----------
    print("\n【④ 裁决层 · 模型每帧 is_listen】")
    mo = [r for r in R if r["ev"] == "model"]
    if mo:
        lis = sum(1 for r in mo if r.get("listen"))
        print("  裁决 %d 次  LISTEN %d(%s)  SPEAK/出字 %d(%s)"
              % (len(mo), lis, pct(lis, len(mo)), len(mo) - lis, pct(len(mo) - lis, len(mo))))
        # 最长连续 LISTEN 段
        best, cur = 0.0, None
        spans = []
        prev = None
        for r in mo:
            if r.get("listen"):
                if cur is None:
                    cur = (r["ts"], r["ts"])
                else:
                    cur = (cur[0], r["ts"])
            else:
                if cur:
                    spans.append(cur[1] - cur[0])
                cur = None
        if cur:
            spans.append(cur[1] - cur[0])
        spans.sort(reverse=True)
        if spans:
            print("  最长连续 LISTEN = %.1fs（这段里模型全程没打算说话）" % spans[0])
            print("  连续 LISTEN 段 top5：" + "  ".join("%.1fs" % x for x in spans[:5]))
        nz = [r for r in mo if (r.get("tlen") or 0) > 0]
        print("  收到过文本的帧 %d 条" % len(nz))
    else:
        print("  （无 model 记录）")

    # ---------- ⑤ 逐轮延迟拆解 ----------
    print("\n【⑤ 逐轮延迟拆解 · 用户说完 → 她出声】")
    print("  说明：'用户有声'取宽松档(>0.01)——弱麦场景（全程到不了 0.05）只有它能当参考；")
    print("        '转写'是 Sherpa 出文本时刻（带 1.2s 端点），'等待'从转写算起。")
    voice_ts = None      # 用户最后"有声"（宽松档）
    asr_ts = None        # 用户最后"转写完成"
    first_text_ts = None
    turns = []
    for r in R:
        e = r["ev"]
        ts = r.get("ts", 0)
        if e == "mic" and (r.get("voice") or r.get("vlo")):
            voice_ts = ts
        elif e == "asr":
            asr_ts = ts
        elif e == "model":
            if (r.get("tlen") or 0) > 0 and first_text_ts is None:
                first_text_ts = ts
        elif e == "turn_end":
            cur = {"voice": voice_ts, "asr": asr_ts, "first_text": first_text_ts,
                   "end": ts, "action": r.get("action"),
                   "text": (r.get("text") or "")[:40]}
            first_text_ts = None
            if r.get("action") == "emit":
                turns.append(cur)
        elif e == "emit" and turns:
            turns[-1].setdefault("emit", ts)
        elif e == "speak_in" and turns:
            turns[-1].setdefault("speak_in", ts)
        elif e in ("speak_out", "speak_frag", "speak_drop") and turns:
            turns[-1].setdefault(e, ts)
            if e == "speak_drop":
                turns[-1].setdefault("drop_reason", r.get("reason"))
        elif e in ("tts_first_audio", "tts_play") and turns:
            turns[-1].setdefault("audio", ts)
        # 【2026-09-18】TTS 层：原来完全没记，导致 speak_out 之后没声只能猜
        elif e == "tts_recv" and turns:
            turns[-1].setdefault("tts_recv", ts)
        elif e == "tts_pick" and turns:
            turns[-1].setdefault("tts_pick", ts)
        elif e == "tts_drop" and turns:
            turns[-1].setdefault("tts_drop", ts)
            turns[-1].setdefault("tts_drop_reason", r.get("reason"))

    def ref_of(tu):
        """本轮"用户说完"的参考时刻：优先 Sherpa 转写（已验证有语），否则有声点。"""
        v, a = tu.get("voice"), tu.get("asr")
        if a and (v is None or a >= v):
            return a, "转写"
        return v, "有声"

    if not turns:
        print("  （没有成功发射的回合）")
    else:
        print("  %-9s %-9s %6s %6s %6s %6s %7s  %s"
              % ("用户有声", "转写(+端点)", "等待", "生成", "门控", "合成", "总计", "内容"))
        stat = defaultdict(list)
        for tu in turns:
            ref, _k = ref_of(tu)
            ft = tu.get("first_text")
            # 参考点太旧（>30s）视为无参考，避免把上一轮的锚点算进本轮
            if ft and ref and ft - ref > 30:
                ref = None
            wait = (ft - ref) if (ft and ref) else None
            endpoint = ((tu["asr"] - tu["voice"])
                        if (tu.get("asr") and tu.get("voice")
                            and 0 <= tu["asr"] - tu["voice"] < 30) else None)
            gen = (tu["end"] - ft) if ft else None
            gate = (tu["speak_out"] - tu["emit"]) if (tu.get("speak_out") and tu.get("emit")) else None
            synth = (tu["audio"] - tu["speak_out"]) if (tu.get("audio") and tu.get("speak_out")) else None
            total = (tu["audio"] - ref) if (tu.get("audio") and ref) else None
            for k, v in (("wait", wait), ("gen", gen), ("gate", gate),
                         ("synth", synth), ("total", total), ("endpoint", endpoint)):
                if v is not None and v >= 0:
                    stat[k].append(v)
            note = ""
            if tu.get("speak_frag") and not tu.get("speak_out"):
                note = " ⚠️ 被二层攒句扣住，未出声"
            elif tu.get("speak_drop") and not tu.get("speak_out"):
                note = " ⚠️ 被门控吞掉(%s)" % (tu.get("drop_reason") or "?")
            elif not tu.get("audio"):
                # 【2026-09-18】细分原因，别再说"未见埋点"这种没结论的话
                if tu.get("tts_drop"):
                    note = " ⚠️ TTS 层丢弃(%s)" % (tu.get("tts_drop_reason") or "?")
                elif tu.get("tts_recv") and not tu.get("tts_pick"):
                    note = " ⚠️ TTS 收到但队列未取走"
                elif tu.get("tts_pick"):
                    note = " ⚠️ 已进 TTS 但没出首音频（合成/播放异常）"
                else:
                    note = " ⚠️ 未见任何 TTS 层埋点（追踪为修复前版本？）"
            asr_col = hhmmss(tu["asr"])[3:] if tu.get("asr") else "-"
            if endpoint is not None:
                asr_col += "(+%.1f)" % endpoint
            print("  %-9s %-9s %6s %6s %6s %6s %7s  %s%s"
                  % (hhmmss(tu["voice"])[3:] if tu.get("voice") else "-",
                     asr_col, sec_fmt(wait), sec_fmt(gen), sec_fmt(gate),
                     sec_fmt(synth), sec_fmt(total), tu.get("text", ""), note))
        print("\n  ── 各段统计（%d 轮）──" % len(turns))
        for k, label in (("endpoint", "⓪ 端点(Sherpa判停)"), ("wait", "① 等待(判轮次)"),
                         ("gen", "② 生成(吐字)"), ("gate", "③ 门控(上层)"),
                         ("synth", "④ 合成(TTS)"), ("total", "总计(从用户有声起)")):
            v = sorted(stat[k])
            if v:
                print("    %-20s n=%2d  中位 %.2fs  均值 %.2fs  p90 %.2fs  max %.2fs"
                      % (label, len(v), v[len(v) // 2], sum(v) / len(v),
                         v[int(len(v) * 0.9)], v[-1]))

    # ---------- ⑤-b 抢话检测 ----------
    print("\n【⑤-b 抢话检测 · 她是否在用户把话说完之前就开口】")
    import bisect as _bs
    END_DELAY = 1.2          # Sherpa 端点：出文本时刻 ≈ 用户结句 + 1.2s
    hers = sorted([r for r in R if r["ev"] == "speak_out"], key=lambda r: r["ts"])
    if not hers:
        hers = sorted([r for r in R if r["ev"] == "emit"], key=lambda r: r["ts"])
    asrs = sorted([r for r in R if r["ev"] == "asr"], key=lambda r: r["ts"])
    if not hers:
        print("    无开口记录")
    else:
        print("    判定基准：Sherpa 出文本 ≈ 用户结句 + %.1fs 端点（min_trailing_silence）" % END_DELAY)
        grabs, normal, idle = [], [], []
        a_ts = [a["ts"] for a in asrs]
        for h in hers:
            # 用户"某一句话"的发音区间 ≈ [结句-句长, 结句)，结句 = 该次 asr - 端点。
            # 句长按字数估（中文口语 ~0.22s/字，下限 1s）——只看"她开口时刻是否落进这个区间"，
            # 避免把"她先说、用户几秒后才回她"误判成抢话。
            i = _bs.bisect_right(a_ts, h["ts"])          # 时间上紧随其后的那次转写
            grabbed = False
            if i < len(asrs):
                user_end = asrs[i]["ts"] - END_DELAY
                _u = (asrs[i].get("clean") or asrs[i].get("raw") or "")
                user_start = user_end - max(1.0, len(_u) * 0.22)
                # 端点时刻本身有 ±0.3s 估算误差，早 0.3s 以内当作"刚好接上"
                if user_start <= h["ts"] < user_end and (user_end - h["ts"]) >= 0.3:
                    grabs.append((h, user_end - h["ts"], _u))
                    grabbed = True
            if grabbed:
                continue
            # 前后 8s 都没有转写 → 无人说话时她自己开口
            near = any(abs(a["ts"] - h["ts"]) <= 8.0 for a in asrs)
            if not near:
                idle.append(h)
            else:
                normal.append(h)
        print("    共 %d 次开口" % len(hers))
        if grabs:
            print("    ⚠️ 抢话 %d 次（%.0f%%）——她开口时用户那句话还没说完："
                  % (len(grabs), 100.0 * len(grabs) / len(hers)))
            for h, ov, utxt in grabs:
                print("        %s 她「%s」  比用户结句早 %.1fs   ← 用户那句：%s"
                      % (T(h), (h.get("text") or "")[:22], ov, (utxt or "?")[:26]))
        else:
            print("    ✅ 无抢话")
        if idle:
            print("    🗣 空场开口 %d 次（前后 8s 用户没有任何语音）：" % len(idle))
            for h in idle:
                print("        %s 「%s」" % (T(h), (h.get("text") or "")[:26]))
        if normal:
            print("    ✅ 正常接话 %d 次（用户已说完或静默后她才开口）" % len(normal))
        if len(hers) >= 4 and len(grabs) + len(idle) >= len(hers) * 0.3 and (grabs or idle):
            print("    ⚠ 抢话/空场开口合计占比偏高 → 优先下调 duplex_call_listen_prob_scale"
                  "（该值越大越倾向 LISTEN；官方默认 1.0）")

    # ---------- ⑤-c 复读检测 ----------
    print("\n【⑤-c 复读检测 · 她是不是把用户的话原样说回去了】")
    import re as _re2

    _PRON = ("你们", "我们", "他们", "她们", "它们", "您", "你", "我", "他", "她", "它", "咱")

    def _norm_cn(s):
        """只留中英文与数字，并去掉人称代词。

        必须去人称：她复述用户的话时会把人称换掉——
        用户"你来自哪里" → 她"嗯嗯，我来自哪里？"，不去人称就检不出。
        """
        s = _re2.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")
        for _p in _PRON:
            s = s.replace(_p, "")
        return s

    if not hers:
        print("    无开口记录")
    else:
        echoes = []
        for h in hers:
            ht = h.get("text") or ""
            nh = _norm_cn(ht)
            if len(nh) < 4:
                continue
            # 时间上最近的前一条用户转写（10s 内才算"刚说的话"）
            j = _bs.bisect_right(a_ts, h["ts"]) - 1
            if j < 0:
                continue
            a = asrs[j]
            if h["ts"] - a["ts"] > 10.0:
                continue
            nu = _norm_cn(a.get("clean") or a.get("raw") or "")
            if len(nu) < 4:
                continue
            kind = None
            # 长度约束：两句话长度须在同一量级——否则"用户说晚上好晚上好 / 她回晚上好"
            # 这类正常短回应会被子串判据误判成复读
            _ratio_ok = (len(nh) >= len(nu) * 0.8) and (len(nh) <= len(nu) * 2.5)
            if (nu in nh or nh in nu) and _ratio_ok:
                kind = "原样重复"
            elif len(nu) >= 5:
                hit = len(set(nu) & set(nh)) / float(len(set(nu)))
                # 她的句子把用户那句话的字几乎全用上了，且长度没膨胀太多
                if hit >= 0.9 and len(nh) <= len(nu) * 2.5:
                    kind = "近乎逐字重复(%.0f%%)" % (hit * 100)
            if kind:
                echoes.append((h, a, kind))

        print("    共 %d 次开口，其中复读 %d 次" % (len(hers), len(echoes)))
        if echoes:
            print("    ❌ 复读明细：")
            for h, a, kind in echoes:
                print("        %s 她「%s」（%s）  ← 用户刚说：%s"
                      % (T(h), (h.get("text") or "")[:24], kind,
                         (a.get("clean") or a.get("raw") or "")[:24]))
        else:
            print("    ✅ 无复读")

        # —— 内容贫乏度：她的输出有多少是"自己的话" ——
        uniq = len({_norm_cn(h.get("text") or "") for h in hers if _norm_cn(h.get("text") or "")})
        avg_len = sum(len(_norm_cn(h.get("text") or "")) for h in hers) / float(len(hers))
        print("    输出多样性：去重 %d / 共 %d 句，平均 %.1f 字/句" % (uniq, len(hers), avg_len))

        if echoes:
            pct_e = 100.0 * len(echoes) / len(hers)
            print("    ⚠ 复读占比 %.0f%% → 检查通话档提示词是否含：①会被逐字念出的引号例句 "
                  "②\"他说什么你接什么\"这类复读指令（见 tests/test_call_prompt_no_echo.py）" % pct_e)
        if uniq <= 2 and len(hers) >= 4:
            print("    ⚠ 输出高度雷同（去重仅 %d 句）→ 通话档上下文被削得太狠，"
                  "模型无内容可讲只能套模板" % uniq)

    # ---------- ⑥ 门控损耗 ----------
    print("\n【⑥ 门控损耗 · 她说了但没出声的】")
    drops = [r for r in R if r["ev"] == "speak_drop"]
    frags = [r for r in R if r["ev"] == "speak_frag"]
    if drops or frags:
        c = Counter(r.get("reason", "?") for r in drops)
        for k, v in c.most_common():
            print("    speak_drop  %-22s %d 次" % (k, v))
            for r in [x for x in drops if x.get("reason") == k][:3]:
                print("        例 %s  %r" % (T(r), (r.get("text") or "")[:40]))
        if frags:
            print("    speak_frag(二层攒句扣住)     %d 次 ← 短回答没句末标点会被扣在这里" % len(frags))
            for r in frags[:5]:
                print("        %s  %r  buf=%r" % (T(r), (r.get("text") or "")[:24],
                                                (r.get("buf") or "")[:24]))
    else:
        print("    无（没有任何一句被门控拦下）")

    # ---------- ⑥-b TTS 层账目 ----------
    print("\n【⑥-b TTS 层账目 · speak_out 之后到出声之间】")
    _recv = [r for r in R if r["ev"] == "tts_recv"]
    _pick = [r for r in R if r["ev"] == "tts_pick"]
    _tdrop = [r for r in R if r["ev"] == "tts_drop"]
    _ibeg = [r for r in R if r["ev"] == "tts_interrupt"]
    _audio = [r for r in R if r["ev"] == "tts_first_audio"]
    if not (_recv or _pick or _tdrop or _ibeg):
        # 旧格式追踪（埋点前生成）：拿 barge 做启发式提示，别让结论空着
        _bg = [r for r in R if r["ev"] == "barge"]
        if _bg:
            print("  （本通追踪无 TTS 层埋点＝修复前版本；但检测到 %d 次 barge-in）" % len(_bg))
            print("    ⚠ 修复前的 TTS._interrupted 是闩锁：interrupt() 清空队列后，worker 的")
            print("      下一次 get() 会拿到「打断之后才入队的第一句回复」并把它当残留丢掉，")
            print("      于是每次插话白吞一句（只有气泡没声音）。请对照上表 speak_out 之后")
            print("      没有首音频的那些轮，看它们是否紧跟在插话之后。")
        else:
            print("  （本通没有 TTS 层埋点，也没有打断事件）")
    else:
        print("   speak_out %d 句 → TTS 收到 %d → 队列取走 %d → 出首音频 %d"
              % (len([r for r in R if r["ev"] == "speak_out"]),
                 len(_recv), len(_pick), len(_audio)))
        for r in _ibeg:
            print("   打断 %s  清掉待播 %s 条" % (T(r), r.get("drained")))
        if _tdrop:
            c = Counter(r.get("reason", "?") for r in _tdrop)
            for k, v in c.most_common():
                print("   tts_drop  %-20s %d 次" % (k, v))
                for r in [x for x in _tdrop if x.get("reason") == k][:3]:
                    print("       例 %s  kind=%s" % (T(r), r.get("kind")))
            if any(r.get("reason") == "interrupt_residue" for r in _tdrop):
                print("      ↑ 打断残留丢弃。少量正常；若「speak_out 之后整轮没有 tts_begin」")
                print("        成片出现，说明丢弃窗口吃得过宽（历史 bug 会连正常回复一起吞）。")
        else:
            print("   无 TTS 层丢弃")
        _stuck = len(_recv) - len(_pick) - len(_tdrop)
        if _stuck > 0:
            print("   ⚠️ %d 条已入队但既未取走也未丢弃（worker 卡住，或追踪提前中断）" % _stuck)

    # ---------- ⑦ GPU ----------
    print("\n【⑦ GPU】")
    gp = [r for r in R if r["ev"] == "gpu"]
    if gp:
        us = [r["util"] for r in gp if isinstance(r.get("util"), (int, float))]
        ms = [r["mem_used"] for r in gp if isinstance(r.get("mem_used"), (int, float))]
        pw = [r["power"] for r in gp if isinstance(r.get("power"), (int, float))]
        tp = [r["temp"] for r in gp if isinstance(r.get("temp"), (int, float))]
        if us:
            us2 = sorted(us)
            print("  利用率 中位 %s%%  均值 %.1f%%  max %s%%  min %s%%"
                  % (us2[len(us2) // 2], sum(us) / len(us), max(us), min(us)))
        if ms:
            print("  显存 中位 %s MiB  max %s MiB" % (sorted(ms)[len(ms) // 2], max(ms)))
        if pw:
            print("  功耗 中位 %s W  max %s W" % (sorted(pw)[len(pw) // 2], max(pw)))
        if tp:
            print("  温度 中位 %s ℃" % sorted(tp)[len(tp) // 2])
        procs = [r["procs"] for r in gp if r.get("procs")]
        if procs:
            print("  分进程显存（最后一次）：%s" % procs[-1])
    else:
        print("  （无 gpu 记录：nvidia-smi 不可用或通话太短）")

    # ---------- 结论提示 ----------
    print("\n【自动归因提示】")
    tips = []
    if stat.get("endpoint"):
        ep = sorted(stat["endpoint"])[len(stat["endpoint"]) // 2]
        tips.append("Sherpa 端点中位 %.2fs（判'用户说完'的固有代价，可调 min_trailing_silence）" % ep)
    if stat.get("wait") and stat.get("gen"):
        mw = sorted(stat["wait"])[len(stat["wait"]) // 2]
        mg = sorted(stat["gen"])[len(stat["gen"]) // 2]
        tips.append("等待中位 %.2fs vs 生成中位 %.2fs → 主因是%s"
                    % (mw, mg, "判轮次(她在等)" if mw > mg else "生成(她在算)"))
    if stat.get("synth"):
        m_s = sorted(stat["synth"])[len(stat["synth"]) // 2]
        tips.append("TTS 合成中位 %.2fs %s" % (m_s, "（健康）" if m_s < 1.5 else "（偏慢，需查 TTS）"))
    if stat.get("gate"):
        mg2 = sorted(stat["gate"])[len(stat["gate"]) // 2]
        tips.append("上层门控中位 %.2fs %s" % (mg2, "（健康）" if mg2 < 0.2 else "（偏慢，查 debounce/攒句）"))
    if grabs:
        tips.append("抢话 %d 次（她插在用户话中间）→ duplex_call_listen_prob_scale 宜偏大"
                    "（越大越倾向 listen，官方默认 1.0）" % len(grabs))
    if idle:
        tips.append("空场开口 %d 次（无人说话她自发声）→ 同上，或收紧通话前缀的「必须答」措辞" % len(idle))
    if mic and sum(1 for r in mic if r.get("voice")) == 0:
        tips.append("麦克风全程未进人声档 → 优先调 duplex_mic_gain / 系统输入音量")
    if fr and sum(1 for r in fr if (r.get("q") or 0) > 0) > len(fr) * 0.3:
        tips.append("推流队列长期积压 → 模型在消费旧音频，检查采集/重采样是否拖慢")
    if gp and us:
        u = sorted(us)[len(us) // 2]
        tips.append("GPU 中位利用率 %s%% %s" % (u, "→ 印证'在等不是在算'" if u < 60 else "(算力确实在忙)"))
    if frags:
        tips.append("二层攒句扣住 %d 次 → 短回答无句末标点不出声，改 _duplex_frag_buf 逻辑" % len(frags))
    for i, s in enumerate(tips, 1):
        print("  %d) %s" % (i, s))
    if not tips:
        print("  （数据不足，无法自动归因）")


if __name__ == "__main__":
    main()
