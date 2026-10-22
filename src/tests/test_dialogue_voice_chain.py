# -*- coding: utf-8 -*-
"""语音→LLM→TTS 消费链闭环：USER_TEXT(source=voice) 必须换来一句真的 AI_DECISION。

这是「听到→想→说」三段主链的收口证据的前半段之后半段：
  · 前半段（test_asr_event_flow.py）证明 asr 唤醒后真的发出 USER_TEXT；
  · 本文件证明 USER_TEXT 被 Dialogue 消费后，真的产出一条可播放的 AI_DECISION
    （否则"她听到却不回答"）。
关键风险点（第七处候选静默失效）：桌面来源（含 voice）的 USER_TEXT 在 _on_user_text 里
走 asyncio.create_task 后台任务（fire-and-forget），而 respond 的尾段（剥 markdown /
拟人化 / 记历史 / 发 AI_DECISION）在 respond 内部 try/except **之外**。一旦尾段抛异常，
异常落到无人 await、也无 done_callback 的后台任务上 → 静默吞掉 → 用户全程无感知。

因此本测试两条：
  1) 正常链：voice USER_TEXT → 一条 failed 缺省、text 非空的 AI_DECISION；
  2) 尾段崩：注入 _strip_markdown 抛异常 → 必须有一条 failed=True 的失败气泡兜底
     （而不是静默）。这条守的就是 dialogue.py 里给 create_task 补的 done_callback。

配套变异（tools/_qc_dialogue_voice_chain_mutation.py）：去掉 done_callback 后，
  第 2 条必须从"有失败气泡"翻成"静默无声"——否则说明这条兜底是空转的。

运行：.venv/Scripts/python.exe src/tests/test_dialogue_voice_chain.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from src.tests._dlg_voice_harness import (  # noqa: E402
    build_dialogue, drive_voice, VALID_DECISION_RAW, FAIL_TEXT,
)

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


async def _run():
    # —— 场景 1：正常链路。voice 用户说一句 → 得到一句真回复 ——
    bus, dlg = build_dialogue()
    got, task = await drive_voice(bus, dlg)
    check(task is not None and task.done(), "USER_TEXT(voice) 触发了桌面后台任务")
    check(task is not None and task.exception() is None,
          f"正常链路后台任务无异常（got exc={task.exception() if task else 'NA'}）")
    succ = [d for d in got if not d.get("failed")]
    check(len(succ) == 1,
          f"恰好一条非失败 AI_DECISION（got={len(got)} 条：{[d.get('failed') for d in got]}）")
    if succ:
        check(bool((succ[0].get("text") or "").strip()),
              f"回复文本非空：{succ[0].get('text')!r}")
        check("今天天气" in (succ[0].get("text") or ""),
              "回复文本来自模型决策（不是失败兜底话术）——证明真的走过 respond 决策链")
    # 对照组：失败话术不应出现在正常链路里（区分"判据正确"与"判据恒真"）
    check(not any(d.get("failed") for d in got),
          "正常链路不出现 failed 失败气泡")

    # —— 场景 2：respond 尾段（_strip_markdown）崩 → 必须兜底成失败气泡，而非静默 ——
    bus2, dlg2 = build_dialogue(strip_markdown_raise=True)
    got2, task2 = await drive_voice(bus2, dlg2)
    check(task2 is not None and task2.done(), "尾段崩溃场景后台任务已结束")
    check(task2 is not None and task2.exception() is not None,
          "确实发生了尾段异常（对照组：证明失败是真的，不是空跑）")
    failed = [d for d in got2 if d.get("failed")]
    check(len(failed) == 1,
          f"尾段崩溃仍补发一条 failed 失败气泡（got={len(got2)} 条）——不是静默失声")
    if failed:
        check(bool((failed[0].get("text") or "").strip()),
              f"失败气泡文本非空：{failed[0].get('text')!r}")
        check(failed[0].get("text") == FAIL_TEXT,
              "失败气泡走 done_callback 兜底话术（证明是这条回调接住的）")


def main():
    asyncio.run(_run())
    print()
    if FAIL:
        print(f"共 {FAIL} 处不符")
        return 1
    print("全部通过：语音→想→说 消费链闭环（正常链有回复；尾段崩有失败气泡兜底）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
