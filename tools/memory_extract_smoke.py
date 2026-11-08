"""记忆提炼·真模型冒烟（2026-10-11）

目的：用**真实聊天模型**验证"让 LLM 自己对整段上下文做记忆提取"到底有没有效果。
不启动 GUI、不写任何记忆文件（纯只读 + 打印），只产生少量 token 费用。

跑法：
    python tools\\memory_extract_smoke.py
    python tools\\memory_extract_smoke.py --long     # 用更长的上下文压一压
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

# ── 模拟一段"像真人聊天"的长上下文：混入平淡事件 + 细节 + 约定 + 偏好 ──
#   刻意包含用户实测的那句"我晚上七点吃的饭"（平淡、无关键词），
#   用来验证：新方案（整段提炼）能不能把它记住，而旧的单句白名单方案记不住。
CONVO_NORMAL = [
    ("user", "在吗"),
    ("assistant", "在的呀，怎么啦"),
    ("user", "刚忙完，累死了"),
    ("assistant", "辛苦啦，先歇会儿嘛～"),
    ("user", "我晚上七点吃的饭"),
    ("assistant", "七点啊，那会儿饿坏了吧，吃的啥呀"),
    ("user", "楼下那家兰州拉面，加了个蛋"),
    ("assistant", "拉面配蛋，这搭配稳的。那家店好吃吗"),
    ("user", "还行，就是有点咸，下次让他少放盐"),
    ("assistant", "记住啦，下次提醒他少放盐哈哈"),
    ("user", "对了下周我打算去趟西安"),
    ("assistant", "西安！去玩吗，还是出差"),
    ("user", "玩，想去吃吃那个羊肉泡馍，听说回民街的不错"),
    ("assistant", "哦哦回民街，那必须安排上。几号去呀"),
    ("user", "周三晚上的高铁，请了两天假"),
    ("assistant", "周三晚上…那我到时候提醒你收行李"),
    ("user", "好啊，帮我记着，我老忘东西"),
    ("assistant", "包在我身上～你之前是不是也老忘带充电器"),
    ("user", "哈哈是的，上次出门忘了带，手机差点关机"),
    ("assistant", "那我给你的清单里加上充电器"),
    ("user", "嗯嗯，你真细心"),
    ("assistant", "那是～"),
]

# 超长版：把日常琐事堆起来，模拟"上下文快超限"的真实状态
_FILLER = [
    ("user", "今天中午吃的盖浇饭"),
    ("assistant", "盖浇饭好吃不"),
    ("user", "就那样吧，食堂的"),
    ("assistant", "食堂嘛，能吃就行"),
    ("user", "下午开了个会，开了好久"),
    ("assistant", "会开得长最磨人了"),
    ("user", "是啊，坐得腰疼"),
    ("assistant", "起来活动活动嘛"),
    ("user", "我们组新来了个同事"),
    ("assistant", "新同事怎么样"),
    ("user", "挺年轻的，话不多"),
    ("assistant", "新人一般都这样，熟了就好"),
]
CONVO_LONG = CONVO_NORMAL + (_FILLER * 3) + [
    ("user", "啊对了我养的那只猫最近老是抓沙发"),
    ("assistant", "哈哈猫抓沙发是通病，给它买个猫抓板试试"),
    ("user", "买了它不玩，就爱抓沙发"),
    ("assistant", "那…要不把沙发角包起来？"),
    ("user", "再说吧，先忍忍"),
]

# ── 新方案提示词：把整段上下文交给 LLM，让它自己判断+提取 ──
NEW_EXTRACT_PROMPT = """你是一个长期记忆提炼器。下面是你（AI 伴侣）与用户**最近一段对话的完整记录**。

请你通读整段对话，像"真的在相处"一样判断：**其中哪些内容值得你长期记住**，并提取出来。

【要记住的】（这些才是能让你"记得他这个人"的东西）：
- 用户明确告诉你的**事实与经历**：他吃了什么、去了哪、做了什么（哪怕说得平淡）
- 用户的**偏好/习惯/口味**（"那家有点咸""不爱吃辣""常忘带充电器"）
- 用户的**计划与约定**（"下周去西安""周三晚高铁""帮我记着收行李"）
- 用户的**身份/处境**（工作、住在哪、养了什么）
- 用户的**情绪与态度**（对某事挺期待、有点烦）
- 你答应过他的事（"那我提醒你收行李"）

