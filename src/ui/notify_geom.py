# -*- coding: utf-8 -*-
"""通知浮窗的几何计算与滑入/滑出动画（自 webapp.py 抽出，2026-10-17）。

全部只调 Win32 API，不碰 WebView2 对象（可在后台线程安全执行）。
_u32 是本模块自己的 user32 句柄；滑动锁与状态仅本模块使用。
"""
from __future__ import annotations

import ctypes as _ct
import logging
import threading
import time

logger = logging.getLogger("src.ui.webapp")

_u32 = _ct.windll.user32
_notify_slide_state = [False]     # True=已滑入 / False=泊在右缘外
_notify_slide_lock = threading.Lock()

__all__ = [
    "_notify_geom", "_notify_set_pos", "_notify_rects_for_monitor",
    "_notify_current_monitor_rect", "_notify_show_in", "_notify_park_out",
    "_notify_slide",
]


def _notify_geom():
    """通知窗**物理像素**几何：(sw, sh, scale, w, h, x_in, y_in, x_out)。

    进程已 SetProcessDPIAware → GetSystemMetrics 返回**物理**像素；与桌宠窗"物理钉位"
    （6463 行）和启动钉位（6202 行）同源。所有返回值均为物理像素，直接喂 SetWindowPos，
    **不再除以 scale 也不乘 scale**（进程感知下 GetSystemMetrics 已是物理值，除/乘都会错）。
    窗口尺寸收紧到 400x176 逻辑（刚好包住卡片，与设计稿同宽、无黑边、比例不拉伸）。
    窗口始终露 2px 在屏内（DWM/Chromium 不判定为不可见 → 合成表面始终存活）。
    """
    _u = _ct.windll.user32
    _g = _ct.windll.gdi32
    sw = _u.GetSystemMetrics(0)
    sh = _u.GetSystemMetrics(1)
    hdc = _g.CreateDCW("DISPLAY", None, None, None)
    dpi = _g.GetDeviceCaps(hdc, 88) or 96
    _g.DeleteDC(hdc)
    scale = max(dpi / 96.0, 1.0)
    w = int(400 * scale)
    h = int(176 * scale)
    x_in = max(0, sw - w - int(24 * scale))
    y_in = int(24 * scale)
    x_out = max(0, sw - 2)     # 留 2px 在屏内，保证"可见"不触发渲染挂起
    return sw, sh, scale, w, h, x_in, y_in, x_out


def _notify_set_pos(x: int, y: int, w: int, h: int) -> bool:
    """把通知窗钉到指定**物理像素**位置（HWND_TOPMOST）。纯 Win32，任意线程可调。

    坐标即物理像素（进程已 DPI 感知，GetSystemMetrics 为物理值，与桌宠窗同款）。
    窗口刚创建时 FindWindowW 可能暂时找不到（WinForms 控件树未就绪）→ 重试最多 ~1s，
    避免窗口卡在 pywebview 默认的 (0,0) 左上角（"左上角黑块"回归根因）。
    """
    try:
        hw = None
        for _ in range(40):
            hw = _u32.FindWindowW(None, "达妮娅通知")
            if hw:
                break
            time.sleep(0.025)
        if not hw:
            return False
        # 0x0010 = NOACTIVATE：保持 TOPMOST（hw=-1 已置顶）且不偷焦点。
        _u32.SetWindowPos(hw, -1, int(x), int(y), int(w), int(h), 0x0010)
        return True
    except Exception:  # noqa: BLE001
        return False


def _notify_slide(showing: bool) -> None:
    """不透明模式的弹出/收回动画：窗口在屏内水平滑动（每帧一次 SetWindowPos）。

    关键点：窗口**自始至终都在屏幕内**（最窄时仍露 2px），所以 WebView2 的合成器
    从不进入"不可见"状态 —— 这是"滑入时必有帧"的保证，与 SW_HIDE/屏外停放那种
    "先不可见再出现"的做法有本质区别。
    本函数在后台线程执行（只调 Win32，不碰 WebView2 对象，符合线程铁律）。
    """
    with _notify_slide_lock:
        if _notify_slide_state[0] is showing:
            return
        _notify_slide_state[0] = showing
        try:
            _, _, _, w, h, x_in, y_in, x_out = _notify_geom()
            frames = 24                       # ~290ms @ ~35fps，配合缓动更顺滑
            # 缓动：滑入用 easeOutCubic（到位即稳），滑出用 easeInCubic（加速离场）
            for i in range(1, frames + 1):
                t = i / frames
                if showing:
                    e = 1 - (1 - t) ** 3
                    x = x_out + (x_in - x_out) * e
                else:
                    e = t ** 3
                    x = x_in + (x_out - x_in) * e
                _notify_set_pos(x, y_in, w, h)
                time.sleep(0.012)
            _notify_set_pos(x_in if showing else x_out, y_in, w, h)
            if not showing:
                # 滑出完成、窗口已在屏外 → 通知前端复位卡片（此时复位不可见，
                # 避免"先黑再飞"；也保证下次 show 能重新初始化）。
                try:
                    from . import mode_notify as _mn
                    _mn._push_cmd({"type": "reset"})
                except Exception:  # noqa: BLE001
                    pass
            logger.info("[通知浮窗] 模式O 已%s（x=%d）",
                        "滑入" if showing else "滑出", x_in if showing else x_out)
        except Exception as _e:  # noqa: BLE001
            logger.warning("[通知浮窗] 模式O 滑动失败: %s", _e)


