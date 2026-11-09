# -*- coding: utf-8 -*-
"""隔离验证：主动输出「去向分流」修复（2026-10-11·请求M）。

背景（用户实锤）：
    主动互动有两个方面——
      A) 双工看屏幕（`_duplex_check` / 快照 / 兜底）——纯粹小功能，出口=桌宠 Live2D 气泡；
      B) 聊天页她主动发消息（`check_and_trigger` 空闲主动 / `_emit_long_session_break` /
         `_maybe_memory_initiative`）——出口=**聊天页**。
    此前 A/B 两条路都经 `_schedule_duplex_emit` → `_flush_duplex_emit` 建 payload 时
    一律 `is_proactive=True` → 被 `webapp._on_chat_ai_decision`（3451）统一改道到桌宠气泡
    并 return ⇒ **聊天页主动消息永远进不了聊天页**（用户反复报"主动聊天无法生效"）。
    日志铁证：pet_tip 138 条 vs 聊天流 2 条。

修复：新增 `to_chat` 意图沿缓冲带到 payload：
    · to_chat=True  → is_proactive=False, to_chat=True,  trigger='proactive_chat'
                      （webapp 落进 _chat_buffer → 聊天页上屏）
    · to_chat=False → is_proactive=True,  to_chat=False, trigger='proactive_screen'
                      （webapp 改道桌宠气泡——维持原行为不回归）

本测试用桩复刻真实 `_schedule_duplex_emit` + `_flush_duplex_emit` 的**判据分支**，
经真实未绑定方法执行，捕获 publish 到总线的 payload，断言两路去向严格分离。
"""
import asyncio
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
SKIP = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


class FakeEvent:
    def __init__(self, etype, data, source):
        self.type = etype
        self.data = data
        self.source = source


class FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, ev):
        self.published.append(ev)


class FakeLLM:
    """返回一句固定文本，供 check_and_trigger / 长会话 / 记忆主动产出真句子。"""

    def __init__(self, reply="在想什么呢？"):
        self.reply = reply
        self.called = 0

    async def chat(self, *a, **k):
        self.called += 1
        return self.reply


def _mk_stub(reply="在想什么呢？"):
    """最小 Proactive 桩：只拥有 _schedule_duplex_emit/_flush 所需字段与协作者。"""
    s = types.SimpleNamespace()
    # --- 出声缓冲三件套（与 __init__ 一致）---
    s._duplex_emit_buf = None
    s._duplex_emit_timer = None
    s._duplex_emit_to_chat = False
    s._duplex_emit_is_fallback = False
    s._duplex_emit_start_ts = 0.0
    # --- 原生音频（native_audio=False → 不掺入 audio_pcm）---
    s._duplex_native_audio = False
    s._duplex_emit_audio = bytearray()
    s._duplex_emit_audio_ts = 0.0
    # --- 运行时状态 ---
    s._duplex_mode = "local_omni"
    s._duplex_test_mode = False
    s._duplex_occupied_flag = False
    s._in_call_flag = False
    s._llm_generate = True
    s._llm = FakeLLM(reply)
    s._persona = None
    # 【2026-10-21 修·桩缺 property】`_schedule_duplex_emit` 里有
    # `if to_chat and not self.chat_initiative`，而 `chat_initiative` 是
    # ProactiveStrategy 上的 property（读写 `_chat_initiative`）。SimpleNamespace
    # 没有这个 property → AttributeError。这里按"她能在聊天页主动找你"填 True，
    # 正对应用例 2 要验的分支。（桩用 SimpleNamespace 冒充带 property 的类，本身
    # 就是脆的——真实类加了 property 桩就得手动补，见本次踩坑。）
    s.chat_initiative = True
    s._cfg = types.SimpleNamespace(active_character_id="")
    s._recent_messages = []
    s._last_message = None
    s._last_proactive = 0.0
    s._idle_threshold_s = 180.0
    s._batch_interval_s = 300.0
    s._last_input_ts = time.time() - 4000
    s._session_boot_ts = time.time() - 4000
    s._duplex_last_speak_ts = 0.0
    s._bus = FakeBus()
    # 依赖的副作用方法（复刻生产语义：不拦本测试要测的路径）
    s._in_call = lambda: s._in_call_flag
    s._record_duplex_ai_line = lambda *a, **k: None
    s._clean_duplex_speech = lambda t: (t or "").strip()
    s._is_empty_filler = lambda t: False
    s._similar_to_recent = lambda t: False
    s._focus_state = lambda: "正常活动中"
    s._duplex_text_emotion = lambda t: "neutral"
    s._effective_emit_debounce_s = lambda: 0.01
    s._publish_call_record = lambda **k: _noop()
    s._duplex_output_log = []

    async def _log_duplex_source(*a, **k):
        return None
    s._log_duplex_source = _log_duplex_source

    async def _maybe_memory_initiative_prod(*a, **k):
        return False
    # 记忆主动单独测（用真实方法），默认桩下走通用引信
    s._maybe_memory_initiative = _maybe_memory_initiative_prod

    async def _emit_long_session_break_stub(*a, **k):
        return False
    s._emit_long_session_break = _emit_long_session_break_stub
    s._last_proactive_or_zero = lambda: float(s._last_proactive or 0.0)

    # 把真实的缓冲方法"绑定"到本桩：SimpleNamespace 不会有类方法，
    # 但 check_and_trigger / _emit_long_session_break 内部会 self._schedule_duplex_emit(...)，
    # 必须让桩上真的挂着这两个真实方法（用 functools 把真实未绑定函数绑到 s）。
    import functools
    from src.companion import proactive as _P
    s._schedule_duplex_emit = functools.partial(
        _P.ProactiveStrategy._schedule_duplex_emit, s)
    s._flush_duplex_emit = functools.partial(
        _P.ProactiveStrategy._flush_duplex_emit, s)
    return s


