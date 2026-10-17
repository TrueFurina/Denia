# -*- coding: utf-8 -*-
"""【交付验证】用修复后的真实通话链路（duplex / adx_ 音频档）生成试听样本。

目的：证明「个性化音色」是否真的生效，且不再被 MiniCPM 原生底色污染。

对照设计（唯一变量 = prepare 里有没有传参考音）：
  A_FIXED   修复后行为：ref_audio_path + ref_audio_base64 + tts_ref_audio_base64
                        + system_content(内嵌 audio 块)
                        ⇒ 引擎在 <|audio_start|> 锚点克隆【达妮娅】声线
  B_NATIVE  修复前行为：以上四样【一律不传】
                        ⇒ worker.py:2095 强制回落 _LANG_REF_AUDIO_PATHS["zh"]
                          = ref_minicpm_signature.wav（MiniCPM 原生签名音 F0≈286Hz）

其余全部一致：同一 live 声码器 cache、同一输入音频、同一 persona、同一采样参数、
同一 worker 启动方式（worker.py --port P --gpu-id 0 --worker-index 0，与 app 完全一致）。

链路忠实度：session_id 用 adx_ 前缀（= app 的音频通话档，media_type=1），
采样方式为「发一 chunk → 等 result」同步乒乓，避免 worker 侧队列(maxsize=2)丢帧。

⚠️ 音频下行有【两条】通道（worker.py 源码实锤）：
   1. {"type":"result",  "audio_data":...}          —— 模型 generate 内联音频
   2. {"type":"audio_only","audio_data":...}        —— 100ms 轮询 C++ T2W 产出的 wav
                                                       (worker.py:2181-2214)
   原生 TTS 档实际走通道 2（[WAV poll] sent audio_only）。两条都收。
"""
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import numpy as np

PY_EMBED = r"H:\Comni\_internal\resources\python-embed\python.exe"
SERVER_DIR = r"H:\Comni\_internal\resources\apps\server"
FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
WK_PORT = 22733

MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")

REF_WAV = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
SIG_WAV = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
INPUT_WAV = r"G:\ai对话\11\data\native_audio_test\INPUT_sapi_neutral.wav"

OUTDIR = r"G:\ai对话\11\data\native_audio_test\final_sample"
SR_IN, SR_OUT = 16000, 24000
TS = time.strftime("%Y%m%d_%H%M%S")

CLONE_INSTR = "模仿音频样本的音色并生成新的内容。"
PERSONA = ("你是达妮娅，鸣潮里的角色。语气温柔活泼、亲切、带点俏皮，说话自然流畅，"
           "像在跟喜欢的人聊天，不要念台词、不要加动作描写。")

GEN_CFG = {
    "generate_audio": True,
    "ls_mode": "explicit",
    "force_listen_count": 2,
    "listen_prob_scale": 1.0,
    "max_new_speak_tokens_per_chunk": 40,
    "length_penalty": 0.9,
    "temperature": 0.3,
    "tts_temperature": 0.3,
}


def log(m):
    print("[sample] " + m, flush=True)


def md5(p):
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def port_free(p):
    s = socket.socket(); s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", p)); s.close(); return False
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


def wait_health(url, timeout_s=600):
    import urllib.request
    op = urllib.request.build_opener(urllib.request.HTTPHandler(),
                                     urllib.request.ProxyHandler({}))
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


