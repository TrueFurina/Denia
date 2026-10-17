# -*- coding: utf-8 -*-
"""写入 2026-09-23 23:30 架构评估结论。

换行约定（实测）：MEMORY.md = CRLF；当日日志 = LF。分别构造 bytes。
"""
import io, os

MEM = r'G:\ai对话\.workbuddy\memory\MEMORY.md'
LOG = r'G:\ai对话\.workbuddy\memory\2026-09-23.md'

# ---------- 1) MEMORY.md：插入 §2cv ----------
raw = open(MEM, 'rb').read()
assert raw.count(b'\r\n') == raw.count(b'\n'), 'MEMORY.md 非纯 CRLF，中止'
txt = raw.decode('utf-8')
nl = chr(13) + chr(10)

ANCHOR = '## 3. 未修项（缺口）'
assert txt.count(ANCHOR) == 1, '锚点不唯一：%d' % txt.count(ANCHOR)

SEC = nl.join([
    '- ★**2cv 架构路线体检（23:30）：当前「双工全包＋丢弃引擎音频＋云端 TTS」＝错位组合，需换路线而非继续调参**：',
    '  - **业界陪伴类（星野/猫箱/Talkie/Character.AI 语音/Replika/AI VTuber）清一色 A 级联＝流式 ASR→强 LLM→流式克隆 TTS**',
    '    （延迟 700~950ms，优化后 400~800ms）；B 原生 S2S（GPT-Realtime ~320ms / Gemini Live ~400ms）延迟最低但人格弱、音色固定；',
    '    C 混合＝S2S 管轮转＋强 LLM 写词＋流式克隆 TTS。**我们＝D**：付了 S2S 代价（人格锁死在 Q4 量化 8B）',
    '    却关掉 `generate_audio` 没拿到零延迟与副语言，又没拿到 cascade「可换强模型/可换音色」的好处。',
    '  - **「延迟高 vs 克隆音质差」是同一矛盾的两个投影，不是两个问题**：流式小分块早出首包 ⇄ 参考音充分编码＋足够上下文。',
    '    再叠加 6~8 字填充短句音色发散（8–12kHz 低 6.48dB）与 F0 随文本漂移（兜底句 flash 252.4／plus 232.7Hz）。**调参无法两全。**',
    '  - **三段之外业界还靠五件工程**（我们基本全缺）：①记忆分层（短期 30~50 条＋长期摘要压缩；**通话档零记忆注入**——',
    '    `_build_duplex_memory` 只在测试模式分支调用）②端点检测＝VAD＋语义判句，非静音计时 ③AEC 回声消除（外放必做，未排查）',
    '    ④barge-in＋打断回填（我们现在是整句丢弃）⑤LoRA 把人设烧进权重。',
    '  - ★**最高 ROI（零风险，视频档已验证）：`_build_duplex_global_prompt()`＝人格站位＋11 条桌宠守则，只在 `proactive.py:660`',
    '    追加到视频档**末尾**；通话档 `_build_duplex_instructions_call` **第 468 行提前 return ⇒ 常驻块从未挂载**。',
    '  - **视频档为何既守人设又能看屏＝三因素**：①常驻块在末尾（弱模型对末尾指令权重最高，通话档人设却写在前头被前缀压住）',
    '    ②**有真实屏幕帧占住注意力**，不会去检索提示词；通话档只有语音，**提示词是唯一强语义源 ⇒ 就去念它**（"冰雪王国"幻觉的机理）',
    '    ③时间压力：桌宠可沉默几十秒；通话档被要求 1~2s 开口 ⇒ 弱模型退化成复读设定/短填充句。**"看屏幕"是输入源不是负担，',
    '    提示词只需写"怎么评"，硬性要求与人设互相成全。**',
    '  - **短提示词 ≠ 弱人格**：短提示词只消除"念设定"（原生 88 字 SPEAK 56.5%／我们 373 字 26.0%），**不产生人格**。',
    '    人格靠 ①**语法**：声明式（我叫达妮娅，在星炬学院）会被**念出来**，反应式（他抱怨时你先站他这边）会被**执行**；',
    '    ②**位置**：放末尾。**两者可兼得，不是二选一。**',
    '  - **取舍路线**：①挂常驻块到通话档末尾（1 天，零风险，不动引擎/TTS 参数）→ ②`duplex_native_audio: true` 做 A/B',
    '    （半天；砍 0.7~2.5s 云端往返、出声率 50%→~100%；代价＝音色换引擎烘焙音，需重烤 `prompt_cache.gguf`）→ ③转 C 混合',
    '    （S2S 只做块级裁决 85~136ms，内容交本地 32B/云端 API，本地流式克隆 TTS：`11/cosyvoice_venv` 已装；',
    '    Qwen3-TTS ~97ms／CosyVoice2 150ms~1.5s／Fish-Speech ~100ms 首包，标称值工程预期 ×2~3）。',
    '  - ⚠️**明确别再做**：提示词加"要等待/要自然/要像真人"（压开口率，已踩）、`listen_prob_scale` 两头挪（已四次）、',
    '    换 TTS 档位治"忽高忽低"（那是文本漂移，减少兜底句才有效）。',
    '',
])

