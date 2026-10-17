# -*- coding: utf-8 -*-
"""系统级"环回排除自身软件声音"采集（WASAPI 进程级环回排除）。

问题：WASAPI 默认环回采的是默认渲染设备的最终混音，达妮娅自己的 TTS 播
放（进程内 sounddevice/pygame 或独立 ffplay 子进程）也在混音里，被模型
听见后产生"自言自语 / 续写自己刚说的话"。

Win10 2004(build 19041)+ 提供**进程级环回排除**：IMMDevice::Activate 时传入
AUDIOCLIENT_ACTIVATION_PARAMS{ActivationType=PROCESS_LOOPBACK,
TargetProcessId=宿主pid, ProcessLoopbackMode=EXCLUDE}，即可拿到"整设备混音
减去宿主进程声音"的环回流——用户照常能听到达妮娅，只是达妮娅自己的声音
不进环回。比软件回声消除（AEC）干净：零 DSP、无残响、说话时仍能听环境音。

排除范围：
- 进程内播放（CosyVoice2 sounddevice / pygame / pyttsx3）：与宿主同 pid，
  EXCLUDE 直接剔除。
- ffplay 独立子进程（Edge / 系统 TTS 等走 ffplay）：不同 pid，单条进程环回
  流只能排除一个 pid，故 ffplay 走"播放中临时静音闸门"(FFPLAY_PLAYING)剔除，
  等效隔离（用户仍能听到，只是不进环回）。

非 Win10 2004+ 或驱动排除不可用：start_self_excluded_capture() 返回 False，
调用方回退到旧 pyaudiowpatch 整设备环回，并由 INPROC_SPEAK_GATE 在自身播放
期间整体静音兜底（安全降级，代价是自身播放那几秒模型对环境音失聪）。

Windows only；非 Windows / 失败均安全降级（返回 False 或零帧）。
复用 audio_sessions.py 的裸 ctypes COM 样板（无 comtypes / pycaw 依赖）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import struct
import threading
import time
from typing import Callable, Optional

import numpy as np

from ._pa_shared import get_shared_pyaudio, safe_find_loopback_device  # 共享 PortAudio 单例
from .audio_sessions import _GUID, _guid, _vtbl_call, _release  # 复用裸 ctypes COM 样板

logger = logging.getLogger(__name__)

HRESULT = ctypes.c_long
_ole32 = ctypes.windll.ole32

# ---- GUID ----
CLSID_MMDeviceEnumerator = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioClient = _guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient = _guid("{C8ADBD64-E71E-48A0-A4DE-185C397CDAFB}")

# ---- 常量 ----
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
AUDCLNT_PROCESS_LOOPBACK_MODE_EXCLUDE = 1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
VT_BLOB = 0x41
MIN_WIN_BUILD = 19041  # Windows 10 2004


class _ACT_PARAMS(ctypes.Structure):
    """AUDIOCLIENT_ACTIVATION_PARAMS（进程环回）。"""
    _fields_ = [
        ("ActivationType", ctypes.c_ulong),       # 1 = PROCESS_LOOPBACK
        ("TargetProcessId", ctypes.c_ulong),      # 排除的进程 pid
        ("ProcessLoopbackMode", ctypes.c_ulong),  # 1 = EXCLUDE
    ]


# ---- 全局闸门 / 状态（供 tts.py 与三处采集共享）----
FFPLAY_PLAYING = threading.Event()
"""ffplay 独立子进程正在播放（自身软件声音，必须排除；进程环回无法排除第二 pid）。"""

INPROC_SPEAK_GATE = threading.Event()
"""进程内 TTS（CosyVoice2/pygame/pyttsx3）正在播放且 driver 未排除时置位 → 采集端静音。"""

_DRIVER_EXCLUDES_HOST = False
"""当前是否有一条 driver 级（进程环回）排除宿主进程的声音在跑。"""

# 探活缓存：本机若进程环回排除不可用（如 GetService 返回 E_INVALIDARG），
# 首探失败后记住，后续 start_self_excluded_capture 直接返回 False 走闸门，
# 避免每次双工启动都空等 2s + 刷告警。
_SELF_EXCLUDE_PROBED = False
_SELF_EXCLUDE_WORKS = False


def driver_excludes_host() -> bool:
    """调用方（tts.py）据此决定是否还要用 INPROC_SPEAK_GATE。"""
    return _DRIVER_EXCLUDES_HOST


def process_loopback_supported() -> bool:
    """Win10 2004(build 19041)+ 才提供进程级环回排除。"""
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.kernel32.GetVersionExW is not None and \
            sys_get_build() >= MIN_WIN_BUILD
    except Exception:  # noqa: BLE001
        return False


def sys_get_build() -> int:
    try:
        vi = ctypes.wintypes.OSVERSIONINFOEXW()
        vi.dwOSVersionInfoSize = ctypes.sizeof(vi)  # type: ignore[attr-defined]
        if ctypes.windll.kernel32.RtlGetVersion(ctypes.byref(vi)) == 0:  # type: ignore[attr-defined]
            return vi.dwBuildNumber  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(os.sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001
        return 0


def _convert(data: Optional[bytes], nframes: int, nch: int,
             rate: int, target_rate: int) -> Optional[np.ndarray]:
    """环回原始帧 → 单声道 + 重采样到 target_rate 的 float32 数组。"""
    if data is None:
        arr = np.zeros(nframes * max(nch, 1), dtype=np.float32)
    else:
        arr = np.frombuffer(data, dtype=np.float32).copy()
    if arr.size == 0:
        return arr
    if nch > 1 and arr.size % nch == 0:
        arr = arr.reshape(-1, nch).mean(axis=1)
    if rate != target_rate:
        n_out = max(1, int(arr.size * target_rate / rate))
        idx = (np.arange(n_out, dtype=np.float64) * (arr.size - 1) / max(n_out - 1, 1))
        lo = idx.astype(np.int64)
        hi = np.minimum(lo + 1, arr.size - 1)
        frac = (idx - lo).astype(np.float32)
        arr = arr[lo] * (1 - frac) + arr[hi] * frac
    return arr.astype(np.float32)


def _gated_on_chunk(arr: np.ndarray, on_chunk: Callable[[np.ndarray], None]) -> None:
    """过滤自身软件声音后交给消费者：ffplay 播放中 / (driver 未排除且进程内播放中) → 静音。"""
    if self_speak_active():
        on_chunk(np.zeros_like(arr))
    else:
        on_chunk(arr)


def self_speak_active() -> bool:
    """当前是否应把环回音频静音（自身软件正在出声，需排除）。

    - FFPLAY_PLAYING：ffplay 独立子进程播放中（进程环回无法排除第二 pid）。
    - 否则若 driver 未用进程环回排除宿主进程，且进程内 TTS(CosyVoice2/pygame/pyttsx3)
      正在播放（INPROC_SPEAK_GATE）→ 静音。
    """
    return FFPLAY_PLAYING.is_set() or ((not _DRIVER_EXCLUDES_HOST) and INPROC_SPEAK_GATE.is_set())


def _safe_find_wasapi_loopback(p=None) -> Optional[dict]:
    """安全获取默认 WASAPI 环回设备信息（绝不调用会堆损坏的 get_default_wasapi_loopback）。

    委托给 ``_pa_shared.safe_find_loopback_device()``：用进程级共享 PyAudio 单例 +
    全局锁串行化，会话期间绝不 terminate，从源头规避多线程并发 init/terminate 触发的
    原生访问违规（0xC0000005）。

    参数 p 已废弃（不再需要调用方自行创建 PyAudio），保留仅为了兼容既有调用方签名。
    """
    return safe_find_loopback_device()


def _capture_thread(stop_event: threading.Event,
                    on_chunk: Callable[[np.ndarray], None],
                    target_rate: int,
                    ready: threading.Event,
                    done: threading.Event) -> None:
    global _DRIVER_EXCLUDES_HOST, _SELF_EXCLUDE_PROBED, _SELF_EXCLUDE_WORKS
    _ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    _SELF_EXCLUDE_PROBED = True
    _SELF_EXCLUDE_WORKS = False
    audio_client = ctypes.c_void_p()
    cap = ctypes.c_void_p()
    device = ctypes.c_void_p()
    dev_enum = ctypes.c_void_p()
    mix_ptr = ctypes.c_void_p()
    try:
        # 1) MMDeviceEnumerator
        hr = _ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator), None, 7,  # CLSCTX_INPROC|HANDLER|LOCAL
            ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(dev_enum))
        if hr < 0 or not dev_enum:
            return
        # 2) 默认渲染设备
        hr = _vtbl_call(dev_enum, 4, HRESULT,
                        (ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)),
                        0, 0, ctypes.byref(device))
        if hr < 0 or not device:
            return
        # 3) 进程环回排除参数（PROPVARIANT 包裹 AUDIOCLIENT_ACTIVATION_PARAMS）
        act = _ACT_PARAMS()
        act.ActivationType = AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        act.TargetProcessId = os.getpid() & 0xFFFFFFFF
        act.ProcessLoopbackMode = AUDCLNT_PROCESS_LOOPBACK_MODE_EXCLUDE
        act_buf = ctypes.create_string_buffer(ctypes.sizeof(act))
        ctypes.memmove(act_buf, ctypes.byref(act), ctypes.sizeof(act))
        # PROPVARIANT: vt(2) + 3×reserved(6) + BLOB{cbSize(4) + pBlobData(8)} = 24B（含对齐）
        pv = ctypes.create_string_buffer(24)
        struct.pack_into("<H", pv, 0, VT_BLOB)
        struct.pack_into("<I", pv, 8, ctypes.sizeof(act))
        struct.pack_into("<Q", pv, 16, ctypes.addressof(act_buf))
        # 4) Activate(IAudioClient, 带进程环回 PROPVARIANT)
        hr = _vtbl_call(device, 3, HRESULT,
                        (ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_void_p)),
                        ctypes.byref(IID_IAudioClient), 7,
                        ctypes.cast(pv, ctypes.c_void_p), ctypes.byref(audio_client))
        if hr < 0 or not audio_client:
            logger.warning("进程环回排除: IMMDevice::Activate 失败 hr=0x%x（回退整设备环回）", hr)
            return
        # 5) 混合格式：GetMixFormat 在本机 ctypes 下会崩溃，改为用共享 PyAudio 单例读真实环回
        #    设备格式（defaultSampleRate / maxInputChannels），WASAPI 共享混音为 float32。
        #    🛡️ 用 safe_find_loopback_device()：共享单例 + 全局锁，绝不 terminate，
        #       避免与别的线程并发 init/terminate 触发原生访问违规（0xC0000005）。
        try:
            _dev = safe_find_loopback_device()
            if _dev is not None:
                rate = int(_dev["defaultSampleRate"])
                nch = max(1, int(_dev["maxInputChannels"]))
            else:
                rate, nch = 48000, 2
        except Exception:  # noqa: BLE001
            rate, nch = 48000, 2
        logger.info("进程环回排除: 设备混音格式 %dHz %dch（float32）", rate, nch)
        # 6) Initialize（共享模式 + 环回；显式 WAVEFORMATEXTENSIBLE float32，NULL 会被拒）
        _sub_float = bytes.fromhex("0300000000001000800000aa00389b71")  # KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
        wfx = struct.pack("<HHIIHHHHI16s", 0xFFFE, nch, rate,
                          rate * nch * 4, nch * 4, 32, 22, 32,
                          3 if nch == 2 else 1, _sub_float)
        wfx_buf = ctypes.create_string_buffer(wfx, len(wfx))
        hr = _vtbl_call(audio_client, 3, HRESULT,
                        (ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int64, ctypes.c_int64,
                         ctypes.c_void_p, ctypes.c_void_p),
                        0, AUDCLNT_STREAMFLAGS_LOOPBACK, 0, 0,
                        ctypes.cast(wfx_buf, ctypes.c_void_p), None)
        if hr < 0:
            logger.warning("进程环回排除: IAudioClient::Initialize 失败 hr=0x%x（回退整设备环回）", hr & 0xFFFFFFFF)
            return
        # 7) GetService(IAudioCaptureClient)
        hr = _vtbl_call(audio_client, 15, HRESULT,
                        (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
                        ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(cap))
        if hr < 0 or not cap:
            logger.warning("进程环回排除: GetService(IAudioCaptureClient) 失败 hr=0x%x（回退整设备环回）", hr & 0xFFFFFFFF)
            return
        # 8) Start
        _vtbl_call(audio_client, 11, HRESULT, (), )

        _DRIVER_EXCLUDES_HOST = True
        _SELF_EXCLUDE_WORKS = True
        logger.info("进程环回排除: 已开启（剔除宿主 pid=%d，%dHz %dch）", os.getpid(), rate, nch)
        ready.set()

        # 9) 拉环回包
        while not stop_event.is_set():
            nxt = ctypes.c_uint(0)
            _vtbl_call(cap, 5, HRESULT, (ctypes.POINTER(ctypes.c_uint),), ctypes.byref(nxt))
            if nxt.value == 0:
                time.sleep(0.005)
                continue
            pp = ctypes.c_void_p()
            nframes = ctypes.c_uint(0)
            flags = ctypes.c_uint(0)
            devpos = ctypes.c_ulonglong(0)
            qpc = ctypes.c_ulonglong(0)
            hr = _vtbl_call(cap, 3, HRESULT,
                            (ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint),
                             ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_ulonglong),
                             ctypes.POINTER(ctypes.c_ulonglong)),
                            ctypes.byref(pp), ctypes.byref(nframes),
                            ctypes.byref(flags), ctypes.byref(devpos), ctypes.byref(qpc))
            if hr < 0 or nframes.value == 0:
                _vtbl_call(cap, 4, HRESULT, (ctypes.c_uint,), 0)
                continue
            raw = None if (flags.value & AUDCLNT_BUFFERFLAGS_SILENT) else \
                ctypes.string_at(pp.value, nframes.value * nch * 4)
            _vtbl_call(cap, 4, HRESULT, (ctypes.c_uint,), nframes.value)
            arr = _convert(raw, nframes.value, nch, rate, target_rate)
            if arr is not None and arr.size:
                try:
                    _gated_on_chunk(arr, on_chunk)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        logger.warning("进程环回排除: 捕获线程异常（回退整设备环回+闸门）", exc_info=True)
    finally:
        _DRIVER_EXCLUDES_HOST = False
        done.set()
        if audio_client:
            try:
                _vtbl_call(audio_client, 12, HRESULT, (), )  # Stop
            except Exception:  # noqa: BLE001
                pass
        for ptr in (cap, audio_client, device, dev_enum):
            _release(ptr)
        if mix_ptr:
            try:
                _ole32.CoTaskMemFree(mix_ptr)
            except Exception:  # noqa: BLE001
                pass
        _ole32.CoUninitialize()


def start_self_excluded_capture(stop_event: threading.Event,
                                 on_chunk: Callable[[np.ndarray], None],
                                 target_rate: int = 16000) -> bool:
    """开启"剔除宿主进程自身声音"的进程级环回采集。

    成功（已真正打开音频设备）返回 True —— 调用方**不要**再开旧 pyaudiowpatch
    整设备环回（否则重复采集）。失败/不支持返回 False —— 调用方回退整设备环回 + 闸门。
    """
    if not process_loopback_supported():
        return False
    # 本机已探明进程环回排除不可用（首探失败后缓存）：直接返回 False 走闸门兜底，
    # 不再每次启动都空等 2s。
    if _SELF_EXCLUDE_PROBED and not _SELF_EXCLUDE_WORKS:
        return False
    ready = threading.Event()
    done = threading.Event()
    t = threading.Thread(
        target=_capture_thread, args=(stop_event, on_chunk, target_rate, ready, done),
        name="self-excluded-loopback", daemon=True)
    t.start()
    # 等线程确认（成功设 ready；失败/异常在 finally 设 done）。最多等 2s，失败即回退，
    # 避免进程环回在本机不可用时拖慢采集启动。
    done.wait(timeout=2.0)
    return ready.is_set()
