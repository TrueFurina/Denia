# -*- coding: utf-8 -*-
"""通用音色烘焙器（参考音可指定）。

用法：
    python bake_ref.py --ref24 <24k wav> --tag v2a [--port 19076]

流程（与规范 6 步一致，且保留"装回 live"这一关键修正）：
  extract_prompt_bundle(16k tokens/spk + 24k mel) → 装配 bundle → 隔离 live cache
  → 起 llama-server(cwd=llamacpp_root) → omni_init 不带 voice_audio → 等导出
  → 把导出的 prompt_cache.gguf 装回 live → 还原原厂 bundle
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

PY_VENV = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
TOOLS = r"H:\daniya_voice_tools"
EXTRACT = os.path.join(TOOLS, "extract_prompt_bundle.py")
LLAMACPP_ROOT = r"H:\Comni\_internal\resources"
SERVER_BIN = os.path.join(LLAMACPP_ROOT, "build", "bin", "Release", "llama-server.exe")
MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
MODEL_GGUF = os.path.join(MODEL_DIR, "MiniCPM-o-4_5-Q4_K_M.gguf")
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")
BUNDLE = os.path.join(LLAMACPP_ROOT, "tools", "omni", "assets", "default_ref_audio")
BUNDLE_EXE = os.path.join(LLAMACPP_ROOT, "build", "bin", "Release", "tools", "omni", "assets", "default_ref_audio")
BUNDLE_FILES = ["default_ref_audio.wav", "spk_f32.bin", "prompt_tokens_i32.bin", "prompt_mel_btc_f32.bin"]
TS = time.strftime("%Y%m%d_%H%M%S")
_logf = None


def log(m):
    line = "[bake] " + m
    print(line, flush=True)
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()


def md5(p):
    if not os.path.exists(p):
        return "MISSING"
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def port_busy(port):
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False


def snapshot_bundle():
    return {f: md5(os.path.join(BUNDLE, f)) for f in BUNDLE_FILES}


def backup_bundle(dst):
    os.makedirs(dst, exist_ok=True)
    for f in BUNDLE_FILES:
        s = os.path.join(BUNDLE, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(dst, f))
    log("bundle 备份 -> %s" % dst)


def restore_bundle(dst):
    n = 0
    for f in BUNDLE_FILES:
        s = os.path.join(dst, f)
        if os.path.exists(s):
            for d in (BUNDLE, BUNDLE_EXE):
                if os.path.isdir(d):
                    shutil.copy2(s, os.path.join(d, f))
                    n += 1
    log("bundle 已还原为原厂（spk md5=%s）" % md5(os.path.join(BUNDLE, "spk_f32.bin")))


def set_bundle(ref24):
    r = subprocess.run([PY_VENV, EXTRACT, "--ref-audio", ref24,
                        "--model-dir", TOOLS, "--output-dir", BUNDLE],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("extract rc=%s | %s" % (r.returncode, (r.stdout or "").strip().replace("\n", " | ")[:200]))
    if r.returncode != 0:
        log("!! extract 失败: %s" % (r.stderr or "")[:300])
        return False
    shutil.copy2(ref24, os.path.join(BUNDLE, "default_ref_audio.wav"))
    for d in (BUNDLE, BUNDLE_EXE):
        if not os.path.isdir(d):
            continue
        for f in BUNDLE_FILES:
            s = os.path.join(BUNDLE, f)
            t = os.path.join(d, f)
            if os.path.exists(s) and os.path.normcase(os.path.abspath(s)) != os.path.normcase(os.path.abspath(t)):
                shutil.copy2(s, t)
    import soundfile as _sf
    w = os.path.join(BUNDLE, "default_ref_audio.wav")
    n_tok = os.path.getsize(os.path.join(BUNDLE, "prompt_tokens_i32.bin")) // 4
    n_mel = os.path.getsize(os.path.join(BUNDLE, "prompt_mel_btc_f32.bin")) // 4 // 80
    log("bundle 装配完成: spk md5=%s  wav md5=%s" % (md5(os.path.join(BUNDLE, "spk_f32.bin")), md5(w)))
    log("                T_token=%d (%.2fs)  mel=%d 帧 (%.2fs)  wav sr=%d dur=%.2fs"
        % (n_tok, n_tok * 0.04, n_mel, n_mel * 0.02, _sf.info(w).samplerate, _sf.info(w).duration))
    return True


def bake(port, tag, export_dir, output_dir, llama_log):
    os.makedirs(export_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env["T2W_EXPORT_CACHE_DIR"] = export_dir
    for k in list(env):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            env.pop(k, None)

    lf = open(llama_log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [SERVER_BIN, "--host", "127.0.0.1", "--port", str(port),
         "--model", MODEL_GGUF, "--ctx-size", "8192", "--n-gpu-layers", "99"],
        cwd=LLAMACPP_ROOT, env=env, stdout=lf, stderr=subprocess.STDOUT)
    start = time.time()
    base = "http://127.0.0.1:%d" % port
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    exported = False
    try:
        ok = False
        while time.time() - start < 300:
            try:
                if op.open(base + "/health", timeout=5).status == 200:
                    ok = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                log("!! llama-server 提前退出 rc=%s" % proc.returncode)
                break
            time.sleep(2)
        log("llama-server health=%s（%.0fs）" % (ok, time.time() - start))
        if not ok:
            return False
        body = {
            "media_type": 2, "use_tts": True, "duplex_mode": False, "model_dir": MODEL_DIR,
            "tts_bin_dir": os.path.join(MODEL_DIR, "tts"), "tts_gpu_layers": 100,
            "token2wav_device": "gpu:0", "output_dir": output_dir,
            "voice_clone_prompt": "<|im_start|>system\nStreaming Duplex Conversation! You are a helpful assistant.\n<|audio_start|>",
            "assistant_prompt": "<|audio_end|><|im_end|>\n",
        }
        req = urllib.request.Request(base + "/v1/stream/omni_init",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with op.open(req, timeout=90) as r:
                r.read(200)
            log("omni_init 200 —— 不带 voice_audio，C++ 走 bundle 兜底")
        except Exception as e:
            log("omni_init note: %s（继续监听导出）" % e)
        ec = os.path.join(export_dir, "prompt_cache.gguf")
        while time.time() - start < 600:
            if os.path.exists(ec) and os.path.getmtime(ec) >= start - 10:
                exported = True
                time.sleep(4)
                break
            if proc.poll() is not None:
                log("!! llama-server 在导出前退出")
                break
            time.sleep(3)
    finally:
        try:
            proc.terminate(); proc.wait(timeout=30)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        lf.close()

    ec = os.path.join(export_dir, "prompt_cache.gguf")
    if not exported or not os.path.exists(ec):
        log("!! 未导出 cache —— 检查 %s" % llama_log)
        try:
            t = open(llama_log, encoding="utf-8", errors="replace").read()
            for line in t.splitlines():
                if any(k in line for k in ("exported", "prompt_bundle", "Token2Mel", "failed", "rror")):
                    log("   | " + line.strip()[:180])
        except Exception:
            pass
        return False
    log("导出成功 %d B md5=%s" % (os.path.getsize(ec), md5(ec)))
    return True


def main():
    global _logf
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref24", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--port", type=int, default=19076)
    a = ap.parse_args()

    standalone = os.path.join(TOOLS, "daniya_cache_%s_%s.gguf" % (a.tag, TS))
    export_dir = os.path.join(TOOLS, "exported_cache_%s_%s" % (a.tag, TS))
    output_dir = os.path.join(TOOLS, "cpp_output_%s_%s" % (a.tag, TS))
    bundle_bak = os.path.join(TOOLS, "bundle_backup_%s_%s" % (a.tag, TS))
    llama_log = os.path.join(TOOLS, "bake_%s_llama_%s.log" % (a.tag, TS))
    _logf = open(os.path.join(TOOLS, "bake_%s_%s.log" % (a.tag, TS)), "w", encoding="utf-8")

    log("=" * 96)
    log("通用烘焙 tag=%s  ref=%s" % (a.tag, a.ref24))
    log("=" * 96)
    for p in (SERVER_BIN, MODEL_GGUF, a.ref24,
              os.path.join(TOOLS, "speech_tokenizer_v2_25hz.onnx"),
              os.path.join(TOOLS, "campplus.onnx")):
        if not os.path.exists(p):
            log("!! 缺失 %s" % p); sys.exit(1)
    if port_busy(a.port):
        log("!! 端口 %d 被占用" % a.port); sys.exit(1)

    import soundfile as sf
    i = sf.info(a.ref24)
    log("参考音 sr=%d dur=%.3fs ch=%d" % (i.samplerate, i.duration, i.channels))
    log("live cache 改前 %d B md5=%s" % (os.path.getsize(CACHE), md5(CACHE)))
    log("bundle 改前 %s" % json.dumps(snapshot_bundle(), ensure_ascii=False))

    backup_bundle(bundle_bak)
    cache_bak = CACHE + ".bak_before_%s_%s" % (a.tag, TS)
    shutil.copy2(CACHE, cache_bak)
    quarantine = CACHE + ".quarantine_%s_%s" % (a.tag, TS)
    ok = False
    try:
        if not set_bundle(a.ref24):
            log("!! bundle 装配失败"); return
        os.rename(CACHE, quarantine)
        log("live cache 隔离 -> %s" % quarantine)
        log("烘焙中 …")
        ok = bake(a.port, a.tag, export_dir, output_dir, llama_log)
        if ok:
            ec = os.path.join(export_dir, "prompt_cache.gguf")
            shutil.copy2(ec, standalone)
            shutil.copy2(ec, CACHE)
            log(">>> 已装回 live: %d B md5=%s" % (os.path.getsize(CACHE), md5(CACHE)))
            log("回滚点: %s" % cache_bak)
    finally:
        if not os.path.exists(CACHE) and os.path.exists(quarantine):
            os.rename(quarantine, CACHE)
            log("保底回滚 live cache（md5=%s）" % md5(CACHE))
        restore_bundle(bundle_bak)
        log("-" * 96)
        log("最终 live cache md5=%s" % md5(CACHE))
        log("SUCCESS=%s" % ok)
        if _logf:
            _logf.close()
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
