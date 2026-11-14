"""关系层「接入」集成测试 —— 直接打 reporter 的配对/落盘/时序接缝。

引擎规则本身有 tools/test_relationship.py 覆盖（41 项）；本文件专测
**引擎之外的接入层**（relationship_reporter + 事件流 + 与 webapp/dual 的交互），
因为历次事故都出在"引擎没错、接入错了"的接缝上。

只用 stdlib（unittest），不依赖 pytest（本项目 pytest 被 langsmith 插件弄坏）。
    python tools/test_relationship_integration.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.event_bus import Event, EventBus, EventType  # noqa: E402
from src.companion import relationship as rel  # noqa: E402
from src.companion import relationship_reporter as rr  # noqa: E402


class _FakeCfg:
    """最小 cfg：reporter 只用 active_character_id + ns.relationship。"""
    def __init__(self, cid="danya", rel_on=True):
        self.active_character_id = cid
        self.ns = type("NS", (), {"relationship": type("RC", (), {"enabled": rel_on})()})()

    def get_character_by_id(self, cid):
        return type("Ch", (), {"name": "达妮娅", "character": "温柔", "background": "", "speaking_style": ""})()

    def get_active_character(self):
        return self.get_character_by_id(self.active_character_id)


class _FakeLLM:
    """裁判用：直接按文本回一个固定 JSON。"""
    def __init__(self, payload='{"valence": 0.8, "care": 0.0, "perfunctory": 0.0, "repair": 0.0, "harsh": 0.0, "sufficient": true, "confidence": 1.0, "reason": "test"}'):
        self.payload = payload
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.payload


def _mk_tmp_base():
    d = tempfile.mkdtemp(prefix="reltest_")
    return d


class TestPairing(unittest.TestCase):
    """USER_TEXT ↔ AI_DECISION 的配对：FIFO 队列 _pending_user_queue 的正确性。"""

    def setUp(self):
        # 每个测试独立 base_dir；重置 relationship 单例，避免串数据
        rel._MGR = None
        self.base = _mk_tmp_base()
        self.bus = EventBus()
        self.cfg = _FakeCfg()
        self.llm = _FakeLLM()
        self.rep = rr.RelationshipReporter(
            self.bus, self.cfg, enabled=True, base_dir=self.base, llm=self.llm)
        self.rep.subscribe()
        self.rep.start()

    def tearDown(self):
        rel._MGR = None
        shutil.rmtree(self.base, ignore_errors=True)

    async def _run_bus_briefly(self, coro):
        """与事件总线消费循环并发跑：总线必须 run() 才会派发 handler。"""
        task = asyncio.ensure_future(self.bus.run())
        try:
            await coro()
        finally:
            self.bus.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # 取消后台总线任务 = 场景正常收尾
                pass

    def test_01_basic_pair_updates_state(self):
        """一轮正常配对：user→ai 后，好感被判定更新（turns +1）。"""
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "你今天怎么样呀", "source": "webui"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "我很好哦", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)  # 等异步判定
        asyncio.run(self._run_bus_briefly(go))
        st = self.rep._mgr.get("danya")
        self.assertGreaterEqual(int(st.get("turns") or 0), 1, "配对后应记一轮")

    def test_02_pending_queue_no_overwrite(self):
        """🟢 回归：用户连发两句时，FIFO 队列不覆盖，AI 回两句各配一次（旧单槽会丢第一句）。

        旧实现（单槽 _pending_user_text）：用户连发"第一句话""第二句话"→ 第二句覆盖第一句，
        第一句永远配不上回复 → 白丢一轮判定。
        新实现（deque）：两句都在队列里，AI 回两句按顺序各消费一次。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "第一句话", "source": "webui"}))
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "第二句话", "source": "webui"}))
            await asyncio.sleep(0.05)
            # 队列语义：两句都在，按序保留
            self.assertEqual(list(self.rep._pending_user_queue), ["第一句话", "第二句话"],
                             "连发两句应都在队列里（不覆盖）")
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "回第一句", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.2)
            self.assertEqual(list(self.rep._pending_user_queue), ["第二句话"],
                             "回一句应消费队列头部（第一句话）")
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "回第二句", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
            self.assertEqual(list(self.rep._pending_user_queue), [],
                             "回两句应把队列消费空")
        asyncio.run(self._run_bus_briefly(go))
        # 两句用户话应都被判过（旧单槽只会判到一句）
        joined = " ".join(str(m) for m in self.llm.calls)
        self.assertIn("第一句话", joined, "第一句话也应被判（旧单槽会丢它）")
        self.assertIn("第二句话", joined, "第二句话也应被判")

    def test_03_proactive_not_counted(self):
        """双工自言自语（source=proactive）不得进聊天关系、不得消费用户 pending 队列。"""
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "我喜欢你", "source": "webui"}))
            await asyncio.sleep(0.05)
            self.assertEqual(list(self.rep._pending_user_queue), ["我喜欢你"])
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "（双工自言自语）", "source": "proactive", "is_proactive": True}))
            await asyncio.sleep(0.3)
            # 不触发 LLM 裁判（proactive 不进聊天关系）
            self.assertEqual(len(self.llm.calls), 0, "proactive 不应触发关系判定")
            # source=proactive 在 _on_ai_decision 的 `if src not in _CHAT_SOURCES: return`
            #   就早返回了，根本没走到 is_proactive 分支 → 不消费队列。
            #   语义上正确：用户那句留着等下一次"真正的聊天回复"配对，不会被主动发言吞掉。
            self.assertEqual(list(self.rep._pending_user_queue), ["我喜欢你"],
                             "proactive 不消费用户队列（留给真回复配对）")
        asyncio.run(self._run_bus_briefly(go))

    def test_04_dedup_same_ai_text(self):
        """同一句 AI 文本 5s 内重复投递 → 只记一次。

        【2026-11-10 修·这条测试从来没验过去重】原场景是「1 句用户话 + 3 次同样的
        AI 回复」，断言 `turns <= 1`。它绿的原因是**队列只有一句**：第一次回复就把
        队列取空了，后两次走到 `if not self._pending_user_queue: return` 就返回了 ——
        去重那一行**根本执行不到**。探针实测：把去重整段删掉，本测试照样绿。

        真正的判据要让队列**还有货**：先连发两句，再用同一句 AI 文本回两次。
          · 有去重 → 第二次被丢，队列里还剩第二句
          · 无去重 → 第二次把第二句也消费掉，turns 变 2
        另外把 `<= 1` 收紧成 `== 1`：`<= 1` 连"根本没判"（turns=0）也算过，
        而 0 恰恰是"整条链路坏了"的形状 —— 与 F3 那次「没有 Key 可擦」压成
        「Key 被擦掉」是同一个病。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "第一句", "source": "webui"}))
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "第二句", "source": "webui"}))
            await asyncio.sleep(0.05)
            for _ in range(2):
                await self.bus.publish(Event(EventType.AI_DECISION, {"text": "同一句回复", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
            self.assertEqual(list(self.rep._pending_user_queue), ["第二句"],
                             "重复的 AI 文本必须被去重丢弃（否则白吃一轮用户话）")
        asyncio.run(self._run_bus_briefly(go))
        st = self.rep._mgr.get("danya")
        self.assertEqual(int(st.get("turns") or 0), 1,
                         "同句重复投递只应记一轮（==1：<=1 会把'根本没判'也判成通过）")
        self.assertEqual(len(self.llm.calls), 1, "裁判只应被叫一次")

    def test_05_streamed_final_is_judged(self):
        """🔴 历史大坑回归：streamed=True 是最终整句，必须判定（曾误当分片过滤）。"""
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "谢谢你帮了大忙", "source": "webui"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "不客气", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
        asyncio.run(self._run_bus_briefly(go))
        st = self.rep._mgr.get("danya")
        self.assertGreaterEqual(int(st.get("turns") or 0), 1, "streamed=True 必须判定")

    def test_06_judge_sees_user_text_not_ai(self):
        """裁判**待判文本**必须是用户那句话，不是 AI 回复（防张冠李戴）。

        2026-10-10 更新：裁判现在会在【最近对话上下文】里**带上 AI 回复**（用于理解
        反讽/承上省略等），这属于正确行为。但核心契约不变——`【用户这句话】` 区块里
        必须是**用户**那句；AI 那句只允许出现在上下文区块，绝不能成为待判文本。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "独特用户串ABC", "source": "webui"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "独特AI串XYZ", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
        asyncio.run(self._run_bus_briefly(go))
        self.assertTrue(self.llm.calls, "裁判应被调用")
        # 逐条检查：每一条裁判消息里，待判文本区块必须只含用户串
        for msgs in self.llm.calls:
            joined = " ".join(str(m) for m in msgs)
            # 待判文本 = "【用户这句话】" 之后、下一个区块/结尾之前
            marker = "【用户这句话】"
            if marker in joined:
                # 【2026-10-21 修·取错了区块】prompt 里有多处 `【用户这句话】`
                # —— few-shot 示例每条都带一个。原实现 `split(marker, 1)[1]` 取的是
                # **第一个**，那是示例区的；于是断言拿"示例之后的所有文本"去查 AI 串，
                # 必然命中（后面还有别的示例 + 真正的 payload 里的上下文）。
                # 真正要验的是**本次待判文本**：只取最后一个区块。
                tail = joined.rsplit(marker, 1)[1]
                self.assertIn("独特用户串ABC", tail, "待判文本必须是用户那句话")
                self.assertNotIn("独特AI串XYZ", tail, "AI 回复绝不能进入待判文本区")

    def test_07_judge_receives_conversation_context(self):
        """🟢 回归（2026-10-10 修·逐句独立判定）：裁判必须拿到**最近对话上下文**。

        场景：用户先问一句 → 她回答 → 用户再回一句。判定最后一句时，
        prompt 里应出现【最近对话上下文】区块，且包含**上一轮的问与答**，
        这样裁判才能识别"承上省略/反讽"等依赖上文的语义。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "我该不该辞职呢", "source": "webui"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "要看你的积蓄和下一份打算哦", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "好的", "source": "webui"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "嗯嗯，想清楚就好", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
        asyncio.run(self._run_bus_briefly(go))
        joined = " ".join(str(m) for m in self.llm.calls)
        self.assertIn("【最近对话上下文", joined, "裁判 prompt 必须带上下文区块")
        # 上下文里应能看到上一轮的问与答（说明确实取了历史，而非只给孤句）
        self.assertIn("我该不该辞职呢", joined, "上下文应含上一轮用户问句")
        self.assertIn("要看你的积蓄", joined, "上下文应含上一轮她的回答")

    # ---- 2026-11-10 补：四条「零判据」的接入层行为 ----

    def test_08_disabled_reporter_does_nothing(self):
        """`relationship.enabled=false` → 一句都不判、一条历史都不写、不进配对队列。

        【为什么补】`enabled` 有三处早退（`start` / `_on_user_text` / `_on_ai_decision`），
        而本文件所有用例都用 `enabled=True` 起 reporter —— 关掉的那条路**一次都没走过**。
        探针实测：把 `_on_ai_decision` 的 `if not self.enabled: return` 改成 `if False:`，
        全门 13 项**照样 OK**。用户把关系功能关掉，本来是不该有任何写入的。
        """
        async def go():
            bus = EventBus()
            rep = rr.RelationshipReporter(bus, _FakeCfg(rel_on=False), enabled=False,
                                          base_dir=self.base, llm=self.llm)
            rep.subscribe()
            rep.start()
            task = asyncio.ensure_future(bus.run())
            try:
                await bus.publish(Event(EventType.USER_TEXT, {"text": "在吗", "source": "webui"}))
                await bus.publish(Event(EventType.AI_DECISION, {"text": "在的", "source": "dialogue", "streamed": True}))
                await asyncio.sleep(0.3)
            finally:
                bus.stop()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        asyncio.run(go())
        self.assertEqual(self.llm.calls, [], "关掉关系后裁判不得被调用")
        self.assertEqual(list(self.rep._pending_user_queue), [], "关掉关系后不得进配对队列")
        st = self.rep._mgr.get("danya")
        self.assertEqual(int(st.get("turns") or 0), 0, "关掉关系后不得记轮")

        # 单独隔离 **AI 侧**那条守卫：手工塞一句进队列（模拟"关之前就排着的"），
        # 再投一条回复。否则 `_on_user_text` 的守卫会让队列恒空，AI 侧那条早退
        # 永远走不到"有货可配"的处境，删了也不会红（第一版这把刀就是这么没红的）。
        async def go2():
            bus = EventBus()
            rep = rr.RelationshipReporter(bus, _FakeCfg(rel_on=False), enabled=False,
                                          base_dir=self.base, llm=self.llm)
            rep.subscribe()
            rep.start()
            rep._pending_user_queue.append("关掉之前排着的")
            task = asyncio.ensure_future(bus.run())
            try:
                await bus.publish(Event(EventType.AI_DECISION, {"text": "在的", "source": "dialogue", "streamed": True}))
                await asyncio.sleep(0.3)
            finally:
                bus.stop()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        asyncio.run(go2())
        self.assertEqual(self.llm.calls, [], "关掉关系后，AI 侧也不得判定（哪怕队列里有货）")
        self.assertEqual(int(self.rep._mgr.get("danya").get("turns") or 0), 0,
                         "关掉关系后，AI 侧也不得记轮")

    def test_09_judge_raises_still_counts_turn(self):
        """裁判抛异常 → 本轮按规则兜底记一轮，不吞、不炸总线。

        【为什么补】`_judge_and_apply` 里 `except Exception: signals = _heuristic_signals(ut)`
        是一条**兜底支路**，没有任何用例让裁判抛过。探针实测：把 `except Exception`
        收窄成 `except ZeroDivisionError`，让真异常逃出去 —— 全门照样 OK，
        而线上表现是"这一轮悄悄没了"，只剩日志里一句 Task exception。
        """
        class _BoomJudge:
            async def judge(self, ut, persona=None, context=None):
                raise RuntimeError("裁判炸了（自测）")

        self.rep._judge = _BoomJudge()
        # 兜底分支会 `logger.exception` 打一整段栈 —— 那是**预期行为**的证据，
        # 但一段 Traceback 混在绿着的门里会训练人忽略 Traceback（本仓踩过：
        # 「红着的门禁会被训练成噪音」同理）。测试期间把该 logger 压到 CRITICAL，
        # 判据仍然只看 turns —— 分支跑没跑过，由状态说话，不由日志说话。
        import logging
        _lg = logging.getLogger(rr.__name__)
        _old_level = _lg.level
        _lg.setLevel(logging.CRITICAL)

        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "我今天好累", "source": "webui"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "那就早点休息", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
        try:
            asyncio.run(self._run_bus_briefly(go))
        finally:
            _lg.setLevel(_old_level)
        st = self.rep._mgr.get("danya")
        self.assertGreaterEqual(int(st.get("turns") or 0), 1,
                                "裁判异常必须按规则兜底记一轮（否则这一轮悄悄消失）")

    def test_14_failed_bubble_not_counted(self):
        """失败气泡 / 被质量门拦截 / 空文本 → 不算"她真的说了一句"。

        【为什么补】`if not text or d.get("blocked") or d.get("failed"): return` 这条过滤
        零判据。探针实测：把 `or d.get("blocked") or d.get("failed")` 删掉，全门照样 OK ——
        失败气泡会被记成一轮正常回复，把用户那句真话白吃掉（还消耗一次 LLM 判定）。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "在吗", "source": "webui"}))
            await asyncio.sleep(0.05)
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "失败的回复", "source": "dialogue", "failed": True}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "被拦的回复", "source": "dialogue", "blocked": True}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "", "source": "dialogue"}))
            await asyncio.sleep(0.3)
        asyncio.run(self._run_bus_briefly(go))
        self.assertEqual(self.llm.calls, [], "失败/被拦/空文本都不该触发判定")
        self.assertEqual(list(self.rep._pending_user_queue), ["在吗"],
                         "用户那句应留着等真正的回复")
        st = self.rep._mgr.get("danya")
        self.assertEqual(int(st.get("turns") or 0), 0, "失败气泡不该被记成一整轮")

    def test_15_missing_source_falls_back_to_chat(self):
        """事件与 data 都不带 source → 按 chat 处理（否则这条链路永远不判定）。

        【为什么补】`src = str(d.get("source") or e.source or "chat")` 的回退零判据。
        探针实测：改成 `str(d.get("source") or "")`，全门照样 OK —— 而所有"不填 source"
        的投递方（旧路径 / 第三方桥）会静默地一句话都不进关系。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "无来源的一句话"}))
            await self.bus.publish(Event(EventType.AI_DECISION, {"text": "无来源的回复", "streamed": True}))
            await asyncio.sleep(0.4)
        asyncio.run(self._run_bus_briefly(go))
        st = self.rep._mgr.get("danya")
        self.assertGreaterEqual(int(st.get("turns") or 0), 1,
                                "缺省 source 必须回退成 chat，否则这条链路永远不判定")
        self.assertTrue(self.llm.calls, "缺省 source 也应触发裁判")

    def test_16_duplex_user_text_judged_directly(self):
        """双工语音里的用户话（source=duplex）→ **当场判**，且不进聊天配对队列。

        【为什么补】`if src == "duplex": self._spawn_judge(...); return` 这条支路零判据。
        探针实测：改成 `if False:`，全门照样 OK —— 语音通话里的用户话会退化成
        "进队列等一条永远不会来的聊天回复"，等于**打电话那一半用户完全不计入关系**。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "语音里说的话", "source": "duplex"}))
            await asyncio.sleep(0.4)   # 故意不发 AI 回复：双工路径不该依赖它
        asyncio.run(self._run_bus_briefly(go))
        self.assertEqual(list(self.rep._pending_user_queue), [],
                         "duplex 用户话不该进聊天配对队列（会污染后续 chat 错配）")
        self.assertTrue(self.llm.calls, "duplex 用户话必须当场判定")
        st = self.rep._mgr.get("danya")
        self.assertGreaterEqual(int(st.get("turns") or 0), 1,
                                "打电话说的话必须计入关系（否则语音用户被无视）")

    def test_17_chat_proactive_keeps_user_queue(self):
        """聊天页的主动发言（source=chat + is_proactive）→ 记一次 initiative，
        但**不清空**用户队列、也不触发判定。

        【为什么补】`if d.get("is_proactive")` 这条支路此前零判据：test_03 用的是
        `source="proactive"`（双工自言自语），它在**更早**的 `_CHAT_SOURCES` 过滤处
        就返回了，根本走不到这里。探针实测：在这条支路里加一句
        `self._pending_user_queue.clear()`，全门照样 OK —— 而线上表现是
        "她主动说一句，用户刚才那句真话就被吞了"。
        """
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "我回来了", "source": "webui"}))
            await asyncio.sleep(0.05)
            await self.bus.publish(Event(EventType.AI_DECISION,
                                         {"text": "你回来啦", "source": "chat", "is_proactive": True}))
            await asyncio.sleep(0.3)
        asyncio.run(self._run_bus_briefly(go))
        st = self.rep._mgr.get("danya")
        self.assertTrue(st.get("pending_response"), "主动发言应记一次 initiative（等用户是否回应）")
        self.assertEqual(list(self.rep._pending_user_queue), ["我回来了"],
                         "主动发言不得清空用户队列（否则用户那句永远配不上正式回复）")
        self.assertEqual(self.llm.calls, [], "主动发言不是对用户那句的回复，不该触发判定")


