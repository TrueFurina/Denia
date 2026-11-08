# -*- coding: utf-8 -*-
"""窗口创建与本地静态服务（自 webapp.py 抽出，2026-10-17）。

职责：
  · _ensure_web_server   本地静态 HTTP 服务（含启动令牌、local_img 白名单）
  · create_*_window      主窗 / 桌宠窗 / 通知浮窗 / 百炼控制台窗
  · _start_gui_liveness_probe  GUI 心跳与卡死现场 dump
  · run_webview          启动 pywebview 主循环

循环依赖处理：create_* 内部需要 CharacterAPI / BailianConsoleAPI，而这两个类仍在
webapp.py（它们持有 450+ 个 js_api 方法，尚未拆分）。本模块在**函数内部**延迟
import，运行时才解析，因此不构成模块级循环。
"""
from __future__ import annotations

import ctypes as _ct
import logging
import os
import threading
import time
from ctypes import wintypes as _wt
from typing import Optional

from .notify_geom import _notify_geom, _notify_set_pos
from .pet_window import _attach_pet_no_taskbar, _pet_find_main_hwnd, _u32
from .ui_state import STATE
from .wv2_env import (
    _install_clr_crash_guard, _patch_webview2_unique_user_data, _safe_js_api,
)

logger = logging.getLogger("src.ui.webapp")


