# -*- coding: utf-8 -*-
"""长任务护栏闸门：第 8 章两条判据 + 接线，缺一就红。

为什么单独一道（2026-11-07）
----------------------------
搬 mini-workbuddy 第 8 章机制进 Denia 时，最危险的不是判据写错，
而是**写了没接进 `AgentBrain`**——模块能单测全绿，线上一次都没跑。
所以这道闸同时查三件事：
  1. `src/llm/loop_guard.py` 的判据真值表（三态 / 重复失败 / 节流 / 预算）；
  2. `agent_brain.py` 里三处接线（初始化、每轮重置、分发后 observe、下轮注入）；
  3. 模块被 import 时不会拖垮主流程（try/except 包裹）。

跑法：.venv/Scripts/python.exe -u tools/check_loop_guard.py
"""
from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

OK = FAIL = 0


def check(cond: bool, label: str) -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS", label)
    else:
        FAIL += 1
        print("  FAIL", label)


def main() -> int:
    from src.llm.loop_guard import (
        TaskList, RepeatFailureGuard, ContextBudget, TimeHint,
        is_failure, PENDING, IN_PROGRESS, COMPLETED,
    )

    print("1) 工具返回值判成败（保守口径）")
    check(is_failure("工具 open_app 执行失败"), "工具前缀 = 失败")
    check(is_failure("权限模式「explore」禁止执行 open_app"), "权限拒绝 = 失败")
    check(is_failure(""), "空返回 = 失败")
    check(not is_failure("已启动：notepad"), "正常成功 = 非失败")
    check(not is_failure("UI 控件树（前 22 个）"), "普通文本 = 非失败")
    check(not is_failure("（自检通过）已确认"), "自检通过 = 非失败")

    print("\n2) 任务清单三态（书 8.2）")
    tl = TaskList()
    a = tl.create("清点目录")
    b = tl.create("写摘要")
    check(tl.counts() == {PENDING: 2, IN_PROGRESS: 0, COMPLETED: 0}, "新建全 pending")
    check(tl.update(a, IN_PROGRESS), "可改 in_progress")
    check(tl.update(b, COMPLETED), "可改 completed")
    check(not tl.update(a, "deleted"), "deleted 不是合法状态")
    check(tl.counts()[IN_PROGRESS] == 1 and tl.counts()[COMPLETED] == 1, "计数正确")
    check("[in_progress]" in tl.render() and "[completed]" in tl.render(), "渲染是短文本")
    check(tl.delete(a), "可删除")
    check(not any(i["id"] == a for i in tl.to_list()), "删除后不在列表")

    print("\n3) 重复失败注入（书 8.6，核心）")
    g = RepeatFailureGuard(limit=3)
    args = {"target": "记事本"}
    check(g.observe("open_app", args, "工具 open_app 执行失败") is None, "第1次失败不注入")
    check(g.observe("open_app", args, "工具 open_app 执行失败") is None, "第2次失败不注入")
    h = g.observe("open_app", args, "工具 open_app 执行失败")
    check(h is not None and "不要再原样重试" in h, "第3次注入 hint")
    check(g.observe("open_app", args, "工具 open_app 执行失败") is None, "只注入一次，不刷屏")
    # 成功后清零
    g.observe("open_app", args, "已启动：notepad")
    check(g.state()["count"] == 0, "成功后计数清零")
    # 换参数清零（换个方案不算原样重试）
    g.observe("open_app", args, "工具 open_app 执行失败")
    g.observe("open_app", args, "工具 open_app 执行失败")
    g.observe("open_app", {"target": "计算器"}, "工具 open_app 执行失败")
    check(g.state()["count"] == 1, "换参数后重新计数")
    # 参数字典键顺序不影响判定
    g2 = RepeatFailureGuard(limit=2)
    g2.observe("x", {"a": 1, "b": 2}, "失败")
    check(g2.observe("x", {"b": 2, "a": 1}, "失败") is not None,
          "字典键顺序不影响同参数判定")

    print("\n4) 上下文预算（书 8.4，阈值 <= 0.9）")
    cb = ContextBudget(window=1000, trigger_ratio=0.8, buffer_ratio=0.2)
    check(cb.threshold == 800 and cb.warn_line == 640, "触发线=窗口×比例")
    check(cb.hint(100) is None, "远离预警带不提示")
    check("接近压缩线" in (cb.hint(700) or ""), "进入预警带给提示")
    check("达到压缩线" in (cb.hint(850) or ""), "越过触发线要求收口")
    cb2 = ContextBudget(window=1000, trigger_ratio=2.0)
    check(cb2.trigger_ratio <= 0.9, "trigger_ratio 被夹到 <=0.9")

    print("\n5) 时间注入节流（书 8.6）")
    th = TimeHint(interval_s=1800)
    check(th.hint(now=1000.0) is not None, "首次注入")
    check(th.hint(now=1000.0 + 1799) is None, "半小时内不重复")
    check(th.hint(now=1000.0 + 1801) is not None, "超过半小时再注入")

    print("\n6) 接线完整性（防「写了没进门」）")
    brain = io.open("src/llm/agent_brain.py", encoding="utf-8").read()
    check("from .loop_guard import RepeatFailureGuard, ContextBudget, TaskList" in brain,
          "__init__ 里 import 护栏")
    check("self._repeat_guard.observe(fname, fargs, result)" in brain,
          "dispatch_tool 后喂结果")
    check("self._pending_hint" in brain and "messages.append({\"role\": \"user\", \"content\": self._pending_hint})" in brain,
          "下一轮请求前注入 hint")
    check(brain.count("self._repeat_guard = RepeatFailureGuard()") >= 2,
          "初始化 + 每轮重置各一次")
    check("if self._pending_hint and step > 0:" in brain, "首轮不注入")
    check("logger.debug(\"重试护栏观察失败(忽略): %s\", _he)" in brain,
          "护栏异常不拖垮主流程")

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
