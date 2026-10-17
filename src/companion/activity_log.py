"""活动日志 + 日报/周报：记录用户每天在电脑上做了什么（参照 pub-local-jarvis 的本地记忆设计）。

原理（对齐 pub-local-jarvis memory/store.py + orchestrator 记忆策略）：
- 感知结果（屏幕摘要 / 前台应用 / 对话 / 游戏事件）经【置信度 + 相似度 + 时间窗口】过滤后，
  作为事件追加进 events.jsonl（append-only，原子写入，崩溃安全）。
- 原始截图 / 音频绝不写入长期记忆，只存提炼后的文本事件（隐私友好）。
- 日报 / 周报由外部 LLM 函数把事件归并为【带起止时间的时间轴】，输出 Markdown 到 daily/、weekly/。

数据布局（项目 data/activity/）：
  events.jsonl        事件日志（id/timestamp/kind/text/metadata）
  daily/YYYY-MM-DD.md 日报（每天自动生成）
  weekly/YYYY-WW.md   周报（每周日自动生成）
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# 事件类型（kind）
KIND_SCREEN = "screen"          # 屏幕活动（感知摘要，内容变化才记）
KIND_APP = "app"                # 前台应用切换
KIND_CHAT = "chat"              # 与 AI 的对话
KIND_GAME = "game"              # 游戏关键事件（击杀/阵亡/撤离等）
KIND_COURSE = "course"          # 网课记录事件
KIND_ACTIVE = "active"          # 活跃/空闲状态变化

# 相似度阈值：与最近同 kind 事件文本相似度高于此值 → 视为重复，不记
SIMILAR_THRESHOLD = 0.82
# 同 kind 事件最短间隔（秒）：防止高频感知刷爆日志（屏幕内容稳定时）
MIN_INTERVAL_S = 90
# 日报/周报生成用的时间格式化
_DATE_FMT = "%Y-%m-%d"
_TIME_FMT = "%H:%M"


def _now_ts() -> float:
    return time.time()


def _fmt_ts(ts: float) -> str:
    """ISO 格式时间戳（本地时间）。"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def _fmt_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime(_TIME_FMT)


def _similar(a: str, b: str) -> float:
    """文本相似度（difflib），用于去重。"""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()


def _atomic_json(path: str, value: Any) -> None:
    """原子写 JSON（临时文件 + os.replace），崩溃安全。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_text(path: str, text: str) -> None:
    """原子写文本。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.rstrip() + "\n")
        os.fsync(f.fileno())
    os.replace(tmp, path)


