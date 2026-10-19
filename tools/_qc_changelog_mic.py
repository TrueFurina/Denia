# -*- coding: utf-8 -*-
"""把"打开麦克风监听"写进 CHANGELOG：1 行速览 + 1 个明细小节（带 assert）。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "CHANGELOG.md"
raw = open(P, "rb").read()
NL = "\r\n" if raw.count(b"\r\n") else "\n"
text = raw.decode("utf-8")
lines = text.split(NL)

TITLE = "### 2026-10-19 · 第四处：麦克风开关关着——她根本不在采集"

BODY = """**来由**：「能听」那条链修到模型就绪后，顺手查最后一环：**本机有麦克风吗、
开关开着吗**。结果：16 个输入设备就绪（默认 Realtek 麦克风）、vosk 模型
已缓存，但 `voice.mic_enabled = false` → `asr.py:_run()` 开头就
"麦克风监听关闭，挂起等待（不采集不识别）"，**唤醒和语音全废**。

**这不是我猜的**：`webapp.py:10036` 的注释自己写着
"B2 修复：默认 mic_enabled=false 导致唤醒/语音全废"——同一个坑踩第二次。
而当前 `voice.mode = 'clone'`（要她的本音）却关着麦克风，**自相矛盾**，
说明这是从未被正确设置过的默认值残留，不是用户的选择。

**修复**：`config.yaml` / `config.release.yaml` 的 `mic_enabled` 改 true。
doctor 的「语音识别」新增收口：模型就绪但麦克风关着 → 降 WARN，
detail 里同时说清"模型 OK + 麦克风关着"，fix 指到设置项。
判 WARN 不判 FAIL：用户有权主动关掉"不想被监听"；FAIL 留给配置指错。

**测试**：`test_doctor_asr.py` 补 5b/5c 两组。这里又踩到一个**断言陷阱**：
第一版写成"lv != PASS 或 detail 不含麦克风字样"，逻辑写反了——
麦克风被改成"不降级"后 lv 恰好变成 PASS、detail 恰好不含麦克风，
断言反而全绿。改成**打桩强制模型就绪**（让含 `sherpa-onnx` 的路径判定存在），
断言才有确定性：mic=False 必须 WARN 且含"麦克风"，mic=True 必须 PASS 且不含。
配套变异刀 ⑥b（改回"麦克风关着也判 PASS"）从漏抓变为必红。

变异 **6 → 7 刀全红**。门禁 **44 项全过、0 跳过**；doctor 16 PASS / 2 WARN / 0 FAIL。"""

ROW = ("| `TBD9` | 01:4x | fix | **第四处静默失效**：`mic_enabled=false` → 唤醒/语音全废"
       "（注释里早有「B2 修复」，同一个坑踩第二次）→ 打开开关 + doctor 收口提示 | "
       "7 刀变异全红；门禁 44 项全过 |")

assert TITLE not in text, "小节已存在"
first = next(i for i, l in enumerate(lines) if l.startswith("### "))

out = []
for i, ln in enumerate(lines):
    if i == first:
        out.append(TITLE)
        out.extend(BODY.split("\n"))
        out.append("")
    if ln.startswith("| `5d2a6ef` |"):
        out.append(ROW)
    out.append(ln)

text2 = NL.join(out)
assert text2.count(TITLE) == 1 and text2.count(ROW.split(" | ")[0]) == 1
open(P, "w", encoding="utf-8", newline="").write(text2)
print("已写入：速览 1 行 + 明细小节 1 个")
