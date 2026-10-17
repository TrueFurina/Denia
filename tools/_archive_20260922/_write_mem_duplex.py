# -*- coding: utf-8 -*-
"""写入本次结论：真双工与外挂 TTS 结构性互斥 + >8k 归属取证。"""
import os
import time

MD = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
LOG = r"G:\ai对话\.workbuddy\memory\2026-09-21.md"
ts = time.strftime("%Y-%m-%d %H:%M")


def read_text(p):
    raw = open(p, "rb").read()
    crlf = b"\r\n" in raw
    s = raw.decode("utf-8")
    return (s.replace("\r\n", "\n") if crlf else s), crlf


def write_text(p, s, crlf):
    assert "\r\r" not in s
    open(p, "wb").write(s.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))


# ---------- 1) MEMORY.md ----------
s, crlf = read_text(MD)
print("MEMORY.md bytes", len(s.encode("utf-8")), "CRLF", crlf)

assert "### 2ai" not in s, "2ai already present"

SEC = """### 2ai 真双工 ⚔ 外挂 TTS：结构性互斥（2026-09-21 晚 · 实测定论）
- 🔴 **外挂 TTS 一定丢真双工，两条独立原因，换引擎救不了**：
  ① **时钟不同源**——原生路径 `local_omni_duplex.py:1980/2098` 把引擎逐帧音频攒进 `_turn_audio`，`:1934` 回合边界取出随 `on_speak(text,pcm,sr)` 下传，下游见 pcm **直接播跳合成**（`:233`/`:1978`）＝与 `is_listen` **同一时钟**；外挂路径 `:1981-1983` 丢弃 `audio_data` 只交文本、`:2022` 连语音一起丢 ⇒ 听到的是回合后**另一时钟**重合成。打断判据 `:1947` 只能按字数**估算**（原生可按 pcm **实测**）⇒ 永远对不齐。
  ② **速率喂不饱**——环路消耗 1.00 音频秒/墙钟秒；本机 CosyVoice2 实测 **RTF 0.90~1.35（无余量）、流式首包 2.51s、非流式 7.55s**（`data/native_audio_test/cosy_probe.log`）；云端首包 3.1s（网络段）；原生首音频百毫秒级。
- ✅ 界面开关：`duplex_native_audio:true`（`config.yaml` L1455）＝**唯一真双工路径**；`false`（`local_omni_duplex` 的 `native_audio` 默认 False）＝外挂档。
- 🔑 **>8k 归属取证**（`tools/_hf_whodunit.py`）：`ref_minicpm_signature.wav` 的 8–12kHz ＝ **−62.5dB**（它本身是 16k，>8k 本为空）⇒ 输出里的 >8k **不可能来自参考音**，只能来自**声码器解码器先验**＝"混原生味"的物理落点。克隆未补偿 **−15.3dB** / EQ1 **−4.2dB** / 真源 **−4.5dB** ⇒ EQ 只对齐**电平与形状**（差 0.76dB、相关 0.979），**改不了纹理来源**。
- 🔴 **必须修正 `docs/RVC变声器接入可行性评估.md`**（已写入 §0.0）：原结论"RVC 是多余一层"的前提（原生音频已是达妮娅音色）**仅对 ≤8k 成立**。VC 实为**唯一能在不改时钟前提下改写 >8k 纹理**的杠杆；阻塞项＝显存（~14.6/16GB，需先上 `n_gpu_layers`/4K）＋语料（RVC 需 10min 干净人声，现有仅 8s ⇒ 走 Seed-VC 零样本 1~30s／~300ms）。
- 耳判包 `data/native_audio_test/final_sample/聆听_v5_高频归属/`：唯一变量＝>8k 处理（未补偿／EQ1／限带8k）。
- ⚠️ CosyVoice2 附带坑：参考文本必须与参考音**同句**，否则 `WARNING ... too short than prompt text` 且**连续乱说**（65 字产 59.2s、零停顿）。

"""

s2 = s.rstrip("\n") + "\n\n" + SEC.rstrip("\n") + "\n"
assert s2.count("### 2ai") == 1
write_text(MD, s2, crlf)
print("MEMORY.md -> %d bytes" % len(s2.encode("utf-8")))

# ---------- 2) 今日日志（append-only）----------
if not os.path.exists(LOG):
    open(LOG, "w", encoding="utf-8").close()
ls, lcrlf = read_text(LOG)
entry = """

## %s · 真双工与外挂 TTS 结构性互斥（定论）

**触发**：用户反馈"无论本地外挂 tts 还是云端外挂，都是一样的效果就是无法真全双工"。

**结论一：外挂 TTS 丢双工是结构性的，不是引擎选型问题。**
- 时钟不同源：原生路径的音频与 `is_listen` 同源（`local_omni_duplex.py:1934/1980/2098`），
  外挂路径丢 `audio_data` 只交文本（`:1981-1983`、`:2022`），音频在回合后由另一时钟重合成；
  打断期限只能按字数估算（`:1947`）而非按 pcm 实测。
- 速率喂不饱：环路消耗 1.00 音频秒/墙钟秒，而 CosyVoice2 实测 RTF 0.90~1.35、流式首包 2.51s、
  非流式 7.55s；云端首包 3.1s。原生首音频百毫秒级。

**结论二：>8k 的"原生味"确认来自声码器解码器先验。**
- 新写 `tools/_hf_whodunit.py`：把四格消融输出与真源/原生签名音放同一把 LTAS 尺子上量。
- 关键数据：`ref_minicpm_signature.wav` 的 8–12kHz＝−62.5dB（它自己就是 16k，>8k 为空）；
  克隆未补偿 −15.3dB、EQ1 −4.2dB、真源 −4.5dB。⇒ 输出 >8k 与参考音无关，只可能是解码器先验；
  EQ 只对齐电平与形状（差 0.76dB / 相关 0.979），纹理来源没变。

**结论三：RVC 评估文档的结论需修订（已改）。**
- `docs/RVC变声器接入可行性评估.md` 新增 §0.0；原文"RVC 是多余的一层"前提仅对 ≤8k 成立。
- VC 升级为"音色路线上仅存的实质杠杆"（唯一不动双工时钟就能改写 >8k 纹理的层）；
  阻塞：显存 ~14.6/16GB + 语料（RVC 需 10min，现有 8s ⇒ Seed-VC 零样本）。

**交付**：
- `tools/_hf_whodunit.py`（8k 归属鉴定）、`tools/_make_hf_abx.py`（耳判包生成）
- `data/native_audio_test/final_sample/聆听_v5_高频归属/`（唯一变量＝>8k 处理方式）
- `docs/RVC变声器接入可行性评估.md`（§0.0 修正，备份 `.bak_hfcorr_20260921_232108`）

**顺带修掉的坑**：CosyVoice2 参考文本与参考音不同句会 `too short than prompt text` 并连续乱说
（65 字产 59.2s、零停顿）——这也是先前 CosyVoice 余弦只有 0.50~0.55 的原因。
""" % ts

write_text(LOG, ls + entry, lcrlf)
print("LOG -> %d bytes" % len((ls + entry).encode("utf-8")))
