# -*- coding: utf-8 -*-
"""双工过渡期看门狗接线回归（2026-10-23）。

背景（本次修的真缺陷）：`_duplex_in_transition` 禁言闸门会把 _on_duplex_speak 的
开口全丢弃（2001/3336），正常出口只有 connected/prepared（2989/3047）。重连卡在
"正在重连"类状态、prepared 永不到达时，她将**永久静默且零报错**。看门狗方法
09-08 就写好了，但**从来没有启动点**——2912 的注释却一直承诺"配合看门狗兜底"。

守六件事：
  [1] 闸门没起 → 不动（对照组：不许误清正常状态）
  [2] 闸门起着、没到 20s → 不动（对照组：正常重连等待窗口内不许强行放行，
      否则禁言闸门形同虚设）
  [3] 卡过 20s → 强制解除禁言 + kind/name 复位 + 通知connected（真缺陷的修复面）
  [4] 没记时间戳（since=0）→ 不动（老状态安全默认；也说明 [5] 的接线断言不可省）
  [5] 接线源码断言：闸门拉起处记时间戳 + 5s 诊断 tick 里真的调看门狗
      （配变异刀：抽掉任一处必须红）
  [6] 墓碑：cloud_omni_realtime 的死包装 _reopen_stream 不许复活（锁不可重入，
      真调用方都在持锁状态下直接调 _reopen_stream_locked）
"""
from __future__ import annotations

import io
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from src.companion import proactive as P  # noqa: E402
from src.ui import mode_notify as MN  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


class _Stub:
    pass


def _stub(in_transition, since):
    s = _Stub()
    s._duplex_in_transition = in_transition
    s._duplex_transition_since = since
    s._duplex_transition_kind = "reconnect"
    s._duplex_transition_name = "云端"
    return s


def _run_with_recorder(stub):
    """跑一次看门狗，返回通知调用记录。"""
    calls = []
    orig = MN.update_mode_notify
    MN.update_mode_notify = lambda **kw: calls.append(kw)
    try:
        P.ProactiveStrategy._duplex_force_clear_transition(stub)
    finally:
        MN.update_mode_notify = orig
    return calls


def main():
    fn = P.ProactiveStrategy._duplex_force_clear_transition
    check(callable(fn), "看门狗方法存在且可取到")

    print("[1] 闸门没起 → 不动（对照）")
    s = _stub(False, 0.0)
    calls = _run_with_recorder(s)
    check(s._duplex_in_transition is False and not calls, "没起闸门时不动、不通知")

    print("[2] 闸门起着、没到 20s → 不动（对照）")
    s = _stub(True, time.time())
    calls = _run_with_recorder(s)
    check(s._duplex_in_transition is True and not calls,
          "20s 等待窗口内不强行放行（禁言闸门不许形同虚设）")

    print("[3] 卡过 20s → 强制解除禁言")
    s = _stub(True, time.time() - 25.0)
    calls = _run_with_recorder(s)
    check(s._duplex_in_transition is False, "闸门被强制解除")
    check(s._duplex_transition_kind is None and s._duplex_transition_name == "",
          "kind/name 一并复位")
    check(s._duplex_transition_since == 0.0, "计时起点复位（下次拉起重新武装）")
    check(calls == [{"connected": True}], "通知connected=True（让她回到可说话态）")

    print("[4] 没记时间戳 → 不动")
    s = _stub(True, 0.0)
    calls = _run_with_recorder(s)
    check(s._duplex_in_transition is True and not calls,
          "since=0 时不动（老状态安全默认；接线靠 [5] 断言守）")

    print("[5] 接线源码断言（抽掉任一处，变异刀必须红）")
    src = io.open(os.path.join(ROOT, "src", "companion", "proactive.py"),
                  encoding="utf-8").read()
    raise_ok = re.search(
        r"self\._duplex_in_transition = True\s*\n(?:\s*#[^\n]*\n)*\s*"
        r"self\._duplex_transition_since = time\.time\(\)", src) is not None
    check(raise_ok, "闸门拉起处紧接着记时间戳")
    tick_ok = re.search(
        r"await self\._check_duplex_deadair\(\)\s*\n(?:\s*#[^\n]*\n)*\s*"
        r"self\._duplex_force_clear_transition\(\)", src) is not None
    check(tick_ok, "5s 双工诊断 tick 里紧接着调看门狗")
    init_ok = re.search(
        r"self\._duplex_in_transition = False\s*\n\s*"
        r"self\._duplex_transition_since = 0\.0", src) is not None
    check(init_ok, "__init__ 里计时起点有初始值")

    print("[6] 墓碑：cloud_omni_realtime 死包装不许复活")
    src2 = io.open(os.path.join(ROOT, "src", "llm", "cloud_omni_realtime.py"),
                   encoding="utf-8").read()
    check(re.search(r"def _reopen_stream\(", src2) is None,
          "_reopen_stream（零调用加锁包装）已删")
    check("def _reopen_stream_locked(" in src2, "_reopen_stream_locked 仍在")
    check(src2.count("self._reopen_stream_locked()") >= 2,
          "locked 变体至少有 2 个真调用（set_device 热切换 / _run 写入自愈）")

    if FAIL:
        print("✗ %d 项不符合" % len(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
