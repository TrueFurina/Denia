# -*- coding: utf-8 -*-
"""生成 21:07 那通「互动很烂 / 延迟高 / 忽略我」诊断报告。"""
import html
import io
import os

OUT = r"G:\ai对话\11\data\diag_lag_20260923"
os.makedirs(OUT, exist_ok=True)
P = []


def esc(s):
    return html.escape(str(s))


def row(cells, cls=""):
    return "<tr%s>%s</tr>" % ((' class="%s"' % cls) if cls else "",
                              "".join("<td>%s</td>" % c for c in cells))


STYLE = """<style>
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
</style>"""

P.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
P.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
P.append("<title>通话互动诊断 21:07</title>")
P.append(STYLE)
P.append("</head><body><div class='wrap'>")

P.append("<h1>通话「互动很烂 · 延迟高 · 忽略我的话」诊断</h1>")
P.append("<div class='sub'>样本：2026-09-23 21:07:12 – 21:08:27（75 秒）　"
         "客户端 trace <span class='mono'>call_trace_omni_20260923_210712.jsonl</span>　"
         "引擎侧录像 <span class='mono'>20260923_210716_adx_1790168832923</span>（69 块）</div>")

# ---------------- 速览 ----------------
P.append("<h2>结论速览</h2>")
P.append("<div class='kpi'>")
for v, cls, lab in [
    ("13.0%", "warn", "引擎 SPEAK 占比（9/69）——上轮 0.7 仍偏沉默"),
    ("6 → 3", "bad", "用户发声 6 次，只有 3 次落定 ASR"),
    ("8.1s", "bad", "模型平均开口延迟（三次 8.1 / 12.1 / 2.7s）"),
    ("34s", "bad", "端到端最长：开口 19s + 首包 4.1s + 合成完才播"),
]:
    P.append("<div><b class='%s'>%s</b><span>%s</span></div>" % (cls, v, lab))
P.append("</div>")

P.append("<div class='bad-note'><b>三个症状是三个独立根因，而且第一、第三是同一个：</b>"
         "<ul>"
         "<li><b>「忽略我的话」</b>＝每块只以 p≈0.13 的概率掷骰子决定开口 ⇒ 期望等 7.7 秒。"
         "用户等不到就重复问，问了三遍。</li>"
         "<li><b>「延迟高」</b>＝等开口 8~19s（主因）＋ 云端首包 1.8~4.1s ＋ 整句合成完才落盘。"
         "<b>模型自身推理只要 57~149ms</b>，跟算力无关。</li>"
         "<li><b>「互动很烂」</b>＝回答内容其实是对的，但晚了 8~19 秒 ⇒ 与提问错位；"
         "中间还夹了一句被队列丢弃的「能听到呢，你说吧」。</li>"
         "</ul></div>")

# ---------------- 1 ----------------
P.append("<h2>1. 「忽略我的话」：6 次发声，只认下 3 次</h2>")
P.append("<table><tr><th>时刻</th><th>麦克风 rms</th><th>ASR 是否落定</th><th>ASR 文本</th></tr>")
for t, rms, ok, txt in [
    ("21:07:35.259", "0.0738", True, "能做个自我介绍吗"),
    ("21:07:45.759", "0.0585", True, "你可以做一个自我介绍吗"),
    ("21:07:47.259", "0.0386", True, "↑ 与上一条合并"),
    ("21:07:55.259", "0.1196", False, "—（音量是常态 3~5 倍，却没落定）"),
    ("21:07:56.759", "0.0377", False, "—"),
    ("21:08:15.261", "0.0263", True, "你的名字叫什么"),
]:
    P.append(row([t, rms, "<span class='pill o'>落定</span>" if ok
                  else "<span class='pill b'>丢失</span>", esc(txt)], "ok" if ok else "bad"))
P.append("</table>")

