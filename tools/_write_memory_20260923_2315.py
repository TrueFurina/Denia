# -*- coding: utf-8 -*-
"""写入 2026-09-23 23:15 的发现：提示词层次澄清 + 原生双工机制地面真值。

三个目标文件换行不同，各自按实测构造：
  MEMORY.md      CRLF
  2026-09-23.md  LF（append-only）
  REFERENCE.md   CRLF（append）
"""
import io, os

D = r'G:\ai对话\.workbuddy\memory'

MEM_NEW = """- ★**2cu 提示词三层结构（更正 §2ct 的说法）**：引擎实际收到的 system_prompt ＝ `_wrap` 产出
  `{prefix}\\n<behavior>{instruction}</behavior>`。**通话档走 `_custom` 分支直接 return（proactive.py:452），
  根本不加载内置 9 条对话守则**（那 9 条在 else 分支 469~505：默认沉默/没有画面/怎么回/别老关心/深夜/声音辨析）。
  ⇒ §2ct 说"没有一句说该怎么反应"的**准确含义 = 通话档没挂载通用守则**，非"全仓没有"。
  角色层 ＝ `prompts/call_profiles/danya_persona.txt`（config `duplex_call_persona_file`，**换角色只改这一个文件**）；
  行为层 ＝ `danya_behavior.txt`（config `duplex_call_prompt_file`，**全角色共用**）。prefix ＝ `_call_audio_vision_prefix()`。
- ★**2cv 原生 Comni 地面真值（88 字 prompt）＝"随便说什么都立刻回复"的机制答案**：`*_adx_mue*` 会话 = 用户手动跑的原生档，
  **系统提示词仅 88 字、零轮转指令**（"…你处于双工模式，可以一边听、一边说…你是由面壁智能开发的人工智能助手：面壁小钢炮。"）；
  其余字段全走默认：`generate_audio`/`deferred_finalize` = true、**不传 `listen_prob_scale`**、1000ms/块。
  实测 **SPEAK 占比 44.7~56.7%（比我们 26% 高一倍），最大连续 41 块（=41 秒）**。
  ⇒ **原生不是"更会等待"，是"更爱开口"。"犹豫／判断／等待"不是引擎能力，是我们在 prompt ＋ lps ＋ 8 道闸门里造的。**
  ⇒ 要真人感应往「**短 prompt ＋ 引擎自产音频 ＋ 减闸门**」走，而不是继续加等待逻辑。
  ⚠️两套 prompt 长度判据即可分流会话：<200 字 = 原生；含 `<behavior>` = 我们（工具 `tools/_cmp_native_vs_ours.py`）。
- ★**2cv-2 全双工无 VAD（铁证）**：`StreamingVAD` 只挂在 `HalfDuplex`（worker.py:1454/1501/1595/1724）。
  ⇒ **"用户说完没有"引擎不做检测**，全靠模型每个块内部裁决一次（原生 36~60ms / 我们 85~136ms），快到你感觉不到
  ⇒ 表现就是"立刻回复"。**VAD 目录存在 ≠ duplex 用它**，别再往这个方向找"判断阶段"。
- ★**2cv-3 `deferred_finalize` 真实语义（我曾误判为"跨块连贯"）**：＝"先回结果、再异步 finalize"，
  **纯延迟优化**（worker.py:2037 实测：模式 A 比 B 省 ~30ms LISTEN／~50ms SPEAK）；默认 true，我们 audio 档显式传 False
  （local_omni_duplex.py:1419）。引擎默认 `length_penalty=1.1`（cpp_backend.py:497），原生传 1.05，我们 1.0。
"""

