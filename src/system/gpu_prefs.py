# -*- coding: utf-8 -*-
"""GPU 显存优化：把不需要独显的普通软件预设到核显（节能）运行。

原理：Windows 注册表 ``HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences``
记录「可执行文件名 -> GPU 偏好」（``GpuPreference=2;`` = 节能/核显）。
应用**下次启动**时，D3D/DXGI 运行时按此偏好选择 GPU——Windows 图形设置
（设置 -> 系统 -> 屏幕 -> 图形）的底层就是写这个注册表。

边界（务必知悉）：
- 只对「下次启动的应用」生效；**已运行的软件无法热迁移**（Windows 无公开 API
  能把运行中进程的 GPU 上下文搬到另一张卡，第三方软件也做不到）。
- 需要系统有可用核显（AMD/Intel iGPU 驱动已加载）。若核显没接任何显示器，
  Windows 可能忽略该偏好——建议至少把副屏接到主板接口再重启软件。
- 游戏/本软件默认就是独显（无需写高性能），只把普通软件设为核显，风险最低。
"""
from __future__ import annotations

import ctypes
import logging
import winreg
from ctypes import wintypes
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 注册表位置
_REG_KEY = r"Software\Microsoft\DirectX"
_REG_VALUE = "UserGpuPreferences"

# GpuPreference 取值（注册表口径）：1=高性能(独显) 2=节能(核显)
_ECONOMY_PREF = "2"

# 不需要独显、预设到核显的普通软件（按 exe 文件名）
ECONOMY_EXES = [
    "msedge.exe",        # Edge 浏览器
    "chrome.exe",        # Chrome
    "QQ.exe",            # QQ 桌面版
    "Weixin.exe",        # 微信
    "WeChatAppEx.exe",   # 微信小程序
    "douyin.exe",        # 抖音
    "wallpaper64.exe",   # 壁纸引擎
    "wallpaper32.exe",
    "SodaMusic.exe",     # 音乐播放器
    "MCHOSE HUB.exe",    # 鼠标驱动
    "WeGame.exe",        # WeGame 平台
    "TencentMeeting.exe",  # 腾讯会议（OCR 抓屏不受影响）
]


# ---------------------------------------------------------------------------
# DXGI 枚举：核显/独显 + 显示器连接
# ---------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


_IID_IDXGIFactory1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA, (0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
_HRESULT = ctypes.c_long


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", wintypes.UINT),
        ("DeviceId", wintypes.UINT),
        ("SubSysId", wintypes.UINT),
        ("Revision", wintypes.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", ctypes.c_longlong),
        ("Flags", wintypes.UINT),
    ]


def _vtable(obj: Any, idx: int) -> Any:
    return ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][idx]


