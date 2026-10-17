"""Agent 执行状态气泡 + 失败原因详细化（2026-10-02）。

覆盖：
 1. error_classify.human_reason：额度耗尽/Key 无效/限流/超时各有用户可读原因；
 2. dialogue._fail_text：LLM 异常 → 带原因与建议的失败气泡文案；
 3. webapp._on_agent_status：AGENT_STATUS 事件 → 聊天缓冲单条状态气泡（复用不刷屏）；
 4. webapp._drop_chat_status：真实回复/清空聊天时移除状态气泡；
 5. openai_client.chat_with_tools 接受 on_status（签名兼容）；
 6. text_chat_router.chat_with_tools 透传 on_status（签名兼容）。

不需要真实网络/LLM：直接构造最小对象调方法。
"""

import asyncio
import inspect
import sys
import time
import types
import unittest

_ollama = types.ModuleType("ollama")
_ollama.AsyncClient = object
_ollama.Client = object
sys.modules.setdefault("ollama", _ollama)
sys.path.insert(0, ".")

from src.event_bus import Event, EventType  # noqa: E402
from src.llm.error_classify import (  # noqa: E402
    KIND_AUTH, KIND_OTHER, KIND_QUOTA, KIND_RATE, KIND_TIMEOUT,
    classify_error, human_reason, is_quota_exhausted,
)

QUOTA_ERR = ("OpenAI 兼容接口调用失败: 403 [服务端: AllocationQuota.FreeTierOnly "
             "Free quota exhausted. To continue accessing the model on a paid basis]")


def _webapp_shim():
    """构造最小 webapp 对象（不走 __init__ 重依赖），只填状态气泡用到的东西。"""
    from src.ui.webapp import CharacterAPI as WebApp  # noqa: PLC0415
    wa = WebApp.__new__(WebApp)
    wa._chat_buffer = []
    wa._chat_seq = 0
    wa._chat_status_id = None
    wa._chatlog = None          # 【2026-10-10 补】clear_chat 现会读 chatlog（落盘为空则跳过）
    wa._dialogue = None
    wa._next_chat_id = lambda: (setattr(wa, "_chat_seq", wa._chat_seq + 1),
                                wa._chat_seq)[1]
    return wa


class TestErrorClassify(unittest.TestCase):
    def test_quota(self):
        self.assertEqual(classify_error(QUOTA_ERR), KIND_QUOTA)
        reason, hint = human_reason(QUOTA_ERR)
        self.assertIn("额度", reason)
        self.assertIn("AllocationQuota.FreeTierOnly", reason)
        self.assertTrue(hint)

    def test_auth_rate_timeout(self):
        self.assertEqual(classify_error("401 Unauthorized: invalid api key"), KIND_AUTH)
        self.assertEqual(classify_error("429 Too Many Requests"), KIND_RATE)
        self.assertEqual(classify_error("Connection timeout"), KIND_TIMEOUT)
        self.assertEqual(classify_error("xxx"), KIND_OTHER)
        # 每类都有用户可读原因
        for kind in (KIND_AUTH, KIND_RATE, KIND_TIMEOUT, KIND_OTHER):
            reason, hint = human_reason({"auth": "401", "rate": "429",
                                         "timeout": "timeout", "other": "zzz"}[kind])
            self.assertTrue(reason and hint)


class TestFailText(unittest.TestCase):
    def test_fail_text_quota(self):
        from src.companion.dialogue import Dialogue
        text = Dialogue._fail_text(RuntimeError(QUOTA_ERR))
        self.assertIn("消息发送失败", text)
        self.assertIn("额度", text)
        self.assertIn("控制台", text)

    def test_fail_text_never_raises(self):
        from src.companion.dialogue import Dialogue
        self.assertTrue(Dialogue._fail_text(RuntimeError("")))
        self.assertTrue(Dialogue._fail_text(None))


