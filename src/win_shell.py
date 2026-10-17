"""Windows 壳层基础能力：DPI 感知、单实例、唤起已有窗口。

为什么单独成模块：这三件事都必须在**任何窗口创建之前**执行（顺序错了就无效），
所以入口集中在 `main()` 最前面几行，便于一眼确认顺序没被后续改动打乱。

实测背景（2026-10-18）：
- 本项目进程默认 DPI 感知值 = 0（不感知），GetSystemMetrics 返回被系统虚拟化的
  1707x1067（真实 2560x1600 / 150%）。不感知 = 整个窗口被 Windows 位图拉伸，
  高缩放下文字必糊——这是"看起来不像成品软件"最直接的来源。
- 全仓没有互斥体，双击两次就有两个进程、两只宠物、两套后端。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys

logger = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"

# 保持互斥体句柄存活：句柄被回收 = 锁失效（进程还活着但别人以为没在跑）
_MUTEX_HANDLES: list = []

ERROR_ALREADY_EXISTS = 183

PROCESS_DPI_AWARENESS_UNAWARE = 0
PROCESS_DPI_AWARENESS_SYSTEM = 1
PROCESS_DPI_AWARENESS_PER_MONITOR = 2


def enable_dpi_awareness() -> str:
    """把进程设为 Per-Monitor V2 DPI 感知。返回实际生效的方式（便于日志核对）。

    顺序：PMv2（Win10 1703+）→ 每显示器（Win8.1+）→ 系统级（Vista+）。
    必须早于任何窗口创建；创建窗口后再调用会被系统忽略。
    """
    if not _IS_WIN:
        return "非 Windows，跳过"

    # ① Per-Monitor V2：DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "Per-Monitor V2"
    except Exception as e:  # noqa: BLE001
        logger.debug("PMv2 不可用：%s", e)

    # ② 每显示器（Win8.1+）
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if int(shcore.SetProcessDpiAwareness(PROCESS_DPI_AWARENESS_PER_MONITOR)) == 0:
            return "Per-Monitor"
    except Exception as e:  # noqa: BLE001
        logger.debug("shcore DPI 不可用：%s", e)

    # ③ 系统级兜底（老系统）
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "System"
    except Exception as e:  # noqa: BLE001
        logger.warning("DPI 感知设置全部失败：%s", e)
    return "未生效"


def dpi_awareness() -> int:
    """读回当前进程 DPI 感知级别：0=不感知 1=系统 2=每显示器。"""
    if not _IS_WIN:
        return -1
    try:
        cur = ctypes.c_int()
        hr = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(cur))
        if int(hr) == 0:
            return int(cur.value)
    except Exception:  # noqa: BLE001
        pass
    try:
        return 1 if ctypes.windll.user32.IsProcessDPIAware() else 0
    except Exception:  # noqa: BLE001
        return -1


def acquire_single_instance(name: str = "DaniyaDesktopCompanion.v1") -> bool:
    """获取单实例锁。返回 True=本进程是唯一实例；False=已有实例在跑。

    用 Local\\ 前缀：按登录会话隔离，多用户同时登录时互不干扰（不用 Global\\）。
    """
    if not _IS_WIN:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        kernel32.CreateMutexW.restype = wt.HANDLE
        kernel32.GetLastError.restype = wt.DWORD
        handle = kernel32.CreateMutexW(None, True, "Local\\" + name)
        err = int(kernel32.GetLastError())
        if not handle:
            logger.warning("单实例互斥体创建失败（err=%s），按允许启动处理", err)
            return True
        _MUTEX_HANDLES.append(handle)
        if err == ERROR_ALREADY_EXISTS:
            logger.info("已有实例在运行（互斥体 %s 已存在）", name)
            return False
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("单实例检查异常，按允许启动处理：%s", e)
        return True


def activate_existing_window(titles: tuple[str, ...] = ("AI 桌面伴侣", "达妮娅")) -> bool:
    """把已在运行的实例窗口拉到前台。找到并激活返回 True。

    只做"还原 + 前置"，不发送自定义消息——避免新旧版本互相唤不醒时的隐性耦合。
    """
    if not _IS_WIN:
        return False
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
    user32.EnumWindows.restype = wt.BOOL
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.SetForegroundWindow.restype = wt.BOOL

    found: list[int] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if title and any(t in title for t in titles):
            found.append(int(hwnd) if hwnd else 0)
            return False  # 找到即停
        return True

    try:
        user32.EnumWindows(
            ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("枚举窗口失败：%s", e)
        return False

    if not found or not found[0]:
        return False
    hwnd = found[0]
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        logger.info("已唤起运行中的实例窗口 hwnd=%s", hwnd)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("唤起已有窗口失败：%s", e)
        return False


__all__ = [
    "enable_dpi_awareness", "dpi_awareness",
    "acquire_single_instance", "activate_existing_window",
]
