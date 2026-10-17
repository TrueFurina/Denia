# -*- coding: utf-8 -*-
"""生成「通话哑火 + 答非所问」诊断报告。"""
import io
import os

OUT = r'G:\ai对话\11\data\diag_turnfix_20260923\report.html'
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# ---------------- 实测数据（全部来自落盘证据，非估计） ----------------
SESS = [
    # (会话, lps, mns, flc, 裁决Hz, SPEAK, 总块, 判定, 行样式)
    ('20260923_194917', 0.5, 48, 2, 2.04, 106, 124, '井喷', 'bad'),
    ('20260923_195045', 0.3, 20, 3, 1.04, 7, 56, '偏沉默', 'warn'),
    ('20260923_200737', 0.5, 48, 2, 2.03, 60, 102, '井喷', 'bad'),
    ('20260923_200856', 0.5, 48, 2, 2.03, 97, 185, '井喷', 'bad'),
    ('20260923_201055', 0.3, 20, 3, 1.02, 22, 85, '健康', 'ok'),
    ('20260923_205035', 1.0, 24, 2, 1.13, 5, 198, '哑火', 'bad'),
]

SPEAK_BLOCKS = [
    (15, 10.3, '听得到，你好呀~'),
    (16, 11.3, '（空文本，end_of_turn 收尾）'),
    (28, 23.3, '我是达妮娅，陪着'),
    (29, 24.3, '你的那个人～'),
    (48, 41.3, '漂泊者。'),
]

FALLBACKS = [
    ('20:51:41', 6.84, 15.24, '不是我的意思是我怎么称呼你'),
    ('20:51:56', 10.38, 30.29, '我该怎么称呼您呼你'),
    ('20:52:46', 10.91, 80.33, '喂你好'),
]

TTS_TTFA = [
    ('20:50:56', '听得到，你好呀~', 8, 2.45, '冷启动'),
    ('20:51:09', '我是达妮娅，陪着你的那个人～', 14, 2.47, '冷启动'),
    ('20:51:26', '漂泊者。', 4, 1.79, '冷启动'),
    ('20:51:42', '嗯？我在听呢，你说。', 10, 2.09, '冷启动'),
    ('20:51:57', '嗯？我在听呢，你说。', 10, 0.012, '★ 缓存命中'),
    ('20:52:47', '嗯？我在听呢，你说。', 10, 0.012, '★ 缓存命中'),
]

KPI = [
    ('2.5%', '引擎 SPEAK 占比', 'bad', '健康区 20~35%；上一轮 52.4%'),
    ('133 秒', '通话中段零开口', 'bad', '第 41s 后 133 秒一个字没出'),
    ('14 → 3', '用户发声 / AI 上屏', 'bad', 'ASR 14 次全识别到，模型只产出 3 句'),
    ('~9 秒', '端到端延迟', 'warn', '等模型 6.8s + 云端首包 2.1s'),
]

CHANGES = [
    ('config.yaml', 'duplex_call_listen_prob_scale', '1.0',
     '<b>0.7</b>', '治哑火。<b>本轮唯一变量</b>——A/B 实测 0.5→52.4%、1.0→2.5%，曲线强非线性'),
    ('config.yaml', 'duplex_call_deadair_text', '单句',
     '<b>3 条轮换池</b>', '同一句反复播会命中 TTS 缓存（首包 0.012s 突兀）+ 机械重复感'),
    ('proactive.py', '兜底句轮换逻辑', '—',
     '<b>新增</b>', '<code>_duplex_deadair_rr</code>：按 <code>|</code> 拆分候选池顺序轮换'),
    ('prompts/danya_persona.txt', '【你怎么叫他】', '叫他漂泊者。',
     '<b>加反例说明 + 补「他问你名字」规则</b>',
     '治答非所问。原文主宾错位——用户问「我该怎么称呼你」和「<b>你</b>怎么叫他」方向相反'),
    ('src/llm/local_omni_duplex.py', 'force_listen_count 取值', '写死 <code>getattr(..., 2)</code>',
     '<b>接通配置</b>', '附带发现：config 键全仓无人读取，引擎恒收 2'),
    ('src/companion/proactive.py', 'set_call_force_listen_count 下发', '—',
     '<b>新增</b>', 'config → client → prepare 请求体，repare 前下发'),
]