def enum_gpus() -> list[dict]:
    """枚举 GPU 适配器（去重），返回 [{desc, vendor_id, dedicated_mb, outputs}]。"""
    result: list[dict] = []
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
        factory = ctypes.POINTER(ctypes.c_void_p)()
        hr = dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory))
        if hr != 0:
            return result
        enum_adapters1 = ctypes.WINFUNCTYPE(
            _HRESULT, ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        )(_vtable(factory, 12))
        idx = 0
        while True:
            adapter = ctypes.POINTER(ctypes.c_void_p)()
            if enum_adapters1(factory, idx, ctypes.byref(adapter)) != 0:
                break
            get_desc1 = ctypes.WINFUNCTYPE(
                _HRESULT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(_AdapterDesc1),
            )(_vtable(adapter, 10))
            desc = _AdapterDesc1()
            if get_desc1(adapter, ctypes.byref(desc)) == 0:
                # 输出数：EnumOutputs 数到失败为止
                outputs = 0
                enum_outputs = ctypes.WINFUNCTYPE(
                    _HRESULT, ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
                )(_vtable(adapter, 7))
                while True:
                    output = ctypes.POINTER(ctypes.c_void_p)()
                    if enum_outputs(adapter, outputs, ctypes.byref(output)) != 0:
                        break
                    outputs += 1
                result.append({
                    "desc": desc.Description,
                    "vendor_id": desc.VendorId,
                    "dedicated_mb": desc.DedicatedVideoMemory // (1024 * 1024),
                    "outputs": outputs,
                })
            idx += 1
    except Exception:  # noqa: BLE001
        logger.debug("DXGI 枚举 GPU 失败", exc_info=True)
    # 去重（DXGI 会重复枚举同一张卡）
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for g in result:
        key = (g["desc"].strip(), g["dedicated_mb"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(g)
    return uniq


def get_gpu_status() -> dict:
    """返回核显/独显状态：是否有核显、核显是否接了显示器。"""
    gpus = enum_gpus()
    igpu = None
    dgpu = None
    for g in gpus:
        desc = g["desc"].strip()
        vendor = g["vendor_id"]
        # 排除 Microsoft 基础渲染驱动 / WARP 虚拟设备
        if vendor == 0x1414 or g["dedicated_mb"] == 0 and "Basic Render" in desc:
            continue
        if vendor == 0x10DE:  # NVIDIA = 独显
            if dgpu is None:
                dgpu = g
        else:  # AMD / Intel：核显或副独显
            if g["dedicated_mb"] < 2048 and igpu is None:
                igpu = g
    return {
        "has_igpu": igpu is not None,
        "igpu_name": igpu["desc"].strip() if igpu else "",
        "igpu_outputs": igpu["outputs"] if igpu else 0,
        "dgpu_name": dgpu["desc"].strip() if dgpu else "",
        "dgpu_outputs": dgpu["outputs"] if dgpu else 0,
        "igpu_ready": bool(igpu and igpu["outputs"] > 0),
    }


# ---------------------------------------------------------------------------
# 注册表读写
# ---------------------------------------------------------------------------

def _read_prefs() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
            value, _ = winreg.QueryValueEx(k, _REG_VALUE)
            return str(value)
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def _write_prefs(text: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
        winreg.SetValueEx(k, _REG_VALUE, 0, winreg.REG_SZ, text)


def _set_exe_pref(exes: list[str], pref: str) -> list[str]:
    """把 exes 设为 pref，保留注册表里其他应用的现有设置。"""
    changed = []
    text = _read_prefs()
    entries: dict[str, str] = {}
    for part in text.split(";"):
        if "=" in part:
            name, val = part.split("=", 1)
            if name:
                entries[name] = val
    target = f"GpuPreference={pref}"
    for exe in exes:
        if entries.get(exe) != target:
            entries[exe] = target
            changed.append(exe)
    # 每个条目自带分号：a.exe=GpuPreference=2;b.exe=GpuPreference=2;
    _write_prefs("".join(f"{n}={v};" for n, v in entries.items()))
    return changed


def get_applied_prefs() -> dict:
    """读当前已应用的 GPU 偏好（exe -> pref）。"""
    text = _read_prefs()
    out: dict[str, str] = {}
    for part in text.split(";"):
        if "=" in part:
            name, val = part.split("=", 1)
            if name:
                out[name] = val
    return out


def apply_gpu_preferences(verbose: bool = False) -> dict:
    """把普通软件预设到核显（节能），返回执行结果摘要。"""
    status = get_gpu_status()
    if not status["has_igpu"]:
        msg = "未检测到核显，跳过 GPU 偏好预设"
        if verbose:
            logger.info(msg)
        return {"status": "no_igpu", "msg": msg, "changed": []}
    changed = _set_exe_pref(ECONOMY_EXES, _ECONOMY_PREF)
    if verbose or changed:
        logger.info("GPU 偏好预设完成：%d 个软件设为核显(节能)%s",
                    len(changed), "" if status["igpu_ready"] else "；注意核显未接显示器，可能需要把副屏接到主板才能生效")
    return {
        "status": "ok" if status["igpu_ready"] else "igpu_no_output",
        "igpu": status["igpu_name"],
        "igpu_ready": status["igpu_ready"],
        "changed": changed,
        "msg": ("已写入 GPU 偏好，重启对应软件后生效" if status["igpu_ready"]
                else "已写入 GPU 偏好，但核显未接显示器，需把至少一个显示器接到主板接口才能生效"),
    }


if __name__ == "__main__":
    import json

    st = get_gpu_status()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    res = apply_gpu_preferences(verbose=True)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print("当前全部偏好:", json.dumps(get_applied_prefs(), ensure_ascii=False, indent=2))
