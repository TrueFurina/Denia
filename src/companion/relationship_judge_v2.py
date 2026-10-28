# -*- coding: utf-8 -*-
"""关系裁判 v2 —— **已定轨**（2026-10-13 起草）。

⚠️ 状态：已定轨（非实验）。本模块**已被生产接线**：`companion/relationship_v2.py:156`
`from .relationship_judge_v2 import heuristic_signals_v2`，即 v2 关系引擎在缺 LLM 裁判时
回退到本模块的规则裁判。`tools/relationship_judge_ab_probe.py` 仍可引它做 A/B 对照，
但那只是可选探针，不改变"本模块已上线"的事实。

## 为什么要写 v2
`heuristic_signals`（v1 规则裁判）实测出的语义盲区（见 docs/_relationship_affection_probe.txt）：
  · 夸外貌「你真好看」≈ 0（外貌夸赞完全不在词典里）
  · 「我爱上你了」只有 +0.11（"爱上"不在词典，"爱你"才在）
  · 「你真没用」-0.08（轻骂不在词典）
  · 「你别这么黏人行不行，烦死了」**+0.22 反向加分**
  · 「谢谢你一直陪着我」被判敷衍 0.25

根因不只是"词典少"，而是四个结构性缺陷：
  ① **没有主语判定** —— 分不清"我烦死了"（用户难受，她该心疼）和"你烦死了"（骂她）。
  ② **没有否定/反语处理** —— "我不喜欢你" 只能靠几个硬编码子串兜。
  ③ **没有程度** —— harsh 只有 0.9 一档，"滚"和"你有点烦"一样重。
  ④ **valence 有 +0.12 无条件基线** —— 说任何话都自动涨好感（"进度条"感的直接来源）。
  ⑤ **完全不看上下文** —— RuleJudge.judge 收到 context 直接丢弃。

## v2 做了什么
  · 语义类别从 5 类扩到 14 类（新增 夸赞/感谢/脆弱/分享/调侃/求安全感/吃醋/告别）
  · 主语判定：我 vs 你，决定负向情绪是"他难受"还是"他伤我"
  · 否定窗口 + 撒娇豁免（"我才不喜欢你呢"是撒娇，不是拒绝）
  · harsh 三级（轻 0.3 / 中 0.6 / 重 0.95），程度词 ×1.25 / ×0.6
  · valence 基线归零：中性闲聊不再自动加分
  · 支持 context（承上省略的"嗯"= 收到，不是敷衍）
  · 输出**向后兼容**：老字段全在，新字段老引擎会忽略（apply_turn 只 .get 固定键）
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ══════════════════════════════════════════════════════════ 词典
# 亲昵/表白
AFFECTION = ("喜欢", "爱你", "爱上了", "爱上你", "爱上", "想你了", "想你", "心动",
             "亲亲", "亲一个", "抱抱", "么么", "mua", "最爱", "好爱", "爱死",
             "宝贝", "香一个", "在一起", "做我女朋友", "做我老婆", "嫁给我",
             "离不开你", "心头好", "稀罕你", "中意你", "我宣你")
# 夸赞（外貌/能力）——必须指向"你"
COMPLIMENT = ("好看", "漂亮", "可爱", "好美", "美貌", "温柔", "贴心", "聪明", "厉害",
              "能干", "好棒", "真棒", "优秀", "好强", "绝了", "好听", "声音好", "好乖",
              "真乖", "萌", "有才", "了不起", "好可爱", "好温柔", "好厉害", "真好看")
# 感谢
GRATITUDE = ("谢谢", "感谢", "多谢", "辛苦了", "感激", "承蒙", "thanks", "thx",
             "多亏", "幸亏有你", "有你真好", "多亏有你")
# 关心她
CARE = ("怎么样", "心情", "你呢", "还好吗", "还好吧", "好不好", "开不开心", "累不累",
        "吃了吗", "吃了没", "吃饭了", "吃饭没", "注意身体", "注意休息", "早点休息",
        "早点睡", "别熬夜", "担心你", "别难过", "陪你", "辛苦",
        "在干嘛", "在忙什么", "开心吗", "开心么", "开心不", "高兴吗", "快乐吗",
        "生气吗", "生气没", "累吗", "困吗", "困不困", "无聊吗", "无聊不",
        "保重", "照顾好自己", "喝热水", "穿暖", "别太累", "别硬撑", "歇会",
        "休息一下", "睡吧", "快去睡", "别忙太晚", "怎么了", "不开心吗", "谁惹你",
        "受委屈", "没事吧", "别哭", "加油", "我陪你", "有我在", "别着凉")
# 道歉/挽回
REPAIR = ("对不起", "抱歉", "我错了", "别生气", "原谅", "不该", "是我的错", "哄",
          "赔罪", "错了嘛", "别不理我", "sorry", "对不起嘛", "抱歉嘛", "我混蛋",
          "我该死", "消消气", "别气了", "不气了", "气消了没", "我不好", "是我不好",
          "我的问题", "我道歉", "向你道歉", "别记仇")
# 伤人：重
HARSH_HEAVY = ("滚", "去死", "闭嘴", "废物", "垃圾", "傻逼", "傻b", "贱", "恶心",
               "蠢货", "智障", "脑子有病", "有病吧", "你算什么", "不配", "滚开",
               "滚一边", "去你的", "操你", "妈的")
# 伤人：中
HARSH_MID = ("讨厌你", "烦人", "没用", "真笨", "好笨", "笨死了", "无语", "受不了你",
             "有毛病", "神经", "蠢", "别演了", "装什么", "虚伪", "白痴", "幼稚",
             "烦不烦", "你真行", "你可真行", "没意思")
# 伤人：轻
HARSH_LIGHT = ("别烦我", "别吵", "走开", "够了", "少管", "别管我", "你走", "不想理你",
               "不理你", "离我远点", "别黏", "黏人", "别烦",
               "不懂我", "不了解我", "不明白我")
# 身份否定（最伤的一类）
SELF_DEPRECATE = ("人工智能", "ai", "程序", "代码", "机器人", "模型", "虚拟",
                  "就是装的", "假的", "不是真的", "没有感情", "一串数据", "算法")
SELF_DEPRECATE_HINT = ("你就是", "不过是", "只是个", "就是个", "反正你", "你根本")
# 用户自己难受（**主语判定后**才成立）——真人感关键：她该心疼，而不是觉得被骂
VULNERABLE = ("我好累", "我累", "好累", "累死了", "撑不住", "快崩溃", "崩溃", "想哭",
              "我难过", "好难过", "难受", "我委屈", "被骂了", "被裁", "失业",
              "分手了", "失恋", "生病", "不舒服", "失眠", "睡不着", "焦虑",
              "压力大", "压力好大", "心烦", "好烦", "烦死了", "emo", "低落",
              "搞砸了", "被骂", "被批评", "被说", "难过", "委屈", "心情不好", "撑得好辛苦")
# 自贬（"我觉得自己真没用"）——主语是"我"时归脆弱，不是骂她
SELF_PUTDOWN = ("没用", "一无是处", "很废", "真废", "好废", "不行了", "很差劲",
                "糟透了", "好笨", "真笨", "太蠢")
# 分享/倾诉
SHARE = ("我跟你说", "告诉你", "说个事", "我今天", "我昨天", "我刚刚", "我刚才",
         "我发现", "我突然", "我想说", "其实我", "你听我说", "我有个事", "跟你说个",
         "我想跟你", "说个秘密", "偷偷告诉你")
# 调侃/玩笑
TEASE = ("逗你", "骗你的", "骗你玩", "开玩笑", "逗你玩", "小傻瓜", "傻瓜", "笨蛋",
         "你猜", "猜猜", "骗到了", "骗到你", "装傻", "哄你的", "逗我玩", "开玩笑的")
# 求安全感（追问确认）——不是质疑，是撒娇
NEED = ("喜欢我吗", "喜欢我么", "爱我吗", "真的吗", "真的假的", "你是不是",
        "你会不会", "你确定", "假的吧", "骗我的吧", "哄我的吧", "不喜欢我",
        "那你呢", "你也是吗", "当真", "想我吗", "有没有想我", "是不是真的")
# 吃醋/独占
JEALOUS = ("不许", "不准", "只能", "只准", "只许", "你是我的", "别的女生",
           "别的女孩", "其他女生", "其他女孩", "小姐姐", "前女友", "前任",
           "吃醋", "酸了", "不准看", "不许看")
# 告别/要离开
FAREWELL = ("我走了", "先走了", "先睡了", "我去睡", "拜拜", "再见", "下次聊",
            "我先忙", "我忙了", "要出门", "晚点聊", "我先下了", "先下了",
            "回头聊", "走了哈", "睡了", "晚安", "下线了")
# 敷衍
PERFUNCTORY = ("嗯", "哦", "噢", "呃", "行", "好", "随便", "都行", "无所谓",
               "还行", "不知道", "没", "没有", "在", "在的", "哈哈", "呵呵",
               "没事", "知道了", "哦哦", "行吧", "好吧", "嗯嗯")
# 纯问候
GREETINGS = ("你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "在么",
             "在不在", "早上好", "早安", "中午好", "下午好", "晚上好", "我来了")

# 否定词（情感词前 2 字窗口内出现即视为否定）
NEG_WORDS = ("不", "没", "别", "无", "非", "莫", "勿")
# 撒娇语气词（句尾）：有这些时，"我才不喜欢你呢"是撒娇不是拒绝
COQUETRY_TAIL = ("呢", "啦", "啊", "嘛", "哦", "鸭", "咯")
# 程度词
INTENSIFIER = ("很", "超", "太", "特别", "巨", "贼", "真的", "非常", "极", "最", "死", "好")
DIMINISHER = ("有点", "稍微", "略", "还算", "比较", "些许", "不太")

_COQUETRY_RE = re.compile(r"(才不|才没有|才不是|才不喜欢)")


def _norm(t: str) -> str:
    return (t or "").replace(" ", "").replace("\n", "").strip()


def _negated(n: str, word: str) -> bool:
    """情感词 word 前面 2 字内是否有否定词。"""
    idx = n.find(word)
    if idx < 0:
        return False
    pre = n[max(0, idx - 2):idx]
    return any(w in pre for w in NEG_WORDS)


def _hits(n: str, words) -> list[str]:
    return [w for w in words if w in n]


def _has_any(n: str, words) -> bool:
    return any(w in n for w in words)


def _intensity_mult(n: str) -> float:
    if _has_any(n, DIMINISHER):
        return 0.6
    if _has_any(n, INTENSIFIER):
        return 1.25
    return 1.0


def _first_pronoun(n: str) -> str:
    """先出现的代词：'you' / 'me' / '' —— 用于区分"你烦"和"我烦"。"""
    yi = n.find("你")
    wi = n.find("我")
    if yi < 0 and wi < 0:
        return ""
    if yi < 0:
        return "me"
    if wi < 0:
        return "you"
    return "you" if yi < wi else "me"


def heuristic_signals_v2(user_text: str,
                         context: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """v2 规则裁判：返回向后兼容的 signals（老字段全在）+ 新语义字段。"""
    t = (user_text or "").strip()
    if not t:
        return {"valence": 0.0, "judge": "rule2"}
    n = _norm(t)
    L = len(n)
    mult = _intensity_mult(n)
    pron = _first_pronoun(n)

    # ---------- 各类命中 ----------
    aff_hits = _hits(n, AFFECTION)
    affection = 0.0
    reject = 0.0            # 真的在推开（"我不喜欢你了"）——不是撒娇
    if aff_hits:
        # 否定语境：真的"不喜欢你"是拒绝；但"我才不喜欢你呢"是撒娇
        neg = any(_negated(n, w) for w in aff_hits)
        coquetry = bool(_COQUETRY_RE.search(n)) or (neg and n[-1:] in COQUETRY_TAIL)
        if neg and not coquetry:
            affection = 0.0
            reject = min(1.0, 0.6 * mult)
        else:
            affection = min(1.0, (0.55 if coquetry else 0.9) * mult)

    # 夸赞：必须指向"你"
    comp_hits = _hits(n, COMPLIMENT)
    compliment = 0.0
    if comp_hits and ("你" in n or "你的" in n):
        neg = any(_negated(n, w) for w in comp_hits)
        compliment = 0.0 if neg else min(1.0, 0.8 * mult)

    gratitude = min(1.0, 0.85 * mult) if _has_any(n, GRATITUDE) else 0.0

    care = min(1.0, 0.9 * mult) if _has_any(n, CARE) else 0.0
    if affection:
        care = max(care, 0.7)

    repair = min(1.0, 0.9 * mult) if _has_any(n, REPAIR) else 0.0

    # ---------- 自贬 vs 骂她：同一个词，看主语 ----------
    self_put = bool(pron == "me" and _has_any(n, SELF_PUTDOWN))

    # ---------- 伤人（三级 + 主语判定） ----------
    harsh = 0.0
    if _has_any(n, HARSH_HEAVY):
        harsh = 0.95 * mult
    elif _has_any(n, HARSH_MID) and not self_put:
        harsh = 0.6 * mult
    elif _has_any(n, HARSH_LIGHT):
        harsh = 0.3 * mult
    # 身份否定（不带爱意语境时最伤）
    if (affection <= 0.0) and _has_any(n, SELF_DEPRECATE) and _has_any(n, SELF_DEPRECATE_HINT):
        harsh = max(harsh, 0.85)
    harsh = min(1.0, max(0.0, harsh))

    # ---------- 用户自己的负向状态（主语判定） ----------
    vul = 0.0
    if _has_any(n, VULNERABLE):
        # "你烦死了"= 骂她；"我烦死了"= 他自己难受
        if pron == "you" and _has_any(n, ("烦死了", "烦人", "烦不烦", "没用", "真笨")):
            vul = 0.0
            harsh = max(harsh, 0.6 * mult)
        else:
            vul = min(1.0, 0.9 * mult)
    elif self_put:
        vul = min(1.0, 0.85 * mult)      # 自贬 → 他对自己失望，她该心疼

    share = min(1.0, 0.7) if _has_any(n, SHARE) else 0.0
    tease = min(1.0, 0.7) if (_has_any(n, TEASE) and not _has_any(n, HARSH_HEAVY)) else 0.0
    need = min(1.0, 0.85) if _has_any(n, NEED) else 0.0
    jealous = min(1.0, 0.7) if _has_any(n, JEALOUS) else 0.0
    farewell = min(1.0, 0.8) if _has_any(n, FAREWELL) else 0.0

    is_greet = L <= 8 and any(n == g or n.startswith(g) for g in GREETINGS)

    # ---------- 敷衍（带上下文修正） ----------
    perf = 0.0
    has_semantics = bool(affection or compliment or gratitude or care or repair
                         or harsh or vul or share or tease or need or jealous)
    if not is_greet and not has_semantics and _has_any(n, PERFUNCTORY):
        # ⚠️ 必须是**整句就是这个词 / 以它开头**，不能只做子串匹配 ——
        #    "你声音真好听" 里含"好"就被判敷衍，是 v1 那类误判的根源。
        if not (any(n == k or n.startswith(k) for k in PERFUNCTORY)):
            perf = 0.0
        elif L <= 6:
            # 重复字 / 语气词 = 认真收到（"嗯嗯""好呀"），不是敷衍；
            # 但单字（"哦"）或"吧"结尾（"随便吧""行吧"）仍是敷衍。
            if re.search(r"(.)\1", n) or (L >= 2 and n[-1:] in ("呀", "啊", "啦", "呢")):
                perf = 0.2
            else:
                perf = 0.9
        elif L <= 10:
            perf = 0.45
    # 上下文修正：上一句她在说正事（≥25 字），用户只回个短应答 → 这是"收到"
    if perf >= 0.45 and context:
        try:
            prev = context[-1] if isinstance(context[-1], dict) else {}
            if str(prev.get("role") or "") == "assistant" and len(str(prev.get("content") or "")) >= 25:
                perf = 0.1
        except Exception:  # noqa: BLE001
            pass

    # ---------- valence：基线归零（v1 是无条件 +0.12） ----------
    valence = 0.0
    valence += affection * 0.70
    valence += compliment * 0.45
    valence += gratitude * 0.40
    valence += care * 0.55
    valence += repair * 0.45
    valence += share * 0.20
    valence += vul * 0.25            # 袒露脆弱 = 信任加深（但不该让她"高兴"）
    valence += tease * 0.25
    valence += need * 0.20           # 求确认是撒娇，不是质疑
    valence += jealous * 0.20
    valence -= farewell * 0.10
    valence -= perf * 0.40
    valence -= harsh * 0.95
    valence -= reject * 0.80         # "我不喜欢你了"
    if is_greet:
        valence += 0.12
    valence = max(-1.0, min(1.0, valence))

    # ---------- 充分度 / 置信 ----------
    sufficient = not (perf >= 0.7 and L <= 6 and not has_semantics)
    confidence = 0.35 if not has_semantics else (0.9 if (affection or harsh or repair) else 0.75)

    # ---------- 深度 / 投入 ----------
    depth = 0.10
    if care:
        depth = max(depth, 0.45 + care * 0.35)
    if affection:
        depth = max(depth, 0.85)
    if repair:
        depth = max(depth, 0.6)
    if vul:
        depth = max(depth, 0.8)      # 袒露脆弱是很深的互动
    if share:
        depth = max(depth, 0.5)
    if need:
        depth = max(depth, 0.55)
    if L >= 24:
        depth += 0.20
    elif L >= 12:
        depth += 0.10
    depth -= perf * 0.5
    depth = max(0.0, min(1.0, depth))

    effort = 0.15
    if care or affection or repair or gratitude or vul:
        effort = max(effort, 0.75)
    if L >= 20:
        effort = max(effort, 0.7)
    elif L >= 10:
        effort = max(effort, 0.45)
    effort -= perf * 0.55
    effort = max(0.0, min(1.0, effort))

    # ---------- 用户当下状态（给"她要不要心疼你"用） ----------
    user_state = "neutral"
    if vul:
        user_state = "tired" if _has_any(n, ("累", "困", "睡", "没劲", "不想动")) else "down"
    elif affection or compliment or tease:
        user_state = "warm"
    elif harsh:
        user_state = "angry"

    # 主类别（供探针评测用；生产不需要）
    if harsh >= 0.25:
        kind = "harsh"
    elif reject >= 0.3:
        kind = "reject"
    elif affection >= 0.5:
        kind = "affection"
    elif repair >= 0.5:
        kind = "repair"
    elif perf >= 0.7:
        kind = "perfunctory"
    elif vul >= 0.5:
        kind = "vulnerable"
    elif care >= 0.5:
        kind = "care"
    elif compliment >= 0.5:
        kind = "compliment"
    elif gratitude >= 0.5:
        kind = "gratitude"
    elif need >= 0.5:
        kind = "need"
    elif jealous >= 0.5:
        kind = "jealous"
    elif tease >= 0.5:
        kind = "tease"
    elif share >= 0.5:
        kind = "share"
    elif farewell >= 0.5:
        kind = "farewell"
    else:
        kind = "neutral"

    return {
        # ---- 老字段（向后兼容，老引擎照读） ----
        "valence": round(valence, 3),
        "care": round(care, 2),
        "perfunctory": round(perf, 2),
        "repair": round(repair, 2),
        "harsh": round(harsh, 2),
        "depth": round(depth, 2),
        "effort": round(effort, 2),
        "sufficient": bool(sufficient),
        "confidence": round(confidence, 2),
        "user_text": t,
        "judge": "rule2",
        # ---- 新字段（老引擎忽略，v2 引擎读） ----
        "affection": round(affection, 2),
        "compliment": round(compliment, 2),
        "gratitude": round(gratitude, 2),
        "vulnerable": round(vul, 2),
        "share": round(share, 2),
        "tease": round(tease, 2),
        "need": round(need, 2),
        "jealous": round(jealous, 2),
        "farewell": round(farewell, 2),
        "user_state": user_state,
        "kind": kind,
        "intensity_mult": round(mult, 2),
        "pronoun": pron,
    }


class RuleJudgeV2:
    """v2 规则裁判（签名与 RuleJudge 一致，可直接替换）。"""

    name = "rule2"

    def __init__(self) -> None:
        self.calls = 0

    async def judge(self, user_text: str, persona: Optional[dict[str, Any]] = None,
                    context: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        self.calls += 1
        return heuristic_signals_v2(user_text, context=context)
