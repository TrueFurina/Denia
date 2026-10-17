"""游戏音效识别：WASAPI 环路采集系统音频，参考音模板比对识别击杀/阵亡/胜负。

与 OCR/视觉画面识别互补，这是"物理事实"级别的信号：
- 不依赖分辨率 / UI 布局 / 字体——游戏画面缩放、HUD 换位置都不影响
- 不会幻觉——声音匹配是数值比对，不是模型看图猜
- 《三角洲行动》的击杀确认、阵亡、撤离成功音效是刻意设计的高辨识度音效，
  录制参考音后做归一化频谱比对，误报率远低于画面识别

识别流程（与 GameOcrMonitor 相同的"引信+即时"模式）：
1. 后台线程用 WASAPI 环路采集系统输出音频（16kHz 单声道）→ 线程安全队列
2. asyncio 循环每块读新音频 → 能量起点检测（RMS 相对背景跳变）
3. 起点后等参考音最大时长 + 余量 → 从环形缓冲截取段落 → log-mel 频谱
4. 与各参考音模板做逐帧归一化互相关（余弦相似度），取最大相似度
5. 超过阈值 → 发布 SCREEN_ACTIVITY(moment, source="audio")，proactive 即时互动

参考音录制：感知调试台点按钮（击杀/阵亡/胜利/失败），游戏里触发一次该音效，
自动裁出最大能量的窗口存为 WAV 模板（reference_dir）。

门禁：与 OCR 相同，当前有可信游戏画面来源（game_screen_source()!="none"）
才监听，防看视频/其他游戏时误触发。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import numpy as np

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from ._pa_shared import (  # 共享 PortAudio 单例 + 模块级常量
    get_shared_pyaudio, safe_find_loopback_device, pa_module)
from .foreground_monitor import ForegroundMonitor
from .game_ocr import MOMENT_LABEL
from .privacy import privacy_mode

logger = logging.getLogger(__name__)

# ==================== 音频 / 频谱参数 ====================
SAMPLE_RATE = 16000       # 统一处理采样率（16kHz 足够分辨音效）
N_FFT = 512               # FFT 窗口 32ms @16k
HOP_LEN = 160             # 帧移 10ms
N_MELS = 32               # 梅尔带数
MEL_MIN = 80.0
MEL_MAX = 7600.0

# 参考音模板时长：录制/加载时都裁到最大能量窗口（秒）
REF_TRIM_S = 1.2

# 起点检测（能量相对背景跳变）
ONSET_MIN_RMS = 0.008     # 绝对下限（低于此值不算起点，防静音噪声毛刺）
BG_DECAY = 0.93           # 背景 RMS 指数衰减（约 0.5s 时间常数）
ONSET_BLOCK_S = 1.6       # 命中起点后屏蔽窗口（同一声音不重复触发）

# 环形缓冲容量（秒）：参考音最长 1.2s + 起点前 0.2s + 匹配余量
RING_CAPACITY_S = 2.5


# ==================== 频谱工具 ====================
def _mel_filterbank(n_mels: int = N_MELS, sr: int = SAMPLE_RATE,
                    n_fft: int = N_FFT, fmin: float = MEL_MIN,
                    fmax: float = MEL_MAX) -> np.ndarray:
    """标准三角梅尔滤波器组，返回 (n_mels, n_fft//2+1)。"""
    fft_freqs = np.linspace(0, sr / 2, n_fft // 2 + 1)
    mel_min = 2595.0 * np.log10(1 + fmin / 700.0)
    mel_max = 2595.0 * np.log10(1 + fmax / 700.0)
    mels = np.linspace(mel_min, mel_max, n_mels + 2)
    hz = 700.0 * (10 ** (mels / 2595.0) - 1)
    bins = np.clip(np.floor((n_fft + 1) * hz / sr).astype(int), 0, n_fft // 2)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float64)
    for m in range(1, n_mels + 1):
        b0, b1, b2 = bins[m - 1], bins[m], bins[m + 1]
        if b1 > b0:
            fb[m - 1, b0:b1] = (np.arange(b0, b1) - b0) / (b1 - b0)
        if b2 > b1:
            fb[m - 1, b1:b2] = (b2 - np.arange(b1, b2)) / (b2 - b1)
    return fb


def _spectrogram(x: np.ndarray, sr: int = SAMPLE_RATE, n_fft: int = N_FFT,
                 hop: int = HOP_LEN, n_mels: int = N_MELS) -> np.ndarray:
    """float32 单声道 → log-mel 频谱 (n_mels, n_frames)。"""
    x = np.asarray(x, dtype=np.float32)
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n_frames = max(1, (len(x) - n_fft) // hop + 1)
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop][:n_frames]
    win = np.hanning(n_fft).astype(np.float32)
    frames = frames * win
    spec = np.fft.rfft(frames, n=n_fft, axis=1)
    power = (np.abs(spec) ** 2).astype(np.float64)
    fb = _mel_filterbank(n_mels, sr, n_fft)
    mel = fb @ power.T  # (n_mels, n_frames)
    return np.log10(mel + 1e-10).astype(np.float32)


def _frame_normalize(spec: np.ndarray) -> np.ndarray:
    """每帧去均值 + L2 归一化（音量不变性；去均值消除 log 底噪偏移）。"""
    s = spec - spec.mean(axis=0, keepdims=True)
    n = np.linalg.norm(s, axis=0, keepdims=True)
    n[n < 1e-8] = 1.0
    return s / n


def _trim_loudest(spec: np.ndarray, target_s: float = REF_TRIM_S,
                  hop: int = HOP_LEN, sr: int = SAMPLE_RATE) -> np.ndarray:
    """截取频谱中能量最大的连续 target_s 秒窗口（去掉首尾静音/无意义部分）。"""
    if spec.shape[1] <= 1:
        return spec
    target_frames = max(1, int(target_s * sr / hop))
    if spec.shape[1] <= target_frames:
        return spec
    energy = np.sum(spec ** 2, axis=0)
    cum = np.concatenate([[0.0], np.cumsum(energy)])
    best_start, best = 0, -1.0
    for i in range(len(cum) - target_frames):
        s = cum[i + target_frames] - cum[i]
        if s > best:
            best, best_start = s, i
    return spec[:, best_start:best_start + target_frames]


def _trim_loudest_time(x: np.ndarray, target_s: float = REF_TRIM_S,
                       sr: int = SAMPLE_RATE) -> np.ndarray:
    """截取时间信号中能量最大的连续 target_s 秒窗口。"""
    x = np.asarray(x, dtype=np.float32)
    target = max(1, int(target_s * sr))
    if len(x) <= target:
        return x
    hop = 320  # 20ms 块
    rms = np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) if len(x[i:i + hop]) else 0.0
                    for i in range(0, len(x), hop)], dtype=np.float64)
    k = max(1, target // hop)
    if len(rms) <= k:
        return x
    cum = np.concatenate([[0.0], np.cumsum(rms)])
    best, best_i = -1.0, 0
    for i in range(len(cum) - k):
        s = cum[i + k] - cum[i]
        if s > best:
            best, best_i = s, i
    start = best_i * hop
    return x[start:start + target]


def _match_template(seg_spec: np.ndarray, tpl_spec: np.ndarray) -> float:
    """模板在段落频谱上滑窗，返回最大平均余弦相似度 (0~1)。

    对 log-mel 频谱逐帧去均值 + L2 归一化后求点积（=归一化互相关），
    时间轴滑窗步长 2 帧（20ms）容忍起点偏移。
    """
    seg_norm = _frame_normalize(seg_spec)
    tpl_norm = _frame_normalize(tpl_spec)
    T = tpl_norm.shape[1]
    if seg_norm.shape[1] < T:
        return 0.0
    best = 0.0
    for start in range(0, seg_norm.shape[1] - T + 1, 2):
        sim = float(np.mean(np.sum(seg_norm[:, start:start + T] * tpl_norm, axis=0)))
        if sim > best:
            best = sim
    return best


def match_segment(seg: np.ndarray, templates: dict, threshold: float,
                  sr: int = SAMPLE_RATE) -> tuple[Optional[str], float]:
    """段音频 → 与模板库（{event_type: [spec,...]}）比对，返回 (moment, sim)。

    供监控器与单元测试共用：无模板 / 段过短 → (None, 0.0)。
    """
    if not templates or len(seg) < int(sr * 0.2):
        return None, 0.0
    seg_spec = _spectrogram(seg)
    best_moment, best_sim = None, 0.0
    for ev, tpl_list in templates.items():
        for tpl in tpl_list:
            sim = _match_template(seg_spec, tpl)
            if sim > best_sim:
                best_sim, best_moment = sim, ev
    if best_moment is not None and best_sim >= threshold:
        return best_moment, best_sim
    return None, 0.0


# ==================== 音频 IO / 重采样 ====================
def _resample(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性插值重采样（对音效模板比对足够）。"""
    if src_sr == dst_sr or len(x) == 0:
        return np.asarray(x, dtype=np.float32)
    n_out = max(1, int(round(len(x) * dst_sr / src_sr)))
    idx = np.arange(n_out, dtype=np.float64) * (len(x) - 1) / max(1, n_out - 1)
    i0 = np.floor(idx).astype(int)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (idx - i0).astype(np.float32)
    return (x[i0] * (1 - frac) + x[i1] * frac).astype(np.float32)


def _load_wav(path: str) -> tuple[np.ndarray, int]:
    """读取 WAV → (float32 单声道, sample_rate)。"""
    import wave
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n_ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        x = np.frombuffer(raw, dtype=np.float32)
    if n_ch > 1 and len(x) % n_ch == 0:
        x = x.reshape(-1, n_ch).mean(axis=1)
    return x.astype(np.float32), sr


def _save_wav(path: str, x: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """float32 mono → 16bit PCM WAV。"""
    import wave
    x = np.asarray(x, dtype=np.float32)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.99:
        x = x * (0.99 / peak)
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def find_loopback_device() -> Optional[dict]:
    """探测系统是否有 WASAPI 环路输出设备（供 UI 展示/门禁）。

    🛡️【2026-09-27 闪退修复】改用共享 PyAudio 单例 + 全局锁（_pa_shared），
    会话期间绝不 terminate，避免与游戏音效采集 / 进程环回等其他线程并发 init/terminate
    触发原生访问违规（0xC0000005）。
    """
    return safe_find_loopback_device()


# ==================== 环路采集线程 ====================
class _LoopbackCapture(threading.Thread):
    """WASAPI 环路采集线程：把系统输出音频（16kHz 单声道）不断写入线程安全队列。"""

    def __init__(self, device: dict, block_ms: int = 40) -> None:
        super().__init__(daemon=True, name="game_audio_capture")
        self._device = device
        self._block_ms = block_ms
        self._queue: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()

    @property
    def ready(self) -> threading.Event:
        return self._ready

    def stop(self) -> None:
        self._stop.set()

    def read_all(self, max_samples: int = 96000) -> np.ndarray:
        """取出队列里所有音频（最多 max_samples 采样，超限保留最近）。"""
        with self._lock:
            chunks = list(self._queue)
            self._queue.clear()
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        x = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        if len(x) > max_samples:
            x = x[-max_samples:]
        return x

    def _append(self, x: np.ndarray) -> None:
        if len(x) == 0:
            return
        with self._lock:
            self._queue.append(x)

    def run(self) -> None:
        try:
            # 🛡️ 共享单例：会话期间绝不 terminate（见 _pa_shared），避免并发销毁踩坏
            # PortAudio 全局状态 → 原生访问违规（0xC0000005）。
            p = get_shared_pyaudio()
        except Exception as e:  # noqa: BLE001
            logger.warning("PyAudio 初始化失败，环路采集不可用: %s", e)
            return
        dev = self._device
        sr = int(dev.get("defaultSampleRate") or 48000)
        ch = int(dev.get("maxInputChannels") or 2)
        chunk = max(1, int(sr * self._block_ms / 1000.0))
        stream = None
        try:
            # ⚠️ paFloat32 是模块级常量（实例上取会 AttributeError）
            stream = p.open(format=pa_module().paFloat32, channels=ch, rate=sr,
                            input=True, input_device_index=dev["index"],
                            frames_per_buffer=chunk)
            self._ready.set()
            logger.info("游戏音效环路采集开始: %s (%dHz %dch)",
                        dev.get("name", ""), sr, ch)
            while not self._stop.is_set():
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                except Exception as e:  # noqa: BLE001
                    logger.warning("环路采集读取失败: %s", e)
                    time.sleep(0.05)
                    continue
                x = np.frombuffer(data, dtype=np.float32)
                if ch > 1 and len(x) % ch == 0:
                    x = x.reshape(-1, ch).mean(axis=1)
                if sr != SAMPLE_RATE:
                    x = _resample(x, sr, SAMPLE_RATE)
                self._append(x.astype(np.float32))
        except Exception as e:  # noqa: BLE001
            logger.warning("环路采集异常: %s", e)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            # 🛡️ 不 terminate 共享单例（见 _pa_shared）：只关流，流关闭不影响其它线程。


# ==================== 环形缓冲 ====================
class _RingBuffer:
    """固定容量环形缓冲，记录绝对写入位置以便截取任意区间。"""

    def __init__(self, capacity: int) -> None:
        self._cap = max(1, int(capacity))
        self._chunks: deque[np.ndarray] = deque()
        self._n = 0            # 缓冲内当前采样数
        self._total = 0        # 累计写入总采样数

    @property
    def total(self) -> int:
        return self._total

    def append(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float32)
        if len(x) == 0:
            return
        self._chunks.append(x)
        self._n += len(x)
        self._total += len(x)
        while self._n > self._cap:
            head = self._chunks[0]
            overflow = self._n - self._cap
            if len(head) <= overflow:
                self._chunks.popleft()
                self._n -= len(head)
            else:
                self._chunks[0] = head[overflow:]
                self._n -= overflow

    def range(self, start_total: int, end_total: int) -> np.ndarray:
        """截取绝对区间 [start_total, end_total) 的采样（按时间顺序）。"""
        start_total = max(0, start_total)
        end_total = min(self._total, end_total)
        if end_total <= start_total:
            return np.zeros(0, dtype=np.float32)
        base = self._total - self._n  # 缓冲内首采样的绝对位置
        if start_total < base:
            start_total = base
        parts: list[np.ndarray] = []
        pos = base
        for c in self._chunks:
            if pos >= end_total:
                break
            c_start = max(0, start_total - pos)
            c_end = min(len(c), end_total - pos)
            if c_end > c_start:
                parts.append(c[c_start:c_end])
            pos += len(c)
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts)


# ==================== 监控器 ====================
DEFAULT_REFS = ("kill", "died", "victory", "defeat")


class GameAudioMonitor:
    """游戏音效识别监控器（WASAPI 环路采集 + 参考音模板比对）。"""

    def __init__(self, bus: EventBus, cfg: Config,
                 monitor: Optional[ForegroundMonitor] = None) -> None:
        self._bus = bus
        self._monitor = monitor
        audio_cfg = getattr(getattr(cfg.ns, "perception", None), "audio", None)
        self._enabled: bool = bool(getattr(audio_cfg, "enabled", True) if audio_cfg else True)
        self._ref_dir: str = str(getattr(audio_cfg, "reference_dir",
                                         "references/game_audio") or "references/game_audio")
        self._block_ms: int = int(getattr(audio_cfg, "block_ms", 40) or 40)
        self._threshold: float = float(getattr(audio_cfg, "threshold", 0.55) or 0.55)
        self._cooldown_s: float = float(getattr(audio_cfg, "cooldown_s", 20) or 20)
        self._min_interval_s: float = float(getattr(audio_cfg, "min_interval_s", 3) or 3)
        self._onset_mult: float = float(getattr(audio_cfg, "onset_mult", 5.0) or 5.0)

        # 事件类型 → 参考音文件名（支持单个字符串或多个列表）
        self._refs: dict[str, list[str]] = {}
        raw_refs = getattr(audio_cfg, "refs", None)
        if raw_refs is not None:
            for key in DEFAULT_REFS:
                v = getattr(raw_refs, key, None)
                if isinstance(v, str) and v.strip():
                    self._refs[key] = [v.strip()]
                elif isinstance(v, (list, tuple)) and v:
                    self._refs[key] = [str(f).strip() for f in v if str(f).strip()]
        for key in DEFAULT_REFS:
            self._refs.setdefault(key, [f"{key}.wav"])

        self._running: bool = True
        self._device: Optional[dict] = None
        self._capture: Optional[_LoopbackCapture] = None
        self._ring = _RingBuffer(int(RING_CAPACITY_S * SAMPLE_RATE))
        self._ref_lock = threading.Lock()
        self._templates: dict[str, list[np.ndarray]] = {}
        self._ref_max_dur: float = 1.0

        # 起点检测状态
        self._bg_rms: float = 0.0
        self._pending_onset: Optional[tuple[int, float]] = None  # (绝对采样位置, 时间)
        self._onset_block_until: float = 0.0

        # 统计 / 调试
        self._moment_last: dict[str, float] = {}
        self._last_publish_ts: float = 0.0
        self._onset_count: int = 0
        self._match_count: int = 0
        self._last_onset_ts: float = 0.0
        self._recent: list[dict] = []

    # ---- 配置 ----
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def refs(self) -> dict:
        return {k: list(v) for k, v in self._refs.items()}

    @property
    def reference_dir(self) -> str:
        return self._ref_dir

    def stop(self) -> None:
        self._running = False
        self._stop_capture()

    def _stop_capture(self) -> None:
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:  # noqa: BLE001
                pass
            self._capture = None

    # ---- 参考音管理 ----
    def reload_references(self) -> None:
        """（重新）加载参考音模板。录制/修改 WAV 后调用。"""
        new_templates: dict[str, list[np.ndarray]] = {}
        ref_max_dur = 1.0
        for ev, files in self._refs.items():
            for fname in files:
                path = os.path.join(self._ref_dir, fname)
                if not os.path.isfile(path):
                    continue
                try:
                    x, sr = _load_wav(path)
                    if len(x) < int(SAMPLE_RATE * 0.08):
                        logger.warning("参考音 %s 过短(%.1fms)，忽略", path, len(x) * 1000 / sr)
                        continue
                    if sr != SAMPLE_RATE:
                        x = _resample(x, sr, SAMPLE_RATE)
                    spec = _trim_loudest(_spectrogram(x), REF_TRIM_S)
                    new_templates.setdefault(ev, []).append(spec)
                    dur = spec.shape[1] * HOP_LEN / SAMPLE_RATE
                    ref_max_dur = max(ref_max_dur, dur)
                except Exception as e:  # noqa: BLE001
                    logger.warning("加载参考音 %s 失败: %s", path, e)
        with self._ref_lock:
            self._templates = new_templates
            self._ref_max_dur = ref_max_dur
        logger.info("参考音模板: %s", {ev: len(t) for ev, t in new_templates.items()} or "无")

    def reference_status(self) -> dict:
        """参考音文件状态（供 UI 展示）：{event_type: {exists, file, duration_s}}。"""
        out: dict[str, dict] = {}
        for ev, files in self._refs.items():
            fname = files[0]
            path = os.path.join(self._ref_dir, fname)
            info: dict[str, Any] = {"exists": os.path.isfile(path), "file": fname,
                                    "duration_s": None}
            if info["exists"]:
                try:
                    x, sr = _load_wav(path)
                    info["duration_s"] = round(len(x) / sr, 2)
                except Exception:  # noqa: BLE001
                    pass
            out[ev] = info
        return out

    def record_reference(self, event_type: str, seconds: float = 2.5,
                         on_progress: Optional[Callable[[float, float], None]] = None,
                         stop_event: Optional[threading.Event] = None,
                         loopback_device: Optional[dict] = None) -> tuple[bool, Any]:
        """录制当前系统输出音频（游戏音效）作为参考音模板。

        - 事件触发：在游戏里触发一次对应音效（如击杀确认音/阵亡音/撤离成功音）
        - 录制 seconds 秒后自动裁出最大能量窗口，存为 {ref_dir}/{event_type}.wav
        - 返回 (成功, 信息 dict / 错误消息)。录完自动重载模板，立即生效。
        """
        if event_type not in self._refs:
            return False, f"未知事件类型: {event_type}（可用: {list(self._refs)}）"
        try:
            # 🛡️ 共享单例：会话期间绝不 terminate（见 _pa_shared）
            p = get_shared_pyaudio()
        except Exception as e:  # noqa: BLE001
            return False, f"PyAudio 初始化失败: {e}"
        dev = loopback_device or safe_find_loopback_device()
        if dev is None:
            return False, "未找到 WASAPI 环路输出设备"
        sr = int(dev.get("defaultSampleRate") or 48000)
        ch = int(dev.get("maxInputChannels") or 2)
        chunk = max(1, int(sr * 0.05))
        frames: list[bytes] = []
        # ⚠️ 同上：模块级常量
        stream = p.open(format=pa_module().paFloat32, channels=ch, rate=sr,
                        input=True, input_device_index=dev["index"],
                        frames_per_buffer=chunk)
        started = time.time()
        try:
            while time.time() - started < seconds:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                except Exception as e:  # noqa: BLE001
                    logger.warning("录制读取失败: %s", e)
                    break
                frames.append(data)
                if on_progress is not None:
                    try:
                        on_progress(time.time() - started, seconds)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            # 🛡️ 只关流，不 terminate 共享单例（见 _pa_shared）
            try:
                stream.stop_stream()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if not frames:
            return False, "未采集到音频（设备可能静音或未运行）"
        x = np.frombuffer(b"".join(frames), dtype=np.float32)
        if ch > 1 and len(x) % ch == 0:
            x = x.reshape(-1, ch).mean(axis=1)
        x = _resample(x, sr, SAMPLE_RATE)
        x = _trim_loudest_time(x, REF_TRIM_S)
        if len(x) < int(SAMPLE_RATE * 0.1):
            return False, "采集音频过短（音量可能太低）"
        os.makedirs(self._ref_dir, exist_ok=True)
        path = os.path.join(self._ref_dir, self._refs[event_type][0])
        _save_wav(path, x)
        self.reload_references()
        logger.info("已录制参考音 %s → %s (%.2fs)", event_type, path, len(x) / SAMPLE_RATE)
        return True, {"event_type": event_type, "path": path,
                      "duration_s": round(len(x) / SAMPLE_RATE, 2)}

    # ---- 运行时快照 ----
    def snapshot(self) -> dict:
        """运行状态快照（供感知调试台轮询，线程安全）。"""
        rec_ok = [ev for ev in DEFAULT_REFS
                  if any(t for t in self._templates.get(ev, []))]
        return {
            "enabled": self._enabled,
            "running": self._capture is not None and self._capture.is_alive(),
            "device": (self._device or {}).get("name", ""),
            "sample_rate": SAMPLE_RATE,
            "block_ms": self._block_ms,
            "threshold": self._threshold,
            "cooldown_s": self._cooldown_s,
            "onset_mult": self._onset_mult,
            "ref_dir": self._ref_dir,
            "references": self.reference_status(),
            "loaded": {ev: len(t) for ev, t in self._templates.items()},
            "onset_count": self._onset_count,
            "match_count": self._match_count,
            "last_onset_ago": round(time.time() - self._last_onset_ts, 1) if self._last_onset_ts else None,
            "last_publish_ago": round(time.time() - self._last_publish_ts, 1) if self._last_publish_ts else None,
            "background_rms": round(self._bg_rms, 4),
            "moment_last": dict(self._moment_last),
            "recent": list(self._recent),
        }

    # ---- 主循环 ----
    async def run(self) -> None:
        if not self._enabled:
            logger.info("游戏音效识别未启用")
            return
        self._device = find_loopback_device()
        if self._device is None:
            logger.warning("未找到 WASAPI 环路输出设备，游戏音效识别禁用")
            return
        self.reload_references()
        self._capture = _LoopbackCapture(self._device, self._block_ms)
        self._capture.start()
        logger.info(
            "游戏音效识别启动: 设备=%s 阈值=%.2f 冷却=%ss 参考音=%s",
            self._device.get("name", ""), self._threshold, self._cooldown_s,
            {ev: len(t) for ev, t in self._templates.items()} or "无（请先录制参考音）")
        while self._running:
            try:
                await self._process()
            except Exception:  # noqa: BLE001
                logger.debug("游戏音效处理异常", exc_info=True)
            await asyncio.sleep(self._block_ms / 1000.0)
        self._stop_capture()

    async def _process(self) -> None:
        cap = self._capture
        if cap is None or not cap.is_alive():
            return
        # 先排空采集队列（无论门禁是否通过，避免非游戏时段队列无限增长）
        x = cap.read_all()
        if len(x) == 0:
            return
        if privacy_mode():
            # 隐私模式：丢弃音频块，不做匹配不发事件（模型拿不到系统声音）
            return
        self._ring.append(x)
        # 门禁：有可信游戏画面来源（游戏前台 / 腾讯会议投屏）才监听——
        # 防看视频/其他游戏时误触发（与 OCR 同源规则）
        if self._monitor is not None:
            try:
                if self._monitor.game_screen_source() == "none":
                    return
            except Exception:  # noqa: BLE001
                pass
        if not self._templates:
            return  # 无模板：只累积缓冲，不做匹配
        now = time.time()
        # 能量起点检测：块 RMS 相对背景跳变（游戏音效是短时高能量事件）
        rms = float(np.sqrt(np.mean(x * x)))
        self._bg_rms = self._bg_rms * BG_DECAY + rms * (1.0 - BG_DECAY)
        if (rms > max(ONSET_MIN_RMS, self._bg_rms * self._onset_mult)
                and now > self._onset_block_until):
            self._pending_onset = (self._ring.total - len(x), now)
            self._onset_block_until = now + ONSET_BLOCK_S
            self._onset_count += 1
            self._last_onset_ts = now
            logger.debug("音效起点: RMS=%.4f 背景=%.4f", rms, self._bg_rms)
        # 起点已到、且已等够参考音最大时长 → 截段匹配（CPU 工作放线程）
        if self._pending_onset is not None:
            onset_total, onset_ts = self._pending_onset
            if now - onset_ts >= self._ref_max_dur + 0.5:
                self._pending_onset = None
                seg = self._ring.range(onset_total, self._ring.total)
                if len(seg) >= int(SAMPLE_RATE * 0.3):
                    moment, sim = await asyncio.to_thread(
                        match_segment, seg, dict(self._templates), self._threshold)
                    if moment is not None:
                        await self._publish(moment, sim)

    async def _publish(self, moment: str, sim: float) -> None:
        now = time.time()
        if now - self._moment_last.get(moment, 0.0) < self._cooldown_s:
            logger.debug("音效时刻 %s 冷却中，跳过（相似度=%.2f）", moment, sim)
            return
        if now - self._last_publish_ts < self._min_interval_s:
            logger.debug("音效全局最小间隔未到，跳过")
            return
        self._moment_last[moment] = now
        self._last_publish_ts = now
        self._match_count += 1
        label = MOMENT_LABEL.get(moment, "游戏关键时刻")
        summary = f"{label}（音效确认）"
        activity = {
            "summary": summary,
            "app_hint": "game",
            "confidence": round(float(sim), 3),
            "moment": moment,
            "source": "audio",
        }
        self._recent.append({
            "ts": now, "moment": moment, "sim": round(float(sim), 3),
            "summary": summary, "source": "audio",
        })
        self._recent = self._recent[-10:]
        logger.info("游戏音效命中[audio]: %s (相似度=%.2f)", label, sim)
        await self._bus.publish(Event(EventType.SCREEN_ACTIVITY, activity, "audio"))
