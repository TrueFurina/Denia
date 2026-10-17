# -*- coding: utf-8 -*-
"""P0 好感度分层增量专项验证（2026-10-11）。

验证「每次加固定的分」→「按互动深浅分档」的改造：
  1) 兼容性：老信号（无 depth/effort 两字段）→ 增益与旧版一致（mult=1.0）
  2) 分层：浅层 < 普通 < 深层（逐级递增）
  3) 深层/浅层比值 ≈ 4~5×（目标"深聊一次涨得明显更多"）
  4) 深聊单轮增益可突破 MAX_STEP(3.0) 的旧上限（正向步长按深度放宽）
  5) 负向（harsh）不受 depth 削减（伤人恒疼）
  6) judge 透传：LLMJudge 输出含 depth/effort；旧 JSON 缺字段时缺省 0 不崩
  7) RuleJudge 也产出 depth/effort
"""
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.companion import relationship as R
from src.companion.relationship_judge import LLMJudge, heuristic_signals

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


def _engine():
    e = R.RelationshipEngine("danya")
    e.newbie_turns = 0
    e.newbie_days = 0.0
    return e


def _turn(eng, sig, *, base=50.0):
    st = R.default_state("danya")
    st["turns"] = 50
    for d in R.DIMENSIONS:
        st["affinity"][d] = base
    # 用真实时钟基准：_is_newbie 会拿 now 与 created_ts 比较，用假时间会误判新手期
    t = time.time()
    st["created_ts"] = t - 30 * 86400.0   # 30 天前建档 → 确定非新手
    st["last_interaction_ts"] = t - 5
    st["daily_gain"] = {"date": R._today(t), "amount": 0.0}
    eng.apply_turn(st, sig, now=t)
    return st


def _delta(eng, sig, base=50.0):
    st = _turn(eng, sig, base=base)
    return st["affinity"]["warmth"] - base


BASE = {"valence": 0.6, "care": 0.5, "perfunctory": 0.0, "repair": 0.0,
        "harsh": 0.0, "sufficient": True, "confidence": 1.0, "user_text": "x"}


def main():
    eng = _engine()

    print("== 1. 兼容性：老信号（无 depth/effort）==")
    old = _delta(eng, dict(BASE))
    st_old = _turn(eng, dict(BASE))
    check("老信号 depth_mult=1.0", st_old["last_signals"].get("depth_mult") == 1.0,
          st_old["last_signals"].get("depth_mult"))
    check("老信号增益为有限正值", old > 0, old)

    print("\n== 2/3. 分层：浅/普通/深逐级递增 ==")
    d_shallow = _delta(eng, {**BASE, "depth": 0.0, "effort": 0.0})
    d_normal = _delta(eng, {**BASE, "depth": 0.5, "effort": 0.5})
    d_deep = _delta(eng, {**BASE, "depth": 1.0, "effort": 1.0})
    print("     浅=%.3f 普通=%.3f 深=%.3f" % (d_shallow, d_normal, d_deep))
    check("浅 < 普通", d_shallow < d_normal, f"{d_shallow} vs {d_normal}")
    check("普通 < 深", d_normal < d_deep, f"{d_normal} vs {d_deep}")
    ratio = d_deep / d_shallow if d_shallow else 0
    check("深层/浅层 ≈ 4~5×", 3.5 <= ratio <= 6.0, "ratio=%.2f" % ratio)

    print("\n== 4. 深聊增益可突破旧 MAX_STEP 语境（步长按深度放宽）==")
    check("深层单轮增益 > 浅层 2 倍", d_deep > d_shallow * 2, f"{d_deep} vs {d_shallow}")
    check("深层 depth_mult > 2.0", _turn(eng, {**BASE, "depth": 1.0, "effort": 1.0})["last_signals"]["depth_mult"] > 2.0)

    print("\n== 5. 负向不受 depth 削减（伤人恒疼）==")
    harsh_shallow = _delta(eng, {"valence": -0.9, "care": 0, "harsh": 0.9,
                                 "sufficient": True, "confidence": 1.0,
                                 "depth": 0.0, "effort": 0.0}, base=60.0)
    harsh_deep = _delta(eng, {"valence": -0.9, "care": 0, "harsh": 0.9,
                              "sufficient": True, "confidence": 1.0,
                              "depth": 1.0, "effort": 1.0}, base=60.0)
    check("harsh 浅层也造成负增量", harsh_shallow < 0, harsh_shallow)
    check("harsh 深层 ≈ 浅层（不被 depth 削减）", abs(harsh_deep - harsh_shallow) < 0.05,
          f"deep={harsh_deep} shallow={harsh_shallow}")

    print("\n== 6. judge 透传 depth/effort ==")
    async def fake_new(msgs):
        return ('{"valence":0.8,"care":0.9,"depth":0.9,"effort":0.8,'
                '"sufficient":true,"confidence":0.95,"reason":"深聊"}')
    out = asyncio.run(LLMJudge(fake_new).judge("想说点心里话"))
    check("新格式 depth=0.9", out.get("depth") == 0.9, out.get("depth"))
    check("新格式 effort=0.8", out.get("effort") == 0.8, out.get("effort"))

    async def fake_old(msgs):
        return '{"valence":0.5,"sufficient":true,"confidence":0.9}'
    out2 = asyncio.run(LLMJudge(fake_old).judge("在吗"))
    check("旧格式 depth 缺省 0", out2.get("depth") == 0.0, out2.get("depth"))
    check("旧格式 effort 缺省 0", out2.get("effort") == 0.0, out2.get("effort"))

    print("\n== 7. RuleJudge 产出 depth/effort ==")
    s_love = heuristic_signals("我喜欢你")
    s_meh = heuristic_signals("嗯")
    check("表白 depth 高(≥0.7)", s_love.get("depth", 0) >= 0.7, s_love.get("depth"))
    check("敷衍 depth 低(≤0.2)", s_meh.get("depth", 1) <= 0.2, s_meh.get("depth"))
    check("表白 effort > 敷衍 effort", s_love.get("effort", 0) > s_meh.get("effort", 0))

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
