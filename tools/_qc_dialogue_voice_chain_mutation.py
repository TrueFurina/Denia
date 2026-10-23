# -*- coding: utf-8 -*-
"""语音→想→说 消费链 · 变异测试：证明 test_dialogue_voice_chain.py 的兜底断言有鉴别力。

思路（与 _qc_asr_event_flow_mutation.py 一致）：把 dialogue.py 按"刀"改出 BUG，再跑
**测试里原封不动的那几个场景函数**，断言它们应当判红。若某把刀改完测试照样全绿 →
这把刀没鉴别力 → 变异测试本身判红。

【2026-10-23】本轮从一把刀扩到三把，因为新增了 respond 尾段守卫（第二层防线），
按项目自己的规矩「加门必须同时加变异」，新代码也要有刀。复用方式：harness 的
`build_dialogue` 对 Dialogue **延迟绑定**（模块全局），故把 `harness.Dialogue` 换成
变异体的类，就能原样跑测试里的场景函数——断言口径单一事实源，不另抄一套。

十二把刀（改完必须让对应场景判红）：
  CB  摘掉 `task.add_done_callback(_on_desktop_msg_done)`
      → 场景 5（崩在 respond 之外）失败气泡消失 = 静默失声。第一层防线失效。
  T1  尾段守卫 `if not _partial:` → `if True:`（一律向上抛、不降级续发）
      → 场景 2/3（她已答完再崩）被补一条 failed 失败气泡 = "已回答 + 红字失败"
        自相矛盾。守卫的判据失效。
  T2  尾段守卫去掉 `or parse_decision(raw).get("text", "")`
      → 场景 3（非流式路径再崩）误判成"毫无产出"。守卫对非流式路径失效。
  T3  去掉清洗后的判空（`if not (_hum or "").strip():`）
      → 场景 6：纯舞台动作描写被当台词播出 / text="" 被前端静默丢弃。
  T4  改 `EMPTY_REPLY_TEXT` 常量
      → 场景 6/7：空响应出口的两个调用点不再逐字一致（双写漂移要被抓住）。

先跑「基线」（干净 dialogue.py，五个场景）：必须全绿，否则说明 harness/测试本身坏了。

用法：.venv/Scripts/python.exe tools/_qc_dialogue_voice_chain_mutation.py
退出码 0=十二把刀都有鉴别力；1=有刀没鉴别力或基线异常。
"""
import asyncio
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DIALOGUE_PATH = os.path.join(ROOT, "src", "companion", "dialogue.py")

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


