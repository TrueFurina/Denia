# -*- coding: utf-8 -*-
"""生成本轮报告：井喷式吐话 + 音色实测（20:08:43 那通）"""
import json, io, os, html
import numpy as np

SP = r'G:\ai对话\11'
TRACE = os.path.join(SP, 'data/call_trace_omni_20260923_200843.jsonl')
OUT = os.path.join(SP, 'data/diag_gush_20260923')
os.makedirs(OUT, exist_ok=True)

# ---------------- 读 trace ----------------
rows = []
for ln in io.open(TRACE, encoding='utf-8', errors='replace'):
    ln = ln.strip()
    if not ln:
        continue
    try:
        rows.append(json.loads(ln))
    except Exception:
        continue
rows.sort(key=lambda e: e.get('t', ''))
byev = {}
for e in rows:
    byev.setdefault(e.get('ev'), []).append(e)

emits = byev.get('emit', [])
tts_recv = byev.get('tts_recv', [])
tts_begin = byev.get('tts_begin', [])
frames = byev.get('frame', [])
drops = [e for e in rows if e.get('ev') in ('tts_drop', 'speak_drop', 'speak_defer')]

# 井喷窗：20:10:14 ~ 20:10:37
gush = [e for e in emits if '20:10:1' in e.get('t', '') or '20:10:2' in e.get('t', '')
        or '20:10:3' in e.get('t', '')]
gush_recv = [e for e in tts_recv if e.get('t', '') >= '20:10:14']
gush_frames = [e for e in frames if e.get('t', '') >= '20:10:10']

# ---------------- 音色数据 ----------------
# F0 一律用 PYIN 读数（§2cg 红线：自相关会错 15%）；分带能量用 FFT
tim = json.load(io.open(os.path.join(SP, 'data/_timbre_session2008_pyin.json'), encoding='utf-8'))
BASE_HZ = 270.5   # 完整素材 daniya_ref_0813_24k.wav 的 PYIN 中位
sess = [r for r in tim if r['tag'] == 'session20:08']
ref_plus = [r for r in tim if r['tag'] == 'REF_plus']
ref_flash = [r for r in tim if r['tag'] == 'REF_flash']
ref_ts = [r for r in tim if r['tag'] == 'REF_truesrc']


def med(rows_, k):
    v = [r[k] for r in rows_ if isinstance(r.get(k), (int, float))]
    return float(np.median(v)) if v else float('nan')


def span_semi(rows_):
    """句间 F0 中位跨度（半音）；剔除 <1.5s 短句（§2cg）"""
    f0s = [r['f0_med'] for r in rows_
           if r.get('f0_med') and (r.get('dur') or 0) >= 1.5]
    if len(f0s) < 2:
        return float('nan'), float('nan')
    return (max(f0s) - min(f0s)), 12 * np.log2(max(f0s) / min(f0s))


def dev_semi(m):
    return 12 * np.log2(m / BASE_HZ)


short = [r for r in sess if r['dur'] <= 3.5]
long_ = [r for r in sess if r['dur'] > 10]

E = html.escape


def row(cells, cls=''):
    return '<tr%s>%s</tr>' % ((' class="%s"' % cls) if cls else '',
                              ''.join('<td>%s</td>' % c for c in cells))


# ================= HTML =================
CSS = '''
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
'''

P = []
P.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
P.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
P.append('<title>井喷式吐话 + 音色实测 · 2026-09-23 20:08</title>')
P.append('<style>%s</style></head><body><div class="wrap">' % CSS)
P.append('<h1>「井喷式吐了一大堆话」+ 音色实测</h1>')
P.append('<div class="sub">对象：2026-09-23 20:08:43 那通（113.3s）· 进程 20:07:20 启动'
         '（上轮新参数已生效）<br>数据源：<code>data/call_trace_omni_20260923_200843.jsonl</code>、'
         '<code>logs/run_20260923_200720.log</code>、<code>src/ui/web/assets/chat_audio/*.wav</code>、'
         '<code>data/voice_switch_plus_20260923/*.wav</code></div>')

