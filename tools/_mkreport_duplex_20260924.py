# -*- coding: utf-8 -*-
"""2026-09-24 00:15 生成《双工模型大脑 / 流畅度归因 / 3D 驱动影响 / 落地建议》四问报告。"""
import io, os

OUT = r'G:\ai对话\11\data\diag_duplex_20260924'
os.makedirs(OUT, exist_ok=True)

CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f7f8fa;color:#1a1d24;
     line-height:1.75;margin:0;padding:32px 20px}
.wrap{max-width:960px;margin:0 auto;background:#fff;border-radius:14px;padding:38px 46px;
      box-shadow:0 2px 20px rgba(0,0,0,.06)}
h1{font-size:26px;margin:0 0 6px;color:#111}
.sub{color:#6b7280;font-size:14px;margin-bottom:26px}
h2{font-size:20px;margin:34px 0 12px;padding-left:11px;border-left:4px solid #3b6ef6;color:#1a2a4a}
h3{font-size:16px;margin:22px 0 8px;color:#243b6b}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
th,td{border:1px solid #e3e6ec;padding:8px 11px;text-align:left;vertical-align:top}
th{background:#f0f3f9;font-weight:600;color:#243b6b}
tr:nth-child(even) td{background:#fafbfd}
.bad{color:#c0392b;font-weight:600}
.good{color:#1e8e5a;font-weight:600}
.warn{color:#b7791f;font-weight:600}
.k{background:#eef2ff;border-left:3px solid #3b6ef6;padding:11px 15px;margin:14px 0;border-radius:0 6px 6px 0;font-size:14px}
.k b{color:#243b6b}
ul{margin:8px 0 8px 4px;padding-left:20px}
li{margin:5px 0}
code{background:#f2f4f8;padding:1px 5px;border-radius:3px;font-size:13px}
.small{font-size:13px;color:#6b7280}
.tag{display:inline-block;background:#eef2ff;color:#3b6ef6;border-radius:4px;padding:1px 7px;font-size:12px;margin-right:5px}
.tag.g{background:#e8f7ef;color:#1e8e5a}
.tag.r{background:#fdeceb;color:#c0392b}
.tag.y{background:#fdf5e6;color:#b7791f}
"""

H = []
A = H.append

A("""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>双工语音方案选型 · 四问结论</title><style>%s</style></head><body><div class="wrap">
<h1>双工模型大脑 / 流畅度归因 / 3D 驱动影响 / 落地建议</h1>
<div class="sub">生成时间 2026-09-24 00:15 · 依据：官方技术报告 arXiv 2604.27393、S2S 模型综述 arXiv 2603.05413、
SpeechRole arXiv 2508.02013、Context Trap (IWSDS 2026)、DSB-IFEval (2026-09)，
以及本项目 config.yaml / 源码 / 服务端录像实测</div>""" % CSS)

# ---------------- Q1 ----------------
A("""<h2>Q1　双工模型内部搭载的 LLM 大脑是什么？能否稳定承载人设？</h2>

<h3>结论</h3>
<div class="k"><b>我们用的 MiniCPM-o 4.5，主干就是 Qwen3-8B，跑的是 INT4/Q4_K_M 量化版。</b>
8B 级模型做「照着明确指令说话」够用，做「从人设推断该怎么反应」不够——
而我们产品要的恰恰是后者。叠加 Q4 量化 + 8k 共享 KV + 1Hz 块级裁决后，
<b>不足以稳定承载人设</b>。这不是调参能解决的，是模型容量问题。</div>

<h3>1.1 内部构成（官方技术报告已公开）</h3>
<table>
<tr><th>模块</th><th>规格</th><th>职责</th></tr>
<tr><td>视觉编码器</td><td>SigLIP-ViT 0.4B</td><td>看</td></tr>
<tr><td>音频编码器</td><td>Whisper-medium 0.3B</td><td>听</td></tr>
<tr><td><b>LLM 主干</b></td><td><b>Qwen3-8B</b></td><td><b>思考 / 出文本 token</b></td></tr>
<tr><td>语音 token 解码器</td><td>~0.3B（轻量 Llama 架构）</td><td>文本 → 语音单元</td></tr>
<tr><td>声码器</td><td>—</td><td>语音单元 → 波形</td></tr>
<tr><td colspan="3" class="small">总 9B。设计要点：<b>LLM 只生成文本 token，语音合成外包给小解码器</b>，
避免大模型被声学任务拖累；模块间 token 级稠密连接。语音生成用 TAIL（时间对齐交错）保证音频与上下文对齐。
我们本地跑 GGUF Q4_K_M：约 11GB 显存、212 tok/s。</td></tr>
</table>

<h3>1.2 同代模型「大脑」对照</h3>
<table>
<tr><th>模型</th><th>层级</th><th>规模</th><th>主干 / 备注</th><th>全双工</th></tr>
<tr><td><b>MiniCPM-o 4.5</b>（我们用）</td><td>Level 2</td><td>9B</td><td><b>Qwen3-8B</b></td><td class="good">✓</td></tr>
<tr><td>Moshi</td><td>Level 1（原生语音思考）</td><td>7B</td><td>Helium 7B，<b>ctx 仅 4096</b>，MMLU 54.3</td><td class="good">✓</td></tr>
<tr><td>Qwen3-Omni</td><td>Level 2</td><td>30B-A3B</td><td>MoE Thinker-Talker，首个支持 function calling</td><td>✗</td></tr>
<tr><td>Qwen2.5-Omni</td><td>Level 2</td><td>7B</td><td>Thinker-Talker</td><td>✗</td></tr>
<tr><td>GLM-4-Voice</td><td>Level 2</td><td>9B</td><td>GLM-4-9B</td><td>✗</td></tr>
<tr><td>Kimi-Audio / LLaMA-Omni / Freeze-Omni</td><td>Level 2</td><td>7–8B</td><td>—</td><td>部分</td></tr>
<tr><td>Step-Audio</td><td>Level 2</td><td>130B</td><td>开源最大</td><td>✗</td></tr>
<tr><td colspan="5" class="small">来源：arXiv 2603.05413 Table 1（S2S 模型综述）。<b>Level 1＝以语音 token 思考；Level 2＝文本 LLM + 语音编解码器；Level 3＝ASR→LLM→TTS 级联。</b>
注意：<b>支持全双工的开源模型，主干清一色是 7–9B</b>；30B/130B 那一档都只能走轮次模式。</td></tr>
</table>

<h3>1.3 能力边界：三条可引用的公开证据</h3>
<table>
<tr><th>来源</th><th>结论（对我们有意义的部分）</th><th>指向</th></tr>
<tr><td><b>SpeechRole</b><br><span class="small">arXiv 2508.02013<br>98 角色 / 111k 对话</span></td>
<td>级联与端到端在「互动能力」「<b>角色保真</b>」上<b>结果相当</b>——说明这两项主要由底层<b>文本 LLM</b> 决定，
与语音建模方式关系不大；开源端到端模型在多维上<b>差距明显</b>；最强端到端（GPT-4o Audio）流畅自然最好，
但韵律一致性、情绪恰当性仍有限</td><td class="warn">人设好坏 = 大脑好坏，<br>不是「双不双工」</td></tr>
<tr><td><b>Context Trap</b><br><span class="small">IWSDS 2026</span></td>
<td>端到端音频 LM 在多轮对话上<b>系统性劣于模块化方案</b>，且随对话推进持续退化；
根因是<b>上下文维护与话题追踪</b>，不是某个组件质量差</td><td class="bad">直击我们<br>8k 共享 KV 的软肋</td></tr>
<tr><td><b>DSB-IFEval</b><br><span class="small">2026-09，1038 用例 / 8 角色</span></td>
<td>全双工模型在「规则只由<b>人设暗示</b>、不明写」时，指令遵循掉 <b>9.7%</b>(F-Actor) / 4.5%(PersonaPlex)；
而 <b>GPT-Realtime / MiniCPM-o / Fun-Audio-Chat 这一簇：内容能守人设，但 floor（轮转）行为不随指令变化</b></td>
<td class="bad">人设 ≠ 行为规则；<br>想让她"会等"必须明写</td></tr>
<tr><td><b>DeSRPA</b></td><td>端到端微调存在「<b>模态对齐税</b>」，会削弱 LLM 固有推理能力</td><td class="warn">越端到端，<br>推理越打折</td></tr>
</table>

<h3>1.4 我们自己的实证（与上面互相印证）</h3>
<ul>
<li>22:55 通话 47 秒，去重后 7 句，<b>6 句是自我介绍</b>；0 句关心 / 玩笑 / 反问。
问「你是谁」→ 吐设定原文：这是<b>关键词检索</b>，不是角色扮演。</li>
<li>提示词里写「没听清就直接说没听清」→ 她照念「没听清，再说一遍哦」；
写「严禁问『你说什么』」→ 她说「你说什么？」。<b>提示词里的词会被念出来，正反都算。</b></li>
<li>同一模型（Q4 8B）在视频档能守人设，在通话档不行 —— 差别只在<b>常驻块挂在末尾</b>与<b>有真实屏幕帧占住注意力</b>。
⇒ 说明不是模型完全没有能力，而是<b>容量不足以在弱约束下稳定发挥</b>。</li>
</ul>

<h3>1.5 可验证方法（不靠感觉判断）</h3>
<ul>
<li><b>人格稳定性测试</b>：固定 10 个问题（你是谁／你喜欢什么／我刚才说啥／安慰我／讲个笑话…），
同一提示词跑 20 遍，统计「去重后非身份证句数占比」与「含反问/情绪词句数」。目标 ≥60%，当前 ~14%。</li>
<li><b>量化影响测试</b>：同一 prompt 在 Q4_K_M 与 Q6_K/fp16 各跑 20 遍，比上述指标。
<span class="tag y">不确定</span>INT4 对角色扮演的具体损失幅度无公开消融，需自测。</li>
<li><b>位置测试</b>：人设块放开头 vs 放末尾，各 20 遍，比指标。已预判末尾显著更好（视频档证据）。</li>
</ul>
""")

# ---------------- Q2 ----------------
A("""<h2>Q2　接入双工后不够流畅：五角度归因</h2>

<div class="k"><b>一句话：模型推理本身不慢（块级裁决仅 85~136ms），慢在「它什么时候决定开口」，
以及我们把引擎已经生成的音频丢掉、再走一趟云端 TTS。</b>
下面五条按「对体验的贡献」排序，每条都给了实测值与验证方法。</div>

<h3>2.1 模型能力：不是算力慢，是开口概率低</h3>
<table>
<tr><th>指标</th><th>我们</th><th>原生 Comni（同引擎）</th><th>含义</th></tr>
<tr><td>块级裁决耗时</td><td>85~136ms</td><td>36~60ms</td><td>推理本身很快，不是瓶颈</td></tr>
<tr><td>SPEAK 占比</td><td class="bad">26.0%</td><td class="good">44.7~56.7%</td><td>开口延迟 ≈ 1 / 占比</td></tr>
<tr><td>引擎起手（用户停→首字）</td><td class="bad">1.13~6.65s</td><td>—</td><td><b>占端到端 53%</b></td></tr>
<tr><td>系统提示词长度</td><td>373 字（原 1663）</td><td>88 字</td><td>越长越容易「念设定」</td></tr>
</table>
<p class="small">机制：<code>listen_prob_scale</code>（lps）压制 SPEAK 概率。lps=0.7 时 SPEAK 仅 13.0%，
表现为「长时间不理人」；lps 调低想治哑火，在 2Hz 档又会井喷（lps=0.3 → SPEAK 90.2%）。
<b>这颗旋钮今天已经踩过四次，两头都试过，都失败。</b></p>

<h3>2.2 端到端链路延迟：三段账</h3>
<table>
<tr><th>段</th><th>实测</th><th>性质</th><th>状态</th></tr>
<tr><td>引擎起手</td><td>1.13 / <b>6.65</b> / 2.15 / 2.16s</td><td>模型轮转决策</td><td class="bad">结构性，调参无效</td></tr>
<tr><td>等整句（首字→送 TTS）</td><td>1.94~1.97s</td><td>纯白等（只在 is_listen 才送）</td><td class="good">已修「成句即送」，省 0.81s</td></tr>
<tr><td>TTS 首包</td><td>0.66~0.81s</td><td>云端往返</td><td>达标；换本地可再降</td></tr>
<tr><td colspan="4">端到端：<b>3.76 / 9.40 / 4.77 / 4.78s</b>（修完约 2.81 / 8.45 / 3.83s）· 目标 ≤2s</td></tr>
</table>
<p class="small">另有：<b>整句非流式合成 → 生成到出声最长 22 秒</b>（§2ch），以及 <code>deferred_finalize=false</code> 白丢 30~50ms。</p>

<h3>2.3 VAD 与打断：全双工路径根本没有 VAD</h3>
<ul>
<li><b>铁证：</b>引擎 <code>StreamingVAD</code> 的调用点全部在 <code>HalfDuplex</code>（worker.py:1454/1501/1595/1724）。
<b>全双工档不做任何「用户说完没有」的检测</b>，完全靠模型在每块内部裁决一次。<code>vad/</code> 目录存在 ≠ duplex 用它。</li>
<li>因此「慢理人」不是 VAD 阈值问题，是<b>模型自己没决定开口</b>；改 VAD 参数无效。</li>
<li>打断（barge-in）走客户端 RMS 判定：<code>_barge_min_rms</code> 原 0.07（等于永不触发，用户实测 RMS 0.018~0.074），
已改 0.03 待观效。当前打断 = <b>整句丢弃、无回填</b>。</li>
</ul>

<h3>2.4 上下文管理：8k 是共享 KV cache（人格连续性差的机制）</h3>
<div class="k"><code>vram_profile.py:95</code>：通话档 <code>ctx_size: 8192</code>。
双工下<b>音频历史与文本争抢同一份 KV cache</b>——会话越久，留给「人设 + 记忆」的越少。
这就是「长期记忆塞不进去、人格连续性差」的<b>机制</b>，不是提示词写得不够好。<br>
另：<code>_build_duplex_memory</code> <b>只在测试模式分支被调用</b> ⇒ 通话档实际<b>零记忆注入</b>。</div>

<h3>2.5 音频传输</h3>
<ul>
<li>音频块 0.5s（16044 字节）vs <b>官方 1000ms/块</b>。用 0.5 时实测裁决 2.03Hz、SPEAK 52~85%（井喷）。</li>
<li><code>generate_audio: false</code> + <code>duplex_native_audio: false</code>：
<b>引擎已经生成的音频被直接丢掉</b>，改由外部云端 TTS 重新合成 ⇒ 多一次网络往返、丢掉副语言（语气/笑声/犹豫）。</li>
<li><code>deferred_finalize: false</code>（引擎默认 true）⇒ 白丢 30~50ms/块。</li>
</ul>

<h3>2.6 归因排序（按可修复性与收益）</h3>
<table>
<tr><th>#</th><th>因子</th><th>贡献</th><th>可修？</th><th>动作</th></tr>
<tr><td>1</td><td>引擎起手（模型何时开口）</td><td class="bad">~53%</td><td class="bad">调参无效</td><td>换架构（让外接 LLM 决定内容）</td></tr>
<tr><td>2</td><td>整句非流式合成</td><td class="bad">最长 22s</td><td class="good">可修</td><td>TTS 输出流式化</td></tr>
<tr><td>3</td><td>丢引擎音频 + 云端往返</td><td class="warn">0.7~2.5s</td><td class="good">可修</td><td>本地流式 TTS 或开 generate_audio</td></tr>
<tr><td>4</td><td>等整句</td><td class="warn">1.95s</td><td class="good">已修</td><td>成句即送</td></tr>
<tr><td>5</td><td>8k 共享 KV + 零记忆注入</td><td class="bad">人格连续性</td><td class="warn">双工下无解</td><td>级联后 8192 全是文本</td></tr>
</table>
""")

# ---------------- Q3 ----------------
A("""<h2>Q3　改「外接 LLM + 单工轮询」对 VR+3D / PC+3D 表情动作驱动的影响</h2>

<h3>3.0 先纠正一个前提：现在双工档根本没有驱动 3D</h3>
<div class="k">代码核实：<code>VRC_EMOTION</code> 事件只有两个下发点 ——
<code>dialogue.py:411</code>（<b>桌宠档</b>，LLM 返回 JSON 里的 emotion 字段）与 <code>webapp.py:1598</code>（前端按钮直控）。
<b>双工 / 通话路径零下发</b>。⇒ 你担心的「级联会不会破坏 3D 驱动」，
实际情形是：<b>双工通话时 3D 角色是静止的；改成级联反而是从「没有」变成「有」。</b></div>

<h3>3.1 三条时钟要分开看（这是分析 3D 驱动的正确切法）</h3>
<table>
<tr><th>通道</th><th>驱动源</th><th>双工</th><th>级联（外接 LLM）</th></tr>
<tr><td><b>表情</b> emotion</td><td>文本语义</td>
<td class="bad">模型只吐纯文本流，无结构化标签 ⇒ 只能后置情绪分类 ⇒ <b>晚一整句</b>；且当前根本没接</td>
<td class="good">强 LLM 可在同一句里带 <code>[emotion]</code>，流式解析即触发 ⇒ <b>与文本同帧</b></td></tr>
<tr><td><b>动作</b> gesture</td><td>句子级语义</td>
<td class="bad">同上；且块级碎片文本难以判断句子意图</td>
<td class="good">完整句子 ⇒ 可做 plan-then-infill（SentiAvatar：6s 动作 0.3s 生成，支持无限多轮流式）</td></tr>
<tr><td><b>唇形</b> viseme</td><td>音频</td>
<td>若开 generate_audio，音频逐块出 ⇒ 可驱动</td>
<td class="good">流式 TTS 首包即出音频 ⇒ 唇形与声音同步起步；或 Audio2Face 类音素驱动</td></tr>
<tr><td><b>倾听态</b>（点头/看向用户）</td><td>用户音频能量/音高</td>
<td>模型内部裁决，不可外部控制</td>
<td class="warn">需<b>另建</b>：VAD + RMS/基频驱动的待机动画（与语音异步，反而更稳）</td></tr>
</table>

<h3>3.2 真实损失（诚实列出来）</h3>
<table>
<tr><th>损失项</th><th>严重度</th><th>能否补偿</th><th>补偿手段</th></tr>
<tr><td><b>backchannel 消失</b>（她不再边听边"嗯／对／真的吗"）</td><td class="bad">高</td><td class="warn">部分</td>
<td>由 VAD + 规则在用户停顿 &gt;300ms 时插入预录的短回应动画+音频（不走 LLM，零延迟）</td></tr>
<tr><td><b>重叠语音消失</b>（不能和用户同时出声）</td><td class="warn">中</td><td class="bad">基本不能</td>
<td>这是单工的架构性限制；可用「倾听态动画」掩盖</td></tr>
<tr><td><b>即时打断的粒度变粗</b>（必须先停播再处理）</td><td class="warn">中</td><td class="good">能</td>
<td>barge-in 是<b>播放层</b>机制：100~200ms，比全双工 100~300ms 更低（不经过模型推理）</td></tr>
<tr><td><b>打断后表情卡住</b></td><td class="warn">中</td><td class="good">能</td><td>清音频队列的同时把表情复位 neutral，做成一个原子操作</td></tr>
<tr><td><b>整体延迟后移</b></td><td class="warn">中</td><td class="good">能</td>
<td>级联 0.75~3.1s（云端 API + 本地 TTS 可压到 1.0~1.5s）vs 现状 3.76~9.40s —— <b>净改善</b></td></tr>
</table>

<h3>3.3 自然度损失多少？（给区间，并标注不确定）</h3>
<div class="k">可参考的锚点：人类轮转间隔中位数 <b>~200ms</b>（PNAS，10 种语言）；
响应超过 <b>800ms</b> 用户就开始插话（Retell 2026，200+ 通测试）。<br>
级联优化后可稳定落在 <b>400~800ms</b> —— <b>仍在人类自然区间内</b>。
因此「节奏」这一项的损失是<b>小档</b>；真正不可补偿的是 <b>backchannel + 重叠语音</b> 两件事。</div>
<p><b>估计：整体互动自然度损失 10~20%</b>，其中约 2/3 来自 backchannel/重叠，1/3 来自打断粒度。
<span class="tag y">不确定</span>这是<b>估计值</b>，不是实测；验证方法见下。
而另一边，<b>人格一致性、内容质量、3D 表现力是净增益</b>（从「静止 + 复读身份证」到「有表情动作 + 真对话」）。</p>

<h3>3.4 VR 场景的额外注意</h3>
<ul>
<li>VR 对延迟更敏感（临场感/晕动），但<b>视觉通道可以异步填补</b>：呼吸、眨眼、重心摆动、视线追踪用户 —— 这些与语音解耦，能完全掩盖 0.5~1s 的语音空档。<b>VR 档必须把这些做满。</b></li>
<li>PC+3D（桌面看板娘 / Unity 窗口）敏感度较低，优先保证「表情与文本同帧」即可。</li>
<li>建议统一成<b>文本事件总线</b>：emotion/action 由 LLM 产出、listening 态由 VAD/RMS 驱动、lip 由音频驱动 —— 三条时钟解耦，任何一条慢了都不阻塞另外两条。</li>
</ul>
""")

# ---------------- Q4 ----------------
A("""<h2>Q4　落地建议：继续优化双工，还是外接 LLM 混合？</h2>

<h3>结论</h3>
<div class="k"><b>推荐「外接 LLM 混合方案」：级联拿人格与内容，双工降级为可选的「轮转感知层」。</b>
理由不是双工不好，而是<b>双工档的大脑被锁死在 Q4 量化的 8B 上，而这个容量不足以承载你要的人设</b>——
继续优化是在一个错误容量上做无上限投入。级联所需的四件套<b>已经在你仓库里跑着</b>，这是接线，不是研发。</div>

<h3>4.1 两条路的代价 / 风险 / 关键优化点</h3>
<table>
<tr><th>方案</th><th>代价</th><th>风险</th><th>关键优化点</th></tr>
<tr><td><b>A 继续优化双工</b><br><span class="tag r">不推荐</span></td>
<td><ul>
<li>人格上限锁死在 Q4 8B，投入与产出不成比例</li>
<li>8k 共享 KV：记忆与音频抢空间，长期记忆<b>结构性塞不进</b></li>
<li>音色受引擎烘焙约束（换音色可能要重建会话）</li>
<li>每次调参都要重跑 1400+ 通回归验证</li></ul></td>
<td><ul>
<li><b>已投入很久仍不达标</b>——同样的边际投入，收益递减明显</li>
<li>lps / fi 这类旋钮已踩四次，两头都试过</li>
<li>模型侧不可控：换大脑要等厂商</li></ul></td>
<td><ul>
<li>开 <code>generate_audio: true</code>（砍 0.7~2.5s、拿回副语言）</li>
<li>常驻块挂到通话档<b>末尾</b>（零风险，最高 ROI）</li>
<li>块节拍回到官方 1000ms；lps 与 fi <b>成对</b>调</li>
<li>引擎层删掉 <code>&lt;think&gt;</code> 标签</li>
<li>TTS 输出流式化</li></ul></td></tr>
<tr><td><b>B 外接 LLM + 单工轮询</b><br><span class="tag g">推荐</span></td>
<td><ul>
<li>3~5 天搭链路（开关式，可 A/B 回滚）</li>
<li>backchannel / 重叠语音需另建规则补偿</li>
<li>3D 倾听态动画要新写（约 1 天）</li>
<li>云端 API 有成本与隐私考量（可选本地 qwen3:14b）</li></ul></td>
<td><ul>
<li>端点检测（判句）不准会抢话或慢半拍 —— 可用语义判句 + 可调阈值兜底</li>
<li>本地 14B 显存占用需评估（当前双工已占 11GB）</li>
<li>若 TTS 仍走云端，延迟改善打折 ⇒ <b>必须配本地流式克隆 TTS</b></li></ul></td>
<td><ul>
<li><b>级联四件套已在仓库</b>：asr.py(sherpa-onnx 流式)／human_voice_detector.py(VAD)／openai_client.py+ollama(qwen3:14b)／cosyvoice_server.py(<b>stream=True + instruct 情感</b>)</li>
<li>端点用「VAD + 语义判句」，不用纯静音计时</li>
<li>barge-in + 打断回填（现在是整句丢弃）</li>
<li>音色<b>每次请求携带参考音</b>，绝不烘焙进 prompt_cache</li>
<li>记忆分层：滚动摘要（8~10 轮压一次）+ 外部向量检索 top-3</li></ul></td></tr>
</table>

<h3>4.2 分阶段路线（每阶段都能独立验收、可回滚）</h3>
<table>
<tr><th>阶段</th><th>工作量</th><th>动作</th><th>验收指标</th></tr>
<tr><td><b>P0</b></td><td>1 天·零风险</td><td>常驻块（人格站位 + 守则）挂到<b>通话档末尾</b>；接通两个死配置</td>
<td>双工档「非身份证句数占比」14% → ≥40%</td></tr>
<tr><td><b>P1</b></td><td>3~5 天</td><td><code>call_mode: cascade</code> 半双工通话链路 + barge-in 与打断回填，<b>与双工并存、可一键回滚</b></td>
<td>端到端 ≤1.5s；可打断；出声率 50% → ~100%</td></tr>
<tr><td><b>P2</b></td><td>2~3 天</td><td><b>3D 统一到文本事件总线</b>：emotion/action←LLM、listening←VAD/RMS、lip←音频；补倾听态动画</td>
<td>通话时 3D 有表情动作；打断后不卡表情</td></tr>
<tr><td><b>P3</b></td><td>3~5 天</td><td>记忆分层：滚动摘要 + 外部检索 top-3；8k 内预算分配</td>
<td>跨会话记得用户；20 轮后不跑偏</td></tr>
<tr><td><b>P4</b></td><td>3~5 天</td><td>PersonaProfile 解耦：人设进 prompt、音色只作引用 ⇒ <b>换音色即时、换人设不串味</b></td>
<td>换音色不重建会话</td></tr>
<tr><td><b>P5（可选）</b></td><td>视预算</td><td>双工回归为<b>轮转感知层</b>：只用它判断「该不该开口 / 用户是不是在犹豫」，内容仍由外接 LLM 写</td>
<td>拿回 backchannel 与语气感知，不牺牲人格</td></tr>
</table>

<h3>4.3 关键优化点清单（做了就有量的变化）</h3>
<ul>
<li><b>端点检测用语义判句</b>，不用静音计时 —— 「慢理人/抢话」大多出在这。</li>
<li><b>TTS 必须本地流式</b>（cosyvoice_server 已支持 stream=True）＋ <b>instruct 情感指令</b>顺便给表情提供一致信号。</li>
<li><b>成句即送 + 流式合成</b>：文本与音频都别等整句。</li>
<li><b>提示词写「怎么反应」而不是「她是谁」</b>：声明式会被念出来，反应式会被执行；且放末尾。</li>
<li><b>别再用 lps 治哑火</b>（已四次），级联下这颗旋钮直接消失。</li>
</ul>

<h3>4.4 什么时候可以回头做双工（决策红线）</h3>
<p>四条<b>同时</b>满足才值得回：<span class="tag">①</span>上下文 ≥32k 且非 INT4
<span class="tag">②</span>音色问题已解决（不再依赖烘焙进 KV 的参考音）
<span class="tag">③</span>级联端到端已稳定压到 1s 内
<span class="tag">④</span>有独立工程预算（不是从主线体验里挤）</p>
<p class="small"><b>当前四条一条都不满足。</b></p>

<h3>4.5 不确定部分与验证方法</h3>
<table>
<tr><th>不确定项</th><th>为什么不确定</th><th>怎么验证</th></tr>
<tr><td>INT4 对角色扮演的具体损失幅度</td><td>无公开消融数据</td><td>同一 prompt 在 Q4_K_M / Q6_K / fp16 各跑 20 遍，比「非身份证句数占比」</td></tr>
<tr><td>自然度损失 10~20%</td><td>估计值，无实测</td><td>P1 上线后做 A/B：同一 10 问脚本，双工 vs 级联各跑 3 轮，盲评打分（自然度/人格/想继续聊）</td></tr>
<tr><td>本地 qwen3:14b 是否够撑人设</td><td>num_ctx 4096 偏小</td><td>先跑 num_ctx 8192 + 人设块末尾，与云端 glm-4.5-flash 对比</td></tr>
<tr><td>本地 CosyVoice 首包实测</td><td>标称值 ≠ 工程预期（×2~3）</td><td>直接测 stream=True 的首包耗时与 RTF</td></tr>
</table>

<div class="k" style="margin-top:20px"><b>最后一句判断：</b>
「双工语音交互最自然」这个前提，在<b>大脑足够强</b>时才成立。
现在我们的双工是「弱大脑 + 强约束」，它换来的流畅被人格缺失和 6 秒起手吃掉了。
<b>先把大脑换成外接 LLM 把人格和记忆立住，再考虑把双工作为轮转感知层接回来 —— 顺序反了就是现在这个结果。</b></div>

<div class="small" style="margin-top:26px;border-top:1px solid #e3e6ec;padding-top:12px">
项目实测数据来源：服务端录像 <code>H:\Comni\...\sessions\*\recording.json</code>、
客户端埋点 <code>data/call_trace_*.jsonl</code>、<code>tools/_diag_call_2255.py</code>、
<code>tools/_cmp_native_vs_ours.py</code>；本报告 <code>tools/_mkreport_duplex_20260924.py</code>。
</div>
</div></body></html>""")

html = ''.join(H)
p = os.path.join(OUT, 'report.html')
io.open(p, 'w', encoding='utf-8').write(html)
print('written', p, len(html), 'bytes')
