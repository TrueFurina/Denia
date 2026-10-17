"""MiniCPM-o 4.5 云端真全双工客户端（WebSocket，mode=video）。

与 turn-based 的 MiniCPMClient（一问一答）不同：这是"持续推流"的全双工会话——
每秒把系统声音（WASAPI 环回采集的游戏音频）+ 屏幕截图推给云端模型，模型完全
自主决定什么时候开口（response.output.delta kind=audio/text）、什么时候安静
（kind=listen），并直接返回合成语音（float32 @24kHz base64），不需要本地 TTS。

协议（官方 examples/realtime/video_probe.py 确认，2026-08 当前真实网关协议）：
  1. 连接 wss://minicpmo45.modelbest.cn/v1/realtime?mode=video
  2. 排队：session.queued / session.queue_update / session.queue_done
  3. queue_done → 上行 session.init {payload: {system_prompt}}
  4. 收到 session.created（带 session_id）→ 开始每秒推流 input.append：
       {type: input.append,
        input: {audio: base64(float32@16kHz 1秒),
                video_frames: [base64(JPEG)],   # 可选
                force_listen: false,            # 非游戏前台时 true 强制闭嘴
                max_slice_nums: 1}}
  5. 下行：response.output.delta（kind=listen 安静/一轮结束 /
            kind=text 文本增量 / kind=audio 语音增量 base64 float32@24kHz）
          + response.done（整轮结束，带完整 text）/
            session.closed（会话到期/被关）→ 自动重连
  6. 会话到期（视频模式 5 分钟）主动 session.close 换新会话

断线/异常自动重连（指数退避 3~30s）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable, Optional

from ..perception._pa_shared import get_shared_pyaudio, safe_find_loopback_device  # 共享 PortAudio 单例
from ..perception.process_loopback import start_self_excluded_capture, self_speak_active, _safe_find_wasapi_loopback

logger = logging.getLogger(__name__)

# 【2026-10-02 安全整改】原默认值指向已废弃的免费网关 wss://minicpmo45.modelbest.cn
# （实测 WS 1225 恒失败；这也是 16:54 通话没声音的直接原因）。任何未显式传 endpoint
# 的调用都会静默连上这个死服务。现改为**默认空端点 + 构造时显式抛错**。
# 注意：本类只是"云端真双工"的**协议客户端实现**（与厂商无关），配合用户自配的
# cloud_duplex.voice/video 端点使用是合理的；禁掉的只是那个免费共享端点。
DEFAULT_DUPLEX_ENDPOINT = ""

# 网关 SSL 证书已过期（2026-09 实测），忽略证书校验以穿透该层（通信内容不受影响）
_SSL_CTX = ssl._create_unverified_context()

# 上行音频：float32 采样率 / 每秒采样数（官方 demo SAMPLE_RATE_IN）
SAMPLE_RATE_IN = 16000
# 下行语音：float32 采样率（官方 demo SAMPLE_RATE_OUT）
SAMPLE_RATE_OUT = 24000

# 视频帧频率：每 N 个上行 tick 发一帧截图（每秒 1 tick）。帧率越高越费流量，
# 官方 demo 默认每秒 1 帧；这里默认每 2 秒 1 帧，够模型感知画面变化。
# 【2026-09-10 说明】每 N 个推流 tick 附一帧（1 tick = 1 秒）。
# 曾试过改为 1（约 1fps，对齐 pub-local-jarvis），但实测（同一场景对照 14:41 vs 15:25）
# 开口数 25 → 9、且出现重复碎语，属**负收益**，故保持 2（约 0.5fps）。
VIDEO_FRAME_EVERY = 2

# 会话建立后前 N 轮强制 force_listen（pubJavis force_listen_count 等效）：
# 让模型先收画面+音频建立上下文再开口，防开局乱语。取 2（约 2 秒，覆盖
# 首帧画面推流，B 方案补推轮本身已 force_listen，这里兜底正常轮）。
FIRST_LISTEN_ROUNDS = 2

# 收消息超时（单次 recv 等待上限）
RECV_TIMEOUT_S = 10.0
# 【2026-09-10 修复·会话被误判"无数据"→每 31s 重连】原值 = 3（即 30s 收不到应用帧就判死）。
# 但双工服务端在模型保持"安静/LISTEN"时**本来就可能长时间不发任何帧**——实测（run_125214）：
# 建会话后服务端 30s 零帧 → 客户端判死 → 3s 后重连 → 再 30s 又死，形成
# 「会话建立(sess_x) → 连接超时(无数据) → 重连」**每 31 秒一轮**的死循环，
# 每次都重新 session.init 提示词、丢掉模型已积累的上下文。
# 改为 30（=300s，**大于** SESSION_MAX_S 280s 的自然换会话间隔）⇒ 空闲不再误杀；
# 真正的连接死亡改由 **WebSocket ping/pong** 判定（见 _run_session 的 ping_interval）。
RECV_MAX_IDLE = 30
# WS 层保活：库每 PING_INTERVAL_S 发一次 ping，PING_TIMEOUT_S 内收不到 pong 即判死。
# 实测网关（uvicorn+websockets）支持：静默 35s（不发任何 input.append）连接仍存活。
PING_INTERVAL_S = 20.0
PING_TIMEOUT_S = 20.0

# 重连退避
RECONNECT_BASE_S = 3.0
RECONNECT_MAX_S = 30.0

# 会话最长时长（视频模式官方 5 分钟，到时主动换会话防静默失效）
SESSION_MAX_S = 280.0
# 会话到期前主动换新的软限制（秒）：比服务端到期窗口提前约 35s，避开服务端
# 强制关流瞬间的输出崩坏（日志实锤：开口乱码总是紧挨着"会话到期"出现）。
SESSION_SOFT_LIMIT_S = 245.0
# 换会话前最多等当前输出轮自然结束的时长（秒）——避免说话中途被 close 截断。
SESSION_WAIT_TURN_S = 6.0


class MiniCPMDuplexClient:
    """云端真全双工客户端。

    回调（全部 async，在事件循环内执行）：
      on_speak(text, audio_pcm, sample_rate)  模型开口一轮说完（含原生语音）
      on_status(msg)                          状态日志（排队/连接/重连）
      on_error(msg)                           可恢复错误
    """

    name = "minicpm-duplex"

    def __init__(
        self,
        endpoint: str = "",
        on_speak: Optional[Callable[[str, bytes, int], Awaitable[None]]] = None,
        on_status: Optional[Callable[[str], Awaitable[None]]] = None,
        on_error: Optional[Callable[[str], Awaitable[None]]] = None,
        screenshot_fn: Optional[Callable[[], Optional[bytes]]] = None,
        capture_audio: bool = True,
        hist_frame_inject: bool = True,
        on_listen: Optional[Callable[[], Awaitable[None]]] = None,
        api_key: str = "",
    ) -> None:
        self._endpoint = (endpoint or DEFAULT_DUPLEX_ENDPOINT).strip()
        if not self._endpoint:
            # 不再静默回落到已废弃的免费网关（见 DEFAULT_DUPLEX_ENDPOINT 注释）。
            # 云端双工必须由用户自备端点（cloud_duplex.voice/video）或改用 cloud_omni。
            raise ValueError(
                "MiniCPMDuplexClient 缺少端点：已废弃的免费网关 (minicpmo45.modelbest.cn) "
                "不再作为默认。请配置 cloud_duplex.voice/video 端点，或启用 cloud_omni"
                "（Qwen-Omni 云端双工，推荐）。")
        self._api_key = (api_key or "").strip()
        self._on_speak = on_speak
        self._on_status = on_status
        self._on_error = on_error
        self._on_listen = on_listen or (lambda: None)
        # 截图回调（同步，返回 JPEG bytes）：每帧推流时取一帧
        self._screenshot_fn = screenshot_fn
        self._capture_audio = capture_audio
        # 重连后补推历史帧（B 方案，2026-08-24）：新会话建立后第一轮用
        # 最近几帧截图 + 真实音频补推一次，让新会话"看到"刚才的画面，尽量
        # 追平本地双工的连续上下文。可由配置关闭（关闭后回退纯文字注入）。
        self._hist_frame_inject = hist_frame_inject
        # 最近推流过的画面帧缓存 (时间戳, JPEG bytes)，maxlen 限 6 帧
        # （约 12 秒画面轨迹，够换会话续忆又不占内存）。
        self._frame_hist: deque[tuple[float, bytes]] = deque(maxlen=6)
        # 【2026-09-10】最近一帧的 base64 + 时间戳：供 proactive.snapshot() 给开发者面板
        # 预览"模型当前看到的画面"（此前只有本地 omni 客户端填，云端模式下面板恒显示"无"）。
        self._last_frame_b64: Optional[str] = None
        self._last_frame_ts: float = 0.0

        self._running = False
        # 当前会话 WS 与运行任务引用：stop() 时主动关 WS + 取消任务，避免 recv 阻塞
        # 直到 RECV_TIMEOUT*RECV_MAX_IDLE 才自然退出（挂断/切模式时残留会话挂起 ~30s）。
        self._ws: Any = None
        self._run_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._instructions: str = ""
        # 【2026-09-07 修复·拨号真值】通话切换提示词时请求强制重建一次会话，
        # 让本次提示词经 session.init 立即下发（而非等 245s 自然到期），并产生
        # 真实"会话建立"事件供前端判"下一次真实连接"。recv_loop 检测到即退当前会话。
        self._reinit_requested = False
        self._force_listen = False       # 前台不满足时强制模型闭嘴
        self._session_started_at = 0.0   # 当前会话开始时间（到期主动换会话）
        self._turn_active = False        # 当前是否正在输出一轮说话（换会话时先等它结束）
        self._video_tick = 0

        # WASAPI 环回采集（独立线程）：音频块（float32@16kHz 字节）放入此队列
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._capture_stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._privacy = False  # 隐私模式：不推真实音频/画面，强制闭嘴
        self._call_mute = False  # 通话静音：停环回采集，模型听不到通话对方声音

    # ===== 外部控制 =====
    def set_instructions(self, text: str) -> None:
        """设置/刷新系统提示（游戏上下文变化时更新，重连时用最新值）。"""
        self._instructions = text

    def request_session_restart(self) -> None:
        """请求强制重建一次会话（通话切换提示词时用）。

        置位 _reinit_requested，_recv_loop 在下一轮检测到即退出当前会话 → 外层
        start() 循环重建新会话 → session.init 用最新 _instructions → 真实"会话建立"。
        若当前没在跑（_running=False），置位即可，下次 start() 自然用新提示词。
        """
        self._reinit_requested = True

    def reprepare(self) -> None:
        """与本地 omni 客户端同名的兼容入口：请求重建会话以让最新提示词生效。

        【2026-09-10】本地客户端（local_omni_duplex）有 `reprepare()`，云端此前**没有**
        这个方法——而 proactive 的 KB 重注（`_kb_reprepare_with_popup`）/人设刷新都写着
        `if hasattr(client, 'reprepare'): client.reprepare()`，于是**切软件/进游戏后新注入的
        三段式提示词在云端不会被下发**，只能等 `SESSION_MAX_S`（~245s）自然轮换才生效
        （表现为"云端陪伴迟迟不按三角洲话术互动"）。这里补齐同名方法，语义 = 强制重建
        一次会话 → `session.init` 用最新 `_instructions` → 提示词**立即生效**。
        """
        self.request_session_restart()

    def set_force_listen(self, flag: bool) -> None:
        """前台不满足时置 True：推流带 force_listen，模型保持安静。"""
        self._force_listen = bool(flag)

    def set_privacy(self, flag: bool) -> None:
        """隐私模式：置 True 时推流只发静音、不发画面帧，并强制模型闭嘴——
        模型无法获取新的屏幕画面和系统声音；恢复 False 后自动继续推流。"""
        self._privacy = bool(flag)
        if self._privacy:
            self._force_listen = True

    def set_call_mute(self, flag: bool) -> None:
        """通话静音：音视频通话/会议/语音频道时置 True——推流端改推静音并强制
        闭嘴（push_loop 里 force_listen 计算已含 _call_mute），模型完全听不到
        通话里对方的声音，避免把好友的话当成用户的话去回应（"NTR"实锤）。

        注意：不停止 WASAPI 采集线程。频繁启停采集会反复重建音频流，导致推给
        模型的音频断裂、模型在破碎上下文里胡言乱语（日志实锤"观察你好似曾么快啊"）。
        静音只是推流侧丢弃真实音频，恢复即继续推流。"""
        self._call_mute = bool(flag)
        if self._call_mute:
            # 清掉残留音频，防止静音生效前把通话里的话推给模型
            while not self._audio_q.empty():
                try:
                    self._audio_q.get_nowait()
                except Exception:  # noqa: BLE001
                    break

    @property
    def running(self) -> bool:
        return self._running

    async def _emit_status(self, msg: str) -> None:
        logger.info("云端双工: %s", msg)
        if self._on_status:
            try:
                await self._on_status(msg)
            except Exception:  # noqa: BLE001
                logger.exception("on_status 回调失败")

    async def _emit_error(self, msg: str) -> None:
        logger.warning("云端双工: %s", msg)
        if self._on_error:
            try:
                await self._on_error(msg)
            except Exception:  # noqa: BLE001
                logger.exception("on_error 回调失败")

    async def _emit_listen(self) -> None:
        """模型转聆听/用户开口：通知上游清双工碎片缓冲。"""
        if self._on_listen:
            try:
                r = self._on_listen()
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # noqa: BLE001
                logger.exception("on_listen 回调失败")

    async def _emit_speak(self, text: str, audio_pcm: bytes) -> None:
        # 双工改走千问/VoxCPM TTS：只要有文本就向上传递（audio_pcm 可为空，
        # 由 proactive 拼句后统一用 TTS 合成），不再依赖网关原生音频。
        if not text:
            return
        if self._on_speak:
            try:
                await self._on_speak(text, audio_pcm or b"", SAMPLE_RATE_OUT)
            except Exception:  # noqa: BLE001
                logger.exception("on_speak 回调失败")

    # ===== 主入口 =====
    async def start(self) -> None:
        """常驻循环：连接 → 会话 → 断开自动重连。被 stop() 终止。"""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        try:
            self._run_task = asyncio.current_task()
        except Exception:  # noqa: BLE001
            self._run_task = None
        self._audio_q = asyncio.Queue()
        if self._capture_audio:
            self._start_capture()
        delay = 0.0
        try:
            while self._running:
                if delay > 0:
                    await self._emit_status(f"%.0fs 后重连" % delay)
                    await asyncio.sleep(delay)
                try:
                    await self._run_session()
                    delay = RECONNECT_BASE_S  # 会话正常结束/到期 → 常规重连
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    await self._emit_error(f"会话异常: {e}")
                    delay = min(max(delay * 2, RECONNECT_BASE_S), RECONNECT_MAX_S) \
                        if delay > 0 else RECONNECT_BASE_S
        finally:
            # 无论正常退出还是被取消，都确保停掉 WASAPI 采集线程，
            # 否则取消路径会泄漏线程 + _running 残留 True（无法重启，审查实锤）
            self._stop_capture()

    async def stop(self) -> None:
        self._running = False
        self._stop_capture()
        # 【修复 2026-09-09·挂断/切模式残留会话挂起】旧实现只置 _running=False + 唤醒推流，
        # 但 recv_loop 可能阻塞在 ws.recv() 上，要等 RECV_TIMEOUT*RECV_MAX_IDLE(≈30s) 才
        # 自然超时退出。这里主动关 WS（让 recv 立即抛 ConnectionClosed）+ 取消会话任务，
        # 确保 stop() 后会话迅速释放，不拖住后续重连/模式切换。
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._run_task is not None and self._run_task is not asyncio.current_task():
            self._run_task.cancel()
        try:
            await self._audio_q.put(None)  # 唤醒推流循环
        except Exception:  # noqa: BLE001
            pass

    # ===== 单次会话 =====
    async def _run_session(self) -> None:
        import websockets

        self._session_started_at = 0.0
        self._video_tick = 0
        # 清空上一会话残留音频（连接前也清一次；session.created 后还会再清，
        # 见下方——排队+init 期间采集线程仍在入队）
        while not self._audio_q.empty():
            try:
                self._audio_q.get_nowait()
            except Exception:  # noqa: BLE001
                break

        await self._emit_status("连接中…")
        _connect_kwargs: dict = dict(
            open_timeout=30,
            ping_interval=PING_INTERVAL_S, ping_timeout=PING_TIMEOUT_S,
            max_size=64 * 1024 * 1024, ssl=_SSL_CTX,
        )
        # 用户自有网关鉴权：把 api_key 作为 Bearer Token 注入 WS 握手头。
        # 官方免费端点无需鉴权，api_key 留空则不附加任何头。
        if self._api_key:
            _connect_kwargs["extra_headers"] = {
                "Authorization": f"Bearer {self._api_key}",
            }
        async with websockets.connect(self._endpoint, **_connect_kwargs) as ws:
            self._ws = ws
            # ---- 排队 ----
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                t = msg.get("type")
                if t in ("session.queued", "session.queue_update"):
                    await self._emit_status(
                        f"排队中（位置{msg.get('position', '?')}/"
                        f"{msg.get('queue_length', '?')}）")
                    continue
                if t == "session.queue_done":
                    await self._emit_status("排队完成，初始化会话…")
                    break
                if t == "error":
                    self._raise_error(msg)

            # ---- 初始化：queue_done → session.init（官方 probe 协议）----
            await ws.send(json.dumps({
                "type": "session.init",
                "payload": {
                    "system_prompt": self._instructions or "（无指令）",
                },
            }))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "session.created":
                    self._session_started_at = time.time()
                    # 排队+init 可能耗时数十秒，期间采集线程持续入队——这些是
                    # "过去的声音"（对当前画面错位）。会话真正建立后再清一次，
                    # 防止模型听到排队等待期间的旧声音（审查 R2 实锤）。
                    while not self._audio_q.empty():
                        try:
                            self._audio_q.get_nowait()
                        except Exception:  # noqa: BLE001
                            break
                    await self._emit_status(f"会话建立（{msg.get('session_id', '')}）")
                    break
                if t == "error":
                    self._raise_error(msg)

            # ---- 双循环：推流 + 接收 ----
            push_task = asyncio.create_task(self._push_loop(ws), name="duplex-push")
            try:
                await self._recv_loop(ws)
            finally:
                push_task.cancel()
                try:
                    await push_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            try:
                await ws.send(json.dumps({"type": "session.close", "reason": "turn_done"}))
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _raise_error(msg: dict) -> None:
        err = msg.get("error") or {}
        raise RuntimeError(
            f"云端双工错误 [{err.get('code', 'unknown')}]: {err.get('message', msg)}")

    # ===== 上行推流 =====
    # 推流画面最大宽度（超过则等比缩放，控制上行体积）
    FRAME_MAX_WIDTH = 768

    @staticmethod
    def _to_jpeg_bytes(frame: Any) -> bytes:
        """把截图结果统一转成 JPEG 字节：PIL.Image → JPEG；bytes 原样返回。

        【2026-09-10】`_screenshot_fn`（proactive._make_screenshot_fn）返回的是
        **PIL.Image 对象**（两条兜底路径都 return img），而推流要的是 JPEG 字节；
        直接 `base64.b64encode(Image)` 会抛 TypeError 并杀死推流循环。
        """
        if isinstance(frame, (bytes, bytearray)):
            return bytes(frame)
        try:
            from PIL import Image  # noqa: F401  （确保可用）
        except Exception:  # noqa: BLE001
            return b""
        try:
            import io as _io

            img = frame
            w = getattr(img, "width", 0) or 0
            if w and w > MiniCPMDuplexClient.FRAME_MAX_WIDTH:
                h = max(1, int((getattr(img, "height", 1) or 1)
                               * MiniCPMDuplexClient.FRAME_MAX_WIDTH / w))
                img = img.resize((MiniCPMDuplexClient.FRAME_MAX_WIDTH, h))
            if getattr(img, "mode", "RGB") != "RGB":
                img = img.convert("RGB")
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return b""

    async def _push_loop(self, ws: Any) -> None:
        silence = b"\x00" * (SAMPLE_RATE_IN * 4)  # 1 秒静音 float32
        while self._running:
            # 音频块（1 秒 float32@16kHz）：没有就推静音（模型仍能看图）
            try:
                chunk = await asyncio.wait_for(self._audio_q.get(), timeout=0.3)
            except asyncio.TimeoutError:
                chunk = silence
            if chunk is None:
                return  # stop() 唤醒
            # 隐私模式：不推真实系统声音（发静音），画面帧也不发（screenshot_fn
            # 已返回 None，这里再显式跳过），并强制模型闭嘴——云端收不到画面/声音
            if self._privacy or self._call_mute:
                chunk = silence
            # 官方 probe 协议：input.append，字段在 input 嵌套对象里
            audio = base64.b64encode(chunk).decode("ascii")
            # 【首帧强制倾听（2026-08-25 学 pub-local-jarvis force_listen_count=1）】
            # 会话建立后前 FIRST_LISTEN_ROUNDS 轮只 LISTEN 不 SPEAK：让模型先收
            # 一帧画面+音频建立上下文再决定开口，避免开局对陌生画面乱发言
            # （实锤：换会话瞬间模型对首帧画面脑补乱语）。
            first_listen = self._video_tick < FIRST_LISTEN_ROUNDS
            inp: dict[str, Any] = {
                "audio": audio,
                "force_listen": bool(
                    self._force_listen or self._privacy or self._call_mute
                    or first_listen),
                "max_slice_nums": 1,
            }
            # 视频帧：每 VIDEO_FRAME_EVERY 个 tick 一帧
            if (not self._privacy and self._screenshot_fn is not None
                    and self._video_tick % VIDEO_FRAME_EVERY == 0):
                try:
                    frame = await asyncio.to_thread(self._screenshot_fn)
                except Exception:  # noqa: BLE001
                    frame = None
                if frame:
                    # 【2026-09-10 决定性修复·"模型全程不开口"】`_screenshot_fn`
                    # （proactive._make_screenshot_fn）返回的是 **PIL.Image 对象**，而
                    # `base64.b64encode` 只吃 bytes → 原代码在**首帧**就抛
                    # `TypeError: a bytes-like object is required, not 'Image'`
                    # → 推流循环(_push_loop)直接死掉 → 之后 audio/video 一帧都推不出去
                    # → 服务端收不到任何 input → 模型永远不产出、会话每 ~300s 被判
                    # 空闲超时重连（用户实测"打了好久一句话没说"）。
                    # （异常被上层以 debug 吞掉，故日志里看不到任何 Traceback。）
                    _jpeg = self._to_jpeg_bytes(frame)
                    if not _jpeg:
                        pass
                    else:
                        _b64 = base64.b64encode(_jpeg).decode("ascii")
                        inp["video_frames"] = [_b64]
                        # 顺带缓存该帧供重连后补推（B 方案记忆接续）
                        self._frame_hist.append((time.time(), _jpeg))
                    # 【2026-09-10 修复·开发者面板"推流画面=无"】proactive.snapshot() 的预览帧
                    # 只从客户端 `_last_frame_b64` 取，而该字段此前**只有本地 omni 客户端**
                    # 会填（local_omni_duplex.py）→ 一旦切到云端（客户端换成本类），面板就
                    # 永远显示"无画面"，被误读成"云端没连上"。这里让云端客户端也缓存最近一帧。
                    self._last_frame_b64 = _b64
                    self._last_frame_ts = time.time()
            # 【B 方案·重连补推】新会话第一轮：若开了历史帧注入且缓存有帧，
            # 且当前拿到的是真实音频（非静音——探测实锤：推静音/纯噪声会被网关
            # msgs is empty 踢掉），就带最近 3 帧补推一次并 force_listen（只让
            # 模型消化记忆，不开口），下一轮恢复自主裁决。
            if (self._video_tick == 0 and self._hist_frame_inject
                    and self._frame_hist and chunk != silence):
                _mem_frames = [f for _, f in self._frame_hist][-3:]
                _mem_age = time.time() - self._frame_hist[0][0]
                await ws.send(json.dumps({"type": "input.append", "input": {
                    "audio": audio,
                    "video_frames": [
                        base64.b64encode(f).decode("ascii") for f in _mem_frames],
                    "force_listen": True,  # 只看不开口，避免对旧画面误搭话
                    "max_slice_nums": 1,
                }}))
                await self._emit_status(
                    "[双工记忆] 新会话补推历史帧 n=%d (最早帧 %.0fs 前)"
                    % (len(_mem_frames), _mem_age))
                self._video_tick += 1
                await asyncio.sleep(1.0)
                continue  # 补推轮已消耗当前音频块，重新取音频走正常第一轮
            await ws.send(json.dumps({"type": "input.append", "input": inp}))
            self._video_tick += 1
            await asyncio.sleep(1.0)
            # 会话到期主动结束：软限制（提前于服务端到期窗口）优先触发，若模型
            # 正在说话则先等这轮自然结束再关，避免截断成半句/乱码（日志实锤：
            # 乱码开口总是紧挨着"会话到期"出现）；SESSION_MAX_S 为硬兜底。
            elapsed = time.time() - self._session_started_at
            if self._session_started_at and elapsed > SESSION_MAX_S:
                await self._emit_status("会话到期，换新会话")
                await ws.send(json.dumps({"type": "session.close", "reason": "session_expired"}))
                return
            if self._session_started_at and elapsed > SESSION_SOFT_LIMIT_S:
                waited = 0.0
                while self._turn_active and waited < SESSION_WAIT_TURN_S:
                    await asyncio.sleep(0.25)
                    waited += 0.25
                await self._emit_status("会话到期，换新会话")
                await ws.send(json.dumps({"type": "session.close", "reason": "session_expired"}))
                return

    # ===== 下行接收 =====
    async def _recv_loop(self, ws: Any) -> None:
        text_parts: list[str] = []
        audio_buf = bytearray()
        idle = 0

        async def flush() -> None:
            nonlocal text_parts, audio_buf
            t = "".join(text_parts).strip()
            if t:
                await self._emit_speak(t, bytes(audio_buf))
            text_parts = []
            audio_buf = bytearray()

        while self._running:
            # 【2026-09-07 修复·拨号真值】通话请求重建会话：退当前会话 → 外层
            # start() 循环重建新会话（用最新提示词）→ 真实"会话建立"。
            if self._reinit_requested:
                self._reinit_requested = False
                raise ConnectionError("请求重建会话（通话提示词切换）")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_S)
            except asyncio.TimeoutError:
                idle += 1
                if idle >= RECV_MAX_IDLE:
                    raise ConnectionError("连接超时（无数据）")
                continue
            idle = 0
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "response.output.delta":
                kind = msg.get("kind")
                if kind == "text":
                    text = msg.get("text") or ""
                    if text:
                        text_parts.append(text)
                        self._turn_active = True
                elif kind == "audio":
                    audio = msg.get("audio")
                    if audio:
                        audio_buf += base64.b64decode(audio)
                        self._turn_active = True
                elif kind == "listen":
                    # 模型回到聆听/安静：上一轮说话结束
                    self._turn_active = False
                    await flush()
                    await self._emit_listen()
            elif t == "response.done":
                # 整轮结束：delta 没拼出文本时用完整 text 兜底
                self._turn_active = False
                text = msg.get("text") or ""
                if text and not text_parts:
                    text_parts.append(text)
                await flush()
            elif t == "session.closed":
                raise ConnectionError(f"会话关闭（{msg.get('reason', '?')}）")
            elif t == "error":
                self._raise_error(msg)

    # ===== WASAPI 环回采集（独立线程） =====
    def _start_capture(self) -> None:
        self._capture_stop = threading.Event()
        self._capture_thread = threading.Thread(
            target=self._capture_worker, name="duplex-wasapi", daemon=True)
        self._capture_thread.start()

    def _stop_capture(self) -> None:
        self._capture_stop.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

    def _capture_worker(self) -> None:
        """采集系统声音（优先进程级环回排除自身软件声音）→ 16kHz 单声道 float32 → 队列。"""
        import numpy as np

        # 优先进程级环回排除自身软件声音（用户照常能听到达妮娅，只是不进环回）。
        acc = bytearray()
        target = SAMPLE_RATE_IN * 4

        def _sys_on_chunk(arr):
            nonlocal acc
            acc += arr.astype(np.float32).tobytes()
            while len(acc) >= target:
                try:
                    self._audio_q.put_nowait(bytes(acc[:target]))
                except Exception:  # noqa: BLE001
                    pass  # 队列满/停止，丢帧
                del acc[:target]

        if start_self_excluded_capture(self._capture_stop, _sys_on_chunk, SAMPLE_RATE_IN):
            logger.info("云端双工: 系统音=进程级环回排除（已剔除自身软件声音）")
            return

        # 回退：整设备环回（安全枚举，绝不调用会堆损坏的 get_default_wasapi_loopback）
        try:
            import pyaudiowpatch as pyaudio
        except Exception:  # noqa: BLE001
            logger.warning("云端双工: 未安装 pyaudiowpatch 且进程环回不可用，推静音音频（模型仍能看图说话）")
            return

        p = None
        stream = None
        try:
            # 🛡️【2026-09-27 闪退修复】用共享 PortAudio 单例，会话期间绝不 terminate，
            # 避免与游戏音效采集 / 进程环回等线程并发 init/terminate 触发原生访问违规
            # （0xC0000005）。安全枚举环回设备，绝不调用会堆损坏的 get_default_wasapi_loopback()。
            p = get_shared_pyaudio()
            dev = safe_find_loopback_device()
            if dev is None:
                logger.warning("云端双工: 找不到可用的 WASAPI 环回设备（推静音音频）")
                return
            rate = int(dev["defaultSampleRate"])
            channels = max(1, int(dev["maxInputChannels"]))
            logger.info("云端双工: WASAPI 环回采集 %s @%dHz %dch", dev["name"], rate, channels)

            # 累积满 1 秒（16000 采样 float32）才推给上行循环，避免半秒块
            acc = bytearray()
            target = SAMPLE_RATE_IN * 4

            def _cb(in_data, frame_count, time_info, status_flags):
                nonlocal acc
                if in_data is None or len(in_data) == 0:
                    return (None, pyaudio.paContinue)
                arr = np.frombuffer(in_data, dtype=np.float32)
                # 【自言自语闸门】自身软件（达妮娅 TTS）正在出声时把环回清零，
                # 避免模型听到自己刚说的话续写/自言自语（系统层进程环回排除失败时的兜底）。
                if self_speak_active():
                    arr = np.zeros_like(arr)
                # 多声道 → 单声道（全声道平均）
                if channels > 1 and arr.size % channels == 0:
                    arr = arr.reshape(-1, channels).mean(axis=1)
                # 重采样 rate → 16kHz（线性插值，官方 demo 同法）
                if rate != SAMPLE_RATE_IN and arr.size > 0:
                    n_out = max(1, int(arr.size * SAMPLE_RATE_IN / rate))
                    idx = (np.arange(n_out, dtype=np.float64) * (arr.size - 1) / max(n_out - 1, 1))
                    lo = idx.astype(np.int64)
                    hi = np.minimum(lo + 1, arr.size - 1)
                    frac = (idx - lo).astype(np.float32)
                    arr = arr[lo] * (1 - frac) + arr[hi] * frac
                acc += arr.astype(np.float32).tobytes()
                while len(acc) >= target:
                    try:
                        self._audio_q.put_nowait(bytes(acc[:target]))
                    except Exception:  # noqa: BLE001
                        pass  # 队列满/停止，丢帧
                    del acc[:target]
                return (None, pyaudio.paContinue)

            stream = p.open(
                format=pyaudio.paFloat32, channels=channels, rate=rate,
                input=True, input_device_index=dev["index"],
                frames_per_buffer=rate // 2, stream_callback=_cb,
            )
            while not self._capture_stop.is_set():
                time.sleep(0.2)
        except Exception:  # noqa: BLE001
            logger.warning("云端双工: WASAPI 环回采集失败（推静音音频）", exc_info=True)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            # 🛡️ 不 terminate 共享单例（见 _pa_shared）：只关流，流关闭不影响其它线程。
