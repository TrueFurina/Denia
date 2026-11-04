"""全屏自动退场：只在**独占全屏**时把桌宠收起来，退出后还回去。

【2026-11-04 加·P0-2】不能复用 `ForegroundMonitor.is_foreground_fullscreen()`：
那个函数把 `IsZoomed`（窗口最大化）也算全屏，是给"专属陪伴切换"用的**沉浸**语义。
拿它做自动退场 ⇒ 用户一最大化浏览器 / IDE / QQ，她就消失。那比没有这功能更糟。

所以这里只认一件事：前台窗口的矩形**覆盖整块显示器 rcMonitor**（独占全屏游戏 /
F11 全屏视频）。最大化窗口因为有任务栏，底边差几十像素，不会命中。

两层：
  1. `strict_exclusive_fullscreen()` —— 纯几何判定，不起 Win32，可离线单测；
  2. `FullscreenGuard` —— 纯状态机：迟滞（防抖）、只还原"自己藏的"、用户手动藏了不接管。

Win32 探测封装在 `probe_exclusive_fullscreen()`，异常一律返回 False（探测失败不该让她消失）。

不进闸门：无（对应 tools/test_presence_controls.py）
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Optional

logger = logging.getLogger("src.system.fullscreen_guard")


def strict_exclusive_fullscreen(
    window_rect: tuple[int, int, int, int],
    monitor_rect: tuple[int, int, int, int],
    zoomed: bool,
    tol: int = 8,
) -> bool:
    """独占全屏判定（纯函数）。

    - `zoomed=True` 一律不算：最大化是"沉浸"，不是"独占全屏"。
    - 必须四边都贴住 **rcMonitor**（含任务栏区域）；只贴 rcWork 不算。
    - 不设"面积 ≥95%"这类兜底：最大化窗口高度通常就是整屏的 95%+，那个兜底
      会把最大化重新放进来，正是老实现的漏洞。
    """
    try:
        if zoomed:
            return False
        wl, wt, wr, wb = window_rect
        ml, mt, mr, mb = monitor_rect
        if mr <= ml or mb <= mt:          # 显示器矩形无效
            return False
        return (abs(wl - ml) <= tol and abs(wt - mt) <= tol
                and abs(wr - mr) <= tol and abs(wb - mb) <= tol)
    except Exception:  # noqa: BLE001
        return False


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def probe_exclusive_fullscreen() -> bool:
    """读一次前台窗口 + 所在显示器，交给纯判定。任何异常 → False。"""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        zoomed = False
        try:
            zoomed = bool(u.IsZoomed(hwnd))
        except Exception:  # noqa: BLE001
            pass
        rect = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        mh = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not mh:
            return False
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not u.GetMonitorInfoW(mh, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        return strict_exclusive_fullscreen(
            (rect.left, rect.top, rect.right, rect.bottom),
            (m.left, m.top, m.right, m.bottom),
            zoomed,
        )
    except Exception:  # noqa: BLE001
        return False


class FullscreenGuard:
    """迟滞状态机：连续 N 次全屏才藏，连续 M 次非全屏才还。

    `update(exclusive, pet_visible)` 返回 ``"hide"`` / ``"show"`` / ``None``，
    调用方负责真的去显隐桌宠。这样状态机不碰窗口、不碰线程，可离线全量测。

    三条不变量：
      1. 不还原别人藏的：用户手动隐藏后进入全屏，本轮不接管，退出时也不自动显示。
      2. 不重复隐藏：已经藏过就不再发 hide。
      3. 抖一下（切窗瞬间）不会让她闪一下：两边都要连续若干次采样。
    """

    def __init__(self, hide_after: int = 2, show_after: int = 3) -> None:
        self.hide_after = max(1, int(hide_after))
        self.show_after = max(1, int(show_after))
        self._full_streak = 0
        self._clear_streak = 0
        self._episode = False
        self._hidden_by_guard = False
        self._suppressed = False

    def update(self, exclusive: bool, pet_visible: bool) -> Optional[str]:
        if exclusive:
            self._clear_streak = 0
            self._full_streak += 1
            if self._full_streak < self.hide_after:
                return None
            if not self._episode:
                self._episode = True
                self._hidden_by_guard = False
                # 用户本来就藏了她 —— 本轮完全不接管，别在退出时把她翻出来
                self._suppressed = not pet_visible
            if self._suppressed:
                return None
            if pet_visible and not self._hidden_by_guard:
                self._hidden_by_guard = True
                return "hide"
            return None

        self._full_streak = 0
        self._clear_streak += 1
        if not self._episode:
            return None
        if self._clear_streak < self.show_after:
            return None
        action = None
        if self._hidden_by_guard and not pet_visible:
            action = "show"
        self._episode = False
        self._hidden_by_guard = False
        self._suppressed = False
        return action

    @property
    def hidden_by_guard(self) -> bool:
        return self._hidden_by_guard
