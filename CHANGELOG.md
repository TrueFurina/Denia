# 变更记录

> 项目：达妮娅 AI 桌面伴侣（`D:\Denia`）
> 起点：2026-10-17 19:39 建立版本控制（此前无 git）
> 对应诊断报告：`项目诊断报告_2026-10-17.md`

**维护约定**：每次提交后在这里追加一行速览，并在下面写一条明细（问题 → 改动 → 验证）。
明细只写"为什么"和"验证证据"，不抄 diff。踩过的坑要写进去，那是最贵的部分。

---

## 速览

| commit | 时间 | 类型 | 一句话 | 验证 |
|---|---|---|---|---|
| `b775a69` | 07:5x | fix | 第五处静默失效：唤醒词整词语法让她名字被静默跳过 | _qc_wake_word 闭环 + 10 组单测 + 6 刀变异；gate 44→46 |
| `efad34e` | 08:3x | test | 唤醒后事件流闭环验证：WAKE_WORD→USER_TEXT 双引擎覆盖 + 3 刀变异（原测试只模 vosk 回退漏了 sherpa 主路径，且摘 vosk 桩让 _run 局部绑定落到真 vosk 假阴性） | 双场景单测 + 3 刀变异全红；gate 46→48 |
| `5bffc68` | 19:39 | chore | 建立版本控制基线（此项目此前无 git） | 646 对象 / 11.97 MiB |
| `e8e1d03` | 19:40 | docs | 全项目诊断报告 | — |
| `57d6101` | 19:46 | security | 清空发布模板漏网的 API Key + 打包凭据硬闸 | 变异：塞假 Key → exit 1 |
| `ecd60a3` | 19:46 | security | `run_command` 白名单 fail-closed | 9 项用例 + 变异 |
| `c90d56b` | 19:46 | security | OneBot 反向 WS 只听回环 + 入站鉴权 | 6 项用例 + 变异 |
| `a90ccf2` | 19:48 | fix | 补 `start` 内建命令（上一提交的回归） | 9 项用例 |
| `2dabbf1` | 19:54 | refactor | `webapp` 抽出 25 个纯函数；修 2 个 NameError | AST 逐字比对 25/25 |
| `972aef9` | 20:00 | refactor | 15 个模块级全局收敛为 `STATE` | 跨模块同一对象 |
| `08cabbd` | 20:08 | refactor | 桌宠域拆 `PetAPI` mixin；修 3 处引用错误 | 21 方法逐字一致 |
| `aab4c70` | 20:13 | refactor | 窗口域拆 `main_window` + `WindowAPI` | 9 函数 + 17 方法逐字一致 |
| `5fa19d8` | 20:16 | test | 通话链路冒烟 22 项 | 变异 3 项 FAIL |
| `c566bc9` | 20:19 | chore | 静默异常扫描器 + 口径纠错 | `--strict` 行为验证 |
| `add925a` | 20:26 | fix | 去掉写死盘符与 locale 编码依赖 | import 实测通过 |
| `3fb4a71` | 20:26 | chore | `live2d/rig` 由 gitlink 改为真实跟踪 | 变异：删文件 → exit 1 |
| `1207233` | 20:31 | chore | 打包闸双向化（也查"必须带的"） | 三组假包实测 |
| `fa86a4c`* | 12:10 | fix | 桌宠后端默认值改回 auto（默认拿到真透明） | doctor PASS + 变异 3 项 FAIL |
| `fa86a4c`* | 12:2x | fix | 依赖档位参数名骗人：`upto="optional"` 静默等于 `important` | [22]~[25] 新用例 + 双变异被抓 |
| `5dbc64c` | 13:2x | fix | 深度质检收口入库（P0~P3 成果 + 修复 HEAD 引用未跟踪模块） | 27 项全过 / 0 跳过；双变异被抓 |
| `422f140` | 10:4x | fix | RPC 401 残留 body 污染 keep-alive（10053 假绿） | 720 次长连接 0 失败 |
| `2035457` | 10:5x | security | 敏感配置 DPAPI 加密落盘（C9-1） | 20 断言 + 3 刀变异 |
| `c216151` | 11:0x | perf | phash 自实现替换 ImageHash，甩 scipy（C9-3） | 208 图 bit 级对照；472→370MB |
| `c010d83` | 11:1x | feat | wheelhouse 改 pip wheel，真离线化收口（C9-4） | 63 whl / 0 sdist；离线装全通 |
| `8f28cc0` | 11:2x | docs | C2 拍板（A 路线）+ 凭据日志卫生门禁 | 规划落档 |
| `b87a8c1` | 11:5x | feat | C2-A 落地：NSIS 安装器 + 升级保留配置 | 见下方明细 |
| `e624b6e` | 13:5x | feat | 代码签名管线 `--sign`（C10）：出包即签 + 签后核验 | 14 断言 + 2 刀变异；真机自签 E2E |
| `3bca8ec` | 01:4x | fix | **第四处静默失效**：`mic_enabled=false` → 唤醒/语音全废（注释里早有「B2 修复」，同一个坑踩第二次）→ 打开开关 + doctor 收口提示 | 7 刀变异全红；门禁 44 项全过 |
| `5d2a6ef` | 01:2x | feat | 按类清扫「配置指向不存在的东西」：路径白名单 + 角色引擎 + 本机服务三层判级（可选功能/端口默认只提示） | 12 组断言 + 8 刀变异全红；门禁 42→44 项 |
| `ab9ba0d` | 01:0x | fix | **她也听不见**：`Model(lang="zh-cn")` 是无效语言代码（vosk 只认 cn）+ `model_path` 指向不存在的目录把自动下载堵死 | 闭环念一句→识别一句全对；6 刀变异全红；门禁 40→42 项 |
| `3563e30` | 00:3x | fix | **她一个字都说不出来**：角色 TTS engine 为空 → 落到「静音后端」（不出声/不联网/零日志）→ 绑本地 pyttsx3 + doctor 新增「语音合成」判级 | 12 组断言 + 7 刀变异全红；门禁 39→40 项 |
| `858dfb6` | 21:2x | fix | **主链路从失效的阿里云百炼迁到免费源**：`${ENV}` 引用（key 不落盘）+ 渠道探活进 doctor | 2 组新测试 + 7 刀变异 + 端到端真发出回复；门禁 36→39 项 |
| `a1e2f17` | 14:0x | fix | doctor GBK 控制台不炸（符号降级 `?`） | [15] 行为测试 + 变异；真机包内复现→修复 |
| `6094d95` | 15:1x | fix | 桌面双击启动即崩：None 流 → pywebview GBK devnull → rich 炸 | io_safe 三态测试 + 变异；真机 crash 日志取证 |
| `94709d4` | 17:5x | fix | 微信通道「能收不能回」+ 回调无鉴权对外暴露 + 发消息卡死事件循环 | 6 断言 + 5 刀变异全红；门禁 28→30 项 |
| `3eb3f86` | 18:2x | fix | 同一类坑再扫三处（qzone 发布 / tts 的 sleep / heavy_apps 拉取）+ JS 闸门能染红了 | 变异 2 刀全红；门禁 30→34 项 |
| `4a4c04a` | 18:4x | fix | 设置页「打开状态文件」恒走降级（适配层漏导出后端已有的方法）+ 桥接三层闸门 | 变异 2 刀全红；门禁 34→35 项 |
| `9bb8845` | 19:0x | fix | 主链路 9 处真静默逐个定性 + 静默异常闸门接线（并修掉闸门自己"沉默"的两个洞） | 变异 3 刀全红；门禁 35→36 项 |

> **\* 夹带说明（必须留档）**：12:10 / 12:2x 两行的内容实际随 `fa86a4c`（E1 开机自启）
> 入库——13:14 的并发提交把质检工作流未提交的改动一起提交了，未按工作流拆分。
> 代码与测试主体由 `5dbc64c` 收口，**归属以本脚注为准**。
> 教训：并发会话各自只 `git add` 自己知道的文件，提交前先 `git log` 确认HEAD 没有
> 在几分钟内被别人推进过。

### 2026-10-19 · 第六处核查：唤醒后事件流闭环（听到→想→说 收口）

**问题**：第五处修完唤醒词命中后，继续验证端到端「听到→想→说」唤醒后的事件流——
`asr._run()` 是否真发 `WAKE_WORD` → 切指令识别 → 发 `USER_TEXT`（喂 LLM 那棒）。
最初的 `src/tests/test_asr_event_flow.py` 报 `USER_TEXT` 不发（`cmd_acc=0`）。
排查后**不是第 6 处静默失效，是测试桩自己漏了主路径 + 自己摘了桩**：

- `asr._run()` 内部 `from vosk import KaldiRecognizer` 是**函数内局部绑定**，而
  `wake_word.py` 是模块顶层绑定。测试在 import asr 后把 vosk 从 `sys.modules` 摘掉 →
  `_run` 的 `cmd_rec` 落到**真 vosk**、假音频喂不进 → 回退路径 `USER_TEXT` 发不出（假阴性）。
- 同时原测试只模了 vosk 回退路径，没模 **sherpa 主路径**（`engine="sherpa"` 是默认优先）。
  所以双场景一成一败才暴露桩有问题——产品代码 `asr.py` 的状态机本来就是对的。

**改动（测试侧）**：
1. 测试打完 vosk 桩后**绝不摘**（保住 `_run` 的局部绑定也指向 `FakeRec`）；
2. 改写 `test_asr_event_flow.py` 确定性覆盖双引擎：场景 A 注入假 sherpa（主路径）、
   场景 B 让 sherpa 导入失败（回退 vosk），两种都必须发 `WAKE_WORD` + `USER_TEXT`；
3. 新增 `tools/_qc_asr_event_flow_mutation.py` 变异 3 刀：
   - A 断 `wake→cmd` 模式切换（注释 `mode="cmd"`）→ 指令分支永不进、USER_TEXT 不出；
   - B1 吞 sherpa 文本（改 cmd 分支 `text = sherpa.feed(data)` 为 `text=""`）→ USER_TEXT 不出；
   - B2 吞 vosk 文本（改 `text = json.loads(cmd_rec.Result())…` 为 `text=""`）→ USER_TEXT 不出。
   B1 初版误改到"免唤醒持续识别"那处同名 `sherpa.feed`（`asr.py` 两处），靠 `"识别指令"`
   上下文唯一定位 cmd 分支才生效——**变异刀若命中多处同名代码必须用上下文唯一定位**。

**验证**：双场景单测全过 + 3 刀变异每刀都让 `USER_TEXT` 消失（有区分力）；
`tools/check_all.py` **46 → 48 项全过、0 跳过**。

**本轮洞察（可迁移）**：给被测模块打桩时，**函数内 `from X import Y` 与模块顶层 `import X`
是不同绑定时机**——摘掉 `sys.modules` 里的桩，函数内 import 会悄悄落到真模块，造成
"桩部分生效"的假阴性。打桩后别摘，或用 `importlib` 显式 reload；断言 / 变异刀若命中多处
同名代码，必须用上下文唯一定位，否则改了个没跑到的分支。

---

### 2026-10-19 · 第五处：唤醒词整词语法——她名字被静默跳过

**问题**：端到端验证「听到→想→说」链路，先卡在唤醒词。实测 `tools/_qc_wake_word.py`：
念配置里的四个唤醒词，只有「小薇」「你好小薇」能命中，**「达妮娅」「你好达妮娅」永远 None**。
查 `Gr.fst` 二进制发现：词图里有「小薇」「你好」，但没有「达妮娅」「达妮」——
而单字 达/妮/娅 都在。结论：旧 `wake_word.py` 把整词塞进 Vosk 语法约束，模型词图没这整词
就被静默跳过。更隐蔽的：即便放开语法，模型把「达妮娅」识别成「达尼亚」（娅/亚 同音），
整字比对永远配不上。所以「念她名字她不理你」是双保险地坏。

**改动**：`src/voice/wake_word.py`
1. 语法按【单字 + 同音字】（BMP 内，pypinyin 反向索引，896 字，构造 0.00s）构建，不是整词；
   `[unk]` 兜底，非唤醒语音才有出路。
2. 匹配按【拼音音节序列连续比对】（`_syllables` + `pypinyin`），娅/亚、薇/微 这类同音字也算中；
   字间空格先归一。长词优先返回最具体的别名。
3. `pypinyin` 缺失时降级为整字精确匹配（启动不崩，但同音字配不上）。

要求 pypinyin 进 `requirements.txt` 重要档。`tools/doctor.py` 新增 `check_wake_words`：空表 FAIL、
pypinyin 缺失/麦克风关/模型未就绪 WARN、并把混入的遗留 TTS 音色名（小薇等）点出来。

**验证**：闭环实测 TTS 念「达妮娅」→ 识别「鞑妮呀/达 妮 亚」→ 拼音命中「达妮娅」；
连被误听成「达尼亚」也中；负面长句「今天天气…」「小爱同学…」零误触。
`test_wake_word.py` 10 组 + `_qc_wake_mutation.py` 6 刀（整词语法/不剥空格/无同音容错/缺 unk/
列表别名/不滤空）全有区分力。`tools/check_all.py` 44 → 46 全绿；doctor 0 FAIL。

---

### 2026-10-19 · 第四处：麦克风开关关着——她根本不在采集
**来由**：「能听」那条链修到模型就绪后，顺手查最后一环：**本机有麦克风吗、
开关开着吗**。结果：16 个输入设备就绪（默认 Realtek 麦克风）、vosk 模型
已缓存，但 `voice.mic_enabled = false` → `asr.py:_run()` 开头就
"麦克风监听关闭，挂起等待（不采集不识别）"，**唤醒和语音全废**。

**这不是我猜的**：`webapp.py:10036` 的注释自己写着
"B2 修复：默认 mic_enabled=false 导致唤醒/语音全废"——同一个坑踩第二次。
而当前 `voice.mode = 'clone'`（要她的本音）却关着麦克风，**自相矛盾**，
说明这是从未被正确设置过的默认值残留，不是用户的选择。

**修复**：`config.yaml` / `config.release.yaml` 的 `mic_enabled` 改 true。
doctor 的「语音识别」新增收口：模型就绪但麦克风关着 → 降 WARN，
detail 里同时说清"模型 OK + 麦克风关着"，fix 指到设置项。
判 WARN 不判 FAIL：用户有权主动关掉"不想被监听"；FAIL 留给配置指错。

**测试**：`test_doctor_asr.py` 补 5b/5c 两组。这里又踩到一个**断言陷阱**：
第一版写成"lv != PASS 或 detail 不含麦克风字样"，逻辑写反了——
麦克风被改成"不降级"后 lv 恰好变成 PASS、detail 恰好不含麦克风，
断言反而全绿。改成**打桩强制模型就绪**（让含 `sherpa-onnx` 的路径判定存在），
断言才有确定性：mic=False 必须 WARN 且含"麦克风"，mic=True 必须 PASS 且不含。
配套变异刀 ⑥b（改回"麦克风关着也判 PASS"）从漏抓变为必红。

变异 **6 → 7 刀全红**。门禁 **44 项全过、0 跳过**；doctor 16 PASS / 2 WARN / 0 FAIL。

### 2026-10-19 · 按类清扫「配置指向不存在的东西」+ 一个只有 6 处命中的体检
**来由**：修 ASR 时发现 `model_path` 指向不存在的目录、而"非空值会把自动
下载堵死"。这跟前面三处（能想/能说/能听）完全同型——**不是功能写错，是验证
缺失**：配置看着都填了，从没被验证过。所以写个扫描器按类扫一遍，不等下一处。

**扫出来的实情**：`config.release.yaml` 只有 6 处可校验引用，其中
`proactive.local_omni_engine.model_dir = 'H:\ceshi'` 不存在——但该子树
`auto_start: false`（可选功能，指向用户自己的 Comni 目录），判红就是假红。
微信网关 2531 没起同样属于"没起可选服务"。所以判级必须分三层。

**判级口径**（和前几项统一：用户能自己解决的只黄，产品级失效/配置指错才红）：
· 路径键（白名单 `PATH_KEYS`）指向不存在 → FAIL；
· 角色 `voice.engine` 空/不认识 → FAIL（静音后端，能收不能说）；
· 挂在 `auto_start/enabled: false` 子树下的缺失 → 只提示；
· 本机服务端口没在听 → 默认只提示，`--strict-ports` 才判红
  （端口是运行环境，网关在 Docker 里没起很正常，判红会天天假红）。

**白名单制是第三次改版才定的**（前两次都在注释里留着，省得再踩）：
一版按"值里有 / 或 \\"判 → `model_path: foo` 这种相对目录名漏报；
二版未知键也查 → `label`（OCR 锚点文字）、`realtime_model`（模型 ID）、
`ws_url` 全被当本地路径，噪声淹没问题。
现版只查登记的路径键——宁可漏几个没登记的键，也不要一片红。

**测试 + 变异**：`test_config_path_refs.py` 12 组；变异 8 刀全红
（漏报 / 假红 / 端口判红 / 静音不判红 / 白名单放宽 / 相对名漏报 /
可选降级失效 / 异常被静默吞）。过程中又抓到两个覆盖盲区：
① 脚本裸跑 `ModuleNotFoundError: No module named 'src'` 而**测试全绿**
   （测试自己 insert 了 ROOT）——"测试能跑"和"脚本能跑"是两件事；
② 早先用 `globals()` 在 scan() 里动态注入收集器，`check_module_globals`
   报"walk 引用了未定义的全局 '_checked'"——静态检查抓得到就别跟它斗。

门禁 **42 → 44 项全过、0 跳过**。

### 2026-10-19 · 她也听不见：vosk 语言代码写错 + model_path 堵死自动下载
**入口**："阿里云没钱"→ 修完"能想"（LLM 迁免费源）和"能说"（TTS 落静音后端）
之后，接着问"**那她现在听得见吗**"。一查，`models/` 目录压根不存在，
而配置里写着 `model_path: models/vosk-model-small-cn-0.22`。

