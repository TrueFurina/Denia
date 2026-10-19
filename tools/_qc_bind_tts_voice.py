# -*- coding: utf-8 -*-
"""把当前角色的 TTS 引擎从「空=静音后端」绑到本地 pyttsx3（免费、一定会响）。

背景（实测，见 tools/_qc_tts_engine.py）：
- create_backend('') 返回 _SilentBackend：不出声、不联网。
- 角色 danya 的 voice.engine 是 ''、voice_profile 未绑 → 她**一句都说不出**。
- voice_profiles[0] 是阿里云百炼（欠费账户），且没有角色引用它。
- 本机 pyttsx3 实测 0.21s 合成出 256KB 合法 WAV，系统有中文音色 Huihui。

只改 config.yaml（被 gitignore，不进提交）；config.release.yaml 同改，
否则新用户拿到的还是"没声音"的模板。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

NL = "\r\n"
ENGINE_OLD = ("    engine: ''" + NL).encode("utf-8")
ENGINE_NEW = ("    engine: pyttsx3" + NL).encode("utf-8")
FB_OLD = ("    fallback_pyttsx3: false" + NL).encode("utf-8")
FB_NEW = ("    fallback_pyttsx3: true" + NL).encode("utf-8")

for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        print(f"[跳过] {P} 不存在")
        continue
    shutil.copy(P, P + ".bak.voice")
    b = open(P, "rb").read()
    n_eng, n_fb = b.count(ENGINE_OLD), b.count(FB_OLD)
    print(f"{P}: engine'' x{n_eng}, fallback false x{n_fb}, "
          f"CRLF={b.count(chr(13).encode() + chr(10).encode())}")
    if n_eng == 0 and n_fb == 0:
        print("  无需修改（可能已绑定）")
        os.unlink(P + ".bak.voice")
        continue
    b = b.replace(ENGINE_OLD, ENGINE_NEW).replace(FB_OLD, FB_NEW)
    open(P, "wb").write(b)
    print(f"  已写盘 {len(b)} 字节")

# 百炼档的注释要改准：它不是"主用"，只是留着的备用档
OLD_NOTE = ("  # 备用云端克隆（主用本地 voxcpm，欠费不影响）；充值后自动可用"
            + NL).encode("utf-8")
NEW_NOTE = ("  # 备用云端克隆：账户欠费时不可用，充值后把角色 voice.engine 改成"
            + NL
            + "  # dashscope_cosy 即自动切回；当前主用本地 pyttsx3（见角色 voice.engine）"
            + NL).encode()
for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        continue
    b = open(P, "rb").read()
    if b.count(OLD_NOTE) == 1:
        open(P, "wb").write(b.replace(OLD_NOTE, NEW_NOTE))
        print(f"{P}: 百炼档注释已改准")

# 验证：解析后每个角色的 engine 都不为空，且能造出非静音后端
print()
sys.path.insert(0, ROOT)
import yaml  # noqa: E402

for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        continue
    d = yaml.safe_load(open(P, encoding="utf-8"))
    print(f"--- {P} ---")
    for c in d.get("characters") or []:
        v = c.get("voice") or {}
        print(f"  {c.get('name')}: engine={v.get('engine')!r} "
              f"fallback_pyttsx3={v.get('fallback_pyttsx3')}")
    for vp in d.get("voice_profiles") or []:
        print(f"  档 {vp.get('name')!r}: engine={vp.get('engine')!r}")

from src.voice.tts_backends import create_backend  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from src.config import load_config  # noqa: E402

cfg = load_config("config.yaml")
vc = cfg.get_active_voice_config()
be = create_backend(getattr(vc, "engine", "") or "", vc)
print(f"\n当前角色实际后端：{type(be).__name__} name={be.name!r}")
assert type(be).__name__ != "_SilentBackend", "仍然是静音后端！"
print("OK：不再是静音后端")
