# -*- coding: utf-8 -*-
"""
主动搭话（proactive speech）回归自测 —— 纯新增脚本，不改动任何业务代码。

覆盖：
  A. relationship_progress_label（引擎 + 管理器委托）：边界 / 阈值 / 权重 / 畸形输入
  B. _proactive_decision 主动闸门顺序：开关→静默→冷却→日上限→冷落按原型
  C. check_and_trigger 双工让位守卫 + 空闲 / 批次 / 8s / 开机起算四道前置守卫
  D. _on_user_text_input 事件总线契约 + subscribe() 真接线

【2026-11-11 判据大修（第 47 轮变异探针）】
52 把探针下去捅出 16 处洞，逐条按「判据先行」先定名字再补：
  · A 组 warmth 权重零判据（样本 warmth 全是 0，唯一非 0 样本已饱和）；
    管理器「委托」只是 inspect.getsource 找源码字符串，改成「调用但不采纳返回值」照样绿。
  · B 组「不回翻倍」只查理由串里有没有「翻倍」两个字，压根没量冷却时长；
    B6 的 else 分支是无条件 ok(..., True, "skip")，整道冷落闸门可以静默消失。
  · C 组 reached_decision / reached_emit 是门自己塞的标记、生产代码从来不写；
    四个前置守卫被桩（_last_input_ts / _last_proactive / _duplex_last_speak_ts）
    钉成不可达；app 通话标志那条 OR 分支根本没有样本。
  · D 组只查 iscoroutinefunction，handler 与 subscribe() 一次都没被执行过
    —— 写在文档字符串里的「回归」不算判据。
  · 裸调用实现一抛异常就炸整门、连汇总行都没有，统一走 _safe 包装：
    坏实现只报 ❌，不崩门（判据崩 ≠ 判据错）。
  · 加 EXPECTED_CHECKS 条数总账：少跑一段不算通过。

运行：python tools/test_proactive_smoke.py
退出码：0=全过，1=有失败。
"""
import os
import sys
import time
import asyncio
import inspect
import datetime
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.companion import relationship as REL
from src.companion.proactive import ProactiveStrategy
from src.event_bus import EventType

PASS = 0
FAIL = 0
EXPECTED_CHECKS = 48  # 实测 48 条；少跑的判据不算通过


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  ❌ %s%s" % (name, ("  " + extra) if extra else ""))


def _safe(fn, *args):
    """坏实现抛异常时返回标记串，不让整门崩掉（判据崩 ≠ 判据错）。"""
    try:
        return fn(*args)
    except Exception as e:
        return "<RAISED:%s>" % type(e).__name__


# ---------------------------------------------------------------------------
# 确定性时间：今天 15:00 本地（避开静默时段 22:00-08:00）
# ---------------------------------------------------------------------------
_today = datetime.date.today()
_naive_day = datetime.datetime(_today.year, _today.month, _today.day, 15, 0, 0)
NOW = time.mktime(_naive_day.timetuple())
_night = datetime.datetime(_today.year, _today.month, _today.day, 23, 0, 0)
NOW_NIGHT = time.mktime(_night.timetuple())


def base_state(**over):
    s = {
        "settings": {"proactive": "on", "frequency": "normal"},
        "archetype": REL.DEFAULT_ARCHETYPE,
        "pending_response": False,
        "last_proactive_ts": 0.0,
        "daily_proactive": {"date": REL._today(NOW), "count": 0},
        "last_interaction_ts": NOW - 3600,  # 1h 前，gap < COLD_GAP_H
    }
    s.update(over)
    return s


# ===========================================================================
# A. relationship_progress_label
# ===========================================================================
print("\n[A] relationship_progress_label（进展定性词）")
ENG = REL.RelationshipEngine


def label(trust, intimacy, warmth):
    st = {"affinity": {"trust": trust, "intimacy": intimacy, "warmth": warmth}}
    return _safe(ENG.relationship_progress_label, None, st)


