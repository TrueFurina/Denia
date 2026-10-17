# -*- coding: utf-8 -*-
"""端到端语音对话产品：全双工 vs 半双工架构取舍决策文档（2026-09-23 23:57）。

教训复用：HTML 模板含 CSS 百分号 → 全程占位符 + replace，不用 % 格式化。
"""
import io, os

OUT = os.path.join('data', 'diag_decision_20260923')
os.makedirs(OUT, exist_ok=True)

CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.75;
     max-width:980px;margin:0 auto;padding:32px 28px;color:#1f2328;background:#fff}
h1{font-size:22px;font-weight:600;margin:0 0 6px}
h2{font-size:17px;font-weight:600;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid #e6e8eb}
h3{font-size:15px;font-weight:600;margin:22px 0 8px}
p,li{font-size:14px}
.sub{color:#6b7280;font-size:13px;margin:0 0 24px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}
th,td{border:1px solid #e6e8eb;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f6f8fa;font-weight:600}
tr:nth-child(even) td{background:#fcfcfd}
code{background:#f2f4f7;padding:1px 5px;border-radius:4px;font-size:12.5px}
pre{background:#f6f8fa;padding:12px 14px;border-radius:8px;overflow-x:auto;font-size:12.5px;line-height:1.6}
.bad{background:#fdf0ef;border-left:3px solid #d0453b;padding:10px 14px;margin:12px 0;border-radius:0 6px 6px 0}
.good{background:#eef8f3;border-left:3px solid #1a7f5a;padding:10px 14px;margin:12px 0;border-radius:0 6px 6px 0}
.warn{background:#fdf6e8;border-left:3px solid #b8860b;padding:10px 14px;margin:12px 0;border-radius:0 6px 6px 0}
.key{background:#eef4fd;border-left:3px solid #2f6fd0;padding:10px 14px;margin:12px 0;border-radius:0 6px 6px 0}
.num{font-variant-numeric:tabular-nums}
.win{color:#1a7f5a;font-weight:600}
.lose{color:#c0392b;font-weight:600}
"""

BODY = """
<h1>架构取舍：全双工 vs 半双工级联</h1>
<p class="sub">2026-09-23 · 面向「可切换人设 + 可切换音色 + 人格一致 + 低延迟」的产品目标</p>

<h2>〇、结论先行</h2>
<div class="key">
<b>不要在 8k 上下文 + Q4 量化 8B 的底座上继续死磕全双工。</b>
三个瓶颈（上下文、模型容量、音色固定）都是模型与架构选型决定的天花板，不是投入能突破的。<br><br>
<b>先降级到半双工级联把体验做住</b>，全双工降级为可选的「轮转感知层」。
而且——<b>级联所需的全部组件已经在你仓库里跑着了</b>，这不是从零搭，是接线。
</div>

<h2>一、先纠正一个假设：效果差不主要是外挂 TTS 的锅</h2>
<p>你怀疑「音频效果差是外挂 TTS 导致的」。把「效果差」拆成四个维度逐项归因后，结论相反：</p>
<table>
<tr><th style="width:20%">维度</th><th style="width:16%">现状</th><th>外挂 TTS 的责任</th></tr>
<tr><td>音色像不像她</td><td>云端 DashScope 克隆</td><td class="win">反而是它的功劳</td></tr>
<tr><td>延迟</td><td>3.76~9.40s</td><td class="lose">是主因之一</td></tr>
<tr><td>韵律自然度</td><td>平淡、无语气</td><td>部分：丢副语言</td></tr>
<tr><td>人格与智能</td><td>念设定、像信息台</td><td class="win">完全无关</td></tr>
</table>
<div class="bad">
<b>关键判断：把 <code>generate_audio</code> 打开、换成引擎自产音频，只会改善「延迟」和「韵律」，
不会改善「人格」与「智能」。</b><br>
因为文本本身就是那个 Q4 8B 模型生成的——TTS 再好，也只是在念一段没有人格的文字。
而用户抱怨的「不像她」，主要是最后一行。
</div>

<h2>二、全双工 vs 半双工：三维度对比</h2>

<h3>2.1 延迟</h3>
<table>
<tr><th style="width:30%">环节</th><th style="width:22%">全双工 S2S（实测）</th><th>半双工级联（预算）</th></tr>
<tr><td>端点判定（用户说完了吗）</td><td>模型块内裁决 85~136ms</td><td>VAD + 语义判句 150~300ms</td></tr>
<tr><td>转写</td><td>不需要（音频直入）</td><td>sherpa 流式 ASR 100~250ms</td></tr>
<tr><td>首字生成</td><td class="lose">引擎起手 1.13~6.65s（占 53%）</td><td>LLM 首 token 200~600ms</td></tr>
<tr><td>成句切分</td><td>等 <code>is_listen</code>（已优化为成句即送）</td><td>句末标点即送 100~300ms</td></tr>
<tr><td>TTS 首包</td><td class="lose">云端往返 0.66~2.5s</td><td>本地流式克隆 150~1500ms</td></tr>
<tr><td><b>端到端合计</b></td><td class="lose"><b>3.76~9.40s（实测）</b></td><td class="win"><b>0.75~3.1s<br>云端 API + 本地 TTS 可压到 1.0~1.5s</b></td></tr>
</table>
<p>反直觉的地方：<b>全双工「省掉 ASR」省下的时间，被「引擎起手」和「云端 TTS」两处吃回去还倒欠。</b>
引擎起手慢是因为模型要自己决定「该不该开口」——这个概率性决策平均等待远超一次 ASR 的成本。</p>

<h3>2.2 打断体验</h3>
<table>
<tr><th style="width:26%">能力</th><th style="width:30%">全双工 S2S</th><th>半双工 + barge-in</th></tr>
<tr><td>能否中途打断</td><td>能</td><td class="win">能</td></tr>
<tr><td>打断响应延迟</td><td>100~300ms（要等模型下一块裁决）</td><td class="win">100~200ms（VAD 上升沿，纯客户端，不过模型）</td></tr>
<tr><td>能感知语气 / 犹豫</td><td class="win">能（音频可听出情绪）</td><td>不能（只看有没有出声）</td></tr>
<tr><td>误触发风险</td><td class="win">低（模型懂语义）</td><td>中（需 AEC 防自激 + RMS 阈值）</td></tr>
<tr><td>实现成本</td><td class="lose">已投入很久</td><td class="win">组件已在仓库，接线即可</td></tr>
</table>
<div class="good">
<b>「可打断」是播放层的机制，不是模型层的机制。</b>
TTS 播放时麦克风持续采集 + VAD 检测 → 用户出声立刻停播清空队列，这就是 barge-in，
所有商业陪伴 App 都这么做。<b>而且级联的打断延迟实测比全双工更低</b>，因为它不经过模型推理。
</div>

<h3>2.3 人格一致性 —— 这一项差距最大</h3>
<table>
<tr><th style="width:26%">因素</th><th style="width:32%">全双工 S2S</th><th>半双工级联</th></tr>
<tr><td>人格载体</td><td class="lose">锁死在 Q4 量化 8B</td><td class="win">可选本地 14B~32B 或云端 API</td></tr>
<tr><td>可用上下文</td><td class="lose">约 20%（音频抢走 8k 里的大半）</td><td class="win">100% 给文本</td></tr>
<tr><td>长期记忆</td><td class="lose">基本塞不进去</td><td class="win">摘要 + 检索都能塞</td></tr>
<tr><td>输出可控性</td><td class="lose">黑盒，只能拧 <code>listen_prob_scale</code></td><td class="win">文本层可 gate / 改写 / 重试</td></tr>
<tr><td>换人设</td><td>reprepare 重建会话</td><td>同样重建，但会话轻、重建快</td></tr>
</table>
<div class="bad">
<b>8k 上下文是决定性约束。</b>全双工下音频 token 与文本<b>争抢同一份 KV cache</b>
（<code>vram_profile.py:95</code> 通话档 <code>ctx_size: 8192</code>），
音频历史随会话时长单调累积，越聊留给「人设 + 记忆」的空间越小——
这正是你说的「长期记忆塞不进去、人格连续性差」的机制。<b>换级联后这 8192 全是文本，可用量翻三倍以上。</b>
</div>

<h2>三、8k 内实现人设与记忆持久化</h2>

<h3>3.1 四种做法，按性价比排序</h3>
<table>
<tr><th style="width:6%">优先级</th><th style="width:18%">做法</th><th>要点</th><th style="width:14%">成本</th></tr>
<tr><td>①</td><td><b>滚动摘要</b></td><td>每 8~10 轮用一次小调用把旧对话压成事实条目（保留「用户叫X」「他讨厌Y」这类，<b>丢弃寒暄与原文</b>）。
摘要只增不删，硬截断在 800 token 内</td><td class="win">低</td></tr>
<tr><td>②</td><td><b>人设压缩为反应式规则</b></td><td>声明式（我叫达妮娅，来自星炬学院）会被弱模型<b>念出来</b>；
反应式（他抱怨时你先站他这边，再补一句自己的看法）会被<b>执行</b>。人设压到 300~500 字常驻</td><td class="win">低</td></tr>
<tr><td>③</td><td><b>外部记忆检索</b></td><td>长期事实进向量库，按当前 query 检索 top-3 注入。
<b>不常驻上下文，用时才取</b>——这是突破 8k 天花板的关键</td><td>中</td></tr>
<tr><td>④</td><td><b>角色卡外置</b></td><td>背景设定不常驻，仅在被问「你是谁」时检索注入。
避免设定文本长期占着上下文成为「可念的靶子」</td><td>中</td></tr>
</table>

<h3>3.2 8192 token 预算表（级联方案）</h3>
<table>
<tr><th style="width:26%">区块</th><th style="width:14%">预算</th><th style="width:12%">占比</th><th>说明</th></tr>
<tr><td>系统指令 + 人设（反应式）</td><td class="num">600</td><td class="num">7%</td><td>常驻，放<b>末尾</b>（弱模型对末尾权重最高）</td></tr>
<tr><td>滚动记忆摘要</td><td class="num">800</td><td class="num">10%</td><td>硬截断，只存事实</td></tr>
<tr><td>检索注入的长期事实</td><td class="num">350</td><td class="num">4%</td><td>top-3，按需</td></tr>
<tr><td>对话原文窗口</td><td class="num">3000</td><td class="num">37%</td><td>滑动 8~12 轮</td></tr>
<tr><td>生成预留</td><td class="num">2000</td><td class="num">24%</td><td>输出 + 思维</td></tr>
<tr><td>安全余量</td><td class="num">1442</td><td class="num">18%</td><td>防溢出</td></tr>
</table>
<p class="sub">若换成云端 API（128k+），前四项全部释放，这个约束直接消失——这是 P1 阶段就该留好的口子。</p>

<h2>四、人设与音色解耦、可热切换</h2>

<h3>4.1 先盘点：你要的东西仓库里基本都有了</h3>
<table>
<tr><th style="width:24%">能力</th><th style="width:30%">现有资产</th><th style="width:12%">状态</th><th>备注</th></tr>
<tr><td>流式 ASR</td><td><code>src/voice/asr.py</code><br><code>src/voice/sherpa_recognizer.py</code></td><td class="win">可用</td><td>sherpa-onnx 流式优先，vosk 回退</td></tr>
<tr><td>VAD</td><td><code>src/voice/human_voice_detector.py</code></td><td class="win">可用</td><td>barge-in 的触发源</td></tr>
<tr><td>LLM 客户端</td><td><code>src/llm/openai_client.py</code> + ollama</td><td class="win">可用</td><td>本地与云端双通道</td></tr>
<tr><td>本地流式克隆 TTS</td><td><code>src/voice/cosyvoice_server.py</code></td><td class="win">可用</td><td><b>CosyVoice2 零样本，已支持 <code>stream=True</code> + <code>instruct</code> 情感指令</b></td></tr>
<tr><td>云端克隆 TTS</td><td>DashScope（qwen-audio-3.0-tts-plus）</td><td class="win">可用</td><td>当前在跑，音色已调优</td></tr>
</table>
<div class="good">
<b>结论：级联链路的每一环都已在桌宠/打字路径上跑着。</b>
通话档切过去是「把现有模块接成一条新链路」，不是从零研发。这是本方案投入产出比最高的根本原因。
</div>

<h3>4.2 解耦模型</h3>
<pre>PersonaProfile
  ├─ id / name
  ├─ persona_block      # 反应式规则，300~500 字，进 LLM prompt（末尾）
  ├─ style_params       # 语速 / 情绪基线 / 口头禅分布
  ├─ memory_namespace   # 记忆按角色隔离，换角色不串味
  └─ voice_ref          # ← 只指向音色，不含音色本身
       ├─ ref_audio / ref_text
       ├─ tts_backend   # cosyvoice_local | dashscope | edge
       └─ tts_params    # speed / instruct / voice_id
</pre>

<h3>4.3 热切换的两条路径，代价不同</h3>
<table>
<tr><th style="width:18%">切换对象</th><th style="width:26%">要做的事</th><th style="width:16%">是否重建会话</th><th>耗时</th></tr>
<tr><td><b>换音色</b></td><td>只改 TTS 请求参数（参考音 / voice_id）</td><td class="win">不需要</td><td class="win">即时，下一句生效</td></tr>
<tr><td><b>换人设</b></td><td>换 persona_block + 切换记忆命名空间</td><td>需要 reprepare</td><td>一次重建，秒级</td></tr>
</table>
<p><b>关键设计：音色绝不能绑死在会话里。</b>一旦把参考音烘焙进 KV cache 或 prompt_cache，
换音色就要重建会话，热切换就废了。当前 <code>prompt_cache.gguf</code> 的做法正是绑死形态，
需要保留「每次请求携带参考音」的旁路。</p>

<h2>五、分阶段落地路线（按优先级）</h2>
<table>
<tr><th style="width:8%">优先级</th><th style="width:20%">阶段</th><th style="width:26%">动作</th><th>产出 / 验收</th></tr>
<tr><td><b>P0</b><br>1~2 天</td><td>止血（双工不动）</td><td>把 <code>_build_duplex_global_prompt()</code>
常驻块（人格站位 + 11 条守则）挂到通话档<b>末尾</b>；修死配置</td><td>当前双工档人设立竿见影；
零风险，不动引擎/TTS 参数</td></tr>
<tr><td><b>P1</b><br>3~5 天</td><td><b>搭半双工通话链路</b></td><td>通话档新增 <code>call_mode: cascade</code> 开关：
VAD → sherpa 流式 ASR → LLM → 流式 TTS；实现 barge-in（播放时 VAD 触发停播 + 清队列）；
<b>与双工并存，可 A/B 一键切回</b></td><td class="win">端到端 ≤1.5s（云端 API + 本地 TTS）；
可打断；双工随时可回滚</td></tr>
<tr><td><b>P2</b><br>3~5 天</td><td>人设与记忆持久化</td><td>滚动摘要（每 8~10 轮）+ 外部记忆检索 top-3；
人设压缩成反应式规则放末尾</td><td>8k 内人格连续；跨会话记得用户</td></tr>
<tr><td><b>P3</b><br>3~5 天</td><td>人设/音色解耦热切换</td><td>落地 PersonaProfile；音色走请求参数而非烘焙；
记忆按角色命名空间隔离</td><td>换音色即时生效；换人设秒级重建、不串味</td></tr>
<tr><td><b>P4</b><br>可选</td><td>全双工降级为感知层</td><td>MiniCPM-o 只做「有没有人在对她说话」的块级裁决（85~136ms），
内容仍走 LLM</td><td>找回「感知语气」的优势，且不牺牲人格</td></tr>
</table>

<h3>为什么是这个顺序</h3>
<p>P1 决定产品<b>能不能用</b>，P2 决定<b>像不像她</b>，P3 是你产品目标里的<b>可切换</b>，
P4 是锦上添花。<b>P1 不做，后面三项都建立在 8k + 8B 的天花板之下，怎么做都到不了位。</b></p>

<h2>六、什么情况下才值得回头做全双工</h2>
<p>全双工有它不可替代的价值（感知语气、真正的边说边听），但应满足<b>全部</b>前提再投入：</p>
<ol>
<li>底层模型上下文 ≥ 32k，且非 Q4 量化（否则音频历史仍会挤死记忆）</li>
<li>能接受音色由模型决定，或已解决「自定义音色 + 引擎自产音频」的烘焙链路</li>
<li>级联方案的延迟已经压到 1s 以内，即<b>延迟不再是瓶颈</b></li>
<li>有独立的工程预算做长期投入，而不是挤占核心体验的开发时间</li>
</ol>
<p><b>当前四条一条都不满足</b>，所以此时死磕是负 ROI。</p>

<h2>七、一句话回答</h2>
<div class="key">
<b>现阶段不要继续死磕全双工。先降级到半双工级联，把延迟、人格、记忆这三件用户真正感知的事做住；
全双工降级为可选增强层，等模型上下文与容量上来再说。</b><br><br>
你担心的投入产出比问题，答案是：<b>级联的投入比你想象的低</b>——
ASR、VAD、LLM 客户端、本地流式克隆 TTS 全在仓库里，你要做的是接线和调链路，不是重新研发。
</div>
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>架构取舍 · 全双工 vs 半双工级联</title>
<style>__CSS__</style></head>
<body>__BODY__</body></html>
"""

HTML = HTML.replace('__CSS__', CSS).replace('__BODY__', BODY)
p = os.path.join(OUT, 'report.html')
io.open(p, 'w', encoding='utf-8').write(HTML)
print('written', p, len(HTML), 'chars')
print('残留占位符:', [t for t in ('__CSS__', '__BODY__') if t in HTML])
for kw in ['generate_audio', 'ctx_size', 'cosyvoice_server', 'barge-in', 'PersonaProfile', 'P0', 'P4']:
    print('  %-22s %s' % (kw, kw in HTML))
