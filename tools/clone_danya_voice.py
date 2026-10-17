# -*- coding: utf-8 -*-
"""克隆达妮娅音色到 Qwen-Omni-Realtime（声音复刻 / voice cloning）。

生成 voice_id 并写入 config.yaml 的 cloud_omni.voice（自动备份 + 断言）。
之后运行 tools/cloud_duplex_demo.py 即可用达妮娅音色对话。

用法：
  python tools/clone_danya_voice.py --audio path/to/danya.wav [--name danya]

音频要求（务必满足，否则复刻失败）：
  - 格式 WAV(16bit)/MP3/M4A；时长 10~20s（最长 60s）；< 10MB
  - 采样率 >= 24kHz；单声道；干净人声（无背景音乐/噪音/其他人声）
  - 中文朗读、语义连贯、至少连续 3s 无中断；可带目标情绪但别过度夸张
  - 避免政治/色情/暴力等敏感词
计费：创建音色 0.01 元/个（百炼开通 90 天内 1000 次免费）；对话按 token 计费。
"""
import os
import sys
import io
import base64
import time
import shutil
import argparse
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = os.path.join(ROOT, 'config.yaml')


def clone(audio_path, name, api_key, model, base_url):
    import requests
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")
    low = audio_path.lower()
    if low.endswith('.wav'):
        mime = 'audio/wav'
    elif low.endswith(('.mp3', '.mpeg')):
        mime = 'audio/mpeg'
    elif low.endswith('.m4a'):
        mime = 'audio/mp4'
    else:
        mime = 'audio/mpeg'
    b64 = base64.b64encode(open(audio_path, 'rb').read()).decode()
    data_uri = f"data:{mime};base64,{b64}"
    # 【2026-10-17 修复·本地克隆必失败（本次"接通即断"的根因）】
    # 百炼定制音色接口的 preferred_name 必须是**合法标识符**：ASCII 字母开头、只含字母数字，
    # 中文会被拒。此前这里直接塞角色显示名（如「达妮娅」）→ 服务端返回
    #   400 InvalidParameter: Required input 'preferred_name' is invalid
    # → 克隆失败 → 自动回退到 config 里那条旧音色 → 云端会话一连上就断（实测 42 次）。
    # 现在统一在这里做 ASCII 化 + 加音频指纹后缀（避免同名重复创建冲突）。
    import re as _re
    import hashlib as _hl
    _fp6 = _hl.md5(open(audio_path, 'rb').read()).hexdigest()[:6]
    _safe = _re.sub(r'[^A-Za-z0-9]', '', str(name or '')).lower()[:12]
    if not _safe or not _safe[0].isalpha():
        _safe = 'v' + _safe
    # 纯中文名会被清成空串/单字母 → 兜底用 danya，再拼指纹保证唯一
    if len(_safe) < 3:
        _safe = 'danya'
    preferred_name = (_safe + _fp6)[:20]
    if preferred_name != name:
        print(f"preferred_name 已自动规范化: {name!r} → {preferred_name!r}（百炼要求 ASCII 标识符）")
    url = (base_url.rstrip('/') + '/api/v1/services/audio/tts/customization')
    payload = {
        "model": "qwen-voice-enrollment",
        "parameters": {"voice_clone_mode": "normal"},
        "input": {
            "action": "create",
            "target_model": model,            # 必须与实时对话模型一致，否则合成失败
            "preferred_name": preferred_name,
            "audio": {"data": data_uri},
        },
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    print(f"正在复刻 {audio_path} -> 模型 {model} …")
    r = requests.post(url, json=payload, headers=headers, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"克隆失败 HTTP {r.status_code}: {r.text[:300]}")
    voice = r.json()["output"]["voice"]
    print("克隆成功！voice_id =", voice)
    return voice


def write_voice(voice):
    raw = open(FP, 'r', encoding='utf-8', errors='ignore').read()
    bak = FP + '.' + time.strftime('%Y%m%d_%H%M%S') + '.bak'
    shutil.copy2(FP, bak)
    lines = raw.split('\n')
    out = []
    in_block = False
    replaced = False
    for ln in lines:
        if ln.startswith('cloud_omni:'):
            in_block = True
            out.append(ln)
            continue
        if in_block:
            # 顶层键（无缩进）结束 cloud_omni 块
            if ln and not (ln.startswith(' ') or ln.startswith('\t')) and ln.strip():
                in_block = False
                out.append(ln)
                continue
            if not replaced and ln.lstrip().startswith('voice:') and ':' in ln:
                indent = ln[:len(ln) - len(ln.lstrip())]
                out.append(f"{indent}voice: {voice}")
                replaced = True
                continue
        out.append(ln)
    if not replaced:
        raise RuntimeError("未在 cloud_omni 块内找到 voice: 行，请检查 config.yaml")
    with io.open(FP, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(out))
    # 真解析校验
    d = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
    got = str(d.get('cloud_omni', {}).get('voice', '')).strip()
    assert got == voice, f"写入后解析为 {got}"
    print(f"已写入 config.yaml cloud_omni.voice（备份 {os.path.basename(bak)}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', required=True, help='达妮娅参考音频路径')
    ap.add_argument('--name', default='danya', help='音色名(字母数字下划线,<=16)')
    ap.add_argument('--model', default='qwen3.8-omni-flash-realtime')
    ap.add_argument('--base-url', default='')
    ap.add_argument('--api-key', default='')
    args = ap.parse_args()

    data = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
    co = data.get('cloud_omni', {}) or {}
    api_key = args.api_key or co.get('api_key', '') or os.getenv('DASHSCOPE_API_KEY', '')
    base_url = args.base_url or co.get('base_url', 'https://dashscope.aliyuncs.com')
    if not api_key:
        print("缺少 API Key：用 --api-key 或在 config.yaml cloud_omni.api_key 配置")
        sys.exit(2)

    try:
        voice = clone(args.audio, args.name, api_key, args.model, base_url)
    except Exception as e:  # noqa: BLE001
        print("克隆失败:", e)
        sys.exit(1)
    write_voice(voice)
    print("\n下一步：python tools/cloud_duplex_demo.py  即可用达妮娅音色体验全双工。")


if __name__ == '__main__':
    main()
