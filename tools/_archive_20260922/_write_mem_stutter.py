# -*- coding: utf-8 -*-
"""追加 §2af：结巴根因=测试台未与 app 同构；并更正旧判据。"""
import os
import time

MEM = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
LOG = r"G:\ai对话\.workbuddy\memory\2026-09-21.md"

ANCHOR = "- **2ae 本轮新增可复用工具（G:\\ai对话\\11\\tools\\）**："

NEW = """- **2af 结巴/卡顿（★结论：不是能力上限，是测试台未与 app 同构）**：
  🔴 **根因**：旧测试台的 `audio_chunk` **不发 `force_listen`**，且尾部一次喂 12s 纯静音、还是"发一收一"乒乓推流
  ⇒ 模型被允许**对静音持续开口**，自嗨成一串短感叹句（日志实见 `哇哦！真好玩呢。哈哈哈`），
  每片收尾都重启声码器 ⇒ **`audio_bos(chunk_idx=0)` 达 40 次/回合** ⇒ 语音被切成中位 0.13~0.15s 的碎块。
  ✅ **修法（`tools/gen_sample_app.py`，1:1 复刻 `local_omni_duplex.py`）**：静音帧 `force_listen=True`
  （判据同 app：`EMA(rms,α=0.35) > 0.018`）+ **定拍推流**（通话档 0.5s/帧，不等待回复）+ 帧长 0.5s。
  修后同一 cache/同一参考音：**audio_bos(chunk0) 40→1、浊音中位 0.152→0.688s、浊音占比 49%→70%、最长静音 1.54→0.78s**，
  文本从自嗨变成连贯回答，**`cos↔参考` 0.6958→0.7103（全线最高）**。
  🔑 **判据**：数引擎日志里 `在 prefill 前添加 audio_bos (chunk_idx=0)` 的次数 = 独立 TTS 轮数；**>10 即有碎句风险**。
  📏 **真机标尺**（4 次真实通话 ai_audio，自适应阈值 RMS−25dB）：浊音占比 **63~74%**、
  ≥0.15s 静音 **0.67~1.12 个/秒**、最长静音 **0.475~1.51s**、浊音中位 **0.135~0.48s**；天然参考音 78% / 0.43/s / 0.74s。
  ⚠️ 真机 T2W **块边界是连续的**（跳变比中位 0.045），**块拼接不是卡顿源**；块时长中位恒定 1.000s。
  ⚠️ 音频到达速率实测 **1.00 音频秒/墙钟秒** ⇒ 无播放欠载。
  ⚠️ **更正上一条**：§2v 说"停不下来/复读是裸测试台通用行为、时长不能当判据"——**部分错**：
  那个"停不下来"正是缺 `force_listen` 造成的，不是模型固有；同构化后模型会正常收尾。
- **2ag 输出侧停顿规整（`tools/smooth_speech.py`）**：只**删除静音样本 + 拼接处 4ms 淡化**，
  一个浊音采样都不动 ⇒ **中位 F0 逐点不变（Δ=0.0）、cos 仅噪声级波动**（实测 ±0.006）。
  双阈值滞回（进 −35dB / 出 −28dB，相对自身 P90 电平），keep=(0.10,0.16,0.28)s。
  效果（A_FIXED）：23.44→16.27s、浊音占比 49%→69%、最长静音 1.54→0.355s。
  这是"让耳朵能判音色"的**下游处理**，不等于修好引擎。"""


