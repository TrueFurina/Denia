# -*- coding: utf-8 -*-
"""诊断：云端全双工 克隆音色 voice_id 是否被服务端接受。

连上 Qwen-Omni-Realtime，发 session.update（带克隆 voice_id），监听服务端回包，
打印 session.created / session.updated / error 里实际生效的 voice，从而判断：
  - 服务端接受克隆音色 -> 问题在参考音频质量（克隆出的声音不像达妮娅）
  - 服务端拒绝/忽略克隆音色 -> 问题在模型-音色不匹配（回退默认 Cherry）
"""
import os
import sys
import time
import json
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = os.path.join(ROOT, 'config.yaml')

import yaml
data = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
co = data.get('cloud_omni', {}) or {}
API_KEY = co.get('api_key', '')
MODEL = co.get('model', 'qwen3.8-omni-flash-realtime')
VOICE = co.get('voice', '')
WS = co.get('ws_url', '') or ('wss://dashscope.aliyuncs.com/api-ws/v1/realtime')

print(f"model = {MODEL}")
print(f"voice = {VOICE!r}")
print(f"ws    = {WS}")

if not VOICE:
    print("config 里 cloud_omni.voice 为空，无法诊断克隆音色（先用 clone 工具生成）")
    sys.exit(0)

import dashscope
from dashscope.audio.qwen_omni import OmniRealtimeConversation, OmniRealtimeCallback, MultiModality

dashscope.api_key = API_KEY

events = []
lock = threading.Lock()

class Cb(OmniRealtimeCallback):
    def on_open(self):
        print("[on_open] 连接已打开")
    def on_event(self, msg):
        with lock:
            events.append(msg)
        t = msg.get('type', '')
        if t in ('session.created', 'session.updated', 'error', 'session.update.error'):
            print("EVENT:", t)
            if 'session' in msg:
                print("   session.voice =", msg['session'].get('voice'))
            if 'error' in msg:
                print("   error =", json.dumps(msg['error'], ensure_ascii=False)[:500])
    def on_close(self, *a):
        print("[on_close]", a)
    def on_error(self, *a):
        print("[on_error]", a)

conv = OmniRealtimeConversation(model=MODEL, callback=Cb(), url=WS)
conv.connect()

# 发 session.update，带克隆 voice + 服务端 VAD
conv.update_session(
    output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
    voice=VOICE,
    turn_detection_type='server_vad',
    turn_detection_threshold=0.5,
    turn_detection_silence_duration_ms=600,
)
print("\n[已发送 session.update，等待 4s 看服务端回包...]\n")
time.sleep(4)

print("\n===== 汇总：服务端实际生效的 voice =====")
with lock:
    for e in events:
        t = e.get('type', '')
        if t in ('session.created', 'session.updated'):
            print(f"{t}: voice = {e.get('session', {}).get('voice')!r}")
print("诊断结束，关闭连接。")
conv.close()