# ---- 结论卡 ----
P.append('<h2>一、结论先说</h2>')
P.append('<div class="card"><b>井喷 = 三件事叠在一起，其中一件是我上轮补丁的漏洞。</b><ul>'
         '<li><b>①模型侧空转</b>：20:10:16–20:10:37 的 <b>21 秒里她连续开口 13 轮</b>，'
         '内容全是空泛应和（"确实是个不错的想法呀"原句出现 <b>2 次</b>），'
         '而且<b>完全不回答</b>你 20:10:16 说的「你可以聊什么」。</li>'
         '<li><b>②闸门失效（我的锅）</b>：上轮为治"说一半"加的"连续产出续接"'
         '（<code>0 &lt; since_ai &lt;= 8</code>）会<b>自锁</b> —— 她每次开口都会刷新计时，'
         '条件永远成立 ⇒ 一次发声买到<b>无限时长独白</b>。</li>'
         '<li><b>③TTS 队列追不上</b>：她 1 秒吐 1 句，云端整句合成要 2~4 秒/句 ⇒ 队列只涨不降，'
         '听感就是"后面一直说了一大堆"。</li></ul></div>')
P.append('<div class="bad-note"><b>"静音帧强制聆听"这道闸本轮全程没起作用</b>：'
         '每一帧 <code>force_listen=true</code>（共 50+ 帧），模型照样开口 13 次。'
         '另外上轮我把通话档 <code>listen_prob_scale</code> 从 1.0 压到 <b>0.5</b>（为治"哑火"），'
         '而这个值是<b>越大越倾向聆听</b> ⇒ 治哑火的代价天然就是话痨。同一旋钮两头挪，这次挪过头了。</div>')

P.append('<div class="kpi">'
         '<div><b class="bad">22 → 9</b><span>UI 上屏 22 句 → 真出声 9 句（41%）</span></div>'
         '<div><b class="bad">13 轮 / 21s</b><span>井喷窗内连续开口轮数</span></div>'
         '<div><b class="warn">5 次</b><span>TTS 排队过期（等 8.1~16.9s）＋1 次被质量门截下</span></div>'
         '<div><b class="ok">1 次</b><span>插话打断成功（barge 0.03 已生效）</span></div>'
         '</div>')

# ---- 井喷时间线 ----
P.append('<h2>二、井喷时间线（20:10:14 → 20:10:37）</h2>')
P.append('<div class="card">你 20:10:15 出声插话（RMS 0.0318）成功打断了她，'
         '20:10:16 ASR 落定「你可以聊什么」。然后 —— <b>她开始自问自答 21 秒</b>，'
         '你此后再没说话（麦克风 RMS 掉回 2e-4 底噪档）。</div>')
P.append('<table><tr><th>时刻</th><th>她打的字</th><th>字数</th><th>闸门</th><th>TTS</th></tr>')
for e in gush:
    t = e.get('t', '')
    txt = str(e.get('text', ''))
    ch = e.get('chars', '')
    # 按**文本前缀**匹配（时间戳截断不可靠：emit/tts_recv 相差几十毫秒）
    key = txt[:10]

    def _same(x):
        return str(x.get('text', ''))[:10] == key

    sp = [d for d in drops if _same(d)]
    gate = '<span class="pill o">放行</span>'
    if any(d.get('ev') == 'speak_defer' for d in sp):
        gate = '<span class="pill b">暂存待补发</span>'
    elif any(d.get('ev') == 'speak_drop' for d in sp):
        gate = '<span class="pill b">拦下(%s)</span>' % E(
            str([d.get('reason') for d in sp if d.get('ev') == 'speak_drop'][0]))
    rv = [r for r in gush_recv if _same(r)]
    if rv:
        tts = ('<span class="pill w">进队列</span>' if not rv[0].get('blocked')
               else '<span class="pill b">质量门截下(blocked)</span>')
        if any(r.get('ev') == 'tts_begin' and _same(r) for r in tts_begin):
            tts += ' <span class="pill o">已出声</span>'
    else:
        tts = '<span class="pill n">未进队列</span>'
    tr = txt if len(txt) <= 46 else txt[:46] + '…'
    P.append(row([E(t), E(tr), ch, gate, tts],
                 'warn' if '暂存' in gate or '拦' in gate else ''))
