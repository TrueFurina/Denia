# -*- coding: utf-8 -*-
"""把 voice.asr.model_path 从「写死不存在的目录」改成留空。

为什么这是毛病：src/voice/asr.py:_load_model 的逻辑是
  model_path 非空 → Model(model_path)        # 目录不存在就抛异常 → ASR_INIT_FAIL
  model_path 留空 → Model(lang="zh-cn")      # vosk 自动下载中文模型（需联网）
而本机 models/ 目录压根不存在（既无 sherpa-onnx 也无 vosk），配置里却写着
`models/vosk-model-small-cn-0.22`。于是：**自动下载那条路被非空值堵死**，
她永远听不见，且只有一条 ERROR 事件，没有"配置指错了"的提示。

留空后优先走 sherpa（若models/sherpa-onnx-* 存在），否则 vosk 自动下载。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

NL = "\r\n"
OLD = ("  model_path: models/vosk-model-small-cn-0.22" + NL).encode("utf-8")
NEW = ("  model_path: ''   # 留空=运行时自动下载中文模型；指到不存在的目录反而"
       + NL + "  # 会把自动下载堵死（src/voice/asr.py:_load_model）" + NL).encode("utf-8")

for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        print(f"[跳过] {P}")
        continue
    b = open(P, "rb").read()
    n = b.count(OLD)
    print(f"{P}: 命中 {n} 处")
    if n != 1:
        continue
    shutil.copy(P, P + ".bak.asr")
    open(P, "wb").write(b.replace(OLD, NEW))
    print("  已改为留空（自动下载）")

# 验证：本机到底有哪些语音识别模型可用
print()
print("本机模型实况：")
import glob  # noqa: E402

for pat in ("models/*",):
    hits = glob.glob(pat)
    print(f"  {pat} → {hits if hits else '（models/ 目录不存在）'}")
for d in ("models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
          "models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"):
    print(f"  {d} → {'存在' if os.path.isdir(d) else '不存在'}")

sys.path.insert(0, ROOT)
import yaml  # noqa: E402

for P in ("config.yaml", "config.release.yaml"):
    d = yaml.safe_load(open(P, encoding="utf-8"))
    asr = (d.get("voice") or {}).get("asr") or {}
    print(f"{P}: asr.model_path = {asr.get('model_path')!r}")
