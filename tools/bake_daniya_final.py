# -*- coding: utf-8 -*-
"""【达妮娅音色克隆 · 步骤 2~5/6】烘焙声码器 prompt_cache.gguf 并装回 live。

机制（llama.cpp-omni / Comni Token2Wav）：
  隔离 live cache → C++ init_from_prompt_cache_gguf 失败 → 回退 bundle
  (tools/omni/assets/default_ref_audio) → 设 T2W_EXPORT_CACHE_DIR 则导出
  → 日志出现 [Token2Mel] exported prompt_cache.gguf

🔴 本脚本相对 2026-09-21 旧脚本的关键修正：
  旧 bake_game48k.py 有"绝不覆盖 live cache"的安全铁律，产出的 cache 只落在
  exported_cache_*/standalone，**从未装回 live** —— 这就是 9/21 三次烘焙
  全部未生效的机制根源。本脚本在导出成功后**显式装回 live**，并逐项校验。

安全铁律（全部保留）：
  - 只改名/复制，绝不删除任何 live 文件
  - live cache 与 bundle 四件套均先完整备份
  - 任何失败路径都自动回滚到原始状态
  - 保留 standalone 产物 + 完整 md5 日志，可随时还原
"""
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
RESAMPLE = os.path.join(TOOLS, "_resample_wav.py")
EXTRACT = os.path.join(TOOLS, "extract_prompt_bundle.py")

LLAMACPP_ROOT = r"H:\Comni\_internal\resources"
SERVER_BIN = os.path.join(LLAMACPP_ROOT, "build", "bin", "Release", "llama-server.exe")
MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
MODEL_GGUF = os.path.join(MODEL_DIR, "MiniCPM-o-4_5-Q4_K_M.gguf")
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")

BUNDLE = os.path.join(LLAMACPP_ROOT, "tools", "omni", "assets", "default_ref_audio")
BUNDLE_EXE = os.path.join(LLAMACPP_ROOT, "build", "bin", "Release", "tools", "omni", "assets", "default_ref_audio")

REF48 = r"G:\ai对话\11\data\native_audio_test\ref_final\daniya_ref_final_48k.wav"
REF24 = r"G:\ai对话\11\data\native_audio_test\ref_final\daniya_ref_final_24k.wav"

TS = time.strftime("%Y%m%d_%H%M%S")
STANDALONE = os.path.join(TOOLS, "daniya_cache_FINAL_%s.gguf" % TS)
EXPORT_DIR = os.path.join(TOOLS, "exported_cache_final_%s" % TS)
OUTPUT_DIR = os.path.join(TOOLS, "cpp_output_final_%s" % TS)
BUNDLE_BACKUP = os.path.join(TOOLS, "bundle_backup_final_%s" % TS)
LOG_FILE = os.path.join(TOOLS, "bake_final_%s.log" % TS)
LLAMA_LOG = os.path.join(TOOLS, "bake_final_llama_%s.log" % TS)
PORT = 19073

BUNDLE_FILES = ["default_ref_audio.wav", "spk_f32.bin", "prompt_tokens_i32.bin", "prompt_mel_btc_f32.bin"]
_logf = open(LOG_FILE, "w", encoding="utf-8")


def log(m):
    line = "[bake_final] " + m
    print(line, flush=True)
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


