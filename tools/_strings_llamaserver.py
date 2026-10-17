# -*- coding: utf-8 -*-
"""从 llama-server.exe 及其同目录 DLL 中抽取关键字符串，判定 C++ 读 bundle 的哪些文件、
以及导出/环境变量名。"""
import os, glob, re

EXEDIR = r"H:\Comni\_internal\resources\build\bin\Release"
EXE = os.path.join(EXEDIR, "llama-server.exe")
KEYS = [b"default_ref_audio", b"prompt_mel", b"prompt_tokens", b"spk_f32", b"spk_cb",
        b"T2W_EXPORT", b"EXPORT_CACHE", b"minicpm_tmp", b"prompt_cache", b"token2wav",
        b"Token2Mel", b"exported prompt_cache"]

targets = [EXE] + [p for p in glob.glob(os.path.join(EXEDIR, "*.dll"))]
print("扫描文件:")
for t in targets:
    print("   %12d  %s" % (os.path.getsize(t), os.path.basename(t)))

for t in targets:
    data = open(t, "rb").read()
    print("=" * 78)
    print(os.path.basename(t), len(data))
    # ASCII 串
    for m in re.finditer(rb"[\x20-\x7e]{5,200}", data):
        s = m.group()
        if any(k in s for k in KEYS):
            try:
                txt = s.decode("ascii")
            except Exception:
                continue
            print("   ASC| " + txt)

# 宽字符（UTF-16LE）也扫一遍 —— Windows 路径常是宽字符
print("=" * 78)
print("UTF-16LE 命中:")
for t in targets:
    data = open(t, "rb").read()
    seen = set()
    for k in KEYS:
        wide = k.decode("ascii").encode("utf-16le")
        start = 0
        while True:
            i = data.find(wide, start)
            if i < 0:
                break
            a, b = max(0, i - 120), i + 120
            frag = data[a:b]
            try:
                txt = frag.decode("utf-16le", "ignore")
            except Exception:
                txt = ""
            txt = "".join(c for c in txt if 32 <= ord(c) < 127 or c in "\t")
            key = (os.path.basename(t), txt[:220])
            if key not in seen:
                seen.add(key)
                print("   U16| %s | %s" % (os.path.basename(t), txt[:220]))
            start = i + 2