def load_mono_f32(path, sr_target):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != sr_target:
        r = subprocess.run([FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", str(sr), "-ac", "1",
                            "-i", "-", "-f", "f32le", "-ar", str(sr_target), "-ac", "1", "-"],
                           input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
        x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    return np.asarray(x, dtype=np.float32)


def ascii_ref_path():
    """与 local_omni_duplex._ascii_ref_audio_path 完全一致的行为。"""
    try:
        REF_WAV.encode("ascii")
        return REF_WAV
    except UnicodeEncodeError:
        pass
    ext = os.path.splitext(REF_WAV)[1] or ".wav"
    fd, tmp = tempfile.mkstemp(prefix="daniya_ref_", suffix=ext)
    os.close(fd)
    shutil.copyfile(REF_WAV, tmp)
    return tmp


def do_one(arm, worker_log_path):
    """arm: 'A_FIXED' | 'B_NATIVE'"""
    log("=" * 78)
    log("臂 %s  开始（worker 端口 %d）" % (arm, WK_PORT))
    log("=" * 78)

    ref_ascii = ascii_ref_path()
    ref_b64 = base64.b64encode(open(REF_WAV, "rb").read()).decode("ascii")
    log("参考音(达妮娅): %s  %d B  -> C++ fopen 用 ASCII 路径 %s"
        % (os.path.basename(REF_WAV), os.path.getsize(REF_WAV), ref_ascii))

    inp = load_mono_f32(INPUT_WAV, SR_IN)
    pk = float(np.max(np.abs(inp))) or 1.0
    inp = np.clip(inp * min(0.7 / pk, 4.0), -1.0, 1.0)

    wk_log = open(worker_log_path, "w", encoding="utf-8")
    wk = subprocess.Popen(
        [PY_EMBED, "worker.py", "--port", str(WK_PORT), "--gpu-id", "0", "--worker-index", "0"],
        cwd=SERVER_DIR, env=clean_env(), stdout=wk_log, stderr=subprocess.STDOUT)
    ok = wait_health("http://127.0.0.1:%d/health" % WK_PORT, timeout_s=600)
    log("worker health: %s" % ok)
    if not ok:
        wk.kill(); wk_log.close(); return None

    import websockets
    chunks, texts = [], []

    async def run():
        sid = "adx_%s_%d" % (arm.lower(), int(time.time()))
        url = "ws://127.0.0.1:%d/ws/duplex?session_id=%s" % (WK_PORT, sid)
        log("连接 %s" % url)
        async with websockets.connect(url, proxy=None, ssl=None,
                                      max_size=512 * 1024 * 1024, ping_interval=None,
                                      open_timeout=240) as ws:
            prepare = {
                "type": "prepare",
                "system_prompt": PERSONA,
                "config": dict(GEN_CFG),
            }
            if arm == "A_FIXED":
                # ↓↓↓ 与修复后 local_omni_duplex.py:1392-1411 逐字段对齐 ↓↓↓
                prepare["ref_audio_path"] = ref_ascii
                prepare["ref_audio_base64"] = ref_b64
                prepare["tts_ref_audio_base64"] = ref_b64
                prepare["system_content"] = [
                    {"type": "text", "text": CLONE_INSTR + "\n" + PERSONA},
                    {"type": "audio", "data": ref_b64},
                ]
            t0 = time.perf_counter()
            await ws.send(json.dumps(prepare))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=240))
                if m.get("type") == "prepared":
                    log("[prepared] %.1fs  prompt_length=%s"
                        % (time.perf_counter() - t0, m.get("prompt_length")))
                    break
                if m.get("type") == "error":
                    log("[prepare error] %s" % m); return

            stream = np.concatenate([inp, np.zeros(SR_IN * 12, dtype=np.float32)])
            step = int(SR_IN * 0.25)
            n_chunk = (len(stream) + step - 1) // step
            log("输入流 %.2fs -> %d chunks（同步乒乓，发一收一）"
                % (len(stream) / SR_IN, n_chunk))

            n_audio_only = 0
            n_inline = 0

            def take(m):
                """收集 result / audio_only 两条通道的音频。"""
                nonlocal n_audio_only, n_inline
                ad = m.get("audio_data")
                if not ad:
                    return
                if m.get("type") == "audio_only":
                    n_audio_only += 1
                else:
                    n_inline += 1
                chunks.append(np.frombuffer(base64.b64decode(ad), np.float32))

            for i in range(n_chunk):
                s = stream[i * step:(i + 1) * step]
                if len(s) < step:
                    s = np.pad(s, (0, step - len(s)))
                await ws.send(json.dumps({
                    "type": "audio_chunk",
                    "audio_base64": base64.b64encode(np.asarray(s, dtype="<f4").tobytes()).decode(),
                }))
                try:
                    got = False
                    while not got:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        m = json.loads(raw)
                        mt = m.get("type")
                        if mt == "result":
                            if not m.get("is_listen"):
                                take(m)
                            tx = (m.get("text") or "").strip()
                            if tx and (not texts or texts[-1] != tx):
                                texts.append(tx)
                                log("  [SPEAK %2d] %s" % (i, tx[:90]))
                            got = True
                        elif mt == "audio_only":
                            take(m)
                        elif mt == "error":
                            log("  [error] %s" % str(m)[:200]); got = True
                except asyncio.TimeoutError:
                    log("  [warn] chunk %d 等 result 超时" % i)

            log("全部 %d chunk 处理完毕，开始 drain（收尾未到的 audio_only）" % n_chunk)
            idle = 0
            while idle < 5:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    idle += 1
                    continue
                m = json.loads(raw)
                if m.get("type") == "audio_only":
                    take(m)
                    idle = 0
            log("音频通道计数：audio_only=%d  result内联=%d" % (n_audio_only, n_inline))
            try:
                await ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass

    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        asyncio.get_event_loop().run_until_complete(run())
    except Exception as e:
        log("[EXC] %r" % (e,))

    try:
        wk.terminate()
    except Exception:
        pass
    time.sleep(4)
    try:
        if wk.poll() is None:
            wk.kill()
    except Exception:
        pass
    wk_log.close()

    if not chunks:
        log("[NO AUDIO] 文本=%s" % (" | ".join(texts)[:300]))
        return None
    import soundfile as sf
    pcm = np.concatenate(chunks)
    out = os.path.join(OUTDIR, "%s%s.wav" % (arm, globals().get("OUT_SUFFIX", "")))
    sf.write(out, pcm, SR_OUT)
    log("[SAVED] %s  %.2fs" % (out, len(pcm) / SR_OUT))
    log("文本: %s" % " ".join(texts)[:400])
    return out


