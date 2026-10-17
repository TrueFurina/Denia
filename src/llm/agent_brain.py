# -*- coding: utf-8 -*-
"""【AGENT·2026-09-27】电脑操作代理大脑：带 function-calling 循环的外接 LLM。

它不是自己操作电脑，而是"决定调哪个工具、按什么顺序调"，工具在 agent_tools.py。
本质就是用户说的"网上开源的、让 AI 去调用它的 API"——这里把开源 browser-use /
pyautogui / VLM 等封装成 OpenAI 格式的 tools，交给支持 function calling 的 LLM（qwen-plus）
来调度。

设计原则（对齐 cascade_brain：完全独立、可单测、取不到就返回、绝不抛异常）
----------------------------------------------------------------
- 复用 config.yaml 的 `cascade` 外接 LLM 端点（qwen-plus，已验证支持 tools）。
  也可在 `agent.llm` 单独指定更强的模型（function calling 越强越好）。
- 工具循环：调 LLM → 若返回 tool_calls 就逐个执行并把结果喂回 → 直到模型给出最终
  自然语言回复（最多 max_steps 步，防止失控）。
- 旁白（on_speak）：LLM 在"要调工具"的那一轮里返回的文字（口头确认/进度）会经 on_speak
  念出来；最终结论由调用方经 TTS 念出。把 LLM/VLM 的"思考"与 TTS 串成一条链。
- 人设注入：system 提示前置达妮娅人设（复用 cascade 的 persona_file/behavior_file），
  聊天模式（route_chat）下也保持角色口吻。
- 失败/未启用时返回说明字符串，绝不抛异常打断通话。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 破坏性 / 高危工具：require_confirm=true 时，执行前必须先经 on_confirm 钩子确认。
# 覆盖所有会"直接对真实系统产生不可逆或干扰性动作"的工具（键鼠、命令行、文件写/剪贴板）。
# 注意：须与 agent_tools.TOOL_SPECS 的工具名保持一致；低风险的 set_volume/browse_web/
# search_web 不在此列（浏览器操作另有 browser.enabled 独立开关）。
_DESTRUCTIVE_TOOLS = {
    "click_at", "mouse_move", "mouse_scroll", "key_press", "type_text",
    "ui_click", "ui_type", "play_song",
    "open_app", "focus_window", "media_control", "run_command",
    "write_file", "append_file", "write_clipboard",
}

# 【P0-1 验证回路】会改变界面/系统状态、执行后"凭调用没报错≠真的成功"的工具集合。
# 这些工具调用成功后，run() 会注入一条自检提示，要求模型在继续/汇报前先复核状态。
_STATE_CHANGING_TOOLS = {
    "open_app", "focus_window", "click_at", "mouse_move", "mouse_scroll",
    "type_text", "key_press", "ui_click", "ui_type", "run_command",
    "browse_web", "media_control", "set_volume", "write_clipboard",
    "write_file", "append_file", "play_song",
}

_BASE_PROMPT = """你是「达妮娅」的电脑操作代理。用户用中文要求你操作这台电脑（打开软件、放歌、\
刷视频、搜索资料、点击、输入文字、执行命令、看屏幕等）。

规则：
1. 把用户的请求拆成工具调用步骤，能用工具完成就一定要调用工具，不要只说"好的我帮你"。
2. 工具可用：open_app（打开应用/网页/网址）、media_control（播放暂停/上下首/音量）、
   see_screen（截图后用视觉大模型"看"屏幕并回答）、screenshot（截图存档）、
   list_windows/focus_window（窗口枚举/聚焦）、key_press（按键/组合键）、
   【UI 自动化】ui_inspect（枚举窗口内按钮/输入框等控件树）、ui_click（按控件名称精确点击）、ui_type（聚焦输入框输入文字）——这三个基于 Windows 原生控件识别，比 click_at 坐标更可靠、且不需要 VLM；
   type_text/click_at/mouse_move/mouse_scroll（键鼠，作为 UI 自动化找不到控件时的兜底）、read_clipboard/write_clipboard（剪贴板）、
   read_file/write_file/append_file/file_search（sandbox 内文件）、
   set_volume/get_volume（系统音量）、browse_web（让 AI 自动操作浏览器）、
   search_web（联网搜索）、run_command（命令行，需白名单）、system_info、wait、
   play_song（在音乐 App 里放某首歌的一站式指令：打开→搜→播）。
