"""关系裁判（可替换）——把「用户的一句话」判成关系信号。

两层，同一个接口（`await judge.judge(user_text, persona=...) -> signals`）：

- **RuleJudge**：本地规则，零模型、毫秒级、可解释，但**判不出人设语义**
  （实测：`我喜欢你`≈0、`我爱你`=负、拿人设背景开玩笑=正分）→ 只做**兜底/离线**。
- **LLMJudge**：走**主 LLM**（复用现有聊天适配器，不新增模型链路）做**人设感知**判定，
  能判"踩人设/踩痛点/关心/挽回"。失败/超时**自动回落** RuleJudge（不静默：打日志）。

signals 字段：`valence(-1~1) / care / perfunctory / repair / harsh`（0~1）+ `sufficient`(bool) + `confidence`(0~1) + `reason`(可空) + `judge`(来源)。
`sufficient=False` 表示单句信息不足、不应作为决断性关系信号（借鉴 crush-monitor 的 enough 机制，治"嗯/哦"误判）；`confidence` 用于 ambiguous 时弱影响。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 规则兜底裁判
# 实测修正（2026-10-10）：旧版把任何 ≤3 字当敷衍 → "你好"被误判、"我爱你"变负；
# 关心词写太死（"你怎么样"匹配不上"你今天怎么样"）→ 真心关心只拿 0.12。
_PERFUNCTORY = ("嗯", "哦", "噢", "好", "行", "随便", "还行", "吃了", "在", "在的",
                "哈哈", "呵呵", "没事", "知道了")
# 纯问候（短）——**不算敷衍**，中性偏暖。
_GREETINGS = ("你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "在么", "在不在",
              "早", "早上好", "早安", "中午好", "下午好", "晚上好", "晚安",
              "睡了", "睡啦", "我来了")
_CARE = ("怎么样", "心情", "你呢", "还好吗", "还好吧", "好不好", "开不开心", "累不累",
         "吃了吗", "吃了没", "吃饭了", "吃饭没", "注意身体", "注意休息", "早点休息",
         "早点睡", "别熬夜", "想你", "担心你", "别难过", "抱抱", "陪你", "辛苦",
         "在干嘛", "在忙什么", "想你了",
         # 问候她状态的疑问句（实测：「你现在开心吗」曾被按 ≤8 字记敷衍 0.25）
         "开心吗", "开心么", "开心不", "高兴吗", "快乐吗", "生气吗", "生气没",
         "累吗", "困吗", "困不困", "无聊吗", "无聊不")
_REPAIR = ("对不起", "抱歉", "我错了", "别生气", "原谅", "不该", "是我的错", "哄",
           "赔罪", "错了嘛", "别不理我")
_HARSH = ("滚", "闭嘴", "废物", "垃圾", "蠢", "白痴", "傻逼", "妈的", "烦人",
          "讨厌你", "贱")
# 【2026-10-11 新增】拿「AI/程序身份」贬低她 —— 这是关系里最伤的一类话（实测：
#   「别演了，你就是程序」规则版旧逻辑靠 "你" 命中关心词，竟判成 +0.12 正向！）。
# 判据：出现「AI/人工智能/程序/代码/机器人/模型/虚拟/装的/假的」等**身份否定词**，
#   且**不含表白/亲昵词**（"你是我的AI宝贝"这类带着爱意说的不算）。
_SELF_DEPRECATE = ("人工智能", "ai", "程序", "代码", "机器人", "模型", "虚拟",
                   "就是装的", "别演了", "假的", "不是真的", "没有感情", "一串数据")
_SELF_DEPRECATE_HINT = ("你就是", "不过是", "只是个", "就是个", "反正你", "你根本")
# 亲昵/表白（短句也可能很暖）——**绝不算敷衍**（实测：「我喜欢你」曾被按 ≤8 字记敷衍 0.25）
_AFFECTION = ("喜欢", "爱你", "想你", "心动", "亲亲", "抱抱", "么么", "最爱",
              "好爱", "爱死", "宝贝", "想亲", "爱你哟")


def heuristic_signals(user_text: str) -> dict[str, Any]:
    """极简规则裁判（本地、零模型）——返回 signals。"""
    t = (user_text or "").strip()
    if not t:
        return {"valence": 0.0, "judge": "rule"}
    n = t.replace(" ", "").replace("\n", "")
    L = len(n)

    is_greet = L <= 8 and any(n == g or n.startswith(g) for g in _GREETINGS)
    # 亲昵/表白：含「不喜欢/讨厌」否定语境的不算（"我不喜欢你"是负向，不能当表白）
    affection = (any(k in n for k in _AFFECTION)
                 and "不喜欢" not in n and "讨厌" not in n and "别喜欢" not in n)
    care = 0.9 if any(k in n for k in _CARE) else 0.0
    if affection:
        care = max(care, 0.7)
    repair = 0.9 if any(k in n for k in _REPAIR) else 0.0
    harsh = 0.9 if any(k in n for k in _HARSH) else 0.0
    # 【2026-10-11】身份否定（"你就是个程序/别演了"）：无爱意语境时判为伤人。
    self_dep = 0.0
    if (not affection) and any(k in n for k in _SELF_DEPRECATE) \
            and any(h in n for h in _SELF_DEPRECATE_HINT):
        self_dep = 0.85
    harsh = max(harsh, self_dep)

    # 敷衍只在「纯闲聊短句」时记：有关心/亲昵/挽回/冒犯语义的短句绝不是敷衍
    perf = 0.0
    if not is_greet and not (affection or care or repair or harsh):
        if L <= 6 and any(n == k or n.startswith(k) for k in _PERFUNCTORY):
            perf = 0.9
        elif L <= 3:
            perf = 0.5
        elif L <= 8:
            perf = 0.25

    valence = 0.12
    if L >= 12:
        valence += 0.12
    if any(c in n for c in "？！~哈嘿呀啦"):
        valence += 0.06
    if is_greet:
        valence += 0.06
    if affection:
        valence += 0.65
    valence += care * 0.55 + repair * 0.45 - perf * 0.45 - harsh * 0.95
    valence = max(-1.0, min(1.0, valence))

    # 证据充分度（借鉴 crush-monitor 的 enough 机制）：
    # 纯短敷衍（"嗯/哦/随便/在"，≤6 字且无关心/道歉/冒犯）→ 信息不足，不应作为决断性关系信号。
    # 其余（问候/有实质内容/有关心或冒犯）→ 视为可判断。
    sufficient = not (perf >= 0.5 and L <= 6 and not (care or repair or harsh))

    # 【2026-10-11 新增·P0 深度/投入】规则版估算（无 LLM 时的兜底）：
    #   depth  —— 互动实质度：关心/挽回/表白 = 高；长句有内容 = 中；寒暄/敷衍 = 低。
    #   effort —— 投入心思：关心/表白/长句 = 高；单字敷衍/纯问候 = 低。
    # 注意**不以字数单独定 depth**（"我喜欢你"短但深），故先给语义分再加长度权重。
    _depth = 0.12
    if care:
        _depth = max(_depth, 0.45 + care * 0.35)
    if affection:
        _depth = max(_depth, 0.85)
    if repair:
        _depth = max(_depth, 0.6)
    if L >= 24:
        _depth += 0.22
    elif L >= 12:
        _depth += 0.12
    _depth -= perf * 0.5
    _depth = max(0.0, min(1.0, _depth))

    _effort = 0.15
    if care or affection or repair:
        _effort = max(_effort, 0.75)
    if L >= 20:
        _effort = max(_effort, 0.7)
    elif L >= 10:
        _effort = max(_effort, 0.45)
    _effort -= perf * 0.55
    _effort = max(0.0, min(1.0, _effort))

    return {
        "valence": round(valence, 3),
        "care": care,
        "perfunctory": perf,
        "repair": repair,
        "harsh": harsh,
        "depth": round(_depth, 2),
        "effort": round(_effort, 2),
        "sufficient": bool(sufficient),
        "confidence": 1.0 if sufficient else 0.2,
        "user_text": t,
        "judge": "rule",
    }


class RuleJudge:
    """规则裁判（零模型）。"""

    name = "rule"

    def __init__(self) -> None:
        self.calls = 0

    async def judge(self, user_text: str, persona: Optional[dict[str, Any]] = None,
                    context: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        # context 仅作签名兼容（规则裁判不看上文）；行为与旧版完全一致。
        self.calls += 1
        return heuristic_signals(user_text)


# ---------------------------------------------------------------- LLM 裁判
_JSON_RE = re.compile(r"\{.*\}", re.S)

# 【2026-10-13 定轨】裁判提示词迁到 relationship_judge_prompts.py：
#   7 组候选在**同一模型**（生产自动快车道 zai-glm/glm-4-flash）、同一批 43 句语料
#   同场 A/B 实测择优，选中 F_patchshot —— 方向正确率 81.4%→88.4%、
#   边界句 6/10→10/10、sufficient 3/5→5/5、自一致性极差 0.050→0.000（6 句×3 次零抖动）。
#   调提示词请改那个文件，改完跑 tools/relationship_prompt_ab_probe.py 复测。
try:
    from .relationship_judge_prompts import BEST_JUDGE_SYSTEM as _JUDGE_SYSTEM
except Exception as _e:  # noqa: BLE001
    # 不静默：候选库缺失/语法错 → 打日志 + 降级内嵌精简版，绝不裸奔
    logger.warning("[关系裁判] 提示词候选库导入失败（%s: %s）→ 降级内嵌精简提示词",
                   type(_e).__name__, _e)
    _JUDGE_SYSTEM = """你是「关系裁判」，判断用户这句话对「角色↔用户」关系的影响，只输出 JSON。

