"""UI 心跳恢复策略（**纯状态机**，不碰 Win32 / COM / 窗口）。

【2026-11-04 加·P0-2「她一直在」】
看门狗发现前端心跳中断后，原来只有两条路：记日志（默认）或 `UI_RELOAD_RECOVER=1`
时尝试 Reload。前者等于"界面死了就再也回不来"，后者没有次数上限、容易重载风暴。

【2026-11-04 二修·节流误判】launch_run16.log 实测：心跳**每 60s 回来一次**
（36 次「无响应约 60s 后重新连接」），「首次判定无响应」也精确每分钟一次。
这是 Chromium/WebView2 对隐藏/最小化页面 `setInterval` 的后台定时器节流，
**不是渲染进程挂死**。按 30s 阈值判死会每分钟重载一次窗口，比不恢复更糟。

所以改成两阶段阈值：
  · gap ≤ timeout(30s)        → healthy
  · timeout < gap < dead(180s) → slow：心跳变慢但仍会回来，只告警、绝不重载
  · gap ≥ dead(180s)          → 真判死，才进入有预算的恢复流程

180s 的依据：实测节流周期 60s，留 3 倍余量；渲染进程真死时 gap 会一直增长，
必然越过 180s。这样两种情形都能正确区分，且不需要碰 Win32/COM/页面可见性。

这个模块只回答"现在该不该再试一次恢复"，COM 访问一律留在 GUI 线程里
（见 `tools/check_ui_reload_boundary.py` 的静态闸门）。

不变量：
  1. 从未收到首跳 → 不判无响应（预览/未连接）。
  2. 心跳变慢（slow）不算死，不消耗恢复预算、不重载。
  3. 启动宽限期内（模型加载会饿前端心跳）只记日志，绝不重载。
  4. 一次"真判死事件"最多试 N 次；到顶明确放弃，不刷屏也不再重载。
  5. 两次尝试之间必须隔够冷却时间。
  6. 心跳一恢复，整个事件清零（下次是全新一轮）。

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
        dead_timeout: float = 180.0,
    ) -> None:
        self.timeout = float(timeout)
        self.grace = float(grace)
        self.cooldown = float(cooldown)
        self.max_attempts = max(0, int(max_attempts))
        self.enabled = bool(enabled)
        # 真判死阈值必须大于 timeout，否则 slow 这一档永远到不了
        self.dead_timeout = max(float(dead_timeout), self.timeout + 1.0)
        self.attempts = 0
        self.last_attempt = 0.0
        self._give_up_logged = False
        self._slow_logged = False

    def on_ping(self, now: float) -> None:
        """心跳恢复：清空这一轮无响应事件的全部状态。"""
        self.attempts = 0
        self.last_attempt = 0.0
        self._give_up_logged = False
        self._slow_logged = False

    def check(
        self,
        now: float,
        last_ping: float,
        armed: bool,
        boot_ts: float,
    ) -> tuple[str, str]:
        """返回 (决策, 原因)。

        决策 ∈ idle/healthy/slow/grace/disabled/cooldown/give_up/reload。
        只有 reload 会让调用方去动窗口。
        """
        if not armed:
            return ("idle", "尚未收到首跳")
        gap = now - last_ping
        if gap <= self.timeout:
            self.on_ping(now)
            return ("healthy", "心跳正常（间隔 %.0fs）" % gap)
        # 启动宽限期优先于 slow：模型加载会饿前端心跳，这段时间内
        # 无论 gap 多大都只判 grace（只记日志、绝不重载），连「心跳变慢」标签都不打。
        if now - boot_ts < self.grace:
            return ("grace", "启动宽限期内（%.0fs/%0.fs），只记日志不重载"
                    % (now - boot_ts, self.grace))
        if gap < self.dead_timeout:
            return ("slow",
                    "心跳间隔 %.0fs（>%.0fs 但 <%.0fs）：判为后台定时器节流，不算 UI 卡死"
                    % (gap, self.timeout, self.dead_timeout))
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

    def should_log_slow(self) -> bool:
        """节流提示一轮只打一条：60s 周期会反复进入 slow，别刷屏。"""
        if self._slow_logged:
            return False
        self._slow_logged = True
        return True
