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
from src.companion.dialogue import Dialogue, EMPTY_REPLY_TEXT  # noqa: E402
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
        # unreachable-ok: 故意不可达——这行 yield 只为把函数变成**异步生成器**
        # （没有 yield 时它是普通协程，dlg 走的是流式迭代路径，测不到想测的分支）。
        # 标记由 tools/check_unreachable.py 认；不用行号白名单，行号会漂移。
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


class _FakeGameKnowledge:
    """最小游戏知识库桩：背景常识 + 当前画面 OCR 文字 + 锚点页面状态。

    main.py 两处启动路径把 `knowledge` 传给 Dialogue，同时
    `analyzer.bind_game_knowledge(knowledge)` 让 VisionAnalyzer 持续更新它——
    所以它是**被多方共享、持续更新的活对象**，不是一次性的数据包。
    """

    knowledge_text = "GK-BG-背景常识"
    screen_text = "SCR-TEXT-正在进行对局"

    def screen_text_age(self):
        return 2.0

    def correct_summary(self, s):
        return s + "(corr)"

    def anchor_state(self):
        return "ANCHOR-大厅"


async def _scenario_game_knowledge_not_dropped():
    """场景 11：一句纯聊天不能把共享的游戏知识库引用永久抹掉。

    【2026-10-23】`_build_messages` 原来用 `self._game_knowledge = None` 来表达
    "本轮不注入画面"。而 `_game_knowledge` 是 main.py 传进来、VisionAnalyzer 持续
    更新的**同一个活对象**，且全仓没有任何地方能恢复它（没有 setter，set_runtime 只
    注入 _vision_router）⇒ 第一句纯聊天之后，连游戏相关问题也再也拿不到背景常识 /
    画面文字 / 锚点状态 / 游戏名校正。症状："她一开始看得懂我在玩什么，聊了几句别的
    之后再也不会了，重启才好"——静默、永久、进程级。

    正反两路：纯聊天要压掉画面（原意图必须保住），但引用必须在；游戏问句要全带上。
    """
    print("=== 场景 11 · 纯聊天不抹掉共享的游戏知识库（正反两路） ===")
    bus, dlg = build_dialogue(keep_build_messages=True)
    gk = _FakeGameKnowledge()
    dlg._game_knowledge = gk
    # 从**实例**取类，不要用模块顶部 import 的 Dialogue：变异工具是靠换
    # `harness.Dialogue` 注入变异体的，直接 import 会让这把刀永远打在真身上。
    build = type(dlg)._build_messages

    # 反路：纯聊天 → 压掉画面信息（原意图），但**不许动引用**，背景常识仍应注入
    b_chat = build(dlg, "你觉得什么相机好", "SUM-用户在对比相机", "")[-1]["content"]
    check(dlg._game_knowledge is gk,
          "纯聊天后 _game_knowledge 仍是同一个对象（没被永久抹掉）")
    check("SCR-TEXT" not in b_chat and "ANCHOR" not in b_chat,
          "纯聊天不注入当前画面/锚点（原意图保住）")
    check("GK-BG" in b_chat,
          "纯聊天仍注入背景常识（原来也只保留这一部分）")

    # 正路：紧随其后的游戏问句 → 四项必须全回来
    b_game = build(dlg, "这局打得怎么样", "SUM-用户在玩某游戏", "")[-1]["content"]
    check(dlg._game_knowledge is gk,
          "游戏问句后 _game_knowledge 仍是同一个对象")
    check("GK-BG" in b_game, "游戏问句注入背景常识")
    check("SCR-TEXT" in b_game, "游戏问句注入当前画面文字")
    check("ANCHOR" in b_game, "游戏问句注入 OCR 锚点确认的页面状态")
    check("corr" in b_game, "游戏问句仍走游戏名校正（correct_summary）")

    # 对照组：一上来就是游戏问句（没有前面的纯聊天）→ 同样四项齐全
    bus2, dlg2 = build_dialogue(keep_build_messages=True)
    dlg2._game_knowledge = _FakeGameKnowledge()
    b_first = build(dlg2, "这局打得怎么样", "SUM-x", "")[-1]["content"]
    check(all(k in b_first for k in ("GK-BG", "SCR-TEXT", "ANCHOR", "corr")),
          "对照组：首轮就是游戏问句时四项也齐全（证明判据不是恒真）")


async def _scenario_memory_stored_when_injection_off():
    """场景 12：`memory.inject_context=false` 是"只存不用"，不是"不记"。

    【2026-10-23】`_remember_seen_images`（看图记忆）和 `_remember_pending_promise`
    （承诺/待办记忆）原来都多闸了一道 `_memory_inject_enabled()` ⇒ 关注入时**两条都不写库」。
    这与三处相矛盾：`_memory_inject_enabled` 自己的 docstring、`_build_messages` 的注释、
    以及其它所有写路径（提炼/纠正/情节，它们只看 `store is None`）。
    后果：按建议关注入的小模型用户，silently 丢掉全部"她看过这张图"和"用户说过要给我看
    照片"——后两者正是 10-04 两个修复的目标（"她压根不知道有过这张图"/"忘了就是失约"）。

    正反两路：注入关 → 照常记录但不注入；注入开 → 记录且注入。
    """
    print("=== 场景 12 · 关注入时照常记录、但不注入（正反两路） ===")
    from src.tests._dlg_voice_harness import FakeMemoryStore

    # 反路：注入关闭
    bus, dlg = build_dialogue(keep_build_messages=True)
    st = FakeMemoryStore()
    dlg._memory_store = st
    dlg._cfg.ns.memory.inject_context = False
    r_img = dlg._remember_seen_images("图中是一只橘猫在窗台上晒太阳", "这是什么", 1)
    r_pr = dlg._remember_pending_promise("我等会发给你看照片")
    check(r_img and any("发给我看过" in f for f in st.added),
          f"注入关：看图记忆仍写入库（got={st.added}）")
    check(r_pr and any("用户说过" in f for f in st.added),
          f"注入关：承诺记忆仍写入库（got={st.added}）")
    body_off = Dialogue._build_messages(dlg, "你好", "", "")[-1]["content"]
    check("发给我看过" not in body_off and "用户说过" not in body_off,
          "注入关：记忆**不**进对话上下文（只存不用的另一半）")

    # 正路：注入开启
    bus2, dlg2 = build_dialogue(keep_build_messages=True)
    st2 = FakeMemoryStore()
    dlg2._memory_store = st2
    dlg2._cfg.ns.memory.inject_context = True
    check(dlg2._remember_seen_images("图中是一只橘猫", "这是什么", 1),
          "注入开：看图记忆写入")
    check(dlg2._remember_pending_promise("我等会发给你看照片"),
          "注入开：承诺记忆写入")
    body_on = Dialogue._build_messages(dlg2, "你好", "", "")[-1]["content"]
    check("发给我看过" in body_on,
          "注入开：记忆进对话上下文（证明上一半不是恒真）")