def main():
    b = open(MEM, "rb").read()
    t = b.decode("utf-8")
    assert "\r\r" not in t, "MEMORY.md 含 \\r\\r，先修"
    assert t.count(ANCHOR) == 1, "锚点命中 %d 次" % t.count(ANCHOR)
    nl = "\r\n" if "\r\n" in t else "\n"
    block = NEW.replace("\n", nl)
    t2 = t.replace(ANCHOR, block + nl + ANCHOR, 1)
    assert "\r\r" not in t2
    open(MEM, "wb").write(t2.encode("utf-8"))
    print("MEMORY.md %d -> %d bytes" % (len(b), len(t2.encode("utf-8"))))

    ts = time.strftime("%Y-%m-%d %H:%M")
    entry = ("""

## __TS__  结巴根因：测试台未与 app 同构（已修）

### 现象
用户反馈：所有克隆样本都有明显结巴/卡顿，无法判断音色。

### 取证（两步否证，才找到真因）
1. **先排除采集**：日志还原每条 `audio_only` 的样本数，与成品 wav **误差 0 样本**；
   `[WAV nowait]` 轮次数=1、sent 单调不减 ⇒ **采集没丢没重**。
2. **再排除拼接**：按日志还原出 1s 块的拼接点，逐点测跳变/电平 ——
   多数拼接点两侧都是语音（-13~-28dB），**块边界连续**，卡顿不在这里。
3. **真机对照**：拿 4 次真实通话 `ai_audio` 用同一把尺子量 ——
   真机浊音占比 **63~74%**、最长静音 ≤1.51s；而我的测试台只有 **49~64%**、最长 1.54s。
4. **抓真因**：引擎日志 `LLM->TTS: text='哇哦！' n_tokens=1` 这类 1~4 token 碎片 + 
   `在 prefill 前添加 audio_bos (chunk_idx=0)` **40 次** ⇒ 模型在**对静音自言自语**。

### 根因
旧测试台 `tools/gen_timbre_sample.py` 的 `audio_chunk` **不带 `force_listen`**，
且尾部连喂 12s 纯静音 + "发一收一"乒乓 ⇒ 模型每片都开新 utterance 并独立收尾。
app 的做法是：静音帧 `force_listen=True`（`EMA(rms,α=0.35)>0.018`）+ 每 0.5s 定拍推帧。

### 修复与实测
新测试台 `tools/gen_sample_app.py`（1:1 复刻 app）：

| 指标 | 旧测试台 | 应用同构版 |
|---|---|---|
| audio_bos(chunk_idx=0) | **40** | **1** |
| text_eos 收尾 | 35 | 8 |
| 浊音中位时长 | 0.152s | **0.688s** |
| 浊音占比 | 49.0% | **70.0%**（真机 63~74%） |
| 最长静音 | 1.540s | **0.780s** |
| cos↔参考音 | 0.6958 | **0.7103**（全线最高） |
| F0 | 237.6 | 250.0（Δ vs 参考 240.0） |

文本也从自嗨（"哇哦！真好玩呢。哈哈哈"）变成连贯回答（"你好呀，我是达尼娅！平时喜欢做些什么呢？…"）。
到达速率实测 1.00 音频秒/墙钟秒（无播放欠载）。

### 输出侧补充：停顿规整
`tools/smooth_speech.py`：只删静音样本（双阈值滞回）+ 拼接 4ms 淡化，
**浊音一个采样不动** ⇒ 中位 F0 Δ=0.0。keep=(0.10,0.16,0.28)。
APP_A：19.24→15.52s、浊音占比 70.0%→86.6%、最长静音 0.78→0.165s。

### 交付
`data/native_audio_test/final_sample/聆听_v3/`（8 段，全部 −20dBFS 等电平，顺序文件 110.7s）
01 目标参考音 → 02 应用同构_自然节奏 → 03 停顿规整 → 04/05 高频补偿(EQ1 8-12k −21.1→−8.4dB，f99 10094≈真源)
→ 06 旧测试台结巴原样 → 07 仅停顿规整 → 08 原生底色对照

### 新的可复用工具
`gen_sample_app.py`（应用保真合成台）｜`smooth_speech.py`（停顿规整，保音色）｜
`_diag_stutter.py`（轮次/去重/文本复读）｜`_diag_stutter_sig.py`（拼接点体检）｜
`_diag_burst.py`（碎块指标）｜`_diag_frag.py`（文本分片与 audio_bos 计数）｜
`_diag_real_vs_mine.py`（真机录音对照）｜`_find_real_ai_audio.py`（在 1334 个会话里找 ai_audio）｜
`_measure_final.py`｜`make_listening_set_v3.py`
""").replace("__TS__", ts)
    lb = open(LOG, "rb").read()
    lt = lb.decode("utf-8")
    assert "\r\r" not in lt
    open(LOG, "wb").write((lt.rstrip("\n") + entry + "\n").encode("utf-8"))
    print("今日日志 %d -> %d bytes" % (len(lb), os.path.getsize(LOG)))


if __name__ == "__main__":
    main()
