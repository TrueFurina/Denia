# -*- coding: utf-8 -*-
"""把 2026-09-23 23:0x 这轮（人格 + 成句即送 + 死配置）写进项目记忆。

MEMORY.md  = CRLF；当日日志 = LF。都按实测换行构造 bytes。
"""
import io
import os
import time

ROOT = r'G:\ai对话\.workbuddy\memory'
os.chdir(ROOT)

fails = []


def patch(path, old, new, tag, crlf=None):
    raw = open(path, 'rb').read()
    _crlf = (raw.count(b'\r\n') > 0 and raw.count(b'\r\n') == raw.count(b'\n'))
    if crlf is not None:
        _crlf = crlf
    ob = old.replace('\n', '\r\n' if _crlf else '\n').encode('utf-8')
    nb = new.replace('\n', '\r\n' if _crlf else '\n').encode('utf-8')
    n = raw.count(ob)
    if n != 1:
        fails.append('%s: 命中 %d 次' % (tag, n))
        return
    open(path, 'wb').write(raw.replace(ob, nb, 1))
    print('  OK %s' % tag)


M = 'MEMORY.md'

# ---------- ① 索引行加热点 ----------
IDX_OLD = "> ⭐最热：**§2cr 提示词改动只动通话档；22:30＝答对但未播出**"
IDX_NEW = ("> ⭐最热：**§2cs 人格＝被提示词删掉（三层叠加：前缀/行为/声明式人设）；成句即送省 0.95s/turn**"
           "／**§2ct 死配置第二例＝`asr_trailing_silence` 恒用硬编码**／**§2cr 提示词改动只动通话档；22:30＝答对但未播出**")
patch(M, IDX_OLD, IDX_NEW, '索引行加 §2cs/§2ct')

# ---------- ② 正文插入 ----------
ANCHOR = """- ★**2cd「不理人」第二型＝模型哑火（LISTEN 偏置），不是闸门吞句**"""
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
- ★**2cd「不理人」第二型＝模型哑火（LISTEN 偏置），不是闸门吞句**"""

patch(M, ANCHOR, NEW, '插入 §2cs / §2ct')

# ---------- ③ 当日日志 ----------
day = time.strftime('%Y-%m-%d')
LOG = os.path.join(ROOT, day + '.md')
entry = """
## 23:00 通话「延迟 + 人格」两轴修复（22:55 通话取证）

**取证**：服务端录像 `20260923_225525_adx_*`（96 块 / 2.00Hz / SPEAK 26.0%）+ 客户端 trace
`data/call_trace_omni_20260923_225519.jsonl`（7 次上屏→6 次真出声，1 次被串行队列压后 4.0s）。

**延迟三段账（用户说完→出声）**：引擎起手 1.13/6.65/2.15/2.16s ｜等整句 1.94~1.97s ｜TTS 首包 0.66~0.81s
⇒ 端到端 3.76 / **9.40** / 4.77 / 4.78s，均值 5.68s。
- 最坏那回合＝他问「能做个简单的自我介绍吗」，她 6.65s 才起手，用户以为没听见**又问了一遍**，
  于是她连答两遍同样的自我介绍（= 用户说的"一次把慢理的多次一起理了"）。
- 「等整句」是纯结构性白等：客户端只在 `is_listen` 才送 TTS。

**已改**：
1. `src/llm/local_omni_duplex.py` 「成句即送」+ `_turn_sent_len` 偏移管理（含 ≥200 超长兜底与残渣
   丢弃两处边界修正，否则会念两遍/丢尾段）；新回合起点复位。回归 `tests/test_call_early_sentence_emit.py`
   回放真实 trace：均值省 **0.81s**/回合，7 条静态守卫。
2. 人格：`prompts/call_profiles/danya_behavior.txt`（去掉"一句就够、不用铺开"→"答完可以带一句你自己的
   反应/反问"+"同一件事别从头讲第二遍"）、`danya_persona.txt`（拆掉"被问名字就背学院"的检索靶子）、
   `proactive.py::_call_audio_vision_prefix`（删"短一点/不用找话说"，保留不抢话约束）。
3. 死配置接通：`duplex_call_asr_trailing_silence`（config 0.45 → 实际恒 0.6）。

**验证**：`test_call_early_sentence_emit` / `test_call_prompt_no_echo` / `test_call_prompt_and_barge` /
`test_call_silence_gate` / `test_call_deadair_watchdog` / `test_call_gate_user_echo` /
`test_call_gate_self_echo` / `test_config_yaml_parses` 全绿；未定义全局名差分审计差值 = 0。

**产物**：`data/diag_call_2255/report.html`、`data/diag_call_2255.json`；
备份 `_bak_soul_20260923_2300/`；补丁 `tools/_patch_20260923_{soul_latency,asr_cfg,earlyedge}.py`。

**待办**：引擎起手（lps 旋钮，风险高未动）／音色仍回落 `ref_minicpm_signature.wav`／
通话前 ~15s 引擎才 prepared 期间用户的话引擎没听到／TTS 整句串行。**所有改动需重启 app。**
"""
if os.path.exists(LOG):
    raw = open(LOG, 'rb').read()
    assert raw.count(b'\r') == 0, '当日日志出现 CR，换行约定变了'
    open(LOG, 'ab').write(entry.encode('utf-8'))
    print('  OK 追加当日日志 %s' % day)
else:
    open(LOG, 'wb').write(entry.encode('utf-8'))
    print('  OK 新建当日日志 %s' % day)

print()
if fails:
    print('[FAIL]')
    for f in fails:
        print('   x', f)
    raise SystemExit(1)
print('[OK] 记忆已更新')
