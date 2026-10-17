# -*- coding: utf-8 -*-
"""主动互动包 · 兴趣分享素材源（抖音式「给你看个东西」的数据口）。

数据文件：`data/proactive_pack_feed.json`（**没有就静默降级，绝不硬编内容**）。

格式（数组，每条是一个"可分享的素材"）：
```json
[
  {
    "id": "2026-10-12-xxx",
    "text": "原神 5.x 版本前瞻直播定档周五晚，新地图曝光",
    "tags": ["原神", "游戏"],
    "ts": 1760000000.0
  }
]
```

约束：
  · `text` 必须是**真实发生的事**（版本公告 / 新闻 / 赛事结果 / 新片上映…）。
    素材从哪来：现阶段由外部投喂（后续可接搜索桥 / RSS / 官方公告爬取）；
    本模块**只负责读取、校验、匹配**，不生成内容。
  · 匹配逻辑：素材 tags 或正文 命中 用户兴趣画像（situation.interest_tags）才开口；
    兴趣画像为空 = 永不分享（没有把握的事不如不说）。
  · 每条素材 `id` 缺省时用 `text` 前 60 字符代替（去重用）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("proactive_pack")

FEED_FILENAME = "proactive_pack_feed.json"


def feed_path(base_dir: Path | str) -> Path:
    return Path(base_dir) / FEED_FILENAME


def load_items(base_dir: Path | str) -> list[dict[str, Any]]:
    """读素材池。文件缺失 / 损坏 / 格式不对 → 返回 []（留日志，不抛）。"""
    p = feed_path(base_dir)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, list):
            logger.warning("[proactive_pack] 素材池顶层不是数组，已忽略：%s", p)
            return []
        out = []
        for it in d:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "id": str(it.get("id") or text[:60]),
                "text": text,
                "tags": [str(t) for t in (it.get("tags") or []) if str(t).strip()],
                "ts": float(it.get("ts") or 0.0),
                # 来源标记：""=手工投喂，"hot"=热榜自动拉取（feed_sources 只替换 hot 段）
                "src": str(it.get("src") or ""),
            })
        return out
    except Exception as e:  # noqa: BLE001 坏文件隔离不静默
        logger.warning("[proactive_pack] 素材池读取失败（忽略）：%s（%s）", p, e)
        return []


def item_key(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("text") or "")[:60]


def matches(item: dict[str, Any], interest_tags: list[str]) -> bool:
    """素材是否命中用户兴趣：双向包含判定（tag⊂素材 或 素材词⊂tag）。

    tags 为空列表 → 返回 False（没匹配上就不硬分享）。
    """
    hay = (str(item.get("text") or "") + " " + " ".join(str(t) for t in (item.get("tags") or [])))
    for tag in (interest_tags or []):
        t = str(tag or "").strip()
        if len(t) < 2:
            continue
        if t in hay:
            return True
    return False


def pick(items: list[dict[str, Any]], interest_tags: list[str],
         done_keys: set[str], now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """从素材池挑一条：命中兴趣 + 未分享过 + 最新的优先。没有就 None。"""
    cands = [it for it in (items or [])
             if matches(it, interest_tags) and (it.get("id") not in done_keys)]
    if not cands:
        return None
    cands.sort(key=lambda it: float(it.get("ts") or 0.0), reverse=True)
    return cands[0]


def template_text() -> str:
    """给 README / 设置页用的最小示例（含注释说明， humans 读了会填）。"""
    return json.dumps([
        {"id": "", "text": "（示例）原神新版本前瞻直播定档周五晚",
         "tags": ["原神", "游戏"], "ts": time.time()},
    ], ensure_ascii=False, indent=2)
