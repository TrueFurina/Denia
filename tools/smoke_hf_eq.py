# -*- coding: utf-8 -*-
"""接线后的冒烟验证：语法 / 导入 / 曲线 / 端到端 EQ / fail-safe / config 解析。"""
import os
import sys
import py_compile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

ok = True


def step(name, fn):
    global ok
    try:
        r = fn()
        print("  ✅ %s%s" % (name, ("  →  " + str(r)) if r else ""))
    except Exception as e:  # noqa: BLE001
        ok = False
        print("  ❌ %s: %s" % (name, e))
        traceback.print_exc()


print("=" * 96)
print("【第 1 层】语法检查")
for p in ("src/voice/hf_eq.py", "src/companion/proactive.py", "tools/voice_eq_calib.py"):
    step("py_compile %s" % p, lambda p=p: (py_compile.compile(p, doraise=True) or "OK"))

print()
print("=" * 96)
print("【第 2 层】模块导入 + 曲线可见性")
import numpy as np
from src.voice import hf_eq

step("import src.voice.hf_eq", lambda: hf_eq.SR)
step("curve_status('danya')", lambda: hf_eq.curve_status("danya"))
step("curve_status('nobody')", lambda: hf_eq.curve_status("nobody"))

print()
print("=" * 96)
print("【第 3 层】端到端 EQ 路径（真实原生产出）")

SAMP = os.path.join(PROJ, "src", "ui", "web", "assets", "chat_audio", "20260922-174709-0.wav")
REF = os.path.join(PROJ, "assets", "voices", "daniya_ref.wav")


def load_f32(p):
    import soundfile as sf
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x.astype(np.float32), sr


x, sr = load_f32(SAMP)
ref, _ = load_f32(REF)
pcm = x.tobytes()                       # 模拟 app 侧传来的 float32 PCM
print("  样本 %.2fs  sr=%d  字节数=%d" % (len(x) / sr, sr, len(pcm)))


def band(xx, lo, hi):
    n = min(len(xx), sr * 20)
    s = xx[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(s)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    r = S[(f >= 300) & (f < 3400)].sum() + 1e-12
    return float(10 * np.log10((S[(f >= lo) & (f < hi)].sum() + 1e-12) / r))


cur = hf_eq.curve_status("danya")
step("曲线存在检查", lambda: ("danya 可用" if cur["available"] else "danya 暂无曲线（需先标定）"))

y, st = hf_eq.apply_pcm_f32_safe(pcm, "danya")
print("  apply_pcm_f32_safe(key='danya') → status=%s  长度 %d→%d" % (st, len(pcm), len(y)))
if st == "applied":
    assert len(y) == len(pcm), "长度应保持不变"
    ya = np.frombuffer(y, dtype=np.float32)
    print("  频段剖面 3.4-6k / 6-8k / 8-12k：")
    print("     补偿前 [%.1f %.1f %.1f]   补偿后 [%.1f %.1f %.1f]   真源 [%.1f %.1f %.1f]"
          % (band(x, 3400, 6000), band(x, 6000, 8000), band(x, 8000, 12000),
             band(ya, 3400, 6000), band(ya, 6000, 8000), band(ya, 8000, 12000),
             band(ref, 3400, 6000), band(ref, 6000, 8000), band(ref, 8000, 12000)))
    print("  ✅ 端到端路径打通，长度不变")
else:
    assert st == "no_curve" and y == pcm, "无曲线时必须原样返回"
    print("  ✅ **接线空转**（无曲线 ⇒ 字节完全一致，对现有行为零影响）")
    print("     标定后用同一条命令即可验证：")
    print("       python tools/voice_eq_calib.py --key danya --ref assets/voices/daniya_ref.wav \\")
    print("           --match-trace \"data/call_trace_*.jsonl\"")

print()
print("=" * 96)
print("【第 4 层】fail-safe（缺曲线 / 空输入 必须原样返回）")
y1, s1 = hf_eq.apply_pcm_f32_safe(pcm, "__no_such_character__")
step("缺曲线 → 原样", lambda: "status=%s, 字节一致=%s" % (s1, y1 == pcm))
assert s1 == "no_curve" and y1 == pcm
y2, s2 = hf_eq.apply_pcm_f32_safe(pcm, "")
step("空 key → 原样", lambda: "status=%s, 字节一致=%s" % (s2, y2 == pcm))
assert s2 == "no_key" and y2 == pcm
y3, s3 = hf_eq.apply_pcm_f32_safe(b"\x00\x01", "danya")
step("过短输入 → 原样", lambda: "status=%s" % s3)
y4, s4 = hf_eq.apply_pcm_f32_safe(b"not float32 at all", "danya")
step("垃圾字节 → 原样(不抛)", lambda: "status=%s, 长度=%d" % (s4, len(y4)))
step("统计", lambda: hf_eq.stats())

print()
print("=" * 96)
print("【第 5 层】config 真解析 + load_config()")


def cfg_test():
    import yaml
    d = yaml.safe_load(open(os.path.join(PROJ, "config.yaml"), encoding="utf-8"))
    pro = d.get("proactive") or {}
    assert pro.get("duplex_hf_eq") is True
    assert pro.get("duplex_call_mode") == "voice"
    from src.config import load_config
    c = load_config(os.path.join(PROJ, "config.yaml"))
    ns = getattr(c, "ns", None)
    p2 = getattr(ns, "proactive", None)
    return "duplex_hf_eq=%r duplex_call_mode=%r active_char=%r" % (
        getattr(p2, "duplex_hf_eq", None), getattr(p2, "duplex_call_mode", None),
        getattr(c, "active_character_id", None))


step("YAML 解析 + load_config()", cfg_test)

print()
print("=" * 96)
print("总结：", "✅ 全部通过" if ok else "❌ 有失败项，请查看上面输出")
sys.exit(0 if ok else 1)