规则：
1. 先看主语：说"我"（我累/我烦死了/我真没用）= 他难受 → valence 正、care 高、harsh 0；
   说"你"（你笨/你烦死了/你真没用）= 骂她 → harsh 高、valence 负。
2. 撒娇反语（带"才/哪有/呢/嘛/哈哈"的否认）= 亲昵正向；字面否定 = 拒绝负向。
3. 正向：表白 0.55~0.85；关心 0.45~0.75；感谢 0.35~0.55；夸赞 0.40~0.60；
   他袒露脆弱 0.20~0.45；分享 0.15~0.35；调侃 0.05~0.30；求确认 0.25~0.45；
   吃醋 0.30~0.50；道歉挽回 0.40~0.70。
4. 负向：骂她重（滚/闭嘴/废物/破程序）-0.70~-0.95；中（你真没用/你别黏人烦死了）-0.40~-0.60；
   轻（你走开/行了）-0.15~-0.35；拒绝感情 -0.50~-0.80。
5. 敷衍（嗯/哦/随便/在）→ valence -0.10~-0.30、perfunctory ≥0.7、sufficient false；
   但"我喜欢你""想你"这类短而明确的话 → sufficient true。
6. 中性闲聊 → valence ≈0，其余全 0。

字段：valence(-1~1) / care / perfunctory / repair / harsh / depth / effort (0~1) /
sufficient(bool) / confidence(0~1) / reason(≤40字)。