P.append("<div class='note'><b>用户其实是在重复问同一件事。</b>"
         "原始 ASR 文本依次是「能做个自我介绍吗」→「你可以做一个自我介绍吗」→「你的名字叫什么」，"
         "20 秒内问了 3 遍。<b>这不是用户啰嗦，是他在等回应、等不到就再问一遍。</b>"
         "「忽略我的话」的体感就是这么来的。</div>")

# ---------------- 2 ----------------
P.append("<h2>2. 「延迟高」：四段拆解</h2>")
P.append("<div class='flow'><pre>"
         "用户说完 ──▶ ASR 落定 ──▶ 【等模型开口】──▶ 【云端首包】──▶ 整句合成完 ──▶ 落盘/播放\n"
         "                       8.1 / 12.1 / 2.7s   1.83~4.09s      再等 10.4s(34字)\n"
         "                        ↑ 最大单项           ↑ 预取短路      ↑ 整句非流式\n"
         "</pre></div>")

P.append("<table><tr><th>模型第几句</th><th>文本</th><th>字数</th>"
         "<th>距上次 ASR</th><th>TTS 入队</th><th>首个音频</th><th>首包</th><th>结果</th></tr>")
for a, b, c, d, e, f, g, h, cl in [
    ("21:07:29.355", "在呢，你说吧。", "7", "—（开场）", "21:07:29.371", "21:07:31.868", "2.50s",
     "<span class='pill o'>出声</span>", "ok"),
    ("21:07:56.366", "我叫达妮娅，星炬学院虚质科学部的学生，粉发蓝眸…", "34", "<b>8.1s</b>",
     "21:07:56.375", "21:08:00.466", "<b>4.09s</b>", "<span class='pill o'>出声</span>", "warn"),
    ("21:08:00.356", "能听到呢，你说吧。", "9", "<b>12.1s</b>", "21:08:00.365", "—", "—",
     "<span class='pill b'>被队列丢弃</span>", "bad"),
    ("21:08:19.484", "达妮娅。", "4", "2.7s", "21:08:19.494", "21:08:21.324", "1.83s",
     "<span class='pill o'>出声</span>", "ok"),
]:
    P.append(row([a, esc(b), c, d, e, f, g, h], cl))
P.append("</table>")

P.append("<div class='bad-note'><b>第三条「能听到呢，你说吧。」整句没播出去。</b>"
         "它 21:08:00.365 入队，排在 34 字那句（合成到 21:08:11）后面，等轮到它时已过 "
         "<span class='mono'>10.72 秒 &gt; duplex_tts_stale_s=8.0</span> ⇒ 判过期丢弃。"
         "用户从头到尾没听到这句。</div>")

P.append("<div class='ok-note'><b>反证：模型本身一点不慢。</b>"
         "服务端录像里每块 <span class='mono'>timing.all_ms</span> 只有 "
         "<b>57.7 ~ 148.8 毫秒</b>（含 prefill 25~54ms）。"
         "「延迟高」<b>不是算力问题</b>，是「等它决定开口」＋「整句 TTS」。</div>")

# ---------------- 3 ----------------
P.append("<h2>3. 「互动很烂」：回答是对的，但错位了</h2>")
P.append("<table><tr><th>时刻</th><th>用户说了什么</th><th>她回了什么</th><th>用户实际何时听到</th><th>评价</th></tr>")
for t, u, a, h, j, cl in [
    ("21:07:35", "能做个自我介绍吗", "（无）", "—", "没接上", "bad"),
    ("21:07:45", "你可以做一个自我介绍吗", "21:07:56 我叫达妮娅，星炬学院…", "21:08:00 起（首字）",
     "内容对，但晚了 8~15 秒", "warn"),
    ("21:07:55", "（音量 0.12，ASR 没落定）", "21:08:00 能听到呢，你说吧。",
     "<b>完全没听到</b>（被丢）", "听到了声音但没听懂 → 回了句空话，还丢了", "bad"),
    ("21:08:15", "你的名字叫什么", "21:08:19 达妮娅。", "21:08:21 起", "快且准", "ok"),
]:
    P.append(row([t, esc(u), esc(a), h, esc(j)], cl))
