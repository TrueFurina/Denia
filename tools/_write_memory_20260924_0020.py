# -*- coding: utf-8 -*-
"""2026-09-24 00:20 写当日日志 + 给 trace-comni-duplex-turn-taking 技能补一节。"""
import io, os

BASE = r'G:\ai对话\.workbuddy\memory'
LOG = os.path.join(BASE, '2026-09-24.md')

NOTE = """
## 00:08-00:25 双工方案选型四问（调研型，不改代码）
- 用户问：①双工模型内部 LLM 大脑是什么、能否承载人设 ②接入后为何不流畅 ③改「外接 LLM + 单工轮询」对 VR+3D/PC+3D 表情动作驱动的影响 ④落地建议。
- **产出**：`data/diag_duplex_20260924/report.html`（`tools/_mkreport_duplex_20260924.py`）。

### 硬事实（外部）
- **MiniCPM-o 4.5 = 9B：SigLIP-ViT 0.4B ＋ Whisper-medium 0.3B ＋ Qwen3-8B 主干 ＋ 语音解码器 ~0.3B（轻量 Llama）＋ 声码器**；TAIL 时间对齐交错生成。我们跑 GGUF `Q4_K_M`（11GB / 212 tok/s）。（官方技术报告 arXiv 2604.27393）
- 同代对照（arXiv 2603.05413 Table 1）：**支持全双工的开源模型主干清一色 7~9B**（Moshi 7B Helium ctx 4096 / GLM-4-Voice 9B / Freeze-Omni 7B）；30B(Qwen3-Omni)、130B(Step-Audio) 都只能轮次模式。
- **SpeechRole（arXiv 2508.02013，98 角色/111k 对话）**：级联与端到端在「互动能力／角色保真」上**相当** ⇒ 人设好坏由底层**文本 LLM** 决定，与是否端到端关系不大；开源端到端多维差距大。
- **Context Trap（IWSDS 2026）**：端到端音频 LM 多轮系统性劣于模块化，**根因＝上下文维护与话题追踪**（不是组件质量）⇒ 直击我们 8k 共享 KV。
- **DSB-IFEval（2026-09，1038 用例/8 角色）**：全双工在「规则只由人设暗示不明写」时掉 **9.7%**(F-Actor)/4.5%(PersonaPlex)；**MiniCPM-o 属「内容守人设、floor 行为不随指令变」那一簇** ⇒ 想让她"会等"必须明写规则。
- DeSRPA：端到端微调有「模态对齐税」，削弱 LLM 推理。

### 硬事实（本地）
- ★**双工路径零 3D 表情/动作下发**：`VRC_EMOTION` 只有两个下发点 `dialogue.py:411`（桌宠档，LLM 返回 JSON 的 emotion 字段）与 `webapp.py:1598`（前端按钮）。⇒ **双工通话时 3D 角色是静止的**；改级联是从"没有"变"有"，不是"损失"。
- 3D 驱动三条时钟应解耦：**表情←文本语义 / 动作←句子级语义 / 唇形←音频 / 倾听态←用户音频 RMS**（最后一条需新建）。
- 可选外接大脑：ollama `qwen3:14b`（`num_ctx` 现 4096，建议提到 8192）／云端 `glm-4.5-flash`／`deepseek-ai/DeepSeek-V3`。
- 人类轮转间隔 ~200ms（PNAS 10 语）；>800ms 用户开始插话（Retell 2026）。级联优化后 400~800ms 仍在自然区间 ⇒ 节奏损失是小档，真正不可补偿的是 **backchannel + 重叠语音**（估计整体自然度损失 10~20%，**估计值待 A/B**）。

### 结论
- Q4 量化的 8B **不足以稳定承载人设**（问"你是谁"吐设定原文＝关键词检索，非角色扮演）。
- 推荐 **外接 LLM 级联**，双工退为可选「轮转感知层」；P0 常驻块挂通话档末尾 → P1 `call_mode: cascade`（开关式可回滚）→ P2 3D 统一到文本事件总线 → P3 记忆分层 → P4 PersonaProfile 解耦。
- 回头做双工四前提：ctx≥32k 非 INT4／音色已解／级联<1s／独立预算。**当前一条不满足。**

### 维护
- MEMORY.md 23.4KB → **10.4KB**（脚本 `tools/_shrink_memory_20260924.py`；原文并入 REFERENCE.md 存档，备份 `_bak_MEMORY_20260924_0010.md`）。注意 Python 隐式字符串拼接遇 `*` 会被吞（`'a' '=' * 78` ⇒ 整串重复），格式化串一律用 `+` 拼接。
"""

if os.path.exists(LOG):
    old = io.open(LOG, encoding='utf-8').read()
else:
    old = '# 2026-09-24 工作日志\n'
io.open(LOG, 'w', encoding='utf-8').write(old.rstrip() + '\n' + NOTE)
print('日志已写', LOG, os.path.getsize(LOG))

# ---- 技能补一节 ----
SK = r'C:\Users\Administrator\.workbuddy\skills\trace-comni-duplex-turn-taking\SKILL.md'
if os.path.exists(SK):
    raw = io.open(SK, 'rb').read()
    nl = '\r\n' if raw.count(b'\r\n') == raw.count(b'\n') else '\n'
    t = raw.decode('utf-8')
    anchor = '## 8.'
    ADD = nl.join([
        '',
        '## 7c. 双工大脑规格与能力边界（先查这个，再决定要不要调参）',
        '',
        '**第一步：确认你用的双工模型主干是哪颗 LLM。** 官方技术报告会写死模块配比。',
        '已知：MiniCPM-o 4.5 = 9B（SigLIP-ViT 0.4B + Whisper-medium 0.3B + **Qwen3-8B 主干** + 语音解码器 ~0.3B）。',
        '若跑 GGUF 量化档（如 `Q4_K_M`），**实际能力低于该主干的标称能力**，调参前先把这条记在账上。',
        '',
        '**第二步：用三条外部证据判断"人设能不能稳住"，不要靠感觉。**',
        '1. SpeechRole（arXiv 2508.02013）：级联与端到端在**角色保真**上相当 ⇒ 人设好坏＝底层文本 LLM 好坏，与端到端无关。',
        '2. Context Trap（IWSDS 2026）：端到端音频 LM 多轮劣于模块化，**根因＝上下文维护与话题追踪** ⇒ 若上下文是共享 KV，先怀疑它。',
        '3. DSB-IFEval（2026-09）：全双工在"规则只由人设暗示"时掉 9.7%；MiniCPM-o 属**内容守人设、floor 行为不随指令变**的一簇 ⇒ 想让模型"会等待"必须明写规则，写在人设里无效。',
        '',
        '**第三步：查 3D/表情/动作是谁在驱动。** 全局搜 `VRC_EMOTION` / `emotion` 的下发点。',
        '常见坑：**双工路径根本不下发表情事件**（只在桌宠档或前端按钮下发）⇒ "改级联会不会破坏 3D" 是伪问题，实际是"从没有变有"。',
        '正确切法＝**三条时钟解耦**：表情←文本语义 / 动作←句子级语义 / 唇形←音频 / 倾听态←用户音频 RMS（最后一条通常要新建）。',
        '',
    ])
    i = t.find(anchor)
    if i > 0 and '7c. 双工大脑规格' not in t:
        t = t[:i] + ADD.lstrip(nl) + nl + t[i:]
        io.open(SK, 'wb').write(t.encode('utf-8'))
        print('技能已更新', SK, os.path.getsize(SK))
    else:
        print('技能跳过（已存在或找不到锚点）')
else:
    print('技能文件不存在，跳过')
