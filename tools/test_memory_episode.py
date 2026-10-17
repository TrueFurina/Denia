# -*- coding: utf-8 -*-
"""记忆库升级（episode 情节记忆）专项验证（2026-10-11）。

覆盖：
  1) MEMORY_TYPES 含 episode；MEMORY_MAX_LEN 有 episode
  2) episode 可入库、可按类型召回、memory_text 有独立分组
  3) _trim 保护 episode（不被当成弱条目删）
  4) episode 与 user 不互相合并（类型不同）
  5) 旧类型行为不变（directive/user/world 分组仍正常）
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.companion.memory_store import (MemoryStore, MEMORY_TYPES, MEMORY_MAX_LEN,
                                       MEMORY_EPISODE_PROMPT, _parse_fact_items)

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


def main():
    print("== 1. 类型定义 ==")
    check("MEMORY_TYPES 含 episode", "episode" in MEMORY_TYPES, MEMORY_TYPES)
    check("MEMORY_MAX_LEN 有 episode=200", MEMORY_MAX_LEN.get("episode") == 200)
    check("episode prompt 含 {text} 占位", "{text}" in MEMORY_EPISODE_PROMPT)

    td = tempfile.mkdtemp(prefix="mem_ep_")
    store = MemoryStore(path=str(Path(td)/"m.json"))
    store.set_character("danya")

    print("\n== 2. episode 入库 / 召回 / 分组 ==")
    ok = store.add("10月10日晚，他跟我聊到国庆出去用尼康Z52拍了照片，说等会发给我看，语气挺高兴。",
                   type="episode", source="chat")
    check("episode 入库成功", ok is True)
    store.add("用户喜欢摄影", type="user", source="user")
    store.add("用户希望叫他漂泊者", type="directive", source="user")
    txt = store.memory_text(10)
    check("memory_text 含『你们一起经历过的事』分组", "你们一起经历过的事" in txt, txt[:200])
    check("memory_text 仍含『关于用户的记忆』", "关于用户的记忆" in txt)
    check("memory_text 仍含『必须逐字遵守』", "必须逐字遵守" in txt)
    check("episode 正文可被注入", "尼康Z52" in txt)

    print("\n== 3. _trim 保护 episode ==")
    # 制造超限：max_items 设很小，塞满弱 user + 1 条 episode
    s2 = MemoryStore(path=str(Path(tempfile.mkdtemp(prefix="mem_trim_"))/"m.json"))
    s2.set_character("danya")
    s2._max_items = 3
    s2.add("重要的情节记忆：他答应发照片给我看", type="episode", source="chat")
    for i in range(8):
        s2.add("用户随口说的弱事实%d" % i, type="user", source="user")
    s2._trim()
    types = [m.get("type") for m in s2._memories]
    check("_trim 后 episode 仍在", "episode" in types, types)
    check("_trim 后总数 ≤ max_items", len(s2._memories) <= 3, len(s2._memories))

    print("\n== 4. episode 不被合并进 user ==")
    s3 = MemoryStore(path=str(Path(tempfile.mkdtemp(prefix="mem_mix_"))/"m.json"))
    s3.set_character("danya")
    s3.add("用户喜欢摄影", type="user", source="user")
    s3.add("用户喜欢摄影这件事他今天又提了", type="episode", source="chat")
    tps = sorted(set(m.get("type") for m in s3._memories))
    check("user 与 episode 分属不同类型", "user" in tps and "episode" in tps, tps)

    print("\n== 5. _parse_fact_items 容错 ==")
    items = _parse_fact_items('[{"fact":"他今天聊了摄影","type":"episode"}]')
    check("解析 fact/type 成功", items == [("他今天聊了摄影", "episode")], items)
    items2 = _parse_fact_items('[]')
    check("空数组 → 空列表", items2 == [], items2)

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
