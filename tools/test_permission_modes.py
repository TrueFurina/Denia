# -*- coding: utf-8 -*-
"""五档权限模式的判定矩阵测试。

守什么
------
1. 五档 × 三类工具（SAFE / WRITE / EXEC）的判定完全符合设计矩阵；
2. 未分类的工具名按 EXEC 处理（保守）；
3. `dont_ask` 的 EXEC 必须是**拒绝**，不是放行（"问不出来就拒绝"）；
4. `explore` 连写文件都拒（"只读"要真的只读）；
5. 未配置（空串）时 `decide_tool` 不参与判定，调用方走老逻辑；
6. 认不出来的模式回 `default`，**绝不能变成全放行**。

跑法：.venv/Scripts/python.exe -u tools/test_permission_modes.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.llm import permission as P  # noqa: E402

OK = FAIL = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS " + name + ((" :: " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL " + name + ((" :: " + extra) if extra else ""))


# 每类取一个代表工具（名字都来自 TOOL_SPECS，别写不存在的）
SAFE_T = "read_file"
WRITE_T = "write_file"
EXEC_T = "run_command"
UNKNOWN_T = "definitely_not_a_tool"

# 设计矩阵（照 permission.py 文档里的表抄的，任何一格变了都必须红）
MATRIX = {
    P.MODE_EXPLORE:      {SAFE_T: P.ALLOW, WRITE_T: P.DENY,  EXEC_T: P.DENY,  UNKNOWN_T: P.DENY},
    P.MODE_DEFAULT:      {SAFE_T: P.ALLOW, WRITE_T: P.ASK,   EXEC_T: P.ASK,   UNKNOWN_T: P.ASK},
    P.MODE_ACCEPT_EDITS: {SAFE_T: P.ALLOW, WRITE_T: P.ALLOW, EXEC_T: P.ASK,   UNKNOWN_T: P.ASK},
    P.MODE_DONT_ASK:     {SAFE_T: P.ALLOW, WRITE_T: P.ALLOW, EXEC_T: P.DENY,  UNKNOWN_T: P.DENY},
    P.MODE_BYPASS:       {SAFE_T: P.ALLOW, WRITE_T: P.ALLOW, EXEC_T: P.ALLOW, UNKNOWN_T: P.ALLOW},
}


def main() -> int:
    print("1) 五档 × 三类工具 的判定矩阵")
    for mode, row in MATRIX.items():
        for tool, want in row.items():
            got = P.decide_tool(mode, tool)
            chk("%-13s %-22s → %s" % (mode, tool, want), got == want, "实际 " + got)

    print("\n2) 未配置 = 不参与判定")
    chk("空串 decide_tool 返回 allow（调用方走老逻辑）",
        P.decide_tool("", EXEC_T) == P.ALLOW)
    chk("is_configured('') 为 False", P.is_configured("") is False)
    chk("is_configured('default') 为 True", P.is_configured("default") is True)

    print("\n3) 别名与未知值")
    chk("read_only → explore", P.normalize_mode("read_only") == P.MODE_EXPLORE)
    chk("auto → accept_edits", P.normalize_mode("auto") == P.MODE_ACCEPT_EDITS)
    chk("大写 DEFAULT 也认", P.normalize_mode("  DEFAULT ") == P.MODE_DEFAULT)
    # 关键：写错一个字母不能变成"全放行"
    chk("未知模式回 default（不是 bypass）",
        P.normalize_mode("explorer") == P.MODE_DEFAULT)
    chk("未知模式的 EXEC 判定是 ask（不是 allow）",
        P.decide_tool("explorer", EXEC_T) == P.ASK)

    print("\n4) 三个最容易做错的点")
    chk("dont_ask 的 EXEC 是拒绝（问不出来就拒绝）",
        P.decide_tool(P.MODE_DONT_ASK, EXEC_T) == P.DENY)
    chk("explore 连写文件也拒（只读要真只读）",
        P.decide_tool(P.MODE_EXPLORE, WRITE_T) == P.DENY)
    chk("bypass 才全放行", P.decide_tool(P.MODE_BYPASS, EXEC_T) == P.ALLOW)

    print("\n5) 每一档都有说明文案")
    for m in P.MODES:
        chk("%-13s describe 非空" % m, bool(P.describe(m).strip()))

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