# 阈值：score = 0.4*trust + 0.4*intimacy + 0.2*warmth
ok("0 分 → 刚认识", label(0, 0, 0) == "刚认识", label(0, 0, 0))
ok("29.6 → 刚认识", label(74, 0, 0) == "刚认识", label(74, 0, 0))
ok("30 → 慢慢熟悉中", label(75, 0, 0) == "慢慢熟悉中", label(75, 0, 0))
ok("41.6 → 慢慢熟悉中", label(100, 4, 0) == "慢慢熟悉中", label(100, 4, 0))
ok("42 → 处得挺舒服", label(100, 5, 0) == "处得挺舒服", label(100, 5, 0))
ok("53.6 → 处得挺舒服", label(100, 34, 0) == "处得挺舒服", label(100, 34, 0))
ok("54 → 越来越亲近", label(100, 35, 0) == "越来越亲近", label(100, 35, 0))
ok("65.6 → 越来越亲近", label(100, 64, 0) == "越来越亲近", label(100, 64, 0))
ok("66 → 真的很在乎你", label(100, 65, 0) == "真的很在乎你", label(100, 65, 0))
ok("77.6 → 真的很在乎你", label(100, 94, 0) == "真的很在乎你", label(100, 94, 0))
ok("78 → 形影不离", label(100, 95, 0) == "形影不离", label(100, 95, 0))
ok("100 → 形影不离", label(100, 100, 100) == "形影不离", label(100, 100, 100))
# 缺字段不崩（默认给最低档），畸形输入兜底萍水相逢
ex1 = _safe(ENG.relationship_progress_label, None, {})
ex2 = _safe(ENG.relationship_progress_label, None, {"affinity": None})
ok("缺 affinity → 不崩，给『刚认识』", ex1 == "刚认识" and ex2 == "刚认识", ex1)
mal = _safe(ENG.relationship_progress_label, None, {"affinity": "not-a-dict"})
ok("affinity 畸形 → 萍水相逢兜底不抛", mal == "萍水相逢", mal)
# 【第 47 轮补】warmth 权重：原样本 warmth 全 0，唯一非 0 的 (100,100,100) 已饱和，
# 把 0.2 改成 0 门照样全绿。这一条让 warmth 成为唯一的决定项。
ok("A13b warmth 参与计分（0/0/150 → 慢慢熟悉中）",
   label(0, 0, 150) == "慢慢熟悉中", label(0, 0, 150))

# 管理器委托（本次修复点：RelationshipManager 委托到 engine）
mgr_has = hasattr(REL.RelationshipManager, "relationship_progress_label")
mgr_src = ""
if mgr_has:
    mgr_src = _safe(inspect.getsource, REL.RelationshipManager.relationship_progress_label)
ok("RelationshipManager.relationship_progress_label 存在且委托 engine",
   mgr_has and "self.engine.relationship_progress_label" in mgr_src)
# 源码文本判据量不出接线：改成「调用 engine 但丢掉返回值、return 常量」字符串还在。
# 这条用 spy 真调一次，既查 state 原样送达，也查返回值被采纳。
class _SpyEngine:
    def __init__(self):
        self.calls = []

    def relationship_progress_label(self, state):
        self.calls.append(state)
        return "委托成功"


_mgr = REL.RelationshipManager.__new__(REL.RelationshipManager)
_mgr.engine = _SpyEngine()
_st = {"affinity": {"trust": 100, "intimacy": 100, "warmth": 100}}
_out = _safe(_mgr.relationship_progress_label, _st)
ok("A14 管理器委托真的把 state 交给 engine 并采纳返回值",
   _out == "委托成功" and _mgr.engine.calls == [_st], str(_out))


# ===========================================================================
# B. _proactive_decision 闸门顺序
# ===========================================================================
print("\n[B] _proactive_decision 主动闸门")
dec = REL._proactive_decision


def decide(state, now):
    return _safe(dec, state, now)


r_off = decide(base_state(settings={"proactive": "off", "frequency": "normal"}), NOW)
ok("B1 主动已关 → 拒", r_off == (False, "主动已关"), str(r_off))