**两个 bug，任何一处都足以让她永远听不见**：
1. `src/voice/asr.py:_load_model` 写 `Model(lang="zh-cn")`，而 vosk 只认 `"cn"`
   → 实测报 `lang zh-cn does not exist`。也就是说代码里那条
   "model_path 留空就自动下载中文模型"的兜底**从来没通过**。
2. 配置把 `model_path` 写成一个不存在的目录 → `Model(路径)` 必抛异常，
   **非空值又把自动下载那条路堵死**。两口一堵，日志里只有一条
   ASR_INIT_FAIL，没有任何"配置指错了"的提示。

**修复**：`lang="zh-cn"` → `"cn"`；`config.yaml` / `config.release.yaml` 的
`model_path` 置空（首次监听自动下载约 42MB，落 `~/.cache/vosk/`，
第二次起本地秒开）。

**闭环实测**（`tools/_qc_asr_roundtrip.py`，念一句→识别一句，不是静态查文件）：
pyttsx3 念"你好呀，我现在能听见了" → 重采样 16k 单声道 → 走**项目自己的**
`_load_model` → vosk 识别结果 `'你好呀我现在能听见了'`，与原文完全一致。
自动下载实测 64.4s / 41.9MB，之后本地加载。

**新增 doctor 检查「语音识别」**：model_path 非空但目录不存在 → **FAIL**
（并说明它把自动下载也堵死了）；目录在但加载失败 → FAIL；sherpa 模型就绪
或 vosk 缓存在且能加载 → PASS；两处都没有 → WARN（首次需联网下载）。
这里**不真去下载** 42MB，否则 doctor 会卡一分钟。
实测：`[PASS] 语音识别 vosk 中文模型已缓存（~/.cache/vosk）`。

**测试**：`test_asr_voice_pipeline.py`（打桩抓语言代码 + 真加载 + 配置回归）
与 `test_doctor_asr.py` 7 组。**变异 6 刀全红**（`tools/_qc_asr_mutation.py`）。
其中两刀一开始没抓住，都是**测试自身的盲区**：
③ 刀挂错了测试文件（改 asr.py 却去跑 doctor 的测试）；
⑥ 刀的断言写成"本机已有模型就跳过"，于是把"变异造成的 PASS"和
"本机真有模型的 PASS"混为一谈——改成强制打桩"两处都没有"后才红。
门禁 **40 → 42 项全过、0 跳过**；doctor 16 PASS / 2 WARN / 0 FAIL。

### 2026-10-19 · 她一个字都说不出来：TTS 落到静音后端 + doctor 新增「语音合成」判级
**入口**：用户说"阿里云没钱" → 上一轮把 LLM 迁到免费源后，意识到该问一句
"**那她现在能说话吗**"。一实测，比欠费更唬人。

**根因（三层，全都是静默的）**：
1. `voice_profiles[0]` 是阿里云百炼（`dashscope_cosy`，欠费账户），注释还写着
   "主用本地 voxcpm，欠费不影响"——**假的**：没有任何角色引用该档，
   注释里那个"主用"从未存在过。
2. 角色 `voice.engine` 是 `''`。而 `create_backend('')` 按 2026-10-16 发布口径
   返回 **`_SilentBackend`**：接口齐全、**不出声、不联网、零日志**。
   于是症状是"AI 有回复但一个字都不说"，框架层一切正常，日志里一条都没有。
3. `fallback_pyttsx3: false` ——连本地兜底都关了。

**实测确认**（`tools/_qc_tts_engine.py`）：当前角色解析出的 `engine=''` →
后端名 `'未绑定'`。同机 pyttsx3 实测 **0.21s 合成出 256KB 合法 WAV**，
系统自带中文音色（Microsoft Huihui Desktop）。

**修复**：两个角色的 `voice.engine` 绑到 `pyttsx3`、`fallback_pyttsx3` 开 true
（本地 SAPI5，免费、必响；想换 voxcpm / 百炼随时改回）。`config.yaml` 与进仓的
`config.release.yaml` 同步改，并把百炼档那句误导注释改准。

**新增 doctor 检查「语音合成」**：解析当前角色 engine，**本地引擎真合成一次**
（云端不真合成——欠费时 15s 超时会卡死整个 doctor）。判级：
静音后端 / 未知引擎 / 真合成失败 / 产物过小 → **FAIL**；
voxcpm 端口没在听、云端无 key → WARN；本地真合成出非空音频 → PASS。
实测：`[PASS] 语音合成 engine='pyttsx3' · 真合成 OK（43406 字节）`。

**测试**：`src/tests/test_doctor_voice_backend.py` 12 组
（含"接线没被摘掉"断言）。
**变异 7 刀全红**（`tools/_qc_voice_mutation.py`，还原 sha256 一致）；
其中 C 刀第一版把 `for` 的冒号也改没了——那种红是 SyntaxError 崩出来的、
**没有区分力**，已改成语法合法的摘除。

门禁 **39 → 40 项全过、0 跳过**；doctor 15 PASS / 2 WARN / 0 FAIL。

### 2026-10-18 · 主链路从"全挂的阿里云"迁到免费源 + `${ENV}` 引用 + 渠道探活

**用户的起点**：*"一开始我就难到了，因为我用的都是免费 Token 总账，阿里云没钱啊。"*

**根因（三层叠加，任何一层都足以让它全哑）**：
1. **渠道全指向阿里云百炼**，而百炼账户 `HTTP 400 "not in good standing"`（欠费/风控，
   **账户级**：`GET /models` 仍能列出 176 个模型，chat 一律被拒）。实测 9 个渠道 7 个可用，
   唯一死的就是它——而项目 defaults 全绑在它身上。
2. **`api_key` 全是空串**。用户的安全约定是"完整 key 只存环境变量、不落盘"，
   于是配置里从来没填过 → 级联大脑 `enabled = bool(base_url and api_key and model)`
   恒 False → **通话不出声，日志里只有一条 warning**。
3. **加载器只认字面量**，没有"从环境变量取"的通路，于是①和②互相锁死：
   要填 key 就得破坏约定，不填就没有大脑。

**改动**：
- `src/config.py` 新增 `${VAR}` 引用：`api_key: ${ZHIPU_API_KEY}` → 内存里展开成明文，
  `save()` 时把**没被用户改过**的字段还原成模板（改过的按新值走 DPAPI 加密）。
  环境变量缺失 → 空串 + 一条 config_issue（doctor 可见），不抛异常。
  `secret_box` 不加密模板（否则配置文件里只会多一段无意义密文）。
- 主链路切到免费源（`config.yaml` + 进仓的 `config.release.yaml` 同步改）：
  主聊天/通话大脑 → 智谱 `glm-4-flash`（永久免费不限量，实测 1.06s，**无 reasoning**
  ——通话大脑 max_tokens 只有 90，推理模型会被思考链吃光）；Agent → `glm-4.7-flash`
  （要规划多步）；视觉 active 本已是 `glm-4v-flash`，只差 key；`manual_provider`
  显式指 `zai-glm`（否则按列表顺序会挑到百炼的空 key 端口）；补 5 个免费端口
  （魔搭 2000 次/天、TokenHub 体验包、AMD 20RPM、DeepSeek 官方 ¥18.7 兜底）。
- 附带修一个潜伏 bug：`OpenAIClient(cfg)` 的 `api_key`/`model` 默认值写成 `""`，
  而判据是 `x if x is not None else 读配置` —— 空串不是 None，于是"不传就走
  llm.openai"这条路**从来**读到空模型、`available` 恒 False。改默认值为 None。
- `tools/doctor.py` 新增 **「模型渠道」探活**：对当前生效的三条链路各发一次最小请求，
  按 HTTP 语义判级——429 判 PASS（限流说明 key 有效）、400/401/402/403 判 FAIL、
  5xx 与超时/断网判 WARN（**断网时不能全红，否则 doctor 就废了**）。
  顺带修它自己的显示 bug：f-string 里 `${...}` 被渲染成 `$Ellipsis`。

**两处假警噪点**（都由新代码触发，修在源头）：
- `check_package_secrets.py` 把 `${VAR}` 当"非空凭据"阻断打包 → 认引用（引用不是密钥）；
  把 `dpapi:...` 当明文告警（`sticker.api_key` 就这样每次打包报一次）→ 认密文前缀。
- `doctor` 的「密钥落盘」项同样要放过 `${...}`。

**验证**：
- 端到端：`tools/_qc_verify_free_channel.py` 用**项目自己的客户端**真发请求，
  拿到 `'智能助手，知无不言。'`；doctor 显示 `主聊天（glm-4-flash）✓ 1133ms；
  当前视觉（glm-4v-flash）✓ 541ms`。
- 依赖盘点 `tools/_qc_bailian_reliance.py`：**17 处指向百炼，0 处正在生效**。
- 新测试三组：`test_config_env_refs.py`（展开/缺失/保存不落盘/改过值优先）、
  `test_doctor_llm_channel.py`（10 组判级 + 收集去重 + 接线在位）、
  `test_package_secrets_scan.py`（正反 7 组，防闸门被改松）。
- 变异 7 刀全红（`tools/_qc_channel_mutation.py`）：400 不判红 / 429 判红 /
  断网判红 / 摘掉接线 / 去重失效 / 空 key 发包 / 5xx 判红。还原后 sha256 一致。
- 门禁 36 → **39 项全过、0 跳过**。

**踩的坑（第三次，记下来）**：Git Bash 的 heredoc 会把字符串里的 `\n` 换成字面 `/n`
——第一版变异脚本因此把锚点写坏，报"命中 0 次"；`doctor.py` 是 CRLF 而锚点用 LF，
也命中 0。多行锚点一律用 `chr(13)/chr(10)` 逐行拼，别用转义序列。

---

`check_silent_except.py` 从 10-17 就写好了、报告里也写了"可直接进 CI"，但**一直没接线**，
9 处真静默（主链路 `except: pass` 且块内无注释）就那么放着。这轮收掉。

**先定性再动手**（不能为了过闸门乱加注释）：

| 位置 | 判定 | 处置 |
|---|---|---|
| `tts._worker` 置空闲失败 | 状态会卡在 speaking，用户只觉得"她不说话了" | 加 `logger.debug(..., exc_info=True)` |
| `tts._tts_cache_put` 写缓存失败 | 退化为每句重合成，"最近变慢"无从查 | 同上 |
| `tts._kick_prefetch` 预合成起不来 | 退化为串行合成，"每句慢半拍" | 同上 |
| `tts.stop()` pygame / sd 停止失败 | 按了停止却没停时是唯一线索 | 同上（两处） |
| `tts._play_pcm` / `_speak_single` 删临时 wav 失败 | 音频已播完，删不掉只留垃圾 | 补注释说明（Windows 偶发） |
| `tts._tts_cache_get` 读缓存失败 | 等同未命中，正确性不受影响 | 补注释 |
| `cloud_omni_realtime._run` `queue.Empty` | 50ms 轮询超时是正常路径，不是错误 | 补注释 |

结果：真静默 9 → **0**，总静默 78 处（其余都是带注释的有意降级）。

**接线时发现闸门自己有两个"沉默"缺陷**（比原问题更值得修）：
1. `scan()` 遇到 `SyntaxError` 直接 `return []` → 主链路文件被改坏时，闸门**少扫一个
   文件、总数变小、退出码还是 0**（实测：注入一段语法错误后 78→66、rc=0）。
2. `if not os.path.exists(p): continue` → 主链路文件被改名/删掉，闸门静默跳过。
现在两者都记为错误，`--strict` 下直接判红并打印原因。

**变异验证**（`tools/_qc_silent_mutation.py`，三刀全红、还原 sha256 一致）：
A 注入 `except: pass` → 点名 `tts.py` 真静默；B 制造语法错误 → 报 `SyntaxError`；
C 主链路文件缺失（in-process 打桩，不动真实文件）→ 报"文件不存在"。

门禁 **35 → 36 项全过、0 跳过**（新增「主链路静默异常」）。

### 2026-10-18 · 桥接三层闸门 + 一个"恒走降级分支"的死按钮

**为什么查这里**：AGENTS.md 第七条写着"定义齐全 ≠ 接线"。前端→适配层→后端这条链
有三级，任何一级断掉都只表现为"点了没反应"，日志里一条都没有。此前的
`check_web_js_syntax` 只做 JS 语法、`check_web_assets` 只查资源文件是否存在，
**没有人查过名字对不对得上**。

**① 找到并修掉一个真断点：设置页「打开状态文件」**
`ui_redesign_settings.html:5845` 写的是
`if (window.Backend && typeof Backend.open_data_file === 'function') { Backend.open_data_file(...) }`，
而 `app_backend.js` 从来没有导出过 `open_data_file` → 守卫恒为 false → 按钮**永远**
走 `Backend.toast('状态文件：data/proactive_pack.json')` 这个降级分支。
后端 `CharacterAPI.open_data_file()`（webapp.py:15839，含防路径穿越）早就实现好了，
纯粹是适配层漏了一行。补上同名转发（页面已按 snake_case 写，改页面风险更大）。
这类 bug 的特征值得记：**带 `typeof` 守卫的降级写法会把"接线漏了"伪装成"环境不支持"**，
所以它比直接报错更难发现。

**② 新闸门「前端桥接」（`tools/check_bridge_methods.py`）**
三层全查：
  ① 每个页面（46 个，排除 `pre_v2` / `SIMPLIFIED` / `_pre_` 历史副本）调用的
    `Backend.xxx` 必须在适配层导出；
  ② 导出的成员必须能在定义里找到 `call('py_name')`（`toast`/`ready`/`call`/
    `capacityBadge` 这类纯前端助手除外）；
  ③ `call('py_name')` 的目标必须真的在 `CharacterAPI` 上——**包括页面还没用到的导出**
    （那是潜伏的坏接线，等页面接上去才炸）。
实测：46 页 / 134 个方法 / 245 个导出 / 320 个后端公开方法，0 断点。

**踩到的两个假阳性（都写进工具注释了）**
  · 不剥注释会把说明文字当调用：页面里明明写着
    "（Backend.getProactivePackFlags 不存在）"和"本地视频文件 → Backend.watch_local_video"，
    第一版因此报了两个假断点；
  · 适配层的 `toast` 是本地函数、不转发后端，不能算"接线不明"——否则永远红。

**变异验证**：① 把 `call('send_chat')` 改成 `call('send_chatt')` → 报
"③ sendChat → call('send_chatt')：后端 CharacterAPI 没有 send_chatt()"；
② 从导出对象删掉 `sendChat,` → 报"① …没导出（点了没反应）"；
③ 删掉 `open_data_file,` → 精确点名 `ui_redesign_settings.html`。
三处还原均 sha256 一致。门禁 **34 → 35 项全过、0 跳过**。

### 2026-10-18 · 同一类坑再扫三处 + 前端 JS 闸门从"装饰"变"能红"

上一节修的是"async 里做同步事"这一类。既然是一类，就不能只修撞见的那一处——
写了个 AST 探针按类扫，又抓到三处，并把这类问题固化成闸门。

**① `qzone._on_decision` 发说说卡死事件循环**
`QzoneClient.publish` 是同步 urllib（超时 15s），原来在事件循环线程里直接调 →
发一条说说期间 TTS / 对话 / 桌宠全停，最长 15 秒。改 `await asyncio.to_thread(...)`。
佐证"这是漏网"：通道里其它出站调用（`onebot.py:346`、`media_ingest`、`video_watch`）
早就是 `to_thread`，只有这里没跟上。新增 `test_qzone_nonblocking.py`（桩 sleep 0.4s，
量循环抖动；变异回同步调用 → "卡住 406ms" 被抓住）。

**② `tts._pyttsx3_speak` 里 `time.sleep(0.6)`**
写在 `async def` 的 finally 里 —— 每用 pyttsx3 说一句就把事件循环占住 600ms。
改 `await asyncio.sleep(0.6)`。注意：同一文件里另有 3 处 `time.sleep(0.6)` 在**同步**
函数里（`_play_file` 等，本身就跑在 `to_thread` 里），不能无脑替换——只改 async 那一处。

**③ `heavy_apps.pull_cloud / report_promoted` 里 `urlopen(timeout=5)`**
两个 `async def` 直接在循环里发 HTTP。它们由 `asyncio.create_task(...)` 在首启与
周期同步时拉起，正好和 TTS/对话抢同一个循环 → 首启卡 5 秒。改为嵌套同步 worker +
`to_thread`（保留原失败重排队逻辑）。

**④ 前端 JS 语法闸门：原来永远 exit 0（装饰闸门）**
`check_web_js_syntax.py` 打印完"语法错误总数"就结束，**没有退出码**——错再多门禁也不红。
而且中间产物写进 `data/_syntax_check`（往用户数据目录堆垃圾，实测已积 12 个文件）。
改：有错 `exit 1`；无 node 时 `exit 77`（check_all 的跳过约定，单列"未验证"）；
中间产物改系统临时目录并在结束时清掉。变异：往 index.html 第一个 `console.` 前插
`}}{` → rc=1 且指出"inline块#3 第 519 行起"，还原后 sha256 一致。
顺带删掉 `tools/check_html_scripts.py`：它把 node 写死成
`C:\Users\Administrator\...\node.exe`（本机直接 FileNotFoundError），功能又与
`check_web_js_syntax` 重复，属 P3 死工具。

**⑤ 新闸门：异步阻塞（`tools/check_async_blocking.py`）**
AST 查"async 函数体内（不下钻嵌套 def）出现 time.sleep / urlopen / subprocess.run /
requests.* / os.system / socket.* 且没被 to_thread/run_in_executor 包裹"。
嵌套同步 worker 是正确写法（通常交给 to_thread），必须排除——否则全是噪音。
配套 `src/tests/test_check_async_blocking.py` 7 条正反例守闸门本身。

**验证**：门禁 **30 → 34 项全过、0 跳过**（新增：前端 JS 语法 / 空间发布不阻塞 /
异步阻塞 / 异步阻塞自测）。变异 2 刀全红：qzone 改回同步 → "卡住 406ms"；
tts 改回 `time.sleep` → 闸门报 `tts.py:1259 async _pyttsx3_speak() 里同步调用 time.sleep()`。
两处还原均 sha256 一致。

