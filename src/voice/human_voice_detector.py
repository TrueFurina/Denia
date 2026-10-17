"""环回音频人声检测（2026-08-24 新增）。

解决"音量阈值 VAD 把音乐/游戏音效误判为人声 → 双工永远闭嘴"的问题：
用 sherpa-onnx 的 Silero VAD（神经网络人声检测）区分【人说话】和【音乐/音效/噪声】。

背景（用户实测痛点）：
- 旧方案：环回 RMS 能量 > 0.012 就判"环境响"→ 闭嘴。壁纸引擎/音乐/鼠标驱动
  持续出声（rms≈0.04）→ 双工永远闭嘴零互动。
- 进程名白名单：覆盖不全（新软件/改名软件漏掉）。
- 本方案：Silero VAD 是专门训练的人声检测模型，音乐/游戏音效通常不触发语音段，
  真人说话/视频人声/游戏语音会触发 → 只有"真的有人在说话"才闭嘴。

用法（本地双工 / 云端双工共用）：
    vad = HumanVoiceDetector()
    vad.start()                    # 后台线程：喂音频 + 周期刷新"最近有人声"
    vad.feed(audio_float32)        # 把环回音频流喂进去（16kHz 单声道 float32）
    if vad.is_human_speaking():    # 最近 ~2s 窗口内检测到人声 → 闭嘴
        ...
    vad.stop()

模型：models/silero_vad.onnx（约 640KB，已随项目分发）。
依赖：sherpa_onnx（本地 TTS/ASR 已在用）。模型缺失/加载失败时安全降级：
is_human_speaking() 恒返回 False（不再因音量误闭嘴，宁缺勿滥）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models", "silero_vad.onnx")
SAMPLE_RATE = 16000
# Silero VAD 输入窗口（样本数）：512 = 32ms@16kHz（官方默认）
VAD_WINDOW = 512
# 语音段最少时长 / 静音段合并阈值（秒）
MIN_SPEECH_S = 0.25
MIN_SILENCE_S = 0.30
# 判断"最近有人声"的滑动窗口（秒）：窗口内产出过语音段即视为有人在说话
SPEECH_WINDOW_S = 2.0
# 后台刷新周期（秒）
REFRESH_EVERY_S = 1.0


class HumanVoiceDetector:
    """流式环回人声检测（Silero VAD 封装，线程安全）。

    外部喂入 16kHz 单声道 float32 音频（与 local_duplex 环回采集格式一致），
    后台线程把音频分块送入 Silero VAD；当 VAD 产出语音段时记录时间戳，
    is_human_speaking() 返回"最近 SPEECH_WINDOW_S 秒内是否有人声"。

    两种用法：
    1. feed() 注入：调用方自己有环回采集（本地双工）→ hvd.feed(arr)。
    2. 内置采集：capture_loopback=True（默认）→ start() 时自动开 WASAPI
       环回采集喂自己（云端双工等无现成音频流的场景）。
    """

    def __init__(self, capture_loopback: bool = True, capture_mic: bool = False) -> None:
        self._vad = None
        self._buf: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loopback_thread: Optional[threading.Thread] = None
        self._mic_thread: Optional[threading.Thread] = None
        self._last_speech_ts = 0.0  # 最近一次检测到语音段的单调时钟
        self._enabled = False
        self._capture_loopback = capture_loopback
        self._capture_mic = capture_mic
        self._init_vad()

    def _init_vad(self) -> None:
        try:
            import sherpa_onnx

            if not os.path.exists(MODEL_PATH):
                logger.warning("人声检测: Silero VAD 模型缺失 (%s)，跳过人声检测", MODEL_PATH)
                return
            cfg = sherpa_onnx.VadModelConfig()
            cfg.silero_vad.model = os.path.abspath(MODEL_PATH)
            cfg.silero_vad.min_silence_duration = MIN_SILENCE_S
            cfg.silero_vad.min_speech_duration = MIN_SPEECH_S
            cfg.silero_vad.threshold = 0.4  # 0.5 会漏部分真实语音，0.4 更灵敏
            cfg.sample_rate = SAMPLE_RATE
            self._vad = sherpa_onnx.VoiceActivityDetector(cfg, VAD_WINDOW)
            self._enabled = True
            logger.info("人声检测就绪（Silero VAD, %s）", MODEL_PATH)
        except Exception:  # noqa: BLE001
            logger.warning("人声检测初始化失败，降级为不检测（不再因音量误闭嘴）", exc_info=True)
            self._vad = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        """启动后台处理线程（幂等）；capture_loopback/capture_mic=True 时同时开采集。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="human-voice-detector")
        self._thread.start()
        if self._capture_loopback and self._enabled:
            try:
                from ..llm.local_duplex import capture_loopback_audio
                self._loopback_thread = threading.Thread(
                    target=self._loopback_worker,
                    daemon=True, name="human-vad-loopback",
                )
                self._loopback_thread.start()
                logger.info("人声检测: 内置 WASAPI 环回采集已启动")
            except Exception:  # noqa: BLE001
                logger.warning("人声检测: 内置环回采集启动失败（仅靠 feed() 注入）", exc_info=True)
        if self._capture_mic and self._enabled:
            try:
                self._mic_thread = threading.Thread(
                    target=self._mic_worker,
                    daemon=True, name="human-vad-mic",
                )
                self._mic_thread.start()
                logger.info("人声检测: 麦克风采集已启动（检测用户本人在说话）")
            except Exception:  # noqa: BLE001
                logger.warning("人声检测: 麦克风采集启动失败（用户说话检测不可用）", exc_info=True)

    def _loopback_worker(self) -> None:
        """内置环回采集：把系统声音喂给 Silero VAD（复用 local_duplex 的采集函数）。"""
        from ..llm.local_duplex import capture_loopback_audio
        try:
            capture_loopback_audio(self._stop, self.feed)
        except Exception:  # noqa: BLE001
            logger.debug("人声检测: 环回采集退出", exc_info=True)

    def _mic_worker(self) -> None:
        """内置麦克风采集：把用户本人说话喂给 Silero VAD（sounddevice）。

        这是第二档（2026-08-25）核心：双工"闭嘴"信号从【环回人声】（分不清
        游戏队友/视频人声）改为【麦克风用户本人】——只有用户真的在说话才闭嘴，
        游戏/视频里的人声当背景不再触发静音。
        """
        # 【2026-10-16 修复·漏 import 导致整条链路静默失效】`_loopback_worker` 有
        # 函数内 import，本函数**漏了同款**：`capture_mic_audio` 从未定义 ⇒ 线程体
        # 第一行就 `NameError`，而它被下面的 `except` 以 **debug 级** 吞掉。
        # 症状极具欺骗性：`start()` 的 `logger.info("...麦克风采集已启动")` 照常打印
        # （线程对象确实起来了），实际线程**秒死** ⇒ 「检测用户本人说话」从上线起
        # 从未生效，双工一直用环回（游戏/视频）声音判"该不该闭嘴"，
        # 正是第二档想解决的那个问题原样保留。
        from ..llm.local_duplex import capture_mic_audio
        try:
            capture_mic_audio(self._stop, self.feed)
        except Exception:  # noqa: BLE001
            # capture_mic_audio 内部已自行降级（缺 sounddevice/设备占用它自己 return），
            # 能走到这里就是异常 ⇒ 用 warning，别再让它隐身（原来是 debug）。
            logger.warning("人声检测: 麦克风采集异常退出（用户说话检测将失效）", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._loopback_thread is not None:
            self._loopback_thread.join(timeout=2.0)
            self._loopback_thread = None
        if self._mic_thread is not None:
            self._mic_thread.join(timeout=2.0)
            self._mic_thread = None

    def feed(self, audio: np.ndarray) -> None:
        """喂入 16kHz 单声道 float32 音频块（与环回采集回调同格式）。"""
        if not self._enabled or self._vad is None:
            return
        with self._lock:
            self._buf.append(np.asarray(audio, dtype=np.float32))

    def is_human_speaking(self) -> bool:
        """最近 SPEECH_WINDOW_S 秒内是否检测到人声（线程安全）。"""
        if not self._enabled:
            return False
        with self._lock:
            last = self._last_speech_ts
        return last > 0 and (time.monotonic() - last) <= SPEECH_WINDOW_S

    # ===== 后台线程 =====
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                # 取出积压音频
                with self._lock:
                    pending = self._buf
                    self._buf = []
                if pending:
                    for chunk in pending:
                        self._feed_chunk(chunk)
                # 周期检查是否有新语音段 → 刷新"最近有人声"时间戳。
                # Silero 段在 flush 时才产出：每轮 flush + drain + reset。
                self._poll_speech()
            except Exception:  # noqa: BLE001
                logger.debug("人声检测线程异常", exc_info=True)
            time.sleep(REFRESH_EVERY_S)

    def _feed_chunk(self, audio: np.ndarray) -> None:
        """把音频块切 512 样本窗口送入 Silero VAD（支持任意长度）。"""
        try:
            vad = self._vad
            if vad is None:
                return
            flat = np.ascontiguousarray(audio, dtype=np.float32).ravel()
            n = len(flat)
            i = 0
            while i + VAD_WINDOW <= n:
                vad.accept_waveform(flat[i:i + VAD_WINDOW].tolist())
                i += VAD_WINDOW
            if i < n:  # 尾部不足一窗 → 补零
                tail = np.zeros(VAD_WINDOW, dtype=np.float32)
                tail[:n - i] = flat[i:]
                vad.accept_waveform(tail.tolist())
        except Exception:  # noqa: BLE001
            logger.debug("喂 VAD 失败", exc_info=True)

    def _drain_segments(self) -> None:
        """把 VAD 已产出的语音段全部取出（记录"有人声"时间戳）。

        注意：sherpa-onnx 的 SpeechSegment 只能通过 pop() 消费（front() 返回
        的对象在部分版本不可直接访问属性），这里只统计段数。
        """
        try:
            vad = self._vad
            if vad is None:
                return
            n = 0
            while not vad.empty():
                vad.pop()
                n += 1
            if n:
                with self._lock:
                    self._last_speech_ts = time.monotonic()
        except Exception:  # noqa: BLE001
            pass

    def _poll_speech(self) -> None:
        """周期性 flush + 收集段（Silero 需要 flush 才能吐出尾部的段）。"""
        try:
            vad = self._vad
            if vad is None:
                return
            vad.flush()
            if not vad.empty():
                self._drain_segments()
                logger.info("人声检测: 检测到人声段（闭嘴）")
            vad.reset()
        except Exception:  # noqa: BLE001
            pass
