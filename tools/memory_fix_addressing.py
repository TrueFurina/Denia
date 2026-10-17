# -*- coding: utf-8 -*-
"""清理互斥的「称呼·用户」历史遗留条目：只保留最新一条，旧的标 invalid_at（不删、可追溯）。

背景：memory_store.add() 已有"同槽位新指令作废旧指令"机制，但本库里的两条冲突
称呼（鸡仔 / 漂泊者）是机制上线**之前**写入的历史数据，未被自动清理 —— 模型每次
召回都看到两条互相矛盾的"必须逐字遵守"指令，导致称呼摇摆、人设混乱。

用法：
    python tools/memory_fix_addressing.py            # 只看不留痕（dry-run）
    python tools/memory_fix_addressing.py --apply    # 实际写入（先自动备份）
"""
import json
import os
import shutil
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from src.companion.memory_store import _directive_slot  # noqa: E402

PATH = os.path.join("data", "memory.json")
APPLY = "--apply" in sys.argv


def main() -> int:
    raw = json.load(open(PATH, encoding="utf-8"))
    mems = raw.get("memories", [])
    print("库内条目总数:", len(mems))

    # 找出所有未作废的 称呼·用户 条目
    live = [m for m in mems
            if not m.get("invalid_at") and _directive_slot(m.get("text", "")) == "称呼·用户"]
    live.sort(key=lambda m: m.get("updated_at", 0))
    print("未作废的「称呼·用户」条目:", len(live))
    for m in live:
        print("   ts=%s | %s" % (m.get("updated_at"), (m.get("text") or "")[:70]))

    if len(live) <= 1:
        print("=> 无冲突，无需处理。")
        return 0

    keep = live[-1]          # 最新一条保留
    drop = live[:-1]         # 其余作废
    print("-" * 56)
    print("保留:", (keep.get("text") or "")[:70])
    for m in drop:
        print("作废:", (m.get("text") or "")[:70])

    if not APPLY:
        print("-" * 56)
        print("[dry-run] 未写入。加 --apply 实际执行。")
        return 0

    bak = PATH + ".bak_addressing_fix_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(PATH, bak)
    print("已备份:", bak)

    now = time.time()
    for m in drop:
        m["invalid_at"] = now
        m["superseded_by"] = keep.get("text", "")[:80]
        m["updated_at"] = now
    json.dump(raw, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写入。作废 %d 条，保留 1 条。" % len(drop))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