class TestAgentStatusBubble(unittest.TestCase):
    def test_status_bubble_reused(self):
        wa = _webapp_shim()
        ev = Event(EventType.AGENT_STATUS, {"text": "正在执行：play_song(汽水音乐, 晴天)"})
        asyncio.run(wa._on_agent_status(ev))
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {"text": "操作完成，正在组织回复…"})))
        self.assertEqual(len(wa._chat_buffer), 1, "状态气泡单条复用，不刷屏")
        m = wa._chat_buffer[0]
        self.assertEqual(m["kind"], "status")
        self.assertIn("操作完成", m["text"])
        self.assertIn("⚙️", m["text"])
        self.assertIsNotNone(wa._chat_status_id)

    def test_status_dropped_on_real_reply(self):
        """真实回复到达 → 状态卡被接管。

        【2026-10-05 语义变更·二】① 最小存活期（_AGENT_CARD_MIN_LIFE）：短操作不再
        "出生即死亡"；② **已完成的卡转持久历史卡**（kind=status_done）而不是消失
        —— 用户 04:17 实锤"刷新界面后卡片不见了"。本例的卡**未完成**（没有 done），
        属异常中断 ⇒ 仍真删除。
        """
        wa = _webapp_shim()
        wa._AGENT_CARD_MIN_LIFE = 0.25          # 测试不等 2.4s，缩短存活期
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {"text": "正在执行：open_app"})))
        self.assertEqual(len(wa._chat_buffer), 1)
        wa._drop_chat_status()
        # 存活期内：卡片仍在（前端才有时间演完"执行中→打勾→完成"）
        self.assertEqual(len(wa._chat_buffer), 1, "最小存活期内不应被立即删除")
        self.assertIsNotNone(wa._chat_status_drop_timer, "应已排延迟删除定时器")
        time.sleep(0.4)
        self.assertEqual(len(wa._chat_buffer), 0, "存活期满后未完成卡应被移除")
        self.assertIsNone(wa._chat_status_id)
        # 幂等
        wa._drop_chat_status()

    def test_done_card_becomes_history(self):
        """【2026-10-05 新增】已完成的卡 → 转持久历史卡（刷新不丢），而非删除。"""
        wa = _webapp_shim()
        wa._AGENT_CARD_MIN_LIFE = 0.05
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {
            "phase": "step", "step": 1, "total": 1, "tool": "open_app",
            "text": "步骤 1/1 · 正在执行：open_app"})))
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {
            "phase": "done", "done": True, "text": "操作完成，正在组织回复…"})))
        self.assertEqual(len(wa._chat_buffer), 1)
        wa._drop_chat_status()
        time.sleep(0.2)
        # 关键：卡还在，且变成历史卡
        self.assertEqual(len(wa._chat_buffer), 1, "已完成的卡不应被删除（刷新要能看见）")
        m = wa._chat_buffer[0]
        self.assertEqual(m.get("kind"), "status_done", "应转为历史卡")
        self.assertTrue((m.get("text") or "").startswith("✓"), "应有一行摘要")
        self.assertIsNotNone(m.get("ts"), "保留 ts 以维持排序位置")
        # 清空聊天时才是真删除
        wa.clear_chat()
        self.assertEqual(len(wa._chat_buffer), 0)

    def test_status_dropped_forced(self):
        """force=True → 无条件真删除（清空聊天/测试用）。"""
        wa = _webapp_shim()
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {
            "phase": "step", "step": 1, "total": 1, "tool": "open_app",
            "text": "步骤 1/1 · 正在执行：open_app"})))
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {
            "phase": "done", "done": True, "text": "操作完成"})))
        self.assertEqual(len(wa._chat_buffer), 1)
        wa._drop_chat_status(force=True)
        self.assertEqual(len(wa._chat_buffer), 0, "force=True 必须真删除")
        self.assertIsNone(wa._chat_status_id)

    def test_empty_status_ignored(self):
        wa = _webapp_shim()
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {"text": "  "})))
        self.assertEqual(len(wa._chat_buffer), 0)

    def test_clear_chat_resets_status(self):
        wa = _webapp_shim()
        asyncio.run(wa._on_agent_status(Event(EventType.AGENT_STATUS, {"text": "x"})))
        wa.clear_chat()
        self.assertIsNone(wa._chat_status_id)


class TestSignatures(unittest.TestCase):
    def test_openai_chat_with_tools_accepts_on_status(self):
        from src.llm.openai_client import OpenAIClient
        params = inspect.signature(OpenAIClient.chat_with_tools).parameters
        self.assertIn("on_status", params)

    def test_router_chat_with_tools_accepts_on_status(self):
        from src.llm.text_chat_router import TextChatRouter
        params = inspect.signature(TextChatRouter.chat_with_tools).parameters
        self.assertIn("on_status", params)

    def test_agent_status_event_exists(self):
        self.assertEqual(EventType.AGENT_STATUS.value, "agent_status")


class TestJsApiGuard(unittest.TestCase):
    """js_api 序列化守卫：不可序列化返回 → 降级错误响应 + 日志带方法名。"""

    def _load(self):
        from src.ui.webapp import _safe_js_api
        return _safe_js_api

    def test_normal_passthrough(self):
        s = self._load()

        class D:
            def ok(self):
                return {"success": True, "n": 1}

        self.assertEqual(s(D(), "T").ok(), {"success": True, "n": 1})

    def test_unserializable_degraded(self):
        s = self._load()

        class D:
            def bad(self):
                return {"fn": self.bad}

        r = s(D(), "T").bad()
        self.assertFalse(r.get("success"))
        self.assertTrue(r.get("error"))

    def test_async_ok_and_state_copied(self):
        import asyncio
        s = self._load()

        class D:
            def __init__(self):
                self.x = 42

            async def a(self):
                return {"success": True}

        w = s(D(), "T")
        self.assertEqual(asyncio.run(w.a()), {"success": True})
        self.assertEqual(w.x, 42)




class _GuardDemo:
    def ok(self):
        return {"success": True, "n": 1}

    def bad(self):
        # 误把绑定方法当数据返回（复现 pywebview "Object of type method is not JSON serializable"）
        return {"success": True, "fn": self.ok}

    async def aok(self):
        return {"success": True}

    def boom(self):
        raise RuntimeError("kaboom")


class TestSafeJsApiGuard(unittest.TestCase):
    def test_sync_bad_downgraded_with_method_name(self):
        from src.ui.webapp import _safe_js_api  # noqa: PLC0415
        g = _safe_js_api(_GuardDemo(), "GuardDemo")
        r = g.bad()
        self.assertFalse(r.get("success"))
        self.assertIn("GuardDemo.bad", r.get("error", ""))  # 日志会点名方法
        self.assertIn("not JSON serializable", r.get("error", ""))

    def test_sync_ok_passthrough(self):
        from src.ui.webapp import _safe_js_api  # noqa: PLC0415
        g = _safe_js_api(_GuardDemo(), "GuardDemo")
        self.assertEqual(g.ok(), {"success": True, "n": 1})

    def test_async_ok_passthrough(self):
        from src.ui.webapp import _safe_js_api  # noqa: PLC0415
        import asyncio  # noqa: PLC0415
        g = _safe_js_api(_GuardDemo(), "GuardDemo")
        self.assertEqual(asyncio.run(g.aok()), {"success": True})

    def test_exception_propagates(self):
        from src.ui.webapp import _safe_js_api  # noqa: PLC0415
        g = _safe_js_api(_GuardDemo(), "GuardDemo")
        with self.assertRaises(RuntimeError):
            g.boom()


if __name__ == "__main__":
    unittest.main(verbosity=2)
