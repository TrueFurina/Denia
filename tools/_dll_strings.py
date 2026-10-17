# -*- coding: utf-8 -*-
"""扫描 omni.dll / llama.dll 中的可读字符串，定位 prompt_cache / voice_audio 的处理逻辑线索。

原理：C++ 源码里的日志格式串、配置键名、函数名会原样留在二进制里。
搜到它们就能确认「cache 与 voice_audio 谁优先」以及「默认参考音从哪来」。
"""
import os
import re
import sys

BIN_DIR = r"H:\Comni\_internal\resources\build\bin\Release"

KEYS = [
    "prompt_cache", "prompt_tokens", "prompt_mel", "spk", "voice_audio",
    "default_ref_audio", "bundle", "export", "T2W_EXPORT",
    "ref_audio", "token2wav", "Token2Mel", "cosy", "speech_token",
    "init_from", "load_prompt", "voice_clone", "prompt_wav",
    "minicpm", "signature", "clone",
]

PRINT_RE = re.compile(rb"[\x20-\x7e]{6,}")


def strings_of(path, minlen=6):
    out = []
    with open(path, "rb") as f:
        data = f.read()
    for m in PRINT_RE.finditer(data):
        s = m.group().decode("ascii", "replace")
        if len(s) >= minlen:
            out.append(s)
    return out


def main():
    files = sys.argv[1:] or [
        os.path.join(BIN_DIR, "omni.dll"),
        os.path.join(BIN_DIR, "llama.dll"),
        os.path.join(BIN_DIR, "llama-server.exe"),
    ]
    pat = re.compile("|".join(re.escape(k) for k in KEYS), re.I)
    for p in files:
        if not os.path.exists(p):
            print("MISS %s" % p)
            continue
        ss = strings_of(p)
        hits = [s for s in ss if pat.search(s)]
        print("=" * 100)
        print("%s  (%.1f MB, 共 %d 条可读串, 命中 %d 条)"
              % (os.path.basename(p), os.path.getsize(p) / 1048576, len(ss), len(hits)))
        seen = set()
        for s in hits:
            if s in seen:
                continue
            seen.add(s)
            print("   %s" % s[:220])


if __name__ == "__main__":
    main()