LOG_NEW = """
## 23:15 回答「提示词层次」与「原生双工为何立刻回复」两问

用户问：①是不是已有基础系统提示词、人设可轮换？为何我上次说没有教模型如何对话 ②为何 Comni 里随便说什么都立刻回复，
而不是先犹豫/判断/等待。**只做分析回答，未改代码。**

### 澄清第一问：层次确有，但通话档绕开了通用守则
实测拼装链（本地 omni 双工）：`_wrap()`（local_omni_duplex.py:476）产出
`{prefix}\\n<behavior>{instruction}</behavior>`，其中
- `prefix` = `_call_audio_vision_prefix()`（proactive.py:329），通话档 = "他在跟你语音通话，你说的每句话他都能听到…"
- `instruction` = `_build_duplex_instructions()`（proactive.py:611）→ 通话档路由到 `_build_duplex_instructions_call()`（:439）
- **关键**：该函数 `_custom`（专属通话提示词文件）非空时**在第 452 行直接 return**，只输出
  `[persona.txt, behavior.txt, ctx]`；第 469~505 行那 9 条内置通用对话守则是 **else 分支**，通话档根本不吃。
⇒ 我上一轮"提示词里全是资料、没有一句说该怎么反应"的**准确含义 = 通话档没挂载通用守则**，不是仓库里没有通用守则。**需更正措辞。**
⇒ 角色层 = `prompts/call_profiles/danya_persona.txt`（`duplex_call_persona_file`）；行为层 = `danya_behavior.txt`（`duplex_call_prompt_file`）。
   **换角色只改 persona 文件**；行为层全角色共用。

### 回答第二问：引擎没有"犹豫期"这个东西
方法：扫全部 1309 个录像，用 `config.system_prompt` 长度分流，找到 **4 个 88 字会话**（`20260923_221555_adx_mue6r8x8` 等）
= 用户手动跑的原生 Comni 语音双工。工具 `tools/_cmp_native_vs_ours.py`，原始输出 `data/_native88.txt`。

原生档 config 只有 5 个字段：`system_prompt`(88字) / `ref_audio`(Temp 里的 java_ruo.wav) / `deferred_finalize`(true) /
`max_slice_nums`(0) / `length_penalty`(1.05)。**不传 `listen_prob_scale`、不传 `generate_audio`。** 间隔中位 1000ms。

实测裁决（原生三通 22:15 / 22:23 / 22:24）：SPEAK 56.5% / 56.7% / 44.7%，**SPEAK 游程最大 41 块（连续 41 秒）**。
对比我们 22:55：SPEAK 26.0%、游程最大 4。

机制结论（引擎源码）：
1. 双工是**块级自回归状态机**，每块裁决一次 LISTEN/SPEAK，输入 = 历史 + 上块自身状态 + 本块音频。
2. **全双工路径不做 VAD**：`StreamingVAD` 仅在 `HalfDuplex`（worker.py:1454/1501/1595/1724）。所以没有"检测用户说完"这一步。
3. 裁决本身极快（原生 wall 60ms / 我们 92ms），**快到你感觉不到 ⇒ 表现为"立刻回复"**。
4. 原生"立刻"的直接原因 = **它的 prompt 里零轮转约束**，且不压制 SPEAK。
5. `deferred_finalize` = "先回结果再异步 finalize"的纯延迟优化（worker.py:2037：A 比 B 省 ~30ms LISTEN／~50ms SPEAK），**不是跨块连贯**（我此前误判）。

⇒ 结论：**"先犹豫、判断、等待后再响应"从来不是引擎自带行为，是我们用 prompt + `listen_prob_scale` + 8 道客户端闸门
人为造出来的**，而且造得把该说的话也吞了（22:55 SPEAK 仅 26%，只有原生的一半）。

### 待用户拍板的方向
把通话档往「短 prompt ＋ `generate_audio=true` 让引擎自产音频 ＋ 削减客户端闸门」靠，而不是继续加等待逻辑。
"""

