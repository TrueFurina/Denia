# -*- coding: utf-8 -*-
"""把本轮交付（修复实机验证 + 自定义音色链路现状）写入项目记忆。CRLF 保留。"""
import os

D = r"G:\ai对话\.workbuddy\memory"
NL = "\r\n"

MEM_ADD = """   - **2z 修复已实机验证（2026-09-21 22:07）**：用 standalone worker（`worker.py --port 22733 --gpu-id 0
     --worker-index 0`，与 app 同参）＋ `ws://127.0.0.1:<p>/ws/duplex?session_id=adx_<ts>`（adx＝音频档
     media_type=1）造两臂对照，**唯一变量＝prepare 里有没有传参考音**。日志实锤：A 臂
     `[Duplex RefAudio] LLM=<Temp>\\daniya_ref_xxxx.wav, TTS=同上`；B 臂 `LLM=None, TTS=None`。
     客观结果：F0 A=237.6 / 244.9（两次复现），B=260.9，参考音 238.8 ⇒ **ΔF0 −1.2 / +6.1 vs +22.1**；
     cos↔原生 A=0.297/0.305 **<** B=0.341。⇒ 修复真的把「原生底色」去掉了，且可复现。
     ⚠️ cos↔参考 两臂只差 0.006 —— 因为两臂**共用同一 `prompt_cache.gguf`（声码器是强层）**，
     故**判 LLM 层只能靠 F0/韵律，不能只看说话人余弦**（这条口径务必延续）。
   - **2aa 双工音频下行有【两条】通道（漏一条＝「有文本没音频」）**：①`{"type":"result", audio_data}`
     （generate 内联）；②`{"type":"audio_only", audio_data}` —— `worker.py:2181-2214` 起了 100ms 轮询任务，
     读 C++ T2W 产出的 `wav_N.wav` 再下发。**原生 TTS 档只走②**（日志 `[WAV poll] sent audio_only`）。
     自建测试台必须两条都收。⚠️ 此路径下 `SPEAK` 行 `llm=0 tts=0 t2w=0 tokens=0 kv=0` 是**正常的**
     （工作量在 C++ 侧，不在 worker 计时口径内），别用它判「TTS 没跑」；
     真判据＝有没有 `T2W线程: wav_N.wav | x.xxs audio | RTF=`。
   - **2ab 自定义音色链路现状（「UI 与接收端能否统一」的答案）**：地基已在，但**原生档没接上**。
     已通：`voice_profiles`（全局音色档，字段含 engine/reference_audio/reference_text/api_url/voice_id…）
     ＋ 角色 `characters[].voice_profile` 绑定 ＋ `config.get_active_voice_config()`（**音色档优先**、返回深拷贝
     防多角色串味）＋ UI `src/ui/web/redesign/ui_redesign_voice.html` 音色档管理区
     ＋ `set_reference_audio()`(webapp.py:8927) —— 且它**同时写** `characters[].voice.reference_audio`
     与 `proactive.duplex_local_omni_ref_audio`（目前**唯一**已通的 UI→原生档通路）。
     未通：①`_VOICE_PROFILE_ENGINES`(webapp.py:2773) **白名单无 Comni 原生档位** ⇒ UI 表达不出
     「用我的音色走原生克隆」；②原生档读的是**全局单键** `proactive.duplex_local_omni_ref_audio`
     （proactive.py:4633→3074），`save/assign_voice_profile` 都不碰它 ⇒ **按角色/按档切换音色在原生档上是断的**；
     ③原生档音色是**两层**（LLM 层每会话传参考音 ＋ 声码器层 `prompt_cache.gguf` 烤死）
     ⇒ 换音色**必须重烤 cache**（本轮实测≈25s），这是它与 cosyvoice/dashscope 等「即插即用」引擎的本质差别。
"""

LOG_ADD = """
## 2026-09-21 22:07 — 修复实机验证：用个性化音色生成试听样本

### 做了什么
- 新建 `tools/gen_timbre_sample.py`：**忠实复刻 app 音频通话档**（`adx_` 前缀 → media_type=1；
  worker 启动参数与 app 一致：`worker.py --port 22733 --gpu-id 0 --worker-index 0`），
  prepare 字段与修复后 `local_omni_duplex.py:1392-1411` 逐字段对齐。
  两臂对照，**唯一变量＝有没有传参考音**：`A_FIXED`（传 ref_audio_path + base64 + tts_ref_audio_base64
  + system_content 内嵌 audio 块）vs `B_NATIVE`（四样全不传＝修复前行为）。
- 首轮跑出「有文本、无音频」——**根因是采集漏了第二条下行通道**，不是功能问题。
  修正后两臂都出音：A 23.44s / B 11.00s。
- 复现：A 臂再跑一次得 25.20s，结论稳定。
- 新增 `tools/check_sample.py`（复用 `ab_objective_check.py` 的同一把尺子）
  与 `tools/make_listening_set.py`（等电平 −20dBFS 试听集）。

### 关键结论（已写入 MEMORY.md §2z / §2aa / §2ab）
- **修复生效且可复现**：`[Duplex RefAudio] LLM=<Temp>\\daniya_ref_*.wav`（修复后）
  vs `LLM=None, TTS=None`（修复前）；F0 **237.6 / 244.9** vs **260.9**，参考音 238.8。
  ⇒ 此前长期归因不了的「+2 半音系统性抬升」＝**LLM 层被原生签名音拉高**，此说法得到实测支撑。
- **口径**：两臂共用同一 `prompt_cache.gguf`（声码器强层），故 cos↔参考 分不开两臂（差 0.006）；
  **判 LLM 层要看 F0/韵律**。
- **下行两条音频通道**：`result.audio_data` 与 `{"type":"audio_only"}`（100ms 轮询 C++ T2W wav）；
  原生 TTS 档**只走后者**。此路径下 `SPEAK` 行全 0 是正常的，真判据是 `T2W线程: wav_N.wav | RTF=`。
- **自定义音色链路**：`voice_profiles` + 角色绑定 + `get_active_voice_config()` + UI 音色页**都已具备**，
  `set_reference_audio` 是唯一已通的 UI→原生档通路；缺口是原生档读**全局单键**、
  `_VOICE_PROFILE_ENGINES` 白名单**无原生档位**、且原生档换音色**必须重烤 cache**。

### 产物
- `data/native_audio_test/final_sample/聆听/`：01 目标参考音 / 02 修复后 / 03 修复前 / 04 修复后第二次
  ＋ `试听_参考音_修复后_修复前.wav`（46.88s，段间 1.5s，全部 −20dBFS 等电平）
- `data/native_audio_test/final_sample/`：A_FIXED.wav / A_FIXED_r2.wav / B_NATIVE.wav / run1/ 备份 / worker_*.log
"""


def append(path, text):
    b = open(path, "rb").read()
    nl = "\r\n" if b"\r\n" in b else "\n"
    t = b.decode("utf-8")
    assert "\r\r" not in t, path
    out = t.rstrip("\r\n") + nl + text.replace("\n", nl) + nl
    assert "\r\r" not in out, path
    open(path, "wb").write(out.encode("utf-8"))
    b2 = open(path, "rb").read()
    print("  %s: %d -> %d bytes  含\\r\\r=%s" % (os.path.basename(path), len(b), len(b2), b"\r\r" in b2))


print("写入项目记忆：")
append(os.path.join(D, "MEMORY.md"), MEM_ADD)
append(os.path.join(D, "2026-09-21.md"), LOG_ADD)
