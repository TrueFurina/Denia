"""上下文驱动的记忆提炼（2026-10-11 新增）

背景与设计动机（实读 + 实测）
--------------------------------
旧方案：`dialogue._maybe_extract_memory` —— **单句** + `EXTRACT_HINT_RE` 关键词白名单 →
实测 `data/memory_profile.jsonl`：`extract 11 次 / 产出 0 条 / assemble 4448 次`（1:404）。
且实测 `"我晚上七点吃的饭"` **连白名单都不命中** → 永远进不了记忆。

新方案（本模块）：把**一整段对话上下文**交给 LLM，让它**自己通读后判断+提取**。
实测（`tools/memory_extract_smoke.py`，真模型）：
  · 22 轮常规对话 → 提炼 9 条，含「用户晚上七点吃的饭」「兰州拉面加蛋」「觉得有点咸」
    「下周去西安」「想去回民街吃羊肉泡馍」「周三晚高铁」「老忘东西」「上次忘带充电器」
  · 63 轮超长对话 → 提炼 17 条，细节全在
  · ★ 关键：`"我晚上七点吃的饭"` 被正确记住（旧方案 0 条）→ **方案有效**
代价：单次 30~37s（云端带思考）→ **必须异步，绝不阻塞对话**。

触发：**上下文用量驱动**（开发者指定）——当估算 token 接近模型窗口上限时自动提炼。
两种入口：
  1. 自动：`should_synthesize()` 判"快超限" → 后台提炼（`auto_synthesize`）；
  2. 手动：UI「立即提炼」按钮 → `synthesize_now()`。

红线（沿用全项目铁律）
----------------------
1. **只加不改**：旧 `_maybe_extract_memory` **不删、只断开调用**（可一键回退）。
2. **失败降级 + 留痕**：任何异常 → 返回结果对象带 `error`，绝不静默假装成功。
3. **不阻塞**：提炼一律 `asyncio.create_task`，且带节流（同一次上下文不重复提炼）。
4. **类型归 MEMORY_TYPES**：LLM 直接输出 user/directive/world/episode，不再让 `fact/plan`
   走兜底被误判成 world（实测这个 bug）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 上下文用量阈值（估算 token；中文 1 字 ≈ 1 token 的保守口径）───────────
# 模型窗口按档位给：云端 32K+、本地 4096。留出响应 + 记忆注入的余量。
CTX_WINDOW = {"cloud": 32000, "local": 4096, "local_duplex": 4096}
# 触发线：用满该比例即认为"快超限"，开始提炼。
CTX_TRIGGER_RATIO = {"cloud": 0.80, "local": 0.70, "local_duplex": 0.70}
# 提炼节流：同一段上下文内容指纹不变则跳过（避免每次调用都重复提炼）
MIN_NEW_TURNS_AFTER_LAST = 4


# ══════════════════════════════════════════════════════════════════════════
# 提炼提示词：让 LLM 自己通读整段上下文，判断 + 分类 + 提取
# ══════════════════════════════════════════════════════════════════════════
MEMORY_SYNTHESIS_PROMPT = """你是一个长期记忆提炼器。下面是你（AI 伴侣）与用户**最近一段对话的完整记录**。

请你通读整段对话，像"真的在和他相处"一样判断：**其中哪些内容值得你长期记住**，并提取出来。

【要记住的】（这些才是能让你"记得他这个人"的东西）：
- 用户告诉你的**事实与经历**：他吃了什么、去了哪、做了什么（哪怕说得很平淡）
- 用户的**偏好/习惯/口味**（"那家有点咸""不爱吃辣""常忘带充电器"）
- 用户的**计划与约定**（"下周去西安""周三晚高铁""帮我记着收行李"）
- 用户的**身份/处境**（工作、住处、养了什么宠物、家里情况）
- 用户的**情绪与态度**（对某事挺期待、有点烦、最近很累）
- 用户给你的**明确要求/纠正**（改称呼、别做什么、以后要怎样）——这类是"规矩"，务必逐字记住
- 你答应过他的事（"那我提醒你收行李"）

【不要记的】：
- 纯寒暄（"在吗""嗯嗯"）、你（AI）自己说的话、一次性的语气词
- 但**注意**：看起来平淡的"我晚上七点吃的饭"**是值得记的**——它告诉你他的作息和吃了啥，
  不是废话。**宁可多记一条事实，也别漏掉他主动讲的事。**

