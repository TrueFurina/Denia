"""长期记忆库：把用户纠正/偏好等"学到的"持久化保存，跨会话注入上下文。

原理：对话历史是滑动窗口（旧对话会被冲掉），长期记忆独立存到磁盘，
每次对话 / 主动话术都重新注入——上下文再压缩、窗口再滑动也不会丢。

两种方案（mode 可切换，UI 开关对比）：
- legacy：原版扁平记忆，按最近更新召回（对比用）
- jarvis：借鉴开源项目 isair/jarvis 的 purpose-driven 记忆——
  1. 记忆分类：directive(用户明确指令,逐字) / user(偏好习惯) / world(外部事实)
  2. 指令特权注入：【用户明确说过的要求（必须逐字遵守）】块永远在最前
  3. 重要性加权：被反复提及(access hits) 的记忆排前面，不是只看最新
  4. 语义检索（可选）：nomic-embed-text 本地嵌入，按相关性召回
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---- 进程级单例（A-4，2026-10-01）----
# 长期记忆库默认由 dialogue / proactive 各自持有同一实例；agent_brain 等操作路径不一定拿得到引用。
# 这里提供登记口，dialogue 初始化时登记，其它模块惰性取用，从而把"操作/语音"路径学来的事实
# 也写进同一份长期记忆——这正是"操作大脑记忆孤岛"的最后一处补丁。
_MEMORY_STORE_SINGLETON: "Optional[MemoryStore]" = None


def set_memory_store(store: "MemoryStore") -> None:
    global _MEMORY_STORE_SINGLETON
    _MEMORY_STORE_SINGLETON = store


def get_memory_store() -> "Optional[MemoryStore]":
    return _MEMORY_STORE_SINGLETON


# 记忆类型：directive=用户明确指令（逐字保存，特权注入）；user=偏好习惯；world=外部事实
# 【2026-10-11 新增 episode】情节记忆：把"一段对话/一个话题"提炼成**带细节的摘要**
#   （时间/地点/人物/事件/情绪），治"只记 30 字扁平事实、总结不出细节"。
#   与 user（扁平画像）分工：user=「用户喜欢摄影」，episode=「10月10日晚他聊到国庆
#   用尼康Z52拍了照片、说等会发给你看，语气挺高兴」。episode 允许更长（≤200字）。
MEMORY_TYPES = ("directive", "user", "world", "episode")

# 各类型长度上限：episode 是摘要，允许比扁平事实长得多
MEMORY_MAX_LEN = {"directive": 80, "user": 80, "world": 120, "episode": 200}

# 【2026-10-01 C】长期记忆 TTL：默认 180 天，超期进入冷归档（置信衰减、不直接删）。
DEFAULT_TTL_DAYS = 180

# 来源白名单（provenance）：chat=打字 / duplex=双工语音 / operation=操作电脑
MEMORY_SOURCES = ("chat", "duplex", "operation", "user")

# 用户这句话疑似包含"纠正/偏好/事实"（值得长期记住）→ 才触发 LLM 提炼
EXTRACT_HINT_RE = re.compile(
    # —— 原有：明确指令/纠正/偏好/习惯 ——
    r"不是|错了|错啦|不对|别|不要|其实|喜欢|讨厌|记住|以后|叫我|别叫|"
    r"我玩|我在玩|我是|我不玩|老是|每次|从来不|都说了|记住了吗|懂了吗|"
    # —— 【2026-10-04 21:05 补·委婉说法】实锤：用户说"能不能换成正常的漂泊者也可以"
    # 属**明确的称呼指令**，但一个字都没命中上面那组（没有"叫我/别叫"），
    # ⇒ 提炼不触发 → 达妮娅只在通话里口头答应"以后叫你漂泊者"，
    #    **记忆库里没有这条** → 下一通/下次聊天又叫回"鸡仔"（用户 21:03 实测）。
    # 中文里表达"改称呼/改设定"还有很多等价说法，一并覆盖：
    r"能不能换|能否换|可不可以换|换个称呼|换称呼|改称呼|改成|换成|"
    r"称呼|昵称|叫我|喊我|管我叫|不要叫|别再叫|"
    # —— 偏好/习惯的委婉说法（"我不太喜欢…"、"我不想要…"）——
    r"我不喜欢|我不想要|我不吃|我不看|我不玩|我怕|我讨厌|我受不了|"
    # —— 设定类（"以后都…"、"默认…"）——
    r"以后都|以后请|以后就|默认叫|以后用"
    # 【2026-10-04 22:15 新增·日常分享/经历/打算】用户 22:00 实锤：通话里聊了
    # "尼康Z52 挺好的""昨天出去拍了照片""国庆出去旅游了""我等会发给你看"，
    # 这些**全是值得记住的内容**，但**没有一句命中**上面任何触发词 ⇒ 提炼从不触发
    # ⇒ 记忆库只有 3 条（称呼+相机偏好），她 `recall` 时压根想不起"要看照片"
    # ⇒ 用户 22:0x 问"你还记得你要干嘛吗"她答不上来。
    # 现在覆盖"我的经历/打算/偏好陈述"这一整类（这也是主流 AI 伴侣
    # Memory Anchoring 提示词的核心：抓住用户主动讲的事，别只记指令）。
    r"我昨天|我今天|我前天|我这周|我上周|我最近|我刚才|我刚|"
    r"我出去|我去了|我到了|我玩|我在|我买|我吃|我喝|我拍|我看了|我买了|"
    r"我觉得|我感觉|我挺|我好喜欢|我最喜欢|我打算|我准备|我计划|"
    r"我想去|我想看|我想试|我打算|我会|我下次|改天|回头|"
    # 物品/作品/经历分享（发照片、发链接、讲一件事）
    r"发给你|发你|给你看|你看一下|我拍了|拍了|照片|图片|截图|拍了几张|"
    r"旅游|出去玩|出去玩|逛街|景区|景点|门票|相机|镜头|拍得"
)

# LLM 提炼 prompt（jarvis 方案）：一次返回 JSON，判定 + 分类 + 提炼
MEMORY_EXTRACT_PROMPT = """你在陪一个正在用电脑（可能玩游戏）的用户聊天。用户刚刚说了一句话。

判断这句话是否包含**值得长期记住的内容**，如果包含，把它分类并提炼：
- type=directive：用户给你的**明确要求/约定/纠正**（如"别叫我宝贝""以后叫我XX""我在打烽火地带不是摸金"）。
  fact 必须**逐字**保留用户的原意，不要改写、不要总结，这是用户要求你遵守的规矩。
- type=user：关于用户的**偏好、习惯、身份、经历**（如"用户喜欢打射击游戏""用户是上班族"）。
  fact 提炼成以"用户"为主语的简短句（不超过 30 字）。
- type=world：你学到的**外部事实**（如"烽火地带是三角洲行动的一个模式"）。

**【2026-10-04 22:20 必记项·用户 22:00 实锤】**除上述三类，下面这些**也必须记**
（它们看起来像"临时状态"，但恰恰是用户最指望你记住的）：
- **用户与你的未决约定/待办**：他说过要做、要给你、还没做的事
  （如"我等会发照片给你""我明天把文件发你""我回头告诉你"）—— 记成
  "用户说等会发照片给达妮娅看"。**这类丢了最伤**：他会以为你记着、你却完全忘了
  （22:00 实锤：他问"你还记得你要干嘛吗"，她答不上来）。
- **用户提到的、想让你之后聊的东西**：想去的地方、在看的装备、在拍的项目等。

**不要记录**一次性/临时状态（如"用户刚才阵亡了""用户今天很累""用户正在观战"）——
这些会很快过时，不是值得跨会话保留的长期记忆。反问句、疑问句（如"我不是死了吗"）通常不值得记录。

**禁止编造关键词**：fact 只能使用用户话里**明确出现**的词。严禁添加、替换或猜测用户没提到的名词
（游戏模式名、地名、物品名、称呼等）。用户说了你不认识的词（如"薯窝"），就**原样保留**这个词，
严禁改成"红薯窝"或换成别的游戏名。如果你必须新增或改写任何名词才能成句，说明提炼不可靠，
应输出 should_extract=false。负向表达（"不是XX"）尽量保留原样，不要自行总结成"用户在玩XX"。

只输出一行 JSON，不要任何其他内容：
{"should_extract": true, "type": "directive|user|world", "fact": "..."}
没有值得长期记住的内容时输出：{"should_extract": false, "type": "user", "fact": ""}

