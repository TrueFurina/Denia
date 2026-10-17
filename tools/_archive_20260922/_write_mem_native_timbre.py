# -*- coding: utf-8 -*-
"""把「原生音色底色」根因与 cache 内部结构写入项目记忆。"""
import os
import time

D = r"G:\ai对话\.workbuddy\memory"
os.makedirs(D, exist_ok=True)
MEM = os.path.join(D, "MEMORY.md")
DAY = os.path.join(D, time.strftime("%Y-%m-%d") + ".md")

BLOCK = '''
- **2w 原生底色（已修·2026-09-21）**：症状＝「换了新 cache 仍能听出 MiniCPM 原生嗓音，但和原版又不完全一样」
  = **两层音色打架**：LLM 层 `voice_audio` 用 `apps/assets/ref_audio/ref_minicpm_signature.wav`(F0≈286Hz 原生)
  ｜ 声码器层用我们烤的 `prompt_cache.gguf`(F0≈235Hz 达妮娅) ⇒ 听感「达妮娅的壳 + 原生的底」。
  根因＝`config.proactive.duplex_local_omni_ref_audio: ''`(空) **＋** `local_omni_duplex._generate_audio`
  **全仓只有 False 一处赋值、从无 True** ⇒ `prepare` 永不带 ref_audio ⇒ `worker.py:2094-2097` 回落
  `_LANG_REF_AUDIO_PATHS["zh"]`。铁证两行：`[Duplex RefAudio] LLM=None, TTS=None` +
  `prefilling voice_audio: .../ref_minicpm_signature.wav`。修法（已落盘）：①`config.yaml` L1409 填
  `G:\\ai对话\\11\\assets\\voices\\daniya_ref.wav` ②`local_omni_duplex.py` 门条件改
  `(self._generate_audio or self._native_audio)`。⚠️ 开销不变：原生档本来每次就在 prefill 参考音(n_past≈1211)。
  ⚠️ **推翻旧结论**：「产出 F0 系统性抬升 +2 半音＝声码器路径特征」→ 实为 **LLM 层被 286Hz 原生音拉高**。
- **2x prompt_cache.gguf 内部（判据）**：恒 5 张量＝`spk_cb`(192×f32，**唯一音色分量**)＋ conformer/estimator 的
  cnn/att cache(中间状态快照；att 的时间维 T ＝参考音帧数)。**data_start 恒＝736**(header_end 719 对齐 32)，
  spk_cb 就在 736 处、768 字节。判「引擎是否真加载」看日志 `Token2Wav: using prompt_cache from` vs
  `prompt_cache failed, fallback to prompt_bundle from`；判「烤制是否生效」比 spk_cb md5 + 反推 T。
  实测四代：微信精修 Live `ee61d3e2`T=346 ／ 历史247MB `a00a7dc8`T=354 ／ 录屏332MB `285dde63`T=476 ／
  9-21失败版 `d85d4fd0`T=316。⚠️ **`d85d4fd0` 与 Comni 安装目录 bundle 的 `spk_f32.bin` 完全相同
  ＝MiniCPM 原生说话人向量**（可当「原生」基准锚点）。
- **2y `duplex_prepare` 丢参**：`cpp_backend.py:511-520` 收了 `prompt_wav_path` 却**只下发 `voice_audio`**
  ⇒ `worker.py:2049` 注释「TTS ref audio → prompt_wav_path（初始化 vocoder）」在 C++ 路径上是空头承诺；
  C++ 不认 `prompt_wav_path` 字段（omni.dll 符号表无此串）。声码器音色只能靠 cache / bundle。
'''

with open(MEM, "a", encoding="utf-8") as f:
    f.write(BLOCK)

DAYBLOCK = '''
## 21:5x 定位并修复「原生音色底色」（用户质疑重烤无效）

- 用户质疑：重烤后仍能听出 MiniCPM 原生嗓音，「和原版不一样但底色能听出来」。逐一取证后确认：
  **不是烤制无效，而是接线断了**。
- 取证链：
  1. `omni.dll` 字符串表 → 加载顺序 `using prompt_cache` → 失败才 `fallback to prompt_bundle`；
     并暴露 KV/张量名与 `T2W_EXPORT_CACHE_DIR`。
  2. `logs/engine/worker.log` → `using prompt_cache from .../token2wav-gguf/prompt_cache.gguf` +
     `initialized successfully`（**没回落**）⇒ 声码器层吃到了我们烤的 cache。
  3. 同日志 → `[Duplex RefAudio] LLM=None, TTS=None` +
     `prefilling voice_audio: .../ref_minicpm_signature.wav` ⇒ **LLM 层用的是 MiniCPM 原生签名音**。
  4. 逐层回溯：`config.yaml:1409 duplex_local_omni_ref_audio: ''` → `proactive.py:5585 or None`
     → client `_ref_audio_path=None`；叠加 `local_omni_duplex.py:214 _generate_audio=False`（全仓无 True）
     → L1380 门条件永远 False → `prepare` 不带 ref_audio → `worker.py:2095` 强制回落。
  5. 声学指纹坐实：`ref_minicpm_signature.wav` F0≈286Hz vs 达妮娅 235Hz（差 ~4 半音）；
     而 A/B 三方产出 F0 全在 260~270Hz＝**原生与达妮娅的中间值** ⇒ 完美解释此前无法归因的「F0 抬升」。
- 修复（已落盘 + 三层冒烟 PASS）：
  - `config.yaml` L1409：`''` → `G:\\ai对话\\11\\assets\\voices\\daniya_ref.wav`
  - `src/llm/local_omni_duplex.py` L1389：门条件 → `(self._generate_audio or self._native_audio) and ...`
  - 备份：`config.yaml.bak_before_refaudio_20260921_215703` / `local_omni_duplex.py.bak_before_refaudio_*`
- 另立判据：cache 内 `spk_cb`(192 维) 是唯一音色分量；`data_start` 恒 736；att 时间维 T 与参考音帧数一一对应。
- 新增工具：`_dll_strings.py`(扫 dll 符号) `_gguf_dump.py` `_spk_extract3.py`(固定偏移读 spk_cb)
  `_scan_engine_log.py` `_diag_t2w_load.py` `_apply_ref_audio_fix.py` `_smoke_ref_audio_fix.py`
- 待办：重启软件后验日志应变为 `[Duplex RefAudio] LLM=<...daniya_ref...>` 且
  `prefilling voice_audio:` 不再是 `ref_minicpm_signature.wav`；可选兜底＝把 Comni 安装目录的
  `ref_minicpm_signature.wav` 也换成达妮娅（未做，需用户确认）。
'''

with open(DAY, "a", encoding="utf-8") as f:
    f.write(DAYBLOCK)

for p in (MEM, DAY):
    b = open(p, "rb").read()
    t = b.decode("utf-8")
    print("%-22s bytes=%d chars=%d CRLF=%s CRCR=%s" % (
        os.path.basename(p), len(b), len(t), b"\r\n" in b, b"\r\r" in b))
print()
print("=== MEMORY.md 新增段（校验）===")
t = open(MEM, encoding="utf-8").read()
for line in t.split("\n"):
    if line.startswith("- **2w") or line.startswith("- **2x") or line.startswith("- **2y"):
        print(line[:180])