P.append('</table>')
P.append('<div class="note">21 秒 13 轮里，<b>7 轮在问你"想聊什么"</b>'
         '（20:10:18/20/21 + 3 句"你想聊点什么呢"），另 5 轮是纯夸奖填充。'
         '20:10:14 那 202 字的纯复读被<b>质量门（garbled）</b>拦下了 —— 说明重复检测在起作用，'
         '但它只抓到了最长那一条，7~22 字的短复读全部放行。</div>')

# ---- force_listen 证据 ----
P.append('<h3>证据：force_listen 全程为 true</h3>')
P.append('<table><tr><th>时刻</th><th>帧号</th><th>pk</th><th>silence</th><th>voice</th>'
         '<th>force_listen</th><th>她是否在开口</th></tr>')
def _sec(t):
    try:
        h, m, s = t.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return None


open_ts = [_sec(e.get('t', '')) for e in gush if _sec(e.get('t', '')) is not None]
for e in gush_frames[:22]:
    t = e.get('t', '')
    ts = _sec(t)
    speaking = ts is not None and any(0.0 <= ts - o <= 1.6 for o in open_ts)
    P.append(row([E(t), e.get('n'), '%.2e' % (e.get('pk') or 0),
                  '<span class="pill n">True</span>' if e.get('silence') else 'False',
                  str(e.get('voice')),
                  '<span class="pill b">True</span>' if e.get('fl') else 'False',
                  ('<b style="color:#b3261e">是，正在说</b>' if speaking
                   else '<span style="color:#9aa1ad">否（静默/间隔）</span>')],
                 '' if speaking else ''))
P.append('</table>')
P.append('<div class="sub">（截取前 22 帧；50+ 帧全部 <code>fl=True</code>，同期模型开口 13 次。）</div>')

# ---- 音色 ----
P.append('<h2>三、音色：本轮到底是谁在发声</h2>')
P.append('<div class="ok-note"><b>引擎身份已确认</b>：本轮每一条 <code>tts_begin</code> 的 '
         '<code>engine</code> 都是 <b>dashscope_cosy</b>（阿里云百炼 CosyVoice，plus 档）——'
         '没有静默降级到 edge-tts（微软晓伊），也没有走本地声码器。'
         '落盘 wav 全部 24 kHz。</div>')
P.append('<h3>1) 本轮落盘音频 vs 百炼直出 vs 真源（中位数）</h3>')
P.append('<table><tr><th>组</th><th>n</th><th>时长中位</th><th>4–8k</th><th>8–12k</th>'
         '<th>F0 中位 (PYIN)</th><th>偏离基准</th><th>句间跨度</th></tr>')
grp = [('本轮落盘（会话 20:08）', sess, ''), ('百炼 plus 直出', ref_plus, 'ok'),
       ('百炼 flash 直出', ref_flash, ''), ('你上传的真源', ref_ts, 'ok')]
for name, rws, cls in grp:
    _m = med(rws, 'f0_med')
    _sp, _se = span_semi(rws)
    P.append(row([name, len(rws), '%.2fs' % med(rws, 'dur'),
                  '%+.2f dB' % med(rws, 'b4_8k'), '%+.2f dB' % med(rws, 'b8_12k'),
                  '%.1f Hz' % _m,
                  '%+.2f 半音' % dev_semi(_m),
                  ('%.2f 半音' % _se) if _se == _se else '—'], cls))
P.append('</table>')
P.append('<div class="sub">F0 一律 PYIN（<code>tools/f0_compare.py</code>）；'
         '句间跨度 = 各句 F0 中位的 max/min 折半音，已剔除 &lt;1.5s 短句。</div>')
P.append('<div class="ok-note"><b>本轮落盘 vs plus 直出：4–8k 只差 −0.43 dB，8–12k 差 +0.11 dB，'
         'F0 差 +5.4 Hz（≈0.35 半音）</b> ⇒ <b>app 没有对音频做任何加工</b>（无 EQ、无变速变调、无重采样损失）。'
         '"不如百炼直出"不是被软件劣化的。</div>')
