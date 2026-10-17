"""语音识别：独立线程跑 Vosk 识别循环。

两阶段：唤醒阶段（语法约束）→ 命中后切指令识别（完整模型）→ 超时回唤醒阶段。
通过 bus.publish_threadsafe 把 WAKE_WORD / USER_TEXT 事件投回主 asyncio 循环。
speaking 期间 pause() 暂停采集，避免自循环拾取 TTS 声音。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from .audio_io import AudioIO
from .wake_word import WakeWordDetector

logger = logging.getLogger(__name__)


class ASR:
    def __init__(self, bus: EventBus, cfg: Config) -> None:
        self._bus = bus
        self._cfg = cfg
        voice = cfg.ns.voice
        self._model_path: str = voice.asr.model_path
        self._sample_rate: int = voice.asr.sample_rate
        self._cmd_timeout: float = voice.asr.cmd_timeout_s
        self._wake_words: list[str] = list(voice.wake_words)
        self._running = True
        self._pause_event = threading.Event()
        # 持续识别（免唤醒）模式：开启后不用喊唤醒词，说话即识别、
        # 随时对话（方便用户随时纠错 AI 说错的话）。
        # threading.Event 保证跨线程热切换安全。
        self._continuous_event = threading.Event()
        if getattr(voice, "continuous_voice", False):
            self._continuous_event.set()
        # 麦克风监听总开关：关闭后释放麦克风设备（完全停止采集），唤醒词/持续识别
        # 全部停；开启恢复（continuous 关=唤醒词模式）。与持续对话开关相互独立。
        self._listen_event = threading.Event()
        if getattr(voice, "mic_enabled", True):
            self._listen_event.set()
        self._thread: Optional[threading.Thread] = None

    def set_continuous(self, enabled: bool) -> None:
        """热切换免唤醒持续识别模式（线程安全，立即生效）。"""
        if enabled:
            self._continuous_event.set()
        else:
            self._continuous_event.clear()
        logger.info("语音持续识别(免唤醒): %s", "开启" if enabled else "关闭")

    def is_continuous(self) -> bool:
        return self._continuous_event.is_set()

    def set_listening(self, enabled: bool) -> None:
        """麦克风监听总开关（线程安全，立即生效）。

        关闭：释放麦克风设备（完全停止采集），唤醒词/持续识别全部停——
        用户不开连续对话也不想被监听时用这个；
        开启：恢复采集（continuous 关=唤醒词模式，开=免唤醒持续识别）。
        与 set_continuous 相互独立，监听关了持续识别开着也不采集。
        """
        if enabled:
            self._listen_event.set()
        else:
            self._listen_event.clear()
        logger.info("麦克风监听: %s", "开启" if enabled else "关闭")

    def is_listening(self) -> bool:
        return self._listen_event.is_set()

    def _resolve_mic_device(self):
        """从配置读麦克风设备选择：'default'/空 → 系统默认(None)；否则整数索引。"""
        try:
            raw = self._cfg.raw.get("proactive", {}) or {}
            dev = raw.get("duplex_mic_device", "default")
            if dev is None or dev == "" or dev == "default":
                return None
            return int(dev)
        except Exception:  # noqa: BLE001
            return None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="vosk-asr", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._pause_event.set()
        logger.debug("ASR 暂停（speaking 期间）")

    def resume(self) -> None:
        self._pause_event.clear()
        logger.debug("ASR 恢复")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _load_model(self):
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        if self._model_path:
            return Model(self._model_path)
        # 留空则尝试自动下载中文模型（需联网）
        return Model(lang="zh-cn")

    def _run(self) -> None:
        # 优先用 sherpa-onnx 流式识别器（识别质量远好于 vosk-small）；失败回退 vosk
        sherpa = None
        try:
            from .sherpa_recognizer import SherpaRecognizer
            sherpa = SherpaRecognizer()
        except Exception as e:  # noqa: BLE001
            logger.warning("Sherpa-ONNX 不可用，回退 vosk: %s", e)
            sherpa = None

        try:
            from vosk import KaldiRecognizer

            model = self._load_model()
        except Exception as e:  # noqa: BLE001
            logger.error("Vosk 模型加载失败: %s", e)
            self._bus.publish_threadsafe(Event(EventType.ERROR, {
                "code": "ASR_INIT_FAIL",
                "message": f"Vosk 模型加载失败: {e}",
            }, "asr"))
            return

        audio = AudioIO(self._sample_rate, device=self._resolve_mic_device())
        wake_det = WakeWordDetector(model, self._sample_rate, self._wake_words)
        cmd_rec = KaldiRecognizer(model, self._sample_rate)
        cmd_rec.SetWords(True)
        free_rec = None  # 免唤醒模式的 vosk 识别器（sherpa 不可用时懒创建）
        engine = "sherpa" if sherpa is not None else "vosk"

        # 麦克风监听总开关（mic_enabled=false / UI 里关掉）：初始就关着时【不打开
        # 麦克风设备】、挂起等开关——避免开关关着仍短暂占用麦克风（Windows 会
        # 记录"已使用麦克风"，用户看到采集误以为在偷听）。开关打开后循环退出，
        # 下面才开始采集；运行中热切换由主循环的监听检查处理。
        if not self._listen_event.is_set():
            logger.info("麦克风监听关闭，挂起等待（不采集不识别）")
            while self._running and not self._listen_event.is_set():
                time.sleep(0.3)

        try:
            audio.start()
        except Exception as e:  # noqa: BLE001
            logger.error("麦克风启动失败: %s", e)
            self._bus.publish_threadsafe(Event(EventType.ERROR, {
                "code": "MIC_FAIL",
                "message": f"麦克风启动失败: {e}",
            }, "asr"))
            return

        logger.info("ASR 循环启动，识别引擎: %s，唤醒词: %s", engine, self._wake_words)
        mode = "wake"  # wake | cmd
        cmd_start = 0.0

        while self._running:
            # 麦克风监听总开关：关闭 → 释放麦克风设备并挂起循环（不采集不识别）；
            # 重新开启 → 重建采集流恢复（唤醒词/持续识别随之恢复）。
            if not self._listen_event.is_set():
                audio.stop()   # 幂等：已释放则跳过
                logger.info("麦克风已释放（监听关闭）")
                while self._running and not self._listen_event.is_set():
                    time.sleep(0.3)
                try:
                    audio.start()
                    logger.info("麦克风采集恢复（监听开启）")
                except Exception as e:  # noqa: BLE001
                    logger.error("麦克风恢复失败: %s", e)
                    time.sleep(1.0)
                continue
            if self._pause_event.is_set():
                time.sleep(0.1)
                continue

            data = audio.get(timeout=0.2)
            if data is None:
                # 指令模式超时检查
                if mode == "cmd" and time.time() - cmd_start > self._cmd_timeout:
                    logger.debug("指令超时，回到唤醒模式")
                    mode = "wake"
                    if sherpa is not None:
                        sherpa.reset()
                continue

            # 免唤醒持续识别：说话即识别、随时对话（无需喊唤醒词）。
            # 识别器按静音自动切分一句话，识别到就发 USER_TEXT。
            if self._continuous_event.is_set():
                if sherpa is not None:
                    text = sherpa.feed(data)
                    if text:
                        logger.info("持续识别: %s", text)
                        self._bus.publish_threadsafe(Event(
                            EventType.USER_TEXT, {"text": text, "source": "voice"}, "asr"))
                else:  # vosk 回退
                    if free_rec is None:
                        free_rec = KaldiRecognizer(model, self._sample_rate)
                        free_rec.SetWords(True)
                    if free_rec.AcceptWaveform(data):
                        text = json.loads(free_rec.Result()).get("text", "")
                        if text:
                            logger.info("持续识别: %s", text)
                            self._bus.publish_threadsafe(Event(
                                EventType.USER_TEXT, {"text": text, "source": "voice"}, "asr"))
                continue

            if mode == "wake":
                word = wake_det.detect(data)
                if word:
                    logger.info("唤醒词命中: %s", word)
                    self._bus.publish_threadsafe(Event(
                        EventType.WAKE_WORD, {"word": word}, "asr"))
                    mode = "cmd"
                    cmd_start = time.time()
                    if sherpa is not None:
                        sherpa.reset()
            else:  # cmd
                if sherpa is not None:
                    text = sherpa.feed(data)
                    if text:
                        logger.info("识别指令: %s", text)
                        self._bus.publish_threadsafe(Event(
                            EventType.USER_TEXT, {"text": text, "source": "voice"}, "asr"))
                        mode = "wake"
                    elif time.time() - cmd_start > self._cmd_timeout:
                        mode = "wake"
                        sherpa.reset()
                else:  # vosk 回退
                    if cmd_rec.AcceptWaveform(data):
                        text = json.loads(cmd_rec.Result()).get("text", "")
                        if text:
                            logger.info("识别指令: %s", text)
                            self._bus.publish_threadsafe(Event(
                                EventType.USER_TEXT, {"text": text, "source": "voice"}, "asr"))
                        mode = "wake"
                    elif time.time() - cmd_start > self._cmd_timeout:
                        mode = "wake"

        audio.stop()
        logger.info("ASR 循环结束")
