# -*- coding: utf-8 -*-
"""桌宠位置的持久化（阶段 A4）。

为什么需要它：以前桌宠位置只存在 `api_pet._pet_pos`（内存里的一个 tuple），
**进程一退就没了** —— 用户每次开机都得重新把人拖回原位。

存什么：**不存绝对坐标**。绝对坐标在换显示器 / 改分辨率 / 拔副屏之后会把窗口
甩到屏幕外（用户看到的就是"人不见了"）。这里存「相对所在显示器工作区的比例
(rx, ry)」+ 存档时的工作区矩形，恢复时再映射回当前最接近的那块屏。

坐标空间：Windows 在 DPI 感知进程下到处返回**物理像素**，而 pywebview 的
create_window 与 Electron 的 BrowserWindow 都收**逻辑像素**。所以本模块对外
只讲逻辑像素，读写时统一乘/除 `screen_scale()`。

尺寸口径：统一用常量 `PET_W/PET_H`（与 `create_desktop_companion` 里写死的
420×540 一致），存和取都用同一组数，否则「工作区 − 窗口尺寸」做分母会把
几像素的口径差放大成几十像素的漂移（实测：存 424×544 取 420×540 → 偏 24px）。
"""
from __future__ import annotations

import ctypes as _ct
import json
import logging
import os
import threading
from ctypes import wintypes as _wt

logger = logging.getLogger("src.ui.pet_state")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_PATH = os.path.join(ROOT, "data", "pet_state.json")

# 桌宠窗的名义尺寸（逻辑像素）——存/取必须同源
PET_W = 420
PET_H = 540

_lock = threading.Lock()

# ctypes 不声明 argtypes 会按 C int 传参（AGENTS.md 第三条），句柄会被截断成 32 位。
_u32 = _ct.windll.user32
_gdi32 = _ct.windll.gdi32

_gdi32.CreateDCW.argtypes = [_wt.LPCWSTR, _wt.LPCWSTR, _wt.LPCWSTR, _ct.c_void_p]
_gdi32.CreateDCW.restype = _wt.HDC
_gdi32.GetDeviceCaps.argtypes = [_wt.HDC, _ct.c_int]
_gdi32.GetDeviceCaps.restype = _ct.c_int
_gdi32.DeleteDC.argtypes = [_wt.HDC]
_gdi32.DeleteDC.restype = _wt.BOOL

MONITORENUMPROC = _ct.WINFUNCTYPE(_ct.c_int, _wt.HMONITOR, _wt.HDC,
                                  _ct.POINTER(_wt.RECT), _wt.LPARAM)
_u32.EnumDisplayMonitors.argtypes = [_wt.HDC, _ct.POINTER(_wt.RECT),
                                     MONITORENUMPROC, _wt.LPARAM]
_u32.EnumDisplayMonitors.restype = _wt.BOOL
_u32.GetMonitorInfoW.argtypes = [_wt.HMONITOR, _ct.c_void_p]
_u32.GetMonitorInfoW.restype = _wt.BOOL
_u32.GetSystemMetrics.argtypes = [_ct.c_int]
_u32.GetSystemMetrics.restype = _ct.c_int


class _MONITORINFO(_ct.Structure):
    _fields_ = [("cbSize", _wt.DWORD), ("rcMonitor", _wt.RECT),
                ("rcWork", _wt.RECT), ("dwFlags", _wt.DWORD)]


def screen_scale() -> float:
    """当前主显示器的缩放比（物理/逻辑）。

    与 `create_desktop_companion` 同一套算法：GetSystemMetrics 与 GetDeviceCaps
    在进程 DPI 感知状态一致的前提下同进同退，相除才是逻辑分辨率。
    """
    try:
        hdc = _gdi32.CreateDCW("DISPLAY", None, None, None)
        try:
            dpi = _gdi32.GetDeviceCaps(hdc, 88) or 96   # LOGPIXELSX
        finally:
            _gdi32.DeleteDC(hdc)
        return max(dpi / 96.0, 1.0)
    except Exception as _e:  # noqa: BLE001
        logger.debug("[桌宠] 读取缩放失败（按 1.0 处理）：%s", _e)
        return 1.0


def logical_work_areas() -> list[tuple[int, int, int, int]]:
    """所有显示器的**工作区**（已换算成逻辑像素）。枚举失败时给一块兜底区。"""
    areas: list[tuple[int, int, int, int]] = []
    scale = screen_scale()

    def _cb(hmon, _hdc, _lprc, _lparam):
        try:
            mi = _MONITORINFO()
            mi.cbSize = _ct.sizeof(_MONITORINFO)
            if _u32.GetMonitorInfoW(hmon, _ct.byref(mi)):
                r = mi.rcWork
                areas.append((int(r.left / scale), int(r.top / scale),
                              int(r.right / scale), int(r.bottom / scale)))
        except Exception:  # noqa: BLE001
            pass
        return 1

    try:
        _u32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    except Exception as _e:  # noqa: BLE001
        logger.debug("[桌宠] 枚举显示器失败：%s", _e)

    if not areas:
        sw, sh = _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)
        areas = [(0, 0, int(sw / scale), int(sh / scale))]
    return areas


def _pick_area(areas, want_w: int, want_h: int):
    """挑一块工作区：能装下窗口的最大一块；装不下就取面积最大的那块。"""
    biggest = max(areas, key=lambda a: (a[2] - a[0]) * (a[3] - a[1]))
    if want_w <= 0 or want_h <= 0:
        return biggest
    fit = [a for a in areas
           if (a[2] - a[0]) >= want_w and (a[3] - a[1]) >= want_h]
    return max(fit, key=lambda a: (a[2] - a[0]) * (a[3] - a[1])) if fit else biggest