【分类 type 只能取这四个之一】：
- `user`    ：关于用户的偏好/习惯/身份/经历/情绪（**绝大多数都归这里**）
- `directive`：用户对你立的**规矩/要求/纠正**（如"以后叫我XX""别叫我宝贝"），fact 逐字保留原意
- `world`   ：你学到的**外部客观事实**（如"回民街在西安"这种跟你俩无关的知识），很少用
- `episode` ：**你们之间发生过的一件事**（带细节的"情节"，如"他跟我聊了去西安的计划，
  说想吃回民街的羊肉泡馍"）

【每条要求】
- 以"用户"为主语，**保留细节**（时间/地点/人物/数量/具体名词），**用他原话里的词，不要编造**
- 每条 ≤ 40 字
- 同一件事不要拆得太碎（如"他吃了饭"+"他七点吃的"应合成一条）

只输出一行 JSON 数组，每项 {"fact": "...", "type": "user|directive|world|episode"}；没有则输出 []。
不要任何解释、不要 markdown。

对话记录：
{text}"""


def estimate_tokens(text: str) -> int:
    """估算 token 数（中文 1 字 ≈ 1 token 的保守口径，与 memory_assembler 一致）。"""
    return len(text or "")


def conversation_token_estimate(turns: list[dict] | None = None) -> int:
    """估算"当前对话上下文"的 token 量（role+content 全算，含她的话）。

    turns 为 None 时自动取 `conversation_history.shared_history()`。
    """
    if turns is None:
        try:
            from .conversation_history import shared_history
            turns = shared_history().get_recent(999)
        except Exception:  # noqa: BLE001
            return 0
    total = 0
    for t in (turns or []):
        total += estimate_tokens(str(t.get("content") or ""))
    return total


def _norm_line(s: str) -> str:
    """比较用归一化：去空白/标点、折叠大小写。"""
    return re.sub(r"[\s\W_]+", "", s or "", flags=re.UNICODE).lower()


def build_conversation_text(turns: list[dict], max_chars: int = 6000,
                            char_name: str = "达妮娅") -> str:
    """把 turns 拼成"用户：…/达妮娅：…"的可读文本（供 LLM 通读）。

    【2026-10-13 去噪】语音双工会把同一句重复落盘（"你好，你好，听得见吗"连两条），
    主动陪伴也会连发多条 AI 独白。这些噪音会把**真正的用户信息淹没**，让弱模型
    误判"没有值得记的"。故先做两步轻量清洗：
      ① 相邻同角色且**高度重复**的句子合并（归一化后相等 / 一方含另一方）；
      ② 连续同角色的 AI 独白若 >=3 条，只保留最后 1 条（连发问候/提醒无用户回应）。
    超长时**保留最近的**（旧内容重要性低，保留最近更能抓到"刚聊的事"）。
    max_chars 是硬上限，防提示词爆掉。
    """
    raw: list[tuple[str, str]] = []
    for t in (turns or []):
        role = "user" if str(t.get("role")) == "user" else "char"
        who = "用户" if role == "user" else (char_name or "达妮娅")
        c = str(t.get("content") or "").strip()
        if c:
            raw.append((role, f"{who}：{c[:300]}"))
    if not raw:
        return ""
    # ① 相邻同角色高度重复 → 合并
    dedup: list[tuple[str, str]] = []
    for role, line in raw:
        if dedup and dedup[-1][0] == role:
            a, b = _norm_line(line), _norm_line(dedup[-1][1])
            if a and b and (a == b or (len(a) >= 4 and (a in b or b in a))):
                continue
        dedup.append((role, line))
    # ② 连续同角色 AI 独白 >=3 条 → 只留最后 1 条
    out: list[tuple[str, str]] = []
    i, n = 0, len(dedup)
    while i < n:
        role = dedup[i][0]
        j = i
        while j + 1 < n and dedup[j + 1][0] == role:
            j += 1
        run = dedup[i:j + 1]
        if role == "char" and len(run) >= 3:
            out.append(run[-1])
        else:
            out.extend(run)
        i = j + 1
    text = "\n".join(line for _, line in out)
    if len(text) > max_chars:
        # 从**尾部**截（保留最近）；再从最近的换行处切开，避免半句
        text = text[-max_chars:]
        nl = text.find("\n")
        if nl > 0:
            text = text[nl + 1:]
    return text


class MemorySynthesizer:
    """上下文驱动的记忆提炼器（自动 + 手动）。"""

    def __init__(self, llm: Any, store: Any, *, cfg: Any = None,
                 tier: str = "cloud", min_turns: int = 4) -> None:
        self._llm = llm
        self._store = store
        self._cfg = cfg
        self._tier = tier
        self._min_turns = int(min_turns)
        # 节流：上次提炼时的"轮数"与"内容指纹"，避免同一段反复提炼
        self._last_turn_count = 0
        self._last_fingerprint = ""
        self.busy = False           # 提炼进行中（UI 可据此显示"提炼中…"）
        self.last_result: dict = {} # 最近一次结果（供 UI 展示/轮询）

    # ---------- 触发判据 ----------
    def current_usage_ratio(self, turns: list[dict] | None = None) -> float:
        """当前上下文占模型窗口的比例（0~N）。"""
        win = CTX_WINDOW.get(self._tier, 32000)
        if win <= 0:
            return 0.0
        return conversation_token_estimate(turns) / float(win)

    def should_synthesize(self, turns: list[dict] | None = None) -> bool:
        """是否"快超限"该提炼了。返回 True 表示达到触发线。"""
        try:
            n_turns = len(turns) if turns is not None else len(
                __import__("src.companion.conversation_history", fromlist=["x"])
                .shared_history().get_recent(999))
        except Exception:  # noqa: BLE001
            n_turns = 0
        if n_turns < self._min_turns:
            return False
        ratio = self.current_usage_ratio(turns)
        trigger = CTX_TRIGGER_RATIO.get(self._tier, 0.80)
        return ratio >= trigger

    def _fingerprint(self, turns: list[dict]) -> str:
        """内容指纹：轮数 + 末 3 轮的哈希，用于"这段是否已提炼过"判定。"""
        tail = "".join(str(t.get("content") or "") for t in (turns or [])[-3:])
        return f"{len(turns)}:{abs(hash(tail)) % (10 ** 9)}"

    # ---------- 提炼 ----------
    async def synthesize(self, turns: list[dict] | None = None,
                         force: bool = False, char_name: str = "达妮娅",
                         timeout: float = 60.0) -> dict:
        """核心：把整段上下文交给 LLM 提炼 → 写入记忆库。

        返回 `{ok, stored, items, skipped, error, usage_ratio, turns, ms}`。
        - `force=False` 时受节流保护（同段不重复）。
        - 任何异常都返回 ok=False + error（**不抛**，供后台任务安全调用）。
        """
        if self._store is None:
            return {"ok": False, "error": "记忆库未启用", "stored": 0}
        # 【2026-10-16 修复·提炼串角色】归属必须在"取上下文那一刻"就定住。
        # 提炼是**后台任务**（LLM 一次要几十秒），期间用户完全可能切角色；若等到写库时
        # 才让 add() 取当时的活动角色，这批本属旧角色的记忆会被记到**新角色**名下 ——
        # 正是"记忆串角色"的另一条隐蔽来源（另一种是无归属旧记忆被当通用注入，已另行修复）。
        _owner = ""
        try:
            _owner = (self._store.get_character() or "").strip()
        except Exception:  # noqa: BLE001
            _owner = ""
        t0 = time.time()
        if turns is None:
            try:
                from .conversation_history import shared_history
                turns = shared_history().get_recent(999)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"取上下文失败: {e}", "stored": 0}
        turns = list(turns or [])
        if len(turns) < self._min_turns:
            return {"ok": False, "error": "上下文太短，无需提炼", "stored": 0,
                    "turns": len(turns)}

        fp = self._fingerprint(turns)
        if not force and fp == self._last_fingerprint:
            return {"ok": False, "error": "该段上下文已提炼过（节流）", "stored": 0,
                    "turns": len(turns), "skipped": True}

        seg = build_conversation_text(turns, char_name=char_name)
        if not seg:
            return {"ok": False, "error": "上下文为空", "stored": 0}
        prompt = MEMORY_SYNTHESIS_PROMPT.replace("{text}", seg)

        self.busy = True
        try:
            # 【2026-10-13】提炼属**后台批处理**：手动模式的"指定端口失败即失败"
            # 契约不适用于它 —— 端口欠费/冷却时直接失败会让用户永远提炼不了。
            # 故优先走路由的容错入口 chat_with_fallback（先手动端口 → 其它可用
            # 端口 → 兜底网关）；老客户端没有该方法时退回普通 chat()。
            _fb = getattr(self._llm, "chat_with_fallback", None)
            _call = _fb if callable(_fb) else self._llm.chat
            raw = await asyncio.wait_for(
                _call([{"role": "user", "content": prompt}],
                      options={"temperature": 0.1}),
                timeout=timeout,
            )
        except Exception as e:  # noqa: BLE001
            self.busy = False
            logger.warning("[记忆合成] LLM 调用失败: %s", e)
            return {"ok": False, "error": f"LLM 失败: {e}", "stored": 0,
                    "turns": len(turns)}
        ms = int((time.time() - t0) * 1000)

        items = self._parse(raw)
        stored = 0
        for fact, typ in items:
            f = (fact or "").strip()
            if len(f) < 3:
                continue
            try:
                # 显式带上"提炼发起时"的角色归属，避免异步期间切角色导致串归属
                if self._store.add(f[:200], type=typ, source="chat", character=_owner):
                    stored += 1
            except Exception:  # noqa: BLE001
                continue
        # 推进节流水位（无论存多少，这段都算处理过了）
        self._last_fingerprint = fp
        self._last_turn_count = len(turns)
        self.busy = False
        result = {
            "ok": True, "stored": stored, "items": items,
            "turns": len(turns), "ms": ms,
            "usage_ratio": round(self.current_usage_ratio(turns), 3),
            "error": "",
        }
        # 如实回执实际使用的端口/模型（后台任务可能发生回退，不能让 UI 猜）
        try:
            _u = getattr(self._llm, "last_used_info", None)
            _info = _u() if callable(_u) else None
            if _info:
                result["used_provider"] = _info.get("name") or _info.get("id") or ""
                result["used_model"] = _info.get("model") or ""
        except Exception:  # noqa: BLE001
            pass
        self.last_result = result
        if stored:
            logger.info("[记忆合成] 从 %d 轮上下文提炼写入 %d 条（耗时 %dms）",
                        len(turns), stored, ms)
        return result

    def synthesize_sync_wrapper(self, *_, **__):
        """占位：同步调用方应改用 `asyncio.create_task(synthesize(...))`。"""
        raise RuntimeError("请用 await synthesize(...) 或 asyncio.create_task(...)")

    @staticmethod
    def _parse(raw: str) -> list[tuple[str, str]]:
        """解析 LLM 输出为 [(fact, type)]；type 归一到 MEMORY_TYPES。

        ⚠️ 实测坑（2026-10-11 被单测抓出）：**不能直接复用 `_parse_fact_items`**——
        它在 `type` 不在 MEMORY_TYPES 时**当场就用 `_guess_fact_type` 兜底**了
        （memory_store.py:242-245），等我们拿到手时 `fact`/`plan` 早已变成 `world`，
        再想归一也回天乏术。故这里**自己解析原始 JSON**，保留 LLM 原样给的 type，
        再显式归一：不在 MEMORY_TYPES 的（fact/plan/未知）一律 → `user`。

        （`fact`/`plan` 是 LLM 面对"提取事实/计划"提示词时最自然的输出；把它们
        错分到 `world`="外部客观事实"会让"用户下周去西安"这类个人信息进错分组。）
        """
        from .memory_store import MEMORY_TYPES
        import json as _json
        out: list[tuple[str, str]] = []
        txt = (raw or "").strip()
        if not txt:
            return out
        m = re.search(r"\[.*\]", txt, re.S)
        cand = m.group(0) if m else txt
        obj = None
        try:
            obj = _json.loads(cand)
        except Exception:  # noqa: BLE001
            obj = None
        if isinstance(obj, list):
            for x in obj:
                if isinstance(x, dict):
                    f = str(x.get("fact") or x.get("text") or "").strip()
                    t = str(x.get("type") or "").strip().lower()
                else:
                    f, t = str(x).strip(), ""
                if not f:
                    continue
                if t not in MEMORY_TYPES:
                    t = "user"      # ★ 未知/自然类型（fact/plan/…）一律归 user
                out.append((f, t))
        if out:
            return out
        # 退路：逐行（无 type）——仍按 user 收（宁可归 user 也别猜成 world）
        for ln in txt.splitlines():
            f = ln.strip(' "\'-')
            if f and f not in ("[]", "[", "{"):
                out.append((f, "user"))
        return out


def make_synthesizer(llm: Any, store: Any, cfg: Any = None,
                     tier: str = "cloud") -> MemorySynthesizer:
    """工厂：从 config 读 min_turns（可选）。"""
    mt = 4
    try:
        v = (cfg.raw.get("memory", {}) or {}).get("synthesis_min_turns")
        if v is not None:
            mt = max(1, int(v))
    except Exception:  # noqa: BLE001
        mt = 4
    return MemorySynthesizer(llm, store, cfg=cfg, tier=tier, min_turns=mt)