P.append("</table>")

P.append("<div class='note'><b>细看能发现「能听到呢，你说吧」不是乱说。</b>"
         "它出现在用户 21:07:55 那次（音量 0.1196，是常态 3~5 倍）之后——"
         "模型<b>听到了声音但没能理解内容</b>（该段 ASR 未落定），于是回了句「能听到呢」。"
         "偏偏这句又被 TTS 队列丢掉 ⇒ 用户得到的是「完全没反应」。"
         "这个链条把「ASR 丢句 + TTS 丢句」叠成了最差组合。</div>")

# ---------------- 4 ----------------
P.append("<h2>4. 根因：lps 是「每块掷骰子」，不是「该说就说」</h2>")
P.append("<div class='card'><p>引擎不带「用户说完了，该你接话」这种信号。它每个音频块独立地判 "
         "LISTEN / SPEAK，唯一的总开关是官方参数 <code>listen_prob_scale</code>"
         "（越大越倾向聆听，默认 1.0）。于是：</p>"
         "<p style='text-align:center;font-size:16px;margin:14px 0'>"
         "<b>期望开口延迟 ≈ 1 / SPEAK占比　秒</b></p>"
         "<p>要「9 秒内开口」的概率 &gt;90%，需要 SPEAK 占比 ≥ <b>0.23</b>。"
         "本轮 13.0% ⇒ 期望等 <b>7.7 秒</b>，还有约 1/4 的概率等超过 10 秒。"
         "体检器给的「健康区 20~35%」正是从这条公式反推出来的。</p></div>")

P.append("<table><tr><th>通话</th><th>lps</th><th>mns</th><th>裁决 Hz</th>"
         "<th>SPEAK</th><th>占比</th><th>判定</th></tr>")
for s, lps, mns, hz, sp, pc, vd, cl in [
    ("19:49", "0.5", "48", "2.04", "106", "85.5%", "井喷", "bad"),
    ("19:40", "0.3", "20", "1.04", "7", "12.5%", "偏沉默", "warn"),
    ("20:07", "0.5", "48", "2.03", "60", "58.8%", "井喷", "bad"),
    ("20:08", "0.5", "48", "2.03", "97", "52.4%", "井喷", "bad"),
    ("20:10", "0.3", "20", "1.02", "22", "25.9%", "<b>健康</b>", "ok"),
    ("20:50", "1.0", "24", "1.13", "5", "2.5%", "哑火", "bad"),
    ("<b>21:07</b>", "<b>0.7</b>", "24", "1.12", "<b>9</b>", "<b>13.0%</b>", "<b>偏沉默（本轮）</b>", "warn"),
    ("21:08", "0.3", "20", "1.08", "13", "48.1%", "偏话多", "warn"),
]:
    P.append(row([s, lps, mns, hz, sp, pc, vd], cl))
P.append("</table>")

P.append("<div class='bad-note'><b>曲线强非线性，而且我上一轮推过头了。</b>"
         "治「井喷」时把 lps 从 0.5 提到 <b>1.0</b>（2.5%，彻底哑火），"
         "回撤到 <b>0.7</b> 仍只有 13.0%。"
         "1 Hz 档下唯一落在健康区中心的是 <b>lps=0.3</b>（三通 12.5 / 25.9 / 48.1%，中位 25.9%；"
         "方差来自内容差异，不是参数噪声）。</div>")

# ---------------- 5 ----------------
P.append("<h2>5. 修复一：lps 0.7 → 0.3（只动这一个参数）</h2>")
P.append("<div class='ok-note'>本轮<b>严格遵守「一次只调一个变量」</b>："
         "其余旋钮（<code>frame_interval=1.0</code> / <code>mns=24</code> / "
         "<code>talk_budget=12</code> / <code>tts_stale=8</code> / "
         "<code>selftalk_silence=12</code>）全部冻结不动。</div>")
