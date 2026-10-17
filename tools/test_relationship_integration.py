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
        """同一句 AI 文本 5s 内重复投递 → 只记一次。"""
        async def go():
            await self.bus.publish(Event(EventType.USER_TEXT, {"text": "你好", "source": "webui"}))
            for _ in range(3):
                await self.bus.publish(Event(EventType.AI_DECISION, {"text": "同一句回复", "source": "dialogue", "streamed": True}))
            await asyncio.sleep(0.4)
        asyncio.run(self._run_bus_briefly(go))
        st = self.rep._mgr.get("danya")
        self.assertLessEqual(int(st.get("turns") or 0), 1, "重复 AI 文本只应记一轮")

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
                tail = joined.split(marker, 1)[1]
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

    def test_20_publish_returns_before_judge_done(self):
        """publish(AI_DECISION) 不应等 LLM 裁判跑完才返回。"""
        class SlowLLM:
            async def chat(self, messages, **kw):
                await asyncio.sleep(1.5)
                return '{"valence":0.5,"sufficient":true,"confidence":1.0}'

        async def go():
            bus = EventBus()
            rep = rr.RelationshipReporter(bus, _FakeCfg(), enabled=True,
                                          base_dir=self.base, llm=SlowLLM())
            rep.subscribe(); rep.start()
            await bus.publish(Event(EventType.USER_TEXT, {"text": "hi", "source": "webui"}))
            t0 = time.time()
            await bus.publish(Event(EventType.AI_DECISION, {"text": "yo", "source": "dialogue", "streamed": True}))
            dt = time.time() - t0
            self.assertLess(dt, 0.5, f"publish 不应阻塞等裁判（实测 {dt:.2f}s）")
            await asyncio.sleep(2.0)  # 让判定完成，避免退出告警
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
