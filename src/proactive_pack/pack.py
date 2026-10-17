# -*- coding: utf-8 -*-
"""主动互动包 · 门面（Facade）。宿主只需认识这一个类。

一句话：`pack.maybe_emit()` 返回 True 表示"这一轮主动已经由本包发出去了"，
宿主就不必再走它自己的通用引信（避免重复开口）。

依赖注入（全部**可选**）：
  · host  —— 宿主 `ProactiveCompanion` 实例（拿 _idle_threshold_s / _last_input_ts /
             _schedule_duplex_emit / _record_duplex_ai_line / _log_duplex_source …）
  · llm   —— 生成用 LLM 客户端（不传则用 host._llm）
  · cfg_ns / base_dir / marks —— 配置与落盘位置
  · emit_fn / clock_fn —— 单测替换用（不传则用 host 的真实实现）

红线：本包**不改任何宿主成员**（除 install() 显式替换 check_and_trigger 那一处），
      所有状态都落在自己包内 + `data/proactive_pack.json`。
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import engine_llm as engine_llm_mod
from . import judge as judge_mod
from . import prompts
from .config import load_flags, DEFAULT_BASE_DIR
from .marks import MarksStore
from .situation import collect
from .triggers import Ctx, Trigger, evaluate

logger = logging.getLogger("proactive_pack")

# 无锚点"万能句"黑名单（文档 §4.2 明令禁止）。
# 判定 = 命中黑名单短语 **且** 整句找不到任何"锚点线索"（时间/回指/具体事），
# 这样"记得喝水"会被丢，而"记得喝水，你昨天说嗓子疼"会放行。
_BANNED = (
    "在忙吗", "在忙么", "忙不忙", "注意身体", "记得喝水", "多喝热水",
    "累了就休息", "记得休息", "歇会儿吧", "有什么需要随时",
)
_ANCHOR_HINTS = (
    "昨天", "前天", "上次", "刚才", "之前", "今天", "今晚", "昨晚",
    "你说", "你提", "连着", "几天", "几点", "那次", "最近",
)

# 「用户说过/提过/推荐过 X」类回指。这类句子**必须真有其事**，否则就是编造她的离线生活。
# 说明：_ANCHOR_HINTS 把「你说/你提」也当锚点线索（为了放过"接着刚才话题说"的正经话），
#       但光凭"你昨天说"识别不了"那件事从没发生过"——所以单独立这一条做**材料核对**。
_USER_SAID_RE = re.compile(
    r"你(?:刚才|昨天|前天|昨晚|早上|上次|之前|最近|老)?"
    r"(?:说|讲|提|聊|问|答应|承诺|推荐|安利|分享|发|记得|点|买|选|订|喝)"
    r"|(?:说|提|讲|聊|推荐|安利|分享|发|记得|点|买|选|订)(?:过)?的"
    r"|你还记得|还记得你"
    r"|那[天回首家部个篇件次张份条种杯碗场瓶款盘本盆盒袋束台只段句]"
    # 观察/行为回指类断言：不依赖"说/提"标记，但同样在断言"用户做过/拥有某件具体事"
    r"|(?:看|见|瞅)到?你[^，。！？]{0,14}(?:分享|发|朋友圈|桌上|手里|手机|电脑|屏幕|杯)"
    r"|你(?:昨晚|昨天|刚才|今天早上|上次|之前|那天|今天)?"
    r"(?:不是|没|没有|也|又|还|正|在)?"
    r"(?:看|吃|做|去|玩|买|跑|追|喝|拍|唠叨|念叨|加班|熬夜)(?:了|的|着|到)"
    r"|你(?:昨晚|昨天|刚才|今天|早上|凌晨)?(?:熬夜|追剧|看剧|看球|打游戏|看电影|看直播|开黑)"
    r"|(?:我)?听说你"
    r"|你(?:最)?(?:爱|喜欢)那"
    r"|那时候|你们刚才|咱们一起|咱俩"
)
# 情境文本里出现任一标记，才算"有可锚的真实材料"——判据在 situation.has_grounding_material
# （单一真相源；本文件不再自留一份副本，避免两处漂移）。
# 暗示等待/轻度施压句式（反 PUA 铁律）——**无条件丢弃**，不分 trigger 类型、不看有没有锚点。
# 注：查岗类句式（"你去哪了"）不在此列（开发者 2026-10-12 明确暂不修，留给 silence 档的自然问法）。
_PUA_WAIT_RE = re.compile(r"我等你|等你好久|等了你|一直在等你|等了你半天|等了你好")
# 自我编造（她自称做了需要身体的现实活动）——她是桌面/VR 里的 AI 伴侣，没有线下身体：
# 吃饭/做饭/出门/洗澡/上班/买东西都不可能。无条件丢弃（宁不说，也不编自己的假生活）。
# 注意：分句处理时，含"你"的分句跳过本检测（那是说用户的，归 _looks_fabricated 管）。
_SELF_ACT_RE = re.compile(
    r"我[^你，。！？]{0,10}(?:去了|来了|回了|逛了|跑了|出门了|回来|回来啦)"
    r"|我[^你，。！？]{0,10}(?:去|来|回|逛|跑)(?:了|过|完|一下|了下)"
    r"|我[^你，。！？]{0,10}(?:尝试(?:了|过|一下)|体验了|打卡了|试了试|尝了尝)"
    r"|我[^你，。！？]{0,6}(?:吃|喝|尝|买|点)(?:了|过|完|着)"
    r"|我[^你，。！？]{0,6}(?:做|炖|煮|端|洗)(?:了|过|完)?[^，。！？]{0,6}(?:饭|菜|汤|排骨|澡|咖啡|奶茶|衣服)"
    r"|我正在(?:吃|喝|做|炖|煮|洗澡|做饭|上课|上班|开会|运动|健身|加班)"
    r"|我[^你，。！？]{0,4}刚(?:吃完|喝完|做好|洗完|出门|下班|起床|起来)"
    r"|我[^你，。！？]{0,8}(?:准备|打算|要去|得去)(?:去)?(?:吃|喝|买)"
    r"|刚(?:做好|煮好|炖好|端上)(?:一)?[^，。！？]{0,6}(?:杯|碗|盘|份|锅)"
    r"|我?刚起(?:来|床)"
)
def _strip_quote_wrap(text: str) -> str:
    """剥掉模型偶尔套在整句外的引号（“…”、"…"、「…」等）——主动发话不该带包装。"""
    t = (text or "").strip()
    while len(t) >= 2 and t[0] in "\"'“”「『『\"'" and t[-1] in "\"'“”」』』\"'":
        t = t[1:-1].strip()
    return t


class ProactivePack:
    """主动互动包主体。"""

    def __init__(self, host: Any = None, llm: Any = None,
                 cfg_ns: Any = None, base_dir: Optional[Path | str] = None,
                 name: str = "", emit_fn: Optional[Callable] = None,
                 judge_llm: Any = None) -> None:
        self.host = host
        self._llm = llm
        self._judge_llm = judge_llm          # 审查器专用模型（不传则按 judge_provider / 回退生成端）
        self.cfg_ns = cfg_ns
        self._reviewer_cache: tuple[tuple[str, str], Any] | None = None  # ((provider_id, model), client)
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
        self._name_override = name
        self._emit_fn = emit_fn
        self.flags = load_flags(cfg_ns, self.base_dir)
        self.marks = MarksStore(self.base_dir)
        self._last_kind: str = ""
        self._last_text: str = ""
        self._last_rejects: list[dict] = []   # 本轮生成被驳回的记录（类别/理由，供排障）
        self._judge_unavailable = 0           # 审查器不可用次数（fail-open，但必须非静默）
        self._offs = False          # 一次性"总开关关"日志去重

    # ---- 开关 ----

    @property
    def enabled(self) -> bool:
        return bool(self.flags.get("enabled", False))

    def reload_flags(self, cfg_ns: Any = None) -> dict:
        self.flags = load_flags(cfg_ns if cfg_ns is not None else getattr(self.host, "_cfg", None),
                                self.base_dir)
        self._reviewer_cache = None      # 端口/模型可能变了 → 让审查器客户端重建（否则吃旧快照）
        return self.flags

    def set_flag(self, key: str, value: Any) -> bool:
        if key not in self.flags and key not in ("enabled",):
            logger.warning("[proactive_pack] 未知开关 %s（仍写入）", key)
        self.flags[key] = value
        from .config import write_state_flags
        ok = write_state_flags(self.flags, self.base_dir)
        logger.info("[proactive_pack] 开关 %s=%s（落盘 %s）", key, value, "成功" if ok else "失败")
        return ok

    def status(self) -> dict:
        """给 UI / 桥看的状态快照（不含任何隐私内容）。"""
        _eng = self._engine_llm(probe=False)   # 只探端点是否存在；不做健康检查，避免 UI 卡顿
        return {
            "enabled": self.enabled,
            "flags": dict(self.flags),
            "ledger_today": self.marks.ledger_count(),
            "last_kind": self._last_kind,
            "last_text": self._last_text,
            "judge_on": bool(self.flags.get("llm_gate", True)),
            "judge_attempts": int(self.flags.get("judge_attempts", 3)),
            "judge_provider": str(self.flags.get("judge_provider") or ""),
            "judge_model": str(self.flags.get("judge_model") or ""),
            "judge_use_engine": self._use_engine(),
            "engine_llm_connected": _eng is not None,
            "engine_llm_endpoint": getattr(_eng, "endpoint", "") if _eng is not None else "",
            "judge_unavailable": int(self._judge_unavailable),
            "last_rejects": list(self._last_rejects),
            "state_file": str(self.marks.path),
        }

    def live_state(self, now: Optional[float] = None) -> dict:
        """只读：此刻真实静默状态 + 是否已满足触发门槛（供 UI 验证开关真实生效）。

        不生成、不派发、不写账本。即使 enabled=false 也能看到"差多久才触发"，
        方便用户确认引擎确实在按真实状态推理。
        """
        t = time.time() if now is None else now
        cid = self._char_id()
        last_in = self._last_input_ts() if cid else 0.0
        thr = self._idle_threshold()
        gap = (t - last_in) if last_in else 0.0
        return {
            "cid": cid,
            "installed": True,
            "enabled": self.enabled,
            "idle_threshold_s": thr,
            "last_input_ts": last_in,
            "gap_s": gap,
            "gap_min": round(gap / 60.0, 1) if gap > 0 else 0.0,
            "meets_idle": bool(cid and last_in and gap >= thr),
        }

    # ---- 取数（宿主优先，允许注入） ----

    def _use_engine(self) -> bool:
        """总开关：是否让本包走**宿主模型引擎**（生成 + 审查同一个模型走引擎）。

        缺省关 = 生成走宿主聊天路由、审查走 `judge_provider` 独立端口（dev 形态）。
        生产环境（不会用免费模型、无限流）把它打开即可。
        """
        return str(self.flags.get("judge_use_engine", "false") or "false").strip().lower() \
            in ("1", "true", "yes", "on", "是", "开")

    def _engine_llm(self, *, probe: bool = True) -> Any:
        """宿主模型引擎的 **llm/vlm 档** 客户端（若有；否则 None）。

        解析顺序：`host.engine_llm`（客户端对象，或懒求值的**可调用**）→
        `host.engine_llm_factory()` → 从宿主引擎自动发现
        （`host._local_omni_engine` / `host._engine` 的 `endpoint`，
        或 `host._duplex_local_omni_endpoint`）。

        引擎的 llm 档 = 网关 `POST {endpoint}/api/chat`（**非** OpenAI 风格，
        见 `engine_llm.py` 顶部说明）。主动包据此用**同一个模型走引擎**
        既主动发言又审查，不再依赖任何免费 / 外部模型、无限流。

        `probe=False`：不做 `is_model_loaded()` 健康检查（给 UI/status 用，避免卡顿）。
        关键：主动包每次调用都是**自带 messages 的 stateless `chat()`**，走独立的
        `_schedule_duplex_emit` 发射路径——和用户的双工/聊天会话上下文不在一处，
        因此**不会干涉用户被动回消息**。
        """
        v = getattr(self.host, "engine_llm", None)
        if v is not None:
            try:
                return v() if callable(v) else v
            except Exception:  # noqa: BLE001
                logger.debug("[proactive_pack] engine_llm 取值失败", exc_info=True)
                return None
        f = getattr(self.host, "engine_llm_factory", None)
        if callable(f):
            try:
                return f()
            except Exception:  # noqa: BLE001
                logger.debug("[proactive_pack] engine_llm_factory 构造失败", exc_info=True)
                return None
        try:
            return engine_llm_mod.engine_client_for_host(
                self.host, base_url=str(self.flags.get("engine_endpoint") or ""), probe=probe)
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] 引擎端点解析失败", exc_info=True)
            return None

    def _llm_client(self) -> Any:
        # 生成端优先级：注入 > （开关开时的）宿主引擎 llm/vlm 档 > 宿主聊天路由。
        if self._llm is not None:
            return self._llm
        if self._use_engine():
            return self._engine_llm() or getattr(self.host, "_llm", None)
        return getattr(self.host, "_llm", None)

    def _reviewer_client(self) -> Any:
        """审查器用的 LLM，解析顺序：

        ① 显式注入 `judge_llm`
        ② `judge_use_engine=true` → 宿主引擎 llm/vlm 档（`host.engine_llm` or `host._llm`）
           ——生产环境：同一模型走引擎既发言又审查，无视独立审查模型、无限流。
        ③ `judge_provider` 指定的独立端口（dev：审查换更强免费模型）
        ④ 与生成同一个（`_llm_client()`，已优先引擎档）

        为什么允许 ③ 分开：审查是"拿材料逐条比对"，对模型能力的要求**比"说一句台词"高得多**。
        实测 glm-4-flash 做审查只有 3/10（编造/自曝身体/万能句全放行）——弱模型会把
        假话一路放行。所以 dev 留一个独立入口，让审查走更强的模型；生产关掉它即可。
        """
        if self._judge_llm is not None:
            return self._judge_llm
        if self._use_engine():
            return self._engine_llm() or self._llm_client()
        pid = str(self.flags.get("judge_provider") or "").strip()
        if not pid:
            return self._llm_client()
        # 缓存键含**模型**：只换 judge_model 时也要重建，否则改了不生效（陈旧快照）。
        key = (pid, str(self.flags.get("judge_model") or "").strip())
        if self._reviewer_cache and self._reviewer_cache[0] == key:
            return self._reviewer_cache[1]
        cli = self._build_reviewer(pid)
        self._reviewer_cache = (key, cli or self._llm_client())
        return self._reviewer_cache[1]

    def _build_reviewer(self, provider_id: str) -> Any:
        """按 config.yaml 的端口 id 建审查器专用客户端；读不到 → None（回退生成端，不静默改行为）。

        `cfg_ns` 有两种形态都要认：**点号命名空间树**（`Config.ns`）或 **Config 对象**（有 `.ns`）——
        `install()` 传的是 `host._cfg`，测试里常直接传 ns 树，认错就会静默回退生成端。
        """
        cfg = self.cfg_ns if self.cfg_ns is not None else getattr(self.host, "_cfg", None)
        provs: list = []
        for cand in (cfg, getattr(cfg, "ns", None)):
            if cand is None:
                continue
            tc = getattr(cand, "text_chat", None)
            if tc is not None:
                try:
                    provs = list(getattr(tc, "providers", []) or [])
                except Exception:  # noqa: BLE001
                    logger.debug("[proactive_pack] 读 text_chat.providers 失败", exc_info=True)
                break
        for p in provs:
            if str(getattr(p, "id", "") or "") != provider_id:
                continue
            base = str(getattr(p, "base_url", "") or "")
            key = str(getattr(p, "api_key", "") or "")
            model = str(self.flags.get("judge_model") or "") or str(getattr(p, "model", "") or "")
            if not (base and model):
                logger.warning("[proactive_pack] 审查器端口 %s 缺 base_url/model，回退生成端", provider_id)
                return None
            logger.info("[proactive_pack] 审查器改用独立端口 %s（%s）", provider_id, model)
            return judge_mod.MiniOpenAIClient(base_url=base, api_key=key, model=model)
        logger.warning("[proactive_pack] 配置里找不到审查器端口 %s，回退生成端", provider_id)
        return None

    def _name(self) -> str:
        if self._name_override:
            return self._name_override
        p = getattr(self.host, "_persona", None)
        return str(getattr(p, "name", "") or "达妮娅")

    def _char_id(self) -> str:
        cfg = getattr(self.host, "_cfg", None)
        return str(getattr(cfg, "active_character_id", "") or "")

    def _idle_threshold(self) -> float:
        try:
            return float(getattr(self.host, "_idle_threshold_s", 180.0) or 180.0)
        except Exception:  # noqa: BLE001
            return 180.0

    def _last_input_ts(self) -> float:
        v = getattr(self.host, "_last_input_ts", 0.0) or 0.0
        if not v:
            v = getattr(self.host, "_session_boot_ts", 0.0) or 0.0
        return float(v or 0.0)

    # ---- 生成 ----

    async def _generate(self, prompt: str) -> str:
        llm = self._llm_client()
        if llm is None:
            return ""
        import asyncio
        raw = await asyncio.wait_for(
            llm.chat([{"role": "user", "content": prompt}], options={"temperature": 0.85}),
            timeout=20.0,
        )
        text = _strip_quote_wrap((raw or "").strip())
        cleaner = getattr(self.host, "_clean_duplex_speech", None)
        if callable(cleaner):
            try:
                text = cleaner(text)
            except Exception:  # noqa: BLE001
                pass
        return (text or "").strip()

    def _is_banned(self, text: str, kind: str = "") -> bool:
        """无锚点万能句判定：命中黑名单短语且全句没有任何锚点线索。

        豁免：`silence`（长静默档）。朋友几天没见，问一句「在忙吗」是**正当且必要**的，
        这时机械套用「必须带锚点」反而没有人味（开发者明确要求保留这个行为）。
        """
        t = text or ""
        if not any(b in t for b in _BANNED):
            return False
        if kind == "silence":
            return False
        return not any(a in t for a in _ANCHOR_HINTS)

    def _is_pua(self, text: str) -> bool:
        """暗示等待/轻度施压句式判定（反 PUA 铁律）：无条件丢弃。

        与 _is_banned 的区别：不看锚点、不分 trigger 类型（silence 档也不豁免）——
        "我等你呢"哪怕带具体事由，也构成"表现我在等你"的情感施压。
        """
        return bool(_PUA_WAIT_RE.search(text or ""))

    def _is_self_claim(self, text: str) -> bool:
        """自我编造判定：她自称做了需要身体的现实活动（吃饭/做饭/出门/洗澡/上班…）。

        她是桌面/VR 里的 AI 伴侣，没有线下身体——声称"我也去了那家店""我刚吃完早餐"
        全是编造自己的假生活，与"不编造她的离线生活"铁律一致：**无条件丢弃**。
        实现按分句处理：含"你"的分句跳过（那是说用户的，归 _looks_fabricated 管），
        避免"我记得你上午跑了步"被误杀。
        """
        t = text or ""
        for clause in re.split(r"[，。！？；、\n]", t):
            c = clause.strip()
            if not c or "你" in c:
                continue
            if _SELF_ACT_RE.search(c):
                return True
        return False

    def _is_self_name(self, text: str) -> bool:
        """第三人称自指判定：她说话时句子里出现**她自己的名字**来指代自己。

        实测真机 bug（2026-10-12）：「嘿，**达妮娅**记得你之前喜欢科幻…」——把自己的
        名字当旁白念出来，真人不会这么讲话。LLM 审查对这条"**症状**"不稳定（定向实测：
        同类的「达妮娅觉得…」「达妮娅跟你说…」变体时判时不判），所以这里降级成
        **代码闸的硬形状检查**：她开口时名字只可能出现在"引用别人的话"里 → 先剥掉成对
        引号内的内容，若名字仍出现即命中。

        只对**她自己的名字**生效；名字短于 2 字时不判（避免把常见字当名字误杀）。
        """
        t = text or ""
        nm = (self._name() or "").strip()
        if len(nm) < 2:
            return False
        core = re.sub(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"", "", t)
        return nm in core

    def _is_duplicate(self, text: str) -> bool:
        fn = getattr(self.host, "_similar_to_recent", None)
        if callable(fn):
            try:
                return bool(fn(text))
            except Exception:  # noqa: BLE001
                return False
        return False

    def _has_grounding_material(self, sit_text: str) -> bool:
        """情境文本里是否有"可锚的真实材料"（topic/memory/pending/interest 任一非空）。

        判据与 prompts.build_prompt 共用（situation.has_grounding_material），
        保证"要不要硬要锚点"和"算不算编造"用同一把尺。
        """
        from .situation import has_grounding_material
        return has_grounding_material(sit_text)

    def _looks_fabricated(self, text: str, sit_text: str) -> bool:
        """编造检测：这句话声称"你说过/提过/推荐过 X"，但情境里根本没有可锚的真实事件。

        设计取舍（与反 PUA / 要真实一致）：
          · 触发条件 = 有"用户说过X"式回指 **且** 情境无真实材料 → 判编造。
          · 误发一句假话比漏发一句糟得多，所以这里**宁可漏发也不编**（调用方会重生成一次，仍编则放弃本轮）。
          · 局限：若情境**有**材料但模型把"喜欢咖啡"夸张成"你昨天说的那家咖啡店"，
            本检测放行（材料在场）——这类"细节 embellishment"由默认开启的 LLM 减法闸（看得到情境）兜底。
        """
        t = text or ""
        if not _USER_SAID_RE.search(t):
            return False
        return not self._has_grounding_material(sit_text)

    def _code_reject(self, text: str, kind: str, sit_text: str) -> tuple[str, str]:
        """**第一道（纯代码）**：只抓"形状"，零成本、确定性。

        返回 (类别, 驳回理由)；通过则 ('', '')。类别词表与 `judge.HINTS` 一致，
        这样代码闸与 LLM 闸的"回灌改写指令"共用一套文案。
        保留它的意义：明显违规（黑名单 / 自我编造 / 无材料编造）不必花钱让 LLM 再看一遍；
        但它**不是**主力——语义问题（一句话两件事、客服腔、答非所问）交给第二道 LLM 审查。
        """
        t = text or ""
        if self._is_self_name(t):
            return "robotic", "用第三人称念自己的名字（真人说话只说「我」）。"
        if self._is_banned(t, kind):
            return "bland", "命中无锚点万能句黑名单，且整句没有任何具体锚点。"
        if self._is_pua(t):
            return "pua", "出现暗示等待 / 施压句式（反 PUA 铁律）。"
        if self._is_self_claim(t):
            return "self_claim", "她声称自己做了需要身体的现实活动（她没有身体）。"
        if self._looks_fabricated(t, sit_text):
            return "fabrication", "回指了用户说过的事，但情境里没有任何可锚的真实材料。"
        if self._is_duplicate(t):
            return "repeat", "与最近说过的话重复。"
        return "", ""

    async def _llm_review(self, trg: Trigger, text: str, sit_text: str):
        """**第二道（LLM 审查）**：语义级判定"这句话此刻能不能发"。

        fail-open：审查器故障 / 输出不可解析 → 放行（不因闸门坏掉就让她永远闭嘴），
        但**必须留痕不静默**：记 warning + 计数，连续出现要能一眼看出"闸门其实没在工作"。
        """
        _raw_think = self.flags.get("judge_thinking", "auto")
        # ⚠️ 不能用 `or "auto"`：布尔 False 是 falsy，会被悄悄吞成 "auto"（=不传），
        # 于是「关思考」在配置里写 False 时反而失效 → 推理模型正文为空 → 闸门静默全放行。
        think = str("auto" if _raw_think is None else _raw_think).strip().lower()
        kw: dict = {}
        if think not in ("", "auto"):
            kw["enable_thinking"] = think in ("1", "true", "yes", "on", "是", "开")
        v = await judge_mod.review(
            self._reviewer_client(), text=text, sit_text=sit_text,
            brief=trg.brief or "", kind=trg.kind, name=self._name(), **kw,
        )
        if not v.parsed:
            self._judge_unavailable += 1
            logger.warning(
                "[proactive_pack] 审查器本次不可用（%s），按放行处理——已累计 %d 次。"
                "请检查 judge_provider/judge_model 是否可调用；若审查模型是推理模型，"
                "建议设 judge_thinking=false（否则思维链吃光 max_tokens，正文为空）。",
                v.category, self._judge_unavailable)
        return v

    async def _generate_with_review(self, trg: Trigger, prompt: str,
                                    sit_text: str) -> tuple[str, bool]:
        """生成 → 代码闸 → LLM 审查 → 不合格把理由回灌重写；直至通过或用尽次数。

        返回 (最终文本, 是否通过)。用尽次数仍未通过 → ("", False)，本轮放弃开口。
        这就是"一个想怎么说、一个判能不能说"的两条流水线；上限由 `judge_attempts` 控制。
        `llm_gate=false` 时只保留第一道代码闸（离线 / 想省钱时的降级形态）。
        """
        max_att = max(1, int(self.flags.get("judge_attempts", 3)))
        use_judge = bool(self.flags.get("llm_gate", True)) and self._reviewer_client() is not None
        rejects: list[dict] = []
        self._last_rejects = rejects
        feedback = ""
        for attempt in range(1, max_att + 1):
            text = (await self._generate(prompt + feedback) or "").strip()
            if not text:
                rejects.append({"attempt": attempt, "by": "code", "category": "empty",
                                "reason": "生成空句", "text": ""})
                feedback = judge_mod.build_retry_feedback("empty", "上一句是空的", "")
                continue
            cat, why = self._code_reject(text, trg.kind, sit_text)
            rewrite = ""
            by = "code"
            if not cat:
                if not use_judge:
                    return text, True
                v = await self._llm_review(trg, text, sit_text)
                if v.ok:
                    if not v.parsed:
                        logger.info("[proactive_pack] 审查器未给出可解析判定，按放行（%s）", v.category)
                    return text, True
                cat, why, rewrite, by = (v.category or "review"), v.reason, v.rewrite, "llm"
            rejects.append({"attempt": attempt, "by": by, "category": cat,
                            "reason": why, "text": text[:60]})
            logger.info("[proactive_pack] 第 %d/%d 次生成被%s驳回[%s]：%s（%s）",
                        attempt, max_att, "审查器" if by == "llm" else "代码闸", cat, text, why)
            feedback = judge_mod.build_retry_feedback(cat, why, rewrite)
        logger.info("[proactive_pack] 生成 %d 次仍未通过审查，放弃本次（kind=%s）", max_att, trg.kind)
        return "", False

    # ---- 闸门 ----

    def _relation_gate(self, cid: str, now: float) -> tuple[bool, str]:
        """宿主关系闸门（冷却 / 日上限 / 静默时段 / 冷落）。异常一律放行（不因闸门故障闭麦）。"""
        try:
            from ..companion.relationship import get_relationship_manager, _proactive_decision
            return _proactive_decision(get_relationship_manager().get(cid), now)
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] 关系闸门不可用，放行", exc_info=True)
            return True, "gate_unavailable"

    def _light_gate(self, now: float) -> tuple[bool, str]:
        """跟进类用的**轻闸门**：只守静默时段 + 本包日上限（不吃宿主的 24h 冷却）。"""
        try:
            from ..companion.relationship import SILENCE_START_H, SILENCE_END_H
        except Exception:  # noqa: BLE001
            SILENCE_START_H, SILENCE_END_H = 22, 8
        h = time.localtime(now).tm_hour
        if SILENCE_START_H > SILENCE_END_H:
            quiet = (h >= SILENCE_START_H) or (h < SILENCE_END_H)
        else:
            quiet = SILENCE_START_H <= h < SILENCE_END_H
        if quiet:
            return False, "silence_hours(%d-%d)" % (SILENCE_START_H, SILENCE_END_H)
        cap = int(self.flags.get("day_cap", 6))
        if self.marks.ledger_count(now) >= cap:
            return False, "day_cap(%d)" % cap
        return True, "light_ok"

    # ---- 记账 / 派发 ----

    async def _deliver(self, cid: str, trg: Trigger, text: str, now: float) -> None:
        """写回共享记忆 + 关系记账 + 派发到聊天页（to_chat=True）。"""
        if self._emit_fn is not None:
            await self._emit_fn(text)
        else:
            await self.host._schedule_duplex_emit(text, to_chat=True)
        # 宿主侧簿记（尽力而为，缺一个不影响说话）
        for attr, val in (("_last_proactive", now), ("_duplex_last_speak_ts", now),
                          ("_last_message", text)):
            try:
                setattr(self.host, attr, val)
            except Exception:  # noqa: BLE001
                pass
        try:
            rm = getattr(self.host, "_recent_messages", None)
            if rm is not None:
                rm.append(text)
        except Exception:  # noqa: BLE001
            pass
        for meth in ("_record_duplex_ai_line",):
            fn = getattr(self.host, meth, None)
            if callable(fn):
                try:
                    fn(text)
                except Exception:  # noqa: BLE001
                    logger.debug("[proactive_pack] %s 失败", meth, exc_info=True)
        fn = getattr(self.host, "_log_duplex_source", None)
        if callable(fn):
            try:
                await fn("speak", text)
            except Exception:  # noqa: BLE001
                pass
        # 关系层记账（与宿主三条路径口径一致）
        if cid:
            try:
                from ..companion.relationship import get_relationship_manager
                get_relationship_manager().record_proactive(cid, now)
            except Exception:  # noqa: BLE001
                pass
        self._last_kind, self._last_text = trg.kind, text

    def _mark_sent(self, cid: str, trg: Trigger, now: float) -> None:
        """写去重标记（下次不再重复这一类）。"""
        try:
            if trg.kind == "festival":
                self.marks.mark_festival(cid, str(trg.meta.get("festival") or trg.dedup_key), now)
            elif trg.kind == "pending":
                self.marks.mark_pending(cid, trg.dedup_key, now)
            elif trg.kind == "followup":
                self.marks.mark_followup(cid, now)
            elif trg.kind == "recall":
                key = str((trg.meta.get("candidate") or {}).get("key") or "")
                if key:
                    self.marks.mark_recall(cid, key, now)
            elif trg.kind == "rhythm":
                kind = str(trg.meta.get("rhythm") or "rhythm")
                self.marks.mark_rhythm(cid, kind, now)
                if kind == "meal":
                    self.marks.mark_meal(cid, now)
            elif trg.kind == "share":
                from . import feed as _feed
                self.marks.mark_share(cid, _feed.item_key(trg.meta.get("item") or {}), now)
                self.marks.mark_share_day(cid, now)
            elif trg.kind == "silence":
                self.marks.mark_silence(cid, now)
            self.marks.ledger_bump(now)
        except Exception:  # noqa: BLE001
            logger.warning("[proactive_pack] 写去重标记失败（不影响本次开口）", exc_info=True)

    # ---- 主入口 ----

    def _maybe_refresh_feed(self, sit: Any, now: float) -> None:
        """素材池保鲜（借鉴 N.E.K.O 的"拉热点喂话题"）：过期才拉，后台线程，绝不阻塞。

        触发条件收敛：只有 `share` 开着 + 用户**确有兴趣画像**才值得拉网
        （没兴趣画像拉了也白拉）；拉取结果下一轮才可见——保鲜本来就该跑在开口前面。
        """
        if not (self.flags.get("share") and self.flags.get("feed_hot", True)):
            return
        if not (sit.get("interest_tags") or []):
            return
        try:
            import asyncio
            from . import feed_sources
            loop = asyncio.get_running_loop()
            ttl = float(self.flags.get("feed_ttl_h", 4.0))
            cap = int(self.flags.get("feed_max_items", 60))
            base = self.base_dir
            loop.run_in_executor(None, lambda: feed_sources.refresh_if_stale(
                base, ttl_h=ttl, max_items=cap, now=now))
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] 热榜保鲜未执行（本轮用现有素材）", exc_info=True)

    async def maybe_emit(self, now: Optional[float] = None) -> bool:
        """本包的唯一对外动作。True = 已开口（宿主不要再走自己的通用引信）。

        绝不对宿主抛异常；任何内部失败都记为"未开口"，让宿主原路径照常工作。
        """
        try:
            if not self.enabled:
                if not self._offs:
                    self._offs = True
                    logger.info("[proactive_pack] 总开关 enabled=false，本包不动作")
                return False
            t = time.time() if now is None else now
            cid = self._char_id()
            if not cid:
                return False
            last_in = self._last_input_ts()
            if not last_in:
                return False
            if (t - last_in) < self._idle_threshold():
                return False
            # 防打断自己：刚说过话（8s 内）不抢话（与宿主同口径）
            spk = float(getattr(self.host, "_duplex_last_speak_ts", 0.0) or 0.0)
            if spk and (t - spk) < 8.0:
                return False

            sit = collect(cid, now=t, last_input_ts=last_in, base_dir=self.base_dir,
                          flags=self.flags, marks=self.marks)
            self._maybe_refresh_feed(sit, t)      # 后台保鲜，不阻塞本轮判定
            ctx = Ctx(cid=cid, now=t, last_input_ts=last_in, flags=self.flags,
                      marks=self.marks, sit=sit, base_dir=self.base_dir)
            trg = evaluate(ctx)
            if trg is None:
                return False

            # 闸门：跟进类走轻闸门（不吃 24h 冷却），其余走宿主完整闸门
            if trg.needs_gate:
                ok, why = self._relation_gate(cid, t)
            else:
                ok, why = self._light_gate(t)
            if not ok:
                logger.info("[proactive_pack] 闸门拦下：%s（kind=%s, why=%s）", trg.kind, trg.kind, why)
                return False

            prompt = prompts.build_prompt(trg, sit.text, self._name())
            # 生成 + 审查闭环：不合格由审查器给出理由 → 回灌重写（上限 judge_attempts）
            text, passed = await self._generate_with_review(trg, prompt, sit.text)
            if not passed or not text:
                return False

            await self._deliver(cid, trg, text, t)
            self._mark_sent(cid, trg, t)
            logger.info("[proactive_pack] 已开口 kind=%s | %s", trg.kind, text)
            return True
        except Exception:  # noqa: BLE001 本包任何异常都不得影响宿主主链路
            logger.warning("[proactive_pack] maybe_emit 异常（本次不开口）", exc_info=True)
            return False

    # ---- 只读辅助（调试用） ----

    def dry_run(self, cid: str = "danya", now: Optional[float] = None,
                last_input_ts: Optional[float] = None) -> dict:
        """不生成、不派发，只回"此刻会命中哪一类 + 情境包长什么样"。"""
        t = time.time() if now is None else now
        li = float(last_input_ts if last_input_ts is not None else (t - 6 * 3600))
        sit = collect(cid, now=t, last_input_ts=li, base_dir=self.base_dir,
                      flags=self.flags, marks=self.marks)
        ctx = Ctx(cid=cid, now=t, last_input_ts=li, flags=self.flags, marks=self.marks,
                  sit=sit, base_dir=self.base_dir)
        trg = evaluate(ctx)
        return {"trigger": (trg.kind if trg else None),
                "brief": (trg.brief if trg else None),
                "situation": sit.text, "data": sit.data}
