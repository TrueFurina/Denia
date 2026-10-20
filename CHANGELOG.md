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
| `d01ea7b` | 22:0x | docs | **README 改成项目门面**（旧用户指南移入 `docs/`，两份流程分开写清）+ 新增「文档链接不悬空」闸门 | `--doctor` 实跑取准基线 16 PASS/3 WARN/0 FAIL；变异 3 刀全红；门禁 57→58 |
| `7816fa1` | 21:5x | chore | **入库 GitHub（私有）**：推送前做了三项体检——跟踪文件与**全部历史新增行**的密钥形态扫描（0 命中）、`config.yaml`/`data/`/`logs/` 从未进过历史、忽略规则复核；顺手忽略 `.workbuddy-ai/` | 774 文件 / 123 提交 / 14.1MiB；远端 `ls-tree` 复核无实配与隐私文件；HEAD == origin/master |
| `8c789b0` | 06:2x | feat | **「去配一个」落到的那页先说人话**：标题「能力槽」→「接入你的模型」，lead 从架构讲起改成从她要做什么讲起，并加一行隐私说明（Key 存本地、不经我们） | 隐私那句先核实过（全仓外发域名无第一方服务器）；headless 截图确认排版 |
| `74dc041` | 06:2x | feat | **首启不填 Key 的代价，由「意图点承接」接住**：他打完字、点发送被挡下的那一刻才弹「她还没接上大脑」（顶部条出现在错误的时刻）；判据抽成三态供两处共用 | node 实跑判据真值表 9 例；变异 11 刀全红；门禁 55→56 |
| `0208e9b` | 06:0x | fix | **第二个实例/重启竞态不再崩溃转储**：ws 绑定先重试 6 次（1s 间隔）+ psutil 定位占用者说人话；初始化失败分支弃跨线程拆窗、改硬退出（那是 0x8001010d race 的另一半） | 占用端口实测 5.1s 后抛带 PID+托盘指引的报错；新增回归门禁 13 断言（两刀变异全红）；门禁 +1（当前 57 项） |
| `9e877ed` | 05:5x | feat | **桌宠右下角亮一个点**：她在看屏幕时你看得见（青=在看 / 琥珀=隐私模式 / 不亮=关着）；走页面轮询而非跨线程推送 | headless 实测三态像素（青52/琥珀52/0）；变异 4 刀全红；门禁 54→55 |
| `9b8b073` | 05:4x | feat | **敏感窗口整帧不采集**：网银/密码管理器/支付页命中就不抓屏（不是抓完再丢）+ 四路竞品调研报告 | 判据是「命中时 analyzer 零调用」；变异 4 刀全红；门禁 53→54 |
| `9b39915` | 03:1x | fix | **通话浮层从来没被关上**（`#callOverlay` 漏一个 `</div>`，从建仓起就在）+ 新增「前端 HTML 配平」闸门 | 标签栈量到末尾深度=1；变异删补的闭合 → 闸门红并指到 1662 行；门禁 52→53 |
| `edbbc0a` | 02:5x | feat | **首启不再问 Key**：引导 5 步 → 2 步（建角色/体检），缺「大脑」改在对话页顶部常驻提示；顺手修掉体检屏的静默死按钮 | node 实跑 FLOW=['screenRole','screenSummary']；变异 4 刀全红；门禁 52/52 |
| `14d4e15` | 02:4x | fix | **每次启动都被拽回引导页**（出厂模板带着开发开关 `force_first_run: true`）+ 交付包没图标（`app.ico` 不在出包清单）+ 旧名当默认值 | 变异 3 刀全红；图标 7 档尺寸；门禁 51→52 |
| `599367b` | 01:1x | fix | **解包真跑**才发现：源码包首跑 `--doctor` 的「配置文件」提示指错方向（"确认运行目录"），且同一项报两条 | [6b] 断言"提示里有可照抄命令" + 变异红；解包复跑 4 FAIL → 16 PASS/3 WARN/0 FAIL |
| `c1d7e88` | 00:5x | feat | **给队友的源码包**：包内容取 `git ls-files`（gitignore 当单一事实源）+ 硬排除纵深防御 + 出包自验；闸门 50→51 | 24 红线 + 18 误伤用例 + 变异 4 刀全红；终包 767 条目 / 17.8MB / 585 个 .py 整包编译通过 |
| `e0a22f5` | 00:4x | fix | **真机验证抓出两个 bug**：迁移写错字段名（model 换了 voice_id 没换 → 照样 418）+ 出厂模板自带失效模型 | 真机副本实测 + 模板闸门 [13] + 门禁 50/50 |
| `dccd6ea` | 00:1x | fix | **屏幕摘要去重接线**：`_similar_summary` 从死代码变成真生效（轨迹不再堆"在游戏；在游戏"）；闸门 49→50 | 3 刀变异（6/4/1 红）+ 门禁 50/50 |
| `5dc1afd` | 23:5x | chore | 删旧主进程采集架构的 6 个遗留函数（270 行，注释写着"决定性修复"却零调用） | AST 前后对比 + 导入冒烟 + 门禁 49/49 |
| `4cc0c72` | 23:4x | fix | **音色档迁移是死代码**（定义完整、零调用）→ 接线 + 原子写盘；闸门 48→49 | 33 项断言 + 3 刀变异全红；门禁 49/49 |
| `3defc4c` | 23:3x | fix | 新克隆仓库首次出包必失败（`build_installer` 不自建 `dist/`）+ 修掉 `[C2-A]` 的假绿（一直靠 `dist/` 里的残留 exe 蒙混） | 变异 2 刀同红；门禁 48/48 PASS |
| `5e105e7` | 23:0x | chore | 清场 ~1.5GB 后把贴纸素材（124 文件/67MB）抢救入库并加忽略 | 出包依赖复核；无引用断裂 |
| `f087ff4` | 22:5x | merge | 并入队友内测版工作树：96 文件级差异 → 9 冲突 16 块全部解决，取双方精华 | worktree 47 项 + 主仓真环境 48/48 |
| `0911538` | 22:3x | snapshot | 队友内测包工作树快照（换行归一后 41 改 + 157 新），合并前置 | — |
| `b0a14ed` | 21:4x | docs | 队友内测版 v1.0 安装包验收报告（.NET 自写 SFX，非 NSIS） | 6320 条目 / 0 真密钥 / 红线 3 项阻断 / 可跑 |
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

### 2026-10-20 · README 是门面，不是内部备忘录

仓库上了 GitHub 之后，`README.md` 的身份变了：它不再只是给测试用户看的安装指南，
而是**任何人打开这个仓库的第一屏**。而它当时的第一句话是
「装好 → 填一个阿里云百炼 Key → 点电话图标就能说话」——**和现在的代码不符**：
引导早就不问 Key 了。更麻烦的是两份东西都"对"，只是版本不同：v1.0 安装包是旧流程，
仓库代码是新流程。所以不覆盖、分开写：

