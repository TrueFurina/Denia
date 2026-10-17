#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 2026-09-24 00:58 通话的质量评价报告"""
import io
import os
import json
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OD = os.path.join(ROOT, 'data', 'diag_eval_20260924')
if not os.path.isdir(OD):
    os.makedirs(OD)

QP = os.path.join(ROOT, 'data', 'quality')
def q(sid):
    p = os.path.join(QP, sid + '.json')
    return json.load(io.open(p, encoding='utf-8')) if os.path.isfile(p) else None

NOW = '20260924_005838_adx_1790182714692'
BASE = '20260923_225525_adx_1790175322099'
OLD = '20260923_223031_adx_1790173813806'
n, b, o = q(NOW), q(BASE), q(OLD)

# ---------- 分类她这通的全部台词 ----------
GOOD = [
    ('累就多休息一下吧。', '接住"今天好累"，简短但到位'),
    ('你总是这样，好像什么事都能扛得住。', '带观察的评价，不是泛泛关心'),
    ('嗯，我也觉得有点让人失望。', '站到用户这边，先共情'),
    ('确实有点磨人啊。', '口语化承接，没有说教'),
    ('那我可得好好夸你了呢。你的笑容总能让人暖和起来。', '主动给出具体内容，不是空话'),
    ('我在这边陪着你呀。', '陪伴感，回应"你在干嘛"'),
    ('我觉得你更像猫吧，独立又', '有观点、有判断（可惜被截断）'),
]
BAD = [
    ('听起来像是在忙一些你并不太喜呢。', '引擎输出中途截断，句子残缺', '引擎侧'),
    ('我觉得你更像猫吧，独立又。', '同上，形容词被吃掉', '引擎侧'),
    ('时候想哎，忘了吧。', '打断吃字，原句开头"有"被清掉', '已修'),
    ('来有点闷啊。', '打断吃字，"听起"两字丢失', '已修'),
    ('嗯，早安呀漂泊者。', '用户说"明天要早起烦死了"，回"早安"语义错位', '模型理解'),
    ('你说说看，有什么让我特别开心的事吗？', '用户要她"说点好听的"，她反问用户，主宾倒置', '模型理解'),
    ('时候想哎，忘了吧。', '第8问"我刚才说啥来着"没接住，且语气消极', '记忆缺失'),
]

def row_good(t, why):
    return '<tr><td class="q">%s</td><td>%s</td></tr>' % (t, why)

def row_bad(t, why, src):
    c = {'已修': 'ok', '引擎侧': 'warn', '模型理解': 'warn', '记忆缺失': 'bad'}.get(src, 'warn')
    return '<tr><td class="q">%s</td><td>%s</td><td><span class="tag %s">%s</span></td></tr>' % (t, why, c, src)

# ---------- 六维表 ----------
DIMS = [
    ('S1 人设稳定', '不在不当场合背设定', 20),
    ('S2 语义正确', '话说得通、答得对', 20),
    ('S3 情绪回应', '接住情绪而非只处理字面', 15),
    ('S4 节奏自然', '反问率 / 碎片率 / 句长', 15),
    ('S5 记忆连贯', '能引用刚才说过的事', 10),
    ('S6 响应速度', '端到端延迟', 20),
]
KEYS = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']
rows = []
for i, (name, desc, w) in enumerate(DIMS):
    k = KEYS[i]
    a = n['scores'][k]
    v = b['scores'][k] if b else 0
    d = a - v
    cls = 'up' if d > 5 else ('down' if d < -5 else 'flat')
    arrow = '&#9650;' if d > 5 else ('&#9660;' if d < -5 else '&minus;')
    rows.append('<tr><td class="nm">%s<div class="ds">%s</div></td><td class="wt">%d%%</td>'
                '<td class="num">%.0f</td><td class="num">%.0f</td>'
                '<td class="num %s">%s %.0f</td></tr>'
                % (name, desc, w, v, a, cls, arrow, abs(d)))

# ---------- 关键指标卡 ----------
def card(label, val, sub, cls=''):
    return ('<div class="mc %s"><div class="ml">%s</div><div class="mv">%s</div>'
            '<div class="ms">%s</div></div>' % (cls, label, val, sub))

CARDS = ''.join([
    card('加权总分', '%.1f' % n['total'], 'P0 前 %.1f · 档位「%s」' % (b['total'], n['level']), 'hl'),
    card('产句量', '%d 句' % len(n['uniq']), 'P0 前仅 %d 句 · 开口率 43.6%%' % len(b['uniq'])),
    card('端到端中位', '%.2fs' % n['e2e_med'], 'P0 前 %.2fs · 目标 ≤1.0s' % b['e2e_med'], 'bad'),
    card('ASR 噪声率', '33%', '12 条转写中 4 条是噪声误识', 'warn'),
])