P.append('<div class="bad-note"><b>但 plus 直出比你上传的真源暗 2.7 dB（4–8k）/ 3.8 dB（8–12k）</b> ——'
         '这是<b>云端克隆本身的天花板</b>。如果你在控制台试听的是那条<b>克隆样例（原声）</b>，'
         '那它本来就是最亮的，别拿它当 app 的靶子。</div>')

P.append('<h3>2) 本轮"难听"的可归因部分＝短句</h3>')
P.append('<table><tr><th>分组</th><th>n</th><th>时长中位</th><th>4–8k</th><th>8–12k</th>'
         '<th>F0 中位</th><th>句间跨度</th></tr>')
for name, rws in [('短句 ≤3.5s（填充句）', short), ('长句 &gt;10s', long_)]:
    _sp, _se = span_semi(rws)
    P.append(row([name, len(rws), '%.2fs' % med(rws, 'dur'),
                  '%+.2f dB' % med(rws, 'b4_8k'), '%+.2f dB' % med(rws, 'b8_12k'),
                  '%.1f Hz' % med(rws, 'f0_med'),
                  ('%.2f 半音' % _se) if _se == _se else '—'],
                 'bad' if rws is short else 'ok'))
P.append('</table>')
P.append('<div class="card">短句的 <b>8–12k 比长句低 6.48 dB</b>、4–8k 低 2.18 dB ⇒ '
         '听感就是"闷、单薄、不像她"。而本轮通话里大部分恰恰是 6~8 字的填充短句。'
         '<b>长句（14~17s）反而接近甚至优于 plus 直出水准</b>（−13.6 vs −17.4 dB）。'
         '⇒ <b>别用一堆短填充句去评判音色</b>，那测的是短句下限，不是克隆质量。</div>')
P.append('<div class="note"><b>音高基准是准的</b>：本轮落盘 F0 中位 <b>268.9 Hz</b>，'
         '对完整素材基准 270.5 Hz 只偏 <b>−0.10 半音</b> ⇒ 不需要任何 pitch 修正。<br>'
         '<b>⚠️ 一处自我更正</b>：我先前用自相关读到"flash 有 86 Hz 异常低音帧（p10=122.8Hz）"，'
         '按 §2cg 红线改用 PYIN 复测为 <b>221.4 Hz</b> —— 那个结论<b>作废</b>，是自相关选错峰的产物。'
         'plus 的真实代价是<b>句间漂移更大</b>：<b>5.00 半音 vs flash 2.90 半音</b>'
         '（与 §2cf 记录的 2.90→5.00 一致）。<br>'
         '你觉得"正常了一点点"，更可能来自 plus 让<b>兜底句从 4.30s 缩到 3.18s、'
         '占有效时长比 60.9%→26.7%</b>（§2cf）—— 也就是那句最难听的短句出现得少了。</div>')

P.append('<h3>3) 短句逐条</h3>')
P.append('<table><tr><th>时长</th><th>4–8k</th><th>8–12k</th><th>文件</th></tr>')
for r in sorted(short, key=lambda x: x['dur']):
    P.append(row(['%.2fs' % r['dur'], '%+.2f dB' % r['b4_8k'], '%+.2f dB' % r['b8_12k'],
                  '<span class="mono">%s</span>' % E(r['file'])],
                 'bad' if r['b8_12k'] < -20 else ''))
P.append('</table>')

# ---- 为什么"后面一直说" ----
P.append('<h2>四、为什么"后面一直说了一大堆"</h2>')
P.append('<div class="flow"><pre>'
         '她产出一句    20:10:20.1  → 进 TTS 队列\n'
         '                20:10:20.3  → 开始合成（云端整句，非流式）\n'
         '                20:10:21.2  → 首音频（1.1s）\n'
         '下一句产出    20:10:21.6  …… 间隔仅 1.5s\n'
         '\n'
         '⇒ 产出 1 句/1~1.5s，合成 1 句/2~4s ⇒ 队列只涨不降\n'
         '⇒ 排到就过期：20:10:05 等 16.9s、20:10:32 等 8.1/9.1/10.6s、20:10:35 等 9.2s\n'
         '⇒ 期间音频不断档地播 ⇒ 听感「井喷，停不下来」</pre></div>')
