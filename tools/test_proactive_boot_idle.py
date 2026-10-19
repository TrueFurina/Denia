# -*- coding: utf-8 -*-
"""隔离验证：开机后未打字时，check_and_trigger 不再死触发（2026-10-11 修复）。

不启动整个软件，只构造最小 Proactive 桩验证判据分支：
  1) _last_input_ts=0 + _session_boot_ts=很久以前 → 空闲成立 → 走到关系闸门/LLM 路径
  2) _last_input_ts=0 + _session_boot_ts=刚刚       → 空闲不足 → 不触发
  3) _last_input_ts=刚打过字                        → 空闲不足 → 不触发
  4) 通话中                                        → 让位（不触发）
用 A/B 对照：把判定链断点记录在 _probe 上。
"""
import asyncio
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 判定链断点记录在 _probe 上：main() 里用 global 覆盖它，FakeLLM.chat 读它。
# 模块级先定义，全局引用才显式（静态名完整性检查也认这条链）。
_probe = None

PASS = 0
FAIL = 0
SKIP = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


class Probe:
    """只跑到"是否越过空闲闸门"为止——用 _llm 记录是否被调用。"""

    def __init__(self, last_input, boot_ts, in_call=False, idle_thr=180.0,
                 batch_interval=300.0, last_proactive=0.0):
        self._last_input_ts = last_input
        self._session_boot_ts = boot_ts
        self._in_call_flag = in_call
        self._idle_threshold_s = idle_thr
        self._batch_interval_s = batch_interval
        self._last_proactive = last_proactive
        self._duplex_last_speak_ts = 0.0
        self._llm_generate = True
        self._llm = None
        self._persona = None
        self._cfg = types.SimpleNamespace(active_character_id="")  # 空 → 跳过关系闸门
        self._recent_messages = []
        self._last_message = None
        self.reached_llm = False          # 断点：是否走到 LLM 生成
        self.returned_early = None        # 断点：在哪一步提前 return

    def _in_call(self):
        return self._in_call_flag

    # 复刻生产实现（Probe 不走真实 __init__，需自带 helper）
    def _last_proactive_or_zero(self):
        try:
            v = getattr(self, '_last_proactive', None)
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    async def _maybe_memory_initiative(self, *a, **k):
        return False                       # 跳过记忆型主动，直落通用引信

    async def _schedule_duplex_emit(self, *a, **k):
        return None

    async def _log_duplex_source(self, *a, **k):
        return None

    def _record_duplex_ai_line(self, *a, **k):
        return None

    def _focus_state(self):
        return "正常活动中"

    def _clean_duplex_speech(self, t):
        return (t or "").strip()

    def _is_empty_filler(self, t):
        return False

    def _similar_to_recent(self, t):
        return False

    # 走真实代码：从 proactive 模块取未绑定函数
    async def run(self):
        from src.companion import proactive as P
        await P.ProactiveStrategy.check_and_trigger(self)


async def main():
    from src.companion import proactive as P
    # 用一个假的 _llm，记录是否被调用（真实函数会在关系闸门后调 self._llm.chat）
    class FakeLLM:
        async def chat(self, *a, **k):
            _probe.reached_llm = True
            return ""
    print("== 主动找人·开机未打字判据（2026-10-11）==")
    now = time.time()

    # 1) 从未输入过 + 开机已过很久 → 应越过空闲闸门（走到 LLM）
    global _probe
    _probe = Probe(last_input=0.0, boot_ts=now - 400)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("1 未输入+开机久 → 触发(走到LLM)", _probe.reached_llm)

    # 2) 从未输入过 + 刚开机 → 空闲不足 → 不触发
    _probe = Probe(last_input=0.0, boot_ts=now - 5)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("2 未输入+刚开机 → 不触发", not _probe.reached_llm)

    # 3) 刚打过字 → 空闲不足
    _probe = Probe(last_input=now - 5, boot_ts=now - 4000)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("3 刚打字 → 不触发", not _probe.reached_llm)

    # 4) 打字已过很久 → 触发
    _probe = Probe(last_input=now - 400, boot_ts=now - 4000)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("4 打字后久无输入 → 触发", _probe.reached_llm)

    # 5) 通话中 → 让位
    _probe = Probe(last_input=now - 400, boot_ts=now - 4000, in_call=True)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("5 通话中 → 让位不触发", not _probe.reached_llm)

    # 6) 批量冷却未到 → 不触发
    _probe = Probe(last_input=now - 400, boot_ts=now - 4000, last_proactive=now - 10)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("6 冷却未到 → 不触发", not _probe.reached_llm)

    # 7)【2026-10-11 回归】真实生产初值 _last_proactive=None（__init__ 显式置 None）
    #    旧代码 `now - getattr(self,'_last_proactive',0.0)`：属性存在且为 None →
    #    getattr 返回 None → `now - None` 抛 TypeError → 被外层 DEBUG 吞 → 永不触发。
    #    修复后 None 应被规整为 0.0（"很久没开口"）→ 冷却放行 → 走到 LLM。
    _probe = Probe(last_input=now - 400, boot_ts=now - 4000, last_proactive=None)
    _probe._llm = FakeLLM()
    await _probe.run()
    check("7 last_proactive=None(生产初值) → 触发[回归]", _probe.reached_llm)

    # 8) 直接验证 helper：None/非法值 → 0.0，正常值原样返回
    from src.companion import proactive as _P
    class _H:
        pass
    _h = _H()
    _h._last_proactive = None
    check("8a helper(None) → 0.0",
          _P.ProactiveStrategy._last_proactive_or_zero(_h) == 0.0)
    _h._last_proactive = "bad"
    check("8b helper('bad') → 0.0",
          _P.ProactiveStrategy._last_proactive_or_zero(_h) == 0.0)
    _h._last_proactive = 123.5
    check("8c helper(123.5) → 123.5",
          _P.ProactiveStrategy._last_proactive_or_zero(_h) == 123.5)
    del _h._last_proactive
    check("8d helper(缺失) → 0.0",
          _P.ProactiveStrategy._last_proactive_or_zero(_h) == 0.0)

    print("\n结果: %d PASS / %d FAIL / %d SKIP" % (PASS, FAIL, SKIP))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
