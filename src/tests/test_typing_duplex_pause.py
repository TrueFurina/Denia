# -*- coding: utf-8 -*-
"""打字时本地双工暂停 / 恢复 接线回归（2026-10-24）。

修复前：_note_user_typing 只记 _duplex_user_typing_ts（供云端 VAD 抬阈），
从没调过 pause()、从没置 _duplex_paused_for_typing —— 本地全双工那套
「打字时暂停麦流上行、8s 后恢复」整条死掉（写了没接线）。

本测试直接驱动 Proactive 的两个相关方法（用 fake self，不构造完整 Proactive）：
  [1] 打字 → 带 pause 的本地双工被 pause() 一次、标志置 True、计时戳刷新
  [2] 重复按键不重复 pause（幂等）
  [3] 双工无 pause 方法（云端/其它）→ 不误暂停
  [4] _duplex_client 为 None → 不误暂停
  [5] 距末次按键 < 8s → 不恢复
  [6] 距末次按键 ≥ 8s → resume() 一次、标志清回 False
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

FAIL = 0


def check(cond, msg):
    global FAIL
    if cond:
        print("  ✓", msg)
    else:
        FAIL += 1
        print("  ✗ FAIL:", msg)


# 直接拿方法，不需要构造完整 Proactive（模块级 logger / 常量可直接用）
from src.companion import proactive


class FakeDuplex:
    """带 pause/resume 计数与 running 标志的本地双工替身。"""

    def __init__(self):
        self.running = True
        self.pause_calls = 0
        self.resume_calls = 0

    def pause(self):
        self.pause_calls += 1

    def resume(self):
        self.resume_calls += 1


class PlainDuplexNoPause:
    """没有 pause 方法的双工（模拟云端/其它双工）。"""

    def __init__(self):
        self.running = True


class FakeSelf:
    """供 unbound 方法调用的最小 self。"""

    def __init__(self, duplex):
        self._duplex_client = duplex
        self._duplex_paused_for_typing = False
        self._duplex_pause_ts = 0.0
        self._duplex_user_typing_ts = 0.0


def test_note_typing_pauses():
    print("[1] 打字 → 本地双工被 pause 一次 + 标志置 True + 计时戳刷新")
    d = FakeDuplex()
    fs = FakeSelf(d)
    proactive.ProactiveStrategy._note_user_typing(fs)
    check(d.pause_calls == 1, "带 pause 的本地双工被 pause() 一次")
    check(fs._duplex_paused_for_typing is True, "_duplex_paused_for_typing 置 True")
    check(fs._duplex_pause_ts > 0.0, "_duplex_pause_ts 已刷新")
    check(fs._duplex_user_typing_ts > 0.0, "_duplex_user_typing_ts 仍记录（云端 VAD 抬阈路径）")


def test_note_typing_idempotent():
    print("[2] 重复按键不重复 pause（幂等）")
    d = FakeDuplex()
    fs = FakeSelf(d)
    proactive.ProactiveStrategy._note_user_typing(fs)
    proactive.ProactiveStrategy._note_user_typing(fs)
    proactive.ProactiveStrategy._note_user_typing(fs)
    check(d.pause_calls == 1, "多次按键只 pause() 一次")


def test_no_pause_method_safe():
    print("[3] 双工无 pause 方法 → 不误暂停")
    d = PlainDuplexNoPause()
    fs = FakeSelf(d)
    proactive.ProactiveStrategy._note_user_typing(fs)
    check(fs._duplex_paused_for_typing is False, "无 pause 方法时不置暂停标志")


def test_no_duplex_client_safe():
    print("[4] _duplex_client 为 None → 不误暂停")
    fs = FakeSelf(None)
    proactive.ProactiveStrategy._note_user_typing(fs)
    check(fs._duplex_paused_for_typing is False, "无双工客户端时不置暂停标志")


def test_resume_not_yet():
    print("[5] 距末次按键 < 8s → 不恢复")
    d = FakeDuplex()
    fs = FakeSelf(d)
    fs._duplex_paused_for_typing = True
    fs._duplex_pause_ts = time.time() - 1.0  # 仅 1s 前
    proactive.ProactiveStrategy._maybe_resume_typing_paused_duplex(fs)
    check(d.resume_calls == 0, "未到 8s 不 resume()")
    check(fs._duplex_paused_for_typing is True, "未到 8s 标志保持 True")


def test_resume_after_8s():
    print("[6] 距末次按键 ≥ 8s → resume() 一次 + 标志清回 False")
    d = FakeDuplex()
    fs = FakeSelf(d)
    fs._duplex_paused_for_typing = True
    fs._duplex_pause_ts = time.time() - (proactive._DUPLEX_TYPING_RESUME_S + 1.0)
    proactive.ProactiveStrategy._maybe_resume_typing_paused_duplex(fs)
    check(d.resume_calls == 1, "到 8s 后 resume() 一次")
    check(fs._duplex_paused_for_typing is False, "恢复后标志清回 False")


if __name__ == "__main__":
    test_note_typing_pauses()
    test_note_typing_idempotent()
    test_no_pause_method_safe()
    test_no_duplex_client_safe()
    test_resume_not_yet()
    test_resume_after_8s()
    print()
    if FAIL:
        print(f"❌ {FAIL} 项失败")
        sys.exit(1)
    print("✅ 全部通过（打字双工暂停/恢复 接线）")
