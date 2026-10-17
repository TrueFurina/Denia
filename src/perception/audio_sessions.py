# -*- coding: utf-8 -*-
"""系统音频会话探测：枚举"当前正在发声"的进程（纯 ctypes COM，无 comtypes 依赖）。

用途：双工推流判断声音来源。WASAPI 环回默认采整个系统的混音，没有"只采某个
软件声音"的常规 API；但 **Win10 2004(build 19041)+ 提供进程级环回排除**
（见 process_loopback.py：IMMDevice::Activate 传 AUDIOCLIENT_ACTIVATION_PARAMS
+ Exclude，可把宿主进程自身 TTS 声音从环回里剔除，用户照常能听到、只是不进模型）。
本模块用 IAudioMeterInformation 的峰值计量枚举每个音频会话（进程）的实时音量，
拿到"当前在出声的进程列表"，与前台进程比对即可识别后台出声 → 双工静音，做到
"只听最顶层的软件声音"的等效效果。

Windows only；非 Windows / 失败时返回空列表（安全降级，不静音）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

HRESULT = ctypes.c_long  # wintypes 未导出 HRESULT；它本就是 c_long
_ole32 = ctypes.windll.ole32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.wintypes.DWORD),
        ("Data2", ctypes.wintypes.WORD),
        ("Data3", ctypes.wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(s: str) -> _GUID:
    """'{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}' → _GUID（小端字节序）。"""
    h = s.strip("{}").replace("-", "")
    g = _GUID()
    g.Data1 = int(h[0:8], 16)
    g.Data2 = int(h[8:12], 16)
    g.Data3 = int(h[12:16], 16)
    for i in range(8):
        g.Data4[i] = int(h[16 + i * 2:18 + i * 2], 16)
    return g


CLSID_MMDeviceEnumerator = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioSessionManager2 = _guid("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = _guid("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
IID_IAudioMeterInformation = _guid("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")

# 通话/语音/会议类进程：这些进程只要在出声，就可能是语音通话/会议里对方的声音，
# 无论前台是谁都应让 AI 闭嘴（治"游戏前台 + 后台微信语音"NTR）。
# 注意：微信/QQ 等【日常聊天软件】不在名单里——它们出声≠通话（提示音/视频自动
# 播放也算出声），会让静音判定疯狂抖动（日志实锤 3 秒一切换，音频断裂胡言乱语）；
# 微信/QQ 通话改由前台窗口标题含"通话"判定。
VOICE_CALL_PROC_NAMES = frozenset({
    "wemeetapp.exe", "wemeet.exe", "voovmeeting.exe",
    "dingtalk.exe", "feishu.exe", "lark.exe", "zoom.exe",
    "discord.exe", "kook.exe", "teamspeak3.exe", "ts3client_win64.exe",
    "yy.exe", "yyexternal.exe",
})

# 峰值计量阈值：0~1，超过即视为"在出声"。音乐/语音/游戏音效的实时峰值通常
# 0.05+；阈值取 0.02 保持灵敏（有声音就算），避免漏判静音视频。
MIN_PEAK = 0.02


def _vtbl_call(obj, index: int, restype, argtypes: tuple, *args):
    """调用 COM 对象 vtable 第 index 个方法。obj 必须已按接口指针传入。"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.CFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj) -> None:
    """IUnknown::Release（释放 COM 接口指针）。"""
    if obj:
        try:
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.CFUNCTYPE(HRESULT, ctypes.c_void_p)(vtbl[2])(obj)
        except Exception:  # noqa: BLE001
            pass


def _proc_name_by_pid(pid: int) -> str:
    """pid → 可执行文件名小写；失败返回空串（如系统会话/无权限）。"""
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
    return ""


