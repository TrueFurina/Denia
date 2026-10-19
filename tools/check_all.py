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
    # 「发给队友的包里到底有什么」：包内容取 git ls-files（.gitignore 是单一事实源），
    # 再叠一层硬排除。这层防御在真实仓库上**一项都不会触发**（gitignore 早洗过了），
    # 所以必须直接对规则表喂路径来证明它是活的——变异 4 刀（规则恒 None / 删红线
    # 文件名表 / 删 data 目录 / 少一项必需文件）全红。
    ("源码包排除规则", PY_VENV, ["src/tests/test_pack_for_teammate.py"], False),
    # 「出厂状态」的守门人：新用户第一眼看到的东西（模板首启开关 / 默认唤醒词 /
    # 应用图标 / 代码里的兜底角色名）。这几项在开发机上永远是对的，只有新用户
    # 才会撞上，所以不能靠"我这儿跑着没问题"。变异 3 刀（首启开关 / 唤醒词 /
    # 图标不进包清单）全红。
    ("出厂模板与图标", PY_VENV, ["src/tests/test_release_template.py"], False),
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
