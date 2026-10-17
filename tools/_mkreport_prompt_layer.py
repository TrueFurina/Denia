# -*- coding: utf-8 -*-
"""生成「提示词层次 + 原生双工机制」两问对照报告。

不依赖外部资源；深浅主题自适应。刻意不用 % / format，避免 CSS 里的花括号打架。
"""
import io, os, json

OUT_DIR = r'G:\ai对话\11\data\diag_prompt_layer_20260923'
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------- 原生 vs 我们 的 config 对照 ----------------
ROWS = [
    ('system_prompt', '88 字（零轮转指令）', '484 字（prefix + behavior）'),
    ('ref_audio', 'Temp\\duplex_llm_ref_java_ruo.wav', 'ref_minicpm_signature.wav'),
    ('generate_audio', '不传（引擎默认 true，自产音频）', 'false（丢引擎音频，外部 TTS）'),
    ('deferred_finalize', 'true（默认）', 'false（显式传）'),
    ('listen_prob_scale', '不传（用引擎默认）', '1.0'),
    ('force_listen_count', '不传', '3'),
    ('max_new_speak_tokens_per_chunk', '不传', '24'),
    ('length_penalty', '1.05', '1.0（引擎默认 1.1）'),
    ('块间隔中位', '1000 ms（1.0 Hz）', '500 ms（2.0 Hz）'),
]

SPEAK = [
    ('原生 22:15', 207, 56.5, '41 块（41 秒）', 'native'),
    ('原生 22:23', 60, 56.7, '6 块', 'native'),
    ('原生 22:24', 152, 44.7, '6 块', 'native'),
    ('我们 22:55（当前）', 96, 26.0, '4 块', 'ours'),
    ('我们 22:31（井喷档）', 67, 58.2, '12 块', 'ours'),
]


def bar(pct):
    w = max(2.0, pct * 1.55)
    cls = 'n' if pct >= 40 else 'o'
    return ('<div class="barwrap"><div class="bar ' + cls + '" style="width:' +
            ('%.2f' % w) + '%"></div><span class="barlab">' + ('%.1f' % pct) + '%</span></div>')


cfg_rows = ''.join(
    '<tr><td class="k">' + k + '</td><td class="n">' + a + '</td><td class="o">' + b + '</td></tr>'
    for k, a, b in ROWS)

speak_rows = ''.join(
    '<tr><td class="k">' + n + '</td><td>' + str(blk) + '</td><td>' + bar(pct) +
    '</td><td>' + run + '</td></tr>'
    for n, blk, pct, run, _ in SPEAK)

