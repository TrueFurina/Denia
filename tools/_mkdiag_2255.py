# -*- coding: utf-8 -*-
"""生成 22:55 通话诊断报告（HTML，自包含）。"""
import io
import json
import os

ROOT = r'G:\ai对话\11'
os.chdir(ROOT)
D = json.load(io.open('data/diag_call_2255.json', encoding='utf-8'))
rows = [r for r in D['rows'] if r['total']]
S = D['server']
C = D['client']

# 去重后她真正"说出"的句子
sent = []
for t, s in S['sp_texts']:
    if sent and (sent[-1][1].endswith('，') or len(s) <= 2 or sent[-1][1].endswith('星')):
        sent[-1] = (sent[-1][0], sent[-1][1] + s)
    else:
        sent.append((t, s))
uniq = []
for t, s in sent:
    s = s.strip()
    if s and (not uniq or uniq[-1][1] != s):
        uniq.append((t, s))

AVG = {
    'hes': sum(r['engine_hesitate'] for r in rows) / len(rows),
    'full': sum(r['wait_full'] for r in rows) / len(rows),
    'tts': sum(r['tts_first'] or 0 for r in rows) / len(rows),
    'tot': sum(r['total'] for r in rows) / len(rows),
}
FIX = 0.95  # 成句即送实测节省

bar_rows = ''
for r in rows:
    tot = r['total'] or 0.01
    w = lambda v: max(0.6, 100.0 * v / 10.0)
    bar_rows += """
    <div class="brow">
      <div class="blab">“%s”</div>
      <div class="bbar">
        <span class="s1" style="width:%.1f%%">%.2fs 引擎起手</span>
        <span class="s2" style="width:%.1f%%">%.2fs 等整句</span>
        <span class="s3" style="width:%.1f%%">%.2fs TTS</span>
      </div>
      <div class="btot">%.2fs</div>
    </div>""" % (r['user_text'][:14], w(r['engine_hesitate']), r['engine_hesitate'],
                 w(r['wait_full']), r['wait_full'], w(r['tts_first'] or 0), r['tts_first'] or 0,
                 r['total'])

