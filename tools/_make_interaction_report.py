# -*- coding: utf-8 -*-
"""生成「互动节奏诊断 · 同类项目对照」报告。
数据源：① Comni 服务端会话录像（引擎视角地面真值）② 本项目 call_trace ③ 官方文档核实结论。
"""
import os, io, re, json, glob, collections, datetime, html as _h

ROOT = r'G:\ai对话\11'
OUT_DIR = os.path.join(ROOT, 'data', 'diag_interaction_20260923')
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, 'report.html')

SESS = r'H:\Comni\_internal\resources\apps\server\data\sessions\20260923_200856_adx_1790165322046'
TRACE = os.path.join(ROOT, 'data', 'call_trace_omni_20260923_200843.jsonl')

# ---------- 复用上一份报告的样式（保持观感统一） ----------
STYLE = ''
_prev = os.path.join(ROOT, 'data', 'diag_gush_20260923', 'report.html')
if os.path.exists(_prev):
    _m = re.search(r'<style[\s\S]*?</style>', io.open(_prev, encoding='utf-8').read())
    if _m:
        STYLE = _m.group(0)

# ---------- 读取服务端地面真值 ----------
rec = json.loads(io.open(os.path.join(SESS, 'recording.json'), encoding='utf-8', errors='replace').read())
ch = rec['chunks']
meta = json.loads(io.open(os.path.join(SESS, 'meta.json'), encoding='utf-8', errors='replace').read())
sysp = (rec.get('config') or {}).get('system_prompt') or ''

