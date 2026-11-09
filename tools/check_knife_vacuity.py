# -*- coding: utf-8 -*-
"""变异刀空转体检：把闸门换成退化实现，看刀套件认不认得出来。

为什么需要
----------
「变异刀全绿」这句话，本会话在每个 PR 里都写过。但它只证明了一件事：
**闸门正常工作时刀会红。** 它没证明：

  · 闸门**永远不红**时刀会不会红 —— 只断言"必须变绿"的套件此时全过
  · 闸门**永远红**时刀会不会红 —— 只断言"必须变红"的套件此时全过

两者缺一个，那个套件就是**空转**的：判据被改松成「一律不报」或改紧成「一律报」，
它都照样通过。它守不住自己声称守住的东西。

做法
----
对每一对 (刀套件, 它验的闸门)，在**仓库副本**里跑三个阶段：

  D0 原样            → 套件必须 exit 0（否则它本来就红，后面的观察没意义）
  D1 闸门 ≡ exit 0   → 套件必须 exit != 0
  D2 闸门 ≡ exit 1   → 套件必须 exit != 0

三个阶段都过，才叫 CERTIFIED。

两个**必须**踩过的坑（都是本工具第一版真踩的，记在这里免得下一个人再踩）
--------------------------------------------------------------------
① **退化桩必须 import-safe。** 第一版写成顶层 `sys.exit(0)`，于是
   `_qc_bridge_guards_mutation.py` 在 `import check_bridge_guards` 时就被
   `SystemExit(0)` 带走 —— 整个套件**一行刀都没跑**，却以 exit 0 收场，
   被本工具判成"空转"。**那是桩的错，不是套件的错。**
   现在桩一律包 `if __name__ == "__main__":`。
   → 一般化：**测别人之前先测自己的仪器**，否则"发现"可能只是仪器坏了。

② **进程内直调闸门的套件，换文件对它无效。** 它 `import check_x as c` 之后用
   `c.scan_files()`；换掉文件只会让它 `AttributeError`。这类套件不是"空转"，
   是**本工具不适用**——所以单列 `SKIP`，不算失败、也不算通过。

分类
----
  CERTIFIED  三阶段全过，且退化是套件**自己报的**失败
  VACUOUS    某个退化方向上套件仍然 exit 0 —— **这就是缺陷**
  WEAK       抓住了，但是靠崩（`Traceback`）而不是靠报失败
             算失败会让工具变噪声源，所以只告警。**已量到**：
             `_qc_broadcast_types_mutation.py` 在闸门零输出时 `IndexError`。
  SKIP       套件进程内 import 了闸门，本工具不覆盖
  ABSENT     本树根本没有这一对（`PAIRS` 是声明式的，可以比本树超前）
             不计失败、也不计通过。它不能用来掩盖删除——`check_gate_registry.py`
             会拦住"登记在 GATES 里的脚本不存在"。
  ERROR      副本建不出来 / 基线本来就红（有但坏了）

已知边界（都是**故意**留的）
----------------------------
① 只覆盖「通过子进程调用判定文件」的套件。
② 全仓 78 个 `_qc_*` 的着落，现在是**可数的**，不再靠"我记得验过哪些"：

   | 类别 | 数量 | 本工具做什么 |
   |---|---|---|
   | 验 `check_*.py` 闸门 | 16 | 全部在 `PAIRS` |
   | 验 `test_*.py` 特性判定 | 13 | 10 在 `PAIRS`，3 在 `EXCUSED`（写清为什么现在验不了） |
   | 改源码后进程内断言 | 48 | 没有独立判定文件，本工具不适用 |
   | 元刀（本工具自己的刀） | 1 | 不适用 |

   48 个里有没有空转风险？有，而且是同一族——但它们的判据写在刀自己身上
   （`if not caught: bad += 1` 后面没有第二个判据），换文件这个手法用不上。
   要覆盖它们需要另一种做法，目前没做，**不要读成"验过了"**。
③ `PAIRS` 里还有 1 个是进程内直调（`_qc_site_counts_mutation`），只能 `SKIP`；
   `SKIP` 不算通过，所以是 25 而不是 26。
④ **分两层跑**（`--tier`）：`portable` 进 CI，`dev` 需要 `.venv`。
   分层不是"重要性"分层，是"这台机器能不能跑"分层——dev 层的刀把解释器
   硬编码成 `.venv/Scripts/python.exe`，CI 上没有 venv，混进去会让整道门恒红。
⑤ 每个套件要跑 3 次 + 复制一次仓库副本（无 venv 约 1s，有 venv 约 2s）。
   慢是刻意的：省掉就是省掉证据。
⑥ **完整性检查只在本仓默认根上做**（`--pair` / `--only` / `--root` 时跳过）：
   它要回答的是"这个仓里的刀有没有被漏掉"，而 `--root` 指向的桩仓本来就没有这些刀。

用法
----
    python tools/check_knife_vacuity.py                 # portable 层（CI 跑这个）
    python tools/check_knife_vacuity.py --tier all      # 两层都跑（需要 .venv）
    python tools/check_knife_vacuity.py --only cross    # 名字含 cross 的
    python tools/check_knife_vacuity.py --pair _qc_x_mutation.py=check_x.py
    python tools/check_knife_vacuity.py --root <另一个仓根>

退出码：0 无 VACUOUS / 1 有 VACUOUS / 2 无法判定（一对都没跑成，或有刀没着落）
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _qc_repo_copy import copy_repo  # noqa: E402

# (刀套件, 它验的判定文件, 层, 为什么它必须在名单里, [副本要不要 git 仓])
#
# 层的含义：
#   portable  纯 stdlib、不需要 .venv / 音频设备 / 桌面会话 —— CI 跑这一层
#   dev       刀自己把解释器硬编码成 `.venv/Scripts/python.exe` —— 只有装了 venv
#             的机器能跑。**不是"不重要"，是"这台机器跑不了"**，所以不能混进 CI，
#             否则 CI 上全 ERROR、整道门恒红。
#
# 第 5 位（可选，默认 False）：这一对的判定要用 `git ls-files`，副本里得有个仓。
# 副本里是**重新 init** 一个仓，不是复制原仓的 .git —— 见 tools/_qc_repo_copy.py。
PAIRS = [
    # ---- portable ----
    ("_qc_unreachable_mutation.py", "check_unreachable.py", "portable",
     "第十五/十六轮：M2/M3 是限位刀，必须被 D2 抓住"),
    ("_qc_duplicate_def_mutation.py", "check_duplicate_def.py", "portable",
     "第十七轮：M3/M4 钉住「两边都没装饰器才报」这个限定"),
    ("_qc_finally_jump_mutation.py", "check_finally_jump.py", "portable",
     "第十八轮：八刀里 M4/M5 是限位刀"),
    ("_qc_constant_cond_mutation.py", "check_constant_cond.py", "portable",
     "第十九轮：十三刀里 M8/M9/M10 是限位刀"),
    ("_qc_gate_exit_mutation.py", "check_gate_exit.py", "portable",
     "第二十轮：十五刀里 K4–K10 是限位刀"),
    ("_qc_cross_ref_mutation.py", "check_cross_ref.py", "portable",
     "第二十一轮：十二刀里 K4–K10 是限位刀"),
    ("_qc_web_js_syntax_mutation.py", "check_web_js_syntax.py", "portable",
     "第二十二轮修好 import 路径之后才第一次能跑"),
    # ---- dev（第二十三轮补：这九对的 `TEST` 也指向 check_*.py，是同一族）----
    ("_qc_loop_guard_mutation.py", "check_loop_guard.py", "dev",
     "长任务护栏：刀的 TEST 直接是闸门"),
    ("_qc_loop_guard_e2e_mutation.py", "check_loop_guard_e2e.py", "dev",
     "长任务护栏端到端"),
    ("_qc_partial_reply_mutation.py", "check_partial_reply.py", "dev",
     "半截回复检测"),
    ("_qc_release_numbers_mutation.py", "check_release_numbers.py", "dev",
     "发布口径对账 —— 本轮查出它**没有基线检查**"),
    ("_qc_runtime_audio_mutation.py", "check_runtime_audio_guard.py", "dev",
     "运行时音频守卫 —— 本轮查出同类"),
    ("_qc_site_counts_mutation.py", "check_site_counts.py", "dev",
     "站点计数对账"),
    ("_qc_tool_permission_mutation.py", "check_tool_permission_coverage.py", "dev",
     "工具权限覆盖 —— 本轮查出同类"),
    ("_qc_ui_reload_boundary_mutation.py", "check_ui_reload_boundary.py", "dev",
     "UI 重载边界 —— 本轮查出同类"),
    ("_qc_verify_deps_order_mutation.py", "check_verify_deps_order.py", "dev",
     "出包验收时序 —— 本轮查出同类"),
    # ---- portable（第二十四轮补：特性族的刀，判定文件是 test_*.py）----
    ("_qc_gate_lock_mutation.py", "tools/test_gate_lock.py", "portable",
     "闸门锁：刀的 PY 是 sys.executable，CI 上也能跑"),
    # ---- dev（第二十四轮补：特性族，判定要 .venv）----
    # 这一族以前**一次都没被验过**：本工具一律把判定拼成 `tools/<名字>`，而它们的
    # 判定在 `src/tests/test_*.py` 或 `tools/test_*.py`（带前缀）。13 对全部
    # ABSENT —— 而 ABSENT 是"不计失败也不计通过"，所以 13 个刀的空转风险
    # 一直是零覆盖。见 `_resolve()` 与 ABSENT/ERROR 的拆分。
    ("_qc_asr_mutation.py", "src/tests/test_asr_voice_pipeline.py", "dev",
     "ASR 语言码 —— 七刀，两个判定文件"),
    ("_qc_config_bootstrap_mutation.py", "tools/test_config_bootstrap.py", "dev",
     "首启兜底"),
    ("_qc_embed_fallback_mutation.py", "tools/test_embed_fallback.py", "dev",
     "内嵌运行时回退"),
    ("_qc_pathrefs_mutation.py", "src/tests/test_config_path_refs.py", "dev",
     "配置路径引用 —— 本轮查出 H 刀的锚点缩进差 4 格"),
    ("_qc_permission_modes_mutation.py", "tools/test_permission_modes.py", "dev",
     "权限模式"),
    ("_qc_run_command_mutation.py", "tools/test_run_command_guard.py", "dev",
     "run_command 守卫"),
    ("_qc_ui_heartbeat_mutation.py", "tools/test_ui_heartbeat.py", "dev",
     "UI 心跳"),
    ("_qc_voice_mutation.py", "src/tests/test_doctor_voice_backend.py", "dev",
     "语音后端自检"),
    ("_qc_workspace_mutation.py", "tools/test_workspace_guard.py", "dev",
     "工作区守卫"),
    # 【2026-11-09 加】壳层两道静态门。判定文件就是它们的自测；这两把刀本轮
    # 才第一次存在（此前自测只测 scan()，退出码零判据，A 刀写不出来）。
    ("_qc_await_sync_mutation.py", "src/tests/test_check_await_sync.py", "dev",
     "协程调度：五刀，A 刀砍退出码（自测本轮才补上这条判据）"),
    ("_qc_async_blocking_mutation.py", "src/tests/test_check_async_blocking.py", "dev",
     "异步阻塞：五刀，B 刀砍 to_thread 实参豁免（本轮删掉的那个真缺陷）"),
    # 【2026-11-09 加】WS 端口绑定：八刀。这道门以前**一次都没被变异验过** ——
    # `check_all.py` 的注释写着「两刀变异已验」，仓库里却没有任何 `_qc_*ws*`。
    # 本轮读判据时读到两处空转（删重试日志、serve 提出循环），补完判据才有这八刀。
    ("_qc_ws_port_bind_mutation.py", "src/tests/test_ws_port_bind.py", "dev",
     "WS 端口绑定：八刀，G 刀「只 bind 一次+空睡 5 次」在补判据之前是全绿的"),
    # 【2026-11-10 加】RPC 通道：十刀。同族的第三个洞 —— 这道门守的通道
    # 「坏了照样返回 200，只是返回的东西不对」，而 19 条等价性用例全是单段名字。
    ("_qc_rpc_mutation.py", "src/tests/test_rpc_server.py", "dev",
     "RPC 通道：十刀，B 刀「逐段检查→整串检查」在补判据之前是全绿的（点号绕过）"),
    # 【2026-11-10 加】壳自愈与退出清理：八刀。15 节、四十来条断言的「最扎实」
    # 那道门，进程回收与重启恢复状态两条支路上五处关键行为全都没判据。
    ("_qc_pet_shell_mutation.py", "src/tests/test_pet_shell_selfheal.py", "dev",
     "壳自愈：八刀，B 刀「不杀子进程树」= 孤儿 Electron 窗口那个坑，此前零判据"),
    # 【2026-11-10 加】双工停止与麦克风：十刀。A/B/C 三段测的是手抄的生产代码副本，
    # 换真实实现后 A 刀（回退成 `await client.stop()`）只在 A5 的日志判据上红 ——
    # 原 A1 那句「stopped 恒 False」是假的。
    ("_qc_duplex_stop_mic_mutation.py", "tools/test_duplex_stop_and_mic.py", "dev",
     "双工停止与麦克风：十刀，A 刀只有 A5 能抓（A1/A2 对回归本来就绿）"),
    # 【2026-11-10 加】打包依赖分级：十二刀。最厚的一道门（1047 行）三处空转，
    # 最狠的是「装完不验收」—— `if verify: return verify_deps(...)` 整段没有判据。
    ("_qc_make_dist_mutation.py", "src/tests/test_make_dist_deps.py", "dev",
     "打包依赖分级：十二刀，C 刀「装完不验收」此前零判据（[14] 只测了 verify_deps 本身）"),
    # ---- dev（第二十六轮补：两条从 EXCUSED 挪进来的）----
    ("_qc_presence_mutation.py", "test_presence_controls.py", "dev",
     "在场感增强：缺 pynput 时兜底校验放行拼错的键名 —— 修好后起点第一次是绿的"),
    ("_qc_tray_switch_mutation.py", "test_tray_switch.py", "dev",
     "托盘切换：判定不再依赖 gitignore 里的 config.yaml"),
    # 【2026-11-08 从 EXCUSED 挪进来的最后一条】判定要 `git ls-files`，副本里
    # 得有个仓。原来记的理由是"要验它得让副本带上 .git，代价是每对多拷几十 MB"——
    # 代价估对了，结论错了：副本里 `git init` + `add -A` + commit 只要几十毫秒。
    ("_qc_bundled_assets_mutation.py", "test_bundled_assets.py", "dev",
     "随包资产：副本里重新 init 一个仓（不是复制 .git），判定才有文件清单可判", True),
]

# 声明了判定文件、但**本工具无法判定**的刀。每条都必须写清"为什么"，空理由等于没写。
#
# 这一张表不是垃圾桶，是**待办清单**：每一行都说明了一个"这道刀现在守不住自己
# 声称守住的东西"的具体原因。想加进来，先回答"什么条件下它就能验了"。
EXCUSED = {
    # 【2026-11-08 清空】最后一条 `_qc_bundled_assets_mutation.py` 已经能验了。
    # 它原来在这里的理由是"判定要 `git ls-files`，而副本把 .git 排除了"——
    # 那不是刀的缺陷，是**副本造得不像仓库**。修法见 tools/_qc_repo_copy.py：
    # 副本里重新 init 一个仓（几十毫秒），不复制原仓的 .git。
    # **空表是有意义的读数**：它说的是"没有已知验不了的"，不是"全都验过了"。
    # 【2026-11-08 删掉两条过时的】`_qc_tray_switch_mutation` 与 `_qc_presence_mutation`
    # 原来在这里，理由是"判定恒红/缺文件 → 无法判定"。第二十六轮查出**两条理由都不是
    # 产品缺陷**，而是判定自己依赖了不该依赖的东西：
    #   · tray：判定要 `config.yaml`，而它在 .gitignore 里 → 新克隆必崩。
    #     改成缺文件时用出厂模板 `config.release.yaml` 当夹具。
    #   · presence：判定要 `pynput` 的 parser 来拒掉拼错的键名，而本机没装 pynput
    #     （`requirements.txt:53` 声明了）→ 兜底只做形状校验 → `<nosuchkey>` 被放行。
    #     **原来记的原因是错的**（写成"全局热键在无桌面会话时返回空表"，跟 pynput 无关）。
    #     修法是让兜底自己认识键名（`_fallback_ok` + 白名单），于是不再依赖装没装依赖。
    # 两条都挪进 `PAIRS` 了 —— 能判的就不该留在"无法判定"里。
}

# 【坑①】桩必须 import-safe：顶层 sys.exit 会在 import 时把套件带走，exit 0 假绿。
STUB_ZERO = 'if __name__ == "__main__":\n    raise SystemExit(0)\n'
STUB_ONE = 'if __name__ == "__main__":\n    raise SystemExit(1)\n'

# 套件自己报失败时用的标记；`Traceback` 不算（崩溃 ≠ 识别）
# 套件自己报失败时用的说法。这张表是**实测补出来的**，不是想当然：
# `_qc_release_numbers_mutation` 等五把刀说的是「没抓住（闸门失效）」，
# 而第一版只有「没被抓住」，于是它们被判成 WEAK —— 又一次"仪器太窄"。
FAIL_MARKS = ("✗", "FAIL", "不符合", "没红", "HAS FAIL", "没被抓住", "没抓住",
              "期望 exit", "未抓住")
# 【2026-11-08】`.venv` **不能**排除：dev 层的刀把解释器硬编码成
# `.venv/Scripts/python.exe`，副本里没有它就跑不起来（会全判 ERROR）。
# 代价是副本大一倍（有 venv 的机器约 60MB）；CI 上没有 venv，不受影响。
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "dist", "build"}


def _resolve(root: str, rel: str) -> str:
    """把 PAIRS 里的名字解析成真实路径。

    【2026-11-08 修·判定文件不一定在 tools/】第一版一律拼 `tools/<名字>`，
    于是特性族的刀（判定在 `src/tests/test_*.py` 或 `tools/test_*.py`）全部解析不到。
    约定：**带 `/` 的一律当仓根相对路径**，不带 `/` 的才按 `tools/` 下的短名解析。
    这样 `"check_x.py"` 和 `"src/tests/test_x.py"` 两种写法都能用，PAIRS 不必写死前缀。
    """
    if "/" in rel or "\\" in rel:
        return os.path.join(root, rel.replace("/", os.sep))
    return os.path.join(root, "tools", rel)


def imports_gate(suite_path: str, gate: str) -> bool:
    """套件是否在进程内 import 了闸门模块（换文件对它无效）。"""
    base = os.path.basename(gate.replace("\\", "/"))
    mod = base[:-3] if base.endswith(".py") else base
    try:
        t = ast.parse(io.open(suite_path, encoding="utf-8", errors="replace").read(),
                      filename=suite_path)
    except (OSError, SyntaxError):
        return False
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == mod:
                    return True
        elif isinstance(n, ast.ImportFrom):
            if n.module == mod or (n.module or "").endswith("." + mod):
                return True
    return False


def _declared_judgment(path: str) -> str | None:
    """刀自己声明的判定文件（模块级 `TEST = "..."`）。没有就返回 None。

    只认**模块级**赋值：嵌在函数里的 `TEST` 不算，那多半是别的用途。
    """
    try:
        t = ast.parse(io.open(path, encoding="utf-8", errors="replace").read(), filename=path)
    except (OSError, SyntaxError):
        return None
    for n in t.body:
        if isinstance(n, ast.Assign) and any(getattr(x, "id", "") == "TEST" for x in n.targets):
            if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                return n.value.value
    return None


def _copy_repo(root: str, need_git: bool = False) -> str:
    """复制一份仓给套件跑。need_git=True 时副本里**重新 init 一个仓**。

    【2026-11-08 修·副本造得不像仓库】`_qc_bundled_assets_mutation` 的判定
    `check_bundled_assets.py` 用 `git ls-files` 判「在盘上但没进 git」。副本原来
    把 `.git` 排除掉，判定就恒红（10 FAIL），于是这把刀被挂进 `EXCUSED`，
    理由写的是"要验它得让副本带上 .git，代价是每对多拷几十 MB"。

    代价估得对，结论错了：**不需要复制 .git**。在副本里 `git init` + `git add -A`
    + commit 只要几十毫秒，而且 `git ls-files` 返回的正是"这份副本该有的文件"，
    还天然遵守 `.gitignore`（`config.yaml`、`logs/` 不进清单）—— 比复制原仓
    更接近新克隆的样子。共用实现在 `tools/_qc_repo_copy.py`。
    """
    if need_git:
        return copy_repo(root, need_git=True)
    d = tempfile.mkdtemp(prefix="qc_vacuity_")
    dst = os.path.join(d, "repo")
    shutil.copytree(root, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    return dst


def _run(suite_path: str, cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run([sys.executable, suite_path], cwd=cwd,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _is_suite_crash(out: str, suite: str) -> bool:
    """崩溃的是**套件自己**吗？

    【坑③】第一版写成「输出里有 Traceback 就算崩」，是错的：套件会把**闸门**的
    stderr 一起收进自己的输出（`(p.stdout or "") + (p.stderr or "")`），于是闸门在
    退化状态下抛的 Traceback 被误判成"套件崩了"。实测 `_qc_silent_mutation.py`
    明明规规矩矩印了「FAIL：5 刀未抓住」，却被判成 WEAK。
    正确判据：traceback 的帧里必须出现**套件自己的文件名**。
    """
    for block in out.split("Traceback (most recent call last):")[1:]:
        if suite in block.split("\n\n")[0][:4000]:
            return True
    return False


def _degenerate(gate_path: str, pristine: bytes, stub: str, suite_path: str,
                repo: str, suite_name: str) -> tuple[str, str]:
    """返回 (分类, 说明)。分类 ∈ {"ok", "vacuous", "weak"}。"""
    try:
        with io.open(gate_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(stub)
        code, out = _run(suite_path, repo)
    finally:
        with io.open(gate_path, "wb") as f:
            f.write(pristine)

    if code == 0:
        return "vacuous", "套件仍然 exit 0 —— 判据这样退化它发现不了"
    if _is_suite_crash(out, suite_name):
        last = [l for l in out.strip().splitlines() if l.strip()][-1][:140]
        return "weak", "抓住了但靠崩溃（%s）" % last
    if not any(m in out for m in FAIL_MARKS):
        return "weak", "exit %d 但输出里没有失败标记，说不清是识别还是别的原因" % code
    return "ok", "exit %d 且自行报失败" % code


def certify(root: str, suite: str, gate: str, need_git: bool = False) -> tuple[str, list[str]]:
    """返回 (分类, 逐阶段说明)。分类 ∈ CERTIFIED/VACUOUS/WEAK/SKIP/ABSENT/ERROR。"""
    log: list[str] = []
    src_suite = _resolve(root, suite)
    src_gate = _resolve(root, gate)
    # 【ABSENT 不是 ERROR】`PAIRS` 是**声明式**的：它可以比本树超前（比如某一对
    # 的闸门还在另一个未合的 PR 里）。这种"本树根本没有这一对"和"有但坏了"必须
    # 分开——混在一起，任何一个只 checkout 了部分目录的人都会看到假红。
    # 它不能用来掩盖删除：`check_gate_registry.py` 会拦住"登记在 GATES 里的脚本
    # 不存在"，而 PAIRS 里的套件都在 GATES 里。
    #
    # 【2026-11-08 修·ABSENT 吞掉了拼错路径】原来是
    #     if not 套件存在 or not 闸门存在: return ABSENT
    # 于是一个**拼错的判定文件路径**会被判成"本树没有这一对，不计失败也不计通过"。
    # 实测：把 13 个特性族的对按 `src/tests/test_x.py` 写进 PAIRS，13 个全部 ABSENT，
    # 门禁输出 `结果：ABSENT 13`、**exit 0** —— 一个都没验，却什么都没说。
    # 正确口径：**套件在、判定不在 = 声明错了**（改名 / 拼错 / 路径规则不对），
    # 这是 ERROR；只有"套件本身都不在"才是 ABSENT。
    suite_ok = os.path.isfile(src_suite)
    gate_ok = os.path.isfile(src_gate)
    if not suite_ok:
        return "ABSENT", ["本树没有套件 %s —— 不计失败，也不计通过" % suite]
    if not gate_ok:
        return "ERROR", ["套件在、判定文件不在（%s → %s）—— PAIRS 声明错了，无法判定"
                         % (gate, src_gate)]
    if imports_gate(src_suite, gate):
        return "SKIP", ["套件进程内 import 了 %s —— 换文件对它无效，本工具不覆盖" % gate]

    repo = _copy_repo(root, need_git=need_git)
    try:
        gate_path = _resolve(repo, gate)
        suite_path = _resolve(repo, suite)
        if not os.path.isfile(gate_path):
            return "ERROR", ["副本里找不到闸门 %s" % gate]
        pristine = io.open(gate_path, "rb").read()

        code, out = _run(suite_path, repo)
        if code != 0:
            tail = [l for l in out.strip().splitlines() if l.strip()][-1][:140]
            return "ERROR", ["D0 基线本来就红（exit %d：%s）—— 无法判定" % (code, tail)]
        log.append("✓ D0 原样：exit 0")

        k1, w1 = _degenerate(gate_path, pristine, STUB_ZERO, suite_path, repo, suite)
        k2, w2 = _degenerate(gate_path, pristine, STUB_ONE, suite_path, repo, suite)
        log.append(("✓" if k1 == "ok" else "✗") + " D1 闸门≡0：" + w1)
        log.append(("✓" if k2 == "ok" else "✗") + " D2 闸门≡1：" + w2)

        if "vacuous" in (k1, k2):
            return "VACUOUS", log
        if "weak" in (k1, k2):
            return "WEAK", log
        return "CERTIFIED", log
    finally:
        shutil.rmtree(os.path.dirname(repo), ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="变异刀空转体检")
    ap.add_argument("--root", default=ROOT, help="仓根（默认本仓）")
    ap.add_argument("--tier", default="portable", choices=["portable", "dev", "all"],
                    help="跑哪一层（默认 portable；CI 只跑这一层）")
    ap.add_argument("--only", default="", help="只跑套件名包含该子串的对")
    ap.add_argument("--pair", action="append", default=[],
                    help="显式指定 suite.py=gate.py，可重复；给了就不看 PAIRS")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    if not os.path.isdir(os.path.join(root, "tools")):
        print("✗ 根目录下没有 tools/：%s，无法判定" % root, file=sys.stderr)
        return 2

    if args.pair:
        pairs = []
        for spec in args.pair:
            if "=" not in spec:
                print("✗ --pair 需要 suite.py=gate.py：%s" % spec, file=sys.stderr)
                return 2
            s, g = spec.split("=", 1)
            pairs.append((s.strip(), g.strip(), "portable", "命令行指定"))
    else:
        want = {"portable"} if args.tier == "portable" else \
               ({"dev"} if args.tier == "dev" else {"portable", "dev"})
        pairs = [p for p in PAIRS if p[2] in want and args.only in p[0]]

    if not pairs:
        print("✗ 一对都没跑（--only 过滤掉了全部？）无法判定", file=sys.stderr)
        return 2

    print("仓根：%s" % root)
    print("待检 %d 对\n" % len(pairs))

    # 【2026-11-08 加·完整性】「哪些刀被验过」不能只靠一张手写的名单。
    # 只要有人新写一把带 `TEST` 的刀、忘了加进 PAIRS，它就自动变成零覆盖，
    # 而**结果行看起来和全验过一模一样**（这也是第二十三轮那个"层被悄悄漏掉"的同族）。
    # 口径：模块级声明了 `TEST` 的刀，必须在 PAIRS 里，或者在 EXCUSED 里写清理由；
    # 没声明 `TEST` 的（改源码后自己在进程内断言的 48 把）本工具不适用，不计。
    # 只在本仓默认根上做：变异刀用 `--root <tempdir>` 建桩仓，那里不该被这条约束。
    # （实测：K1–K9 的桩套件都不声明 `TEST`，所以对桩仓跑这条也不会误报；K12 就是
    #   在桩仓里造一把"声明了 TEST 却没登记"的刀来钉这条判据。）
    if not args.pair and not args.only:
        audited = {p[0] for p in PAIRS}
        missing = []
        declared = 0
        for f in sorted(os.listdir(os.path.join(root, "tools"))):
            if not (f.startswith("_qc_") and f.endswith(".py")):
                continue
            if _declared_judgment(os.path.join(root, "tools", f)) is None:
                continue          # 无独立判定文件，本工具不适用（见 docstring 边界③）
            declared += 1
            if f in audited or f in EXCUSED:
                continue
            missing.append(f)
        if missing:
            print("✗ 这些刀声明了判定文件，却既不在 PAIRS 里、也没写不进的理由：", file=sys.stderr)
            for f in missing:
                print("   " + f, file=sys.stderr)
            print("   （加进 PAIRS，或在 EXCUSED 里写清「为什么现在验不了、什么条件下能验」）",
                  file=sys.stderr)
            return 2
        print("完整性：模块级声明了判定文件的刀 %d 把，全部有着落（PAIRS 共 %d 对；"
              "EXCUSED %d 条）\n" % (declared, len(audited), len(EXCUSED)))


    counts: dict[str, int] = {}
    for pair in pairs:
        suite, gate, _tier, why = pair[:4]
        # 第 5 位是可选的"副本要不要 git 仓"。用 len 判断而不是 try/except：
        # PAIRS 是声明式的，元组长度不齐时应该安静地按默认走，不该炸。
        need_git = bool(pair[4]) if len(pair) > 4 else False
        print("== %s → %s" % (suite, gate))
        print("   （%s）" % why)
        kind, log = certify(root, suite, gate, need_git=need_git)
        for line in log:
            print("   " + line)
        print("   → %s\n" % kind)
        counts[kind] = counts.get(kind, 0) + 1

    order = ["CERTIFIED", "VACUOUS", "WEAK", "SKIP", "ABSENT", "ERROR"]
    print("结果：" + " / ".join("%s %d" % (k, counts[k]) for k in order if k in counts))

    ran = sum(counts.get(k, 0) for k in ("CERTIFIED", "VACUOUS", "WEAK"))
    if ran == 0:
        print("✗ 一对都没跑成，无法判定（全部 SKIP/ERROR 不算通过）", file=sys.stderr)
        return 2
    if counts.get("VACUOUS"):
        print("\n✗ 有空转的刀套件：它的判据被改松或改紧，它都发现不了。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
