"""屏幕级模式切换通知浮窗 —— WPF 原生渲染（单后端，无兜底）。

设计目标（用户 2026-09-05 / 09-06 明确诉求）：
  · 切换后从屏幕右上角滑入；
  · 弹出后由连接进度条驱动，进度跑完（connected/error）自动缩回；
  · 无论弹出还是缩回，整窗全程点击透过（不挡右上角）。
  · 外观 1:1 对齐 src/ui/web/redesign/ui_redesign_mode_notify.html 设计稿。
  · 【2026-09-06 通知栈】单卡槽 + 向上传送带交接（手机式通知栈）：
      - 首卡（云端/性能卡）从右侧滑入（in_right）；
      - 次卡（陪伴/普通卡）从下方上移、旧卡同步上移淡出（handoff 传送带）；
      - 单独缩回（retract）当前卡向上移出（out_up）——与交接旧卡同构；
      - show(key,...) 用 key 区分卡种（cloud/companion/normal/ready/default），
        不同 key 之间走交接动画，不互相覆盖。

为什么甩掉 WebView2 / GDI+：见项目 MEMORY.md。
本模块仅用 WPF 后端（Win10/11 桌面版 .NET Framework 自带，零额外依赖）。

布局结构（平滑抗锯齿圆角方案 + 单卡槽传送带）：
  Window(透明·点透·顶右锚点)
    └── Canvas(_slot, Width=_W)  ← 卡片卡槽（按 RenderTransform 做垂直传送带）
          ├── [main]  Grid(root)  当前卡（TranslateTransform.X 做水平滑入/滑出 + Opacity 淡入淡出）
          └── [ghost] Grid(root)  交接中的旧卡（仅 handoff 期间存在，结束后移除）

圆角渲染关键决策：
  · 不用 Border.CornerRadius（WPF 渲染器不做抗锯齿→像素阶梯/锯齿）
  · 改用 UIElement.Clip + RectangleGeometry（几何引擎完整抗锯齿≈CSS border-radius）
  · "边框"效果通过「outline矩形(全尺寸) + bg矩形(Margin=2)」叠加实现
"""
from __future__ import annotations

import sys
import ctypes
import logging
import math
import os
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# —— 先尝试加载 pythonnet（clr）——
try:
    import clr  # pywebview 已加载 pythonnet
except Exception as _e:  # noqa: BLE001
    logger.warning("[native_notify] 未找到 pythonnet(clr)，通知窗禁用: %s", _e)
    clr = None

# —— 尺寸（逻辑像素，WPF DPI 自动感知）——
_W = 460               # 卡片宽（对齐设计稿 460px，固定设计尺寸，缩放只走 ScaleTransform）
_WIN_PAD = 20           # 窗口相对卡片的左右留白（滑入/滑出缓冲，透明不可见）
# 【2026-10-02 修复·按钮行被裁】窗口/卡槽/Viewbox 的初始高度此前**硬编码 211.0**。
# 211 只是"无按钮卡"的实测高度；一旦卡片带决策按钮（_build_actions_row，行高约 34+上边距12
# ≈46px），卡片实际高度变成 ~257，但窗口仍是 211 → 底部按钮被视口裁掉（用户截图：
# 能看到按钮上边缘、文字被切一半）。_on_tick 的测高只同步"已渲染卡片"的高度，
# 而 Viewbox(Stretch=Uniform) 会把超出视口的内容等比缩掉，肉眼就是"按钮缺一截"。
# 修法：初始高度改为足够放下"标题+正文+进度条+按钮行"的常量 _CARD_DESIGN_H；
# _on_tick 仍会按实际卡片高度二次校正。
_CARD_DESIGN_H = 268.0
# 【2026-10-02 新增】hold_while_connecting（_hide_at=None，等 connected/error 事件）时
# 的**最长可见兜底**。原设计假设"那个事件一定会来"，实测有场景不来 → 卡片永久停在
# "正在切换…"。超过这个秒数无条件收起，宁可收早也不留一张僵尸卡在屏幕右上角。
# ⚠️【2026-10-02 修正】原值 45s **太短**：真实云端双工重连常常要 60~180s
# （本地 Comni 引擎加载就有 ~180s 超时），45s 会把"正在正常等待、进度条还在跑"的
# 连接卡提前收走 —— 用户反馈"该等进度条跑满的没等 UI 就直接隐藏了"。
# 提到 300s：本地模型挂载实测就要 35 秒（抖音场景日志实证 run_20261002_201218.log：
# ①"模型载入中" 20:12:39 → ②真就绪 20:13:14），叠加重连可能逼近 180s 超时，
# 210 只剩 30s 余量太危险。300s 覆盖最坏情况，同时仍保留"事件永不来"的兜底。
_MAX_STUCK_S = 300.0
_SETTLE_S = 2.5           # 进度跑完（connected/error）后停留多久再缩回（秒）
_MIN_VISIBLE_S = 8.0      # 最短完整展示时长（秒）
_ANIM_DUR = 0.5           # 弹入/弹出动画时长（秒，加长更优雅）
_FAKE_LOAD_S = 7.0        # 假进度条从 0 跑到 0.9 的时长（秒）；连接成功由 connected 补满到 1.0
_TEXT_POP_S = 0.45        # 连接成功后文字弹入（淡入+轻微上移）时长（秒）

# 设计令牌（对齐 design_tokens.css）
_CARD_BG = "#0D1018"       # --bg-2
_INK = "#E9EDF6"           # --ink
_INK_2 = "#AEB7C9"         # --ink-2
_TRACK = "#1B2230"         # 进度条轨道

# 场景配色（对齐设计稿 .v-* + 文案）：
#   (主色 hue, 次色 hue2, pill 底, kicker 小标, pill 文字, 图标类型)
_SCENE = {
    "game":  ("#FFB84D", "#A06BFF", "#3A2A10", "让路给游戏",   "省显卡模式", "game"),
    "back":  ("#2DD4FF", "#A06BFF", "#0E2F3F", "回到本机陪你", "本机模式",   "house"),
    "focus": ("#A06BFF", "#2DD4FF", "#231A3A", "专属陪伴",     "换个陪法",   "star"),
    "ok":    ("#36D399", "#2DD4FF", "#0E2E24", "准备好了",     "开始陪你",   "check"),
    "error": ("#E5484D", "#FFB84D", "#3A1416", "连接失败",     "云端不可用", "alert"),
    "default": ("#6C7A92", "#6C7A92", "#1A2030", "提示",        "通知",       "info"),
}
_ERR_HUE = "#E5484D"
_ERR_HUE2 = "#FFB84D"


# =====================================================================
# WPF 后端探测 + 类型导入
# =====================================================================
_HAVE_WPF = False
if clr is not None:
    try:
        _wpf_loaded = False
        try:
            import System
            from System.Reflection import Assembly
            for _d in (r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF',
                       r'C:\Windows\Microsoft.NET\Framework\v4.0.30319\WPF'):
                for _asm in ('PresentationFramework', 'PresentationCore', 'WindowsBase'):
                    _fp = os.path.join(_d, _asm + '.dll')
                    if os.path.exists(_fp):
                        try:
                            Assembly.LoadFrom(_fp)
                            _wpf_loaded = True
                        except Exception as _le:  # noqa: BLE001
                            # 【2026-10-17 降噪】WPF 加载失败（常见于只装了 32 位 .NET
                            # 或 Framework64\WPF 目录缺失的机器）只是"原生通知不可用"，
                            # 会自动降级到其它通知方式。旧实现每次通知都打完整异常 +
                            # Assembly 绑定日志，刷屏几十行红字，用户以为软件坏了。
                            # 现在只记一条简短警告（同进程内仅一次）。
                            _k = "_wpf_warn_" + _asm
                            if not getattr(sys.modules[__name__], _k, False):
                                setattr(sys.modules[__name__], _k, True)
                                logger.info("[native_notify] %s 未能加载（%s），"
                                            "原生通知降级；不影响聊天/语音", _asm,
                                            type(_le).__name__)
            for _xp in (r'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework'
                        r'\.NETFramework\v4.8\System.Xaml.dll',
                        r'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework'
                        r'\.NETFramework\v4.7.2\System.xaml.dll'):
                if os.path.exists(_xp):
                    try:
                        Assembly.LoadFrom(_xp)
                    except Exception:  # noqa: BLE001
                        pass
                    break
        except Exception as _e:  # noqa: BLE001
            logger.warning("[native_notify] 路径加载 WPF 失败，回退按名: %s", _e)
        if not _wpf_loaded:
            clr.AddReference("PresentationFramework")
            clr.AddReference("PresentationCore")
            clr.AddReference("WindowsBase")
            try:
                clr.AddReference("System.Windows.Forms")
            except Exception:  # noqa: BLE001
                pass
            try:
                clr.AddReference("System.Xaml")
            except Exception:  # noqa: BLE001
                pass
            try:
                clr.AddReference("System.Windows.Forms")
            except Exception:  # noqa: BLE001
                pass

        # pywebview(WinForms 平台) 通常已加载 System.Windows.Forms；但为稳妥，显式引用一次，
        # 保证 Screen / SystemEvents 在本模块内可用（否则跨语言 import 会静默失败→回退主屏）。
        try:
            clr.AddReference("System.Windows.Forms")
        except Exception:  # noqa: BLE001
            pass

        from System.Windows import (  # noqa: E402
            Window, WindowStyle, ResizeMode, WindowStartupLocation,
            Thickness, HorizontalAlignment, VerticalAlignment, FontWeights,
            SystemParameters, CornerRadius, Point, Rect, TextWrapping, SizeToContent,
            UIElement, Visibility,  # 【2026-10-02】决策卡隐藏进度条用
        )
        from System.Windows.Controls import (  # noqa: E402
            Border, StackPanel, TextBlock, Orientation, Canvas,
            Grid, Viewbox, Image,
            Button,  # 【2026-10-02】决策按钮行（notify actions）
        )
        from System.Windows.Shapes import Ellipse, Path, Rectangle  # noqa: E402
        from System.Windows.Media import (  # noqa: E402
            Brushes, Color, SolidColorBrush, LinearGradientBrush, GradientStop,
            FontFamily, Geometry, PenLineCap, PenLineJoin, Stretch,
            RectangleGeometry, EllipseGeometry, TranslateTransform, ScaleTransform,
        )
        from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption  # noqa: E402
        from System.Windows.Threading import DispatcherTimer  # noqa: E402
        from System.Threading import Thread, ApartmentState, ThreadStart  # noqa: E402
        from System import Action, TimeSpan  # noqa: E402
        _HAVE_WPF = True
    except Exception as _e:  # noqa: BLE001
        logger.warning("[native_notify] WPF 不可用（%s），通知窗禁用", _e)
        _HAVE_WPF = False


# =====================================================================
# 颜色 / 几何 辅助
# =====================================================================
def _hex_to_rgb(hexstr: str):
    h = hexstr.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _blend_hex(h1: str, h2: str, r: float) -> str:
    """把 h1 按比率 r 与 h2 混合（r=1 取 h1，r=0 取 h2）。"""
    a = _hex_to_rgb(h1)
    b = _hex_to_rgb(h2)
    return "#%02X%02X%02X" % (
        int(a[0] * r + b[0] * (1 - r)),
        int(a[1] * r + b[1] * (1 - r)),
        int(a[2] * r + b[2] * (1 - r)),
    )


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _touch_poll():
    """刷新 GUI 探活时间戳。"""
    try:
        from . import mode_notify as _mn
        _mn._last_poll[0] = time.time()
    except Exception:  # noqa: BLE001
        pass


