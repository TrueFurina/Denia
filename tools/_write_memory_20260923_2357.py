# -*- coding: utf-8 -*-
"""写入 2026-09-23 23:57 架构决策结论（§2cw）。

换行约定（实测）：MEMORY.md = CRLF；当日日志 = LF。
坑（本轮新踩）：split('\\n') 得到的行尾带 CR，替换索引行时必须补回 chr(13)，否则该行退化为 LF。
"""
import io

MEM = r'G:\ai对话\.workbuddy\memory\MEMORY.md'
LOG = r'G:\ai对话\.workbuddy\memory\2026-09-23.md'

# ---------- 1) MEMORY.md ----------
raw = open(MEM, 'rb').read()
assert raw.count(b'\r\n') == raw.count(b'\n'), 'MEMORY.md 非纯 CRLF，中止'
txt = raw.decode('utf-8')
nl = chr(13) + chr(10)

ANCHOR = '## 3. 未修项（缺口）'
assert txt.count(ANCHOR) == 1, '锚点不唯一：%d' % txt.count(ANCHOR)

SEC = nl.join([
    '- ★**2cw 架构决策（23:57）：不死磕全双工，先降级半双工级联 —— 且级联组件全在仓库，是"接线"不是"研发"**：',
    '  - **级联四件套已在跑**：`src/voice/asr.py`（sherpa-onnx 流式优先 + vosk 回退）／',
    '    `src/voice/human_voice_detector.py`（VAD）／`src/llm/openai_client.py` + ollama／',
    '    `src/voice/cosyvoice_server.py`（**CosyVoice2 零样本，已实现 `stream=True` + `instruct` 情感指令**）。',
    '  - **8k 是共享 KV cache**：`vram_profile.py:95` 通话档 `ctx_size: 8192`（另两档 4096）。',
    '    全双工下音频历史与文本<b>争抢同一份</b>，会话越久留给"人设+记忆"越少 ⇒ **这就是"长期记忆塞不进去、',
    '    人格连续性差"的机制**。级联下 8192 全是文本，可用量翻三倍以上。',
    '  - **纠正「效果差＝外挂 TTS 的锅」**：音色像不像＝外挂 TTS 的功劳；延迟＝它是主因之一；韵律＝部分；',
    '    **人格与智能＝与 TTS 完全无关**（文本是 Q4 8B 生成的）。⇒ 打开 `generate_audio` 只改善延迟与韵律，',
    '    **不改善人格**。别把力气投错地方。',
    '  - **「可打断」是播放层机制，不是模型层机制**：播放时麦克风持续采集 + VAD → 停播清队列＝barge-in。',
    '    级联打断延迟 100~200ms，**比全双工 100~300ms 更低**（不经过模型推理）。全双工独有优势只剩"感知语气/犹豫"。',
    '  - **级联延迟预算**：端点 150~300 + 流式 ASR 100~250 + LLM 首 token 200~600 + 成句 100~300 +',
    '    本地流式 TTS 150~1500 + 播放 50~150 ＝ **0.75~3.1s**（云端 API + 本地 TTS 可压到 1.0~1.5s）vs 现状 3.76~9.40s。',
    '  - **路线 P0~P4**：P0 常驻块挂通话档（1~2 天，零风险）→ **P1 半双工通话链路 `call_mode: cascade`，',
    '    与双工并存、可 A/B 一键回滚（3~5 天）** → P2 滚动摘要（每 8~10 轮）+ 外部向量检索 top-3（3~5 天）→',
    '    P3 PersonaProfile 解耦热切换 → P4 全双工降级为"轮转感知层"（可选）。',
    '  - ⚠️**音色绝不能烘焙进 `prompt_cache`**：一旦参考音进 KV cache / prompt_cache，换音色就得重建会话，',
    '    热切换直接废。必须保留"每次请求携带参考音"的旁路（音色切换应即时生效、不重建；人设切换才重建）。',
    '  - **回头做全双工的四前提**（当前一条都不满足）：ctx ≥ 32k 且非 Q4／接受音色由模型定或已解决烘焙链路／',
    '    级联延迟已压到 1s 内／有独立工程预算。',
    '',
]).replace('<b>', '').replace('</b>', '')

