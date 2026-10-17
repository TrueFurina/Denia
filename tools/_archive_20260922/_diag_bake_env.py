# -*- coding: utf-8 -*-
"""诊断烘焙环境：模型 token2wav 目录 / 运行中的 llama-server / 监听端口。"""
import os, glob, subprocess, time

print("=" * 78)
d = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"
print("MODEL token2wav-gguf:", d, os.path.isdir(d))
if os.path.isdir(d):
    for f in sorted(glob.glob(d + r"\*")):
        print("   %12d  %s  %s" % (os.path.getsize(f),
              time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(f))),
              os.path.basename(f)))

print("=" * 78)
print("MODEL dir top-level:")
md = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
for f in sorted(glob.glob(md + r"\*")):
    tag = "<DIR>" if os.path.isdir(f) else str(os.path.getsize(f))
    print("   %12s  %s" % (tag, os.path.basename(f)))

print("=" * 78)
print("导出目录 exported_cache:")
ec = r"H:\daniya_voice_tools\exported_cache"
for f in sorted(glob.glob(ec + r"\*")):
    print("   %12d  %s  %s" % (os.path.getsize(f),
          time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(f))),
          os.path.basename(f)))

print("=" * 78)
print("进程 llama-server / python / gateway:")
try:
    out = subprocess.run(["tasklist"], capture_output=True, text=True,
                         encoding="gbk", errors="replace", timeout=30).stdout
    for ln in out.splitlines():
        low = ln.lower()
        if any(k in low for k in ["llama-server", "python", "node", "comni"]):
            print("   " + ln.strip())
except Exception as e:
    print("   tasklist ERR", e)

print("=" * 78)
print("监听端口 190xx / 22700 / 8006:")
try:
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="gbk", errors="replace", timeout=30).stdout
    for ln in out.splitlines():
        if "LISTENING" in ln and any(p in ln for p in [":190", ":2270", ":8006", ":9883"]):
            print("   " + ln.strip())
except Exception as e:
    print("   netstat ERR", e)

print("=" * 78)
print("GPU 显存:")
try:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                          "--format=csv,noheader"], capture_output=True, text=True,
                         timeout=30).stdout
    print("   " + out.strip())
except Exception as e:
    print("   nvidia-smi ERR", e)
