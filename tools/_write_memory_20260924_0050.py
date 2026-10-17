# -*- coding: utf-8 -*-
"""2026-09-24 00:50 追加：P0 完成（常驻块挂到通话档末尾）+ 验收工具 + 基线口径修正"""
import io, os

LOG = r'G:\ai对话\.workbuddy\memory\2026-09-24.md'
raw = open(LOG, 'rb').read()
nl = '\r\n' if raw.count(b'\r\n') == raw.count(b'\n') and raw.count(b'\n') else '\n'

ADD = [
    '',
    '### 00:50 P0 完成：通话档常驻块（含验收工具与基线口径修正）',
    '- **改动**：新增 `proactive.py::_build_duplex_global_prompt_call()`（240 字 / 8 条，'
    '**纯反应式**），挂到 `_build_duplex_instructions_call` 的**两个** return 点之前'
    '（_custom 分支 ~470 行 + 内置回退分支 ~512 行）。桌宠档 `_build_duplex_global_prompt` 未动。',
    '- 备份 `proactive.py._bak_p0global_20260924_004441`；LF 保持；'
    '拼装后通话档 prompt **679 字**（改前 373，原生 Comni 88）。',
    '- 内容红线（§2cq）：常驻块**零激活词**（不出现 自我介绍/名字/身份/没听清/你说什么/屏幕/画面/窗口/WorkBuddy）。'
    '要抑制背设定只能正面写「他已经听过的事，别从头再讲一遍」。',
    '- 与桌宠版的差异：桌宠版 1)「每句 20 字内」+ 5)「看场景再开口」+ 6)「不念屏幕/窗口」'
    '——前一条**压缩人格**（正是 §2ct 元凶之一），后两条看屏专用且「屏幕/窗口」对 Q4 是激活源。'
    '故**不可直接复用桌宠版**，必须另写通话版。',
    '',
    '- **新增资产**：',
    '  · `tests/test_call_global_block_tail.py`（9/9）：AST 取常驻块正文，断言挂载点数=2、'
    '均在 return 前、零激活词、含反应式条款、无压缩人格措辞、≤320 字。',
    '  · `tools/verify_p0_call.py`：打完电话跑一条命令自动判四项（见下）。'
    '**它会先从录像 system_prompt 里查「【接话方式」来确认改动是否真的生效**——'
    '避免"没重启就测"的白测。',
    '',
    '- ⚠️ **基线口径修正（重要，避免下次对不上）**：',
    '  · 「非身份证句占比」此前口头说的 **14%** 是另一种口径（只数完整自我介绍句）；'
    '  · verify_p0_call.py 按「去重句是否含设定词（星炬/学院/虚质/我叫/我的名字/我是达妮娅）」计，'
    '    22:55 那通实测 **37.5%**（8 句 5 句背设定）。**两种口径差 2.6 倍，引用时必须注明口径。**',
    '  · 出声率波动大：22:30 那通 50%，22:55 那通 100% ⇒ 不能拿单通当基线，只设下限 ≥80%。',
    '',
    '- **达标线**（P0 验收，基线均取 22:55 同口径实测）：',
    '  [0] 录像 prompt 含【接话方式 = YES ｜ [1] 非身份证占比 ≥55%（基线 37.5%）｜'
    '  [2] SPEAK 占比 30~55%（基线 26%，<30 哑火 >60 井喷）｜ [3] 出声率 ≥80% ｜'
    '  [4] 端到端中位 ≤4.0s（基线 4.78s，靠成句即送应降 ~0.8s）',
    '',
    '- ⚠️ **教训：AST 单方法提取的测试桩，新增被调方法必须同步进 grab 列表**。'
    '`test_call_prompt_and_barge.py` 用 `grab(PRO,[...])` 抽出方法 exec 到桩 Host 上，'
    '本次新增 `_build_duplex_global_prompt_call` 后未同步 ⇒ '
    "`AttributeError: 'Host' object has no attribute ...` 假红。"
    '修法：grab 列表加新方法，并顺势加断言「输出含常驻块且位于行为段之后」。',
    '- ⚠️ 所有改动**需重启 app 才生效**（提示词在会话 reprepare 时构建）。',
]

with open(LOG, 'ab') as f:
    f.write((nl + nl.join(ADD) + nl).encode('utf-8'))

b = open(LOG, 'rb').read()
print('日志 size=%d CR=%d LF=%d 纯CRLF=%s' % (
    len(b), b.count(b'\r'), b.count(b'\n'), b.count(b'\r\n') == b.count(b'\n')))
t = io.open(LOG, encoding='utf-8').read()
print('含新章:', 'P0 完成' in t, '| 含口径修正:', '两种口径差 2.6 倍' in t)
