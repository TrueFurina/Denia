# -*- coding: utf-8 -*-
"""主动互动包 · 情境事实包装配器（**本包的核心**）。

问题（文档 §零 / §4.1）：宿主给模型的只有一句"用户 300 秒没操作"，
于是她只能吐"在忙吗 / 歇会儿吧"这类**对谁都能说的万能句**。

本模块把"用户的当下处境"翻译成一坨**结构化中文材料**交给模型，
让每一句主动都**锚定在真实发生过的事上**（不编造她的离线生活）。

六个块（全部只读真实数据，任一取不到 → 该块留空 + 记日志，绝不假装成功）：
  now      现在几点 / 星期几 / 是否深夜 / 是否饭点
  gap      距上次互动多久（阶梯语义：短暂离开 / 半天没来 / 过了一夜 / 好几天没来）
  habit    连续熬夜天数 / 今日在线时长（session_clock，数据现成）
  longterm **长期上下文**：总轮次 / 最早一次多久前 + 更早之前聊过的原话（2026-10-12 新增）
  memory   记忆库里"情绪重 且 未回访"的条目（带"几天前"）
  topic    最近几轮对话摘要 + 用户未兑现的承诺
  relation 关系状态块（宿主现有 assemble_relation_block，复用）

角色隔离（开发者 2026-10-12）：**各角色绝不串味**。记忆库读数一律走 `_scoped_memories`
（按 char_id 过滤，不信全局 `_character`）；对话历史走 `shared_history_for` / 分角色落盘文件。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_BASE_DIR

logger = logging.getLogger("proactive_pack")

# 与 dialogue.Dialogue._PROMISE_RE 同源（改一侧需同步另一侧）。
# 说明：主路径优先读记忆库里 source=="promise" 的确定性条目，本正则为兜底。
PROMISE_RE = (
    r"(?:等(?:会|下|一下)|回头|改天|明天|今晚|待会|晚点|稍后|过(?:会|两天)|下次)"
    r".{0,16}?(?:给|发|递|拿|告诉).{0,2}?(?:我|你)?"
    r"|我(?:会|要|想|打算)?(?:先)?(?:把|将)?.{0,14}?(?:发给你|发给你看|给你看|给你听|告诉我|拍给你看)"
)

# 用户「宣告要做的事」→（事件名，预期离开时长秒）。超过预期还没回来，才值得问一句进展。
# 例子：说「我去打两把游戏」→ 1.5h 没动静 →「打完了？」；问过一次就闭（见 marks.pending）。
_PENDING_PATTERNS = (
    (r"玩(?:一?把|一?局|几把|两把)?游戏|打(?:一?把|一?局|几把|两把)?(?:游戏|排位|匹配)|上号|开黑",
     "游戏", 5400),
    (r"洗(?:个)?澡|去洗澡|冲(?:个)?凉", "洗澡", 2400),
    (r"开(?:个)?会|去开会|有(?:个)?会议", "会议", 5400),
    (r"上(?:课|班)|去上课|去上班|加班|赶工|赶(?:个)?方案", "工作/上课", 10800),
    (r"看(?:剧|电影|番|一集)|追剧|追番", "剧/电影", 9000),
    (r"先睡了|去睡|睡了|困了|眯(?:一会|一下)|睡午觉", "睡觉", 32400),
    (r"去吃饭|吃(?:个)?饭|点(?:个)?外卖", "吃饭", 2700),
)
_PENDING_LOOKBACK_H = 12.0        # 宣告只在最近 N 小时内有效（隔夜的「我去打游戏」不再追）

# 兴趣画像抽取（抖音式分享的匹配源）：只认**用户明确表达过**的喜好，绝不脑补。
_INTEREST_RE = re.compile(
    r"(?:喜欢|最爱|爱玩|常玩|在玩|在追|爱看|爱听|沉迷|上头|感兴趣)(?:的是|玩|看|听|上)?"
    r"([^，。！？,.!?;；\s]{1,16})")
_INTEREST_TRIM = re.compile(r"^(?:喝|玩|看|听|打|追|吃|用)+")

# 情绪/重要性词表：命中即给该条记忆加权（文档 §要素2 · significant-moment weighting）
#   strong = 明确的情感事件（脆弱/决定/强烈情绪）→ 高权重
#   weak   = 偏好/状态类（"喜欢""累"）→ 低权重，**单靠它不足以被选中回访**
_EMOTION_STRONG = (
    "难过", "难受", "焦虑", "紧张", "害怕", "担心", "崩溃", "生气", "委屈",
    "分手", "吵架", "冷战", "离职", "辞职", "面试", "考试", "体检", "看病", "生病",
    "感冒", "发烧", "失眠", "熬夜", "加班", "截止", "ddl", "答辩", "出差", "搬家",
    "期待", "激动", "想哭", "失望", "遗憾", "决定", "答应", "承诺",
    "第一次", "最后一个", "再也没",
)
_EMOTION_WEAK = (
    "压力", "开心", "高兴", "兴奋", "喜欢", "讨厌", "烦", "累", "孤独", "心情",
)


# ---- 时间小工具 ----------------------------------------------------------

def _fmt_gap(seconds: float) -> tuple[str, float]:
    """把静默时长翻成阶梯语义 + 小时数（文档 §三·①）。"""
    h = max(0.0, seconds / 3600.0)
    if h < 1.0:
        return "刚刚还在聊", h
    if h < 3.0:
        return "短暂离开（1–3 小时）", h
    if h < 12.0:
        return "半天没来（3–12 小时）", h
    if h < 36.0:
        return "过了一夜（12–36 小时）", h
    return "好几天没来（超过 36 小时）", h


def _fmt_ago(ts: float, now: float) -> str:
    if not ts:
        return "时间不详"
    d = max(0.0, (now - ts) / 86400.0)
    if d < 1:
        return "今天"
    if d < 2:
        return "昨天"
    if d < 3:
        return "前天"
    return "%d 天前" % int(d)


def _time_band(now: float) -> str:
    h = time.localtime(now).tm_hour
    if 5 <= h < 9:
        return "清晨"
    if 9 <= h < 12:
        return "上午"
    if 12 <= h < 14:
        return "中午"
    if 14 <= h < 18:
        return "下午"
    if 18 <= h < 22:
        return "晚上"
    return "深夜"


def _is_meal_time(now: float) -> Optional[str]:
    h = time.localtime(now).tm_hour
    m = time.localtime(now).tm_min
    t = h * 60 + m
    if 11 * 60 + 30 <= t <= 13 * 60:
        return "午饭"
    if 17 * 60 + 30 <= t <= 19 * 60 + 30:
        return "晚饭"
    return None


def emotion_salience(text: str) -> float:
    """给一条记忆打"情绪重量"分（0~1+）。只用来**挑哪条先回访**，不回写记忆库。

    分档：强烈情感事件 0.34/个，弱信号（偏好/状态）0.15/个；长度只是弱加权。
    ⇒ 单独一条"喜欢冰美式"到不了 0.34 的门槛，因而不会被当"值得回访的事"。
    """
    t = text or ""
    strong = sum(1 for w in _EMOTION_STRONG if w in t)
    weak = sum(1 for w in _EMOTION_WEAK if w in t)
    base = min(1.0, strong * 0.34 + weak * 0.15)
    if len(t) >= 20:
        base += 0.15
    if len(t) >= 40:
        base += 0.1
    return round(base, 3)


# ---- 结果容器 ------------------------------------------------------------

@dataclass
class Situation:
    text: str = ""
    blocks: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


# 情境文本里出现任一标记，才算"有可锚的真实材料"（topic/memory/pending/interest 块）。
# prompts（决定要不要硬要锚点）与 pack（编造检测闸）共用这一定义，避免两处漂移。
# 情境文本里出现任一标记，才算"有可锚的真实材料"（longterm/topic/memory/pending/interest 块）。
# prompts（决定要不要硬要锚点）与 pack（编造检测闸）共用这一定义，避免两处漂移。
# 注：新增「更早之前聊过的事」标记——**只有真的取到了长期历史原话**才会出现该块，
#     所以它出现即代表"确有可引用的长期材料"，不会削弱无材料时的防护。
GROUND_MARKERS = ("最近几轮对话", "的记忆", "用户之前说要去", "感兴趣的东西",
                  "还没兑现", "更早之前聊过的事")


def has_grounding_material(sit_text: str) -> bool:
    t = sit_text or ""
    return any(m in t for m in GROUND_MARKERS)


# ---- 角色作用域读数（多角色不串味的**唯一入口**） -------------------------
#
# 背景（开发者 2026-10-12）：记忆库是"一个实例 + 全局 _character"的设计，读接口
# （recent_episodes / list_user_facts / memory_text）都走 `self._character`。
# 主动互动是**后台**触发，若此刻全局作用域恰好是空串（未隔离）或另一个角色，
# 读出来就会串味——A 角色的往事被拿给 B 角色主动开口，正是"各角色剥离不能串行"要治的。
# 所以本包**不信全局**：自己按 char_id 过滤原始条目（只读不写，零副作用、无竞态）。

def _scoped_memories(store: Any, cid: str, *, types: tuple[str, ...] = ()) -> list[dict]:
    """按角色作用域读记忆条目（返回原始 dict 列表，已排除 invalid_at）。

    隔离规则与 memory_store 一致：只取「本角色」+「通用（无 character 字段）」两类；
    cid 为空 → 只取通用（宁可少，绝不串）。
    """
    cid = (cid or "").strip()
    items: list[dict] = []

    def _gather() -> list[dict]:
        out: list[dict] = []
        for m in list(getattr(store, "_memories", []) or []):
            if not isinstance(m, dict) or not str(m.get("text") or "").strip():
                continue
            if m.get("invalid_at"):
                continue
            ch = str(m.get("character") or "").strip()
            if cid:
                if ch and ch != cid:
                    continue
            elif ch:
                continue
            if types and str(m.get("type") or "") not in types:
                continue
            out.append(m)
        return out

    try:
        lock = getattr(store, "_lock", None)
        if lock is not None:
            with lock:
                items = _gather()
        else:
            items = _gather()
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 作用域记忆读取失败（留空）", exc_info=True)
        return []
    return items


def _sample(seq: list, k: int) -> list:
    """从序列里**均匀取 k 个**（覆盖整段时间，而不是只取开头几条）。"""
    n = len(seq)
    if n <= k:
        return list(seq)
    step = n / float(k)
    return [seq[min(n - 1, int(i * step))] for i in range(k)]


# ---- 各块 -----------------------------------------------------------------

def _now_block(now: float) -> str:
    lt = time.localtime(now)
    wk = "一二三四五六日"[lt.tm_wday]
    parts = ["现在：%d 月 %d 日（周%s）%02d:%02d，%s" %
             (lt.tm_mon, lt.tm_mday, wk, lt.tm_hour, lt.tm_min, _time_band(now))]
    if lt.tm_hour >= 23 or lt.tm_hour < 5:
        parts.append("已属深夜")
    meal = _is_meal_time(now)
    if meal:
        parts.append("正在%s时段" % meal)
    return "；".join(parts)


def _gap_block(now: float, last_input_ts: float) -> tuple[str, dict]:
    if not last_input_ts or last_input_ts <= 0:
        return "", {}
    label, hours = _fmt_gap(now - last_input_ts)
    return "距上次互动：%s（约 %.1f 小时）" % (label, hours), {"gap_label": label, "gap_h": hours}


def _habit_block(now: float, base_dir: Path) -> tuple[str, dict]:
    try:
        from ..companion.session_clock import SessionClock
        s = SessionClock(base_dir).late_night_summary(now)
        streak = int(s.get("night_streak") or 0)
        mins = float(s.get("online_minutes_today") or 0.0)
        bits = []
        if streak >= 2:
            bits.append("已连续 %d 天凌晨还在（熬夜）" % streak)
        elif streak == 1 and s.get("is_late_night"):
            bits.append("今晚又熬到这个点")
        if mins >= 180:
            bits.append("今天已连续在线约 %.1f 小时" % (mins / 60.0))
        if not bits:
            return "", {"night_streak": streak, "online_minutes_today": mins}
        return "作息观察：" + "；".join(bits), {
            "night_streak": streak, "online_minutes_today": mins,
            "is_late_night": bool(s.get("is_late_night")),
        }
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 作息块取数失败（留空）", exc_info=True)
        return "", {}


def _memory_block(now: float, marks, flags: dict, char_id: str = "") -> tuple[str, list[dict]]:
    """情绪重 且 未回访 的记忆条目（episode + 用户事实）。

    读数一律走 `_scoped_memories`（按 char_id 过滤），**不依赖全局 _character**——
    否则切到别的角色后，这里会把别人的往事当成"她记得的事"拿去主动开口。
    """
    if not flags.get("emotion_weight", True):
        return "", []
    try:
        from ..companion.memory_store import get_memory_store
        store = get_memory_store()
        if store is None:
            return "", []
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 记忆库不可用（留空）", exc_info=True)
        return "", []

    cid = str(char_id or "")
    cands: list[dict] = []
    try:
        eps = _scoped_memories(store, cid, types=("episode",))
        eps.sort(key=lambda m: float(m.get("created_at") or 0), reverse=True)
        for m in eps[:20]:
            cands.append({"kind": "episode", "text": str(m.get("text") or ""),
                          "ts": float(m.get("created_at") or 0),
                          "key": str(m.get("id") or m.get("text") or "")})
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] episodes 读取失败", exc_info=True)
    try:
        facts = _scoped_memories(store, cid, types=("user", "world"))
        facts.sort(key=lambda m: float(m.get("updated_at") or 0), reverse=True)
        for m in facts[:20]:
            t = str(m.get("text") or "")
            cands.append({"kind": "fact", "text": t, "ts": 0.0, "key": t})
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] user_facts 读取失败", exc_info=True)

    fresh = []
    for c in cands:
        if not c["text"]:
            continue
        if marks is not None and cid and marks.recall_done(cid, c["key"]):
            # 「说过了就不再问，除非再说一次」：用户最近重提了这件事 → 撤销标记，重新获得开口资格
            if _rementioned_recently(c["text"], cid):
                try:
                    marks.unmark_recall(cid, c["key"])
                except Exception:  # noqa: BLE001
                    logger.debug("[proactive_pack] 撤销回访标记失败", exc_info=True)
            else:
                continue
        c["salience"] = emotion_salience(c["text"])
        if c["salience"] < 0.34:
            continue
        fresh.append(c)
    fresh.sort(key=lambda c: (c["salience"], c["ts"]), reverse=True)
    fresh = fresh[:3]
    if not fresh:
        return "", []
    lines = []
    for c in fresh:
        ago = _fmt_ago(c["ts"], now) if c["ts"] else "（时间不详）"
        who = "她记得的一件事" if c["kind"] == "episode" else "关于用户的事实"
        lines.append("- [%s·%s] %s" % (who, ago, c["text"][:80]))
    return "情绪上比较重、而且还没回访过的记忆：\n" + "\n".join(lines), fresh


def _rementioned_recently(text: str, char_id: str = "") -> bool:
    """用户最近是否**重提**了这条记忆里的关键情绪词（「感冒了」又说一遍 → 该再问一次）。"""
    words = [w for w in (_EMOTION_STRONG + _EMOTION_WEAK) if w in (text or "")]
    if not words:
        return False
    try:
        from ..companion.conversation_history import shared_history_for
        utts = shared_history_for(char_id or "").recent_user_texts(n=12) or []
    except Exception:  # noqa: BLE001
        return False
    return any(any(w in u for w in words) for u in utts)


def detect_pending_activity(char_id: str, now: float) -> Optional[dict]:
    """从最近用户发言里找「宣告要做的事」（我去打游戏 / 先睡了 / 去开会…）。

    返回 {"label","expected_s","ts","text"} 或 None。只认最近 _PENDING_LOOKBACK_H 小时内的宣告。
    """
    try:
        from ..companion.conversation_history import shared_history_for
        h = shared_history_for(char_id or "")
        turns = list(getattr(h, "_turns", []) or [])
    except Exception:  # noqa: BLE001
        return None
    cut = now - _PENDING_LOOKBACK_H * 3600.0
    for t in reversed(turns[-40:]):
        if str(t.get("role") or "") != "user":
            continue
        ts = float(t.get("ts") or 0.0)
        if ts <= 0:
            continue
        if ts < cut:
            break                      # 时间正序，倒着走到超窗即可停
        text = str(t.get("content") or "")
        for pat, label, exp in _PENDING_PATTERNS:
            try:
                if re.search(pat, text):
                    return {"label": label, "expected_s": float(exp),
                            "ts": ts, "text": text[:60]}
            except Exception:  # noqa: BLE001
                logger.debug("[proactive_pack] 宣告正则异常（跳过该条）", exc_info=True)
    return None


def _pending_block(char_id: str, now: float) -> tuple[str, dict]:
    act = detect_pending_activity(char_id, now)
    if not act:
        return "", {}
    return ("用户之前说要去%s（「%s」），之后还没回来" % (act["label"], act["text"]),
            {"pending_activity": act})


def _extract_tags_from_texts(texts: list[str], cap: int = 8) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for t in texts:
        m = _INTEREST_RE.search(t or "")
        if not m:
            continue
        cand = _INTEREST_TRIM.sub("", m.group(1).strip("的了呀哈哦嗯～~。")).strip()
        if len(cand) >= 2 and cand not in seen:
            seen.add(cand)
            tags.append(cand)
        if len(tags) >= cap:
            break
    return tags


def extract_interest_tags(char_id: str) -> list[str]:
    """兴趣画像：只从**用户亲口表达过的喜好**里抽（记忆库事实 + 长期历史原话 + 最近原话）。

    三者都**按角色隔离**：记忆库走 `_scoped_memories`；历史走 shared_history_for / 落盘文件
    （天然分角色）。长期历史使兴趣不只反映"最近几轮"，而是这个用户一贯的喜好。
    """
    raw: list[str] = []
    try:
        from ..companion.memory_store import get_memory_store
        store = get_memory_store()
        if store is not None:
            facts = _scoped_memories(store, char_id, types=("user", "world"))
            facts.sort(key=lambda m: float(m.get("updated_at") or 0), reverse=True)
            raw += [str(m.get("text") or "") for m in facts[:40]]
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..companion.conversation_history import shared_history_for
        raw += [str(t) for t in (shared_history_for(char_id or "").recent_user_texts(n=12) or [])]
    except Exception:  # noqa: BLE001
        pass
    # 长期：整段历史里的用户原话（均匀取样，覆盖早中晚各阶段）
    try:
        rows = _longterm_history_rows(char_id)
        users = [str(r.get("content") or "") for r in rows if str(r.get("role") or "") == "user"]
        raw += _sample(users, 60)
    except Exception:  # noqa: BLE001
        pass
    return _extract_tags_from_texts(raw, cap=8)


def _interest_block(char_id: str) -> tuple[str, dict]:
    tags = extract_interest_tags(char_id)
    if not tags:
        return "", {"interest_tags": []}
    return ("用户感兴趣的东西（可用于分享话题）：" + "、".join(tags)), {"interest_tags": tags}


def _longterm_history_rows(char_id: str, base_dir: Optional[Path | str] = None) -> list[dict]:
    """长期对话历史（**按角色隔离**）——三级回退，任一可用即返回。

    ① `conversation_history.load_context`（运行时落盘 `data/context/<角色>.jsonl`，最全）；
    ② `HistoryStore.load_recent`（`data/history/<角色>.jsonl`，分角色文件）；
    ③ 内存桶 `shared_history_for(char_id)._turns`（未开持久化时兜底，如单测 / 仿真）。
    """
    rows: list[dict] = []
    try:
        from ..companion.conversation_history import load_context
        rows = list(load_context(char_id, n=200) or [])
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        try:
            from ..companion.history_store import HistoryStore
            base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
            rows = list(HistoryStore(base).load_recent(char_id, n=200) or [])
        except Exception:  # noqa: BLE001
            rows = []
    if not rows:
        try:
            from ..companion.conversation_history import shared_history_for
            rows = list(getattr(shared_history_for(char_id), "_turns", []) or [])
        except Exception:  # noqa: BLE001
            rows = []
    return [r for r in rows
            if isinstance(r, dict) and str(r.get("content") or r.get("text") or "").strip()]


def _longterm_block(char_id: str, now: float,
                    base_dir: Optional[Path | str] = None) -> tuple[str, dict]:
    """长期上下文块：关系跨度 + **更早之前的对话片段**。

    为什么必须有（开发者 2026-10-12）：只喂"最近几轮"，主动开口就永远只能接眼前话题，
    像只有短期记忆的客服；而且"编造"与"真实长线记忆"在短窗口里根本分不清。
    这里给出：① 总共聊过多少轮 / 最早一次多久以前；② 均匀取样若干**更早的用户原话**，
    让她能自然回指长期话题（同时这也给审查器提供了"哪些长期事实是真的"的判据）。
    """
    rows = _longterm_history_rows(char_id, base_dir)
    data: dict[str, Any] = {"history_turns": len(rows)}
    if not rows:
        return "", data
    out: list[str] = []
    ts_list = [float(r.get("ts") or 0) for r in rows if float(r.get("ts") or 0) > 0]
    if ts_list:
        span_days = max(0.0, (now - min(ts_list)) / 86400.0)
        data["history_first_days"] = int(span_days)
        when = ("%d 天前" % int(span_days)) if span_days >= 1 else "今天"
        out.append("你们已经聊过约 %d 轮，最早一次在 %s。" % (len(rows), when))
    # 更早的片段：跳过最近 8 条（那部分归"最近几轮"块），均匀取样
    older = rows[:-8] if len(rows) > 8 else []
    older_users = [str(r.get("content") or r.get("text") or "").strip() for r in older
                   if str(r.get("role") or "") == "user"]
    older_users = [t for t in older_users if t]
    if older_users:
        picks = _sample(older_users, 6)
        out.append("更早之前聊过的事（可以自然回指，但**只能引用这里写到的**）：\n"
                   + "\n".join("- " + p[:50] for p in picks))
        data["longterm_samples"] = len(picks)
    return "\n".join(out), data


def _topic_block(char_id: str) -> tuple[str, dict]:
    """最近几轮对话摘要 + 未兑现承诺。**承诺读取按角色隔离**（此前遍历全库，会串味）。"""
    out: list[str] = []
    data: dict[str, Any] = {}
    try:
        from ..companion.conversation_history import shared_history_for
        h = shared_history_for(char_id)
        snap = h.snapshot_text(n=4, sep="\n")
        if snap:
            out.append("最近几轮对话：\n" + snap)
        # 未兑现承诺：优先读记忆库里 source=="promise" 的确定性条目（**按本角色过滤**）
        promises: list[str] = []
        try:
            from ..companion.memory_store import get_memory_store
            store = get_memory_store()
            if store is not None:
                for m in _scoped_memories(store, char_id):
                    if str(m.get("source") or "") == "promise":
                        txt = str(m.get("text") or "").strip()
                        if txt:
                            promises.append(txt)
            promises = promises[-3:]
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] 承诺读记忆失败，回退正则", exc_info=True)
        if not promises:
            import re as _re
            for t in h.recent_user_texts(n=12):
                if _re.search(PROMISE_RE, t or ""):
                    promises.append(t.strip()[:40])
            promises = promises[-3:]
        if promises:
            out.append("用户说过但还没兑现的事：\n" + "\n".join("- " + p for p in promises))
        data["promises"] = promises
        data["turns"] = len(getattr(h, "_turns", []) or [])
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 话题块取数失败（留空）", exc_info=True)
    return "\n".join(out), data


def _relation_block(char_id: str) -> str:
    try:
        from ..companion.relationship import get_relationship_manager
        return get_relationship_manager().assemble_relation_block(char_id, tier="local") or ""
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 关系块取数失败（留空）", exc_info=True)
        return ""


# ---- 认知材料：今日电脑使用 / 昨日状态（只读 data/activity/） -----------------
#
# 为什么要接（开发者 2026-10-12）：项目早已在 `data/activity/` 里持续积累"用户每天在
# 电脑上做了什么"（events.jsonl）并自动生成日报/周报，但**主动包完全没读** —— 于是每次
# 开口只能靠对话记忆，看不到"他今天一直在敲代码""到现在还没吃晚饭"这种最像真人的贴身
# 材料。这里**只读、只做客观陈述（不调 LLM）**，无数据则块留空，绝不硬编。

def _extract_section(md: str, title: str) -> str:
    """从 Markdown 里取某个二级小节正文（用于日报的「一天小结」）。"""
    m = re.search(r"##+\s*%s\s*\n+(.+?)(?:\n##|\Z)" % re.escape(title), md or "", re.S)
    return (m.group(1).strip() if m else "")


def _usage_block(now: float, base_dir: Path) -> tuple[str, dict]:
    """今日电脑使用实况（客观记录：常用应用 / 游戏 / 最近在做什么）。"""
    try:
        from ..companion.activity_log import ActivityStore
    except Exception:  # noqa: BLE001
        return "", {}
    try:
        store = ActivityStore(str(Path(base_dir) / "activity"))
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        stats = store.stats_for_day(day)
        n = int(stats.get("event_count") or 0)
        if n <= 0:
            return "", {"activity_event_count": 0}
        apps = stats.get("apps") or []
        games = stats.get("games") or []
        lines: list[str] = []
        if apps:
            lines.append("今天在电脑上常用的：" + "、".join("%s(%d次)" % (a, c) for a, c in apps[:4]))
        if games:
            lines.append("今天玩过：" + "、".join(str(g) for g, _ in games[:3]))
        evs = store.events_for_day(day)
        last = evs[-1] if evs else None
        if last:
            hm = str(last.get("timestamp") or "")[11:16]
            lines.append("最近一次记录（约 %s）：%s" % (hm, str(last.get("text") or "")[:60]))
        if not lines:
            return "", {"activity_event_count": n}
        return ("今天用户电脑的使用实况（真实记录，可据此自然搭话；别当流水账念、"
                "别说成是你亲眼看到的）：\n- " + "\n- ".join(lines)), {
                    "activity_event_count": n,
                    "activity_apps": [a for a, _ in apps[:4]],
                    "activity_games": [g for g, _ in games[:3]],
                    "activity_last": str((last or {}).get("text") or "")[:60],
                }
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] usage 块取数失败（留空）", exc_info=True)
        return "", {}


def _dayreview_block(now: float, base_dir: Path) -> tuple[str, dict]:
    """昨天日报的「一天小结」片段（自动生成的真实材料，给她"你的昨天"的语感）。"""
    try:
        from ..companion.activity_log import ActivityStore
    except Exception:  # noqa: BLE001
        return "", {}
    try:
        store = ActivityStore(str(Path(base_dir) / "activity"))
        yday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
        txt = store.read_daily(yday)
        if not txt:
            return "", {}
        summary = _extract_section(txt, "一天小结") or _extract_section(txt, "小结")
        summary = re.sub(r"\s+", " ", summary).strip()
        if not summary:
            return "", {}
        return ("昨天这位用户的大致状态（来自自动日报，可自然引用；别照念、别添油加醋）："
                + summary[:140]), {"day_review": True}
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] dayreview 块取数失败（留空）", exc_info=True)
        return "", {}


# 看屏观察里的"垃圾行"（视觉模型自己声明看不清的输出，没有信息量）
_SCREEN_JUNK_RE = re.compile(r"画面不清晰|看不太清")
# 兴趣信号（代码级、确定性）：从看屏原话里数高频活动类别。
# 只数事实频次、不做语义解读——"他爱好什么"的解读交给生成端 LLM 拿着材料自己说。
_SCREEN_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("打游戏", ("游戏", "射击", "对局", "开枪")),
    ("看视频/直播", ("视频", "直播", "抖音", "B站", "bilibili", "短视频")),
    ("写代码/调试", ("代码", "编程", "IDE", "编辑器", "调试", "vscode", "终端")),
    ("浏览网页", ("网页", "浏览", "搜索")),
    ("看文档/资料", ("文档", "PDF", "阅读", "论文")),
    ("聊天", ("聊天", "微信", "QQ")),
)


def _screen_block(now: float, base_dir: Path) -> tuple[str, dict]:
    """她此前"看屏"看到的画面：通用视觉模型的**第一手原始观察**。

    数据源：`data/activity/events.jsonl` 的 `screen` 事件（vision_analyzer
    通用看屏 VISION_PROMPT 的原话）+ `game` 事件（GameOcrMonitor 的关键时刻：
    击杀/胜负）。此前只有日报这条"LLM 二次总结"的间接路，第一手观察从没被
    主动包读过（开发者 2026-10-12 指出）。

    只读 + 确定性处理（垃圾行剔除 / 去重 / 频次统计），不调 LLM；兴趣信号只
    给**带频次的活动类别**这类可验证事实，不做语义解读。无数据则块留空。
    """
    try:
        from ..companion.activity_log import ActivityStore
    except Exception:  # noqa: BLE001
        return "", {}
    try:
        store = ActivityStore(str(Path(base_dir) / "activity"))
        horizon = now - 6 * 3600  # 最近 6 小时：太旧的画面不再是"此刻语境"
        obs: list[tuple[str, str]] = []          # (HH:MM, 原话) 时间正序
        topics: dict[str, int] = {}              # 活动类别 → 出现次数（全量计数）
        game_tally: dict[str, int] = {}          # 游戏事件 → 次数
        last_game = ""
        seen: set[str] = set()
        for day in (time.strftime("%Y-%m-%d", time.localtime(now - 86400)),
                    time.strftime("%Y-%m-%d", time.localtime(now))):
            for e in store.events_for_day(day):
                ts = str(e.get("timestamp") or "")
                try:
                    et = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:  # noqa: BLE001
                    continue
                if not (horizon <= et <= now + 60):
                    continue
                kind = str(e.get("kind") or "")
                text = str(e.get("text") or "").strip()
                if kind == "game":
                    verb = text.split("：", 1)[-1].strip()
                    if verb:
                        game_tally[verb] = game_tally.get(verb, 0) + 1
                        last_game = "%s %s" % (ts[11:16], verb)
                    continue
                if kind != "screen" or not text or _SCREEN_JUNK_RE.search(text):
                    continue
                for label, kws in _SCREEN_TOPICS:
                    if any(k in text for k in kws):
                        topics[label] = topics.get(label, 0) + 1
                        break
                key = re.sub(r"\s+", "", text)
                if key in seen:      # 去重只作用于"展示的观察行"，频次照记
                    continue
                seen.add(key)
                obs.append((ts[11:16], text))
        if not obs and not game_tally:
            return "", {}
        lines: list[str] = []
        if topics:
            top = sorted(topics.items(), key=lambda x: -x[1])[:3]
            lines.append("最近 6 小时他大概在做：" + "、".join("%s(%d次)" % (k, v) for k, v in top))
        for hm, tx in obs[-4:]:
            lines.append("约 %s：%s" % (hm, tx[:40]))
        if game_tally:
            parts = ["%s%d次" % (k, v)
                     for k, v in sorted(game_tally.items(), key=lambda x: -x[1])[:3]]
            tail = ("；最近一次 %s" % last_game) if last_game else ""
            lines.append("游戏战况（最近 6 小时）：" + "、".join(parts) + tail)
        if not lines:
            return "", {}
        return ("她此前'看屏'留下的画面观察（通用视觉识别的客观记录；转述自然点、"
                "别报流水账、别说成是你此刻亲眼看到的）：\n- " + "\n- ".join(lines)), {
                    "screen_obs_count": len(obs),
                    "screen_topics": [k for k, _ in sorted(topics.items(), key=lambda x: -x[1])[:3]],
                    "screen_game_tally": dict(sorted(game_tally.items(), key=lambda x: -x[1])[:3]),
                }
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] screen 块取数失败（留空）", exc_info=True)
        return "", {}


# ---- 主入口 ---------------------------------------------------------------

def collect(char_id: str, *, now: Optional[float] = None,
            last_input_ts: float = 0.0,
            base_dir: Optional[Path | str] = None,
            flags: Optional[dict] = None,
            marks: Optional[Any] = None) -> Situation:
    """装配情境事实包。任何一块失败只让该块留空，整体绝不抛。"""
    t = time.time() if now is None else now
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    flags = flags or {}

    blocks: dict[str, str] = {}
    data: dict[str, Any] = {}

    try:
        blocks["now"] = _now_block(t)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] now 块失败", exc_info=True)

    try:
        g, gd = _gap_block(t, float(last_input_ts or 0.0))
        if g:
            blocks["gap"] = g
        data.update(gd)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] gap 块失败", exc_info=True)

    try:
        hb, hd = _habit_block(t, base)
        if hb:
            blocks["habit"] = hb
        data.update(hd)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] habit 块失败", exc_info=True)

    try:
        lt, ltd = _longterm_block(char_id, t, base)
        if lt:
            blocks["longterm"] = lt
        data.update(ltd)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] longterm 块失败", exc_info=True)

    try:
        mb, cands = _memory_block(t, marks, flags, char_id)
        if mb:
            blocks["memory"] = mb
        data["memory_candidates"] = cands
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] memory 块失败", exc_info=True)

    try:
        pb, pd = _pending_block(char_id, t)
        if pb:
            blocks["pending"] = pb
        data.update(pd)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] pending 块失败", exc_info=True)

    try:
        ib, idata = _interest_block(char_id)
        if ib:
            blocks["interest"] = ib
        data.update(idata)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] interest 块失败", exc_info=True)

    try:
        tb, td = _topic_block(char_id)
        if tb:
            blocks["topic"] = tb
        data.update(td)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] topic 块失败", exc_info=True)

    try:
        rb = _relation_block(char_id)
        if rb:
            blocks["relation"] = rb
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] relation 块失败", exc_info=True)

    if flags.get("usage_context", True):
        try:
            ub, ud = _usage_block(t, base)
            if ub:
                blocks["usage"] = ub
            data.update(ud)
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] usage 块失败", exc_info=True)
        try:
            db, dd = _dayreview_block(t, base)
            if db:
                blocks["dayreview"] = db
            data.update(dd)
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] dayreview 块失败", exc_info=True)

    if flags.get("screen_context", True):
        try:
            sb, sd = _screen_block(t, base)
            if sb:
                blocks["screen"] = sb
            data.update(sd)
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] screen 块失败", exc_info=True)

    if not flags.get("situation", True):
        # 情境包被关掉 → 退回"极简事实"，至少还有时段与静默（不至于退回万能句）
        blocks = {k: v for k, v in blocks.items() if k in ("now", "gap")}

    # ⚠️ order 必须与上面装配的块一致：longterm 曾被这里漏掉（装配了却不进文本，
    # 长程历史静默失效）——selftest 里有"longterm 进文本"的回归守卫（t_situation）。
    order = ["now", "gap", "habit", "usage", "screen", "pending", "memory",
             "interest", "topic", "longterm", "dayreview", "relation"]
    text = "\n".join(blocks[k] for k in order if blocks.get(k))
    return Situation(text=text, blocks=blocks, data=data)
