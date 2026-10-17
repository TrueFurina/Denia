# -*- coding: utf-8 -*-
"""确认 duplex_native_audio=false 之后，外部 TTS 会用到哪个音色档。

这是"第三条路"的关键：双工保留实时轮次/打断，嗓子换成云端克隆。
若当前生效音色不是 dashscope_cosy ⇒ 关掉原生音频后会变成通用音（如微软晓伊），
那就必须先切音色档，否则越改越糟。
"""
import os
import sys

ROOT = r"G:\ai对话\11"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    from src.config import load_config
    cfg = load_config(os.path.join(ROOT, "config.yaml"))

    print("① 当前生效音色档")
    v = cfg.get_active_voice_config()
    if v is None:
        print("   ❌ 取不到生效音色档")
        return 2
    print("   id      = %s" % getattr(v, "id", ""))
    print("   name    = %s" % getattr(v, "name", ""))
    print("   engine  = %s" % getattr(v, "engine", ""))
    print("   voice   = %s" % getattr(v, "voice", ""))
    print("   voice_id= %s" % getattr(v, "voice_id", ""))
    print("   ref_audio = %s" % getattr(v, "reference_audio", ""))

    ok = str(getattr(v, "engine", "") or "") == "dashscope_cosy"
    print("\n   ⇒ %s" % ("✅ 已是 dashscope_cosy（关掉原生音频即走云端达妮娅克隆）"
                         if ok else "⚠️ 不是 dashscope_cosy —— 关掉原生音频会变成通用音，需先切档"))

    print("\n② 全部音色档")
    for p in (cfg.list_voice_profiles() or []):
        mark = "  <= 生效" if str(p.get("id", "")) == str(getattr(v, "id", "")) else ""
        print("   %-22s %-18s %s%s"
              % (p.get("id", ""), p.get("engine", ""),
                 (p.get("voice_id", "") or p.get("voice", ""))[:42], mark))

    print("\n③ 当前双工相关开关")
    ns = getattr(cfg, "ns", None)
    pro = getattr(ns, "proactive", None) if ns else None
    if pro is not None:
        for k in ("duplex_native_audio", "duplex_mode", "duplex_call_mode",
                  "duplex_hf_eq", "duplex_local_omni_ref_audio"):
            print("   %-30s = %s" % (k, getattr(pro, k, "<未设置>")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
