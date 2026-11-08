"""纯文本聊天路由层：把"纯聊天"（无屏幕上下文）从双工网关摘出来，走免费文本端口池。

背景：用户分发软件时，MiniCPM 官方网关只有 14 个共享 worker
（wss://minicpmo45.modelbest.cn）。此前陪伴聊天（self._llm，由 create_llm 创建）也走
这个网关的 chat 端点，与"聊天+看屏幕"双工抢同一池子 → 纯聊天把双工 worker 沾满。
本模块让纯文本聊天改走 OpenAI 兼容的免费文本端口（OpenRouter / 智谱 / 硅基流动 /
本地 Ollama），只有全部失败时兜底回网关 chat()（占 1 个 worker，最后手段）。

用法：create_text_chat_router(cfg) → TextChatRouter，鸭子类型兼容 LLMClient
（chat / chat_stream / generate / available / model_name / vision_describe），供
Dialogue / ActivityReporter / CourseRecorder 等纯文本生成使用。create_llm 已改为返回它。

配置（config.yaml → text_chat）：
  fallback_to_duplex_gateway: true   # 文本端口全挂时兜底回 MiniCPM 网关 chat()
  providers:
    - id: openrouter-free
      name: OpenRouter 免费池
      provider: openai
      base_url: https://openrouter.ai/api/v1
      api_key: ''          # 留空则跳过（去 openrouter.ai 领免费 key，无需绑卡）
      model: openrouter/free
      timeout_s: 30
    - id: zai-glm
      name: 智谱 GLM-4.5-Flash（免费）
      provider: openai
      base_url: https://open.bigmodel.cn/api/paas/v4
      api_key: ''
      model: glm-4.5-flash
    - id: siliconflow
      name: 硅基流动
      provider: openai
      base_url: https://api.siliconflow.cn/v1
      api_key: ''
      model: deepseek-ai/DeepSeek-V3
    - id: local-ollama-text
      name: 本地 Ollama（文本）
      provider: ollama
      model: qwen3:latest
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from ..config import Config
from .base import LLMClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# 单端口失败冷却：冷却期间跳过该端口（治"每次先试坏掉的云端等超时"）。
FAIL_COOLDOWN_S = 60.0
DEFAULT_TIMEOUT_S = 30.0

# 【2026-10-04 18:55 破耦合·provider 别名归一（模块级）】
# 本文件此前两套口径：`_build_client` 只认 `openai`/`openai-compatible`，
# 而"空 key 跳过"保护只认精确 `openai` ⇒ 别名端口失去保护、空 key 空跑并占冷却。
# 与 `vision_router._PROV_ALIAS` 保持同一套归一规则，供全项目复用。
_PROV_ALIAS = {
    "openai": "openai",
    "openai_compatible": "openai",
    "openai-compatible": "openai",
    "dashscope": "openai",
    "aliyun": "openai",
    "bailian": "openai",
}


def _build_client(cfg: Config, item: Any) -> Any:
    """按条目 provider 构造对应客户端（复用 OpenAIClient / OllamaClient）。"""
    provider = str(getattr(item, "provider", "") or "").strip().lower()
    provider = _PROV_ALIAS.get(provider, provider)
    model = str(getattr(item, "model", "") or "").strip()
    if provider == "ollama":
        if not model:
            raise ValueError("ollama 文本模型条目缺少 model")
        return OllamaClient(cfg, model=model)
    if provider == "openai":
        base_url = str(getattr(item, "base_url", "") or "").strip().rstrip("/")
        api_key = str(getattr(item, "api_key", "") or "").strip()
        model_id = model or str(getattr(item, "model", "") or "").strip()
        timeout = float(getattr(item, "timeout_s", 0) or 0) or DEFAULT_TIMEOUT_S
        if not base_url or not model_id:
            raise ValueError("openai 文本模型条目缺少 base_url/model")
        # 纯文本端口显式声明"不看图"：不再继承 `llm.openai.vision`
        # （否则改核心 LLM 的视觉开关会连坐影响文本路由的能力标签）。
        return OpenAIClient(cfg, base_url=base_url, api_key=api_key,
                            model=model_id, timeout_s=timeout, vision=False)
    raise ValueError(f"未知文本聊天 provider: {provider!r}")


def create_text_chat_router(cfg: Config) -> "TextChatRouter":
    """按 config.yaml 的 text_chat 构建纯文本聊天路由。"""
    return TextChatRouter(cfg)


class TextChatRouter(LLMClient):
    """纯文本聊天路由：多候选 + 失败冷却 + 兜底回网关。

    与 VisionRouter 的区别：只服务纯文本 chat（不处理图片），且失败兜底目标是
    MiniCPM 网关 chat()（占 1 个双工 worker，最后手段），目的是把免费文本端口池
    与双工网关解耦——纯聊天尽量不沾双工 worker。
    """

    name = "text-chat-router"

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._entries: list[dict[str, Any]] = []
        self._cooldown_until: dict[str, float] = {}  # id -> 冷却截止时间戳
        self._last_errors: dict[str, str] = {}       # id -> 最近错误
        self._last_used_id: str = ""                 # 最近一次实际成功调用者
        # 【双工占卡时跳过本地端口（2026-09-03 修复打字锁死）】当 local_omni/local
        # 双工正在占用整张 GPU 时，聊天路由跳过本地 ollama 端口（与 Comni 抢卡必超时
        # → 把端口打进冷却 → 连带锁死输入框）。聊天只走云端端口（不占本地 GPU）。
        self._skip_local_when_duplex: bool = False
        tc = getattr(cfg.ns, "text_chat", None)
        self._fallback = bool(tc) and bool(getattr(tc, "fallback_to_duplex_gateway", True))
        # 自动/手动切换模式：auto=自动选可用端口（失败兜底）；manual=严格用指定端口（"改什么是什么"）。
        self._mode = (str(getattr(tc, "mode", "") or "").strip().lower() or "auto")
        if self._mode not in ("auto", "manual"):
            self._mode = "auto"
        self._manual_id = str(getattr(tc, "manual_provider", "") or "").strip()
        if tc is None:
            logger.warning("未配置 text_chat，纯文本聊天将直接兜底回网关")
            return
        for item in (getattr(tc, "providers", None) or []):
            try:
                client = _build_client(cfg, item)
            except Exception as e:  # noqa: BLE001 单条目构造失败不拖垮整个路由
                # 【2026-11-08 质检】模板里「api_key 留空、base_url 也留空」的端口
                # （用户尚未启用）会在 _build_client 因缺 base_url 抛错；旧代码一律按
                # WARNING 报「构造失败」，每次启动刷一条红字，可实际什么都没坏——
                # 空 key 端口本来就该被下面的保护跳过。区分两种情形：没配 key 是
                # 正常未启用（INFO），配了 key 却构造失败才是真故障（WARNING 必须留着，
                # 否则用户会以为端口配好了却始终不生效）。控制流不变，仍 continue。
                if not str(getattr(item, "api_key", "") or "").strip():
                    logger.info("文本聊天端口 %s 尚未启用（api_key 为空），跳过",
                                getattr(item, "id", "?"))
                else:
                    logger.warning("文本聊天条目构造失败: %s", e)
                continue
            _prov = str(getattr(item, "provider", "") or "").strip().lower()
            # 【2026-10-04 18:55 破耦合·provider 别名归一】
            # 此前同一文件里两套口径：`_build_client`(67) 接受 `openai-compatible`，
            # 而这里的"空 key 跳过"保护(121) 只认精确 `openai` ⇒ 用户把端口存成
            # 别名时**跳过保护失效**，空 key 端口照样进池、每次调用空跑并占冷却，
            # 表现为"聊天莫名变慢/失败"。现统一归一。
            _prov = _PROV_ALIAS.get(_prov, _prov)
            # openai 端口需要 key，留空则跳过（避免每次空跑 + 占冷却）
            if _prov == "openai" and not str(getattr(item, "api_key", "") or "").strip():
                logger.info("文本聊天端口 %s 未填 api_key，跳过（去对应平台领免费 key 后启用）",
                            getattr(item, "id", "?"))
                continue
            self._entries.append({
                "id": str(getattr(item, "id", "") or ""),
                "name": str(getattr(item, "name", "") or getattr(item, "id", "")),
                # 回填归一后的 provider，供 `_skip_local_when_duplex` 等按值判断处使用
                "provider": _prov,
                "model": str(getattr(item, "model", "") or ""),
                "client": client,
            })

    # ==================== LLMClient 鸭子接口 ====================
    @property
    def available(self) -> bool:
        # 有任一可用端口，或允许兜底回网关 → 视为可用
        return bool(self._entries) or self._fallback

    def is_any_port_usable(self) -> bool:
        """运行时可用性：是否存在"已知可用"的文本端口。

        与 `available`（配置期布尔，有兜底就永远 True）不同，本方法反映的是
        **当前真实连通状态**——只要任一端口不在"失败冷却"中（即上次调用没把它判死），
        就认为仍有端口可试；当所有端口都进入了冷却（= 全部刚失败），才返回 False，
        前端据此把聊天输入框锁死。

        模式感知：
          - manual：只看「手动指定的那个端口」是否可用（其它端口不算）。
          - auto：任一配置端口可用即 True（无任何端口时仅当兜底网关开启才 True）。

        没有任何配置端口的极端情况：仅当"兜底网关也关闭"（连最后 1 个端口都没有）
        才算真全死；否则网关作为最后端口保留 True，避免启动期误锁。
        """
        if self._mode == "manual":
            e = next((x for x in self._entries if x["id"] == self._manual_id), None)
            if e is None:
                return self._fallback  # 手动端口没配 → 仅兜底网关能顶
            return not self._in_cooldown(e["id"])
        if self._entries:
            if any(not self._in_cooldown(e["id"]) for e in self._entries):
                return True
            # 【fail-open（2026-09-03 修复打字锁死）】所有配置端口都冷却了，但
            # 只要还有兜底网关（fallback_to_duplex_gateway），输入框仍可发——聊天会
            # 兜底回 MiniCPM 网关 chat()（占 1 个双工 worker，最后手段），而不是把
            # 用户彻底锁死。仅当"全端口冷却 且 无兜底网关"才返回 False（真全死）。
            return self._fallback
        return self._fallback

    def set_skip_local_when_duplex(self, flag: bool) -> None:
        """双工(local_omni/local)占用本地 GPU 时置 True：路由跳过 ollama 端口。

        聊天只走云端端口，避免与 Comni 抢卡超时把本地端口打进冷却、连带锁死输入框。
        双工切到云端/关闭（Comni 卸载）时置 False，恢复本地端口作为兜底。
        """
        self._skip_local_when_duplex = bool(flag)
        logger.info("聊天路由 skip_local_when_duplex → %s", self._skip_local_when_duplex)

    def set_mode(self, mode: str, manual_provider: Optional[str] = None) -> None:
        """热切换聊天模型模式（无需重启）。manual 模式需显式指定 manual_provider。

        - auto：自动按冷却顺序试可用端口，失败兜底回网关。
        - manual：严格只用 manual_provider 指定的那一个端口（"改什么是什么"，
          不兜底、不串其它端口）。
        """
        m = (str(mode or "").strip().lower())
        if m not in ("auto", "manual"):
            m = "auto"
        self._mode = m
        if manual_provider is not None:
            self._manual_id = str(manual_provider).strip()
        logger.info("聊天模型切换模式 → %s（manual_provider=%s）", self._mode, self._manual_id or "-")

    @property
    def vision_available(self) -> bool:
        return False  # 纯文本路由不处理视觉

    @property
    def model_name(self) -> Optional[str]:
        e = next((x for x in self._entries if x["id"] == self._last_used_id), None)
        if e:
            return f"text:{e['model']}"
        return "text-chat-router"

    def _in_cooldown(self, eid: str) -> bool:
        return self._cooldown_until.get(eid, 0) > time.time()

    def _mark_fail(self, eid: str, err: str) -> None:
        self._cooldown_until[eid] = time.time() + FAIL_COOLDOWN_S
        self._last_errors[eid] = err

    def _clear_cooldown(self, eid: str) -> None:
        self._cooldown_until.pop(eid, None)
        self._last_errors.pop(eid, None)

    def _manual_entry(self) -> Optional[dict]:
        if not self._manual_id:
            return None
        return next((x for x in self._entries if x["id"] == self._manual_id), None)

    async def chat(self, messages: list[dict], options: Optional[dict] = None) -> str:
        """按模式选端口：manual 严格用指定端口；auto 顺序试可用端口 + 兜底回网关。"""
        if self._mode == "manual":
            return await self._chat_manual(messages, options)
        return await self._chat_auto(messages, options)

    async def _chat_manual(self, messages: list[dict], options: Optional[dict]) -> str:
        """手动模式：只走用户指定的那一个端口，失败即失败，不兜底、不串其它端口。"""
        e = self._manual_entry()
        if e is None:
            if self._fallback:
                # 手动端口没配上，降级走兜底网关（避免"改了个不存在的端口"导致完全发不出）。
                return await self._gateway_fallback_chat(messages, options,
                    [f"手动指定的聊天端口 {self._manual_id!r} 未配置"])
            raise RuntimeError(f"手动指定的聊天端口 {self._manual_id!r} 未配置，且无兜底网关")
        if self._in_cooldown(e["id"]):
            # 【2026-10-13】把「上次为什么失败」带出来。此前只说"冷却中"，
            # 用户无从判断是欠费 / key 错 / 还是模型名不存在 —— 只能干等冷却结束。
            _why = (self._last_errors.get(e["id"]) or "").strip()
            _detail = f"（上次失败：{_why}）" if _why else "（冷却中）"
            raise RuntimeError(
                f"手动指定的聊天端口「{e['name']}」当前不可用{_detail}。"
                f"可稍后重试、在「设置 → 模型与 API」换一个端口，或改用自动模式")
        try:
            text = (await e["client"].chat(messages, options)) or ""
            text = text.strip()
            if not text:
                self._mark_fail(e["id"], "返回空")
                raise RuntimeError(f"手动指定的聊天端口「{e['name']}」返回空")
            self._clear_cooldown(e["id"])
            self._last_used_id = e["id"]
            return text
        except Exception as exc:  # noqa: BLE001
            self._mark_fail(e["id"], str(exc))
            # 【2026-10-19 修复·额度耗尽被当成"普通失败"】
            # 实测：主模型免费额度用尽时服务端返回 403
            #   AllocationQuota.FreeTierOnly / "Free quota exhausted"
            # 旧实现只打一条 "手动聊天端口 X 失败" 就 raise，用户在界面上只看到
            # 一句 HTTP 403，根本不知道"是额度用完了、该换模型"——于是反复重试、
            # 以为是软件坏了。这里复用 vision_router 已在用的共享分类器
            # （error_classify），把 quota/auth 这类"确定性失败"明确区分出来。
            try:
                from .error_classify import classify_error, human_reason, KIND_QUOTA, KIND_AUTH
                _kind = classify_error(str(exc))
            except Exception:  # noqa: BLE001
                _kind = ""
            if _kind in ("quota", "auth"):
                try:
                    _why, _fix = human_reason(str(exc))
                except Exception:  # noqa: BLE001
                    _why, _fix = "模型不可用", "请更换模型或检查 Key"
                logger.error(
                    "聊天模型「%s」(%s) 不可用：%s。建议：%s。"
                    "（可换用实测有额度的模型：qwen3.7-plus / qwen3.6-plus / "
                    "qwen-flash / qwen-plus）原始错误: %s",
                    e["name"], e["model"], _why, _fix, str(exc)[:200])
            else:
                logger.warning("手动聊天端口 %s 失败: %s", e["id"], exc)
            raise

    # ==================== 后台 / 批处理任务的容错调用 ====================
    def last_used_info(self) -> dict:
        """最近一次**成功**调用实际用到的端口（供调用方如实回执"用了哪个模型"）。"""
        eid = self._last_used_id
        e = next((x for x in self._entries if x["id"] == eid), None)
        if not e:
            return {}
        return {"id": e["id"], "name": e["name"], "model": e["model"]}

    async def chat_with_fallback(self, messages: list[dict],
                                 options: Optional[dict] = None) -> str:
        """【2026-10-13】给「后台批处理」类调用（记忆提炼 / 反思聚合 …）的容错入口。

        与 `chat()` 的唯一区别：**手动模式下也允许回退**。
        手动模式"严格只用指定端口、失败即失败"是给**交互式聊天**定的契约
        （设置里选了哪个就必须是哪个，"改什么是什么"）；但记忆提炼这类后台
        任务并不需要精确指定模型 —— 手动端口欠费 / 冷却时直接抛错，用户只会
        看到"提炼失败"而永远拿不到结果。

        顺序：先试手动指定端口（未冷却时）→ 再按配置顺序试其它可用端口 →
        最后才走兜底网关。成功即记入 `_last_used_id`，调用方可用
        `last_used_info()` 如实回执实际用了哪个模型（绝不静默换模型）。
        """
        order: list[dict] = []
        e = self._manual_entry()
        if e is not None:
            order.append(e)
        for x in self._entries:
            if all(y["id"] != x["id"] for y in order):
                order.append(x)
        errors: list[str] = []
        for x in order:
            if self._in_cooldown(x["id"]):
                _w = (self._last_errors.get(x["id"]) or "").strip()
                errors.append(f"{x['name']}: 冷却中" + (f"（{_w}）" if _w else ""))
                continue
            if self._skip_local_when_duplex and x["provider"] == "ollama":
                continue
            try:
                text = (await x["client"].chat(messages, options)) or ""
                text = text.strip()
                if not text:
                    self._mark_fail(x["id"], "返回空")
                    errors.append(f"{x['name']}: 返回空")
                    continue
                self._clear_cooldown(x["id"])
                self._last_used_id = x["id"]
                return text
            except Exception as exc:  # noqa: BLE001
                self._mark_fail(x["id"], str(exc))
                errors.append(f"{x['name']}: {exc}")
                logger.warning("后台任务聊天端口 %s 失败（冷却 %.0fs）: %s",
                               x["id"], FAIL_COOLDOWN_S, exc)
        if self._fallback:
            return await self._gateway_fallback_chat(messages, options, errors)
        raise RuntimeError("所有纯文本聊天端口均不可用: " + " | ".join(errors))

    async def _chat_auto(self, messages: list[dict], options: Optional[dict] = None) -> str:
        """自动模式：按配置顺序尝试纯文本端口，首个成功即用；全失败兜底回网关。"""
        if not self._entries:
            if self._fallback:
                return await self._gateway_fallback_chat(messages, options)
            raise RuntimeError("未配置任何纯文本聊天端口（config.yaml text_chat.providers 为空）")
        errors: list[str] = []
        for e in self._entries:
            if self._in_cooldown(e["id"]):
                continue
            # 双工占卡时跳过本地 ollama 端口（与 Comni 抢卡必超时 → 冷却 → 锁输入框）
            if self._skip_local_when_duplex and e["provider"] == "ollama":
                continue
            try:
                text = (await e["client"].chat(messages, options)) or ""
                text = text.strip()
                if not text:
                    errors.append(f"{e['name']}: 返回空")
                    self._mark_fail(e["id"], "返回空")
                    continue
                self._clear_cooldown(e["id"])
                self._last_used_id = e["id"]
                return text
            except Exception as exc:  # noqa: BLE001
                self._mark_fail(e["id"], str(exc))
                errors.append(f"{e['name']}: {exc}")
                logger.warning("纯文本聊天端口 %s 失败（冷却 %.0fs 后重试）: %s",
                               e["id"], FAIL_COOLDOWN_S, exc)
        if self._fallback:
            return await self._gateway_fallback_chat(messages, options, errors)
        detail = " | ".join(errors)
        raise RuntimeError(f"所有纯文本聊天端口均失败: {detail}")

    def supports_stream(self) -> bool:
        """只要存在支持流式的端口（或兜底网关）即声明支持，chat_stream 内部退化保证安全。"""
        if self._mode == "manual":
            e = self._manual_entry()
            if e and getattr(e["client"], "supports_stream", lambda: False)():
                return True
            return bool(self._fallback)
        return any(getattr(e["client"], "supports_stream", lambda: False)() for e in self._entries) or bool(self._fallback)

    async def chat_stream(self, messages: list[dict], options: Optional[dict] = None):
        """流式路由：优先用支持流式的端口逐 chunk 产出；失败/不支持则退化非流式。

        manual 模式：只用指定端口，失败即失败（不兜底）。
        auto 模式：依次尝试支持流式的端口，全失败才退化到非流式 self.chat（含兜底网关）。
        """
        if self._mode == "manual":
            e = self._manual_entry()
            entries = [e] if (e and not self._in_cooldown(e["id"])) else []
        else:
            entries = [
                e for e in self._entries
                if not self._in_cooldown(e["id"])
                and not (self._skip_local_when_duplex and e["provider"] == "ollama")
            ]
        for e in entries:
            client = e["client"]
            if not getattr(client, "supports_stream", lambda: False)():
                continue
            try:
                text = ""
                async for chunk in client.chat_stream(messages, options):
                    text += chunk
                    yield chunk
                if text.strip():
                    self._clear_cooldown(e["id"])
                    self._last_used_id = e["id"]
                    return
                self._mark_fail(e["id"], "流式返回空")
            except Exception as exc:  # noqa: BLE001
                logger.warning("流式聊天端口 %s 失败（退化非流式）: %s", e["id"], exc)
                self._mark_fail(e["id"], str(exc))
                if self._mode == "manual":
                    raise
        # 兜底：非流式（auto 含网关兜底；manual 无则抛错）
        text = await self.chat(messages, options)
        if text:
            yield text

    async def generate(self, messages: list[dict], options: Optional[dict] = None) -> str:
        """dialogue.py 兼容：非流式生成（等价于 chat）。"""
        return await self.chat(messages, options)

    async def vision_describe(self, image_bytes: bytes, prompt: str) -> str:
        return ""  # 纯文本路由不处理视觉

    async def chat_with_tools(self, messages: list[dict], options: Optional[dict],
                             tools: list[dict], on_tool: Optional[Callable[[str, dict], Any]],
                             max_steps: int = 6,
                             on_confirm: Optional[Callable[[str], Any]] = None,
                             require_confirm: bool = False,
                             destructive_tools: Optional[set] = None,
                             on_status: Optional[Callable[[str], Any]] = None,
                             on_token: Optional[Callable[[str], Any]] = None) -> str:
        """带 function-calling 的聊天（"聊天中允许调用工具"用）：在支持工具的端口上跑工具循环。

        - auto 模式：依次尝试支持 chat_with_tools 的端口（跳过不支持/冷却中的），
          首个成功即用；全失败兜底退化为普通 chat（不带工具，至少能正常聊天）。
        - manual 模式：只用指定端口；该端口不支持工具或失败则抛错（不兜底）。
        - 【2026-09-29 修"聊天不理人"】dialogue.py 会传 on_confirm / require_confirm /
          destructive_tools 三个 kwargs（逐步确认闸），本方法此前没接 → 每条消息
          必抛 TypeError → 全部落到"工具调用失败"道歉气泡。现补齐签名并转发；
          老客户端不认识这些 kwargs 时由下方 except 兜住，走下一端口/普通聊天降级。
        - 【2026-10-02】on_status：Agent 执行进度回调（"正在执行：xxx"状态气泡），
          透传给端口客户端；老端口不认识时由 except 兜住走下一端口。
        """
        if self._mode == "manual":
            e = self._manual_entry()
            entries = [e] if (e and not self._in_cooldown(e["id"])) else []
        else:
            entries = [
                e for e in self._entries
                if not self._in_cooldown(e["id"])
                and not (self._skip_local_when_duplex and e["provider"] == "ollama")
            ]
        for e in entries:
            client = e["client"]
            # 仅 OpenAI 兼容等支持 function calling 的端口能跑工具循环
            if not hasattr(client, "chat_with_tools"):
                continue
            try:
                _fwd = dict(on_confirm=on_confirm,
                            require_confirm=require_confirm,
                            destructive_tools=destructive_tools)
                if on_status is not None:
                    _fwd["on_status"] = on_status
                if on_token is not None:
                    _fwd["on_token"] = on_token
                text = (await client.chat_with_tools(
                    messages, options, tools, on_tool, max_steps, **_fwd)) or ""
                text = text.strip()
                if not text:
                    self._mark_fail(e["id"], "工具聊天返回空")
                    continue
                self._clear_cooldown(e["id"])
                self._last_used_id = e["id"]
                return text
            except Exception as exc:  # noqa: BLE001
                logger.warning("工具聊天端口 %s 失败: %s", e["id"], exc)
                self._mark_fail(e["id"], str(exc))
                if self._mode == "manual":
                    raise
        # 兜底：没有端口支持工具 → 退化为普通 chat（不带工具，至少能正常对话）
        logger.warning("无端口支持工具调用，退化为普通聊天（allow_tools_in_chat 降级）")
        return await self.chat(messages, options)

    # ==================== 兜底 ====================
    async def _gateway_fallback_chat(self, messages: list[dict], options: Optional[dict],
                                     errors: Optional[list[str]] = None) -> str:
        """最后手段：兜底回 MiniCPM chat() 客户端。

        【2026-10-02 改·不再静默连废弃的免费网关】原实现 `MiniCPMClient(self._cfg)`
        在未配 llm.minicpm.endpoint 时会回落到已废弃的免费网关 wss://minicpmo45.modelbest.cn
        （实测 WS 1225 恒失败）→ 纯文本端口全挂时，这里必然再失败一次，且错误信息
        指向"网关"而非真实原因，把排查带偏。

        现改为：**只允许用用户自配端点**兜底；没有自配端点就明确抛错（附上各端口的
        真实失败原因），让上层弹失败气泡提示用户去设置里修，而不是静默连共享服务。
        """
        detail = f" | {errors}" if errors else ""
        try:
            _llm_ns = getattr(getattr(self._cfg, "ns", None), "llm", None)
            _ep = str(getattr(getattr(_llm_ns, "minicpm", None), "endpoint", "") or "").strip()
            if not _ep:
                raise RuntimeError(
                    "所有纯文本聊天端口均失败，且未配置自备兜底端点"
                    "（已拒绝回落到已废弃的免费 MiniCPM 网关）。"
                    f"请到设置里检查各端口的 Key/网络。各端口错误：{errors or '无'}")
            logger.warning("纯文本聊天端口全部失败，用自配端点兜底 chat()%s", detail)
            from .minicpm_client import MiniCPMClient
            client = MiniCPMClient(self._cfg, endpoint=_ep)
            text = (await client.chat(messages, options)) or ""
            if not text.strip():
                # 兜底返回空不抛异常会被 dialogue 静默吞掉（用户"啥也不回"）。
                # 显式当失败，让上层发失败气泡，而不是假装成功返回空串。
                raise RuntimeError("自配端点兜底返回空")
            return text
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"纯文本聊天端口全失败且兜底也失败: {exc}") from exc

    # ==================== 状态快照 ====================
    def snapshot(self) -> dict:
        """运行状态快照（供 webapp / UI 显示当前走了哪个端口、是否有冷却）。"""
        now = time.time()
        manual = self._manual_entry()
        return {
            "entries": [
                {
                    "id": e["id"],
                    "name": e["name"],
                    "provider": e["provider"],
                    "model": e["model"],
                    "cooldown_left": max(0.0, self._cooldown_until.get(e["id"], 0) - now),
                    "last_error": self._last_errors.get(e["id"], ""),
                }
                for e in self._entries
            ],
            "fallback": self._fallback,
            "last_used": self._last_used_id,
            "usable": self.is_any_port_usable(),
            "mode": self._mode,
            "manual_provider": self._manual_id,
            "manual_name": manual["name"] if manual else "",
            # 设置页手动模式下拉可选列表（id/name 即可）。
            "selectable": [
                {"id": e["id"], "name": e["name"], "model": e["model"]}
                for e in self._entries
            ],
        }
