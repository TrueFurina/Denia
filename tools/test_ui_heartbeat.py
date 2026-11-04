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
        print("  FAIL %s%s" % (name, ("  <- " + detail) if detail else ""))


def P(**kw) -> HeartbeatRecoveryPolicy:
    base = dict(timeout=30.0, grace=120.0, cooldown=60.0,
                max_attempts=2, enabled=True)
    base.update(kw)
    return HeartbeatRecoveryPolicy(**base)


def main() -> int:
    BOOT = 1000.0
    DEAD = 200.0  # 明显大于 dead_timeout(180)，确保进入"真判死"分支

    print("1) 未收到首跳 / 心跳正常")
    p = P()
    d, _ = p.check(BOOT + 10000, 0.0, False, BOOT)
    chk("未 armed -> idle（预览/未连接不误报）", d == "idle", d)
    d, _ = p.check(BOOT + 200, BOOT + 190, True, BOOT)
    chk("间隔 10s -> healthy", d == "healthy", d)

    print("\n2) 启动宽限期内绝不重载")
    p = P()
    d, _ = p.check(BOOT + 100, BOOT + 30, True, BOOT)   # 启动仅 100s，仍在宽限
    chk("宽限期内 -> grace", d == "grace", d)
    chk("宽限期没消耗尝试次数", p.attempts == 0, str(p.attempts))
    d, _ = p.check(BOOT + 210, BOOT + 30, True, BOOT)    # 启动 210s 过宽限，gap=180 真断
    chk("过了宽限且真断 -> reload", d == "reload", d)

    print("\n3) 冷却 + 次数上限（gap 超 dead_timeout）")
    p = P()
    t = BOOT + 210
    d1, _ = p.check(t, t - DEAD, True, BOOT)
    d2, _ = p.check(t + 10, t - DEAD, True, BOOT)
    d3, _ = p.check(t + 61, t - DEAD, True, BOOT)
    d4, _ = p.check(t + 200, t - DEAD, True, BOOT)
    chk("第一次 reload", d1 == "reload", d1)
    chk("10s 后 cooldown", d2 == "cooldown", d2)
    chk("61s 后第二次 reload", d3 == "reload", d3)
    chk("第三次给到 give_up（不无限重载）", d4 == "give_up", d4)
    chk("总尝试次数 = 2", p.attempts == 2, str(p.attempts))
    chk("放弃日志只打一次",
        p.should_log_give_up() is True and p.should_log_give_up() is False)

    print("\n4) 心跳恢复 -> 新一轮预算")
    p = P()
    t = BOOT + 210
    p.check(t, t - DEAD, True, BOOT)          # reload（attempt 1）
    p.check(t + 61, t - DEAD, True, BOOT)     # reload（attempt 2）
    d, _ = p.check(t + 300, t + 295, True, BOOT)   # 心跳回来（gap=5）
    chk("恢复后 healthy", d == "healthy", d)
    chk("预算清零", p.attempts == 0, str(p.attempts))
    d, _ = p.check(t + 500, t + 295, True, BOOT)   # 又断（gap=205）
    chk("新一轮可以再 reload", d == "reload", d)

    print("\n5) 关闭开关 / 零预算")
    p = P(enabled=False)
    d, _ = p.check(BOOT + 210, BOOT + 30, True, BOOT)
    chk("disabled 不消耗预算", d == "disabled" and p.attempts == 0, d)
    p = P(max_attempts=0)
    d, _ = p.check(BOOT + 210, BOOT + 30, True, BOOT)
    chk("max_attempts=0 -> give_up", d == "give_up", d)

    print("\n6) 边界：等于 timeout 不算断 / 等于 dead_timeout 要判死")
    p = P()
    d, _ = p.check(BOOT + 200, BOOT + 170, True, BOOT)
    chk("gap == timeout -> healthy", d == "healthy", d)
    d, _ = p.check(BOOT + 210, BOOT + 30, True, BOOT)   # gap == dead_timeout(180)
    chk("gap == dead_timeout -> 判死（非 slow）", d in ("reload", "give_up"), d)

    print("\n7) 节流带 30s<gap<180s：只判 slow，绝不重载、不消耗预算（二修主路径）")
    p = P()
    d, _ = p.check(BOOT + 260, BOOT + 200, True, BOOT)   # gap=60，模拟 60s 节流
    chk("gap=60s -> slow（不是 reload）", d == "slow", d)
    chk("slow 不消耗尝试次数", p.attempts == 0, str(p.attempts))
    chk("slow 日志一轮只打一条",
        p.should_log_slow() is True and p.should_log_slow() is False)
    d, _ = p.check(BOOT + 350, BOOT + 200, True, BOOT)   # gap=150，仍在节流带
    chk("gap=150s 仍 -> slow", d == "slow", d)
    chk("整段节流期零尝试", p.attempts == 0, str(p.attempts))
    d, _ = p.check(BOOT + 390, BOOT + 200, True, BOOT)   # gap=190 越过 dead_timeout
    chk("gap 越过 180s 才 -> reload", d == "reload", d)
    d, _ = p.check(BOOT + 400, BOOT + 395, True, BOOT)   # 心跳回来
    chk("心跳恢复清 slow 标志（visibilitychange 即时上报的收益）",
        d == "healthy" and p.should_log_slow() is True, d)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
