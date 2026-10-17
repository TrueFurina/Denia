"""给 trace-comni-duplex-turn-taking 技能补一节：打断吃字。"""
import io, os

F = r'C:\Users\Administrator\.workbuddy\skills\trace-comni-duplex-turn-taking\SKILL.md'
raw = open(F, 'rb').read()
NL = '\r\n' if raw.count(b'\r\n') == raw.count(b'\n') else '\n'
t = io.open(F, encoding='utf-8', errors='replace').read().replace('\r\n', '\n')

ANCHOR = '## 8.'
assert t.count(ANCHOR) == 1, '锚点 8. 命中 %d 次' % t.count(ANCHOR)

SEC = '''## 7d. 打断必查三件事（含"吃字"这一型）

用户说"语音变奇怪 / 句子说一半 / 语速慢"时，**先算语速再查打断**，不要直接怀疑 TTS。

### ① 语速到底变没变（用来排除 TTS 侧）
落盘音频在 `src/ui/web/assets/chat_audio/*.wav`（`tts.py:_save_pcm_wav` 写的）。

    语速(字/秒) = 埋点里所有 tts_begin 的 chars 之和 ÷ 通话时间窗内 wav 时长之和

实测案例（2026-09-24）：用户抱怨"语速慢"，实测 **4.03 字/秒**，P0 前基线只有
**2.89** ⇒ 反而变快。**TTS 侧没变，问题在文本**——这一算就省掉一半排查。

### ② 客户端文本 vs 引擎原文，差几个字（吃字检测）
引擎原文 = 服务端录像 `chunks[]` 按**连续 SPEAK 段**聚合（逐块 text 是增量碎片，
必须聚合，否则得到一堆"假短句"）。客户端文本 = 埋点 `speak_out`。

    差 = 客户端总字数 − 引擎总字数     （实测案例：−9 字 = 吃字）

再逐条找：客户端某条在引擎原文里找不到 / 只能匹配后缀 ⇒ 丢了开头。

### ③ 打断是不是"清空缓冲"造成的（最常见）
`_fire_barge_in` 会 `self._turn_text = ""`。**引擎不知道客户端已丢弃，仍继续吐
这一句的剩余字**；客户端从空缓冲重新攒够就当完整 utterance 播出 ⇒ 播出半句话。

铁证长这样（客户端 trace，`model` 事件的 `buf` = 引擎侧累积长度）：

    93.67 model text="有"       buf=0
    94.07 barge                          ← 用户插话
    94.43 model text="时候想"   buf=0     ← 缓冲被清，从 0 重新攒
    96.19 播出  "时候想哎，忘了吧。"       ← 开口字没了

**判据：`barge` 事件之后紧跟的 `model` 事件 `buf` 归零，且很快又攒够播出 ⇒ 吃字。**

正确修法不是"别打断"，而是**标记本回合作废**：打断时置 `_turn_aborted=True`，
文本分支开头丢弃引擎残字（`return`，不累积不送出），**并在 `is_listen` 时复位**
——不复位会永久吞掉之后所有回答，比吃字严重得多。见
`src/llm/local_omni_duplex.py` 的 `_turn_aborted` 四处。

### ⚠️ 别把"用户真插话"误判成"自激"
判断 barge 是谁触发的，看**紧邻 barge 之前 2~3 秒的 `mic` 事件 pk 序列**：

| 形态 | 判定 |
|---|---|
| barge 前 pk 长期 0.0001（死静），barge 时突跳到 0.3~0.6，且有 `user_voice` | **用户真插话**（正常） |
| 她 TTS 播放期间 pk 持续偏高、无 `user_voice` | **自激**（她的声音进麦，需 AEC / mic_gate） |

我曾在实测里先入为主判成自激，看 pk 序列才发现 6 次全是你真插话。
**先取证再下结论。**

### 附带：出声率统计的坑
配对式统计（"emit 后 N 秒内有音频就算出声"）**窗口一宽就把缺失项洗掉**：
30s 窗口下，被打断没播的句子会被后面句子的音频认领 ⇒ 出声率恒定虚高 100%
（实测真实值 80%）。改用**三段计数**：`speak_out(送TTS) → tts_begin(进合成)
→ tts_first_audio(真出声)`，并单独报 `tts_interrupt` 次数区分"被打断"与"被闸门吞"。

'''

t = t.replace(ANCHOR, SEC + ANCHOR, 1)
data = t.encode('utf-8')
if NL == '\r\n':
    data = data.replace(b'\n', b'\r\n')
open(F, 'wb').write(data)
print('技能已更新 %d 字节' % len(data))
print('  含 7d 节:', '## 7d.' in t)
