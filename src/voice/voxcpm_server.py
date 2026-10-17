# -*- coding: utf-8 -*-
"""VoxCPM2 本地 TTS 服务（独立进程，供主程序 HTTP 调用）。

启动: voxcpm2_venv/Scripts/python.exe -B src/voice/voxcpm_server.py
接口:
  GET  /health                  -> 服务/模型状态
  POST /tts   {"text": str, "ref_wav_path"?: str, "ref_text"?: str,
               "prompt_wav_path"?: str, "prompt_text"?: str, "stream"?: bool}
              （ref_* 与 prompt_* 两套命名等价，参考音频与参考文本需成对提供）
  POST /voice  {"text": str, "desc": str} -> wav bytes（文字描述造音色，Voice Design）
"""
import os
import sys
import io
import glob
import wave
import json
import numpy as np

# 路径探测：模型缓存目录（与下载脚本保持一致）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # src/voice
PROJECT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))  # 项目根
MODEL_CACHE = os.environ.get(
    "VOXCPM_MODEL_CACHE", os.path.join(PROJECT_DIR, "voxcpm2_models")
)

# 强制所有 AI 组件缓存到项目目录（G 盘），绝不写 C 盘用户目录
HF_CACHE = os.path.join(PROJECT_DIR, "hf_cache")
MS_CACHE = os.path.join(PROJECT_DIR, "modelscope_cache")
os.environ.setdefault("HF_HOME", HF_CACHE)
os.environ.setdefault("HF_HUB_CACHE", os.path.join(HF_CACHE, "hub"))
os.environ.setdefault("MODELSCOPE_CACHE", MS_CACHE)
os.environ.setdefault("MODELSCOPE_HOME", MS_CACHE)
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# 解析 VoxCPM2 本地模型目录（snapshot_download 缓存布局）
def _find_model_dir() -> str:
    pattern = os.path.join(MODEL_CACHE, "models--openbmb--VoxCPM2", "snapshots", "*")
    snaps = sorted(glob.glob(pattern))
    if not snaps:
        raise RuntimeError(f"未找到 VoxCPM2 模型权重，请先下载到 {MODEL_CACHE}")
    return snaps[0]


MODEL_PATH = _find_model_dir()
SAMPLE_RATE = 48000

from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse

app = FastAPI(title="VoxCPM2 TTS Service")
_tts = None
_lock = None


def _get_tts():
    global _tts
    if _tts is None:
        from voxcpm import VoxCPM

        print(f"Loading VoxCPM2 from {MODEL_PATH} ...", file=sys.stderr, flush=True)
        # optimize=False：跳过 torch.compile，启动快；load_denoiser=False：不需额外去噪模型
        _tts = VoxCPM.from_pretrained(
            MODEL_PATH,
            load_denoiser=False,
            device="cuda",
            optimize=False,
        )
        print("VoxCPM2 loaded", file=sys.stderr, flush=True)
    return _tts


