# -*- coding: utf-8 -*-
"""主动互动包 · 去重账本与回访标记（落 `data/proactive_pack.json`）。

解决三件事（文档 §4.3 / §4.4）：
  1. **每类各自去重**："这个节日今年发过了""这条记忆回访过了""今天饭点聊过了"；
  2. **全局今日开口账本**：四类触发器共用一个日计数与"上次开口时间"
     （宿主 `_proactive_decision` 里三条产出点各算各的，这里补一个统一账本）；
  3. **回访标记**：主动回访过某条记忆后写 `revisited_ts`，避免反复问同一件事。

落盘格式（人类可读，便于排查）：
```json
{
  "flags": { },                       // 运行时开关，见 config.py
  "ledger": { "2026-10-12": { "count": 2, "last_ts": 1750... } },
  "marks": {
    "danya": {
      "festival": { "2026": ["中秋节"] },
      "meal":     { "2026-10-12": 1750... },
      "followup": { "2026-10-12": [1750...] },
      "recall":   { "<记忆key>": 1750... },
      "rhythm":   { "<子类型>": 1750... },
      "silence":  { "2026-10-12": [1750...] }
    }
  }
}
```
红线：文件损坏 → **改名隔离**（`.corrupt-<ts>`）后另起新档，绝不静默吞掉。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .config import STATE_FILENAME, DEFAULT_BASE_DIR

logger = logging.getLogger("proactive_pack")


def _day(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if ts is None else ts))


class MarksStore:
    """去重 / 账本 / 回访标记 的小型 JSON 存储。线程内使用即可（宿主调用点都在事件循环里）。"""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        self.base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
        self.path = self.base / STATE_FILENAME
        self._data: dict[str, Any] = {}
        self._loaded = False

    # ---- 读写 ----

    def load(self) -> dict[str, Any]:
        """读盘（幂等，只读一次）。损坏则隔离改名 + 另起新档。"""
        if self._loaded:
            return self._data
        self._loaded = True
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                raise ValueError("顶层不是对象")
            self._data = d
        except Exception as e:  # noqa: BLE001 坏数据隔离不静默
            bad = self.path.with_suffix(".json.corrupt-%d" % int(time.time()))
            try:
                self.path.replace(bad)
                logger.warning("[proactive_pack] 状态文件损坏，已隔离到 %s（%s）", bad.name, e)
            except Exception:  # noqa: BLE001
                logger.warning("[proactive_pack] 状态文件损坏且无法隔离（%s）", e)
            self._data = {}
        return self._data

    def save(self) -> bool:
        """原子写回。失败只留痕，绝不影响主链路。"""
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[proactive_pack] 状态文件保存失败：%s", e)
            return False

    # ---- 账本（全局今日开口） ----

    def ledger_today(self, ts: Optional[float] = None) -> dict[str, Any]:
        d = self.load().setdefault("ledger", {})
        return d.setdefault(_day(ts), {"count": 0, "last_ts": 0.0})

    def ledger_count(self, ts: Optional[float] = None) -> int:
        return int(self.ledger_today(ts).get("count") or 0)

    def ledger_last_ts(self, ts: Optional[float] = None) -> float:
        return float(self.ledger_today(ts).get("last_ts") or 0.0)

    def ledger_bump(self, now: float) -> None:
        rec = self.ledger_today(now)
        rec["count"] = int(rec.get("count") or 0) + 1
        rec["last_ts"] = float(now)
        self.save()

    # ---- 角色标记桶 ----

    def _char(self, cid: str) -> dict[str, Any]:
        marks = self.load().setdefault("marks", {})
        return marks.setdefault(str(cid or "default"), {})

    # 节日：一个"节日名"一年只发一次
    def festival_sent(self, cid: str, name: str, now: Optional[float] = None) -> bool:
        year = str(time.localtime(time.time() if now is None else now).tm_year)
        return name in (self._char(cid).setdefault("festival", {}).get(year) or [])

    def mark_festival(self, cid: str, name: str, now: Optional[float] = None) -> None:
        year = str(time.localtime(time.time() if now is None else now).tm_year)
        bucket = self._char(cid).setdefault("festival", {}).setdefault(year, [])
        if name not in bucket:
            bucket.append(name)
        self.save()

    # 饭点：当天只提一次
    def meal_pinged(self, cid: str, now: Optional[float] = None) -> bool:
        return bool(self._char(cid).setdefault("meal", {}).get(_day(now)))

    def mark_meal(self, cid: str, now: float) -> None:
        self._char(cid).setdefault("meal", {})[_day(now)] = float(now)
        self.save()

    # 会话内跟进：每日上限 + 每角色冷却
    def followup_times(self, cid: str, now: Optional[float] = None) -> list[float]:
        return list(self._char(cid).setdefault("followup", {}).get(_day(now)) or [])

    def mark_followup(self, cid: str, now: float) -> None:
        self._char(cid).setdefault("followup", {}).setdefault(_day(now), []).append(float(now))
        self.save()

    # 关系型召回：某条记忆回访过就不再反复问
    def recall_done(self, cid: str, key: str) -> bool:
        return key in (self._char(cid).setdefault("recall", {}) or {})

    def recall_times(self, cid: str, now: Optional[float] = None) -> list[float]:
        """当日已回访次数（从 recall 字典的时间戳按天过滤派生，不新增存储桶）。

        2026-10-12：`recall_per_day` 配置项此前是**死键**（_ev_recall 从未读取），
        6 组实测里 A2 出现同一天连访 5 条记忆（查户口感），这里补上计数口径。
        """
        day = _day(now)
        try:
            vals = (self._char(cid).setdefault("recall", {}) or {}).values()
        except Exception:  # noqa: BLE001
            return []
        return [float(ts) for ts in vals if _day(ts) == day]

    def mark_recall(self, cid: str, key: str, now: float) -> None:
        self._char(cid).setdefault("recall", {})[str(key)] = float(now)
        self.save()

    def unmark_recall(self, cid: str, key: str) -> bool:
        """用户**重新提起**某件事 → 撤销回访标记，重新获得一次开口资格。"""
        bucket = self._char(cid).setdefault("recall", {})
        if str(key) in bucket:
            bucket.pop(str(key), None)
            self.save()
            return True
        return False

    # 宣告事件跟进：一次宣告只问一次（问过即闭；除非用户再宣告一次）
    def pending_done(self, cid: str, key: str) -> bool:
        return key in (self._char(cid).setdefault("pending", {}) or {})

    def mark_pending(self, cid: str, key: str, now: float) -> None:
        self._char(cid).setdefault("pending", {})[str(key)] = float(now)
        self.save()

    # 兴趣分享：每条素材只分享一次；每天有上限
    def share_item_done(self, cid: str, item_key: str) -> bool:
        return item_key in (self._char(cid).setdefault("share", {}) or {})

    def mark_share(self, cid: str, item_key: str, now: float) -> None:
        self._char(cid).setdefault("share", {})[str(item_key)] = float(now)
        self.save()

    def share_times(self, cid: str, now: Optional[float] = None) -> list[float]:
        return list(self._char(cid).setdefault("share_daily", {}).get(_day(now)) or [])

    def mark_share_day(self, cid: str, now: float) -> None:
        self._char(cid).setdefault("share_daily", {}).setdefault(_day(now), []).append(float(now))
        self.save()

    # 长期静默召回：按"静默档位"去重，同一天同一档不重复开口
    def silence_times(self, cid: str, now: Optional[float] = None) -> list[float]:
        return list(self._char(cid).setdefault("silence", {}).get(_day(now)) or [])

    def mark_silence(self, cid: str, now: float) -> None:
        self._char(cid).setdefault("silence", {}).setdefault(_day(now), []).append(float(now))
        self.save()

    # 生活节律：按子类型冷却（late_night / sedentary / meal）
    def rhythm_last(self, cid: str, kind: str) -> float:
        return float(self._char(cid).setdefault("rhythm", {}).get(kind) or 0.0)

    def mark_rhythm(self, cid: str, kind: str, now: float) -> None:
        self._char(cid).setdefault("rhythm", {})[str(kind)] = float(now)
        self.save()

    # ---- 明文调试 ----

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.load(), ensure_ascii=False))
