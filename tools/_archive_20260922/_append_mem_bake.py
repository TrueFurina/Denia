# -*- coding: utf-8 -*-
"""把本轮"声码器缓存重烤"结论写入日更与 REFERENCE（追加，只增不改）。"""
import os

MEM = r"G:\ai对话\.workbuddy\memory"

DAILY = """\

### 19. ★ 声码器缓存 prompt_cache.gguf 重烤成功（"糊"的第一杠杆，承接 §18）
#### 19.1 机制（反编 `omni.dll` 确认，非猜测）
`G:\\ai对话\\11\\tools\\_strings_llamaserver.py` 抽到关键串：
`tools/omni/assets/default_ref_audio`、`/spk_f32.bin`、`/prompt_tokens_i32.bin`、`/prompt_mel_btc_f32.bin`、
`tools/omni/assets/default_ref_audio/default_ref_audio.wav`、`Token2Wav: using prompt_cache from %s`、
`Token2Wav: prompt_cache failed, fallback to prompt_bundle from %s`、`T2W_EXPORT_CACHE_DIR`、
`[Token2Mel] exported prompt_cache.gguf: %s`。
⇒ 缓存缺失时 C++ 走 `init_from_prompt_cache_gguf` 失败 → `load_prompt_bundle_dir(<CWD>/tools/omni/assets/default_ref_audio)`；
若设 `T2W_EXPORT_CACHE_DIR` 就把重生缓存导出。**CWD = `H:\\Comni\\_internal\\resources`**（`cpp_backend.py:1306 cwd=llamacpp_root`）。

#### 19.2 ★★ 昨天烤失败的真正原因：bundle 里的 wav 被塞成 48kHz
- 历史成功 bundle 的 `default_ref_audio.wav` 是 **16k（原厂快照 7.20s）或 24k（09-13 各次 9.68s）**。
- 昨天 `_do_bake.py` 第 2 步把 48kHz 参考音**原样** copy 成 `default_ref_audio.wav`。
- 现象：日志照常 `init tts....init t2w....` + `gguf_init_from_file: failed to open ...prompt_cache.gguf`，
  但**再没有 `[Token2Mel] exported` 行** ⇒ 静默放弃建缓存，bake 900s 超时还原。
- **修法：wav 必须重采样成 24kHz 再放进去**（`_resample_wav.py`）。改完立刻导出成功。
- **判据（可复用）：烤完只认日志里有没有 `[Token2Mel] exported prompt_cache.gguf`；没有＝没烤成。**

#### 19.3 本次产出（两版，供用户耳朵选）
| 版本 | 参考音 | F0 中位 | 频带%[120-300,300-1k,3.4-6k,6-8k,8-12k] | cache |
|---|---|---|---|---|
| **A 自然音高** | FINAL2（48k 原生）| **287.5Hz** | 64.0/24.5/2.33/1.12/**3.81** | `daniya_cache_A_natural_287.gguf` md5 `1fb10436a7b7` 221361632B **＝当前 live** |
| **B 对齐历史音高** | FINAL3（rubberband 保共振峰 ×0.8557）| **241.2Hz** | 73.7/17.5/1.56/1.42/**2.72** | `daniya_cache_B_pitchmatch_241.gguf` |
| 旧 live（08-28,16k 素材）| — | — | — | `prompt_cache.gguf.bak_v2_20260921_092505` md5 `146fdbe71184` |
- 音高基准：原厂 bundle **246.0Hz** ｜ 旧 `daniya_ref.wav` **243.6Hz** ｜ 视频全片中位 ~263.7Hz。
- ⚠️ **降调会把能量挤进 120-300Hz 低带**（FINAL2 64% → FINAL3 73.7%，已接近被判"闷"的旧参考 70.5%）
  ⇒ **"糊"可能同时来自"砍高频"和"F0 偏低"**，故 A/B 都给用户听。
- 回退旧版：`copy /Y ...\\prompt_cache.gguf.bak_v2_20260921_092505 ...\\prompt_cache.gguf`
- ⚠️ **新 cache 生效必须重启 llama-server**（`full_reinit` 只在改档/会话结束时发生；同档内 `/v1/stream/break` 不重载缓存）。
- 全流程细节见 `REFERENCE.md §23`。
"""

