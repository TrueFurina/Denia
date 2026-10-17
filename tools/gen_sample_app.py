# -*- coding: utf-8 -*-
"""应用保真版合成台：修复"结巴"必须先把测试台改成与 app 同构。

与旧测试台（gen_timbre_sample.py）的三处关键差异 —— 全部照抄 local_omni_duplex.py：
  1. 【静音帧 force_listen】app: `_mic_voice = EMA(rms, α=0.35) > 0.018`；
     非人声帧 msg["force_listen"]=True（L1124 / L1584-1588）。
     旧台完全不发该字段 ⇒ 模型被允许对静音持续开口 ⇒ 自嗨成一串短感叹句，
     每次收尾都重启声码器（实测 audio_bos 12~40 次/回合）⇒ 结巴。
  2. 【定拍连续推流】app: 每 frame_interval_s(通话档 0.5s) 推一帧，不等待回复。
     旧台是"发一收一"乒乓 ⇒ 模型被喂成非实时的碎片节奏。
  3. 【帧长 0.5s】与 app 通话档一致（旧台 0.25s）。

另实测音频到达速率（音频秒/墙钟秒）：<1 说明 T2W 慢于实时，
在 app 里会导致播放欠载（另一种"卡顿"来源）。
"""
import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import numpy as np

PY_EMBED = r"H:\Comni\_internal\resources\python-embed\python.exe"
SERVER_DIR = r"H:\Comni\_internal\resources\apps\server"
FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
WK_PORT = 22734

MODEL_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
CACHE = os.path.join(MODEL_DIR, "token2wav-gguf", "prompt_cache.gguf")

REF_WAV = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
INPUT_WAV = r"G:\ai对话\11\data\native_audio_test\INPUT_sapi_neutral.wav"
OUTDIR = r"G:\ai对话\11\data\native_audio_test\final_sample"
SR_IN, SR_OUT = 16000, 24000

FRAME_S = 0.5          # app 通话档 frame_interval_s
TAIL_S = 10.0          # 喂给模型的尾部静音（会被 force_listen 掐掉）
EMA_ALPHA = 0.35       # app: _mic_rms_smooth 的 α
VOICE_THR = 0.018      # app: _mic_voice 阈值

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

TS = time.strftime("%Y%m%d_%H%M%S")


