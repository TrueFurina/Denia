# -*- coding: utf-8 -*-
"""doctor 的「语音识别」判级测试（不下载 42MB 模型，全靠打桩切状态）。

为什么需要它：2026-10-19 实测，`Model(lang="zh-cn")` 是无效语言代码
（vosk 只认 "cn"），而配置又把 model_path 写成不存在的目录 —— 两口一堵，
她就是"听不见"，日志里只有一条 ASR_INIT_FAIL。这类失效必须由 doctor 显式
判红，否则用户只会觉得"她不理我"。

运行：.venv/Scripts/python.exe src/tests/test_doctor_asr.py
"""
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import doctor  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


def make_cfg(model_path: str, mic_enabled: bool = True):
    """造一个只有 ns.voice.asr.model_path 的假 cfg。"""
    asr = types.SimpleNamespace(model_path=model_path, sample_rate=16000,
                                cmd_timeout_s=8)
    voice = types.SimpleNamespace(asr=asr, mic_enabled=mic_enabled,
                                  muted=False, mode="clone")
    return types.SimpleNamespace(ns=types.SimpleNamespace(voice=voice))


print("1) model_path 指向不存在的目录 -> FAIL（它还把自动下载堵死了）")
missing = os.path.join(tempfile.gettempdir(), "daniya_no_such_model_dir")
lv, item, detail, fix = doctor.check_asr(make_cfg(missing))
check(lv == doctor.FAIL and "不存在" in detail, f"FAIL（实际 {lv}）：{detail}")
check("自动下载" in fix, f"修复提示说清堵死了什么：{fix}")

print("\n2) model_path 目录在但加载失败 -> FAIL")
d = tempfile.mkdtemp()
lv, item, detail, fix = doctor.check_asr(make_cfg(d))
check(lv == doctor.FAIL, f"FAIL（实际 {lv}）：{detail}")

print("\n3) 两处模型都没有 -> WARN（首次用麦克风会自动下载，不该判红）")
_real_expanduser = os.path.expanduser
_real_dirs = doctor._SHERPA_MODEL_DIRS
try:
    # 强制"两处都没有"：sherpa 候选清空 + vosk 缓存指到一个空目录。
    # 以前这里只打桩 expanduser、靠"本机恰好没有 sherpa 目录"来触发 WARN，
    # 于是把"变异造成的 PASS"和"本机真有模型的 PASS"混为一谈，⑥ 刀因此漏抓。
    doctor._SHERPA_MODEL_DIRS = ()
    os.path.expanduser = lambda p: tempfile.mkdtemp()
    lv, item, detail, fix = doctor.check_asr(make_cfg(""))
    check(lv == doctor.WARN, f"WARN（实际 {lv}）：{detail}")
    check("自动下载" in detail, "提示说清会发生什么")
finally:
    os.path.expanduser = _real_expanduser
    doctor._SHERPA_MODEL_DIRS = _real_dirs

print("\n4) 有 model_path 且加载成功 -> PASS（打桩 vosk）")
import src.voice.tts_backends  # noqa: F401,E402  # noqa: F401

fake = types.ModuleType("vosk")


class _M:
    def __init__(self, *a, **k):
        pass


fake.Model = _M
fake.SetLogLevel = lambda lv_: None
_orig_vosk = sys.modules.get("vosk")
sys.modules["vosk"] = fake
try:
    lv, item, detail, fix = doctor.check_asr(make_cfg(d))
    check(lv == doctor.PASS and "指定模型" in detail, f"PASS（实际 {lv}）：{detail}")
finally:
    if _orig_vosk is not None:
        sys.modules["vosk"] = _orig_vosk
    else:
        sys.modules.pop("vosk", None)

print("\n5) 本机真实状态（不打桩，跑一遍看结论合不合理）")
lv, item, detail, fix = doctor.check_asr(make_cfg(""))
check(item == "语音识别", f"检查项名叫「语音识别」（实际 {item!r}）")
check(lv in (doctor.PASS, doctor.WARN, doctor.FAIL), f"判级合法：{lv}")

print("\n5b) 模型就绪 + 麦克风关着 → 必须 WARN 且说清是麦克风")
_real_isdir = os.path.isdir
try:
    # 强制"sherpa 模型就绪"：让任何含 sherpa-onnx 的路径判定为存在。
    # 不这么做，断言会取决于本机有没有vosk缓存——那种"有时过有时不过"的
    # 断言等于没有断言（⑥b 变异刀一开始就是靠这个漏洞漏抓的）。
    os.path.isdir = lambda p: True if "sherpa-onnx" in str(p) else _real_isdir(p)
    lv, item, detail, fix = doctor.check_asr(make_cfg("", mic_enabled=False))
    check(lv == doctor.WARN, f"WARN（实际 {lv}）：{detail}")
    check("麦克风" in detail, f"说清是麦克风：{detail}")
    check("mic_enabled" in fix, f"修复指路：{fix}")

    print("\n5c) 模型就绪 + 麦克风开着 → PASS 且不误报麦克风")
    lv, item, detail, fix = doctor.check_asr(make_cfg("", mic_enabled=True))
    check(lv == doctor.PASS, f"PASS（实际 {lv}）：{detail}")
    check("麦克风" not in detail, f"不误报：{detail}")
finally:
    os.path.isdir = _real_isdir

print("\n6) 检查项必须真的接在 run_all 里（写成工具却没接线 = 没有）")
import inspect  # noqa: E402

src = inspect.getsource(doctor.run_all)
check("check_asr" in src, "run_all 注册了 check_asr")
check("check_voice_backend" in src, "语音合成检查还在")

print("\n7) 配置没加载成功 -> FAIL（不能假装没事）")
lv, item, detail, fix = doctor.check_asr(None)
check(lv == doctor.FAIL, f"FAIL（实际 {lv}）：{detail}")

print()
if FAIL:
    print(f"共 {FAIL} 处不符")
    sys.exit(1)
print("全部通过：语音识别判级守住（配置指错必红 / 没模型只黄 / 接线没被摘掉）")
