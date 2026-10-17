# -*- coding: utf-8 -*-
"""追加 2026-09-22 13:1x 的重大纠错到今日日志（纯二进制 append，写前备份 + 写后断言）。"""
import io, os, shutil, time

P = r'G:\ai对话\.workbuddy\memory\2026-09-22.md'

ADD = '''
## 13:1x 【重大纠错·撤回】「重装 Comni 才能修双工」是错的

用户质疑：「你是指需要重新安装新版本才能修复双工合成的问题吗？」
自查后必须**撤回上一条结论**。

### 我的错在哪
上一条把「`invalid vector subscript` → 引擎 04-29 vs 模型 08-27 → 版本不匹配 → 重装新版」
这条链下得太快。**第 2 步到第 3 步是跳的**：拿一次崩溃观察推了一个从没验证过的因果。

### 推翻它的硬证据（全都可复现）
1. **同一套引擎 + 同一套模型，09-21 17:59 完整成功加载 `media_type=2`**：
   `Calling omni_init: media_type=2` → `load_tensors: skipping TTS tensor … loaded separately`
   → `TTS: weights loaded successfully` → `Token2Wav: using prompt_cache from …`
   → `omni_init success: {'success': True, 'media_type': 2, 'use_tts': True, 'voice_audio_used': True}`。
2. **数成功率**（同 04-29 引擎 + 08-27 模型）：
   ```
   worker.log.3 (16.49GB) : invalid vector subscript = 5（全在 09-03 11:06） / omni_init success = 3554
   worker.log.1           : 0 / 2
   worker.log             : 0 / 73
   ```
   ⇒ 成功率 ≈ 99.8%。**"引擎读不懂模型"应该是 0%**。
3. **09-17 23:58 日志把机制记死了**：
   `Token2Wav (C++): session released` → 同进程**第二次**读 `tts/MiniCPM-o-4_5-tts-F16.gguf`
   → `invalid vector subscript` → `llama_init_tts: unable to load TTS model`
   → 官方分支 `restarting C++ server for clean state` → 重启后 `omni_init success`。
   ⇒ 这是**已知偶发**：同一 llama-server 进程里二次加载 TTS 权重会 vector 越界。
   唯一可靠解法＝完全重启 llama-server（`_call_omni_init` 已内置：重试3 → 重启 → 再重试3）。
4. **补丁状态核实**：`reuse 补丁已打=False`、`官方重启分支在=True`（只有 tts 补丁打了）。
   ⇒ 安全网完好，不存在"被补丁搞坏"。

### 更重要的纠错：模型没加载 ≠ 音色混合
- 模型没加载 ⇒ **完全没声音**，不是"原生+达妮娅混合"。逻辑不通。
- 音色的真凶在日志里写着（`worker.log` 里 44 次，**全部同一个值**）：
  ```
  [Duplex RefAudio] LLM=None, TTS=None, tts_field_present=False, tts_same_as_llm=True
  prefilling voice_audio: .../apps/assets/ref_audio/ref_minicpm_signature.wav
  ```
  ⇒ **每一次会话都在用原厂签名音**，达妮娅参考音从来没真正送进去。＝ §2w 那条线。

### 新增可复用判据
- 🔑 **别用一次崩溃日志推"版本不兼容"。先数成功率**（`omni_init success` vs 崩溃次数）。
  99.8% ⇒ 偶发故障，不是版本问题。
- 🔑 **日志里的 `omni_init success` 不可尽信**：09-17 那次 C++ 已 `unable to load TTS model`，
  Python 侧仍打出 `omni_init success: {'success': True…}`。判"TTS 真加载了吗"要看
  **C++ 侧** `TTS: weights loaded successfully` / `Token2Wav: using prompt_cache from`。
- 🔑 **别自己起 worker 复现**：`probe_duplex_refaudio.py --live` 缺 gateway、
  缺 `own_llama_server._worker_env()`，且当时 GPU `free=1.1GB`（用户在跑三角洲）。
  **测试台不同构 ⇒ 结论不可用**（§2af 同款坑）。

### 现场状态（本轮**只读**，未做任何修改）
- 上轮回滚保持不变；`ref_audio_util.py` / `cosyvoice_server.py` 未再动。
- 最后一个成功会话：09-21 22:57（`adx_`）；最后一个成功 omni_init：09-21 17:59。
- 09-21 17:59 之后**唯一被动过**的文件：`token2wav-gguf\\prompt_cache.gguf`（09-21 22:58, 231.03MB）。
- 引擎二进制 `llama-server.exe` / `omni.dll` 仍是 **04-29**；模型仍 **08-27**；**这一对未变，且能跑**。
- `H:\\Comni\\...\\token2wav-gguf\\` 下有 17 个 `prompt_cache*.gguf` 家族备份（09-21 烘焙痕迹）。
'''

b = io.open(P, 'rb').read()
print('原文件', len(b), 'B')
ts = time.strftime('%Y%m%d_%H%M%S')
bak = P + '.bak_verfix_' + ts
shutil.copy2(P, bak)
print('备份 ->', bak)

add = ADD.encode('utf-8')
with io.open(P, 'ab') as f:
    f.write(add)

b2 = io.open(P, 'rb').read()
assert b2[:len(b)] == b, 'append 破坏了原有内容'
assert b2[len(b):] == add, 'append 内容不一致'
assert b2.count(b'\r\r') == 0
print('OK: %d -> %d B' % (len(b), len(b2)))
print('含新章节:', '重大纠错·撤回'.encode('utf-8') in b2)