say_rows = ''.join('<li><span class="ts">%5.1fs</span>%s</li>' % (t, s) for t, s in uniq)

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>22:55 通话诊断 · 延迟与人格</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--dim:#5d6772;--line:#e3e6ea;
--c1:#c2410c;--c2:#a16207;--c3:#0e7490;--ok:#15803d;--bad:#b91c1c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.75 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:23px;margin:0 0 6px}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin:16px 0}
h2{font-size:16px;margin:0 0 12px;padding-left:9px;border-left:3px solid #2563eb}
h3{font-size:14px;margin:16px 0 8px;color:var(--dim);font-weight:600}
.kv{display:flex;flex-wrap:wrap;gap:10px}
.kv div{flex:1 1 150px;background:#fafbfc;border:1px solid var(--line);
border-radius:9px;padding:10px 12px}
.kv b{display:block;font-size:20px;line-height:1.3}
.kv span{font-size:12px;color:var(--dim)}
.brow{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:12px}
.blab{width:150px;color:var(--dim);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.bbar{flex:1;display:flex;height:22px;border-radius:5px;overflow:hidden;background:#eef1f4;position:relative}
.bbar span{display:flex;align-items:center;justify-content:center;color:#fff;
font-size:11px;white-space:nowrap;overflow:hidden}
.s1{background:var(--c1)} .s2{background:var(--c2)} .s3{background:var(--c3)}
.btot{width:56px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
th{background:#fafbfc;color:var(--dim);font-weight:600;font-size:12px}
td.n{font-variant-numeric:tabular-nums;white-space:nowrap}
ul{margin:6px 0;padding-left:20px}
li{margin:4px 0}
.ts{color:var(--dim);font-variant-numeric:tabular-nums;margin-right:10px;font-size:12px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;
border:1px solid;margin-right:6px;vertical-align:1px}
.t-ok{color:var(--ok);border-color:#86efac;background:#f0fdf4}
.t-bad{color:var(--bad);border-color:#fecaca;background:#fef2f2}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:9px;
padding:11px 13px;font-size:13px;color:#78350f;margin:12px 0}
b.hl{background:#fef08a;padding:0 3px;border-radius:3px}
code{background:#f1f3f5;padding:1px 5px;border-radius:4px;font-size:12px}
.foot{color:var(--dim);font-size:12px;margin-top:26px;border-top:1px solid var(--line);padding-top:14px}
</style></head><body><div class="wrap">

<h1>22:55 通话诊断 ·「延迟」与「人格」</h1>
<div class="sub">样本：2026-09-23 22:55:19–22:56:13 语音通话（adx 档 / 2Hz）　证据：服务端录像 96 块 + 客户端埋点 476 条</div>

<div class="card">
<h2>一句话结论</h2>
<p>这次通话<b>延迟 5.68s（中位 4.8s、最坏 9.4s）</b>，比 2 秒目标高了 2~4 倍；
而她 47 秒里说的 <b>7 句话、6 句是「我叫达妮娅 / 我在星炬学院」</b>。<br>
两件事的根因不同：延迟的大头是「<b>等引擎自己决定开口</b>」；人格的根因是
「<b>提示词把她写成了一个安静的一问一答信息台</b>」——不是模型没有性格，是被要求收起来了。</p>
</div>

<div class="card">
<h2>① 延迟：把「他刚说完 → 她出声」拆成三段</h2>
<div class="kv">
  <div><b>{{TOT}}s</b><span>平均端到端（目标 ≤2s）</span></div>
  <div><b>{{HES}}s</b><span>其中「引擎起手」占 {{HESPCT}}%</span></div>
  <div><b>{{FULL}}s</b><span>等整句说完（已修，见下）</span></div>
  <div><b>{{TTS}}s</b><span>TTS 首包（达标）</span></div>
</div>
<h3>逐回合明细（单位：秒）</h3>
{{ROWS}}
<p style="font-size:13px;color:var(--dim);margin-top:10px">
橙 = 用户说完了，模型还在犹豫要不要开口；黄 = 已确定要答，但客户端在等整句；
蓝 = 合成首包。<b>三段加起来才是用户感受到的「她怎么半天不理我」。</b></p>

<div class="note">
<b>最刺眼的一回合</b>：他问「能做个简单的自我介绍吗」，她 <b>6.65s</b> 才起手，
端到端 <b>9.40s</b>。这一段时间里用户以为她没听见，<b>又问了一遍</b>——
第二遍落定为「可以做一个简单的自我介绍吗」，于是她连着答了两遍同样的自我介绍。
<b>用户感受到的「一次性把慢理的好几次一起理了」，就是这么来的。</b></div>
</div>

<div class="card">
<h2>② 延迟：哪些能修、哪些不能</h2>
<table>
<tr><th>段</th><th>实测</th><th>性质</th><th>本轮处理</th></tr>
<tr><td><b>引擎起手</b><br><span style="color:var(--dim)">用户停 → 模型首字</span></td>
    <td class="n">1.13 ~ 6.65s<br>均值 %.2fs</td>
    <td>NDA：模型自己的轮转决策，由 <code>listen_prob_scale</code> 控制。<br>
        引擎自身算力只占 85~136ms，<b>慢的不是算力，是它选择"再等等"</b>。</td>
    <td><span class="tag t-bad">未动</span>这是唯一的根本性瓶颈，
        且旋钮非线性（小一点就话痨/井喷），今天已在同一旋钮上踩过四次，不轻动。</td></tr>
<tr><td><b>等整句</b><br><span style="color:var(--dim)">模型首字 → 客户端送 TTS</span></td>
    <td class="n">1.94 ~ 1.97s<br>均值 %.2fs</td>
    <td>结构性：客户端原先只在引擎返回 <code>is_listen</code>（宣布回合结束）时才把整句送出。
        2Hz 档下模型吐完最后一片文字，还要再跑 2 个 chunk 才置真 is_listen ⇒ 白等 0.9~1.0s。</td>
    <td><span class="tag t-ok">已修</span>「成句即送」：一到句末标点就送去合成，
        与后续生成重叠。实测每回合省 <b>0.94~0.98s</b>（7 回合中 6 回合命中，均值 0.81s）。</td></tr>
<tr><td><b>TTS 首包</b><br><span style="color:var(--dim)">送合成 → 出声</span></td>
    <td class="n">0.66 ~ 0.81s<br>均值 %.2fs</td>
    <td>已达 2 秒预算内应有水平（云端整句合成）。</td>
    <td><span class="tag t-ok">不动</span></td></tr>
</table>
<div class="note">修完之后单回合账：<b>3.76 / 9.40 / 4.78s → 2.81 / 8.45 / 3.83s</b>。
最好的那回合直接进 2 秒区，最坏那回合仍由「引擎起手」撑在 8s 以上。<br>
<b>结论：≤2s 的天花板不在合成栈，在引擎的轮转决策</b>——这块不换思路（比如把「等它开口」换成「先出声再修正」）就压不下去。</div>

<h3>顺带修掉的：TTS 串行吞话</h3>
<p style="font-size:13.5px">他夸她「漂泊者先生你好」那句，上屏 7 次但只真出声 6 次；
其中一次被前一句的串行播放压住，<b>晚了 4.0 秒才响</b>。这也是「话说一半换个话题」的观感来源。</p>
</div>

<div class="card">
<h2>③ 人格：她这 47 秒到底说了什么</h2>
<p style="font-size:13.5px">把服务端 15 片 SPEAK 拼回去，去重后是这 7 句：</p>
<ul>{{SAY}}</ul>
<p style="font-size:13.5px;margin-top:12px">
<b>7 句里 6 句是姓名 + 学院</b>，0 句关心、0 句玩笑、0 句反问、0 句试探。
她把「人设」当成了「资料」在念——用户的原话「人格性都没有」，指的正是这个。</p>
<div class="note">
注意这不是「复读自己说过的话」那种闸门能拦的病。<b>每一句措辞都不一样、每一句都通顺</b>，
只是内容全是同一张身份证。所以 call_gate 的复读闸门一次都没拦（也确实不该拦）。
</div>
</div>

<div class="card">
<h2>④ 人格的根因：三层提示词叠成一个「安静问答机」</h2>
<table>
<tr><th>层</th><th>原文（改动前）</th><th>它造成的效果</th></tr>
<tr><td><b>前缀</b><br><span style="color:var(--dim)">最高优先级</span></td>
<td>「接的时候直接点、<b class="hl">短一点</b>」「轮不到你的时候<b class="hl">就安静待着，不用找话说</b>」</td>
<td>模型最先读到的就是"少说、别主动"，性格描述全被压平</td></tr>
<tr><td><b>行为规范</b></td>
<td>「他问你什么<b class="hl">就答什么，一句就够，不用铺开</b>」</td>
<td>把对话降级成一问一答；答完就停，不会接话头、不会反问</td></tr>
<tr><td><b>角色段</b></td>
<td>纯声明式设定：「你是达妮娅。星炬学院虚质科学部的学生，粉发蓝眸…」</td>
<td>Q4 量化弱模型对声明式文本做<b>关键词检索</b>：问"你是谁"→ 吐设定原文。
这就是"自我介绍复读"的出处</td></tr>
</table>
<p style="font-size:13.5px"><b>三者叠加 = 一个安静、简短、只会背身份证的信息台。</b>
提示词里写满了她是怎样的人，却全是"资料"，没有一句告诉模型"该怎么反应"。</p>
</div>

<div class="card">
<h2>⑤ 本轮已经改了什么</h2>
<table>
<tr><th>文件</th><th>改动</th></tr>
<tr><td><code>prompts/call_profiles/danya_behavior.txt</code></td>
<td>重写：去掉「一句就够、不用铺开」；改成「答完可以带一句你自己的反应，或者反过来问他一句」；
加「你早就把名字和学院告诉过他了，除非他直接问不用再讲一遍；又问起就换个说法轻轻带过」。</td></tr>
<tr><td><code>prompts/call_profiles/danya_persona.txt</code></td>
<td>去掉"被问名字就背学院和来历"的检索靶子，改成把话头交回去。</td></tr>
<tr><td><code>src/companion/proactive.py</code></td>
<td>通话前缀删掉「短一点」「不用找话说」，保留"不抢话"的轮转约束（这是防井喷的，不能删）。</td></tr>
<tr><td><code>src/llm/local_omni_duplex.py</code></td>
<td>「成句即送」：不再干等 <code>is_listen</code>。含 <code>_turn_sent_len</code> 偏移管理、
超长兜底只切未送段（否则会念两遍 / 丢尾段）、新回合复位。</td></tr>
<tr><td><code>config.yaml</code> + 两处代码</td>
<td>修死配置：<code>duplex_call_asr_trailing_silence: 0.45</code> 全仓无人读取
（实际恒用硬编码 0.6）——和之前那个 <code>force_listen_count</code> 是同一类 bug，现已接通。</td></tr>
<tr><td><code>tests/test_call_early_sentence_emit.py</code></td>
<td>新增回归：用这通真实 trace 回放，验证省时量 + 不丢字/不重字 + 7 条静态守卫。</td></tr>
</table>
<h3>验证</h3>
<ul>
<li><code>test_call_early_sentence_emit</code>　<b>通过</b>（7 回合回放，均值省 0.81s）</li>
<li><code>test_call_prompt_no_echo</code>　<b>通过</b>（无引号例句 / 无负面清单词 / 保留必须作答约束）</li>
<li><code>test_call_prompt_and_barge</code>　<b>通过</b>（角色段 + 行为段 + 上下文结构完好）</li>
<li><code>test_call_silence_gate</code> / <code>test_call_deadair_watchdog</code> /
    <code>test_call_gate_user_echo</code> / <code>test_call_gate_self_echo</code> /
    <code>test_config_yaml_parses</code>　<b>全部通过</b></li>
<li>未定义全局名「差分审计」：补丁前后差值 = 0</li>
</ul>
<div class="note"><b>全部改动都需要重启 app 才生效。</b>刚测的这通（22:55）跑的还是旧提示词，没有任何新改动在里面。</div>
</div>

<div class="card">
<h2>⑥ 还差什么（按性价比排）</h2>
<ul>
<li><b>引擎起手 1.1~6.7s</b> ← 唯一还没解决的大头。想进 2 秒，只有两条路：
细调 <code>listen_prob_scale</code>（风险：另一头是井喷），
或改范式——不再"等她决定"，而是像原生 Comni 那样让引擎直接产音频、客户端只做转发。</li>
<li><b>音色还在回落原厂签名音</b>：录像里引擎实际加载的是
<code>ref_minicpm_signature.wav</code>，不是角色档配的克隆参考音。属于独立问题。</li>
<li><b>通话前十几秒引擎才 prepared</b>（这次 trace 里是 14.97s），这期间用户说的话引擎根本没听到。</li>
<li><b>TTS 仍整句串行</b>：本次有一次被压后 4.0s 才响。真解是把合成做成流式边生成边播。</li>
</ul>
</div>

<div class="card">
<h2>预测：下一通应该看到什么</h2>
<ul>
<li>端到端延迟整体下移约 <b>0.9s</b>，最好的回合进 2.8s 以内；<b>「引擎起手」这一项不动就不会有大变化</b>。</li>
<li>她仍然会答"我叫达妮娅"，但应该出现<b>反问、玩笑、或先接住情绪再接话</b>；
如果还是清一色自我介绍，说明真实瓶颈在模型能力（Q4 量化），而不是提示词措辞。</li>
</ul>
<p style="font-size:13px;color:var(--dim)">
判断方法（不用猜）：回放新通话的 <code>data/call_trace_*.jsonl</code>，
数「<code>turn_end action=early_emit</code>」出现次数应≈回合数；
再数服务端录像里 SPEAK 文本的<b>去重后句数</b>与<b>含反问/第一人称情绪词的句数</b>。</p>
</div>

<div class="foot">
证据文件：<code>data/diag_call_2255.json</code>、<code>data/call_trace_omni_20260923_225519.jsonl</code>、
<code>H:\\Comni\\...\\sessions\\20260923_225525_adx_1790175322099\\recording.json</code><br>
备份：<code>_bak_soul_20260923_2300/</code>　补丁脚本：<code>tools/_patch_20260923_*.py</code>
</div>
</div></body></html>"""
HTML = (HTML.replace('{{TOT}}', '%.2f' % AVG['tot'])
            .replace('{{HES}}', '%.2f' % AVG['hes'])
            .replace('{{HESPCT}}', '%.0f' % (100.0 * AVG['hes'] / AVG['tot']))
            .replace('{{FULL}}', '%.2f' % AVG['full'])
            .replace('{{TTS}}', '%.2f' % AVG['tts'])
            .replace('{{ROWS}}', bar_rows)
            .replace('{{SAY}}', say_rows))

os.makedirs('data/diag_call_2255', exist_ok=True)
p = 'data/diag_call_2255/report.html'
io.open(p, 'w', encoding='utf-8').write(HTML)
print('written', p, os.path.getsize(p), 'bytes')
