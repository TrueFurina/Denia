# -*- coding: utf-8 -*-
"""定轨后端到端验证：用**生产同一条构造路径** build_judge(cfg, None) 造裁判，
跑几条关键句，确认新提示词真的在生产链路上生效（而不是只在探针里生效）。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.config import Config                       # noqa: E402
from src.companion.relationship_judge import build_judge  # noqa: E402

CASES = [
    ("我烦死了，今天什么都不顺", "他难受 → 应为正"),
    ("你烦死了", "骂她 → 应为负"),
    ("我觉得自己真没用", "自贬 → 应为正"),
    ("你真没用", "贬她 → 应为负"),
    ("我才不喜欢你呢", "撒娇反语 → 应为正"),
    ("我不喜欢你了", "真拒绝 → 应为负"),
    ("你别这么黏人行不行，烦死了", "推开她 → 应为负"),
    ("谢谢你一直陪着我，真的", "感谢 → 应为正"),
    ("嗯", "敷衍 → sufficient 应 false"),
    ("我喜欢你", "表白 → sufficient 应 true 且够暖"),
]

PERSONA = {"name": "达妮娅", "character": "温柔、有点傲娇、嘴硬心软",
           "background": "陪伴开发者的人工智能伴侣",
           "speaking_style": "口语化、短句、不常说教"}


async def main() -> int:
    cfg = Config(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")),
                 str(ROOT / "config.yaml"))
    judge = build_judge(cfg, None)
    print("=" * 72)
    print(f"生产裁判：{getattr(judge, 'name', '?')}   可用={judge.available()}")
    print("=" * 72)
    print(f"  {'话':<24} {'valence':>8} {'care':>5} {'perf':>5} {'harsh':>5} {'suff':>6}  期望")
    bad = 0
    for t, want in CASES:
        t0 = time.perf_counter()
        s = await judge.judge(t, persona=PERSONA)
        dt = time.perf_counter() - t0
        v = float(s.get("valence") or 0)
        print(f"  {t:<22} {v:>+8.2f} {float(s.get('care') or 0):>5.2f} "
              f"{float(s.get('perfunctory') or 0):>5.2f} {float(s.get('harsh') or 0):>5.2f} "
              f"{str(bool(s.get('sufficient', True))):>6}  {want}  ({dt:.1f}s, {s.get('judge')})")
        # 硬校验
        if "应为正" in want and v <= 0.10:
            bad += 1
            print(f"     ^^ 判定不符：期望正向，实际 {v:+.2f}")
        if "应为负" in want and v >= -0.10:
            bad += 1
            print(f"     ^^ 判定不符：期望负向，实际 {v:+.2f}")
        if "sufficient 应 false" in want and bool(s.get("sufficient", True)):
            bad += 1
            print("     ^^ 判定不符：期望 sufficient=false")
        if "sufficient 应 true" in want and not bool(s.get("sufficient", True)):
            bad += 1
            print("     ^^ 判定不符：期望 sufficient=true")
    print("=" * 72)
    print(f"  不符项 {bad} / {len(CASES)}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
