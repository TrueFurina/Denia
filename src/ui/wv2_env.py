# -*- coding: utf-8 -*-
"""WebView2 运行环境：缓存目录加固、唯一 user-data 目录、CLR 崩溃守卫
（自 webapp.py 抽出，2026-10-17）。

注意：_wv2_broadcast 仍留在 webapp.py —— 它读写 STATE.wv2_controls 字典，
而该字典同时被 CharacterAPI 的方法使用。两边现在都走 ui_state.STATE，
不会再出现「各写各的字典」。
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import sys
import threading

from .ui_state import STATE

logger = logging.getLogger("src.ui.webapp")

# 原 webapp.py 17800 / 17799 行的两处状态，仅被本模块函数使用，一并迁入
_WV2_FOLDER_SEQ = itertools.count(1)
_WV2_PATCHED = False
# 【2026-10-17】随 _install_clr_crash_guard 迁入。原来它留在 webapp，
# 而 global 在本模块解析 → NameError（守卫装不上、进程照旧会无声闪退）。
_CLR_CRASH_GUARD_INSTALLED = False

__all__ = [
    "_stable_wv2_folder_key", "_sweep_legacy_wv2_folders", "_sweep_wv2_caches",
    "_harden_webview2", "_run_coroutine_sync", "_safe_js_api",
    "_patch_webview2_unique_user_data", "_install_clr_crash_guard",
]


def _stable_wv2_folder_key(window, n: int) -> str:
    """给窗口生成一个「跨启动稳定」的 UserDataFolder 目录名。

    两个约束必须同时满足：
      · 稳定 —— 同一窗口每次启动都落到同一个目录，WebView2 的 localStorage /
        IndexedDB 才能持久化（主题色、深浅模式、语速、云端 TTS 配置都存在这儿）。
      · 独立 —— 不同窗口必须不同目录，否则 WebView2 并发初始化同一个 folder 会
        E_ABORT(0x80004004) → 白屏（2026-08-24 那个根因，不能回退）。

    老实现用 f"{os.getpid()}_{n}"：独立性有了，但每次启动 PID 都变 → 每次都是
    全新空目录 → localStorage 次次被清空，表现就是「设置永远保存不了」。
    改为按窗口标题哈希：标题不同 → 目录不同；标题不变 → 目录跨启动不变。
    """
    title = str(getattr(window, "title", "") or "").strip()
    if title:
        try:
            import hashlib as _hashlib
            return "w_" + _hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
        except Exception:  # noqa: BLE001
            pass
    return f"win_{n}"          # 兜底：没标题就退回序号，同进程内仍然唯一


def _sweep_legacy_wv2_folders(root: str, max_age_days: int = 7) -> None:
    """清理旧版按 PID 命名的 UserDataFolder（形如 12345_1），避免目录无限堆积。

    新代码已改用 w_<md5> 稳定目录，所有「纯数字_数字」旧格式目录都不再被使用，
    直接全部删除（不再等 7 天），顺带清掉其中可能残留的跨实例共享缓存。
    稳定目录（w_ 前缀）与正在使用的目录一律不动。
    """
    import re as _re
    import shutil as _shutil
    try:
        if not os.path.isdir(root):
            return
        for name in os.listdir(root):
            if not _re.fullmatch(r"\d+_\d+", name):
                continue
            p = os.path.join(root, name)
            try:
                if os.path.isdir(p):
                    _shutil.rmtree(p, ignore_errors=True)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass


def _sweep_wv2_caches(root: str) -> None:
    """清空 WebView2 磁盘缓存（Cache / Code Cache / GPUCache / Service Worker）。

    软件每次启动都清一次，彻底绕开 WebView2 对 fetch/HTML 的顽固磁盘缓存——
    实测 HTTP 层 no-store 头对 WebView2 的 fetch 缓存无效，导致前端 JS/HTML
    改了仍在设备端跑旧逻辑（如知识库页 SPA 切栏一直拉旧版 HTML）。
    只删缓存子目录，不动 UserDataFolder 下的 localStorage / Cookie，不影响登录态。
    """
    import shutil as _shutil
    try:
        if not os.path.isdir(root):
            return
        cache_subdirs = ("Cache", "Code Cache", "GPUCache", "Service Worker", "JumpListCache")
        for name in os.listdir(root):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            for sub in cache_subdirs:
                c = os.path.join(d, sub)
                if os.path.isdir(c):
                    _shutil.rmtree(c, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _harden_webview2(ctrl, window) -> None:
    """让窗口表现得像原生桌面控件而非浏览器：

    - 麦克风/摄像头权限自动允许（不再弹 Edge 授权页）；
    - 关闭右键菜单、开发者工具、浏览器快捷键（Ctrl+U/Ctrl+S/F12 等）；
    - 拦截 window.open 新窗口（防 360 把弹窗误判为广告）。
    """
    try:
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2PermissionState,
            CoreWebView2PermissionKind,
        )
        cv2 = ctrl.CoreWebView2
        if cv2 is None:
            return
        st = cv2.Settings
        for _attr, _val in (
            ("AreDefaultContextMenusEnabled", False),
            ("AreDevToolsEnabled", False),
            ("AreBrowserAcceleratorKeysEnabled", False),
            ("IsStatusBarEnabled", False),
            ("IsGeneralAutofillEnabled", False),
            ("IsPasswordAutosaveEnabled", False),
        ):
            try:
                setattr(st, _attr, _val)
            except Exception:  # noqa: BLE001
                pass

        def _on_perm(sender, args):
            try:
                if args.PermissionKind in (CoreWebView2PermissionKind.Microphone,
                                           CoreWebView2PermissionKind.Camera):
                    args.State = CoreWebView2PermissionState.Allow
                args.Handled = True
            except Exception:  # noqa: BLE001
                pass

        def _on_ctx(sender, args):
            try:
                args.Handled = True
            except Exception:  # noqa: BLE001
                pass

        def _on_new(sender, args):
            try:
                args.Handled = True
            except Exception:  # noqa: BLE001
                pass

        cv2.PermissionRequested += _on_perm
        cv2.ContextMenuRequested += _on_ctx
        cv2.NewWindowRequested += _on_new
        logger.info("[WebView2] 窗口 %s 已加固（禁右键/DevTools/快捷键/弹窗，麦克风自动授权）", window.uid)
    except Exception as e:  # noqa: BLE001
        logger.warning("[WebView2] 加固失败（不影响功能）: %s", e)


def _patch_webview2_unique_user_data() -> None:
    """给每个 pywebview 窗口分配独立的 WebView2 UserDataFolder（幂等）。

    白屏根因（2026-08-24 三窗口探针实锤）：
    pywebview 的 winforms.init_storage() 只初始化一次全局 cache_dir，
    所有窗口共用同一个 UserDataFolder。WebView2 对同一 folder 的并发初始化
    会互相踩（E_ABORT 0x80004004），导致窗口初始化失败 → 白屏/黑屏。
    这里整体替换 EdgeChrome.__init__：每窗口独立 folder + 非透明窗口
    追加 --disable-gpu 软件渲染（GPU 被本地模型占满时 WebView2 GPU 进程
    起不来同样 E_ABORT），实测 3 窗口全部 READY OK。
    """
    global _WV2_PATCHED
    if _WV2_PATCHED:
        return
    try:
        import webview.platforms.edgechromium as _ec
    except Exception:  # noqa: BLE001 非 edgechromium 后端则跳过
        _WV2_PATCHED = True
        return

    _orig_init = _ec.EdgeChrome.__init__

    def _init_with_unique_folder(self, form, window, cache_dir):
        # 复制 pywebview 6.2.1 EdgeChrome.__init__ 官方实现，仅两处改动：
        # 1) UserDataFolder 每窗口独立（多窗口共享 folder → E_ABORT 白屏）；
        # 2) 非透明窗口追加 --disable-gpu（GPU 显存被本地模型占满时 WebView2
        #    GPU 进程启动失败 → E_ABORT；软件渲染绕开）。
        import logging as _logging
        from webview import _state as _wv_state
        from webview import settings as _wv_settings
        from webview.util import DEFAULT_HTML as _DEFAULT_HTML
        from Microsoft.Web.WebView2.WinForms import (
            CoreWebView2CreationProperties as _Props,
            WebView2 as _WebView2,
        )

        self.pywebview_window = window
        self.webview = _WebView2()
        props = _Props()
        n = next(_WV2_FOLDER_SEQ)
        # 2026-08-24 修正：UserDataFolder 不能用 %TEMP%（沙箱/系统会重定向 Temp，
        # WebView2 子进程（gpu/network/renderer）无法启动 → 导航开始后永不完成
        # → 白屏；系统自身 WebView2 用的是 Packages 目录所以正常）。
        # 改到 %LOCALAPPDATA% 下的 pywebview/wv2/<稳定键>（按窗口标题哈希，跨启动不变），每窗口独立。
        _base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        _wv2_root = os.path.join(_base, "pywebview", "wv2")
        # 2026-08-30 再修正：目录名由 f"{os.getpid()}_{n}" 改为按窗口标题哈希。
        # 带 PID 时每次启动都是全新目录 → localStorage 次次被清空 → 设置永远存不住。
        # 新键既要跨启动稳定（能持久化），又要每窗口独立（否则 E_ABORT 白屏）。
        folder = os.path.join(_wv2_root, _stable_wv2_folder_key(window, n))
        _sweep_legacy_wv2_folders(_wv2_root)
        _sweep_wv2_caches(_wv2_root)  # 启动即清 WebView2 磁盘缓存，绕过顽固 fetch 缓存
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:  # noqa: BLE001
            folder = cache_dir
        props.UserDataFolder = folder
        self.user_data_folder = props.UserDataFolder
        props.set_IsInPrivateModeEnabled(_wv_state['private_mode'])
        # 渲染模式：始终使用 GPU 合成（透明桌宠与主窗均依赖 DirectComposition/GPU）。
        # 2026-09-04 求证：此前「显存满 → WebView2 GPU 合成器冻结 → 卡死」的假设
        # 未被实锤，用户远程（UU 远程·屏幕共享禁止观察）看到的黑屏/未响应实为
        # 远程画面未传回，本地软件一直正常。故撤除「非透明窗改 --disable-gpu 软件渲染」
        # 的兜底分支，避免无谓掉帧。多窗口 E_ABORT 白屏的真正修复是「每窗口独立
        # UserDataFolder」（见上方 _patch_webview2_unique_user_data）。
        # 注意：曾试过 --disk-cache-size=1 禁用磁盘缓存，实测会让 UserDataFolder 行为异常
        # （主页卡死 + 触发首启引导页重置），已回退。前端缓存统一由 webapp.py 的
        # 「启动令牌 ?v 重写 + SPA fetch 的 _cb 时间戳」两层机制处理（见 _ensure_web_server / morph_icons.js）。
        _cache_off = '--disable-features=ElasticOverscroll'
        props.AdditionalBrowserArguments = _cache_off
        if _wv_settings['ALLOW_FILE_URLS']:
            props.AdditionalBrowserArguments += ' --allow-file-access-from-files'
        if _wv_settings['REMOTE_DEBUGGING_PORT'] is not None:
            props.AdditionalBrowserArguments += (
                f' --remote-debugging-port={_wv_settings["REMOTE_DEBUGGING_PORT"]}'
            )
        self.webview.CreationProperties = props

        self.form = form
        form.Controls.Add(self.webview)
        from threading import Semaphore as _Semaphore
        self.js_results = {}
        self.js_result_semaphore = _Semaphore(0)
        import System.Windows.Forms as _WinForms
        self.webview.Dock = _WinForms.DockStyle.Fill
        self.webview.BringToFront()
        self.webview.CoreWebView2InitializationCompleted += self.on_webview_ready
        self.webview.NavigationStarting += self.on_navigation_start
        self.webview.NavigationCompleted += self.on_navigation_completed
        self.webview.WebMessageReceived += self.on_script_notify
        from System.Threading.Tasks import TaskScheduler as _TaskScheduler
        self.syncContextTaskScheduler = _TaskScheduler.FromCurrentSynchronizationContext()
        from System.Drawing import Color as _Color
        self.webview.DefaultBackgroundColor = _Color.FromArgb(
            255,
            int(window.background_color.lstrip('#')[0:2], 16),
            int(window.background_color.lstrip('#')[2:4], 16),
            int(window.background_color.lstrip('#')[4:6], 16),
        )
        if window.transparent:
            self.webview.DefaultBackgroundColor = _Color.Transparent

        self.url = None
        self.ishtml = False
        self.html = _DEFAULT_HTML
        logger.info("[WebView2] 窗口 %s 独立 UserDataFolder=%s 透明=%s args=%s",
                    window.uid, folder, window.transparent,
                    props.AdditionalBrowserArguments)
        # 初始化完成诊断：区分"哪个窗口成功/失败"（pywebview 失败只打 ERROR，
        # 成功无日志 → 无法判断是加载窗销毁误报还是主窗真失败）
        _orig_ready = type(self).on_webview_ready
        def _ready_wrap(sender, args):
            try:
                if args.IsSuccess:
                    logger.info("[WebView2] 窗口 %s READY OK", window.uid)
                    # 【2026-10-17 修】原为 global _WV2_CONTROLS + 下标赋值。
                    # 函数迁到本模块后该名字不存在 → NameError → 被下面 except 吞掉，
                    # 控件永远注册不上（后端原生推送静默失效）。改走共享单例。
                    STATE.wv2_controls[window.uid] = self.webview
                    _harden_webview2(self.webview, window)
                else:
                    logger.error("[WebView2] 窗口 %s READY FAILED: %s",
                                 window.uid, args.InitializationException)
            except Exception:  # noqa: BLE001
                pass
            return _orig_ready(self, sender, args)
        self.webview.CoreWebView2InitializationCompleted -= self.on_webview_ready
        self.webview.CoreWebView2InitializationCompleted += _ready_wrap
        # 导航诊断（临时）：确认页面是否真的导航并渲染成功
        def _nav_start(sender, e):
            try:
                logger.info("[WebView2] 窗口 %s 导航开始: %s", window.uid, getattr(e, 'Uri', '?'))
            except Exception:  # noqa: BLE001
                pass
        def _nav_complete(sender, e):
            try:
                ok = getattr(e, 'IsSuccess', None)
                err = getattr(e, 'WebErrorStatus', None)
                logger.info("[WebView2] 窗口 %s 导航完成: ok=%s err=%s", window.uid, ok, err)
            except Exception:  # noqa: BLE001
                pass
        self.webview.NavigationStarting += _nav_start
        self.webview.NavigationCompleted += _nav_complete
        self.webview.EnsureCoreWebView2Async(None)

    _ec.EdgeChrome.__init__ = _init_with_unique_folder
    _WV2_PATCHED = True
    logger.info("[WebView2] 已启用每窗口独立 UserDataFolder（修复多窗口并发初始化 E_ABORT → 白屏）")


def _install_clr_crash_guard(force_load: bool = False) -> None:
    """挂接 .NET 未捕获异常处理器，避免 GUI 线程异常直接终止进程。

    force_load=False 时，若 CLR 尚未加载就直接返回 —— 纯逻辑场景（测试、离线脚本）
    不该为了装守卫而平白拉起整个 .NET 运行时。GUI 建窗前传 force_load=True。

    【2026-10-17 修复】原实现只 `import System`，但 pythonnet 3.x 下必须先
    `import clr` 再 `AddReference`，否则恒定 ModuleNotFoundError: No module
    named 'System' —— 守卫从未安装成功（日志里一条 warning 就跳过了）。
    另外 UnhandledExceptionMode 是 System.Windows.Forms 下的类，不是
    Application 的属性，旧写法 AttributeError 后被 except 吞掉。
    """
    global _CLR_CRASH_GUARD_INSTALLED
    if _CLR_CRASH_GUARD_INSTALLED:
        return
    if not force_load and "clr" not in sys.modules:
        logger.debug("[CLR守卫] CLR 尚未加载，跳过（GUI 建窗前会再调用一次）")
        return
    try:
        import clr
        clr.AddReference("System.Windows.Forms")  # 与 pywebview 的加载方式一致
        from System.Windows.Forms import Application
        from System import AppDomain
    except Exception as _e:  # noqa: BLE001
        logger.warning("[CLR守卫] pythonnet/WinForms 不可用，跳过: %s", _e)
        return
    try:
        from System.Windows.Forms import UnhandledExceptionMode
        # 必须在任何窗口创建之前设置，否则抛异常
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException)
    except Exception as _e:  # noqa: BLE001
        logger.debug("[CLR守卫] 设置异常模式失败（不影响后续挂接）: %s", _e)

    def _on_thread_exc(sender, e) -> None:
        try:
            logger.error("[CLR守卫] GUI 线程未捕获异常（已吞掉防闪退）: %s", e.Exception)
        except Exception:  # noqa: BLE001
            pass

    def _on_domain_exc(sender, e) -> None:
        try:
            logger.error("[CLR守卫] 域级未捕获异常（已标记处理防闪退）: %s",
                         e.ExceptionObject)
        except Exception:  # noqa: BLE001
            pass
        try:
            e.Handled = True  # 非致命异常：阻止 .NET 终止进程
        except Exception:  # noqa: BLE001
            pass

    try:
        Application.ThreadException += _on_thread_exc
    except Exception:  # noqa: BLE001
        pass
    try:
        AppDomain.CurrentDomain.UnhandledException += _on_domain_exc
    except Exception:  # noqa: BLE001
        pass
    _CLR_CRASH_GUARD_INSTALLED = True
    logger.info("[CLR守卫] 已安装（GUI 线程/域级 CLR 异常 → 记录并防闪退）")


def _run_coroutine_sync(coro, timeout: float = 300.0):
    """在一个**独立线程的全新事件循环**里跑完协程并返回结果。

    【2026-10-02 新增】给 js_api 守卫兜底用。本项目 GUI 进程已有一个常驻事件循环，
    所以不能在当前线程直接 asyncio.run（会报 "loop already running"），也不能把协程
    挂在那个循环上（会死等）。故：起独立线程 → 新建 loop → run_until_complete。
    """
    import asyncio as _aio
    import threading as _th
    _box: dict = {}

    def _worker():
        loop = _aio.new_event_loop()
        try:
            _aio.set_event_loop(loop)
            _box["r"] = loop.run_until_complete(coro)
        except Exception as e:  # noqa: BLE001
            _box["r"] = {"success": False, "error": f"异步接口执行失败：{type(e).__name__}: {e}"}
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    t = _th.Thread(target=_worker, name="jsapi-coro", daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return {"success": False, "error": f"异步接口超时（>{int(timeout)}s）"}
    return _box.get("r")


def _safe_js_api(api, label: str = ""):
    """js_api 序列化守卫（2026-10-02）：治 pywebview 层静默 TypeError。

    背景：pywebview 对 js_api 返回值做 json.dumps，任何不可序列化对象（如误
    返回的 bound method）都在序列化层抛 TypeError——前端 Promise 永远挂起、
    UI 毫无反馈，Python 侧只留一个**不带方法名**的 traceback（实测 01:59 运行
    时 1 小时内 245 次，完全无法定位是哪个接口）。

    守卫做三件事：
      1. 返回前本地 json.dumps 试序列化（与 pywebview 同规则）；
      2. 失败 → 日志直接打出 **方法名**（真凶可见）+ 返回值类型；
      3. 降级返回错误 dict——前端至少能收到响应，不再静默挂起。

    实现方式：动态派生子类包裹所有公开方法（同步/异步都支持），原对象状态
    原样搬迁，不影响 pywebview 的方法枚举（类函数仍是 bound method）。
    """
    import functools as _ft
    import inspect as _inspect

    cls = api.__class__

    def _make_guard(fn, name):
        _where = f"{label or cls.__name__}.{name}"

        def _fail(e):
            logger.error(
                "[js_api守卫] %s 返回不可 JSON 序列化（%r）→ 降级错误响应",
                _where, e)
            # 把方法名 + 错误类型带进返回值：前端可据此提示，也便于用户直接贴日志定位
            return {"success": False,
                    "error": f"接口 {_where} 返回了不可序列化的结果（{e}），详见运行日志"}

        if _inspect.iscoroutinefunction(fn):
            @_ft.wraps(fn)
            async def guard(self, *a, **k):
                result = await fn(self, *a, **k)
                try:
                    json.dumps(result)
                except Exception as e:  # noqa: BLE001
                    return _fail(e)
                return result
        else:
            @_ft.wraps(fn)
            def guard(self, *a, **k):
                result = fn(self, *a, **k)
                # 【2026-10-02 兜底】若某个"本应同步"的方法意外返回了协程对象（例如误写成
                # async def），这里就地把它跑完再返回，而不是让协程一路传到前端炸出
                # 「Object of type coroutine is not JSON serializable」这种天书错误。
                # 本项目 GUI 已有运行中的事件循环，故用独立线程跑一个全新的 loop。
                if _inspect.iscoroutine(result):
                    result = _run_coroutine_sync(result)
                try:
                    json.dumps(result)
                except Exception as e:  # noqa: BLE001
                    return _fail(e)
                return result
        return guard

    attrs = {}
    for name, fn in _inspect.getmembers(cls, _inspect.isfunction):
        if name.startswith("_"):
            continue
        attrs[name] = _make_guard(fn, name)
    safe_cls = type(cls.__name__ + "_Safe", (cls,), attrs)
    inst = object.__new__(safe_cls)
    # 【2026-10-10 修"agent卡/新消息永不上屏"根因】旧实现 `inst.__dict__.update(api.__dict__)`
    # 是**浅拷贝**：不可变标量（_chat_seq / _chat_history_loaded / _chat_status_id 等）
    # 在拷贝瞬间被定格，此后 wrapper 与原对象**各自分叉**——
    #   · 总线事件（USER_TEXT/AGENT_STATUS/PARTIAL_TEXT）处理在原对象上：seq 0→1→2→3，
    #     消息 append 进原对象的缓冲；
    #   · 前端轮询的 get_chat_messages 在 wrapper 上：永远看到拷贝瞬间的旧状态；
    #   · 今天新加的 _load_chat_history 会**重建 deque 并赋值**（rebinding），把 wrapper
    #     的 _chat_buffer 与原对象的彻底拆成两个 → 历史能恢复、实时消息全失踪，
    #     且落盘 id 每次启动都从 1 重来（日志 22:01:03「已恢复 16 条」vs 22:02:00「id=2」实锤）。
    # 修法：让 wrapper 与原对象**共享同一个 __dict__**（同一命名空间，读写全等）。
    # guard 方法挂在 safe_cls 类属性上不受影响；pywebview 的方法枚举基于类，亦不受影响。
    inst.__dict__ = api.__dict__
    return inst
