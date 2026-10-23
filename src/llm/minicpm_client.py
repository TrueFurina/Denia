"""MiniCPM-o 4.5 Realtime 免费云端 LLM 客户端（WebSocket）。

协议参考官方文档：https://minicpmo45.modelbest.cn/docs/zh/realtime-api/
使用 mode=chat 的 turn-based 对话：连接 → 等待 session.queue_done →
session.init → input.append → 收集 response.output.delta / response.done。
支持多模态图片（messages[].content 列表中的 {type: image, data: base64}），
官方当前免费、无需 API Key。

配置（config.yaml → llm.minicpm）：
  endpoint:        wss 地址（默认官方免费网关）
  max_new_tokens:  生成长度上限
  timeout_s:       生成阶段超时（收到排队后等结果）
  recv_timeout_s:  单条消息等待上限（排队阶段靠它做心跳判活）
  queue_max_wait_s: 排队最长等待（超出即放弃，避免无限排队）
  health_check_s:  网关 /status 探测间隔
  fail_cooldown_s / fail_cooldown_threshold: 连续超时后进本地冷却，期间不发请求

【2026-08-31 排队治理】免费网关是排队制，且 worker 会被僵尸会话长期占死
（实测 14 worker 中 11 offline、3 个被 10 小时前的 omni_duplex 会话占死，吞吐=0）。
原实现有三个问题，均已修复：
  1. 超时/异常时不发 `session.close`、不退排队票 → 每次失败都往死队列里灌一张
     僵尸票，越超时排队越长（自我 DoS）。现：任何退出路径都关会话 + DELETE 退票。
  2. 排队阶段用固定 timeout → 排队稍久就误判失败。现：只要还收到排队消息就续期
     （心跳式），只有"长时间无消息"或"超过总上限"才放弃。
  3. 网关没 worker 时仍按节奏狂发请求。现：后台探测 /status，无空闲 worker 时
     主动让 vision_available=False（上层静默跳过，不计入失败计数、不触发全局禁用）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from typing import Optional

import websockets

from ..config import Config
from .base import LLMClient

logger = logging.getLogger(__name__)

# 【2026-10-02 安全整改】原默认值指向已废弃的免费网关 wss://minicpmo45.modelbest.cn
# （实测 WS 1225 恒失败），任何未显式传 endpoint 的调用都会静默连上这个死服务，
# 且失败被 log+except 吞掉 → 表现为"功能莫名不工作"，极难排查。
# 现改为**默认空端点 + 构造时显式抛错**：必须由调用方提供用户自配端点，
# 杜绝静默使用任何共享免费服务。需要用该客户端请传 endpoint=（或配 llm.minicpm.endpoint）。
DEFAULT_ENDPOINT = ""

# 判定"网关拥堵"的阈值：有任务在跑但一个空闲 worker 都没有，且这些任务都已跑
# 很久（僵尸会话）→ 新请求排多久都不会被服务。
_STALE_TASK_S = 300.0

# 网关 SSL 证书已过期（2026-09 实测 CERTIFICATE_VERIFY_FAILED: certificate has
# expired），忽略证书校验以穿透该层；WS/HTTP 通信内容本身不受影响，仅为绕开
# 过期证书，使云端 MiniCPM 在官方换证前仍可用。
_SSL_CTX = ssl._create_unverified_context()


class MiniCPMClient(LLMClient):
    name = "minicpm-realtime"

    def __init__(self, cfg: Config, endpoint: str = "") -> None:
        """endpoint 可覆盖 llm.minicpm 配置（视觉模型路由层按条目指定时用）。"""
        llm_ns = getattr(cfg.ns, "llm", None)
        mc = getattr(llm_ns, "minicpm", None)
        self._endpoint = (endpoint or getattr(mc, "endpoint", "") or DEFAULT_ENDPOINT).strip()
        if not self._endpoint:
            # 不再静默回落到已废弃的免费网关：没有自配端点就明确失败（见 DEFAULT_ENDPOINT 注释）。
            raise ValueError(
                "MiniCPMClient 缺少端点：已废弃的免费网关 (minicpmo45.modelbest.cn) 不再作为默认。"
                "请显式传 endpoint=（并在 config 配 llm.minicpm.endpoint），"
                "或改用 vision_router / text_chat 路由里的自配模型。")
        self._max_new_tokens = int(getattr(mc, "max_new_tokens", 512) or 512)
        self._timeout = float(getattr(mc, "timeout_s", 60) or 60)
        self._temperature = float(getattr(mc, "temperature", 0.6) or 0.6)
        # ---- 排队治理参数 ----
        self._recv_timeout = float(getattr(mc, "recv_timeout_s", 30) or 30)
        self._queue_max_wait = float(getattr(mc, "queue_max_wait_s", 180) or 180)
        self._health_interval = float(getattr(mc, "health_check_s", 60) or 60)
        self._cooldown_s = float(getattr(mc, "fail_cooldown_s", 120) or 120)
        self._cooldown_threshold = int(getattr(mc, "fail_cooldown_threshold", 2) or 2)
        # ---- 运行时状态 ----
        self._consec_fail = 0
        self._down_until = 0.0        # 本地冷却截止（连续超时触发）
        self._gw_congested = False    # /status 判定：无空闲 worker 且任务僵死
        self._gw_checked_at = 0.0
        self._probe_task: Optional[asyncio.Task] = None
        self._bg_tasks: set[asyncio.Task] = set()

    # ==================== 可用性（含网关健康感知） ====================
    @property
    def available(self) -> bool:
        return bool(self._endpoint)

    def _ready(self) -> bool:
        """此刻是否值得向网关发请求（视觉与文字对话共用）。

        四种情况返回 False：
          1. 未配置端点；
          2. 连续超时触发本地冷却；
          3. /status 显示网关无空闲 worker；
          4. 首次探测尚未完成（防"抢跑"）。
        """
        if not self.available:
            return False
        if time.time() < self._down_until:
            return False
        self._ensure_probe()
        # 首次探测完成前保守跳过：探测是一次 ~0.5s 的 HTTP 请求，启动瞬间若按
        # "乐观可用"放行，会抢跑发出一个注定排不到队的请求（实测启动时因此
        # 多发一次 120s 超时，还白白计入一次 fail_count）。
        if self._gw_checked_at <= 0:
            return False
        if self._gw_congested:
            return False
        return True

    @property
    def vision_available(self) -> bool:
        """视觉是否可用（上层据此静默跳过，不再往死队列里灌票）。"""
        return self._ready()

    def _ensure_probe(self) -> None:
        """启动后台 /status 探测任务（幂等；无事件循环时静默跳过）。"""
        if self._probe_task is not None and not self._probe_task.done():
            return
        try:
            self._probe_task = asyncio.create_task(self._probe_loop())
        except RuntimeError:
            # 没有运行中的事件循环（同步上下文调用）→ 探询轮不启动，状态保持未知
            pass

    async def _probe_loop(self) -> None:
        """周期性探测网关是否有空闲 worker（顺带驱动"恢复"）。"""
        while True:
            try:
                await self._probe_gateway()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 探测失败不影响主链路
                logger.debug("MiniCPM 网关健康探测异常", exc_info=True)
            await asyncio.sleep(self._health_interval)

    async def _probe_gateway(self) -> None:
        st = await asyncio.to_thread(self._fetch_status)
        if st is None:
            return
        self._gw_checked_at = time.time()
        idle = int(st.get("idle_workers") or 0)
        busy = int(st.get("busy_workers") or 0)
        tasks = st.get("running_tasks") or []
        # 判据只看"有没有空闲 worker"：没有空闲 worker，新请求就必然排队等待，
        # 发了也只是给队列添一张票。
        # 【2026-08-31 修正】原判据额外要求"任务僵死"，但实测某次 /status 的
        # running_tasks 会短暂为空 → 误判为"已恢复" → 闸门错误打开，立刻又灌
        # 一张排队 120s 的票（日志 03:48:42 实锤）。僵死信息只用于日志措辞。
        congested = idle <= 0
        stale = bool(tasks) and all(
            float(t.get("elapsed_s") or 0) > _STALE_TASK_S for t in tasks)
        was = self._gw_congested
        self._gw_congested = congested
        if congested and not was:
            if stale:
                logger.warning(
                    "MiniCPM 网关拥堵：idle=%d busy=%d offline=%s，%d 个任务均已运行 >%.0fs"
                    "（疑似僵尸会话占死 worker）→ 暂停视觉请求，待其恢复后自动继续",
                    idle, busy, st.get("offline_workers"), len(tasks), _STALE_TASK_S)
            else:
                logger.warning(
                    "MiniCPM 网关无空闲 worker（idle=%d busy=%d offline=%s）→ "
                    "暂停视觉请求，待其恢复后自动继续",
                    idle, busy, st.get("offline_workers"))
        elif was and not congested:
            logger.info("MiniCPM 网关已恢复（idle=%d）→ 视觉请求自动继续", idle)

    def _http_base(self) -> str:
        """wss://host/path → https://host（用于 /status 与退票接口）。"""
        e = self._endpoint
        if e.startswith("wss://"):
            return "https://" + e[6:].split("/")[0]
        if e.startswith("ws://"):
            return "http://" + e[5:].split("/")[0]
        return e

    def _fetch_status(self) -> Optional[dict]:
        """同步 GET /status（在线程里跑，不阻塞事件循环）。"""
        try:
            req = urllib.request.Request(
                self._http_base() + "/status",
                headers={"User-Agent": "ai-companion/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError):
            # 服务没起/网络不通/返回不是 JSON → 状态未知（None），上层按未就绪处理
            return None

    # ==================== 排队票管理 ====================
    def _spawn_release(self, ticket_id: str) -> None:
        """后台退票：不阻塞当前协程，且不受调用方被取消的影响。

        用独立 task 是刻意的——超时路径上当前协程正被 CancelledError 拆掉，
        此时 await 新请求会立刻再被取消，只有独立 task 能真正把票退掉。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 同步上下文里没有 loop → 退票任务挂不上，由后台重试探路
            return
        task = loop.create_task(self._release_ticket(ticket_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _release_ticket(self, ticket_id: str) -> None:
        """DELETE /api/queue/{ticket_id}：主动退出排队，不留僵尸票。"""
        try:
            await asyncio.to_thread(self._delete_ticket, ticket_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 退票失败无所谓，不影响主链路
            logger.debug("MiniCPM 退票失败 ticket=%s", ticket_id, exc_info=True)

    def _delete_ticket(self, ticket_id: str) -> None:
        try:
            req = urllib.request.Request(
                self._http_base() + "/api/queue/" + ticket_id,
                method="DELETE",
                headers={"User-Agent": "ai-companion/1.0"},
            )
            with urllib.request.urlopen(req, timeout=6, context=_SSL_CTX) as resp:
                resp.read()
            logger.debug("MiniCPM 已退票 ticket=%s", ticket_id)
        except (urllib.error.URLError, OSError, ValueError):
            # 退票请求失败（服务已下线/网络抖）→ 票由服务端超时回收，不重试轰炸
            pass

    def _note_failure(self) -> None:
        """记录一次失败；连续达到阈值进本地冷却，避免在死网关上空转刷票。"""
        self._consec_fail += 1
        if self._consec_fail >= self._cooldown_threshold:
            self._down_until = time.time() + self._cooldown_s
            logger.warning(
                "MiniCPM 连续失败 %d 次 → 本地冷却 %.0fs（期间跳过视觉请求，不再灌票）",
                self._consec_fail, self._cooldown_s)
            self._consec_fail = 0

    @property
    def model_name(self) -> Optional[str]:
        return "minicpm-o-4.5"

    def _to_messages(self, messages: list[dict]) -> list[dict]:
        """内部消息格式 → MiniCPM 协议（图片转 base64 多模态 content）。"""
        out: list[dict] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            images = m.get("images") or []
            if images:
                parts: list[dict] = []
                if content:
                    parts.append({"type": "text", "text": content})
                for img in images:
                    parts.append({"type": "image", "data": base64.b64encode(img).decode("ascii")})
                out.append({"role": role, "content": parts})
            else:
                out.append({"role": role, "content": content})
        return out

    @staticmethod
    def _raise_error(msg: dict) -> None:
        err = msg.get("error") or {}
        raise RuntimeError(f"MiniCPM Realtime 错误 [{err.get('code', 'unknown')}]: {err.get('message', msg)}")

    async def _request(self, messages: list[dict]) -> str:
        # 显式 proxy=None=直连(忽略 env 代理)，避开 WorkBuddy transparent_proxy 等
        # 会把 WS 握手挂起的本地代理（2026-08-29 修复）
        # 免费云端会偶发限流/握手超时 → 加重试+退避，遮掉绝大多数瞬时抖动
        _max_retry = 3
        _backoff = [1.0, 2.0]
        _last_err: Optional[Exception] = None
        for _attempt in range(_max_retry):
            # 重试前复查一次：若期间已被判定冷却/拥堵，立刻停手——
            # 否则每次重试都往队列里多灌一张僵尸票。
            if _attempt > 0 and not self.vision_available:
                break
            try:
                text = await self._request_once(messages)
                self._consec_fail = 0
                self._down_until = 0.0
                return text
            except (asyncio.TimeoutError, OSError, websockets.exceptions.WebSocketException) as e:
                # 连接级/超时异常 → 重试；协议层 error（_raise_error 抛 RuntimeError）不重试
                _last_err = e
                self._note_failure()
                _wait = _backoff[_attempt] if _attempt < len(_backoff) else _backoff[-1]
                logger.warning("MiniCPM 云端连接异常（第%d/%d次）：%s，%ss 后重试",
                               _attempt + 1, _max_retry, e, _wait)
                if _attempt < _max_retry - 1:
                    await asyncio.sleep(_wait)
        logger.error("MiniCPM 云端连接失败，已重试 %d 次：%s", _max_retry, _last_err)
        raise RuntimeError(f"MiniCPM 云端连接失败（重试 {_max_retry} 次）：{_last_err}") from _last_err

    async def _request_once(self, messages: list[dict]) -> str:
        ticket_id: Optional[str] = None
        text_parts: list[str] = []
        done_text = ""
        async with websockets.connect(self._endpoint, open_timeout=min(self._recv_timeout, 30),
                                      ping_interval=None, max_size=64 * 1024 * 1024,
                                      proxy=None, ssl=_SSL_CTX) as ws:
            try:
                # 1) 排队（心跳式：只要还在收到排队消息就说明服务端活着，继续等）
                deadline = time.monotonic() + self._queue_max_wait
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout)
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t in ("session.queued", "session.queue_update"):
                        ticket_id = msg.get("ticket_id") or ticket_id
                        if time.monotonic() > deadline:
                            raise asyncio.TimeoutError(
                                f"排队超过 {self._queue_max_wait:.0f}s 仍未获得服务")
                        continue
                    if t == "session.queue_done":
                        break
                    if t == "error":
                        self._raise_error(msg)

                # 2) 初始化会话（chat 模式空 payload）
                await ws.send(json.dumps({"type": "session.init", "payload": {}}))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout)
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "session.created":
                        break
                    if t == "error":
                        self._raise_error(msg)

                # 3) 提交输入（non-streaming：完整文本在 response.done 里）
                await ws.send(json.dumps({
                    "type": "input.append",
                    "input": {
                        "messages": self._to_messages(messages),
                        "streaming": False,
                        "generation": {
                            "max_new_tokens": self._max_new_tokens,
                            "temperature": self._temperature,
                        },
                    },
                }))

                # 4) 收集输出
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "response.output.delta":
                        if msg.get("kind") == "text" and msg.get("text"):
                            text_parts.append(msg["text"])
                    elif t == "response.done":
                        done_text = msg.get("text") or ""
                        break
                    elif t == "error":
                        self._raise_error(msg)
            finally:
                # 关闭会话 + 退票：成功、失败、被上层 wait_for 取消，都要走这里。
                # 少了这一步，每次失败都会给网关留一张僵尸票（实测 6 分钟把队列
                # 从 8 灌到 14），越超时排队越长。
                try:
                    await ws.send(json.dumps({"type": "session.close", "reason": "turn_done"}))
                except Exception:
                    # 连接已经断了也要继续走下面的退票（不能因为关不了会话就留僵尸票）
                    pass
                if ticket_id:
                    self._spawn_release(ticket_id)

        # streaming=False 时 deltas 与 done.text 内容一致，优先用 done 完整文本
        return (done_text or "".join(text_parts)).strip()

    async def chat(self, messages: list[dict], options: Optional[dict] = None) -> str:
        if not self.available:
            raise RuntimeError("MiniCPM endpoint 未配置")
        # 文字对话同样走这个网关（llm.provider=minicpm），网关拥堵时必须一起拦：
        # 否则打字会先干等 120s 排队超时才报错，界面像卡死。快速失败后 UI 能
        # 立刻提示"云端不可用"，用户改走本地双工即可（2026-08-31）。
        if not self._ready():
            raise RuntimeError(
                "MiniCPM 云端暂时不可用（网关无空闲 worker 或处于失败冷却），"
                "已跳过本次对话请求")
        return await self._request(messages)

    async def vision_describe(self, image_bytes: bytes, prompt: str) -> str:
        return await self._request(
            messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        )
