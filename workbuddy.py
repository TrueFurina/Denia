# -*- coding: utf-8 -*-
"""Denia · 工作助手模式（无桌宠壳的交互入口）。

这是照 WorkBuddy 范式做的拆分：
  · 对话窗（本脚本）= 工具引擎，一等公民是「任务」，每次执行落 data/tasks.jsonl；
  · 桌宠（main.py 的 Live2D 形象）= 可选皮肤层，用 --pet 手动拉起，浮在对话窗上方。

与桌宠的区别
------------
- 默认不加载达妮娅人设（prompts/call_profiles/danya_persona.txt），改用
  prompts/workbuddy_persona.txt（中性、任务导向）；不加载陪伴行为规范。
- 不加载 companion 整包（Live2D / 双语拨号 / 关系记忆），启动轻、依赖少。
- 不会改动 config.yaml：persona 切换只在内存的 Config 对象上做。

用法
----
    python workbuddy.py                 # 纯工作助手交互
    python workbuddy.py --confirm ask   # 高危工具逐项询问
    python workbuddy.py --pet           # 同时浮起桌宠形象（独立进程，手动开的皮肤层）

退出：输入 exit / quit / 空行 + Ctrl-D，或 Ctrl-C。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time

# 控制台编码兜底：Windows 默认 GBK 码页没有 "✓"(U+2713) 这类符号，
# print 出去会抛 UnicodeEncodeError —— 状态行一打就把整个会话打断
# （main.py:66 记的就是同一个坑，crash_20261018_150448）。
# 必须在任何输出之前调用；本模块只依赖标准库，安全。
from src.io_safe import ensure_safe_stdio

ensure_safe_stdio()


def _repo_root() -> str:
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.exists(os.path.join(d, "config.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


def _load_config(persona_rel: str, model: str = "") -> object:
    """读 config.yaml 成一个 Config，并就地把 agent 的人设切到实用版。

    只改内存中的 ns，不写回磁盘，不影响桌宠运行。

    model 非空时覆盖 agent.llm.model（同样只在内存）——用来做模型故障转移：
    智谱不同模型限流池独立、可用额度也不同（实测 2026-11-03：
    glm-4.7-flash 报 1305 过载，glm-4-plus/air/4.5-x 报 1113 无资源包，
    只有 glm-4-flash 返 200），此时不改配置文件也能换档。
    """
    from src.config import Config
    import yaml  # type: ignore
    path = os.path.join(_repo_root(), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Config(raw, path)
    ag = getattr(cfg.ns, "agent", None)
    if ag is not None:
        ag.persona_file = persona_rel
        ag.behavior_file = ""  # 清空陪伴行为规范
        # 【2026-11-03】代理身份名：_BASE_PROMPT 原先硬编码"达妮娅"，会让工作助手
        # 自称"我是达妮娅的电脑操作代理"。这里切成中性身份（仅内存）。
        ag.agent_name = "助手"
        if model:
            llm = getattr(ag, "llm", None)
            if llm is None:
                raise ValueError("config.yaml 的 agent.llm 不存在，无法覆盖模型")
            llm.model = model
    return cfg


def _effective_model(cfg: object) -> str:
    """本次实际会用哪个模型（与 AgentBrain.from_config 的取值顺序同口径）。"""
    ag = getattr(cfg.ns, "agent", None) if cfg is not None else None
    llm = getattr(ag, "llm", None) if ag is not None else None
    if llm is not None and getattr(llm, "model", ""):
        return str(llm.model)
    cas = getattr(cfg.ns, "cascade", None) if cfg is not None else None
    return str(getattr(cas, "model", "") or "(未配置)")


def _spawn_pet() -> "subprocess.Popen | None":
    """把桌宠作为独立浮窗进程拉起（手动开的皮肤层）。返回进程句柄或 None。"""
    main_py = os.path.join(_repo_root(), "main.py")
    if not os.path.isfile(main_py):
        print("[提示] 未找到 main.py，无法拉起桌宠浮窗。", file=sys.stderr)
        return None
    try:
        # Windows 下 detached + CREATE_NO_WINDOW 让宠物窗浮在上方但不抢控制台
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) \
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([sys.executable, main_py], **kwargs)
        print("[宠物] 桌宠浮窗已拉起（手动皮肤层，独立进程）。关闭本助手不会自动关它。")
        return proc
    except Exception as e:  # noqa: BLE001
        print(f"[提示] 桌宠拉起失败：{e}", file=sys.stderr)
        return None


async def _chat_turn(instruction: str, cfg: object, confirm: str) -> None:
    from src.llm.agent_brain import run_agent_task

    async def _on_confirm(desc: str) -> bool:
        if confirm == "ask":
            try:
                ans = input(f"  确认执行？ {desc} [y/N] ").strip().lower()
            except Exception:  # noqa: BLE001
                return False
            return ans in ("y", "yes", "是")
        return True  # auto：用户在交互里下达，默认放行

    def _on_status(payload):
        if isinstance(payload, dict):
            txt = payload.get("text")
            if txt:
                phase = payload.get("phase", "")
                mark = "✓" if phase == "done" else "·"
                print(f"  {mark} {txt}", flush=True)

    t0 = time.time()
    # auto 模式**不挂确认钩**：语义上就是"不问、直接放行"。挂上钩会被
    # run_agent_task 记成 confirmed=1 → 账本 human_intervened=true，
    # 而其实没人参与 ⇒ 该指标永久虚高，失去意义（口径唯一）。
    result = await run_agent_task(
        instruction, cfg=cfg, on_status=_on_status,
        on_confirm=_on_confirm if confirm == "ask" else None,
    )
    dur = time.time() - t0
    print("\n" + "=" * 56)
    print("结果：")
    print((result or "（无返回）").strip())
    print("-" * 56)
    print(f"耗时 {dur:.1f}s · 记录已写入 data/tasks.jsonl")
    print("=" * 56 + "\n")


async def _loop(cfg: object, confirm: str, model: str) -> None:
    print("\n" + "╔" + "═" * 54 + "╗")
    print("  Denia · 工作助手模式（无桌宠壳）")
    print(f"  模型：{model}")
    # 权限档要显式打出来：用户得知道自己给出去多少权力（与"别谎报"同一套要求）
    try:
        from src.llm.permission import describe as _perm_desc
        _agent_ns = getattr(getattr(cfg, "ns", None), "agent", None)
        print("  权限：%s" % _perm_desc(getattr(_agent_ns, "permission_mode", "") or ""))
    except Exception:  # noqa: BLE001  横幅失败不影响主流程
        pass
    print("  输入一句任务回车执行；exit 退出；记录落 data/tasks.jsonl")
    print("╚" + "═" * 54 + "╝\n")
    while True:
        try:
            instruction = input("你> ").strip()
        except EOFError:
            print("\n[再见]")
            break
        except KeyboardInterrupt:
            print("\n[已中断本轮，输入 exit 退出]")
            continue
        if not instruction:
            continue
        if instruction.lower() in ("exit", "quit", "q"):
            print("[再见]")
            break
        try:
            await _chat_turn(instruction, cfg, confirm)
        except KeyboardInterrupt:
            print("\n[已中断本轮]")
        except Exception as e:  # noqa: BLE001
            print(f"[执行出错] {type(e).__name__}: {e}")


def _setup_logging() -> None:
    """把执行轨迹落盘到 logs/run_*.log（与 Denia 主程序同一约定）。

    没有它，``logger.info("AGENT 工具 %s -> %s", ...)`` 这类关键证据只往
    控制台打 —— 窗口一关就没了。而"工具到底返回了什么、界面到底变没变"
    正是判断真假成功的唯一依据，不能只留在屏幕上。
    """
    import logging
    import logging.handlers
    log_dir = os.path.join(_repo_root(), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return
    path = os.path.join(log_dir, f"run_{time.strftime('%Y%m%d_%H%M%S')}.log")
    handlers: list = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            path, mode="a", maxBytes=8 * 1024 * 1024, backupCount=1,
            encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S", handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="workbuddy", description="Denia 工作助手交互模式（无桌宠壳）")
    ap.add_argument("--persona", default="prompts/workbuddy_persona.txt",
                    help="persona 文件相对工程根（默认实用版）")
    ap.add_argument("--model", default="",
                    help="覆盖本次使用的模型（仅内存，不改 config.yaml）；"
                         "用于限流/额度故障转移，如 --model glm-4-flash")
    ap.add_argument("--confirm", choices=["auto", "ask"], default="auto",
                    help="高危操作确认方式：auto=默认放行 / ask=逐项询问")
    ap.add_argument("--permission", default="",
                    choices=["", "explore", "default", "accept_edits", "dont_ask", "bypass"],
                    help="五档权限模式（仅内存，不改 config.yaml）："
                         "explore=只读 / default=写与执行都问 / accept_edits=写放行执行问 / "
                         "dont_ask=无人值守（执行一律拒）/ bypass=全放行。"
                         "留空=沿用配置里的 agent.permission_mode")
    ap.add_argument("--pet", action="store_true",
                    help="同时拉起桌宠浮窗（独立进程，手动开的皮肤层）")
    args = ap.parse_args(argv)

    try:
        cfg = _load_config(args.persona, args.model)
    except Exception as e:  # noqa: BLE001
        print(f"[配置加载失败] {e}", file=sys.stderr)
        return 2

    model = _effective_model(cfg)
    # 【2026-11-04】--permission 只改内存（与 --model 同一套纪律），不动 config.yaml。
    if args.permission:
        try:
            cfg.raw.setdefault("agent", {})["permission_mode"] = args.permission
            from src.config import _to_ns
            cfg.ns = _to_ns(cfg.raw)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] 权限模式未能应用（沿用配置值）：{e}", file=sys.stderr)
    _setup_logging()
    pet_proc = _spawn_pet() if args.pet else None
    try:
        asyncio.run(_loop(cfg, args.confirm, model))
    finally:
        if pet_proc is not None:
            try:
                pet_proc.terminate()
                print("[宠物] 已关闭桌宠浮窗。")
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