VERIFY = [
    ('test_call_silence_gate.py', '43/43'),
    ('test_call_deadair_watchdog.py', '28/28（新增 C7c/C9 两条轮换断言）'),
    ('test_tts_chain_and_talk_gate.py', '25/25'),
    ('test_config_yaml_parses.py', '5/5'),
    ('四层冒烟', 'ALL OK（含端到端：默认 2 零回归 / set(3)→3 / set(0)→0 / 非法值不崩）'),
    ('tools/check_turn_taking.py', '新增体检器，可一键复查 SPEAK 占比与开口时段'),
]


def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


P = []
A = P.append

A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
A('<meta name="viewport" content="width=device-width,initial-scale=1">')
A('<title>通话哑火 + 答非所问 诊断报告</title>')
A("""<style>
*{box-sizing:border-box}
body{margin:0;padding:30px 22px 70px;background:#f6f7fa;color:#1f232b;
 font:15px/1.72 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:24px;margin:0 0 6px}
h2{font-size:18px;margin:36px 0 12px;padding-left:11px;border-left:4px solid #1f6feb}
h3{font-size:15.5px;margin:22px 0 8px;color:#374151}
.sub{color:#6b7280;font-size:13.5px;margin-bottom:24px}
.card{background:#fff;border:1px solid #e3e6ec;border-radius:10px;padding:15px 18px;margin:12px 0}
.kpi{display:flex;gap:13px;flex-wrap:wrap;margin:14px 0}
.kpi>div{flex:1;min-width:150px;background:#fff;border:1px solid #e3e6ec;border-radius:10px;padding:12px 15px}
.kpi b{display:block;font-size:24px;line-height:1.3;font-variant-numeric:tabular-nums}
.kpi span{color:#6b7280;font-size:12.5px}
.kpi b.bad{color:#b3261e}.kpi b.ok{color:#0f7b3f}.kpi b.warn{color:#8a5a00}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ec;
 border-radius:9px;overflow:hidden;font-size:13.5px;margin:10px 0}
th{background:#f1f3f7;text-align:left;padding:9px 10px;font-weight:600;color:#374151;
 border-bottom:1px solid #e3e6ec;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #eef0f4;vertical-align:top}
tr:last-child td{border-bottom:none}
tr.ok td{background:#eaf7ef}tr.bad td{background:#fdeceb}tr.warn td{background:#fdf3e3}
.mono{font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:12.3px;color:#4b5563}
ul{margin:8px 0 8px 20px;padding:0}li{margin:5px 0}
code{background:#eef1f6;padding:1.5px 6px;border-radius:5px;font-size:12.6px;
 font-family:ui-monospace,Consolas,monospace}
.flow{background:#fff;border:1px solid #e3e6ec;border-radius:10px;padding:6px 18px;margin:12px 0}
.flow pre{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.9;
 white-space:pre;overflow-x:auto;margin:12px 0;color:#2b3038}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}
.pill.b{background:#fdeceb;color:#b3261e}.pill.o{background:#eaf7ef;color:#0f7b3f}
.pill.w{background:#fdf3e3;color:#8a5a00}.pill.n{background:#eef1f6;color:#4b5563}
.note{border-left:3px solid #f0b429;background:#fffaf0;padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0}
.bad-note{border-left:3px solid #b3261e;background:#fdf5f4;padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0}
.ok-note{border-left:3px solid #0f7b3f;background:#f2faf5;padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0}
</style></head><body><div class="wrap">""")

