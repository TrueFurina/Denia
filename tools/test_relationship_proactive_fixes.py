"""好感度 + 主动发言：本轮改动回归测试（纯新增脚本，不改动任何业务代码）。

覆盖：
  A. relationship_progress_label（新增定性词）边界
  B. _proactive_decision 关系闸门（开口决策，P5/P6）
  C. check_and_trigger 双工守卫（死触发修复核心）
  D. _on_user_text_input 事件总线契约（async 修复）
  E. 事件总线契约回归（非协程 handler 会被 await None 炸）
  F. 备注：main.py 的 enabled 任务启动门 / webapp.get_relationship_state 的 progress 字段
     需要 GUI/集成环境，标记 SKIP，不在本脚本内伪造。

运行：python tools/test_relationship_proactive_fixes.py
输出：逐项 PASS/FAIL，末尾汇总；退出码 0=全绿，1=有失败。
"""
import asyncio
import inspect
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.companion.relationship as R
from src.companion.relationship import _proactive_decision, get_relationship_manager
import src.companion.proactive as P
from src.event_bus import EventBus, Event, EventType

PASS, FAIL, SKIP = [], [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not cond else ""))

# ---------------- A. relationship_progress_label ----------------
print("\n== A. relationship_progress_label 边界 ==")
mgr = get_relationship_manager()
def aff_state(tr, it, wa):
    return {"affinity": {"trust": tr, "intimacy": it, "warmth": wa}}
def score_of(tr, it, wa):
    return 0.4*tr + 0.4*it + 0.2*wa
# 等权 1.0 的三维点：score=0.4*tr+0.4*it+0.2*wa
A_cases = [
    (10, 10, 10, "刚认识"),
    (30, 30, 30, "慢慢熟悉中"),
    (42, 42, 42, "处得挺舒服"),
    (54, 54, 54, "越来越亲近"),
    (66, 66, 66, "真的很在乎你"),
    (78, 78, 78, "形影不离"),
]
for tr, it, wa, exp in A_cases:
    got = mgr.relationship_progress_label(aff_state(tr, it, wa))
    check(f"score={score_of(tr,it,wa):.0f}->{exp}", got == exp, f"got={got}")
# 边界
A_edge = [
    (29.9, "刚认识"), (30.0, "慢慢熟悉中"), (41.9, "慢慢熟悉中"), (42.0, "处得挺舒服"),
    (53.9, "处得挺舒服"), (54.0, "越来越亲近"), (65.9, "越来越亲近"), (66.0, "真的很在乎你"),
    (77.9, "真的很在乎你"), (78.0, "形影不离"),
]
for sc, exp in A_edge:
    # 用 trust=sc, intimacy=sc, warmth=sc 使 score=sc
    got = mgr.relationship_progress_label(aff_state(sc, sc, sc))
    check(f"edge score={sc:g}->{exp}", got == exp, f"got={got}")
# 真实 danya
real = mgr.relationship_progress_label(get_relationship_manager().get("danya"))
check("真实 danya state -> 处得挺舒服", real == "处得挺舒服", f"got={real}")

# ---------------- B. _proactive_decision 闸门 ----------------
print("\n== B. _proactive_decision 关系闸门 ==")
# 2026-10-10 修·时间脆弱：此前 now=time.time() 直接用真实时钟，
#   深夜（22:00–08:00）跑测试时 _proactive_decision 一律判「静默时段」→
#   B6/B7（期望放行）假失败。改为固定「当天 15:00」（白天、非静默）作为基准，
#   与真实运行时刻解耦；B2 仍显式构造当天 23:00 验证静默拦截。
_d = time.localtime()[:3]           # 当天 (年,月,日)
_now_t = time.mktime(_d + (15, 0, 0, 0, 0, -1))
now = _now_t

def base_state(over=None):
    st = {
        "settings": {"proactive": "on", "pace": "标准", "frequency": "normal"},
        "archetype": "温柔",
        # 【2026-10-11 修·时间脆弱2】原为 time.time()（真实时钟）。当机器时间 < 基准
        #   now（如今日 15:00 而实际是凌晨 00:21）时，now - last_interaction 变成
        #   +14.6h ≥ COLD_GAP_H(12h) → B7 被误判「冷落且原型不主动」假失败。
        #   改为与基准 now 同源：默认「刚刚互动过」，与运行时刻彻底解耦。
        "last_interaction_ts": None,   # 占位，由 base_state 按 now 填充
        "last_proactive_ts": 0.0,
        "daily_proactive": {"date": "", "count": 0},
        "pending_response": False,
    }
    if over: st.update(over)
    # 未显式指定 last_interaction_ts 时，锚定到「同一基准时刻刚互动」
    if st.get("last_interaction_ts") is None:
        st["last_interaction_ts"] = now
    return st