【不要记的】：
- 纯寒暄（"在吗""嗯嗯"）、你（AI）自己的话、一次性的寒暄语气
- 但**注意**：看起来平淡的"我晚上七点吃的饭"**是值得记的**——它告诉你他的作息和吃了啥，
  不是废话。**宁可多记一条事实，也别漏掉他主动讲的事。**

【每条要求】
- 以"用户"为主语，**保留细节**（时间/地点/人物/数量/具体名词，用他原话里的词，不要编造）
- 每条 ≤ 40 字
- 分类：`fact`（事实/经历/偏好）或 `plan`（计划/约定）

只输出一行 JSON 数组，每项 {"fact": "...", "type": "fact|plan"}；没有则输出 []。
不要任何解释、不要 markdown。

对话记录：
{text}"""


async def run_case(llm, name: str, convo: list) -> None:
    seg = []
    for who, txt in convo:
        label = "用户" if who == "user" else "达妮娅"
        seg.append(f"{label}：{txt}")
    text = "\n".join(seg)
    # 用**生产提示词**（src/companion/memory_synthesis.py:MEMORY_SYNTHESIS_PROMPT），
    # 不在这里留副本——否则提示词改了这里不知道，冒烟测的是"假的"。
    from src.companion.memory_synthesis import MEMORY_SYNTHESIS_PROMPT
    prompt = MEMORY_SYNTHESIS_PROMPT.replace("{text}", text)
    print(f"\n{'='*72}")
    print(f"【{name}】上下文 {len(convo)} 轮 / {len(text)} 字")
    print(f"{'='*72}")
    t0 = time.time()
    try:
        raw = await asyncio.wait_for(
            llm.chat([{"role": "user", "content": prompt}], options={"temperature": 0.1}),
            timeout=60.0,
        )
    except Exception as e:  # noqa: BLE001
        print("  调用失败:", type(e).__name__, e)
        return
    ms = int((time.time() - t0) * 1000)
    print(f"  耗时 {ms}ms，LLM 原始输出：")
    print("  " + (raw or "(空)").strip().replace("\n", "\n  "))

    # 解析：必须用**生产解析器**（MemorySynthesizer._parse），它做了 fact/plan→user 归一；
    # 不能用 _parse_fact_items（它会把未知 type 猜成 world，正是本次修掉的 bug）。
    from src.companion.memory_synthesis import MemorySynthesizer
    items = MemorySynthesizer._parse(raw)
    print(f"\n  解析出 {len(items)} 条（生产解析器·已归类）：")
    for f, t in items:
        print(f"    · [{t}] {f}")
    # 关键：有没有记住"晚上七点吃的饭"
    hit = any(("七点" in f or "7点" in f or "拉面" in f or "兰州" in f) for f, _ in items)
    print(f"\n  ★ 是否记住『晚上七点吃（兰州拉面）』：{'✅ 是' if hit else '❌ 否'}")
    # 分类体检：不应出现 world（个人信息被错分的外部事实桶）
    n_world = sum(1 for _, t in items if t == "world")
    print(f"  ★ 误分到 world 的条数：{n_world}（应为 0，除非真提了外部知识）")


async def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    print("[smoke] 加载 config…")
    cfg = load_config(ROOT / "config.yaml")
    llm = create_llm(cfg)
    if not getattr(llm, "available", False):
        print("[smoke] 聊天模型不可用（未配 Key？）")
        return 1
    print(f"[smoke] 模型={type(llm).__name__}")
    await run_case(llm, "常规对话（含'我晚上七点吃的饭'）", CONVO_NORMAL)
    # 【2026-11-08】原来写的是 `if "--long" in args or True:` —— `or True` 让条件恒真，
    # 那个开关从来没起过作用（作者注释自己写着"默认也跑长版"）。既然永远要跑，就直说。
    await run_case(llm, "超长上下文（模拟快超限）", CONVO_LONG)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