r_sil = decide(base_state(), NOW_NIGHT)
ok("B2 静默时段(23:00) → 拒", r_sil == (False, "静默时段"), str(r_sil))

r_cool = decide(base_state(last_proactive_ts=NOW - 100.0), NOW)  # normal=24h，100s 内
ok("B3 冷却中 → 拒", r_cool[0] is False and str(r_cool[1]).startswith("冷却中"), str(r_cool))

r_cool2 = decide(base_state(last_proactive_ts=NOW - 100.0, pending_response=True), NOW)
ok("B4 冷却+你不回翻倍 → 拒",
   r_cool2[0] is False and "翻倍" in str(r_cool2[1]), str(r_cool2))
# 【第 47 轮补】B4 只查理由串里有没有「翻倍」两个字，没量冷却时长：
# 把倍率拆掉、理由串照写「翻倍」，门照样绿。这里用 30h 卡在 24h 与 48h 之间。
r_dbl = decide(base_state(last_proactive_ts=NOW - 30 * 3600,
                          pending_response=True), NOW)
ok("B4b 不回确实把冷却拉到 48h（30h 仍冷却）",
   r_dbl[0] is False and str(r_dbl[1]).startswith("冷却中"), str(r_dbl))
r_nodbl = decide(base_state(last_proactive_ts=NOW - 30 * 3600,
                            pending_response=False), NOW)
ok("B4c 正常 24h：30h 已放行", r_nodbl == (True, "ok"), str(r_nodbl))

r_cap = decide(base_state(daily_proactive={"date": REL._today(NOW),
                                           "count": REL.DAILY_PROACTIVE_CAP}), NOW)
ok("B5 日上限 → 拒", r_cap == (False, "日上限"), str(r_cap))

# 【第 47 轮补】原 else 分支是 ok(..., True, "skip")：只要原型表里再没有
# proactive_when_cold=False，整道冷落闸门零判据且门全绿。改成前提判据 + 真调用，
# 并靠条数总账兜住「整段被跳过」。
cold_archs = [a for a, v in REL.ARCHETYPES.items() if not v.get("proactive_when_cold")]
ok("B6a 前提：至少一个不主动原型且黏人是主动原型",
   bool(cold_archs) and bool(REL.ARCHETYPES.get("黏人", {}).get("proactive_when_cold")),
   "不主动原型=%d 个" % len(cold_archs))
cold_arch = cold_archs[0] if cold_archs else "黏人"
r_cold = decide(base_state(archetype=cold_arch,
                           last_interaction_ts=NOW - (REL.COLD_GAP_H * 3600 + 10)), NOW)
ok("B6 冷落+原型不主动 → 拒", r_cold == (False, "冷落且原型不主动"), str(r_cold))
# 原型查表：默认原型「温柔」也是不主动，只测不主动原型分辨不出「查表被拆掉」。
r_warm = decide(base_state(archetype="黏人",
                           last_interaction_ts=NOW - (REL.COLD_GAP_H * 3600 + 10)), NOW)
ok("B6b 冷落但原型黏人 → 照常开口", r_warm == (True, "ok"), str(r_warm))

r_ok = decide(base_state(), NOW)
ok("B7 正常 → 开口", r_ok == (True, "ok"), str(r_ok))


# ===========================================================================
# C. check_and_trigger 双工让位守卫 + 四道前置守卫
# ===========================================================================
print("\n[C] check_and_trigger 双工守卫（不能打断真实语音/视频双工）")


class Mini(ProactiveStrategy):
    def __init__(self, mode, with_llm=False, in_call=False):
        self._duplex_mode = mode
        self._llm = object() if with_llm else None
        self._llm_generate = bool(with_llm)
        self._duplex_last_speak_ts = 0.0
        self._last_input_ts = NOW - 99999.0   # 很久没输入 → 空闲
        self._idle_threshold_s = 60.0
        self._batch_interval_s = 120.0
        self._last_proactive = 0.0
        # 【2026-10-10】让位判据现为 _in_call()：显式注入通话标志
        self._duplex_call_mode = "voice" if in_call else None
        self._duplex_app_call_active = False
        # check_and_trigger 的关系闸门块仅在 `_cid` 非空时执行
        self._cfg = types.SimpleNamespace(active_character_id="danya")