# B1 主动关
check("B1 主动关->拦", _proactive_decision(base_state({"settings": {"proactive": "off"}}), now)[0] is False)
# B2 静默时段 23:00（当天）
t23 = time.mktime(_d + (23, 0, 0, 0, 0, -1))
check("B2 静默时段(23:00)->拦", _proactive_decision(base_state(), t23)[0] is False)
# B3 冷却（normal=24h，刚说过）
check("B3 冷却中(24h)->拦", _proactive_decision(base_state({"last_proactive_ts": now - 3600}), now)[0] is False)
# B4 日上限
check("B4 日上限(6/日)->拦", _proactive_decision(base_state({"daily_proactive": {"date": R._today(now), "count": 6}}), now)[0] is False)
# B5 冷落>=12h + 温柔 -> 拦
check("B5 冷落12h+温柔->拦", _proactive_decision(base_state({"last_interaction_ts": now - 13*3600, "archetype": "温柔"}), now)[0] is False)
# B6 冷落>=12h + 黏人 -> 放
check("B6 冷落12h+黏人->放", _proactive_decision(base_state({"last_interaction_ts": now - 13*3600, "archetype": "黏人"}), now)[0] is True)
# B7 默认可开口
ok, why = _proactive_decision(base_state(), now)
check("B7 默认场景->放", ok is True, f"why={why}")

