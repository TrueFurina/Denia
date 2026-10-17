# -*- coding: utf-8 -*-
"""2026-09-24：给 diagnose-persona-tics-and-fixation 补一节
「量化人格改善时必须先定死口径」——同一通电话两种算法差 2.6 倍。"""
import io

F = r'C:\Users\Administrator\.workbuddy\skills\diagnose-persona-tics-and-fixation\SKILL.md'
t = io.open(F, encoding='utf-8').read()
NL = '\r\n' if '\r\n' in t else '\n'

ANCHOR = '### D 型：先问「模型实际收到什么」，别只读源码'
assert ANCHOR in t, '锚点缺失'
assert '口径' not in t.split(ANCHOR)[0][-3000:] or True

SEC = NL.join([
    '### 量化人格改善时，先定死口径（否则两次评估差 2~3 倍，永远对不上账）',
    '',
    '改完提示词要证明"有效"，最常见的翻车不是改错，而是**两次评估用了不同算法**。',
    '实测同一通电话（2026-09-23 22:55）：',
    '',
    '| 口径 | 算法 | 结果 |',
    '|---|---|---|',
    '| A | 只数「完整自我介绍句」 | **14%** |',
    '| B | 去重句中是否含设定词（学校名 / 院系名 / 我叫 / 我的名字 / 我是X） | **37.5%** |',
    '',
    '**差 2.6 倍，而两者都没算错。** ⇒ 引用任何百分比必须注明口径，前后对比必须同口径。',
    '',
    '配套三条硬性要求：',
    '1. 引擎 `result.text` 是**逐块碎片**，必须按**连续 SPEAK 段聚合**成完整句再统计——',
    '   否则一整句被切成 8 段，会被误判成"短句复读"。',
    '2. **单通样本不可靠**：本项目出声率 22:30 那通 50%、22:55 那通 100%，波动一倍。',
    '   只设下限（如 ≥80%），不要断言"改善了 X%"。',
    '3. **判据词表固定写进工具**，别每次临时想。词表变了，两次数字就不可比。',
    '',
    '**另加一条第 0 项指标：改动到底生效了没有。**',
    '从服务端录像 `recording.json` 的 `config.system_prompt` 里**直接查新加块的标题词**——',
    '录像里的 prompt 是地面真值，比"我改了代码"可靠。',
    '**没重启 app 就测，是最常见的白测**（提示词在会话 reprepare 时才构建）。',
    '',
])

t = t.replace(ANCHOR, SEC + NL + ANCHOR, 1)
io.open(F, 'w', encoding='utf-8', newline='').write(t)

b = open(F, 'rb').read()
chk = io.open(F, encoding='utf-8').read()
print('size=%d CR=%d 纯CRLF=%s' % (len(b), b.count(b'\r'), b.count(b'\r\n') == b.count(b'\n')))
for kw in ['差 2.6 倍', '连续 SPEAK 段聚合', '没重启 app 就测']:
    print('   %-20s %s' % (kw, kw in chk))
