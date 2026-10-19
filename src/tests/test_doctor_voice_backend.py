# -*- coding: utf-8 -*-
"""doctor 的「语音合成」判级测试（不打真实网络；本地引擎只跑一次真合成）。

为什么需要它：2026-10-19 实测发现，create_backend('') 对「engine 为空/未知」
返回**静音后端**（_SilentBackend：不出声、不联网）。于是角色没绑引擎时，症状是
「AI 有回复但一个字都不说」——日志里一条都没有。这类"配置看着没填错、
只是没被验证过"的失效，必须由 doctor 显式判红，否则用户只会觉得她不理人。

运行：.venv/Scripts/python.exe src/tests/test_doctor_voice_backend.py
"""
import os
import sys
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


def voice_cfg(**kw):
    base = dict(engine="", voice="", rate="+0%", pitch="+0Hz", volume="+0%",
                fallback_pyttsx3=True, api_url="", reference_audio="",
                reference_text="", language="zh", api_key="", group_id="",
                voice_id="", model="", speed=1, emotion_tags={},
                timeout_s=30)
    base.update(kw)
    return types.SimpleNamespace(**base)


class FakeCfg:
    """只提供 get_active_voice_config()，够 doctor 用。"""

    def __init__(self, vc):
        self._vc = vc

    def get_active_voice_config(self):
        return self._vc


class FakeCfgRaise:
    def get_active_voice_config(self):
        raise RuntimeError("boom")


print("1) engine 为空 -> 静音后端 -> FAIL（能收不能说的哑巴）")
lv, item, detail, fix = doctor.check_voice_backend(FakeCfg(voice_cfg()))
check(lv == doctor.FAIL and item == "语音合成", f"FAIL（实际 {lv}）")
check("静音" in detail or "未绑定" in detail, f"说清原因：{detail}")

print("\n2) engine 不在引擎表里 -> 同样 FAIL")
lv, item, detail, fix = doctor.check_voice_backend(
    FakeCfg(voice_cfg(engine="不存在的引擎")))
check(lv == doctor.FAIL, f"FAIL（实际 {lv}）：{detail}")

print("\n3) 配置压根没加载成功 -> FAIL（不能假装没事）")
lv, item, detail, fix = doctor.check_voice_backend(None)
check(lv == doctor.FAIL, f"FAIL（实际 {lv}）：{detail}")

print("\n4) 解析角色音色就抛异常 -> FAIL 且说清是解析失败")
lv, item, detail, fix = doctor.check_voice_backend(FakeCfgRaise())
check(lv == doctor.FAIL and "解析" in detail, f"FAIL（实际 {lv}）：{detail}")

print("\n5) 绑了 pyttsx3（本地免费）-> 真合成一次 -> PASS")
lv, item, detail, fix = doctor.check_voice_backend(
    FakeCfg(voice_cfg(engine="pyttsx3")))
check(lv == doctor.PASS, f"PASS（实际 {lv}）：{detail}")
check("真合成" in detail, "证明是听过才判过的，不是只看配置")

print("\n6) voxcpm 但本机 9881 没在听 -> WARN（起服务就能好，不该判红）")
lv, item, detail, fix = doctor.check_voice_backend(
    FakeCfg(voice_cfg(engine="voxcpm")))
check(lv == doctor.WARN, f"WARN（实际 {lv}）：{detail}")

print("\n7) 云端引擎（dashscope_cosy）但没 key -> WARN")
lv, item, detail, fix = doctor.check_voice_backend(
    FakeCfg(voice_cfg(engine="dashscope_cosy",
                      api_url="https://dashscope.aliyuncs.com")))
check(lv == doctor.WARN and "api_key" in detail, f"WARN（实际 {lv}）：{detail}")

print("\n8) 云端引擎有 key -> 不真合成（欠费时 15s 超时会卡死 doctor）-> PASS")
lv, item, detail, fix = doctor.check_voice_backend(
    FakeCfg(voice_cfg(engine="dashscope_cosy",
                      api_url="https://dashscope.aliyuncs.com",
                      api_key="sk-test")))
check(lv == doctor.PASS and "未真合成" in detail, f"PASS（实际 {lv}）：{detail}")

print("\n9) 本地引擎声称可用却出不了声 -> FAIL（打桩让合成抛异常）")
import src.voice.tts_backends as tb  # noqa: E402

_orig = tb.create_backend


class BrokenBackend:
    name = "broken"

    @property
    def output_ext(self):
        return ".wav"

    def speak(self, text, out):
        raise RuntimeError("合成器炸了")


tb.create_backend = lambda eng, cfg: BrokenBackend()
try:
    lv, item, detail, fix = doctor.check_voice_backend(
        FakeCfg(voice_cfg(engine="pyttsx3")))
    check(lv == doctor.FAIL and "真合成失败" in detail,
          f"FAIL（实际 {lv}）：{detail}")
finally:
    tb.create_backend = _orig

print("\n10) 真合成了但产物是空文件 -> FAIL（等于没出声）")
_orig_speak = None


class EmptyBackend:
    name = "empty"

    @property
    def output_ext(self):
        return ".wav"

    def speak(self, text, out):
        with open(out, "wb") as f:
            f.write(b"")          # 0 字节


tb.create_backend = lambda eng, cfg: EmptyBackend()
try:
    lv, item, detail, fix = doctor.check_voice_backend(
        FakeCfg(voice_cfg(engine="pyttsx3")))
    check(lv == doctor.FAIL, f"FAIL（实际 {lv}）：{detail}")
finally:
    tb.create_backend = _orig

print("\n11) 检查项必须真的接在 run_all 里（写成工具不接线=没有）")
import inspect  # noqa: E402

src = inspect.getsource(doctor.run_all)
check("check_voice_backend" in src, "run_all 注册了 check_voice_backend")

print("\n12) 不会误伤：check_llm_channel / check_secret_storage 仍在册")
check("check_llm_channel" in src, "模型渠道探活还在")
check("check_secret_storage" in src, "密钥落盘检查还在")

print()
if FAIL:
    print(f"共 {FAIL} 处不符")
    sys.exit(1)
print("全部通过：语音合成判级守住（静音后端必红 / 本地引擎真听过 / 起服务能好的只黄）")
