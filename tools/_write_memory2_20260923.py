# -*- coding: utf-8 -*-
"""补插 §2cs / §2ct 正文（上一脚本锚点写错一个字：文件是「不理我」不是「不理人」）。"""
import io
import os

M = r'G:\ai对话\.workbuddy\memory\MEMORY.md'
raw = open(M, 'rb').read()
assert raw.count(b'\r\n') == raw.count(b'\n'), 'MEMORY.md 换行约定变了'

ANCHOR = "- ★**2cd「不理我」第二型＝模型哑火（LISTEN 偏置），不是闸门吞句**"

NEW = """- ★**2cs「不像真人」必须拆两轴：延迟 / 人格，根因不同（22:55 通话实测）**：
  **延迟三段账**（用户说完→出声，`tools/_diag_call_2255.py`）＝**引擎起手**（用户停→模型首字）
  1.13~6.65s 占 53% ＋**等整句**（首字→送 TTS）1.95s ＋**TTS 首包** 0.71s；
  ⇒ **≤2s 的天花板在「等引擎自己决定开口」，不在合成栈**；引擎自身裁决仅 85~136ms（§2ck-4）。
  ⭐**「等整句」是纯结构性白等**：客户端原先**只在 `is_listen`（引擎宣布回合结束）才把整句送 TTS**，
  2Hz 档下模型吐完最后一片文字还要再跑 ~2 个 chunk 才置真 ⇒ 白扣 0.9~1.0s/回合。
  **已修＝「成句即送」**（`local_omni_duplex.py::_handle_msg`）：pending 段一到硬句末
  （`。！？!?…`，**不含 ～**）且 ≥8 字就立刻 `_emit_turn_text`，与后续生成重叠。
  回归 `tests/test_call_early_sentence_emit.py` 用真实 trace 回放：**7 回合 6 命中，均值省 0.81s**。
  ⚠️**改文本累积必须同步管「已送偏移 `_turn_sent_len`」，三处必查**：`is_listen` 分支只取
  `_turn_text[_sentn:]`（否则同一句念两遍）、`≥200 字超长兜底` 只在 pending 上找切点（否则念两遍＋丢尾段）、
  **新回合起点必须复位为 0**（否则下一轮首片较长时被当旧前缀吞掉＝丢字）。
  **通用：凡「提前送出」，先问「已送出的那段怎么记账」。**
- ★**2ct 人格＝被提示词删掉，不是模型没有人格（22:55 通 47 秒 7 句 6 句是自我介绍）**：
  三层叠加：①**前缀**（最高优先级）「接的时候直接点、**短一点**」「轮不到你就安静待着，**不用找话说**」
  ②**行为段**「他问你什么就答什么，**一句就够，不用铺开**」⇒ 把对话降级成一问一答
  ③**角色段纯声明式设定**（"你是达妮娅。星炬学院虚质科学部的学生…"）⇒ Q4 弱模型对声明式文本做
  **关键词检索**：问"你是谁"就吐设定原文 ⇒ 这就是"自我介绍复读"的出处（同源 §2ck-2）。
  ⇒ **提示词里写满了她是怎样的人，却全是"资料"、没有一句说"该怎么反应"。**
  ⚠️这类复读**闸门拦不住也不该拦**：每句措辞都不同、都通顺，只是内容同一张身份证
  （call_gate 的 self_echo/user_echo 一次都没命中）。判据＝**去重后 SPEAK 句数 vs 含反问/情绪词的句数**。
  ⚠️「一次性把慢理的多次一起理了」＝**引擎起手 6.65s 后用户重问**，不是她在批量回。
- ★**2ct-2 死配置第二例**：`duplex_call_asr_trailing_silence: 0.45` **全仓无人读取**
  （`_user_asr_trailing_silence` 无任何赋值点 ⇒ 恒用硬编码 0.6；实测 asr 事件 `sl=1.029s`）。
  已接通（config→属性→client setter，且**必须早于 `set_audio_source`**：采集线程一起来就建 Sherpa 识别器）。
  **指纹＝埋点实测值与 config 对不上**；已两踩（§2ck-3 force_listen_count）。
  ⚠️该阈值只影响转写/`_turn_user_spoke`，**不在 TTS 关键路径**（`_mark_user_speech` 由 VAD 上升沿触发）。
"""

ab = ANCHOR.encode('utf-8')
assert raw.count(ab) == 1, '锚点命中 %d 次' % raw.count(ab)
out = raw.replace(ab, NEW.replace('\n', '\r\n').encode('utf-8') + ab, 1)
assert out.count(b'\r\n') == out.count(b'\n'), '换行被破坏'
open(M, 'wb').write(out)
print('OK 已插入 §2cs / §2ct，MEMORY.md 现 %d 字节' % len(out))
