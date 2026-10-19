"""自动收集：把「用户发过的表情包」收进库里。

借鉴 astrbot meme_manager 的 auto_collect 设计（README/CHANGELOG v4.13→v5.0.7），
它踩过的坑我都保留了对策：

1. **不是每张图都收**——否则很快被普通照片塞满。用「重复次数 → 采样概率」递增：
   平台明确标注为表情的图片每次都进抽样；普通图片第 1 次只记录不收，
   第 2 次 45%，之后逐次提高，第 8 次必定触发。
2. **同一发送者在不同消息里重复发同一张 → 也计数**（说明这是他的常用表情）。
3. **去重**：内容哈希（sha256）判重，已入包/待审/被拒的都不再进来。
4. **人工审核队列**：默认不直接入包，先进待审，由用户确认分类后接收——
   避免"她突然开始发奇怪图"。可配置为自动接收。
5. **7 天过期**：连续 7 天未再出现的记录清理掉，避免无限膨胀。

本模块**纯逻辑 + 本地文件**，不调模型、不依赖达妮娅主程序，可独立单测。
"""
import hashlib
import json
import os
import time
from pathlib import Path

# 第 N 次出现时的采样概率。下标 = 出现次数 - 1。
# 第 1 次 = 0（只记录不识别），第 2 次 = 0.45，逐次提高，第 8 次起必定触发。
REPEAT_SAMPLE_PROB = (0.0, 0.45, 0.60, 0.70, 0.80, 0.88, 0.94, 1.0)

# 已出现的图片记录多久没再出现就过期（秒）。7 天。
RECORD_TTL_S = 7 * 24 * 3600

# 同一来源两次识别之间的最短间隔（秒），防止连发刷爆。（astrbot 也有这条）
MIN_SOURCE_INTERVAL_S = 30.0

