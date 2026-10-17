# -*- coding: utf-8 -*-
"""云端克隆 vs 本地双工(EQ后) 同文本对比。

用项目自己的 dashscope_cosy 后端（阿里云百炼）合成**与本地产出完全相同**的文字，
和本地双工样本放一起，让人耳直接裁决两条路线的差距。

用法（须 client_venv，需外网可达百炼）：
    H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe tools/_make_cloud_compare.py
"""
import os
import sys
import glob
import json
import asyncio
import wave

ROOT = r"G:\ai对话\11"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PKG = os.path.join(ROOT, "data", "云端vs本地_同文本_20260923")
OUTDIR = os.path.join(ROOT, "data", "duplex_out")
LOCAL_SRC = os.path.join(ROOT, "data", "高频补偿_更强档_20260923", "V3_100%+SBR.wav")


def latest_trace():
    fs = sorted(glob.glob(os.path.join(ROOT, "data", "call_trace_*.jsonl")),
                key=os.path.getmtime)
    return fs[-1] if fs else ""


def emit_texts(path, limit=6):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("ev") == "emit":
            t = (r.get("text") or "").strip()
            if t:
                out.append(t)
    return out[:limit]


def main():
    os.makedirs(PKG, exist_ok=True)

    tr = latest_trace()
    texts = emit_texts(tr) if tr else []
    if not texts:
        texts = ["你好呀，漂泊者。我在这儿呢，有什么事吗？"]
    full = "".join(texts)
    print("① 待合成文本（取自本地产出，保证同文本）")
    print("   追溯文件：%s" % os.path.basename(tr))
    for t in texts:
        print("     · %s" % t)
    print("   合计 %d 字" % len(full))

    print("\n② 解析百炼云端音色档")
    from src.config import load_config
    cfg = load_config(os.path.join(ROOT, "config.yaml"))

    prof = None
    active = cfg.get_active_voice_config()
    if active is not None and str(getattr(active, "engine", "") or "") == "dashscope_cosy":
        prof = active
    if prof is None:
        for p in (cfg.list_voice_profiles() or []):
            if str(p.get("engine", "") or "") == "dashscope_cosy":
                from types import SimpleNamespace
                prof = SimpleNamespace(
                    api_key=p.get("api_key", ""), voice_id=p.get("voice_id", ""),
                    model=p.get("model", "") or "qwen-audio-3.0-tts-plus",
                    reference_audio=p.get("reference_audio", ""),
                    reference_text=p.get("reference_text", ""))
                break
    if prof is None:
        print("   ❌ 没有 dashscope_cosy 音色档")
        return 2

    print("   voice_id      = %s" % getattr(prof, "voice_id", ""))
    print("   model         = %s" % getattr(prof, "model", ""))
    print("   api_key       = %s" % ("已配置(%d字符)" % len(getattr(prof, "api_key", "") or "")
                                     if getattr(prof, "api_key", "") else "❌ 缺失"))
    print("   reference_audio = %s" % getattr(prof, "reference_audio", ""))

    from types import SimpleNamespace
    ns = SimpleNamespace(
        api_key=getattr(prof, "api_key", "") or "",
        voice_id=getattr(prof, "voice_id", "") or "",
        model=getattr(prof, "model", "") or "qwen-audio-3.0-tts-plus",
        reference_audio=getattr(prof, "reference_audio", "") or "",
        reference_text=getattr(prof, "reference_text", "") or "",
    )
    from src.voice.tts_backends import create_backend
    tts = create_backend("dashscope_cosy", ns)
    print("   后端 = %s" % getattr(tts, "name", type(tts).__name__))

    print("\n③ 合成")
    outp = os.path.join(PKG, "云端百炼克隆_同文本.wav")
    try:
        asyncio.run(tts.synthesize(full, outp))
    except Exception as e:
        print("   ❌ 合成失败：%r" % e)
        return 3
    if not os.path.isfile(outp):
        print("   ❌ 未产出文件")
        return 4
    print("   ✅ %s  (%.1f KB)" % (outp, os.path.getsize(outp) / 1024))

    # 本地版本拷过来
    if os.path.isfile(LOCAL_SRC):
        dst = os.path.join(PKG, "本地双工_EQ后_同文本.wav")
        import shutil
        shutil.copy2(LOCAL_SRC, dst)
        print("   ✅ 本地对照已放入：%s" % os.path.basename(dst))
    else:
        print("   ⚠️ 找不到本地样本 %s" % LOCAL_SRC)

    print("\n输出目录：%s" % PKG)
    for f in sorted(os.listdir(PKG)):
        print("   %s" % f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
