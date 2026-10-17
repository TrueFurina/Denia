# -*- coding: utf-8 -*-
"""agent_pro 包测（真实环境）：索引质量 / 启动验证 / find_file / state_snapshot。"""
import sys
import time

sys.path.insert(0, r"G:\ai对话\11")
from src.agent_pro import app_index as ai
from src.agent_pro import launch as lp
from src.agent_pro import config as cfg

print("== [1] 构建真实应用索引 ==")
t0 = time.time()
apps = ai.build_index()
print(f"来源记录（去重后）: {len(apps)} 个，耗时 {time.time()-t0:.1f}s")
from collections import Counter
print("来源分布:", dict(Counter(r["source"] for r in apps)))

print("\n== [2] 匹配质量（通用性验证：无硬编码映射）==")
targets = ["汽水音乐", "抖音", "微信", "记事本", "网易云音乐", "QQ音乐", "计算器", "B站", "搜狗输入法", "不存在的软件xyz"]
for t in targets:
    hits = ai.match(t, apps)
    if hits:
        b, s = hits[0]
        print(f"  {t:<8} → {b['name']:<16} [{b['source']}/{b['token']['type']}] {s:.0f}分  {b['token']['value'][:60]}")
    else:
        print(f"  {t:<8} → 无匹配（如实拒绝）")

print("\n== [3] 缓存 TTL 生效性 ==")
t0 = time.time()
apps2 = ai.load_index(ttl_h=24.0)
print(f"load_index（应走缓存，毫秒级）: {len(apps2)} 个，耗时 {(time.time()-t0)*1000:.0f}ms")

print("\n== [4] 带验证启动（真实通道） ==")
# 先确保汽水音乐没在跑
import subprocess
subprocess.run(["taskkill", "/F", "/IM", "SodaMusic.exe"], capture_output=True)
time.sleep(1.5)
flags = dict(cfg.DEFAULTS, verify_timeout_s=15.0)
t0 = time.time()
r = lp.index_launch("汽水音乐", flags=flags)
print(f"index_launch('汽水音乐') {time.time()-t0:.1f}s →")
print("  ", r)

print("\n== [5] find_file（真实桌面） ==")
from src.agent_pro.wait import find_file
import os
desk = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
f = dict(cfg.DEFAULTS)
r = find_file({"pattern": "*.png", "folder": desk}, f)
print(r[:500])

print("\n== [6] es.exe 探测 ==")
from src.agent_pro.wait import _find_es
es_path = _find_es(f)
print("es.exe:", es_path or "(未安装，find_file 将走受限遍历)")