2.1 点击/输入【优先用 UI 自动化】：先用 ui_inspect 看清目标窗口有哪些可点击/可输入的控件，再用 ui_click/ui_type 按控件名称精确操作。只有当 ui_click/ui_type 报告"找不到控件"（多见于未开启无障碍的 Electron 应用）时，才退回 see_screen 截图 + click_at/type_text 的坐标方式。不要一上来就用 click_at 盲点坐标——那是最不可靠的路径。
3. 放歌：首选 play_song(app=用户【指定】的音乐软件, song=歌名) 一站式完成（它会自动打开 App、搜歌、点播放）；用户说哪个 App 就用哪个（如汽水音乐/网易云音乐/QQ音乐/Spotify），用户没指定时优先本机已安装的『汽水音乐』，不要默认网易云。
   3.1 只打开 App 不等于放歌成功。play_song 有三种结果：⑥「已确认正在播放」才算成功；
   返回「未确认播放」或「未对 UI 开放控件/无法用 UI 自动化完成」时，**必须立刻改走视觉路径**：
   see_screen 看屏 → click_at 点搜索框 → type_text 输入歌名 → 回车 → 再看屏点第一条结果的播放按钮，最后再 see_screen 复核一次是否真的在播。
   3.2 严禁凭「App 已经打开了」或「工具没报错」就汇报『已经帮你播放了』；没确认在播就照实说卡在哪一步、需要用户手动点一下什么。
   刷视频：用 browse_web 打开 bilibili/抖音 并搜索播放（browser-use 未装时退化为打开链接）。
   找不到按钮/不确定界面：先用 ui_inspect 看控件；UI 自动化也找不到时再用 see_screen 截图让视觉模型帮你看，再根据回答 click_at 点击。
4. 每步只做一件具体的事；多步任务就连续调用多个工具。操作之间可用 wait 留间隔。
5. 在"决定调用工具"的那一轮里，先用一句中文自然地说出你打算做什么（会被念出来给用户听），
   然后再调用工具；不要静默操作。
