"""PortAudio 共享访问层 —— 根治多线程并发 init/terminate 触发的原生段错误。

== 背景 ==
pyaudio 与 pyaudiowpatch 都封装**同一个 C 层 PortAudio 全局状态**。在 11 项目里，
麦克风采集、WASAPI 环回采集、游戏音效识别、云端双工播放/麦克风、UI 设备枚举
全都在不同线程里各自 ``PyAudio()`` 初始化、用完 ``terminate()`` 销毁。

当两条线程**同时**做 init 或**一条 init、另一条 terminate** 时，PortAudio 的
全局状态被踩坏，直接触发原生访问违规 ``0xC0000005``（exit -1073741819）。
这是 C 层崩溃，faulthandler/excepthook/CLR 守卫都拦不住，只能从源头避免。

== 对策（铁律）==
1. 全工程只创建一个 PyAudio 实例（单例），会话期间**绝不 terminate**
   （避免销毁别的线程正在用的流）。真正退出时由 atexit 统一收尾。
2. 所有 init/terminate/设备枚举/开流 都在本模块内、用全局锁 ``_pa_lock`` 串行化，
   杜绝并发 init/terminate。
3. 枚举默认 WASAPI 环回设备用安全方式，**绝不调用会堆损坏的**
   ``get_default_wasapi_loopback()``。

外部模块一律通过 ``get_shared_pyaudio()`` 取实例、通过 ``safe_find_loopback_device()``
枚举环回设备；需要自己 init/terminate 的（如 cloud_omni 播放器）也要用 ``pa_lock()``
串行化，并且**不要在会话期间 terminate**（只 close 流）。
"""
from __future__ import annotations

import atexit
import threading
from typing import Any, Optional

_pa_lock = threading.RLock()
_shared_pa: Any = None
_pa_mod: Any = None


def pa_lock() -> threading.RLock:
    """返回全局锁，供外部模块串行化自己的 PyAudio init/terminate。"""
    return _pa_lock


def get_shared_pyaudio(use_wpatch: bool = True) -> Any:
    """返回进程级共享 PyAudio 实例（pyaudiowpatch 优先，用于环回/枚举）。

    单例 + 全局锁，保证 Pa_Initialize 只发生一次且不与 terminate 并发。
    会话期间绝不 terminate；进程退出由 atexit 统一收尾。
    """
    global _shared_pa, _pa_mod
    with _pa_lock:
        if _shared_pa is None:
            if use_wpatch:
                import pyaudiowpatch as _mod  # type: ignore
            else:
                import pyaudio as _mod  # type: ignore
            _pa_mod = _mod
            _shared_pa = _mod.PyAudio()
            atexit.register(_shutdown)
        return _shared_pa


def pa_module() -> Any:
    """返回实际的 pyaudio/pyaudiowpatch 模块对象（用于取 paInt16 等**模块级常量**）。

    ⚠️【2026-10-02 实锤 bug】paInt16/paWASAPI 等常量定义在**模块**上，不在 PyAudio
    实例上。曾误写成 `pa_instance.paInt16` → AttributeError: 'PyAudio' object has
    no attribute 'paInt16' → 云端全双工麦克风打不开 → Qwen-Omni 秒断并疯狂重连 40 次。
    取常量一律走本函数，实例只用来 open()/枚举。
    """
    global _pa_mod
    with _pa_lock:
        if _pa_mod is None:
            get_shared_pyaudio()
        return _pa_mod


def pa_int16() -> int:
    """返回 paInt16 常量（16bit PCM），云端双工/播放开流用。"""
    return pa_module().paInt16


def _shutdown() -> None:
    global _shared_pa
    with _pa_lock:
        if _shared_pa is not None:
            try:
                _shared_pa.terminate()
            except Exception:  # noqa: BLE001
                pass
            _shared_pa = None


def safe_find_loopback_device() -> Optional[dict]:
    """安全枚举默认 WASAPI 环回设备；找不到返回 None。

    绝不调用危险的 ``get_default_wasapi_loopback()``（非法/虚拟输出设备上会触发
    C 层堆损坏 0xC0000374）；用共享实例、绝不 terminate。
    """
    pa = get_shared_pyaudio(use_wpatch=True)
    with _pa_lock:
        try:
            # ⚠️ paWASAPI 同样在模块上：曾写成 pa.paWASAPI → AttributeError 被下面
            # except 吞掉 → 永远"找不到环回设备"（静默失效，真凶难查）
            wasapi = pa.get_host_api_info_by_type(pa_module().paWASAPI)
            default_speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        except Exception:  # noqa: BLE001
            return None
        if default_speakers.get("isLoopbackDevice"):
            return default_speakers
        first_loopback: Optional[dict] = None
        try:
            for loopback in pa.get_loopback_device_info_generator():
                if not loopback.get("isLoopbackDevice"):
                    continue
                if default_speakers["name"] in loopback.get("name", ""):
                    return loopback
                if first_loopback is None:
                    first_loopback = loopback
        except Exception:  # noqa: BLE001
            pass
        return first_loopback