def active_audio_processes(min_peak: float = MIN_PEAK) -> list[dict]:
    """枚举默认渲染设备上当前正在发声的音频会话。

    返回 [{"pid": int, "name": str, "peak": float}]，按 peak 降序；
    只保留 peak >= min_peak 且 pid > 4（排除系统会话 System）的会话。
    COM 初始化失败 / 无会话 / 非 Windows → 返回 []（安全降级）。
    """
    if os.name != "nt":
        return []
    _ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    try:
        dev_enum = ctypes.c_void_p()
        hr = _ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator), None, 1,  # CLSCTX_INPROC_SERVER
            ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(dev_enum))
        if hr < 0 or not dev_enum:
            return []
        try:
            device = ctypes.c_void_p()
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eConsole=0)
            hr = _vtbl_call(
                dev_enum, 4, HRESULT,
                (ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)),
                0, 0, ctypes.byref(device))
            if hr < 0 or not device:
                return []
            try:
                mgr = ctypes.c_void_p()
                # IMMDevice::Activate(IAudioSessionManager2)
                hr = _vtbl_call(
                    device, 3, HRESULT,
                    (ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_void_p)),
                    ctypes.byref(IID_IAudioSessionManager2), 1, None, ctypes.byref(mgr))
                if hr < 0 or not mgr:
                    return []
                try:
                    sess_enum = ctypes.c_void_p()
                    # IAudioSessionManager2::GetSessionEnumerator
                    hr = _vtbl_call(
                        mgr, 5, HRESULT,
                        (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(sess_enum))
                    if hr < 0 or not sess_enum:
                        return []
                    try:
                        count = ctypes.c_int(0)
                        # IAudioSessionEnumerator::GetCount
                        if _vtbl_call(
                                sess_enum, 3, HRESULT,
                                (ctypes.POINTER(ctypes.c_int),), ctypes.byref(count)) < 0:
                            return []
                        out: list[dict] = []
                        for i in range(count.value):
                            try:
                                item = ctypes.c_void_p()
                                if _vtbl_call(
                                        sess_enum, 4, HRESULT,
                                        (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                                        i, ctypes.byref(item)) < 0 or not item:
                                    continue
                                try:
                                    # GetSessionEnumerator 返回的是 IAudioSessionControl
                                    # 基类（无 GetProcessId），先 QI 到 IAudioSessionControl2
                                    ctrl2 = ctypes.c_void_p()
                                    if _vtbl_call(
                                            item, 0, HRESULT,  # QueryInterface
                                            (ctypes.POINTER(_GUID),
                                             ctypes.POINTER(ctypes.c_void_p)),
                                            ctypes.byref(IID_IAudioSessionControl2),
                                            ctypes.byref(ctrl2)) < 0 or not ctrl2:
                                        continue  # 拿不到进程身份的会话跳过
                                    try:
                                        pid = ctypes.c_ulong(0)
                                        # IAudioSessionControl2::GetProcessId
                                        # （Control 基类有 9 个方法，Control2 从索引
                                        # 12 起：GetSessionIdentifier=12, ...,
                                        # GetProcessId=14 —— 用 12 会调到
                                        # GetSessionIdentifier 输出垃圾指针，实锤）
                                        if _vtbl_call(
                                                ctrl2, 14, HRESULT,
                                                (ctypes.POINTER(ctypes.c_ulong),),
                                                ctypes.byref(pid)) < 0 or pid.value <= 4:
                                            continue  # 系统会话（System 等）不参与判定
                                        meter = ctypes.c_void_p()
                                        if _vtbl_call(
                                                ctrl2, 0, HRESULT,  # QueryInterface
                                                (ctypes.POINTER(_GUID),
                                                 ctypes.POINTER(ctypes.c_void_p)),
                                                ctypes.byref(IID_IAudioMeterInformation),
                                                ctypes.byref(meter)) < 0 or not meter:
                                            continue
                                        try:
                                            peak = ctypes.c_float(0.0)
                                            # IAudioMeterInformation::GetPeakValue
                                            if _vtbl_call(
                                                    meter, 3, HRESULT,
                                                    (ctypes.POINTER(ctypes.c_float),),
                                                    ctypes.byref(peak)) >= 0 and peak.value >= min_peak:
                                                out.append({
                                                    "pid": pid.value,
                                                    "name": _proc_name_by_pid(pid.value),
                                                    "peak": round(peak.value, 3),
                                                })
                                        finally:
                                            _release(meter)
                                    finally:
                                        _release(ctrl2)
                                finally:
                                    _release(item)
                            except Exception:  # noqa: BLE001
                                logger.debug("枚举单个音频会话失败", exc_info=True)
                        out.sort(key=lambda s: s["peak"], reverse=True)
                        return out
                    finally:
                        _release(sess_enum)
                finally:
                    _release(mgr)
            finally:
                _release(device)
        finally:
            _release(dev_enum)
    except Exception:  # noqa: BLE001
        logger.debug("音频会话枚举失败", exc_info=True)
        return []
    finally:
        _ole32.CoUninitialize()
