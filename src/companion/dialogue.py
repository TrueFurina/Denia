"""对话编排：上下文 + 输入 → LLM → 决策事件。

订阅 USER_TEXT / UNITY_INBOUND，调用 LLM 生成结构化决策，
发 AI_DECISION 事件（由 ws_server 广播、tts 播放、console 展示）。
同时缓存最近的屏幕活动作为对话上下文。
支持运行时角色切换（switch_character）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Any, Deque, Optional

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from ..llm.base import LLMClient
from ..llm.decision_parser import parse as parse_decision, had_json as decision_had_json
from ..llm.prompts import build_system, build_user_turn
from ..llm import web_search
from ..protocol import State, make_character_switched
from ..state_machine import StateMachine
from .conversation_history import shared_history
from .game_knowledge import SCREEN_TEXT_FRESH_S
from .memory_store import EXTRACT_HINT_RE, MEMORY_EXTRACT_PROMPT
from .persona import Persona

logger = logging.getLogger(__name__)

# 【2026-10-04 22:37 扩大短期上下文：10 轮 → 40 轮（即注入 80 条）】
# 用户 22:36 判断正确：**短期靠上下文（逐字原话）、长期靠记忆（提炼精华），
# 两者分工而非二选一**。原先只给 20 条（10 轮）太短 ⇒ 她"看起来没在听"、
# 记不住刚聊过什么；而记忆是提炼后的一句话摘要，信息密度差一个量级。
#
# 成本核算（用真实通话记录实测：单条平均 18 字）：
#   20 条 ≈  374 字 ≈  261 token（旧）
#   80 条 ≈ 1496 字 ≈ 1047 token（新）
#   占 qwen-plus/3.8-max（1M 上下文）的 **0.1%** —— 几乎免费。
# 上限由 `conversation_history.SHARED_MAXLEN = 80` 兜底（存 80 轮 = 160 条，
# 注入取最近 80 条），超出部分自然淘汰、不会无限增长。
MAX_HISTORY = 40  # 保留最近 40 轮 (user+assistant 各算 1 条) → 实际注入 80 条

# 【2026-10-23】「她没有可说的内容」失败气泡的文案。提成常量是因为有**两个**调用点
# （parse 后就是空 / 清洗后被清空）必须逐字一致——本仓最忌"同一件事两份实现"。
# 测试按这个常量断言，改动它必须同时被测试发现（否则两个出口悄无声息地漂移）。
EMPTY_REPLY_TEXT = "消息发送失败（模型没有给出可说的内容，请稍后再试）"

# 【2026-10-04】聊天工具循环的整体超时（秒）。原为 75s，对「打开 App → 搜歌 → 点播 →
# 复核」这类多步链路太紧：光多轮 LLM 往返就吃掉大半，表现为只完成第一步就被砍
# （用户看到的就是「只打开没播放」）。放宽到 120s；工具侧已改为异步等待，
# 不再用同步 sleep 冻住事件循环白白烧掉预算。
_CHAT_TOOLS_TIMEOUT_S = 120.0

# 场景意图（2026-08-22）：vision 产出的 app_hint 结构化场景分类 → 对话场景分寸。
# turn-based 每轮新鲜注入，让模型明确"用户当前在什么场景、该怎么搭话"。
# 命中才注入，未知场景（app_hint 为空/未命中）不注入。
_SCENE_INTENT_MAP: dict[str, str] = {
    "ide": "【当前场景】用户在写代码/用开发工具，专注状态：除非用户先开口或明显卡住，否则少说别打扰",
    "browser": "【当前场景】用户在浏览网页/看视频，轻松陪伴：简短搭话，别长篇大论",
    "document": "【当前场景】用户在阅读/看文档，安静陪伴：只在明显有趣/重要的内容时轻说一句",
    "chat": "【当前场景】用户在聊天/沟通，别插嘴",
    "game": "【当前场景】用户在玩游戏，按游戏知识库/话术库回应",
}

# 流式切句：从 LLM 流式 JSON 输出中实时提取 text 字段内容，按句子边界切分。
# text 是决策 JSON 的字段之一（通常最后生成）；一旦出现 "text":" 就开始捕获，
# 遇句子结束标点切出一句，实现"边生成边念 / 打字机显示"。
TEXT_VALUE_START = re.compile(r'"text"\s*:\s*"')
_SENT_BOUNDARIES = frozenset("。！？；…!?；\n")
# 聊天页"真正的句子结束"标点（分号/换行不算句末——不拿它们断气泡）
_REPLY_SENT_END = frozenset("。！？!?…")
# 聊天页单句软上限：攒到这么多字还没遇到句末标点就强制断句。与 _SentenceStreamer
#   的 24 字硬切配合——被硬切碎的半句（"…铁人也" | "撑不住呢。"）在这里合回整句，
#   否则聊天气泡会被切成不自然的半句。
_CHAT_MAX_SEG_CHARS = 36
# 【2026-10-12 学 AstrBot 分段】最小可发段长：句末标点命中但没攒够这个长度就
#   继续攒（短句自动并入下一句），避免"响。"/"嗯。"这种残句单独成一条气泡。
_CHAT_MIN_SEG_CHARS = 12

_PET_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_pet_response(raw: str) -> dict:
    """解析桌宠互动响应 → {text, action, emotion}（**保留桌宠自己的动作/表情词表**）。

    【2026-10-11 修·原始 JSON 泄漏进聊天框】此前生成端是**裸 `json.loads(response)`**：
      模型偶发把结果包在 ```json 围栏里（实测桌宠 drag 互动约 1/3 概率复现），
      json.loads 直接抛 JSONDecodeError → 兜底分支把**整段围栏 JSON** 当 text 返回 →
      `pet_interact` 再当 AI_DECISION 发出去（绕过 decision_parser）→ 用户聊天框里
      出现一大坨 `{"text": "...", "action": ...}`。
    这里先剥围栏再解析，解析不出就正则抽字段；与聊天链路不同，**不套用聊天动作词表**
    （nod/stretch/smile 等桌宠动作名要原样保留，否则驱动不了桌面模型）。
    绝不把原始 JSON 原文当说话内容。
    """
    t = (raw or "").strip()
    if not t:
        return {"text": "", "action": "nod", "emotion": "neutral"}
    body = _PET_FENCE_RE.sub("", t).strip()
    obj: dict = {}
    try:
        _loaded = json.loads(body)
        if isinstance(_loaded, dict):
            obj = _loaded
    except json.JSONDecodeError:
        obj = {}
    if not obj:
        _m = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
        if _m:
            try:
                obj["text"] = json.loads('"' + _m.group(1) + '"')
            except json.JSONDecodeError:
                obj["text"] = _m.group(1).replace("\\n", "\n")
        for _k in ("action", "emotion"):
            _mm = re.search(r'"%s"\s*:\s*"([^"]*)"' % _k, body)
            if _mm:
                obj[_k] = _mm.group(1)
    text = str(obj.get("text", "") or "").strip()
    if not text:
        # 解析不出 text：若原文像 JSON（含 "text" 键）就**别把键名当话**，宁可为空；
        # 真非 JSON（纯文本）才原样用。
        text = "" if ('"' in body and '"text"' in body) else body
    return {
        "text": text,
        "action": str(obj.get("action", "") or "").strip() or "nod",
        "emotion": str(obj.get("emotion", "") or "").strip() or "neutral",
    }

# ---- VRC 衣服/玩具指令：关键词 → 物品名（对齐 Unity VRCFeatureController.itemNodes）----
# 注意：先匹配更具体的词（毛绒帽子/毛绒内裤 优先于 毛绒），避免"毛绒帽子"被"毛绒"抢先命中。
CLOTHING_KEYWORDS: dict[str, tuple[str, ...]] = {
    # ===== 泳装（衣服材质修复5）=====
    "泳装": ("泳装", "泳衣", "泳裙"),
    "胸罩": ("胸罩", "内衣", "bra"),
    "内裤": ("内裤", "胖次"),
    "裙子": ("裙子", "裙"),
    "帽子": ("帽子",),
    "鞋子": ("鞋子", "鞋"),
    "头饰": ("头饰", "头花", "头戴花", "发饰"),
    "墨镜": ("墨镜", "眼镜"),
    "头戴花朵": ("头戴花朵", "花朵", "花"),
    "手上饰品": ("手上饰品", "手饰", "手链", "手环"),
    "脚上的装饰": ("脚上的装饰", "脚饰", "脚链"),
    "脸上贝壳": ("脸上贝壳", "贝壳"),
    "肩膀海豹": ("肩膀海豹", "海豹"),
    "腿环": ("腿环",),
    # ===== 毛绒派对（切换毛绒派对）=====
    "毛绒帽子": ("毛绒帽子",),
    "毛绒内裤": ("毛绒内裤",),
    "毛绒鞋子": ("毛绒鞋子",),
    "毛绒派对": ("毛绒派对", "毛绒衣服", "毛绒套装", "毛绒"),
    "项圈": ("项圈",),
    "蝴蝶结": ("蝴蝶结",),
    "丝袜": ("丝袜",),
    # ===== 默认衣服（达妮娅衣服默认骨骼版本）=====
    "默认衣服": ("默认衣服", "默认套装", "穿回默认"),
    "白丝": ("白丝",),
    "娅娅团子": ("娅娅团子", "团子", "丸子头"),
    # ===== 全局 =====
    "武器": ("武器", "刀", "剑"),
    "巧克力盒子": ("巧克力盒子", "巧克力", "盒子"),
    "手机": ("手机",),
    "井字棋": ("井字棋", "棋盘"),
}

# 关/开 动作词（先匹配长词，避免"收起来"里的"拿"误判）
_CLOTHING_OFF = ("脱下来", "脱掉", "摘下来", "摘掉", "收起来", "收掉", "拿下来",
                 "关掉", "关上", "卸下来", "卸掉", "脱", "摘", "卸")
_CLOTHING_ON = ("穿上", "戴上", "带上", "拿出来", "换上", "打开", "开启",
                "穿", "戴", "拿", "换")

# ---- VRC 表情指令：关键词 → 表情名（对齐 Unity VRCFeatureController.emotions 表）----
EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "生气表情": ("生气", "愤怒", "气死"),
    "震惊": ("震惊", "震惊表情"),
    "惊": ("吓一跳", "惊讶", "吓到"),
    "眨眼": ("眨眼", "挤眉弄眼", "抛媚眼", "wink"),
    "机智如我表情": ("机智如我", "机智", "得意"),
    "眯眯眼": ("眯眯眼", "咪咪眼"),
    "眯眯眼哭泣优化": ("眯眯眼哭", "哭泣"),
    "哭哭": ("哭哭", "呜呜", "哭", "流泪"),
    "尴尬": ("尴尬",),
    "疑惑": ("疑惑", "好奇", "问号"),
    "哇哦表情": ("哇哦", "哇塞", "哇"),
    "邪恶表情": ("邪恶", "奸笑", "阴险"),
    "晕了": ("晕了", "晕倒", "晕乎乎"),
    "发情": ("发情", "色色", "想要"),
    "高潮过程表情": ("高潮", "高氵朝"),
    "达妮娅被爆炒了": ("爆炒",),
}
_EMOTION_OFF = ("恢复正常", "恢复表情", "收起表情", "取消表情", "别做表情", "收起", "别")
# 表情指令句式词：只有出现明确的"做表情"动作才解析为表情指令，
# 防止"我生气了"这类自然语言被关键词拦截（情绪交由 LLM 理解）
_EMOTION_CMD_HINTS = ("表情", "做个", "做一下", "做一张", "摆个", "摆一下", "摆一个",
                      "来一个", "来个", "给我来个", "表演一下", "演示一下")
# VRC 表情名 → live2d 看板娘表情 preset（明确指令"做个XX表情"时同步驱动看板娘）
_VRC_TO_LIVE2D: dict[str, str] = {
    "生气表情": "jito", "机智如我表情": "jito", "邪恶表情": "jito",
    "震惊": "surprise", "惊": "surprise", "哇哦表情": "surprise",
    "哭哭": "usume", "眯眯眼哭泣优化": "usume",
    "眯眯眼": "smile", "眨眼": "winkL",
    "尴尬": "neutral", "疑惑": "neutral", "晕了": "neutral",
    "发情": "neutral", "高潮过程表情": "neutral", "达妮娅被爆炒了": "neutral",
}


def parse_emotion_command(text: str) -> Optional[tuple[str, bool]]:
    """解析「做个生气的表情 / 恢复正常表情」这类指令 → (表情名, on)。

    只识别明确指令句式（含"表情 / 做个 / 摆个"等指令词），自然语言里提到
    "生气"等词不触发，交由 LLM 理解情绪，避免"一说生气就摆生气脸"的机械拦截。
    命中则返回，非表情指令返回 None（继续走衣服指令 / LLM 对话）。
    注意：先匹配更长的词（如"眯眯眼哭泣优化"优先于"眯眯眼"），故 EMOTION_KEYWORDS 中
    长词在前、短词在后即可（dict 顺序保证）。
    """
    if not text:
        return None
    t = text.strip()
    # 明确指令句式才识别为表情指令（否则"我生气了"这类自然语言会误拦截）
    if not any(h in t for h in _EMOTION_CMD_HINTS):
        return None
    name = None
    for n, kws in EMOTION_KEYWORDS.items():
        if any(k.lower() in t.lower() for k in kws):
            name = n
            break
    if name is None:
        return None
    off = any(w in t for w in _EMOTION_OFF)
    return name, (not off)


def parse_clothing_command(text: str) -> Optional[tuple[str, bool]]:
    """解析「穿上裙子 / 把武器收起来」这类指令 → (物品名, on)。

    命中则返回，非衣服指令返回 None（继续走 LLM 对话）。
    """
    if not text:
        return None
    t = text.strip()
    item = None
    for name, kws in CLOTHING_KEYWORDS.items():
        if any(k.lower() in t.lower() for k in kws):
            item = name
            break
    if item is None:
        return None
    off = any(w in t for w in _CLOTHING_OFF)
    on = any(w in t for w in _CLOTHING_ON)
    return item, (not off if (off or on) else True)


class _SentenceStreamer:
    """增量切句器：feed(chunk) 返回本次切出的句子列表，flush() 取剩余未切内容。"""

    def __init__(self) -> None:
        self._raw = ""
        self._start = -1      # text 值首字符在 _raw 中的下标
        self._emitted = 0     # 已切出的 text 字符数
        self._buf = ""

    @property
    def raw(self) -> str:
        return self._raw

    def feed(self, chunk: str) -> list[str]:
        self._raw += chunk
        if self._start < 0:
            m = TEXT_VALUE_START.search(self._raw)
            if not m:
                return []
            self._start = m.end()
        content = self._extract_text()
        if self._emitted >= len(content):
            return []
        self._buf += content[self._emitted:]
        self._emitted = len(content)
        return self._drain()

    def flush(self) -> str:
        rest = self._buf
        self._buf = ""
        return rest

    def _extract_text(self) -> str:
        """从 text 值起点截取到当前已知闭合引号前的内容（处理 \\ 转义）。"""
        out = []
        i = self._start
        n = len(self._raw)
        while i < n:
            c = self._raw[i]
            if c == "\\":
                if i + 1 < n:
                    out.append(self._raw[i + 1])
                    i += 2
                    continue
                break  # 转义未完，等下一 chunk
            if c == '"':
                break  # text 值已闭合
            out.append(c)
            i += 1
        return "".join(out)

    def _drain(self) -> list[str]:
        sentences = []
        while True:
            idx = -1
            for i, c in enumerate(self._buf):
                if c in _SENT_BOUNDARIES:
                    idx = i
                    break
            if idx < 0:
                if len(self._buf) >= 24:  # 无标点长句强制切，避免迟迟不出声
                    sentences.append(self._buf)
                    self._buf = ""
                break
            sentences.append(self._buf[:idx + 1])
            self._buf = self._buf[idx + 1:]
        return sentences

# 游戏时刻 → 口语化经历（供对话上下文引用，实现"对话记忆闭环"）
MOMENT_EVENT_LABEL = {
    "kill": "你击杀了敌人",
    "kill_bot": "你打死了人机",
    "died": "你阵亡了",
    "victory": "你赢了这局",
    "defeat": "你输了这局",
    "clear": "你通关了",
    "levelup": "你升级了",
    "highlight": "你打出了高光操作",
    "default": "游戏里发生了关键时刻",
}

# 值得记录的游戏状态切换（进入结算/大厅/选模式/等待回合等），记录为"最近经历"
KEY_STATE_RE = re.compile(
    r"结算|大厅|排名|等待|选择|模式|开始|进入|退出|胜利|失败|升级|商店|仓库|加载|观战|出生"
)


def _annotate_activity_age(summary: str, ts: float) -> str:
    """给屏幕活动摘要追加时效标注：超过 30s 未更新就明确告知 AI 是较早前的画面状态，
    避免把旧的屏幕内容当成"现在正在发生"（牛头不对马嘴）。
    """
    if not ts:
        return summary
    ago = time.time() - ts
    if ago > 180:
        return f"{summary}（较早前的画面状态，可能已变化）"
    if ago > 30:
        return f"{summary}（{int(ago)}秒前的画面状态）"
    return summary


def _memory_intent_judge(cfg: Any) -> bool:
    """读取 config.yaml memory.intent_judge 开关（意图小模型前置，默认开）。"""
    try:
        mem_cfg = getattr(cfg.ns, "memory", None)
        if mem_cfg is not None:
            return bool(getattr(mem_cfg, "intent_judge", True))
    except Exception:  # noqa: BLE001
        pass
    return True


def _memory_inject_enabled(cfg: Any) -> bool:
    """读取 config.yaml memory.inject_context 开关（记忆是否注入 LLM 上下文）。

    关闭后记忆库照常存储新记忆（提炼/纠正仍记录），只是不再注入对话/搭话
    上下文——对小模型（如 9B）注入记忆容易产生歧义/幻觉时，可只存不用。
    """
    try:
        mem_cfg = getattr(cfg.ns, "memory", None)
        if mem_cfg is not None:
            return bool(getattr(mem_cfg, "inject_context", True))
    except Exception:  # noqa: BLE001
        pass
    return True


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


def _parse_extract_json(raw: str) -> Optional[dict]:
    """从 LLM 输出里鲁棒解析提取 JSON（容忍前后多余文字/截断）。"""
    m = _JSON_OBJECT_RE.search(raw or "")
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


# 强纠正句式：只有这类话才值得在"LLM 完全失败"时原样兜底保存。
# 刻意不放"不是"——反问/疑问句（"我刚刚不是死了吗"）命中率太高，会把普通对话固化成立规矩。
_STRONG_CORRECT_RE = re.compile(
    r"都说了|说了多少遍|说了几次|记住了吗|懂了吗|给我记住|我说过|别再|别叫|叫我|"
    r"以后别|以后不要|不要再|说了不要"
)


class Dialogue:
    def __init__(
        self,
        bus: EventBus,
        llm: LLMClient,
        state: StateMachine,
        persona: Persona,
        cfg: Config,
        game_knowledge: Any = None,
        app_knowledge: Any = None,
        memory_store: Any = None,
        session: Any = None,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._state = state
        self._persona = persona
        self._cfg = cfg
        # 【2026-10-02】视觉路由（看屏/聊天看图/看视频共用）：聊天发图、发视频链接
        # 走视觉模型时用到。运行时由 webapp.set_runtime 注入（Dialogue 构造期可能为 None）。
        self._vision_router = None
        self._game_knowledge = game_knowledge  # GameKnowledge（可选）：识别到的游戏情报注入上下文
        self._app_knowledge = app_knowledge  # AppKnowledge（可选）：识别到的软件说明注入上下文
        self._memory_store = memory_store    # MemoryStore（可选）：长期记忆（纠正/偏好），跨会话保留
        # 【2026-10-01 A-4】登记进程级单例，让 agent_brain 等操作路径也能写同一份长期记忆
        try:
            from .memory_store import set_memory_store
            if memory_store is not None:
                set_memory_store(memory_store)
        except Exception:  # noqa: BLE001
            pass
        # 【2026-10-01 A-5】启动即按当前角色隔离长期记忆（否则只有切换角色后才生效）
        try:
            if self._memory_store is not None and hasattr(self._memory_store, "set_character"):
                self._memory_store.set_character(
                    str(getattr(self._cfg, "active_character_id", "") or ""))
        except Exception:
            logger.debug("长期记忆设置初始角色作用域失败", exc_info=True)
        # 【2026-10-11·多角色隔离】启动即把共享会话历史的活动指针钉到当前角色，
        #   使打字与双工两条写入路径都落到该角色独立的历史桶（换人不混淆）。
        try:
            from .conversation_history import set_active_char as _set_hist_char
            _set_hist_char(str(getattr(self._cfg, "active_character_id", "") or ""))
        except Exception:
            logger.debug("共享会话历史设定初始角色失败", exc_info=True)
        self._session = session              # DeltaGameSession（可选）：本局数据，主动对话时也能接上本局
        self._last_extract_at: float = 0.0   # 记忆提炼节流
        # 【2026-10-11 新增·会话级情节记忆】按"累积对话轮数"触发一次 episode 提炼
        #   （不靠关键词白名单 → 覆盖"平淡讲事"）。_episode_turn_ctr 为自上次提炼以来
        #   的对话轮数；到阈值才提炼，避免每轮都调 LLM。
        self._episode_turn_ctr: int = 0
        self._episode_min_turns: int = 6     # 每累积 6 轮触发一次（可被 config 覆盖）
        # 【2026-10-11 新增·Plan B 反思聚合】每积累 N 条新 episode 才聚合一次画像洞察
        self._reflect_min_episodes: int = 4
        self._reflect_last_ep_ts: float = 0.0   # 上次反思聚合覆盖到的 episode 时间水位
        # 【2026-10-11 新增·整段记忆提炼】惰性构造的合成器（见 _get_synthesizer）
        self._synth = None
        self._history: list[dict[str, str]] = []
        self._last_activity: dict[str, Any] = {}
        self._last_activity_ts: float = 0.0  # 最近一次屏幕活动识别时间（判断时效，避免把旧画面当当前状态）
        self._event_log: Deque[dict] = deque(maxlen=8)  # 最近经历（游戏时刻/状态切换/自己说过的话），供对话上下文引用

    def set_vision_router(self, vr: Any) -> None:
        """注入视觉路由（看屏/聊天看图/看视频共用）。由 webapp.set_runtime 调用。"""
        self._vision_router = vr

    # ==================== 聊天自动适配器（2026-10-02） ====================
    # 三类输入自动分流：纯文本→文本模型；图片（上传/图片链接）→视觉模型；
    # 视频平台链接→视觉模型（陪你看视频）。语音转写/社交通道走各自既有路径。
    @staticmethod
    def _extract_images(d: dict) -> list[bytes]:
        """从 USER_TEXT 事件取出图片字节（前端传 base64 data URL 或纯 base64）。"""
        raw = d.get("images") or []
        out: list[bytes] = []
        import base64 as _b64  # noqa: PLC0415
        for item in raw:
            if not item:
                continue
            try:
                if isinstance(item, (bytes, bytearray)):
                    out.append(bytes(item))
                    continue
                s = str(item).strip()
                if s.startswith("data:"):
                    _, _, b64 = s.partition(",")
                    s = b64
                s = s.strip()
                if not s:
                    continue
                out.append(_b64.b64decode(s))
            except Exception:  # noqa: BLE001 单张坏图不影响其它
                logger.debug("聊天图片解码失败，跳过")
        return out

    def _vision_enabled_for_chat(self) -> bool:
        """视觉路由可用（已配置 + 视觉总闸开 + 有未冷却候选）。"""
        vr = self._vision_router
        if vr is None:
            return False
        try:
            return bool(vr.vision_available)
        except Exception:  # noqa: BLE001
            return False

    def _vision_chat_system_prompt(self) -> str:
        """看图聊天用的轻量人设系统提示（自然语言对话，不走决策 JSON）。"""
        name = "达妮娅"
        try:
            name = str(getattr(self._persona, "name", "") or name)
        except Exception:  # noqa: BLE001
            pass
        return (
            f"你是{name}，一个温柔、有点俏皮的 AI 陪伴女孩，正在和主人用文字和图片聊天。"
            "请用自然口语化的中文回应，像亲密朋友一样，别用书面腔。"
            "如果图片里有需要描述或讨论的内容，自然地融入回答，不要生硬报幕；"
            "如果和之前的对话相关，自然地接上。"
        )

    def _shrink_images(self, images: list[bytes], max_bytes: int = 1200 * 1024,
                       max_edge: int = 1600) -> list[bytes]:
        """把过大的图片等比压缩，降低云端 VLM 超时/413 风险。

        【2026-10-04 20:47 治"大图必然超时"】20:39 实锤：用户发了 12.7MB 的图，
        转 base64 后 16.9MB，再乘视觉竞速并发数（修前 ×19 ≈ 320MB）⇒ dashscope
        直接 ReadTimeout、视觉对话整体超时、消息彻底不回。

        前端（`index.html.compressImage`）已在**选图时**压到 700KB，本方法是后端兜底：
        覆盖 QQ/微信等其它渠道、以及前端压缩失败的情况。
        压缩失败**原样返回**（不因图片问题让整条消息发不出去）。
        """
        out: list[bytes] = []
        for raw in (images or []):
            if not raw:
                continue
            if len(raw) <= max_bytes:
                out.append(raw)
                continue
            try:
                import io as _io
                from PIL import Image  # type: ignore
                img = Image.open(_io.BytesIO(raw))
                img.load()
                w, h = img.size
                scale = min(1.0, float(max_edge) / float(max(w, h) or 1))
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                if scale < 1.0:
                    img = img.resize((nw, nh), Image.LANCZOS)
                buf = _io.BytesIO()
                try:
                    img.save(buf, format="JPEG", quality=85, optimize=True)
                except Exception:  # noqa: BLE001 透明图等退回原格式
                    buf = _io.BytesIO()
                    img.save(buf, format="PNG", optimize=True)
                data = buf.getvalue()
                if data and len(data) < len(raw):
                    logger.info("聊天图片过大已压缩: %d KB → %d KB（%dx%d → %dx%d）",
                                len(raw) // 1024, len(data) // 1024, w, h, nw, nh)
                    out.append(data)
                else:
                    out.append(raw)
            except Exception:  # noqa: BLE001
                out.append(raw)
        return out

    def _remember_seen_images(self, vlm_answer: str, user_text: str,
                              img_count: int = 1) -> bool:
        """把「看图后 VLM 的描述」提炼成一条长期记忆，实现跨通道连续。

        【2026-10-04 21:55 新增】背景（用户 21:50 实锤）：通话里她主动说
        「我已经迫不及待想看啦」，用户随后把照片发进聊天 —— 但看图链路**从不写记忆**
        （`vision_router` 零 memory 代码），所以下次通话她压根不知道有过这张图，
        表现为"我完全没印象 / 你上次不是说要给我看吗"。

        为什么直接用 VLM 描述而不再调一次 LLM 提炼：
          · VLM 刚刚**已经真的看过这张图**，它的一句话描述就是最可靠的事实来源；
          · 再调一次文本 LLM 提炼等于"让没看过图的人转述看过图的人说的话"，
            反而容易丢细节或编造（这正是 20:15 修的"编歌名"那类幻觉）。
        代价控制：截断到 60 字、按句号断句，避免把整段描述塞进记忆库。
        """
        store = self._memory_store
        if store is None or not vlm_answer:
            return False
        try:
            if not self._memory_inject_enabled():
                return False
        except Exception:  # noqa: BLE001
            return False
        desc = (vlm_answer or "").strip()
        # 取第一句（VLM 通常先给整体描述）
        first = re.split(r"[。！？\n]", desc)[0].strip() or desc
        # 剥掉无信息量的开头（VLM 常见套话）："这张图/图中/图片里/画面里/图里…"，
        # 以及"图片中的内容是/图片显示的是/我看到的是"这类引导句 —— 留着会变成
        # 记忆里的一句废话，还占 token。
        first = re.sub(
            r"^(?:这张图|图中|图片里|画面里|图里|照片里|图片中|照片中)"
            r"(?:的?(?:内容|画面|东西))?(?:是|为|显示的是|拍的是)?[，,、：:是]?\s*",
            "", first)
        first = re.sub(
            r"^(?:图片中的内容是|图片的内容是|图片显示的是|图片显示的是|画面中|我看到的是|我看到的是)"
            r"[，,、：:]?\s*", "", first)
        if not first:
            return False
        first = first[:60].rstrip("，,、")
        if len(first) < 4:
            return False
        # "这是一张…" 只去掉引导词「这是」，**保留后面的量词**（"一张秋天的…"）；
        # 早先写成剥量词会把"张秋天的银杏叶"这种病句放出来。
        first = re.sub(r"^这(?:是|是一张|是一幅|是一个|是一张)", "", first)
        lead = "用户发给我看过"
        if img_count > 1:
            lead = f"用户发给我看过 {img_count} 张图，其中"
        fact = f"{lead}：{first}。"
        try:
            added = store.add(fact, type="user", source="vision")
        except Exception:  # noqa: BLE001
            return False
        if added:
            logger.info("看图记忆已写入: %s", fact)
        return bool(added)

    async def _respond_vision_chat(self, images: list[bytes], text: str, source: str) -> None:
        """图片/看图聊天：直接让视觉模型看图 + 文本回答（自然语言），结果当 AI_DECISION 播。"""
        await self._state.transition(State.THINKING, "dialogue:vision")
        vr = self._vision_router
        try:
            # 【2026-10-04 20:47】后端兜底压缩：前端已在选图时压过，这里再挡一层
            # （覆盖 QQ/微信渠道与前端压缩失败的情况），避免超大图打爆云端 VLM。
            try:
                images = self._shrink_images(images)
            except Exception:  # noqa: BLE001
                pass
            # 【2026-10-04 20:50 治"发了图就一直不回"】20:39-20:40 实锤：云端 VLM
            # 全部 ReadTimeout → 这里死等满 75s 才抛错，而**这 75 秒里界面毫无反应**，
            # 用户体感就是"消息彻底不回"。现在：
            #   · 超时从 75s 收到 40s（VLM 正常 3~10s，40s 足够）；
            #   · 超时/网络类失败**自动降级回文本聊天**（把"图里大概是什么"的空档
            #     交给文本模型，至少不冷场），并明确告诉用户"看图超时、已改用文字"，
            #     绝不让用户对着一个不动的界面干等。
            _vision_failed = False
            try:
                ans = await asyncio.wait_for(
                    vr.vision_chat(images, text,
                                   system_prompt=self._vision_chat_system_prompt()),
                    timeout=40.0,
                )
            except asyncio.TimeoutError:
                _vision_failed = True
                logger.warning("看图超时(40s)，降级为文本回复（原图 %.0f KB）",
                               sum(len(x) for x in (images or [])) / 1024)
            except Exception as e:  # noqa: BLE001 网络/额度/开关类
                _vision_failed = True
                logger.warning("看图失败（%s: %s），降级为文本回复",
                               type(e).__name__, str(e)[:120])
            if _vision_failed:
                _hint = (f"（用户发来 {len(images)} 张图片，但我的看图能力这次没接上"
                         f"——网络超时或额度问题。我看不到图，"
                         f"请直接问他图片里是什么，或稍后再让我看一次。）")
                try:
                    await self.respond((text or "") + " " + _hint, source=source)
                except Exception:  # noqa: BLE001
                    pass
                return
        except Exception as e:  # noqa: BLE001
            logger.exception("视觉对话失败")
            # 【2026-10-04 修·发图不回】此前视觉失败只发笼统错误气泡，前端几乎无感，
            # 用户体感是"发图啥也不说"。这里按失败类别给一句明确、可行动的话：
            #  - 总开关关着 → 提示去开开关；
            #  - 额度耗尽 → 明确"看图额度用完、文字聊天正常"；
            #  - 其它 → 兜底文案。保证用户一定收到回话。
            _err = str(e)
            if "视觉能力已关闭" in _err or "vision_models.enabled=false" in _err:
                _msg = ("我的看图功能现在关着呢~ 你到设置里把「视觉感知」那个开关打开，"
                        "或者先打字告诉我图片里是什么，我也能帮你想想呀。")
            elif "quota" in _err.lower() or "额度" in _err or "403" in _err:
                _msg = ("我看图的模型免费额度用完了，这次没法看图啦😣 文字聊天不受影响，"
                        "你直接描述一下图片我也能帮你~ 要恢复看图去阿里云给视觉模型充点值就行。")
            else:
                _msg = self._fail_text(e)
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "neutral", "action": "talk",
                "text": _msg, "failed": True,
                "channel": "", "reply_target": None,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
            await self._state.to_idle("dialogue")
            return
        ans = (ans or "").strip()
        if not ans:
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "neutral", "action": "talk",
                "text": "我好像没看清这张图，能再发一次或者描述一下吗？", "failed": True,
                "channel": "", "reply_target": None,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
            await self._state.to_idle("dialogue")
            return
        # 【2026-10-23】VLM 也可能只吐 markdown 骨架（列表/加粗符号）或纯舞台动作
        # （"（翻个白眼）"）→ 清洗后为空。与 respond 主链 / 工具链共用同一道闸
        # （_guard_speakable），否则发一条 text="" 的决策被前端静默丢弃 =
        # "图也发了、话也不说"；或把舞台指示当图片描述播出去（TTS 念"翻个白眼"）。
        # 清洗口径与工具链的 _emit_seg 一致：_drop_stage_dirs（"全删空"时返回空串，
        # 不走 _strip_stage_directions 的"退回原文"保底——保底会把残骸放出来）。
        # **不加 _humanize_reply 的句数截断**：图片描述不该被"回多长看对方"砍断，
        # 视觉链本就该说清图里是什么（10-12 工具链对齐时也没给它加截断）。
        _ans = self._drop_stage_dirs(self._strip_markdown(ans))
        if not await self._guard_speakable(_ans, "", None):
            return
        # 记录历史（与打字聊天共用共享会话历史，双向连贯）
        shared_history().add_turn(
            "user", ((text or "") + f" [图片×{len(images)}]"), source="chat")
        shared_history().add_turn("assistant", _ans, source="chat")
        # 【2026-10-04 21:55 新增·看图后写记忆】此前 `vision_router` 零记忆代码、
        # `_respond_vision_chat` 看完图只记 shared_history（**进程内**、重启即失），
        # ⇒ 用户 21:50 通话里说「我等会发给你看照片」、她「我已经迫不及待想看啦」，
        # 但**用户真把图发进聊天后，她下次通话完全不知道这张图存在过** ——
        # 表现为"你上次不是说给我看吗/我完全没印象"，跨通道记忆断裂。
        #
        # 现在：VLM 已经看过图并给出自然语言描述 ⇒ 直接把**这份描述**提炼成一条
        # 长期记忆（type=user），这样①通话时能从记忆里主动提起 ②跨通道连续。
        # 只在"记忆库可用 + 注入开启"时做；失败绝不影响看图回复本身。
        try:
            self._remember_seen_images(ans, text, len(images))
        except Exception:  # noqa: BLE001
            logger.debug("看图记忆提炼失败（不影响回复）", exc_info=True)
        await self._bus.publish(Event(EventType.AI_DECISION, {
            "emotion": "neutral", "action": "talk", "intensity": 3,
            # 【23:11】VLM 常顺手给 markdown（列表/加粗），一并剥掉（见上方 _ans）
            "text": _ans, "streamed": False,
            "channel": "", "reply_target": None,
            "user_activity": self._activity_payload(),
            "voice": self._current_voice(),
        }, "dialogue"))
        # 【2026-10-11】旧单句白名单提炼**已断开**（保留代码可回退；实测 1:404 触发率 +
        #   "我晚上七点吃的饭"不命中白名单）。改走"整段上下文 LLM 提炼"：
        #   asyncio.create_task(self._maybe_extract_memory(text))   # ← 旧方案，已断开
        #   asyncio.create_task(self._maybe_extract_episode(text))  # ← 旧情节提炼，已断开
        asyncio.create_task(self._maybe_synthesize_memory())  # 整段提炼（上下文快超限时）
        await self._state.to_idle("dialogue")

    def subscribe(self) -> None:
        self._bus.subscribe(EventType.USER_TEXT, self._on_user_text)
        self._bus.subscribe(EventType.SCREEN_ACTIVITY, self._on_activity)
        self._bus.subscribe(EventType.UNITY_INBOUND, self._on_unity_inbound)

    def _record_event(self, text: str) -> None:
        """记录一条"最近经历"（保留最近 8 条，连续相同内容去重）。"""
        if self._event_log and self._event_log[-1]["text"] == text:
            return
        self._event_log.append({"ts": time.time(), "text": text})

    def _format_events(self) -> str:
        """把最近经历格式化成时间线文本：如「2分钟前 你击杀了敌人；刚刚 你阵亡了」。"""
        if not self._event_log:
            return ""
        now = time.time()
        parts = []
        for ev in self._event_log:
            ago = int(now - ev["ts"])
            if ago < 5:
                t = "刚刚"
            elif ago < 60:
                t = f"{ago}秒前"
            elif ago < 3600:
                t = f"{ago // 60}分钟前"
            else:
                t = f"{ago // 3600}小时前"
            parts.append(f"{t} {ev['text']}")
        return "；".join(parts)

    async def _on_activity(self, e: Event) -> None:
        d = e.data or {}
        if not isinstance(d, dict) or not d.get("summary"):
            return
        self._last_activity = d
        self._last_activity_ts = time.time()
        summary = str(d.get("summary") or "").strip()
        moment = (d.get("moment") or "").strip()
        if moment:
            # 游戏关键时刻（击杀/阵亡/胜负…）记入最近经历
            self._record_event(MOMENT_EVENT_LABEL.get(moment, MOMENT_EVENT_LABEL["default"]))
        elif d.get("app_hint") == "game" and KEY_STATE_RE.search(summary):
            # 游戏状态切换（结算/排名/大厅/选模式…）也值得记住
            self._record_event(summary)

    async def _on_user_text(self, e: Event) -> None:
        d = e.data or {}
        # 主动搭话：这是 AI 自己说的话，直接发声展示，不再喂给 LLM，
        # 否则 AI 会对自己说的话再回复一遍，变成自言自语
        if d.get("is_proactive"):
            await self._broadcast_proactive(d)
            return
        # 【2026-10-13·fix2】双工用户话不进聊天引擎：避免对"通话里用户说的话"
        # 也生成一条聊天回复（双工落盘/关系由 relationship_reporter 单独处理）。
        if str(d.get("source") or "") == "duplex":
            return
        text = str(d.get("text", ""))
        # VRC 表情指令：优先于衣服指令（一次只做一个表情），直接下发 Unity 直控
        emo_cmd = parse_emotion_command(text)
        if emo_cmd is not None:
            name, on = emo_cmd
            await self._bus.publish(Event(EventType.VRC_EMOTION, {
                "name": name,
                "on": on,
                "source": d.get("source", "unknown"),
            }, "dialogue"))
            reply = f"好呀，做个{name}～" if on else "好的，表情收起来了～"
            await self._state.transition(State.THINKING, "dialogue:emotion")
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "gentle",
                "action": "talk",
                "text": reply,
                "live2d_expr": _VRC_TO_LIVE2D.get(name, "neutral"),
                "is_proactive": True,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
            return
        # VRC 衣服/玩具指令：直接下发 Unity 直控，不再调用 LLM
        cmd = parse_clothing_command(text)
        if cmd is not None:
            item, on = cmd
            await self._bus.publish(Event(EventType.VRC_CLOTHING, {
                "item": item,
                "on": on,
                "source": d.get("source", "unknown"),
            }, "dialogue"))
            verb = "穿上" if on else "脱下"
            reply = f"好呀，我帮你把{item}{verb}了～"
            await self._state.transition(State.THINKING, "dialogue:clothing")
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "gentle",
                "action": "talk",
                "text": reply,
                "is_proactive": True,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
            return
        # —— 桌面消息全链路放后台任务（2026-10-04 关键修复：UI 卡片"后出"）——
        # 【为什么不能在这里 await】本函数是总线 USER_TEXT 处理器，而总线是串行派发
        # （event_bus.run 逐个 await handler）。看视频/链接要 10~60s、LLM 回复也要数秒：
        # 在这里 await 会独占总线，期间发布的 AGENT_STATUS / VIDEO_WATCH_PROGRESS 全部
        # 排队，直到回复结束才派发 → 前端"正在看/正在操作电脑"卡片只在最后一次性出现
        # （用户实测"UI 是后出的"）。改为 create_task 立即返回，总线保持空闲、进度事件
        # 实时派发；多条消息之间仍用 _desktop_msg_lock 串行，保持原有顺序语义。
        source = d.get("source", "unknown")
        _DESKTOP_SOURCES = ("webui", "voice", "console", "")
        if source in _DESKTOP_SOURCES:
            images = self._extract_images(d)
            task = asyncio.create_task(
                self._process_desktop_message(d, source, text, images))
            self._desktop_msg_task = task
            # 【2026-10-19 修复·第七处静默失效】后台任务里若 respond 的"无 try 包裹尾段"
            # （剥 markdown / 拟人化 / shared_history 记录 / 发 AI_DECISION）或
            # _respond_vision_chat / 媒体摄入抛异常，原本因 create_task 没有 done_callback
            # → 异常既不会被 await 者取出、也没有回调取出 → asyncio 在任务 GC 时打印
            # "Task exception was never retrieved"，而用户全程无感知：典型"她听到却不回答"。
            # 这里补一个兜底回调：取出异常（消除警告）→ 记日志 → 补发一条 failed
            # AI_DECISION（前端红字失败气泡），让用户至少看到"出错了"而非对着静音的桌宠。
            def _on_desktop_msg_done(t: asyncio.Task) -> None:
                try:
                    t.result()  # 正常完成返回 None；异常在此抛出并被下方捕获
                except asyncio.CancelledError:
                    return  # 主动取消（退出时）不算失败
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[桌面消息] 处理失败（source=%s），降级为失败气泡", source)
                    try:
                        # publish_threadsafe 同步入队、不依赖调用方是否 await 本任务
                        # （本任务本就是 fire-and-forget）。failed=True：TTS 不合成播放、
                        # 前端用红字失败气泡提示，而不是让用户对着沉默的桌宠。
                        self._bus.publish_threadsafe(Event(EventType.AI_DECISION, {
                            "emotion": "neutral",
                            "action": "talk",
                            "text": "消息处理失败了，请稍后再试～",
                            "failed": True,
                            "channel": "",
                            "reply_target": None,
                            "user_activity": self._activity_payload(),
                            "voice": self._current_voice(),
                        }, "dialogue"))
                    except Exception:  # noqa: BLE001
                        logger.exception("[桌面消息] 补发失败气泡也失败了（总线异常）")
            task.add_done_callback(_on_desktop_msg_done)
            return
        # 非桌面来源（QQ/微信等社交通道）：保持原有同步路径（通道内已自行摄入媒体）
        # 【2026-10-23】channel / reply_target 从事件载荷里取着往下传。
        # 当前生产里社交通道并不走 USER_TEXT（它们由 base._feed_brain 直接调
        # respond(channel=self.name)），所以这两个键现在是缺省空、行为不变。但**这一跳
        # 曾经和工具链一样是断的**：谁若从 USER_TEXT 喂一条通道消息进来，回复会因为
        # channel 为空而被通道侧忽略、同时被桌面播走。补齐之后，载荷里带了就生效。
        await self.respond(text, source=source,
                           channel=str(d.get("channel") or ""),
                           reply_target=d.get("reply_target"))

    async def _process_desktop_message(self, d: dict, source: str, text: str, images) -> None:
        """后台执行桌面消息全链路：图片聊天 / 媒体摄入（陪看视频）/ LLM 回复。

        与总线派发解耦——本任务 await 期间，总线可继续派发进度事件，
        _on_video_watch_progress / _on_agent_status 才能实时更新聊天流卡片。
        _desktop_msg_lock 保证用户连发多条消息时仍按序处理（维持旧串行语义）。
        """
        lock = getattr(self, "_desktop_msg_lock", None)
        if lock is None:
            lock = self._desktop_msg_lock = asyncio.Lock()
        async with lock:
            _DESKTOP_SOURCES = ("webui", "voice", "console", "")
            # —— 聊天发图 / 看图（自动适配器：图片 → 视觉模型）——
            # 【2026-10-04 19:40 关键修复·发图被静默丢弃 → 模型跑去截屏】
            # 旧门槛是 `if images and ... and self._vision_enabled_for_chat()`：
            # 视觉不可用时**整个分支被跳过**，图片字节直接丢弃、只剩纯文本进 LLM。
            # 实锤（19:20 用户发达妮娅立绘 +「你认识这是谁吗」）：
            #   [发送诊断] send_chat: text_len=7   ← 只传了 7 个字，图片没了
            #   [桌面媒体] 摄入结束 … 上下文=0字   ← 零上下文
            #   聊天工具 see_screen({"question":"屏幕上现在显示的是什么…"})
            #   → see_screen -> 屏幕上显示的是一个电脑桌面，上面有多个窗口…
            # 模型拿不到图，就去调 see_screen 截**电脑屏幕**，再把屏幕上碰巧出现的
            # 东西（这张图正好显示在聊天窗口里）当成用户发的图来描述 ⇒
            # **答非所问，且换个场景就会变成纯幻觉**（"看"的是屏幕不是用户发的图）。
            #
            # 现在：**只要用户发了图就一定走视觉分支**。
            # 视觉不可用时 `_respond_vision_chat` 会给出明确可行动的话
            # （"看图功能关着，去设置里开" / "额度用完"），而不是让模型去截屏瞎猜。
            if images and source in _DESKTOP_SOURCES:
                await self._respond_vision_chat(images, text, source)
                return
            # —— 桌面陪你看视频 / 链接解析（G 扩展）——
            # 含视频/链接时先让 AI "看"过再讨论；进度经 _make_video_progress 实时上屏
            enriched = text
            if self._desktop_media_enabled():
                try:
                    await self._state.transition(State.THINKING, "dialogue:media")
                except Exception:  # noqa: BLE001
                    pass
                import time as _time
                _t0 = _time.time()
                try:
                    media_ctx = await self._desktop_video_context(
                        text, on_progress=self._make_video_progress())
                except Exception:  # noqa: BLE001
                    logger.exception("[桌面媒体] 摄入失败 source=%s", source)
                    media_ctx = None
                logger.info("[桌面媒体] 摄入结束 source=%s 用时%.1fs 上下文=%d字",
                            source, _time.time() - _t0, len(media_ctx or ""))
                if media_ctx:
                    enriched = media_ctx + "\n\n" + text
            await self.respond(enriched, source=source)

    def _make_video_progress(self):
        """生成一个同步进度回调：把看视频各阶段通过事件总线发给前端进度条。

        同步（可在 yt-dlp 下载钩子里直接调用）；内部用 publish_threadsafe 非阻塞投回主循环。
        """
        bus = self._bus

        def _cb(stage, message, done=False, pct=None, meta=None):
            try:
                payload = {"stage": stage, "message": message,
                           "done": done, "pct": pct}
                if isinstance(meta, dict):
                    payload["kind"] = meta.get("kind")
                    payload["title"] = meta.get("title")
                    payload["url"] = meta.get("url")
                bus.publish_threadsafe(Event(
                    EventType.VIDEO_WATCH_PROGRESS, payload, "dialogue",
                ))
            except Exception:  # noqa: BLE001
                pass

        return _cb

    async def _desktop_video_context(self, text: str, on_progress=None) -> str:
        """桌面聊天里若含视频链接，取回"AI 看过的视频"上下文文本；无则空串。

        复用 channels.media_ingest（已单测）：命中视频平台 → 取原视频→抽帧→VLM 看画面；
        失败/非视频链接返回空串（按普通消息处理，不阻断主流程）。
        on_progress: 阶段进度回调，供前端显示"正在看视频"进度条。
        """
        try:
            from ..channels.media_ingest import ingest_media
            logger.debug("[桌面媒体] 开始摄入 text=%s", (text or "")[:120])
            ctxs = await ingest_media(
                self._vision_router or self._llm, None, text, [],
                ingest_images=False,
                # 【2026-10-03 开】图文链接（公众号/微博/普通网页）也要"看"：
                # _fetch_page 有 15s 超时且只取正文文本，风险可控；
                # 视频链接观看失败时也退回这里兜底，不再出现"点开只会看个空壳"。
                ingest_urls=True,
                store_to_memory=False, watch_videos=True,
                on_progress=on_progress,
            )
            if ctxs:
                out = "\n\n".join(ctxs)
                logger.info("[桌面媒体] 产出上下文 %d 段 / %d 字", len(ctxs), len(out))
                return out
            logger.debug("[桌面媒体] 无上下文（非视频链接或观看未产出）")
        except Exception:  # noqa: BLE001
            logger.exception("桌面媒体摄入失败（按普通消息处理）")
        return ""

    def _desktop_media_enabled(self) -> bool:
        """读取 config.yaml desktop.media.watch_videos（默认开）。"""
        try:
            desktop = getattr(self._cfg.ns, "desktop", None)
            if desktop is None:
                return True
            media = getattr(desktop, "media", None)
            if media is None:
                return True
            return bool(getattr(media, "watch_videos", True))
        except Exception:  # noqa: BLE001
            return True

    def _is_watch_together_intent(self, user_text: str, media_enabled: bool) -> bool:
        """判断用户消息是否为「陪我看视频」意图，避免被 agent 当普通网址丢进浏览器。

        命中条件：开启"一起看"（desktop.media.watch_videos）+ 原文含可识别的视频平台链接。
        命中后调用方应跳过聊天工具循环（open_app/browse_web 会真的打开浏览器/新标签），
        回落普通决策链路——此时进入 respond 的 user_text 已含 _on_user_message 注入的
        "一起看"视频上下文（标题/帧描述），AI 会基于画面/标题直接聊，而不是去开网页。

        纯函数式实现（不碰 LLM/IO），便于单测。
        """
        if not media_enabled:
            return False
        if not (user_text or "").strip():
            return False
        from ..channels.video_watch import is_video_url as _is_video_url  # noqa: PLC0415
        import re as _re  # noqa: PLC0415
        urls = _re.findall(r'https?://\S+', user_text)
        return any(_is_video_url(u) for u in urls)

    async def _broadcast_proactive(self, d: dict) -> None:
        """把主动搭话内容直接作为 AI 决策广播（TTS 发声 / 控制台 / GUI 聊天 / Unity）。"""
        text = (d.get("text") or "").strip()
        if not text:
            return
        # 记入最近经历：AI 自己说过的话也进入上下文，对话时保持一致、不重复
        self._record_event(f"你对用户说：{text}")
        emotion = d.get("emotion") or "gentle"
        action = d.get("action") or "talk"
        # 先进入 THINKING，让 IDLE → THINKING → SPEAKING → IDLE 状态流合法
        await self._state.transition(State.THINKING, "dialogue:proactive")
        await self._bus.publish(Event(EventType.AI_DECISION, {
            "emotion": emotion,
            "action": action,
            "text": text,
            "is_proactive": True,
            "trigger": d.get("trigger", ""),
            "activity_type": d.get("activity_type", ""),
            "user_activity": self._activity_payload(),
            "voice": self._current_voice(),
        }, "dialogue"))

    async def _publish_empty_reply(self, channel: str,
                                   reply_target: Optional[dict]) -> None:
        """「她没有可说的内容」的统一出口：发 failed 失败气泡 + 回 idle。

        【2026-10-23】抽出这个出口是因为有**两个**必须走它的地方，而它们的文案/载荷
        必须逐字一致（本仓最忌"同一件事两份实现"）：
          ① parse 之后 decision["text"] 本来就是空（模型返回空 / 被网关兜底吞掉）；
          ② **清洗之后被清空**——纯 markdown 骨架（"**"/"__"）被 _strip_markdown 剥成
             ""；或纯舞台动作描写（"（翻个白眼）"）被 _humanize_reply 清空。
        不走这个出口的两种坏结果：
          · text="" → webapp 静默丢弃 → 用户"说了话没人应"；
          · 未清洗的舞台指示当台词播出去 → 非流式路径下 TTS 念"翻个白眼"。
        """
        logger.warning("LLM 没有给出可说的内容，按失败处理（避免打字无回复/念舞台指示）")
        await self._bus.publish(Event(EventType.AI_DECISION, {
            "emotion": "neutral",
            "action": "talk",
            "text": EMPTY_REPLY_TEXT,
            "failed": True,
            "channel": channel,
            "reply_target": reply_target,
            "user_activity": self._activity_payload(),
            "voice": self._current_voice(),
        }, "dialogue"))
        await self._state.to_idle("dialogue")

    async def _guard_speakable(self, text: str, channel: str,
                               reply_target: Optional[dict]) -> bool:
        """清洗后的终稿必须"有可说的内容"，否则发失败气泡并返回 False。

        【2026-10-23】**三个能出声的入口共用这一道闸**：respond 主链、
        `_respond_with_tools`（聊天中调用工具）、`_respond_vision_chat`（聊天发图）。
        三者的终稿都过 `_strip_markdown`（± `_humanize_reply`），而这两个清洗都可能把
        内容清空——纯 markdown 骨架（"**"/"__"）或纯舞台动作（"（翻个白眼）"）。
        不拦的两种结局：
          · text="" → 前端静默丢弃 → "说了话没人应"；
          · 未清洗的残骸当台词播出去 → TTS 念"翻个白眼"。
        调用方写法：`if not await self._guard_speakable(t, ch, rt): return`。
        """
        if (text or "").strip():
            return True
        logger.warning("清洗后无可说内容（纯 markdown 骨架或纯舞台动作描写）")
        await self._publish_empty_reply(channel, reply_target)
        return False

    async def _on_unity_inbound(self, e: Event) -> None:
        d = e.data or {}
        msg_type = d.get("message_type")
        payload = d.get("payload", {}) or {}
        if msg_type == "user_input":
            await self.respond(payload.get("text", ""), source="unity")
        elif msg_type == "set_emotion":
            emotion = payload.get("emotion", "neutral")
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": emotion,
                "action": "idle",
                "text": "",
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
        elif msg_type == "set_character":
            # Unity 请求切换角色
            char_id = payload.get("char_id", "")
            if char_id:
                await self.switch_character(char_id)

    def _activity_payload(self) -> Optional[dict]:
        if not self._last_activity:
            return None
        return {
            "summary": self._last_activity.get("summary", ""),
            "app_hint": self._last_activity.get("app_hint", ""),
            "confidence": self._last_activity.get("confidence", 0.0),
        }

    def _current_voice(self) -> Optional[str]:
        voice_cfg = self._cfg.get_active_voice_config()
        if voice_cfg:
            return voice_cfg.voice
        return None

    async def respond(self, user_text: str, source: str = "",
                      channel: str = "", reply_target: Optional[dict] = None) -> None:
        """生成并发布 AI 决策。流式：边生成边切句发 PARTIAL_TEXT（TTS 逐句念 / GUI 打字机）。

        channel / reply_target（G 社交通道）：当某外部通道（QQ 等）把用户文本喂给
        同一个大脑时，用 channel 标记来源通道名、reply_target 标记"回复发往哪个 peer"。
        对应通道订阅 AI_DECISION，只取回 channel==本通道名 的决策并发回该 peer；
        桌面（ws_server）只播放 channel 为空（桌面默认）的决策，互不串台。
        """
        if not self._llm.available:
            await self._bus.publish(Event(EventType.ERROR, {
                "code": "NO_MODEL",
                "message": "Ollama 无可用模型，请先 ollama pull",
            }, "dialogue"))
            return

        # 进入思考状态（IDLE/LISTENING → THINKING）
        await self._state.transition(State.THINKING, f"dialogue:{source}")

        activity_summary = _annotate_activity_age(
            self._last_activity.get("summary", ""), self._last_activity_ts)
        # 场景意图（2026-08-22）：vision 结构化场景分类给模型分寸感。
        # 命中且新鲜（30s 内）才注入，前缀到活动摘要前；未知场景不注入。
        _hint = (self._last_activity.get("app_hint") or "").strip()
        if _hint in _SCENE_INTENT_MAP and time.time() - self._last_activity_ts <= 30:
            _scene = _SCENE_INTENT_MAP[_hint]
            activity_summary = (_scene + "；" + activity_summary) if activity_summary else _scene
        events_str = self._format_events()
        # 按需联网搜索（2026-08-22）：模型是 9B 视觉陪伴模型，知识少，用户问
        # 实时/冷门/知识库外信息时容易瞎编。先让 LLM 快速裁决"这个问题需不需要
        # 搜索"，需要则搜完把结果注入本轮生成。搜索结果空/裁决失败都不影响主流程。
        search_results: list[dict] = []
        # 【2026-10-08 修·"发完抖音要等 20 多秒才回话"】"一起看"链接**不联网搜索**：
        # 本轮已经拿到视频/网页上下文（见上方 _desktop_video_context），再让模型裁决"要不要搜"
        # 只会搜出平台首页这种噪声（实测 01:36:17：query='抖音短视频 pP1ScxJEfXI 内容'，
        # 5 条结果全是 douyin.com 首页/DOU+官网），却把回复硬生生推迟了 **17 秒**。
        _media_intent = self._is_watch_together_intent(user_text, self._desktop_media_enabled())
        if _media_intent and user_text.strip():
            logger.info("[按需搜索] 本轮是「一起看」媒体链接，跳过联网搜索（已有媒体上下文）")
        if user_text.strip() and source not in ("game_ocr", "ocr") and not _media_intent:
            try:
                need, query = await self._decide_search(user_text)
                if need and query:
                    # 【2026-09-04·UI 卡死根因修复】web_search.search 内部是同步
                    # httpx.get；DNS/网络挂起时 timeout 在 DNS 阶段常不生效，会冻结
                    # asyncio 主循环数分钟（日志全停、前端画面冻结）。丢线程池隔离。
                    search_results = await asyncio.to_thread(web_search.search, query)
                    if search_results:
                        logger.info("按需搜索: query=%r 结果=%d 条", query, len(search_results))
                        for i, r in enumerate(search_results[:5], 1):
                            logger.info("  搜索[%d] %s | %s",
                                        i, r.get("title", "")[:40], r.get("snippet", "")[:50])
            except Exception as e:  # noqa: BLE001
                logger.debug("搜索裁决/执行异常: %s", e)
        # 【聊天工具调用（2026-09-27）】开启"聊天中允许调用工具"时走工具循环分支，
        # 模型自己决定调不调工具（tool_choice=auto）；否则走原决策 JSON 对话链路。
        # 注意：_tools_in_chat_enabled 是同步方法（纯读 config），不可 await，否则
        # 会抛 "object bool can't be used in 'await' expression" 让整条回复链崩溃。
        if self._tools_in_chat_enabled():
            # 【2026-09-29 降级】工具分支返回 False（异常/返回空）→ 不 return，
            # 继续走下方普通决策链路，保证用户总能得到一句正常回复。
            try:
                _tools_done = await self._respond_with_tools(
                    user_text, activity_summary, events_str, search_results,
                    source=source, channel=channel, reply_target=reply_target)
            except Exception:  # noqa: BLE001
                logger.exception("聊天工具分支异常，降级为普通聊天")
                _tools_done = False
            if _tools_done:
                return
        # 【2026-10-11 回多长看对方】按用户这一句的长短定她该回多长：短话短回、
        #   长话才允许多接一两句。同一个口径贯穿三处（提示词/流式分句上限/终稿截断），
        #   保证"提示词要求 == 实际发出的句子数 == 记录进历史的文本"三者一致。
        _rb_sent, _rb_chars = self._reply_budget(user_text)
        messages = self._build_messages(user_text, activity_summary, events_str, search_results,
                                        reply_budget=(_rb_sent, _rb_chars))

        streamer = _SentenceStreamer()
        try:
            # 【打字无回复兜底（2026-09-03）】双工 local_omni 下聊天路由 skip_local，
            # 只走云端/兜底网关，端口慢或挂起时 await 会长时间无返回，用户"说了话
            # 没人应"。加 75s 超时，超时就走下方 failed 分支给出明确失败提示，
            # 而不是无限挂起。
            raw, streamed = await asyncio.wait_for(
                self._chat_decision(messages, streamer, channel, seg_limit=_rb_sent), timeout=75.0)
        except Exception as e:  # noqa: BLE001
            logger.exception("LLM 调用失败")
            # 流式已产出部分文字：兜底把已生成内容当决策广播（streamed），
            # 让 TTS 正常收尾、GUI 展示已生成的部分，不卡在 SPEAKING
            partial_text = parse_decision(streamer.raw).get("text", "")
            if partial_text:
                await self._bus.publish(Event(EventType.AI_DECISION, {
                    "emotion": "neutral",
                    "action": "talk",
                    "text": partial_text,
                    "streamed": True,
                    "channel": channel,
                    "reply_target": reply_target,
                    "user_activity": self._activity_payload(),
                    "voice": self._current_voice(),
                }, "dialogue"))
            else:
                # 一句话都没生成（断连/超时/额度耗尽）：给用户明确失败提示，
                # 带具体原因（额度耗尽/Key 无效/限流/超时），避免"说了话没人应"。
                # failed=True：TTS 不合成播放，前端用红字失败气泡展示。
                await self._bus.publish(Event(EventType.AI_DECISION, {
                    "emotion": "neutral",
                    "action": "talk",
                    "text": self._fail_text(e),
                    "failed": True,
                    "channel": channel,
                    "reply_target": reply_target,
                    "user_activity": self._activity_payload(),
                    "voice": self._current_voice(),
                }, "dialogue"))
            await self._bus.publish(Event(EventType.ERROR, {
                "code": "LLM_ERROR",
                "message": str(e),
            }, "dialogue"))
            await self._state.to_idle("dialogue")
            return

        # 【2026-10-23 尾段守卫】parse_decision 之下的每一步（剥 markdown / 拟人化 /
        # JSON 兜底 / 记历史 / 发决策）都在上方那个 try/except（只管 LLM 调用）的覆盖
        # 之外。这里补一层，并**复用上方 LLM 失败分支的同一条判据**：
        #   已经流式播出过内容 → 补发一条 streamed=True 的**非 failed** 决策——她话已
        #     说出口、TTS 已逐句念完、聊天气泡已封口上屏，此时不能再补一句"失败"
        #     （否则"已回答 + 红字失败气泡"同时出现，自相矛盾）；
        #   一个字都没流出来 → 才发 failed 失败气泡（带具体原因，同 _fail_text）。
        # 任务级 done_callback（_on_user_text）只是最后防线，覆盖 vision / 媒体支路。
        try:
            decision = parse_decision(raw)
            if not (decision.get("text") or "").strip():
                # 空响应（模型返回空/被网关兜底吞掉）：与异常同待遇，发失败气泡，
                # 否则 webapp.py 会静默丢弃，用户体验"说了话没人应"。
                await self._publish_empty_reply(channel, reply_target)
                return
            logger.info("决策: emotion=%s action=%s text=%r", decision["emotion"], decision["action"], decision["text"])

            # 【2026-10-04 23:11 剥 markdown 标记】用户 22:55/23:06 两次截图实锤：
            #   她把 `**娅娅**`、`**你叫我叫「鸡仔」**`、`## 记下来的三件事`、`| 特性 | 细节 |`
            #   原样吐在气泡里（前端**没有** markdown 渲染，是纯文本显示 ⇒ `**` 直接可见）。
            #   这是"AI 在念 markdown"的破绽。**源头剥掉**（只去符号、不删内容）。
            decision["text"] = self._strip_markdown(decision["text"])
            # 【2026-10-11 说话节奏】再收一道：去括号动作/场景描写 + 多段并句 + 超长截断，
            # 让聊天页像真人一句一句说（模型不一定听提示词，这层兜底不靠它自觉）。
            # 口径与本轮流式分句一致（_rb_sent/_rb_chars），确保上屏句数 == 记录句数。
            _hum = self._humanize_reply(decision["text"],
                                        max_sentences=_rb_sent, trim_min_chars=_rb_chars)
            # 【2026-10-23】清洗之后**可能被清空**，必须判空，否则两种坏结果：
            #   ① 纯 markdown 骨架（"**" / "__" / "****"）被 _strip_markdown 剥成 ""
            #      → 发一条 text="" 的决策，前端静默丢弃 → "说了话没人应"；
            #   ② 纯舞台动作描写（"（翻个白眼）" / "（ Action ）"）被 _humanize_reply
            #      清空 → 原代码 `if _hum:` 不成立会**留着未清洗的舞台指示** → 非流式
            #      路径下 TTS 把它当台词念出来（"翻个白眼"）——正是 10-11/10-14 两次要治的。
            # `_humanize_reply` 只在"输入本身就是空 / 全是舞台动作 / 全是空白"时返空
            # （实测"好呀/嗯/啊？/呜呜……"都不返空），所以这一查同时覆盖 ① ②。
            # 三种坏结局都按"空响应"走同一个出口（见 _publish_empty_reply / _guard_speakable）。
            if not await self._guard_speakable(_hum, channel, reply_target):
                return
            decision["text"] = _hum

            # 【2026-10-14 JSON 兜底（A1c 配套）】长对话里模型偶尔"忘了套 JSON 外壳"、
            #   只吐纯台词 → parse 回退成 neutral/idle，桌面表情/动作不跟随（静止不动）。
            #   锚句是软约束、偶有残留漂移，这里做**解析层硬兜底**：仅当 raw 里根本没有
            #   可解析 JSON 时，回退重试一次——让模型把"她已经说出口的这句话"补成结构化
            #   情绪/动作。**只补 emotion/action/intensity，绝不动 text**（text 已流式播出，
            #   改它会导致二次说话/重复 TTS）。任何失败都静默保留原决策，不影响正常回复。
            if not decision_had_json(raw):
                try:
                    _rep_prompt = (
                        "下面是桌面伴侣刚说出口的一句话。请把它理解成一次桌宠互动，"
                        "只输出一个 JSON 对象，字段：\n"
                        "emotion——必须严格取以下之一（不许自造词）："
                        "neutral/happy/shy/surprised/sad/angry/sleepy/curious。"
                        "注意“comfort/concerned/caring”等都不是合法 emotion，"
                        "想表达关心请用 sad 或 happy，配上 action=comfort。\n"
                        "action——必须严格取以下之一："
                        "idle/greet/listen/think/talk/laugh/wave/yawn/comfort。\n"
                        "intensity——1~5 的整数。\n"
                        "不要输出任何其他文字，不要重复原句。\n"
                        "她说的话：" + decision["text"]
                    )
                    _rep_raw = await asyncio.wait_for(
                        self._llm.chat([{"role": "user", "content": _rep_prompt}],
                                       options={"temperature": 0.0, "max_tokens": 80}),
                        timeout=12.0,
                    )
                    _rep = parse_decision(_rep_raw)
                    if decision_had_json(_rep_raw):
                        decision["emotion"] = _rep.get("emotion", "neutral")
                        decision["action"] = _rep.get("action", "talk")
                        decision["intensity"] = _rep.get("intensity", 3)
                        logger.info("JSON 兜底补全: emotion=%s action=%s",
                                    decision["emotion"], decision["action"])
                    else:
                        logger.warning("JSON 兜底重试仍未返回 JSON，保留原决策")
                except Exception:  # noqa: BLE001
                    logger.warning("JSON 兜底重试失败，保留原决策", exc_info=True)

            # 记录历史（方案 C：打字聊天与双工共用共享会话历史，双向连贯）
            # 记 decision["text"]（已清描写/截断的干净文本）而非 raw——raw 是含 thinking 的
            # 原始 JSON，且可能比实际发出的句子多（超预算被丢）；历史与"她真说出口的"对齐，
            # 否则她下一轮会"记得"屏幕/语音里根本没出现的句子。
            shared_history().add_turn("user", user_text, source="chat")
            shared_history().add_turn("assistant", decision["text"], source="chat")

            # 发决策事件（streamed=True：句子已逐句播放，TTS 不再整段重播）
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": decision["emotion"],
                "action": decision["action"],
                "intensity": decision.get("intensity", 3),
                "text": decision["text"],
                "streamed": streamed,
                "channel": channel,
                "reply_target": reply_target,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
        except Exception as e:  # noqa: BLE001
            # 尾段崩（剥 markdown / 拟人化 / 记历史 / 发决策 任一步）。判据与上方
            # LLM 失败分支同构：**她有没有已经说出口的话**。有 → 非 failed 续发；
            # 一句话都没有 → 向上抛，交给任务级 done_callback 发失败气泡。
            # 绝不在"已回答"之后补一句"失败"。
            # streamed 保持原值：流式播过就标 True（TTS 别整段重播），非流式标 False
            # （TTS 把整段念出来）——两条路她都确实把话说出口了。
            # 取文本用 streamer.raw or raw：流式路径两者同一个串；非流式路径
            # （后端不支持流式，_chat_decision 返回 llm.chat 的整段）streamer 从未被
            # 喂过、raw 才是正文，只取 streamer 会拿到空而误判成"毫无产出"。
            logger.exception("决策尾段处理失败（按已有产出降级）")
            _partial = (parse_decision(streamer.raw).get("text", "")
                        or parse_decision(raw).get("text", ""))
            if not _partial:
                # 走到这里说明"她一句话都没说出口"，而上面的空响应检查（含
                # 2026-10-23 补的"清洗后再判空"）本已挡掉这种情形 → 正常不可达。
                # **不在这里复制一份 failed 气泡**（那会是没人跑过的死代码，还会和
                # 任务级兜底形成两份实现）：直接向上抛，让 done_callback 用同一条
                # 兜底路径发失败气泡。宁可达不到，也不写没人验证的分支。
                raise
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "neutral",
                "action": "talk",
                "text": _partial,
                "streamed": streamed,
                "channel": channel,
                "reply_target": reply_target,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
            await self._state.to_idle("dialogue")
            return

        # 长期记忆：【2026-10-11】旧单句白名单提炼**已断开**（保留代码可回退），
        #   改走"整段上下文 LLM 提炼"（上下文快超限时自动触发；详见 _maybe_synthesize_memory）。
        # 后台异步执行——云端 LLM 提炼耗时数十秒，串行 await 会占住事件循环，
        # 拖慢 TTS 播放、下一条消息和感知处理（用户感知"回复变慢"）。
        # asyncio.create_task(self._maybe_extract_memory(user_text))   # ← 旧方案，已断开
        # asyncio.create_task(self._maybe_extract_episode(user_text))  # ← 旧情节提炼，已断开
        asyncio.create_task(self._maybe_synthesize_memory())  # 整段提炼（上下文快超限时）

    async def _chat_decision(self, messages: list[dict], streamer: _SentenceStreamer,
                            channel: str = "", seg_limit: int = 0) -> tuple[str, bool]:
        """流式生成决策，每切出一句就发布 PARTIAL_TEXT 事件。

        返回 (完整 raw 输出, 是否真流式)。后端不支持流式时退化为一次性 chat（streamed=False）。
        channel：外部通道名（如 "qq"），随 PARTIAL_TEXT 一并带出，供桌面 TTS/ws_server
        过滤——避免 QQ 等外部消息的流式句子被桌面扬声器念出来。
        seg_limit（2026-10-11）：本轮"最多发几句"（由 _reply_budget 按用户话长给）。
        这是气泡数与 TTS 播报句数的**真实上限**——超出的句子不再单独成句上屏，
        避免"用户只说一句、她连发五条"。提示词是主约束，这里只做硬兜底；0=不限。
        """
        if not getattr(self._llm, "supports_stream", lambda: False)():
            return await self._llm.chat(messages), False
        _sent_count = 0
        _pending = ""   # 攒句缓冲：等"真正句末"再发（见下）

        async def _emit(seg: str) -> None:
            """作为一句发出（受 seg_limit 限流）。"""
            nonlocal _sent_count
            if not seg:
                return
            if seg_limit and _sent_count >= seg_limit:
                # 超出本轮口径：不再单独成句（避免连发刷屏）。提示词已按话长给口径，
                # 模型偶尔写多时靠这里收口；被丢的句子不进历史（见 respond 只记
                # decision["text"]），保证"她说的"与"她记得的"一致。
                logger.info("聊天回复超出本轮口径(%d 句)，丢弃尾句: %r", seg_limit, seg[:50])
                return
            _sent_count += 1
            await self._bus.publish(Event(EventType.PARTIAL_TEXT,
                                         {"text": seg, "channel": channel,
                                          "granularity": "sentence",
                                          "seg_limit": seg_limit}, "dialogue"))

        async for chunk in self._llm.chat_stream(messages):
            for sent in streamer.feed(chunk):
                # 【2026-10-11 说话节奏】流式分句就是 TTS 逐句念的内容（tts.py 订阅
                #   PARTIAL_TEXT）→ 同样去掉括号里的动作/场景描写，别把它念出声。
                #   清空了就不发，避免推一条空气泡/空播报。
                sent = self._strip_stage_directions(sent)
                if not sent:
                    continue
                _pending += sent
                # 【2026-10-12 学 AstrBot 分段·治"乱断句"】只在"真正句末标点 **且**
                #   攒够最小段长"才封口发出；短句（如"响。"）不再单独成段，而是
                #   自动并入下一句。AstrBot 内置分段是对**完整回复**按
                #   `.*?[｡?!~…]+` 切（自带"整段重排"语义），我们是流式，等价做法
                #   就是加一个最小段长闸：句子末标点 + >=_CHAT_MIN_SEG_CHARS 才发，
                #   36 字硬切兜底不变（防无标点狂流）。
                if _pending[-1] in _REPLY_SENT_END:
                    if len(_pending) >= _CHAT_MIN_SEG_CHARS:
                        await _emit(_pending)
                        _pending = ""
                elif len(_pending) >= _CHAT_MAX_SEG_CHARS:
                    await _emit(_pending)
                    _pending = ""
        # 末句常以"～/呢"收尾（非句末标点）→ flush 兜底作为最后一句发出
        _pending += self._strip_stage_directions(streamer.flush())
        if _pending:
            await _emit(_pending)
        return streamer.raw, True

    # ==================== 聊天中允许调用工具（2026-09-27） ====================
    def _tools_in_chat_enabled(self) -> bool:
        """读取 config.yaml agent.allow_tools_in_chat（运行时热读，无需重启）。"""
        try:
            agent = getattr(self._cfg.ns, "agent", None)
            if agent is None:
                return False
            return bool(getattr(agent, "allow_tools_in_chat", False))
        except Exception:  # noqa: BLE001
            return False

    async def _respond_with_tools(self, user_text: str, activity_summary: str,
                                 events_str: str, search_results: list[dict],
                                 source: str = "",
                                 channel: str = "",
                                 reply_target: Optional[dict] = None) -> bool:
        """聊天中允许调用工具：模型自己决定调不调工具（tool_choice=auto）。

        闲聊就只返回文本；操作电脑类指令才调 open_app / media_control 等。
        工具集默认是安全子集（agent_tools.chat_tool_specs）；当 config agent.require_confirm
        开启时，额外暴露破坏性工具（click_at / type_text / key_press / run_command 等），
        且每次执行前弹确认框（见 agent_confirm_broker）——聊天场景仍走确认闸，不静默操作。

        【2026-10-23】channel / reply_target 必须从 respond 透传进来，并带在**每一个**
        AI_DECISION / PARTIAL_TEXT 上。此前这里一个都不带：社交通道（base._feed_brain →
        respond(channel=self.name)）的消息一旦进工具链，发布的决策 channel 为空 →
        ① 通道侧 `_on_decision` 判 `d.get("channel") != self.name` 直接忽略
           ⇒ **发消息的人完全收不到回复**；
        ② 桌面侧（ws_server）把 channel 为空当桌面决策 ⇒ **桌宠替那条 QQ 消息开口念出来**。
        一头没接到、另一头接错了，两头都是静默的（没有报错，只是"她不回"+"桌宠自言自语"）。
        默认 `allow_tools_in_chat: false`，所以这是潜伏缺陷——一开开关就发作。

        返回 True=本分支已产出回复（含失败气泡）；False=未产出任何回复，
        调用方应降级走普通聊天决策链路（2026-09-29 修"聊天不理人"：
        此前任何异常都发死胡同道歉气泡，用户说什么都只回那句"出错了"）。
        """
        from ..llm.agent_tools import chat_tool_specs, dispatch_tool, _CHAT_DESTRUCTIVE_TOOL_NAMES
        from ..llm.agent_brain import build_agent_ctx
        from ..llm import agent_confirm_broker
        # 【2026-10-02 修·抖音/视频链接被开浏览器】用户发视频平台链接且开启"一起看"时，
        # 意图是陪你看视频，不是用 open_app/browse_web 把链接丢进浏览器。检测到视频链接
        # 则跳过工具循环（避免 agent 调浏览器工具），回落普通决策链路——此时进入 respond
        # 的 user_text 已含 _on_user_message 注入的视频上下文，AI 会基于画面/标题直接聊。
        if self._is_watch_together_intent(user_text, self._desktop_media_enabled()):
            logger.info("[聊天工具] 命中视频链接，路由为一起看（跳过浏览器工具，避免丢进浏览器）")
            return False
        ctx = build_agent_ctx(self._cfg, "")
        on_tool = lambda name, args: dispatch_tool(name, args, ctx)  # noqa: E731
        # 执行前确认：config agent.require_confirm（默认开）。开启时才暴露破坏性工具（点击/输入/按键等），
        # 且每次执行前弹确认框（fail-closed：超时/无回执=拒绝）。
        _agent_cfg = getattr(self._cfg.ns, "agent", None)
        require_confirm = bool(getattr(_agent_cfg, "require_confirm", False)) if _agent_cfg else False
        tools = chat_tool_specs(include_destructive=require_confirm)
        messages = self._build_tool_messages(user_text, activity_summary, events_str, search_results)
        # 最大操作步骤：读 config agent.max_steps（设置页滑块可调，默认 15），
        # 让滑块对"聊天中允许调用工具"这条链路也生效（之前这里写死 6，滑块改了也没用）。
        # 与设置页 UI 上限（max=40）保持一致。
        _ms = int(getattr(_agent_cfg, "max_steps", 6) or 6) if _agent_cfg else 6
        max_steps = max(1, min(_ms, 40))
        # 【2026-10-08 流式·仅桌面打字聊天】流式打字机/逐字上屏只服务电脑版聊天框
        # （source=="webui"）。语音/双工(source=="voice")、控制台/Unity 等其它来源保持
        # 原样（工具分支非流式、整段返回），不被纯聊天的改动波及。对照 git HEAD 原始实现：
        # 原始 _respond_with_tools 无 on_token、决策不带 streamed 键。
        _stream_for = (source == "webui")
        # 【2026-10-12 修·"分段/减话术全没生效"真根因】闲聊消息实际默认走**工具链路**
        # （_tools_in_chat_enabled 开启时 respond() 先进这里），而此前所有治理
        # （说话节奏提示词 / _humanize_reply 截断 / 分段输出）都只落在普通决策链路
        # `_chat_decision` 上 ⇒ 用户看到的一直是"工具链路原始 token 流：一坨、
        # 带(凑近)动作描写、不限长"。这里把同一套口径搬进工具链路：
        #   ① token 流按句累积（真句末标点或 36 字才发一段，与 _chat_decision 合句同参）；
        #   ② 每段先 _strip_markdown + _strip_stage_directions 再上屏；
        #   ③ 事件带 granularity="sentence" + seg_limit（webapp 据此连发分段气泡）；
        #   ④ 终稿：没真调工具的纯聊天回复按 _reply_budget 截长（调了工具的仍 cap=False，
        #      避免切掉操作步骤说明）。
        _rb_sent, _rb_chars = self._reply_budget(user_text)
        _tools_called = False

        def _on_tool_wrapped(name, args):
            nonlocal _tools_called
            _tools_called = True
            return on_tool(name, args)

        _pending = ""

        async def _emit_seg(seg: str) -> None:
            nonlocal _pending
            # 【10-12】用"删空即丢"变体：整段只是 (动作) 时直接丢弃，
            # 不能走 _strip_stage_directions 的"退回原文"保底。
            seg = self._drop_stage_dirs(self._strip_markdown(seg))
            if not seg:
                return
            try:
                await self._bus.publish(Event(EventType.PARTIAL_TEXT, {
                    "text": seg,
                    "channel": channel,
                    "granularity": "sentence",
                    "seg_limit": _rb_sent,
                }, "dialogue"))
            except Exception:  # noqa: BLE001
                logger.debug("聊天工具 PARTIAL_TEXT 广播失败", exc_info=True)

        async def _on_token(tok: str) -> None:
            # 【2026-10-08 流式】工具链路逐 token 吐字 → PARTIAL_TEXT → 桌面打字机 / TTS 逐句。
            # 不传 channel（桌面默认），与非流式分支过滤逻辑一致，确保只进桌面对话。
            # 【2026-10-12】不再逐 token 裸发：先攒句（见上注释），清洗后按句上屏。
            nonlocal _pending
            if not tok or not _stream_for:
                return
            _pending += tok
            # 与 _chat_decision 同构：攒句，句末标点 + 最小段长才封口；
            # 尾部无句末标点攒到 36 字才硬切（短句自动并入下一句）。
            while True:
                if _pending[-1:] and _pending[-1] in _REPLY_SENT_END:
                    if len(_pending) >= _CHAT_MIN_SEG_CHARS:
                        await _emit_seg(_pending)
                        _pending = ""
                        continue
                    break
                if len(_pending) >= _CHAT_MAX_SEG_CHARS:
                    _seg, _pending = _pending[:_CHAT_MAX_SEG_CHARS], _pending[_CHAT_MAX_SEG_CHARS:]
                    await _emit_seg(_seg)
                    continue
                break
        try:
            result = await asyncio.wait_for(
                self._llm.chat_with_tools(
                    messages, None, tools, _on_tool_wrapped, max_steps,
                    on_confirm=agent_confirm_broker.request_confirm if require_confirm else None,
                    require_confirm=require_confirm,
                    destructive_tools=set(_CHAT_DESTRUCTIVE_TOOL_NAMES),
                    on_status=self._make_agent_status_cb(),
                    on_token=_on_token if _stream_for else None,
                ), timeout=_CHAT_TOOLS_TIMEOUT_S)
        except asyncio.TimeoutError:
            # 工具循环整体超时：不再重试普通聊天（否则用户最多等 2 倍时间），
            # 直接给明确失败提示并收尾。
            # 【2026-10-04】75s 对「打开 App + 搜歌 + 点播 + 复核」这条多步链路太紧：
            # 光 LLM 多轮往返就可能吃掉大半，实际表现为「只打开没播放」然后甩一句超时。
            # 放宽到 120s；工具本身也已改为异步等待（不再同步 sleep 冻住事件循环）。
            logger.warning("聊天工具调用超时（%ss）", int(_CHAT_TOOLS_TIMEOUT_S))
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "neutral", "action": "talk",
                "text": f"操作超时：{int(_CHAT_TOOLS_TIMEOUT_S)} 秒内这一串操作还没跑完"
                        f"（多为网络慢或服务端忙；已经做过的步骤在执行卡片里能看到），请稍后再试",
                "failed": True,
                "streamed": _stream_for,
                "channel": channel,
                "reply_target": reply_target,
                "user_activity": self._activity_payload(),
                "voice": self._current_voice(),
            }, "dialogue"))
            await self._state.to_idle("dialogue")
            return True
        except Exception as e:  # noqa: BLE001
            # 【2026-10-02】确定性失败（额度耗尽/Key 无效）降级重试只会再撞一次墙：
            # 直接给带原因的失败气泡收尾。瞬时失败（限流/超时）仍降级走普通聊天。
            from ..llm.error_classify import classify_error, KIND_QUOTA, KIND_AUTH
            if classify_error(str(e)) in (KIND_QUOTA, KIND_AUTH):
                logger.warning("聊天工具调用确定性失败（%s），不降级重试", str(e)[:120])
                await self._bus.publish(Event(EventType.AI_DECISION, {
                    "emotion": "neutral", "action": "talk",
                    "text": self._fail_text(e),
                    "failed": True,
                    "streamed": _stream_for,
                    "channel": channel,
                    "reply_target": reply_target,
                    "user_activity": self._activity_payload(),
                    "voice": self._current_voice(),
                }, "dialogue"))
                await self._state.to_idle("dialogue")
                return True
            # 【2026-09-29 降级】工具分支任何异常（含 TypeError 签名不匹配、模型不支持
            # tools、网络错误）都不再发死胡同道歉气泡，而是返回 False 让调用方落回
            # 普通聊天链路——用户至少能得到一句正常回复。
            logger.exception("聊天工具调用失败，降级为普通聊天")
            await self._bus.publish(Event(EventType.ERROR, {
                "code": "CHAT_TOOLS_ERROR",
                "message": str(e),
            }, "dialogue"))
            return False
        result = (result or "").strip()
        if not result:
            # 工具端点返回空：同样降级走普通聊天（比"模型返回空"失败气泡更有用）。
            logger.warning("聊天工具调用返回空，降级为普通聊天")
            return False
        # 【2026-10-12】流式攒句的尾巴：最后一段往往不带句末标点（"～"结尾/被 max
        # 截断的 36 字段），必须 flush 出去，否则 UI 最后一句丢失（decision 收尾在
        # 分段模式下不覆盖气泡文本）。
        if _stream_for and _pending:
            await _emit_seg(_pending)
            _pending = ""
        # 【2026-10-12】终稿口径与普通链路对齐：
        #   · 没真调工具的纯聊天回复 → 按 _reply_budget 截长（长度跟随用户）；
        #   · 真调了工具 → 仍 cap=False（操作步骤说明不能被切掉）。
        #   历史与 AI_DECISION 都记清洗后的文本，和 UI 气泡一致。
        if _tools_called:
            _text_out = self._humanize_reply(self._strip_markdown(result), cap=False)
        else:
            _text_out = self._humanize_reply(
                self._strip_markdown(result),
                max_sentences=_rb_sent, trim_min_chars=_rb_chars)
        # 【2026-10-23】工具链这条终稿也要过同一道闸。此前它 strip+humanize 之后
        # **不判空**：模型/工具只返回"（翻个白眼）"这类纯舞台动作时 _text_out 为 ""，
        # 发出去被前端静默丢弃 → "问了话、工具也跑了，她却不说话"。
        # channel/reply_target 从 respond 透传（2026-10-23 修：此前整个丢掉，
        # 导致通道侧收不到、桌面侧替它播）。工具链本就未自己解析外部通道语义，
        # 现在至少把"这条回复该给谁"原样带出去。
        if not await self._guard_speakable(_text_out, channel, reply_target):
            return True
        # 记录文本历史（不掺工具调用痕迹，保持多轮自然）
        shared_history().add_turn("user", user_text, source="chat")
        shared_history().add_turn("assistant", _text_out, source="chat")
        # 发决策事件（text=自然语言结果，TTS 正常念、GUI 展示）
        await self._bus.publish(Event(EventType.AI_DECISION, {
            "emotion": "neutral", "action": "talk",
            # 【23:11】工具回复同样剥 markdown；【10-11】再去动作/场景描写；
            # 【10-12】纯聊天回复按本轮口径截长（见上），真调工具才不截。
            "text": _text_out,
            "streamed": _stream_for,
            # 【2026-10-23】channel/reply_target 必须带上（见函数 docstring）：
            # 少了它们，通道侧判 channel 不匹配直接忽略、桌面侧当成桌面决策播放。
            "channel": channel,
            "reply_target": reply_target,
            "user_activity": self._activity_payload(),
            "voice": self._current_voice(),
        }, "dialogue"))
        return True

    def _make_agent_status_cb(self):
        """Agent 工具执行进度回调（2026-10-02）：广播 AGENT_STATUS 事件。

        webapp 订阅后在聊天页显示 Agent 执行卡片（计划 / 步骤时间线 / 完成态）。
        状态只在 UI 展示，不进 TTS、不进对话历史。
        兼容纯文本与结构化 dict（{phase,text,step,total,tool,args,state,result,done}）。
        """
        async def _on_status(payload) -> None:
            try:
                if isinstance(payload, dict):
                    data = dict(payload)
                    if "text" not in data:
                        data["text"] = (data.get("raw") or
                                       (f"步骤 {data.get('step')}/{data.get('total')} · "
                                        f"{data.get('tool', '')}") or "正在操作")
                else:
                    data = {"text": str(payload or "")}
                await self._bus.publish(Event(EventType.AGENT_STATUS, data, "dialogue"))
            except Exception:  # noqa: BLE001
                logger.debug("AGENT_STATUS 广播失败", exc_info=True)
        return _on_status

    @staticmethod
    def _fail_text(err: BaseException) -> str:
        """把 LLM 异常翻译成用户可读的失败气泡文案（带具体原因与建议）。

        治"消息发送失败（回复超时），请稍后再试"这种笼统提示——额度耗尽、
        Key 无效、限流各有明确说法，用户不用猜（2026-10-02）。
        """
        try:
            from ..llm.error_classify import human_reason
            reason, hint = human_reason(str(err))
            return f"消息发送失败：{reason}。{hint}"
        except Exception:  # noqa: BLE001
            return f"消息发送失败：{str(err)[:80] or '未知错误'}，请稍后再试"

    def _build_tool_messages(self, user_text: str, activity_summary: str,
                            events_str: str, search_results: Optional[list[dict]]) -> list[dict]:
        """为"聊天中允许工具调用"构造自然语言消息（不走决策 JSON 格式）。"""
        name = "达妮娅"
        try:
            name = self._persona.name
        except Exception:  # noqa: BLE001
            pass
        sys_text = (
            f"你是{name}，一个陪伴用户的 AI 少女，性格鲜活、口语化、有温度、会撒娇。"
            "你可以通过工具直接操作这台电脑：\n"
            "- 打开本机应用或网页：open_app（参数 target 是应用显示名/网址/exe，如 微信/哔哩哔哩/抖音/汽水音乐/网易云音乐/QQ音乐/剪映/记事本/设置）\n"
            "- 媒体与音量：media_control / set_volume / get_volume\n"
            "- 【UI 自动化·优先】ui_inspect（枚举窗口内按钮/输入框等控件树）、ui_click（按控件名称精确点击）、ui_type（聚焦输入框输入文字）——基于 Windows 原生控件识别，比坐标点击(click_at)可靠、且不需要 VLM\n"
            "- 放歌一站式：play_song(app=用户指定的音乐软件, song=歌名)——自动打开/聚焦 App、搜歌、点播放，并复核是否真的在播\n"
            "- 看屏幕：see_screen / screenshot；窗口：list_windows / focus_window；联网搜索：search_web；系统信息：system_info；等待：wait\n"
            "规则：\n"
            "1. 当用户让你操作电脑（例如『打开汽水音乐』『在汽水音乐放首晴天』『帮我把音量调小』『搜一下XX资料』"
            "『看看现在屏幕上是什么』『点一下那个按钮』）时，就调用对应工具去完成，并在工具执行后用一句自然的中文"
            "告诉用户你做了什么、结果如何。\n"
            "   点击/输入【优先用 UI 自动化】：先用 ui_inspect 看目标窗口有哪些控件，再用 ui_click/ui_type 按控件名称精确操作；"
            "只有当 ui_click/ui_type 报告找不到控件（多见于未开启无障碍的 Electron 应用）时，才退回 see_screen 截图 + click_at/type_text 坐标方式。不要一上来就用 click_at 盲点坐标。\n"
            "   放歌：首选 play_song(app=用户【指定】的音乐软件, song=歌名) 一站式完成（用户说哪个 App 用哪个，如汽水音乐/网易云音乐/QQ音乐/Spotify）；"
            "用户没指定时优先本机已安装的『汽水音乐』，不要默认网易云。\n"
            "   放歌成功与否以 play_song 的复核结论为准：只有『已确认正在播放』才算成功；"
            "返回『未确认播放』或『未对 UI 开放控件』时，必须改走视觉路径——see_screen 看屏 → click_at 点搜索框 → "
            "type_text 输入歌名 → 回车 → 再看屏点第一条结果的播放按钮，最后再 see_screen 复核一次。\n"
            "   ⚠️ 只把 App 打开不算放歌成功：严禁凭『App 打开了』或『工具没报错』就说『已经帮你播放了』；"
            "没确认真的在播，就照实告诉用户卡在哪一步、需要他手动点一下什么。\n"
            "   ⚠️⚠️【2026-10-04 20:12 治幻觉·放歌】**严禁编造歌名、歌手或『正在播放』的结论**。"
            "20:03 实锤：play_song 只做到『① 应用已在运行』且 click_at 被安全确认超时拒绝，"
            "实际什么都没播；但你仍回答『**确实有歌在放**——底部进度条走到 04:42/04:53，"
            "正在播的是《Oukizla Manda Slap》』——**整段都是编的**（她根本没读到歌名，"
            "却给出了精确到秒的进度）。"
            "正确做法：① 工具回执里没有明确歌名/播放状态时，**只说没成功**；"
            "② 绝不给出你没从工具回执里读到的歌名、歌手、进度数字；"
            "③ 需要歌名时必须先 see_screen 看到播放条文字，读不到就说不确定。\n"
            "2. 如果只是闲聊、问候、情感交流，就正常用你的口吻聊天，不要强行调用工具。\n"
            "3. 不要编造工具没做到的事；工具做不到就直说限制。\n"
            "4. ⚠️【本轮新增·重要】**用户发图时，绝对不许用 see_screen 代替「看这张图」**。"
            "see_screen 截的是**当前电脑屏幕**，和用户【发给你的那张图片】是两件不同的事。"
            "用户发图让你认/描述时，要用看图能力直接读那张图片；看不到就老实说"
            "「我这边看不了这张图」并让用户重发或描述，"
            "**严禁**因为屏幕窗口里恰好也有同一张图就当成你看到了那张图。\n"
            "5. ⚠️【2026-10-05 修'人机味'·重要】recall / remember / system_info 这类调用"
            "是你的**内心动作**，不是要说给用户听的事：调完后要像**本来就记得、张口就来**"
            "一样，直接说出想起来的内容本身（例：他说'还记得昨天聊什么吗'，直接说"
            "'记得呀，你昨天在挑微单…'）。\n"
            "   **严禁**在回复里出现「记忆库 / 翻了下记忆 / 召回 / 检索 / 上下文 / "
            "我这边存的是… / 我的记忆里」等任何描述内部机制的词；也**不要解释**你的记忆"
            "是怎么工作的、存的是什么格式（什么'关键结论不是完整记录'这种说明一律不说）——"
            "真人不会说'我翻了翻大脑'，只会直接把想起来的事讲出来。\n"
            "6. 【称呼·两层各管各（2026-10-05）】记忆里【称呼·AI名字】是**用户给你起的"
            "名字**——那是你自己，自我介绍、被叫时都用它；【称呼·用户】是**你叫他什么**"
            "——那是对面这个人的称呼。两者绝不能混：召回后先想清楚这条说的是你还是他，"
            "**绝不能把'用户叫我X'说成'我叫用户X'**；同一句话里也只能出现一个称呼。"
        )
        ctx_lines = []
        if activity_summary:
            ctx_lines.append(f"【用户当前屏幕/活动】{activity_summary}")
        if events_str:
            ctx_lines.append(f"【最近发生】{events_str}")
        if search_results:
            sr = "\n".join(f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in search_results[:5])
            ctx_lines.append(f"【联网搜索结果】\n{sr}")
        user_content = user_text
        if ctx_lines:
            user_content = (user_text
                           + "\n\n（以下仅供你判断是否需要调用工具，不要当作用户原话复述）\n"
                           + "\n".join(ctx_lines))
        messages = [{"role": "system", "content": sys_text}]
        # 与 _build_messages 保持一致：注入共享会话历史，否则连续「操作电脑」指令会丢失上下文
        # （例：用户先说"汽水"，第二次说"播放"时模型不知道此前偏好，跑去网易云）。
        messages.extend(shared_history().get_recent(MAX_HISTORY * 2))
        messages.append({"role": "user", "content": user_content})
        return messages

    def _build_messages(self, user_text: str, activity_summary: str, events_str: str = "",
                        search_results: Optional[list[dict]] = None,
                        reply_budget: Optional[tuple[int, int]] = None) -> list[dict]:
        # P3 态度注入：把关系状态（心情/原型/阶段）翻成自然语言块喂回她的语气。
        # 每轮重建系统提示时取一次；失败静默跳过，绝不阻断对话。
        rel_block = ""
        try:
            from .relationship import get_relationship_manager
            cid = str(getattr(self._cfg, "active_character_id", "") or "")
            if cid:
                rel_block = get_relationship_manager().assemble_relation_block(cid, tier="cloud")
        except Exception:  # noqa: BLE001
            logger.debug("关系状态块注入失败（跳过，不影响对话）", exc_info=True)
        # 「回多长看对方」：本轮口径由 respond 按用户话长算好传进来；缺省走模板默认。
        _ms = _mc = None
        if reply_budget:
            _ms, _mc = reply_budget
        messages: list[dict] = [{"role": "system", "content": build_system(
            self._persona, relation=rel_block,
            reply_max_sent=_ms, reply_max_chars=_mc)}]
        # 方案 C：用共享会话历史作多轮上下文（含双工说过的话），打字聊天能接上双工
        messages.extend(shared_history().get_recent(MAX_HISTORY * 2))
        # 视觉摘要可能看错游戏名（如把三角洲行动看成暗区突围）——用 OCR 识别的
        # 真实游戏名校正（看字不猜），避免对话里出现"你在玩Apex"这类串游戏话术
        if activity_summary and self._game_knowledge is not None:
            activity_summary = self._game_knowledge.correct_summary(activity_summary)
        # 游戏信息 = 背景常识 + 当前画面 OCR 文字（让 AI 知道用户当前模式/地图，看字不猜）
        game_knowledge = ""
        # =====================================================================
        # 【2026-10-04 22:45 治"纯聊天也看屏幕"】用户 22:42 实锤：他问"你觉得什么相机好"
        #   （**纯聊天、无关游戏**），她却答"**我刚刚看了下屏幕**，你在对比尼康Z1和索尼a7R5对吧？"
        #
        # 根因：本函数**无条件**把 `【当前画面文字】OCR…` + `activity_summary`（活动摘要）
        #   塞进系统提示词（甚至 `_screen_ctx` 还进了搜索裁决）⇒ 模型看到"当前画面…"
        #   就自然说"我看了下屏幕"。**而 22:37 我把上下文从 20 条扩到 80 条后
        #   活动摘要会随历史反复出现，更容易被它当成"我刚看过"的依据**（加剧了症状）。
        #
        # 解法：**按意图闸门**——只有当用户这句话确实与"他此刻在屏幕上做什么"有关时
        #   才注入画面信息；纯聊天（聊相机/闲聊/问答）一律不注入。
        #   这样既保住"陪看/游戏联动"能力，又不会在普通聊天里莫名其妙"看屏幕"。
        # =====================================================================
        _screen_related = self._query_is_screen_related(user_text)
        if not _screen_related and activity_summary:
            logger.debug("纯聊天意图：不注入活动摘要/画面文字（避免她莫名说\"我看了下屏幕\"）")
            activity_summary = ""
        # 【2026-10-23 修·构建函数抹掉共享引用】这里原来写 `self._game_knowledge = None`
        # 来"本轮不注入画面"——而 `_game_knowledge` 是 main.py 两处启动路径传进来、
        # 且被 VisionAnalyzer（bind_game_knowledge）持续更新 OCR/画面文字/锚点的**同一个
        # 活对象**。于是**第一句纯聊天就把它永久置 None**，之后连游戏相关问题也再也拿不到
        # 背景常识/画面文字/锚点状态/游戏名校正（全仓没有任何地方能恢复它：没有 setter，
        # set_runtime 只注入 _vision_router）⇒ 症状是"她一开始看得懂我在玩什么，聊了几句
        # 别的之后再也不会了，重启才好"——静默、永久、进程级。
        # 改成局部变量：本轮照样压掉画面信息，但不碰实例状态。
        _gk = self._game_knowledge
        if not _screen_related and _gk is not None:
            # 保留 game_knowledge 的"背景常识"(knowledge_text)，只压掉"当前画面/当前页面"
            kt_only = (getattr(_gk, "knowledge_text", "") or "").strip()
            _gk = None
            game_knowledge = kt_only  # 后面 if _gk is None 分支不会再加画面
        if _gk is not None:
            kt = _gk.knowledge_text
            st = _gk.screen_text
            age = _gk.screen_text_age()
            if kt:
                game_knowledge += kt
            if st:
                if game_knowledge:
                    game_knowledge += "\n"
                # 画面文字带时效：新鲜才当"当前"，过期明确标注，避免 AI 拿旧快照瞎猜
                if age is not None and age <= SCREEN_TEXT_FRESH_S:
                    game_knowledge += f"【当前画面文字】{st}（OCR 识别，可能不完整）"
                else:
                    game_knowledge += (
                        f"【画面文字（约{int(age) if age else 0}秒前识别，可能已过时，仅作背景参考）】{st}"
                    )
            # 文字锚点状态（区域文字锚点训练器）：OCR 区域文字命中用户训练的
            # 关键词规则 → 确定性页面状态（如"正在撤离倒计时"）。这是画面里真实
            # 渲染的文字得出的强判定，比视觉模型猜当前页面可靠——明确告诉 AI
            # 当前页面是什么，视觉摘要的页面猜测作废（看字不猜，治"乱猜页面"）。
            try:
                anchor = _gk.anchor_state()
                if anchor:
                    game_knowledge += (
                        f"\n【当前页面（OCR文字锚点确认）】{anchor}"
                        "（OCR 识别到的真实页面状态，优先于视觉猜测）"
                    )
            except Exception:  # noqa: BLE001 老版本知识库无锚点接口时忽略
                pass
        # 软件知识库：用户正在用的软件说明（命中才注入，离开即卸——游戏前台时
        # AppKnowledge 已被 proactive 卸载，这里拿到的是空串，不会串游戏话题）
        if self._app_knowledge is not None:
            _app_kt = self._app_knowledge.knowledge_text
            if _app_kt:
                game_knowledge = (game_knowledge + "\n" + _app_kt) if game_knowledge else _app_kt
        # 本局对局小结（对局进行中/已结算时）：用户主动问"刚才那局咋样"时，
        # AI 能接上本局击杀/阵亡/撤离/时长等数据，综合起来夸或安慰，而不是瞎猜
        session_line = ""
        if self._session is not None:
            try:
                if (getattr(self._session, "playing", False)
                        or getattr(self._session, "settled", False)):
                    line = self._session.summary_line()
                    if line:
                        session_line = f"\n【本局对局小结】{line}"
            except Exception:  # noqa: BLE001
                pass
        # 长期记忆（用户纠正/偏好），独立存储、每次注入——窗口滑动也不会丢。
        # inject_context=false 时只存不用：记忆照常记录，但不进对话上下文。
        memory_text = ""
        if self._memory_store is not None and _memory_inject_enabled(self._cfg):
            memory_text = self._assemble_chat_memory()
            # 【2026-10-11 Plan C】用户回指过去 → 追加"历史原话"引用块
            _ctx = self._assemble_context_recall(user_text)
            if _ctx:
                memory_text = (memory_text + "\n\n" + _ctx) if memory_text else _ctx
        messages.append({"role": "user", "content": build_user_turn(
            user_text, activity_summary, events_str,
            game_knowledge + session_line, memory_text, search_results)})
        return messages

    # 搜索裁决廉价预筛：明显闲聊/问候/自身或用户状态，且不含搜索触发词 → 跳过 LLM 裁决，省一轮往返。
    # （用户反馈"打字聊天到回复十分慢"：每次消息都先跑一次完整 LLM 裁决"要不要搜"，白耗一轮往返。）
    _SEARCH_SKIP_RE = re.compile(
        r"(你好|您好|在吗|在不在|早|晚安|午安|几点|现在[几什]|什么[时时候]|你是谁|你叫|你多|辛苦|谢谢|感谢|"
        r"哈哈|嘿|嗨|喂|干嘛|干什|吃饭|睡|休息|无聊|想你|喜欢|爱|生气|开心|难过|烦|累|今天|心情|状态|咋啦|怎么了|没事|在忙|忙吗|"
        r"讲个|讲一|唱首|唱一|背首|背一|说个|来个|翻译|算一|算下|猜|推荐|陪我|陪你|逗|夸|骂|安慰|抱|摸|亲|晚点|等会|一会)")
    # 动作/工具意图：明显是要本地操作或调工具，不需要联网搜索（如"打开汽水音乐"
    # "设置亮度""帮我发消息""关掉空调"），直接跳过搜索裁决，省下那一轮 LLM
    # 往返（~2s）——这正是"打字聊天到回复十分慢"的主因（裁决 LLM 串行在真正
    # 生成之前）。
    # 注意：搜/查/找类词不放这里，它们是搜索意图，要保留裁决去联网搜。
    _ACTION_INTENT_RE = re.compile(
        r"(打开|开启|启动|运行|关掉|关闭|退[出出]|停止|暂停|继续|播放|切[到换]|切换|设置|调成|改成|调节|"
        r"发送|发给|发消息|发一下|提醒我|记一下|记住|新建|删除|创建|复制|粘贴|截图|截屏|录屏|输入|点开|点击|"
        r"帮我|帮个忙|调一下|调高|调低|开一下|关一下|最小化|最大化|隐藏|显示)")
    _SEARCH_TRIGGER_RE = re.compile(
        r"(天气|新闻|最新|攻略|教程|怎么玩|怎么用|用法|价格|多少钱|股票|汇率|比赛|比分|上映|发布|版本|更新|"
        r"排名|冠军|谁赢|查一下|搜一下|百科|资料|来源|怎么写|怎么弄|为什么|原理|区别|对比)")

    async def _decide_search(self, user_text: str) -> tuple[bool, str]:
        """让 LLM 快速裁决"这个问题是否需要联网搜索"。

        只有用户明确问实时新闻/冷门知识/软件游戏用法/最新信息等模型不知道的
        内容才 need_search=true；日常闲聊、画面陪伴类一律 false，避免每次对话
        都触发搜索拖慢回复、浪费网关配额。
        """
        ut = (user_text or "").strip()
        # 廉价预筛：短消息且命中"闲聊/问候/自身状态"且不含搜索触发词 → 直接判定无需搜索，
        # 跳过这次 LLM 裁决调用，省下一轮往返延迟（长消息或含搜索触发词仍走 LLM 裁决）。
        # 【2026-10-08 修 NameError（哑火 bug）】两个正则是**类属性**（定义在本方法上方
        # 的类体里），方法内必须用 `self.` 取；原先写裸名 ⇒ 每次走到这一行都
        # `NameError: name '_SEARCH_SKIP_RE' is not defined`，而调用点
        # （respond 里 line ~999 的 try/except）把它降级成 logger.debug 吞掉 ⇒
        # 这个"省一轮往返"的预筛**从来没生效过**（≤40 字的短消息一律直接放弃搜索裁决）。
        if len(ut) <= 40 and (self._SEARCH_SKIP_RE.search(ut) or self._ACTION_INTENT_RE.search(ut)) \
                and not self._SEARCH_TRIGGER_RE.search(ut):
            return False, ""
        try:
            # 画面信息（2026-08-22 用户要求）：让裁决 LLM 结合"用户当前屏幕在干嘛"
            # 判断要不要搜——画面上的事不需要搜（如"这个怎么玩"在看教程），
            # 冷门/实时/知识库外信息才需要，裁决更准。
            _sum = (self._last_activity.get("summary") or "").strip()[:100]
            _hint2 = (self._last_activity.get("app_hint") or "").strip()
            _scene = _SCENE_INTENT_MAP.get(_hint2, "") if _hint2 else ""
            _screen_ctx = ""
            if _sum and time.time() - self._last_activity_ts <= 30:
                _screen_ctx = f"当前屏幕画面：{_sum}"
                if _scene:
                    _screen_ctx += f"（{_scene}）"
            prompt = (
                "判断下面的用户问题是否需要联网搜索才能回答好"
                "（涉及实时新闻、冷门知识、具体软件/游戏用法、最新信息等"
                "模型可能不知道的内容才需要；日常闲聊、问候、画面上的事"
                "——尤其当前屏幕里能看到的内容——不需要）。\n"
                + (f"{_screen_ctx}\n" if _screen_ctx else "")
                + f"用户问题：{user_text.strip()[:200]}\n"
                '只输出 JSON，不要其他文字：\n'
                '{"need_search": true, "search_query": "搜索关键词"}\n'
                '或 {"need_search": false, "search_query": ""}'
            )
            raw = await self._llm.chat([{"role": "user", "content": prompt}])
            raw = raw or ""
            m = re.search(r'"need_search"\s*:\s*(true|false)', raw)
            if not m or m.group(1) != "true":
                return False, ""
            mq = re.search(r'"search_query"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            if not mq:
                return False, ""
            try:
                query = json.loads('"' + mq.group(1) + '"')
            except Exception:  # noqa: BLE001
                query = mq.group(1)
            query = (query or "").strip()
            return bool(query), query
        except Exception as e:  # noqa: BLE001
            logger.debug("搜索裁决失败，跳过搜索: %s", e)
            return False, ""

    # 归一化：只保留"字"（中文/字母/数字），去掉标点与空白并小写。
    # 用于"是否复述用户原话"的比对——这样「以后放歌都用汽水音乐」与
    # 「我跟你说，以后放歌都用汽水音乐，记住了吗」才能被认出是同一句。
    @staticmethod
    def _norm_txt(s: str) -> str:
        return re.sub(r'[\s\W_]+', '', (s or ''), flags=re.UNICODE).lower()

    def _is_user_echo(self, candidate: str, user_text: str) -> bool:
        """判定提炼结果是否只是把用户原话复述/截了一段——这类不该进长期记忆。

        用户实锤反馈：说什么话就被"提取话里一段"记进记忆，之后模型照着原话复述，
        体验很差。长期记忆的职责是提炼**稳定事实**（偏好/规矩/约定），不是复读原文。
        用户原话本身已经留在短期对话历史 shared_history 里，不会真的丢失。
        """
        c = self._norm_txt(candidate)
        u = self._norm_txt(user_text)
        if not c or not u:
            return False
        if c == u:
            return True
        # 候选是用户原话里的子串（被截了一段）→ 复述
        if len(c) >= 4 and c in u:
            return True
        # 用户原话是候选的子串，且只多了很少的字（如加个"用户"前缀）→ 仍是复述
        if len(u) >= 4 and u in c and (len(c) - len(u)) <= 6:
            return True
        return False

    def _raw_fallback_allowed(self) -> bool:
        """是否允许在 LLM 提炼失败时，把用户原话截断后兜底入库。

        默认**关闭**——这正是"说什么就记一段"的成因。需要时可在 config.yaml 的
        memory.allow_raw_fallback 打开。
        """
        try:
            return bool(getattr(self._cfg.ns.memory, "allow_raw_fallback", False))
        except Exception:
            return False

    def _assemble_context_recall(self, user_text: str) -> str:
        """【2026-10-11 Plan C】上下文引用：用户回指过去时，从**历史原话**里检索相关句注入。

        与 `_assemble_chat_memory`（长期记忆=已提炼事实）分工：本方法给的是**可追溯
        到具体某天某句的历史原话**（"（昨天）你说过：「等会发照片给你看」"）——
        治"她记不住**当时那句**"（用户问"你还记得你要干嘛吗"就靠这个）。

        只在用户话里含**时间词/回忆词**（"上次/那天/你记得/我说过…"）时才检索，
        否则直接返回空串（零开销，不每轮翻盘）。
        任何异常 → 空串（注入点退化为"没有引用"，绝不比改前更差）。
        """
        t = (user_text or "").strip()
        if not t or self._memory_store is None:
            return ""
        try:
            from .context_recall import get_context_recall
            if not get_context_recall().should_recall(t):
                return ""
            _char = str(getattr(self._cfg, "active_character_id", "") or "")
            return get_context_recall().recall_block(_char, t, limit=3) or ""
        except Exception:  # noqa: BLE001
            logger.debug("上下文引用检索失败（已跳过）", exc_info=True)
            return ""

    def _assemble_chat_memory(self) -> str:
        """【B 步】打字聊天路径的长期记忆装配（2026-10-01）。

        云端档**保持原样**（memory_text(8)，行为不变，未经实测不擅自改宽）；
        本地档（qwen3:14b 仅 4096 上下文）按预算裁剪，防止记忆把上下文挤爆。
        任何异常都回退到原逻辑，保证绝不比改之前更差。

        【2026-10-04 18:35 语义检索修复】此前调 `memory_text(8)` **不传 query**，
        而 `_score()` 里语义权重占 0.4，只有 query 非空且 embed 可用时才生效
        ⇒ 打字聊天这条路径的语义检索一直是关的。现补上「当前用户发言」作 query，
        装了 nomic-embed-text 就能按语义排序；取不到则退化为关键词召回（不变差）。

        【2026-10-11 按档放开条数】此前**写死 `memory_text(8)`**（无论哪档都只取 8 条）
        —— 这是"记忆灌不满/记不住"的**最直接根因**（cloud 档 budget 本就不限，却只喂 8 条）。
        现改为按 `_TIER_CFG[tier]["mem"]` 取条数（cloud 24 / local 10 / local_duplex 3），
        并保留 budget 裁剪。**同档位内仍不裁剪条数之外的内容**，只是把"固定 8"换成"按能力分档"。
        """
        try:
            from .memory_assembler import resolve_tier, TIER_CLOUD, _TIER_CFG, current_topic_query
            _cm = ''
            try:
                _cm = str(getattr(getattr(self._cfg, 'ns', None), 'companion_mode', '') or '')
            except Exception:
                _cm = ''
            tier = resolve_tier(companion_mode=_cm)
            try:
                _q = current_topic_query()
            except Exception:
                _q = ''
            # 按档取条数（旧实现写死 8 —— 本次改为读 _TIER_CFG）
            _hit = int((_TIER_CFG.get(tier) or {}).get('mem') or 8)
            txt = self._memory_store.memory_text(_hit, query=_q) or ''
            if tier != TIER_CLOUD and txt:
                budget = (_TIER_CFG.get(tier) or {}).get('budget') or 0
                if budget and len(txt) > budget:
                    txt = txt[:budget].rstrip()
            return txt
        except Exception:
            try:
                return self._memory_store.memory_text(8) or ''
            except Exception:
                return ''

    # 「我等会 / 回头 / 明天 / 晚点 …给/发/告诉你 …」——用户对达妮娅的未决承诺。
    # 命中即确定性入库（不等 LLM 判定），因为这类"约定"丢了最伤信任。
    _PROMISE_RE = re.compile(
        r"(?:等(?:会|下|一下)|回头|改天|明天|今晚|待会|晚点|稍后|过(?:会|两天)|"
        r"回头|下次|等会)"
        r".{0,16}?"
        r"(?:给|发|递|拿|告诉|给).{0,2}?(?:我|你|达妮娅)?"
        r"|"
        # 反过来："我把照片发给你" / "我给你看看我拍的照片"
        r"我(?:会|要|想|打算)?(?:先)?"
        r"(?:把|将)?.{0,14}?"
        r"(?:发给你|发给你看|给你看|给你听|发我|告诉我|拍给你看)"
    )

    def _remember_pending_promise(self, user_text: str) -> bool:
        """把"用户承诺要做/要给我看的东西"**确定性地**记进长期记忆。

        【2026-10-04 22:22】用户 22:00 实锤：他通话里说"我等会发给你看照片"、
        她回"我已经迫不及待想看啦"，但这句话**没进记忆库** ⇒ 之后他问
        "你还记得你要干嘛吗"她完全答不上来 ⇒ 只能去 `file_search` 翻文件
        （被我们看到"操作电脑"卡片）。

        为什么这条要**绕过 LLM**：
          · LLM 有概率判 `should_extract=false`，或把它当"一次性临时状态"丢掉；
          · 而"他答应给我看照片"恰恰是**最该记住**的一类 —— 忘了就是失约。
          · 规则很窄（将来时 + 给/发/告诉），误伤概率低，代价可控。
        存 `type="user"`（属于"他跟我讲过的事"），文案保留原话首句以便日后原样引用。
        """
        t = (user_text or "").strip()
        if not t or self._memory_store is None:
            return False
        if not self._PROMISE_RE.search(t):
            return False
        try:
            if not self._memory_inject_enabled():
                return False
        except Exception:  # noqa: BLE001
            return False
        # 只取第一句，避免长句灌进记忆库
        first = re.split(r"[。！？\n；]", t)[0].strip() or t
        if len(first) < 4 or len(first) > 60:
            first = first[:60]
        fact = f"用户说过：{first}"
        try:
            added = self._memory_store.add(fact, type="user", source="promise")
        except Exception:  # noqa: BLE001
            return False
        if added:
            logger.info("承诺/待办记忆已写入: %s", fact)
        return bool(added)

    # 与"他此刻在屏幕上做什么"相关的说法 —— 命中才注入画面信息
    _SCREEN_INTENT_RE = re.compile(
        r"屏幕|画面|窗口|桌面|在看|在看什么|显示|界面|游戏|对局|这局|这关|关卡|"
        r"地图|技能|装备|血量|战绩|击杀|死亡|死了|阵亡|倒地|队友|敌人|怪物|BOSS|boss|"
        r"视频|直播|弹幕|主播|播放器|网页|代码|报错|bug|代码高亮|终端|cmd|"
        r"我玩|在玩|正在|操作|打|玩得|卡了|任务|目标|进度|存档|"
        r"你在看|你看到|刚刚|刚才|之前|现在在|我在看|帮我看|看看屏幕|"
        # 【22:48 补·22:47 实测误杀】下列说法第一版没覆盖，导致游戏/直播/代码场景被误杀：
        r"拍照|拍了|相机"          # 拍照讨论常伴随"看下我拍的"
        r"死了|复活|团灭|上分|掉分|排位|段位|出装|连招|操作|瞄准|压枪|"
        r"直播间|评论区|念弹幕|他说了什么"
    )
    # 游戏/直播/代码等**强场景词**：即使同时命中"纯聊天词"也判定为与屏幕相关
    _SCREEN_STRONG_RE = re.compile(
        r"游戏|对局|这局|关卡|地图|击杀|阵亡|倒地|死亡|死了|复活|团灭|上分|掉分|"
        r"排位|段位|出装|连招|瞄准|压枪|血量|战绩|队友|敌人|怪物|BOSS|boss|"
        r"直播|弹幕|主播|视频|屏幕|画面|窗口|桌面|界面|"
        r"代码|报错|bug|终端|cmd|网页|播放器"
    )

    def _query_is_screen_related(self, user_text: str) -> bool:
        """判断这句话是否**真的与"他此刻屏幕上的内容"有关**。

        【2026-10-04 22:45】纯聊天（聊相机/闲聊/知识问答）不该触发画面注入 ——
        否则模型会莫名说"我刚刚看了下屏幕"（22:42 实锤）。
        反过来，**陪看/游戏联动/问"你看我屏幕"必须继续注入**，否则会丢能力。

        ⚠️【22:48 修正·过度收紧】第一版用"纯聊天词优先否决"，实测**误杀 4 个真实场景**：
        「我刚死了吗」「你看我直播有弹幕吗」「帮我看下这个代码报什么错」「我现在在打三角洲」
        —— 尤其**"拍照/相机"被当成纯聊天词，但它完全可能是"看我拍的照片"**。
        现在改为：**强场景词（游戏/直播/代码/画面…）优先，纯聊天词只在"两者都没命中"时否决**。
        """
        t = (user_text or "").strip()
        if not t:
            return False
        # ① 强场景词命中 → 与屏幕相关（优先，覆盖一切）
        if self._SCREEN_STRONG_RE.search(t):
            return True
        # ② 弱线索词命中（"在看/刚/帮我看…"）→ 也算相关
        if self._SCREEN_INTENT_RE.search(t):
            return True
        return False

    # ------------------------------------------------------------------
    # 【2026-10-04 23:10 新增】markdown 符号清洗
    # 用户 22:55/23:06 两次截图实锤：她把 `**娅娅**`、`**你叫我叫「鸡仔」**`
    #   原样吐在气泡里，`**` 直接可见（前端**没有** markdown 渲染，是纯文本）。
    #   同类还有 `## 记下来的三件事`（`##` 井号）、`| 特性 | 细节 |`（表格竖线）、
    #   `- 1.**...` 列表符 → 全都是"Markdown 当聊天说"的破绽。
    # 现在**在源头剥掉**：加粗/斜体的 `**`、`*`、`_`、`##` 标题井号、
    #   行首列表符、行内表格竖线。反引号保留（`x` 偶尔是代码量）。
    # ⚠️ 只剥"标记符号"，**不删内容** —— 否则她写的正经内容会缺字。
    # ------------------------------------------------------------------
    # ⚠️ `(?m)` 必须放**pattern 最前**（Python 3.11+ 对"global flags not at the
    # start"会直接抛 re.error）——这是实测踩到的：写在 `|...|` 之后会编译失败、
    # 整个模块 import 崩。行首列表符/标题井号改用逐行处理，不依赖 (?m)。
    _MD_HINT_RE = re.compile(r"\*\*|__|^\s{0,3}#{1,6}\s+|\|\s*-{2,}")

    @classmethod
    def _strip_markdown(cls, text: str) -> str:
        """剥掉聊天里不该出现的 Markdown 标记（只去符号、不删内容）。"""
        t = (text or "").strip()
        if not t:
            return t
        # 表格：把整块「| a | b | c |」行压成一行可读文本
        if t.count("|") >= 2 and "\n" in t:
            _rows = []
            for ln in t.splitlines():
                if ln.count("|") >= 2:
                    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                    cells = [c for c in cells if c and not re.fullmatch(r"[:\- ]+", c)]
                    if cells:
                        _rows.append("，".join(cells))
                elif ln.strip():
                    _rows.append(ln.strip())
            if _rows:
                t = "\n".join(_rows)
        # 逐行去：标题井号 / 行首列表符
        _out = []
        for ln in t.splitlines():
            ln2 = re.sub(r"^\s{0,3}#{1,6}\s+", "", ln)
            ln2 = re.sub(r"^\s*[-*+]\s+", "", ln2)
            ln2 = re.sub(r"^\s*\d+[.)]\s+", "", ln2)
            _out.append(ln2)
        t = "\n".join(_out)
        # 行内标记
        t = cls._MD_HINT_RE.sub("", t)
        t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)   # 兜底：漏网的成对加粗
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)  # 斜体
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    # ===== 说话节奏：把"念小说"的回复收成"像人在打字"（2026-10-11）=====
    # 背景（实测 22 条聊天页回复）：均长 95 字、最长 233；68% 超 60 字、77% 带
    #   （动作）描写、72% 多段、54% 一次问 ≥2 个问题 —— 用户体感"不像人、话太多、
    #   还描述场景"。双工语音那条链路由 proactive 自己的提示词管住（均长 20 字），
    #   只有聊天页这条需要收。两层兜底：
    #   ① 提示词侧：prompts.SYSTEM_TEMPLATE 新增【说话节奏】块，让模型别写长；
    #   ② 这里：格式清理 + 超长截断，不靠模型自觉（模型会照写不误）。
    # 【2026-10-14 补·覆盖【】】弱模型（如免费 GLM）在傲娇人设下会冒"【翻个白眼】"这类
    #   全角方括号动作描写；原正则只洗（）/()，【】会漏网 → 原样上屏/进 TTS。
    #   与（）、() 并列补一条【】分支，同样带 \d 护栏（不误删"【1】"这类编号）。
    _STAGE_DIR_RE = re.compile(
        r"[（(]\s*[^（()）\d]{1,40}?\s*[）)]|【\s*[^【】\d]{1,40}?\s*】")
    # 兜底常量：只在没传本轮预算时使用（直调 _humanize_reply 的场景）。
    # 实际每轮由 _reply_budget(user_text) 按用户话长给值——0 句 = 关闭截断。
    CHAT_MAX_SENTENCES: int = 2
    CHAT_TRIM_MIN_CHARS: int = 70
    # 「回多长」分档：(用户这句话的字数上限, 她最多回几句, 她整条字数上限)。
    #   用户话短 → 回得短；说了一大段 → 允许她多接一两句。增长慢于输入且有封顶，
    #   避免"用户粘长文她就长篇大论"。（只数实义字符，空白不计）
    _REPLY_LEN_TIERS = (
        (8,     1, 24),    # 很短（"我讨厌你""在吗"）→ 一句，别啰嗦
        (25,    2, 44),    # 一两句
        (60,    3, 72),    # 中段 → 可接三句
        (120,   4, 104),   # 长倾诉
        (10**9, 5, 132),   # 很长 → 最多五句（封顶，防刷屏）
    )

    @classmethod
    def _reply_budget(cls, user_text: str) -> tuple[int, int]:
        """按用户这句话的长短，算她该回多长 → (句数上限, 整条字数上限)。

        这是"话长短跟着用户走"的唯一真相源：短话短回、长话才允许多说两句。
        提示词口径、流式分句上限、终稿截断都从这里取值，保证三处一致。
        """
        n = len(re.sub(r"\s+", "", user_text or ""))
        for limit, sent, chars in cls._REPLY_LEN_TIERS:
            if n <= limit:
                return sent, chars
        return cls.CHAT_MAX_SENTENCES, cls.CHAT_TRIM_MIN_CHARS

    @classmethod
    def _strip_stage_directions(cls, text: str) -> str:
        """删掉括号里的动作/表情/心理/场景描写（如"（捂住脸）"）——这是要念出来的破绽。

        只删括号内容、不碰正文；含数字的括号（编号/年份）保留，避免误删。
        括号删完若整行空了则清掉该行；若全删空了则退回原文（绝不交白卷）。
        """
        t = (text or "").strip()
        if not t:
            return t
        t = cls._STAGE_DIR_RE.sub("", t)
        _out = []
        for ln in t.splitlines():
            ln = ln.strip()
            # 只剩标点的残行（删掉括号后常出现）→ 丢弃
            if ln and re.sub(r"[。，、！？…—~～.\-—\s]", "", ln):
                _out.append(ln)
        t2 = re.sub(r"[ \t]{2,}", " ", "\n".join(_out)).strip()
        return t2 or t

    @classmethod
    def _drop_stage_dirs(cls, text: str) -> str:
        """同 _strip_stage_directions，但"全删空"时返回空串而不是退回原文。

        【2026-10-12】给流式分段（_emit_seg）专用：整段只是一句动作描写
        （如 "(凑近)"）就该整段丢掉；整段回复的"绝不交白卷"保底在这里
        反而会把动作描写原样放行（实测 case：末段 "(凑近)" 原样上屏）。
        """
        t = (text or "").strip()
        if not t:
            return ""
        t = cls._STAGE_DIR_RE.sub("", t)
        _out = []
        for ln in t.splitlines():
            ln = ln.strip()
            if ln and re.sub(r"[。，、！？…—~～.\-—\s]", "", ln):
                _out.append(ln)
        return re.sub(r"[ \t]{2,}", " ", "\n".join(_out)).strip()

    @classmethod
    def _humanize_reply(cls, text: str, cap: bool = True,
                        max_sentences: Optional[int] = None,
                        trim_min_chars: Optional[int] = None) -> str:
        """把"念小说"的长回复收成"像人在打字"的短回复（聊天页专用）。

        ① 去括号动作/场景描写；② 多段并成一段；③ 句子数超上限才截断。
        max_sentences / trim_min_chars：本轮口径（由 _reply_budget 按用户话长给），
        不传则退回类默认常量。
        极短语气句（"啊？""呜呜……"）先并入下一句再计数 —— 否则它们会被当成
        独立句子吃掉名额、把真正的回答挤掉。
        """
        t = cls._strip_stage_directions(text)
        if not t:
            return t
        t = re.sub(r"\s*\n+\s*", "", t)          # 多段 → 一段（中文句末自带标点）
        t = re.sub(r"[ \t]{2,}", " ", t).strip()
        _max_sent = cls.CHAT_MAX_SENTENCES if max_sentences is None else int(max_sentences)
        _trim = cls.CHAT_TRIM_MIN_CHARS if trim_min_chars is None else int(trim_min_chars)
        if not cap or _max_sent <= 0 or len(t) <= _trim:
            return t
        frags = re.findall(r"[^。！？!?…]*[。！？!?…]+|[^。！？!?…]+", t)
        merged: list[str] = []
        for f in frags:
            f = f.strip()
            if not f:
                continue
            _core = re.sub(r"[。！？!?…\s]", "", f)
            if merged and len(re.sub(r"[。！？!?…\s]", "", merged[-1])) < 4:
                merged[-1] += f                  # 上句太短（语气句）→ 并入本句
            else:
                merged.append(f)
        if len(merged) > _max_sent:
            t = "".join(merged[:_max_sent]).strip()
            logger.info("聊天回复过长，按本轮口径收到 %d 句：%r → %r",
                        _max_sent, (text or "")[:80], t[:80])
        return t.strip()

    async def _maybe_extract_memory(self, user_text: str) -> None:
        """从用户话里提炼长期记忆（纠正/偏好/约定）并永久保存。

        jarvis 方案（store.mode=="jarvis" 且 intent_judge 开）：
          LLM 一次调用返回 JSON {should_extract, type, fact}——
          判定 + 分类（directive/user/world）+ 提炼一步完成；指令类(directive)逐字保留，注入时特权显示。
        legacy / 关闭意图判定：正则预筛 + 提炼一句，存为 user 类型。
        失败或 LLM 判定"无可记内容"时：含强纠正词的句子原样兜底保存，别把教训丢了。
        """
        if self._memory_store is None or not user_text.strip():
            return
        if not EXTRACT_HINT_RE.search(user_text):
            return
        # 【2026-10-04 22:22 新增·"承诺/待办"确定性兜底·不依赖 LLM】
        # 用户 22:00 实锤：他说"我等会发给你看照片"、她回"迫不及待想看"，
        # 但这句话**没进记忆库** ⇒ 22:0x 他问"你还记得你要干嘛吗"她完全答不上。
        #
        # 为什么不用 LLM 提炼来兜底：LLM 有概率判"无可记内容"(should_extract=false)、
        # 或提炼时把它当成"一次性临时状态"丢掉。**而这类承诺恰恰是用户最指望你
        # 记住的**（他答应给你看的东西，忘了就是失约）。
        # ⇒ 这里用**确定性规则**直接入库：命中承诺句式就记，LLM 判定如何都不影响。
        #   代价可控：句式很窄（必须含"给/发/告诉你" + 将来时），且截 60 字。
        try:
            if self._remember_pending_promise(user_text):
                # 记了就继续走正常提炼（可能还会记出偏好等其它事实）
                pass
        except Exception:  # noqa: BLE001
            logger.debug("承诺兜底记忆失败", exc_info=True)
        # 节流：10s 内只提炼一次，避免连续对话疯狂写记忆
        now = time.time()
        if now - self._last_extract_at < 10:
            return
        self._last_extract_at = now
        _t0 = time.time()
        try:
            prompt = MEMORY_EXTRACT_PROMPT.replace("{text}", user_text[:200])
            raw = await asyncio.wait_for(
                self._llm.chat([{"role": "user", "content": prompt}], options={"temperature": 0.0}),
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("长期记忆提炼失败: %s", e)
            raw = ""
        # 【2026-10-01 D】观测仪：记录一次提炼调用的模型 / 提示词量 / 耗时
        try:
            from ..llm.memory_profiler import record_probe
            _model = getattr(self._llm, "model", "") or getattr(self._llm, "name", "") or ""
            record_probe("", _model, "extract",
                         prompt_tokens=len(prompt),
                         latency_ms=(time.time() - _t0) * 1000.0,
                         note="chat")
        except Exception:  # noqa: BLE001
            pass

        # jarvis 方案：JSON 判定 + 分类
        if self._memory_store.mode == "jarvis" and _memory_intent_judge(self._cfg):
            parsed = _parse_extract_json(raw)
            if parsed and parsed.get("should_extract"):
                fact = str(parsed.get("fact", "")).strip()
                typ = str(parsed.get("type", "user")).strip() or "user"
                if fact:
                    # 【2026-10-01 提质】提炼结果若只是复述用户原话 → 不入库
                    if self._is_user_echo(fact, user_text):
                        logger.debug("长期记忆跳过：提炼结果只是复述用户原话，不入库")
                        return
                    self._memory_store.add(fact[:80], type=typ, source="user_correction")
                    return
            # LLM 明确判定"无可记"（should_extract=false）→ 尊重判定，不写记忆。
            # 【2026-10-01 提质】原先"LLM 失败就把强纠正句原样截断入库"是
            #   "说什么话就记一段、之后照着复述"的直接成因，现已默认关闭
            #   （可用 memory.allow_raw_fallback 打开）。原话本身仍留在短期对话历史
            #   shared_history 里，不会真的丢失。
            if not raw and _STRONG_CORRECT_RE.search(user_text):
                if not self._raw_fallback_allowed():
                    logger.debug("强纠正句未入库：LLM 提炼失败且未开启 allow_raw_fallback"
                                 "（原话保留在短期对话历史中）")
                    return
                self._memory_store.add(user_text[:80], type="user",
                                       source="user_correction_fallback")
            return

        # legacy / 关闭意图判定：提炼一句存 user
        raw = (raw or "").strip().strip('“”"\'')
        if raw and not raw.upper().startswith("NONE"):
            # 【2026-10-01 提质】同样拦掉"只是把用户原话复述一遍"的提炼结果
            if self._is_user_echo(raw, user_text):
                logger.debug("长期记忆跳过(legacy)：提炼结果复述用户原话，不入库")
                return
            self._memory_store.add(raw[:60], type="user", source="user_correction")
            return
        # 仅当 LLM 调用失败（raw 为空）时，对强纠正句式兜底（同样只存 user）
        # 【2026-10-01 提质】默认不再用原话兜底，见 _raw_fallback_allowed 说明
        if not raw and _STRONG_CORRECT_RE.search(user_text):
            if not self._raw_fallback_allowed():
                logger.debug("强纠正句未入库(legacy)：未开启 allow_raw_fallback")
                return
            self._memory_store.add(user_text[:60], type="user", source="user_correction_fallback")

    # ══════════════════════════════════════════════════════════════════════
    # 【2026-10-11 新增】会话级情节记忆（episode）提取
    # ══════════════════════════════════════════════════════════════════════
    async def _maybe_extract_episode(self, user_text: str) -> None:
        """把**最近一段对话**提炼成带细节的"情节记忆"，治"只记扁平事实、总结不出细节"。

        与 `_maybe_extract_memory` 的区别（关键）：
          · 后者：**单句** + 关键词白名单触发 + ≤30 字扁平事实 → 实测 11 次提炼产出 0 条；
          · 本方法：**一段对话** + **按轮数累积触发**（不靠关键词）→ 保留"何时/做了什么/
            和谁/什么感受"的细节，存为 type=episode（≤200 字）。
        触发：每累积 `_episode_min_turns` 轮触发一次；失败/无内容一律静默降级（不抛）。
        """
        if self._memory_store is None or not user_text.strip():
            return
        try:
            self._episode_turn_ctr += 1
            if self._episode_turn_ctr < self._episode_min_turns:
                return
            self._episode_turn_ctr = 0
            # 取最近一段对话（含她的话，才能判"这件事"的来龙去脉）
            try:
                from .conversation_history import shared_history
                recent = shared_history().get_recent(12)
            except Exception:  # noqa: BLE001
                recent = []
            if not recent:
                return
            seg = []
            for m in recent:
                who = "用户" if str(m.get("role")) == "user" else "达妮娅"
                c = str(m.get("content") or "").strip()
                if c:
                    seg.append(f"{who}：{c[:200]}")
            if not seg:
                return
            seg_text = "\n".join(seg)[-1500:]
            from .memory_store import MEMORY_EPISODE_PROMPT
            prompt = MEMORY_EPISODE_PROMPT.replace("{text}", seg_text)
            raw = await asyncio.wait_for(
                self._llm.chat([{"role": "user", "content": prompt}], options={"temperature": 0.2}),
                timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("情节记忆提炼失败（已跳过）: %s", e)
            return
        # 解析 [{fact,type}]（复用既有解析器，容忍前后废话）
        try:
            from .memory_store import _parse_fact_items
            items = _parse_fact_items(raw)
        except Exception:  # noqa: BLE001
            items = []
        stored = 0
        for fact, _typ in items:
            f = (fact or "").strip()
            if len(f) < 4:
                continue
            try:
                # 情节记忆统一存 episode（不采信 LLM 的 type，避免污染其它分类）
                if self._memory_store.add(f[:200], type="episode", source="chat"):
                    stored += 1
            except Exception:  # noqa: BLE001
                continue
        if stored:
            logger.info("情节记忆已写入 %d 条（会话级摘要）", stored)
            # 【2026-10-11 Plan B】刚写了新情节 → 顺手触发一次"反思聚合"（异步，
            #   把零散事件聚合成用户画像洞察）。失败不影响主链。
            asyncio.create_task(self._maybe_reflect_episodes())

    # ══════════════════════════════════════════════════════════════════════
    # 【2026-10-11 新增】周期性反思聚合（Plan B，Generative Agents 式）
    # ══════════════════════════════════════════════════════════════════════
    async def _maybe_reflect_episodes(self) -> None:
        """把近期零散 episode 聚合成**画像级洞察**（存 type=user），治"她对用户没有整体认知"。

        触发：每积累 `_reflect_min_episodes` 条**新** episode 才聚合一次（节流，避免
        每写一条就调 LLM）。产出写 user 记忆，与既有扁平事实互补。
        失败/无内容一律静默降级（不抛）。
        """
        if self._memory_store is None:
            return
        try:
            eps = self._memory_store.recent_episodes(limit=20)
        except Exception:  # noqa: BLE001
            return
        if len(eps) < int(getattr(self, "_reflect_min_episodes", 4)):
            return
        # 节流：只为"自上次反思以来新增的" episode 做聚合（避免反复聚合同一批）
        _last_ts = float(getattr(self, "_reflect_last_ep_ts", 0.0) or 0.0)
        fresh = [e for e in eps if float(e.get("created_at", 0) or 0) > _last_ts]
        if len(fresh) < int(getattr(self, "_reflect_min_episodes", 4)):
            return
        try:
            lines = []
            for e in eps[:12]:
                txt = str(e.get("text") or "").strip()
                if txt:
                    lines.append("- " + txt[:200])
            if not lines:
                return
            from .memory_store import MEMORY_REFLECT_PROMPT
            prompt = MEMORY_REFLECT_PROMPT.replace("{text}", "\n".join(lines))
            raw = await asyncio.wait_for(
                self._llm.chat([{"role": "user", "content": prompt}],
                               options={"temperature": 0.2}),
                timeout=20.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("反思聚合失败（已跳过）: %s", e)
            return
        # 解析并写入（跳过与既有 user 记忆近似重复的洞察）
        try:
            from .memory_store import _parse_fact_items
            items = _parse_fact_items(raw)
        except Exception:  # noqa: BLE001
            items = []
        stored = 0
        _max_ts = _last_ts
        for fact, _typ in items:
            f = (fact or "").strip()
            if len(f) < 4:
                continue
            try:
                if self._memory_store.has_insight(f):
                    continue
                if self._memory_store.add(f[:80], type="user", source="chat"):
                    stored += 1
            except Exception:  # noqa: BLE001
                continue
        # 无论是否写入成功，都推进反思水位（避免同一批反复尝试）
        for e in eps:
            _max_ts = max(_max_ts, float(e.get("created_at", 0) or 0))
        self._reflect_last_ep_ts = _max_ts
        if stored:
            logger.info("反思聚合已写入 %d 条用户画像洞察", stored)

    # ══════════════════════════════════════════════════════════════════════
    # 【2026-10-11 新增】上下文驱动的整段记忆提炼（替代旧单句白名单方案）
    # ══════════════════════════════════════════════════════════════════════
    def _get_synthesizer(self):
        """惰性构造 MemorySynthesizer（按当前档位）。失败返回 None（降级）。"""
        syn = getattr(self, "_synth", None)
        if syn is not None:
            return syn
        if self._memory_store is None or self._llm is None:
            return None
        try:
            from .memory_synthesis import make_synthesizer
            from .memory_assembler import resolve_tier
            try:
                _cm = str(getattr(getattr(self._cfg, 'ns', None), 'companion_mode', '') or '')
            except Exception:  # noqa: BLE001
                _cm = ''
            tier = resolve_tier(companion_mode=_cm)
            self._synth = make_synthesizer(self._llm, self._memory_store,
                                           cfg=self._cfg, tier=tier)
            return self._synth
        except Exception:  # noqa: BLE001
            logger.debug("记忆合成器构造失败（降级为不提炼）", exc_info=True)
            return None

    async def _maybe_synthesize_memory(self, turns: list | None = None,
                                       force: bool = False) -> None:
        """上下文快超限时，把**整段对话**交给 LLM 提炼（异步，绝不阻塞对话）。

        与旧 `_maybe_extract_memory` 的分工（2026-10-11 开发者拍板）：
          · 旧方案（单句 + 关键词白名单）**已断开调用**（保留代码可回退）——实测 1:404
            触发率 + "我晚上七点吃的饭"连白名单都不命中；
          · 本方案：整段上下文 → LLM 自己判断+提取，实测能记住"七点吃饭/去西安/养猫"等细节。
        触发：`should_synthesize()` 判"快超限"（云端 80% / 本地 70% 窗口）。
        失败静默降级（不抛、不影响对话）。
        """
        syn = self._get_synthesizer()
        if syn is None:
            return
        try:
            _char = ''
            try:
                _ch = self._cfg.get_active_character() if self._cfg else None
                _char = str(getattr(_ch, 'name', '') or '达妮娅')
            except Exception:  # noqa: BLE001
                _char = '达妮娅'
            if not force and not syn.should_synthesize(turns):
                return
            res = await syn.synthesize(turns, force=force, char_name=_char)
            if res.get("ok") and res.get("stored"):
                logger.info("[记忆合成] 自动提炼写入 %d 条（上下文 %d 轮 / 用量 %.0f%%）",
                            res["stored"], res.get("turns", 0),
                            (res.get("usage_ratio") or 0) * 100)
        except Exception:  # noqa: BLE001
            logger.debug("[记忆合成] 自动提炼失败（已跳过）", exc_info=True)

    async def synthesize_memory_now(self, max_turns: int = 60) -> dict:
        """【UI 手动触发】对**当前对话上下文**立即跑一次整段记忆提炼。

        返回结果（供 UI 显示）：`{ok, stored, items, turns, ms, usage_ratio, error}`。
        与自动提炼共用同一合成器；`force=True` 跳过节流（用户明确点了就要跑）。
        """
        syn = self._get_synthesizer()
        if syn is None:
            return {"ok": False, "error": "记忆库未启用或模型不可用", "stored": 0}
        _char = '达妮娅'
        try:
            _ch = self._cfg.get_active_character() if self._cfg else None
            _char = str(getattr(_ch, 'name', '') or '达妮娅')
        except Exception:  # noqa: BLE001
            pass
        try:
            from .conversation_history import shared_history
            turns = shared_history().get_recent(int(max_turns))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"取上下文失败: {e}", "stored": 0}
        return await syn.synthesize(turns, force=True, char_name=_char)

    async def switch_character(self, char_id: str) -> bool:
        """运行时切换角色，更新 persona 并通知 Unity。"""
        char = self._cfg.get_character_by_id(char_id)
        if char is None:
            logger.warning("角色不存在: %s", char_id)
            return False

        # 更新配置
        self._cfg.switch_character(char_id)

        # 更新 persona
        self._persona.update_from_character(char)

        # 【2026-10-11·多角色隔离重构】旧实现 `shared_history().clear()` 是"一把抹掉"：
        #   ① 换人后新角色拿到的是空历史（可接受），但**切回旧角色时他的整段上下文已永久
        #      丢失**（磁盘 chatlog 只用于界面展示，喂模型的 messages 上下文救不回来）；
        #   ② clear 只作用于"当时活动桶"，若双工/关系写入方在切换瞬间落笔仍可能串味。
        #   现改为**按 char_id 分桶**：切人 = 把活动指针指向新角色的独立历史，
        #   旧角色历史原样保留（切回即恢复上下文），写入/读取全部按桶隔离，天然不混淆。
        try:
            from .conversation_history import set_active_char as _set_hist_char
            _set_hist_char(char_id)
        except Exception:
            logger.debug("共享会话历史切换角色失败", exc_info=True)
        self._event_log.clear()

        # 【2026-10-01 A-5】长期记忆切到该角色作用域。
        # MemoryStore 实例被 dialogue 与 proactive 共享，故这里设一次，打字与语音两条
        # 路径同时隔离；旧数据（无 character 字段）作为通用记忆仍会注入，不会突然失忆。
        try:
            if self._memory_store is not None and hasattr(self._memory_store, "set_character"):
                self._memory_store.set_character(char_id)
        except Exception:
            logger.debug("长期记忆切换角色作用域失败", exc_info=True)

        logger.info("角色切换成功: %s → %s", self._cfg.active_character_id, char.name)

        # 广播角色切换事件（给控制台）
        await self._bus.publish(Event(EventType.CHARACTER_SWITCHED, {
            "char_id": char_id,
            "char_name": char.name,
        }, "dialogue"))

        return True

    async def refresh_active_persona(self) -> None:
        """从配置刷新当前激活角色的人设（编辑角色后调用，不打断对话、不通知 Unity）。"""
        char = self._cfg.get_active_character()
        if char is not None:
            self._persona.update_from_character(char)

    async def generate_response(self, user_message: str, context: dict = None, mode: str = "normal") -> dict:
        """生成AI响应（支持多种模式，包括桌宠互动）。
        
        Args:
            user_message: 用户消息
            context: 上下文信息
            mode: 模式（normal, pet_interaction等）
            
        Returns:
            {"text": str, "action": str, "emotion": str}
        """
        if not self._llm.available:
            return {"text": "抱歉，AI模型暂时不可用", "action": "sigh", "emotion": "neutral"}
        
        try:
            # 构建特定模式的提示词
            if mode == "pet_interaction":
                character = getattr(self._cfg, 'character', '达妮娅')
                personality = getattr(self._cfg, 'personality', '慵懒俏皮')
                
                # 根据上下文构建不同的提示词
                context_type = context.get('type', 'click') if context else 'click'
                
                prompts = {
                    'click': f"""
你是{character}，性格{personality}。用户点击了你，你应该做出可爱的反应。
请返回JSON格式的响应：
{{
    "text": "你说的话",
    "action": "动作名称（如nod, shake, thinking等）",
    "emotion": "表情名称（如smile, surprise, curious等）"
}}""",
                    'double_click': f"""
你是{character}，性格{personality}。用户双击了你，表现出惊喜和喜悦。
请返回JSON格式的响应：
{{
    "text": "惊喜的话",
    "action": "惊讶动作（如surprised, cheer等）",
    "emotion": "惊讶表情（如surprise等）"
}}""",
                    'drag': f"""
你是{character}，性格{personality}。用户拖动你，你应该有相应的反应。
请返回JSON格式的响应：
{{
    "text": "被拖动时的话",
    "action": "轻微摆动（如thinking等）",
    "emotion": "中性表情（如neutral等）"
}}"""
                }
                
                system_prompt = prompts.get(context_type, prompts['click'])
                user_prompt = user_message
            else:
                # 普通对话模式
                system_prompt = "你是一个智能助手，请自然地回应用户的消息。"
                user_prompt = user_message
            
            # 构建消息
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            # 调用LLM
            response = await self._llm.generate(messages)
            
            # 解析响应（剥 ```json 围栏 + 正则抽字段，**绝不把原始 JSON 原文当话**）
            # 【2026-10-11 修·原始 JSON 泄漏】原为裸 `json.loads(response)`：模型偶发包
            #   ```json 围栏时 json.loads 必失败 → 兜底把整段 JSON 当 text → 泄漏进聊天框。
            _out = parse_pet_response(response)
            # `expression` 是 pet_interact 读取的键名（历史遗留）；与 `emotion` 同值回填，
            # 否则桌宠表情永远丢成空串（= 实测泄漏条目里 emotion='' 的成因）。
            _out["expression"] = _out["emotion"]
            return _out
                
        except Exception as e:
            logger.exception("生成响应失败")
            # 返回默认响应
            return {
                "text": "哎呀，我暂时想不出什么好说的～",
                "action": "thinking",
                "emotion": "curious"
            }

    def list_characters(self) -> list[dict]:
        """列出所有角色（供 UI 选择）。"""
        result = []
        for char in self._cfg.list_characters():
            result.append({
                "id": char.id,
                "name": char.name,
                "character": char.character[:50] + "..." if len(char.character) > 50 else char.character,
                "voice_engine": char.voice.engine,
                "voice_name": char.voice.voice,
                "is_active": char.id == self._cfg.active_character_id,
            })
        return result

    @property
    def current_character_id(self) -> str:
        return self._cfg.active_character_id

    @property
    def current_character_name(self) -> str:
        char = self._cfg.get_active_character()
        return char.name if char else "AI"