def _system_dpi_scale():
    """返回『系统 DPI / 96』——即 WPF 在 System-DPI-Aware 下渲染逻辑单位所用的倍率。

    这是把『物理像素几何』转回『WPF 逻辑单位』的唯一正确除数：只有除以此值，
    WPF 渲染后才会精确还原到预期物理位置/尺寸。取错（如误得 1.0）会让窗口被当成
    100% DPI、÷1 后 WPF 再 ×真实 DPI → 物理暴涨、右缘溢出到副屏/出屏（主屏空窗）。

    【2026-09-06 二期·修 DPI 误判根因】上一版直接 GetDeviceCaps(CreateDCW("DISPLAY"),88)
    却没给 ctypes 声明 restype=指针；64 位进程下 HDC 被截成 32 位 → GetDeviceCaps 返 0
    → scale 误判 1.0（主屏 4K@150% 实测弹窗因此跑到副屏）。本版：优先 GetDpiForSystem()
    （Win10+ 直接给系统 DPI，与 WPF 倍率完全一致、最可靠）；失败再走带正确 restype 的
    GetDeviceCaps(HDC)；再不行比值法兜底。任何一步都保证 ≥1.0。
    """
    try:
        import ctypes
        _u = ctypes.windll.user32
        # 1) GetDpiForSystem：进程 System-DPI-Aware 时即主屏系统 DPI，与 WPF 倍率一致。
        try:
            _u.GetDpiForSystem.restype = ctypes.c_uint
            _u.GetDpiForSystem.argtypes = []
            _d = _u.GetDpiForSystem()
            if _d and _d >= 96:
                return max(float(_d) / 96.0, 1.0)
        except Exception:  # noqa: BLE001
            pass
        # 2) 回退：CreateDCW("DISPLAY")→GetDeviceCaps(LOGPIXELSX)。⚠️ 必须设 restype 为指针，
        #    否则 64 位 HDC 被截 32 位→GetDeviceCaps 返 0→scale 误判 1.0（旧坑）。
        try:
            _g = ctypes.windll.gdi32
            _g.CreateDCW.restype = ctypes.c_void_p
            _g.CreateDCW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p]
            _g.GetDeviceCaps.restype = ctypes.c_int
            _g.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
            _g.DeleteDC.restype = ctypes.c_int
            _g.DeleteDC.argtypes = [ctypes.c_void_p]
            _hdc = _g.CreateDCW("DISPLAY", None, None, None)
            if _hdc:
                _d = _g.GetDeviceCaps(_hdc, 88)  # LOGPIXELSX
                _g.DeleteDC(_hdc)
                if _d and _d >= 96:
                    return max(float(_d) / 96.0, 1.0)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def _primary_physical_workarea():
    """返回主屏**物理**工作区 (L, T, R, B) 物理像素。

    用 Win32 Monitor API 直取（MONITOR_DEFAULTTOPRIMARY → GetMonitorInfo.rcWork），
    不经过任何 .NET / WPF 的 DPI 逻辑换算，因此没有任何『物理 vs DIP』歧义。
    这是把弹窗『硬钉在主屏』的几何权威来源——SetWindowPos 拿到的物理坐标只会落在
    这块矩形内，物理上不可能溢出到副屏或虚拟屏外。
    """
    try:
        import ctypes
        _u = ctypes.windll.user32

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong),
                        ("rcMonitor", ctypes.c_long * 4),
                        ("rcWork", ctypes.c_long * 4),
                        ("dwFlags", ctypes.c_ulong)]

        _u.MonitorFromWindow.restype = ctypes.c_void_p
        _u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        _hmon = _u.MonitorFromWindow(0, 0x00000001)   # MONITOR_DEFAULTTOPRIMARY
        if not _hmon:
            _u.MonitorFromPoint.restype = ctypes.c_void_p
            _u.MonitorFromPoint.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_ulong]
            _hmon = _u.MonitorFromPoint(0, 0, 0x00000001)
        if _hmon:
            _mi = _MONITORINFO()
            _mi.cbSize = ctypes.sizeof(_MONITORINFO)
            _u.GetMonitorInfoW.restype = ctypes.c_int
            _u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            if _u.GetMonitorInfoW(_hmon, ctypes.byref(_mi)):
                _w = _mi.rcWork
                return (float(_w[0]), float(_w[1]), float(_w[2]), float(_w[3]))
    except Exception as _e:  # noqa: BLE001
        logger.debug("[native_notify] 主屏物理工作区(Monitor API)获取失败: %s", _e)
    # 兜底：GetSystemMetrics 物理宽高（进程 SetProcessDPIAware → 返回物理像素）
    try:
        import ctypes
        _u = ctypes.windll.user32
        _sw = _u.GetSystemMetrics(0)
        _sh = _u.GetSystemMetrics(1)
        return (0.0, 0.0, float(_sw), float(_sh))
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 1920.0, 1080.0)


# 通知窗锚定到『主屏工作区』。
# 【2026-09-06 终版·锚定主屏】用户确认：桌宠"推流/屏幕采集"的画面就是主屏
# （perception.screenshot.monitor_index 默认 1 = 主屏；双工截屏 _duplex_monitor_index
# 亦默认主屏）。之前用 FindWindow/FromHandle(本窗) 这类会变屏的判定：本窗停在
# 屏外右侧缓冲，被 Screen 误判到副屏 → 弹窗飞走/被裁。直接锚定 PrimaryScreen.WorkingArea
# 永远是用户认定的主屏，双屏也不会算飞，且坐标空间与 WPF 窗体一致（无需坐标换算）。
def _resolve_target_workarea():
    """返回主屏**物理**工作区 + 该屏 DPI scale：(left, top, right, bottom, dpi_scale)。

    【2026-09-06·三修·彻底根治"飘到副屏"】
    主屏物理工作区改用 Win32 Monitor API 直取（见 _primary_physical_workarea，无 DPI/逻辑
    歧义）。dpi_scale 仅用于把 WPF 内容尺寸（逻辑单位）换算成物理像素——**窗口落点不再依赖它**：
    落点由 _place() 经 SetWindowPos 直接以物理像素钉在主屏，WPF 的 DPI 乘算被完全绕过，
    因此即便我对 DPI 倍率估计有偏差，窗口也只可能落在『主屏物理工作区』内，物理上不可能
    溢出到副屏或虚拟屏外。这是用户"不管副屏、只看主屏"诉求的硬保险。
    """
    try:
        _pl, _pt, _pr, _pb = _primary_physical_workarea()
        _scr_scale = _system_dpi_scale()
        return (_pl, _pt, _pr, _pb, _scr_scale)
    except Exception as _e:  # noqa: BLE001
        logger.debug("[native_notify] 主屏几何获取失败: %s", _e)
    # 兜底：假设 1920×1080 @100%(scale=1.0)，物理==逻辑
    return (0.0, 0.0, 1920.0, 1080.0, 1.0)


