# -*- coding: utf-8 -*-
"""抓「哑巴」真凶：统计 speak_drop 的 reason、speak_frag、turn_end 动作、emit 覆盖率。

用法：
    python tools/_diag_drop_reasons.py                # 最新 trace
    python tools/_diag_drop_reasons.py <trace.jsonl>
"""
import os
import sys
import glob
import json
from collections import Counter

ROOT = r"G:\ai对话\11"
SEC_PER_CHAR = 0.20


def latest_trace():
    fs = sorted(glob.glob(os.path.join(ROOT, "data", "call_trace_*.jsonl")),
                key=os.path.getmtime)
    return fs[-1] if fs else ""


def main(path):
    if not path or not os.path.isfile(path):
        print("找不到 trace")
        return 2
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass

    print("文件：%s（%d 条）" % (os.path.basename(path), len(rows)))
    print("=" * 92)

    # ① 谁被丢了
    drops = [r for r in rows if r.get("ev") == "speak_drop"]
    print("\n① speak_drop 共 %d 次，按原因分组：" % len(drops))
    c = Counter((r.get("reason") or "<无>") for r in drops)
    for reason, n in c.most_common():
        print("   %-22s %3d 次" % (reason, n))
    print("\n   被丢的原文样本：")
    for reason, _ in c.most_common():
        ex = [r for r in drops if (r.get("reason") or "<无>") == reason][:3]
        for r in ex:
            print("     [%-20s] %r" % (reason, (r.get("text") or "")[:38]))

    # ② 攒句缓冲（非成句被扣住）
    frag = [r for r in rows if r.get("ev") == "speak_frag"]
    print("\n② speak_frag（没句末标点/未到长，扣进攒句缓冲暂不出声）：%d 次" % len(frag))
    for r in frag[-6:]:
        print("     timeout=%-5s buf=%r" % (r.get("timeout"), (r.get("buf") or r.get("text") or "")[:36]))

    # ③ 回合结束动作
    te = [r for r in rows if r.get("ev") == "turn_end"]
    print("\n③ turn_end 共 %d 次，按动作分组：" % len(te))
    for act, n in Counter((r.get("action") or "<无>") for r in te).most_common():
        print("   %-22s %3d 次" % (act, n))
    for r in [x for x in te if x.get("action") == "drop_stale_buffer"][:4]:
        print("     [丢弃陈腐] held=%ss %r" % (r.get("held"), (r.get("text") or "")[:32]))

    # ④ emit 覆盖率
    em = [r for r in rows if r.get("ev") == "emit"]
    print("\n④ emit 共 %d 条（应有≈字数×0.20s）：" % len(em))
    print("   %-4s %-7s %-7s %-6s %s" % ("字数", "应有", "实际", "覆盖", "文字"))
    print("   " + "-" * 88)
    bad = 0
    for r in em:
        n = r.get("chars") or 0
        ms = r.get("pcm_ms") or 0
        need = n * SEC_PER_CHAR
        act = ms / 1000.0
        cov = act / need if need > 0 else 0
        flag = ""
        if cov < 0.70:
            flag = "  ✗"
            bad += 1
        print("   %-4d %-7.2f %-7.2f %-6.0f%%%s %r"
              % (n, need, act, cov * 100, flag, (r.get("text") or "")[:30]))
    print("   " + "-" * 88)
    print("   覆盖率 <70%% 的：%d / %d" % (bad, len(em)))

    # ⑤ TTS 侧
    for ev in ("tts_recv", "tts_pick", "tts_play", "tts_drop", "tts_interrupt"):
        xs = [r for r in rows if r.get("ev") == ev]
        if xs:
            print("\n   %-14s %d 次" % (ev, len(xs)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else latest_trace()))