class ActivityStore:
    """事件日志 + 日报/周报存储（线程安全）。"""

    def __init__(self, root: str = "") -> None:
        if not root:
            # 默认存到项目 data/activity/
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            root = os.path.join(base, "data", "activity")
        self.root = root
        self.events_path = os.path.join(root, "events.jsonl")
        self.daily_root = os.path.join(root, "daily")
        self.weekly_root = os.path.join(root, "weekly")
        os.makedirs(self.daily_root, exist_ok=True)
        os.makedirs(self.weekly_root, exist_ok=True)
        self._lock = threading.Lock()

    # ---------- 事件写入 ----------
    def append(
        self,
        kind: str,
        text: str,
        metadata: Optional[Mapping[str, Any]] = None,
        *,
        timestamp: Optional[float] = None,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """追加一条事件。kind/text 空则忽略。返回事件 dict（供调用方回读）。"""
        kind = (kind or "").strip()
        text = (text or "").strip()
        if not kind or not text:
            return None
        event = {
            "id": event_id or uuid4().hex,
            "timestamp": _fmt_ts(timestamp if timestamp is not None else _now_ts()),
            "kind": kind,
            "text": text,
            "metadata": dict(metadata or {}),
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            os.makedirs(os.path.dirname(self.events_path), exist_ok=True)
            with open(self.events_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        return event

    def events(self) -> list[dict[str, Any]]:
        """读取全部事件（时间升序）。坏行跳过。"""
        if not os.path.exists(self.events_path):
            return []
        result: list[dict[str, Any]] = []
        with open(self.events_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("活动日志跳过坏行: %r", line[:60])
        return result

    def events_for_day(self, day: str) -> list[dict[str, Any]]:
        """取某天（YYYY-MM-DD）的事件，时间升序。"""
        prefix = day + "T"
        return [e for e in self.events() if e.get("timestamp", "").startswith(prefix)]

    def last_event(self) -> Optional[dict[str, Any]]:
        """最近一条事件（无则 None）。"""
        events = self.events()
        return events[-1] if events else None

    # ---------- 日报 / 周报 ----------
    def daily_path(self, day: str) -> str:
        return os.path.join(self.daily_root, f"{day}.md")

    def write_daily(self, day: str, content: str) -> str:
        path = self.daily_path(day)
        _atomic_text(path, content)
        return path

    def read_daily(self, day: str) -> Optional[str]:
        path = self.daily_path(day)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return None

    def daily_days(self) -> list[str]:
        """已有日报的日期列表（倒序）。"""
        days = []
        if os.path.isdir(self.daily_root):
            for name in os.listdir(self.daily_root):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", name):
                    days.append(name[:-3])
        return sorted(days, reverse=True)

    def weekly_path(self, week: str) -> str:
        return os.path.join(self.weekly_root, f"{week}.md")

    def write_weekly(self, week: str, content: str) -> str:
        path = self.weekly_path(week)
        _atomic_text(path, content)
        return path

    def read_weekly(self, week: str) -> Optional[str]:
        path = self.weekly_path(week)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return None

    def weekly_weeks(self) -> list[str]:
        weeks = []
        if os.path.isdir(self.weekly_root):
            for name in os.listdir(self.weekly_root):
                if re.fullmatch(r"\d{4}-W\d{2}\.md", name):
                    weeks.append(name[:-3])
        return sorted(weeks, reverse=True)

    # ---------- 统计辅助（供 LLM 归并时附上客观数据） ----------
    def stats_for_day(self, day: str) -> dict[str, Any]:
        """当天客观统计：各 kind 条数、应用/游戏/对话分布（LLM 归并的"事实底料"）。"""
        events = self.events_for_day(day)
        stats: dict[str, Any] = {
            "event_count": len(events),
            "kinds": {},
            "apps": [],
            "games": [],
        }
        apps: dict[str, int] = {}
        games: dict[str, int] = {}
        for e in events:
            kind = e.get("kind", "?")
            stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1
            meta = e.get("metadata") or {}
            app = str(meta.get("app_hint", "") or "")
            if app:
                apps[app] = apps.get(app, 0) + 1
            game = str(meta.get("game", "") or "")
            if game:
                games[game] = games.get(game, 0) + 1
        stats["apps"] = sorted(apps.items(), key=lambda x: -x[1])
        stats["games"] = sorted(games.items(), key=lambda x: -x[1])
        return stats


class ActivityLogger:
    """订阅事件总线 → 过滤/聚合 → 写入 ActivityStore。

    过滤规则（对齐 pub-local-jarvis：置信度 + 画面变化 + 时间窗口 + 相似度）：
    - 屏幕活动：内容与最近记录相似度 > 阈值 → 跳过（画面稳定不刷屏）；
      内容变化才记，且同 kind 最小间隔 MIN_INTERVAL_S（防止高频抖动）。
    - 置信度过低 → 跳过。
    - 前台应用切换 → 记 app 事件（含应用名 + 窗口标题）。
    - 对话 / 游戏关键事件直接记。
    """

    def __init__(self, store: ActivityStore) -> None:
        self._store = store
        self._last: dict[str, dict] = {}  # kind -> {"text":.., "ts":..}

    def handle_screen(self, summary: str, *, app_hint: str = "", confidence: float = 1.0,
                      game: str = "") -> Optional[dict]:
        """屏幕活动感知结果。summary 为空 / 置信度太低 / 与上次相似 → 跳过。"""
        summary = (summary or "").strip()
        if not summary or confidence < 0.4:
            return None
        prev = self._last.get(KIND_SCREEN)
        now = _now_ts()
        if prev:
            # 内容相似 → 只刷新时间不记录（画面稳定）
            if _similar(prev.get("text", ""), summary) > SIMILAR_THRESHOLD:
                prev["ts"] = now
                return None
            # 内容变了但距上次太近 → 也跳过（防抖动）
            if now - prev.get("ts", 0) < MIN_INTERVAL_S:
                return None
        meta: dict[str, Any] = {}
        if app_hint:
            meta["app_hint"] = app_hint
        if game:
            meta["game"] = game
        event = self._store.append(KIND_SCREEN, summary, meta)
        if event:
            self._last[KIND_SCREEN] = {"text": summary, "ts": now}
        return event

    def handle_foreground(self, title: str, app_hint: str = "") -> Optional[dict]:
        """前台窗口切换。标题空或与上次相同 → 跳过。"""
        title = (title or "").strip()
        if not title:
            return None
        prev = self._last.get(KIND_APP)
        if prev and _similar(prev.get("text", ""), title) > SIMILAR_THRESHOLD:
            return None
        meta: dict[str, Any] = {}
        if app_hint:
            meta["app_hint"] = app_hint
        text = f"{app_hint}：{title}" if app_hint else title
        event = self._store.append(KIND_APP, text, meta)
        if event:
            self._last[KIND_APP] = {"text": title, "ts": _now_ts()}
        return event

    def handle_chat(self, text: str, *, direction: str = "user") -> Optional[dict]:
        """对话事件（direction: user/ai）。"""
        text = (text or "").strip()
        if not text:
            return None
        return self._store.append(KIND_CHAT, text, {"direction": direction})

    def handle_game(self, text: str, meta: Optional[Mapping[str, Any]] = None) -> Optional[dict]:
        text = (text or "").strip()
        if not text:
            return None
        return self._store.append(KIND_GAME, text, meta)


# ---------- LLM 归并生成日报/周报 ----------
# 注入的 llm_fn(prompt) -> str（async），返回 Markdown 正文。
SummarizeFn = Callable[[str], Any]

DAILY_PROMPT = """你在整理用户 {day} 的电脑使用日志，生成一份**日报**（Markdown）。

以下是当天按时间顺序记录的【事件】（屏幕活动 / 应用切换 / 对话 / 游戏）：

{events}

当天客观统计：
{stats}

要求：
1. 把零散事件归并为**带起止时间和大致时长的时间轴**（时间段：活动内容）。
2. 用自然语言概括用户这天主要在做什么、节奏如何、有没有值得说的亮点（比如学习/游戏战绩/专注工作）。
3. 结构：标题「# {day} 日报」→「## 时间轴」→「## 一天小结」。
4. 语气像贴心 AI 伴侣在回顾这一天，口语化、有人情味，别写成流水账；时长用"X分钟/X小时"。
5. 只输出 Markdown 正文，不要代码块包裹。

{extra}"""

WEEKLY_PROMPT = """你在整理用户这一周（{start} ~ {end}）的电脑使用日志，生成一份**周报**（Markdown）。

以下是本周每天的日报内容：

{dailies}

要求：
1. 汇总本周**总体节奏**：主要时间花在哪类事情上（工作/学习/游戏/社交）、有没有明显变化或规律。
2. 挑出本周**亮点/值得说的**（新学习的内容、游戏高光、专注时段）。
3. 结构：标题「# {start} ~ {end} 周报」→「## 本周概览」→「## 时间分布」→「## 本周亮点」→「## 下周小建议」。
4. 语气温和亲切，像朋友的每周闲聊回顾，别写成干巴巴的报告。
5. 只输出 Markdown 正文，不要代码块包裹。"""


def build_events_text(events: list[dict[str, Any]]) -> str:
    """事件列表 → LLM 可读文本（含时间）。"""
    lines = []
    for e in events:
        ts = e.get("timestamp", "")
        hm = ts[11:16] if len(ts) >= 16 else ts
        lines.append(f"[{hm}] ({e.get('kind', '?')}) {e.get('text', '')}")
    return "\n".join(lines)


def build_stats_text(stats: dict[str, Any]) -> str:
    """客观统计 → LLM 附料。"""
    parts = [f"- 事件总数：{stats.get('event_count', 0)}"]
    apps = stats.get("apps") or []
    if apps:
        parts.append("- 常用应用：" + "、".join(f"{name}({cnt}次)" for name, cnt in apps[:5]))
    games = stats.get("games") or []
    if games:
        parts.append("- 游戏：" + "、".join(f"{name}({cnt}次)" for name, cnt in games[:5]))
    return "\n".join(parts)


async def generate_daily_report(store: ActivityStore, llm_fn: SummarizeFn, day: str) -> Optional[str]:
    """生成某天日报并写入。当天无事件 → 返回 None。"""
    events = store.events_for_day(day)
    if not events:
        return None
    prompt = DAILY_PROMPT.format(
        day=day,
        events=build_events_text(events),
        stats=build_stats_text(store.stats_for_day(day)),
        extra="",
    )
    try:
        content = (await _call_llm(llm_fn, prompt) or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("日报生成失败（%s）", day)
        return None
    if not content:
        return None
    store.write_daily(day, content)
    logger.info("日报已生成: %s", day)
    return content


async def generate_weekly_report(store: ActivityStore, llm_fn: SummarizeFn, monday: str) -> Optional[str]:
    """生成某周（周一日期 YYYY-MM-DD）周报并写入。该周无日报 → 返回 None。"""
    dailies = []
    start_dt = datetime.strptime(monday, _DATE_FMT)
    for i in range(7):
        day = (start_dt + timedelta(days=i)).strftime(_DATE_FMT)
        content = store.read_daily(day)
        if content:
            dailies.append(f"## {day}\n{content}")
    if not dailies:
        return None
    end_day = (start_dt + timedelta(days=6)).strftime(_DATE_FMT)
    week = start_dt.strftime("%Y-W%W")
    prompt = WEEKLY_PROMPT.format(
        start=monday, end=end_day, dailies="\n\n".join(dailies)
    )
    try:
        content = (await _call_llm(llm_fn, prompt) or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("周报生成失败（%s）", week)
        return None
    if not content:
        return None
    store.write_weekly(week, content)
    logger.info("周报已生成: %s", week)
    return content


async def _call_llm(llm_fn: SummarizeFn, prompt: str) -> Optional[str]:
    """统一调用注入的 LLM 函数（兼容 async / 同步）。"""
    result = llm_fn(prompt)
    if hasattr(result, "__await__"):
        result = await result
    return str(result) if result else None
