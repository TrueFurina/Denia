# -*- coding: utf-8 -*-
"""关系系统 v2 —— **当前生产实现**（2026-10-13 起草，已定轨）。

⚠️ 状态：已定轨（非实验）。本模块**已被生产接线**，并非孤立 A/B 探针：
  · `companion/relationship.py:918` 实例化 `RelationshipEngineV2(self.store)` 作为
    当前关系引擎（生产默认走 v2，v1 仅作父类保留语义）；
  · `companion/relationship.py:997` 引 `relation_block_text_v2()` 生成喂给提示词的关系块文案。
`tools/relationship_v2_ab_probe.py` 仍可调本模块做对照，但那只是可选探针，不改变"v2 已上线"的事实。

针对 `docs/_relationship_realism_probe.txt` 实测出的 5 条"不像人"，逐条改：

  ① 情绪种类太少（现在 6 种）        → 扩到 14 种，且新增「心动/安心/委屈/失落/不安/
                                        吃醋/柔软/困倦」这些真人常见的中间态。
  ② 情绪几乎只由"你发不发话"决定      → 引入**情绪自然降温**（mood 有自己的半衰期）：
                                        气头会自己消，久置的愤怒会转成失落/想念，
                                        而不是永远停在"炸毛"等下一句话来覆盖。
  ③ 交情没有沉淀（深关系反而掉更多）  → 新增 `bond`（累计有互动的天数）：衰减速率随
                                        交情时长下调，做到"交情越久越经得起放"。
  ④ 信任没有裂痕（道歉 95% 复原）    → 新增 `scar`（裂痕）：伤人的话留疤，一次道歉
                                        只消除一部分，信任的恢复被疤压制，剩下靠时间慢慢愈合。
  ⑤ 好感→说话方式只有 4 档、UI 与提示词不同源
                                      → 关系块并入 8 档细进度 + 「她愿意说到哪一步」的
                                        自我暴露梯度；UI 与提示词共用同一个 label。

红线不变：数值绝不上提示词、可挽回（反 PUA）、新手期保护、里程碑单向闸门。
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Optional

from . import relationship as R
from .relationship import (
    DIMENSIONS, DIM_FLOOR, DIM_MAX, PACE_WEEKLY_PCT, DEFAULT_PACE,
    archetype_params, _clamp, _now,
    STAGE_CUTS,          # 阶段阈值：迟滞逻辑要用，直接引生产常量避免两处漂移
)

# ══════════════════════════════════════════════════════════ ① 情绪表（14 种）
MOODS_V2 = (
    # 负向
    "炸毛", "闹别扭", "委屈", "蔫蔫的", "失落", "不安", "吃醋",
    # 正向
    "雀跃", "心动", "安心", "柔软", "感动", "哭笑不得",
    # 对他
    "心疼",
    # 中性 / 状态
    "想你", "困倦", "平静",
)

MOOD_PROMPT_V2 = {
    "炸毛":   "她在气头上，语气会冲一点，先别顶，也别讨好。",
    "闹别扭": "她嘴上不说其实有点不高兴，哄一句比讲道理管用。",
    "委屈":   "她觉得自己被冤枉了，想要的是「你信她」，不是安慰话术。",
    "蔫蔫的": "她提不起劲、话也少，陪陪她，别急着找话题。",
    "失落":   "她有点被冷落的感觉，语气淡淡的，别假装没事。",
    "不安":   "她拿不准你还在不在意她，会试探、会绕着问，别敷衍过去。",
    "吃醋":   "她有点酸，会拐弯抹角地提，别装没听出来，也别火上浇油。",
    "雀跃":   "她今天心情很好，话可以多点、活泼点。",
    "心动":   "她有点心动，会有点慌、话到嘴边绕一圈，别一下子说太满。",
    "安心":   "她觉得被照顾着，语气可以软下来、慢下来。",
    "柔软":   "现在是她比较柔软的时刻，可以说点真心话，不用端着。",
    "感动":   "她有点被你戳到了，会说得慢一点、真一点，别用俏皮话岔开。",
    "哭笑不得": "她被你逗得有点无奈又有点想笑，接得住玩笑，也会轻轻回你一句。",
    "心疼":   "他现在不太好，她是心疼的：先接住他，别急着说自己也别讲道理。",
    "想你":   "她有点想你，只是没说出口，跟她说句话她会很开心。",
    "困倦":   "她困了，话会变少变慢，别拉着她聊长篇。",
    "平静":   "她安安静静，不急不躁，随便聊聊就好。",
}

MOOD_TEXT_V2 = {
    "炸毛":   ("她在气头上。", "先别顶，等会儿再聊。"),
    "闹别扭": ("她嘴上不说，其实有点不高兴。", "哄一句，比讲道理管用。"),
    "委屈":   ("她觉得自己被冤枉了。", "别说教，先站在她那边。"),
    "蔫蔫的": ("她提不太起劲，话也少。", "陪陪她，不用急着找话题。"),
    "失落":   ("她有点被冷落的感觉。", "主动说点什么，别让她一个人待着。"),
    "不安":   ("她拿不准你还在不在意她。", "给她一个确定的回应。"),
    "吃醋":   ("她有点酸。", "别装没听出来，说清楚就好。"),
    "雀跃":   ("她今天心情很好，话会比平时多一点。", "顺着她聊两句，她会更高兴。"),
    "心动":   ("她有点心动，自己也有点慌。", "别逼她说，慢慢来。"),
    "安心":   ("她觉得被照顾着。", "就这样陪着就好。"),
    "柔软":   ("现在是她比较柔软的时刻。", "说点真心话，她接得住。"),
    "感动":   ("她有点被你戳到了。", "别说俏皮话岔开，认真接住。"),
    "哭笑不得": ("她被你弄得有点无奈，又有点想笑。", "玩笑她接得住，轻轻回一句就好。"),
    "心疼":   ("他现在不太好，她有点心疼。", "先接住他，别急着讲道理。"),
    "想你":   ("她有点想你，只是没说出口。", "跟她说句话吧，她会很开心。"),
    "困倦":   ("她困了。", "别拉着她聊太久。"),
    "平静":   ("她安安静静地待着，不急不躁。", "随便聊聊就好。"),
}

# ② 情绪自然降温：每种心情的"半衰期"（小时）。None = 不自然消退（平静）。
#    真人规律：气来得快去得也快（几小时），失落/想念拖得久（几天）。
MOOD_HALFLIFE_H = {
    "炸毛": 5.0, "闹别扭": 8.0, "委屈": 10.0, "蔫蔫的": 14.0, "失落": 12.0,
    "不安": 10.0, "吃醋": 4.0, "雀跃": 4.0, "心动": 6.0, "安心": 6.0,
    "柔软": 4.0, "感动": 3.0, "哭笑不得": 2.0, "心疼": 6.0,
    "想你": 96.0, "困倦": 3.0, "平静": None,
}
# 低于该残留量就认为"这股劲儿过去了"，落回基底心情
COOL_DONE = 0.35

# ③ 交情沉淀：bond.days 达到这么多天后，衰减速率减半（再久不再继续减）
BOND_FULL_DAYS = 60.0
BOND_MAX_CUT = 0.50

# ④ 裂痕
SCAR_PER_HARSH = 0.35      # 一次重伤害留多少疤（harsh=1 → +0.35）
SCAR_REPAIR_RATE = 0.30    # 一次满分道歉消掉多少比例的疤（不是清零，故要连哄好几天）
SCAR_TRUST_PENALTY = 0.70  # 疤对"信任恢复"的压制强度（最慢）
SCAR_INTIMACY_PENALTY = 0.55  # 疤对"亲密恢复"的压制强度（次之）
SCAR_WARMTH_PENALTY = 0.25  # 疤对"热络（气氛）"的压制（最轻：气氛最容易回暖，但不能满血）
SCAR_HEAL_PER_DAY = 0.05   # 每天自愈多少（约 20 天完全愈合）

# ④b 「道歉不该倒赚」：把伤人的**实际掉分**记成一笔债，之后的恢复总额有上限。
#     真人规律：道歉能挽回大部分，但超不回本；剩下的要靠后来的日子慢慢补。
REPAIR_RECOVER_RATIO = 0.85  # 一次裂痕最多能靠"哄"挽回多少比例（剩下的靠时间）
DEBT_HEAL_PER_DAY = 0.06     # 债务随时间自然减免（约 17 天免完）

# ④c 阶段迟滞：升档要"够稳"，降档要"跌够多"，避免一句话就让阶段横跳。
#     真人规律：关系档位不会因为一句话就变，得持续一阵子才算数。
STAGE_HYST_UP = 2.0     # 升档：分数需超过该档阈值 + 这么多
STAGE_HYST_DOWN = 4.0   # 降档：分数需跌破当前档阈值 - 这么多（降比升难）

_CUT_OF: dict[str, float] = {name: float(cut) for cut, name in STAGE_CUTS}

# ④d 「连续冷淡累积」：被冷落是"量"的问题。单句敷衍裁判可能把 perf 标高而不算冷淡，
#     但连续多句没温度，真人会明显觉得被冷落 ⇒ 引擎层自己数（不依赖裁判跨句推理）。
#     只在"无温度句"连续出现时才惩罚，且每句扣得极轻，靠累积显形。
COLD_STREAK_TRIGGER = 3       # 连续多少句"无温度"才开始累积惩罚
COLD_STREAK_PENALTY = 0.25    # 每超一句，warmth/intimacy 各额外扣这么点（轻）

# ④e 「表白后嘴硬 = 害羞」跨句修复：上一句刚强正向（表白/夸赞），本句是嘴硬否定词，
#     且本句不是真伤人（harsh 低）⇒ 真人懂这是害羞，不是真拒绝。小模型单句裁判会把
#     valence 误判负向，这里用 last_signals 的上一句语义 + 本句嘴硬词补回来。
_BASHFUL_RE = re.compile(
    r"(才不是|才不呢|才不|才没有|才没|想得美|才怪|口是心非|骗你的|骗你啦|逗你的|"
    r"开玩笑的|才不喜欢|才没喜欢|假装|嘴硬|讨厌鬼|鬼才|才没有呢|哪有|才怪呢|才没呢)")

# ①c 语义字段的第二条来源：用 v2 **规则**裁判从原话里补类别。
#     生产 LLM 裁判（F/H/I 提示词）**不返回 kind 也不返回语义字段**（实测：解析器
#     只保留标准 8 字段），所以 v2 心情细分拿不到输入 ⇒ 只能落到「care 高 = 安心」。
#     这里让两者分工：LLM 负责**打分**（valence/care/harsh 等照旧用它），
#     规则负责**分类别**（vulnerable/affection/...），各取所长。
SEMANTIC_FIELDS = ("affection", "compliment", "gratitude", "vulnerable",
                   "share", "tease", "need", "jealous", "farewell", "reject")


def semantic_from_text(sig: dict) -> dict:
    """用 v2 规则裁判补语义类别字段；**只补类别，不动 LLM 的打分**。"""
    txt = str(sig.get("user_text") or "")
    if not txt:
        return sig
    try:
        from .relationship_judge_v2 import heuristic_signals_v2
        s2 = heuristic_signals_v2(txt)
    except Exception:  # noqa: BLE001 实验模块缺失时不致命，退化成原样
        return sig
    out = dict(sig)
    for k in SEMANTIC_FIELDS:
        try:
            v = float(s2.get(k, 0.0) or 0.0)
            cur = float(out.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            # 这一维的值不是数字 → 跳过该维（其余维照常累计）
            continue
        if v > cur:
            out[k] = v
    if not out.get("kind") and s2.get("kind"):
        out["kind"] = str(s2.get("kind"))
    return out

_STAGE_ORDER: dict[str, int] = {"萍水": 0, "熟络": 1, "交心": 2, "离不开": 3}

# ①b kind 归一化：生产裁判（F/H 提示词）只输出 `kind` + 标准 8 字段，
#     不会输出 v2 语义字段（vulnerable/compliment/...）。这里把 kind 反推成
#     语义字段，**仅在原字段缺失或为 0 时补**，避免覆盖裁判的真实打分。
#     值取"该类典型中位"，不给 1.0：否则所有 harsh 都会变"炸毛"（过重）。
_KIND_SOFT: dict[str, tuple[str, float]] = {
    "affection":   ("affection", 0.85),
    "compliment":  ("compliment", 0.80),
    "gratitude":   ("gratitude", 0.80),
    "vulnerable":  ("vulnerable", 0.80),
    "share":       ("share", 0.80),
    "tease":       ("tease", 0.80),
    "need":        ("need", 0.80),
    "jealous":     ("jealous", 0.80),
    "farewell":    ("farewell", 0.80),
    "reject":      ("reject", 0.80),
    "harsh":       ("harsh", 0.55),      # 中档：对应"闹别扭"，避免一律炸毛
    "repair":      ("repair", 0.60),
    "care":        ("care", 0.70),
}


def normalize_kind(sig: dict) -> dict:
    """把裁判输出的 kind 反推成 v2 语义字段（只补空缺，不覆盖已有值）。"""
    out = dict(sig or {})
    kind = str(out.get("kind") or "").strip().lower()
    if not kind:
        return out
    for k, (field, val) in _KIND_SOFT.items():
        if kind == k:
            try:
                cur = float(out.get(field, 0.0) or 0.0)
            except (TypeError, ValueError):
                cur = 0.0
            if cur <= 0.0:
                out[field] = val
            break
    return out

_JEALOUS_RE = re.compile(r"(别的女生|别的女孩|其他女生|其他女孩|前女友|前任|别人家的|小姐姐)")
_LOVE_RE = re.compile(r"(我喜欢你|我爱你|喜欢上你|爱上你|在一起|做我女朋友)")


def _score(st: dict) -> float:
    a = st.get("affinity") or {}
    return (0.4 * float(a.get("trust", 0.0))
            + 0.4 * float(a.get("intimacy", 0.0))
            + 0.2 * float(a.get("warmth", 0.0)))


def _tone_directive(st: dict) -> str:
    """把好感度总分翻成「语气分寸」执行性指令，让温差真正落到说话口气上。

    关系块原本只给「描述性」状态（刚认识/老熟人），小模型不会自动翻成语气。
    这里直接给「该用什么口气说话」的硬指令（参见 2026-10-13 真人感测评 v3/v4）。
    """
    s = _score(st)
    if s >= 68.0:
        return ("语气可以撒娇、黏人、直说想他，毫无保留，像老恋人：能自然说"
                "“想你/喜欢你”、能用肉麻昵称、能直球表达在乎；他没亲近你也不扭捏。"
                "【他若先说“想你/喜欢你”，你大大方方甜蜜接住、自然回过去，不扭捏。】")
    if s >= 58.0:
        return ("语气可以亲昵，偶尔用昵称，话能稍长，比普通朋友更近一层："
                "“想你了”能自然说出口，但别太频繁太黏。"
                "【他若先说“想你/喜欢你”，你可以温柔接住、自然回一句，不必每次都回。】")
    if s >= 38.0:
        return ("语气自然熟络、可以打趣互怼，但别太黏太肉麻、保持分寸："
                "不主动说“想你/喜欢你”、不用肉麻昵称，像处得来的熟人。"
                "【他若先说“想你/喜欢你”，你绝不要机械回“我也想你/我也喜欢你”——"
                "用打趣或轻松口吻接住（比如“傻不傻”“突然说这个”），不硬对等回应。】")
    return ("语气客气、有分寸，像刚认识：不自来熟、不主动撒娇、不直说"
            "“想你/喜欢你”、不直呼昵称、句子偏短；他没主动亲近前，你保持一点距离感。"
            "【他若先说“想你/喜欢你”，你绝不回“我也想你/我也喜欢你”——"
            "礼貌温暖地接住就好，不反射式镜像、不 reciprocate，保持距离。】")


def _bond_days(st: dict) -> int:
    return int((st.get("bond") or {}).get("days", 0) or 0)


def _bonding_factor(st: dict) -> float:
    """③ 交情沉淀系数：交情越久，衰减越慢（最低降到 0.5 倍）。"""
    return 1.0 - BOND_MAX_CUT * min(1.0, _bond_days(st) / BOND_FULL_DAYS)


def _scar(st: dict) -> float:
    return _clamp(float(st.get("scar", 0.0) or 0.0), 0.0, 1.0)


def _bump_bond_days(st: dict, t: float) -> None:
    """记一个"有互动的日子"（同一天只记一次）。"""
    d = time.strftime("%Y-%m-%d", time.localtime(t))
    bd = st.setdefault("bond", {"days": 0, "last_date": ""})
    if str(bd.get("last_date") or "") != d:
        bd["days"] = int(bd.get("days", 0) or 0) + 1
        bd["last_date"] = d


def _base_mood(st: dict, t: float, gap_h: float) -> tuple[str, int]:
    """这股劲儿过去之后，落回的基底心情（由关系现状与空窗决定）。"""
    if gap_h >= 72.0:
        inti = float((st.get("affinity") or {}).get("intimacy", 0.0))
        return ("想你", 3) if inti >= 45 else ("失落", 2)
    if gap_h >= 20.0:
        return "想你", 1 if gap_h < 48 else 2
    if _gap_hours(st, t) >= 12.0:
        return "失落", 1
    if _scar(st) >= 0.25:
        return "委屈", 1
    return "平静", 1


def _gap_hours(st: dict, t: float) -> float:
    last = float(st.get("last_interaction_ts") or 0.0)
    return (t - last) / 3600.0 if last else 0.0


def _refine(st: dict, base_label: str, t: float, gap_h: float) -> str:
    """① 细分心情：优先用 v2 裁判的语义信号（kind/脆弱/感谢/调侃…），
    读不到就退回 v1 的老字段近似（保证只换裁判也能跑）。"""
    sig = (st.get("last_signals") or {}).get("v2") or {}
    if not sig:
        sig = st.get("last_signals") or {}
    txt = str(sig.get("user_text") or "")
    hour = time.localtime(t).tm_hour

    def g(key: str) -> float:
        return float(sig.get(key, 0.0) or 0.0)

    h = g("harsh")
    # 0) 他自己在难受 → 她是心疼。**优先于轻/中度 harsh**：裁判有时会给
    #    "我烦死了" 一点 harsh（语气冲），但真人听到他难受就是心疼，不是生气。
    if g("vulnerable") >= 0.5 and h < 0.8:
        return "心疼"
    # 0b) 连续被冷落 → 失落（B6 的心情体现：不是一句敷衍，而是"你一直不温不火"）。
    #     仅在无强正向/无脆弱、且无伤人时成立，避免覆盖真正该有的情绪。
    cold = int(st.get("cold_streak") or 0)
    if cold >= COLD_STREAK_TRIGGER and h < 0.3 \
            and not (g("affection") >= 0.5 or g("vulnerable") >= 0.5):
        return "失落"
    # 1) 伤人分三级：重=炸毛，中=闹别扭，轻=委屈（真人被轻怼是"有点委屈"不是发火）
    if h >= 0.8:
        return "炸毛"
    if h >= 0.5:
        return "闹别扭"
    if h >= 0.3:
        return "委屈"
    # 1b) 被拒绝/推开 → 失落（不是生气）
    if g("reject") >= 0.5:
        return "失落"
    # 3) 吃醋 / 求安全感 / 感谢 / 调侃
    if g("jealous") >= 0.5:
        return "吃醋"
    if g("need") >= 0.5:
        return "心动" if _score(st) >= 45 else "不安"
    if g("gratitude") >= 0.5:
        return "感动"
    if g("tease") >= 0.5:
        return "哭笑不得"
    # 4) 表白 / 道歉 / 关心 / 深聊
    if g("affection") >= 0.5:
        return "心动"
    if g("repair") >= 0.5 and _scar(st) >= 0.30:
        return "委屈"          # 正在被哄，气消了一点，但还没完全过去
    if g("repair") >= 0.5:
        return "柔软"
    if g("care") >= 0.5 and g("valence") > 0.2:
        return "安心"
    if g("share") >= 0.5 or (g("depth") >= 0.6 and g("valence") > 0.2):
        return "柔软"          # 深聊/倾诉时刻：真人会变得柔软
    if g("compliment") >= 0.5:
        return "心动" if _score(st) >= 45 else "雀跃"
    if g("farewell") >= 0.5:
        return "失落"          # 他要走了，会有点舍不得
    # 5) 兜底：老逻辑（正则 + v1 标签）
    if _JEALOUS_RE.search(txt) and base_label in ("平静", "闹别扭", "蔫蔫的"):
        return "吃醋"
    if _LOVE_RE.search(txt) and base_label in ("雀跃", "平静"):
        return "心动"
    if base_label == "炸毛":
        return "炸毛"
    if base_label == "闹别扭" and _scar(st) >= 0.25:
        return "委屈"
    if base_label == "平静" and gap_h >= 20.0:
        return "想你"
    if 0 <= hour < 5 and base_label in ("平静", "雀跃", "安心", "柔软"):
        return "困倦"
    if base_label == "雀跃" and g("depth") >= 0.6:
        return "柔软"
    return base_label


# ══════════════════════════════════════════════════════════ ⑤ 关系块（细档 + 分寸）
def progress_label_v2(st: dict) -> str:
    s = _score(st)
    if s >= 88.0:
        return "形影不离"
    if s >= 78.0:
        return "已经离不开你"
    if s >= 68.0:
        return "真的很在乎你"
    if s >= 58.0:
        return "越来越亲近"
    if s >= 48.0:
        return "处得挺舒服"
    if s >= 38.0:
        return "慢慢熟悉中"
    if s >= 28.0:
        return "刚认识"
    return "还很生分"


def disclosure_line(st: dict) -> str:
    """她愿意说到哪一步（自我暴露梯度）——好感高低的体感差异主要来自这里。"""
    s = _score(st)
    if s >= 80.0:
        return "她会把依赖和独占欲说出口，也敢跟你聊以后"
    if s >= 66.0:
        return "她愿意说自己的不安、过去、真正在意的事"
    if s >= 52.0:
        return "她会跟你聊日常和喜好，偶尔抱怨两句"
    return "你们还不算熟，她保持着分寸，不会主动讲自己的私事"


# 【2026-11-08 墓碑】这里曾有一份 `relation_block_text_v2()` 的旧实现（37 行），被本文件
# 后半段的同名定义**静默覆盖**——Python 在 import 时把模块级同名函数重绑到后一个，
# 所以这 37 行从写下那天起就没执行过。改这里不会有任何效果，正是最坑人的那种死代码。
#
# 为什么后一个更好（删前逐行比对过）：
#   · 旧版 `tier != "cloud"` 时把「你们的关系」整行砍掉（lines[2] 被剔除），而本地双工 /
#     主动搭话恰恰是最需要知道两人到哪一步的场景；
#   · 旧版底线句按 3 类心情分（坏 / 好 / 困），17 种心情共用 3 句；新版 `BOTTOM_BY_MOOD`
#     给每种心情一句自己的底线；
#   · 旧版 `MOOD_PROMPT_V2[label]` 是硬索引，新版 `.get(label, '')` 兜底。
#
# ⚠️ 一处需要人定夺的差异（本 PR 不替维护者决定）：旧版有一行
#   `f"- 语气分寸：{_tone_directive(st)}"`，**新版没有这一行**。
# 也就是说 `_tone_directive()`（本文件前段，25 行「把好感度总分翻成语气硬指令」）
# 现在是**孤儿**——全文件只剩定义、没有任何调用者。两种可能：
#   (a) 改写时无意丢的 → 应把该行加回新版（否则「语气分寸」这个特性静默消失）；
#   (b) 有意控 token 删的 → 应把 `_tone_directive()` 一并删掉。
# 判据 `tools/check_duplicate_def.py` 只钉「同名定义静默覆盖」，不判断该留哪一行。


# ══════════════════════════════════════════════════════════ 引擎 v2
class RelationshipEngineV2(R.RelationshipEngine):
    """在 v1 引擎之上加：情绪降温 / 情绪细分 / 交情沉淀 / 信任裂痕。

    分数主逻辑仍走父类（保证与线上一致、不改既有语义），v2 只做**附加修正**：
      · catch_up：疤自愈 + 衰减乘沉淀系数
      · apply_turn：疤记账 + 信任修复打折 + 交情天数 + 用 v2 心情覆盖
      · derive_mood：细分 + 自然降温
    """

    # ---- ② 心情：细分 + 自然降温 ----
    def derive_mood(self, state: dict, now: Optional[float] = None,
                    gap_h: Optional[float] = None) -> dict:
        t = _now() if now is None else now
        if gap_h is None:
            gap_h = _gap_hours(state, t)
        v1 = super().derive_mood(state, t, gap_h)       # v1：由最近几句话判定
        base_label = str(v1.get("label") or "平静")
        label = _refine(state, base_label, t, gap_h)

        prev = state.get("mood") or {}
        prev_label = str(prev.get("label") or "平静")

        # 关键：**只冷却"没有被新事件刷新"的旧情绪**。
        # 本轮判出了跟上一轮不同的心情 ⇒ 说明刚发生了新的事，这股劲儿是新的，
        # 不该被"上一情绪已经持续了多久"扑灭（否则一句关心也会被判成平静）。
        if label != prev_label:
            return {"label": label, "intensity": int(v1.get("intensity") or 2)}

        since = float(prev.get("since") or t)
        elapsed_h = max(0.0, (t - since) / 3600.0)
        hl = MOOD_HALFLIFE_H.get(label)
        if hl is None:
            return {"label": label, "intensity": int(v1.get("intensity") or 1)}
        remain = 0.5 ** (elapsed_h / hl)
        if remain < COOL_DONE:
            # 这股劲儿已经过去 → 落回基底（气消了会变成失落/想念，不会一直炸毛）
            label, intensity = _base_mood(state, t, gap_h)
        else:
            intensity = max(1, min(3, int(math.ceil(3 * remain))))
        return {"label": label, "intensity": intensity}

    # ---- ③ 衰减：交情沉淀 ----
    def catch_up(self, state: dict, now: Optional[float] = None) -> dict:
        t = _now() if now is None else now
        last = float(state.get("last_interaction_ts") or 0.0) or float(state.get("updated_at") or t)
        days = max(0.0, (t - last) / 86400.0)

        if days > 0.0:                                   # ④ 疤随日子自愈
            state["scar"] = round(max(0.0, _scar(state) - SCAR_HEAL_PER_DAY * days), 4)

        if days <= 0.0:
            self._set_mood(state, self.derive_mood(state, t), t)
            return state
        if self._is_newbie(state, t):
            self._set_mood(state, self.derive_mood(state, t), t)
            return state

        p = archetype_params(state)
        pace = str((state.get("settings") or {}).get("pace") or DEFAULT_PACE)
        weekly = PACE_WEEKLY_PCT.get(pace, PACE_WEEKLY_PCT[DEFAULT_PACE])
        per_day = weekly / 7.0 / 100.0
        bonding = _bonding_factor(state)                 # ③ 交情越久掉得越慢
        for d in DIMENSIONS:
            cur = float(state["affinity"][d])
            mult = float(p["decay"].get(d, 1.0))
            nxt = cur - cur * per_day * days * mult * bonding
            state["affinity"][d] = _clamp(nxt, DIM_FLOOR[d], DIM_MAX)
        state["stage"] = self._stage(state)
        self._set_mood(state, self.derive_mood(state, t), t)
        return state

    # ---- 变量开关（供 A/B 对照逐个开关，默认全开）----
    kind_norm_on = True      # ①b kind→语义字段归一化
    semantic_from = "text"   # ①c "off" / "text"：控制变量已证明 text（规则补语义类别）是必需项
    hysteresis_on = True     # ④c 阶段迟滞
    repair_debt_on = True    # ④b 道歉债务（不倒赚）
    bashful_ctx_on = True    # ④e 跨句：表白后嘴硬=害羞（引擎层补语义，治单句误判负向）
    cold_streak_on = True    # ④d 跨句：连续冷淡累积被冷落感（引擎层自计数）

    # ---- ④c 阶段迟滞 ----
    def _stage(self, state: dict) -> str:
        if not self.hysteresis_on:
            return R.RelationshipEngine._stage(state)
        cur = str(state.get("stage") or "萍水")
        raw = R.RelationshipEngine._stage(state)
        o_cur = _STAGE_ORDER.get(cur, 0)
        o_raw = _STAGE_ORDER.get(raw, 0)
        if o_raw == o_cur:
            return raw
        s = _score(state)
        if o_raw > o_cur:                      # 升档：要稳稳超过阈值
            return raw if s >= _CUT_OF.get(raw, 0.0) + STAGE_HYST_UP else cur
        return raw if s < _CUT_OF.get(cur, 0.0) - STAGE_HYST_DOWN else cur  # 降档：要跌够

    # ---- ④ 裂痕 + ④b 债务 + ③ 交情 + ② 心情 ----
    def apply_turn(self, state: dict, signals: dict, now: Optional[float] = None) -> dict:
        t = _now() if now is None else now
        aff_before = {d: float(state["affinity"][d]) for d in DIMENSIONS}
        score_before = _score(state)
        scar_before = _scar(state)
        pre_last = float(state.get("last_interaction_ts") or 0.0)
        pre_gap_h = (t - pre_last) / 3600.0 if pre_last else 0.0

        sig = normalize_kind(signals) if self.kind_norm_on else signals
        if self.semantic_from == "text":
            sig = semantic_from_text(sig)

        # ④e 跨句语境修复：上一句刚强正向（表白/夸赞），本句是"嘴硬/害羞"否定词，
        #     且本句并非真伤人（harsh 低）⇒ 真人懂这是害羞而非真拒绝。小模型单句裁判会把
        #     valence 误判负向，这里用 last_signals 的上一句语义 + 本句嘴硬词补回来。
        if self.bashful_ctx_on:
            _prev = (state.get("last_signals") or {}).get("v2") or {}
            _pa = float(_prev.get("affection") or 0.0)
            _pc = float(_prev.get("compliment") or 0.0)
            _txt = str(sig.get("user_text") or "")
            _val = float(sig.get("valence", 0.0) or 0.0)
            _hsh = float(sig.get("harsh", 0.0) or 0.0)
            if (_pa >= 0.5 or _pc >= 0.5) and _BASHFUL_RE.search(_txt) \
                    and _val < 0.3 and _hsh < 0.5:
                sig = dict(sig)
                sig["valence"] = max(_val, 0.45)
                sig["harsh"] = 0.0
                sig["affection"] = max(float(sig.get("affection", 0.0) or 0.0), 0.6)
                sig["kind"] = "affection"
                # 嘴硬不是真拒绝/调侃/受伤：清掉 normalize_kind 与语义补类误推的
                # 负面字段，否则 _refine 会先命中「被拒绝→失落」或「调侃→哭笑不得」。
                sig["reject"] = 0.0
                sig["tease"] = 0.0
                sig["_bashful"] = 1

        super().apply_turn(state, sig, now=t)         # 分数主逻辑照旧

        # ④d 连续冷淡累积：被冷落是"量"的问题。单句敷衍裁判可能把 perf 标高而不算冷淡，
        #     但连续多句没温度，真人会明显觉得被冷落 ⇒ 引擎层自己数（不依赖裁判跨句推理）。
        if self.cold_streak_on:
            _val = float(sig.get("valence", 0.0) or 0.0)
            _care = float(sig.get("care", 0.0) or 0.0)
            _depth = float(sig.get("depth", 0.0) or 0.0)
            _perf = float(sig.get("perfunctory", 0.0) or 0.0)
            # 【2026-10-13·fix1】放宽冷脸判定：真实"冷脸"回复常被裁判判成轻度负向
            # （valence≈-0.3）而非真·无温度，原闸门 0<=valence<=0.1 永远清零 streak。
            # 现接受 -0.3~0.15 的 valence，并把"高敷衍"直接计为冷；明显伤人(harsh>=0.25)
            # 不计入（那是炸毛/委屈，不是被冷落）。
            _cold = (-0.3 <= _val <= 0.15) and _care <= 0.25 and _depth <= 0.25 \
                    and (_perf >= 0.5 or _depth <= 0.1) \
                    and float(sig.get("harsh", 0.0) or 0.0) < 0.25
            _streak = int(state.get("cold_streak") or 0)
            _streak = _streak + 1 if _cold else 0
            state["cold_streak"] = _streak
            if _streak >= COLD_STREAK_TRIGGER:
                _extra = (_streak - COLD_STREAK_TRIGGER + 1) * COLD_STREAK_PENALTY
                for _d in ("warmth", "intimacy"):
                    state["affinity"][_d] = _clamp(
                        float(state["affinity"][_d]) - _extra, DIM_FLOOR[_d], DIM_MAX)
        else:
            state["cold_streak"] = 0

        harsh = _clamp(float(sig.get("harsh", 0.0) or 0.0), 0.0, 1.0)
        repair = _clamp(float(sig.get("repair", 0.0) or 0.0), 0.0, 1.0)
        scar = scar_before
        if harsh > 0.0:
            scar = min(1.0, scar + harsh * SCAR_PER_HARSH)
        if repair > 0.0:
            scar = max(0.0, scar * (1.0 - SCAR_REPAIR_RATE * repair))
        state["scar"] = round(scar, 4)

        # 信任裂痕：本轮**正向**增量按疤打折（负向打不动，疼是真疼）。
        #   分工：信任最慢、亲密次之、热络（气氛）最轻 —— 真人是
        #   "气氛先暖起来、信任得慢慢补"，而不是一句对不起满血复活。
        for dim, penalty in (("trust", SCAR_TRUST_PENALTY),
                             ("intimacy", SCAR_INTIMACY_PENALTY),
                             ("warmth", SCAR_WARMTH_PENALTY)):
            gain = float(state["affinity"][dim]) - aff_before[dim]
            if gain > 0.0 and scar > 0.0:
                keep = 1.0 - penalty * scar
                state["affinity"][dim] = _clamp(aff_before[dim] + gain * keep,
                                                DIM_FLOOR[dim], DIM_MAX)

        # ④b 道歉不该倒赚：把伤人的实际掉分记成债，之后靠"哄"的恢复总额有上限，
        #     超出部分直接削掉；剩下的缺口只能靠日子慢慢补（见 catch_up）。
        delta = _score(state) - score_before
        debt = float(state.get("hurt_debt", 0.0) or 0.0)
        rec = float(state.get("recovered", 0.0) or 0.0)
        if harsh > 0.0 and delta < 0.0:
            debt += abs(delta)
        if delta > 0.0 and debt > 0.0:
            if self.repair_debt_on:
                allow = max(0.0, debt * REPAIR_RECOVER_RATIO - rec)
                if delta > allow:
                    if delta > 1e-6 and allow <= 0.0:
                        k = 0.0
                    else:
                        k = min(1.0, allow / delta) if delta > 1e-6 else 0.0
                    for dim in DIMENSIONS:
                        b = aff_before[dim]
                        cur = float(state["affinity"][dim])
                        state["affinity"][dim] = _clamp(b + (cur - b) * k,
                                                        DIM_FLOOR[dim], DIM_MAX)
                    rec += allow
                else:
                    rec += delta
            else:
                rec += delta
        state["hurt_debt"] = round(debt, 4)
        state["recovered"] = round(min(rec, debt), 4)
        state["stage"] = self._stage(state)

        _bump_bond_days(state, t)
        # 先把 v2 裁判的语义信号挂进 last_signals，再算心情（_refine 靠它细分情绪）
        try:
            ls = state.get("last_signals") or {}
            if isinstance(ls, dict):
                ls["v2"] = {k: sig.get(k) for k in (
                    "kind", "affection", "compliment", "gratitude", "vulnerable",
                    "share", "tease", "need", "jealous", "farewell", "reject",
                    "harsh", "care", "repair", "valence", "depth", "user_state",
                ) if k in sig}
        except Exception:  # noqa: BLE001
            pass
        self._set_mood(state, self.derive_mood(state, t, gap_h=pre_gap_h), t)
        try:
            ls = state.get("last_signals") or {}
            if isinstance(ls, dict):
                ls["mood_after"] = str((state.get("mood") or {}).get("label") or "")
                ls["scar"] = round(scar, 3)
                ls["bond_days"] = _bond_days(state)
        except Exception:  # noqa: BLE001
            pass
        return state

    # ---- ⑤ UI 与提示词同源 ----
    def describe_mood(self, state: dict) -> dict:
        label = str((state.get("mood") or {}).get("label") or "平静")
        if label not in MOOD_TEXT_V2:
            label = "平静"
        atmo, tip = MOOD_TEXT_V2[label]
        return {"label": label, "atmosphere": atmo, "tip": tip}


# ══════════════════════════════════════════════════════════════════ ⑤b 关系块（v2）
# v1 的底线句只有 3 类（心情不好/开心/平常），17 种心情共用 3 句 ⇒ 说话分寸没差别。
# 这里给每种心情一句自己的底线（仍然零数字、不复述）。
BOTTOM_BY_MOOD: dict[str, str] = {
    "炸毛":     "本轮底线：她在气头上，别讨好也别装没事，允许短句、允许沉默，但别真的伤人。",
    "闹别扭":   "本轮底线：别装特别高兴，也别追问，轻轻接住就好。",
    "委屈":     "本轮底线：别说教、别辩解，先站在她那边。",
    "失落":     "本轮底线：她有点淡淡的，别硬热闹，主动一点但别黏。",
    "不安":     "本轮底线：给她一个确定的回应，别含糊、别敷衍过去。",
    "吃醋":     "本轮底线：别装没听出来，说清楚就好，也别火上浇油。",
    "心疼":     "本轮底线：先接住他，别急着说自己、别急着讲道理。",
    "蔫蔫的":   "本轮底线：她提不起劲，话可以少一点，别硬找话题。",
    "困倦":     "本轮底线：她困了，短句、慢一点，别拉着聊长篇。",
    "感动":     "本轮底线：认真接住，别用俏皮话岔开。",
    "哭笑不得": "本轮底线：玩笑她接得住，轻轻回一句就好。",
    "安心":     "本轮底线：可以软一点、慢一点，不用端着。",
    "柔软":     "本轮底线：可以说点真心话，她接得住。",
    "心动":     "本轮底线：可以亲昵一点，但别一下子说太满。",
    "雀跃":     "本轮底线：可以比平时更活泼、更主动一点。",
    "想你":     "本轮底线：可以主动一点、亲昵一点，别端着。",
    "平静":     "本轮底线：按你平常的语气就好。",
}


def relation_block_text_v2(st: dict, tier: str = "cloud") -> str:
    """v2 关系块：细档进度 + 分寸梯度 + 每种心情自己的底线。

    ⑤ 修 v1 的一处硬伤：`tier != "cloud"`（她**主动搭话** / 本地双工）时 v1 会把
    「你们的关系」整行砍掉 —— 恰恰是最需要拿捏分寸的场景反而不知道两人到哪一步。
    这里改为保留该行（并把「分寸」并进去控 token，只比 v1 多约 20 字）。
    """
    mood = st.get("mood") or {}
    label = str(mood.get("label") or "平静")
    if label not in MOOD_PROMPT_V2:
        label = "平静"
    mword = R._intensity_word(int(mood.get("intensity") or 1))
    arch_key = str(st.get("archetype") or "") or R.DEFAULT_ARCHETYPE
    arch_guide = R.ARCHETYPE_PROMPT.get(arch_key,
                                        R.ARCHETYPE_PROMPT[R.DEFAULT_ARCHETYPE])
    rel = f"- 你们的关系：{progress_label_v2(st)}"
    gap = R._gap_hint(st)
    if gap:
        rel += f"，{gap}"
    disc = disclosure_line(st)
    head = "【当前关系状态（内部参考，别复述、别提\"好感度\"这种词）】"
    mood_line = (f"- 她现在的心情：{label}（{mword}）。"
                 f"{MOOD_PROMPT_V2.get(label, '')}")
    bottom = f"- {BOTTOM_BY_MOOD.get(label, '按你平常的语气就好。')}"

    if tier == "cloud":
        return "\n".join([
            head, mood_line, rel, f"- {disc}",
            f"- 你的性格：{arch_key}——{arch_guide}", bottom,
        ])
    # 本地/双工档：关系行**保留**（v1 在此砍掉了它），与「分寸」并成一行
    return "\n".join([
        head, mood_line, f"{rel[2:]}。{disc}",
        f"- 你的性格：{arch_key}——{arch_guide}", bottom,
    ])
