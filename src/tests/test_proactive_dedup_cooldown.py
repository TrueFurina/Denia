# -*- coding: utf-8 -*-
"""主动搭话「去重 + 冷却时间戳守卫」回归（2026-10-31）。

为什么测这几个函数
------------------
proactive.py 里「不刷屏骚扰用户」这条核心行为由两层纯/轻量逻辑撑着：
  · **输入轨迹去重** `_screen_activity_repeat` / `_similar_summary`：同一窗口停留时
    视觉模型每隔几秒就上报几乎相同的摘要（"在打游戏""在打游戏"），直接 append 会让
    批量轨迹变成"在游戏；在游戏；在游戏"——白烧 token、且模型顺着重复说同一句话。
  · **输出重复去重** `_similar_to_recent`：与最近说过的话太像就丢弃。
  · **冷却时间戳守卫** `_last_proactive_or_zero`：`_last_proactive` 在 __init__ 被显式
    置 None（"本进程尚未主动开过口"），旧代码 `now - getattr(self,'_last_proactive',0.0)`
    对 None 相减抛 TypeError，被最外层 except 以 DEBUG 吞掉 → 每轮批冷却判定必炸、
    主动搭话永不触发。这个 helper 把 None/非法值规整为 0.0（视为"很久没开口"→放行）。

测法：纯函数直接调；实例方法用 fake self（不构造完整 Proactive，避免拉起重依赖链）。
覆盖面为「去重 + 冷却时间戳地基」；handler 级的 type_cooldown_s/moment_cooldown_s 比较
需要完整事件流夹具，不在本测试范围（已在报告 §5 P1 备注）。
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

FAIL = 0


def check(cond, msg):
    global FAIL
    if cond:
        print("  ✓", msg)
    else:
        FAIL += 1
        print("  ✗ FAIL:", msg)


from src.companion import proactive

ProactiveStrategy = proactive.ProactiveStrategy


def _fake_self(**kw):
    """最小 fake：只带被测方法会读的属性。"""
    ns = SimpleNamespace()
    ns._recent_messages = kw.get("recent", [])
    ns._duplex_ai_lines = kw.get("ai_lines", [])
    ns._dup_sim = ProactiveStrategy._dup_sim
    if "last_proactive" in kw:
        ns._last_proactive = kw["last_proactive"]
    return ns


def test_similar_summary():
    print("[A] _similar_summary —— 屏幕摘要重复判定")
    ss = proactive._similar_summary
    check(ss("在打游戏", "在打游戏") is True, "完全相同 → True")
    check(ss("在打游戏", "在 打游戏。") is True, "仅标点/空白差异 → True（归一化后相同）")
    check(ss("在打游戏", "他在打游戏呢") is True, "互为子串 → True")
    check(ss("在打游戏", "我在打游戏而且很开心地打了一整天") is True,
          "长串包含短串但 ratio<0.7 → 仍 True（子串判定独立于相似度生效）")
    check(ss("在写代码", "在看电影") is False, "明显不同 → False")
    check(ss("", "在打游戏") is False, "空串 → False")
    check(ss("在打游戏", "") is False, "空串（反向）→ False")
    # 非子串但字符相似度 ≥0.7：只差末尾一个字
    check(ss("今天天气很好啊", "今天天气很好吧") is True, "相似度≥0.7（差一字）→ True")


def test_screen_activity_repeat():
    print("[B] _screen_activity_repeat —— 输入轨迹去重")
    rep = proactive._screen_activity_repeat
    buf = [{"summary": "在打游戏", "app_hint": "steam"}]
    check(rep(buf, "在打游戏", "steam") is not None, "同摘要+同 app_hint → 命中（去重）")
    check(rep(buf, "在打游戏", "epic") is None, "摘要像但 app_hint 变 → 不算重复（不吞真实变化）")
    check(rep(buf, "在看电影", "steam") is None, "摘要明显不同 → 不算重复")
    check(rep(buf, "", "steam") is None, "空摘要 → None")
    check(rep([], "在打游戏", "steam") is None, "空缓冲 → None")

    # 跳过空摘要条目，命中最近的带摘要条目
    buf2 = [{"summary": "", "app_hint": "steam"},
            {"summary": "在打游戏", "app_hint": "steam"}]
    check(rep(buf2, "在打游戏", "steam") is not None, "跳过空摘要条目后命中带摘要条目")

    # 只与「最近一条带摘要的条目」比较：最近一条不同 → 直接 None，不回头找更旧的
    buf3 = [{"summary": "在打游戏", "app_hint": "steam"},
            {"summary": "在看电影", "app_hint": "steam"}]
    check(rep(buf3, "在打游戏", "steam") is None,
          "只比最近一条：最近是不同摘要 → None（不回头命中更旧那条）")
    # 反向：最近一条正好相同 → 命中
    buf4 = [{"summary": "在看电影", "app_hint": "steam"},
            {"summary": "在打游戏", "app_hint": "steam"}]
    check(rep(buf4, "在打游戏", "steam") is not None, "最近一条相同 → 命中")


def test_last_proactive_or_zero():
    print("[C] _last_proactive_or_zero —— 冷却时间戳守卫（None 相减静默死修复）")
    fn = ProactiveStrategy._last_proactive_or_zero
    check(fn(_fake_self(last_proactive=None)) == 0.0, "None → 0.0（视为很久没开口，放行）")
    check(fn(_fake_self(last_proactive=123.5)) == 123.5, "合法 float → 原值")
    check(fn(_fake_self(last_proactive="abc")) == 0.0, "非法字符串 → 0.0")
    check(fn(_fake_self(last_proactive=0.0)) == 0.0, "0.0 → 0.0")
    check(fn(_fake_self()) == 0.0, "属性缺失 → 0.0（getattr 默认 None）")


def test_similar_to_recent():
    print("[D] _similar_to_recent —— 输出重复去重")
    fn = ProactiveStrategy._similar_to_recent
    check(fn(_fake_self(recent=[]), "") is True, "空文本 → True（丢弃）")
    check(fn(_fake_self(recent=[]), "你好") is False, "无历史 → False")
    check(fn(_fake_self(recent=["你好"]), "你好") is True, "与最近一句完全相同 → True")
    check(fn(_fake_self(recent=["完全不同的另一句话内容"]), "你好") is False,
          "与历史明显不同 → False")
    check(fn(_fake_self(recent=[], ai_lines=["你好"]), "你好") is True,
          "与双工 AI 历史行相同 → True")


def main():
    test_similar_summary()
    test_screen_activity_repeat()
    test_last_proactive_or_zero()
    test_similar_to_recent()
    total = 8 + 8 + 5 + 5
    if FAIL == 0:
        print(f"\n===== {total}/{total} 通过 =====")
        return 0
    print(f"\n===== {total - FAIL}/{total} 通过，{FAIL} 失败 =====")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
