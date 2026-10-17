# -*- coding: utf-8 -*-
"""核对两个候选 bundle 目录：exe 旁 vs resources 下。判断 C++ 到底读哪个。"""
import os, glob, time, hashlib

CANDS = [
    r"H:\Comni\_internal\resources\build\bin\Release\tools\omni\assets\default_ref_audio",
    r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio",
    r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio.bak_20260913",
]


def md5(p, n=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(n)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:12]


for d in CANDS:
    print("=" * 78)
    print(d, os.path.isdir(d))
    if not os.path.isdir(d):
        continue
    for f in sorted(glob.glob(d + r"\*")):
        print("   %10d  %s  md5=%s  %s" % (
            os.path.getsize(f),
            time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(f))),
            md5(f)[:10],
            os.path.basename(f)))

print("=" * 78)
print("exe 旁的 tools/omni/assets 顶层:")
top = r"H:\Comni\_internal\resources\build\bin\Release\tools\omni\assets"
for f in sorted(glob.glob(top + r"\*")):
    tag = "<DIR>" if os.path.isdir(f) else str(os.path.getsize(f))
    print("   %12s  %s  %s" % (tag,
          time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(f))),
          os.path.basename(f)))

print("=" * 78)
print("server 安装根目录下是否存在 tools/omni:")
for p in [r"H:\Comni\_internal\resources\tools\omni",
          r"H:\Comni\_internal\resources\tools\omni\assets"]:
    print("  ", p, os.path.isdir(p))