# =====================================================================
# WPF 后端（单后端）
# =====================================================================
class _WpfBackend:
    """纯原生 WPF 通知窗（独立 STA 线程 + Dispatcher）。

    单卡槽 + 向上传送带交接：
      · 一次只显示一张卡（_main_root），交接时旧卡临时搬到 _ghost_root 同步上移淡出；
      · 首卡右侧滑入（in_right）；次卡向上交接（handoff）；单独缩回上移出（out_up）。
    圆角方案：RectangleGeometry Clip（几何引擎抗锯齿），
    不使用 Border.CornerRadius（WPF 渲染器不做抗锯齿→锯齿）。
    """

    def __init__(self):
        self._dispatcher = None
        self._win = None
        self._ready = threading.Event()

        # —— 卡槽（单卡槽 Canvas）——
        self._slot = None
        self._vb = None             # Viewbox：对卡片做统一缩放（Stretch=Uniform），保持比例不拉伸
        self._main_root = None      # 当前卡根（Grid）
        self._main_ctrl = None      # 当前卡控件元组
        self._ghost_root = None     # 交接中的旧卡（handoff 期间）
        self._ghost_ctrl = None
        self._controls = None       # 当前卡控件元组别名（_apply_scene 读取）
        self._slot_h = 120.0        # 卡片高度（首帧测量后更新，动画位移基准）
        self._applied_h = 0.0       # 已应用到 slot.Height 的高度（防每帧重复赋值）

        # 共享状态
        self._title = ""
        self._body = ""
        self._scene = "default"
        self._status = "正在切换…"
        self._progress = None
        self._connected = None
        self._error = None
        self._indeterminate = True
        self._visible = False
        self._hide_at = None
        self._active_key = "default"
        # 软件图标（已配软件专属陪伴时，徽章的小标识换成该软件 logo；保留底层 hue 双色）。
        # 值为临时 PNG 绝对路径；None 时用场景矢量图标。每次 show 重置，防旧软件图标泄漏。
        self._app_icon_path = None
        # 【2026-10-02 新增】决策按钮列表（None=纯告知卡，不渲染按钮行）。
        # 由 show(actions=...) 每次重置，防上一张卡的按钮泄漏到新卡。
        self._actions = None
        # 【2026-10-02 新增】本次可见起点（time.time()）；配合 _MAX_STUCK_S 做
        # "永久停住在正在切换"的看门狗。0=当前无卡可见。
        self._shown_at = 0.0
        # 【2026-10-02 新增】hold 卡的**分级等待上限**（秒）。0=沿用全局 _MAX_STUCK_S。
        # 陪伴切换卡用 45（换陪法几秒完成），真·重连卡用 300（本地模型挂载 35s+）。
        self._hold_cap = 0.0

        # —— 弹入/弹出动画（帧驱动，逐帧由 _on_tick 逼近目标）——
        # 滑入/滑出由 _slide_in()/_slide_out() 标记状态、_on_tick 每帧推进 reveal(0..1)，
        # 再写 Opacity + RenderTransform.X。逐属性 try/except + 看门狗/兜底线程兜底，
        # 即使某帧异常也只跳一帧、绝不冻整窗（此前 DoubleAnimation 在 pythonnet 下静默
        # 失败→瞬移无动画，已弃用）。
        self._sliding = False
        self._slide_target = 1.0    # 当前滑向：1.0=滑入到位，0.0=滑出屏外
        self._slide_start = 0.0     # 本次滑动开始时间戳（用于判断滑完停车）
        self._last_tick = 0.0       # 最近一次 _on_tick 时间戳（看门狗检测计时器是否停摆）
        self._diag_n = 0            # 心跳计数
        self._scale = 1.0           # 当前卡片逻辑缩放（仅用于 WPF 内容布局/高度推导）
        self._scr_scale = 1.0       # 系统 DPI / 96（仅用于 WPF 逻辑尺寸推导）
        # —— 物理几何（_place 用，直接 SetWindowPos 物理像素落窗，绕过 WPF 逻辑×DPI 不确定）——
        self._win_w_phys = float(_W + _WIN_PAD * 2)
        self._win_h_phys = 211.0
        self._x_in_phys = 0.0       # 主屏上的窗口物理左缘
        self._y_in_phys = 0.0       # 主屏上的窗口物理上缘
        self._design_h = 211.0      # 卡片设计高度（首帧测量后更新，用于窗口高度/比例）
        self._x_in = 0              # 兼容日志/旧引用（存物理 x）
        self._y_in = 0
        self._x_out = 0

        # —— 假进度条 + 连接文字弹入状态 ——
        self._fake = 0.0            # 假进度条当前值 0..0.9（连接成功由 connected 补满到 1.0）
        self._fake_start = 0.0      # 假进度条起始时间戳（show 时重置）
        self._text_popping = False  # 连接成功后文字是否正在弹入
        self._text_pop_t0 = 0.0     # 文字弹入起始时间戳

        self._rendered_scene = None
        self._cur_hue = _SCENE["default"][0]
        self._cur_grad = None

        # —— 定位（每次 show 都重算，避免进/出全屏游戏改了分辨率后窗口错位）——
        self._recompute_geom()

        t = Thread(ThreadStart(self._run))
        t.SetApartmentState(ApartmentState.STA)
        t.IsBackground = True
        t.Start()
        self._ready.wait(timeout=8.0)
        if self._dispatcher is None:
            raise RuntimeError("WPF 窗体在 8s 内未就绪")

    # ── 颜色构造 ──
    @staticmethod
    def _brush(hexstr: str, alpha: int = 255) -> "SolidColorBrush":
        r, g, b = _hex_to_rgb(hexstr)
        return SolidColorBrush(Color.FromArgb(alpha, r, g, b))

    def _grad_v(self, c1: str, c2: str, a1: int = 255, a2: int = 255) -> "LinearGradientBrush":
        g = LinearGradientBrush()
        g.StartPoint = Point(0, 0)
        g.EndPoint = Point(0, 1)
        r1, g1, b1 = _hex_to_rgb(c1)
        r2, g2, b2 = _hex_to_rgb(c2)
        g.GradientStops.Add(GradientStop(Color.FromArgb(a1, r1, g1, b1), 0.0))
        g.GradientStops.Add(GradientStop(Color.FromArgb(a2, r2, g2, b2), 1.0))
        return g

    def _grad_h(self, c1: str, c2: str) -> "LinearGradientBrush":
        """水平渐变（进度条填充用）。"""
        g = LinearGradientBrush()
        g.StartPoint = Point(0, 0)
        g.EndPoint = Point(1, 0)
        r1, g1, b1 = _hex_to_rgb(c1)
        r2, g2, b2 = _hex_to_rgb(c2)
        g.GradientStops.Add(GradientStop(Color.FromArgb(255, r1, g1, b1), 0.0))
        g.GradientStops.Add(GradientStop(Color.FromArgb(255, r2, g2, b2), 1.0))
        return g

    # ── 图标（对齐设计稿 SVG，用 WPF Shape 重画）──
    def _build_icon(self, ic: str, hue: str, hue2: str) -> "Canvas":
        """返回一个 24×24 Canvas，内含场景图标。"""
        cv = Canvas()
        cv.Width = 24
        cv.Height = 24
        if ic == "game":
            pad = Rectangle()
            pad.Width = 18
            pad.Height = 8
            pad.RadiusX = 4
            pad.RadiusY = 4
            pad.Fill = self._brush(hue)
            pad.SetValue(Canvas.LeftProperty, 3.0)
            pad.SetValue(Canvas.TopProperty, 9.0)
            cv.Children.Add(pad)
            cross_v = Path()
            cross_v.Data = Geometry.Parse("M7.2 13 H9.6 M8.4 11.8 V14.2")
            cross_v.Stroke = self._brush("#0D1018")
            cross_v.StrokeThickness = 1.5
            cross_v.StrokeLineCap = PenLineCap.Round
            cross_v.StrokeLineJoin = PenLineJoin.Round
            cv.Children.Add(cross_v)
            for _cx, _cy in ((15.4, 12.1), (17.6, 14.0)):
                dot = Ellipse()
                dot.Width = 2.1
                dot.Height = 2.1
                dot.Fill = self._brush("#0D1018")
                dot.SetValue(Canvas.LeftProperty, _cx - 1.05)
                dot.SetValue(Canvas.TopProperty, _cy - 1.05)
                cv.Children.Add(dot)
        elif ic == "star":
            s1 = Path()
            s1.Data = Geometry.Parse("M11 3.6 L12.5 8 L16.9 9.5 L12.5 11 L11 15.4 L9.5 11 L5.1 9.5 L9.5 8 Z")
            s1.Fill = self._brush(hue)
            cv.Children.Add(s1)
            s2 = Path()
            s2.Data = Geometry.Parse("M17.6 14.2 L18.3 16.2 L20.3 16.9 L18.3 17.6 L17.6 19.6 L16.9 17.6 L14.9 16.9 L16.9 16.2 Z")
            s2.Fill = self._brush(hue2)
            cv.Children.Add(s2)
        elif ic == "check":
            p = Path()
            p.Data = Geometry.Parse("M5.5 12.6 L9.5 16.6 L18.5 7.6")
            p.Stroke = self._brush("#36D399")
            p.StrokeThickness = 2.6
            p.StrokeLineCap = PenLineCap.Round
            p.StrokeLineJoin = PenLineJoin.Round
            p.Fill = None
            cv.Children.Add(p)
        elif ic == "house":
            roof = Path()
            roof.Data = Geometry.Parse("M12 4.2 L4.4 11 L19.6 11 Z")
            roof.Fill = self._brush(hue)
            cv.Children.Add(roof)
            body_r = Rectangle()
            body_r.Width = 10.8
            body_r.Height = 8.6
            body_r.RadiusX = 1
            body_r.RadiusY = 1
            body_r.Fill = self._brush(hue)
            body_r.SetValue(Canvas.LeftProperty, 6.6)
            body_r.SetValue(Canvas.TopProperty, 10.6)
            cv.Children.Add(body_r)
            door = Rectangle()
            door.Width = 3.2
            door.Height = 5.0
            door.RadiusX = 0.6
            door.RadiusY = 0.6
            door.Fill = self._brush("#0D1018")
            door.SetValue(Canvas.LeftProperty, 10.4)
            door.SetValue(Canvas.TopProperty, 14.2)
            cv.Children.Add(door)
        elif ic == "alert":
            # 警示三角（红）：云端连接失败/异常专用
            tri = Path()
            tri.Data = Geometry.Parse("M12 4 L20 19 H4 Z")
            tri.Fill = self._brush(hue)
            cv.Children.Add(tri)
            ex1 = Path()
            ex1.Data = Geometry.Parse("M12 9 V13.6")
            ex1.Stroke = self._brush("#0D1018")
            ex1.StrokeThickness = 2.0
            ex1.StrokeLineCap = PenLineCap.Round
            cv.Children.Add(ex1)
            ex2 = Path()
            ex2.Data = Geometry.Parse("M12 16 V16.8")
            ex2.Stroke = self._brush("#0D1018")
            ex2.StrokeThickness = 2.0
            ex2.StrokeLineCap = PenLineCap.Round
            cv.Children.Add(ex2)
        else:  # info — 圆点 + 底杠（i 形状）
            d = Ellipse()
            d.Width = 6
            d.Height = 6
            d.Fill = self._brush(hue)
            d.SetValue(Canvas.LeftProperty, 9.0)
            d.SetValue(Canvas.TopProperty, 6.5)
            cv.Children.Add(d)
            line = Path()
            line.Data = Geometry.Parse("M12 14 V17.5")
            line.Stroke = self._brush(hue)
            line.StrokeThickness = 2.0
            line.StrokeLineCap = PenLineCap.Round
            cv.Children.Add(line)
        return cv

    def _build_app_icon(self, path: str) -> "Canvas":
        """返回一个 24×24 Canvas，内含软件 logo（圆形裁切，居中），用于替换场景矢量图标。

        加载失败（路径不存在/不是图片）返回 None，调用方回退到场景矢量图标。
        """
        try:
            import os as _os
            if not _os.path.exists(path):
                return None
            bmp = BitmapImage()
            bmp.BeginInit()
            # Windows 绝对路径 → file:/// URI（正斜杠）
            bmp.UriSource = System.Uri("file:///" + path.replace("\\", "/"))
            bmp.CacheOption = BitmapCacheOption.OnLoad  # 立即解码，避免 Dispatcher 外访问异常
            bmp.EndInit()
            img = Image()
            img.Width = 22
            img.Height = 22
            img.Stretch = Stretch.Uniform
            img.Source = bmp
            # 圆形裁切，贴合徽章里圈（34px 内圈 → 半径 11 居中）
            eg = EllipseGeometry()
            eg.Center = Point(11.0, 11.0)
            eg.RadiusX = 11.0
            eg.RadiusY = 11.0
            img.Clip = eg
            cv = Canvas()
            cv.Width = 24
            cv.Height = 24
            cv.Children.Add(img)
            return cv
        except Exception as _ev:  # noqa: BLE001
            logger.warning("[native_notify] 软件图标加载失败，回退矢量图标: %s", _ev)
            return None

    # ── 控件树构建（平滑抗锯齿圆角方案）──
    def _build_actions_row(self, col, hue: str, hue2: str):
        """【2026-10-02 新增】在卡片正文下方构建一排决策按钮。

        没有 actions 时返回 None（不占高度）——对既有纯告知型弹窗**零影响**。
        按钮点击不直接执行业务，而是走 mode_notify.dispatch_action 上报给宿主
        （proactive）统一处理"执行 + 记忆用户选择"，保持本层为纯 UI。
        """
        try:
            _acts = list(getattr(self, "_actions", None) or [])
        except Exception:  # noqa: BLE001
            _acts = []
        if not _acts:
            return None
        try:
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 12, 0, 0)
            # ⭐⭐【2026-10-02 关键修复·按钮点了没反应】整窗设了
            # `win.IsHitTestVisible = False`（点穿透：不让通知卡挡住下面的窗口操作），
            # 但该属性是**继承到所有子元素**的 → Button 同样收不到鼠标点击 →
            # 用户看到按钮、点了毫无反应（日志里连"用户选择动作"都没有）。
            # WPF 的 `IsHitTestVisible` 会在子树上**向下覆盖但不向上**：在按钮行这里
            # 显式设回 True，就把"可点区域"限定在按钮行本身，卡片其余部分仍然点穿透。
            row.IsHitTestVisible = True
            for a in _acts[:3]:                      # 最多 3 个，超出会挤爆卡片
                try:
                    _id = str(a.get("id") or "").strip()
                    _label = str(a.get("label") or "").strip()
                    if not _id or not _label:
                        continue
                    _primary = bool(a.get("primary"))
                    btn = Button()
                    btn.Content = _label
                    # 双保险：按钮自身也显式可命中（父已设 True，这里再设一次防止
                    # 未来有人在中间插入容器又继承了 False）
                    btn.IsHitTestVisible = True
                    btn.FontSize = 12
                    btn.FontWeight = FontWeights.SemiBold
                    btn.Padding = Thickness(14, 6, 14, 6)
                    btn.Margin = Thickness(0, 0, 8, 0)
                    btn.Cursor = System.Windows.Input.Cursors.Hand
                    # 主按钮用场景色实心，次按钮用描边透明底
                    if _primary:
                        btn.Background = self._brush(hue)
                        btn.Foreground = self._brush("#0D1018")
                        btn.BorderThickness = Thickness(0)
                    else:
                        btn.Background = self._brush(_blend_hex(hue, _CARD_BG, 0.10))
                        btn.Foreground = self._brush(_INK)
                        btn.BorderThickness = Thickness(1)
                        btn.BorderBrush = self._brush(_blend_hex(hue, _CARD_BG, 0.55))
                    # 点击 → 上报（WPF 事件在 UI 线程，转线程后由 dispatch 处理业务）
                    def _make(_act_id):
                        def _click(sender, _e):
                            # 【2026-10-02】补日志：按钮是否真的被点到，只能靠这条
                            # "进入 _click" 来证明。之前点击链断在 IsHitTestVisible
                            # （按钮收不到事件），日志里连 dispatch 都没有 →
                            # 只能看到"点了没反应"，无从区分是没点到还是点了没处理。
                            logger.info("[native_notify] 按钮被点击 action=%s key=%s",
                                        _act_id, getattr(self, "_active_key", "") or "")
                            try:
                                from . import mode_notify as mn
                                mn.dispatch_action(_act_id, getattr(self, "_active_key", "") or "")
                            except Exception:  # noqa: BLE001
                                logger.exception("[native_notify] 动作上报失败 %s", _act_id)
                            # 点击即收起 + 立刻禁用所有按钮，给出"点到了"的确定反馈
                            try:
                                self._disable_actions()
                            except Exception:  # noqa: BLE001
                                pass
                            try:
                                self._schedule_hide(0.4)
                            except Exception:  # noqa: BLE001
                                pass
                        return _click
                    btn.Click += _make(_id)
                    row.Children.Add(btn)
                except Exception as _be:  # noqa: BLE001
                    logger.debug("[native_notify] 构建按钮失败: %s", _be)
            if len(row.Children) == 0:
                return None
            col.Children.Add(row)
            return row
        except Exception as _e:  # noqa: BLE001
            logger.warning("[native_notify] 决策按钮行构建失败（忽略）: %s", _e)
            return None

    def _disable_actions(self) -> None:
        """点击后立刻禁用全部决策按钮 —— 给"点到了"的确定反馈。

        【2026-10-02】用户反馈"点了选项没响应"。除修 IsHitTestVisible 外再加这层：
        点了就变灰 + 不可再点，避免用户以为没点上而反复点、或连点多次触发多个动作。
        """
        try:
            if not getattr(self, "_actions", None):
                return
            # 遍历视觉树里的 Button 全部禁用
            def _walk(el):
                try:
                    for ch in list(el.Children):
                        if isinstance(ch, Button):
                            ch.IsEnabled = False
                            ch.Opacity = 0.45
                            ch.Cursor = System.Windows.Input.Cursors.Arrow
                        _walk(ch)
                except Exception:  # noqa: BLE001
                    pass
            if self._main_root is not None:
                _walk(self._main_root)
        except Exception:  # noqa: BLE001
            logger.debug("[native_notify] 禁用按钮失败（忽略）", exc_info=True)

    def _schedule_hide(self, delay_s: float) -> None:
        """点击按钮后按延迟收起卡片（复用既有 retract 路径，不引入新的动画状态）。"""
        try:
            t = threading.Timer(max(0.1, float(delay_s)), self.retract)
            t.daemon = True
            t.start()
        except Exception:  # noqa: BLE001
            logger.debug("[native_notify] 收起定时器启动失败", exc_info=True)

    def _build_content(self, scene: str = "default"):
        """
        布局结构（与 HTML DOM 对齐，圆角用 RectangleGeometry Clip 实现）：

          Grid(root, Width=_W)
          ├── Rectangle(outline)   描边色 hue（全尺寸）
          ├── Rectangle(bg)        底色 #0D1018（Margin=2 → 露出 2px outline = 边框）
          ├── Grid(content)        实际内容（Margin=2）
          │   ├── Rectangle       左侧渐变脊 (::after)
          │   ├── Rectangle       顶部受光微晕 (::before)
          │   ├── StackPanel(row) → [Grid(badge) → StackPanel(col)]
          │   └── Border(pill)     右上胶囊
          └── [首帧后] root.Clip = RectangleGeometry(RadiusX/Y=18) 平滑裁剪

        返回：(root, controls_tuple)
        """
        hue = _SCENE["default"][0]
        hue2 = _SCENE["default"][1]

        # ═══════════════════════════════════════════════════════
        #  根容器（后续会被 RectangleGeometry 裁剪成平滑圆角）
        # ═══════════════════════════════════════════════════════
        root = Grid()
        root.Width = _W

        # 【Layer 0】描边色矩形（全尺寸）—— 充当"边框"
        outline = Rectangle()
        outline.Fill = self._brush(hue)
        root.Children.Add(outline)

        # 【Layer 1】卡片底色（Margin=2 → 四周露出 2px 描边色 = 边框效果）
        bg = Rectangle()
        bg.Fill = self._brush(_CARD_BG)
        bg.Margin = Thickness(2)
        root.Children.Add(bg)

        # 【Layer 2】内容容器（Margin=2，在底色之上）
        content = Grid()
        content.Margin = Thickness(2)

        # ① 左侧渐变脊（hue→hue2）— 对应 CSS ::after
        spine = Rectangle()
        spine.Width = 4
        spine.HorizontalAlignment = HorizontalAlignment.Left
        spine.Margin = Thickness(0, 14, 0, 14)
        spine.RadiusX = 2
        spine.RadiusY = 2
        spine.Fill = self._grad_v(hue, hue2)
        content.Children.Add(spine)

        # ② 顶部受光微晕 — 对应 CSS ::before
        glow = Rectangle()
        glow.Height = 46
        glow.VerticalAlignment = VerticalAlignment.Top
        _glow_c = _blend_hex(hue, _CARD_BG, 0.13)
        glow.Fill = self._grad_v(_glow_c, "#00000000", 255, 0)
        content.Children.Add(glow)

        # ③ 内容行 — 对应 HTML .row { display:flex }
        row = StackPanel()
        row.Orientation = Orientation.Horizontal
        row.Margin = Thickness(20, 20, 20, 20)

        # ── 徽章 (.badge): 38px 圆形 ──
        badge = Grid()
        badge.Width = 38
        badge.Height = 38
        badge.Margin = Thickness(0, 4, 0, 0)

        outer = Ellipse()
        outer.Width = 38
        outer.Height = 38
        outer.Fill = self._brush(hue)
        badge.Children.Add(outer)

        inner = Ellipse()
        inner.Width = 34
        inner.Height = 34
        inner.Margin = Thickness(2)
        inner.Fill = self._brush(_blend_hex(hue, _CARD_BG, 0.14))
        badge.Children.Add(inner)

        # 图标：Viewbox 包裹 24×24 Canvas → 缩放到 20×20
        # 软件专属陪伴时优先用软件 logo（保留底层 hue 双色），否则用场景矢量图标。
        _icon_name = _SCENE.get(scene, _SCENE["default"])[5]
        if self._app_icon_path:
            _app_cv = self._build_app_icon(self._app_icon_path)
            icon_canvas = _app_cv if _app_cv is not None else self._build_icon(
                _icon_name, _SCENE["default"][0], _SCENE["default"][1])
        else:
            icon_canvas = self._build_icon(
                _icon_name, _SCENE["default"][0], _SCENE["default"][1])
        icon_host = None
        try:
            icon_host = Viewbox()
            icon_host.Width = 20
            icon_host.Height = 20
            icon_host.Stretch = Stretch.Uniform
            icon_host.Child = icon_canvas
            badge.Children.Add(icon_host)
        except Exception as _ev:  # noqa: BLE001
            logger.warning("[native_notify] Viewbox 失败，直接挂载图标: %s", _ev)
            icon_canvas.SetValue(Canvas.LeftProperty, 7.0)
            icon_canvas.SetValue(Canvas.TopProperty, 7.0)
            badge.Children.Add(icon_canvas)
            icon_host = icon_canvas

        row.Children.Add(badge)

        # ── 文字列 (.col) ──
        col = StackPanel()
        col.Orientation = Orientation.Vertical
        col.Margin = Thickness(14, 0, 0, 0)
        # 【2026-09-06 修复·文字截断+卡片塌缩】col 是垂直 StackPanel，外层 row 又是
        # 水平 StackPanel——水平 StackPanel 不会约束子元素宽度，导致 col 拿到"无限宽"，
        # 内部 body 的 TextWrapping=Wrap 永不触发 → 正文变超长单行被 460px 卡片裁掉
        # （截断），且卡片高度塌缩成一条薄带（像静态图片）、进度条被挤歪（像死了）。
        # 显式约束 col 宽度 = 卡宽 - row边距(40) - 徽章(38) - col左边距(14) - 安全余量(2)。
        col.Width = float(_W - 40 - 38 - 14 - 2)  # = 366

        kicker = TextBlock()
        kicker.FontSize = 11
        kicker.FontWeight = FontWeights.Bold
        kicker.Foreground = self._brush(hue)
        kicker.Margin = Thickness(0, 0, 0, 5)
        col.Children.Add(kicker)

        title = TextBlock()
        title.FontSize = 16
        title.FontWeight = FontWeights.Bold
        title.Foreground = self._brush(_INK)
        title.Margin = Thickness(0, 0, 0, 9)
        col.Children.Add(title)

        body = TextBlock()
        body.FontSize = 12.5
        body.Foreground = self._brush(_INK_2)
        body.TextWrapping = TextWrapping.Wrap
        body.Margin = Thickness(0, 0, 0, 15)
        col.Children.Add(body)

        status = TextBlock()
        status.FontSize = 12
        status.FontWeight = FontWeights.Bold
        status.Foreground = self._brush(hue)
        status.Margin = Thickness(0, 0, 0, 7)
        # 连接成功文字弹入用：独立的平移变换 + 默认不透明度（弹入时由 _on_tick 从 0 渐入）
        status.RenderTransform = TranslateTransform()
        status.Opacity = 1.0
        col.Children.Add(status)

        # ── 自定义进度条（对齐 HTML .bar > i）──
        bar_track = Border()
        bar_track.Height = 7
        bar_track.Background = self._brush(_TRACK)
        bar_track.CornerRadius = CornerRadius(4)
        bar_track.ClipToBounds = True
        bar_fill = Rectangle()
        bar_fill.Height = 7
        bar_fill.Width = 0.0
        bar_fill.VerticalAlignment = VerticalAlignment.Stretch
        bar_fill.Fill = self._grad_h(hue, hue2)
        bar_track.Child = bar_fill
        # 【2026-10-02 修复·决策卡不该画进度条】进度条/状态文字表达的是"等连接结果"，
        # 而决策卡等的是"等用户点击"——画一条永远跑不满的进度条，用户看到的就是
        # 「这卡怎么卡在中间不动了」，反而像坏了（用户实锤：原本该跑满的进度没等 UI
        # 就没了 / 决策卡的进度条永远不动）。
        # 决策卡改为**不显示进度条**，状态文字直接写"等你选一个"（下面覆盖）。
        if not (getattr(self, "_actions", None) or []):
            col.Children.Add(bar_track)
        else:
            bar_track.Visibility = Visibility.Collapsed

        # 【2026-10-02 新增·决策按钮行】让用户能表达意愿，而不只是被动接收通知。
        # 无 actions 时这一行不加入（高度为 0），对既有纯告知型弹窗零影响。
        btn_host = self._build_actions_row(col, hue, hue2)

        row.Children.Add(col)
        content.Children.Add(row)

        # ④ 右上 pill 胶囊（.pill）
        pill = Border()
        pill.CornerRadius = CornerRadius(999)
        pill.BorderThickness = Thickness(1)
        pill.Background = self._brush(_SCENE["default"][2])
        pill.BorderBrush = self._brush(hue)
        pill.Padding = Thickness(12, 5, 12, 5)
        pill.HorizontalAlignment = HorizontalAlignment.Right
        pill.VerticalAlignment = VerticalAlignment.Top
        pill.Margin = Thickness(0, 18, 20, 0)
        pill_text = TextBlock()
        pill_text.FontSize = 11
        pill_text.FontWeight = FontWeights.Bold
        pill_text.Foreground = self._brush(hue)
        pill.Child = pill_text
        content.Children.Add(pill)

        root.Children.Add(content)

        # 控件元组（_apply_scene / _on_tick 解包顺序必须一致）
        ctrl = (title, body, status, bar_fill, root, outline, bg, content,
                spine, glow, outer, inner, kicker, pill, pill_text, icon_host, bar_track)
        return root, ctrl

    def _apply_scene(self, scene: str):
        """切换场景配色/文案/图标（不重建控件树）。"""
        hue, hue2, pillbg, kicker_txt, pill_txt, ic = _SCENE.get(scene, _SCENE["default"])
        self._cur_hue = hue
        self._cur_grad = self._grad_h(hue, hue2)
        (title, body, status, bar_fill, root, outline, bg, content,
         spine, glow, outer, inner, kicker, pill, pill_text, icon_host, bar_track) = self._controls
        # 描边色 + 徽章外圈
        outline.Fill = self._brush(hue)
        outer.Fill = self._brush(hue)
        inner.Fill = self._brush(_blend_hex(hue, _CARD_BG, 0.14))
        # 渐变脊 + 微晕
        spine.Fill = self._grad_v(hue, hue2)
        _glow_c = _blend_hex(hue, _CARD_BG, 0.13)
        glow.Fill = self._grad_v(_glow_c, "#00000000", 255, 0)
        # 文案
        kicker.Text = kicker_txt
        kicker.Foreground = self._brush(hue)
        pill_text.Text = pill_txt
        pill_text.Foreground = self._brush(hue)
        pill.BorderBrush = self._brush(hue)
        pill.Background = self._brush(pillbg)
        # 替换图标：软件专属陪伴优先用软件 logo（保留底层 hue 双色），否则场景矢量图标
        if icon_host is not None:
            if self._app_icon_path:
                _app_cv = self._build_app_icon(self._app_icon_path)
                new_icon = _app_cv if _app_cv is not None else self._build_icon(ic, hue, hue2)
            else:
                new_icon = self._build_icon(ic, hue, hue2)
            try:
                if isinstance(icon_host, Viewbox):
                    icon_host.Child = new_icon
                else:
                    parent_grid = icon_host.Parent
                    if parent_grid is not None:
                        idx = parent_grid.Children.IndexOf(icon_host)
                        if idx >= 0:
                            parent_grid.Children.RemoveAt(idx)
                            new_icon.SetValue(Canvas.LeftProperty, 7.0)
                            new_icon.SetValue(Canvas.TopProperty, 7.0)
                            parent_grid.Children.Add(new_icon)
            except Exception as _ev:  # noqa: BLE001
                logger.warning("[native_notify] 图标替换失败: %s", _ev)
        self._rendered_scene = scene

    def _build_into_main(self, title: str, body: str, scene: str) -> None:
        """把一张新卡构建进主卡槽（_main_root）。旧主卡若仍在（未在 ghost 中）先移出卡槽。"""
        if self._main_root is not None and self._main_root is not self._ghost_root:
            try:
                self._slot.Children.Remove(self._main_root)
            except Exception:  # noqa: BLE001
                pass
        root, ctrl = self._build_content(scene)
        # 【修复·2026-09-06】必须先置 _controls，再调 _apply_scene（其内部解包
        # self._controls）—— 否则首卡构建时 self._controls 仍为 None → 解包抛
        # TypeError → 异常跑在 Dispatcher 线程且未被接住 → Dispatcher.Run 退出
        # → WPF 线程死掉 → 之后所有 show 都调已死 dispatcher → 弹窗永久不出现。
        self._controls = ctrl
        self._main_ctrl = ctrl
        self._main_root = root
        self._apply_scene(scene)
        self._title = title or ""
        self._body = body or ""
        self._scene = scene
        # 卡片入场起始态：先置于右侧屏外 + 透明，再由 show() 调 _slide_in() 原生滑入。
        root.RenderTransform = TranslateTransform()
        root.RenderTransform.X = float(_W + _WIN_PAD)
        root.Opacity = 0.0
        try:
            root._clip_done = False  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        # 挂载到 Viewbox（统一缩放容器）。⚠️ Viewbox 是 Decorator，只能放【一个】
        # Child（单数），没有 Children 集合——之前误用 Children.Add/Remove 会抛
        # 'Viewbox' object has no attribute 'Children'，导致卡片永远挂不进视觉树 →
        # 弹窗整张卡不渲染（"弹窗没了"）。直接赋值 Child 即替换旧卡，无需 Remove/Add。
        if self._vb is not None:
            try:
                self._vb.Child = root
            except Exception as _ev:  # noqa: BLE001
                logger.warning("[native_notify] 挂载卡片到 Viewbox 失败: %s", _ev)

    def _finish_handoff(self) -> None:
        """清理可能的 ghost 残留（无动画后通常不会产生，保留以防万一）。"""
        if self._ghost_root is not None:
            try:
                self._slot.Children.Remove(self._ghost_root)
            except Exception:  # noqa: BLE001
                pass
            self._ghost_root = None
            self._ghost_ctrl = None

    def _win_real_scale(self):
        """返回本窗真实 DPI 倍率 (DPI/96)。优先取 WPF 渲染变换(最权威)，失败退回系统倍率。

        WPF 渲染逻辑单位时：物理像素 = 逻辑 × 此倍率。用它把『物理几何』换算成『WPF 逻辑单位』，
        渲染后精确还原物理尺寸/位置 → 比例正确、不溢出副屏。这是比 GetDpiForSystem 更可靠的
        来源（直接来自本窗 CompositionTarget，自动适配进程/系统 DPI 上下文）。
        """
        try:
            if self._win is not None:
                from System.Windows import PresentationSource
                _ps = PresentationSource.FromVisual(self._win)
                if _ps is not None and _ps.CompositionTarget is not None:
                    _m = _ps.CompositionTarget.TransformToDevice
                    if _m is not None and _m.M11 > 0:
                        return float(_m.M11)
        except Exception:  # noqa: BLE001
            pass
        return _system_dpi_scale()

    def _place(self, size_too=False):
        """以物理像素把窗体钉在主屏工作区内（Win32 SetWindowPos，仅定位；尺寸由 WPF 逻辑管理）。

        【2026-09-06·四修】定位用物理像素（绕过 WPF 逻辑×DPI 不确定 → 绝不下飘到副屏/出屏）；
        尺寸交由 WPF 逻辑单位管理（win.Width/Height = 物理/真实倍率），与 SetWindowPos 只定位
        不冲突 → 比例正确、动画流畅。同时用本窗真实倍率同步 win.Left/Top（记账），保证与 WPF
        渲染一致，后续布局重应用也不会把窗乘到主屏外。
        """
        win = self._win
        if win is None:
            return
        try:
            from System.Windows.Interop import WindowInteropHelper
            import ctypes
            _hwnd = int(WindowInteropHelper(win).Handle)
            if not _hwnd:
                return
            _u = ctypes.windll.user32
            # 本窗真实 DPI 倍率（物理↔逻辑换算的唯一正确除数）
            _real = self._win_real_scale()
            # 同步 WPF 内部逻辑坐标（记账）：物理落点 ÷ 真实倍率 = 逻辑，与 WPF 渲染一致。
            if _real > 0:
                try:
                    _lx = self._x_in_phys / _real
                    _ly = self._y_in_phys / _real
                    if abs(win.Left - _lx) > 0.5:
                        win.Left = _lx
                    if abs(win.Top - _ly) > 0.5:
                        win.Top = _ly
                except Exception:  # noqa: BLE001
                    pass
            # 仅定位（SWP_NOSIZE）：尺寸交给 WPF 逻辑管理，避免与 SetWindowPos 物理尺寸打架
            # 导致窗口被反复 resize / 比例失真 / 动画期间闪跳。
            _flags = 0x0004 | 0x0010 | 0x0001   # SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE
            _u.SetWindowPos.restype = ctypes.c_int
            _u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            _u.SetWindowPos(_hwnd, 0, int(round(self._x_in_phys)),
                            int(round(self._y_in_phys)), 0, 0, _flags)
        except Exception as _e:  # noqa: BLE001
            logger.debug("[native_notify] _place 失败: %s", _e)

    def _recompute_geom(self):
        """按主屏物理工作区推导统一缩放 + 物理落点，最终用 Win32 SetWindowPos 把窗体钉在主屏。

        【2026-09-06·三修·彻底根治"飘到副屏"】
        前两次失败根因：WPF Window.Left/Top/Width/Height 是逻辑单位，WPF 渲染时按『它自己实际的
        DPI 倍率』乘成物理像素。只要我对 DPI 倍率的估计(WPF 内部可能不同)有丝毫偏差，靠近右缘的
        窗口就会被乘到主屏外 → 溢出到副屏。
        本次改为：所有几何先按『主屏物理工作区』(Win32 Monitor API 直取，无歧义) 算好，
        再用 SetWindowPos 以**物理像素**直接落窗——WPF 的 DPI 换算被完全绕过，窗口物理落点由我
        精确控制，硬夹在主屏物理工作区内 → 物理上不可能再飘到副屏/出屏。卡片仍 Viewbox(Uniform)
        不变形。
        """
        try:
            _PL, _PT, _PR, _PB, _scr_scale = _resolve_target_workarea()
            self._scr_scale = _scr_scale
            self._screen_tag = "primary"

            _Psw = max(1.0, _PR - _PL)   # 物理屏宽
            _Psh = max(1.0, _PB - _PT)   # 物理屏高

            # —— 物理卡片缩放：屏宽 22% 为卡片物理宽，受屏高 40% / 窗口物理宽 50% 约束，
            #     夹紧 [0.6, 1.6]（与设计稿同逻辑，全程物理像素，避免 DPI 误判）——
            _win_w_base = float(_W + _WIN_PAD * 2)          # 500 设计宽（含缓冲）
            _phys_scale = (_Psw * 0.22) / float(_W)
            if self._design_h > 0:
                _phys_scale = min(_phys_scale, (_Psh * 0.40) / self._design_h)
            _phys_scale = min(_phys_scale, (_Psw * 0.50) / _win_w_base)
            _phys_scale = max(0.6, min(1.6, _phys_scale))

            # —— 物理尺寸 ——
            _win_w_phys = _win_w_base * _phys_scale
            _win_h_phys = self._design_h * _phys_scale

            # —— 物理落点（右上角，距右/上各 16 物理像素；硬夹在主屏物理工作区内）——
            _margin_phys = 16.0
            _x_in_phys = _PR - _win_w_phys - _margin_phys
            _x_in_phys = max(_PL + 8.0, min(_x_in_phys, _PR - _win_w_phys - 8.0))
            _y_in_phys = _PT + 24.0

            # 存物理几何（_place / _on_tick 直接用）
            self._win_w_phys = _win_w_phys
            self._win_h_phys = _win_h_phys
            self._x_in_phys = _x_in_phys
            self._y_in_phys = _y_in_phys
            # 本窗真实 DPI 倍率（物理↔逻辑换算的唯一正确除数；WPF 渲染逻辑单位实际用的倍率）
            _real = self._win_real_scale()

            # 逻辑缩放：= 窗口逻辑宽 / _W = Viewbox 实际应用的 Uniform 缩放因子。
            # 这样 win.Height = design_h × self._scale 精确等于卡片渲染高度 → 不再裁掉底部
            # 状态行/进度条（三修用 width 基准 scale 推导出 8% 偏矮高度，正是进度条被裁的根因）。
            if _real > 0:
                self._scale = (_win_w_phys / _real) / float(_W)
                self._x_in = int(_x_in_phys / _real)
                self._y_in = int(_y_in_phys / _real)
            else:
                self._scale = _phys_scale
                self._x_in = int(_x_in_phys)
                self._y_in = int(_y_in_phys)
            self._x_out = self._x_in

            logger.info(
                "[native_notify] 重算坐标：主屏物理=(L%.0f,T%.0f,R%.0f,B%.0f) "
                "real_scale=%.3f phys_scale=%.3f → 物理窗=(%.0f×%.0f) 落位(x_in=%.0f,y_in=%.0f)",
                _PL, _PT, _PR, _PB, _real, _phys_scale,
                _win_w_phys, _win_h_phys, self._x_in_phys, self._y_in_phys)

            # —— 应用尺寸（WPF 逻辑单位 = 物理/真实倍率；win 为 None 时仅算坐标）——
            # 尺寸交给 WPF 逻辑管理，SetWindowPos 只定位(SWP_NOSIZE) → 二者不打架、比例正确、动画流畅。
            if _real > 0:
                _lw = _win_w_phys / _real                       # 逻辑宽
                _lh = self._design_h * self._scale             # 逻辑高 = 卡片渲染高（不裁底）
                if self._win is not None:
                    try:
                        if abs(self._win.Width - _lw) > 0.5:
                            self._win.Width = _lw
                        if abs(self._win.Height - _lh) > 0.5:
                            self._win.Height = _lh
                    except Exception:  # noqa: BLE001
                        pass
                if self._slot is not None:
                    try:
                        if abs(self._slot.Width - _lw) > 0.5:
                            self._slot.Width = _lw
                        if abs(self._slot.Height - _lh) > 0.5:
                            self._slot.Height = _lh
                    except Exception:  # noqa: BLE001
                        pass
                if self._vb is not None:
                    try:
                        if abs(self._vb.Width - _lw) > 0.5:
                            self._vb.Width = _lw
                        if abs(self._vb.Height - _lh) > 0.5:
                            self._vb.Height = _lh
                    except Exception:  # noqa: BLE001
                        pass

            # —— 整窗钉在主屏物理落点（SetWindowPos 仅定位，尺寸由 WPF 逻辑管理）——
            self._place()
        except Exception as _e:  # noqa: BLE001
            logger.warning("[native_notify] 重算弹窗坐标/尺寸失败（沿用旧值）: %s", _e)

    def _run(self):
        try:
            win = Window()
            # 【2026-09-06 修复·可见性】设标题(供 webapp FindWindowW 自检定位) +
            # Manual 启动位置(确保下面 win.Left/win.Top 一定生效，否则 WPF 默认可能
            # 忽略 Left/Top 把窗口放到意想不到的位置导致"看不见")。
            win.Title = "达妮娅通知"
            win.WindowStartupLocation = WindowStartupLocation.Manual
            win.WindowStyle = WindowStyle(0)
            win.AllowsTransparency = True
            win.Background = Brushes.Transparent
            win.IsHitTestVisible = False
            win.Topmost = True
            win.ShowInTaskbar = False
            win.ResizeMode = ResizeMode.NoResize
            # 窗口 = 卡片设计尺寸 × 缩放；显式尺寸（不用 SizeToContent，避免内容测量塌缩）。
            # 窗口比卡片宽 _WIN_PAD*2，右侧留白给滑入/滑出缓冲（透明不可见）。
            win.Width = float(_W + _WIN_PAD * 2)
            win.Height = _CARD_DESIGN_H
            win.SizeToContent = SizeToContent.Manual
            # 初始落点用逻辑等价值（_recompute_geom 已在 __init__ 算好物理几何+scale）；
            # 下方 win.Show() 后即刻 _recompute_geom() → _place() 用 SetWindowPos 物理像素钉死主屏。
            win.Left = self._x_in_phys / max(_system_dpi_scale(), 1e-3)
            win.Top = self._y_in_phys / max(_system_dpi_scale(), 1e-3)
            # 单卡槽 Canvas（卡片按 RenderTransform 滑入/滑出；Viewbox 统一缩放不变形）
            slot = Canvas()
            slot.Width = float(_W + _WIN_PAD * 2)
            slot.Height = _CARD_DESIGN_H
            self._slot = slot
            # Viewbox：对卡片做统一(Stretch=Uniform)缩放 → 任意屏幕保持同一长宽比、绝不拉伸；
            # 窗口尺寸 = 设计尺寸 × scale，Viewbox 把固定设计尺寸的卡片等比缩放到窗口内。
            vb = Viewbox()
            vb.Stretch = Stretch.Uniform
            vb.Width = float(_W + _WIN_PAD * 2)
            vb.Height = _CARD_DESIGN_H
            slot.Children.Add(vb)
            self._vb = vb
            win.Content = slot
            self._win = win
            self._dispatcher = win.Dispatcher
            # 【修复·2026-09-06】必须调用 win.Show() 才能让窗口真正可见——WPF 的 Window
            # 即使建好、Dispatcher.Run() 也在跑，不调 Show() 就永远不显示（重写 _run 时
            # 漏掉，导致弹窗全灭）。窗口初始即钉在主屏右上角(_x_in)，show() 时由 _on_tick
            # 逐帧把卡片从右侧滑入（靠卡槽裁剪，溢出主屏右界的部分不渲染到副屏）。
            win.Show()

            # 窗口已存在，按当前屏幕重算缩放 + 停靠（__init__ 时 win 尚为 None 只算了坐标）。
            try:
                self._recompute_geom()
            except Exception:  # noqa: BLE001
                pass

            # 分辨率/DPI 变化（进/出全屏游戏、拔插显示器、缩放设置改）→ 立即按新屏幕重算
            # 缩放与停靠，杜绝"比例跑偏/裁切"。经 _invoke 在 WPF 线程执行，避免跨线程碰 UI。
            try:
                from System.Windows.Forms import SystemEvents
                def _on_disp_change(sender, evt):  # noqa: ANN001
                    try:
                        self._invoke(self._recompute_geom)
                    except Exception:  # noqa: BLE001
                        pass
                SystemEvents.DisplaySettingsChanged += _on_disp_change
            except Exception:  # noqa: BLE001
                pass

            timer = DispatcherTimer()
            timer.Interval = TimeSpan.FromMilliseconds(16)
            timer.Tick += self._on_tick
            timer.Start()

            # 看门狗：独立 Python 线程监测 _on_tick 心跳；若 > 5s 无心跳说明
            # DispatcherTimer 已停摆（极端情况下 WPF 调度线程退出）→ 打 CRITICAL 告警（仅诊断）。
            try:
                _wd = threading.Thread(target=self._watchdog, daemon=True)
                _wd.start()
            except Exception:  # noqa: BLE001
                pass
            # 兜底驱动：若 DispatcherTimer 疑似停摆（心跳 > 1s 没更新、但调度线程还活着），
            # 经 BeginInvoke 把 _on_tick 再驱动起来（10fps 兜底），避免卡片永久冻死。
            try:
                _fb = threading.Thread(target=self._fallback_looper, daemon=True)
                _fb.start()
            except Exception:  # noqa: BLE001
                pass

            _touch_poll()
            self._ready.set()
            logger.info("[native_notify] WPF 通知窗已就绪")
            try:
                from System.Windows.Threading import Dispatcher
                Dispatcher.Run()
            except Exception as _e:  # noqa: BLE001
                logger.warning("[native_notify] Dispatcher.Run 退出: %s", _e)
        except Exception as _e:  # noqa: BLE001
            logger.warning("[native_notify] WPF 窗创建失败: %s", _e)
            self._ready.set()

    def _watchdog(self):
        """看门狗：监测 _on_tick 心跳。若连续 > 5s 无心跳，说明 DispatcherTimer 已停摆
        （极端情况下 WPF 调度线程退出），打 CRITICAL 告警（仅诊断，无法热重启调度线程）。
        """
        while True:
            try:
                time.sleep(2.0)
                _gap = time.time() - self._last_tick
                if self._last_tick > 0.0 and _gap > 5.0:
                    logger.warning(
                        "[native_notify] 看门狗：_on_tick 已 %ss 无心跳，"
                        "DispatcherTimer 可能已停摆（卡片将冻死，需重启应用）", int(_gap))
            except Exception:  # noqa: BLE001
                pass

    def _fallback_looper(self):
        """兜底驱动：DispatcherTimer 疑似停摆（心跳 > 1s 无更新、但调度线程仍活着）时，
        经 BeginInvoke 把 _on_tick 再驱动起来（100ms 一帧 ≈ 10fps 兜底）。
        DispatcherTimer 健康时心跳 < 1s，本循环不触发，不会与正常 16ms 计时器重复驱动。
        """
        while True:
            try:
                time.sleep(0.1)
                if (self._last_tick > 0.0
                        and (time.time() - self._last_tick) > 1.0
                        and self._dispatcher is not None):
                    try:
                        self._dispatcher.BeginInvoke(Action(lambda: self._on_tick(None, None)))
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

    # ── 滑入/滑出（帧驱动，逐帧由 _on_tick 逼近目标，绝不在 pythonnet 里 BeginAnimation）──
    # 【2026-09-06 修复·动画消失根因】上一版改用 WPF 原生 DoubleAnimation(BeginAnimation)，
    # 在 pythonnet 跨语言调用下静默失败 → except 兜底把卡片直接 snap 到终态 → 表现为
    # "瞬移出现/消失、没有滑入滑出"。本版回归逐帧驱动（此前已证能正常播放动画），但复用
    # 已修好的防火墙（逐属性 try/except + 看门狗/兜底线程），杜绝之前"卡半路冻死"。
    def _slide_in(self):
        """标记进入「滑入」状态；实际位移由 _on_tick 每帧按 _ANIM_DUR 逼近。"""
        main = self._main_root
        if main is None:
            return
        # 起始态：右侧屏外 + 透明（与 _build_into_main / _on_tick 滑入公式一致）
        try:
            if main.RenderTransform is not None:
                main.RenderTransform.X = float(_W + _WIN_PAD)
            main.Opacity = 0.0
        except Exception:  # noqa: BLE001
            pass
        self._sliding = True
        self._slide_target = 1.0
        self._slide_start = time.time()

    def _slide_out(self):
        """标记进入「滑出」状态；_on_tick 每帧逼近屏外透明，滑完由停车逻辑停泊窗口。"""
        main = self._main_root
        if main is None:
            try:
                self._place()
            except Exception:  # noqa: BLE001
                pass
            return
        self._sliding = True
        self._slide_target = 0.0
        self._slide_start = time.time()

    def _on_tick(self, sender, e):  # noqa: ANN001
        try:
            _touch_poll()
            self._last_tick = time.time()  # 心跳：供看门狗检测计时器是否停摆
            win = self._win
            if win is None:
                return
            main = self._main_root
            if main is None:
                # 无卡：窗口仍钉在主屏右上角（不移动，避免经过副屏）
                self._place()
                return

            # 测量卡片设计高度（卡片在 Viewbox 内布局，Actual* 仍是其原始设计像素，未含缩放），
            # 用于推导窗口高度；并同步窗口/卡槽/Viewbox 高度（显式尺寸，不再依赖 SizeToContent，
            # 避免 Canvas 测量返回 0 把窗口压成 2px）。⚠️ 尺寸写入包独立 try/except，异常只跳
            # 一帧，绝不外泄吞掉整帧 _on_tick（否则卡片永久冻结）。
            if main.ActualHeight > 0:
                self._design_h = float(main.ActualHeight)
                if self._scale > 0 and self._win is not None:
                    _wh = self._design_h * self._scale
                    if abs(self._win.Height - _wh) > 0.5:
                        try:
                            self._win.Height = _wh
                        except Exception:  # noqa: BLE001
                            pass
                    if self._slot is not None and abs(self._slot.Height - _wh) > 0.5:
                        try:
                            self._slot.Height = _wh
                        except Exception:  # noqa: BLE001
                            pass
                    if self._vb is not None and abs(self._vb.Height - _wh) > 0.5:
                        try:
                            self._vb.Height = _wh
                        except Exception:  # noqa: BLE001
                            pass

            # 每卡首帧应用平滑圆角裁剪（RectangleGeometry 抗锯齿 ≈ CSS border-radius）
            # 【2026-10-02 修复·按钮被裁的第二重保险】`_clip_done` 一旦置 True 就永不再改，
            # 而 Rect 用的是**首帧**的 ActualWidth/Height —— WPF 首帧可能还没完成子控件
            # measure（按钮行的 StackPanel 常在第二帧才拿到最终尺寸），于是裁剪矩形被
            # 永久锁在偏小的高度上，底部按钮即使窗口够高也仍被圆角裁掉。
            # 修法：高度变化超过 2px 时重建裁剪矩形（而不是只做一次）。
            for _r in (main, self._ghost_root):
                if _r is None or _r.ActualHeight <= 0:
                    continue
                _prev_h = float(getattr(_r, "_clip_h", 0.0) or 0.0)
                _h_now = float(_r.ActualHeight)
                if (not getattr(_r, "_clip_done", False)) or abs(_h_now - _prev_h) > 2.0:
                    try:
                        rg = RectangleGeometry()
                        rg.Rect = Rect(0, 0, _r.ActualWidth, _h_now)
                        rg.RadiusX = 18
                        rg.RadiusY = 18
                        _r.Clip = rg
                        _r._clip_done = True      # type: ignore[attr-defined]
                        _r._clip_h = _h_now       # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass

            ctrl = self._main_ctrl
            if ctrl is None:
                # 控件元组尚未就绪（瞬态）：仅把窗口摆到位，不动 Opacity——
                # 滑入动画(BindAnimation)会自动接管透明度，直接写会与动画抢值导致卡片停半空。
                try:
                    self._place()
                except Exception:  # noqa: BLE001
                    pass
                return
            (title, body, status, bar_fill, root, outline, bg, content,
             spine, glow, outer, inner, kicker, pill, pill_text, icon_host, bar_track) = ctrl

            # 文本（每帧更新，支持动态内容）【独立保护·崩不杀动画段】
            try:
                title.Text = self._title
                body.Text = self._body
                status.Text = self._status
            except Exception:  # noqa: BLE001
                pass

            # 场景切换时更新静态控件【独立保护】
            try:
                if self._rendered_scene != self._scene:
                    self._apply_scene(self._scene)
                    ctrl = self._main_ctrl
                    (title, body, status, bar_fill, root, outline, bg, content,
                     spine, glow, outer, inner, kicker, pill, pill_text, icon_host, bar_track) = ctrl
            except Exception:  # noqa: BLE001
                pass

            # ── 自定义进度条更新（独立保护·崩不杀动画段）──
            try:
                track_w = bar_track.ActualWidth
                if track_w <= 0:
                    track_w = _W - 20 - 38 - 14 - 20
                track_w = max(track_w, 100.0)

                if self._error:
                    status.Foreground = self._brush(_ERR_HUE)
                    bar_fill.HorizontalAlignment = HorizontalAlignment.Left
                    bar_fill.Margin = Thickness(0, 0, 0, 0)
                    bar_fill.Width = track_w
                    bar_fill.Fill = self._grad_h(_ERR_HUE, _ERR_HUE2)
                elif self._connected:
                    status.Foreground = self._brush(self._cur_hue)
                    bar_fill.HorizontalAlignment = HorizontalAlignment.Left
                    bar_fill.Margin = Thickness(0, 0, 0, 0)
                    bar_fill.Width = track_w
                    bar_fill.Fill = self._cur_grad
                elif self._progress is not None:
                    status.Foreground = self._brush(self._cur_hue)
                    bar_fill.HorizontalAlignment = HorizontalAlignment.Left
                    bar_fill.Margin = Thickness(0, 0, 0, 0)
                    bar_fill.Width = track_w * max(0.0, min(1.0, self._progress))
                    bar_fill.Fill = self._cur_grad
                else:
                    # 假进度条（默认加载态）：随时间平滑推进到 ~0.9，连接成功由 connected
                    # 分支(上方)补满到 1.0。避免"瞬满"或"死条"，给"正在连上"一个真实感。
                    status.Foreground = self._brush(self._cur_hue)
                    bar_fill.HorizontalAlignment = HorizontalAlignment.Left
                    bar_fill.Margin = Thickness(0, 0, 0, 0)
                    bar_fill.Fill = self._cur_grad
                    if self._fake_start > 0:
                        self._fake = min(0.9, (time.time() - self._fake_start) / _FAKE_LOAD_S)
                    else:
                        self._fake = 0.0
                    bar_fill.Width = track_w * self._fake
            except Exception:  # noqa: BLE001
                pass

            # ── 连接成功：状态文字流畅线性弹入（淡入 + 轻微上移）──
            # 【2026-09-06 修复·用户诉求】未连接不写"连上啦"；真正 connected 时由
            # update() 置 _text_popping，这里每帧把 status 的 Opacity 从 0→1、Y 从 8→0
            # （线性），实现"文字弹入窗口内"。独立 try/except，崩不杀其它段。
            if self._text_popping:
                try:
                    _tp = max(0.0, min(1.0, (time.time() - self._text_pop_t0) / _TEXT_POP_S))
                    if status is not None:
                        status.Opacity = _tp
                        if status.RenderTransform is not None:
                            status.RenderTransform.Y = (1.0 - _tp) * 8.0
                    if _tp >= 1.0:
                        self._text_popping = False
                except Exception:  # noqa: BLE001
                    pass

            # ── 自动缩回（到 _hide_at 触发滑出）──
            # 【2026-09-06 修复·自动缩回】到达 _hide_at 即滑出；此前若忘了消费会
            # 永久卡右上角。滑出由下方帧驱动逻辑执行，滑完停泊窗口屏外。
            # 【2026-10-02 加固·绝不永久停住】`_hide_at=None`（hold_while_connecting
            # 等 connected 事件）的前提是"**那个事件一定会来**"。实测有场景它不来：
            # 陪伴切换弹窗曾传 hold=True 却根本不会推 connected/error → 卡片永久停在
            # "正在切换…"（用户实锤：游戏退出才被别的卡顶掉）。故再加一道看门狗：
            # 无论 _hide_at 是什么，卡片连续可见超过 _MAX_STUCK_S 就强制收起。
            if self._visible and self._hide_at is None:
                _shown_at = float(getattr(self, "_shown_at", 0.0) or 0.0)
                if _shown_at <= 0:
                    self._shown_at = time.time()
                # 【2026-10-02】按卡片语义取上限：陪伴切换卡 45s、真·重连卡 300s。
                _cap = float(getattr(self, "_hold_cap", 0.0) or 0.0) or _MAX_STUCK_S
                if time.time() - _shown_at > _cap:
                    logger.info("[notify] 看门狗：卡片已连续可见 %.0fs 仍未收到结果（上限 %.0fs），强制收起（key=%s）",
                                time.time() - _shown_at, _cap, getattr(self, "_active_key", ""))
                    self._visible = False
                    self._hide_at = None
                    self._shown_at = 0.0
                    self._slide_out()
            elif not self._visible:
                self._shown_at = 0.0
            # ⭐【2026-10-02 补】一旦拿到了 hide_at（含 update(connected/error) 翻绿翻红
            # 后的 _SETTLE_S、以及决策卡的 120s），说明这张卡已经有明确归宿 → 清掉看门狗
            # 计时基准。否则：一次长等待（>210s）后 update 迟到，看门狗计时还停留在旧起点，
            # 下一轮新的 hold 会被"已经等了很久"误判而秒收。
            elif self._hide_at is not None:
                self._shown_at = 0.0
            if self._visible and self._hide_at is not None and time.time() > self._hide_at:
                self._visible = False
                self._hide_at = None
                self._slide_out()

            # ── 滑入/滑出（帧驱动，逐帧逼近目标，绝不卡死）──
            # 每帧按 elapsed/_ANIM_DUR 推进 reveal(0..1)，经 _ease_out 缓动后写 Opacity
            # 与 RenderTransform.X；窗口固定在 x_in，仅滑动卡片 → 不抖。每个属性独立
            # try/except：某帧单个属性写入崩了也只跳一帧、绝不冻整窗。滑出到位后把窗停
            # 泊屏外（不残留透明窗）；滑入到位则停在 x_in。
            if self._sliding:
                try:
                    _elapsed = time.time() - self._slide_start
                    _p = max(0.0, min(1.0, _elapsed / _ANIM_DUR))
                    _eased = _ease_out(_p)
                    # target=1 滑入：reveal 0→1；target=0 滑出：reveal 1→0
                    _reveal = _eased if self._slide_target >= 0.5 else (1.0 - _eased)
                    try:
                        main.Opacity = _reveal
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        if main.RenderTransform is not None:
                            # Viewbox 已统一缩放，位移量按设计像素即可（滑入/滑出由右往左）。
                            main.RenderTransform.X = (1.0 - _reveal) * float(_W + _WIN_PAD)
                    except Exception:  # noqa: BLE001
                        pass
                    if _p >= 1.0:
                        self._sliding = False
                        if self._slide_target < 0.5:
                            # 隐藏态：窗口仍钉在主屏右上角（不移动，避免经过副屏）；
                            # 卡片靠 Opacity=0 不可见。
                            try:
                                self._place()
                            except Exception:  # noqa: BLE001
                                pass
                except Exception:  # noqa: BLE001
                    pass
            else:
                # 静止态：把卡片对齐到其"应处状态"，避免某次滑动未收尾残留偏移。
                #  · 应显示(_visible)：强制 (X=0, Opacity=1) —— 杜绝滑入未收尾残留 X 偏移→右边被裁；
                #  · 已隐藏(非 visible)：强制 (X=屏外, Opacity=0) —— 杜绝滑出未收尾残留→卡片仍挡屏
                #    （用户实测：收回动画播完窗口还钉在右上角——正是这段把已隐藏卡片又复位成可见所致）。
                #  每帧开销极小（仅在非滑动时执行）。
                try:
                    _off = float(_W + _WIN_PAD)
                    if self._visible:
                        if main.RenderTransform is not None and main.RenderTransform.X != 0.0:
                            main.RenderTransform.X = 0.0
                        if main.Opacity != 1.0:
                            main.Opacity = 1.0
                    else:
                        if main.RenderTransform is not None and main.RenderTransform.X != _off:
                            main.RenderTransform.X = _off
                        if main.Opacity != 0.0:
                            main.Opacity = 0.0
                except Exception:  # noqa: BLE001
                    pass

            # 心跳日志：每 ~120 帧（~2s）打一次，确认 _on_tick 仍在跑。
            self._diag_n += 1
            if self._diag_n % 120 == 0:
                logger.info("[native_notify] 心跳 sliding=%s target=%s visible=%s scene=%s",
                            self._sliding, self._slide_target, self._visible, self._scene)
        except Exception as _e:  # noqa: BLE001
            # 【关键】except 体自身绝不能抛异常！否则新异常冒泡到 WPF Dispatcher
            # 计时器基础设施 → 计时器被永久禁用 → _on_tick 再也不触发 → UI 永久冻死。
            try:
                import traceback
                logger.warning("[native_notify] _on_tick 异常（已吞掉）: %s", _e)
            except Exception:  # noqa: BLE001
                pass  # 日志本身也崩了就彻底静默，至少保住计时器不死

    # ── 对外 API（经 Dispatcher.BeginInvoke 跨线程安全）──
    def _invoke(self, fn):
        if self._dispatcher is None:
            return
        def _safe():
            # 【修复·2026-09-06】保护 Dispatcher 线程：单个回调(_do)抛错会被吞掉，
            # 不再让 Dispatcher.Run 退出导致整窗死亡（之前 _apply_scene 解包 None
            # 就是这么把线程搞死的）。
            try:
                fn()
            except Exception as _e:  # noqa: BLE001
                import traceback
                logger.warning("[native_notify] 跨线程调用异常（已吞，保活 Dispatcher）: %s\n%s",
                               _e, traceback.format_exc())
        try:
            self._dispatcher.BeginInvoke(Action(_safe))
        except Exception as _e:  # noqa: BLE001
            logger.warning("[native_notify] BeginInvoke 失败: %s", _e)

    def show(self, title, body, scene, timeout_ms, key="default",
             hold_while_connecting: bool = False, app_icon: Optional[str] = None,
             actions: Optional[list] = None, hold_max_s: Optional[float] = None):
        """hold_max_s：hold 卡的**分级上限**（秒），None 时沿用全局 _MAX_STUCK_S(300s)。

        【2026-10-02 新增】陪伴切换卡传 45：它只是"换陪法"的告知，reprepare 通常
        几秒内完成；等满 300s 会让用户看到"卡在右上角不消失"。真·重连卡（本地模型
        挂载 35s+）继续用 300s，两者不再共用一个值。
        """
        def _do():
            # 重建卡片内容并置可见。如有旧 ghost 残留先清掉。
            self._finish_handoff()
            # 【2026-10-02 新增】决策按钮：每次 show 必须重置，防上一张卡的按钮泄漏到新卡
            # （用户看到"这卡怎么有我不认识的按钮"）。无 actions 时置 None → 不渲染该行。
            self._actions = list(actions) if actions else None
            # 【2026-09-06 修复·状态泄漏】每次 show 必须重置进度条状态，否则上一张卡留下的
            # connected/error/progress 会污染新卡 → 新卡直接显示静态满条/半条（用户感知
            # "进度条死了/卡在1/4"）。重置后新卡进入"假加载"态，直到 orchestration
            # 显式 update(connected=True) 才翻绿。
            self._progress = None
            self._connected = None
            self._error = None
            self._indeterminate = True
            # 软件图标：每次 show 重置，防旧软件 logo 泄漏到新卡
            self._app_icon_path = app_icon
            # 【2026-09-06 修复·连上文字默认残留】show() 从不重置 self._status，导致上一张卡的
            # "连上啦，开始陪你" 泄漏到新卡 → 用户感知"默认显示已连接"。重置为加载中提示，
            # 真正连上由 update(connected=True) 写入"连上啦"并触发文字弹入。
            self._status = "正在切换…"
            # 【2026-10-02】决策卡的状态文字要说清"在等什么"。此前沿用"正在切换…"，
            # 而决策卡（等用户点按钮）根本不连任何东西、也没有进度条 → 用户看到
            # 一句永远不变的"正在切换…"，与进度条被折叠后叠加起来就是"这卡坏了"。
            # 决策卡改成明确的"等你选一个"（它等的确实是这个）。
            if getattr(self, "_actions", None):
                self._status = "等你选一个 👇"
            # 复位假进度条 + 文字弹入状态
            self._fake = 0.0
            self._fake_start = time.time()
            self._text_popping = False
            self._build_into_main(title, body, scene)
            self._active_key = key
            self._visible = True
            # 【2026-10-02】记录本次可见起点，供 _on_tick 的"永久停住"看门狗计时。
            self._shown_at = time.time()
            # 【2026-09-08 优化·连接中保持】hold_while_connecting=True 时（双工重连类弹窗），
            # 连接未完成（connected/error 均为 None）绝不设自动回收 → 弹窗一直挂到会话 100%
            # 才由 update(connected=True/error) 翻绿/报红并按 _SETTLE_S 收起。否则沿用最短展示下限。
            # 【2026-10-02】决策卡（有按钮）在上面两条规则**之前**判定：它在等用户点，
            # 按 timeout 收起用例会显得"提示一闪就没、按钮来不及点"。
            # ⚠️ 60→120→**60**：120s 那版实测用户反馈"一直卡右上角不隐藏"（日志：
            # 20:35:04 弹的卡到 20:37:07 才被下一张盖掉，123 秒）。60s 是"读完+决定"
            # 的舒适上限，且真到点会自动收起，不会留僵尸卡。
            if getattr(self, "_actions", None):
                self._hide_at = time.time() + 60.0
                self._hold_cap = 0.0
            elif hold_while_connecting and self._connected is None and self._error is None:
                # 【2026-10-02 新增·按卡片语义分级上限】hold 卡必须配一个"最多等多久"，
                # 否则两类语义共用 _MAX_STUCK_S(300s)：
                #   · 真·重连卡（本地模型挂载可要 35s+）→ 等 300s 合理；
                #   · 陪伴切换卡（只是换陪法提示词）→ 等 300s 就是"卡在右上角不消失"。
                # hold_max_s 由调用方按语义给；缺省沿用全局上限（保持旧行为不变）。
                # 到达上限后 _on_tick 的看门狗分支会强制收起，不会留僵尸卡。
                self._hide_at = None
                self._hold_cap = float(hold_max_s) if hold_max_s else _MAX_STUCK_S
            else:
                self._hide_at = time.time() + max(timeout_ms / 1000.0, _MIN_VISIBLE_S)
                self._hold_cap = 0.0
            # 重算停靠坐标（进/出全屏游戏后分辨率可能变了）→ 再用新 x_in 贴回窗口。
            self._recompute_geom()
            # 窗口先就位（物理钉主屏），卡片由 _on_tick 逐帧从右侧滑入。
            try:
                self._place()
            except Exception:  # noqa: BLE001
                pass
            self._slide_in()
            logger.info(
                "[notify] show(key=%s) 窗口物理落位 x_in=%.0f y_in=%.0f "
                "(WPF.Left=%.0f WPF.Top=%.0f, dpi_scale=%.3f, 屏=%s)",
                key, self._x_in_phys, self._y_in_phys,
                (self._win.Left if self._win else -1),
                (self._win.Top if self._win else -1),
                self._scr_scale, getattr(self, "_screen_tag", "?"))

        self._invoke(_do)

    def update(self, status=None, progress=None, connected=None, error=None, indeterminate=None):
        def _do():
            # 【2026-10-02 修正·上一版改过头了】此前对决策卡**完全忽略 update**，
            # 结果：用户反馈"原本该等进度条跑满的逻辑没了 / UI 直接隐藏"。
            # 真实需求是两条**独立**的诉求：
            #   ① 决策卡**不该被结果自动收起**（用户正要按按钮时卡消失 = 点了没反应）
            #   ② 但**别的连接卡**该跑满进度条、该翻绿翻红、该按 _SETTLE_S 收起，
            #      这一整套原有行为**一个字都不能改**。
            # 所以这里只拦"connected/error 触发的自动收起"，其余（status/progress）
            # 照常处理；且决策卡本身不画进度条（见 _build_content），自然不会看到卡住的条。
            _is_choice = bool(getattr(self, "_actions", None)) and self._visible
            if status is not None:
                self._status = status
            if progress is not None:
                self._progress = max(0.0, min(1.0, float(progress)))
                self._indeterminate = False
            if connected is not None:
                self._connected = bool(connected)
                self._indeterminate = False
                if connected:
                    self._progress = 1.0
                    # 连接成功：触发状态文字流畅弹入（淡入 + 轻微上移），由 _on_tick 驱动
                    self._text_popping = True
                    self._text_pop_t0 = time.time()
                # 决策卡：**不因结果自动收起**（仍等用户点击；hide_at 已在 show 时给足 60s）
                if not _is_choice:
                    self._hide_at = time.time() + _SETTLE_S
            if error is not None:
                self._error = str(error)[:60]
                self._indeterminate = False
                self._progress = 1.0
                if not _is_choice:
                    self._hide_at = time.time() + _SETTLE_S
            if indeterminate is not None:
                self._indeterminate = bool(indeterminate)
        self._invoke(_do)

    def retract(self):
        def _do():
            self._finish_handoff()
            if self._main_root is not None and self._visible:
                self._visible = False
                self._hide_at = None
                self._slide_out()
                logger.debug("[notify] retract")
        self._invoke(_do)

    def is_showing(self) -> bool:
        return bool(self._visible)

    def is_choice_card_showing(self) -> bool:
        """【2026-10-02 新增】当前可见卡是否带决策按钮（在等用户点击）。

        供 mode_notify 决定"要不要忽略双工链路的 update"：决策卡与连接进度无关，
        被别的链路翻绿/翻红收起会让用户"正要点按钮卡却消失"。
        """
        return bool(self._visible) and bool(getattr(self, "_actions", None))