async def _invoke(m):
    """统一 async 包装：坏实现抛异常只变成一条 ❌，不炸整门。"""
    try:
        await m.check_and_trigger()
        return ""
    except Exception as e:
        return "<RAISED:%s>" % type(e).__name__


async def run_guard():
    # check_and_trigger 每轮 `from .relationship import _proactive_decision` 重取模块函数，
    # 故打模块级补丁即可观测「是否走到闸门」。
    # 【第 47 轮修正】原来 finally 在 m2 那段之前就还原了补丁，导致后续用例
    # _seen 恒为 0（假绿）。所有用例统一放在 patched 区域内。
    _orig_dec = REL._proactive_decision
    _seen = {"n": 0}

    def _spy_dec(state, now=None):
        _seen["n"] += 1
        return (False, "test")   # 一律拦下，避免后续 emit

    REL._proactive_decision = _spy_dec
    try:
        async def case(name, m, want):
            _seen["n"] = 0
            REL.RELATION_EVENT_LOG.clear()
            err = await _invoke(m)
            ok(name, (not err) and _seen["n"] == want,
               "gate_calls=%d %s" % (_seen["n"], err))
            return [e["kind"] for e in REL.RELATION_EVENT_LOG]

        # 【2026-10-10 判据修正】引擎档位(realtime/local/local_omni)不再决定是否让位；
        #   让位只看「此刻是否真在通话」(_in_call())。档位在双工三档但未通话时，
        #   打字空闲主动必须照常跑到闸门。
        for mode in ("realtime", "local", "local_omni"):
            await case("C-%s 档位但未通话：主动管线照常走到闸门" % mode,
                       Mini(mode, with_llm=True, in_call=False), 1)
        # 真正通话中（_in_call()=True）→ 必须让位，保护双工电路
        for mode in ("realtime", "local", "local_omni"):
            await case("C-%s 通话中：让位不打扰" % mode,
                       Mini(mode, with_llm=True, in_call=True), 0)

        # 【第 47 轮补】原判据看的是门自己塞的 reached_decision / reached_emit，
        # 生产代码从来不写这两个标记 —— 拆掉无 llm 前置守卫照样绿。改用 spy 计数。
        await case("C-非双工(无llm)：没走到关系闸门",
                   Mini(False, with_llm=False), 0)

        # app 通话标志（_duplex_app_call_active）是 _in_call() 的第二条 OR，
        # 原门里一个样本都没有；且源码文本判据认注释里的字符串就能过。
        m_app = Mini("local", with_llm=True, in_call=False)
        m_app._duplex_app_call_active = True
        ok("C-前提：app 通话标志让 _in_call() 为真", m_app._in_call() is True)
        await case("C-app 通话标志独立生效：让位不打扰", m_app, 0)

        # 四道前置守卫：原桩把 _last_input_ts / _last_proactive /
        # _duplex_last_speak_ts 全钉成「守卫必然放行」，分支根本不可达。
        m_idle = Mini("local", with_llm=True)
        m_idle._last_input_ts = time.time()
        await case("C-刚输入过（空闲未达标）：不评估关系闸门", m_idle, 0)

        m_batch = Mini("local", with_llm=True)
        m_batch._last_proactive = time.time()
        await case("C-批次冷却未过：不评估关系闸门", m_batch, 0)

        m_spk = Mini("local", with_llm=True)
        m_spk._duplex_last_speak_ts = time.time()
        await case("C-刚播过一句 8s 内：不评估关系闸门", m_spk, 0)

        # 旧「死触发」bug：_last_input_ts=0.0 时应当拿开机时刻当空闲起算点。
        m_boot = Mini("local", with_llm=True)
        m_boot._last_input_ts = 0.0
        m_boot._session_boot_ts = time.time() - 99999.0
        await case("C-从没输入过：用开机时刻当空闲起点（不是放弃判定）", m_boot, 1)

        # 闸门拦下后必须真的停：桩返回 (False,"test") 只是「希望」生产别往下走。
        # 观测 RELATION_EVENT_LOG：拦下记 gate_block，放行记 gate_allow。
        m_blk = Mini("local", with_llm=True)
        kinds = await case("C-闸门拦下：仍有到达闸门", m_blk, 1)
        ok("C-闸门拦下：记 gate_block 且不往下走",
           "gate_block" in kinds and "gate_allow" not in kinds,
           "events=%s" % ",".join(kinds))

        # 守卫精确性（源码文本，保留作文档性对照；真正的接线由上面行为判据钉死）
        src = _safe(inspect.getsource, ProactiveStrategy.check_and_trigger)
        ok("C-让位判据来自 _in_call()（不再用 _duplex_mode in 三档字符串）",
           ("self._in_call()" in src)
           and ("self._duplex_mode in ('realtime', 'local', 'local_omni')" not in src))
    finally:
        REL._proactive_decision = _orig_dec