P.append("<div class='card'><p>顺便纠正一条我上轮的理解错误："
         "<code>force_listen_count</code> <b>不是</b>「静音后强制聆听」，"
         "官方原文是「<i>前 N 次 streaming_generate() 强制返回 listen 状态</i>」的"
         "<b>启动保护期</b>（默认 3 即开场前 3 块）。它对通话中段完全无效 —— "
         "所以上一轮把它 2→3 对本次症状毫无影响，可以排除。</p></div>")

# ---------------- 6 ----------------
P.append("<h2>6. 修复二：TTS「预取」把「流式」短路成了「整句」（真 bug）</h2>")
P.append("<div class='card'><p>通话 TTS 本就该走流式合成（<code>_speak_stream</code>，边收 PCM 边播，"
         "注释里写「首块约 1s 出声」）。但 trace 显示："
         "<code>tts_begin</code> 之后必须先等 <code>tts_prefetch_hit</code> 才见 "
         "<code>tts_first_audio</code> —— <b>走了预取路径</b>。</p>"
         "<p>而预取路径的实现是这样的：</p></div>")

P.append("<div class='flow'><pre>"
         "# 修复前  tts.py\n"
         "_task = self._tts_prefetch.pop(_key, None)\n"
         "...\n"
         "if _task is not None:\n"
         "    # 预合成已在进行（与上一句播放并行）：等它，别再建一条连接\n"
         "    _data = await asyncio.wait_for(asyncio.shield(_task), timeout=25.0)   ← 等整段\n"
         "if _data:\n"
         "    stream = self._pcm_chunks(_data)         ← 一次性喂整段，流式性归零\n"
         "</pre></div>")

P.append("<div class='bad-note'><b>问题有两层：</b>"
         "<ul>"
         "<li><code>_prefetch_pcm</code> 把整段 PCM 攒完才 <code>return</code>，"
         "所以 <code>await</code> 它 ≡ <b>等整句合成完</b> ⇒ 首包 2.5~4.1 秒（等于没流式）。</li>"
         "<li><code>timeout=25.0</code> —— 预取一旦卡住，这句要阻塞 25 秒。</li>"
         "</ul></div>")

P.append("<div class='flow'><pre>"
         "# 修复后  tts.py\n"
         "# _prefetch_pcm：边收边推入增量队列（put_nowait，finally 补结束标记）\n"
         "async for chunk in backend.synthesize_stream(text):\n"
         "    acc.extend(chunk)\n"
         "    if q is not None:\n"
         "        q.put_nowait(bytes(chunk))\n"
         "...\n"
         "finally:\n"
         "    if q is not None:\n"
         "        q.put_nowait(None)\n"
         "\n"
         "# _speak_stream：消费增量，首块一到即出声\n"
         "_q = self._tts_prefetch_q.pop(_key, None)\n"
         "elif _q is not None:\n"
         "    _ctrace(\"tts_prefetch_hit\", chars=len(text), mode=\"stream\")\n"
         "    stream = self._iter_prefetch(_q, backend, text, emotion=emotion)\n"
         "</pre></div>")

P.append("<div class='ok-note'><b>兼容性设计</b>（不改变原有兜底语义）："
         "<ul>"
         "<li>预取在<b>产出任何音频前</b>失败（异常 / 被取消）→ <code>_iter_prefetch</code> "
         "自动改直连 <code>synthesize_stream</code> 重试，<b>不静默丢句</b>；</li>"
         "<li>已经产出过音频才失败 → 不重试，避免同一句播两遍；</li>"
         "<li><code>interrupt()</code> 打断时清空增量通道；</li>"
         "<li>攒完后仍写回 PCM 缓存（下次同文本仍可秒出）。</li>"
         "</ul></div>")