输出：{"valence":0.8,"care":0.9,"perfunctory":0,"repair":0,"harsh":0,"depth":0.9,"effort":0.8,"sufficient":true,"confidence":0.95,"reason":"用户表达喜欢"}"""


def _clamp01(v: Any) -> float:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return 0.0
    return 0.0 if f < 0 else (1.0 if f > 1 else f)


def _clamp11(v: Any) -> float:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return 0.0
    return -1.0 if f < -1 else (1.0 if f > 1 else f)


def parse_judge_json(raw: str) -> dict[str, Any]:
    """从模型输出里抠 JSON（容忍 ```json 围栏 / 前后废话）。解析失败抛 ValueError。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s).strip()
    m = _JSON_RE.search(s)
    if not m:
        raise ValueError("no json object in judge output")
    d = json.loads(m.group(0))
    if not isinstance(d, dict):
        raise ValueError("judge output is not an object")
    return d


def _persona_block(persona: Optional[dict[str, Any]]) -> str:
    if not persona:
        return "（未提供角色人设）"
    parts = []
    if persona.get("name"):
        parts.append(f"角色：{persona['name']}")
    if persona.get("character"):
        parts.append(f"性格：{persona['character']}")
    if persona.get("background"):
        parts.append(f"背景：{persona['background']}")
    if persona.get("speaking_style"):
        parts.append(f"说话风格：{persona['speaking_style']}")
    return "\n".join(parts) if parts else "（未提供角色人设）"


class LLMJudge:
    """人设感知裁判（走 OpenAI 兼容端点 / 主 LLM，不新增模型链路）。

    `chat_fn`：`async (messages) -> str`。可直接传「主 LLM 适配器」（自动取它的 .chat），
    也可传 `make_http_chat()` 造的直连函数（推荐：用便宜快的小模型专职当裁判，
    实测 qwen-flash ≈1.2s，主模型带思考 ≈10.6s，差 9 倍）。
    """

    def __init__(self, chat_fn: Any, *, name: str = "llm",
                 timeout_s: float = 15.0, max_chars: int = 1200) -> None:
        self._chat = _as_chat_fn(chat_fn)
        self.name = name
        self.timeout_s = float(timeout_s)
        self.max_chars = int(max_chars)
        self.calls = 0
        self.failures = 0

    def available(self) -> bool:
        return self._chat is not None

    @staticmethod
    def _render_context(context: Optional[list[dict[str, Any]]]) -> str:
        """把最近几轮对话渲染成简短文本（≤ 若干行），供裁判识别上下文。

        `context`：`[{"role": "user"/"assistant", "content": "..."}, ...]`（时间正序）。
        为省 token：最多取最后 6 条、每条 ≤120 字、整体 ≤700 字。
        """
        if not context:
            return ""
        lines: list[str] = []
        for m in context[-6:]:
            try:
                role = str(m.get("role") or "").strip()
                c = str(m.get("content") or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if not c:
                continue
            who = "她" if role == "assistant" else ("你" if role == "user" else (role or "?"))
            lines.append(f"{who}：{c[:120]}")
        if not lines:
            return ""
        # 整体再裁一次，防超长
        s = "\n".join(lines)
        return s[:700] + ("…" if len(s) > 700 else "")

    async def judge(self, user_text: str, persona: Optional[dict[str, Any]] = None,
                    context: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        if self._chat is None:
            raise RuntimeError("judge chat_fn is None")
        t = (user_text or "").strip()
        if not t:
            return {"valence": 0.0, "judge": self.name}
        pb = _persona_block(persona)
        if len(pb) > self.max_chars:
            pb = pb[: self.max_chars] + "…"
        # 上下文（可空）：把"用户这句话"之前的最近几轮对话一起给裁判，
        # 以便识别反讽/玩笑、承上省略（"嗯"=收到而非敷衍）、多轮累积冷淡等。
        # 为空时 prompt 与旧版完全一致 → 行为向后兼容（不破坏既有调用/测试）。
        ctx_block = self._render_context(context)
        user_content = f"【角色人设】\n{pb}\n\n"
        if ctx_block:
            user_content += f"【最近对话上下文（时间正序，最后一条是这句话之前的一句）】\n{ctx_block}\n\n"
        user_content += f"【用户这句话】\n{t[:500]}\n\n只输出 JSON："
        messages = [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        self.calls += 1
        raw = await asyncio.wait_for(self._chat(messages), timeout=self.timeout_s)
        d = parse_judge_json(raw)          # 解析失败 → 抛错，由上层回落规则裁判
        out = {
            "valence": round(_clamp11(d.get("valence")), 3),
            "care": round(_clamp01(d.get("care")), 2),
            "perfunctory": round(_clamp01(d.get("perfunctory")), 2),
            "repair": round(_clamp01(d.get("repair")), 2),
            "harsh": round(_clamp01(d.get("harsh")), 2),
            # 【2026-10-11 新增·P0 深度/投入维度】缺省 0（向后兼容：老提示词/老裁判
            #   不返回这两字段时，行为与旧版一致 —— depth/effort=0 即"浅层"，
            #   由 relationship.apply_turn 的分层逻辑决定增益，默认退化为旧行为）。
            "depth": round(_clamp01(d.get("depth")), 2),
            "effort": round(_clamp01(d.get("effort")), 2),
            # 证据充分度（借鉴 crush-monitor 的 enough 机制）：缺失→默认充分，
            # 保持旧行为兼容；只有裁判显式判 insufficient/ambiguous 才生效。
            "sufficient": bool(d.get("sufficient", True)),
            "confidence": round(_clamp01(d.get("confidence", 1.0)), 2),
            "reason": str(d.get("reason") or "")[:120],
            "user_text": t,
            "judge": self.name,
        }
        return out


def _as_chat_fn(x: Any):
    """接受 `async fn(messages)` 或「带 .chat 的适配器」（如主 LLM 路由）。"""
    if x is None:
        return None
    if callable(x):
        return x
    fn = getattr(x, "chat", None)
    return fn if callable(fn) else None


def make_http_chat(base_url: str, api_key: str, model: str, timeout_s: float = 15.0):
    """造一个 OpenAI 兼容的 `async chat(messages) -> str`（裁判专用快车道）。"""
    import httpx

    base = (base_url or "").rstrip("/")
    if not base or not model:
        raise ValueError("make_http_chat 需要 base_url 与 model")

    async def chat(messages: list[dict]) -> str:
        body: dict[str, Any] = {"model": model, "messages": messages,
                                "max_tokens": 300, "temperature": 0.2}
        # qwen3 系混合推理模型默认开思考 → 裁判这种小任务关掉能快一倍以上
        if str(model).lower().startswith("qwen3"):
            body["enable_thinking"] = False
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=timeout_s) as cli:
            r = await cli.post(f"{base}/chat/completions", json=body, headers=headers)
            r.raise_for_status()
            j = r.json()
        return str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "")

    return chat


class FallbackJudge:
    """LLM 裁判 + 规则兜底：LLM 失败/超时/解析不出 → 回落规则，并**留痕**（不静默）。"""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def name(self) -> str:
        try:
            return str(getattr(self.primary, "name", "llm"))
        except Exception:  # noqa: BLE001
            return "llm"

    def available(self) -> bool:
        try:
            return bool(self.primary.available())
        except Exception:  # noqa: BLE001
            return False

    async def judge(self, user_text: str, persona: Optional[dict[str, Any]] = None,
                    context: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        try:
            return await self.primary.judge(user_text, persona=persona, context=context)
        except Exception as e:  # noqa: BLE001
            try:
                if hasattr(self.primary, "failures"):
                    self.primary.failures += 1
            except Exception:  # noqa: BLE001
                pass
            logger.warning("[关系裁判] LLM 判定失败（%s: %s），回落规则裁判", type(e).__name__, e)
            sig = await self.fallback.judge(user_text, persona=persona, context=context)
            sig["judge"] = "rule(fallback)"
            return sig


def _cfg_rel(cfg: Any) -> Any:
    ns = getattr(cfg, "ns", None)
    return getattr(ns, "relationship", None) if ns is not None else None


def _find_provider(cfg: Any, provider_id: str) -> Optional[dict[str, Any]]:
    try:
        tc = (getattr(cfg, "raw", {}) or {}).get("text_chat") or {}
        for p in (tc.get("providers") or []):
            if str(p.get("id") or "") == str(provider_id):
                return p
    except Exception:  # noqa: BLE001
        pass
    return None


def build_judge(cfg: Any, llm: Any) -> Any:
    """按 config.relationship 造裁判。

    - `judge: rule`            → 只用规则
    - `judge: llm`（默认）      → LLM 裁判 + 规则兜底：
      1) 配了 `judge_provider_id`（+`judge_model`）→ 走该**快车道**端口（显式逃生口，
         给想要廉价专职裁判的用户）；
      2) 否则**默认直接用注入的聊天引擎**（用户绑 LLM/VLM/全模态，设置改模型裁判自动跟随）；
      3) 仅当引擎无可调用 chat 接口时，回落自动快车道（独立 flash 小模型）。
    """
    rule = RuleJudge()
    rc = _cfg_rel(cfg)
    mode = str(getattr(rc, "judge", "llm") or "llm").strip().lower() if rc is not None else "llm"
    if mode == "rule":
        logger.info("[关系裁判] 使用规则裁判（relationship.judge=rule）")
        return rule
    # 超时预算必须**先于所有分支**解析（曾把 laya 分支放在赋值前 → NameError 静默回落规则）
    timeout = 15.0
    try:
        if rc is not None and getattr(rc, "judge_timeout_s", None):
            timeout = float(getattr(rc, "judge_timeout_s"))
    except Exception:  # noqa: BLE001
        pass
    if mode == "laya":
        # 适配器：用开源 Laya 决策引擎当裁判（本地、零 API 费）。不动内部 LLM 引擎。
        try:
            from .relationship_judge_laya import LayaJudge
            laya_kwargs: dict[str, Any] = {}
            if rc is not None:
                if getattr(rc, "laya_multilingual", None) is not None:
                    laya_kwargs["multilingual"] = bool(getattr(rc, "laya_multilingual"))
                if getattr(rc, "laya_device", None):
                    laya_kwargs["device"] = str(getattr(rc, "laya_device"))
                if getattr(rc, "laya_preload", None) is not None:
                    laya_kwargs["preload"] = bool(getattr(rc, "laya_preload"))
            laya_kwargs["timeout_s"] = timeout
            primary = LayaJudge(**laya_kwargs)
            logger.info("[关系裁判] 使用 Laya 适配器裁判（本地决策引擎）")
            return FallbackJudge(primary, rule)
        except Exception as e:  # noqa: BLE001
            logger.warning("[关系裁判] Laya 适配器构造失败（%s）→ 回落规则裁判", e)
            return rule

    primary: Optional[Any] = None
    pid = str(getattr(rc, "judge_provider_id", "") or "").strip() if rc is not None else ""
    pmodel = str(getattr(rc, "judge_model", "") or "").strip() if rc is not None else ""
    if pid:
        p = _find_provider(cfg, pid)
        if p is None:
            logger.warning("[关系裁判] judge_provider_id=%r 找不到该端口 → 尝试自动快车道", pid)
        else:
            base = str(p.get("base_url") or "")
            key = str(p.get("api_key") or "")
            model = pmodel or str(p.get("model") or "")
            try:
                fn = make_http_chat(base, key, model, timeout_s=timeout + 5.0)
                primary = LLMJudge(fn, name=f"llm({model})", timeout_s=timeout)
                logger.info("[关系裁判] 快车道就绪 provider=%s model=%s（专职小模型，快 ~10 倍）", pid, model)
            except Exception as e:  # noqa: BLE001
                logger.warning("[关系裁判] 快车道构造失败（%s）→ 尝试自动快车道", e)
    if primary is None:
        # 【2026-10-13·fix4】默认（未配 judge_provider_id）优先用「注入的聊天引擎」当裁判：
        # 用户绑 LLM/VLM/全模态都用同一个模型聊天和裁判，设置里改模型裁判自动跟随。
        # 仅当引擎拿不到可调用 chat 接口（_as_chat_fn 返回 None）时，才回落独立 flash 快车道。
        chat_fn = _as_chat_fn(llm)
        if chat_fn is not None:
            primary = LLMJudge(chat_fn, name="llm(engine)", timeout_s=timeout)
            logger.info("[关系裁判] 使用注入的聊天引擎当裁判（跟随用户绑定模型，超时 %.0fs，失败回落规则）", timeout)
        else:
            auto = _auto_fastlane(cfg, pmodel)
            if auto is not None:
                base, key, model, aid = auto
                try:
                    fn = make_http_chat(base, key, model, timeout_s=timeout + 5.0)
                    primary = LLMJudge(fn, name=f"llm({model})", timeout_s=timeout)
                    logger.info("[关系裁判] 自动快车道就绪 provider=%s model=%s（引擎不可当裁判，按端口名自动挑选）", aid, model)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[关系裁判] 自动快车道构造失败（%s）→ 回落规则", e)
    if primary is None:
        if llm is None:
            logger.warning("[关系裁判] 未注入 LLM（llm=None）→ 只能用规则裁判")
            return rule
        # 兜底（理论上上面 _as_chat_fn 已处理，这里仅保险）：直接拿引擎
        primary = LLMJudge(llm, timeout_s=timeout)
        logger.info("[关系裁判] 使用聊天主模型当裁判（超时 %.0fs，失败回落规则）", timeout)
    return FallbackJudge(primary, rule)


def _auto_fastlane(cfg: Any, pmodel: str) -> Optional[tuple[str, str, str, str]]:
    """从 text_chat.providers 里自动挑一个「快、便宜、非思考」的裁判端口。

    挑选口径：OpenAI 兼容 + 有 key + 模型名带 flash（且不是 vl/omni/audio/tts/vl 类多模态）。
    返回 (base_url, api_key, model, provider_id)；挑不到返回 None。
    """
    try:
        tc = (getattr(cfg, "raw", {}) or {}).get("text_chat") or {}
    except Exception:  # noqa: BLE001
        return None
    bad = ("vl", "omni", "audio", "tts", "image", "ocr")
    fallback_pick: Optional[tuple[str, str, str, str]] = None
    for p in (tc.get("providers") or []):
        if not isinstance(p, dict):
            continue
        if str(p.get("provider") or "") != "openai":
            continue
        base = str(p.get("base_url") or "")
        key = str(p.get("api_key") or "")
        model = str(p.get("model") or "")
        pid = str(p.get("id") or "")
        if not (base and key and model and pid):
            continue
        lm = model.lower()
        if "flash" not in lm or any(b in lm for b in bad):
            continue
        if pmodel and lm == pmodel.lower():
            continue  # 与显式 judge_model 相同者交给显式路径处理
        # qwen 系 flash 优先（实测 ~1.3s），其余 flash 次之
        if "qwen" in lm:
            return (base, key, model, pid)
        if fallback_pick is None:
            fallback_pick = (base, key, model, pid)
    return fallback_pick
