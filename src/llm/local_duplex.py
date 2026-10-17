"""本地真全双工客户端（对标云端 MiniCPM-o 网关，但全本地、无 300s 强踢）。

设计取舍（关键）：
  云端 MiniCPM-o 是"原生音频双工"——模型直接听音频流、直接吐 audio。但本地
  ollama 跑的 omni 模型（minicpm-o2.6:q4_K_M）通过 ollama API **不支持把音频流
  实时喂进去做双工**（只能吃图片+文本）。因此本地真全双工不能直接照搬云端网关
  那套"每秒推音频流"的协议，改用 **Jarvis 式混合架构**：

    1. 视觉驱动：常驻线程每秒抓屏（最近一帧作为模型"看到的画面"）。
    2. 听声音只做"开口时机判断"（不喂给模型）：WASAPI 环回采集系统音 → VAD
       算能量 → 判断"环境/用户是否在发声"。环境在响或 AI 自己正在 TTS 播放时，
       抑制本轮开口（对应云端 force_listen 闭嘴），避免抢话/自言自语。
    3. 模型自主裁决：主循环每 decision_interval_s（默认 2s，本地快）把"最近截图
       + 上下文 + 当前是否在播放自己声音 + 环回音活动"打包给 ollama omni，让它
       输出 JSON 裁决 {should_speak, text}。
    4. TTS 出声：模型说该说 → 回调 on_speak(text, b"", 0)，proactive 走本地 TTS
       （达妮娅嗓音）合成播放，同时标记"正在出声"避免自己抢话。
    5. 无限会话、不断线、不截断——这是本地相对云端的最大优势（无 300s 强踢、
       无重连、无 token/时长上限导致的半句 done）。

与云端客户端的接口对称（on_speak/on_status/on_error/screenshot_fn/set_instructions/
set_force_listen/set_privacy/set_call_mute/running），proactive 侧可无感切换。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable, Optional

import numpy as np

from ..perception._pa_shared import get_shared_pyaudio, safe_find_loopback_device  # 共享 PortAudio 单例
from ..perception.process_loopback import start_self_excluded_capture, self_speak_active, _safe_find_wasapi_loopback

logger = logging.getLogger(__name__)


def parse_duplex_decision(raw: str) -> Optional[dict]:
    """解析模型返回的 JSON 裁决 {"should_speak": bool, "text": str}。

    模型可能输出 markdown 代码块/多余文字，用正则提取第一个 {...} 再解析。
    模块级实现，避免从 proactive 反向导入造成循环依赖。

    【2026-08-26 容错增强】本地 omni 模型常把布尔写成中文/字符串（"是/否/要/不"）
    或漏给 should_speak 只给了 text。这里做容错，让更多合法输出被识别为"开口"，
    直接提升桌面互动率（旧版严格解析→大量输出被判 should_speak=false→零互动）。
    """
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        # 没有 JSON 但有实打实的文字（模型直接开口了）：视为要说话，文本照用
        _txt = raw.strip().strip("`'\"")
        if len(_txt) >= 2 and not _txt.startswith("【观察】"):
            return {"should_speak": True, "text": _txt}
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    ss = d.get("should_speak")
    if isinstance(ss, str):
        ss = ss.strip()
        if ss in ("true", "是", "要", "yes", "1", "对", "开口", "嗯", "好"):
            ss = True
        elif ss in ("false", "否", "不", "no", "0", "错", "闭嘴", "安静", "无"):
            ss = False
        else:
            ss = None  # 无法判定 → 交给下方"有 text 即开口"兜底
    text = str(d.get("text") or "").strip()
    # 没给 should_speak 但给了文字 → 视为要开口（容错提升互动率）
    if ss is None:
        ss = bool(text)
    return {"should_speak": bool(ss), "text": text}


# 本地决策轮询间隔（秒）：本地 omni 推理比云端流式慢，2s 一轮足够"准实时"，
# 比云端每秒推流更省显存/CPU，且对话体感仍连续。
DECISION_INTERVAL_S = 2.0
# 开口最小间隔（秒）：本地整句生成，之前 6s 太短叠加剧情同质→"破唱片"感。
# 提到 12s，让桌面搭话更克制、更有" humans 在旁边偶尔开口"的体感。
LOCAL_MIN_INTERVAL_S = 12.0
# 启动后前 N 轮静默（对齐云端 force_listen 首帧思路，2026-08-27）：本地虽是无状态
# 单帧裁决、没有云端流式"积累上下文"过程，但开局第一帧常是不完整/突变画面，小模型
# 容易对陌生首帧乱发言。这里让本地也压住前 N 轮开口（只观察不说话），过滤首帧突变，
# 之后再正常裁决。小成本、防开局乱语；每轮约 decision_interval_s，2 轮≈4s。
FIRST_LOCAL_LISTEN_ROUNDS = 2
# 持久滚动对话窗口长度（2026-08-27 超越云端核心）：本地把最近 N 轮(用户说过/AI说过)
# 作为多轮 messages 持续喂模型，且永不清零（云端每 245s 重连清零，永远拼不出长对话）。
# 仅"当前帧"带图，历史轮为纯文本，避免重复发图爆 token。8 轮≈4 个来回，足够连贯
# 又不撑爆小模型上下文。
LOCAL_HISTORY_TURNS = 8
# 本地模型常驻显存时长：双工期间让 ollama 保持 minicpm 加载在 VRAM，避免每次开口
# 都从磁盘重载（实测 5.4GB 重载 ~2.6s，造成"低显存 + 开口卡顿"假象）。本地模型常驻
# 符合架构设计（游戏时只占显存不占算力，16GB 可行）；开游戏触发锁模式时 unload 逻辑
# 会用 keep_alive=0 强制卸载，互不冲突。30m=空闲 30 分钟才允许 ollama 自行卸载。
LOCAL_MODEL_KEEP_ALIVE = "30m"
# 截图帧率：每 N 秒抓一帧送给模型（本地不需要每秒一帧，2s 一帧够感知变化）。
FRAME_EVERY_S = 2.0
# VAD 能量阈值（环回音 RMS，float32 归一化）：超过说明环境/用户在发声 → 闭嘴。
VAD_ENERGY_THRESHOLD = 0.012
# 环回音采集块时长（秒）用于能量统计。
VAD_WINDOW_S = 1.0
# 背景出声源白名单（2026-08-24 修复）：这些软件**持续出声**（壁纸引擎/音乐播放器/
# 鼠标驱动/驱动助手等）但**不是人在说话**。环回 VAD 只测能量、分不清"背景音乐"和
# "真人语音"，导致这些软件一响（rms≈0.04 远超阈值 0.012）双工就永远闭嘴不互动
# （实锤：用户开着 wallpaper64/mchose hub/sodamusic 时本地双工 0 开口）。
# 修复：VAD 判"环境响"前先枚举出声进程，若出声的**全是**白名单背景软件 → 不算
# 人在说话 → 允许开口。真人语音（微信/QQ/会议/游戏语音等非白名单）仍正常闭嘴。
BACKGROUND_SPEAKER_PROC_NAMES: frozenset = frozenset({
    # 壁纸引擎 / 桌面美化（持续音效/视频声音）
    "wallpaper64.exe", "wallpaper32.exe", "wallpaper engine.exe", "desktoppet.exe",
    "lively.exe", "plastuer.exe", "rainmeter.exe",
    # 音乐播放器 / 视频（用户主动放歌/视频——模型开口会被"听歌"场景接受）
    "sodamusic.exe", "cloudmusic.exe", "qqmusic.exe", "kuwo.exe", "kugou.exe",
    "spotify.exe", "foobar2000.exe", "potplayer.exe", "vlc.exe", "mpv.exe",
    "bilibili.exe", "bilibilipc.exe", "dandanplay.exe",
    # 鼠标驱动 / 键盘驱动 / 外设助手（提示音/宏音效）
    "mchose hub.exe", "mchosehub.exe", "ghub.exe", "lghub.exe", "icue.exe",
    "armourycrate.exe", "razer synapse 3.exe", "synapse3.exe", "devicedriver.exe",
    "das device.exe", "keychron.exe", "vgn hub.exe", "rog armoury.exe",
    # 系统/工具类常驻发声
    "widgets.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "calculator.exe",
    # 截图/录屏/通知（短暂提示音）
    "snipaste.exe", "pixpin.exe", "sharex.exe", "qttabbar.exe",
})
# 自身窗口进程名（2026-08-26 修复"对自家 UI 搭话"）：前台是伴侣自身软件时，
# 决策循环直接静默跳过，不送模型（否则模型会把自己界面 hallucinate 成"写代码"等）。
# 之前只靠指令软约束("忽略你自己的软件")，小模型不守→出现"写代码到凌晨啦"这类输出。
SELF_PROC_NAMES: frozenset = frozenset({
    "workbuddy.exe", "ai桌面伴侣.exe", "codebuddy.exe",
})


# ==========================================================
# WASAPI 环回采集（与云端客户端共享，抽成模块级函数）
# ==========================================================
def capture_loopback_audio(
    stop_event: threading.Event,
    on_chunk: Callable[[np.ndarray], None],
    target_rate: int = 16000,
) -> None:
    """采集系统声音（WASAPI 环回）→ 重采样到 target_rate 单声道 float32 →
    on_chunk(ndarray)。独立线程运行，stop_event 置位即退出。失败静默返回。

    不把音频喂给模型，只供 VAD 判断"环境是否在响"。

    优先进程级环回排除自身软件声音（VAD 不被自身 TTS 触发"环境在响"）；
    失败回退整设备环回。
    """
    # 优先进程级环回排除自身软件声音
    if start_self_excluded_capture(stop_event, on_chunk, target_rate):
        return
    try:
        import pyaudiowpatch as pyaudio
    except Exception:  # noqa: BLE001
        logger.warning("本地双工: 未安装 pyaudiowpatch 且进程环回不可用，跳过环回采集（仍能看屏说话）")
        return
    # 🛡️ 共享单例：会话期间绝不 terminate，避免与别的线程并发 init/terminate
    # 触发原生访问违规（0xC0000005）。安全枚举环回，绝不调用会堆损坏的
    # get_default_wasapi_loopback()。
    p = get_shared_pyaudio()
    stream = None
    try:
        dev = safe_find_loopback_device()
        if dev is None:
            logger.warning("本地双工: 找不到可用的 WASAPI 环回设备（跳过环回采集，仍可看屏说话）")
            return
        rate = int(dev["defaultSampleRate"])
        channels = max(1, int(dev["maxInputChannels"]))
        logger.info("本地双工: WASAPI 环回采集 %s @%dHz %dch", dev["name"], rate, channels)

        def _cb(in_data, frame_count, time_info, status_flags):
            if in_data is None or len(in_data) == 0:
                return (None, pyaudio.paContinue)
            arr = np.frombuffer(in_data, dtype=np.float32)
            # 【自言自语闸门】自身软件（达妮娅 TTS）出声时把环回清零，VAD 不被自身 TTS 触发
            if self_speak_active():
                arr = np.zeros_like(arr)
            if channels > 1 and arr.size % channels == 0:
                arr = arr.reshape(-1, channels).mean(axis=1)
            if rate != target_rate and arr.size > 0:
                n_out = max(1, int(arr.size * target_rate / rate))
                idx = (np.arange(n_out, dtype=np.float64)
                       * (arr.size - 1) / max(n_out - 1, 1))
                lo = idx.astype(np.int64)
                hi = np.minimum(lo + 1, arr.size - 1)
                frac = (idx - lo).astype(np.float32)
                arr = arr[lo] * (1 - frac) + arr[hi] * frac
            try:
                on_chunk(arr.astype(np.float32))
            except Exception:  # noqa: BLE001
                pass
            return (None, pyaudio.paContinue)

        stream = p.open(
            format=pyaudio.paFloat32, channels=channels, rate=rate,
            input=True, input_device_index=dev["index"],
            frames_per_buffer=rate // 2, stream_callback=_cb,
        )
        while not stop_event.is_set():
            time.sleep(0.1)
    except Exception:  # noqa: BLE001
        logger.warning("本地双工: WASAPI 环回采集失败（仍可看屏说话）", exc_info=True)
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        # 🛡️ 不 terminate 共享单例（见 _pa_shared）：只关流。


def capture_mic_audio(
    stop_event: threading.Event,
    on_chunk: Callable[[np.ndarray], None],
    target_rate: int = 16000,
) -> None:
    """采集麦克风（用户本人说话）→ 16kHz 单声道 float32 → on_chunk(ndarray)。

    第二档（2026-08-25）：双工"闭嘴"信号改用麦克风——只有用户本人在说话才
    闭嘴；游戏/视频/队友语音走环回，不再触发静音。独立线程运行，stop_event
    置位即退出。与 ASR 的 sounddevice 麦克风流可能共存（PortAudio 默认共享
    模式），冲突/失败时静默降级（返回，不影响其他功能）。
    """
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        logger.warning("本地双工: 未安装 sounddevice，跳过麦克风采集（用户说话检测不可用）")
        return

    def _cb(indata, frames, time_info, status) -> None:  # noqa: A002
        if status:
            logger.debug("麦克风输入状态: %s", status)
        try:
            arr = np.asarray(indata, dtype=np.float32)
            if arr.ndim > 1:
                arr = arr.mean(axis=1)
            on_chunk(arr.astype(np.float32))
        except Exception:  # noqa: BLE001
            pass

    try:
        with sd.InputStream(
            samplerate=target_rate, channels=1, dtype="float32",
            blocksize=target_rate // 20,  # 50ms 块
            callback=_cb,
        ):
            while not stop_event.is_set():
                time.sleep(0.1)
    except Exception as e:  # noqa: BLE001
        logger.warning("本地双工: 麦克风采集失败（%s），用户说话检测不可用（仅靠通话窗口判定）", e)


class LocalMiniCPMDuplexClient:
    """本地真全双工客户端（全本地：看屏 + VAD 闭嘴 + 本地 TTS 出声）。

    回调（全部 async，在事件循环内执行）：
      on_speak(text, audio_pcm, sample_rate)  模型开口（本地 audio_pcm 恒为空，走 TTS）
      on_status(msg)                          状态日志
      on_error(msg)                           可恢复错误

    外部依赖（通过构造函数注入，保持与 proactive 解耦）：
      llm             OllamaClient 实例（chat(images=...) 多模态调用）
      context_fn()    -> str  取当前双工上下文（_duplex_context）
      is_speaking_fn()-> bool 本地 TTS / AI 语音是否正在播放（避免自己抢话）
    """

    name = "local-minicpm-duplex"

    def __init__(
        self,
        llm: Any = None,
        on_speak: Optional[Callable[[str, bytes, int], Awaitable[None]]] = None,
        on_status: Optional[Callable[[str], Awaitable[None]]] = None,
        on_error: Optional[Callable[[str], Awaitable[None]]] = None,
        screenshot_fn: Optional[Callable[[], Optional[bytes]]] = None,
        context_fn: Optional[Callable[[], str]] = None,
        is_speaking_fn: Optional[Callable[[], bool]] = None,
        foreground_fn: Optional[Callable[[], str]] = None,
        capture_audio: bool = True,
        decision_interval_s: float = DECISION_INTERVAL_S,
        min_interval_s: float = LOCAL_MIN_INTERVAL_S,
        on_listen: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._llm = llm
        self._on_speak = on_speak
        self._on_status = on_status
        self._on_error = on_error
        self._on_listen = on_listen or (lambda: None)
        self._screenshot_fn = screenshot_fn
        self._context_fn = context_fn
        self._is_speaking_fn = is_speaking_fn
        self._foreground_fn = foreground_fn
        self._capture_audio = capture_audio
        self._decision_interval_s = decision_interval_s
        self._min_interval_s = min_interval_s

        self._running = False
        self._instructions: str = ""
        self._force_listen = False       # 前台不满足/隐私时强制闭嘴
        self._privacy = False            # 隐私模式：不抓真实画面、强制闭嘴
        self._call_mute = False          # 通话静音：抑制开口（仍看屏，但不出声）

        # 最近截图（线程安全）：抓屏线程更新，决策线程读取
        self._latest_frame: Optional[bytes] = None
        self._frame_lock = threading.Lock()
        self._last_frame_ts = 0.0

        # VAD：环回音能量（最近 VAD_WINDOW_S 秒均值），抓屏/VAD 线程更新
        self._audio_rms = 0.0
        self._audio_buf: list[np.ndarray] = []
        self._audio_buf_lock = threading.Lock()
        self._capture_stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        # 当前出声进程缓存（audio_worker 后台线程周期刷新，供 VAD 区分背景声/人声）
        self._active_speakers: list[dict] = []
        # Silero 人声检测器（audio_worker 内懒创建；模型缺失时内部降级）
        self._human_vad: Optional[Any] = None

        # 决策线程状态
        self._last_speak_ts = 0.0
        self._capture_started_at = 0.0

        # 持久滚动对话窗口（2026-08-27 超越云端核心）：存最近若干轮
        # {role:user/assistant, content} 纯文本。用户语音经 push_user_text 压入，
        # AI 开口时压入 assistant 轮；决策循环每轮把它作为多轮历史发给模型。
        # 永不清零（不像云端 245s 重连丢上下文），是本地结构性超越云端的关键。
        self._conv_history: deque = deque(maxlen=LOCAL_HISTORY_TURNS)

    # ===== 外部控制（与云端客户端接口对称） =====
    def set_instructions(self, text: str) -> None:
        self._instructions = text

    def set_force_listen(self, flag: bool) -> None:
        self._force_listen = bool(flag)

    def set_privacy(self, flag: bool) -> None:
        self._privacy = bool(flag)
        if self._privacy:
            self._force_listen = True

    def set_call_mute(self, flag: bool) -> None:
        self._call_mute = bool(flag)

    def push_user_text(self, text: str) -> None:
        """（2026-08-27 超越云端·听声）把用户刚说的话（本地 ASR 转写）压入滚动对话窗口。

        让本地双工"听得到"用户说啥——对齐云端音频理解能力；且靠本地"不断线"特性，
        对话窗口持久不重置（云端每 245s 断链清零，永远拼不出长对话），连贯性反超云端。
        仅压纯文本轮，不带入图片，避免重复发图爆 token。
        """
        text = (text or "").strip()
        if not text or len(text) > 200:
            return
        self._conv_history.append({"role": "user", "content": text})

    @property
    def running(self) -> bool:
        return self._running

    async def _emit_status(self, msg: str) -> None:
        logger.info("本地双工: %s", msg)
        if self._on_status:
            try:
                await self._on_status(msg)
            except Exception:  # noqa: BLE001
                logger.exception("on_status 回调失败")

    async def _emit_error(self, msg: str) -> None:
        logger.warning("本地双工: %s", msg)
        if self._on_error:
            try:
                await self._on_error(msg)
            except Exception:  # noqa: BLE001
                logger.exception("on_error 回调失败")

    async def _emit_speak(self, text: str) -> None:
        if not text or not self._on_speak:
            return
        # 本地无原生语音：audio_pcm 传空，proactive 的 _on_duplex_speak 会自动走
        # 本地 TTS 合成播放（音色=达妮娅 qwen3tts）。
        try:
            await self._on_speak(text, b"", 0)
        except Exception:  # noqa: BLE001
            logger.exception("on_speak 回调失败")

    # ===== 后台采集线程（截图 + VAD） =====
    def _start_collector(self) -> None:
        self._capture_stop = threading.Event()
        t_cap = threading.Thread(
            target=self._collector_worker, name="local-duplex-collector",
            daemon=True)
        t_cap.start()
        self._capture_thread = t_cap
        if self._capture_audio:
            t_aud = threading.Thread(
                target=self._audio_worker, name="local-duplex-audio",
                daemon=True)
            t_aud.start()

    def _stop_collector(self) -> None:
        self._capture_stop.set()
        try:
            hvd = getattr(self, "_human_vad", None)
            if hvd is not None:
                hvd.stop()
        except Exception:  # noqa: BLE001
            pass

    def _collector_worker(self) -> None:
        """每秒抓一帧截图（供决策线程读最近画面）。"""
        while not self._capture_stop.is_set():
            try:
                if self._screenshot_fn is not None and not self._privacy:
                    img = self._screenshot_fn()
                    if img:
                        with self._frame_lock:
                            self._latest_frame = img
                            self._last_frame_ts = time.time()
            except Exception:  # noqa: BLE001
                logger.exception("本地双工: 截图失败")
            time.sleep(FRAME_EVERY_S)

    def _audio_worker(self) -> None:
        """环回音采集 → RMS 能量统计 + Silero 人声检测（并行）。

        2026-08-24：音频采集（阻塞）与"人声检测 + 出声进程枚举"各跑一个
        线程。音频块同时喂给 RMS 统计（旧兜底）和 HumanVoiceDetector
        （Silero VAD 神经网络人声检测——区分人说话 vs 音乐/游戏音效）。
        """
        # 初始化人声检测器（模型缺失时内部降级，不影响采集）。
        # 【2026-08-25 第二档】本地双工也改【麦克风】人声检测：用户本人说话才
        # 闭嘴（capture_mic=True 内部采集麦克风）；环回音频只喂 RMS 统计兜底，
        # 不再喂 HVD——否则游戏队友语音/视频人声会被误判"人声"→ 闭嘴不互动
        # （老问题实锤："跑路很溜"接队友茬、看视频不互动）。
        try:
            from ..voice.human_voice_detector import HumanVoiceDetector
            self._human_vad = HumanVoiceDetector(
                capture_loopback=False, capture_mic=True)
            self._human_vad.start()
        except Exception:  # noqa: BLE001
            logger.warning("本地双工: 人声检测器初始化失败，回退音量+进程名判断", exc_info=True)
            self._human_vad = None

        def _on_chunk(arr: np.ndarray) -> None:
            with self._audio_buf_lock:
                self._audio_buf.append(arr)
                # 限制缓冲长度约 VAD_WINDOW_S 秒（按 16kHz 估算）
                max_blocks = max(1, int(VAD_WINDOW_S * 16000 / max(arr.size, 1)))
                while len(self._audio_buf) > max_blocks:
                    self._audio_buf.pop(0)
                if self._audio_buf:
                    cat = np.concatenate(self._audio_buf)
                    self._audio_rms = float(np.sqrt(np.mean(np.square(cat))))
            # 注：不再喂 hvd.feed(环回)——2026-08-25 起本地双工人声检测改用
            # 麦克风（capture_mic=True 内部采集），喂环回会把游戏/视频人声混入
            # 导致误闭嘴。RMS 统计保留（HVD 不可用时的旧兜底）。
        # 音频采集线程（阻塞直到 stop）
        _loop_t = threading.Thread(
            target=capture_loopback_audio,
            args=(self._capture_stop, _on_chunk),
            daemon=True, name="local-duplex-loopback",
        )
        _loop_t.start()
        # 主线程：周期枚举出声进程（COM 枚举有开销，不能放决策线程）
        while not self._capture_stop.is_set():
            self._refresh_active_speakers()
            time.sleep(2.0)

    def _refresh_active_speakers(self) -> None:
        """后台线程：刷新当前出声进程缓存（COM 枚举有开销，不能放决策线程）。"""
        try:
            from ..perception.audio_sessions import active_audio_processes
            self._active_speakers = active_audio_processes()
        except Exception:  # noqa: BLE001
            self._active_speakers = []

    def _is_env_loud(self) -> bool:
        """环回音人声检测：环境里**是否有人在说话**（用于闭嘴判断）。

        2026-08-24 二次修复（替代音量阈值+进程名白名单的初版方案）：
        初版"RMS 能量 + 背景进程白名单"仍不完美——壁纸引擎/音乐/游戏音效
        会被能量阈值误判，白名单又覆盖不全。本版用 **Silero VAD 神经网络
        人声检测**（src/voice/human_voice_detector.py）：只认"人说话"的声学
        特征，音乐/游戏音效/鼠标驱动声不会触发语音段 → 不再误闭嘴。
        进程名白名单降级为兜底（人声检测不可用时才用）。

        逻辑：
        1. 若 Silero 人声检测可用 → 以它为准：最近窗口内检测到人声 → 闭嘴；
           否则（音乐/游戏音效/静音）→ 放行开口。
        2. 人声检测不可用（模型缺失/初始化失败）→ 回退初版逻辑：
           RMS 超阈值 + 出声进程含非背景软件 → 闭嘴。
        """
        if not self._capture_audio:
            return False
        hvd = getattr(self, "_human_vad", None)
        if hvd is not None and hvd.enabled:
            if hvd.is_human_speaking():
                logger.info("本地双工闭嘴: 环回检测到人声(Silero VAD)")
                return True
            # 有人声检测但当前无人声 → 无论音量多大（音乐/游戏）都放行
            return False
        # ===== 兜底：人声检测不可用 → 音量+进程名启发式 =====
        with self._audio_buf_lock:
            rms = self._audio_rms
        if rms <= VAD_ENERGY_THRESHOLD:
            return False
        speakers = list(getattr(self, "_active_speakers", None) or [])
        if not speakers:
            return True
        non_bg = [
            s for s in speakers
            if (s.get("name") or "").strip().lower() not in BACKGROUND_SPEAKER_PROC_NAMES
        ]
        if non_bg:
            return True
        logger.info(
            "本地双工: 环回有音(rms=%.4f)但仅背景软件出声(%s)，放行开口",
            rms, ",".join((s.get("name") or "?") for s in speakers[:5]) or "?")
        return False

    def _should_stay_quiet(self) -> bool:
        """是否应闭嘴（对应云端 force_listen）：
        - 前台/隐私强制闭嘴；
        - AI 自己正在 TTS 播放（避免抢话）；
        - 环境/用户正在发声（不打断）。
        """
        if self._force_listen:
            logger.info("本地双工闭嘴: force_listen(前台不满足/隐私)")
            return True
        if self._call_mute:
            logger.info("本地双工闭嘴: call_mute(通话静音)")
            return True
        if self._is_speaking_fn is not None:
            try:
                if self._is_speaking_fn():
                    logger.info("本地双工闭嘴: 自身TTS播放中")
                    return True
            except Exception:  # noqa: BLE001
                pass
        if self._is_env_loud():
            logger.info("本地双工闭嘴: 环境响(环回VAD rms=%.4f)", self._audio_rms)
            return True
        return False

    # ===== 决策循环（核心） =====
    async def _decision_loop(self) -> None:
        """每 decision_interval_s 跑一轮：看屏+上下文 → 模型裁决 → TTS 出声。"""
        self._capture_started_at = time.time()
        last_decision = 0.0
        rounds = 0
        while self._running:
            now = time.time()
            # 节奏控制
            if now - last_decision < self._decision_interval_s:
                await asyncio.sleep(0.2)
                continue
            last_decision = now
            rounds += 1

            # 闭嘴判断
            if self._should_stay_quiet():
                # 闭嘴原因已由 _should_stay_quiet 内部 INFO 记录；这里每 30 轮补一次
                # "决策循环存活"心跳，便于排查"零互动"是卡在安静还是根本没在跑
                if rounds % 30 == 0:
                    logger.info(
                        "本地双工决策循环存活: 第%d轮, 最近截图ts=%.1f, 闭嘴=是"
                        "(环境响/自播/通话/强制)", rounds, self._last_frame_ts)
                continue

            # 自身窗口守卫（2026-08-26）：前台是伴侣自身软件时静默跳过，
            # 不送模型，避免"对自己界面搭话"(hallucinate 成"写代码到凌晨")。
            if self._foreground_fn is not None:
                try:
                    _fg = (self._foreground_fn() or "").strip().lower()
                    if _fg and _fg in SELF_PROC_NAMES:
                        if rounds % 15 == 0:
                            logger.info(
                                "本地双工决策循环存活: 第%d轮, 前台是自身窗口(%s)，静默跳过",
                                rounds, _fg)
                        continue
                except Exception:  # noqa: BLE001
                    pass

            # 取最近截图
            with self._frame_lock:
                img = self._latest_frame
            if img is None:
                if rounds % 15 == 0:
                    logger.info(
                        "本地双工决策循环存活: 第%d轮, 无截图(截图失败/隐私), 跳过",
                        rounds)
                continue

            # 取上下文（含对话记忆、时间护栏等）
            context = ""
            if self._context_fn is not None:
                try:
                    context = self._context_fn() or ""
                except Exception:  # noqa: BLE001
                    context = ""
            # 把"当前是否在播放自己声音/环境响"这类信号也塞进上下文，帮模型判断时机
            env_note = "（当前环境安静，可以开口）" if not self._is_env_loud() \
                else "（当前环境有声音/用户在说话，除非必要先别开口）"
            context = (context + "\n" + env_note).strip()

            # 构造 messages（复用 build_duplex_messages，自带时间块+人设）
            try:
                from ..llm.prompts import build_duplex_messages
                # persona 需要由 context_fn 提供？这里改由 instructions 承载人设，
                # 直接用 llm.chat 走纯文本+图更简单：用 instructions 作为 system。
                # 【持久滚动对话窗口（2026-08-27 超越云端核心）】云端的"连续上下文"
                # 每 245s 重连清零，永远拼不出长对话；本地把最近若干轮(用户说过/
                # AI说过，存于 _conv_history)作为多轮 messages 持续喂模型，且永不清零
                # ——这是本地结构性超越云端的关键。仅"当前帧"带图，历史轮为纯文本，
                # 避免重复发图爆 token（ollama minicpm-o 实测只稳吃 1 图/轮）。
                messages = [
                    {"role": "system", "content": self._instructions or "你是桌宠助手"},
                ]
                messages.extend(self._conv_history)  # 历史轮（纯文本，user/assistant）
                messages.append(
                    {"role": "user", "content": context, "images": [img]})
                raw = await asyncio.wait_for(
                    self._llm.chat(
                        messages, options={"temperature": 0.7},
                    ),
                    timeout=30.0,
                )
            except Exception:  # noqa: BLE001
                logger.exception("本地双工: 模型裁决失败（下一轮重试）")
                await asyncio.sleep(1.0)
                continue

            decision = parse_duplex_decision(raw or "")
            if not decision or not decision.get("should_speak"):
                # 每 10 轮 INFO 采样一次，暴露"模型到底判了不该说"——这是之前桌面
                # 零互动的主因(旧指令过严 → should_speak=false)，现在可见可调
                if rounds % 10 == 0:
                    logger.info(
                        "本地双工决策: 第%d轮 模型裁决 should_speak=false (raw=%s)",
                        rounds, (raw or "")[:120])
                continue
            text = (decision.get("text") or "").strip()
            if len(text) < 2:
                continue
            # 【首轮静默（对齐云端 force_listen，2026-08-27）】启动后前 N 轮即便模型
            # 判该说也压住不开口，先把"首帧突变"过滤掉，之后再正常裁决。防开局乱语。
            if rounds <= FIRST_LOCAL_LISTEN_ROUNDS:
                logger.info(
                    "本地双工首轮静默(force_listen对齐): 第%d轮 压制开口", rounds)
                continue
            # 防退化（2026-08-27 移植对话记忆桥后新增）：4bit 小模型偶尔会把注入的
            # 【对话记忆·接续】背景文字当成自己的输出吐出来。拦截疑似"记忆回声"
            # （以记忆块特征前缀开头），判为不开口，避免 AI 把背景当台词念出来。
            _echo_markers = ("【对话记忆", "用户最近在", "你最近说过", "屏幕之前出现过")
            if text.startswith(_echo_markers):
                if rounds % 10 == 0:
                    logger.info(
                        "本地双工决策: 第%d轮 拦截记忆回声(text=%s...)，判不开口",
                        rounds, text[:40])
                continue
            # 最小开口间隔（防刷屏）
            if now - self._last_speak_ts < self._min_interval_s:
                continue
            self._last_speak_ts = now
            # 把这次开口压入滚动对话窗口（仅文本轮），供后续轮次作为多轮历史，
            # 让本地双工"记得自己刚说过啥"——持久连贯、反超云端 245s 断链。
            self._conv_history.append({"role": "assistant", "content": text})
            await self._emit_speak(text)

    # ===== 主入口 =====
    async def start(self) -> None:
        """常驻循环：启动采集线程 → 决策循环 → stop() 终止。本地无重连/无强踢。"""
        if self._running:
            return
        if self._llm is None or not getattr(self._llm, "available", False):
            await self._emit_error("本地双工: Ollama 模型不可用（请 ollama pull minicpm-o2.6）")
            return
        self._running = True
        self._start_collector()
        await self._emit_status("本地全双工启动（看屏+VAD闭嘴+本地TTS），模型="
                                f"{getattr(self._llm, 'model_name', '?')}")
        try:
            await self._decision_loop()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await self._emit_error(f"本地双工异常: {e}")
        finally:
            self._running = False
            self._stop_collector()
            await self._emit_status("本地全双工已停止")

    async def stop(self) -> None:
        self._running = False
        self._stop_collector()
