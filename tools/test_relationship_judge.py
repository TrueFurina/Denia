"""关系裁判自测：RuleJudge / LLMJudge / FallbackJudge / 异步判定端到端。

跑法：
    python tools\\test_relationship_judge.py
退出码 0 = 全绿。不启动 GUI、不碰真实 data/（全部临时目录）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.companion.relationship_judge import (  # noqa: E402
    FallbackJudge, LLMJudge, RuleJudge, build_judge, heuristic_signals,
    parse_judge_json,
)

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, name: str) -> None:
    RESULTS.append((bool(cond), name))
    print(("[PASS] " if cond else "[FAIL] ") + name)


class FakeLLM:
    """替身：按脚本返回文本 / 抛超时。记录收到的 messages 供断言。"""

    def __init__(self, script: list[str], fail_after: int = 10**9) -> None:
        self.script = list(script)
        self.calls = 0
        self.fail_after = fail_after
        self.last_messages: list[dict] = []
        self.available = True

    async def chat(self, messages: list[dict]) -> str:
        self.calls += 1
        self.last_messages = messages
        if self.calls > self.fail_after:
            raise TimeoutError("fake timeout")
        return self.script.pop(0) if self.script else "{}"


PERSONA = {"name": "达妮娅", "character": "温柔", "background": "为守护自爆而死", "speaking_style": "轻缓"}


def main_sync() -> None:
    # ---------- parse_judge_json ----------
    check(parse_judge_json('{"valence":0.8,"care":0.9}')["valence"] == 0.8, "纯 JSON 可解析")
    check(parse_judge_json('```json\n{"valence":0.5}\n```')["valence"] == 0.5, "```json 围栏可解析")
    check(parse_judge_json('好的，结果如下 {"valence":-0.3} 以上')["valence"] == -0.3, "带前后废话可抠出 JSON")
    try:
        parse_judge_json("没有 json")
        check(False, "无 JSON 应抛错")
    except Exception:
        check(True, "无 JSON 应抛错")

    # ---------- LLMJudge 钳制 ----------
    llm = FakeLLM(['{"valence":5,"care":-2,"perfunctory":0.9,"repair":0,"harsh":0.2,"reason":"测试"}'])
    j = LLMJudge(llm)
    sig = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        j.judge("我喜欢你", persona=PERSONA))
    check(sig["valence"] == 1.0, "valence 越界被钳到 1.0")
    check(sig["care"] == 0.0, "care 负值被钳到 0")
    check(sig["judge"] == "llm" and sig["reason"] == "测试", "来源=llm 且带 reason")

    # ---------- 提示词必须带人设 ----------
    llm2 = FakeLLM(['{"valence":0.9,"reason":"表白"}'])
    j2 = LLMJudge(llm2)
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        j2.judge("我喜欢你", persona=PERSONA))
    blob = json.dumps(llm2.last_messages, ensure_ascii=False)
    check("达妮娅" in blob and "自爆" in blob, "LLM 裁判提示词带角色名+背景（人设感知）")

    # ---------- 超时/失败回落 ----------
    bad = LLMJudge(FakeLLM([], fail_after=0))
    fb = FallbackJudge(bad, RuleJudge())
    sig2 = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        fb.judge("嗯", persona=PERSONA))
    check(sig2["judge"].startswith("rule"), "LLM 失败 → 回落规则裁判（且标注来源）")
    check(sig2["perfunctory"] > 0, "回落结果仍可用（嗯=敷衍）")
    check(bad.failures == 1, "失败计数留痕（不静默）")


async def _end2end() -> None:
    """端到端：真实事件总线 + LLM 裁判（替身）→ 状态被异步更新、面板快照带 reason。"""
    from src.event_bus import Event, EventBus, EventType
    from src.companion.relationship_reporter import RelationshipReporter
    from src.companion.relationship import snapshot

    tmp = Path(tempfile.mkdtemp(prefix="reljudge_"))
    bus = EventBus()
    cfg = types.SimpleNamespace(active_character_id="danya", ns=types.SimpleNamespace())
    llm = FakeLLM(['{"valence":0.95,"care":0.9,"perfunctory":0,"repair":0,"harsh":0,'
                   '"reason":"用户表白，非常暖"}'])
    rep = RelationshipReporter(bus, cfg, base_dir=tmp, llm=llm)
    rep.subscribe()
    rep.start()
    bt = asyncio.create_task(bus.run())     # ⚠️ 必须跑总线循环，否则 publish 没人派发

    await bus.publish(Event(EventType.USER_TEXT, {"text": "我喜欢你", "source": "webui"}, "webui"))
    await bus.publish(Event(EventType.AI_DECISION,
                            {"text": "……真、真了吗", "emotion": "happy", "streamed": True}, "dialogue"))
    await asyncio.sleep(0.8)          # 等后台判定任务落地
    bus.stop()
    bt.cancel()
    try:
        await bt
    except asyncio.CancelledError:
        # 取消后台任务 = 场景正常收尾
        pass

    s = snapshot("danya", tmp)
    check(s.get("ok") and s.get("turns") == 1, f"异步判定已应用（turns={s.get('turns')}）")
    check(abs((s.get("signals") or {}).get("valence", 0) - 0.95) < 1e-6, "LLM 判定的 valence 已生效")
    check((s.get("signals") or {}).get("reason") == "用户表白，非常暖", "面板可拿到判定理由")
    check(s.get("dims", {}).get("trust", 50) > 50 or s.get("dims", {}).get("intimacy", 45) > 45,
          f"表白推高好感（dims={s.get('dims')}）")
    bus.stop()


def main() -> int:
    print("关系裁判自测\n")
    main_sync()
    asyncio.run(_end2end())
    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== {passed}/{total} 通过 =====")
    if passed != total:
        for ok, name in RESULTS:
            if not ok:
                print("  - " + name)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