class TestStoreConsistency(unittest.TestCase):
    """落盘一致性：on_turn / mark_response 的 load→save 时序。"""

    def setUp(self):
        rel._MGR = None
        self.base = _mk_tmp_base()

    def tearDown(self):
        rel._MGR = None
        shutil.rmtree(self.base, ignore_errors=True)

    def test_10_marks_then_turn_no_lost_write(self):
        """mark_response 与 on_turn 连做，不应互相覆盖丢字段。"""
        mgr = rel.RelationshipManager(self.base)
        mgr.mark_initiative("danya")           # pending=True
        was = mgr.mark_response("danya")       # pending→False
        self.assertTrue(was)
        st = mgr.on_turn("danya", {"valence": 0.5, "user_text": "hi"})
        st2 = mgr.get("danya")
        self.assertFalse(st2.get("pending_response"))
        self.assertGreaterEqual(int(st2.get("turns") or 0), 1)

    def test_11_singleton_shared_between_calls(self):
        """get_relationship_manager 必须返回同一实例（避免双实例竞写）。"""
        a = rel.get_relationship_manager(self.base)
        b = rel.get_relationship_manager(self.base)
        self.assertIs(a, b)

    def test_12_state_file_isolated_per_char(self):
        mgr = rel.RelationshipManager(self.base)
        mgr.on_turn("a", {"valence": 0.9, "user_text": "A"})
        mgr.on_turn("b", {"valence": 0.1, "user_text": "B"})
        sa, sb = mgr.get("a"), mgr.get("b")
        self.assertNotEqual(sa["affinity"]["warmth"], sb["affinity"]["warmth"])


