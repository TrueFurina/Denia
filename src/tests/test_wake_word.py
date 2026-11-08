# -*- coding: utf-8 -*-
"""唤醒词「她理不理你」：单字+同音字语法、拼音容错、空格归一、长词优先、列表隔离。

2026-10-19 实测定性后写的回归：
  · 旧代码语法按整词 → "达妮娅"不在模型词图里被静默跳过，念她名字永不醒；
  · 新代码语法按单字+同音字，匹配按拼音音节序列（娅/亚 这类同音字也算中）。
  · 真实声学闭环交给 tools/_qc_wake_word.py（需要麦克风模型，不塞进单测）。

运行：.venv/Scripts/python.exe src/tests/test_wake_word.py
"""
import importlib
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


# ── 假 vosk：KaldiRecognizer 必须支持 (model, sample_rate[, grammar]) 三参数，
#    以及 SetWords / AcceptWaveform / Result / PartialResult。─────────────
class FakeRec:
    """每次测试前通过类属性配置：last_grammar 捕获构造时传入的语法字符串，
    text 是 Result() 返回的 {"text": ...}，accept 是 AcceptWaveform 的返回值。"""
    last_grammar = None
    text = ""
    accept = True

    def __init__(self, model=None, sample_rate=None, grammar=None):
        FakeRec.last_grammar = grammar

    def SetWords(self, v):
        pass

    def AcceptWaveform(self, data):
        return FakeRec.accept

    def Result(self):
        return json.dumps({"text": FakeRec.text})

    def PartialResult(self):
        return json.dumps({"partial": ""})


class FakeVosk(types.ModuleType):
    KaldiRecognizer = FakeRec


_orig_vosk = sys.modules.get("vosk")
sys.modules["vosk"] = FakeVosk("vosk")
try:
    ww = importlib.import_module("src.voice.wake_word")
finally:
    if _orig_vosk is not None:
        sys.modules["vosk"] = _orig_vosk
    else:
        sys.modules.pop("vosk", None)

# 前置：第 1 节验的是「同音字语法扩张」，必须读 pypinyin 的真实字典。
# 本机没有 pypinyin 时，make_det(pinyin=True) 会走到 _homophones() 里的
# _PINYIN_DICT_RAW → NameError（2026-11-08 在只装 requirements-test 的干净
# venv 里实测），看起来像产品缺陷，其实是用例缺件。早说清楚，别甩 traceback。
# 依赖已在 requirements-test.txt 声明；这里只负责把缺失原因点到人脸上。
if not ww._HAS_PINYIN:
    print("FAIL 本用例需要 pypinyin：pip install pypinyin（requirements-test.txt 已声明）")
    sys.exit(1)


def make_det(wake_words, text="", accept=True, pinyin=True):
    """构造一个用假 recognizer 的 WakeWordDetector，并预设识别结果。"""
    ww._HAS_PINYIN = pinyin
    sys.modules["vosk"] = FakeVosk("vosk")
    try:
        FakeRec.text = text
        FakeRec.accept = accept
        det = ww.WakeWordDetector("M", 16000, wake_words)
        grammar = FakeRec.last_grammar
    finally:
        if _orig_vosk is not None:
            sys.modules["vosk"] = _orig_vosk
        else:
            sys.modules.pop("vosk", None)
    return det, grammar


print("1) 语法必须按【单字+同音字】构建，不能按整词；且含 [unk] 兜底")
det, grammar = make_det(["达妮娅", "你好达妮娅"], pinyin=True)
gl = json.loads(grammar)
check(isinstance(gl, list), f"语法是 JSON 数组（{type(gl).__name__}）")
check("[unk]" in gl, "语法包含 [unk]（非唤醒语音的唯一出路）")
check("达妮娅" not in gl, "整词「达妮娅」不在语法里（旧 bug：整词语法让她名字被静默跳过）")
for ch in ("达", "妮", "娅"):
    check(ch in gl, f"唤醒字 {ch!r} 在语法里")
# 同音字扩张：娅 的同音字（亚等）应被纳入，解码器才吐得出真正的音
check(any(c != "娅" and ww._syllables(c) == ww._syllables("娅") for c in gl),
      "「娅」的同音字（如 亚）被纳入语法（实测：不放同音字解码吐『达妮达』）")

