# -*- coding: utf-8 -*-
"""音色 A/B：同一句话，两个 voice_id 各合成一次，供人耳判断哪个是"对的达妮娅"。

背景（2026-09-24 02:50）：用户反馈通话音色"怪怪的"。
排查发现：角色 danya 配了 voice_profile: tmu1ik64h，而 Config.get_active_voice_config()
是"音色档优先"，把角色内联那套调好的参数整个盖掉了——
  生效(音色档) : cosyvoice-v3.5-plus-bailian-e683… / model=cosyvoice-v3.5-plus / pitch_scale=1.0
  内联(被覆盖) : qwen-audio-3.0-tts-plus-bailian-c64f… / model=qwen-audio-3.0-tts-plus
                 rate=+13% pitch=-4Hz reference_audio=daniya_ref.wav
两者是**不同模型克隆出的不同音色**，且音色档没有 rate/pitch/参考音频。
本脚本产出两个 mp3，让人耳直接裁决。
"""
import asyncio
import os
import sys

import httpx

ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"

# 从 config.yaml 读，避免硬编码 Key 泄漏到脚本里
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

TEXT = "嗯……还好呢，你呢？今天加班到十点，累不累呀，漂泊者。"


def load_variants():
    from src.config import load_config
    cfg = load_config(os.path.join(REPO, "config.yaml"))
    ch = cfg.get_character_by_id("danya")
    active = cfg.get_active_voice_config()
    inline = getattr(ch, "voice", None)
    # B：角色内联的 voice_id/model（达妮娅原本调好的音色），
    #    但内联 api_key 实测 401 失效 → 借用音色档那把有效 Key 来验证音色本身
    import copy
    inline_fixed = copy.deepcopy(inline)
    if getattr(active, "api_key", ""):
        inline_fixed.api_key = active.api_key
    return [
        ("A_当前生效_音色档cosyvoice", active),
        ("B_达妮娅原音色_qwen-audio", inline_fixed),
    ]


async def synth(name, v, text):
    model = getattr(v, "model", "") or "qwen-audio-3.0-tts-plus"
    voice_id = getattr(v, "voice_id", "") or getattr(v, "voice", "")
    key = getattr(v, "api_key", "")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "input": {"text": text, "voice": voice_id, "format": "mp3"},
    }
    ps = float(getattr(v, "pitch_scale", 1.0) or 1.0)
    params = {}
    if abs(ps - 1.0) > 1e-6:
        params["pitch"] = ps
    if params:
        body["parameters"] = params

    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post(ENDPOINT, headers=headers, json=body)
    if r.status_code >= 400:
        print(f"[{name}] FAIL HTTP {r.status_code}: {r.text[:300]}")
        return None
    ctype = r.headers.get("content-type", "")
    data = None
    if "application/json" in ctype:
        j = r.json()
        # 结构不固定：output.audio 可能是 str(url)/dict{url|data}/b64 字符串，递归找
        def _find_str(o, keys):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in keys and isinstance(v, str) and v:
                        return v
                    got = _find_str(v, keys)
                    if got:
                        return got
            elif isinstance(o, list):
                for v in o:
                    got = _find_str(v, keys)
                    if got:
                        return got
            return ""

        import base64
        url = _find_str(j, {"audio_url", "url", "download_url"})
        b64 = _find_str(j, {"audio", "data", "audio_data"})
        if url and url.startswith("http"):
            async with httpx.AsyncClient(timeout=60) as cli:
                data = (await cli.get(url)).content
        elif b64:
            try:
                data = base64.b64decode(b64)
            except Exception:
                data = None
        if not data:
            print(f"[{name}] FAIL 响应无法解析: {str(j)[:300]}")
            return None
    elif "audio" in ctype or "mpeg" in ctype or "octet" in ctype:
        data = r.content
    if not data:
        print(f"[{name}] FAIL 无音频数据 content-type={ctype}")
        return None
    out_dir = os.path.join(REPO, "data", "voice_ab_20260924")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.mp3")
    with open(path, "wb") as f:
        f.write(data)
    print(f"[{name}] OK {len(data)/1024:.1f}KB  model={model}  voice={voice_id[:44]}…  pitch_scale={ps}")
    return path


async def main():
    variants = load_variants()
    paths = []
    for name, v in variants:
        p = await synth(name, v, TEXT)
        if p:
            paths.append(p)
    print()
    print("测试句:", TEXT)
    for p in paths:
        print("  ", p)


if __name__ == "__main__":
    asyncio.run(main())
