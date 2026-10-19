# -*- coding: utf-8 -*-
"""实测 TTS 当前真实合成链路：不猜配置，直接看落到哪个后端。

背景：voice_profiles[0] 是阿里云百炼（engine=dashscope_cosy，${DASHSCOPE_API_KEY}），
账户已欠费。而 create_backend 对「engine 为空/未知」返回**静音后端**（不出声、
不联网，2026-10-16 发布口径）。所以角色到底落到哪个后端必须实测。

用法：.venv/Scripts/python.exe -u tools/_qc_tts_engine.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

print("=" * 72)
print("TTS 合成链路实测")
print("=" * 72)

from src.config import load_config  # noqa: E402

cfg = load_config("config.yaml")
raw = cfg.raw

# ① 角色 → 音色档 → engine 的完整解析
print("\n① 角色解析出的音色配置")
ch = raw.get("characters") or []
active_id = raw.get("active_character_id")
cur = next((c for c in ch if c.get("id") == active_id), (ch or [{}])[0])
print(f"   当前角色：{cur.get('name')}（id={cur.get('id')}）")
print(f"   角色内联 voice.engine = {(cur.get('voice') or {}).get('engine')!r}")
print(f"   角色 voice_profile    = {cur.get('voice_profile')!r}（空=不绑档）")
vps = raw.get("voice_profiles") or []
print(f"   全局 voice_profiles   = {len(vps)} 个：")
for v in vps:
    print(f"     · {v.get('name')!r} engine={v.get('engine')!r} "
          f"model={v.get('model')!r}")

vc = cfg.get_active_voice_config()
if vc is None:
    print("   → get_active_voice_config() = None（TTS 会降级成 Pyttsx3Backend）")
    engine = ""
else:
    engine = getattr(vc, "engine", "") or ""
    print(f"   → 实际生效 engine = {engine!r}，voice={getattr(vc, 'voice', '')!r}，"
          f"api_url={getattr(vc, 'api_url', '')!r}")
    print(f"     fallback_pyttsx3={getattr(vc, 'fallback_pyttsx3', None)}，"
          f"api_key={'有' if (getattr(vc, 'api_key', '') or '').strip() else '空'}")

# ② 这个 engine 会造出哪个后端、能不能出声
print("\n② 该 engine 对应的后端")
from src.voice.tts_backends import create_backend  # noqa: E402
from types import SimpleNamespace  # noqa: E402

try:
    be = create_backend(engine if vc is not None else "", vc or SimpleNamespace())
    print(f"   后端名：{getattr(be, 'name', type(be).__name__)!r}")
    print(f"   类：{type(be).__name__}")
    if type(be).__name__ == "_SilentBackend":
        print("   ✗ 静音后端：接口齐全但不出声、不联网 → 她永远不说话")
except Exception as e:  # noqa: BLE001
    print(f"   ✗ 构造失败：{type(e).__name__}: {e}")

# ③ 逐个候选后端探测（不真发声，只看能否构造）
print("\n③ 候选后端就绪检查")
cands = [
    ("voxcpm", "本地 VoxCPM（项目自带，无需联网）"),
    ("pyttsx3", "本地 pyttsx3（系统 SAPI5，无需联网）"),
    ("gpt-sovits", "本地 GPT-SoVITS（需自建服务）"),
    ("qwen3tts", "Qwen3-TTS"),
    ("openai_tts", "OpenAI 兼容 TTS（智谱等）"),
    ("dashscope_cosy", "阿里云百炼 CosyVoice（账户欠费）"),
]
probe = SimpleNamespace(
    engine="", voice="", rate="+0%", pitch="+0Hz", volume="+0%",
    fallback_pyttsx3=True, api_url="", reference_audio="", reference_text="",
    language="zh", api_key="", group_id="", voice_id="", model="", speed=1,
    emotion_tags={},
)
for eng, desc in cands:
    probe.engine = eng
    try:
        b = create_backend(eng, probe)
        print(f"   {eng:16s} 可构造：{type(b).__name__:22s} {desc}")
    except Exception as e:  # noqa: BLE001
        print(f"   {eng:16s} ✗ {type(e).__name__}: {e}")

# ④ 本地依赖实情
print("\n④ 本地依赖")
import importlib  # noqa: E402
for mod, note in (("pyttsx3", "系统 SAPI5"),
                  ("voxcpm", "VoxCPM"),
                  ("soundfile", "音频 IO"),
                  ("edge_tts", "已按发布口径下架")):
    try:
        importlib.import_module(mod)
        print(f"   import {mod:12s} OK  {note}")
    except Exception as e:  # noqa: BLE001
        print(f"   import {mod:12s} NO  {type(e).__name__}  {note}")
print("\n完成。")
