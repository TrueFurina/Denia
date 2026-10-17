# -*- coding: utf-8 -*-
"""一键切换达妮娅音色 A/B（cache + LLM 参考音 + bundle 三者同时对齐）。

用法（项目解释器或任意 python）：
    python switch_daniya_voice.py A     # 自然音高（287Hz，宽频）
    python switch_daniya_voice.py B     # 对齐历史音高（241Hz，保共振峰降调）
    python switch_daniya_voice.py old   # 回到 08-28 旧版（16k 素材，糊）
    python switch_daniya_voice.py       # 只看当前状态

切换后需**重启 llama-server** 才生效：最省事＝切一次桌宠/通话档，或重启软件。
"""
import os, sys, shutil, time, hashlib

TS = time.strftime("%Y%m%d_%H%M%S")
TOOLS = r"H:\daniya_voice_tools"
CACHE = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf\prompt_cache.gguf"
BUNDLE = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio"
BUNDLE_EXE = r"H:\Comni\_internal\resources\build\bin\Release\tools\omni\assets\default_ref_audio"
REFS = [r"G:\ai对话\11\assets\voices\daniya_ref.wav",
        r"H:\Comni\_internal\resources\apps\assets\ref_audio\daniya_ref.wav"]
REFDIR = r"G:\ai对话\11\data\native_audio_test\ref_build"

PROFILES = {
    "A": {
        "cache": os.path.join(TOOLS, "daniya_cache_A_natural_287.gguf"),
        "ref": os.path.join(REFDIR, "FINAL2_daniya_ref_16k.wav"),
        "bundle": BUNDLE + ".keep_A_natural",
        "desc": "自然音高 ~287Hz（48k 原生宽频）",
    },
    "B": {
        "cache": os.path.join(TOOLS, "daniya_cache_B_pitchmatch_241.gguf"),
        "ref": os.path.join(REFDIR, "FINAL3_daniya_ref_16k.wav"),
        "bundle": BUNDLE + ".keep_B_pitchmatch",
        "desc": "对齐历史音高 ~241Hz（保共振峰降调，宽频）",
    },
    "old": {
        "cache": CACHE + ".bak_v2_20260921_092505",
        "ref": os.path.join(TOOLS, "daniya_ref_ORIGINAL_16k.wav"),
        "bundle": BUNDLE + ".bak_prev_20260921_090130",
        "desc": "08-28 旧版（16k 素材，音色偏闷）",
    },
}


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:12]


def cur():
    print("当前:")
    print("  cache : %s  md5=%s  %d B" % (CACHE, md5(CACHE) if os.path.exists(CACHE) else "MISSING",
                                          os.path.getsize(CACHE) if os.path.exists(CACHE) else -1))
    for r in REFS:
        print("  ref   : %s  md5=%s  %d B" % (r, md5(r) if os.path.exists(r) else "MISSING",
                                              os.path.getsize(r) if os.path.exists(r) else -1))


def apply(prof):
    p = PROFILES[prof]
    print("切到 %s : %s" % (prof.upper(), p["desc"]))
    if not os.path.exists(p["cache"]):
        print("  !! cache 文件缺失: %s" % p["cache"]); return
    shutil.copy2(CACHE, CACHE + ".swapbak_" + TS)
    shutil.copy2(p["cache"], CACHE)
    print("  cache <- %s (md5=%s)" % (os.path.basename(p["cache"]), md5(CACHE)))
    if p["ref"] and os.path.exists(p["ref"]):
        for r in REFS:
            if os.path.exists(r):
                shutil.copy2(r, r + ".swapbak_" + TS)
            shutil.copy2(p["ref"], r)
            print("  ref   <- %s -> %s (md5=%s)" % (os.path.basename(p["ref"]), r, md5(r)))
    if os.path.isdir(p["bundle"]):
        for d in (BUNDLE, BUNDLE_EXE):
            if not os.path.isdir(d):
                continue
            for f in os.listdir(p["bundle"]):
                if f.endswith((".bin", ".wav")):
                    s, t = os.path.join(p["bundle"], f), os.path.join(d, f)
                    if os.path.normcase(os.path.abspath(s)) != os.path.normcase(os.path.abspath(t)):
                        shutil.copy2(s, t)
        print("  bundle<- %s" % os.path.basename(p["bundle"]))
    print("\n⚠️ 需**重启 llama-server** 才生效：切一次桌宠/通话档，或重启软件。")


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if arg in ("A", "a"):
        apply("A")
    elif arg in ("B", "b"):
        apply("B")
    elif arg in ("old", "OLD", "0"):
        apply("old")
    if arg:
        cur()
    else:
        cur()
        print("\n可用: A(自然287Hz) / B(对齐241Hz) / old(08-28旧版)")
