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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.relationship import RelationshipManager  # noqa: E402
from companion.relationship import RelationshipEngine as _V1Engine  # noqa: E402
from companion.relationship_judge import heuristic_signals  # noqa: E402
from companion.history_store import HistoryStore  # noqa: E402
from companion.session_clock import SessionClock  # noqa: E402

RESULTS: list[tuple[bool, str]] = []
# 判据条数总账：少跑一条就红（见汇总处注释）。
EXPECTED_CHECKS = 73
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

    # ============================================================ 2026-11-10 补
    # 【第 44 轮变异探针】上面 41 条判据全绿，探针下去捅出 **21 处零判据**。
    # 下面每一段都对应一处"刀落下去门还绿"，段首写明它逼住的是哪一行生产代码。
    section("去重：隔离测量（原来的判据被饱和度满足了）")
    # 原判据 `(a2-a1) < (a1-a0)*0.7` 绿的原因不是去重生效：intimacy 越接近 100，
    # 单轮增益本身就越小（`delta *= 1 - aff/100`），第二次增益天然更小 —— 把
    # DEDUP_FACTOR 改成 1.0（去重彻底失效）照样满足那个不等式。
    # 所以改成**只差一个变量**的对照：两次测量唯一的差别是第二句是否与第一句重复。
    def _gain_second(second_text: str, tag: str) -> float:
        m = RelationshipManager(base_dir=tmp / ("dd_" + tag))
        stamp(m, CID)
        m.on_turn(CID, {"valence": 0.5, "user_text": "在吗在吗在吗"}, now=T0)
        b = float(m.get(CID)["affinity"]["intimacy"])
        m.on_turn(CID, {"valence": 0.5, "user_text": second_text}, now=T0 + 60)
        return float(m.get(CID)["affinity"]["intimacy"]) - b

    g_rep = _gain_second("在吗在吗在吗", "rep")
    g_new = _gain_second("今天下班路上看见一只很胖的橘猫", "new")
    check(g_rep < g_new * 0.6,
          f"重复内容增益打折（重复 {g_rep:.3f} < 新内容 {g_new:.3f} × 0.6）")

    section("软每日上限：隔离测量（原来的判据被饱和度满足了）")
    # 同上：原判据比较"首3轮最大"与"末3轮最大"，而那 12 轮本身就让 trust 从 50 涨到
    # 接近 100 —— 末 3 轮增益小是**饱和**造成的，跟每日上限无关。把上限整段拆掉
    # （`return 1.0`）照样绿。改成把 daily_gain.sum 预置成两种值，其余完全相同。
    def _cap_gain(pre_sum: float, tag: str) -> float:
        m = RelationshipManager(base_dir=tmp / ("dc_" + tag))
        stamp(m, CID)
        st = m.get(CID)
        st["daily_gain"] = {"date": time.strftime("%Y-%m-%d", time.localtime(T0)),
                            "sum": pre_sum}
        m.store.save(st)
        b = float(m.get(CID)["affinity"]["trust"])
        m.on_turn(CID, {"valence": 0.5, "care": 0.5, "user_text": f"新话题{tag}"},
                  now=T0 + 30)
        return float(m.get(CID)["affinity"]["trust"]) - b

    g_low = _cap_gain(0.0, "low")
    g_high = _cap_gain(99.0, "high")
    check(g_high < g_low * 0.4,
          f"当日已满额 → 增益被压（满额 {g_high:.3f} vs 未满 {g_low:.3f}）")

    section("旧档兼容 / 文件名净化 / 未知原型")
    # 旧档补字段：老版本写下的状态文件缺一堆字段，读的时候必须补全而不是 KeyError。
    d_old = tmp / "old"
    (d_old / "affinity").mkdir(parents=True, exist_ok=True)
    (d_old / "affinity" / "old.json").write_text(
        json.dumps({"char_id": "old", "affinity": {"trust": 60}}), encoding="utf-8")
    st_old = RelationshipManager(base_dir=d_old).get("old")
    check(st_old.get("affinity", {}).get("trust") == 60.0, "旧档已有的值原样保留")
    check(set(st_old.get("affinity") or {}) == {"trust", "intimacy", "warmth"},
          "旧档缺的好感维度被补全（不 KeyError）")
    check((st_old.get("mood") or {}).get("label") == "平静"
          and (st_old.get("settings") or {}).get("pace") == "标准",
          "旧档缺的顶层字段被补全（mood / settings）")
    # 文件名净化：char_id 是用户可写的（角色名），带 ../ 就能写到 data/ 外面去。
    p_evil = mgr.store.path_for("../../evil")
    check(p_evil.parent == mgr.store.base and ".." not in p_evil.name,
          "_safe_id 挡住路径穿越（../../evil 只落在 affinity/ 里）")
    check(mgr.store.path_for("a/b") == mgr.store.path_for("a_b"),
          "非法字符被换成下划线（不会多出一层目录）")
    # 未知原型：原型名是 UI/配置传进来的，写错不许把整段行为带偏。
    mgr.set_archetype(CID, "根本不存在的原型")
    check(mgr.get(CID)["archetype"] == "温柔", "未知原型回落默认『温柔』（不是照收）")

    section("心情：最近三句最负 / 72h 空窗 / 雀跃 / 想你门槛")
    # ① 被凶一句就该有反应，不能被"最近 6 句的加权均值"稀释。
    m13 = RelationshipManager(base_dir=tmp / "m13")
    stamp(m13, CID)
    for i in range(5):
        m13.on_turn(CID, {"valence": 0.5, "user_text": f"开心话{i}"}, now=T0 + 60 * i)
    m13.on_turn(CID, {"valence": -0.3, "user_text": "随便你吧"}, now=T0 + 400)
    check(m13.get(CID)["mood"]["label"] in ("闹别扭", "炸毛", "委屈"),
          f"前面都聊得好、最后一句冷下来 → 心情『{m13.get(CID)['mood']['label']}』"
          f"（均值仍为正，只有『最近三句最负』能判出来）")
    # ② 72 小时空窗是"想你/蔫蔫的"的分界线：亲密度低 → 蔫蔫的。
    m14 = RelationshipManager(base_dir=tmp / "m14")
    st14 = m14.get(CID)
    st14["affinity"]["intimacy"] = 30.0
    st14["last_interaction_ts"] = T0 - 100 * 3600
    st14["recent_valence"] = []
    check(m14.engine.derive_mood(st14, now=T0)["label"] == "蔫蔫的",
          "空窗 100 小时 + 亲密度低 → 蔫蔫的（72h 分支，不是 20h 分支）")
    st14b = dict(st14)
    st14b["affinity"] = dict(st14["affinity"])
    st14b["affinity"]["intimacy"] = 60.0
    st14b["last_interaction_ts"] = T0 - 200 * 3600
    md14b = m14.engine.derive_mood(st14b, now=T0)
    check(md14b["label"] == "想你" and int(md14b["intensity"]) == 3,
          f"空窗 200 小时 + 亲密度够 → 想你·明显（实际 {md14b['label']}/{md14b['intensity']}）")
    st14c = dict(st14)
    st14c["affinity"] = dict(st14["affinity"])
    st14c["affinity"]["intimacy"] = 60.0
    st14c["last_interaction_ts"] = T0 - 100 * 3600
    check(m14.engine.derive_mood(st14c, now=T0)["label"] == "想你",
          "同样 100 小时，亲密度够 → 想你（45 分门槛）")
    # ③ 强正向一轮 → 不会还是"平静"（雀跃分支）。
    m15 = RelationshipManager(base_dir=tmp / "m15")
    stamp(m15, CID)
    m15.on_turn(CID, {"valence": 0.95, "care": 1.0, "depth": 0.8, "effort": 0.9,
                      "user_text": "我今天特别想谢谢你陪着我"}, now=T0 + 60)
    check(m15.get(CID)["mood"]["label"] in ("雀跃", "柔软", "心动", "感动", "安心"),
          f"强正向一轮 → 心情『{m15.get(CID)['mood']['label']}』（不是平静）")
    # v1 的雀跃分支本身：v2 的 _refine 会用语义信号把标签改写成"感动/心动"，
    # 所以「雀跃」这个分支只能直接打在 v1 上 —— 它是 _refine 的兜底 base_label。
    st15 = m15.get(CID)
    st15["recent_valence"] = [0.9]
    st15["last_interaction_ts"] = T0 + 30
    md15 = _V1Engine(m15.store).derive_mood(st15, now=T0 + 60)
    check(md15["label"] == "雀跃",
          f"v1 心情：强正向 → 雀跃（实际『{md15['label']}』，v2 的细分以它为兜底）")

    section("历史：截断 / 空行 / 角色字段")
    hs2 = HistoryStore(base_dir=tmp / "h2")
    for i in range(5):
        hs2.append_turn(CID, "user", f"第{i}句", ts=T0 + i)
    recs2 = hs2.load_recent(CID, 2)
    check(len(recs2) == 2 and recs2[-1]["content"] == "第4句",
          f"load_recent(2) 只给最近 2 条（实际 {len(recs2)} 条）")
    n_before = hs2.count(CID)
    with hs2.path_for(CID).open("a", encoding="utf-8") as f:
        f.write("\n   \n")
    check(hs2.count(CID) == n_before, f"空行不计入条数（{n_before} → {hs2.count(CID)}）")
    check(hs2.append_turn(CID, "narrator", "旁白不算对话")["role"] == "user",
          "非法 role 归为 user（不会写出一条谁都读不懂的记录）")

    section("会话时钟：空会话 / 断档切分 / 夜间窗口 / 在线分钟")
    sc3 = SessionClock(base_dir=tmp / "s3")
    sc3.touch(ts=T0, force_new=True)
    sc3.stop(ts=T0)
    check(len(sc3._read_log()) == 0, "零长度会话不写日志（不刷空记录）")
    sc4 = SessionClock(base_dir=tmp / "s4")
    sc4.touch(ts=T0, force_new=True)
    sc4.touch(ts=T0 + 300)              # 先让这段会话有 5 分钟长度
    sc4.touch(ts=T0 + 300 + 20 * 60)    # 空档 20 分钟 > 15 分钟阈值
    check(len(sc4._read_log()) == 1, "空档超过 15 分钟 → 旧会话收尾")
    check(float((sc4._read_current() or {}).get("start") or 0) == T0 + 300 + 20 * 60,
          "断档后那次心跳开的是新会话")
    check(sc._in_night(day + 2 * 3600) is True, "02:00 属于夜间窗口")
    check(sc._in_night(day + 7 * 3600) is False, "07:00 不属于夜间窗口（上界是 5 点）")
    check(abs(sm["online_minutes_today"] - 120.0) < 1.0,
          f"今日在线分钟按『分钟』算（实际 {sm['online_minutes_today']}，应为 120 左右）")

    section("规则裁判：否定语境 / 两字敷衍")
    # "我不喜欢你"是负向，不能因为含"喜欢"就判成表白。
    neg = heuristic_signals("我不喜欢你")
    check(float(neg.get("valence") or 0.0) < 0.3,
          f"'我不喜欢你' 不算表白（valence={neg.get('valence')}）")
    check(float(heuristic_signals("别喜欢我了").get("valence") or 0.0) < 0.3,
          "'别喜欢我了' 不算表白")
    # 两字冷句（不在 _PERFUNCTORY 里）也要判成敷衍：perf 必须 >= 0.5 才算信息不足。
    pf = heuristic_signals("算了")
    check(float(pf.get("perfunctory") or 0.0) >= 0.5,
          f"两字冷句判为敷衍（perf={pf.get('perfunctory')}）")
    check(pf.get("sufficient") is False, "'算了' 信息不足 → sufficient=False")

    section("v2 引擎：阶段迟滞 / 疤自愈 / 交情沉淀 / 情绪降温")
    m16 = RelationshipManager(base_dir=tmp / "v1")
    st16 = m16.get(CID)
    st16["stage"] = "萍水"
    st16["affinity"] = {"trust": 53.0, "intimacy": 53.0, "warmth": 53.0}
    check(m16.engine._stage(st16) == "萍水",
          "升档要够稳：53 分刚过 52 的门槛，但不跳档（迟滞 +2）")
    st16b = dict(st16)
    st16b["affinity"] = {"trust": 60.0, "intimacy": 60.0, "warmth": 60.0}
    check(m16.engine._stage(st16b) == "熟络", "60 分稳稳超过门槛 → 升到熟络")
    # 疤随日子自愈：20 天前留的疤不该一辈子记着。
    m17 = RelationshipManager(base_dir=tmp / "v2")
    st17 = m17.get(CID)
    stamp(m17, CID)
    st17 = m17.get(CID)
    st17["scar"] = 0.8
    st17["last_interaction_ts"] = T0 - 10 * 86400
    m17.store.save(st17)
    scar_after = float(m17.tick(CID, now=T0).get("scar") or 0.0)
    check(scar_after < 0.8, f"疤会随时间自愈（0.8 → {scar_after:.3f}）")
    # 交情越久，衰减越慢。
    def _decay_warmth(bond_days: int, tag: str) -> float:
        m = RelationshipManager(base_dir=tmp / ("bd_" + tag))
        stamp(m, CID)
        st = m.get(CID)
        st["affinity"] = {"trust": 70.0, "intimacy": 70.0, "warmth": 70.0}
        st["bond"] = {"days": bond_days, "last_date": ""}
        st["last_interaction_ts"] = T0 - 7 * 86400
        st["settings"]["pace"] = "标准"
        m.store.save(st)
        return float(m.tick(CID, now=T0)["affinity"]["warmth"])

    w_new = _decay_warmth(0, "new")
    w_old = _decay_warmth(60, "old")
    check(w_old > w_new, f"交情越久衰减越慢（老交情 {w_old:.1f} > 新关系 {w_new:.1f}）")
    # 情绪会自然降温：48 小时前的气头，现在不该还是炸毛。
    m18 = RelationshipManager(base_dir=tmp / "v3")
    st18 = m18.get(CID)
    stamp(m18, CID)
    st18 = m18.get(CID)
    st18["recent_valence"] = [-0.8]
    st18["mood"] = {"label": "炸毛", "intensity": 3, "since": T0 - 100 * 3600}
    st18["last_interaction_ts"] = T0 - 100 * 3600
    mood18 = m18.engine.derive_mood(st18, now=T0)
    check(mood18["label"] != "炸毛",
          f"气头会自然降温（100 小时后是『{mood18['label']}』，不是一直炸毛）")

    # ------------------------------------------------------------ 汇总
    # 【2026-11-10 条数总账】`passed == total` 只证明"跑过的都过了"，不证明"该跑的都跑了"：
    # 某段判据被 try/except 吞掉、或被人顺手删掉，总数跟着变小，报告照样是 `N/N 通过`。
    # 少跑的判据不算通过 —— 所以把期望条数也钉住。
    if len(RESULTS) != EXPECTED_CHECKS:
        check(False, f"判据条数 {len(RESULTS)} != 期望 {EXPECTED_CHECKS}"
                     f"（有整段被跳过/删掉了 —— 少跑的判据不算通过）")
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