6. 全部工具执行完后，用一句自然中文向用户汇报结果（做了什么、结果如何），不要编造没做到的事。
7. 如果用户只是闲聊/问候、不需要操作电脑，就正常用达妮娅的口吻聊天，不要强行调用工具。
8. 如果某件事工具做不到，直接说明限制，不要假装完成。
9. 执行时严格按前面「规划阶段」给出的分步计划推进；若实际界面与计划不符，先复核再调整，不要盲走。简单任务（一步就能完成）无需多步计划，直接执行即可。
10. 动作后必须复核：每完成一个会改变界面的动作（点击/输入/打开/切换窗口/执行命令），在继续下一步或最终汇报前，必须先复核该动作是否真的生效——优先用 ui_inspect 看控件是否变化，必要时用 see_screen 截图让视觉模型确认。绝不要凭"调用没报错"就假设成功；未生效就重试或换方案，不得在未确认的情况下汇报"已完成"。"""


def _repo_root() -> str:
    """向上找到含 config.yaml 的目录（即工程根）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.exists(os.path.join(d, "config.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


def _find_config() -> str:
    return os.path.join(_repo_root(), "config.yaml")


_CFG_CACHE: dict = {}


def _load_cfg() -> Any:
    """懒加载 Config（cfg=None 时）。带缓存，避免每次调用都解析。"""
    if _CFG_CACHE.get("cfg") is not None:
        return _CFG_CACHE["cfg"]
    try:
        import yaml  # type: ignore
        from ..config import Config
        path = _find_config()
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        cfg = Config(raw, path)
        _CFG_CACHE["cfg"] = cfg
        return cfg
    except Exception as e:  # noqa: BLE001
        logger.error("AGENT 懒加载 config 失败: %s", e)
        return None


def build_agent_ctx(cfg: Any, repo_root: str = "") -> dict:
    """构造 dispatch_tool 所需的 ctx（供聊天工具调用复用，避免重复计算）。

    与 AgentBrain.from_config 里拼 ctx 的逻辑一致：解析 agent.llm 或兜底 cascade
    的 base_url/api_key/model（看屏/浏览器工具要用），并带走 allow_cmd /
    require_confirm / allow_commands / vision_model / repo_root。
    """
    agent = getattr(cfg.ns, "agent", None) if cfg is not None else None
    base = key = model = ""
    llm_cfg = getattr(agent, "llm", None) if agent else None
    if llm_cfg is not None and getattr(llm_cfg, "api_key", ""):
        base = (getattr(llm_cfg, "base_url", "") or "").strip().rstrip("/")
        key = getattr(llm_cfg, "api_key", "") or ""
        model = getattr(llm_cfg, "model", "") or ""
    if not (base and key and model):
        cas = getattr(cfg.ns, "cascade", None) if cfg is not None else None
        base = (getattr(cas, "base_url", "") or "").rstrip("/")
        key = getattr(cas, "api_key", "") or ""
        model = getattr(cas, "model", "") or ""
    allow_cmd = bool(getattr(agent, "allow_command", False)) if agent else False
    allow_cmds = list(getattr(agent, "allow_commands", []) or []) if agent else []
    require_confirm = bool(getattr(agent, "require_confirm", False)) if agent else False
    vision_model = (getattr(agent, "vision_model", "") or "").strip() if agent else ""
    plan_first = bool(getattr(agent, "plan_first", True)) if agent else True
    verify_actions = bool(getattr(agent, "verify_actions", True)) if agent else True
    search_provider = (getattr(agent, "search_provider", "") or "duckduckgo") if agent else "duckduckgo"
    search_fallback = list(getattr(agent, "search_fallback", []) or []) if agent else []
    _vlm = _vision_endpoint(cfg)          # 统一视觉端点（能力槽维护）→ Agent 看屏优先用它
    return {
        "allow_cmd": allow_cmd,
        "allow_cmds": allow_cmds,
        "require_confirm": require_confirm,
        "base_url": base,
        "api_key": key,
        "model": model,
        "vision_model": vision_model,
        "vlm_base_url": _vlm[0],
        "vlm_api_key": _vlm[1],
        "vlm_model": _vlm[2],
        "plan_first": plan_first,
        "verify_actions": verify_actions,
        "search_provider": search_provider,
        "search_fallback": search_fallback,
        "repo_root": repo_root or _repo_root(),
    }


def _vision_endpoint(cfg: Any) -> tuple:
    """统一的「看图 / 看屏」视觉端点 → (base_url, api_key, model)；读不到返回 ("","","")。

    为什么需要（2026-10-13 收敛）：Agent 看屏原本只读 `agent.vision_model` —— 那是**历史遗留的
    独立配置节点、没有 UI、用户改不了**。于是会出现「设置在能力槽里换成智谱免费视觉，
    Agent 看屏却还在用另一个模型（agent.vision_model）」的分叉。
    这里以能力槽统一维护的 `vision_models.active` 为准；调用方读不到时才回落旧配置。
    """
    raw = getattr(cfg, "raw", None) if cfg is not None else None
    if not isinstance(raw, dict):
        return "", "", ""
    vm = raw.get("vision_models") or {}
    models = vm.get("models") or []
    act = str(vm.get("active") or "")
    ent = None
    for e in models:
        if isinstance(e, dict) and str(e.get("id") or "") == act:
            ent = e
            break
    if ent is None:                                  # active 指向的条目没了 → 取第一个有 model 的
        for e in models:
            if isinstance(e, dict) and str(e.get("model") or "").strip():
                ent = e
                break
    if not isinstance(ent, dict):
        return "", "", ""
    m = str(ent.get("model") or "").strip()
    if not m:
        return "", "", ""
    return (str(ent.get("base_url") or "").strip().rstrip("/"),
            str(ent.get("api_key") or "").strip(), m)


def _load_persona_text(cfg: Any) -> str:
    """读人设 + 行为规范，拼成一段文本前置到 system 提示（复用 cascade 的配置路径）。"""
    agent = getattr(cfg.ns, "agent", None) if cfg is not None else None
    pf = bf = ""
    if agent:
        pf = getattr(agent, "persona_file", "") or ""
        bf = getattr(agent, "behavior_file", "") or ""
    if not pf:
        cas = getattr(cfg.ns, "cascade", None) if cfg is not None else None
        if cas:
            pf = getattr(cas, "persona_file", "") or ""
            bf = getattr(cas, "behavior_file", "") or ""
    root = _repo_root()
    parts: list[str] = []
    for p in (pf, bf):
        if p:
            try:
                with open(os.path.join(root, p), "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
            except Exception:  # noqa: BLE001
                pass
    return "\n\n".join(parts)


class AgentBrain:
    """电脑操作代理大脑：一次自然语言指令 -> 多步工具调用 -> 一句话结果。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 enabled: bool = True, max_steps: int = 6,
                 temperature: float = 0.0, timeout_s: float = 60.0,
                 max_tokens: int = 1024, ctx: Optional[dict] = None,
                 persona: str = "", plan_first: bool = True,
                 verify_actions: bool = True) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.enabled = enabled
        self.max_steps = max(1, int(max_steps or 6))
        self.temperature = float(temperature)
        self.timeout_s = float(timeout_s or 60.0)
        self.max_tokens = int(max_tokens or 1024)
        self._persona = (persona or "").strip()
        self.plan_first = bool(plan_first)
        self.verify_actions = bool(verify_actions)
        # 传给工具的上下文（LLM 配置 + 安全闸 + 视觉模型）
        self._ctx: dict = dict(ctx or {})
        self._ctx.setdefault("base_url", self.base_url)
        self._ctx.setdefault("api_key", self.api_key)
        self._ctx.setdefault("model", self.model)
        self._ctx["plan_first"] = self.plan_first
        self._ctx["verify_actions"] = self.verify_actions

    def _system_prompt(self) -> str:
        if self._persona:
            return self._persona + "\n\n" + _BASE_PROMPT
        return _BASE_PROMPT

    async def run(self, instruction: str,
                  on_status: Optional[Callable[[str], Any]] = None,
                  on_speak: Optional[Callable[[str], Any]] = None,
                  on_confirm: Optional[Callable[[str], Any]] = None) -> str:
        """执行一条自然语言指令，返回最终回复文本。失败返回说明字符串。

        on_status(text): 进度广播（UI 展示，不念）。
        on_speak(text):  LLM 在"要调工具"那一轮说出的话（口头确认/进度），经 TTS 念出。
        """
        instruction = (instruction or "").strip()
        if not instruction:
            return "没有收到要执行的指令"
        if not self.enabled or not self.base_url or not self.api_key or not self.model:
            return "电脑代理未启用或未配置 LLM（config.yaml 的 agent.enabled 及 cascade LLM）。"
        # 【2026-10-01 A-3】堵"操作大脑记忆孤岛"。
        # 旧实现 messages 从零构造（只有 system + 本次指令）：既读不到用户在打字/语音里
        # 聊过什么（偏好、习惯、约定），操作完也不写回共享历史 —— 于是她在电脑上帮你
        # 放完歌，下一轮聊天已经完全不记得自己干过什么。这里两侧都补上。
        _messages_head: list[dict] = [{"role": "system", "content": self._system_prompt()}]
        _hist_ctx: list[dict] = []
        try:
            from ..companion.conversation_history import shared_history as _sh
            _hist_ctx = _sh().get_recent(8) or []
        except Exception:  # noqa: BLE001
            _hist_ctx = []
        for _h in _hist_ctx[-6:]:
            _t = (_h.get("content") or "").strip()
            if _t:
                _messages_head.append({"role": _h.get("role") or "user", "content": _t})
        _messages_head.append({"role": "user", "content": instruction})
        messages: list[dict] = _messages_head

        async def _remember(answer: str) -> str:
            """把本次「操作指令 → 结果」写回跨通道共享记忆，并提炼可长期记住的用户事实。"""
            try:
                from ..companion.conversation_history import shared_history as _sh2
                _h = _sh2()
                _h.add_turn("user", instruction, source="chat")
                if answer:
                    _h.add_turn("assistant", answer, source="chat")
            except Exception:  # noqa: BLE001
                pass
            # 【2026-10-01 A-4】操作也可能暴露用户偏好（如"以后放歌都用汽水音乐"），
            # 提炼成长期记忆，否则下次聊天已不记得刚帮用户做过什么、用户有什么习惯。
            try:
                await self._extract_operation_memory(instruction, answer)
            except Exception:  # noqa: BLE001
                pass
            return answer

        try:
            import httpx
        except Exception:  # noqa: BLE001
            return "电脑代理需要 httpx（pip install httpx）"
        from .agent_tools import TOOL_SPECS, dispatch_tool, available_tool_specs
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": available_tool_specs(),
            "tool_choice": "auto",
            "stream": False,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as cli:
                # 【P0-2 显式规划】执行前先产出分步计划（低成本、禁止调工具），广播给用户，
                # 并作为上下文注入后续执行循环，让模型"按图施工"而非盲走。
                plan_text = ""
                if self.plan_first:
                    try:
                        plan_body = dict(body)
                        plan_body["tools"] = []
                        plan_body["tool_choice"] = "none"
                        plan_body["max_tokens"] = 400
                        plan_body["messages"] = messages + [{
                            "role": "user",
                            "content": "请把上面的任务拆成简洁的中文分步执行计划：用编号列表，每步一句话说明要调哪个工具/做什么。"
                                       "只输出计划，不要调用任何工具。如果任务很简单（一步就能完成），就直接说『无需多步计划，直接执行』。",
                        }]
                        presp = await cli.post(url, json=plan_body, headers=headers)
                        if presp.status_code == 200:
                            plan_text = (((presp.json().get("choices") or [{}])[0]
                                          .get("message") or {}).get("content") or "").strip()
                    except Exception as e:  # noqa: BLE001
                        logger.debug("规划阶段失败(忽略): %s", e)
                    if plan_text and on_status is not None:
                        try:
                            await on_status({"phase": "plan", "text": plan_text,
                                            "done": False,
                                            "raw": "📋 计划：" + plan_text})
                        except Exception:  # noqa: BLE001
                            pass
                    if plan_text:
                        messages.append({"role": "assistant", "content": "（执行计划）" + plan_text})
                for step in range(self.max_steps):
                    resp = await cli.post(url, json=body, headers=headers)
                    if resp.status_code != 200:
                        return f"电脑代理 LLM 调用失败（HTTP {resp.status_code}）：" \
                               f"{resp.text[:200]}"
                    data = resp.json()
                    try:
                        from .quota_meter import record as _qm_record
                        _qm_record(self.model, ((data.get("usage") or {}).get("total_tokens") or 0), self.base_url)
                    except Exception:  # noqa: BLE001
                        pass
                    # 【2026-10-01 D】观测仪：记录操作路径每次 LLM 调用的模型 / token / 延迟
                    try:
                        from .memory_profiler import record_probe
                        _tok = ((data.get("usage") or {}).get("total_tokens") or 0)
                        record_probe("cloud", self.model, "llm",
                                     prompt_tokens=int(_tok),
                                     latency_ms=(resp.elapsed.total_seconds() * 1000.0)
                                     if hasattr(resp, "elapsed") else 0.0)
                    except Exception:  # noqa: BLE001
                        pass
                    msg = (data.get("choices") or [{}])[0].get("message") or {}
                    tool_calls = msg.get("tool_calls") or []
                    content = (msg.get("content") or "").strip()
                    if not tool_calls:
                        # 终态：模型给出自然语言结论（调用方负责念出）
                        if on_status is not None:
                            try:
                                await on_status({"phase": "done", "done": True,
                                                "text": "操作完成，正在组织回复…"})
                            except Exception:  # noqa: BLE001
                                pass
                        return await _remember(content or "（代理未返回结果）")
                    # 中间态：模型打算调工具——先把它的"心里话"念出来（口头确认/进度）
                    if content and on_speak is not None:
                        try:
                            await on_speak(content)
                        except Exception:  # noqa: BLE001
                            pass
                    # 回显 assistant 消息（含 tool_calls），供下一步模型续写
                    messages.append({
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    })
                    for tc in tool_calls:
                        fname = (tc.get("function") or {}).get("name", "")
                        fargs_raw = (tc.get("function") or {}).get("arguments", "{}") or "{}"
                        try:
                            fargs = json.loads(fargs_raw) if fargs_raw.strip() else {}
                        except Exception:  # noqa: BLE001
                            fargs = {}
                        if on_status is not None:
                            try:
                                await on_status({
                                    "phase": "step", "step": step + 1,
                                    "total": self.max_steps, "tool": fname,
                                    "args": fargs, "state": "running",
                                    "text": f"步骤 {step+1}/{self.max_steps} · 正在执行：{fname}",
                                })
                            except Exception:  # noqa: BLE001
                                pass
                        # 安全闸：破坏性工具 + require_confirm=true + 提供了确认钩子 → 先确认
                        if (on_confirm is not None and self._ctx.get("require_confirm")
                                and fname in _DESTRUCTIVE_TOOLS):
                            desc = f"{fname}({fargs})"
                            try:
                                allowed = bool(await on_confirm(desc))
                            except Exception:  # noqa: BLE001
                                allowed = False
                            if not allowed:
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc.get("id", ""),
                                    "content": "用户拒绝执行该操作（安全确认未通过）。",
                                })
                                continue
                        result = await dispatch_tool(fname, fargs, self._ctx)
                        logger.info("AGENT 工具 %s -> %s", fname, result[:120])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": result,
                        })
                        # 【P0-1 验证回路】状态改变类工具成功后，注入自检提示，要求模型在继续/
                        # 汇报前先复核界面状态是否真的达成（避免"调用没报错=成功"的静默误判）。
                        if (self.verify_actions and fname in _STATE_CHANGING_TOOLS
                                and not str(result).startswith(
                                    ("工具 ", "电脑代理", "未知工具", "浏览器代理"))):
                            messages.append({"role": "user", "content":
                                "（自检）你刚刚执行了可能改变界面状态的动作。在继续下一步或最终汇报前，"
                                "请先用 ui_inspect 或 see_screen 复核该动作是否真的生效；"
                                "未生效则重试或换方案，不要凭调用没报错就假设成功。"})
                    # 继续循环：把工具结果喂回模型，直到它给出最终自然语言回复
                    continue
                # 【2026-09-29 修"操作过多就甩一句提示"】步数用尽时不再直接抛硬编码提示，
                # 而是再问一次（不带 tools，强制收口）让它总结已完成的操作与结果。
                try:
                    summary_body = dict(body)
                    summary_body.pop("tools", None)
                    summary_body.pop("tool_choice", None)
                    summary_body["messages"] = messages + [{
                        "role": "user",
                        "content": "（你已经达到本轮最大操作步数，不能再调用任何工具了。"
                                   "请用一句自然的中文总结：你刚刚完成了哪些操作、结果如何、"
                                   "还有什么没做完需要用户确认。不要调用工具，直接回答。）",
                    }]
                    resp = await cli.post(url, json=summary_body, headers=headers)
                    if resp.status_code == 200:
                        final = ((resp.json().get("choices") or [{}])[0]
                                 .get("message") or {}).get("content") or ""
                    if final.strip():
                        if on_status is not None:
                            try:
                                await on_status({"phase": "done", "done": True,
                                                "text": "操作完成，正在组织回复…"})
                            except Exception:  # noqa: BLE001
                                pass
                        return await _remember(final.strip())
                except Exception as e:  # noqa: BLE001
                    logger.warning("步数用尽后的总结请求失败: %s", e)
                return await _remember("（已到本轮操作步数上限，先停一下。上面是已经做完的部分；若还没完成，告诉我还要接着做什么，我重新帮你做。）")
        except Exception as e:  # noqa: BLE001
            logger.warning("电脑代理执行异常: %s", e)
            return f"电脑代理执行异常：{e}"

    async def _extract_operation_memory(self, instruction: str, answer: str) -> None:
        """【A-4】从「操作指令 + 执行结果」里提炼用户长期事实（偏好/习惯/约定）并入库。

        失败/无内容一律静默忽略，绝不拖慢或打断主流程。store 经由进程级单例懒取，
        若 dialogue 尚未登记则为 None，安全跳过（不会把操作孤岛问题变成崩溃）。
        """
        try:
            from ..companion.memory_store import (
                get_memory_store, store_extracted_facts, MEMORY_FACTS_PROMPT,
            )
            store = get_memory_store()
            if store is None:
                return
            if not answer or not answer.strip():
                return
            # 错误/兜底说明不是"学到了用户事实"，跳过
            if answer.startswith(("电脑代理", "（已到", "没有收到", "（代理未返回")):
                return
            prompt = MEMORY_FACTS_PROMPT.replace("{text}", (instruction + "\n" + answer)[:600])
            import httpx
            body = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": 256,
                "temperature": 0.0,
            }
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = "Bearer " + self.api_key
            url = f"{self.base_url}/chat/completions"
            async with httpx.AsyncClient(timeout=min(self.timeout_s, 15.0)) as cli:
                _t0 = time.time()
                resp = await cli.post(url, json=body, headers=headers)
                _lat = (time.time() - _t0) * 1000.0
                # 【2026-10-01 D】观测仪：记录操作路径事实提炼调用的模型 / 耗时
                try:
                    from .memory_profiler import record_probe
                    record_probe("cloud", self.model, "extract",
                                 prompt_tokens=len(prompt), latency_ms=_lat, note="operation")
                except Exception:  # noqa: BLE001
                    pass
                if resp.status_code != 200:
                    return
                data = resp.json()
                raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            store_extracted_facts(
                store, raw, [instruction, answer],
                source_label="operation", character=store.get_character() or "",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("操作记忆提炼失败(忽略): %s", e)

    # ---------- 从 config 构造 ----------
    @classmethod
    def from_config(cls, cfg: Any, repo_root: str = "") -> "AgentBrain":
        agent = getattr(cfg.ns, "agent", None) if cfg is not None else None
        enabled = bool(getattr(agent, "enabled", False)) if agent else False
        # LLM 选择：agent.llm 显式配置优先；否则复用 cascade 外接 LLM
        base = key = model = ""
        llm_cfg = getattr(agent, "llm", None) if agent else None
        if llm_cfg is not None and getattr(llm_cfg, "api_key", ""):
            base = (getattr(llm_cfg, "base_url", "") or "").strip().rstrip("/")
            key = getattr(llm_cfg, "api_key", "") or ""
            model = getattr(llm_cfg, "model", "") or ""
        if not (base and key and model):
            cas = getattr(cfg.ns, "cascade", None) if cfg is not None else None
            base = (getattr(cas, "base_url", "") or "").rstrip("/")
            key = getattr(cas, "api_key", "") or ""
            model = getattr(cas, "model", "") or ""
        max_steps = max(1, min(int(getattr(agent, "max_steps", 15) or 15), 40)) if agent else 15
        allow_cmd = bool(getattr(agent, "allow_command", False)) if agent else False
        allow_cmds = list(getattr(agent, "allow_commands", []) or []) if agent else []
        require_confirm = bool(getattr(agent, "require_confirm", False)) if agent else False
        vision_model = (getattr(agent, "vision_model", "") or "").strip() if agent else ""
        plan_first = bool(getattr(agent, "plan_first", True)) if agent else True
        verify_actions = bool(getattr(agent, "verify_actions", True)) if agent else True
        search_provider = (getattr(agent, "search_provider", "") or "duckduckgo") if agent else "duckduckgo"
        search_fallback = list(getattr(agent, "search_fallback", []) or []) if agent else []
        _vlm = _vision_endpoint(cfg)      # 同 _build_ctx：Agent 看屏跟随能力槽的统一视觉端点
        ctx = {
            "allow_cmd": allow_cmd,
            "allow_cmds": allow_cmds,
            "require_confirm": require_confirm,
            "base_url": base,
            "api_key": key,
            "model": model,
            "vision_model": vision_model,
            "vlm_base_url": _vlm[0],
            "vlm_api_key": _vlm[1],
            "vlm_model": _vlm[2],
            "plan_first": plan_first,
            "verify_actions": verify_actions,
            "search_provider": search_provider,
            "search_fallback": search_fallback,
            "repo_root": repo_root or _repo_root(),
        }
        persona = _load_persona_text(cfg) if cfg is not None else ""
        return cls(base_url=base, api_key=key, model=model, enabled=enabled,
                   max_steps=max_steps, ctx=ctx, persona=persona,
                   plan_first=plan_first, verify_actions=verify_actions)


async def run_agent_task(instruction: str, cfg: Any = None,
                         on_status: Optional[Callable[[str], Any]] = None,
                         on_speak: Optional[Callable[[str], Any]] = None,
                         on_confirm: Optional[Callable[[str], Any]] = None) -> str:
    """对外入口：执行一条"操作电脑"的自然语言指令。

    cfg 为 None 时懒加载 config（带缓存）。返回最终回复文本 / 说明字符串。
    on_confirm(desc)->bool：高危工具执行前的确认钩子（无则跳过确认闸）。
    """
    if cfg is None:
        cfg = _load_cfg()
    if cfg is None:
        return "电脑代理无法加载配置（config.yaml 缺失或解析失败）。"
    brain = AgentBrain.from_config(cfg, _repo_root())
    return await brain.run(instruction, on_status=on_status, on_speak=on_speak,
                           on_confirm=on_confirm)


def agent_route_chat(cfg: Any = None) -> bool:
    """是否把聊天框输入（user_input）直接路由到 agent：需 agent.enabled 且 agent.route_chat。"""
    if cfg is None:
        cfg = _load_cfg()
    if cfg is None:
        return False
    agent = getattr(cfg.ns, "agent", None)
    if not agent:
        return False
    return bool(getattr(agent, "enabled", False)) and bool(getattr(agent, "route_chat", False))
