# -*- coding: utf-8 -*-
"""打开麦克风监听开关（voice.mic_enabled: false → true）。

【2026-10-19】第四处静默失效：本机 16 个输入设备就绪、ASR 模型也缓存了，
但 `voice.mic_enabled = false` → asr.py 的 _run() 开头就挂起等待
（"麦克风监听关闭，挂起等待（不采集不识别）"），**唤醒和语音全废**。
webapp.py:10036 的注释早就写着"B2 修复：默认 mic_enabled=false 导致唤醒/语音全废"，
而当前 voice.mode='clone'（要她的本音）却关着麦克风——自相矛盾，
说明这是从未被正确设置过的默认值残留，不是用户的选择。

只改 config.yaml（被 gitignore，不进提交）与进仓的 config.release.yaml 同步，
否则新用户拿到的还是"听不见"的模板。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

OLD = ("  mic_enabled: false" + "\r\n").encode("utf-8")
NEW = ("  mic_enabled: true" + "\r\n").encode("utf-8")

for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        print(f"[跳过] {P}")
        continue
    b = open(P, "rb").read()
    n = b.count(OLD)
    if n == 0 and b.count(("  mic_enabled: true" + "\r\n").encode()) == 1:
        print(f"{P}: 已是 true，跳过")
        continue
    print(f"{P}: 命中 {n} 处")
    if n != 1:
        continue
    shutil.copy(P, P + ".bak.mic")
    open(P, "wb").write(b.replace(OLD, NEW))
    print("  已改为 true（备份 .bak.mic）")

# 验证
import yaml  # noqa: E402

sys.path.insert(0, ROOT)
from src.config import load_config  # noqa: E402

for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        continue
    d = yaml.safe_load(open(P, encoding="utf-8"))
    v = d.get("voice") or {}
    print(f"{P}: mode={v.get('mode')!r} mic_enabled={v.get('mic_enabled')} "
          f"muted={v.get('muted')}")

c = load_config("config.yaml")
print(f"\n实配解析：mic_enabled={c.ns.voice.mic_enabled}，"
      f"voice_enabled 推导值={not bool(c.ns.runtime.text_mode_only) and bool(c.ns.voice.mic_enabled)}")
