"""P0 自测：关系状态机 / 历史持久化 / 在线时段计时。

**纯离线**：不联网、不加载任何模型、不碰真实 data/（全部写在临时目录）。
跑法：
    C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tools\\test_relationship.py
退出码 0 = 全绿。

时间基准：全部用固定 t0，并把 state 的 created_ts 显式钉到 t0（否则"新手期"判定会
被真实时钟干扰——踩过这个坑）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.relationship import RelationshipManager  # noqa: E402
from companion.relationship_judge import heuristic_signals  # noqa: E402
from companion.history_store import HistoryStore  # noqa: E402
from companion.session_clock import SessionClock  # noqa: E402

RESULTS: list[tuple[bool, str]] = []
T0 = 1_760_000_000.0  # 固定时间基准
CID = "danya"


def check(cond: bool, name: str) -> None:
    RESULTS.append((bool(cond), name))
    print(("[PASS] " if cond else "[FAIL] ") + name)


def section(title: str) -> None:
    print("\n=== " + title + " ===")


def stamp(mgr: RelationshipManager, cid: str, *, turns: int = 10, days_old: float = 10.0) -> None:
    """把状态钉成"非新手期"（turns 够 + 创建时间在 days_old 天前）。"""
    st = mgr.get(cid)
    st["turns"] = turns
    st["created_ts"] = T0 - days_old * 86400.0
    mgr.store.save(st)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="reltest_"))

    # ------------------------------------------------------------ 状态 / 落盘
    section("状态骨架 / 落盘 / 坏文件降级")
    mgr = RelationshipManager(base_dir=tmp)
    st = mgr.get(CID)
    check(set(st["affinity"].keys()) == {"trust", "intimacy", "warmth"}, "默认状态含三维好感")
    check(st["mood"]["label"] == "平静", "初始心情=平静")
    check(st["archetype"] == "", "初始原型为空（待反推）")

    mgr.set_archetype(CID, "傲娇")
    check(mgr.get(CID)["archetype"] == "傲娇", "可设置原型")

    p = mgr.store.path_for(CID)
    check(p.exists(), "落盘文件存在")
    check(not p.with_suffix(".json.tmp").exists(), "原子写无残留 .tmp")
    check(json.loads(p.read_text(encoding="utf-8"))["char_id"] == CID, "文件内容可解析")

    p.write_text("{ 这不是合法 JSON ", encoding="utf-8")
    st2 = mgr.get(CID)
    check(len(list(p.parent.glob("danya.json.bad-*"))) == 1, "坏文件被隔离为 *.bad-<ts>")
    check(st2["mood"]["label"] == "平静" and isinstance(st2["affinity"]["warmth"], (int, float)),
          "坏文件后从默认重建且可读")

    # ------------------------------------------------------------ 好感动力学
    section("好感动力学：新手期 / 负向 / 正向 / 去重 / 每日上限")
    mgr2 = RelationshipManager(base_dir=tmp / "d2")
    mgr2.set_archetype(CID, "傲娇")
    s = mgr2.get(CID)
    s["created_ts"] = T0          # 钉住创建时间（新手期）
    mgr2.store.save(s)

    w_before = float(mgr2.get(CID)["affinity"]["warmth"])
    mgr2.on_turn(CID, {"harsh": 1.0, "valence": -1.0, "user_text": "你是个废物"}, now=T0)
    check(abs(float(mgr2.get(CID)["affinity"]["warmth"]) - w_before) < 1e-6,
          "新手期内负向被闸住（warmth 不变）")

    for i in range(6):
        mgr2.on_turn(CID, {"valence": 0.4, "user_text": f"普通第{i}轮内容不同"}, now=T0 + 60 * (i + 1))
    stamp(mgr2, CID, turns=10, days_old=10.0)
    w_pre = float(mgr2.get(CID)["affinity"]["warmth"])
    mgr2.on_turn(CID, {"harsh": 1.0, "valence": -1.0, "user_text": "你真垃圾啊"}, now=T0 + 600)
    w_post = float(mgr2.get(CID)["affinity"]["warmth"])
    check(w_post < w_pre, f"非新手期负向生效（{w_pre:.1f} -> {w_post:.1f}）")
    check(w_post >= 15.0, "warmth 不低于留底 15")

    mgr3 = RelationshipManager(base_dir=tmp / "d3")
    tr0 = float(mgr3.get(CID)["affinity"]["trust"])
    mgr3.on_turn(CID, {"valence": 0.7, "care": 1.0, "user_text": "你今天怎么样呀"}, now=T0)
    tr1 = float(mgr3.get(CID)["affinity"]["trust"])
    check(tr1 > tr0, f"关心她 → 信任上升（{tr0:.1f} -> {tr1:.1f}）")

    # 去重
    mgr4 = RelationshipManager(base_dir=tmp / "d4")
    stamp(mgr4, CID)
    for i in range(6):
        mgr4.on_turn(CID, {"valence": 0.5, "user_text": f"铺垫内容{i}"}, now=T0 + 30 * i)
    a0 = float(mgr4.get(CID)["affinity"]["intimacy"])
    mgr4.on_turn(CID, {"valence": 0.5, "user_text": "在吗在吗在吗"}, now=T0 + 1000)
    a1 = float(mgr4.get(CID)["affinity"]["intimacy"])
    mgr4.on_turn(CID, {"valence": 0.5, "user_text": "在吗在吗在吗"}, now=T0 + 1100)
    a2 = float(mgr4.get(CID)["affinity"]["intimacy"])
    check((a2 - a1) < (a1 - a0) * 0.7, f"重复内容增益打折（{a1 - a0:.3f} -> {a2 - a1:.3f}）")

    # 每日上限
    mgr5 = RelationshipManager(base_dir=tmp / "d5")
    stamp(mgr5, CID)
    for i in range(6):
        mgr5.on_turn(CID, {"valence": 0.5, "user_text": f"前置{i}"}, now=T0 + 10 * i)
    gains = []
    for i in range(12):
        b = float(mgr5.get(CID)["affinity"]["trust"])
        mgr5.on_turn(CID, {"valence": 0.9, "care": 1.0, "user_text": f"今天聊点新东西{i}"},
                     now=T0 + 200 + 20 * i)
        gains.append(float(mgr5.get(CID)["affinity"]["trust"]) - b)
    check(max(gains[-3:]) < max(gains[:3]) * 0.6,
          f"软每日上限生效（首3轮最大 {max(gains[:3]):.3f} vs 末3轮 {max(gains[-3:]):.3f}）")

    # ------------------------------------------------------------ 证据充分度门（借鉴 crush-monitor enough）
    section("证据充分度门：sufficient / confidence")
    # 规则裁判：孤立短敷衍 → sufficient=False；有明确情绪/意图 → True
    check(heuristic_signals("嗯")["sufficient"] is False, "规则裁判：'嗯' sufficient=False")
    check(heuristic_signals("哦")["sufficient"] is False, "规则裁判：'哦' sufficient=False")
    check(heuristic_signals("你好")["sufficient"] is True, "规则裁判：'你好' sufficient=True")
    check(heuristic_signals("我喜欢你")["sufficient"] is True, "规则裁判：'我喜欢你' sufficient=True")
    check(heuristic_signals("滚")["sufficient"] is True, "规则裁判：'滚'(有 harsh) sufficient=True")

    # sufficient=False：即便带负信号也不动好感（治"嗯/哦"误判）
    mg = RelationshipManager(base_dir=tmp / "dg")
    aff0 = dict(mg.get(CID)["affinity"])
    mg.on_turn(CID, {"valence": -0.4, "perfunctory": 0.9, "sufficient": False, "user_text": "嗯"}, now=T0)
    aff1 = mg.get(CID)["affinity"]
    check(all(abs(aff1[d] - aff0[d]) < 1e-6 for d in aff0),
          "sufficient=False → 好感完全不动")
    check(mg.get(CID)["last_signals"].get("skipped") is True, "sufficient=False → 留痕 skipped=True")

    # 清晰信号默认 sufficient=True（缺省）→ 正常移动
    mg2 = RelationshipManager(base_dir=tmp / "dg2")
    t0 = float(mg2.get(CID)["affinity"]["trust"])
    mg2.on_turn(CID, {"valence": 0.7, "care": 1.0, "user_text": "你今天怎么样呀"}, now=T0)
    check(float(mg2.get(CID)["affinity"]["trust"]) > t0, "缺省 sufficient=True → 正向正常移动")

    # ambiguous（confidence<1）缩放负向惩罚：同样幅度，conf=0.5 跌幅 < conf=1.0
    mca = RelationshipManager(base_dir=tmp / "dca")
    mcb = RelationshipManager(base_dir=tmp / "dcb")
    stamp(mca, CID)
    stamp(mcb, CID)
    bca = sum(float(mca.get(CID)["affinity"][d]) for d in ("trust", "intimacy", "warmth"))
    mca.on_turn(CID, {"valence": -0.9, "harsh": 0.9, "confidence": 1.0, "user_text": "你个废物"}, now=T0 + 1)
    drop_clear = bca - sum(float(mca.get(CID)["affinity"][d]) for d in ("trust", "intimacy", "warmth"))
    bcb = sum(float(mcb.get(CID)["affinity"][d]) for d in ("trust", "intimacy", "warmth"))
    mcb.on_turn(CID, {"valence": -0.9, "harsh": 0.9, "confidence": 0.5, "user_text": "你个废物"}, now=T0 + 1)
    drop_ambig = bcb - sum(float(mcb.get(CID)["affinity"][d]) for d in ("trust", "intimacy", "warmth"))
    check(drop_clear > drop_ambig > 0, f"ambiguous 惩罚被缩放（clear={drop_clear:.2f} > ambig={drop_ambig:.2f}）")

    # ------------------------------------------------------------ 停机补算
    section("停机冻结 + 开机一次性补算")
    mgr6 = RelationshipManager(base_dir=tmp / "d6")
    s6 = mgr6.get(CID)
    s6.update({"turns": 20, "created_ts": T0 - 30 * 86400,
               "affinity": {"trust": 60.0, "intimacy": 60.0, "warmth": 60.0},
               "last_interaction_ts": T0 - 7 * 86400})
    s6["settings"]["pace"] = "标准"     # 8%/周
    mgr6.store.save(s6)
    s6b = mgr6.tick(CID, now=T0)
    w7 = float(s6b["affinity"]["warmth"])
    check(50.0 < w7 < 58.0, f"7 天不聊按周 8% 衰减（60 -> {w7:.1f}）")
    check(s6b["mood"]["label"] in ("想你", "蔫蔫的"), f"久未联系心情转『{s6b['mood']['label']}』")

    s7 = mgr6.get(CID)
    s7["affinity"]["warmth"] = 16.0
    s7["last_interaction_ts"] = T0 - 60 * 86400
    mgr6.store.save(s7)
    check(float(mgr6.tick(CID, now=T0)["affinity"]["warmth"]) >= 15.0, "长期不聊也不跌破留底")

    # 心情：被凶一句就该有反应
    mgr8 = RelationshipManager(base_dir=tmp / "d8")
    stamp(mgr8, CID)
    for i in range(7):
        mgr8.on_turn(CID, {"valence": 0.1, "user_text": f"热身{i}"}, now=T0 + i)
    mgr8.on_turn(CID, {"valence": -0.8, "harsh": 0.8, "user_text": "你就是个笑话"}, now=T0 + 200)
    check(mgr8.get(CID)["mood"]["label"] in ("闹别扭", "炸毛"),
          f"被凶 → 心情『{mgr8.get(CID)['mood']['label']}』")

    # ------------------------------------------------------------ 原型差异
    section("人格原型差异")
    ma = RelationshipManager(base_dir=tmp / "ca")
    mb = RelationshipManager(base_dir=tmp / "cb")
    ma.set_archetype(CID, "黏人")
    mb.set_archetype(CID, "傲娇")
    stamp(ma, CID)
    stamp(mb, CID)
    for i in range(8):
        ma.on_turn(CID, {"valence": 0.5, "user_text": f"A{i}"}, now=T0 + i)
        mb.on_turn(CID, {"valence": 0.5, "user_text": f"B{i}"}, now=T0 + i)
    aw = float(ma.get(CID)["affinity"]["warmth"])
    bw = float(mb.get(CID)["affinity"]["warmth"])
    check(aw > bw, f"黏人的热络涨得比傲娇快（{aw:.1f} > {bw:.1f}）")

    # ------------------------------------------------------------ 角色隔离
    section("多角色隔离")
    m9 = RelationshipManager(base_dir=tmp / "d9")
    m9.on_turn("danya", {"valence": 0.8, "care": 1.0, "user_text": "达妮娅你好"}, now=T0)
    m9.on_turn("other", {"valence": -0.5, "user_text": "别的角色"}, now=T0)
    check(float(m9.get("danya")["affinity"]["trust"]) != float(m9.get("other")["affinity"]["trust"]),
          "不同角色好感互不影响")
    check(m9.store.path_for("danya") != m9.store.path_for("other"), "不同角色不同文件")

    # ------------------------------------------------------------ 历史
    section("历史持久化")
    hs = HistoryStore(base_dir=tmp / "h")
    hs.append_turn(CID, "user", "你好呀", ts=T0)
    hs.append_turn(CID, "assistant", "嗯，来啦", ts=T0 + 1)
    check(hs.count(CID) == 2, "两轮写入成功")
    check(hs.append_turn(CID, "user", "   ") is None, "空内容被跳过")
    check(hs.append_turn(CID, "user", "x" * 2000) is None, "超长内容被跳过")
    recs = hs.load_recent(CID, 10)
    check(len(recs) == 2 and recs[0]["content"] == "你好呀", "读回顺序与内容正确")
    with hs.path_for(CID).open("a", encoding="utf-8") as f:
        f.write("{坏行\n")
    check(len(hs.load_recent(CID, 10)) == 2, "坏行被跳过、不影响其余记录")
    check(len(HistoryStore(base_dir=tmp / "h").load_recent(CID, 10)) == 2,
          "新实例（模拟重启）仍能读回历史")
    check(HistoryStore(base_dir=tmp / "h").count("other") == 0, "角色历史互相隔离")

    # ------------------------------------------------------------ 会话时钟
    section("在线时段 / 熬夜统计")
    sc = SessionClock(base_dir=tmp / "s")
    day = SessionClock._day_start(T0)   # 与模块一致：本地日界（不是 UTC 日界）
    tcur = day - 3600                         # 昨天 23:00
    sc.touch(ts=tcur, force_new=True)
    while tcur < day + 2 * 3600:              # 心跳到今晨 02:00（每 5 分钟）
        tcur += 300
        sc.touch(ts=tcur)
    sc.stop(ts=tcur)
    sm = sc.late_night_summary(now=day + 3 * 3600)
    check(sm["night_streak"] >= 1, f"识别到夜间活动（streak={sm['night_streak']}）")
    check(sm["online_minutes_today"] > 0, f"今日在线分钟={sm['online_minutes_today']}")

    sc2 = SessionClock(base_dir=tmp / "s2")
    t2 = day + 10 * 3600
    sc2.touch(ts=t2, force_new=True)
    while t2 < day + 11 * 3600:
        t2 += 300
        sc2.touch(ts=t2)
    sc2.stop(ts=t2)
    check(sc2.late_night_summary(now=day + 12 * 3600)["night_streak"] == 0, "白天活动不算熬夜")

    # ------------------------------------------------------------ 汇总
    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== {passed}/{total} 通过 =====")
    if passed != total:
        print("失败项：")
        for ok, name in RESULTS:
            if not ok:
                print("  - " + name)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