asyncio.run(run_guard())


# ===========================================================================
# D. _on_user_text_input 事件总线契约 + subscribe() 真接线
# ===========================================================================
print("\n[D] 事件总线契约")
ok("_on_user_text_input 是 async def（await h(event) 不会 TypeError）",
   inspect.iscoroutinefunction(ProactiveStrategy._on_user_text_input))

# 【第 47 轮补】原门连 handler 都没调用过：把 self._last_input_ts = time.time()
# 改成 pass 照样绿。真调一次，查副作用。
_h = Mini("local", with_llm=False)
_h._last_input_ts = 0.0


async def _handler_probe():
    try:
        r = ProactiveStrategy._on_user_text_input(_h, object())
        if inspect.isawaitable(r):
            await r
    except Exception:
        pass


asyncio.run(_handler_probe())
ok("D-USER_TEXT handler 真的刷新 _last_input_ts",
   _h._last_input_ts > 0, str(_h._last_input_ts))


class _Bus:
    def __init__(self):
        self.subs = []

    def subscribe(self, ev, handler):
        self.subs.append((ev, handler))


_b = _Bus()
_s = Mini("local", with_llm=False)
_s._bus = _b
_sub_err = _safe(_s.subscribe)
_user_subs = [(e, h) for e, h in _b.subs if e == EventType.USER_TEXT]
ok("D-USER_TEXT 订阅存在且绑到 async handler",
   (not _sub_err) and len(_user_subs) == 1
   and inspect.iscoroutinefunction(_user_subs[0][1]),
   "命中 %d 条 %s" % (len(_user_subs), _sub_err or ""))

_s2 = Mini("local", with_llm=False)
_s2._bus = None
_safe(_s2.subscribe)
ok("D-无 bus：不改内部状态", not hasattr(_s2, "_screen_batch_buf"))


# ===========================================================================
print("\n================ 汇总 ================")
if PASS + FAIL != EXPECTED_CHECKS:
    ok("判据条数总账（少跑的判据不算通过）", False,
       "%d != 期望 %d（有整段被跳过/异常吞掉了）" % (PASS + FAIL, EXPECTED_CHECKS))
print("PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAIL:
    print("结论：存在未通过的用例，需排查。")
    sys.exit(1)
else:
    print("结论：主动搭话相关守卫/闸门逻辑全部通过。")
    print("注意：本测试验证『通话中不被打断』『档位不再误拦打字主动』『闸门顺序正确』；")
    print("『空闲时真正主动开口』由 check_and_trigger 挂载点支撑——")
    print("  非双工档：_rule_loop / _snapshot_loop；双工三档：_duplex_refresh_loop（2026-10-10 修复 B）。")
    sys.exit(0)
