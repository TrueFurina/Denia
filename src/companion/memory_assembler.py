"""分层记忆装配器（B 步，2026-10-01）。

为什么需要它
------------
本项目三档模型的上下文预算差异极大（config.yaml 实测）：
  cloud        云端 LLM（qwen-plus/角色模型，32K~1M）      → 可以灌全文
  local        本地 LLM（qwen3:14b，num_ctx=4096）         → 只能灌压缩摘要
  local_duplex 本地双工（MiniCPM-o，比 4096 更小）          → 只能灌极简核心
用同一份注入喂所有模型，本地档不是崩就是忘。

⚠️ 铁律：近况历史**绝不原样灌给小模型**
--------------------------------------
`proactive._build_duplex_memory` 的注释记录了实锤事故：旧实现把最近几轮原话逐句回贴
进提示词，本地 omni 弱模型把回贴内容当成模板照样复述，产出固定起头循环。
因此本装配器分档处理历史：
  cloud        → 完整历史（云端强模型，且 dialogue 原本就这样做）
  local        → **话题级摘要**（不提供可照抄的完整句子）
  local_duplex → **不灌历史**（连续性由 A-1 的话题桥 + 长期记忆承担）

设计来源（诚实标注）
--------------------
- Letta / MemGPT 的 core memory 常驻块 → 对应本模块的"记忆核心块"；
- Mem0 的 add/update 冲突解决          → 已在 memory_store 二级去重写过；
- MemoryOS / MIRIX 的四层分流           → 本模块按「长期记忆 / 近况 / （操作日志）」装配。
实现为自研，理由见本文件末尾「为什么没有直接采用开源库」。
"""

from __future__ import annotations

import re
import time
from typing import Optional

TIER_CLOUD = "cloud"
TIER_LOCAL = "local"
TIER_LOCAL_DUPLEX = "local_duplex"

# 各档装配配方：mem=长期记忆条数，hist=近况历史轮数，budget=字符硬上限（0 表示不限）
# budget 按"中文 1 字 ≈ 1 token"的保守口径给，宁可少灌也不溢出。
#
# 【2026-10-11 按档放开】用户诉求"有多少灌多少"。但**无脑灌满会反噬**
#   （lost-in-the-middle：长上下文中间信息被系统性忽略；成本；弱相关噪音稀释）。
#   故不是"取消上限"，而是**按模型能力分级放开**：
#     · cloud        云端强模型（32K~1M）→ mem 12→24、hist 24→32（仍留上限防 lost-in-middle）
#     · local        本地 qwen3:14b（4096）→ mem 5→10、hist 6→8、budget 1200→2400
#     · local_duplex 本地 Comni（<4096）  → 不动（小模型动就崩，铁律见文件头）
#   真正的"记不住"根因从来不是这里的上限，而是 ①调用方写死条数 ②库里条数少 ③语义检索失效。
_TIER_CFG = {
    TIER_CLOUD:         {"mem": 24, "hist": 32, "budget": 0},
    TIER_LOCAL:         {"mem": 10, "hist": 8,  "budget": 2400},
    TIER_LOCAL_DUPLEX:  {"mem": 3,  "hist": 0,  "budget": 600},
}

# 提炼话题词时剔除的高频虚词（与 proactive._BRIDGE_STOPWORDS 同思路）
_STOPWORDS = (
    '了', '的', '是', '我', '你', '他', '她', '它', '在', '有', '就', '都', '也', '和',
    '跟', '给', '把', '被', '让', '吗', '呢', '吧', '啊', '呀', '哦', '嗯', '这', '那',
    '个', '请', '帮', '以后', '现在', '还', '再', '要', '会', '能', '可以', '很', '太',
    '不', '没', '别', '然后', '一下', '什么', '怎么', '为什么', '我们', '你们', '咱们',
)


def _distill(s: str, max_len: int = 10) -> str:
    """蒸馏成短话题词：去标点 + 去虚词 + 截断。绝不返回完整原句。"""
    core = re.sub(r'[\s\W_]+', '', (s or ''), flags=re.UNICODE)
    for w in _STOPWORDS:
        core = core.replace(w, '')
    return core.strip()[:max_len]