- 旧的用户指南原样移到 `docs/使用指南_内测版v1.0.md`（那 4 个安装坑是真金白银换来的，一个字没删）
- 新的 `README.md` 只回答四件事：她是什么 / 怎么跑起来 / 能做什么不能做什么 / 数据在哪

新 README 里每个数字都是当场量出来的，不是回忆：`--doctor` 实跑基线
**16 PASS / 3 WARN / 0 FAIL**（3 个 WARN 全是开发环境专属：配置是出厂默认但有使用痕迹、
没有签名的 exe 入口、旧配置残留「小薇」唤醒词），闸门 57 道、约 4 分钟。

顺手补一道闸：**文档链接不悬空**。这类坏法很安静——**没人会点**，断了也不会有人报错，
直到新来的人照着点一次 404。而"文档搬家"恰恰最容易制造悬空（本次就把 README 挪了位置）。
判据只查本地相对路径（`[文字](path)` 与 `<img src>`），跳过 http/mailto/页内锚点，
并且按**文档所在目录**解析而不是仓库根——后者是最常见的假红来源。
变异 3 刀全红：搬家没改链接 / 图片路径写错 / 指向不存在的文档。

### 2026-10-20 · 入库 GitHub：先把「历史里有什么」查清楚，再推

推到远端和推到本地不一样——**本地删掉的东西，在历史里还在**。所以体检分三层，
而不是只看工作区干不干净：

1. **跟踪文件的密钥形态**：`LTAI…`（阿里云）/ `AKID…`（腾讯云）/ `sk-…` /
   `sk-ant-…` / `ghp_…` / JWT 三段式。774 个文件，**0 命中**。
2. **全部历史的新增行**（`git log --all -p` 只取 `+` 行）——这一层是关键：哪怕
   文件后来被清理过，只要它曾以明文进过某次提交，推送就等于公开。**0 命中**。
3. **实配与隐私文件是否进过历史**：`config.yaml`、`data/`、`logs/`、
   `call_records.json`、`user_knowledge.yaml` 逐个 `git log --all -- <path>`，
   全空。发布模板里只有 `${DASHSCOPE_API_KEY}` 这类环境变量引用和空串。

结论：`gitignore` 是从第一次提交就生效的，不是后来补的——这点值得记住，
它决定了"要不要 rewrite history"这个更贵的选择题根本不用做。

顺手把 `.workbuddy-ai/`（项目记忆与会话数据）也加进忽略：它是**项目资产**，
但不该进仓库——里面是给下一个 AI 看的笔记，不是产品的一部分。

仓库是**私有**的（`TrueFurina/Denia`）。公开与否是个产品决定，不是技术决定：
里面有竞品调研、诊断报告、还有对自家缺陷的完整记录，这些在公开前该再过一遍。

### 2026-10-20 · 「去配一个」点下去之后，那页得先说人话

浮层把人送到了 `/redesign/ui_redesign_modelapi.html`，但那页开口第一句是
「按『能力』把模型分成 4 个槽」——那是**我的架构**，不是他的问题。他刚被挡下来的
问题是「她为什么不回我」，落到这页该看到的第一句是「这里决定她用什么模型说话」。

改了三处口径（调研 P2）：标题「能力槽」→**「接入你的模型」**；lead 改成从
「她说话 / 听你说 / 和你通话」讲起，最省事那条路放前面；加一行隐私说明。

**隐私那句是核实过才写的**，不是文案修饰：「Key 只保存在这台电脑上，不会上传给
我们；聊天内容直接发给你选的服务商。」依据：Key 落在本地 `config.yaml`（被
`.gitignore` 挡住、不进仓库）；`grep` 全仓外发域名，只有各模型厂商 API 与视频站点，
**没有任何第一方服务器**。哪天加了中转服务（比如 P0 的官方补贴通道），这句必须同步
改——否则它就从「信任」变成「欺骗」，而这是陪伴类产品最贵的一次失误。

排版上的一个坑：隐私行**不能**放进 `.cap-lead`，因为 `.embed-onboard .cap-lead`
是 `display:none`，而「首启引导里嵌这个页面」恰恰是最需要这句话的场合。

### 2026-10-20 · 首启不填 Key 的代价，得有人接住：在他打完字点发送的那一刻

`edbbc0a` 把引导从 5 步砍到 2 步、不再问 Key，换来的代价一直没人接：**他第一次
打完字点发送，得到的是「未发送：xxx」**。`edbbc0a` 的补偿是对话页顶部一条常驻
提示条，但那条解决不了这件事——它出现在**错误的时刻**：刚进桌面时用户没有聊天
意图，看到了也不会动；等他真想说话，眼睛早把它过滤掉了。

调研的 28 个消费级产品里 0 个要求填 API Key，成功的那几个都是「意图点承接」：
Chatbox 把引导做成一个常驻会话、Cline 把模型选择放在输入框上方。于是做一个浮层，
只在**他打完字、点了发送**的那一刻浮在输入框上方，两个出口：去配一个 / 稍后。

四件容易做错的事，都写成了断言：

1. **只在失败那一刻亮**。静态标签不带 `show`；全页只有一处 `add('show')`，且位置
   在「未发送」toast 之后（成功路径不会亮）。进页面就亮 = 顶部条换个位置，白做。
2. **判据是能力槽状态，不是 error 文案**。文案会改，而且「端口冷却」和「没配」
   在 error 里长得几乎一样。闸门到点亮之间不许碰 `res.error` / `reason`。
3. **判据必须是三态**（这一条是写测试时才发现的真问题）。第一版写成
   `if (chat === 'none')` 弹，第二版抽成布尔 `__chatUsable`——两种都会在**后端还
   没就绪**（`success:false`）时判成「聊不了」，也就是在她正加载模型的那两秒弹
   「她还没接上大脑」。顶部条为此专门写了 12 次重试躲这个假结论，浮层不能把它
   重新引回来。所以判据返回 `'yes' / 'no' / 'unknown'`，只有 `'no'` 才弹。
   - 真值表交给 **node 跑页面里那段真 JS**，不在 Python 里复刻逻辑：复刻出来的
     测试只能证明「我两次写得一样」（`e0a22f5` 的 33 项断言全绿、字段名却写错，
     就是这么被骗的）。
   - `{}` 这一例逼出了第二个口子：原来写 `r.success === false` 才判未知，于是
     「压根没有 success 字段」被当成成功、被读成「未配置」。改成只认 `=== true`。
4. **文案承诺的事得有代码保证**。副文写「刚才那句话还在输入框里——配好之后直接
   点发送就行」，断言 `input.value = v` 这行还原动作必须**先于**点亮浮层存在。
   否则那句话就是写得好听。

