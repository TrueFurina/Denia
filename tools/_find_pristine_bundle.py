# -*- coding: utf-8 -*-
"""全盘找 default_ref_audio / prompt_bundle 的其它副本，确定原生 bundle 的 wav 规格。"""
import os, glob, time, wave

ROOTS = [r"H:\Comni", r"H:\minicpm models", r"H:\daniya_voice_tools", r"H:\ceshi"]
hits_spk = []
hits_ref = []
for r in ROOTS:
    if not os.path.isdir(r):
        continue
    for dp, dns, fns in os.walk(r):
        # 跳过巨大的模型/包目录，加速
        if any(s in dp.lower() for s in ["site-packages", r"\tts", "\\vision\\"]):
            dns[:] = []
            continue
        for fn in fns:
            if fn == "spk_f32.bin":
                hits_spk.append(os.path.join(dp, fn))
            if fn.lower() == "default_ref_audio.wav":
                hits_ref.append(os.path.join(dp, fn))
        if len(hits_spk) > 40:
            break

print("### spk_f32.bin 副本:")
for p in sorted(set(hits_spk)):
    print("   %10d  %s" % (os.path.getsize(p), p))

print("### default_ref_audio.wav 副本:")
for p in sorted(set(hits_ref)):
    info = ""
    try:
        with wave.open(p, "rb") as w:
            info = "sr=%d ch=%d frames=%d dur=%.2fs" % (w.getframerate(), w.getnchannels(),
                                                        w.getnframes(), w.getnframes()/w.getframerate())
    except Exception as e:
        info = "wave-open ERR %s" % e
    print("   %10d  %s   [%s]" % (os.path.getsize(p), p, info))
