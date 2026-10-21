"""一条命令跑完所有静态与冒烟闸门。

为什么需要它：闸门散在 tools/ 下十几个脚本里，靠人记一定会漏（本项目已经漏过）。
这里只做"编排 + 汇总"，不重写任何检查逻辑——检查的单一真值源仍是各自的脚本。

用法：
  python tools/check_all.py            # 全量（含导入冒烟与回归测试，约 1~2 分钟）
  python tools/check_all.py --fast     # 跳过慢项，改代码时的快速回环（约 5 秒）
  python tools/check_all.py --list     # 只看有哪些闸门

退出码：0=全部通过；1=有闸门失败（失败项会列出来）。
子脚本退出码 77 = 跳过：该能力当前不是产品承诺、本机环境不满足，
会单列为「跳过（未验证）」并打印原因，**不计入通过**。
目前只有 `壳自愈端到端` 用这个约定（壳是实验特性，见 src/ui/pet_backend.py）。
"""
from __future__ import annotations

import os
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
    ("前端 JS 语法", PY_RUNTIME, ["tools/check_web_js_syntax.py"], False),
    # HTML 标签配平：`index.html` 的通话浮层 `#callOverlay` 曾经**从来没被关上**
    # （call-screen 漏一个 </div>），靠浏览器在 </body> 处自动补，运行时毫无症状。
    # 判据用「容错匹配」：只报真漏标签，放过浏览器能确定性恢复的错位嵌套。
    ("前端 HTML 配平", PY_RUNTIME, ["tools/check_html_balance.py"], False),
    # 桥接三层：index.html 的 Backend.xxx → app_backend.js 的 call('py_name') →
    # CharacterAPI。任一层断掉都是"点了没反应"，控制台之外没有任何痕迹。
    ("前端桥接", PY_VENV, ["tools/check_bridge_methods.py"], False),
    ("凭据/隐私", PY_RUNTIME, ["tools/check_package_secrets.py"], False),
    # ${ENV} 引用与 dpapi: 密文都不算泄漏（本轮修的两处假警噪点）
    ("凭据扫描正反", PY_VENV, ["src/tests/test_package_secrets_scan.py"], False),
    ("凭据日志卫生", PY_RUNTIME, ["tools/check_log_leak.py"], False),
    ("依赖声明", PY_VENV, ["tools/check_requirements.py", "--strict"], True),
    ("导入冒烟", PY_VENV, ["tools/smoke_import.py", "--strict"], True),
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
    # 配置引用体检：路径指错 / 角色没绑引擎 → 红；可选功能（auto_start:false）
    # 与端口未就绪 → 只提示。扫进仓模板，CI 上也能跑。
    ("配置引用体检", PY_VENV, ["tools/_qc_path_refs.py", "config.release.yaml"], False),
    ("配置引用判级", PY_VENV, ["src/tests/test_config_path_refs.py"], False),
    # 模型渠道探活的判级（本轮事故的直接教训：百炼账户 400 欠费，配置里却"填好了"）。
    # 测试 ⑦ 组 + 变异 7 刀全红（tools/_qc_channel_mutation.py）。
    ("渠道探活判级", PY_VENV, ["src/tests/test_doctor_llm_channel.py"], False),
    # 配置里的 ${ENV} 引用：展开、缺失告警、保存不把明文 key 落盘。
    ("配置环境变量引用", PY_VENV, ["src/tests/test_config_env_refs.py"], False),
    ("打包依赖分级", PY_VENV, ["src/tests/test_make_dist_deps.py"], False),
    ("队列语义", PY_VENV, ["src/tests/test_queue_util.py"], True),
    ("通话冒烟", PY_VENV, ["src/ui/tests/test_call_smoke.py"], True),
    ("通道状态", PY_VENV, ["src/channels/tests/test_agent_status.py"], True),
    # 微信回调：无签名 POST 的鉴权边界 + AI 决策回发（曾因 `await 同步函数`
    # 导致回复一条都发不出去）。变异 5 刀全红，见 tools/_qc_gewechat_mutation.py。
    ("微信回调边界", PY_VENV, ["src/channels/tests/test_gewechat_callback.py"], False),
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
    # 导入冒烟：逐个 import 每个模块（独立子进程，并发 8），抓「导入期就崩」。
    # compileall 只保证语法和名字对得上，保证不了能不能被加载——2026-10-17 实测两例
    # （import 期 mkdir('G:\\')、顶层 argtypes 引用已删的 _u32）compileall 全绿。
    # 172 个模块约 24s，故标慢项。白名单只收「确认不影响主程序启动」且**必须写原因**。
    # 变异三刀：非白名单坏模块→1；白名单内有原因→0（对照组，证明白名单真生效）；
    # 白名单内不写原因→1（2026-10-21 修，原实现无原因也放行）。
    ("导入冒烟", PY_VENV, ["tools/smoke_import.py", "--strict"], True),
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
            # 只留最后几行，避免刷屏
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-4:]
            detail = " | ".join(tail)
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
