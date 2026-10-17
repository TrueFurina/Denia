# -*- coding: utf-8 -*-
"""执行重烤：备份 → 安装新 prompt_bundle → 跑 bake_cache.py → 校验。
全程只改名/复制，不删除任何东西。
"""
import os, sys, shutil, subprocess, time, hashlib, struct

PY_PROJ = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
PY_VENV = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
TOOLS = r"H:\daniya_voice_tools"
REF48 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"
REF16 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_16k.wav"

BUNDLE_DIR = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio"
CACHE = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf\prompt_cache.gguf"
PROJ_REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
COMNI_REF = r"H:\Comni\_internal\resources\apps\assets\ref_audio\daniya_ref.wav"

TS = time.strftime("%Y%m%d_%H%M%S")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def log(m):
    print(m, flush=True)


def size(p):
    return os.path.getsize(p) if os.path.exists(p) else -1


log("=" * 100)
log("0) 烘焙前快照")
log("=" * 100)
log("  live prompt_cache.gguf : %d B  md5=%s" % (size(CACHE), md5(CACHE)[:16]))
log("  default_ref_audio 目录:")
for f in sorted(os.listdir(BUNDLE_DIR)):
    log("     %10d B  %s" % (size(os.path.join(BUNDLE_DIR, f)), f))
log("  项目 daniya_ref.wav    : %d B  md5=%s" % (size(PROJ_REF), md5(PROJ_REF)[:16]))
log("  Comni daniya_ref.wav   : %d B  md5=%s" % (size(COMNI_REF), md5(COMNI_REF)[:16]))
log("  新参考音 48k           : %d B" % size(REF48))

# 1) 备份 default_ref_audio
bak_bundle = BUNDLE_DIR + ".bak_prev_" + TS
if not os.path.exists(bak_bundle):
    shutil.copytree(BUNDLE_DIR, bak_bundle)
    log("\n1) 已备份 default_ref_audio -> %s" % bak_bundle)

# 2) 安装新 wav
shutil.copy2(REF48, os.path.join(BUNDLE_DIR, "default_ref_audio.wav"))
log("2) 已安装新 default_ref_audio.wav  <- %s" % os.path.basename(REF48))

# 3) 提取新 bundle
log("\n3) 提取 prompt_bundle (spk/tokens/mel) ...")
r = subprocess.run([PY_VENV, os.path.join(TOOLS, "extract_prompt_bundle.py"),
                    "--ref-audio", REF48, "--model-dir", TOOLS,
                    "--output-dir", BUNDLE_DIR],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
log("   rc=%s" % r.returncode)
log("   " + (r.stdout or "").strip().replace("\n", " | "))
if r.returncode != 0:
    log("   stderr: " + (r.stderr or "").strip()[-600:])
    sys.exit(1)

# 4) 校验 bundle
import numpy as np
old_spk = np.fromfile(os.path.join(TOOLS, "tmp_probe_old_ref", "spk_f32.bin"), dtype=np.float32)
new_spk = np.fromfile(os.path.join(BUNDLE_DIR, "spk_f32.bin"), dtype=np.float32)
c = float(new_spk @ old_spk / (np.linalg.norm(new_spk) * np.linalg.norm(old_spk) + 1e-12))
mel = np.fromfile(os.path.join(BUNDLE_DIR, "prompt_mel_btc_f32.bin"), dtype=np.float32)
tok = np.fromfile(os.path.join(BUNDLE_DIR, "prompt_tokens_i32.bin"), dtype=np.int32)
log("4) 校验: spk cos(新,旧参考) = %.3f   tokens=%d   mel=%d floats(%d 帧)" %
    (c, len(tok), len(mel), len(mel) // 80))
if c < 0.55:
    log("   !! 相似度过低，疑似不是同一个人 —— 中止烘焙")
    sys.exit(2)

# 5) 备份 live cache
bak_cache = CACHE + ".bak_prev_" + TS
if not os.path.exists(bak_cache):
    shutil.copy2(CACHE, bak_cache)
    log("\n5) 已备份 live cache -> %s (%d B)" % (bak_cache, size(bak_cache)))

# 6) 跑烘焙
log("\n6) 运行 bake_cache.py（启动 llama-server 重生 cache）...")
t0 = time.time()
r = subprocess.run([PY_PROJ, os.path.join(TOOLS, "bake_cache.py")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   cwd=TOOLS, timeout=1800)
log("   rc=%s  用时 %.1fs" % (r.returncode, time.time() - t0))
tail = (r.stdout or "").strip().split("\n")
for l in tail[-25:]:
    log("   " + l)
if r.returncode != 0:
    log("   stderr: " + (r.stderr or "").strip()[-800:])

# 7) 校验 cache
log("\n7) 烘焙后校验")
after = md5(CACHE) if os.path.exists(CACHE) else "MISSING"
log("  live prompt_cache.gguf : %d B  md5=%s" % (size(CACHE), after[:16]))
log("  是否变化: %s" % (after != md5(bak_cache)))

# 顺便把 llama 日志留档
lg = os.path.join(TOOLS, "bake_llama.log")
if os.path.exists(lg):
    dst = os.path.join(TOOLS, "bake_llama_%s.log" % TS)
    shutil.copy2(lg, dst)
    log("  llama 日志留档: %s" % dst)
log("=" * 100)
