# -*- coding: utf-8 -*-
"""【云端全双工试听】独立 demo：麦克风 -> Qwen-Omni-Realtime -> 扬声器。

用于先“看看前沿效果”，不修改本地 P0/P1 主程序；重启主程序不受影响。
体验到的就是真·全双工：你说话时它自动停、自然接话（服务端 VAD 原生打断）。

用法：
  python tools/cloud_duplex_demo.py            # 用 config.yaml 的 cloud_omni 配置
  python tools/cloud_duplex_demo.py --mic-rate 16000 --chunk 1600
  python tools/cloud_duplex_demo.py --list-voices   # 仅列出已克隆音色（需联网）

依赖：pip install "dashscope>=1.26.5" pyaudio requests
退出：Ctrl+C
"""
import os
import sys
import time
import argparse
import types
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FP = os.path.join(ROOT, 'config.yaml')


def _load_cloud_omni():
    """按文件直接加载模块，绕开 src/llm/__init__（不依赖 ollama 等兄弟模块）。"""
    import importlib.util
    p = os.path.join(ROOT, 'src', 'llm', 'cloud_omni_realtime.py')
    spec = importlib.util.spec_from_file_location('cloud_omni_realtime', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CloudOmniRealtime


def load_cloud_cfg():
    data = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mic-rate', type=int, default=16000)
    ap.add_argument('--chunk', type=int, default=1600, help='每帧采样数(16k下1600=100ms)')
    ap.add_argument('--list-voices', action='store_true', help='仅列出已克隆音色后退出')
    args = ap.parse_args()

    data = load_cloud_cfg()
    co = data.get('cloud_omni', {}) or {}

    if args.list_voices:
        try:
            import requests
        except Exception:  # noqa: BLE001
            print("缺少 requests，请 pip install requests"); sys.exit(2)
        base = (co.get('base_url', 'https://dashscope.aliyuncs.com')).rstrip('/')
        key = co.get('api_key', '') or os.getenv('DASHSCOPE_API_KEY', '')
        url = base + '/api/v1/services/audio/tts/customization'
        r = requests.post(url, json={"model": "qwen-voice-enrollment",
                                    "input": {"action": "list", "page_size": 20, "page_index": 0}},
                          headers={"Authorization": f"Bearer {key}",
                                    "Content-Type": "application/json"}, timeout=30)
        print("HTTP", r.status_code)
        print(r.text[:800])
        return

    # 用与 app 相同的逻辑构造客户端（自动读人格/行为文件 + 默认规则）
    ns = types.SimpleNamespace(**{
        k: (types.SimpleNamespace(**v) if isinstance(v, dict) else v)
        for k, v in data.items()
    })
    CloudOmniRealtime = _load_cloud_omni()
    bot = CloudOmniRealtime.from_config(ns, ROOT)
    if not bot.enabled:
        print("云端全双工未启用：请在 config.yaml 填写 cloud_omni.api_key（复用 CosyVoice/级联那把 DashScope Key）")
        sys.exit(2)

    # 文本回显
    bot._on_user_text = lambda t: print(f"\n[用户] {t}", flush=True)
    bot._on_assistant_text = lambda t: print(f"[助手] {t}", flush=True)

    print(f"模型={bot.model}  voice={bot.voice or '<预设/默认>'}  ws={bot.ws_url}")
    print("连接中…")
    try:
        bot.connect()
    except Exception as e:  # noqa: BLE001
        print(f"连接失败：{type(e).__name__}: {e}")
        print("排查：1) pip install \"dashscope>=1.26.5\" pyaudio requests；"
              "2) 若公共域名连不通，把 config 的 cloud_omni.ws_url 改成工作空间专属域名；"
              "3) 确认 DASHSCOPE_API_KEY 有效。")
        sys.exit(1)

    try:
        import pyaudio
    except Exception:  # noqa: BLE001
        print("缺少 pyaudio，请 pip install pyaudio（需系统 PortAudio）"); sys.exit(2)

    pa = pyaudio.PyAudio()
    try:
        mic = pa.open(format=pyaudio.paInt16, channels=1, rate=args.mic_rate,
                     input=True, frames_per_buffer=args.chunk)
    except Exception as e:  # noqa: BLE001
        print(f"打开麦克风失败：{e}"); sys.exit(2)

    print("\n===== 对话开始，对麦克风说话（Ctrl+C 退出）=====\n")
    try:
        while True:
            data_audio = mic.read(args.chunk, exception_on_overflow=False)
            bot.send_audio(data_audio)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n===== 对话结束 =====")
        try:
            bot.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            mic.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            pa.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == '__main__':
    main()
