# -*- coding: utf-8 -*-
"""把最近若干通电话里的 ASR 原文全翻出来，看识别质量到底烂在哪。"""
import glob
import json
import os
import re
from collections import Counter

ROOT = r"G:\ai对话\11"

files = sorted(glob.glob(os.path.join(ROOT, "data", "call_trace_*.jsonl")),
               key=os.path.getmtime, reverse=True)[:8]

# 噪声判据：极短 / 纯标点 / 单字重复 / 明显无意义
NOISE_PAT = re.compile(r"^[，。、！？,.!?\s~\-…]+$")
SINGLE = re.compile(r"^(.)\1{1,}$")

total = 0
per_file = []
for f in files:
    rows = []
    for line in open(f, "r", encoding="utf-8", errors="ignore").read().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("ev") != "asr":
            continue
        raw = str(ev.get("raw", ""))
        clean = str(ev.get("clean", ""))
        rows.append((ev.get("t", ""), raw, clean))
    per_file.append((os.path.basename(f), rows))
    total += len(rows)

print("=" * 70)
print(f"最近 {len(files)} 通电话，ASR 落定共 {total} 句")
print("=" * 70)

all_rows = []
for name, rows in per_file:
    if not rows:
        continue
    print(f"\n--- {name}  ({len(rows)} 句)")
    for t, raw, clean in rows:
        flag = ""
        c = clean.strip()
        if not c or NOISE_PAT.match(c):
            flag = " [噪声:空/纯标点]"
        elif len(c) <= 1:
            flag = " [噪声:单字]"
        elif SINGLE.match(c):
            flag = " [噪声:重复字]"
        if raw != clean:
            print(f"  {t}  原={raw!r}")
            print(f"         清={clean!r}{flag}")
        else:
            print(f"  {t}  {clean!r}{flag}")
    all_rows += rows

# 汇总
print("\n" + "=" * 70)
print("汇总")
print("=" * 70)
n = len(all_rows)
if n:
    noise = sum(1 for _, _, c in all_rows
                if not c.strip() or NOISE_PAT.match(c.strip())
                or len(c.strip()) <= 1 or SINGLE.match(c.strip()))
    changed = sum(1 for _, r, c in all_rows if r != c)
    lens = [len(c.strip()) for _, _, c in all_rows]
    print(f"总句数         : {n}")
    print(f"疑似噪声句     : {noise}  ({noise/n*100:.1f}%)")
    print(f"被清理改写过   : {changed}  ({changed/n*100:.1f}%)")
    print(f"平均字数       : {sum(lens)/len(lens):.1f}")
    print(f"最短/最长      : {min(lens)} / {max(lens)}")

# 单字/双字高频（典型误识碎片）
short = Counter(c.strip() for _, _, c in all_rows if 0 < len(c.strip()) <= 3)
if short:
    print("\n≤3 字的高频碎片（典型误识）:")
    for k, v in short.most_common(15):
        print(f"    {k!r} x{v}")