### 2026-10-18 · 微信通道三连修：回复发不出去 / 回调对外裸奔 / 发消息卡死主循环

> 起点：门禁 27 项全绿，但绿的是"已知项"。这轮换成从**运行中的进程**往回查：
> doctor 报"端口 9912 被占用" → 顺着这条线进了 `src/channels/gewechat.py`。

**① 微信回复从来没发出去过（P1，功能完全失效）**
`_on_decision` 写的是 `await self._safe_post(...)`，而 `_safe_post` 是**同步 def** →
每次回微信都在 `await None` 上抛 `TypeError: object NoneType can't be used in 'await'
expression`。事件总线 `except Exception: logger.exception(...)` 把它吞了，所以症状只是
"AI 不理人"，日志里一行没人看的异常。**这条从基线 `5bffc68` 就在**（`git log -S` 确认）。
修法：`_safe_post` 改 async + `asyncio.to_thread(fn, *args)`；`_send_media_item` 同步改
async（原来媒体那条在事件循环里直接发）。

**② 同一个函数里的第二个洞：发消息卡死整个桌宠**
`_client.post_text` 走 `urllib`（超时 15s）。就算把 `await` 去掉，它仍然是在**事件循环
线程里**做同步 HTTP——网关慢/挂时 TTS、对话、桌宠动画一起停。`to_thread` 一并解决。
实测：桩函数 sleep 0.4s，改前事件循环抖动 406ms，改后 16ms。

**③ 回调服务绑 0.0.0.0 且零鉴权（安全边界，与 OneBot 不一致）**
Gewechat 的回调是**无签名**的裸 JSON POST。绑 `0.0.0.0` 又没令牌 ⇒ 同网段任何主机
POST 一条 `{"data":{"fromWxid":"...","content":"..."}}` 就能让桌宠"以为微信来了消息"，
AI 会当成真人说的去回复、甚至调工具（投毒/越权）。实测：伪造 POST 返回 200 且**入队成功**。
OneBot(reverse) 早就按"默认只听回环、非回环必须配 token、否则一律拒绝"做了
（`onebot.py:32`），微信这条漏了。修法照同一套标准：
- `callback_host` 缺省改 `127.0.0.1`；`config.yaml` / `config.release.yaml` 同步，
  并新增 `callback_token`；
- 非回环 + 无 token ⇒ **拒绝启动**（fail-closed）并打 ERROR，写明两种修法；
- 配了 token ⇒ 校验 `?token=` / `X-GEWECHAT-TOKEN` / `Authorization: Bearer`，
  `hmac.compare_digest` 定长比较，错的一律 401 且**不入队**；
- README 补上"为什么必须鉴权"和 Docker 场景怎么填。

**验证**
- 新增 `src/channels/tests/test_gewechat_callback.py`（6 组）：默认回环 / 非回环无
  token 拒启 / 回环免 token 入队 / 三种带 token 方式放行且错的一律 401 不入队 /
  **决策回发真的发出去了** / 回发不阻塞事件循环。
- 变异 5 刀全红（`tools/_qc_gewechat_mutation.py`，还原后 sha256 一致）：
  还原原 bug → `TypeError: object NoneType...`；同步调用 → "卡住 406ms"；
  鉴权恒放行 → "无 token 必须 401"；去掉 fail-closed → "必须拒绝启动"；
  默认 host 改回 0.0.0.0 → "默认必须只听回环"。
- 新增闸门 **`协程调度`**（`tools/check_await_sync.py`）：AST 查两个方向——
  `await` 了同步方法、async 方法没被 await/调度（协程对象直接丢）。真实案例就是①。
  误报边界已处理：异步生成器、`asyncio.run` / `create_task` / `gather` / `wait_for`。
  配套 `src/tests/test_check_await_sync.py` 7 条正反例守闸门本身（含 `asyncio.run`
  消费、`async for` 生成器两个反例）。
- 全量门禁 **30 项全过、0 跳过**；doctor 12 PASS / 3 WARN / 0 FAIL（WARN 均为已知）。

**留给用户的动作**：`config.yaml` 的 `callback_host` 已改 `127.0.0.1`。若 Gewechat 跑在
Docker 里（容器访问不到宿主回环），把它改回 `0.0.0.0` **并**配 `callback_token`，
后台回调地址写 `http://<本机IP>:9912/?token=<同一个值>`，否则回调服务会按设计拒绝启动。

### 2026-10-18 · C9 收尾双刀：装配源码指纹闸（C9-5）+ cookie 覆盖 DPAPI


**C9-5 源码指纹闸**：堵上「装配漂移守卫只看版本号」的盲区（上轮实测撞上：
版本没 bump 但 dist_stage 源码过期，出包带着旧程序）。装配收尾把
`src/**/*.py + main.py` 的（相对路径+内容）聚合 sha256 存档到
`.src_fingerprint`；`build_installer` 出包前重算比对——不一致或缺存档
（旧装配）直接拒绝，makensis 不启动。只看内容不看 mtime，git checkout
不假阳性。**真机级双向实测**：装配后给 version.py 追加一行注释 → 出包
被拒（存档 12da30… ≠ 当前 3a1248…）；还原 → 放行。

**cookie 覆盖 DPAPI（C9 缺口关闭）**：`_SECRET_FIELDS` 加 `cookie`
（qzone 登录态，全仓唯一 cookie 字段无误伤）——写盘自动加密为
`dpapi:`、读盘解密、明文兼容，与 api_key 同路径。doctor 第 14 项扫描
同步扩到 `cookie:` 行（明文 cookie → WARN）。

测试：secret_box 24 断言 / make_dist_deps 34 断言（C9-5 段 7 项）/
doctor 32 断言（14.5/14.6 cookie 两路）；变异 M1（指纹丢内容）M2
（比对恒假跳闸）全红。端到端 4m45s 装配+出包，门禁 27/27。

### 2026-10-18 · E 阶段开篇：开机自启开关（HKCU Run 键，全链路实测）

**src/autostart.py**：写/清/查 `HKCU\...\Run` 的 `DaniaAI` 值（每用户无 UAC，
与 C2-A 安装器同理念）。命令行 `"<root>\python\pythonw.exe" "<root>\main.py"`
——pythonw 无控制台、全路径引号包裹防含空格路径解析漂移。enable/disable 幂等；
is_enabled 对 FileNotFoundError/OSError 一律 False 不抛。

**挂接四处**：① config.release.yaml `app.autostart: false`（单一真值）；
② main.py 启动 sync（包内判定：root 同时有 main.py 与 python/pythonw.exe，
开发机 .venv 自动跳过；失败不拖垮启动）；③ WindowAPI 新增
`get_autostart`/`set_autostart` RPC（UI 开关）——set **双写**注册表+config：
只写注册表不写 config，下次启动 sync 会按旧 config 悄悄关掉（本仓
「开关接线了但没通电」典型病，一次双写根治）；④ installer.nsi 卸载段
`DeleteRegValue` 清自启项（保留数据≠保留自启——程序没了 Run 键残留只会
让每次开机弹「找不到文件」）。

**端到端实测**：装 → 包内 python enable → 真实 Run 键写入且命令指向安装目录 →
静默卸载 → Run 键被清、目录只剩 config.yaml。

**测试事故与工程化**：M2 变异（enable 写死全局值名）跑变异测试时把
`DaniaAI` 写进真实 Run 键无人清——**变异测试污染真实系统状态的活案例**。
工程化修复：测试加快照守卫（测前记录真实键原值，测后不一致自动还原），
M2 重验下守卫生效（变异红 + 真实键零污染）。

### 2026-10-18 · C2-A 补强：装配漂移守卫 + 安装/卸载运行中检测

**漂移守卫**：dist_stage 是旧装配、src/version.py 已 bump 时，原实现会
出"安装包报新版本、里面是旧程序"的双口径。`build_installer` 现在读
装配目录的 src/version.py 与注入版本比对，不一致或缺文件直接拒绝，
makensis 根本不启动（先挡后跑）。测试：mismatch / 缺 version.py 两路
断言 + argv 未被调用断言。

**运行中检测**：应用在跑时升级/卸载会锁文件失败、装一半。安装与卸载
段开头用 `FileOpen "$INSTDIR\python\python.exe" a` 写试探——被进程
映射的 exe 写打开必然 sharing violation；打开成功立即 FileClose。
挡下时 MessageBox 提示"先退出再装"，`/SD IDOK` 静默路径同样中止。
**真机级验证**：ctypes CreateFileW 独占锁模拟运行中 → 卸载被挡（13
条原样）；释放句柄重卸 → 完成（只剩 config.yaml）。

**踩坑**：MessageBox 的 /SD 枚举是 `IDOK` 不是 `OK`——填错 makensis
只报一串 modeflag 用法不报具体哪个词（第一次构建即炸，好逮）。

### 2026-10-18 · C2-A 收口：版本号单一真值注入（--installer 一步出包）

**问题**：installer.nsi 硬编码 `VERSION "0.2.0"`——发版改了 `src/version.py`
但忘了改脚本，就会出"安装包报 0.2.0、程序自报 0.3.0"的双口径（正是本日
质检 P2/P3 刚抓过的同类 fixture 漂移）。

**改动**：`make_dist.py` 新增 `read_app_version`（正则读 `src/version.py`，
不 import 不依赖包内环境）/ `find_makensis` / `build_installer`（/D 注入
VERSION + STAGE 绝对路径），main 加 `--installer`：装配过闸后才出包，
一步到位。installer.nsi 的 VERSION/STAGE 加 `!ifndef` 守卫，兜底是
`0.0.0-dev` 哨兵——忘注入时"添加删除程序"里一眼可见，绝不给像真的版本号。

**验证**：测试两法互证（正则 vs exec version.py，测试内不硬编码版本）+
argv 断言；2 刀变异全红（M1 版本硬编码 → 版本一致性断言红 + 产物落空红；
M2 去 /DSTAGE 注入 → argv 断言红）。端到端：`--installer` 全流程 4m29s
出 `达妮娅AI桌面伴侣_Setup_v0.2.0.exe`（72.8MB），静默装 → 包内 doctor
13 PASS / 0 FAIL → 静默卸载只留 config.yaml。门禁 27/27。
**顺带修真缺陷**：build_installer 原实现对传入不存在的 makensis 路径
直接 FileNotFoundError——补了 isfile 预检（测试抓住的）。

### 2026-10-18 · C2-A 落地：NSIS 安装器（每用户、升级保留配置、卸载保数据）

**为什么不是 electron-builder**：查盘实锤当前架构是 Python 主导、Electron 只渲染
桌宠窗，B 路线等于把 D 主窗迁移提前；A 路线 installer 只做「铺文件+快捷方式+
卸载+升级保留配置」。NSIS 3.10 本体仅 ~2.4MB（SourceForge），不是 200MB+ 的
electron-builder。

**改动**：`tools/installer.nsi`。每用户装 `%LOCALAPPDATA%\DaniaAI`（无 UAC）；
config.yaml `SetOverwrite off`、data\ logs\ 安装器从不写入；卸载默认保数据
（GUI 才弹询问）；不打包 wheels\ 与 config.user.bak.yaml（构建残留会制造假 WARN）。

**验证（全部实测）**：静默装 `/D` 自定义路径 → 装后包内 doctor 13 PASS / 0 FAIL
（rc=0）；连续两次升级重装，用户 config.yaml 标记行均保留；静默卸载删净程序
文件与注册表键，只留 config.yaml。体积 314MB → **72.8MB**（LZMA 23.1%）。

**踩坑**：① NSIS `File` 相对路径按脚本目录解析（非 CWD）→ `!cd ..` 锚定；
② `MessageBox` 静默模式取默认按钮（YES=删用户数据）→ `/SD IDNO` 必加——
这条若漏掉，脚本化卸载会静默清掉用户配置；③ Git Bash 传 `//S //D=...`
会弄丢 `/D`（装到默认路径），静默操作走 `cmd /c`；④ .nsi 必须 UTF-8 带 BOM；
⑤ NSIS 卸载器自复制 TEMP 后异步重执行，验证要等 ~15s。
| — | 11:45 | fix | 壳 GPU 崩溃后自动退让软件合成（真透明恢复可用） | 端到端 PASS + 变异 4 项 FAIL |

### 2026-10-18 · 质检 P2/P3：版本号 fixture 去字面量、位置还原口径同源、三处死代码

**问题**：
1. `test_config_schema` 的 fixture 写死 `"version": "0.2.0"`，而它断言"无问题"。
   按文档"发版只改 `src/version.py`"操作 → 配置校验这道闸**必红**。
2. 位置持久化：`save_physical` 用 `PET_W/PET_H`（420×540）归一化，
   `_finalize_pet_window` 却拿**实际窗口尺寸**（实测 424×544）还原 —— 分母差 4px，
   在右/下边缘放大成几像素漂移。这条坑 `pet_state` 自己的注释里就写着。
3. 三处死代码/失效工具：`make_dist._BOOT_MODULES` 定义后无人使用；
   `check_web_js_syntax.py` 硬编码 `G:\ai对话\11` 与某台机器的 node 路径（换机即崩）；
   `pet_state.screen_scale()` 取了 `sw` 不用。

**改动**：
- fixture 改用 `from src.version import APP_VERSION`。
- `_finalize_pet_window` 还原改用 `PET_W/PET_H`；`create_desktop_companion` 的窗口尺寸
  也改成从 `pet_state` 取，三处口径合一。
- 删 `_BOOT_MODULES`；`check_web_js_syntax` 的 ROOT 从 `__file__` 推、node 走
  `DANIYA_NODE`/PATH；去掉未用的 `sw`；`version.__all__` 补 `name_version`。

**验证**：
- `test_pet_state` 新增 [7]：源码级断言"`pet_window` 不得用实际尺寸调用 `restore_logical`"，
  且必须出现 `restore_logical(_ps.PET_W, _ps.PET_H`。
- `check_web_js_syntax.py`（指定 DANIYA_NODE）→ 语法错误 0，桥方法 41 个。
- 全量门禁：26 通过 + 1 跳过。

### 2026-10-18 · 质检 P1 两条：看门狗「失败即停摆」+ 配置可选缺失误报 ERROR

**问题**：
1. `_watchdog_loop` 写的是 `if not self._try_restart(): return`，而 `_try_restart` 在
   **"这一次没起来"**时也返回 False —— 一次瞬时失败（端口占用、单实例锁残留、GPU 抖动）
   就把看门狗弄死：既不重试，也不降级，桌宠静默消失。而 `_try_restart` 末尾的注释
   还写着"保留看门狗，下一轮再试"——**行为与注释相反**。
2. `config_schema` 给可选项 `app.version` 补默认 `""`，`validate` 再拿 `""` 去和
   `APP_VERSION` 比，于是"没写版本号"被判成"版本漂移"的 **ERROR**；
   `doctor.check_version` 里"缺失 → WARN"的分支因此永远走不到（死代码）。

**改动**：
1. `_try_restart` 的返回值只表示"这一次起没起来"，是否收工另用 `_give_up` 表达：
   只有"配额耗尽 / 已降级"才置 True，`_watchdog_loop` 只在 `_give_up` 时退出。
   单次失败仍计入配额（防止无限 spawn），但不再等于终局。
2. `app.version` 默认值改 `None` 且**不补**（无默认值的可选项保持缺失；不补也不在这里报，
   否则 `apply_defaults` 失去幂等性），由 doctor 的「版本一致」项负责出声；
   `validate` 把空串按"没配"处理；`doctor.check_version` 同样判空。

**验证**：
- `test_pet_shell_selfheal` 新增 [14]：单次失败后 `_give_up` 仍为 False；从零跑满配额时
  "1 次直调 + 3 次重试 = 4 次"。变异（循环改回旧写法）→ **FAIL**（实际 2 次、未降级），还原 PASS。
- `test_config_schema` [9] 新增"缺失 ≠ 漂移"：不补假值、不产生 ERROR、再补一次仍为空（幂等）。
  变异（默认改回 `""` + 旧比较）→ **3 项 FAIL**，还原 PASS。
- `test_doctor` [5] 新增"空串 = 没配 → WARN"。
- 实测：删掉 `config.yaml` 的 `version:` 再跑 `--doctor` → `[WARN] 版本一致 配置里没有 app.version`，
  汇总 **0 FAIL**（改之前是"配置文件"+"版本一致"两条 FAIL）。
- `tools/check_all.py`：26 通过 + 1 跳过（壳在本机起不来）。

### 2026-10-18 · 质检 P0 处置：桌宠壳默认关闭，门禁区分「跳过」与「失败」

**问题**：深度质检（`质检报告_2026-10-18.md`）发现 `check_all.py` 的 `壳自愈端到端` 稳定红（4/4）：
Electron 渲染真实 rig 页面时 GPU 进程连续崩（`0xC0000005`）→ Chromium
`FATAL: GPU process isn't usable. Goodbye.`（rc=`0x80000003`），壳根本起不来。
而默认 `ui.pet_backend: auto` 会让**每次启动**都先崩一次再静默回退 pywebview ——
用户只看到白底，日志里多一段看不懂的 GPU 报错；`--doctor` 的 `check_shell` 只查
`electron.exe`/`main.js`/`preload.js` 在不在，还报 **PASS**，是典型的假绿。

**改动**：
1. 默认后端 `auto → webview`（`pet_backend.DEFAULT`、`config_schema`、两份 config、
   `run_webview` 形参、`main.py` 回退值）。壳降级为**显式选择**
   （`ui.pet_backend: shell` 或 `--pet-backend shell`），注释里写明"已知部分 GPU 环境必崩"。
2. `--doctor` 的壳检查改成三态：缺件 → WARN；装了但没启用 → WARN（不再假绿）；
   配置要求 shell → **真起一次**（`probe_shell()`，判据是 stdout 出现 `CONTROL`），
   起不来 → FAIL 并带 FATAL 原文。
