# -*- coding: utf-8 -*-
"""主动互动包 · 四类触发器的"何时说"判定（纯代码，确定性）。

设计原则（文档 §要素5）：**"何时说"与"说什么"彻底分离** ——
  本模块只回答"此刻该不该开口、属于哪一类"，**绝不产出预设台词**；
  内容永远由 `prompts.py` 现场即兴生成。

优先级（高 → 低，命中即返回，不重复开口）：
  ① 节日        （当天首次进入，且今年未用过该节日）
  ② 宣告跟进    （「我去玩游戏」→ 超预期没回来 →「打完了？」；一次宣告只问一次）
  ③ 会话内跟进  （1–3h 内有 ≥3 轮 + 已静默；**不吃宿主的 24h 冷却**）
  ④ 关系型召回  （有"情绪重且未回访"的条目 + 到该回访的时间窗）
  ⑤ 生活节律    （饭点未聊 / 熬夜 streak）
  ⑥ 兴趣分享    （抖音式：用户兴趣 × 真实素材，长静默才用；没素材绝不硬编）
  ⑦ 长期静默召回（按阶梯，兜底；此档允许自然地问「在忙什么」）

注：本文件内的中文引号统一用「」（避免与 Python 字符串分隔符冲突）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import festivals
from .situation import Situation

logger = logging.getLogger("proactive_pack")


def _daypart(now: float) -> str:
    """虚拟时刻 → 时段词（给 brief 做"措辞随时间段变化"，治千篇一律的寒暄）。"""
    h = time.localtime(now).tm_hour
    if 5 <= h < 10:
        return "早上"
    if 10 <= h < 11:
        return "上午"
    if 11 <= h < 14:
        return "午饭点"
    if 14 <= h < 17:
        return "下午"
    if 17 <= h < 19:
        return "傍晚"
    if 19 <= h < 22:
        return "晚上"
    return "深夜"


# 久别招呼的开口方向（按小时轮换，避免每次都是同一句"好久不见今天过得怎么样"）。
_GREET_DIRS = (
    "自然招呼一声就好，比如问问在忙什么",
    "轻轻带一句'好久不见'，再问问他今天过得怎么样",
    "从当前时间点切入，比如'这个点还没忙完？'",
    "像随口聊天那样，问问这个点吃饭了没",
    "接一句日常寒暄，问问今天怎么样、事情顺不顺利",
)

# 饭点关心的问法方向（按小时轮换，治"每顿都是吃了吗"的复读）。
_MEAL_DIRS = (
    "随口问一句吃了没",
    "问他想好吃什么了没",
    "提醒一句别忙忘了吃饭，顺便问这顿怎么解决",
    "问问这顿是外卖还是自己做",
)


@dataclass
class Trigger:
    kind: str                       # festival / pending / followup / recall / rhythm / share / silence
    priority: int                   # 越小越先
    brief: str                      # 给模型的"这次为什么开口"（不是台词！）
    dedup_key: str = ""
    needs_gate: bool = True         # 是否需要先过宿主关系闸门（_proactive_decision）
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    cid: str
    now: float
    last_input_ts: float
    flags: dict[str, Any]
    marks: Any                      # MarksStore
    sit: Situation
    base_dir: Any = None            # 兴趣分享读素材池用（data/proactive_pack_feed.json）


# 状态类事件词：这类事（感冒/面试…）通常几天内就有结果，回访窗口比一般记忆短得多
_STATUS_WORDS = (
    "感冒", "发烧", "生病", "咳嗽", "头疼", "胃疼", "面试", "考试", "答辩",
    "体检", "看病", "手术", "出院", "出差", "搬家", "离职", "入职",
)


# ---- ① 节日 / 纪念日 ------------------------------------------------------

def _ev_festival(ctx: Ctx) -> Optional[Trigger]:
    f = festivals.festival_today(ctx.now)
    name = ""
    if f:
        name = f["name"]
    else:
        a = festivals.anniversary_today(ctx.cid, ctx.now)
        if a:
            name = "%s（认识第 %d 天）" % (a["name"], a["days"])
    if not name:
        return None
    try:
        if ctx.marks is not None and ctx.marks.festival_sent(ctx.cid, name, ctx.now):
            return None
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 节日去重判定失败", exc_info=True)
    # 节日不吃"24h 频率冷却"（昨天聊过不代表今天不能祝），但仍受静默时段约束。
    return Trigger(
        kind="festival", priority=10,
        brief="今天是个特别的日子：%s。用一句干净的祝福或问候开个头（说完就停，不追问、不制造期待）。" % name,
        dedup_key="festival:%s" % name, needs_gate=True,
        meta={"festival": name},
    )


# ---- ② 宣告事件跟进（「我去玩游戏」→ 超预期没回来 →「打完了？」） ----------------

def _ev_pending(ctx: Ctx) -> Optional[Trigger]:
    act = ctx.sit.get("pending_activity") or {}
    if not act:
        return None
    declared = float(act.get("ts") or 0.0)
    if declared <= 0:
        return None
    # 宣告必须（几乎）是最后说的话——宣告之后还聊了一堆别的，说明已经回来过，不追
    if declared < ctx.last_input_ts - 900.0:
        return None
    expected = float(act.get("expected_s") or 3600.0)
    since = ctx.now - declared
    if since < expected:
        return None
    label = str(act.get("label") or "那件事")
    key = "pending:%s:%d" % (label, int(declared))
    try:
        if ctx.marks is not None and ctx.marks.pending_done(ctx.cid, key):
            return None
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] pending 去重判定失败", exc_info=True)
    return Trigger(
        kind="pending", priority=15,
        brief="用户之前说要去%s（「%s」），到现在 %.1f 小时没消息了。像随口一问那样自然地问一句进展，"
              "比如「%s完了？」。**只问这一件事**：别把别的话题（吃饭/别的活动）拼进同一句。"
              "**只问这一次**：对方没回就不再催第二遍，答了就正常接话。"
              % (label, str(act.get("text") or ""), since / 3600.0, label),
        dedup_key=key, needs_gate=False,
        meta={"label": label, "declared_ts": declared},
    )


# ---- ③ 会话内跟进 --------------------------------------------------------

def _ev_followup(ctx: Ctx) -> Optional[Trigger]:
    if ctx.sit.get("pending_activity"):
        return None            # 有具体宣告在身 → 交给 pending（锚点更实），这里让位
    flags = ctx.flags
    turns = int(ctx.sit.get("turns") or 0)
    if turns < 6:                                   # ≥3 轮发言（user+assistant 计）
        return None
    gap_h = float(ctx.sit.get("gap_h") or 0.0)
    lo = float(flags.get("followup_silence_h", 1.0))
    hi = float(flags.get("followup_silence_max_h", 3.0))
    if not (lo <= gap_h <= hi):
        return None
    if ctx.now - float(ctx.last_input_ts or 0) > hi * 3600.0:
        return None
    try:
        times = ctx.marks.followup_times(ctx.cid, ctx.now) if ctx.marks else []
    except Exception:  # noqa: BLE001
        times = []
    if len(times) >= int(flags.get("followup_per_day", 2)):
        return None
    cd = float(flags.get("followup_cooldown_h", 12.0)) * 3600.0
    if times and (ctx.now - max(times)) < cd:
        return None
    return Trigger(
        kind="followup", priority=20,
        brief="刚才你们正聊得起劲，然后人走开了（约 %.1f 小时）。现在是%s，"
              "接着刚才的话题自然补一句，或说说你自己的一件小事；"
              "可以从当前时间段的状态自然切入（快到饭点、下午、晚上该休息了），"
              "别每次都用同一种开场。**绝不问「你去哪了」、不催、不抱怨**。"
              % (gap_h, _daypart(ctx.now)),
        dedup_key="followup:%d" % int(ctx.now // 3600), needs_gate=False,
        meta={"gap_h": gap_h},
    )


# ---- ④ 关系型召回 --------------------------------------------------------

def _ev_recall(ctx: Ctx) -> Optional[Trigger]:
    cands = ctx.sit.get("memory_candidates") or []
    if not cands:
        return None
    top = cands[0]
    ts = float(top.get("ts") or 0.0)
    text = str(top.get("text") or "")
    min_age = float(ctx.flags.get("recall_min_age_h", 24.0)) * 3600.0
    # 状态类事件（感冒/面试…）通常几天内就有结果：问晚了反而像没在听。窗口收窄。
    is_status = any(w in text for w in _STATUS_WORDS)
    default_window = (float(ctx.flags.get("recall_status_window_days", 5.0)) if is_status
                      else float(ctx.flags.get("recall_window_days", 14.0)))
    max_age = default_window * 86400.0
    if ts > 0:
        age = ctx.now - ts
        if age < min_age:
            return None
        if age > max_age:
            return None
    # 每日回访上限（2026-10-12 补：该配置项此前未被读取，实测出现同一天连访 5 条记忆）
    try:
        times = ctx.marks.recall_times(ctx.cid, ctx.now) if ctx.marks else []
    except Exception:  # noqa: BLE001
        times = []
    if len(times) >= int(ctx.flags.get("recall_per_day", 1)):
        return None
    return Trigger(
        kind="recall", priority=30,
        brief="你想起了关于用户的一件事（见情境包里的记忆条目），自然地回访一句近况。"
              "**不要机械复述「我记得你…」**，像朋友随口一问。",
        dedup_key="recall:%s" % str(top.get("key") or top.get("text") or "")[:60],
        needs_gate=True, meta={"candidate": top},
    )


# ---- ⑤ 生活节律 ----------------------------------------------------------

def _ev_rhythm(ctx: Ctx) -> Optional[Trigger]:
    flags = ctx.flags
    cd = float(flags.get("rhythm_cooldown_h", 5.0)) * 3600.0
    streak = int(ctx.sit.get("night_streak") or 0)
    is_night = bool(ctx.sit.get("is_late_night"))

    # 熬夜（数据现成：session_clock.late_night_summary）
    if streak >= 2 and is_night:
        if ctx.marks and (ctx.now - ctx.marks.rhythm_last(ctx.cid, "late_night")) < cd:
            return None
        return Trigger(
            kind="rhythm", priority=40,
            brief="用户连着 %d 天熬到这个点了。说事实、劝一句就够，"
                  "**不命令、不说「你必须睡」**。" % streak,
            dedup_key="rhythm:late_night", needs_gate=True,
            meta={"rhythm": "late_night", "night_streak": streak},
        )

    # 饭点（当天没聊过饭）
    from .situation import _is_meal_time   # 复用同一判据，避免两处口径不一致
    meal = _is_meal_time(ctx.now)
    if meal:
        try:
            if ctx.marks and ctx.marks.meal_pinged(ctx.cid, ctx.now):
                return None
        except Exception:  # noqa: BLE001
            pass
        if ctx.marks and (ctx.now - ctx.marks.rhythm_last(ctx.cid, "meal")) < cd:
            return None
        return Trigger(
            kind="rhythm", priority=41,
            brief="现在是%s时间，今天还没聊过这个。%s（别像查岗）。"
                  % (meal, _MEAL_DIRS[int(ctx.now // 3600) % len(_MEAL_DIRS)]),
            dedup_key="rhythm:meal", needs_gate=True,
            meta={"rhythm": "meal", "meal": meal},
        )
    return None


# ---- ⑥ 兴趣分享（抖音式：兴趣 × 真实素材；没素材/没兴趣绝不硬编） -----------

def _ev_share(ctx: Ctx) -> Optional[Trigger]:
    tags = ctx.sit.get("interest_tags") or []
    if not tags:
        return None                      # 没有把握的兴趣画像 → 宁可不说
    gap_h = float(ctx.sit.get("gap_h") or 0.0)
    if gap_h < float(ctx.flags.get("share_min_gap_h", 6.0)):
        return None
    try:
        times = ctx.marks.share_times(ctx.cid, ctx.now) if ctx.marks else []
    except Exception:  # noqa: BLE001
        times = []
    if len(times) >= int(ctx.flags.get("share_per_day", 1)):
        return None
    try:
        from . import feed
        items = feed.load_items(ctx.base_dir)
    except Exception:  # noqa: BLE001
        logger.debug("[proactive_pack] 素材池读取失败（本次不分享）", exc_info=True)
        return None
    if not items:
        return None
    pick_item = None
    for it in sorted(items, key=lambda x: float(x.get("ts") or 0.0), reverse=True):
        if not feed.matches(it, tags):
            continue
        try:
            if ctx.marks is not None and ctx.marks.share_item_done(ctx.cid, feed.item_key(it)):
                continue
        except Exception:  # noqa: BLE001
            pass
        pick_item = it
        break
    if pick_item is None:
        return None
    return Trigger(
        kind="share", priority=55,
        brief="你刷到一条用户可能感兴趣的消息：「%s」（和他喜欢的%s有关）。像朋友随手转发那样，"
              "用一两句自己的话讲给他听，别像新闻播报、别硬转话题、别推销。"
              % (str(pick_item.get("text") or ""), "、".join(tags[:3])),
        dedup_key="share:%s" % feed.item_key(pick_item)[:60],
        needs_gate=True, meta={"item": pick_item},
    )


# ---- ⑦ 长期静默召回 ------------------------------------------------------

def _ev_silence(ctx: Ctx) -> Optional[Trigger]:
    gap_h = float(ctx.sit.get("gap_h") or 0.0)
    if gap_h < 3.0:
        return None
    label = str(ctx.sit.get("gap_label") or "")
    try:
        times = ctx.marks.silence_times(ctx.cid, ctx.now) if ctx.marks else []
    except Exception:  # noqa: BLE001
        times = []
    # 每日上限（与 share_per_day 同口径；默认 2 次，长缺席时更克制）
    if len(times) >= int(ctx.flags.get("silence_per_day", 2)):
        return None
    # 同一天同一档位只开口一次（静默阶梯本身已含"要不要说话"的语义）
    if times and (ctx.now - max(times)) < 3.0 * 3600.0:
        return None
    return Trigger(
        kind="silence", priority=60,
        brief="用户离开了挺久（%s），现在是%s。%s（久别之后这么问很正常，不算打扰），"
              "能接上次话题更好。每次开口换个说法，别重复同一句寒暄。"
              "**不卖惨、不表现「我一直在等你」、不催对方回复。**"
              % (label or "很久", _daypart(ctx.now),
                 _GREET_DIRS[int(ctx.now // 3600) % len(_GREET_DIRS)]),
        dedup_key="silence:%d" % int(ctx.now // 10800), needs_gate=True,
        meta={"gap_h": gap_h, "gap_label": label},
    )


_EVALUATORS = [
    ("festival", _ev_festival),
    ("pending", _ev_pending),
    ("followup", _ev_followup),
    ("recall", _ev_recall),
    ("rhythm", _ev_rhythm),
    ("share", _ev_share),
    ("silence", _ev_silence),
]


def evaluate(ctx: Ctx) -> Optional[Trigger]:
    """按优先级依次求值，命中即返回（不重复开口）。未启用 / 求值异常 → 跳过该类。"""
    for name, fn in _EVALUATORS:
        if not bool(ctx.flags.get(name, True)):
            continue
        try:
            trg = fn(ctx)
        except Exception:  # noqa: BLE001 单个触发器崩了不该拖垮整套
            logger.debug("[proactive_pack] 触发器 %s 求值异常（跳过）", name, exc_info=True)
            continue
        if trg is not None:
            return trg
    return None
