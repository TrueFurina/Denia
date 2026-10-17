# -*- coding: utf-8 -*-
"""【P1·2026-09-24】级联大脑：用外接 LLM（阿里云百炼 / 任意 OpenAI 兼容端点）产出通话回复。

设计原则
--------
1. **完全独立**：不 import 本仓任何双工代码，可单测、可随时摘掉。
   双工引擎仍在跑（负责听、打断、情绪），本模块只负责「说什么」。
2. **流式成句即吐**：收到标点就回调一次，让 TTS 提前开工，不等整句生成完。
3. **人格来自现成人设文件**：复用 prompts/call_profiles/*.txt，与双工档同源，
   避免两套人格各写各的（这是 §2ct 踩过的坑）。
4. **零配置也能跑**：默认从 config.yaml 的 dashscope Key 取，Key 缺失时直接
   返回空串，绝不抛异常打断通话（取不到必须返回「没有」，不是吞异常）。

回滚：把 config.yaml 的 duplex_call_mode 改回 duplex 即完全走回原链路，
本文件不会被 import（import 点由开关门控）。
"""
from __future__ import annotations

import os
import re
import time
import json
import logging
from typing import Optional, Callable, Awaitable, List, Dict, Any

logger = logging.getLogger(__name__)

# 硬句末标点（不含 ～：达妮娅口语爱用 ～ 收尾，不能当断句点）
_HARD_TERM = "。！？!?；;\n"
# 软句末（连续两句后才可切，避免把 "嗯，然后呢" 切碎）
_SOFT_TERM = "，,~～、"
_MIN_SENT_CHARS = 8

_DEFAULT_RULES = """【这通电话怎么打】
- 他说话的时候你听着，听完再接；他话没说完，别插嘴。
- 他一停下来、或者是在问你，你就立刻接上，别让他干等。
- 接着他的话往下说，别自己另起一个话题。

【话怎么答】
- 先正面回答他问的那件事，别绕开，也别把问题原样丢回去。
- 一次只说一句，十到四十字，像打电话那样口语、自然。
- 答完可以带一句你自己的反应，或反过来问他一句。
- 一句话里只说一件事，别一句套一句，也别自问自答。
- 绝不要把他刚说过的话原样重复一遍。
- 他早就知道你的名字和学院，除非他问，否则不要自我介绍。

【记住】
- 轮到你时必须出声；他喊你、问你，一声不响是最糟的回应。
- 不啰嗦、不套话、不复读、不写括号动作描写。
- 只输出你要说的话本身，不要任何前缀、标签或解释。"""


def _read(path: str) -> str:
    try:
        if path and os.path.exists(path):
            return open(path, 'r', encoding='utf-8', errors='ignore').read().strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


