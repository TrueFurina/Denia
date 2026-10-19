"""OpenAI 兼容云端多模态 LLM 客户端。

支持任意 OpenAI-compatible 端点（OpenAI / DeepSeek / 智谱 / 通义 / 硅基流动 /
MiniCPM-o 官方兼容接口等），base_url / api_key / model 完全可自定义。
图片以 raw bytes 传入，自动转为 data URI 交给云端多模态模型。

配置（config.yaml → llm.openai）：
  base_url: 兼容端点根地址，如 https://api.openai.com/v1
  api_key:  密钥（无需鉴权的本地/内网服务可留空）
  model:    模型名，如 gpt-4o / deepseek-chat / glm-4v-flash
  vision:   模型是否支持图片输入（默认 true）
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Callable, Optional

from ..config import Config
from .base import LLMClient
from .quota_meter import record as _qm_record

logger = logging.getLogger(__name__)

import asyncio
import httpx

# 视觉工具（看屏/截图）结果里出现这些标记，即判定"视觉不可用 / 额度耗尽"，
# 由 chat_with_tools 累计失败次数后强制收口，避免模型在 403 上反复重试空转。
_VISION_FAIL_MARKERS = (
    "403", "Forbidden", "quota", "额度", "免费额度", "不可用",
    "视觉能力已关闭", "看屏 VLM 调用失败", "截图失败",
    # 【2026-10-04 20:15 补·措辞别名】实际回执里"视觉/看图/看屏"三种说法混用
    # （如 `vision_chat` 抛的是"看图能力已关闭"），只认"视觉能力已关闭"会漏判
    # ⇒ 该失败被当成"看屏成功"，UI 照样显示完成。补齐别名。
    "看图能力已关闭", "看图已关闭", "看屏已关闭", "视觉不可用", "看图不可用",
    "未配置任何视觉模型", "没有可用的视觉",
)


def _is_vision_failure(result: str) -> bool:
    """粗略判定一次视觉工具结果是否为"失败/不可用"（用于收敛重试）。"""
    if not result:
        return False
    low = result.lower()
    return any(m.lower() in low for m in _VISION_FAIL_MARKERS)


# ===== 工具成败判定（20-10 治"假完成"）=====
# `phase:"done"` 只表示"工具循环结束了"，前端却把它渲染成"任务完成"，
# 于是"没做成"也被告知"完成"。这里按工具回执里的**否定线索**判定成败，
# 供状态层区分「完成 / 部分完成 / 未完成」。
# ⚠️ 保守取向：**判不准一律按"成功"算**（宁可少报失败，也不要误报失败、
#    把本来做成了的操作说成没做成）；只有命中明确否定词才判失败。
_TOOL_FAIL_MARKERS = (
    # —— play_song 的失败/未确认回执（实测最典型）——
    "未确认播放", "没有播放", "播放失败", "未播放", "无法播放", "播放不了",
    "没找到该", "未找到歌曲", "歌单里没有", "搜索不到", "搜索无结果",
    "未播", "搜不到",
    # —— 通用失败/拒绝 ——
    "执行失败", "调用失败", "工具异常", "执行异常", "被拒绝", "拒绝执行",
    "超时", "timeout", "失败", "无法完成", "未能完成", "没成功", "未成功",
    "不支持对", "不开放控件", "未找到控件", "找不到控件", "没有找到",
    "权限不足", "需要确认", "已取消", "被拦截", "安全确认",
    # —— 视觉不可用 ——
    "额度用完", "额度耗尽", "免费额度", "未安装", "不可用",
)
# 这些词虽然含"失败/错误"字样，但常常出现在**成功**描述里（如"上次失败后重试成功"），
# 命中它们时需要额外确认，避免误判。
_TOOL_OK_HINTS = (
    "已确认播放", "正在播放", "播放成功", "已成功", "已完成", "成功",
    "已打开", "已聚焦", "已启动", "已发送", "已保存", "已点击", "已输入",
)


def _judge_tool_result(tool_name: str, result: str) -> tuple[bool, str]:
    """判定一次工具调用是否**真正达成了目标**，返回 (是否成功, 原因短语)。

    面向 UI 状态卡片的诚实性：失败时要能说清"卡在哪一步"。

    ⭐⭐【play_song 专用规则·20-10 实锤】`play_song` 的回执分多段（①②③…），
    **只做到 ①「应用已在运行 / 已打开」绝不算成功** —— 歌根本没播。
    20:03 用户实测：play_song 返回「① 应用已在运行：汽水音乐」，随后 click_at
    被安全确认超时拒绝，**什么都没播**，但 UI 显示「完成」、她还编出了歌名。
    ⇒ 判定 `play_song` 必须**真的见到"已确认正在播放"或"播放成功"**才算成功；
    只出现"已打开/已启动"一律判失败，并明确说"只打开了应用，没能播放"。
    """
    if result is None:
        return False, "无回执"
    r = str(result).strip()
    if not r:
        return False, "工具无返回"

    # ---- play_song：目标达成标志必须是"真的在播" ----
    if tool_name == "play_song":
        if any(k in r for k in ("已确认播放", "已确认正在播放", "播放成功",
                               "正在播放", "已播放", "确认正在播")):
            return True, ""
        if any(k in r for k in ("未确认播放", "没有播放", "播放失败", "未播放",
                               "搜不到", "搜索不到", "未找到", "没找到",
                               "未播", "无法播放", "播放不了", "超时", "被拒绝")):
            for k in ("未确认播放", "播放失败", "未播放", "搜不到", "搜索不到",
                      "未找到", "没找到", "未播", "无法播放", "超时", "被拒绝"):
                if k in r:
                    i = r.find(k)
                    return False, r[max(0, i - 10): i + len(k) + 16].strip()
        # 只做到"打开应用/聚焦窗口"就返回 → 明确点破"没播成"
        if any(k in r for k in ("应用已在运行", "已打开", "已启动", "已聚焦")):
            return False, "只打开了应用，没能确认播放"
        for mk in _TOOL_FAIL_MARKERS:
            if mk.lower() in r.lower():
                i = r.lower().find(mk.lower())
                return False, r[max(0, i - 12): i + len(mk) + 12].strip()
        return True, ""

    # ---- 视觉工具：不可用必须判失败（否则会白烧步数）----
    if tool_name in ("see_screen", "screenshot"):
        if _is_vision_failure(r):
            return False, "看屏/截图不可用（额度或开关）"
        return True, ""

    # ---- 通用：先看明确的成功线索（优先级最高，避免"成功"里含"失败"被误判）----
    for ok in _TOOL_OK_HINTS:
        if ok in r:
            return True, ""
    low = r.lower()
    for mk in _TOOL_FAIL_MARKERS:
        if mk.lower() in low:
            i = low.find(mk.lower())
            frag = r[max(0, i - 12): i + len(mk) + 12].strip()
            return False, (frag or mk)
    return True, ""


# 云端高占用（智谱等）瞬时错误自愈：对可重试状态码/传输错误自动重试若干次。
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


def _http_err_detail(e: Optional[Exception]) -> str:
    """从 HTTP 异常里提取**服务端返回的具体错误**，附到异常文案后面。

    【2026-10-02】此前只抛 `Client error '403 Forbidden' for url ...`，看不到服务端
    给的真实原因，排查"为什么视觉/聊天坏了"只能靠抓包实测。阿里云百炼会在响应体里
    给明确错误码（如 AllocationQuota.FreeTierOnly = 免费额度已用完且开了"仅免费额度"
    模式），这里把它透传出来，用户看日志即可定位，不必再猜。
    """
    resp = getattr(e, "response", None)
    if resp is None:
        return ""
    try:
        body = resp.text or ""
    except Exception:  # noqa: BLE001
        return ""
    if not body:
        return ""
    try:
        j = json.loads(body)
        err = j.get("error") or {}
        code = err.get("code") or err.get("type") or ""
        msg = err.get("message") or ""
        if code or msg:
            return f" [服务端: {code} {msg}]"
    except Exception:  # noqa: BLE001
        pass
    return f" [响应: {body[:200]}]"
_BACKOFF = (1.0, 2.0, 4.0)  # 第 1、2 次失败后的等待（秒）


def _is_retryable_exc(e: BaseException) -> bool:
    """仅对瞬时/高占用类错误重试：429/5xx 与连接超时等传输错误。"""
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in _RETRYABLE_STATUS
    if isinstance(e, (httpx.TimeoutException, httpx.ConnectError,
                      getattr(httpx, "ConnectionClosed", httpx.RemoteProtocolError),
                      httpx.RemoteProtocolError,
                      httpx.TransportError, httpx.ProtocolError)):
        return True
    return False


def _backoff_seconds(attempt: int, headers=None) -> float:
    """尊重 429 的 Retry-After；否则用指数退避。attempt 从 1 开始。"""
    ra = (headers or {}).get("Retry-After") if headers is not None else None
    if ra:
        try:
            return max(0.5, float(ra))
        except (ValueError, TypeError):
            pass
    return _BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)]


class OpenAIClient(LLMClient):
    name = "openai-compatible"

    def __init__(self, cfg: Config, base_url: str = "", api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout_s: Optional[float] = None,
                 vision: Optional[bool] = None,
                 max_tokens: Optional[int] = None) -> None:
        """base_url / api_key / model / timeout_s / vision / max_tokens 可覆盖 llm.openai 配置。

        【2026-10-18 修·默认值写反】api_key / model 的默认值原来是 `""`，而判据是
        `x if x is not None else 读配置` —— 空串**不是** None，于是 `OpenAIClient(cfg)`
        这种"不传就走 llm.openai"的用法，拿到的永远是空 model → `available` 恒 False
        → 调用方只看到"OpenAI 兼容服务未配置"，而配置里明明填好了。改默认值为 None。
        语义不变：显式传 `api_key=""` 仍表示"这个端点不要鉴权"（本地 vLLM/Ollama）。
        
        视觉模型路由层（VisionRouter）用它按条目参数构造云端客户端
        （如智谱 GLM-4V-Flash：base_url=open.bigmodel.cn/api/paas/v4）。
        不传时行为与原来一致（读 llm.openai 配置段）。

        【2026-10-04 18:55 破耦合·治"改一处爆死"】此前 `_vision` / `_max_tokens`
        **只从 `llm.openai` 读、且视觉条目构造时不传** ⇒ 视觉 20 个条目与聊天端口
        共用同一个 `llm.openai.vision` 开关：**把它设成 false，20 个视觉模型会
        同时 `vision_available=False`**，桌面看屏/发图/陪看视频全线哑火，而用户
        只想改一个"核心 LLM"的配置。
        现在这两个参数可由构造参数独立覆盖：视觉路由按**条目自身**的 vision 能力
        传值，不再被核心 LLM 的配置连坐。
        """
        llm_ns = getattr(cfg.ns, "llm", None)
        openai_cfg = getattr(llm_ns, "openai", None)
        self._base_url = (base_url or getattr(openai_cfg, "base_url", "") or "").rstrip("/")
        self._api_key = api_key if api_key is not None else (getattr(openai_cfg, "api_key", "") or "")
        self._model = model if model is not None else (getattr(openai_cfg, "model", "") or "")
        # vision：显式传入优先（视觉路由按条目能力传），否则回落 llm.openai.vision
        _v = getattr(openai_cfg, "vision", True)
        self._vision = bool(vision if vision is not None else _v)
        self._timeout = float(timeout_s or getattr(openai_cfg, "timeout_s", 60) or 60)
        # max_tokens 同理：允许按条目/按调用方独立给，不被核心 LLM 连坐
        self._max_tokens = max_tokens if max_tokens is not None else getattr(
            openai_cfg, "max_tokens", None)

    def set_api_key(self, api_key: str) -> None:
        """热更新 api_key（视觉模型路由层填智谱等云端 key 时用）。"""
        self._api_key = (api_key or "").strip()

    def _is_reasoning_model(self) -> bool:
        """当前模型是否带 reasoning 能力（model_caps 判定，含名称关键词兜底）。"""
        try:
            from .model_caps import infer_caps
            caps = infer_caps(model=self._model) or {}
            return "reasoning" in (caps.get("features") or ())
        except Exception:  # noqa: BLE001 能力表异常不拖垮请求
            logger.debug("reasoning 能力判定失败", exc_info=True)
            return False

    def _thinking_body(self, options: Optional[dict] = None, deep: bool = False) -> dict:
        """【2026-10-12 治高延迟】按调用场景给"思考开关"参数，覆盖所有厂商：

        * 纯聊天（deep=False，chat/chat_stream）：默认**关思考**。百炼 qwen3 系推理模型
          默认开思考——服务端先吐几千字思维链（reasoning_content，实测 3473 字）才出正文，
          客户端只读 content ⇒ 白等 10~25s。实测关思考首字 22.3s→1.0s。
        * 工具调用（deep=True，chat_with_tools）：默认**开思考**。操作电脑要多步规划，
          深度思考显著提高工具选择/参数准确率；代价是每轮慢几秒，Agent 场景可接受。
        * 调用方传 options["enable_thinking"]=True/False 显式覆盖（最高优先）。
        * 厂商适配：百炼→enable_thinking；智谱 GLM→thinking.type（仅 caps 判定支持
          reasoning 的型号传，防不支持的型号 400）；其余厂商不传（非标准字段）。
        """
        # 1) 调用方显式指定，最高优先
        if options and options.get("enable_thinking") is not None:
            want = bool(options["enable_thinking"])
            return self._vendor_thinking(want)
        # 2) 按场景 + 模型能力自动判定
        if not self._is_reasoning_model():
            return {}  # 非推理模型没有思考开关可给
        return self._vendor_thinking(deep)

    def _vendor_thinking(self, want: bool) -> dict:
        """把"要/不要思考"翻译成对应厂商的请求字段（仅推理模型可到这一步）。"""
        base = (self._base_url or "")
        if "dashscope.aliyuncs.com" in base:
            return {"enable_thinking": want}
        if "open.bigmodel.cn" in base:
            # 智谱 GLM：thinking.type = enabled/disabled（open.bigmodel.cn 为其 API 域名）
            return {"thinking": {"type": "enabled" if want else "disabled"}}
        return {}  # 其它厂商无标准开关，保持厂商默认行为

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def available(self) -> bool:
        return bool(self._base_url and self._model)

    @property
    def vision_available(self) -> bool:
        return self._vision and self.available

    @property
    def model_name(self) -> Optional[str]:
        return self._model

    def _to_openai_messages(self, messages: list[dict]) -> list[dict]:
        """把内部消息格式（content 字符串 + images 原始字节）转成 OpenAI content 结构。"""
        out: list[dict] = []
        for m in messages:
            m2: dict[str, Any] = {"role": m.get("role", "user")}
            content = m.get("content", "")
            images = m.get("images") or []
            if images and self._vision:
                parts: list[dict] = []
                if content:
                    parts.append({"type": "text", "text": content})
                for img in images:
                    b64 = base64.b64encode(img).decode("ascii")
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                m2["content"] = parts
            else:
                m2["content"] = content
            out.append(m2)
        return out

    async def chat(self, messages: list[dict], options: Optional[dict] = None) -> str:
        """调用 OpenAI 兼容接口（带高占用自愈重试）。options 可覆盖 temperature/top_p/max_tokens 等。"""
        if not self.available:
            raise RuntimeError("OpenAI 兼容服务未配置（base_url/model 为空），请检查 config.yaml 的 llm.openai")

        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(messages),
            "stream": False,
        }
        if self._max_tokens:
            body["max_tokens"] = self._max_tokens
        if options:
            # enable_thinking 不走白名单直写：由 _thinking_body 按厂商翻译
            # （百炼→enable_thinking / 智谱→thinking.type），避免双字段冲突。
            for k in ("temperature", "top_p", "max_tokens"):
                if k in options and options[k] is not None:
                    body[k] = options[k]
        # 【2026-10-12】非流式按场景给思考开关（见 _thinking_body）：纯聊天关、工具深。
        body.update(self._thinking_body(options))

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"
        last_err: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                _qm_record(self._model, ((data.get("usage") or {}).get("total_tokens") or 0), self._base_url)
                try:
                    return (data["choices"][0]["message"]["content"] or "").strip()
                except (KeyError, IndexError, TypeError) as e:
                    logger.error("OpenAI 兼容接口响应异常: %s", data)
                    raise RuntimeError(f"OpenAI 兼容接口响应格式异常: {e}") from e
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < _MAX_ATTEMPTS and _is_retryable_exc(e):
                    wait = _backoff_seconds(
                        attempt,
                        getattr(e, "response", None).headers if isinstance(e, httpx.HTTPStatusError) else None,
                    )
                    logger.warning(
                        "云端 LLM 高占用/瞬时错误（%s），第 %d/%d 次重试，%.1fs 后重连",
                        (getattr(e, "response", None) and getattr(e.response, "status_code", "")) or type(e).__name__,
                        attempt, _MAX_ATTEMPTS, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise RuntimeError(f"OpenAI 兼容接口调用失败 ({self._base_url}): {e}"
                                   f"{_http_err_detail(e)}") from e
        raise RuntimeError(f"OpenAI 兼容接口调用失败 ({self._base_url}): {last_err}"
                           f"{_http_err_detail(last_err)}") from last_err

    async def vision_describe(self, image_bytes: bytes, prompt: str) -> str:
        """单图视觉描述。视觉不可用返回空串。"""
        if not self._vision:
            return ""
        return await self.chat(
            messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        )

    def supports_stream(self) -> bool:
        """OpenAI 兼容接口支持 SSE 流式输出。"""
        return True

    async def chat_with_tools(self, messages: list[dict], options: Optional[dict],
                              tools: list[dict], on_tool: Optional[Callable],
                              max_steps: int = 6,
                              on_confirm: Optional[Callable] = None,
                              require_confirm: bool = False,
                              destructive_tools: Optional[set] = None,
                              on_status: Optional[Callable] = None,
                              on_token: Optional[Callable] = None) -> str:
        """带 function-calling 的对话：模型可调用 tools，工具结果回灌后再让模型总结。

        用于"聊天中允许调用工具"——模型自己决定调不调工具（tool_choice=auto）：
        闲聊就只返回文本，操作电脑类指令才调 open_app / media_control 等。
        通过 on_tool(name, args)->str 回调执行工具（由调用方注入 dispatch_tool + ctx）。
        on_status(text)：工具执行进度广播（UI 状态气泡用，不念），支持同步或异步回调。
        关键：后续轮次手工拼 OpenAI 原生消息（保留 assistant.tool_calls 与 role=tool），
        不能走 _to_openai_messages（它会丢弃 tool_calls）。
        """
        if not self.available:
            raise RuntimeError("OpenAI 兼容服务未配置（base_url/model 为空），请检查 config.yaml 的 llm.openai")
        openai_msgs = self._to_openai_messages(messages)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": openai_msgs,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
        }
        if self._max_tokens:
            body["max_tokens"] = self._max_tokens
        if options:
            # 同 chat()：enable_thinking 由 _thinking_body 按厂商翻译，不进白名单。
            for k in ("temperature", "top_p", "max_tokens"):
                if k in options and options[k] is not None:
                    body[k] = options[k]
        # 【2026-10-12】工具链路 deep=True：Agent 操作电脑要多步规划，默认开深度思考
        #   （百炼/智谱推理模型）；显式 options["enable_thinking"] 可覆盖。
        body.update(self._thinking_body(options, deep=True))
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = f"{self._base_url}/chat/completions"

        async def _status(payload) -> None:
            """进度广播（支持结构化 dict 或纯文本；同步/异步回调都支持；异常吞掉不影响主流程）。

            dict 形态：{"phase": "plan"|"step"|"done"|"error"|"status", "text": ...,
                        "step": int, "total": int, "tool": str, "args": dict,
                        "state": "running"|"done", "result": str, "done": bool, "error": bool}
            前端据此渲染「Agent 执行卡片」（计划 / 步骤时间线 / 完成态）。
            """
            if on_status is None:
                return
            try:
                if isinstance(payload, str):
                    payload = {"phase": "status", "text": payload}
                r = on_status(payload)
                if hasattr(r, "__await__"):
                    await r
            except Exception:  # noqa: BLE001
                logger.debug("on_status 回调失败", exc_info=True)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            _total = max(1, int(max_steps or 6))
            _VISION_TOOLS = ("see_screen", "screenshot")
            _vision_fail = 0  # 视觉工具因额度/不可用反复失败的次数（超过阈值即强制收口）
            # 【2026-10-04 20:10】工具成败统计（治 UI"假完成"）：
            # `phase:"done"` 只代表"循环结束"，若不统计成败就会把没做成的操作
            # 也报成"完成"（20:03 实锤：play_song 只做到①应用已运行 + click_at 被
            # 安全确认超时拒绝，UI 仍显示「完成」）。这些计数用于生成诚实的收口文案。
            _tools_total = 0
            _tools_ok = 0
            _tools_failed = 0
            _failed_names: list[str] = []

            async def _call_model(stream: bool):
                """统一的模型调用（流式 / 非流式通用），返回 (content, tool_calls)。

                流式（stream=True 且传入 on_token）时逐 delta 吐字并累积 tool_calls：
                OpenAI SSE 把同一个 tool_call 拆成多片，需按 index 拼接 id / name / arguments。
                非流式（无 on_token / 兜底）走原逻辑，行为不变。
                """
                if stream and on_token is not None:
                    req_body = dict(body)
                    req_body["stream"] = True
                    acc_content = ""
                    acc_tcs: list[dict] = []
                    async with client.stream("POST", url, json=req_body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            line = (line or "").strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[len("data:"):].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                _u = chunk.get("usage")
                                if _u and _u.get("total_tokens"):
                                    _qm_record(self._model, _u["total_tokens"], self._base_url)
                                d = (chunk.get("choices") or [{}])[0].get("delta") or {}
                                c = d.get("content") or ""
                                if c:
                                    acc_content += c
                                    r = on_token(c)
                                    if hasattr(r, "__await__"):
                                        await r
                                for tc in (d.get("tool_calls") or []):
                                    idx = tc.get("index", len(acc_tcs))
                                    while len(acc_tcs) <= idx:
                                        acc_tcs.append({
                                            "id": "", "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        })
                                    if tc.get("id"):
                                        acc_tcs[idx]["id"] = tc["id"]
                                    fn = tc.get("function") or {}
                                    if fn.get("name"):
                                        acc_tcs[idx]["function"]["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        acc_tcs[idx]["function"]["arguments"] += fn["arguments"]
                            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                                continue
                    return acc_content, acc_tcs
                # 非流式兜底（无 on_token）：原逻辑
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                _qm_record(self._model, ((data.get("usage") or {}).get("total_tokens") or 0), self._base_url)
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                return (msg.get("content") or "").strip(), (msg.get("tool_calls") or [])

            for _si in range(_total):
                body["messages"] = openai_msgs
                content, tool_calls = await _call_model(stream=True)
                if not tool_calls:
                    # 【2026-10-04 23:20 治"一弹出来就是完成 / 步骤跟进乱跳"】
                    # 模型给出最终自然语言回复 = **整条工具链真正结束**，`done` 该在这里发。
                    # 旧代码把它发在**循环体末尾**（每一轮工具调用之后就发一次）——
                    # 而"模型还要接着调下一个工具"的中间轮也被当成 done ⇒
                    # 卡片在**第 1 步刚做完就翻成「完成」**，下一轮的 step 事件再把它
                    # 打回"执行中"（`_merge_agent_meta` 里 step 分支会 `done=False`）
                    # ⇒ 用户看到的就是"一弹出来就是完成"+ 进度反复横跳。
                    # 这里与 agent_brain.run 的语义对齐：done = 全部结束。
                    if on_status is not None:
                        if _tools_failed and _tools_total:
                            await _status({"phase": "done", "done": True, "partial": True,
                                           "ok_count": _tools_ok, "total_count": _tools_total,
                                           "failed_tools": list(_failed_names),
                                           "text": "部分完成（%d/%d 步达成，%s 未成功），正在组织回复…"
                                                   % (_tools_ok, _tools_total,
                                                      "、".join(_failed_names[:3]))})
                        else:
                            await _status({"phase": "done", "done": True, "partial": False,
                                           "ok_count": _tools_ok, "total_count": _tools_total,
                                           "text": "操作完成，正在组织回复…"})
                    return content or "（未返回结果）"
                # 回显 assistant（含 tool_calls），供下一步模型续写
                openai_msgs.append({
                    "role": "assistant",
                    # 【2026-10-08 修 NameError】原为 msg.get("content")，但 msg 已随
                    # 流式改造挪进 _call_model 的非流式分支，此处不再可见 ⇒
                    # 一旦命中工具分支就 NameError: name 'msg' is not defined，
                    # 被上层 catch 成"工具调用失败"→ 用户只看到"服务暂时不可用"。
                    # 直接用 _call_model 的返回值 content（语义等价：模型本轮原始文本）。
                    "content": content or "",
                    "tool_calls": [
                        {"id": tc.get("id", ""), "type": "function",
                         "function": {
                             "name": (tc.get("function") or {}).get("name", ""),
                             "arguments": (tc.get("function") or {}).get("arguments", "{}") or "{}",
                         }}
                        for tc in tool_calls
                    ],
                })
                _step_vision_fail = 0
                for tc in tool_calls:
                    fname = (tc.get("function") or {}).get("name", "")
                    fargs_raw = (tc.get("function") or {}).get("arguments", "{}") or "{}"
                    try:
                        fargs = json.loads(fargs_raw) if fargs_raw.strip() else {}
                    except Exception:  # noqa: BLE001
                        fargs = {}
                    # 【AGENT·逐步确认闸】破坏性工具 + 开启确认 + 有确认回调 → 执行前先确认。
                    # 拒绝则把"用户拒绝"作为该工具结果回灌模型，并跳过本次 dispatch。
                    if (require_confirm and on_confirm is not None
                            and fname in (destructive_tools or ())):
                        desc = f"{fname}({fargs})"
                        try:
                            allowed = bool(await on_confirm(desc))
                        except Exception:  # noqa: BLE001
                            allowed = False
                        if not allowed:
                            openai_msgs.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": "用户拒绝执行该操作（安全确认未通过）。",
                            })
                            continue
                    result = ""
                    # 【2026-10-04】可观测性：聊天工具链路此前一个字都不落日志（agent_brain
                    # 的那条 logger.info 只覆盖独立 agent 模式），出「只打开没播放」这类问题时
                    # 完全无法判断模型到底调了什么、返回了什么。这里补上与 agent_brain 同级
                    # 的日志：工具名 + 结果前 120 字。
                    logger.info("聊天工具 %s(%s) 开始", fname,
                                json.dumps(fargs, ensure_ascii=False)[:120]
                                if isinstance(fargs, dict) else str(fargs)[:120])
                    try:
                        # 发射「步骤开始」结构化状态（含工具名 / 参数 / 序号），前端渲染时间线。
                        if on_status is not None:
                            await _status({
                                "phase": "step", "step": _si + 1, "total": _total,
                                "tool": fname, "args": fargs, "state": "running",
                                "text": f"步骤 {_si + 1}/{_total} · 正在执行：{fname}",
                            })
                        if on_tool is not None:
                            result = await on_tool(fname, fargs)
                    except Exception as e:  # noqa: BLE001
                        result = f"工具 {fname} 执行异常：{e}"
                    logger.info("聊天工具 %s -> %s", fname, str(result)[:120])
                    # 【2026-10-04 20:10 治"假完成"】记录每个工具的真实成败。
                    # 背景（20:03 实锤）：用户说"帮我打开汽水音乐播放dj歌曲"，日志显示
                    #   play_song -> 「① 应用已在运行：汽水音乐」   ← 并没有播成功
                    #   agent_confirm: 超时（60.0s）默认拒绝 click_at  ← 卡在安全确认
                    # 但 UI 状态卡片却显示「完成」——因为 `phase:"done"` 在本函数里
                    # **只表示"工具循环结束了"**，前端把它渲染成"任务完成"。
                    # ⇒ 用户被告知"完成了"，实际什么都没做成，且**没有任何警告**。
                    # 现在把成败如实传给状态层，由 UI 区分「完成 / 部分完成 / 未完成」。
                    _ok, _why = _judge_tool_result(fname, str(result))
                    _tools_total += 1
                    if _ok:
                        _tools_ok += 1
                    else:
                        _tools_failed += 1
                        _failed_names.append(fname)
                        logger.warning("工具 %s 未达成目标: %s", fname, _why)
                    # 发射「步骤完成」结构化状态（带回显结果摘要），前端把该步标记为 done。
                    if on_status is not None:
                        await _status({
                            "phase": "step", "step": _si + 1, "total": _total,
                            "tool": fname, "args": fargs, "state": "done" if _ok else "failed",
                            "ok": _ok, "reason": _why,
                            "result": str(result)[:160],
                            "text": (f"步骤 {_si + 1}/{_total} · {fname} "
                                     + ("完成" if _ok else "未达成：" + _why)),
                        })
                    openai_msgs.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": str(result)[:2000],
                    })
                    # 【2026-10-04 修·视觉工具空转】see_screen / screenshot 一旦因免费额度
                    # 耗尽（HTTP 403）或视觉不可用而失败，模型往往当"没成功"反复重试
                    # （ui_inspect→see_screen→…），把 120s 预算烧光才超时，表现即
                    # "只显示操作电脑/完成、半天不回话"。累计视觉失败次数，达 2 次即判定
                    # 视觉不可用，停止再调视觉工具并强制进入收口总结。
                    if fname in _VISION_TOOLS and _is_vision_failure(str(result)):
                        _step_vision_fail += 1
                _vision_fail += _step_vision_fail
                if _vision_fail >= 2:
                    logger.warning("视觉工具连续失败 %d 次，强制收口（不再重试看屏/截图）", _vision_fail)
                    break
                # 【23:20】这里**不再发 `phase:"done"`**——本轮工具跑完不等于
                # 任务结束，模型很可能还要接着调下一个工具。done 只在整条链真正
                # 收口时发（见上面 `if not tool_calls` 分支 与 步数用尽总结分支）。
                # 继续循环：把工具结果喂回模型，直到它给出最终自然语言回复
                continue
            # 【2026-09-29 修"操作过多就甩一句提示"】步数用尽时不再直接抛硬编码的
            # "（操作步骤过多，已停止，避免失控）"，而是再问一次（不带 tools，
            # 强制它收口）让它用一句自然的中文总结"已经做了什么、结果如何"。
            # 拿不到总结才退回原来的提示，用户至少知道进度而不是被一句官话堵回来。
            try:
                _vision_hint = (
                    "注意：前面的看屏/截图工具因视觉模型额度耗尽或不可用而失败，"
                    "请你直接告诉用户『我的看图能力暂时用不了（免费额度用完或开关没开），"
                    "但文字聊天正常』，并基于已知信息（已打开的应用、窗口名等）尽量回答，"
                    "不要再调用 see_screen / screenshot。"
                    if _vision_fail >= 2 else "")
                summary_msgs = openai_msgs + [{
                    "role": "user",
                    "content": "（你已经达到本轮最大操作步数，不能再调用任何工具了。"
                               "请用一句自然的中文总结：你刚刚完成了哪些操作、结果如何、"
                               "还有什么没做完需要用户确认。不要调用工具，直接回答。）"
                               + _vision_hint,
                }]
                body2 = dict(body)
                body2.pop("tools", None)
                body2.pop("tool_choice", None)
                body2["messages"] = summary_msgs
                resp = await client.post(url, json=body2, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                _qm_record(self._model, ((data.get("usage") or {}).get("total_tokens") or 0), self._base_url)
                final = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if final.strip():
                    if on_status is not None:
                        if _tools_failed and _tools_total:
                            await _status({"phase": "done", "done": True, "partial": True,
                                           "ok_count": _tools_ok, "total_count": _tools_total,
                                           "failed_tools": list(_failed_names),
                                           "text": "部分完成（%d/%d 步达成，%s 未成功）"
                                                   % (_tools_ok, _tools_total,
                                                      "、".join(_failed_names[:3]))})
                        else:
                            await _status({"phase": "done", "done": True, "partial": False,
                                           "ok_count": _tools_ok, "total_count": _tools_total,
                                           "text": "操作完成，正在组织回复…"})
                    return final.strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("步数用尽后的总结请求失败: %s", e)
            if on_status is not None:
                if _tools_failed and _tools_total:
                    await _status({"phase": "done", "done": True, "partial": True,
                                   "ok_count": _tools_ok, "total_count": _tools_total,
                                   "failed_tools": list(_failed_names),
                                   "text": "未全部完成（%d/%d 步达成，%s 卡住）"
                                           % (_tools_ok, _tools_total,
                                              "、".join(_failed_names[:3]))})
                else:
                    await _status({"phase": "done", "done": True, "partial": False,
                                   "ok_count": _tools_ok, "total_count": _tools_total,
                                   "text": "操作完成，正在组织回复…"})
            return "（已到本轮操作步数上限，先停一下。上面是已经做完的部分；若还没完成，告诉我还要接着做什么，我重新帮你做。）"

    async def chat_stream(self, messages: list[dict], options: Optional[dict] = None):
        """流式调用（带高占用自愈重试）：逐 chunk 产出增量文本片段（SSE data: 行解析）。

        重试主要针对建连/首包阶段的高占用（429/503/超时）；已进入流式后中断则直接抛出，
        由上层（TextChatRouter / dialogue）捕获并退化。
        """
        if not self.available:
            raise RuntimeError("OpenAI 兼容服务未配置（base_url/model 为空），请检查 config.yaml 的 llm.openai")
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(messages),
            "stream": True,
        }
        if self._max_tokens:
            body["max_tokens"] = self._max_tokens
        if options:
            # enable_thinking 由 _thinking_body 按厂商翻译，不进白名单。
            for k in ("temperature", "top_p", "max_tokens"):
                if k in options and options[k] is not None:
                    body[k] = options[k]
        # 【2026-10-12 治高延迟】纯聊天默认关思考：实测首字 22.3s → 1.0s。
        #   思维链（reasoning_content）客户端本就丢弃，开了纯等。
        body.update(self._thinking_body(options))
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = f"{self._base_url}/chat/completions"
        last_err: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            line = (line or "").strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[len("data:"):].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                _u = chunk.get("usage")
                                if _u and _u.get("total_tokens"):
                                    _qm_record(self._model, _u["total_tokens"], self._base_url)
                                delta = chunk["choices"][0]["delta"].get("content") or ""
                            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                                continue
                            if delta:
                                yield delta
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < _MAX_ATTEMPTS and _is_retryable_exc(e):
                    wait = _backoff_seconds(
                        attempt,
                        getattr(e, "response", None).headers if isinstance(e, httpx.HTTPStatusError) else None,
                    )
                    logger.warning(
                        "云端 LLM 流式高占用/瞬时错误（%s），第 %d/%d 次重试，%.1fs 后重连",
                        (getattr(e, "response", None) and getattr(e.response, "status_code", "")) or type(e).__name__,
                        attempt, _MAX_ATTEMPTS, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise RuntimeError(f"OpenAI 兼容接口流式调用失败 ({self._base_url}): {e}") from e
        raise RuntimeError(f"OpenAI 兼容接口流式调用失败 ({self._base_url}): {last_err}") from last_err