STATS = [
    ('反问率', '%.0f%%' % n['q_rate'], '真人闲聊 15~25%，40% 偏高 → 采访感'),
    ('平均句长', '%.1f 字' % n['avg_len'], '真人单句 15~25 字，当前偏短'),
    ('碎片句占比', '%.0f%%' % n['frag_rate'], '≤6 字的半截话，目标 <10%'),
    ('SPEAK 占比', '43.6%', '健康区 30~55%（P0 前 26%）'),
    ('出声率', '80%', '30 句送 TTS，24 句真播出'),
]

HTML = u"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>通话质量评价 · 2026-09-24 00:58</title>
<style>
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f7f7f5;
color:#2C2C2A;margin:0;padding:32px 20px;line-height:1.7}
.wrap{max-width:940px;margin:0 auto}
h1{font-size:22px;font-weight:500;margin:0 0 6px}
.sub{color:#5F5E5A;font-size:13px;margin-bottom:28px}
h2{font-size:16px;font-weight:500;margin:32px 0 12px;padding-left:10px;border-left:3px solid #185FA5}
h3{font-size:14px;font-weight:500;margin:20px 0 8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:8px}
.mc{background:#fff;border:0.5px solid rgba(0,0,0,.12);border-radius:10px;padding:14px 16px}
.mc.hl{border:1.5px solid #185FA5}
.mc.bad{border:1.5px solid #A32D2D}
.mc.warn{border:1.5px solid #BA7517}
.ml{font-size:12px;color:#5F5E5A}
.mv{font-size:24px;font-weight:500;margin:2px 0}
.ms{font-size:12px;color:#888780}
table{width:100%;border-collapse:collapse;background:#fff;font-size:13px;
border:0.5px solid rgba(0,0,0,.12);border-radius:10px;overflow:hidden}
th{background:#F1EFE8;text-align:left;padding:9px 12px;font-weight:500;font-size:12px;color:#5F5E5A}
td{padding:9px 12px;border-top:0.5px solid rgba(0,0,0,.08);vertical-align:top}
td.nm{font-weight:500;width:30%}
td.ds{font-weight:400;font-size:12px;color:#888780}
td.wt{color:#888780;width:8%}
td.num{text-align:right;font-variant-numeric:tabular-nums;width:14%}
td.q{font-family:Consolas,Menlo,monospace;font-size:12.5px}
.up{color:#0F6E56}.down{color:#A32D2D}.flat{color:#888780}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px}
.tag.ok{background:#E1F5EE;color:#0F6E56}
.tag.warn{background:#FAEEDA;color:#854F0B}
.tag.bad{background:#FCEBEB;color:#A32D2D}
.box{background:#fff;border:0.5px solid rgba(0,0,0,.12);border-radius:10px;padding:16px 20px;margin-bottom:12px}
.box.ok{border-left:3px solid #0F6E56}
.box.warn{border-left:3px solid #BA7517}
.box.bad{border-left:3px solid #A32D2D}
.box b{font-weight:500}
ul{margin:8px 0;padding-left:20px}
li{margin:4px 0}
.foot{color:#888780;font-size:12px;margin-top:36px;border-top:0.5px solid rgba(0,0,0,.12);padding-top:12px}
</style></head><body><div class="wrap">
<h1>通话质量评价 · 2026-09-24 00:58</h1>
<div class="sub">会话 __SID__ ｜ 六维自定义标尺评分（非业界标准）｜ 对照 P0 前基线 22:55</div>

<h2>结论</h2>
<div class="box ok">
<b>确实优于以往，而且幅度不小。</b>加权总分从 __BOLD__ 提到 <b>__NOW__</b>，档位由「能对话但不自然」进入
「接近可接受」。最实质的三项变化：她<b>不再背身份证</b>（背设定 3/7 → 1/30，且那 1 句是对「你叫什么」的正确回答）、
<b>开始接情绪</b>（承接句 0 → 11）、<b>话量翻了 4 倍</b>（7 句 → 30 句）。
</div>
<div class="box warn">
<b>但还没到「水准」。</b>66 分离真人目标（85+）仍有明显距离，短板集中在三项：
<b>响应 3.14s</b>（目标 ≤1s，占分权重 20% 却只拿 28.7）、
<b>记忆 0 分</b>（第 8 问「我刚才说啥来着」完全没接住）、
<b>反问率 40%</b>（真人 15~25%，偏高造成采访感）。
</div>

<h2>关键指标</h2>
<div class="cards">__CARDS__</div>

<h2>六维评分对照</h2>
<table>
<tr><th>维度</th><th>权重</th><th>P0 前</th><th>本次</th><th>变化</th></tr>
__ROWS__
</table>

<h2>逐项细看</h2>
<table>
<tr><th>指标</th><th>实测</th><th>说明</th></tr>
__STATS__
</table>

<h2>她这通说得好在哪</h2>
<table><tr><th style="width:44%">她说的话</th><th>为什么算好</th></tr>__GOOD__</table>

<h2>她说得差在哪</h2>
<table><tr><th style="width:36%">她说的话</th><th>问题</th><th style="width:14%">归因</th></tr>__BAD__</table>

<h2>剩余差距的三层归因</h2>
<div class="box ok">
<b>第一层 · 已修，只需重启复测</b>
<ul>
<li><b>打断吃字</b>：6 次打断吃掉 6 个句首（「时候想哎」「来有点闷啊」）。已加 <code>_turn_aborted</code>，回放测试通过。</li>
</ul>
</div>
<div class="box warn">
<b>第二层 · 可修，不需换架构</b>
<ul>
<li><b>反问率 40%</b>：常驻块里「答完可以带一句你自己的反应或反问」被过度执行 → 改成「多数时候给反应，反问不超过三分之一」。</li>
<li><b>句长 10.7 字</b>：仍残留「一句就够」的压缩痕迹 → 常驻块里明确「一句话可以说到二十字左右」。</li>
<li><b>ASR 噪声 33%</b>：12 条转写里 4 条是「的 / MO / 您 / A」这类误识，各自触发了一次回复 → 转写 ≤2 字时不作为用户发言。</li>
<li><b>引擎截断</b>：「独立又。」「你并不太喜呢。」是引擎自己吐一半就停 → 需查 <code>max_new_speak_tokens_per_chunk</code>。</li>
</ul>
</div>
<div class="box bad">
<b>第三层 · 架构性，P0 修不了</b>
<ul>
<li><b>响应 3.14s</b>：引擎起手（模型自己决定何时开口）占大头，Q4 8B 的轮转决策就是慢，提示词改不动。</li>
<li><b>记忆 0 分</b>：通话档零记忆注入，8k 共享 KV cache 也塞不下 → 这是 P2（滚动摘要 + 外部检索）的活。</li>
<li><b>语义错位</b>：「早安」「反问用户要好听的话」属 Q4 8B 的理解力上限 → 换大脑（P1 级联）才解。</li>
</ul>
</div>

<h2>下一步</h2>
<div class="box">
<b>先做一次复测</b>（重启 app，同样 1&ndash;10 念一遍）确认打断吃字真的消失了、句子不再被咬开头。<br>
复测后按数值决定：
<ul>
<li>若总分 &gt; 70 → 常驻块微调（压反问率、放句长），当天可完成。</li>
<li>若总分仍 &lt; 70 → 说明剩下的就是速度与记忆，直接进 <b>P1 级联</b>，别再在提示词上花时间。</li>
</ul>
复测命令：<code>python tools/verify_p0_call.py</code>，评分命令：<code>python tools/rate_call_quality.py</code>
</div>

<div class="foot">生成于 2026-09-24 01:30 ｜ 数据来自服务端录像 recording.json 与客户端埋点 call_trace_omni_20260924_005833.jsonl<br>
评分标尺为本项目自定义，用于纵向比较，不代表业界通用标准。目标线为经验设定。</div>
</div></body></html>"""

HTML = (HTML.replace('__SID__', NOW)
            .replace('__NOW__', '%.1f' % n['total'])
            .replace('__BOLD__', '%.1f' % b['total'])
            .replace('__CARDS__', CARDS)
            .replace('__ROWS__', ''.join(rows))
            .replace('__STATS__', ''.join('<tr><td class="nm">%s</td><td class="num">%s</td><td>%s</td></tr>' % s for s in STATS))
            .replace('__GOOD__', ''.join(row_good(*g) for g in GOOD))
            .replace('__BAD__', ''.join(row_bad(*x) for x in BAD)))

out = os.path.join(OD, 'report.html')
io.open(out, 'w', encoding='utf-8').write(HTML)
print('已生成', out, os.path.getsize(out), 'bytes')
