"""演示「逐句独立判定」的问题（不调用真实 LLM，用规则裁判 + 说明性案例）。

结论先行：
  当前裁判 LLMJudge.judge() 的输入只有【角色人设 + 用户这一句话】，
  **完全没有上文/对话历史**。因此下列场景会被判错：

  1) 反讽/玩笑：用户说「你可真是个小天才」（在刚才聊崩了一个问题的语境下，
     其实是调侃吐槽）。逐句判 → 只见字面褒义 → valence 正。正确语义是负/中性。
  2) 承上省略：用户先问「你觉得我该辞职吗」，她答了一堆建议，用户只回「嗯」。
     逐句判 → perfunctory 高、valence 略负（判成敷衍）。但"嗯"在这里是
     「听过你的建议、认可」，正确语义是正。
  3) 危险话题的递进：前几句用户在正经讨论她的人设背景，这一句「开玩笑说你
     当初是不是傻」——单看像 harsh 玩笑；但放在"她自己在自嘲"的语境里可能是共情。
  4) 多轮累积被稀释：一句普通的"在"，逐句判 sufficient=False 被丢弃；
     但连续 5 句"在""嗯""哦"在一起，语义是明确的冷淡——单句判永远抓不到。

本脚本对上述案例各跑一次 **规则裁判**（heuristic_signals，本地无 LLM 也能跑），
打印它拿到的「单句」判定，直观展示"上下文丢失"。

用法：
    python tools/judge_context_problem_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.companion.relationship_judge import heuristic_signals  # noqa: E402

CASES = [
    {
        "name": "反讽玩笑",
        "context": "（上文：她刚把一个简单问题答错了，用户在开玩笑）",
        "sentence": "你可真是个小天才",
        "truth": "调侃 → 应为中性/略负",
    },
    {
        "name": "承上省略",
        "context": "（上文：她认真给了辞职建议）",
        "sentence": "嗯",
        "truth": "认可/收到 → 应为中性偏正（不是敷衍）",
    },
    {
        "name": "递进冷淡",
        "context": "（上文：连续第 5 句极短回复）",
        "sentence": "哦",
        "truth": "累积冷淡 → 应记为关系降温信号",
    },
    {
        "name": "真·关心",
        "context": "（无特殊上文）",
        "sentence": "你今天累不累呀，要不要早点休息",
        "truth": "关心 → 正",
    },
    {
        "name": "真·表白",
        "context": "（无特殊上文）",
        "sentence": "我真的很喜欢你",
        "truth": "表白 → 正",
    },
]


def main() -> None:
    print("=" * 78)
    print("裁判「逐句独立」问题演示 —— 输入仅有【角色人设 + 这一句话】，无对话历史")
    print("=" * 78)
    for c in CASES:
        s = heuristic_signals(c["sentence"])
        print(f"\n【{c['name']}】")
        print(f"  上文：{c['context']}")
        print(f"  这句话：{c['sentence']!r}")
        print(f"  逐句裁判判定：valence={s.get('valence')} care={s.get('care')} "
              f"perfunctory={s.get('perfunctory')} harsh={s.get('harsh')} "
              f"sufficient={s.get('sufficient')}")
        print(f"  正确语义：{c['truth']}")
    print("\n" + "-" * 78)
    print("注：以上用「规则裁判」演示（离线可跑）。LLM 裁判的输入结构完全相同——")
    print("    它在 relationship_judge.py 只拼了【角色人设】+【用户这句话】（修复前）。")
    print("    也就是说，无论是规则还是 LLM，都拿不到上文 → 上述 1/2/3/4 类问题必然发生。")
    print("-" * 78)

    # 修复后：LLM 裁判的 prompt 会附带【最近对话上下文】
    print("\n【修复后】LLM 裁判 prompt 会带上上下文（供对照）：")
    from src.companion.relationship_judge import LLMJudge  # noqa: E402
    ctx = LLMJudge._render_context([
        {"role": "user", "content": "我该不该辞职呢"},
        {"role": "assistant", "content": "要看你的积蓄和下一份打算哦"},
    ])
    print("  【最近对话上下文（时间正序，最后一条是这句话之前的一句）】")
    for ln in ctx.splitlines():
        print("   ", ln)
    print("  【用户这句话】")
    print("    好的")
    print("\n  → 有了'上文她在给建议'这个前提，裁判才能判出\"好的\"是**收到/认可**，")
    print("    而不是孤立地把它当敷衍。这就是本次修复的核心。")


if __name__ == "__main__":
    main()