def _ensure_web_server() -> str:
    """启动一个本地 HTTP 服务（仅本机 127.0.0.1）提供 src/ui/web 目录，
    作为主窗口/桌宠页面的加载源（替代 file://，规避 WebView2 对 file:// 的
    各种加载限制导致的空白/黑屏）。模块级单例，只起一次。返回基础 URL。
    """
    if STATE.web_server_base:
        return STATE.web_server_base
    import http.server
    import socketserver
    import re as _re
    web_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    # 每次启动生成一个唯一令牌，注入到所有 HTML 里引用的 ?v= 版本号上，
    # 强制 WebView2 永远拿不到磁盘缓存的旧 JS/CSS（实测 no-store / 启动清缓存都不可靠：
    # 旧 ?v=2 的 morph_icons.js 一旦被缓存，跑的就是无 _cb 时间戳逻辑的老 JS，
    # 导致 SPA 切栏一直拉旧版 HTML、旧聊天页 gsap.from(opacity:0) 卡透明态）。
    STATE.web_start_token = str(int(time.time()))
    _V_RE = _re.compile(r'\?v=[^"\'>\s]+')
    # 兜底：给【未带 ?v=】的本地 CSS/JS 资源也补上启动令牌。
    # 旧逻辑只重写已有的 ?v=，若某 HTML 以 <link href="x.css"> / <script src="x.js">
    # （无 ?v=）引用资源，WebView2 会跨重启命中磁盘缓存 → 改了样式/逻辑却看不到。
    # 关键：绝不给 .html（SPA 导航链接）加戳，否则 spa.js 解析路径会变成 xxx.html?v=.. 而 404。
    _ASSET_RE = _re.compile(r'(href|src)=("|\')([^"\']+?\.(?:css|js))\2')
    # 「外壳守门」脚本：redesign 子页若被当成顶层文档整页加载（F5 / location.reload /
    # 浏览器前进后退 / window.location.href 直跳 / 会话恢复），它会在第一时间回到外壳
    # index.html，并把原目标页写进 sessionStorage，由 spa.js 的 bootRestore 接着 SPA 载入。
    # 为什么必须这样：子页独立文档里【没有 spa.js、没有右栏 Live2D 舞台、侧栏还是各自
    # 文件里的旧副本】→ 一旦整页加载就是用户反馈的「无缘无故卡回老页面」，且此后所有点击
    # 都退化成整页跳转、再也回不去外壳。外壳已由 spa.js 置上 window.__SPA_SHELL，
    # 所以 SPA 正常注入子页脚本时这段会直接 return，不影响切栏。
    _SHELL_GUARD = (
        "<script>(function(){try{"
        "if(window.__SPA_SHELL)return;"
        "if(window.top!==window)return;"
        "var p=location.pathname||'';"
        "if(!/^\\/redesign\\/[^/]+\\.html$/.test(p))return;"
        "try{sessionStorage.setItem('__spa_pending',p+(location.search||''));}catch(e){}"
        "location.replace('/index.html');"
        "}catch(e){}})();</script>"
    )
    # 三个「知识库」别名桩（391B，内部只有 meta refresh + location.replace）：
    # 一旦被当子页拉取，DOM 里根本没有 .main，SPA 会直接抛错。统一换成真身 lib.html。
    _SPA_ALIAS = {
        'redesign/kb.html': 'redesign/lib.html',
        'redesign/knowledge_base.html': 'redesign/lib.html',
        'redesign/ui_redesign_knowledge.html': 'redesign/lib.html',
    }
    def _stamp_html(_html, _tok, _rel=''):
        _html = _V_RE.sub('?v=' + _tok, _html)          # 兼容旧 ?v= 写法
        def _add(m):
            _attr, _q, _url = m.group(1), m.group(2), m.group(3)
            if _url.startswith(('http://', 'https://', '//', 'data:', 'blob:', 'mailto:')):
                return m.group(0)
            if '?' in _url:                              # 已有查询（如 ?v=2）交给上一行处理
                return m.group(0)
            return _attr + '=' + _q + _url + '?v=' + _tok + _q
        _html = _ASSET_RE.sub(_add, _html)
        # 注入服务端构建令牌，供前端显示「当前运行的版本」便于核对是否最新
        if '</head>' in _html:
            _inj = '<script>window.__APP_BUILD=' + repr(_tok) + ';</script>'
            if _rel.startswith('redesign/') and _rel.endswith('.html'):
                _inj = _SHELL_GUARD + _inj
            _html = _html.replace('</head>', _inj + '</head>', 1)
        return _html
    # 允许中文/特殊路径文件名
    class _Handler(http.server.SimpleHTTPRequestHandler):
        # 【2026-11-04】/local_img 的图片要长缓存（原因见 do_GET 里那段注释），
        # 其余响应（HTML/JS/CSS）仍然强制不缓存，避免改了前端跑旧逻辑。
        #
        # ⚠️ 必须写成**类属性**，不能在 __init__ 里设实例属性：
        # BaseHTTPRequestHandler.__init__ 会在构造过程中就把本次请求处理完，
        # 等 __init__ 走到我们那行时 end_headers 早就执行过了 —— 那时属性还不存在，
        # 直接 AttributeError，表现为**所有静态文件都 RemoteDisconnected**
        # （2026-11-04 实测踩到：/local_img 正常、/index.html 全部断连）。
        _skip_no_store = False

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=web_root, **kwargs)
        def end_headers(self):
            # 强制不缓存：WebView2 的 HTTP 缓存会跨 app 重启保留在同一 UserDataFolder，
            # 导致前端 JS/CSS 改了但设备仍跑旧逻辑（如旧版 #modeBtn 仍是跳设置页的链接、
            # 旧聊天页 gsap.from(opacity:0) 卡透明态）。加 no-store 后每次加载都重新拉取。
            if not self._skip_no_store:
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            super().end_headers()
        def do_GET(self):
            # 🖼️ 角色卡头图（2026-09-29）：页面跑在 http://127.0.0.1，无法直接加载
            # web 根目录之外的本地图片（file:// 被同源策略挡住），故走这个只读路由
            # 吐图。仅限 data/char_banners/ 一个目录（白名单校验，防路径穿越）。
            if self.path.split('?', 1)[0] == '/local_img':
                try:
                    from urllib.parse import urlparse, parse_qs, unquote
                    _q = parse_qs(urlparse(self.path).query)
                    _p = unquote(_q.get("path", [""])[0])
                    # 白名单：data/char_banners（角色卡头图）+ data/char_avatars（角色头像）
                    #       + 聊天发图落盘目录（_save_chat_images 运行时注册进 _LOCAL_IMG_EXTRA_DIRS，
                    #       因 ActivityStore root 可能被配置自定义，不能写死相对路径）。
                    _allowed = [
                        os.path.abspath(
                            os.path.join(web_root, "..", "..", "..", "data", "char_banners")),
                        os.path.abspath(
                            os.path.join(web_root, "..", "..", "..", "data", "char_avatars")),
                        # 【2026-11-04】随包角色素材（danya.png / fatfish.jpg 等）：
                        # 公开化后角色图放在 web 根下的 `_role_assets/`，而角色卡、
                        # 聊天页、通话页统一经 `/local_img?path=` 取图。不放行这个目录，
                        # 出厂模板一引用它们就只能显示占位图（素材其实一直都在仓库里）。
                        os.path.abspath(os.path.join(web_root, "_role_assets")),
                    ]
                    try:
                        _allowed.extend([os.path.abspath(_d) for _d in list(STATE.local_img_extra_dirs)])
                    except Exception:  # noqa: BLE001
                        pass
                    _full = os.path.abspath(_p)
                    # 【2026-11-04】相对路径按 **web 根** 解析：角色素材存的是
                    # `_role_assets/xxx.jpg` 这种 web 相对路径，而 os.path.abspath
                    # 是按进程 CWD（仓库根）解析的 → 会落到 D:/Denia/_role_assets
                    # 这个不存在的位置，然后 404。存在才采用，保持 data/ 老路径不变。
                    if _p and not os.path.isabs(_p):
                        _cand = os.path.abspath(os.path.join(web_root, _p))
                        if os.path.isfile(_cand):
                            _full = _cand
                    if any(_full.startswith(_a + os.sep) and os.path.isfile(_full) for _a in _allowed):
                        _ext = os.path.splitext(_full)[1].lower()
                        # 【2026-10-22】补音频类型：克隆面板「试听」经本路由播参考
                        # 音频（data/voice_ref/ 里的 wav/mp3）。原先只认图片扩展名，
                        # 音频落到 application/octet-stream → <audio> 直接拒绝播放，
                        # 而播放失败的 catch 是空的 → 按钮点了没声也没报错。
                        _ct = {'.png': 'image/png', '.jpg': 'image/jpeg',
                               '.jpeg': 'image/jpeg', '.gif': 'image/gif',
                               '.webp': 'image/webp', '.bmp': 'image/bmp',
                               '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
                               '.flac': 'audio/flac', '.ogg': 'audio/ogg',
                               '.m4a': 'audio/mp4'}.get(
                                   _ext, 'application/octet-stream')
                        with open(_full, 'rb') as _f:
                            _data = _f.read()
                        # 【2026-10-13 修·表情包不播动画】只按扩展名定 Content-Type 不可靠：
                        # 表情包库里的文件常是 **GIF 内容却存成 .jpg 名**（实测 24/24 张都如此），
                        # 于是浏览器按 image/jpeg 当静态图渲染 → **动图永远不播**。
                        # 这里加一层**魔数嗅探**：以真实字节为准，扩展名只作兜底。
                        try:
                            if _data[:6] in (b'GIF87a', b'GIF89a'):
                                _ct = 'image/gif'
                            elif _data[:8] == b'\x89PNG\r\n\x1a\n':
                                _ct = 'image/png'
                            elif _data[:2] == b'\xff\xd8':
                                _ct = 'image/jpeg'
                            elif _data[:4] == b'RIFF' and _data[8:12] == b'WEBP':
                                _ct = 'image/webp'
                            elif _data[:2] == b'BM':
                                _ct = 'image/bmp'
                        except Exception:  # noqa: BLE001
                            pass
                        # 【2026-10-13 相机对焦加载】带 &thumb=1 时返回**超小模糊占位图**：
                        # 表情包大图/动图可能几 MB，加载期间页面全是白块很难看。
                        # 这里把图缩到 16px 的 JPEG（约 1KB），前端再放大 + blur 滤镜
                        # → 得到"同色调模糊占位"，原图到了再清晰（相机对焦效果）。
                        if _q.get("thumb", ["0"])[0] == "1":
                            try:
                                import io as _io
                                from PIL import Image as _PILImage
                                _im = _PILImage.open(_io.BytesIO(_data))
                                try:
                                    _im.seek(0)          # 动图取首帧
                                except Exception:        # noqa: BLE001
                                    pass
                                _im = _im.convert("RGB")
                                # ⚠️ 占位图尺寸 16→48：太小（16px）拉伸后色块糊成一团，
                                #    还能透出下层棋盘格，观感"脏"。48px 保住轮廓与主色
                                #    分布，配 CSS blur(10px) 才是"主色底"而不是"脏块"。
                                _im.thumbnail((48, 48))
                                _buf = _io.BytesIO()
                                _im.save(_buf, "JPEG", quality=72)
                                _data = _buf.getvalue()
                                _ct = "image/jpeg"
                            except Exception:  # noqa: BLE001  缩略失败就回原图，不挡显示
                                pass
                        self.send_response(200)
                        self.send_header('Content-Type', _ct)
                        self.send_header('Content-Length', str(len(_data)))
                        # 【2026-11-04 修·"对话头像经常加载不出来"】
                        # 原来这里和 end_headers 各发一次 no-store ⇒ 每张头像**每次
                        # 渲染都重新下载**：聊天页一条消息一个 img，30 条就是 30 次
                        # 376KB 的 danya.png（约 11MB），任何一次失败都会被前端
                        # `onerror="this.remove()"` 永久删掉 → 表现为"头像时有时无"。
                        # 文件名即内容标识（用户换头像会生成带时间戳的新文件名），
                        # 所以长缓存是安全的。
                        self._skip_no_store = True
                        self.send_header('Cache-Control', 'public, max-age=86400')
                        self.end_headers()
                        try:
                            self.wfile.write(_data)
                        except Exception:  # noqa: BLE001 客户端提前断开
                            pass
                        return
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.send_response(404)
                    self.end_headers()
                except Exception:  # noqa: BLE001
                    pass
                return
            # 🔔 通知窗页面状态回报（show=卡片已渲染应展开 / hidden=已隐藏应收缩）
            if self.path.startswith('/notify_state'):
                try:
                    from urllib.parse import urlparse, parse_qs, unquote
                    _q = parse_qs(urlparse(self.path).query)
                    _st = unquote(_q.get("state", ["hidden"])[0])
                    STATE.notify_ui_state[0] = _st  # 保留原始值（show/hidden/complete）
                    # 🔑 连上(complete)后约 1.8s 自动滑出窗口：事件驱动、只触发一次，
                    # 不靠心跳轮询（轮询每 0.5s 重驱动滑窗会在多次切换时闪动）。同时消除
                    # "卡片经 JS 2.2s 隐藏后、窗口仍停在屏内"的空深色框窗口。
                    if False:  # 已废弃：原生窗不再需要 WebView2 滑窗
                        try:
                            from . import mode_notify as _mn
                            if _st == "complete":
                                _th = threading.Timer(1.8, _mn._notify_slide_if,
                                                      args=(False,), daemon=True)
                                _th.start()
                            elif _st == "show":
                                # 前端已渲染好卡片内容并回报 show → 整窗带着内容滑入，
                                # 避免"黑底先到、UI 后显"的割裂观感（配合卡片 opacity:1）。
                                _mn._notify_slide_if(True)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.send_response(204)
                    self.end_headers()
                except Exception:  # noqa: BLE001
                    pass
                return
            # 🔔 通知浮窗命令通道（2026-09-04 17:20 实锤）：通知窗页面的 pywebview 桥
            # 不通（window.pywebview.api 不可用：poll/report 从未到达 Python），改走
            # HTTP——页面 fetch /notify_poll 原子取走全部积压命令（JSON 数组）。
            if self.path.split('?', 1)[0] == '/notify_poll':
                try:
                    from . import mode_notify as _mn
                    _cmds = []
                except Exception:  # noqa: BLE001
                    _cmds = []
                if _cmds:
                    # 交付日志（仅非空时打）：证明页面 fetch 活着、命令真实送达页面
                    logger.info("[通知浮窗] HTTP 轮询交付 %d 条命令: %s",
                                len(_cmds), [c.get("type") for c in _cmds])
                try:
                    import json as _json
                    _data = _json.dumps(_cmds).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(_data)))
                    self.end_headers()
                    self.wfile.write(_data)
                except Exception:  # noqa: BLE001
                    try:
                        self.send_response(500)
                        self.end_headers()
                    except Exception:  # noqa: BLE001
                        pass
                return
            # 🔔 通知浮窗诊断回报（页面 __notifyDiag 经 HTTP 回传卡片状态，绕开桥）
            if self.path.startswith("/notify_report"):
                try:
                    from urllib.parse import urlparse, parse_qs, unquote
                    _q = parse_qs(urlparse(self.path).query)
                    _st = unquote(_q.get("state", [""])[0])
                    logger.info("[通知浮窗-自检回报] %s", _st or "(空)")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.send_response(204)
                    self.end_headers()
                except Exception:  # noqa: BLE001
                    pass
                return
            # SPA 片段路由：/_spa/<启动时间戳>/<相对文件> 返回整页 HTML（含 ?v 重写）。
            # 把时间戳放进「路径」使 WebView2 缓存键必变，彻底绕开其对 fetch 磁盘缓存的
            # 顽固保留（实测 ?v= 令牌 + no-store 仍可能命中旧版），保证 SPA 切栏每次拉最新页面。
            _spa_m = _re.match(r'^/_spa/(\d+)/(.+)$', self.path.split('?', 1)[0])
            if _spa_m:
                _rel = _spa_m.group(2)
                _rel = _SPA_ALIAS.get(_rel, _rel)
                if (_rel.startswith('redesign/') and _rel.endswith('.html')
                        and '..' not in _rel and os.path.isfile(os.path.join(web_root, _rel))):
                    try:
                        with open(os.path.join(web_root, _rel), 'r', encoding='utf-8') as _f:
                            _html = _stamp_html(_f.read(), STATE.web_start_token, _rel)
                        _data = _html.encode('utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html; charset=utf-8')
                        self.send_header('Content-Length', str(len(_data)))
                        self.end_headers()
                        self.wfile.write(_data)
                        return
                    except Exception:  # noqa: BLE001
                        pass
                self.send_response(404); self.end_headers(); return
            # 对 .html 做版本号重写：把内部所有 ?v=旧版本 改成 ?v=<本次启动令牌>，
            # 让每个版本号都独一无二 → WebView2 缓存必失效、每次拉磁盘最新文件。
            fpath = self.translate_path(self.path)
            if fpath.endswith(".html") and os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as _f:
                        try:
                            _rel2 = os.path.relpath(fpath, web_root).replace('\\', '/')
                        except Exception:  # noqa: BLE001
                            _rel2 = ''
                        _html = _stamp_html(_f.read(), STATE.web_start_token, _rel2)
                    _data = _html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(_data)))
                    self.end_headers()
                    self.wfile.write(_data)
                    return
                except Exception:  # noqa: BLE001 读取/重写失败就退回默认处理
                    pass
            super().do_GET()
        def log_message(self, *a, **k):  # 静默
            pass
    class _TCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    srv = _TCPServer(("127.0.0.1", 8766), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="web_static")
    t.start()
    STATE.web_server_base = "http://127.0.0.1:8766"
    # 启动令牌只打前 8 位：日志会留存、可能被截图/上传，完整令牌不该落盘。
    # 同类处理见 webapp.py 的 api_key=%.8s。
    logger.info("本地静态 HTTP 服务已启动: %s (根目录 %s, 启动令牌 %.8s...)",
                STATE.web_server_base, web_root, STATE.web_start_token)
    return STATE.web_server_base