顺带把「能不能聊」收敛成**全页一处定义**（`window.__chatUsable`），顶部条与浮层
共用。两处各写一遍迟早漂移成「顶部条说能聊、点了发送浮层说聊不了」，刀7 就是切
这个。定义放在 `if (!bar) return` 之前：顶部条被删掉时浮层仍要能判定。

**不做「只弹一次」的记忆**（不写 sessionStorage）：每次点发送都代表一次真实意图，
每次都会被同一个原因挡住；不说明原因，他看到的就只是「消息发出去没人回」。

**语音也是发送入口**（`5e0fbcb` 补）：只守打字那条，用语音的人永远等不到这句话——
和「两个启动路径只给一个传参数」是同一种漏法。点亮因此收敛成唯一入口
`__showSetupHint`、判定收敛成 `__maybeSetupHint`，两条路径各传一个 voice 布尔；
语音副文换成「配好之后直接开口说就行」（语音没有输入框，照抄打字那句就是假话）。

验证：`python src/tests/test_setup_hint.py` 全绿（判据真值表 9 例由 node 实跑）；
变异 **11 刀全红**——其中刀8（把立刻出现换成 180ms 淡入）第一轮**漏了**，因为断言
只检查了 `.setup-hint{}` 基础规则，而变异加在 `.setup-hint.show{}` 上；补成「两条
规则都查 transition/opacity/animation」后红。门禁 55→56。
headless 实测：浮层浮在输入框上方（`bottom:104px`），不遮输入、不挤动对话流。

### 2026-10-20 · 启动崩在「第二个实例正在跑」：不该是 crash dump，该是一句说人话的报错

**问题（03:11 实锤）**：上一个实例退出几秒后再启动，`ws_server.start()` 绑 8765
直接 `OSError 10048` → asyncio 线程异常 → 监督线程走进初始化失败分支，
**跨线程调 `_destroy_all_webview_windows()` 拆 pywebview 窗口**，与主线程还在
`webview.start` 里的 `create_window` COM 调用 race，进程以 fatal exception
0x8001010d（RPC_E_CANTCALLOUT_ININPUTSYNCMESSAGE：主线程处于 input-synchronous
调用未返回时，别的线程发起 COM 出站调用即致命）崩溃转储收场。用户视角：
什么都没提示，桌面多个 crash log。上一轮已记「第二实例会抢 data/，不能硬起」——
判断没错，错在**该死得体面的时候死了个崩溃**。

**改动**（两处，都在启动失败路径上）：
1. `src/ws_server.py`：绑定失败先重试 6 次（1s 间隔）——旧实例退出后端口还会
   10048 一两秒，不重试就把「重启」误判成「双开」；仍失败则用 psutil 定位占用
   进程，抛出带 PID/进程名和「先去托盘退出另一个实例」指引的 OSError。
2. `main.py` 初始化失败分支：错误展示 2.5s 后 `os._exit(1)` 硬退出，删掉跨线程
   `_destroy_all_webview_windows()`——那正是 fatal COM race 的另一半，且 try/except
   拦不住 C 级信号。子线程全是 daemon、无状态需要保存，宁可瞬间消失也不要 crash dump。

**验证**：占用端口实测 5.1s 后抛出带 PID+指引的报错（含端口号/退出指引/data/ 冲突
说明四项断言）；空闲端口启动→客户端连接→stop 全正常；全量门禁通过（当前 57 项，
含并发会话同期新增的「首启承接浮层」，顺带验过是通过的）。
**回归门禁**：`src/tests/test_ws_port_bind.py`（13 断言：空闲零 warning / 被占重试
+报错四要素 / 占用探测不扰民）已注册进门禁（+1 项，当前 57）；两刀变异已验——
删重试→「确有重试」红，改回裸 raise→三条诊断断言红。

### 2026-10-20 · 她说"我看见你在写代码"之前，得先让你看见她在看

上一轮补了「敏感窗口不采集」，但只补了一半：**用户仍然不知道她在看**。
同类产品全是显式共享 + 可见指示（Copilot Vision 有屏幕边框和停止按钮、豆包挂
「已共享屏幕给豆包」悬浮条、Kimi 是 `Shift+Alt+A` 框选），而达妮娅原本是
每 2s 抓一帧、桌面上零迹象。**用户只能靠信任，没法靠看见。**

落点选在**桌宠本体**而不是主窗口——主窗口可以关掉，桌宠常驻右下角。
三态：

| 状态 | 表现 |
|---|---|
| 正在看屏 | 青色小点，2.4s 呼吸一次 |
| 隐私模式（全停） | 琥珀色常亮 |
| 看屏关着 | 不显示 |

**为什么走页面轮询，不用 Python 推送**：`main_window.py:454` 有实测结论——
GUI 线程内同步调 `evaluate_js` 会「自己锁死自己」（它在等 WebView2 回执，
而回执只能由正被本调用堵住的消息泵派发），跨线程调同样不可靠。
所以页面每 2.5s 主动拉 `pywebview.api.get_perception_state()`。

**为什么用 `display` 而不是 `opacity` 淡入**：淡入 350ms 意味着"她已经开始看了，
但用户还有一小段时间不知道"——**隐私指示的延迟本身就是缺陷**。立刻出现，
只让颜色做过渡。

这条是 headless 验证时撞出来的，不是想出来的：`--dump-dom` 显示 `class="on"`，
但截图里点根本没出现。读计算样式才看到 `op=0`——virtual time 下 CSS transition
不推进。换成 `display` 后三态立刻可截、可真机可信。
**顺带一个副产品**：headless 的 viewport 比 `--window-size` 宽（420 的窗口，
元素 left=482），第一轮截图是"点被裁在画面外"，不是"点没渲染"。

**验证不只看断言，看像素**：三份副本注入假 pywebview，headless 实测
on→青 52 像素 / paused→琥珀 52 像素 / off→0/0；`dump-dom` 确认 class 分别是
`on` / `paused` / 空。

**写测试时同一个坑踩了两次**（都是"变异没变红"才发现的）：
1. `"evaluate_js" not in html` —— 被我自己的注释满足，注释里就写着这个函数名。
   改成匹配调用形态 `(pywebview\.api|window)\.evaluate_js\s*\(`。
2. `"classList.toggle('on'" in html` —— 被页面原有代码满足：这个页面里
   `classList.toggle('on'` 一共出现 **3 次**（Live2D 面板开关也用）。
   改成锚定 `dot.classList.toggle('on', on)`——自己的变量名 + 条件参数。

**教训收口**：源码级断言要锚定到「自己的变量名 / 完整调用串」，
不能锚定到「方法名」——方法名在页面里往往不止一处。这和
「断言被自己的注释满足」是同一条规则的两个变体。



### 2026-10-20 · 竞品调研后的第一个动作：她不该看见的东西，一开始就别抓

