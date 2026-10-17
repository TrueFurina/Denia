# -*- coding: utf-8 -*-
"""烤 B 版（保共振峰降调对齐 241Hz）cache，并把 A 版（自然音高）留在 live。
- 保留 live = A(FINAL2 自然音高)
- 产出 B 文件：H:\\daniya_voice_tools\\daniya_cache_B_pitchmatch_241.gguf
- 产出 A 文件：H:\\daniya_voice_tools\\daniya_cache_A_natural_287.gguf
- bundle 最终还原为 A(FINAL2)，与 live 一致
全程只改名/复制。"""
import os, sys, time, json, shutil, subprocess, hashlib, urllib.request

PY_VENV = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
TOOLS = r"H:\daniya_voice_tools"
RESAMPLE = os.path.join(TOOLS, "_resample_wav.py")
EXTRACT = os.path.join(TOOLS, "extract_prompt_bundle.py")

LLAMACPP_ROOT = r"H:\Comni\_internal\resources"
SERVER_BIN = os.path.join(LLAMACPP_ROOT, "build", "bin", "Release", "llama-server.exe")
MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
MODEL_GGUF = os.path.join(MODEL_DIR, "MiniCPM-o-4_5-Q4_K_M.gguf")
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")
BUNDLE = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio"
BUNDLE_EXE = r"H:\Comni\_internal\resources\build\bin\Release\tools\omni\assets\default_ref_audio"
REF2_48 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"
REF3_48 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL3_daniya_ref_48k.wav"
CACHE_A = os.path.join(TOOLS, "daniya_cache_A_natural_287.gguf")
CACHE_B = os.path.join(TOOLS, "daniya_cache_B_pitchmatch_241.gguf")
BUNDLE_A_KEEP = BUNDLE + ".keep_A_natural"
BUNDLE_B_KEEP = BUNDLE + ".keep_B_pitchmatch"

TS = time.strftime("%Y%m%d_%H%M%S")
EXPORT_DIR = os.path.join(TOOLS, "exported_cache_v3_" + TS)
OUTPUT_DIR = os.path.join(TOOLS, "cpp_output_v3_" + TS)
LOG_FILE = os.path.join(TOOLS, "bake_v3_llama.log")
PORT = 19069


def log(m):
    print("[bake3] " + m, flush=True)


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def size(p):
    return os.path.getsize(p) if os.path.exists(p) else -1


