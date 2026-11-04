"""UI 心跳恢复策略（**纯状态机**，不碰 Win32 / COM / 窗口）。

【2026-11-04 加·P0-2「她一直在」】
看门狗发现前端心跳中断后，原来只有两条路：记日志（默认）或 `UI_RELOAD_RECOVER=1`
时尝试 Reload。前者等于"界面死了就再也回不来"，后者没有次数上限、没有冷却之外
的整体预算，容易变成重载风暴。

这个模块只回答一个问题：**现在该不该再试一次恢复**，返回决策字符串，由调用方
（`WindowAPI._ui_watchdog_loop`）去执行。COM 访问一律留在 GUI 线程里
（见 `tools/check_ui_reload_boundary.py` 的静态闸门）。

不变量：
  1. 从未收到首跳 → 不判无响应（预览/未连接）。
  2. 启动宽限期内（模型加载会饿前端心跳）只记日志，绝不重载。
  3. 一次"无响应事件"最多试 N 次；到顶就明确放弃，不再刷屏也不再重载。
  4. 两次尝试之间必须隔够冷却时间。
  5. 心跳一恢复，整个事件清零（下次是全新一轮）。

不进闸门：无（对应 tools/test_ui_heartbeat.py）
"""
from __future__ import annotations


class HeartbeatRecoveryPolicy:
    """心跳恢复的判定与预算。所有时间由调用方传入，便于离线确定性测试。"""

    def __init__(
        self,
        timeout: float = 30.0,
        grace: float = 120.0,
        cooldown: float = 60.0,
        max_attempts: int = 2,
        enabled: bool = True,
    ) -> None:
        self.timeout = float(timeout)
        self.grace = float(grace)
        self.cooldown = float(cooldown)
        self.max_attempts = max(0, int(max_attempts))
        self.enabled = bool(enabled)
        self.attempts = 0
        self.last_attempt = 0.0
        self._give_up_logged = False

    def on_ping(self, now: float) -> None:
        """心跳恢复：清空这一轮无响应事件的全部状态。"""
        self.attempts = 0
        self.last_attempt = 0.0
        self._give_up_logged = False

    def check(
        self,
        now: float,
        last_ping: float,
        armed: bool,
        boot_ts: float,
    ) -> tuple[str, str]:
        """返回 (决策, 原因)。决策 ∈ idle/healthy/grace/disabled/cooldown/give_up/reload。"""
        if not armed:
            return ("idle", "尚未收到首跳")
        gap = now - last_ping
        if gap <= self.timeout:
            self.on_ping(now)
            return ("healthy", "心跳正常（间隔 %.0fs）" % gap)
        if now - boot_ts < self.grace:
            return ("grace", "启动宽限期内（%.0fs/%0.fs），只记日志不重载"
                    % (now - boot_ts, self.grace))
        if not self.enabled:
            return ("disabled", "自动恢复按配置关闭")
        if self.attempts >= self.max_attempts:
            return ("give_up", "本轮已试 %d 次仍无心跳，停止自动恢复" % self.attempts)
        if self.last_attempt and now - self.last_attempt < self.cooldown:
            return ("cooldown", "冷却中（距上次 %.0fs / %.0fs）"
                    % (now - self.last_attempt, self.cooldown))
        self.attempts += 1
        self.last_attempt = now
        return ("reload", "第 %d/%d 次尝试恢复" % (self.attempts, self.max_attempts))

    def should_log_give_up(self) -> bool:
        """放弃只打一条日志，别每 5 秒刷一遍。"""
        if self._give_up_logged:
            return False
        self._give_up_logged = True
        return True
