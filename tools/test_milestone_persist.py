# -*- coding: utf-8 -*-
"""里程碑「绝对不丢」三写验证（2026-10-11）。

开发者要求："里程碑是绝对不能丢弃的"。`relationship_reporter._emit_milestone_if_any`
在里程碑发生**当下**做三件事，任一失败都不影响其它：
  ① 写进 `shared_history()`（此后整段记忆提炼能通读到那句）
  ② 作为 episode 直写记忆库（不依赖"上下文快超限"时机）
  ③ 发 AI_DECISION{kind:'milestone'}（以特殊气泡进聊天流）

本测试用桩件验证这三条都真的发生，且：
  · 未注入共享 store → 不直写（**绝不另开第二写者**，防 json 丢更新）
  · shared_history 抛异常 → 不影响记忆直写与事件发射（隔离）
  · episode 类型正确、正文带台词、不含数字（反 PUA 红线）
"""
import sys
import tempfile
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

    def publish_threadsafe(self, ev):
        self.events.append(ev)

    def subscribe(self, *a, **k):
        pass


class _Store:
    """桩 MemoryStore：记录 add 调用。"""

    def __init__(self):
        self.added = []
        self._char = ""
        self.fail = False

    def set_character(self, cid):
        self._char = cid

    def add(self, text, type="user", source="user", **kw):
        if self.fail:
            raise RuntimeError("store boom")
        self.added.append((text, type, source, self._char))
        return True


def main():
    from src.companion.relationship_reporter import RelationshipReporter

    print("== 1. 三写齐发（history + memory + ai_decision）==")
    with tempfile.TemporaryDirectory() as td:
        bus = _Bus()
        store = _Store()
        rep = RelationshipReporter(bus, cfg=None, enabled=True, base_dir=Path(td),
                                   memory_store=store)
        seen_hist = []
        # 猴补 shared_history：记录写入（隔离真实 data/）
        import src.companion.conversation_history as CH

        class _Hist:
            def add_turn(self, role, content, source=""):
                seen_hist.append((role, content, source))

            def get_recent(self, n=999):
                return []
        orig = getattr(CH, "shared_history", None)
        CH.shared_history = lambda: _Hist()
        try:
            st = {"last_signals": {"milestone": {
                "kind": "stage:交心", "stage": "交心",
                "line": "有些心里话，我只想说给你听。"}}}
            rep._emit_milestone_if_any("danya", st, {})
        finally:
            if orig is not None:
                CH.shared_history = orig

        # ① 历史
        check("① 写进对话历史（assistant）", len(seen_hist) == 1
              and seen_hist[0][0] == "assistant", seen_hist)
        check("① 历史正文=台词", seen_hist and seen_hist[0][1] == "有些心里话，我只想说给你听。",
              seen_hist)
        # ② 记忆
        check("② 直写记忆 1 条", len(store.added) == 1, store.added)
        if store.added:
            txt, typ, src, ch = store.added[0]
            check("② 类型=episode", typ == "episode", typ)
            check("② 来源=relationship", src == "relationship", src)
            check("② 正文含台词", "有些心里话" in txt, txt)
            check("② 正文含阶段名", "交心" in txt, txt)
            check("② 作用域=当前角色", ch == "danya", ch)
            # 反 PUA 红线：不含数字/分数
            import re as _re
            check("② 不含数字（反 PUA）", not _re.search(r"\d", txt), txt)
        # ③ 事件
        adv = [e for e in bus.events
               if str(getattr(e.type, "value", "")) == "ai_decision"]
        check("③ 发了 AI_DECISION", len(adv) == 1, bus.events)
        check("③ kind=milestone", adv and adv[0].data.get("kind") == "milestone", adv)

    print("\n== 2. 未注入共享 store → 不直写（绝不另开第二写者）==")
    with tempfile.TemporaryDirectory() as td:
        bus = _Bus()
        rep = RelationshipReporter(bus, cfg=None, enabled=True, base_dir=Path(td),
                                   memory_store=None)
        import src.companion.conversation_history as CH

        class _Hist:
            def add_turn(self, *a, **k):
                pass

            def get_recent(self, n=999):
                return []
        orig = CH.shared_history
        CH.shared_history = lambda: _Hist()
        try:
            st = {"last_signals": {"milestone": {"kind": "stage:熟络", "stage": "熟络",
                                                 "line": "跟你聊天时间过得好快。"}}}
            ok = rep._direct_write_milestone_memory("danya", "跟你聊天时间过得好快。", "熟络", "stage:熟络")
            check("无共享 store → 返回 False（降级不直写）", ok is False)
            # 仍应发事件（不因缺 store 就不发气泡）
            rep._emit_milestone_if_any("danya", st, {})
            check("无 store 仍发聊天事件", any(
                str(getattr(e.type, "value", "")) == "ai_decision" for e in bus.events))
        finally:
            CH.shared_history = orig

    print("\n== 3. 历史写失败 → 隔离（记忆/事件照常）==")
    with tempfile.TemporaryDirectory() as td:
        bus = _Bus()
        store = _Store()
        rep = RelationshipReporter(bus, cfg=None, enabled=True, base_dir=Path(td),
                                   memory_store=store)
        import src.companion.conversation_history as CH

        class _BoomHist:
            def add_turn(self, *a, **k):
                raise RuntimeError("history boom")
        orig = CH.shared_history
        CH.shared_history = lambda: _BoomHist()
        try:
            st = {"last_signals": {"milestone": {"kind": "stage:交心", "stage": "交心",
                                                 "line": "只跟你说。"}}}
            rep._emit_milestone_if_any("danya", st, {})   # 不应抛
        finally:
            CH.shared_history = orig
        check("历史抛异常不冒泡", True)
        check("记忆仍写入", len(store.added) == 1, store.added)
        check("事件仍发出", any(str(getattr(e.type, "value", "")) == "ai_decision"
                             for e in bus.events))

    print("\n== 4. 记忆写失败 → 隔离（事件照常）==")
    with tempfile.TemporaryDirectory() as td:
        bus = _Bus()
        store = _Store()
        store.fail = True
        rep = RelationshipReporter(bus, cfg=None, enabled=True, base_dir=Path(td),
                                   memory_store=store)
        import src.companion.conversation_history as CH

        class _Hist:
            def add_turn(self, *a, **k):
                pass
        orig = CH.shared_history
        CH.shared_history = lambda: _Hist()
        try:
            st = {"last_signals": {"milestone": {"kind": "stage:离不开", "stage": "离不开",
                                                 "line": "别让我改这个习惯。"}}}
            rep._emit_milestone_if_any("danya", st, {})   # 不应抛
        finally:
            CH.shared_history = orig
        check("记忆抛异常不冒泡", True)
        check("事件仍发出", any(str(getattr(e.type, "value", "")) == "ai_decision"
                             for e in bus.events))

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
