# -*- coding: utf-8 -*-
"""2026-09-24 00:35 追加：混合架构（双工做感知层 + 外接 LLM/TTS）必现 bug 清单（代码实证）"""
import io, os

LOG = r'G:\ai对话\.workbuddy\memory\2026-09-24.md'
if not os.path.exists(LOG):
    io.open(LOG, 'w', encoding='utf-8').write('# 2026-09-24 工作日志\r\n')

raw = open(LOG, 'rb').read()
nl = '\r\n' if raw.count(b'\r\n') == raw.count(b'\n') and raw.count(b'\n') else '\n'

ADD = [
    '',
    '### 00:35 混合架构（双工留作裁决/感知层 + 外接 LLM 写内容 + TTS）必现 bug（代码实证）',
    '- **结论：会有 bug，3 个架构级必现、3 个高概率；但都能修，且现有代码已有 ~80% 零件。**'
    '真正的问题不是 bug 而是**收益存疑**（见 §2cx-4）。',
    '',
    '#### Bug1（最严重）回声自激 —— 外放必踩',
    '- 实证 `local_omni_duplex.py:1244`：`_trace_gate_on = bool(self_speak_active() and '
    '(self._audio_source != "mic" or self._mic_gate))`。',
    '- `config: duplex_mic_gate: false` ＋ 构造默认 `mic_gate: bool = False` ⇒ '
    '**通话档（mic 源）她出声时麦克风零保护**（`process_loopback.py` 进程级环回排除在 mic 档无效，'
    '源码注释本机不可用）。',
    '- 现状未爆的原因：引擎自己在 SPEAK、知道"我在说"能部分抵消；且戴耳机时声音不进麦。',
    '- **混合后必爆**：引擎不再生成内容 ⇒ 它不知道"我在说话" ⇒ 只听麦克风 ⇒ '
    '把自己 TTS 听成用户在说 ⇒ 永远 LISTEN（死锁）或反复触发（叠播）。',
    '- ⚠️ 正确解法是 **AEC**（拿送播 PCM 做参考信号自适应滤波），**不是 `_mic_gate`**'
    '——后者是伪半双工，会把她出声期间的用户插话整段丢掉（源码注释已写明该取舍）。',
    '- ⚠️ AEC 是**纯级联也需要**的，不是混合独有（外放场景通用）。',
    '',
    '#### Bug2 打断仲裁失效（两个方向）',
    '- `_fire_barge_in` 三动作：① 清 `_turn_text` ② `_barge_hold_left`→`force_listen` ③ 回调上层停 TTS。',
    '- ①② **只作用于双工引擎**，管不到外部 LLM 的生成、也管不到外部 TTS 队列 ⇒ '
    '用户插话后外部 LLM 继续生成并播出 ⇒ **打断失效**。',
    '- 反向：`_barge_check(rms, now)` 基于**麦克风 RMS**（:1229）⇒ 她自己的 TTS 让 RMS 飙升 ⇒ '
    '满足连续 2 帧 ⇒ **她自己打断自己**（外放时）。',
    '- 修法：③ 已是回调上层，接线到外部 LLM 的 cancel 即可；零件现成。',
    '',
    '#### Bug3 两套状态机无仲裁',
    '- 引擎 LISTEN/SPEAK 状态 vs 外部 LLM 回合状态，互相独立。',
    '- 引擎判"该开口"时外部 TTS 还在播 ⇒ 叠播；外部 LLM 生成完但引擎仍 LISTEN ⇒ 无人起播 ⇒ 卡住。',
    '- 必须新增**单一仲裁器**（一个状态机，双工只提供"用户是否还在说"的观测值）。',
    '',
    '#### Bug4（最反直觉）延迟不降反升',
    '- 双工裁决起手 1.13~6.65s、SPEAK 占比 26% ⇒ 平均开口延迟 ≈ 1/0.26 ≈ 3.8s。',
    '- 拿它当触发器 ＝ **把哑火带进级联**；纯级联 VAD 端点 ~200-300ms。',
    '- ⇒ "加一层双工当触发器"很可能**比不加更慢**。这是决定 P5 该不该做的唯一判据，须实测。',
    '',
    '#### Bug5 8k ctx 溢出 / Bug6 记忆双写不同步',
    '- 引擎只做裁决仍需持续喂音频，ctx 8192 是共享 KV cache ⇒ 长通话必溢出。',
    '- 引擎不知道她自己回了什么（TTS 音频不回灌引擎）⇒ 它的对话理解残缺 ⇒ '
    '裁决质量随通话时长下降。',
    '',
    '#### 决策含义',
    '- **P5（混合）必须排在 P1（纯级联）之后**：纯级联既是它的对照组，也是它的降级路径。',
    '- 混合不是"加一层"，是"**加一个仲裁器**"：单一状态机 + AEC + 打断三路联动。',
    '- 现有可用零件：`INPROC_SPEAK_GATE`（tts.py 播放时置位/结束清除，361/391/975/1071/1159 等）、'
    '`_fire_barge_in` 的回调上层、`self_speak_active()`。',
    '- 判断走不走混合只看一个数据：P1 上线后级联实测延迟 vs 双工触发延迟。',
]

with open(LOG, 'ab') as f:
    f.write((nl + nl.join(ADD) + nl).encode('utf-8'))

b = open(LOG, 'rb').read()
print('日志追加完成 size=%d CR=%d LF=%d 纯CRLF=%s' % (
    len(b), b.count(b'\r'), b.count(b'\n'), b.count(b'\r\n') == b.count(b'\n')))
t = io.open(LOG, encoding='utf-8').read()
print('含新章:', 'Bug4（最反直觉）' in t)