用户说：{text}"""


# A-4（2026-10-01）：从「操作结果 / 语音片段」批量提炼稳定事实的 prompt 与解析器。
# 与 MEMORY_EXTRACT_PROMPT 区别：后者面向单句用户话（JSON 判定+分类），
# 本 prompt 面向一段对话片段，直接产出事实数组（操作/双工路径触发用）。
MEMORY_FACTS_PROMPT = """你是一个长期记忆提炼器。下面是一段用户与 AI 伴侣的对话片段（可能是用户的一条操作指令及其执行结果，也可能是语音聊天内容）。
请从中提取**值得跨会话长期记住**的、关于用户本人的稳定事实（偏好、习惯、身份、约定、被纠正的点），每条不超过 30 字。
【不要记】一次性/临时状态（如"刚在打游戏""今天很累"）、反问句、以及用户原话的简单复述。
只输出一行 JSON 数组（不要任何其它内容），数组里每项是一个字符串事实；若没有任何值得长期记住的内容，输出 []。
注意：fact 只能使用用户话里**明确出现**的词，禁止编造或替换名词。
片段：
{text}"""


# ══════════════════════════════════════════════════════════════════════════
# 【2026-10-11 新增】会话级情节记忆提炼（治"总结不出细节"）
# ══════════════════════════════════════════════════════════════════════════
# 为什么需要：现有 MEMORY_EXTRACT_PROMPT 面向**单句**、强制 ≤30 字、以"用户"为主语
#   → 只能产出"用户喜欢打射击游戏"这种**没有细节的扁平事实**（实测 11 次提炼
#   产出 0 条）。用户诉求"总结出细节"，本质是要**情节记忆**：
#   保留"何时/和谁/做了什么/什么感受"。
# 故新增本 prompt：输入是**一段对话**（而非一句），产出一条**带细节的摘要**
#   （≤200 字），存为 type=episode。它可与"扁平事实"并存、互不替代。
MEMORY_EPISODE_PROMPT = """你是一个长期记忆提炼器。下面是你（AI 伴侣）与用户**最近一段对话**。
请把它归纳成 **1~3 条"情节记忆"**：即"你们之间发生过的一件事"，要**保留具体细节**。

每条包含（能写就写，没有就略过，**不要编造**）：
- 什么时候（今天/昨晚/上周末…，用对话里出现的时间或时段即可）
- 用户做了什么 / 聊了什么 / 说了什么打算
- 涉及的人、事、物、地点（用他原话里的名词，**不要替换或猜测**）
- 他的情绪或态度（高兴/抱怨/随口一提…）

【要求】
- 每条 ≤ 200 字，**以"记录你们这段关系"的口吻写**（如"10月10日晚，他跟我聊到…"）。
- **要细节，不要空话**：把"他聊了摄影"写成"他跟我说国庆出去用尼康Z52拍了照片，说等会发给我看"。
- **只记"事"，不记当场的小状态**（"他现在有点困"这种不要）——但"他说这周要早点睡"要记（是打算）。
- 如果这段对话**确实没有任何值得记住的事**（纯寒暄/纯问答/纯操作指令），输出 []。
- 严格使用原文出现的名词；不得编造。

只输出一行 JSON 数组，每项 {"fact": "...", "type": "episode"}；没有则输出 []。
不要任何解释、不要 markdown。

对话片段：
{text}"""


# ══════════════════════════════════════════════════════════════════════════
# 【2026-10-11 新增】周期性反思聚合（Plan B，Generative Agents 式）
# ══════════════════════════════════════════════════════════════════════════
# 为什么需要：episode（情节记忆）是**零散事件**（"10月8日聊了相机""10月10日说要去旅游"），
#   光有一堆事件，"她"仍**对用户没有一个成形的整体认知**。Generative Agents 的
#   reflection：周期性把近期事件**聚合成更高层洞察**，例如把
#     "喜欢尼康""爱拍风景""国庆去旅游拍照"
#   聚合成 → "用户在摄影上投入很多，喜欢用镜头记录生活，是个注重生活细节的人"。
# 故新增本 prompt + `reflect_episodes()`：给一批 episode，产出 1~3 条**画像级洞察**
#   （存 type=user，与既有扁平事实并存、互为补充）。
MEMORY_REFLECT_PROMPT = """你是一个长期记忆的"反思聚合器"。下面是 AI 伴侣（你）与用户**近期积累的一些交往片段**（情节记忆）。
请从中提炼 **1~3 条更高层的"对用户的认识"**——不是复述事件，而是**归纳出关于这个人的稳定特质/倾向/在乎的事**。

【要求】
- 每条 ≤ 60 字，以"用户"为主语，写成**画像级判断**（而不是"他在X那天做了Y"）。
  · 好：用户很在意摄影，喜欢用镜头记录生活细节。
  · 差：用户10月8日聊了尼康相机。
- **要基于给定片段**：可以归纳（"多次提到摄影"→"很在意摄影"），但**不得编造**片段里没有依据的内容。
- 若这些片段还不足以归纳出任何稳定特质（太零散/纯寒暄），输出 []。
- 严格使用片段里出现的名词，不替换、不猜测。

只输出一行 JSON 数组，每项 {"fact": "...", "type": "user"}；没有则输出 []。
不要任何解释、不要 markdown。

近期片段：
{text}"""


def _new_mem_id() -> str:
    """【2026-10-13】记忆条目稳定短 id（UI 单条删除/定位用）。"""
    return uuid.uuid4().hex[:12]


def _norm_fact(s: str) -> str:
    return re.sub(r'[\s\W_]+', '', (s or ''), flags=re.UNICODE).lower()

def _parse_fact_list(raw: str) -> list[str]:
    """从 LLM 输出鲁棒解析事实数组（容忍前后多余文字 / 退化成逐行）。"""
    raw = (raw or '').strip()
    if not raw:
        return []
    m = re.search(r'\[.*\]', raw, re.S)
    cand = m.group(0) if m else raw
    try:
        obj = json.loads(cand)
    except Exception:  # noqa: BLE001
        obj = None
    if isinstance(obj, list):
        return [str(x).strip() for x in obj if str(x).strip()]
    # 退路：按行拆
    return [ln.strip(' "\'-') for ln in raw.splitlines()
            if ln.strip() and ln.strip() not in ('[]', '[')]


def _parse_fact_items(raw: str) -> list[tuple[str, str]]:
    """解析 LLM 输出为 `[(fact, type)]`，**保留 LLM 判定的分类**。

    【2026-10-04 21:10 修·分类被丢弃】旧 `_parse_fact_list` 用 `str(x)` 把每个条目
    拍平成字符串 ⇒ LLM 认真判定的 `type`（directive/user/world）在解析时就丢了；
    紧接着 `store_extracted_facts` 又写死 `store.add(..., type="user")` ⇒ **两次
    丢弃**，结果是"改称呼"这种本该 `directive`（置顶 + 逐字遵守 + 参与指令更替）
    的指令，被当成普通偏好存成 `user`，白白降级。
    本函数保留 type；`should_extract`/`type` 缺失时按"指令优先、其次偏好"兜底。
    """
    raw = (raw or '').strip()
    if not raw:
        return []
    m = re.search(r'\[.*\]', raw, re.S)
    cand = m.group(0) if m else raw
    obj = None
    try:
        obj = json.loads(cand)
    except Exception:  # noqa: BLE001
        obj = None
    out: list[tuple[str, str]] = []
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                f = str(x.get("fact") or x.get("text") or "").strip()
                t = str(x.get("type") or "").strip().lower()
                if f and t in MEMORY_TYPES:
                    out.append((f, t))
                elif f:
                    out.append((f, _guess_fact_type(f)))
            else:
                f = str(x).strip()
                if f:
                    out.append((f, _guess_fact_type(f)))
        if out:
            return out
    # 退路：按行拆（无 type，用启发式猜）
    for ln in raw.splitlines():
        f = ln.strip(' "\'-')
        if f and f not in ('[]', '[', '{', '}'):
            out.append((f, _guess_fact_type(f)))
    return out


def _guess_fact_type(fact: str) -> str:
    """无 type 时的兜底分类：**指令 > 偏好 > 外部事实**。"""
    t = fact or ""
    if re.search(r"以后|不要|别|必须|要求|记住|叫我|别叫|默认|不许|只能", t):
        return "directive"
    if re.search(r"喜欢|讨厌|偏好|习惯|爱吃|爱玩|常用", t):
        return "user"
    return "world"


def store_extracted_facts(store: "MemoryStore", raw_llm: str,
                          source_texts: list[str], source_label: str = "operation",
                          character: str = "") -> int:
    """解析 LLM 返回的事实数组并写入长期记忆；自动剔除只是复述源文本的条目。

    返回实际新增条数。store 为 None 时安全返回 0。复述判定与 dialogue._is_user_echo 同思路，
    避免 A 步已修掉的"说什么就记一段、之后照着复述"旧疾在操作/语音路径复发。

    【2026-10-04 21:10 修】**保留 LLM 判定的 type**（此前解析丢一次、写入又写死
    `user` 二次丢弃，导致"改称呼"这类 directive 被降级成普通偏好）。
    """
    if store is None or not raw_llm:
        return 0
    items = _parse_fact_items(raw_llm)
    if not items:
        return 0
    src_norms = [_norm_fact(t) for t in (source_texts or [])]
    added = 0
    for f, ftype in items:
        fn = _norm_fact(f)
        if len(fn) < 3:
            continue
        is_echo = False
        for sn in src_norms:
            if not sn:
                continue
            if fn == sn or (len(fn) >= 4 and fn in sn) or (len(sn) >= 4 and sn in fn):
                is_echo = True
                break
        if is_echo:
            continue
        try:
            if store.add(f[:40], type=ftype, source=source_label, character=character or ""):
                added += 1
        except Exception:  # noqa: BLE001
            pass
    if added:
        logger.info("长期记忆新增(来自%s): %d 条", source_label, added)
    return added


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（向量检索排序用）。"""
    try:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)
    except Exception:  # noqa: BLE001
        return 0.0


