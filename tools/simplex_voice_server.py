# -*- coding: utf-8 -*-
"""单工轮询语音服务（独立进程）。

与双工通话并列的第二种通话模式：ASR(本地 sherpa) + LLM(云端文本端口) + TTS(阿里云百炼)。
本服务把 src.voice.simplex_session 暴露给浏览器，便于独立实测，不依赖主程序(pywebview/Comni)。

运行（必须用 python-embed，已含 fastapi/uvicorn/httpx/websockets/sherpa_onnx/openai）：
  H:\\Comni\\_internal\\resources\\python-embed\\python.exe tools\\simplex_voice_server.py
  → 默认 http://127.0.0.1:9871

接口：
  GET  /                      单工通话 UI（src/ui/web/simplex_voice.html）
  GET  /api/health            配置探测（tts_ok / llm_ok / char_name）
  POST /api/turn  {"pcm": "<base64 16k int16 PCM>", "char_id": "<可选>"}
        -> {"user_text","reply_text","audio_b64","audio_ext","asr_ms","llm_ms","tts_ms",
            "total_ms","empty","reason","tts_used_stream","turn"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # G:/ai对话/11
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("simplex_voice_server")

UI_PATH = os.path.join(PROJECT_ROOT, "src", "ui", "web", "simplex_voice.html")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")

app = FastAPI(title="Simplex Voice (单工轮询)")

# 延迟构建：首个请求时才加载配置与会话，避免启动期依赖未就绪 / 配置缺失直接崩。
_session = None
_session_lock = threading.Lock()
_cfg = None


def _load_cfg():
    global _cfg
    if _cfg is None:
        from src.config import load_config

        _cfg = load_config(CONFIG_PATH)
    return _cfg


def get_session(char_id=None):
    global _session
    with _session_lock:
        if _session is None:
            cfg = _load_cfg()
            from src.voice.simplex_session import SimplexSession

            _session = SimplexSession(cfg, char_id=char_id)
    return _session


@app.get("/")
def index():
    if os.path.exists(UI_PATH):
        return FileResponse(UI_PATH)
    return Response("simplex UI 未找到: " + UI_PATH, status_code=404)


@app.get("/api/health")
def health():
    try:
        cfg = _load_cfg()
        tts_ok = False
        try:
            sess = get_session()
            tts_ok = bool(getattr(sess._tts, "name", "") == "dashscope_cosy"
                          and getattr(sess._tts, "_api_key", ""))
        except Exception as e:  # noqa: BLE001
            logger.warning("health 探测 TTS 失败: %s", e)
        char = cfg.get_active_character()
        return {
            "ok": True,
            "config_loaded": True,
            "tts_ok": tts_ok,
            "llm_ok": bool(cfg.ns.text_chat.providers if getattr(cfg.ns, "text_chat", None) else False),
            "char_name": getattr(char, "name", "") if char else "",
            "engine": "dashscope_cosy",
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@app.post("/api/turn")
async def turn(req: Request):
    try:
        data = await req.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    pcm_b64 = data.get("pcm")
    if not pcm_b64:
        return JSONResponse({"error": "缺少 pcm（16k int16 PCM 的 base64）"}, status_code=400)
    try:
        pcm = base64.b64decode(pcm_b64)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "pcm base64 解码失败"}, status_code=400)

    async def _empty_stream():
        yield _sse({"type": "empty", "reason": "audio_too_short"})

    if len(pcm) < 2000:  # 少于 ~60ms 音频，视为空
        return StreamingResponse(_empty_stream(), media_type="text/event-stream")

    char_id = data.get("char_id")
    try:
        sess = get_session(char_id)
        sess.reset_cancel()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"会话构建失败: {e}"}, status_code=500)

    async def _gen():
        try:
            async for ev in sess.process_turn_stream(pcm):
                yield _sse(ev)
        except Exception as e:  # noqa: BLE001
            logger.exception("单工流式处理异常")
            yield _sse({"type": "error", "error": str(e)})

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.post("/api/turn_cancel")
async def turn_cancel():
    """前端打断：置会话取消标志，流式管线据此中止 TTS 生成（已发送音频会继续播完）。"""
    try:
        get_session().cancel()
        return {"ok": True, "cancelled": True}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/reset")
async def reset():
    try:
        get_session().reset()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


if __name__ == "__main__":
    port = int(os.environ.get("SIMPLEX_PORT", "9871"))
    logger.info("Simplex Voice 服务启动: http://127.0.0.1:%s  (Ctrl+C 退出)", port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
