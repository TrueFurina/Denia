# -*- coding: utf-8 -*-
"""屏幕活动入缓冲时的摘要去重（`_similar_summary` 的接线）。

**为什么需要这组测试**

`_similar_summary` 写好之后一直**没有任何调用点**——注释里说得很清楚（"否则停留
页面时会疯狂重复搭话"），但那个"疯狂重复"其实由输出侧的 `_similar_to_recent` 挡着，
而它本该管的是**输入轨迹**：同一窗口停留时视觉模型每隔几秒上报一次几乎相同的摘要，
直接 append 会让批量整合的轨迹变成"在游戏；在游戏；在游戏"——白烧 token，
也更容易让模型顺着重复说同一句话。

所以这里测三层：
  ① 判据本身（相同 / 互为子串 / 相似度 ≥0.7 / 无关）；
  ② 缓冲层（`_screen_activity_repeat`）：该合并的合并、该分开的分开；
  ③ 真行为：直接喂 `_on_screen_activity`，看缓冲实际长什么样。
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.companion.proactive import (  # noqa: E402
    ProactiveStrategy,
    _screen_activity_repeat,
    _similar_summary,
)

FAIL = 0
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(cond, msg):
    global FAIL
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL += 1


print("[1] 判据：什么样的两段摘要算「同一场景」")
check(_similar_summary("在打游戏", "在打游戏"), "完全相同 → 重复")
check(_similar_summary("用户在打游戏", "在打游戏"), "互为子串 → 重复")
check(_similar_summary("用户在玩三角洲行动", "用户在玩三角洲行动呢"), "只差一个语气词 → 重复")
check(not _similar_summary("在打游戏", "在写代码"), "完全无关 → 不重复")
check(not _similar_summary("", "在打游戏"), "空串 → 不重复（不能把空摘要当万能重复）")
check(not _similar_summary("，。！", "？？？"), "全是标点、去标点后为空 → 不重复")

print()
print("[2] 缓冲层：_screen_activity_repeat 返回「该合并到哪一条」")
buf = [{"summary": "在打游戏", "app_hint": "game", "ts": 1}]
check(_screen_activity_repeat(buf, "在打游戏", "game") is buf[0], "相同摘要 → 命中那一条")
check(_screen_activity_repeat(buf, "在打游戏呢", "game") is buf[0], "相似摘要 → 也命中")
check(_screen_activity_repeat(buf, "在写代码", "game") is None, "不同摘要 → 不命中")
check(_screen_activity_repeat(buf, "在打游戏", "code") is None,
      "摘要相同但 app_hint 变了 → 不合并（换了活动就该算新条目）")
check(_screen_activity_repeat([], "在打游戏", "game") is None, "空缓冲 → 不命中")
check(_screen_activity_repeat(buf, "", "game") is None, "空摘要 → 不命中")
check(_screen_activity_repeat(
    [{"summary": "", "app_hint": "game", "ts": 1},
     {"summary": "在打游戏", "app_hint": "game", "ts": 2}], "在打游戏", "game")
      is not None, "跳过没有摘要的条目，命中有摘要的那条")


def _stub():
    """造桩：不走 __init__（会拉起 LLM/总线），只给 _on_screen_activity 需要的字段。"""
    api = ProactiveStrategy.__new__(ProactiveStrategy)
    api._screen_batch_buf = []
    api._batch_max_entries = 0          # 0 = 不因攒满而立即 flush
    api._screen_batch_task = object()   # 非 None = 不启动批量循环
    api._llm = object()
    api._llm_generate = True
    api._in_call = lambda: False
    api._record_ocr_moment = lambda data: None
    return api


def _feed(api, summary, app_hint="game"):
    ev = SimpleNamespace(data={"summary": summary, "app_hint": app_hint,
                               "confidence": 0.8})
    asyncio.run(api._on_screen_activity(ev))


print()
print("[3] 真行为：同一场景连喂 5 次相同摘要 → 缓冲里只该有 1 条")
api = _stub()
for _ in range(5):
    _feed(api, "用户在打游戏")
check(len(api._screen_batch_buf) == 1,
      f"5 次上报只留 1 条（实际 {len(api._screen_batch_buf)} 条）")
check(api._screen_batch_buf[0]["summary"] == "用户在打游戏", "保留的是摘要原文")
_ts1 = api._screen_batch_buf[0]["ts"]
_feed(api, "用户在打游戏")
check(api._screen_batch_buf[0]["ts"] >= _ts1,
      "重复上报会刷新时间戳（表示「还在这个场景」）")

print()
print("[4] 真行为：真实变化必须能进缓冲（别把去重做成哑巴）")
_feed(api, "用户在写代码")
check(len(api._screen_batch_buf) == 2, "换内容 → 新条目进缓冲")
_feed(api, "用户在打游戏", app_hint="code")
check(len(api._screen_batch_buf) == 3, "摘要像但 app_hint 变了 → 也进缓冲")

print()
print("[5] 接线：_on_screen_activity 必须真的调用去重（源码级，防以后又摘掉）")
src = open(os.path.join(ROOT, "src", "companion", "proactive.py"),
           encoding="utf-8").read()
body = src.split("async def _on_screen_activity", 1)[-1].split("async def _on_foreground_changed", 1)[0]
check("_screen_activity_repeat(" in body, "_on_screen_activity 里调了 _screen_activity_repeat")
check("_dup['ts'] = time.time()" in body, "命中重复时刷新时间戳")
# 防退化：去重必须发生在 append 之前，否则等于没做
_i_dup = body.find("_screen_activity_repeat(")
_i_app = body.find("_screen_batch_buf.append")
check(0 <= _i_dup < _i_app, "去重判断在 append 之前（顺序错了等于没去重）")

print()
if FAIL:
    print(f"❌ {FAIL} 项未通过")
    sys.exit(1)
print("✅ 屏幕摘要去重测试全部通过")
