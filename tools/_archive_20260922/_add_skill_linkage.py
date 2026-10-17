# -*- coding: utf-8 -*-
"""把「先分清哪条链路」插入技能开头 + 追加「版本不匹配」判据。
纯 LF 文件，写前备份，写后断言。
"""
import os, shutil, time

SK = r"C:\Users\Administrator\.workbuddy\skills\comni-voice-clone-audit\SKILL.md"
b0 = open(SK, "rb").read()
assert b"\r\n" not in b0, "该技能应为纯 LF"
s = b0.decode("utf-8")

ANCHOR = "# Comni 原生音色克隆排查与修复\n"

NEW = """# Comni 原生音色克隆排查与修复

## 第零步（先做这个）：分清是哪条链路，别拿 A 的毛病解释 B

本项目同时存在**两套互不相干**的语音链路。搞混会浪费大量时间（**真实踩过**：把
外挂 CosyVoice2 的哼鸣当成双工音色问题查了一整轮）。

| | 纯双工（用户说"纯双工/通话/桌宠"） | 外挂 TTS（"克隆试听/9883"） |
|---|---|---|
| 开关 | `config.yaml: duplex_mode: local_omni` | 独立服务 `src/voice/cosyvoice_server.py` |
| 引擎 | `OwnLlamaServerEngine` → Comni worker+gateway+llama-server | CosyVoice2（9883 端口） |
| 音色载体 | `token2wav-gguf/prompt_cache.gguf`（声码器，**强**）+ `voice_audio`（LLM 层，弱） | 参考音 wav（零样本克隆） |
| 能否真双工 | **是**（边说边听） | **否**（先出文本再合成，用户说完才轮到它） |
| 典型故障 | 不出声/原生音色/版本不匹配崩 | 哼鸣、辅音缺失、ΔF0 偏低 |

**判据：先问用户"你听到的是通话里的声音，还是我导出的试听文件？"**
或直接读 `config.yaml` 的 `duplex_mode` / 看端口 8006（双工）vs 9883（外挂）。

## ⚠️ 双工「模型加载直接失败」的第一嫌疑：C++ 引擎版本 << 模型版本

**症状**（`logs/engine/worker.log`）：
```
[CPP] llama_model_loader: loaded meta data with N key-value pairs and M tensors from .../tts/xxx-F16.gguf
[CPP] llama_model_load: error loading model: invalid vector subscript
[CPP] srv log_server_r: request: POST /v1/stream/omni_init 127.0.0.1 500
omni_init returned 500 (attempt 1/3) … attempt 2/3 ConnectionError: C++ server likely crashed
RuntimeError: omni_init failed after restart: 599      ← 重试 3 次全败
ERROR: Application startup failed. Exiting.
```
**注意：主 LLM 往往加载成功（`main: model loaded`），崩的是 `model_dir/tts/` 子模型。**
⇒ 别去验模型文件，先比 mtime：

```python
# 一行定案：引擎二进制 vs 模型文件 的日期
import os, time
for p in (r"H:\Comni\_internal\resources\build\bin\Release\llama-server.exe",
          r"H:\Comni\_internal\resources\build\bin\Release\omni.dll",
          r"H:\minicpm models\MiniCPM-o-4_5-gguf\MiniCPM-o-4_5-Q4_K_M.gguf"):
    print(time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(p))), p)
```
**差 1 个月以上 ⇒ 基本就是这个**。修法是装与模型同日期发布的 Comni 安装包
（找 `Comni-full.exe` / `Comni-Setup-win64.exe`，比 mtime）。
⚠️ 装前备份 `apps/server/{worker.py,config.json}` + 全部 `prompt_cache*.gguf`（装机会覆盖）。

**排除"文件本身坏"用** `tools/verify_gguf.py`：手动解析 GGUF header/KV/张量目录，
若 magic=GGUF、tensors/kv 数与日志吻合、tokenizer 数组长度对齐 vocab、
尺寸 ≥ 张量所需 ⇒ **文件没问题，问题在运行时**。

"""

assert s.count(ANCHOR) == 1, "锚点不唯一: %d" % s.count(ANCHOR)
s2 = s.replace(ANCHOR, NEW, 1)

shutil.copy2(SK, SK + ".bak_linkage_" + time.strftime("%Y%m%d_%H%M%S"))
with open(SK, "wb") as f:
    f.write(s2.encode("utf-8"))
b1 = open(SK, "rb").read()
assert b"\r\n" not in b1
print("OK  %d -> %d B (+%d)" % (len(b0), len(b1), len(b1) - len(b0)))
