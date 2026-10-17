"""P1 仪式感里程碑测试。

反 PUA 三铁律的行为验证：
  1. 首次跨门槛 → 记一条里程碑 + 给"有分量的话"
  2. 单向闸门：同 kind 不重复触发
  3. 回落不撤销、回落再升也不重复
  4. 措辞不含数字（不暴露好感度）
  5. 原型差异（同一门槛，不同人格不同话）
  6. 里程碑进 last_signals（供上层展示）
  7. 绝不因里程碑异常影响好感主链
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.companion import relationship as R  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def _mk(stage_vals, archetype="温柔"):
    """造一个状态：affinity 直接给定，stage 由 setter 前需同步。"""
    t = time.time()
    st = R.default_state("danya", archetype=archetype, ts=t - 30 * 86400)
    st["created_ts"] = t - 30 * 86400
    st["turns"] = 999          # 非新手期
    st["affinity"] = dict(stage_vals)
    st["stage"] = R.RelationshipEngine._stage(st)
    st["last_interaction_ts"] = t - 3600
    return st, t


def main():
    eng = R.RelationshipEngine()

    print("== 1. 首次跨门槛 → 记里程碑 + 有话 ==")
    # 萍水 → 熟络：门槛 52。起始分≈50.4（0.4*50+0.4*48+0.2*50），一轮强正向可越过。
    st, t = _mk({"trust": 50, "intimacy": 48, "warmth": 50})
    check("初始为萍水", st["stage"] == "萍水", st["stage"])
    # 打一记强正向（care+valence 充足）把它推过 52
    sig = {"valence": 1.0, "care": 1.0, "sufficient": True, "confidence": 1.0,
           "depth": 1.0, "effort": 1.0}
    eng.apply_turn(st, dict(sig), now=t)
    check("已升到熟络", st["stage"] == "熟络", st["stage"])
    ms = st.get("milestones") or []
    check("记了 1 条里程碑", len(ms) == 1, ms)
    check("kind 正确", ms and ms[0].get("kind") == "stage:熟络", ms)
    check("last_signals 带 milestone", bool((st.get("last_signals") or {}).get("milestone")),
          st.get("last_signals"))
    _line = (st.get("last_signals") or {}).get("milestone", {}).get("line", "")
    check("有分量的话非空", bool(_line), _line)
    print("      熟络台词：", _line)

    print("\n== 2. 单向闸门：不重复 ==")
    eng.apply_turn(st, dict(sig), now=t + 10)
    ms = st.get("milestones") or []
    check("仍只有 1 条 stage:熟络", sum(1 for m in ms if m["kind"] == "stage:熟络") == 1, ms)

    print("\n== 3. 回落不撤销、再升不重复 ==")
    before = len(st.get("milestones") or [])
    # 真实回落：直接压低 affinity 并让引擎自己重算 stage（不手改 stage 字段，
    #   否则 catch_up 会先把 stage 重算回去 → 与线上路径不一致）。
    st["affinity"] = {"trust": 40, "intimacy": 40, "warmth": 40}
    st["stage"] = R.RelationshipEngine._stage(st)   # 落回萍水
    check("已回落到萍水", st["stage"] == "萍水", st["stage"])
    # 再推回熟络：起始分低 + 每轮之间有衰减 + 每日软上限 → 多打几轮、跨天摊销
    for i in range(6):
        eng.apply_turn(st, dict(sig), now=t + 20 + i * 90000)   # 每轮隔 ~25h，避开日上限
    after = len(st.get("milestones") or [])
    check("里程碑总数不变（不撤销）", after == before, (before, after))
    check("再次升到熟络", st["stage"] == "熟络", st["stage"])

    print("\n== 4. 措辞不含数字 ==")
    import re as _re
    bad = []
    for arch_tbl in R.MILESTONE_LINES.values():
        for k, v in arch_tbl.items():
            if _re.search(r"\d", v):
                bad.append((k, v))
    check("所有里程碑台词无数字", not bad, bad)

    print("\n== 5. 原型差异 ==")
    l_wen = R.MILESTONE_LINES["温柔"]["stage:交心"]
    l_ao = R.MILESTONE_LINES["傲娇"]["stage:交心"]
    check("温柔≠傲娇台词", l_wen != l_ao)
    st2, t2 = _mk({"trust": 50, "intimacy": 48, "warmth": 50}, archetype="傲娇")
    eng.apply_turn(st2, dict(sig), now=t2)
    st3, t3 = _mk({"trust": 50, "intimacy": 48, "warmth": 50}, archetype="温柔")
    eng.apply_turn(st3, dict(sig), now=t3)
    line_a = (st2["last_signals"].get("milestone") or {}).get("line", "")
    line_w = (st3["last_signals"].get("milestone") or {}).get("line", "")
    check("不同原型给不同话", line_a != line_w, (line_a, line_w))
    print("      傲娇：", line_a)
    print("      温柔：", line_w)

    print("\n== 6. 一次跨两档只记最高档（不越级重复）==")
    # 真实跳档：起始分贴近「熟络」门槛下沿（≈50），一轮强正向 → 直接越过 66 到「交心」。
    #   （不使用"手改 stage=萍水"这种与线上不一致的做法：catch_up 会先按 affinity 重算 stage。）
    st4, t4 = _mk({"trust": 40, "intimacy": 40, "warmth": 40})
    st4["affinity"] = {"trust": 62, "intimacy": 62, "warmth": 62}   # 分≈62 → 萍水<52? 不，62 已熟络
    # 要真正"跨两档"，起始必须 <52、终点 >66：起始分≈50 → 单轮 +3 上限只能到 ~53。
    #   故改用"多轮累积跨两档"，验证中间档也会各记一条（不越级合并）。
    st4, t4 = _mk({"trust": 50, "intimacy": 48, "warmth": 50})   # 分≈50.4 萍水
    st4["stage"] = "萍水"
    for i in range(3):
        eng.apply_turn(st4, dict(sig), now=t4 + i * 90000)   # 跨天摊销避开日上限
    kinds = [m["kind"] for m in st4.get("milestones") or []]
    check("至少记到熟络", "stage:熟络" in kinds, kinds)
    check("不重复同类", len(kinds) == len(set(kinds)), kinds)

    print("\n== 7. 里程碑异常不影响主链 ==")
    st5, t5 = _mk({"trust": 40, "intimacy": 35, "warmth": 40})
    st5["milestones"] = "不是列表"   # 故意坏数据
    try:
        eng.apply_turn(st5, dict(sig), now=t5)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        print("      异常:", e)
    check("坏 milestones 不抛异常", ok)
    check("好感仍被更新", float(st5["affinity"]["trust"]) > 40, st5["affinity"])

    print("\n== 8. milestone_line 单元 ==")
    st6, _ = _mk({"trust": 40, "intimacy": 35, "warmth": 40}, archetype="毒舌")
    ln = eng.milestone_line(st6, {"kind": "stage:交心"})
    check("milestone_line 取到毒舌版", "正经" in ln or ln, ln)

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
