# -*- coding: utf-8 -*-
"""无壳任务入口：把 Denia 当成「工作助手」用，不经桌宠壳。

用法
----
    python -m src.system.task_cli "帮我把桌面上的 report.docx 重命名为 周报.docx"
    python -m src.system.task_cli --confirm ask "删除 Downloads 里大于 1GB 的临时文件"

与桌宠的区别
------------
- 不加载达妮娅人设（prompts/call_profiles/danya_persona.txt），改用
  prompts/workbuddy_persona.txt（中性、任务导向）。
- 不加载陪伴行为规范（cascade.behavior_file）。
- 每次执行都会落一条 data/tasks.jsonl 记录（见 src/system/tasks.py），
  这是「工具」相对「桌宠」的结构性差异：一等公民是任务本身。

不会改动 config.yaml：persona 切换只在内存的 Config 对象上做。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time


def _repo_root() -> str:
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        if os.path.exists(os.path.join(d, "config.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


def _load_config(persona_rel: str) -> object:
    """读 config.yaml 成一个 Config，并就地把 agent 的人设切到实用版。

    只改内存中的 ns，不写回磁盘，不影响桌宠运行。
    """
    from src.config import Config
    path = os.path.join(_repo_root(), "config.yaml")
    import yaml  # type: ignore
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Config(raw, path)
    # 切 persona：agent 优先于 cascade，设了它就不会回落到达妮娅
    ag = getattr(cfg.ns, "agent", None)
    if ag is not None:
        ag.persona_file = persona_rel
        ag.behavior_file = ""  # 清空陪伴行为规范
    return cfg


async def _run(instruction: str, cfg: object, confirm: str) -> None:
    from src.llm.agent_brain import run_agent_task
    from src.system.tasks import record  # 复用同一套判定，拿回记录对象
    import time as _t

    confirmed = 0

    async def _on_confirm(desc: str) -> bool:
        nonlocal confirmed
        confirmed += 1
        if confirm == "ask":
            try:
                ans = input(f"  确认执行？ {desc} [y/N] ").strip().lower()
            except Exception:  # noqa: BLE001
                return False
            return ans in ("y", "yes", "是")
        return True  # auto：用户已用命令行下达，默认放行

    def _on_status(payload):
        if isinstance(payload, dict):
            txt = payload.get("text")
            if txt:
                phase = payload.get("phase", "")
                mark = "✓" if phase == "done" else "·"
                print(f"  {mark} {txt}", flush=True)

    t0 = _t.time()
    result = await run_agent_task(
        instruction, cfg=cfg, on_status=_on_status, on_confirm=_on_confirm,
    )
    dur = _t.time() - t0

    print("\n" + "=" * 56)
    print("结果：")
    print((result or "（无返回）").strip())
    print("-" * 56)
    print(f"耗时 {dur:.1f}s · 确认闸触发 {confirmed} 次 · 记录见 data/tasks.jsonl")
    print("=" * 56)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="task_cli", description="把 Denia 当工作助手用（无桌宠壳）")
    ap.add_argument("instruction", nargs="+", help="一句自然语言任务")
    ap.add_argument("--persona", default="prompts/workbuddy_persona.txt",
                    help="persona 文件相对工程根（默认实用版）")
    ap.add_argument("--confirm", choices=["auto", "ask"], default="auto",
                    help="高危操作确认方式：auto=默认放行 / ask=逐项询问")
    args = ap.parse_args(argv)

    instruction = " ".join(args.instruction)
    try:
        cfg = _load_config(args.persona)
    except Exception as e:  # noqa: BLE001
        print(f"[配置加载失败] {e}", file=sys.stderr)
        return 2

    try:
        asyncio.run(_run(instruction, cfg, args.confirm))
    except KeyboardInterrupt:
        print("\n[已中断]", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
