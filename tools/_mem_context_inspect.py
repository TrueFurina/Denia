# -*- coding: utf-8 -*-
import sys, os, json, time
ROOT = r"G:\ai对话\11"
os.chdir(ROOT); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "_mem_context_inspect.txt")
L = []
def log(s=""):
    L.append(str(s))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

log("复核：记忆库落盘 & 共享上下文（进程退出后，排除读取过早）")
log("时间：" + time.strftime("%Y-%m-%d %H:%M:%S"))

from src.config import load_config
from src.companion.memory_store import MemoryStore
cfg = load_config(os.path.join(ROOT, "config.yaml"))
mem_cfg = getattr(cfg.ns, "memory", None)
mode = getattr(mem_cfg, "mode", "jarvis") if mem_cfg else "jarvis"
max_items = getattr(mem_cfg, "max_items", 100) if mem_cfg else 100
m = MemoryStore(max_items=max_items, mode=mode, embed_fn=None)
log(f"\n记忆库 count={m.count()}  类型={m.count_by_type()}")
facts = m.list_user_facts(limit=30)
log(f"list_user_facts（{len(facts)} 条）：")
for i, f in enumerate(facts, 1):
    log(f"  {i}. {f}")

for rel in ["data/context/danya.jsonl", "data/history/danya.jsonl", "data/chatlog/danya.jsonl"]:
    p = os.path.join(ROOT, *rel.split("/"))
    log(f"\n--- {rel} ---")
    if not os.path.exists(p):
        log("  （不存在）"); continue
    size = os.path.getsize(p); mt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
    rows = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except Exception: pass
    log(f"  大小={size}B  最后修改={mt}  行数={len(rows)}")
    log("  末 10 行：")
    for r in rows[-10:]:
        role = r.get("role", r.get("speaker", "?"))
        content = str(r.get("content", r.get("text", "")))[:100]
        log(f"    [{role}] {content}")
log("\n复核完成")
print("done")
