# -*- coding: utf-8 -*-
"""把新参考音落到两处 daniya_ref.wav（项目 + Comni），带备份。"""
import os, shutil, time, hashlib

TS = time.strftime("%Y%m%d_%H%M%S")
NEW16 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_16k.wav"
NEW48 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"
TARGETS = [
    r"G:\ai对话\11\assets\voices\daniya_ref.wav",
    r"H:\Comni\_internal\resources\apps\assets\ref_audio\daniya_ref.wav",
]
SIDECAR = r"G:\ai对话\11\assets\voices\daniya_ref_48k.wav"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


print("新参考音 16k: %d B  md5=%s" % (os.path.getsize(NEW16), md5(NEW16)[:16]))
for t in TARGETS:
    if os.path.exists(t):
        bak = t + ".bak_prev_" + TS
        shutil.copy2(t, bak)
        print("备份 %s -> %s" % (t, os.path.basename(bak)))
    shutil.copy2(NEW16, t)
    print("安装 %s  (%d B, md5=%s)" % (t, os.path.getsize(t), md5(t)[:16]))

# 48k 版放一份 sidecar，便于以后取用
shutil.copy2(NEW48, SIDECAR)
print("sidecar -> %s (%d B)" % (SIDECAR, os.path.getsize(SIDECAR)))