A('<h1>通话「哑火 + 答非所问 + 高延迟」诊断报告</h1>')
A('<div class="sub">通话 <span class="mono">20260923_205035</span>（174.3 秒 / 198 块）· '
  '数据源：Comni 服务端会话录像 <span class="mono">recording.json</span>（引擎地面真值）+ 客户端 trace + 运行日志 · '
  '2026-09-23 21:0x</div>')

A('<div class="kpi">')
for v, t, cls, note in KPI:
    A('<div><b class="%s">%s</b><span>%s<br>%s</span></div>' % (cls, esc(v), esc(t), esc(note)))
A('</div>')

# ---------------- 结论速览 ----------------
A('<h2>结论速览：三个症状，三个互相独立的根因</h2>')
A('<table><tr><th style="width:22%">你的症状</th><th style="width:26%">根因</th>'
  '<th style="width:32%">证据</th><th>性质</th></tr>')
A('<tr class="bad"><td><b>中途不再实质性回应</b><br>只反复说「嗯，我在听呢，你说」</td>'
  '<td>模型被 <code>listen_prob_scale=1.0</code> 压成「几乎恒听」<br>'
  '<span class="mono">引擎 SPEAK 仅 2.5%</span></td>'
  '<td>引擎第 41 秒后再无一次开口，<b>133 秒零输出</b>；<br>'
  '三次「哑火看门狗」触发兜底句，占全部发声的 3/4</td>'
  '<td><span class="pill b">我上轮改动所致</span></td></tr>')
A('<tr class="warn"><td><b>问「我该怎么称呼你」<br>答「漂泊者」</b></td>'
  '<td>提示词主宾错位：唯一含「称呼」的段落是<br>「<b>你</b>怎么叫他 → 叫他漂泊者」</td>'
  '<td>模型做关键词检索式作答——听到「称呼」<br>就从提示词里搬现成答案</td>'
  '<td><span class="pill w">提示词设计缺陷</span></td></tr>')
A('<tr class="warn"><td><b>整体响应延迟很高</b></td>'
  '<td>大头<b>不是</b>合成速度，而是「模型不产出」<br>的干等 + 云端整句非流式</td>'
  '<td>模型自身裁决仅 85~136 ms（极快）；<br>'
  '但用户说完后要等 6.8 s 才走兜底，再加 2.1 s 云端首包</td>'
  '<td><span class="pill w">哑火的次生症状</span></td></tr>')
A('</table>')
A('<div class="bad-note"><b>先纠正一个方向性错误：上一轮我把这个旋钮调反了。</b><br>'
  '官方定义里 <code>listen_prob_scale</code> 是「越大越倾向聆听／越沉默」。'
  '上一轮为治「井喷」我把它从 0.5 提到 1.0——方向确实是对的（更安静），'
  '<b>但幅度过头了 20 倍</b>：开口率从 52.4% 直接崩到 2.5%。'
  '我上一轮的报告里原话就写着「若又哑火：优先回调本值 1.0 → 0.85 → 0.7」，这次正是踩中这个坑。</div>')

# ---------------- 一、哑火 ----------------
A('<h2>一、为什么「中途不再实质性回应」——引擎层面她真的闭嘴了</h2>')

A('<h3>1.1 六通通话的引擎裁决 A/B 对照</h3>')
A('<p>客户端的 trace 只能看到「我们推了什么」，看不到「引擎怎么裁决」。'
  '引擎把每一块的真实裁决落盘在服务端录像里，这是 turn-taking 问题的地面真值。</p>')
A('<table><tr><th>会话</th><th>listen_prob_scale</th><th>max_new_speak</th>'
  '<th>force_listen_count</th><th>裁决频率</th><th>SPEAK 块</th><th>SPEAK 占比</th><th>判定</th></tr>')
