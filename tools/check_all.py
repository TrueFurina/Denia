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
    ("字节码编译", PY_RUNTIME, ["-m", "compileall", "-q", "src", "main.py"], False),
    ("全局名完整性", PY_RUNTIME, ["tools/check_module_globals.py", "--all", "src"], False),
    ("文本编码", PY_RUNTIME, ["tools/check_open_encoding.py", "--all", "src", "main.py"], False),
    ("前端资源", PY_RUNTIME, ["tools/check_web_assets.py", "--strict"], False),
    ("凭据/隐私", PY_RUNTIME, ["tools/check_package_secrets.py"], False),
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
    ("doctor 自检结构", PY_VENV, ["src/tests/test_doctor.py"], False),
    ("打包依赖分级", PY_VENV, ["src/tests/test_make_dist_deps.py"], False),
    ("队列语义", PY_VENV, ["src/tests/test_queue_util.py"], True),
    ("通话冒烟", PY_VENV, ["src/ui/tests/test_call_smoke.py"], True),
    ("通道状态", PY_VENV, ["src/channels/tests/test_agent_status.py"], True),
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