HTML = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>提示词层次与原生双工机制 · 两问对照</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--text:#1f1f1e;--muted:#6b6b66;--bd:#e2e1db;--ac:#185FA5;--ok:#0F6E56;--warn:#993C1D;}
@media (prefers-color-scheme:dark){
:root{--bg:#1b1b19;--card:#262624;--text:#ececea;--muted:#a3a29c;--bd:#3a3a37;--ac:#85B7EB;--ok:#5DCAA5;--warn:#F0997B;}
}
*{box-sizing:border-box}
body{margin:0;padding:36px 20px 60px;background:var(--bg);color:var(--text);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:21px;font-weight:600;margin:0 0 6px}
h2{font-size:17px;font-weight:600;margin:38px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--bd)}
h3{font-size:14px;font-weight:600;margin:22px 0 8px;color:var(--muted)}
p{margin:10px 0}
.sub{color:var(--muted);font-size:13px;margin:0 0 4px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:20px 22px;margin:16px 0}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:12px 0}
th{text-align:left;font-weight:600;color:var(--muted);font-size:12.5px;padding:8px 10px;border-bottom:1px solid var(--bd)}
td{padding:9px 10px;border-bottom:1px solid var(--bd);vertical-align:middle}
tr:last-child td{border-bottom:none}
td.k{font-weight:500}
td.n{color:var(--ok)}
td.o{color:var(--warn)}
code{background:rgba(128,128,128,.13);padding:1.5px 5px;border-radius:4px;
font:12.5px/1.5 ui-monospace,Consolas,monospace}
.barwrap{position:relative;display:flex;align-items:center;gap:8px;min-width:170px}
.bar{height:9px;border-radius:5px}
.bar.n{background:#1D9E75}
.bar.o{background:#378ADD}
.barlab{font-size:12.5px;color:var(--muted);white-space:nowrap}
.badge{display:inline-block;font-size:12px;padding:2px 9px;border-radius:20px;
background:rgba(128,128,128,.14);color:var(--muted);margin-right:6px}
.note{border-left:3px solid var(--ac);padding:2px 0 2px 14px;margin:14px 0;color:var(--muted);font-size:13.5px}
.fix{border-left:3px solid var(--ok);padding:2px 0 2px 14px;margin:14px 0}
ul{margin:10px 0;padding-left:22px}
li{margin:5px 0}
.mono{font:12.5px/1.6 ui-monospace,Consolas,monospace;color:var(--muted)}
</style></head><body><div class="wrap">

<h1>提示词层次 &amp; 原生双工机制</h1>
<p class="sub">2026-09-23 23:15 · 回答「是不是已有基础系统提示词 / 为何原生随便说什么都立刻回复」</p>

<div class="card">
<p><span class="badge">一句话</span>提示词确实分层、角色确实可换，但<b>通话档那条路径没有加载通用的对话守则</b>；
而「先犹豫再回应」<b>从来不是引擎的行为</b>，是我们自己在提示词、旋钮和闸门里加出来的。</p>
</div>

<h2>一、提示词是怎么拼出来的</h2>

<p>引擎实际收到的 <code>system_prompt</code> 由 <code>_wrap()</code>（<span class="mono">local_omni_duplex.py:476</span>）
拼成两段：<code>{prefix}\\n&lt;behavior&gt;{instruction}&lt;/behavior&gt;</code>。</p>

<svg viewBox="0 0 680 372" width="100%" role="img" style="margin:8px 0">
<title>通话提示词的层次结构</title>
<rect x="40" y="10" width="600" height="352" rx="18" fill="none" stroke="var(--bd)" stroke-width="1"/>
<text x="60" y="36" fill="var(--text)" font-size="13.5" font-weight="600">最终 system_prompt</text>
<text x="60" y="62" fill="var(--muted)" font-size="12">① 最高优先级前缀 — 按档位切换</text>
<rect x="56" y="74" width="568" height="42" rx="8" fill="rgba(55,138,221,.13)" stroke="#378ADD" stroke-width="1"/>
<text x="72" y="95" fill="var(--text)" font-size="13">他在跟你语音通话，你说的每句话他都能听到…</text>
<text x="60" y="138" fill="var(--muted)" font-size="12">② behavior 包裹区 — 两条互斥路径</text>
<rect x="56" y="150" width="568" height="86" rx="10" fill="rgba(29,158,117,.13)" stroke="#1D9E75" stroke-width="1"/>
<text x="72" y="170" fill="var(--text)" font-size="13" font-weight="600">通话档（当前生效）— 到这里就 return 了</text>
<text x="72" y="194" fill="var(--text)" font-size="13">persona.txt · 角色层 — 换角色只改这一个文件</text>
<text x="72" y="218" fill="var(--text)" font-size="13">behavior.txt · 行为层 — 全角色共用</text>
<rect x="56" y="244" width="568" height="86" rx="10" fill="rgba(136,135,128,.13)" stroke="#888780" stroke-width="1"/>
<text x="72" y="264" fill="var(--muted)" font-size="13" font-weight="600">其他档：桌宠互动 / 看屏双工（通话不走这条）</text>
<text x="72" y="288" fill="var(--muted)" font-size="13">character + speaking_style + 身份行</text>
<text x="72" y="312" fill="var(--muted)" font-size="13">9 条内置对话守则 —「何时开口、说多长」</text>
<text x="60" y="350" fill="var(--muted)" font-size="12">③ 当前上下文（双工模式 / 用户状态）</text>
</svg>

<h3>为什么我之前说「没有教模型如何对话」</h3>
<p>那句话需要更正。准确的说法是：</p>
<ul>
<li>仓库里<b>确实有一套</b>通用对话守则（默认沉默、没有画面、一次只说一句、别总拿关心当开场、别在深夜聊睡觉、辨析环境声……），共 9 条，写在
<span class="mono">proactive.py:469~505</span>。</li>
<li>但它所在的代码块，是 <code>if _custom:</code> 之后的 <b>else 分支</b>。</li>
<li>通话档的 <code>_custom</code>（专属通话提示词文件）非空，函数在 <span class="mono">proactive.py:452</span>
<b>直接 return</b>，永远走不到那 9 条。</li>
<li>所以通话档手上只有「人设 + 一份行为文件」——而那份行为文件当时是操作手册性质的（字数规则、负面清单、元指令）。</li>
</ul>
<div class="note">不是仓库里没有通用守则，是<b>它没被挂到通话档上</b>。这是我上一轮表述不准确的地方。</div>

<h3>角色怎么轮换</h3>
<table>
<tr><th>层</th><th>文件 / 配置</th><th>换角色时</th></tr>
<tr><td class="k">角色层（通话）</td><td><code>prompts/call_profiles/danya_persona.txt</code><br><span class="mono">config: duplex_call_persona_file</span></td><td class="n">只改这一个文件</td></tr>
<tr><td class="k">行为层（通话）</td><td><code>prompts/call_profiles/danya_behavior.txt</code><br><span class="mono">config: duplex_call_prompt_file</span></td><td>全角色共用，一般不动</td></tr>
<tr><td class="k">桌宠档角色</td><td><span class="mono">config: characters[] + active_character_id</span></td><td>另一套，与通话档互不影响</td></tr>
</table>

<h2>二、为什么原生「随便说什么都立刻回复」</h2>

<p>因为我们期望的「先犹豫、判断、等待」，<b>引擎里根本没有这个环节</b>。</p>

<svg viewBox="0 0 680 392" width="100%" role="img" style="margin:8px 0">
<title>引擎逐块裁决流水线</title>
<defs><marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
<path d="M2 1L8 5L2 9" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="180" y="10" width="320" height="48" rx="8" fill="none" stroke="var(--bd)" stroke-width="1"/>
<text x="340" y="28" fill="var(--text)" font-size="13" font-weight="600" text-anchor="middle">麦克风音频流</text>
<text x="340" y="46" fill="var(--muted)" font-size="12" text-anchor="middle">16 kHz 单声道，连续送入</text>
<line x1="340" y1="58" x2="340" y2="86" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#ar)"/>
<rect x="180" y="88" width="320" height="48" rx="8" fill="rgba(55,138,221,.13)" stroke="#378ADD" stroke-width="1"/>
<text x="340" y="106" fill="var(--text)" font-size="13" font-weight="600" text-anchor="middle">切成固定长度的块</text>
<text x="340" y="124" fill="var(--muted)" font-size="12" text-anchor="middle">原生 1000 ms ／ 我们 500 ms</text>
<line x1="340" y1="136" x2="340" y2="164" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#ar)"/>
<rect x="140" y="166" width="400" height="72" rx="8" fill="rgba(127,119,221,.13)" stroke="#7F77DD" stroke-width="1"/>
<text x="340" y="186" fill="var(--text)" font-size="13" font-weight="600" text-anchor="middle">模型裁决 — 每块一次，块内毫秒级完成</text>
<text x="340" y="206" fill="var(--muted)" font-size="12" text-anchor="middle">输入 ＝ 历史 ＋ 上块自己的状态 ＋ 本块音频</text>
<text x="340" y="224" fill="var(--muted)" font-size="12" text-anchor="middle">耗时 36~60 ms（原生）／ 85~136 ms（我们）</text>
<text x="554" y="186" fill="var(--muted)" font-size="11.5">全双工不做</text>
<text x="554" y="202" fill="var(--muted)" font-size="11.5">VAD 检测</text>
<path d="M340 238 L340 258 L200 258 L200 284" fill="none" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#ar)"/>
<path d="M340 238 L340 258 L480 258 L480 284" fill="none" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#ar)"/>
<rect x="60" y="286" width="280" height="92" rx="8" fill="rgba(29,158,117,.13)" stroke="#1D9E75" stroke-width="1"/>
<text x="200" y="306" fill="var(--text)" font-size="13" font-weight="600" text-anchor="middle">原生：裁决完即出声</text>
<text x="200" y="326" fill="var(--muted)" font-size="12" text-anchor="middle">引擎自己产音频，说与听同一条通路</text>
<text x="200" y="346" fill="var(--muted)" font-size="12" text-anchor="middle">提示词 88 字，零轮转约束</text>
<text x="200" y="366" fill="var(--muted)" font-size="12" text-anchor="middle">SPEAK 占比 45~57%</text>
<rect x="360" y="286" width="280" height="92" rx="8" fill="rgba(226,75,74,.13)" stroke="#E24B4A" stroke-width="1"/>
<text x="500" y="306" fill="var(--text)" font-size="13" font-weight="600" text-anchor="middle">我们：裁决完还要过闸门</text>
<text x="500" y="326" fill="var(--muted)" font-size="12" text-anchor="middle">8 道客户端闸门 ＋ 外部 TTS 重合成</text>
<text x="500" y="346" fill="var(--muted)" font-size="12" text-anchor="middle">提示词 484 字，写满「要等」</text>
<text x="500" y="366" fill="var(--muted)" font-size="12" text-anchor="middle">SPEAK 占比 26%</text>
</svg>

<h3>三条机制事实</h3>
<ul>
<li><b>裁决在块内完成，不是一段等待期。</b>原生 <span class="mono">prefill 24ms / all 36ms / wall 60ms</span>。
快到你感觉不到有「判断」，所以看起来像立刻回复。</li>
<li><b>全双工路径不做 VAD。</b><code>StreamingVAD</code> 的调用点全部在 <code>HalfDuplex</code>
（<span class="mono">worker.py:1454 / 1501 / 1595 / 1724</span>）。duplex 档下没有任何「用户说完没有」的检测环节。
<span class="mono">vad/</span> 目录存在 ≠ duplex 用它。</li>
<li><b>原生提示词零轮转指令。</b>全文 88 字，只说「你处于双工模式，可以一边听、一边说」，
一个字都没提「等他说完」「没轮到就安静」。</li>
</ul>

<h3>原生档 system_prompt 全文（88 字）</h3>
<div class="card" style="padding:16px 18px">
<p style="margin:0;font-size:13.5px">扮演一个具有以上声音特征的助手。请认真、高质量地回复用户的问题。请用高自然度的方式和用户聊天。你处于双工模式，可以一边听、一边说。你是由面壁智能开发的人工智能助手：面壁小钢炮。</p>
</div>

<h3>参数对照（同一引擎，两个客户端）</h3>
<table>
<tr><th>字段</th><th>原生 Comni</th><th>我们接入</th></tr>
__CFGROWS__
</table>
<p class="sub">原生只传 5 个字段，其余全走引擎默认。它不压制 SPEAK，也不丢引擎音频。</p>

<h3>实测裁决（服务端录像 result.mode）</h3>
<table>
<tr><th>会话</th><th>块数</th><th>SPEAK 占比</th><th>最大连续 SPEAK</th></tr>
__SPEAKROWS__
</table>

<div class="fix">
<p><b>反直觉结论：原生不是「更会等待」，是「更爱开口」。</b></p>
<p>它的 SPEAK 占比是我们当前档的两倍，还出现过连续 41 块（41 秒）的发言。你觉得它好，
是因为它<b>接得快、说得整、不卡壳</b>——而不是因为它会犹豫。</p>
<p>「先判断、再等待」不是引擎的能力，是我们在提示词（写满「要等」）、
<code>listen_prob_scale</code>（压制 SPEAK）和 8 道客户端闸门里<b>人为造出来的</b>，
而且造得把该说的话也吞了。</p>
</div>

<h3>由此得到的方向</h3>
<ul>
<li>要「真人感」，应该往 <b>短提示词 + 引擎自产音频（generate_audio=true）+ 削减客户端闸门</b> 走，
而不是继续增加等待逻辑。</li>
<li>原生路径的优势是「说与听在同一条通路上」，所以它说一句停一句、不卡；我们的回合边界靠
<code>is_listen</code> 标志，链路更长。</li>
<li>若要保留「不抢话」，靠 <code>listen_prob_scale</code> 一处调即可，不必在提示词里再写一遍——
两处叠加会把 SPEAK 常年压在健康区以下。</li>
</ul>

<p class="sub" style="margin-top:34px">证据文件：<code>data/_native88.txt</code>（原生 prompt 全集）·
<code>data/_cmp_native.txt</code> · <code>data/_sess_scan.txt</code> ·
工具 <code>tools/_cmp_native_vs_ours.py</code></p>

</div></body></html>'''

HTML = HTML.replace('__CFGROWS__', cfg_rows).replace('__SPEAKROWS__', speak_rows)

p = os.path.join(OUT_DIR, 'report.html')
io.open(p, 'w', encoding='utf-8').write(HTML)
print('OK ->', p, len(HTML), 'chars')
assert '__' not in HTML.split('<style>')[0] or True
for tok in ('__CFGROWS__', '__SPEAKROWS__'):
    assert tok not in HTML, 'leftover token ' + tok
print('no leftover tokens')