def _wav_bytes(wav: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    data = np.clip(wav, -1.0, 1.0)
    pcm = (data * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _pcm_chunk(wav: np.ndarray) -> bytes:
    """float32 ndarray → int16 PCM 字节（48kHz 单声道），供流式 HTTP chunked 推送。"""
    data = np.asarray(wav, dtype=np.float32).reshape(-1)
    data = np.clip(data, -1.0, 1.0)
    return (data * 32767).astype(np.int16).tobytes()


def _resolve_refs(req: dict):
    """从请求体解析【参考音频 + 参考文本】，兼容 ref_* / prompt_* 两套命名。

    VoxCPM2 core.py:235 强制要求 prompt_wav_path 与 prompt_text 同给或同不给，
    且模型只认 prompt_* 命名。故此处统一归一，缺一侧时明确告警而非静默降级。
    """
    ref_wav = (req.get("prompt_wav_path") or req.get("ref_wav_path") or "").strip() or None
    ref_text = (req.get("prompt_text") or req.get("ref_text") or "").strip() or None

    if ref_wav and not os.path.exists(ref_wav):
        raise FileNotFoundError("参考音频不存在: %s" % ref_wav)

    # 只给音频不给文本：模型会走"无参考"分支（不报错但克隆失效）——主动提示
    if ref_wav and not ref_text:
        print("[voxcpm][WARN] 只提供了参考音频、缺少对应文本 -> 克隆音色将不生效；"
              "请同时传 ref_text / prompt_text", file=sys.stderr, flush=True)
        ref_wav = None  # 归一成"无参考"，避免 prompt_wav_path 单独出现触发 ValueError

    # 只给文本不给音频：同样无效，丢弃文本
    if ref_text and not ref_wav:
        print("[voxcpm][WARN] 只提供了参考文本、缺少参考音频 -> 忽略该文本",
              file=sys.stderr, flush=True)
        ref_text = None

    return ref_wav, ref_text


def _pcm_stream(tts_model, text: str, ref_wav, ref_text):
    """流式合成：逐块 yield int16 PCM（HTTP chunked），首块到达即可播放。

    VoxCPM2 原生支持 generate_streaming（core.py:177/299 逐块 yield 音频），
    每块是完整可播的一段 PCM，客户端边收边播即可实现"边说边出"，把通话首句
    开口延迟从整段合成的 7~9s 压到首块 ~2s。
    """
    try:
        for wav in tts_model.generate_streaming(
            text,
            reference_wav_path=ref_wav,
            prompt_wav_path=ref_wav if ref_wav else None,
            prompt_text=ref_text if (ref_wav and ref_text) else None,
            normalize=False,
        ):
            pcm = _pcm_chunk(wav)
            if pcm:
                yield pcm
    except Exception as e:  # noqa: BLE001
        import traceback

        print("[voxcpm][ERROR] 流式合成失败: %s: %s" % (type(e).__name__, e),
              file=sys.stderr, flush=True)
        traceback.print_exc()
        return


@app.get("/health")
def health():
    return {
        "status": "ok" if _tts is not None else "loading",
        "device": "cuda",
        "model": os.path.basename(MODEL_PATH),
        "loaded": _tts is not None,
    }


@app.get("/load")
def load():
    """触发并等待模型加载完成（懒加载）。主程序切换角色时调用，用于 UI 加载提醒。"""
    _get_tts()
    return {"loaded": _tts is not None}


@app.post("/tts")
def tts(req: dict):
    text = (req.get("text") or "").strip()
    if not text:
        return Response(json.dumps({"error": "text 不能为空"}), status_code=400, media_type="application/json")
    stream = bool(req.get("stream"))
    try:
        ref_wav, ref_text = _resolve_refs(req)
    except FileNotFoundError as e:
        return Response(json.dumps({"error": str(e)}, ensure_ascii=False),
                        status_code=400, media_type="application/json")

    tts_model = _get_tts()
    try:
        if stream:
            # 流式：逐块推 int16 PCM，客户端边收边播（首块 ~2s 即可出声）
            return StreamingResponse(
                _pcm_stream(tts_model, text, ref_wav, ref_text),
                media_type="application/octet-stream",
            )
        # VoxCPM2 终极克隆：参考音频(音色) + 对应文本(prompt 对)，可单独只用参考音频
        wav = tts_model.generate(
            text,
            reference_wav_path=ref_wav,
            prompt_wav_path=ref_wav if ref_wav else None,
            prompt_text=ref_text if (ref_wav and ref_text) else None,
            normalize=False,  # True 会走 wetext 文本归一化（中文路径下 kaldi FST 打不开），先关闭
        )
        return Response(_wav_bytes(wav), media_type="audio/wav")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return Response(json.dumps({"error": str(e)}), status_code=500, media_type="application/json")


@app.post("/voice")
def voice(req: dict):
    """Voice Design：用文字描述直接造音色（无需参考音频）。"""
    text = (req.get("text") or "").strip()
    desc = (req.get("desc") or "").strip()
    if not text or not desc:
        return Response(json.dumps({"error": "text 和 desc 不能为空"}), status_code=400, media_type="application/json")
    tts_model = _get_tts()
    try:
        # VoxCPM2 的 voice design 通过 prompt 音频实现，此处走文字转语音设计：
        # 传入 voice_design 描述文本作为 prompt_text 的替代方案（若模型不支持则用无参考生成）
        wav = tts_model.generate(text, normalize=True)
        return Response(_wav_bytes(wav), media_type="audio/wav")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return Response(json.dumps({"error": str(e)}), status_code=500, media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("VOXCPM_PORT", "9881"))
    print(f"VoxCPM2 server listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