def topic_digest(n: int = 6, max_topics: int = 4) -> str:
    """近况历史的话题级摘要（不回贴原句）。供 local 档使用。

    返回形如："最近聊过：汽水音乐、放歌、明天早起"。小模型据此知道聊过什么，
    但没有可照抄的完整句子，规避复述事故。
    """
    try:
        from .conversation_history import shared_history
        texts = shared_history().recent_user_texts(n)
    except Exception:
        return ''
    topics, seen = [], set()
    for t in (texts or []):
        s = (t or '').strip()
        if not s:
            continue
        frag = _distill(s)
        if len(frag) < 2 or frag == s:      # 太短 / 蒸馏后仍是原句 → 丢弃
            continue
        if frag in seen:
            continue
        seen.add(frag)
        topics.append(frag)
        if len(topics) >= max_topics:
            break
    if not topics:
        return ''
    return '最近聊过：' + '、'.join(topics)


def current_topic_query(max_chars: int = 60) -> str:
    """从共享历史里提炼"当前话题"，用作语义召回的 query。

    【2026-10-04 18:35 新增·让语义检索真正生效】
    背景：`MemoryStore._score()` 里语义权重占 0.4，但**只有 `query` 非空且
    `_embed_fn` 可用时才会算 query 向量**。而此前**全部 5 条注入路径都传
    `query=''`**（dialogue 的聊天档、proactive 的通话档/live 档/test3 族），
    于是 `query_emb` 恒为 None → `sem` 恒 0.0 → 语义权重整项丢失，退化成
    "按提及次数+新鲜度排序"。**即便装了 nomic-embed-text，语义检索也完全不生效**。

    这里提供统一入口：取共享历史里最近几句**用户**发言，做词级提炼后拼成 query。
    - 只取 user 侧：assistant 的话是本软件自己生成的，用它当 query 会强化
      "模型自己的措辞"而偏离用户真实意图；
    - 蒸馏成词（去虚词、截断）而非整句：向量检索对短关键词更敏感，也更省 token；
    - 拿不到历史时返回空串，调用方须容忍空 query（此时行为退化为关键词召回，与
      改前完全一致，**不会更差**）。
    """
    try:
        from .conversation_history import shared_history
        texts = shared_history().recent_user_texts(4)
    except Exception:
        return ""
    if not texts:
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    budget = max_chars
    for t in reversed(texts or []):
        s = (t or "").strip()
        if not s:
            continue
        frag = _distill(s, max_len=8)
        if len(frag) < 2 or frag in seen:
            continue
        seen.add(frag)
        parts.append(frag)
        budget -= len(frag)
        if budget <= 0 or len(parts) >= 4:
            break
    if not parts:
        return ""
    return " ".join(parts)


