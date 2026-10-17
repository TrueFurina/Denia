# -*- coding: utf-8 -*-
"""主动互动包 · 自测（可脱离主软件独立运行）。

用法：
    python -m src.proactive_pack.selftest
    （或 python src/proactive_pack/selftest.py）

设计：**不依赖主软件启动**。宿主用桩对象（FakeHost），LLM 用桩（FakeLLM），
数据落临时目录；断言全部是"判据/落盘/优先级"这类确定性行为。
结果格式与项目既有 `tools/test_*.py` 一致：PASS/FAIL 计数 + 退出码。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.proactive_pack import (ProactivePack, build_proactive_pack, install, uninstall,  # noqa: E402
                                load_flags, write_state_flags, DEFAULTS, MarksStore)
from src.proactive_pack import festivals, prompts, situation, triggers  # noqa: E402
from src.proactive_pack.situation import Situation, emotion_salience  # noqa: E402
from src.proactive_pack.triggers import Ctx, evaluate  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


# ---- 桩 ------------------------------------------------------------------

class _Cfg:
    active_character_id = "danya"


class _Persona:
    name = "达妮娅"


class FakeLLM:
    def __init__(self, reply="回来啦？昨晚那局打得怎么样"):
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        return self.reply


class _GateLLM:
    """按调用次序返回不同回复：第 1 次=生成，第 2 次=闸门判定。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, messages, options=None):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return r


class _FlakyLLM:
    """第 1 次正常，之后抛异常——模拟闸门故障。"""

    def __init__(self, first):
        self.first = first
        self.calls = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        if self.calls == 1:
            return self.first
        raise RuntimeError("judge boom")


