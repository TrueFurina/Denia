# -*- coding: utf-8 -*-
"""agent_pro · dsh 思路全链路真机包测：open_app → search_in_app → media_state"""
import asyncio
import sys
import time

sys.path.insert(0, r"G:\ai对话\11")

from src.agent_pro import install, uninstall
from src.llm.agent_tools import dispatch_tool


def _vlm_ctx():
    import yaml, io
    d = yaml.safe_load(io.open(r"G:\ai对话\11\config.yaml", encoding="utf-8"))
    ctx = {"cwd": r"G:\ai对话\11"}
    vm = d.get("vision_models") or {}
    for m in (vm.get("models") or []):
        if m.get("id") == (vm.get("active") or ""):
            ctx.update(vlm_base_url=m.get("base_url"), vlm_model=m.get("model"),
                       vlm_api_key=m.get("api_key"))
    return ctx


async def main():
    install(None, flags_override={"enabled": True})
    ctx = _vlm_ctx()
    print("VLM ctx 就绪:", bool(ctx.get("vlm_api_key")))
    try:
        print("== [1] open_app 汽水音乐（应用索引 + 启动验证）==")
        t0 = time.time()
        r = await dispatch_tool("open_app", {"target": "汽水音乐"}, ctx)
        print(f"耗时 {time.time()-t0:.1f}s"); print(r)

        await asyncio.sleep(3)  # 等窗口就绪

        print("\n== [2] search_in_app：搜「晴天 周杰伦」==")
        t0 = time.time()
        r = await dispatch_tool("search_in_app", {"app": "汽水音乐", "query": "晴天 周杰伦"}, ctx)
        print(f"耗时 {time.time()-t0:.1f}s"); print(r)

        print("\n== [3] media_state 复核（等 6 秒让搜索结果出歌）==")
        await asyncio.sleep(6)
        r = await dispatch_tool("media_state", {}, {})
        print(r)
    finally:
        uninstall()


if __name__ == "__main__":
    asyncio.run(main())