def _normalize_fact(text: str) -> str:
    """记忆去重归一化：大小写折叠 + 空白折叠。"""
    return re.sub(r"\s+", "", (text or "").casefold())


# 【2026-10-11】跨类型合并保护：合并只应发生在**同类型**条目之间。
#   实测：test_memory_episode 场景
#     add("用户喜欢摄影", type="user")
#     add("用户喜欢摄影这件事他今天又提了", type="episode")   ← 归一化后前者是后者子串
#   旧 `add()` 二级"互为包含 → 新覆盖旧"只按文本比，不看 type ⇒ episode 被合并进
#   user 且把旧条 type 改写成 "episode"，两类型互相污染：user 记忆消失、episode
#   内容变成"用户喜欢摄影"这类**扁平事实**（正是用户抱怨"总结不出细节"的一种表现）。
#   现在：只有当两条 **type 相同** 时才允许近似合并；跨类型一律视为两条独立记忆。
def _same_kind_for_merge(a: str, b: str) -> bool:
    return (a or "") == (b or "")


# ===== 指令槽位（directive slot）—— 用于"新指令覆盖旧指令" =====
# 【2026-10-04 21:08】用户改了称呼/语言/作息这类"单值设定"时，新旧两条措辞几乎
# 一定不同（"默认用这个昵称" vs "用这个称呼"），字符串去重永远命中不了 ⇒ 两条
# directive 同时置顶、互相矛盾，模型随机取一个（21:03 实锤：说了"叫你漂泊者"
# 却又叫"漂泊者"）。这里给每类**单值设定**一个槽位名，同槽位的新指令自动作废旧条。
# ⚠️ 只收"单值型"设定；"不要做X"这类可并存的多条禁令**不设槽位**（避免误杀）。
_DIRECTIVE_SLOTS = (
    # 语言
    ("语言", ("说中文", "说英文", "用中文", "用英文", "语言", "英文", "日文", "韩文")),
    # 作息/时间
    ("作息", ("几点睡", "起床", "熬夜", "作息", "早睡")),
    # 单位/制式
    ("单位", ("用公里", "用英里", "摄氏度", "华氏度", "单位")),
    # 回复风格
    ("风格", ("说话风格", "回复风格", "说话方式", "简短一点", "详细一点", "别太啰嗦")),
    # 称呼以外的"人设关系"
    ("关系", ("把我当", "当作朋友", "当作哥哥", "当作妹妹")),
)

# 【2026-10-05 00:45 修"称呼记混"】称呼拆成**两个独立槽位**：
#   称呼·AI名字 = 用户给 AI 起的名字（"以后叫你娅娅"）
#   称呼·用户   = AI 对用户的称呼（"以后叫我漂泊者"）
# 实锤（00:45 用户报告）：用户先说"叫我漂泊者"、后说"叫你娅娅"——旧逻辑把两条
# 都归进同一个「称呼」槽 ⇒ 娅娅那条把漂泊者那条**作废**了 ⇒ 她既忘了该叫用户
# 什么、又把"用户叫我娅娅"念反成"我叫用户娅娅"。
_AI_NAME_RE = re.compile(
    r"(?:叫|喊|称|称呼)(?:你|您)|(?:给你|给你自己|帮你)(?:起名|改名|取名)"
    r"|你(?:的名字|本名)(?:是|叫|改为)|改叫你")
_USER_NAME_RE = re.compile(
    r"(?:叫|喊|称|称呼)(?:我|我们)|被称呼|称呼他|称呼她|我(?:的称呼|的昵称|的名字)"
    r"|对我(?:的)?(?:称呼|叫法)")

# 称呼规范句的**槽位标签前缀**（normalize_addressing 生成；_directive_slot 优先识别）
_SLOT_TAG_AI = "【称呼·AI名字】"
_SLOT_TAG_USER = "【称呼·用户】"

# 归一化时的"伪名字"黑名单：这些不是名字，别把动词短语抓成称呼
_NAME_STOP = {"什么", "啥", "怎么", "怎样", "别", "不", "少", "一下", "过来",
              "出去", "帮忙", "看看", "等等", "先", "再", "这样", "那样"}
# 名字首字禁用（"叫你少管闲事/别磨蹭"这类祈使句）
_NAME_BAD_HEAD = "少别不没快慢先再莫休"
_NAME_BAD_SUB = ("什么", "怎么", "样", "闲事", "管")


def normalize_addressing(text: str) -> str:
    """把用户口述的称呼句改写成**无代词歧义**的规范记忆句（不匹配原样返回 ""）。

    根因（00:45 实锤）：`remember`/提取链路把整句存进去——"能不能以后叫你娅娅"
    存成"用户希望以后称呼我为「娅娅」"。句子里全是 我/你 相对代词：谁称呼谁
    完全取决于"这句话是谁写的视角"，模型召回时把方向念反（她说成"我叫你娅娅"）。
    ⇒ 写入库**之前**就把方向钉死：AI 的名字用"用户叫我「X」"，用户的称呼用
    "我称呼他「X」"，并打上槽位标签（同槽位更替由此生效，且两槽互不干扰）。
    """
    t = (text or "").strip()
    if not t or len(t) > 60:
        return ""
    def _ok_name(nm: str) -> bool:
        nm = nm.strip("的了吗呢吧呀～~ ")
        return bool(nm) and nm not in _NAME_STOP \
            and nm[0] not in _NAME_BAD_HEAD \
            and not any(w in nm for w in _NAME_BAD_SUB)

    m = (re.search(r"(?:以后|今后)?(?:就)?(?:叫|喊|称|称呼)(?:你|您)(?:做|为|叫)?\s*[「『\"']?([\u4e00-\u9fa5A-Za-z0-9·]{1,8})[」』\"']?", t)
         or re.search(r"(?:给你|给你自己|帮你)(?:起名|改名|取名)(?:叫|为)?\s*[「『\"']?([\u4e00-\u9fa5A-Za-z0-9·]{1,8})[」』\"']?", t)
         or re.search(r"你(?:以后)?(?:就)?(?:叫|是|叫做)\s*[「『\"']?([\u4e00-\u9fa5A-Za-z0-9·]{1,8})[」』\"']?", t))
    if m and _ok_name(m.group(1)):
        name = m.group(1).strip("的了吗呢吧呀～~ ")
        return (f"{_SLOT_TAG_AI}用户（他）给我的名字/称呼是「{name}」——他叫我「{name}」，"
                f"我自我介绍、被叫时都用「{name}」，不再用角色卡默认名。")
    m = (re.search(r"(?:以后|今后)?(?:就)?(?:叫|喊|称|称呼)(?:我|我们)(?:为|做|是|叫)?\s*[「『\"']?([\u4e00-\u9fa5A-Za-z0-9·]{1,8})[」』\"']?", t)
         or re.search(r"(?:给我|给我自己)(?:起名|改名|取名)(?:叫|为)?\s*[「『\"']?([\u4e00-\u9fa5A-Za-z0-9·]{1,8})[」』\"']?", t)
         or re.search(r"我(?:的名字|的称呼|的昵称)(?:是|叫|改为)\s*[「『\"']?([\u4e00-\u9fa5A-Za-z0-9·]{1,8})[」』\"']?", t))
    if m and _ok_name(m.group(1)):
        name = m.group(1).strip("的了吗呢吧呀～~ ")
        return (f"{_SLOT_TAG_USER}用户希望我称呼他「{name}」——我叫他「{name}」，"
                f"对话里默认用这个称呼。")
    return ""