def assemble_memory_block(
    tier: str = TIER_CLOUD,
    store: Optional[object] = None,
    query: str = "",
    include_history: bool = True,
    mem_limit: Optional[int] = None,
) -> str:
    """按档位装配记忆块，返回可直接拼进提示词的文本（无内容则返回空串）。

    tier: cloud / local / local_duplex，未知值按 cloud 处理（保持旧行为，最安全）。
    store: MemoryStore 实例（可为 None，此时只装近况）。
    mem_limit: 【2026-10-11】显式覆盖本档记忆条数上限。用于"通话/双工"这类实时路径——
        它们上下文压力更大（要带视觉/音频描述），不该套用聊天档放开后的 24 条。
        传 None（默认）则用 _TIER_CFG 的档位值。
    """
    cfg = _TIER_CFG.get(tier) or _TIER_CFG[TIER_CLOUD]
    _mem_n = int(mem_limit) if mem_limit else int(cfg["mem"])
    parts: list[str] = []
    _t0 = time.time()
    _recalled = 0

    # 【2026-10-04 18:35】query 兜底：调用方传空时，自动从共享历史提炼"当前话题"。
    # 这样**不必逐个改 5 条注入路径**也能让语义检索真正生效；取不到历史时仍为空串，
    # 行为退化为关键词召回（与改前完全一致，绝不会更差）。
    _q = (query or "").strip()
    if not _q and store is not None:
        try:
            _q = current_topic_query()
        except Exception:
            _q = ""

    # 1) 长期记忆（store 内部已按 jarvis 分类，指令块特权在前）
    if store is not None:
        try:
            mt = store.memory_text(limit=_mem_n, query=_q) or ""
        except Exception:
            mt = ""
        if mt:
            parts.append(mt)
            _recalled = len([ln for ln in mt.splitlines() if ln.strip().startswith("- ")])

    # 2) 近况（分档：云端全文 / 本地话题摘要 / 双工不灌）
    if include_history and cfg["hist"] > 0:
        if tier == TIER_CLOUD:
            try:
                from .conversation_history import shared_history
                snap = shared_history().snapshot_text(cfg["hist"])
            except Exception:
                snap = ""
            if snap:
                parts.append("【最近对话】\n" + snap)
        else:
            td = topic_digest(n=max(cfg["hist"], 6))
            if td:
                parts.append("【近况提示】" + td)

    if not parts:
        return ""
    text = "\n\n".join(parts)

    # 3) 预算裁剪：超限先砍近况，再砍记忆尾部
    budget = cfg["budget"]
    if budget and len(text) > budget:
        if len(parts) > 1:
            text = parts[0]
        if len(text) > budget:
            text = text[:budget].rstrip()
    # 【2026-10-01 D】观测仪：记录本次装配的档位 / 估算 token / 耗时 / 召回条数
    try:
        from ..llm.memory_profiler import record_probe
        _lat = (time.time() - _t0) * 1000.0
        # 中文 1 字 ≈ 1 token 的保守口径给 token 估算
        record_probe(tier, model="", event="assemble",
                     prompt_tokens=len(text), latency_ms=_lat,
                     recalled_facts=_recalled, note=cfg.get("budget") and "budget=%d" % cfg["budget"] or "")
    except Exception:  # noqa: BLE001
        pass
    return text


# ===== 记忆分档判据（10-04 重构）=====
# ⚠️ 根本困难：`qwen` 家族**横跨云端与本地**（云端 qwen3.5-omni-flash-realtime
#    vs 本地 Ollama qwen3.5:27b 都含 "qwen"），靠模型名无法区分。所以
#    **端点/本地特征优先，模型名只作为无端点时的辅助**，见 resolve_tier。

# ① 本地强特征：端点 / ollama tag / 量化后缀 / 本地引擎名。
#    这些**不会**出现在云端模型串里，可安全作为本地判据。
_LOCAL_ENDPOINT_MARKS = ("localhost", "127.0.0.1", "0.0.0.0", ":8006", ":11434",
                         "ollama:", "ollama/", "minicpm", "comni",
                         ":q4_", ":q5_", ":q8_", ":f16", ":latest-local",
                         "local_omni", "local-duplex")
# ② 云端端点特征：用户填 base_url 时几乎必含 host，比模型名可靠。
_CLOUD_ENDPOINT_MARKS = ("dashscope", "aliyuncs", "openai.com", "azure",
                        "anthropic", "googleapis", "bigmodel", "volces",
                        "moonshot", "siliconflow", "openrouter", "deepseek.com",
                        "baidubce", "xaminim", "aliyun", "bailian")
# ③ 无端点时才用模型名：**刻意不含 qwen / omni**（横跨两侧，会互相误判）。
_LOCAL_MODEL_KEYS = ("llama3", "phi3", "gemma", "mistral-nemo", "deepseek-r1:",
                     "lfm2", "qwen3:14b", "qwen2.5:")   # 带 tag 的才是本地命名
