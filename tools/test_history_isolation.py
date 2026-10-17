# -*- coding: utf-8 -*-
"""隔离验证：共享会话历史按角色分桶（2026-10-11 多角色隔离重构）。

背景（旧行为）：
  conversation_history.shared_history() 是**单一进程级 deque**，打字 + 双工都往
  同一条写，且不区分角色。切角色只能 `clear()` 一把抹掉 → 切回旧角色上下文永久丢失，
  且切换瞬间的并发写入可能串味。

新行为：按 char_id 分桶；`set_active_char` 切活动桶；`clear()` 只清当前桶。

本测试不启动整个软件，只验证 conversation_history 的桶语义。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


def main():
    from src.companion import conversation_history as CH

    print("== 共享会话历史·多角色隔离（2026-10-11）==")

    # 干净起点
    CH._shared_by_char.clear()
    CH.set_active_char("")

    # 1) 角色 A 写入 → A 桶有内容
    CH.set_active_char("alice")
    CH.shared_history().add_turn("user", "A说的话", source="chat")
    CH.shared_history().add_turn("assistant", "A的回复", source="chat")
    check("1 A 桶写入 2 轮", len(CH.shared_history().get_recent(10)) == 2)

    # 2) 切到 B → B 桶应为空（不串 A 的内容）
    CH.set_active_char("bob")
    check("2 切到 B 不串 A 的内容", CH.shared_history().get_recent(10) == [])

    # 3) B 写入 → 只有 B 的内容
    CH.shared_history().add_turn("user", "B说的话", source="chat")
    _b = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("3 B 桶只含 B 内容", _b == ["B说的话"], str(_b))

    # 4) 切回 A → A 的历史**原样恢复**（旧实现这里已被 clear 抹掉 = 数据丢失）
    CH.set_active_char("alice")
    _a = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("4 切回 A 上下文恢复（非破坏性）", _a == ["A说的话", "A的回复"], str(_a))

    # 5) A / B 是两个不同对象（桶隔离）
    CH.set_active_char("alice")
    _ha = CH.shared_history()
    CH.set_active_char("bob")
    _hb = CH.shared_history()
    check("5 A/B 为不同历史对象", _ha is not _hb)

    # 6) clear 只清当前桶（B），A 不受影响
    CH.set_active_char("bob")
    CH.shared_history().clear()
    check("6a clear 后 B 桶为空", CH.shared_history().get_recent(10) == [])
    CH.set_active_char("alice")
    check("6b clear 未误伤 A 桶",
          [t["content"] for t in CH.shared_history().get_recent(10)] == ["A说的话", "A的回复"])

    # 7) 未注册角色（"" 通用桶）与具名角色互不混淆
    CH.set_active_char("")
    CH.shared_history().add_turn("user", "通用桶内容", source="chat")
    CH.set_active_char("alice")
    _a2 = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("7 通用桶与具名桶隔离", "通用桶内容" not in _a2, str(_a2))

    # 8) 双工写入（source=duplex）也落在当前角色桶
    CH.set_active_char("bob")
    CH.shared_history().add_turn("assistant", "双工开口", source="duplex")
    _b2 = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("8 双工写入落当前角色桶", _b2 == ["双工开口"], str(_b2))

    # 9) all_histories() 反映各桶
    _all = CH.all_histories()
    check("9 all_histories 含 alice/bob/通用桶",
          {"alice", "bob", ""} <= set(_all.keys()), str(list(_all.keys())))

    # 10) 兼容性：get_active_char 可读回
    CH.set_active_char("alice")
    check("10 get_active_char 回读", CH.get_active_char() == "alice")

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
