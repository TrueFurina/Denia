# -*- coding: utf-8 -*-
"""离线高频补偿试听：用达妮娅真源做参考，对真实通话产出跑 hf_restore 的三档 EQ。

目的：**不改代码、不重启、不碰双工**，先让人耳判断补偿到底有没有用。
有效再考虑接到双工输出链路。

用法（须 client_venv：soundfile/numpy）：
    H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe tools/_make_eq_demo.py
"""
import os
import sys
import glob
import shutil

import numpy as np
import soundfile as sf

ROOT = r"G:\ai对话\11"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hf_restore as HF  # noqa: E402

REF = os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")
OUTDIR = os.path.join(ROOT, "data", "duplex_out")
PKG = os.path.join(ROOT, "data", "高频补偿试听_20260923")

# 挑两句比较长、发音清楚的
PICK = ["001_那我可就不客气了，你可真", "005_我在这儿呢，有什么事吗？"]


def main():
    os.makedirs(PKG, exist_ok=True)
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print("参考（达妮娅真源）：%s  sr=%d  %.1fs" % (os.path.basename(REF), rsr, len(ref) / rsr))
    rb, rf99 = HF.bandprof(ref)
    print("真源 3.4-6k/6-8k/8-12k = %s   f99=%.0f" % (np.round(rb, 1), rf99))
    print("=" * 100)

    subs = sorted(os.listdir(OUTDIR))
    latest = os.path.join(OUTDIR, subs[-1])
    print("产出会话：%s\n" % subs[-1])

    made = []
    for pick in PICK:
        cands = glob.glob(os.path.join(latest, pick + "*.wav"))
        if not cands:
            print("  跳过（找不到）%s" % pick)
            continue
        p = cands[0]
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != 24000:
            print("  跳过 sr=%d：%s" % (sr, p))
            continue
        base = os.path.basename(p).replace(".wav", "")
        b0, f0 = HF.bandprof(x)
        flat0 = HF.flatness(x)
        print("%s  (%.1fs)" % (base, len(x) / sr))
        print("   原始     3.4-6k/6-8k/8-12k = %s   f99=%.0f  8-12k平坦度=%.4f"
              % (np.round(b0, 1), f0, flat0))

        variants = []
        g1, _ = HF.build_gain(ref, x, scale=1.0)
        y1 = HF.apply_gain(x, g1)
        b1, f1 = HF.bandprof(y1)
        variants.append(("EQ1_全量", y1, b1, f1, HF.flatness(y1)))

        g2, _ = HF.build_gain(ref, x, scale=0.6)
        y2 = HF.apply_gain(x, g2, sbr=True, sbr_db=-6.0)
        b2, f2 = HF.bandprof(y2)
        variants.append(("EQ2_60%+SBR", y2, b2, f2, HF.flatness(y2)))

        g3, _ = HF.build_gain(ref, x, scale=0.7)
        y3 = HF.apply_gain(x, g3)
        b3, f3 = HF.bandprof(y3)
        variants.append(("EQ3_温和70%", y3, b3, f3, HF.flatness(y3)))

        for tag, y, b, f, fl in variants:
            print("   %-12s %s   f99=%.0f  平坦度=%.4f"
                  % (tag, np.round(b, 1), f, fl))
            out = os.path.join(PKG, "%s__%s.wav" % (tag, base[:14]))
            sf.write(out, y, 24000, subtype="PCM_16")
            made.append(out)
        # 原始也放一份，方便同目录对比
        orig = os.path.join(PKG, "00_原始__%s.wav" % base[:14])
        shutil.copy2(p, orig)
        made.insert(0, orig)
        made.append(orig)
        print()

    note = os.path.join(PKG, "怎么听.txt")
    with open(note, "w", encoding="utf-8") as f:
        f.write(
            "高频补偿 A/B 试听（2026-09-23）\n"
            "=====================================\n"
            "背景：产出带宽只有真源的 19%（f95 1227 Hz vs 6453 Hz），\n"
            "      8-12k 比真源低约 15dB ⇒ 听感发闷、不像本人。\n"
            "本目录是**离线**补偿结果（未改代码、未重启、未动双工），供耳朵裁决。\n\n"
            "文件命名：\n"
            "  00_原始__*      = 通话原产出（补偿前）\n"
            "  EQ1_全量__*     = 静态谱匹配 100%（补偿最猛，可能偏亮/有嘶声）\n"
            "  EQ2_60%+SBR__*  = 静态 60% + SBR 谐波折叠（高频跟着辅音包络走，更自然）\n"
            "  EQ3_温和70%__*  = 静态 70%（折中）\n\n"
            "怎么听：\n"
            "  同一句，先听 00_原始，再依次听 EQ3 → EQ2 → EQ1。\n"
            "  重点听：亮度/气声/齿音是否回来了、是不是更像达妮娅、\n"
            "          同时注意有没有出现明显的\"沙沙\"嘶声（补偿过头的副作用）。\n\n"
            "判读：\n"
            "  若某一档明显更像她且没有刺耳嘶声 ⇒ 接线到双工输出链路（保留双工）。\n"
            "  若都只是\"变亮了但还是不像\" ⇒ 说明带宽不是唯一因素，\n"
            "      需要认真考虑牺牲真双工、改走离线克隆。\n"
            "  注意：EQ 只能放大**已有**的高频残量，不能凭空造出 8-12k 的真实细节。\n"
        )
    made.append(note)
    print("输出目录：%s" % PKG)
    for m in made:
        print("   %s" % os.path.basename(m))


if __name__ == "__main__":
    main()
