"""活动日志接入 + 日报/周报自动调度（参照 pub-local-jarvis 的本地记忆编排）。

- ActivityReporter.subscribe() 订阅事件总线：屏幕活动 / 前台切换 / 对话 / 游戏事件，
  经 ActivityLogger 过滤后写入 ActivityStore（events.jsonl）。
- ActivityReporter.run() 后台循环：检测到新的一天 → 为昨天生成日报；
  检测到新的一周 → 为上周生成周报（全自动，无需用户操作）。
- 原始截图/音频不落盘，只存提炼文本事件（隐私友好，同 pub-local-jarvis）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from ..event_bus import Event, EventBus, EventType
from .activity_log import (
    ActivityLogger,
    ActivityStore,
    generate_daily_report,
    generate_weekly_report,
)

logger = logging.getLogger(__name__)

# 日报/周报 LLM 生成的超时与参数
_REPORT_OPTIONS: dict[str, Any] = {"temperature": 0.5, "num_predict": 2000}


class ActivityReporter:
    """事件订阅 + 日报/周报定时调度。"""

    def __init__(
        self,
        bus: EventBus,
        cfg: Any,
        llm: Any,
        store: Optional[ActivityStore] = None,
    ) -> None:
        self._bus = bus
        self._cfg = cfg
        self._llm = llm
        self._store = store or ActivityStore()
        self._logger = ActivityLogger(self._store)
        self._last_day: Optional[str] = None
        self._last_week: Optional[str] = None
        self._gen_lock = asyncio.Lock()

    # ---------- 事件接入 ----------
    def subscribe(self) -> None:
        self._bus.subscribe(EventType.SCREEN_ACTIVITY, self._on_screen_activity)
        self._bus.subscribe(EventType.FOREGROUND_CHANGED, self._on_foreground_changed)
        self._bus.subscribe(EventType.USER_TEXT, self._on_user_text)
        self._bus.subscribe(EventType.AI_DECISION, self._on_ai_decision)

    async def _on_screen_activity(self, e: Event) -> None:
        d = e.data or {}
        if not isinstance(d, dict):
            return
        # 屏幕活动摘要 → screen 事件（置信度/相似度/时间窗口过滤在 ActivityLogger 内）
        self._logger.handle_screen(
            str(d.get("summary") or ""),
            app_hint=str(d.get("app_hint") or ""),
            confidence=float(d.get("confidence") or 0.0),
        )
        # 游戏关键时刻（击杀/阵亡/撤离成功等，OCR 引信）→ game 事件
        moment = (d.get("moment") or "").strip()
        if moment:
            self._logger.handle_game(f"游戏事件：{moment}", {"app_hint": "game"})

    async def _on_foreground_changed(self, e: Event) -> None:
        d = e.data or {}
        if not isinstance(d, dict):
            return
        self._logger.handle_foreground(
            str(d.get("title") or ""), app_hint=str(d.get("app_hint") or "")
        )

    async def _on_user_text(self, e: Event) -> None:
        d = e.data or {}
        if not isinstance(d, dict):
            return
        # 【2026-10-23】两条闸门，都与本类 AI 侧的处理对齐：
        # ① 双工通话里用户说的话不进活动日志的"聊天"流：它已由 `_publish_call_record`
        #    发 CALL_SPEECH 进独立通话记录通道（docstring 原话"通话记录与聊天流彻底
        #    分离…不进聊天页"）。混进来会让日报把通话原话当成打字聊天，且
        #    `shared_history(source="duplex")` 里已有同一份。
        # ② 主动搭话（is_proactive）不在此记：那是**她说的**，不是用户说的。
        #    `_on_ai_decision` 已经按 `direction="ai_proactive"` 记过一次——
        #    在这里再记就会即错标成"用户说的"、又重复记一条。
        if str(d.get("source") or "") == "duplex":
            return
        if d.get("is_proactive"):
            return
        self._logger.handle_chat(str(d.get("text") or ""), direction="user")

    async def _on_ai_decision(self, e: Event) -> None:
        d = e.data or {}
        if not isinstance(d, dict):
            return
        text = (d.get("text") or "").strip()
        if not text or d.get("blocked"):
            # blocked：质量门拦截的乱语，不进活动日志/日报
            return
        # 主动搭话/回应都在 AI_DECISION；按是否自己发起（is_proactive）标记来源
        direction = "ai_proactive" if d.get("is_proactive") else "ai"
        self._logger.handle_chat(text, direction=direction)

    # ---------- 日报/周报调度 ----------
    def llm_fn(self, prompt: str) -> Any:
        """把 LLM 客户端 chat 包装成语义调用（_call_llm 会 await）。"""
        if self._llm is None:
            raise RuntimeError("LLM 不可用")
        return self._llm.chat([{"role": "user", "content": prompt}], _REPORT_OPTIONS)

    async def run(self) -> None:
        """后台调度循环：每分钟检查跨天/跨周。"""
        while True:
            try:
                await self._check_reports()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("日报/周报调度检查失败")
            await asyncio.sleep(60)

    async def _check_reports(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self._last_day != today:
            self._last_day = today
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            await self._generate_daily(yesterday)
        # 周维度：以周一日期标识；跨周 → 为上周生成周报
        this_monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        if self._last_week != this_monday:
            self._last_week = this_monday
            last_monday = (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%d")
            await self._generate_weekly(last_monday)

    async def _generate_daily(self, day: str) -> None:
        async with self._gen_lock:
            # 已生成过则跳过（read_daily 存在）
            if self._store.read_daily(day):
                return
            try:
                await asyncio.wait_for(
                    generate_daily_report(self._store, self.llm_fn, day),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.warning("日报生成超时（%s），稍后重试", day)
            except Exception:  # noqa: BLE001
                logger.exception("日报生成失败（%s）", day)

    async def _generate_weekly(self, monday: str) -> None:
        async with self._gen_lock:
            wk = datetime.strptime(monday, "%Y-%m-%d").strftime("%Y-W%W")
            if self._store.read_weekly(wk):
                return
            try:
                await asyncio.wait_for(
                    generate_weekly_report(self._store, self.llm_fn, monday),
                    timeout=180,
                )
            except asyncio.TimeoutError:
                logger.warning("周报生成超时（%s），稍后重试", monday)
            except Exception:  # noqa: BLE001
                logger.exception("周报生成失败（%s）", monday)

    # ---------- 工具 ----------
    def recent_events(self, limit: int = 20) -> list[dict]:
        """最近 N 条事件（UI 展示用）。"""
        return self._store.events()[-limit:]
