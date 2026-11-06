# -*- coding: utf-8 -*-
"""LLM 收尾失败时的本地事实汇报：功能判据 + 真实 run() 链路验证。

背景：工具已执行、收尾 LLM 却 429（本会话实证 3 次），旧实现把原始 HTTP 错误
原样丢给用户，屏幕上完全看不出事办过。_llm_failure_reply() 改为列本地事实。

三层判据（缺一不可）：
  1. 纯函数：列工具、不编造结果、没跑过工具就退回原始错误串；
  2. 账本耦合：返回串必须仍含「LLM 调用失败」，否则 run_agent_task 的
     partial_execution 会漏记；且 judge_ok 仍保守判 False；
  3. 真实链路：完整跑 AgentBrain.run()，工具执行后第二次 LLM 请求 429，
     断言最终返回串确实带上了工具事实（不是只测了 helper）。

不依赖真实 API，可复现。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.llm import agent_brain as ab  # noqa: E402
from src.system.tasks import judge_ok  # noqa: E402

PASS = 0
FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ " + label)
    else:
        FAIL += 1
        print("  ✗ " + label)


ERR = '{"error":{"code":"1305","message":"模型忙"}}'


class OKResp:
    status_code = 200
    text = ""

    def __init__(self, message):
        self._m = message

    def json(self):
        return {"choices": [{"message": self._m}], "usage": {"total_tokens": 10}}


class FailResp:
    status_code = 429
    text = ERR

    def json(self):
        return {}


def _tc():
    return {"id": "c1", "type": "function",
            "function": {"name": "open_app",
                         "arguments": json.dumps({"target": "记事本"},
                                                 ensure_ascii=False)}}


async def run_partial_scenario():
    """工具成功执行 → 第二次 LLM 请求 429 → 看返回给用户的是什么。"""
    n = [0]

    async def fake_post(cli, url, body, headers, retries=2):
        n[0] += 1
        if n[0] == 1:
            return OKResp({"content": "", "tool_calls": [_tc()]})
        return FailResp()

    async def fake_dispatch(fname, fargs, ctx):
        return "已启动：notepad"

    brain = ab.AgentBrain(base_url="http://fake.local", api_key="k", model="m",
                          max_steps=6, plan_first=False, verify_actions=False)
    with mock.patch.object(ab, "_post_llm", fake_post), \
         mock.patch("src.llm.agent_tools.dispatch_tool", fake_dispatch), \
         mock.patch("httpx.AsyncClient.post", fake_post):
        return await brain.run("打开记事本")


def main() -> int:
    print("1) 纯函数判据")
    bare = ab._llm_failure_reply(429, ERR, [])
    check(bare.startswith("电脑代理 LLM 调用失败（HTTP 429）"),
          "无工具 → 退回原始错误串，不粉饰")
    check("已实际执行" not in bare and "未做复核" not in bare,
          "无工具 → 不声称执行过任何东西")

    one = ab._llm_failure_reply(429, ERR, ["open_app"])
    check("open_app" in one and "1 次工具调用" in one,
          "单工具 → 列出工具名与调用次数")
    check("LLM 调用失败" in one, "保留账本用的失败标记")
    check("未做复核" in one, "明确说未复核，不暗示已达成目标")
    check(not any(w in one for w in ("已完成", "已成功", "操作成功", "任务完成")),
          "不编造完成式措辞")

    dup = ab._llm_failure_reply(429, ERR, ["open_app", "open_app", "ui_inspect"])
    check("3 次工具调用" in dup and "open_app、ui_inspect" in dup,
          "重复工具 → 次数用原始长度、名字保序去重")
    check(judge_ok(one, ["open_app"], "", expect_tools=True) is False,
          "judge_ok 仍保守判 False（用户确实没拿到完整回复）")

    print("\n2) 账本耦合")
    _tools = ["open_app"]
    _reply_failed = any(mk in one for mk in
                        ("LLM 调用失败", "电脑代理执行异常", "未配置 LLM",
                         "电脑代理需要 httpx"))
    check(_reply_failed, "partial_execution 的触发条件仍成立")

    print("\n3) 真实 run() 链路（工具跑过 + 收尾 429）")
    out = asyncio.run(run_partial_scenario())
    print("  返回 =", repr(out[:110]))
    check("open_app" in out, "用户能看到实际执行过的工具")
    check("LLM 调用失败" in out, "用户能看到收尾失败的真实原因")
    check("HTTP 429" in out, "原始错误码没有被藏起来")
    check("未做复核" in out, "没有把「执行过」说成「已完成」")

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
