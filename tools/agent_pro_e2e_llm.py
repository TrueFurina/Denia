# -*- coding: utf-8 -*-
"""agent_pro 包测（真实 LLM）：AgentBrain + 技能包启用，跑真实指令。"""
import asyncio
import sys
import time

sys.path.insert(0, r"G:\ai对话\11")
ROOT = r"G:\ai对话\11"

from src.agent_pro import install, uninstall
install(None, flags_override={"enabled": True})  # 包测强制启用

import yaml
from src.config import Config
from src.llm.agent_brain import AgentBrain

cfg = Config(yaml.safe_load(open(ROOT + r"\config.yaml", encoding="utf-8")), ROOT + r"\config.yaml")
brain = AgentBrain.from_config(cfg, ROOT)
print(f"大脑: {brain.model} | max_steps={brain.max_steps}")


async def run(instruction: str) -> None:
    steps = []

    async def st(d):
        if isinstance(d, dict) and d.get("phase") == "step":
            steps.append(d.get("tool"))
            print(f"    · {d.get('tool')} {str(d.get('args'))[:60]}")

    print(f"\n>>> 指令: {instruction}")
    t0 = time.time()
    out = await brain.run(instruction, on_status=st, on_confirm=None)
    dt = time.time() - t0
    print(f"    工具链: {steps or '【无】'} | 耗时 {dt:.1f}s")
    print(f"    回复: {out[:260]}".replace("\n", " "))


async def main():
    try:
        await run("帮我打开汽水音乐")
        await run("帮我在桌面找找有哪些png图片")
        await run("帮我看看电脑现在开了哪些窗口")
    finally:
        uninstall()  # 包测结束恢复现场


asyncio.run(main())
