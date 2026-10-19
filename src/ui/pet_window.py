# -*- coding: utf-8 -*-
"""桌宠窗口的 Win32 操作（自 webapp.py 抽出，2026-10-17）。

包含：句柄查找、置顶/取消置顶、置顶保活线程、WDA 隐身（防屏幕捕获）、
窗口样式收尾、去掉任务栏图标。全部只调 Win32，不碰 WebView2 对象。
状态统一走 ui_state.STATE（pet_win / pet_topmost / pet_keepalive / pet_capture_hidden）。
"""
from __future__ import annotations

import ctypes as _ct
import logging
import threading
import time
from ctypes import wintypes as _wt

from .ui_state import STATE

logger = logging.getLogger("src.ui.webapp")

_u32 = _ct.windll.user32

# 【2026-10-17 修·Win32 置顶从未生效的根因】ctypes 不声明 argtypes 时，
# 参数按 C int 传递 → `SetWindowPos(hwnd, -1/HWND_TOPMOST, ...)` 里的 -1
# 变成 32 位的 0xFFFFFFFF，而不是 64 位全 1 的伪句柄 → 调用失败，
# GetLastError=1400(ERROR_INVALID_WINDOW_HANDLE)。实测本项目的"Win32 置顶"
# 路径一直失败（只是被 except/返回值吞掉），实际靠 pywebview 的 on_top 兜着，
# 而 on_top 是跨线程写 WinForms 属性 → 切窗时卡死的隐患。
# 这里把用到的 user32 函数签名声明齐，句柄类参数统一走 _wt.HWND。
_u32.SetWindowPos.argtypes = [
    _wt.HWND, _wt.HWND, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_uint]
_u32.SetWindowPos.restype = _wt.BOOL
_u32.FindWindowW.argtypes = [_wt.LPCWSTR, _wt.LPCWSTR]
_u32.FindWindowW.restype = _wt.HWND
_u32.GetWindowRect.argtypes = [_wt.HWND, _ct.POINTER(_wt.RECT)]
_u32.GetWindowRect.restype = _wt.BOOL
_u32.GetWindowLongW.argtypes = [_wt.HWND, _ct.c_int]
_u32.GetWindowLongW.restype = _ct.c_long
_u32.SetWindowLongW.argtypes = [_wt.HWND, _ct.c_int, _ct.c_long]
_u32.SetWindowLongW.restype = _ct.c_long


def _hwnd_arg(v: int):
    """把 -1/-2 这类伪句柄包成 c_void_p，避免 64 位截断。"""
    return _wt.HWND(v & 0xFFFFFFFFFFFFFFFF if v < 0 else v)


def _clamp(v: int, lo: int, hi: int) -> int:
    """把 v 夹到 [lo, hi]；hi < lo 时退化为 lo（工作区比窗口还小的极端情况）。"""
    if hi < lo:
        return lo
    return lo if v < lo else (hi if v > hi else v)


class _POINT(_ct.Structure):
    _fields_ = [("x", _ct.c_long), ("y", _ct.c_long)]


__all__ = [
    "_pet_topmost_keepalive", "_pet_find_hwnd", "_pet_apply_topmost",
    "_pet_install_topmost_guard", "_pet_set_wda", "_pet_find_main_hwnd",
    "_pet_set_capture_hidden", "_finalize_pet_window", "_attach_pet_no_taskbar",
]


def _pet_topmost_keepalive():
    """后台保活：每 1s 重新钉最前（仅当 _PET_TOPMOST=True）。

    直接走 _pet_apply_topmost()：GUI 线程下用 window.on_top（锁定正确窗口），
    非 GUI 线程下自动回退 SetWindowPos（HWND 声明已修，线程安全）。不放子类、不碰
    WndProc，仅靠低频保活恒盖过音乐软件等其它 TopMost 窗口（≤1s 间隙人眼不可察）；
    取消置顶时设 _PET_KEEPALIVE_ON=False 让线程退出即解钉。
    """
    while STATE.pet_keepalive:
        try:
            if STATE.pet_topmost:
                _pet_apply_topmost()
        except Exception:  # noqa: BLE001
            pass
        import time as _t
        _t.sleep(1.0)