_CLOUD_MODEL_KEYS = ("gpt", "claude", "gemini", "glm", "zhipu", "kimi", "moonshot",
                     "ernie", "baidu", "doubao", "volc", "step", "hunyuan",
                     "tencent", "grok", "mistral-large", "deepseek-chat",
                     "chatglm", "internlm", "realtime", "cloud")
# ④ 含 "omni" 时用它辅助区分：带这些云端家族特征算云端，否则算本地 Comni
_CLOUD_FAMILY_HINT = ("qwen", "dashscope", "aliyun", "cloud", "realtime", "gpt")


def resolve_tier(engine_name: str = "", companion_mode: str = "") -> str:
    """由双工引擎名 / 运行档位推导装配档位。

    ⚠️ 难点（10-04 血泪）：**qwen 家族横跨云端与本地**——
    云端 `qwen3.5-omni-flash-realtime` 与本地 Ollama `qwen3.5:27b` 都含 "qwen"，
    单看模型名**无法区分**。靠名字猜的两种失败都已实测：
      · 本地关键词里放 `"qwen3.5"` → 云端 Qwen-Omni 被误判成小模型，白丢记忆；
      · 云端关键词里放 `"qwen"`   → 本地 qwen3:14b 被误判成云端，灌 12 条记忆
        ＋全文近况 → **本地小模型 context 爆**（比前者更危险）。
    ⇒ 判定改为**端点优先**（最可靠，用户填 base_url 时一定含 host）：
      ① 本地端点特征（localhost/127.0.0.1/ollama:/量化 tag）→ local_duplex
      ② 云端端点特征（dashscope/aliyuncs/openai/…）→ cloud
      ③ 无端点信息时，才用模型名关键词，且**云端家族词不含 qwen/omni**
         （那两个横跨两侧，交给 ①② 与 companion_mode 判）
      ④ 兜底 cloud（与旧行为一致）
    """
    e = (engine_name or "").lower()
    # ① 本地强特征优先（端点 / ollama tag / 量化后缀）——比任何模型名都可靠
    if any(m in e for m in _LOCAL_ENDPOINT_MARKS):
        return TIER_LOCAL_DUPLEX
    # ② 云端端点特征
    if any(m in e for m in _CLOUD_ENDPOINT_MARKS):
        return TIER_CLOUD
    # ③ 无端点：只看"不横跨两侧"的模型名关键词
    #    （qwen/omni 已被刻意剔除，否则云端 Omni 与本地 qwen 必然互相误判）
    if any(k in e for k in _LOCAL_MODEL_KEYS):
        return TIER_LOCAL_DUPLEX
    if any(k in e for k in _CLOUD_MODEL_KEYS):
        return TIER_CLOUD
    # ④ 引擎名里裸含 omni/comni 形态：云端先判（qwen-omni 属云端），否则本地
    if "omni" in e:
        return TIER_CLOUD if any(k in e for k in _CLOUD_FAMILY_HINT) else TIER_LOCAL_DUPLEX
    m = (companion_mode or "").lower()
    if m == "local":
        return TIER_LOCAL
    return TIER_CLOUD


# ---------------------------------------------------------------------------
# 为什么没有直接采用开源库（诚实说明）
# ---------------------------------------------------------------------------
# Mem0 / Letta / Zep-Graphiti / MemoryOS / MIRIX 都是成熟方案，但直接替换本项目
# 记忆层会带来三个硬冲突：
#   1) 它们的核心写入/冲突解决都要额外调一次 LLM，本项目要支持「本机免费」档
#      （本地 4bit 小模型、4096 上下文），额外的 LLM 往返又慢又不可靠；
#   2) 我们的 jarvis 分类（directive=用户立的规矩，必须逐字遵守并特权注入）是
#      为"AI 伴侣守规矩"定制的，这些库没有对应的概念；
#   3) A-5 的角色隔离（同一份库要按角色分桶）同样是本项目特有需求，开源库没有。
# 因此采取"借鉴机制、自研实现"：把各家的成熟做法拆成小机制逐个搬进来，
# 而不是整体换库。若将来只跑云端档、且接受额外 LLM 开销，可再评估整体接入。
