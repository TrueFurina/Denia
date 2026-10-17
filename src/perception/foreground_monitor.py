"""前台窗口监听：Win32 轮询前台窗口 + 系统空闲时间（事件驱动层）。

作用：
  1. 窗口/应用切换时发布 FOREGROUND_CHANGED 事件 —— 零 Token 成本的
     "有事发生"信号，AI 只在切应用时才"知道"，不用每几秒截图问大模型。
  2. 通过 GetLastInputInfo 提供真实的系统空闲时间，替代"只靠用户文本交互
     判断空闲"的旧方式（看视频/玩游戏不动手也照样能正确判定空闲）。

窗口标题 → 应用类型（app_hint）映射是确定性规则，不用 LLM。
仅支持 Windows；其他平台自动禁用（no-op）。
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
import os
import functools
import subprocess
import threading
import time
import re as _re
from typing import Optional

from ..config import Config
from ..event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)


# ============================================================================
# 进程名 PID→exe TTL 缓存（2026-09-05 治本修复·降 OpenProcess 频次）
# ----------------------------------------------------------------------------
# 每次枚举顶层窗口都要对【每个窗口】OpenProcess + QueryFullProcessImageNameW
# 拿进程名。几十上百个窗口 × 每秒多次枚举 → OpenProcess 调用量爆炸，且对无响应/
# 受保护进程 OpenProcess 可能拖慢线程。这里按 PID 缓存进程名 3s，同 PID 在缓存期内
# 只 OpenProcess 一次。配合下面的"采样线程隔离"，把 Win32 枚举从热路径彻底挪走，
# 从根上消除"枚举卡住 → 事件循环冻结 → 软件卡死"。
# ============================================================================
_exe_cache: dict[int, tuple[float, str]] = {}
_exe_cache_lock = threading.Lock()
_EXE_TTL_S = 3.0


def _exe_of_pid_cached(pid: int) -> str:
    """带 TTL 缓存的 PID→exe（小写）；缓存未命中才真正 OpenProcess 查询。"""
    if not pid:
        return ""
    _now = time.time()
    with _exe_cache_lock:
        _c = _exe_cache.get(pid)
        if _c is not None and _now - _c[0] < _EXE_TTL_S:
            return _c[1]
    _name = _exe_of_pid(pid)
    with _exe_cache_lock:
        _exe_cache[pid] = (_now, _name)
    return _name


# ============================================================================
# 感知采样隔离（2026-09-05 治本修复·根除软件卡死）
# ----------------------------------------------------------------------------
# 设计：所有 Win32 枚举（EnumWindows / OpenProcess / GetForegroundWindow /
# SendMessageTimeout）只在【独立串行 daemon 采样线程】上发生。该线程每 ~0.4s 把
# 全部感知结果算好写进 self._snap（带锁）。其余所有查询方法被 @_sampled 包装：
#   - 采样线程内调用 → 跑原始实时逻辑并把结果写进 _snap（live 路径）；
#   - 其他任何线程（asyncio 事件循环 / executor / proactive 逻辑线程）调用 →
#     直接读 _snap 缓存（纯字典读取，零 Win32、零阻塞）。
# 这样无论前台怎么切、哪个窗口无响应，事件循环都绝不会被 Win32 枚举阻塞，
# 之前"几天一卡死"的根因（热路径同步枚举）被结构性消除，而非打补丁。
# ============================================================================
_tls = threading.local()


def _sampled(key: str, default=None):
    """把查询方法变成"采样线程算+写缓存，其他线程读缓存"。保留原方法实时逻辑不变。"""
    def _deco(fn):
        @functools.wraps(fn)
        def _wrapper(self, *args, **kwargs):
            if getattr(_tls, "sampling", False):
                _val = fn(self, *args, **kwargs)
                with self._snap_lock:
                    self._snap[key] = _val
                return _val
            with self._snap_lock:
                return self._snap.get(key, default)
        return _wrapper
    return _deco


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


# 窗口标题 → 应用类型（按优先级匹配，越靠前越优先）
_TITLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("game", ("league of legends", "绝地求生", "pubg", "原神", "genshin",
              "鸣潮", "wuthering", "wuthering waves",
              "王者荣耀", "steam", "csgo", "counter-strike", "英雄联盟",
              "valorant", "无畏契约", "apex", "永劫无间", "dota",
              # 腾讯会议：用户手机屏幕共享打手游（如三角洲）时，前台是腾讯会议窗口
              "腾讯会议", "tencent meeting", "wemeet", "voov meeting")),
    ("ide", ("visual studio", "vscode", "visual studio code", "pycharm",
             "intellij", "webstorm", "clion", "code - ", "sublime", "编辑器")),
    ("browser", ("chrome", "microsoft edge", "firefox", "opera", "brave",
                 "bilibili", "哔哩哔哩", "爱奇艺", "腾讯视频", "优酷", "edge",
                 "抖音", "douyin", "快手", "kuaishou", "tiktok", "直播")),
    ("chat", ("wechat", "微信", "qq", "钉钉", "dingtalk", "tim", "discord",
              "telegram", "slack", "企业微信")),
    ("document", ("word", "wps", "pdf", "acrobat", "excel", "powerpoint",
                  "onenote", "文档")),
]

# 直播场景标题关键词：前台属浏览器类（抖音/B站/快手/网页）且标题含这些词，
# 或纯直播平台（斗鱼/虎牙/YY直播/twitch），判定为"在看直播"。
# 用于双工在直播时只做旁观点评、不接主播话茬。
_LIVESTREAM_TITLE_KW = ("直播", "直播间", "直播中", "douyu", "斗鱼", "huya", "虎牙",
                        "yy直播", "twitch")

WINDOW_TITLE_BUF = 512
WINDOW_CLASS_BUF = 256

# 前台窗口标题虽含游戏关键词，但进程属于以下"明显非游戏"程序时，
# 不应判为游戏前台——否则用户打开名为 "Delta Force" 的文件夹（资源管理器）、
# 或录屏面板标题带游戏名，会被误判成"正在打游戏"而误锁快照模式。
# 真游戏进程的 exe 不在本集合内，不受影响。
_NON_GAME_FOREGROUND_PROC = frozenset({
    "explorer.exe",   # 文件资源管理器（如 "Delta Force - 文件资源管理器" 窗口）
})

# 音视频通话/会议/语音频道场景（看窗口判定，不用音频关键词）。
# 通话时系统扬声器会播放对方声音，WASAPI 环回会把对方的话也采集进双工推流，
# 模型会把好友的话当成用户的话去回应（"NTR"实锤）。这些软件前台时视为
# "通话中"，由 proactive 双工停采环回音频并强制闭嘴。
# 1) 前台窗口标题含"通话"字样：微信音视频通话 / QQ音视频通话 / 正在通话…
_CALL_TITLE_KW = ("通话",)
# 2) 前台窗口所属进程为会议/语音软件：腾讯会议 / 钉钉 / 飞书 / Zoom / Discord /
#    KOOK / TeamSpeak / YY语音（精确 exe 名，避免前缀误伤）
_CALL_PROC_NAMES = frozenset({
    "wemeetapp.exe", "wemeet.exe", "voovmeeting.exe",
    "dingtalk.exe", "feishu.exe", "lark.exe", "zoom.exe",
    "discord.exe", "kook.exe", "teamspeak3.exe", "ts3client_win64.exe",
    "yy.exe", "yyexternal.exe",
})
# 3) 日常聊天软件：本身出声≠通话（提示音/自动播放视频），不能进 _CALL_PROC_NAMES
#    或 VOICE_CALL_PROC_NAMES（否则静音判定疯狂抖动、推流音频断裂胡言乱语，老坑）。
#    它们只在"通话窗口存在"（has_call_window）或"持续出声达标+去抖"（proactive 双工
#    静音循环）时才判定为通话，从而既能屏蔽 QQ/微信通话，又不误伤日常提示音。
_COMM_CALL_PROC_NAMES = frozenset({
    "wechat.exe", "weixin.exe", "qq.exe", "tim.exe",
    "wechatappex.exe", "wechatweb.exe",
})

# 游戏进程名（exe）白名单：除标题含游戏词外，按"前台进程是可执行游戏引擎/
# 启动器"判定真在打游戏。覆盖未登记到 _TITLE_RULES 的游戏（如鸣潮/绝区零/三角洲
# 用的 Unreal Engine 发行客户端 Client-Win64-Shipping.exe）。这类进程只在"真打
# 游戏"时出现，看游戏视频（抖音/浏览器）不会命中，不会误锁。
# 重负载应用进程库（用于"诞生拦截 / 快速通道"），分两档：
#  - INSTANT（秒卸档）：明确的大型 3A / 剪辑 / 3D / AI 出图 / 转码。这些几乎必吃满
#    GPU，进程一出现即锁+卸（不等 GPU 负载爬升）。含具体大作 exe + 创作类软件。
#  - ENGINE（负载档）：通用游戏引擎客户端（UE/Unity 发行版）。不秒卸，靠 GPU 负载
#    驱动判定——小游戏(2D/轻量)走引擎但 GPU 占用低 → 自然不卸；3A 大作 GPU 跑满 → 卸。
# 启动器（Wuthering Waves.exe / 各平台 launcher）不收录 → 不误触。
_HEAVY_INSTANT_PROC_NAMES = frozenset({
    # 具体大型 3A（按 exe 命中，避免子串误伤）
    "deltaforce.exe", "deltaforceclient.exe",      # 三角洲行动
    "zzz.exe", "zenlesszonezero.exe",              # 绝区零
    # 米哈游自研引擎（非 UE/Unity，进程名不匹配 load 规则，必须显式进 instant 秒卸档）
    "starrail.exe", "honkaiimpact3.exe", "bh3.exe",  # 崩坏：星穹铁道 / 崩坏3
    "genshinimpact.exe", "yuanshen.exe",            # 原神（国际服 / 国服）
    "blackmythwukong.exe", "wukong.exe",           # 黑神话悟空
    "cyberpunk2077.exe", "eldenring.exe",          # 赛博朋克2077 / 艾尔登法环
    "ghosts.exe", "tga.exe",                       #  other 3A（占位，按需补）
    # 剪辑 / 后期
    "premiere pro.exe", "afterfx.exe", "davinciresolve.exe", "vegaspro.exe",
    # 3D / 建模 / 渲染
    "blender.exe", "cinema 4d.exe", "maya.exe", "3dsmax.exe", "houdini.exe",
    # AI 出图 / 视频（本地推理，必吃 GPU）
    "comfyui.exe", "stable-diffusion-webui.exe", "invokeai.exe",
    # 转码 / 通用渲染
    "handbrake.exe", "renderdoc.exe",
})
_GAME_ENGINE_PROC_NAMES = frozenset({
    "client-win64-shipping.exe",  # Unreal 发行客户端（鸣潮/三角洲/绝区零…）
    "unityplayer.exe", "unity.exe",
    "ue4game.exe", "ue5game.exe",
})

# 游戏平台启动器进程名（用于"是否由平台启动"零维护识别，见 is_launched_by_game_platform）
_PLATFORM_LAUNCHER_PROC_NAMES = frozenset({
    "steam.exe", "epicgameslauncher.exe", "egs.exe",
    "wegame.exe", "wgame.exe", "wegameplayer.exe",
    "origin.exe", "eaapp.exe", "battlenet.exe",
    "xboxapp.exe", "gamingservices.exe",
})

# 【2026-09-04 修复·启动器不算"真在打游戏"】游戏启动器/更新器/补丁器的窗口标题
# 往往就是游戏名本身（实锤：鸣潮启动器进程 launcher_main.exe，窗口标题"鸣潮"）。
# 于是出现三套标准打架的错配：
#   ① 前台类型判定（标题含游戏词）→ 判为 game → UI/陪伴方案显示"已切到鸣潮"；
#   ② 游戏知识/提示词（跟随前台标题 + OCR 文字）→ 加载鸣潮提示词（OCR 甚至从
#      启动器公告文字"《鸣潮》3.6版本创作激励计划"识别出游戏名）；
#   ③ 高负载锁/模式切换（真实游戏进程 + GPU 负载）→ 启动器不是游戏 exe → 不锁、
#      不切云端 → 仍是本地双工。
# 结果就是用户看到的"UI 显示切到鸣潮，却还在用本地双工跑鸣潮提示词"。
# 故：标题命中游戏词、但前台进程是启动器/更新器/补丁器 → 一律不算真在游戏，
# 从源头消掉 ①②③ 的错配。真游戏进程（client-win64-shipping.exe /
# deltaforceclient-win64-shipping.exe / zzz.exe 等）不含下列词，不受影响。
_LAUNCHER_PROC_KEYWORDS = (
    "launcher", "启动器", "updater", "patcher", "installer", "setup",
    "crashhandler", "crashreport", "bugreport", "bootstrap",
)
_LAUNCHER_PROC_EXACT = frozenset({
    "launcher_main.exe",      # 鸣潮启动器（实锤：前台进程 launcher_main / 标题"鸣潮"）
    "wuthering waves.exe",    # 鸣潮启动器（国际服命名）
    "launcher.exe", "gamelauncher.exe",
})


def _is_launcher_proc(proc: str) -> bool:
    """进程是否为"游戏启动器/更新器/补丁器/平台客户端"——不算真在打游戏。

    仅用于"标题命中游戏词"时的反查（见 is_game_foreground）。真游戏进程不会
    命中（它们的可执行名不含 launcher/updater 等词）。
    """
    if not proc:
        return False
    p = (proc or "").lower().strip()
    if not p:
        return False
    if p in _LAUNCHER_PROC_EXACT or p in _PLATFORM_LAUNCHER_PROC_NAMES:
        return True
    return any(k in p for k in _LAUNCHER_PROC_KEYWORDS)


# SendMessageTimeoutW 参数（替代 GetWindowTextW，避免目标窗口无响应时永久阻塞调用线程）
WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002
SMTO_ERRORONEXIT = 0x0020
_MSGOUT_TIMEOUT_MS = 500


def _window_title(hwnd: int) -> str:
    """读取窗口标题（安全版：目标窗口无响应也绝不阻塞调用线程）。

    ⚠️ 必须用 SendMessageTimeoutW + SMTO_ABORTIFHUNG，绝不能用 GetWindowTextW。
    原因：GetWindowTextW 对「属于其他线程的窗口」会内部 SendMessage(WM_GETTEXT)
    阻塞等待目标窗口线程响应；若该线程卡死 / 独占全屏 / 无响应（如切到 QQ 全屏、
    任务管理器卡顿），调用线程会被**永久阻塞**。本函数调用链
    game_screen_source → visible_windows 跑在 asyncio 主循环线程上，一旦被阻塞
    整个后端冻结（现象=软件卡死，fault_watchdog 每 45s dump 全栈）。
    SendMessageTimeoutW 在目标「看起来挂了」或超时时立即返回，绝不永久阻塞。
    """
    buf = ctypes.create_unicode_buffer(WINDOW_TITLE_BUF)
    try:
        _u = ctypes.windll.user32
        # argtypes/restype 只设置一次（全局共享），确保 64 位 HWND/句柄不被截断
        if not getattr(_window_title, "_typed", False):
            # ⚠️ 2026-10-13 修复：第 4 参原声明为 wintypes.LPARAM（整数类型），
            # 但下方调用传的是 ctypes.byref(buf)（指针对象）→ ctypes 参数转换抛
            # ArgumentError: argument 4 ... cannot be interpreted as an integer，
            # 被 except 静默吞掉 → _window_title() 恒返回空。LPARAM 本就是指针宽度
            # 的整型槽，用 c_void_p 声明即可与 byref 指针正确匹配（实测可读出标题）。
            _u.SendMessageTimeoutW.argtypes = [
                ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM,
                ctypes.c_void_p, ctypes.wintypes.UINT, ctypes.wintypes.UINT,
                ctypes.POINTER(ctypes.wintypes.DWORD),
            ]
            # ⚠️ 2026-10-13 修复：原为 ctypes.wintypes.LRESULT —— 该属性在 Python 3.12
            # 的 ctypes.wintypes 里**不存在**，赋值即抛 AttributeError，被下方
            # `except Exception: return ""` 静默吞掉 → _window_title() 对任何窗口都返回空
            # （实测 12/12 窗口全空，主/子线程皆然）→ fg_title 恒空 → 前台切换事件自
            # 2026-09-05 起永不发布（app 事件断供），前台 hint 恒为 unknown。
            # LRESULT 的真实宽度 = 指针宽度，用 c_ssize_t 表达。
            _u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
            _window_title._typed = True
        _res = _u.SendMessageTimeoutW(
            ctypes.wintypes.HWND(hwnd),
            WM_GETTEXT,
            ctypes.wintypes.WPARAM(WINDOW_TITLE_BUF),
            ctypes.byref(buf),
            ctypes.wintypes.UINT(SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT),
            ctypes.wintypes.UINT(_MSGOUT_TIMEOUT_MS),
            None,
        )
    except Exception:  # noqa: BLE001
        # 不吞异常：静默 return "" 曾让 LRESULT 缺失类 bug 潜伏一个月（见上方 2026-10-13 注）
        logger.debug("读取窗口标题失败 hwnd=%s", hwnd, exc_info=True)
        return ""
    if not _res:
        return ""
    return buf.value[:WINDOW_TITLE_BUF - 1].strip()


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(WINDOW_CLASS_BUF)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, WINDOW_CLASS_BUF)
    return buf.value or ""


def _process_is_meeting(hwnd: int) -> bool:
    """按进程名判断窗口是否属于腾讯会议（WeMeet）。

    腾讯会议窗口标题多变（主界面"腾讯会议"，开会时变成"xxx的会议"/"xxx的快速会议"），
    类名又是通用 Qt 类名（Qt680QWindowIcon），只有可执行文件名稳定（wemeetapp.exe），
    因此用进程名兜底判定，避免开会时被误判成"非游戏"导致 OCR/队伍识别停扫。
    """
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        # 走 PID→exe TTL 缓存，避免每个窗口都 OpenProcess（治本修复·降暴露面）
        name = _exe_of_pid_cached(int(pid.value))
        return name in ("wemeetapp.exe", "wemeet.exe") or name.startswith("wemeet")
    except Exception:  # noqa: BLE001
        logger.debug("进程名判定失败", exc_info=True)
    return False


def _process_name(hwnd: int) -> str:
    """返回窗口所属进程的可执行文件名（小写）；失败返回空串。"""
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # 走 PID→exe TTL 缓存，避免每个窗口都 OpenProcess（治本修复·降暴露面）
        return _exe_of_pid_cached(int(pid.value))
    except Exception:  # noqa: BLE001
        logger.debug("进程名读取失败", exc_info=True)
    return ""


def _exe_of_pid(pid: int) -> str:
    """按 PID 返回进程可执行文件名（小写）；失败返回空串。供 GPU 负载兜底识别元凶。"""
    try:
        if not pid:
            return ""
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        logger.debug("PID→exe 失败 (%s)", pid, exc_info=True)
    return ""


def _process_is_unity(hwnd: int) -> bool:
    """按进程名判断窗口是否属于 Unity（编辑器/打包后的 Unity 应用）。

    本软件的 3D 模型（达妮娅）跑在 Unity 里：Unity 窗口画面是"伴侣自身"，
    不是用户的屏幕活动。感知系统必须识别它并跳过分析——否则视觉模型会把
    二次元模型画面脑补成"打游戏/看视频/直播"，摘要注入对话上下文后 AI
    会乱聊游戏、抖音（用户实锤："搁那说游戏的""又说抖音"）。
    Unity 打包应用进程名是自定义 exe，标题兜底在 is_unity_foreground 里做。
    """
    name = _process_name(hwnd)
    return name in ("unity.exe", "unityhub.exe", "unity editor.exe") or name.startswith("unity")


# 【2026-10-02 新增】浏览器 / WebView 宿主进程：常驻、不是游戏。
# 浏览器看视频/看网页正是「桌宠看屏幕互动」的典型场景，绝不能触发"让显卡→卸本地模型"。
# 之前 WebView2 宿主（msedgewebview2.exe）被算进 load 档，导致开机 3 秒就误判在玩游戏
# → 卸载本地 Comni → 桌宠看屏互动被关掉（run_20261002_174210.log 行 186 实锤）。
_BROWSER_HOSTS = frozenset({
    "msedgewebview2.exe", "msedge.exe", "chrome.exe", "firefox.exe",
    "brave.exe", "opera.exe", "vivaldi.exe", "360chrome.exe", "360se.exe",
    "sogouexplorer.exe", "qqbrowser.exe", "iexplore.exe", "maxthon.exe",
    "quarkbrowser.exe", "browser.exe", "thunder.exe", "qqpcmgr.exe",
    # WebView2/WebKitGTK 通用宿主（pywebview / 任意 Electron-WinUI 应用）
    "msedgewebview.exe", "webview2.exe", "webkitwebprocess.exe",
})

# 【2026-10-02 新增】宿主 / 开发工具 / 终端进程：常驻、非游戏。
# 实锤：workbuddy.exe（用户开着 WorkBuddy）被判成游戏 → 卸载本地 Comni → 桌宠互动消失。
# 这类进程是"用户的工作环境"，与"让显卡给游戏"毫无关系。
_HOST_TOOL_HOSTS = frozenset({
    # 本软件宿主 / 助手
    "workbuddy.exe", "codebuddy.exe", "python.exe", "pythonw.exe",
    # 终端 / Shell
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "conhost.exe", "bash.exe", "sh.exe", "git.exe",
    # 编辑器 / IDE
    "code.exe", "cursor.exe", "devenv.exe", "idea64.exe", "pycharm64.exe",
    "webstorm64.exe", "sublime_text.exe", "notepad++.exe", "zed.exe",
    "trae.exe", "trae cn.exe",
    # 文件 / 同步 / 通讯（常驻后台，非游戏）
    "explorer.exe", "onedrive.exe", "dropbox.exe", "qq.exe", "tim.exe",
    "wechat.exe", "weixin.exe", "dingtalk.exe", "feishu.exe", "slack.exe",
    "discord.exe", "telegram.exe",
    # ⚠️ 故意**不**排除 steam.exe / steamwebhelper.exe：Steam 启动游戏时确实该让显卡，
    #    而且真正的游戏进程本身已在重负载名单里，误判成本高于漏判成本。
    # 远程桌面 / 录屏
    "teamviewer.exe", "anydesk.exe", "mstsc.exe", "parsec.exe",
    "obs64.exe", "obs32.exe", "nvidia share.exe", "xscreenrecorder.exe",
})


def _heavy_app_tier(proc: str) -> str:
    """进程名属于哪档重负载应用。

    返回：
      'instant' → 明确大型 3A / 剪辑 / 3D / AI 出图 / 转码。进程一出现即锁+卸（快速通道，
                  不等 GPU 负载爬升）。
      'load'    → 通用游戏引擎客户端（UE/Unity 发行版）。靠 GPU 负载驱动判定：小游戏
                  (2D/轻量) GPU 占用低 → 自然不卸；3A 大作 GPU 跑满 → 卸。
      ''        → 非重负载应用（含启动器 / 看视频的浏览器 / 办公软件）。
    启动器进程（Wuthering Waves.exe 等）不含 shipping 且不在名单 → 返回 ''。
    """
    if not proc:
        return ""
    p = proc.lower()
    # 【2026-09-06 修复·误锁根因】游戏辅助/优化/overlay 类软件（GamePP、游戏加加、
    # MSI Afterburner、RivaTuner、NVIDIA 广播/overlay 等）本身不吃显卡，却常驻后台
    # 挂大量隐藏窗口。一旦被学进 learned 库（instant 秒卸档），就会"常驻即误锁云端"
    # 且永不解锁（heavy 永远为 True）→ 表现正是「切换路径全死、卡云端、进游戏也不弹」。
    # 这类进程绝不参与重负载判定，硬排除。
    _HEAVY_EXCLUDE_PROC_NAMES = frozenset({
        "gamepp.exe",            # 游戏加加/GamePP 类优化器，常驻多隐藏窗口
        "xig.exe",               # 游戏加加
        "msiafterburner.exe",    # MSI Afterburner
        "rtss.exe",              # RivaTuner Statistics Server
        "nvidia broadcast.exe",  # NVIDIA 广播（录制/overlay）
        "nvidia overlay.exe",    # NVIDIA 游戏 overlay
    })
    if p in _HEAVY_EXCLUDE_PROC_NAMES:
        return ""
    if p in _HEAVY_INSTANT_PROC_NAMES:
        return "instant"
    if "client-win64-shipping" in p or "unityplayer" in p \
            or p.endswith("-win64-shipping.exe") or p in _GAME_ENGINE_PROC_NAMES:
        return "load"
    # 【自学习库（2026-08-27 用户设想）】本地 learned.json / 云端 cloud.json 里
    # 已确认"秒卸档"的进程：首次靠 GPU 负载兜底清理过、之后直接秒卸；云端库让新
    # 用户首启即带全量，全局每个软件最多只坑一个人一次。咨询单例注册表（proactive
    # 初始化时 set_registry），未初始化则跳过（不影响原逻辑）。
    try:
        from .heavy_apps import get_registry
        _reg = get_registry()
        if _reg is not None and _reg.is_instant(p):
            return "instant"
    except Exception:  # noqa: BLE001
        logger.debug("_heavy_app_tier 咨询学习库失败", exc_info=True)
    return ""


def _is_game_proc_name(proc: str) -> bool:
    """兼容包装：是否为真实游戏/重负载进程（任意档，instant/load 均算）。"""
    return _heavy_app_tier(proc) != ""


# EnumWindows 回调类型
_EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

# ============================================================================
# 重入安全的窗口枚举（2026-09-05 修复·进程原生栈溢出崩溃）
# ----------------------------------------------------------------------------
# 现象：异步线程在调用 EnumWindows 期间，若某窗口的 SendMessage（GetWindowTextW 本质）、
# 全局钩子(SetWindowsHookEx)或注入 DLL 在回调/远程进程调用途中再次触发窗口枚举，会造成
# C 栈无限再入，直到原生栈溢出、进程直接崩退。faulthandler 抓到的损坏栈顶是
# <frozen ntpath> splitroot（os.path.basename 内部），且无 setrecursionlimit 提升，
# 纯 Python 递归本应提前抛 RecursionError——故断定是原生层再入而非 Python 自环。
# 该路径是 game_screen_source() 的热路径（game_audio / game_ocr 每轮各调一次），
# 前台快速切换 + 游戏/Overlay/杀软钩子时极易触发再入。
#
# 防御：用线程局部的调用深度计数。回调一旦在自身执行期间被重入（depth>=1），
# 立即 return 且不再执行任何 Win32 远程调用，掐断 C 栈增长；重入时放弃该次回调判定
# （返回 True=继续枚举），对“会议/游戏窗口检测”属安全降级（漏检一个窗口可接受，
# 崩进程不可接受）。线程局部避免多线程并发误判。
# ============================================================================
_enum_local = threading.local()


def _guarded(cb):
    """给 EnumWindows 回调包一层重入保护：重入调用直接放行，不做任何 Win32 远程调用。"""
    def _w(hwnd, lparam):
        if getattr(_enum_local, "depth", 0) >= 1:
            # 重入：本回调在自身执行期间被再次调用（钩子/消息泵再入），立即放行，
            # 不再执行 GetWindowThreadProcessId/OpenProcess/QueryFullProcessImageNameW 等，
            # 防止 C 栈无限再入导致进程崩退。
            return True
        _enum_local.depth = 1
        try:
            return cb(hwnd, lparam)
        finally:
            _enum_local.depth = 0
    return _w


def _enum_windows(cb):
    """重入安全的 EnumWindows：回调被全局钩子/重入再次调用时自动降级，杜绝 C 栈溢出。"""
    ctypes.windll.user32.EnumWindows(_EnumWindowsProc(_guarded(cb)), 0)


def _meeting_running() -> bool:
    """系统中是否有腾讯会议进程在运行（任一顶层窗口属于 wemeet 进程）。

    用户手机屏幕共享打手游时，腾讯会议窗口可能被遮挡/最小化/切到其他窗口，
    前台判定会失败——但只要腾讯会议进程还在（用户很可能仍在手机上打游戏），
    OCR/队伍识别就应持续工作而不是停扫（否则手机里的阵亡/失败会漏检）。
    """
    try:
        found = False

        def _cb(hwnd, lparam):
            nonlocal found
            if _process_is_meeting(hwnd):
                found = True
                return False
            return True

        _enum_windows(_cb)
        return found
    except Exception:  # noqa: BLE001
        logger.debug("枚举腾讯会议进程失败", exc_info=True)
        return False


def _window_at_point_root(x: int, y: int) -> Optional[int]:
    """返回屏幕点 (x, y) 处最顶层可见窗口的【根窗口】句柄；无窗口返回 None。

    WindowFromPoint 返回包含该点的最深层子窗口，GetAncestor(GA_ROOT) 拿到
    它的顶层窗口，再交给 _process_is_meeting 判断进程——用于检测某个矩形
    区域是否真的"露出"在屏幕上（没被其他窗口完全遮挡）。
    """
    try:
        hwnd = ctypes.windll.user32.WindowFromPoint(
            ctypes.wintypes.POINT(ctypes.wintypes.LONG(x), ctypes.wintypes.LONG(y)))
        if not hwnd:
            return None
        root = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
        return root or hwnd
    except Exception:  # noqa: BLE001
        logger.debug("窗口点查询失败", exc_info=True)
        return None


def _meeting_window_rect() -> Optional[tuple[int, int, int, int]]:
    """返回腾讯会议主窗口矩形 (left, top, width, height)；未找到返回 None。

    手机屏幕共享时游戏画面在腾讯会议窗口里，前台可能已切到其他窗口
    （用户在电脑上看聊天/写代码，同时手机打游戏）。要抓到游戏画面，
    必须直接定位腾讯会议窗口，而不是抓"前台窗口"。

    **可见性校验**：会议窗口被其他窗口完全遮挡时（矩形中心点不属于会议
    进程，如用户前台切到 IDE/资源管理器把会议窗口盖住），返回 None——
    否则 mss.grab 抓该矩形区域会抓到遮挡它的窗口内容，OCR 扫到 IDE 代码/
    文件路径文字，误触发击杀/胜负引信（16:55 实锤：切 IDE 时 OCR 把
    "XXX 淘汰了 YYY"代码注释当击杀疑似）。
    """
    try:
        result = None

        def _cb(hwnd, lparam):
            nonlocal result
            if not _process_is_meeting(hwnd):
                return True
            if ctypes.windll.user32.IsIconic(hwnd):  # 最小化窗口无画面可抓
                return True
            rect = ctypes.wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                w, h = rect.right - rect.left, rect.bottom - rect.top
                # 过滤异常小/隐藏窗口（被完全隐藏时矩形可能为 0）
                if w > 200 and h > 150:
                    cx, cy = rect.left + w // 2, rect.top + h // 2
                    top = _window_at_point_root(cx, cy)
                    if top is None or not _process_is_meeting(top):
                        # 会议窗口中心点被其他窗口盖住 → 画面不可见，不抓
                        return True
                    result = (rect.left, rect.top, w, h)
                    return False
            return True

        _enum_windows(_cb)
        return result
    except Exception:  # noqa: BLE001
        logger.debug("获取腾讯会议窗口矩形失败", exc_info=True)
        return None


# 标题"实质"提取：去掉会随刷新跳动的部分（尾随数字、括号数字、常见 volatile 词），
# 用于判断"同一个应用里是不是换了内容"（换视频/换标签页），而非单纯计数在涨。
# 例："Video A (1,234) - YouTube" 与 "Video A (1,235) - YouTube" → 同核（计数跳动不算）；
#     "Video A - YouTube" 与 "Video B - YouTube" → 异核（内容确实换了）。
_VOLATILE_TOKENS = ("watching", "online", "live", "直播", "在线", "观看中", "人气", "关注")


def _title_core(title: str) -> str:
    if not title:
        return ""
    t = title.strip().lower()
    # 去掉所有数字组（含千分位逗号）与常见 volatile 词
    t = _re.sub(r"\d+(?:[.,]\d+)*", "", t)
    for tok in _VOLATILE_TOKENS:
        t = t.replace(tok, " ")
    # 去多余空白与尾随标点
    t = _re.sub(r"[\s\-_|·•·:：]+$", "", t)
    t = _re.sub(r"\s+", " ", t).strip()
    return t


def hint_from_title(title: str, rules: Optional[list[tuple[str, tuple[str, ...]]]] = None) -> str:
    """从窗口标题确定性判断应用类型，识别不出返回空串。"""
    t = (title or "").lower()
    for hint, keywords in (rules or _TITLE_RULES):
        for kw in keywords:
            if kw.lower() in t:
                return hint
    return ""


def _system_idle_seconds() -> Optional[float]:
    """系统级空闲秒数（键盘/鼠标最后一次输入至今），失败返回 None。"""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (ticks - info.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001
        return None


class ForegroundMonitor:
    """前台窗口轮询器：应用切换 → FOREGROUND_CHANGED 事件；空闲时间查询。"""

    def __init__(self, bus: EventBus, cfg: Config) -> None:
        self._bus = bus
        fg = getattr(cfg.ns.perception, "foreground", None)
        self._enabled: bool = bool(
            getattr(fg, "enabled", True) if fg is not None else True
        ) and os.name == "nt"
        self._poll_interval_s: float = getattr(fg, "poll_interval_s", 1.5) if fg is not None else 1.5

        # 自定义游戏标题关键词（config.yaml perception.foreground.game_titles）
        # 合并进内置游戏规则，用于识别内置列表外的游戏窗口
        extra_titles = [t.strip() for t in (getattr(fg, "game_titles", []) or []) if t.strip()] if fg else []
        self._rules: list[tuple[str, tuple[str, ...]]] = list(_TITLE_RULES)
        if extra_titles:
            self._rules = list(self._rules)
            game_kw = self._rules[0][1] + tuple(extra_titles)
            self._rules[0] = ("game", game_kw)

        self._running: bool = True
        self._last_hwnd: Optional[int] = None
        self._last_title: str = ""
        self._last_title_core: str = ""  # 标题"实质"部分（去尾随数字/标点/ volatile 词），用于同应用内换视频检测
        self._last_full: Optional[bool] = None  # 上次发布的前台全屏状态（用于全屏翻转重发事件）

        # ---- 沉浸判定迟滞（2026-10-06·治「全屏判定抖动」导致 8s 闸门永不满足）----
        # 现象：抖音全屏时 IsZoomed / 面积比会在阈值（88%）附近抖动（双屏拼接、窗口跨屏
        # 移动、标题与 hint 采样噪声），fg_immersive 在 True↔False 之间反复翻转
        # （日志「前台全屏状态翻转」密集出现）。翻转会让 SwitchController._tick 每次都
        # 重置 _candidate_since ⇒ 8 秒永远数不满 ⇒ 永远不提交 ⇒ 「进不去/退不出」。
        # 对策：迟滞（连续 N 次同向才承认翻转）。采样周期 0.4s ⇒ 进入约 0.8s、退出约 1.2s，
        # 远小于 8s 闸门，不会拖慢正常切换，但能吃掉亚秒级抖动。
        self._imm_stable: bool = False   # 迟滞后的稳定值（对外返回）
        self._imm_true_run: int = 0      # 连续「真」次数
        self._imm_false_run: int = 0     # 连续「假」次数
        self._IMM_ENTER_N: int = 2       # 连续几次真才承认"进入沉浸"
        self._IMM_EXIT_N: int = 3        # 连续几次假才承认"退出沉浸"（退出更保守）

        # ---- 感知采样缓存（治本修复·采样线程隔离）----
        # 所有 Win32 枚举只在 fg-sampler 线程上跑，结果写进 _snap；其余查询方法
        # 全部读这份带锁缓存，绝不在事件循环/executor 上做同步枚举 → 根除卡死。
        self._snap_lock = threading.RLock()
        self._snap: dict = {
            "enabled": self._enabled,
            "idle_seconds": None,
            "fg_pid": 0, "fg_title": "", "fg_hint": "", "fg_exe": "",
            "fg_is_meeting": False, "fg_is_unity": False, "fg_is_game": False,
            "fg_is_fullscreen": False, "fg_rect": None,
            "meeting_running": False, "meeting_rect": None,
            "visible_windows": [], "pc_game_window": None,
            "game_screen_source": "none",
            "has_call_window": False, "fg_is_call": False,
            "is_game_process_foreground": False, "heavy_app_tier_foreground": "",
        }
        self._sampler_thread: Optional[threading.Thread] = None
        # 采样周期：足够实时（0.4s），又让枚举频次降到每 2.5s 一次（而非每调用一次）
        self._sample_interval_s: float = 0.4
        self._last_hint: str = ""
        self._last_title_evt_ts: float = 0.0  # 上次发标题变化事件的时间（节流）
        self._first: bool = True  # 启动后第一次采集只记录，不发布（避免启动噪音）

        # ---- 前台稳定期 + 全屏确认（规则二 2026-09-06·治本）----
        # 前台必须「全屏 + 连续保持 _STABLE_S 秒」才认定为"真切换"并发布 FOREGROUND_CHANGED；
        # 中途失去全屏即重置计时。非全屏的 QQ↔微信 等每秒级抖动永不满足 → 不触发
        # 【2026-09-06 回退·修复】之前为消除 0.1s 闪弹把闸门改成"全屏+连续 8s"，实测
        # 对抖音/UWP 等窗口全屏判定不稳 → FOREGROUND_CHANGED 几乎永不发布 → 陪伴提示词
        # 注入、游戏陪伴卡全部失效。现回退为【前台变化 + 1.2s 短去抖】即发布（吸收 Alt-Tab
        # 亚秒级抖动），全屏仅作为事件数据(is_fullscreen)由弹窗侧 _fs 自行决定是否弹，
        # 不再卡死事件本身。
        self._STABLE_S: float = 1.2
        self._cand_hint: Optional[str] = None    # 尚未稳定的候选前台 hint
        self._cand_title: str = ""
        self._cand_title_core: str = ""
        self._cand_since: float = 0.0            # 候选首次出现时间
        self._cand_full: bool = False            # 候选是否处于全屏（规则二：需连续全屏满稳定期）

        # 调试统计
        self._idle_seconds: Optional[float] = None
        self._switch_count: int = 0
        self._last_switch_ts: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def current_hint(self) -> str:
        """当前前台应用类型（采样缓存，供 OCR 等模块判断前台应用）。"""
        return self._snap.get("fg_hint", "")

    @property
    def last_title(self) -> str:
        """最近一次前台窗口标题（采样缓存，只读，供调试/日志显示）。"""
        return self._snap.get("fg_title", "")

    @property
    def is_livestream(self) -> bool:
        """当前前台是否在看直播（主播对观众说话，不是与用户对话）。

        用于让双工在直播时只做旁观点评、不接主播话茬。判定：
        - 纯直播平台（斗鱼/虎牙/YY直播/twitch），不限前台类型；
        - 浏览器类（抖音/B站/快手/网页）且标题含"直播/直播间/直播中"。
        """
        if not self._enabled:
            return False
        t = (self._snap.get("fg_title", "") or "").lower()
        if any(kw in t for kw in ("douyu", "斗鱼", "huya", "虎牙", "yy直播", "twitch")):
            return True
        if self._snap.get("fg_hint", "") == "browser" and any(
                kw in t for kw in ("直播", "直播间", "直播中")):
            return True
        return False

    @_sampled("fg_is_meeting", False)
    def is_meeting_foreground(self) -> bool:
        """当前前台窗口是否属于腾讯会议（手机屏幕共享场景）。

        手机版《三角洲》HUD 在画面右上角，队伍识别用它区分 PC/手机共享。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            return _process_is_meeting(hwnd)
        except Exception:  # noqa: BLE001
            return False

    @_sampled("meeting_running", False)
    def is_meeting_running(self) -> bool:
        """腾讯会议进程是否在运行（不要求是前台窗口）。

        用户手机屏幕共享打手游时，电脑前台常切到其他窗口（看聊天/写代码），
        但只要腾讯会议还开着（用户仍在手机上打游戏），OCR/队伍识别应持续工作，
        否则手机里的阵亡/失败/击杀会漏检。
        """
        return _meeting_running()

    @_sampled("fg_is_fullscreen", False)
    def is_foreground_fullscreen(self) -> bool:
        """当前前台窗口是否"全屏/最大化覆盖所在显示器"（专属陪伴切换判定用）。

        判定为"沉浸在该软件"的两种情况：
          1. 窗口已最大化（IsZoomed）—— 最常见的"全屏"形态（QQ/浏览器/IDE 最大化）；
          2. 窗口矩形覆盖整块显示器（rcMonitor）—— 独占全屏游戏 / DX 全屏。
        两者任一即视为沉浸，避免 alt-tab 切到小窗就误触发换陪伴/弹窗。
        多显示器安全：取窗口所在显示器矩形比较。任意异常一律返回 False。

        【2026-09-05 修复·最大化判定失效】原实现只比"窗口矩形与显示器矩形差 ≤8px"，
        但 Windows 对【最大化】窗口的 GetWindowRect 返回的是"显示器矩形 + 屏幕外边框"
        （边框被推出屏幕），差值常 >8px → rcWork/rcMonitor 都不命中 → 返回 False →
        QQ 等最大化窗口永不被判为全屏，专属陪伴弹出被卡死。现优先用 IsZoomed 直接
        识别最大化（最稳，不依赖矩形诡异行为），再用矩形兜底真·独占全屏。
        【2026-09-06 诊断】抖音/视频类全屏判定加节流日志，定位"全屏了却没判全屏"的断点。
        """
        if not self._enabled:
            return False
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            # ① 最大化窗口 = 占满工作区 = 沉浸（覆盖绝大多数"全屏"诉求，且不依赖
            # GetWindowRect 对最大化窗口返回"含屏幕外边框"矩形的诡异行为）。
            _zoomed = False
            try:
                _zoomed = bool(user32.IsZoomed(hwnd))
            except Exception:  # noqa: BLE001
                pass
            if _zoomed:
                self._fs_diag(zoomed=True, covers=False, big=False,
                              reason="IsZoomed", result=True)
                return True
            _wr = ctypes.wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(_wr)):
                return False
            mh = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if not mh:
                return False
            _mi = _MONITORINFO()
            _mi.cbSize = ctypes.sizeof(_mi)
            if not user32.GetMonitorInfoW(mh, ctypes.byref(_mi)):
                return False
            # ② 真·独占全屏：窗口矩形 ≈ 整块显示器（含任务栏位置）即可。
            tol = 8

            def _covers(r):
                return (abs(_wr.left - r.left) <= tol and abs(_wr.top - r.top) <= tol
                        and abs(_wr.right - r.right) <= tol
                        and abs(_wr.bottom - r.bottom) <= tol)

            _coversv = _covers(_mi.rcWork) or _covers(_mi.rcMonitor)
            if _coversv:
                self._fs_diag(zoomed=False, covers=True, big=False,
                              reason="covers_monitor", result=True)
                return True
            # ③ 兜底：窗口宽高 ≥ 显示器 95% 也视为沉浸（部分游戏伪全屏留极小边、
            # 或多显示器下窗口略小于整屏但明显占满用户视野）。纯小窗（<95%）不算。
            mt = _mi.rcMonitor
            _big = ((_wr.right - _wr.left) >= (mt.right - mt.left) * 0.95
                    and (_wr.bottom - _wr.top) >= (mt.bottom - mt.top) * 0.95)
            self._fs_diag(zoomed=False, covers=_coversv, big=_big,
                          reason="big>=95%" if _big else "not_fullscreen", result=bool(_big))
            return bool(_big)
        except Exception:  # noqa: BLE001
            return False

    def _is_immersive_raw(self) -> bool:
        """「专属陪伴弹窗」用的沉浸判定：最大化 / 铺满工作区 / 铺满显示器 任一即沉浸。

        比 is_foreground_fullscreen 更宽——fullscreen 锁的是「独占全屏游戏」语义（游戏锁用，
        见 proactive.py），这里只要"用户真的在用它"即可弹窗：
          ① 窗口最大化（IsZoomed）—— 标准 Windows 窗口最大化一定拿到；
          ② 覆盖工作区(rcWork) ≥ 88% —— 微信等 Qt 自绘无边框窗口最大化时不走 WS_MAXIMIZE，
             IsZoomed 拿不到，但铺满工作区，靠面积兜底（留极小阴影边框也过）；
          ③ 覆盖整显示器(rcMonitor) ≥ 88% —— 多显示器下窗口略小于整屏但明显占满视野。
        任意异常一律返回 False。

        ⚠️ 这是**原始判定**，会抖动。外部请调 is_foreground_immersive()（带迟滞）。
        """
        if not self._enabled:
            return False
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            # ① 最大化 = 沉浸（最稳，不依赖矩形诡异行为）
            try:
                if bool(user32.IsZoomed(hwnd)):
                    self._fs_diag(zoomed=True, covers=False, big=True,
                                  reason="IsZoomed", result=True)
                    return True
            except Exception:  # noqa: BLE001
                pass
            _wr = ctypes.wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(_wr)):
                return False
            mh = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if not mh:
                return False
            _mi = _MONITORINFO()
            _mi.cbSize = ctypes.sizeof(_mi)
            if not user32.GetMonitorInfoW(mh, ctypes.byref(_mi)):
                return False
            _ww = max(0, _wr.right - _wr.left)
            _wh = max(0, _wr.bottom - _wr.top)
            if _ww <= 0 or _wh <= 0:
                return False
            _thr = 0.88

            def _ratio(r: ctypes.wintypes.RECT) -> float:
                _a = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                return (_ww * _wh) / _a if _a > 0 else 0.0

            _r_work = _ratio(_mi.rcWork)
            _r_mon = _ratio(_mi.rcMonitor)
            _immersive = _r_work >= _thr or _r_mon >= _thr
            self._fs_diag(zoomed=False, covers=(_r_mon >= 0.99), big=_immersive,
                          reason=("work>=88%" if _r_work >= _thr
                                  else ("monitor>=88%" if _r_mon >= _thr else "not_immersive")),
                          result=_immersive)
            return _immersive
        except Exception:  # noqa: BLE001
            return False

    @_sampled("fg_immersive", False)
    def is_foreground_immersive(self) -> bool:
        """带迟滞的沉浸判定（对外唯一入口）——治"全屏抖动把 8s 闸门打永不满足"。

        原始判定 _is_immersive_raw 在阈值附近会反复 True↔False（双屏拼接/窗口跨屏/
        采样噪声）。这里加**迟滞**：连续 _IMM_ENTER_N 次真才承认进入、连续
        _IMM_EXIT_N 次假才承认退出。采样周期 0.4s ⇒ 约 0.8s 进 / 1.2s 出，
        远小于 SwitchController 的 8s 闸门，不拖慢正常切换。

        ⚠️ 状态只由采样线程写（`_sampled` 保证 live 路径只在 fg-sampler 线程跑），
        其他线程读缓存，无竞态。
        """
        if not self._enabled:
            # 感知关闭：清状态并回落 False，避免下次启用时残留旧稳定值
            self._imm_stable = False
            self._imm_true_run = self._imm_false_run = 0
            return False
        try:
            raw = self._is_immersive_raw()
        except Exception:  # noqa: BLE001
            raw = False

        if raw == self._imm_stable:
            # 一致：清计数（迟滞只在"想翻转"时累积）
            self._imm_true_run = 0
            self._imm_false_run = 0
            return self._imm_stable

        if raw:
            self._imm_true_run += 1
            self._imm_false_run = 0
            if self._imm_true_run >= self._IMM_ENTER_N:
                self._imm_stable = True
                self._imm_true_run = 0
                logger.info("沉浸判定迟滞：连续 %d 次采样为沉浸 → 认定进入沉浸",
                            self._IMM_ENTER_N)
        else:
            self._imm_false_run += 1
            self._imm_true_run = 0
            if self._imm_false_run >= self._IMM_EXIT_N:
                self._imm_stable = False
                self._imm_false_run = 0
                logger.info("沉浸判定迟滞：连续 %d 次采样为非沉浸 → 认定退出沉浸",
                            self._IMM_EXIT_N)
        return self._imm_stable

    def _fs_diag(self, zoomed: bool, covers: bool, big: bool, reason: str, result: bool) -> None:
        """抖音/视频类全屏判定节流诊断（≥5s 一次）：仅当前台属浏览器/视频类才打。

        用于定位「用户说全屏了但 _fs 返回 False」的断点——直接看出是 IsZoomed 没命中、
        还是矩形 covers/big 都没达阈值。
        """
        try:
            if not getattr(self, "_enabled", False):
                return
            _t = (self._snap.get("fg_title", "") or "").lower()
            _h = (self._snap.get("fg_hint", "") or "")
            _need = (_h in ("browser", "chat")) or any(
                k in _t for k in ("抖音", "douyin", "bilibili", "哔哩", "快手", "kuaishou",
                                 "youtube", "腾讯视频", "爱奇艺", "优酷", "直播",
                                 "微信", "wechat", "weixin", "qq", "腾讯"))
            if not _need:
                return
            _now = time.time()
            if _now - getattr(self, "_fs_diag_ts", 0.0) < 5.0:
                return
            self._fs_diag_ts = _now
            logger.info("[抖音/视频全屏诊断] 标题=%r hint=%s IsZoomed=%s covers=%s big>=95%%=%s → %s (%s)",
                        (self._snap.get("fg_title", "") or "")[:50], _h,
                        zoomed, covers, big, result, reason)
        except Exception:  # noqa: BLE001
            pass

    @_sampled("visible_windows", [])
    def visible_windows(self) -> list[dict]:
        """枚举当前屏幕上所有可见的顶层窗口（窗口识别器）。

        返回 [{hwnd, title, hint, is_meeting, topmost, rect}]，过滤掉桌面/
        任务栏/隐藏/最小化/无标题窗口。用于判断"同屏/置顶有几个软件"，从而
        定位 OCR 画面文字到底来自哪个窗口——比如抖音视频里出现"三角洲行动"
        字样只是视频内容，不是用户真在玩游戏。
        """
        # 两步式枚举（2026-09-05 修复）：回调内只做本地、无消息泵的 Win32 调用
        # （IsWindowVisible/IsIconic/GetWindowRect/GetWindowLongW）。**绝不**在回调里
        # 调 GetWindowTextW——其本质是 SendMessage，会泵送调用方消息队列，在 EnumWindows
        # 期间造成重入/死锁，是 C 栈溢出的直接诱因之一。标题与会议判定延后到枚举结束
        # 后逐个取，彻底消除“SendMessage-in-EnumWindows”再入风险。
        raw: list[tuple] = []

        def _cb(hwnd, lparam):
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            if ctypes.windll.user32.IsIconic(hwnd):
                return True
            rect = ctypes.wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w <= 0 or h <= 0 or w > 8192 or h > 8192:
                return True
            cls = _window_class(hwnd).lower()
            if cls in ("progman", "shelldll_defview", "shell_traywnd"):
                return True
            # WS_EX_TOPMOST 置顶窗口
            topmost = bool(
                ctypes.windll.user32.GetWindowLongW(hwnd, -8) & 0x00000008)
            raw.append((hwnd, topmost, rect.left, rect.top, w, h))
            return True

        try:
            _enum_windows(_cb)
        except Exception:  # noqa: BLE001
            logger.debug("枚举可见窗口失败", exc_info=True)
        wins: list[dict] = []
        for hwnd, topmost, l, t, w, h in raw:
            try:
                # 枚举外取标题：不再有任何 SendMessage-in-EnumWindows 风险
                title = _window_title(hwnd)
                if not title.strip():
                    continue
                wins.append({
                    "hwnd": hwnd,
                    "title": title,
                    "hint": hint_from_title(title, self._rules),
                    "is_meeting": _process_is_meeting(hwnd),
                    "topmost": topmost,
                    "rect": (l, t, w, h),
                })
            except Exception:  # noqa: BLE001
                logger.debug("读取窗口标题失败", exc_info=True)
        return wins

    @_sampled("game_screen_source", "none")
    def game_screen_source(self) -> str:
        """当前屏幕上"游戏画面"的可信来源：'meeting' / 'pc_game' / 'none'。

        - 'meeting'：腾讯会议进程在运行（**白名单**）。用户手机投屏打手游时
          游戏画面来自腾讯会议窗口，OCR/左上角识别都可信。
        - 'pc_game'：前台是 PC 版游戏窗口（含腾讯会议在前台被映射成 game 的情况），
          或**前台非游戏但游戏窗口还在**（被遮挡/切走后，OCR 用 PrintWindow
          后台抓取窗口交换链内容——对标"切换屏幕后仍识别游戏"）。
        - 'none'：其他场景（抖音/浏览器/IDE 里出现"三角洲行动"字样只是视频/
          文章内容，不是用户真在玩）→ 画面文字不可当作游戏内容。
        OCR 层拿它决定是否把屏幕文字当游戏画面处理，杜绝"看抖音三角洲视频
        触发游戏识别"的误判。
        """
        # 轻量缓存（2026-09-05）：本函数是原生栈溢出崩溃的触发热路径——内部多次
        # EnumWindows + OpenProcess/QueryFullProcessImageNameW，而 game_audio 与 game_ocr
        # 每轮各调一次，前台快速切换时高频枚举。0.25s TTL 既保证“切应用”实时性，
        # 又把 Win32 枚举频次与重入暴露窗口压到极低，从源头减少崩溃触发面。
        _now = time.time()
        _cache = getattr(self, "_gsrc_cache", None)
        if _cache is not None and _now - _cache[0] < 0.25:
            return _cache[1]
        # 重入保护（2026-09-05）：若本函数在自身 Win32 调用途中被钩子/消息泵再次进入
        # （导致 C 栈无限再入崩溃），立即返回上一次缓存值，掐断嵌套。先查缓存再查重入，
        # 保证正常高频调用走缓存、异常重入走降级，互不影响。
        if getattr(_enum_local, "gsrc", 0) >= 1:
            return _cache[1] if _cache is not None else "none"
        _enum_local.gsrc = 1
        try:
            if self.is_meeting_running():
                v = "meeting"
            elif self.is_game_foreground():
                v = "pc_game"
            elif self.pc_game_window() is not None:
                # 前台切到其他窗口但游戏窗口还在（被遮挡）→ 后台识别游戏窗口
                v = "pc_game"
            else:
                v = "none"
        finally:
            _enum_local.gsrc = 0
        self._gsrc_cache = (_now, v)
        return v

    @_sampled("pc_game_window", None)
    def pc_game_window(self) -> Optional[dict]:
        """返回当前可见的 PC 版游戏窗口（hint=='game' 且非腾讯会议、非最小化）。

        前台切到软件/IDE 后游戏窗口还在（可能被遮挡），后台识别要直接抓这个
        窗口的交换链内容（PrintWindow，不经过屏幕，被遮挡也能抓），而不是
        待命停扫——对标"切换屏幕后仍能识别游戏"。
        返回第一个匹配窗口 {hwnd, title, rect}；无则 None。
        """
        for w in self.visible_windows():
            if w.get("is_meeting"):
                continue
            if w.get("hint") != "game":
                continue
            return w
        return None

    def window_overview(self) -> str:
        """同屏窗口概览（供心跳/调试台诊断 OCR 文字来源）。"""
        wins = self.visible_windows()
        if not wins:
            return "无可见窗口"
        parts: list[str] = []
        meetings = [w for w in wins if w["is_meeting"]]
        games = [w for w in wins if not w["is_meeting"] and w["hint"] == "game"]
        if meetings:
            parts.append(f"会议x{len(meetings)}")
        if games:
            parts.append(f"游戏窗口x{len(games)}")
        others = [w for w in wins if not w["is_meeting"] and w["hint"] != "game"]
        if others:
            hints: dict[str, int] = {}
            for w in others:
                h = w["hint"] or "unknown"
                hints[h] = hints.get(h, 0) + 1
            parts.append("其他=" + ",".join(f"{k}x{v}" for k, v in sorted(hints.items())))
        return f"同屏窗口{len(wins)}个 ({'; '.join(parts)})"

    @_sampled("meeting_rect", None)
    def meeting_rect(self) -> Optional[tuple[int, int, int, int]]:
        """腾讯会议窗口矩形 (left, top, width, height)；未找到返回 None。

        手机共享场景下游戏画面在腾讯会议窗口里，抓这个矩形才能拿到游戏内容
        （前台可能已不是腾讯会议，foreground_rect 不可用）。
        """
        return _meeting_window_rect()

    @_sampled("fg_rect", None)
    def foreground_rect(self) -> Optional[tuple[int, int, int, int]]:
        """返回前台窗口在屏幕上的矩形 (left, top, width, height)。

        用于抓屏裁剪：游戏窗口化/无边框时只抓窗口区域，
        避免桌面图标、任务栏、其他窗口文字混进 OCR/视觉（如"无畏契约"图标被误识别）。
        全屏游戏时矩形 = 整屏，无差别。获取失败返回 None（调用方回退整屏）。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return None
            rect = ctypes.wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            w, h = rect.right - rect.left, rect.bottom - rect.top
            # 最小化/隐藏窗口矩形可能为 0 或异常巨大，视为无效
            if w <= 0 or h <= 0 or w > 8192 or h > 8192:
                return None
            return rect.left, rect.top, w, h
        except Exception:  # noqa: BLE001
            logger.debug("获取前台窗口矩形失败", exc_info=True)
            return None

    @_sampled("fg_is_unity", False)
    def is_unity_foreground(self) -> bool:
        """当前前台窗口是否为 Unity（本软件 3D 模型窗口，不是用户活动）。

        Unity 编辑器进程名统一是 unity.exe（无论项目叫什么）；打包后的
        Unity 应用进程名是自定义的，用标题兜底（Unity 打包窗口标题默认
        为项目名，游戏画面场景下通常含模型/项目名，标题判定宽松命中）。
        供视觉分析器在 Unity 前台时跳过分析——伴侣自己的画面不进感知。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            if _process_is_unity(hwnd):
                return True
            title = _window_title(hwnd)
            t = (title or "").lower()
            # 编辑器标题必含 "unity"；打包应用窗口标题是项目名（无固定特征），
            # 但至少不能把普通用户窗口误判成 Unity——只认标题里明确带 unity 的。
            return "unity" in t or " - unity " in t or t.startswith("unity")
        except Exception:  # noqa: BLE001
            logger.debug("Unity 前台判定失败，退回 False", exc_info=True)
            return False

    @_sampled("fg_is_game", False)
    def is_game_foreground(self) -> bool:
        """实时判断当前前台窗口是否为游戏（重新读取窗口标题，不依赖轮询缓存）。

        供 OCR 扫描前即时校验，避免窗口切换瞬间用滞后的轮询缓存误扫非游戏界面。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            title = _window_title(hwnd)
            # 标题瞬间读不到（游戏切屏/加载窗口）→ 沿用轮询缓存，避免 OCR 停扫
            if not title:
                return self._last_hint == "game"
            hint = hint_from_title(title, self._rules)
            if not hint and _process_is_meeting(hwnd):
                hint = "game"  # 腾讯会议开会时标题变成"xxx的会议"，按进程名兜底
            # 标题含游戏词但进程是文件资源管理器等非游戏程序 → 不算游戏前台，
            # 否则用户打开 "Delta Force" 文件夹/录屏面板就会误锁快照。
            if hint == "game" and not _process_is_meeting(hwnd):
                proc = _process_name(hwnd)
                if proc in _NON_GAME_FOREGROUND_PROC:
                    logger.debug("前台标题含游戏词但进程=%s 非游戏，不判为游戏前台", proc)
                    return False
                # 【2026-09-04 修复】启动器/更新器窗口标题常就是游戏名（鸣潮启动器
                # launcher_main.exe 标题"鸣潮"）。若在此判为游戏前台，会出现
                # "陪伴方案/游戏知识切到该游戏、提示词按游戏加载，但高负载锁用的
                # 是真实游戏进程→不切云端"的错配（本地双工跑游戏提示词）。
                # 这里统一口径：启动器不算真在打游戏。
                if _is_launcher_proc(proc):
                    logger.info(
                        "前台标题含游戏词但进程=%s 是启动器/更新器（非真游戏进程），"
                        "不判为游戏前台——避免陪伴方案与模式切换错配", proc)
                    return False
            return hint == "game"
        except Exception:  # noqa: BLE001
            logger.debug("实时前台判定失败，退回轮询缓存", exc_info=True)
            return self._last_hint == "game"

    @_sampled("is_game_process_foreground", False)
    def is_game_process_foreground(self) -> bool:
        """按前台进程 exe 判定是否真在打游戏（补充 is_game_foreground 的标题分类）。

        覆盖未登记到 _TITLE_RULES 的游戏引擎进程（如鸣潮/绝区零用的
        Client-Win64-Shipping.exe）。这类进程只在真打游戏时出现，看游戏视频
        （抖音/浏览器）不会命中，故不会误锁快照。失败安全降级返回 False。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            return _is_game_proc_name(_process_name(hwnd))
        except Exception:  # noqa: BLE001
            logger.debug("前台进程游戏判定失败", exc_info=True)
            return False

    def is_game_process_running(self) -> bool:
        """系统中是否有真实游戏进程在运行（枚举所有顶层窗口，不限于前台）。

        与 is_game_process_foreground 的区别：后者只看【前台】窗口（用户必须切到
        游戏），前者看"有没有游戏进程在跑"——用户开着游戏但切到别处（看聊天/二屏/
        最小化）也能命中。对应需求：鸣潮一启动就该腾显存，不必等它抢到前台焦点。
        启动器进程（Wuthering Waves.exe 等）不在 _GAME_PROC_NAMES 也不含 shipping
        → 不命中，故启动器不会误触（与鸣潮真进程 Client-Win64-Shipping.exe 区分）。
        失败安全降级返回 False。
        """
        try:
            found = False

            def _cb(hwnd, lparam):  # noqa: ANN001
                nonlocal found
                if _is_game_proc_name(_process_name(hwnd)):
                    found = True
                    return False  # 找到即停止枚举
                return True

            _enum_windows(_cb)
            return found
        except Exception:  # noqa: BLE001
            logger.debug("枚举游戏进程失败", exc_info=True)
            return False

    def detect_new_heavy_process(self) -> Optional[str]:
        """事件驱动式"新游戏/3D 进程诞生拦截"（GamePP 同款思路，前后台无关）。

        维护一个已见过的窗口进程 PID 集合；每次调用枚举所有顶层窗口的进程名，
        若发现【新出现】且命中游戏/3D 引擎特征(_is_game_proc_name)的进程 → 返回
        其进程名（调用方据此立即锁+卸模型）。进程退出后其 PID 不再出现，下次回到
        已见集合，不会重复触发。

        为什么比"前台/是否在跑"更好：
        - is_game_foreground 只看【前台】窗口 → 用户开着游戏看聊天时不命中；
        - is_game_process_running 只看"是否在跑" → 无法区分"刚启动"与"已跑很久"，
          且每次轮询都返回 True 无法作为"诞生瞬间"事件；
        - 本方法抓住"诞生这一刻"，实现"一有高性能软件启动就卸模型"，且不需要
          用户把游戏切到前台。

        失败安全降级返回 None（不触发）。无新游戏进程也返回 None。
        """
        try:
            if not hasattr(self, "_seen_pids"):
                self._seen_pids = None  # 首次调用仅建库，不触发
            cur: set = set()
            found_new: Optional[str] = None

            def _cb(hwnd, lparam):  # noqa: ANN001
                nonlocal found_new
                pid = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if not pid.value:
                    return True
                cur.add(int(pid.value))
                if found_new is None and self._seen_pids is not None \
                        and int(pid.value) not in self._seen_pids:
                    name = _process_name(hwnd)
                    _nl = name.lower()
                    # 【2026-09-06 修复·自锁根因】跳过软件自身运行时(python.exe/
                    # pythonw.exe)，避免把 companion 自己的进程误判成"新诞生的秒卸档游戏"。
                    if _nl in ("python.exe", "pythonw.exe"):
                        return True
                    # 仅【秒卸档(instant)】触发即时卸：具体大作/剪辑/3D/AI/转码。
                    # 负载档(load，如通用引擎小游戏)不秒卸，交给 GPU 负载驱动判定，
                    # 实现"小游戏不卸、大作卸"（见 proactive 的 heavy 逻辑）。
                    if _heavy_app_tier(name) == "instant":
                        found_new = name
                return True

            _enum_windows(_cb)
            self._seen_pids = cur  # 无论是否首次都更新为当前快照
            return found_new
        except Exception:  # noqa: BLE001
            logger.debug("新进程拦截失败", exc_info=True)
            return None

    def heavy_app_tier_running(self) -> str:
        """系统中是否有重负载进程在跑，返回最高档（'instant' > 'load' > ''）。

        供状态机区分"具体大作/剪辑/AI（秒卸档）"与"通用引擎（负载档，靠 GPU 分大小）"。
        """
        try:
            best = ""

            def _cb(hwnd, lparam):  # noqa: ANN001
                nonlocal best
                _n = _process_name(hwnd).lower()
                # 【2026-09-06 修复·自锁根因】软件自身运行时(python.exe/pythonw.exe)绝不能
                # 当成高压游戏：否则一启动就判「库中窗口在跑=True」→ 误锁云端，且因自身进程
                # 常驻而 heavy 永远为 True → 锁上后永不解锁 → 表现就是「切换路径全死、卡在云端」。
                if _n in ("python.exe", "pythonw.exe"):
                    return True
                # 【2026-10-02 修复·浏览器被当游戏（实测误判链）】日志实锤：
                #   游戏锁决策(规则一·库+GPU兜底) 命中信号 里 唯一为真的就是「库中窗口在跑=True」，
                #   而当时前台是 msedgewebview2.exe（= 本软件界面用的 WebView2 宿主），
                #   已识别游戏=False / 前台游戏进程=False / GPU重负载=False。
                #   ⇒ 浏览器/WebView 宿主被算成 load 档 → 开机 3 秒就误判"在玩游戏"
                #      → 卸载本地 Comni（本地看屏互动被关掉）→ 之后重载还 180s 超时。
                # 浏览器看视频/看网页是**桌宠看屏互动**的典型场景，绝不能算游戏。
                # 故排除浏览器/宿主类进程（它们本来也不该触发"让显卡"）。
                if _n in _BROWSER_HOSTS:
                    return True
                # 【2026-10-02 修复·WorkBuddy 被当游戏（用户实锤）】用户开着 WorkBuddy 时，
                # 日志实锤：`游戏锁决策 目标=云端陪伴 | 前台进程='workbuddy.exe' | 已识别游戏=False
                # 前台游戏进程=False GPU重负载=False 库中窗口在跑=True` ⇒ 唯一命中信号是
                # "库中窗口在跑"，于是本地 Comni 被卸载、切云端，而云端又起不来 → 桌宠直接消失。
                # 宿主/开发工具（IDE/终端/本软件自身）都属"用户的工作环境"，绝不能当游戏。
                if _n in _HOST_TOOL_HOSTS:
                    return True
                t = _heavy_app_tier(_n)
                if t == "instant":
                    best = "instant"
                    return False  # 找到最高档即停止
                if t == "load" and best != "instant":
                    best = "load"
                return True

            _enum_windows(_cb)
            return best
        except Exception:  # noqa: BLE001
            logger.debug("重负载进程档位枚举失败", exc_info=True)
            return ""

    def window_procs(self) -> list:
        """当前系统**所有顶层窗口**的进程名列表（小写、去重、排除本程序/宿主）。

        与 `heavy_window_procs` 的区别：**不按"重负载档"过滤**。用途是把"在跑的窗口"
        反查成知识库条目（游戏 `GameKnowledge` / 软件 `AppKnowledge` 都可），当前台被
        WeGame/explorer/宿主抢占、或某些游戏进程名不匹配重负载规则（如 cs2.exe、
        dota2.exe、r5apex.exe）时，仍能认出到底在玩哪款游戏。
        ⚠️ 与 `heavy_app_tier_running` 同风格：**不加 @_sampled**，由调用方经
        `asyncio.to_thread` 调用（绝不在事件循环线程上跑 Win32 枚举）。
        """
        names: list = []
        try:
            def _cb(hwnd, lparam):  # noqa: ANN001
                try:
                    _n = (_process_name(hwnd) or "").lower()
                    if (_n and _n not in ("python.exe", "pythonw.exe")
                            and _n not in names):
                        names.append(_n)
                except Exception:  # noqa: BLE001
                    pass
                return True

            _enum_windows(_cb)
        except Exception:  # noqa: BLE001
            logger.debug("窗口进程枚举失败", exc_info=True)
        return names

    def heavy_window_procs(self) -> list:
        """当前系统里"重负载档"窗口的进程名列表（instant/load，去重，小写）。

        用途：游戏锁在"前台是启动器(WeGame)/explorer/本程序，靠前台进程名认不出游戏"
        时，用"在跑的窗口"反查是哪款游戏（`GameKnowledge.detect('', exe)`），
        从而写出正确的弹窗文案（"让给 三角洲行动"）并注入对应游戏的三段式提示词。
        ⚠️ 与 heavy_app_tier_running 同风格：**不加 @_sampled**，由调用方经
        `asyncio.to_thread` 调用（绝不在事件循环线程上跑 Win32 枚举）。
        """
        names: list = []
        try:
            def _cb(hwnd, lparam):  # noqa: ANN001
                try:
                    _n = (_process_name(hwnd) or "").lower()
                    if (_n and _n not in ("python.exe", "pythonw.exe")
                            and _n not in names and _heavy_app_tier(_n)):
                        names.append(_n)
                except Exception:  # noqa: BLE001
                    pass
                return True

            _enum_windows(_cb)
        except Exception:  # noqa: BLE001
            logger.debug("重负载窗口进程枚举失败", exc_info=True)
        return names

    @_sampled("heavy_app_tier_foreground", "")
    def heavy_app_tier_foreground(self) -> str:
        """前台窗口进程的重负载档位（instant/load/''）。"""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ""
            return _heavy_app_tier(_process_name(hwnd))
        except Exception:  # noqa: BLE001
            return ""

    @_sampled("fg_exe", "")
    def foreground_proc_name(self) -> str:
        """当前前台窗口所属进程的可执行文件名（小写）；失败返回空串。

        供 GPU 负载兜底(_dx_high)识别"元凶进程"：DX/Vulkan 游戏不吃 CUDA compute-
        apps，nvidia-smi 查不到进程名，只能用"前台正在跑的那个应用"近似。首遇有效，
        学到后该进程即进秒卸档，后续不再依赖此法。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ""
            return _process_name(hwnd)
        except Exception:  # noqa: BLE001
            return ""

    def is_process_running(self, exe: str) -> bool:
        """判断给定 exe 的进程当前是否仍在运行（任一顶层窗口属于该进程即算在跑）。

        供「软件进程被终止 → 立即切回对应陪伴」使用：8 秒稳定闸门的例外——软件已被
        彻底关闭时再等 8 秒毫无意义，应立即切。判定基于"该进程是否还有顶层窗口"：
        最小化/后台运行的软件仍有窗口，不会被误判为终止。

        ⚠️ 不走 @_sampled：本方法由 SwitchController 的独立线程调用（非 fg-sampler），
        且只在"确有已提交陪伴软件"时才枚举，开销可忽略。枚举失败保守返回 True——
        宁可走 8 秒正常流程，也不要误判终止导致立即切换。
        """
        if not exe:
            return False
        _e = (exe or "").lower().strip()
        try:
            found = False

            def _cb(hwnd, lparam):  # noqa: ANN001
                nonlocal found
                try:
                    if (_process_name(hwnd) or "").lower() == _e:
                        found = True
                        return False
                except Exception:  # noqa: BLE001
                    pass
                return True

            _enum_windows(_cb)
            return found
        except Exception:  # noqa: BLE001
            logger.debug("枚举进程 %s 是否在跑失败", exe, exc_info=True)
            return True

    @_sampled("fg_title", "")
    def foreground_title(self) -> str:
        """当前前台窗口标题（实时读取，不缓存）；失败返回空串。

        供 proactive 锁状态机的强制游戏锁白名单兜底匹配：全屏/无边框游戏前台判定
        常失效、但窗口标题仍含游戏名（如「三角洲行动」）。配合 fg_game（前台确为
        游戏）使用，不会误锁普通软件。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ""
            return _window_title(hwnd)
        except Exception:  # noqa: BLE001
            return ""

    def is_launched_by_game_platform(self) -> bool:
        """是否正从游戏平台启动器（Steam/Epic/WeGame/…）运行游戏。

        零维护识别"这是游戏"：游戏进程通常是平台 launcher 的直接/间接子进程。
        用于快速通道——平台启动的游戏即便 exe 不在库、GPU 负载还没起来，也提前
        锁+卸（比等 GPU 负载爬升快几秒）。小游戏(2D/轻量)虽也由平台启动，但 GPU
        占用低，状态机会结合负载判定 → 不卸（见 proactive 的 platform_heavy 逻辑）。
        结果缓存 30s（进程表枚举较重，避免每轮轮询拖慢锁循环）。
        """
        try:
            _now = time.time()
            _cache = getattr(self, "_plat_cache", None)
            if _cache is not None and _now - _cache[0] < 30.0:
                return _cache[1]
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress"],
                capture_output=True, text=True, errors="replace", timeout=8,
                creationflags=0x08000000,
            )
            if out.returncode != 0:
                self._plat_cache = (_now, False)
                return False
            import json
            raw = json.loads(out.stdout or "[]")
            procs = raw if isinstance(raw, list) else [raw]
            pid2name = {
                int(p["ProcessId"]): (p.get("Name") or "").lower()
                for p in procs if "ProcessId" in p
            }
            ppid_of = {
                int(p["ProcessId"]): int(p.get("ParentProcessId") or 0)
                for p in procs if "ProcessId" in p
            }
            launcher_pids = {
                pid for pid, name in pid2name.items()
                if name in _PLATFORM_LAUNCHER_PROC_NAMES
            }
            found = False

            def _cb(hwnd, lparam):  # noqa: ANN001
                nonlocal found
                if found:
                    return False
                pid = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if not pid.value:
                    return True
                name = _process_name(hwnd).lower()
                if not name:
                    return True
                # 【2026-09-05 修复·误锁根因】启动器自身(Steam/WeGame/Epic…)常驻后台、
                # 自带窗口 → 原逻辑把"启动器窗口的父链命中启动器"当"正在打游戏"，只要
                # 挂着平台就恒为真 → 叠加 instant 档进程即误切云端（日志实证：externalCUDA
                # =0MB、util=8% 仍被踢到云端陪伴）。故启动器自身的窗口直接跳过，只认
                # "非启动器进程且祖先是启动器"的窗口=真有游戏在跑。
                if name in _PLATFORM_LAUNCHER_PROC_NAMES:
                    return True
                # 向上追溯父链（限 6 层防环），命中 launcher 即视为平台启动游戏
                cur = int(pid.value)
                seen = set()
                for _ in range(6):
                    if cur in launcher_pids:
                        found = True
                        return False
                    if cur in seen:
                        break
                    seen.add(cur)
                    cur = ppid_of.get(cur, 0)
                    if not cur:
                        break
                return True

            _enum_windows(_cb)
            self._plat_cache = (_now, found)
            return found
        except Exception:  # noqa: BLE001
            logger.debug("平台启动器识别失败", exc_info=True)
            return False

    @_sampled("has_call_window", False)
    def has_call_window(self) -> bool:
        """枚举可见窗口，判断是否存在"通话中"的通信/会议窗口（即使非前台）。

        覆盖：后台进行中的 QQ/微信/腾讯会议通话——其通话窗口标题含通话关键词
        （微信/QQ音视频通话、正在通话、xxx的会议等），且属于通信/会议软件。
        供双工静音循环在"前台不是通话软件、但后台有通话窗口"时也能屏蔽声音。
        失败安全降级返回 False。
        """
        try:
            for w in self.visible_windows():
                proc = _process_name(w.get("hwnd") or 0)
                if proc not in _CALL_PROC_NAMES and proc not in _COMM_CALL_PROC_NAMES:
                    continue
                title = (w.get("title") or "")
                t = title.lower()
                if any(k in t for k in _CALL_TITLE_KW):
                    return True
            return False
        except Exception:  # noqa: BLE001
            logger.debug("通话窗口枚举判定失败", exc_info=True)
            return False

    @_sampled("fg_is_call", False)
    def is_call_active(self) -> bool:
        """实时判断当前是否处于音视频通话场景（重新读取窗口，不依赖缓存）。

        看软件窗口判定（标题含"通话" / 进程为会议·语音软件），不用音频关键词——
        通话时 WASAPI 环回会把对方的声音也采集进双工推流，模型会把好友的话当成
        用户的话去回应（"NTR"）。双工据此停采环回音频并强制闭嘴，通话结束恢复。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            # 会议/语音软件前台（腾讯会议开会中、投屏打手游、语音频道挂机等）
            proc = _process_name(hwnd)
            if proc in _CALL_PROC_NAMES:
                return True
            # 标题含"通话"：微信音视频通话 / QQ音视频通话 / 正在通话…
            title = _window_title(hwnd)
            if title:
                t = title.lower()
                if any(k in t for k in _CALL_TITLE_KW):
                    return True
            # 前台非通话，但同屏存在通信/会议软件的通话窗口（后台通话）→ 也算通话
            return self.has_call_window()
        except Exception:  # noqa: BLE001
            logger.debug("通话状态判定失败，退回 False", exc_info=True)
            return False

    @_sampled("foreground_process", (0, ""))
    def foreground_process(self) -> tuple[int, str]:
        """返回当前前台窗口所属进程 (pid, exe名小写)；失败返回 (0, "")。

        供出声源比对：判定"当前正在发声的进程是不是前台进程"，从而识别
        后台声音（后台视频/音乐/语音）造成的串台。
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return 0, ""
            pid = ctypes.wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return 0, ""
            name = _process_name(hwnd)
            return pid.value, name
        except Exception:  # noqa: BLE001
            logger.debug("前台进程查询失败", exc_info=True)
            return 0, ""

    @property
    def idle_seconds(self) -> Optional[float]:
        """真实系统空闲秒数（GetLastInputInfo）。"""
        return self._idle_seconds

    def stop(self) -> None:
        self._running = False
        _t = self._sampler_thread
        if _t is not None and _t.is_alive():
            _t.join(timeout=1.0)
        self._sampler_thread = None

    # ========================================================================
    # 感知采样线程（2026-09-05 治本修复·隔离 Win32 枚举，根除事件循环冻结）
    # ------------------------------------------------------------------------
    # 所有 EnumWindows / OpenProcess / GetForegroundWindow / SendMessageTimeout 只
    # 在下面这条独立串行 daemon 线程上发生，结果写进 self._snap（带锁）。其余查询
    # 方法（被 @_sampled 包装）在任何其他线程上调用时只读取 _snap，零 Win32、零阻塞。
    # 这样无论前台怎么快速切换、哪个窗口无响应，asyncio 事件循环都绝不会被冻结，
    # 从结构上消除"几天一卡死"的根因；旧的补丁式修复（SendMessageTimeout /
    # 重入保护 / 0.25s TTL 缓存）只是降低概率，未解决架构。
    # ========================================================================
    def _start_sampler(self) -> None:
        if self._sampler_thread is not None and self._sampler_thread.is_alive():
            return
        self._sampler_thread = threading.Thread(
            target=self._sampler_loop, name="fg-sampler", daemon=True)
        self._sampler_thread.start()

    def _sampler_loop(self) -> None:
        """独立串行线程：周期性枚举窗口并把结果写进 _snap；绝不在事件循环上跑。"""
        _tls.sampling = True
        try:
            while self._running:
                try:
                    self._sample_now()
                except Exception:  # noqa: BLE001
                    logger.debug("感知采样异常", exc_info=True)
                # 分段 sleep，stop 后更快退出
                _deadline = time.time() + self._sample_interval_s
                while self._running and time.time() < _deadline:
                    time.sleep(0.1)
        finally:
            _tls.sampling = False

    def _sample_now(self) -> None:
        """采样线程内：跑一遍各查询方法的 live 路径，填充 _snap 并发布前台切换事件。"""
        self._idle_seconds = _system_idle_seconds()
        with self._snap_lock:
            self._snap["idle_seconds"] = self._idle_seconds
        # 触发各查询方法 live 计算并写缓存；嵌套调用也会各自写缓存，互不冲突。
        self.foreground_hint()          # 写 fg_hint / fg_title
        self.visible_windows()          # 写 visible_windows
        self.game_screen_source()       # 内部调 is_meeting_running / is_game_foreground / pc_game_window
        self.meeting_rect()             # 写 meeting_rect
        self.is_call_active()           # 内部调 has_call_window
        self.is_unity_foreground()       # 写 fg_is_unity
        self.is_foreground_fullscreen()  # 写 fg_is_fullscreen
        self.is_foreground_immersive()  # 写 fg_immersive（SwitchController 8s 闸门靠它判沉浸）
        self.foreground_process()        # 写 foreground_process (pid, exe)
        self.foreground_title()         # 写 fg_title
        self.foreground_proc_name()     # 写 fg_exe
        self.heavy_app_tier_foreground()  # 写 heavy_app_tier_foreground
        self.is_game_process_foreground()  # 写 is_game_process_foreground
        self.is_meeting_foreground()    # 写 fg_is_meeting
        self.is_meeting_running()       # 写 meeting_running
        self.is_game_foreground()       # 写 fg_is_game
        self.pc_game_window()           # 写 pc_game_window
        self.has_call_window()          # 写 has_call_window
        self._publish_fg_events()

    @_sampled("fg_hint", "")
    def foreground_hint(self) -> str:
        """当前前台窗口的应用类型 hint（标题规则 + 腾讯会议进程兜底）；采样缓存读。"""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ""
            title = _window_title(hwnd)
            hint = hint_from_title(title, self._rules)
            if not hint and _process_is_meeting(hwnd):
                hint = "game"  # 腾讯会议开会时标题变成"xxx的会议"，按进程名兜底
            return hint
        except Exception:  # noqa: BLE001
            return ""

    def _publish_fg_events(self) -> None:
        """比较本次采样与前次前台 hint/标题，发布 FOREGROUND_CHANGED 事件（不做 Win32）。

        【2026-09-06 回退·修复】之前为消除 0.1s 闪弹加了"全屏 + 连续 8s"闸门；实测对
        抖音/UWP 等窗口全屏判定不稳，导致 FOREGROUND_CHANGED 几乎永不发布 → 陪伴提示词
        注入、游戏陪伴卡全部失效（用户反馈"原本好的全死了"）。现回退为【前台变化 + 短去抖】
        即发布（1.2s 吸收 Alt-Tab 亚秒级抖动），全屏仅作为事件数据(is_fullscreen)由弹窗
        侧 _fs 自行决定是否弹，不再卡死事件本身。
        【2026-09-06 修复·全屏翻转重发】切到某 App 时若还是窗口化(_fs=False)，之后用户按
        F11/全屏按钮不会换前台窗口 → FOREGROUND_CHANGED 不再触发 → 抖音/视频类全屏陪伴
        弹窗+会话重连永不触发（用户实锤：抖音全屏看视频没弹窗没重连）。故「前台未变但全屏
        状态翻转」时也重发一次，让 handler 用最新 _fs 重新决策（去抖防抖）。
        """
        _title = self._snap.get("fg_title", "")
        _hint = self._snap.get("fg_hint", "")
        _full = bool(self._snap.get("fg_is_fullscreen", False))
        _now = time.time()

        if self._first:
            self._first = False
            self._last_title, self._last_hint = _title, _hint
            self._last_full = _full
            logger.debug("前台窗口初始化: %r (%s) full=%s", _title, _hint or "unknown", _full)
            return

        _prev = self._last_hint
        # 【2026-09-06 修复·全屏翻转重发】前台未变但全屏状态翻转（窗口化↔全屏）→ 重发一次，
        # 让 _on_foreground_changed 用最新 _fs 重新判定陪伴弹窗/重连。1.2s 去抖防连点抖动。
        _from_full = self._last_full
        if _from_full is not None and _from_full != _full \
                and _now - self._last_switch_ts >= self._STABLE_S:
            self._last_full = _full
            self._last_switch_ts = _now
            logger.info("前台全屏状态翻转: %s → %s (%r hint=%s)，重发前台切换事件",
                        _from_full, _full, _title, _hint or "unknown")
            self._bus.publish_threadsafe(Event(EventType.FOREGROUND_CHANGED, {
                "title": _title,
                "app_hint": _hint,
                "prev_hint": _prev,
                "is_fullscreen": _full,
                "ts": _now,
                "fullscreen_toggle": True,
            }, "foreground"))
            return

        # 与前次确认状态完全一致（hint + 标题都未变）→ 抖动/轮询重复，跳过；
        # 但仍刷新 _last_full，避免翻转检测卡在旧值。
        if _hint == self._last_hint and _title == self._last_title:
            self._last_full = _full
            return

        # 前台确实变化了：短去抖（吸收 Alt-Tab 间的亚秒级抖动），稳定后再发布。
        if _now - self._last_switch_ts < self._STABLE_S:
            return

        self._last_title, self._last_hint = _title, _hint
        self._last_full = _full
        self._switch_count += 1
        self._last_switch_ts = _now
        logger.info("前台应用切换(去抖%.1fs): %s → %s (%r) full=%s",
                    self._STABLE_S, _prev or "unknown", _hint or "unknown", _title, _full)
        self._bus.publish_threadsafe(Event(EventType.FOREGROUND_CHANGED, {
            "title": _title,
            "app_hint": _hint,
            "prev_hint": _prev,
            "is_fullscreen": _full,
            "ts": _now,
        }, "foreground"))

    def snapshot(self) -> dict:
        """运行状态快照（供调试台轮询，线程安全）。"""
        return {
            "enabled": self._enabled,
            "poll_interval_s": self._poll_interval_s,
            "sample_interval_s": self._sample_interval_s,
            "idle_seconds": round(self._idle_seconds, 1) if self._idle_seconds is not None else None,
            "last_title": self._last_title,
            "last_hint": self._last_hint,
            "switch_count": self._switch_count,
            "last_switch_ago": round(time.time() - self._last_switch_ts, 1) if self._last_switch_ts else None,
        }

    async def run(self) -> None:
        """前台监听入口：仅启动独立采样线程并保持任务存活。

        关键：本协程**绝不**在事件循环上做任何 Win32 枚举——所有枚举都在
        fg-sampler 线程完成。事件循环只负责 sleep，故前台窗口无响应/卡顿时
        后端其它协程（双工/OCR/GPU 查询）照常推进，根除"软件卡死"。
        """
        if not self._enabled:
            logger.info("前台窗口监听未启用（需 Windows）")
            return
        logger.info("前台窗口监听启动（采样线程隔离）: 采样周期=%.2fs", self._sample_interval_s)
        self._start_sampler()
        while self._running:
            await asyncio.sleep(1.0)
