"""长任务护栏 · 端到端接线验证（2026-11-07）

与 check_loop_guard.py 的分工：
  - 那边验「判据对不对」（直接调 RepeatFailureGuard.observe）+ 静态检查源码里
    有没有那几行接线；
  - 这里验「**跑起来时 hint 真的进了发给 LLM 的 messages**」，走完整
    AgentBrain.run() 多轮循环。

为什么必须补这一层：源码里写着 `messages.append(hint)` 不等于执行到那一行。
`step > 0` 条件、确认闸 `continue`、LLM 非 200 提前 return、护栏异常被吞，
任何一处都能让判据空转，而静态字符串检查全绿。

不依赖真实 API：_post_llm 与 dispatch_tool 全部替换为可控假实现，
可复现、不消耗额度、不受 429 影响。
"""
import asyncio
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest import mock  # noqa: E402

from src.llm import agent_brain as ab  # noqa: E402

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


class FakeResp:
    def __init__(self, message):
        self.status_code = 200
        self.text = ""
        self._message = message

    def json(self):
        return {"choices": [{"message": self._message}],
                "usage": {"total_tokens": 10}}


def _tc(i, target="记事本"):
    return {"id": "c%d" % i, "type": "function",
            "function": {"name": "open_app",
                         "arguments": json.dumps({"target": target},
                                                 ensure_ascii=False)}}


async def run_scenario(results, targets=None, max_steps=10):
    """跑一次完整 run()。results 是每次 dispatch_tool 的返回值序列。

    返回 (每轮请求开始时的 messages 快照列表, 最终回复, 工具调用次数)。
    假 LLM 在 results 用完前一直发起同一个工具调用，之后给出终态回复。
    """
    captured = []
    dispatch_i = [0]
    targets = targets or ["记事本"] * len(results)

    async def fake_post(cli, url, body, headers, retries=2):
        # body["messages"] 在 run() 里是同一个 list 引用，必须深拷贝，
        # 否则最后拿到的全是循环结束时的最终状态。
        captured.append(copy.deepcopy(body["messages"]))
        n = len(captured)
        if n <= len(results):
            return FakeResp({"content": "", "tool_calls": [_tc(n, targets[n - 1])]})
        return FakeResp({"content": "卡住了，已如实说明。", "tool_calls": []})

    async def fake_dispatch(fname, fargs, ctx):
        i = dispatch_i[0]
        dispatch_i[0] += 1
        return results[min(i, len(results) - 1)]

    async def fake_confirm(desc):
        return True

    brain = ab.AgentBrain(base_url="http://fake.local", api_key="k", model="m",
                          max_steps=max_steps, plan_first=False,
                          verify_actions=False)
    with mock.patch.object(ab, "_post_llm", fake_post), \
         mock.patch("src.llm.agent_tools.dispatch_tool", fake_dispatch), \
         mock.patch("httpx.AsyncClient.post", fake_post):
        out = await brain.run("打开记事本", on_confirm=fake_confirm)
    return captured, out, dispatch_i[0]


def _hints(msgs):
    return [m["content"] for m in msgs
            if m.get("role") == "user" and "（重试护栏）" in str(m.get("content", ""))]


async def main():
    F = "工具 open_app 执行失败：目标无响应"
    OK = "已启动：notepad"

    print("=== 场景 1：同工具 + 同参数连续失败 3 次 ===")
    caps, out, ntools = await run_scenario([F, F, F, F])
    print("  轮数 = %d | 工具调用 = %d | 回复 = %r" % (len(caps), ntools, out[:30]))
    per_round = [len(_hints(c)) for c in caps]
    print("  每轮 hint 数 =", per_round)
    check(len(caps) >= 5, "循环至少跑满 5 轮（没有被提前 return 截断）")
    check(ntools >= 4, "工具真的被执行了 4 次（不是只在源码里写了 observe）")
    check(per_round[:3] == [0, 0, 0], "前 3 轮请求不带 hint（首轮不该看到『你失败过』）")
    check(len(per_round) > 3 and per_round[3] == 1,
          "第 4 轮请求带且只带 1 条 hint（阈值=3 后注入）")
    # 【口径】messages 是累积列表：第 4 轮注入的 hint 会作为历史留在后续轮快照里。
    # 所以「只注入一次」不能判「快照里出现 1 次」，要判「**新增**过 1 次」——
    # 若真刷屏，第 5 轮应有 2 条（或 2 条不同的）hint。
    all_hints = [h for c in caps for h in _hints(c)]
    check(len(set(all_hints)) == 1,
          "整轮只产生 1 条不同的 hint（后续轮只是历史留存，不是重复注入）")
    if len(caps) > 4:
        i3 = next((i for i, m in enumerate(caps[3])
                   if "（重试护栏）" in str(m.get("content", ""))), -1)
        i4 = next((i for i, m in enumerate(caps[4])
                   if "（重试护栏）" in str(m.get("content", ""))), -1)
        check(i3 >= 0 and i3 == i4,
              "hint 在后续轮仍是同一条历史消息（索引未变 ⇒ 未再次 append）")
    h = _hints(caps[3])[0] if len(per_round) > 3 and per_round[3] else ""
    check("不要再原样重试" in h and "open_app" in h, "hint 内容含工具名与换方案指示")
    check(caps[3][-1].get("role") == "user" and "（重试护栏）" in str(caps[3][-1].get("content")),
          "hint 位于该轮消息末尾、角色为 user（弱模型不会当成 system 复读）")

    print("=== 场景 2：成功后清零（旧失败不串到新方案）===")
    caps2, _, _ = await run_scenario([F, F, OK, F, F])
    p2 = [len(_hints(c)) for c in caps2]
    print("  每轮 hint 数 =", p2)
    check(sum(p2) == 0, "2 次失败 → 成功 → 2 次失败，全程不注入（阈值未达 3）")

    print("=== 场景 3：换参数重新计数 ===")
    caps3, _, _ = await run_scenario([F, F, F, F], targets=["记事本", "记事本", "计算器", "计算器"])
    p3 = [len(_hints(c)) for c in caps3]
    print("  每轮 hint 数 =", p3)
    check(sum(p3) == 0, "参数在第 3 次变化 ⇒ 连续同参数失败未达 3，不误伤")

    print()
    print("结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
