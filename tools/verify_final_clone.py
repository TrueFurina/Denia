# -*- coding: utf-8 -*-
"""【达妮娅音色克隆 · 步骤 6/6】决定性验证：新 cache vs 旧 cache 的严格 A/B 合成。

对照设计（严谨性关键）：
  - 同一输入音频 INPUT_sapi_neutral.wav
  - 同一 prepare 参考音（本次的新宽频参考音，两次完全一致）
  - 同一 persona / 同一 generation 参数
  ⇒ 唯一变量 = live 声码器 cache（voice_clone_prompt 的强音色层）

  新 = daniya_cache_FINAL_20260921_211602.gguf（9.62s 宽频 48k 素材，>8k=27.16%，F0 246Hz）
  旧 = prompt_cache.gguf.bak_before_final_20260921_211602（8.03s 16k 素材，>8k=0.00%，F0 239Hz）

直连 worker（绕过 gateway），worker 为明文 ws://（非 wss://）。
安全：测试期间临时替换 live cache，结束无条件还原为新 cache。
"""
import asyncio
import base64
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time

import numpy as np

PY_EMBED = r"H:\Comni\_internal\resources\python-embed\python.exe"
SERVER_DIR = r"H:\Comni\_internal\resources\apps\server"
WK_PORT = 22730
OUTDIR = r"G:\ai对话\11\data\native_audio_test\verify_final"
INPUT_WAV = r"G:\ai对话\11\data\native_audio_test\INPUT_sapi_neutral.wav"
REF16 = os.path.join(OUTDIR, "ref_final_16k.wav")
REF48_SRC = r"G:\ai对话\11\data\native_audio_test\ref_final\daniya_ref_final_48k.wav"
SR_IN, SR_OUT = 16000, 24000
FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"

MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")
NEW_CACHE = r"H:\daniya_voice_tools\daniya_cache_FINAL_20260921_211602.gguf"
OLD_CACHE = CACHE + ".bak_before_final_20260921_211602"

TS = time.strftime("%Y%m%d_%H%M%S")


def log(m):
    print("[verify] " + m, flush=True)


def port_free(p):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", p))
        s.close()
        return False
    except Exception:
        return True


def clean_env():
    e = os.environ.copy()
    for k in list(e):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            e.pop(k, None)
    e["PYTHONPATH"] = "."
    e["TORCHINDUCTOR_CACHE_DIR"] = os.path.join(SERVER_DIR, "torch_compile_cache")
    return e


