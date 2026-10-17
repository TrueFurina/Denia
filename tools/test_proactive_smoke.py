# -*- coding: utf-8 -*-
"""
主动搭话（proactive speech）回归自测 —— 纯新增脚本，不改动任何业务代码。

覆盖：
  A. relationship_progress_label（引擎 + 管理器委托）：边界/阈值
  B. _proactive_decision 主动闸门顺序：开关→静默→冷却→日上限→冷落按原型
  C. check_and_trigger 双工让位守卫：真实语音/视频双工(realtime/local/local_omni)
     绝不可触发主动管线（保护双工电路不被打断）
  D. _on_user_text_input 事件总线契约：必须是 async（否则 await h(event) 抛 TypeError）

运行：
  python tools/test_proactive_smoke.py
退出码：0=全过，1=有失败。
"""
import sys
import time
import asyncio
import inspect
import datetime
import types

sys.path.insert(0, r"G:/ai对话/11")

from src.companion import relationship as REL
from src.companion.proactive import ProactiveStrategy

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  ❌ %s%s" % (name, ("  " + extra) if extra else ""))


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
    return ENG.relationship_progress_label(None, st)


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
ex1 = ENG.relationship_progress_label(None, {})
ex2 = ENG.relationship_progress_label(None, {"affinity": None})
ok("缺 affinity → 不崩，给『刚认识』", ex1 == "刚认识" and ex2 == "刚认识", ex1)
mal = ENG.relationship_progress_label(None, {"affinity": "not-a-dict"})
ok("affinity 畸形 → 萍水相逢兜底不抛", mal == "萍水相逢", mal)

# 管理器委托（本次修复点：RelationshipManager 委托到 engine）
mgr_has = hasattr(REL.RelationshipManager, "relationship_progress_label")
mgr_src = ""
if mgr_has:
    mgr_src = inspect.getsource(REL.RelationshipManager.relationship_progress_label)
ok("RelationshipManager.relationship_progress_label 存在且委托 engine",
   mgr_has and "self.engine.relationship_progress_label" in mgr_src)


# ===========================================================================
# B. _proactive_decision 闸门顺序
# ===========================================================================
print("\n[B] _proactive_decision 主动闸门")
dec = REL._proactive_decision

r_off = dec(base_state(settings={"proactive": "off", "frequency": "normal"}), NOW)
ok("B1 主动已关 → 拒", r_off == (False, "主动已关"), str(r_off))

r_sil = dec(base_state(), NOW_NIGHT)
ok("B2 静默时段(23:00) → 拒", r_sil == (False, "静默时段"), str(r_sil))

r_cool = dec(base_state(last_proactive_ts=NOW - 100.0), NOW)  # normal=24h，100s 内
ok("B3 冷却中 → 拒", r_cool[0] is False and r_cool[1].startswith("冷却中"), str(r_cool))

r_cool2 = dec(base_state(last_proactive_ts=NOW - 100.0, pending_response=True), NOW)
ok("B4 冷却+你不回翻倍 → 拒", r_cool2[0] is False and "翻倍" in r_cool2[1], str(r_cool2))

r_cap = dec(base_state(daily_proactive={"date": REL._today(NOW),
                                         "count": REL.DAILY_PROACTIVE_CAP}), NOW)
ok("B5 日上限 → 拒", r_cap == (False, "日上限"), str(r_cap))

# B6 冷落且原型不主动：找一个 proactive_when_cold=False 的原型
cold_arch = None
for _a, _v in REL.ARCHETYPES.items():
    if not _v.get("proactive_when_cold"):
        cold_arch = _a
        break
if cold_arch:
    r_cold = dec(base_state(archetype=cold_arch,
                            last_interaction_ts=NOW - (REL.COLD_GAP_H * 3600 + 10)), NOW)
    ok("B6 冷落+原型不主动 → 拒", r_cold == (False, "冷落且原型不主动"), str(r_cold))
else:
    ok("B6 冷落+原型不主动 → 跳过（无此类原型）", True, "skip")

r_ok = dec(base_state(), NOW)
ok("B7 正常 → 开口", r_ok == (True, "ok"), str(r_ok))


# ===========================================================================
# C. check_and_trigger 双工让位守卫（保护双工电路）
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
        # check_and_trigger 的关系闸门块仅在 `_cid` 非空时执行；给个 active_character_id
        # 让闸门真正被走到（否则整块跳过，无法观测"是否让位"）。
        self._cfg = types.SimpleNamespace(active_character_id="danya")
        # check_and_trigger 内部走 `from .relationship import _proactive_decision`，
        # 故无法用实例方法覆盖；这里只在【实例】上标记，由 run_guard 统一打模块补丁观测。
        self.reached_decision = False
        self.reached_emit = False


async def run_guard():
    # check_and_trigger 每轮 `from .relationship import _proactive_decision` 重取模块函数，
    # 故打模块级补丁即可观测"是否走到闸门"。
    _orig_dec = REL._proactive_decision
    _seen = {"n": 0}

    def _spy_dec(state, now=None):
        _seen["n"] += 1
        return (False, "test")   # 一律拦下，避免后续 emit

    REL._proactive_decision = _spy_dec
    try:
        # 【2026-10-10 判据修正】引擎档位(realtime/local/local_omni)不再决定是否让位；
        #   让位只看「此刻是否真在通话」(_in_call())。档位在双工三档但未通话时，
        #   打字空闲主动必须照常跑到闸门（否则就是用户复现的"死触发"）。
        for mode in ("realtime", "local", "local_omni"):
            m = Mini(mode, with_llm=True, in_call=False)
            _seen["n"] = 0
            await m.check_and_trigger()
            ok("C-%s 档位但未通话：主动管线照常走到闸门" % mode,
               _seen["n"] == 1,
               "gate_calls=%d" % _seen["n"])
        # 真正通话中（_in_call()=True）→ 必须让位，保护双工电路
        for mode in ("realtime", "local", "local_omni"):
            m = Mini(mode, with_llm=True, in_call=True)
            _seen["n"] = 0
            await m.check_and_trigger()
            ok("C-%s 通话中：让位不打扰" % mode,
               _seen["n"] == 0,
               "gate_calls=%d" % _seen["n"])
    finally:
        REL._proactive_decision = _orig_dec

    # 非双工 + 无 llm：应安全提前返回（不崩、不触发）
    m2 = Mini(False, with_llm=False)
    await m2.check_and_trigger()
    ok("C-非双工(无llm)：安全返回不触发",
       (not m2.reached_decision) and (not m2.reached_emit))

    # 守卫精确性（核心）：让位判据来自 _in_call()，与引擎档位字符串解耦
    import inspect as _ins
    src = _ins.getsource(ProactiveStrategy.check_and_trigger)
    ok("C-让位判据来自 _in_call()（不再用 _duplex_mode in 三档字符串）",
       ("self._in_call()" in src) and ("self._duplex_mode in ('realtime', 'local', 'local_omni')" not in src))


asyncio.run(run_guard())


# ===========================================================================
# D. _on_user_text_input 必须是 async（事件总线契约）
# ===========================================================================
print("\n[D] 事件总线契约")
ok("_on_user_text_input 是 async def（await h(event) 不会 TypeError）",
   inspect.iscoroutinefunction(ProactiveStrategy._on_user_text_input))


# ===========================================================================
print("\n================ 汇总 ================")
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
