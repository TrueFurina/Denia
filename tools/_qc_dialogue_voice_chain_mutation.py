# -*- coding: utf-8 -*-
"""语音→想→说 消费链 · 变异测试：证明 test_dialogue_voice_chain.py 的兜底断言有鉴别力。

思路（与 _qc_asr_event_flow_mutation.py 一致）：把 dialogue.py 按"刀"改出 BUG，再跑
**测试里原封不动的那几个场景函数**，断言它们应当判红。若某把刀改完测试照样全绿 →
这把刀没鉴别力 → 变异测试本身判红。

【2026-10-23】本轮从一把刀扩到三把，因为新增了 respond 尾段守卫（第二层防线），
按项目自己的规矩「加门必须同时加变异」，新代码也要有刀。复用方式：harness 的
`build_dialogue` 对 Dialogue **延迟绑定**（模块全局），故把 `harness.Dialogue` 换成
变异体的类，就能原样跑测试里的场景函数——断言口径单一事实源，不另抄一套。

三把刀（改完必须让对应场景判红）：
  CB  摘掉 `task.add_done_callback(_on_desktop_msg_done)`
      → 场景 5（崩在 respond 之外）失败气泡消失 = 静默失声。第一层防线失效。
  T1  尾段守卫的 `if _partial:` 改成 `if False:`
      → 场景 2/3（她已答完再崩）被补一条 failed 失败气泡 = "已回答 + 红字失败"
        自相矛盾。守卫的判据失效。
  T2  尾段守卫去掉 `or parse_decision(raw).get("text", "")`
      → 场景 3（非流式路径再崩）误判成"毫无产出"→ 发 failed。守卫对非流式路径失效。

先跑「基线」（干净 dialogue.py，五个场景）：必须全绿，否则说明 harness/测试本身坏了。

用法：.venv/Scripts/python.exe tools/_qc_dialogue_voice_chain_mutation.py
退出码 0=三把刀都有鉴别力；1=有刀没鉴别力或基线异常。
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

    # —— 基线：干净 dialogue.py，五个场景必须全绿 ——
    print("=== 基线（干净 dialogue.py，五个场景应全绿） ===")
    for name in ("_scenario_positive", "_scenario_crash_after_streamed",
                 "_scenario_crash_non_streamed", "_scenario_llm_failed_before_output",
                 "_scenario_crash_outside_respond"):
        fn = getattr(tv, name)
        bad = await _scenario_fails(fn, RealDialogue)
        check(not bad, f"基线 {name}：全绿（否则 harness/测试本身坏了）")

    base_src = open(DIALOGUE_PATH, "r", encoding="utf-8").read()

    # ── 三把刀：每把 (名称, 锚点, 替换, 应判红的场景函数名列表) ──
    KNIVES = [
        ("CB_no_done_callback",
         "            task.add_done_callback(_on_desktop_msg_done)\n",
         "",
         ["_scenario_crash_outside_respond"]),
        ("T1_guard_always_failed",
         "            _partial = (parse_decision(streamer.raw).get(\"text\", \"\")\n"
         "                        or parse_decision(raw).get(\"text\", \"\"))\n"
         "            if _partial:",
         "            _partial = (parse_decision(streamer.raw).get(\"text\", \"\")\n"
         "                        or parse_decision(raw).get(\"text\", \"\"))\n"
         "            if False:  # 变异：一律走 failed 分支",
         ["_scenario_crash_after_streamed", "_scenario_crash_non_streamed"]),
        ("T2_guard_drops_raw_fallback",
         "                        or parse_decision(raw).get(\"text\", \"\"))\n",
         "                        )  # 变异：丢掉 raw 兜底，非流式路径取不到文本\n",
         ["_scenario_crash_non_streamed"]),
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
        sys.modules.pop("src.companion._mut_dialogue_voice_chain_CB_no_done_callback", None)
        sys.modules.pop("src.companion._mut_dialogue_voice_chain_T1_guard_always_failed", None)
        sys.modules.pop("src.companion._mut_dialogue_voice_chain_T2_guard_drops_raw_fallback", None)


def main():
    asyncio.run(_run())
    print()
    if FAIL:
        print(f"共 {FAIL} 处不符")
        sys.exit(1)
    print("全部通过：三把刀都有鉴别力（done_callback 兜底 + 尾段守卫判据 + 非流式兜底）")


if __name__ == "__main__":
    main()
