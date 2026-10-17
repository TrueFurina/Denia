"""上下文引用（Plan C）——从**历史原话**里按时间/话题检索，注入"可追溯到具体某句"的引用。

【2026-10-11 新增】开发者诉求（原话）：
    "记忆主动引用能不能不只是记忆库，还有上下文的引用"

与「记忆库」的分工（关键，别混）
--------------------------------
  · 记忆库（MemoryStore）  = **已提炼的长期事实/画像**：结构化、扁平、
    "用户喜欢摄影"。适合"她是否知道他这个人"。
  · 上下文引用（本模块）    = **历史原话**：可追溯到**具体某天某句**
    "（10月10日）你跟我说过：『等会发照片给你看』"。
    适合"她是否记得**当时那句**"——用户问"你还记得你要干嘛吗"就靠这个。

为什么数据一直躺在盘里没人用
----------------------------
`data/history/<char>.jsonl`（HistoryStore）与 `data/chatlog/<char>.jsonl`
（ChatLogStore）**早已存了完整对话**，但**没有任何模块按时间/话题去检索**——
检索器缺，不是数据缺。本模块补上这个检索器。

设计红线（沿用全项目铁律）
--------------------------
1. **只读**：绝不写 history/chatlog，不改任何既有写入路径。
2. **失败降级 + 留痕**：任何异常 → 返回空串（注入点退化为"没有引用"，不会更差），
   且只在 debug 留痕，绝不静默吞掉后假装成功（用 `_last_error` 记录）。
3. **只加不改**：本模块是**旁挂**，不接入则不改变任何现有行为。
4. **防 lost-in-middle**：只精选**少量**（默认 ≤3 条）最相关的原话，不全灌。
5. **不制造焦虑 / 反 PUA**：引用是为了"她真的记得"，**不是**"你怎么才来"式追责；
   引用措辞由提示词侧约束（本模块只给中性事实行）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

# ── 时间词识别：用户提到"过去"时，才值得去翻历史原话 ─────────────────────
# 只在这些**明确的回指**出现时触发上下文检索（避免每轮都翻盘、拖慢与噪音）。
_TIME_REF_RE = re.compile(
    r"上次|上回|之前|以前|那天|当时|昨天|昨晚|前天|上周|刚才|刚刚|"
    r"我跟你说过|跟你说过|我说过|你记得|还记得|忘了没|"
    r"什么时候(?:说|讲|聊)|说好|答应了|约定"
)

# 明显不值得引用的行（状态卡 / 纯表情 / 太短 / 系统提示）
_SKIP_RE = re.compile(r"^(?:\[|（|\(|状态|系统|正在|已|【)")
_MIN_LEN = 4


def _fmt_day(ts: float, now: Optional[float] = None) -> str:
    """把时间戳格式化成"人话相对日期"：今天 / 昨天 / 10月9日。

    相对日期比绝对时间戳更自然（"昨天你说的"），且跨天/跨周可读。
    """
    if not ts:
        return ""
    now = now or time.time()
    try:
        import datetime as _dt
        d = _dt.datetime.fromtimestamp(ts)
        n = _dt.datetime.fromtimestamp(now)
        days = (n.date() - d.date()).days
        if days <= 0:
            return "今天"
        if days == 1:
            return "昨天"
        if days == 2:
            return "前天"
        if days < 7:
            return "%d天前" % days
        return "%d月%d日" % (d.month, d.day)
    except Exception:  # noqa: BLE001
        return ""


def _bigrams(s: str) -> list[str]:
    """字符二元组（与 memory_store._lexical_overlap 同口径，中文无需分词）。"""
    s = (s or "").strip()
    if len(s) < 2:
        return [s] if s else []
    return [s[i:i + 2] for i in range(len(s) - 1)]


def _overlap(query: str, text: str) -> float:
    """query 与 text 的二元组重合率（0~1）。"""
    q = _bigrams(query)
    t = text or ""
    if not q or not t:
        return 0.0
    hit = sum(1 for g in q if g in t)
    return hit / float(len(q))


class ContextRecall:
    """历史原话检索器（只读 history / chatlog）。"""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "data"
        self._base = Path(base_dir)
        self._last_error: str = ""     # 留痕：最近一次失败原因（非静默）

    # ---------- 读取 ----------
    def _read_jsonl(self, kind: str, char_id: str) -> list[dict[str, Any]]:
        """读 `data/<kind>/<char>.jsonl`（kind ∈ history/chatlog）。

        history 记录：{ts, role(user/assistant), content, source}
        chatlog 记录：{ts, role(user/ai), text, ...}
        这里统一成 {ts, role, text}，坏行跳过但**记录**坏行数（不静默）。
        """
        safe = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(char_id or "").strip()) or "default"
        p = self._base / kind / ("%s.jsonl" % safe)
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        bad = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        bad += 1
                        continue
                    if not isinstance(rec, dict):
                        bad += 1
                        continue
                    text = str(rec.get("content") or rec.get("text") or "").strip()
                    if not text:
                        continue
                    role = str(rec.get("role") or "")
                    # 归一：assistant→ai，统一成 user/ai 两值
                    role = "user" if role == "user" else "ai"
                    out.append({"ts": float(rec.get("ts") or 0.0), "role": role, "text": text})
        except Exception as e:  # noqa: BLE001
            self._last_error = "read %s/%s: %s" % (kind, safe, e)
            return []
        if bad:
            self._last_error = "%s/%s 有 %d 行无法解析（已跳过）" % (kind, safe, bad)
        return out

    def _all_turns(self, char_id: str) -> list[dict[str, Any]]:
        """合并 history + chatlog 去重（优先 chatlog：它带更完整的界面消息）。

        去重键：(role, text 前 60 字)。两条来源高度重叠（都存同一对话），
        不合并会出现"同一句话被引用两次"。
        """
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for kind in ("chatlog", "history"):
            for t in self._read_jsonl(kind, char_id):
                key = (t["role"], t["text"][:60])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(t)
        merged.sort(key=lambda x: x.get("ts") or 0.0)
        return merged

    # ---------- 检索 ----------
    def search(self, char_id: str, query: str, limit: int = 3,
               max_age_days: float = 30.0) -> list[dict[str, Any]]:
        """按话题关键词检索历史原话，返回最相关的若干条（时间正序无关，按分排序）。

        - query 为空 → 返回 []（不翻盘）。
        - 只保留 `max_age_days` 内的（太旧的"原话"引用起来像翻旧账，反 PUA）。
        - 打分 = 二元组重合（主）+ 新鲜度（次）。用户自己的话优先于 AI 的话
          （引用"你说过"比"我说过"更有意义，且 AI 的话可能只是模板）。
        """
        q = (query or "").strip()
        if not q:
            return []
        turns = self._all_turns(char_id)
        if not turns:
            return []
        now = time.time()
        cutoff = now - max_age_days * 86400.0
        scored: list[tuple[float, dict[str, Any]]] = []
        for t in turns:
            ts = t.get("ts") or 0.0
            if ts and ts < cutoff:
                continue
            txt = t.get("text") or ""
            if len(txt) < _MIN_LEN or _SKIP_RE.match(txt):
                continue
            ov = _overlap(q, txt)
            if ov <= 0.0:
                continue
            # 新鲜度：7 天内 1.0，之后线性衰减到 0.4
            if ts:
                age_d = max(0.0, (now - ts) / 86400.0)
                fresh = 1.0 if age_d <= 7 else max(0.4, 1.0 - (age_d - 7) / 60.0)
            else:
                fresh = 0.7
            role_bonus = 0.15 if t["role"] == "user" else 0.0
            score = ov * 0.7 + fresh * 0.2 + role_bonus
            scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        seen_text: set[str] = set()
        for sc, t in scored:
            key = t["text"][:40]
            if key in seen_text:
                continue
            seen_text.add(key)
            out.append({"ts": t.get("ts") or 0.0,
                        "role": t["role"],
                        "text": t["text"],
                        "day": _fmt_day(t.get("ts") or 0.0, now),
                        "score": round(sc, 3)})
            if len(out) >= limit:
                break
        return out

    def recall_block(self, char_id: str, query: str, limit: int = 3,
                     enabled: bool = True) -> str:
        """格式化成可注入的"历史引用块"；无命中返回空串。

        输出形态（开发者要的"可追溯到某句"）：
            【你们以前聊到过（可自然提起，不要生硬复述）】
            - （昨天）你说过：「等会发照片给你看」  ← 这句他一直没发
        注意：措辞是**中性事实**，不带"你怎么还没…"式追责（反 PUA）。
        """
        if not enabled:
            return ""
        try:
            hits = self.search(char_id, query, limit=limit)
        except Exception as e:  # noqa: BLE001
            self._last_error = "search: %s" % e
            return ""
        if not hits:
            return ""
        lines: list[str] = []
        for h in hits:
            day = h.get("day") or ""
            prefix = "你说过" if h.get("role") == "user" else "我说过"
            txt = str(h.get("text") or "").strip()
            if len(txt) > 60:
                txt = txt[:60] + "…"
            lines.append("- （%s）%s：「%s」" % (day or "之前", prefix, txt))
        return "【你们以前聊到过（可自然提起，别生硬复述）】\n" + "\n".join(lines)

    # ---------- 触发判据 ----------
    @staticmethod
    def should_recall(user_text: str) -> bool:
        """用户这句话是否**回指过去**（含时间词/回忆词）→ 才去翻历史原话。

        不满足则返回 False（调用方可据此完全跳过检索，零开销）。
        """
        t = (user_text or "").strip()
        if not t:
            return False
        return bool(_TIME_REF_RE.search(t))


# ── 进程级单例（与 memory_store / conversation_history 同风格）──────────
_CTX_SINGLETON: Optional[ContextRecall] = None


def get_context_recall(base_dir: Optional[Path | str] = None) -> ContextRecall:
    global _CTX_SINGLETON
    if _CTX_SINGLETON is None:
        _CTX_SINGLETON = ContextRecall(base_dir=base_dir)
    return _CTX_SINGLETON