P.append('<table><tr><th>计数</th><th>值</th><th>说明</th></tr>')
P.append(row(['emit（UI 上屏）', len(byev.get('emit', [])), '文字全部显示']))
P.append(row(['tts_recv（进队列）', len(byev.get('tts_recv', [])), '差额 = 闸门吞']))
P.append(row(['tts_begin（真出声）', len(byev.get('tts_begin', [])), '差额 = 排队过期']))
P.append(row(['tts_drop stale', len([e for e in rows if e.get('ev') == 'tts_drop' and e.get('reason') == 'stale']),
              '等 8.1/9.1/9.2/10.6/16.9s 后被丢（另有 1 次 reason=blocked 被质量门截下）']))
P.append(row(['tts_interrupt', len(byev.get('tts_interrupt', [])), 'call-barge-in（插话打断）✅']))
P.append('</table>')

# ---- 改动 ----
P.append('<h2>五、本轮改动（需重启生效）</h2>')
P.append('<table><tr><th>#</th><th>文件 / 键</th><th>改动</th><th>为什么</th></tr>')
P.append(row(['1', '<code>config.yaml</code><br><code>duplex_call_talk_budget_s</code>',
              '<b>新增 20.0</b>', '单次发声最多买 20s 连续产出，堵死续接自锁']))
P.append(row(['2', '<code>proactive.py</code> 闸门',
              '续接加 <code>_since_u &lt;= _talk_budget</code>',
              '防自锁 → 独白']))
P.append(row(['3', '<code>config.yaml</code><br><code>duplex_call_continue_s</code>',
              '3.5 → <b>8.0</b>',
              '实测句间间隔 2.0/4.46/5.99s，3.5 会切尾。<b>这个键本来就有</b>——'
              '我上轮误造了 <code>_CALL_CONT_GAP_S</code>，已删除']))
P.append(row(['4', '<code>proactive.py</code>',
              '拦截原因加 <code>talk_budget_exceeded</code>',
              '把"讲太久被拦"和"没人说话被拦"分开']))
P.append('</table>')
P.append('<div class="ok-note"><b>验证</b>：<code>test_call_silence_gate</code> <b>43/43</b>'
         '（新增 A5d/A5e 防自锁回归 + E12/E15）、<code>test_tts_chain</code> 25/25、'
         '<code>test_call_deadair_watchdog</code> 24/24、<code>test_config_yaml_parses</code> 5/5；'
         '三层冒烟通过（YAML 真解析 / 模块真加载 / <code>load_config()</code> 读回 4 个键）。<br>'
         '备份：<code>_bak_talkbudget_*</code></div>')

# ---- 待拍板 ----
P.append('<h2>六、要你拍板的三件事</h2>')
P.append('<div class="card"><ul>'
         '<li><b>① <code>listen_prob_scale</code> 0.5 要不要回调到 0.7？</b>'
         '（该值<b>越大越倾向聆听</b>）上轮 1.0→0.5 治好了"哑火"，本轮反噬成"话痨"。'
         '这是同一旋钮的两端，改后需重连才生效 —— 听感要你定。</li>'
         '<li><b>② force_listen 全程 true 却压不住她开口</b>，需要深挖引擎侧是否真执行'
         '（若协议层没实现，这道闸就是装饰）。</li>'
         '<li><b>③ 通话 TTS 改流式</b>：本轮"后面一直说"和上轮"语速慢"是同一个根'
         '（整句非流式，2~4s/句）。这是唯一的结构性解，需单独一轮。</li>'
         '</ul></div>')

P.append('</div></body></html>')

path = os.path.join(OUT, 'report.html')
io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(P))
print('written', path, os.path.getsize(path), 'bytes')
print('CR', open(path, 'rb').read().count(b'\r'))
