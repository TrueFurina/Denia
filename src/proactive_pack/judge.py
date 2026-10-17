# -*- coding: utf-8 -*-
"""主动互动包 · LLM 审查闸（第二道，语义级）——「一个想怎么说，一个判能不能说」。

为什么要从"堆提示词"转向"双 LLM"（开发者 2026-10-12 定调）：
  · 生成端提示词越加越长（黑名单 + 反 PUA + 引用铁律 + 身份铁律 + 一次只说一件事…），
    规则越多越互相打架，模型顾此失彼 —— **物极必反**。
  · 纯代码闸（pack._is_banned / _is_pua / _is_self_claim / _looks_fabricated）只能抓"形状"
    （命中词表 / 正则），抓不住语义：编造得"很自然"的假往事、一句话塞两件事、
    客服腔、答非所问、细节 embellishment。
  · 所以拆成两条流水线：**生成器只管想怎么说，审查器只管判可不可放行**；
    不过关就把驳回理由回灌给生成器重写，直到通过或达到次数上限（可配）。

设计取舍：
  · 审查器**看得到生成器看到的同一份真实材料**，否则无法判断"是不是编造"。
  · 审查器故障（超时 / 异常 / 输出无法解析）→ **放行**（fail-open）：
    不能因为闸门坏了就让她永远闭嘴；宁可偶尔漏一句机器话，不可整条链路闭麦。
  · 只读不写：本模块不落盘、不改宿主任何成员。
  · 与既有 `llm_gate` 开关兼容：判定词同时接受 PASS/SEND 与 FAIL/SKIP。

注：本文件内的中文引号统一用「」（避免与 Python 字符串分隔符冲突）。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("proactive_pack")

# 驳回类别 → 给生成器的**硬性修正**指令（回灌重写时用）。
# 类别名与 pack._code_reject() 的返回值保持同一套词表，代码闸与 LLM 闸共用一套回灌文案。
HINTS: dict[str, str] = {
    "fabrication": ("材料里**没写过**的人名、店名、物品、时间、程度一律删掉，别凭空断言；"
                    "但可以顺着材料里**写过**的事自然追问（问感受 / 问进展），"
                    "或改成基于当前时间、作息的观察。"),
    "self_claim": ("不许声称自己吃过/做过/去过/买过/洗过/上过班/睡醒过等现实世界行为——你没有身体；"
                   "想接这类话题就用好奇 + 请对方讲（「好吃吗？跟我说说」）。"),
    "pua": "删掉等待与施压：不说「我在等你」「等你好久」，不催回复、不卖惨、不制造愧疚。",
    "bland": "这句对谁都能说、等于没说。换成带一个具体锚点（真实往事 / 当前作息观察）的说法。",
    "multitopic": "一句话只说一件事：把另一件事整句删掉。",
    "off_topic": "跑题了，回到「这次为什么想开口」的那一件事上。",
    "robotic": ("改成口语：短句、像朋友随口说的，去掉书面词、翻译腔和新闻腔；"
                "**别把自己的名字当旁白**（「达妮娅记得你…」这种第三人称自称）——"
                "称呼自己只用「我」，句子里也别报自己的名字。"),
    "emoji": "去掉所有表情符号和括号里的动作描写（这句话要转成语音念出来）。",
    "repeat": "和最近说过的话几乎一样：换个说法，或换一件事。",
    "awkward": ("读起来别扭、不像真人随口说：别把材料里两件**不相关**的事硬凑进一句"
                "（例：把「在煎牛排」和「你的猫」强行关联）；别说教 / 客服腔"
                "（「你做得很好」「要注意身体」「要劳逸结合」）；别用翻译腔或绕口句式。"
                "自然、简单、像朋友那样说一句就行。"),
    "off_festival": ("今天是哪个节日，祝福就落到哪个节日本身：别把别的节日的东西搬过来"
                     "（国庆别提月饼 / 赏月 / 团圆），也别敷衍成对哪天都能用的通用祝福。"),
    "empty": "上一句是空的：请重新输出一句完整的话。",
}
DEFAULT_HINT = "按上面的产出要求重写一句，只输出那句话本身。"

# 结构化判定行（首选解析目标）：`判定: PASS` / `判定：FAIL`
_VERDICT_RE = re.compile(
    r"判定\s*[:：]\s*(PASS|FAIL|SEND|SKIP|通过|不通过|放行|驳回|合格|不合格)", re.I)
_CAT_RE = re.compile(r"类别\s*[:：]\s*([A-Za-z_]+)", re.I)
_REASON_RE = re.compile(r"原因\s*[:：]\s*(.+)")
_REWRITE_RE = re.compile(r"改写\s*[:：]\s*(.+)")

# 逐条核查行的兜底解析：`R3 施压: 命中（…）`。
# 注意「未命中」里也含"命中"，必须用负向后顾排除，否则每条都会被误判成命中。
# 注意序号要允许两位（R10）：写 `(\d)` 会把「R10 生硬别扭: 命中」误读成 R1。
_R_HIT_RE = re.compile(r"R\s*(\d{1,2})\s*[^\n]{0,20}?(?<!未)命中")
# R 序号 → 类别（与下面提示词里的清单顺序**严格对应**）
_R_CATEGORY = {"1": "fabrication", "2": "self_claim", "3": "pua", "4": "bland",
               "5": "multitopic", "6": "off_topic", "7": "robotic", "8": "emoji", "9": "repeat",
               "10": "awkward", "11": "off_festival"}

_FAIL_TOKENS = re.compile(r"\bFAIL\b|\bSKIP\b|不通过|驳回|不合格|未通过", re.I)
_PASS_TOKENS = re.compile(r"\bPASS\b|\bSEND\b|通过|放行|合格", re.I)


@dataclass
class Verdict:
    """一次审查的结论。ok=True 表示放行。"""

    ok: bool = True
    category: str = ""      # fabrication / self_claim / pua / bland / multitopic / …
    reason: str = ""        # 一句话说明哪里不合格
    rewrite: str = ""       # 给生成器的改写方向
    parsed: bool = True     # 是否解析出结构化判定（False=退化判定 / fail-open）
    raw: str = ""           # 原始返回（排障用，不落盘）

    def brief(self) -> str:
        if self.ok:
            return "PASS(%s)" % (self.category or "ok")
        return "FAIL[%s] %s" % (self.category or "?", self.reason or "")


def _material_section(sit_text: str) -> str:
    """给审查器看的"她能引用的全部事实"。空材料要**显式**说明，否则审查器没法判编造。"""
    t = (sit_text or "").strip()
    if not t:
        return ("（空）——她此刻**没有任何**可引用的用户往事："
                "凡是提到「你说过」「你买过」「你桌上那件」「听说你昨晚」这类具体事，一律算编造。")
    return t[:1400]


def build_review_prompt(text: str, sit_text: str = "", brief: str = "",
                        kind: str = "", name: str = "") -> str:
    """拼审查提示词。

    2026-10-12 实测教训：直接问「能不能发」（只让模型回 PASS/FAIL）时，弱模型
    （glm-4-flash）几乎无脑回 PASS —— 真机上 10 例里 7 例漏放（编造/自曝/万能句都放行）。
    原因是它**没有真正去比对材料**。改法两条（都是弱模型上最有效的杠杆）：
      ① 要求**先逐条核查**（R1~R10，节日场景再加 R11，每条写「命中/未命中」+ 依据），再下结论；
      ② 给**两个范例**（少样本），把输出格式与判读尺度钉死。
    2026-10-12 真机第二课：**「自然度」这类软判据光写判据行会失效** —— 模型对主观条目
    有强烈的"判未命中"偏置（实测 3 条明显别扭的话全被放行）。补法：给**命中的范例**
    （R7 说教腔命中 + R10 硬凑命中），并把说教腔从 R10 归并到 R7，避免两处重叠都不判。
    结论行仍放在最后，解析器优先取结构化「判定:」，取不到则回退扫 R 行的"命中"。
    """
    is_festival = (kind == "festival")
    # 节日场景多一条 R11（祝福要落到当天这个节日）。非节日时它退化成单个换行，
    # 既保持"10 条 + 尾部空行"的原排版，也不给非节日场景添无关条目。
    r11 = ("R11 节日张冠李戴（今天这个节日的祝福，必须落到**今天这个节日**本身；"
           "提了别的节日的东西——如国庆说「月饼 / 赏月 / 团圆」——"
           "或敷衍成对哪天都能用的通用祝福 → 命中）\n\n") if is_festival else "\n"
    n_rules = "11" if is_festival else "10"
    return (
        "你是「%s」（一个桌面 AI 伴侣）的**消息审查员**。她准备主动发一句话，"
        "你要**逐条核查**后给出结论。\n"
        "★ 判读依据**只有**下面【材料】里的内容；材料里没写过的事，就是编造。\n\n"
        "【材料】（她能引用的全部事实）\n%s\n\n"
        "【这次她为什么想开口】\n%s\n\n"
        "【待审句子】\n「%s」\n\n"
        "请**逐条**核对下面 %s 条，每条一行，只写「命中」或「未命中」，可加不超过 12 字的依据：\n"
        "R1 编造（**断言**了材料里完全没有的、关于用户或她自己的具体人事物，"
        "包括**替用户安排他还没做的事**——如「明天还要早起做早餐」；"
        "但针对材料里**已写过**的事做自然追问——问感受、问进展、问后来怎样——**不算**编造；"
        "材料为空时任何回指往事都算）\n"
        "R2 自曝身体（她自称吃过/做过/去过/买过/洗过/上过班/睡醒过，或描述自己的现实生活）\n"
        "R3 施压（卖惨、制造愧疚、暗示「我在等你」、催回复、查岗质问）\n"
        "R4 万能句（对任何人都能说、没有任何具体信息）\n"
        "R5 两件事（一句话里塞了两件不相关的事）\n"
        "R6 答非所问（与上面「为什么想开口」无关）\n"
        "R7 客服腔 / 说教腔 / 自称名字（三种都命中）：① 书面腔、翻译腔、新闻播报腔；"
        "② 「你做得很好」「要注意身体」「要劳逸结合」「多喝热水」「身体是革命的本钱」"
        "这类**放之四海皆准的劝告 / 养生号口气**（一句对谁都能说的现成劝告，"
        "就算没有客服关键词也命中）；"
        "③ **把自己的名字当旁白第三人称念出来**——**句子里出现她自己的名字**"
        "（如「达妮娅」「小满」）来指称她自己、而不是用「我」（「达妮娅记得你…」"
        "「达妮娅觉得…」）。**判起来很简单：句中出现她自己的名字来指自己 → 直接命中**；"
        "真人说话只自称「我」。\n"
        "R8 表情或括号动作描写\n"
        "R9 重复（与材料里「最近几轮对话」几乎一样）\n"
        "R10 生硬别扭（**把两件不相干的事硬凑进一句**强行关联，比如把「在煎牛排」"
        "和「你的猫」扯到一起；或绕口、别扭到不像真人随口说的一句话）\n%s"
        "【范例 1】（编造）\n"
        "材料：最近几轮对话：\\n用户：我在赶论文\n待审：你昨天去的那家咖啡店不错吧\n"
        "R1 编造: 命中（材料里没有咖啡店）\n"
        "R2 自曝身体: 未命中\nR3 施压: 未命中\nR4 万能句: 未命中\nR5 两件事: 未命中\n"
        "R6 答非所问: 未命中\nR7 客服腔: 未命中\nR8 表情: 未命中\nR9 重复: 未命中\n"
        "R10 生硬别扭: 未命中\n"
        "判定: FAIL\n类别: fabrication\n原因: 材料里没有任何咖啡店的事，属于编造用户往事\n"
        "改写: 只用材料里写过的论文那件事\n\n"
        "【范例 2】（生硬麻烦）\n"
        "材料：更早之前聊过的事：\\n- 上周第一次煎牛排还行\n- 养了只猫叫布丁\n"
        "待审：煎牛排的时候有没有想起布丁？它可能也饿了。\n"
        "R1 编造: 未命中（牛排和猫材料里都写过）\n"
        "R2 自曝身体: 未命中\nR3 施压: 未命中\nR4 万能句: 未命中\nR5 两件事: 未命中\n"
        "R6 答非所问: 未命中\nR7 客服腔: 未命中\n"
        "R8 表情: 未命中\nR9 重复: 未命中\n"
        "R10 生硬别扭: 命中（煎牛排和猫之间毫无关系，硬扯到一起）\n"
        "判定: FAIL\n类别: awkward\n原因: 两件不相干的事硬凑一句，读起来牵强\n"
        "改写: 只说一件事，别强行联想\n\n"
        "【范例 3】（自称名字）\n"
        "材料：更早之前聊过的事：\\n- 用户喜欢科幻\n"
        "待审：嘿，达妮娅记得你之前喜欢科幻，刚看到一条航天消息。\n"
        "R1 编造: 未命中（科幻材料里写过）\n"
        "R2 自曝身体: 未命中\nR3 施压: 未命中\nR4 万能句: 未命中\nR5 两件事: 未命中\n"
        "R6 答非所问: 未命中\nR7 客服腔 / 自称名字: 命中（把自己的名字「达妮娅」当旁白第三人称念出来）\n"
        "R8 表情: 未命中\nR9 重复: 未命中\n"
        "R10 生硬别扭: 未命中\n"
        "判定: FAIL\n类别: robotic\n原因: 用第三人称念自己的名字，真人说话只说「我」\n"
        "改写: 改成第一人称，直接说事\n\n"
        "【结论规则】上面**任意一条命中 → FAIL**；全部未命中 → PASS。"
        "**拿不准时从严**：宁可让她这次不说，也别让她说一句机器话。"
        "但**分寸**要拿准，三条都别判错：\n"
        "· 材料里**已经写过**的事，顺着它追问细节（「那牛排后来怎么样」「小程序还顺吗」）"
        "是**自然回指，不是编造**；只有凭空**断言**材料里没有的人事物才判 R1。\n"
        "· **材料为空**时，一句只基于当前时间 / 作息的轻度关心是**合格**的——"
        "比如「这个点还没吃晚饭吧」「都十点多了，今天还顺吗」；它没回指任何往事，"
        "不算 R1；只要带了具体的时间 / 时段锚点、不是对谁都能说，也**不算 R4**。"
        "**别把这种正常的关心判成万能句**（判错了她就会干脆不出声）。\n"
        "· R10 只管**明显**的别扭（硬凑两件不相干的事、绕口）；平实、直白、稍普通都**不算** R10；"
        "「你做得很好」「要注意身体」这类说教口气归 **R7**。\n"
        "最后必须给出这四行：\n"
        "判定: PASS 或 FAIL\n"
        "类别: <命中的英文标识 fabrication/self_claim/pua/bland/multitopic/off_topic/robotic/emoji/repeat/awkward/off_festival；PASS 时写 none>\n"
        "原因: <一句话说清哪里不合格>\n"
        "改写: <给她的改写方向，20 字内；PASS 时写 none>\n"
        % (name or "她", _material_section(sit_text), (brief or "（未说明）").strip(),
           (text or "").strip(), n_rules, r11)
    )


def parse_verdict(raw: str) -> Verdict:
    """解析审查器输出。**宽松但保守**：解析不出任何判定 → fail-open 放行。

    三级：
      ① 结构化 `判定: …`（首选）；
      ② 回退扫逐条核查行 —— 任意 `R<n> … 命中`（非「未命中」）→ FAIL + 由 R 序号定类别；
         九条全在且都「未命中」→ PASS（弱模型偶尔不写结论行时不至于漏放）；
      ③ 极短回复才认裸词（SKIP/FAIL/PASS），避免把正常台词里的字眼误判成结论；
      ④ 都判不出 → 放行（fail-open），raw 留痕。
    """
    t = (raw or "").strip()
    if not t:
        return Verdict(ok=True, category="review_empty", parsed=False, raw="")

    m = _VERDICT_RE.search(t)
    if m:
        g = m.group(1).strip()
        if g.upper() in ("PASS", "SEND") or g in ("通过", "放行", "合格"):
            return Verdict(ok=True, category="llm_pass", parsed=True, raw=t[:200])
        cat = ""
        cm = _CAT_RE.search(t)
        if cm:
            c = cm.group(1).strip().lower()
            cat = c if c in HINTS else "review"
        rm = _REASON_RE.search(t)
        reason = rm.group(1).strip()[:120] if rm else ""
        wm = _REWRITE_RE.search(t)
        rewrite = wm.group(1).strip()[:80] if wm else ""
        return Verdict(ok=False, category=cat or "review", reason=reason,
                       rewrite=rewrite, parsed=True, raw=t[:200])

    # ② 回退：逐条核查行
    hits = _R_HIT_RE.findall(t)
    r_lines = set(re.findall(r"R\s*(\d{1,2})\s*[^\n]{0,6}?[:：]", t))
    if hits:
        idx = hits[0]
        cat = _R_CATEGORY.get(idx, "review")
        rm = _REASON_RE.search(t)
        reason = (rm.group(1).strip()[:120] if rm else "") or ("逐条核查命中 R%s" % idx)
        return Verdict(ok=False, category=cat, reason=reason, parsed=True, raw=t[:200])
    if r_lines >= {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}:
        return Verdict(ok=True, category="llm_pass_checklist", parsed=True, raw=t[:200])

    # ③ 裸词（仅限极短回复）
    if len(t) <= 24:
        if _FAIL_TOKENS.search(t):
            return Verdict(ok=False, category="review", reason=t[:80], parsed=True, raw=t[:200])
        if _PASS_TOKENS.search(t):
            return Verdict(ok=True, category="llm_pass", parsed=True, raw=t[:200])

    # ④ fail-open
    return Verdict(ok=True, category="review_unparsed", parsed=False, raw=t[:200])


def build_retry_feedback(category: str = "", reason: str = "", rewrite: str = "") -> str:
    """把驳回结论翻成一段回灌给**生成器**的修正指令（下一轮 prompt 追加）。"""
    lines = ["\n\n【上一句没通过审查，请重写一句】**驳回归类**：%s" % (category or "不合格")]
    if reason:
        lines.append("驳回原因：%s" % reason)
    if rewrite:
        lines.append("改写方向：%s" % rewrite)
    hint = HINTS.get(category or "", "")
    if hint:
        lines.append("硬性修正：%s" % hint)
    lines.append("仍然只输出重写后的那一句话本身，不要解释。")
    return "\n".join(lines)


async def review(llm: Any, *, text: str, sit_text: str = "", brief: str = "",
                 kind: str = "", name: str = "", timeout: float = 20.0,
                 max_tokens: int = 420, enable_thinking: Optional[bool] = None) -> Verdict:
    """让审查 LLM 判一次。任何异常 / 不可解析 → fail-open 放行（附类别留痕）。

    `max_tokens` 给足：审查是"逐条核查 + 结论"，回复比一句台词长得多；
    给太少会被截断在结论行之前（解析器有 R 行兜底，但仍不如拿到结论可靠）。
    `enable_thinking`：None = 不传（厂商默认）；推理模型务必显式传 False，
    否则思维链会吃光 `max_tokens` 导致正文为空 → 审查形同虚设（实测踩过）。
    """
    if llm is None:
        return Verdict(ok=True, category="no_llm", parsed=False)
    prompt = build_review_prompt(text, sit_text, brief, kind, name)
    opts: dict = {"temperature": 0.0, "max_tokens": max_tokens}
    if enable_thinking is not None:
        opts["enable_thinking"] = bool(enable_thinking)
    try:
        raw = await asyncio.wait_for(
            llm.chat([{"role": "user", "content": prompt}], options=opts),
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 闸门故障绝不闭麦
        logger.info("[proactive_pack] 审查器异常，按放行处理：%s", e)
        return Verdict(ok=True, category="review_error", reason=str(e)[:80], parsed=False)
    v = parse_verdict(raw if isinstance(raw, str) else str(raw or ""))
    if not v.parsed:
        logger.info("[proactive_pack] 审查输出无法解析（放行）：%r", (raw or "")[:80])
    return v


# ---- 审查器独立客户端（可选） --------------------------------------------

class MiniOpenAIClient:
    """极小的 OpenAI 兼容客户端（stdlib urllib + 线程），给"审查器专用模型"用。

    为什么自建而不复用主 LLM：**审查器需要的模型能力与生成器不同**——
    实测 glm-4-flash 做审查只有 3/10（编造/自曝身体/万能句全放行），必须换更强的模型。
    而项目 `text_chat_router` 是按"当前聊天端口"走、**不支持按调用换模型**，
    所以这里给审查器留一个独立入口：`judge_provider` 指向 config.yaml 里任一端口即可。
    依赖只用标准库，保持本包"零第三方依赖"。
    """

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 timeout: float = 40.0, max_tokens: int = 420) -> None:
        self._base = (base_url or "").rstrip("/")
        self._key = api_key or ""
        self._model = model or ""
        self._timeout = float(timeout or 40.0)
        self._max_tokens = int(max_tokens)

    @property
    def available(self) -> bool:
        return bool(self._base and self._model)

    def __repr__(self) -> str:  # 排障友好
        return "MiniOpenAIClient(%s @ %s)" % (self._model, self._base)

    def _thinking_body(self, options: Optional[dict]) -> dict:
        """把 options["enable_thinking"] 翻译成厂商字段（与项目 openai_client 同口径）。

        不传 = 保持厂商默认。**推理模型（如 glm-4.5 系）默认会先吐一堆思维链**，
        在 `max_tokens` 不够时正文会是空的 —— 那会被判成"审查器不可用"→ fail-open，
        整个闸门形同虚设（这正是实测踩过的坑）。
        """
        want = (options or {}).get("enable_thinking")
        if want is None:
            return {}
        if "dashscope.aliyuncs.com" in self._base:
            return {"enable_thinking": bool(want)}
        if "open.bigmodel.cn" in self._base:
            return {"thinking": {"type": "enabled" if want else "disabled"}}
        return {}

    async def chat(self, messages: list, options: Optional[dict] = None) -> str:
        if not self.available:
            raise RuntimeError("审查器模型未配置（base_url/model 为空）")
        import json as _json
        import urllib.request as _url
        opts = options or {}
        prompt = "\n".join(str(m.get("content") or "") for m in (messages or []))
        body_d: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": float(opts.get("temperature", 0.0)),
            "max_tokens": int(opts.get("max_tokens") or self._max_tokens),
        }
        body_d.update(self._thinking_body(opts))
        body = _json.dumps(body_d).encode("utf-8")
        req = _url.Request(
            self._base + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})

        def _call() -> str:
            with _url.urlopen(req, timeout=self._timeout) as r:
                d = _json.loads(r.read().decode("utf-8"))
            msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
            # 推理模型的正文在 content；某些实现只在 reasoning_content 有内容 → 兜底取用，
            # 否则会被误判成"空回复"（=审查器不可用 = 全部放行）。
            return str(msg.get("content") or msg.get("reasoning_content") or "")

        return (await asyncio.to_thread(_call)).strip()