def _pet_find_hwnd():
    """按标题定位桌宠窗句柄（_finalize_pet_window 已验证此标题可靠可得句柄）。"""
    try:
        return _ct.windll.user32.FindWindowW(None, "达妮娅看板娘")
    except Exception:
        return 0


def _pet_apply_topmost():
    """按 STATE.pet_topmost 把桌宠窗设为最前/普通。

    【2026-10-17 修复·切窗卡死】原实现优先 `STATE.pet_win.on_top = ...`。
    该 setter 最终执行 `form.TopMost = ...`（pywebview winforms.py:1006），
    而 **WinForms 控件属性不允许跨线程访问**：置顶保活线程每秒调一次，
    与用户切窗（激活状态变化）叠加时容易卡死在跨线程封送上。
    现在改成：**永远优先纯 Win32 SetWindowPos**（线程安全、无 COM/WinForms 依赖），
    只有拿不到 HWND 时才退回 window.on_top。两条路效果等价，后者仅作兜底。
    """
    hwnd = _pet_find_hwnd()
    if hwnd:
        ins = _hwnd_arg(-1 if STATE.pet_topmost else -2)   # HWND_TOPMOST / HWND_NOTOPMOST
        ok = _u32.SetWindowPos(
            hwnd, ins, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE
        if ok:
            return True
        logger.warning("桌宠置顶 SetWindowPos 失败 hwnd=%s topmost=%s", hwnd, STATE.pet_topmost)
    if STATE.pet_win is not None:
        try:
            STATE.pet_win.on_top = STATE.pet_topmost
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("回退 pywebview on_top 也失败: %s", e)
    return False


def _pet_install_topmost_guard():
    """置顶保活：启动后台线程每 1s 重新 SetWindowPos TOPMOST（仅启动一次；子类方案已弃用）。

    不放子类、不碰 WndProc：避免 WebView2 宿主窗每消息进 Python 回调导致系统卡顿；
    仅靠低频保活即可恒盖过其它 TopMost 窗口（≤1s 间隙人眼不可察）。取消置顶时由
    pet_toggle_always_on_top 设 _PET_KEEPALIVE_ON=False 让线程退出，再一次性 NOTOPMOST 解钉。
    """
    if STATE.pet_keepalive:
        return
    STATE.pet_keepalive = True
    try:
        _ka = __import__("threading").Thread(target=_pet_topmost_keepalive, daemon=True)
        _ka.start()
        logger.info("桌宠置顶保活线程已启动")
    except Exception:  # noqa: BLE001
        STATE.pet_keepalive = False


def _pet_set_wda(hwnd, hidden: bool) -> bool:
    """对单个窗口设置 WDA_EXCLUDEFROMCAPTURE（透过亲和性）。"""
    aff = 0x00000011 if hidden else 0x00000000  # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
    ok = _u32.SetWindowDisplayAffinity(hwnd, aff)
    if not ok:
        logger.warning("SetWindowDisplayAffinity 失败 hwnd=%s hidden=%s", hwnd, hidden)
    return bool(ok)


def _pet_find_main_hwnd():
    """定位主窗口句柄（本进程拥有的、非桌宠的可见顶层窗口，取面积最大者）。"""
    try:
        import win32gui
        import win32process
        import os
        pid = os.getpid()
        cand = []
        # 两步式枚举（2026-09-05 修复）：回调内只做本地、无消息泵的 Win32 调用，
        # GetWindowText（本质 SendMessage，会泵送调用方消息队列）移到枚举结束后取，
        # 消除“SendMessage-in-EnumWindows”在快速切换时造成原生栈溢出/卡死的风险。
        raw: list[tuple] = []
        def _enum(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            try:
                _, wp = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:  # noqa: BLE001
                return
            if wp != pid:
                return
            try:
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
            except Exception:  # noqa: BLE001
                return
            if w <= 1 or h <= 1:
                return
            raw.append((hwnd, w * h))
        win32gui.EnumWindows(_enum, None)
        for hwnd, area in raw:
            try:
                t = win32gui.GetWindowText(hwnd)  # 枚举外取标题：避免 SendMessage-in-EnumWindows 重入
            except Exception:  # noqa: BLE001
                t = ""
            if t == "达妮娅看板娘":
                continue  # 跳过桌宠本身
            cand.append((area, hwnd))
        if cand:
            cand.sort(reverse=True)
            return cand[0][1]
    except Exception:  # noqa: BLE001
        pass
    return 0


def _pet_set_capture_hidden(hidden: bool) -> bool:
    """对桌宠窗 + 主窗口同时设 WDA_EXCLUDEFROMCAPTURE（双工推流帧/截图都"透过"）。

    效果：你屏幕上照常看到桌宠和软件，但任何屏幕捕获（双工实时推流帧、截图、录屏、
    屏幕共享/Zoom/Discord/OBS/Teams/Win+Shift+S/游戏栏）都抓不到它们——像直接透过了
    窗口看到后面的真实画面。这正是"双工推流帧里看不见桌宠和软件、直接透过"的需求。
    需 Windows 10 2004+。返回是否桌宠窗设置成功。
    """
    pet_hwnd = _pet_find_hwnd()
    main_hwnd = _pet_find_main_hwnd()
    pet_ok = _pet_set_wda(pet_hwnd, hidden) if pet_hwnd else False
    main_ok = _pet_set_wda(main_hwnd, hidden) if main_hwnd else False
    # 【2026-09-04 修】灰涂是否执行改由抓屏调用方按 WDA 实际生效数(applied)决定
    # （applied>0 帧已真透过→不灰涂；applied==0→灰涂兜底），不再在此覆盖
    # OWN_WINDOW_WDA（之前把它强制 False 导致灰涂永远跑、透明度被涂成灰块）。
    STATE.pet_capture_hidden = hidden
    logger.info("双工/截屏透过: pet=%s(%s) main=%s(%s) hidden=%s",
                bool(pet_hwnd), pet_ok, bool(main_hwnd), main_ok, hidden)
    return bool(pet_hwnd)


def _finalize_pet_window() -> None:
    """桌宠窗口收尾（窗口加载完成后调用）：
    1. 加 WS_EX_TOOLWINDOW：不出现在任务栏/Alt+Tab，像真正的桌面宠物；
    2. 用物理像素 SetWindowPos 固定到屏幕右下角——绕开 pywebview 把 create_window
       的 x/y 当逻辑像素再 ×scale 的换算（该换算在 DPI 感知进程下会把窗口推出屏幕，
       例：4K/150% 逻辑 3396 → 物理 5094 超出 3840）。兜底保证窗口一定在屏幕内。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "达妮娅看板娘")
        if not hwnd:
            logger.info("桌宠收尾：未找到窗口句柄")
            return
        # 1) 去任务栏
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_TOOLWINDOW:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
        # 1.5) 【2026-09-04 用户实测仍出现在任务栏预览】三连补强：
        #   ① 剥掉 WS_EX_APPWINDOW（pywebview 默认带它，与 TOOLWINDOW 同挂时预览仍露出）；
        #   ② 挂属主窗口（GWL_HWNDPARENT=主窗）——无属主的工具窗口在 Win11 预览分组
        #      仍可能被列出；挂了属主才算真正的"浮动小窗"；
        #   ③ WS_EX_NOACTIVATE——点击不抢焦点，纯悬浮。
        try:
            GWL_HWNDPARENT = -8
            WS_EX_APPWINDOW = 0x00000400
            WS_EX_NOACTIVATE = 0x08000000
            main_hwnd2 = _pet_find_main_hwnd()
            if main_hwnd2:
                user32.SetWindowLongW(hwnd, GWL_HWNDPARENT, main_hwnd2)
            style2 = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new2 = (style2 | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
            if new2 != style2:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new2)
            logger.info("桌宠窗属主/NOACTIVATE 已设置（owner=%s）", bool(main_hwnd2))
        except Exception:  # noqa: BLE001
            logger.warning("桌宠窗属主/样式补强失败", exc_info=True)
        # 2) 右下角定位（物理像素；进程已 DPI 感知 → GetSystemMetrics 为物理值）
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w_phys = rect.right - rect.left
        h_phys = rect.bottom - rect.top
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        # 【阶段 A4】优先回到用户上次拖到的位置（按工作区比例存盘，跨屏/改分辨率也成立）；
        # 没有存档才用右下角默认位。存/取用同一组名义尺寸，避免几像素口径差被
        # 「工作区 − 窗口尺寸」这个分母放大成几十像素漂移。
        try:
            from . import pet_state as _ps

            _scale = _ps.screen_scale() or 1.0
        except Exception:  # noqa: BLE001
            _scale = 1.0
        _lw = max(int(round(w_phys / _scale)), 1)
        _lh = max(int(round(h_phys / _scale)), 1)
        _def_x = max(0, int(round(sw / _scale)) - _lw - 24)
        _def_y = max(0, int(round(sh / _scale)) - _lh - 24)
        _lx, _ly = _def_x, _def_y
        try:
            # 还原必须和存盘用**同一组名义尺寸**（save_physical 用的就是 PET_W/PET_H）。
            # 这里曾传实际窗口尺寸 _lw/_lh（实测 424×544），分母差 4px，
            # 在右/下边缘会被放大成几像素偏移——正是本模块注释里警告过的坑。
            _lx, _ly = _ps.restore_logical(_ps.PET_W, _ps.PET_H, (_def_x, _def_y))
        except Exception:  # noqa: BLE001
            logger.debug("[桌宠] 位置恢复失败，用默认位", exc_info=True)
        x = _clamp(int(round(_lx * _scale)), 0, max(0, sw - w_phys))
        y = _clamp(int(round(_ly * _scale)), 0, max(0, sh - h_phys))
        # NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED（FRAMECHANGED 让 TOOLWINDOW 样式生效）
        user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x0020)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        logger.info("桌宠窗口已收尾：右下角(物理 %d,%d)，TOOLWINDOW=%s",
                    x, y, bool(style & WS_EX_TOOLWINDOW))
        # 3) 置顶（仅当 _PET_TOPMOST 为真才钉最前；默认不置顶 → 不装子类/不保活，
        #    避免 WebView2 宿主窗每消息都进 Python 子类回调造成卡顿，也避免"默认就置顶"）
        try:
            if STATE.pet_topmost:
                _pet_apply_topmost()
                _pet_install_topmost_guard()
        except Exception:  # noqa: BLE001
            pass
        # 4) 双工/截屏“透过”：默认【关闭】WDA_EXCLUDEFROMCAPTURE，
        #    即桌宠窗+主窗口对屏幕捕获【可见】——这样 UU 远程 / ToDesk / 腾讯会议 /
        #    屏幕共享 / OBS 等抓到的软件区域不再是黑屏，用户远程也能看页面。
        #    （双工 AI 不“看见自己”的需求改由 own_window.paint_own_windows 灰涂兜底，
        #     见 _pet_set_capture_hidden 内部 OWN_WINDOW_WDA=False 分支；功能等价。）
        #    若想隐藏自家窗口做干净推流，桌宠右键菜单「对屏幕捕获隐身」可临时开启。
        try:
            _pet_set_capture_hidden(False)
        except Exception:  # noqa: BLE001
            logger.debug("桌宠透过亲和性设置失败", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("桌宠窗口收尾失败", exc_info=True)


def _attach_pet_no_taskbar(win) -> None:
    """挂载桌宠窗口"去任务栏+右下角定位"：等窗口加载完成（或已加载则立即）应用。"""
    def _apply(_win=None):
        _finalize_pet_window()
    try:
        if win.events.loaded.is_set():
            _apply()
        else:
            win.events.loaded += _apply
    except Exception:  # noqa: BLE001
        _apply()