3. 端到端冒烟引入退出码 **77 = 跳过**：只有 `DANIYA_REQUIRE_SHELL=1` 或配置
   `pet_backend: shell` 时才把失败升级为红；`check_all.py` 把 77 单列为
   「跳过（未验证）」并打印原因，**不计入通过**（避免静默绿）。
4. 壳启动失败时顺手删握手文件：原来只有 `close()`/`shutdown_all()` 会删，
   而 `create_pet_shell` 失败后调用方直接回退，根本走不到那里，令牌会留在盘上。

**验证**：
- `tools/pet_shell_selfheal_smoke.py` → `rc=77`，打印 `[SKIP] 壳在本机起不来` + 「这不是通过」；
  同命令加 `DANIYA_REQUIRE_SHELL=1` → `rc=1`（红项仍在，只是不再默认挡发布）。
- `tools/doctor.py` → `[WARN] Electron 壳 已安装，但当前不启用（实验特性…）`；
  临时把配置改成 `pet_backend: shell` 再跑 → `[FAIL] … 本机起不来：GPU process isn't usable. Goodbye.`
- `tools/check_all.py` → 27 项：**26 通过 + 1 跳过**（跳过原因原样打印）。
- `test_pet_backend` / `test_config_schema` 的默认值断言同步改为 webview，均 PASS。

### 2026-10-18 · C9 前两刀：密钥 DPAPI 加密 + `.pth` 静默失效报警

**问题**：① `config.yaml` 里 `sticker.api_key` 等字段以**明文**存真实服务商 Key
（实测查到一颗智谱 Key 躺在 1781 行）——文件被 git ignore 挡住了版本控制，
但挡不住交付包、`config.user.bak.yaml` 备份、误传截图；② 包内运行时是
isolated 模式（`python312._pth`，`site` 不执行），install_deps 之后新装的
任何 `.pth` 都会**静默失效**——功能没反应且无报错，是最难查的一类。

**改动**：
- 新增 `src/secret_box.py`：DPAPI（crypt32，ctypes 直调，零依赖）。
  `Config.save()` 写盘前把非空明文 `api_key` 加密为 `dpapi:<base64>`
  （deepcopy 副本上加密，内存保持明文，调用方零感知）；
  `load_config()` 读盘时自动解密回明文；明文旧格式兼容；解密失败
  （配置从别的机器拷来）不抛异常，转降级提示让用户重填。
- `tools/doctor.py` 第 14 项「密钥落盘」：文本级扫描，明文 → WARN
  并给出"保存一次配置即自动加密"的修复路径。
- `tools/make_dist.py`：`scan_stale_pth` + `warn_stale_pth`，
  install_deps 收尾调用；白名单 `pywin32.pth`（C6 已手工补回），其余
  `.pth` 点名报警并给三步处置指引。

**验证**：`test_secret_box.py` 20 项（往返/树遍历/幂等/save 端到端
"磁盘无明文"/load 端到端/换机降级）+ 3 刀变异全红；
doctor 第 14 项 5 断言 + 1 刀变异红；`scan_stale_pth` 6 断言 + 2 刀变异红。
测试抓到两个真缺陷：① doctor 扫描漏 YAML 列表项 `- api_key:` 前缀
（真实 config 里恰好有大量此形态）；② 自家测试断言算错解密计数。
门禁 27/27（首次跑壳自愈端到端 flaky 一次，RPC 10054 竞态，复跑即绿，
与本轮四个改动文件无交集）。

**边界（如实）**：DPAPI 只防"文件被拷走"，不防同机同用户其它进程；
`qzone.cookie` 等其他敏感字段未覆盖；config 里现存那颗明文 Key 要等
下次程序保存才自动升级为密文。

### 2026-10-18 · C9 第三刀：numpy 版 phash 替换 ImageHash，甩掉 scipy ~124MB

**问题**：`scipy`（115MB）+ `PyWavelets`（8.6MB）纯由 `ImageHash>=4.3` 拖进
交付包，而全仓 6 处调用（screen_capture / realtime_vision / game_ocr /
squad_monitor / course_recorder）全部只用 `imagehash.phash(img)` 后做
`h1 - h2`——109MB+ 换一个距离比较，是 C3 体积报告里的最大单项。

**改动**：新增 `src/perception/phash.py`（numpy DCT 矩阵实现，接口只含
调用方用到的汉明距离）；6 处调用点替换；requirements.txt 显式排除标记
（`# ImageHash` 整行注释 + 原因）。

**对齐口径（生死线）**：`convert('L')` → LANCZOS 32×32 → 两次 DCT-II
（norm=None 均匀因子 2 可省——中位数二值化对均匀缩放免疫）→ 左上 8×8 →
median 严格大于二值化。**验证：208 张图（噪声 120 / 渐变 40 / 纯色 8 /
混合 40）与 imagehash 4.3.2 bit 级全部一致，含汉明距离**。

**踩坑（最贵部分）**：
1. 强对称图（纯色/渐变）低频系数**数学相等**——scipy FFT 把余弦和精确
   归零，矩阵乘留 ~1e-15 残差，残差悬在中位数两侧让纯色图二值化一半
   翻 1（ref 全 0 / own 32 个 1）。修法 `np.round(low, 9)`：压回数学零点，
   对 1e4 量级真实系数侵入（1e-13 相对）低于双精度极限。
   round(6)/round(9) 实测 208/208 全一致，取侵入更小的 9 位。
2. `str(imagehash)` 是 16 位 hex、自实现是 64 位 0/1——测试第一版比 str
   得出 208/208「全不一致」的假红，实际第一张就一字不差。
3. 最初 grep 用 head 截断，漏掉 squad_monitor 的第 6 处调用——
   「依赖声明」门禁按已知依赖反查才暴露。教训：全量清单类操作禁 head 截断。

**验证**：`test_phash.py` 10 断言 + 2 刀变异全红（去 round → 3 红；
DCT 矩阵去转置 → 4 红）。门禁 27/27。

**遗留**：dist/ 里的旧构建仍含 scipy——收益在下次 `--wheelhouse` 重建时
兑现（预计 472MB → ~348MB）；tools/ 下 4 个一次性脚本仍用 scipy（走
dev 环境，不进包，不受影响）。

### 2026-10-18 · C9-4：wheelhouse wheel-only 纪律 + 重建兑现 472→370MB

**问题（隔离重建实测撞上）**：`build_wheelhouse` 用 `pip download`，对
PyPI 上**只有 sdist** 的包（实测：`srt`、`proxy_tools`——后者是 pywebview
的传递依赖）会原样存 .tar.gz。离线 `--no-index` 安装时 pip 要为 sdist 跑
PEP 517 构建，联网拉 `setuptools>=40.8.0` → 「核心」档安装失败，
包不能发布。之前 472MB 那次构建没炸是因为装依赖走了联网——
**离线化一直是「半离线」，只有干净机器上才炸**。

**改动**：`build_wheelhouse` 改用 `pip wheel`（构建机联网时就把 sdist
构建成 .whl，交付侧零构建）+ 兜底清扫目录里的 sdist 残留并点名。

**踩坑**：`pip download` 的目标参数是 `-d`，`pip wheel` 是 **`-w`**——
用错得到 `no such option: -d`，报错带 `pip>` 前缀且被 `tail -55` 截掉，
第一眼看不到（调了三步才发现：grep wheels 目录 → 前台单跑 build_wheelhouse）。
教训：构建工具的报错别只用 tail 看结尾。

**验证**：测试 2 断言（pip wheel 参数 / 无 sdist 残留）+ 2 刀变异红
（M2 去清扫以 NameError 崩溃形式红——第一次误读为绿，实际是恢复命令
先执行、tail 跑在恢复后；变异判定必须看**变异后那次**的原始输出）。
端到端：wheelhouse 63 whl / 0 sdist → 离线安装成功 → 隔离 doctor
**12 PASS / 3 WARN / 0 FAIL** → **370.1MB**（旧 472MB，降 102MB；
scipy/pywt 消失，包内 phash 零 imagehash 依赖可用）。门禁 27/27。

**遗留（待拍板）**：`dist_stage_old_20261018/`（472MB，已改名保留）与
`dist/`（69MB）是否清理。
| `206cc47` | 20:38 | feat | 导入冒烟 + 依赖对账 | 变异 3 次 |
| `01d185c` | 20:41 | fix | CLR 崩溃守卫此前从未装上 | `_CLR_CRASH_GUARD_INSTALLED` 可置 True |
| `5b348a5` | 20:51 | fix | 6 处无界队列改有界丢最旧 | 14 项测试 + 变异 |
| `c1f6b30` | 20:55 | docs | 报告回填队列结果 | — |
| `a4a6a38` | 23:40 | feat | 交付目录组装器 `make_dist.py` + launcher 去旧盘符 | 组装 65.3MB/1334 文件，三道闸门 |
| `2f14c0a` | 23:58 | fix | 应用图标/任务栏归属 + Win32 置顶 64 位句柄截断 | AUMID 读回一致、WM_GETICON 非零、TOPMOST 位正确 |
| `e46f21b` | 00:40 | fix | 单实例锁 + DPI/单实例前置到建窗之前 | `test_win_shell.py` 6 项；跨进程同名锁 Failure |
| `86fa3df` | 00:45 | chore | AGENTS.md 协作宪章 + `check_all.py` 统一门禁 | 16 项全 PASS；变异后 exit 1 并点名 |
| `6d75b24` | 00:52 | feat | 换壳 PoC：同一 rig 页面在 Electron 下真透明 | 角落与真背景逐点相等、中心不同 |
| `cac2760` | 00:53 | chore | 锁定 Electron 依赖版本 | — |
| `4792f40` | 01:20 | feat | 换壳 1/2 步：CharacterAPI 暴露本地 RPC + 壳侧 shim | 19 方法两通道一致；端到端五项全通过 |
| `6801aee` | 02:10 | feat | 换壳 3 步：桌宠窗改由 Electron 壳承载（主窗口仍 pywebview） | 壳桌宠冒烟 11 项连续三次通过 |
| 待提交 | 10-18 | fix | `main.py` 模块级 ImportError：双击直接崩 | `--help` / `--doctor` 实测通过 |
| 待提交 | 10-18 | feat | `ui.pet_backend` 配置项 + 两处入口统一接线 | 22 项测试 + 变异 2 次 FAIL |
| 待提交 | 10-18 | feat | 桌宠位置持久化（按工作区比例存） | 18 项测试 + 突变越界夹取 FAIL |
| 待提交 | 10-18 | feat | 版本号单点 `src/version.py` | 三处口径归一 |
| 待提交 | 10-18 | feat | `--doctor` 一键自检（9 项） | 每项带修复提示，支持 `--json` |
| 待提交 | 10-18 | feat | 壳自愈 + 退出清理 + 崩溃降级 | 单测 13 组；端到端真杀进程验证 |
| 待提交 | 10-18 | feat | `make_dist --install-deps`：依赖按档装进包内运行时 | 包内 `--doctor` 9 PASS / 0 FAIL |
| 待提交 | 10-18 | feat | `make_dist --report`：体积可拆（谁占了 408MB） | 实测 python/ 占 93.1%，scipy 109MB |
| 待提交 | 10-18 | fix | RPC 长连接被 401 残留 body 污染（P1） | 压测 720 次 0 失败；3 项变异全 FAIL |
| 待提交 | 10-18 | feat | 交付包运行期隔离（`pythonXY._pth`）+ pywin32 DLL 铺放（C6） | 隔离环境 doctor 11 项；摘掉包内 rich → 报缺失 exit 1；8 项变异全 FAIL |
| 待提交 | 10-18 | feat | `--doctor` 增「干净机器三件套」：WebView2 / UCRT / 代码签名（C4+C7） | 包内 12 PASS / 2 WARN / 0 FAIL；26 项断言 + 7 项变异全 FAIL |
| 待提交 | 10-18 | fix | 升级吞配置：装配覆盖前抢救改过的 config.yaml（P1） | `preserve_user_config` + doctor「出厂默认+使用痕迹」检测；3 道变异全红 |
| 待提交 | 10-18 | fix | 启动令牌全量打进日志（P2） | `%.8s` 截断 + 新门禁「凭据日志卫生」（27 项）；4 轮调试闭环 |
| 待提交 | 10-18 | fix | **make_dist.py 被 `git checkout --` 误还原，从测试规格+产物重放**（P0 事故） | 21 段测试全绿 + 隔离环境端到端验证通过；教训：变异还原禁用 git checkout |

---

## 2026-10-18（待提交）

### 阶段 C8：产品化缺口两刀 + 一次误还原事故

**#2 升级吞配置（P1）**。`RENAME: config.release.yaml → config.yaml` 意味着老用户
解压新版 zip 时，填了 API Key 的 config.yaml 被出厂默认值静默覆盖。两处修复：
装配机侧 `preserve_user_config`（--force 重装配时旧 config 与模板不同 → 备份成
`config.user.bak.yaml`）；用户机侧 doctor 检测「出厂默认值 + data/ 有使用痕迹」
（两个单信号都不报——没填 Key 的新用户和 Key 还在的老用户都会误报，双信号
同时成立才是升级覆盖），发现 `config.user.bak.yaml` 时点名提示比对恢复。

**#6 启动令牌进日志（P2）**。`main_window.py` 把 `web_start_token` 以 `%s` 全量
打进日志，而同仓 `webapp.py` 对 api_key 早就用 `%.8s`——同类问题两种处理，
说明不能靠每个人每次记得，改成门禁：`tools/check_log_leak.py`（已注册进
check_all，27 项）。

**新门禁的调试过程本身就是 4 个真问题**，逐一被变异验证戳穿后才收口：
1. `\w*` 不含 `.`，抓不到 `STATE.web_start_token` 这种限定名；
2. 调用分两行写，单行口径对生产形态**永久失明**——升级为括号平衡的跨行拼接；
3. 拼接串里的 `\n` 让 `(.*)` 提前截断——补 `re.S`；
4. 「格式串里存在裸 %s」会把另两个不敏感参数（URL/目录）也抓进来——改成
   占位符与参数**位置对齐**后只看 token 参数对应的那一个。
最后一轮：修复态 exit 0 / 变异体（token 裸 %s）exit 1 / 删参数变异保守放行，
三态全部符合预期。

**一次 P0 事故，如实记录**：变异验证后还原时误用 `git checkout -- tools/make_dist.py`，
把当天全部未提交修改（C1 依赖隔离、C6 `_pth`/DLL 铺放/残留清理）冲回 HEAD。
恢复靠三样东西：08:53 的 `/tmp/make_dist.bak`（只到 C1 状态）、**测试文件**
（21 段断言就是完整行为规格）、磁盘产物（`dist_stage/python/python312._pth`
的 8 行内容）。按测试重写实现后 21 段全绿 + 隔离环境端到端复验（12 PASS /
0 FAIL、`isolated=1`、rich 来自包内、win32api 真调用通过）。教训进规矩：
**变异/实验还原只用明确的备份文件，`git checkout --` 对未提交工作等于 rm。**

### 阶段 C7 + C4 前半：干净机器三件套（WebView2 / UCRT / 代码签名）

**动机**：C1 收尾时留了一句「没在真·另一台电脑上验过」。这句话没法靠加班消掉——
手上只有一台机器。但能让程序在干净机器上打不开的原因，其实只有三类，其中两类
可以**前置判定**：WebView2 Runtime 缺失、Universal CRT 缺失。第三类（杀软拦截）
判不了，但「未签名」是它的最大诱因，而签名状态能在本地读出来。

#### 一、先拆掉一个臆想中的风险：VC++ 运行库

原本的假设是「干净机器缺 VC++ 运行库 → numpy 起不来」。所以写了 PE 导入表解析器，
对交付包内 300+ 个 PE 逐个数依赖（本机没有 dumpbin，纯结构体解析）：

| DLL | 谁提供 | 被几个文件引用 |
|---|---|---|
| `vcruntime140.dll` | **包内自带** | 93 |
| `vcruntime140_1.dll` | **包内自带** | 32 |
| `msvcp140.dll` | 引用数 **0**（numpy 用自带的哈希改名版 `msvcp140-a4c2...dll`） | 0 |
| `api-ms-win-crt-*` | **系统 UCRT** | 133 / 84 / 64 / 19 / 12 |

**结论：不需要装 VC++ 运行库。** 真正的系统依赖只剩 Universal CRT，而它是 Win10 起的
系统组件。于是这条检查从「查 VC++ 装没装」改成了「查 UCRT 在不在」——查对了才有意义。

#### 二、PE 签名解析：一个自己造的假绿

第一版把数据目录起始偏移写反了（PE32 应 `opt+0x60`，PE32+ 应 `opt+0x70`，
我写成 `0x70/0x80`）。读到的其实是 **Debug 目录**：

- `electron.exe` 返回 `(237485440, 28)` —— 28 字节，正好**一个调试目录条目的大小**，
  看上去完全像"已签名"
- `notepad.exe` 返回 `(189168, 112)`，也像

用 PowerShell `Get-AuthenticodeSignature` 交叉校验才发现三个文件全被判反。
修好后 3 个真实未签名文件（仓库根的 .NET exe）+ 2 个真实已签名文件（两次 python.exe）
与官方接口**逐一吻合**。

另一条查清的事实：**内嵌签名为空 ≠ 未签名**。`notepad.exe` 内嵌签名为空但系统判
`Valid`——它走的是 catalog 签名。所以这个函数只用于我们自己的交付件，不能拿去判系统文件，
代码注释里写死了这条边界。

#### 三、签名检查不能拿别人的签名给自己背书

第一版 `_signature_targets()` 把 `python/python.exe` 也算进去了。它是 Python 软件基金会
签的，在交付包里一跑就是 PASS——而我们自己**一个 exe 都没有**。这是第 N 次同型假绿。
收紧成「只认包根的自研 exe」后，交付包里如实报出：

```
[WARN] 代码签名  没有自研可执行入口（包根没有 .exe），签名无可检对象
```

