"""关系裁判·真模型冒烟：用项目自己的聊天 LLM 判一批典型语句，并与规则裁判对比。

跑法（会真的调用一次聊天模型，产生少量 token 费用）：
    python tools\\judge_smoke.py
只读 config，不写任何文件、不启动 GUI。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config                                  # noqa: E402
from src.llm import create_llm                                      # noqa: E402
from src.companion.relationship_judge import LLMJudge, heuristic_signals  # noqa: E402

CASES = [
    ("表白",   "我喜欢你"),
    ("表白",   "我爱你"),
    ("思念",   "我想你了，今天一整天都在想你"),
    ("关心",   "娅娅你为什么要去死，我明明能救你的"),
    ("踩雷",   "哈哈你为啥要自杀啊，还挺悲壮的"),
    ("敷衍",   "嗯"),
    ("敷衍",   "哦"),
    ("中性",   "今天天气不错"),
    ("事务",   "帮我写个函数"),
    ("挽回",   "对不起，我错了，别生气了好不好"),
    ("骂人",   "你个废物，滚"),
    ("关心",   "你都熬三个通宵了，今晚早点睡行不行"),
]


def persona_of(cfg) -> dict:
    ch = cfg.get_character_by_id(cfg.active_character_id) or cfg.get_active_character()
    if ch is None:
        return {}
    return {"name": getattr(ch, "name", ""), "character": getattr(ch, "character", ""),
            "background": getattr(ch, "background", ""),
            "speaking_style": getattr(ch, "speaking_style", "")}


async def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    print("[smoke] 加载 config…", flush=True)
    cfg = load_config(ROOT / "config.yaml")
    print("[smoke] 构造聊天模型…", flush=True)
    llm = create_llm(cfg)
    if not getattr(llm, "available", False):
        print("[smoke] 聊天模型不可用（未配 Key？）——无法做真模型对比", flush=True)
        return 1
    p = persona_of(cfg)
    print(f"[smoke] 模型={type(llm).__name__} 角色={p.get('name')}（人设 {len(str(p.get('background','')))} 字）\n")

    judge = LLMJudge(llm, timeout_s=20.0)
    print(f'{"类型":<4} {"输入":<26} {"规则val":>8} {"LLM val":>8} {"care":>5} {"perf":>5} {"rep":>5} {"harsh":>5} {"耗时":>6}')
    print("-" * 96)
    bad = 0
    for kind, text in CASES:
        r = heuristic_signals(text)
        t0 = time.time()
        try:
            s = await judge.judge(text, persona=p)
            ms = int((time.time() - t0) * 1000)
            print(f'{kind:<4} {text:<26} {r["valence"]:>8.3f} {s["valence"]:>8.2f} '
                  f'{s["care"]:>5.2f} {s["perfunctory"]:>5.2f} {s["repair"]:>5.2f} {s["harsh"]:>5.2f} {ms:>5}ms'
                  + (f'  | {s["reason"]}' if s.get("reason") else ""))
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f'{kind:<4} {text:<26} {r["valence"]:>8.3f} {"FAIL":>8}  {type(e).__name__}: {e}')
    print(f"\n[smoke] 失败 {bad}/{len(CASES)}；裁判调用 {judge.calls} 次（LLM），耗时合计见上")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
