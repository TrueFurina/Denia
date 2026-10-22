# -*- coding: utf-8 -*-
"""语音→LLM→TTS 消费链的可复用测试夹具（不依赖 pytest，供单测与变异工具共用）。

目标：在隔离环境里驱动 Dialogue 的整条 USER_TEXT(source=voice) → respond → AI_DECISION
链路，并能在「respond 尾段（无 try 包裹）抛异常」时，验证 fire-and-forget 后台任务是否把
异常静默吞掉（第七处候选静默失效："她听到却不回答"）。

为什么单独抽一个 harness：
- pytest 单测（test_dialogue_voice_chain.py）与变异工具（tools/_qc_dialogue_voice_chain_mutation.py）
  都要跑同一套驱动逻辑，写两遍必然漂移（本项目 §5：断言口径要单一事实源）。
- 变异工具需要注入一个「去掉 done_callback 的 Dialogue 类」，故构造入口必须接受
  DialogueCls 参数。

桩约定：
- FakeLLM.available=True；本 harness 覆盖 _chat_decision / _decide_search /
  _tools_in_chat_enabled / _build_messages / _maybe_synthesize_memory，让链路只走
  "决策已生成 → 剥 markdown → 拟人化 → 记历史 → 发 AI_DECISION"这一段，不碰真实 LLM /
  搜索 / 工具 / 记忆提炼（那些各自有独立单测）。
- strip_markdown_raise=True：把 _strip_markdown 换成抛异常的桩，模拟 respond 尾段
  （dialogue.py 1186 行，位于 respond 内部 try/except 之外）偶发崩溃。

运行：.venv/Scripts/python.exe -c "from src.tests._dlg_voice_harness import *"
"""
from __future__ import annotations

import asyncio

from src.event_bus import EventBus, Event, EventType
from src.state_machine import StateMachine
from src.config import Config
from src.companion.persona import Persona
from src.llm.base import LLMClient
from src.companion.dialogue import Dialogue

# 一条含合法 JSON 外壳的决策：emotion/action/intensity/text 齐全、text 非空。
# decision_had_json(raw) 为 True → 不触发 JSON 兜底重试（省一次 LLM.chat）。
VALID_DECISION_RAW = (
    '{"emotion":"happy","action":"talk","intensity":3,'
    '"text":"今天天气不错呀，要一起出去走走吗？"}'
)

FAIL_TEXT = "消息处理失败了，请稍后再试～"


class FakeLLM(LLMClient):
    """最小可用 LLM 桩：available=True；本 harness 覆盖 _chat_decision 故不触达 chat。

    必须显式覆写 LLMClient 的全部抽象成员（available / vision_available / chat /
    vision_describe），否则 ABCMeta 拒绝实例化。
    """

    name = "fake"

    def __init__(self, available: bool = True) -> None:
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    @property
    def vision_available(self) -> bool:
        return False

    async def chat(self, messages, options=None):
        return "{}"

    async def vision_describe(self, image_bytes, prompt):
        return ""

    async def chat_stream(self, messages, options=None):
        yield "{}"


def _minimal_config() -> Config:
    """一份能通过 Config 构造的最小配置（无文件、无绝对路径，纯内存 dict）。"""
    raw = {
        "characters": [{
            "id": "danya",
            "name": "达妮娅",
            "character": "桌面伴侣",
            "speaking_style": "可爱",
            "background": "测试",
        }],
        "active_character_id": "danya",
        "voice_profiles": [],
        "voice": {"mic_enabled": True},
        "perception": {"vision": {"enabled": False}},
        "vision_models": {"enabled": False},
        "proactive": {"duplex_mode": "local"},
        "runtime": {"text_mode_only": False},
    }
    return Config(raw)


def build_dialogue(*, strip_markdown_raise: bool = False,
                   DialogueCls=Dialogue):
    """构造一个跑得通 respond 全链路的 Dialogue，并接好订阅。

    返回 (bus, dlg)。调用方负责 drive_voice 与收尾。
    """
    bus = EventBus()
    cfg = _minimal_config()
    state = StateMachine(bus)
    persona = Persona.from_config(cfg)
    llm = FakeLLM(available=True)
    dlg = DialogueCls(bus, llm, state, persona, cfg)

    # —— 只 stub 会外延到真实 LLM / 搜索 / 工具 / 记忆的协作者，保留 respond 真身 ——
    async def _fake_chat_decision(messages, streamer, channel="", seg_limit=0):
        # 返回 (raw, streamed)；raw 是含 JSON 外壳的合法决策
        return (VALID_DECISION_RAW, True)

    async def _fake_decide_search(user_text):
        return (False, "")

    dlg._chat_decision = _fake_chat_decision
    dlg._decide_search = _fake_decide_search
    dlg._tools_in_chat_enabled = lambda: False
    dlg._build_messages = (
        lambda *a, **k: [{"role": "user", "content": (a[0] if a else "")}])
    dlg._maybe_synthesize_memory = lambda: asyncio.sleep(0)

    if strip_markdown_raise:
        def _boom(text):
            raise RuntimeError("injected tail failure: _strip_markdown")
        dlg._strip_markdown = _boom

    dlg.subscribe()
    return bus, dlg


async def drive_voice(bus: EventBus, dlg: Dialogue,
                      text: str = "今天天气怎么样", source: str = "voice",
                      timeout: float = 5.0):
    """发布一条 voice USER_TEXT，把链路跑到底，返回 (收集到的 AI_DECISION 载荷, 后台任务)。

    语义刻意贴合生产：USER_TEXT 走 bus.run 串行派发 → _on_user_text 里 create_task
    落后台任务 → 立即返回。本函数不 await 后台任务取异常（那正是要验证的"静默点"），
    而是轮询等它 done，再等 AI_DECISION 派发（成功气泡 / 失败气泡都算）。

    返回的 task 供断言 task.exception()——证明"确实发生过异常"，配合"有无失败气泡"
    区分「被兜底接住」与「被静默吞掉」。
    """
    got = []
    done = asyncio.Event()

    async def _capture(e):
        if e.type == EventType.AI_DECISION:
            got.append(e.data)
            done.set()

    bus.subscribe(EventType.AI_DECISION, _capture)
    bus.bind_loop(asyncio.get_event_loop())
    bus_run = asyncio.create_task(bus.run())
    task = None
    try:
        await bus.publish(Event(EventType.USER_TEXT, {"text": text, "source": source}))
        # 给总线一点时间派发 USER_TEXT 并创建后台任务
        # （_desktop_msg_task 只在 _on_user_text 里首次赋值，构造期不存在，故用 getattr）
        for _ in range(50):
            task = getattr(dlg, "_desktop_msg_task", None)
            if task is not None:
                break
            await asyncio.sleep(0)
        if task is not None:
            # 等它自然结束，但**不在此 await 取异常**（那是被测的静默点）
            for _ in range(int(timeout * 20)):
                if task.done():
                    break
                await asyncio.sleep(0.05)
        # 等 AI_DECISION 派发（成功或失败气泡）；超时 = 一条都没发（静默失声）
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
    finally:
        bus.stop()
        bus_run.cancel()
        try:
            await bus_run
        except asyncio.CancelledError:
            pass
    return got, task