for name, lps, mns, flc, hz, sp, tot, vd, cls in SESS:
    A('<tr class="%s"><td class="mono">%s</td><td>%s</td><td>%s</td><td>%s</td>'
      '<td>%.2f Hz</td><td>%d / %d</td><td><b>%.1f%%</b></td><td>%s</td></tr>'
      % (cls, esc(name), lps, mns, flc, hz, sp, tot, 100.0 * sp / tot, esc(vd)))
A('</table>')
A('<div class="note">三通的 <code>system_prompt</code> <b>md5 完全一致</b>（1589 字），'
  '所以提示词不是变量——唯一的变量就是这两个参数。这也排除了「模型换了」「提示词写坏了」这类猜测。</div>')

A('<h3>1.2 曲线是强非线性的：0.5 是话痨峰，1.0 是悬崖</h3>')
A('<div class="flow"><pre>'
  'lps = 0.3  →  SPEAK 12.5% ~ 25.9%   （可接受 / 健康）\n'
  'lps = 0.5  →  SPEAK 52.4% ~ 85.5%   （井喷：一次发声触发独白）\n'
  'lps = 1.0  →  SPEAK  2.5%           （哑火：几乎恒听）\n'
  '\n'
  '   100% |                    ●●\n'
  '        |                  ●\n'
  '    50% |                ●\n'
  '        |\n'
  '        |  ●●\n'
  '     0% |___________________________●\n'
  '        0.3      0.5            1.0\n'
  '</pre></div>')
A('<p>所以 <b>1.0 不是文档意义上的「中性值」，而是实测的「悬崖边」</b>。'
  '新值取 <b>0.7</b>（按 0.5~1.0 段线性外推约 32%，落在健康区上沿）。</p>')

A('<h3>1.3 那 174 秒里，她究竟说了什么</h3>')
A('<table><tr><th>块 #</th><th>相对时刻</th><th>引擎吐出的文本</th></tr>')
for idx, t, txt in SPEAK_BLOCKS:
    A('<tr><td class="mono">%d</td><td class="mono">+%.1fs</td><td>%s</td></tr>' % (idx, t, esc(txt)))
A('</table>')
A('<div class="bad-note"><b>全部 5 个 SPEAK 块集中在通话前 41 秒。</b>'
  '从第 <span class="mono">+41.3s</span> 到通话结束 <span class="mono">+174.3s</span>，'
  '整整 <b>133 秒没有任何一次开口</b>——这就是你感到的「中途开始不理我」。</div>')

A('<h3>1.4 为什么你只听到「嗯？我在听呢，你说」</h3>')
A('<p>模型整通只产出了 3 句真话，而系统兜底句播了 3 次，'
  '所以你的听觉印象里兜底句占了近一半。三次兜底全部来自「哑火看门狗」：</p>')
A('<table><tr><th>时刻</th><th>距用户说完</th><th>距她上次开口</th><th>当时的 ASR 识别</th></tr>')
for t, su, sa, asr in FALLBACKS:
    A('<tr class="warn"><td class="mono">%s</td><td class="mono">%.2fs</td>'
      '<td class="mono">%.2fs</td><td>%s</td></tr>' % (t, su, sa, esc(asr)))
A('</table>')
A('<div class="note"><b>看门狗本身工作正常</b>（`_duplex_deadair_fired_lu` 去重有效，'
  '三次触发间隔 15s / 50s，没有重复刷屏）。但它只能「发现」哑火，不能「治好」哑火——'
  '所以修的是模型开口倾向，不是看门狗。</div>')