_NOTIFY_OPAQUE = os.environ.get("NOTIFY_OPAQUE", "0") != "0"


def _wv2_broadcast(payload: dict) -> None:
    """通过 WebView2 原生通道把消息实时推到所有窗口（聊天即时刷新用）。

    【防卡死·2026-09-03 实锤】原实现直接在调用线程（多为事件循环线程）上执行
    cv2.PostWebMessageAsJson，渲染进程挂死时该 COM 调用跨线程永久阻塞 → 事件循环
    卡死 → 双工/聊天/日志全停、整软件冻结。现改为：
      1) 提交到独立线程池 _WV2_BC_EXECUTOR，事件循环线程绝不等待；
      2) 推送前检查 CoreWebView2.IsConnected，渲染进程已断则跳过（不再触发挥死）；
    如此即便渲染器死了，后端（双工/聊天/心跳）仍正常运转，只是前端少收一条推送。
    """
    import json as _json
    msg = _json.dumps(payload, ensure_ascii=False)
    ctrls = list(STATE.wv2_controls.values())
    if not ctrls:
        return
    if payload.get("type") == "pet_tip":
        logger.info("[pet_tip] 广播目标窗口数=%d uids=%s", len(ctrls), list(STATE.wv2_controls.keys()))

    def _post_one(ctrl, m):
        try:
            from System import Action as _Act

            def _deliver():
                try:
                    cv2 = getattr(ctrl, "CoreWebView2", None)
                    if cv2 is None:
                        return
                    # 渲染进程已断开（GPU 高压崩溃等）→ 跳过，避免 PostWebMessageAsJson 挂死
                    if not getattr(cv2, "IsConnected", True):
                        return
                    cv2.PostWebMessageAsJson(m)
                except Exception:  # noqa: BLE001
                    pass

            # 【2026-09-05 冻结根因修复】ctrl.CoreWebView2 / cv2.IsConnected /
            # cv2.PostWebMessageAsJson 全是跨套间 COM 调用：上一版只在 BeginInvoke 里放了
            # PostWebMessageAsJson，却把 ctrl.CoreWebView2 与 cv2.IsConnected 的读取留在
            # 调用线程（asyncio 事件循环线程）上。从非 GUI 线程读 WebView2 的 COM 属性会触发
            # STA 同步封送并永久阻塞——faulthandler 多次抓到 asyncio 线程卡死在
            # webapp.py:5616 的 getattr(CoreWebView2)。事件循环一卡，双工/OCR/聊天/日志全停
            # = 整软件“卡死”。现把“所有 COM 访问”整体封送进 BeginInvoke，在 GUI 线程执行；
            # 调用线程只碰 BeginInvoke（任意线程安全、非阻塞、不创建线程），绝不碰任何
            # WebView2/COM 属性。即便 GUI 线程异常，asyncio 主循环也不受影响（仅少推一条）。
            ctrl.BeginInvoke(_Act(_deliver))
        except Exception:  # noqa: BLE001
            pass

    # 【2026-09-04 18:28 冻结实锤·深层根因】绝不经 ThreadPoolExecutor.submit 广播！
    # faulthandler 抓到：asyncio 主循环线程卡死在 submit → _adjust_thread_count →
    # Thread.start() → Event.wait()——线程池动态新开 worker 时，新 OS 线程创建了却
    # 永远进不了 Python（dump 顶部 <no Python frame> 的线程 = 卡在原生层，WebView2+
    # pythonnet 混合环境的 DLL 加载锁/COM 初始化病理）。主循环一停，双工/OCR/聊天
    # 全部停摆 = 整软件"崩溃"。
    # 🔑 BeginInvoke 本身就是异步投递（任意线程安全、非阻塞、不创建线程），直接在
    # 调用线程执行即可——广播路径从此零线程创建。
    for ctrl in ctrls:
        try:
            _post_one(ctrl, msg)
        except Exception:  # noqa: BLE001
            pass


