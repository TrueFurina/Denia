"""
达娅 CosyVoice2 零样本克隆样片生成 + 延迟实测
参考音: H:\daniya_ref.wav  (16k 单声道达娅人声)
模型:   G:/ai对话/11/models/CosyVoice2-0.5B  (需先 modelscope 下载)
输出:   G:/ai对话/11/deliverables/daniya_cosy_0..2.wav + 延迟日志
运行:   .venv_cosy/Scripts/python.exe gen_daniya_cosy_sample.py
"""
import os
import time
import sys

import torch
import soundfile as sf

# Windows 兼容: openai-whisper 在导入时调用 ctypes.util.find_library('c')，
# Windows 上返回 None 导致 ctypes.CDLL(None) 崩溃。提前 patch 返回 msvcrt.dll。
import ctypes.util as _cu

_orig_find = _cu.find_library


def _find_library(name):
    if name == "c":
        return "msvcrt.dll"
    return _orig_find(name)


_cu.find_library = _find_library

MODEL_DIR = r"G:/ai对话/11/cosyvoice_models/models/iic--CosyVoice2-0.5B/snapshots/master"
REF_WAV = r"H:\daniya_ref.wav"
OUT_DIR = r"G:/ai对话/11/deliverables"
TEXTS = [
    "在呢，怎么啦，我一直在呀",
    "刚打完一局，手感还不错哦",
    "你今天过得怎么样呀，有没有想我",
]

os.makedirs(OUT_DIR, exist_ok=True)


def log(msg):
    print(msg, flush=True)


# ---------- 1. 参考音 -> 16k tensor ----------
from cosyvoice.utils.file_utils import load_wav

log(f"[1] 加载参考音 {REF_WAV}")
try:
    prompt_speech = load_wav(REF_WAV, 16000)  # [1, T] @16k
except Exception as e:
    log(f"参考音加载失败: {e}")
    sys.exit(1)
log(f"    参考音长度: {prompt_speech.shape[-1]/16000:.2f}s")

# ---------- 2. 尝试 ASR 得到 prompt_text ----------
prompt_text = "你好，我是达娅。"
try:
    from funasr import AutoModel

    log("[2] funasr 识别参考音文字 (prompt_text)...")
    asr = AutoModel(model="paraformer-zh", model_revision="v2.0.4", disable_update=True)
    r = asr.generate(input=REF_WAV, language="zh")
    if r and r[0].get("text"):
        prompt_text = r[0]["text"]
    log(f"    ASR 结果: {prompt_text}")
except Exception as e:
    log(f"    ASR 不可用，用占位 prompt_text: {e}")

# ---------- 3. 加载 CosyVoice2 (CPU, fp32) ----------
from cosyvoice.cli.cosyvoice import CosyVoice2

log(f"[3] 加载 CosyVoice2 模型 {MODEL_DIR}")
if not os.path.isdir(MODEL_DIR):
    log(f"模型目录不存在! 请先 modelscope download iic/CosyVoice2-0.5B 到 {MODEL_DIR}")
    sys.exit(1)
t_load = time.time()
cosyvoice = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=False)
log(f"    模型加载耗时: {time.time()-t_load:.1f}s")

# ---------- 4. zero-shot 合成 + 计时 ----------
log("[4] 合成样片 (流式, 计时首包TTF + 整句TOTAL)")
for i, text in enumerate(TEXTS):
    t0 = time.time()
    first = None
    speech = None
    for sample in cosyvoice.inference_zero_shot(text, prompt_text, REF_WAV, stream=True):
        if first is None:
            first = time.time()
            ttf = first - t0
        speech = sample["tts_speech"].squeeze(0).cpu().numpy()
    total = time.time() - t0
    out = f"{OUT_DIR}/daniya_cosy_{i}.wav"
    sf.write(out, speech, 24000)
    log(f"    [{i}] TTF={ttf:.2f}s  TOTAL={total:.2f}s  -> {out}  | 文本: {text}")

log("DONE. 样片已生成，请试听确认达娅音色与延迟。")
