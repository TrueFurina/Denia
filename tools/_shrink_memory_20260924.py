# -*- coding: utf-8 -*-
"""2026-09-24 00:10 整理 MEMORY.md：23.4KB -> 压缩版；原文并入 REFERENCE.md 存档（不丢信息）。"""
import io, os, shutil, time

BASE = r'G:\ai对话\.workbuddy\memory'
MP = os.path.join(BASE, 'MEMORY.md')
RP = os.path.join(BASE, 'REFERENCE.md')

raw = open(MP, 'rb').read()
old = raw.decode('utf-8')
print('原 MEMORY.md %d bytes' % len(raw))

# 1) 原样并入 REFERENCE.md（存档，不丢信息）
stamp = time.strftime('%Y-%m-%d %H:%M')
BAR = chr(61) * 78
hdr = ('\r\n\r\n' + BAR + '\r\n'
       '## [存档] MEMORY.md 2026-09-24 压缩前的完整原文（' + stamp + '，'
       + str(len(old)) + ' 字符）\r\n'
       '> 以下为压缩前快照，仅供追溯；现行版本见 MEMORY.md。\r\n'
       + BAR + '\r\n')
with open(RP, 'ab') as f:
    f.write(hdr.encode('utf-8'))
    f.write(old.encode('utf-8'))
print('已并入 REFERENCE.md，现 %d bytes' % os.path.getsize(RP))

# 2) 备份
bak = os.path.join(BASE, '_bak_MEMORY_20260924_0010.md')
shutil.copyfile(MP, bak)
print('备份 ->', bak)

