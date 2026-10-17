"""关系状态接入层 —— 订阅事件总线，把对话喂给关系状态机 + 历史落盘 + 在线时钟。

边界：
- 只 `bus.subscribe(...)`；不改 `dialogue.py` / `proactive.py` / `prompts.py`；
- 不 import 任何引擎适配器；**只用调用方注入进来的 `llm` 做「关系裁判」**（见 `relationship_judge.py`）；
- 事件处理里做的是"落历史 + 更新关系状态 + 打一行日志"；判定**异步执行**（`create_task`），
  **绝不阻塞事件总线**（总线串行，await 会拖慢她的回复上屏）；
- 出异常由事件总线兜底记录（`logger.exception`），**不会静默、也不会打断对话**。

它消费的事件（都是现成的，无需新增发布点）：
- `USER_TEXT`        → 用户说了什么
- `AI_DECISION`      → 她回了什么（含 emotion / is_proactive / streamed / source）
- `CHARACTER_SWITCHED` → 换了角色（{char_id, char_name}），切档并补算

⚠️ `AI_DECISION` 的 `streamed=True` 是**流式回复的最终整句**（正常路径，见 dialogue.py:1105），
   **不是半句分片**（分片走 `PARTIAL_TEXT`）。曾误把它当分片过滤 ⇒ 打字聊天从不判定。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Optional

from ..event_bus import Event, EventBus, EventType
from .history_store import HistoryStore
from .relationship import RelationshipManager, ARCHETYPES, get_relationship_manager
from .relationship_judge import build_judge, _as_chat_fn, parse_judge_json
from .session_clock import SessionClock

logger = logging.getLogger(__name__)

# 只统计"聊天"来源。双工自言自语（source='proactive'）走独立通话记录，不进聊天关系。
_CHAT_SOURCES = ("chat", "dialogue")

# 从三段式人设反推人格原型的提示词（§4.3 / §16.3）。原型不改人设，只决定"同一种好感用哪种脾气表达"。
_INFER_ARCHETYPE_SYSTEM = (
    "你是「人设分析师」。根据角色的【性格 / 说话风格 / 背景】，判断她最像下面哪种人格原型（只选一个）："
    + "、".join(ARCHETYPES.keys()) + "。"
    "只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块："
    '{"archetype": "原型名", "reason": "一句中文理由，≤30字"}'
)


async def _infer_archetype(persona: dict, chat_fn: Any) -> str:
    """用 LLM 从三段式人设一次性反推原型（§4.3）。失败/解析不出 → 返回空串（沿用默认温柔）。"""
    if not chat_fn or not isinstance(persona, dict):
        return ""
    user = (f"【性格】{persona.get('character', '')}\n"
            f"【说话风格】{persona.get('speaking_style', '')}\n"
            f"【背景】{persona.get('background', '')}")
    try:
        raw = await asyncio.wait_for(
            chat_fn([
                {"role": "system", "content": _INFER_ARCHETYPE_SYSTEM},
                {"role": "user", "content": user},
            ]),
            timeout=15.0,
        )
    except Exception:  # noqa: BLE001
        return ""
    # 先按 JSON 解析
    try:
        d = parse_judge_json(raw)
        arch = str(d.get("archetype") or "").strip()
        if arch in ARCHETYPES:
            return arch
    except Exception:  # noqa: BLE001
        pass
    # 容错：从原文里抠一个合法原型名
    for k in ARCHETYPES:
        if k in (raw or ""):
            return k
    return ""

# 规则裁判已抽到 relationship_judge.py（规则兜底 + LLM 人设感知深判）。
# 这里保留 `_heuristic_signals` 别名，兼容既有测试/外部调用。
from .relationship_judge import heuristic_signals as _heuristic_signals  # noqa: E402,F401


# ---------------------------------------------------------------------- 接入层

class RelationshipReporter:
    """把事件总线上的对话，喂给关系状态机 + 历史落盘 + 在线时钟。"""

    def __init__(self, bus: EventBus, cfg: Any,
                 *, enabled: Optional[bool] = None,
                 base_dir: Optional[Any] = None,
                 llm: Optional[Any] = None,
                 memory_store: Optional[Any] = None) -> None:
        self._bus = bus
        self._cfg = cfg
        self._llm = llm                      # 只用于「关系裁判」（见 relationship_judge.py）
        # 【2026-10-11】里程碑"发生即直写记忆"用的**共享** MemoryStore（与 dialogue 同一个
        #   实例）。**绝不在此另开一个 handle 指向 data/memory.json** —— 两个写者各自
        #   load→改→save 会静默丢更新（本项目已知坑）。故由 main.py 注入；未注入时
        #   里程碑只写对话历史、不直写记忆（降级，不制造第二写者）。
        self._memory_store = memory_store
        self._judge: Any = None
        # 与对话链路共用同一个 RelationshipManager 单例（同一批数据文件，避免双实例竞写）
        self._mgr = get_relationship_manager(base_dir)
        self._history = HistoryStore(base_dir)
        self._clock = SessionClock(base_dir)
        self._char_id = ""
        # P7 配对修复（2026-10-10）：用户话从"单槽"改为 FIFO 队列。
        #   旧实现单槽会在用户**连发两句**时覆盖前一句（前一句永远配不上回复 → 白丢一轮判定）；
        #   队列则按顺序"一问配一答"逐条消费，连发也不丢。上限防内存无限增长。
        self._pending_user_queue: deque[str] = deque(maxlen=8)
        self._started = False
        self._enabled_override = enabled
        self._last_ai_text = ""      # 重复投递去重（同一句只记一次）
        self._last_ai_ts = 0.0
        self._tasks: set[Any] = set()        # 在途判定任务（防被 GC）
        # 把注入的 llm 包成 `async (messages) -> str`，供「原型自动反推」复用（§4.3/§16.3）
        self._chat_fn: Any = None
        try:
            self._chat_fn = _as_chat_fn(llm)
        except Exception:  # noqa: BLE001
            self._chat_fn = None

    # ---- 开关：config 里 `relationship.enabled: false` 可整体关掉（不写则默认开） ----
    @property
    def enabled(self) -> bool:
        if self._enabled_override is not None:
            return bool(self._enabled_override)
        try:
            ns = getattr(self._cfg, "ns", None)
            rc = getattr(ns, "relationship", None) if ns is not None else None
            if rc is None:
                return True
            return bool(getattr(rc, "enabled", True))
        except Exception:  # noqa: BLE001
            return True

    # ---- 生命周期 ----

    def subscribe(self) -> None:
        self._bus.subscribe(EventType.USER_TEXT, self._on_user_text)
        self._bus.subscribe(EventType.AI_DECISION, self._on_ai_decision)
        self._bus.subscribe(EventType.CHARACTER_SWITCHED, self._on_char_switched)

    def start(self) -> None:
        """启动时调用：收尾上次残留会话 + 开机补算 + 打一行"恢复"日志。"""
        if not self.enabled:
            logger.info("[关系] 未启用（relationship.enabled=false），跳过")
            return
        self._apply_cfg_policy()
        try:
            self._judge = build_judge(self._cfg, self._llm)
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 构造裁判失败（回落默认规则裁判）")
            from .relationship_judge import RuleJudge
            self._judge = RuleJudge()
        try:
            self._clock.start()
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 会话时钟启动失败")
        self._char_id = self._resolve_char_id()
        try:
            st = self._mgr.tick(self._char_id)
            self._log_state(st, "恢复")
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 开机补算失败")
        # §4.3/§16.3：原型未设时，用 LLM 从三段式人设一次性反推（缓存、可手改）
        self._maybe_infer_archetype(self._char_id)
        self._started = True

    def set_llm(self, new_llm: Any) -> None:
        """【2026-10-13·fix4】热切换：用新聊天引擎重建关系裁判，使其跟随用户设置。
        调用方：webapp._apply_llm_live()。失败则保留旧裁判（不静默）。"""
        self._llm = new_llm
        try:
            self._chat_fn = _as_chat_fn(new_llm)
        except Exception:  # noqa: BLE001
            self._chat_fn = None
        try:
            self._judge = build_judge(self._cfg, new_llm)
            logger.info("[关系] 裁判已随聊天引擎热切换重建")
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 热切换重建裁判失败（保留旧裁判）")

    def _apply_cfg_policy(self) -> None:
        """config.relationship.newbie_turns / newbie_days 覆盖新手期窗口（实测用）。"""
        ns = getattr(self._cfg, "ns", None)
        rc = getattr(ns, "relationship", None) if ns is not None else None
        if rc is None:
            return
        turns = getattr(rc, "newbie_turns", None)
        days = getattr(rc, "newbie_days", None)
        if turns is None and days is None:
            return
        self._mgr.set_newbie_policy(turns, days)
        logger.info("[关系] 新手期窗口已按 config 覆盖：turns=%s days=%s",
                    self._mgr.engine.newbie_turns, self._mgr.engine.newbie_days)

    def stop(self) -> None:
        """退出时调用：收尾在线会话。"""
        try:
            self._clock.stop()
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 会话时钟收尾失败")

    # ---- 事件 ----

    async def _on_user_text(self, e: Event) -> None:
        if not self.enabled:
            return
        try:
            self._clock.touch()   # 心跳：让"在线时段/熬夜"真的算得出来
        except Exception:  # noqa: BLE001
            logger.debug("[关系] 会话时钟心跳失败", exc_info=True)
        d = e.data if isinstance(e.data, dict) else {}
        text = str(d.get("text") or "").strip()
        if not text:
            return
        cid = self._resolve_char_id()
        src = str(d.get("source") or e.source or "chat")
        self._history.append_turn(cid, "user", text, source=src)
        if src == "duplex":
            # 【2026-10-13·fix2】双工用户话：直接判（不依赖 AI 回复、不入聊天配对队列，
            # 避免污染 chat 的 FIFO 队列导致后续聊天句错配）。AI 回复(source='proactive')被
            # _on_ai_decision 的 _CHAT_SOURCES 过滤排除，不会二次判。
            self._spawn_judge(cid, text)
            return
        self._pending_user_queue.append(text)

    async def _on_ai_decision(self, e: Event) -> None:
        if not self.enabled:
            return
        d = e.data if isinstance(e.data, dict) else {}
        text = str(d.get("text") or "").strip()
        # 失败气泡 / 空串 / 被质量门拦截 → 不算"她真的说了一句"，不入历史也不配对。
        # ⚠️ **绝不能过滤 streamed=True**：那是"流式回复的最终整句"的**正常**标志
        #    （dialogue.py:1105「句子已逐句播放，TTS 不再整段重播」）；真正的半句走
        #    EventType.PARTIAL_TEXT（→ webapp 的打字机），根本不进这里。
        #    曾因把 streamed 当分片过滤 ⇒ **打字聊天整条链路从不判定**（2026-10-10 实测炸出）。
        if not text or d.get("blocked") or d.get("failed"):
            return
        src = str(d.get("source") or e.source or "chat")
        # 非聊天来源（双工自言自语 source='proactive'、通话等）不进聊天历史、不动关系状态
        if src not in _CHAT_SOURCES:
            return
        # 重复投递去重（同一句 5s 内只记一次）：防重试/双路径把一轮记成两轮
        _now = time.time()
        if text == self._last_ai_text and (_now - self._last_ai_ts) < 5.0:
            return
        self._last_ai_text = text
        self._last_ai_ts = _now
        try:
            self._clock.touch()
        except Exception:  # noqa: BLE001
            logger.debug("[关系] 会话时钟心跳失败", exc_info=True)
        cid = self._resolve_char_id()
        emotion = str(d.get("emotion") or "")
        self._history.append_turn(cid, "assistant", text, source=src, emotion=emotion)

        if d.get("is_proactive"):
            # 她主动开口（聊天页的主动发言）→ 记一笔，等用户是否回应（P2 才用它扣/不扣）
            # ⚠️ 不清空用户 pending 队列：主动发言不是对"用户某句话"的回复，
            #    若此时把队列清掉，用户之前那句真话会永远配不上她的正式回复（丢一轮）。
            try:
                self._mgr.mark_initiative(cid)
            except Exception:  # noqa: BLE001
                logger.exception("[关系] 记录主动开口失败")
            return

        # 一问配一答：从队列头部取"最早那句还没被回复的用户话"。
        # FIFO 保证连发多句也按顺序各配一次，不再互相覆盖。
        if not self._pending_user_queue:
            # 没有对应的用户那句话（例如历史回放 / 语音路径）→ 只记历史，不动状态
            return
        ut = self._pending_user_queue.popleft()
        if not ut:
            return
        # 判定**异步执行**：事件总线是串行派发，若在这里 await LLM（2~5s），
        # 会把"她的回复上屏/TTS"也拖后同样久。所以先把 pending 同步消费掉，
        # 再由后台任务完成判定 → 更新状态 → 打日志（面板随后由轮询补上）。
        self._spawn_judge(cid, ut)

    # ---- 判定（异步） ----

    def _spawn_judge(self, cid: str, ut: str) -> None:
        try:
            t = asyncio.create_task(self._judge_and_apply(cid, ut))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 判定任务创建失败（本轮按规则兜底，同步应用）")
            try:
                self._mgr.mark_response(cid)
                st = self._mgr.on_turn(cid, _heuristic_signals(ut))
                self._log_state(st, "更新", signals={"valence": "?"})
            except Exception:  # noqa: BLE001
                logger.exception("[关系] 同步兜底判定也失败")

    def _persona_for(self, cid: str) -> dict[str, Any]:
        """取角色三段式人设（裁判要带人设才能判"踩雷"）。"""
        try:
            ch = None
            try:
                ch = self._cfg.get_character_by_id(cid)
            except Exception:  # noqa: BLE001
                ch = None
            if ch is None:
                ch = self._cfg.get_active_character()
        except Exception:  # noqa: BLE001
            ch = None
        if ch is None:
            return {}
        return {
            "name": str(getattr(ch, "name", "") or ""),
            "character": str(getattr(ch, "character", "") or ""),
            "background": str(getattr(ch, "background", "") or ""),
            "speaking_style": str(getattr(ch, "speaking_style", "") or ""),
        }

    # ---- 原型自动反推（§4.3 / §16.3）----
    def _maybe_infer_archetype(self, cid: str) -> None:
        """原型还没设过（空串）才反推；已设（手填或已反推）跳过。后台异步、不阻塞。

        【2026-10-10 修·静默失败】此前 create_task 无运行 loop 时（如从纯同步上下文
        调用）会造出**永不 await 的协程**（RuntimeWarning）且被 except 静默吞掉，
        表现为"原型反推从未发生、日志里毫无痕迹"（线上实测 turns=16 但 archetype 恒空）。
        现改为：无运行 loop 时直接记 WARNING（不静默），有 loop 才建任务；
        且成功/失败都留痕（成功 INFO、失败 WARNING 带异常）。
        """
        try:
            if not cid:
                return
            st = self._mgr.get(cid)
            if str(st.get("archetype") or ""):
                return
            if self._chat_fn is None:
                return
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # 无运行事件循环：**不制造孤儿协程**，如实告警（不静默）
                logger.warning("[关系] 原型反推跳过：当前无运行事件循环（char=%s，沿用默认原型）", cid)
                return
            t = asyncio.create_task(self._infer_archetype_task(cid))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        except Exception:  # noqa: BLE001
            logger.warning("[关系] 原型反推触发失败（沿用默认原型）", exc_info=True)

    async def _infer_archetype_task(self, cid: str) -> None:
        try:
            persona = self._persona_for(cid)
            if not persona:
                logger.warning("[关系] 原型反推跳过：取不到人设（char=%s）", cid)
                return
            arch = await _infer_archetype(persona, self._chat_fn)
            if arch:
                self._mgr.set_archetype(cid, arch, inferred=True)
                logger.info("[关系] 原型已自动反推 char=%s → %s", cid, arch)
            else:
                # 模型没给出合法原型名（或超时/解析失败）→ 如实告警，不再静默
                logger.warning("[关系] 原型反推未得到合法原型（char=%s，沿用默认「温柔」）", cid)
        except Exception:  # noqa: BLE001
            logger.warning("[关系] 原型自动反推失败（沿用默认「温柔」）", exc_info=True)

    async def _judge_and_apply(self, cid: str, ut: str) -> None:
        signals: dict[str, Any]
        # 2026-10-10：给裁判补上**最近对话上下文**（修"逐句独立判定"），
        #   让裁判能识别反讽/玩笑、承上省略（"嗯"=收到而非敷衍）、多轮累积冷淡。
        #   取历史时排除"最后一条 user"（正是待判的 ut 那句，已单独传入），
        #   只保留它**之前**的轮次；失败/为空则传 None（裁判退回旧行为，不炸）。
        ctx: Optional[list[dict[str, Any]]] = None
        try:
            recent = self._history.load_recent(cid, n=10)
            # 去掉末尾可能是"本轮 ut"的 user 记录（同文本且是最后一条 user）
            trimmed = list(recent)
            for i in range(len(trimmed) - 1, -1, -1):
                if str(trimmed[i].get("role")) == "user":
                    if str(trimmed[i].get("content") or "").strip() == ut.strip():
                        trimmed = trimmed[:i] + trimmed[i + 1:]
                    break
            if trimmed:
                ctx = [{"role": m.get("role"), "content": m.get("content")} for m in trimmed]
        except Exception:  # noqa: BLE001
            logger.debug("[关系] 取判定上下文失败（按无上下文判定）", exc_info=True)
            ctx = None
        try:
            if self._judge is None:
                signals = _heuristic_signals(ut)
            else:
                signals = await self._judge.judge(ut, persona=self._persona_for(cid), context=ctx)
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 裁判异常（本轮按规则兜底）")
            signals = _heuristic_signals(ut)
        try:
            self._mgr.mark_response(cid)
            st = self._mgr.on_turn(cid, signals)
            self._log_state(st, "更新", signals=signals)
            # 【2026-10-11·P1 仪式感】本轮若**首次**跨过阶段门槛 → 发一条"里程碑"事件，
            #   让上层（聊天页）把"有分量的话"作为一个**可感知的质变时刻**说出来。
            #   反 PUA：一次性闸门（relationship.py 已保证不重复）、不暴露数字、
            #   不制造"必须维持"的压力；只是她愿意说一句真心话而已。
            self._emit_milestone_if_any(cid, st, signals)
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 应用本轮判定失败")

    def _emit_milestone_if_any(self, cid: str, st: dict[str, Any],
                               signals: dict[str, Any]) -> None:
        """首跨阶段门槛时，把里程碑（含台词）作为一次**可感知的质变时刻**呈现。

        实现方式（复用既有管线，零新事件类型）：发一条 `AGENT_STATUS`，data 带
        `milestone=True` + `line`（有分量的话）+ `stage`。UI 侧可识别后作为一条
        特殊样式消息呈现（不是状态卡的"正在处理"，而是"她说了一句真心话"）。
        若没有消费方，事件被忽略也无害（不改变任何既有行为）。

        【2026-10-11 补·"里程碑绝对不能丢"】上面那条 AGENT_STATUS 只够"当场提醒"，
        她说出口的这句话**说完就没了**（不进对话历史、不进记忆库）→ 用户下次问她
        "我们是什么时候变熟的" 她答不出。故本函数**在发送当下就双写**：
          · ① 写进 `shared_history()`（此后整段记忆提炼能通读到这句）；
          · ② 作为一条 `episode` 记忆**直写记忆库**（不依赖"上下文快超限"时机，
                100% 不丢；对应 `memory_synthesis` 的 episode 语义）。
        并且 ③ 额外发一条 `AI_DECISION{kind:'milestone'}` → 走正常聊天路径，
        让她这句话**以一条正式聊天消息出现在聊天流里**（前端渲染成特殊气泡）。
        三步全部 try/except 隔离：任何一步失败都不影响其它步，也不影响对话。
        """
        ms = None
        try:
            ms = (st.get("last_signals") or {}).get("milestone")
        except Exception:  # noqa: BLE001
            ms = None
        if not ms:
            return
        line = str(ms.get("line") or "").strip()
        stage = str(ms.get("stage") or "").strip()
        kind = str(ms.get("kind") or "").strip()

        # ① 写进共享对话历史（此后"整段记忆提炼"能通读到这句）
        if line:
            try:
                from .conversation_history import shared_history
                shared_history().add_turn("assistant", line, source="chat")
            except Exception:  # noqa: BLE001
                logger.debug("[关系] 里程碑写入对话历史失败（已跳过）", exc_info=True)

        # ② 直写记忆库（episode，不依赖提炼时机）
        if line:
            try:
                self._direct_write_milestone_memory(cid, line, stage, kind)
            except Exception:  # noqa: BLE001
                logger.debug("[关系] 里程碑直写记忆失败（已跳过）", exc_info=True)

        # ③ 作为一条正式聊天消息呈现（特殊气泡）
        if line:
            try:
                self._publish_milestone_chat(line, stage, kind)
            except Exception:  # noqa: BLE001
                logger.debug("[关系] 里程碑聊天消息发送失败（已跳过）", exc_info=True)

        # ④ 原有的 AGENT_STATUS（桌宠/状态侧，保持不变，向后兼容）
        try:
            self._bus.publish_threadsafe(Event(
                type=EventType.AGENT_STATUS,
                data={"char_id": cid, "phase": "milestone", "milestone": True,
                      "kind": kind, "stage": stage,
                      "line": line, "text": line},
            ))
        except Exception:  # noqa: BLE001
            logger.debug("[关系] 里程碑事件发射失败（已跳过）", exc_info=True)

    def _direct_write_milestone_memory(self, cid: str, line: str,
                                       stage: str, kind: str) -> bool:
        """把里程碑那句话作为一条 episode 记忆直写记忆库（不依赖提炼时机）。

        episode 正文刻意带上"首次抵达某阶段"的语义，且**不含任何数字**（反 PUA 红线）。
        角色隔离：按 cid 设作用域（store 由 main.py 注入，与 dialogue 同一实例）。

        ⚠️ **绝不自己 new 一个 MemoryStore** —— 那会变成"两个写者各 load/save 同一
        data/memory.json"，静默丢更新（本项目铁律）。没注入共享 store 时**不直写**
        （降级：只写对话历史，靠整段提炼带出），绝不制造第二写者。
        """
        store = getattr(self, "_memory_store", None)
        if store is None:
            return False
        text = "她第一次对你说出「%s」这句话：%s" % (stage or "关系更进一步", line)
        try:
            # 确保作用域对（dialogue 切角色时也会设；此处幂等重设，防时序差）
            try:
                store.set_character(cid or "default")
            except Exception:  # noqa: BLE001
                pass
            return bool(store.add(text[:200], type="episode", source="relationship"))
        except Exception:  # noqa: BLE001
            logger.debug("[关系] 里程碑 episode 写入失败", exc_info=True)
            return False

    def _publish_milestone_chat(self, line: str, stage: str, kind: str) -> None:
        """把里程碑那句话以一条正式 AI 聊天消息发出（前端渲染成特殊气泡）。

        走 `AI_DECISION`（与普通回复同一条路）→ 会进 `_chat_buffer`、落 chatlog、
        前端按 `kind:'milestone'` 渲染成"质变时刻"气泡。`milestone=True` 供后端/前端
        识别；`streamed=False` 表示这是一条独立完整消息（不是打字机收尾）。

        ⚠️ 用 `publish_threadsafe` 而**不是** `await publish(...)`：本函数是**同步**的
        （由 async 的 `_on_ai_decision` 同步调用），若写成 `self._bus.publish(...)`
        会得到一个**没被 await 的协程对象** → 事件根本不会入队 → 里程碑气泡静默不出现
        （项目老坑："在但不生效"）。`publish_threadsafe` 是普通同步方法（`put_nowait`），
        在原线程调用也安全（总线是 queue + 串行消费，publish 只入队、不同步跑 handler）。
        """
        self._bus.publish_threadsafe(Event(EventType.AI_DECISION, {
            "emotion": "warm", "action": "talk", "intensity": 3,
            "text": line, "streamed": False,
            "kind": "milestone", "milestone": True,
            "stage": stage, "milestone_kind": kind,
            "line": line,        # 与 AGENT_STATUS 对齐：两种事件的台词都可在 line / text 读到
            "channel": "", "reply_target": None,
        }, "relationship"))

    async def _on_char_switched(self, e: Event) -> None:
        d = e.data if isinstance(e.data, dict) else {}
        cid = str(d.get("char_id") or "").strip()
        if not cid:
            return
        # 切角色：清空尚未配对的用户队列，避免旧角色的话被配到新角色的回复上（跨角色错配）。
        self._pending_user_queue.clear()
        self._char_id = cid
        try:
            self._mgr.tick(cid)
            self._log_state(self._mgr.get(cid), f"切换到「{d.get('char_name') or cid}」")
        except Exception:  # noqa: BLE001
            logger.exception("[关系] 切换角色补算失败")
        # 切到新角色时，若其原型还没定，后台反推一次
        self._maybe_infer_archetype(cid)

    # ---- 内部 ----

    def _resolve_char_id(self) -> str:
        if self._char_id:
            return self._char_id
        try:
            cid = str(getattr(self._cfg, "active_character_id", "") or "").strip()
        except Exception:  # noqa: BLE001
            cid = ""
        self._char_id = cid or "default"
        return self._char_id

    def _log_state(self, st: dict[str, Any], prefix: str,
                   signals: Optional[dict[str, Any]] = None) -> None:
        aff = st.get("affinity") or {}
        mood = st.get("mood") or {}
        gap_txt = ""
        last = float(st.get("last_interaction_ts") or 0.0)
        if last:
            gap_h = (time.time() - last) / 3600.0
            gap_txt = f"上次互动 {gap_h / 24:.1f} 天前" if gap_h >= 24 else f"上次互动 {gap_h:.1f} 小时前"
        parts: list[str] = []
        if gap_txt:
            parts.append(gap_txt)
        if signals:
            bits = [f"{k}={signals[k]}" for k in ("valence", "care", "perfunctory", "repair", "harsh")
                    if signals.get(k)]
            if bits:
                seg = "判定 " + " ".join(bits)
                # 新手期会把负向清零——日志必须显示"实际生效值"，否则看着像"扣了却没变化"
                raw_v = float(signals.get("valence") or 0.0)
                rv = st.get("recent_valence") or []
                applied = float(rv[-1]) if rv else raw_v
                if abs(applied - raw_v) > 1e-6:
                    seg += f"（新手期免负→生效 {applied:+.3f}）"
                parts.append(seg)
        try:
            if self._mgr.engine.is_newbie(st):
                parts.append("新手期")
        except Exception:  # noqa: BLE001
            pass
        head = (f"[关系] {prefix} · 心情={mood.get('label')}({mood.get('intensity')}) · "
                f"信任={float(aff.get('trust', 0)):.1f} 亲密={float(aff.get('intimacy', 0)):.1f} "
                f"热络={float(aff.get('warmth', 0)):.1f} · 阶段={st.get('stage')}")
        tail = " · ".join(parts)
        logger.info("%s%s", head, (" · " + tail) if tail else "")
