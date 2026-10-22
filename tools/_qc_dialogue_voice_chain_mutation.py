# -*- coding: utf-8 -*-
"""语音→想→说 消费链 · 变异测试：证明 test_dialogue_voice_chain.py 的兜底断言有鉴别力。

思路（与 _qc_asr_event_flow_mutation.py 一致）：把 dialogue.py 里给 fire-and-forget
后台任务补的 done_callback 摘掉，再跑同款"尾段崩溃"场景，断言失败气泡应当「消失」
（即用户回到静默失声）。若摘掉回调后失败气泡仍在 → 这把刀没鉴别力 → 变异测试判红。

一把刀：
  CB  摘掉 `task.add_done_callback(_on_desktop_msg_done)`
      → 尾段异常重新落回无人接的 fire-and-forget 任务 → 无 failed 气泡 = 静默。

先跑「基线」（干净 dialogue.py + 尾段崩溃）：failed 失败气泡必须出现，否则说明
harness/桩本身坏了（这是第七处修复生效的证据）。

用法：.venv/Scripts/python.exe tools/_qc_dialogue_voice_chain_mutation.py
退出码 0=刀有鉴别力；1=刀没鉴别力或基线异常。
"""
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


async def _negative_scenario(DialogueCls):
    """尾段崩溃场景：_strip_markdown 抛异常，返回 (AI_DECISION 载荷列表, 后台任务)。"""
    from src.tests._dlg_voice_harness import build_dialogue, drive_voice
    bus, dlg = build_dialogue(strip_markdown_raise=True, DialogueCls=DialogueCls)
    return await drive_voice(bus, dlg)


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


def _has_failed_bubble(got):
    return any(isinstance(d, dict) and d.get("failed") for d in got)


async def _run():
    import asyncio as _a

    # —— 基线：干净 dialogue.py，尾段崩溃必须有 failed 失败气泡兜底（第七处修复生效） ——
    from src.companion.dialogue import Dialogue as RealDialogue
    print("=== 基线（干净 dialogue.py + 尾段崩溃，应补发 failed 失败气泡） ===")
    got, task = await _negative_scenario(RealDialogue)
    check(task is not None and task.exception() is not None,
          "基线：尾段确实抛了异常（对照组）")
    check(_has_failed_bubble(got),
          f"基线：补发了 failed 失败气泡（got={len(got)} 条）——done_callback 接住了")

    # —— 刀 CB：摘掉 add_done_callback → 失败气泡必须消失（静默失声） ——
    base_src = open(DIALOGUE_PATH, "r", encoding="utf-8").read()
    anchor = "            task.add_done_callback(_on_desktop_msg_done)\n"
    print("\n=== 刀 CB（摘掉 done_callback，失败气泡应消失 = 静默） ===")
    if anchor not in base_src:
        check(False, f"刀 CB：找不到锚点 {anchor!r}（dialogue.py 已变，需重对）")
        return
    patched = base_src.replace(anchor, "", 1)
    written = None
    try:
        mod, fpath = load_patched("dialogue_voice_chain_cb", patched)
        written = fpath
        got2, task2 = await _negative_scenario(mod.Dialogue)
        check(task2 is not None and task2.exception() is not None,
              "刀 CB：尾段仍抛了异常（证明失败真实发生，不是没跑到）")
        check(not _has_failed_bubble(got2),
              f"刀 CB：失败气泡消失（got={len(got2)} 条）——摘了回调就静默失声，鉴别力成立")
    finally:
        if written:
            try:
                os.remove(written)
            except OSError:
                pass
        sys.modules.pop("src.companion._mut_dialogue_voice_chain_cb", None)


def main():
    import asyncio
    asyncio.run(_run())
    print()
    if FAIL:
        print(f"共 {FAIL} 处不符")
        sys.exit(1)
    print("全部通过：消费链测试对 done_callback 兜底有鉴别力（摘了它就静默失声）")


if __name__ == "__main__":
    main()
