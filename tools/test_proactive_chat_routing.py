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

【2026-11-11 判据大修（第 45 轮变异探针，43 把刀捅出 32 处零判据）】

  ① **`SKIP` 在返回值里算通过**（最刺眼的一处）。第 5 组「记忆型主动」命中不了就
     `SKIP += 1`，而 `return 0 if FAIL == 0 else 1` 根本不看 SKIP ⇒ 把
     `_maybe_memory_initiative` 整段改成 `return False`，本门照样绿。
     SKIP 是「本门没有意见」，不是「本门认为没问题」—— 现在计入条数总账。
  ② **桩把整段分支钉死了**。`_mk_stub` 里 `_in_call_flag = False` ⇒
     `_flush_duplex_emit` 后半段 130 行**通话生命周期闸门**（2026-09-23「她不理我」
     那次的全部修复）一次都没跑过；`_duplex_native_audio = False` 同理，
     2026-09-23 的原生音频硬闸整段不可见。补了 20 个桩字段 + 第 11/13 组。
  ③ **只测 payload 不测记账**：`_duplex_emit_start_ts` / `_duplex_emit_is_fallback` /
     `_long_session_warned` / `_mem_init_idx` 一条判据都没有，而这些字段正是
     「下一句」要用的。补第 9/14d/15 组。
  ④ **六道前置闸门只走了 happy path**：通话让位 / LLM 未就绪 / 8s 防打断 /
     `0.0` 当无解 / 空闲阈值 / 批冷却。补第 13 组「一次只破一道」的隔离对照，
     每条都配「放行时真的开口」的反面对照 —— 否则又会出现「判据绿的原因跟它名字无关」。
  ⑤ 一处**等价变异**如实记录：删掉 `if not facts: return False` 没有行为差异
     （`% len(facts)` 会先抛 ZeroDivisionError，被最外层 except 吞掉、照样
     `return False`）。按第 41 轮的规矩不"修"，刀的形态改成「无记忆时编一条事实」。

判据口径提醒：`check_and_trigger` 内部用 `call_later(0.01)` 起 debounce，
用「缓冲是否为空」当判据会跟定时器抢时序；第 13 组一律看 `_last_message`
（只有真开口时才置上），对时序免疫。
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

# 【2026-11-11 第 45 轮】条数总账：跑过的都过了，不等于该跑的都跑了。
EXPECTED_CHECKS = 67


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