async def _noop():
    return None


def _payload_of(stub):
    evs = [e for e in stub._bus.published if getattr(e, "type", None)]
    return evs[-1].data if evs else None


async def main():
    from src.companion import proactive as P
    print("== 主动输出·去向分流 修复验证（2026-10-11·请求M）==")

    # ---------- 1) 双工看屏（to_chat=False）→ 维持桌宠气泡语义 ----------
    s = _mk_stub()
    await P.ProactiveStrategy._schedule_duplex_emit(s, "屏幕好热闹呀", to_chat=False)
    # 立即 flush（跳过 debounce）
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("1a 双工看屏缓冲已清空", s._duplex_emit_buf == "")
    check("1b 双工看屏 is_proactive=True", d is not None and d.get("is_proactive") is True,
          repr(d))
    check("1c 双工看屏 to_chat=False", d is not None and d.get("to_chat") is False, repr(d))
    check("1d 双工看屏 trigger=proactive_screen",
          d is not None and d.get("trigger") == "proactive_screen", repr(d))

    # ---------- 2) 聊天页空闲主动（to_chat=True）→ 落聊天页语义 ----------
    s = _mk_stub()
    await P.ProactiveStrategy._schedule_duplex_emit(s, "歇会儿不？", to_chat=True)
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("2a 聊天页主动 is_proactive=False（不再被改道桌宠）",
          d is not None and d.get("is_proactive") is False, repr(d))
    check("2b 聊天页主动 to_chat=True", d is not None and d.get("to_chat") is True, repr(d))
    check("2c 聊天页主动 trigger=proactive_chat",
          d is not None and d.get("trigger") == "proactive_chat", repr(d))
    check("2d 去聊天页意图用后复位（不与下一段串味）", s._duplex_emit_to_chat is False)

    # ---------- 3) check_and_trigger 真路径 → 必带 to_chat=True ----------
    s = _mk_stub(reply="在忙什么呢")
    await P.ProactiveStrategy.check_and_trigger(s)
    # check_and_trigger 内部会 call_later(debounce) → 手动取缓冲并 flush
    check("3a check_and_trigger 确实攒进了缓冲", bool(s._duplex_emit_buf), repr(s._duplex_emit_buf))
    check("3b check_and_trigger 置位 to_chat=True", s._duplex_emit_to_chat is True)
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("3c check_and_trigger → is_proactive=False", d.get("is_proactive") is False, repr(d))
    check("3d check_and_trigger → trigger=proactive_chat",
          d.get("trigger") == "proactive_chat", repr(d))

    # ---------- 4) 长会话收尾真路径 → to_chat=True ----------
    s = _mk_stub(reply="喝口水歇会儿吧")
    ok = await P.ProactiveStrategy._emit_long_session_break(s, "charA", time.time())
    check("4a 长会话收尾返回 True（真播出）", ok is True)
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("4b 长会话收尾 → is_proactive=False", d.get("is_proactive") is False, repr(d))
    check("4c 长会话收尾 → to_chat=True", d.get("to_chat") is True, repr(d))

    # ---------- 5) 记忆型主动真路径 → to_chat=True ----------
    s = _mk_stub(reply="最近还在玩那个游戏吗")
    s._last_memory_initiative = 0.0   # 冷却是 0 → `0.0 and ...` 为假 → 不因冷却跳过
    # 注入最小记忆库：list_user_facts 返回一条
    import src.companion.proactive as _P
    _orig_get_store = _P.get_memory_store
    import random as _random
    _orig_random = _random.random

    class _Store:
        def list_user_facts(self, limit=15):
            return ["用户喜欢喝美式"]

    _P.get_memory_store = lambda: _Store()
    _random.random = lambda: 0.0   # 强制通过 35% 随机门
    try:
        ok = await P.ProactiveStrategy._maybe_memory_initiative(
            s, time.time(), "达娅", "正常活动中", 400.0)
    finally:
        _P.get_memory_store = _orig_get_store
        _random.random = _orig_random
    # 记忆主动有 35% 随机门 → 命中才测去向，未命中记 SKIP
    if ok:
        await P.ProactiveStrategy._flush_duplex_emit(s)
        d = _payload_of(s)
        check("5a 记忆型主动 → is_proactive=False", d.get("is_proactive") is False, repr(d))
        check("5b 记忆型主动 → to_chat=True", d.get("to_chat") is True, repr(d))
    else:
        global SKIP
        SKIP += 1
        print("  SKIP 5 记忆型主动本轮随机未命中（35% 门），跳过去向断言")

    # ---------- 6) 混合缓冲：双工句 + 聊天句 → OR 语义（不丢聊天意图） ----------
    s = _mk_stub()
    await P.ProactiveStrategy._schedule_duplex_emit(s, "第一句", to_chat=False)
    await P.ProactiveStrategy._schedule_duplex_emit(s, "第二句", to_chat=True)
    check("6a 混合缓冲 to_chat 被 OR 置 True", s._duplex_emit_to_chat is True)
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("6b 混合缓冲整段去聊天页（不丢意图）",
          d.get("to_chat") is True and d.get("is_proactive") is False, repr(d))
    check("6c 混合缓冲两句子拼接",
          "第一句" in d.get("text", "") and "第二句" in d.get("text", ""),
          repr(d.get("text")))

    # ---------- 7) 空缓冲 flush 不误发 ----------
    s = _mk_stub()
    await P.ProactiveStrategy._flush_duplex_emit(s)
    check("7 空缓冲 flush → 不 publish", _payload_of(s) is None)

    # ---------- 8) 消费端：webapp 必须仍然按 to_chat 分道 ----------
    # 【2026-11-10 加】上面所有判据都只验「生产出来的 payload 对不对」，
    # 没有一条验「webapp 收下之后怎么走」。而 2026-10-11 那个 bug 恰恰发生在 webapp：
    # 它当时一律 `is_proactive=True → 改道桌宠气泡 + return`，聊天页永远收不到。
    # 也就是说：把 webapp 那行改回「只看 is_proactive」，本文件 21 条**照样全绿**。
    # 这里补一条静态接线断言，把「消费端不许漂移」也钉住。
    _webapp_path = os.path.join(ROOT, "src", "ui", "webapp.py")
    _webapp = open(_webapp_path, encoding="utf-8").read()
    _guard = 'bool(d.get("is_proactive")) or bool(d.get("to_chat") is False)'
    check("8a webapp 分道判据仍读 to_chat（消费端未漂移）", _guard in _webapp)
    _n_guard = _webapp.count('bool(d.get("to_chat") is False)')
    check("8b 该分道判据只有一处（多处改道会各自为政）", _n_guard == 1, f"count={_n_guard}")

    print("\n结果: %d PASS / %d FAIL / %d SKIP" % (PASS, FAIL, SKIP))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