#### 四、顺带查到的一件事（未处理，只记录）

仓库根躺着三个 10-17 产出的 exe（`达妮娅.exe` / `安装环境.exe` /
`达妮娅AI桌面伴侣_安装程序_v1.0_零依赖.exe`，共 35MB）。读节表与 CLR 头得知它们是
**.NET 程序**，不是 NSIS/Inno；而仓库里**找不到任何生成它们的脚本**。
即：一次性产物，不可重现。三者官方接口均判 `NotSigned`。

**验证**：26 项新断言（`[9]`–`[11]`）+ 7 道变异**全部按预期变红**（偏移写反、
UCRT 少查一个文件、无入口冒充 PASS、没装 WebView2 报 PASS、去掉越界判断、
版本号不进 detail、把第三方 exe 算成自研）。门禁 26/26 全绿；包内隔离环境
`--doctor` = 12 PASS / 2 WARN / 0 FAIL。

### 阶段 C6：交付包运行期隔离（`pythonXY._pth`）

**问题**：上一轮把「装」和「验」都做了隔离（`PYTHONNOUSERSITE=1`），但**运行期**没管。
包内 `python/` 是完整 Python 拷贝，不是 `python._pth` 隔离版，用户双击 .exe 时 `site`
照常加载 `%APPDATA%\Roaming\Python\Python312\site-packages`。后果是「在我机器上能跑」
这句话永远成立——用户自己 `pip install` 过什么都会混进来，而我们看不见。

**做法**：装配时写 `python/python312._pth`，让包内解释器进入 isolated 模式，
`sys.path` 完全由这个文件固定。默认开启，`--no-isolate` 仅供调试。

**代价是实测出来的，不是设计时想到的**（这是本轮最费时间的部分，三条路逐一试）：

| 尝试 | 结果 |
|---|---|
| `_pth` 里加 `Lib\site-packages\pywin32_system32` | **无效**，Python 3.8+ 的依赖 DLL 不从 `sys.path` 找 |
| 在 `main.py` 里 `os.add_dll_directory` | 有效，但只管走 `main.py` 的入口；`run_webui.py` / `python -c` 照旧坏；且句柄不保活会被 `RemoveDllDirectory` 立刻撤销 |
| 把 DLL 复制到 `python/` 根（解释器同目录） | **有效且入口无关**，`python -c "import win32api"` 直接过 ← 采用 |

根因是 `site` 不执行 ⇒ `site-packages/*.pth` 全部失效，而全包只有一个
`pywin32.pth`（负责 `win32` / `win32\lib` / `Pythonwin` 三段路径）。所以 `_pth` 里
手工补回这三段；`..`（包根，`main.py` 在那儿）放在**最后**，避免遮蔽标准库。

顺带两个真问题：

1. **`_pth` 不支持 `import` 行**。照抄 `pywin32.pth` 写 `import pywin32_bootstrap`
   会得到 `unsupported 'import' line`，而那个模块在包里**根本不存在**（死代码）。
2. **`--force` 只覆盖不删多余**。上一次在包内跑过 `main.py` 留下的 `src/__pycache__`
   会一直躺着，凭据闸判「包内混入目录」直接拦下发布。补 `purge_stale_caches()`，
   只清 `__pycache__` / `.pyc` / `.log`，**不动 `python/`、也不顺手删 `tests/`**
   ——真漏拷了东西，宁可让闸门当场抓住，也不要它悄悄消失。

**验证**（同一台机器，用户 site 里明明装着 rich 15.0.0）：
- 隔离环境 `main.py --doctor` → 11 项全 PASS，含「运行期隔离：isolated 模式，
  sys.path 全部落在包内（8 条）」
- 把包内 `rich` 改名 → doctor 报 `缺少：rich`，**退出码 1**。上一轮同样的场景
  报的是「9 PASS / 可发布」，因为读到了用户 site。这就是隔离生效的硬证据。
- `win32api.GetSystemMetrics` / `win32gui.GetForegroundWindow` 真调用通过

**测试自身的盲区（本轮又踩一次）**：`check(all(... for n in got))` 在 `got` 为空时
恒真；`[8.2]` 的 `sys.path` 里放了包外路径，导致 FAIL 可能来自旁边那道判定，
去掉 isolated 判定照样绿。两处都改成「先判长度/隔离变量，再判内容」后才真能红。


### 阶段 C2 前置：RPC 长连接污染（一条真 P1，不是测试噪声）

**症状**：`RPC 通道`闸门偶发失败，客户端侧 `ConnectionAbortedError: [WinError 10053]`，
重跑就好，连续多次未复现——按惯例这会被当成测试抖动放过去。

**先量化再下结论**：连跑 20 次统计到 2 次失败（10%），失败点固定在
`test_security` 的第一个调用。再用分组压测（每组 180 次）把假设拆开：

| 分组 | 分支 | 是否读走请求体 | 失败 |
|---|---|---|---|
| 带令牌 → 200 | 正常 | ✅ | 0/180 |
| 无令牌 → 401 | 鉴权失败即 return | ❌ | 1/180 |
| 错令牌 → 401 | 鉴权失败即 return | ❌ | 5/180 |
| 下划线方法 → 403 | 读完 body 才 return | ✅ | 0/180 |

差异只有一个变量：**是否读了请求体**。机制随之确定——`protocol_version = "HTTP/1.1"`
下连接保持，而拒绝分支在 `_read_json()` 之前 return，body 原封不动留在接收缓冲里；
handler 回到 `readline()`，把这段残留字节当成下一个请求的请求行去解析，连接状态从此错位。

**这比闸门挂掉严重得多**：Electron 壳用 `fetch`，连接是复用的。壳重启后握手文件里的
令牌换新，只要有一次带着旧令牌打进来，**壳之后的所有调用都落在错位的连接上**——
表现为「界面突然全哑」，而不是一条清晰的 401。

**修法**：拒绝分支先排空请求体再响应，排不空（超过 `DRAIN_LIMIT` = 1MB / 长度畸形 /
对端已断）才 `close_connection = True`。不用「一律关连接」是因为 body 未读就 `closesocket`
在 Windows 上会发 RST，把客户端的干净 EOF 变成 10053——那等于把 bug 换个地方发作。
`_body_consumed` 标记防止 `_read_json` 已读之后被二次排空卡死。

**验证**：新增 7 项断言（401 / 413 / 404 三条拒绝路径各自「同一条连接上再发一个合法请求
必须拿到 200」）。变异验证把 `_drain_body` 改成永远返回真，**三条全部变红且失败形态正是
「状态 400」**——残留 body 被当成请求行的机制被完整复现。还原后：单跑 30/30 全过，
分组压测 3 轮共 720 次 0 失败，全量闸门 26/26。

---

### 阶段 C1 + C3：依赖离线化与体积可拆

**起点问题是硬的**：`dist/` 里 `python/` 只有 pip 一个包，双击必然起不来，而闸门 3
对这件事只给 `⚠`（非阻断），于是"包能发布"和"包能运行"长期是两件事。

新增 `make_dist.py --install-deps`：把 `requirements.txt` 里**本来就写好但没人读**的
「核心 / 重要 / 可选」分档从注释里解析出来，按档推进、按档定责（核心失败即停，
重要失败只告警，可选默认不装）。`--wheelhouse` 把 wheel 连传递依赖下到 `<out>/wheels`，
`--offline` 强制 `--no-index` 安装 —— 「离线」如果会静默退化成联网，对目标机器就是假承诺。

**这一轮真正值钱的是打包链路里被顺手挖出来的 8 个 bug**，每一个都只在"真装、真跑"时才现形：

| # | 症状 | 根因 |
|---|---|---|
| 1 | 交付包里凭空多出 `data/`、`logs/` | `--doctor` 排在 DPI/单实例/崩溃钩子**之后**，"只读自检"自己写文件，随后被凭据闸判为混入隐私目录 |
| 2 | 包里 `--doctor` 只剩一句「自检脚本加载失败」 | `tools/doctor.py` 没进白名单，交付包缺自检本体 |
| 3 | 自检脚本加载失败仍 `rc=0` | 走的是 `return` 不是 `SystemExit(非零)`，上层得到一条**假绿** |
| 4 | `--tier core` 却把「重要」档也装了 | 档位循环缺 `break`，"递进"被写成"全装" |
| 5 | 验收把交付件弄脏 | 直接在 `dist/` 上跑 `main.py`，改为在临时副本里验 |
| 6 | 配置加载失败时冒出 `TypeError: check_version() missing 'cfg'` | `run_all` 按"参数值是否 None"决定传不传参；cfg 恰为 None 时退化成无参调用，把真正的配置问题盖掉 |
| 7 | 装完依赖后整包被静态资源闸判死 | `check_web_assets` 扫进了 `site-packages`，pygame docs / win32com 的失效外链被当成"我们的资源" |
| 8 | 依赖闸报 `import make_dist` 未声明 | `local_modules` 只扫 `src/`，不认 `tools/` 下的本地模块 |

**验证**：`dist_stage` 装配 + 装依赖 → 包内运行时跑 `--doctor` = 9 PASS / 1 WARN / 0 FAIL，
三道闸全过，输出 `✓ 可发布`。5 个变异（去 break、核心档不阻断、去 `--no-index`、
不剥行内注释、去 offline 守卫）全部 FAIL，还原后 PASS。
其中「去 offline 守卫」第一次**没被抓住**，补了 `--offline 无 wheel 必须失败` 用例后才红。

**C3 体积报告**（`--report`）第一次让 408.6MB 可以拆开看：`python/` 占 93.1%，
其中 `scipy` 109MB + `scipy.libs` 19MB + `PyWavelets` 8MB 全部由 **ImageHash** 一个包
拖进来（而代码里只用 `phash`，它要的只是 `scipy.fftpack.dct`）。`vosk` 51MB、`pygame` 24MB
属「重要」档。**结论：`--tier core` 是可用的第一刀，但真正的大头要动 ImageHash。**

### C1 复查：包内依赖是假的（真 P0，比 RPC 那条严重）

上一条写的「包内运行时 `--doctor` 9 PASS / 0 FAIL、✓ 可发布」是**假绿**。
触发复查的动作很朴素：不想再拿"包内能跑"当"换台机器能跑"，于是把交付目录复制到
一个全新路径，用 `env -i` 清空环境变量、`PYTHONNOUSERSITE=1`、空的 `USERPROFILE`/`APPDATA`，
再跑一遍自检。

**结果第一行就崩了**：`main.py:88 from rich.console import Console` → `ModuleNotFoundError: rich`。

逐个核实核心档 12 个包的来源，**5 个根本不在包里**：

| 核心包 | 干净环境 |
|---|---|
| PyYAML / numpy / sounddevice / pywebview / mss / Pillow / psutil | ✅ 来自包内 |
| **httpx / websockets / requests / rich / dashscope** | ❌ **不存在** |

**一个根因，两个假象。** 包内 `python/` 是**完整 Python 拷贝**，不是带 `python._pth`
的隔离版，`site` 模块照常加载 `%APPDATA%\Roaming\Python\Python312\site-packages`：

- **装的时候**：`pip install rich` 输出
  `Requirement already satisfied: rich in c:\users\lenovo\appdata\roaming\python\python312\site-packages (15.0.0)`
  —— pip 认为已满足，一个字节都不往包里装。
- **验的时候**：`--doctor` 的依赖检查看到同一份 site-packages，报「12 个核心依赖全部可导入」。

对照实验把因果钉死：同一台机器、同一个包内运行时，只加 `PYTHONNOUSERSITE=1`，
`pip install --dry-run rich` 就从「already satisfied」变成 `Collecting rich ... (310 kB)`。

**最讽刺的一点**：`verify_deps` 的 docstring 早就写着「不能用构建机的 dev 环境验 ——
dev 装了什么跟包里带什么没关系，验了等于没验」。规矩写进了注释，没写进 `env=`，等于没写。

**四处修复**：

| # | 改哪 | 怎么改 |
|---|---|---|
| 1 | `_pip` | 传 `_clean_env()`：否则 pip 拿构建机用户 site 判「已满足」而跳过安装 |
| 2 | `verify_deps` | 同样传 `_clean_env()`，并把 `PYTHONNOUSERSITE` 的实际值打印出来，让隔离这件事被看见 |
| 3 | `doctor.check_deps` | 包内运行时下不只判「能 import」，还判 `__file__` 是否落在包内；借了外部依赖直接 FAIL 并点名到目录 |
| 4 | `main.py` | `--doctor` 提前到**任何三方 import 之前**：它只依赖标准库，才能在"什么都缺"的机器上工作。原先它排在 `main()` 里，而 `main.py` 顶层就有几十个三方 import，缺 rich 时先崩在 import，用户看到的是 traceback 而不是「缺 rich」 |

**验证**（每项都有对照，不是自说自话）：

- 隔离探测：核心 12 个包 **12/12 的 `__file__` 全部落在包内 `python\Lib\site-packages`**；
- 重新装配后第三方包数 **96 → 153**，体积 432MB → 443.3MB（差的 11MB 就是那 5 个包的传递依赖）；
- 隔离环境跑 `--doctor`：**9 PASS / 1 WARN / 0 FAIL**（这一次是真绿）；
- 新增 `[14]`/`[15]`/`[16]` 三组测试 + doctor `[7]` 组，共 8 条断言；
- 变异验证 3 轮：删 `env=`（2 红）、删 `_pip` 的 `env=`（2 红）、`_borrowed_deps` 恒假（1 红）。
  其中第一轮发现 `"PYTHONPATH" not in {}` 是**空洞通过**——变异体下照样打勾，改成先判 `env is not None` 再判内容。

**仍然没做到的事**（别当成已解决）：

1. 包内 Python 依旧会读用户级 site-packages。这次只是让**构建与验收**睁着眼，没堵住运行期。
   真正根治要放 `python._pth` 走 isolated 模式，但那样 `site` 不执行、`.pth` 不生效，
   pywin32 这类依赖 `.pth` 的包可能直接坏——必须实测过才敢上。
2. 「另一台电脑」仍未被真正验证。这次是**同一台机器上的环境隔离 + 换路径**，
   覆盖了依赖完整性与路径无关性，但没覆盖缺 VC++ 运行库、缺 WebView2、杀软拦截。

### 阶段 A 收口 + 可诊断地基（A1 → A4 → B1 → B3 → A2/A3）

这一批都是"**错了不会崩、只会让人看不懂**"的改动，所以每一条都配了能抓住回归的测试，
并按 AGENTS.md 第二条做了变异验证（改坏代码 → 测试必须 FAIL）。

---

#### P0 · `main.py` 根本起不来

**问题**：`main.py:116` 从 `src.ui.webapp` 导入 `_attach_pet_no_taskbar` /
`_patch_webview2_unique_user_data` / `_start_gui_liveness_probe`，但 10-17 那次拆分
已把这三个搬到 `src/ui/main_window.py`，webapp 只 re-export 了一部分 →
**模块级 ImportError，双击就崩**。

**最贵的部分**：这说明那次拆分之后主入口**一次都没被真正执行过**（用户跑的是
`达妮娅.exe` 里的旧包）。**以后动 import 结构，先跑一次 `--help`。**

**改动**：`webapp.py` 补全 re-export，并写明这些函数现在的真正归属。

**验证**：`python main.py --help`、`python main.py --doctor` 均正常退出。

---

#### A1 · `ui.pet_backend` 配置项（开关真正通电）

**问题**：决策散在两处——`main.py` 看 `--pet-shell`，`run_webview()` 无条件先试壳。
结果是 `--pet-shell` 只对 RPC 生效，**桌宠窗照旧走 pywebview**，开关接了线没通电。

**改动**：新增 `src/ui/pet_backend.py` 作唯一决策点（命令行 > 配置 > 默认 auto）；
`main.py:_startup_supervisor` 与 `main_window.py:run_webview` 两条入口都改调它；
回退原因统一由 `shell_readiness()` 产出。

**验证**：`test_pet_backend.py` 22 项；变异（忽略命令行 / 去掉回退）均 FAIL。

---

#### A4 · 桌宠位置持久化

**问题**：位置只存在内存 `api_pet._pet_pos`，**进程一退就没**，每次开机得重新拖。

**改动**：`src/ui/pet_state.py`。**不存绝对坐标**（换屏/改分辨率会甩出屏幕外），
存「相对所在显示器工作区的比例」+ 存档时工作区矩形，恢复时映射到当前最接近的屏并夹回可视区。

**踩坑**：存用实际矩形（带边框 424×544）、取用名义 420×540 →
「工作区 − 窗口尺寸」这个分母把 4px 口径差**放大成 24px 漂移**。修法：存/取同源，统一常量。

**验证**：往返一致、跨屏越界夹回、损坏档回落默认，三组实测。

---

#### B1 · 版本号单点 & B3 · `--doctor`

**问题**：版本号三处口径不一（配置 0.1.0、安装包 v1.0、日志里没有）；
排障只能靠人肉翻日志，没有一个"先跑这个"的入口。

**改动**：`src/version.py` 单点定义，日志首行横幅；`tools/doctor.py`
（也可 `python main.py --doctor`）9 项检查，每项 PASS/WARN/FAIL 带可行动修复提示，支持 `--json`。

**关键设计**：`--doctor` 在 `load_config` **之前**执行——配置坏掉的时候正是最需要它的时候。

---

#### A2 · 壳自愈 & A3 · 退出清理

**问题**（两个都是"桌宠会消失"的剩余风险）：
  · 壳是独立进程，驱动重启/崩溃/误杀都会让它无声消失，主窗口却一切正常；
  · 退出不干净 → 孤儿 Electron 窗口滞留，且下次启动撞 userData 单实例限制
    （Electron 会**无输出地立刻退出**，本项目最难查的坑之一）；
  · 握手文件里有 RPC 令牌，不该留在磁盘上。

