# -*- coding: utf-8 -*-
"""MEMORY.md 第二轮瘦身：压到 ~20KB。全文已归档于 REFERENCE §24。"""
import io, time, shutil

MEM = r'G:\ai对话\.workbuddy\memory\MEMORY.md'
ts = time.strftime('%Y%m%d_%H%M%S')
shutil.copy2(MEM, MEM + '.bak_v2_' + ts)

NEW = r"""# 项目长期记忆（AI 桌面伴侣 / G:\ai对话\11）
> **触发词 → 判据**索引。详情 → `REFERENCE.md`（§16 显存｜§17 补丁器｜§18 VoxCPM2｜§19 通话重设计｜§20 克隆选型｜§21 素材质检｜§22 旧文存档｜§23 声码器缓存｜§24 **音色带宽·消融·真双工 + 本文件历次全文**）；日更 `YYYY-MM-DD.md`；设计 `11/docs/`。
> ⚠️ **09-21 晚定论**：旧 §2s「16k 素材链导致糊」**已推翻**；音色/清晰度读 §2s/§2ac/§2ad/§2ah；**真双工是硬约束**读 §2ai。

## 0. 心法
- **先问用户「屏幕上显示了什么字」，grep 那句原文反查分支**（最快收敛）。
- 分层判据：①设备"死的"?（§2a）→ ②电平 → ③模型裁决偏置 → ④被哪道闸吃掉（§2h）。
- **先看「模型有没有产出」，再看「被哪道闸吃掉」**：`is_listen` 收尾 `len>=4`（通话放宽 2）；`_is_truncated_speech`（无句末标点且 `len<8`）；与 `length_penalty` 耦合 ⇒ 沉默常是叠加。
- 🔑 **静默容错＝把"取不到"变成"没有"**：`except: continue/return` 同病 ⇒ 取不到必须返回 `None` 交上层。**`log`+`except` 吞错是本项目最大一类隐性 bug**。
- 🔑 「前端零痕迹+后端零痕迹」→ 先查桥版本（`window.Backend &&` 短路）；留痕要双通道（桥+直连 `window.pywebview.api.xxx`）；工具 `tools/check_web_bridge.js`。
- 🔑 判死代码要**全仓搜**（含 `src/companion/`、`src/ui/`）。🔑 **选型先查「项目里是否已造好」**（CosyVoice2 就是现成的，§2o）。🔑 **测"效果问题"前先问：测试台与 app 同构吗？**（§2af 的"结巴"完全是旧测试台自造）。🔑 **判"像不像某人的音色"必须先建刻度、换算成区间占比**，裸绝对值无意义（§2ah）。
- LISTEN 占比 >90%(+ASR)=哑火／<70%=多话／健康 8~15%。「GPU 利用率低+延迟高」⇒ **她在等不是在算**（`frame.dt≈推帧间隔`）。「她变笨了」先比两档输入量（通话 `prefill 24~30` vs 桌宠 `476~552`）；**强制输出+零输入=复读**。
- 打断类先问「她这句能响多久」（中位 7 字≈2.7s）。⚠️ **按「帧」计的参数换档后秒数会变，改前 ×该档帧间隔**。
- **旋钮铁律**：协议只有 `force_listen`、**无 force_speak** ⇒ 只调 `listen_prob_scale`（越大越倾向 listen，默认 1.0，需重连）+ 哑火看门狗；`length_penalty` **<1.0 才鼓励更早结束**。

## 0b. 通话全链路追踪器
- `tools/analyze_call_trace.py [文件]`（不带参=最新）；埋点 `src/llm/call_trace.py` → `data/call_trace_*.jsonl`；**改埋点须重启 companion**。
- 链：`mic`(电平/`dm`死流) → `frame`(pk/silence/voice/q/fl/hold/dt) → `model`(1Hz) → `speak_*` → `tts_recv/tts_pick/tts_drop/tts_interrupt` → `tts_begin/first_audio`；锚 `sv/sl/se`（弱麦 `sv` 恒空 ⇒ 靠 `sl`/`asr`）。

## 1. 改代码红线
- 入口接线在 **`11/main.py`（src/ 之外）** + `launch_companion.py`/`run_webui.py`。
- **用 Python 脚本写文件**（Edit 遇多行/双引号易静默失败），每处断言 `count==1`；改前 `cp` `_bak_*`。⚠️ 同名串锚点 count=2 ⇒ 用行号级替换。
- 换行逐文件实测：**CRLF**=`config.yaml`/`main.py`/`local_omni_duplex.py`/`webapp.py`/**`.workbuddy/memory/MEMORY.md`**，其余 LF。读 bytes→判 `b'\r\n'`→归 LF→写回 `replace('\n',nl)`。⚠️ 写含 `\r\n` 的串会翻成 `\r\r\n`（已三踩，**PyYAML 仍能解析**）⇒ 铁律写前写后 `assert count('\r\r')==0`。
- 🔴 改 `config.yaml` 后用**真解析器**验证（缩进错→`mapping values are not allowed here`）；`py_compile`/「键存在」抓不到。
- 🔴 **冒烟三层**：①YAML 真解析 ②被改模块用项目解释器 import ③`load_config()` 跑通。隔离环境缺 `yaml/numpy/PIL/websockets/pytest` 的大面积失败＝环境噪音。
- ⚠️ 带 `sys.exit(0)` 的自跑测试会让 pytest 整目录 INTERNALERROR ⇒ 逐文件跑。判「是否我改出的回归」硬判据：该测试是否 import 我本轮改动的模块 + mtime。
- 基线 `pre-refactor-20260910`；git 不含 live2d/rig。**功能已成熟，AI 本职是「接入/接线」非重写。**

### 1b. 环境
- bash 工具链 `dirname`/`ls`/`head`/`grep`/`tail` 全 not found ⇒ **一律纯 Python**；`rm` 报错但退出码 0 且不删 ⇒ 用 `os.remove`。内联 `-Command`/`Get-` 被安全策略拒 ⇒ 先 Write 成 .py 再跑；`wmic` 已移除。
- 🔑 沙箱注入 `http_proxy=127.0.0.1:52377` 会拦截 127.0.0.1 返假 502；真实环境无代理 ⇒ 探测脚本必须 `ProxyHandler({})`；**见 502 先怀疑自己的代理**。
- 工具起的后台进程会被回收；长驻服务交给项目启动器（§2o）。
- 🔑 managed py3.13.12 **无 numpy** ⇒ 数值/音频用 **`H:\daniya_voice_tools\client_venv\Scripts\python.exe`**（numpy/torch/soundfile/librosa）或项目 `…\Python\Python312\python.exe`；**CosyVoice 侧用 `11\cosyvoice_venv`**。`Config`：`load_config(path)` 需传路径；`cfg.ns` 是属性；无 `as_dict`。

## 2. 问题族谱（全文 REFERENCE §22/§24）
### A 组：已修（只留判据）
- **2a 麦克风死流**：1 秒 `np.unique` 峰值 **≤3 值**（不数高低）。`dead`→修设备（调 gain 无效）；`quiet`→调增益。
- **2b/2c 不理人↔抢话**：同一旋钮两头，一格一格挪（SPEAK 4%→22%）；前缀优先级 > 提示词文件；取证 `logs/engine/worker.log` 逐帧 `LISTEN/SPEAK`。
- **2d 说了没声**：`interrupt()` 的 `_interrupted` 被当闩锁 ⇒ 白吞紧接那句；改时间窗口 `_interrupt_until`，复位点＝接受任务时。
- **2e 复读三源**：前缀引号例句／提示词字面「他说什么你接什么」／专属文件分支不注入 background（**专属文件反而更空**）。
- **2f 提示词副本化+打断锚点**：通话档={角色}+{通话提示词}+{上下文}（曾 `parts=[_custom]` 跳过人设）；打断窗口 `_speak_end_ts=max(2.5,len*0.22+1.2)`+1.0s。
- **2g 通话延迟六段（★唯一口径）**：中位 **4.6s** = ⓪端点 1.53(33%)／①0.39／②0.93／③0.01／④0.44／⑤1.16(25%) ⇒ **算力仅 9%，别升级显卡；58% 在"等"**。
- **2h 静音判据「精确=0」= 延迟高+话多+没主张的总根因**：真实底噪 pk≈1e-4 被当"有人说话"。修法：复用 `_mic_voice`(RMS>0.018) + 静音帧 `force_listen=True` + token 40→24 + `length_penalty`→0.9；⚠️ 接线三处缺一不生效。
- **2k 拉不起来**：三处日志同时零痕迹 ⇒ WebView2 加载过期桥；修 `ensureCallBridge()` 自愈 + `dcLog()` 双通道。
- **2n 长 script 块顶层**：`ReferenceError` 根因不是 TDZ，是同块顶层抛异常 ⇒ 整块中止；改惰性 getter+try/catch。

### B 组：进行中 / 决策
- **2m 显存（RTX5080 16GB）**：别信"关壁纸能省"；唯一静态杠杆=`ctx_size`(8K→4K≈省 2.4GB)。三机制默认全关：`vram_profile.py`（白名单仅 `ctx_size`/`n_gpu_layers`/`vision_backend`，须在拉起 worker 前写 `config.json`）｜视觉开关（差 1178.4MB）｜`comni_source_patch.py`（省≈2028MB + 免重启→2.3s）。⚠️ 三档都不改 `n_gpu_layers` ⇒ 恰是最划算杠杆。**音色侧新方案（VC/超分）都要先过这道显存账**。
- **2o 本地/云端 TTS 现状**：🔴 VoxCPM2 在 16GB 不可用（`0xC0000005`，还拖崩 Comni，详见 §18）；只认 `prompt_wav_path`+`prompt_text` 成对，漏转⇒抛错被吞⇒HTTP 200 空响应体。⭐ **CosyVoice2 全套已在项目里就绪**（`src/voice/cosyvoice_server.py`，`POST /tts`，输出 **24kHz**，端口 9883；另有 `cosyvoice_venv`/`cosyvoice_repo`/`cosyvoice_models`）⇒ 启用≈改一行 `engine`+UI 切档。🔑 云端档=`mode=clone`+`clone.sub=cloud`+角色 `engine=dashscope_cosy`。⚠️ 保存会 dump 整个 `config.yaml` ⇒ 只用语义 diff。服务生命周期归软件管。
- **2p 通话人设「三份割裂」（已设计未实施）**：人设在 `characters[]`/`prompts/call_profiles/*`/`danya_*.txt` 三处互不相干、无通道。🔴 `save_character` 静默丢 `call_instructions` ⇒ UI 编辑一次永久丢通话红线（**建议优先修**）。设计：复用现有字段零迁移→`CallPersona` JSON；背景「只出不进」只抽 `never_raise[]`；接入 `proactive.py:370` 按 `char_id` 三级解析。目标 4.6s→2.0s。备份 `backup_persona_20260921_070523/`。
- **2r 原生音频开关**：启用=`duplex_native_audio:true`(config.yaml L1455)+`duplex_comni_patch_disable_tts:false`(L1520) **两处成对**；代价通话显存 10.2→13.55GB。🔑 `use_tts` 是请求体字段 ⇒ 发 `omni_init` 传 `use_tts:true` 可绕补丁。⚠️ 复用已在跑的 server（19060）；`keep_buffer` 之类**必须配最大滞留时长**（现 2.5s）。🔑 双工 prefill 只认【音频】(`text=` 静默无效)，真判据=有没有 wav 产出（`kv_cache_length` 恒 102 不可用）。
- **2s 烘焙链路只看得见 0~8kHz（09-21 定案；旧结论「16k 素材链导致糊」已推翻）**：`extract_prompt_bundle.py` mel 参数 `sr=24000,n_fft=1920,hop=480,n_mels=80,fmax=8000`；语音 token 走 s3tokenizer 16k；说话人走 `kaldi.fbank(sample_frequency=16000)`（截止 7.6k）。**实证**：用原厂 bundle 自带 `default_ref_audio.wav` 反算 log-mel 与原厂 `prompt_mel_btc_f32.bin` 对拍 ⇒ fmax=8000 **corr 1.00000**（MAE 0.0002）／10000 → 0.95993／12000 → 0.93568；旁证 `omni.dll` 的 mel 符号**全是 `whisper_mel`**、只有一条 `/prompt_mel_btc_f32.bin` 路径串 ⇒ **prompt_mel 完全由我们装配的 bundle 决定，C++ 自己不算 mel**。⇒ **48k 录屏宽频对本地克隆零增益**，只带进 BGM/杂人声。真源＝微信 `msg\file\8月13日 (1).mp3`（44.1k/128kbps/8.03s）；与它的 16k 降采样在 0-8k **只差约 1dB** ⇒ **换真源对输出几乎无改善**（ΔF0 0.0），但微信源实测最亮（3.4-6k −8.4dB／6-8k −12.3dB）⇒ **素材裁决＝微信源**。⚠️ campplus 前置 `feat-feat.mean(dim=0)`(CMN) ⇒ **说话人 embedding 天然电平无关**；而 `prompt_mel=log(clamp(mel,1e-5))` **不归一** ⇒ **电平只从 mel 条件这条路起作用**（§2ad②）。
- **2t 素材加工铁律**：① 🔴 变调必须保共振峰（`rubberband=pitch=X:formant=preserved`）；`asetrate`/`librosa.pitch_shift` 会把共振峰一起放大⇒小黄人 ⇒ 交付前必验共振峰倍率。② ⚠️ 筛选偏置：「够响+语音带占比高」系统性挑中喊叫句⇒F0 虚高；先看全片中位。③ 判同一人用**声道长度比 r**（`参考包络(f)≈对方包络(f·r)`）：r≈1.00=同一人／r≈1.63=整体重采样；标定 自比 .900／异人 .306／录屏 .918。⚠️ 别拿同一 cache 生成的文件当「异人」标定。
- **2u 音色两层 + 烘焙机制**：音色＝LLM `voice_audio`(**弱**) ＋ 声码器 `prompt_cache.gguf`(**强**)。缓存缺失时 `omni.dll` 先试 `init_from_prompt_cache_gguf`，失败→`load_prompt_bundle_dir("tools/omni/assets/default_ref_audio")`（**CWD 相对＝`H:\Comni\_internal\resources`**，`cpp_backend.py:1306`）→ 设了 `T2W_EXPORT_CACHE_DIR` 则导出（日志 `[Token2Mel] exported prompt_cache.gguf`）。**烘焙＝隔离 live cache → 跑 llama-server → `omni_init` 不带 `voice_audio` → 等导出**。⚠️ 概率性失败（09-21 失败疑因 bundle wav 被误塞 48k，见 REFERENCE §23.3）。工具 `tools/bake_ref.py`／`H:\daniya_voice_tools\bake_cache.py`。
- **2v 微信纯源烘焙（09-21 21:31 成功，一次通过，23s）**：旧 bundle 是**微信原文件原封不动**（含 1.05s 全零首尾静音、未归一 −28.8dBFS，低 8.8dB）。修法**只修这两个客观缺陷、不做美化干预**：裁静音（0.45/0.60→0.02/0.01s）+ 归一 −20dBFS RMS；送 extract 用**原生 16k**（零重采样），bundle wav 用 24k。**审核点**：`T_token`=176（7.04s）、mel=346 帧、`F0` 必须原样保持 **238.8Hz**（变调即毁音色）。live cache md5 `e6bbe6c3`（后一轮 v2a 为 `f3c56d7099ed`，§2ae）。🔑 参考音**就地升级** `assets/voices/daniya_ref.wav`（8.034s `174c0faa` → 7.040s `8168000c`）⇒ **config.yaml 无需改动**，所有消费方自动生效。⚠️ 该裸测试台的「时长」不能当话痨判据。
- **2w 原生底色（已修·09-21）**：症状＝"换了新 cache 仍能听出 MiniCPM 原生嗓音，但和原版又不完全一样"＝**两层音色打架**：LLM 层 `voice_audio` 用 `ref_minicpm_signature.wav`(F0≈286Hz) ｜ 声码器层达妮娅 cache(F0≈235Hz) ⇒「达妮娅的壳 + 原生的底」。根因＝`proactive.duplex_local_omni_ref_audio:''`(空) ＋ `local_omni_duplex._generate_audio` **全仓只有 False 一处赋值、从无 True** ⇒ `prepare` 永不带 ref_audio ⇒ `worker.py:2094-2097` 回落 `_LANG_REF_AUDIO_PATHS["zh"]`。铁证：`[Duplex RefAudio] LLM=None, TTS=None` + `prefilling voice_audio: .../ref_minicpm_signature.wav`。已修：①config.yaml L1409 填 `assets\voices\daniya_ref.wav` ②门条件改 `(self._generate_audio or self._native_audio)`。⚠️ **推翻旧结论**：「产出 F0 +2 半音＝声码器路径特征」→ 实为 **LLM 层被 286Hz 原生音拉高**。
- **2x `prompt_cache.gguf` 内部（强判据）**：恒 5 张量＝`spk_cb`(192×f32，**唯一音色分量**) ＋ conformer/estimator 的 cnn/att cache（att 时间维 T＝参考音帧数）。**data_start 恒＝736**（header_end 719 对齐 32），`spk_cb` 在 736 处、768 字节。判「引擎是否真加载」看 `Token2Wav: using prompt_cache from` vs `prompt_cache failed, fallback to prompt_bundle from`；判「烤制是否生效」比 `spk_cb` md5 + 反推 T。实测四代：微信精修 `ee61d3e2` T=346／历史247MB `a00a7dc8` T=354／录屏332MB `285dde63` T=476／09-21 失败版 `d85d4fd0` T=316。⚠️ `d85d4fd0` 与 Comni 安装目录 bundle 的 `spk_f32.bin` **完全相同＝MiniCPM 原生说话人向量**（可当「原生」锚点）。
- **2y `duplex_prepare` 丢参**：`cpp_backend.py:511-520` 收了 `prompt_wav_path` 却**只下发 `voice_audio`** ⇒ `worker.py:2049` 注释「TTS ref audio → prompt_wav_path（初始化 vocoder）」在 C++ 路径上是空头承诺（omni.dll 无此串）⇒ **声码器音色只能靠 cache / bundle**。
- **2z 修复已实机验证（09-21 22:07）**：standalone worker（`worker.py --port 22733 --gpu-id 0 --worker-index 0`）＋ `ws://127.0.0.1:<p>/ws/duplex?session_id=adx_<ts>`（**adx＝音频档 media_type=1**）两臂对照，**唯一变量＝prepare 有没有传参考音**。日志 A 臂 `[Duplex RefAudio] LLM=<Temp>\daniya_ref_xxxx.wav, TTS=同上`、B 臂 `None`。客观：F0 A=237.6/244.9（两次复现）、B=260.9、参考 238.8 ⇒ **ΔF0 −1.2/+6.1 vs +22.1**；`cos↔原生` A=0.297/0.305 **<** B=0.341 ⇒ 修复真把原生底色去掉了。⚠️ `cos↔参考` 两臂只差 0.006（两臂**共用同一 cache**）⇒ **判 LLM 层只能靠 F0/韵律，不能只看说话人余弦**（口径务必延续）。
- **2aa 双工音频下行有【两条】通道（漏一条＝「有文本没音频」）**：①`{"type":"result", audio_data}`；②`{"type":"audio_only", audio_data}`——`worker.py:2181-2214` 起 **100ms 轮询**读 C++ T2W 的 `wav_N.wav` 再下发。**原生 TTS 档只走②**（日志 `[WAV poll] sent audio_only`）。自建测试台必须两条都收。⚠️ 此路径下 `SPEAK` 行 `llm=0 tts=0 t2w=0 tokens=0 kv=0` 是**正常的**（工作量在 C++ 侧）；真判据＝有没有 `T2W线程: wav_N.wav | x.xxs audio | RTF=`。✅ 采集侧**不是**丢片源：`_collect_wav_output_nowait`(L1860) 是按文件名去重的**增量**收集。
- **2ab 自定义音色链路现状（「UI 与接收端能否统一」的答案）**：地基已在，**原生档没接上**。已通：`voice_profiles`（含 engine/reference_audio/reference_text/api_url/voice_id…）＋ `characters[].voice_profile` 绑定 ＋ `config.get_active_voice_config()`（**音色档优先**、返回深拷贝防串味）＋ UI `ui_redesign_voice.html` ＋ `set_reference_audio()`(webapp.py:8927，**同时写** `characters[].voice.reference_audio` 与 `proactive.duplex_local_omni_ref_audio`，目前**唯一**已通的 UI→原生档通路)。未通：①`_VOICE_PROFILE_ENGINES`(webapp.py:2773) **白名单无 Comni 原生档** ⇒ UI 表达不出「用我的音色走原生克隆」；②原生档读**全局单键** `proactive.duplex_local_omni_ref_audio`（proactive.py:4633→3074），`save/assign_voice_profile` 都不碰它 ⇒ **按角色切换音色在原生档上是断的**；③原生档音色两层（LLM 每会话传参考音 ＋ 声码器 cache 烤死）⇒ **换音色必须重烤 cache**（≈25s），这是它与 cosyvoice/dashscope「即插即用」的本质差别。
- **2ac 引擎输出带宽天花板 ≈8kHz（清晰度总根因）**：条件带宽只到 8k ⇒ **产出 8-12k 只有模型先验**。实测产出 8-12k = **−22.2dB**（真源 −7.8dB）、f99 **5400Hz**（真源 10168Hz）；连 `ref_minicpm_signature.wav` 自身 8-12k = **−76dB**、f99 6536 ⇒ **整族是窄带（≈16k 采样）模型**。🔴 **在这颗引擎上"和原素材一样清晰"做不到**；换参考音、换 48kHz 源都无济于事。⭐ **唯一有效手段＝输出侧补偿** `tools/hf_restore.py`（真源 LTAS→STFT 增益掩码；EQ2 档另加 4-8k→8-12k 谐波折叠 SBR）：8-12k −22.2→**−9.2dB**、f99 5400→**10059**、`cos↔真源` 0.6958→**0.7008**、该带谱平坦度 0.8988→0.6621（≈真源 0.6688）。⚠️ 补出来的是**模型先验高频，不是本人真实齿音/气声**。
- **2ad 参考音选型三条硬判据（比"挑最清晰的"重要得多）**：① **真源先找对**（微信 44.1k mp3 才是原档），但 0-8k 几乎相同 ⇒ **不是解法，别再花时间**。② **电平是绝对尺度**：`prompt_mel=log(clamp(mel,1e-5))`、链路不归一 ⇒ 参考音绝对电平直接进条件。标定靶＝原厂 `default_ref_audio` 浊音帧 mel 中位均值 **−3.213**（≈RMS −17.0dBFS）；历史线上版 −5.678（**低 6dB**）、16k 版 −3.407、标定后 −3.232。求增益 `g=exp(目标−实测)`（`tools/prep_ref_v2.py` 已实现）。③ 🔴 **语域会被复制**：拿 F0 269.7Hz 的台词做参考→产出 F0 **266.7**（+26.7Hz，变激动）；拿 F0 240Hz 平静片段→产出 **240.0（Δ 0.0）** ⇒ **参考音要挑"你想要的说话语气"**。④ 实测 `cos↔真源` 天花板约 **0.70**（参考音自比 1.0000）⇒ 0.70＝"同一个人但不够贴合"，是本引擎的身份保真上限。
- **2ae 可复用工具与回滚点（`G:\ai对话\11\tools\`）**：`bake_ref.py`｜`prep_ref_v2.py`｜`prep_wechat_final.py`+`bake_wechat_final.py`｜`run_v2.py`｜`check_v2.py`（同一把尺子+频段剖面）｜`hf_restore.py`｜`make_listening_set_v2/_v3/_v5`｜`_probe_mel_fmax.py`（**fmax 判定，最有价值**）｜`smooth_speech.py`（§2ag）｜`gen_sample_app.py`（§2af 同构测试台）｜`_calib_spk.py`／`_run_mix_ablation.py`／`_mix_forensics.py`／`_band_attribution.py`／`_hf_whodunit.py`（§2ah/§2ai）。live 收尾：cache=v2a（242,251,232B md5 `f3c56d70`）；`daniya_ref.wav`＝24k 真源标定版（md5 `9e2a7206`）。回滚点：`prompt_cache.gguf.bak_before_v2a_*`、`prompt_cache.gguf.V2B_*.keep`、`daniya_ref.wav.bak_16k_*`。
- **2af 结巴/卡顿（★不是能力上限，是测试台未与 app 同构）**：🔴 旧测试台 `audio_chunk` **不发 `force_listen`**、尾部一次喂 12s 纯静音、还是"发一收一"乒乓推流 ⇒ 模型被允许**对静音持续开口**，自嗨成一串短感叹句（日志实见 `哇哦！真好玩呢。哈哈哈`），每片收尾重启声码器 ⇒ **`audio_bos(chunk_idx=0)` 达 40 次/回合** ⇒ 语音被切成中位 0.13~0.15s 碎块。✅ **修法**（`gen_sample_app.py`，1:1 复刻 `local_omni_duplex.py`）：静音帧 `force_listen=True`（判据同 app：`EMA(rms,α=0.35)>0.018`）＋**定拍推流**（通话档 0.5s/帧，不等回复）。修后同一 cache/参考音：**audio_bos(chunk0) 40→1、浊音中位 0.152→0.688s、浊音占比 49%→70%、最长静音 1.54→0.78s**，文本自嗨→连贯回答，**`cos↔参考` 0.6958→0.7103（全线最高）**。🔑 **判据**：数日志 `在 prefill 前添加 audio_bos (chunk_idx=0)` 的次数＝独立 TTS 轮数；**>10 即有碎句风险**。📏 **真机标尺**（4 次真实通话 ai_audio，自适应阈值 RMS−25dB）：浊音占比 **63~74%**、≥0.15s 静音 **0.67~1.12 个/秒**、最长静音 **0.475~1.51s**、浊音中位 **0.135~0.48s**；天然参考音 78%/0.43/s/0.74s。⚠️ 真机 T2W **块边界连续**（跳变比中位 0.045）⇒ 块拼接不是卡顿源；块时长中位恒 1.000s。⚠️ 音频到达速率 **1.00 音频秒/墙钟秒** ⇒ 无播放欠载。⚠️ **更正 §2v**："停不下来/复读＝裸测试台通用行为"**部分错**——那正是缺 `force_listen` 造成的，同构化后模型会正常收尾。
- **2ag 输出侧停顿规整（`tools/smooth_speech.py`）**：只**删静音样本 + 拼接处 4ms 淡化**，一个浊音采样都不动 ⇒ **中位 F0 逐点不变（Δ=0.0）、cos 仅噪声级波动**（±0.006）。双阈值滞回（进 −35dB/出 −28dB，相对自身 P90 电平），keep=(0.10,0.16,0.28)s。效果（A_FIXED）：23.44→16.27s、浊音占比 49%→69%、最长静音 1.54→0.355s。**这是"让耳朵能判音色"的下游处理，不等于修好引擎。**
- **2ah 「像原生音色混合」判定＝模型固有为主（已实测，勿重查）**：🔑 **先建刻度**：campplus 同人上限（同录音劈两半）= **0.7280**／异人下限（达妮娅 vs 原生签名）= **0.1947**／同录音只降采样 = 0.9957（`_calib_spk.py`）。克隆产出 0.7103 ⇒ 区间占比 **96.7%** ⇒ **说话人身份已基本完全迁移**，"混合"不是身份串味。🔑 **2×2 消融矩阵**（`_run_mix_ablation.py`，`ABL_REF=0/1`）：
  | 缓存 | 传参考音 | cos↔达妮娅 | cos↔原生 |
  |---|---|---|---|
  | 达妮娅 | 传 | **0.7103** | 0.3054 |
  | 达妮娅 | 不传 | 0.6525 | 0.3497 |
  | 原生 | 传 | 0.2257 | 0.6653 |
  | 原生 | 不传 | 0.1347 | 0.7679 |
  ⇒ **声码器缓存是音色主控**（换原生缓存→塌到异人地板）；**参考音真实但次控**（同缓存 +0.06~+0.09）。两层都生效、方向都对，**没有"原生通道偷偷覆盖"的证据**。⚠️ 早年用 `B_NATIVE`（不传参考音）当"原生对照"是**污染对照**（其 cache 当时已是达妮娅烤的）。🔑 **判"是不是混合体"用线性混合拟合度**：最优 α_原生≈0.8 但**拟合度仅 0.67~0.73**（真混合应 >0.9）⇒ 输出**不是线性混合，是"以达妮娅为底的第三种音色"**。🔑 **原生成的物理来源＝模型对 8kHz 以上完全失明**：①`core/processors/base.py:271` `librosa.load(audio_path, sr=16000, mono=True)` ⇒ 参考音**进模型前无条件降采样，永久丢 8-16k**；②声码器条件带宽 **fmax=8000**（§2s）⇒ 8-16kHz **只能由声码器先验生成**。实测（`_band_attribution.py`）：真源 8-12k 相对本带 **−6.6dB**、8k+ 能量占比 **5.27%**；全部产出 **−15.8~−24.6dB / 0.10~0.22%** ⇒ 高频比真人低 **9~18dB**、占比只有真人 **1/20~1/50**。另两成：声码器过平滑（6-8k 能量比真源低 ~3 倍）；**韵律/断句/呼吸完全由 LLM 决定，参考音不传递韵律**。⚠️ **唯一配置层残留**（建议清理，预期收益小）：`apps/server/config.json` → `audio.ref_audio_path="assets/ref_audio/ref_minicpm_signature.wav"`，被 `cpp_backend.py:1456` 在**每次 omni_init** 当 `voice_audio` 下发；`cpp_backend.py:1616` 在模式切换时（通话 2→1 档）必走完整重初始（日志实锤 22:40:54 先原生、22:41:10 才 prefilling 我们的）。修法优先：①启动 worker 加 `--ref-audio-path`（worker.py:2398 优先于 cfg）②改 config.json（⚠️ 会把**所有角色**默认音色一起带偏）。复现链：`_calib_spk.py`→`_run_mix_ablation.py`→`_mix_forensics.py`→`_band_attribution.py`→`_make_band_pack.py`。⚠️ 消融会临时替换 live 缓存，脚本已带备份+finally 还原+md5 校验。
- **2ai 真双工 ⚔ 外挂 TTS：结构性互斥（09-21 晚实测定论；用户已确认云端/本地外挂同样丢双工）**：🔴 **换引擎救不了，两条独立原因**：① **时钟不同源**——原生路径 `local_omni_duplex.py:1980/2098` 把引擎逐帧音频攒进 `_turn_audio`，`:1934` 回合边界取出随 `on_speak(text,pcm,sr)` 下传，下游见 pcm **直接播跳合成**（`:233`/`:1978`）＝与 `is_listen` **同一时钟**；外挂路径 `:1981-1983` 丢弃 `audio_data` 只交文本、`:2022` 连语音一起丢 ⇒ 听到的是回合后**另一时钟**重合成。打断判据 `_speak_end_ts` 全项目**只有 L1947 一处赋值**＝按字数估算 `max(2.5, len*0.22+1.2)`，**两条路径都一样**；原生手里本有 pcm 真实时长却没用 ⇒ **顺手可修**，外挂则无从实测。② **速率喂不饱**——环路消耗 1.00 音频秒/墙钟秒；本机 CosyVoice2 实测 **RTF 0.90~1.35（无余量）、流式首包 2.51s、非流式 7.55s**（`cosy_probe.log`）；云端首包 3.1s（网络段）；原生首音频百毫秒级。✅ **开关**：`duplex_native_audio:true`（L1455）＝**唯一真双工路径**；`false`（`native_audio` 默认 False）＝外挂档。🔑 **>8k 归属取证**（`_hf_whodunit.py`）：`ref_minicpm_signature.wav` 的 8-12kHz ＝ **−62.5dB**（它本身是 16k，>8k 本为空）⇒ 输出里的 >8k **不可能来自参考音**，只能来自**声码器解码器先验**。克隆未补偿 **−15.3dB**／EQ1 **−4.2dB**／真源 **−4.5dB** ⇒ EQ 只对齐**电平与形状**（差 0.76dB、相关 0.979），**改不了纹理来源**。🎯 **⇒ VC（RVC/Seed-VC）不再是"多余的一层"**，它是唯一能在**不碰双工时钟**前提下改写 >8k 纹理的杠杆；阻塞项＝显存（~14.6/16GB，需先上 `n_gpu_layers`/4K）＋语料（RVC 需 10min 干净人声，现有仅 8s ⇒ 走 Seed-VC 零样本 1~30s／~300ms）。🔴 **已修正 `docs/RVC变声器接入可行性评估.md`**（新增 §0.0，备份 `.bak_hfcorr_20260921_232108`）：原结论"RVC 是多余一层"的前提**仅对 ≤8k 成立**。耳判包 `data/native_audio_test/final_sample/聆听_v5_高频归属/`。⚠️ CosyVoice2 附带坑：参考文本必须与参考音**同句**，否则 `WARNING ... too short than prompt text` 且**连续乱说**（65 字产 59.2s、零停顿）。

## 3. 未修项
- **接通 20~29s 聋期**：会话先建 `media_type=2` 再切 audio → Comni 重启(~18s)，用户的话堆在服务端队列、就绪瞬间被丢。修向：一开始就按 audio 态建会话。
- **克隆参考音仍污染上下文**：服务端 `ref_audio=ref_minicpm_signature.wav`、`voice_clone_prompt`=我们的系统提示词（文本与参考音不匹配）。云端 TTS 首包 3.1s 是网络硬门槛。`_duplex_frag_buf` 第二层攒句：无句末标点且 <200 字就 return 不出声。
- **【云端首抓手】** `duplex_tts_stream:true` 已开，但日志仍有 `TTS 丢弃过期任务(已等待 6.08s)` ⇒ 流式疑似未生效、静默退化回整句 HTTP（与 §2o 同族）。查 `duplex_tts_stream`→`synthesize_stream` 接线。
- **`_speak_end_ts` 可用真实 pcm 时长**（§2ai①）：零风险改动，待拍板。
- **VC／超分路线待拍板**（§2ai）：先量 Seed-VC 本机延迟与显存再决定投入。
"""

new = NEW.replace('\n', '\r\n')
assert '\r\r' not in new
assert new.count('\r') == new.count('\n')
open(MEM, 'wb').write(new.encode('utf-8'))
print('MEMORY.md -> %d bytes' % len(new.encode('utf-8')))
for k in ['2af', '2ag', '2ah', '2ai', '2ac']:
    print('  section', k, 'x%d' % new.count('**' + k))
