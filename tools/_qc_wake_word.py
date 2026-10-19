# -*- coding: utf-8 -*-
"""闭环验证「她理不理你」：真说唤醒词 → WakeWordDetector 命中？

这不是静态检查，是真走一遍 声音→唤醒命中。分三组：
  ① 四个配置里的唤醒词逐一念，每个都必须 detect() 命中
  ② 念一句不相关的话，不能误触发（唤醒词表是子串匹配，[unk] 兜底）
  ③ 构造期观察 vosk 对词表的报错（词不在 lexicon 会被跳过——静默失效点）

用法：.venv/Scripts/python.exe -u tools/_qc_wake_word.py
"""
import io
import os
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

print("=" * 72)
print("唤醒词闭环验证：念唤醒词 → 必须命中")
print("=" * 72)

import pyttsx3  # noqa: E402

tmp = os.path.join(os.environ.get("TEMP", "."), "daniya_wake_%s.wav")


def synth(text: str, tag: str) -> str:
    """pyttsx3 念一句，重采样到 16k 单声道 int16 wav，返回路径。

    末尾补 2s 静音：真实麦克风场景用户念完会停一下，而 detect() 只在
    AcceptWaveform 报告句尾静音时才收口——TTS 裸音频常常戛然而止，
    不补静音等于测"用户不停嘴"，不是产品行为。
    """
    import numpy as np
    import soundfile as sf

    raw = os.path.join(os.environ.get("TEMP", "."), f"daniya_wake_raw_{tag}.wav")
    dst = tmp % tag
    for p in (raw, dst):
        if os.path.exists(p):
            os.unlink(p)
    eng = pyttsx3.init()
    eng.setProperty("rate", 120)
    eng.save_to_file(text, raw)
    eng.runAndWait()
    if not os.path.exists(raw):
        raise RuntimeError(f"pyttsx3 没产出音频: {text}")
    data, sr = sf.read(raw, dtype="int16", always_2d=True)
    mono = data.mean(axis=1).astype("int16")
    if sr != 16000:
        n = int(round(len(mono) * 16000 / sr))
        idx = np.linspace(0, len(mono) - 1, n).round().astype(int)
        mono = mono[idx]
    mono = np.concatenate([mono, np.zeros(32000, dtype="int16")])  # 2s 静音
    sf.write(dst, mono, 16000, subtype="PCM_16")
    return dst


from src.config import load_config  # noqa: E402
from src.voice.asr import ASR  # noqa: E402
from src.voice.wake_word import WakeWordDetector  # noqa: E402

cfg = load_config("config.yaml")
wake_words = list(cfg.ns.voice.wake_words)
print(f"\n配置唤醒词表：{wake_words}")
print(f"voice.mode = {cfg.ns.voice.mode!r}，continuous_voice = "
      f"{getattr(cfg.ns.voice, 'continuous_voice', False)}")

asr = ASR.__new__(ASR)
asr._model_path = ""
model = asr._load_model()
print("模型已加载（自动下载分支）")

# ③ 构造期保留 vosk 自身日志：词不在 lexicon 时它会把词跳过（静默失效点）
print("\n① 构造 WakeWordDetector（与 asr.py 第 139 行同参），观察 vosk 日志")
det = WakeWordDetector(model, 16000, wake_words)
print("   构造 OK（无异常）")


def feed(wav_path: str) -> str:
    """流式喂音频，返回命中的唤醒词或 None。"""
    with wave.open(wav_path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        frames = w.readframes(w.getnframes())
    for i in range(0, len(frames), 4000):
        hit = det.detect(frames[i:i + 4000])
        if hit:
            return hit
    return None


print("\n② 念每个唤醒词，必须命中（含同音字变体：模型会把娅听成亚）")
fails = []
# (念的话, 期望命中)
CASES = [(w, w) for w in wake_words] + [("达尼亚", "达妮娅")]
for say, expect in CASES:
    tag = f"c{CASES.index((say, expect))}"
    p = synth(say, tag)
    # 每个用例新建检测器：语法约束识别器有内部状态，复用会串味
    det = WakeWordDetector(model, 16000, wake_words)
    hit = feed(p)
    mark = "PASS" if hit == expect else "FAIL"
    if hit != expect:
        fails.append((say, expect, hit))
    print(f"   [{mark}] 念 {say!r} → 期望 {expect!r}，实得 {hit!r}")

print("\n③ 念不相关/近音的话，不能误触发")
det = WakeWordDetector(model, 16000, wake_words)
for say in ["今天天气真好啊我想出去散步", "小爱同学今天真漂亮"]:
    p = synth(say, "neg" + str(len(say)))
    det = WakeWordDetector(model, 16000, wake_words)
    hit = feed(p)
    if hit:
        fails.append(("(误触发)", say, hit))
    print(f"   [{'FAIL' if hit else 'PASS'}] 念 {say!r} → {hit!r}")

print()
if not fails:
    print("PASS：唤醒词闭环成（念得中、闲话不误触）")
    sys.exit(0)
print("FAIL：以下项没过：" + repr(fails))
sys.exit(1)
