# -*- coding: utf-8 -*-
"""Qwen3-TTS 本地语音克隆服务（独立进程，供主程序 HTTP 调用）。

启动: qwen3_tts_venv/Scripts/python.exe -B src/voice/qwen3tts_server.py
接口:
  GET  /health                   -> 服务/模型状态
  POST /tts  {"text": str, "ref_wav_path": str, "ref_text"?: str, "language"?: str}
                                 -> wav bytes（零样本克隆：参考音频 + 对应文本）
"""
import base64
import glob
import io
import json
import os
import sys
import wave

import numpy as np
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # src/voice
PROJECT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))  # 项目根
MODEL_CACHE = os.environ.get(
    "QWEN3_MODEL_CACHE", os.path.join(PROJECT_DIR, "qwen3_tts_models")
)
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

# 强制所有 AI 组件缓存到项目目录（G 盘），绝不写 C 盘用户目录；HF 下载走国内镜像
HF_CACHE = os.path.join(PROJECT_DIR, "hf_cache")
os.environ.setdefault("HF_HOME", HF_CACHE)
os.environ.setdefault("HF_HUB_CACHE", os.path.join(HF_CACHE, "hub"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _find_model_dir() -> str | None:
    """解析 Qwen3-TTS 本地模型目录（兼容 HF 与 ModelScope 两种缓存布局）。"""
    for pattern in (
        # ModelScope: cache/models/Qwen--xxx/snapshots/*/
        os.path.join(MODEL_CACHE, "models", "Qwen--Qwen3-TTS-12Hz-0.6B-Base", "snapshots", "*"),
        # HuggingFace: cache/models--Qwen--xxx/snapshots/*/
        os.path.join(MODEL_CACHE, "models--Qwen--Qwen3-TTS-12Hz-0.6B-Base", "snapshots", "*"),
    ):
        snaps = sorted(glob.glob(pattern))
        if snaps:
            return snaps[0]
    return None


MODEL_PATH = _find_model_dir()

from ref_audio_util import ensure_usable_wav  # noqa: E402（sys.path[0] 为脚本目录）

from fastapi import FastAPI
from fastapi.responses import Response

app = FastAPI(title="Qwen3-TTS Voice Clone Service")

# 【2026-09-10 决定性修复】模型加载互斥锁。
# 主程序两个挂载调用方（切换引擎 + 引导预载）会**并发 POST /load 到同一个本进程**；
# 若无锁，两个请求会同时执行 from_pretrained 加载同一模型，互相把对方搞成
# **meta 空壳** → 878 个子模块在 meta 设备、报「权重文件损坏或不完整」的假象，
# 模型永远加载不出（试听无声/失败）。决定性实证：
#   单个 /load      → 必成功（loaded:true, device:cuda:0, meta=0）
#   并发两个 /load  → 必失败（878 个子模块在 meta 设备，自愈重试 3 次全败）
# 加锁后：先到者真正加载，后到者等锁释放后直接复用已加载模型（不再并发加载）。
try:
    import threading as _threading
    _LOAD_LOCK = _threading.Lock()
except Exception:  # noqa: BLE001
    _LOAD_LOCK = None

_tts = None
# 记录模型【最近一次实际加载】的设备，供 /tts（不指定设备）复用，
# 避免预览临时 CUDA 加载后，合成端点又因 device=None 默认回退 CPU、
# 把刚上卡的模型卸掉重跑在 CPU（表现：显卡没跑、CPU 骤增、还慢）。
_loaded_device = None

# ---------- 参考音频 / 克隆 prompt 缓存（提速关键） ----------
# 达娅参考音 + ref_text 都固定不变，但每次 /tts 都重跑：
#   1) ensure_usable_wav  -> ffmpeg 转码成 16k 单声道 wav
#   2) create_voice_clone_prompt -> Mimi encode + CAM++ 说话人嵌入提取（约 4s 固定开销）
# 这些与请求文本无关，缓存后省掉固定开销：快照 TTS 从 ~0.4x 实时提升至接近实时。
# 参考音频文件常驻（不删除），仅在文件 mtime/size 变化时失效重转。
_usable_wav_cache: dict = {}   # (ref_wav_path, mtime, size) -> 转码后 wav 路径
_ref_prompt_cache: dict = {}   # (ref_wav_identity, ref_text, x_vec) -> list[VoiceClonePromptItem]


def _cached_usable_wav(ref_wav: str) -> str:
    """转码后的 wav 路径缓存：同一条参考音频只转码一次。"""
    try:
        st = os.stat(ref_wav)
        identity = (ref_wav, st.st_mtime, st.st_size)
    except OSError:
        identity = (ref_wav, 0, 0)
    cached = _usable_wav_cache.get(identity)
    if cached and os.path.exists(cached):
        return cached
    converted = ensure_usable_wav(ref_wav)  # 无效/损坏直接抛错（不缓存）
    _usable_wav_cache[identity] = converted
    return converted  # 常驻，不在此删除


def _cached_ref_prompt(tts_model, identity: tuple, converted: str, ref_text: str, x_vec: bool):
    """克隆 prompt 缓存：参考音特征只提取一次，之后每次合成直接复用。

    必须用 list[VoiceClonePromptItem] 形式（不能用 dict 形式），否则 generate 会走
    voice_clone_prompt_dict 分支把 ref_texts_for_ids 置 None，丢失 ICL 文本条件导致音色漂移。
    """
    key = (identity, ref_text, x_vec)
    cached = _ref_prompt_cache.get(key)
    if cached is None:
        cached = tts_model.create_voice_clone_prompt(
            ref_audio=converted, ref_text=ref_text, x_vector_only_mode=x_vec)
        _ref_prompt_cache[key] = cached
    return cached

# 允许本地引导页（file:// / 127.0.0.1）跨域调用本服务，避免浏览器 CORS 拦截
# （尤其 /mimo_clone 代理小米 MiMo 云端克隆——浏览器直连 api.xiaomimimo.com 会被 CORS 挡住）
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_device(device):
    """解析目标设备：cuda 需要真实可用显卡，否则回退 cpu；未指定则看 FORCE_CUDA 环境变量。"""
    import torch
    if device == "cuda":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        return "cpu"
    # 未指定：沿用 QWEN3_TTS_FORCE_CUDA 环境变量（默认不设置 → CPU，不争双工显存）
    if os.environ.get("QWEN3_TTS_FORCE_CUDA") == "1" and torch.cuda.is_available():
        return "cuda:0"
    return "cpu"


def _move_all_to_device(tts, target):
    """把整个 TTS 模型（含 speech_tokenizer 这类【非 nn.Module 包装】）搬到目标设备。

    关键坑（本次 /tts 合成 500 unsupported scalarType 的真因）：
    self.model.speech_tokenizer 是 Qwen3TTSTokenizer —— 普通 Python 包装类（不是 nn.Module），
    内部持有真正的 transformers Mimi（nn.Module）做 encode/decode。nn.Module.to() 只递归
    nn.Module 子模块，会【跳过这个包装】→ 包装里的 Mimi 永远留在 from_pretrained 的初始设备
    （CPU，或在显存压力下偶发 meta）。合成第一步 create_voice_clone_prompt → Mimi.encode 里
    torch.autocast(device_type="meta"/"cpu") 与模型所在 CUDA 不匹配 → 直接炸
    RuntimeError: unsupported scalarType（或 device mismatch）。
    所以必须显式把 st.model 搬到 target，并设 st.device=target（encode 用 self.device 放输入张量）。
    self.speaker_encoder 是真实 nn.Module 子模块，.to 已覆盖，无需单独处理。
    """
    import torch
    tts.model = tts.model.to(target).eval()
    st = getattr(tts.model, "speech_tokenizer", None)
    if st is not None:
        inner = getattr(st, "model", None)
        if inner is not None and hasattr(inner, "to"):
            try:
                st.model = inner.to(target)
            except Exception:  # noqa: BLE001
                pass
        try:
            st.device = torch.device(target)
        except Exception:  # noqa: BLE001
            pass
    tts.device = torch.device(target)


def _count_meta(tts) -> int:
    """统计残留在 meta 设备的参数（含 speech_tokenizer 内部 Mimi，避免漏检导致合成炸）。"""
    n = 0
    try:
        n += sum(1 for _ in tts.model.parameters() if _.device.type == "meta")
    except Exception:  # noqa: BLE001
        pass
    st = getattr(tts.model, "speech_tokenizer", None)
    inner = getattr(st, "model", None) if st is not None else None
    if inner is not None:
        try:
            n += sum(1 for _ in inner.parameters() if _.device.type == "meta")
        except Exception:  # noqa: BLE001
            pass
    return n


def _clear_voice_caches():
    """重载/设备切换时清空克隆 prompt 与转码缓存，避免不同设备/模型的 prompt 张量被跨模型复用。"""
    try:
        _ref_prompt_cache.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        _usable_wav_cache.clear()
    except Exception:  # noqa: BLE001
        pass


def _get_tts(device=None):
    """获取/加载模型（**加锁串行化**，杜绝并发 from_pretrained 造成 meta 空壳）。

    所有调用方（/load、/tts 等）都走本包装：
    - 无锁可用（threading 导入失败）时退化为直接调用，行为同前；
    - 有锁时同一时刻只允许一个线程执行加载，后到者等锁释放后直接复用已加载模型。
    """
    if _LOAD_LOCK is None:
        return _get_tts_unsafe(device)
    with _LOAD_LOCK:
        return _get_tts_unsafe(device)


def _get_tts_unsafe(device=None):
    global _tts, _loaded_device
    import torch
    # 复用已加载设备：/tts 等不指定 device 时，直接沿用当前模型所在设备，
    # 不要因为 _resolve_device(None) 默认回退 CPU 而把刚上卡的模型卸掉重跑。
    if device is None:
        target = _loaded_device if (_tts is not None and _loaded_device) else _resolve_device(None)
    else:
        target = _resolve_device(device)
    # 已加载但目标设备不同 → 先卸载（释放旧设备显存），再按新设备重挂
    # （支持「预览时临时 CUDA、试完卸载、下次再按需加载」的设备热切换）
    if _tts is not None:
        cur = str(getattr(_tts, "device", "cpu"))
        if cur != target:
            print(f"Qwen3-TTS 设备切换 {cur} -> {target}，先卸载旧模型",
                  file=sys.stderr, flush=True)
            _tts = None
            # 设备切换 = 模型重建，必须清空克隆 prompt 缓存，否则旧设备张量被跨模型复用
            _clear_voice_caches()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    if _tts is None:
        from qwen_tts import Qwen3TTSModel

        # 本地缓存缺失则按 MODEL_ID 从 HF（hf-mirror 镜像）拉取到 HF_HOME
        path = MODEL_PATH or MODEL_ID
        # 加载策略（2026-09-10 修正 v2）：
        # ① 绝不用 device_map：device_map="cuda:0" 会把 speech_tokenizer(Mimi 语音分词器)
        #   这个独立子模型漏在 meta 设备，合成时 transformers 的 Mimi.forward 里
        #   torch.autocast(device_type="meta", ...) 报 RuntimeError: unsupported scalarType
        #   （日志见 logs/qwen3tts_server.log，本次 /tts 合成 500 的真因）。
        # ② 统一先 CPU 标准载入（已实测 meta_submodules=0，权重全落 CPU），
        #   再整体 .to(目标设备)：.to 会把 model 整棵（含 speech_tokenizer）搬到目标设备，
        #   从根本上消除 meta 残留，也比 device_map 可靠。
        # ③ dtype 必须用 bf16（float32 + 搬到 CUDA 在本机加载阶段硬崩，已实测）。
        # ④ 目标设备：device 参数优先（预览传 cuda）；否则看 QWEN3_TTS_FORCE_CUDA 环境变量，
        #   都没设 → CPU（不争双工显存）。cuda 搬运中途 OOM/失败自动回退 CPU（可出声）。
        #
        # 【2026-09-10 自愈·终版】权重偶发污染（重载/热切换瞬间全局状态异常，表现为
        # 部分子模块残留在 meta 设备）时，必须清空全局 _tts 引用 + gc + 释放显存后重试
        # from_pretrained（隔离测试连续 3 载重载 meta 恒为 0，刷新一次即恢复）。
        # ⚠️ 关键红线：任何失败路径都要把 _tts 置回 None——绝不能留下「loaded:true,
        # device:meta」的毒状态，否则 /health 永远谎报已加载、且每次 /load 都进
        # “device 不同→先卸→重载→又 meta→又 raise”的死循环，前端既收不到失败也出不了声。
        import gc as _gc
        last_err = None
        for _attempt in range(3):
            try:
                print(f"Loading Qwen3-TTS from {path} ... (attempt {_attempt + 1})",
                      file=sys.stderr, flush=True)
                _tts = Qwen3TTSModel.from_pretrained(
                    path, dtype=torch.bfloat16, attn_implementation="sdpa")
                # 防御：权重文件损坏/不完整时，from_pretrained 会把模型建成 meta 空壳
                # （部分子模块留在 meta 设备、无权重数据），之后 .to(target) 会抛难懂的
                # "Cannot copy out of meta tensor; no data!"。这里提前检测并触发自愈重试。
                # ⚠️ 必须连 speech_tokenizer 内部 Mimi 一起扫：它不在 model.parameters() 递归里，
                # 漏检会让模型谎报已加载、合成时才炸 unsupported scalarType。
                _meta = _count_meta(_tts)
                if _meta > 0:
                    raise RuntimeError(
                        f"权重文件损坏或不完整（{_meta} 个子模块在 meta 设备、无权重数据）")
                # 清空克隆 prompt / 转码缓存（避免不同设备/重载的 prompt 张量被跨模型复用 → 设备不匹配）
                _clear_voice_caches()
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                _tts = None
                _gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                print(f"Qwen3-TTS 载入尝试 {_attempt + 1} 失败: {e}",
                      file=sys.stderr, flush=True)
        else:
            raise RuntimeError(
                f"Qwen3-TTS 模型多次载入失败：{last_err}。"
                f"请检查模型缓存目录 qwen3_tts_models 下是否存在 .incomplete 占位文件或缺失权重，"
                f"清理损坏布局后重启；若仍失败删除 qwen3_tts_models 重新下载。")
        try:
            # 关键：_move_all_to_device 会连 speech_tokenizer 内部 Mimi 一起搬到 target，
            # 否则 Mimi 残留在初始设备 → 合成第一步 encode 时 autocast(device_type=meta/cpu) 炸。
            _move_all_to_device(_tts, target)
            _loaded_device = target
            print(f"Qwen3-TTS loaded on {target}", file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            if target != "cpu":
                print(f"搬到 {target} 失败 ({e})，回退 CPU 加载",
                      file=sys.stderr, flush=True)
                _move_all_to_device(_tts, "cpu")
                _loaded_device = "cpu"
                print("Qwen3-TTS loaded on cpu (fallback)", file=sys.stderr, flush=True)
            else:
                _tts = None
                _loaded_device = None
                raise
    return _tts


def _wav_bytes(wav: np.ndarray, sr: int) -> bytes:
    data = np.clip(np.asarray(wav, dtype=np.float32), -1.0, 1.0)
    pcm = (data * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _err(msg: str, code: int) -> Response:
    return Response(json.dumps({"error": msg}, ensure_ascii=False), status_code=code,
                    media_type="application/json")


@app.get("/health")
def health():
    return {
        "status": "ok" if _tts is not None else "loading",
        "model": MODEL_ID,
        "loaded": _tts is not None,
        "device": str(_tts.device) if _tts is not None else None,
    }


@app.get("/load")
def load_get():
    """触发并等待模型加载完成（默认设备：环境变量/CPU）。"""
    return _do_load(None)


@app.post("/load")
def load_post(req: dict = None):
    """触发加载并指定目标设备：body {"device": "cuda"|"cpu"}。

    用于「按需 CUDA」：预览试听时传 device=cuda 临时上卡提速，试完调 /unload 释放。
    """
    device = None
    if isinstance(req, dict):
        device = req.get("device")
    return _do_load(device)


def _do_load(device):
    try:
        _get_tts(device)
    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        print(f"Qwen3-TTS 模型加载异常: {e}\n{tb}", file=sys.stderr, flush=True)
        return Response(
            json.dumps({"error": f"{e}", "traceback": tb}, ensure_ascii=False),
            status_code=500,
            media_type="application/json",
        )
    return {
        "loaded": _tts is not None,
        "device": str(_tts.device) if _tts is not None else None,
    }


@app.post("/unload")
def unload():
    """卸载模型释放显存（按需 CUDA：预览试听结束后调用，避免常驻占卡与双工碰撞）。

    仅置空全局模型引用并清空 CUDA 缓存；服务进程保持存活，下次 /load 可立即按需重挂。
    """
    global _tts, _loaded_device
    _tts = None
    _loaded_device = None
    _clear_voice_caches()
    _loaded_device = None
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return {"unloaded": True, "loaded": False}


@app.post("/tts")
def tts(req: dict):
    text = (req.get("text") or "").strip()
    if not text:
        return _err("text 不能为空", 400)
    ref_wav = req.get("ref_wav_path") or None
    ref_text = req.get("ref_text") or None
    if not ref_wav or not os.path.exists(ref_wav):
        return _err("Qwen3-TTS 克隆需要参考音频 ref_wav_path", 400)

    converted = None
    try:
        # ① 转码缓存：同一条参考音频只 ffmpeg 转码一次
        converted = _cached_usable_wav(ref_wav)
        tts_model = _get_tts()
        import torch

        # ② 克隆 prompt 缓存：参考音特征（Mimi encode + CAM++ 嵌入）只提取一次，
        #    之后每次合成直接复用，跳过约 4s 固定开销。
        try:
            st = os.stat(ref_wav)
            identity = (ref_wav, st.st_mtime, st.st_size)
        except OSError:
            identity = (ref_wav, 0, 0)
        prompt_items = _cached_ref_prompt(tts_model, identity, converted, ref_text or "", False)

        # 用采样但固定 seed：全贪心（do_sample=False）在复杂参考音频（游戏提取
        # 语音等）上会陷入重复 token 循环导致合成卡死；随机采样能跳出该循环。
        # 固定 torch seed 让同一句话每次合成结果一致。temperature 保持默认 0.9：
        # 实测调低温度（0.5）会让克隆音色整体偏离参考音频，默认值最贴近原声。
        torch.manual_seed(1234)
        torch.cuda.manual_seed_all(1234)
        wavs, sr = tts_model.generate_voice_clone(
            text=text,
            language=req.get("language") or "Chinese",
            voice_clone_prompt=prompt_items,  # 复用缓存的克隆 prompt，跳过特征提取
            do_sample=True,
            subtalker_dosample=False,
        )
        if isinstance(wavs, (list, tuple)):
            if not wavs:
                return _err("Qwen3-TTS 返回空音频", 500)
            wavs = wavs[0]
        return Response(_wav_bytes(wavs, int(sr)), media_type="audio/wav")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return _err(str(e), 500)
    # 注：converted 为缓存常驻文件，不在此删除（下次请求复用）。


# ===== 小米 MiMo-V2.5-TTS 语音克隆代理 =====
# 作用：引导页 HTML 在浏览器里直连 api.xiaomimimo.com 会被 CORS 拦截，且会把用户
# API Key 暴露在页面请求里；此路由在本机 9882 中转，绕过 CORS、Key 只经本机内存，
# 不上传第三方。本地中转属个人学习使用，非公网对外服务，符合 MiMo 免费层条款。
MIMO_ENDPOINT = "https://api.xiaomimimo.com/v1/chat/completions"


@app.post("/mimo_clone")
def mimo_clone(req: dict):
    """代理小米 MiMo 语音克隆（mimo-v2.5-tts-voiceclone）。

    请求体: {
      api_key: str,                 # 用户的小米 MiMo API Key
      text: str,                    # 要合成的文本（放进 assistant 消息）
      audio_b64: str,               # 参考音频 base64（不含 data: 前缀）
      audio_mime?: str,             # audio/wav（默认）或 audio/mpeg
      format?: str                  # wav（默认）或 mp3
    }
    返回: wav / mp3 字节
    """
    api_key = (req.get("api_key") or "").strip()
    text = (req.get("text") or "").strip()
    audio_b64 = (req.get("audio_b64") or "").strip()
    audio_mime = (req.get("audio_mime") or "audio/wav").strip()
    fmt = (req.get("format") or "wav").strip().lower()
    if not api_key:
        return _err("缺少 api_key", 400)
    if not text:
        return _err("缺少要合成的 text", 400)
    if not audio_b64:
        return _err("缺少参考音频 audio_b64", 400)
    if fmt not in ("wav", "mp3"):
        return _err("format 仅支持 wav / mp3", 400)

    # 官方文档要求 voice 传裸 base64；部分集群要求 data: URI。先试裸 base64，400 参数错再重试带前缀。
    messages = [
        {"role": "user", "content": ""},        # 可放风格指令，这里留空
        {"role": "assistant", "content": text},  # 要说的文本放 assistant
    ]
    headers = {"api-key": api_key, "Content-Type": "application/json"}

    def _payload(voice_val):
        return {"model": "mimo-v2.5-tts-voiceclone", "messages": messages,
                "audio": {"format": fmt, "voice": voice_val}}

    try:
        r = requests.post(MIMO_ENDPOINT, json=_payload(audio_b64), headers=headers, timeout=180)
        if r.status_code == 400:
            # 参数类错误（voice/audio 格式）：重试 data: URI 形式
            r = requests.post(MIMO_ENDPOINT, json=_payload(f"data:{audio_mime};base64,{audio_b64}"),
                              headers=headers, timeout=180)
    except Exception as e:  # noqa: BLE001
        return _err(f"调用 MiMo 失败: {e}", 502)
    if r.status_code >= 400:
        return _err(f"MiMo 返回 {r.status_code}: {r.text[:400]}", 502)
    try:
        msg = r.json()["choices"][0]["message"]
        audio_field = msg.get("audio") or {}
        if not audio_field.get("data"):
            return _err(f"MiMo 未返回音频: {msg.get('content') or r.text[:400]}", 502)
        audio_bytes = base64.b64decode(audio_field["data"])
    except Exception as e:  # noqa: BLE001
        return _err(f"解析 MiMo 响应失败: {e}", 502)
    if not audio_bytes:
        return _err("MiMo 返回空音频", 502)
    return Response(audio_bytes, media_type=("audio/wav" if fmt == "wav" else "audio/mpeg"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("QWEN3_TTS_PORT", "9882"))
    # 【2026-09-10 决定性修复】禁止多个本服务进程并发加载模型。
    # 实锤：单进程 POST /load{cuda} → 干净 loaded/device=cuda:0（meta=0）；
    # 而主程序曾并发拉起两个本服务进程，二者同时 from_pretrained 加载同一模型，
    # 结果 878 个子模块落在 meta 设备（空壳、无权重），自愈重试 3 次全败，
    # 表现为「权重文件损坏或不完整」的假象，模型永远加载不出 → 试听无声/失败。
    # 启动时若端口已被占用，说明已有实例在服务，本进程直接退出，绝不加载模型。
    try:
        import socket as _socket
        _probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _probe.settimeout(1.5)
        try:
            if _probe.connect_ex(("127.0.0.1", port)) == 0:
                print(f"端口 {port} 已被占用（已有 Qwen3-TTS 实例在服务），本进程直接退出，"
                      f"避免并发加载模型导致 meta 空壳", file=sys.stderr, flush=True)
                sys.exit(0)
        finally:
            try:
                _probe.close()
            except Exception:  # noqa: BLE001
                pass
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        pass  # 端口探测本身异常不阻塞启动
    print(f"Qwen3-TTS server listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
