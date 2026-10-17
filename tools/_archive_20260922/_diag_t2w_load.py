# -*- coding: utf-8 -*-
"""诊断：Token2Wav 实际加载了 prompt_cache 还是回退到了 default bundle。

关键字符串（来自 omni.dll 符号表）：
    "Token2Wav: using prompt_cache from %s"
    "Token2Wav: prompt_cache failed, fallback to prompt_bundle from %s"
这两行的出现与否，直接决定声码器音色来源。
"""
import os
import glob
import time

KEY = [
    "Token2Wav",
    "using prompt_cache",
    "fallback to prompt_bundle",
    "prompt_bundle",
    "set_ref_audio",
    "voice_audio",
    "spk_cb",
    "token2wav-gguf",
    "initialization failed",
    "not found",
    "T2W_EXPORT",
]

MAXL = 60


def scan_log(p, maxlines=MAXL):
    try:
        t = open(p, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return None, "读失败 %r" % e
    hits = []
    for l in t.splitlines():
        ls = l.strip()
        if not ls:
            continue
        low = ls.lower()
        if any(k.lower() in low for k in KEY):
            hits.append((ls[:200], l[:1] in (" ", "\t")))
    return hits, None


def main():
    logs = sorted(glob.glob(r"H:\daniya_voice_tools\*.log"))
    print("扫描 %d 个日志 …" % len(logs))
    # 先专找最重要的两行
    print()
    print("=" * 100)
    print("【A】是否出现过 prompt_cache 加载 / bundle 回退")
    print("=" * 100)
    found_any = False
    for p in logs:
        hits, err = scan_log(p)
        if err or not hits:
            continue
        key = [h for h, _ in hits
               if "using prompt_cache" in h or "fallback to prompt_bundle" in h
               or "Token2Wav" in h or "set_ref_audio" in h]
        if key:
            found_any = True
            print("--- %s" % os.path.basename(p))
            for h in key[:20]:
                print("    %s" % h)
    if not found_any:
        print("  (无任何 Token2Wav 加载日志命中)")

    print()
    print("=" * 100)
    print("【B】_gguf_cmp.log 全文（历史对比产物，含张量清单）")
    print("=" * 100)
    p = os.path.join(r"H:\daniya_voice_tools", "_gguf_cmp.log")
    if os.path.exists(p):
        for l in open(p, encoding="utf-8", errors="replace").read().splitlines():
            print("  " + l[:200])
    else:
        print("  (不存在)")

    print()
    print("=" * 100)
    print("【C】logs 目录下与引擎相关的文件")
    print("=" * 100)
    for d in [r"G:\ai对话\11\logs", r"G:\ai对话\11\logs\engine"]:
        if not os.path.isdir(d):
            print("  [无目录] %s" % d)
            continue
        print("  [%s]" % d)
        items = []
        for n in os.listdir(d):
            q = os.path.join(d, n)
            if os.path.isfile(q):
                items.append((os.path.getmtime(q), n, os.path.getsize(q)))
        for t, n, s in sorted(items)[-20:]:
            print("    %s %10.1fKB  %s" % (time.strftime("%m-%d %H:%M", time.localtime(t)), s / 1024, n))


if __name__ == "__main__":
    main()