def main():
    global REF_WAV
    os.makedirs(OUTDIR, exist_ok=True)
    # 支持 --ref=<path> 与 --tag=<name>：便于同一合成台跑不同参考音/缓存
    for _a in sys.argv[1:]:
        if _a.startswith("--ref="):
            REF_WAV = _a.split("=", 1)[1]
            log("参考音覆盖为 %s" % REF_WAV)
        elif _a.startswith("--tag="):
            globals()["OUT_TAG"] = _a.split("=", 1)[1]
            log("输出 tag = %s" % globals()["OUT_TAG"])
    if not port_free(WK_PORT):
        log("!! 端口 %d 被占用，先释放" % WK_PORT); sys.exit(1)
    for p in (REF_WAV, INPUT_WAV, SIG_WAV, CACHE):
        if not os.path.exists(p):
            log("!! 缺失 %s" % p); sys.exit(1)

    log("live 声码器 cache : %d B  md5=%s" % (os.path.getsize(CACHE), md5(CACHE)[:12]))
    log("达妮娅参考音     : %s" % REF_WAV)
    log("原生签名音(回落) : %s" % SIG_WAV)
    log("")

    results = {}
    _want = [a for a in sys.argv[1:] if a and not a.startswith("--")]
    _arms = tuple(_want) if _want else ("A_FIXED", "B_NATIVE")
    log("本轮臂: %s" % (_arms,))
    for arm in _arms:
        wl = os.path.join(OUTDIR, "worker_%s_%s.log" % (arm, TS))
        _tag = globals().get("OUT_TAG")
        if _tag:
            globals()["OUT_SUFFIX"] = "_" + _tag
        elif len(_arms) == 1:
            globals()["OUT_SUFFIX"] = "_r2"
        else:
            globals()["OUT_SUFFIX"] = ""
        results[arm] = do_one(arm, wl)
        log("")

    import soundfile as sf
    seq = []
    if globals().get("OUT_TAG"):
        log("（带 --tag 的运行不覆盖通用试听文件）")
        log("=" * 78)
        for k, v in results.items():
            log("RESULT %-9s %s" % (k, v))
        log("=" * 78)
        return
    for arm in ("A_FIXED", "B_NATIVE"):
        f = os.path.join(OUTDIR, "%s.wav" % arm)
        if os.path.exists(f):
            x, sr = sf.read(f, dtype="float32")
            x = np.clip(x, -1.0, 1.0)
            x = x / max(1e-9, float(np.max(np.abs(x)))) * 0.6
            if seq:
                seq.append(np.zeros(int(sr * 1.5), dtype=np.float32))
            seq.append(x)
    if seq:
        out = os.path.join(OUTDIR, "试听_修复后_vs_修复前.wav")
        sf.write(out, np.concatenate(seq), 24000)
        log("连续试听文件: %s" % out)

    log("")
    log("=" * 78)
    for k, v in results.items():
        log("RESULT %-9s %s" % (k, v))
    log("=" * 78)


if __name__ == "__main__":
    main()