def _finish():
    """汇总 + 条数总账。

    【2026-11-11 第 45 轮】这里原来写的是 `return 0 if FAIL == 0 else 1`，
    **`SKIP` 根本不算数** —— 而第 5 组「记忆型主动」命中不了就 SKIP。
    也就是说：把 `_maybe_memory_initiative` 整段改成 `return False`，
    这道门照样绿。SKIP 是「本门没有意见」，不是「本门认为没问题」。
    现在 SKIP 计入总账，条数对不上就红。
    """
    global FAIL
    ran = PASS + FAIL + SKIP
    if ran != EXPECTED_CHECKS:
        FAIL += 1
        print(f"  FAIL 判据条数 {ran} != 期望 {EXPECTED_CHECKS}"
              f"（有整段被跳过/删掉了 —— 少跑的判据不算通过）")
    print("\n结果: %d PASS / %d FAIL / %d SKIP" % (PASS, FAIL, SKIP))
    return 0 if FAIL == 0 else 1


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
        self.prompts = []      # 【第 45 轮】记忆主动要验「轮着聊不同事实」，得看 prompt

    async def chat(self, *a, **k):
        self.called += 1
        try:
            self.prompts.append(a[0][0].get("content", ""))
        except Exception:  # noqa: BLE001
            self.prompts.append("")
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

    # 【2026-11-11 第 45 轮】下面这批字段原来一个都没有 ⇒ 对应分支在判据上完全不可见：
    #   · 原生音频硬闸（2026-09-23）：`_duplex_native_audio` 恒 False、音频时间戳全无
    #     ⇒ `_flush_duplex_emit` 里那 30 行「随句出声 / 过期丢弃 / 起点回看」一次没跑过。
    #   · 通话生命周期闸门（2026-09-23「她不理我」那次的全部修复）：桩里 `_in_call_flag`
    #     恒 False ⇒ 后面那 130 行（开场白放行 / 系统兜底豁免 / 回应窗口 / 静默暂存待补发）
    #     整段不可见。
    #   · 长会话收尾 / 记忆主动的记账字段。
    # 桩缺字段 = 整段分支在判据上不存在。这不是"桩写得不全"，是"门以为自己测过了"。
    s._duplex_emit_audio_start_ts = 0.0
    s._duplex_hf_eq = False
    s._duplex_last_raw_text = ''
    # 通话生命周期闸门
    s._duplex_opened = False
    s._duplex_cont_used = False
    s._duplex_last_ai_emit_ts = 0.0
    s._duplex_last_ai_emit_text = ''
    s._duplex_last_user_speech_ts = 0.0
    s._duplex_call_selftalk_silence_s = 12.0
    s._duplex_call_continue_s = 8.0
    s._CALL_TALK_BUDGET_S = 20.0
    s._CALL_PENDING_RETRY_S = 3600.0   # 别让 3s 定时器在测试里真去补发
    s._call_pending_text = ''
    s._call_pending_ts = 0.0
    s._call_pending_timer = None
    # 长会话收尾 / 记忆主动的记账
    s._long_session_warned = False
    s._session_start_ts = None
    s._prev_last_input_ts = 0.0
    s._last_memory_initiative = 0.0
    s._mem_init_idx = 0

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
    # 【2026-11-11 第 45 轮】这里原来是「命中才测、未命中 SKIP」，而 `SKIP` 在返回值里
    # 算通过 ⇒ 把 `_maybe_memory_initiative` 整段改成 `return False`，本门照样绿。
    # 上面已经把 `random.random` 钉成 0.0，随机门**必然放行**，所以 ok 必须为 True。
    check("5 记忆型主动在随机门放行时必须真的开口（不是 SKIP 了事）", ok is True,
          "35% 随机门已钉成必中，仍没开口 ⇒ 整条记忆主动路径断了")
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("5a 记忆型主动 → is_proactive=False",
          d is not None and d.get("is_proactive") is False, repr(d))
    check("5b 记忆型主动 → to_chat=True", d is not None and d.get("to_chat") is True, repr(d))

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

    # ======================================================================
    # 以下为 2026-11-11 第 45 轮补（43 把刀探出 32 处零判据）。
    # 成因四类：① SKIP 算通过（已在上面的第 5 组修掉）；② 桩把整段分支钉死
    # （通话生命周期闸门 / 原生音频硬闸）；③ 只测 payload 不测记账；
    # ④ 六道前置闸门只走了 happy path。
    # ======================================================================

    # ---------- 8) 主动互动开关：聊天页主动全丢，看屏那条不受影响（2026-10-17 解耦） ----------
    s = _mk_stub()
    s.chat_initiative = False
    await P.ProactiveStrategy._schedule_duplex_emit(s, "该被丢掉的一句", to_chat=True)
    check("8a 主动互动关闭 → 缓冲里不留聊天页主动句", not s._duplex_emit_buf,
          repr(s._duplex_emit_buf))
    await P.ProactiveStrategy._flush_duplex_emit(s)
    check("8b 主动互动关闭 → 不 publish", _payload_of(s) is None)

    s = _mk_stub()
    s.chat_initiative = False
    await P.ProactiveStrategy._schedule_duplex_emit(s, "屏幕好热闹呀", to_chat=False)
    await P.ProactiveStrategy._flush_duplex_emit(s)
    d = _payload_of(s)
    check("8c 主动互动关闭 → 看屏那条照发（彻底解耦）",
          d is not None and d.get("trigger") == "proactive_screen", repr(d))

    # ---------- 9) 出声缓冲的记账：起点只记一次、兜底标志可被真回应洗掉 ----------
    s = _mk_stub()
    _t9 = time.time()
    await P.ProactiveStrategy._schedule_duplex_emit(s, "第一句", to_chat=False, sys_fallback=True)
    _st9 = s._duplex_emit_start_ts
    check("9a 新段起点被记下（原生音频对齐的基准）", _st9 >= _t9 - 0.01, repr(_st9))
    check("9b 兜底发声被标记为兜底", s._duplex_emit_is_fallback is True,
          repr(s._duplex_emit_is_fallback))
    await P.ProactiveStrategy._schedule_duplex_emit(s, "第二句", to_chat=False)
    check("9c 同段第二句不重置起点（基准是整段起点）",
          s._duplex_emit_start_ts == _st9, repr(s._duplex_emit_start_ts))
    check("9d 真回应到达 → 兜底标志被洗掉", s._duplex_emit_is_fallback is False)

    s = _mk_stub()
    await P.ProactiveStrategy._schedule_duplex_emit(s, "真回应", to_chat=False)
    await P.ProactiveStrategy._schedule_duplex_emit(s, "系统兜底", to_chat=False, sys_fallback=True)
    check("9e 兜底句不把整段降级成兜底（真回应优先）",
          s._duplex_emit_is_fallback is False)

    # ---------- 10) 去聊天页是「任一句」语义：先真后聊也必须整段去聊天页 ----------
    s = _mk_stub()
    await P.ProactiveStrategy._schedule_duplex_emit(s, "看屏那句", to_chat=False)
    await P.ProactiveStrategy._schedule_duplex_emit(s, "聊天页那句", to_chat=True)
    await P.ProactiveStrategy._schedule_duplex_emit(s, "又来一句看屏", to_chat=False)
    check("10a 去聊天页意图不被后续看屏句覆盖（任一句成立即成立）",
          s._duplex_emit_to_chat is True)

    # ---------- 11) 原生音频硬闸（2026-09-23）：只在 native_audio 且对齐时才随句出声 ----------
    async def _flush_audio(native, start_off, age_off):
        s2 = _mk_stub()
        await P.ProactiveStrategy._schedule_duplex_emit(s2, "带原生语音的一句", to_chat=False)
        s2._duplex_native_audio = native
        s2._duplex_emit_audio = bytearray(b"\x01\x02\x03\x04")
        s2._duplex_emit_audio_ts = time.time() - age_off
        s2._duplex_emit_audio_start_ts = s2._duplex_emit_start_ts - start_off
        await P.ProactiveStrategy._flush_duplex_emit(s2)
        return s2, _payload_of(s2)

    s, d = await _flush_audio(True, 0.0, 0.0)
    check("11a native_audio=true 且对齐 → audio_pcm 随句出声",
          d is not None and d.get("audio_pcm") == b"\x01\x02\x03\x04",
          repr(d.get("audio_pcm") if d else None))
    check("11b 出声后音频缓冲被清空（不串到下一句）",
          s._duplex_emit_audio == bytearray(), repr(s._duplex_emit_audio))

    _, d = await _flush_audio(False, 0.0, 0.0)
    check("11c native_audio=false 硬闸 → 绝不漏原生音频（改走 TTS）",
          d is not None and "audio_pcm" not in d,
          repr(d.get("audio_pcm") if d else None))

    _, d = await _flush_audio(True, 0.0, 30.0)
    check("11d 音频过期（>10s）→ 丢弃", d is not None and "audio_pcm" not in d)

    _, d = await _flush_audio(True, 20.0, 0.0)
    check("11e 音频起点早于本句 20s（超 8s 回看窗）→ 丢弃（防串音）",
          d is not None and "audio_pcm" not in d)

    # ---------- 12) 通话中出声要留通话记录 ----------
    s = _mk_stub()
    _recs12 = []
    s._in_call_flag = True
    s._publish_call_record = lambda **k: (_recs12.append(k), _noop())[1]
    await P.ProactiveStrategy._schedule_duplex_emit(s, "通话里的一句", to_chat=False)
    await P.ProactiveStrategy._flush_duplex_emit(s)
    check("12a 通话中出声 → 写入通话记录",
          len(_recs12) == 1 and _recs12[0].get("text") == "通话里的一句", repr(_recs12))

    # ---------- 13) 通话生命周期闸门（2026-09-23「她不理我」那次的整段） ----------
    # 判据看 payload / 记账字段，不看缓冲 —— 避开 debounce 定时器的时序抖动。
    async def _call_emit(text, *, opened, since_user, fallback=False, la=0.0):
        s2 = _mk_stub()
        s2._in_call_flag = True
        s2._duplex_opened = opened
        s2._duplex_last_user_speech_ts = since_user
        s2._duplex_last_ai_emit_ts = la
        await P.ProactiveStrategy._schedule_duplex_emit(s2, text, to_chat=False,
                                                       sys_fallback=fallback)
        await P.ProactiveStrategy._flush_duplex_emit(s2)
        return s2, _payload_of(s2)

    # ① 开场白：本通还没听到用户任何声（_lu=0）→ 放行，否则一接通就死寂
    s, d = await _call_emit("接通了呀", opened=False, since_user=0.0)
    check("13a 通话开场白放行（_lu=0 不算自言自语）", d is not None, repr(d))
    check("13b 开场白置位 _duplex_opened（每通只放一次）", s._duplex_opened is True)

    # ② 用户 60 秒前发过声、已超 12 秒回应窗口 → 判自言自语，暂存不播
    s, d = await _call_emit("她自顾自说的一句", opened=True, since_user=time.time() - 60)
    check("13c 用户静默超窗 → 不出声（防井喷式独白）", d is None, repr(d))
    check("13d 静默句被暂存待补发（不是直接丢）",
          s._call_pending_text == "她自顾自说的一句", repr(s._call_pending_text))

    # ③ 用户 1 秒前发过声 → 判回应，放行并刷新 _la
    s, d = await _call_emit("在呢在呢", opened=True, since_user=time.time() - 1)
    check("13e 用户刚发过声 → 判回应放行", d is not None, repr(d))
    check("13f 回应放行后刷新 _la（供续句判定）", s._duplex_last_ai_emit_ts > 0,
          repr(s._duplex_last_ai_emit_ts))

    # ④ 系统兜底：静默也放行（避免死寂），但不刷新 _la（不挤掉紧随的真回答）
    s, d = await _call_emit("（哑火看门狗）你还在吗", opened=True,
                            since_user=time.time() - 60, fallback=True, la=111.0)
    check("13g 系统兜底在静默中照样出声（不死寂）", d is not None, repr(d))
    check("13h 兜底不刷新 _la（否则挤掉紧随的真回答）",
          s._duplex_last_ai_emit_ts == 111.0, repr(s._duplex_last_ai_emit_ts))
    check("13i 通话中 desktop_companion=False（TTS 不静默）",
          d is not None and d.get("desktop_companion") is False, repr(d))

    # ⑤ check_and_trigger 的六道前置闸门：一次只破一道，其余全部放行。
    #    判据看 `_last_message`（只有真开口时才置上），不受 debounce 时序影响。
    s = _mk_stub(reply="在忙什么呢")
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13j 空闲达标且无闸门 → 真的开口（下面几条的前提）",
          s._last_message == "在忙什么呢", repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢"); s._in_call_flag = True
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13x 通话中不触发空闲主动", s._last_message is None, repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢"); s._llm_generate = False
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13y LLM 未就绪不开口", s._last_message is None, repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢"); s._duplex_last_speak_ts = time.time() - 3
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13z 刚说过话 8s 内不抢话", s._last_message is None, repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢"); s._duplex_last_speak_ts = time.time() - 9
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13z2 过了 8s 就正常开口（13z 的反面对照）",
          s._last_message == "在忙什么呢", repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢")
    s._last_input_ts = 0.0                      # 开机后用户还没打过字
    s._session_boot_ts = time.time() - 4000
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13w 首次打字前按开机时刻起算（0.0 不当无解）",
          s._last_message == "在忙什么呢", repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢"); s._last_input_ts = time.time() - 100
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13v 未达空闲阈值不开口", s._last_message is None, repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢"); s._last_proactive = time.time() - 10
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13u 批冷却内不重复开口", s._last_message is None, repr(s._last_message))

    # 关系闸门：patch 掉 relationship 模块的三个出口，避免真去读写用户数据目录
    import src.companion.relationship as _R

    class _RelStub:
        def get(self, cid):
            return {}

        def record_proactive(self, cid, now):
            pass

        def assemble_relation_block(self, cid, tier="local"):
            return ""

    _orig_rel = (_R.get_relationship_manager, _R._proactive_decision, _R.log_relation_event)
    _R.get_relationship_manager = lambda: _RelStub()
    _R.log_relation_event = lambda *a, **k: None
    try:
        _R._proactive_decision = lambda st, now: (False, "今日主动已达上限")
        s = _mk_stub(reply="在忙什么呢")
        s._cfg = types.SimpleNamespace(active_character_id="c1")
        await P.ProactiveStrategy.check_and_trigger(s)
        check("13t 关系闸门拦下不开口", s._last_message is None, repr(s._last_message))

        _R._proactive_decision = lambda st, now: (True, "ok")
        s = _mk_stub(reply="在忙什么呢")
        s._cfg = types.SimpleNamespace(active_character_id="c1")
        await P.ProactiveStrategy.check_and_trigger(s)
        check("13t2 关系闸门放行就开口（13t 的反面对照）",
              s._last_message == "在忙什么呢", repr(s._last_message))

        # 长会话收尾：命中后必须 return，不能再叠一句通用关心
        async def _break_stub(cid, now):
            s._last_message = "喝口水歇会儿吧"
            return True

        s = _mk_stub(reply="在忙什么呢")
        s._cfg = types.SimpleNamespace(active_character_id="c1")
        s._long_session_due = lambda now, last_in: True
        s._emit_long_session_break = _break_stub
        await P.ProactiveStrategy.check_and_trigger(s)
        check("13s 长会话收尾后不再叠一句通用关心",
              s._last_message == "喝口水歇会儿吧", repr(s._last_message))
    finally:
        _R.get_relationship_manager, _R._proactive_decision, _R.log_relation_event = _orig_rel

    # 记忆主动与通用引信互斥（两条路径互斥，不重复开口）
    s = _mk_stub(reply="在忙什么呢")

    async def _mem_ok(now, name, hint, idle):
        s._last_message = "最近还在玩那个游戏吗"
        return True

    s._maybe_memory_initiative = _mem_ok
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13r 记忆主动开口后不再走通用引信",
          s._last_message == "最近还在玩那个游戏吗", repr(s._last_message))

    # 空句 / 废话 / 重复
    s = _mk_stub(reply="")
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13q 空句不开口", s._last_message is None, repr(s._last_message))

    s = _mk_stub(reply="嗯")
    s._is_empty_filler = lambda t: True
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13q2 废话不开口", s._last_message is None, repr(s._last_message))

    s = _mk_stub(reply="在忙什么呢")
    s._similar_to_recent = lambda t: True
    await P.ProactiveStrategy.check_and_trigger(s)
    check("13q3 重复不开口", s._last_message is None, repr(s._last_message))

    # ---------- 14) 记忆主动：冷却 / 随机门 / 无事实 / 事实轮转 ----------
    _orig_get_store = _P.get_memory_store
    _orig_random2 = _random.random

    class _Store2:
        def __init__(self, facts):
            self._facts = facts

        def list_user_facts(self, limit=15):
            return list(self._facts)

    try:
        _P.get_memory_store = lambda: _Store2(["用户喜欢喝美式"])
        _random.random = lambda: 0.0

        s = _mk_stub(reply="最近还在玩那个游戏吗")
        s._last_memory_initiative = time.time() - 100
        _ok14a = await P.ProactiveStrategy._maybe_memory_initiative(
            s, time.time(), "达娅", "正常活动中", 400.0)
        check("14a 记忆主动 10 分钟冷却内不开口", _ok14a is False, repr(_ok14a))

        _random.random = lambda: 0.9
        s = _mk_stub(reply="最近还在玩那个游戏吗")
        _ok14b = await P.ProactiveStrategy._maybe_memory_initiative(
            s, time.time(), "达娅", "正常活动中", 400.0)
        check("14b 记忆主动 35% 随机门（未命中不开口）", _ok14b is False, repr(_ok14b))

        _random.random = lambda: 0.0
        _P.get_memory_store = lambda: _Store2([])
        s = _mk_stub(reply="最近还在玩那个游戏吗")
        _ok14c = await P.ProactiveStrategy._maybe_memory_initiative(
            s, time.time(), "达娅", "正常活动中", 400.0)
        check("14c 没有用户事实时不开口", _ok14c is False, repr(_ok14c))

        # 事实指针轮转：两次问不同的事实
        _P.get_memory_store = lambda: _Store2(["用户喜欢喝美式", "用户在玩黑神话"])
        s = _mk_stub(reply="最近还在玩那个游戏吗")
        _n14 = time.time()
        await P.ProactiveStrategy._maybe_memory_initiative(s, _n14, "达娅", "正常活动中", 400.0)
        _p1 = s._llm.prompts[-1]
        s._last_memory_initiative = 0.0      # 手动解冷却，模拟 10 分钟后再来一次
        await P.ProactiveStrategy._maybe_memory_initiative(s, _n14 + 700, "达娅", "正常活动中", 400.0)
        _p2 = s._llm.prompts[-1]
        check("14d 事实指针轮转（两次问不同的事实）",
              "美式" in _p1 and "黑神话" in _p2 and "黑神话" not in _p1,
              repr((_p1[-60:], _p2[-60:])))
    finally:
        _P.get_memory_store = _orig_get_store
        _random.random = _orig_random2

    # ---------- 15) 长会话收尾的记账 ----------
    s = _mk_stub(reply="喝口水歇会儿吧")
    _ok15 = await P.ProactiveStrategy._emit_long_session_break(s, "", time.time())
    check("15a 长会话收尾播出返回 True", _ok15 is True, repr(_ok15))
    check("4d 长会话收尾后置位 _long_session_warned（不再每轮重复劝）",
          s._long_session_warned is True, repr(s._long_session_warned))

    return _finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
