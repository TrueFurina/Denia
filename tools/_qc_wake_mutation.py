# -*- coding: utf-8 -*-
"""唤醒词修复的变异测试：每刀把 wake_word.py 退回一个 bug，验证测试能抓住。

约定（与本仓其它 _qc_*_mutation.py 一致）：
  · 每刀是一个具体回归（旧 bug），应用后应当【让行为偏离正确值】；
  · 若刀应用后行为仍"正确"（=测试抓不住这刀），该刀无区分力 → 失败；
  · 若 old 锚点在源文件里找不到（=刀没切中，等于没变异），按"跳过"记 → 失败。

运行：.venv/Scripts/python.exe tools/_qc_wake_mutation.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

SRC_PATH = os.path.join(ROOT, "src", "voice", "wake_word.py")
SRC = open(SRC_PATH, encoding="utf-8").read()

FAIL = 0
E = lambda s: s.encode("utf-8")  # 锚点可能在多行里，统一成字节比对保险（这里用字符串）


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


# 假 vosk：让 WakeWordDetector 能无模型构造，且 Result 返回预设文本
class FakeRec:
    text = ""
    accept = True

    def __init__(self, *a, **k):
        FakeRec.grammar = k.get("grammar") or (a[2] if len(a) > 2 else None)

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


def load_mutated(mutated_src: str, name: str):
    """把变异后的源码写成临时模块并导入，vosk 用假实现。"""
    d = tempfile.mkdtemp(prefix="wake_mut_")
    p = os.path.join(d, f"{name}.py")
    open(p, "w", encoding="utf-8").write(mutated_src)
    sys.modules["vosk"] = FakeVosk("vosk")
    spec = importlib.util.spec_from_file_location(f"_ww_mut_{name}", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def grammar_of(mod, wake_words):
    FakeRec.text = ""
    FakeRec.accept = True
    mod.WakeWordDetector("M", 16000, wake_words)
    return json.loads(FakeRec.grammar)


def detect_of(mod, wake_words, text, accept=True):
    FakeRec.text = text
    FakeRec.accept = accept
    det = mod.WakeWordDetector("M", 16000, wake_words)
    return det.detect(b"x")


# ── 各刀：old=正确代码锚点，new=退回的 bug，symptom=应用 bug 后应当为真的现象 ──
def sym_whole_word(mod):
    gl = grammar_of(mod, ["达妮娅", "你好达妮娅"])
    return "达妮娅" in gl          # 整词语法：整词进语法（正确代码下为 False）


def sym_no_space_strip(mod):
    return detect_of(mod, ["达妮娅"], "达 妮 亚") != "达妮娅"   # 不剥空格 → 配不上


def sym_no_pinyin_tol(mod):
    return detect_of(mod, ["达妮娅"], "达妮亚") != "达妮娅"      # 无同音容错 → 亚≠娅


def sym_no_unk(mod):
    gl = grammar_of(mod, ["达妮娅"])
    return "[unk]" not in gl      # 缺 [unk] 兜底（正确代码下在）


def sym_alias(mod):
    L = ["达妮娅"]
    det = mod.WakeWordDetector("M", 16000, L)
    L.append("小薇")              # 外部改列表
    FakeRec.text = "小薇"
    FakeRec.accept = True
    return det.detect(b"x") == "小薇"   # 别名泄漏：外部词被识别（正确代码下为 False）


def sym_no_filter(mod):
    try:
        detect_of(mod, ["达妮娅", "", None, "  "], "达妮娅")
        return False              # 没崩 → 没复现 bug（正确代码下也不崩，这里应True）
    except Exception:
        return True               # 空/None 没被过滤 → sorted 撞 None 抛 TypeError


KNIVES = [
    ("A 整词语法（旧 bug：她名字被静默跳过）",
     'grammar = json.dumps(sorted(chars) + ["[unk]"], ensure_ascii=False)',
     'grammar = json.dumps(sorted(wake_words) + ["[unk]"], ensure_ascii=False)',
     sym_whole_word),
    ("B 不剥空格",
     '.replace(" ", "")',
     "",  # 删掉 .replace(" ", "")
     sym_no_space_strip),
    ("C 去掉拼音同音容错",
     "    t, w = _syllables(text), _syllables(word)\n"
     "    n = len(w)\n"
     "    if n == 0:\n"
     "        return False\n"
     "    return any(t[i:i + n] == w for i in range(len(t) - n + 1))",
     "    return word in text",
     sym_no_pinyin_tol),
    ("D 删掉 [unk] 兜底",
     ' + ["[unk]"]',
     "",
     sym_no_unk),
    ("E 列表别名（外部改动串味）",
     "self._wake_words: list[str] = [w for w in list(wake_words or []) if w]",
     "self._wake_words = wake_words",
     sym_alias),
    ("F 不滤空/None（sorted 撞 None 会崩）",
     "self._wake_words: list[str] = [w for w in list(wake_words or []) if w]",
     "self._wake_words: list[str] = list(wake_words or [])",
     sym_no_filter),
]


print("唤醒词变异测试：每刀退回一个 bug，验证测试能区分（锚点缺失=失败）\n")
for name, old, new, symptom in KNIVES:
    if old not in SRC:
        check(False, f"{name}：锚点未命中（old 不存在）→ 视为跳过")
        continue
    mutated = SRC.replace(old, new, 1)
    if mutated == SRC:
        check(False, f"{name}：替换无效")
        continue
    mod = load_mutated(mutated, f"k{name[0]}")
    caught = symptom(mod)
    check(caught, f"{name}：刀切中且行为偏离（测试能抓住）")

print()
if FAIL:
    print(f"共 {FAIL} 把刀无区分力/未命中")
    sys.exit(1)
print("全部通过：六把刀都有区分力，唤醒词修复无回归死角")