REF_NEW = """
================================================================================
§2cv 原生 Comni 语音双工的地面真值（2026-09-23 23:15）
================================================================================
【分流方法】扫 H:\\Comni\\_internal\\resources\\apps\\server\\data\\sessions\\*\\recording.json，
用 config.system_prompt 长度分类：<200 字 = 原生 Comni app 自己跑的；含 <behavior> = 我们接入的。

【原生档样例】20260923_221555_adx_mue6r8x8（207 块）/ 20260923_222317_adx_mue70q30（60 块）
               20260923_222447_adx_mue72nl2（152 块）/ 20260923_222108_adx_mue6xy26（0 块）
system_prompt 全文（88 字）：
  扮演一个具有以上声音特征的助手。请认真、高质量地回复用户的问题。请用高自然度的方式和用户聊天。你处于双工模式，
  可以一边听、一边说。你是由面壁智能开发的人工智能助手：面壁小钢炮。
config 全字段：ref_audio=C:\\Users\\Administrator\\AppData\\Local\\Temp\\duplex_llm_ref_java_ruo.wav,
  deferred_finalize=True, max_slice_nums=0, length_penalty=1.05。**无 listen_prob_scale / 无 generate_audio / 无 force_listen_count。**
块间隔中位 1000ms（1.0Hz）。
SPEAK：56.5% / 44.7% / 56.7%；SPEAK 游程：22:15 那通 [4, 41, 14, 10, 8, 5, 7, 15, 2, 11]（最大 41 块）。

【我们档样例】20260923_225525_adx_1790175322099（96 块）
config：system_prompt 484 字、ref_audio=ref_minicpm_signature.wav、deferred_finalize=False、listen_prob_scale=1.0、
  generate_audio=False、force_listen_count=3、max_new_speak_tokens_per_chunk=24、length_penalty=1.0。
间隔中位 500ms（2.0Hz）。SPEAK 26.0%，游程最大 4。

【引擎机制源码证据】
1. `worker.py:1815` `use_deferred_finalize: bool = True`；`worker.py:2038` `msg.get("deferred_finalize", True)`。
   `worker.py:1965~2037`：模式 A(True) = 先 ws.send_json 结果再异步 finalize（省 ~37ms wall）；
   模式 B(False) = 同步 finalize。注释实测：A 比 B 的 LISTEN wall 低 ~30ms、SPEAK 低 ~50ms。
   ⇒ **deferred_finalize 与"跨块连贯"无关**，纯延迟取舍。
2. `cpp_backend.py:286~291` `_CPP_SAMPLING_KEYS = (listen_prob_scale, force_listen_count,
   max_new_speak_tokens_per_chunk, tts_temperature)`；`:497` `length_penalty: float = 1.1`（引擎默认）。
3. `vad/` 目录只有 `StreamingVAD` 一个流式实现，调用点全在半双工：
   `worker.py:1454` `from vad import StreamingVAD, VadOptions`、`:1501` `vad = StreamingVAD(...)`、
   `:1595` `[HalfDuplex] prepared: ... vad_threshold=`、`:1724` `[HalfDuplex] turn N done, VAD reset`。
   ⇒ **全双工（Duplex）路径完全不使用 VAD。**

【提示词拼装链】
`local_omni_duplex.py:469~476` `_wrap()`：`prefix = self._instruction_prefix`（由 set_instructions 的第二个参数写入），
  返回 `f"{prefix}\\n<behavior>{instruction}</behavior>"`。
`proactive.py:611` `_build_duplex_instructions()` 总入口 → 通话中路由到 `:439 _build_duplex_instructions_call()`。
`:451 _custom = self._load_duplex_call_prompt_file()`；`:452 if _custom:` → `parts=[persona, custom, ctx]` 后 **:468 return**；
  `:469 起 if self._persona is None:` 那段 9 条内置守则（默认沉默/没有画面/怎么回/别老关心/深夜/声音辨析）**只在 _custom 为空时执行**。
`:385 _load_duplex_call_persona_file()` 读 `_duplex_call_persona_file`（config `duplex_call_persona_file`）。
`:379 _load_duplex_call_prompt_file()` 读 `_duplex_call_prompt_file`。
config.yaml：`duplex_call_persona_file=prompts/call_profiles/danya_persona.txt`（706B）、
`duplex_call_prompt_file=prompts/call_profiles/danya_behavior.txt`（583B）。

【产出物】tools/_cmp_native_vs_ours.py、data/_native88.txt、data/_cmp_native.txt、data/_sess_scan.txt
"""


def w_crlf(path, text, mode):
    """mode='insert_before' 用 anchor；此处直接按需调用。"""
    pass


# ---------- 1) MEMORY.md：在 §2cd 之前插入 ----------
p = os.path.join(D, 'MEMORY.md')
raw = open(p, 'rb').read()
anchor = '- ★**2cd「不理我」第二型'.encode('utf-8')
assert raw.count(anchor) == 1, 'MEMORY anchor count=%d' % raw.count(anchor)
blk = (MEM_NEW + chr(10)).replace(chr(10), chr(13) + chr(10)).encode('utf-8')
i = raw.find(anchor)
raw2 = raw[:i] + blk + raw[i:]
open(p, 'wb').write(raw2)
chk = open(p, 'rb').read()
assert chk.count(b'\r\n') == chk.count(b'\n'), 'MEMORY CRLF broken'
print('OK MEMORY.md  %d -> %d bytes' % (len(raw), len(chk)))

# ---------- 2) 当日日志：append（LF） ----------
lg = os.path.join(D, '2026-09-23.md')
before = open(lg, 'rb').read()
assert before.count(b'\r') == 0, '日志应为 LF，实测 CR=%d' % before.count(b'\r')
with open(lg, 'ab') as f:
    f.write(LOG_NEW.encode('utf-8'))
after = open(lg, 'rb').read()
assert after.count(b'\r') == 0, '日志 CRLF 被破坏'
print('OK 2026-09-23.md  %d -> %d bytes (+%d)' % (len(before), len(after), len(after) - len(before)))

# ---------- 3) REFERENCE.md：append（CRLF） ----------
rf = os.path.join(D, 'REFERENCE.md')
b0 = open(rf, 'rb').read()
blk2 = REF_NEW.replace(chr(10), chr(13) + chr(10)).encode('utf-8')
open(rf, 'ab').write(blk2)
b1 = open(rf, 'rb').read()
assert b1.count(b'\r\n') == b1.count(b'\n'), 'REFERENCE CRLF broken'
print('OK REFERENCE.md  %d -> %d bytes (+%d)' % (len(b0), len(b1), len(b1) - len(b0)))