# 每包待审容量上限
DEFAULT_PENDING_CAP = 200


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CollectStore:
    """自动收集的持久化：出现记录 + 去重集合 + 待审队列。

    落盘：data/collect.json
      {
        "records":   {hash: {"count":n, "first_ts":t, "last_ts":t, "sender":s}},
        "accepted":  [hash, ...],          # 已入包（永久去重）
        "rejected":  [hash, ...],          # 用户明确"不再收集"（永久去重）
        "pending":   [{hash, pack_id, bytes_path, suggested_category,
                       category_confidence, quality_confidence, ts, sender}]
      }
    图片字节落在 data/pending/<hash>.<ext>，接收后才复制进包。
    """
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir = self.data_dir / "pending"
        self.pending_dir.mkdir(exist_ok=True)
        self.path = self.data_dir / "collect.json"
        self._state = self._load()

    def _load(self):
        if not self.path.exists():
            return {"records": {}, "accepted": [], "rejected": [], "pending": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                s = json.load(f)
            for k, v in (("records", {}), ("accepted", []), ("rejected", []), ("pending", [])):
                s.setdefault(k, v)
            return s
        except Exception:  # noqa: BLE001  坏文件不致命
            return {"records": {}, "accepted": [], "rejected": [], "pending": []}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---------- 过期清理 ----------
    def expire(self, now=None):
        """清掉 7 天未再出现的记录（已接收/已拒绝的永久保留）。"""
        now = time.time() if now is None else now
        recs = self._state["records"]
        gone = [h for h, r in recs.items() if now - float(r.get("last_ts") or 0) > RECORD_TTL_S]
        for h in gone:
            recs.pop(h, None)
        if gone:
            self.save()
        return gone

    def is_known(self, h: str) -> bool:
        """是否已经在库里/待审/被拒（这些都不再重复收集）。"""
        s = self._state
        return (h in s["accepted"] or h in s["rejected"]
                or any(p.get("hash") == h for p in s["pending"]))

    def note_seen(self, h: str, sender: str = "", now=None):
        """记一次"这张图出现了"，返回累计出现次数。"""
        now = time.time() if now is None else now
        r = self._state["records"].get(h)
        if not r:
            r = {"count": 0, "first_ts": now, "last_ts": now, "sender": sender}
            self._state["records"][h] = r
        r["count"] = int(r.get("count", 0)) + 1
        r["last_ts"] = now
        if sender:
            r["sender"] = sender
        return r["count"]

    def should_sample(self, h: str, platform_marked: bool, rng=None) -> tuple[bool, str]:
        """是否该把这张图送去识别。返回 (是否送去, 原因)。

        这是控制"不要什么都收"的核心闸门，完全复刻 astrbot 的递增采样策略。
        """
        import random
        rng = rng or random
        if self.is_known(h):
            return False, "已在库/待审/被拒"
        if platform_marked:
            return True, "平台标记为表情"
        count = int((self._state["records"].get(h) or {}).get("count") or 0)
        # 下标 = 次数 - 1，因此第 1 次取到 0.0（只记录）。次数超出表长则用最后一档（1.0）。
        idx = min(max(count - 1, 0), len(REPEAT_SAMPLE_PROB) - 1)
        p = REPEAT_SAMPLE_PROB[idx]
        if count <= 1:
            return False, f"第 {count} 次出现，仅记录"
        if p >= 1.0:
            return True, f"第 {count} 次出现，必定触发"
        hit = rng.random() < p
        return hit, f"第 {count} 次出现，采样 p={p:.2f} -> {'命中' if hit else '跳过'}"

    # ---------- 待审队列 ----------
    def add_pending(self, data: bytes, ext: str, pack_id: str, sender="",
                    suggested_category="", category_confidence=0.0,
                    quality_confidence=0.0, cap=DEFAULT_PENDING_CAP):
        h = content_hash(data)
        if self.is_known(h):
            return None
        pend = [p for p in self._state["pending"] if p.get("pack_id") == pack_id]
        if len(pend) >= cap:
            return None
        fname = f"{h}{ext}"
        (self.pending_dir / fname).write_bytes(data)
        item = {"hash": h, "pack_id": pack_id, "file": fname, "sender": sender,
                "suggested_category": suggested_category,
                "category_confidence": round(float(category_confidence), 3),
                "quality_confidence": round(float(quality_confidence), 3),
                "ts": time.time()}
        self._state["pending"].append(item)
        self.save()
        return item

    def list_pending(self, pack_id=None):
        items = self._state["pending"]
        if pack_id:
            items = [p for p in items if p.get("pack_id") == pack_id]
        # 默认按分类置信度从高到低（低把握度排在后面，与 astrbot 一致）
        return sorted(items, key=lambda p: -float(p.get("category_confidence") or 0.0))

    def get_pending(self, h):
        return next((p for p in self._state["pending"] if p.get("hash") == h), None)

    def read_pending_bytes(self, h):
        it = self.get_pending(h)
        if not it:
            return None
        p = self.pending_dir / it["file"]
        return p.read_bytes() if p.exists() else None

    def accept(self, h, category=None):
        """接收一张待审图片：返回 (字节, 最终分类, 包id)；由调用方写进包。"""
        it = self.get_pending(h)
        if not it:
            return None
        data = self.read_pending_bytes(h)
        cat = category if category is not None else it.get("suggested_category", "")
        self._state["pending"] = [p for p in self._state["pending"] if p.get("hash") != h]
        self._state["accepted"].append(h)
        try:
            (self.pending_dir / it["file"]).unlink()
        except Exception:  # noqa: BLE001
            pass
        self.save()
        return (data, cat, it["pack_id"]) if data else None

    def reject(self, h, never_again=True):
        """丢弃一张待审图片；never_again=True 时相同内容不再进来。"""
        it = self.get_pending(h)
        if not it:
            return False
        self._state["pending"] = [p for p in self._state["pending"] if p.get("hash") != h]
        if never_again:
            self._state["rejected"].append(h)
        try:
            (self.pending_dir / it["file"]).unlink()
        except Exception:  # noqa: BLE001
            pass
        self.save()
        return True

    # ---------- 来源冷却 ----------
    def source_cooling(self, sender: str, now=None) -> tuple[bool, str]:
        now = time.time() if now is None else now
        last = self._state.setdefault("source_last_ts", {})
        t = float(last.get(sender) or 0.0)
        if sender and now - t < MIN_SOURCE_INTERVAL_S:
            return True, f"来源 {sender} 距上次 {now - t:.0f}s，冷却中"
        return False, ""

    def touch_source(self, sender: str, now=None):
        now = time.time() if now is None else now
        self._state.setdefault("source_last_ts", {})[sender] = now
        self.save()

    def stats(self):
        s = self._state
        return {"records": len(s["records"]), "accepted": len(s["accepted"]),
                "rejected": len(s["rejected"]), "pending": len(s["pending"])}
