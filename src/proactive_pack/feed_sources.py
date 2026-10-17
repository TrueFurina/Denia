# -*- coding: utf-8 -*-
"""主动互动包 · 热榜素材源（借鉴 N.E.K.O 的"全网冲浪拉热点"思路）。

职责：把**真实发生的热点**拉下来、过滤掉不该分享的、merge 进素材池
`data/proactive_pack_feed.json`，喂给 `share` 触发器（兴趣分享）。

设计红线（与整包一致）：
  · **只搬事实，不编内容**：条目就是热榜上的原句（版本公告/赛事/新片/游戏动态），
    「怎么说」由 prompts.py 现场即兴生成，这里绝不代笔。
  · **拉不到 = 静默降级**：网络失败 / 接口改版 → 保留旧素材、留日志、绝不硬编占位内容。
  · **内容过滤宁严勿松**：涉政、灾难伤亡、明星八卦一律不进池（伴侣分享这些不是"有意思"，
    是没分寸；涉政类是合规硬红线）。
  · 素材条目带 `src:"hot"` 标记，与手工投喂的条目区分；刷新只替换 hot 段，**绝不覆盖手工条目**。
  · 全部用标准库 urllib（不引第三方依赖）；后台线程执行，绝不阻塞宿主 tick。

来源（免费、无需鉴权；接口改版会自动降级到其它源）：
  1. Bilibili 热搜（游戏/ACG 浓度高，适合"给你看个东西"的语境）
  2. 微博热搜（vvhan 聚合口；纯兜底，覆盖非游戏兴趣）

用法：
  · 自动：pack.maybe_emit 里按 TTL 后台保鲜（默认 4h 一次）。
  · 手动：`python -m src.proactive_pack.feed_sources --force`（真机排查用）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("proactive_pack")

# ---- 内容过滤 --------------------------------------------------------------
# 涉政（合规硬红线）+ 灾难伤亡 + 明星八卦（分享这些不像朋友，像营销号）
_BLOCK_WORDS = (
    # 政治（硬红线）
    "习近平", "国务院", "中央", "党", "总书记", "国家主席", "政府", "台独", "两岸",
    "外交部", "两会", "军队", "军事", "国防", "干部", "选举", "宪法", "主权",
    "抗议", "游行", "演习", "导弹", "核武", "政治", "外交", "制裁", "台湾", "香港",
    "澳门", "新疆", "西藏", "领导人", "官员", "反腐", "政治局", "联合国", "战争",
    # 灾难 / 伤亡 / 疾病（分享这些是没分寸）
    "坠", "死亡", "去世", "遇难", "自杀", "车祸", "火灾", "爆炸", "地震", "洪水",
    "疫情", "确诊", "杀人", "失踪", "判刑", "死刑", "诈骗", "强奸", "未成年", "尸",
    "肺炎", "病毒", "感染", "流感", "不明疾病", "禽流感", "癌",
    # 明星八卦 / 桃色（不像朋友会说的话）
    "出轨", "离婚", "恋情", "绯闻", "恋爱曝", "分手", "官宣结婚", "恋情曝",
)

_MIN_TEXT = 3          # 热搜词最短长度（短于此的多是单字热搜词，没信息量）
_MAX_TEXT = 48         # 截断保护


def _blocked(text: str) -> bool:
    return any(w in text for w in _BLOCK_WORDS)


# ---- 解析器（容错：接口改版返回 []，不抛） ----------------------------------

def _parse_bilibili(d: Any) -> list[str]:
    """B 站热搜 square 接口：data.trending.list[].keyword。"""
    try:
        lst = d["data"]["trending"]["list"]
        return [str(x.get("keyword") or "") for x in lst if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        return []


def _parse_vvhan(d: Any) -> list[str]:
    """vvhan 聚合热榜：data[].name（兼容 title/word 字段名变体）。"""
    try:
        lst = d.get("data") or []
        out = []
        for x in lst:
            if not isinstance(x, dict):
                continue
            out.append(str(x.get("name") or x.get("title") or x.get("word") or ""))
        return out
    except Exception:  # noqa: BLE001
        return []


SOURCES: list[dict[str, Any]] = [
    {"name": "bilibili",
     "url": "https://api.bilibili.com/x/web-interface/search/square?limit=20",
     "parser": _parse_bilibili},
    {"name": "weibo",
     "url": "https://api.vvhan.com/api/hotlist/wbHot",
     "parser": _parse_vvhan},
]


def _http_get_json(url: str, timeout: float = 6.0) -> Any:
    """标准库拉 JSON（带 UA；不引第三方依赖）。失败抛异常，由调用方兜。"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"),
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_source(src: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """拉一个源 → 过滤 → 规范条目。失败/空返回 []（记 debug，不抛）。"""
    name = str(src.get("name") or "?")
    parser: Callable[[Any], list[str]] = src["parser"]
    try:
        words = parser(_http_get_json(str(src["url"]))) or []
    except Exception as e:  # noqa: BLE001 接口改版 / 断网 → 降级不静默
        logger.debug("[proactive_pack] 热榜源 %s 拉取失败：%s", name, e)
        return []
    out, seen = [], set()
    for w in words:
        w = w.strip()
        if len(w) < _MIN_TEXT:
            continue
        if _blocked(w):
            continue
        w = w[:_MAX_TEXT]
        h = hashlib.md5(w.encode("utf-8")).hexdigest()[:12]
        iid = "hot:%s:%s" % (name, h)
        if iid in seen:
            continue
        seen.add(iid)
        out.append({"id": iid, "text": w, "tags": [name], "ts": float(now), "src": "hot"})
    logger.info("[proactive_pack] 热榜源 %s 取得 %d 条（过滤后）", name, len(out))
    return out


# ---- merge 进素材池 ---------------------------------------------------------

def merge_into_feed(base_dir: Path | str, hot_items: list[dict[str, Any]],
                    max_items: int = 60) -> bool:
    """把 hot 条目 merge 进 feed.json：替换旧 hot 段，**保留全部手工条目**。原子写。"""
    from . import feed
    p = feed.feed_path(base_dir)
    old = feed.load_items(base_dir)
    manual = [dict(it) for it in old if not it.get("src")]
    merged = manual + list(hot_items)
    # 上限：优先保手工条目，hot 从新到旧截断
    if len(merged) > max_items:
        merged = manual + list(hot_items)[: max(0, max_items - len(manual))]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[proactive_pack] 素材池写入失败：%s", e)
        return False


def _has_fresh_hot(base_dir: Path | str, ttl_h: float, now: float) -> bool:
    """素材池里是否已有「未过期」的 hot 段（mtime + 条目内 src 双判据）。"""
    from . import feed
    p = feed.feed_path(base_dir)
    if not p.exists():
        return False
    try:
        if now - p.stat().st_mtime < ttl_h * 3600.0:
            return any(it.get("src") == "hot" for it in feed.load_items(base_dir))
    except Exception:  # noqa: BLE001
        pass
    return False


_inflight = threading.Lock()      # 并发刷新防抖（后台线程 + 手动 CLI 可能撞车）
_refreshing = threading.Event()


def refresh_if_stale(base_dir: Path | str, ttl_h: float = 4.0,
                     max_items: int = 60, now: Optional[float] = None,
                     force: bool = False) -> dict[str, Any]:
    """素材池保鲜：过期才拉，拉到的结果 merge 进 feed.json。可从后台线程调。

    返回报告 {"skipped"/"ok"/"fresh": ..., "count": n, ...}；**任何失败都不抛**。
    """
    t = float(now if now is not None else time.time())
    if not force and _has_fresh_hot(base_dir, ttl_h, t):
        return {"skipped": True}
    if _refreshing.is_set():
        return {"skipped": True, "inflight": True}
    with _inflight:
        if _refreshing.is_set():
            return {"skipped": True, "inflight": True}
        _refreshing.set()
    try:
        hot: list[dict[str, Any]] = []
        seen: set[str] = set()
        for src in SOURCES:
            for it in fetch_source(src, t):
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                hot.append(it)
        if not hot:
            # 全源失败：**不动旧素材**（拉不到新的就继续用旧的，旧的也比编的强）
            logger.info("[proactive_pack] 热榜全源拉取失败，素材池保持原样")
            return {"ok": False, "count": 0}
        ok = merge_into_feed(base_dir, hot, max_items)
        return {"ok": ok, "count": len(hot)}
    finally:
        _refreshing.clear()


# ---- 手动 CLI（真机排查用） ---------------------------------------------------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="主动互动包 · 热榜素材手动刷新")
    ap.add_argument("--dir", default="", help="data 目录（默认项目 data/）")
    ap.add_argument("--force", action="store_true", help="无视 TTL 强制拉取")
    args = ap.parse_args()
    base = Path(args.dir) if args.dir else Path(__file__).resolve().parents[2] / "data"
    rep = refresh_if_stale(base, force=args.force)
    print(json.dumps(rep, ensure_ascii=False))