_GUI_STALL_S = 6.0          # GUI 线程停滞判定阈值（秒）：beat 超此时间未刷新即判定卡死并抓全线程栈


_FREEZE_DUMPS: list = []   # 最近几次 GUI 冻结现场 dump 文件绝对路径（供开发者页健康面板读取/导出）


def _start_gui_liveness_probe(win) -> None:
    """GUI 消息泵黑匣子（2026-09-04「Python 未响应」排查·v4 BeginInvoke 心跳版）。

    v1 教训：WinForms.Timer 建在 events.shown 派发线程（无消息泵）→ 永不 Tick。
    v2 教训：守护线程对主窗 evaluate_js('1+1') 走阻塞式 Invoke → 本身就是死锁源。
    v3 教训：指望 notify 页 JS poll() 当心跳——页面 setTimeout 被 WebView2 节流或
    js_api 桥未注入时不跑，心跳假死；且又在 shown 回调线程建 Timer 重蹈 v1。
    🔑 v4（根治）：守护线程每 2s 用 **win.native.BeginInvoke(Action(_gui_beat))** 把
    心跳投递到真正的 GUI 线程（Control.BeginInvoke 从任意线程调用合法且非阻塞，由
    GUI 消息泵执行）。_gui_beat 在 GUI 线程内做四件事：① 刷新 _last_poll 心跳；
    ② 队列非空则 evaluate_js 注入 __notifyFlush（在本线程调用，绝无死锁）→ 弹窗
    兜底推送；③ 跟随前台显示器移动通知窗；④ 周期自检（窗口矩形+卡片状态）。
    心跳停超 15s = 消息泵真死 → 转储全线程栈（此时判定才准确）。
    """
    if STATE.gui_probe_started:
        return
    STATE.gui_probe_started = True
    import sys as _sys
    import threading as _th
    import time as _time
    import traceback as _tb

    try:
        from System import Action as _Action
        from System.Threading.Tasks import Task as _Task
    except Exception as _e:  # noqa: BLE001
        logger.error("[GUI探针] v4 启动失败：pythonnet System.Action/Task 不可用: %s", _e)
        return

    def _fire_async(_cv2, _script):
        # 🔧【2026-09-05 冻结根因修复】非阻塞触发：经线程池线程调用 ExecuteScriptAsync，
        # 绝不占用 GUI 线程。直接在主线程调 ExecuteScriptAsync 会被 pythonnet **同步 await**
        # 其返回的 Task<string>；当目标 WebView2 被遮挡/节流（游戏全屏、2px 泊位等，
        # 即"弹窗即将出现、窗口尚未滑入"的瞬间）脚本迟迟不返回 → GUI 消息泵被卡死 →
        # 主页面 UI 冻结（"弹窗一瞬间主页面卡死"的真凶）。丢到线程池后，即便脚本挂起也只
        # 占用线程池线程，主消息泵永不阻塞。ExecuteScriptAsync 本身是跨线程安全的异步 API。
        try:
            _Task.Run(_Action(lambda: _cv2.ExecuteScriptAsync(_script)))
        except Exception:  # noqa: BLE001
            pass
    try:
        _native = win.native  # BrowserForm：与所有窗共享同一 GUI 消息泵
    except Exception as _e:  # noqa: BLE001
        logger.error("[GUI探针] v4 启动失败：win.native 不可用: %s", _e)
        return

    state = {'reported': False, 'beat': 0, 'last_mon': None}

    def _notify_corewebview2():
        """取通知窗的 CoreWebView2（GUI 线程内 fire-and-forget ExecuteScriptAsync 用）。

        🔑【2026-09-04 17:03 冻结实锤】在 GUI 线程回调内**绝不能同步调 win.evaluate_js**：
        它内部要等 WebView2 的异步回执，而回执只能由"当前正被本调用堵住的消息泵"派发
        → 自己锁死自己（_gui_beat 卡死在 evaluate_js → 消息泵死 → 前端心跳断 → 整个
        UI 冻结）。ExecuteScriptAsync 发射后不管、不等待回执，物理上无死锁。
        """
        try:
            ctrl = STATE.wv2_controls.get(getattr(win, "uid", None))
            if ctrl is not None:
                cv2 = getattr(ctrl, "CoreWebView2", None)
                if cv2 is not None:
                    return cv2
        except Exception:  # noqa: BLE001
            pass
        try:
            for c in win.native.Controls:
                cv2 = getattr(c, "CoreWebView2", None)
                if cv2 is not None:
                    return cv2
        except Exception:  # noqa: BLE001
            pass
        return None

    def _gui_beat():
        """在 GUI 线程执行（BeginInvoke 封送）：心跳 + 弹窗兜底推送 + 跟屏 + 自检。"""
        try:
            from . import mode_notify
        except Exception:  # noqa: BLE001
            return
        mode_notify._last_poll[0] = _time.time()
        state['beat'] += 1
        # ② 兜底推送：fire-and-forget ExecuteScriptAsync（绝不同步 evaluate_js——
        #    GUI 线程内同步等回执 = 消息泵自死锁，17:03 实测全 UI 冻结）。
        # 🔑【2026-09-04 23:30 修复·真因】必须推给**通知窗自己**的 CoreWebView2，
        #    不能只推主窗：旧写法 _notify_corewebview2() 取的是主窗 uid 的控件 →
        #    __notifyFlush 被推到主窗（主窗页面无此函数 → 静默 no-op）→ 通知窗命令
        #    永不送达 → 弹窗永不显示。改为遍历所有已注册控件（含通知窗），命令必抵达。
        #    通知窗自身的 150ms setInterval 在 WebView2 判定其被遮挡时会**节流**
        #    （用户在抖音/全屏游戏时尤其明显）→ 弹窗不显示；而 ExecuteScriptAsync 由
        #    浏览器进程发起、不受页面计时器节流 → 可靠触发 flush→fetch→命令送达。
        try:
            if getattr(mode_notify, "_cmd_q", None):
                _n = len(getattr(mode_notify, "_cmd_q", None))
                _pushed = 0
                for _ctrl in STATE.wv2_controls.values():
                    _cv2 = getattr(_ctrl, "CoreWebView2", None)
                    if _cv2 is not None:
                        _fire_async(_cv2,
                                    "(window.__notifyFlush&&window.__notifyFlush())||0")
                        _pushed += 1
                if _pushed:
                    logger.info("[通知浮窗] GUI 心跳兜底推送 %d 条命令（ExecuteScriptAsync → %d 个窗）",
                                _n, _pushed)
        except Exception as _e:  # noqa: BLE001
            logger.warning("[通知浮窗] 兜底推送失败: %s", _e)
        # ④ 每 5 拍（约 10s）弹窗可见性自检：rect 不对=窗口位置问题；
        #    rect 正常但卡片无 visible/op=0=DOM 渲染层问题。
        if state['beat'] % 5 == 0:
            _rc = None
            try:
                # 🔑 必须用 FindWindowW（宽字符）匹配 CJK 标题"达妮娅通知"；
                #    ANSI 版 win32gui.FindWindow 永远匹配不上 → rect 恒为 None。
                _hw = _u32.FindWindowW(None, "达妮娅通知")
                if _hw:
                    _r = _ct.wintypes.RECT()
                    if _u32.GetWindowRect(_hw, _ct.byref(_r)):
                        _rc = (_r.left, _r.top, _r.right, _r.bottom)
            except Exception:  # noqa: BLE001
                pass
            try:
                # 🔑 同样遍历所有控件推 __notifyDiag（旧写法只推主窗 → 通知窗卡片状态
                #    永不回报）。通知窗页面经 /notify_report 把卡片状态回传后端日志。
                for _ctrl in STATE.wv2_controls.values():
                    _cv2 = getattr(_ctrl, "CoreWebView2", None)
                    if _cv2 is not None:
                        _fire_async(_cv2,
                                    "(window.__notifyDiag&&window.__notifyDiag())||0")
                logger.info("[通知浮窗-自检] rect=%s（卡片状态由页面 /notify_report 回报）", _rc)
            except Exception as _e2:  # noqa: BLE001
                logger.info("[通知浮窗-自检] 读取失败: %s rect=%s", _e2, _rc)
        # ③ 通知窗显隐（【2026-09-05 00:05 定版·去竞态】）
        #   滑入/滑出**不再由心跳每 0.5s 根据 _NOTIFY_UI_STATE 重驱动**——那样会在
        #   多次切换叠加时因状态反复翻转导致窗口"弹入→收起→又弹入"抖动。
        #   改由显式调用驱动：show 提交时 _push_show_debounced._fire 调 _notify_slide(True)
        #   滑入；dismiss 时 _dismiss 调 _notify_slide_if(False) 滑出；connected=True 经
        #   update_mode_notify→_arm_dismiss(3s)→_dismiss 收起。心跳只负责推命令 + 自检。
        #   模式 T（透明）：零窗口操作（同桌宠窗）。
        try:
            if not _NOTIFY_OPAQUE:
                # 模式 T：仅做"极端漂移"兜底——每 60 拍（30s）检查一次，且只有当
                # 窗口被挪到明显不合理的位置（完全在屏外/回到 0,0）时才纠回一次。
                state['ticks'] = state.get('ticks', 0) + 1
                if state['ticks'] % 60 == 0:
                    _sw0 = _ct.windll.user32.GetSystemMetrics(0)
                    _hwg = _u32.FindWindowW(None, "达妮娅通知")
                    if _hwg:
                        from ctypes import wintypes as _wt3
                        _r0 = _wt3.RECT()
                        if _ct.windll.user32.GetWindowRect(_hwg, _ct.byref(_r0)):
                            if _r0.left < 0 or _r0.left > _sw0 or \
                                    (_r0.right - _r0.left) < 100:
                                _, _, _, _w0, _h0, _x0, _y0, _ = _notify_geom()
                                _notify_set_pos(_x0, _y0, _w0, _h0)
                                logger.info("[通知浮窗] 模式T 检测到窗口被挪到屏外，已纠回 (%d,%d)",
                                            _x0, _y0)
        except Exception as _me:  # noqa: BLE001
            if not state.get('move_fail'):
                state['move_fail'] = True
                logger.warning("[通知浮窗] 钉位保持失败（只报一次）: %s", _me)

    def _watch():
        while True:
            _time.sleep(0.5)  # 0.5s 一拍：展开/收缩响应更跟手（每拍开销极低）
            try:
                _native.BeginInvoke(_Action(_gui_beat))
            except Exception:  # noqa: BLE001
                continue  # 窗口销毁中等情况：静默重试
            try:
                from . import mode_notify
            except Exception:  # noqa: BLE001
                continue
            if mode_notify._win is None:
                continue
            last = mode_notify._last_poll[0]
            gap = _time.time() - last
            if gap < _GUI_STALL_S:
                if state['reported']:
                    logger.info("[GUI探针] GUI 消息泵已恢复（停滞 %.0fs 后心跳恢复）", gap)
                state['reported'] = False
                continue
            # 心跳超 15s 未更新 = GUI 线程停摆（_gui_beat 已无法被泵执行）
            if state['reported']:
                continue
            state['reported'] = True
            try:
                _lines = []
                frames = _sys._current_frames()
                for th in _th.enumerate():
                    fr = frames.get(th.ident)
                    if fr is None:
                        continue
                    stack = _tb.extract_stack(fr)
                    tail = stack[-14:] if len(stack) > 14 else stack
                    _lines.append("--- 线程 %s (ident=%s) ---" % (th.name, th.ident))
                    _lines.extend(_l.rstrip() for _l in _tb.format_list(tail))
                _txt = "\n".join(_lines)
                # 🔑 先写独立文件再打日志（17:56 实测：全局冻结时 logging 先被冻死，
                # 先 logger 后写文件的顺序会连 dump 一起丢）。
                try:
                    import os as _os
                    _d = _os.path.join(_os.path.dirname(_os.path.dirname(
                        _os.path.abspath(__file__))), "logs")
                    _os.makedirs(_d, exist_ok=True)
                    _p = _os.path.join(_d, "gui_freeze_%s.txt" % _time.strftime("%Y%m%d_%H%M%S"))
                    with open(_p, "w", encoding="utf-8") as _f2:
                        _f2.write(_txt)
                    # 记录到全局列表，供开发者页健康面板读取/导出
                    global _FREEZE_DUMPS
                    _FREEZE_DUMPS.append(_p)
                    if len(_FREEZE_DUMPS) > 12:
                        del _FREEZE_DUMPS[:len(_FREEZE_DUMPS) - 12]
                    # 额外抓一份 faulthandler 全线程栈（含 C 侧部分符号），命名区分便于定位 native 死锁
                    try:
                        import faulthandler as _fh
                        _pf = _os.path.join(_d, "gui_freeze_fa_%s.txt" % _time.strftime("%Y%m%d_%H%M%S"))
                        with open(_pf, "w", encoding="utf-8") as _fa:
                            _fh.dump_traceback(file=_fa, all_threads=True)
                        _FREEZE_DUMPS.append(_pf)
                    except Exception:  # noqa: BLE001
                        pass
                    logger.error("[GUI探针] 💥 已抓取 GUI 冻结现场 → %s", _p)
                except Exception:  # noqa: BLE001
                    pass
                for _ln in _lines:
                    logger.error("[GUI探针] %s", _ln)
            except Exception:  # noqa: BLE001
                logger.warning("[GUI探针] 转储失败", exc_info=True)

    _th.Thread(target=_watch, daemon=True, name='gui-liveness-probe').start()
    logger.info("[GUI探针] v4 BeginInvoke 心跳版已安装（每2s投递 GUI 线程；兼做弹窗兜底推送/跟屏/自检）")
    logger.info("[GUI探针] v3 心跳版已安装（基于 js_api.poll 心跳，零跨线程 evaluate_js）")


