# -*- coding: utf-8 -*-
"""语音→LLM→TTS 消费链闭环：USER_TEXT(source=voice) 必须换来一句真的 AI_DECISION。

这是「听到→想→说」三段主链的收口证据的后半段：
  · 前半段（test_asr_event_flow.py）证明 asr 唤醒后真的发出 USER_TEXT；
  · 本文件证明 USER_TEXT 被 Dialogue 消费后，真的产出一条可播放的 AI_DECISION
    （否则"她听到却不回答"）。

**两层风险，两条判据**（缺一不可）：
  1. 桌面来源（含 voice）的 USER_TEXT 在 _on_user_text 里走 asyncio.create_task
     后台任务（fire-and-forget）→ 配 done_callback 兜底（第七处静默失效）；
  2. respond 的尾段（剥 markdown / 拟人化 / 记历史 / 发 AI_DECISION）在 respond
     内部那个只管 LLM 调用的 try/except **之外** → 尾段自己也要有守卫。

**尾段守卫的判据是"她有没有已经说出口的话"**，与 respond 里原有 LLM 失败分支同构：
已经流式播出 → 补发**非 failed** 决策（她话已说出口、TTS 已念完、气泡已上屏，
    不能再补一句"失败"，否则"已回答 + 红字失败气泡"自相矛盾）；
毫无产出 → 才发 failed 失败气泡。
所以必须四条场景一起跑才能区分"判据正确"与"判据恒真"：

  | 场景 | 流式产出 | 尾段 | 期望 |
  |---|---|---|---|
  | 1 正常链 | 有 | 不崩 | 一条非 failed 决策，streamed=True |
  | 2 已答完再崩 | **有** | 崩 | **不得**有 failed 气泡；一条非 failed 决策 |
  | 3 非流式再崩 | 无（后端不支持流式） | 崩 | 不得有 failed 气泡；非 failed、streamed=False |
  | 4 压根没生成 | 无 | LLM 就失败 | 必须有 failed 气泡（守住原有兜底）|

场景 2 是本文件的核心：它守的正是"别在已回答之后补一句失败"。
【2026-10-23】第一版没有场景 2——harness 把 _chat_decision 桩成返回 (RAW, True)，
一条 PARTIAL_TEXT 都没发，"已答完"这个前提根本没被造出来，于是把自相矛盾的行为
断言成了正确。现在 harness 让 FakeLLM 真流式，场景 1/2 用 partials 非空证明
"她确实已经说出口了"。

运行：.venv/Scripts/python.exe src/tests/test_dialogue_voice_chain.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from src.tests._dlg_voice_harness import (  # noqa: E402
    build_dialogue, drive_voice, drive_direct,
    FakeVisionRouter, make_tool_llm,
    VALID_DECISION_RAW,
    STAGE_DIRECTION_ONLY_RAW, MARKDOWN_SCAFFOLD_ONLY_RAW,
)
from src.companion.dialogue import EMPTY_REPLY_TEXT  # noqa: E402
from src.event_bus import Event, EventType  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


def _decisions(ai):
    return [d for d in ai]


def _failed(ai):
    return [d for d in ai if d.get("failed")]


def _ok_decisions(ai):
    return [d for d in ai if not d.get("failed")]


async def _scenario_positive():
    """场景 1：正常流式链。voice 用户说一句 → 得到一句真回复。"""
    print("=== 场景 1 · 正常流式链（应一条非 failed 决策，streamed=True） ===")
    bus, dlg = build_dialogue(stream_support=True)
    ai, partials, task = await drive_voice(bus, dlg)
    check(task is not None and task.done(), "USER_TEXT(voice) 触发了桌面后台任务")
    check(task is not None and task.exception() is None,
          f"正常链后台任务无异常（exc={task.exception() if task else 'NA'}）")
    check(len(partials) >= 1,
          f"流式真的发生了：{len(partials)} 条 PARTIAL_TEXT（streamed=True 不是谎）")
    check(len(_decisions(ai)) == 1,
          f"恰好一条 AI_DECISION（got={len(ai)}）")
    succ = _ok_decisions(ai)
    if succ:
        check(bool((succ[0].get("text") or "").strip()),
              f"回复文本非空：{succ[0].get('text')!r}")
        check("今天天气" in (succ[0].get("text") or ""),
              "回复文本来自模型决策（不是失败兜底话术）——证明真的走过 respond 决策链")
        check(succ[0].get("streamed") is True,
              "streamed=True（与真流式一致，TTS 才不会整段重播）")
    check(not _failed(ai), "正常链不出现 failed 失败气泡（对照组）")


async def _scenario_crash_after_streamed():
    """场景 2（核心）：她已经说出口了，尾段才崩 → 绝不能再补一句"失败"。"""
    print("=== 场景 2 · 已答完再崩（不得有 failed 气泡） ===")
    bus, dlg = build_dialogue(strip_markdown_raise=True, stream_support=True)
    ai, partials, task = await drive_voice(bus, dlg)
    check(getattr(dlg, "_inject_hits", 0) >= 1,
          "注入点确实执行到了（对照组：证明失败是真的，不是空跑）")
    check(task is not None and task.exception() is None,
          "尾段守卫已接住异常 → 后台任务本身无异常（分层生效，不需要任务级兜底）")
    check(len(partials) >= 1,
          f"前提成立：崩溃前已流式播出 {len(partials)} 句（她话已说出口）")
    check(not _failed(ai),
          f"**不得**出现 failed 失败气泡（got={len(_failed(ai))} 条）"
          "——她已答完，不能再补一句失败")
    succ = _ok_decisions(ai)
    check(len(succ) == 1,
          f"补发恰好一条非 failed 决策（got={len(succ)}）")
    if succ:
        check(bool((succ[0].get("text") or "").strip()),
              f"续发文本非空：{succ[0].get('text')!r}")
        check(succ[0].get("streamed") is True,
              "续发标 streamed=True（TTS 别整段重播，她已逐句念过）")


async def _scenario_crash_non_streamed():
    """场景 3：后端不支持流式，尾段才崩 → 仍要把话说出口，不能报失败。"""
    print("=== 场景 3 · 非流式再崩（不得有 failed 气泡，streamed=False） ===")
    bus, dlg = build_dialogue(strip_markdown_raise=True, stream_support=False)
    ai, partials, task = await drive_voice(bus, dlg)
    check(getattr(dlg, "_inject_hits", 0) >= 1,
          "注入点确实执行到了（对照组）")
    check(task is not None and task.exception() is None,
          "尾段守卫已接住异常 → 后台任务本身无异常")
    check(len(partials) == 0, "非流式路径没有 PARTIAL_TEXT（符合生产语义）")
    check(not _failed(ai),
          f"**不得**出现 failed 失败气泡（got={len(_failed(ai))} 条）"
          "——模型已生成正文，只是没来得及后处理")
    succ = _ok_decisions(ai)
    check(len(succ) == 1, f"补发恰好一条非 failed 决策（got={len(succ)}）")
    if succ:
        check(bool((succ[0].get("text") or "").strip()),
              f"续发文本非空：{succ[0].get('text')!r}")
        check(succ[0].get("streamed") is False,
              "续发标 streamed=False（TTS 把整段念出来，她还没说过）")


async def _scenario_crash_outside_respond():
    """场景 5（第七处那道防线）：崩在 respond **之外** → 只能靠任务级 done_callback。

    注入 `_desktop_media_enabled`（_process_desktop_message 里、respond 之前的探测），
    异常会逃出 respond 的两层 try，落进无人 await 的 fire-and-forget 任务。
    没有 done_callback 就是静默失声；有 → failed 失败气泡。
    这正是 tools/_qc_dialogue_voice_chain_mutation.py 那把刀要证明有鉴别力的地方。
    """
    print("=== 场景 5 · 崩在 respond 之外（必须靠 done_callback 兜底） ===")
    bus, dlg = build_dialogue(desktop_media_raise=True)
    ai, partials, task = await drive_voice(bus, dlg)
    check(getattr(dlg, "_inject_hits", 0) >= 1,
          "注入点确实执行到了（对照组）")
    check(task is not None and task.exception() is not None,
          "异常逃出了 respond → 后台任务确实带着异常（这正是会被静默吞掉的那种）")
    failed = _failed(ai)
    check(len(failed) == 1,
          f"done_callback 补发了 failed 失败气泡（got={len(failed)}）——不是静默失声")
    if failed:
        check(bool((failed[0].get("text") or "").strip()),
              f"失败气泡文本非空：{failed[0].get('text')!r}")


async def _scenario_llm_failed_before_output():
    """场景 4（对照组）：LLM 压根没生成 → 必须仍有 failed 失败气泡。

    守住 respond 里原有的"打字无回复兜底"，证明尾段守卫没把它改坏。
    """
    print("=== 场景 4 · LLM 就没生成（必须有 failed 气泡） ===")
    bus, dlg = build_dialogue()
    # 让 LLM 两条路都抛：流式迭代抛 / 一次性 chat 抛
    async def _boom_chat(messages, options=None):
        raise RuntimeError("injected: llm down")

    async def _boom_stream(messages, options=None):
        raise RuntimeError("injected: llm down")
        yield ""  # noqa: 让它成为异步生成器

    dlg._llm.chat = _boom_chat
    dlg._llm.chat_stream = _boom_stream
    ai, partials, task = await drive_voice(bus, dlg)
    check(len(partials) == 0, "一句话都没流出来（前提成立）")
    failed = _failed(ai)
    check(len(failed) == 1,
          f"LLM 无产出时仍有 failed 失败气泡（got={len(failed)}）——守住原兜底")
    if failed:
        check(bool((failed[0].get("text") or "").strip()),
              f"失败气泡文本非空：{failed[0].get('text')!r}")


async def _scenario_stage_direction_only():
    """场景 6：模型只吐了舞台动作描写 / 纯 markdown 骨架（弱模型真会这样）
    → 必须走"空响应"失败出口。

    两条都不能发生：
      · 把"（翻个白眼）"当台词播出去——非流式路径下 TTS 会念"翻个白眼"，
        正是 10-11/10-14 两次要治的（原代码 `if _hum:` 不成立时就留着未清洗文本）；
      · 发一条 text="" 的决策——前端静默丢弃，又变成"说了话没人应"。
    """
    print("=== 场景 6 · 模型只吐舞台动作/markdown 骨架（必须走空响应失败出口） ===")
    for label, raw in (("舞台动作", STAGE_DIRECTION_ONLY_RAW),
                       ("markdown 骨架", MARKDOWN_SCAFFOLD_ONLY_RAW)):
        bus, dlg = build_dialogue(stream_support=False, llm_raw=raw)
        ai, partials, task = await drive_voice(bus, dlg)
        check(task is not None and task.exception() is None,
              f"[{label}] 没有异常逃出 respond（空响应是被正常处理的，不是崩溃）")
        spoken = [d for d in ai if not d.get("failed")]
        check(not spoken,
              f"[{label}] **不得**把清洗后的残骸当台词播出（got={len(spoken)}）")
        failed = _failed(ai)
        check(len(failed) == 1,
              f"[{label}] 走空响应失败出口：恰好一条 failed 气泡（got={len(failed)}）")
        if failed:
            check(bool((failed[0].get("text") or "").strip()),
                  f"[{label}] 失败气泡文本非空：{failed[0].get('text')!r}")
            check("翻个白眼" not in (failed[0].get("text") or ""),
                  f"[{label}] 失败气泡里不含原始舞台指示")
            check(failed[0].get("text") == EMPTY_REPLY_TEXT,
                  f"[{label}] 文案走 EMPTY_REPLY_TEXT 常量（与场景 7 同一个出口，不是双写）")


async def _scenario_empty_text_from_model():
    """场景 7：模型直接就返回空 text → 早期那个空响应检查也要走同一个出口。

    与场景 6 合起来证明 `_publish_empty_reply` 的**两个调用点产出逐字一致**——
    否则就是"同一件事两份实现"，其中一份悄无声息地漂移。
    """
    print("=== 场景 7 · 模型直接返回空 text（早期检查，同一个出口） ===")
    raw = '{"emotion":"neutral","action":"idle","intensity":3,"text":""}'
    bus, dlg = build_dialogue(stream_support=False, llm_raw=raw)
    ai, partials, task = await drive_voice(bus, dlg)
    check(task is not None and task.exception() is None,
          "没有异常逃出 respond（空响应是被正常处理的，不是崩溃）")
    check(not [d for d in ai if not d.get("failed")],
          "不得播出任何台词（本来就没有）")
    failed = _failed(ai)
    check(len(failed) == 1, f"恰好一条 failed 气泡（got={len(failed)}）")
    if failed:
        check(failed[0].get("text") == EMPTY_REPLY_TEXT,
              f"文案与场景 6 逐字一致（同一个出口）：{failed[0].get('text')!r}")


async def _scenario_vision_and_tools_paths():
    """场景 8/9：另外两个能出声的入口也要过同一道闸。

    三个入口（respond 主链 / 聊天发图 / 聊天调工具）的终稿都过 _strip_markdown
    （± _humanize_reply），而这两个清洗都可能把内容清空。主链 10-23 已守，另两个
    此前**完全没守**：模型/VLM 只吐"（翻个白眼）"这类纯舞台动作时，终稿为 ""，
    发出去被前端静默丢弃 → "图也发了 / 工具也跑了，她却不说话"。

    每个入口都跑**正反两路**：正常文本必须照常播出（证明闸门不是"永远发失败"），
    纯舞台动作必须走失败出口。
    """
    print("=== 场景 8 · 聊天发图入口（正反两路） ===")
    for label, ans, want_fail in (("正常描述", "图里是一只橘猫在窗台上晒太阳", False),
                                  ("纯舞台动作", "（翻个白眼）", True)):
        bus, dlg = build_dialogue()
        dlg._shrink_images = lambda imgs: imgs
        dlg._vision_router = FakeVisionRouter(ans)
        ai, partials = await drive_direct(
            dlg, lambda: dlg._respond_vision_chat([b"img"], "这是什么", "voice"))
        check(dlg._vision_router.calls == 1, f"[{label}] 视觉模型真被调了一次")
        failed = _failed(ai)
        spoken = _ok_decisions(ai)
        if want_fail:
            check(not spoken,
                  f"[{label}] **不得**把舞台指示当图片描述播出（got={len(spoken)}）")
            check(len(failed) == 1, f"[{label}] 走失败出口（got={len(failed)}）")
            if failed:
                check(failed[0].get("text") == EMPTY_REPLY_TEXT,
                      f"[{label}] 文案走同一个常量：{failed[0].get('text')!r}")
        else:
            check(len(spoken) == 1 and not failed,
                  f"[{label}] 正常播出一条、无失败气泡（got spoken={len(spoken)} failed={len(failed)}）")
            if spoken:
                check("橘猫" in (spoken[0].get("text") or ""),
                      f"[{label}] 播出的是模型描述本身：{spoken[0].get('text')!r}")

    print("=== 场景 9 · 聊天调工具入口（正反两路） ===")
    for label, ans, want_fail in (("正常回复", "已经帮你打开播放器了", False),
                                  ("纯舞台动作", "（翻个白眼）", True)):
        bus, dlg = build_dialogue()
        dlg._tools_in_chat_enabled = lambda: True
        dlg._build_tool_messages = lambda *a, **k: [{"role": "user", "content": "x"}]
        make_tool_llm(dlg, ans)
        handled, ai = None, []
        # _respond_with_tools 自己发事件；用 drive_direct 收
        bus2, dlg2 = dlg._bus, dlg
        ai, partials = await drive_direct(
            dlg, lambda: dlg._respond_with_tools("帮我开播放器", "", "", []))
        failed = _failed(ai)
        spoken = _ok_decisions(ai)
        if want_fail:
            check(not spoken,
                  f"[{label}] **不得**把舞台指示当工具结果播出（got={len(spoken)}）")
            check(len(failed) == 1, f"[{label}] 走失败出口（got={len(failed)}）")
            if failed:
                check(failed[0].get("text") == EMPTY_REPLY_TEXT,
                      f"[{label}] 文案走同一个常量：{failed[0].get('text')!r}")
        else:
            check(len(spoken) == 1 and not failed,
                  f"[{label}] 正常播出一条、无失败气泡（got spoken={len(spoken)} failed={len(failed)}）")
            if spoken:
                check("播放器" in (spoken[0].get("text") or ""),
                      f"[{label}] 播出的是工具链结果本身：{spoken[0].get('text')!r}")


async def _scenario_channel_relayed_through_tools():
    """场景 10：社交通道的消息进工具链，channel/reply_target 必须原样带出去。

    base._feed_brain 用 respond(text, source=..., channel=self.name, reply_target=<peer>)
    喂大脑；通道侧 _on_decision 判 `d.get("channel") != self.name` 才忽略，桌面侧
    （ws_server）只播 channel 为空的。此前工具链 4 个发布点一个都不带这两个键 ⇒
    ① 发消息的人判 channel 不匹配被忽略 → **完全收不到回复**；
    ② 桌面侧当桌面决策 → **桌宠替那条 QQ 消息念出来**。
    默认 allow_tools_in_chat:false，所以这是潜伏缺陷——一开开关就发作。

    正反两路：外部通道要带上、桌面要留空（否则桌面会把 QQ 的回复也念一遍）。
    """
    print("=== 场景 10 · 工具链透传 channel/reply_target（正反两路） ===")
    tgt = {"message_type": "private", "user_id": 123456}

    # 反路（对照）：桌面来源 → channel 必须留空（否则桌宠会念 QQ 的回复）
    bus, dlg = build_dialogue()
    dlg._tools_in_chat_enabled = lambda: True
    dlg._build_tool_messages = lambda *a, **k: [{"role": "user", "content": "x"}]
    make_tool_llm(dlg, "已经帮你打开播放器了")
    ai, partials = await drive_direct(
        dlg, lambda: dlg.respond("帮我开播放器", source="webui"))
    desktop = [d for d in ai if not d.get("failed")]
    check(len(desktop) == 1, f"[桌面] 播出一条（got={len(desktop)}）")
    if desktop:
        check(desktop[0].get("channel", "") in ("", None),
              f"[桌面] channel 留空={desktop[0].get('channel', '')!r}（桌面才认）")

    # 正路：外部通道 → channel/reply_target 必须原样出现在决策上
    bus, dlg = build_dialogue()
    dlg._tools_in_chat_enabled = lambda: True
    dlg._build_tool_messages = lambda *a, **k: [{"role": "user", "content": "x"}]
    make_tool_llm(dlg, "已经帮你打开播放器了")
    ai, partials = await drive_direct(
        dlg, lambda: dlg.respond("帮我开播放器", source="qq",
                                 channel="onebot", reply_target=tgt))
    chan = [d for d in ai if not d.get("failed")]
    check(len(chan) == 1, f"[onebot] 播出一条（got={len(chan)}）")
    if chan:
        d0 = chan[0]
        check(d0.get("channel") == "onebot",
              f"[onebot] channel 透传={d0.get('channel')!r}（通道侧靠它认领）")
        check(d0.get("reply_target") == tgt,
              f"[onebot] reply_target 透传={d0.get('reply_target')!r}（靠它发回那个 peer）")
        # 直接照 _on_decision 的过滤逻辑验一遍两头
        check(d0.get("channel") == "onebot",
              "[onebot] 通道侧 _on_decision 会认领（不再被忽略）")
        check(bool(d0.get("channel")),
              "[桌面] 桌面侧不会播这条（channel 非空）→ 桌宠不替 QQ 答话")

    # 第三条路：走 USER_TEXT 事件喂一条带 channel 的消息（当前生产不用这条路，
    # 但 _on_user_text 那一跳以前也是断的——补齐后载荷里带了就生效）
    bus, dlg = build_dialogue()
    dlg._tools_in_chat_enabled = lambda: True
    dlg._build_tool_messages = lambda *a, **k: [{"role": "user", "content": "x"}]
    make_tool_llm(dlg, "已经帮你打开播放器了")
    ai, partials = await drive_direct(dlg, lambda: dlg._on_user_text(Event(
        EventType.USER_TEXT,
        {"text": "帮我开播放器", "source": "onebot",
         "channel": "onebot", "reply_target": tgt}, "test")))
    via_event = [d for d in ai if not d.get("failed")]
    check(len(via_event) == 1, f"[USER_TEXT 路径] 播出一条（got={len(via_event)}）")
    if via_event:
        check(via_event[0].get("channel") == "onebot",
              f"[USER_TEXT 路径] channel 也从载荷透传={via_event[0].get('channel')!r}")
        check(via_event[0].get("reply_target") == tgt,
              f"[USER_TEXT 路径] reply_target 也从载荷透传={via_event[0].get('reply_target')!r}")


async def _run():
    await _scenario_positive()
    await _scenario_crash_after_streamed()
    await _scenario_crash_non_streamed()
    await _scenario_llm_failed_before_output()
    await _scenario_crash_outside_respond()
    await _scenario_stage_direction_only()
    await _scenario_empty_text_from_model()
    await _scenario_vision_and_tools_paths()
    await _scenario_channel_relayed_through_tools()


def main():
    asyncio.run(_run())
    print()
    if FAIL:
        print(f"共 {FAIL} 处不符")
        return 1
    print("全部通过：语音→想→说 消费链闭环（含尾段守卫真值表：已答完不报失败，"
          "没生成才报失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