txt2 = txt.replace(ANCHOR, SEC + ANCHOR)

# 索引行更新
OLD_IDX = None
for line in txt2.split(chr(10)):
    if line.startswith('> ⭐最热：'):
        OLD_IDX = line
        break
assert OLD_IDX is not None, '未找到索引行'
NEW_IDX = ('> ⭐最热：**§2cv 路线错位＝双工全包＋丢弃引擎音频＋云端 TTS；业界陪伴类清一色级联 A；'
           '最高 ROI＝把常驻块挂到通话档末尾**／**§2cs/§2ct 人格被提示词删掉；成句即送省 0.95s/turn**／'
           '**§2cl `lps` 三档＝哑火/井喷总开关**'
           + chr(13))  # split('\n') 后行尾带 CR，替换时必须补回，否则该行退化为 LF
assert OLD_IDX.endswith(chr(13)), '索引行行尾无 CR，与预期不符'
txt2 = txt2.replace(OLD_IDX, NEW_IDX)

b = txt2.encode('utf-8')
assert b.count(b'\r\n') == b.count(b'\n'), '写前校验失败'
open(MEM, 'wb').write(b)
print('MEMORY.md %d -> %d bytes' % (len(raw), len(b)))
print('  §2cv 已插入:', '2cv' in txt2)

# ---------- 2) 当日日志（LF，追加） ----------
LOG_TXT = chr(10).join([
    '',
    '## 23:30 架构级评估：双工 + 外挂 TTS 路线是否正确',
    '',
    '用户四问：①我们的路线对不对、为何反复碰壁 ②市面同类产品怎么搭链路 ③短提示词能否兼顾人设 ④视频档为何既守人设又能看屏。',
    '',
    '**核心判断：路线错位。** 我们＝D「Q4 双工全包 → 纯文本 → 云端 DashScope 克隆 TTS」：',
    '付了 S2S 的代价（人格锁死 8B 量化）却关掉 `generate_audio` 没拿零延迟，又没拿 cascade 可换模型的红利。',
    '「延迟高」与「克隆差」是同一矛盾（流式早出首包 ⇄ 参考音充分编码）的两个投影。',
    '',
    '**调研结论**：陪伴类（星野/猫箱/Talkie/Character.AI 语音/Replika/AI VTuber）清一色级联 A，',
    '700~950ms（优化 400~800）；原生 S2S 300~550ms（GPT-Realtime ~320ms）；混合 C 约 1~2s。',
    '除三段外还有五件关键工程：记忆分层、语义端点检测、AEC、barge-in 打断回填、LoRA 烧人设——我们基本全缺。',
    '',
    '**代码侧铁证**（回答第 ④ 问）：`_build_duplex_global_prompt()`（人格站位＋11 条桌宠守则）',
    '只在 `proactive.py:660` 追加到视频档末尾；`_build_duplex_instructions_call` 在 468 行提前 return ⇒ 通话档从未挂载。',
    '叠加：视频档有真实屏幕帧占住注意力（通话档提示词是唯一语义源 ⇒ 念设定）；桌宠无 1~2s 开口压力。',
    '',
    '**短提示词**：只消除念设定（原生 88 字 SPEAK 56.5% vs 我们 373 字 26.0%），不产生人格；',
    '人格靠「反应式语法（他抱怨时你… 会被执行）＋置于末尾」。两者可兼得。',
    '',
    '产出：`data/diag_arch_20260923/report.html`（七节：结论／业界链路／碰壁根因／短提示词／视频档三因素／取舍路线与 TTS 选型／一句话回答）。',
    '',
    '**待用户拍板**：①先把常驻块挂通话档（1 天，零风险）②再做 `duplex_native_audio: true` 的 A/B（决定"要快还是要像她"）',
    '③必要时转 C 混合路线。本次**未改任何代码**，仅诊断与报告。',
    '',
])

old = io.open(LOG, encoding='utf-8').read()
io.open(LOG, 'w', encoding='utf-8', newline='\n').write(old + LOG_TXT)
print('当日日志 %d -> %d chars' % (len(old), len(old) + len(LOG_TXT)))