def _notify_rects_for_monitor(rc):
    """给定显示器物理矩形 (left,top,right,bottom)，返回该屏上通知窗的
    (x_in, y_in, w, h, x_out) 物理像素。x_in=右上角可见位，x_out=右缘外只露 2px。
    """
    try:
        _g = _ct.windll.gdi32
        hdc = _g.CreateDCW("DISPLAY", None, None, None)
        dpi = _g.GetDeviceCaps(hdc, 88) or 96
        _g.DeleteDC(hdc)
    except Exception:  # noqa: BLE001
        dpi = 96
    scale = max(dpi / 96.0, 1.0)
    w = int(400 * scale)
    h = int(176 * scale)
    x_in = max(rc[0], rc[2] - w - int(24 * scale))
    y_in = rc[1] + int(24 * scale)
    x_out = max(rc[0], rc[2] - 2)   # 右缘外留 2px（仍在屏内 → WebView2 合成器存活）
    return x_in, y_in, w, h, x_out


def _notify_current_monitor_rect():
    """返回『当前前台窗口所在显示器』物理矩形；取不到则回退主屏。"""
    try:
        import win32api
        fg = win32api.GetForegroundWindow()
        if fg:
            hmon = win32api.MonitorFromWindow(fg, 2)   # MONITOR_DEFAULTTONEAREST
        else:
            hmon = win32api.MonitorFromPoint((0, 0), 2)
        if hmon:
            return win32api.GetMonitorInfo(hmon)["Monitor"]
    except Exception:  # noqa: BLE001
        pass
    return (0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1))


def _notify_show_in():
    """把通知窗泊到『前台所在显示器』右上角可见位（多屏跟到游戏/抖音屏）。

    两种模式都要执行：它是一次性静态停靠（把窗口钉到右上角 x_in），不是逐帧动画，
    与页面 CSS 卡片滑动不冲突（窗口已在 x_in 时 SetWindowPos 同位是 no-op，无跳变）。
    ⚠️ 透明模式(T·默认)下窗口创建后 pywebview 会默认停在 (0,0) 左上角（见 _notify_set_pos
    注释），而 T 模式的滑入/泊出都是 no-op → 若本函数也 no-op，窗口永远卡在左上角、
    卡片滑进来也在左上角小窗里 → 用户在右上角看不到弹窗（2026-09-05 04:46 实锤：
    日志自检 rect 恒为 (0,0,600,264)，弹窗"进去没弹"真凶）。故 T 模式也必须 dock 到 x_in。
    卡片显隐/滑入滑出仍纯靠页面 CSS，窗口位置固定不动（park_out 在 T 模式 no-op），
    契合用户"固定透明窗 + HTML 动画"的设计。
    """
    try:
        rc = _notify_current_monitor_rect()
        x_in, y_in, w, h, _ = _notify_rects_for_monitor(rc)
        _notify_set_pos(x_in, y_in, w, h)
    except Exception as _e:  # noqa: BLE001
        logger.warning("[通知浮窗] 泊入可见位失败: %s", _e)


def _notify_park_out():
    """空闲时把通知窗泊回『其当前所在显示器』右缘外（只露 2px），彻底不挡点击。

    透明模式(T·默认)下窗口固定右上角、卡片 CSS 隐藏即完事，本函数直接 no-op
    （不移动窗口），避免窗口在屏内/屏外来回跳导致"闪一下"的观感。
    【2026-09-05 定版·用户设计】窗口固定右上角、永不移动（两种模式都 no-op）；
    空闲透明 + WS_EX_TRANSPARENT 已保证"看不见且不挡点击"，无需泊屏外（泊屏外反而
    会让 WebView2 节流页面轮询 → show 命令送不到 → 弹窗不显示）。
    """
    return
    # 【2026-11-08 墓碑】原实现：取窗口当前所在显示器 → 算右缘外坐标 → _notify_set_pos()。
    # 上面那行裸 `return` 是 2026-09-05 定版时加的（「两种模式都 no-op」），加完之后
    # 这 17 行就再没执行过。本函数全仓**零调用点**（只有 __all__ 与 src/ui/webapp.py
    # 的导入提到它）。签名与 no-op 行为保留：notify_geom 模块仍被 webapp.py 使用
    # （_notify_show_in / _notify_current_monitor_rect / _notify_geom 都在用）。