def _directive_slot(text: str) -> str:
    """判断一条 directive 属于哪个"单值设定槽位"；不属于任何槽位则返回 ""。

    只用于**同槽位互相取代**；返回 "" 表示该指令可与其它并存。
    【2026-10-05】称呼拆成 称呼·AI名字 / 称呼·用户 两个独立槽位（见上方注释）：
    给 AI 起名字**不再作废**"AI 该怎么称呼用户"，反之亦然。
    """
    t = (text or "").strip()
    if not t:
        return ""
    # 规范句的槽位标签最优先（normalize_addressing 生成）
    if t.startswith(_SLOT_TAG_AI):
        return "称呼·AI名字"
    if t.startswith(_SLOT_TAG_USER):
        return "称呼·用户"
    has_ai = bool(_AI_NAME_RE.search(t))
    has_user = bool(_USER_NAME_RE.search(t))
    if has_ai and not has_user:
        return "称呼·AI名字"
    if has_user and not has_ai:
        return "称呼·用户"
    if has_ai and has_user:
        # 双向都沾（如"我叫你X你叫我Y"）→ 独立成槽，不与单向指令互相作废
        return "称呼·双向"
    for slot, keys in _DIRECTIVE_SLOTS:
        for k in keys:
            if k in t:
                return slot
    # 兜底：老逻辑的「称呼」宽关键词（称呼/昵称/名字/叫我/喊我…）命中且无方向
    # 证据时，归入「称呼·含糊」——仍会取代**同为含糊**的旧条，但不动两个明确槽。
    for k in ("称呼", "昵称", "叫我", "喊我", "管我叫", "怎么称呼", "名字"):
        if k in t:
            return "称呼·含糊"
    return ""


