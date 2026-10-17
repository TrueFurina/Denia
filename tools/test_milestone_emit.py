"""里程碑 → 事件发射（reporter 侧）测试：验证首跨阶段时总线收到 AGENT_STATUS(milestone)。"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)

    # 【2026-10-11】reporter 现改用 publish_threadsafe 发事件（同步、不产生未 await 协程）。
    # 桩需同时支持两个方法，否则新代码路径静默丢事件。
    def publish_threadsafe(self, ev):
        self.events.append(ev)

    def subscribe(self, *a, **k):
        pass


async def main():
    from src.companion import relationship as R
    from src.companion.relationship_reporter import RelationshipReporter

    print("== 1. 首跨阶段 → 发 milestone 事件 ==")
    with tempfile.TemporaryDirectory() as td:
        tc = Path(td)
        # 造一个"即将跨熟络门槛"的状态并落盘
        mgr = R.get_relationship_manager(tc)
        t = time.time()
        st = R.default_state("danya", archetype="温柔", ts=t - 30 * 86400)
        st["created_ts"] = t - 30 * 86400
        st["turns"] = 999
        st["affinity"] = {"trust": 50, "intimacy": 48, "warmth": 50}
        st["stage"] = "萍水"
        st["last_interaction_ts"] = t - 3600
        mgr.store.save(st)

        bus = _Bus()
        rep = RelationshipReporter(bus, cfg=None, enabled=True, base_dir=tc)
        # 桩裁判：直接返回强正向信号（含 depth/effort），确保一轮跨过门槛
        class _Judge:
            async def judge(self, ut, persona=None, context=None):
                return {"valence": 1.0, "care": 1.0, "sufficient": True,
                        "confidence": 1.0, "depth": 1.0, "effort": 1.0,
                        "judge": "stub"}
        rep._judge = _Judge()
        await rep._judge_and_apply("danya", "今天聊得真开心，谢谢你一直陪着我")
        ms_events = [e for e in bus.events
                     if getattr(e, "data", None) and isinstance(e.data, dict)
                     and e.data.get("milestone")]
        print("     事件数：", len(bus.events), "里程碑事件：", len(ms_events))
        # 【2026-10-11】现在首跨阶段会发**两条**带 milestone 的事件：
        #   ① AI_DECISION{kind:'milestone'}（让她那句话进聊天流特殊气泡）
        #   ② AGENT_STATUS{phase:'milestone'}（桌宠/状态侧，向后兼容）
        check("首跨阶段发射了里程碑事件", len(ms_events) == 2, bus.events)
        ev_types = sorted(str(getattr(e.type, "value", e.type)) for e in ms_events)
        check("含 AI_DECISION 与 AGENT_STATUS 两种",
              ev_types == ["agent_status", "ai_decision"], ev_types)
        if ms_events:
            check("事件带台词", all(bool(e.data.get("line")) for e in ms_events),
                  [e.data.get("line") for e in ms_events])
            for e in ms_events:
                print("      台词(%s)：" % str(getattr(e.type, "value", e.type)),
                      e.data.get("line"))
        # 状态确认
        st_after = R.get_relationship_manager(tc).store.load("danya")
        check("已升到熟络", st_after["stage"] == "熟络", st_after["stage"])
        check("milestones 已落盘", len(st_after.get("milestones") or []) == 1,
              st_after.get("milestones"))

    print("\n== 2. _emit_milestone_if_any 直接单元 ==")
    with tempfile.TemporaryDirectory() as td:
        bus = _Bus()
        rep = RelationshipReporter(bus, cfg=None, enabled=True, base_dir=Path(td))
        st = {"last_signals": {"milestone": {"kind": "stage:交心", "stage": "交心",
                                             "line": "有些心里话，我只想说给你听。"}}}
        rep._emit_milestone_if_any("danya", st, {})
        # 【2026-10-11】两条事件：AI_DECISION（聊天流特殊气泡）+ AGENT_STATUS（状态侧）
        check("发了两条事件", len(bus.events) == 2, bus.events)
        by_type = {str(getattr(e.type, "value", e.type)): e for e in bus.events}
        check("含 AGENT_STATUS", "agent_status" in by_type, list(by_type))
        ev = by_type.get("agent_status")
        if ev:
            check("AGENT_STATUS data.milestone=True", ev.data.get("milestone") is True, ev.data)
            check("AGENT_STATUS data.line 正确",
                  ev.data.get("line") == "有些心里话，我只想说给你听。", ev.data)
        adv = by_type.get("ai_decision")
        check("含 AI_DECISION", adv is not None, list(by_type))
        if adv:
            check("AI_DECISION kind=milestone", adv.data.get("kind") == "milestone", adv.data)
            check("AI_DECISION streamed=False", adv.data.get("streamed") is False, adv.data)
            check("AI_DECISION 带 stage", adv.data.get("stage") == "交心", adv.data)

        print("\n== 3. 无里程碑 → 不发事件 ==")
        bus2 = _Bus()
        rep2 = RelationshipReporter(bus2, cfg=None, enabled=True, base_dir=Path(td))
        rep2._emit_milestone_if_any("danya", {"last_signals": {}}, {})
        check("不发射", len(bus2.events) == 0, bus2.events)
        rep2._emit_milestone_if_any("danya", {}, {})
        check("空 state 也不发射/不抛", len(bus2.events) == 0)

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