def set_bundle(ref48, tag):
    """把 ref48 转 24k 作 wav，提取 bins，装进两个 bundle 目录。"""
    tmp24 = os.path.join(TOOLS, "tmp_%s_24k.wav" % tag)
    r = subprocess.run([PY_VENV, RESAMPLE, ref48, tmp24, "24000"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("   resample(%s) rc=%s %s" % (tag, r.returncode, (r.stdout or r.stderr or "").strip()[:120]))
    r = subprocess.run([PY_VENV, EXTRACT, "--ref-audio", ref48, "--model-dir", TOOLS,
                        "--output-dir", BUNDLE],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("   extract(%s) rc=%s %s" % (tag, r.returncode, (r.stdout or r.stderr or "").strip().replace("\n", " | ")[:220]))
    shutil.copy2(tmp24, os.path.join(BUNDLE, "default_ref_audio.wav"))
    for d in (BUNDLE, BUNDLE_EXE):
        if not os.path.isdir(d):
            continue
        for f in ("default_ref_audio.wav", "spk_f32.bin", "prompt_tokens_i32.bin",
                  "prompt_mel_btc_f32.bin"):
            s = os.path.join(BUNDLE, f)
            t = os.path.join(d, f)
            if os.path.exists(s) and os.path.normcase(os.path.abspath(s)) != os.path.normcase(os.path.abspath(t)):
                shutil.copy2(s, t)
    log("   bundle(%s) 装配完成" % tag)


def bake():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for f in os.listdir(EXPORT_DIR):
        try:
            os.remove(os.path.join(EXPORT_DIR, f))
        except Exception:
            pass
    q = CACHE + ".quarantine_v3_" + TS
    os.rename(CACHE, q)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env["T2W_EXPORT_CACHE_DIR"] = EXPORT_DIR
    logf = open(LOG_FILE, "w", encoding="utf-8")
    proc = subprocess.Popen([SERVER_BIN, "--host", "0.0.0.0", "--port", str(PORT),
                             "--model", MODEL_GGUF, "--ctx-size", "8192", "--n-gpu-layers", "99"],
                            cwd=LLAMACPP_ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    exported = False
    start = time.time()
    try:
        base = "http://127.0.0.1:%d" % PORT
        ok = False
        while time.time() - start < 420:
            try:
                if urllib.request.urlopen(base + "/health", timeout=5).status == 200:
                    ok = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                break
            time.sleep(2)
        log("   health=%s" % ok)
        if ok:
            body = {
                "media_type": 2, "use_tts": True, "duplex_mode": False, "model_dir": MODEL_DIR,
                "tts_bin_dir": os.path.join(MODEL_DIR, "tts"), "tts_gpu_layers": 100,
                "token2wav_device": "gpu:0", "output_dir": OUTPUT_DIR,
                "voice_clone_prompt": "<|im_start|>system\nStreaming Duplex Conversation! You are a helpful assistant.\n<|audio_start|>",
                "assistant_prompt": "<|audio_end|><|im_end|>\n",
            }
            req = urllib.request.Request(base + "/v1/stream/omni_init",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    _ = r.read(200)
                log("   omni_init 200")
            except Exception as e:
                log("   omni_init note: %s" % e)
            while time.time() - start < 480:
                ec = os.path.join(EXPORT_DIR, "prompt_cache.gguf")
                if os.path.exists(ec) and os.path.getmtime(ec) >= start - 5:
                    exported = True
                    time.sleep(2)
                    break
                if proc.poll() is not None:
                    break
                time.sleep(3)
    finally:
        try:
            proc.terminate(); proc.wait(timeout=25)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logf.close()
    ec = os.path.join(EXPORT_DIR, "prompt_cache.gguf")
    if exported and os.path.exists(ec):
        shutil.copy2(ec, CACHE)
    elif os.path.exists(q):
        os.rename(q, CACHE)
    return exported


log("=" * 90)
log("0) 保存 A 版（自然音高 287Hz）为稳定产物")
shutil.copy2(CACHE, CACHE_A)
log("   A -> %s (%d B, md5=%s)" % (os.path.basename(CACHE_A), size(CACHE_A), md5(CACHE_A)[:12]))
if os.path.isdir(BUNDLE):
    if os.path.isdir(BUNDLE_A_KEEP):
        shutil.rmtree(BUNDLE_A_KEEP, ignore_errors=True)
    shutil.copytree(BUNDLE, BUNDLE_A_KEEP)
    log("   bundle(A) 存档 -> %s" % os.path.basename(BUNDLE_A_KEEP))

log("=" * 90)
log("1) 装配 B 版 bundle（FINAL3 保共振峰降调 241Hz）")
set_bundle(REF3_48, "b")
if os.path.isdir(BUNDLE_B_KEEP):
    os.rename(BUNDLE_B_KEEP, BUNDLE_B_KEEP + ".old_" + TS)
shutil.copytree(BUNDLE, BUNDLE_B_KEEP)
log("   bundle(B) 存档 -> %s" % os.path.basename(BUNDLE_B_KEEP))

log("=" * 90)
log("2) 烤 B 版 cache")
okb = bake()
log("   烤制结果: %s" % okb)
if okb:
    shutil.copy2(CACHE, CACHE_B)
    log("   B -> %s (%d B, md5=%s)" % (os.path.basename(CACHE_B), size(CACHE_B), md5(CACHE_B)[:12]))

log("=" * 90)
log("3) 还原 live = A 版（自然音高），并把 bundle 还原为 A")
shutil.copy2(CACHE_A, CACHE)
log("   live <- A (%d B, md5=%s)" % (size(CACHE), md5(CACHE)[:12]))
if os.path.isdir(BUNDLE_A_KEEP):
    for f in ("default_ref_audio.wav", "spk_f32.bin", "prompt_tokens_i32.bin",
              "prompt_mel_btc_f32.bin"):
        s = os.path.join(BUNDLE_A_KEEP, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(BUNDLE, f))
            if os.path.isdir(BUNDLE_EXE):
                shutil.copy2(s, os.path.join(BUNDLE_EXE, f))
log("   bundle 还原为 A")
log("=" * 90)
log("产物: A=%s  B=%s" % (CACHE_A, CACHE_B))
