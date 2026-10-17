"""在线时段计时（P0）——只记"软件开着/在用"，**不读任何内容**。

用途（供主动发言的"路径 A 纯非内容"用，见设计文档 §23.3）：
- 在线时段 → "今晚熬到几点""今天用了多久""连续几天熬夜"；
- 全是**低强度信号**，零隐私（不涉及屏幕内容 / 摄像头）。

落盘：
- `data/sessions.jsonl`      —— 已结束的会话（一行一段）
- `data/sessions_current.json` —— 当前正在进行的会话（进程被杀也不丢）

约定：极端情况下两个文件不一致（如强杀）→ 读到 current 时按"到 last_touch 为止"收尾，
不抛异常、不静默改数据。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

# 夜间窗口（本地时间）：[NIGHT_START, NIGHT_END) 内有活动 = 算"熬夜"。
NIGHT_START_H = 0
NIGHT_END_H = 5
# 会话被判定"已断"的空隙（分钟）：超过则下一次 touch 自动开新会话。
IDLE_SPLIT_MIN = 15.0


def _now() -> float:
    return time.time()


class SessionClock:
    """记录/统计本机在线时段。线程内调用即可，无异步依赖。"""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "data"
        self.base = Path(base_dir)
        self.log_path = self.base / "sessions.jsonl"
        self.cur_path = self.base / "sessions_current.json"

    # ---- 记录 ----

    def start(self, ts: Optional[float] = None) -> None:
        """软件启动 / 首次活动。先把上次没收尾的会话补收尾，再开新会话。"""
        self._recover_stale()
        self.touch(ts, force_new=True)

    def _recover_stale(self) -> None:
        """上次被强杀（没走 stop）时 current 文件会残留 —— 按 last_touch 收尾，不丢时段。"""
        cur = self._read_current()
        if cur is None:
            return
        self._close(cur)
        try:
            self.cur_path.unlink()
        except FileNotFoundError:
            pass

    def touch(self, ts: Optional[float] = None, force_new: bool = False) -> None:
        """心跳：每 N 分钟调一次即可（沿用"用户在用电脑"的低强度信号）。"""
        t = _now() if ts is None else ts
        cur = self._read_current()
        if force_new or cur is None:
            cur = {"start": t, "last_touch": t}
        elif (t - float(cur.get("last_touch") or t)) > IDLE_SPLIT_MIN * 60.0:
            # 空档太久 → 视为断开：收尾旧会话，开新的
            self._close(cur)
            cur = {"start": t, "last_touch": t}
        else:
            cur["last_touch"] = t
        self._write_current(cur)

    def stop(self, ts: Optional[float] = None) -> None:
        """正常退出：收尾当前会话。"""
        t = _now() if ts is None else ts
        cur = self._read_current()
        if cur is None:
            return
        cur["last_touch"] = t
        self._close(cur)
        try:
            self.cur_path.unlink()
        except FileNotFoundError:
            pass

    # ---- 统计 ----

    def sessions(self, days: int = 30, now: Optional[float] = None) -> list[dict[str, Any]]:
        """最近 days 天的会话（含未收尾的当前会话）。"""
        t = _now() if now is None else now
        since = t - days * 86400.0
        out: list[dict[str, Any]] = []
        for rec in self._read_log():
            if float(rec.get("last_touch") or 0) >= since:
                out.append(rec)
        cur = self._read_current()
        if cur is not None and float(cur.get("last_touch") or 0) >= since:
            out.append(dict(cur))
        out.sort(key=lambda r: float(r.get("start") or 0))
        return out

    def night_streak(self, now: Optional[float] = None) -> int:
        """连续几天在夜间窗口 [00:00, 05:00) 有活动（"连续熬夜天数"）。"""
        t = _now() if now is None else now
        nights = self._night_days(t, days=14)
        streak = 0
        day = self._day_start(t)
        # 今天若还没在夜间窗口活动过（例如现在是白天），从昨天开始数
        if not self._touched_night(nights, day):
            day -= 86400.0
        for _ in range(14):
            if not self._touched_night(nights, day):
                break
            streak += 1
            day -= 86400.0
        return streak

    def online_minutes_today(self, now: Optional[float] = None) -> float:
        t = _now() if now is None else now
        d0 = self._day_start(t)
        total = 0.0
        for rec in self.sessions(days=3, now=t):
            a = max(float(rec.get("start") or 0), d0)
            b = min(float(rec.get("last_touch") or 0), t)
            if b > a:
                total += (b - a)
        return round(total / 60.0, 1)

    def late_night_summary(self, now: Optional[float] = None) -> dict[str, Any]:
        """给主动发言用的一句话素材（无内容、只看时段）。"""
        t = _now() if now is None else now
        streak = self.night_streak(t)
        return {
            "night_streak": streak,                       # 连续熬夜天数
            "online_minutes_today": self.online_minutes_today(t),
            "is_late_night": self._in_night(t),           # 现在是否处在夜间窗口
            "local_hour": time.localtime(t).tm_hour,
        }

    # ---- 内部 ----

    @staticmethod
    def _day_start(ts: float) -> float:
        lt = time.localtime(ts)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    @staticmethod
    def _in_night(ts: float) -> bool:
        h = time.localtime(ts).tm_hour
        return NIGHT_START_H <= h < NIGHT_END_H

    def _night_days(self, now: float, days: int) -> set[str]:
        """返回"哪几天在夜间窗口有活动"（按 0 点所在日期记）。"""
        got: set[str] = set()
        since = self._day_start(now) - days * 86400.0
        for rec in self.sessions(days=days + 1, now=now):
            a = float(rec.get("start") or 0)
            b = float(rec.get("last_touch") or 0)
            d = self._day_start(a)
            while d <= b and d >= since - 86400.0:
                n0 = d + NIGHT_START_H * 3600.0
                n1 = d + NIGHT_END_H * 3600.0
                if max(a, n0) < min(b, n1):
                    got.add(time.strftime("%Y-%m-%d", time.localtime(d)))
                d += 86400.0
        return got

    @staticmethod
    def _touched_night(nights: set[str], day_start: float) -> bool:
        return time.strftime("%Y-%m-%d", time.localtime(day_start)) in nights

    def _read_log(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            with self.log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        continue
        except Exception as e:  # noqa: BLE001
            print(f"[session] 读取会话日志失败：{e}")
        return out

    def _read_current(self) -> Optional[dict[str, Any]]:
        if not self.cur_path.exists():
            return None
        try:
            d = json.loads(self.cur_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except Exception as e:  # noqa: BLE001
            print(f"[session] 当前会话文件损坏，已忽略（{e}）")
            return None

    def _write_current(self, cur: dict[str, Any]) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self.cur_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cur_path)

    def _close(self, cur: dict[str, Any]) -> None:
        if float(cur.get("last_touch") or 0) <= float(cur.get("start") or 0):
            return
        self.base.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(cur, ensure_ascii=False) + "\n")
            f.flush()