class TestAsyncJudgeNonBlocking(unittest.TestCase):
    """判定异步性：不阻塞事件总线。"""

    def setUp(self):
        rel._MGR = None
        self.base = _mk_tmp_base()

    def tearDown(self):
        rel._MGR = None
        shutil.rmtree(self.base, ignore_errors=True)

    def test_20_judge_does_not_block_bus(self):
        """判定跑在后台任务里：裁判耗时 1.5s 时，**后续事件**不得被拖住。

        【2026-11-10 修·这条测试量的是两次 `queue.put` 的耗时】原实现从头到尾
        **没有 `bus.run()`** —— 事件只进了队列，一个 handler 都没执行，
        SlowLLM 那 1.5s 根本没被等过，`dt < 0.5` 恒真。
        探针实测：把 `self._spawn_judge(...)` 换成 `await self._judge_and_apply(...)`
        （判定同步跑在 handler 里，会把整条总线堵 1.5s），本测试**照样绿**。

        改成量真正该量的事：总线在跑，发一条 AI 回复后紧跟一条用户话，
        看**后一条**多久被处理 —— 判定异步时是毫秒级，同步时就是 1.5s。
        """
        class SlowLLM:
            async def chat(self, messages, **kw):
                await asyncio.sleep(1.5)
                return '{"valence":0.5,"sufficient":true,"confidence":1.0}'

        async def go():
            bus = EventBus()
            rep = rr.RelationshipReporter(bus, _FakeCfg(), enabled=True,
                                          base_dir=self.base, llm=SlowLLM())
            rep.subscribe()
            rep.start()
            seen = []

            async def probe(e):
                seen.append(time.time())

            task = asyncio.ensure_future(bus.run())
            try:
                await bus.publish(Event(EventType.USER_TEXT, {"text": "hi", "source": "webui"}))
                await asyncio.sleep(0.05)   # 先让这条被消费掉，探针只量"之后"的
                bus.subscribe(EventType.USER_TEXT, probe)
                t0 = time.time()
                await bus.publish(Event(EventType.AI_DECISION, {"text": "yo", "source": "dialogue", "streamed": True}))
                await bus.publish(Event(EventType.USER_TEXT, {"text": "后一条", "source": "webui"}))
                for _ in range(60):
                    if seen:
                        break
                    await asyncio.sleep(0.05)
                dt = (seen[0] - t0) if seen else 99.0
                self.assertTrue(seen, "后一条用户话根本没被处理（总线没跑起来）")
                self.assertLess(dt, 0.5, "判定必须异步：后一条事件被裁判堵了 %.2fs" % dt)
                await asyncio.sleep(1.6)    # 让判定完成，避免退出告警
            finally:
                bus.stop()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        asyncio.run(go())