class _StubChatSink:
    """驱动 `CharacterAPI._on_chat_user_text` 真身所需的最小 self。

    只实现它摸到的那几个成员（_chat_buffer / _next_chat_id / _save_chat_images /
    _persist_chat / _overlay_push / _wv2_broadcast），并记录落盘次数——
    "进没进聊天页"和"有没有被持久化"正是本轮要守的两件事。
    """

    def __init__(self):
        self._chat_buffer = []
        self._n = 0
        self.persisted = []

    def _next_chat_id(self):
        self._n += 1
        return self._n

    def _save_chat_images(self, imgs):
        return []

    def _persist_chat(self, entry):
        self.persisted.append(entry)

    def _overlay_push(self, *a):
        pass

    def _wv2_broadcast(self, *a):
        pass


class _StubActivityLogger:
    def __init__(self):
        self.chats = []

    def handle_chat(self, text, direction="user"):
        self.chats.append((text, direction))


async def _scenario_duplex_not_in_chat_page():
    """场景 13：双工通话里用户说的话不进桌面聊天页、不进活动日志。

    【2026-10-23】proactive 发 `USER_TEXT{source:"duplex"}` 只是为了驱动关系事件
    （dialogue 已对 duplex 早退），而通话文本本来就由 `_publish_call_record` 发
    CALL_SPEECH 进独立的"通话记录"通道——那个函数的 docstring 原话：
    "通话记录与聊天流彻底分离…不进聊天页"。

    但 `webapp._on_chat_user_text` 只判 is_proactive、漏了 duplex ⇒ 通话中用户说的
    每句话都被当成打字消息追加进 `_chat_buffer` **并 `_persist_chat` 落盘**，
    正是 10-02 建 CALL_SPEECH 要消除的那种"混进聊天页"（用户没打过的字出现在聊天流里，
    且污染持久的聊天日志）。`activity_reporter` 同漏，会让日报把通话原话当打字聊天。

    正反两路：打字/语音转写要照常进；双工与主动搭话要挡住。
    """
    print("=== 场景 13 · 双工通话内容不进聊天页/活动日志（正反两路） ===")
    from src.tests._dlg_voice_harness import (
        chat_sink_cls, activity_reporter_cls)
    ChatSink = chat_sink_cls()
    ActivityReporter = activity_reporter_cls()

    cases = (
        ("打字消息", {"text": "你好呀", "source": "webui"}, True),
        ("语音转写", {"text": "达妮娅你好", "source": "voice"}, True),
        ("双工通话·用户说的话", {"text": "我现在去打局游戏", "source": "duplex"}, False),
        ("主动搭话", {"text": "该休息啦", "source": "proactive", "is_proactive": True}, False),
    )
    for label, data, want_in in cases:
        sink = _StubChatSink()
        await ChatSink._on_chat_user_text(
            sink, Event(EventType.USER_TEXT, data, "t"))
        got_in = len(sink._chat_buffer) == 1
        check(got_in == want_in,
              f"[聊天页·{label}] {'进' if want_in else '不进'}聊天页（got={len(sink._chat_buffer)}）")
        # 落盘必须与上屏同构——进了页面就必须落盘，没进就一条都不该落
        check((len(sink.persisted) == 1) == want_in,
              f"[聊天页·{label}] 落盘数与上屏一致（got={len(sink.persisted)}）")

        act = _StubActivityLogger()
        stub_act = type("S", (), {"_logger": act})()
        await ActivityReporter._on_user_text(stub_act, Event(EventType.USER_TEXT, data, "t"))
        check((len(act.chats) == 1) == want_in,
              f"[活动日志·{label}] {'记' if want_in else '不记'}一条聊天（got={len(act.chats)}）")

    # 直接验"双工那条确实被 CALL_SPEECH 覆盖"——否则滤掉就成了丢数据。
    # _publish_call_record 是唯一发布点，且本地双工用户侧/达妮娅侧/云端都调它。
    import inspect
    import src.companion.proactive as _pro
    src_txt = inspect.getsource(_pro)
    check(src_txt.count("_publish_call_record(") >= 4,
          "通话记录发布点仍在（本地用户侧/达妮娅侧/云端）——滤掉聊天页不会丢数据")


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
    await _scenario_game_knowledge_not_dropped()
    await _scenario_memory_stored_when_injection_off()
    await _scenario_duplex_not_in_chat_page()


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