print("\n2) 拼音容错：模型把娅听成亚、带空格，仍应命中『达妮娅』")
det, _ = make_det(["达妮娅"], text="达 妮 亚", pinyin=True)
check(det.detect(b"x") == "达妮娅",
      "识别结果『达 妮 亚』（娅→亚、带空格）仍命中『达妮娅』")

print("\n3) [unk] / 空结果不误触发")
det, _ = make_det(["达妮娅"], text="[unk]", pinyin=True)
check(det.detect(b"x") is None, "[unk] 不触发")
det, _ = make_det(["达妮娅"], text="", pinyin=True)
check(det.detect(b"x") is None, "空文本不触发")

print("\n4) 未到句尾（AcceptWaveform=False）不收口")
det, _ = make_det(["达妮娅"], text="达妮娅", accept=False, pinyin=True)
check(det.detect(b"x") is None, "流式未收口时不命中（避免半句乱醒）")

print("\n5) 长词优先：『你好达妮娅』应返回更具体的别名而非『达妮娅』")
det, _ = make_det(["达妮娅", "你好达妮娅"], text="你好达妮娅", pinyin=True)
check(det.detect(b"x") == "你好达妮娅", "返回最长匹配别名")

print("\n6) 空/None/空白唤醒词被过滤，检测器不受影响")
det, _ = make_det(["达妮娅", "", None, "  "], text="达妮娅", pinyin=True)
check(det.detect(b"x") == "达妮娅", "过滤后只剩有效词仍能命中")

print("\n7) 外部列表随后被改，已建好的检测器不串味（拷贝而非别名）")
L = ["达妮娅"]
det, _ = make_det(L, text="达妮娅", pinyin=True)
L.append("小薇")  # 外部改动
check(det.detect(b"x") == "达妮娅", "构造后改外部列表不影响检测器")

print("\n8) 英文唤醒词也能用（非汉字逐字保留）")
det, _ = make_det(["hello"], text="hello", pinyin=True)
check(det.detect(b"x") == "hello", "英文唤醒词『hello』命中")

print("\n9) pypinyin 缺失时降级：整字精确匹配，同音字配不上但仍不崩")
det, _ = make_det(["达妮娅"], text="达 妮 亚", pinyin=False)
check(det.detect(b"x") is None, "无拼音容错时『达妮亚』配不上『达妮娅』（降级预期）")
det, _ = make_det(["达妮娅"], text="达妮娅", pinyin=False)
check(det.detect(b"x") == "达妮娅", "降级模式下整字精确匹配仍有效")

print("\n10) 模块在无 pypinyin 时也能 import（降级路径不崩）")
# reload 会重新执行模块顶部的 `from vosk import KaldiRecognizer`，而本用例从没装
# 真 vosk（识别器一直是 FakeRec）；不把假 vosk 放回去，这里报的是
# ModuleNotFoundError: vosk —— 验不到本意要验的 pypinyin 降级（2026-11-08 实测）。
sys.modules["pypinyin"] = None  # 让 import 失败
sys.modules["vosk"] = FakeVosk("vosk")
try:
    importlib.reload(ww)
    check(getattr(ww, "_HAS_PINYIN", True) is False, "无 pypinyin → _HAS_PINYIN=False，import 不崩")
except Exception as e:  # noqa: BLE001
    check(False, f"无 pypinyin 时 import 抛异常：{type(e).__name__}: {e}")
finally:
    sys.modules.pop("pypinyin", None)
    sys.modules["vosk"] = FakeVosk("vosk")   # 恢复成真实 pypinyin 的那次 reload 同样要它
    importlib.reload(ww)
    if _orig_vosk is not None:
        sys.modules["vosk"] = _orig_vosk
    else:
        sys.modules.pop("vosk", None)

print()
if FAIL:
    print(f"共 {FAIL} 处不符")
    sys.exit(1)
print("全部通过：她理你了（单字+同音字语法 / 拼音容错 / 长词优先 / 列表隔离 / 降级不崩）")