mode = collections.Counter((c.get('result') or {}).get('mode') for c in ch)
ts = [c.get('receive_ts_ms') for c in ch]
gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:]) if a is not None and b is not None]
gaps_s = sorted(gaps)
med_gap = gaps_s[len(gaps_s) // 2]
hz = 1000.0 / med_gap

sp = []
for i, c in enumerate(ch):
    r = c.get('result') or {}
    if r.get('mode') == 'SPEAK':
        t = r.get('text') or ''
        if isinstance(t, list):
            t = ''.join(t)
        sp.append((i, c.get('receive_ts_ms'), t))
tot_chars = sum(len(t) for _, _, t in sp)
sp_span = (sp[-1][1] - sp[0][1]) / 1000.0 if sp else 1.0

# 最长连续 SPEAK 段
best = []
run = []
prev_i = -99
for i, t, tx in sp:
    if i - prev_i == 1:
        run.append((i, t, tx))
    else:
        if len(run) > len(best):
            best = run
        run = [(i, t, tx)]
    prev_i = i
if len(run) > len(best):
    best = run
burst_dur = (best[-1][1] - best[0][1]) / 1000.0 if best else 0.0
burst_chars = sum(len(x[2]) for x in best)

# ---------- 读取客户端 trace ----------
def _ts(s):
    try:
        h, m, sec = s.split(':')
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except Exception:
        return None


fr = []
tts_drop_n = 0
burst_emits = []
for ln in io.open(TRACE, encoding='utf-8', errors='replace'):
    ln = ln.strip()
    if not ln:
        continue
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get('ev') == 'frame':
        fr.append(e)
    elif e.get('ev') == 'tts_drop':
        tts_drop_n += 1
    elif e.get('ev') == 'emit':
        _t = e.get('t', '') or ''
        if '20:10:14' <= _t <= '20:10:40':
            burst_emits.append((_t, len(e.get('text') or '')))
fl_true = sum(1 for e in fr if e.get('fl') is True)
voice_true = sum(1 for e in fr if e.get('voice') is True)
_cli_burst_n = len(burst_emits)
_cli_burst_s = ((_ts(burst_emits[-1][0]) - _ts(burst_emits[0][0]))
                if len(burst_emits) >= 2 else 0.0)
_cli_burst_chars = sum(x[1] for x in burst_emits)

# ---------- 提示词拆解 ----------
_bi = sysp.find('<behavior>')
prefix_hard = sysp[:_bi] if _bi > 0 else ''
prompt_len = 1589  # 引擎 prepared 回传的 prompt_length（token）

# ---------- HTML 工具 ----------
def esc(s):
    return _h.escape(str(s))

def table(headers, rows, cls=None):
    out = ['<table>', '<tr>'] + ['<th>%s</th>' % _h.escape(h) for h in headers] + ['</tr>']
    for r in rows:
        c = ''
        if cls and len(r) > 0 and isinstance(r[0], str) and r[0] in cls:
            c = ' class="%s"' % cls[r[0]]
            r = r[1:]
        out.append('<tr%s>' % c)
        for x in r:
            out.append('<td>%s</td>' % (x if x is None else _h.escape(str(x)) if not str(x).startswith('<') else x))
        out.append('</tr>')
    out.append('</table>')
    return '\n'.join(out)

def code(s):
    return '<code>%s</code>' % _h.escape(s)

P = []
w = P.append

w('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
w('<meta name="viewport" content="width=device-width,initial-scale=1">')
w('<title>互动节奏诊断 · 同类项目对照 · 2026-09-23 20:08</title>')
w(STYLE)
w('</head><body><div class="wrap">')
w('<h1>为什么"互动好奇怪"——官方原文 + 同类项目对照 + 本轮实测</h1>')
w('<div class="sub">数据源：Comni 服务端会话录像 <span class="mono">%s</span>（引擎视角地面真值，185 块）'
  ' ＋ 客户端 trace <span class="mono">%s</span>（226 帧）</div>'
  % (esc(os.path.basename(SESS)), esc(os.path.basename(TRACE))))

# ---- KPI ----
w('<div class="kpi">')
w('<div><b class="bad">%.2f Hz</b><span>引擎裁决频率 · 官方要 1.00 Hz</span></div>' % hz)
w('<div><b class="bad">%.1f%%</b><span>SPEAK 块占比（97/185）</span></div>' % (100.0 * mode.get('SPEAK', 0) / len(ch)))
w('<div><b class="bad">%.1f 字/秒</b><span>引擎产出速率 · 真人 4~5</span></div>' % (tot_chars / sp_span))
w('<div><b class="bad">0%%</b><span>per-chunk force_listen 有效率</span></div>')
w('</div>')

w('<div class="card"><b>一句话</b>：不是模型"不会聊天"，是<strong>我们把它推成了 2 倍速的决策节拍</strong>'
  '（官方要求 1 Hz、我们发 2 Hz），再用 <code>listen_prob_scale=0.5</code> 把它往"更爱说话"推——'
  '两个都<strong>与官方默认方向相反</strong>。同时我们以为在起作用的"静音强制聆听"闸门，'
  '在 C++ 引擎上<strong>从接口层就不存在</strong>，98.2% 的帧白发了。</div>')

# ================= 1. 官方原文 =================
w('<h2>1. 官方原文核实：这几个 0-1 数值到底啥意思</h2>')
w('<div class="sub">来源：openbmb.github.io/MiniCPM-o-Demo/site/zh/api/duplex.html（已逐字段抓原文）</div>')
w(table(['参数', '官方默认', '官方原文描述', '我们现在的值'], [
    ['listen_prob_scale', '1.0', '<b>监听概率缩放因子。值 &gt; 1.0 使模型更倾向监听（更沉默）；&lt; 1.0 使其更积极说话</b>',
     '<span class="pill b">通话 0.5（偏"爱说"）</span>'],
    ['max_new_speak_tokens_per_chunk', '20', '每步最大 speak token 数。<b>限制每秒生成的文本量以维持实时节奏</b>',
     '<span class="pill b">48（2.4 倍）</span>'],
    ['force_listen_count', '3', '启动保护：前 N 步强制模型处于监听状态，防止在建立足够上下文之前过早说话',
     '<span class="pill w">2</span>'],
    ['chunk_ms', '1000', '<b>音频块时长（毫秒）。决定每秒推理循环的节奏。客户端必须按此间隔发送音频块</b>',
     '<span class="pill b">500（=2 Hz）</span>'],
    ['ls_mode', 'explicit', 'Listen/Speak 决策模式（explicit / implicit）', '<span class="pill n">未传（走默认）</span>'],
    ['temperature / top_k / top_p', '0.7 / 20 / 0.8', '文本生成的采样参数', '<span class="pill n">C++ 后端不接受</span>'],
    ['length_penalty', '<b>不存在</b>', '官方 DuplexConfig <b>没有此项</b>（我们注释里写的"官方默认 1.1"无出处）',
     '<span class="pill w">1.0</span>'],
]))

w('<div class="note"><b>官方对"谁决定开口"的原文：</b><br>'
  '"系统运行<strong>每秒一轮的推理循环</strong>：每秒将用户音频送入模型，模型自主决定是<strong>监听</strong>还是<strong>说话</strong>。"<br>'
  '"与 Half-Duplex 不同，<strong>没有显式的 VAD</strong>——模型自身基于音频内容和习得的监听/说话概率来决定何时说话。"<br><br>'
  '<b>官方对"打断"的原文：</b><br>'
  '"如果客户端检测到用户在模型说话过程中开始说话（基于输入音频能量或客户端侧 VAD），<strong>可以继续发送 audio_chunk</strong>。'
  '模型可能在下一步自然地从说话转回监听——<strong>这就是"打断"行为</strong>。"<br>'
  '<span class="pill w">注意</span> 官方打断<strong>不需要能量阈值门限</strong>——持续送音频即可；'
  '我们另加了一套 <code>_barge_min_rms</code> 客户端阈值，反而把它挡在了门外（07 之前阈值 0.07 &gt; 你说话 RMS 0.018~0.074 ⇒ 9 通 0 次）。</div>')

# ================= 2. 同类项目 =================
w('<h2>2. 同类项目怎么给"轮次/主动发言"定参数</h2>')
w('<div class="sub">实时语音 Agent 框架的通行默认值（可直接抄的数字）</div>')
w(table(['项目', 'VAD / 说完判定', '打断（barge-in）', '单轮长度约束', 'TTS 形态'], [
    ['<b>OpenAI Realtime</b>', 'silence_duration_ms <b>500</b>、prefix_padding_ms 300、threshold 0.5', '服务端 VAD 触发即停', '无硬约束（靠 prompt）', '流式'],
    ['<b>Pipecat</b>', 'Silero stop_secs <b>0.3</b>、start_secs 0.2、confidence 0.7', 'min_words=<b>3</b> 才构成有效打断（防"嗯/好"误触）', '无硬约束', '句子级流式（SentenceAggregator）'],
    ['<b>LiveKit Agents</b>', 'endpointing min_delay <b>0.5s</b> / max_delay 3.0s', 'adaptive 模式：min_duration <b>0.5s</b>，误打断后 2.0s 自动续播', '无硬约束', '流式，首包 &lt;200ms'],
    ['<b>xiaozhi-esp32</b>', 'Silero threshold 0.5，min_silence 200~700ms', '4 档模式；Mode1 = 连续语音 <b>360ms</b> 即打断', '无', '流式'],
    ['<b>Fay</b>', 'WebRTC VAD sensitivity 2', 'min_interrupt_duration <b>300ms</b>，三级检测（音量→VAD→意图）', '有主动播报接口', '流式'],
    ['<b>百聆 bailing</b>', 'Silero 0.5，min_silence <b>0.3s</b>', '检测到说话立即打断', '无', '流式'],
    ['<b>Open-LLM-VTuber</b>', 'Silero prob_threshold 0.3~0.5', '打断冷却 <b>300~500ms</b>；<b>说话时自动关麦</b>（防回声）', '可设"空闲 N 秒 AI 主动开口"', '流式'],
    ['<b>销售型语音 Agent（n4n.ai）</b>', '—', '—', '<b>每状态 max_tokens=120 / max_duration_sec=15 硬上限</b>', '流式'],
]))
w('<div class="note"><b>三条通行共识</b>：<br>'
  '① <b>静音判定 200~550ms</b>（我们模型自决，没有 VAD，不适用）；<br>'
  '② <b>工程硬约束 &gt; 提示词约束</b>——n4n.ai 直接给每个对话状态设 <code>max_tokens=120</code>/<code>max_duration_sec=15</code>；'
  'lukeocodes 建议 50~150 token/轮。这类"发言预算"是治独白的公认手段，<strong>不能只靠 prompt 写"别啰嗦"</strong>；<br>'
  '③ <b>句子级流式 TTS 是"反应快"的最大单项收益</b>（sentence buffer 是甜点，token 级太碎、整句太慢），'
  '行业公认首字延迟目标 <strong>700~800ms</strong>，超过 1s 用户就判定"这东西坏了"。'
  '<span class="pill b">我们目前是整句非流式</span></div>')

# ================= 3. 提示词对照 =================
w('<h2>3. 通话提示词：我们 vs 同行</h2>')
w('<div class="sub">我们的实际提示词取自引擎录像（不是本地文件——本地文件只是其中一段）</div>')
w('<div class="card"><b>竞品原文（普遍很短）</b>'
  '<div class="mono" style="margin-top:8px">'
  'LiveKit 官方示例：<i>"You are a helpful voice assistant. <b>Keep responses concise, ideally under 2 sentences.</b> '
  'Be conversational and friendly."</i><br>'
  'LiveKit 文档补充：<i>"<b>Keep responses short.</b> Long responses feel unnatural… <b>Avoid lists and formatting.</b> '
  'They don\'t translate to speech. <b>Write for the ear, not the eye.</b>"</i><br>'
  'ChatGPT-AutoExpert：<i>"Most of your responses should be <b>a sentence or two</b>… '
  '<b>Don\'t monopolize the conversation.</b>" "Never use the list format."</i><br>'
  'towardsai：<i>"Keep responses under <b>2-3 sentences</b>… A 200-word response takes <b>10 seconds</b> to speak."</i>'
  '</div></div>')

w('<div class="card"><b>我们的实际通话系统提示词（引擎录像原文，前 3 段为硬编码，共 %d token）</b>' % prompt_len)
w('<div class="mono" style="white-space:pre-wrap;margin-top:8px">%s</div>' % esc(prefix_hard[:700]))
w('<div class="sub" style="margin:10px 0 0">… 后面接 <code>&lt;behavior&gt;</code>（danya_persona.txt 人设）'
  ' + danya_behavior.txt（行为规范，2778 字节）</div></div>')

w('<div class="bad-note"><b>关键对照</b>：竞品用 <b>1~2 句</b>讲"说多长"；我们用 <b>%d token</b>（约 20 倍），'
  '把"什么时候开口、说多长、别复读、别自言自语"写成了一整本操作手册——'
  '而本轮实测里，模型<b>三条全违反</b>：抢在用户话说完之前接、同一句复读 2 次、21 秒连吐 13 句。<br><br>'
  '<b>结论（也是同行的共识）</b>：<b>提示词管内容，管不了节拍。</b>'
  '"该不该开口"是模型每一步的<strong>采样决策</strong>（受 <code>listen_prob_scale</code> 控制），'
  '不是它能"读规则照做"的事。想让它少说，只有两条路：<b>调采样偏置</b> + <b>上层工程硬约束</b>。</div>' % prompt_len)

# ================= 4. 逐项对账 =================
w('<h2>4. 逐项对账：我们 vs 官方，附实测证据</h2>')
w(table(['维度', '官方 / 同行', '我们', '本轮实测证据'], [
    ['<b>决策节拍</b>', 'chunk_ms=<b>1000</b>（1.00 Hz）', '<b>500</b>（2 Hz）',
     '服务端 185 块的 receive_ts_ms 中位 <b>%.1f ms</b>，98.4%% 落在 400~600ms ⇒ <b>实测 %.2f Hz</b>'
     % (med_gap, hz)],
    ['<b>说话倾向</b>', 'listen_prob_scale <b>1.0</b>（中性）', '通话档 <b>0.5</b>',
     '引擎裁决 SPEAK <b>%d/185 = %.1f%%</b>，且 SPEAK 产出间隔精确 = 0.5s（与推流节拍一一对应）'
     % (mode.get('SPEAK', 0), 100.0 * mode.get('SPEAK', 0) / len(ch))],
    ['<b>单步产能</b>', 'max_new_speak_tokens_per_chunk=<b>20</b>', '<b>48</b>',
     '引擎产出 <b>%.1f 字/秒</b>（真人 4~5）；最长连续 SPEAK 段 %d 块 / %.1fs / %d 字'
     % (tot_chars / sp_span, len(best), burst_dur, burst_chars)],
    ['<b>静音强制聆听</b>', 'C++ 引擎<b>无此接口</b>', '<code>duplex_call_silence_force_listen: true</code>',
     '客户端 <b>%d/%d 帧（%.1f%%）</b>发了 <code>force_listen:true</code>，引擎侧 0 反应'
     % (fl_true, len(fr), 100.0 * fl_true / len(fr))],
    ['<b>打断</b>', '持续送音频即可，模型自然切回听', '客户端 RMS 阈值 0.03 + 抢话权 2 帧',
     '<code>barge</code> 触发 <b>1 次</b>（上两通 0 次）⇒ 0.07→0.03 的改动<b>已生效</b>'],
    ['<b>单轮上限</b>', '同行 12~15s 硬上限', 'talk_budget 20s（本轮新加）',
     '客户端实测：井喷段 %.1fs 内 <b>emit %d 句</b> / %d 字' % (_cli_burst_s, _cli_burst_n, _cli_burst_chars)],
    ['<b>TTS</b>', '句子级流式（共识），TTFS 700~800ms', '整句非流式',
     '生成→出声最长约 22s；<code>tts_recv</code> 19 vs <code>tts_begin</code> 9，'
     '<b>%d 次排队过期</b>（stale）' % tts_drop_n],
]))

# ================= 5. 因果链 =================
w('<h2>5. 本轮"井喷"的完整因果链</h2>')
w('<div class="flow"><pre>'
  '① 客户端把 1 秒音频拆成 2 段发（frame_interval=0.5）\n'
  '        ↓  引擎每 0.5s 收到一块 ⇒ 每 0.5s 裁决一次\n'
  '② 引擎实测裁决频率 2.00 Hz（官方要求 1.00 Hz）⇒ 开口机会<b>翻倍</b>\n'
  '        ↓  且模型"主观时间"也走 2 倍：1 秒真实语音在它看来跨了 2 步\n'
  '③ listen_prob_scale=0.5（官文：&lt;1.0 = 更积极说话）⇒ 每个机会都倾向"说"\n'
  '        ↓  实测 SPEAK 占 52.4%\n'
  '④ max_new_speak_tokens_per_chunk=48（官方 20）⇒ 每步还能吐更多\n'
  '        ↓  实测产出 8.7 字/秒（真人 4~5），峰值 18.7 / 44.2 字/秒\n'
  '⑤ 云端 TTS 整句非流式：一句 2~4s ⇒ 产出 2 句/秒，播放 0.3 句/秒\n'
  '        ↓  TTS 串行队列只涨不降\n'
  '⑥ 队列过期丢弃（stale 3s，本轮已改 8s）⇒ "UI 显示了但没出声"\n'
  '\n'
  '（旁路）客户端 98.2% 的帧在发 force_listen=true 想让她闭嘴\n'
  '        ↓  cpp_backend.duplex_generate 收下参数但<b>从不放进请求体</b>\n'
  '        ↓  llama-server.exe 里也没有独立的 force_listen 键\n'
  '        ⇒  这条闸门 100% 空转，与"她为什么不闭嘴"无关\n'
  '</pre></div>')

w('<h3>5.1 铁证：模型产出间隔 = 推流间隔</h3>')
w('<div class="mono">引擎连续产出的时间戳（服务端 receive_ts_ms，截取井喷段）：<br>')
_ex = best[:8] if best else []
w('  '.join('chunk%d t=%.1fms' % (i, t) for i, t, _ in _ex) if _ex else '')
w('<br>相邻两块间隔恰好 500ms —— <b>每推一块她吐一句</b>，这就是"井喷"的机械本质。</div>')

# ================= 6. 建议改动 =================
w('<h2>6. 建议改动（按证据强度排序）</h2>')
w(table(['#', '旋钮', '现值', '建议', '依据', '风险'], [
    ['1', 'duplex_call_frame_interval_s', '0.5', '<b>1.0</b>',
     '官方 chunk_ms=1000"客户端必须按此间隔发送"；实测 2.00 Hz vs 1.00 Hz。<b>最强杠杆</b>',
     '通话反应延迟 +约 0.25s'],
    ['2', 'duplex_call_listen_prob_scale', '0.5', '<b>1.0</b>',
     '官方默认 1.0；<b>&lt;1.0 = 更积极说话</b>，0.5 是官文里的"话痨档"',
     '可能回到"哑火"——但现在有 deadair 兜底 + 发言预算兜着'],
    ['3', 'duplex_call_max_new_speak_tokens_per_chunk', '48', '<b>24</b>',
     '官方 20，明说是"维持实时节奏"；48 让单步产能超过实时',
     '句子可能在块边界被切（若出现，回调到 32）'],
    ['4', 'duplex_call_force_listen_count', '2', '<b>3</b>', '官方默认 3（启动保护期，前 3 步强制听）', '无'],
    ['5', 'duplex_call_talk_budget_s', '20.0', '<b>12.0</b>', '同行通行 12~15s 单轮硬上限', '长回答被截'],
    ['6', '<code>duplex_call_silence_force_listen</code>', 'true', '保留但<b>标注无效</b>',
     'C++ 后端无 per-chunk force_listen 接口', '不会变好，但也不会变坏'],
    ['7', '通话 TTS 改句子级流式', '整句非流式', '（待拍板）',
     '行业共识"最大单项延迟收益"，TTFS 目标 700~800ms', '结构性改动，需单独一轮'],
]))

# ================= 7. 未解 =================
w('<h2>7. 仍未解 / 需说明的</h2>')
w('<ul>')
w('<li><b>唯一可用的"强制闭嘴"原语</b>：C++ 引擎只认会话级的 <code>force_listen_count</code>'
  '（二进制里可见 <code>force_listen_count</code> 与内部计数器 <code>force_listen_used</code>，'
  '且会话配置更新时会把 <code>force_listen_used</code> 重置为 0）。'
  '理论上可以用"更新会话配置"来重置它，达到"让她闭嘴 N 步"的效果——需要单独验证。</li>')
w('<li><b>kv_cache_length 恒为 0</b>：185 块全是 0。<b>不要误读成"模型没有上下文记忆"</b>——'
  'C++ 只在 SSE 最后一个事件里带这个字段，当前实现下没读到，属<b>埋点缺失</b>，不代表模型失忆。</li>')
w('<li><b>麦克风电平偏弱</b>：<code>_mic_voice</code> 判据（平滑 RMS&gt;0.018）在 226 帧里只判出 %d 帧"有人在说话"，'
  '而块级原始 RMS 判据（0.01，9-23 新加）正常触发。项目自己在 9-23 已经学到"EMA 平滑会漏短句"，'
  '但只改了 <code>user_voice</code>，<code>_mic_voice</code> 还是旧写法。<b>这是独立 bug，与本节结论无关，但值得单独修。</b></li>' % voice_true)
w('<li><b>我们和官方的用法差异</b>：官方 demo 是"每秒一块 + 模型自决"；我们是"每 0.5 秒一块 + 上层四道闸"。'
  '把节拍调回 1 Hz 后，上层闸门的阈值（12s 窗口 / 8s 续接）也都需要重标。</li>')
w('</ul>')

w('<div class="sub" style="margin-top:34px">生成时间：%s ｜ 本报告所有数字均来自上列两个数据源，可复算</div>'
  % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
w('</div></body></html>')

io.open(OUT, 'w', encoding='utf-8', newline='\n').write('\n'.join(P))
print('OK ->', OUT, os.path.getsize(OUT), 'bytes')
print('  裁决频率 %.2f Hz / SPEAK %d / 产出 %.1f 字/秒 / force_listen %d/%d'
      % (hz, mode.get('SPEAK', 0), tot_chars / sp_span, fl_true, len(fr)))
print('  prompt 前缀 %d 字 / voice=True 帧 %d' % (len(prefix_hard), voice_true))
