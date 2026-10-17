# -*- coding: utf-8 -*-
"""下载 sherpa-onnx 流式 Paraformer（中文，替代 2023 zipformer transducer）。"""
import os
import socket
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
socket.setdefaulttimeout(60)
BASE = ("https://hf-mirror.com/csukuangfj/"
        "sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/")
DST = r"G:\ai对话\11\models\sherpa-onnx-streaming-paraformer-bilingual-zh-en"
FILES = ["tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx"]

os.makedirs(DST, exist_ok=True)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

for fn in FILES:
    dst = os.path.join(DST, fn)
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        print(f"已存在，跳过: {fn} ({os.path.getsize(dst)/1048576:.1f}MB)")
        continue
    t0 = time.time()
    try:
        r = op.open(urllib.request.Request(BASE + fn, headers=UA))
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        with open(dst, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    print(f"\r  {fn}: {got/1048576:.1f}/{total/1048576:.1f}MB "
                          f"({got/total*100:.0f}%)", end="", flush=True)
        print(f"\r  完成 {fn}: {os.path.getsize(dst)/1048576:.1f}MB  "
              f"{time.time()-t0:.0f}s")
    except Exception as e:
        print(f"\n  失败 {fn}: {type(e).__name__} {e}")
        if os.path.exists(dst):
            os.remove(dst)
        raise

print("下载目录:", DST)
for fn in FILES:
    p = os.path.join(DST, fn)
    print(f"  {fn}: {os.path.getsize(p)/1048576:.1f}MB" if os.path.exists(p) else f"  {fn}: 缺失")