# ---------------- C/D/E：需要事件循环的引擎级测试 ----------------
async def engine_tests():
    print("\n== C. check_and_trigger 双工守卫（死触发修复核心）==")
    bus = mock.AsyncMock(); state = mock.MagicMock(); cfg = mock.MagicMock()
    cfg.active_character_id = "danya"
    s = P.ProactiveStrategy(bus, state, cfg, llm=object(), persona=mock.MagicMock())
    # 用 fake 闸门记录是否被调用（check_and_trigger 内部 `from .relationship import _proactive_decision` 每轮重取，故打补丁生效）
    calls = {"n": 0}
    orig = R._proactive_decision
    def fake_dec(st, nw=None):
        calls["n"] += 1
        return False, "fake"
    R._proactive_decision = fake_dec
    # 通用：放行到闸门所需的最小前置
    s._llm = object(); s._llm_generate = True
    s._last_input_ts = 0.0          # idle 巨大
    s._last_proactive = 0.0
    s._idle_threshold_s = 0.0
    s._batch_interval_s = 0.0
    try:
        # C1 快照模式 True -> 应当放行到闸门（用真实历史时间戳作空闲基线，idle 巨大）
        calls["n"] = 0; s._duplex_mode = True; s._last_input_ts = time.time() - 1000.0
        await s.check_and_trigger()
        check("C1 _duplex_mode=True -> 到达闸门", calls["n"] == 1, f"calls={calls['n']}")
        # C2/C3 【2026-10-10 判据修正后】引擎档位 realtime/local_omni 不再让位：
        #   只要「不在通话中」(_in_call()==False)，打字空闲主动就必须照常跑到闸门。
        #   （旧断言"realtime/local_omni->早退"正是把缺陷写进了测试，已废弃。）
        calls["n"] = 0; s._duplex_mode = "realtime"; s._last_input_ts = time.time() - 1000.0
        await s.check_and_trigger()
        check("C2 _duplex_mode=realtime 非通话 -> 到达闸门", calls["n"] == 1, f"calls={calls['n']}")
        calls["n"] = 0; s._duplex_mode = "local_omni"; s._last_input_ts = time.time() - 1000.0
        await s.check_and_trigger()
        check("C3 _duplex_mode=local_omni 非通话 -> 到达闸门", calls["n"] == 1, f"calls={calls['n']}")
        # C3b 真正通话中(_in_call()==True) -> 让位（原设计意图 §7.1）
        calls["n"] = 0; s._duplex_call_mode = "voice"; s._last_input_ts = time.time() - 1000.0
        await s.check_and_trigger()
        check("C3b 通话中(_in_call=True) -> 早退让位", calls["n"] == 0, f"calls={calls['n']}")
        s._duplex_call_mode = None
        # C3c 通话标志清空后恢复放行（证明判据是 _in_call 而非档位）
        calls["n"] = 0; s._last_input_ts = time.time() - 1000.0
        await s.check_and_trigger()
        check("C3c 挂断后 -> 恢复到达闸门", calls["n"] == 1, f"calls={calls['n']}")
        # C4 _llm=None -> 早退
        calls["n"] = 0; s._duplex_mode = True; s._llm = None
        await s.check_and_trigger()
        check("C4 _llm=None -> 早退", calls["n"] == 0, f"calls={calls['n']}")
        s._llm = object()
        # C5 _llm_generate=False -> 早退
        calls["n"] = 0; s._llm_generate = False
        await s.check_and_trigger()
        check("C5 _llm_generate=False -> 早退", calls["n"] == 0, f"calls={calls['n']}")
        s._llm_generate = True
        # C6 无 _last_input_ts（从未打字）→ 【2026-10-11 语义修正】不再早退：
        #    应以 _session_boot_ts 为兜底基准；开机已久即算空闲到达闸门。
        #    旧断言「早退」把第二层死触发当成期望行为，已废弃。
        calls["n"] = 0; s._last_input_ts = None
        s._session_boot_ts = time.time() - 1000.0
        await s.check_and_trigger()
        check("C6a 无输入+开机久 -> 到达闸门(第二层死触发修复)", calls["n"] == 1, f"calls={calls['n']}")
        # C6b 无 _last_input_ts 且刚开机 → 空闲不足 → 早退
        #   （需显式设阈值：本桩前面把 _idle_threshold_s 置 0 以便其它用例放行）
        calls["n"] = 0; s._last_input_ts = None
        s._session_boot_ts = time.time() - 5.0
        s._idle_threshold_s = 180.0
        await s.check_and_trigger()
        check("C6b 无输入+刚开机 -> 早退", calls["n"] == 0, f"calls={calls['n']}")
        s._session_boot_ts = time.time() - 1000.0
        s._idle_threshold_s = 0.0
        # C7 idle 不足 -> 早退
        calls["n"] = 0; s._last_input_ts = time.time()  # 刚刚输入
        s._idle_threshold_s = 180.0
        await s.check_and_trigger()
        check("C7 idle<阈值 -> 早退", calls["n"] == 0, f"calls={calls['n']}")
    finally:
        R._proactive_decision = orig

    print("\n== D. _on_user_text_input 事件总线契约 ==")
    check("D1 是协程函数(async)", inspect.iscoroutinefunction(s._on_user_text_input))
    s._last_input_ts = None
    await s._on_user_text_input(Event(EventType.USER_TEXT, {"text": "你好"}))
    check("D2 await 后 _last_input_ts 被写入(无 TypeError)", s._last_input_ts is not None, f"val={s._last_input_ts}")

    print("\n== E. 事件总线契约回归 ==")
    eb = EventBus(); loop = asyncio.get_running_loop(); eb.bind_loop(loop)
    eb.subscribe(EventType.USER_TEXT, s._on_user_text_input)
    task = asyncio.create_task(eb.run())
    s._last_input_ts = None
    await eb.publish(Event(EventType.USER_TEXT, {"text": "测试"}))
    await asyncio.sleep(0.5)
    task.cancel()
    try: await task
    except asyncio.CancelledError:
        # 取消后台任务 = 场景正常收尾
        pass
    check("E1 真实总线派发 USER_TEXT 不抛错 & 写入 _last_input_ts", s._last_input_ts is not None, f"val={s._last_input_ts}")

asyncio.run(engine_tests())

print("\n== F. 需集成环境项（SKIP，非伪造）==")
SKIP.append("F1 main.py proactive_enabled 任务启动门（读 config.proactive.enabled）")
SKIP.append("F2 webapp.get_relationship_state 返回 progress 字段（需 pywebview GUI）")
for s in SKIP:
    print(f"  [SKIP] {s}")

print("\n================ 汇总 ================")
print(f"PASS={len(PASS)}  FAIL={len(FAIL)}  SKIP={len(SKIP)}")
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