**改动**：
  · 守护线程每 2s 查存活，死了自动重拉并恢复生前的可见/置顶/位置；
  · **配额**：60s 内重启 5 次判定为反复崩溃 → 停手；
  · 配额耗尽时**降级**到 pywebview 窗口（本对象转为透明代理，调用方无感、不会拿到 None）；
  · `close()` 先礼后兵（quit → terminate → 杀进程树），并删除握手文件；
  · `shutdown_all()` 幂等，挂在 `main.py` 的 atexit 上。

**为什么宁可白底也不能没人**：桌宠的背景透明度是体验问题，桌宠消失是功能问题。

**验证**：`test_pet_shell_selfheal.py` 13 组；变异 8 次（配额/关闭检查/降级/清队列等）全部 FAIL、还原 PASS；
另有 `tools/pet_shell_selfheal_smoke.py` 端到端**真杀 electron 进程**验证自动恢复。

---

#### B2 · 日志轮转

**问题**：`main.py` 每次启动新建 `logs/run_<时间戳>.log`，崩溃时再落一个
`logs/crash_<时间戳>.log`，**从来没人删过**。现在盘上 8 个 crash 文件全是 0 字节——
崩溃钩子每次启动都会创建文件，哪怕没崩。跑一年就是几千个文件。

**改动**：新增 `src/log_rotate.py`。
  · 按份数：`run_*` 与 `crash_*` 各自保留最近 N 份（默认 30，配置项 `app.log_keep`）；
  · 按单文件上限：当前这次运行的日志交给 `RotatingFileHandler` 滚动（默认 8MB，
    配置项 `app.log_max_mb`）；
  · 清理在建 handler **之前**做，避免把正在写的当前文件算进"待删旧档"。

**一个易错点**：排序按 **mtime 降序**，不按文件名。文件名里的时间戳是进程启动时刻，
而 `run_` 文件是持续写入的——按名字排会删掉还在用的文件。另外空文件排最后优先被清，
它们没有任何排障价值。

**验证**：`test_log_rotate.py` 6 组；变异 5 次（删反了 / 去掉空文件优先 /
不夹负数 / keep 被吃掉 / 漏掉 crash_）全部 FAIL、还原 PASS。

**顺带收口 B1**：光有 `src/version.py` 不算单点——`app.version` 还是 0.1.0，
两处照样漂移。现已同步为 0.2.0，并新增 doctor 项「版本一致」，
漂移即 FAIL 且在提示里写明该改成什么。

---

#### B4 · 配置校验与迁移

**问题**：`load_config()` 就是 `yaml.safe_load` + 转 SimpleNamespace，**零校验**。
后果分两类：
  · 缺必需段（如 `perception`）→ `Config.__init__` 里 `self.ns.perception.vision`
    直接 AttributeError，**崩在一个完全看不出是配置问题的地方**；
  · 缺可选键 → 到处 `getattr(x, "y", default)` 静默回落，
    用户以为配了其实没生效——这是"玄学 bug"的主要来源。

**改动**：新增 `src/config_schema.py`，`Config.__init__` 里加载即校验。
  · **必需项**缺失/类型错/取值非法 → ERROR；
  · **可选项**缺失 → 补齐默认值 **并 log 一条 WARNING**（关键：不留声不叫迁移，叫静默默认）；
  · hint 一律写成「改哪个文件的哪个键」，能直接照着改，不写"请检查配置"这种废话。

**设计取舍**：**不抛异常**。配置写错是"该被大声告知"的事，不是"该让软件打不开"的事——
所以补齐默认值让它能起来，问题交给 `--doctor` 集中呈现。

**为什么只列关键项不做全量 schema**：30 个顶层段全量 schema 是维护陷阱，
改配置就得改 schema，最后变成谁都不敢动。只列会崩 / 会静默出错的。

**验证**：`test_config_schema.py` 11 组；变异 5 次（缺字段不报 / 类型检查失效 /
枚举失效 / 版本漂移失效 / 补齐不出声）全部 FAIL、还原 PASS。
其中「补齐不出声」第一次**没被抓住**——原测试只断言了返回值清单，
没断言日志。补齐动作"必须被看见"是这个模块的核心要求，已补上日志断言。

**实测**：`config.yaml` 与 `config.release.yaml` 关键项齐全，0 issues。

---

## 2026-10-17

### `5bffc68` 建立版本控制

**问题**：99,751 行代码、175 个 Python 文件，**没有任何版本控制**。任何一次改动都不可回滚，排查问题只能靠记忆。

**改动**：`git init` + 首次提交。`.gitignore` 排除 `.venv/`（692MB）、`python/`（45MB）、三个 `.exe`（35MB）、`config.yaml`、`data/`、`call_records.json`（141 条真实通话记录）。

**验证**：确认库内无密钥、无隐私数据（`git diff --cached` 检查）。

---

### `57d6101` 发布模板漏了一枚真实 Key

**问题**：`config.release.yaml` 32 个 `api_key` 里 31 个手工置空、漏了 1 个（`sticker.api_key`，智谱 GLM）。根因不是疏忽，而是脱敏方式本身不可靠——这个"发布模板"和 `config.yaml` 全文 diff 只有 **1 行**，本质是实配副本改的。

**改动**：置空该行（保留 CRLF 行尾）；新增 `tools/check_package_secrets.py`，模板出现非空凭据即 `exit 1`，`--dir` 模式同时拦 `data/`、`logs/`、`call_records.json` 等。

**踩坑**：第一次用 `sed -i` 改，1787 行 CRLF 全变 LF，文件从 62992 掉到 61159 字节，diff 显示整个文件重写。**已回滚**，改用 Python 二进制定点替换。

**验证**：三个 `.exe` 二进制里搜索该 Key 前 10 字符，**0 命中**（尚未外泄）。变异测试：塞假 Key → `exit 1`；还原 → `exit 0`。

---

### `ecd60a3` 命令白名单能被一个分号绕过

**问题**（两个缺口叠加）：

```python
if allow and head not in [...]:   # allow 为空 → 短路 → 全放行
subprocess.run(command, shell=True)  # 只校验首词，整串交给 shell
```

白名单有 `git`，模型输出 `git status; del /s /q C:\...` 照样通过。

**改动**：白名单为空即拒绝；拒元字符 `; & | \` $ > < % ^ ! ( )` 与换行；`shlex` 解析后按 basename 校验，`shell=False` 执行；cmd 内建命令单独走 `cmd /d /c`。

**验证**：8 条越权用例 + 正常执行用例全绿。变异：把元字符闸改成永不匹配 → 测试立即 FAIL。

---

### `c90d56b` OneBot 反向 WS 默认听 0.0.0.0 且无入站鉴权

**问题**：同局域网任意主机连上 WS 就能伪造 QQ 消息，消息会进聊天上下文并写入长期记忆——等于可以往记忆库投毒。

**改动**：`listen_host` 默认 `0.0.0.0` → `127.0.0.1`；新增 `_inbound_authorized`，无 token 时只允许回环，配 token 后校验 `Authorization: Bearer` 或 `?access_token=`（常时比较）；非回环 + 无 token 启动即打 error 并拒绝所有连入（`close(1008)`）。

**验证**：6 组用例全绿；变异（鉴权恒真）→ 测试必 FAIL。

---

### `a90ccf2` 安全修复差点弄坏功能（回归）

**问题**：实配 `allow_commands` 是 `['notepad','calc','explorer','start']`，其中 `start` 是 cmd 内建命令，`shell=False` 下 `FileNotFoundError`——等于把"能打开程序"改成"执行失败"。

**改动**：`_CMD_BUILTINS` 补 `start`；新增 `_norm_exe` 统一大小写与 `.exe` 后缀。

**教训**：改安全默认值之前必须 grep 实际配置，否则修漏洞会顺手制造功能回归。

---

### `2dabbf1` webapp.py 第一刀：25 个纯函数

**问题**：`webapp.py` 19,820 行，改不动。

**改动**：按"不引用任何模块级可变状态"为标准抽出 4 组纯函数：

| 新模块 | 内容 | 函数 |
|---|---|---|
| `ui_fmt.py` | 时间/路径/图片格式化 | 5 |
| `bailian_key.py` | 百炼 Key 识别与归一 | 5 |
| `wv2_env.py` | WebView2 加固、唯一 user-data 目录 | 8 |
| `notify_geom.py` | 通知浮窗几何 + 滑动动画 | 7 |

**顺带修两个真实 NameError**（新写的 `check_module_globals.py` 首次运行就抓到）：
1. `_list_speaker_devices` 尾部 `return out`，`out` 从未定义 → 走回退分支必 NameError → 被 `except` 吞 → **用户看到"扬声器列表是空的"**；
2. `slot_config._set_duplex` 用 `logger.info`，整个模块没定义 `logger` → 引导页一键配置传 `bailian`/`aliyun` 别名时必崩。

**验证**：AST 逐字比对 25/25 完全一致；`compileall` + import 冒烟 + 能力槽 41 项回归全绿。

---

### `972aef9` 15 个隐式全局收敛为 `STATE`

**问题**：`webapp.py` 有 15 处模块级可变全局，同时被类方法和模块级函数读写。**上一刀真的引入了 bug**：`wv2_env._safe_js_api` 里 `global _WV2_CONTROLS` + 下标赋值，函数迁走后这个名字在新模块不存在 → `NameError` → 被紧挨着的 `except: pass` 吞掉 → **WebView2 控件永远注册不上**，后端原生推送全部静默失效。

**根因**：我的自由变量分析脚本里可变全局清单是手工维护的，只写了 `_WV2_BC_EXECUTOR`，漏了 `_WV2_CONTROLS`。**手工清单一定会漏，这是方法论错误。**

**改动**：新增 `src/ui/ui_state.py`（`WindowState` + 进程级单例 `STATE`），61 处读写点改为 `STATE.<field>`，删掉 15 条 `global` 与 15 个定义行。检查器补硬规则：`global X` 但模块顶层没定义 X → 直接报错。

**顺带复活一个失效测试**：`tools/test_key_unify.py` 里硬编码 `G:\ai对话\11`，项目搬家后一直跑不了。改成基于 `__file__` 的相对路径，41 项断言全部复活并通过。

---

### `08cabbd` 桌宠域拆为 `PetAPI` mixin

**改动**：`_pet_*` 9 个 Win32 函数 → `src/ui/pet_window.py`（262 行）；21 个 js_api 方法 → `src/ui/api_pet.py`（529 行）。`class CharacterAPI(PetAPI)`。

**三个引用错误**：
1. `_wt` 未定义 —— **原始代码的 bug**：`pet_drag_start`/`pet_move` 用 `_wt.RECT()`，但整个文件只有另一个函数内部 import 过 → 桌宠拖拽必然 NameError → 表现就是"按下拖不动"；
2. `_u32` 顶层引用 —— **这次拆分引入的**：`webapp.py` 末尾有模块级 `argtypes` 声明，删掉 `_u32` 定义后 **import 的瞬间就崩**，而 `compileall` 完全通过；
3. `_CLR_CRASH_GUARD_INSTALLED` —— 上一轮遗留：`wv2_env` 里 `global` 它，变量却留在 `webapp`。

**检查器两次迭代**：先加了"顶层语句"检查（要跳过 `__main__` 自测块，否则误报）；再修掉"已定义"集合的判定——原来用 `symtable.get_symbols()`，`_u32.Attr = 1` 这种属性赋值会让它误判为已定义，**变异验证第一次因此误报通过**。改用 AST 只收集真实绑定后才抓住。

---

### `aab4c70` 窗口域拆分（唯一难点是循环 import）

**改动**：`main_window.py`（1,348 行：本地静态服务、GUI 心跳/卡死 dump、4 个 `create_*_window`、`run_webview`）+ `api_window.py`（`WindowAPI` 17 个方法）。`webapp.py` 18,371 → 16,616 行。

**难点**：`create_main_window` 等函数内部要 `CharacterAPI()`，而这两个类还在 `webapp.py`——模块级 import 会成环。解法是**函数内延迟 import**（4 处，带 `PLC0415` 注释）。

---

### `5fa19d8` 通话链路补 22 项冒烟

**问题**：`webapp` 通话链路零测试覆盖，而其中几个逻辑一旦改错，用户直接感知、日志看不出来。

**覆盖**：`_finalize_device_list` 7 项（WDM-KS 剔除、空名端点剔除、31 字符截断重名丢弃、同名多 host 合并、`id` 兼容老配置、顺序稳定）；`_features_to_engine_config` 15 项（含 **解耦开关下"云端语音+本地看屏"必须并存**、`vision_models.enabled` 恒 True——这是 2026-10-04 实锤过的 bug）。

**踩坑**：变异验证第一次是**假通过**——我只改了 1 处 `vm["enabled"] = True`，而测试没覆盖解耦分支。补断言后全量替换，3 项 FAIL 才被抓到。

---

### `c566bc9` 静默异常：我第一版报告写重了

**问题**：原报告称"`cloud_omni_realtime.py` 有 **41 处**吞异常，通话失败无从排查"。实际核下来，那是把**静默总数**当成了**真空吞**。

**实测**（`tools/check_silent_except.py` 区分"带注释的有意识降级"与"无注释的真静默"）：

| 范围 | 静默总数 | 真静默 |
|---|---|---|
| `cloud_omni_realtime.py` | 49 | 1（`queue.Empty`，正常） |
| `dialogue.py` | 17 | 0 |
| `tts.py` | 17 | 8（7 处临时文件清理） |

**唯一值得修的 1 处**：`tts.__init__` 解析双工 TTS 配置失败时静默退回默认值 → 缓存/预取/stale 阈值全变，用户只感到"变卡"，日志零线索。已补 `logger.debug(exc_info=True)`。

---

### `add925a` 生产代码遗留旧盘符

**问题**：项目从 `G:\ai对话\11` 搬到 `D:\Denia` 后，`src/tts/cloud_clone_bridge.py` 仍写死旧路径，且第 41 行在 **import 阶段**执行 `CACHE_DIR.mkdir()`：

```
>>> import src.tts.cloud_clone_bridge
FileNotFoundError: [WinError 3] 系统找不到指定的路径。: 'G:\\'
```

**改动**：`CACHE_DIR` / `DANIYA_REF` 默认值改为相对仓库根（参考音指向确实存在的 `assets/voices/daniya_ref.wav`）；`mkdir` 改为惰性调用，目录不可写只告警降级。

**同时纠错**：原报告"167 处 `open()` 未指定 `encoding`，读中文 JSON 必炸"是粗 grep 的产物（把二进制模式、`Image.open`、`pa.open` 全算进去了）。AST 复核后 **`src` + `main.py` 实为 0 处**，`tools/` 下 2 处已补。

---

### `3fb4a71` 看板娘根本不在仓库里

**问题**：`src/ui/web/live2d/rig` 以 gitlink（mode `160000`）记录，但仓库**没有 `.gitmodules`** → clone 后只得到一个空目录。而它是**运行时依赖**：

- `src/ui/web/index.html:291` — 主界面右下角 `<iframe src="live2d/rig/index.html?v=2">`
- `src/ui/main_window.py:868` — 桌宠透明窗加载同一页（`?bare`）

**后果**：任何人重新 clone 或按目录打包，看板娘和桌宠窗一起消失，日志里只有一条 404。本地 961 行自研改造（`__rigControl`、`__petInteract`、`petShowBubble`、`?bare` 透明模式、FX 偏移持久化）也不在任何历史里。

**改动**：移除 gitlink，9 个文件改为普通文件跟踪（先导出 1251 行补丁核对，上游只有 1 个提交 `d488258` 可重取）；新增 `LOCAL_CHANGES.md` 记录上游基线与每项改造；新增 `tools/check_web_assets.py`（HTML 的 `src`/`href` 存在性断言）。

**验证**：变异——把 `rig/index.html` 临时挪走 → 精确报出 `index.html` 与 `index.legacy.html` 两处引用并 `exit 1`。

---

### `1207233` 打包闸从单向变双向

**问题**：原闸只查"不该带的"（`data/`、`call_records.json` 等）。看板娘事故是"该带的没带"，一条都拦不到。

**改动**：`--dir` 模式在隐私检查后追加"包内 HTML 引用的本地资源必须存在"（复用 `check_web_assets.scan_dir`，根相对路径跳过以免误报，`--no-assets` 可关）。

**验证**：三组假包——含隐私目录 → `exit 1` 逐项列出；正常 → `exit 0`；删掉 `rig/index.html` → `exit 1` 并指出两处引用。

---

### `206cc47` 导入冒烟 + 依赖对账

**问题**：静态检查证明不了"模块能被加载"，当天已经踩到两次只在 import 时才暴露的问题。

**改动**：
- `tools/smoke_import.py`：每模块独立子进程 import、超时即杀、按错误类型归类；配 `import_allowlist.txt`（带原因），`--strict` 只对"非预期失败"报错。
- `tools/check_requirements.py`：AST 真实 import → `packages_distributions` 映射发行包 → 与 requirements 对账；支持别名表（`clr`→pythonnet、`win32*`→pywin32）、注释排除、`# not-pip:` 登记、运行时注入插件豁免。

**结果**：155 模块 / 149 通过 / 6 已知可选。修掉两个漏声明：`pythonnet`、`websocket-client`。新增 `requirements-optional.txt`（A 段可 pip、B 段 `not-pip` 登记，避免 `pip install -r` 直接失败）。

**验证**：三次变异（删白名单行、注入 import 期崩溃、删 `not-pip` 行）全部被抓。

---

### `01d185c` CLR 崩溃守卫从未装上

**问题**：`_install_clr_crash_guard` 一次都没生效，两层原因叠加：

1. 只写 `import System`，而 pythonnet 3.x 必须先 `import clr` 再 `AddReference` → 恒定 `ModuleNotFoundError`；
2. 调用点在 `import webview` 之后、`create_window` 之前，而 **`import webview` 不会加载 clr**（实测 `'clr' in sys.modules == False`）。

结果：每次启动只留一条 warning，兜底形同不存在。

