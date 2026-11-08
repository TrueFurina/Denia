# -*- coding: utf-8 -*-
"""验证关系裁判修复：求确认式追问不再被误判为负向。"""
import os
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.getcwd())

from src.companion.relationship_judge import _JUDGE_SYSTEM, heuristic_signals  # noqa: E402

print("=" * 60)
print("A. LLM 裁判提示词是否已含「求确认≠不信任」规则")
print("=" * 60)
# 【2026-11-08】这里原本逐条打印 [PASS]/[FAIL]，然后整条脚本始终 exit 0 ——
# 它登记在 GATES 的「裁判确认条款」一项里，于是三条探针全 [FAIL] 也显示 ✓。
# 现在把结论收进 ok，末尾按它退出。
ok = True
for probe in ["求确认式的追问，不是\"不信任\"", "撒娇式试探", "别演了"]:
    hit = probe in _JUDGE_SYSTEM
    ok = ok and hit
    print(("  [PASS] " if hit else "  [FAIL] ") + probe)

print()
print("=" * 60)
print("B. 规则裁判(零模型)对这几句的真实判定（真值，非推测）")
print("=" * 60)
for t in ["我喜欢你", "你喜欢我吗", "你真的喜欢我吗", "你是不是哄我的", "别演了，你就是程序"]:
    s = heuristic_signals(t)
    print("  %-16s valence=%+.2f suff=%-5s perf=%.2f harsh=%.2f" % (
        t, s["valence"], str(s["sufficient"]), s["perfunctory"], s["harsh"]))

print()
print("说明：规则裁判是零模型兜底，只保证「表白/求确认」不被判成敷衍；")
print("     语义级判断（不信任 vs 撒娇）由 LLM 裁判按 A 段新增规则决定。")

print()
print("RESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
