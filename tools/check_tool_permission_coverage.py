# -*- coding: utf-8 -*-
"""工具权限分类完备性：每个工具都必须"有归属"。

为什么需要（2026-11-04，借鉴 mini-workbuddy 第 7 章「五档权限模式」）
--------------------------------------------------------------------
那本书的核心判据不是"分几档"，而是**每个工具都必须被判定一次**——
框架对每次工具调用都要走一遍 _check_* 静态分析，不存在"没归类就默认放行"。

而 Denia 此前恰恰是"没归类就默认放行"：`agent_brain._DESTRUCTIVE_TOOLS`
是一份手写名单，只有列进去的工具才会在 require_confirm=true 时被询问。
实测就漏了两个：

  · kill_process   —— 杀进程，不可逆
  · start_process  —— 起进程

也就是说，开着"高危操作要确认"，她照样能不问一声杀掉你的进程。
这类漏洞不会报错、不会红门禁，只会静默变松。

判据三条：
  1. 每个工具名必须出现在 SAFE_TOOLS 或 _DESTRUCTIVE_TOOLS 之一（并集 == 全集）；
  2. 两个集合不能相交（否则"要不要确认"没答案）；
  3. 不能有僵尸项（集合里有、工具表里没有 —— 改名后忘了同步）。

跑法：.venv/Scripts/python.exe -u tools/check_tool_permission_coverage.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

OK = FAIL = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS " + name + ((" :: " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL " + name + ((" :: " + extra) if extra else ""))


def main() -> int:
    from src.llm.agent_brain import _DESTRUCTIVE_TOOLS
    from src.llm.agent_tools import SAFE_TOOLS, TOOL_SPECS

    tools = []
    for spec in TOOL_SPECS:
        fn = spec.get("function") or {}
        nm = fn.get("name") or spec.get("name")
        if nm:
            tools.append(str(nm))
    all_tools = set(tools)

    print("1) 工具表本身")
    chk("TOOL_SPECS 非空", bool(all_tools), "%d 个工具" % len(all_tools))
    chk("工具名不重复", len(tools) == len(all_tools),
        "%d 条 / %d 唯一" % (len(tools), len(all_tools)))

    print("\n2) 分类完备（每个工具都要有归属）")
    unclassified = sorted(all_tools - SAFE_TOOLS - _DESTRUCTIVE_TOOLS)
    chk("没有未归类的工具", not unclassified, str(unclassified))

    print("\n3) 两类互不相交")
    both = sorted(SAFE_TOOLS & _DESTRUCTIVE_TOOLS)
    chk("同一工具不能既安全又高危", not both, str(both))

    print("\n4) 没有僵尸项（改名后忘了同步）")
    zombies = sorted((SAFE_TOOLS | _DESTRUCTIVE_TOOLS) - all_tools)
    chk("分类里的工具都在工具表里", not zombies, str(zombies))

    print("\n统计：工具 %d = 安全 %d + 高危 %d"
          % (len(all_tools), len(SAFE_TOOLS), len(_DESTRUCTIVE_TOOLS)))
    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    if FAIL:
        print("\n⚠️ 加新工具时必须在 src/llm/agent_tools.py 的 SAFE_TOOLS 或")
        print("   src/llm/agent_brain.py 的 _DESTRUCTIVE_TOOLS 里选一个归属。")
        print("   拿不准就放高危 —— 多问一句，好过不声不响地动手。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
