# -*- coding: utf-8 -*-
"""语音→LLM→TTS 消费链的可复用测试夹具（不依赖 pytest，供单测与变异工具共用）。

目标：在隔离环境里驱动 Dialogue 的整条 USER_TEXT(source=voice) → respond → AI_DECISION
链路，并能在「respond 尾段（无 try 包裹）抛异常」时，验证 fire-and-forget 后台任务是否把
异常静默吞掉（第七处候选静默失效："她听到却不回答"）。

【2026-10-23 修正·第一版是谎】第一版把 `_chat_decision` 整个换成返回 `(RAW, True)` 的
桩——`streamed=True` 是**假的**：一条 PARTIAL_TEXT 都没发、TTS 什么都没念、聊天气泡什么
都没上屏。于是"尾段崩"场景模拟的是"崩在流式之前"的世界，与它注入的位置（流式之后）不是
同一个，从而掩盖了"她已答完 + 红字失败气泡"自相矛盾这个真缺陷。现在：**不桩
_chat_decision**，让 FakeLLM 真的支持流式（supports_stream + chat_stream），跑生产那条
真流式路径——PARTIAL_TEXT 是真发过的，`streamed` 不再是谎。

为什么单独抽一个 harness：pytest 单测（test_dialogue_voice_chain.py）与变异工具
（tools/_qc_dialogue_voice_chain_mutation.py）都要跑同一套驱动逻辑，写两遍必然漂移
（本项目 §5：断言口径要单一事实源）。变异工具还需要注入一个「去掉 done_callback 的
Dialogue 类」，故构造入口必须接受 DialogueCls 参数。

桩约定：
- 只 stub 会外延到真实搜索 / 工具 / 记忆的协作者；`_chat_decision` 保持真身。
- `stream_support=False`：FakeLLM 不支持流式 → `_chat_decision` 走
  `(await llm.chat(...), False)` 分支（生产里"后端不支持流式"的那条路）。
- `strip_markdown_raise=True`：把 `_strip_markdown` 换成抛异常的桩，模拟 respond 尾段
  （流式已结束之后）偶发崩溃——由 respond 的尾段守卫接住，任务本身无异常。
- `desktop_media_raise=True`：把 `_desktop_media_enabled` 换成抛异常的桩，崩在
  `_process_desktop_message` 里 respond **之外**——异常逃出 respond，只能由任务级
  done_callback 接住（第七处静默失效那道防线）。
两个注入点都把命中次数记到 `dlg._inject_hits`，供测试证明"这一步真执行到了"。

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

# 纯舞台动作描写 / 纯 markdown 骨架：弱模型真的会只吐这些。清洗后会被清空，
# 必须走"空响应"失败出口，而不能把"（翻个白眼）"当台词播出去、也不能发 text=""。
STAGE_DIRECTION_ONLY_RAW = (
    '{"emotion":"neutral","action":"idle","intensity":3,'
    '"text":"（翻个白眼）"}'
)
MARKDOWN_SCAFFOLD_ONLY_RAW = (
    '{"emotion":"neutral","action":"idle","intensity":3,'
    '"text":"****"}'
)


class FakeLLM(LLMClient):
    """最小可用 LLM 桩。**支持真流式**，让生产的 _chat_decision 原样跑。

    `raw` 可覆盖，用来喂各种"模型吐了什么"的输入（纯舞台动作描写、纯 markdown
    骨架、无 JSON 外壳的纯台词…）——生产里弱模型真的会吐这些。

    必须显式覆写 LLMClient 的全部抽象成员（available / vision_available / chat /
    vision_describe），否则 ABCMeta 拒绝实例化。
    """

    name = "fake"

    def __init__(self, available: bool = True, stream_support: bool = True,
                 raw: str = VALID_DECISION_RAW) -> None:
        self._available = available
        self._stream_support = stream_support
        self._raw = raw

    @property
    def available(self) -> bool:
        return self._available

    @property
    def vision_available(self) -> bool:
        return False

    def supports_stream(self) -> bool:
        return self._stream_support

    async def chat(self, messages, options=None):
        # 非流式路径（后端不支持流式）与 JSON 兜底重试都走这里
        return self._raw

    async def vision_describe(self, image_bytes, prompt):
        return ""

    async def chat_stream(self, messages, options=None):
        # 按 text 值切几块吐（逼真：生产是一个个 chunk 到达的）。切成 3 块，
        # 短 raw 时退化成 1~2 块，都不影响 _SentenceStreamer 切句。
        r = self._raw
        n = max(1, len(r) // 3)
        for i in range(0, len(r), n):
            yield r[i:i + n]


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
                   desktop_media_raise: bool = False,
                   stream_support: bool = True,
                   llm_raw: str = VALID_DECISION_RAW,
                   DialogueCls=None):
    """构造一个跑得通 respond 全链路的 Dialogue，并接好订阅。

    返回 (bus, dlg)。调用方负责 drive_voice 与收尾。

    DialogueCls 默认 None → 用模块全局 `Dialogue`（**延迟绑定**：变异工具通过
    `harness.Dialogue = mutant.Dialogue` 换掉被测类，测试里的断言就能原样复用在
    变异体上，不必再抄一遍——断言口径单一事实源）。

    `llm_raw`：模型这一轮吐出的原始文本，用来喂各种真实会出现的输入（纯舞台动作
    描写 / 纯 markdown 骨架 / 无 JSON 外壳的纯台词）。

    两个注入点（各自把命中次数记到 dlg._inject_hits，供测试证明
    "这一步确实执行到了"，而不是空跑）：
      · strip_markdown_raise：崩在 respond **尾段**（流式已结束之后）——由 respond
        2026-10-23 新加的尾段守卫接住，任务本身**不应**有异常；
      · desktop_media_raise：崩在 `_process_desktop_message` 里 respond **之外**
        （媒体摄入探测）——异常会逃出 respond，只能由任务级 done_callback 接住。
    """
    bus = EventBus()
    cfg = _minimal_config()
    state = StateMachine(bus)
    persona = Persona.from_config(cfg)
    llm = FakeLLM(available=True, stream_support=stream_support, raw=llm_raw)
    cls = DialogueCls if DialogueCls is not None else Dialogue
    dlg = cls(bus, llm, state, persona, cfg)

    # —— 只 stub 会外延到真实搜索 / 工具 / 记忆的协作者，_chat_decision 保持真身 ——
    async def _fake_decide_search(user_text):
        return (False, "")

    dlg._decide_search = _fake_decide_search
    dlg._tools_in_chat_enabled = lambda: False
    dlg._build_messages = (
        lambda *a, **k: [{"role": "user", "content": (a[0] if a else "")}])
    dlg._maybe_synthesize_memory = lambda: asyncio.sleep(0)

    dlg._inject_hits = 0

    if strip_markdown_raise:
        def _boom(text):
            dlg._inject_hits += 1
            raise RuntimeError("injected tail failure: _strip_markdown")
        dlg._strip_markdown = _boom

    if desktop_media_raise:
        def _boom_media():
            dlg._inject_hits += 1
            raise RuntimeError("injected: _desktop_media_enabled (respond 之外)")
        dlg._desktop_media_enabled = _boom_media

    dlg.subscribe()
    return bus, dlg


async def drive_voice(bus: EventBus, dlg: Dialogue,
                      text: str = "今天天气怎么样", source: str = "voice"):
    """发布一条 voice USER_TEXT，把链路跑到底。

    返回 (ai_decisions, partial_texts, task)：
      · ai_decisions：收集到的 AI_DECISION 载荷列表；
      · partial_texts：收集到的 PARTIAL_TEXT 载荷列表（证明流式真的发生过）；
      · task：_on_user_text 建的后台任务（供断言 task.exception()）。

    语义刻意贴合生产：USER_TEXT 走 bus.run 串行派发 → _on_user_text 里 create_task
    后台任务 → 立即返回。本函数**不 await 后台任务取异常**（那正是要验证的静默点），
    改轮询它 done。

    等待全部用**循环轮次**（asyncio.sleep(0)），不用墙上时钟：依赖时间的判据会偶发假红
    （本项目已有前科）。收尾用 bus._queue.join() 保证"已入队的事件全部派发完毕"，
    这样"没有任何 AI_DECISION"才是一个确定的否定结论，而不是"5 秒内没到"。
    """
    ai: list[dict] = []
    partials: list[dict] = []

    async def _cap(e):
        if e.type == EventType.AI_DECISION:
            ai.append(e.data)
        elif e.type == EventType.PARTIAL_TEXT:
            partials.append(e.data)

    bus.subscribe(EventType.AI_DECISION, _cap)
    bus.subscribe(EventType.PARTIAL_TEXT, _cap)
    bus.bind_loop(asyncio.get_event_loop())
    bus_run = asyncio.create_task(bus.run())
    try:
        await bus.publish(Event(EventType.USER_TEXT, {"text": text, "source": source}))

        # 1) 等总线派发 USER_TEXT 并创建后台任务
        # （_desktop_msg_task 只在 _on_user_text 里首次赋值，构造期不存在，故用 getattr）
        task = None
        for _ in range(64):
            task = getattr(dlg, "_desktop_msg_task", None)
            if task is not None:
                break
            await asyncio.sleep(0)
        if task is None:
            raise RuntimeError("harness: USER_TEXT 没有创建桌面后台任务")

        # 2) 等它自然结束，但**不在此 await 取异常**（那是被测的静默点）
        for _ in range(512):
            if task.done():
                break
            await asyncio.sleep(0)
        if not task.done():
            raise RuntimeError("harness: 桌面后台任务在 512 个循环轮次内没结束")

        # 3) 让 done_callback（call_soon 调度）跑完，再把它入队的事件全部派发干净
        for _ in range(32):
            await asyncio.sleep(0)
        await bus._queue.join()
        return ai, partials, task
    finally:
        bus.stop()
        bus_run.cancel()
        try:
            await bus_run
        except asyncio.CancelledError:
            pass