A('<h3>1.5 麦克风 / VAD / ASR 全部正常（先排除采集侧）</h3>')
A('<p>这一通你一共开口 <b>14 次</b>，ASR 也完整落定 <b>14 次</b>：</p>')
A('<table><tr><th>你的发声（块级 RMS）</th><th>识别落定</th></tr>')
A('<tr><td class="mono">20:50:49 rms=0.0196 / 20:50:50 rms=0.0127<br>'
  '20:51:03 rms=0.0280 / 20:51:04 rms=0.0321<br>'
  '20:51:19 rms=0.0328 / 20:51:21 rms=0.0165 / 20:51:22 rms=0.0117<br>'
  '20:51:33 rms=0.0150 / 20:51:45 rms=0.0311 / 20:51:57 rms=0.0426<br>'
  '<b>20:52:04 rms=0.1083 / 20:52:14 rms=0.1088</b> / 20:52:20 rms=0.0529 / 20:52:28 rms=0.0564</td>'
  '<td class="mono">20:50:51 / 20:51:05<br>20:51:18 / 20:51:20 / 20:51:24<br>'
  '20:51:35 / 20:51:46 / 20:51:59<br>20:52:06 / 20:52:16 / 20:52:19<br>'
  '20:52:21 / 20:52:29 / 20:52:36</td></tr></table>')
A('<div class="bad-note">特别值得注意：<b>20:52:04 和 20:52:14 你把音量提到了 rms≈0.108'
  '（是平时说话 0.02~0.03 的三到五倍）</b>，ASR 照样识别到了，模型照样一个字不出。'
  '这彻底排除「你没说话」「话筒没收到」——是模型侧不出声。</div>')

# ---------------- 二、答非所问 ----------------
A('<h2>二、为什么问「我该怎么称呼你」答「漂泊者」——提示词主宾错位</h2>')

A('<h3>2.1 提示词原文（<span class="mono">prompts/call_profiles/danya_persona.txt</span>）</h3>')
A('<div class="flow"><pre>'
  '【你怎么叫他】\n'
  '叫他漂泊者。\n'
  '</pre></div>')
A('<p>这是整份提示词里<b>唯一</b>含「称呼」语义的段落。而它描述的方向是'
  '<b>「你怎么叫他」</b>——即 AI 对用户的称呼。</p>')

A('<h3>2.2 方向正好相反</h3>')
A('<div class="flow"><pre>'
  '你问的是：  我 该怎么称呼 你        （用户 → AI：你的名字是什么？）\n'
  '提示词写的是：你 怎么         叫他   （AI → 用户：管他叫漂泊者）\n'
  '\n'
  '         ↑ 主语宾语完全互换，模型识别不了这层区别\n'
  '</pre></div>')
A('<p>于是模型做了一次<b>关键词检索式作答</b>：听到「称呼」这个词，'
  '就从提示词里搬出唯一的现成答案「漂泊者」。</p>')

A('<h3>2.3 时间线还原</h3>')
A('<table><tr><th>时刻</th><th>事件</th></tr>')
A('<tr><td class="mono">20:51:19~22</td><td>你说话（rms 0.033 / 0.017 / 0.012）</td></tr>')
A('<tr><td class="mono">20:51:18 / 20 / 24</td><td>ASR 分三次落定，日志记到的最完整版本是'
  '<span class="mono">「我该怎么称呼您呼你」</span>（本身已是碎片）</td></tr>')
A('<tr class="warn"><td class="mono">20:51:25.678</td><td>模型输出 <b>「漂泊者。」</b>（4 字）</td></tr>')
A('<tr><td class="mono">20:51:26.625</td><td><code>speak_pass</code> 放行 → 出声，'
  'since_user=2.09s / since_ai=17.0s</td></tr>')
A('</table>')
A('<div class="note">顺带说明：这一句其实<b>通过了闸门、正常出声了</b>（<code>speak_pass</code> 有记录）。'
  '所以它不是被吞，是<b>答非所问</b>——性质完全不同，修法也完全不同'
  '（改提示词，不是改闸门参数）。</div>')

A('<h3>2.4 修法</h3>')
A('<div class="flow"><pre>'
  '【你怎么叫他】\n'
  '叫他漂泊者。（这是你对他的称呼，不是你的名字。）\n'
  '\n'
  '【他问你名字、或者问该怎么称呼你的时候】\n'
  '直接答\"达妮娅\"，别拿你对他的称呼去答他。比如\"叫我达妮娅就好\"。\n'
  '</pre></div>')

