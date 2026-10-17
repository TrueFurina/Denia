# -*- coding: utf-8 -*-
"""上下文驱动记忆提炼（memory_synthesis.py）专项验证（2026-10-11）。

覆盖（不联网、不依赖真模型——用桩 LLM/桩 store）：
  1) 触发阈值：用量未达线不提炼、达线才提炼、轮数不足不提炼
  2) tier 差异：cloud 80% / local 70%；窗口不同导致同样文本结论不同
  3) 节流：同一段上下文 force=False 第二次跳过；force=True 强制跑
  4) 类型归一：LLM 输出 fact/plan/未知 type → 全部归 user（不再被猜成 world）
  5) 失败降级：LLM 抛异常 → ok=False + error，不抛、busy 复位
  6) 上下文太短 → 明确拒绝（不空跑）
  7) 写入：解析出的条目确实写进 store，且类型正确；<3 字的丢弃
  8) 拼文本：超长从尾部保留最近内容（含最新一轮）
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.companion.memory_synthesis import (
    MemorySynthesizer, make_synthesizer, build_conversation_text,
    conversation_token_estimate, CTX_WINDOW, CTX_TRIGGER_RATIO,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


# ---------------- 桩件 ----------------
class StubStore:
    """最小 store：记录 add 调用；可开关成功/失败。"""

    def __init__(self):
        self.added = []
        self.fail = False

    def add(self, text, type="user", source="user", **kw):
        if self.fail:
            raise RuntimeError("store boom")
        self.added.append((text, type))
        return True


class StubLLM:
    """桩 LLM：返回预设文本；可配置抛异常 / 记录收到的 prompt。"""

    def __init__(self, reply="[]", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.seen = []

    async def chat(self, messages, options=None):
        self.seen.append(messages)
        if self.raise_exc:
            raise self.raise_exc
        return self.reply


def turns(n, per=10, role="user"):
    """造 n 轮对话，每轮 per 个字符（用于精确控制 token 估算）。"""
    out = []
    for i in range(n):
        out.append({"role": role if i % 2 == 0 else "assistant",
                    "content": f"第{i}轮" + "字" * per})
    return out


def run(coro):
    return asyncio.run(coro)


def main():
    print("== 1. 触发阈值（cloud, 窗口 32000, 线 80%=25600）==")
    syn = MemorySynthesizer(StubLLM(), StubStore(), tier="cloud", min_turns=4)
    check("cloud 窗口=32000", CTX_WINDOW["cloud"] == 32000)
    check("cloud 触发线=0.80", CTX_TRIGGER_RATIO["cloud"] == 0.80)
    few = turns(3)                       # 轮数不足
    check("轮数 < min_turns 不触发", syn.should_synthesize(few) is False)
    small = turns(10, per=10)            # 约 400 字符，远不到线
    check("用量远低于线不触发", syn.should_synthesize(small) is False)
    big = turns(10, per=3000)            # 约 30000+ 字符 > 25600
    check("用量超线触发", syn.should_synthesize(big) is True)

    print("\n== 2. tier 差异 ==")
    syn_l = MemorySynthesizer(StubLLM(), StubStore(), tier="local", min_turns=4)
    check("local 窗口=4096", CTX_WINDOW["local"] == 4096)
    check("local 触发线=0.70", CTX_TRIGGER_RATIO["local"] == 0.70)
    # 同一段 1000 字符：local(4096) 约 24%→不触发；不需要；换 3000 字符
    mid = turns(10, per=300)             # ~3000 字符
    check("cloud 下 3000 字不触发", syn.should_synthesize(mid) is False)
    check("local 下 3000 字触发(≈73%)", syn_l.should_synthesize(mid) is True)
    # 未知 tier 兜底 0.80
    unk = MemorySynthesizer(StubLLM(), StubStore(), tier="weird", min_turns=4)
    check("未知 tier 窗口兜底 32000", unk.current_usage_ratio(big) < 2.0)
    check("未知 tier 触发线兜底 0.80", unk.should_synthesize(big) is True)

    print("\n== 3. 节流 ==")
    store = StubStore()
    llm = StubLLM(reply='[{"fact":"用户晚上七点吃的饭","type":"user"}]')
    syn = MemorySynthesizer(llm, store, tier="cloud", min_turns=4)
    seg = turns(6, per=50)
    r1 = run(syn.synthesize(seg))
    check("首次提炼 ok", r1.get("ok") is True, r1)
    check("首次写入 1 条", r1.get("stored") == 1, r1)
    r2 = run(syn.synthesize(seg))         # 同一段，force=False
    check("同段第二次被节流跳过", r2.get("skipped") is True and r2.get("ok") is False, r2)
    check("节流时未再调用 LLM", len(llm.seen) == 1, len(llm.seen))
    r3 = run(syn.synthesize(seg, force=True))
    check("force=True 强制跑", r3.get("ok") is True, r3)
    check("force 时再调 LLM", len(llm.seen) == 2, len(llm.seen))

    print("\n== 4. 类型归一（fact/plan/未知 → user）==")
    reply = ('[{"fact":"用户晚上七点吃的饭","type":"fact"},'
             '{"fact":"用户下周去西安","type":"plan"},'
             '{"fact":"用户希望叫他漂泊者","type":"directive"},'
             '{"fact":"用户对某事的情绪","type":"bogus"}]')
    items = MemorySynthesizer._parse(reply)
    types = [t for _, t in items]
    check("fact → user", types[0] == "user", types)
    check("plan → user", types[1] == "user", types)
    check("directive 保留", types[2] == "directive", types)
    check("未知 type → user（不再 world）", types[3] == "user", types)
    check("解析出 4 条", len(items) == 4, items)

    print("\n== 5. 失败降级 ==")
    bad = MemorySynthesizer(StubLLM(raise_exc=RuntimeError("net down")),
                            StubStore(), tier="cloud", min_turns=4)
    rf = run(bad.synthesize(turns(6, per=50)))
    check("LLM 失败 → ok=False", rf.get("ok") is False, rf)
    check("LLM 失败 → 带 error", "net down" in str(rf.get("error", "")), rf)
    check("LLM 失败 → busy 复位", bad.busy is False)
    # store 抛异常：单条失败只跳过，不影响整体
    st2 = StubStore(); st2.fail = True
    syn2 = MemorySynthesizer(StubLLM(reply='[{"fact":"用户养了只猫","type":"user"}]'),
                             st2, tier="cloud", min_turns=4)
    r5 = run(syn2.synthesize(turns(6, per=50)))
    check("store 抛异常不冒泡（stored=0）", r5.get("ok") is True and r5.get("stored") == 0, r5)

    print("\n== 6. 上下文太短 ==")
    syn3 = MemorySynthesizer(StubLLM(), StubStore(), tier="cloud", min_turns=4)
    rs = run(syn3.synthesize([{"role": "user", "content": "在吗"}]))
    check("太短 → ok=False", rs.get("ok") is False, rs)
    check("太短 → 不调 LLM", len(syn3._llm.seen) == 0)

    print("\n== 7. 写入正确性 ==")
    store7 = StubStore()
    llm7 = StubLLM(reply='[{"fact":"用户晚上七点吃的饭","type":"user"},'
                         '{"fact":"好","type":"user"},'                 # <3 字应丢
                         '{"fact":"用户希望叫他漂泊者","type":"directive"}]')
    syn7 = MemorySynthesizer(llm7, store7, tier="cloud", min_turns=4)
    r7 = run(syn7.synthesize(turns(6, per=50)))
    check("写入 2 条（<3 字丢弃）", r7.get("stored") == 2, r7)
    check("store 收到的类型正确", store7.added[1][1] == "directive", store7.added)
    check("store 收到含七点那条", any("七点" in a[0] for a in store7.added), store7.added)

    print("\n== 8. 拼文本 / token 估算 ==")
    txt = build_conversation_text([{"role": "user", "content": "你好"},
                                   {"role": "assistant", "content": "在"}])
    check("用户行带『用户：』", "用户：你好" in txt, txt)
    check("AI 行带角色名", "达妮娅：在" in txt, txt)
    long_txt = build_conversation_text(turns(500, per=50), max_chars=1000)
    check("超长文本被截到 ≤1000+半句", len(long_txt) <= 1300, len(long_txt))
    check("截断后保留最新内容", "第499轮" in long_txt, long_txt[-80:])
    check("token 估算 = len", conversation_token_estimate(
        [{"role": "user", "content": "123456"}]) == 6)

    print("\n== 9. 工厂 ==")
    class _Cfg:
        raw = {"memory": {"synthesis_min_turns": 7}}
    ms = make_synthesizer(StubLLM(), StubStore(), cfg=_Cfg(), tier="cloud")
    check("工厂读到 min_turns=7", ms._min_turns == 7, ms._min_turns)
    ms2 = make_synthesizer(StubLLM(), StubStore(), cfg=None, tier="cloud")
    check("无 cfg 时默认 min_turns=4", ms2._min_turns == 4, ms2._min_turns)

    print(f"\n== 结果：{PASS} PASS / {FAIL} FAIL ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
