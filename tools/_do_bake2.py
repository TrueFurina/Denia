# -*- coding: utf-8 -*-
"""重烤 prompt_cache.gguf v2 —— 修正 bundle wav 采样率(48k->24k) + 同步两个 bundle 目录
+ 全新空导出目录 + 重度埋点。全程只改名/复制，不删除。"""
import os, sys, time, json, shutil, subprocess, hashlib, urllib.request

PY_PROJ = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
PY_VENV = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
TOOLS = r"H:\daniya_voice_tools"
RESAMPLE = os.path.join(TOOLS, "_resample_wav.py")

LLAMACPP_ROOT = r"H:\Comni\_internal\resources"
SERVER_BIN = os.path.join(LLAMACPP_ROOT, "build", "bin", "Release", "llama-server.exe")
MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
MODEL_GGUF = os.path.join(MODEL_DIR, "MiniCPM-o-4_5-Q4_K_M.gguf")
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")
CACHE_MINICPM_TMP = CACHE + ".minicpm_tmp"

BUNDLE = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio"
BUNDLE_EXE = r"H:\Comni\_internal\resources\build\bin\Release\tools\omni\assets\default_ref_audio"
REF48 = r"G:\ai对话\11\data\native_audio_test\ref_build\FINAL2_daniya_ref_48k.wav"

TS = time.strftime("%Y%m%d_%H%M%S")
EXPORT_DIR = os.path.join(TOOLS, "exported_cache_v2_" + TS)
OUTPUT_DIR = os.path.join(TOOLS, "cpp_output_v2_" + TS)
LOG_FILE = os.path.join(TOOLS, "bake_v2_llama.log")
CPP_PORT = 19067
HEALTH_TIMEOUT = 420
WATCH_TIMEOUT = 420


def log(m):
    line = "[bake2] " + m
    print(line, flush=True)


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def size(p):
    return os.path.getsize(p) if os.path.exists(p) else -1


def wav_info(p):
    try:
        import wave
        with wave.open(p, "rb") as w:
            return "sr=%d ch=%d frames=%d dur=%.2fs" % (
                w.getframerate(), w.getnchannels(), w.getnframes(),
                w.getnframes() / w.getframerate())
    except Exception as e:
        return "ERR %s" % e


