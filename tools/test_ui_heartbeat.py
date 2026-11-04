"""UI 心跳恢复策略的判据（纯状态机，时间由用例给，不依赖真实时钟）。

不进闸门：无（已登记 GATES）
"""
from __future__ import annotations

import sys

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.ui.heartbeat_policy import HeartbeatRecoveryPolicy  # noqa: E402

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  ← " + detail) if detail else ""))


def P(**kw) -> HeartbeatRecoveryPolicy:
    base = dict(timeout=30.0, grace=120.0, cooldown=60.0,
                max_attempts=2, enabled=True)
    base.update(kw)
    return HeartbeatRecoveryPolicy(**base)


def main() -> int:
    BOOT = 1000.0

    print("1) 未收到首跳 / 心跳正常")
    p = P()
    d, _ = p.check(BOOT + 10000, 0.0, False, BOOT)
    chk("未 armed → idle（预览/未连接不误报）", d == "idle", d)
    d, _ = p.check(BOOT + 200, BOOT + 190, True, BOOT)
    chk("间隔 10s → healthy", d == "healthy", d)

    print("\n2) 启动宽限期内绝不重载")
    p = P()
    d, why = p.check(BOOT + 100, BOOT + 30, True, BOOT)   # gap=70，启动仅 100s
    chk("宽限期内 → grace", d == "grace", d)
    chk("宽限期没消耗尝试次数", p.attempts == 0, str(p.attempts))
    d, _ = p.check(BOOT + 200, BOOT + 30, True, BOOT)     # gap=170，过了宽限
    chk("过了宽限 → reload", d == "reload", d)

    print("\n3) 冷却 + 次数上限")
    p = P()
    t = BOOT + 200
    d1, _ = p.check(t, t - 100, True, BOOT)
    d2, _ = p.check(t + 10, t - 100, True, BOOT)
    d3, _ = p.check(t + 61, t - 100, True, BOOT)
    d4, _ = p.check(t + 200, t - 100, True, BOOT)
    chk("第一次 reload", d1 == "reload", d1)
    chk("10s 后 cooldown", d2 == "cooldown", d2)
    chk("61s 后第二次 reload", d3 == "reload", d3)
    chk("第三次给到 give_up（不无限重载）", d4 == "give_up", d4)
    chk("总尝试次数 = 2", p.attempts == 2, str(p.attempts))
    chk("放弃日志只打一次",
        p.should_log_give_up() is True and p.should_log_give_up() is False)

    print("\n4) 心跳恢复 → 新一轮预算")
    p = P()
    t = BOOT + 200
    p.check(t, t - 100, True, BOOT)
    p.check(t + 61, t - 100, True, BOOT)
    d, _ = p.check(t + 300, t + 295, True, BOOT)     # 心跳回来
    chk("恢复后 healthy", d == "healthy", d)
    chk("预算清零", p.attempts == 0, str(p.attempts))
    d, _ = p.check(t + 400, t + 295, True, BOOT)     # 又断了
    chk("新一轮可以再 reload", d == "reload", d)

    print("\n5) 关闭开关 / 零预算")
    p = P(enabled=False)
    d, _ = p.check(BOOT + 200, BOOT + 30, True, BOOT)
    chk("disabled 不消耗预算", d == "disabled" and p.attempts == 0, d)
    p = P(max_attempts=0)
    d, _ = p.check(BOOT + 200, BOOT + 30, True, BOOT)
    chk("max_attempts=0 → give_up", d == "give_up", d)

    print("\n6) 边界：刚好等于阈值不算断")
    p = P()
    d, _ = p.check(BOOT + 200, BOOT + 170, True, BOOT)
    chk("gap == timeout → healthy", d == "healthy", d)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
