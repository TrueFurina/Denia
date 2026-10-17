# -*- coding: utf-8 -*-
"""生成「双工 + 外挂 TTS 路线评估」架构报告（2026-09-23 23:30）。

教训：HTML 模板里含 CSS 百分号，与 % 格式化冲突 —— 全程用占位符 + replace。
"""
import io, os

OUT_DIR = os.path.join('data', 'diag_arch_20260923')
os.makedirs(OUT_DIR, exist_ok=True)

CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.75;
     max-width:960px;margin:0 auto;padding:32px 28px;color:#1f2328;background:#fff}
h1{font-size:22px;font-weight:600;margin:0 0 6px}
h2{font-size:17px;font-weight:600;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid #e6e8eb}
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
.num{font-variant-numeric:tabular-nums}
"""

BODY = """
<h1>架构评估：双工 + 外挂 TTS 路线是否成立</h1>
<p class="sub">2026-09-23 · 基于 22:55 通话实测、服务端录像地面真值、以及当前代码/config 的逐行核对</p>

<h2>一、结论先行</h2>
<div class="bad">
<b>当前这条「双工模型全包 + 外挂云端 TTS」的路线是错位的。</b>
它同时付了两边的代价，却只拿到了两边的零头：<br>
· 付了原生语音到语音的代价 —— 人格被锁死在 Q4 量化的 8B 小模型里；<br>
· 却关掉了它自产音频（<code>generate_audio: false</code>），没拿到零延迟与副语言信息；<br>
· 又没拿到级联式的好处 —— 不能换强模型、不能换人格载体、文本层还得再跑一趟云端 TTS。
</div>
<p>所谓「要么延迟高，要么克隆质量差」不是两个独立问题，而是<b>同一个结构性矛盾的两个投影</b>：
延迟低要求流式小分块早出首包，克隆质量高要求参考音充分编码与足够上下文——二者在当前的
TTS 技术下直接冲突。继续调参不会让它同时成立。</p>

<h2>二、市面上的同类产品是怎么搭的</h2>
<p>可自定义角色人格 + 音色的二次元陪伴类应用（星野、猫箱、Talkie、Character.AI 语音、
Replika、各类 AI VTuber），<b>绝大多数是级联式：ASR → LLM → TTS</b>，不是语音到语音。
这不是因为它们不懂 S2S，而是三个硬约束决定的：</p>
<table>
<tr><th style="width:22%">约束</th><th>为什么级联式更合适</th></tr>
<tr><td>人格与音色必须解耦</td><td>角色要能换、音色要能换，两者独立。级联式里人格在 LLM 的 prompt/权重里，
音色在 TTS 的参考音里，各改各的。S2S 里两者被焊在同一个模型里。</td></tr>
<tr><td>人设稳定性需要模型容量</td><td>「她是谁」靠长人设 + 记忆 + 风格一致性维持。Q4 量化的 8B 模型撑不住，
70B 级或云端 API 才撑得住。S2S 模型目前普遍偏小。</td></tr>
<tr><td>成本与合规</td><td>异步语音消息（陪伴类主流形态）对延迟不敏感，单条 TTS 成本约 0.003 美元。
只有真正的实时通话才值得上 S2S。</td></tr>
</table>

<h3>三条路线的实测水位（2026 年公开基准）</h3>
<table>
<tr><th style="width:16%">路线</th><th style="width:26%">构成</th><th style="width:16%">端到端延迟</th><th>适用</th></tr>
<tr><td>A 级联式</td><td>流式 ASR → 强 LLM → 流式克隆 TTS</td><td class="num">700~950ms<br>优化后 400~800ms</td><td>陪伴类主流。可观测、可替换、可合规、人格强</td></tr>
<tr><td>B 原生 S2S</td><td>音频直入直出，单模型</td><td class="num">300~550ms<br>（GPT-Realtime ~320ms）</td><td>要极致自然度与副语言；人格弱、音色固定、黑盒</td></tr>
<tr><td>C 混合式</td><td>S2S 管轮转 → 强 LLM 写词 → 流式克隆 TTS</td><td class="num">约 1.0~2.0s</td><td>既要有即时打断感，又要人格与音色可控</td></tr>
<tr><td>D <b>我们当前</b></td><td>Q4 双工全包 → 纯文本 → 云端 TTS</td><td class="num">3.76~9.40s</td><td>—</td></tr>
</table>

<h3>除了 ASR+LLM+TTS，它们还有哪些关键做法</h3>
<p>这是问题的重点——<b>光有三段不够，真正拉开差距的是这五件工程</b>：</p>
<ol>
<li><b>记忆分层</b>。短期记忆 = 上下文窗口里最近 30~50 条；长期记忆 = 二级模型定期把历史压成摘要存起来。
用户感知的「她记得我」，来源是这套机制，不是人设提示词。我们目前通话档完全没有记忆注入
（<code>_build_duplex_memory</code> 只在测试模式分支调用）。</li>
<li><b>端点检测（end-of-turn）</b>。不是「静音 N 秒就认为说完了」，而是 VAD + 语义判句（这句话像不像说完了）。
纯粹的静音计时是我们「不理人 / 慢理人」的直接来源之一。</li>
<li><b>回声消除 AEC</b>。扬声器外放必须做，否则模型听到自己的声音会自激。这条我们还没排查。</li>
<li><b>barge-in 与打断回填</b>。用户插话时立刻停播，并把被打断的半句留在上下文里，
否则下一句接不上。我们当前是整句丢弃（<code>elif self._turn_audio:</code>）。</li>
<li><b>LoRA 微调而非纯 prompt</b>。重度运营的角色会把人设烧进权重，
避免长对话后人设漂移。成本高于 prompt，但一致性是另一个量级。</li>
</ol>

<h2>三、我们为什么反复碰壁</h2>
<table>
<tr><th style="width:26%">碰壁现象</th><th>结构性根因</th><th style="width:16%">是否可调参解决</th></tr>
<tr><td>人格差、念设定</td><td>人格载体是 Q4 量化的 8B 模型。弱模型对长提示词是<b>检索式</b>处理——
把提示词当可引用语料，而不是行为准则。写的越多，可念的靶子越多。</td><td>否</td></tr>
<tr><td>延迟高</td><td>云端 TTS 往返 0.7~2.5s，且整句串行；引擎起手（等模型自己决定开口）占 53%。
算力本身只占 85~136ms。</td><td>部分</td></tr>
<tr><td>出声率仅 50%</td><td>TTS 单槽串行，前一句占用播放 5~6s，后句被判 stale 丢弃。</td><td>部分（需改流式）</td></tr>
<tr><td>克隆音色差 / 像变声</td><td>① 流式小分块 ⇒ 参考音编码不充分；② 6~8 字填充短句音色发散
（8~12kHz 低 6.48dB）；③ F0 随文本漂移（兜底句实测 flash 252.4Hz / plus 232.7Hz）。</td><td>否（与延迟二选一）</td></tr>
<tr><td>不理人 / 井喷来回横跳</td><td>三套等待机制叠加：提示词写「要等」+ <code>listen_prob_scale</code> 压 SPEAK
+ 8 道客户端闸门。同一旋钮两头挪，已踩四次。</td><td>是（但只能取一个平衡點）</td></tr>
</table>

<h2>四、短提示词能否兼顾人设：能改善，但不能「增加」人格</h2>
<p>先给判据。原生 Comni 的提示词只有 <b>88 字、零轮转指令</b>，实测 SPEAK 占比
<b>56.5% / 56.7% / 44.7%</b>；我们是 373 字，SPEAK 占比 <b>26.0%</b>。
提示词短确实让模型更敢开口、更少念设定——<b>但它不产生人格</b>。</p>

<h3>真正决定人设表现的，是两件事</h3>
<div class="good">
<b>① 人设的语法：声明式 vs 反应式。</b><br>
· <code>我叫达妮娅，在星炬学院……</code> —— 声明式，会被<b>念出来</b>（Q4 弱模型做关键词检索式作答）；<br>
· <code>他抱怨的时候，你先站他这边，再补一句自己的看法</code> —— 反应式，会被<b>执行</b>。<br>
把 persona 里的声明改成反应规则，字数不变，人设表现会明显改善。
</div>
<div class="good">
<b>② 人设的位置：开头 vs 末尾。</b><br>
弱模型对<b>末尾指令权重最高</b>。视频档的常驻块正是追加在指令末尾（<code>proactive.py:660</code>），
所以 11 条守则一直在生效；通话档的人设写在开头，被前缀压住。
</div>
<p>所以准确的说法是：<b>短提示词能消除「念设定」，人设要靠「反应式写法 + 放末尾」来补回来</b>。
两者可以同时做到，不是二选一。</p>

<h2>五、为什么视频档既能守人设，又能看屏幕</h2>
<p>三个因素叠加，其中第一个是可以直接搬到通话档的真 bug。</p>

<h3>因素一：装配路径不同 —— 常驻块从未挂到通话档</h3>
<p><code>_build_duplex_global_prompt()</code> 提供「人格站位 + 11 条桌宠守则」，是所有双工模式的兜底。
但两条路径的终点不一样：</p>
<pre>_build_duplex_instructions()        # 视频/看屏档（第 611 行）
    ...
    _g = self._build_duplex_global_prompt()   # 第 660 行，追加到末尾 = 最高优先级
    return _base + '\\n' + _g                 # 常驻块生效

_build_duplex_instructions_call()   # 通话档（第 439 行）
    ...
    return chr(10).join(parts)                # 第 468 行，提前 return
                                              # 常驻块从未挂载
</pre>
<p>通话档手上只有「persona.txt + behavior.txt + ctx」，<b>人格站位与 11 条守则全缺</b>。
这是视频档人设明显好于通话档的第一个、也是最便宜可修的原因。</p>

<h3>因素二：有无真实输入，决定了模型去念提示词还是去说内容</h3>
<p>视频档有<b>真实屏幕帧</b>输入，模型的输出被真实画面锚定，注意力被占住，没有余量去检索提示词文本。
通话档只有语音，<b>提示词是唯一的强语义激活源</b>——Q4 弱模型没有别的东西可说，就去念它。
这正是「三通通话全在讲冰雪王国/绿色森林」的机理。</p>
<p>反过来说，「关注屏幕」这个硬性要求本身不是负担，<b>它是输入源</b>：
提示词只需要写「怎么评」（守则第 6 条：不描写画面，只给带态度的点评），
不需要写「屏幕上有什么」。硬性要求和人设不但不冲突，反而互相成全。</p>

<h3>因素三：时间压力完全不同</h3>
<p>桌宠可以沉默几十秒，用户不期待即时回应，弱模型有时间组织一句有人味的话；
通话档被要求在 1~2 秒内开口，弱模型在高压下退化到最省力的输出——复读设定或短填充句。
<b>「人格差」在通话档里有一半是「延迟焦虑」的副产品。</b></p>

<h2>六、取舍建议</h2>

<h3>推荐顺序：先止血，再决策，最后才是大改</h3>
<table>
<tr><th style="width:12%">阶段</th><th style="width:26%">动作</th><th style="width:18%">预期收益</th><th>代价</th></tr>
<tr><td><b>第一步</b><br>1 天内</td><td>把常驻块挂到通话档：在
<code>_build_duplex_instructions_call</code> return 前追加<b>通话版</b>常驻块
（人格站位 + 守则，去掉第 6/7 条看画面相关，改成交话措辞），置于<b>末尾</b></td>
<td>人设立竿见影；不动任何引擎/TTS 参数；零风险</td>
<td>需重启生效；需回归 8 个通话测试</td></tr>
<tr><td><b>第二步</b><br>半天 A/B</td><td>打开 <code>generate_audio</code>
（<code>duplex_native_audio: true</code>），用引擎自产音频</td>
<td>砍掉 0.7~2.5s 云端往返，出声率 50% → 接近 100%，端到端有望进 1.5~2.5s</td>
<td>音色换成引擎烘焙音，需重烤 <code>prompt_cache.gguf</code>；
<b>这是「要快还是要像她」的决策点，必须做了才知道值不值</b></td></tr>
<tr><td><b>第三步</b><br>大改</td><td>转 C 混合路线：MiniCPM-o 只做「听 + 决定何时开口」
（块级裁决仅 85~136ms），内容交给本地 32B 或云端 API，本地流式克隆 TTS 出声</td>
<td>人格、音色、延迟三者兼得；端到端目标 1.5~2.5s</td>
<td>工程量大；需自实现端点检测与 barge-in；
项目里已有 <code>cosyvoice_venv</code>，本地 TTS 有基础</td></tr>
</table>

<h3>本地流式克隆 TTS 选型（若走第三步）</h3>
<table>
<tr><th style="width:20%">模型</th><th style="width:18%">首包延迟</th><th style="width:14%">显存</th><th>备注</th></tr>
<tr><td>Qwen3-TTS</td><td class="num">~97ms</td><td>2~6GB</td><td>延迟最低，支持自然语言音色设计</td></tr>
<tr><td>CosyVoice 2 / 3.5</td><td class="num">150ms~1.5s</td><td>~4GB</td><td>中文生态最完善，项目已装 venv</td></tr>
<tr><td>Fish Speech 1.5</td><td class="num">~100ms</td><td>4~6GB</td><td>中文 WER 最低（1.30%），社区活跃</td></tr>
<tr><td>IndexTTS2</td><td class="num">~1.2s</td><td>~8GB</td><td>情感控制最好，可精确控时长</td></tr>
</table>
<p class="sub">注：标称首包是理想值，工程预期应乘以 2~3（项目已踩过一次，见 §2cl-2 预取短路）。</p>

<h3>明确不要做的</h3>
<div class="warn">
· <b>不要继续在提示词里加「要等待」「要自然」「要像真人」</b>——已证明会把开口率压到健康区以下，
22:55 那通 SPEAK 只有 26.0% 就是这么来的。<br>
· <b>不要再把 <code>listen_prob_scale</code> 两头挪</b>——同一旋钮已踩四次，它只能取一个平衡点。<br>
· <b>不要指望换 TTS 档位解决「音色忽高忽低」</b>——那是文本漂移（每句话文本不同 ⇒ F0 不同），
换档不改善，减少兜底句才改善。
</div>

<h2>七、一句话回答</h2>
<p>路线<b>不正确</b>：应该在「级联式」和「原生 S2S」之间选一条走完整，
而不是停在中间的畸形组合。但<b>不必立刻推倒重来</b>——
先做第一步（把常驻块挂到通话档末尾，零风险），再用第二步（打开引擎自产音频）做一次 A/B，
拿到数据后再决定要不要投入第三步。</p>
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>架构评估 · 双工 + 外挂 TTS 路线</title>
<style>__CSS__</style></head>
<body>__BODY__</body></html>
"""

HTML = HTML.replace('__CSS__', CSS).replace('__BODY__', BODY)

p = os.path.join(OUT_DIR, 'report.html')
io.open(p, 'w', encoding='utf-8').write(HTML)
print('written', p, len(HTML), 'chars')
print('leftover placeholders:', [t for t in ('__CSS__', '__BODY__') if t in HTML])