txt2 = txt.replace(ANCHOR, SEC + ANCHOR)

# 索引行（行尾带 CR，替换时必须补回）
OLD_IDX = None
for line in txt2.split(chr(10)):
    if line.startswith('> ⭐最热：'):
        OLD_IDX = line
        break
assert OLD_IDX is not None and OLD_IDX.endswith(chr(13)), '索引行异常'
NEW_IDX = ('> ⭐最热：**§2cw 不死磕全双工→降级级联；级联四件套已在仓库（sherpa/VAD/LLM客户端/CosyVoice2流式），'
           '8k 是共享 KV cache**／**§2cv 常驻块从未挂通话档＝最高 ROI**／'
           '**§2cs/§2ct 人格被提示词删掉**／**§2cl `lps` 三档总开关**' + chr(13))
txt2 = txt2.replace(OLD_IDX, NEW_IDX)

b = txt2.encode('utf-8')
assert b.count(b'\r\n') == b.count(b'\n'), '写前校验失败'
open(MEM, 'wb').write(b)
print('MEMORY.md %d -> %d bytes；§2cw 已插入：%s' % (len(raw), len(b), '2cw' in txt2))

# ---------- 2) 当日日志（LF 追加） ----------
LOG_TXT = chr(10).join([
    '',
    '## 23:57 架构取舍：全双工 vs 半双工级联（决策文档）',
    '',
    '用户四问：①两方案在延迟/打断/人格一致性上的对比，8k 上下文下哪种划算 ②8k 内人设与记忆持久化 ③人设音色解耦热切换 ④是否继续死磕全双工。',
    '',
    '**结论：不死磕全双工，先降级级联。全双工降级为可选增强层。**',
    '',
    '**决定性论据 —— 级联组件全在仓库，切换是"接线"不是"研发"**：',
    '`src/voice/asr.py`（sherpa-onnx 流式优先 + vosk 回退）／`src/voice/human_voice_detector.py`（VAD）／',
    '`src/llm/openai_client.py` + ollama／`src/voice/cosyvoice_server.py`（CosyVoice2 零样本，已支持 stream=True + instruct）。',
    '',
    '**8k 上下文是共享 KV cache**（`vram_profile.py:95`，通话档 ctx_size 8192）：',
    '全双工下音频历史与文本争抢，会话越久留给"人设+记忆"越少 —— 这就是用户说的"长期记忆塞不进去"。',
    '级联下 8192 全是文本，可用量翻三倍以上。这一项上级联是碾压级优势。',
    '',
    '**纠正用户假设「效果差是外挂 TTS 的锅」**：四维归因后结论相反 ——',
    '音色像不像＝外挂 TTS 的功劳；延迟＝它是主因之一；韵律＝部分；**人格与智能＝完全无关**。',
    '⇒ 打开 generate_audio 只改善延迟与韵律，不改善人格。',
    '',
    '**「可打断」是播放层机制不是模型层机制**：barge-in（播放时麦克风采集 + VAD → 停播清队列）',
    '延迟 100~200ms，比全双工 100~300ms 更低。全双工独有优势只剩"感知语气/犹豫"。',
    '',
    '产出：`data/diag_decision_20260923/report.html`（七节：结论／归因纠正／三维度对比／8k 记忆方案与预算表／解耦模型与热切换／P0-P4 路线／回头做全双工的四前提）。',
    '',
    '⚠️ 关键设计红线：**音色绝不能烘焙进 prompt_cache**，否则换音色要重建会话、热切换作废。',
    '',
    '本次**未改任何代码**，仅诊断与决策文档。下一步待用户拍板是否启动 P0 + P1。',
    '',
])

old = io.open(LOG, encoding='utf-8').read()
io.open(LOG, 'w', encoding='utf-8', newline='\n').write(old + LOG_TXT)
print('当日日志 %d -> %d chars' % (len(old), len(old) + len(LOG_TXT)))