**怎么来的**：用户要求「先暂停，纵观全局，拉团队深度调研同类型/竞品」。四路并行
（国内陪伴 / 海外陪伴 / 桌面形态+开源 / BYOK 首启策略），产出
`竞品调研_2026-10-20.md`。

调研里最扎眼的一条不是首启 Key，是**屏幕采集**：

| 产品 | 屏幕感知的做法 |
|---|---|
| Copilot Vision | 点眼镜图标 → **显式选窗口**才看；共享时屏幕有边框 + 停止按钮 |
| 豆包 | 共享时挂「已共享屏幕给豆包」悬浮条 |
| Kimi | `Shift+Alt+A` 截图提问，带标注画笔 |
| Raycast | 只读**当前窗口**上下文 |
| **达妮娅** | **每 2 秒抓一帧全屏**，持续、静默、桌面上无任何指示 |

**没有一家做后台定时截图。** 这是全场最激进的采集策略。

激进本身不一定是错——「她真的知道我在干什么」是差异化，而且现有实现已经比
想象中克制（pHash 变化才送分析、游戏/会议前台让位、隐私模式一键全停、
涂掉自身窗口和输入法候选窗）。问题在于有一类画面**一旦进模型上下文就撤不回来**：

> 没有「删掉这一帧」的操作，只有「当初别抓」。

所以补的是应用级排除，不是降频：

- **`privacy.py`**：`sensitive_skip_reason(proc, title, patterns)` 纯函数 +
  `DEFAULT_SKIP_APPS` 13 条（keepass / 1password / bitwarden / lastpass / dashlane +
  网上银行 / 网银 / 转账 / 支付密码 / 支付宝 / 微信支付 / 银行卡 / 凭据管理器）。
  进程名与窗口标题**都查**、包含匹配、不区分大小写——浏览器里开网银时进程名是
  `chrome.exe`，只有标题能识别。
- **`screen_capture.py`**：判定放在**抓屏之前**。`capture_loop` 和事件驱动的
  `_grab_and_analyze` 两处都拦（漏后者的话，「前台切到游戏」的触发逻辑会把网银
  窗口顺手抓一遍）。命中时 `_last_hash` 保持旧值 → 离开该窗口后画面不同 →
  正常判为变化并分析，不会「瞎掉」。
- **与隐私模式的区别**：隐私模式是**全停**（她整体失明，用户在游戏里双击悬浮层
  就能触发）；这份清单是**局部让位**（只是这一个窗口没看）。
- **两个语义必须分开**：字段缺失 → 用默认清单（老配置也受保护）；
  `skip_apps: []` → 用户明确说「都别拦」，**不能被默认值覆盖回来**。
- **刻意不含微信/QQ**：那是产品核心能力，用户对它已有预期。
- **可审计**：snapshot 暴露 `skip_count` / `skip_last` / `skip_last_ts`，
  日志按「原因变化」去重（网银停留 10 分钟不该刷 300 行）。

**判据为什么是「analyzer 零调用」而不是「函数返回对不对」**：
抓完再丢同样能返回正确结果，但那不是隐私保护——图已经进过内存、进过 JPEG 编码、
进过日志。所以测试断言的是"没生成"，不是"没送出去"。

**变异 4 刀全红**：删事件驱动路径的判定 / 空列表被默认覆盖 / 日志去重退回自比较 /
`main.py` 第二处装配漏传 `foreground_check`。最后那一刀守的是「两处装配」——
`main.py` 有 1214 和 2848 两个启动路径，漏一处那条路径上的采集就**静默不设防**，
而"出包成功""程序能跑"都发现不了。

**自己埋的坑**：`_mark_skip` 第一版写成先赋值 `self._skip_last = reason`、
再比较 `if self._skip_last != reason` —— 永远相等，日志永远只打第一条。
和老坑同形（源码级断言被自己的副作用满足），已改成先存 `prev` 再比较，刀3 守它。

**没做的**：桌面上的常驻采集指示灯。感知活动时间线目前只在「调试页」里
（`ui_redesign_developer.html`，能看到最近 20 条 + 模型当前看到的那一帧），
普通用户不会去那儿。这条留给下一轮——它需要和桌宠壳一起设计，不是加个角标就完。



### 2026-10-20 · 真机渲染：看到了两步引导和那条提示条，也顺手量出一个从建仓就在的漏标签

**为什么不能只信闸门**：前一轮的首启简化只过了静态断言 + node 推导。布局、配色、
文案挤不挤，只有渲染出来才知道。所以这一轮做了两件事：渲染验证 + 把"渲染不出来
但结构坏了"的那类问题变成闸门。

**起第二个实例失败，原因很实在**：`main.py` 直接报
`OSError: [Errno 10048] ... bind on address ('0.0.0.0', 8765)`——**用户自己那个实例
正在跑**（截图里能看到 bilibili 在播）。第二实例会抢 `data/`，不能硬起。

**改用无头 Edge 直接渲染**（不需要装任何东西）：
1. `python -m http.server 8899` 起在 `src/ui/web`（页面里的 `/redesign/...` 绝对路径
   刚好能对上）；
2. `msedge --headless=new --screenshot=... --window-size=1440,900 <url>`；
3. 提示条要单独造一份副本把 `hidden` 摘掉（无后端时 `getCapabilitySlots` 拿不到，
   它会一直保持隐藏——这正好也证明"不是写死常亮"）。

**渲染结果**：引导页顶部步骤是 **① 建角色 ② 体检**（原 5 步）；角色卡是
达妮娅 / **温柔女友** / AI老师 / 萌友 / 大肥鱼——「小薇」没了；对话页顶部提示条
按设计渲染（青色描边 + 去配一个 / 稍后），且默认状态下不出现。

**顺手量出一个从 2026-10-17 建仓起就存在的结构错误**：`index.html` 的
`#callOverlay`（通话浮层）**没有闭合标签**——`call-screen` 视图漏了一个 `</div>`，
单工通话视图 `#simplexView` 因此成了它的子节点，浮层自己靠浏览器在 `</body>`
处自动补。**运行时毫无症状**，所以两个月没人发现；代价是 `#callOverlay` 的
hidden 开关会连带控制单工视图。

**新增闸门 `tools/check_html_balance.py`（门禁 52→53）**，判据是这一轮唯一值得
记的技术点：**用容错匹配，不用严格栈**。闭合标签优先匹配最近的同名开标签，中间的
没关元素按浏览器行为一并弹出——这样只报"真漏标签"，放过
`<section><div></section>` 这种错位嵌套（浏览器恢复是确定的，报它只会制造噪音）。
`first_launch_setup.html` 有 5 处错位嵌套，容错匹配下为 0，所以没动它。
设计稿（`_` 前缀 9 个 theme editor 外壳）不参与，但**跳过会打印出来**——静默跳过
等于让闸门少扫还不告诉你。