_BAILIAN_INJECT_JS = (
    "(function(){try{"
    "var t=document.body?document.body.innerText:'';"
    "var ls=t.split('\\n').map(function(s){return s.trim();}).filter(function(s){return s&&s.length<90;});"
    "var kw=/token|额度|免费|万|%|配额|用量|重置|到期/i;"
    "var hits=ls.filter(function(s){return kw.test(s)&&/\\d/.test(s);}).slice(0,50);"
    "var login=(/登录|扫码|login/i.test(t))&&hits.length===0;"
    "if(window.pywebview&&window.pywebview.api&&window.pywebview.api.report_quota){"
    "window.pywebview.api.report_quota({url:location.href,title:document.title,hits:hits,login:login});"
    "}"
    "}catch(e){}})();"
)


def _bailian_inject_loop(win) -> None:
    """每 60s 向控制台窗口注入一次取数脚本（窗口开着才有效；异常静默，绝不影响主功能）。"""
    try:
        url = getattr(win, "_bailian_loaded_url", "") or ""
        if "bailian.console.aliyun.com" in url:
            win.evaluate_js(_BAILIAN_INJECT_JS)
    except Exception:  # noqa: BLE001
        logger.debug("[百炼控制台] 注入失败（窗口未就绪或已关闭）", exc_info=True)
    try:
        STATE.bailian_inject_timer = threading.Timer(60.0, _bailian_inject_loop, args=(win,))
        STATE.bailian_inject_timer.daemon = True
        STATE.bailian_inject_timer.start()
    except Exception:  # noqa: BLE001
        STATE.bailian_inject_timer = None


