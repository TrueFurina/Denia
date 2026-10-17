"""写入 01:00 轮次的结论。"""
import io, os

D = r'G:\ai对话\.workbuddy\memory'
LOG = os.path.join(D, '2026-09-24.md')

add = """
### 01:00 P0 验收 + "语音变奇怪/语速慢"根因（新章节 §2cx）

**P0 验收四项全达标**（会话 20260924_005838_adx，工具 tools/verify_p0_call.py）：
- 改动生效：录像 system_prompt 795 字，含常驻块 YES
- 人格：非身份证句占比 **96.9%**（基线 37.5%，达标 ≥55%）— 32 句只 1 句背设定
- SPEAK 占比 **43.6%**（基线 26.0%，健康区 30~55%）
- 出声率 **80%**（30 送 TTS / 24 真出声），延迟中位 **3.33s**（基线 4.78s）
⇒ 结论：**人格确实是被提示词删掉的，P0 成立。** 常驻块挂末尾这招有效。
⚠️ 但主观"效果一般" ⇒ 指标达标 ≠ 听感好，真凶见下。

**★§2cx「语音奇怪/语速慢」真因＝打断吃字，不是 TTS 变慢**
- 反证：实测语速 **4.03 字/秒**（P0 前基线 2.89）⇒ 反而变快，TTS 侧无变化。
- 铁证（客户端 trace call_trace_omni_20260924_005833.jsonl）：
  `93.67 引擎吐"有" → 94.07 barge → 清缓冲 → 94.43 引擎吐"时候想"(buf=0)
   → 96.19 播出"时候想哎，忘了吧。"`
  同理 `106.18"听起" → 106.57 barge → 106.96"来有点闷啊。" → 播出丢"听起"`。
- 机理：`_fire_barge_in` **只清 `_turn_text`，没标记本回合作废**。引擎不知道客户端
  已丢弃，继续吐该句剩余字；客户端从空缓冲重新攒够就当完整 utterance 播出
  ⇒ **每打断一次吃一次句子开头**。本通 6 次打断 = 6 次吃字。
- ⚠️**不是自激**（我一度误判）：barge 前 2.5s 麦克风 pk=0.0001（死静），
  barge 时 pk 跳到 0.53 且有 `user_voice` 事件 ⇒ **用户真实插话**。
  判据：看 barge 前 mic 的 pk 序列，一直很低再突跳 = 真人；持续高 = 自激。

**已修（src/llm/local_omni_duplex.py，CRLF，备份 ._bak_aborted_20260924_011158）**：
新增 `_turn_aborted` 四处：①__init__ 初始化 ②`_fire_barge_in` 清空缓冲时置 True
③文本分支开头 `if getattr(self,"_turn_aborted",False): return`（丢弃引擎残字、
不累积不送出）④`is_listen` 时复位（不复位会永久吞掉后续回答，比吃字严重）。
回归 `tests/test_call_barge_abort.py` **全通过**（回放真实 model/barge 序列：
修复前复现两条残句、修复后消除、且未误伤其它句）。

**★通用方法（可复用）**：判断"客户端输出是不是丢了引擎的字" ⇒
拿**服务端录像聚合的引擎原文**与**客户端 speak_out** 逐条比对，看总字数差与
是否能互相找到。本通差 -9 字。工具 `tools/_diag_textloss_20260924.py`。
语速可用落盘音频（`src/ui/web/assets/chat_audio/*.wav` 总时长）÷ 埋点 `tts_begin`
的 chars 总量算，工具 `tools/_diag_speed_20260924.py`。

**★验收工具口径修正**：`verify_p0_call.py` 原用「emit 后 30s 窗口内有音频就算
出声」⇒ 被打断没播的句子会被后面句子的音频认领，**恒定虚高 100%**。
改为三段计数 `送TTS → 进合成 → 真出声` + 单独报打断次数。
**教训：配对式统计必须收紧时间窗或按计数配对，宽窗口会把缺失项洗掉。**
"""
raw = open(LOG, 'rb').read()
nl = b'\r\n' if raw.count(b'\r\n') == raw.count(b'\n') else b'\n'
with open(LOG, 'ab') as f:
    f.write(add.encode('utf-8').replace(b'\n', nl))
print('日志追加完成', os.path.getsize(LOG), 'bytes  换行', nl)