**改动**：守卫默认"CLR 未加载就返回"（不给测试平白拉起 .NET）；新增 `force_load` 参数，建窗前主动 `import clr` + `clr.AddReference("System.Windows.Forms")`（与 pywebview 自身一致）；修正 `UnhandledExceptionMode` 取法（是 `System.Windows.Forms` 的类，不是 `Application` 属性）。

**验证**：默认参数下 `_CLR_CRASH_GUARD_INSTALLED` 仍为 False 且 clr 未被加载；`force_load=True` 后变 True。

---

### `5b348a5` 6 处无界队列改有界丢最旧

**先纠错**：13 处 `Queue()` 里真正需要处理的只有 6 处。`event_bus` / `tts` 用 `await put()`（asyncio 阻塞投递）天然背压，加 `maxsize` 反而可能死锁；`webapp` 两处是函数内临时队列。

**新增 `src/queue_util.py`**：`put_drop_oldest` / `put_drop_oldest_async`，满则丢最旧、绝不阻塞。为什么不是"丢新"——那会让**最新一句话永远进不来**。

| 位置 | 上限 | 说明 |
|---|---|---|
| `voice/audio_io.py` | 32 帧 ≈16s | **最严重**：sounddevice 回调每 0.5s 无条件 put，识别线程被推理拖住就无界涨（32KB/s） |
| `llm/cloud_omni_realtime.py` | 256 块 | 实时音频增量 → 播放线程 |
| `channels/gewechat.py` | 200 条 | HTTP 回调线程 → 主循环 |
| `ui/chat_overlay.py` | 32 条 | tkinter 轮询 |
| `companion/course_recorder.py` | 500 段 | 转写线程 → `_tick` |
| `ui/api_pet.py` | 20 条 | 桌宠窗 250ms 轮询 |

**验证**：`src/tests/test_queue_util.py` 14 项全绿；变异（丢最旧→丢新）4 项 FAIL。

---

### `a4a6a38` 交付目录组装器 + launcher 去旧盘符

**问题**：项目根同时是开发目录——`config.yaml`（真实 Key）、`call_records.json`（141 条真实通话）、`data/`、`.venv/`（692MB）、290 个一次性脚本都在同一个文件夹。只要打包方式是"复制文件夹"，这些就会一起进安装包。

**改动**：新增 `tools/make_dist.py`，白名单是唯一清单来源，默认 dry-run：

- 目录：`src` / `assets` / `prompts`（+ 可选 `python` 运行时）
- 文件：`main.py`、`launch_companion.py`、`run_webui.py`、`README`、两个 requirements
- 改名：`config.release.yaml` → `config.yaml`
- `tools/` 只带 `clone_danya_voice.py`——运行时真会调用它（`proactive.py:5097`、`cloud_omni_realtime.py:2218`），其余 290 个是开发脚本
- 输出目录非空且未加 `--force` 时**拒绝执行**（只覆盖同名文件，绝不删多余文件）

**实测发现（重要）**：内置 `python/Lib/site-packages` 里**只有 pip**，没有 yaml，也没有 `.pth` 指向 `.venv`，`main.py` 里同样没有 site-packages 引导。**单靠 `python/` 打出来的包双击起不来**——现有发布流程必然依赖「安装环境.exe」往运行时补装依赖。已加成第三道闸门：默认告警，`--strict-deps` 下失败。

**顺带修打包闸误判**：`FORBIDDEN_DIRS` 原本全树匹配目录名，把内置运行时里天然存在的 `python/Lib/venv`、`pip/_internal/operations/build` 判成隐私目录，正常包直接不合格。改为只看深度 2 以内。变异验证：`dist/data/` + `dist/logs/` → `exit 1` 并逐项列出；删掉 → `exit 0`。

**顺带重写 `launch_companion.py`**：旧版写死 `glob.glob("G:/ai*")` 和别人机器上的 `C://Users//Administrator/.../python.exe`。改为按 `__file__` 定位 + 优先内置 `python/python.exe`，日志改到 `logs/launcher.log`。

**验证**：dry-run 65.3MB / 1334 文件；实际组装成功；闸门 1 通过、必需文件齐全、闸门 3 正确报出依赖未装。

---

### `2f14c0a` 图标/任务栏 + 置顶 64 位句柄（用户反馈：像 python 脚本、切窗卡死、背后白底）

**① 任务栏显示 Python、没有图标**
仓库里本来就有 `app.ico`（30KB），但从未被使用。两处修：
- `main.py` 早期设 `SetCurrentProcessExplicitAppUserModelID("Daniya.DesktopCompanion.1")`——不设时任务栏按 python.exe 归组；
- `webview.start(..., icon=app.ico)`，pywebview 在 `winforms.py:242-244` 会设给窗体。

实测：`GetCurrentProcessExplicitAppUserModelID` 读回一致；窗口 `WM_GETICON` 返回非零句柄。

**② 切窗卡死：置顶保活每秒跨线程写 WinForms 属性**
`_pet_topmost_keepalive` 每秒调 `_pet_apply_topmost()`，原实现优先 `STATE.pet_win.on_top = ...` → `form.TopMost = ...`（`winforms.py:1006`）。WinForms 控件属性不允许跨线程访问，与切窗（激活变化）叠加时容易卡死。改为**永远优先纯 Win32 `SetWindowPos`**。

**③ 顺带查出：Win32 置顶一直没成功过**
改完实测 `SetWindowPos` 返回 0、`GetLastError=1400`（无效句柄）。根因是 **ctypes 没声明 argtypes**：`HWND_TOPMOST=-1` 按 C int 传成 32 位 `0xFFFFFFFF`，不是 64 位全 1 的伪句柄。

这解释了原代码为什么「优先用 on_top」——不是偏好，是 Win32 那条路一直失败。现在在 `pet_window` 里声明齐 `SetWindowPos / FindWindowW / GetWindowRect / GetWindowLongW / SetWindowLongW` 签名，并用 `_hwnd_arg()` 把 -1/-2 包成 `c_void_p`；`agent_tools._win32_focus_hwnd` 的 TOPMOST 翻转兜底同样修了。

实测：置顶 `set=True` 且 EXSTYLE 含 `WS_EX_TOPMOST`；取消后 `set=True` 且该位清除；last error = 0。

**④ 白底长方形：四条原生路线实测全部不可行（只记录，未改行为）**

新增 `tools/diag_pet_window.py`：起同参数窗口 → 截屏取角落像素 → 与"藏窗后的真背景"逐点比对。

| 方案 | 结果 |
|---|---|
| 现状 `transparent=True` | 四周均匀 `#F0F0F0`（WinForms 客户区底色） |
| `WS_EX_LAYERED` + colorkey | 整片变 (32,32,32) 实色，桌面透不出来 |
| `WS_EX_NOREDIRECTIONBITMAP` | 无变化，仍 `#F0F0F0` |
| `DwmEnableBlurBehindWindow`（空区域） | 变 (255,255,255)，仍是填充色 |
| `SetWindowRgn` 圆角裁剪 | 裁掉部分变纯黑，比白底更糟 |

原因：WebView2 是 DirectComposition 硬件合成面，宿主窗体一旦分层/裁剪，合成链就断，只剩填充色。`_pet_apply_region` 曾实现并接入，实测四角变黑后**已回滚**。

**教训**：`transparent=True` 在 pywebview 的 Windows 后端只是"把页面背景设透明"，不等于桌面透明。验证窗口透明度必须**截屏 + 与藏窗后的真背景逐像素比对**，光看"有没有变黑"会误判（我一开始把 240,240,240 判成"非白"，差点据此下错结论）。

---

## `e46f21b` 单实例锁 + 壳层前置（2026-10-18 00:40）

**问题**：全仓搜不到 `CreateMutex`——双击两次图标就是两个进程、两只宠物、两套后端，互相抢麦克风和麦克风设备。
顺带发现 DPI 感知实测值为 **0**（不感知），而 `notify_geom.py`、`create_desktop_companion` 等多处注释都写着"进程已 SetProcessDPIAware"。

**改动**：新增 `src/win_shell.py`，四个能力（DPI 感知、读回感知级别、单实例、唤起已有窗口），
在 `main()` 最前面调用——**必须在任何窗口创建之前**，否则系统静默忽略且不报错。
`--no-gui` 是控制台工具模式，跳过单实例检查。

**验证**：`src/tests/test_win_shell.py` 6 项全绿（DPI 0→2；同名锁第二次 False、子进程 False、异名锁 True）。

**没有做的事（诚实口径）**：我原以为 DPI 不感知会导致高缩放文字模糊，实测 `devicePixelRatio` **已经是 1.5**、
开关 DPI 感知窗口矩形完全一致——页面本来就按原生分辨率渲染。所以这条的收益是"Win32 坐标与注释假设一致"，
不是修可见缺陷。**不把它写成"修好了模糊"**。

---

## `86fa3df` AGENTS.md + 统一门禁（2026-10-18 00:45）

**问题**：闸门散在 `tools/` 下十几个脚本里，靠人记必漏——这个项目已经漏过一次（看板娘整个目录没进版本控制）。

**改动**：`tools/check_all.py` 编排 16 项闸门（`--fast` 跳过慢项，约 5 秒回环），只做编排与汇总，
检查逻辑仍以各自脚本为单一真值源。`AGENTS.md` 把踩过的坑写成条款，重点是：
证据口径、变异验证、Windows 三条硬约束、WebView2 透明已确认的结论、环境坑、交付红线。

**验证**：16 项全 PASS；变异（临时挪走 `check_web_assets.py`）→ `exit 1` 并点名失败项，还原 → `exit 0`。

---

## `6d75b24` 换壳可行性 PoC（2026-10-18 00:52）

**问题**：桌宠背后那块白底，之前一直被当成"参数没调对"。

**查到底**：pywebview 的 Windows 后端是 WebView2 **windowed hosting**，不提供每像素 alpha。
用**最小用例**（完全不含项目页面）逐个手测 8 种原生手法：

| 手法 | 角落像素 |
|---|---|
| 基线 `transparent=True, frameless=True` | `(240,240,240)` |
| `WS_EX_LAYERED` + `LWA_ALPHA(255)` | `(240,240,240)` |
| `WS_EX_LAYERED` + `LWA_COLORKEY(#F0F0F0)` | `(32,32,32)` |
| `Form.TransparencyKey = #F0F0F0` | `(32,32,32)` |
| `Form.BackColor = ARGB(0,0,0,0)` | `(240,240,240)` |
| `WS_EX_NOREDIRECTIONBITMAP` | 无变化 |
| `DwmEnableBlurBehindWindow` | 白色填充 |
| `SetWindowRgn` 裁剪 | 裁掉处纯黑 |
| `WS_EX_LAYERED` + `LWA_ALPHA(0)` | 整窗消失（**反证：DWM 合成链路是通的，缺的只是顶层 alpha**） |

**正证**：同一份 `rig/index.html?bare=1` 换到 Electron 宿主，前端一行未改，
`window.pywebview.api` 由 `shell/preload.js` 的 Proxy shim 顶替，结果——窗口隐藏态与显示态三处角落**逐点相等**，
中心不同（人物在渲染）。截图里人物背后是后面的应用窗口文字，不是白底。

**踩坑两条**（已写进 AGENTS.md）：本机 shell 带 `ELECTRON_RUN_AS_NODE=1` 会让 Electron 退化成 node（`app` 未定义）；
读子进程 stdout 用裸 `readline()` 会无限阻塞，第一次跑直接挂满 180 秒、且 `python` 不加 `-u` 时被杀后一个字都不留。

**产出**：`产品化路线_2026-10-18.md`（证据表、三方案代价、四步可回滚迁移与验收）。

---

## `4792f40` 换壳第 1、2 步：RPC + 壳侧 shim（2026-10-18 01:20）

**为什么不是直接开始搬窗口**：透明只是外观。换壳真正会翻车的是"页面调用的
`window.pywebview.api` 没人实现"——rig 页面有几十处这样的调用。把方法在壳里重写一遍，
必然与后端漂移；正确做法是**让两条通道指向同一个 `char_api` 实例**。

**Python 侧**（`src/ui/rpc_server.py`）：
只绑 `127.0.0.1`、端口随机、每次启动生成令牌；只允许公开成员（下划线/dunder 一律拒绝）；
请求体上限 32MB；业务异常原样回报（不吞）；不可 JSON 化的返回值降级为字符串并标 `degraded`。
`main.py` 新增 `--rpc` / `--rpc-handshake`，启动后把 `{url, token, web_base}` 写进握手文件
（先写临时文件再 rename，避免壳读到半截 JSON）。

**壳侧**（`shell/preload.js`）：Proxy 把任意 `api.xxx()` 转发到 RPC。
**失败一律抛异常，不静默返回 null**——页面大量 `await` 之后判断返回值，
静默 null 会被当成"功能正常但没有数据"，是最难查的一类 bug。

**验证（两层）**：
1. `src/tests/test_rpc_server.py`：19 个方法两条通道结果**逐一比对完全一致**；
   缺/错令牌 401、私有方法 403、不存在 404、超大体 413、OPTIONS 204、
   业务异常 500 保留类型、不可序列化降级、`stop()` 后端口关闭。
   变异（把 `_authorized` 改成恒真）→ 4 项失败，还原 → 全绿。
2. `tools/rpc_bridge_smoke.py` 连续两次通过：

```
PROBE {"hasBridge":true,"psdType":"function","title":"看板娘[OK]","psdLen":4187477}
后端收到 RPC 调用：['get_live2d_psd']
窗口真透明（角落与背景差 0/0/60，背景噪声 70，容差内）
```

**踩到的假阳性（重要）**：第一版用 **stdin** 给壳发 `hide`/`show`。Windows 下管道里的 stdin
收不到数据，命令全丢 → "隐藏后抓屏"抓的其实是显示态 → 两次采样一样 → 得出"透明 ✅"。
现在命令走 TCP 且必须有回执，拿不到回执直接判失败。已写进 AGENTS.md。

**顺带修正判定口径**：桌面背景本身会变（壁纸动画/背后窗口刷新），严格"逐点相等"会把真透明
判成失败（第二次跑就出现了 14 级差）。改为先连续抓两次隐藏态量化噪声，再看显示态是否落在
噪声内；并单独检测 `#F0F0F0`（WinForms 客户区底色）作为反面特征。

---

## `6801aee` 桌宠窗换壳（2026-10-18 02:10）

按"只切桌宠窗"实施。核心约束是**不能改调用方**：`api_pet.py` 里到处是
`self._pet_win.show() / .hide() / .on_top / .x / .y`，所以 `src/ui/pet_shell.py`
用**鸭子类型**冒充 pywebview Window，只实现真正被用到的那几个成员；
`native` 返回 None，让 `pet_hit_rect` 里的 `hasattr` 安全跳过。

`run_webview` 里两条路径二选一，**任何一步不满足就回退 pywebview**——
这个回退有专门测试（`test_pet_shell_fallback.py`），否则没装 `node_modules`
的机器上用户会连人都看不到。

### 迁移中踩到的三个坑

1. **页面会改写 `document.title`**。所有 Win32 能力（拖动、置顶、截屏隐身）都靠
   `FindWindowW(None, "达妮娅看板娘")` 定位窗口，而 rig 页面会把运行状态
   （`看板娘[BARE]/[OK]`）写进标题。实测 `_pet_find_hwnd()` 直接返回 None，
   置顶/拖动全部静默失效。修法：壳里拦 `page-title-updated` 并固定标题。
2. **Electron 的 userData 目录单实例限制**：残留一个实例时，新进程**无输出地 rc=-1 退出**，
   看起来像"启动失败但没有任何报错"。生产用固定 profile，测试必须传独立目录。
3. **`--url` 开关名会让 Electron 立刻退出**（rc=-1）。页面地址改走握手文件的 `web_base`。

顺带一条判断口径：**置顶不要信 Electron 的 `isAlwaysOnTop()`**——实测那个窗口上
`setAlwaysOnTop(true)` 之后它仍回报 0。改为读写 Win32 `WS_EX_TOPMOST`，并用
`SetWindowPos` 兜底（`argtypes` 已声明，避免 `-1` 被截断成 `0xFFFFFFFF` 那个老坑）。

### 验证

`tools/pet_shell_smoke.py` 11 项，连续三次全通过：

```
页面渲染就绪（title='看板娘[OK]'）   页面侧调用结果：'LEN:4187477'
设为置顶后读回 True / 取消后读回 False   move 生效（200,160）
与背景一致像素 76.2%；接近 #F0F0F0 像素 0.1%
```

透明判定这轮改了两次口径，两次都是被假象骗：
- **角落采样会撞到角色本体**（实测左下角差值 195，那是头发不是底色）→ 改整区域逐像素统计；
- **`#F0F0F0` 单看颜色会误伤**（浅色桌面本身就是 240）→ 只在"与背景差得远"时才算失败。

---

## 壳的 GPU 回退：崩溃后自动退让到软件合成（2026-10-18 11:45）

**问题**：质检把壳判成"本机不可用"并降级为实验特性。但它只是**起不来**，不是**做不到**——
壳存在的唯一理由就是真透明，判死之后桌宠只能回白底，等于把这个功能废掉。

**查的过程（结论来自实测，不是猜）**：

| 开关组合 | 进程 | 画面 | 透明 |
|---|---|---|---|
| 无 | ✗ `FATAL: GPU process isn't usable` | — | — |
| `--disable-gpu` / `--use-angle=swiftshader` / `--disable-gpu-compositing` 单独 | ✗ 照样崩 | — | — |
| `--in-process-gpu` | ✓ 活 | ✗ **一帧不出** | — |
| `--in-process-gpu --disable-gpu-compositing` | ✓ 活 | ✓ 出帧 | ✓ 真透明 |

**改动**（`src/ui/pet_shell.py`）：
- 新增 `GPU_FALLBACK_FLAGS` / `GPU_CRASH_MARKS`；
- `_launch()` 先按原样启动，`CONTROL` 等不到且输出命中崩溃特征时，带开关**重试一次**并记住状态；
- `_read_loop()` 维护 `_recent` 环形缓冲（`_wait()` 会把行取走，崩溃原因得另存一份才能判断）。

