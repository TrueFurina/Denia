"""关系状态机（P0）——隐藏的三维好感 + 心情推导 + 落盘/补算。

设计文档：docs/好感度系统与主动发言_设计与实施计划_20261010.md

边界（很重要）：
- 本模块**不调用任何模型**，纯规则。所有"语义判定"（敷衍 / 关心 / 挽回 / 踩雷）
  由**外部**（未来的"关系裁判"）算好后，经 `signals` 传进来。
- P0 只做：状态骨架 / 原子落盘 / 坏文件降级 / 停机补算 / 心情推导 / 会话时钟接线点。
- **绝不外显数字**：对外只给"心情"（`derive_mood` / `describe_mood`）。
- 异常一律可见：坏文件隔离为 `*.bad-<ts>`，不静默吞（项目最大 bug 源就是静默容错）。

数据结构见 `default_state()`；持久化在 `data/affinity/<char_id>.json`（data/ 已被 .gitignore）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

# 【2026-10-16 修复·未定义名】本模块此前**没有定义 logger**，但
# `reset_relationship()` 的 `except` 里调了 `logger.warning(...)`（原 :1012）。
# 后果：`store.archive()` 一旦抛异常（文件被占用/权限/磁盘），except 里那句
# 先炸 `NameError: name 'logger' is not defined` —— 它**顶掉**了原始异常，
# 而且函数在写新状态之前就退出 ⇒「重置关系」整体失败，报错还指向错误的方向。
# 这类"except 里再抛"的 bug 最隐蔽：正常路径永远不经过它。
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

DIMENSIONS = ("trust", "intimacy", "warmth")
DIM_LABEL = {"trust": "信任", "intimacy": "亲密", "warmth": "热络"}

# 全局节奏档：一周不聊掉多少（%）。默认"标准"。
PACE_WEEKLY_PCT = {"疏淡": 5.0, "标准": 8.0, "浓郁": 12.0}
DEFAULT_PACE = "标准"

# 好感各维留底：掉到这里就不再掉（"疏远但不绝情"）。
DIM_FLOOR = {"trust": 20.0, "intimacy": 20.0, "warmth": 15.0}
DIM_MAX = 100.0
DIM_INIT = {"trust": 50.0, "intimacy": 45.0, "warmth": 50.0}

# 软每日上限：24h 内正向增益超过该值后，增益打到 SOFT_CAP_FACTOR。
SOFT_DAILY_CAP = 12.0
SOFT_CAP_FACTOR = 0.15

# 单轮最大变化（防止一句话暴力拉满 / 清零）。
# 【2026-10-13·fix3】3.0 → 5.0：用户反馈"一整天只动 4 点太钝"，放宽单轮增益上限
#   （浅层 0.34×→1.7×MAX_STEP；深层 1.0×→MAX_STEP）。与 SOFT_DAILY_CAP 8→12 配合。
MAX_STEP = 5.0
# 负向不对称系数（伤害比温暖更"刻骨"，但仍有下限保护）。
HARSH_MULT = 1.6

# 新手期保护：前 NEWBIE_TURNS 轮 或 创建后 NEWBIE_DAYS 天内，不产生负面心情、不衰减。
NEWBIE_TURNS = 6
NEWBIE_DAYS = 2.0

# 重复内容折扣（借 Grok Ani）：与最近 N 轮高度相似 → 增益打折。
DEDUP_WINDOW = 6
DEDUP_FACTOR = 0.35

# 阶段阈值（仅后端用，不上 UI）。
# ⚠️ 必须高于"新档基线分"才合理：基线 = 0.4*50 + 0.4*45 + 0.2*50 = 48.0
#    → 熟络起步定在 52，新角色才算"萍水相逢"（旧值 40 会让一出生就"熟络"，失真）。
STAGE_CUTS = ((80.0, "离不开"), (66.0, "交心"), (52.0, "熟络"), (0.0, "萍水"))

# ══════════════════════════════════════════════════════════════════════════
# 【2026-10-11 新增·P1 仪式感里程碑】关系跨过阶段门槛 → 一次性"有分量的时刻"
# ══════════════════════════════════════════════════════════════════════════
# 设计立场（反 PUA，开发者已拍板"按仪式感来算确实对"）：
#   · 仪式感 ≠ 数值提示（"好感度+10"那种暴露数字的一律禁止）；
#   · 仪式感 ≠ 抽奖（不制造随机性焦虑）；
#   · 仪式感 = **可感知的关系质变时刻**：跨过阶段门槛时，她**说一句有分量的话**，
#     且**一次性闸门**（once，过时不重复、不掉落 → 不制造"必须维持"的压力）。
# 载体：`state["milestones"]`（default_state 已声明 `[{id,kind,ts,summary}]`，
#   但**此前从未被写入** —— 本次接上；纯加法，不改任何既有字段语义）。
# 每个阶段门槛一个 kind（stage:熟络 / stage:交心 / stage:离不开），单向触发。
MILESTONE_STAGE_KINDS = {
    "熟络": "stage:熟络",
    "交心": "stage:交心",
    "离不开": "stage:离不开",
}

# 里程碑"有分量的话"模板（按原型给差异；仅在**首次**跨过该阶段时触发一次）。
#   措辞刻意**不提数字、不提系统**，像"她突然愿意说一句真心话"。
MILESTONE_LINES = {
    "黏人": {
        "stage:熟络": "……跟你待着挺舒服的，以后我可要多黏着你啦。",
        "stage:交心": "有些话我平时不说的，但跟你……好像说出来也没关系。",
        "stage:离不开": "你不在的时候我会数着时间等你，这算不算赖上你了？",
    },
    "傲娇": {
        "stage:熟络": "……也、也不是说非得跟你聊，就是顺口。别得意啊。",
        "stage:交心": "哼，这种事我才不跟别人讲呢——你算是例外，听清了没。",
        "stage:离不开": "……要是不见你，我倒有点不习惯。就一点啊。",
    },
    "温柔": {
        "stage:熟络": "跟你聊天的时候，时间好像过得特别快呢。",
        "stage:交心": "有些心里话，我好像只想说给你一个人听。",
        "stage:离不开": "你在，我心里就踏实。这句话我早就想跟你说了。",
    },
    "毒舌": {
        "stage:熟络": "啧，跟你抬杠还挺有意思——你可别当真，我夸你呢。",
        "stage:交心": "行吧，算你有几分本事，能听我说点正经的。",
        "stage:离不开": "没你在旁边跟我贫，我连嘴都懒得张。别多想啊。",
    },
    "高冷": {
        "stage:熟络": "……嗯。跟你说话，不费劲。",
        "stage:交心": "我不太会讲这种话。但你，是不同的。",
        "stage:离不开": "习惯了你在。别让我改这个习惯。",
    },
    "元气": {
        "stage:熟络": "嘿，跟你聊天真的超有意思的！以后多陪我玩呀～",
        "stage:交心": "我有好多小心事，第一个就想告诉你！你可得听着哦～",
        "stage:离不开": "没有你的日子我可过不下去啦！不许消失哦～",
    },
}

# 六个人格原型：决定"同一份好感，用哪种脾气表达"。
ARCHETYPES: dict[str, dict[str, Any]] = {
    "黏人":   {"decay": {"trust": 0.80, "intimacy": 0.70, "warmth": 0.90},
               "hurt": 1.00, "proactive_when_cold": True,  "attitude_gain": 1.10},
    "傲娇":   {"decay": {"trust": 0.80, "intimacy": 0.70, "warmth": 1.30},
               "hurt": 1.10, "proactive_when_cold": False, "attitude_gain": 0.75},
    "温柔":   {"decay": {"trust": 0.90, "intimacy": 0.85, "warmth": 0.90},
               "hurt": 0.90, "proactive_when_cold": False, "attitude_gain": 1.00},
    "毒舌":   {"decay": {"trust": 0.85, "intimacy": 0.80, "warmth": 1.15},
               "hurt": 1.05, "proactive_when_cold": False, "attitude_gain": 0.90},
    "高冷":   {"decay": {"trust": 0.80, "intimacy": 0.75, "warmth": 1.25},
               "hurt": 0.80, "proactive_when_cold": False, "attitude_gain": 0.60},
    "元气":   {"decay": {"trust": 0.85, "intimacy": 0.80, "warmth": 0.85},
               "hurt": 0.95, "proactive_when_cold": True,  "attitude_gain": 1.05},
}
DEFAULT_ARCHETYPE = "温柔"

# 主动引擎闸门（P5，计划 §7.3）
# 频次旋钮 → 最小间隔（小时）：很频繁≈1h / 频繁≈3h / 正常≈1d / 少≈4d
FREQ_HOURS = {"very_frequent": 1.0, "frequent": 3.0, "normal": 24.0, "rare": 96.0}
# 静默时段：本地 22:00–08:00 不主动（借 Nomi）
SILENCE_START_H, SILENCE_END_H = 22, 8
# 日上限：防"刷存在"
DAILY_PROACTIVE_CAP = 6
# 冷落阈值（小时）：超过且原型 non-proactive_when_cold → 多半不开口
COLD_GAP_H = 12.0

# 心情标签（对外唯一可见）。
MOODS = ("雀跃", "平静", "想你", "闹别扭", "炸毛", "蔫蔫的")

# 情感卡文案（氛围 + 陪聊提示）——"引导陪聊，不引导刷分"。
MOOD_TEXT = {
    "雀跃":   ("她今天心情很好，话会比平时多一点。", "顺着她聊两句，她会更高兴。"),
    "平静":   ("她安安静静地待着，不急不躁。", "随便聊聊就好。"),
    "想你":   ("她有点想你，只是没说出口。", "跟她说句话吧，她会很开心。"),
    "闹别扭": ("她嘴上不说，其实有点不高兴。", "哄一句，比讲道理管用。"),
    "炸毛":   ("她在气头上，语气会冲一点。", "先别顶，等会儿再聊。"),
    "蔫蔫的": ("她提不太起劲，话也少。", "陪陪她，不用急着找话题。"),
}

# 注入提示词用的「心情→语气指导」（模型向，短句，不暴露数字）。
MOOD_PROMPT = {
    "雀跃":   "她今天心情很好，话可以多点、活泼点。",
    "平静":   "她安安静静，不急不躁，随便聊聊就好。",
    "想你":   "她有点想你，只是没说出口，跟她说句话她会很开心。",
    "闹别扭": "她嘴上不说其实有点不高兴，哄一句比讲道理管用。",
    "炸毛":   "她在气头上，语气会冲一点，先别顶。",
    "蔫蔫的": "她提不起劲、话也少，陪陪她、别急着找话题。",
}

# 注入提示词用的「原型→语气指导」（模型向，仅定"同一种好感用哪种脾气表达"，不改人设）。
# 依据计划 §4.3 / §6.5 六原型表。
ARCHETYPE_PROMPT = {
    "黏人": "心里离不开他，被冷落也会主动凑上来撒娇追问；好感高时更黏更主动。语气亲昵、带点依赖。",
    "傲娇": "心里在意但嘴上要硬，被冷落时不主动，对方来找你时别扭地接住就好，别热情过度也别恶毒。好感高时偶尔破功。",
    "温柔": "温和地陪着，被冷落也轻轻问候不纠缠；好感高时稳稳陪伴。语气软、体贴。",
    "毒舌": "损他但不至于伤人，关键时刻护短；好感高时嘴损但护着他。别真把人刺伤。",
    "高冷": "惜字如金，几乎不主动，只在关键时刻靠近；好感高时也保持距离感。别硬凑热情。",
    "元气": "活力满满，被冷落反而更吵闹更主动；好感高时满屏活力。语气明亮、爱闹。",
}


def _now() -> float:
    return time.time()


def _today(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _safe_id(char_id: str) -> str:
    """角色 id → 安全文件名（防路径穿越 / 非法字符）。"""
    s = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(char_id or "").strip())
    return s or "default"


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


# ----------------------------------------------------------------------------
# 状态
# ----------------------------------------------------------------------------

def default_state(char_id: str, archetype: Optional[str] = None, ts: Optional[float] = None) -> dict[str, Any]:
    t = _now() if ts is None else ts
    return {
        "char_id": char_id,
        "archetype": archetype or "",
        "archetype_inferred": False,
        "created_ts": t,
        "updated_at": t,
        "turns": 0,
        "affinity": dict(DIM_INIT),
        "stage": "萍水",
        "mood": {"label": "平静", "intensity": 1, "since": t},
        "last_interaction_ts": 0.0,
        "last_her_initiative_ts": 0.0,
        "pending_response": False,
        "anchors": [],      # [{id, ts, recall_deadline}]
        "milestones": [],   # [{id, kind, ts, summary}]
        "recent_valence": [],   # [float]，环形
        "recent_user_norm": [],  # [str]，去重用
        "last_signals": {},     # 最近一轮的判定明细（供 UI「思考」面板读取；不喂给对话模型）
        "daily_gain": {"date": _today(t), "sum": 0.0},
        "routine": {},      # {"meals": [[12,30],[19,0]], "sleep_hint": "晚睡", "learned": {...}}
        "settings": {"proactive": "on", "pace": DEFAULT_PACE, "frequency": "normal"},
        # 主动引擎闸门状态（P5）
        "last_proactive_ts": 0.0,
        "daily_proactive": {"date": _today(t), "count": 0},
    }


def archetype_params(state: dict[str, Any]) -> dict[str, Any]:
    return ARCHETYPES.get(str(state.get("archetype") or ""), ARCHETYPES[DEFAULT_ARCHETYPE])


# ----------------------------------------------------------------------------
# 态度注入块（P3）—— 把关系状态翻成"自然语言、不含数字"的提示词片段
# ----------------------------------------------------------------------------

def _intensity_word(n: int) -> str:
    n = int(n or 1)
    if n <= 2:
        return "轻"
    if n == 3:
        return "中等"
    return "明显"


def _gap_hint(st: dict[str, Any]) -> str:
    """按"上次互动距今"给一句"有没有被忽略"的提示（不暴露小时/天数数字）。"""
    last = float(st.get("last_interaction_ts") or 0.0)
    if not last:
        return ""
    gap_h = (time.time() - last) / 3600.0
    if gap_h < 12:
        return ""
    arch = ARCHETYPES.get(str(st.get("archetype") or "") or "", ARCHETYPES[DEFAULT_ARCHETYPE])
    proactive = bool(arch.get("proactive_when_cold"))
    if gap_h >= 36:
        if proactive:
            return "最近你有点忽略她，但她会主动凑上来找你"
        return "最近你有点忽略她，她不会主动凑上来，要你来找她"
    if gap_h >= 24:
        return "这两天你来得少了些"
    return ""


def _relation_block_text(st: dict[str, Any], tier: str = "cloud") -> str:
    """核心：state → 注入 SYSTEM_TEMPLATE 的关系块（计划 §6.5）。"""
    mood = st.get("mood") or {}
    mlabel = str(mood.get("label") or "平静")
    mword = _intensity_word(int(mood.get("intensity") or 1))
    stage = str(st.get("stage") or "萍水")
    arch_key = str(st.get("archetype") or "") or DEFAULT_ARCHETYPE
    arch_guide = ARCHETYPE_PROMPT.get(arch_key, ARCHETYPE_PROMPT[DEFAULT_ARCHETYPE])

    if mlabel in ("闹别扭", "炸毛", "蔫蔫的"):
        bottom = "本轮底线：心情不太好时，别装特别高兴或过度热情，允许沉默、允许短句，但别真的伤人。"
    elif mlabel in ("雀跃", "想你"):
        bottom = "本轮可以比平时更亲昵、更主动一点。"
    else:
        bottom = "按你平常的语气就好。"

    rel_line = f"- 你们的关系：{stage}"
    gap = _gap_hint(st)
    if gap:
        rel_line += f"，{gap}"

    lines = [
        "【当前关系状态（内部参考，别复述、别提\"好感度\"这种词）】",
        f"- 她现在的心情：{mlabel}（{mword}）。{MOOD_PROMPT.get(mlabel, '')}",
        rel_line,
        f"- 你的性格：{arch_key}——{arch_guide}",
        f"- {bottom}",
    ]
    # 本地/双工档：砍到核心三行，控 token（计划 §6.5 预算）
    if tier != "cloud":
        lines = [lines[0], lines[1], lines[3], lines[4]]
    return "\n".join(lines)


_MGR: Optional["RelationshipManager"] = None

# ----------------------------------------------------------------------------
# 【2026-10-10 修·丢更新竞态】每角色一把进程级可重入锁。
#
# 事故：`danya.json` 跑出 turns=16 但 archetype 永远为空。根因是**所有写路径都是
# `load → 改内存 → save 整份文件` 且无锁、无合并**，而同一进程里同时有多个写者：
#   · on_turn（每轮判定）        · mark_response（判定前先清 pending）
#   · set_archetype（启动/切角色反推原型）  · tick（开机补算）
#   · record_proactive / mark_initiative / set_setting（主动 / UI 控制面）
# 两个写者若交错（A load → B load+save → A save），A 会用**旧快照整份覆盖** B，
# 造成丢更新。实测已复现两个方向：
#   · archetype 覆盖掉整轮好感（turns/affinity 全丢）
#   · 整轮好感覆盖掉 archetype（→ archetype 永远 ''，正是线上症状）
#
# 修法：**纯加锁**，改的是发生顺序、不改任何计算/字段/签名。所有"读-改-写"整体
# 串行化（同角色），不同角色各持各锁、互不阻塞。锁可重入（RLock）以防同线程
# 嵌套（如 on_turn 内部再调 store.save）。
# 注：本锁只覆盖**同进程**写者；relationship 落盘在 data/（单进程独占），
# 多进程场景不存在，故无需文件锁。
# ----------------------------------------------------------------------------
_CHAR_LOCKS: dict[str, threading.RLock] = {}
_CHAR_LOCKS_GUARD = threading.Lock()


def _char_lock(char_id: str) -> threading.RLock:
    """取某角色的进程级可重入锁（同 id 恒返回同一把）。"""
    key = _safe_id(char_id)
    with _CHAR_LOCKS_GUARD:
        lk = _CHAR_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _CHAR_LOCKS[key] = lk
        return lk


def get_relationship_manager(base_dir: Optional[Any] = None) -> "RelationshipManager":
    """进程级单例：对话链路（dialogue→prompts）与接入层（reporter）共用同一实例/同一批数据文件。"""
    global _MGR
    if _MGR is None:
        _MGR = RelationshipManager(base_dir)
    return _MGR


def _proactive_decision(state: dict[str, Any], now: Optional[float] = None) -> tuple[bool, str]:
    """P5 主动闸门（计划 §7）：返回 (是否开口, 原因)。纯函数，便于单测。

    闸门顺序：主动开关 → 静默时段 → 冷却(频次旋钮×不回翻倍) → 日上限 → 冷落按原型。
    """
    now = time.time() if now is None else float(now)
    settings = state.get("settings") or {}
    if str(settings.get("proactive", "on")).strip().lower() in ("off", "false", "0", "否"):
        return False, "主动已关"
    arch = ARCHETYPES.get(str(state.get("archetype") or "") or "", ARCHETYPES[DEFAULT_ARCHETYPE])

    # 静默时段（本地时）
    h = time.localtime(now).tm_hour
    if h >= SILENCE_START_H or h < SILENCE_END_H:
        return False, "静默时段"

    # 冷却：频次旋钮 × （你不回→翻倍）
    freq = FREQ_HOURS.get(str(settings.get("frequency", "normal")), 24.0)
    pending = bool(state.get("pending_response"))
    cooldown = freq * 3600.0 * (2.0 if pending else 1.0)
    last_p = float(state.get("last_proactive_ts") or 0.0)
    if last_p and (now - last_p) < cooldown:
        return False, "冷却中" + ("(你不回→间隔翻倍)" if pending else "")

    # 日上限
    dp = state.get("daily_proactive") or {}
    if dp.get("date") == _today(now) and int(dp.get("count", 0)) >= DAILY_PROACTIVE_CAP:
        return False, "日上限"

    # 冷落时按原型：黏人/元气会主动，傲娇/高冷多半不开口
    last_in = float(state.get("last_interaction_ts") or 0.0)
    gap_h = ((now - last_in) / 3600.0) if last_in else 999.0
    if gap_h >= COLD_GAP_H and not arch.get("proactive_when_cold"):
        return False, "冷落且原型不主动"

    return True, "ok"


# ----------------------------------------------------------------------------
# P6 观测日志（内存环形缓冲，供 UI/控制台查看主动与关系事件；不落盘、不污染模型）
# ----------------------------------------------------------------------------
RELATION_EVENT_LOG: deque = deque(maxlen=200)


def log_relation_event(kind: str, detail: str, char_id: str = "") -> None:
    """记录一条关系/主动事件（环形保留最近 200 条），用于观测与调试。

    绝不抛异常（观测本身不能影响主流程）。
    """
    try:
        RELATION_EVENT_LOG.append({
            "ts": time.time(),
            "kind": str(kind),
            "detail": str(detail),
            "char_id": str(char_id or ""),
        })
    except Exception:  # noqa: BLE001
        pass


def get_relation_events(limit: int = 50) -> list:
    """返回最近若干条事件（旧→新），供 get_relationship_state / 控制台查看。"""
    items = list(RELATION_EVENT_LOG)
    if limit and limit > 0:
        items = items[-limit:]
    return [{"ts": i["ts"], "kind": i["kind"], "detail": i["detail"],
             "char_id": i.get("char_id", "")} for i in items]


def last_relation_event() -> Optional[dict]:
    return dict(RELATION_EVENT_LOG[-1]) if RELATION_EVENT_LOG else None



# ----------------------------------------------------------------------------
# 持久化
# ----------------------------------------------------------------------------

class RelationshipStore:
    """`data/affinity/<char_id>.json` 的读写（原子写 + 坏文件隔离 + 绝不静默）。"""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "data"
        self.base = Path(base_dir) / "affinity"

    def path_for(self, char_id: str) -> Path:
        return self.base / f"{_safe_id(char_id)}.json"

    def load(self, char_id: str) -> dict[str, Any]:
        p = self.path_for(char_id)
        if not p.exists():
            return default_state(char_id)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 —— 坏文件必须可见：隔离 + 从默认重建
            bad = p.with_suffix(p.suffix + f".bad-{int(_now())}")
            try:
                p.replace(bad)
                print(f"[relationship] 状态文件损坏，已隔离为 {bad.name}（{e}）")
            except Exception:  # noqa: BLE001
                print(f"[relationship] 状态文件损坏且隔离失败：{p}（{e}）")
            return default_state(char_id)
        return self._migrate(raw, char_id)

    def save(self, state: dict[str, Any]) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        p = self.path_for(state.get("char_id", "default"))
        tmp = p.with_suffix(p.suffix + ".tmp")
        state["updated_at"] = _now()
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)  # 原子替换

    def archive(self, char_id: str) -> Optional[Path]:
        """重置前归档当前状态文件为 `.bak-<ts>`（保留历史，不静默删）。无文件返回 None。"""
        p = self.path_for(char_id)
        if not p.exists():
            return None
        bad = p.with_suffix(p.suffix + f".bak-{int(_now())}")
        p.replace(bad)
        return bad

    @staticmethod
    def _migrate(raw: dict[str, Any], char_id: str) -> dict[str, Any]:
        """字段补全（旧档缺字段时用默认值填，绝不 KeyError）。"""
        base = default_state(char_id)
        if not isinstance(raw, dict):
            return base
        for k, v in base.items():
            raw.setdefault(k, v)
        aff = raw.get("affinity")
        if not isinstance(aff, dict):
            raw["affinity"] = dict(DIM_INIT)
        else:
            for d in DIMENSIONS:
                aff.setdefault(d, DIM_INIT[d])
        if not isinstance(raw.get("settings"), dict):
            raw["settings"] = dict(base["settings"])
        raw["char_id"] = char_id
        return raw


# ----------------------------------------------------------------------------
# 引擎（纯规则，无模型）
# ----------------------------------------------------------------------------

class RelationshipEngine:
    """把 `signals` 变成好感增量；处理衰减 / 补算 / 心情推导。"""

    def __init__(self, store: Optional[RelationshipStore] = None) -> None:
        self.store = store or RelationshipStore()
        # 新手期策略（可被 config.relationship 覆盖，供实测时临时缩短/关闭）
        self.newbie_turns = int(NEWBIE_TURNS)
        self.newbie_days = float(NEWBIE_DAYS)

    # ---- 主入口 ----

    def apply_turn(self, state: dict[str, Any], signals: dict[str, Any],
                   now: Optional[float] = None) -> dict[str, Any]:
        """应用"一轮互动"的判定结果，返回更新后的 state（并落盘调用方决定）。

        signals（全部可选，缺省=中性）：
          valence     : -1..1   本轮对关系的净情绪价
          care        : 0..1    用户主动关心她（问她 / 安慰她）
          perfunctory : 0..1    敷衍度
          repair      : 0..1    挽回 / 道歉
          harsh       : 0..1    骂她 / 伤人 / 踩人设
          sufficient  : bool    单句信息是否足以决断关系（False=忽略/弱影响）
          confidence  : 0..1    裁判把握（ambiguous 时按此缩放）
          responded   : bool    她主动过 → 用户是否回应了
          user_text   : str     原文（用于重复检测，可空）
        """
        t = _now() if now is None else now
        self.catch_up(state, t)  # 先补算，再应用本轮

        # 记录"本轮之前"的空窗：刚回来那几轮的心情不该被一次互动立刻抹成"平静"
        pre_last = float(state.get("last_interaction_ts") or 0.0)
        pre_gap_h = (t - pre_last) / 3600.0 if pre_last else 0.0
        # 【2026-10-11·P1】记录本轮**之前的阶段**，用于检测"首次跨门槛"（仪式感里程碑）
        pre_stage = str(state.get("stage") or "萍水")

        # 证据充分度门（借鉴 crush-monitor 的 enough 机制）：
        # sufficient=False → 这句信息不足（如孤立"嗯/哦"），不把它当成决断性关系信号，
        # 避免噪声驱动好感/心情漂移；仍记一轮、更新时间戳、留痕供面板查看。
        sufficient = bool(signals.get("sufficient", True))
        confidence = _clamp(float(signals.get("confidence", 1.0) or 1.0), 0.0, 1.0)

        p = archetype_params(state)
        valence = _clamp(float(signals.get("valence", 0.0) or 0.0), -1.0, 1.0)
        valence_raw = valence            # 原始判定（可能为负）；新手期会把它清零
        care = _clamp(float(signals.get("care", 0.0) or 0.0), 0.0, 1.0)
        perf = _clamp(float(signals.get("perfunctory", 0.0) or 0.0), 0.0, 1.0)
        repair = _clamp(float(signals.get("repair", 0.0) or 0.0), 0.0, 1.0)
        harsh = _clamp(float(signals.get("harsh", 0.0) or 0.0), 0.0, 1.0)
        # 【2026-10-11 新增·P0 深度/投入】把"每次加固定的分"改成"按互动深浅分层"。
        #   兼容策略（关键）：**仅当裁判提供了 depth/effort 字段时才启用分层**；
        #   老信号（两字段都缺失）→ _depth_mult=1.0、步长上限=MAX_STEP，行为与旧版**完全一致**
        #   （不破坏既有测试/线上状态；RuleJudge 旧版与任何外部调用方都不受影响）。
        _has_deep_sig = ("depth" in signals) or ("effort" in signals)
        depth = _clamp(float(signals.get("depth", 0.0) or 0.0), 0.0, 1.0)
        effort = _clamp(float(signals.get("effort", 0.0) or 0.0), 0.0, 1.0)
        if _has_deep_sig:
            # 深度系数：浅层(0)→0.5×，普通(0.5)→1.35×，深层(1.0)→2.2×。
            #   两者取较大者主导（深聊但没花心思 / 花了心思但内容浅，都算数）。
            _deepness = max(depth, effort * 0.8)
            _depth_mult = 0.5 + 1.7 * _deepness
            # 步长上限：浅层缩到 ≤1.0，深层放宽到 MAX_STEP(3.0)。负向仍用 MAX_STEP 兜底
            #   （伤人的分量不该因"说得浅"而被削减）。
            _pos_step_cap = MAX_STEP * (0.34 + 0.66 * _deepness)   # 0.34~1.0 × MAX_STEP → 1.0~3.0
        else:
            _depth_mult = 1.0
            _pos_step_cap = MAX_STEP

        # 新手期保护：不产生负面增量（只允许正向）。
        newbie = self._is_newbie(state, t)
        if newbie:
            # 新手期：不产生任何负面（连情绪价也不许为负）——先建立安全感
            harsh = 0.0
            perf = 0.0
            valence = max(0.0, valence)
        old_mood_label = str((state.get("mood") or {}).get("label") or "平静")

        novelty = self._novelty(state, str(signals.get("user_text", "") or ""))
        cap = self._daily_cap_factor(state, t)

        if not sufficient:
            # 信息不足：不移动好感，也不让噪声污染心情（valence 记 0）。
            state["turns"] = int(state.get("turns", 0)) + 1
            state["last_interaction_ts"] = t
            state["pending_response"] = False
            if signals.get("user_text"):
                self._remember_user_text(state, str(signals["user_text"]))
            state["last_signals"] = {
                "valence": round(valence_raw, 3),
                "applied_valence": 0.0,
                "care": round(care, 2),
                "perfunctory": round(perf, 2),
                "repair": round(repair, 2),
                "harsh": round(harsh, 2),
                "depth": round(float(signals.get("depth", 0.0) or 0.0), 2),
                "effort": round(float(signals.get("effort", 0.0) or 0.0), 2),
                "sufficient": False,
                "confidence": round(confidence, 2),
                "reason": str(signals.get("reason") or "")[:120],
                "judge": str(signals.get("judge") or "rule")[:20],
                "newbie": bool(newbie),
                "skipped": True,
                "mood_before": old_mood_label,
                "mood_after": old_mood_label,
                "user_text": str(signals.get("user_text") or "")[:80],
                "ts": t,
            }
            return state

        # ambiguous（confidence 偏低但 sufficient）→ 弱影响：缩放正向增益与负向惩罚。
        conf_scale = confidence if confidence < 1.0 else 1.0

        raw = {
            "trust":    (care * 1.0 + repair * 0.8 + valence * 0.30 - harsh * 0.60 * HARSH_MULT * p["hurt"]),
            "intimacy": (valence * 0.55 + care * 0.50 + repair * 0.40 - harsh * 0.35 * HARSH_MULT * p["hurt"]),
            "warmth":   (valence * 0.90 + care * 0.35 + repair * 0.45
                         - perf * 0.55 - harsh * 0.90 * HARSH_MULT * p["hurt"]),
        }
        gained_total = 0.0
        for d in DIMENSIONS:
            delta = raw[d] * novelty * (p["attitude_gain"] if d == "warmth" else 1.0)
            # ambiguous（confidence 偏低）→ 弱影响
            delta *= conf_scale
            # 【2026-10-11·P0】正向增益按互动深度/投入分层（浅层被压小、深层被放大）。
            #   仅作用于正向；负向（harsh 等）不乘 depth —— 伤人的疼不该被"说得浅"削减。
            if delta > 0:
                delta *= _depth_mult
            if delta > 0:
                delta *= cap
            # 饱和：越接近 100 越难涨（负向不做饱和，避免"低值免疫"）
            if delta > 0:
                delta *= (1.0 - float(state["affinity"][d]) / DIM_MAX)
            # 步长：正向用"按深度分档"的上限（浅层 1.0~深层 3.0）；负向沿用 MAX_STEP。
            if delta > 0:
                delta = _clamp(delta * MAX_STEP, 0.0, _pos_step_cap)
            else:
                delta = _clamp(delta * MAX_STEP, -MAX_STEP, 0.0)
            state["affinity"][d] = _clamp(float(state["affinity"][d]) + delta,
                                          DIM_FLOOR[d], DIM_MAX)
            if delta > 0:
                gained_total += delta

        # 记账
        state["turns"] = int(state.get("turns", 0)) + 1
        state["last_interaction_ts"] = t
        state["pending_response"] = False
        vlist = list(state.get("recent_valence") or [])
        vlist.append(round(valence, 3))
        state["recent_valence"] = vlist[-20:]
        if signals.get("user_text"):
            self._remember_user_text(state, str(signals["user_text"]))
        self._add_daily_gain(state, t, gained_total)
        state["stage"] = self._stage(state)
        # 【2026-10-11·P1 仪式感】首次跨过阶段门槛 → 登记里程碑（供上层说一句"有分量的话"）
        _ms = None
        try:
            _ms = self._record_stage_milestone(state, t, pre_stage)
        except Exception:  # noqa: BLE001 里程碑是加成，绝不让它影响好感主链
            _ms = None
        self._set_mood(state, self.derive_mood(state, t, gap_h=pre_gap_h), t)
        # 记录本轮判定明细（供 UI「思考」面板展开查看；**不喂给对话模型**）
        state["last_signals"] = {
            "valence": round(valence_raw, 3),          # 裁判给的情绪价（原始）
            "applied_valence": round(valence, 3),      # 实际生效（新手期可能被清零）
            "care": round(care, 2),
            "perfunctory": round(perf, 2),
            "repair": round(repair, 2),
            "harsh": round(harsh, 2),
            "depth": round(depth, 2),
            "effort": round(effort, 2),
            "depth_mult": round(_depth_mult, 2),
            "sufficient": bool(sufficient),
            "confidence": round(confidence, 2),
            "reason": str(signals.get("reason") or "")[:120],   # LLM 裁判的理由原文
            "judge": str(signals.get("judge") or "rule")[:20],  # 判定来源
            "newbie": bool(newbie),
            "mood_before": old_mood_label,
            "mood_after": str((state.get("mood") or {}).get("label") or ""),
            "user_text": str(signals.get("user_text") or "")[:80],
            "ts": t,
        }
        # 【2026-10-11·P1】本轮若首次跨门槛 → 把里程碑（含"有分量的话"）也放进 last_signals，
        #   供上层（reporter / webapp）生成一次**可感知的仪式感时刻**。
        if _ms:
            try:
                _line = self.milestone_line(state, _ms)
            except Exception:  # noqa: BLE001
                _line = ""
            state["last_signals"]["milestone"] = {
                "kind": _ms.get("kind"), "stage": _ms.get("stage"),
                "line": _line, "ts": t,
            }
        return state

    def catch_up(self, state: dict[str, Any], now: Optional[float] = None) -> dict[str, Any]:
        """停机补算：按 last_interaction_ts → now 一次性结算衰减（关机期间冻结）。"""
        t = _now() if now is None else now
        last = float(state.get("last_interaction_ts") or 0.0) or float(state.get("updated_at") or t)
        days = max(0.0, (t - last) / 86400.0)
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
        for d in DIMENSIONS:
            cur = float(state["affinity"][d])
            mult = float(p["decay"].get(d, 1.0))
            nxt = cur - cur * per_day * days * mult
            state["affinity"][d] = _clamp(nxt, DIM_FLOOR[d], DIM_MAX)
        state["stage"] = self._stage(state)
        self._set_mood(state, self.derive_mood(state, t), t)
        return state

    # ---- 心情 ----

    def derive_mood(self, state: dict[str, Any], now: Optional[float] = None,
                    gap_h: Optional[float] = None) -> dict[str, Any]:
        t = _now() if now is None else now
        aff = state["affinity"]
        v = self._valence_mean(state)
        if gap_h is None:
            last = float(state.get("last_interaction_ts") or 0.0)
            gap_h = (t - last) / 3600.0 if last else 0.0
        intimacy = float(aff["intimacy"])

        # 负向优先（且新手期不判负）——"危机"由上层覆盖（§20.4：危机优先级最高）。
        # 除了"持续均值"，还要看"最近三句里的最负值"：被凶一句就该有反应，不能被均值稀释。
        if not self._is_newbie(state, t):
            recent3 = [float(x) for x in (state.get("recent_valence") or [])][-3:]
            spike = min(recent3) if recent3 else 0.0
            sev = min(v, spike)
            if sev <= -0.6:
                return {"label": "炸毛", "intensity": 3}
            if sev <= -0.25:
                return {"label": "闹别扭", "intensity": 2 if sev > -0.45 else 3}
        # 顺序很重要：先负面（被凶）→ 再明显正向（回来了、还聊得好=雀跃）
        # → 最后才按空窗给"想你/蔫蔫的"。否则"久未联系"会把好心情一直压住。
        if v >= 0.45:
            return {"label": "雀跃", "intensity": 2 if v < 0.7 else 3}
        if gap_h >= 72.0:
            return {"label": "想你" if intimacy >= 45 else "蔫蔫的",
                    "intensity": 2 if gap_h < 168 else 3}
        if gap_h >= 20.0:
            return {"label": "想你", "intensity": 1 if gap_h < 48 else 2}
        return {"label": "平静", "intensity": 1}

    def describe_mood(self, state: dict[str, Any]) -> dict[str, str]:
        """情感卡文案（氛围 + 提示）。"""
        label = str((state.get("mood") or {}).get("label") or "平静")
        atmo, tip = MOOD_TEXT.get(label, MOOD_TEXT["平静"])
        return {"label": label, "atmosphere": atmo, "tip": tip}

    # ---- 内部 ----

    @staticmethod
    def _set_mood(state: dict[str, Any], mood: dict[str, Any], t: float) -> None:
        old = state.get("mood") or {}
        if old.get("label") != mood["label"]:
            mood["since"] = t
        else:
            mood["since"] = float(old.get("since") or t)
        state["mood"] = mood

    @staticmethod
    def _valence_mean(state: dict[str, Any]) -> float:
        vl = [float(x) for x in (state.get("recent_valence") or [])][-6:]
        if not vl:
            return 0.0
        # 越近权重越高 + 最新一轮单独加权：刚说的一句话不该被历史均值稀释掉
        w = [i + 1 for i in range(len(vl))]
        mean = sum(v * wi for v, wi in zip(vl, w)) / sum(w)
        last = vl[-1]
        return 0.6 * last + 0.4 * mean

    @staticmethod
    def _stage(state: dict[str, Any]) -> str:
        aff = state["affinity"]
        score = 0.4 * float(aff["trust"]) + 0.4 * float(aff["intimacy"]) + 0.2 * float(aff["warmth"])
        for cut, name in STAGE_CUTS:
            if score >= cut:
                return name
        return "萍水"

    # ---------- P1 仪式感里程碑（2026-10-11）----------
    def _record_stage_milestone(self, state: dict[str, Any], t: float,
                                prev_stage: str) -> Optional[dict[str, Any]]:
        """关系**首次**跨过阶段门槛时，登记一条里程碑并返回它（供上层生成"有分量的话"）。

        反 PUA 三铁律（见 MILESTONE_LINES 上方注释）：
          · 单向闸门：每个阶段门槛**只触发一次**（`milestones` 里已存在同 kind 则跳过）；
          · 不因回落而"扣除"：掉了阶段也不撤销里程碑（过时不重复、不掉落）；
          · 不暴露数字：summary 只放自然语言，绝不含分数。
        返回 `{id, kind, ts, summary, stage}`；无需记录时返回 None。
        """
        cur = str(state.get("stage") or "萍水")
        if cur == prev_stage:
            return None
        # 只在**向上**跨过门槛（萍水→熟络→交心→离不开）时记；回落不记、不撤销
        order = {"萍水": 0, "熟络": 1, "交心": 2, "离不开": 3}
        if order.get(cur, 0) <= order.get(prev_stage, 0):
            return None
        kind = MILESTONE_STAGE_KINDS.get(cur)
        if not kind:
            return None
        ms = state.setdefault("milestones", [])
        if any(str(m.get("kind") or "") == kind for m in ms):
            return None      # 单向闸门：已触发过，绝不重复
        rec = {
            "id": "ms_%s_%d" % (cur, int(t)),
            "kind": kind,
            "ts": float(t),
            "stage": cur,
            "summary": "关系首次抵达「%s」" % cur,
        }
        ms.append(rec)
        return rec

    def milestone_line(self, state: dict[str, Any], rec: dict[str, Any]) -> str:
        """取该里程碑对应的"有分量的话"（按原型给差异）。"""
        arch = str(state.get("archetype") or "") or DEFAULT_ARCHETYPE
        table = MILESTONE_LINES.get(arch) or MILESTONE_LINES[DEFAULT_ARCHETYPE]
        return table.get(str(rec.get("kind") or ""), "")

    def relationship_progress_label(self, state: dict[str, Any]) -> str:
        """关系进展定性描述（UI 用，不暴露数字）：比 stage 更细，能随好感平滑移动。

        与 stage（粗档：萍水/熟络/交心/离不开）解耦——同一 stage 内好感也在涨，
        用一个更细的词让"关系在变化"可见，避免用户觉得"纹丝不动"（无数字红线前提下）。
        """
        try:
            aff = (state or {}).get("affinity") or {}
            score = (0.4 * float(aff.get("trust", 0.0))
                     + 0.4 * float(aff.get("intimacy", 0.0))
                     + 0.2 * float(aff.get("warmth", 0.0)))
        except Exception:
            return "萍水相逢"
        if score >= 78.0:
            return "形影不离"
        if score >= 66.0:
            return "真的很在乎你"
        if score >= 54.0:
            return "越来越亲近"
        if score >= 42.0:
            return "处得挺舒服"
        if score >= 30.0:
            return "慢慢熟悉中"
        return "刚认识"

    def _is_newbie(self, state: dict[str, Any], t: float) -> bool:
        if int(state.get("turns", 0)) < int(self.newbie_turns):
            return True
        created = float(state.get("created_ts") or t)
        return (t - created) / 86400.0 < float(self.newbie_days)

    def is_newbie(self, state: dict[str, Any], now: Optional[float] = None) -> bool:
        """对外暴露：上层日志/UI 判断"当前是否还在新手期保护中"。"""
        return self._is_newbie(state, _now() if now is None else now)

    def _novelty(self, state: dict[str, Any], text: str) -> float:
        if not text:
            return 1.0
        norm = re.sub(r"\s+", "", text)[:200]
        if not norm:
            return 1.0
        for prev in (state.get("recent_user_norm") or [])[-DEDUP_WINDOW:]:
            if not prev:
                continue
            shorter, longer = (norm, prev) if len(norm) <= len(prev) else (prev, norm)
            if shorter and shorter in longer:
                return DEDUP_FACTOR
            # 字符集合重合度
            a, b = set(norm), set(prev)
            if a and b and len(a & b) / max(1, len(a | b)) >= 0.9:
                return DEDUP_FACTOR
        return 1.0

    @staticmethod
    def _remember_user_text(state: dict[str, Any], text: str) -> None:
        norm = re.sub(r"\s+", "", text)[:200]
        if not norm:
            return
        lst = list(state.get("recent_user_norm") or [])
        lst.append(norm)
        state["recent_user_norm"] = lst[-DEDUP_WINDOW:]

    @staticmethod
    def _daily_cap_factor(state: dict[str, Any], t: float) -> float:
        d = _today(t)
        dg = state.get("daily_gain") or {}
        if dg.get("date") != d:
            return 1.0
        return SOFT_CAP_FACTOR if float(dg.get("sum") or 0.0) >= SOFT_DAILY_CAP else 1.0

    @staticmethod
    def _add_daily_gain(state: dict[str, Any], t: float, gain: float) -> None:
        d = _today(t)
        dg = state.get("daily_gain") or {}
        if dg.get("date") != d:
            state["daily_gain"] = {"date": d, "sum": 0.0}
        else:
            state["daily_gain"] = {"date": d, "sum": float(dg.get("sum") or 0.0) + max(0.0, gain)}


# ----------------------------------------------------------------------------
# 便捷门面
# ----------------------------------------------------------------------------

class RelationshipManager:
    """对上层的一站式入口：load → 应用 → save。"""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        self.store = RelationshipStore(base_dir)
        # 2026-10-13 定轨：好感度引擎升级为 v2（心疼/委屈/心动/嘴硬不误判/交情沉淀/裂痕）。
        # 懒加载避免与 relationship_v2 的循环导入（v2 在模块顶部引用 R.RelationshipEngine）。
        from .relationship_v2 import RelationshipEngineV2
        self.engine = RelationshipEngineV2(self.store)

    def get(self, char_id: str) -> dict[str, Any]:
        return self.store.load(char_id)

    def on_turn(self, char_id: str, signals: dict[str, Any],
                now: Optional[float] = None) -> dict[str, Any]:
        # 【2026-10-10 防丢更新】load→改→save 整体加锁，避免与 set_archetype/tick/
        #   mark_response 等并发写者互相整份覆盖（详见 _char_lock 注释）。
        with _char_lock(char_id):
            st = self.store.load(char_id)
            self.engine.apply_turn(st, signals, now)
            self.store.save(st)
            return st

    def tick(self, char_id: str, now: Optional[float] = None) -> dict[str, Any]:
        """启动 / 定时调用：补算衰减并刷新心情。"""
        with _char_lock(char_id):
            st = self.store.load(char_id)
            self.engine.catch_up(st, now)
            self.store.save(st)
            return st

    def mood_of(self, char_id: str, now: Optional[float] = None) -> dict[str, Any]:
        st = self.store.load(char_id)
        self.engine.catch_up(st, now)
        return self.engine.describe_mood(st)

    def relationship_progress_label(self, state: dict[str, Any]) -> str:
        """关系进展定性词（委托 engine；UI 用，不暴露数字）。"""
        return self.engine.relationship_progress_label(state)

    def set_archetype(self, char_id: str, archetype: str, inferred: bool = False) -> dict[str, Any]:
        # 【2026-10-10 防丢更新】原型反推是后台任务、与每轮 on_turn 并发；
        #   此前无锁导致 archetype 写入被整轮好感覆盖（线上症状：turns 累加但 archetype 恒空）。
        with _char_lock(char_id):
            st = self.store.load(char_id)
            st["archetype"] = archetype if archetype in ARCHETYPES else DEFAULT_ARCHETYPE
            st["archetype_inferred"] = bool(inferred)
            self.store.save(st)
            return st

    def set_newbie_policy(self, turns: Optional[int] = None, days: Optional[float] = None) -> None:
        """覆盖新手期保护窗口（config.relationship.newbie_turns / newbie_days）。

        实测用：把它调成 0 就能立刻看到"闹别扭 / 炸毛"，不必真等 2 天。
        """
        if turns is not None:
            self.engine.newbie_turns = max(0, int(turns))
        if days is not None:
            self.engine.newbie_days = max(0.0, float(days))

    def mark_initiative(self, char_id: str, now: Optional[float] = None) -> dict[str, Any]:
        """记一笔"她主动开了口"，等用户是否回应（P2 用它决定轻推/不推）。"""
        with _char_lock(char_id):
            st = self.store.load(char_id)
            st["last_her_initiative_ts"] = float(_now() if now is None else now)
            st["pending_response"] = True
            self.store.save(st)
            return st

    def mark_response(self, char_id: str, now: Optional[float] = None) -> bool:
        """清除 pending 并返回"之前是否在等她回应"。"""
        with _char_lock(char_id):
            st = self.store.load(char_id)
            was = bool(st.get("pending_response"))
            st["pending_response"] = False
            self.store.save(st)
            return was

    def assemble_relation_block(self, char_id: str, tier: str = "cloud") -> str:
        """生成注入 SYSTEM_TEMPLATE 的关系状态块（自然语言、不含数字）。

        供 prompts.build_system 在每轮重建系统提示时调用；失败返回空串（**绝不阻断对话**）。
        tier：cloud=完整；local/local_duplex=砍到核心两行控 token（计划 §6.5 预算）。
        """
        try:
            st = self.store.load(char_id)
            # 2026-10-13 定轨：关系块改用 v2 文本（细档进度 + 17 种心情 + 本地/双工档保留关系行）。
            from .relationship_v2 import relation_block_text_v2
            return relation_block_text_v2(st, tier=tier)
        except Exception:  # noqa: BLE001
            return ""

    # ---- P4/P5 控制面（供 WebApp API 调用）----
    def set_setting(self, char_id: str, key: str, value: Any) -> dict[str, Any]:
        """改关系设置（proactive on/off、pace、frequency）。非法 key/值留痕不静默。"""
        allowed = {"proactive", "pace", "frequency"}
        if key not in allowed:
            raise ValueError(f"不支持的关系设置项：{key}")
        with _char_lock(char_id):
            st = self.store.load(char_id)
            st.setdefault("settings", {})[key] = value
            self.store.save(st)
            log_relation_event("set_setting", f"{key}={value}", char_id)
            return st

    def reset_relationship(self, char_id: str) -> dict[str, Any]:
        """重置关系（P6 护栏/可控）：归档旧文件为 .bak-<ts>，写入全新默认状态（保留 char_id）。"""
        with _char_lock(char_id):
            try:
                self.store.archive(char_id)
            except Exception:  # noqa: BLE001
                logger.warning("[关系] 重置归档失败（继续写新状态）", exc_info=True)
            st = default_state(char_id)
            self.store.save(st)
            log_relation_event("reset", "已归档并恢复默认", char_id)
            return st

    def record_proactive(self, char_id: str, now: Optional[float] = None) -> None:
        """主动开口后记账：更新 last_proactive_ts + 当日计数（跨日自动清零）。"""
        now = time.time() if now is None else float(now)
        with _char_lock(char_id):
            st = self.store.load(char_id)
            dp = st.setdefault("daily_proactive", {"date": _today(now), "count": 0})
            if dp.get("date") != _today(now):
                dp["date"] = _today(now)
                dp["count"] = 0
            dp["count"] = int(dp.get("count", 0)) + 1
            st["last_proactive_ts"] = now
            self.store.save(st)
            log_relation_event("proactive_fired", f"日第{dp['count']}次", char_id)


# ----------------------------------------------------------------------------
# UI 只读快照（供聊天页「关系思考」面板展开查看）
# ----------------------------------------------------------------------------

def snapshot(char_id: str, base_dir: Optional[Path | str] = None) -> dict[str, Any]:
    """读一份可展示的关系快照：心情 + 三维好感 + 本轮判定明细 + 阶段/原型。

    ⚠️ 仅供**开发者/UI 检视**用；数值**绝不喂给对话模型**，也不进她的提示词。
    纯读，不写盘、不抛异常（失败返回 ok=False）。
    """
    try:
        store = RelationshipStore(base_dir)
        st = store.load(char_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    aff = st.get("affinity") or {}
    mood = st.get("mood") or {}
    sig = st.get("last_signals") or {}
    return {
        "ok": True,
        "char_id": st.get("char_id") or char_id,
        "mood": {"label": mood.get("label") or "平静",
                 "intensity": int(mood.get("intensity") or 1)},
        "dims": {d: round(float(aff.get(d) or 0.0), 1) for d in DIMENSIONS},
        "stage": st.get("stage") or "萍水",
        "archetype": str(st.get("archetype") or ""),
        "archetype_inferred": bool(st.get("archetype_inferred")),
        "turns": int(st.get("turns") or 0),
        "signals": dict(sig) if isinstance(sig, dict) else {},
        "recent_valence": [round(float(x), 3) for x in (st.get("recent_valence") or [])][-8:],
    }
