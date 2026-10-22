"""一条命令跑完所有静态与冒烟闸门。

为什么需要它：闸门散在 tools/ 下十几个脚本里，靠人记一定会漏（本项目已经漏过）。
这里只做"编排 + 汇总"，不重写任何检查逻辑——检查的单一真值源仍是各自的脚本。

用法：
  python tools/check_all.py            # 全量（含导入冒烟与回归测试，约 1~2 分钟）
  python tools/check_all.py --fast     # 跳过慢项，改代码时的快速回环（约 4 分钟，不是 5 秒）
  python tools/check_all.py --list     # 只看有哪些闸门

退出码：0=全部通过；1=有闸门失败（失败项会列出来）。
子脚本退出码 77 = 跳过：该能力当前不是产品承诺、本机环境不满足，
会单列为「跳过（未验证）」并打印原因，**不计入通过**。
目前只有 `壳自愈端到端` 用这个约定（壳是实验特性，见 src/ui/pet_backend.py）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_RUNTIME = os.path.join(ROOT, "python", "python.exe")   # 内置运行时
PY_VENV = os.path.join(ROOT, ".venv", "Scripts", "python.exe")  # 装了三方依赖的解释器

# (名称, 解释器, 参数, 是否慢项)
GATES: list[tuple[str, str, list[str], bool]] = [
    # 三道「全仓级」闸门的扫描范围必须名副其实：src + tools + 根目录脚本。
    # 2026-10-18 专项体检（闸门名 vs 实际覆盖）的整改：这三道原本只圈
    # src main.py，tools/ 整棵树在视野外——doctor.py 的 return AUTO
    # NameError、_append_banter2_log.py 的换行 corruption 都因此漏进过 HEAD。
    # 文本编码还必须带 --strict：检查器默认只打印计数、永远 exit 0，
    # 少了它这就是一道装饰闸门（曾长期如此）。
    ("字节码编译", PY_RUNTIME, ["-m", "compileall", "-q", "src", "tools", "main.py", "launch_companion.py", "run_webui.py"], False),
    ("全局名完整性", PY_RUNTIME, ["tools/check_module_globals.py", "--all", "src", "tools", "launch_companion.py", "run_webui.py"], False),
    # 协程调度：两个方向都查——`await 同步方法`（await None 抛 TypeError，被 except
    # 吞掉就是功能静默失效；微信回复一条发不出去就是这么来的）与 `async 方法没被
    # await/调度`（协程对象直接丢弃，函数体一行不执行）。自测守着闸门本身别退化。
    ("协程调度", PY_RUNTIME, ["tools/check_await_sync.py", "src"], False),
    ("协程调度自测", PY_RUNTIME, ["src/tests/test_check_await_sync.py"], False),
    # 异步阻塞：async 里做同步 HTTP/sleep 会把事件循环占住（"卡一下"，不报错）。
    # 一天内撞到四处：gewechat 回发、qzone 发布、tts 的 time.sleep(0.6)、
    # heavy_apps 的 urlopen(timeout=5)。嵌套同步 worker（交给 to_thread）不算。
    ("异步阻塞", PY_RUNTIME, ["tools/check_async_blocking.py", "src"], False),
    ("异步阻塞自测", PY_RUNTIME, ["src/tests/test_check_async_blocking.py"], False),
    ("文本编码", PY_RUNTIME, ["tools/check_open_encoding.py", "--all", "--strict", "src", "tools", "main.py", "launch_companion.py", "run_webui.py"], False),
    # 主链路静默异常：`except: pass/continue` 且块内无注释 = 出错零痕迹
    # （NameError 和"设备不存在"长得一模一样）。--strict 才判红；带注释的有意降级放行。
    ("主链路静默异常", PY_RUNTIME, ["tools/check_silent_except.py", "--main", "--strict"], False),
    ("前端资源", PY_RUNTIME, ["tools/check_web_assets.py", "--strict"], False),
    # 前端 JS 语法：一处语法错误会让整个 <script> 块静默不执行（该块里所有按钮全哑），
    # 而 check_web_js_syntax 原来打印完就 exit 0 —— 是装饰闸门。本机没 node 时
    # 按 77 跳过（单列「未验证」，不计通过）。
    # 【2026-10-21 扩】原来只查 index.html 一个页面 + 两个硬编码的外链 JS，设置页/
    # 引导页/通知页全在盲区。现在扫全站（46 页 / 65 内联块 / 23 个 JS = 88 段），
    # 扩完第一处就抓到 probe2.html 把 '\n' 写成真换行、整块从未执行。
    # 88 段走一个 node 进程（vm.Script / vm.SourceTextModule 按加载上下文选目标），
    # 1.9s；逐段起 node --check 要 21s。
    ("前端 JS 语法", PY_RUNTIME, ["tools/check_web_js_syntax.py"], False),
    # 语法闸门的变异：三把刀各打一种载体（非首页内联块 / 原名单外的外链 JS /
    # 首页的非第一块）。标慢项：它会改源码再还原，不能与其他闸门并发。
    ("前端 JS 语法变异", PY_RUNTIME, ["tools/_qc_web_js_syntax_mutation.py"], True),
    # HTML 标签配平：`index.html` 的通话浮层 `#callOverlay` 曾经**从来没被关上**
    # （call-screen 漏一个 </div>），靠浏览器在 </body> 处自动补，运行时毫无症状。
    # 判据用「容错匹配」：只报真漏标签，放过浏览器能确定性恢复的错位嵌套。
    ("前端 HTML 配平", PY_RUNTIME, ["tools/check_html_balance.py"], False),
    # 桥接三层：index.html 的 Backend.xxx → app_backend.js 的 call('py_name') →
    # CharacterAPI。任一层断掉都是"点了没反应"，控制台之外没有任何痕迹。
    # 2026-10-21 补了「外链 JS」这一层（原来只扫 HTML 内联 script，引导页那个
    # 2000 行的 onboarding_role.js 整个不可见 —— 里面就躺着一处假进度条）。
    ("前端桥接", PY_VENV, ["tools/check_bridge_methods.py"], False),
    # 桥接闸门自己的变异（2026-10-21）：这道门连着两次"看着绿、其实整文件没被扫"，
    # 而它没有变异脚本。四把刀各打一条路径：内联+别名（index.html / 另一页面）、
    # 外链 JS、以及"内联扫描退化成空集"。约 2s，标慢项（会改源码再还原）。
    ("桥接变异", PY_VENV, ["tools/_qc_bridge_mutation.py"], True),
    # 桥接**守卫**（2026-10-21）：`typeof Backend.x === 'function'` 这种形态上面那道门
    # 看不见 —— 它长得像"能力探测"，不像调用。而它会把「这个版本确实没有」和「名字写错 /
    # 适配层漏导出」压成同一种结果：静默什么都不做。实测就抓到一处（probeVisionModel →
    # 额度提醒浮层「再试一次」点了只是把按钮恢复原样）。判据：守卫名必须在导出表里。
    ("桥接守卫", PY_VENV, ["tools/check_bridge_guards.py"], False),
    # 四把刀：①导出少了（今天这个 bug 原样）②页面出现不存在的守卫 ③**正则坏了**
    # （0 命中不许当成通过，靠闸门里的 MIN_GUARDS 兜底）④对照组（注释里复述守卫名必须
    # 仍然绿，否则"剥注释"这层判据可能根本没生效）。会改源码再还原，标慢项。
    ("桥接守卫变异", PY_VENV, ["tools/_qc_bridge_guards_mutation.py"], True),
    # 广播 type 对账（2026-10-22）：前端 host message 监听器里分发的 type，后端必须真的
    # 会广播。两边靠一个字符串 type 对上，而**没有任何机制保证它对得上**：
    # `d.type === 'character_switched'` 这条分支自 2026-10-02 写下起就是死的（后端只经
    # ws_server 广播 {message_type, payload}，而 app_backend.js 没有 WebSocket 客户端）
    # → 切角色后界面里写死的名字不跟随，三个月没人发现。症状永远静默：不执行、不报错。
    # 只做**单向**（前端 ⊆ 后端全集）：反向会假红——pet_tip 就走 api.pop_pet_bubble 轮询，
    # 桌宠窗的 chrome.webview 监听对 PostWebMessageAsJson 完全不触发（rig 页 2026-09-03 实锤）。
    ("广播 type 对账", PY_RUNTIME, ["tools/check_broadcast_types.py"], False),
    # 七把刀：①**原 bug 原样**（后端不广播 character_switched → 必须红，证明闸门在修复前
    # 确实是红的）②前端多一个后端不广播的分支 ③④对照组（页面内部 postMessage /
    # dataset.type 不许误伤——全仓库 25 处以上非消息 .type 比较，一刀切必假红）
    # ⑤少一个 host message 监听器 → exit 2（0 命中不许当成通过）⑥监听器注释里提的
    # type 不算分发分支 ⑦广播的 type 不是字面量 → exit 2。会改源码再还原，标慢项。
    ("广播对账变异", PY_RUNTIME, ["tools/_qc_broadcast_types_mutation.py"], True),
    # 死引用看门狗：`getElementById('x')` 拿到 null 是**静默**的（JS 不报错，
    # 只是 `if(el)` 恒假、那段逻辑永不执行）。它自带 KNOWN_DEAD 白名单（每条写了原因）。
    # 【2026-10-21 收进闸门】这个工具早就写好了，docstring 也写着"纳入自测后即成为回归
    # 闸门"，但两件事一直没做：①它硬编码 `ROOT = r"G:\ai对话\11"` —— 仓库搬家后**从那天
    # 起就跑不了**，而它不在闸门里所以没人发现；②它把结果写到 `docs/_audit_dead_ids.txt`，
    # 而 docs/ 不是忽略目录 → 每跑一次工作区就多一个未跟踪文件。
    # 现在：ROOT 按文件位置推导、产物写 logs/、收进闸门（约 0.2s）。
    ("前端死引用", PY_VENV, ["tools/audit_dead_ids.py"], False),
    ("凭据/隐私", PY_RUNTIME, ["tools/check_package_secrets.py"], False),
    # ${ENV} 引用与 dpapi: 密文都不算泄漏（本轮修的两处假警噪点）
    ("凭据扫描正反", PY_VENV, ["src/tests/test_package_secrets_scan.py"], False),
    ("凭据日志卫生", PY_RUNTIME, ["tools/check_log_leak.py"], False),
    ("依赖声明", PY_VENV, ["tools/check_requirements.py", "--strict"], True),
    # 【2026-10-21 删·重复登记】这里原来还有一行
    #   ("导入冒烟", PY_VENV, ["tools/smoke_import.py", "--strict"], True)
    # 而列表末尾**已经有一份带完整注释的**。同一项被登记两次 → 每次全量闸门都白跑
    # 两遍（各约 28s，全量里占 ~1 分钟），而且**两遍的通过/失败会互相打架**：
    # 实测本轮第一遍 ✓ 27.9s、第二遍 ✗ 95.0s，最后判 FAIL —— 光看汇总根本分不清
    # 是"测试本身不稳"还是"跑了两遍"。判据只该有一条真身。
    ("壳层能力", PY_VENV, ["src/tests/test_win_shell.py"], True),
    ("RPC 通道", PY_VENV, ["src/tests/test_rpc_server.py"], False),
    # WS 端口绑定契约（2026-10-20 03:11 启动崩溃的回归）：空闲一次起、被占先重试
    # 再报人话（PID/退出指引）、占用探测不扰民。两刀变异已验：删重试→「确有重试」
    # 红；改回裸 raise→三条诊断断言红。
    ("WS 端口绑定", PY_VENV, ["src/tests/test_ws_port_bind.py"], False),
    # README/文档的本地链接不悬空。坏法很安静——**没人会点**，断了也不会有人报错，
    # 直到新来的人照着点一次 404。而"文档搬家"正是最容易制造悬空的动作。
    # 变异 3 刀全红：搬家没改链接 / 图片路径写错 / 指向不存在的文档。
    ("文档链接不悬空", PY_VENV, ["tools/check_readme_links.py"], False),
    ("壳桌宠回退", PY_VENV, ["src/tests/test_pet_shell_fallback.py"], False),
    ("桌宠后端决策", PY_VENV, ["src/tests/test_pet_backend.py"], False),
    ("桌宠位置持久化", PY_VENV, ["src/tests/test_pet_state.py"], False),
    ("壳自愈与退出清理", PY_VENV, ["src/tests/test_pet_shell_selfheal.py"], False),
    ("壳自愈端到端", PY_VENV, ["tools/pet_shell_selfheal_smoke.py"], True),
    ("日志轮转", PY_VENV, ["src/tests/test_log_rotate.py"], False),
    ("配置校验", PY_VENV, ["src/tests/test_config_schema.py"], False),
    # 「能说」的入口体检：失效 TTS 模型 / 音色档的启动期迁移。2026-10-19 合并
    # 队友工作树时抓到 migrate_stale_voice_profiles 是死代码（定义完整、注释完整、
    # 零调用）——用户已建的音色档绑着下线模型，试听永远 400 而启动期从没人修。
    # 这道测的重点是「接线」而不是「算得对」：唯一入口是否真跑两半、webapp 是否
    # 真调了入口、写盘是否真是原子替换（不是原地截断）。
    ("TTS 配置迁移", PY_VENV, ["src/tests/test_config_migrate.py"], False),
    # 「别一直说同一句话」：屏幕活动入缓冲时去掉几乎相同的摘要。_similar_summary
    # 写好很久但零调用（注释里承诺的"防重复搭话"实际只靠输出侧兜着），轨迹里
    # 堆满"在游戏；在游戏；在游戏"既烧 token 也更容易让模型顺着重复。
    ("屏幕摘要去重", PY_VENV, ["src/tests/test_screen_dedup.py"], False),
    # 敏感窗口跳过：屏幕采集每 2s 抓一帧全屏，网银/密码管理器一旦进模型上下文
    # 就撤不回来。这条守的是「命中时真的没抓屏」而不是「函数返回对不对」，
    # 外加 main.py 两处装配都得传 foreground_check（漏一处那条路径就静默不设防）。
    ("敏感窗口跳过", PY_VENV, ["src/tests/test_screen_skip_apps.py"], False),
    # 感知指示器：桌宠右下角那个点。守的不是"页面上有个点"，而是
    # ①亮灭由后端真实状态决定（写死 class=on 就是骗人）②走页面轮询而不是
    # 跨线程 evaluate_js（main_window.py:454 有自锁实锤）。
    ("感知指示器", PY_VENV, ["src/tests/test_perception_indicator.py"], False),
    # 「没配大脑」承接浮层：首启不拿填表拦人，代价是第一次点发送只会得到「未发送」。
    # 这条守四件事：①只在失败那一刻亮（进页面就亮=顶部条换个位置）②判据是能力槽
    # 状态不是 error 文案 ③判据三态——后端没就绪是「问不出来」，不是「没配」
    # ④文案承诺的「原话还在输入框里」有代码保证。判据真值表直接交给 node 跑，
    # 不在 Python 里复刻逻辑（复刻只能证明"我两次写得一样"）。变异 9 刀全红。
    ("首启承接浮层", PY_VENV, ["src/tests/test_setup_hint.py"], False),
    # 会话上下文落盘（写穿透 / 重启回填 / 老日志迁移 / **初始化顺序无关**）。
    # 最后那条是 2026-10-21 补的：`_make_history()` 只在建桶那一刻决定挂不挂写回调，
    # 顺序反过来（先 shared_history 后 enable）那个桶就永远纯内存——对话照常、日志正常、
    # 重启全丢。这条测试此前 198 行、27 项全绿，却**从来没进过闸门**。
    ("会话上下文落盘", PY_VENV, ["tools/test_context_persistence.py"], False),
    # 【2026-10-21】「测试写了没进闸门」普查：仓库里 108 个 test_*/check_*，54 个不在名单上。
    # 下面这批是**逐个跑通确认后**才加的，不是按文件名猜的。
    # 其中 5 个（tools/test_*.py）原来硬编码 `G:\ai对话\11`——旧项目路径，本机早没了，
    # 于是它们从搬进本仓库那天起就跑不了，自然也没人发现它们不在闸门里。
    # `test_write_layer` 还额外缺退出码（FAIL 也返回 0），是典型「装饰闸门」，一并修了。
    # 剩下的 42 个仍在待评估清单里（多数是一次性诊断脚本或需要真机/网络），不要盲加。
    ("io 安全三态", PY_VENV, ["src/tests/test_io_safe.py"], False),
    ("感知哈希", PY_VENV, ["src/tests/test_phash.py"], False),
    ("密钥盒加密", PY_VENV, ["src/tests/test_secret_box.py"], False),
    ("开机自启", PY_VENV, ["src/tests/test_autostart.py"], False),
    ("裁判确认条款", PY_VENV, ["tools/test_judge_confirm_clause.py"], False),
    ("陪伴话术条款", PY_VENV, ["tools/test_prompt_companion_clause.py"], False),
    ("历史隔离", PY_VENV, ["tools/test_history_isolation.py"], False),
    ("js_api 状态分叉", PY_VENV, ["tools/test_jsapi_state_split.py"], False),
    ("虚构守卫", PY_VENV, ["tools/test_fabrication_guard.py"], False),
    ("主动开口冒烟", PY_VENV, ["tools/test_proactive_smoke.py"], False),
    ("关系主动修复", PY_VENV, ["tools/test_relationship_proactive_fixes.py"], False),
    ("写入层", PY_VENV, ["tools/test_write_layer.py"], False),
    # 第二批普查（2026-10-21 02:0x）：又试跑 8 个，6 个能过 → 收进来。
    # `test_character_card_images` 原本 3 FAIL 是**假红**：`/local_img` 路由随 UI 拆分
    # 搬进了 `main_window.py`，测试却还只看 `webapp.py` → 白名单三条恒 FAIL。
    # 改成拼接两份源码后 21 PASS / 0 FAIL。
    # 两个**故意不进**的（红着，且不是"能跑就守"能解决的）：
    #   · `tools/test_call_engine_resolve.py` 13/20 —— 用例 1.2/2.2 自相矛盾
    #     （1.2 要 config=local_omni 走本地，2.2 说运行态 local_omni 走云端★新策略），
    #     6.x 的错误码 `no_cloud_duplex_cfg` 已被新门控 `no_cloud_duplex` 抢先拦下。
    #     疑似测试未跟上策略变更，**需人工裁决**，别盲改测试也别盲改代码。
    #   · `tools/check_models_layer.py` 15/16 —— `[FAIL] 引擎表漂移: 仅我有={'edge-tts'}`。
    #     webapp 侧 10-16 已按发布口径下架 edge-tts，`src/models/protocols.py` 的
    #     TTS_ENGINES 还留着。是真漂移，但删引擎要走老配置迁移验证，单独处理。
    ("关系深度档", PY_VENV, ["tools/test_relationship_depth_tier.py"], False),
    ("关系裁判", PY_VENV, ["tools/test_relationship_judge.py"], False),
    ("记忆片段", PY_VENV, ["tools/test_memory_episode.py"], False),
    ("里程碑发射", PY_VENV, ["tools/test_milestone_emit.py"], False),
    ("上下文召回", PY_VENV, ["tools/test_context_recall.py"], False),
    ("角色卡图片", PY_VENV, ["tools/test_character_card_images.py"], False),
    # 投影层自检（2026-10-21 02:1x 转绿后收进来）：它比的是 `src/models/protocols.py`
    # 的 TTS_ENGINES 与 webapp 的 `_VOICE_PROFILE_ENGINES` —— 两处各存一份，天然会漂移。
    # 本轮抓到真漂移：models 层还列着 10-16 已按发布口径下架的 `edge-tts`。
    # 顺手治了一条脆弱判据：原来硬编码 `len(TTS_ENGINES) == 11`，删一个引擎就红，
    # 而数量本身不表达"为什么是 11"；改成"非空下限 + 由白名单逐字比对守具体集合"。
    # 变异（把 edge-tts 塞回表）→ `[FAIL] 引擎表漂移`，退出码 1。
    ("投影层自检", PY_VENV, ["tools/check_models_layer.py"], False),
    # 【2026-10-21】doctor「配置文件」检查项的升级覆盖检测：原判据 `raw_cfg.get("api_key")`
    # **恒真**（顶层根本没这个键），只要有 data/ 就恒报「配置可能被覆盖」——所有真正在用的
    # 人都看到假警。改成递归扫任何非空 api_key。三态 + 对照组（有 Key 的老用户不许报）。
    # 变异（判据退回顶层 api_key）→ 场景 2/4a 变红，退出码 1。
    ("配置覆盖检测", PY_VENV, ["src/tests/test_doctor_config_warn.py"], False),
    # 「发给队友的包里到底有什么」：包内容取 git ls-files（.gitignore 是单一事实源），
    # 再叠一层硬排除。这层防御在真实仓库上**一项都不会触发**（gitignore 早洗过了），
    # 所以必须直接对规则表喂路径来证明它是活的——变异 4 刀（规则恒 None / 删红线
    # 文件名表 / 删 data 目录 / 少一项必需文件）全红。
    ("源码包排除规则", PY_VENV, ["src/tests/test_pack_for_teammate.py"], False),
    # 打包排除规则的变异刀（2026-10-22 新增项）：`src/ui/web/redesign/` 下 14 个
    # `_` 前缀文件（设计稿 / 重构前备份 / 测试台，约 1.1MB）零引用却一路进队友包，
    # 队友解包后能在 URL 里打开"能点但没反应"的旧 UI。加 EXCLUDE_DEAD_WEB 精确清单
    # （**不用前缀一刀切**：`_gpu.txt` 同样带 `_` 但是 `_has_usable_gpu()` 写的
    # 运行时数据）。四刀：清空清单 / 删判定 / 按前缀一刀切（对照组，_gpu.txt 必须被
    # 误排才算抓到）/ 扫描目录失效。
    ("打包排除变异", PY_VENV, ["tools/_qc_pack_exclude_mutation.py"], False),
    # 「出厂状态」的守门人：新用户第一眼看到的东西（模板首启开关 / 默认唤醒词 /
    # 应用图标 / 代码里的兜底角色名）。这几项在开发机上永远是对的，只有新用户
    # 才会撞上，所以不能靠"我这儿跑着没问题"。变异 3 刀（首启开关 / 唤醒词 /
    # 图标不进包清单）全红。
    ("出厂模板与首启", PY_VENV, ["src/tests/test_release_template.py"], False),
    ("doctor 自检结构", PY_VENV, ["src/tests/test_doctor.py"], False),
    # 「她现在能说话吗」：engine 为空会落到静音后端（不出声、不联网、零日志）。
    ("语音合成判级", PY_VENV, ["src/tests/test_doctor_voice_backend.py"], False),
    # 「听得见吗」：vosk 语言代码写成 zh-cn（应为 cn）+ model_path 指向
    # 不存在的目录，两口堵死自动下载。闭环测试 + doctor 判级测试。
    ("ASR 听得见", PY_VENV, ["src/tests/test_asr_voice_pipeline.py"], False),
    ("语音识别判级", PY_VENV, ["src/tests/test_doctor_asr.py"], False),
    # 「理不理你」：small-cn 词图没有"达妮娅"整词，整词语法让她名字被静默跳过；
    # 改单字+同音字语法 + 拼音容错匹配（娅/亚 同音也算中）。单测 + 变异 6 刀全红。
    ("唤醒词命中", PY_VENV, ["src/tests/test_wake_word.py"], False),
    ("唤醒词变异", PY_VENV, ["tools/_qc_wake_mutation.py"], False),
    # 「醒了却不理你」：唤醒→指令识别→USER_TEXT 的链路闭环。sherpa 主路径与 vosk
    # 回退路径都必须发出 USER_TEXT（喂给 LLM 的那一棒）。双引擎事件流单测 +
    # 变异 3 刀（断模式切换 / 吞 sherpa 文本 / 吞 vosk 文本）全红。
    ("ASR 事件流", PY_VENV, ["src/tests/test_asr_event_flow.py"], False),
    ("ASR 事件流变异", PY_VENV, ["tools/_qc_asr_event_flow_mutation.py"], False),
    # 「听到却不回答」：USER_TEXT(source=voice) 被 Dialogue 消费后，必须真的产出一条可
    # 播放的 AI_DECISION。桌面来源走 create_task 后台任务，而 respond 尾段（剥 markdown /
    # 记历史 / 发决策）在 respond 内部 try/except 之外 → 一旦崩，异常落无人接的 fire-and-
    # forget 任务，静默吞掉。单测守「正常链有回复 + 尾段崩有失败气泡兜底」；
    # 变异一刀（摘掉 create_task 的 done_callback）必须让失败气泡消失（翻回静默）。
    ("语音消费链", PY_VENV, ["src/tests/test_dialogue_voice_chain.py"], False),
    ("语音消费链变异", PY_VENV, ["tools/_qc_dialogue_voice_chain_mutation.py"], False),
    # 配置引用体检：路径指错 / 角色没绑引擎 → 红；可选功能（auto_start:false）
    # 与端口未就绪 → 只提示。扫进仓模板，CI 上也能跑。
    ("配置引用体检", PY_VENV, ["tools/_qc_path_refs.py", "config.release.yaml"], False),
    ("配置引用判级", PY_VENV, ["src/tests/test_config_path_refs.py"], False),
    # 模型渠道探活的判级（本轮事故的直接教训：百炼账户 400 欠费，配置里却"填好了"）。
    # 测试 ⑦ 组 + 变异 7 刀全红（tools/_qc_channel_mutation.py）。
    ("渠道探活判级", PY_VENV, ["src/tests/test_doctor_llm_channel.py"], False),
    # 【2026-10-22 补登记】上面那条的变异刀**一直没进闸门**，注释里写着"变异 7 刀全红"
    # 却从没在这里跑过 —— 而它已经烂了：七刀里 C/D/F/G 四把锚点命中 0 次被静默跳过
    # （根因：`J()` 写死 CRLF，而 doctor.py 早变成了 LF）。也就是说那四组判级断言
    # （断网不判红 / 空 key 不发包 / 5xx 不判红 / run_all 必须接线）其实没有牙齿。
    # 现已修好锚点（_eol() 实测换行符）并登记，锚点再失效会直接让全量门禁变红。
    ("渠道探活判级变异", PY_VENV, ["tools/_qc_channel_mutation.py"], True),
    # 配置里的 ${ENV} 引用：展开、缺失告警、保存不把明文 key 落盘。
    ("配置环境变量引用", PY_VENV, ["src/tests/test_config_env_refs.py"], False),
    ("打包依赖分级", PY_VENV, ["src/tests/test_make_dist_deps.py"], False),
    ("队列语义", PY_VENV, ["src/tests/test_queue_util.py"], True),
    ("通话冒烟", PY_VENV, ["src/ui/tests/test_call_smoke.py"], True),
    ("通道状态", PY_VENV, ["src/channels/tests/test_agent_status.py"], True),
    # 微信回调：无签名 POST 的鉴权边界 + AI 决策回发（曾因 `await 同步函数`
    # 导致回复一条都发不出去）。变异 5 刀全红，见 tools/_qc_gewechat_mutation.py。
    ("微信回调边界", PY_VENV, ["src/channels/tests/test_gewechat_callback.py"], False),
    # 【2026-10-22 补登记】同上的"写了没进闸门"：五刀（同步 def / 同步调用阻塞循环 /
    # 鉴权恒放行 / 去掉 fail-closed / 默认回 0.0.0.0）实测全红、还原字节一致，
    # 但注释里提过它、名单里没有它 → 那把刀从没被跑过。会改源码再还原，标慢项。
    ("微信回调变异", PY_VENV, ["tools/_qc_gewechat_mutation.py"], True),
    # 空间发布：publish 是同步 urllib（超时 15s），在事件循环线程里直接调会卡住
    # TTS/对话/桌宠。变异：改回同步调用 → "卡住 406ms" 被抓住。
    ("空间发布不阻塞", PY_VENV, ["src/channels/tests/test_qzone_nonblocking.py"], False),
    ("Key 归一", PY_VENV, ["tools/test_key_unify.py"], True),
    ("能力槽位", PY_VENV, ["tools/test_capability_slots.py"], True),
    ("OneBot 协议", PY_VENV, ["src/channels/tests/test_onebot.py"], True),
    ("OneBot 反连鉴权", PY_VENV, ["src/channels/tests/test_onebot_reverse_auth.py"], True),
    ("命令白名单", PY_VENV, ["src/llm/tests/test_run_command_whitelist.py"], True),
    # ---- 第三批测试普查（2026-10-21）：27 个没进闸门的测试逐个试跑，修好 7 个，
    #      连同本来就绿的 13 个一起收进来。修的过程抓到 1 个出厂缺陷
    #      （模板里三个社交通道默认 enabled: true，新用户装完就去连不存在的本地服务）
    #      和 3 类「测试与产品脱节」：桩缺导出名、回调签名过时、fire-and-forget 时序。
    ("通道接线", PY_VENV, ["src/channels/tests/test_config_wiring.py"], False),
    ("桌面视频", PY_VENV, ["src/channels/tests/test_desktop_video.py"], False),
    ("桌面视频 e2e", PY_VENV, ["src/channels/tests/test_desktop_video_e2e.py"], False),
    ("双工链路路由", PY_VENV, ["src/channels/tests/test_duplex_link_routing.py"], False),
    ("空间说说", PY_VENV, ["src/channels/tests/test_gewechat_qzone.py"], False),
    ("媒体路由", PY_VENV, ["src/channels/tests/test_media_routing.py"], False),
    ("视频观看", PY_VENV, ["src/channels/tests/test_video_watch.py"], False),
    ("视频进度", PY_VENV, ["src/channels/tests/test_video_watch_progress.py"], False),
    ("双工停止与麦克风", PY_VENV, ["tools/test_duplex_stop_and_mic.py"], False),
    ("记忆反思", PY_VENV, ["tools/test_memory_reflect.py"], False),
    ("记忆合成", PY_VENV, ["tools/test_memory_synthesis.py"], False),
    ("里程碑落盘", PY_VENV, ["tools/test_milestone_persist.py"], False),
    ("模型下载器", PY_VENV, ["tools/test_model_downloader.py"], False),
    ("P1 增量", PY_VENV, ["tools/test_p1_incremental.py"], False),
    ("主动开口空闲", PY_VENV, ["tools/test_proactive_boot_idle.py"], False),
    ("关系模型", PY_VENV, ["tools/test_relationship.py"], False),
    ("关系里程碑", PY_VENV, ["tools/test_relationship_milestone.py"], False),
    ("抓屏性能闸", PY_VENV, ["tools/test_screen_capture_perf_guard.py"], False),
    ("看屏热生效", PY_VENV, ["tools/test_desktop_watch_hotapply.py"], False),
    ("引导页 TTS 键", PY_VENV, ["tools/test_onboarding_tts_key.py"], False),
    # ---- 第三批普查的第二轮（同日）：又修好 5 个，同样是"判据/桩过时"而非功能坏。
    #      test_onboarding_role_backend 退出码 0 但跑 29s，不进闸门。
    ("额度告警", PY_VENV, ["src/channels/tests/test_quota_alert.py"], False),
    # 额度墓碑的变异刀（2026-10-22）：test_quota_alert 加了两层墓碑（Router 层 +
    # 产品层），没有变异就分不清"判据正确"与"恒真"。三刀：注册点加回必须红 /
    # 前端 quota_alert 分支加回必须红 / 只在注释里提名字必须仍绿。
    ("额度墓碑变异", PY_VENV, ["tools/_qc_quota_tombstone_mutation.py"], False),
    ("TTS 多供应商", PY_VENV, ["tools/test_tts_multi_provider.py"], False),
    ("主动输出分流", PY_VENV, ["tools/test_proactive_chat_routing.py"], False),
    ("关系接线", PY_VENV, ["tools/test_relationship_wiring.py"], False),
    ("关系集成", PY_VENV, ["tools/test_relationship_integration.py"], False),
    # 关系面板的两个新入口（2026-10-21）：原型下拉 + 最近事件列表。判据是"界面变成
    # 什么样"，所以**原样加载 relation_panel.js** 在假 DOM + 假 pywebview 里跑（不是
    # 复刻逻辑）。24 项，含两个对照组：老后端没有 archetypes 时**不许**写死枚举兜底、
    # 后端没连上时不许把下拉变成空壳。
    ("关系面板", PY_VENV, ["tools/test_relation_panel.py"], False),
    # 四刀：调错方法名 / 下拉写死枚举 / 事件行走 innerHTML / 不再拉事件。会改源码再
    # 还原，标慢项。
    ("关系面板变异", PY_VENV, ["tools/_qc_relation_panel_mutation.py"], True),
    # 克隆面板「预填她的」（2026-10-22 修的真缺陷）：预置角色的参考音频随包分发
    # （_role_assets/ref/xiaowei.wav），而旧面板**预填写一份、按钮读另一份**——
    # 试听/开始克隆只读 c.voice.ref，预置角色里没有 ⇒ 试听静默失效、克隆画假进度条
    # 还谎报「克隆完成」（用户机器上什么都没发生）。判据是"点了以后发生什么"，
    # 所以 jsdom **原样加载** onboarding_role.js 整文件真点真走（不抽片段）。
    # 四个场景含两个对照组：随包资产 / 用户自选文件 / 无桥。缺 node 或 jsdom → 77。
    ("克隆面板真值表", PY_VENV, ["tools/test_clone_card_ui.py"], False),
    # 六刀：试听读回 voice.ref / 随包资产不物化 / 无桥改回假进度谎报 /
    # 预置加回开发机路径 / 后端白名单前缀改严 / 去掉 wav 音频类型。会改源码再还原。
    ("克隆面板变异", PY_VENV, ["tools/_qc_clone_card_mutation.py"], True),
    # 后端侧：use_bundled_ref_audio 把随包资产物化成 data/voice_ref/ 真实路径 +
    # 白名单（防路径穿越）+ /local_img 音频 Content-Type + register_local_img_dir。
    ("参考音频物化", PY_VENV, ["tools/test_clone_ref_audio.py"], False),
    # ---- 引导页集成（106 项）----
    # 【为什么现在收了】上一轮我判定「不收：5 个 FAIL 是设计稿未落地的产品待办」——
    # 那个判断是**错的**。逐个查下来 5 个里没有一个是"要动产品"的：A1 拿仓库里
    # 从没进过库的本地设计稿当判据、A4 盯着两种已不存在的代码形态、A9 是
    # `_SHELL_GUARD` 搬进 main_window.py 后判据没跟着搬、B5 拿本机显存当判据、
    # B15 则**抓到一处真缺陷**（`_save_config` 里的幽灵调用 `refresh_characters_map`，
    # 见 webapp.py 该处注释）。这正好说明"红着就挂着"会同时藏住真 bug 和过时判据。
    # 【代价】约 11s，标慢项，`--fast` 跳过。
    # 【输入】它把 config.release.yaml 复制到临时目录当沙盒（绝不写真实配置），
    # 所以验的是**出厂状态**而不是"我这台机器现在什么样"。
    ("引导页集成", PY_VENV, ["tools/test_onboarding_integration.py"], True),
    # ---- 变异脚本（判据的判据）：验证闸门里的检查器**真的还有牙齿** ----
    # 【为什么必须收进来】2026-10-21 实测：doctor.run_all 里加了一项之后，
    # `_qc_voice_mutation` 的 A/B/C/E 四刀与 `_qc_asr_mutation` 的若干刀
    # 全部"锚点命中 0 次"→ 整把刀被跳过。脚本本身是健全的（它把"跳过"记成失败），
    # 但**没人跑就没人知道**——那 4 个检查器（语音合成/语音识别/配置引用/静默吞异常）
    # 都还在闸门里跑，等于一直在跑一个"可能已经没有牙齿"的判据。
    # 【代价】合计约 43s（12+12+17+2），故全标慢项，`--fast` 跳过。
    # 【注意】这些脚本会**改源码再还原**（二进制读写 + finally 还原 + 末尾 hash 校验）。
    # **不要并发跑两份 check_all**，否则两份会同时改同一个文件、互相破坏。
    ("静默吞异常变异", PY_VENV, ["tools/_qc_silent_mutation.py"], True),
    ("语音合成变异", PY_VENV, ["tools/_qc_voice_mutation.py"], True),
    ("语音识别变异", PY_VENV, ["tools/_qc_asr_mutation.py"], True),
    ("配置引用变异", PY_VENV, ["tools/_qc_pathrefs_mutation.py"], True),
    # 导出聊天记录（2026-10-21 新增功能）：33 项，约 0.6s。
    # 三条红线：导的是**全部**不是最近 200 条；没记录时**报错而不写空文件**；
    # 角色名带 `/ : * ?` 也不能炸。四刀变异见 _qc_chat_export_mutation。
    ("导出聊天记录", PY_VENV, ["tools/test_chat_export.py"], False),
    ("导出聊天记录变异", PY_VENV, ["tools/_qc_chat_export_mutation.py"], True),
    # 拨号引擎解析（2026-10-21 改写）：守 **2026-10-16 用户决策「语音电话恒走云端」**。
    # 它原来 13/20 红着挂了很久 —— 因为用例守着 10-09 那版「显式选本地就走本地」的契约，
    # 而那条路已被否决。按 test_quota_alert 的教训：这种测试最坏的下场不是红，而是
    # "谁把它修绿，谁就把用户停掉的行为加了回来"。现在是改写 + 三把刀。
    ("拨号引擎解析", PY_VENV, ["tools/test_call_engine_resolve.py"], False),
    ("拨号引擎变异", PY_VENV, ["tools/_qc_call_engine_mutation.py"], True),
    # 反向接线（2026-10-21 新增）：check_bridge_methods 守「前端调了 → 后端有」，
    # 反过来没人守 —— **后端写完了、界面上没有任何地方能触发它**（本仓叫「有实现、
    # 没出口」，scope=all / pet_hit_rect 都是这么漏的）。这道门守：后端公开方法必须
    # 有人调（调用点或字符串分发都算），存量 20 个列进 KNOWN_UNWIRED，新增即红；
    # 清单里的名字**一旦被接线也必须删掉**（否则清单会像「发现」栏一样腐烂）。
    # 三刀：加没人调的方法→③ / 改名→② / 给清单项加真实调用点→①。约 8s，标慢项。
    ("反向接线", PY_VENV, ["tools/check_api_wiring.py"], False),
    ("反向接线变异", PY_VENV, ["tools/_qc_api_wiring_mutation.py"], True),
    # 设置页「关于」分类（2026-10-21）：版本号 + 开机自启。这两个后端早就有、
    # 界面上一直没有入口（反向接线闸门抓到的第一批）。
    # 判据是"点了以后界面变成什么样"，所以**真值表交给 node 跑页面里的真 JS**：
    # JS 测试从 ui_redesign_settings.html 里原样抽 aboutTier 那个 IIFE 在假 DOM 里跑。
    # 三刀：去掉开发环境守卫 / 三态压二态 / 失败不翻回去。本机没 node → 77 跳过。
    ("关于页真值表", PY_RUNTIME, ["tools/test_about_tier.py"], False),
    ("关于页变异", PY_RUNTIME, ["tools/_qc_about_tier_mutation.py"], True),
    # 音色克隆「录音」按钮（2026-10-21 修的 bug）：它原来调 `record_audio_reference`
    # —— 那个方法录的是**系统输出**（游戏音效识别），事件类型白名单里没有 'reference'，
    # 于是按钮每一次都失败。设备方向相反的两个动作共用一个名字是根因，现在分开。
    # 判据：帧数/声道/格式对得上时长、时长收进 [1.5,30]、失败如实报、
    # **绝不调用 record_audio_reference**（bug 墓碑）。假 PyAudio，不碰真实设备。
    # 三刀：去掉时长上限 / 立体声不下混 / 失败当成功。约 1s。
    ("音色录音", PY_VENV, ["tools/test_voice_ref_record.py"], False),
    ("音色录音变异", PY_VENV, ["tools/_qc_voice_ref_mutation.py"], True),
    # 导入冒烟：逐个 import 每个模块（独立子进程，并发 8），抓「导入期就崩」。
    # compileall 只保证语法和名字对得上，保证不了能不能被加载——2026-10-17 实测两例
    # （import 期 mkdir('G:\\')、顶层 argtypes 引用已删的 _u32）compileall 全绿。
    # 172 个模块约 24s，故标慢项。白名单只收「确认不影响主程序启动」且**必须写原因**。
    # 变异三刀：非白名单坏模块→1；白名单内有原因→0（对照组，证明白名单真生效）；
    # 白名单内不写原因→1（2026-10-21 修，原实现无原因也放行）。
    ("导入冒烟", PY_VENV, ["tools/smoke_import.py", "--strict"], True),
    # 导入冒烟的「串行复核」逻辑（2026-10-21）：并发 import 时 OpenBLAS 分配不到内存会
    # 让 3 个模块假红（本机空闲 2.4G 时实测）。修法是只对**这一种错误**串行复核一次。
    # 这个修法本身有两个危险方向（都会让闸门更绿），所以必须有刀守着：
    # 复核范围放宽 → 真缺陷被重试掩盖；复核一律判过 → 真失败被洗出判决。
    # 用**独立假根**（临时目录的 src/）跑真工具，不碰真仓库模块。约 2s。
    ("导入冒烟复核", PY_VENV, ["tools/test_smoke_import_retry.py"], False),
    ("导入冒烟复核变异", PY_VENV, ["tools/_qc_smoke_retry_mutation.py"], True),
]


def _pick_python(preferred: str) -> str:
    if os.path.exists(preferred):
        return preferred
    return sys.executable


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for name, py, args, slow in GATES:
            print(f"{'[慢]' if slow else '    '} {name:14s} {' '.join(args)}")
        return 0

    fast = "--fast" in argv
    todo = [g for g in GATES if not (fast and g[3])]
    print(f"共 {len(todo)} 项闸门{'（--fast：已跳过慢项）' if fast else ''}\n")

    results: list[tuple[str, bool, float, str]] = []
    skips: list[tuple[str, str]] = []
    for name, py, args, _slow in todo:
        exe = _pick_python(py)
        if not os.path.exists(exe):
            results.append((name, False, 0.0, f"解释器不存在：{exe}"))
            print(f"  ✗ {name:14s}  解释器不存在")
            continue
        t0 = time.time()
        p = subprocess.run([exe] + args, cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
        cost = time.time() - t0
        # 退出码 77 = 跳过（环境不满足，且该能力当前不是产品承诺）。
        # **不能当通过**：跳过项会单独列出来，并说明"本轮未验证"。
        if p.returncode == 77:
            out = (p.stdout or "") + (p.stderr or "")
            reason = next((ln.strip()[len("[SKIP]"):].strip()
                           for ln in out.splitlines()
                           if ln.strip().startswith("[SKIP]")), "未说明原因")
            skips.append((name, reason))
            print(f"  ↷ {name:14s} {cost:5.1f}s  跳过：{reason}")
            continue
        ok = p.returncode == 0
        detail = ""
        if not ok:
            out = (p.stdout or "") + (p.stderr or "")
            lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
            # 【2026-10-21 修·摘错了行】原来一律取"最后 4 行"。对 `smoke_import` 这类
            # 「先打失败摘要、最后再打一张白名单明细表」的工具，最后 4 行恰好是
            # **已知可选项**那张表 → 闸门把"本来就允许失败的东西"当成失败原因报出来，
            # 真正闯祸的那一行被截掉。看到的人只会去查那 4 个没问题的模块，白费一轮。
            # 现在优先摘"带错误语义"的行，摘不到再退回末尾 4 行。
            _err_pat = re.compile(
                r"(非预期|FAIL|FAILED|ERROR|Error|Traceback|异常|失败|not found|No module)")
            hits = [ln for ln in lines if _err_pat.search(ln)]
            detail = " | ".join((hits or lines)[-4:])
            # 【2026-10-21 补·把全文落盘】只摘 4 行在"端到端类"闸门上仍然不够：
            # `pet_shell_selfheal_smoke` 失败时最后一行是它自己的汇总（"FAIL：1 项未通过"），
            # 真正失败的是哪一步、报了什么，全在中间被截掉 —— 想复查只能整轮重跑
            # （而它一轮 20 分钟）。所以失败项**完整输出**写到 logs/ 下（已 gitignore），
            # 并在汇总里给出路径。
            try:
                _dir = os.path.join(ROOT, "logs", "gate_failures")
                os.makedirs(_dir, exist_ok=True)
                _safe = re.sub(r"[^\w\u4e00-\u9fa5.-]+", "_", name)
                _fp = os.path.join(_dir, f"{_safe}.log")
                with open(_fp, "w", encoding="utf-8") as _f:
                    _f.write(f"# {name}\n# 退出码 {p.returncode}，耗时 {cost:.1f}s\n")
                    _f.write(f"# cmd: {exe} {' '.join(args)}\n\n")
                    _f.write(p.stdout or "")
                    if p.stderr:
                        _f.write("\n--- stderr ---\n")
                        _f.write(p.stderr)
                detail += f" ｜全文：{os.path.relpath(_fp, ROOT)}"
            except Exception as _e:  # noqa: BLE001  落盘失败不该掩盖失败本身
                detail += f" ｜全文落盘失败：{_e}"
        results.append((name, ok, cost, detail))
        print(f"  {'✓' if ok else '✗'} {name:14s} {cost:5.1f}s")

    bad = [r for r in results if not r[1]]
    print()
    if skips:
        print(f"跳过 {len(skips)} 项（**未验证**，不是通过）：")
        for name, reason in skips:
            print(f"  ↷ {name}：{reason}")
        print()
    if not bad:
        tail = f"，{len(skips)} 项跳过（未验证）" if skips else ""
        print(f"PASS：{len(results)} 项全部通过{tail}")
        return 0

    print(f"FAIL：{len(bad)}/{len(results)} 项未通过\n")
    for name, _ok, _cost, detail in bad:
        print(f"  【{name}】{detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