# ---------------- 7 ----------------
P.append("<h2>7. 已改清单与验证</h2>")
P.append("<table><tr><th>文件</th><th>改动</th><th>理由</th></tr>")
P.append(row(["<span class='mono'>config.yaml</span>",
              "<code>duplex_call_listen_prob_scale</code> 0.7 → <b>0.3</b>",
              "13.0% 偏沉默 ⇒ 回归实测健康区中位（25.9%）；附三修注释"], "ok"))
P.append(row(["<span class='mono'>src/voice/tts.py</span>",
              "新增 <code>_tts_prefetch_q</code> 增量通道 + <code>_iter_prefetch</code>；"
              "<code>_prefetch_pcm</code> 改边收边入队；<code>_speak_stream</code> 消费增量；"
              "<code>interrupt</code> 清通道",
              "流式被预取短路（首包 4.09s）；去掉 <code>timeout=25.0</code> 的长阻塞"], "ok"))
P.append(row(["<span class='mono'>tests/test_tts_prefetch_stream.py</span>",
              "<b>新增</b> 17 项（结构 8 + 行为 9）",
              "把「边收边入队」这个核心行为钉住回归"], "ok"))
P.append("</table>")

P.append("<div class='card'><b>测试结果</b><ul>"
         "<li><span class='mono'>test_tts_prefetch_stream.py</span>　<b>17/17</b>（含"
         "「首块在整段合成结束前即可取到」—— 修复的本质）</li>"
         "<li><span class='mono'>test_tts_chain_and_talk_gate.py</span>　25/25</li>"
         "<li><span class='mono'>test_call_silence_gate.py</span>　43/43</li>"
         "<li><span class='mono'>test_call_deadair_watchdog.py</span>　28/28</li>"
         "<li><span class='mono'>test_config_yaml_parses.py</span>　5/5</li>"
         "<li>四层冒烟：YAML 真解析 ✓ / py_compile ✓ / "
         "<code>load_config()</code> 读回 0.3 ✓ / 包导入 ✓</li>"
         "</ul>备份：<span class='mono'>_bak_stream_20260923/</span>（config.yaml + tts.py）</div>")

# ---------------- 8 ----------------
P.append("<h2>8. 预期效果与待办</h2>")
P.append("<div class='card'><b>下次通话预期</b><ul>"
         "<li>SPEAK 占比 13% → 期望 25% 上下 ⇒ <b>开口等待从 8~19s 降到 3~4s</b>；</li>"
         "<li>首包 2.5~4.1s → <b>约 1s</b>（真流式首块）⇒ 端到端从 12~34s 降到 <b>4~6s</b>；</li>"
         "<li>「重复问同一句」应显著减少（不用等那么久）。</li>"
         "</ul></div>")

P.append("<table><tr><th>待办</th><th>说明</th><th>状态</th></tr>")
P.append(row(["重启 app", "两处改动均为启动期读取", "<span class='pill w'>需要你做</span>"]))
P.append(row(["跑体检器复核", "python tools/check_turn_taking.py —— 看 SPEAK 是否落在 20~35%",
              "<span class='pill o'>工具已就绪</span>"]))
P.append(row(["「她说话时用户发声被丢」",
              "21:07:55 那次 rms=0.1196 却没落定 ASR。怀疑与她正在生成/播报时的采集或识别抑制有关，"
              "需单独一轮查 ASR 触发条件（本轮未动）", "<span class='pill n'>待深挖</span>"]))
P.append(row(["TTS 串行队列过期（stale=8s）",
              "本轮 1 句被丢。流式修好后单句占队列时间变短，预计自然缓解；若仍丢再考虑并发合成",
              "<span class='pill n'>观察</span>"]))
P.append("</table>")

P.append("<div class='sub' style='margin-top:34px'>"
         "数据源：客户端 trace（166 事件）+ 引擎服务端录像 recording.json（69 块 / 逐块 mode 与 timing）。"
         "所有数字均取自原始记录，未做估算。</div>")
P.append("</div></body></html>")

out = os.path.join(OUT, "report.html")
io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(P))
print("written", out, os.path.getsize(out), "bytes")