变异：把补的那层 `</div>` 删掉 → 闸门红，且精确指到「第 1662 行 `<div>` 没有闭合」。

### 2026-10-20 · 产品级收口：一进来就让人填 Key，是"每次都要重填"叠出来的

**用户原话**：「一进入要让人填写 key 等操作，是非常繁琐的，不符合化繁为简的产品思想。」

先查事实，别急着改 UI。查完发现是**三件事叠在一起**，其中一件是 bug：

1. **`config.release.yaml` 第一行是 `force_first_run: true`**。注释写着"反复重启看
   首启效果用"——那是**开发调试开关**。判级是 `force or not first_run_done`
   （`webapp.py:10103`），所以只要它还在，用户**填完 Key 下次打开又得填一遍**。
   这不是"设计繁琐"，是"每次都要重来"。→ 模板改 `false`，本机实配也一并关掉。
2. **交付包里根本没有图标**。`app.ico` 一直在仓库根，`main_window.py:1395` 按
   `<根>/app.ico` 找它，但 `make_dist.INCLUDE_FILES` 里没有 → 出包后找不到 →
   `icon=None` → Windows 给的是 **python.exe 的图标**（任务栏/Alt-Tab 都是）。
   顺手把图标本身也换了：原来是紫粉同心圆占位图，现在用 `_role_assets/danya.png`
   生成 7 档尺寸（16/24/32/48/64/128/256），4 倍超采样圆角遮罩。
3. **首启第一步是一张配置表**。`first_launch_setup.html` 的「连引擎」屏是个 iframe，
   嵌着整个「模型与API」页（14 家服务商 × 4 个能力槽）。新用户第一眼看到的是
   要填的东西，而不是她。→ 流程从 5 步收成 **2 步：建角色（含声音）→ 体检**。

**少问一步的前提是有人承接**，否则用户进去以后不知道自己缺什么。所以对话页顶部
加了一条常驻提示：「还没给她配『大脑』——能听会说，但还聊不了天」，一个按钮直达
设置，「稍后」只压本次启动。显隐按真实槽位状态（`getCapabilitySlots` 的 chat 槽），
配好自动消失。旧行为是发一句话只拿到一句报错，看着像软件坏了。

**顺手抓到一个静默死按钮**：流程瘦身后，体检屏四行「去配置」还指着已经不在流程里
的屏，而 `goToFix` 是 `if(idx>=0) goTo(idx)`——拿不到就什么都不做。这类失效最难查，
用户只会说"点了没反应"。

**角色名去旧品牌**：预设「温柔女友·小薇」→「温柔女友」（人设、声音、id 全不动）。
至此除 Edge-TTS 的音色名「微软小薇」外，代码里不再有旧产品名。

**验证**：node 实跑 FLOW 推导 → `['screenRole','screenSummary']` → 步骤标签
「建角色 / 体检」；变异 4 刀全红（流程改回能力槽开头 / goToFix 删回静默 /
对话页去掉提示条 / 模板唤醒词塞回小薇）。门禁 51→52，52/52 通过。

**这轮又踩了一次老坑**：`goToFix` 那条断言第一版写的是
`"if(idx>=0) goTo(idx);\n  }" not in flow`——这个串**改前改后都不存在**，
所以变异照样绿，是条空断言。改成"取出函数体、断言里面有 toast/跳转"，
重跑变异才真红。**源码级断言必须配一次变异，否则它只是装饰。**

### 2026-10-20 · 解包真跑：队友看到的第一屏是"两个 FAIL 和一句指错方向的话"

**怎么发现的**：包验过内容、验过 CRC、验过整包编译，全绿。但那些都只证明
"包里是这些字节"，不证明"队友拿到手能跑起来"。所以把 zip 解到 `D:\_danya_smoke`，
用队友的方式（`python main.py --doctor`）跑了一遍——第一屏：

```
[FAIL] 配置文件  找不到 config.yaml
       ↳ 修复：确认运行目录，或用 --config 指定路径
[FAIL] 配置文件  加载失败：[Errno 2] No such file or directory: 'config.yaml'
       ↳ 修复：检查 YAML 语法与路径
```

**两个问题，都不是"代码错"，是"话没说对"**：

1. **提示指错方向**。源码包有 `config.release.yaml`、没有 `config.yaml`，这是
   **正常初始状态**，不是"运行目录错了"。让队友去怀疑运行目录，等于让他自己猜。
   现在模板就在旁边时直接把动作写出来：
   `↳ 修复：复制模板再填 Key：copy config.release.yaml config.yaml`。
2. **同一项报两条**。`run_all` 自己加载失败报一条、`check_config` 又报一条。
   队友的第一反应是"到底哪个是真的"。删掉 `run_all` 那条——`check_config`
   会再加载一次，且把"找不到 / 解析失败 / 缺字段"分场景说得更细。

**测试断言的是"有可照抄的命令"而不是"有提示"**：`check("copy config.release.yaml
config.yaml" in fix)`。变异（把 `fix` 改回旧文案）→ 这条变红。

**按修好的提示走一遍全流程**（同一个解包目录）：复制模板 → 再跑 `--doctor`
→ **16 PASS / 3 WARN / 0 FAIL**（模型渠道 959ms、语音合成真合成 43406 字节、
vosk 中文模型已缓存、13 处 Key 用 `${ENV}` 不落盘）。

**剩下的 3 个 WARN 里，两个是环境、一个是内容**：Electron 壳没装（`npm install`
即可）、源码包无 .exe 可签（正常）；第三个是**出厂模板的唤醒词里有「小薇」**
——那是旧产品身份（`chat_overlay.py` / `console.py` / `onboarding_role.js`
的默认角色名都还是「小薇」）。这属于改名遗留，牵扯 UI 文案与角色列表，
**没动**，留档待定：新用户装完会看到"她说的话以后都是这个声音（小薇）"。

### 2026-10-20 · 给队友的源码包：把「排除清单」从人的记忆里搬进脚本

**问题**：2026-10-19 的交付验包（`交付验包_机语恋内测版_v1.0_2026-10-19.md`）
发现，上一个包是**把整个工作目录直接压了**：混进 `data/`、`call_records.json`、
`user_knowledge.yaml`，三项全踩交付红线；还顺带发了 290 个开发期一次性脚本和
一个 `.swapbak` 编辑器备份。

根因不是"不知道要排除"，而是**排除清单活在人的记忆里**：这次记住了，下次
换个会话、换个人手压，还得再想一遍，且没人能证明它被执行过。

**改动**（`tools/pack_for_teammate.py`，新增）：

1. **包内容取 `git ls-files`**。`.gitignore` 本来就是这份规则的单一事实源
   （`node_modules` / `python` / `data` / `logs` / `config.yaml` 全在里面），
   而且它随代码一起演进，不会和打包脚本各说一套。实测：764 个跟踪文件，
   硬排除层**一项都没触发**——正好说明 gitignore 已经把该挡的都挡住了。