class MemoryStore:
    """持久化长期记忆（JSON 文件）。线程安全（锁 + 原子写）。

    mode="legacy" 走原扁平逻辑；mode="jarvis" 走分类 + 重要性 + 语义检索。
    embed_fn: 可选的同步文本嵌入函数（返回 list[float]），用于语义召回。
    """

    def __init__(
        self,
        path: str = "",
        max_items: int = 100,
        mode: str = "jarvis",
        embed_fn: Optional[Callable[[str], list[float]]] = None,
        auto_trim: bool = False,
        max_items_per_character: int = 0,
    ) -> None:
        if not path:
            # 默认存到项目 data/memory.json
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.join(root, "data", "memory.json")
        self._path = path
        self._max_items = max(1, int(max_items))
        # 【2026-10-17 开发者要求】默认**不自动清理**记忆（超上限也不自动删条目）。
        # 若显式开启 auto_trim，则按「每个角色各算一份额度」裁剪 —— 谁都不会挤掉谁。
        # max_items_per_character=0 ⇒ 每个角色回落全局 _max_items。
        self._auto_trim = bool(auto_trim)
        self._max_per_char = max(0, int(max_items_per_character or 0))
        self._mode = "legacy" if mode == "legacy" else "jarvis"
        self._embed_fn = embed_fn
        # 【2026-10-01 C】改用可重入锁：stats() 在持锁内调用 count_by_type/count_by_source
        # （二者也各自加锁），非重入 Lock 会自死锁。RLock 对本场景零副作用。
        self._lock = threading.RLock()
        self._memories: list[dict[str, Any]] = []
        # 【2026-10-01 A-5】当前角色作用域。空串=不隔离（兼容旧行为）。
        # dialogue 与 proactive 共享同一个 MemoryStore 实例，故只需在 switch_character
        # 时 set_character 一次，打字与语音两条路径即同时生效。
        self._character: str = ""
        # 【2026-10-16】无 character 字段的"旧(无归属)记忆"归谁：首次 set_character 时确定，
        # 之后只对该角色可见（作用域规则见 recall / list_user_facts / recent_episodes）。
        self._legacy_owner: str = ""
        self._load()
        self._migrate()

    # ---------- 配置 ----------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = "legacy" if mode == "legacy" else "jarvis"

    def set_embed_fn(self, fn: Optional[Callable[[str], list[float]]]) -> None:
        self._embed_fn = fn

    # ---------- 角色作用域（2026-10-01 A-5）----------
    def set_character(self, char_id: str) -> None:
        """设定当前角色。写入的新记忆归属该角色；召回只取「本角色」（无归属旧记忆仅归 legacy owner）。

        传空串 = 关闭隔离（恢复旧行为，取全部记忆）。
        """
        with self._lock:
            self._character = (char_id or "").strip()
            # 【2026-10-16 修复·记忆串味】把"无归属"的旧记忆一次性盖上归属
            # （= 首个设定的角色，通常就是主角色达妮娅）。否则它们会按旧规则注入给每个角色——
            # 实测：那条「用户希望我称呼他『漂泊者』」漏给了别的角色，表现为"换人后人设串了"。
            if self._character and not self._legacy_owner:
                self._legacy_owner = self._character
                try:
                    self._stamp_legacy_memories(self._legacy_owner)
                except Exception:  # noqa: BLE001
                    logger.debug("旧记忆归属盖章失败（不影响使用）", exc_info=True)

    def _stamp_legacy_memories(self, owner: str) -> int:
        """把没有 character 字段的旧记忆盖上 owner；返回盖章条数（一次性迁移）。"""
        n = 0
        for m in self._memories:
            if isinstance(m, dict) and m.get("text") and not (m.get("character") or "").strip():
                m["character"] = owner
                n += 1
        if n:
            try:
                self._save()
            except Exception:  # noqa: BLE001
                logger.debug("旧记忆归属迁移写盘失败（内存态已生效）", exc_info=True)
            logger.info("记忆归属迁移：%d 条无归属旧记忆已归到角色 %r（此后不再跨角色注入）",
                        n, owner)
        return n

    def get_character(self) -> str:
        return self._character

    def _same_scope(self, m: dict, char: str) -> bool:
        """【2026-10-16 修复·跨角色偷记忆】候选记忆 m 是否与写入方同一角色作用域。

        **必须与读侧（`recall` / `list_user_facts` / `recent_episodes`）的可见性同口径**，
        否则会出现"写入时按全库去重、读取时按角色隔离"的错位：
          · 旧行为（本函数存在前）：一级去重 / 二级近似合并 / 三级指令更替
            **一律遍历全库**，于是角色 B 说出一句与角色 A 近似的记忆时，
            `m["character"] = char` 会把那条记忆从 A **悄悄搬走**（A 从此看不到）；
            完全相同的一句则被 `return False` 吞掉（B 永远学不到）。
            实测（`_ab_mem_regress.py` 修复前）：跨角色同句 → `added=[True, False]`；
            跨角色近似 → 库内仅 1 条且归属被改成新角色。
          · 新行为：只有"同域"才允许去重 / 合并 / 更替；跨角色一律走新增路径。

        同域判定 = 读侧可见性：
          · `char` 为空（未启用隔离）→ 全库同域（保持旧行为，兼容 legacy 模式）；
          · 记忆 owner == `char` → 同域；
          · 记忆 owner 为空 且 `char == _legacy_owner` → 同域（无归属旧记忆只归 legacy owner）；
          · 其余 → 不同域。
        """
        owner = (m.get("character") or "").strip()
        c = (char or "").strip()
        if not c:
            return True
        if owner == c:
            return True
        _lo = (self._legacy_owner or "").strip()
        return (not owner) and bool(_lo) and c == _lo

    def set_max_items(self, max_items: int,
                      max_items_per_character: Optional[int] = None) -> None:
        """运行时调整容量上限。

        【2026-10-17】默认**不自动清理**：仅当 auto_trim=True 时才裁剪，
        且裁剪按「每个角色各自上限」进行，不再全局淘汰。
        """
        self._max_items = max(1, int(max_items))
        if max_items_per_character is not None:
            self._max_per_char = max(0, int(max_items_per_character or 0))
        with self._lock:
            before = len(self._memories)
            self._maybe_trim()
            if len(self._memories) != before:
                self._save()

    def set_auto_trim(self, on: bool) -> None:
        """显式开关自动清理（默认关）。开启后按角色各自上限裁剪。"""
        self._auto_trim = bool(on)
        with self._lock:
            before = len(self._memories)
            self._maybe_trim()
            if len(self._memories) != before:
                self._save()

    def auto_trim(self) -> bool:
        return bool(self._auto_trim)

    def _cap_for(self, char: str) -> int:
        """某角色各自的条数上限；未单独设定时回落全局 _max_items。"""
        return self._max_per_char if self._max_per_char > 0 else self._max_items

    def _maybe_trim(self) -> None:
        """超上限检查入口。默认**不自动清理**（auto_trim=False）⇒ 直接返回。"""
        if not self._auto_trim:
            return
        groups: dict[str, int] = {}
        for m in self._memories:
            c = str(m.get("character") or "")
            groups[c] = groups.get(c, 0) + 1
        for c, n in groups.items():
            if n > self._cap_for(c):
                self._trim()
                return

    # ---------- 持久化 ----------
    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._memories = data.get("memories", [])
        except Exception:
            logger.warning("长期记忆加载失败，重置", exc_info=True)
            self._memories = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"memories": self._memories}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except Exception:
            logger.warning("长期记忆保存失败", exc_info=True)

    def _migrate(self) -> None:
        """兼容旧数据：无 type/hits 的旧条目迁移到 user 类型；补 C 步新增字段。"""
        changed = False
        now = time.time()
        for m in self._memories:
            if "type" not in m:
                m["type"] = "user"
                changed = True
            if "hits" not in m:
                m["hits"] = 1
                changed = True
            if "created_at" not in m:
                m["created_at"] = m.get("updated_at", now)
                changed = True
            # 【2026-10-01 C】补 ttl_days / invalid_at / confidence / source
            if "ttl_days" not in m:
                m["ttl_days"] = DEFAULT_TTL_DAYS
                changed = True
            if "invalid_at" not in m:
                m["invalid_at"] = 0
                changed = True
            if "confidence" not in m:
                m["confidence"] = 1.0
                changed = True
            # 【2026-10-13】补稳定 id：UI 单条删除/定位需要。旧数据无 id → 一次性补齐。
            if not m.get("id"):
                m["id"] = _new_mem_id()
                changed = True
            if "source" not in m:
                m["source"] = "user"
                changed = True
        if changed:
            self._save()

    # ---------- 写入 ----------
    def add(self, text: str, type: str = "user", source: str = "user",
            character: Optional[str] = None, ttl_days: Optional[int] = None,
            confidence: float = 1.0) -> bool:
        """添加一条记忆。

        去重分两级（2026-10-01 提质）：
          ① 完全相同 → 只刷新时间并累计 hits（重要性）；若旧条已被反悔(invalid_at)，
             重新提及视为"改口后又说回"，清除 invalid_at 复活该记忆。
          ② **互相包含的近似事实**（如「喜欢汽水音乐」vs「用户喜欢汽水音乐」）→ 视为同一条，
             按**新的覆盖旧的**（Mem0 式冲突解决），避免同一件事堆出好几条。
             仅对 >=6 字的条目启用，防止把「喜欢音乐」这类短句过度合并。
             已反悔的旧条不参与合并，保证"撤回后重新表达"能落成新记忆。
        character：归属角色；None=用当前 set_character 设定的作用域。
        ttl_days：有效期（天），默认 DEFAULT_TTL_DAYS；超期进冷归档不删。
        confidence：置信度（0~1），提取链路可靠性低时可下调，参与衰减。
        """
        text = (text or "").strip()
        if not text:
            return False
        if type not in MEMORY_TYPES:
            type = "user"
        # 【2026-10-05 修"称呼记混"】称呼类句子入库前先归一化：把"叫你X/叫我X"
        # 这类**相对代词句**钉死方向（谁称呼谁），并打上 称呼·AI名字/称呼·用户
        # 槽位标签——召回的模型再也不用猜"我"指谁，两槽也不会互相误作废。
        if type in ("directive", "user"):
            try:
                _canon = normalize_addressing(text)
            except Exception:  # noqa: BLE001
                _canon = ""
            if _canon:
                if text != _canon:
                    logger.info("称呼记忆归一化: %s → %s", text[:40], _canon[:56])
                text = _canon
        if source not in MEMORY_SOURCES:
            source = "user"
        norm = _normalize_fact(text)
        char = (character if character is not None else self._character or "").strip()
        ttl = int(ttl_days) if ttl_days else DEFAULT_TTL_DAYS
        conf = max(0.0, min(1.0, float(confidence)))
        _added = False
        with self._lock:
            now = time.time()
            for m in self._memories:
                # 【2026-10-16 修复·跨角色偷记忆】跨角色不允许"完全相同"去重：
                # 否则第二个角色永远学不到同一条事实（实测 added/stored=False）。
                if not self._same_scope(m, char):
                    continue
                if _normalize_fact(m.get("text", "")) == norm:
                    m["updated_at"] = now
                    m["hits"] = int(m.get("hits", 1)) + 1  # 被反复提及 → 更重要的记忆
                    if m.get("invalid_at"):  # 反悔后又被提及 → 复活
                        m["invalid_at"] = 0
                    self._save()
                    return False
            # 二级：互为包含的近似事实 → 新覆盖旧（含角色归属一并更新）
            # 【2026-10-11】`_same_kind_for_merge(type, m["type"])` 守卫：**同类型**才合并。
            #   否则 episode（"你们今天聊了摄影"）会被合并进 user（"用户喜欢摄影"）并
            #   继承 user 的 type，导致两类记忆互相污染（见 `_same_kind_for_merge` 注释）。
            if len(norm) >= 6:
                for m in self._memories:
                    if m.get("invalid_at"):  # 已反悔的不参与合并
                        continue
                    # 【2026-10-16 修复·跨角色偷记忆】近似合并**只允许同角色**：
                    # 旧实现在合并分支里写 `m["character"] = char`，跨角色时会把这
                    # 条记忆从原角色悄悄搬走（达妮娅从此看不到它）。见 `_same_scope`。
                    if not self._same_scope(m, char):
                        continue
                    if not _same_kind_for_merge(type, m.get("type", "")):
                        continue
                    mn = _normalize_fact(m.get("text", ""))
                    if not mn or len(mn) < 6:
                        continue
                    if norm in mn or mn in norm:
                        m["text"] = text
                        m["type"] = type
                        m["character"] = char
                        # 【2026-10-01 C 修正】保留原始 source（provenance 以首次来源为准），
                        # 不因为近似合并被新调用的默认 "user" 覆盖，否则来源统计失真。
                        # 若旧条曾被反悔(invalid_at)，本次重新表达视为"改口又改回"→ 复活。
                        if m.get("invalid_at"):
                            m["invalid_at"] = 0
                        m["ttl_days"] = ttl
                        m["confidence"] = conf
                        m["updated_at"] = now
                        m["hits"] = int(m.get("hits", 1)) + 1
                        self._save()
                        logger.info("长期记忆合并近似条目: %s", text[:50])
                        return False
            # 【2026-10-04 21:08 新增·三级：同类指令的"更替"】
            # 前两级都只认**字符串相同/互含**。但"改称呼"这类指令的两次措辞通常
            # 完全不同，用户 21:03 实锤：
            #   旧：「用户要求我称呼他为「鸡仔」，以后对话中默认用这个昵称。」
            #   新：「用户要求我称呼他为「漂泊者」，以后对话中用这个称呼。」
            # 两者**互不包含**（实测 normalize 后 a in b / b in a 均为 False）
            # ⇒ 两条都留在库里、且都是 directive（置顶"必须逐字遵守"），
            #    模型看到两条互相矛盾的指令 ⇒ 称呼随机摇摆（用户反馈"还是叫我漂泊者"
            #    说明抽到的是旧的那条）。
            # 现在加一级判定：**同属一个"指令槽位"**（如称呼/语言/风格/作息…）
            # 的 directive，新来的**作废旧的那条**（标 invalid_at 保留可追溯），
            # 实现 Mem0 式的"同槽位新指令覆盖旧指令"。
            slot = _directive_slot(text)
            if slot and type == "directive":
                for m in self._memories:
                    if m.get("invalid_at") or m.get("type") != "directive":
                        continue
                    # 【2026-10-16 修复·跨角色偷记忆】指令"同槽位更替"也必须限定同角色：
                    # 旧实现会让角色 B 的一条称呼指令**把角色 A 的称呼指令作废**
                    # （实测 danya 那条 invalid_at 被置位），A 的专属称呼就此失守。
                    if not self._same_scope(m, char):
                        continue
                    if _directive_slot(m.get("text", "")) == slot:
                        m["invalid_at"] = now
                        m["superseded_by"] = text[:80]
                        m["updated_at"] = now
                        logger.info("长期记忆指令更替（槽位=%s）: 旧=%s → 新=%s",
                                    slot, str(m.get("text"))[:36], text[:36])
            self._memories.append({
                "id": _new_mem_id(),
                "text": text,
                "type": type,
                "source": source,
                "character": char,
                "hits": 1,
                "created_at": now,
                "updated_at": now,
                "ttl_days": ttl,
                "invalid_at": 0,
                "confidence": conf,
            })
            # 超出上限：默认**不自动清理**；开启 auto_trim 时按角色各自上限删
            # （非指令里 hits 最低 + 最旧的），指令/情节永远保留。
            self._maybe_trim()
            self._save()
            logger.info("长期记忆新增[%s/%s]: %s", type, source, text[:50])
            _added = True
        # 【2026-10-04 18:50】新增条目**立刻**起后台补算向量。
        # 旧实现只在 `_recall_items()` 里惰性补算，导致"写完记忆但没人调用召回"
        # 时向量永远缺失 ⇒ 语义排序对这条记忆完全不生效（实测 5 条新记忆
        # 静默写入后向量补齐数为 0）。记忆写入是低频事件，这里起个后台线程
        # 补一条新记忆的开销可以忽略，且不影响调用方。
        try:
            if self._embed_fn is not None and _added:
                self._embed_bg_start()
        except Exception:  # noqa: BLE001
            pass
        return _added

    def _trim(self) -> None:
        """删到上限：优先保留 directive（用户规矩）+ episode（情节记忆），
        删 user/world 里最弱最旧的。

        【2026-10-11】episode 与 directive 一样受保护：情节记忆（"你们经历过的事"）
        是稀缺且高价值的（一天最多几条），不该因 hits 低被当成弱条目删掉。
        """
        # 【2026-10-17】改为**按角色各自上限**裁剪（原来用全局 _max_items ⇒ 角色 B 的新记忆
        # 会挤掉角色 A 的旧记忆；实测 max_items=5 时鱼加 1 条 → 达妮娅 5→4）。
        # 且本函数只在 auto_trim=True 时由 _maybe_trim() 调起；默认不自动清理。
        groups: dict[str, list[dict[str, Any]]] = {}
        for m in self._memories:
            groups.setdefault(str(m.get("character") or ""), []).append(m)
        kept: list[dict[str, Any]] = []
        for char, items in groups.items():
            cap = self._cap_for(char)
            keep = [m for m in items if m.get("type") in ("directive", "episode")]
            others = [m for m in items if m.get("type") not in ("directive", "episode")]
            others.sort(key=lambda m: (int(m.get("hits", 1)), m.get("updated_at", 0)))
            while len(others) + len(keep) > cap and others:
                others.pop(0)  # 删 hits 最少 + 最旧的
            if len(others) + len(keep) > cap:
                logger.info("[记忆] 角色 %r 的指令/情节已占满额度(%d)，未再裁剪", char, cap)
            kept.extend(keep + others)
        self._memories = kept

    # ---------- 召回 ----------

    # ---------- 关键词兜底（22:35）----------
    # ⚠️⚠️ 实测发现：`nomic-embed-text` 在中文短句上**区分度极低** ——
    #    query="相机" 与 6 条记忆的余弦全挤在 **0.54~0.68**（跨度仅 0.14），
    #    且**最相关的「用户在考虑尼康Z52相机」(0.6059) 竟低于毫不相干的
    #    「用户养了一只橘猫」(0.6493)**。⇒ 纯向量排序在本项目（中文短句记忆）
    #    基本失效，这是"记忆效果很差"的**真正主因**（比权重配错更根本）。
    #
    # 对策：把**字面关键词命中**作为独立信号加进打分。理由：
    #   · 用户问"相机"、记忆里写"尼康Z52相机" —— **字面就有"相机"**，这是
    #     最强也最可信的信号，不该被 embedding 的噪声淹没；
    #   · 中文不像英文有词边界，用**字符二元组（bigram）**做子串匹配即可，
    #     不需要分词（"相机"/"照片"/"旅游" 这类 2~3 字词组能稳定命中）。
    @staticmethod
    def _lexical_overlap(query: str, text: str) -> float:
        """query 与 text 的字符二元组重合率（0~1）。无需分词，适配中文短句。"""
        q = (query or "").strip()
        t = (text or "").strip()
        if not q or not t:
            return 0.0
        # 提取所有长度>=2 的汉字/字母数字片段
        toks = [q[i:i + 2] for i in range(len(q) - 1)]
        toks += [q] if len(q) == 1 else []
        if not toks:
            return 0.0
        hit = sum(1 for g in toks if g in t)
        return hit / float(len(toks))

    def _score(self, m: dict, query_emb: Optional[list[float]],
               query: str = "") -> float:
        """jarvis 召回分。**语义优先**（相关性第一），新鲜度/重要性/置信只做次级调节。

        【2026-10-04 22:30 重写·治"召回最相关的却排最后"】旧式是
        `sem*0.4 + hits*0.2 + age*0.2 + conf*0.2` —— 语义只占 0.4，**其余 0.6
        全与"相不相关"无关**（新鲜度、命中次数、置信），于是**刚记下的新条目
        永远压过语义最匹配的那条**。实测（造 13 条记忆）：
            recall("相机", limit=5) → 第 5 名才是「用户在考虑尼康Z52相机」
        而用户问"相机"时最该被召回的就是那条 ⇒ 表现为"记忆效果很差/她答非所问"。

        现在改为**语义主导**：
          · 给了 query 且这条有向量 → 以语义相似度为主（占 0.85），其余只微调；
          · 语义相似度做**相对归一**（同一次召回里按最高分缩放），避免
            "所有余弦都是 0.5~0.6"这种区分度不足时，被新鲜度翻盘；
          · 没有 query（如 `memory_text` 的纯注入）→ 退化为"重要性+新鲜度"排序，
            与旧行为一致，不影响"主动注入最新记忆"的既有行为。
        """
        now = time.time()
        if m.get("invalid_at"):
            return 0.01  # 已反悔：几乎不召回（冷归档保留）
        age = now - m.get("updated_at", 0)
        ttl = int(m.get("ttl_days") or DEFAULT_TTL_DAYS) * 86400
        # 新鲜度：前半程 1.0，后半程 1.0→0.4，超期 0.25
        if age < ttl * 0.5:
            age_score = 1.0
        elif age < ttl:
            age_score = 1.0 - 0.6 * (age - ttl * 0.5) / (ttl * 0.5)
        else:
            age_score = 0.25
        hits = int(m.get("hits", 1))
        hits_score = min(hits / 10.0, 1.0)
        conf = max(0.0, min(1.0, float(m.get("confidence", 1.0))))
        emb = m.get("emb")
        # ⭐⭐ 关键词命中分（22:35）：字符二元组重合率。**这是本项目最可信的相关性信号**
        #   —— 见 `_lexical_overlap` 上方注释：中文短句上 embedding 余弦全挤在
        #   0.54~0.68、最相关条目竟低于无关条目，纯向量排序基本失效。
        lex = self._lexical_overlap(query, m.get("text", "")) if query else 0.0
        base = hits_score * 0.4 + age_score * 0.3 + conf * 0.3
        if query_emb and emb:
            sem = _cosine(query_emb, emb)
            # 余弦可能为负（不相关），整体映射到 0~1
            sem = max(0.0, min(1.0, (sem + 1.0) / 2.0))
            if sem < 0.55:
                # 语义明显偏低：几乎只靠关键词决定（有关键词就进，无关就沉底）
                return lex * 0.75 + sem * 0.1 + base * 0.15
            # 关键词命中为主、语义次之，新鲜度/命中次数等只做微调
            return lex * 0.55 + sem * 0.38 + base * 0.07
        # 无向量可用：**关键词就是唯一相关性来源**，给它绝大部分权重
        if query:
            return lex * 0.85 + base * 0.15
        return base

    def _recall_items(self, query: str = "", limit: int = 8) -> list[dict]:
        """按当前 mode 召回条目列表（memory_text 用它做分组注入）。"""
        with self._lock:
            mems = [m for m in self._memories if m.get("text")]
            # 【2026-10-01 C】反悔记忆(invalid_at)不进入召回；仍留库做冷归档/排障。
            mems = [m for m in mems if not m.get("invalid_at")]
            # 【2026-10-01 A-5】角色隔离：只取「本角色」+「通用」两类。
            # 旧数据没有 character 字段 → 视为通用记忆，仍会注入，保证升级后不会突然失忆。
            _char = (self._character or "").strip()
            if _char:
                # 【2026-10-16 修复·记忆串味】原规则：没有 character 字段的旧记忆 = 通用记忆，
                # **注入给每一个角色**（原注释："保证升级后不会突然失忆"）。实测后果：
                # 记忆库里那条无归属的「用户希望我称呼他『漂泊者』」（达妮娅的专属称呼）
                # 被注入给了所有角色 —— 换角色后对方仍叫他"漂泊者"，用户看到的就是"人设串了"。
                # 新规则：无归属旧记忆只归 legacy owner（=首次设定的角色，通常即主角色），
                # 其余角色不再看到；set_character 还会把这些条目一次性盖上有归属。
                _lo = (self._legacy_owner or "").strip()
                mems = [m for m in mems
                        if (m.get("character") or "").strip() == _char
                        or (not (m.get("character") or "").strip() and _lo and _char == _lo)]
            if not mems:
                return []
            if self._mode == "legacy":
                mems.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
                return mems[:limit]
            # jarvis：directive **限量**优先，其余按 语义+重要性+新鲜度 排序
            # 【2026-10-04 22:31】旧实现 `return (directives + others)[:limit]` 有两个问题：
            #   ① directive **无条件全量插到最前** —— 哪怕这次 query 跟它毫无关系
            #      （例如问"相机"），它也照样占掉一个坑位；
            #   ② 更糟：others 是"按分排序后取 limit"再与 directives 拼接，
            #      等于 directives 额外插队、others 的名额被挤压。
            # 现在：directive 同样走打分（**语义相关/高分的先留，最多 2 条**），
            # 再与 others 按分**统一合并排序**取 limit ⇒ 相关的那条一定在前面。
            query_emb = None
            if query and self._embed_fn is not None:
                query_emb = self._embed(query)
                if query_emb:
                    self._embed_cached()  # 给尚无向量的记忆惰性补算，语义才参与排序
            # ⚠️ 必须**先补算向量再打分**，否则缺 emb 的条目全都走"无语义"降级路径
            for m in mems:
                if query_emb and not m.get("emb"):
                    v = self._embed(m.get("text", ""))
                    if v:
                        m["emb"] = v
            scored = [(self._score(m, query_emb, query), m) for m in mems]
            # directive 单独限额：最多 2 条，且必须是"打分够高"的（不硬塞无关的）
            d_max = 2
            d_items = [m for sc, m in sorted(scored, key=lambda x: x[0], reverse=True)
                       if m.get("type") == "directive" and sc >= 0.5][:d_max]
            d_ids = {id(m) for m in d_items}
            o_items = [m for sc, m in sorted(scored, key=lambda x: x[0], reverse=True)
                       if id(m) not in d_ids]
            merged = [(self._score(m, query_emb, query), m) for m in d_items] + \
                     [(sc, m) for sc, m in scored if id(m) not in d_ids]
            merged.sort(key=lambda x: x[0], reverse=True)
            return [m for _, m in merged[:limit]]

    def recall(self, query: str = "", limit: int = 8) -> list[str]:
        """返回召回的记忆文本列表。legacy 按时间；jarvis 指令优先 + 重要性/语义。"""
        return [m["text"] for m in self._recall_items(query=query, limit=limit)]

    def memory_text(self, limit: int = 8, query: str = "") -> str:
        """格式化为注入文本；无记忆返回空串。

        jarvis 模式按类型分组并带标题（指令块特权在前）：
          【用户明确说过的要求（必须逐字遵守）】... （directive）
          【关于用户的记忆】...            （user）
          【学到的外部事实】...            （world）
        legacy 模式保持原 "- xxx" 平铺。
        """
        items = self._recall_items(query=query, limit=limit)
        if not items:
            return ""
        if self._mode == "legacy":
            return "\n".join(f"- {m['text']}" for m in items)
        blocks: list[str] = []
        for typ, title in (("directive", "用户明确说过的要求（必须逐字遵守）"),
                           ("user", "关于用户的记忆"),
                           ("world", "学到的外部事实"),
                           ("episode", "你们一起经历过的事")):
            group = [m for m in items if m.get("type") == typ]
            if group:
                lines = "\n".join(f"- {m['text']}" for m in group)
                blocks.append(f"【{title}】\n{lines}")
        return "\n\n".join(blocks)

    def list_user_facts(self, limit: int = 12, include_world: bool = True) -> list[str]:
        """【2026-10-01 E】返回可用于「主动聊起」的用户偏好/习惯事实（仅 active）。

        - 角色隔离：只取本角色 + 通用（与 recall 同一套作用域规则，保证升级后不失忆）。
        - 反悔过滤：invalid_at 的条目不进入（已改口的事实不再被拿来主动聊）。
        - 取 user（关于用户的记忆）+ 可选 world（学到的外部事实），按更新时间倒序。
        返回纯文本列表，供主动触发模块挑一条自然接进对话。
        """
        with self._lock:
            mems = [m for m in self._memories if m.get("text") and not m.get("invalid_at")]
            _char = (self._character or "").strip()
            if _char:
                # 【2026-10-16 修复·记忆串味】原规则：没有 character 字段的旧记忆 = 通用记忆，
                # **注入给每一个角色**（原注释："保证升级后不会突然失忆"）。实测后果：
                # 记忆库里那条无归属的「用户希望我称呼他『漂泊者』」（达妮娅的专属称呼）
                # 被注入给了所有角色 —— 换角色后对方仍叫他"漂泊者"，用户看到的就是"人设串了"。
                # 新规则：无归属旧记忆只归 legacy owner（=首次设定的角色，通常即主角色），
                # 其余角色不再看到；set_character 还会把这些条目一次性盖上有归属。
                _lo = (self._legacy_owner or "").strip()
                mems = [m for m in mems
                        if (m.get("character") or "").strip() == _char
                        or (not (m.get("character") or "").strip() and _lo and _char == _lo)]
            wanted = {"user"}
            if include_world:
                wanted.add("world")
            mems = [m for m in mems if m.get("type") in wanted]
            mems.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
            return [m["text"] for m in mems[:limit]]

    def recent_episodes(self, limit: int = 12, since_ts: float = 0.0) -> list[dict]:
        """【2026-10-11 Plan B】取近期情节记忆（供反思聚合的输入）。

        只取 active（未 invalid）的 episode，按更新时间倒序；`since_ts` 可限定
        只取某时间之后新增的（用于"增量反思"，不重复聚合旧片段）。
        """
        with self._lock:
            mems = [m for m in self._memories
                    if m.get("type") == "episode" and m.get("text")
                    and not m.get("invalid_at")]
            _char = (self._character or "").strip()
            if _char:
                # 【2026-10-16 修复·记忆串味】原规则：没有 character 字段的旧记忆 = 通用记忆，
                # **注入给每一个角色**（原注释："保证升级后不会突然失忆"）。实测后果：
                # 记忆库里那条无归属的「用户希望我称呼他『漂泊者』」（达妮娅的专属称呼）
                # 被注入给了所有角色 —— 换角色后对方仍叫他"漂泊者"，用户看到的就是"人设串了"。
                # 新规则：无归属旧记忆只归 legacy owner（=首次设定的角色，通常即主角色），
                # 其余角色不再看到；set_character 还会把这些条目一次性盖上有归属。
                _lo = (self._legacy_owner or "").strip()
                mems = [m for m in mems
                        if (m.get("character") or "").strip() == _char
                        or (not (m.get("character") or "").strip() and _lo and _char == _lo)]
            if since_ts:
                mems = [m for m in mems if float(m.get("created_at", 0) or 0) >= since_ts]
            mems.sort(key=lambda m: m.get("created_at", 0), reverse=True)
            return [dict(m) for m in mems[:limit]]

    def has_insight(self, text: str) -> bool:
        """【2026-10-11 Plan B】判断某条"反思洞察"是否已在 user 记忆里（近似去重）。

        用现有 `_normalize_fact`（去空白/标点/大小写）+ 互为包含判定，避免同一
        洞察被反复聚合出来（与 `add()` 的二级去重同口径，但这里只查不写）。
        """
        n = _normalize_fact(text)
        if len(n) < 4:
            return False
        with self._lock:
            # 【2026-10-16 修复·跨角色偷记忆】必须按当前角色作用域查，否则角色 A 的
            # 记忆会让角色 B 的同类洞察被判定"已存在"→ 直接 continue 不写库，
            # 表现同样是"换个角色就学不到"。调用方（dialogue.py 反思路径）紧随其后
            # 调 add() 且不传 character ⇒ 同口径用 self._character。
            _char = (self._character or "").strip()
            for m in self._memories:
                if m.get("invalid_at") or m.get("type") != "user":
                    continue
                if not self._same_scope(m, _char):
                    continue
                mn = _normalize_fact(m.get("text", ""))
                if not mn:
                    continue
                if n == mn or (len(n) >= 4 and (n in mn or mn in n)):
                    return True
        return False

    # ---------- 语义嵌入 ----------
    def _embed(self, text: str) -> Optional[list[float]]:
        """算一条文本的嵌入向量（失败返回 None，调用方降级）。"""
        if self._embed_fn is None:
            return None
        try:
            vec = self._embed_fn(text)
            return vec if vec else None
        except Exception:  # noqa: BLE001
            logger.debug("记忆嵌入失败", exc_info=True)
            return None

    def _embed_cached(self) -> None:
        """给缺嵌入向量的记忆条目惰性补算并缓存（语义检索用）。

        【2026-10-04 18:45 性能修复】旧实现是**同步逐条**调 ollama 算向量，
        且在**每次召回**里都跑一遍。实测首次召回 6 条记忆要 **12.2 秒**
        （每条 embedding 请求约 2s），后续 2.3s —— 对话链路（尤其双工通话）
        是绝对不可接受的卡顿。

        现在改为：
          · 每次召回**最多同步补 1 条**（保证语义能逐步生效，且单次开销可控）；
          · 剩下的交给**后台守护线程**慢慢补，不阻塞调用方；
          · 后台线程用 `_embed_bg_running` 自保护，不会重复启动。
        效果：首次召回从 12s 降到 0.1s 级，语义排序在随后几秒内自动补齐。
        """
        if self._embed_fn is None:
            return
        pending = [m for m in self._memories if not m.get("emb")]
        if not pending:
            return
        # 同步只补 1 条：够让本次排序里至少有一条真语义分，且开销可控
        m = pending[0]
        vec = self._embed(m.get("text", ""))
        if vec:
            m["emb"] = vec
            try:
                self._save()
            except Exception:  # noqa: BLE001
                pass
        if len(pending) > 1:
            self._embed_bg_start()

    def _embed_bg_start(self) -> None:
        """后台守护线程：把剩余缺向量的记忆慢慢补齐（每条之间 sleep，避免占满本机）。"""
        if getattr(self, "_embed_bg_running", False):
            return
        self._embed_bg_running = True

        def _worker() -> None:
            try:
                import time as _t
                while True:
                    with self._lock:
                        todo = [m for m in self._memories if not m.get("emb")]
                    if not todo or self._embed_fn is None:
                        return
                    m = todo[0]
                    try:
                        vec = self._embed_fn(m.get("text", "")) or []
                    except Exception:  # noqa: BLE001
                        return
                    if not vec:
                        return  # 嵌入服务不可用：放弃，别无限重试刷日志
                    with self._lock:
                        m["emb"] = vec
                    try:
                        self._save()
                    except Exception:  # noqa: BLE001
                        pass
                    _t.sleep(0.2)   # 让出 CPU，别和主链路抢资源
            finally:
                self._embed_bg_running = False

        try:
            import threading
            threading.Thread(target=_worker, daemon=True, name="mem-embed").start()
        except Exception:  # noqa: BLE001
            self._embed_bg_running = False

    # ---------- 管理 ----------
    def count(self) -> int:
        with self._lock:
            return len(self._memories)

    def count_by_type(self) -> dict[str, int]:
        with self._lock:
            out = {t: 0 for t in MEMORY_TYPES}
            for m in self._memories:
                typ = m.get("type", "user")
                if typ in out:
                    out[typ] += 1
            return out

    def count_by_source(self) -> dict[str, int]:
        """provenance 统计：chat/duplex/operation/user 各来源条数（含已反悔）。"""
        with self._lock:
            out = {s: 0 for s in MEMORY_SOURCES}
            for m in self._memories:
                s = m.get("source", "user")
                if s in out:
                    out[s] += 1
                else:
                    out["user"] += 1
            return out

    def retract(self, fact_text: str, character: Optional[str] = None) -> int:
        """【2026-10-01 C】反悔/改口：把与 fact_text 语义一致的**有效**记忆软失效
        （打 invalid_at=now），不硬删。返回实际失效条数。

        匹配规则（无嵌入时）：归一化完全相同，或互为包含（>=4 字防误伤）。
        角色作用域：传 character 仅失效该角色；不传则用当前 set_character 作用域
        （空作用域 = 命中全部角色的同类事实）。
        """
        fact_text = (fact_text or "").strip()
        if not fact_text:
            return 0
        norm = _normalize_fact(fact_text)
        if len(norm) < 2:
            return 0
        char = (character if character is not None else self._character or "").strip()
        n = 0
        with self._lock:
            now = time.time()
            for m in self._memories:
                if m.get("invalid_at"):
                    continue
                # 【2026-10-16 修复·与读侧同口径】原条件"仅当两侧归属都非空且不同才跳过"
                # ⇒ **无归属(char=="")的记忆会被任何角色的"反悔"误伤**：读侧根本看不到它
                # （无归属只归 legacy owner），却能被另一个角色一句话标成 invalid_at。
                # 改用 `_same_scope`，与 recall / list_user_facts / add() 同一套作用域规则。
                if not self._same_scope(m, char):
                    continue
                mn = _normalize_fact(m.get("text", ""))
                if not mn:
                    continue
                if mn == norm or (len(mn) >= 4 and mn in norm) or (len(norm) >= 4 and norm in mn):
                    m["invalid_at"] = now
                    m["updated_at"] = now
                    n += 1
            if n:
                self._save()
                logger.info("长期记忆反悔失效 %d 条: %s", n, fact_text[:50])
        return n

    def stats(self) -> dict[str, Any]:
        """观测用快照：总量 / 按类型 / 按来源 / 按角色 / 失效数 / 估算字符量。"""
        with self._lock:
            by_char: dict[str, int] = {}
            invalid = 0
            chars = 0
            for m in self._memories:
                if m.get("invalid_at"):
                    invalid += 1
                c = (m.get("character") or "").strip() or "(通用)"
                by_char[c] = by_char.get(c, 0) + 1
                chars += len(m.get("text", ""))
            return {
                "total": len(self._memories),
                "active": len(self._memories) - invalid,
                "invalidated": invalid,
                "by_type": self.count_by_type(),
                "by_source": self.count_by_source(),
                "by_character": by_char,
                "est_chars": chars,
            }

    def items(self) -> list[dict]:
        """返回全部条目（UI 展示用，只读副本）。"""
        with self._lock:
            return [dict(m) for m in self._memories]

    def remove(self, key: str) -> int:
        """【2026-10-13】单条删除：按 id 精确删；id 不中则按文本（归一化）兜底删一条。

        与 `retract()`（软失效、保留可追溯、仍计入 total）不同，本方法是**硬删除**
        ——用户明确要"移除这条"，就不该留在库里、也不该再参与任何统计与召回。
        返回实际删除条数（0 或 1）。
        """
        k = (key or "").strip()
        if not k:
            return 0
        n = 0
        with self._lock:
            for i, m in enumerate(self._memories):
                if str(m.get("id") or "") == k:
                    del self._memories[i]
                    n = 1
                    break
            if not n:
                kn = _normalize_fact(k)
                if len(kn) >= 2:
                    # 【2026-10-16 修复·文本兜底会删错角色】跨角色**同文本**条目现在可以
                    # 并存（见 `_same_scope`），而旧兜底删的是"全库第一条" ⇒ 在记忆库页
                    # 删某个角色的无 id 旧数据时，可能把**另一个角色**的同文本记忆硬删掉
                    # （硬删不可追溯）。现在优先删"当前角色作用域内"的那条；当前角色没有
                    # 匹配时才退回旧行为（全库第一条）——单角色场景下与旧行为逐字一致。
                    _c = (self._character or "").strip()
                    _hit = None
                    _any = None
                    for i, m in enumerate(self._memories):
                        if _normalize_fact(m.get("text", "")) != kn:
                            continue
                        if _any is None:
                            _any = i
                        if self._same_scope(m, _c):
                            _hit = i
                            break
                    _idx = _hit if _hit is not None else _any
                    if _idx is not None:
                        del self._memories[_idx]
                        n = 1
            if n:
                self._save()
                logger.info("长期记忆单条删除: key=%s", k[:40])
        return n

    def clear(self) -> None:
        with self._lock:
            self._memories = []
            self._save()