log("=" * 96)
log("A) 修正 bundle wav -> 24kHz")
tmp24 = os.path.join(TOOLS, "tmp_default_ref_24k.wav")
r = subprocess.run([PY_VENV, RESAMPLE, REF48, tmp24, "24000"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
log("   resample rc=%s  %s" % (r.returncode, (r.stdout or r.stderr or "").strip()))
if r.returncode != 0 or not os.path.exists(tmp24):
    log("   !! 重采样失败，中止")
    sys.exit(1)
log("   新 wav: %s [%s]" % (tmp24, wav_info(tmp24)))

for d in (BUNDLE, BUNDLE_EXE):
    if not os.path.isdir(d):
        log("   %s 不存在，跳过" % d)
        continue
    cur = os.path.join(d, "default_ref_audio.wav")
    if os.path.exists(cur):
        bak = cur + ".bak48k_" + TS
        shutil.copy2(cur, bak)
        log("   备份 %s -> %s" % (cur, os.path.basename(bak)))
    shutil.copy2(tmp24, cur)
    log("   安装 24k wav -> %s [%s]" % (cur, wav_info(cur)))

log("=" * 96)
log("B) 同步 bundle 的 .bin 到 exe 旁目录")
if os.path.isdir(BUNDLE_EXE):
    for f in ("spk_f32.bin", "prompt_tokens_i32.bin", "prompt_mel_btc_f32.bin"):
        s, t = os.path.join(BUNDLE, f), os.path.join(BUNDLE_EXE, f)
        if os.path.exists(s):
            if os.path.exists(t):
                shutil.copy2(t, t + ".bak_" + TS)
            shutil.copy2(s, t)
            log("   %s -> exe旁 (%d B)" % (f, size(t)))
log("   bundle 现状:")
for f in sorted(os.listdir(BUNDLE)):
    if f.endswith(".bin") or f.endswith(".wav"):
        log("      %10d  %s" % (size(os.path.join(BUNDLE, f)), f))

log("=" * 96)
log("C) 备份并隔离 live cache")
os.makedirs(EXPORT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
for f in os.listdir(EXPORT_DIR):
    try:
        os.remove(os.path.join(EXPORT_DIR, f))
    except Exception:
        pass
log("   导出目录(空): %s" % EXPORT_DIR)
old_md5 = md5(CACHE) if os.path.exists(CACHE) else "MISSING"
log("   live cache: %d B md5=%s" % (size(CACHE), old_md5[:16]))
bak_cache = CACHE + ".bak_v2_" + TS
shutil.copy2(CACHE, bak_cache)
log("   备份 -> %s" % os.path.basename(bak_cache))
q = CACHE + ".quarantine_v2_" + TS
os.rename(CACHE, q)
log("   隔离 -> %s" % os.path.basename(q))

log("=" * 96)
log("D) 启动 llama-server (port %d)" % CPP_PORT)
env = os.environ.copy()
env["CUDA_VISIBLE_DEVICES"] = "0"
env["T2W_EXPORT_CACHE_DIR"] = EXPORT_DIR
cmd = [SERVER_BIN, "--host", "0.0.0.0", "--port", str(CPP_PORT), "--model", MODEL_GGUF,
       "--ctx-size", "8192", "--n-gpu-layers", "99", "--repeat-penalty", "1.05", "--temp", "0.7"]
logf = open(LOG_FILE, "w", encoding="utf-8")
proc = subprocess.Popen(cmd, cwd=LLAMACPP_ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
log("   pid=%d, cwd=%s" % (proc.pid, LLAMACPP_ROOT))

exported = False
start = time.time()
try:
    base = "http://127.0.0.1:%d" % CPP_PORT
    ok = False
    t0 = time.time()
    while time.time() - t0 < HEALTH_TIMEOUT:
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            pass
        if proc.poll() is not None:
            log("   !! 进程提前退出")
            break
        time.sleep(2)
    log("   health=%s (%.0fs)" % (ok, time.time() - t0))
    if not ok:
        raise RuntimeError("server not healthy")

    body = {
        "media_type": 2, "use_tts": True, "duplex_mode": False,
        "model_dir": MODEL_DIR, "tts_bin_dir": os.path.join(MODEL_DIR, "tts"),
        "tts_gpu_layers": 100, "token2wav_device": "gpu:0", "output_dir": OUTPUT_DIR,
        "voice_clone_prompt": "<|im_start|>system\nStreaming Duplex Conversation! You are a helpful assistant.\n<|audio_start|>",
        "assistant_prompt": "<|audio_end|><|im_end|>\n",
    }
    req = urllib.request.Request(base + "/v1/stream/omni_init",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    log("   POST omni_init (no voice_audio)")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            _ = r.read(200)
        log("   omni_init 200")
    except Exception as e:
        log("   omni_init note: %s" % e)

    log("   监听导出 (最多 %ds)..." % WATCH_TIMEOUT)
    deadline = time.time() + WATCH_TIMEOUT
    n = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            log("   进程结束，停止监听")
            break
        ec = os.path.join(EXPORT_DIR, "prompt_cache.gguf")
        if os.path.exists(ec) and os.path.getmtime(ec) >= start - 5:
            exported = True
            time.sleep(3)
            break
        if os.path.exists(CACHE) and os.path.getmtime(CACHE) >= start - 5:
            exported = True
            time.sleep(3)
            break
        if os.path.exists(CACHE_MINICPM_TMP) and os.path.getmtime(CACHE_MINICPM_TMP) >= start - 5:
            log("   [注意] 出现 .minicpm_tmp (%d B)" % size(CACHE_MINICPM_TMP))
        n += 1
        if n % 3 == 1:
            try:
                tail = open(LOG_FILE, "r", encoding="utf-8", errors="replace").read()[-1600:]
            except Exception:
                tail = ""
            keep = [l for l in tail.splitlines()
                    if any(k in l for k in ("export", "fallback", "prompt_bundle",
                                            "prompt_cache", "Token2", "error", "fail"))]
            log("   t=%ds 日志要点: %s" % (int(time.time() - start),
                                          " | ".join(keep[-4:]) if keep else "(无)"))
        time.sleep(4)
finally:
    try:
        proc.terminate()
        proc.wait(timeout=25)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    logf.close()

log("=" * 96)
if not exported:
    if not os.path.exists(CACHE) and os.path.exists(q):
        os.rename(q, CACHE)
    log("E) 结果: 失败 —— cache 未重生，已还原")
    log("   完整日志: %s" % LOG_FILE)
    sys.exit(1)

ec = os.path.join(EXPORT_DIR, "prompt_cache.gguf")
if os.path.exists(ec) and os.path.getmtime(ec) >= start - 5:
    shutil.copy2(ec, CACHE)
    log("E) 结果: 成功！导出 cache -> live (%d B)" % size(CACHE))
else:
    log("E) 结果: 成功(原地写入) (%d B)" % size(CACHE))
new_md5 = md5(CACHE)
log("   old md5=%s" % old_md5[:16])
log("   new md5=%s" % new_md5[:16])
log("   变化: %s" % (new_md5 != old_md5))
log("=" * 96)