2. **再叠一层硬排除**做纵深防御：即使哪天有人 `git add -f` 了实配或隐私文件，
   也进不了包。`data`/`logs` 按**任意层级**排（实测跟踪路径里没有任何
   data/logs 段，不会误伤；按"只看包根"排会漏掉嵌套的
   `tools/daniya_sticker_skill/data/` 那 67MB）。
3. **出包后自验**：重新打开 zip 逐条核对（不是复述 collect() 的结果），
   再解到临时目录跑既有闸门 `check_package_secrets.py --dir`。红了**删包**退出。
4. **随包附一份 `队友须知_先读我.md`**（内容内嵌在脚本里，不往仓库根目录再添
   md）：装依赖 → `copy config.release.yaml config.yaml` → 跑 → `--doctor` 自检
   → 改代码前先 `check_all`。`node_modules` 没随包，所以写了 `npm install`。

**为什么测试要单独对着规则表喂路径**：这层硬排除在真实仓库上一项都不触发，
所以"跑一次出包成功"**证明不了任何一条规则是活的**——把 `_exclude_reason`
整个改成 `return None`，出包照样 766 个文件、照样验包通过。这和 2026-10-19
那次假绿是同一个形状。`src/tests/test_pack_for_teammate.py` 因此直接喂
24 条红线路径 + 18 条不该误伤的路径（`src/data_loader.py`、`distutils_compat.py`
这种"以 data/dist 开头但只是文件名"的必须留着）。

**写测试时抓到的真缺口**：条目本身就是个目录名时规则不生效——zip 的目录项
不一定带尾斜杠，`verify_zip` 拿到的可能是 `T/logs` 这种。已补（`low in
EXCLUDE_DIR_ANY`）。这条**不是我先想到的**，是断言先红了才发现的。

**验证**：变异 4 刀全红（规则恒 `None` / 删红线文件名表 / 删 `data` 目录 /
`REQUIRED` 少一项），基线绿。终包 767 条目 / 17.8 MB / 压缩率 57%；
独立复核：红线 0 命中、`sk-`/AKID/LTAI 形态 0 命中、中文文件名全带 UTF-8 标志位
（bit 11，否则 Windows 按 GBK 解会乱码）、解包后 **585 个 .py 整包字节码编译通过**
（CRC 只能证明字节没坏，编译才能证明源码没被截断）。门禁 50→51 全过。

**顺带清理**（真垃圾，非依赖）：`__pycache__`/`.pyc`、`logs/` 15 个 0 字节 crash
日志、76 个 `tools/_*` 诊断产物（保留被跟踪的 `_test_img.png`）、
`assets/voices/*.swapbak`、空 `dist/`。

**没动的大块**：`.venv/` 696MB 与 `shell/node_modules/` 383MB 是**依赖安装**
而不是垃圾——`.venv` 删了 51 道门禁当场跑不了，`node_modules` 删了要重新下
Electron（国内网络容易失败）。两者都被 gitignore 挡在包外，留不留只影响本地磁盘。

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

---

## 队友内测版 v1.0 安装包验收（2026-10-19 21:4x，`b0a14ed`）

**背景**：队友并行交付 `D:/Denia/机语恋_内测版_v1.0.exe`（221MB）。不能运行安装器
验包——它是 **.NET 自写 SFX**（`JiyulianSetup` / `ZipArchive` / `DefaultInstallDir` /
`RegisterUninstall`），不是 NSIS。用 `PK\x05\x06` 尾部签名定位 ZIP 起点，`zipfile`
直读即可。

**结论**：包本体完整（6320 条目，解压 364MB），**是 0.1.0 旧快照**——没有 `shell/`、
没有 `pet_shell/pet_backend`、没有 `src/version.py`，即不含 C11 的 Electron 桌宠壳。
未签名、`FileVersion 0.0.0.0`。红线扫描 3 项阻断（`data/`、`call_records.json`、
`user_knowledge.yaml`），但 **0 个真密钥**。可跑：内置 Python 3.12.10，`main.py --help`
正常。

**边界**：只验「包对不对、能不能起」，未验真机交互。报告见
`交付验包_机语恋内测版_v1.0_2026-10-19.md`。

---

## 两条工作流合并（2026-10-19 22:5x，`f087ff4`）

**背景**：这几天主线（质检 + Electron 壳 + C9 系列）与队友线（TTS 失效模型迁移 +
内测包）各自独立推进，基线 `5bffc68` 只有 64% 文件相同，不能直接快进。

**量化**：先做**换行归一**（CRLF vs LF）再比——不归一会把 41 个真改夸大成 111 个。
归一后：他们改 41 文件 / 我们改 37 / 冲突 9 文件 16 块。

**做法**：`git worktree` + 独立分支 + `git merge --no-commit`，冲突逐块人工裁决。
`HEAD` 是队友侧（被合入方是 `master`）。

**冲突决策**：
- `webapp.py`：**保留他们的 TTS 失效模型迁移**（`_TTS_STALE` + `migrate_stale_tts_model`
  + `migrate_stale_voice_profiles`，我们没这能力）；桌宠三块取 master（Electron 壳）。
  两套桌宠实现撞车时按用户拍板「保留 Electron 壳」。
- `config.py` `save()`：**合成**——他们的进程级锁 + 原子写，叠加我们的 C9-1 密钥加密
  与环境变量引用还原。
- 其余：路径健壮版取 master；导入取并集。

**合并中修掉的 4 个真问题**（都不是机械冲突，是两边各自潜伏的）：
1. `config.py` 用 `threading.RLock()` 但模块级没有 `import threading`（他们只 `import as _th`）
2. `webapp.py` 引用 `_ALIYUN_DEFAULT_CHAT` 却从未导入 —— **队友原版同样潜伏 NameError**
3. `webapp.py` 类头 `class CharacterAPI(...)` 被拼到迁移函数之前 → IndentationError
4. `webapp.py` 的 `or {` 右括号在拼接中丢失

**验证**：worktree 内 47 项 → 42 过 / 1 跳过（无 Electron）/ 2 红（doctor 隔离、
build_installer，均为 worktree 的 junction/ROOT 环境产物，主仓同测试通过）；全局名
完整性 577 文件全绿；全部 `.py` 编译通过。合入主仓用 `--ff-only`，**全程没碰**并发
会话正在改的 `dialogue.py`；主仓真环境 48/48 全绿。

---

## 清场：删旧包旧产物，贴纸素材抢救入库（2026-10-19 23:0x，`5e105e7`）