# =====================================================================
# 模块级单例 API（供 mode_notify 调用；WPF 单后端）
# =====================================================================
_backend = None


def ensure_notify_window():
    """创建 WPF 原生通知窗（单后端，无兜底）。幂等。返回后端或 None。"""
    global _backend
    if _backend is not None:
        return _backend
    if not clr:
        logger.warning("[native_notify] 无 pythonnet，通知窗禁用")
        return None
    if _HAVE_WPF:
        try:
            _backend = _WpfBackend()
            logger.info("[native_notify] 已启用 WPF 通知窗")
            return _backend
        except Exception as _e:  # noqa: BLE001
            logger.warning("[native_notify] WPF 后端失败，通知窗禁用: %s", _e)
    logger.warning("[native_notify] 无可用渲染后端，通知窗禁用")
    return None


def show(title, body, scene, timeout_ms, key="default",
         hold_while_connecting: bool = False, app_icon=None, actions=None):
    """模块级入口。

    【2026-10-02 修复】actions 必须在此透传给后端——此前只有 _WpfBackend.show
    接受该参数，模块级 show 没转发，导致 mode_notify 传 actions 时抛
    "show() got an unexpected keyword argument 'actions'"，
    **所有带按钮的弹窗全部静默失败**（用户连原生浮窗都看不到）。
    """
    if _backend is None:
        ensure_notify_window()
    if _backend is None:
        logger.warning("[native_notify] show 跳过：无可用后端")
        return
    _backend.show(title, body, scene, timeout_ms, key,
                  hold_while_connecting=hold_while_connecting, app_icon=app_icon,
                  actions=actions)


def update(status=None, progress=None, connected=None, error=None, indeterminate=None):
    if _backend is None:
        ensure_notify_window()
    if _backend is None:
        return
    _backend.update(status, progress, connected, error, indeterminate)


def retract():
    if _backend is not None:
        _backend.retract()


def is_showing() -> bool:
    return bool(_backend is not None and _backend.is_showing())


def is_choice_card_showing() -> bool:
    """当前可见的是否是**决策卡**（带按钮、在等用户点击）。

    【2026-10-02 新增】供 mode_notify.update_mode_notify 判断"该不该忽略 update"：
    决策卡等的是用户点击，不是双工连接进度，若被别的链路的 connected/error 翻绿/翻红
    收起，用户正要点按钮时卡片会突然消失（表现为"点了没反应"）。
    """
    try:
        if _backend is None:
            return False
        return bool(_backend.is_choice_card_showing())
    except Exception:  # noqa: BLE001
        return False