class _ScriptLLM:
    """按调用次序逐条返回预设回复（用尽后重复最后一条）；记录每次收到的 prompt 与 options。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.prompts = []
        self.options = []

    async def chat(self, messages, options=None):
        self.prompts.append("".join(str(m.get("content") or "") for m in (messages or [])))
        self.options.append(dict(options or {}))
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return r


class FakeHost:
    def __init__(self, reply="回来啦？昨晚那局打得怎么样"):
        self._cfg = _Cfg()
        self._persona = _Persona()
        self._llm = FakeLLM(reply)
        self._idle_threshold_s = 180.0
        self._last_input_ts = 0.0
        self._session_boot_ts = 0.0
        self._duplex_last_speak_ts = 0.0
        self._recent_messages = []
        self.emitted = []
        self._last_proactive = 0.0

    async def _schedule_duplex_emit(self, text, sys_fallback=False, to_chat=False):
        self.emitted.append({"text": text, "to_chat": to_chat})

    def _record_duplex_ai_line(self, text):
        self.recorded = text

    async def _log_duplex_source(self, kind, text):
        pass

    def _clean_duplex_speech(self, t):
        return (t or "").strip()

    def _similar_to_recent(self, t):
        return t in self._recent_messages

    async def check_and_trigger(self):
        self.orig_called = getattr(self, "orig_called", 0) + 1


# ---- 用例 -----------------------------------------------------------------

def t_flags(tmp):
    print("[1] 开关与配置")
    f = load_flags(None, tmp)
    check("默认总开关为关（缺省即关）", f["enabled"] is False, f)
    check("默认各子开关齐全",
          all(k in f for k in ("festival", "followup", "pending", "recall", "rhythm", "share", "silence")))
    f2 = dict(DEFAULTS)
    f2["enabled"] = True
    f2["day_cap"] = 3
    check("写运行时开关成功", write_state_flags(f2, tmp) is True)
    f3 = load_flags(None, tmp)
    check("运行时开关覆盖默认", f3["enabled"] is True and f3["day_cap"] == 3, f3)
    # 字符串布尔兼容
    write_state_flags({"enabled": "true"}, tmp)
    check("字符串 'true' 能转成布尔真", load_flags(None, tmp)["enabled"] is True)
    write_state_flags({"enabled": False}, tmp)


def t_marks(tmp):
    print("[2] 去重账本与回访标记")
    m = MarksStore(tmp)
    check("账本初始为 0", m.ledger_count(1000) == 0)
    m.ledger_bump(1000)
    check("记账 +1", m.ledger_count(1000) == 1)
    check("节日未发过", m.festival_sent("danya", "中秋节", 1000) is False)
    m.mark_festival("danya", "中秋节", 1000)
    check("节日标记生效", m.festival_sent("danya", "中秋节", 1000) is True)
    check("不同节日互不影响", m.festival_sent("danya", "春节", 1000) is False)
    check("回访未做过", m.recall_done("danya", "k1") is False)
    m.mark_recall("danya", "k1", 1000)
    check("回访标记生效", m.recall_done("danya", "k1") is True)
    m.mark_followup("danya", 1000)
    check("跟进计数", len(m.followup_times("danya", 1000)) == 1)
    # 宣告跟进 / 兴趣分享 / 回访撤销
    m.mark_pending("danya", "pending:游戏:1", 1000)
    check("宣告跟进标记生效（一次宣告一个 key）",
          m.pending_done("danya", "pending:游戏:1") and not m.pending_done("danya", "pending:游戏:2"))
    m.mark_share("danya", "n1", 1000)
    m.mark_share_day("danya", 1000)
    check("分享素材标记 + 日计数生效",
          m.share_item_done("danya", "n1") and len(m.share_times("danya", 1000)) == 1)
    check("撤销回访标记（用户重提 → 重新可问）",
          m.unmark_recall("danya", "k1") is True and m.recall_done("danya", "k1") is False)
    check("角色隔离", m.ledger_count(1000) >= 1 and m.recall_done("other", "k1") is False)
    # 损坏隔离
    p = Path(tmp) / "proactive_pack.json"
    p.write_text("{ 这不是合法 JSON", encoding="utf-8")
    m2 = MarksStore(tmp)
    m2.load()
    corrupts = list(Path(tmp).glob("*.corrupt-*"))
    check("坏文件被隔离改名（不静默吞掉）", len(corrupts) == 1, corrupts)


def t_festival(tmp):
    print("[3] 节日与纪念日")
    # 用固定时间戳构造 2 月 14 日
    d = time.mktime((2026, 2, 14, 20, 0, 0, 0, 0, -1))
    f = festivals.festival_today(d)
    check("公历情人节命中", bool(f) and f["name"] == "情人节", f)
    d2 = time.mktime((2026, 9, 25, 20, 0, 0, 0, 0, -1))
    f2 = festivals.festival_today(d2)
    check("农历中秋命中（2026-09-25）", bool(f2) and f2["name"] == "中秋节", f2)
    d3 = time.mktime((2026, 4, 7, 20, 0, 0, 0, 0, -1))
    check("普通日子返回 None", festivals.festival_today(d3) is None)
    check("无落盘历史时纪念日为 None（不猜）",
          festivals.days_together("nobody", time.time(), tmp) is None)
    # 有历史 → 推得出天数
    hp = Path(tmp) / "history"
    hp.mkdir(exist_ok=True)
    old = time.time() - 30 * 86400
    (hp / "danya.jsonl").write_text(json.dumps({"role": "user", "content": "hi", "ts": old}) + "\n",
                                    encoding="utf-8")
    days = festivals.days_together("danya", time.time(), tmp)
    check("纪念日可推算（约 31 天）", days is not None and 30 <= days <= 32, days)


def t_salience():
    print("[4] 情绪加权")
    hot = emotion_salience("用户说他下周三有个很重要的面试，最近压力特别大，有点焦虑")
    cold = emotion_salience("用户喜欢喝冰美式")
    check("情绪重的条目分更高", hot > cold, (hot, cold))
    check("平淡事实分较低", cold < 0.34, cold)


def t_situation(tmp):
    print("[5] 情境事实包")
    li = time.time() - 6 * 3600
    s = situation.collect("danya", last_input_ts=li, base_dir=tmp, flags=DEFAULTS, marks=MarksStore(tmp))
    check("返回 Situation 对象", isinstance(s, Situation))
    check("now 块必存在", "now" in s.blocks and s.blocks["now"], s.blocks)
    check("gap 块标注了阶梯语义", "半天没来" in (s.blocks.get("gap") or ""), s.blocks.get("gap"))
    check("文本可拼装", isinstance(s.text, str))
    # 关掉情境包 → 只留 now/gap
    f = dict(DEFAULTS); f["situation"] = False
    s2 = situation.collect("danya", last_input_ts=li, base_dir=tmp, flags=f, marks=MarksStore(tmp))
    check("关闭情境包后只剩极简块", set(s2.blocks).issubset({"now", "gap"}), s2.blocks)
    # order 回归守卫：longterm 曾被装配却不在 order 里 → 文本里永远见不到（静默失效）
    orig_rows = situation._longterm_history_rows
    try:
        situation._longterm_history_rows = lambda cid, base=None: [
            {"role": "user", "content": "我上个月去云南徒步了，超累但值", "ts": time.time() - 30 * 86400}
        ] + [{"role": "assistant", "content": "好棒", "ts": time.time()} for _ in range(9)]
        s3 = situation.collect("danya", last_input_ts=li, base_dir=tmp,
                               flags=DEFAULTS, marks=MarksStore(tmp))
        check("longterm 块进入情境文本（order 回归守卫）",
              "云南徒步" in (s3.text or ""), (s3.blocks or {}).get("longterm"))
    finally:
        situation._longterm_history_rows = orig_rows


def t_usage_context(tmp):
    print("[17] 认知材料：电脑使用 / 昨日状态 接入情境包")
    from src.companion.activity_log import ActivityStore
    store = ActivityStore(str(Path(tmp) / "activity"))
    now = time.time()
    store.append("app", "code：main.py - VSCode", {"app_hint": "code"})
    store.append("game", "三角洲行动 排位", {"game": "三角洲行动"})
    st = store.stats_for_day(time.strftime("%Y-%m-%d", time.localtime(now)))
    check("活动库 stats_for_day 读到事件", int(st.get("event_count") or 0) >= 2, st)
    s = situation.collect("danya", now=now, last_input_ts=now - 7200,
                          base_dir=tmp, flags=DEFAULTS, marks=MarksStore(tmp))
    check("usage 块进入情境文本", "电脑的使用实况" in (s.text or ""), s.blocks)
    check("usage 块标注最近在做什么", "最近一次记录" in (s.blocks.get("usage") or ""), s.blocks.get("usage"))
    check("data 带 activity_event_count", int(s.get("activity_event_count") or 0) >= 2,
          s.data.get("activity_event_count"))
    yday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
    store.write_daily(yday, "# %s 日报\n\n## 时间轴\n- 敲代码\n\n## 一天小结\n他昨晚通宵敲代码，凌晨才睡。\n" % yday)
    s2 = situation.collect("danya", now=now, last_input_ts=now - 7200,
                           base_dir=tmp, flags=DEFAULTS, marks=MarksStore(tmp))
    check("dayreview 块引用昨日小结", "通宵敲代码" in (s2.blocks.get("dayreview") or ""),
          s2.blocks.get("dayreview"))
    f2 = dict(DEFAULTS); f2["usage_context"] = False
    s3 = situation.collect("danya", now=now, last_input_ts=now - 7200,
                           base_dir=tmp, flags=f2, marks=MarksStore(tmp))
    check("usage_context=false 时不产 usage/dayreview 块",
          "usage" not in s3.blocks and "dayreview" not in s3.blocks, s3.blocks)


def t_screen_context(tmp):
    print("[18] 认知材料：看屏第一手观察 + 游戏战况 接入情境包")
    from src.companion.activity_log import ActivityStore
    store = ActivityStore(str(Path(tmp) / "activity"))
    now = time.time()
    store.append("screen", "在打游戏，画面是第一人称射击", {"app_hint": "game"})
    store.append("screen", "在打游戏，画面是第一人称射击", {"app_hint": "game"})   # 重复 → 展示去重、频次照记
    store.append("screen", "画面不清晰，看不太清用户在做什么", {})                # 垃圾行 → 剔除
    store.append("screen", "在使用编辑器写代码并查看网页预览", {"app_hint": "ide"})
    store.append("game", "游戏事件：victory", {"app_hint": "game"})
    store.append("game", "游戏事件：victory", {"app_hint": "game"})
    store.append("game", "游戏事件：defeat", {"app_hint": "game"})
    s = situation.collect("danya", now=now, last_input_ts=now - 7200,
                          base_dir=tmp, flags=DEFAULTS, marks=MarksStore(tmp))
    blk = s.blocks.get("screen") or ""
    check("screen 块进入情境文本", "看屏" in blk and blk, s.blocks)
    check("垃圾观察被剔除", "画面不清晰" not in blk, blk)
    check("重复观察只展示一次", blk.count("第一人称射击") == 1, blk)
    check("兴趣信号带频次", "打游戏(2次)" in blk, blk)
    check("游戏战况进了块", "victory2次" in blk and "最近一次" in blk, blk)
    check("data 带 screen_topics", "打游戏" in (s.get("screen_topics") or []),
          s.data.get("screen_topics"))
    f2 = dict(DEFAULTS); f2["screen_context"] = False
    s2 = situation.collect("danya", now=now, last_input_ts=now - 7200,
                           base_dir=tmp, flags=f2, marks=MarksStore(tmp))
    check("screen_context=false 时不产 screen 块", "screen" not in s2.blocks, s2.blocks)


def _ctx(tmp, **kw):
    now = kw.get("now", time.time())
    li = kw.get("last_input_ts", now - 6 * 3600)
    flags = kw.get("flags", dict(DEFAULTS, enabled=True))
    sit = kw.get("sit") or Situation(text="", blocks={}, data=kw.get("data", {}))
    return Ctx(cid=kw.get("cid", "danya"), now=now, last_input_ts=li,
               flags=flags, marks=kw.get("marks", MarksStore(tmp)), sit=sit,
               base_dir=kw.get("base_dir", tmp))


def _fixed_now() -> float:
    """触发器类用例的**固定时刻**：2026-03-10（周二）15:00。

    为什么必须钉死：`_ev_rhythm` 的饭点分支只看 `_is_meal_time(now)`，命中就返回
    priority=41 的 meal 节律，**抢在 silence/share 之前**（求值顺序 recall→rhythm→
    share→silence）。于是用 `time.time()` 的用例在午饭/晚饭时段跑必挂——这是
    「饭点跑自测假 FAIL」的老坑，与代码改动无关。固定成一个非饭点 / 非节日 / 非深夜
    的时刻，用例才只测它真正想测的优先级。
    """
    return time.mktime((2026, 3, 10, 15, 0, 0, 0, 0, -1))


def t_triggers(tmp):
    print("[6] 触发器优先级与判据")
    orig = festivals.festival_today
    try:
        festivals.festival_today = lambda now=None: {"name": "测试节", "kind": "solar"}
        ctx = _ctx(tmp, data={"gap_h": 6.0, "turns": 8, "gap_label": "半天没来"})
        trg = evaluate(ctx)
        check("节日优先级最高", trg is not None and trg.kind == "festival", trg)
    finally:
        festivals.festival_today = orig

    # 无节日 → 会话内跟进（turns>=6, gap 在 1~3h）
    now = _fixed_now()
    ctx = _ctx(tmp, now=now, last_input_ts=now - 2 * 3600,
               data={"gap_h": 2.0, "turns": 8, "gap_label": "短暂离开（1–3 小时）"})
    trg = evaluate(ctx)
    check("无节日时会话内跟进命中", trg is not None and trg.kind == "followup", trg)
    check("跟进不吃宿主 24h 冷却（needs_gate=False）", trg is not None and trg.needs_gate is False)

    # 轮次不够 → 不跟进
    ctx = _ctx(tmp, now=now, last_input_ts=now - 2 * 3600,
               data={"gap_h": 2.0, "turns": 2, "gap_label": "短暂离开（1–3 小时）"})
    check("轮次不足不跟进", evaluate(ctx) is None or evaluate(ctx).kind != "followup")

    # 静默太久（>3h）→ 不走跟进，落到静默召回
    ctx = _ctx(tmp, now=now, last_input_ts=now - 8 * 3600,
               data={"gap_h": 8.0, "turns": 8, "gap_label": "半天没来（3–12 小时）"})
    trg = evaluate(ctx)
    check("超过跟进窗口后落到静默召回", trg is not None and trg.kind == "silence", trg)

    # 静默 <3h 且轮次不足 → 不开口
    ctx = _ctx(tmp, now=now, last_input_ts=now - 1.5 * 3600,
               data={"gap_h": 1.5, "turns": 2})
    check("没到门槛不开口", evaluate(ctx) is None)

    # 关系型召回：有情绪候选 + 已超 24h
    ctx = _ctx(tmp, now=now, last_input_ts=now - 30 * 3600,
               data={"gap_h": 30.0, "turns": 0, "memory_candidates": [
                   {"key": "k2", "text": "面试", "ts": now - 3 * 86400, "salience": 0.9}]})
    trg = evaluate(ctx)
    check("关系型召回命中（情绪候选 + 到窗口）", trg is not None and trg.kind == "recall", trg)

    # 熬夜节律
    ctx = _ctx(tmp, now=now, last_input_ts=now - 4 * 3600,
               data={"gap_h": 4.0, "turns": 0, "night_streak": 4, "is_late_night": True})
    trg = evaluate(ctx)
    check("连续熬夜触发生活节律", trg is not None and trg.kind == "rhythm", trg)

    # 关掉某类 → 该类不再触发
    f = dict(DEFAULTS, enabled=True); f["silence"] = False; f["recall"] = False
    ctx = _ctx(tmp, now=now, last_input_ts=now - 8 * 3600, flags=f,
               data={"gap_h": 8.0, "turns": 8})
    check("关闭 silence/recall 后不再从这两类开口", evaluate(ctx) is None)


def t_pending(tmp):
    print("[10] 宣告事件跟进（去玩游戏 → 长时间没理 → 只问一次）")
    now = time.time()
    declared = now - 2.5 * 3600
    act = {"label": "游戏", "expected_s": 5400, "ts": declared, "text": "我去打两把游戏"}
    ctx = _ctx(tmp, now=now, last_input_ts=declared,
               data={"gap_h": 2.5, "turns": 2, "gap_label": "半天没来（3–12 小时）",
                     "pending_activity": act})
    trg = evaluate(ctx)
    check("宣告超时未归 → pending 命中", trg is not None and trg.kind == "pending", trg)
    check("pending 走轻闸门（needs_gate=False）", trg is not None and trg.needs_gate is False)
    check("brief 里带着具体锚点（不是万能句）", trg is not None and "游戏" in (trg.brief or ""))

    # 没到预期时长 → 不问
    ctx2 = _ctx(tmp, now=now, last_input_ts=declared,
                data={"gap_h": 0.8, "turns": 2,
                      "pending_activity": {"label": "游戏", "expected_s": 5400,
                                           "ts": now - 0.8 * 3600, "text": "x"}})
    r2 = evaluate(ctx2)
    check("没到预期时长不问", r2 is None or r2.kind != "pending", r2)

    # 问过一次 → 不再问（除非再宣告一次 = 新 key）。注意：用同一个 marks 实例（生产路径即如此）
    m = MarksStore(tmp)
    m.mark_pending("danya", trg.dedup_key, now)
    ctx_r = _ctx(tmp, now=now, last_input_ts=declared, marks=m,
                 data={"gap_h": 2.5, "turns": 2, "gap_label": "半天没来（3–12 小时）",
                       "pending_activity": act})
    r3 = evaluate(ctx_r)
    check("问过一次后不再问", r3 is None or r3.kind != "pending")

    # 宣告之后还聊了别的 → 不追（宣告离最后输入超过 15 分钟）
    ctx4 = _ctx(tmp, now=now, last_input_ts=declared - 1800,
                data={"gap_h": 3.0, "turns": 10, "pending_activity": dict(act)})
    r4 = evaluate(ctx4)
    check("宣告后又聊过天 → 不追", r4 is None or r4.kind != "pending")

    # 有宣告在身时，泛化跟进让位（用另一条宣告，避免撞上上面问过的 key）
    declared5 = declared - 7.0
    act5 = {"label": "游戏", "expected_s": 5400, "ts": declared5, "text": "我上号了"}
    ctx5 = _ctx(tmp, now=now, last_input_ts=declared5, marks=MarksStore(tmp),
                data={"gap_h": 2.7, "turns": 8, "gap_label": "半天没来",
                      "pending_activity": act5})
    r5 = evaluate(ctx5)
    check("有具体宣告时 followup 让位给 pending", r5 is not None and r5.kind == "pending", r5)

    # 开关关掉 → 不触发
    f = dict(DEFAULTS, enabled=True)
    f["pending"] = False
    ctx6 = _ctx(tmp, now=now, last_input_ts=declared, flags=f,
                data={"gap_h": 2.5, "turns": 2, "pending_activity": act})
    check("关闭 pending 开关后不触发", evaluate(ctx6) is None or evaluate(ctx6).kind != "pending")


def t_share(tmp):
    print("[11] 兴趣分享（抖音式：兴趣画像 × 真实素材，没料不硬编）")
    from src.proactive_pack import feed as feed_mod
    now = _fixed_now()          # 钉钟：避免饭点 meal 节律抢占 share（同上）
    check("无素材池 → 返回空（静默降级）", feed_mod.load_items(tmp) == [])
    (Path(tmp) / "proactive_pack_feed.json").write_text(json.dumps([
        {"id": "n1", "text": "原神 5.2 版本前瞻直播定档周五晚", "tags": ["原神", "游戏"], "ts": now - 3600},
        {"id": "n2", "text": "英雄联盟 S 赛赛程公布", "tags": ["英雄联盟"], "ts": now - 7200},
    ], ensure_ascii=False), encoding="utf-8")
    items = feed_mod.load_items(tmp)
    check("素材池可读且字段规整", len(items) == 2 and items[0]["id"] == "n1", items)
    check("兴趣命中判定（双向包含）", feed_mod.matches(items[0], ["原神"]) is True)
    check("兴趣不命中 → 不分享", feed_mod.matches(items[1], ["原神"]) is False)
    check("无兴趣画像 → 永不分享", feed_mod.matches(items[0], []) is False)

    # 命中 → share 触发
    ctx = _ctx(tmp, now=now, last_input_ts=now - 8 * 3600,
               data={"gap_h": 8.0, "turns": 0, "interest_tags": ["原神"]})
    trg = evaluate(ctx)
    check("兴趣 × 素材 × 长静默 → share 命中", trg is not None and trg.kind == "share", trg)
    check("素材内容进入 brief（不凭空编）", trg is not None and "前瞻直播" in (trg.brief or ""))
    check("挑的是最新的匹配素材", trg is not None and "n1" in trg.dedup_key, trg and trg.dedup_key)

    # 无兴趣画像 → 不开口（长静默下可能落到 silence，但绝不是 share）
    ctx2 = _ctx(tmp, now=now, last_input_ts=now - 8 * 3600,
                data={"gap_h": 8.0, "turns": 0, "interest_tags": []})
    r2 = evaluate(ctx2)
    check("没有兴趣画像就不分享", r2 is None or r2.kind != "share", r2)

    # 静默不够久 → 不开口
    ctx3 = _ctx(tmp, now=now, last_input_ts=now - 2 * 3600,
                data={"gap_h": 2.0, "turns": 0, "interest_tags": ["原神"]})
    r3 = evaluate(ctx3)
    check("静默不足 6h 不分享", r3 is None or r3.kind != "share")

    # 当日已达上限 → 不开口
    m = MarksStore(tmp)
    m.mark_share_day("danya", now)
    ctx4 = _ctx(tmp, now=now, last_input_ts=now - 8 * 3600, marks=m,
                data={"gap_h": 8.0, "turns": 0, "interest_tags": ["原神"]})
    r4 = evaluate(ctx4)
    check("当日 share_per_day=1 已用完 → 不再分享", r4 is None or r4.kind != "share")

    # 该素材已分享过 → 跳过（另一条不匹配 → 无可分享）
    m2 = MarksStore(tmp)
    m2.mark_share("danya", "n1", now)
    ctx5 = _ctx(tmp, now=now, last_input_ts=now - 8 * 3600, marks=m2,
                data={"gap_h": 8.0, "turns": 0, "interest_tags": ["原神"]})
    r5 = evaluate(ctx5)
    check("分享过的素材不再分享", r5 is None or r5.kind != "share")


def t_feed_sources(tmp):
    print("[13] 热榜素材源（借鉴 N.E.K.O：拉真实热点喂 share；全桩 HTTP，不碰真网）")
    from src.proactive_pack import feed as feed_mod
    from src.proactive_pack import feed_sources as fs
    now = time.time()
    check("热榜开关在默认表里",
          all(k in DEFAULTS for k in ("feed_hot", "feed_ttl_h", "feed_max_items")))

    # 桩：按 URL 路由假响应（含涉政词 / 灾难词 / 过短词，验证过滤）
    def fake_http(url, timeout=6.0):
        if "bilibili" in url:
            return {"data": {"trending": {"list": [
                {"keyword": "原神 5.3 前瞻直播定档"},
                {"keyword": "某明星官宣恋情"},
                {"keyword": "台湾局势"},
                {"keyword": "坠机事故调查"},
                {"keyword": "英雄联盟 S15 赛程"},
                {"keyword": "短"},          # 过短 → 滤
            ]}}}
        raise RuntimeError("network down")   # 第二个源失败 → 单源降级
    orig_http = fs._http_get_json
    fs._http_get_json = fake_http
    try:
        items = fs.fetch_source(fs.SOURCES[0], now)
        check("单源拉取 + 内容过滤（涉政/灾难/八卦/过短）",
              len(items) == 2 and items[0]["text"].startswith("原神"), items)
        check("条目带 src=hot 标记", all(it.get("src") == "hot" for it in items))
        check("id 稳定（同名去重可用）",
              items[0]["id"].startswith("hot:bilibili:") and len(items[0]["id"]) > 15)

        # 手工条目先落盘；refresh 后必须原样保留
        (Path(tmp) / "proactive_pack_feed.json").write_text(json.dumps([
            {"id": "manual1", "text": "手工投喂的一条", "tags": ["手工"], "ts": now - 99},
        ], ensure_ascii=False), encoding="utf-8")
        rep = fs.refresh_if_stale(tmp, force=True, now=now)
        check("全源刷新成功", rep.get("ok") is True, rep)
        merged = feed_mod.load_items(tmp)
        check("手工条目不被热榜覆盖", any(it["id"] == "manual1" for it in merged), merged)
        check("hot 段已并入", sum(1 for it in merged if it.get("src") == "hot") == 2)
        check("load_items 透传 src", all("src" in it for it in merged))

        # TTL 内不重复拉（mtime 刚更新 + 已有 hot 段）
        rep2 = fs.refresh_if_stale(tmp, ttl_h=4.0, now=now + 60)
        check("TTL 内刷新跳过", rep2.get("skipped") is True, rep2)

        # 上限：手工优先，hot 截断
        rep3 = fs.refresh_if_stale(tmp, force=True, max_items=2, now=now)
        merged3 = feed_mod.load_items(tmp)
        check("上限截断时手工条目保留",
              any(it["id"] == "manual1" for it in merged3) and len(merged3) <= 2, merged3)

        # 全源失败：旧素材原样保留（拉不到新的就继续用旧的，绝不硬编占位）
        def dead_http(url, timeout=6.0):
            raise RuntimeError("network down")
        fs._http_get_json = dead_http
        before = feed_mod.load_items(tmp)
        rep4 = fs.refresh_if_stale(tmp, force=True, now=now)
        check("全源失败 → ok=False 且不抛", rep4.get("ok") is False, rep4)
        check("全源失败 → 旧素材原样保留", feed_mod.load_items(tmp) == before)
    finally:
        fs._http_get_json = orig_http


def t_prompts():
    print("[7] 产出提示词")
    trg = triggers.Trigger(kind="silence", priority=50, brief="用户离开了挺久")
    p = prompts.build_prompt(trg, "现在：晚上 21:00", "达妮娅")
    check("含产出要求", "10~30 字" in p)
    check("明令禁止无锚点万能句", "在忙吗" in p and "禁止" in p)
    check("含反 PUA 约束", "绝不施压" in p or "不催" in p)
    check("含情境材料", "现在：晚上 21:00" in p)
    pf = prompts.build_prompt(triggers.Trigger(kind="festival", priority=10, brief="今天中秋"),
                              "", "达妮娅")
    check("节日附加规则生效", "干净的祝福" in pf)
    check("情境为空时不崩", isinstance(pf, str) and len(pf) > 10)
    # 节日提示词**不能**要求"必须带锚点"（那会逼模型编旧事 → 连续驳回 → 节日不开口）；
    # 且要显式禁止回指往事。
    check("节日提示词不含「必须带一个具体锚点」", "必须带一个具体锚点" not in pf)
    check("节日提示词显式禁止回指往事", "别回指任何往事" in pf)
    # 「第三人称自指」生成端禁令：三条产出分支（有材料 / 无材料 / 节日）都必须带上。
    _pn = prompts.build_prompt(trg, "", "达妮娅")
    check("生成端禁令：别把名字当旁白（无材料分支）", "只用「我」" in _pn and "达妮娅记得你" in _pn)
    check("生成端禁令：别把名字当旁白（节日分支）", "只用「我」" in pf)
    check("生成端禁令：别把名字当旁白（有材料分支）",
          "只用「我」" in prompts.build_prompt(
              trg, "最近几轮对话：\n用户：在忙", "达妮娅"))


def t_pack(tmp):
    print("[8] 门面 maybe_emit（端到端，全桩）")
    host = FakeHost(reply="回来啦？昨晚那局打得怎么样")
    pack = build_proactive_pack(host, base_dir=tmp)
    pack._relation_gate = lambda cid, now: (True, "test")
    pack._light_gate = lambda now: (True, "test")

    now = time.time()
    host._last_input_ts = now - 6 * 3600

    # 关 → 不动作
    check("总开关为关时不动作", asyncio.run(pack.maybe_emit(now=now)) is False)

    # 开 → 打桩：直接用节日触发器
    write_state_flags({"enabled": True}, tmp)
    pack = build_proactive_pack(host, base_dir=tmp)
    pack._relation_gate = lambda cid, now: (True, "test")
    pack._light_gate = lambda now: (True, "test")
    orig = festivals.festival_today
    try:
        festivals.festival_today = lambda now=None: {"name": "测试节", "kind": "solar"}
        # 让 collect 返回确定性数据（隔离外部依赖）
        import src.proactive_pack.pack as pack_mod
        orig_collect = pack_mod.collect
        pack_mod.collect = lambda *a, **kw: Situation(
            text="现在：晚上 21:00\n距上次互动：半天没来（3–12 小时）",
            blocks={"now": "x", "gap": "y"},
            data={"gap_h": 6.0, "turns": 8, "gap_label": "半天没来（3–12 小时）"})
        try:
            ok1 = asyncio.run(pack.maybe_emit(now=now))
            check("启用后成功开口", ok1 is True)
            check("派发到聊天页（to_chat=True）",
                  bool(host.emitted) and host.emitted[0]["to_chat"] is True, host.emitted)
            check("文本原样送出", host.emitted and host.emitted[0]["text"] == "回来啦？昨晚那局打得怎么样")
            check("账本 +1", pack.marks.ledger_count(now) == 1, pack.marks.ledger_count(now))
            check("种类记录正确", pack.status()["last_kind"] == "festival")
            ok2 = asyncio.run(pack.maybe_emit(now=now))
            check("同一天同一节日不重复发", ok2 is False)
        finally:
            pack_mod.collect = orig_collect
    finally:
        festivals.festival_today = orig

    # 万能句被丢弃（走 followup 档——短时机下「在忙吗」无锚点，必须杀）
    host2 = FakeHost(reply="在忙吗")
    host2._last_input_ts = time.time() - 2 * 3600
    pack2 = build_proactive_pack(host2, base_dir=tmp)
    pack2._relation_gate = lambda cid, now: (True, "test")
    pack2._light_gate = lambda now: (True, "test")
    write_state_flags({"enabled": False}, tmp)          # 复位，避免影响后续
    write_state_flags({"enabled": True, "festival": False}, tmp)
    pack2 = build_proactive_pack(host2, base_dir=tmp)
    pack2._relation_gate = lambda cid, now: (True, "test")
    pack2._light_gate = lambda now: (True, "test")
    try:
        festivals.festival_today = lambda now=None: None
        import src.proactive_pack.pack as pack_mod2
        orig_collect2 = pack_mod2.collect
        pack_mod2.collect = lambda *a, **kw: Situation(
            text="", blocks={}, data={"gap_h": 2.0, "turns": 8, "gap_label": "短暂离开（1–3 小时）"})
        try:
            check("无锚点万能句被丢弃（followup 档）", asyncio.run(pack2.maybe_emit(now=time.time())) is False)
            check("丢弃时不派发", not host2.emitted)
        finally:
            pack_mod2.collect = orig_collect2
    finally:
        festivals.festival_today = orig

    # 但同样的「在忙吗」在长静默档是**正当的**（开发者明确要求），必须放行
    host3 = FakeHost(reply="这几天在忙什么呢")
    host3._last_input_ts = time.time() - 30 * 3600
    write_state_flags({"enabled": True, "festival": False}, tmp)
    pack3 = build_proactive_pack(host3, base_dir=tmp)
    pack3._relation_gate = lambda cid, now: (True, "test")
    pack3._light_gate = lambda now: (True, "test")
    try:
        festivals.festival_today = lambda now=None: None
        import src.proactive_pack.pack as pack_mod3
        orig_collect3 = pack_mod3.collect
        pack_mod3.collect = lambda *a, **kw: Situation(
            text="", blocks={}, data={"gap_h": 30.0, "turns": 0, "gap_label": "好几天没来（超过 36 小时）"})
        try:
            check("长静默档问「在忙什么」放行（silence 豁免）",
                  asyncio.run(pack3.maybe_emit(now=time.time())) is True)
            check("豁免句成功派发", bool(host3.emitted) and "在忙" in host3.emitted[0]["text"])
        finally:
            pack_mod3.collect = orig_collect3
    finally:
        festivals.festival_today = orig
    check("_is_banned 判据（短句命中黑名单）", pack2._is_banned("在忙吗") is True)
    check("_is_banned 不误杀带锚点的长句",
          pack2._is_banned("记得喝水，你昨天说嗓子疼还没好") is False)
    check("silence 档豁免黑名单（久别问「在忙吗」是正当的）",
          pack2._is_banned("在忙吗", "silence") is False)


def t_llm_gate(tmp):
    print("[12] LLM 减法闸（发出前判 SEND/SKIP；借鉴 thunlp/ProactiveAgent）")
    import src.proactive_pack.pack as pack_mod
    orig = festivals.festival_today
    try:
        festivals.festival_today = lambda now=None: None
        stub = lambda *a, **kw: Situation(
            text="", blocks={}, data={"gap_h": 2.0, "turns": 8, "gap_label": "短暂离开（1–3 小时）"})
        orig_collect = pack_mod.collect

        host = FakeHost()
        host._llm = _GateLLM(["今晚早点睡吧", "SKIP"])
        host._last_input_ts = time.time() - 2 * 3600
        write_state_flags({"enabled": True, "festival": False}, tmp)
        pack = build_proactive_pack(host, base_dir=tmp)
        pack._relation_gate = lambda cid, now: (True, "t")
        pack._light_gate = lambda now: (True, "t")
        pack_mod.collect = stub
        try:
            check("LLM 判 SKIP → 不开口", asyncio.run(pack.maybe_emit(now=time.time())) is False)
            check("SKIP 时不派发", not host.emitted)
        finally:
            pack_mod.collect = orig_collect

        host2 = FakeHost()
        host2._llm = _FlakyLLM("今晚早点睡吧")     # 注意：first 是字符串，不是列表
        host2._last_input_ts = time.time() - 2 * 3600
        pack2 = build_proactive_pack(host2, base_dir=tmp)
        pack2._relation_gate = lambda cid, now: (True, "t")
        pack2._light_gate = lambda now: (True, "t")
        pack_mod.collect = stub
        try:
            check("闸门故障 fail-open 照常开口",
                  asyncio.run(pack2.maybe_emit(now=time.time())) is True)
            check("fail-open 派发成功", bool(host2.emitted))
        finally:
            pack_mod.collect = orig_collect

        host3 = FakeHost()
        host3._llm = _GateLLM(["今晚早点睡吧", "SKIP"])
        host3._last_input_ts = time.time() - 2 * 3600
        # 独立账本目录：上一步 fail-open 已写入跟进冷却（12h），不能污染本用例
        tmp3 = tempfile.mkdtemp(prefix="ppack_gate3_")
        write_state_flags({"enabled": True, "festival": False, "llm_gate": False}, tmp3)
        pack3 = build_proactive_pack(host3, base_dir=tmp3)
        pack3._relation_gate = lambda cid, now: (True, "t")
        pack3._light_gate = lambda now: (True, "t")
        pack_mod.collect = stub
        try:
            check("llm_gate 关时跳过判断直接发",
                  asyncio.run(pack3.maybe_emit(now=time.time())) is True)
            check("开关关闭时闸门未消耗判定调用", host3._llm.calls == 1)
        finally:
            pack_mod.collect = orig_collect
            shutil.rmtree(tmp3, ignore_errors=True)

        # 代码闸新增（2026-10-12 真机 bug）：第三人称自指 —— 她自己说话时句中出现她自己的名字。
        # 为什么放代码闸而非只靠 LLM：定向实测同义变体「达妮娅觉得…」时判时不判；
        # 这是"症状"不是"语义"，用硬形状检查才稳（LLM 端 R7 仍保留兜底）。
        pack._name_override = "达妮娅"
        _sit_nm = "最近几轮对话：\n用户：今天闲着没事干"
        cat, _why = pack._code_reject("嘿，达妮娅记得你之前喜欢科幻", "share", _sit_nm)
        check("代码闸：自称名字 → 拒[robotic]", cat == "robotic", cat)
        cat4, _ = pack._code_reject("达妮娅觉得你今天该早点休息", "rhythm", "")
        check("代码闸：空材料变体也拦（X觉得）", cat4 == "robotic", cat4)
        cat2, _ = pack._code_reject("我还记得你喜欢科幻", "share", _sit_nm)
        check("代码闸：第一人称「我」不误杀", cat2 != "robotic", cat2)
        cat3, _ = pack._code_reject("你说「达妮娅」这名字好听", "share", _sit_nm)
        check("代码闸：引号内出现名字不误杀", cat3 != "robotic", cat3)
    finally:
        festivals.festival_today = orig
    write_state_flags({"enabled": False}, tmp)          # 复位


def t_install(tmp):
    print("[9] 接线 / 摘除（实例包装，零文件改动）")
    host = FakeHost()
    write_state_flags({"enabled": True}, tmp)
    pack = install(host, base_dir=tmp)
    check("install 返回包实例", isinstance(pack, ProactivePack))
    check("包装后仍是可调用的 async", callable(host.check_and_trigger))
    check("_proactive_pack 已挂上", getattr(host, "_proactive_pack", None) is pack)
    # 幂等
    again = install(host, base_dir=tmp)
    check("重复 install 幂等", again is pack)
    # 关掉开关 → 包装层放行原方法
    write_state_flags({"enabled": False}, tmp)
    pack.reload_flags()
    asyncio.run(host.check_and_trigger())
    check("开关关时原 check_and_trigger 照常执行", getattr(host, "orig_called", 0) == 1)
    # 摘除
    check("uninstall 成功", uninstall(host) is True)
    check("摘除后 _proactive_pack 为空", getattr(host, "_proactive_pack", None) is None)


def t_judge(tmp):
    """[13] 生成 + LLM 审查闭环（一个想怎么说 / 一个判能不能说；驳回则回灌重写）。"""
    print("[13] 生成→代码闸→LLM 审查→回灌重写（judge_attempts 上限）")
    import src.proactive_pack.pack as pack_mod
    from src.proactive_pack import judge as judge_mod

    orig = festivals.festival_today
    orig_collect = pack_mod.collect
    # 有真实材料的情境（否则代码闸就会先拦"回指往事"，测不到 LLM 审查这一段）
    stub = lambda *a, **kw: Situation(
        text="最近几轮对话：\n用户：我在做那个小项目\n现在：晚上 21:00",
        blocks={"topic": "x"}, data={"gap_h": 2.0, "turns": 8, "gap_label": "短暂离开（1–3 小时）"})
    try:
        festivals.festival_today = lambda now=None: None
        pack_mod.collect = stub

        # ---- ① 第 1 句被审查驳回 → 回灌理由 → 第 2 句通过 ----
        # 注意：用**独立沙盒**——同目录下前面的用例已把「会话内跟进」当日额度/冷却用掉，
        #       复用会让 trigger 根本不命中（本用例测不到生成/审查链路）。
        tmp1 = tempfile.mkdtemp(prefix="ppack_judge1_")
        try:
            host = FakeHost()
            host._llm = _ScriptLLM([
                "晚饭吃了没，对了你上次说的那个项目怎么样了",          # 生成①（一句话两件事）
                "判定: FAIL\n类别: multitopic\n原因: 两件事硬拼一句\n改写: 只说项目那一件",  # 审查①
                "你上次说的那个项目，后来顺利吗？",                    # 生成②（重写后）
                "判定: PASS",                                          # 审查②
            ])
            host._last_input_ts = time.time() - 2 * 3600
            write_state_flags({"enabled": True, "festival": False}, tmp1)
            pack = build_proactive_pack(host, base_dir=tmp1)
            pack._relation_gate = lambda cid, now: (True, "t")
            pack._light_gate = lambda now: (True, "t")
            ok = asyncio.run(pack.maybe_emit(now=time.time()))
            check("审查驳回后可回灌重写并通过", ok is True)
            check("最终发出的是重写后的句子",
                  bool(host.emitted) and host.emitted[0]["text"] == "你上次说的那个项目，后来顺利吗？",
                  host.emitted)
            check("共 4 次 LLM 调用（生成/审查 ×2）", host._llm.calls == 4, host._llm.calls)
            check("重写时把驳回理由回灌给了生成器",
                  len(host._llm.prompts) >= 3 and "驳回归类" in host._llm.prompts[2]
                  and "multitopic" in host._llm.prompts[2])
            check("驳回记录留痕（类别=multitopic）",
                  bool(pack._last_rejects) and pack._last_rejects[0]["category"] == "multitopic",
                  pack._last_rejects)
        finally:
            shutil.rmtree(tmp1, ignore_errors=True)

        # ---- ② 每次都驳回 → 用尽次数 → 放弃开口 ----
        tmp2 = tempfile.mkdtemp(prefix="ppack_judge2_")
        try:
            host2 = FakeHost()
            host2._llm = _ScriptLLM([
                "第一句候选话", "判定: FAIL\n类别: bland\n原因: 没有信息量",
                "第二句候选话", "判定: FAIL\n类别: bland\n原因: 还是没有信息量",
                "第三句候选话", "判定: FAIL\n类别: bland\n原因: 依旧没有信息量",
            ])
            host2._last_input_ts = time.time() - 2 * 3600
            write_state_flags({"enabled": True, "festival": False, "judge_attempts": 3}, tmp2)
            pack2 = build_proactive_pack(host2, base_dir=tmp2)
            pack2._relation_gate = lambda cid, now: (True, "t")
            pack2._light_gate = lambda now: (True, "t")
            ok2 = asyncio.run(pack2.maybe_emit(now=time.time()))
            check("审查连续驳回 → 用尽上限后放弃开口", ok2 is False)
            check("放弃时不派发", not host2.emitted)
            check("尝试次数=judge_attempts(3)", host2._llm.calls == 6, host2._llm.calls)
            check("三次驳回全部留痕", len(pack2._last_rejects) == 3, pack2._last_rejects)
            check("账本未 +1（没开口）", pack2.marks.ledger_count(time.time()) == 0)
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        # ---- ③ 审查输出无法解析 → fail-open 放行（不因闸门坏掉闭麦） ----
        tmp3 = tempfile.mkdtemp(prefix="ppack_judge3_")
        try:
            host3 = FakeHost()
            host3._llm = _ScriptLLM(["今晚这个点还没睡啊", "嗯……我觉得大概也许可以吧"])
            host3._last_input_ts = time.time() - 2 * 3600
            write_state_flags({"enabled": True, "festival": False, "judge_attempts": 3}, tmp3)
            pack3 = build_proactive_pack(host3, base_dir=tmp3)
            pack3._relation_gate = lambda cid, now: (True, "t")
            pack3._light_gate = lambda now: (True, "t")
            check("审查输出不可解析 → fail-open 放行",
                  asyncio.run(pack3.maybe_emit(now=time.time())) is True)
            check("fail-open 确实派发", bool(host3.emitted))
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)

        # ---- ④ 回灌文案覆盖各驳回类别 ----
        fb = judge_mod.build_retry_feedback("fabrication", "材料里没有这家店", "别提店名")
        check("回灌文案含类别与原因", "fabrication" in fb and "材料里没有这家店" in fb)
        check("回灌文案带硬性修正", "硬性修正" in fb)

        # ---- ⑤ 解析器：结论行被截断时回退扫逐条核查行（弱模型实测会截断） ----
        v_r = judge_mod.parse_verdict("R1 编造: 命中（材料里没有咖啡店）\nR2 自曝身体: 未命中")
        check("结论行缺失 → 回退扫 R 行命中", v_r.ok is False and v_r.category == "fabrication", v_r)
        all_no = "\n".join("R%d x: 未命中" % i for i in range(1, 11))
        v_all = judge_mod.parse_verdict(all_no)
        check("十条全未命中 → 放行（走核查行兜底，而非 fail-open 兜底）",
              v_all.ok is True and v_all.category == "llm_pass_checklist", v_all)
        check("「未命中」不被误判为命中", judge_mod.parse_verdict("R2 自曝身体: 未命中").ok is True)
        # R10 自然度：序号两位数，曾被 `(\d)` 正则误读成 R1 → 类别错判成 fabrication。
        v_r10 = judge_mod.parse_verdict("R10 生硬别扭: 命中（硬凑牛排和猫）")
        check("R10 命中 → FAIL[awkward]（两位序号不误读成 R1）",
              v_r10.ok is False and v_r10.category == "awkward", v_r10)
        check("awkward 回灌文案齐备（HINTS）", "awkward" in judge_mod.HINTS)
        # 提示词层面：别哪天被改回 9 条 / 丢掉空材料放行口径
        _rp = judge_mod.build_review_prompt("x", "材料", "b")
        check("审查提示词含 R10 判据", "R10" in _rp and "10 条" in _rp)
        check("审查提示词含「空材料轻度关心放行」口径",
              "轻度关心" in judge_mod.build_review_prompt("x", "", "b"))
        # 节日场景才加 R11（祝福要落到当天这个节日），非节日不加，避免无谓条目。
        check("节日场景审查提示词含 R11（节日张冠李戴）",
              "R11" in judge_mod.build_review_prompt("国庆快乐", "", "今天国庆节", kind="festival"))
        check("非节日场景不含 R11",
              "R11" not in judge_mod.build_review_prompt("x", "", "b", kind="silence"))
        check("R11 → off_festival 类别（解析器）",
              judge_mod._R_CATEGORY.get("11") == "off_festival")
        # 「第三人称自指」（她把自己的名字当旁白念出来）——实测真机 bug，归 R7(robotic)。
        # 教训：软判据光写条目没用，必须给"命中"范例；这里同时锁住判据行与范例。
        check("审查提示词含「自称名字」判据（R7 第三人称）",
              "第三人称" in _rp and "只自称「我」" in _rp)
        check("审查提示词含「自称名字」命中范例",
              "达妮娅记得你" in _rp)
        check("R7 命中 → FAIL[robotic]（自称名字当旁白）",
              judge_mod.parse_verdict("R7 客服腔 / 自称名字: 命中（把自己的名字当旁白）").category
              == "robotic")
        check("robotic 回灌文案含「别用自己的名字」",
              "名字" in judge_mod.HINTS.get("robotic", ""))
        check("审查拿不到 llm → 放行（fail-open）", judge_mod.parse_verdict("").ok is True)

        # ---- ⑥ 审查器换独立模型（judge_provider 指向 config.yaml 端口） ----
        import types as _types
        ns = _types.SimpleNamespace(text_chat=_types.SimpleNamespace(providers=[
            _types.SimpleNamespace(id="stronger", base_url="https://x/v1",
                                   api_key="k", model="big-model"),
        ]))
        tmp4 = tempfile.mkdtemp(prefix="ppack_judge4_")
        try:
            host4 = FakeHost()
            host4._llm = _ScriptLLM(["候选句"])
            write_state_flags({"enabled": True, "festival": False,
                               "judge_provider": "stronger", "judge_model": ""}, tmp4)
            p4 = build_proactive_pack(host4, cfg_ns=ns, base_dir=tmp4)
            rc = p4._reviewer_client()
            check("judge_provider 生效 → 审查器换独立模型（judge_model 留空 → 用端口自带模型）",
                  isinstance(rc, judge_mod.MiniOpenAIClient) and rc._model == "big-model", rc)
            # judge_model 显式设置 → 覆盖端口自带模型（默认 glm-4.5-flash 正是靠这条生效；
            # zai-glm 端口自带是 glm-4-flash，做审查只有 3/10，必须被覆盖）
            write_state_flags({"judge_model": "glm-4.5-flash"}, tmp4)
            p4.reload_flags(cfg_ns=ns)
            rc_m = p4._reviewer_client()
            check("judge_model 覆盖端口自带模型",
                  isinstance(rc_m, judge_mod.MiniOpenAIClient) and rc_m._model == "glm-4.5-flash", rc_m)
            write_state_flags({"judge_provider": "nope"}, tmp4)
            p4.reload_flags(cfg_ns=ns)
            check("端口不存在时回退生成端", p4._reviewer_client() is host4._llm)
            write_state_flags({"judge_provider": ""}, tmp4)
            p4.reload_flags(cfg_ns=ns)
            check("留空时与生成端同源", p4._reviewer_client() is host4._llm)
            p5 = build_proactive_pack(host4, base_dir=tmp4, judge_llm="X")
            check("显式 judge_llm 注入优先", p5._reviewer_client() == "X")

            # judge_thinking 透传（推理模型必须能关思考，否则审查静默失效）
            from src.proactive_pack.triggers import Trigger as _T
            write_state_flags({"judge_thinking": False, "judge_provider": ""}, tmp4)
            host6 = FakeHost()
            host6._llm = _ScriptLLM(["判定: PASS"])
            p6 = build_proactive_pack(host6, base_dir=tmp4)
            asyncio.run(p6._llm_review(_T(kind="silence", priority=1, brief="b"), "候选句", "材料"))
            check("judge_thinking=false 透传给审查器",
                  bool(host6._llm.options) and host6._llm.options[-1].get("enable_thinking") is False,
                  host6._llm.options)
            write_state_flags({"judge_thinking": ""}, tmp4)
            host7 = FakeHost()
            host7._llm = _ScriptLLM(["判定: PASS"])
            p7 = build_proactive_pack(host7, base_dir=tmp4)
            asyncio.run(p7._llm_review(_T(kind="silence", priority=1, brief="b"), "候选句", "材料"))
            check("judge_thinking 留空 → 不传该字段（用厂商默认）",
                  "enable_thinking" not in (host7._llm.options[-1] if host7._llm.options else {}))
            check("审查回复不可解析 → 计入 judge_unavailable 并非静默",
                  p7._judge_unavailable == 0)
            host8 = FakeHost()
            host8._llm = _ScriptLLM(["嗯……大概也许可以吧"])
            p8 = build_proactive_pack(host8, base_dir=tmp4)
            asyncio.run(p8._llm_review(_T(kind="silence", priority=1, brief="b"), "候选句", "材料"))
            check("审查输出无法解析 → judge_unavailable +1", p8._judge_unavailable == 1,
                  p8._judge_unavailable)
        finally:
            shutil.rmtree(tmp4, ignore_errors=True)
    finally:
        festivals.festival_today = orig
        pack_mod.collect = orig_collect
        write_state_flags({"enabled": False}, tmp)          # 复位


def t_isolation_longterm(tmp):
    """[14] 角色隔离 + 长期上下文（本次两项核心诉求的确定性证据）。"""
    print("[14] 角色隔离（记忆/历史不串味）+ 长期上下文块")
    import threading

    # ---- ① 记忆读数按角色过滤（全局 _character 故意留空＝未隔离，本包须自己兜住） ----
    class _Store:
        def __init__(self, mems):
            self._memories = mems
            self._lock = threading.RLock()
        _character = ""

    store = _Store([
        {"text": "用户喜欢摄影", "type": "user", "character": "danya", "invalid_at": 0, "updated_at": 3.0},
        {"text": "用户喜欢滑雪", "type": "user", "character": "other", "invalid_at": 0, "updated_at": 2.0},
        {"text": "用户喜欢喝茶", "type": "user", "character": "", "invalid_at": 0, "updated_at": 1.0},
        {"text": "用户搬过家", "type": "user", "character": "other", "invalid_at": 5.0, "updated_at": 9.0},
    ])
    got = {str(m.get("text")) for m in situation._scoped_memories(store, "danya", types=("user",))}
    check("作用域读只取「本角色 + 通用」", got == {"用户喜欢摄影", "用户喜欢喝茶"}, got)
    got_o = {str(m.get("text")) for m in situation._scoped_memories(store, "other", types=("user",))}
    check("另一角色读不到别人的记忆", got_o == {"用户喜欢滑雪", "用户喜欢喝茶"}, got_o)
    check("invalid_at 的记忆被排除", "用户搬过家" not in got_o, got_o)

    # ---- ② 长期历史按角色隔离 + 出现「更早之前聊过的事」 ----
    def _rows(seed, total=12):
        out = [{"role": "user", "content": seed, "ts": 1000.0}]
        out += [{"role": "user", "content": "旧话%d" % i, "ts": 1000.0 + i} for i in range(1, total)]
        return out

    table = {"danya": _rows("我最近在学摄影"), "other": _rows("我最近在滑雪")}
    orig = situation._longterm_history_rows
    situation._longterm_history_rows = lambda cid, base=None: table.get(cid, [])
    try:
        blk, data = situation._longterm_block("danya", now=1000.0 + 30 * 86400)
        check("长期块出现「更早之前聊过的事」", "更早之前聊过的事" in blk, blk[:60])
        check("长期块带总轮次与跨度", data.get("history_turns") == 12 and "history_first_days" in data, data)
        check("长期块只含本角色的话", "摄影" in blk and "滑雪" not in blk, blk[-90:])
        blk2, _ = situation._longterm_block("other", now=2000.0)
        check("另一角色只看到自己的长期历史", "滑雪" in blk2 and "摄影" not in blk2)
        check("有长期材料 → 判为有可锚材料", situation.has_grounding_material(blk) is True)
        check("只有时间信息 → 不算有材料", situation.has_grounding_material("现在：晚上 21:00") is False)
    finally:
        situation._longterm_history_rows = orig

    # ---- ③ 兴趣画像也按角色隔离（记忆库 + 长期历史都不串） ----
    orig_gms = None
    try:
        import src.companion.memory_store as ms_mod
        orig_gms = ms_mod.get_memory_store
        ms_mod.get_memory_store = lambda: store
        situation._longterm_history_rows = lambda cid, base=None: table.get(cid, [])
        tags_a = situation.extract_interest_tags("danya")
        tags_b = situation.extract_interest_tags("other")
        check("A 的兴趣画像不含 B 的喜好", "滑雪" not in tags_a, tags_a)
        check("B 的兴趣画像不含 A 的喜好", "摄影" not in tags_b, tags_b)
    except Exception as e:  # noqa: BLE001
        check("兴趣画像隔离检查可运行", False, e)
    finally:
        situation._longterm_history_rows = orig
        if orig_gms is not None:
            import src.companion.memory_store as ms_mod
            ms_mod.get_memory_store = orig_gms


def t_engine_adapter(tmp):
    """[15] 模型引擎适配：llm/vlm 档既发言又审查；同模型；不干涉用户聊天。"""
    print("[15] 模型引擎适配（host.engine_llm 档；同模型既发言又审查；不干涉用户聊天）")
    import src.proactive_pack.pack as pack_mod
    # 独立账本目录：前面的用例已把共享 tmp 的 day_cap 用满，不能污染本用例。
    tmp15 = tempfile.mkdtemp(prefix="ppack_eng_")
    orig = festivals.festival_today
    try:
        festivals.festival_today = lambda now=None: None
        stub = lambda *a, **kw: Situation(
            text="", blocks={}, data={"gap_h": 2.0, "turns": 8, "gap_label": "短暂离开（1–3 小时）"})
        orig_collect = pack_mod.collect

        # 宿主把引擎 llm/vlm 档挂到 host.engine_llm（独立客户端，与聊天路由无关）。
        # 生产环境宿主把 Comni/llama.cpp-omni 网关的 /api/chat 档包成聊天客户端挂这里
        # （见 engine_llm.EngineChatClient；也可只放引擎对象，由本包自动发现端点）。
        # 第 1 次 chat=生成台词；第 2 次 chat=审查判定（PASS）→ 放行开口。
        engine_llm = _ScriptLLM(["坐太久啦，起来伸展一下对腰好", "判定: PASS"])
        host = FakeHost()
        host._llm = FakeLLM("兜底（生产不应被用到）")   # 用户聊天路由兜底（须保持不被用）
        host.engine_llm = engine_llm
        host._recent_messages = []                        # 宿主近期消息（包会记下自己这句主动话，属正常）
        host._last_input_ts = time.time() - 2 * 3600
        write_state_flags({"enabled": True, "festival": False, "judge_use_engine": True}, tmp15)
        pack = build_proactive_pack(host, base_dir=tmp15)
        pack._relation_gate = lambda cid, now: (True, "t")
        pack._light_gate = lambda now: (True, "t")
        pack_mod.collect = stub
        try:
            # ① 审查器解析到引擎档，而非独立 judge_provider / 聊天路由
            check("judge_use_engine → 审查器吃 host.engine_llm",
                  pack._reviewer_client() is engine_llm, pack._reviewer_client())
            # ② 生成端也吃引擎档（同一个模型走引擎）
            check("生成端也吃 host.engine_llm（同模型走引擎）",
                  pack._llm_client() is engine_llm, pack._llm_client())
            # ③ 真跑一轮：生成+审查都命中引擎档
            ok = asyncio.run(pack.maybe_emit(now=time.time()))
            check("引擎档一轮照常开口", ok is True and bool(host.emitted), host.emitted)
            check("生成调用走引擎档（≥1）", engine_llm.calls >= 1, engine_llm.calls)
            check("审查调用也走引擎档（生成+审查共 ≥2）", engine_llm.calls >= 2, engine_llm.calls)
            # ④ 不干涉用户聊天：主动包的调用全走引擎档，用户聊天路由一次都没被用
            check("不干涉用户聊天（host._llm 聊天路由零调用）",
                  host._llm.calls == 0, host._llm.calls)
            check("发出的是引擎档生成的台词",
                  host.emitted and "伸展" in host.emitted[0]["text"], host.emitted)
            # ④ status 如实上报引擎接入
            st = pack.status()
            check("status: engine_llm_connected=true", st["engine_llm_connected"] is True, st)
            check("status: judge_use_engine=true", st["judge_use_engine"] is True, st)
        finally:
            pack_mod.collect = orig_collect

        # ⑤ 无 engine_llm 时回落 host._llm（不静默坏掉；dev 默认路径）
        host2 = FakeHost()
        host2._last_input_ts = time.time() - 2 * 3600
        write_state_flags({"enabled": True, "festival": False, "judge_use_engine": True}, tmp15)
        pack2 = build_proactive_pack(host2, base_dir=tmp15)
        pack2._relation_gate = lambda cid, now: (True, "t")
        pack2._light_gate = lambda now: (True, "t")
        pack_mod.collect = stub
        try:
            check("无 engine_llm 时回落 host._llm",
                  pack2._reviewer_client() is host2._llm, pack2._reviewer_client())
            check("status: engine_llm_connected=false（回落聊天路由）",
                  pack2.status()["engine_llm_connected"] is False, pack2.status())
        finally:
            pack_mod.collect = orig_collect

        # ⑥ 自动发现：宿主只放「引擎对象」（没挂 engine_llm 客户端）也能解析出端点
        from src.proactive_pack import engine_llm as engine_llm_mod

        class _Eng:
            endpoint = "https://localhost:8006"

            def is_model_loaded(self):
                return True

        host3 = FakeHost()
        host3._local_omni_engine = _Eng()
        host3._last_input_ts = time.time() - 2 * 3600
        write_state_flags({"enabled": True, "festival": False, "judge_use_engine": True}, tmp15)
        pack3 = build_proactive_pack(host3, base_dir=tmp15)
        _rc3 = pack3._reviewer_client()
        check("自动发现：宿主引擎对象 → 解析出引擎档客户端",
              isinstance(_rc3, engine_llm_mod.EngineChatClient), _rc3)
        check("自动发现端点正确",
              getattr(_rc3, "endpoint", "") == "https://localhost:8006", _rc3)
        check("status: engine_llm_endpoint 如实上报",
              pack3.status().get("engine_llm_endpoint") == "https://localhost:8006", pack3.status())

        # ⑦ 缺省关（judge_use_engine=false）→ 宿主即使挂了引擎档，生成也走聊天路由（不静默切引擎）
        host4 = FakeHost()
        host4.engine_llm = _ScriptLLM(["x"])
        host4._last_input_ts = time.time() - 2 * 3600
        write_state_flags({"enabled": True, "festival": False, "judge_use_engine": False}, tmp15)
        pack4 = build_proactive_pack(host4, base_dir=tmp15)
        check("缺省关：生成不走引擎（回落聊天路由）",
              pack4._llm_client() is host4._llm, pack4._llm_client())
    finally:
        festivals.festival_today = orig
        shutil.rmtree(tmp15, ignore_errors=True)
    write_state_flags({"enabled": False}, tmp)


def t_engine_client(tmp):
    """[16] 引擎档客户端：Comni `/api/chat` 协议 + 失败不静默 + 自动发现边界。"""
    print("[16] 引擎档客户端（Comni /api/chat schema）与自动发现")
    from src.proactive_pack import engine_llm as el

    captured: dict = {}

    class _Resp:
        def __init__(self, b: bytes):
            self._b = b

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _OkOpener:
        def open(self, req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _Resp(json.dumps({"text": "你好呀", "success": True}).encode("utf-8"))

    cli = el.EngineChatClient(base_url="https://localhost:8006/", max_tokens=128)
    cli._opener = lambda: _OkOpener()          # 绕过真实网络
    out = asyncio.run(cli.chat([{"role": "user", "content": "在吗"}], {"temperature": 0.8}))
    check("引擎客户端返回 text", out == "你好呀", out)
    check("请求打到引擎 /api/chat",
          str(captured.get("url", "")).endswith("/api/chat"), captured.get("url"))
    b = captured.get("body") or {}
    check("messages 合规（role/content）",
          b.get("messages") == [{"role": "user", "content": "在吗"}], b)
    check("generation.max_new_tokens 透传",
          (b.get("generation") or {}).get("max_new_tokens") == 128, b)
    check("generation.temperature 透传",
          abs(float((b.get("generation") or {}).get("temperature", -1)) - 0.8) < 1e-9, b)
    check("显式关思考（enable_thinking=false）", b.get("enable_thinking") is False, b)

    class _BadOpener:
        def open(self, req, timeout=None):
            return _Resp(json.dumps({"text": "", "success": False,
                                     "error": "Turn-based Chat is currently disabled"}
                                    ).encode("utf-8"))

    cli2 = el.EngineChatClient(base_url="https://localhost:8006")
    cli2._opener = lambda: _BadOpener()
    raised = False
    try:
        asyncio.run(cli2.chat([{"role": "user", "content": "x"}]))
    except RuntimeError as e:
        raised = ("turnbased" in str(e)) or ("disabled" in str(e))
    check("引擎返回 success=false → 抛错（含原因，不静默吃空）", raised is True)

    # 自动发现边界
    class _EngOn:
        endpoint = "https://localhost:9999"

        def is_model_loaded(self):
            return True

    class _EngOff:
        endpoint = "https://localhost:9999"

        def is_model_loaded(self):
            return False

    class _Host:
        pass

    h = _Host()
    h._local_omni_engine = _EngOn()
    c = el.engine_client_for_host(h, probe=True)
    check("自动发现：引擎对象 → 端点正确",
          isinstance(c, el.EngineChatClient) and c.endpoint == "https://localhost:9999", c)
    h2 = _Host()
    h2._local_omni_engine = _EngOff()
    check("引擎未挂载（probe）→ None", el.engine_client_for_host(h2, probe=True) is None)
    h3 = _Host()
    check("无引擎信息 → None", el.engine_client_for_host(h3) is None)
    h4 = _Host()
    h4._duplex_local_omni_endpoint = "https://localhost:7000"
    c4 = el.engine_client_for_host(h4)
    check("仅有 _duplex_local_omni_endpoint 也能解析",
          isinstance(c4, el.EngineChatClient) and c4.endpoint == "https://localhost:7000", c4)


def main():
    tmp = tempfile.mkdtemp(prefix="ppack_")
    try:
        t_flags(tmp)
        t_marks(tmp)
        t_festival(tmp)
        t_salience()
        t_situation(tmp)
        t_usage_context(tmp)
        t_screen_context(tmp)
        t_triggers(tmp)
        t_pending(tmp)
        t_share(tmp)
        t_feed_sources(tmp)
        t_prompts()
        t_pack(tmp)
        t_llm_gate(tmp)
        t_judge(tmp)
        t_isolation_longterm(tmp)
        t_engine_adapter(tmp)
        t_engine_client(tmp)
        t_install(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d PASS / %d FAIL" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
