# -*- coding: utf-8 -*-
"""把本轮 ASR 修复写进 CHANGELOG：1 行速览 + 1 个明细小节（插在最前面）。

上一轮的教训：插入脚本对**每个** `### ` 都执行了一次插入，把 CHANGELOG
撑到 3136 行、同一小节出现 42 次。所以这里显式只处理"第一个 `### `"，
并在写入前 assert 标题不存在、写入后 assert 只出现 1 次。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "CHANGELOG.md"
raw = open(P, "rb").read()
NL = "\r\n" if raw.count(b"\r\n") else "\n"
lines = raw.decode("utf-8").split(NL)

TITLE = "### 2026-10-19 · 她也听不见：vosk 语言代码写错 + model_path 堵死自动下载"

BODY = """**入口**："阿里云没钱"→ 修完"能想"（LLM 迁免费源）和"能说"（TTS 落静音后端）
之后，接着问"**那她现在听得见吗**"。一查，`models/` 目录压根不存在，
而配置里写着 `model_path: models/vosk-model-small-cn-0.22`。

**两个 bug，任何一处都足以让她永远听不见**：
1. `src/voice/asr.py:_load_model` 写 `Model(lang="zh-cn")`，而 vosk 只认 `"cn"`
   → 实测报 `lang zh-cn does not exist`。也就是说代码里那条
   "model_path 留空就自动下载中文模型"的兜底**从来没通过**。
2. 配置把 `model_path` 写成一个不存在的目录 → `Model(路径)` 必抛异常，
   **非空值又把自动下载那条路堵死**。两口一堵，日志里只有一条
   ASR_INIT_FAIL，没有任何"配置指错了"的提示。

**修复**：`lang="zh-cn"` → `"cn"`；`config.yaml` / `config.release.yaml` 的
`model_path` 置空（首次监听自动下载约 42MB，落 `~/.cache/vosk/`，
第二次起本地秒开）。

**闭环实测**（`tools/_qc_asr_roundtrip.py`，念一句→识别一句，不是静态查文件）：
pyttsx3 念"你好呀，我现在能听见了" → 重采样 16k 单声道 → 走**项目自己的**
`_load_model` → vosk 识别结果 `'你好呀我现在能听见了'`，与原文完全一致。
自动下载实测 64.4s / 41.9MB，之后本地加载。

**新增 doctor 检查「语音识别」**：model_path 非空但目录不存在 → **FAIL**
（并说明它把自动下载也堵死了）；目录在但加载失败 → FAIL；sherpa 模型就绪
或 vosk 缓存在且能加载 → PASS；两处都没有 → WARN（首次需联网下载）。
这里**不真去下载** 42MB，否则 doctor 会卡一分钟。
实测：`[PASS] 语音识别 vosk 中文模型已缓存（~/.cache/vosk）`。

**测试**：`test_asr_voice_pipeline.py`（打桩抓语言代码 + 真加载 + 配置回归）
与 `test_doctor_asr.py` 7 组。**变异 6 刀全红**（`tools/_qc_asr_mutation.py`）。
其中两刀一开始没抓住，都是**测试自身的盲区**：
③ 刀挂错了测试文件（改 asr.py 却去跑 doctor 的测试）；
⑥ 刀的断言写成"本机已有模型就跳过"，于是把"变异造成的 PASS"和
"本机真有模型的 PASS"混为一谈——改成强制打桩"两处都没有"后才红。
门禁 **40 → 42 项全过、0 跳过**；doctor 16 PASS / 2 WARN / 0 FAIL。"""

ROW = ("| `TBD7` | 01:0x | fix | **她也听不见**：`Model(lang=\"zh-cn\")` 是无效语言代码"
       "（vosk 只认 cn）+ `model_path` 指向不存在的目录把自动下载堵死 | "
       "闭环念一句→识别一句全对；6 刀变异全红；门禁 40→42 项 |")

assert TITLE not in raw.decode("utf-8"), "小节已存在，别重复插"
first = next(i for i, l in enumerate(lines) if l.startswith("### "))

out = []
for i, ln in enumerate(lines):
    if i == first:
        out.append(TITLE)
        out.extend(BODY.split("\n"))
        out.append("")
    if ln.startswith("| `3563e30` |"):
        out.append(ROW)
    out.append(ln)

text = NL.join(out)
assert text.count(TITLE) == 1, "标题出现次数不是 1"
assert text.count(ROW.split(" | ")[0]) == 1, "速览行插入异常"
open(P, "w", encoding="utf-8", newline="").write(text)
print("已写入：速览 1 行 + 明细小节 1 个")
print("总行数", len(lines), "->", len(text.split(NL)))