不一律带开关跑：GPU 正常的机器不该白吃软件合成的性能损失。

**验证**：
- `DANIYA_REQUIRE_SHELL=1 tools/pet_shell_selfheal_smoke.py` → PASS（真进程崩溃 → 自动恢复，
  `页面渲染就绪` 等的是页面自己写 `看板娘[OK]`，即 Live2D 真的画出来了）；
- 新增单测 [15] + 变异（改成 `if False` → 4 项 FAIL）；
- `tools/check_all.py` → **27 项全部通过，0 项跳过**（`壳自愈端到端` 从"跳过"变"真跑通"）；
- `tools/_qc_shell_transparency.py` → 三关全过，对照组（同位置纯绿不透明窗）证明判据有区分力。

**这轮最贵的教训**：判据先自证，再下结论。
1. 第一版用 `capturePage()` 的 alpha 当"透明"证据，加 `--opaque` 对照一跑就废——
   `transparent:false` 时它照样返回 alpha=0，**根本没有区分力**。
2. `--in-process-gpu` 单独用是陷阱：进程活、`did-finish-load` 也打了、`IsWindowVisible` 正常，
   但屏幕是空的。**只看进程存活会误判成"修好了"。**
3. `WindowFromPoint` 返回的是 Chromium 的**子** HWND，和顶层 HWND 比永远不等 → 要 `IsChild`。
4. Win32 窗口必须**建窗与消息泵同线程**，跨线程 `GetMessage` 收不到 `WM_PAINT`，底色铺不上。
5. 两个自建窗口不能共用一个窗口类名：第二次 `RegisterClassW` 失败，画刷/回调会串。
6. 新版 Electron 的透明走 DirectComposition（`WS_EX_NOREDIRECTIONBITMAP`），
   没有 `WS_EX_LAYERED`——按"必须有 LAYERED"判会误报"不可能透明"。

---

## 桌宠后端默认值改回 auto（2026-10-18 12:10）

**问题**：上一轮把壳判成"本机不可用"、默认降到 webview。但自动退让落地后，
"默认不试壳"等于把壳存在的唯一理由（真透明）默认关掉——AI 桌面伴侣的白底
看板娘一直是这个项目最显眼的未兑现承诺。

**改动**：
- `src/ui/pet_backend.py` `DEFAULT = WEBVIEW` → `AUTO`，注释写清"为什么改回来"
- `src/config_schema.py` / `config.yaml` / `config.release.yaml` 缺省值同步为 `auto`
- `main.py` 帮助文案、`src/ui/main_window.py` 的 `run_webview()` 形参默认值同步
- `tools/doctor.py` `_shell_required()`（二态）→ `_shell_mode()`（三态）

**必须一起改的两处，否则是半截接线**：
1. **doctor 必须和生产行为一致**。改完默认值才发现 doctor 的 `probe_shell()`
   仍是裸启动，于是它报 `[WARN] 本机起不来：GPU process isn't usable`，
   而用户真实启动是好的——自检工具又骗了一次人。已改成复刻生产的退让逻辑，
   成功时点明"走了 GPU 回退开关"。
2. **RPC 随默认值常开**（`wants_rpc(DEFAULT)` False → True），测试断言同步
   改为"默认后端要起 RPC"，并注明这是 auto 的代价而不是漏改。

**验证**：`test_pet_backend` / `test_config_schema` / `test_doctor` 全 PASS；
`tools/doctor.py` → `[PASS] Electron 壳 配置为 auto，实起一次通过（走了 GPU 回退开关）`；
`tools/check_all.py` → 27 项全过。

**变异验证**：`DEFAULT` 改回 `WEBVIEW` → `test_pet_backend` FAIL 3 项；
doctor 去掉回退重试（`if False`）→ 重新变成 `[WARN] 本机起不来`（假红）。

---
## 待办（需要你决策）

| 项 | 现状 | 需决定什么 |
|---|---|---|
| **桌宠白底（换壳）** | ✅ 第 1~3 步已完成：RPC + 壳侧 shim + 桌宠窗改由 Electron 承载（`--pet-shell`），主窗口仍在 pywebview | 是否把 `--pet-shell` 设为默认开启；以及第 4 步（主窗迁移 + electron-builder 打包）什么时候做 |
| **tkinter 两功能不可用** | 内置运行时无 `Lib/tkinter`、无 `_tkinter.pyd`、无 `tcl/`，OCR 校准与对话悬浮层在任何机器上都返回"不可用"（代码优雅降级，不崩） | 补 tcl/tk（+10MB，版本须严格匹配）／改 WebView2 浮层／隐藏入口 |
| **交付物依赖安装** | `tools/make_dist.py` 已能产出干净目录（65.3MB），但内置运行时只有 pip，必须再执行 `dist/python/python.exe -m pip install -r requirements.txt`（或走「安装环境.exe」）才能启动 | 这段安装是留在安装器里，还是做成 `make_dist.py --install-deps` 一步到位 |
| **打包方式** | 目前是"便携目录"，`dist/` 已在 `.gitignore` 内 | 若迁 Electron，则整体转为 electron-builder + NSIS |

---

## 项目级经验（踩过的坑，别再踩）

1. **`sed -i` 会洗掉 CRLF**：中文 Windows 项目的 YAML/配置文件，改单行用 Python 二进制定点替换。
2. **`compileall` 通过 ≠ 能加载**：`webapp.py` 顶层 `argtypes` 引用已删名字，编译全绿、import 必崩。每次拆分后必须真实 import 一次。
3. **手工维护的"可变全局清单"一定会漏**：漏了 `_WV2_CONTROLS`，代价是 WebView2 控件永远注册不上且无日志。用工具扫，不要靠记。
4. **测试"通过"可能是假的**：变异验证要确认真的跑在变异体上（`/tmp` 在 Git Bash 和 Windows Python 下不是同一个目录，`cp` 失败时 `;` 让 `$?` 变成 cp 的退出码）。
5. **数字对不代表结论对**：13 处无界队列只有 6 处该改；167 处缺 encoding 实为 0 处。报数前先确认口径。
6. **修安全默认值前先 grep 实际配置**：白名单里有 `start`，`shell=False` 会把它从"能开程序"变成"执行失败"。
7. **HTTP/1.1 长连接上"读 body 之前就 return"必炸**：拒绝分支（401/413/404）不排空请求体，
   残留字节会被下一个 `readline()` 当成请求行。症状是客户端 `WinError 10053` 或莫名的 400，
   且**只在连接被复用后才现形**——单请求测试永远抓不到。判据：只要分支在读完 body 前返回，
   就必须排空或关连接。
8. **偶发失败先量化再定性**：这轮如果直接重跑放过，"偶发"就会被归档成抖动。连跑 20 次拿到 10%
   失败率、再分组 180 次定位到唯一变量（是否读 body），才配得上"找到根因"这四个字。

---

## 依赖档位的参数名骗人：`upto="optional"` 静默等于 `important`（2026-10-18 12:20）

**问题**：质检第七节/P3 唯一"当轮没改"的一条，现在收口。`make_dist.py` 的
`install_deps(upto="optional")` 表现和 `important` 一模一样——optional 档被
`upto != "all"` 挡在门外。三层叠加：

1. **门**写死 `upto != "all"` → 传 `"optional"` 的人拿到 `important` 的行为；
2. **入口** CLI `--tier` 的 choices 里没有 `optional` → 命令行根本传不进来；
3. **容错**缺失 → `upto="optinal"`（拼错）不装可选项也不报错，打包现场才发现。

共同点：**参数名承诺了一件代码不做的事**。

**改动**（`tools/make_dist.py`）：
- 新增 `normalize_tier()`：`all` 收敛为 `optional`（别名不是独立档位）；
  未知值当场抛 `ValueError`，错误信息点名合法值；
- `tier_specs()` 走 `normalize_tier()`；`install_deps()` 的门改为
  `tier not in _TIER_ORDER[:index(wanted)+1]`，且校验在第一个 pip 之前；
- CLI `--tier` choices 补 `optional`。

**验证**：`src/tests/test_make_dist_deps.py` 新增 `[22]`~`[25]`——
`optional` 真装且含更低档、`all` 与 `optional` 等价、五种拼错全部抛
（`"OPTIONAL "` 这种大小写空白的合法值要接受）、CLI choices 含 optional。

**双变异验证**（判定看测试末尾汇总行，不 grep `✗`——被测代码自己打印的
拒绝理由里也带 `✗`，上一轮就这样误判过一次）：

| 变异 | 抓到它的 | 结果 |
|---|---|---|
| 档位门退回旧语义 | `[22]` | FAIL |
| `normalize_tier` 静默回落 | `[24]` | 7 项断言全红 |

变异 1 的细节值得记：`upto="core"` 时它放行了「重要」档，但 `[12]` 没报警——
因为 `install_deps` 还有第二道门 `if tier == upto: break`。两道门是冗余设计：
360 行的门负责"跳过时告诉用户为什么"，380 行的 break 负责硬停止。
`[12]` 守 break（对应"`--tier core` 却装了重要档"那次真回归），`[22]` 守门。

**真打包核对**：改完后真打一次包（输出到临时目录），解包确认包内
`DEFAULT = AUTO` / `pet_backend: auto` / `GPU_FALLBACK_FLAGS` 都在——
分类安装的"分类"现在名副其实，前面两轮的成果没被挤掉。

**教训**：参数名是接口的一部分。当调用方按名字理解语义、实现却不按名字办事，
错的是实现；而当这个错还要靠拼写运气才暴露时，它早晚会在打包现场兑现代价。

---

## C10：代码签名管线 `--sign`（2026-10-18 13:5x）

**问题**：C4 只做到"doctor 能告诉你没签名"，签名这件事本身仍然全手工——
出包之后要人肉开命令行找 signtool、拼参数、签完还不一定核验。等真证书到手
再建管线，等于把最贵的调试留给发布当天。

**改动**（`tools/make_dist.py` + 测试）：
- `--sign`（须连 `--installer`，无包可签即口径错误硬失败）；
- 凭证三选一走环境变量（`DANIA_SIGN_SUBJECT / THUMBPRINT / PFX(+PASS)`，
  不落仓库不进 config），`DANIA_SIGN_TOOL` 显式指路 signtool；
- `resolve_sign_config()`：多选择器/凭证缺工具/PFX 不存在/指纹脏值
  全部在解析期挡住，不等到签名那一步才炸；
- `sign_file()`：签完必核验（`verify /pa`）——自签链不受信降级为警告
  （自签预期），其余 verify 失败一律 False。

**验证**：14 项断言（三种凭证映射 / 五种坏凭证拒绝 / 四路签验分支 /
OSError 不炸）+ 2 刀变异全红（sign 失败被吞 → `[C10-10]` 抓；指纹校验
旁路 → `[C10-4]` 抓）；真机 E2E：自签证书（New-SelfSignedCertificate +
signtool 26100）签真 exe → 警告通过。测试探针 exe 与 3 天期测试证书
验完即删（不留测试签名能力在用户证书库里）。

**口径**：管线建成 ≠ 已签名。对外分发仍须 CA 证书（OV/EV，企业资质
决策在用户）；届时只换环境变量值，管线零改动。doctor 第 13 项 WARN
语义不变——"未签名"在真证书就位前是事实，不该被管线存在美化。

---

## 修复：doctor 在 GBK 控制台 UnicodeEncodeError（2026-10-18 14:0x）

**问题**（真机实锤，装完最新包跑包内 doctor 当场炸）：
`UnicodeEncodeError: 'gbk' codec can't encode character '\u21b3'`——
GBK 控制台编不了 `↳` 符号，doctor 第 14 项一打印修复提示就崩，
整个自检跑不完。

**改动**（`tools/doctor.py`）：入口处
`sys.stdout/stderr.reconfigure(errors="replace")`——**不动编码**
（中文在 GBK 下本来就正常），只把编不了的符号降级为 `?`。
这比强设 UTF-8 好：GBK 控制台里中文照样可读，只有符号变 `?`。

**验证**：测试新增 `[15]` 行为级——子进程 `PYTHONIOENCODING=gbk`
跑完整 doctor，断言无 `UnicodeEncodeError` 且输出含诊断结果；
变异（reconfigure 旁路）→ `[15]` 红复绿。门禁 27/27。

**踩坑记录**：跑测试用错解释器——miniconda 系统 Python 缺
sounddevice 等核心依赖，doctor 依赖检查 FAIL（红的是环境不是代码）。
本项目的测试解释器是 `.venv/Scripts/python.exe`。

---

## 修复：桌面双击启动即崩（GBK devnull 真凶链）（2026-10-18 15:1x）

**问题**（用户双击快捷方式实测 crash_20261018_150448）：应用「点了加载很久」
其实是**启动即崩**。完整因果链：
1. pythonw 无控制台 → `sys.stdout/stderr = None`；
2. main.py 原有 reconfigure 对 None 无效（hasattr 跳过）；
3. pywebview `webview/http.py` 见 None → 替换成 `open(os.devnull, 'w')`
   —— devnull 按 **locale 编码 = GBK** 打开；
4. rich 往 GBK 流写 `✓` → UnicodeEncodeError → asyncio 启动线程死亡。

**取证**：三次探针才定位——管道子进程（句柄有效、GBK 但非 None）不复现；
同 snippet 在 `pythonw -c` 下也不复现（仍非 None）；最后用**干净环境 +
装机 pythonw + 无管道**复刻出 GBK 流，锁定 pywebview 替换路径。
教训：**复现失败 ≠ 缺陷不存在，启动态的句柄环境必须一模一样。**

**改动**：新增 `src/io_safe.py`（零依赖，只许标准库）——`ensure_safe_stdio()`
在一切第三方 import 之前把 None 流补成 **UTF-8 devnull**（pythonw 下输出
本来就无人可见，丢弃即正确语义；pywebview 见非 None 不再替换，GBK devnull
消失）；非 None 流维持原 reconfigure(utf-8, replace) 语义。main.py 引导期
改调它。

**验证**：`test_io_safe.py` 三态行为测试（None→UTF-8 devnull / GBK→转
UTF-8 / 正常→幂等）+ 变异红复绿；门禁 27/27。重出包重装后真机启动验证。

**同族缺陷全景**：doctor（已修，GBK 控制台）、主程序入口（本条）——
同一类「符号 × 编码」坑，两处入口各自兜底。

---

## 最终收口：闸门覆盖体检 + 并行工作入库（2026-10-18 15:4x，`96a9021`）

**问题**：三道「全仓级」闸门（字节码编译 / 全局名完整性 / 文本编码）名不副实——
只圈 `src main.py`，`tools/` 整棵树在视野外；doctor 的 `return AUTO`
NameError、`_append_banter2_log.py` 换行损坏都是这样漏进 HEAD 的。文本编码
闸还没带 `--strict`，检查器默认只打印计数、恒 exit 0——装饰闸门。

**改动**：三闸扩到 `src + tools + main.py + launch_companion.py +
run_webui.py`；编码闸加 `--strict`；doctor `_shell_mode` except 分支
`return AUTO` → `return "auto"`（字面量，附 why 注释）；配套把测试与工具
脚本里的裸 `open()` 补 `encoding="utf-8"`。

**验证**：doctor / make_dist_deps / autostart / io_safe 四套测试全过；
新闸门 27/27（tools 树首次全量进闸即全绿）。

**协作说明**：本条为主体改动出自并行会话（闸门覆盖专项体检），收尾时由
主线会话统一验证（四套测试 + 新闸门）后入库。

---

## C11 + 引导页双修复：桌宠壳进包 / 能力槽顶部截断 / 建角色防白屏（2026-10-18 16:4x，`0a31a4e`）

**用户真机四连差评**：①桌宠外围背景不透明 ②点击不能浮到最上 ③「把达妮娅连上大脑和声音」下面
的内嵌页最上面被截断 ④建角色完全卡死、没有任何选项、下一步灰色。

**根因（全部实测取证）**：
- ①② 同根：**Electron 桌宠壳根本没进安装包**——`INCLUDE_DIRS` 只有 src/assets/prompts，
  装机里没有 electron.exe → pet_backend 静默回退 pywebview 桌宠（代码注释自己写着「背景会有
  白底」，置顶能力也弱）。run 日志实锤：`壳不可用（未找到 Electron 可执行文件）→ 使用
  pywebview 桌宠`。并行会话把壳写好了，装配白名单没跟上。
- ③ Playwright + 960×620 实测复现：`ui_redesign_modelapi.html?embed=onboard` 里
  `.cap-wrap` 顶部在视口上方 **-65px**——design_tokens.css 的 SPA 基线
  `body{display:grid; place-items:center; padding:20px}` 在 iframe 内把超高内容垂直居中，
  溢出部分顶部不可达（经典居中溢出坑）。embed 模式的 CSS 只关了滚动没复位布局。
- ④ 建角色模块本体无桥/真桥/桥缺失三态在浏览器里全部渲染正常（stub 桥逐帧链模拟）。
  真机「没有选项 + 下一步灰」= 高度 ~533px（150% 缩放）下模块确认键在折叠线下 + 失败
  被静默吞掉的综合体验。处置：进屏 scrollTop 归零；挂载失败显式出兜底文案，绝不白屏。

**改动**：
- `tools/make_dist.py`：`copy_shell()` —— 壳三件 + electron dist 全量进包（locales 裁至
  zh-CN/zh-TW/en-US，省 46MB）；闸门 2 加壳漂移检查（源有 shell/main.js 而包没有 → 点名
  硬失败）；壳脚本进 C9-5 源码指纹。
- `ui_redesign_modelapi.html`：embed 模式复位 body 布局（display:block / padding:0）。
- `first_launch_setup.html`：enterRole scrollTop 归零 + 双路兜底文案。

**验证**：C11 段 9 断言 + 2 刀变异全红复绿；浏览器实测 embed wrapTop -65→0、页头完整；
正常路径无回归；缺模块路径出兜底文案。门禁 27/27。重出包重装后真机验证桌宠。