class CascadeBrain:
    """外接 LLM 大脑：一次用户话 -> 流式回复（按句回调）。"""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        persona_path: str = "",
        behavior_path: str = "",
        history_turns: int = 6,
        max_tokens: int = 90,
        temperature: float = 0.85,
        timeout_s: float = 20.0,
        extra_rules: str = "",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.history_turns = max(0, int(history_turns or 0))
        self.max_tokens = int(max_tokens or 90)
        self.temperature = float(temperature or 0.85)
        self.timeout_s = float(timeout_s or 20.0)
        self._history: List[Dict[str, str]] = []
        self._last_call_ts = 0.0
        self._fail_count = 0
        # 电脑控制（单模型 agent）：默认不挂工具，仅云端开启时由 set_agent 注入。
        # 保持本模块独立——不 import 本仓双工/agent 代码，dispatch/on_confirm/on_status
        # 均由调用方注入，工具 schema 也由调用方传入（OpenAI function-calling 格式）。
        self._agent_dispatch: Optional[Callable] = None
        self._agent_tools: List[Dict[str, Any]] = []
        self._agent_on_confirm: Optional[Callable[[str], Awaitable[bool]]] = None
        self._agent_on_status: Optional[Callable[[str], Awaitable[None]]] = None
        self._agent_destructive: set = set()
        self.max_tool_rounds: int = 6
        # 系统提示词：人设文件 + 行为文件 + 实时规则（末尾权重最高）
        persona = _read(persona_path)
        behavior = _read(behavior_path)
        parts = [p for p in (persona, behavior, extra_rules or _DEFAULT_RULES) if p]
        self.system_prompt = "\n\n".join(parts)
        self.enabled = bool(self.base_url and self.api_key and self.model)
        if not self.enabled:
            logger.warning("级联大脑未启用（base_url/api_key/model 有空），将完全不产出")

    # ---------- 历史 ----------
    def reset_history(self) -> None:
        self._history = []

    def note_user(self, text: str) -> None:
        self._push("user", text)

    def note_assistant(self, text: str) -> None:
        self._push("assistant", text)

    def _push(self, role: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        self._history.append({"role": role, "content": t})
        n = self.history_turns * 2
        if n > 0 and len(self._history) > n:
            self._history = self._history[-n:]

    def build_messages(self, user_text: str) -> List[Dict[str, str]]:
        msgs: List[Dict[str, str]] = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self._history)
        msgs.append({"role": "user", "content": (user_text or "").strip()})
        return msgs

    # ---------- 电脑控制（单模型 agent） ----------
    def set_agent(
        self,
        dispatch: Optional[Callable] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_confirm: Optional[Callable[[str], Awaitable[bool]]] = None,
        on_status: Optional[Callable[[str], Awaitable[None]]] = None,
        destructive_tools: Optional[set] = None,
        max_rounds: int = 6,
        add_persona_rule: bool = True,
    ) -> None:
        """注入电脑控制能力（仅云端开启时调用）。

        - dispatch(tool_name, args_dict) -> str：实际执行工具，返回结果文本。
        - tools：OpenAI function-calling schema 列表（来自 agent_tools.TOOL_SPECS）。
        - on_confirm(desc) -> bool：高危工具执行前确认（返回 False 即拒绝）；不传则跳过确认。
        - on_status(desc)：进度回调（用于聊天气泡 / agent_status 广播）。
        - destructive_tools：需要确认的工具名集合。
        - add_persona_rule：是否在系统提示词末尾追加「你能操作电脑」的规则（避免闲聊误触发）。
        """
        self._agent_dispatch = dispatch
        self._agent_tools = list(tools or [])
        self._agent_on_confirm = on_confirm
        self._agent_on_status = on_status
        self._agent_destructive = set(destructive_tools or set())
        self.max_tool_rounds = max(1, int(max_rounds or 6))
        if add_persona_rule and self._agent_tools:
            rule = ("\n\n【你能操作这台电脑】\n"
                    "- 你拥有若干电脑操作工具（打开软件、按键、点击、读写文件、看屏幕等）。\n"
                    "- 仅当用户明确要求你操作电脑（如“帮我打开记事本/调音量/找文件”）时才调用工具；"
                    "普通闲聊绝不要调用任何工具，照常以达妮娅的身份自然聊天即可。\n"
                    "- 操作前先用一句口语告诉用户你要做什么（例如“我帮你打开记事本”）。")
            if rule not in self.system_prompt:
                self.system_prompt = (self.system_prompt + rule).strip()

    def clear_agent(self) -> None:
        """卸下电脑控制能力（本地模式 / 关闭时）。"""
        self._agent_dispatch = None
        self._agent_tools = []

    async def stream_reply(
        self,
        user_text: str,
        on_sentence: Optional[Callable[[str], Awaitable[None]]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> str:
        """流式产出回复；每凑够一句就 await on_sentence(句)。

        返回完整文本（便于写记录）。失败/未启用时返回空串——**绝不抛异常**。

        若已注入电脑控制能力（set_agent），则走「工具调用循环」：模型自己决定
        闲聊还是操作——想操作就吐 tool_call，否则正常回文本。单模型、无独立裁判。
        """
        user_text = (user_text or "").strip()
        if not user_text or not self.enabled:
            return ""
        if self._agent_dispatch and self._agent_tools:
            return await self._stream_with_tools(user_text, on_sentence, should_abort)
        return await self._stream_plain(user_text, on_sentence, should_abort)

    async def _stream_plain(
        self,
        user_text: str,
        on_sentence: Optional[Callable[[str], Awaitable[None]]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> str:
        # —— 原 stream_reply 的纯对话实现（无工具） ——
        full = ""
        buf = ""
        t0 = time.time()
        try:
            import httpx
        except Exception:  # noqa: BLE001
            logger.error("级联大脑需要 httpx，但未安装")
            return ""
        body = {
            "model": self.model,
            "stream": True,
            "messages": self.build_messages(user_text),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        headers = {"Authorization": "Bearer " + self.api_key,
                   "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as cli:
                async with cli.stream("POST", f"{self.base_url}/chat/completions",
                                      json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        txt = await resp.aread()
                        logger.error("级联大脑 HTTP %s: %s", resp.status_code,
                                     txt[:200].decode('utf-8', 'ignore'))
                        self._fail_count += 1
                        return ""
                    async for raw in resp.aiter_lines():
                        if should_abort is not None and should_abort():
                            logger.info("级联大脑: 用户打断，中止生成")
                            break
                        line = (raw or "").strip()
                        if not line.startswith("data:"):
                            continue
                        d = line[5:].strip()
                        if d == "[DONE]":
                            break
                        try:
                            j = json.loads(d)
                        except Exception:  # noqa: BLE001
                            continue
                        try:
                            _u = j.get("usage")
                            if _u and _u.get("total_tokens"):
                                from .quota_meter import record as _qm_record
                                _qm_record(self.model, _u["total_tokens"], self.base_url)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            delta = j["choices"][0]["delta"].get("content")
                        except Exception:  # noqa: BLE001
                            delta = None
                        if not delta:
                            continue
                        full += delta
                        buf += delta
                        sent = self._cut_sentence(buf)
                        if sent and on_sentence is not None:
                            try:
                                await on_sentence(sent)
                            except Exception:  # noqa: BLE001
                                logger.debug("级联大脑: on_sentence 回调异常", exc_info=True)
                        if sent:
                            buf = buf[len(sent):]
        except Exception as e:  # noqa: BLE001
            self._fail_count += 1
            logger.warning("级联大脑调用失败(%s): %s", type(e).__name__, e)
        tail = self._finalize(buf)
        if tail and on_sentence is not None:
            try:
                await on_sentence(tail)
            except Exception:  # noqa: BLE001
                logger.debug("级联大脑: 尾句回调异常", exc_info=True)
        if full.strip():
            self.note_assistant(full)
        dt = time.time() - t0
        logger.info("级联大脑: %.2fs 产出 %d 字（%s）", dt, len(full), self.model)
        return full

    async def _stream_with_tools(
        self,
        user_text: str,
        on_sentence: Optional[Callable[[str], Awaitable[None]]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> str:
        """工具调用循环：模型自己判断闲聊 / 操作。多轮直到产出纯文本回复。"""
        try:
            import httpx
        except Exception:  # noqa: BLE001
            logger.error("级联大脑需要 httpx，但未安装")
            return ""
        messages = self.build_messages(user_text)
        last_text = ""
        for _round in range(self.max_tool_rounds):
            if should_abort is not None and should_abort():
                break
            body = {
                "model": self.model,
                "stream": True,
                "messages": messages,
                "tools": self._agent_tools,
                "tool_choice": "auto",
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }
            headers = {"Authorization": "Bearer " + self.api_key,
                       "Content-Type": "application/json"}
            content_parts: List[str] = []
            # index -> {"id","name","args"}
            calls_acc: Dict[int, Dict[str, Any]] = {}
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s) as cli:
                    async with cli.stream("POST", f"{self.base_url}/chat/completions",
                                          json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            txt = await resp.aread()
                            logger.error("级联大脑(agent) HTTP %s: %s", resp.status_code,
                                         txt[:200].decode('utf-8', 'ignore'))
                            self._fail_count += 1
                            return last_text
                        async for raw in resp.aiter_lines():
                            if should_abort is not None and should_abort():
                                break
                            line = (raw or "").strip()
                            if not line.startswith("data:"):
                                continue
                            d = line[5:].strip()
                            if d == "[DONE]":
                                break
                            try:
                                j = json.loads(d)
                            except Exception:  # noqa: BLE001
                                continue
                            try:
                                _u = j.get("usage")
                                if _u and _u.get("total_tokens"):
                                    from .quota_meter import record as _qm_record
                                    _qm_record(self.model, _u["total_tokens"], self.base_url)
                            except Exception:  # noqa: BLE001
                                pass
                            try:
                                delta = j["choices"][0]["delta"]
                            except Exception:  # noqa: BLE001
                                delta = None
                            if not delta:
                                continue
                            if delta.get("content"):
                                piece = delta["content"]
                                content_parts.append(piece)
                                # 流式吐字（边操作边念）
                                buf_local = "".join(content_parts)
                                sent = self._cut_sentence(buf_local)
                                if sent and on_sentence is not None:
                                    try:
                                        await on_sentence(sent)
                                    except Exception:  # noqa: BLE001
                                        logger.debug("级联大脑: on_sentence 异常", exc_info=True)
                            for tc in (delta.get("tool_calls") or []):
                                idx = tc.get("index", 0)
                                slot = calls_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["args"] += fn["arguments"]
            except Exception as e:  # noqa: BLE001
                self._fail_count += 1
                logger.warning("级联大脑(agent)调用失败(%s): %s", type(e).__name__, e)
                return last_text
            content = "".join(content_parts)
            last_text = content
            calls = [{"id": v["id"], "type": "function",
                      "function": {"name": v["name"], "arguments": v["args"]}}
                     for v in calls_acc.values() if v.get("name")]
            if not calls:
                # 纯文本回复（已流式吐出）
                if content.strip():
                    self.note_assistant(content)
                return content
            # 有工具调用：把 assistant 消息（含 tool_calls）写入上下文
            asst_msg: Dict[str, Any] = {"role": "assistant", "tool_calls": calls}
            if content:
                asst_msg["content"] = content
            messages.append(asst_msg)
            # 逐个执行
            for tc in calls:
                fname = tc["function"]["name"]
                try:
                    fargs = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:  # noqa: BLE001
                    fargs = {}
                if self._agent_on_status is not None:
                    try:
                        await self._agent_on_status(f"准备执行：{fname}({fargs})")
                    except Exception:  # noqa: BLE001
                        pass
                # 高危确认闸
                if self._agent_on_confirm is not None and fname in self._agent_destructive:
                    try:
                        allowed = bool(await self._agent_on_confirm(f"{fname}({fargs})"))
                    except Exception:  # noqa: BLE001
                        allowed = False
                    if not allowed:
                        result = "用户拒绝了该操作，请勿执行。"
                        if self._agent_on_status is not None:
                            try:
                                await self._agent_on_status("用户已取消该操作")
                            except Exception:  # noqa: BLE001
                                pass
                        messages.append({"role": "tool", "tool_call_id": tc["id"],
                                         "content": result})
                        continue
                try:
                    result = await self._agent_dispatch(fname, fargs)
                except Exception as e:  # noqa: BLE001
                    result = f"工具执行异常：{e}"
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": str(result)})
            # 继续下一轮，让模型根据工具结果决定下一步或最终回复
        # 超过最大轮次：返回已累积文本（可能为空）
        return last_text

    # ---------- 断句 ----------
    @staticmethod
    def _cut_sentence(buf: str) -> str:
        """从缓冲里切出第一句完整的话；没成型返回空串。"""
        if not buf:
            return ""
        for i, ch in enumerate(buf):
            if ch in _HARD_TERM:
                s = buf[:i + 1].strip()
                if len(s) >= _MIN_SENT_CHARS:
                    # 模型爱用 \n 分行（我们靠它断句），但换行不能念出来
                    return re.sub(r"\s+", " ", s).strip()
                # 太短（如 "嗯。"）不单发，等后面拼
                return ""
        return ""

    @staticmethod
    def _finalize(buf: str) -> str:
        """生成结束时的收尾：把剩余内容清掉思维链/括号后返回。"""
        t = (buf or "").strip()
        if not t:
            return ""
        t = re.sub(r"<think>.*?(?:</think>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"</?think(?:ing)?\s*>", "", t, flags=re.IGNORECASE)
        # 去掉括号动作描写（（歪头）之类）——弱模型爱写，念出来很出戏
        t = re.sub(r"[（(][^（()）]{1,20}[)）]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        # 结尾补句号，避免 TTS 把半句念得像没说完
        if t and t[-1] not in _HARD_TERM + _SOFT_TERM:
            t += "。"
        return t


    @classmethod
    def from_config(cls, cfg: Any, repo_root: str = "") -> "CascadeBrain":
        """从 config 命名空间构造；读不到就用默认值。

        注意：必须是类方法——装配侧用 hasattr(CascadeBrain, 'from_config') 探测，
        写成模块级函数会探测失败并静默降级回双工（2026-09-24 冒烟抓到的真 bug）。
        """
        return _build(cls, cfg, repo_root)


def _build(cls, cfg: Any, repo_root: str = "") -> "CascadeBrain":
    """真正的构造逻辑（供类方法与模块级函数共用）。"""
    base = ""
    key = ""
    model = ""
    persona = behavior = ""
    hist = 6
    mt = 90
    temp = 0.85
    try:
        cas = getattr(cfg, "cascade", None)
        if cas is not None:
            base = (getattr(cas, "base_url", "") or "").rstrip("/")
            key = getattr(cas, "api_key", "") or ""
            model = getattr(cas, "model", "") or ""
            hist = int(getattr(cas, "history_turns", 6) or 6)
            mt = int(getattr(cas, "max_tokens", 90) or 90)
            temp = float(getattr(cas, "temperature", 0.85) or 0.85)
            persona = getattr(cas, "persona_file", "") or ""
            behavior = getattr(cas, "behavior_file", "") or ""
    except Exception:  # noqa: BLE001
        pass
    # 【2026-09-24】相对路径必须按仓库根解析：config 写的是
    # "prompts/call_profiles/danya_persona.txt"，直接 os.path.exists 会按 cwd 找，
    # 进程 cwd 不等于仓库根时就读不到 → 人设整段静默丢失（只剩规则，人格归零）。
    # 这与 §2ct「人格被提示词删掉」同源，判据＝system_prompt 字数（正常 ~785 字）。
    def _abs(p: str) -> str:
        if not p:
            return ""
        return p if os.path.isabs(p) else os.path.join(repo_root, p)

    persona = _abs(persona) or _abs("prompts/call_profiles/danya_persona.txt")
    behavior = _abs(behavior) or _abs("prompts/call_profiles/danya_behavior.txt")
    return cls(base_url=base, api_key=key, model=model,
               persona_path=persona, behavior_path=behavior,
               history_turns=hist, max_tokens=mt, temperature=temp)