def log(m):
    print("[app-ct] " + m, flush=True)


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
    try:
        REF_WAV.encode("ascii")
        return REF_WAV
    except UnicodeEncodeError:
        pass
    ext = os.path.splitext(REF_WAV)[1] or ".wav"
    fd, tmp = tempfile.mkstemp(prefix="daniya_ref_", suffix=ext)
    os.close(fd)
    import shutil
    shutil.copyfile(REF_WAV, tmp)
    return tmp


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "APP_A"
    os.makedirs(OUTDIR, exist_ok=True)
    if not port_free(WK_PORT):
        log("端口 %d 被占用，退出" % WK_PORT); return

    wl = os.path.join(OUTDIR, "worker_%s_%s.log" % (tag, TS))
    wk_log = open(wl, "w", encoding="utf-8")
    wk = subprocess.Popen(
        [PY_EMBED, "worker.py", "--port", str(WK_PORT), "--gpu-id", "0", "--worker-index", "0"],
        cwd=SERVER_DIR, env=clean_env(), stdout=wk_log, stderr=subprocess.STDOUT)
    log("worker 启动 pid=%d 日志=%s" % (wk.pid, os.path.basename(wl)))

    ok = wait_health("http://127.0.0.1:%d/health" % WK_PORT, timeout_s=600)
    log("worker health: %s" % ok)
    if not ok:
        wk.kill(); wk_log.close(); return

    ref_ascii = ascii_ref_path()
    ref_b64 = base64.b64encode(open(REF_WAV, "rb").read()).decode("ascii")
    log("参考音 %s (%d B) -> %s" % (os.path.basename(REF_WAV), os.path.getsize(REF_WAV), ref_ascii))

    inp = load_mono_f32(INPUT_WAV, SR_IN)
    pk = float(np.max(np.abs(inp))) or 1.0
    inp = np.clip(inp * min(0.7 / pk, 4.0), -1.0, 1.0)
    rms_in = float(np.sqrt((inp.astype(np.float64) ** 2).mean()))
    log("输入 %.2fs  rms=%.4f(%.1f dBFS)  pk=%.3f → 定拍 %.2fs/帧"
        % (len(inp) / SR_IN, rms_in, 20 * np.log10(rms_in + 1e-12), float(np.abs(inp).max()), FRAME_S))

    step = int(SR_IN * FRAME_S)
    stream = np.concatenate([inp, np.zeros(int(SR_IN * TAIL_S), dtype=np.float32)])
    n_chunk = (len(stream) + step - 1) // step
    log("总帧数 %d（含尾部静音 %.0fs）" % (n_chunk, TAIL_S))

    import websockets
    audio_chunks = []      # (arrival_wall_s, np.float32 array)
    texts = []
    t_start = [0.0]

    async def run():
        sid = "adx_%s_%d" % (tag.lower(), int(time.time()))
        url = "ws://127.0.0.1:%d/ws/duplex?session_id=%s" % (WK_PORT, sid)
        async with websockets.connect(url, proxy=None, ssl=None,
                                      max_size=512 * 1024 * 1024, ping_interval=None,
                                      open_timeout=240) as ws:
            prepare = {
                "type": "prepare",
                "system_prompt": PERSONA,
                "config": dict(GEN_CFG),
                "ref_audio_path": ref_ascii,
                "ref_audio_base64": ref_b64,
                "tts_ref_audio_base64": ref_b64,
                "system_content": [
                    {"type": "text", "text": CLONE_INSTR + "\n" + PERSONA},
                    {"type": "audio", "data": ref_b64},
                ],
            }
            t0 = time.perf_counter()
            await ws.send(json.dumps(prepare))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=240))
                if m.get("type") == "prepared":
                    log("[prepared] %.1fs prompt_length=%s" % (time.perf_counter() - t0, m.get("prompt_length")))
                    break
                if m.get("type") == "error":
                    log("[prepare error] %s" % m); return

            stop = asyncio.Event()
            n_ao = [0]
            n_res = [0]
            fl_hist = []

            async def consumer():
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        return
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    mt = m.get("type")
                    if mt == "audio_only":
                        ad = m.get("audio_data")
                        if ad:
                            n_ao[0] += 1
                            audio_chunks.append((time.perf_counter() - t_start[0],
                                                 np.frombuffer(base64.b64decode(ad), np.float32)))
                    elif mt == "result":
                        n_res[0] += 1
                        tx = (m.get("text") or "").strip()
                        if tx and (not texts or texts[-1] != tx):
                            texts.append(tx)
                            log("  [SPEAK] %s" % tx[:100])

            ct = asyncio.create_task(consumer())

            # ---- 定拍推流（与 app 同构）----
            smooth = 0.0
            t_start[0] = time.perf_counter()
            n_fl = 0
            for i in range(n_chunk):
                s = stream[i * step:(i + 1) * step]
                if len(s) < step:
                    s = np.pad(s, (0, step - len(s)))
                rms = float(np.sqrt((s.astype(np.float64) ** 2).mean()))
                smooth = smooth * (1 - EMA_ALPHA) + rms * EMA_ALPHA
                fl = not (smooth > VOICE_THR)
                if fl:
                    n_fl += 1
                if i < 40 or i % 10 == 0:
                    fl_hist.append((i, round(rms, 4), round(smooth, 5), fl))
                await ws.send(json.dumps({
                    "type": "audio_chunk",
                    "audio_base64": base64.b64encode(np.asarray(s, dtype="<f4").tobytes()).decode(),
                    "force_listen": bool(fl),
                }))
                await asyncio.sleep(FRAME_S)
            log("推流完毕：%d 帧，其中 force_listen=%d 帧（%.0f%%）"
                % (n_chunk, n_fl, 100 * n_fl / max(1, n_chunk)))
            log("force_listen 判定轨迹(i,rms,smooth,fl): %s" % fl_hist[:16])

            await asyncio.sleep(4.0)   # 收尾：让 T2W 把余下音频吐完
            stop.set()
            await asyncio.sleep(0.3)
            ct.cancel()
            log("音频通道计数: audio_only=%d  result=%d" % (n_ao[0], n_res[0]))
            try:
                await ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass

    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        asyncio.get_event_loop().run_until_complete(run())
    except Exception as e:
        log("[EXC] %r" % (e,))
    finally:
        try:
            wk.terminate(); wk.wait(timeout=20)
        except Exception:
            try:
                wk.kill()
            except Exception:
                pass
        wk_log.close()

    if not audio_chunks:
        log("无音频产出")
        return
    segs = np.concatenate([a for _, a in audio_chunks])
    out = os.path.join(OUTDIR, tag + ".wav")
    import soundfile as sf
    sf.write(out, segs, SR_OUT, subtype="PCM_16")

    dur = len(segs) / SR_OUT
    span = audio_chunks[-1][0] - audio_chunks[0][0] if len(audio_chunks) > 1 else 0
    log("=" * 78)
    log("产出 %s  %.2fs  块数=%d  RMS=%.1f dBFS" %
        (os.path.basename(out), dur, len(audio_chunks),
         20 * np.log10(np.sqrt((segs ** 2).mean()) + 1e-12)))
    log("音频到达：首块 %.2fs 起，末块 %.2fs，跨度 %.2fs" %
        (audio_chunks[0][0], audio_chunks[-1][0], span))
    if span > 0:
        log("到达速率 = %.2f 音频秒/墙钟秒（<1 表示慢于实时→app 播放会欠载）" % (dur / span))
    log("块时长分布: %s" % [round(len(a) / SR_OUT, 3) for _, a in audio_chunks][:30])
    log("文本拼接: %s" % "".join(texts)[:300])
    log("=" * 78)


if __name__ == "__main__":
    main()