NEW = r'''# 项目长期记忆（AI 桌面伴侣 / G:\ai对话\11）
> 索引（触发词 → 一句判据）。完整推导/数字见 `REFERENCE.md` 与当日 `YYYY-MM-DD.md`。
> ⭐最热：**§2cw 不死磕全双工→先降级联（四件套已在仓库＝接线非研发；8k 是共享 KV cache）**／**§2cv 常驻块从未挂通话档＝最高 ROI**／**§2cx 双工大脑＝Qwen3-8B@Q4_K_M（9B 总）**／**§2cs+§2ct 人格被提示词删掉**
> ⚠️已撤回：§2s 16k 素材糊／§2az／§2ba／§2at／§2bx-2by。章节 §16–§30（**§29/§30 已作废**）。

## 0. 心法
- 评估延迟前**先确认档位**：录像 `_adx_`＝通话档（wall 69/131ms，瓶颈非算力）／`_omni_`＝全模态（787/868ms 贴线）。门槛 ≤2s 可修／≤0.8s 换合成栈／≤0.4s 重设计。
- 先问「屏幕上显示什么字」，grep 原文反查分支。分层：①设备死流②电平③模型裁决偏置④被哪道闸吃掉。
- 「不理我」**五型**：①模型没产出（LISTEN 偏置）②闸门吞③出了但太慢（兜底抢先）④UI 上屏但语音全程没声⑤复读用户的话（抢掉本轮）。
- 「UI 有字但没声」判据＝**三计数**：`emit`→`tts_recv`→`tts_begin`；第一段差额＝闸门吞。
- 静默容错：取不到必须返回 None；**`log+except` 吞错是本项目最大隐性 bug 源**（已四踩）。
- 判死代码全仓搜；选型先查「项目里是否已造好」；测效果前先问「测试台与 app 同构吗」。
- ⭐⭐**旋钮必须成对改**：`listen_prob_scale`(lps) 与 `*_frame_interval_s`(fi)。fi **不是推流频率**，是把 1s 音频切 N 块。

## 0b. 追踪器
- `tools/analyze_call_trace.py`；埋点 `src/llm/call_trace.py`→`data/call_trace_*.jsonl`。
- **服务端地面真值**（比客户端硬）：`H:\Comni\...\sessions\*\recording.json`→`chunks[]`；工具 `tools/_analyze_server_truth.py`、`tools/_cmp_native_vs_ours.py`。

## 1. 改代码红线
- 入口：`11/main.py` + `launch_companion.py`/`run_webui.py`。多处改动一律**单脚本 + 每处断言 count==1**（并行 Edit 有竞态）。改前 `cp _bak_*`。
- 换行：config.yaml/local_omni_duplex.py/webapp.py/记忆＝CRLF；**proactive.py＝LF**。
- config.yaml 改后三层冒烟：YAML 真解析→import→`load_config()`。基线 `pre-refactor-20260910`。

### 1b. 环境
- 数值/音频用 `H:\daniya_voice_tools\client_venv`；CosyVoice 侧 `11\cosyvoice_venv`；managed py3.13.12 无 numpy/yaml。
- 沙箱 `http_proxy=127.0.0.1:52377` 会拦 localhost 返假 502⇒探测脚本必须 `ProxyHandler({})`。
- bash 工具链 dirname/ls/head/tail 全 not found⇒纯 Python；`rm` 假成功⇒`os.remove`；内联反引号被吃掉⇒写脚本文件。

## 2. 问题族谱（详见 REFERENCE.md）
### A 组 已修（只留判据）
2a 麦死流：1s 内 `np.unique` 峰值 ≤3 值｜2b/2c 不理人↔抢话（同一旋钮两头挪）｜2d 闩锁⇒时间窗口｜2e 复读三源｜2f 提示词副本化｜2g 延迟中位 4.6s｜2h 静音判据「精确=0」＝总根因｜2k WebView2 旧桥｜2n 长 script 块抛异常。

### B 组（★＝热点）
- ★**2cx 双工大脑＝Qwen3-8B**：MiniCPM-o 4.5 共 9B＝SigLIP-ViT 0.4B＋Whisper-medium 0.3B＋**Qwen3-8B 主干**＋语音解码器 ~0.3B（详见 §4）。我们跑 `Q4_K_M`。
- ★**2cw 不死磕全双工→先降级联，组件全在仓库（接线非研发）**：`src/voice/asr.py`(sherpa-onnx 流式+vosk 回退)／`src/voice/human_voice_detector.py`(VAD)／`src/llm/openai_client.py`+ollama(`qwen3:14b`, `num_ctx 4096`)／`src/voice/cosyvoice_server.py`(**stream=True+instruct 情感**)。
  - **8k 是共享 KV cache**（`vram_profile.py:95`，通话档 8192）⇒双工下音频历史与文本抢同一份＝**长期记忆塞不进、人格连续性差的机制**。
  - 纠正「效果差＝外挂 TTS 的锅」：**音色像不像＝外挂 TTS 功劳；人格与智能＝与 TTS 完全无关**。
  - **可打断＝播放层机制**（100~200ms，比双工 100~300ms 更低）。级联预算 0.75~3.1s vs 现状 3.76~9.40s。
  - ⚠️**音色绝不能烘焙进 `prompt_cache`**（换音色就要重建会话⇒热切换作废）。
  - 回头做双工四前提：ctx≥32k 且非 Q4／音色已解／级联已 <1s／独立预算。**当前一条不满足。**
- ★**2cv 常驻块从未挂通话档＝最高 ROI**：`_build_duplex_global_prompt()`（人格站位＋11 条守则）只在 `proactive.py:660` 追加到**视频档末尾**；通话档 `_build_duplex_instructions_call` **第 468 行提前 return**。弱模型对**末尾**指令权重最高。
  视频档既守人设又看屏＝三因素：①块在末尾 ②**真实屏幕帧占住注意力**（通话档提示词是唯一强语义源⇒就去念它）③时间压力（桌宠可沉默，通话被逼 1~2s⇒退化复读）。
- ★**2cv 原生 Comni 地面真值**：88 字 prompt、零轮转指令、`generate_audio`/`deferred_finalize`=true、不传 lps、1000ms/块 ⇒ SPEAK **44.7~56.7%**（我们 26%），最大连续 41 块。⇒ **原生不是更会等待，是更爱开口**；「犹豫/判断/等待」**不是引擎能力**，是我们 prompt+lps+8 道闸门造的。
- ★**2cv-2 全双工无 VAD（铁证）**：`StreamingVAD` 只在 `HalfDuplex`（worker.py:1454/1501/1595/1724）。duplex 靠每块内部裁决（原生 36~60ms／我们 85~136ms）。**VAD 目录存在 ≠ duplex 用它。**
- ★**2cv-3 `deferred_finalize`＝纯延迟优化**（省 30~50ms），默认 true，我们 audio 档显式传 False。引擎默认 `length_penalty=1.1`，原生 1.05，我们 1.0。
- ★**2cu 提示词三层**：`system_prompt`＝`{prefix}\n<behavior>{instruction}</behavior>`；通话档走 `_custom` 分支**直接 return（452 行），不加载 else 分支 469~505 的 9 条通用守则**。角色层＝`prompts/call_profiles/danya_persona.txt`（**换角色只改它**）；行为层＝`danya_behavior.txt`（全角色共用）；prefix＝`_call_audio_vision_prefix()`。桌宠人设来自 config `characters:`（与通话档隔离）。
- ★**2ct 人格＝被提示词删掉**：①前缀「短一点/不用找话说」②行为段「一句就够」③角色段纯声明式⇒弱模型做关键词检索⇒**自我介绍复读**。判据＝去重 SPEAK 句数 vs 含反问/情绪词句数。⚠️这类复读闸门拦不住也不该拦。
- ★**2cs 延迟三段账**＝引擎起手 1.13~6.65s(53%)＋等整句 1.95s＋TTS 首包 0.71s。**「成句即送」已修**（回放 7 回 6 中，省 0.81s/turn；回归 `tests/test_call_early_sentence_emit.py`）。
  ⚠️改文本累积必查三处：`is_listen` 只取 `_turn_text[_sentn:]`、≥200 字兜底只在 pending 找切点、**新回合复位为 0**。
- ★**2cr 提示词改动只动通话档**；22:30 首包 0.70/0.83/1.07s 已达标但 **6 句上屏仅 3 句出声**⇒「效果差」主因＝**答对但没播**。`_call_audio_vision_note()` 是死代码。
- ★**2cm 井喷＝自复读**：整句 Jaccard>0.7（`_dup_sim`），**别用前 N 字**（误伤合法问候）。2Hz 档 lps↓⇒井喷更凶（0.3→SPEAK 90.2%／**1.0→44.4% 且自复读仅 2.9%**）。回放两坑：录像 text 是逐块碎片须按连续 SPEAK 段聚合；桩对象须每通一个、跨 emit 保状态。
- ★**2co 复读用户话**：`user_echo` 整句丢弃。已知取舍：用户自己重复问时重复答也被拦。
- ★**2cl lps＝哑火/井喷总开关**（0.7⇒SPEAK 13.0%，开口延迟≈1/占比）。★**2cj 块节拍**：官方 1000ms；用 0.5⇒2.03Hz、SPEAK 52~85%。★**2cj-2 `force_listen` 是死闸**。
- ★**2ce 音色忽高忽低＝云端确定性文本漂移**（落盘 vs 直出逐样本一致⇒软件零后处理；兜底句 F0 低 8.5%）⇒**换 TTS 档位无效**。★2cf flash→plus 已换但不治本。
- ★**2ck-2 答非所问先查 prompt 主宾错位**。★**2ck-4 模型自身裁决仅 85~136ms**，大头是「等它开口」6.8~10.9s。
- ★**2cd 哑火＝LISTEN 偏置**（非闸门吞）。★**2ct-2/2ck-3 死配置两例**（`asr_trailing_silence`／`force_listen_count`），指纹＝**埋点实测值与 config 对不上**。
- 非热点：2bt 三方 A/B（云端 0.8228>本地 0.7016）｜2br/2bn/2bl/2bb 音色四判据（`hf_restore.py` 未接线＝最高 ROI）｜2cp 音频被我们丢了（`native_audio=false`）。

## 3. 未修项
- 20~29s 聋期（先建 video 再切 audio⇒Comni 重启丢话）；音色回落原厂签名音。
- ★`hf_restore.py` 未接线（EQ1，LTAS 6.53→1.40）；★**通话 TTS 输出未流式**（整句生成→出声最长 22s）＝§2ch 真解法。
- 音频碎片化＋后半段丢弃；开场白被丢（`prepared` 稳定期 3.0s）。
- `<think>` 残渣致断句无句末标点⇒被 frag 清（09-23 吞 1 句）⇒建议**引擎层删标签**。
- 兜底句 F0：flash 252.4／plus 232.7Hz⇒「变声感」主源之一。barge-in `_barge_min_rms` 已 0.07→0.03 待观效。
- 帧率改 1.0 后上层闸门阈值需重标（12s/8s/12s 都按 2Hz 调）；lps 回 1.0 若再哑火**只回调它**（1.0→0.85→0.7）。
- ★**双工路径零 3D 表情/动作下发**：`VRC_EMOTION` 只在 `dialogue.py:411`（桌宠档）与 `webapp.py:1598`（前端按钮）⇒**双工通话时 3D 角色是静止的**。

## 4. 架构结论（09-24 定型）
- **双工大脑参数**：MiniCPM-o 4.5 = 9B（主干 Qwen3-8B），我们跑 Q4_K_M INT4（11GB、212 tok/s）。同代：Qwen3-Omni 30B-A3B／GLM-4-Voice 9B／Kimi-Audio 7B／Step-Audio 130B／LLaMA-Omni 8B／Moshi 7B(Helium, 4096 ctx)。
- **三条公开评测硬结论**：①SpeechRole(arXiv 2508.02013)：级联与端到端在「互动能力／角色保真」**相当**，差异主要来自底层文本 LLM；开源端到端多维差距大。②Context Trap(IWSDS 2026)：端到端音频 LM 多轮明显劣于模块化，**根因＝上下文维护与话题追踪**，非组件质量。③DSB-IFEval(2026-09)：全双工模型在「规则只由人设暗示、不明写」时指令遵循掉 **9.7%**(F-Actor)／4.5%(PersonaPlex)；MiniCPM-o 属「内容守人设、**floor 行为不随指令变**」那一簇。
- ⇒ **Q4 8B 不足以稳定承载人设**；先降级联拿人格与记忆，双工退为「轮转感知层」。
'''

data = NEW.replace('\n', '\r\n').encode('utf-8')
with open(MP, 'wb') as f:
    f.write(data)
print('新 MEMORY.md %d bytes（上限 12000）' % len(data))
assert len(data) <= 12000, '仍然超限'
b = open(MP, 'rb').read()
print('校验 纯CRLF=%s' % (b.count(b'\r\n') == b.count(b'\n')))
