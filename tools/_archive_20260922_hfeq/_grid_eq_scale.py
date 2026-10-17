# -*- coding: utf-8 -*-
"""为 daniya 扫一遍曲线强度，找出「每个样本都变好」的档位。"""
import os, sys, io, contextlib
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ); sys.path.insert(0, HERE)

import numpy as np
import voice_eq_calib as C
import hf_restore as HR

REF = os.path.join(PROJ, "assets", "voices", "daniya_ref.wav")
ref = C.read_mono24(REF)
rl, rfa = C._ltas_db(ref)
ms = C.match_trace(["data/call_trace_*.jsonl"])
outs = [(p, C.read_mono24(p)) for p, _ in ms]
print("样本 %d 个：%s" % (len(outs), [os.path.basename(p) for p, _ in outs]))
print()
print("%-8s %-8s %-8s | %s" % ("scale", "cap_hi", "联合d0", "各样本 LTAS 距真源 前→后"))
print("-" * 100)
best = None
for scale in (0.3, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0):
    for cap in (8.0, 10.0, 12.0, 16.0):
        out = np.concatenate([x for _, x in outs])
        g, f = HR.build_gain(ref, out, cap_hi=cap, cap_lo=-6.0, scale=scale)
        d0 = C.ltas_dist(*C._ltas_db(out), rl, rfa)
        d1 = C.ltas_dist(*C._ltas_db(HR.apply_gain(out, g)), rl, rfa)
        per, worse = [], 0
        for p, x in outs:
            a = C.ltas_dist(*C._ltas_db(x), rl, rfa)
            b = C.ltas_dist(*C._ltas_db(HR.apply_gain(x, g)), rl, rfa)
            per.append((a, b))
            if b >= a:
                worse += 1
        tag = " ".join("%.1f→%.1f%s" % (a, b, "" if b < a else "✗") for a, b in per)
        flag = ""
        if worse == 0 and (best is None or d1 < best[0]):
            best = (d1, scale, cap)
            flag = "  ★全样本改善"
        print("%-8s %-8s %-8s | %s%s" % (scale, cap, "%.2f→%.2f" % (d0, d1), tag, flag))
print("-" * 100)
if best:
    print("最佳：LTAS 距真源 %.2f dB，scale=%s cap_hi=%s" % best)
else:
    print("⚠️ 没有任何档位做到「每个样本都改善」⇒ 建议累积更多/更长的原生产出后再标定")
