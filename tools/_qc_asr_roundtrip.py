# -*- coding: utf-8 -*-
"""闭环验证「她现在听得见吗」：pyttsx3 念一句中文 → vosk 识别 → 比对文本。

这不是"模型文件在不在"的静态检查，而是真走一遍 声音→文字。识别结果允许
同音字误差（small 模型），所以只要求关键词命中。

用法：.venv/Scripts/python.exe -u tools/_qc_asr_roundtrip.py
"""
import os
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

SAY = "你好呀，我现在能听见了"
EXPECT = ("你好", "听见", "能")

print("=" * 72)
print("ASR 闭环验证：念一句 → 识别一句")
print("=" * 72)

# ① 用 pyttsx3（已确认可用）念一句，产出 16k 单声道 wav
print("\n① pyttsx3 合成语音")
import pyttsx3  # noqa: E402

tmp = os.path.join(os.environ.get("TEMP", "."), "daniya_asr_roundtrip.wav")
if os.path.exists(tmp):
    os.unlink(tmp)
eng = pyttsx3.init()
eng.setProperty("rate", 120)          # 慢一点，small 模型更容易识别
eng.save_to_file(SAY, tmp)
eng.runAndWait()
print(f"   念的是：{SAY}")
print(f"   产物：{tmp}（{os.path.getsize(tmp) if os.path.exists(tmp) else 0} 字节）")

# pyttsx3 输出可能是 8k/16k、单声道；统一重采样到 16k 单声道 int16
print("\n② 重采样到 16k 单声道（vosk 要的格式）")
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

data, sr = sf.read(tmp, dtype="int16", always_2d=True)
mono = data.mean(axis=1).astype("int16")
print(f"   原始 {sr}Hz {data.shape[1]} 声道 → {mono.shape[0]} 帧")
if sr != 16000:
    # 线性重采样（够用：识别不要求高保真）
    n = int(round(len(mono) * 16000 / sr))
    idx = np.linspace(0, len(mono) - 1, n).round().astype(int)
    mono = mono[idx]
    print(f"   重采样到 16kHz：{len(mono)} 帧")
w16 = os.path.join(os.environ.get("TEMP", "."), "daniya_asr_16k.wav")
sf.write(w16, mono, 16000, subtype="PCM_16")

# ③ 走项目自己的 _load_model 逻辑（不绕过，验证的是产品路径）
print("\n③ 用项目 ASR 的取模型方式加载")
from src.config import load_config  # noqa: E402
from src.voice.asr import ASR  # noqa: E402

cfg = load_config("config.yaml")
asr = ASR.__new__(ASR)              # 不起线程，只用 _load_model
asr._model_path = ""
model = asr._load_model()
print("   _load_model() OK（model_path 为空 → 走自动下载分支）")

# ④ 流式喂进去识别
print("\n④ 流式识别")
from vosk import KaldiRecognizer, SetLogLevel  # noqa: E402
SetLogLevel(-1)
rec = KaldiRecognizer(model, 16000)
rec.SetWords(True)

with wave.open(w16, "rb") as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1
    frames = w.readframes(w.getnframes())

CHUNK = 4000
for i in range(0, len(frames), CHUNK):
    rec.AcceptWaveform(frames[i:i + CHUNK])
import json  # noqa: E402
final = json.loads(rec.FinalResult())
text = (final.get("text") or "").replace(" ", "")
print(f"   识别结果：{text!r}")

hit = [k for k in EXPECT if k in text]
print(f"   关键词命中：{hit} / {EXPECT}")

print()
if len(hit) >= 2:
    print("PASS：听得见（真识别出中文）")
    sys.exit(0)
print("FAIL：没识别出来（检查麦克风/模型/采样率）")
sys.exit(1)
