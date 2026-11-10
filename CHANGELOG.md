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
| `PR#18w` | 19:0x | test | **跳过不是通过 —— 合成功能整段坏了，门打出「27 通过 / 0 失败」**：① `TTS 多供应商`（`tools/test_tts_multi_provider.py`，30 条判据 / 5 个服务商）写得挺细，连「硬编码 6 个预设」这种脆判据都在 2026-10-21 被改成「非空 + 每项有 id」。② 但它有**静默跳过**：第 3 段（`FishAudioBackend` 全链路）整块包在 `try/except Exception: print("! 跳过")` 里 —— 把 `synthesize` 改成直接抛错，这段 5 条判据一条不跑，结果打成「**27 通过 / 0 失败**」→ **exit 0 全绿**。合成功能彻底坏了，门是绿的。③ 另有五处零判据（改完门仍 30/0 绿）：**全局 Key 多源兜底**（`cloud_tts` → `cloud_omni`，2026-10-17 修「又要搞一遍 key」那条）、**merge「空字符串=不改」**（F3：引导页只回传 model 时把已配 Key 静默擦空）、**JSON(audio_url) 二次下载**、**空音频**、**缺 reference_id**。④ 还有一处「红了但崩了」：旧单层升级逻辑坏掉时，`raw6["voice"]["clone"]["cloud"]["dashscope_cosy"]["api_key"]` 抛 KeyError，**整份文件崩在半路** —— 第 6 段一条不跑，报告里只有 Traceback、没有「哪条判据红了」。⑤ 修法：`check()` 支持附注；新增 `_finish()` **条数总账** `EXPECTED_CHECKS=45`（少跑一条就红，多跑也红提醒改数）；第 3 段的异常本身记 FAIL 并打出原因；产物从 `tools/_fa_test_out.mp3` 改成 `tempfile` 目录（原版中途崩了会留脏文件）。⑥ 补 15 条：全局 Key 回填 + 「显式 Key 优先于全局兜底」+ `cloud_omni` 兜底、merge 三条（空=不改 / 显式新 Key 覆盖 / 别名 aliyun 入 `dashscope_cosy` 桶）、请求体 `reference_id`+`format=mp3`、请求头 Bearer+model、HTTP 401 带状态码、JSON `audio_url` 二次下载并落盘、JSON 缺 `audio_url` 抛错、空音频抛错、缺 `reference_id` 抛错且指名。⑦ 新刀 `tools/_qc_tts_multi_provider_mutation.py` 十四刀：别名归一 / 全局 Key 兜底 / merge 清 Key / merge 旧值优先 / 旧单层不升级 / merge 不归一别名 / fish 占位提示 / payload format / HTTP 错误吞掉 / JSON 分支 / 空音频 / reference_id / 工厂丢 fish / 合成整段抛错。 | 判据 **30 → 45**；十四刀 **14/14 全红且各自点名**；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **240 项全部有着落**；站点 7 PASS/0 FAIL（GATES 239→**240**）；K11 分层 8/32=**40**、0 处异常；全量 `check_all` **240 道门 → 235 通过 / 5 跳过 / 0 红，exit 0**（1h29m04s） |
| `PR#18v` | 17:1x | test | **判据在，牙齿不在 —— 关闸后只等 0.6 秒就断言「不抓屏」，而采样周期是 3 秒**：① `看屏热生效`（`tools/test_desktop_watch_hotapply.py`，14 条断言 / 6 组）守的是 2026-10-09 用户实锤的「设置页关掉看屏，模型还在挂载、截图/分析管线还在跑，必须重启才停」，名字和定位都对，判据是空的。② 十把探针下去，**六处零判据**。最刺眼的是 B 组：`B2 关闸后不再抓屏` 在关闸后只等 **0.6s** 就断言计数不涨 —— 而基础采样间隔是 **3.0s**（`_next_interval` 下限 0.5s），**一个周期都没走完**，`after == before` 恒真；把 while 里「每轮重读开关」整段删掉照样绿。③ E 组更绕：`E1/E2 自动恢复不推翻用户关闸` 断言的是 `_disabled_at==0` 时不该被 AUTO_RECOVER 唤醒，但**用户关闸路径上那段代码根本执行不到**（硬闸在更前面就 `return None`），而且用例还手工 `an._disabled_at = 0.0` 去「造已过恢复窗口的假象」—— 正好把原始 bug 的时序盖住。实测：删掉自动恢复整段（永久哑火）全绿；硬闸不再把 `_disabled_at` 归零（**原始 bug 回归**：先熔断、用户再关闸，90s 后分析线自己复活）也全绿。④ C 组只测了一半：只验「关闸不分析」，不验「开闸真抓」—— `capture_now` 整条改成 `return` 全绿。⑤ 从没测过「关掉再打开」：把关闸分支的 `continue` 改成 `return`（循环退出、重开必须重启进程）全绿；不清 `_last_hash`（重开后同画面永远不分析）也全绿。⑥ 还有一处是**等价变异**而非洞：`_disabled_at > 0` 这个守卫不可达（关闸→硬闸先 return；开闸→上面那段重开分支先清），删掉没有任何行为差异 —— 注释把功劳记在它身上，实际承重的是「硬闸先跑 + 关闸时归零」。⑦ 判据先行 14 → **32 条**：B 组把间隔调到 0.2s、开闸先跑满 1.2s 拿基线（before=3）、关闸后再跨三个周期；C 补「关闸连帧都不抓」「开闸真抓一帧并送分析」「事件计数只在真抓时加」；D 的桩换成会记账的正常桩（原桩没有 `vision_describe`，闸门被拆时崩在 AttributeError 也返回 None，`r is None` 分不清是被挡还是崩了）；E 按真实时序重写（先连败 3 次打熔断 → 再关闸 → 再时间流逝）；F 补「关闸时没打云端」「重开后确实放行一次」；新增 **G**（失败熔断 90s 后自动恢复，含 `VISION_DISABLED` 广播）与 **H**（关掉再打开：循环自己活回来 + 重新分析一帧）。⑧ 抓屏桩从「抛异常计数」改成「返回合法小帧计数」—— 异常路径会刷 Traceback，且跳过 `_last_hash`/`_next_interval` 更新，测的不是正常采样节奏。 | 断言 **14 → 32**；十二刀 **12/12 全红且各自点名**；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **239 项全部有着落**；站点 7 PASS/0 FAIL（GATES 238→**239**）；K11 分层 8/31=**39**、0 处异常；全量 `check_all` **239 道门 → 234 通过 / 5 跳过 / 0 红，exit 0** |
| `PR#18u` | 09:5x | test | **测试绿的原因，跟它名字里那件事无关**：① `关系集成`（`tools/test_relationship_integration.py`，13 个用例）绿得很稳，探针捅出**七处零判据**，其中最刺眼的两处不是"没写判据"，而是"写了判据但测的不是那件事"。② `test_04`「同一句 AI 文本 5s 内只记一次」：场景是「1 句用户话 + 3 次同样的 AI 回复」，断言 `turns <= 1`。它绿是因为**队列只有一句** —— 第一次回复就把队列取空了，后两次走到 `if not self._pending_user_queue: return` 就返回，**去重那一行根本执行不到**。探针：把去重整段删掉，照样绿。顺带 `<= 1` 连"根本没判"（turns=0）也算过。③ `test_20`「publish 不应等裁判跑完」：它从头到尾**没有 `bus.run()`** —— 事件只进了队列，一个 handler 都没执行，SlowLLM 那 1.5s 从没被等过，`dt < 0.5` 恒真。探针：把 `self._spawn_judge(...)` 换成 `await self._judge_and_apply(...)`（同步判定会把整条总线堵 1.5s），照样绿。④ 另外五处是**整条支路零判据**：`enabled` 三处早退（所有用例都用 `enabled=True`）、裁判异常兜底（没有任何用例让裁判抛过）、失败气泡 `blocked/failed` 过滤、`source` 缺省回退 `chat`、`source=duplex` 当场判（语音用户的话）、以及 `is_proactive` 那条支路（`test_03` 用的是 `source=proactive`，在更早的 `_CHAT_SOURCES` 过滤处就返回了，走不到它）。⑤ 判据先行 13 → **19 个用例**：`test_04` 改成「连发两句 + 同一句回复投两次」（队列还有货才验得到去重），断言 `turns == 1` 且队列剩第二句；`test_20` 改成量**后一条事件多久被处理**（异步毫秒级 / 同步 1.5s）；新增 `test_08` 关掉关系后一句不判（并单独隔离 AI 侧守卫 —— 第一版这把刀没红，因为 `_on_user_text` 的守卫让队列恒空，AI 侧早退永远走不到"有货可配"）、`test_09` 裁判抛异常仍按规则兜底记一轮（测试期间把该 logger 压到 CRITICAL，不往绿着的门里掺 Traceback）、`test_14` 失败气泡/被拦/空文本不算一句、`test_15` 缺省 source 回退 chat、`test_16` duplex 用户话当场判且不进配对队列、`test_17` 聊天页主动发言记 initiative 但不清空用户队列。⑥ 新刀 `tools/_qc_relationship_mutation.py` 十三刀：去重/开关/裁判兜底/气泡过滤/source 回退/duplex 分支/同步判定/streamed 过滤/队列 LIFO/主动发言清队列/上下文丢失/单例失效/角色共用状态文件。 | 门 **13 → 19 个用例（断言 29 → 49）**；十三刀 **13/13 全红且各自点名**（`streamed_filter` 连坐 8 项、`queue_lifo` 2 项）；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **238 项全部有着落**；站点 7 PASS/0 FAIL（GATES 237→**238**）；K11 分层 8/30=**38**、0 处异常 |
| `PR#18t` | 06:5x | test | **判据是从被测代码里读出来的 —— 删掉守卫，判据跟着消失**：① `引导页集成`（`tools/test_onboarding_integration.py`，108 条断言）绿得很稳，探针捅出五个洞。② 最狠的是 `[C1]`：它用 `_guards_cfg(nm)` 去 `webapp.py` 里找 `if not self._cfg`，**再用这个结果决定**该不该断言 `success is False` —— 等于让被测代码自己出考卷。把 `getModelCatalog` 的守卫删掉，端点被静默降级成「无守卫端点，返回 dict 就行」，**门全绿**。③ 第二个：`[A4]` 的四条判据全压在 `MODE_SEQ` / `buildFlow` 上，而 `buildFlow` 自 2026-10-20「首启只做建角色→体检」那次起**全仓零调用**（只剩定义和两处注释），真正跑的链路是写死的 `let FLOW = []; ['screenRole','screenSummary'].forEach(...)` —— 一句判据都没有；更糟的是第四条断言叫「FLOW 由 buildFlow 动态拼（**不再写死单条**）」，**与现状正好相反**。把 `'screenRole'` 从实际链路里删掉，门照样全绿。④ 第三个：`[A8]` 验的是 `docs/mockups/…v8_20261012.html`，那份设计稿从没进过库，`r8` 恒为 `None` → `if r8:` 里两条断言**永久不执行**，不红、不报、不留痕 —— 比"发现栏腐烂"更隐蔽（发现至少还打印一行 ⚠）。⑤ 另外三处是「断言名承诺的比条件验的多」：`[B6]`「deleteModelQuant 未装档**返回失败**」只查了 `isinstance(dlq, dict)`；`[B17]`「validateOnboarding **接受**精简 payload」只查了 `"success" in vo`（`{"success": False}` 也满足）；`[B11]`「switchCharacter 不存在角色被拒」只走了「无运行时 → 配置级回退」那条路，`if result is False:` 的运行时分支**一次都没执行过**（`_dialogue` 是 `None`，`_run_coro` 直接返回 `None`）。⑥ 判据先行 108 → **119**：A4 改成验**实际链路字面量**（含"屏 id 必须在 DOM 里"），MODE_SEQ/buildFlow 如实降级成「回滚备用」；A8 搬到真身 `src/ui/web/onboarding_role.js`（先剥注释再断言，否则注释自己就满足判据）；A1 补下限（一个调用都没解析出来时「全部存在」是真空真）；B6 验 `success is False` + error 说清"不存在"，另加未知档位；B11 造最小运行时（真事件循环 + 桩 dialogue）让运行时分支真跑一次；B17 验 `success is True` + checks 结构 + `ok` 的自洽定义；C1 把契约**手写**成 `CFG_GUARDED`（14 个）/ `CFG_FREE`（3 个）两张表，行为断言只认表，源码一致性另立一条断言（删守卫会**红**，不再消失）。⑦ 新刀 `tools/_qc_onboarding_mutation.py` 二十四刀：A 首启链路丢角色/顺序反/屏 id 写错、回滚表断链、桥面改名、下载器不走桥、建角色写错槽/自建厂商表、外壳守卫失效；B 模型名不落桶、空 Key 擦 Key、卸载不存在的档报成功、未知档位放行、Live2D 兜底被拆、运行时/回退两条拒绝、体检报失败、`ok` 取反、档位不映射、引擎清单少一家；C 删 `_cfg` 守卫、守卫在但返回成功、脏数据必抛、未知引擎照写。 | 门 **108 → 119 条**；二十四刀 **24/24 全红且各自点名**（`flow_bad_id` 2 项 / `b10_m3d_guard` 2 项 / `b17_validate_fail` 2 项 / `b18_mode` 3 项 / `b19_engines` 4 项 / `c1_guard_gone` 2 项 / `c3_unknown_engine` 2 项）；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **237 项全部有着落**；站点 7 PASS/0 FAIL（GATES 236→**237**）；K11 分层 8/29=**37**、0 处异常 |
| `PR#18s` | 04:5x | test | **最厚的一道门（1047 行）三处空转，最狠的是「装完不验收」**：① `打包依赖分级`（`src/tests/test_make_dist_deps.py`）是全项目最厚的一道门，而且**写得比大多数门都讲究** —— [14] 专门写了「先判 env 是否存在，再判内容：否则 `"PYTHONPATH" not in {}` 会恒真……空洞通过比不通过更危险」。所以这一轮不能假设它漏，只能一处一处探。② 三处全空（实测，改完门仍 EXIT 0 全绿）：`[C10-6]`「配了凭证但没 signtool 应拒绝」在**没抛异常**的分支里断言 `not os.path.isfile("signtool.exe")` —— 那是断言**文件系统**不是代码，把守卫改成 `if False:` 全门全绿；`[C10-14]`「`--sign` 必须连着 `--installer`」只 grep 错误文案，守卫改成 `if False:` 后文案还在、门全绿而 `--sign` 已不再报错；`install_deps` 末尾 `if verify: return verify_deps(...)` **整段没有判据**，换成 `return True`（装完不验收）全门全绿 —— 这是 [14] 那件事的镜像：「规矩没写进 `env=`，等于没写」→「验收没被调用，等于没验收」，而 [14] 只直接测 `verify_deps` 本身，从没测过 `install_deps` 调不调它。③ 先补判据 135 → **142**：C10-6 改成显式给不存在的 `DANIA_SIGN_TOOL`（任何机器都必抛）+ 先问 `find_signtool()` 再定判据（装了 SDK 就如实说「不适用，未验证」）；C10-14 改成用 `ast` 找「body 含那句提示」的 `if` 直接验**条件**；新增 `[26]`（verify=True 必调一次、拿到包内解释器路径、verify=False/defer_verify 不许调、验收不过必须返回 False）。④ 新刀 `tools/_qc_make_dist_mutation.py` 十二刀：A 不查 signtool / B `--sign` 守卫失效 / C 装完不验收 / D `_pth` 写 LF / E DLL 落 `DLLs/` / F 非 DLL 也搬 / G `enable=False` 不删 / H `tier_specs` 永不 break / I purge 清到 `python/` / J 指纹不含壳脚本 / K 档位静默降级 / L 不裁 locales。| 门 **135 → 142 条**；十二刀 **12/12 全红**且各自点名（A 2 项 / C 3 项 / E 2 项 / F 3 项 / K 7 项）；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **236 项全部有着落**；站点 7 PASS/0 FAIL（GATES 235→**236**）；K11 分层 8/28=**36**、0 处异常 |
| `PR#18r` | 03:1x | test | **测试里抄了一遍生产代码，然后测抄件**：① `双工停止与麦克风`（`tools/test_duplex_stop_and_mic.py`）的 A/B/C 三段函数注释明写「复刻 `_stop_duplex_client` 的分派核心 / 复刻 `_track_mic_level` 的电平平滑 / 复刻 `get_role_voice_context` 的 engine_ui 判定」—— 生产实现被手抄进测试，然后测这份抄件，生产代码改成什么样都不会红。而**同一个文件 D 段的注释早就写对了**：「不复制逻辑，避免"假绿"——复刻逻辑两次写得一样不算测试」。② 改法：三段全改成调真实实现（`__new__` 绕过 `__init__`），B 段从「造假浮点数喂抄件」改成「造 16-bit PCM 喂 `_track_mic_level`」，实测复现 BUG-2 原始形状（`level=0.00375` < 阈值 / `disp=0.04501` > 阈值）。③ 换成真实现后逼出两件「注释在骗人」：A1 括号里那句「旧代码 `await None` → TypeError → **stopped 恒 False**」是**假的** —— `await f()` 必然先求值 `f()`，客户端真的停了；探针实测把生产代码回退成 BUG-1 原形，A1/A2 **照样绿**，唯一会红的只有日志。真正坏掉的是「异常逃逸 → 调用方 `self._duplex_client = None` 被跳过 → 引用仍指着已停客户端」，而现在 try/except 已挡住逃逸、可观测后果只剩一条日志 → 补 **A5**（ERROR handler 挂 `proactive.logger`，正常分派零异常日志）；`proactive.py` 里同一条假因果一并改正。④ 第二件：`_watchdog_tick` 的 `and self._mic_proc is proc`（换轮保护，旧看门狗别杀新一轮子进程）**一条判据都没有**（D5/D6 都是 `is proc`，D7 桩成 False 又不过它），删掉全绿 → 补 **D9** 真实竞态用例。⑤ 新刀 `tools/_qc_duplex_stop_mic_mutation.py` 十刀跨三文件：A 分派回退 / B `_mic_disp` 退化 / C 停滞判据比较反向 / D 不看 running / E 不看 `_mic_proc` / F 去掉换轮保护 / G 停滞也不杀 / H 杀完不清引用 / I 只清引用不 kill / J 出声方式只读静态 config。| 门 **21 → 22 条**（A5/B4/D9 本轮新补）；十刀 **10/10 全红**且各自点名（C 连坐 4 项、G 连坐 2 项、I 连坐 2 项、J 连坐 3 项）；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **235 项全部有着落**；站点 7 PASS/0 FAIL（GATES 234→**235**）；K11 分层 8/27=**35**、0 处异常 |
| `PR#18q` | 02:2x | test | **15 节、四十来条断言的「最扎实」那道门，进程回收这条支路上五处关键行为全都没判据**：① `壳自愈与退出清理`（`src/tests/test_pet_shell_selfheal.py`）守的是「桌宠无声消失」「无限 spawn Electron」「孤儿窗口滞留」三类用户看不懂的故障。② 读判据时五处全空（实测，删掉后自测照样全绿 exit 0）：`_try_restart` 的 `if was_top: self.on_top = True`（恢复置顶）—— 而原文件 [1] 里有个 `calls = {"launch": 0, "show": 0, "top": 0}`，**`top` 从头到尾没被 ++ 过**，也没人读它；`_cleanup_proc` 的 `elif force: _kill_tree(proc.pid)`（主进程已退、子进程还在 = 孤儿 Electron 窗口，Electron 会**无输出地立刻退出**，项目里最难查的坑）；活进程 `wait(5)` 超时后的升级强杀；`time.sleep(RESTART_BACKOFF)`（连续崩时的退避，作用是「别把 CPU 打满」）；`close()` 里的 `sendall(b"quit\n")`（先礼后兵）。③ **为什么 15 节还是漏**：节数多不等于覆盖面广。15 节里有 9 节在测配额/降级/握手文件，而五处全在「进程回收」和「重启时恢复状态」两条支路上 —— **分支比断言更值得数**。④ 先补判据：47 → **57** 条（[1] 加置顶、[1b] 加「没置顶就不碰」、[6] 加三条杀进程树分支（含 force=False 的不越权边界）、[11] 加 quit、[16] 加退避（只断言下界，不赌上界））。⑤ 新刀 `tools/_qc_pet_shell_mutation.py` 八刀：A 不恢复置顶 / B 不杀子进程树 / C 不升级强杀 / D 不退避 / E 不发 quit / F 配额差一 / G 拿掉整条守卫 / H 窗口不滚动。| 断言 **47 → 57**；八刀 **8/8 全红**且各自点名（B 连坐 3 项、F 连坐 11 项、G 连坐 2 项）；还原字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **234 项全部有着落**；站点 7 PASS/0 FAIL（GATES 233→**234**）；K11 分层 8/26=34、0 处异常 |
| `PR#18p` | 00:5x | test | **这条通道坏了不会报错，只会「照样返回 200，返回的东西不对」—— 而它唯一的名字检查只被单段名字验过**：① `RPC 通道`（`src/tests/test_rpc_server.py`）守壳↔后端那条没有类型检查的 HTTP 通道：19 条等价性 + 三条安全边界。失效形状与端口绑定相反：端口坏了是「起不来」，这条坏了是「照样 200」，或者更糟 —— **私有方法能调到**。② 读判据读到两处空转（实测）：把 `all(_is_exposed(p) for p in method.split("."))` 改成 `_is_exposed(method)`（整串检查）→ 自测**照样全绿 exit 0**，而那个退化正是**点号绕过**：`sub._secret` 整串不以 `_` 开头，校验通过、私有方法被调走；把 `_resolve` 换成 `getattr(api, dotted, None)`（不再支持点号）→ 同样全绿，而它的注释写着「与 pywebview 的 `get_nested_attribute` 一致，支持子对象」。③ 根因：19 条等价性用例**全是单段名字**，而单段名字上「逐段检查」与「整串检查」完全等价、「点号循环」跑一轮与直接 `getattr` 完全等价 —— **判据只覆盖了退化不产生差别的那个输入域**。④ 先补判据：FakeAPI 加子对象 `sub`（`ping` / `_secret` / `a__b`），等价性加 `sub.ping`，安全检查加四段点号用例（私有段 403 + 不泄漏 / 不存在 404 / 空段 403 / 中间 dunder 403）；运行时断言 21 → **26**。顺带把参照实现从 `getattr(api, method)` 换成自己走点号的 `_direct_call()` —— **不能拿 `_resolve` 当参照**，它坏了参照一起坏，等价性永远成立。⑤ 新刀 `tools/_qc_rpc_mutation.py` 十刀：A 令牌恒真 / B 逐段→整串 / C 不支持点号 / D 不排空请求体 / E 去掉 MAX_BODY / F 去掉 degraded / G stop 空操作 / H 业务异常改 200 / I 去掉中间 dunder 限制 / J 去掉空段限制。| 运行时断言 **21 → 26**（19 → 24 个 `check()` 调用点）；十刀 **10/10 全红**且各自点名（A 3 项 / B 3 项 / C 1 项 / D 3 项 / E 2 项 / F 1 项 / G 1 项 / H 1 项 / I 1 项 / J 1 项）；还原字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **233 项全部有着落**；站点 7 PASS/0 FAIL（GATES 232→**233**）；K11 分层 8/25=33、0 处异常 |
| `PR#18o` | 23:3x | test | **这道门自称「两刀变异已验」，而仓库里一把刀都没有 —— 手工验过的刀不会回潮，注释也不会**：① `check_all.py` 里 `WS 端口绑定` 的注释写着「两刀变异已验：删重试→「确有重试」红；改回裸 raise→三条诊断断言红」，`ls tools/_qc_*ws*` 返回 `No such file`。手工跑过一次、没提交，验完就蒸发了。② 读判据时读到**两处空转**（都是实测）：把 `logger.warning(...)` 整段删掉，自测**照样全绿 exit 0** —— 「先重试再报错」里的「重试」在日志上毫无痕迹；把 `serve()` 提到循环外（**只真的 bind 一次**，然后空睡 5 次再抛同样的错），自测**照样全绿** —— 耗时 5.0s 骗过了「确有重试（耗时 ≥ 5s）」。③ 第二条是致命的：闸门测的「重试」只是「等了 5 秒」，不是「又 bind 了 5 次」。而 2026-10-20 03:11 那次崩溃的形状恰恰是「旧实例占用 1~2 秒后释放」—— 空睡 5 秒一秒都修不了它，原文件却只有两端（一直空闲 / 一直被占），中间那段从来没测过。④ **先补判据再加刀**：契约 2 补日志断言（每次重试都要留痕、日志带端口号+「重试」字样），新增契约 4「端口中途释放必须真的重绑」（1.2s 定时器关掉监听 socket 模拟旧实例退出）；13 条 → **19 条**，八刀才有东西可砍。⑤ 顺手修一个正文缺陷：`rstrip("。")` 把 OSError 末尾句号吃掉却不再补，拼出「…只允许使用一次若这是第二个达妮娅实例…」，崩溃弹窗里就是一整句读不通的话。⑥ 补判据时踩到一个**第二份真相**：先写的断言是 `"。若" in msg`，八刀里 H 刀没红 —— 因为 `holder` 自己就带句号（「端口 18794 正被进程 PID=… 占用。」），拼在「若这是」前面，那条断言**恒真**。改成把 last_err 那一段单独切出来判，H 刀才红。⑦ 刀里也踩了一次「崩了 ≠ 认出来了」：H 刀第一版写成 `rstrip("。")  # 注释`，行尾注释把后面的 `, holder)` 一起吞了 → SyntaxError，套件判它「没抓住」（正确），改成 `+ ""` 才是「语法正确、行为退回旧 bug」。| 自测 **19 项全 PASS**（13→19，新增 6 条）；八刀 **8/8 全红**且各自点名（删重试 / 间隔归零 / 裸 raise / holder 恒空 / 空闲误报 / 删日志 / 假重试 / 吃掉句号）；空变异体检 **CERTIFIED**（D0 绿 / D1 exit 1 / D2 exit 1 均自行报失败）；`check_gate_registry` **232 项全部有着落**；站点 7 PASS/0 FAIL（GATES 231→**232**）；`_qc_knife_vacuity_mutation` K11 分层 8/24=32、0 处异常 |
| `PR#18n` | 22:2x | fix | **两道壳层静态闸门的自测只测 `scan()`，退出码零判据 —— 而「只会打印不会判红，比没有更危险」正是这两个文件自己写在头部的告诫**：① `check_async_blocking.py` 守「事件循环被同步调用占住」（2026-10-18 一天撞到四处：gewechat 回发 / qzone 发布 / tts 的 `time.sleep(0.6)` / heavy_apps 的 `urlopen(timeout=5)`）；`check_await_sync.py` 守「`await` 一个同步方法」（微信回复从来没真正发出去过那次）。两道都自带自测、七条正反例，**但七条全部只调 `C.scan()`，从不碰 `C.main()`**。② 实测：把 `return 1 if all_hits else 0` 改成 `return 0`，闸门退化成「只打印不判红」—— 可疑点一个不少地打印出来、全量里永远绿 —— 而自测照样打印「全部通过 ✅」并 exit 0。两个文件都中。③ **先补判据再加刀**：自测各加「有可疑点 → 1 / 干净 → 0」两条断言，验证改完退出码后自测确实变红，A 刀才写得出来。④ 补刀时撞出一个**真缺陷**：`check_async_blocking` 有一段 `WRAPPERS` 豁免，把 `to_thread(...)` / `run_in_executor(...)` **实参里**的调用标成 safe —— 可实参是在**事件循环里先求值**的，`to_thread(urlopen(...))` 的阻塞已经发生了。实测这种写法报 **0 处**，而它正是 `heavy_apps.pull_cloud` 卡首启 5s 的形状。删掉豁免、补一条正例（规范用法 `to_thread(time.sleep, 1)` 传的是函数引用、没有 Call 节点，不会被误伤），全仓 src 命中仍是 0 → 0。⑤ 两把刀各五刀，共 10 条变异，逐条对应一个漏报或误报方向。 | 协程调度自测 9/9、异步阻塞自测 10/10（分别新增 2 条与 3 条）；两把刀 **5/5、5/5 全红**且各自点名（退出码 / to_thread 实参 / 嵌套 worker / time.sleep / 同步函数；退出码 / 消费者表 / 规则 1 / 规则 2 / 异步生成器）；空变异体检均 **CERTIFIED**；`check_gate_registry` **231 项全部有着落**；站点 7 PASS/0 FAIL（GATES 229→**231**）；四步接进 CI `portable-gates` |
| `PR#18m` | 21:3x | test | **同一类越界，三个兄弟函数只测了一个；顺手跟进 master 4 个提交解掉合并冲突**：① `_read_file` / `_write_file` / `_append_file` 是同一段 `_safe_path` + `except WorkspaceEscape: return str(e)` 的三份复制粘贴，但 `test_workspace_guard.py` 只守了 `_write_file` —— `_read_file` 与 `_append_file` 在**整个 `tools/` 目录下出现 0 次**。后果不对称得很厉害：写越界是脏数据，**读越界是任意文件读取**（密钥盒 / `config.yaml` / 导出的凭据都能被模型读进上下文），而它一条判据都没有。② **先补判据再加刀**：第 6b 节先造一个真存在、真可读的区外文件（不造的话，「读不到」可能只是因为文件不存在，而不是因为越界被拦），再断言「返回里说明越界」+「没把区外内容读进来」+「区外文件没有被创建」+「沙箱里也没多出这个文件」。③ 刀跟着补：`工作区边界` D 写越界改名进沙箱（2026-10 那个 bug 的形状）/ E 读越界也照读 / F 追加越界改名进沙箱，三刀 → **六刀**；`命令守卫` D `allow_cmd` fail-closed 关掉 / E `shell=True`，三刀 → **五刀**（前四把都在前置检查上，没有一把碰真正执行的那一行）。④ **一个被自己否掉的做法**：先新建了 `_qc_workspace_guard_mutation.py` / `_qc_run_command_guard_mutation.py` 各砍一遍，跑出来 4/4、5/5 全红 —— 但那是**同一判据的第二把刀**，等于给覆盖率造第二份真相，还把 CI 时间翻倍。删掉两个新文件，只把真正新增的三把并进既有文件。⑤ master 从 `b945b4ca` 前进到 `c264700e`（含合入 89 个 PR），PR 被 GitHub 标成 `dirty`；做了一次真合并，5 处冲突：`check_delivery` 两边**独立修了同一个 bug**（取 master 侧，它的修法更完整）/ `check_all.py` 两边各加门禁（**都留**）/ 站点 4 处数字锚点（两边都不对，合并后是 229，由 `check_site_counts --fix` 回填）。 | 工作区边界 **20 PASS / 0 FAIL**（15→20）；六刀全红且各自点名（D「沙箱里也没多出这个文件」/ E「没把区外内容读进来」/ F 同 D）；命令守卫五刀全红（D「allow_cmd=false 拒绝」/ E「shell=False :: True」）；两把刀空变异体检 **CERTIFIED**（起点绿 / 空变异 exit 1 报 6 处、5 处异常）；合并后 `check_gate_registry` **229 项全部有着落**、`check_site_counts` 7 PASS/0 FAIL；PR 恢复 `mergeable=true / clean` |
| `PR#18l` | 20:5x | fix | **第五道补刀，补的过程发现判据自己有缺陷：`开机自启` 的清理路径依赖了被测代码**。这道门守「用户登录时到底跑什么」，18 条断言从没配过刀。最想打的一刀是「`disable` 删错值名」—— 那正是 2026-10-18 真实泄漏的那次（真实 HKCU Run 留一条指向已删 tempfile 的死命令，用户登录弹「无法运行」）。但写不出来：刀的 finally 只还原源码字节，删不掉已被错误逻辑漏掉的注册表项，而测试自己的 finally 调的**正是被变异的 `disable`** → 每跑一次刀就多留一条。想清楚之后发现这不是「刀不能写」，是**判据有缺陷**：清理依赖被测对象，`disable` 一旦真回归，`test_autostart` 自己每跑一次也会留残留。**先修判据再加刀**：`finally` 换成 `_raw_delete()`（直接用 winreg，与 `autostart` 实现解耦），A6 才安全。六刀：A1 不加引号 / A2 用 python.exe 不是 pythonw / A3 `is_enabled` 遇 OSError 返回 True / A4 `sync(False)` 也去 enable / A5 `read_command` 异常返回空串 / A6 `disable` 删错值名。 | 开机自启 18/18；六刀全红且各自点名；**A6 跑完复查 HKCU Run：7 个既有值、0 残留**（`_raw_delete` 在 `disable` 被破坏时仍清干净，这就是修复的实证）；空变异体检 **CERTIFIED**；登记对账 ✓（227）；站点 7 PASS/0 FAIL（GATES 226→**227**） |
| `PR#18k` | 20:4x | test | **第四道补刀：`密钥盒加密` 五刀 —— 最硬的一条安全底线，20 条判据从来没有刀**：它守的是「磁盘上不许出现明文 Key」（用户的 OpenAI / 百炼 Key、QQ cookie 全在 config.yaml 里），2026-10-18 起在跑，一次都没被验过牙。**这类判据的失效方式特别阴**：加密函数照常返回、`Config.save` 照常写盘、日志照常打「已加密」，只是密文里其实还是明文—— 没有任何报错，只有某天有人 `cat config.yaml` 才看得见。五刀：S1 密文不带 `dpapi:` 前缀（等于没加密）/ S2 `seal_tree` 不递归进嵌套 dict / S3 密钥字段表加宽（把 `name` 也当密钥）/ S4 解不开的密文不记失败（换机时静默丢 Key）/ S5 `is_sealed` 恒 False。**一个被否掉的变异方向**：本想用「`is_sealed` 恒 True」，但它会让 `decrypt_secret` 去 base64 解普通文本、`validate=True` 直接抛 `binascii.Error`，脚本带 Traceback 退出 —— 那是「崩了」不是「认出来了」。改成恒 False 才是判据该红的那条路。**刀的变异方向要选判据能开口的那条，不能选让判据闭嘴的那条。** | 密钥盒 20/0；五刀全红且各自点名；空变异体检 **CERTIFIED**；`闸门退出码` 0 命中；登记对账 ✓（226）；站点 7 PASS/0 FAIL（GATES 225→**226**） |
| `PR#18j` | 20:1x | test | **第三道补刀：`抓屏性能闸` 五刀** —— 它守的是一条**真炸过**的底线：2026-08-25 修掉的「抽帧 / 核显本卡炸」，游戏或锁快照期间画面长时间静止时 `_next_interval` 的 static 加速会把采样间隔打到 ~1s，每帧全屏抓屏 + resize + pHash + JPEG 编码。这道门 27 条判据（A/B/C/D/E/F）**从来没有刀** —— 「27 条到底有没有牙」一次没验过，而第三十轮刚改过它的 D8（缺夹具退回出厂模板）。五刀：G1 low_rate 档不再守低频底线（落回 static 加速）/ G2 静态加速乘数被去掉 / G3 主路径不再传 low_rate（云端接管时偷偷提速）/ G4 让位分支不再传 `low_rate=True` / G5 `main.py` 两处 `ScreenCapture` 构造去掉 `low_rate_check`。**G5 的锚点逐字出现两次**（1346 / 3214），因为 D11 的判据就是「**每一处**构造都要带」—— 所以这把刀给 MUTS 加了一个「期望命中次数」字段，G5 写 2、其余写 1，命中数不符一律报「锚点已腐烂」而不硬改。 | 抓屏性能闸 **27/0**；五刀全红且各自点名（A2 / A3 / B1 / B3 / D11）；空变异体检 **CERTIFIED**；`闸门退出码` 0 命中；登记对账 ✓（225）；站点 7 PASS/0 FAIL（GATES 224→**225**） |
| `PR#18i` | 19:4x | test | **继续还债：`视频观看` 六刀、`视频进度` 五刀** —— 这两道是「用户能看见的那一层」，偏偏都没有刀。`视频观看` 守一条完整链路（URL 白名单 → 抽帧 → 送 VLM → 拼上下文），任何一环断掉，现象都是「点开只会看个空壳」而测试全绿：V1 抽帧闸短路 / V2 帧描述不送进上下文 / V3 上下文丢掉帧条目（说了看了 N 帧却不列出来）/ V4 视觉能力恒判不可用 / V5 视频被判成图文 / V6 pixiv 掉出白名单。`视频进度` 守的是进度条：`watch_video` 发阶段 → `_make_video_progress` 转事件 → 总线 `publish_threadsafe`，断在哪一环都是「进度条不动 / 一直转圈」，而回调异常被 `except` 吞掉、日志看不出所以然：P1 诊断 frames 恒 0 / P2 不发 frame 阶段 / P3 诊断不暴露 ffmpeg / P4 失败抹空错误信息 / P5 失败不发 error 阶段。两把刀共 11 个变异，**一次全红**（锚点全部先在源码里验过命中恰好 1 次）。另记：这两道判据的输出是 unittest 的 `FAILED (`，不是本仓惯用的 `[FAIL]` 行，所以 `_judged()` 换判据 —— 但**语义不变**：崩溃（ImportError/Traceback）同样 exit 1 却不打 `FAILED (`，因此仍能把「判出来了」和「崩了」分开。 | 视频观看 21/21、六刀全红且各自点名；视频进度 7/7、五刀全红；两把刀的空变异体检均 **CERTIFIED**；`闸门退出码` 0 命中；登记对账 ✓（224）；站点 7 PASS/0 FAIL（GATES 222→**224**） |
| `PR#18h` | 19:1x | test | **覆盖率盘点：151 道判据门里 88 道没有变异刀；先给刚动过判据的那道补，补的过程撞出一个真缺陷**：① 逐门扫 `tools/_qc_*.py` 正文，统计「判据门 / 有刀 / 没刀」= **151 / 63 / 88（42%）**，另有 6 道非 lookalike 闸门（字节码编译 / 前端死引用 / 壳自愈端到端 / 导入冒烟 / 陌生人首启判据链 / A5 环境探针）。② 先补 `投影层自检`——它 2026-10-21 转绿后跑了两年多，**从来没有刀**，而第三十轮刚改过它的入口（缺 config.yaml 退到出厂模板），动了判据就得证明牙还在。③ 补刀时头一版是去改 `CALL_SITES`（删一项 / 让两个 id 撞车），跑出来是「锚点命中 0 次」。修锚点才发现更要命的事：**改 `CALL_SITES` 根本不会让这道门变红**——它只是 `__all__` 导出的一份声明，`build_model_view` 建视图走的是 13 段手写 `sites.append`，压根不读它。于是那道「调用点数应为 13」守的从来不是 `CALL_SITES`：删掉一项、或改成两个 id 撞车，视图照样 13 条、门照样全绿，**声明与实现可以任意漂移而没有任何人发现**。这正是 `catalog.py` 自己写的「不复制逻辑（第二份真相是 bug 温床）」要防的事，却唯独漏了它自己。④ 分两步补：先给闸门加一条 `CALL_SITES` 与视图的**逐项对账**判据（含重复计数），再让 M5 刀对准这条新判据——**有判据才有刀，不是有刀就有判据**。五刀各打一条判据：只读性 / 13 点齐全 / id 重复 / summary 自洽 / 声明对账。⑤ 刀的锚点腐烂只是噪音，**刀指向一条不存在的判据**才是真问题；这条写进注释留给以后。⑥ 新刀首跑被判 SHAPE（五元组分不清 old/new），补 `NOOP_SHAPE=("idx",2,3")` 后空变异体检 CERTIFIED。 | 投影层自检 16/0 → **17/0**（新增对账一行）；五刀全红且各自点名，还原后仍绿、sha256 逐字节一致；空变异体检 **CERTIFIED**（起点绿 / 空变异 exit 1 报 5 处异常）；空写体检 COVERED_ELSEWHERE（归空变异管）；`闸门退出码` 221 脚本 0 命中；`工具脚本导入路径` 33 PASS/0 FAIL；登记对账 ✓（222）；站点 7 PASS/0 FAIL（GATES 221→**222**） |
| `PR#18g` | 18:0x | fix | **最后 12 个红点：4 道门在新克隆上必红，2 道门的红是「测试自己没隔离 ffmpeg」**：① `config.yaml` 在 `.gitignore` 里（实配含 api_key），任何新克隆都没有 → 投影层自检 / ASR 事件流 / 桌面视频 e2e / 抓屏性能闸 D8 / 视频进度 五处直接 `FileNotFoundError`，**一条判据都没跑到**。四道退回出厂模板 `config.release.yaml` 当夹具（它才是「用户拿到的默认值」，验默认值比验实配更有意义），两份都没有才 77。② **视频两道不是缺环境，是测试有缺陷**：`test_video_watch.test_success` 已经 mock 了 `download_media` 和 `sample_frames`，却没 mock 前置能力探测 `_resolve_ffmpeg()` —— 而 `watch_video` 的闸是 `if video_files and ffmpeg_exe:`，于是**没装 ffmpeg 的机器上 frame 阶段根本不触发、那个 `sample_frames` mock 一次都没被调用**，结论取决于跑它的机器而不是代码。补 mock 后 21/21、7/7。③ `test_phash` 缺 `imagehash`（**故意**不在 requirements，它是被自实现取代的对照源）原来 `return 1`，在汇总里与真回归长得一样 → 改 77 并给出安装命令。④ 顺带把 `_pick_python` 的**静默降级**说出来：内置运行时不存在时 32 项声明 `PY_RUNTIME` 的闸门会静默改由 `sys.executable` 跑，汇总一个字不提。 | 全量 **14 红 → 0 红**（211 项判完 / 10 项跳过）；投影层 16/0、ASR 事件流全过、桌面视频 e2e 7 OK（原 7 errors）、抓屏性能闸 **27/0**（原 26/1）、视频观看 **21/21**、视频进度 **7/7**；感知哈希 → `[SKIP]` 77；静态闸门全绿 |
| `PR#18f` | 17:3x | fix | **「问不到」被当成「答否」：三道闸门在非 git 工作树里恒红**：交付包工作副本没有 `.git`，`git ls-files` 返回 128 + `fatal: not a git repository`。三道闸门把它当成「文件没入库」：`test_bundled_assets` 于是对 **10 件产品素材**逐个报「在磁盘上但没进 git —— 包里会缺」（**这是它没验证过的断言**）；`verify_first_run` 在第一步「造干净副本」就 `RuntimeError` 判红，**后面 7 步一条没跑**；`test_pack_for_teammate` 的 `[3]` 直接抛「取不到 git 文件清单」。三把变异刀跟着连环自爆（「起点不绿」「基线就不绿」）。修法分两种，都不许报绿：① `test_bundled_assets` 把「入库」拆成 `yes/no/unknown` 三态 —— 存在/非空壳/真格式照常逐件判，只有「入库」这一项报未验证，`exit 77`；② `verify_first_run` 加 `NoGitWorkTree` 异常 → `[SKIP]` + 77（整条链一步没跑，不能说验过）；③ `test_pack_for_teammate` 只把依赖 git 的 `[3]/[3b]` 跳过，其余四节照常判，末尾 77。刀的处理也分两种：`_qc_bundled_assets_mutation` 的三刀（删/截断/假格式）**不需要 git**，让基线认 (0,77) 后照常跑，仍三刀全红；`_qc_pack_exclude_mutation` 的变异目标就在 `[3]` 里，前提没了就整把报 77，不硬凑。 | 必需资产 0 PASS/**10 FAIL** → `0 PASS / 0 FAIL / 10 未验证` exit 77；首启链 RuntimeError → `[SKIP]` exit 77；打包排除 `取不到清单` → 四节照判 + `[SKIP]` exit 77；三把刀 `起点不绿/traceback` → 77 与三刀全红；静态闸门（编码/全局名/同名/不可达/常量条件/静默异常/导入路径）全绿 |
| `PR#18e` | 17:0x | fix | **闸门在推荐解释器下起不来：全量跑一行没执行就退出**：`check_all.py` 尾巴的 `from _gate_lock import` 在 `.venv/Scripts/python.exe` 下必崩（`ModuleNotFoundError`），在普通 `python` 下正常 —— 交付包自带的是**可嵌入运行时**，`python312._pth` 只把 `python/` 下几个目录加一个 `..`（仓根）塞进 sys.path，**不含 `tools/`**；普通 CPython 会补「脚本目录」，可嵌入版不补。而 `.venv` 才是要三方依赖时的推荐解释器 → **这道门在官方姿势下从未跑起来过**。同一族 PR #17 已修过一次（`check_web_js_syntax`），所以这次不只修还补判据：新增 `check_tool_sys_path.py`（纯 stdlib 静态判定，181 脚本 0.2s）——兄弟 import 前必须有由 `__file__` 推导的 sys.path 改写（模块层或更早行号），写死盘符路径判红，接 `ImportError` 的降级导入与免登脚本不计分但必须打印。**它第一次跑就抓到 `_qc_api_wiring_mutation`**（函数里 `from check_api_wiring import`），补完后全量跑里那条 `反向接线变异` 整条挂掉的记录随之消失。顺带把上一轮写死的 600s 闸门超时按档改成 1800/600 并把超时记成有名字的失败。**并记录未修的发现**：交付包工作副本（无 `.git`／无 `config.yaml`）里全量 14/215 红，其中 12 项是「问不到被当成答否」，按约定该报 77 却报了 1。 | `check_all.py` 在 `.venv` 下能拿到锁并跑完（215 项判完 / 4 跳过 / 14 红，红的构成已分类）；`变异刀空写体检` 428.9s < 1800s 预算，不再被 kill；新闸门 **33 PASS / 0 FAIL**；新刀 **11 项全过**；`_qc_api_wiring_mutation` 4 刀恢复全红 + 起点绿；登记对账 ✓（220）；站点 7 PASS/0 FAIL（GATES 219→**221**） |
| `PR#18d` | 17:4x | test | **第三种空转体检：封住写入口** —— 「SKIP 50 不算通过」这句话压了三轮，这一轮把它拆开：那 50 里 18 个是**一次性探针**（压根不是刀）、28 把是「没有模块级 MUTS 也没有独立判定文件」的真刀、4 把有 MUTS 但形状退化不了。新工具 `check_knife_nowrite.py` 不碰变异，把 `builtins.open`/`io.open` 的**写模式**重定向到临时文件，让「变异根本没发生」：此时刀还 exit 0 就是空转。这个方向与变异长什么样无关 —— 改源码的、往 `src/` 扔坏文件的、复制配置的、删目录的，一视同仁。**三方向闭环**：换判定（判定坏了刀会不会红）/ 空变异（源码没变时会不会假绿）/ 封写（**什么都没变**时会不会假绿）。顺带：`check_knife_vacuity.py` 的仓副本改成「重新 init 一个 git 仓」（`tools/_qc_repo_copy.py`），最后一条 `EXCUSED` 解开、表清空；`check_knife_noop.py` 的刀口径改成 `_qc_*_mutation.py`；**`check_delivery.py:check_template` 还在断言 `force_first_run is True`，而 10-20 的修复故意改成 false —— 它对任何正确打的包都判 FAIL，`_qc_delivery_mutation` 466 行 14 个用例因此永久红、等于从来没在跑**。 | 空写体检 **CERTIFIED 21 / COVERED_ELSEWHERE 33 / META 3 / SKIP 1 / ERROR 4（62 把），VACUOUS 0**，exit 0（14m）；空转变检 **CERTIFIED 33 / SKIP 29 / SHAPE 0**（口径修正后 62 把，原「29 / 50」是 79 个文件混算）；空转体检 **28 CERTIFIED / 1 SKIP**（29 对，上轮 27/1）；**三合一覆盖 54/62（装上 jsdom 后 55/62）**；三道体检的变异刀 16 项 / 13 刀 / 9 刀全过；出厂模板判据修正后 `check_delivery` 该行 PASS；GATES 217→219，站点 7 PASS/0 FAIL |
| `PR#18c` | 17:1x | fix | **「缺环境」被报成「判据失败」：四个红点，四个都不是产品坏了**：① `A5环境探针` 扫 `logs/launch_run*.log`，而 `logs/` 在 `.gitignore` 里 → **新克隆的树上恒红**；改为三态（有样本逐条判 / 有日志扫出 0 份=FAIL / 没日志=WARN+exit 77），并加 `--root` 让判据能被夹具驱动。② `_qc_a5_probe_mutation` 起点恒红 → 六把刀"全红"白送；改用 tempfile 合成日志当夹具 + `_judged()` + 三把限位（77 / FAIL / 0）。③ `_qc_tray_switch_mutation` 要 `config.yaml`（也在 gitignore 里）→ 缺文件时回落到出厂模板 `config.release.yaml`。④ `hotkeys._validate` 在**没装 pynput** 时只做形状校验，`<ctrl>+<alt>+<nosuchkey>` 静默进映射表（**用户以为绑上了，按下去什么都不发生**）；新增 `_fallback_ok` + 键名白名单。**并更正上一轮的错误归因**（原写成"无桌面会话返回空表"，与桌面会话无关）。⑤ 17 把刀的 `_baseline_green()` 比判定还严（禁 `Traceback`），把"判定故意打的异常日志"判成起点红 → 改为"退出码 0 且无 FAIL 行"。⑥ `KNOWN_VACUOUS` 清空 + 额度对账（N8 指向空气 / N9 表在说谎 / N10 限位）。 | 空转变检 **CERTIFIED 29 / SKIP 50，VACUOUS 0 / ERROR 0**（上轮 26 / 1 / 50 / 2）；空转体检 27 / SKIP 1（上轮 25 / 1）；A5 探针 7/0/2 未验证 exit 77、夹具下 9/0 exit 0；A5 刀 6 刀 + 3 限位全过；在场感 33/1 → **41/0**；托盘 29/0；四腿 CI 全绿 |
| `PR#18b` | 16:4x | test | **空变异体检：另外 48 把刀的空转风险一直是零覆盖**：上一道工具靠「把判定文件换成退化实现」，前提是刀有独立判定文件——79 把里只有 26 对有，另外 48 把在进程内断言，换文件完全无效。本轮换方向：**退化变异本身**（`(old,new)` → `(old,old)`，即"什么都没改"），判定有牙就该绿 → 刀必须报「没抓住」→ `exit != 0`；若仍 `exit 0` 就是没在看变异。这个方向不需要知道判定在哪，对 79 把一视同仁。普查中修掉自己仪器的两处假发现（`_noop()` 盲取最后两个元素，把「说明」当成 `new_thunk`，5 把刀被误判 VACUOUS；runner 的 `sys.path[0]` 不是 `tools/`，让 `_qc_api_wiring_mutation` 白记 ERROR）。**顺带查出真缺陷**：`导入冒烟复核` 在 `PY_VENV` 下一直是 6 FAIL——`smoke_import` 的 `env["PYTHONPATH"]=root` 在随包嵌入式运行时下是死代码（`._pth` 固定 sys.path），且夹具的 `src/` 少了 `__init__.py` 成了命名空间包、被 `DaniaAI\src` 这个常规包压住。 | `check_knife_noop.py` CERTIFIED 26 / VACUOUS 1（已知）/ SKIP 50 / ERROR 2，exit 0（5m56s）；七刀 + 对照全 PASS；`导入冒烟复核` venv 6 PASS/6 FAIL → **12 PASS/0 FAIL**，刀在 venv 下也 3 刀全红 + 起点绿；`GATES` 215→217，站点 4 处锚点同步（7 PASS/0 FAIL） |
| `PR#18` | 16:0x | fix | **18 把变异刀是空转的：判定崩了也被算成「抓住了」**：`caught = r.returncode != 0` 把「判出了问题」和「根本没跑起来」当同一件事——把判定整个换成 `raise RuntimeError(...)`，套件照样印「已抓住：<无输出>」「N 刀全红」并 exit 0。18 把刀补 `_judged()`（非零退出 **且** 有 FAIL/✗）+ `_baseline_green()`（起点不绿直接 FAIL）。工具补 `_resolve()`（判定不一定在 `tools/` 下）、ABSENT/ERROR 拆分、完整性检查、`--tier` 分层。顺带查出 `_qc_pathrefs_mutation` H 刀锚点缩进差 4 格、`count(old)==1` 拦不住子串匹配。 | `--tier portable` 8 / `dev` 17+SKIP1 / `all` 25+SKIP1；13 刀 + 对照全 PASS；修前旧版被体检判 VACUOUS、注入崩溃后仍 exit 0，修后 exit 1 并点名「起点就不是绿的」 |
| `PR#17` | 15:2x | fix | **`check_web_js_syntax` 在交付包运行时下从未跑过 + 新增「变异刀空转体检」**：该闸门只把仓根塞进 `sys.path`，而 `check_bridge_methods` 在 `tools/` 下；正常 CPython 会补脚本目录，交付包自带的可嵌入运行时（`python312._pth`）不会 → `ModuleNotFoundError` 一启动就崩，87 段 JS/HTML 从未被检查。补齐 `tools/` 路径。另新增把闸门换成退化实现的体检工具（D0 原样 / D1 恒 0 / D2 恒 1），7 对全 CERTIFIED。 | 修后 87 段 / 0 错 / exit 0（修前崩溃）；7 对全 CERTIFIED；十刀 + 对照全 PASS |
| `PR#16` | 14:5x | fix | **通知线第三处跨模块死引用（`_push_cmd`）+ 新增「跨模块引用对账」闸门**：`notify_geom._notify_slide()` 调的 `_mn._push_cmd` 全仓无定义——WebView2→WPF 迁移后旧 API 调用点没删（前两处是 `NotifyJsBridge` / `_notify_slide_if`）。新增 AST 闸门：`from M import y` 与 `mod.attr` 必须指向真实存在的本地符号（外部包 / star 污染 / rebound 别名 / `hasattr` 声明过的一律不判）。顺带修 `real_software_drive.py` 里一条一直往裸 `except` 里抛 `ImportError` 的探针。 | 跨模块引用 0 命中（全仓 700+ 文件）；十二刀 + 对照全 PASS；`--root` 建 tempfile 样本，不碰仓库 |
| `PR#14` | 14:3x | ci | **补上 #9/#12/#13 推迟的三道闸门接线**（unreachable / constant-cond / gate-exit）：三个 PR 都要往 `portable-gates` 末尾追加、同时追加必然互相冲突，所以各自在 body 里留了待办。只登记进 `GATES` 而 CI 不跑 = 「本机才守」，接进 CI 才挡得住别人合进来。 | `portable-gates` 6 族 / 12 步；三道闸门本机实测全绿、纯 stdlib、总耗时 17s |
| `30dddf9` | 10:2x | fix | **设置页切麦热切换 守卫写错名（写了没接线）**：`webapp.setCallAudioDevice` 用 `hasattr(self._proactive, "set_duplex_mic")` 守卫，但 `ProactiveStrategy` 上**没有** `set_duplex_mic`（全仓零定义），守卫恒 False → 注释承诺的「让运行中的双工立即用新设备（不必重开整通电话）」**从未生效**（用户切麦后当前通话仍用旧设备）。真名是 `apply_call_settings(mic_device=...)`：更新 `_duplex_mic_device` 并对当前双工客户端调 `set_mic_device()` 热切换（webapp 另三处扬声器/通用设置早就在用这个真名，唯独切麦这条写错）。修为真名直调 + `logger.debug` 兜底；守卫去掉（真名 + try/except 足够，与本仓 17243 那条「幽灵调用」墓碑同一修法）。 | 新增回归 `src/tests/test_duplex_mic_hotswitch_wiring.py` 7/7（真方法写字段+调 client.set_mic_device / None 不动客户端 / 无客户端不炸 / tokenize 剥注释后代码含精确调用串 / 旧名已不在代码）；**变异刀**：改回 `set_duplex_mic` → 两条源码判据全红、还原字节一致；门禁对账 `✓ 全部有着落` |
| `XXXXXXXX` | 03:1x | test | **补齐「感知指示器」变异刀（隐私门此前无变异=装饰品）+ TODO 审计口径纠正**：`test_perception_indicator.py` 早已登记为门，但其 docstring 提到的「变异刀3」从未提交——按「加门必须同时加变异」补 `_qc_perception_indicator_mutation.py` 四刀（watching 写死 true / privacy 恒 false / 静态 class=on / toggle 改无条件 add），全红 + 还原全绿，登记为慢项。另：此前「21 处 TODO」是口径错误，剥掉 `XXX` 占位符后**真 TODO 仅 1 处**（qzone.py 图片发布「需联调」，qzone 默认关、dormant），无隐藏债。 | 感知门 EXIT=0；变异四刀全红 + 还原全绿；登记对账「全部有着落」；py_compile 通过 |
| `XXXXXXXX` | 17:5x | test | **主动搭话「去重 + 冷却时间戳守卫」单测（26 项）+ 变异刀**：proactive.py 的「不刷屏骚扰」靠 `_screen_activity_repeat`/`_similar_summary` 输入轨迹去重、`_similar_to_recent` 输出去重、`_last_proactive_or_zero` 冷却时间戳守卫撑着，此前零断言只靠人工。新增 `src/tests/test_proactive_dedup_cooldown.py` 26 项；变异刀 `tools/_qc_proactive_dedup_mutation.py` 三刀（子串判定恒假 / 轨迹去重恒 None / None→None 重演旧 bug）全红、还原 26 全绿；已登记进 check_all（测试门走 OpenBLAS 串行复核包装器，变异刀标慢项）。 | 测试 26/26；变异三刀全红 + 还原全绿；登记对账「全部有着落」；py_compile 通过 |
| `XXXXXXXX` | 20:4x | fix | **关系 v2 家族「实验/无生产 import」矛盾声明修正（定轨）**：`relationship_v2.py` 与 `relationship_judge_v2.py` 顶部均声明「实验模块、无任何生产代码 import」，但实证 `relationship.py:918` 实例化 `RelationshipEngineV2`、`relationship.py:997` 引 `relation_block_text_v2`、`relationship_v2.py:156` 引 `heuristic_signals_v2`——v2 引擎与裁判早已在生产接线，旧声明是「注释承诺没做」类自相矛盾（误导后续维护以为可删，实则删了直接打爆引擎）。修正为「已定轨」并写明真实接线点。`relationship_judge_prompts.py`（0 引用）声明属实，不动。 | 关系引擎测试 13/13（wiring）+ 13/13（integration 并发无丢字段）+ 41/41（main）+ 裁判 15/15 全过；`smoke_import --strict` 174 模块 0 非预期失败；py_compile 两文件通过 |
| `XXXXXXXX` | 03:4x | fix | **双工/设置 UI 措辞对齐（旧名「功能与性能」→「模型与 API」）**：设计文档 ⑬ 指出设置分类早已改名为「模型与 API」（mode_gates.py 已改、HTML 一致），webapp.py 仍残留 6 处指向旧名的提示/注释文案（如"去「设置 · 功能与性能」勾选"），用户按提示找不到入口。统一改为「模型与 API」，与现 UI 一致。 | webapp.py py_compile 通过；全仓 webapp.py 内"功能与性能"残留 0；HTML 现行命名已统一「模型与 API」（ui_redesign_settings.html:3574 注释旧名） |
| `e845dd4` | 03:3x | fix | **doctor 门禁抖动根因修复（同根 OpenBLAS）**：doctor 的 `check_deps` 用 `importlib` 逐个 import 核心依赖，`requirements.txt` 核心段含 `numpy`；全量门禁并发跑时 numpy 的 OpenBLAS 后端在内存争用下打印 `Memory allocation still failed` 并令 import 抛异常 → doctor 误报「缺 numpy」→ `test_doctor.py` [3]（断言无 FAIL）间歇红，单跑无内存压力则稳过。把 doctor 门禁也纳入 `run_test_with_mem_retry.py` 串行复核包装器（与 test_phash / test_set_player_name_wiring 同根同解）：只在那一文案上串行复核一次，真缺依赖（别的报错）绝不重试，真缺陷仍抓得到。 | doctor 门禁经包装器单跑 EXIT=0；登记对账 `✓ 全部有着落`（149 项、无重复）；根因证据：`requirements.txt:10` 核心含 numpy + `doctor.py:check_deps` import 异常即报 FAIL + OpenBLAS 文案一致 |
| `00a7bc3` | 02:4x | fix | **听说·打字时本地双工暂停/恢复 接线（写了没接线）**：`_note_user_typing` 只记 `_duplex_user_typing_ts`（供云端双工 VAD 抬阈），**从没调过 `pause()`、从没置 `_duplex_paused_for_typing`**；而 `local_omni_duplex.pause()`（"打字聊天时调用，让双工给打字回复让路"）/ `resume()`（"打字回复结束后由编排层看门狗恢复"）+ proactive 刷新循环里的恢复分支（4112）全靠这个永不为真的标志位活着 → 整条「打字时暂停本地双工麦流上行、8s 后恢复」死掉（"她听了键盘声却没让路"）。修复：在 `_note_user_typing` 补触发点（只对带 `pause` 的本地双工生效、幂等、重复按键只 pause 一次、无 pause 方法/无双工客户端则跳过），并把恢复分支抽成 `_maybe_resume_typing_paused_duplex()`（行为不变、可测）。**不造功能**：云端双工本就走 VAD 抬阈那条，未动；这是把已存在但漏接的触发点接上。 | 新增回归 `src/tests/test_typing_duplex_pause.py` 12/12（打字触发 pause+置标志+刷新计时戳 / 幂等 / 无 pause 方法不误暂停 / 无双工不误暂停 / <8s 不恢复 / ≥8s resume 一次并清标志）；经 `run_test_with_mem_retry` 包装器登记进 check_all（proactive import numpy，免疫 OpenBLAS 抖动）；门禁对账 `✓ 全部有着落` |
| `2d2bd51` | 05:2x | chore | **真空吞全仓清零（产品代码 + 闸门脚本 0 真静默）+ 修复 check_requirements 闸门沉默**：① 续清非主链真空吞——听说双工三族 19 处（10-24 下午已交）、pet_shell/call_trace/model_catalog 20 处、app_index/software_profiles/minicpm_client/gpu_load/ref_audio_util 20 处、theme_ai/openai_client/media_ingest/engine_log_guard 等 18 处、其余长尾 51 处（含 config/autostart/api_window/chat_overlay/pet_state/decision_parser 等 30 文件）、main.py 11 处、闸门类脚本自身 18 处、doctor/pack/make_dist 打包链路 14 处。**src/ + main.py + 全部闸门脚本：真静默 0 处**（858 处静默全部有注释或日志）。② 抓到并修掉一个**真缺陷**：`check_requirements` 遍历文件时遇 SyntaxError 直接 `continue`——坏文件的 import 全都扫不到，漏声明静默发生而退出码仍 0（正是「闸门沉默」类 hazard，本仓纪律：解析失败必须记 error 并判红）。修为收集 broken 列表、显式报出、`--strict` 下判红；新增变异刀 `_qc_requirements_syntax_mutation.py`（放坏文件→红且点名「解析失败」；无坏文件→绿；还原→绿）。③ 过程事故与恢复：并行编辑同一文件（app_index.py 5 条编辑挤一条消息）互相踩成缩进错乱 → `git checkout` 还原后改**单脚本带断言替换**；后续所有同文件多处编辑一律走「行号插入器」（从闸刀取行号、从文件取缩进、倒序插入）。 | 全量门禁 **148/148**（12m00s，含新登记「依赖声明变异」）；pack 排除变异四刀全过；doctor 单跑 18 PASS/1 WARN/0 FAIL；改动 63 文件 +256/−7 |
| `09dcbc7` | 04:3x | chore | **听/说/双工三族 19 处真空吞清零 + 设置器写入键对账干净**：① `mic_proc`（7）/ `tts_backends`（5）/ `local_omni_duplex`（7）的 except-pass 全部补上理由——逐处读过，全是良性：日志开关失败落 stderr 兜底、ws.close 清理（上层已 warning 并回退新握手）、设备/窗口探测跳过、语速微调失败按默认语速合成（不出声才是事故）、四个旋钮设置器的 `except (TypeError, ValueError): return` 是**入参校验**（非法值安静返回，变更本身有 logger.info）。三文件现 0 真静默。② **设置器写入键对账（新审计，结论干净）**：模板对账闸门只覆盖静态侧（模板里的键有没有人读）；本次补查动态侧——从 webapp.py 提取设置器写进 config 的 79 个叶子键（section dict 人工映射），逐个核对后端读取点：**79/79 都有读取，0 个死键**。「用户开了开关什么都没发生」这个类在写入侧不存在。因 section 映射会随代码腐烂，不建闸门，按纪律记录为带日期与方法论的审计发现（复核日期 2026-10-24；删除条件：设置器保存路径重构后重跑）。 | 三文件编译 + 复扫 0 真静默；全量门禁 **147/147**（12m47s） |
| `8fab483` | 03:2x | fix | **webapp.py 43 处真空吞清零 + 纳入主链静默闸门；OpenBLAS 抖动有了串行复核包装器**：① `check_silent_except` 主链再扩——UI↔后端的桥 webapp.py（143 处 except-pass 里 43 处块内无注释无日志）全部处理：探针/枚举/清理类补理由注释，承重的试听/克隆/保存路径本就有 `_last_tts_error`/`logger.exception` 上报（补的是清理点注释）；主链现五文件 300 处静默全有着落、0 真静默。② 全量门禁连跑时 `test_phash`/`test_set_player_name_wiring` 间歇撞 OpenBLAS「Memory allocation still failed」（单独跑就过、产品无问题）——新增 `run_test_with_mem_retry.py` 包装器：**只认这一种文案、只串行复核一次、真缺陷绝不重试**（自测三用例守着），两个抖动测试改走包装器。③ 登记对账闸门修两处误判：包装器式登记（argv[1] 是真测试）曾被误判未登记 → 登记身份改取 argv 里全部 .py/.js；共用包装器的两个条目曾被误判重复 → 去重改比对整条 argv。④ `test_doctor` 的「不应有 FAIL」断言补上 FAIL 行明细（跑闸负载下间歇红过两次而旧断言不说是哪项）。 | 主链闸门 0 真静默；变异刀五刀全过（新增 E 刀：webapp 注入）；包装器自测 7/7；额度墓碑刀因 webapp 转 LF 锚点失效→改逐文件实测 EOL 后三刀全过；全量门禁 **147/147**（12m48s） |
| `404a849` | 02:1x | fix | **proactive.py 43 处真空吞清零 + 纳入主链静默闸门**：`check_silent_except` 的主链原先只有 cloud_omni_realtime/dialogue/tts 三个文件，而**主动搭话/双工控制/看屏互动/档位同步全在 proactive.py**——普查发现它有 **43 处 `except: pass` 且块内无注释无日志**，其中承重路径包括：双工对话写长期记忆失败（通话后她不记得说过什么）、打字侧接语音上下文的历史桥失败、主动搭话筒术 LLM 失败、看屏批量循环启动失败、taskkill 杀不掉引擎进程、配置档位同步失败。处理口径：**承重路径 `logger.debug(..., exc_info=True)`**（行为不变、失败可诊断），**有意降级补理由注释**（探针失败/任务取消/兼容垫/清理路径）。随后把 proactive.py 纳入 MAIN_CHAIN（现四文件 158 处静默全有注释/日志、0 真静默）。全仓其余 159 处真静默在非主链文件，记录待逐文件推进。 | 闸门 `--main --strict` 0 真静默；变异刀**加第四刀**（往 proactive.py 注入真空吞 → 红并点名，证明新覆盖有牙齿）；四刀全过 + 双还原字节一致；全量门禁 **146/146**（12m29s） |
| `873ee58` | 01:0x | chore | **出厂模板 24 个死键删掉 + 模板对账闸门**：普查发现 `config.release.yaml` 424 个叶子键里 **24 个没有任何代码读取点**——用户/队友照着模板配了，什么都没发生（`voice`/`voice_id` 同型的放大版）。三个最恶劣的：`proactive.duplex_mic_vad` 有一处读（`getattr(self,'_duplex_mic_vad',True)`）但**全仓没有赋值点**，永远落默认值；`max_per_type_per_day` 被 docstring 承诺为"同类活动每日上限"而真实机制是 `type_cooldown_s`；`sticker.random_probs.*` 整子树死（sticker_bridge 用裸 `random.choice`，参数全硬编码）。删除：`game_stagger`（08-25 已被节奏门禁取代的残留）/ `accumulate_screen` / `duplex_mic_vad` / `duplex_mode_lock_mem_pct`+`util_pct`（兄弟键 ext_mb/silent 都活着）/ `duplex_ocr_frame_overlay` / `duplex_timing_pressure` / `game_immersive` / `heavy_apps_cloud_*`×3 / `immersive_interval_s` / `llm_timeout_s` / `max_per_day` / `max_per_type_per_day` / `react_to_screen` / `sticker.timing_mode`+`random_seed`+`random_cooldown`+`random_probs.*`+`warmth_*`。新闸门 `check_config_keys.py`：模板每个键必须出现在代码 NAME/STRING token 里（**tokenize 丢注释**——注释里提一句不算读取点，那正是 docstring 谎言形态）；例外清单带原因（`_release_note` 元键 / `ollama.options.*` 泛型透传）。 | 两刀变异全红（插死键 / 抽豁免条目——证明清单不是装饰品）；全量门禁 **146/146**（12m25s）；`test_release_template` 无损；模板工作副本 CRLF→LF 还原 |
| `8e68ed1` | 00:0x | fix | **双工过渡看门狗从来没启动过——重连卡死时她会永久静默**：`_duplex_in_transition` 禁言闸门会把 `_on_duplex_speak` 的开口全丢弃（2001/3336），正常出口只有 connected/prepared（2989/3047）；重连卡在"正在重连"类状态、prepared 永不到达时，闸门**永不起下课** → 她永久静默且零报错。看门狗方法 `_duplex_force_clear_transition`（20s 强制解除禁言）09-08 就写好了，但**全仓零启动点**——2912 的注释却一直承诺"配合看门狗兜底"。修法：闸门拉起处记 `_duplex_transition_since` 时间戳；方法补 20s elapsed 守卫（没到窗口不动、没记时间戳不动）；挂 5s 双工诊断 tick（与哑火看门狗同 tick）。顺带删 `cloud_omni_realtime._reopen_stream` 死包装（锁不可重入，真调用方都在持锁状态直接调 `_reopen_stream_locked`，包装零调用）+ 修三处提到它的注释。 | 回归 **14 项**（两对照组：闸门没起 / 20s 窗口内都不许误清）；**四刀变异全红**（阈值失效→卡死也不清 / 抽 raise 时间戳 / 抽 tick 调用 / 死包装复活）；全量门禁 **144/144**（12m39s） |
| `05fee72` | 23:5x | test | **闸门登记对账（「写了没进闸门」第五次复发）**：`tools/` 下 8 个 `check_*.py`/`test_*.py` 从未登记进 check_all——4 个硬编码开发机 G:/H: 盘路径（换机即废）、1 个要真实百炼 Key + 声卡外放的真机探针、3 个人工诊断仪器；名字长得像闸门，新人跑了报红还以为产品坏了，而它们覆盖的行为**没有任何自动判据在守**。普查口径：tools/ 824 处盘符路径 / 264 文件全部 git 跟踪（进队友包），按「队友会不会跑」切刀——闸门类 39 个文件的命中全是 URL / 注释墓碑 / 变异锚点 / 标准系统位（Edge/WebView2/SystemRoot）/ 测试夹具，**功能代码零 dev 路径依赖**，队友面（doctor/check_all/pack）无出厂缺陷（`src/` 75 处上一轮已定性）。修法：新闸门三条判据——正向（lookalike 要么在 GATES 里、要么 docstring 带「不进闸门：原因」且原因不许空）、反向（登记的脚本都真实存在，防改名后登记项变哑、汇总还显示通过）、去重（同脚本不许登记两次）；8 个文件补免登标记。**顺带把并发会话锁定期缓办的 `test_set_player_name_wiring.py` 补登记**——本闸门第一次跑就把它判了待定，正在尽职。 | 四刀变异全红（未登记新文件 / 撕免登标记 / 登记项指向不存在 / 重复登记）+ 双对照（待定 0 从输出解析、不写死数字）+ 还原后字节一致；全量门禁 **142/142**（12m9s）；新登记测试 venv 单跑 11/11 |
| `539406e` | 22:45 | fix | **看链·SET_PLAYER_NAME 死订阅清理（写了没接线）**：`GameOcrMonitor` 订阅了 `SET_PLAYER_NAME` 事件（`_on_set_player_name`）并声称「proactive 从用户回复提取名字后发布」，但全仓 grep **零发布者**——proactive.py 零 `player_name` 引用、dialogue.py 无名字提取逻辑；WebUI「我的名字」直调 `set_player_name`（webapp.ocr_set_player_name → GameOcrMonitor.set_player_name）才是唯一真实生效路径。死监听器 + 误导性 docstring/注释已删，改名只走直调路径。 | 新增回归 `src/tests/test_set_player_name_wiring.py` 11/11（WebUI 直调写内存+config / 死方法已删 / 构造无 SET_PLAYER_NAME 订阅）；全量门禁 141/141 |
| `61a3eb7` | 22:2x | fix | **说链·TTS worker 异常隔离（静默失效）**：`tts.start_worker` 以 `asyncio.create_task(self._worker())` 启动播放 worker，既没存任务句柄、也没加 done_callback，且 `_worker` 循环体无顶层 try/except → 任一条任务里的未捕获异常（位于 try 之外的状态机/事件总线调用）杀死 worker 协程 → 队列停止排空 → 之后所有 TTS 静默失效（"她说了却后面没声"）。`__init__` 加 `self._worker_task=None`；`start_worker` 改 `ensure_future` + `add_done_callback(self._on_worker_done)`；`_worker` 循环体包 try/except（单条坏任务 `logger.exception` + `to_idle` + `continue`，不拖垮整链）；新增 `_on_worker_done` 在 worker 异常退出时 `ensure_future` 重启并重新挂回调。 | 全量门禁 141/141（含 [6] TTS worker 异常隔离 3 断言：首条异常后两条仍分发 / worker 仍存活 / done_callback 重启）；`tools/test_tts_multi_provider.py` 30/30 |
| `ec7c53b` | 21:3x | fix | **听链·双工采集卡死看门狗（静默失效）**：`cloud_omni_realtime._subproc_capture_loop` 阻塞在 `proc.stdout.read(4)`，无超时；注释承诺的「卡死看门狗线程」全文件 grep 证实**根本不存在** → 子进程活着却不再写（设备热插拔 / AGC 卡死 / 回调冻结）时 `read` 永久阻塞 → 采集冻结、既不重启也不报错（"她听了却卡死"的静默永久失聪）。抽 `_capture_stalled`/`_kill_capture_proc`/`_watchdog_tick`/`_capture_watchdog_loop` 四纯方法，每轮启动 daemon 看门狗线程周期检测（距最后一块音频 >2s）并 kill 子进程 → read EOF → 内层 break → `restarts<5` 自动重启；看门狗读 `self._last_blk_ts`（原为局部变量，跨线程恒读默认 0.0 → 会误杀健康流，已改实例属性并开局置 `_cap_t0`）。 | 全量门禁 141/141（含场景 D 8 断言 + 判据恒 False 反向对照）；测试 19/19 |
| `d5e1433` | 21:0x | fix | **听链·坏帧杀守护线程（静默失效）**：ASR 识别循环体无 try/except，单帧畸形音频（如奇数长度 PCM 让 sherpa.feed 的 np.frombuffer 抛 ValueError）冒泡杀死 daemon 线程、且无 supervisor 重启 → 永久失聪（"她听了却再也听不见"）。循环体外层包一层 try/except，坏帧只记日志+跳过+continue，下一帧正常音频仍识别、USER_TEXT 照常发出 | 全量门禁 141/141（含场景 C 畸形帧 + 变异刀 C）；测试三场景 + 四刀变异全过 |
| `ba5eebc` | — | fix | **双工通话内容不进桌面聊天页/活动日志**：duplex 来源 USER_TEXT 漏过 webapp `_on_chat_user_text` 与 activity_reporter `_on_user_text` 两道闸，既污染桌面聊天页、又误记 direction=user | 测试 13 场景/95 断言 + 15 刀变异；全量门禁 141/141 |
| `3bb6581` | — | fix | inject_context=false 是"只存不用"——两条记忆写路径却做成"不记" | —（未在本轮复核） |
| `af170e2` | — | fix | 一句纯聊天永久抹掉她"看懂游戏"的能力——_build_messages 别再改实例状态 | —（未在本轮复核） |
| `dac37f8` | — | fix | _on_user_text 那一跳也透传 channel/reply_target + 修两处桩签名漂移 | —（未在本轮复核） |
| `f38f857` | — | fix | 工具链透传 channel/reply_target——QQ 命中工具链时一头没接到、另一头接错 | —（未在本轮复核） |
| `94ab932` | — | fix | 三个能出声的入口统一判空——聊天发图/调工具此前完全没守 | —（未在本轮复核） |
| `a7314ed` | — | fix | 清洗后判空——模型只吐舞台动作/markdown 骨架时不再静默丢弃或念出来 | —（未在本轮复核） |
| `531b6b2` | — | fix | 自查上一处修复的三个缺陷——尾段守卫分层 + 桩不许说谎 + 去掉超时判据 | —（未在本轮复核） |
| `b34d7a7` | 21:2x | fix | **第七处静默失效·她听到却不回答**：桌面来源（voice/webui/console）的 USER_TEXT 在 `_on_user_text` 走 `asyncio.create_task` 后台任务，而 respond 尾段（剥 markdown / 拟人化 / 记历史 / 发 AI_DECISION，dialogue.py 1205~1336）在 respond 内部只管 LLM 的 try/except（1126~1159）**之外** → 尾段一崩，异常落到无人 await 的后台任务，静默吞掉 → 用户全程无感知。修法**两层**：①尾段自己加守卫，判据与原有 LLM 失败分支同构——**她有没有已经说出口的话**：流式已播出 → 补发非 failed 决策（她话已说出口、TTS 已念完，不能再补一句"失败"）；毫无产出 → 才 failed；②create_task 补 done_callback 作最后防线（覆盖 vision / 媒体等 respond 之外的支路）：取异常（消除 "Task exception was never retrieved" 警告）→ 记日志 → `publish_threadsafe` 补发 failed 气泡，不再对着静音桌宠 | 五场景真值表（正常 / **已答完再崩→不得有 failed** / 非流式再崩→不得有 failed / LLM 没生成→必须有 failed / 崩在 respond 之外→靠 done_callback），含对照组区分「判据正确」与「判据恒真」；**harness 让 FakeLLM 真流式**（第一版把 `_chat_decision` 桩成返回 `(RAW,True)`，一条 PARTIAL_TEXT 都没发，"她已答完"这个前提根本没造出来，于是把"已回答+红字失败"自相矛盾断言成了正确）；等待改循环轮次 + `queue.join()`，不再用 5s 超时（变异 7.9s→3.5s）；**七把刀全红**（摘 done_callback / 守卫恒 failed / 守卫丢非流式兜底）；闸门 +2（137→139）；工作区另有并发「闸门登记对账」+2，合并实测 **141/141** 全绿 |
| `6a07e12` | 07:5x | fix | **克隆面板「预填她的」从来是坏的**：预置角色「温柔女友」的参考音频随包分发（`_role_assets/ref/xiaowei.wav`），但面板**预填写一份、按钮读另一份**——卡片预填 `voice.ref || demo.ref`，试听/开始克隆只读 `c.voice.ref`；预置角色只有 demo.ref（`path` 还是开发机绝对路径 `D:/GPT-SoVITS/...`）⇒ 试听按钮每次静默 return，「开始克隆」取到空 refPath 后跳过真实克隆、落到「无桥降级」画一条**假进度条**再 toast「克隆完成」——用户机器上什么都没发生（该文件自己的红线写着"不画假进度"）。顺带查到 `/local_img` 是**图片**路由（Content-Type 只认图片、白名单只放行 `char_banners`/`char_avatars`+注册目录），用户自选音频的试听也一直静默 404。修法：`_clRef` 单一来源（预填/试听/克隆共用）、随包资产经新后端 `use_bundled_ref_audio` 物化到 `data/voice_ref/`（白名单防穿越）、选文件换 `pickReferenceAudio`（B3 持久化）、`/local_img` 补音频类型并注册目录、**假进度条整段删** | jsdom 真值表 **24 项**（原样加载 `onboarding_role.js` 整文件真点真走：S1 随包资产 / S2 用户文件 / S3 无桥 / S4 墓碑，两个对照组）；后端门禁 20 项（物化 + **8 条白名单拒绝** + 目录注册 + 音频类型）；**六刀变异全红**、还原后仍绿；闸门 134 → **137**，全量 **137/137**（11m13s） |
| `b2bc771` | 06:2x | test | **「后端广播 type ↔ 前端分发分支」对账闸门**：后端往前端推消息走 `CoreWebView2.PostWebMessageAsJson`，前端接 `chrome.webview.addEventListener('message')`，两边靠一个字符串 `type` 对上，而**没有任何机制保证它对得上**（`quota_alert` 与 `character_switched` 两起都死在这，症状永远静默：分支不执行、不报错，只是"那个功能不工作"）。只做**单向**（前端 type ⊆ 后端广播全集）——反向会假红：`pet_tip` 就走 `api.pop_pet_bubble()` 1.5s 轮询，桌宠窗的 `chrome.webview` 监听对 `PostWebMessageAsJson` **完全不触发**（rig 页 2026-09-03 实锤：3 次广播 / 0 回执），"广播了但没人用 webview 监听器接"在这里是合法设计。边界必须精确圈定：全仓 `.type === 'x'` 有 25 处以上**不是**消息分发（`dataset.type`、本地事件总线 `ev.type==='asr'`、通知项 `n.type`、骨架 `s.type==='row'`…），一刀切必假红。**顺带补登记两把从没跑过的变异刀**：`_qc_channel_mutation` 与 `_qc_gewechat_mutation` 只在 check_all 的注释里被提到、从没进名单——按项目自己的规矩「加门必须同时加变异」，那两道门**一直是装饰品**；实跑前者，七刀里 **C/D/F/G 四把锚点命中 0 次被静默跳过**（根因：`J()` 写死 CRLF 而 `doctor.py` 早变成 LF，**单行锚点不受影响、刀照跑，所以坏得极隐蔽**） | 七刀变异全过（含"原 bug 原样"与两个"不许误伤"对照组）；渠道探活判级变异修好后**七刀全红**、还原字节一致；微信回调变异五刀全红；闸门 130 → **134**；全量门禁 **134/134**（10m43s） |
| `ca6cff3` | 06:2x | fix | **切角色后界面名字不跟随**：`app_backend.js` 里 `d.type === 'character_switched'` 这条分支自 2026-10-02 写下起就是**死的**——后端 `EventType.CHARACTER_SWITCHED` 只经 `ws_server.broadcast` 发（`{message_type, payload}` 信封，给 Unity 与旧壳 `app.js` 用），而 `app_backend.js` 里**没有 WebSocket 客户端**；`app.js` 有 ws 却没这个 case、且只被 `index.legacy.html` 加载。于是 `applyCharacterNameBoot`（那段注释原话就是"用户切换角色后这些仍显示旧名…看着像 bug"）**要修的那个 bug 本身就没修好**：点「设为默认角色」toast 说成功了，侧边栏 / 通话页 / 输入框 placeholder 一直是旧名字，直到刷新页面；`window.refreshCharacterName` 全仓零调用者。两端都修：后端 `_broadcast_character_switched()` 在 `switch_character` 的**两处**成功返回前都调（配置级回落路径不发事件，只挂总线会漏），前端 `setDefault()` 成功后显式兜底 | 新闸门 `check_broadcast_types.py` 的变异刀 M1 就是**原 bug 原样**（把那行广播改掉 → 闸门红并点名 `character_switched`），证明闸门在修复前确实是红的；七刀变异全过；全量门禁 **134/134** |
| `0ab49ab` | 05:3x | fix | **队友包里一直带着 1.1MB 开发残留**：`src/ui/web/redesign/` 下 14 个 `_` 前缀文件（设计稿 / 重构前备份 / 测试台，最大一个是 350KB 的旧设置页）零引用，却按 `git ls-files` 一路进包 —— 队友解包后能在 URL 里打开"能点但没反应"的旧 UI。**不用前缀一刀切**：`_gpu.txt` 同样带 `_`，但它是 `_has_usable_gpu()` 写的运行时数据 | 安全先扫（密钥/绝对路径/token/cookie 全干净）；三层判据（逐个排除 / `_gpu.txt` 必须保留的对照组 / 未登记与 stale 反向催）；四刀变异全红（含"一刀切必须红在 `_gpu.txt`"）；实测 `--dry-run` 排除 14 项、入包 788（跟踪 802）；全量门禁 **130/130** |
| `f971356` | 02:4x | refactor | **额度浮层整条死链删掉**：`window.__quotaAlert` 不是"不可达"，是**三段各自看着都在工作、合起来是死的**——Router 有回调槽位但 `_fire_quota_alert` 从不调、webapp 注册了但存根只 `logger.info` 从不广播、前端有浮层但后端广播 type 全集里从来没有 `quota_alert`。删三段（浮层 IIFE 141 行 + `.qa-*` CSS 25 行 + 注册点 7 行 + 存根 21 行），**保留** `quota_state()` 作将来「检测额度」的数据源 | 全量门禁 **129/129**（9m14s，新增「额度墓碑变异」）；`test_quota_alert` 13→15 项；三刀变异全过 |
| `d14b309` | 22:2x | fix | **反向接线变异刀第二次失效**（同一坑）：M3 选目标时只搜 `webapp.py`，而清单瘦到 3 条后剩下三个全在 `api_pet.py` / `api_window.py` →「找不到可改名的方法」。改成走 `CharacterAPI.__mro__` 定位定义所在文件 | 三刀全绿（M3 报「② `clear_pet_cache`（`src/ui/api_pet.py`）」）；全量闸门 **128/128**（上一轮 127/128） |
| `b78e5ab` | 20:5x | feat | **关系面板的「原型 温柔 ·自动」原来改不了**：那个「·自动」标签本身就在说"手动覆盖是设计里的东西"，但入口一直没建 —— `set_archetype_manual` 和 `get_relation_events` 都是"有实现、没出口"。补两个入口：**原型下拉**（选项由后端 `ARCHETYPES` 给，JS 不写死枚举）+ 底部**最近事件**列表（最近 6 条，最近的在上） | node 真值表 **24 项**（**原样加载** `relation_panel.js` 在假 DOM + 假 pywebview 里跑，含两个对照组：老后端没有 `archetypes` 时禁用而非写死一份枚举 / 后端没连上时不许把下拉变成空壳）；四刀变异全红（调错方法名 / 下拉写死枚举 / 事件行走 innerHTML / 不再拉事件）；反向接线 312 有人调、未接线 **3**；闸门 126→**128** |
| `b12dd39` | 20:1x | fix | **额度浮层「再试一次」点了什么都不做**：`app_backend.js` 里 `typeof Backend.probeVisionModel === 'function'` **恒为 false** —— 后端 `probe_vision_model` 一直在，适配层从没导出它。补导出；并新增闸门 `check_bridge_guards.py` 专守「守卫的目标必须在导出表里」（`typeof` + `if (B.x)` + `!B.x` 三种形态，全站 **220 处** / 109 个不同名字 / 导出表 255 个） | 五刀变异全红（导出少了 / 页面出现假守卫 / **正则坏了 → 必须 exit 2** / 对照组：注释里复述守卫名必须仍绿 / `if (B.x)` 形态也要查）；闸门 124→**126** |
| `5d3c02a` | 20:1x | fix | **6 个「同一份数据的第二个出口」删掉**：`get_audio_status` / `ocr_get_regions` / `ocr_get_anchors` / `chat_overlay_state` 返回的全是 `get_vision_debug()` 里 `ocr`/`audio`/`chat_overlay_open` 的子集（调试台只轮询那一个）；`get_bailian_quota` 是 09-29「额度卡改用内嵌控制台看真实额度」后废弃的本地估算；`get_model_3d_options` 是没人读的写死清单。未接线 **12 → 5**，后端公开 325 → **315** | 反向接线闸门 315 / 有人调 310 / 未接线 5 全绿；三刀变异全红；桥接闸门 166 个调用三层全对得上 |
| `73d1564` | 20:1x | fix | **`smoke_import` 的复核范围太窄**（修上一条留下的）：它只遍历「非预期失败」，而白名单里的模块同样会因为抢内存假红，却**永远不进复核** → 印出「白名单内 1 条」而它其实单独跑能过。改成复核全部失败项 | 自测 12 项（新增"确实进了复核"的前置断言，否则后一条是空断言）；三刀变异全红（M2 锚点因缩进变化失效 → 按"命中 0 次"报红而不是静默跳过） |
| `3689221` | 19:5x | fix | **设置页「录音」按钮从来没成功过**：它调的 `record_audio_reference` 录的是**系统输出** loopback（给游戏音效用的），而参考音要的是**麦克风**；事件白名单又不认 `'reference'` → 点了必失败。新写 `src/audio/voice_ref_record.py` + 桥方法 `record_voice_reference`，前端两处改成 `Backend.recordAudio(2.5)` | 自测 **38 项**（假设备：时长窗口 / 帧数与格式 / 立体声下混 / 99 秒只录 30 秒 / 失败如实报 / 墓碑：假 `record_audio_reference` 一旦被调即 AssertionError）；三刀变异全红；真机录到 176172 字节（本机麦克风静音，peak=2，不是代码问题） |
| `30f7552` | 19:5x | fix | **`smoke_import` 那 3 个红是并发抢内存，不是缺陷**：8 并发 import numpy/scipy 时 OpenBLAS 分配失败（本机空闲 2.4G），报 `Memory allocation still failed`。只对**这一种错误文案**串行复核一次——不按"失败了几条"放宽（重试会掩盖真缺陷） | 自测 **12 项**（独立假根：`flaky` 首败次过 / `memfail` 永远同样文案 / `real` 真缺陷）；三刀变异全红（放宽复核范围 → 真缺陷被掩盖；一律判过 → 真失败被洗出判决；白名单计数不重算 → 判决对、数字错）；实测救回 1 条、非预期失败 0 |
| `e3641e9` | 19:5x | fix | **自测把假文件建在仓库里，被 SIGTERM 打断就留垃圾**：`test_doctor` 的假 sys32 / 假 PE / 坏 YAML 全建在 `tools/` 与仓库根，正常跑完会删，但 **Windows 上 TerminateProcess 不走 finally** → 留下 `ucrtbase.dll` 和 `_broken_cfg_for_test.yaml`。全部改到 `tempfile.mkdtemp()`；顺带修 `check_signature` 的 `os.path.relpath` 跨盘符抛 `ValueError` | doctor 自检 16 组全绿；跑完 `git status` 无残留 |
| `5c4ac3c` | 19:5x | chore | 闸门 120 → **124**：登记「音色录音 / 音色录音变异 / 导入冒烟复核 / 导入冒烟复核变异」 | 全量 124/124 |
| `0c84238` | 18:2x | fix | **反向接线清单里 5 个不是「缺入口」，是假接口**：`enable_tray` / `restore_from_tray` / `set_minimize_to_tray` 只改一个没人读的布尔字段就 `return {"ok": True}`（前端会被告知"托盘已关闭"，实际什么都没变）；`pop_notify_cmd` 调的 `mode_notify.drain_cmds()` **已经不存在**（通知卡改成原生窗，命令队列整个删了）→ 被 `except: return []` 吞成"永远没有积压"；`get_backend_options` 返回 4 条写死的引擎、`pyttsx3` **出现两次**，而真表 `TTS_ENGINES` 有 10 个。全部删除，顺手删掉 `main.py` 里那个没人读的 `runtime.minimize_to_tray` | 变异刀 M3 原来写死 `enable_tray`（删掉后"锚点命中 0 次"→ 静默跳过）→ 改成从清单现取；全量闸门 120/120（7m41s）；未接线清单 17 → **12** |
| `8213c43` | 18:0x | fix | **全站 JS 语法闸门原来只查 `index.html` 一个页面** —— 46 个页面里另外 45 个的内联块、以及除两个硬编码文件以外的所有外链 JS 全是盲区（设置页 8 个块、引导页 4 个块、通知页、桌宠 rig 页都不在内）。扩成全站扫描（46 页 / 65 内联块 / 23 个外链 JS = 88 段），扩完第一处就抓到 `probe2.html` 把 `'\n'` 写成**真换行**、那一整块从未执行过 | 三刀变异全红（非首页内联块 / 原名单外的外链 JS / 首页的**非第一块**）；闸门 119→**120**，全量 120/120（8m28s） |
| `c62ad20` | 18:0x | feat | **设置页新增「关于」分类：版本号 + 开机自启** —— 反向接线闸门抓到的第一批。`get_version` 的 docstring 写着"给**关于页**与排障用"、`get_autostart` 写着"**UI 设置开关**读取"，两处都是**注释承诺了、界面从没建过**。现在设置页多一个分类：版本号（只显示、不另存第二份真值源）+ 开机自启开关（开发环境置灰并说清原因，不假装成功） | node 行为真值表 **20 项**（从页面里原样抽 IIFE 在假 DOM 里跑，含两个"不许假装成功"的对照组）；三刀变异全红；闸门 117→**119**，全量 119/119 |
| `a85207e` | 17:4x | feat | **反向接线闸门**：`check_bridge_methods` 只守「前端调了 → 适配层有 → 后端有」，**反过来没人守**——后端写完了、界面上没有任何地方能触发它（本仓叫「有实现、没出口」，`export_chat_history` 的 `scope='all'` 和 `pet_hit_rect` 都是这么漏的）。新门扫全仓（`name(` 调用点 + 字符串分发三种形态），325 个后端公开方法里 **20 个零引用**，列进 `KNOWN_UNWIRED`；**清单里的名字一旦被接线也必须删掉**，否则它会变成第二个「发现」栏。顺带把 `pet_hit_rect` 那段描述着已不存在架构的 docstring 改成「这是纯 no-op」 | 三刀变异全红（加没人调的方法→③ / 改名→② / 给清单项加真实调用点→①）；闸门 115→**117**，全量 117/117（8m00s） |
| `86288f9` | 16:4x | feat | **「导出全部角色」原本是个没人调的参数**：后端 `scope=current / all` 两个范围都实现并测过，前端只有左键（当前角色）——本仓把这种形态算作缺陷。现在右键 = 全部角色，`title` 里写明。同时补上第 11 组「前端入口」断言，它守的是"入口在不在"，最容易假绿：全页搜 `contextmenu` 会命中页面顶部那条**全局** `document.addEventListener('contextmenu', …)`（`index.html:134`，用来禁掉 WebView 右键菜单），于是判据改成在**导出绑定块内**找 `ec.addEventListener('contextmenu'` | 变异 4 刀 → **7 刀**（删右键绑定 / 右键绑着但不传 scope / 左右键载荷对调），三刀全红；测试 33 → **39** 项；闸门 115/115 |
| `d7784c4` | 16:2x | test | **`test_call_engine_resolve` 红了很久，因为它在守一条已被否决的契约**：13/20 PASS，红的 7 项全是「显式选本地双工 → 走本地」——而 **2026-10-16 用户已拍板「语音电话恒走云端」**（`start_duplex_call` 里无条件 `target = "realtime"`）。这种测试最坏的下场不是红，而是"谁把它修绿，谁就把用户停掉的行为加了回来"。改写为守当前契约：组 1 任何输入组合都必须是 realtime、组 2 没云端就如实拦截、**组 3 给旧契约立墓碑**（`no_cloud_duplex_cfg` / `fallback` / `no_cloud_tts` 在 240 组输入下都不许出现）。顺带把 `start_duplex_call` 里那段自相矛盾的注释（9182 说"显式选本地就走本地"，20 行后被无条件覆盖）标明，并指出 `no_cloud_duplex_cfg` 那段目前**不可达** | 3 刀变异全红（删掉强制云端 → 组 1 红 5 项；错误码改回旧值 → 组 2.3/3.1 红；加回 `fallback` → 组 3.2 红）；22/22 PASS，扫 240 组输入；闸门 113→**115**，全量 115/115 |
| `97f1fc7` | 16:0x | feat | **新增「导出聊天记录」**：`data/chatlog/<char>.jsonl` 里躺着完整历史，但用户没有任何出口（`export_markdown` 只服务日报/记忆页）——想备份、换机器、把一段对话发给别人只能截屏。现在聊天页工具栏多一个下载按钮 → 桌面「AI桌面伴侣报告」下的 Markdown（按日期分节、`**HH:MM · 谁**` 两段式、附图计数），支持当前角色 / 全部角色。**顺带修好桥接闸门的第三层盲区**：内联 `<script>` 完全没有别名支持，`index.html` 的工具栏块（`var B = (window.Backend && window.Backend) \|\| …`）整段不可见（136→163 个桥方法）。补上后立刻抓到三处真断点 | 4 刀变异全红（`load_all` 退回 200 / 去掉空历史守卫 / 空角色不跳过 / 文件名不过滤 `/ : * ?`）；桥接变异 4 刀（内联+别名 ×2、外链、空集退化）；真机探针真实写盘；闸门 110→**113**，全量 113/113 |
| `3df998b` | 14:4x | fix | **「出厂状态」第 6 起**：`model_catalog` 三处（`_build_cmd` / `download_model_quant` / `download_bundle`）在"用户还没选目录"时都回落到开发机的 `H:\minicpm models\...` —— 新用户没有 H 盘，点下载直接失败、复制出来的"可手动执行命令"也指着 H 盘。改成跟着程序走（`<程序目录>/models/...`）。顺带救活一个**搬家后就跑不了**的死引用看门狗（`ROOT = r"G:\ai对话\11"`），它还把结果写进未忽略的 `docs/` | 变异（默认目录改回 H: 盘）→ `默认目录在程序目录下（可移植）` 红；死引用看门狗变异（塞一个不存在的 id）→ 报 `[DEAD] zzzNotExistProbe 首见于第 6718 行` 退出码 1；闸门 109→**110**，全量 110/110 |
| `147d0f5` | 14:3x | fix | **桥接闸门只扫 HTML 内联 `<script>`，外链 JS 全是盲区** —— 补上后发现引导页「下载语音模型」调的是 `B.downloadVoiceModel`，而适配层导出的名字是 `downloadModel`。于是 `if(B.downloadVoiceModel)` 恒 false → 静默落到「无桥降级」分支：**假进度条**（固定 8.2MB/s）走到 100% 还提示"下载完成"，一个字节没下。顺带把三条**早已落地却一直挂着**的「发现」F1/F2/F3 换成真断言 | 变异（塞 `B.zzzNotARealBridgeMethod` / 删后端 model_3d 兜底 / 合并层改成无条件写空 Key）三刀全红；引导页集成 108 PASS / 0 FAIL；全量闸门 109/109 |
| `cb6d312` | 13:3x | fix | **`_save_config()` 里一个静默 no-op，让参考音"上传了却没生效"**：`refresh_characters_map() if hasattr(...)` —— `Config` 上只有 `refresh_characters()`，`hasattr` 恒 False，整行恒等于 `None`。于是保存只写 raw、角色表 `_characters_map` 不跟着变，`get_characters()` 回读 `voice.reference_audio` 永远是空 → 用户反复重传。顺带把 `test_onboarding_integration`（106 项）修绿并收进闸门，删掉 `check_all` 里**重复登记**的导入冒烟 | 变异（把幽灵调用放回去）→ `[B15] 参考音已落角色` 红、退出码 1；闸门 109 项全绿（引导页集成 35.4s）；顺带修好「闸门失败摘要只摘最后 4 行、恰好摘到白名单明细表」 |
| `55484a3` | 03:5x | fix | **变异脚本的锚点全失效**：26 个 `_qc_*` 里只有 2 个在闸门，另有 4 个变异脚本不在——而它们验证的检查器**全在闸门里**（等于 4 个判据可能早已没牙齿却没人验证）。2 个红的真因是 `run_all` 加了 `check_wake_words`，锚点原假定 `check_asr` 是列表最后一项。顺带查清一个更隐蔽的坑：**我自己用 `io.open(p,"w")` 改源码，把 1398 行全写成 CRLF**，于是所有 `\n` 锚点静默失效 | 两处锚点改成不依赖位置 → 0 跳过、七刀全红；加 `.gitattributes` 固定 LF（实测 checkout 能把 CRLF 纠正回 LF）；4 个变异脚本进闸门 105→109，全量 109/109 |
| `7108ce8` | 03:3x | test | **第三批普查第二轮**：剩下 5 个红全修好，无一需要改产品行为。最值钱的两条：`test_relationship_wiring` 的白名单抄的是 **v1** 的 `MOODS`（6 种），运行时用 **v2**（17 种含「困倦」）→ **凌晨红、白天绿**，一条依赖时间的判据；`test_quota_alert` 验的是「额度耗尽主动弹窗」，而该行为 **2026-10-02 已被用户停掉** | 5 刀变异全红（清空预设 / 清空 MOODS_V2 / 把弹窗接回去）；闸门 100→105，全量 105/105 |
| `6e7bf7c` | 03:0x | fix | **出厂缺陷第 5 起**：模板里 qq/wechat/qzone 三个通道默认 `enabled: true`，新用户装完就去连不存在的本地服务（OneBot 每 5s 刷一条 warning 永不停）；顺带修好 7 个"测试与产品脱节"（桩缺导出名 ×2、判据过时、回调签名过时、fire-and-forget 时序、硬编码旧路径 ×2） | 变异（模板改回 true）→ 报 `实际构建了 ['qq']` 退出码 1；闸门 80→100 |
| `62287f4` | 02:3x | fix | **导入冒烟收进闸门**（79→80）：它抓的「这个模块能不能被加载」没有任何别的门禁能抓——`compileall` 只保证语法和名字对得上（10-17 两例实测全绿）；顺手修掉它自己的松口子：白名单 `why.strip() or "（未写原因）"` 等于**允许无原因加入** | 172 模块 0 非预期失败；变异三刀 1/0/1（含对照组）；全量 80/80 通过，导入冒烟真跑 23.2s |
| `c6ff5e4` | 02:2x | fix | **doctor「配置文件」判据恒真**：`raw_cfg.get("api_key")` 读的顶层键**根本不存在**（密钥在 `llm.openai` / `text_chat.providers[]` 等处），于是只要有 `data/` 就恒报「配置可能被覆盖」——所有在用的用户都看到假警。改成递归扫任何非空 `api_key` | 三态 + 对照组 9 项（含「有 Key 不许报」）；变异（判据退回旧写法）→ 场景 2/4a 变红退出码 1；`--doctor` 16 PASS/3 WARN → **17 PASS/2 WARN**；门禁 79/79 |
| `80eb3d7` | 02:1x | fix | **models 层不再推荐已下架的 `edge-tts`**：`protocols.py` 的 `TTS_ENGINES` 还留着它，`hint` 甚至写着「零配置、免费、立刻能说」，而 webapp 侧 10-16 已按发布口径下架。只从推荐清单摘掉，后端保留（老配置仍能解析，静音是 10-16 的有意设计）。顺带治了一条脆弱判据：`check_models_layer` 硬编码 `len(TTS_ENGINES)==11` | 修前 15/16 → 修后 16/16；变异（把 edge-tts 塞回表）→ `[FAIL] 引擎表漂移` 退出码 1；投影层自检收进闸门，门禁 78/78 |
| `4f85ac9` | 02:1x | test | **第二批测试普查**：又试跑 8 个，6 个能过收进闸门（71→77）；`test_character_card_images` 的 3 FAIL 是**假红**（`/local_img` 早随 UI 拆分搬进 `main_window.py`，测试还在看 `webapp.py`）；`test_call_engine_resolve` 7 项红着但**用例自相矛盾**（1.2 与 2.2 冲突），标为待裁决；`check_models_layer` 抓到**真漂移**——models 层还在推荐 10-16 已下架的 `edge-tts` | 逐个复核退出码 0；角色卡修后 21 PASS / 0 FAIL；门禁 77/77 |
| `63e21bc` | 02:0x | test | **108 个测试里 54 个不在闸门**；其中 5 个硬编码 `G:\ai对话\11`（旧项目路径）→ **从搬进本仓库那天起就跑不了**，自然没人发现它们不在名单上；`test_write_layer` 还缺退出码（FAIL 也返回 0，装饰闸门） | 逐个试跑确认后加 12 道（59→71，合计只多 16.6s）；退出码变异（强制 `sys.exit(1)`）验证为 1；门禁 71/71 |
| `658d431` | 01:5x | fix | **会话上下文落盘有初始化顺序依赖**：桶在 `enable_persistence()` 之前被创建就永远不落盘——对话照常、日志正常、**重启全丢**；顺带发现 `tools/test_context_persistence.py` 27 项断言全绿却**从未进过闸门** | 探针实测两种顺序（先 enable → 2 行 / 先建桶 → **0 行**）；修后补用例 14 + 接进闸门；变异（删补挂）→ `FAIL 14 got=0` 退出码 1；门禁 58→59 |
| `59b929e` | 23:1x | fix | **出厂模板里 7 个开发机绝对路径**（角色头像/头图指向 G: 盘、本地双工与 omni 的 exe/模型指向 H: 盘）→ 新用户装完就是裂图；顺带挖出**微信回调拒绝时 401 变成连接重置**（不读请求体就 close） | 模板闸门 [6] 扫解析后的 YAML + 两刀变异精确报出 `$.proactive.duplex_local_omni_exe`；回调判据改成"看服务端等不等 body"（不拿竞态当断言），变异 4/4 全抓；新用户自检 16 PASS/3 WARN/0 FAIL；门禁 58/58 连跑两次 |
| `d01ea7b` | 22:0x | docs | **README 改成项目门面**（旧用户指南移入 `docs/`，两份流程分开写清）+ 新增「文档链接不悬空」闸门 | `--doctor` 实跑取准基线 16 PASS/3 WARN/0 FAIL；变异 3 刀全红；门禁 57→58 |
| `7816fa1` | 21:5x | chore | **入库 GitHub（私有）**：推送前做了三项体检——跟踪文件与**全部历史新增行**的密钥形态扫描（0 命中）、`config.yaml`/`data/`/`logs/` 从未进过历史、忽略规则复核；顺手忽略 `.workbuddy-ai/` | 774 文件 / 123 提交 / 14.1MiB；远端 `ls-tree` 复核无实配与隐私文件；HEAD == origin/master |
| `8c789b0` | 06:2x | feat | **「去配一个」落到的那页先说人话**：标题「能力槽」→「接入你的模型」，lead 从架构讲起改成从她要做什么讲起，并加一行隐私说明（Key 存本地、不经我们） | 隐私那句先核实过（全仓外发域名无第一方服务器）；headless 截图确认排版 |
| `74dc041` | 06:2x | feat | **首启不填 Key 的代价，由「意图点承接」接住**：他打完字、点发送被挡下的那一刻才弹「她还没接上大脑」（顶部条出现在错误的时刻）；判据抽成三态供两处共用 | node 实跑判据真值表 9 例；变异 18 刀全红；门禁 55→56 |
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
| `PR#2` | 待合并 | fix | **doctor 三处「只看存在不看状态」的假红**：① pypinyin 检测只看 `importlib.util.find_spec("pypinyin")` 不为 None，但包内存在 pypinyin 目录无 `__init__.py` 时 find_spec 返回 None 而包是好的 → 误报「唤醒词退化」；改为 `find_spec()` + 显式 `import pypinyin` 双重验证。② WebView2 检测只看注册表键存在，不读版本号 → 空/损坏键也 PASS；补版本号非空检查。③ UCRT 检测只看 `api-ms-win-crt-*.dll` 文件存在，不 LoadLibrary 验证可加载 → 损坏 DLL 也 PASS；补 `ctypes.WinDLL` 试加载。 | 变异验证：损坏 WebView2 键 / 空版本 → 改前 PASS 改后 FAIL；LoadLibrary 失败 → 改前 PASS 改后 FAIL；还原后全绿 |
| `PR#3` | 待合并 | fix | **text_chat_router 空 Key 模板端口误报 WARNING**：路由器对 `model.api_key` 做真实性检查，但交付包出厂模板里 `${ENV_VAR}` 引用展开后是空字符串；空 Key 是预期初始态（首启不问 Key 的设计），却被当成异常按 WARNING 打日志 → 首启 doctor 有一条误导告警，让用户以为「已经配了但没配对」。修为：检测到 Key 为空时按 DEBUG 打、附说明「首次使用需在设置里填入 Key」，不触发 WARNING 路径。 | 变异验证：Key='' → 改前 WARNING 改后 DEBUG；Key='sk-xxx' → 两版均正常 INFO；还原后字节一致 |
| `PR#4` | 待合并 | fix | **webapp get_quota_status 指向不存在的 quota_guard 模块**：`src/ui/webapp.py:7729` 在 `try/except Exception` 块里 `import src.ui.quota_guard` 做 API 额度角标数据源，但全仓无 `src/ui/quota_guard.py`（该模块从未提交）→ import 恒走 except、额度角标永远静默不显示，用户看不到剩余调用次数。两条修法：①（本 PR）把 try 块改为直调 `self._get_quota_from_config()` 从 config 读，不依赖缺件；②（备选，未采）等源码仓补发 quota_guard 后从白名单移除。 | 变异验证：临时创建空 quota_guard.py → 改前 import 成功返回 None 改后仍走直调路径不依赖；删掉 quota_guard.py → 改前恒 except 改后正常返回 config 数据 |
| `PR#5` | 待合并 | chore | **新增交付包发布前闸门 `tools/check_delivery.py`**：本机交付包无 `.git`，改动不能走 git 闭环，需要独立发布前闸门防漏件。七层判据：① 36 个必备文件逐条点名（含来源注释）；② 6 棵资源子树文件数下限；③ AST 扫 `src/**/*.py` 的 145 个 `src.*` 导入目标逐个解析；④ 文件实体 PE `MZ` / HTML 结构 / RIFF / 非空文本 / YAML 映射五类魔数；⑤ 依赖完整性——`site-packages/*.dist-info` 对 `requirements.txt` 核心 12 项 + 重要 13 项；⑥ 出厂模板 `force_first_run=true` + `app.version` 一致 + 38 个 api_key 无明文；⑦ 临时目录复验「误删 config.yaml 能自愈」。可选 `--smoke` 真跑包内解释器做 import 冒烟。 | 32 个变异场景全红（含 `--root` 端到端）+ 4 个按预期 WARN 不误报；交付包实测 8 PASS / 1 WARN / 0 FAIL |
| `PR#6` | 待合并 | fix | **main.py 解释器守卫——拦「用错 python 跑 --doctor」的误导性 FAIL**：交付包用 `python/python.exe`（isolated 模式）跑 doctor 得 16 PASS / 3 WARN / 0 FAIL；用系统 python 跑得 10 PASS / 5 WARN / 4 FAIL（核心依赖缺失 / 语音坏了 / 唤醒词退化），全是「该解释器没装包依赖」的假象。包内 python 不在系统 PATH，用户极易直接 `python main.py --doctor` 触发假象 → 误判「包是坏的」。改动：`main.py` 的 `--doctor` 截获块加前置检测，交付包存在但当前解释器不是它时打醒目提示（列假象 + 给正确重跑命令），**不阻止运行、不改退出码**。源码仓无 `python/` 目录 → 触发条件恒假 → 守卫整体跳过，零干扰。 | 交付包双向实测：系统 python → 提示 + 4 个误导 FAIL；包内 python → 无提示、干净 16-3-0；`tools/check_delivery.py --smoke` 8 PASS / 0 FAIL 无回归 |
| `PR#7` | 待合并 | fix | **create_notify_window 329 行不可达代码墓碑**：`src/ui/main_window.py` 的 `create_notify_window()` 里 try 和 except 两条分支都 `return`，其后的 329 行 WebView2 旧实现永远执行不到——死代码占位、误导维护以为那是活跃路径。删掉 return 之后的整段死代码，改用新增回归 `test_notify_dead_path.py` 守住「create_notify_window 真正调到 native_notify.ensure_notify_window()」+ 变异刀 4 刀（try 提前 return / except 吞掉 / 删掉调native_notify / 改坏 return 值），全红 + 还原全绿；登记进 `check_all.py` 门禁。 | 变异 4 刀全红；还原后回归 12/12 全绿；check_all 登记对账 ✓ |
| `PR#9` | 待合并 | fix | **4 处不可达代码清理 + 新增「必返回之后不许有语句」闸门**：`create_notify_window` 的 329 行死代码（#7）暴露出一个类目——必返回语句之后还有语句，编译不报错、测试不报错、跑起来也看不出来，只有人眼逐函数读才发现，所以能潜伏两个月。本轮全仓扫出并清理 4 处：`proactive.py` 一条「已停止」日志写在 try/after 全 return 之后→从未打印，移进 finally 才真会在每条退出路径打印；`api_pet.py` 一份 default_responses 兜底字典，上面 if/else 两分支都 return→永远到不了；`notify_geom.py` 17 行旧实现，前面一行裸 return（2026-09-05 定版加的）后全部死代码；`webapp.py` 1 行同类。新增闸门 `tools/check_unreachable.py`（AST 判「必返回后不许有语句」，`unreachable-ok` 注释豁免故意不可达），+218 行变异刀 `tools/_qc_unreachable_mutation.py` 证明判据有牙齿，登记进 `check_all.py`。 | 新闸门全绿（4 处清理 + 豁免标记认得）；变异刀 4/4 全红 + 还原全绿；check_all 登记对账 ✓；CI 四腿全绿 |
| `PR#10` | 待合并 | fix | **relationship_v2 删掉被静默覆盖的 37 行同名函数 + 新增同名覆盖闸门**：`src/companion/relationship_v2.py` 里有一个函数被**同名函数定义静默覆盖**——Python 里后一个 `def` 会覆盖前一个，前一个 37 行函数体从定义那一刻起就永远不可达（编译/测试/运行都不报错，只有逐行读才看出）。删掉被覆盖的 37 行死函数体，保留真正生效的那个；新增闸门 `tools/check_duplicate_def.py`（AST 判「同作用域内不许有同名函数定义」，`duplicate-ok` 注释豁免故意覆盖如重载兼容垫），+220 行变异刀 `tools/_qc_duplicate_def_mutation.py` 证明判据有牙齿，登记进 `check_all.py`。 | 新闸门全绿；变异刀 4/4 全红 + 还原全绿；check_all 登记对账 ✓；CI 四腿全绿 |

> **\* 夹带说明（必须留档）**：12:10 / 12:2x 两行的内容实际随 `fa86a4c`（E1 开机自启）
> 入库——13:14 的并发提交把质检工作流未提交的改动一起提交了，未按工作流拆分。
> 代码与测试主体由 `5dbc64c` 收口，**归属以本脚注为准**。
> 教训：并发会话各自只 `git add` 自己知道的文件，提交前先 `git log` 确认HEAD 没有
> 在几分钟内被别人推进过。

### 2026-10-22 · 第七处静默失效：桌面消息后台任务吞掉 respond 尾段异常（她听到却不回答）

**这一处守的是「听到→想→说」主链的收口。** ASR 唤醒后发 USER_TEXT 已由
`test_asr_event_flow.py` 证明（efad34e）；本轮补上后半段——USER_TEXT 被 Dialogue
消费后，真的产出一条可播放的 AI_DECISION。

**风险点**：桌面来源（`_DESKTOP_SOURCES = ("webui","voice","console","")`）的
USER_TEXT 在 `_on_user_text`（dialogue.py:840）走 `asyncio.create_task` 后台任务
（2026-10-04 为修「UI 卡片后出」刻意为之，不能在这里 await）。而 respond 的尾段——
`parse_decision` 之后的剥 markdown（1228）/ 拟人化（1232）/ JSON 兜底 /
`shared_history` 记录（1279）/ 发 AI_DECISION（~1283）——**在 respond 内部那个
try/except（1126~1159）之外**。LLM 调用本身有 75s 超时兜底，但尾段没有：一旦这里抛
异常（或 `_respond_vision_chat` / 媒体摄入崩），异常落到无人 await、也无 done_callback
的后台任务上 → 静默吞掉 → 用户「说了话没人应」，且连失败气泡都没有。这正是 §5/§6 那类：
链路看着在跑，其实断了。

**修法**——两层，判据必须一致：

1. **尾段自己的守卫**（dialogue.py:1196 起，包住 1205~1336）。判据与 respond 里**原有**
   的 LLM 失败分支（1126~1159）同构：**她有没有已经说出口的话**。
   - 流式已播出 → 补发一条 `streamed` 保持原值的**非 failed** 决策。她话已说出口、
     TTS 已逐句念完、聊天气泡已封口上屏（`_on_chat_partial` 把片段写成真气泡），
     此时再补一句"失败"就是"已回答 + 红字失败气泡"自相矛盾。
   - 毫无产出 → 才发 failed 失败气泡（带 `_fail_text(e)` 的具体原因）。
   - 取文本用 `parse_decision(streamer.raw) or parse_decision(raw)`：流式路径两者同一个串；
     **非流式路径**（后端不支持流式，`_chat_decision` 返回 `llm.chat` 的整段）streamer 从未
     被喂过，只取 streamer 会拿到空而误判成"毫无产出"。
2. **任务级 done_callback 作最后防线**（dialogue.py:850/874）。`t.result()` 取出异常
   （消除 asyncio 的 "Task exception was never retrieved" 警告，`CancelledError` 单独放过）
   → `logger.exception` 落日志 → `publish_threadsafe` 补发一条 `failed=True` 的
   AI_DECISION（前端红字失败气泡）。用 `publish_threadsafe` 而非再 `create_task` 去
   publish：它同步入队，不依赖调用方是否 await 这个本就是 fire-and-forget 的任务。
   它覆盖的是第 1 层管不到的地方——`_respond_vision_chat` / 媒体摄入等 respond 之外的支路。

**验证**（五场景真值表，缺一不能区分"判据正确"与"判据恒真"）：

| 场景 | 流式产出 | 崩在哪 | 期望 |
|---|---|---|---|
| 1 正常链 | 有 | 不崩 | 一条非 failed 决策，`streamed=True` |
| 2 **已答完再崩** | **有** | respond 尾段 | **不得**有 failed 气泡；一条非 failed 决策 |
| 3 非流式再崩 | 无（后端不支持流式） | respond 尾段 | 不得有 failed；非 failed、`streamed=False` |
| 4 LLM 就没生成 | 无 | LLM 调用 | **必须**有 failed 气泡（守住原有兜底） |
| 5 崩在 respond 之外 | — | 媒体摄入探测 | 必须靠 done_callback 补发 failed 气泡 |

- `tools/_qc_dialogue_voice_chain_mutation.py`：**七把刀全红**——CB 摘
  `add_done_callback`（场景 5 翻回静默）；T1 守卫 `if _partial:` → `if False:`
  （场景 2/3 被补失败气泡）；T2 守卫丢掉 `or parse_decision(raw)` 兜底（场景 3 误判
  毫无产出）。变异工具通过 `harness.Dialogue = mutant.Dialogue` **原样跑测试里的场景
  函数**，不另抄一套断言。
- 夹具 `src/tests/_dlg_voice_harness.py`：单测与变异共用同一驱动；`drive_voice`
  **不 await 后台任务取异常**（那正是被测的静默点），改轮询 done + `task.exception()`；
  等待全用**循环轮次** + `bus._queue.join()`，不用 5s 超时（依赖时间的判据会偶发假红），
  变异耗时 7.9s → 3.5s。
- 全量门禁：本轮 +2（137→139）；跑门禁时工作区另有并发的「闸门登记对账」+2，合并实测
  **141/141** 全绿、无跳过。

### 同日续：自查抓到的三个自身缺陷（已一并修）

上一轮收尾后 review 自己的改动，抓到三处，都在本轮修掉：

1. **测试把错误行为钉成了契约**（最重）。harness 第一版把 `_chat_decision` 整个换成返回
   `(RAW, True)` 的桩——`streamed=True` 是**假的**：一条 PARTIAL_TEXT 都没发、TTS 什么都没
   念、气泡什么都没上屏。于是"尾段崩"场景模拟的是"崩在流式之前"的世界，与它注入的位置
   （流式之后）不是同一个，从而掩盖了"她已答完 + 红字失败气泡"这个真缺陷；而场景 2 的
   断言还**主动要求**看到失败话术——谁把矛盾修好，这条测试会红着拦他。
   修法：**不桩 `_chat_decision`**，让 FakeLLM 真支持流式（`supports_stream` +
   `chat_stream`），跑生产那条真流式路径；场景 1/2 用 `partials` 非空证明"她确实已经
   说出口了"。这正是 §5「Python 复刻只证明我两次写得一样」那一类。
2. **判据依赖墙上时钟**。`drive_voice` 用 `sleep(0.05)` 轮询 + `wait_for(..., 5.0)`，
   变异刀那个"静默"结论实际是"5 秒内没到"。改循环轮次 + `queue.join()`。
3. **文档行号被自己的修复冲掉 +33**。commit message / 明细 / memory 都写 "1186~1251"，
   真实已是 1205~1336——下一个人照着 1186 找会落到 `to_idle` 上。

顺带修掉 memory 里一句写过头的话："fire-and-forget 必须配 done_callback"——同文件另 3 处
`create_task`（727/1339/2388 一带）是靠**自身内部 try/except** 兜住的。正确规则是
「**任务体自己兜住，或调用方兜住，二者有一即可，且都要能出声**」。

**并发纪律**：本轮提交只 `git add` 自己的文件（dialogue.py + 3 个测试/工具文件 +
check_all.py 里**自己那一 hunk**，用 `git add -p` 精确摘选），把并发的「闸门登记对账」
工作树原样留下（check_all.py 的 hunk1 + 8 个改动的 check_*.py + 2 个未跟踪工具）——
又一次印证「只 add 自己知道的文件，别让收口提交吞掉别人的改动」。

### 同日再续：顺着"不可达分支"查到两处真实的静默/错误播出

尾段守卫里留了一条"毫无产出 → failed"的分支，注释写着"正常情况下不可达"。顺着它
查下去，发现**不可达是真的，但附近藏着两个真问题**——都在 respond 的清洗环节：

| 模型吐了什么 | `_strip_markdown` | `_humanize_reply` | 原代码结局 |
|---|---|---|---|
| `（翻个白眼）`（纯舞台动作） | 原样保留 | **返回 `""`** | `if _hum:` 不成立 → 文本**保持未清洗的舞台指示** → 非流式路径下 TTS 把它当台词念出来（"翻个白眼"） |
| `****`（纯 markdown 骨架） | **返回 `""`** | — | `decision["text"]` 变 `""` → 发一条 `text=""` 的决策 → **前端静默丢弃** → "说了话没人应" |

第二条正是 10-11/10-14 两次要治的"舞台指示上屏/进 TTS"，只是换了个入口：那两次治的是
流式分句（`_strip_stage_directions`）和提示词，**终稿这条路一直是漏的**。

**修法**：清洗之后判空，且**查 `_hum` 而不是 `decision["text"]`**——一查同时覆盖两种
情形（`_humanize_reply` 只在"输入本身是空 / 全是舞台动作 / 全是空白"时返空；实测
"好呀/嗯/啊？/呜呜……"都不返空，不会误杀正常短回复）。走**同一个出口**
`_publish_empty_reply`，文案提成模块常量 `EMPTY_REPLY_TEXT`——因为现在有**两个**调用点
（parse 后就是空 / 清洗后被清空），必须逐字一致，否则又是"同一件事两份实现"。

**上一问的那个待决项也一并处理了**：把不可达的 `else` 分支改成**向上 `raise`**，交给
任务级 done_callback 用同一条兜底路径发失败气泡。理由：不可达的分支是没人跑过的死
代码，而且它会在仓里复制第二份失败气泡载荷（和任务级兜底形成双实现）。宁可达不到，
也不写没人验证的分支。

**验证**：测试扩到**七场景 / 45 项断言**（新增场景 6 纯舞台动作 + 纯 markdown 骨架、
场景 7 模型直接返回空 text——后两者合起来证明两个调用点产出逐字一致）；变异
**三把刀 → 五把刀**（新增 T3 去掉清洗后判空、T4 改 `EMPTY_REPLY_TEXT` 抓文案漂移）。
基线七场景全绿、五刀全红。

### 同日三续：三个能出声的入口，另两个一直没守

主链守住了，顺势查"还有哪些入口会发布带模型文本的 AI_DECISION"——**还有两个，且都没守**：

| 入口 | 原状 |
|---|---|
| respond 主链 | 已守（上一轮） |
| 聊天发图 `_respond_vision_chat` | 只 `_strip_markdown`，**不判空**；且**从不清舞台动作**（10-12 工具链对齐过，视觉链没对齐） |
| 聊天调工具 `_respond_with_tools` | strip + humanize 之后**不判空** |

两个入口的坏结局一样：模型/VLM 只吐"（翻个白眼）"这类纯舞台动作时终稿为 ""，发出去被
前端静默丢弃 → "图也发了 / 工具也跑了，她却不说话"。

**修法**：把判空抽成共用闸门 `_guard_speakable(text, channel, reply_target)`（内部走
`_publish_empty_reply`），三个入口统一调用。视觉链的清洗口径也对齐成工具链 `_emit_seg`
同款 `_drop_stage_dirs(_strip_markdown(...))`——`_drop_stage_dirs` 是"全删空时返回空串"
的变体，不走 `_strip_stage_directions` 的"退回原文"保底（保底恰会把残骸放出来）。
**不给视觉链加 `_humanize_reply` 的句数截断**：图片描述不该被"回多长看对方"砍断。

**验证**：测试扩到**九场景 / 57 项断言**（新增场景 8 聊天发图、场景 9 聊天调工具，
每个都跑**正反两路**——正常文本必须照常播出，证明闸门不是"永远发失败"）；
变异**五把刀 → 七把刀**（T5 拆视觉链闸门、T6 拆工具链闸门）。基线九场景全绿、七刀全红。

**另案记录（不在本轮改）**：`_respond_with_tools` 发布 AI_DECISION 时**不带
channel/reply_target**（写死桌面语义），而它是从 `respond` 的 `channel` 分支进来的
⇒ QQ 等外部通道的消息若命中工具链，回复会串到桌面播。这是"她在错的地方说话"类缺陷，
与本轮"她不说话"类不同，需单独查通道语义后再改。

### 同日四续：那个"另案"查下来是真 bug——一头没接到、另一头接错

顺着上面那条另案查通道语义，链路是这样的：

```
base._feed_brain(text, reply_target)                      # 通道侧，正确带上
  → dialogue.respond(text, source, channel=self.name, reply_target=<peer>)
      → _respond_with_tools(...)                          # ← 这里把两个参数丢了
          → publish(AI_DECISION)  ×4 个发布点，一个都不带 channel/reply_target
```

而两端的判据是：通道侧 `_on_decision` 判 `d.get("channel") != self.name` 就忽略；
桌面侧（ws_server）只播 channel 为空的。所以工具链发出的决策 **channel 为空**：

- 通道侧判不匹配 → **直接忽略 ⇒ 发消息的人完全收不到回复**；
- 桌面侧当成桌面决策 → **桌宠替那条 QQ 消息念出来**。

一头没接到、另一头接错了，而且**两头都是静默的**（没有报错，只是"她不回"+"桌宠自言自语"）。
默认 `allow_tools_in_chat: false`，所以这是潜伏缺陷——用户一开"聊天中允许调用工具"就发作。

**修法**：`_respond_with_tools` 增加 `channel` / `reply_target` 形参，从 `respond` 透传；
**4 个发布点全部带上**（终稿 AI_DECISION、超时失败气泡、确定性失败气泡、PARTIAL_TEXT）。
PARTIAL_TEXT 那个是与 `_chat_decision._emit` 对齐（它本来就带 channel）。

**验证**：测试扩到**十场景 / 64 项断言**（新增场景 10：端到端跑
`respond(..., channel="onebot", reply_target=<peer>)`，断言决策带上这两个键，并**照
`_on_decision` 的过滤逻辑直接验两头**——通道侧会认领、桌面侧不会播；反路对照桌面来源
channel 必须留空，否则桌宠会把 QQ 的回复也念一遍）；变异**七刀 → 八刀**（T7 丢掉终稿的
channel/reply_target → 场景 10 判红）。基线十场景全绿、八刀全红。

### 同日五续：把 respond 的每个调用点对一遍，又抓到一个同型断点

上一轮的教训是"中间层丢参数最难发现——两头都看着对"。所以把这家族**从头到尾对一遍**：
`base._feed_brain` 对；`_respond_vision_chat` / `_process_desktop_message` / unity 三条
只可能桌面来源，`channel=""` 是正确语义；**只有 `_on_user_text` 的社交通道那一跳
（dialogue.py:893）也是断的**——`await self.respond(text, source=source)`，同样不带
channel/reply_target。

当前生产里社交通道并不走 USER_TEXT（它们由 `_feed_brain` 直接调 respond），所以这一跳
还没爆。但它是同一类的断点：谁若从 USER_TEXT 喂一条通道消息进来，回复就会因 channel 为空
被通道侧忽略、同时被桌面播走——正是上一轮刚修好的那个形态。

**修法**：从事件载荷取着往下传（`channel=str(d.get("channel") or "")`、
`reply_target=d.get("reply_target")`）。载荷里没带时缺省空、**行为与改动前完全一致**；
带了就生效。不预填假值，也不改 `_DESKTOP_SOURCES` 的路由。

**验证**：场景 10 增加第三条路——直接发一条带 channel/reply_target 的 USER_TEXT 事件，
断言决策带上这两个键（67 项断言）；变异**八刀 → 九刀**（T8 把那一跳改回不传 → 判红）。

### 同日六续：一句纯聊天，永久抹掉她"看懂游戏"的能力

顺着上一轮查"想"这一层（`_decide_search` / `_build_messages`）。`_decide_search` 引用的三个
类属性正则都在、能编译（docstring 里记的那次 NameError 已修）；`build_user_turn` 六个参数
全用上了（搜索链在提示词层没断）。**但 `_build_messages` 里抓到一个真的**：

它用 `self._game_knowledge = None` 来表达"本轮不注入画面信息"（10-04 治"纯聊天也看屏幕"）。
问题是 `_game_knowledge` 是 **main.py 两处启动路径传进来、且被 `VisionAnalyzer`
（`bind_game_knowledge`）持续更新 OCR/画面文字/锚点状态的同一个活对象**，而**全仓没有任何
地方能恢复它**——没有 setter，`set_runtime` 只注入 `_vision_router`。

实测（`type(dlg)._build_messages` 真身 + 假知识库）：

```
初始        : _game_knowledge 是 FakeGK
一句"你觉得什么相机好"（纯聊天）之后 : _game_knowledge is None   ← 永久
再问"这局打得怎么样"（游戏相关）      : 背景常识/画面文字/锚点/游戏名校正 四项全无
```

症状：**"她一开始看得懂我在玩什么、能读屏幕；聊了几句别的之后再也不会了，重启才好"**
——静默、永久、进程级。而且 `knowledge` 对象本身还在被分析器更新，数据一直在，只是
`dialogue` 把引用丢了。

**修法**：改用**局部变量** `_gk`。本轮照样压掉画面信息（原意图完整保住），但不碰实例状态。

**验证**：新增场景 11（`keep_build_messages=True` 让 harness 不打桩，直接测"想"这一层），
正反两路 + 对照组：纯聊天后引用必须还在、画面必须仍被压掉、背景常识仍注入；紧随的游戏
问句四项必须全回来；首轮就是游戏问句时四项也齐（证明判据不是恒真）。**76 项断言**；
变异**九刀 → 十刀**（T9 把局部变量改回抹实例引用 → 场景 11 判红）。

> 顺带修了自己测试里的一个坑：场景 11 第一版用 `from src.companion.dialogue import Dialogue`
> 直接取真类，导致变异工具换 `harness.Dialogue` 对它无效、T9 判"没鉴别力"。
> 改成 `type(dlg)._build_messages` 从实例取类——**测试里取被测类必须走注入点，不能直接 import**。

### 同日七续：`inject_context=false` 是"只存不用"，两条写路径却做成"不记"

顺着查"看"这一层（`_shrink_images` / `VisionRouter.vision_chat` / `_extract_images`）。
前三个查过没大问题（压缩失败一律原样返回、三种图片输入都覆盖、单张坏图不连坐），
**但顺着"她看到且记住了"这个承诺查到 `_remember_seen_images` / `_remember_pending_promise`**：

两个**写**路径都多闸了一道 `_memory_inject_enabled()`（`inject_context`）。而这与三处相矛盾：
`_memory_inject_enabled` 自己的 docstring、`_build_messages` 的注释、以及其它**所有**写路径
（提炼 / 纠正 / 情节——它们只看 `store is None`）。该开关的语义是"**只存不用**"。

实测（`inject_context=false` + 假记忆库）：

```
看图记忆写入: False | store.added = []
承诺记忆写入: False | store.added = []
```

后果：按建议关注入的小模型用户（docstring 里正是这么建议的），**silently 丢掉全部**
"她看过这张图" 和 "用户说过要给我看照片"——后两者分别是 10-04 两个修复的目标
（"她压根不知道有过这张图" / 注释自己写的"最该记住的一类，忘了就是失约"）。

**修法**：删掉这两道闸。关记忆的正闸门是 `memory.enabled`（`_make_memory_store` 会因此
返回 None，由既有的 `store is None` 挡住）；`inject_context` 只管注不注入，不管记不记。

**验证**：新增场景 12，正反两路——注入关 → 照常记录**且**不注入；注入开 → 记录且注入
（证明"不注入"那一半不是恒真）。测试扩到 **82 项断言**；变异**十刀 → 十二刀**
（T10/T11 分别把两道闸加回去 → 场景 12 判红）。

顺带清掉这一路查到的两个死代码：
- `vision_router.py` 的 `_vm = ...`——10-04 注释写"改为动态取值"，但算出来后**从未被使用**，
  抛出的仍是写死那句（目前恰好是对的，因为 `_perception_enabled()` 只读一个键）。
  删掉死变量并把"只有这一个条件"写成显式说明。
- `dialogue.py` 的 `_vision_enabled_for_chat()`——**全仓零调用者**，只在解释"旧门槛已删"
  的注释里被提到。留着会让人以为发图前有可用性预检（其实没有）。删掉并留墓碑注释。

### 工程坑：又一次踩了自己的 MSYS heredoc 坑

给变异工具补 T10/T11 时用 `python - <<'PY'` 写补丁，heredoc 里的 `\\n` 被 **MSYS 路径转换
改成 `//n`** → 生成的两把刀锚点 0 命中、变异体还 SyntaxError。**这条 memory 里明明写着**
（"heredoc 会改字节字面量里的 \n，锚点用 bytes([10]) 拼或把变异脚本落成 .py"），还是踩了。
改用 Edit 工具（不过 shell）重写才对。教训升级：**只要内容里出现反斜杠，就不要用 heredoc**，
不管它是字节字面量还是普通字符串转义。

### 同日八续：双工通话里用户说的话，混进桌面聊天页了

顺着上一轮末尾留的疑点查"双工用户在 `_on_user_text` 被早退"那条。早退本身是**两端都有注释的
有意设计**（proactive 发 `USER_TEXT{source:"duplex"}` 专为驱动好感度，dialogue 早退避免误回
聊天）。但顺着"这个事件还有谁订阅"查下去，7 个订阅者里**两个漏了**：

| 订阅者 | 漏的情形 | 后果 |
|---|---|---|
| `webapp._on_chat_user_text` | 只判 `is_proactive`，不判 `source=="duplex"` | 通话中用户说的每句话都被当成**打字消息**追加进 `_chat_buffer`，并被 `_persist_chat` **落盘** |
| `activity_reporter._on_user_text` | 两个都不判 | 日报/周报把通话原话当打字聊天 |

而 `CALL_SPEECH` 这个通道 10-02 建立时的 docstring 原话是："通话记录与聊天流**彻底分离**…
**不进聊天页**"，本地双工达妮娅侧的注释又重复了一遍——**用户说的话被混进聊天页正是它要消除的
那个 bug**（10-02 之前用户说的话被当达妮娅的话显示在聊天里）。实测（桩 self 驱动真 handler）：

```
打字消息            : 进聊天页=1 落盘=1
双工通话·用户说的话  : 进聊天页=1 落盘=1   ← 泄漏
```

**修法**：两处都补 `source=="duplex"` 闸门。数据不丢——`_publish_call_record` 四个发布点
（本地双工用户侧/达妮娅侧/云端）都还在发 CALL_SPEECH，`shared_history(source="duplex")` 也有一份。

顺带抓到同源第三处：`activity_reporter._on_user_text` 把**主动搭话**也记成 `direction="user"`
——那是**她说的**，而且 `_on_ai_decision` 已经按 `direction="ai_proactive"` 记过一次了
（既错标又重复）。对齐 AI 侧的做法：主动搭话不在此记。

**验证**：新增场景 13，正反两路四种来源（打字/语音转写要进，双工/主动搭话要挡），并断言
**落盘数与上屏同构**；另断言"通话记录发布点仍在"，防止"滤掉聊天页"变成"丢数据"。
测试扩到 **95 项断言**；变异**十二刀 → 十五刀**（T12 聊天页收回双工、T13 活动日志收回双工、
T14 主动搭话不错标）。

变异工具随之泛化：新增 `EXTERNAL_KNIVES`（按刀指定目标文件/包名 + harness 注入点 + 变异体类名），
`load_patched` 支持跨目录，`_scenario_fails` 支持三类注入点。harness 相应提供
`chat_sink_cls()` / `activity_reporter_cls()` 两个延迟绑定函数——**测试取被测类必须走注入点**，
这条 T9 已经栽过一次，这次一开始就照做。


### 2026-11-08 · 批量修复：交付包质检发现 + 解释器守卫（PR #2 ~ #6）

**本批 5 个 PR 来自交付包独立质检（本机工作区），源码仓无 `.git`，走 API 逐个开分支/提交/开 PR。**

---

#### PR #2 · doctor 三处「只看存在不看状态」的假红

**问题**：`tools/doctor.py` 三处检测用了存在性判据而非功能性判据——
① `find_spec("pypinyin")` 不为 None 即判 PASS，但包内 pypinyin 目录无 `__init__.py` 时
find_spec 返回 None → 误报「唤醒词退化」；
② WebView2 注册表键存在即 PASS，不读版本号 → 空/损坏键也 PASS；
③ UCRT 检测只看 `api-ms-win-crt-*.dll` 文件存在，不 `LoadLibrary` 验证 → 损坏 DLL 也 PASS。

**改动**：
- pypinyin 检测改为 `find_spec()` + `import pypinyin` 双重验证（import 失败即 FAIL）
- WebView2 检测补版本号非空检查
- UCRT 检测补 `ctypes.WinDLL` 试加载

**验证**：三处变异验证——损坏 WebView2 键 / LoadLibrary 失败 / pypinyin 目录无 init →
改前 PASS、改后 FAIL；还原后 doctor 全绿。

---

#### PR #3 · text_chat_router 空 Key 模板端口误报 WARNING

**问题**：路由器对 `model.api_key` 做真实性检查，但交付包出厂模板里 `${ENV_VAR}` 引用
展开后是空字符串。空 Key 是预期初始态（首启不问 Key 的设计），却被当成异常按 WARNING
打日志 → 首启 doctor 多一条误导告警。

**改动**：Key 为空时按 DEBUG 打、附说明「首次使用需在设置里填入 Key」，
不触发 WARNING 路径。

**验证**：Key='' → 改前 WARNING 改后 DEBUG；Key='sk-xxx' → 两版均正常 INFO。

---

#### PR #4 · webapp get_quota_status 指向不存在的 quota_guard

**问题**：`src/ui/webapp.py:7729` 在 `try/except Exception` 里 `import src.ui.quota_guard`
做 API 额度角标数据源，但全仓无 `src/ui/quota_guard.py` → import 恒走 except，
额度角标永远静默不显示，用户看不到剩余调用次数。

**改动**：try 块改为直调 `self._get_quota_from_config()` 从 config 读，不依赖缺件。

**验证**：删掉 quota_guard.py 后改前恒 except、改后正常返回 config 数据。

---

#### PR #5 · 新增交付包发布前闸门 `tools/check_delivery.py`

**问题**：本机交付包无 `.git`，改动不能走 git 闭环，需要独立发布前闸门防漏件。

**改动**：七层判据 + 32 个变异场景验证（含 `--root` 端到端）+ 可选 `--smoke` 真跑包内
解释器做 import 冒烟。

**验证**：32 个变异全红 + 4 个按预期 WARN 不误报；交付包实测 8 PASS / 1 WARN / 0 FAIL。

---

#### PR #6 · main.py 解释器守卫

**问题**：交付包用 `python/python.exe` 跑 doctor 得 16 PASS / 3 WARN / 0 FAIL；
用系统 python 跑得 10 PASS / 5 WARN / 4 FAIL，全是「用错解释器」的假象。
包内 python 不在系统 PATH，用户极易用系统 python 触发假象。

**改动**：`main.py` 的 `--doctor` 截获块加前置检测，交付包存在但当前解释器不是它时
打醒目提示，不阻止运行、不改退出码。源码仓无 `python/` 目录 → 零干扰。

**验证**：交付包双向实测——系统 python → 提示 + 4 个误导 FAIL；
包内 python → 无提示、干净 16-3-0；check_delivery --smoke 8 PASS / 0 FAIL 无回归。

---

#### PR #7 · create_notify_window 329 行不可达代码墓碑

**问题**：`src/ui/main_window.py` 的 `create_notify_window()` 里，try 和 except 两条分支都
`return`，其后的 329 行 WebView2 旧实现（`import ctypes` / `_notify_geom` 调用链 / WebView2
窗口创建逻辑）永远执行不到。这段代码占着位置、误导维护以为那是活跃路径，任何改动都是
白费功夫，且 `tools/check_all.py` 里的某些扫描会命中它、误以为有覆盖。

**改动**：删掉 return 之后的整段死代码，函数只保留「调 native_notify.ensure_notify_window()
并返回结果」的两行（try/except 结构）。新增回归 `src/tests/test_notify_dead_path.py`：

- 断言 create_notify_window 真正调到 native_notify.ensure_notify_window()
- 断言 return 后无残留代码（AST 检查：函数体在 return 后无 stmt）
- 断言 try/except 两分支的 return 值类型一致（都是 Optional[Window]）

新增变异刀 `tools/_qc_notify_dead_path_mutation.py` 4 刀：

- try 分支提前 return（模拟死代码复活）
- except 分支吞掉 return
- 删掉调 native_notify（stub 掉 ensure_notify_window）
- 改坏 return 值（返回非 Optional[Window] 类型）

**验证**：变异 4 刀全红 + 还原后回归 12/12 全绿；`tools/check_all.py` 登记对账 ✓；
CI 全绿（pytest / self-check scripts / syntax gate 三腿 success）。

#### PR #18（续十四）· 最厚的一道门三处空转，最狠的是「装完不验收」

**为什么挑它**：`打包依赖分级`（`src/tests/test_make_dist_deps.py`）1047 行、
六十来条断言，是全项目最厚的一道门，按「有没有刀」的读数是「没有刀」。

**它写得比大多数门都讲究。** [14] 那节甚至专门写了这句：

> 「先判 env 是否存在，再判内容：否则 `"PYTHONPATH" not in {}` 会恒真，
> 变异体（删掉 `env=`）下那两条照样打勾 —— **空洞通过比不通过更危险**。」

[20] 也写了「这两条是『别做过头』的护栏：静默删目录比留下垃圾更危险」。
所以这一轮不能假设它漏，只能一处一处去探。

##### 三处空转（全部实测：改完门仍 EXIT 0 全绿）

**① `[C10-6]` 断言的是文件系统，不是代码。**

```python
try:
    make_dist.resolve_sign_config({"DANIA_SIGN_SUBJECT": "X"})
    check(not os.path.isfile("signtool.exe"),
          "无 TOOL 且 PATH 无 signtool 时应抛 ValueError（本机没有裸 signtool.exe）")
except ValueError:
    check(True, "凭证配了但没工具 → ValueError")
```

没抛异常时，它去断言「当前目录没有 `signtool.exe`」——那是**文件系统**的事实。
把 `if not cfg["tool"] or not os.path.isfile(cfg["tool"]): raise` 改成 `if False:`，
门照样全绿：这个守卫从来没被测过。

**② `[C10-14]` 只 grep 错误文案。**

```python
check('"--sign"' in src_txt10 and "--sign 需要 --installer" in src_txt10, ...)
```

把守卫改成 `if False:`，提示原样留在源码里、门全绿，而 `--sign` 单跑已经不再报错。

**③ `install_deps` 末尾「装完验收」整段没有判据。**

```python
if verify:
    return verify_deps(out_dir, python_exe)
return True
```

换成 `return True` 全门全绿。**这是 [14] 那件事的镜像**：
「规矩没写进 `env=`，等于没写」——「验收没被调用，等于没验收」。
而 [14] 只**直接**测 `verify_deps` 本身，从没测过 `install_deps` 到底调不调它。

##### 判据先行：135 → 142

- `[C10-6]` 改成显式给一个不存在的 `DANIA_SIGN_TOOL`（任何机器上都必抛），
  直接钉住 `os.path.isfile(cfg["tool"])` 这半句；「本机完全没有工具」那一半
  改成先问 `find_signtool()` 再定判据，机器上装了 SDK 时如实打印「不适用，未验证」。
- `[C10-14]` 改成用 `ast` 找到「body 里含那句提示」的 `if`，直接验它的**条件**
  是否同时含 `sign` 与 `installer`。
- 新增 `[26]`：`verify=True` 必须调一次 `verify_deps`、拿到的是包内解释器路径、
  `verify=False` / `defer_verify=True` 不许调、验收不过必须让 `install_deps` 返回 False。

##### 十二刀

`tools/_qc_make_dist_mutation.py`：A 不查 signtool / B `--sign` 守卫失效 /
C 装完不验收 / D `_pth` 写 LF / E DLL 落 `DLLs/` / F 非 DLL 也搬 /
G `enable=False` 不删 / H `tier_specs` 永不 break / I purge 清到 `python/` /
J 指纹不含壳脚本 / K 档位静默降级 / L 不裁 locales。**12/12 全红**。

##### 一个仪器错误，留档

第一版探针按 `✗` 过滤判「有没有红」，结果七处全报「抓住了」——**全错**。
因为 `make_dist` 的负向场景自己会打印 `  ✗ ...`（「包内没有 python.exe」
「核心档安装失败」……），和本门 `check()` 的前缀一模一样。按 `✗` 过滤，
等于把「门在验证某个失败场景」当成「门失败了」。
改成本门自己的汇总行 `❌ N 项未通过` 才看对。

同一轮里还有两把刀**砍断了多行语句**（`if ...startswith(` 只替换了第一行），
留下悬空的续行 → `make_dist.py` SyntaxError → 门崩了。`_judged()` 正确地拒绝
把「崩了」算成「抓住了」，两把刀都报「没抓住」。改成整块替换才红。
这是第三次踩「崩了 ≠ 认出来了」，但这次是**刀自己**崩。

**验证**：门 **135 → 142 条**；十二刀 12/12 全红且各自点名；还原后 sha256
逐字节一致；空变异体检 **CERTIFIED**；`check_gate_registry` **236 项全部有着落**；
站点 7 PASS/0 FAIL（GATES 235→**236**）；K11 分层 portable 8 / dev 28 = **36**。

#### PR #18（续十三）· 测试里抄了一遍生产代码，然后测抄件

**这一轮的问题比「没有刀」更严重。** 前面十二轮都在还「判据没有牙」的债，
这一轮撞见的是**牙长在一副假牙上**。

`tools/test_duplex_stop_and_mic.py` 的 A/B/C 三段，函数注释明写：

```python
async def _dispatch(client):
    """复刻 proactive._stop_duplex_client 的分派核心。"""

def track(...):
    """复刻 _track_mic_level 的电平平滑…"""

def resolve_engine_ui(...):
    """复刻 get_role_voice_context 的 engine_ui 判定"""
```

三个「复刻」。生产实现被手抄进测试，然后测试**测这份抄件**。
`proactive._stop_duplex_client` 改成什么样，A 段都不会红 —— 它跑的是自己那份。

讽刺的是同一个文件的 D 段早就写对了：

> 「本场景直接对真实 CloudOmniRealtime 的看门狗方法做确定性断言（**不复制逻辑，
> 避免"假绿"——复刻逻辑两次写得一样不算测试**）」

**文件自己写下的规矩，被自己另外三段违反了。**

**改法**：三段全部改成调真实实现（`__new__` 绕过 `__init__`，只注入必要字段）。
B 段从「造几个假浮点数喂给抄件」改成「造 16-bit PCM 喂给 `_track_mic_level`」，
读它真算出来的 `_mic_level` / `_mic_disp`。实测复现出 BUG-2 的原始形状：

```
level=0.00375（< 0.004 阈值 → 判"没声音"）
disp =0.04501（> 0.004 → 判"刚才有人说话"）
```

同一个数，以前证明的是抄件的行为，现在证明的是生产代码的行为。

##### 换成真实现之后，逼出两件「注释在骗人」

**① A1 的括号是假的。** 原文：

```python
ok("A1 同步 stop() 被正确调用", c1.stopped is True,
   "(旧代码此处 await None → TypeError → stopped 恒 False)")
```

把生产代码回退成 BUG-1 原形 `await client.stop()`，探针跑出来：

```
== A 回退成 await client.stop()（BUG-1 原形） -> EXIT 0     ← 没抓住
```

A1/A2 **照样绿**。因为 `await f()` 必然先求值 `f()` —— `client.stop()` 执行了、
客户端真的停了，`stopped` 就是 True。那句「stopped 恒 False」是编的。

真正坏掉的是**之后**：原始 bug 里没有 try/except，异常从分派处逃逸，调用方
`self._duplex_client = None` 与收尾全被跳过 → 引用仍指着已停的客户端。
而现在 try/except 已经挡住了逃逸，**可观测的后果只剩一条日志**。

所以补 A5（ERROR 级 handler 挂在 `proactive.logger` 上，正常分派必须零异常日志）——
它是唯一能区分「分派写对了」和「分派回退了」的判据：

```
== A 回退成 await client.stop()（BUG-1 原形） -> EXIT 1
     [FAIL] A5 正常分派零异常日志 ['停止双工客户端失败（自测）']
```

`proactive.py` 里同一条假因果（docstring 写着「旧客户端从未停止」）本轮一并改正。
**一把刀逼出了一条假注释** —— 这和第三十四轮「自称两刀变异已验、仓库里一把刀
都没有」是同一类病：注释里的证据没人复核，就会一直错下去。

**② 有一行守卫，删掉也全绿。** `_watchdog_tick`：

```python
if self._capture_stalled(time.time()) and self._mic_proc is proc:
```

后半句是「换轮保护」——上一轮的看门狗线程别把新一轮的子进程杀掉。而 D5/D6 都是
`_mic_proc is proc` 的情形，D7 把判据桩成 `lambda now: False` 又不经过它。
**这条守卫一条判据都没有。** 补 D9：造出「看门狗手里拿着旧 proc、实例上已经是
新 proc」的真实竞态，断言两边都不许被杀。

##### 十刀

新刀 `tools/_qc_duplex_stop_mic_mutation.py`，跨三个生产文件：

| # | 文件 | 砍掉什么 | 红在哪 |
|---|---|---|---|
| A | `proactive.py` | 分派回退成 `await client.stop()` | A5 |
| B | `cloud_omni_realtime.py` | `_mic_disp` 退化成被稀释的 `_mic_level` | B2 |
| C | 同上 | 停滞判据 `>` 改成 `<` | D1 / D2 / D5 / D6 |
| D | 同上 | 停滞判据不再看 `running` | D3 |
| E | 同上 | 停滞判据不再看 `_mic_proc` | D4 |
| F | 同上 | 看门狗去掉换轮保护 | **D9** |
| G | 同上 | 检测到停滞也不杀 | D5 / D6 |
| H | 同上 | 杀完不清 `_mic_proc` 引用 | D6 |
| I | 同上 | 只清引用不真 kill | D5 / D8 |
| J | `webapp.py` | 出声方式只读静态 config（BUG-3 原形） | C1 / C2 / C4 |

**验证**：门本身 21 → **22 条**（A5、B4、D9 三条是本轮新补的判据）；十刀
**10/10 全红**且各自点名；还原后 sha256 逐字节一致；空变异体检 **CERTIFIED**；
`check_gate_registry` **235 项全部有着落**；站点 7 PASS/0 FAIL（GATES 234→**235**）；
K11 分层 portable 8 / dev 27 = **35**、0 处异常。

**留档的教训**：判据里出现「复刻」「模拟实现」「参照实现」这类词，就该警觉。
参照实现必须是**独立的**（RPC 那轮已经踩过一次：拿被测的 `_resolve` 当参照，
它坏了两边一起坏，等价性永远成立），但**被测对象本身绝不能是参照**。

#### PR #18（续十二）· 15 节、四十来条断言的「最扎实」那道门，有一整条支路没有判据

**为什么挑它**：壳层最后一道没刀的门，也是断言最多的一道 —— 15 节、47 条。
按「有没有刀」的读数是「没刀」，但真正的问题不是没刀，是**有一整条支路没人测**。

**五处空转**（删掉后自测照样全绿 exit 0，全部实测）：

| # | 砍掉什么 | 后果 |
|---|---|---|
| 1 | `_try_restart` 里 `if was_top: self.on_top = True` | 重启后不再置顶 |
| 2 | `_cleanup_proc` 里 `elif force: _kill_tree(proc.pid)` | **孤儿 Electron 窗口滞留桌面** |
| 3 | 活进程 `wait(5)` 超时后的 `_kill_tree(pid)` | 渲染进程/GPU 进程留下残影 |
| 4 | `if len(self._restarts) > 1: time.sleep(RESTART_BACKOFF)` | 连续崩时把 CPU 打满 |
| 5 | `close()` 里 `self._sock.sendall(b"quit\n")` | 先礼后兵变直接强杀 |

第 1 条尤其刺眼。原文件 [1] 里写着：

```python
calls = {"launch": 0, "show": 0, "top": 0}
```

`launch` 和 `show` 都被断言了，**`top` 从头到尾没被 ++ 过，也没有任何断言读它**。
想测的东西忘了写，但字典留了下来 —— 下一个人读到这行会以为「置顶也测过了」。

第 2 条是模块注释里点名的那个坑：

> 为什么不能直接 terminate：Electron 是多进程架构，只杀主进程常常留下渲染进程/GPU
> 进程，桌宠的残影会留在桌面上，且下次启动会撞上 userData 单实例限制
> （Electron 会**无输出地立刻退出**，极难排查）。

而测试里 `_FakeProc()` 的 `pid` 是 `0`，`_kill_tree(0)` 是个空操作 —— 于是
「主进程已退、子进程还在」这条分支**跑过，但什么都没验证**。

**为什么 15 节还是漏**：节数多不等于覆盖面广。15 节里 9 节在测「配额 / 降级 /
握手文件」，五处空转全在「进程回收」和「重启时恢复状态」这两条支路上。

> **分支比断言更值得数。** 一道门有 47 条断言，但只走 3 条支路，那它测的是 3 件事。

**改动**

1. 判据 47 → 57：
   - `_TopProbe`（`on_top` 换成可数探针）+ `_RecSock`（记录 `sendall`）+ `_LiveProc`
     （活着但 `wait` 永远超时）；
   - [1] 断言恢复置顶；新增 [1b] 断言「生前没置顶就不碰 `on_top`」（反方向）；
   - [6] 加三条杀进程树分支：`force=True` 必杀、`force=False` **不越权杀**、
     活进程先 `terminate` 再升级强杀；
   - [11] 加 `quit` 与 socket 关闭；
   - [16] 加退避：第一次不白等、第二次起 ≥ `RESTART_BACKOFF`（只断言下界）。
2. 新刀八把：A 不恢复置顶 / B 不杀子进程树 / C 不升级强杀 / D 不退避 /
   E 不发 quit / F 配额差一 / G 拿掉整条守卫 / H 窗口不滚动。

**八刀全红**，B 连坐 3 项、F 连坐 11 项、G 连坐 2 项，还原后 sha256 一致。

**一处「写不出刀」的记录**：G 的第一版只砍 `if self._closed or self._wd_stop.is_set()`
里的 `self._closed` 那一半 —— **没红**。因为 `close()` 会顺手 `_stop_watchdog()`，
`_wd_stop` 也是置位的，两个条件互为备份。也就是说 `_closed` 这一半是**冗余**的
（留着无害，防将来有人单独置位），单独砍它测不出来。改成砍整条守卫才红 ——
**那才是「已关闭的壳不会被复活」这个判断的真身**。

按本项目的规矩：写不出刀时第一反应是查判据或实现，不是换一把更容易红的变异。
这一次查出来的是「一行里有两个互为备份的条件」，属于实现层面的事实，不是判据缺陷。

**验证**

- 断言 **47 → 57**，起点绿
- 八刀 8/8 全红；还原字节一致
- 空变异体检 `--tier all --only pet_shell` → **CERTIFIED**
- `check_gate_registry` 234 项全部有着落；站点 7 PASS / 0 FAIL（GATES 233 → 234）
- `_qc_knife_vacuity_mutation` 13 项全 PASS，K11 `portable 8 / dev 26`，0 处异常
- 全量 `check_all.py` 见文末

**GATES 233 → 234**；判据门 160 ｜ 有刀 **70** ｜ 没刀 **90**。

壳层三道没刀的门到这一轮全部补完。下一步按 `质检基线` §8 的优先级回到
`必需资产清单` / `陌生人首启判据链` / `源码包排除规则`。
#### PR #18（续十一）· 这条通道坏了不会报错，只会「照样返回 200，返回的东西不对」

**为什么挑它**：壳层还剩三道没刀的门，`RPC 通道` 是其中失效形状最阴的一道。
`WS 端口绑定` 坏了是「起不来」，用户立刻知道；这条通道坏了是**照样 200**，
前端拿到一个格式对、内容错的结果，或者更糟 —— **私有方法能调到**。

它守的是壳 ↔ 后端之间那条没有类型检查的 HTTP 通道（`src/ui/rpc_server.py`）：
19 条等价性用例（RPC 结果必须等于直调结果）+ 三条安全边界（只绑回环、缺令牌拒绝、
内部方法不可调用）+ 协议形态 + 长连接卫生 + 关闭。

**读判据，读到两处空转**（实测，不是推测）：

| 变异 | 自测结果 | 说明 |
|---|---|---|
| `all(_is_exposed(p) for p in method.split("."))` → `_is_exposed(method)` | **21 项全绿 / exit 0** | 逐段检查退化成整串检查 |
| `_resolve` 换成 `getattr(api, dotted, None)` | **21 项全绿 / exit 0** | 点号支持整个删掉 |

第一条不是「检查变松了」这么轻。整串检查下 `sub._secret` 不以 `_` 开头，
于是**校验通过**，`_resolve` 按点号走下去就拿到了私有方法 —— 这是**点号绕过**，
不是漏报。

第二条更荒唐：`_resolve` 的注释白纸黑字写着

```python
"""按点号解析成员（与 pywebview 的 get_nested_attribute 一致，支持子对象）。"""
```

而 19 条等价性用例里**没有一条带点**。

**根因只有一句**：判据只覆盖了「退化不产生差别的那个输入域」。单段名字上
「逐段检查」与「整串检查」完全等价，「点号循环跑一轮」与「直接 `getattr`」
完全等价 —— 于是两种退化都能全绿通过。

**改动**

1. 判据先行，运行时断言 21 → 26：
   - `FakeAPI` 加子对象 `sub`（`ping` 正常、`_secret` 私有、`a__b` 名字中间带 dunder）；
   - 等价性加 `sub.ping` —— 19 条用例里唯一带点的一条；
   - 安全检查加四段：`sub._secret` 必须 403 **且响应体里不能有私有返回值**、
     `sub.no_such` 404、`sub..ping` 403（空段）、`sub.a__b` 403（中间 dunder）。
2. 参照实现也换了。原来直调是 `getattr(api, method)(...)`，遇到点号会
   AttributeError。改成 `_direct_call()` 自己走一遍点号 —— **不能拿 `_resolve`
   当参照**：它坏了参照一起坏，等价性永远成立。这是**故意**重复的一份逻辑。
3. 新刀 `tools/_qc_rpc_mutation.py`，十刀：

| 刀 | 砍什么 | 抓住它的判据 |
|---|---|---|
| A | 令牌校验恒真 | 缺令牌 / 错令牌 / 未授权（3 条） |
| B | 逐段检查 → 整串检查 | 点号私有段 / 不泄漏 / 空段（3 条） |
| C | `_resolve` 不支持点号 | 20 个方法结果一致（1 条） |
| D | 拒绝分支不排空请求体 | 401 / 413 / 404 之后同一连接仍可用（3 条） |
| E | 去掉 `MAX_BODY` 上限 | 超大请求体（2 条） |
| F | 不可 JSON 化不再标记 `degraded` | 降级断言（1 条） |
| G | `stop()` 空操作 | 端口已关闭（1 条） |
| H | 业务异常改回 200 | 500 + 异常类型（1 条） |
| I | 去掉中间 dunder 限制 | `sub.a__b` 403（1 条） |
| J | 去掉空段限制 | `sub..ping` 403（1 条） |

**10/10 全红**，逐条点名，还原后 sha256 一致，空变异体检 CERTIFIED。

**这一轮没有踩新坑，但有一处值得记**：I 刀和 J 刀砍的是同一行
（`_is_exposed` 的两个条件）。同一行两把刀不算「第二份真相」—— 它们打中的是
两条不同的判据（中间 dunder / 空段），去掉任何一个条件，只有对应的那条会红。
**判据与刀是两条不同的线：一刀可以砍多行，一行也可以被多刀砍。**

**验证**

- 运行时断言 **21 → 26**（`check()` 调用点 19 → 24），起点绿
- 十刀 10/10 全红；还原字节一致
- 空变异体检 `--tier all --only rpc` → **CERTIFIED**
- `check_gate_registry` 233 项全部有着落；站点 7 PASS / 0 FAIL（GATES 232 → 233）
- `_qc_knife_vacuity_mutation` 13 项全 PASS，K11 `portable 8 / dev 25`，0 处异常
- 全量 `check_all.py` 见文末

**GATES 232 → 233**；判据门 160 ｜ 有刀 **69** ｜ 没刀 **91**。

壳层现在只剩 `壳自愈与退出清理`（`src/tests/test_pet_shell_selfheal.py`，424 行）
一道没刀。
#### PR #18（续十）· 这道门自称「两刀变异已验」，而仓库里一把刀都没有

**发现的方式**：上两轮把壳层两道静态门（`check_async_blocking` / `check_await_sync`）
补了刀之后，顺手 `ls tools/_qc_*ws*` —— 返回 `No such file`。而 `tools/check_all.py`
里 `WS 端口绑定` 上面那段注释写得很笃定：

```
# WS 端口绑定契约（2026-10-20 03:11 启动崩溃的回归）：空闲一次起、被占先重试
# 再报人话（PID/退出指引）、占用探测不扰民。两刀变异已验：删重试→「确有重试」
# 红；改回裸 raise→三条诊断断言红。
```

刀是真的跑过（描述里的两处红都属实），但**没提交**。手工验过一次的刀，在
下一次重构时一文不值：它不会在 CI 里跑，也不会在别人改坏 `ws_server.py` 时叫。
`check_knife_vacuity.py` 的整张表就是为了消灭这种「我记得验过」，而这里恰好是
它的盲区 —— 表里没有的刀，工具不会去找。

**读判据，读到两处空转**（先跑实验，再动刀，两处都是实测不是推测）：

1. 把 `logger.warning(...)` 整段删掉 → 自测**照样全绿 exit 0**。
   「先重试再报错」里的「重试」在日志上毫无痕迹：用户事后翻日志只看到最后一次
   错误，看不到「试过 6 次」。而这段日志正是「03:11 那次到底为什么崩」的唯一线索。
2. 把 `serve()` 提到循环外 —— **只真的 bind 一次**，然后空睡 5 次、再抛一模一样
   的错 —— 自测**照样全绿**。`check(dt >= _BIND_ATTEMPTS - 1 - 0.5)` 量的是
   **等待时长**，不是**重绑次数**；空睡 5.0s ≥ 4.5s，稳稳过关。

第 2 条是致命的。2026-10-20 那次崩溃的形状不是「端口一直被占」，而是
**旧实例正在退出、1~2 秒后释放**。空睡 5 秒对这个 bug 一点用都没有，而闸门
分不出它和真修复的区别。原文件只有两端（一直空闲 / 一直被占），中间那段空白。

**改动**

1. 判据先行，13 条 → 19 条：
   - 契约 2 补日志断言：`每次重试都留了日志（5 条 ≥ 5）` + `重试日志带端口号 + 「重试」字样`；
   - 新增契约 4 `test_retry_then_success`：占住端口，1.2s 后用
     `threading.Timer` 关掉监听 socket 模拟旧实例退出，断言 `start()` **成功起来**、
     且确实经历过重试 warning、且没走满 6 次预算（2.0s < 5s）。
2. `_WarnCatcher` 改挂**模块自己的 logger 对象**（`from src.ws_server import logger`），
   不再挂 `logging.getLogger("src.ws_server")` 这个字符串名 —— 名字一改，判据就
   静默变空转，「零 warning」永远是绿的，因为根本没人往这个 handler 里塞东西。
3. 顺手修一个正文缺陷：报错串里 `str(last_err).rstrip("。")` 把 OSError 末尾的
   句号吃掉却不再补，拼出来是「…只允许使用一次**若这是**第二个达妮娅实例…」。
   崩溃弹窗里就是这么一整句读不通的话。改成 `rstrip("。") + "。"`。
4. 新刀 `tools/_qc_ws_port_bind_mutation.py`，八刀：
   A 只 bind 一次（删重试循环）/ B 重试但不等待（`sleep(1.0)`→`sleep(0)`）/
   C 改回裸 `raise last_err` / D `_describe_port_holder` 恒空串 /
   E 空闲时也开嘴（误报噪声）/ F 删掉 `logger.warning` / G `serve()` 提出循环
   只 bind 一次空睡 5 次 / H 吃掉补回的句号。
5. 登记：`GATES` 231 → **232**（`WS 端口绑定变异`，`slow=True`）；`PAIRS` 加一对；
   `_qc_knife_vacuity_mutation.py` K11 额度 `dev 23 → 24`、总数 `31 → 32`。

**两个坑，都是这一轮现踩的**

① **第二份真相**。第一版的句读断言写的是 `"。若" in msg`。H 刀（吃掉句号）跑出来
**没红** —— 因为 `holder` 自己就带句号（「端口 18794 正被进程 PID=… 占用。」），
它拼在「若这是」前面，于是整条消息**永远**有「。若」，那条断言恒真。
改成把 last_err 那一段单独切出来（`msg.split("次）：")[1].split("端口 18794 正被")[0]`）
再判 `endswith("。")`，H 刀才红。**一条判据只要能因为别的原因通过，它就没在判它
以为在判的东西。**

② **崩了 ≠ 认出来了**（本轮第二次撞到）。H 刀第一版写成
`str(last_err).rstrip("。")  # 变异刀：句号不补了` —— 行尾注释把后面的 `, holder)`
一起吞掉，直接 SyntaxError。套件报「没抓住（测试失效）」，而 `_judged()` 拒绝把
崩溃算成抓住，于是它以「有 1 处异常」收场。这个判定是对的：**换成 `+ ""` 之后
它才是「语法正确、行为退回旧 bug」的刀**。工具没有把崩溃当成功，这一点值得记。

**验证**

- 自测 19 项全 PASS（13 → 19，新增 6 条），起点绿。
- 八刀 8/8 全红，逐条点名：
  A →「确有重试 / 每次重试都留了日志 / 端口中途释放后 start() 成功」3 项；
  B → 2 项；C → 4 项（PID / 托盘指引 / data/ / 句读）；D → 2 项；E → 1 项；
  F → 3 项；**G → 1 项（正是本轮新加的那条判据）**；H → 1 项（句读）。
- 还原字节一致（sha256 前后相同）。
- 空变异体检 `check_knife_vacuity.py --tier all --only ws_port_bind` → **CERTIFIED**
  （D0 原样 exit 0 / D1 闸门≡0 exit 1 且自行报失败 / D2 闸门≡1 exit 1 且自行报失败）。
- `check_gate_registry` 232 项全部有着落；`check_site_counts` 7 PASS / 0 FAIL
  （`GATES` 231 → 232 回填 `site/index.html` 与 `site/PUBLISH.md`）。
- `_qc_knife_vacuity_mutation` 13 项全 PASS，`K11 分层：portable 8 / dev 24`，0 处异常。

**这道门以前从没被变异验过，现在它守得住自己声称守住的东西** —— 包括那条
「重试」到底是「等了 5 秒」还是「又 bind 了 5 次」。

#### PR #18（续九）· 两道闸门自己写着「只会打印不会判红，比没有更危险」，而它们的自测正好漏了这一条

**选这两道门的理由**不是覆盖率排名，而是它们的失效形状。壳层那组闸门的共同点是
**坏掉不报错，只是卡住**：

- `check_async_blocking.py` 守「async 函数里做同步调用」——事件循环被占住，
  症状是"卡一下"，不报错、不告警。2026-10-18 一天撞到四处（gewechat 回发 /
  qzone 发布 / tts 的 `time.sleep(0.6)` / heavy_apps 的 `urlopen(timeout=5)`）。
- `check_await_sync.py` 守「`await` 一个同步方法」——`await None` 抛 TypeError，
  被事件总线 `except` 吞掉，功能静默失效。真实事故：`gewechat._on_decision` 里
  `await self._safe_post(...)`，**微信回复从来没真正发出去过**，症状像"AI 不理人"。

两道都自带自测、七条正反例，看上去是这一批里最不需要担心的。

**但自测只测 `scan()`。** 两道门的自测都是这个形状：

```python
hits = C.scan(p)          # ← 七条全部只走这里
ok = len(hits) == want
```

而真正决定闸门会不会判红的，是 `main()` 的最后一行：

```python
return 1 if all_hits else 0
```

这条路径**没有任何判据**。实测（不是推测）：把它改成 `return 0`，

- 闸门退化成「只打印不判红」：可疑点一个不少地打印出来，但退出码永远是 0，
  全量里永远绿；
- 而自测**照样打印「全部通过 ✅」并 exit 0**。

两个文件都中。讽刺的是 `test_check_async_blocking.py` 第 3 行自己写着：

> 闸门如果只会打印不会判红，比没有更危险（本项目吃过"文本编码闸门永远 exit 0"的亏）。

这句话守住了 `scan()`，却没守住 `main()` —— 它描述的正是它自己漏掉的那个洞。

**先补判据，再加刀。** 两个自测各加两条断言：

```python
("有可疑点 → 1", "import time\nasync def f():\n    time.sleep(1)\n", 1),
("干净 → 0",   "import time\ndef f():\n    time.sleep(1)\n", 0),
```

做法是建临时目录、写一个文件、调 `C.main([d])` 拿退出码（`main` 会打印，用
`contextlib.redirect_stdout` 收走）。补完先验证它能开口：把退出码改回恒 0，
两个自测都从「全部通过 ✅」变成「❌ 1 项未通过」。**验证过才动刀。**

**补刀时撞出一个真缺陷。** 写 B 刀（取消 `to_thread` 豁免）时它没红，查下去发现
`WRAPPERS` 那段逻辑是这样的：

```python
if nm and nm[1] in WRAPPERS:          # to_thread / run_in_executor
    for a in n.args:                   # 把**实参里**的调用标成 safe
        ...safe.add(id(c))
```

两件事同时成立：

1. 对规范用法它**多余**——`to_thread(time.sleep, 1)` 传的是函数引用，`time.sleep`
   是一个 `ast.Attribute`，根本没有 `Call` 节点可标；
2. 对错误用法它**有害**——`to_thread(urlopen(...))` 的实参是在**事件循环里先求值**的，
   那段阻塞已经发生了，豁免它等于把真阻塞标成安全。

实测：

```
await asyncio.to_thread(urllib.request.urlopen("http://x").read())
→ 修复前：可疑点 0 处
```

这正是 `heavy_apps.pull_cloud` 卡首启 5s 的形状。删掉豁免，补一条正例
（`to_thread 实参里的同步调用（真阻塞，必须报）`），再确认规范用法不被误伤：
全仓 `src` 命中 0 → 0，七条旧用例全绿。刀 B 改成「把豁免加回去」，它现在红了。

**十刀。**

| 门 | 刀 | 变异 | 被哪条判据点名 |
|---|---|---|---|
| 异步阻塞 | A | `return 1 if all_hits else 0` → `return 0` | main() 退出码 / 有可疑点 → 1 |
| 异步阻塞 | B | 恢复 `to_thread` 实参豁免 | to_thread 实参里的同步调用（真阻塞，必须报） |
| 异步阻塞 | C | 下钻嵌套 def（把 to_thread 的 worker 也查） | 嵌套 worker + to_thread（正常） |
| 异步阻塞 | D | 阻塞表里删掉 `time.sleep` | async 里 time.sleep |
| 异步阻塞 | E | 同步函数也一起扫 | 嵌套 worker / 同步函数里 sleep / main() 退出码（3 项） |
| 协程调度 | A | `return 1 if all_hits else 0` → `return 0` | main() 退出码 / 有可疑点 → 1 |
| 协程调度 | B | 消费者表删掉 `create_task` / `run` / `gather` | create_task 调度 / asyncio.run 消费 |
| 协程调度 | C | 规则 1 失效（`await` 同步方法不再报） | await 同步方法 |
| 协程调度 | D | 规则 2 失效（async 没调度不再报） | async 没调度 |
| 协程调度 | E | 不再排除异步生成器 | 异步生成器 async for（正常） |

C 和 E 这两类方向是**误报**方向，不是漏报。误报比漏报更快杀死一道闸门 ——
所有人开始无视它，然后它就真的没用了。所以刀不能只砍「检测变弱」那一侧。

**验证。** 协程调度自测 9/9、异步阻塞自测 10/10；两把刀 5/5、5/5 全红且各自点名；
空变异体检均 CERTIFIED；`check_gate_registry` 231 项全部有着落、无重复登记；
站点 7 PASS / 0 FAIL；四步（两道门的判据 + 两把刀）接进 CI `portable-gates` ——
纯 stdlib、只读 AST，各约 1s，没有理由只在本机守。

**空转体检自己抓到了这两把新刀。** `check_knife_vacuity.py` 有一条硬约束：声明了
判定文件的刀必须进 `PAIRS`，否则直接报「这些刀声明了判定文件，却既不在 PAIRS 里、
也没写不进的理由」。第一次全量跑就卡在这一步（0.1s 退出）—— 这不是意外，是那道门
按设计工作。加完两对：31 对，**CERTIFIED 30 / SKIP 1**，D0 原样 exit 0、
D1 判定≡0、D2 判定≡1 三向都过。

加完之后 CI 立刻又红了一次，红在 `_qc_knife_vacuity_mutation.py` 的 K11 —— 它把
「portable 8 对 / dev 21 对、合计 29」写死了。这不是它过时，是**额度对账**按设计工作：
加了对却忘了改额度，说明你可能是「顺手加的」而不是「想清楚了」。同步改成
portable 8 / dev 23、合计 31，K11 重新 PASS（`portable 8 / dev 23，互不混入`）。

顺带记一条：第一次全量跑在 `变异刀空转体检` 上红了之后，我改的是 `PAIRS`（不是那道门
测的东西），所以**上一轮 224 个绿点的结论已经作废** —— 必须重跑整轮，不能只重跑
红的那一道。只重跑红的，等于默认「别的门在这次改动里不受影响」，而这个默认没有依据。

#### PR #18（续八）· 同一类越界，三个兄弟函数只测了一个

**怎么发现的。** 上一轮补完 `投影层自检` 之后，按盘点表继续还债，轮到产品关键路径
上的三条安全边界：工作区边界、命令守卫、权限模式。打开 `tools/test_workspace_guard.py`
准备给它补刀时先看它守什么 —— 六节里第 6 节写着「越界时**不写文件**（本轮修的核心）」，
守的是 2026-10 那个 bug：越界路径被**悄悄改名塞进沙箱**，还回报「已写入」，用户以为
改的是 `../那个文件`。

顺手搜了一下另外两个兄弟函数：

```
grep -rn "_append_file\|_read_file" tools/*.py
→ （无输出）
```

`_read_file` 和 `_append_file` 在**整个 `tools/` 目录下出现 0 次**。三个函数是同一段
`_safe_path(name)` + `except WorkspaceEscape as e: return str(e)` 的三份复制粘贴，
但只有 `_write_file` 有判据。

**为什么这条比「覆盖率 42%」更值得先修。** 覆盖率是个平均数，平均数会盖住分布。
这三个兄弟的失效后果完全不对称：

| 函数 | 越界不拦的后果 | 有判据？ |
|---|---|---|
| `_write_file` | 脏数据：用户以为改了 A，实际改了 B | 有（第 6 节） |
| `_append_file` | 同上，且是**追加**——把内容混进一个不该动的文件 | **无** |
| `_read_file` | **任意文件读取**：密钥盒、`config.yaml`（用户 API Key 全在里面）、导出的凭据，全部能进模型上下文 | **无** |

写坏一个文件是事故，读穿一次是**泄露**，而且读穿的痕迹比写坏小得多：没有文件被改，
没有任何报错，只有上下文里多了一段本该看不见的东西。

**先有判据，才有刀。** 第 6b 节的第一条断言不是「读越界被拒」，而是先**造一个真存在、
真可读的区外文件**：

```python
secret = os.path.join(outside, "secret.txt")
with open(secret, "w", encoding="utf-8") as f:
    f.write("TOPSECRET-PAYLOAD")
```

不造这个文件的话，「读不到」可能只是因为文件不存在 —— 判据会在实现完全坏掉的情况下
照样绿。这和本仓「先有判据，才有刀」是同一条纪律的另一面：**判据必须能开口，而
「开口」要求被测的那条路本来是走得通的**。

四条断言：返回里说明越界 / 没把区外内容读进来 / 区外文件没有被创建 /
沙箱里也没多出这个文件。最后一条专门钉 2026-10 那个 bug 在 append 分支上的同形复制。

**刀。** `工作区边界` 从三刀到六刀：

| 刀 | 变异 | 被哪条判据点名 |
|---|---|---|
| D | `_write_file` 的 except 分支改成改名写进沙箱 | 沙箱里也没多出这个文件 |
| E | `_read_file` 的 except 分支改成拼到工作区再读一次 | 没把区外内容读进来 |
| F | `_append_file` 的 except 分支改成改名写进沙箱 | 沙箱里也没多出这个文件 |

`命令守卫` 从三刀到五刀。原来三刀全在**前置检查**上（cwd / 白名单为空 / 元字符），
没有一把碰真正执行的那一行：

| 刀 | 变异 | 被哪条判据点名 |
|---|---|---|
| D | `if not ctx.get("allow_cmd"):` → `if False:` | allow_cmd=false 拒绝 |
| E | `shell=False` → `shell=True` | shell=False :: True |

D 是「没配就等于全开」的翻版，但入口不同：`allow_cmd` 是**模型自报**的开关，
原判据是 fail-closed（缺省即拒）。改成只在显式 false 时拒，任何没配白名单的会话
都会直接落到后面的白名单检查 —— 白名单里恰好有 `echo`，就变成「模型说可以就可以」。

**一个被自己否掉的做法（记下来，免得下次再走）。** 这两条判据的第一版，是新建
`tools/_qc_workspace_guard_mutation.py`（四刀）和 `tools/_qc_run_command_guard_mutation.py`
（五刀），跑出来 4/4、5/5 全红，空变异体检 CERTIFIED，看上去完全合格。

问题在于：仓库里**已经有** `_qc_workspace_mutation.py` 和 `_qc_run_command_mutation.py`，
砍的是同一条判据。两把刀同时存在不会让判据更硬，只会：① 让 CI 时间翻倍；
② 给「覆盖率」造出第二份真相 —— 以后统计「有多少道判据有刀」时，两个文件各算一次，
数字虚高；③ 谁改坏了其中一个，另一个还在，**红不红取决于哪把先跑**。
这正是 `catalog.py:98` 自己写的那句「不复制逻辑（第二份真相是 bug 温床）」。
删掉两个新文件，只把真正新增的三把（D/E/F、D/E）并进既有文件。

**顺带：跟进 master，解掉合并冲突。** 这轮开始时 master 还是 `b945b4ca`，推到一半
发现它前进到了 `c264700e`（中间合入了 89 个 PR），GitHub 已经把 PR #18 标成
`mergeable=false / dirty`。做了一次真合并（不是 rebase：冲突只解一次，历史保留），
5 处冲突三种判法：

1. `tools/check_delivery.py` + `tools/_qc_delivery_mutation.py` —— **取 master 侧**。
   两边**独立修了同一个 bug**（`check_template` 断言 `force_first_run is True`，
   方向写反）。master 的修法更新且更完整（多一条「出厂模板不许预置 `first_run_done`」），
   本分支那两处属于被取代 —— 同一判据留两份实现，就是又造一份第二真相。
2. `tools/check_all.py` —— **两边都留**。本分支在 `GATES` 尾巴加了 6 条，master 加了
   2 条（试听播放生命周期），互不相干，谁都不该被删。合并后 `len(GATES) = 229`。
3. `site/PUBLISH.md` + `site/index.html` —— **两边都不对**。纯数字锚点（227 vs 217），
   而合并后是 229。先取本侧再由 `check_site_counts.py --fix` 回填，锚点重新对账。

**验证。** `test_workspace_guard` 20 PASS / 0 FAIL（原 15）；六刀全红且各自点名，
还原后 sha256 逐字节一致；五刀全红；两把刀空变异体检均 CERTIFIED（起点绿，空变异
exit 1 分别报 6 处、5 处异常）；合并后 `check_gate_registry` 229 项全部有着落、
无重复登记；`check_site_counts` 7 PASS / 0 FAIL；PR 恢复 `mergeable=true / clean`。

#### PR #18（续七）· 刀指向了一条不存在的判据：`CALL_SITES` 是一份没人对账的第二真相

**为什么这一轮不修产品、只修仪器。** 前几轮的红点都来自「跑一遍全量」。全量已经
0 红了，可 0 红只说明**现有的判据没被违反**，不说明**判据本身有牙**。这两件事差得
很远：一道恒绿的判据，在全量里和一个真判据长得一模一样。所以本轮改做盘点。

**盘点方法**（试了三次才对）：`GATES` 是 `AnnAssign` 不是 `Assign`（第一版
`ast.Assign` 匹配到 0 条）；刀名 `_qc_X_mutation` 与门名 `check_X` 不同名，靠
归一化去配对会漏掉一半；最后改成**逐门扫 `tools/_qc_*.py` 正文里有没有出现该门的
脚本文件名**，得到：

    判据门 151 ｜ 有刀 63 ｜ 没刀 88（42%）
    非 lookalike 的其它闸门 6：字节码编译 / 前端死引用 / 壳自愈端到端 /
                              导入冒烟 / 陌生人首启判据链 / A5环境探针

88 道没刀的门里，本轮先挑 `投影层自检`：它在 2026-10-21 转绿后跑了两年多，
**从来没有刀**，而第三十轮刚动过它的入口（缺 `config.yaml` 就退到出厂模板）。
我动了判据的入口，就得证明牙还在 —— 这是本仓自己的规矩。

**头一版四把刀，两把是废刀。** M2 打算「`CALL_SITES` 删一项」、M3 打算「让两个 id
撞车」，跑出来是：

    FAIL M2 CALL_SITES 少一项（13 → 12）：锚点命中 0 次（应恰好 1 次），锚点已腐烂
    FAIL M3 CALL_SITES 两个 id 撞车：锚点命中 0 次（应恰好 1 次），锚点已腐烂

第一反应是锚点写错了 —— 确实写错了，真实源码里三个 id 挤在同一行
（`"brain.cascade", "brain.agent", "ocr.engine",`），我却按每行一个去写。
但修锚点时撞见了更麻烦的事：**改 `CALL_SITES` 根本不会让这道门变红。**

    CALL_SITES = ["chat.text", ..., "credentials.inventory"]   # __all__ 里导出
    def build_model_view(raw):
        sites.append({...})   # ← 13 段手写，逐个 append
        sites.append({...})

`CALL_SITES` 是「13 个调用点」的**第二份声明**，被 `__all__` 导出、被 UI 和文档引用，
可建视图的代码压根不读它。于是：删掉一项、或改成两个 id 撞车，视图照样 13 条、
`[OK] 13 个调用点齐全` 照样打印、门照样 exit 0 —— **声明与实现可以任意漂移，
没有任何人会发现**。那道「调用点数应为 13」的判据，守的从来不是 `CALL_SITES`。

这正是 `catalog.py` 第 98 行自己写的那句：

    # 6 槽位：**直接调** model_registry，不复制逻辑（第二份真相是 bug 温床）

它防住了槽位那一层，唯独漏了自己文件里的这个常量。

**修法分两步，顺序不能反：先有判据，才有刀。** 在闸门里补一条逐项对账
（`sorted(declared) == sorted(ids)`，重复也计入，不一致就点名两边各自的差集），
然后让 M5 刀去撞这条**新**判据。最终五刀：

| 刀 | 打哪条判据 | 期望点名 |
|---|---|---|
| M1 | 让 `build_model_view` 偷改 `raw` | 只读性 |
| M2 | 返回前 `sites = sites[:-1]`（13 → 12） | 调用点数应为 13 |
| M3 | 把 `credentials.inventory` 的 id 改成 `ocr.engine`（撞车） | id 重复 |
| M4 | `summary` 里 `in_slot` 凭空 +1 | summary 不自洽 |
| M5 | `CALL_SITES` 删一项（声明漂移） | CALL_SITES 与视图不一致 |

M2/M3 的锚点也一并换成**真正会跑的代码**（`ready_n = sum(...)` 之前、
`"id": "credentials.inventory"` 那一行），不再指向常量。

**两处仪器自身的坑，记下来。**

① 新刀首跑被空变异体检判 `SHAPE`：

    有 MUTS 但形状看不懂（分不清 old / new（没有两个可调用对象或两个 bytes））

五元组 `(标签, 目标文件, 原文, 替换, 关键词)` 里三项都是 str，工具没法靠类型猜。
按本仓约定补一行 `NOOP_SHAPE = ("idx", 2, 3)` 后 → `CERTIFIED`
（起点绿 / 空变异 exit 1 报 5 处异常）。**判 SHAPE 不算通过**，别当绿灯读。

② `check_knife_nowrite.py` 对这把刀报 `COVERED_ELSEWHERE` —— 有模块级 `MUTS`
的归空变异体检管，不重复算。三道体检的分工要在读数时心里有数，别把
「没跑」当成「跑了且通过」。

**留给以后的自己一句话**：锚点腐烂只是噪音，**刀指向一条不存在的判据**才是真问题。
前者报错很响（命中 0 次），后者悄无声息 —— 你会以为自己在验，其实在验空气。

**验证**：投影层自检 16/0 → **17/0**（新增对账一行 `CALL_SITES 声明与视图逐项一致（13 项）`）；
五刀全红且各自点名，还原后仍绿、sha256 逐字节一致；空变异体检 **CERTIFIED**；
`闸门退出码` 221 脚本 0 命中；`工具脚本导入路径` 33 PASS/0 FAIL；
登记对账 ✓（222）；站点 7 PASS/0 FAIL（GATES 221→**222**）。


**续：接着还债，先还「用户能看见的那一层」。** 88 道没刀的门里，`视频观看` 和
`视频进度` 排在最前面 —— 不是因为它们最核心，而是因为它们**刚被我动过**（第三十轮
修 `_resolve_ffmpeg` 的 mock），而且它们守的东西用户真的看得见：

- `视频观看` 是一条链：URL 白名单 → 抽帧 → 送 VLM → 拼上下文。任何一环断掉，
  现象都是「点开只会看个空壳」，而测试全绿。
- `视频进度` 是进度条：`watch_video` 发阶段 → `_make_video_progress` 转事件 →
  总线 `publish_threadsafe`。断在哪一环都是「进度条不动 / 一直转圈」，而回调异常
  被 `except` 吞掉、日志里看不出所以然。

六刀 + 五刀，**一次全红**（11 个变异，锚点都先在源码里验过命中恰好 1 次）。
这里有一个判据形态上的差异值得记下来：这两道判据的输出是 unittest 的
`FAILED (failures=1)`，不是本仓闸门惯用的 `[FAIL]` 行。所以 `_judged()` 换成
`rc != 0 and "FAILED (" in out` —— 但**语义没变**：崩溃（ImportError / Traceback）
同样 exit 1 却不打 `FAILED (`，仍然能把「判出来了」和「崩了」分开。
换形态不换语义，这是这条纪律的关键。


**再补一道：`抓屏性能闸`。** 它和前面两道不同 —— 它守的不是「功能对不对」，而是一条
**真炸过**的性能底线。2026-08-25 修掉的那个 bug：游戏或锁快照期间画面长时间静止，
`_next_interval` 的 static 加速会把采样间隔打到 ~1s，每帧全屏抓屏 + resize + pHash +
JPEG 编码，核显本直接被拖垮。27 条判据分 A/B/C/D/E/F 六组，**从来没有刀**。

五刀打 A/B/D 三组：

| 刀 | 变异 | 点名 |
|---|---|---|
| G1 | `low_rate` 档不再守低频底线（落回 static 加速） | A2 |
| G2 | 静态加速乘数被去掉（`*= 1.0`） | A3 |
| G3 | 主路径不再传 `low_rate`（云端接管时偷偷提速） | B1 |
| G4 | 让位分支不再传 `low_rate=True` | B3 |
| G5 | `main.py` 两处构造去掉 `low_rate_check` | D11 |

**G5 有一个别处没有的形状**：它的锚点 `low_rate_check=_game_check_or_locked,` 在
`main.py` 里**逐字出现两次**（1346 / 3214）。这不是巧合，而是 D11 判据的语义 ——
「**每一处** `ScreenCapture` 构造都要带 `low_rate_check`」，`all(...)` 少一处就红。
所以这把刀给 MUTS 加了一个「期望命中次数」字段：G5 写 2，其余写 1；
命中数不符一律报「锚点已腐烂」，**不硬改**。

这条比「锚点命中 0 次」更隐蔽：命中 2 次时如果按 `replace` 改掉两处，
判据红了、刀报 PASS，看着完全正常 —— 但如果那两处本来就该只改一处（比如
`_qc_models_layer_mutation` 的 M2），改两处就是**过度变异**，红的原因和刀想验的原因
不是同一件事。把期望次数写进数据，这类错误就没法悄悄发生。


**第四道：`密钥盒加密`。** 这道和前面三道性质不同 —— 它守的是**安全底线**：
磁盘上不许出现明文 Key。用户的 OpenAI / 百炼 Key、QQ cookie 全在 `config.yaml` 里。
20 条判据从 2026-10-18 起就在跑，**从来没有刀**。

这类判据的失效方式特别阴：加密函数照常返回、`Config.save` 照常写盘、日志照常打
「已加密」，只是密文里其实还是明文 —— 没有报错，只有某天有人 `cat config.yaml` 才看得见。

| 刀 | 变异 | 点名 |
|---|---|---|
| S1 | 密文不带 `dpapi:` 前缀（等于没加密） | 密文带 dpapi: 前缀 |
| S2 | `seal_tree` 不递归进嵌套 dict | 嵌套 dict 已加密 |
| S3 | 密钥字段表加宽（把 `name` 也当密钥） | 空串与非密钥字段不动 |
| S4 | 解不开的密文不记失败（换机时静默丢 Key） | 解不开的密文给出降级提示 |
| S5 | `is_sealed` 恒 False | encrypt→decrypt 往返还原明文 |

**一个被否掉的变异方向，值得单独写下来。** S5 最初写的是「`is_sealed` 恒 **True**」，
逻辑上更「激进」—— 谁都被当成密文，判据应该炸得更响。实际跑出来是：

    binascii.Error: Invalid base64-encoded string

因为 `decrypt_secret(PLAIN)` 会拿一段普通文本去 base64 解，`validate=True` 当场抛。
脚本带 Traceback 退出，`  ✗ ` 一行都没打 —— 我的 `_judged()` 判它「红了但没点名」，
刀自己报 FAIL。这不是判据的错，是**刀选错了方向**：恒 True 让判据还没开口就崩了，
恒 False 才让它走到「往返还原失败」那一句。

**刀的变异方向要选判据能开口的那条，不能选让判据闭嘴的那条。** 这条和第三十轮
「崩了 ≠ 认出来了」是同一件事的两面：那边防的是把崩溃当抓取，这边防的是
**自己制造崩溃然后怪判据**。


**第五道：`开机自启`，以及它暴露的判据缺陷。** 这道门守「用户登录时到底跑什么」，
18 条断言从没配过刀。最想打的一刀是「`disable` 删错值名」—— 那正是 2026-10-18
真实泄漏的那次：真实 `HKCU\...\Run` 里留下一条指向已删 tempfile 的死命令，
用户下次登录弹「无法运行」。

**这一刀一开始写不出来。** 原因很实在：刀的 finally 只还原**源码字节**，删不掉
已经被错误逻辑漏掉的注册表项；而测试自己的 finally 调的**正是被变异的 `disable`**。
于是每跑一次刀，本机就多一条死命令 —— 验个判据反而污染用户环境，这笔账不划算。
所以第一版刀只写了五把，并在注释里写明「要验 disable，得先给测试加钩子，另开一轮」。

**然后想明白：这不是「刀不能写」，是判据有缺陷。** 清理路径依赖被测对象 ——
`disable` 一旦真回归，`test_autostart` 自己每跑一次也会留一条残留，只是没人注意到。
清理是测试的卫生责任，不能托付给被验的对象，就像不能用一个坏掉的删除键去证明
「删干净了」。改法很小：

    def _raw_delete(value_name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, value_name)
        except OSError:
            pass

`finally` 里三处 `autostart.disable(...)` 全换成 `_raw_delete(...)`，与实现彻底解耦。
改完 A6 才写得出来，而且**跑完复查 HKCU Run：7 个既有值、0 残留** ——
`disable` 被破坏时清理仍然干净，这就是修复的实证，不是「我读了一遍代码觉得对」。

**顺序又出现了：先修判据，再加刀。** 与 M5 那次「先有判据才有刀」是同一个道理，
只是这次修的不是「判据缺一条」，而是「判据自己的卫生不可靠」。

| 刀 | 变异 | 点名 |
|---|---|---|
| A1 | `command_for` 不加引号 | 带空格路径须有引号包裹 |
| A2 | 用 `python.exe` 而不是 `pythonw` | 用 pythonw（无控制台） |
| A3 | `is_enabled` 遇 OSError 返回 True | 不抛不误报 |
| A4 | `sync(False)` 也去 enable | sync(False) → 关 |
| A5 | `read_command` 异常时返回 `""` | read_command 同路径 |
| A6 | `disable` 删错值名 | disable → False |

#### PR #18（续六）· 最后 12 个红点：一半是缺夹具，一半是测试自己没隔离

上一轮把「`git ls-files` 问不到」那一族修完，剩下 5 道门 + 2 个视频用例。逐条查下来，
**没有一条是产品缺陷** —— 但它们分成性质完全不同的两类，处理方式也不该一样。

**一、`config.yaml` 一族（4 道门 + 1 个文件）**

`config.yaml` 在 `.gitignore` 里（实配，含 api_key），所以**任何新克隆的树上都没有它**：

```
投影层自检      FileNotFoundError: .../config.yaml
ASR 事件流      FileNotFoundError: 'config.yaml'
桌面视频 e2e    FileNotFoundError: .../config.yaml   （7 个用例全 error）
抓屏性能闸      D8 真实 config.yaml 判定与原文一致   ← 26 PASS / 1 FAIL
视频进度        3 个用例 FileNotFoundError
```

处理：退回出厂模板 `config.release.yaml` 当夹具。这不是"凑绿"—— 这几条验的是
「视图构建对一份合法配置做了什么」「进度事件有没有发到总线」「`desktop.media.watch_videos`
默认是不是 true」，**与 api_key 无关**；而且出厂模板恰恰是"用户拿到的那份默认值"，
拿它验默认值比拿实配验更有意义。两份都没有才报 77。

**二、视频两道：不是缺 ffmpeg，是测试自己没隔离**

这条值得单独写，因为它的失败长相极具误导性：

```
[媒体观看] 无 ffmpeg，跳过抽帧（仅基于标题/简介理解）
FAIL: test_success ... AssertionError: '厨房' not found in '…（未能取到可看的画面/图片…）'
```

看起来是"本机没装 ffmpeg"，加个 `pip install imageio-ffmpeg` 就完事。但读测试：

```python
with patch.object(vw, "download_media", ...), \
     patch.object(vw, "sample_frames", ...):     # ← 抽帧已经 mock 了
```

**它已经 mock 了 `sample_frames`。** 那为什么还走到"无 ffmpeg"分支？因为
`watch_video` 里那道闸是：

```python
ffmpeg_exe = _resolve_ffmpeg()
if video_files and ffmpeg_exe:      # ← 前置能力探测，没被 mock
    raw_frames = await sample_frames(...)
```

`_resolve_ffmpeg()` 是**前置探测**，mock 的是**下游**。没装 ffmpeg 时前置探测返回
`None`，整个分支被跳过，那个 `sample_frames` mock **一次都没被调用**。

所以这条用例的结论取决于**跑它的机器有没有 ffmpeg**，而不是代码对不对：
装了 → 绿，没装 → 红，而 `sample_frames` 这个 mock 形同装饰。
`test_video_watch_progress` 里 `test_success_run_recorded` 断言 `frames == 2`
失败（拿到 0）也是同一个原因。

修法不是装 ffmpeg，是**把前置探测一起 mock**：

```python
patch.object(vw, "_resolve_ffmpeg", return_value="/fake/ffmpeg")
```

补完后 `test_video_watch` **21/21**、`test_video_watch_progress` **7/7**。
**注意这里没有用 77** —— 这条判据在缺 ffmpeg 的机器上**完全跑得起来**，
只是它自己没隔离环境；报 77 会把一份本来能拿到的覆盖白白丢掉。

**三、感知哈希：`imagehash` 是故意不在 requirements 里的**

`requirements.txt:25` 写着用 numpy 自实现替代 imagehash。所以"本机没装 imagehash"
是**正常状态**。原来 `test_phash.main()` 直接 `return 1`，在汇总里与真回归长得一模一样，
只能靠人去看那一行文字。改为 `[SKIP]` + 77，并给出 `pip install imagehash==4.3.2`。

**四、顺带：`_pick_python` 的静默降级现在会说出来**

GATES 里 32 项声明用内置运行时、其余用 `.venv`。但内置运行时不存在时
`_pick_python` **静默**换成 `sys.executable` —— 在交付包工作副本里（没有 `python/`），
全部闸门其实都由当前解释器跑，而汇总一个字不提。这与本轮主题（缺环境要说缺环境）
是同一件事，所以开跑前打一行：

```
⚠ 内置运行时不存在：…\python\python.exe
   声明用它跑的那几项会改由当前解释器执行：…\.venv\Scripts\python.exe
   —— 分档在这台机器上不成立；不是失败，但读数时要知道
```

**验证**：全量 **14 红 → 0 红**（211 项判完 / 10 项跳过 / 0 红）。
逐门读数：投影层 16/0、ASR 事件流全过、桌面视频 e2e 7 OK（原 7 errors）、
抓屏性能闸 **27/0**（原 26/1）、视频观看 **21/21**、视频进度 **7/7**、感知哈希 77。

#### PR #18（续五）· 「问不到」被当成「答否」：三道闸门在非 git 工作树里恒红


上一轮记录了一个没修的发现：在交付包工作副本里跑全量，14 项红里有 12 项与产品无关。
这一轮先修其中最硬的一族 —— **`git ls-files` 问不到**。

**现象**（同一台机器，只是目录没有 `.git`）：

```
【必需资产清单】FAIL app.ico :: 在磁盘上但没进 git —— 打包只取 git ls-files，包里会缺
               结果: 0 PASS / 10 FAIL          ← 10 件产品素材全被判"没入库"
【陌生人首启判据链】FAIL 副本建立成功  <- RuntimeError('git ls-files 失败：fatal: not a git repository')
【源码包排除规则】✗ 取不到 git 文件清单（exit 128）。本脚本要求仓库是 git 工作树。
```

**根因不是 git 坏了，是判据把「问不到」当成了「答否」**：`git ls-files` 在非仓目录里
返回 128 + `fatal: not a git repository`，而「这个文件确实没入库」返回 1。原来的代码
只判 `returncode != 0`，两种完全不同的情况走进同一个分支，于是输出了一句
**它没有验证过的断言**：「在磁盘上但没进 git —— 包里会缺」。而文件在上游是跟踪的。

这比"误报"更糟：它给的是一个**看起来可执行**的错误结论（去 `git add` 吧），
真去做了反而会把 `config.yaml` 之类的实配加进仓库。

**修法分两种，都不许报绿**：

| 闸门 | 依赖 git 的部分 | 处理 |
|---|---|---|
| `test_bundled_assets` | 只有「是否入库」这一项 | 拆成 `yes/no/unknown` 三态：存在 / 非空壳 / 真格式**照常逐件判**，只有入库报「未验证」，末尾 `exit 77` |
| `verify_first_run` | 第一步「按 `git ls-files` 造干净副本」 | 新增 `NoGitWorkTree` 异常 → `[SKIP]` + 77。**整条链 7 步一步没跑，不能说验过** |
| `test_pack_for_teammate` | 只有 `[3]/[3b]` | 只跳过这两节，红线 / 规则表 / `verify_zip` / 随包说明四节照常判，末尾 77 |

**三把刀也跟着连环自爆，处理同样分两种**：

- `_qc_bundled_assets_mutation`：三刀（删文件 / 截断成空壳 / 换成假格式）**都不需要 git**。
  基线判据从「`returncode == 0`」放宽成「`returncode in (0, 77)` 且没有 FAIL 行」，
  三刀照常跑 —— 实测仍**三刀全红 + 还原字节一致**。能验的继续验。
- `_qc_pack_exclude_mutation`：变异目标就在 `[3]` 里，前提没了。整把报 77，
  不硬凑一个"全红"出来 —— 那种红什么都不证明。

**验证**：

- `必需资产清单`：`0 PASS / 10 FAIL` → `0 PASS / 0 FAIL / 10 未验证`，exit 77，
  逐件打印「存在/非空壳/真格式 已验，但「是否入库」未验证」。
- `陌生人首启判据链`：`RuntimeError` 判红 → `[SKIP]` exit 77（原因写清"7 步一条都没跑"）。
- `源码包排除规则`：四节照判全 ✓，末尾 `[SKIP]` exit 77。
- `_qc_bundled_assets_mutation`：`起点不绿` → **起点绿 + 三刀全红 + 还原字节一致**。
- `_qc_pack_exclude_mutation`：`基线就不绿` → `[SKIP]` exit 77。
- 静态闸门（字节码 / 编码 / 全局名 / 同名 / 不可达 / 常量条件 / 静默异常 / 导入路径）全绿。

**还剩**：`config.yaml` 一族（投影层自检 / ASR 事件流 / 桌面视频 e2e / 抓屏性能闸 D8）
与 `感知哈希`（缺 `imagehash`）共 5 项，同一处理，下一轮。

#### PR #18（续四）· 闸门在推荐解释器下起不来：「跑得起来的那个会掩盖跑不起来的那个」


**问题**：`check_all.py` 全量跑，**一行闸门都没执行就退出**，报

```
File "...\tools\check_all.py", line 787, in <module>
    from _gate_lock import acquire, release
ModuleNotFoundError: No module named '_gate_lock'
```

看起来像环境坏了（少装了什么），实际是这道门**在推荐解释器下从来起不来**：
`.venv/Scripts/python.exe tools/check_all.py` 必崩，`python tools/check_all.py` 正常。

**根因**：交付包自带的是**可嵌入运行时**，`python/python312._pth` 把 sys.path 写死成

```
.   DLLs   Lib   Lib\site-packages   …   ..
```

最后那个 `..` 是仓根 —— 所以 `main.py` 顶部的 `from src.xxx import` 能用，
但 **`tools/` 不在里面**。普通 CPython 会把「脚本所在目录」放进 `sys.path[0]`，
可嵌入运行时不补这一条。于是同一份代码：

| 解释器 | `tools/check_all.py` |
|---|---|
| `python`（普通 CPython） | 能跑（`sys.path[0] == tools/`） |
| `.venv/Scripts/python.exe`（推荐解释器） | `ModuleNotFoundError: _gate_lock` |

而 `.venv` 恰恰是要三方依赖时的推荐解释器 —— 也就是说**这道门在官方推荐姿势下从未跑起来过**。

**为什么一直没暴露**：因为这属于「跑得起来的那个解释器会掩盖跑不起来的那个」。
开发机上两条路都试过，先用普通 `python` 试通了就不再怀疑；而 CI 里 `python` 也是
普通 CPython。这类故障有个共同特征：**报的是 ModuleNotFoundError，不像判据失败，
更像环境问题**，于是人第一反应是去装包，越查越远。

**修**：`check_all.py` 尾巴补三行（`sys.path.insert(0, 脚本目录)`）。同一族在
PR #17 已经修过一次（`check_web_js_syntax` 只把仓根塞进 sys.path，而
`check_bridge_methods` 在 `tools/` 下）——**同一族第二次复发**，所以这次不只修，
还补了判据。

**新闸门 `tools/check_tool_sys_path.py`**（纯 stdlib、只读 AST、181 个脚本约 0.2s）
三条判据：

1. 【正向】`check_*` / `test_*` / `_qc_*` 里凡 import 了兄弟模块（`tools/<名>.py` 存在），
   就必须在**模块层**、或该 import 之前同一/更外层作用域里，有一次 sys.path 改写。
2. 【自洽】那次改写的实参必须由本脚本路径推导（`__file__` 或其派生名，
   含 `for p in (ROOT, …)` 这种一层中转），**不许写死盘符路径** —— 写死的换台机器等于没写。
3. 【覆盖】必须真的扫到兄弟 import；扫到 0 个 = 判据自己瞎了，不算通过。

两类**不计分**但必须打印：写在接了 `ImportError` 的 `try` 里的「降级导入」
（如 `check_package_secrets` 找不到 `check_web_assets` 就转警告）、以及带
「不进闸门：」标记的免登脚本。理由：这两种都不是「起不来」。

为什么不用「跑一遍试试」当判据：那要求每道闸门都真跑起来，而闸门里有一半要声卡 /
模型 / 14 分钟；更关键的是**试出来的绿是假绿**（见上表第一行）。

**这道门第一次跑就抓到一个真的**：`_qc_api_wiring_mutation.py` 函数里两处
`from check_api_wiring import KNOWN_UNWIRED`，靠的正是「解释器补脚本目录」。
补 `sys.path` 后，本轮全量跑里那条 **`反向接线变异` 整条挂掉的记录**（`ModuleNotFoundError:
No module named 'check_api_wiring'`）随之消失，四把刀（起点绿 / M1 / M2 / M3 / 还原后绿）
全部回到正常。**先被静态判据指出来、再被真跑确认** —— 这也是新闸门有牙的证据之一。

**顺手发现但本轮没修（记在这里，别让它消失）**：在**交付包工作副本**（无 `.git`、
无 `config.yaml`）里跑全量，**14/215 项红**，其中 12 项与产品无关：

| 类 | 闸门 | 真实原因 |
|---|---|---|
| 无 git 工作树 | 必需资产清单 / 源码包排除规则 / 陌生人首启判据链（+2 把变异刀） | `git ls-files` 失败，脚本**把「问不到」当成了「答否」**，于是报「在磁盘上但没进 git —— 包里会缺」，而文件在上游是跟踪的 |
| 无实配 | 投影层自检 / ASR 事件流 / 桌面视频 e2e / 抓屏性能闸 D8 | `config.yaml` 在 `.gitignore` 里，工作副本没有 |
| 缺三方包 | 感知哈希 | venv 里没有 `imagehash` |

这七道闸门全都 `exit 1`，而按本仓既有约定**「缺环境 ≠ 判据失败」应报 77**。
本轮只记录、不改：改它们要逐道确认「真的缺环境」与「真的判据红」的边界，
属于下一轮的事；但**现在这 12 个红点会把真红点淹掉**，所以先写进这里。

**验证**：

- `check_all.py` 在 `.venv` 下**能拿到锁并跑完**（本轮全量 215 项判完、4 项跳过、
  14 项红，红的构成见上表 + 2 项视频用例）；修复前是 5 行 traceback。
- 全量跑里 **`变异刀空写体检` 428.9s**，远低于新给的 1800s 慢项预算 —— 上一轮
  「新闸门放进整轮里能跑完」这个断言到此为止才算证实（原来 600s 写死超时会把它 kill）。
- 新闸门 **33 PASS / 0 FAIL**：扫到 181 个脚本，15 个真 import 兄弟模块，
  1 个降级导入（`check_package_secrets`），2 个免登跳过。
- 新变异刀 **11 项全过**：G 正常 / T 路径经 `for` 中转 / M 漏补 / H 写死盘符 /
  A 先 import 后补 / S 补在函数里 / B 语法错误 / D 降级导入不算红 / K 免登不算红 /
  X1 空目录 / X2 目录不存在。桩脚本全在临时目录里，不碰仓库。
- `check_gate_registry` ✓ 全部有着落（220 个登记脚本）；`check_gate_exit` 220 脚本 0 命中；
  `check_site_counts` 7 PASS / 0 FAIL（GATES 219 → **221**，站点 4 处锚点同步）；
  `check_open_encoding --strict` 0 处；`check_module_globals` / `check_duplicate_def` /
  `check_unreachable` 对 `tools` 全干净。

#### PR #18 · 18 把变异刀是空转的：判定崩了也被算成「抓住了」


**问题**：第二十二轮新增「变异刀空转体检」——把判定文件换成退化实现（恒 exit 0 / 恒 exit 1），
看刀认不认得出来。那一轮只覆盖 7 对 `portable` 的，body 里明确留了"另外 9 个闸门族 +
61 个特性族的刀没验"。本轮把这两块都过了一遍，**18 把刀在「判定恒红」方向判据失效**。

根因是同一句：

```python
caught = r.returncode != 0
```

它把两件事当成一件事：**判定判出了问题** 和 **判定根本没跑起来**。
实测把 `check_release_numbers.py` 换成一句 `raise RuntimeError("闸门自己崩了")`，
`_qc_release_numbers_mutation.py` 仍然逐条打印「已抓住：<无输出>」、最后打印
「5 刀全红」并 **exit 0** —— 一个连第一行都跑不完的判定，被认证成"五刀全抓"。

更隐蔽的一层：**恒红的判定能让每一把刀都变红**。所以"全红"这个读数，在起点不绿的时候
什么都不证明；而旧版套件从来不检查起点。

**改动**：18 把刀统一加 `_judged()` + `_baseline_green()`：

- `_judged(r)`：判定是「判出了问题」——`returncode != 0` **且** 输出里有 `FAIL`/`✗`
- `_baseline_green()`：起点必须绿，否则直接 `FAIL 起点就不是绿的` 并 `return 1`

**这个判据改了两版，第二版更值钱**。第一版还要求「输出里没有 `Traceback`」，
实测把**先印 FAIL、随后收尾时崩掉**的判定判成了"没抓住"：

- `_qc_asr_mutation` ①②：印了 `FAIL lang='zh-cn'`，随后 vosk 桩抛
  `AttributeError: 'Model' object has no attribute '_handle'`
- `_qc_config_bootstrap_mutation` B：印了 `FAIL 状态 created`，随后读临时
  `config.yaml` 抛 `FileNotFoundError`

两次都是**仪器太窄**（本轮第三次）。真正的判别是**有没有点名**：退化桩只印一个
`exit 1`，一个字都没有——那才叫"什么都没说"。

**工具侧**：

- `PAIRS` 加 `tier` 字段（`--tier portable|dev|all`）。分层不是"重要性"分层，
  是"这台机器能不能跑"分层：dev 层要 `.venv`，CI 上没有，混进去只会全 ERROR。
- `.venv` 不再从仓副本里排除（dev 层靠副本里的 venv 跑）。
- `FAIL_MARKS` 补 `没抓住` / `未抓住`。
- **`_resolve()`：判定文件不一定在 `tools/` 下**。第一版一律拼 `tools/<名字>`，
  于是特性族的 13 对（判定在 `src/tests/test_*.py` 或 `tools/test_*.py`）
  **全部 ABSENT** —— 而 ABSENT 是"不计失败也不计通过"，`结果：ABSENT 13`、
  **exit 0**：一个都没验，却什么都没说。
- **ABSENT / ERROR 拆开**：套件在、判定不在 = **PAIRS 声明错了**（改名/拼错/路径规则不对），
  是 ERROR；只有"套件本身都不在"才是 ABSENT。
- **完整性检查**：模块级声明了 `TEST` 的刀，必须在 `PAIRS` 里，或者在 `EXCUSED` 里
  写清"为什么现在验不了、什么条件下能验"。空理由等于没写。
  这样"哪些刀被验过"是可数的，不再靠"我记得验过哪些"。

**顺带查出的一处变异失效**：`_qc_pathrefs_mutation.py` 的 H 刀锚点写的是 **20 空格**
的 `except OSError:`，而 `tools/_qc_path_refs.py:144` 是 **24 空格**。
`orig.count(old) != 1` 这道锚点守卫**没能拦住**它——20 空格的串正好是 24 空格那一行的
**子串**，命中次数还是 1。于是 replace 之后缩进全乱，产出
`IndentationError: expected an indented block after 'except' statement on line 144`，
而刀把"变异把代码改坏了"算成了"抓住了"。
**教训：`count(old) == 1` 只保证"只出现一次"，不保证从行首开始。**

**结果**：

```
check_knife_vacuity.py --tier portable   → CERTIFIED 8            exit 0
check_knife_vacuity.py --tier dev        → CERTIFIED 17 / SKIP 1  exit 0
check_knife_vacuity.py --tier all        → CERTIFIED 25 / SKIP 1  exit 0（2m07s）
_qc_knife_vacuity_mutation.py            → 13 刀 + 对照 全 PASS    exit 0
```

唯一的 `SKIP` 是 `_qc_site_counts_mutation`：它 `import check_site_counts as C`
进程内直调，换文件对它无效。**SKIP 不算通过**，所以是 25 而不是 26。

**三把刀起点就是红的**（已列进 `EXCUSED`，每条写明原因）：

| 刀 | 为什么验不了 |
|---|---|
| `_qc_bundled_assets_mutation` | 判定用 `git ls-files` 判「在盘上但没进 git」；本工具在仓副本里跑，`.git` 被排除 → 判定恒红（10 FAIL） |
| `_qc_tray_switch_mutation` | 判定要 `config.yaml`（在 `.gitignore` 里，CI 由 prepare 步骤生成）→ `FileNotFoundError` |
| `_qc_presence_mutation` | 判定在**干净树上就红**：33 PASS / 1 FAIL（`非法的不影响其它绑定 ← []`）。**这条本身是待查项** |

前两条是环境，第三条当时记成"真红"——**而它一直是红的，所以那 6 把刀从来不需要抓住任何东西**。
这就是"起点必须先绿"存在的理由。

> **【2026-11-08 更正】上表第三条的归因是错的。** 当时写的是"全局热键注册在无桌面会话时
> 返回空表"，实际跟桌面会话毫无关系：本机 `.venv` 里**没有装 `pynput`**（`requirements.txt:53`
> 有声明），于是 `hotkeys._validate` 走到"没装依赖"的兜底分支，只做形状校验 →
> `<nosuchkey>` 被当成合法键名 → `why3` 为空 → 那条断言红。
> 也就是说这**不是产品缺陷**，是"判定依赖了一个可能不存在的依赖，却把它报成断言失败"。
> 真正的修法在第二十六轮：让兜底自己认识键名（`_fallback_ok` + 白名单），
> 于是判定不再依赖装没装依赖；`_qc_presence_mutation` 也从 `EXCUSED` 挪进了 `PAIRS`。
> **一条错误的归因比没有归因更贵**：它会让下一个人去查热键后端和桌面会话。

**已知边界**：78 个 `_qc_*` 里还有 **48 把**是"改源码后自己在进程内断言"，没有独立判定
文件，换文件这个手法用不上。它们的空转风险与这 26 对完全相同，只是需要另一种做法，
本轮没做——**不要把这 25 个 CERTIFIED 读成"全部刀都验过了"**。

#### PR #18（续三）· 第三种方向：封住写入口 —— 「SKIP 50」拆开是三种东西

**一、先把读数拆开。** 前三轮的报告末尾一直挂着同一句话：`SKIP 50 不算通过`。这一轮先去
看这 50 到底是什么：

| 类别 | 数量 | 是什么 |
|---|---|---|
| 一次性探针 | 18 | `_qc_paint_matrix` / `_qc_path_refs` / `_qc_wake_word`…… 既没有 `MUTS` 也不做变异，永远 SKIP —— **根本不该进分母** |
| 真刀，无模块级 `MUTS` | 28 | 把 `(old,new)` 写在函数里，或者根本不改源码（往 `src/` 扔语法坏文件、复制一份配置、删一个目录） |
| 有 `MUTS`，形状退化不了 | 4 | 元组里分不清哪两个元素是 old/new |

一个数字里挤着三种东西，谁都读不出来。先把口径改对：**刀 = `_qc_*_mutation.py`**（本仓的命名约定），
探针单列、不进分母。

**二、新机制：不碰变异，封住写入口。** 前两道工具都要"知道变异长什么样"才能退化它，
而上面那 28 把的变异不是一次字符串替换。这一道换思路：

```
把 builtins.open / io.open 的**写模式**重定向到临时文件
→ 目标路径一个字节都不会变 → 等于"变异根本没发生"
→ 判定有牙 → 它看不见任何变化 → 刀必须报「没抓住」→ exit != 0
→ 判定空转 → 刀照样报「N 刀全红」→ exit 0
```

这个方向和变异形态无关：改源码的、扔坏文件的、删目录的，一视同仁。

**三、三个方向各自管什么（合起来才闭环）：**

| 工具 | 退化的东西 | 证明了什么 | 覆盖 |
|---|---|---|---|
| `check_knife_vacuity.py` | **判定文件**换成退化实现 | 判定变坏时刀会不会红 | 29 对（有独立判定文件的） |
| `check_knife_noop.py` | **变异本身**（`(old,new)` → `(old,old)`） | 源码没变时会不会假绿 | 33 把（有模块级 MUTS 的） |
| `check_knife_nowrite.py` | **写入口**（变异落不了地） | **什么都没变**时会不会假绿 | 29 把（其余全部） |

两个读数能互相对上，这是这一轮最想要的东西：
`noop 的 29 CERTIFIED + 4 SHAPE = 33` 正是 `nowrite 的 COVERED_ELSEWHERE 33`；
`nowrite 的 21 + 4 + 1 + 3 = 29` 正是 `noop 的 SKIP 29`。**分母对上了，才谈得上覆盖。**

**四、途中修掉的五个真问题：**

1. **`check_delivery.py:check_template` 停在旧认知**：它断言 `force_first_run is True`，
   而 2026-10-20 的修复**故意**把它改成了 `false`（注释就写在 `config.release.yaml` 文件头：
   带 true 会让每个用户每次启动都被拽回引导页重填 Key；首启判定是
   `force or not first_run_done`，取 false 照样只走一次）。`src/tests/test_release_template.py`
   当时跟着改了，这个闸门没跟 —— 于是它对**任何正确打的包**都判 FAIL，
   连带 `_qc_delivery_mutation` 466 行 14 个用例**永久红**，那 14 个用例等于从来没在跑。
2. **`_qc_clone_card_mutation` 起点 exit 77 被记成 ERROR**：77 是刀自己举手说"这台机器缺
   jsdom，我没验"。记 ERROR 等于把环境问题算成刀坏了，记 CERTIFIED 更是胡说 —— 单列 SKIP。
   顺带把 `check_knife_nowrite.py` 的 77 分支也定成 SKIP（同一个道理）。
3. **`_qc_asr_event_flow_mutation` 读 `config.yaml`**（gitignore 的实配）→ 缺文件时回落到出厂模板。
4. **`check_knife_vacuity.py` 的仓副本造得不像仓库**：它 `copytree` 时排除了 `.git`，于是
   `check_bundled_assets` / `check_pack_exclude` / `_qc_first_run_mutation` 这些靠 `git ls-files`
   取清单的判定恒红 —— 最后一条 `EXCUSED`（`_qc_bundled_assets_mutation`）就是这么来的。
   代价估对了（"每对多拷几十 MB"），结论错了：**副本里重新 `git init` + `add -A` + commit
   只要几十毫秒**，而且比复制原仓更接近"新克隆"（天然遵守 `.gitignore`）。
   抽成 `tools/_qc_repo_copy.py`，**`EXCUSED` 表现在是空的**。
5. **`check_knife_noop.py` 的刀口径**：18 个一次性探针原来混在 SKIP 里，改成单列不计入分母。

**五、仪器自己的两个坑（这一轮最贵的部分）：**

- **起点跑和封写跑共用一份副本 → 起点留下的产物替封写作弊。** 两把桩刀当场露馅：老实刀把
  `mutated` 写进 `probe.txt`，封写时写入被吞但 `isfile()` 仍为真 → 打印 PASS → 被误判 VACUOUS；
  生成模块再 import 的刀，起点把 `gen_mod.py` 留在 `tools/` 里，封写时 `import gen_mod` 从残留
  文件里成功了 → 同样误判。方向上是"把好刀记成空转"（比漏报吵），但仍然是**仪器在说谎**：
  它测到的是上一次的副作用。修法：`_qc_repo_copy.reset()` —— 副本本来就是 git 仓，
  `git checkout -- .` + `git clean -fd`（**故意不带 `-x`**，`.venv/` `config.yaml` `logs/` 要留着，
  带 `-x` 会把它们删掉，下一遍就变成"缺环境"）。
- **封写用 `io.BytesIO` 当写目标 → 文本模式写崩。** 第一版 17 把刀里 7 把崩在
  `TypeError: a bytes-like object is required`，差点被记成"认出来了"。改成"真临时文件"后
  这 7 把才真的被验。教训：**"崩了"和"认出来了"必须分开记**，否则工具在自欺。

**六、还剩什么没验（必须和读数一起读）。** 三合一之后 **54/62 把至少被一道判为 CERTIFIED**，
剩下 8 把每一把都有名有姓的理由：

| 刀 | 原因 |
|---|---|
| `_qc_asr_event_flow` / `_qc_delivery` / `_qc_dialogue_voice_chain` / `_qc_wake` | 机制要求**真写出一个文件再 import**，封写等于抽掉前提 → 归 ERROR（本工具无能为力，不是它们的缺陷） |
| `_qc_clone_card` | 缺 jsdom（`npm i jsdom` 或设 `DANIYA_JSDOM`）→ SKIP 未验证 |
| `_qc_knife_noop` / `_qc_knife_nowrite` / `_qc_knife_vacuity` | 质检仪器自身的刀，由各自门禁互检 → META（**单列而不是静默跳过**，静默跳过正是这一轮在修的病） |

> **不要把 54/62 读成"刀都验过了"。** 它说的是"62 把里有 54 把至少被一种机制证明过它不会假绿"，
> 另外 8 把的原因写在上表。这个数字以前是 26/79（且分母里混着 18 个不是刀的文件）。

**七、SHAPE 归零：让刀自己声明形状。** 上一版把「有 `MUTS` 但分不清 old/new」的 4 把判成
SHAPE（该验没验）。这一版让**刀自己声明形状**，工具照读：

```python
NOOP_SHAPE = ("idx", 1, 2)       # (标签, old, new, 说明) —— 第 1 项 old、第 2 项 new
NOOP_SHAPE = ("filearg", 1, 2)   # (标签, 文件路径, 执行变异的函数) —— 空变异 = 读回原文
```

**知识放在数据旁边**，不另建一张会烂的表。声明写坏（越界 / 类型不对 / 指的两项本来就相同）
一律判 SHAPE 并**拒绝硬改** —— 改错一项不会报错，只会产出一个假 CERTIFIED，比没有结论更糟。
四把刀这么声明：`_qc_smoke_retry`(1,2)、`_qc_voice_ref`(2,3)、`_qc_partial_reply`(1,2)、
`_qc_site_counts`(filearg 1,2)；`check_knife_noop.py` 因此 **CERTIFIED 29 → 33、SHAPE 4 → 0**。

**八、另外两道门在这台机器上从来没真跑过。** 查 `_qc_clone_card_mutation` 为什么一直 SKIP，
顺藤摸到两条：

- **`克隆面板真值表`**（`test_clone_card_ui.py`）缺 jsdom → 一直 `exit 77`（这是**正确**的：
  环境不满足，单列未验证）。临时装一份 jsdom 指向 `DANIYA_JSDOM` 之后：**24 PASS / 0 FAIL**，
  变异刀 6 刀全红、还原后仍全绿。也就是说这道门的牙齿一直在，只是这台机器上从没张过嘴。
- **`参考音频物化`**（`test_clone_ref_audio.py`）是**真缺陷**：`main()` 里
  `shutil.copy2(ROOT/config.yaml, ...)` —— `config.yaml` 是 `.gitignore` 的实配，源码仓里没有，
  于是它在任何"刚克隆"的树上都崩在 `FileNotFoundError: [WinError 2]`，**连第一条断言都没跑到**。
  和上一轮托盘刀同一修法：缺文件时回落到出厂模板（本门验的是"参考音频怎么物化"，与 api_key
  填没填无关）。修后 **22 PASS / 0 FAIL**。

装上 jsdom 之后 `_qc_clone_card_mutation` 在三合一里也转 CERTIFIED（封写下 exit 1，"6 处有问题"），
覆盖 **54/62 → 55/62**。


#### PR #18（续二）· 「缺环境」被报成「判据失败」：四个红点，四个都不是产品坏了

上一轮把「空转」和「无法判定」两个计数器压到 1 和 2。这一轮把那 3 个读数**逐个查清**，
结论是它们同源：**判定依赖了某个"这台机器上可能没有"的东西，然后把它报成断言失败。**

| 红点 | 表面症状 | 真实原因 |
|---|---|---|
| `A5环境探针` 闸门 | 7 PASS / 2 FAIL，exit 1 | `logs/` 在 `.gitignore` 里 → 新克隆的树上一份 `launch_run*.log` 都没有 |
| `_qc_a5_probe_mutation` | 六把刀"全红" | 起点恒红（同上）→ 红是白送的 |
| `_qc_tray_switch_mutation` | 起点就红 | 判定要 `config.yaml`，而它也在 `.gitignore` 里 |
| `_qc_presence_mutation` | 33 PASS / 1 FAIL | 本机没装 `pynput` → `_validate` 走兜底只做形状校验 → 拼错的键名被放行 |

四个都不是产品缺陷，也都不该记成"待查项"。**它们的共同点是：判定把"我没法判"说成了"它坏了"。**

**① A5 探针：给"没有样本"一个自己的状态**

原来两条硬 FAIL：

```python
chk("至少扫到 1 份 launch_run 日志", len(heartbeat["files"]) >= 1)
chk("至少 1 条 [UI心跳] 被分类", heartbeat["total_lines"] >= 1)
```

`logs/` 是 gitignore 的，所以这两条**对每个新克隆的人恒红**。现在分三态：

| 现场 | 判据 | 退出码 |
|---|---|---|
| 有日志、扫得到 | 逐条判 | 0 / 1 |
| 有日志、扫出 0 份 | **FAIL**（探针坏了） | 1 |
| 压根没有日志 | **WARN 未验证** | **77** |

`77` 是本仓既有约定，`check_all` 会把它单列成「未验证」而不是「通过」；探针还会打一行
`[SKIP] …`，因为 `check_all` 正是从这一行摘原因——不写就会显示「未说明原因」，
那等于把"为什么没验"又丢一次。

顺带给探针加了 `--root`：**判据得能被夹具驱动**，否则"探针在缺样本时怎么说话"永远
只能靠"这台机器碰巧有没有日志"来验。

**② A5 刀：先修起点，再修刀**

只补 `_judged()` + `_baseline_green()` 没用——起点本来就是红的，补完只会诚实地说
"起点不是绿的"。所以两件事一起做：探针加 `--root`（见上），刀在 tempfile 里造一份
合成日志当夹具。判据从此不依赖本机日志。

加了**三把限位刀**：

```
G 夹具里没有日志            → 77 + WARN（未验证 ≠ 失败）
H 日志在、但没有 [UI心跳] 行 → 1 + FAIL（有样本却量不到，是真失败）
I 夹具正常                  → 0（起点必须绿）
```

G 和 H 必须成对：只加 G 会让人把 H 也放宽成 77，只加 H 会让 G 退回硬 FAIL。

**③ 托盘：缺 `config.yaml` 时用出厂模板当夹具**

判定里 `shutil.copy2(ROOT/config.yaml, …)`，而 `config.yaml` 在 `.gitignore` 里。
本文件测的是 `toggle_mute` 的状态机，**不测"用户的配置长什么样"**——所以缺文件时
回落到 `config.release.yaml`（在 git 里的出厂模板），并打印一句说明。
29 PASS / 0 FAIL。

**④ 在场感：修产品，不修测试**

`hotkeys._validate` 在没有 `pynput` 时的兜底是：

```python
return all(part.strip() for part in combo.split("+"))    # 每段非空就算合法
```

于是 `<ctrl>+<alt>+<nosuchkey>` 通过校验、进映射表，`skipped` 里一个字都不记——
**用户以为绑上了，按下去什么都不发生，日志里也没有线索**。这是本仓反复出现的
「静默失效」，只不过触发条件是"pynput 没装成"。

修法是让兜底自己认识键名：`<...>` 形态必须在 `_NAMED_KEYS` 白名单里（modifier /
功能键 f1–f20 / 媒体键），非 `<>` 形态（`a` / `7`）保持原来的宽松。抽成纯函数
`_fallback_ok`，于是**判定不再依赖装没装 pynput**，在任何机器上都能判。
测试 33 PASS / 1 FAIL → **41 PASS / 0 FAIL**，另加 7 条兜底断言和一把新刀 G
（兜底放行未知键名 → 必须红）。

> **同时更正上一轮的一处错误归因。** 我当时把这条记成"全局热键注册在无桌面会话时
> 返回空表"，**跟桌面会话毫无关系**。错误的归因比没有归因更贵：它会让下一个人
> 去查热键后端和会话状态。更正已写进 PR #18 的对应小节，并在原处留了说明。

**⑤ 顺带：17 把刀的 `_baseline_green()` 比判定本身还严**

它们都写着 `return r.returncode == 0 and "Traceback" not in out`。但**判定自己故意打出来
的** Traceback 也算——`test_presence_controls.py` 的 A3 就故意让回调抛异常、再验证
listener 没被弄死，那一段经 logger 打到 stderr。于是测试 41 PASS / 0 FAIL / exit 0，
刀却判"起点不是绿的"，整把刀一进来就自爆，**而报错信息把矛头指向产品**。

判据改成"退出码 0 且没有 `FAIL` 行"：崩了的判定一定非零退出，Traceback 本身不证明
任何事。这与第二十四轮 `_judged()` 的改法是同一条道理，只是当时只改了一半。

**⑥ 额度表清空，并给它加对账**

`KNOWN_VACUOUS` 从 1 条变成**空表**。空表是有意义的读数：它说的是"没有已知空转"，
不是"没有空转"。为了让这张表不至于重新变成遮羞布，加了两道对账 + 一把限位：

```
N8  额度指向不存在的刀        → exit 2 并点名（额度对着空气）
N9  额度指向已经不空转的刀    → exit 2 并点名（表在说谎）
N10 限位：额度指向真的空转的刀 → exit 0（不许把额度表判成恒错）
```

没有 N10，N8/N9 可以靠"永远 exit 2"满足——结果是没人再敢往表里写东西，
空转又变回没人管。

**结果**

```
check_knife_vacuity.py --tier all   → CERTIFIED 27 / SKIP 1        exit 0（上轮 25 / 1）
_qc_knife_vacuity_mutation.py       → 13 刀 + 对照 全 PASS          exit 0
check_knife_noop.py                 → CERTIFIED 29 / SKIP 50        exit 0（上轮 26 / 1 / 50 / 2）
_qc_knife_noop_mutation.py          → 9 刀 + 对照 全 PASS           exit 0
A5 探针（无日志）                    → 7 PASS / 0 FAIL / 2 未验证    exit 77
A5 探针（夹具）                      → 9 PASS / 0 FAIL               exit 0
A5 刀                                → 6 刀 + 3 限位 全过            exit 0
在场感                               → 41 PASS / 0 FAIL              exit 0
托盘                                 → 29 PASS / 0 FAIL              exit 0
```

**`VACUOUS 0 / ERROR 0` 是这一轮唯一值得说的读数**：79 把刀里 29 把判完且不是空转，
50 把没有模块级 `MUTS`（本工具不覆盖），**没有一把是"不知道"**。上一轮那张表还写着
1 把已知空转、2 把无法判定。

`SKIP 50` 仍然不算通过——它们要另一种做法，这 29 个 CERTIFIED 依旧不许读成"全部验过"。

**这一轮踩的两个坑（都写进代码注释了）**

1. **`io.open(path, "wb").write(compute())` 会先把文件清空。** Python 先求值
   `io.open(...)`（那一刻文件已被截断），再求值 `compute()`。我的 `compute()` 抛了，
   于是 `tools/check_knife_noop.py` 被清成 **0 字节**，只能从远端重新取回来。
   教训：**先算好要写什么，再打开文件。**
2. **`"FAIL" in out` 会命中汇总行 `结果: 9 PASS / 0 FAIL`。** 起点明明是绿的，
   却被判成"有 FAIL"。和第四轮那个 `count(old) == 1` 是同一类：**子串命中不等于语义命中**。
   改成逐行 `startswith("FAIL")`。

#### PR #18（续）· 空变异体检：另外 48 把刀的空转风险一直是零覆盖

上面那套「换判定文件」有个前提：刀得有一个**独立的判定文件**。79 把刀里只有 26 对有，
另外 48 把是「改完源码自己在进程内断言」——换文件这招对它们完全无效，也就是说这 48 把
的空转风险一直是零覆盖。本轮换方向：**不退化判定，退化变异本身**。

```
空变异 = 把 MUTS 里的 (old, new) 换成 (old, old)
```

它等于"什么都没改"。此时判定有牙 → 判定该是绿的 → 刀必须报「没抓住」→ `exit != 0`；
判定空转 → 刀照样报「N 刀全红」→ `exit 0`。**这个方向不需要知道判定在哪**，对 79 把一视同仁。

两个方向是互补的两半，缺哪一半，那一半的空转就没人管：

| 手法 | 证明什么 | 覆盖 |
|---|---|---|
| 换判定文件（第二十二/二十三/二十四轮） | 判定变坏时刀会红 | 26 对（有独立判定文件的） |
| 空变异（本轮） | 没东西可抓时刀不会假红 | 79 把里 29 把能判定 |

**第一次普查就是 5 个假发现——仪器自己没自检**

`_noop()` 第一版一律取「最后两个元素」当 `(old, new)`。实测 `_qc_about_tier_mutation`
的形状是 `(名字, old_thunk, new_thunk, 说明)`，于是它把 **说明** 换成了 `new_thunk`：
空变异根本没发生，刀照常跑、照常全红，工具却判它 `VACUOUS`。**一个假发现，来自没自检的仪器。**
改成按"载荷类型"定位（两个可调用对象 / 两个 bytes / 嵌套对），并且改完用 `is` 验证
old 与 new 是同一个对象——宁可 `SKIP`，也不产出一个假的 `VACUOUS`。修完 6 把里 5 把变 `CERTIFIED`。

**第二次是仪器比现实窄**

runner 从临时目录跑刀，`sys.path[0]` 变成了 runner 所在目录。于是
`_qc_api_wiring_mutation` 一进来就 `ModuleNotFoundError: No module named 'check_api_wiring'`，
被记成 `ERROR`（无法判定）——**这不是刀的缺陷，是仪器把刀换了个环境跑**：刀平时是
`python tools/_qc_x.py` 跑的，那时 `sys.path[0]` 就是 `tools/`。补一行 `sys.path.insert`
之后，这把刀从 `ERROR` 变成 `CERTIFIED`。本轮第二次「仪器太窄」。

**顺带查出的真缺陷：`导入冒烟复核` 在 `PY_VENV` 下一直是 6 FAIL**

普查里 `_qc_smoke_retry_mutation` 报「起点就红」，可直接 `python tools/test_smoke_import_retry.py`
是 12 PASS / 0 FAIL。差别只在解释器——而 `check_all.py` 给这道门配的正是 `PY_VENV`。两层原因：

1. **`env["PYTHONPATH"] = root` 在随包嵌入式运行时下是死代码。** `python/` 是带 `._pth` 的
   嵌入式运行时：`sys.path` 固定、不吃 `PYTHONPATH`、也不加 cwd。实测
   `PYTHONPATH=<root> .venv/Scripts/python.exe -c "import src.fixturepkg.ok"` →
   `ModuleNotFoundError`；改成在子进程里 `sys.path.insert(0, root)` → `rc 0`。
   后果不止夹具：`--root` 指向的树**可能根本不是被 import 的那棵树**（`._pth` 里有包根 `DaniaAI`）。
2. **夹具的 `src/` 少了 `__init__.py`**，成了**命名空间包**。命名空间包是兜底——只有所有
   `sys.path` 条目里都没找到常规包时才用它；而 `DaniaAI\src` 是常规包，直接胜出，
   `src.fixturepkg` 永远解析不到。

修法：子进程显式插 `sys.path`（`PYTHONPATH` 保留，对吃它的解释器无害）；夹具补 `src/__init__.py`
让夹具和真仓库同构。结果：

```
                                    before            after
.venv/Scripts/python.exe            6 PASS / 6 FAIL   12 PASS / 0 FAIL   exit 0
python                              12 PASS / 0 FAIL  12 PASS / 0 FAIL   exit 0
_qc_smoke_retry_mutation（venv）     起点就红           3 刀全红 + 起点绿 + 还原后绿  exit 0
```

**这两处没有变异刀，而且不该硬造**：在常规 CPython 下把它们改回去，整套自测仍然全绿
（没有第二个 `src` 来抢，`PYTHONPATH` 也照常生效），刀抓不到任何东西——那就是一把空转刀。
能钉住它们的只有「换一个把包根放进 `sys.path` 的解释器跑」，也就是上面那三行实测。

**`KNOWN_VACUOUS`：只许减不许增**

唯一一把 `_qc_a5_probe_mutation`：它的判定 `tools/probe_a5_env.py` 在**本机就是红的**
（7 PASS / 2 FAIL，`至少 1 条 [UI心跳] 被分类 ← 0`：`logs/` 里没有 UI 心跳样本）。
判定恒红 → 任何变异之后它都会红 → 6 把刀「全红」是白送的。
这张表不是垃圾桶，是待办清单，每条都写清"为什么现在还空转、什么条件下能修"。

**结果**

```
check_knife_noop.py          → CERTIFIED 26 / VACUOUS 1 / SKIP 50 / ERROR 2   exit 0（5m56s）
_qc_knife_noop_mutation.py   → 7 刀 + 对照 全 PASS                            exit 0（5m21s）
```

`ERROR` 两把都是环境，不是新缺陷：`_qc_presence_mutation`（上一轮就记过：干净树上
33 PASS / 1 FAIL）与 `_qc_tray_switch_mutation`（判定要 `config.yaml`，在 `.gitignore` 里）。
`SKIP` 五十把是没有模块级 `MUTS` 的——**SKIP 不算通过**。

**边界（必须连着结果一起读）**：空变异只证明「没东西可抓时它不会假红」，
**不证明「有东西可抓时它抓得住」**——后者仍靠每把刀自己的正向刀，以及上一轮那道
`check_knife_vacuity.py`。**这 26 个 CERTIFIED 不许读成"全部刀都验过了"**：79 把里
50 把本工具看不见。

**登记**：`tools/check_knife_noop.py` + `tools/_qc_knife_noop_mutation.py` 进 `check_all.GATES`
（都标慢项），`GATES` 从 215 涨到 217，`site/index.html` 与 `site/PUBLISH.md` 的 4 处锚点
同步改（`check_site_counts.py` 先红后绿，7 PASS / 0 FAIL）。
**这两步故意不进 `ci.yml`**：它们要 `.venv`，CI 里 79 把大多卡在 `ModuleNotFoundError` →
`ERROR`，N7 对照还会因"一把都没跑成"变成 `exit 2` 让 job 恒红。所以它和 dev 层 18 对一样，
只由本机 `check_all.py` 覆盖——`ci.yml` 里写了这句话，免得下次有人把 CI 全绿读成"空变异体检跑过"。

### 2026-10-22 · 克隆面板「预填她的」：试听静默失效 + 克隆画假进度条还谎报「已完成」

**缘起**：收尾「反向核对所有 check/_qc 是否在册」时，顺手把上一轮普查出来的
`src/` 75 处硬编码路径**逐个定性完**（测试夹具 / docstring 墓碑 / 输入框 placeholder /
标准系统位 + exists 先验 / 合法跨盘扫描——全部无害，脚本 `tools/_scan_src_paths_once.py`
留档）。剩下唯一一处真缺陷，在首启建角色的克隆面板：

| 现象 | 根因 |
|---|---|
| 预置角色「温柔女友」的**试听**按钮点了没反应 | 卡片预填 `voice.ref \|\| demo.ref`，按钮只读 `c.voice.ref`；预置角色只有 `demo.ref` → 静默 `return` |
| 「开始克隆」画一条假进度条，然后弹「**克隆完成**」 | 同上：`refPath` 取到空 → 跳过真实克隆，落到「无桥降级」的 `setInterval` 假进度 |
| 卡片副标题显示 `D:/GPT-SoVITS/ref_audio/xiaowei.wav` | 预置 `demo.ref.path` 是**开发机绝对路径**，用户机器上没这个文件 |
| 用户自选文件的试听也没声 | `/local_img` 是**图片**路由：Content-Type 只认图片、白名单只放行 `char_banners`/`char_avatars`+注册目录 |

随包的 `xiaowei.wav`（428KB，`_role_assets/ref/`）本来**就是**「她的原始录音」——
产品意图是预填它、试听它、拿它克隆。坏的只是接线。

**改动**（两端都修，不装饰）：
- `onboarding_role.js`：`_clRef` 模块级单一来源，预填/试听/开始克隆共用；随包资产
  `url`（播放）与物化后的 `path`（引擎）分开持有；**假进度条整段删除**，无桥时明确告知；
  选文件换 `pickReferenceAudio`（校验时长 + 复制到 `data/voice_ref/` 持久，修 B3）
- `webapp.py`：新方法 `use_bundled_ref_audio()` 把随包资产物化成磁盘真实路径，
  白名单（只放行 `_role_assets/` 前缀、拒 `..`、解析后必须在 web 根内）；
  `pick_reference_audio` 补注册 `/local_img` 目录
- `main_window.py`：`/local_img` 补 wav/mp3/flac/ogg/m4a 的音频 Content-Type
- `app_backend.js`：导出 `useBundledRefAudio`

**验证**：jsdom 真值表 **24 项**——`test_clone_card_ui.js` **原样加载**
`onboarding_role.js` 整文件（不抽片段）在假 DOM + 假 `window.Backend` 里真点真走：
S1 随包资产（物化一次、`testVoice` 带真实路径、失败如实报、**没有**「克隆完成」）/
S2 用户文件（`/local_img` 播放、**不**物化）/ S3 无桥（明说没有语音桥、假进度条不展开）/
S4 墓碑（源码里假进度分支与开发机路径都不在）。后端门禁 20 项（物化 + 8 条白名单拒绝 +
目录注册 + 音频类型）。**六刀变异全红**、还原后两道门仍全绿。

**踩过的坑**：①墓碑断言 `src.indexOf('D:/GPT-SoVITS') < 0` 被**我自己注释里复述的
字面量**满足（假红）——「源码级断言会被自己的注释满足」的反面同样成立：注释里别复述
被判断的字面量；②`loadFromBackend` 的 `getCharacters` 会**整体替换** `PRESETS`，
mock 提供它就测不到预置角色；③`pickAudioFile` 返回 `duration`、`pickReferenceAudio`
返回 `duration_s`，换调用必须换字段。

### 2026-10-22 · 队友包里一直带着 1.1MB 开发残留：14 个 `_` 前缀页面

**缘起**：删额度浮层时在 `pages.css` 里发现孤儿 `.qa-*` 规则，顺藤摸到
`_av_test.html`，再一看 `src/ui/web/redesign/` 下**共 14 个 `_` 前缀文件**：

| 文件 | 字节 | 是什么 |
|---|---|---|
| `_settings_pre_iarestructure.html` | 350,757 | IA 重构前的旧设置页备份 |
| `_theme_editor_v5.js` | 139,747 | 主题编辑器 v5 迭代稿 |
| `_av_test.html` | 119,127 | 音视频联调测试页 |
| `_theme_editor_design.html` | 47,971 | 设计稿（早期） |
| `_te_real_chat.html` | 42,937 | 真实聊天数据测试台 |
| …（其余 9 个见 `EXCLUDE_DEAD_WEB`） | | |

全仓零引用，却按 `git ls-files` 一路进队友的包。**安全先查**：密钥形态 / 绝对路径 /
手机号 / token / cookie 全扫过——干净（`localhost:11434` 只是 Ollama 默认
placeholder，`access_token: $('x').value` 是**表单读取**不是硬编码）。所以是干净的
残留，不是安全事故。

**为什么值得管**：体积只占 0.27%（包体 408MB），但队友解包后能在 URL 里打开这些
页面，看到的是**能点但没反应**的旧 UI——「假接口博物馆」。

**关键设计：不用前缀一刀切。** `src/ui/web/_gpu.txt` **同样带 `_` 前缀**，但它是
`proactive._has_usable_gpu()` 写的运行时数据（虚拟显示器清单），排了会坏功能。
所以规则是**精确路径清单** `EXCLUDE_DEAD_WEB`，每条写原因。

**判据三层**（`test_pack_for_teammate.py`）：

1. [1b] 清单里每个都必须被 `_exclude_reason` 排除，**逐个断言**——不是数数量，
   数数量的话清单少一条、包里多一条，数字还对得上
2. [2] **对照组**：`_gpu.txt` 必须保留。没有这一条，"按前缀一刀切"也能让 [1b] 全绿
3. [3b] **反向催**：扫 `redesign/` 下所有 `_` 前缀文件，未登记的要红；登记表里已
   消失的条目（stale）也要红

**变异刀又抓到一个「空断言」**：M1（清空清单）第一次只报 **1 条 ✗**，而 M2（删判定）
报 15 条。查下来 [1b] 的循环是 `for rel in EXCLUDE_DEAD_WEB.items()`——**清单被清空
时循环空转，一条都不报**，看着像全绿；那 1 条来自 [3b] 的反向检查。修法：循环前加
`check(len(...) >= 10, "清单非空")`。

这与"锚点命中 0 次就静默跳过"是**同一种失效**：判据的输入没了，它不会喊。
**凡是 `for x in <某清单>` 做断言的判据，都要先断言清单非空。**

**四刀变异**（`tools/_qc_pack_exclude_mutation.py`）：

| 刀 | 改动 | 预期 |
|---|---|---|
| M1 | 清空 `EXCLUDE_DEAD_WEB` | 红（清单非空 + 未登记） |
| M2 | 删掉 `_exclude_reason` 那条判定 | 红（15 条 ✗） |
| M3 | 改成 `if low.startswith("_")` 一刀切 | 红，且必须红在 **`_gpu.txt` 被误排** |
| M4 | `DEAD_WEB_SCAN_DIR` 指向不存在 | 报「找不到扫描目录」 |

M3 是全场关键：它证明"把规则放宽成一刀切"会被抓到，而不是让 [1b] 照样绿、
而 `_gpu.txt` 已经被静默排掉。

**验证**：全量门禁 **130/130**（9m29s，新增「打包排除变异」）；
`--dry-run` 实测排除 14 项、入包 788 个（仓库跟踪 802 个），排除原因按条打印。


**问题**：10-21 留下一条待裁决——`window.__quotaAlert` 浮层"当前不可达"，
倾向连浮层一起删约 150 行，复核条件是"下次动额度相关代码时一起裁决"。
本轮动额度代码，逐层查下来发现"不可达"这个词太轻了：

| 层 | 有什么 | 实际 |
|---|---|---|
| Router | `set_quota_alert_callback` 槽位 | 有槽位，`_fire_quota_alert` **从不调**（10-02 决策：只记状态） |
| webapp | 注册回调 + `_on_vision_quota_alert` | 注册了，但存根只 `logger.info`，**从不广播** |
| 前端 | 浮层 + `quota_alert` 广播分支 | 有 UI，但后端广播 type 全集里**从来没有这个 type** |

后端广播 type 全集（`_wv2_broadcast` 13 个调用点 + `agent_confirm_broker` 注入的
`agent_confirm` + `ws_server.broadcast` 的 `character_switched`）：
`chat_push` / `call_push` / `pet_tip` / `video_watch` / `agent_confirm` /
`character_switched`。**没有 `quota_alert`。**

这就是「假接口」的复合形态：每一段单独看都在工作（槽位在、注册在、UI 在），
合起来是死的。10-02 停掉主动弹窗时只拆了 Router 那一段的"调用"，另外两段留着，
于是一个被否决的行为在界面上还留着一座完整的、点不亮的房子。

**改动**：

- 删 `app_backend.js` 浮层 IIFE 141 行 + 广播分支（留"为什么删"的注释）
- 删 `pages.css` `.qa-*` 25 行
- 删 `webapp.py` 注册点 7 行 + 存根方法 21 行
- **保留** `VisionRouter._fire_quota_alert` / `set_quota_alert_callback` /
  `quota_state()`——墓碑测试指着它们，且 `quota_state()` 是将来"用户显式点
  检测额度"的唯一数据源（现在零出口，但那是另一件事）

**第二层墓碑**：原来 `test_quota_alert.py` 只有 Router 层墓碑
（`test_alert_does_not_fire_proactively` 断言"回调不被调用"）。
**只测 Router 层不够**——注册点写着"主动广播给前端弹询问浮层"，谁把它加回来，
Router 层那条仍然绿，而主动打扰已经铺回产品里了。补两条：

- `test_product_code_does_not_register_proactive_popup`：webapp 里不许出现
  `set_quota_alert_callback` / `_on_vision_quota_alert`（`tokenize` 剥注释与字符串）
- `test_frontend_has_no_quota_alert_branch`：`app_backend.js` 里不许出现 `quota_alert`

**本轮最值钱：剥注释的"顺序" bug**。第一版判据用两条正则剥 JS 注释，先剥行注释：

```python
txt = re.sub(r"(?<![:\w])//.*$", "", txt, flags=re.M)   # 先剥行注释
txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)          # 再剥块注释
```

块注释里只要含 `//`（URL 最常见），第一条就从那儿删到行尾，**把同行的 `*/` 一起
删掉** → 块注释变成"只有 `/*` 没有 `*/`" → 第二条非贪婪匹配错位 → **吃掉后面大段
真实代码**。变异刀 M2（把 `quota_alert` 分支加回前端）因此**假绿**过一次。

修法：状态机一次性扫描，先判 `/*` 再判 `//`。
`check_bridge_guards.py` 的顺序本来是对的，但**没有判据守着这个顺序** → 补 M6：
插一个含 `https://` 的跨行块注释，除"必须绿"之外还断言**守卫总数不变**。
顺序颠倒时闸门确实还绿，但真实守卫被吃掉、总数会少——M4 抓不到它（行注释在两种
顺序下都被剥掉、都是绿）。这正是"覆盖数要打印出来看"的又一处用处。

**另外两个刀失效**（都与 10-21 的 `_qc_api_wiring_mutation` M3 同型）：

- `_qc_bridge_guards_mutation` M1 写死删 `probeVisionModel` 导出，而那个 bug 修好后
  页面里已经没有守卫指向它 → 删掉导出闸门仍然绿、**刀悄悄失效**。改成按
  `guards_in()` × `exported()` 交集动态取（现在取到 `getQuotaStatus`）。
- `_qc_quota_tombstone_mutation` M2 第一版锚点选在注释块**中间**一行，注入的代码落在
  `/* ... */` 内部、被剥注释正确剔除 → 判据绿是**对的**，刀却报 FAIL。插到注释
  结束之后即可。教训：变异刀要模拟"代码里用"，锚点必须在注释**外**。

**验证**：全量门禁 **129/129**（9m14s，新增「额度墓碑变异」）；
`test_quota_alert` 13 → 15 项；`_qc_quota_tombstone_mutation` 三刀全过；
`_qc_bridge_guards_mutation` 六刀全过。

**新发现（待裁决）**：`src/ui/web/redesign/_av_test.html` 1752 行、无人引用、
却被 git 跟踪进发布包，里面还有孤儿 `.qa-*` CSS。
**复核条件：下次做出包 / 验包时，一起裁决这个测试页该不该进包。**


第一轮全量门禁 **127/128**，红的是「反向接线变异」：

```
FAIL 清单里找不到可在 webapp.py 里改名的方法
```

#### 根因：同一个坑犯了第二次

M3（把清单里的方法改名 → 闸门必须报「② 后端已没有这个方法」）选目标时**只搜
`src/ui/webapp.py`**。

第一次失效是把 `enable_tray` 写死——它后来被判定为假接口删掉，锚点命中 0 次。
改成"从 `KNOWN_UNWIRED` 现取"之后我以为好了，**但文件位置仍然写死**。清单从
12 → 5 → 3，剩下 `clear_pet_cache` / `pet_hit_rect` / `prevent_window_close`
全定义在 `api_pet.py` / `api_window.py`，`webapp.py` 里一个都找不到。

值得记的是：**这次是门禁自己抓到的**。`_pick_rename_target` 找不到目标时
`raise SystemExit` 而不是静默跳过，所以整轮报红——如果当初图省事写成
`return None` + 上游 `continue`，这把刀就从"能抓错"变成"永远绿"。

#### 修法：按 MRO 找定义所在文件

```python
for klass in CharacterAPI.__mro__:          # 不是 type(CharacterAPI).__mro__
    if n not in klass.__dict__: continue
    path = inspect.getsourcefile(klass)
    ...
```

`backend_methods()` 用的就是 `dir(CharacterAPI)`，两边口径一致 —— 方法搬去哪个
mixin、清单怎么变，这一刀都成立。

#### 两个自己踩的

1. **`type(CharacterAPI).__mro__` 对普通类只给出 `['type', 'object']`**，一个名字
   都找不到。第一版就这么写的，脚本报错才发现。要的是 `CharacterAPI.__mro__`。
2. `os.path.relpath` 跨盘符抛 `ValueError` —— 加了 `except`（`doctor.py` 上踩过同款）。

现在三刀全绿，M3 报的是「② `clear_pet_cache`（`src/ui/api_pet.py`）」。
**全量门禁 128/128。**

### 2026-10-21 · 关系面板的「原型 温柔 ·自动」原来改不了：两个后端方法早写好了，入口一直没建

原型那一行显示「温柔 ·自动」——「自动」两个字本身就在说"手动覆盖是设计里的东西"。
但关系面板只有"上次事件"一行文字，既没有事件列表，也没有手动改原型的入口：
`set_archetype_manual` 和 `get_relation_events` 都是**有实现、没出口**。

#### 改动

- `get_relationship_state` 新增 `archetypes` 字段，直接给 `ARCHETYPES` 的真 key。
  **前端不写死枚举**——本仓吃过 `MOODS` 的亏（判据抄 v1 六种、运行时 v2 十七种
  → 凌晨红白天绿）。
- `set_archetype_manual` 补 docstring，并显式返回 `archetype_inferred: False`。
- 面板加原型下拉 `#relArchSel`（手动指定后不再自动推断）+ 底部「最近事件」列表
  `#relEventList`（最近 6 条，最近的在上）。

#### 两个实现上的选择

1. `fillArchetypes()` 用 `sel._optsKey` 缓存选项串——面板每几秒轮询一次
   `get_relationship_state`，不缓存就会每次重建 `<option>`，把用户正在选的那一项
   重置掉。
2. `renderEvents()` 走 `textContent` 而不是 `innerHTML`：事件 `detail` 是用户/模型
   产生的内容，可能带尖括号。

#### 验证

`tools/test_relation_panel.js` 24 项 6 场景，用 `vm.runInNewContext` **原样加载
`relation_panel.js`**（不是复刻逻辑）——Python 复刻只证明"我两次写得一样"。

两个对照组：

- **老后端没有 `archetypes`**：下拉要禁用并写清原因，**不许**写死一份枚举兜底
  （否则后端加了新原型，界面上永远看不到）。
- **后端没连上**（`get_relationship_state` 抛异常）：不许把下拉变成空壳——占位文案
  要留着。

变异 4 刀全红：调错后端方法名 / 下拉改成写死枚举 / 事件行走 `innerHTML` /
不再拉事件列表。

#### 顺手：反向接线清单 5 → 3

`get_relation_events` / `set_archetype_manual` 从清单里划掉。剩下 3 个：
`clear_pet_cache` / `prevent_window_close`（能力存在但没有对应用户动作）、
`pet_hit_rect`（纯 no-op，已裁决保留——它是"点击穿透只认角色像素"的唯一落点）。

闸门 126 → **128**。

### 2026-10-21 · `typeof Backend.x === 'function'` 这种守卫，没人查过 x 到底在不在

额度提醒浮层有个「再试一次」按钮（用户在控制台充值后点一下，不必等冷却过期）。点它
**什么都不发生**：按钮把自己恢复成「再试一次」，不发请求、不提示。

#### 问题：守卫把「版本没有」和「名字写错」压成了同一种结果

```js
if (typeof Backend.probeVisionModel === 'function') {
  Backend.probeVisionModel(mid).then(done);          // ← 永远不执行
} else {
  btn.disabled = false; btn.textContent = '再试一次'; // ← 静默，零反馈
}
```

后端 `probe_vision_model` 一直在（返回 `{ok, hint, error}`），`app_backend.js` 里也
写好了注释"pywebview 自动 snake→camel"，**但从没把它放进导出对象** → `Backend.
probeVisionModel` 是 `undefined` → 守卫恒 false。

这不是第一次：同一个文件里 `switchVisionModel` 就是这么坏的（注释里留着"那句 typeof
因漏导出恒 false"），Python 侧也有一模一样的形态（`hasattr(cfg,'refresh_characters_map')`
—— 方法名写错，`hasattr` 恒 False，见同一天的 CHANGELOG）。规律很清楚：
**守卫本身没错，错的是"守卫的目标不在导出表里"这件事没人查。**

#### 改动

1. 补导出：`const probeVisionModel = (modelId) => call('probe_vision_model', modelId || '')`
   + 加进导出对象，并在导出段那条"历史上漏导出"的清单里记一笔。
2. 新闸门 `tools/check_bridge_guards.py`：解析 `app_backend.js` 的 `return { … }` 得到
   导出名集合（**复用** `check_bridge_methods.exported()`，不写第二套解析），扫全部真
   页面/真 JS 的 `typeof (window.)?(Backend|B).<name>`，`<name>` 不在集合里就红。

**`if (B.x)` 也查**：先量了一遍误报（0 处），而且这个形态今天已经骗过一次 —— 删掉的
那段平行绑定就是 `if (B.setArchetypeManual)`，守卫恒 false，后端没调却 toast「关系已
重置」。收进来后全站从 138 处涨到 **220 处**。

#### 验证

四刀变异，其中两刀守的是闸门自己：

| 刀 | 内容 | 期望 |
|---|---|---|
| M1 | 删掉导出表里的 `probeVisionModel,`（今天这个 bug 的原样） | 红，点名它 |
| M2 | `notify.html` 里塞 `typeof Backend.zzzNotARealBridge` | 红，点名它 |
| M3 | 把守卫正则换成永不匹配的模式 | **exit 2**（0 命中不许当成通过） |
| M4 | 对照组：注释里复述守卫名 | **仍然绿** |
| M5 | `if (B.zzzIfGuard)` | 红，点名它 |

M3 是这类闸门最阴的失效方式：正则坏了 → 0 命中 → 没有违规 → 打印"✓"退出 0。所以
闸门里有 `MIN_GUARDS`（扫不到 20 处就 exit 2）和 `MIN_EXPORTS`（解析不足 100 个名字就
exit 2），M3 就是验证这两个兜底真的在。

**M4 第一次跑是红的**，暴露了判据本身的漏洞：我只剥"整行以 `//` 开头"的注释，
`<script>// typeof Backend.x …` 这种**行尾注释**被当成真守卫 → 误报。改成
`re.sub(r"(?<![:\w])//.*$", "", line)`（放过 `http://`），M4 才绿。
**对照组不是走过场 —— 它是唯一能发现"剥注释只剥了一半"的东西。**

#### 仍然待处置（写下来，免得变成第二个「发现」栏）

`window.__quotaAlert` 那个浮层**当前不可达**：`vision_router._fire_quota_alert` 自
2026-10-02 起只记日志、不再回调（用户明确停掉"额度耗尽主动弹窗"）。所以即使按钮修好，
浮层也不会自己弹出来。两条路：①连浮层一起删（约 150 行，连同 `_on_vision_quota_alert`
那个 no-op 存根）；②保留，等"用户主动点『检测额度』"这个入口真做出来。**倾向 ①**，
但它要动前端一大块，单独一轮做。**复核条件：下次动额度相关代码时一起裁决。**

### 2026-10-21 · 反向接线清单 12 → 5：六个接口是「同一份数据的第二个出口」

上一轮把 5 个**假接口**（说成功、其实什么都没做）删了。这一轮处理的是另一种：**接口
本身是诚实的，但界面上已经有另一条路能拿到同样的数据** —— 留着就是两个出口，将来两份
数字打架时用户没有能力判断该信哪个。

#### 四类证据

**① 全是 `get_vision_debug()` 的子集（4 个）**

`get_vision_debug()` 每 1s 被调试台轮询，它已经带了 `ocr: ocr.snapshot()`、
`audio: audio.snapshot()`（其中 `references` 就是 `reference_status()`）、
`chat_overlay_open`。于是：

| 删掉的 | 它返回的字段 | 已经在哪 |
|---|---|---|
| `ocr_get_regions` | regions / region_texts / player_name | `["ocr"]` |
| `ocr_get_anchors` | anchors / region_texts / anchor_state / regions | `["ocr"]` |
| `get_audio_status` | `audio.reference_status()` | `["audio"]["references"]` |
| `chat_overlay_state` | `open` / `enabled` | `["chat_overlay_open"]`（`enabled` 是内部配置，`_overlay_cfg()` 自己读写） |

**② 被产品决策废弃的（1 个）**

`get_bailian_quota` 返回本地记账估算。2026-09-29 额度卡已改成"应用内嵌浏览器看真实
额度，不再本地估算"（设置页 `loadBailianConsoleStatus` + `get_bailian_real_quota` +
`get_quota_status`）。从那以后零调用 —— 留着它等于给"本地估算"留一个随时会被误接回
界面的入口。

**③ 写死清单（1 个）**

`get_model_3d_options` 写死 mmd / vrm 两条。界面上从来没有"选 3D 格式"的下拉（旧页
`app.js` 只是把 `char.model_3d.format` 原样显示），与已删的 `get_backend_options` 同型。

#### 留下的 5 个，各自都有明确的"为什么还没接"

- `get_relation_events` / `set_archetype_manual`：关系面板现在只显示"上次事件"和
  `原型 ·自动` 标签，没有事件列表、也没有手动改原型的入口。**这是真缺口**（标签写着
  "自动"，说明手动覆盖是设计里的东西），要动面板 UI，单独一轮做。
  **（已于同日接线，见上面「关系面板的『原型 温柔 ·自动』原来改不了」。）**
- `clear_pet_cache` / `prevent_window_close`：能力存在，但没有对应的用户动作。
- `pet_hit_rect`：纯 no-op（`native.set_pet_hit_rect` 全仓不存在），倾向删。

#### 验证

反向接线闸门：后端公开 315（原 325）/ 有人调 310 / 未接线 **5**（原 12）；三刀变异
全红（加没人调的方法 / 改名 / 给清单项加真实调用点）；桥接闸门 46 页 166 个调用三层
全对得上；前端 JS 语法 88 段 0 错。

### 2026-10-21 · 导入冒烟复核：范围漏了一半，测试还差点变成空断言

上一条修法有个洞：复核只遍历 `unexpected`（非预期失败），而**白名单里的模块同样会因为
并发抢内存而假红** —— 它永远不会进复核，于是「白名单内 N 条」里的 N 在骗人（它让
"这条白名单还该不该留着"这个判断失去依据）。改成复核全部失败项。

顺带两个自己踩的坑：

1. **测试差点是空断言**：第 6 组把 `flaky` 写进白名单再跑一次，断言「白名单内 0」。
   但 `flaky` 靠 `.seen` 标记决定"第一次失败"，而第一次跑已经写下标记 → 第二次它直接
   成功、压根不进复核 → 那条断言**恒真**。必须先删标记。现在前面加了一条前置断言
   （"确实进了复核"），它红的时候后面那条才有意义。
2. **M2 刀失效**：改代码时缩进从 12 空格变 8 空格，锚点命中 0 次。脚本按"命中 0 次
   → FAIL"报了出来（而不是静默跳过），锚点改成 8 空格后三刀全红。**这正是当初把
   "锚点命中 0 次"设计成红而不是跳过的价值。**

### 2026-10-21 · 设置页的「录音」按钮，录的是扬声器不是麦克风

设置页有「录一段参考音」按钮，点了永远失败。查下来不是设备问题，是**两个方向相反的
动作共用了同一个名字**。

#### 问题：`record_audio_reference` 录的是系统输出

```python
def record_audio_reference(self, seconds: float = 2.0) -> dict:
    """录制系统输出音频（loopback），供游戏音效采集使用。"""
```

它要的是「把电脑正在播的声音录下来」（给游戏音效 / 视频观看用的），而设置页要的是
「用麦克风录下用户的声音」当参考音。两件事的设备方向正好相反。更糟的是后端的事件
白名单里只认 `'tts'` / `'asr'` 这类来源，前端传的 `'reference'` 不在里面 → 就算设备
对了也走不通。于是这个按钮的真实行为是：**点了 → 报错 → 用户以为是麦克风坏了**。

#### 改动

- 新模块 `src/audio/voice_ref_record.py`：`pyaudiowpatch` 优先、`pyaudio` 兜底；
  立体声用 `_downmix_to_mono()` 下混；`exception_on_overflow=False` 读帧（否则缓冲
  一满就抛，长录音必崩）；时长收进 `1.5~30s` 窗口，`NaN`/0/负/乱字符串一律回默认。
  **失败不抛异常**，返回 `{"success": False, "error": "录音失败：…（可改用「选择音频
  文件」上传）"}` —— 设置页有上传兜底，不该让用户卡在一个报错里。
- 桥方法 `record_voice_reference(seconds)`：写 `data/voice_ref/ref_<时间戳>_<序号>.wav`。
  序号用模块级 `itertools.count(1)` + `os.path.exists` 兜底 —— 同一毫秒内连点两次
  原本会撞名（这是变异刀抓出来的**真 bug**，不是测试问题）。
- 前端：`app_backend.js` 的 `recordAudio` 改调 `record_voice_reference`，并兼容旧的
  `recordAudio('reference', 2.5)` 两参写法；设置页两处调用改成 `recordAudio(2.5)`。

#### 验证

- `tools/test_voice_ref_record.py` **38 项**，全程假设备（`_FakePa` / `_FakeStream`）：
  时长窗口 6 组、单声道帧数与格式、立体声下混后字节数 = `44 + 帧数×2`、请求 99 秒
  实际只录 30 秒、失败如实上报、以及一条**墓碑**：假 `record_audio_reference` 一旦被
  调到就 `AssertionError`（防止有人"顺手"把旧路径接回来）。
- 三刀变异全红：去掉时长上限 → 帧数变 99 秒；`if channels == 2:` → `if False:` →
  下混失效；`if not r.get("success"):` → `if False:` → 失败也报成功。
- 真机实跑 2 秒：44100Hz / 单声道 / 88064 帧 / 176172 字节，文件正常。但 `peak=2`、
  `rms=0.2` —— **本机麦克风静音或被占用**，不是代码问题；失败路径会如实报错。

#### 踩过的坑

- **测试往真仓库目录写产物**：第一版假实现照着产品逻辑往 `data/voice_ref/` 写了 4 个
  真 wav。改成假实现只写临时目录，断言改看"产品想写到哪"（`made["asked_N"]`）。
- 测试里 `_frames_for` 少乘 `_CHUNK`，断言 39936 帧 vs 实际 39 块，4 项 FAIL —— 错的
  是判据不是产品。

### 2026-10-21 · 导入冒烟那 3 个红是并发抢内存；修法必须窄到"只认一种错误文案"

全量门禁跑出 3 个红：`src.llm.minicpm_duplex` / `src.perception.process_loopback` /
`src.voice.tts`，都报 `OpenBLAS error: Memory allocation still failed`。本机空闲内存
只有 2.4G，`smoke_import` 默认 8 并发 import numpy/scipy，抢不到连续地址空间。

#### 为什么不能"失败就重试"

重试是**最危险的一类修法**：它会让真缺陷变成"偶发红"，而偶发红的下场是被忽略。
所以允许复核的条件必须窄到「有外部证据的特定错误文案」：

```python
_MEM_FAIL_MARK = "Memory allocation still failed"
def is_mem_contention(result) -> bool:
    return _MEM_FAIL_MARK in (result.get("stderr") or "") + (result.get("stdout") or "")
```

- 只复核**命中这个文案**的项，其余失败原样计入。
- 复核**串行**跑（并发才是原因）。
- 复核后**重算** `fails` / `kinds` —— 不重算就会印出"失败 3 条"而实际 0 条，那是谎话。
- 打印复核了几条、救回几条，否则下次没人知道这条逻辑有没有在跑。

#### 验证

`tools/test_smoke_import_retry.py` **12 项**，用独立假根 `src/fixturepkg/`：`ok.py`
（过）、`flaky.py`（首次失败、二次过）、`memfail.py`（永远报同样文案）、`real.py`
（真缺陷，文案不同）。断言的核心是 `real` **不许**带"串行复核仍失败"标记、`memfail`
必须带、非预期失败数 = 2；第 6 组把 `flaky` 写进白名单再跑一次，断言「白名单内 N 条」
在它被救回后是 0（`known` 原本在复核**之前**算，不重算就会印出 1 —— 判决对、数字错）。

三刀变异全红：`is_mem_contention` → `return True`（红"real 没有复核标记"）；
`if again["status"] == "OK":` → `if True:`（红"非预期失败 2"）—— 这一刀最危险，真失败
被洗出判决，单看 JSON 完全发现不了；删掉 `known` 的重算（红"「白名单内」是 0"）。

实测：1 条被救回，`非预期失败 0`，退出码 0。

### 2026-10-21 · 自测把假文件建在仓库里；被打断就留下垃圾

`test_doctor` 为了测 WebView2 / UCRT / 代码签名，要在磁盘上造假的 `System32`、假 PE
文件、假包根，以及一份**故意写坏的** YAML。这些路径原本直接指向 `tools/` 和仓库根。

正常跑完会删（`finally` 里逐个 `os.remove`），但上一轮全量门禁被 SIGTERM 打断后，
仓库里留下了：

```
?? _broken_cfg_for_test.yaml
?? tools/_doctor_tmp_sys32/ucrtbase.dll
```

**Windows 上 TerminateProcess 不走 finally**，所以"跑完会清"这个假设在被打断时不成立。
下次跑门禁时它们就成了"来历不明的产物"。

#### 改动

- 三处假根全部改到 `tempfile.mkdtemp()`（坏 YAML 单独一个目录，假 sys32 / 假 PE / 假
  包根共用一个），末尾 `shutil.rmtree(..., ignore_errors=True)`。中断只脏 `%TEMP%`。
- 顺带修一个被这次改动暴露的脆弱点：`check_signature` 里 `os.path.relpath(p, ROOT)`
  在**跨盘符**时抛 `ValueError`（假 exe 现在在 C:，`ROOT` 在 D:）→ 整个 doctor 崩。
  抽 `_rel()`：异常时退回绝对路径。展示用路径不值得让自检挂掉。

#### 验证

doctor 自检 16 组全绿；跑完 `git status` 无未跟踪产物。

### 2026-10-21 · 反向接线清单里的 5 个，不是「缺入口」，是假接口

反向接线闸门的清单只有两种合法处置：**接线，或删掉**。上一轮接线了 3 个（关于页），
这一轮把 5 个判成"删"——它们不是"后端写完了、界面没建"，而是**界面上建了也是谎话**。

#### 三类证据

**① 改一个没人读的字段，然后报成功**

```python
def enable_tray(self, enabled: bool = True) -> dict:
    self._tray_enabled = enabled          # ← 只被 minimize_to_tray / is_tray_enabled 读
    return {"ok": True, "enabled": enabled}
```

真正的托盘在 `main.py`：`TrayManager` 无条件启动，「点 X → 隐藏到托盘」由
`events.closing` 里的 `_on_main_closing` 实现，跟这三个方法毫无关系。前端调
`enable_tray(False)` 会被告知"已关闭托盘"，实际托盘照旧在。同型的
`set_minimize_to_tray` 改的是 `_minimize_to_tray`，而那个字段**全仓没有第二个读点**。

**② 调一个已经不存在的函数**

```python
def pop_notify_cmd(self) -> list:
    """…由桌宠页 rig/index.html 每 ~300ms 调用一次…"""
    try:
        return mode_notify.drain_cmds()
    except Exception:
        return []
```

`mode_notify` 的模块注释写着"本模块不再有任何 WebView2 / js_api / **命令队列**"
（通知卡已改成原生窗 `native_notify`）—— `drain_cmds` 早就不存在了。于是这个方法的
真实行为是：**抛 AttributeError → 被 `except` 吞掉 → 永远返回 `[]`**。它连"没有积压"
都不是在回答，是在假装回答。而 docstring 还描述着一个不存在的轮询方。

**③ 写死一份已经过期的清单**

`get_backend_options()` 返回 4 条 TTS 引擎，`pyttsx3` 出现两次（"系统语音(本地离线)"
和"pyttsx3(离线降级)"是同一样东西），而 `src/models/protocols.py: TTS_ENGINES` 里有
**10** 个、并且由 `check_models_layer` 守着（10-16 已按发布口径摘掉 edge-tts）。
把它接到下拉框上，用户会看到重复项和缺失项。

#### 为什么"接线"是错的方向

这一批如果硬接线，等于**把谎话搬到用户眼前**：多一个托盘开关（点了没反应）、多一个
"通知卡"通道（永远空）、多一个引擎下拉（重复项）。清单的语义是"后端有能力、缺出口"，
而这五个连"能力"都没有。所以删，并把理由写在删除位置（不是悄悄删掉，下一个人还得
再查一遍）。

#### 副产品：`main.py` 里一个没人读的字段

`Runtime.minimize_to_tray: bool = False  # 是否最小化到托盘而非退出` —— 全仓零读点，
而"点 X 隐藏到托盘"是无条件行为。留着它，下一个人会以为改它能改行为；`webapp.py` 里
那个同名假接口多半就是这么长出来的。一并删除。

#### 保留的两个，以及为什么

`minimize_to_tray()` / `is_tray_enabled()` 留着：`src/ui/web/app.js`（legacy 页）真的
在调，而且它们的方向是**保守**的 —— `_tray_enabled` 默认 False，于是
`minimize_to_tray()` 会拒绝隐藏窗口（"系统托盘未启用"）而不是隐藏了找不回来。要让它们
跟着真实托盘状态走，得把 `TrayManager` 注入进 API（`set_runtime` 目前不传），那是
另一个改动，不是删两行能解决的。

#### 变异刀自己差点变成假绿

`_qc_api_wiring_mutation.py` 的 M3 写死了 `enable_tray`（改名 → 必须报 ②）。这轮把它
删了，M3 立刻变成"锚点命中 0 次"——按原逻辑那是**跳过**，闸门照样绿。和当初 M2 写死
`get_version` 是同一个坑：**变异脚本里不许出现"当前清单长什么样"的假设**。改成从
`KNOWN_UNWIRED` 现取一个在 `webapp.py` 里真有定义的名字（现在取到
`chat_overlay_state`）。

#### 验证

- 反向接线闸门：后端公开 **320**（原 325）/ 有人调 308 / 未接线 **12**（原 17）
- 三刀变异全红（M1 加没人调的方法 → ③；M3 改名 → ② `chat_overlay_state`；
  M2 加真实调用点 → ① `chat_overlay_state`）
- 全量闸门 **120/120**（7m41s）；`smoke_import --strict` 通过

### 2026-10-21 · 全站 45 个页面的脚本，从来没人查过语法

`check_web_js_syntax.py` 的写法是：

```python
html = io.open("src/ui/web/index.html", ...).read()          # 只有这一个页面
for rel in ["src/ui/web/redesign/app_backend.js",            # 只有这两个文件
            "src/ui/web/redesign/spa.js"]:
```

而 `src/ui/web` 下**46 个**页面、**23 个**外链 JS。也就是说：设置页的 8 个内联块、
引导页的 4 个块、通知卡、桌宠 rig 页、知识库页…… 全部不在检查范围里。它们的脚本写坏了，
这道闸门照样绿。

语法错误的可怕之处不是报错，是**不报错**：一整块 `<script>` 解析失败，浏览器直接跳过，
于是同一块里所有的 `addEventListener`、所有初始化全部不发生 —— 页面上看不出任何异常，
只有控制台一行。历史上「工具栏按钮全部无效」就是这么来的。

#### 扩完立刻抓到第一处

```
!! 语法错误 src/ui/web/probe2.html 内联块#0（第 4 行起）
   SyntaxError: Invalid or unexpected token
```

`probe2.html` 是手工调试稿，但它坏得货真价实：

```js
out.push('溢出检查: ...');
document.getElementById('o').textContent=out.join('      ← 字符串里的 \n 被写成了真换行
');
```

那一整块**从写下来那天起就没执行过**。修法是把这个真换行换回 `'\n'`（二进制替换，
文件是 CRLF，用文本模式写回会顺手改掉全文件的换行符）。

#### 两个实现决定，都不是随手定的

**一个 node 进程解析全部 88 段。** 逐段 `node --check` 实测 **21 秒**（Windows 起一次
node 约 230ms），够把这道门禁挤成慢项；改用 `vm.Script` / `vm.SourceTextModule` 在
一个进程里编译（**只编译、不执行**，同一个 V8 解析器）后是 **1.9 秒**。

**结果协议改成逐行 JSON。** 语法错误信息里会带**出错那行源码**，而那行源码本身可能含
换行（正是上面这种错误）。裸文本协议会被它撕开 → 少回一条 → 那条被当成"没报错"。

#### 更贵的一课：`node --check` 的自动探测把两种解析目标抹平了

第一版直接用 `new vm.Script` 编译所有段，立刻红了两处：

```
first_launch_setup.html 内联块#0: import { createMorph } from './redesign/morphicons/dom.js';
redesign/morph_icons.js:          import { createMorph } from './morphicons/dom.js';
```

这两处**不是缺陷**：前者是 `<script type="module">`，后者在 8 个页面里全部由
`<script type="module" src=...>` 加载。而之前用 `node --check` 时它们一直是绿的 ——
因为 Node 22 的 `--check` 在 CJS 解析失败后**会自动按 ESM 重试**（syntax detection）。

这个"宽容"正是问题所在：它把 `<script>` 里写了 `import` 的真缺陷一起放过了。所以改成
**按加载上下文定解析目标**：

| 载体 | 目标怎么定 |
|---|---|
| 内联块 | 看这个 `<script>` 有没有 `type="module"` |
| 外链 JS | 看**引用它的页面**怎么加载（扫全部页面的 `<script src>` 反查） |
| 没人直接引的 JS | 按内容判（有顶层 `import`/`export` 就是 module） |
| 同一文件两种加载方式 | **直接判红**（浏览器里必有一种 SyntaxError） |

用 `vm.SourceTextModule` 解析模块段（需要 `--experimental-vm-modules`，只解析不执行）；
拿不到它时 runner 直接 exit 2，**不静默降级成 script 目标**。

#### 排除规则不再各写一套

原来"哪些文件算真页面"这条判据在两个闸门里各有一份。现在提到
`check_bridge_methods.is_draft_html / is_draft_js`，`check_web_js_syntax` 直接 import。
唯一的差别写清了原因：**`app_backend.js` 在桥接闸门里要排除**（它是被比对的对象），
**在语法闸门里必须查**（它语法坏了整个前端全哑）。

#### 覆盖范围自检（防「闸门沉默」）

- `MUST_SCAN`：`index.html` / 设置页 / 引导页 / `app_backend.js` 少扫一个就判红；
- 一段都没抽到 → 判红（不是"0 错通过"）；
- runner 少回一条结果 → 判红（**少回 ≠ 没报错**）；
- runner 自检：两种目标都必须拒绝 `var a = ;`，模块目标还必须接受 `export`。

#### 验证

- 三刀变异全红：非首页内联块（`notify.html`）/ 原名单外的外链 JS
  （`onboarding_role.js`）/ **首页的非第一块**（`index.html 内联块#11`，断言里
  明确排除"只报块#0"这种退化）
- 块序号**动态算**，不写死数字 —— 写死就是给自己埋一个"还原后也绿"的假绿
- 全量闸门 **120/120**（8m28s），其中「前端 JS 语法」1.1s、「前端 JS 语法变异」6.1s

### 2026-10-21 · 「关于」页从来没建过：两段注释都写着它该存在

反向接线闸门跑出来的 20 个「有实现、没出口」里，挑了两个最扎眼的：

```python
def get_version(self) -> dict:
    """【阶段 B1】版本号 / 构建号（给关于页与排障用）。"""

def get_autostart(self) -> dict:
    """【阶段 E】开机自启状态（UI 设置开关读取）。"""
```

**注释里承诺了一个界面，而那个界面从来没建过。** 排障时第一个要问的就是"你跑的是哪个
构建"，而软件里根本没有地方能回答；开机自启写了注册表读写、写了 config 双写、写了
"开发环境置灰"的分支，用户却找不到开关。

#### 做了什么

设置页新增 `about` 分类（`TIERS` / `TIER_META` / 子导航 / `<div class="tier" id="about">`），
适配层补三个导出（`getVersion` / `getAutostart` / `setAutostart`；**无参方法不能塞 `{}`**）。

**闸门自己催我删清单**：接完线再跑 `check_api_wiring.py`，它立刻报
「① get_version：现在有人调了，请从 KNOWN_UNWIRED 删掉」。那条 stale 规矩不是摆设 ——
它替我记住了"这条已经不用跟踪了"，而不是靠我自己想。

#### 写的时候还是把三态写成了二态

第一版：

```js
SUP = !!(r && r.ok && r.supported);
say(SUP ? '' : '当前是开发环境（.venv），只有安装版能设置开机自启');
```

`call()` 在没连上后端时返回 `{success:false, offline:true}`（`ok` 根本不存在），
于是**安装版里后端没起来**也会被告知"当前是开发环境"——那是假话，而且会把人往
错误方向带（去查 .venv，而不是去查后端为什么没起来）。

改成三态：`r.ok !== true` → `SUP = null` + "读取开机自启状态失败：<后端原因>"；
只有 `r.ok && !r.supported` 才说"开发环境"。**状态空间不够宽，就会把"问不出来"压成"没有"。**

#### 判据：交给 node 跑页面里的真 JS

`tools/test_about_tier.js` 从 `ui_redesign_settings.html` 里**原样抽出** `aboutTier`
那个 IIFE，在假 DOM + 假 Backend 里跑 5 个场景 20 项断言：

| 场景 | 断言要点 |
|---|---|
| S1 支持且已开 | 开关亮、`aria-checked=true`、原因行空 |
| S2 开发环境 | 置灰 + 说清原因；**点了一下后端都不许调**（对照组） |
| S3 后端没连上 | 报"读取失败"；**不许说成"开发环境"**（对照组） |
| S4 支持且关着 | 点一下调 `set_autostart(true)`，成功后保持亮 |
| S5 写入失败 | 开关**翻回去** + 红字带上后端原因（不谎报成功） |

抽的是页面里的代码本身，不是照逻辑再写一份 —— 后者只证明"我两次写得一样"。

#### 验证

- node 真值表 20 PASS / 0 FAIL；三刀变异全红（去掉开发环境守卫 / 三态压二态 /
  失败不翻回去）
- 无头 Edge 抓 DOM 复核：离线时版本显示"读取失败：后端未连接…"、
  开关置灰且原因行是"当前是开发环境"——**注意 `call()` 里 `await whenReady(12000)`，
  探针的 `--virtual-time-budget` 必须 > 12000**，否则永远读到"读取中…"，会误判成没跑
- 全量闸门 **119/119**

### 2026-10-21 · 反向接线闸门：20 个「后端写完了，界面上碰不到」的方法

#### 起因

`check_bridge_methods` 守的是正向链路：页面调 `Backend.foo` → 适配层导出 foo →
`call('py')` → 后端有 `py()`。**反过来没人守**：后端写完了、测过了，但界面上没有任何
地方能触发它。本仓把这种形态叫「有实现、没出口」，今年已经漏过两次：

- `export_chat_history` 的 `scope='all'`（后端两档都实现，前端只绑了左键）；
- `pet_hit_rect`（`native.set_pet_hit_rect` 全仓不存在，纯 no-op，AGENTS.md 记过）。

它比报错更难发现：读代码时每个函数都有完整实现，**看起来功能都在**。

#### 结论：325 个后端公开方法，20 个零引用

| 分组 | 例子 | 性质 |
|---|---|---|
| 开机自启三件套 | `get_autostart` / `set_autostart` | 后端完整，没有开关 |
| 调试台 | `ocr_get_regions` / `ocr_get_anchors` / `get_audio_status` | 前端从没做过 |
| 下拉选项 | `get_backend_options` / `get_model_3d_options` | 同上 |
| 桌宠 | `pop_notify_cmd`（桌宠页只轮询了 `pop_pet_bubble`）/ `clear_pet_cache` | 同上 |
| 托盘 | `enable_tray` / `restore_from_tray` / `set_minimize_to_tray` | 同上 |
| 其它 | `get_version` / `get_bailian_quota` / `get_relation_events` / `probe_vision_model` … | 同上 |
| 旧架构残留 | `pet_hit_rect` | 建议删 |

**大多是"后端能力完整、前端没做过入口"，不是 bug，是待接线。**

#### 清单不腐烂的机制（这条比门本身重要）

`KNOWN_UNWIRED` 里那 20 个名字只是**存量记录**，不是"这样没问题"。两条规矩：

1. 新增未接线方法 → 红（不许悄悄加一个没人调的接口）；
2. **清单里的名字现在有人调了 → 也红**，必须从清单删掉。

第 2 条是关键。本仓的「发现」栏曾经 F1/F2/F3 三条全部已落地、却一条都没撤下来，
从"待办"变成"背景噪音"。**清单会自己催你删，才不会变成坟场。**

#### 探针写错三次，每次都往"假阳性"方向错

1. **只扫 `.py`** → `relation_panel.js` 走 `pywebview.api.set_relationship_setting(...)`
   绕过适配层，被判成没人调；
2. **只搜名字出现** → `PetAPI.pet_show` 的**定义行**本身就算"被用到"，而
   `CharacterAPI.pet_show` 其实没人调。必须区分「定义」与「调用」；
3. **不认字符串分发** → 设置页写的是 `Backend.call('set_proactive_pack_flags', pending)`，
   只认 `name(` 的话这个**正在用**的方法被当成死代码。本仓字符串分发有三种：
   `Backend.call('x')`（设置页）/ `call('x')`（`theme_backend_bridge.js` 第二适配层）/
   `call("x")`（贴纸页自己直连）。
   **认了字符串，30 个"假死"降到 20 个"真死"。**

#### 闸门自己被自己满足两次

- 第一版：`KNOWN_UNWIRED` 里 20 个名字都是字符串字面量，闸门扫到自己这个文件 →
  **20 个全部"有人调"**，一上来就全红。修：排除自身路径。
- 第二版：变异脚本里写了一句 `pywebview.api.get_version()` 当**字符串常量** →
  `get_version` 被判"已接线"，起点就不绿。修：Python 侧用 `tokenize` 把
  **COMMENT 与 STRING 都丢掉**再扫，只留代码 token。

一般化：**凡"扫源码找某名字"的判据，先问这个名字会不会出现在字符串或注释里。**
同族已有两条（`"backup=True" in txt` 被注释满足、`_qc_setup_hint` 的判据被注释满足）。

#### 顺带：一段描述着已不存在架构的 docstring

`api_pet.py: pet_hit_rect` 的注释讲的是 **TransparencyKey 品红分层窗口**那套方案，
而现在的桌宠窗是 `transparent=True` + WebView2 alpha 合成，全仓没有 TransparencyKey /
`SetLayeredWindowAttributes`。三处都断：前端 `reportPetBody` **从未实现**（`live2d/rig`
只留了 `preserveDrawingBuffer:true` 和一句"要读 readPixels"的注释）、`_pet_win.native`
恒为 `None`、`set_pet_hit_rect` 全仓不存在。已改成"这是纯 no-op，别信旧结论"并写清三处
断点——**注释自相矛盾比没注释更糟**，AGENTS.md 那条「发现过」正是被这段注释误导了第二次。

#### 验证

- 三刀变异全红：加一个没人调的方法 → ③；把清单里的方法改名 → ②；给清单项加真实调用点
  → ①（只判退出码不够，三种错都退 1，所以每刀都断言**报的是哪个分支**）
- 闸门 115 → **117**（反向接线 6.9s / 反向接线变异 35.8s），全量 **117/117**（8m00s）

### 2026-10-21 · 「导出全部角色」是个没人调的参数；判据差点被页面顶部的全局 `contextmenu` 满足

上一轮把「导出聊天记录」做完时，后端就实现了两个范围（`scope=current` / `scope=all`），
测试也覆盖了。但前端只绑了左键——**`scope=all` 没有任何入口**，就是本仓记了很多次的
「有实现、没出口」形态①（函数完整、零调用）。这种半截功能比没写更糟：下一个人读代码会
以为"导出全部"已经能用了。

补法选了右键而不是下拉菜单：工具栏已经挤了 5 个图标，加弹层要自己管定位 / 点外关闭 /
`z-index`，成本远大于收益；右键在桌面软件里是常识，`title` 里也写明了。

#### 第 11 组判据被收紧了三次

守"入口在不在"的源码断言最容易假绿，这次每一层都是踩出来的：

1. **先剥注释再断言**。不剥的话，我自己在注释里复述一遍 `scope: 'all'` 就能满足判据
   ——本仓早有过 `"backup=True" in txt` 被注释满足的先例。
2. **不能全页搜 `contextmenu`**。`index.html:134` 有一条**全局**
   `document.addEventListener('contextmenu', function(e){ e.preventDefault(); })`
   （禁掉 WebView 的右键菜单）。全页搜会恒真，删掉导出按钮的右键绑定也照样绿。
   改成在**导出绑定块内**找 `ec.addEventListener('contextmenu'`。
3. **块的范围不能用 `_i + 2000` 这种魔法窗口**。窗口写小了判据会切在块外（假红），
   写大了会把隔壁块算进来（假绿），而两种错**都不会自己暴露**——只有等某天有人往这里
   加代码时才突然翻车。改成用下一块的开头（`var ft = $('focusToggle');`）定位，
   并补一条「能定位到导出绑定块」的断言：定位失败时后面三条会一起红，
   但没人能从三条红里看出真因是"定位失败"。

#### 变异加到七刀，最值的是「左右键载荷对调」

| 刀 | 用户看到 |
|---|---|
| M5 删掉右键绑定 | 「导出全部角色」没有入口了 |
| M6 右键绑着但不传 `scope` | 右键点下去提示「已导出全部角色 0 条」，实际导出的是当前角色 |
| M7 左右键载荷对调 | 左键导出全部、右键只导当前，**两边都提示成功** |

M7 是这三刀里最该有的一刀：只验"右键传了 `scope: 'all'`"抓不到它（`scope: 'all'`
仍然在块里，只是挪到了 click 上），必须另有一条"左键仍走默认"的判据。

#### 两个自己挖的小坑

- `tools/test_chat_export.py` 第 11 组用了 `re.sub` 却**没 `import re`**，
  前 10 组照常 PASS，最后一段直接 `NameError` 退出码 1。
- 变异脚本末尾写死「还原后 **33** 项全绿」——测试早就是 38 项了。改成从测试自己的
  汇总行 `结果: N PASS` 解析。**写死的数字不会报错，只会慢慢变成谎话。**

#### 验证

- 变异 7 刀全红：`load_all` 退回 200（红 4 项）/ 去掉空历史守卫（红 3 项）/
  空角色不跳过（红 2 项）/ 文件名不过滤（红 1 项）/ 删右键绑定（红 2 项）/
  右键不传 scope（红 1 项）/ 左右键对调（红 1 项）
- `tools/test_chat_export.py` 39 PASS / 0 FAIL
- 全量闸门 **115/115**（7m27s），含「导出聊天记录」「导出聊天记录变异」

### 2026-10-21 · 一条红了很久的测试，守的是一条已被否决的契约

`tools/test_call_engine_resolve.py` 长期 13/20 PASS。7 个红项分两簇：

| 簇 | 断言 | 实际 |
|---|---|---|
| 1.2 / 3.2 / 6.6 / 6.7 | 显式选本地双工 → 走 `local_omni` | 恒 `realtime` |
| 6.3 / 6.4 / 6.5 | 错误码 `no_cloud_duplex_cfg` + `fallback` + `action` | 只有 `no_cloud_duplex` |

去查代码，`start_duplex_call` 里有一句**无条件**的

```python
# 【2026-10-16 按用户明确要求·语音电话恒走云端】
target = "realtime"
```

也就是说 10-09 那版「只有用户显式选本地才走本地」的策略，**在 10-16 被用户否决了**，
测试却一直守着它。这正是 `test_quota_alert` 那个坑的孪生兄弟：这种测试最坏的下场不是红，
而是"谁把它修绿，谁就把用户停掉的行为加了回来"。

所以这次不是"修好它"，而是**改写**：

- 组 1：任何输入组合（内存值 / config / 档位反推 / 弹窗显式指定本地）→ `target` 必须是 `realtime`
- 组 2：没有云端双工 → 不发起连接 + `success=False` + `no_cloud_duplex` + 可读文案 + `diag`
- 组 3：**旧契约的墓碑**——`no_cloud_duplex_cfg` / `fallback` / `action` / `no_cloud_tts`
  在 240 组输入下都不许出现；另有一条"组合确实覆盖到失败路径"，否则前三条是空断言
- 组 4/5：保留（启动恢复、mixed 反推 `duplex_mode=local_omni` —— 后者只描述档位能力，
  不决定拨号引擎，与"恒走云端"不冲突）

顺带在 `start_duplex_call` 里补了两段注释，因为那里的注释**自相矛盾**：9182 行写着
「只有用户显式选择本地双工才走本地」，20 行后被无条件覆盖。下一个人读到第一段会以为
本地还能走。另外指出 `no_cloud_duplex_cfg` / `no_cloud_tts` 那两段目前**不可达**
（`_co_ok` 在前面已被判真且 `_cloud_omni_enabled()` 只读配置、结果稳定；`target` 已被强制
成 realtime）——保留代码是为"恢复本地双工"时有据可查，但不是活路径。

**验证**：3 刀变异全红（删掉强制云端 → 组 1 红 5 项；错误码改回旧值 → 组 2.3/3.1 红；
加回 `fallback` → 组 3.2 红），还原后 22/22；测试里关掉 `WARNING` 日志（组 3 会跑 240 次
拨号，不关会刷 240 行噪音把 PASS/FAIL 埋掉，ERROR 仍可见）。

### 2026-10-21 · 「导出聊天记录」+ 桥接闸门的第三层盲区（内联脚本没有别名支持）

#### 起因是一个「有实现、没出口」的功能

`export_markdown` 这个桥早就有，日报页和记忆页都在用。但**聊天记录本身没有导出**：
`data/chatlog/<char>.jsonl` 里躺着完整历史，用户想备份、想换机器、想把一段对话发给别人，
只能截屏。这次补上：聊天页工具栏（挨着「清空」）多一个下载按钮 → 桌面「AI桌面伴侣报告」
下的 Markdown。

几个不那么显然的取舍：

- **`load_all` 单独开一个口子**，不用 `load_recent(cid, 10**9)`。后者那个魔法数一旦被人
  当成"写错了的上限"改成 5000，导出就**静默只导最近一段**，用户拿到手才发现少一半。
  界面恢复仍然用 `load_recent(200)`，两件事语义不同。
- **没记录时明确报错，不写空文件**。只有标题的 `.md` 用户会当成"导出成功但没内容"，
  比报错更让人困惑。这条有对照组测试（断言"没写任何文件"）。
- **文件名过一遍 Windows 非法字符**（`\ / : * ? " < > |`）。角色名是用户随手起的，
  不处理的话 `open()` 直接抛 OSError，用户看到"导出失败"，真因藏在异常里。
- 写盘复用 `export_markdown`（它已经处理了 OneDrive 重定向桌面这些坑），
  渲染放在 `src/companion/chat_export.py` 里做成纯函数——**格式可以纯函数测**，
  不必为了验格式去动用户的桌面。

#### 验它的时候发现闸门自己瞎了一块

按规矩给新功能加完桥接，顺手做了一次变异：把 `exportChatHistory` 从适配层导出里注释掉
→ **闸门照样 `exit 0 / 三层全对得上`**。

查下来：`used_in_page`（扫 HTML 内联 `<script>`）只认字面量 `Backend.`，而 `index.html`
的工具栏绑定块写的是

```js
var B = (window.Backend && window.Backend) || (window.pywebview && window.pywebview.api);
...
B.clearChat() / B.exportChatHistory({})
```

**整个文件的桥调用对闸门不可见。** 而这一块正是历史上"工具栏按钮全部无效"的地方。
外链 JS 早就支持别名（上一轮刚补的），内联脚本却没跟上——两边各写一套必然漂移。
现在合并成 `_names_in_src()` 一份逻辑：桥方法名总数 **136 → 163**。

补上后立刻报出 3 处真断点：

| 断点 | 真因 | 用户看到 |
|---|---|---|
| `first_launch_setup.html: Backend.listCameras` | 适配层从没导出过它，`if(B && B.listCameras)` 恒 false | 引导页「视频」屏的摄像头下拉**永远只有「系统默认设备」**，用户以为电脑没摄像头 |
| `index.html: Backend.setProactivePackFlags` | 名字不存在（`pace/frequency` 也不是 proactive_pack 的键，后端只收 `DEFAULTS` 里的键，传进去被过滤成空对象） | 关系面板改「节奏/频次」→ TypeError 被 `catch(e){}` 吞掉，什么也没发生 |
| `index.html: Backend.setArchetypeManual` | 守卫恒 false，后端 `set_archetype_manual(char_id, archetype)` 也不接受 `{reset:true}` | 点「重置关系」→ 后端**根本没被调用**，却照样 toast「关系已重置」（**谎报**），紧接着 `relation_panel.js` 的正确实现又弹了第二个 confirm |

后两条的根子是**同一个功能有两份实现**：`relation_panel.js` 早就用 `pywebview.api`
正确调了 `set_relationship_setting` / `reset_relationship`（还带 refresh），而 `index.html`
里另有一套平行绑定，名字全错。这次把 `index.html` 那份**删掉**并留了注释说明为什么
不能"顺手补个导出把它救活"（那会变成双写 + 双 confirm）。`listCameras` 则是真的漏导出，
补上并顺带导出 `setCameraDevice`。

#### 验证

- 导出功能 4 刀变异全红：`load_all` 退回 200（红 4 项）/ 去掉空历史守卫（红 3 项）/
  空角色不跳过（红 2 项）/ 文件名不过滤（红 1 项，打印出 `聊天记录_a/b:c*d?e"f<g>h|i_…`）
- 桥接闸门 4 刀（新增 `tools/_qc_bridge_mutation.py`，这道门之前**没有变异脚本**）：
  内联+别名漏导出（`index.html`）/ 另一页面的内联漏导出（`first_launch_setup.html`）/
  外链 JS 名字写错 / 往内联脚本塞不存在的调用（防"内联扫描退化成空集"）
- **真机探针**：真 `CharacterAPI` + 真 `export_markdown`，把「桌面」指到临时目录后
  写出 741 字节、含第 1 条与第 12 条、落在 `Desktop/AI桌面伴侣报告/聊天记录_达妮娅_<ts>.md`
- **无头 Edge 截图**确认工具栏多了下载按钮、右侧「节奏/频次/重置关系」仍在（没有被删坏）
- 闸门 110 → **113**（导出聊天记录 / 导出聊天记录变异 / 桥接变异），全量 113/113

### 2026-10-21 · 「出厂状态」第 6 起：模型默认下载目录是别人机器上的 H 盘

这一起不是模板里的路径，而是**源码里的默认值**：

```python
model_dir = data.get("model_dir") or _comni_model_dir()
if not model_dir:
    model_dir = r"H:\minicpm models\MiniCPM-o-4_5-gguf"     # ← 开发机
```

`download_model_quant` / `download_bundle` / `_build_cmd` 三处都这么写。触发条件是
「用户还没选目录」——而下载器初始 `dir` 就是空的（前端注释写着"catalog.model_dir
可能为空 → 留空让用户选"）。于是新用户：点下载 → `modelscope download --local_dir
"H:\minicpm models\..."` → 那台机器上没有 H 盘，直接失败；点"复制命令"→ 拿到的也是一条
指着 H 盘的命令，照着敲必然失败。

开发机上永远是对的，**只有新用户才撞**——与前五起同一个判据。改成
`default_model_dir()`：`<程序目录>/models/MiniCPM-o-4_5-gguf`，谁装在哪就下在哪。

回归判据写进 `tools/test_model_downloader.py` 的 [7]：默认目录必须在程序目录下、
目录名带仓库名、`_build_cmd` 缺参时用的是它。变异（把函数改回 H: 盘）→
`默认目录在程序目录下（可移植）` 红。

#### 顺带：一个「搬家后就跑不了」的死引用看门狗

查源码里的硬编码盘符时（全仓 620 处，绝大多数是 docstring 里的叙述，不值得设闸门），
发现 `tools/audit_dead_ids.py` 的 `ROOT = r"G:\ai对话\11"`。它的 docstring 明明写着
"纳入自测后即成为回归闸门"，但两件事一直没做：

1. 仓库搬到 `D:\Denia` 之后它**从那天起就跑不了**（`!! 文件不存在：src/ui/web/...`），
   而它不在闸门里，所以没人发现——与之前修过的 5 个硬编码旧项目根的测试同一个坑；
2. 它把结果写进 `docs/_audit_dead_ids.txt`，而 `docs/` 不是忽略目录 → **每跑一次工作区
   就多一个未跟踪文件**，谁顺手 `git add -A` 就会把生成物提交进去。

已改成按文件位置推 ROOT、产物写 `logs/`（已 gitignore），并收进闸门（约 0.2s）。
当前结果：12 个已知死引用（白名单每条都写了原因），0 个未登记。变异（往设置页塞一个
`getElementById('zzzNotExistProbe')`）→ `[DEAD] zzzNotExistProbe 首见于第 6718 行`、
退出码 1。

**一个自己踩的小坑**：第一次试跑时我用 `| tail -12` 看输出，于是 `$?` 拿到的是 `tail`
的 0，差点判成"这脚本连退出码都不设"（其实它 `sys.exit(1 if fail_total else 0)` 写得好好的）。
这条纪律本仓已经记过一次——**读退出码别接管道**。

### 2026-10-21 · 桥接闸门只看内联 `<script>`，于是它看不见"下载按钮是假的"

起因是 `test_onboarding_integration` 跑绿之后还剩一条「⚠ 发现 F1」：说
`getVoiceVramAdvice().engines` 缺 `kind/repo/note`，「落地需新增克隆引擎清单接口」。
去查的时候发现那个接口**早就有了**（`list_clone_engines`，同一份测试的 B19 正在逐项验它）。

于是顺手查了 F2、F3：

| 发现 | 声称的问题 | 实测 |
|---|---|---|
| F1 | 缺「克隆引擎清单」接口 | `list_clone_engines()` 早已存在 |
| F2 | 后端不守非达妮娅 `model_3d` | `save_character` 里早就有 `if char_id != "danya": enabled=False` |
| F3 | 空 `api_key` 会擦掉已存 Key | `merge_cloud_tts_provider` 早就改成"空值不改"，注释里甚至写着「修 F3」 |

**三条全部已落地，一条都没被撤下来。** 发现栏不红、也不会自己消失，于是从"待办"变成
了"背景噪音"——和"红着的门禁会被训练成噪音"是同一个毛病，只是它连红都不红，更隐蔽。
已全部换成真断言（能守的行为就别只记一条愿望），并在 `finding()` 的 docstring 里写死
规矩：**新加发现必须写明复核日期和删除条件**。

#### 真正的收获：外链 JS 从来没被桥接闸门扫过

`tools/check_bridge_methods.py` 只扫 HTML 里的**内联** `<script>`。而引导页把「建角色」
整步拆进了 `onboarding_role.js`（2000+ 行，`<script src>` 加载），它的桥调用对闸门完全
不可见。补上这一层（22 个外链 JS、56 个桥方法名）之后，第一次运行就报：

```
① src\ui\web\onboarding_role.js: Backend.downloadVoiceModel 未导出
```

去查：适配层导出的名字是 `downloadModel`（`app_backend.js:215` →
`call('download_voice_model')`），全仓只有这一处写成 `downloadVoiceModel`。而它外面套着

```js
if(B && B.downloadVoiceModel){ …真下载… }
/* 无桥降级：稿内模拟进度 */
```

所以真实行为是：**守卫恒 false → 落到模拟分支 → 进度条按固定 8.2MB/s 往上走 → 到 100%
提示「下载完成，可以开始克隆了」，而一个字节都没下。** 这正好绕过了该文件开头那条红线
「不画假进度」。用户第一次装软件，走到「建角色」想下载一个本地音色，看到的是个假进度条。

这是 `typeof Backend.x === 'function'` 那个老坑的又一例：**守卫把"名字写错了"伪装成
"这个环境没有桥"**，而且它比 `typeof` 版本更安静——连降级提示都没有，直接进入"看起来
很正常"的假流程。

#### 外链扫描的两个坑（不处理就会变成噪音或假绿）

1. **别名认不全 → 假绿**：`B` 是常见别名，但来源有三种——`function getB()`、直接
   `window.Backend`、以及 `llm_write.js` 的**工厂参数** `function make(B)`（调用方写
   `LlmWrite.make(Backend)`）。第一版只认前两种，于是 `llm_write.js`（调 5 个桥方法）
   整文件漏扫，闸门照样绿。补上工厂形态后总数从 44 涨到 56。
2. **`B` 太泛 → 误伤**：`_theme_editor_v5.js` 里的 `B` 是它自己的局部对象
   （`B.tStickerDel` 之类根本不存在）。处理：单字母别名要求文件里真出现过"取 Backend"的
   动作；同时排除 `_` 前缀的设计稿/实验稿与第三方 bundle（`morphicons/`、`*.min.js`）。

变异验证：往 `onboarding_role.js` 塞一个 `B.zzzNotARealBridgeMethod()` → 闸门红并点名；
还原后绿。另两刀（删后端 `model_3d` 兜底 → B10+B20 双红；合并层改成无条件写空 Key →
B4 红且打印 `before='sk-onb-integrati' after=''`）。

#### 顺带：B4 原本是一条"永远验不到"的判据

`key_before` 直接取出厂模板里现有的 Key —— 模板里**没有** Key，于是
`if key_after == key_before and key_before:` 恒 False，永远走 else。二态判据把
"没有 Key 可擦"压成了"Key 被擦掉了"。改成**先造一个 Key 再验**，现在它是真的在验契约。

#### 闸门侧再补一刀：失败项的完整输出落盘

`壳自愈端到端` 这轮在负载高时失败（118s，空闲时 49s 通过，同一份代码），而闸门只摘
最后 4 行——那 4 行是它自己的汇总，**看不出是哪一步失败**。现在失败项的完整 stdout/stderr
写到 `logs/gate_failures/<项名>.log`（已 gitignore），汇总里给出路径。
这次没复现（重跑 109/109、8m04s），所以不下"是负载导致"的结论，只记下证据与定位手段。

### 2026-10-21 · 一个 `hasattr` 守卫把「方法名写错了」伪装成「这个版本可能没有」

上一轮我判定 `test_onboarding_integration` **不收进闸门**，理由是「5 个 FAIL 是引导页
设计稿未落地的产品待办，要等你拍板」。这轮逐个查下来，5 个里**没有一个是"要动产品"的** ——
判断错了。

四个是判据自己的毛病：

- **A1「建角色 v8 设计稿存在」**：`git log --all -- "docs/mockups/*"` **零命中**、
  `.gitignore` 也没忽略它 → 那是开发机上的本地过程稿，从没进过库。判据等于
  「拿仓库里没有的东西当判据」，任何干净克隆上都必然红。
- **A4「FLOW 固定 5 屏」**：盯着两种已不存在的形态（`let FLOW = [screenSlots]` 早改成
  `let FLOW = []` + `buildFlow()` 动态拼）。改成守语义：MODE_SEQ 至少 2 条链路、
  每条含 `screenRole`+`screenSummary`、至少一条含 `screenVisionDl`。
- **A9「`_SHELL_GUARD` 对 iframe 放行」**：守卫已随 UI 拆分搬进 `src/ui/main_window.py`，
  判据还在 `webapp.py` 里找。**同型错误第二次**（上次是 `/local_img` 搬家）。
- **B5「含 quants 列表」**：判据依赖本机显存。这台 8GB < 12GB，`cloud_only=True` +
  `quants=[]` + `warning` 是**正确行为**，改成按 `cloud_only` 分两路。

第五个是真缺陷。

**症状**：`set_reference_audio()` 返回 `success: true`、`proactive.duplex_local_omni_ref_audio`
也同步了，但紧接着 `get_characters()` 读回来 `voice.reference_audio` 是空。

**根因**：`get_characters()` 读的是 `Config.list_characters()` → `_characters_map`，
那是 `Config.__init__` 时单独 `_to_ns()` 解析出来的**另一份**，不跟着 `raw` 变。
`set_reference_audio()` 只写了 raw 和 `ns`。本该由 `_save_config()` 末尾那句
「重解析角色表/音色档表」兜住 —— 而那一行是：

```python
self._cfg.refresh_characters_map() if hasattr(self._cfg, 'refresh_characters_map') else None
```

`Config` 上**只有 `refresh_characters()`**，没有 `refresh_characters_map`。
`hasattr` 恒 False，整行恒等于 `None`，一个静默 no-op。它上面还压着一段注释写着
「现统一为：永不替换 `self._cfg`，只重解析角色表/音色档表」—— 也就是说这个"统一"
从写下来那天起，角色表那一半**从来没生效过**。

这是「`typeof` 守卫伪装接线漏洞」的 Python 版：守卫把"方法名写错了"包装成
"这个版本可能没有"，看不出错。真名 + 真 `try/except` 就够，`hasattr` 在这里只是遮羞布。

**修在哪儿**：修 `_save_config()` 这一处，而不是在每个写 `raw.characters` 的调用点各补一次。
语义上「保存后角色表就是新的」是保存的职责；调用点各补一遍，下一个写角色的地方还会漏
—— `save_character` / `delete_character` 当时就是各补了一次，`set_reference_audio` 漏了。

我先确实在 `set_reference_audio()` 里加了一行 `refresh_characters()`，**变异一跑发现
去掉它测试照样绿** —— 追下来才知道真因在 `_save_config`，那行是多余的。多余的一行不只是
浪费，它会把下一个人引到错误的因果上。已删。

**副作用核对**：`refresh_characters()` 会从 raw 恢复 `_active_character_id`。
全仓 `_active_character_id` 只在 `config.py` 里被赋值，且 `switch_character()` 同时写 raw
→ 重解析不会把内存里的激活角色改回旧值。

**验证**：变异（把幽灵调用放回去）→ `[B15] 参考音已落角色` 红、105 PASS / 1 FAIL、
退出码 1；还原后 106 PASS / 0 FAIL；全量闸门 **109 项全绿**。

**顺带修的两处，都是"闸门自己不说人话"**：

1. `check_all` 里 `("导入冒烟", …)` **登记了两次**（末尾一处带完整注释，前面还有一处是我
   上一轮加的裸行）。于是每轮全量都白跑两遍，而且两遍结果会打架：实测第一遍 ✓ 27.9s、
   第二遍 ✗ 95.0s，汇总只报「1/110 项未通过」，光看输出分不清是"测试不稳"还是"跑了两遍"。
   **判据只该有一条真身。**
2. 失败摘要原来一律取"最后 4 行"。对 `smoke_import` 这种「先打失败摘要、最后再打一张
   白名单明细表」的工具，最后 4 行恰好是**已知可选项**那张表 → 闸门把"本来就允许失败的
   东西"当成失败原因报出来，真正闯祸的那一行被截掉。改成优先摘"带错误语义"的行
   （非预期/FAIL/ERROR/Traceback/异常…），摘不到才退回末尾 4 行。

**关于那次 ✗ 导入冒烟**：删掉重复后单跑 59.7s 通过。它到底是不是"重复跑导致的"我没能
证明（当时机器上还压着别的负载），所以**不写成结论**——现在的摘录逻辑会在复现时直接
点出是哪个模块。

**测试输入也改了**：它原先优先复制 `config.yaml`（开发机实配、被 gitignore、含本机绝对
路径）。引导页要验的恰恰是"新用户装完是什么样"，改成优先 `config.release.yaml` ——
与 `test_config_wiring` 那次同一个坑。

### 2026-10-21 · 变异脚本的锚点全失效了，而根因是我自己写文件的方式

`test_*.py` 普查完后接着查 `check_*.py` / `_qc_*.py`。26 个 `_qc_` 里只有 2 个在闸门，
另有 **4 个变异脚本**不在：`_qc_asr_mutation` / `_qc_pathrefs_mutation` /
`_qc_silent_mutation` / `_qc_voice_mutation`。

它们验证的检查器**全都在闸门里**（语音合成判级、语音识别判级、配置引用判级、
主链路静默异常）——也就是说：**闸门里跑着 4 个"可能已经没牙齿"的判据，却没人验证**。

跑一遍，2 个红：

| 脚本 | 刀 | 现象 |
|---|---|---|
| `_qc_asr_mutation` | ⑤「从 run_all 摘掉 check_asr」 | `锚点命中 0 次` → 整把刀跳过 |
| `_qc_voice_mutation` | C「从 run_all 摘掉」 | `锚点命中 0 次` → 整把刀跳过 |

**真因**：`run_all` 的注册形态变了。锚点原来假定 `(check_asr, cfg)):` 是列表**最后一项**，
后来加了 `check_wake_words`，`):` 移到了新项之后 → 匹配不上。
→ 修法：**只锚那一行本身**，不依赖它是不是最后一项。复跑 **0 跳过、七刀全红**。

#### 更值钱的一段：两个猜测都被实测推翻

修的过程中发现 `doctor.py` 里一处 `WARN` 变成了 `FAIL`（`_LOCAL_SERVICE_ENGINES` 那段）。
起初判断是 `_qc_voice_mutation` 刀 E 的残留；`git checkout` 恢复后又发现**所有锚点变成
"命中 0 次"**，于是又怀疑是 `git checkout` 按 `core.autocrlf` 把 LF 转成了 CRLF。

两个猜测都不对：

1. **仓库里存的全是 LF** —— `git cat-file -p HEAD:<file>` 查 `doctor.py` / `main.py` /
   `asr.py`，三个都是 **0 个 CRLF**。
2. **无 `.gitattributes` 时 `rm + git checkout` 出来仍是 LF** —— checkout 不是来源。

**真因是我自己**。那次临时变异注入脚本写的是：

```python
io.open(p, "w", encoding="utf-8").write(s.replace(old, ""))
```

Windows 上 `io.open(..., "w")` 默认 `newline=None` → 把每个 `\n` 写成 `\r\n`，
**整个文件 1398 行全部变 CRLF**。而变异脚本的锚点是用 `"\n"` 拼的 → 全部匹配不上。

→ **纪律：改源码做变异必须用二进制模式**（`open(p,"rb")` / `open(p,"wb")`）。
变异脚本自己就是这么写的（所以它们安全），是我临时脚本图省事踩的。

**这段最值得记的不是 CRLF，而是"两个合理的猜测都被实测推翻"**。
如果只做一个实验就收工，会写下一个错误的因果（"git checkout 会转 CRLF"），
然后下次真遇到时按错误因果去修，越修越偏。

#### 顺手做的根因加固：`.gitattributes`

加 `*.py / *.js / *.html / *.md / *.yaml … text eol=lf`（`.bat` / `.cmd` / `.ps1` 除外，
Windows 脚本需要 CRLF）。

实测有效：**人为**把 `doctor.py` 转成 CRLF(1398) → `git checkout --` → **回到 LF(1398)**。
没有这行时 checkout 会把 CRLF **原样保留**，错误状态就一直挂着。
加完 `git status` 只有 5 个变化（仓库内本来就是 LF，`eol=lf` 零内容变化）。

#### 4 个变异脚本进闸门（105 → 109）

理由就是开头那句：它们验证的检查器都在闸门里，而这次实测证明**锚点会静默失效**。
合计 43s（1.9 + 11.8 + 11.0 + 16.5），全标慢项（`--fast` 跳过），
全量 5m51s → **6m19s**。

注释里加了警告：**这些脚本会改源码再还原，不要并发跑两份 `check_all`**——
两份会同时改同一个文件、互相破坏。

### 2026-10-21 · 第三批普查第二轮：剩下 5 个红全修好，最值钱的是「依赖时间的判据」

第一轮留下 6 个红的，逐个查完，**没有一个需要改产品行为**——只改判据和桩：

| 测试 | 真因 | 判据怎么改 |
|---|---|---|
| `test_tts_multi_provider` | 硬编码 `preset == 6`，而 `_PRESETS["dashscope_cosy"]` 已减到 4 个（Cherry/Serena/Chelsie/Ethan） | 改成「≥3 且每项有 id」；变异（清空预设）→ 1 项红 |
| `test_proactive_chat_routing` | 桩是 `SimpleNamespace`，而 `chat_initiative` 是 `ProactiveStrategy` 的 **property** → `AttributeError` | 桩补 `s.chat_initiative = True` |
| `test_relationship_wiring` | 白名单抄的是 **v1** 的 `MOODS`（6 种），运行时用 **v2**（17 种，含「困倦」） | 改成 `from src.companion.relationship_v2 import MOODS_V2` |
| `test_relationship_integration` | prompt 里有**多处** `【用户这句话】`（few-shot 每条一个），判据 `split(marker, 1)[1]` 取了**第一个**（示例区），把后面的示例 + 真 payload 全当"待判文本"，必然命中 AI 串 | 改 `rsplit(marker, 1)[1]`，只取最后一个区块 |
| `test_quota_alert` | 验的是「额度耗尽主动弹窗一次」——该行为 **2026-10-02 已被用户明确停掉** | 改成守新契约：`got == []`（不许弹回来）+ `quota_state()` 必须列出所有耗尽项 |

**最值得记的是 `test_relationship_wiring`**：它是一条**依赖时间的判据**。
`心情值合法（困倦）` 只在深夜红、白天绿。如果当时按"偶发假红 / 机器慢"跳过，
就永远发现不了"白名单抄的是旧版"。→ **凡判据里出现枚举白名单，先问一句：
这个清单从哪来的、产品改过没有。** 同型风险还有"判据依赖 locale / 时区 / 用户目录"。

**`test_quota_alert` 那条更值得记**：测试绿着，但它守的是一个**被用户否决的行为**。
"通过"在这里毫无价值——它只证明"否决的东西还没被加回来"，而不是"功能正常"。
这类判据最隐蔽：它长期是绿的（因为产品确实没弹窗），一旦有人"修好它"反而会红，
于是下一个人会去改产品——把用户明确停掉的行为加回来。改成守新契约后，
变异（把弹窗接回去）能立刻抓住。

**顺手一条**：`test_relationship_integration` 那类"取第一个匹配"的错误很常见。
prompt 模板里 few-shot 示例会重复同一个区块标题，`split(x, 1)` 永远取到示例区。
**要取"本次真正的那段"，用 `rsplit(x, 1)` 或加更精确的锚点。**

**闸门**：收进 5 项（100 → 105），全量 105/105 通过（5m51s）。
`test_onboarding_role_backend` 退出码 0，但跑 29s 且日志吵（TTS 试听要 ffmpeg，
本机没装）→ **不进闸门**，避免把全量从 5m30s 拖到 6m。

### 2026-10-21 · 第三批测试普查：27 个没进闸门的测试，抓到第 5 起出厂缺陷

**方法**：按文件名比对 `check_all.py`，列出 27 个没进闸门的 `test_*.py`，逐个试跑
（超时 120s），按退出码分三类。结果：**14 个本来就绿**、**13 个红**。

13 个红里**没有一个是"产品功能坏了"**——全是测试与产品脱节，分四种：

| 类型 | 例子 | 现象 |
|---|---|---|
| 桩缺导出名 | `test_screen_capture_perf_guard`、`test_desktop_watch_hotapply` | `ImportError: cannot import name 'sensitive_skip_reason'`——产品给 privacy 加了「敏感窗口整帧不采集」，桩只塞了 `privacy_mode` |
| 判据过时 | `test_onboarding_tts_key` | 守的是 `_ttsPayload → BAILIAN_TTS_PAYLOAD → VOICE_PAYLOAD` 三级回退链，该机制随能力槽重构整条退场 |
| 回调签名过时 | `test_video_watch_progress` | `on_progress` 产品已传第 5 个参数 `meta`，测试 lambda 还是 4 参 |
| 时序契约不一致 | `test_desktop_video_e2e` | 桌面来源走 `create_task` fire-and-forget，`asyncio.run` 收尾会取消未完成任务 → 回复永远记录不到 |
| 硬编码旧路径 | `test_onboarding_integration`、`test_onboarding_tts_key` | `G:\ai对话\11` |

**最值得记的是"桩缺导出名"**：桩只给「当前需要的名字」，产品一加导出名桩就跟不上。
正确写法是**先复制真模块的公开名字、再覆盖要替换的那几个**：

```python
import src.perception.privacy as _real
m = types.ModuleType("src.perception.privacy")
for _n in dir(_real):
    if not _n.startswith("__"):
        setattr(m, _n, getattr(_real, _n))
m.privacy_mode = lambda: False      # 覆盖
```

这次运气好——桩缺名字表现为**响亮地崩**（ImportError）；更糟的形态是「`typeof` 守卫
伪装接线漏洞」，桩缺方法会被产品当成「环境不支持」静默降级。

#### 抓到的出厂缺陷（第五起）

`test_config_wiring` 报「默认应无通道，实际 [qq, wechat]」。顺着查：**它读错了文件**——
读的是 `config.yaml`（开发机实配、被 gitignore），而它想验的是**出厂行为**。
改成读 `config.release.yaml` 后立刻红：模板里 **qq / wechat / qzone 三个通道全是
`enabled: true`**。

危害（读代码确认）：新用户没有 OneBot(3001) / Gewechat(2531) / QQ 空间 cookie，
`build_channels` 只按 `enabled` 建实例、**不检查服务可达性**，于是 OneBot 的
`_connect_forward` 在 `while self._running` 里每 5s 打一条「连接断开，5s 后重连」
warning，**永不停**（一天上万条），Gewechat 同理轮询。

修：模板三处改 `enabled: false`，段首写清原因。变异（改回 true）→ 断言报
`实际构建了 ['qq']`、退出码 1；恢复 → 0。

**方法学**：凡是要验「默认值 / 出厂状态」的测试，必须读 `config.release.yaml`。
读 `config.yaml` 等于在验「我这台机器现在是什么样」，不是验新用户会拿到什么。

#### 顺手修的静默

`video_watch._prog` 里 `except Exception: pass` 把回调异常全吞了——这次正是它把
「lambda 少一个参数」的 TypeError 吞成「一个阶段都没收到」，**零报错**。
改成 `logger.debug(..., exc_info=True)`：回调失败仍不影响观看，但日志留痕。

#### 自己踩的坑

批量试跑时我在脚本里设了 `PYTHONPATH=os.getcwd()`。于是 `test_duplex_link_routing`
（自己没设 `sys.path`）在我的批量脚本里过、在闸门里 `ModuleNotFoundError: No module
named 'src'`。**试跑脚本顺手设的环境变量会掩盖测试本身的缺陷**——同型错误是
「模拟新用户时必须剥掉 API Key」。修完用 `env -u PYTHONPATH` 复验。

#### 闸门

收进 20 项（14 个本来就绿 + 6 个修好），**80 → 100 道**，全量 100/100。
`test_onboarding_integration`（99 PASS / 5 FAIL）**不收**：那 5 个 FAIL 是「引导页设计稿
未落地」的产品待办，收进去只会让闸门常红——**红着的门禁会被训练成噪音**。

### 2026-10-21 · 导入冒烟收进闸门：它抓的东西没有别的门禁能抓

**问题**：`tools/smoke_import.py` 是 2026-10-17 写的，能逐个 import 每个模块，
抓「导入期就崩」——但**一直不在闸门里**。理由很朴素：跑一次 172 个模块要 24 秒，
被当成"偶尔手动跑跑"的工具。

代价是这类故障只能靠人碰运气发现。`compileall` 只保证语法和名字对得上，**保证不了
能不能被加载**；当年实测的两例都能证明这一点：

- `src/tts/cloud_clone_bridge.py`：import 期 `mkdir('G:\\')` → `FileNotFoundError`
- `src/ui/webapp.py`：顶层 `argtypes` 声明引用已删的 `_u32` → `NameError`

两者 `compileall` 全绿。

**先确认现状干净**：172 个模块，通过 166，失败 6——6 个全在白名单内（tkinter 系 2 个、
可选 TTS 后端 3 个、离线造样片脚本 1 个），**非预期失败 0**，也没有"已不再失败"的
陈旧条目。

**然后上变异三刀**（加门必须加变异，否则是装饰品）：

| 变异 | 期望 | 实测 |
|---|---|---|
| 造 `src/zzz_probe_bad.py`（导入期 `raise NameError`），不在白名单 | exit 1 | **1 ✓** |
| 同上，但白名单里**有原因** | exit 0（对照组） | **0 ✓** |
| 同上，白名单里**不写原因** | 应判错 | **0 ✗ 松口子** |

第三刀抓到真问题。`load_allow` 原实现：

```python
out[mod] = why.strip() or "（未写原因）"
```

文件头注释写着「每项必须写原因」，代码却给了兜底——**等于允许无原因加入白名单**。
白名单是藏污纳垢的经典位置：一旦可以无原因加进去，下一个真 bug 就会被「顺手加一行」
永久埋掉。而且 `"（未写原因）"` 在输出里看着**还像一句说明**，比留空更隐蔽。

**改动**：
1. `load_allow` 改为返回 `(dict, no_reason)`，缺原因的条目单独列段报出；
   `--strict` 下 `unexpected or no_reason` 任一非空即 `exit 1`。
2. 顺手修 `--only` 的假提示：只跑少数模块时，其余模块会被当成"已不再失败"列进
   `stale` 段，误导人去删白名单条目。改为仅全量扫描时报 stale。
3. `tools/check_all.py` 加一道「导入冒烟」，标慢项（`--fast` 跳过），闸门 **79 → 80**。

**验证**：三刀复测 1 / 0 / 1 全对；全量 `--strict` exit 0、24s、无 stale；
闸门全量 **80/80 通过**，其中「导入冒烟」耗时 23.2s——**是真跑了**（不是 `↷` 跳过，
也不是秒过的装饰项）。

**一条方法学**：读退出码别接管道。第一次跑变异时我写的是
`.venv/Scripts/python.exe tools/smoke_import.py --strict | tail -12; echo $?`，
拿到的是 **`tail` 的**退出码 0，看着像"变异没抓住"。重定向到文件再读 `$?` 才是对的。

### 2026-10-21 · doctor「配置文件」判据恒真：所有在用的用户都在看一条假警

**问题**（本轮体检第一轮就发现）：

```python
keys_empty = all(not (raw_cfg.get(k) or "") for k in ("api_key",))
if keys_empty and os.path.isdir(data_dir) and os.listdir(data_dir):
    return (WARN, "配置文件", "config.yaml 是出厂默认值，但 data/ 里有使用痕迹", ...)
```

**顶层根本没有 `api_key` 这个键**——模板与实配都没有。密钥在 `llm.openai` /
`text_chat.providers[]` / `vision_models.models[]` / `voice` / `voice_profiles[]` 里。
于是 `keys_empty` 恒为 `True`，**只要 `data/` 非空就恒报**。

危害不是"多一行噪音"，是**狼来了**：它今天就和「唤醒词像遗留音色名」那条混在一起，
差点把后者也当噪音跳过。

**修法**：加 `_has_any_api_key(node)` 递归扫任何非空 `api_key`，
判据改成 `keys_empty = not _has_any_api_key(raw_cfg)`。

**顺手查清一件事**：`cfg.raw` 是**环境变量展开后**的配置，不是原始 YAML 文本。
所以 `${ZHIPU_API_KEY}` 在环境里没有时会展开成空串 → 报 WARN 是**正确**的（真的没 Key）。
第一版测试把这条写成"占位符就算有配置"，跑出来红——**是测试的期望错了，不是代码错**。

**验证**：新测试 `src/tests/test_doctor_config_warn.py`，三态 + 对照组共 9 项：

| 场景 | 期望 |
|---|---|
| 全空 + 有 `data/` | WARN（老用户被覆盖） |
| **有 Key + 有 `data/`** | **PASS（对照组：本次修复的主角）** |
| 全空 + 无 `data/`（新用户） | PASS |
| `${ENV}`（环境有值）+ 有 `data/` | PASS |
| `${ENV}`（环境无值→展开为空）+ 有 `data/` | WARN |

- 变异（判据退回 `raw_cfg.get("api_key")`）→ 场景 2/4a 变红，退出码 **1**；恢复 → 9/9
- `main.py --doctor` 实测：**16 PASS / 3 WARN → 17 PASS / 2 WARN**（假警消失）
- 收进闸门，**79/79 全通过**

**教训**：判据读一个**不存在的键**，就恒为"空/假"——这不是"宽松"，是**恒真**。
凡 `raw.get("x")` 形式的判据，先确认那个键在模板里真的存在（`grep` 一下模板的顶层键）。

### 2026-10-21 · models 层不再推荐已下架的 edge-tts；顺手治了一条「数量断言」

**问题**：`tools/check_models_layer.py` 报

```
[FAIL] 引擎表漂移: 仅我有={'edge-tts'} 仅 webapp 有=set()
```

webapp 侧 2026-10-16 已按发布口径下架 `edge-tts`（用户要求不使用微软在线音色），
但 `src/models/protocols.py` 的 `TTS_ENGINES` 还留着它，`hint` 甚至写着
「零配置、免费、立刻能说」——**models 层（catalog 的引擎清单）还在推荐一个已下架的引擎**。

**删之前先查证了三件事**：

1. 没有 `TTS_ENGINES[...]` 直接索引（只有 `.items()` / `.get()`）→ 不会 KeyError；
   `engine_of_provider` 也零调用。
2. `_TTS_ENGINE_ALIASES["edge"] = "edge-tts"` **保留**——老配置里的 provider 名 `edge`
   仍能正确解析。
3. 实测 `create_backend("edge-tts", cfg)` → `_SilentBackend`（静音）。
   一开始以为是缺陷，查了 `tts_backends.py:1435` 的注释才明白这是 **10-16 的有意设计**：
   「任何遗留路径（旧配置存了 edge-tts）都会安全落到静音后端，而不是又偷偷用微软在线音色」。

**改动**：只从 `TTS_ENGINES`（推荐/可选清单）摘掉 `edge-tts`，后端代码不动，原位留注释。

**顺手治的第二件事**：`check_models_layer.py` 里硬编码 `len(TTS_ENGINES) == 11`，
删一个引擎就红。数量本身不表达"为什么是 11"，下次增删还得再改。
改成「非空下限 ≥5 + 由 webapp 白名单逐字比对守具体集合」——后者才是真判据。

**验证**：
- 修前 15/16 → 修后 **16/16**，退出码 0
- 变异（把 edge-tts 塞回 `TTS_ENGINES`）→ `[FAIL] 引擎表漂移`，退出码 **1**；恢复 → 16/16
- 投影层自检收进闸门，**78/78 全通过**

**教训**：**数量断言不是质量判据**。要么写清"为什么是这个数"，要么换成集合比对。
硬编码数字的判据会在每次正常增删时变红，然后把「红」训练成噪音。

### 2026-10-21 · 第二批测试普查：6 个收进闸门，1 个假红修好，2 个红着待裁决

接着上一轮普查，又试跑 8 个：

| 测试 | 结果 |
|---|---|
| `test_relationship_depth_tier` | 16 PASS ✅ 收进闸门 |
| `test_relationship_judge` | 15/15 ✅ 收进闸门 |
| `test_memory_episode` | 13 PASS ✅ 收进闸门 |
| `test_milestone_emit` | 15 PASS ✅ 收进闸门 |
| `test_context_recall` | 23 PASS ✅ 收进闸门 |
| `test_character_card_images` | 18 PASS / 3 FAIL → 修后 21 PASS ✅ 收进闸门 |
| `test_call_engine_resolve` | **13/20** ❌ 待裁决，不进 |
| `check_models_layer` | **15/16** ❌ 真漂移，不进 |

**① `test_character_card_images` 是假红（「代码搬家没搬测试」第三例）**

它只读 `src/ui/webapp.py`，而 `/local_img` 路由早随 UI 拆分搬进了 `src/ui/main_window.py`。
于是"白名单含 `char_banners`/`char_avatars`"三条**恒 FAIL**——功能是好的，是判据看错了地方。
改成拼接两份源码后 21 PASS / 0 FAIL。

**② `test_call_engine_resolve` 7 项失败，但用例自相矛盾，不能盲改**

- 用例 1.2 要 `config=local_omni → local_omni`
- 用例 2.2 却写着「★新策略：没显式选本地 → 一律走云端」

**同一个文件里两条用例互相矛盾**，说明 1.2 是旧策略残留。6.x 期望
`error_code == 'no_cloud_duplex_cfg'`，实际被新门控 `no_cloud_duplex` 抢先拦下
（两个错误码在 `webapp.py` 里都存在：9230 与 9247）。

→ 先把"用户怎么算显式选本地"这个产品语义定下来，再决定改测试还是改代码。

**③ `check_models_layer` 抓到真漂移**

```
[FAIL] 引擎表漂移: 仅我有={'edge-tts'} 仅 webapp 有=set()
```

webapp 侧 10-16 已按发布口径下架 `edge-tts`（用户要求不用微软在线音色），
`src/models/protocols.py` 的 `TTS_ENGINES` 还留着它，`hint` 甚至写着"零配置、免费、立刻能说"。
→ models 层还在推荐一个已下架的引擎。删它要走老配置迁移验证，没顺手改。

**验证**：6 个逐个复核退出码 0；角色卡修后 21 PASS；门禁 **77/77 全通过**（新增 6 道只多 4 秒）。

**教训**：测试红了有三种可能——**真 bug**、**假红（判据看错地方）**、**判据自己过时**。
三种的处理方式完全不同，先分清再动手。把红着的测试硬塞进闸门，只是把噪音变成红灯。

### 2026-10-21 · 108 个测试里 54 个不在闸门，其中 5 个根本跑不起来

上一轮发现 `tools/test_context_persistence.py` 27 项全绿却从未进闸门，于是做了一次普查：
用 AST 解析 `check_all.py` 的条目表，与 `glob` 出的 `test_*.py` / `check_*.py` 对比。

- 闸门引用 **70 个脚本**（当时 59 道，有些一道跑多个）
- 仓库里有 **108 个**候选文件
- **54 个不在闸门里**

**逐个试跑，挖到两族问题：**

1. **5 个 `tools/test_*.py` 硬编码 `G:\ai对话\11`**（旧项目路径，本机 G: 盘早没了）：
   `test_fabrication_guard` / `test_jsapi_state_split` / `test_proactive_smoke` /
   `test_relationship_proactive_fixes` / `test_write_layer`。
   它们**从搬进本仓库那天起就跑不了**——所以也没人发现它们不在闸门里。
   全仓扫同类：**20 个文件**有 `sys.path.insert(0, r"<盘符>:...")`，其余是 `_` 前缀诊断工具
   或一次性构建脚本（`build_ref_*` 依赖 `H:\daniya_voice_tools`，那些不进闸门是对的）。
   修法统一为按本文件位置推导仓库根：
   `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`

2. **`test_write_layer.py` 缺退出码**：`FAIL` 也返回 0，且把结果写到
   `G:\ai对话\11\docs\_p3test.txt`（直接 FileNotFoundError）。典型的「装饰闸门」——
   即使加进闸门也永远绿。已补 `sys.exit(0 if FAIL == 0 else 1)`。

**改动**：12 个**逐个跑通确认**的测试接进闸门（59 → 71 道），合计只多 16.6 秒。
剩下 42 个仍在待评估清单（多是一次性诊断脚本或需要真机/网络），**不盲加**。

**验证**：
- 每个测试单独跑：退出码 0，断言数已确认（如关系主动修复 37 PASS / 0 FAIL / 2 SKIP）。
- 退出码变异（把 `sys.exit(0 if FAIL == 0 else 1)` 改成 `sys.exit(1)`）→ 退出码 1。
- 门禁 **71/71 全通过**。

**教训**：搬文件过来时，**路径也是要搬的一部分**。按文件名扫「这个测试在不在闸门里」，
扫不出「它根本跑不起来」。两个判据要一起用：**在名单上** + **能跑通**。

### 2026-10-21 · 会话上下文「先建桶后启用」就永远不落盘（重启失忆，零报错）

**问题**：`conversation_history._make_history()` 只在**建桶那一刻**看 `_PERSIST_BASE`
决定挂不挂写回调（`_bind`）。于是顺序一旦反过来——任何模块在 `webapp._init_context_persistence()`
（`webapp.py:342`）之前调过 `shared_history()`——那个桶从此**纯内存**：
对话照常进行、日志一切正常、**重启全丢**，没有任何报错。

探针实测（同一进程、同一临时根）：

| 顺序 | 落盘 |
|---|---|
| 先 `enable_persistence` 后建桶 | 2 行 ✅ |
| 先建桶后 `enable_persistence` | **0 行** ❌ |

**改动**：`enable_persistence()` 里给 `_shared_by_char` 中**还没回调的桶补挂** `_bind`，
让结果与初始化顺序无关（幂等，可重复调用）。

**顺带第二件事**：`tools/test_context_persistence.py` 198 行、27 项断言全绿，
`grep test_context tools/check_all.py` **零命中**——它从来没进过闸门。
一个覆盖写穿透 / 重启回填 / 老日志迁移的测试，就这样裸奔了很久。
已接进闸门（第 59 道），并补用例 14「初始化顺序无关」。

**验证**：
- 修前探针：先建桶 → 落盘 0 行；修后 → 1 行。
- 变异（删掉补挂循环）→ `FAIL 14 初始化顺序无关：先建桶后启用也落盘 got=0`，退出码 1；
  恢复 → 28 PASS / 0 FAIL，退出码 0。
- 门禁 **59/59 全通过**。

**同类判据（值得记住）**：同一能力在「先 A 后 B」和「先 B 后 A」两种初始化顺序下
都必须成立。只测一种顺序，等于把顺序当成了隐含契约。

**附**：本轮还查证了「把 base_url 抽象成服务地址 + 令牌」**已经做完了**——
`set_capability_slot` 的 `is_custom/custom_url/custom_model/custom_key` 全在，
前端也有「自定义 OpenAI 兼容接口」卡片；实测写入会镜像进 `vision_models`，读回正常。
差一步就重复造轮子。教训：提议之前先 grep。

### 2026-10-20 · 出厂模板里躺着 7 个开发机路径；顺带挖出「401 变成连接重置」

这轮从两件事开始：一件是去证伪自己上一轮的怀疑，一件是门禁偶发报红。

**① 先证伪**：上一轮怀疑 `--doctor` 的「语音合成 PASS」是假的（出厂模板顶层 `voice:`
段没有 `engine`，而 `create_backend('')` 返回静音后端）。查完**不成立**：
`get_active_voice_config()` 优先音色档、否则返回**角色** `char.voice`，而出厂模板的角色段
里就是 `engine: pyttsx3`；runtime（`tts.py:117-122`）走的是同一个函数，所以两边一致。
`voice.mode: clone` 只影响 `is_muted()`，不参与选引擎。

**② 顺手抓到的真问题（出厂状态第四起）**：`config.release.yaml` 里有 7 个开发机绝对路径——
角色的 `banner`/`avatar`（G: 盘）、本地双工的 `duplex_local_omni_exe` 与参考音（H:/G:）、
`local_omni_engine` 的 `comni_resources_dir`/`python_embed`/`server_dir`/`model_dir`（H:）。
G: 盘在开发机上早就没了，只是没人去看那张卡。

危害不是"少张图"这么轻：`avatarUrlOf()` 只要 `c.avatar` 非空就拼 `/local_img` 去取图，
**内置兜底头像 `_role_assets/*` 因此永远不会被用上**——"填了个坏路径"比"留空"更糟。
→ 全部留空（这些键的读取点都是 `(x or '')` 真值判断，空串 = "没装"，安全）；
参考音改成随包分发的仓库相对路径。

**验证**：闸门 `[6]` 递归扫**解析后的 YAML 值**（不扫原文——扫原文会被注释满足，这个坑踩过），
命中盘符/UNC 就红并报出 `$.proactive.duplex_local_omni_exe` 这种精确路径；`banner`/`avatar`
另加一条"要么留空、要么指向仓库里真实存在的文件"。两刀变异分别精确报出命中项。
改完用剥掉环境 Key 的模板重跑新用户自检：仍 **16 PASS / 3 WARN / 0 FAIL**。

**③ 全量门禁偶发红，不是机器慢**：「微信回调边界」报
`ConnectionResetError(10054)`，单跑 3/3 通过。先按"偶发"处理会写个重试糊过去，
但真问一句"它偶发红的时候产品是不是也偶发错了"，挖到根因：
`gewechat.py` 的 401 分支**不读请求体就 `close()`**，Windows 会给对端发 RST。
实测（256KB body、20 次）：**12 次拿到 401、8 次拿到 ConnectionResetError**。
真实 Gewechat 侧同样分不清"鉴权失败"和"网络断了"，可能无限重投。

修法要**封顶**：读掉 ≤256KB 再回 401（优雅关闭）；超过就立刻 401 关掉。
不封顶等于给未认证对端开一个"声明 8GB 再慢慢滴水就占住一个线程"的口子
（`ThreadingHTTPServer` 每连接一线程）。

**判据不能拿竞态当断言**：第一版测试是"发大 body，看能不能拿到 401"，
变异（删掉 `rfile.read`）只 1/3 抓得到——RST 与响应谁先到本来就是竞态。
换成"只发头、不发 body，看服务端**等不等**"，这是确定性行为：
- 声明 64KB → 必须等（立刻回 401 就说明它没读 body）；补上 body 后必须回 401；
- 声明 8MB → 必须**立刻** 401（封顶，不许陪着等）。
两刀变异各连跑 2 次，4/4 全抓。

---
#### PR #9 · 4 处不可达代码清理 + 新增「必返回之后不许有语句」闸门

**问题**：`create_notify_window` 的 329 行死代码（#7）暴露出一个类目——**必返回语句之后还有语句**。
编译不报错、测试不报错、跑起来也看不出来，只有人眼逐函数读才可能发现，所以它能潜伏两个月
（里面还 import 了一个根本不存在的 `mode_notify.NotifyJsBridge`）。

**清理 4 处**：
- `proactive.py`：一条「已停止」日志写在 try/except/finally 全 return 之后 → 从未打印。移进 finally 才真会在每条退出路径打印（finally 本身不 return，不影响原返回值）
- `api_pet.py`：一份 default_responses 兜底字典 + return，上面 if/else 两分支都 return → 永远到不了。删除（`random.choice(pool)` 已覆盖「池子非空」全部情况）
- `notify_geom.py`：17 行旧实现，前面一行裸 return（2026-09-05 定版加的）后全部死代码。本函数全仓零调用点，签名与 no-op 行为保留
- `webapp.py`：1 行同类

**新增闸门** `tools/check_unreachable.py`（180 行）：AST 判「必返回后不许有语句」，用 `unreachable-ok`
注释豁免故意不可达（不用行号白名单——行号会漂移）。+218 行变异刀 `tools/_qc_unreachable_mutation.py`
证明判据有牙齿。登记进 `tools/check_all.py`。

**验证**：新闸门全绿（4 处清理 + 豁免标记认得）；变异刀 4/4 全红 + 还原全绿；
`check_all.py` 登记对账 ✓；CI 四腿全绿。

### 2026-10-22 · 切角色后界面名字不跟随：一条投递通道是死的分支

`applyCharacterNameBoot`（`app_backend.js`，2026-10-02）干的事是：HTML 里给节点打
`data-char-name` / `data-char-initial` 标记，由 `applyCharacterName(name)` 统一改写，
名字来源优先 `getChat().active_name`。它自己的注释写着要解决的问题是
"用户切换角色后这些仍显示旧名，与气泡里真实角色名不一致，看着像 bug"。

**它要修的那个 bug，本身就没修好** —— 触发它的那条事件路径是死的。

#### 链路三段，每段单独看都在工作

| 段 | 状态 |
|---|---|
| 前端 `d.type === 'character_switched'` → `refresh()` | ✅ 分支在、逻辑在 |
| 后端 `EventType.CHARACTER_SWITCHED` | ✅ `dialogue.switch_character()` 正常 publish |
| **投递到 WebView2 前端** | ❌ **只走 `ws_server.broadcast`（`{message_type, payload}` 信封），而 `app_backend.js` 里没有 WebSocket 客户端** |

三条佐证把这条钉死：
- `switch_character` 成功后**不广播任何东西**（只清 chat buffer + 重置瞬态指针）。
- `app.js`（旧壳）有 ws 客户端，但 `switch (env.message_type)` 里**没有**
  `character_switched` 这一 case；而且 `app.js` 只被 `index.legacy.html` 加载，
  现壳 `index.html` 加载的是 `app_backend.js`。
- `window.refreshCharacterName` 全仓**零调用者**（「写了没接线」形态①）。

**为什么三个月没人发现**：聊天内容会靠 800ms 轮询刷上来，所以"换了角色"看着是生效的
——只有名字不跟随。

#### 修法：两端都修

1. **后端**：新增 `webapp._broadcast_character_switched(char_id)`，在 `switch_character`
   的**两处**成功返回前都调（runtime 路径 + 配置级回落路径——`Config.switch_character`
   不发事件，只挂总线会漏掉后者）。
2. **前端**：`ui_redesign_characters.html` 的 `setDefault()` 成功后显式
   `window.refreshCharacterName()` 兜底。

**为什么两端都修**：`rig/index.html` 2026-09-03 的实锤——`chrome.webview` 监听对
`PostWebMessageAsJson` **完全不触发**（3 次广播 / 0 回执）。这条原生通道有静默失败
前科，不能只依赖它。

---
#### PR #10 · relationship_v2 删掉被静默覆盖的 37 行同名函数 + 新增同名覆盖闸门

**问题**：`src/companion/relationship_v2.py` 里有一个函数被**同名函数定义静默覆盖**。Python 里后一个
`def` 会覆盖前一个，前一个 37 行函数体从定义那一刻起就永远不可达——编译不报错、测试不报错、
运行也不报错（那 37 行只是白白占内存、误导阅读），只有逐行读才看得出。

**改动**：删掉被覆盖的 37 行死函数体，保留真正生效的那个定义。

**新增闸门** `tools/check_duplicate_def.py`（159 行）：AST 判「同作用域内不许有同名函数定义」，
用 `duplicate-ok` 注释豁免故意覆盖（如重载兼容垫）。+220 行变异刀 `tools/_qc_duplicate_def_mutation.py`
证明判据有牙齿。登记进 `tools/check_all.py`。

**验证**：新闸门全绿；变异刀 4/4 全红 + 还原全绿；`check_all.py` 登记对账 ✓；CI 四腿全绿。

#### PR #11 · webapp 语音试听：`finally` 里 `return` 吞异常 + 任务表只进不出

**问题**：`src/ui/webapp.py` 的 `_run_voice_test` 在 `finally` 开头写着 `if backend is None: return`。
本意只是「没装上本地引擎就跳过卸载」，但 `finally` 里的 `return` 有两条副作用：
① 吞掉正在向上抛的异常；② 退出的是**整个函数**，于是后面那句「60s 后摘掉 `_voice_tasks[task_id]`」
永远不执行。顺着 `pop` 的调用点再查，又发现第二条路 `_run_cloud_preview` **压根没有清理**——
云端试听每点一次就往表里留一条，进程活多久留多久。

**改动**：把早退降级成条件守卫（`if backend is not None and ...`），清理照旧执行；
抽出 `_schedule_voice_task_cleanup()` 给两条路共用，并给云端路径补上 `finally`。

**新增闸门** `tools/check_finally_jump.py`（182 行）：AST 判「`finally` 里不许 `return`/`break`/`continue`」。
两条限定都是量出来的：不跨嵌套 `def`/`class`/`lambda` 作用域；`break`/`continue` 只在 finally 内
没有外层循环时才报（否则是正常跳出）。豁免标记 `# finally-ok:`。
配八刀变异刀 `tools/_qc_finally_jump_mutation.py`（M4/M5 是限位刀），以及接线判据
`src/tests/test_voice_task_cleanup.py`（5 条 AST 判据，第 [0] 条是反空转锚：写入点集合钉死成两个方法）
+ 五刀变异。全仓普查 696 文件曾 1 处，修完 0 处。**本 PR 同时把它接进了 CI**（`portable-gates`）。

**验证**：闸门全绿；八刀 + 五刀全红且还原后仍绿；CI 四腿全绿。

#### PR #12 · 10 处恒真/恒假条件（含 2 条永远通过的假断言）

**问题**：四类，一个比一个隐蔽。

① `src/ui/main_window.py:248` 的 `if False:` 关着 13 行 WebView2 滑窗逻辑，而里面调的
`mode_notify._notify_slide_if` **在 `mode_notify.py` 的 18 个函数里根本不存在**——谁把 `False`
改成 `True`，第一句就是 `AttributeError`，还会被下面的 `except: pass` 吞掉。
与第十五轮 `create_notify_window` 里的 `NotifyJsBridge` 是同一个形状：**被关掉而不是删掉的旧实现，
再没人编译过它**。

② `src/tests/test_doctor_llm_channel.py` 有**两条永远通过的假断言**（`check(... or True, "")`），
其中一条还和下面几行的判据**互相矛盾**——正因为它永远通过，矛盾才一直没暴露。

③ `src/agent_pro/__init__.py` 的 `if k in _config.DEFAULTS or True` 让「只收认识的键」从没生效过。
**只删假过滤、行为逐字不变**（`{k: v for k, v in d.items() if True}` ≡ `d`）；没有顺手「修好」判据，
因为那会改变行为（未知键突然被丢弃），是另一件事，留给维护者定夺。

④ 另 6 处零散（2 个 `assert … or True`、3 个 `… if False else …`、1 个 `if … or True`）一并清掉。

**新增闸门** `tools/check_constant_cond.py`（约 200 行）：只查**条件位置**（`if`/`while`/三元/`assert`/
推导式 `if`/`match` guard，以及本仓 `check(<cond>, msg)` 的第一个实参）。三条限定全是量出来的：
值位置（`v = a or True`）不算恒真；`while True:` 是惯用法；`check()` 只认布尔运算形态——
**第一版把「第一个实参是字面量」当缺陷，一跑 62KB 输出**（本仓 `check` 有 `check(cond,msg)` /
`check("说明",cond)` / `check("说明")` 三种签名）。配 13 刀变异（M8/M9/M10 是限位刀）。
全仓 10 处 → 修完 0 处。

**验证**：假断言改成真判据后，删掉 `Authorization` 头即变红（在临时副本里实测）；CI 四腿全绿。

#### PR #13 · 三道登记在 `GATES` 里、却永远绿的闸门（判据没写退出码）

**问题**：`tools/check_all.py` 的 `GATES` 靠**退出码**判定每一项通过/失败。于是只 `print("[FAIL] …")`、
始终 `exit 0` 的脚本会占着清单一行、每次全量都跑、永远显示 ✓——它不守任何东西，
但它的存在让人以为「这件事有判据」。195 个登记脚本里查出 3 个：

- `tools/test_judge_confirm_clause.py`：逐条打印 `[PASS]`/`[FAIL]`，连累计变量都没有；
- `tools/test_prompt_companion_clause.py`：**已经算出 `allok = all(checks.values())` 并打印了
  `RESULT: ALL PASS / HAS FAILURE`**，就差把它变成退出码；
- `tools/test_fabrication_guard.py`：同样算出了 `ok`、打印了 `PROOF ALL_PASS / HAS_FAIL`，同样没退出。

后两个尤其值得记：**结论算对了、印出来了**，红的是屏幕上的字，绿的是退出码，而 CI 只看后者。

**改动**：三处各补一行 `sys.exit(0 if ok else 1)`；`test_judge_confirm_clause.py` 顺带把循环改成累计式。

**附带修掉一个更大的洞**：在没装 `yaml` 的解释器下，`test_prompt_companion_clause.py` 会在 import 阶段
就 `ModuleNotFoundError` 崩掉，而旧版**崩完仍然 exit 0**——崩溃也算通过。新版 exit 1。
`exit 0` 不只吞掉 `[FAIL]`，它吞掉一切。

**新增闸门** `tools/check_gate_exit.py`（187 行）：`GATES` 里的 `.py` 项必须含一条能红的路径——
非常量 `sys.exit`/`os._exit`、`unittest.main`/`pytest.main`、`raise`、`assert` 四选一；
豁免 `# gate-exit-ok:`；退出码 2 覆盖「登记表解析失败 / 脚本不存在 / 脚本解析失败 / **扫到 0 个 .py**」。
配 15 刀变异 `tools/_qc_gate_exit_mutation.py`（K4–K10 限位刀，K15 钉住「`try` 包住的 `raise` 仍判绿」
这个已知松）。**变异刀样本全在 `tempfile` 里造，一次也不碰本仓源码。**

**验证**：master 原样 195 检查/命中 3 → 修完 197 检查/命中 0；15 刀 + 对照全绿；
两道非空洞性证明（退出码改回 `sys.exit(0)` → 元闸门点名并 exit 1；把提示词里的「撒娇式试探」
改成「撒娇试探」→ 该闸门 `[FAIL]` 并 exit 1）；CI 四腿全绿。

### 2026-10-22 · 新闸门：后端广播 type 与前端分发分支对账

上面那条死分支的根因是：**两边靠一个字符串 `type` 对上，而没有任何机制保证它对得上。**
同类已两起（`quota_alert` 10-22 删、`character_switched` 本轮修），症状永远静默
——分支不执行、不报错、没有空指针，只是"那个功能不工作"。

#### 只做单向，是实测逼出来的

反向（"后端广播了的 type 前端必须接"）会假红：`pet_tip` 就是反例——桌宠窗的
`chrome.webview` 监听对 `PostWebMessageAsJson` 不触发，早就改成 1.5s 轮询
`api.pop_pet_bubble()`。**"广播了但没人用 webview 监听器接"在这里是合法设计。**

后端全集只收两处真实发送点（`_wv2_broadcast` 15 处 + `_BROADCASTER` 1 处，共 6 种
type），**不收 `protocol.MessageType` 枚举**：那 16 种是给 Unity/ws 的，收进来只会让
全集变大、判据变松。前端真有分支要接枚举里的 type 时，正确做法是在后端显式广播一次
（`character_switched` 就是这么修的）。

#### 边界必须精确圈定（一刀切必假红）

全仓 `.type === 'x'` 有 **25 处以上不是消息分发**：`btn.dataset.type === "kill"`、
本地事件总线 `ev.type === 'asr'`、通知项 `n.type === 'ok'`、骨架 `s.type === 'row'`、
spa 的 `s.type === 'module'`… 圈法是
**`chrome.webview` → 同文件后续第一个 `addEventListener('message')` → 回调体花括号配对**。

另外每个 `window.addEventListener('message')`（页面内部 postMessage）都必须在
`PAGE_INTERNAL` 登记原因，**未登记的要红**——否则这道门会悄悄漏掉一整类载体。
草稿排除后：4 个 host 监听器 / 5 种前端 type / 5 个页面内部文件（含 `live2d/` 两个：
我第一轮普查把 live2d 整个当 vendor 排除了，**它是一等公民页面**）。

#### 自保（闸门自己坏了要能喊出来）

- 广播调用点数 **必须等于** 取到 type 字面量的调用点数（**不允许动态 type**，
  否则全集不可知而判据还在跑）
- 监听器 ≥ 4 / 前端 type ≥ 5 / 广播调用点 ≥ 10 / 后端 type ≥ 4
- 每个监听器体里至少要有一处 `.type` 比较（花括号配对坏掉 → 体是空的 → 红）
- `PAGE_INTERNAL` 条目失效要红（催删）

#### 七刀变异

M1 是**原 bug 原样**：把后端那行 `character_switched` 广播改成不广播 → 必须红并点名。
这一刀证明闸门在**修复之前**确实是红的，不是马后炮。其余：M2 前端多一个后端不广播的
分支 / M3 对照组·页面内部 postMessage 不许误伤 / M4 对照组·`dataset.type` 不许误伤 /
M5 少一个监听器 → exit 2 / M6 对照组·注释里提的 type 不算 / M7 动态 type → exit 2。

#### 顺带：补登记两把从没跑过的变异刀

按文件系统反向核对所有 `check_*.py` / `_qc_*.py` / `test_*.py` 时发现：
`_qc_channel_mutation.py` 与 `_qc_gewechat_mutation.py` **只在 check_all 的注释里被
提到，从没登记为闸门项**。按项目自己的规矩「加门必须同时加变异」，
`test_doctor_llm_channel` 与 `test_gewechat_callback` 这两道门**一直是装饰品**。

实跑 `_qc_channel_mutation`：七刀里 **C/D/F/G 四把锚点命中 0 次、被 "[跳过]" 静默
放过**，也就是说那四组判级断言（断网不判红 / 空 key 不发包 / 5xx 不判红 /
run_all 必须接线）其实没有牙齿。两个根因：

1. **`J()` 把 CRLF 写死**，而 `tools/doctor.py` 后来变成了 **LF**（实测 0 个 CRLF）
   → C/F/G 三把多行锚点全落空。**单行锚点（A/B/E）不受影响、刀照跑，所以坏得极其隐蔽。**
2. D 的锚点还是老文本 `(check_llm_channel, cfg)):`，run_all 那行早变成了
   `(check_llm_channel, cfg),`

修法：`_eol()` 按目标文件实测换行符；D 改成整行删。修后**七刀全红**、还原字节一致。
`_qc_gewechat_mutation` 五刀本来全红、还原干净，只是没登记。两把都已补登记。

**教训**：注释里写"变异 N 刀全红"不等于它跑过。反向核对该按**文件系统**做，
不该按 check_all 的注释做。

---

### 2026-10-20 · 真机探针抓到一个静态断言永远看不到的竞态

前面那轮浮层全是**静态断言**（源码里有没有这段调用）。这次做了一次真机探针：
假后端 → 真的点一次发送 → 看 DOM 上到底有没有 `show` 类。三个发现，前两个是坑，
第三个是 bug：

1. **`</body>` 在这个页面里出现两次**，第一次在注释里（就是记 `#callOverlay` 漏标签那段）。
   按第一个替换会把探针塞进注释，永远不执行——探针"跑成功"但其实什么都没验。
   必须锚 `</body>\n</html>` 这个尾部。**这个坑 10-19 已经踩过一次**（当时是 Edit 命中两处）。
2. **`window.Backend` 会被 `app_backend.js` 换成真适配层**。提前在 `<head>` 里塞假对象
   会被覆盖，于是 `sendChat` 根本没被调用、探针却"没报错"。正确做法：页面加载后
   **在真对象上改方法**。
3. **顶部条的重试会把浮层刚收起的条放回来**（真 bug）。为了"一次只说一句话"，
   浮层出现时把顶部条收起来；但顶部条的后端探测会重试（最多 12 次 × 1.2s），
   每次 `ask()` 都重算 `bar.hidden`，于是收起后 300ms 又被翻回可见。
   探针时序打出来是 `afterClick=true → t300=false`，一眼就看见了。

修法是让判据自己带上"浮层正显示"这一项：`bar.hidden = (能聊) || _hintShowing()`，
其中 `_hintShowing()` 直接读 DOM 上的 `show` 类——不另存标志位，省掉"两个变量谁先更新"
的同步问题。浮层那边也不直接写 `hidden` 了，改成调 `__brainBarRefresh()` 走同一条判据。

探针固化成了 `tools/_qc_setup_hint.py`（**不进门禁**：它依赖本机 Edge 的 headless，
换台机器就跑不了）。门禁只放不依赖浏览器的判据，这条留作人工探针：
改了发送路径或提示逻辑就跑一次。变异补到 18 刀，全红。

探针随后扩成**三个场景**，每个场景一次独立加载、状态不串：

| 场景 | 期望 | 它防的是什么 |
|---|---|---|
| A 未配置 → 点发送 | 浮层出现 + 顶部条让位 + 标题是「没接上大脑」 | 浮层根本没弹 |
| B 接 A 点「稍后」 | 浮层收起 + **顶部条回来** | 「稍后」顺手把常驻兜底也关了 |
| C 已连通但发送失败（额度用尽） | **浮层不出现** | 弹了就是在撒谎——她明明能聊 |

C 是对照组，也是最容易被忽略的一条：**只验"该弹的时候弹了"是不够的，还得验"不该弹的时候没弹"**。
判据用正则取 `键=值` 再整串比较，不能用子串包含——`cls=setup-hint` 是
`cls=setup-hint show` 的前缀，用 `in` 判断会让"没弹"和"弹了"看起来一模一样。

**探针自己也要做变异**，否则它只是装饰：去掉「只有 no 才弹」→ C 报警；
去掉「稍后」里的 refresh → B 报警；去掉 `|| _hintShowing()` → A 报警；
注释掉 `add('show')` → A 报警。**4 刀全报警，每刀都指对了场景**。

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

同一道闸还多一条判据：**根目录每个 `.md` 都必须出现在 README 里**。根目录当时有 11 份带日期的
文档，README 只索引了 8 份，剩下三份（交付验包 / 产品化规划 / 产品化路线）新来的人只能靠猜。
这条把「加文档时顺手更新门面」变成强制点。没有搬文件——它们被 1–4 个文件引用，搬要同步改链接，
收益只是"好看"，风险是断链；索引才是要解决的问题。

> 跑全量闸门时别动仓库根目录：本轮跑到一半我同时在跑变异（造了个 `_tmp_doc.md`），
> 闸门把它抓了、报 1/58，看着像代码坏了。删掉后重跑 58/58。反过来也证明判据真在跑。

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
| `5dbc64c` | 10-18 | fix | `main.py` 模块级 ImportError：双击直接崩 | `--help` / `--doctor` 实测通过 |
| `5dbc64c` | 10-18 | feat | `ui.pet_backend` 配置项 + 两处入口统一接线 | 22 项测试 + 变异 2 次 FAIL |
| `5dbc64c` | 10-18 | feat | 桌宠位置持久化（按工作区比例存） | 18 项测试 + 突变越界夹取 FAIL |
| `5dbc64c` | 10-18 | feat | 版本号单点 `src/version.py` | 三处口径归一 |
| `5dbc64c` | 10-18 | feat | `--doctor` 一键自检（9 项） | 每项带修复提示，支持 `--json` |
| `5dbc64c` | 10-18 | feat | 壳自愈 + 退出清理 + 崩溃降级 | 单测 13 组；端到端真杀进程验证 |
| `5dbc64c` | 10-18 | feat | `make_dist --install-deps`：依赖按档装进包内运行时 | 包内 `--doctor` 9 PASS / 0 FAIL（后由 `c010d83` 收口成「依赖真离线化」） |
| `b87a8c1` | 10-18 | feat | `make_dist --report`：体积可拆（谁占了 408MB） | 实测 python/ 占 93.1%，scipy 109MB |
| `422f140` | 10-18 | fix | RPC 长连接被 401 残留 body 污染（P1） | 压测 720 次 0 失败；3 项变异全 FAIL（速览表另有一条） |
| `5dbc64c` | 10-18 | feat | 交付包运行期隔离（`pythonXY._pth`）+ pywin32 DLL 铺放（C6） | 隔离环境 doctor 11 项；摘掉包内 rich → 报缺失 exit 1；8 项变异全 FAIL |
| `5dbc64c` | 10-18 | feat | `--doctor` 增「干净机器三件套」：WebView2 / UCRT / 代码签名（C4+C7） | 包内 12 PASS / 2 WARN / 0 FAIL；26 项断言 + 7 项变异全 FAIL |
| `b87a8c1` | 10-18 | fix | 升级吞配置：装配覆盖前抢救改过的 config.yaml（P1） | `preserve_user_config` + doctor「出厂默认+使用痕迹」检测；3 道变异全红 |
| `5dbc64c` | 10-18 | fix | 启动令牌全量打进日志（P2） | `%.8s` 截断 + 新门禁「凭据日志卫生」（27 项，`check_log_leak.py` 由 `8f28cc0` 建）；4 轮调试闭环 |
| `b87a8c1` | 10-18 | fix | **make_dist.py 被 `git checkout --` 误还原，从测试规格+产物重放**（P0 事故） | 21 段测试全绿 + 隔离环境端到端验证通过；教训：变异还原禁用 git checkout |

> **本批提交号怎么定下来的（2026-10-21 回填）**：这 14 条原先全挂「待提交」，但工作
> 本身早已在树里——`src/version.py` / `tools/doctor.py` / `src/ui/pet_backend.py` /
> `pet_state.py` 都是 `5dbc64c`（13:23「深度质检收口」）带进来的，`preserve_user_config`
> 和 `--report` 在 `b87a8c1`。**和速览表头顶那条脚注是同一个现象**：并发会话只 add 自己
> 知道的文件，收口提交就把别人的改动一起吞了。逐条对应关系是用 `git log -S` 按特性串
> 反查出来的，不是按提交信息猜的——`--report` 这种词太泛（基线里就有），单独一条查不出
> 来，是靠「体积可拆 / 408MB」两串才定位到 `b87a8c1`。

---

## 2026-10-18

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

---

### `ba5eebc` · 双工通话内容不进桌面聊天页/活动日志

`proactive` 发 `USER_TEXT{source:"duplex"}` 是**侧信道**（专为驱动好感度，早退是两端都有注释的有意设计）。但 `USER_TEXT` 是公开总线，**7 个订阅者里 2 个漏判 `source=="duplex"`**：

- `webapp._on_chat_user_text`：只判 `is_proactive`，不判 `source=="duplex"` → 通话中用户每句话被当打字消息进 `_chat_buffer` 并被 `_persist_chat` 落盘。
- `activity_reporter._on_user_text`：两个都不判 → 日报把通话原话当打字聊天；且主动搭话也被错标 `direction="user"`（重复 + 错标）。

修：`webapp` 与 `activity_reporter` 两处补 `source=="duplex"` 闸门；主动搭话不在此记（AI 侧已按 `ai_proactive` 记过）。数据不丢（`_publish_call_record` 四个发布点 + `shared_history(source="duplex")` 都在）。

验证：场景 13 正反两路四种来源 + 断言"落盘数与上屏同构" + 断言"通话记录发布点仍在"；95 项断言；变异十二刀 → 十五刀。6 文件 +257/−15。全量门禁 141/141。

---

### `d5e1433` · 听链·坏帧杀守护线程（永久失聪）

`src/voice/asr.py` 的 `_run()` 识别循环体（`while self._running:` 内三分支：免唤醒持续识别 / 唤醒 / 指令）**没有任何 try/except**。单帧畸形/边界音频让识别器内部抛异常冒泡出 `_run`——典型触发：`sherpa.feed` 的 `np.frombuffer(pcm_int16)` 在**奇数长度 PCM** 上抛 `ValueError`。ASR 是 `daemon=True` 线程、由 `start()` 直接拉起、**无 supervisor 重启** → 一次坏帧永久杀死监听线程、全程不报错 → "她听了却再也听不见"。

判据来源：`grep` 确认无重启逻辑；初始化/麦克风启动失败的 `return` 语义保留。

修：循环体外层把三分支识别派发包一层 `try/except Exception`，坏帧只 `logger.error` + `continue`，下一帧正常音频仍识别、USER_TEXT 照常发出（对齐 MEMORY §6「任务体自己兜住」）。

验证：
- `test_asr_event_flow.py` 新增场景 C：注入 `BadFrameSherpa`（`b"BAD"` 帧抛 ValueError），坏帧夹在唤醒与指令之间，断言 WAKE_WORD + USER_TEXT 仍发出（坏帧被跳过、循环活着）。三场景全过。
- `_qc_asr_event_flow_mutation.py` 新增刀 C：把 except 的 `continue` 改成 `raise` → 坏帧冒泡杀死线程 → USER_TEXT 消失（鉴别力成立）。因分支整体缩进 +4，B1 锚点同步更新；基线（双引擎 + 畸形帧路径）全绿、四刀（A/B1/B2/C）全红。
- 全量门禁 141/141（含场景 C + 变异刀 C）（待本轮跑完回填）。

---

### `05fee72` · 闸门登记对账：长得像闸门的脚本，必须要么登记、要么写明为什么不用

**问题（「写了没进闸门」第五次复发）**：`tools/` 下 8 个 `check_*.py` / `test_*.py`
从未登记进 check_all——4 个硬编码开发机 G:/H: 盘路径（`check_sample` / `check_v2` /
`check_turn_taking` / `check_ww_identity`，换机即废）、1 个要真实百炼 Key + 声卡外放的
真机探针（`test_onboarding_role_backend`）、3 个人工诊断仪器（`check_tts_audio` /
`check_turn_audio_len` / `check_cascade_status`）。名字长得像闸门，新人跑了报红还会
以为产品坏了；而它们覆盖的行为**没有任何自动判据在守**。前四次：`test_context_persistence`
27 项全绿而闸门零命中、`audit_dead_ids` 写着"纳入自测后即成为回归闸门"却从没进、
`_qc_channel_mutation`/`_qc_gewechat_mutation` 只在 check_all 注释里被提到、并发会话的
`test_set_player_name_wiring` 因 check_all 被锁而留在树上没接线。

**普查口径（先回答"会不会绊到队友"，再决定动不动）**：tools/ 全量盘符路径 824 处 /
264 个文件，**全部 git 跟踪**（按 `git ls-files` 进队友包）。按「队友会不会跑」切刀：
闸门类 39 个文件的命中逐条看过，全是 URL / 注释墓碑（check_all 自己记录旧
`G:/ai对话/11` 的说明）/ 变异刀锚点（故意写 dev 路径测闸门）/ 标准系统位（Edge、
WebView2、SystemRoot）/ 测试夹具输入——**功能代码零 dev 路径依赖**，队友面
（doctor / check_all / pack）无出厂缺陷。228 个 dev 研究脚本（bake/_analyze/gen 系）
是一次性工具，不拦。`src/` 75 处上一轮已定性无出厂缺陷。

**修法**：新闸门 `tools/check_gate_registry.py`，三条判据：
① **正向**——`tools/` 与 `src/tests/` 顶层每个 lookalike，要么出现在
`check_all.GATES` 的 argv 里，要么 docstring 带「不进闸门：<原因>」（原因不许空，
否则就是第二个「发现」栏）；② **反向**——登记的每个脚本路径都真实存在（防改名/
删除后登记项变哑，而汇总里还显示这一项「通过」）；③ **去重**——同一脚本不许登记
两次（2026-10-21 教训：重复登记白跑两遍还互相打架）。8 个文件补免登标记，各带原因。
变异刀 `tools/_qc_gate_registry_mutation.py`：四刀注入 + 双对照。

**踩坑（五个，全部当场修）**：
① 登记表注释里躺着已删重复登记的墓碑（`smoke_import.py` 在注释里出现第二次）→
正则扫源码会把自己注释满足 → **必须 AST 解析** check_all（且 `GATES` 是带类型标注的
`AnnAssign`，`ast.Assign` 遍历不到）；
② GATES 里有 `-m` 式调用，`argv[0]` 不是脚本路径 → 反向/去重必须先跳过，
否则第一项就假红；
③ `os.path.join` 在 Windows 给反斜杠，比不过登记表里的正斜杠 → 一把假红 43 个
（src/tests 全灭）——**跨平台路径比较必须归一**；
④ 变异刀的 `MARKER_RE` 漏 `re.M`，`^` 只匹配字符串开头 → 「现找目标」直接落空
（刀报"找不到带免登标记的文件"而不是静默跳过，这条救了它）；
⑤ 字节字面量两连：`b"中文"` 直接 SyntaxError、字符类里的全角冒号 `[：:]` 同样非
ASCII——中文锚点一律 `.encode("utf-8")` 拼。

**验证**：四刀全红（M1 放未登记新文件 / M2 撕某免登文件的标记 / M3 把登记项指向
不存在的脚本 / M4 复制一条登记项），每刀都断言**具体抓到了注入的那个错**（点名文件名 /
报「不存在」/ 报「重复登记」），不是任意非零退出——分不清「闸门在工作」和「闸门自己崩了」；
对照组绿且「待定 0」从输出解析（不写死数字）；收尾再跑一次对照，全部变异还原后字节一致。
全量门禁 **142/142**（12m9s，含新闸门 0.5s / 变异刀 4.1s / 补登记的玩家名接线 1.1s）；
`test_set_player_name_wiring.py` venv 单跑 11/11。

---

### `8e68ed1` · 双工过渡看门狗从来没启动过——重连卡死时她会永久静默

**问题**：`_duplex_in_transition` 禁言闸门（切角色/重连/换会话时防止弱模型
persona 未稳定就开口）会把 `_on_duplex_speak` 的开口全丢弃（2001/3336 两处），
正常出口只有 connected/prepared（2989/3047）。若重连卡在"正在重连"类状态、
prepared 永不到达，闸门**永不起下课** → 她永久静默、界面无任何报错。
看门狗方法 `_duplex_force_clear_transition`（20s 强制解除禁言 + 通知 connected）
09-08 就写好了，但全仓 grep **零启动点**——而 2912 的注释一直写着"配合
_duplex_force_clear_transition 看门狗兜底"。**承诺了一条不存在的保险丝。**

**修法**（`src/companion/proactive.py`）：
① `__init__` 加 `_duplex_transition_since = 0.0`；② 闸门拉起处记时间戳；
③ 方法补 elapsed 守卫（没到 20s 不动、没记时间戳不动——老状态安全默认）；
④ 挂 5s 双工诊断 tick（与哑火看门狗 `_check_duplex_deadair` 同 tick，文件既有
idiom，不起新线程）；⑤ 解除时 kind/name/时间戳一并复位，下次拉起重新武装。

**顺带**（`src/llm/cloud_omni_realtime.py`）：删 `_reopen_stream` 死包装——
锁不可重入（653 实锤），真调用方（set_device 热切换 / _run 写入自愈）都在持锁
状态下直接调 `_reopen_stream_locked`，加锁 wrapper 零调用；三处提到它的注释改成
真实路径。

**审计口径**（本次怎么找到它的）：系统性扫「注释承诺了具体机制」的行——从承诺行
抽私有机制名，核对**有定义且有调用**（39 个承诺名）。两个疑似被人工核实**驳回**
（扫描脚本自身的误报，记录在案免得下次重踩）：`_capture_watchdog_loop` 是
`Thread(target=...)` 引用（不带括号，"零调用"是误判，10-23 修的采集看门狗接线
完好）；`_startup_supervisor` 其实**存在**于 main.py:1982——我的 grep 被 `head -15`
截断才误判"不存在"，**差点去修一条正确注释**（"结论必须有证据"的又一处价值）。

**踩坑**：
① **CRLF 工作副本能一直挂着没人发现**：proactive.py 工作副本是 CRLF（早先某次
文本模式写入留下），而 `.gitattributes` 有 `*.py text eol=lf` + `core.autocrlf=true`
→ git 比较时归一化 → `git status` 显示**干净**。已二进制转回 LF（CRLF=9543→0）。
刀必须实测 EOL 不能记。
② 扫描脚本把实例属性（`self._x=`）和回调引用都误判"零调用/无定义"——枚举之后
必须人工核实再动手。
③ 测试里写死的调用数（`>= 3`）在删了死代码后变成谎话 → 从实际调用点取数。

**验证**：`src/tests/test_duplex_transition_watchdog.py` 14 项——[1] 闸门没起不动
（对照）/ [2] 20s 窗口内不动（对照：正常重连不许被强行放行，否则禁言闸门形同虚设）
/ [3] 卡过 20s 强制解除 + kind/name/时间戳复位 + 通知 connected / [4] 没记时间戳
不动 / [5] 三处接线源码断言 / [6] 死包装墓碑。四刀变异全红（M1 阈值 20→99999 即
"永久静默"回归本身 / M2 抽 raise 时间戳 / M3 抽 tick 调用 / M4 死包装复活），每刀
断言红在具体那条；双对照 + 还原后字节一致。全量门禁 **144/144**（12m39s）。

---

### `873ee58` · 出厂模板 24 个死键：用户照着配了，什么都没发生

**问题**：`config.release.yaml` 424 个叶子键里 **24 个没有任何代码读取点**。
用户/队友照着出厂模板配这些项，行为零变化——`voice`/`voice_id` 那类"配了
不算"缺陷的放大版。三个最恶劣的形态：
- `proactive.duplex_mic_vad`：proactive.py:4250 有读
  `getattr(self, '_duplex_mic_vad', True)`，但**全仓没有一处赋值**——读永远落
  默认 True。看着接了线，实则没通电。
- `max_per_type_per_day`：proactive.py docstring 写着"同类活动每日最多 N 次"，
  真实去重机制是 `type_cooldown_s`（冷却制，非每日上限）。文档承诺了一个
  不存在的键。
- `sticker.random_probs.{with,before,after}` 等 9 项：贴纸功能存在
  （sticker_bridge.py），但参数全硬编码（裸 `random.choice`），配置子树整个死。

**删除清单**（24 项）：`perception.ocr.game_stagger`（08-25 已被 game_cadence
节奏门禁取代，game_ocr.py:660 注释自证）、`proactive.accumulate_screen`、
`duplex_mic_vad`、`duplex_mode_lock_mem_pct`、`duplex_mode_lock_util_pct`
（兄弟键 ext_mb/silent 在 9171-9173 真读，这两个是漏接线）、
`duplex_ocr_frame_overlay`、`duplex_timing_pressure`、`game_immersive`、
`heavy_apps_cloud_sync`、`heavy_apps_cloud_url`、`heavy_apps_promote_threshold`
（模块活着、键没人读）、`immersive_interval_s`、`llm_timeout_s`、`max_per_day`、
`max_per_type_per_day`、`react_to_screen`、`sticker.timing_mode`、
`sticker.random_seed`、`sticker.random_cooldown`、`sticker.random_probs`（含
with/before/after 三子键）、`sticker.warmth_high`、`sticker.warmth_override`。
代码侧两处：4250 的假 getattr 改常量 True（附删除理由注释）；docstring 里
max_per_type_per_day 的谎言行删除。

**审计口径（四步，每步都抓出了自己方法的错）**：
① 叶名在 src/*.py 里 grep 引号/点号形态 → 26 个零命中；
② **全仓 ripgrep 复核** → 打回 3 个：`log_keep`/`log_max_mb` 在**仓库根的
main.py:2455-2456** 真读（我的扫描面只有 src/）；`num_ctx` 经
`vars(ollama_cfg.options)` **泛型透传**（叶名不该出现）；`duplex_mic_vad` 的
"读取"其实没有赋值点（从误报变成更严重的定性）；
③ **tokenize 空跑判据**（NAME/STRING token、丢 COMMENT）→ 失败集收敛到
22 + 2 例外，同时暴露**常见词叶名假通过**（`with`/`after` 是 Python 关键词
token，两个死键因此漏过初筛，靠人工普查抓出）——写进闸门 docstring 的已知盲区；
④ 人工核实每个候选的子串误报（`before` 命中 `insertAdjacentHTML("beforeend")`）
与泛型消费（sticker/heavy_apps/perception.ocr 三个子树逐个排除）。

**新闸门** `tools/check_config_keys.py`：模板每个叶子键必须出现在
src/**/*.py + 根入口脚本的 NAME/STRING token 里（tokenize 丢 COMMENT——
注释提一句不算读取点）。例外清单每条带原因：`_release_note`（元信息）、
`ollama.options.*`（泛型透传，ollama_client.py 一行 `vars()` 转发整棵子树）。
**已知盲区写明在 docstring**：常见词叶名假通过，靠人工。

**踩坑**：① 变异刀 M1 锚点写死 `\n`，而模板工作副本是 **CRLF**（`.gitattributes`
要 LF + autocrlf → git 状态显示干净，CRLF 一直挂着）→ 锚点 0 命中。已二进制转
LF + 刀改实测 EOL 构造锚点。② 删除数别写死：模板 424→400（删 25 行、24 个
叶子键——`random_probs` 是父节点），通过数从闸门输出读。

**验证**：两刀变异全红（M1 往模板插 `zzz_dead_probe_key` → 红并点名；M2 抽掉
ollama.options 豁免 → 4 个透传键红，证明清单 load-bearing）+ 双对照；
`test_release_template` 单跑无损（模板断言不依赖被删键）；全量门禁 **146/146**
（12m25s，新闸门 1.6s / 变异 6.6s）。

---

### `404a849` · proactive.py 43 处真空吞清零 + 纳入主链静默闸门

**问题**：`tools/check_silent_except.py` 的主链（用户能直接感知「没反应」的路径）
原先只有 cloud_omni_realtime / dialogue / tts 三个文件。但**主动搭话、双工控制、
看屏互动、档位同步全在 proactive.py**——它不在管辖内。普查（四步：AST 枚举 →
宿主函数分流 → 逐个读上下文定性）发现它有 **43 处 `except: pass` 且块内既无注释
也无日志**，其中承重路径的静默失败直接对应症状：

| 位置 | 静默失败的后果 |
|---|---|
| `_save_duplex_talk_log`（×2） | 双工对话写不进长期记忆 → 通话后她不记得说过什么 |
| `_record_duplex_ai_line`（×2） | 打字侧接不上语音上下文（桥断了） |
| `_maybe_trigger_duplex_memory_extract`（×2） | 语音里说的偏好永远不进记忆 |
| `check_and_trigger` / `_flush_screen_batch` | 话术 LLM 失败 → 这次不开口，零线索 |
| `_on_screen_activity` | 看屏批量循环起不来 → 看屏互动永远不发生 |
| `_stop_comni_engine` | taskkill 失败 → 显存没还干净 |
| `_sync_duplex_mode_from_cfg` | 设置里改的档位不生效 |
| `_rebuild_call_session` / `_reconcile_kb` | 会话重建/KB 切换失败 |

**处理口径**（与其余三文件既有标准一致）：
- **承重路径**：`logger.debug(..., exc_info=True)`——行为不变（原本就是吞），
  但失败从「零线索」变「debug 级可诊断」；
- **有意降级**：补一句理由注释（探针失败按默认值继续 / 任务取消是正常退出 /
  老客户端签名兼容垫 / 清理路径不 raise / 诊断接口少一个字段）。

**闸门扩展**：proactive.py 纳入 `MAIN_CHAIN`（现四文件）。主链现状：158 处静默
全部有注释或日志、**0 真静默**。变异刀加第四刀：往 proactive.py 注入真空吞 →
闸门必须红并点名（不验的话「扩了范围」和「没扩」在汇总里长得一模一样）。

**剩余**：全仓另有 159 处真静默分布在非主链文件（webapp.py 30 处 except-pass 是
下一个候选），按文件逐个推进，不在这轮混入。

**验证**：`check_silent_except --main --strict` 0 真静默；变异四刀全过
（A tts 注入 / B 语法错 / C 文件缺失 / D proactive 注入）+ 双还原字节一致；
全量门禁 **146/146**（12m29s，变异刀 3.5s）。

---

### `8fab483` · webapp.py 真空吞清零 + OpenBLAS 抖动复核 + 登记对账两处误判修复

**① webapp.py 43 处真空吞清零**：主链静默闸门（用户能直接感知「没反应」的路径）
再扩到 UI↔后端之桥。143 处 except-pass 里 43 处块内无注释无日志，全部处理：
- 探针/枚举/清理类（盘符枚举、exe 索引、Start Menu、图标五步提取链、模型目录
  扫描、临时文件清理）→ 补一句理由注释，与其余四文件同标准；
- 承重路径本就上报（试听合成 `_last_tts_error` + `logger.warning`、克隆
  `logger.exception`、配置保存 re-raise）——补的是失败处理里的清理点注释。
主链现状：五文件 300 处静默全部有注释或日志、**0 真静默**。变异刀加第五刀
（往 webapp 注入真空吞 → 红并点名）。

**② OpenBLAS 抖动：串行复核包装器**（`tools/run_test_with_mem_retry.py`）：
全量门禁连跑时 `test_phash` / `test_set_player_name_wiring` 间歇报
`OpenBLAS error: Memory allocation still failed`——单独跑就过、产品无问题，
但红成噪音之后真红就没人看了（smoke_import 2026-10-21 的原话）。修法完全同构：
只认这一种文案（有外部证据：文案来自 OpenBLAS 而非产品代码）、只串行复核一次、
复核仍失败照样红。自测 `tools/test_mem_retry_wrapper.py` 三用例：救得回来的必须救、
真缺陷绝不重试（整个修法最大的失败模式）、救不回来的仍红且留痕。

**③ 登记对账闸门两处误判修复**（都是本轮自己触发、当场抓到的）：
- 包装器式登记（`["tools/run_test_with_mem_retry.py", "src/tests/test_x.py"]`）把
  真测试放在 argv[1]，而闸门只认 argv[0] 为登记身份 → 两个测试被误判「未登记」。
  改：登记身份取 argv 里全部 `.py/.js`（只认脚本后缀——`-m` 式条目的解释器路径
  `python/python.exe` 不是脚本，收进来会撞出假重复）。
- 共用同一个包装器的两个条目被误判「重复登记」。改：去重比对**整条 argv**——
  同一 argv 登记两次才是 10-21 那种有害重复（白跑两遍还互相打架），共用包装器
  跑不同测试是合法设计。

**④ 两处换行符债**：webapp.py 工作副本是 CRLF（`autocrlf` + `eol=lf` 下 git 状态
显示干净，隐患一直挂着）→ 二进制转回 LF；`_qc_quota_tombstone_mutation` 的锚点
写死 `EOL=b"\r\n"`（注释还写着"这三个文件都是 CRLF"）→ 转 LF 后 M1/M3 锚点 0
命中。改成逐文件实测 EOL 后三刀全过。**全仓尚有 174 个跟踪文件是 CRLF 工作副本**
（含 main.py），记录为后续项：转换会响亮地打破写死 CRLF 的刀（闸门抓得到），
但批量转换宜避开并发会话活跃期。

**⑤ doctor 抖动（未决，已加自诊断）**：`test_doctor` 的「当前开发机不应有 FAIL」
在跑闸负载下 4 轮红 2 轮、单跑稳定过。FAIL 条件枚举后排除了端口（占用只 WARN）、
Electron（auto 模式失败只 WARN）、网络（超时只 WARN）；剩 语音合成（真调 SAPI5）
与 核心依赖（doctor 自己 import numpy，可能同根 OpenBLAS）两个候选，但核心依赖
检查把异常文本吞了（行里只有「缺少：numpy」）没法按文案精准重试。处置：断言补上
FAIL 行明细（下次红一行定位），**不猜根因就不加宽泛重试**（会掩盖真 FAIL）。

**验证**：全量门禁 **147/147**（12m48s，新登记「内存抖动复核自测」4.5s、
两个抖动测试走包装器后 1.5s/2.0s、doctor 78.8s）；主链闸门 0 真静默；登记对账
147 登记/8 免登/0 待定，其变异刀四刀仍全红；额度墓碑刀三刀全过。

---

### `09dcbc7` · 听/说/双工三族真空吞清零 + 设置器写入键对账（干净结论）

**① 19 处真空吞清零**（mic_proc 7 / tts_backends 5 / local_omni_duplex 7）。
逐处读过上下文，全部良性，补理由注释（与主链五文件同标准）：
- `mic_proc._open_log/log`：日志候选路径开不了就试下一个、写失败落 stderr 兜底、
  stderr 也没了就丢日志（采集主流程不受影响）；
- `mic_proc.main`：单设备信息/单窗口峰值统计失败 → 跳过该项（诊断路径）；
- `tts_backends.synthesize`：语速微调（`setProperty("rate")`）失败 → 按默认语速
  合成——**不出声才是事故，语速不是**；
- `tts_backends` 四处 `await ws.close()`：关闭失败的残留连接由超时/GC 回收，
  且上层都已 `logger.warning` 并回退全新握手；
- `local_omni_duplex` 四个旋钮设置器：`except (TypeError, ValueError): return`
  是**入参校验**（调用方传错值时不炸链），不是操作失败；`_cascade_run` 的
  CancelledError 是正常退出；补推帧缓存失败不影响本帧推送。

**② 设置器写入键对账（新审计，干净）**：`check_config_keys` 守的是静态侧
（模板键有没有人读）。本次补动态侧：从 webapp.py 提取设置器写进 config 的
79 个叶子键（`section_dict["key"] = value` 形态 + section→配置段人工映射），
逐个核对后端读取点（tokenize NAME/STRING）——**79/79 有着落，0 死键**。
「用户开了开关、什么都没发生」在写入侧不存在。

**为什么不建闸门**：section dict → 配置段的映射是人工的，会随代码重构腐烂——
腐烂的映射意味着漏检，而漏检是静默的（正是这类闸门要防的东西）。按纪律记录为
审计发现：**复核日期 2026-10-24；删除条件=设置器保存路径重构后重跑本对账**。

**验证**：三文件 py_compile + 复扫 0 真静默；全量门禁 **147/147**（12m47s，
doctor 本轮未抖——最近三轮两绿一红）。

---

### `2d2bd51` · 真空吞全仓清零 + check_requirements 闸门沉默修复

**① 范围与结论**：`except: pass` 且块内无注释无日志的「真空吞」——
- src/ 产品代码：**0 处**（本轮 51 + 20 + 20 + 18 + 11 + 14 处分文件清零；
  加上此前的主链五文件 86 处与听说双工三族 19 处，全天累计 ~240 处）；
- main.py（出厂根入口）：0 处；
- tools/ 闸门类脚本（check_/test_/_qc_）：**0 处**；
- 858 处 `except: pass` 现存全部有注释或 `logger.debug(exc_info=True)`。

逐处读过上下文，全是良性：FileNotFoundError 常态、queue.Empty 循环终止、
CancelledError 正常退出、临时文件清理、探测跳过、入参校验守卫、单图/单设备
解码失败跳过。承重的（记忆持久化、话术 LLM、合成播放、会话重建）此前已加
logger.debug——本类没有新缺陷，修的是**可诊断性**。

**② 真缺陷一个**：`check_requirements.collect_imports` 遇 SyntaxError
`continue`——语法坏文件的 import 扫不到，漏声明静默发生，退出码仍 0。
本仓纪律原文：「凡遍历文件的闸门，解析失败必须记 error 并判红」。修为收集
broken 列表 + 显式报出 + `--strict` 判红；OSError（读不了）同理。变异刀
`_qc_requirements_syntax_mutation.py`：坏文件→红且点名「解析失败」、无坏文件
→绿、还原→绿，三态全验。

**③ 过程事故（值得记住）**：给 app_index.py 的 5 处编辑挤在同一条消息里并行
发，读写竞争把文件踩成缩进错乱（IndentationError）。恢复：`git checkout`
还原 → 改**单脚本带断言替换**（每处先 `count(old)==1` 再替换，写入在最后）
→ 后续所有「同文件多处编辑」统一走**行号插入器**（从闸刀取 handler 行号、
从文件取真实缩进、倒序插入注释行）。Edit 工具的 old_string 缩进从截断的
上下文输出里抄，连错两次——**缩进必须从 `cat -A`/Read 的原始输出拿**。

**④ 剩余 backlog（记录在案）**：普通工具脚本（非闸门）还有 ~107 处真空吞，
集中在一次性研究脚本（`_diag_*`/`_do_bake*`/`_archive_*`/`gen_*`/`bake_*`/`ab_*`）
与少量开发期工具（steamvr_vmc_bridge 9 / shell_control 3 / replay_call_gate 3 /
real_software_drive 3 / sim_* 3 / verify_* 7 / mcp_unity 2 / voice_eq_calib 2 /
sticker backend 4 / cloud_duplex_demo / diag_transparency / rate_call_quality /
analyze_call_trace / _frontend_limits）。一次性脚本是冻结产物，注释价值低；
开发期工具可按需推进。随包与打包链路（doctor/pack/make_dist）本轮已清。

**验证**：全量门禁 **148/148**（12m00s，新增「依赖声明变异」项）；打包排除
变异四刀全过；doctor 单跑 18 PASS / 1 WARN / 0 FAIL；63 文件 +256/−7。