def wait_http_health(url, timeout_s=480):
    import urllib.request
    op = urllib.request.build_opener(urllib.request.HTTPHandler(), urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with op.open(url, timeout=4) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def load_wav_mono_f32(path, target_sr):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != target_sr:
        r = subprocess.run([FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                            "-f", "f32le", "-ar", str(target_sr), "-ac", "1", "-"],
                           input=x.astype("<f4").tobytes(), capture_output=True)
        x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    return x


ref_b64 = None
inp = None


def do_one(cache_src, out_wav, label):
    log("=== %s : 载入 cache %s (%d B) ===" % (label, os.path.basename(cache_src), os.path.getsize(cache_src)))
    shutil.copy2(cache_src, CACHE)
    env = clean_env()
    wk_log = open(os.path.join(OUTDIR, "worker_%s_%s.log" % (label, TS)), "w", encoding="utf-8")
    wk = subprocess.Popen(
        [PY_EMBED, "worker.py", "--port", str(WK_PORT), "--gpu-id", "0",
         "--worker-index", "0", "--ref-audio-path", REF16],
        cwd=SERVER_DIR, env=env, stdout=wk_log, stderr=subprocess.STDOUT)
    ok = wait_http_health("http://127.0.0.1:%d/health" % WK_PORT, timeout_s=480)
    log("worker health: %s" % ok)
    chunks, texts, err = [], [], None
    if ok:
        import websockets

        clone_instr = "模仿音频样本的音色并生成新的内容。"
        persona = ("你是达妮娅，鸣潮里的角色。语气温柔活泼、亲切、带点俏皮，说话自然流畅，"
                   "像在跟喜欢的人聊天，不要念台词、不要加动作描写。")

        async def run():
            sid = "vfy_%s_%d" % (label, int(time.time()))
            url = "ws://127.0.0.1:%d/ws/half_duplex?session_id=%s" % (WK_PORT, sid)
            t0 = time.perf_counter()
            async with websockets.connect(url, proxy=None, ssl=None, max_size=512 * 1024 * 1024,
                                          ping_interval=None, open_timeout=200) as ws:
                await ws.send(json.dumps({
                    "type": "prepare",
                    "system_content": [
                        {"type": "text", "text": clone_instr},
                        {"type": "audio", "data": ref_b64},
                        {"type": "text", "text": persona},
                    ],
                    "config": {
                        "vad": {"threshold": 0.35, "min_speech_duration_ms": 80,
                                "min_silence_duration_ms": 600, "speech_pad_ms": 30},
                        "generation": {"max_new_tokens": 512, "length_penalty": 1.3, "temperature": 0.3},
                        "tts": {"enabled": True, "tts_temperature": 0.3},
                        "session": {"timeout_s": 400},
                    },
                }))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=220))
                    if m.get("type") == "prepared":
                        log("[prepared] after %.1fs" % (time.perf_counter() - t0))
                        break
                    if m.get("type") == "error":
                        log("[prepare error] " + str(m))
                        return
                await asyncio.sleep(8)
                step = int(SR_IN * 0.25)
                for i in range(0, len(inp), step):
                    s = inp[i:i + step]
                    if len(s) < step:
                        s = np.pad(s, (0, step - len(s)))
                    await ws.send(json.dumps({"type": "audio_chunk",
                        "audio_base64": base64.b64encode(s.astype("<f4").tobytes()).decode()}))
                    await asyncio.sleep(0.12)
                sil = np.zeros(int(SR_IN * 2.5), dtype=np.float32)
                for i in range(0, len(sil), step):
                    s = sil[i:i + step]
                    if len(s) < step:
                        s = np.pad(s, (0, step - len(s)))
                    await ws.send(json.dumps({"type": "audio_chunk",
                        "audio_base64": base64.b64encode(s.astype("<f4").tobytes()).decode()}))
                    await asyncio.sleep(0.12)
                t1 = time.perf_counter()
                while time.perf_counter() - t1 < 150:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    except asyncio.TimeoutError:
                        continue
                    m = json.loads(raw)
                    mt = m.get("type")
                    if mt == "chunk":
                        ad = m.get("audio_data")
                        if ad:
                            chunks.append(np.frombuffer(base64.b64decode(ad), np.float32))
                        tx = m.get("text_delta") or m.get("text")
                        if tx:
                            texts.append(tx)
                    elif mt == "turn_done":
                        log("[turn_done] " + (m.get("text") or "")[:190])
                        break
                    elif mt in ("error", "timeout"):
                        log("[%s] %s" % (mt, m))
                        break
                try:
                    await ws.send(json.dumps({"type": "stop"}))
                except Exception:
                    pass

        asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            asyncio.get_event_loop().run_until_complete(run())
        except Exception as e:
            err = repr(e)
            log("[EXC] %s" % e)
    try:
        wk.terminate()
    except Exception:
        pass
    time.sleep(3)
    try:
        if wk.poll() is None:
            wk.kill()
    except Exception:
        pass
    wk_log.close()
    if chunks:
        import soundfile as sf
        pcm = np.concatenate(chunks)
        sf.write(out_wav, pcm, SR_OUT)
        txt = " | ".join(texts).strip()
        log("[SAVED] %s  %.2fs rms=%.4f" % (out_wav, len(pcm) / SR_OUT, float(np.sqrt(np.mean(pcm ** 2)))))
        log("        文本: %s" % txt[:190])
        return True, txt
    log("[NO AUDIO] 文本=%s err=%s" % (" | ".join(texts)[:300], err))
    return False, ""


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    if not port_free(WK_PORT):
        log("!! 端口 %d 被占用" % WK_PORT)
        sys.exit(1)
    for p in (NEW_CACHE, OLD_CACHE, INPUT_WAV, REF48_SRC):
        if not os.path.exists(p):
            log("!! 缺失: %s" % p)
            sys.exit(1)
    if not os.path.exists(FFMPEG):
        log("!! ffmpeg 缺失: %s" % FFMPEG)
        sys.exit(1)

    global ref_b64, inp
    # 参考音 16k（prepare 用；两次完全一致，保证唯一变量是 cache）
    if not os.path.exists(REF16):
        r = subprocess.run([FFMPEG, "-v", "quiet", "-i", REF48_SRC, "-ac", "1", "-ar", str(SR_IN),
                            "-f", "wav", REF16], capture_output=True)
        log("生成 16k 参考音: %s" % (REF16 if os.path.exists(REF16) else "FAILED "+str(r.stderr)[:200]))
    ref_pcm = load_wav_mono_f32(REF16, SR_IN)
    ref_b64 = base64.b64encode(ref_pcm.astype("<f4").tobytes()).decode()
    log("参考音(prepare): %s  %.2fs" % (os.path.basename(REF16), len(ref_pcm) / SR_IN))
    inp = load_wav_mono_f32(INPUT_WAV, SR_IN)
    peak = float(np.max(np.abs(inp)))
    inp = np.clip(inp * min(0.7 / peak, 4.0), -1.0, 1.0) if peak > 0 else inp
    log("输入音频: %.2fs rms=%.4f" % (len(inp) / SR_IN, float(np.sqrt(np.mean(inp ** 2)))))

    results = {}
    try:
        ok1, t1 = do_one(NEW_CACHE, os.path.join(OUTDIR, "S1_new.wav"), "new")
        results["new"] = (ok1, t1)
        ok2, t2 = do_one(OLD_CACHE, os.path.join(OUTDIR, "S2_old.wav"), "old")
        results["old"] = (ok2, t2)
    finally:
        shutil.copy2(NEW_CACHE, CACHE)
        log("live cache 已还原为【新】烘焙版（md5 校验见下）")

    # 拼对照：新版 → 1.2s 静音 → 旧版
    import soundfile as sf
    a, b = os.path.join(OUTDIR, "S1_new.wav"), os.path.join(OUTDIR, "S2_old.wav")
    if os.path.exists(a) and os.path.exists(b):
        xa, sr = sf.read(a, dtype="float32")
        xb, _ = sf.read(b, dtype="float32")
        gap = np.zeros(int(sr * 1.2), dtype=np.float32)
        sf.write(os.path.join(OUTDIR, "AB_对照_新在前旧在后.wav"),
                 np.concatenate([xa, gap, xb]), sr)
        log("对照文件: AB_对照_新在前旧在后.wav")

    log("=" * 78)
    log("RESULT  新=%s  旧=%s" % (results.get("new"), results.get("old")))
    log("输出目录: %s" % OUTDIR)


if __name__ == "__main__":
    main()