REF = """\

# §23【2026-09-21】声码器缓存 prompt_cache.gguf 重烤成功（"糊"的第一杠杆）

## 23.1 音色两层与谁定音色（承接 §20.2）
- LLM 层 `voice_audio`：弱，只是 system prompt 里的风格提示（APM）。
- **Vocoder 层 `prompt_cache.gguf`：强，真正决定音色。**
- 正常会话：`apps/server/core/processors/cpp_backend.py` L1456-1457
  `if self.ref_audio_path and os.path.exists(self.ref_audio_path): req_body["voice_audio"] = self.ref_audio_path`
- 烘焙时**刻意不带 voice_audio**，逼 C++ 走 bundle 回退路径。

## 23.2 回退/导出机制（从 `omni.dll` 抽串反编，非猜测）
工具：`G:\\ai对话\\11\\tools\\_strings_llamaserver.py`（ASCII 扫描 `llama-server.exe` 及同目录 DLL）。
关键串：
```
tools/omni/assets/default_ref_audio            <- bundle 目录（**CWD 相对**）
/spk_f32.bin  /prompt_tokens_i32.bin  /prompt_mel_btc_f32.bin
tools/omni/assets/default_ref_audio/default_ref_audio.wav
Token2Wav: using prompt_cache from %s
Token2Wav: prompt_cache failed, fallback to prompt_bundle from %s
T2W_EXPORT_CACHE_DIR
[Token2Mel] exported prompt_cache.gguf: %s
?load_prompt_bundle_dir@Token2Mel@flow@omni@@SA_N...    (static bool load_prompt_bundle_dir(std::string const&, PromptBundle&))
?init_from_prompt_bundle@Token2MelSession@flow@omni@@...
?init_from_prompt_cache_gguf@Token2MelSession@flow@omni@@...
prompt_cache.spk_cb / conformer_cnn_cache / conformer_att_cache / estimator_cnn_cache / estimator_att_cache
```
⇒ 缓存缺失 → `init_from_prompt_cache_gguf` 失败 → `load_prompt_bundle_dir(<CWD>/tools/omni/assets/default_ref_audio)`
→ 若设 `T2W_EXPORT_CACHE_DIR` 则导出重生缓存。
**CWD 由 `cpp_backend.py:1306` 的 `cwd=self.llamacpp_root` 决定 = `H:\\Comni\\_internal\\resources`。**
⚠️ 另有一份 bundle 在 `build\\bin\\Release\\tools\\omni\\assets\\default_ref_audio`（构建产物），**当前未被读取**（本次也一并同步以防万一）。
⚠️ C++ 无源码（预编译二进制），所有结论靠"抽串 + 行为对照"推出。

## 23.3 ★★ 昨日烤失败的真正原因：bundle 的 wav 采样率
- 历史成功 bundle 的 `default_ref_audio.wav`：原厂快照 **16k/7.20s**；09-13 各次 **24k/9.68s**。**两者都能成功。**
- 昨日 `_do_bake.py` 第 2 步把 `FINAL2_daniya_ref_48k.wav` **原样** copy 成 `default_ref_audio.wav`（**48kHz**）。
- 现象：C++ 日志照常 `init tts....init t2w....flowGGUFModelLoader: init_backend` +
  `gguf_init_from_file: failed to open GGUF file '...prompt_cache.gguf'`，
  但**之后没有 `[Token2Mel] exported prompt_cache.gguf`** ⇒ 静默放弃建缓存；bake 900s 到期还原原缓存。
- 对照 diff（今天 vs 09-13 成功日志）：**只差这一行**，其余逐行相同。
- **修法：wav 必须重采样为 24kHz 再放入 bundle**（`tools/_resample_wav.py`，torchaudio Resample → PCM16 单声道）。
- **判据（可复用）：烤完只看日志有没有 `[Token2Mel] exported prompt_cache.gguf`；没有＝没烤成。**

## 23.4 已排除的错误假设（记录以免重复排查）
- ❌「导出目录已有同名文件 ⇒ C++ 不覆盖」：`bake_run3`/`bake_run4` 在同一导出目录**已有文件**时仍然**成功** ⇒ 不成立。
- ❌「烘焙脚本差异」：`bake_cache_ceshi.py` 与 `bake_cache_ceshi3.py` **除端口/路径常量外逐字节等价**，却一失败一成功。
- ❌「Comni 栈在跑导致 GPU/端口冲突」：今天失败时栈在跑，但同条件下换 24k wav 立刻成功。
- ❌「C++ 读 exe 旁那份 bundle」：CWD 已证为 resources 根，exe 旁那份未被读取。
- ❌「bundle 的 .bin 自洽性」：新旧 bundle 的 tokens/mel 均满足 `mel 帧 = (tokens-3)*2`，都自洽。

## 23.5 本次产出（两版，供耳朵选）
| 版本 | 参考音 | F0 中位 | 频带% [120-300, 300-1k, 3.4-6k, 6-8k, 8-12k] | cache 文件 | md5 | 大小 |
|---|---|---|---|---|---|---|
| **A 自然音高** | FINAL2（48k 原生） | **287.5Hz** | 64.0 / 24.5 / 2.33 / 1.12 / **3.81** | `H:\\daniya_voice_tools\\daniya_cache_A_natural_287.gguf` | `1fb10436a7b7` | 221361632 |
| **B 对齐历史音高** | FINAL3（rubberband ×0.8557 保共振峰） | **241.2Hz** | 73.7 / 17.5 / 1.56 / 1.42 / **2.72** | `H:\\daniya_voice_tools\\daniya_cache_B_pitchmatch_241.gguf` | 见产物 | — |
| 旧 live（08-28 · 16k 素材） | — | — | — | `prompt_cache.gguf.bak_v2_20260921_092505` | `146fdbe71184` | 247821792 |

音高基准：原厂 bundle **246.0Hz** ｜ 旧 `daniya_ref.wav` **243.6Hz** ｜ 旧 bundle(9.68s) 268.5Hz ｜ 视频全片中位 ~263.7Hz。
🔑 **重要洞见：降调会把能量挤进 120-300Hz 低带**（FINAL2 64% → FINAL3 73.7%，已接近被判"闷"的旧参考 70.5%）
⇒ **"糊"可能同时来自"砍高频"与"F0 偏低"两件事**，所以两版都交给用户耳朵定夺。

## 23.6 操作手册（复现 / 切换 / 回退）
- 单版重烤：`python G:\\ai对话\\11\\tools\\_do_bake2.py`（自带 24k 重采样 + 空导出目录 + 重度埋点）
- A/B 两版：`python G:\\ai对话\\11\\tools\\_do_bake3.py`（自动存档 A、烤 B、把 live 还原为 A）
- 验证缓存可加载：`python G:\\ai对话\\11\\tools\\_verify_cache_load.py`
  （日志里**不再出现** `gguf_init_from_file: failed to open` 即视为成功加载）
- 切换 A/B：`copy /Y <目标cache> "H:\\minicpm models\\MiniCPM-o-4_5-gguf\\token2wav-gguf\\prompt_cache.gguf"`
- 回退旧版：`copy /Y "...\\prompt_cache.gguf.bak_v2_20260921_092505" "...\\prompt_cache.gguf"`
- ⚠️ **新 cache 生效必须重启 llama-server**：`full_reinit()` 只在改档（duplex/media_type 变化）或会话结束时触发；
  同档内走 `/v1/stream/break` 轻量重置，**不会重载 prompt_cache.gguf**。最省事＝切一次桌宠/通话档，或重启软件。
- bundle 备份：`default_ref_audio.keep_A_natural`（FINAL2）／`default_ref_audio.keep_B_pitchmatch`（FINAL3）／
  `default_ref_audio.bak_prev_20260921_090130`（09-21 前状态，24k）／`default_ref_audio.wav.bak48k_20260921_092505`（48k 误版留证）
- 参考音素材：`G:\\ai对话\\11\\data\\native_audio_test\\ref_build\\FINAL2_daniya_ref_{48k,16k}.wav`（自然音高 287.5Hz）、
  `FINAL3_daniya_ref_{48k,16k}.wav`（保共振峰降调 241.2Hz）

## 23.7 工具清单（本轮新增，均在 `G:\\ai对话\\11\\tools\\`）
`_strings_llamaserver.py`（反编 DLL 定位契约）· `_do_bake2.py` · `_do_bake3.py` · `_resample_wav.py` ·
`_verify_cache_load.py` · `_f0_compare.py` · `_build_ref_pitchmatch.py` · `_diag_bake_env.py` ·
`_cmp_bundle_dirs.py` · `_scan_omni_tools.py` · `_find_t2w_ref.py` · `_find_pristine_bundle.py`
"""


def append(path, text):
    with open(path, "rb") as f:
        b = f.read()
    nl = b"\r\n" if b"\r\n" in b else b"\n"
    assert b"\r\r" not in b, "目标文件已有 \\r\\r"
    out = b + text.replace("\n", nl.decode()).encode("utf-8")
    assert b"\r\r" not in out.replace(nl, b"@@"), "写回后出现 \\r\\r"
    with open(path, "wb") as f:
        f.write(out)
    print("appended %d chars -> %s (nl=%r)" % (len(text), os.path.basename(path), nl))


append(os.path.join(MEM, "2026-09-21.md"), DAILY)
append(os.path.join(MEM, "REFERENCE.md"), REF)