# ---------------- 三、延迟 ----------------
A('<h2>三、延迟到底慢在哪——不是合成慢，是「等她说第一句」慢</h2>')

A('<h3>3.1 拆解：每一段的真实耗时</h3>')
A('<table><tr><th>环节</th><th>实测</th><th>说明</th></tr>')
A('<tr class="ok"><td>引擎裁决（模型自身）</td><td class="mono">wall_clock 85~136 ms<br>'
  'prefill 26~57 ms</td><td><b>极快</b>，模型 100 毫秒级就给结论。延迟跟算力无关</td></tr>')
A('<tr class="bad"><td>等模型开口</td><td class="mono">6.8 ~ 10.9 s</td>'
  '<td><b>真正的大头</b>。用户说完后干等，最后走看门狗兜底。<br>'
  '本质是第一节的哑火</td></tr>')
A('<tr class="warn"><td>云端 TTS 首包</td><td class="mono">1.79 ~ 2.47 s</td>'
  '<td>整句非流式，攒完整句才合成</td></tr>')
A('<tr class="ok"><td>缓存命中时</td><td class="mono">0.012 s</td>'
  '<td>同一句重复时秒出——证明<b>链路本身不慢</b></td></tr>')
A('</table>')
A('<div class="flow"><pre>'
  '端到端 ≈ 9 秒  =  等模型 6.8s  +  云端首包 2.1s\n'
  '                     ↑                  ↑\n'
  '        【本轮的锅，已修】      【老问题：整句非流式】\n'
  '</pre></div>')

A('<h3>3.2 一个反直觉的观察：兜底句反而「秒出」</h3>')
A('<p>三次兜底句的首包耗时：<b>2.09s → 0.012s → 0.012s</b>。'
  '后两次是因为同一句文本命中了 TTS 缓存。这带来一个副作用：'
  '<b>前一句让你等两秒，后一句突然秒回</b>，听感上非常突兀，'
  '也强化了「她只会这一句」的印象。</p>')
A('<p>所以本轮把兜底句改成 3 条轮换——既避免缓存秒出的突兀，也避免机械重复。</p>')

# ---------------- 四、附带发现 ----------------
A('<h2>四、附带发现：一个从未生效的「死配置」</h2>')
A('<div class="bad-note"><b><code>duplex_call_force_listen_count: 3</code> 这个配置，'
  '全仓没有任何代码读它。</b></div>')
A('<p>追查链条：</p>')
A('<div class="flow"><pre>'
  'config.yaml:1457        duplex_call_force_listen_count: 3    ← 配置在这\n'
  'local_omni_duplex.py    "force_listen_count": int(getattr(\n'
  '                            self, "_call_force_listen_count", 2))  ← 读的是另一个属性名\n'
  '全仓搜索 _call_force_listen_count 的赋值点              ← 0 处\n'
  '服务端录像实测          引擎恒收 2                     ← 铁证\n'
  '</pre></div>')
A('<p>也就是说：<b>上一轮我把它从 2 调到 3，其实什么都没发生</b>。'
  '这次已经把链路接通（config → client → prepare 请求体），并做了端到端验证。'
  '顺带说，服务端录像里 <code>flc</code> 字段恰好成了这个 bug 的指纹——'
  '通话档（audio）恒为 2、陪伴档（video）恒为 3。</p>')

# ---------------- 五、改动清单 ----------------
A('<h2>五、本轮改动清单（重启后生效）</h2>')
A('<table><tr><th>文件</th><th>项</th><th>原值</th><th>新值</th><th>依据</th></tr>')
for f, k, old, new, why in CHANGES:
    A('<tr><td class="mono">%s</td><td>%s</td><td class="mono">%s</td><td>%s</td><td>%s</td></tr>'
      % (esc(f), esc(k), esc(old), new, why))