def run(cmd, timeout=900):
    log("  $ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    log("    rc=%s %s" % (r.returncode, out.strip().replace("\n", " | ")[:400]))
    return r.returncode, out


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


def backup_bundle():
    os.makedirs(BUNDLE_BACKUP, exist_ok=True)
    for f in BUNDLE_FILES:
        s = os.path.join(BUNDLE, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(BUNDLE_BACKUP, f))
    log("bundle 备份 -> %s" % BUNDLE_BACKUP)


def restore_bundle():
    for f in BUNDLE_FILES:
        s = os.path.join(BUNDLE_BACKUP, f)
        if os.path.exists(s):
            for d in (BUNDLE, BUNDLE_EXE):
                if os.path.isdir(d):
                    shutil.copy2(s, os.path.join(d, f))
    log("bundle 已还原为原始（spk md5=%s）" % md5(os.path.join(BUNDLE, "spk_f32.bin")))


def set_bundle():
    """装配达妮娅 bundle：三个 bin 由 extract 从 48k 源生成；wav 用严格同源的 24k 版。"""
    run([PY_VENV, EXTRACT, "--ref-audio", REF48, "--model-dir", TOOLS, "--output-dir", BUNDLE])
    shutil.copy2(REF24, os.path.join(BUNDLE, "default_ref_audio.wav"))
    for d in (BUNDLE, BUNDLE_EXE):
        if not os.path.isdir(d):
            continue
        for f in BUNDLE_FILES:
            s = os.path.join(BUNDLE, f)
            t = os.path.join(d, f)
            if os.path.exists(s) and os.path.normcase(os.path.abspath(s)) != os.path.normcase(os.path.abspath(t)):
                shutil.copy2(s, t)
    log("bundle 装配完成（spk md5=%s，wav md5=%s）"
        % (md5(os.path.join(BUNDLE, "spk_f32.bin")),
           md5(os.path.join(BUNDLE, "default_ref_audio.wav"))))


def bake_and_install(quarantine):
    """烘焙并把产物装回 live。返回 (ok, exported_md5)"""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env["T2W_EXPORT_CACHE_DIR"] = EXPORT_DIR

    log("启动 llama-server（cwd=%s）" % LLAMACPP_ROOT)
    llama_logf = open(LLAMA_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [SERVER_BIN, "--host", "127.0.0.1", "--port", str(PORT),
         "--model", MODEL_GGUF, "--ctx-size", "8192", "--n-gpu-layers", "99"],
        cwd=LLAMACPP_ROOT, env=env, stdout=llama_logf, stderr=subprocess.STDOUT)

    start = time.time()
    base = "http://127.0.0.1:%d" % PORT
    exported = False
    try:
        ok = False
        while time.time() - start < 420:
            try:
                if urllib.request.urlopen(base + "/health", timeout=5).status == 200:
                    ok = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                log("!! llama-server 提前退出 rc=%s" % proc.returncode)
                break
            time.sleep(2)
        log("llama-server health=%s（耗时 %.0fs）" % (ok, time.time() - start))
        if not ok:
            return False, None

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
            with urllib.request.urlopen(req, timeout=90) as r:
                _ = r.read(200)
            log("omni_init 200 —— 无 voice_audio，C++ 走达妮娅 bundle 兜底")
        except Exception as e:
            log("omni_init note: %s（继续监听导出）" % e)

        ec = os.path.join(EXPORT_DIR, "prompt_cache.gguf")
        while time.time() - start < 660:
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
            proc.terminate()
            proc.wait(timeout=30)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            llama_logf.close()
        except Exception:
            pass

    ec = os.path.join(EXPORT_DIR, "prompt_cache.gguf")
    if not exported or not os.path.exists(ec):
        log("!! 未导出 cache —— 检查 %s" % LLAMA_LOG)
        try:
            t = open(LLAMA_LOG, encoding="utf-8", errors="replace").read()
            for line in t.splitlines():
                if any(k in line for k in ("exported", "prompt_bundle", "default_ref_audio",
                                           "Token2Mel", "token2wav", "error", "Error", "failed")):
                    log("   | " + line.strip()[:180])
        except Exception:
            pass
        return False, None

    # ==== 关键修正：装回 live ====
    shutil.copy2(ec, STANDALONE)
    log("STANDALONE 产物 -> %s (%d B, md5=%s)" % (STANDALONE, os.path.getsize(STANDALONE), md5(STANDALONE)))
    shutil.copy2(ec, CACHE)
    log(">>> 已装回 live: %s (%d B, md5=%s)" % (CACHE, os.path.getsize(CACHE), md5(CACHE)))

    # 校验导出行
    try:
        t = open(LLAMA_LOG, encoding="utf-8", errors="replace").read()
        n = t.count("exported prompt_cache.gguf")
        log("llama 日志中 'exported prompt_cache.gguf' 出现 %d 次" % n)
    except Exception:
        pass
    return True, md5(CACHE)


def main():
    log("=" * 96)
    log("达妮娅音色克隆 · 烘焙（含装回 live）        TS=%s" % TS)
    log("=" * 96)
    log("参考音 48k = %s" % REF48)
    log("参考音 24k = %s" % REF24)

    # --- 前置检查 ---
    errs = []
    for p in (SERVER_BIN, MODEL_GGUF, REF48, REF24, os.path.join(TOOLS, "speech_tokenizer_v2_25hz.onnx"),
              os.path.join(TOOLS, "campplus.onnx")):
        if not os.path.exists(p):
            errs.append("缺失: %s" % p)
    if not os.path.isdir(BUNDLE):
        errs.append("bundle 目录缺失: %s" % BUNDLE)
    if not os.path.exists(CACHE):
        errs.append("live cache 缺失: %s" % CACHE)
    if port_busy(PORT):
        errs.append("端口 %d 被占用" % PORT)
    if errs:
        for e in errs:
            log("!! 前置检查失败: " + e)
        log("中止，未做任何改动。")
        sys.exit(1)

    cache_md5_before = md5(CACHE)
    cache_sz_before = os.path.getsize(CACHE)
    bundle_before = snapshot_bundle()
    log("live cache  (改前) %d B  md5=%s" % (cache_sz_before, cache_md5_before))
    log("bundle  (改前) %s" % json.dumps(bundle_before, ensure_ascii=False))

    # --- 备份 ---
    log("-" * 96)
    backup_bundle()
    cache_bak = CACHE + ".bak_before_final_" + TS
    shutil.copy2(CACHE, cache_bak)
    log("live cache 备份 -> %s (md5=%s)" % (cache_bak, md5(cache_bak)))

    quarantine = CACHE + ".quarantine_final_" + TS
    ok = False
    try:
        # --- 装配新 bundle ---
        log("-" * 96)
        log("装配达妮娅 bundle …")
        set_bundle()

        # --- 隔离 live cache（强制 C++ 走 bundle 兜底）---
        log("-" * 96)
        os.rename(CACHE, quarantine)
        log("live cache 改名隔离 -> %s（不删除）" % quarantine)

        # --- 烘焙 + 装回 ---
        log("-" * 96)
        log("烘焙中 …（llama-server 加载 + omni_init + 等导出，约 2~6 分钟）")
        ok, new_md5 = bake_and_install(quarantine)

        if ok:
            log("-" * 96)
            log("验证：")
            log("  live cache (改后) %d B  md5=%s" % (os.path.getsize(CACHE), new_md5))
            log("  live cache (改前) %d B  md5=%s" % (cache_sz_before, cache_md5_before))
            log("  是否已变化: %s" % ("✅ 是（烘焙生效）" if new_md5 != cache_md5_before else "❌ 否"))
            log("  隔离原件保留在: %s" % quarantine)
        else:
            log("!! 烘焙失败，回滚 …")
            if os.path.exists(quarantine):
                if os.path.exists(CACHE):
                    os.rename(CACHE, CACHE + ".failed_" + TS)
                os.rename(quarantine, CACHE)
                log("live cache 已回滚（md5=%s）" % md5(CACHE))
    finally:
        # 保底：任何异常路径下若 live cache 缺失，立即从隔离件恢复
        if not os.path.exists(CACHE) and os.path.exists(quarantine):
            try:
                os.rename(quarantine, CACHE)
                log("保底回滚：live cache 已从隔离件恢复（md5=%s）" % md5(CACHE))
            except Exception as e:
                log("!! 保底回滚失败: %s —— 请手动把 %s 改名回 prompt_cache.gguf" % (e, quarantine))
        # bundle 无论如何都还原为原厂
        log("-" * 96)
        restore_bundle()

    log("=" * 96)
    log("最终状态：")
    log("  live cache md5=%s (大小 %s B)" % (md5(CACHE), os.path.getsize(CACHE) if os.path.exists(CACHE) else "-"))
    log("  bundle spk md5=%s" % md5(os.path.join(BUNDLE, "spk_f32.bin")))
    if ok:
        log("")
        log("SUCCESS —— 新音色 cache 已装回 live。")
        log("  下一步：重启 companion / Comni 引擎（同档内 /v1/stream/break 不会重载缓存）。")
        log("  回滚命令：copy /Y \"%s\" \"%s\"" % (cache_bak, CACHE))
    else:
        log("")
        log("FAILED —— 系统已回到原始状态，未留下任何改动。")
    log("日志: %s" % LOG_FILE)
    _logf.close()
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
