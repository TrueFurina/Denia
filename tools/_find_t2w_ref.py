# -*- coding: utf-8 -*-
"""找 token2wav_service.py / 任何 T2W 参考实现，确认 bundle 加载与 export 的判定条件。"""
import os, glob

roots = [r"H:\Comni", r"H:\minicpm models", r"H:\daniya_voice_tools"]
hits = []
for r in roots:
    if not os.path.isdir(r):
        continue
    for dp, dns, fns in os.walk(r):
        low = dp.lower()
        if any(s in low for s in ["site-packages", "node_modules", r"\tts", "\\vision"]):
            dns[:] = []
            continue
        for fn in fns:
            lf = fn.lower()
            if ("token2wav" in lf or "t2w" in lf or "prompt_bundle" in lf
                    or "prompt_cache" in lf) and (lf.endswith(".py") or lf.endswith(".json")
                                                  or lf.endswith(".md") or lf.endswith(".txt")):
                hits.append(os.path.join(dp, fn))
    if len(hits) > 60:
        break

for p in sorted(set(hits)):
    print("%10d  %s" % (os.path.getsize(p), p))
print("---- 共 %d ----" % len(set(hits)))