**删掉约 1.5GB**：`D:/_jyl_beta_extract`（378M，队友包解压残留）、4 个旧 exe
（211M + 35M + 48K + 40K）、`daniya_ref.wav`、7 个 config 备份、`dist`（172M）+
`dist_stage`（714M）、24 个 `__pycache__`。

**抢救**：`tools/daniya_sticker_skill/data/` 124 文件 / 67MB 贴纸素材原本是未跟踪的
本地产物，但技能运行依赖它——先入库再在 `.gitignore` 里放行（后续生成物不入库）。

**副作用（正是它暴露了下一个 bug）**：删掉 `dist/` 后，门禁 `打包依赖分级` 立刻转红。

---

## 首次出包必失败 + 一道一直假绿的用例（2026-10-19 23:3x，`3defc4c`）

**产品 bug**：`build_installer()` 直接往 `root/dist/` 写 OutFile，**自己不建目录**。
旧代码一直靠「上一次出包留下的 `dist/`」蒙混过关，所以从没暴露。清场删掉 `dist/`
后，任何新克隆的仓库第一次出包都会被 makensis 报 `error opening file for writing`。

**测试 bug（更值得记）**：`src/tests/test_make_dist_deps.py` 的 `[C2-A]` 段一直是
**假绿**。它的假 `subprocess` 从不产出 exe，`build_installer` 收尾 stat 不到文件本该
返回 False——但 `D:/Denia/dist` 里躺着上一次**真实**出包的同名 exe，于是「通过」了。
**这道用例验证的不是代码，是上一轮构建的残留。** 同文件的 `[C9-5]` 段（`_fake_run5`）
反而写对了，会模拟 NSIS 产物——同一份测试里两种写法并存，说明这是疏忽不是设计。

**改动**：
- `make_dist.build_installer`：调 makensis 前 `os.makedirs(root/dist, exist_ok=True)`。
- 测试 `[C2-A]` 改用**独立假根**：若继续拿真仓库当 root，假 subprocess 就得往
  `D:/Denia/dist` 写一个 0 字节、与真安装包同名的假 exe——那是能被人当成品发出去的雷。
- 假 makensis 改成真 makensis 的语义：**输出目录不存在就 rc≠0**，绝不替调用方建目录
  （否则「自己建 dist/」这条永远测不出来）。新增断言「root 没有 dist/ 时自己建」。

**验证**：变异（删掉那行 `os.makedirs`）→ 2 项断言同时红；还原后全绿；全量门禁
48/48 PASS。

**同族教训**：「假实现顺手把前置条件补齐」= 测试自我安慰。凡是用假外部程序验证
「产物存在」的用例，假程序必须**只做真程序会做的事**，缺前置就按真程序的失败方式失败。

---

## 音色档迁移是死代码 + 死代码全仓体检（2026-10-19 23:4x，`4cc0c72` / `5dc1afd`）

**怎么发现的**：合并队友工作树时做静态检查，`grep -rn migrate_stale_voice_profiles`
只命中一行——**定义处**。函数写得很完整，docstring 也写得很清楚（"用户已建的音色档
如果绑着已下线的模型，试听/聊天必然 400，启动时静默修复"），但全仓没有第二个调用点。
功能写好了却没接线，等于没修。

**同一处还有两个隐患**（都在 `webapp.__init__` 的迁移块里）：
1. 只调了 `migrate_stale_tts_model`，漏掉音色档那一半——两半本来就该一起跑；
2. 写盘是 `io.open(path, "w")` **裸写**：非原子、不 fsync、不拿锁。这与
   `Config.save()` 审计出的 P0（并发写出含 NULL 的半个 YAML → 之后所有保存永久失败）
   是同一类事故，只是入口不同。

**改动**：新增 `src/config_migrate.py`——两个迁移函数搬出来（纯函数，可被轻量测试直接打），
加唯一入口 `migrate_stale_tts_config`（**同时跑两半**）+ `dump_config_atomic`
（临时文件 + fsync + os.replace，Windows 退避重试；写盘副本加密，幂等）。
webapp 启动期改调唯一入口 + 原子写，删掉两个函数定义（全仓无外部引用，不留
误导性的兼容别名），迁移失败从 `except: pass` 改成 debug 日志。闸门 48→49。

**测试的重点不是"算得对"而是"真的接上了"**（33 项断言）：
- 唯一入口是否同时跑两半；webapp 是否真的调了入口（源码级断言）；
- 端到端：迁移 → 写盘 → `load_config` 读到新值；
- 原子性用 **inode 变化**判据（`os.replace` 会换成临时文件的 inode；原地截断则不变）。
  这条比"返回值是 True"有区分力得多。

**三刀变异**：① 入口只跑模型那一半 → 6 项红；② webapp 回退成只调模型 → 接线断言红；
③ 原子写换回裸写 → 目录不存在用例直接崩。

**顺手做的全仓体检**：判据是"模块级 def 在全仓零引用"，命中 33 个，逐一定性后：
- 6 个是旧主进程采集架构的遗留（`_resample_pcm16` 等，注释写着"决定性修复"，
  但采集早已改独立子进程，修复在 `mic_proc.py` 里重写了）→ 删，270 行；
- 2 个是**误报**：`load_get`/`load_post` 是 FastAPI 路由，装饰器注册不算名字引用；
- 1 个是**该用没用但接线要设计**：`_similar_summary`（屏幕摘要去重）——连配套的
  状态变量都不存在，属于"写了一半"，本轮不动，单独排期；
- 其余是工具/诊断函数与旧版实现，清单见 `死代码体检_2026-10-19.md`。

**方法论教训（写进闸门设计里）**：
- **装饰器注册、前端按名字调用、`getattr` 反射**是这类扫描的三个盲区。第一版只看
  `.py` 语料，差点把 webview 暴露的后端 API 全判成死代码。
- 删代码前先问"它承载的实测知识有没有别处记录"。`_resample_pcm16` 的 docstring 里
  写着"K20 Pro 用 16k 开流恒静音、44.1k 正常"——这条是实现"为什么按原生率开流"的
  唯一记录，删函数前先搬进 `mic_proc.py` 的 `TARGET_RATE` 注释。
- 这套扫描**不进闸门**：误报率取决于覆盖度，硬编码成门只会制造噪音，留作一次性工具。

---

## 屏幕摘要去重接线：死代码体检的唯一"用户可感知"遗留（2026-10-20 00:1x，`dccd6ea`）

**接线前先纠正了它自己注释里的误解**。`_similar_summary` 的注释写"否则停留页面时
会疯狂重复搭话"，但"疯狂重复搭话"其实由 `_flush_screen_batch` 里的
`_similar_to_recent`（**输出**侧）挡着——两层去重分工不同：

| 层 | 函数 | 防什么 |
|---|---|---|
| 输出 | `_similar_to_recent` / `_duplex_dedup_window` | 这句话与最近说过的太像 → 不开口 |
| 输入 | `_similar_summary`（本轮接线） | 轨迹里堆满同一场景的摘要 → 模型顺着重复 |

