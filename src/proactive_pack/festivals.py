# -*- coding: utf-8 -*-
"""主动互动包 · 节日 / 纪念日。

分两块：
  A) **公历固定日** —— 万年不变，直接写死（元旦/情人节/儿童节/国庆/平安夜/圣诞/跨年…）。
  B) **农历节日** —— 春节/端午/中秋。公历日期每年不同，用一张**年份对照表**。
     表放在可编辑的数据文件 `data/proactive_pack_festivals.json`，
     文件不存在时用 `_LUNAR_SEED` 兜底（**已核对 2024–2031**，超出范围则不发，
     并在日志留痕 —— 不猜、不编）。

反 PUA 红线（文档 §三·节日）：
  节日问候只输出**一句干净祝福**，说完不追，绝不制造期待 / 愧疚。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_BASE_DIR

logger = logging.getLogger("proactive_pack")

FESTIVALS_FILENAME = "proactive_pack_festivals.json"

# A) 公历固定日：(月, 日) -> 名称
SOLAR_FIXED: dict[tuple[int, int], str] = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (5, 1): "劳动节",
    (6, 1): "儿童节",
    (10, 1): "国庆节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
    (12, 31): "跨年夜",
}

# B) 农历节日种子表：年份 -> {节日名: (月, 日)}（**公历日期**）
#    已核对范围 2024–2031；超出范围请在 data/proactive_pack_festivals.json 里补。
_LUNAR_SEED: dict[str, dict[str, list[int]]] = {
    "2024": {"春节": [2, 10], "端午节": [6, 10], "中秋节": [9, 17]},
    "2025": {"春节": [1, 29], "端午节": [5, 31], "中秋节": [10, 6]},
    "2026": {"春节": [2, 17], "端午节": [6, 19], "中秋节": [9, 25]},
    "2027": {"春节": [2, 6], "端午节": [6, 9], "中秋节": [9, 15]},
    "2028": {"春节": [1, 26], "端午节": [5, 28], "中秋节": [10, 3]},
    "2029": {"春节": [2, 13], "端午节": [6, 16], "中秋节": [9, 22]},
    "2030": {"春节": [2, 3], "端午节": [6, 5], "中秋节": [9, 12]},
    "2031": {"春节": [1, 23], "端午节": [6, 24], "中秋节": [10, 1]},
}


def _load_lunar(base_dir: Optional[Path | str]) -> dict[str, dict[str, list[int]]]:
    """年份表：数据文件优先，缺失/损坏则用内置种子（留痕）。"""
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    p = base / FESTIVALS_FILENAME
    if not p.exists():
        return _LUNAR_SEED
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        lunar = d.get("lunar") if isinstance(d, dict) else None
        if isinstance(lunar, dict) and lunar:
            return {str(k): v for k, v in lunar.items()}
        logger.warning("[proactive_pack] 节日文件 %s 无有效 lunar 段，改用内置表", p.name)
    except Exception as e:  # noqa: BLE001
        logger.warning("[proactive_pack] 节日文件损坏（%s），改用内置表：%s", p.name, e)
    return _LUNAR_SEED


def ensure_festival_file(base_dir: Optional[Path | str] = None) -> Path:
    """首次运行时把内置表**导出**成可编辑文件（若已存在则不覆盖）。返回路径。"""
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    p = base / FESTIVALS_FILENAME
    if not p.exists():
        try:
            base.mkdir(parents=True, exist_ok=True)
            payload = {
                "_note": "农历节日的公历日期，按年维护；改动后重启软件生效。",
                "_solar_fixed": {f"{m:02d}-{d:02d}": n for (m, d), n in SOLAR_FIXED.items()},
                "lunar": _LUNAR_SEED,
            }
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("[proactive_pack] 导出节日文件失败：%s", e)
    return p


def festival_today(now: Optional[float] = None,
                   base_dir: Optional[Path | str] = None) -> Optional[dict[str, Any]]:
    """今天是什么节日？返回 {"name","kind"}；没有则 None。"""
    t = time.time() if now is None else now
    lt = time.localtime(t)
    name = SOLAR_FIXED.get((lt.tm_mon, lt.tm_mday))
    if name:
        return {"name": name, "kind": "solar"}
    lunar = _load_lunar(base_dir).get(str(lt.tm_year)) or {}
    for fname, md in lunar.items():
        try:
            if int(md[0]) == lt.tm_mon and int(md[1]) == lt.tm_mday:
                return {"name": fname, "kind": "lunar"}
        except Exception:  # noqa: BLE001 单条坏数据跳过
            continue
    return None


# ---- 关系纪念日 ----------------------------------------------------------

def days_together(char_id: str, now: Optional[float] = None,
                  base_dir: Optional[Path | str] = None) -> Optional[int]:
    """从"最早一条落盘记录"推算你们认识了多少天。拿不到就 None（不猜）。

    数据源：`data/history/<char>.jsonl`（长期会话日志）优先，
    其次 `data/context/<char>.jsonl`（本版上下文镜像）。
    """
    import re as _re
    t = time.time() if now is None else now
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    key = _re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(char_id or "").strip()) or "default"
    for rel in (("history", key), ("context", key)):
        p = base / rel[0] / f"{rel[1]}.jsonl"
        if not p.exists():
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except Exception:  # noqa: BLE001
                        continue
                    ts = float(rec.get("ts") or 0)
                    if ts > 0:
                        return max(0, int((t - ts) // 86400) + 1)
        except Exception as e:  # noqa: BLE001
            logger.debug("[proactive_pack] 纪念日推算读 %s 失败：%s", p.name, e)
    return None


def anniversary_today(char_id: str, now: Optional[float] = None,
                      base_dir: Optional[Path | str] = None) -> Optional[dict[str, Any]]:
    """今天是"认识满 N 天"的整十/整百日子吗？（只在整十与整百开口，不天天念叨。）"""
    days = days_together(char_id, now, base_dir)
    if not days or days < 10:
        return None
    if days % 100 == 0 or days % 10 == 0:
        return {"name": "认识纪念日", "kind": "anniversary", "days": days}
    return None