def _clamp(v: int, lo: int, hi: int) -> int:
    if hi < lo:
        return lo
    return lo if v < lo else (hi if v > hi else v)


def load() -> dict | None:
    """读存盘状态；文件不存在/损坏时返回 None（调用方按默认位置走）。"""
    try:
        # 解析失败不能让软件起不来，见下方 except
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        # 没有状态文件（首次运行/被清）→ 用默认位置
        return None
    except Exception as _e:  # noqa: BLE001
        logger.warning("[桌宠] 位置存档损坏，按默认位置处理：%s", _e)
        return None


def _write_ratio(lx: int, ly: int, area: tuple[int, int, int, int],
                 screen_count: int, w: int, h: int) -> bool:
    """归一化 + 原子落盘。area 为**逻辑像素**工作区。"""
    try:
        aw = max((area[2] - area[0]) - w, 1)
        ah = max((area[3] - area[1]) - h, 1)
        rx = (lx - area[0]) / aw
        ry = (ly - area[1]) / ah
        rx = 0.0 if rx < 0 else (1.0 if rx > 1 else rx)
        ry = 0.0 if ry < 0 else (1.0 if ry > 1 else ry)

        payload = {"rx": round(rx, 5), "ry": round(ry, 5),
                   "wa": {"left": area[0], "top": area[1],
                          "right": area[2], "bottom": area[3]},
                   "screen_count": screen_count}
        with _lock:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, STATE_PATH)   # 原子替换：写到一半被杀不会留下坏档
        logger.info("[桌宠] 位置已存盘：逻辑(%d,%d) → 比例(%.3f,%.3f)，屏数=%d",
                    lx, ly, rx, ry, screen_count)
        return True
    except Exception as _e:  # noqa: BLE001
        logger.warning("[桌宠] 位置存盘失败（不影响使用）：%s", _e)
        return False


def _locate_area(lx: int, ly: int, w: int, h: int):
    """窗口中心落在哪块屏（越界时取最近的一块）。"""
    areas = logical_work_areas()
    for a in areas:
        if a[0] <= lx + w // 2 < a[2] and a[1] <= ly + h // 2 < a[3]:
            return a, areas
    nearest = min(areas, key=lambda a: ((lx - a[0]) ** 2 + (ly - a[1]) ** 2))
    return nearest, areas


def save_logical(x: int, y: int, w: int = PET_W, h: int = PET_H) -> bool:
    """按**逻辑像素**存当前位置（内部归一化成工作区比例）。

    只在**拖动结束**时调用一次：拖拽过程中 `pet_move` 会被高频调用。
    """
    lx, ly = int(x), int(y)
    w = max(int(w), 1)
    h = max(int(h), 1)
    area, areas = _locate_area(lx, ly, w, h)
    return _write_ratio(lx, ly, area, len(areas), w, h)


def save_physical(x: int, y: int, w: int = 0, h: int = 0) -> bool:
    """按**物理像素**存（内部换算成逻辑像素再归一化）。

    归一化一律用名义尺寸 PET_W/PET_H，不用调用方传进来的实际矩形——
    实际矩形带边框（实测 424×544 vs 名义 420×540），口径不一致会让
    「工作区 − 窗口尺寸」这个分母把几像素的差放大成几十像素的漂移。
    传进来的 w/h 只用来判断窗口中心落在哪块屏。
    """
    scale = screen_scale()
    lx, ly = int(x / scale), int(y / scale)
    cx, cy = lx + PET_W // 2, ly + PET_H // 2
    areas = logical_work_areas()
    area = None
    for a in areas:
        if a[0] <= cx < a[2] and a[1] <= cy < a[3]:
            area = a
            break
    if area is None:
        area = min(areas, key=lambda a: ((cx - a[0]) ** 2 + (cy - a[1]) ** 2))
    return _write_ratio(lx, ly, area, len(areas), PET_W, PET_H)


def restore_logical(w: int = PET_W, h: int = PET_H,
                    default: tuple[int, int] | None = None) -> tuple[int, int]:
    """把存盘比例映射回**当前**屏幕的逻辑坐标；越界则夹回可视区。

    没有存档 / 存档损坏 / 字段缺失时返回 default（右下角）。
    """
    default = default or (0, 0)
    st = load()
    if not st:
        return int(default[0]), int(default[1])
    try:
        rx, ry = float(st.get("rx")), float(st.get("ry"))
    except (TypeError, ValueError):
        return int(default[0]), int(default[1])

    areas = logical_work_areas()
    area = None
    want_wa = st.get("wa")
    if isinstance(want_wa, dict) and all(k in want_wa for k in ("left", "top", "right", "bottom")):
        tw = int(want_wa["right"]) - int(want_wa["left"])
        th = int(want_wa["bottom"]) - int(want_wa["top"])
        if tw > 0 and th > 0:
            area = min(areas, key=lambda a: (abs((a[2] - a[0]) - tw)
                                              + abs((a[3] - a[1]) - th)))
    if area is None:
        area = _pick_area(areas, w, h)

    aw = max((area[2] - area[0]) - w, 0)
    ah = max((area[3] - area[1]) - h, 0)
    x = _clamp(area[0] + int(round(rx * aw)), area[0], max(area[0], area[2] - w))
    y = _clamp(area[1] + int(round(ry * ah)), area[1], max(area[1], area[3] - h))
    logger.info("[桌宠] 位置已恢复：比例(%.3f,%.3f) → 逻辑(%d,%d)", rx, ry, x, y)
    return x, y


__all__ = ["STATE_PATH", "PET_W", "PET_H", "screen_scale", "logical_work_areas",
           "load", "save_logical", "save_physical", "restore_logical"]
