# -*- coding: utf-8 -*-
"""跨进程记忆互通验证（真软件持久化路径：enable_persistence + rehydrate）。

phase 1：开启持久化 → 真实 Dialogue 聊几句埋一个独特事实 → 退出（模拟关闭软件）。
phase 2：新进程 → 开启持久化 → 新建真实 Dialogue（__init__ 会 set_active_char→rehydrate）
          → 不提示任何事实，直接问“那个事实” → 看是否召回（跨会话记忆互通）。
"""
import sys
import os
import json
import asyncio
import time
import traceback

ROOT = r"G:\ai对话\11"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
PHASE = sys.argv[1] if len(sys.argv) > 1 else "1"
OUT = os.path.join(ROOT, "docs", f"_persist_phase{PHASE}.txt")
_lines: list[str] = []


def log(s: str = "") -> None:
    _lines.append(str(s))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))


async def main() -> None:
    log("=" * 78)
    log(f"跨进程记忆验证 · phase {PHASE}   时间：{time.strftime('%H:%M:%S')}")
    log("=" * 78)

    from src.config import load_config
    from src.event_bus import EventBus, Event, EventType
    from src.state_machine import StateMachine
    from src.llm import create_llm
    from src.companion.persona import Persona
    from src.companion.dialogue import Dialogue
    from src.companion.memory_store import MemoryStore
    from src.companion import conversation_history as CH
    from src.companion.history_store import ChatLogStore

    cfg = load_config(os.path.join(ROOT, "config.yaml"))
    cid = str(getattr(cfg, "active_character_id", "") or "")
    # === 真软件同款：开启上下文持久化（webapp:4600 的做法）===
    try:
        root = ChatLogStore().base.parent
    except Exception:
        root = os.path.join(ROOT, "data")
    ok = CH.enable_persistence(root)
    log(f"enable_persistence({root}) = {ok}")

    mem_cfg = getattr(cfg.ns, "memory", None)
    memory = MemoryStore(
        max_items=getattr(mem_cfg, "max_items", 100) if mem_cfg else 100,
        mode=getattr(mem_cfg, "mode", "jarvis") if mem_cfg else "jarvis",
        embed_fn=None)

    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.bind_loop(loop)
    state = StateMachine(bus)
    llm = create_llm(cfg)
    persona = Persona.from_config(cfg)

    # 构造 Dialogue 前，看盘中上下文
    before_rows = CH.load_context(cid, n=50)
    log(f"构造 Dialogue 前，盘中上下文 {cid}.jsonl 共 {len(before_rows)} 行")
    for r in before_rows[-4:]:
        log(f"    [{r.get('role')}] {str(r.get('content'))[:80]}")

    dialogue = Dialogue(bus, llm, state, persona, cfg, memory_store=memory)
    dialogue.subscribe()
    # 构造后，内存桶（已被 __init__ 的 set_active_char 回填）
    h = CH.shared_history()
    log(f"Dialogue 构造后，内存桶轮次={len(h.get_recent(200))}")

    captured: list[dict] = []

    async def _cap(ev):
        captured.append(ev.data or {})

    bus.subscribe(EventType.AI_DECISION, _cap)
    task = asyncio.create_task(bus.run())

    async def chat(msg, wait=100.0):
        before = len(captured)
        await bus.publish(Event(EventType.USER_TEXT, {"text": msg, "source": "chat"}, "test"))
        t0 = time.time()
        while time.time() - t0 < wait:
            await asyncio.sleep(0.3)
            for d in captured[before:]:
                if str(d.get("source")) != "proactive" and (d.get("text") or "").strip():
                    return d
        return None

    if PHASE == "1":
        msg = "我最近养了只鹦鹉，起名叫豆豆，它老是学我说话，烦死了"
        d = await chat(msg)
        log(f"\n埋事实 → 我：{msg}")
        log(f"  她：{(d or {}).get('text')}")
        await asyncio.sleep(2)
        # 再确认已写盘
        rows = CH.load_context(cid, n=10)
        log(f"\n本次结束后盘中行数={len(rows)}；末 4 行：")
        for r in rows[-4:]:
            log(f"    [{r.get('role')}] {str(r.get('content'))[:80]}")
    else:
        rows = CH.load_context(cid, n=200)
        log(f"\n盘中上下文 {len(rows)} 行（含上一进程写入）")
        q = "我养的那个宠物，叫什么名字来着？"
        d = await chat(q)
        log(f"\n跨会话召回测试 → 我：{q}")
        log(f"  她：{(d or {}).get('text')}")

    bus.stop()
    task.cancel()
    log("\n" + "=" * 78)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log("!!! FATAL !!!\n" + traceback.format_exc())