class TestWriteRace(unittest.TestCase):
    """🔴 丢更新竞态回归：多写者并发 load→save 不得互相覆盖。"""

    def setUp(self):
        rel._MGR = None
        self.base = _mk_tmp_base()

    def tearDown(self):
        rel._MGR = None
        shutil.rmtree(self.base, ignore_errors=True)

    def test_30_concurrent_set_archetype_and_on_turn(self):
        """archetype 与 on_turn 并发：两者都必须保住（此前必丢一个）。"""
        import threading
        mgr = rel.RelationshipManager(self.base)
        # 预置：archetype 空，turns 0
        N = 40
        errs = []

        def writer_archetype():
            for _ in range(N):
                try:
                    mgr.set_archetype("danya", "傲娇", inferred=True)
                except Exception as e:
                    errs.append(("arch", e))

        def writer_turn():
            for i in range(N):
                try:
                    mgr.on_turn("danya", {"valence": 0.5, "user_text": f"hi{i}"})
                except Exception as e:
                    errs.append(("turn", e))

        ts = [threading.Thread(target=writer_archetype), threading.Thread(target=writer_turn)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        st = mgr.get("danya")
        self.assertFalse(errs, f"并发写不应抛异常：{errs}")
        self.assertEqual(st.get("archetype"), "傲娇", "archetype 不应被 on_turn 覆盖丢")
        # on_turn 每轮 turns+1；并发下不能丢太多（有锁应收敛到接近 N）
        self.assertGreaterEqual(int(st.get("turns") or 0), N, "turns 不应因 archetype 写入而丢")

    def test_31_many_writers_no_lost_fields(self):
        """三种写者（on_turn / record_proactive / set_setting）并发，字段互不覆盖。"""
        import threading
        mgr = rel.RelationshipManager(self.base)
        N = 30

        def w_turn():
            for i in range(N):
                mgr.on_turn("danya", {"valence": 0.3, "user_text": f"t{i}"})

        def w_pro():
            for _ in range(N):
                mgr.record_proactive("danya")

        def w_set():
            for _ in range(N):
                mgr.set_setting("danya", "pace", "浓郁")

        ts = [threading.Thread(target=f) for f in (w_turn, w_pro, w_set)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        st = mgr.get("danya")
        self.assertGreaterEqual(int(st.get("turns") or 0), N, "turns 不该丢")
        self.assertEqual((st.get("settings") or {}).get("pace"), "浓郁", "设置不该丢")
        self.assertGreaterEqual(int((st.get("daily_proactive") or {}).get("count") or 0), N,
                                "主动计数不该丢")


if __name__ == "__main__":
    unittest.main(verbosity=2)