def create_bailian_console_window(api=None) -> Optional["webview.Window"]:  # noqa: F821
    """创建百炼控制台内嵌窗口：启动期调用、隐藏、懒加载 URL。

    复用 edgechromium + 每窗口独立 UserDataFolder（登录态持久化到本机，跨重启保留）。
    绝不在运行时 create_window（会触发 WebView2 STA 死锁）。返回窗口对象或 None。
    """
    import webview
    from .webapp import BailianConsoleAPI  # noqa: PLC0415  循环依赖：延迟到调用时

    placeholder = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;height:100%;background:#0d1017;color:#9aa3b2;"
        "font-family:system-ui,'Microsoft YaHei',sans-serif;display:flex;"
        "align-items:center;justify-content:center;text-align:center;padding:24px}"
        ".b{max-width:540px}.t{font-size:15px;line-height:1.8}"
        ".s{margin-top:10px;font-size:12px;color:#5d6473}</style></head>"
        "<body><div class='b'><div class='t'>百炼控制台将在你点击「在应用内查看额度」时打开。"
        "<br>首次使用请在窗口内登录一次阿里云，之后额度常驻可见。</div>"
        "<div class='s'>免费额度仅华北2·北京地域模型享有；其他地域页面为空属正常。</div></div></body></html>"
    )
    try:
        win = webview.create_window(
            title="百炼控制台 · 免费额度",
            html=placeholder,
            js_api=_safe_js_api(BailianConsoleAPI(), "BailianConsole"),
            width=1280, height=860,
            min_size=(900, 600),
            text_select=True,
            resizable=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[百炼控制台] 创建窗口失败: %s", e)
        return None
    # 创建即隐藏（避免启动期抢焦点 / 闪现）
    try:
        win.hide()
    except Exception:  # noqa: BLE001
        pass
    # 关闭 → 取消并隐藏（保留实例，避免运行时重建触发死锁）
    try:
        def _on_console_closing():  # noqa: ANN001
            try:
                win.hide()
            except Exception:  # noqa: BLE001
                pass
            return False  # 取消关闭 → 不销毁窗口实例
        win.events.closing += _on_console_closing
    except Exception:  # noqa: BLE001
        logger.warning("[百炼控制台] 关闭拦截挂载失败（关闭将销毁窗口，重开需重启）", exc_info=True)
    STATE.bailian_console_win = win
    logger.info("[百炼控制台] 内嵌窗口已创建（隐藏，懒加载）")
    return win


def create_main_window(api: Optional[CharacterAPI] = None,
                       title: str = "AI 桌面伴侣 - 角色管理"):
    """创建主窗口（加载全部完成后由启动监督线程调用）。返回 webview.Window。"""
    import webview
    # 🛡️ 确保 CLR 异常守卫在窗口创建/消息泵运行前就位（防启动期 ObjectDisposedException 闪退）。
    # force_load=True：此刻 pywebview 还没导入 clr，必须由这里主动加载，否则守卫装不上。
    try:
        _install_clr_crash_guard(force_load=True)
    except Exception:  # noqa: BLE001
        pass
    if api is None:
        from .webapp import CharacterAPI  # noqa: PLC0415  循环依赖：延迟到调用时
        api = CharacterAPI()
    base = _ensure_web_server()
    # 给入口文档加时间戳版本号，强制 WebView2 每次重启都重新拉取 index.html。
    # 脚本已有 ?v=2 版本爆破，但入口文档本身无版本号，旧版可能被 WebView2 的 HTTP
    # 缓存跨重启保留（表现为：改了 index.html 里的首启 overlay，重启却仍走旧逻辑）。
    # 本地静态服务虽已发 no-store，但部分 Edge/WebView2 版本对顶层文档缓存处理有差异，
    # 加 ?v= 时间戳双保险。SimpleHTTPRequestHandler.translate_path 会忽略 ? 后查询串，
    # 仍正确映射到 index.html。
    html_path = base + "/index.html?v=" + str(int(time.time()))

    # 用本地 HTTP 加载页面（非 file://），规避 WebView2 对 file:// 的加载限制
    # 导致的空白/黑屏。端口 8766 与 ws_server(8765) 不冲突。
    window = webview.create_window(
        title=title,
        url=html_path,
        js_api=_safe_js_api(api, "CharacterAPI-main"),
        width=1280,
        height=800,
        min_size=(1000, 700),
        text_select=False,
        resizable=True,
    )
    
    # 窗口创建后不立即显示，让调用者控制显示/隐藏
    logger.info("create_main_window: webview.create_window 返回 %s", window)

    # 【2026-09-05 稳定性根治】拦截主窗口关闭：用户点 X 时不真正销毁窗口，
    # 而是取消关闭 + 隐藏 + 桌宠接管。这样"关闭→桌宠→回主窗"全程只有启动时那
    # 一次 create_window，彻底避免运行时二次 create_window 触发 WebView2 STA 死锁
    # （之前每次重建主窗都卡死整进程，用户累计卡死 40+ 次的根因之一）。
    try:
        _fw = getattr(window, "_window", None)
        if _fw is not None and hasattr(_fw, "FormClosing"):
            def _on_main_form_closing(sender, e):
                # 程序正在退出（托盘"退出"已触发 _exiting）→ 放行真正关闭
                if getattr(api, "_exiting", False):
                    return
                try:
                    e.Cancel = True  # 取消关闭，改为隐藏而非销毁
                except Exception:  # noqa: BLE001
                    return  # 取消失败则交给默认关闭逻辑
                try:
                    api.hide_window()
                    api.pet_show()
                except Exception:  # noqa: BLE001
                    logger.warning("主窗关闭拦截: 隐藏/桌宠接管失败", exc_info=True)
            _fw.FormClosing += _on_main_form_closing
            logger.info("create_main_window: 已挂载主窗 FormClosing 拦截（关闭→隐藏+桌宠）")
    except Exception:  # noqa: BLE001
        logger.warning("create_main_window: FormClosing 拦截挂载失败（降级为真正关闭）", exc_info=True)
    # 【渲染自检（2026-08-24 多轮实测）】：必须在 GUI 线程调用 evaluate_js。
    # 用 window.events.loaded / shown 回调（由 pywebview 在 GUI 线程派发），
    # 绝不能用 threading.Timer（跨线程 evaluate_js 会触发 shown.wait 超时误报
    # "Main window failed to start"）。窗口真正 loaded 后读 innerHTML 长度确认渲染。
    def _on_loaded_selfcheck():
        try:
            v = window.evaluate_js(
                "(function(){try{var o=document.getElementById('onboardingOverlay');"
                "var st=o?((o.hidden?'hidden':(o.isConnected?'in-dom':'removed'))"
                "+(o&&o.style.opacity!=''?('|op='+o.style.opacity):'')):'removed';"
                "var b=document.getElementById('onboardingBoot');"
                "var bs=(b&&getComputedStyle(b).display!=='none')?'boot-visible':'boot-hidden';"
                "return (document.readyState||'')+'|'+(document.body?"
                "document.body.innerHTML.length:-1)+'|'+(document.title||'')"
                "+'|ob='+st+'|'+bs"
                "+'|bk='+(!!window.Backend)"
                "+'|pw='+(!!(window.pywebview&&window.pywebview.api));}"
                "catch(e){return 'ERR:'+e}})()")
            logger.info("[渲染自检-主窗口] %s", v)
        except Exception as e:  # noqa: BLE001
            logger.warning("[渲染自检-主窗口] 读取失败(可能黑/白屏): %s", e)
    # 进软件就绪提醒：窗口首帧 loaded 后一次性检查视觉模型，无配置/无模型在右上角弹引导。
    _readiness_fired = {"done": False}
    def _on_loaded_readiness():
        if _readiness_fired["done"]:
            return
        _readiness_fired["done"] = True
        try:
            # 首启引导期间：不弹视觉就绪窗，也不自动后台下载/准备模型，
            # 等用户完成引导页配置后再按所选配置启动。
            _fr = api.is_first_run()
            if _fr.get("first_run"):
                logger.info("[就绪弹窗] 首启引导期间，跳过视觉就绪提醒与自动准备")
                return
            api.notify_companion_readiness()
            # 进软件自动准备（后台下载）已保存的本地模型：满足"进软件就自动加载本地模型"。
            # download_model_quant 内部对已存在量化包直接跳过，不会重复下载；云端能力零下载。
            try:
                raw = api._cfg.raw
                feats = raw.get("companion_features")
                if not isinstance(feats, list):
                    feats = api._derive_features_from_config(raw)
                api._kickoff_custom_model_prep(feats, raw.get("proactive", {}))
            except Exception:  # noqa: BLE001
                logger.warning("[就绪自动准备] 触发失败（忽略）")
        except Exception:  # noqa: BLE001
            logger.warning("[就绪弹窗] 调用失败", exc_info=True)
    try:
        window.events.loaded += _on_loaded_selfcheck
        window.events.shown += _on_loaded_selfcheck
        window.events.loaded += _on_loaded_readiness
    except Exception:  # noqa: BLE001
        logger.warning("[渲染自检-主窗口] 事件注册失败(窗口可能白屏)")

    # GUI 线程活性探针（2026-09-04）：shown 事件在 GUI 线程派发，借此安装
    # WinForms.Timer 心跳；消息泵停滞 >6s 自动转储主线程栈，实锤「未响应」卡点。
    _probe_state = {'on': False}

    def _install_gui_probe(*_args):
        if _probe_state['on']:
            return
        _probe_state['on'] = True
        try:
            _start_gui_liveness_probe(window)
        except Exception:  # noqa: BLE001
            logger.warning("[GUI探针] 安装失败", exc_info=True)

    try:
        window.events.shown += _install_gui_probe
    except Exception:  # noqa: BLE001
        logger.warning("[GUI探针] 事件注册失败")
    return window


def create_desktop_companion(api: Optional[CharacterAPI] = None) -> "webview.Window":
    """创建桌面透明可拖动看板娘窗口（桌宠模式，屏幕右下角）。

    加载 Anime2.5DRig 的 ?bare 纯人物模式：透明背景 + frameless + 置顶。
    不使用 easy_drag：拖动由 rig 页面自己实现（JS 跟踪鼠标位移 → pet_move），
    才能同时做到"左键点击互动 / 拖动时下半身摆动物理 / 右键操作面板"。
    PSD 由 rig 页面直调 js_api.get_live2d_psd() 获取（file:// 下无法 fetch 本地文件）。
    初始 hidden，由调用方在合适时机 show()。
    """
    import ctypes
    import webview

    if api is None:
        from .webapp import CharacterAPI  # noqa: PLC0415  循环依赖：延迟到调用时
        api = CharacterAPI()
    base = _ensure_web_server()
    rig_path = base + "/live2d/rig/index.html?v=2"
    # 尺寸与 pet_state.PET_W/PET_H 同源：位置存盘的归一化用的就是这两个数，
    # 两处各写一份会在「工作区 − 窗口尺寸」这个分母上产生几像素差（2026-10-18 质检 P2）。
    try:
        from .pet_state import PET_H as _PET_H, PET_W as _PET_W

        w, h = _PET_W, _PET_H
    except Exception:  # noqa: BLE001
        w, h = 420, 540
    margin = 24
    # pywebview 的 x/y/width/height 均为逻辑像素（内部按显示器 DPI 换算成物理像素）。
    # 不能用 webview.screens 当逻辑分辨率：应用进程 start 后已 SetProcessDPIAware，
    # 此时 Screen.Bounds 返回的是物理像素且 scale 恒为 1.0（pywebview 6.2.1 实现如此），
    # 直接把 width 当逻辑值会在 >100% 缩放下把窗口推到屏幕外（4K/150% → 物理 5094）。
    # 改用 GetSystemMetrics/GetDeviceCaps：两者随进程感知状态一致（要么都返回虚拟化
    # 逻辑值、要么都返回物理值），相除后恒为逻辑分辨率，感知与否都正确。
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    hdc = gdi32.CreateDCW("DISPLAY", None, None, None)
    dpi = gdi32.GetDeviceCaps(hdc, 88) or 96  # LOGPIXELSX
    gdi32.DeleteDC(hdc)
    scale = max(dpi / 96.0, 1.0)
    lw, lh = int(sw / scale), int(sh / scale)
    # 【阶段 A4】有存档就回到上次位置，否则右下角。收尾阶段（_finalize_pet_window）
    # 会再夹一次，这里先给对位置，避免"先在右下角闪一下再跳"。
    try:
        from .pet_state import restore_logical

        px, py = restore_logical(w, h, (lw - w - margin, lh - h - margin))
    except Exception:  # noqa: BLE001
        px, py = lw - w - margin, lh - h - margin
    pet_window = webview.create_window(
        title="达妮娅看板娘",
        url=rig_path,
        js_api=_safe_js_api(api, "CharacterAPI-pet"),
        frameless=True,
        transparent=True,
        easy_drag=False,
        text_select=False,
        on_top=False,
        x=px,
        y=py,
        width=w,
        height=h,
        hidden=False,
    )
    # 桌宠渲染自检：必须在 GUI 线程（events 回调内）调用 evaluate_js，
    # 不可跨线程（threading.Timer 会误报 failed to start）。
    def _pet_selfcheck():
        try:
            v = pet_window.evaluate_js(
                "(function(){try{return (document.readyState||'')+'|'+(document.body?"
                "document.body.innerHTML.length:-1)+'|'+(document.title||'')}"
                "catch(e){return 'ERR:'+e}})()")
            logger.info("[渲染自检-桌宠] %s", v)
        except Exception as e:  # noqa: BLE001
            logger.warning("[渲染自检-桌宠] 读取失败(可能黑/白屏): %s", e)
    try:
        pet_window.events.loaded += _pet_selfcheck
        pet_window.events.shown += _pet_selfcheck
    except Exception:  # noqa: BLE001
        logger.warning("[渲染自检-桌宠] 事件注册失败(窗口可能白屏)")

    # 【2026-10-03 修复·桌宠拖不动根因】分离 WebView2 渲染窗为独立顶层窗：
    # webview/platforms/winforms.py 的 detach_pet_render() 已实现但**从未被调用**，
    # 导致 colorkey 分层窗整窗点击穿透（角色只显示、不参与命中）→ 既点不动也拖不动。
    # 分离后 on_move/on_resize/_sync_pet_render_rect 才会把角色跟手移动、让窗口可拖。
    # 渲染窗 Chrome_WidgetWin_1 在 WebView2 初始化完成后才出现，故后台线程多次重试。
    def _pet_detach_render():
        try:
            import time as _t
            # 【2026-11-04 修·native 是"窗口创建后才赋值"】pywebview 6.x 的
            # Window.__init__ 里是 `self.native = None  # set in the gui after
            # window creation`。原来这里只查一次：查早了就永久放弃，而这条分支
            # **只在没有 Electron 壳的机器上走**（源码直跑 / 队友新装），开发机
            # 有壳从来不走，所以一直没暴露——验包时才撞到。改成等它出现再判。
            native = None
            for _i in range(24):
                native = getattr(pet_window, "native", None)
                if native is not None:
                    break
                _t.sleep(0.25)
            if native is None or not hasattr(native, "detach_pet_render"):
                # 实话实说：detach_pet_render 在 pywebview 6.x 里**根本不存在**
                # （全仓也没有它的定义），所以没有 Electron 壳时，分层透明窗整窗
                # 点击穿透——桌宠只能显示，点不到也拖不动。
                # 想要可拖动的桌宠：cd shell && npm install（装壳后走另一条路径）。
                logger.warning(
                    "[桌宠] 没有 Electron 壳：当前 pywebview 不提供 detach_pet_render，"
                    "桌宠窗口只能显示、不能拖动（要拖动：cd shell && npm install）")
                return
            for _i in range(24):
                try:
                    native.detach_pet_render()
                except Exception as _e:  # noqa: BLE001
                    if _i == 0:
                        logger.warning("[桌宠] 渲染窗分离调用失败: %s", _e)
                _t.sleep(0.25)
            logger.info("[桌宠] 渲染窗分离重试结束（最多 ~6s）")
        except Exception as _e:  # noqa: BLE001
            logger.warning("[桌宠] 渲染窗分离调度异常: %s", _e)

    def _pet_start_detach(*_args):
        try:
            _th = __import__("threading")
            _th.Thread(target=_pet_detach_render, daemon=True).start()
        except Exception:  # noqa: BLE001
            logger.warning("[桌宠] 渲染窗分离线程启动失败")

    try:
        pet_window.events.shown += _pet_start_detach
    except Exception:  # noqa: BLE001
        logger.warning("[桌宠] 渲染窗分离事件注册失败（改用立即启动）")
        _pet_start_detach()
    return pet_window


def create_notify_window(api=None):
    """创建模式切换通知浮窗 —— 兼容壳，渲染由 native_notify（WPF 原生）负责。

    【2026-11-08 墓碑】本函数曾内联 329 行 WebView2 实现（frameless 窗口 +
    notify.html + NotifyJsBridge 轮询）。2026-09 通知浮窗改 WPF 原生渲染后，
    函数体被改成三行转调，旧实现整段留在 `return` 之后——而 try 与 except
    两条分支**都 return**，其后的 329 行全部不可达：

        try:
            return native_notify.ensure_notify_window()
        except Exception:
            return None
        import ctypes          # ← 从这里往后，永远执行不到
        ...
        return win

    不可达段里还引用了 `mode_notify.NotifyJsBridge` —— 该符号在
    src/ui/mode_notify.py 里**从未存在**（全文件 0 次命中）。谁哪天删掉那两行
    return，第一句就是 ImportError，而不是"恢复到旧实现"。

    删除依据：
      · src/ui/native_notify.py 的 docstring 明写「单后端，无兜底」——旧路是
        **有意废弃**的，不是待启用的备用分支；
      · 旧段内 5 个嵌套函数（_notify_apply_clickthrough / _install_gui_heartbeat /
        _notify_selfcheck / _apply_child_transparent / _walk）全是块内局部定义，
        外部零引用；
      · 随之失效的只有模块级 `_notify_park_out` 导入（已一并移除）；
        notify_geom 模块本身仍被 src/ui/webapp.py 使用，未动；
      · 全仓无任何 `create_notify_window(` 调用点（main.py 的实调是
        `native_notify.ensure_notify_window()`，见 main.py:2288）。函数签名因此
        保留为兼容壳——src/ui/webapp.py:77 与 main.py:169 仍按名字导入它，
        删函数会打断那两处导入。

    `api` 参数保留：调用方历史上按位置传 CharacterAPI，改签名有打断风险；
    WPF 路径不消费它。
    """
    try:
        from . import native_notify
        return native_notify.ensure_notify_window()
    except Exception as _e:  # noqa: BLE001
        logger.warning("[通知浮窗] 兼容壳 ensure 失败: %s", _e)
        return None


def run_webview(debug: bool = False,
                api: Optional[CharacterAPI] = None,
                title: str = "AI 桌面伴侣 - 角色管理",
                pet_backend: str = "auto") -> None:
    """启动 pywebview 角色管理窗口。

    当 main.py 集成调用时，api 会是已注入 runtime 的 CharacterAPI。
    主窗口关闭后，桌面透明看板娘窗口接管（继续在桌面展示达妮娅）。
    """
    import webview

    # 白屏修复：必须在创建任何窗口之前为每个窗口分配独立 UserDataFolder
    _patch_webview2_unique_user_data()

    if api is None:
        from .webapp import CharacterAPI  # noqa: PLC0415  循环依赖：延迟到调用时
        api = CharacterAPI()
    main = create_main_window(api=api, title=title)

    # 桌宠窗有两条实现路径（shell = Electron 真透明 / webview = pywebview 有白底），
    # 选哪条由 `ui.pet_backend` 或命令行定，决策点只有 `pet_backend.create_pet_window` 一处——
    # 本函数与 main.py 的 _startup_supervisor 共用它，避免"开关接线了但没通电"。
    from . import pet_shell as _pet_shell_mod
    from .pet_backend import create_pet_window as _create_pet_window
    from .pet_backend import normalize as _norm_backend
    from .pet_backend import wants_rpc as _wants_rpc

    _backend = _norm_backend(pet_backend, source="run_webview 参数")
    # 本入口不经过 main.py 的 --rpc 分支，壳要用的通道得自己起。
    # 已注入则不动（main.py 集成时那边早就起好了，避免重复监听）。
    if _wants_rpc(_backend) and not _pet_shell_mod.rpc_ready():
        try:
            from .rpc_server import RpcServer as _RpcServer

            _pet_shell_mod.configure(_RpcServer(api).start())
        except Exception as _e:  # noqa: BLE001  起不来就回退，不影响主窗口
            logger.warning("[RPC] 本地通道启动失败（桌宠回退 pywebview）：%s", _e)

    desk, _used, _why = _create_pet_window(_backend, api=api)
    api.set_windows(main_win=main, pet_win=desk)
    if _used == "shell":
        logger.info("桌宠窗由 Electron 壳承载（真透明）")
    else:
        _attach_pet_no_taskbar(desk)   # 桌宠不占任务栏/Alt+Tab（壳自带 skipTaskbar，不需要）

    def _on_main_closed(_win=None) -> None:
        try:
            api.pet_show()   # 主窗口关闭后，桌面桌宠接管
        except Exception:  # noqa: BLE001
            pass

    main.events.closed += _on_main_closed
    # 强制使用 Edge Chromium 后端：避免 pywebview 在多后端间试探时
    # 重复注册窗口类导致 "Failed to unregister class Chrome_WidgetWin_0 (Error 1411)"，
    # 该错误会让主窗口/桌宠窗口创建后白屏或崩溃（尤其主窗口+桌宠同时创建时）。
    # 本机已确认安装 WebView2 Runtime，指定该后端是安全的。
    # 渲染后端策略（2026-08-24 关键修正）：
    # 1) 经查 pywebview 源码(edgechromium.py:82)确认：pywebview **不读取**
    #    WEBVIEW2_ADDITIONAL_ARGS 环境变量，所有手动设置的渲染参数从未生效；
    #    之前"能出窗口"的轮次实际都是 pywebview 默认行为，WebView2 默认初始化成功。
    # 2) 手动设 --no-sandbox / --enable-unsafe-swiftshader 反而导致
    #    WebView2 初始化 E_ABORT（0x80004004）失败 → 窗口空白。
    # 因此**不设任何额外参数**，完全交给 pywebview/WebView2 默认行为最稳。
    # 应用图标：pywebview 会把 _state['icon'] 设给 WinForms 窗体
    # （winforms.py:242-244），任务栏/Alt-Tab/窗口标题栏都用它。
    # 不给的话 Windows 显示的是 python.exe 的图标。
    _icon = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app.ico")
    if not os.path.isfile(_icon):
        _icon = None
    webview.start(debug=debug, func=None, gui="edgechromium", icon=_icon)
