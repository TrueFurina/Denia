# -*- coding: utf-8 -*-
"""纯二进制 append 到今日日志（避免 decode->encode 往返）。
写前备份 + 写后断言前缀不变。
"""
import os, shutil, time

LOG = r"G:\ai对话\.workbuddy\memory\2026-09-22.md"

TEXT = """

---

## 13:0x 【链路纠偏 + 双工真凶】外挂 TTS 与纯双工必须分开查

### 用户纠偏（重要）
> 「等一下你说的这个是外挂tts吧，就你说的那个cosyvoice，不是说纯双工吗」

**我此前跑偏了**：把 `src/voice/cosyvoice_server.py`（9883 端口外挂 TTS）的哼鸣，
当成双工音色问题在查。二者**互不相干**：
- **纯双工** = `duplex_mode: local_omni` → `OwnLlamaServerEngine` → Comni worker/gateway
  + llama-server，音色由 **prompt_cache.gguf（声码器，强）+ voice_audio（LLM 层，弱）** 决定
- **外挂 TTS** = CosyVoice2 独立服务，先出文本再合成，**不是真双工**

⇒ **今后凡涉及"克隆音色/双工效果"，先确认问的是哪条链路**（问用户或看 `duplex_mode`）。

### 双工链路：新发现的致命问题（真凶）
用 `tools/probe_duplex_refaudio.py --live` 拉起 standalone worker 直测，**模型加载即失败**：

```
cpp_backend: Calling omni_init: media_type=2, duplex=True, lang=zh, custom_prompt=False
[CPP] llama_model_loader: loaded meta data with 30 key-value pairs and 193 tensors
      from H:\\minicpm models\\MiniCPM-o-4_5-gguf/tts/MiniCPM-o-4_5-tts-F16.gguf
[CPP] llama_model_load: error loading model: invalid vector subscript      <-- 崩
[CPP] srv log_server_r: request: POST /v1/stream/omni_init 127.0.0.1 500
omni_init returned 500 (attempt 1/3) ... attempt 2/3 ConnectionError: C++ server likely crashed
RuntimeError: omni_init failed after restart: 599   ← 重试 3 次全败
ERROR: Application startup failed. Exiting.
```
**注意：主 LLM（Q4_K_M）加载是成功的（`main: model loaded`），崩的是 `tts/` 子模型。**

### 根因锁定：C++ 引擎版本 << 模型版本（时间线铁证）
```
H:\\Comni\\Comni.exe / llama-server.exe / omni.dll / ggml-cuda.dll   全部 04-29 21:5x
MiniCPM-o-4_5-*.gguf（主/tts/audio/vision）                          全部 08-27 09:3x
G:\\Comni\\Comni-full.exe（与模型同日期的新安装包，622MB）             08-27 03:51  ← 尚未安装！
```
⇒ **04-29 编译的 llama.cpp/omni 去加载 08-27 的模型**，中间隔近 4 个月；
`invalid vector subscript` 是新元数据/张量维度撞旧解析器的典型报错。
**修法方向：安装 `G:\\Comni\\Comni-full.exe`（08-27 版）覆盖 `H:\\Comni`（04-29 版）。**
⚠️ 装前必须备份 `H:\\Comni\\_internal\\resources\\apps\\server\\{worker.py,config.json}` +
   到处可见的 `prompt_cache.gguf` 家族（09-21 烘焙产物，装机会覆盖）。

### 已排除（本轮实测，别再回头查）
| 嫌疑 | 结论 | 证据 |
|---|---|---|
| TTS gguf 文件损坏 | **排除** | `tools/verify_gguf.py` 手动解析：magic GGUF/v3、193 tensors、
30 kv、tokens/token_type 均 158626 对齐、尺寸 1094MB 需要 vs 1103MB 实际 ⇒ **文件完整** |
| 模型文件被人动过 | **排除** | 全部模型 mtime = 08-27 09:3x（原始下载时间），未被动 |
| `config.json` ref_audio 路径失效 | **我误判，已推翻** | worker 的 `_PROJECT_ROOT = dirname(worker.py) = apps\\server`，
但 `_LANG_REF_AUDIO_PATHS` 拼的是 `apps/server/assets/...`，
而文件实际在 **`apps/assets/ref_audio/`**；不过 worker 启动日志显示
`config: 配置已加载: ref_audio=H:\\Comni\\...\\apps/assets/ref_audio/ref_mini...` ⇒ **实际解析是对的** |
| 09-21「原生底色」修复（门条件 generate_audio→or native_audio） | **代码正确** | `local_omni_duplex.py:1389` 已改，
且 `proactive.py:3090` 传了 `ref_audio_path=...or None` + `native_audio=True` ⇒ 链路通 |
| 外挂 CosyVoice2 的 fmax=8000 | **排除** | 改 null 后 flat=0.00001（比原来 0.00005 更差） |

### 双工参考音的三层文件（务必分清）
| 文件 | 层 | 采样率 | flat | f95 | 状态 |
|---|---|---|---|---|---|
| `Comni/apps/assets/ref_audio/daniya_ref.wav` | LLM 层备用 | 16000 | 0.02345 | 1828 | 【已换成达妮娅】 |
| `Comni/apps/assets/ref_audio/ref_minicpm_signature.wav` | LLM 兜底 | 16000 | 0.00582 | 1438 | **仍是原生音**（未换） |
| `Comni/.../tools/omni/assets/default_ref_audio/default_ref_audio.wav` | C++ 声码器层 | 24000 | 0.00888 | 1219 | **未换成达妮娅** |
| `11/assets/voices/daniya_ref.wav` | 项目侧（prepare 下发用） | 24000 | 0.01387 | 1992 | 达妮娅 |
`config.json: audio.ref_audio_path` 仍 = `assets/ref_audio/ref_minicpm_signature.wav`。
09-21 有 `daniya_ref.wav` + `.swapbak_20260921_094240`（同尺寸 257170B）⇒ 换过又换回过。

### 本轮新增工具（可复用）
- `tools/check_tts_audio.py` —— **TTS 音频合格体检器**（`flat`/`f95`/`rms`/`dB` + PASS/FAIL 判定）
  用法 `python tools/check_tts_audio.py <文件或目录>`，判据见该文件头注释
- `tools/verify_gguf.py` —— **GGUF 完整性手动解析器**（header/KV/张量目录/尺寸交叉校验），
  能区分「文件被截断」vs「文件没问题=运行时/版本问题」
- `tools/probe_duplex_refaudio.py` —— **双工参考音链路验证器**（静态检查 + `--live` 拉起 worker 实测）
⚠️ 用 `C:\\Users\\Administrator\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe` 跑（无需 numpy 的两个）；
  `check_tts_audio.py` 需 numpy ⇒ 用 `H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe`

### 备份（本轮）
- `H:\\Comni\\...\\apps\\server\\config.json.bak_refaudio_20260922_124931`（645 B）
"""

data = TEXT.encode("utf-8")
shutil.copy2(LOG, LOG + ".bak_before_duplex_diag_" + time.strftime("%Y%m%d_%H%M%S"))
before = open(LOG, "rb").read()
with open(LOG, "ab") as f:
    f.write(data)
after = open(LOG, "rb").read()
assert after[:len(before)] == before, "前缀被破坏！"
assert after[len(before):] == data, "追加内容不一致！"
print("OK  %d -> %d B  (+%d)" % (len(before), len(after), len(data)))
