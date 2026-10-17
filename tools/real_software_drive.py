# -*- coding: utf-8 -*-
"""真机驱动器：用软件真实对象图端到端实测，不是 mock。

构造与 main._build_system 完全一致的对象图：
    EventBus + StateMachine + create_llm(真实路由→qwen-max) + Persona
    + MemoryStore(真实) + Dialogue(真实) + ProactiveStrategy(真实)
用户消息通过总线发 USER_TEXT 事件（= 前端发消息走的同一条路），
捕获 AI_DECISION（= GUI 显示的同一事件），读她真实回出来的话。
再实测：记忆库/上下文是否互通、主动互动是否真触发。
"""
import sys
import os
import asyncio
import time
import json
import logging
import traceback

ROOT = r"G:\ai对话\11"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "docs", "_real_software_drive.txt")
_lines: list[str] = []


def log(s: str = "") -> None:
    _lines.append(str(s))
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines))
    except Exception:
        pass


class _LogCap(logging.Handler):
    def emit(self, r):
        try:
            log("   ·[日志] " + r.getMessage())
        except Exception:
            pass


async def main() -> None:
    log("=" * 80)
    log("真机驱动器 · 用软件真实管线端到端实测")
    log("时间：" + time.strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 80)

    from src.config import load_config
    from src.event_bus import EventBus, Event, EventType
    from src.state_machine import StateMachine
    from src.llm import create_llm
    from src.companion.persona import Persona
    from src.companion.dialogue import Dialogue
    from src.companion.memory_store import MemoryStore

    def _make_memory_store(cfg):
        """与 main._make_memory_store 等价（内联，避免导入 main.py 的重依赖）。"""
        mem_cfg = getattr(cfg.ns, "memory", None)
        if mem_cfg is not None and not getattr(mem_cfg, "enabled", True):
            return None
        max_items = getattr(mem_cfg, "max_items", 100) if mem_cfg else 100
        mode = getattr(mem_cfg, "mode", "jarvis") if mem_cfg else "jarvis"
        semantic = bool(getattr(mem_cfg, "semantic_search", True)) if mem_cfg else True
        embed_fn = None
        if semantic:
            try:
                from src.llm.ollama_client import make_embed_fn
                embed_fn = make_embed_fn(cfg.ns.ollama.host)
            except Exception:
                embed_fn = None
        return MemoryStore(max_items=max_items, mode=mode, embed_fn=embed_fn)

    # 捕获主动/关系引擎的日志，便于区分“没跑”还是“被闸门拦下”
    for name in ("src.companion.proactive", "src.companion.relationship"):
        lg = logging.getLogger(name)
        lg.addHandler(_LogCap())
        lg.setLevel(logging.INFO)

    cfg = load_config(os.path.join(ROOT, "config.yaml"))
    log(f"配置：active_character_id={getattr(cfg, 'active_character_id', '?')}")

    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.bind_loop(loop)
    state = StateMachine(bus)
    llm = create_llm(cfg)
    persona = Persona.from_config(cfg)
    memory = _make_memory_store(cfg)
    log(f"persona.name={getattr(persona, 'name', '?')}  "
        f"memory={'有' if memory else '无'}  路由={type(llm).__name__}")

    dialogue = Dialogue(bus, llm, state, persona, cfg, memory_store=memory)
    dialogue.subscribe()

    captured: list[dict] = []

    async def _cap(ev: Event) -> None:
        captured.append(ev.data or {})

    bus.subscribe(EventType.AI_DECISION, _cap)
    bus_task = asyncio.create_task(bus.run(), name="bus")

    log(f"记忆库启动前：count={memory.count() if memory else '-'}"
        f"  类型={memory.count_by_type() if memory else '-'}")

    async def chat(msg: str, wait_s: float = 100.0) -> dict | None:
        before = len(captured)
        await bus.publish(Event(EventType.USER_TEXT,
                                {"text": msg, "source": "chat"}, "test"))
        t0 = time.time()
        while time.time() - t0 < wait_s:
            await asyncio.sleep(0.3)
            for d in captured[before:]:
                if str(d.get("source")) != "proactive" and (d.get("text") or "").strip():
                    return d
        return None

    # ============ ① 各种类型的对话 ============
    log("\n" + "=" * 80)
    log("① 用户对话（各种类型，真实 Dialogue 管线）")
    log("=" * 80)

    script = [
        ("基础问候", "早啊"),
        ("分享日常", "今天加班到现在，累死了"),
        ("情绪低落", "项目又出bug了，我有点烦"),
        ("埋事实·重要", "跟你说个事，我女朋友叫小雨，下周三我要去医院拔智齿"),
        ("兴趣分享", "我最近迷上看科幻片了，沙丘真好看"),
        ("被调侃", "你是不是傻，别的AI都比你聪明"),
        ("撒娇/想她", "想你了"),
        ("半夜", "睡不着，脑子全是工作的事"),
        ("问关系", "你喜欢我吗"),
        ("记忆召回·探针", "你还记得我下周三要干嘛吗？"),
        ("记忆召回·探针2", "我女朋友叫什么来着？"),
        ("告别", "我先去睡了，晚安"),
    ]
    chat_turns = []
    for label, msg in script:
        d = await chat(msg)
        if d:
            t = (d.get("text") or "").strip()
            log(f"\n【{label}】")
            log(f"  我：{msg}")
            log(f"  她（{d.get('emotion')}/{d.get('action')}）：{t}")
            chat_turns.append({"label": label, "user": msg, "text": t,
                               "emotion": d.get("emotion"), "action": d.get("action")})
        else:
            log(f"\n【{label}】")
            log(f"  我：{msg}")
            log("  她：（超时无回复）")
            chat_turns.append({"label": label, "user": msg, "text": "", "timeout": True})
        await asyncio.sleep(3)

    # ============ ② 记忆库 / 上下文 互通 ============
    log("\n" + "=" * 80)
    log("② 记忆库与上下文是否互通")
    log("=" * 80)
    try:
        log(f"记忆库 count={memory.count() if memory else '-'}")
        log(f"记忆库 类型分布={memory.count_by_type() if memory else '-'}")
        facts = memory.list_user_facts(limit=25) if memory else []
        log(f"记忆库 list_user_facts（{len(facts)} 条）：")
        for i, f in enumerate(facts, 1):
            log(f"  {i}. {f}")
    except Exception:
        log("读记忆库异常：\n" + traceback.format_exc())

    # 读共享会话上下文 data/context/<char>.jsonl
    try:
        cid = str(getattr(cfg, "active_character_id", "") or "")
        cpath = os.path.join(ROOT, "data", "context", f"{cid}.jsonl")
        log(f"\n共享会话上下文文件：{cpath}")
        if os.path.exists(cpath):
            rows = []
            with open(cpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
            log(f"  共 {len(rows)} 行；末 8 行：")
            for r in rows[-8:]:
                role = r.get("role", "?")
                content = str(r.get("content", ""))[:90]
                log(f"    [{role}] {content}")
        else:
            log("  （文件不存在 —— 说明对话未落到共享上下文）")
        # 直接问 dialogue 历史桶
        try:
            from src.companion.conversation_history import get_history
            h = get_history()
            recent = h.get_recent(8) if h else []
            log(f"  conversation_history.get_recent(8)={len(recent)} 条")
        except Exception:
            log("  conversation_history 读取跳过：" + traceback.format_exc().splitlines()[-1])
    except Exception:
        log("读上下文异常：\n" + traceback.format_exc())

    # ============ ③ 主动互动（真实 ProactiveStrategy） ============
    log("\n" + "=" * 80)
    log("③ 主动互动实测（真实 ProactiveStrategy，强制空闲态）")
    log("=" * 80)
    try:
        from src.companion.proactive import ProactiveStrategy
        pro = ProactiveStrategy(bus, state, cfg, llm=llm, persona=persona,
                                monitor=None, memory_store=memory)
        pro._llm_generate = True
        pro._idle_threshold_s = 180.0
        pro._session_boot_ts = time.time() - 400.0
        pro._last_input_ts = time.time() - 400.0
        pro._last_proactive = None
        log(f"已构造真实 ProactiveStrategy：idle_threshold={pro._idle_threshold_s}s "
            f"llm_generate={pro._llm_generate} in_call={pro._in_call()}")

        async def run_proactive(desc: str, coro_fn) -> None:
            before = len(captured)
            try:
                res = await coro_fn()
            except Exception:
                log(f"[{desc}] 调用异常：\n" + traceback.format_exc())
                return
            # 等 debounce flush
            got = None
            t0 = time.time()
            while time.time() - t0 < 12:
                await asyncio.sleep(0.3)
                for d in captured[before:]:
                    if str(d.get("source")) == "proactive" and (d.get("text") or "").strip():
                        got = d
                        break
                if got:
                    break
            if got:
                log(f"\n【主动·{desc}】返回={res}")
                log(f"  她主动开口：{got.get('text')}  "
                    f"(emotion={got.get('emotion')} action={got.get('action')} "
                    f"to_chat={got.get('to_chat')} trigger={got.get('trigger')})")
            else:
                log(f"\n【主动·{desc}】返回={res} —— 未捕获到主动消息（可能被闸门拦/概率未中）")

        await run_proactive("通用空闲关心 check_and_trigger", pro.check_and_trigger)

        # 长会话收尾（直接调真实生成器，强制触发）
        pro._long_session_warned = False
        pro._session_start_ts = time.time() - 40 * 60
        await run_proactive("长会话收尾 _emit_long_session_break",
                            lambda: pro._emit_long_session_break("danya", time.time()))

        # 记忆型主动（概率 1/3，多试几次）
        for i in range(4):
            _name = getattr(persona, "name", "达妮娅")
            await run_proactive(f"记忆型主动 _maybe_memory_initiative #{i+1}",
                                lambda: pro._maybe_memory_initiative(
                                    time.time(), _name, pro._focus_state(), 400.0))
            await asyncio.sleep(1)
    except Exception:
        log("主动段异常：\n" + traceback.format_exc())

    bus.stop()
    bus_task.cancel()
    log("\n" + "=" * 80)
    log("测试结束")
    log("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log("!!! FATAL EXCEPTION !!!\n" + traceback.format_exc())