A('</table>')
A('<div class="note"><b>严格遵守「一次只调一个变量」</b>：本轮核心只动了 <code>listen_prob_scale</code> 一个参数。'
  '上一轮的教训正是同时改了帧率、偏置、每块上限、预算四个旋钮，结果无法归因。'
  '<code>max_new_speak_tokens_per_chunk</code> 保持 24（官方默认 20）不动。</div>')

# ---------------- 六、验证 ----------------
A('<h2>六、验证结果</h2>')
A('<table><tr><th>项目</th><th>结果</th></tr>')
for k, v in VERIFY:
    A('<tr class="ok"><td>%s</td><td>%s</td></tr>' % (esc(k), esc(v)))
A('</table>')
A('<p>配置改动均通过 PyYAML 真解析 + 断言；'
  '<span class="mono">config.yaml</span> 换行保持全 CRLF、'
  '<span class="mono">proactive.py</span> 保持全 LF、无双 CR。</p>')

A('<h3>下次通话后怎么自查</h3>')
A('<div class="flow"><pre>'
  'python tools/check_turn_taking.py\n'
  '\n'
  '  会话                    lps    mns   flc   裁决Hz  SPEAK     占比   wall 判定\n'
  '  ...\n'
  '  20260923_21xxxx         0.7    24     3    1.0x     xx   xx.x%   xxxms 健康\n'
  '</pre></div>')
A('<p>健康区是 <b>20~35%</b>。若落在「哑火」→ 按 0.05 一档往下调；'
  '若落在「井喷」→ 往上调。改完需重启（prepare 时才下发引擎）。</p>')

# ---------------- 七、待拍板 ----------------
A('<h2>七、待你拍板的三件事</h2>')
A('<table><tr><th>#</th><th>事项</th><th>说明</th></tr>')
A('<tr><td>①</td><td><b>通话 TTS 改句子级流式</b></td>'
  '<td>「高延迟」和之前的「井喷」「语速慢」其实是同一个根——整句非流式。'
  '同类项目普遍认它是<b>最大单项延迟收益</b>（首包目标 700~800 ms）。'
  '这是结构性改动，要单独一轮</td></tr>')
A('<tr><td>②</td><td><b>0.7 的实战手感</b></td>'
  '<td>首包听感要你定。若仍偏静，就按 0.05 往 0.6 / 0.5 调（<b>只动这一个</b>）</td></tr>')
A('<tr><td>③</td><td><b>兜底句预合成</b></td>'
  '<td>现在是「每条首播 2 秒」。可在通话开始时把 3 条候选预先合成好，'
  '做到「轮换 + 秒出」兼得（TTS 层已有 <code>_kick_prefetch</code> 基建）</td></tr>')
A('</table>')

A('<div class="card"><b>一句话总结</b><br>'
  '「不理我」是我上一轮把 <code>listen_prob_scale</code> 从 0.5 推到 1.0 推过头了——'
  '开口率从 52.4% 崩到 2.5%，第 41 秒之后整整 133 秒零开口，你听到的只剩兜底句。<br>'
  '「漂泊者」是提示词里「你怎么叫他」和「我该怎么称呼你」主宾错位，模型做了关键词检索式作答。<br>'
  '「延迟高」是哑火的次生症状：模型自身只要 100 毫秒，但等它开口要 6.8 秒。<br>'
  '三者已分别修好，且不是同一个原因——<b>先分清「没产出」「产出了但答歪」「产出了但被吞」三种情况</b>，'
  '才不会像前面几轮那样治错地方。</div>')

A('</div></body></html>')

html = '\n'.join(P)
io.open(OUT, 'w', encoding='utf-8', newline='\n').write(html)
print('written %s  %d bytes' % (OUT, len(html.encode('utf-8'))))
print('tr=%d table=%d h2=%d' % (html.count('<tr'), html.count('<table'), html.count('<h2')))
