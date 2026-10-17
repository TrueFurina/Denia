"""Plan B 反思聚合测试（episode → 画像洞察）。

用**桩 LLM** 驱动 `_maybe_reflect_episodes`，不起真模型：
  1. episode 不足阈值 → 不触发
  2. 达到阈值 + 有增量 → 触发、写入 user 洞察
  3. 近似洞察不重复写（has_insight 去重）
  4. 水位推进（同一批不反复聚合）
  5. LLM 异常 → 静默降级不抛
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.companion.memory_store import MemoryStore, MEMORY_REFLECT_PROMPT  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


class _StubLLM:
    def __init__(self, reply='[{"fact":"用户很在意摄影，喜欢记录生活细节","type":"user"}]',
                 raise_exc=False):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("stub boom")
        return self.reply


class _FakeDialogue:
    """只带反思所需的最小字段（不构造真 Dialogue，避免重依赖）。"""
    def __init__(self, store, llm, min_ep=4):
        self._memory_store = store
        self._llm = llm
        self._reflect_min_episodes = min_ep
        self._reflect_last_ep_ts = 0.0


async def main():
    from src.companion import dialogue as D
    fn = D.Dialogue._maybe_reflect_episodes

    print("== 1. 阈值不足 → 不触发 ==")
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(path=str(Path(td) / "m.json"))
        st.add("10月8日他聊了相机", type="episode", source="chat")
        st.add("10月9日他说要去旅游", type="episode", source="chat")
        llm = _StubLLM()
        fd = _FakeDialogue(st, llm, min_ep=4)   # 只有 2 条 < 4
        await fn(fd)
        check("episode 不足 → LLM 未被调用", llm.calls == 0)

    print("\n== 2. 达阈值 + 增量 → 触发并写入 ==")
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(path=str(Path(td) / "m.json"))
        for i, s in enumerate(["10月8日他聊了相机", "10月9日他说要去旅游",
                               "10月10日他拍了照片", "10月10日晚他说等会发照片给我"],
                              start=1):
            st.add(s, type="episode", source="chat")
        llm = _StubLLM()
        fd = _FakeDialogue(st, llm, min_ep=4)
        await fn(fd)
        check("LLM 被调用一次", llm.calls == 1)
        users = [m["text"] for m in st._memories if m.get("type") == "user"]
        check("画像洞察已写入 user", any("摄影" in u for u in users), users)

    print("\n== 3. 近似洞察不重复写 ==")
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(path=str(Path(td) / "m.json"))
        for i in range(4):
            st.add("10月%d日他聊了摄影" % (8 + i), type="episode", source="chat")
        st.add("用户很在意摄影，喜欢记录生活细节", type="user", source="chat")
        llm = _StubLLM('[{"fact":"用户很在意摄影，喜欢记录生活细节","type":"user"}]')
        fd = _FakeDialogue(st, llm, min_ep=4)
        await fn(fd)
        users = [m["text"] for m in st._memories if m.get("type") == "user"]
        check("近似洞察未重复写入", len(users) == 1, users)

    print("\n== 4. 水位推进 → 同批不反复聚合 ==")
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(path=str(Path(td) / "m.json"))
        for i in range(4):
            st.add("10月%d日他聊了摄影" % (8 + i), type="episode", source="chat")
        llm = _StubLLM('[{"fact":"用户偏爱摄影","type":"user"}]')
        fd = _FakeDialogue(st, llm, min_ep=4)
        await fn(fd)
        c1 = llm.calls
        await fn(fd)   # 第二次：无新 episode → 不应再调 LLM
        check("第一次触发了", c1 == 1, c1)
        check("第二次未重复调用", llm.calls == 1, llm.calls)
        check("水位已推进", fd._reflect_last_ep_ts > 0, fd._reflect_last_ep_ts)

    print("\n== 5. LLM 异常 → 静默降级不抛 ==")
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(path=str(Path(td) / "m.json"))
        for i in range(4):
            st.add("10月%d日他聊了摄影" % (8 + i), type="episode", source="chat")
        llm = _StubLLM(raise_exc=True)
        fd = _FakeDialogue(st, llm, min_ep=4)
        try:
            await fn(fd)
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
            print("      异常:", e)
        check("不抛异常", ok)

    print("\n== 6. recent_episodes / has_insight 单元 ==")
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(path=str(Path(td) / "m.json"))
        st.add("情节甲", type="episode", source="chat")
        st.add("普通事实乙", type="user", source="chat")
        eps = st.recent_episodes(limit=5)
        check("recent_episodes 只返回 episode", all(e.get("type") == "episode" for e in eps), eps)
        check("recent_episodes 含情节甲", any(e["text"] == "情节甲" for e in eps), eps)
        check("has_insight('普通事实乙') → True", st.has_insight("普通事实乙"))
        check("has_insight('毫不相干的内容XYZ') → False",
              not st.has_insight("毫不相干的内容XYZ"))

    print("\n== 7. prompt 完整性 ==")
    check("REFLECT prompt 含 {text}", "{text}" in MEMORY_REFLECT_PROMPT)
    check("REFLECT prompt 要求 JSON 数组", "JSON 数组" in MEMORY_REFLECT_PROMPT or "[]" in MEMORY_REFLECT_PROMPT)

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