不接的代价是具体的：同一窗口停留时，视觉模型每隔几秒上报一次几乎相同的摘要，
`_screen_batch_buf` 直接 append → 批量整合的轨迹变成"在游戏；在游戏；在游戏"。
白烧 token，而且这种重复输入本身就在诱导模型重复输出。

**做法**：新增 `_screen_activity_repeat(buf, summary, app_hint)`——只和**最近一条带
摘要的条目**比，且要求 `app_hint` 也相同（同窗口换了活动就该算新条目，不能把真实
变化吞掉）。`_on_screen_activity` 在 `append` **之前**调用，命中则只刷新 `ts`
（表示"还在这个场景"）。缓冲没变长也就不触发"攒满立即 flush"，但周期 flush
（`_screen_batch_loop`）照常跑，不会因此变哑巴。

**测试**（`src/tests/test_screen_dedup.py`，闸门「屏幕摘要去重」，49→50）：
- 判据 6 项（相同/互为子串/差一个语气词/无关/空串/全标点）；
- 缓冲层 7 项（含 app_hint 变化不合并、跳过无摘要条目）；
- **真行为**：`ProactiveStrategy.__new__` 造桩（替换 `_llm`/`_in_call`/
  `_record_ocr_moment`/`_screen_batch_task`，不走 `__init__`），直接喂
  `_on_screen_activity`——连喂 5 次相同摘要，缓冲只有 1 条；换内容、换 app_hint
  都能正常进缓冲；
- 源码级接线 + **顺序**断言。

**三刀变异**：摘掉去重块 → 6 项红；把 `append` 挪到去重之前 → 顺序断言红；
去掉 `app_hint` 判断 → 1 项红。

**这一轮最值钱的教训：顺序断言不是冗余**。把去重放到 `append` 之后，
**行为测试全部照过**——因为 append 后立刻命中自己，表现和正确实现一模一样。
只有"去重必须在 append 之前"这条源码级断言抓得住。凡是"先判断再入队"的逻辑，
行为测试看不见顺序，必须单独断言。

**闸门偶发**：首轮 50 项里 `doctor 自检结构` 报 FAIL（68.3s），但单独连跑 2 次都
通过，"先跑壳自愈再跑 doctor"也通过——**未复现的一次性失败**。潜在脆弱点在于
`test_doctor.py` 的 [15] 会 `subprocess` 真跑 `tools/doctor.py`（含网络/端口探测）。
重跑全量 50/50 PASS，暂记为观察项。

---

## 真机验证：迁移写错字段名 + 出厂模板自带失效模型（2026-10-20 00:4x，`e0a22f5`）

**为什么这轮必须做真机验证**：前几轮所有结论都建立在"代码读起来对"上——33 项断言、
三刀变异、闸门全绿。但**测试里写的字段名也是我以为的**，所以它证明不了配置真能被修好。
于是拿真实 `config.yaml` 的副本跑了一遍 `CharacterAPI` 启动路径。

### ① 迁移写了字段名，等于白做

`migrate_stale_voice_profiles` 写 `prof["voice"] = "Cherry"`。但音色档的音色字段是
**`voice_id`**：

```python
# src/voice/tts_backends.py:212
self._voice_id = getattr(config, "voice_id", "female-shaonv") or "female-shaonv"
```

（`voice` 在 minimax 后端是 `fallback_voice`，完全另一回事。）

真机副本实测迁移结果：

| 字段 | 迁移前 | 迁移后（修复前） |
|---|---|---|
| `model` | `cosyvoice-v3.5-plus` | `qwen3-tts-flash` ✓ |
| `voice_id` | `cosyvoice-v3.5-plus-bailian-e683…` | **原封不动** ✗ |
| `voice` | 不存在 | 凭空多出 `Cherry`（没人读） |

**模型换了、音色没换 → 请求照样 418。** 而 `tts_backends.py:928` 早就写着实测结论：

> 百炼 engine 418 = 该 voice_id 不可用（复刻音色过期 / 未生效 / 地域不符），
> **与 model 匹配无关**——排查时先换一个已知可用的 voice_id 交叉验证，别在 model 上打转。

迁移恰好只改了 model。修：写 `prof["voice_id"]`，判据同时读 `voice_id`/`voice`。

### ② 根因：出厂模板本身带病

`doctor` 报了个 WARN：**「config.yaml 是出厂默认值，但 data/ 里有使用痕迹」**。
顺着查 `config.release.yaml`，发现模板里同时写着三个已下线的值：

- `voice_profiles[0].model: cosyvoice-v3.5-plus`
- `voice_profiles[0].voice_id: cosyvoice-v3.5-plus-bailian-e683…`
- `cloud_tts.dashscope_cosy.model_name: cosyvoice-v3.5-flash`

cosyvoice 全系列在百炼已下线（实测 400），`qwen-audio-3.0-tts-*` 同样，只有
`qwen3-tts-flash` 返回 200。**也就是说：每个新用户装完就是一套失效配置**，
试听必 400，还会以为是自己音色 ID 填错——这正是"能说"这条链在源头就断了。

修：模板改成 `qwen3-tts-flash` + 预置音色 `Cherry`（`cloud_tts.py:50` 有实测可用的
预置音色清单：Cherry / Serena / Chelsie / Ethan），并在注释里写明"想要自己的音色请用
qwen3-tts-flash 重新克隆"——克隆音色 id 绑旧模型族，换模型后不通用。

### ③ 新增根因闸门 [13]

出厂模板里出现 `STALE_TTS_MODELS` 就红。变异（把 model 改回 cosyvoice）→ 精确报出
`$.voice_profiles[0].model=cosyvoice-v3.5-plus`。另有反向断言"模板里确实还写着
qwen3-tts-flash"，防止靠整段删配置过关。

### 真机验证结果

- 副本跑 `CharacterAPI`：`model=qwen3-tts-flash`、`voice_id=Cherry`、无 `voice` 字段、
  内联 `model_name` 已换、`load_config` **0 降级提示**；
- `doctor` 真实环境：**16 PASS / 3 WARN / 0 FAIL**（模型渠道 ✓ 1223ms/666ms、
  语音合成真合成 43406 字节、密钥 1 处 DPAPI + 13 处 `${ENV}` 不落盘）；
- 真 `config.yaml` **未改动**（已备份），下次启动自动迁移。

**教训（已进 MEMORY）**：改配置字段前必须查"后端实际读哪个 key"，判据优先级是
**后端 `getattr` 读取点 > 出厂模板实际字段 > 注释/命名直觉**。测试全绿也救不了
字段名写错——因为测试里写的也是同一个错误假设。任何"迁移/修复配置"的代码，
都要拿真实配置文件的副本跑一次。