def load_patched(knife_name, source):
    """把改过的 dialogue 源写到 src/companion/_mut_<name>.py 并导入，返回 (module, path)。"""
    fname = f"_mut_{knife_name}.py"
    fpath = os.path.join(ROOT, "src", "companion", fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(source)
    modname = f"src.companion.{fname[:-3]}"
    sys.modules.pop(modname, None)
    try:
        return importlib.import_module(modname), fpath
    except Exception:
        try:
            os.remove(fpath)
        except OSError:
            pass
        raise


async def _scenario_fails(scenario_fn, DialogueCls):
    """用给定的 Dialogue 类跑测试里的某个场景函数，返回"它是否判红"。"""
    import src.tests._dlg_voice_harness as harness
    import src.tests.test_dialogue_voice_chain as tv
    old = harness.Dialogue
    harness.Dialogue = DialogueCls
    tv.FAIL = 0
    try:
        await scenario_fn()
    finally:
        harness.Dialogue = old
    return tv.FAIL > 0


async def _run():
    import src.tests.test_dialogue_voice_chain as tv
    from src.companion.dialogue import Dialogue as RealDialogue

    # —— 基线：干净 dialogue.py，十二个场景必须全绿 ——
    print("=== 基线（干净 dialogue.py，十二个场景应全绿） ===")
    for name in ("_scenario_positive", "_scenario_crash_after_streamed",
                 "_scenario_crash_non_streamed", "_scenario_llm_failed_before_output",
                 "_scenario_crash_outside_respond", "_scenario_stage_direction_only",
                 "_scenario_empty_text_from_model", "_scenario_vision_and_tools_paths",
                 "_scenario_channel_relayed_through_tools"):
        fn = getattr(tv, name)
        bad = await _scenario_fails(fn, RealDialogue)
        check(not bad, f"基线 {name}：全绿（否则 harness/测试本身坏了）")

    base_src = open(DIALOGUE_PATH, "r", encoding="utf-8").read()

    # ── 刀：每把 (名称, 锚点, 替换, 应判红的场景函数名列表) ──
    KNIVES = [
        # 第一层防线：任务级兜底失效 → 崩在 respond 之外时静默失声
        ("CB_no_done_callback",
         "            task.add_done_callback(_on_desktop_msg_done)\n",
         "",
         ["_scenario_crash_outside_respond"]),
        # 尾段守卫判据失效：一律向上抛 → "她已答完"也被补一条失败气泡（自相矛盾）
        ("T1_guard_always_raise",
         "            if not _partial:\n",
         "            if True:  # 变异：一律向上抛，不做降级续发\n",
         ["_scenario_crash_after_streamed", "_scenario_crash_non_streamed"]),
        # 尾段守卫对非流式路径失效：丢掉 raw 兜底 → 误判"毫无产出"
        ("T2_guard_drops_raw_fallback",
         "                        or parse_decision(raw).get(\"text\", \"\"))\n",
         "                        )  # 变异：丢掉 raw 兜底，非流式路径取不到文本\n",
         ["_scenario_crash_non_streamed"]),
        # 主链清洗后判空被拆掉 → 纯舞台动作被当台词播出 / text="" 被静默丢弃
        ("T3_no_empty_check_after_clean",
         "            if not await self._guard_speakable(_hum, channel, reply_target):\n"
         "                return\n"
         "            decision[\"text\"] = _hum\n",
         "            # 变异：拆掉主链清洗后判空\n"
         "            decision[\"text\"] = _hum\n",
         ["_scenario_stage_direction_only"]),
        # 空响应出口文案漂移 → 两个调用点不再逐字一致（双写/漂移要被抓住）
        ("T4_empty_reply_text_drift",
         "EMPTY_REPLY_TEXT = \"消息发送失败（模型没有给出可说的内容，请稍后再试）\"\n",
         "EMPTY_REPLY_TEXT = \"消息发送失败（变异的文案）\"\n",
         ["_scenario_stage_direction_only", "_scenario_empty_text_from_model"]),
        # 视觉链（聊天发图）那道闸被拆 → text="" 被静默丢弃 / 舞台指示被当图片描述播出
        ("T5_vision_guard_removed",
         "        if not await self._guard_speakable(_ans, \"\", None):\n"
         "            return\n",
         "        # 变异：拆掉视觉链那道闸\n",
         ["_scenario_vision_and_tools_paths"]),
        # 工具链（聊天调工具）那道闸被拆 → 同上，只是换成工具结果被静默丢弃
        ("T6_tools_guard_removed",
         "        if not await self._guard_speakable(_text_out, channel, reply_target):\n"
         "            return True\n",
         "        # 变异：拆掉工具链那道闸\n",
         ["_scenario_vision_and_tools_paths"]),
        # 工具链终稿丢掉 channel/reply_target → 通道侧收不到、桌面侧替它播
        ("T7_tools_drops_channel",
         "            # 【2026-10-23】channel/reply_target 必须带上（见函数 docstring）：\n"
         "            # 少了它们，通道侧判 channel 不匹配直接忽略、桌面侧当成桌面决策播放。\n"
         "            \"channel\": channel,\n"
         "            \"reply_target\": reply_target,\n",
         "            # 变异：丢掉 channel/reply_target\n",
         ["_scenario_channel_relayed_through_tools"]),
        # _on_user_text 那一跳丢掉载荷里的 channel/reply_target → 从 USER_TEXT 喂进来的
        # 通道消息又变成"通道侧收不到、桌面侧替它播"
        ("T8_user_text_drops_channel",
         "        await self.respond(text, source=source,\n"
         "                           channel=str(d.get(\"channel\") or \"\"),\n"
         "                           reply_target=d.get(\"reply_target\"))\n",
         "        await self.respond(text, source=source)  # 变异：丢掉载荷里的两个键\n",
         ["_scenario_channel_relayed_through_tools"]),
        # 构建函数又抹掉共享引用 → 一句纯聊天之后游戏知识库永久失效
        ("T9_build_drops_game_knowledge",
         "            kt_only = (getattr(_gk, \"knowledge_text\", \"\") or \"\").strip()\n"
         "            _gk = None\n",
         "            kt_only = (getattr(_gk, \"knowledge_text\", \"\") or \"\").strip()\n"
         "            self._game_knowledge = None  # 变异：又抹掉共享引用\n",
         ["_scenario_game_knowledge_not_dropped"]),
        # 看图记忆写路径又闸上 inject_context → 关注入时 silently 丢掉全部"她看过这张图"
        ("T10_vision_memory_gated_by_inject",
         "        store = self._memory_store\n"
         "        if store is None or not vlm_answer:\n"
         "            return False\n",
         "        store = self._memory_store\n"
         "        if store is None or not vlm_answer:\n"
         "            return False\n"
         "        try:\n"
         "            if not self._memory_inject_enabled():\n"
         "                return False\n"
         "        except Exception:\n"
         "            return False\n",
         ["_scenario_memory_stored_when_injection_off"]),
        # 承诺/待办记忆写路径同样又闸上 → "忘了就是失约"那一类对关注入的用户全丢
        ("T11_promise_memory_gated_by_inject",
         "        if not self._PROMISE_RE.search(t):\n"
         "            return False\n",
         "        if not self._PROMISE_RE.search(t):\n"
         "            return False\n"
         "        try:\n"
         "            if not self._memory_inject_enabled():\n"
         "                return False\n"
         "        except Exception:\n"
         "            return False\n",
         ["_scenario_memory_stored_when_injection_off"]),
    ]

    written = []
    try:
        for name, old, new, scenarios in KNIVES:
            print(f"\n=== 刀 {name}（应让 {', '.join(s.split('_scenario_')[1] for s in scenarios)} 判红） ===")
            if old not in base_src:
                check(False, f"刀 {name}：找不到锚点（dialogue.py 已变，需重对）")
                continue
            patched = base_src.replace(old, new, 1)
            mod, fpath = load_patched("dialogue_voice_chain_" + name, patched)
            written.append(fpath)
            for sname in scenarios:
                fn = getattr(tv, sname)
                bad = await _scenario_fails(fn, mod.Dialogue)
                check(bad, f"刀 {name}：{sname} 判红（鉴别力成立）")
    finally:
        for fpath in written:
            try:
                os.remove(fpath)
            except OSError:
                pass
        for _n in ("CB_no_done_callback", "T1_guard_always_raise",
                   "T2_guard_drops_raw_fallback", "T3_no_empty_check_after_clean",
                   "T4_empty_reply_text_drift", "T5_vision_guard_removed",
                   "T6_tools_guard_removed", "T7_tools_drops_channel",
                   "T8_user_text_drops_channel", "T9_build_drops_game_knowledge",
                   "T10_vision_memory_gated_by_inject",
                   "T11_promise_memory_gated_by_inject"):
            sys.modules.pop(f"src.companion._mut_dialogue_voice_chain_{_n}", None)


def main():
    asyncio.run(_run())
    print()
    if FAIL:
        print(f"共 {FAIL} 处不符")
        sys.exit(1)
    print("全部通过：十二把刀都有鉴别力（done_callback 兜底 + 尾段守卫判据 + 非流式兜底"
          " + 主链清洗后判空 + 空响应出口文案不漂移 + 视觉链闸门 + 工具链闸门"
          " + 工具链透传 channel/reply_target + USER_TEXT 那一跳 + 不抹共享游戏知识库 + 两条记忆写路径不闸在 inject 上）")


if __name__ == "__main__":
    main()
