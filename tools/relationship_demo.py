"""好感度系统 P0 —— 回放 demo（给你实测用）。

不启动软件、不联网、不调模型：用脚本模拟一段"聊天时间线"，
把每天的三维好感 + 心情打印出来，让你直观看到"像不像人"。

跑法：
    C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tools\\relationship_demo.py

想看别的剧本：改下面 SCRIPT 即可。
数据落在临时目录，不碰软件真实 data/。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.relationship import RelationshipManager, DIM_LABEL  # noqa: E402

DAY = 86400.0
BASE = 1_760_000_000.0  # 固定起点，只用于演示

# 剧本：("day", 说明, signals)
#   day    = 第几天（0 起）
#   signals 见 relationship.apply_turn（valence/care/perfunctory/repair/harsh）
SCRIPT = [
    (0, "聊得很开心",        {"valence": 0.8, "care": 0.7, "user_text": "今天遇到件好玩的事跟你说"}),
    (0, "继续聊",            {"valence": 0.6, "care": 0.4, "user_text": "你觉得呢哈哈"}),
    (1, "普通聊天",          {"valence": 0.2, "user_text": "今天有点忙"}),
    (1, "普通聊天",          {"valence": 0.1, "user_text": "晚上再说吧"}),
    (2, "敷衍",              {"valence": -0.1, "perfunctory": 0.9, "user_text": "嗯"}),
    (2, "敷衍",              {"valence": -0.2, "perfunctory": 0.9, "user_text": "哦"}),
    (3, "（沉默一天）",       None),
    (4, "（沉默）",           None),
    (5, "（沉默）",           None),
    (6, "（沉默）",           None),
    (7, "（沉默一周了）",     None),
    (8, "回来了，还是敷衍",   {"valence": -0.2, "perfunctory": 0.8, "user_text": "在吗"}),
    (8, "道歉 + 安慰她",      {"valence": 0.5, "repair": 1.0, "care": 0.8,
                              "user_text": "对不起呀，这几天太忙了，别生气了，我在"}),
    (9, "继续陪她",          {"valence": 0.7, "care": 0.6, "user_text": "今天陪你多聊会儿"}),
]


def run(archetype: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix=f"reldemo_{archetype}_"))
    mgr = RelationshipManager(base_dir=tmp)
    cid = "demo"
    mgr.set_archetype(cid, archetype)
    st = mgr.get(cid)
    st["turns"] = 10                      # 跳过新手期，便于观察成人行为
    st["created_ts"] = BASE - 10 * DAY
    st["affinity"] = {"trust": 58.0, "intimacy": 55.0, "warmth": 48.0}
    st["last_interaction_ts"] = BASE - 2 * 3600
    mgr.store.save(st)

    print(f"\n{'=' * 72}")
    print(f"人格原型：{archetype}   （三维好感 + 心情，随剧本变化）")
    print(f"{'=' * 72}")
    print(f"{'第几天':<6}{'发生了什么':<22}{'信任':>6}{'亲密':>6}{'热络':>6}  心情")
    print("-" * 72)

    last_day = -1
    for day, note, sig in SCRIPT:
        now = BASE + day * DAY + 20 * 3600
        if sig is None:
            mgr.tick(cid, now=now)        # 沉默日：只补算衰减
        else:
            mgr.on_turn(cid, sig, now=now)
        s = mgr.get(cid)
        aff = s["affinity"]
        mood = s["mood"]
        star = " " if day == last_day else ""
        print(f"{star}D{day:<4}{note:<22}{aff['trust']:>6.1f}{aff['intimacy']:>6.1f}"
              f"{aff['warmth']:>6.1f}  {mood['label']}"
              f"{'（强度%d）' % mood['intensity'] if mood['intensity'] > 1 else ''}")
        last_day = day

    s = mgr.get(cid)
    card = mgr.engine.describe_mood(s)
    print("-" * 72)
    print(f"当前阶段：{s['stage']}")
    print(f"情感卡：{card['atmosphere']}  →  {card['tip']}")
    print(f"（数据目录：{tmp}）")


def main() -> int:
    print("\n好感度系统 P0 回放 demo —— 纯本地、不调模型")
    for arch in ("黏人", "傲娇"):
        run(arch)
    print("\n提示：把 SCRIPT 里的剧本改一改，就能看不同情境下的心情走向。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
