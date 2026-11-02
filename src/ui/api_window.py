# -*- coding: utf-8 -*-
"""窗口生命周期 / 健康检查 / Live2D 设置的 js_api（自 CharacterAPI 抽出，2026-10-17）。

WindowAPI 是 CharacterAPI 的 mixin：方法体未改动，通过 self 访问实例属性
（__init__ 仍由 CharacterAPI 提供）；recreate_main_window 里构造 CharacterAPI
的语句改成函数内延迟 import（webapp 依赖本模块，模块级 import 会成环）。
"""
from __future__ import annotations

import json
import logging
import os
import time

from .main_window import _FREEZE_DUMPS, _GUI_STALL_S, create_main_window
from .ui_state import STATE

logger = logging.getLogger("src.ui.webapp")


class WindowAPI:
    """窗口与运行时健康相关的前端桥方法。"""


    def ui_ping(self) -> dict:
        """前端存活心跳。幂等、极低开销，前端可安全高频调用。

        【2026-09-12 通话心跳契约】通话期间（_duplex_app_call_active=True）本方法
        兼任「通话看门狗」的心跳源：每 5s 续期 proactive._last_call_poll_ts。
        背景：前端只在**拨号振铃期间**以 ~350ms 轮询 get_call_status
        （src/ui/web/index.html:901-922，接通即 break），接通后零轮询；
        若没有这里续期，通话必然在阈值后被锁循环误判"前端消失"而强制解锁
        （表现：音频源被切 system 听不见用户 + 桌面静默不出声 + AI 自言自语）。
        ⚠️ 契约：本心跳 5s 一跳（redesign/app_backend.js:431），
        对应 proactive.CALL_FRONTEND_GONE_S=25s。改任一侧必须同步另一侧。
        """
        now = time.time()
        # ① 新增逻辑一律外裹 try/except，且放在 _last_ui_ping 更新【之前】：
        #    绝不能因续期失败打断 UI 心跳（不更新 → UI 看门狗误报前端无响应
        #    → 触发窗口重载）。内层在 _touch_call_heartbeat 里再兜一层。
        try:
            self._touch_call_heartbeat(now)
        except Exception:  # noqa: BLE001
            pass
        if not self._ui_ping_armed:
            self._ui_ping_armed = True
            logger.info("[UI心跳] 看门狗已就绪（前端开始上报存活）")
            self._last_ui_ping = now
            return {"success": True, "ts": now}
        prev = self._last_ui_ping
        self._last_ui_ping = now
        # 心跳恢复：之前报过无响应，现在又来了 → 记一条恢复（用恢复前的间隔算时长）
        if self._ui_unresponsive_logged:
            self._ui_unresponsive_logged = False
            lag = now - prev
            logger.warning("[UI心跳] 前端心跳已恢复（无响应约 %.0fs 后重新连接）", lag)
        return {"success": True, "ts": now}

    def _touch_call_heartbeat(self, now: float) -> None:
        """通话看门狗续期：通话标志为真时刷新 proactive._last_call_poll_ts。

        只门控「通话标志」_duplex_app_call_active，**不门控** _duplex_call_ready：
        ready 会在会话报错/重连期间翻 False（proactive.py:1690-1692），门控它会让
        续期在重连窗口中断 → 仍被误判"前端消失"；"拨了号但从没接通"由
        _dead_connect（90s，与前端振铃上限 CALL_TIMEOUT_MS 对齐）单独兜底，
        不依赖本心跳。

        调用方：ui_ping（前端每 5s，src/ui/web/redesign/app_backend.js:431）。
        前端真死 → 不再有 ui_ping → proactive 侧阈值到点自愈解锁（自愈不失效）。

        异常策略：内部全兜，绝不向上抛——保护 UI 心跳看门狗（见 ui_ping 内注释）。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None:
            # 预览模式 / 独立启动（create_*_window(api=None) 自建实例）无主动策略。
            return
        try:
            if getattr(pro, "_duplex_app_call_active", False):
                pro._last_call_poll_ts = now
        except Exception:  # noqa: BLE001
            logger.debug("[通话心跳] 续期失败（忽略，不影响 UI 心跳）", exc_info=True)

    def get_ui_health(self) -> dict:
        """返回 UI 健康诊断快照（开发者页健康面板调用）。

        ⚠️ 必须低开销、绝不阻塞 GUI 线程：只读时间戳/计数器，不调 evaluate_js、不碰 WebView2。
        字段：
          gui_beat_ms      GUI 线程最近一次心跳距今毫秒（mode_notify._last_poll）
          gui_ok/gui_stalled  GUI 线程是否活跃
          frontend_ping_ms 前端最近一次心跳距今毫秒
          frontend_ok     前端是否活跃
          wv2_windows     WebView2 窗口数（>2 警惕泄漏）
          last_send_chat 最近一次发消息诊断
          freeze_dumps    GUI 冻结现场 dump 文件列表（卡死发生后可在此取到栈）
        """
        now = time.time()
        try:
            from . import mode_notify as _mn
            gui_last = _mn._last_poll[0]
        except Exception:
            gui_last = 0.0
        gui_gap = (now - gui_last) if gui_last else -1.0
        fe_gap = (now - self._last_ui_ping) if self._last_ui_ping else -1.0
        wv2 = 0
        try:
            wv2 = len(STATE.wv2_controls)
        except Exception:
            # STATE 还没初始化完全 → 控件数按 0
            pass
        return {
            "success": True,
            "gui_beat_ms": int(gui_gap * 1000) if gui_gap >= 0 else -1,
            "gui_ok": bool(gui_last and gui_gap < _GUI_STALL_S),
            "gui_stalled": bool(gui_last and gui_gap >= _GUI_STALL_S),
            "frontend_ping_ms": int(fe_gap * 1000) if fe_gap >= 0 else -1,
            "frontend_ok": bool(self._ui_ping_armed and fe_gap < self.UI_PING_TIMEOUT),
            "frontend_armed": bool(self._ui_ping_armed),
            "wv2_windows": wv2,
            "uptime_ms": int((now - self._boot_ts) * 1000),
            "last_send_chat": dict(self._last_send_chat),
            "freeze_dumps": list(_FREEZE_DUMPS)[-6:],
        }

    def _ui_watchdog_loop(self) -> None:
        """守护线程：周期性检查前端心跳是否中断。

        【2026-09-03 加固】不再「只记日志不干预」：首次判定 UI 无响应即尽力重载卡死
        的 WebView2 窗口（渲染进程还活着时 Reload 可救回），并持续周期记录方便排查。
        关键前提：_wv2_broadcast 已改为非阻塞推送，即便 UI 真死，后端（双工/聊天/
        心跳）也不再被拖垮，看门狗日志可正常落盘。
        """
        while True:
            try:
                time.sleep(self.UI_PING_INTERVAL)
                if not self._ui_ping_armed:
                    continue  # 前端尚未上报过首跳（预览/未连接），不误报
                gap = time.time() - self._last_ui_ping
                if gap > self.UI_PING_TIMEOUT:
                    if not self._ui_unresponsive_logged:
                        self._ui_unresponsive_logged = True
                        logger.warning(
                            "[UI心跳] ⚠️ 前端无响应（心跳中断 %.0fs，阈值 %.0fs）"
                            " — 判定为 UI 卡死/渲染进程挂死，尝试重载窗口恢复",
                            gap, self.UI_PING_TIMEOUT,
                        )
                        if time.time() - self._boot_ts < self.UI_RECOVER_GRACE_S:
                            logger.warning("[UI心跳] 启动宽限期内（模型加载会饿前端心跳），"
                                           "只记日志不触发恢复")
                        else:
                            self._recover_stuck_windows()
                    else:
                        # 仍在无响应：周期提醒，便于用户/日志定位
                        logger.warning(
                            "[UI心跳] 前端仍无响应（已中断 %.0fs），重载未生效或渲染进程已退出",
                            gap,
                        )
            except Exception as exc:  # 看门狗自身绝不能崩
                try:
                    logger.error("[UI心跳] 看门狗异常: %s", exc)
                except Exception:
                    # 看门狗连报错都失败（日志系统坏）→ 绝不能让看门狗自己崩
                    pass
                time.sleep(self.UI_PING_INTERVAL)

    def _recover_stuck_windows(self) -> None:
        """尽力重载卡死的 WebView2 窗口（UI 卡死自救，best-effort）。

        【2026-09-03 加固】避免重载风暴把 UI 拖死：
          - 全局冷却 30s，一次判定无响应只重载一轮；若重载本身卡在 UI 线程等待
            已挂死的渲染进程，反复重载反而雪崩；
          - 跳过桌宠窗：其卡顿多来自本地模型抢占 GPU，重载不仅无效还会雪崩，
            留待其自然恢复更稳。
        仅当渲染进程仍 IsConnected 时才 Reload（真正死了重载无效，须重启软件）。
        调用在守护线程上、且全程 try/except，绝不影响后端事件循环。
        """
        now = time.time()
        if now - STATE.pet_recover_last < 30.0:
            return
        STATE.pet_recover_last = now
        # 【2026-09-04 16:55 冻结实锤】看门狗线程直接调 cv2.Reload()（WebView2 要求
        # 必须在 GUI 线程调用，跨线程调它 → 整个进程冻结：日志 16:55:50 起全停 4 分钟+，
        # 所有线程无输出）。默认禁用自动 Reload；确要启用设环境变量 UI_RELOAD_RECOVER=1，
        # 且经控件 BeginInvoke 封送到 GUI 线程执行。
        import os as _os
        if _os.environ.get("UI_RELOAD_RECOVER") != "1":
            logger.warning("[UI心跳] 自动 Reload 恢复保持禁用（跨线程 Reload 曾冻结整个进程，"
                           "16:55:50 实证）；前端无响应仅记录，不影响后端运行")
            return
        pet_uid = getattr(getattr(self, "_pet_win", None), "uid", None)
        for uid, ctrl in list(STATE.wv2_controls.items()):
            if uid == pet_uid:
                continue  # 桌宠窗不重载（GPU 争用卡顿，重载雪崩）
            try:
                # 【2026-09-05 冻结修复】原实现在守护线程上先读 ctrl.CoreWebView2 /
                # cv2.IsConnected（跨套间 COM 同步封送→阻塞），再 BeginInvoke(Reload)。整体
                # 移入 BeginInvoke，在 GUI 线程执行所有 COM 访问；调用线程只碰 BeginInvoke，
                # 绝不碰任何 COM 属性。
                def _reload():
                    try:
                        cv2 = getattr(ctrl, "CoreWebView2", None)
                        if cv2 is None:
                            return
                        if getattr(cv2, "IsConnected", False):
                            cv2.Reload()
                            logger.info("[UI心跳] 已对窗口 %s 触发 Reload 尝试恢复", uid)
                        else:
                            logger.warning(
                                "[UI心跳] 窗口 %s 渲染进程已断开，Reload 无效（需重启软件）", uid)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[UI心跳] 窗口 %s 重载失败（best-effort）: %s", uid, exc)

                from System import Action as _Act
                ctrl.BeginInvoke(_Act(_reload))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[UI心跳] 窗口 %s 重载排队失败（best-effort）: %s", uid, exc)

    def get_live2d_psd(self) -> str:
        """返回看板娘 PSD 的 base64 data URL（软件用 file:// 加载，前端无法直接 fetch 本地文件）。

        无模型文件时返回空字符串，前端静默跳过。也支持读取 models 目录下最新的
        model_fixed.psd（修复层名版），便于替换验证。
        """
        import base64
        base = os.path.join(os.path.dirname(__file__), "web", "live2d", "models")
        for name in ("model.psd", "model_fixed.psd"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    data = f.read()
                if data:
                    return "data:application/octet-stream;base64," + base64.b64encode(data).decode("ascii")
        return ""

    def get_live2d_prop(self) -> str:
        """返回看板娘道具图（models/prop_bowl.png）的 base64 data URL，供 rig 合成道具层。

        没有该文件时返回空字符串，前端静默跳过（不影响看板娘加载）。
        """
        import base64
        p = os.path.join(os.path.dirname(__file__), "web", "live2d", "models", "prop_bowl.png")
        if os.path.exists(p):
            with open(p, "rb") as f:
                data = f.read()
            if data:
                return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        return ""

    def quit_app(self) -> None:
        """关闭全部窗口并退出程序（桌面看板娘双击退出用）。

        【2026-09-04 修复】主窗口挂了 events.closing 拦截（点 X → 隐藏到托盘），
        本方法 destroy 主窗会被拦成"隐藏"，导致"退出程序"根本退不掉、窗口凭空
        消失。先置 _app_quitting=True 让拦截器放行真实关闭。
        """
        import webview
        self._app_quitting = True
        logger.info("收到退出程序请求（quit_app）→ 真实关闭全部窗口")
        try:
            for w in list(webview.windows):
                w.destroy()
        except Exception:  # noqa: BLE001
            pass

    def set_windows(self, main_win=None, pet_win=None) -> None:
        """注入主窗口 / 桌宠窗口引用（main.py 创建窗口后调用）。"""
        self._main_win = main_win
        self._pet_win = pet_win
        STATE.pet_win = pet_win
        STATE.main_win = main_win
        self._main_win_closed = False
        self._pet_pos = None
        self._pet_base = None
        self._pet_dragging = False
        self._pet_visible = False
        logger.info("已注册窗口引用: 主窗口=%s 桌宠=%s",
                    "有" if main_win else "无", "有" if pet_win else "无")

    def mark_main_window_closed(self) -> None:
        """主窗口已关闭回调（main.py 的 closed 事件回调调用，供恢复时重建窗口）。"""
        self._main_win_closed = True
        logger.info("主窗口已标记为关闭（_main_win_closed=True）")

    def recreate_main_window(self) -> Optional[Any]:
        """重建主窗口（主窗口已关闭后从托盘/桌宠恢复时调用）。

        create_main_window 在线程中调用会走 pywebview 的非主线程创建路径，
        与启动时加载完主窗口的路径一致。重建后重新挂载"关闭→桌宠接管"回调。

        后台线程建窗偶发失败（pywebview 非主线程创建依赖一个存活实例），
        这里对 create 失败做一次重试，并把每一步失败原因完整落日志，便于定位。
        """
        import time as _time
        logger.info("recreate_main_window: 开始重建主窗口")
        # 【2026-09-05 稳定性根治】若主窗仍存活（仅隐藏、未真正销毁），直接 show 复用，
        # 不再二次 create_window（STA 死锁根因）。FormClosing 拦截成功时主窗从不销毁，
        # 这条快速路径就是常态恢复路径。
        _existing = self._main_win
        if _existing is not None:
            _fw = getattr(_existing, "_window", None)
            _alive = _fw is not None and not getattr(_fw, "IsDisposed", True)
            if _alive:
                try:
                    _existing.show()
                    self.set_windows(main_win=_existing, pet_win=self._pet_win)
                    logger.info("recreate_main_window: 主窗仍存活，直接 show 复用（跳过重建）")
                    return _existing
                except Exception:  # noqa: BLE001
                    logger.warning("recreate_main_window: 复用存活主窗失败，回退重建", exc_info=True)
        # 【2026-09-05 加固·修复 WebView2 进程泄漏】重建前先释放旧主窗口的 WebView2
        # browser 实例，否则每次重建都会泄漏一个 msedgewebview2 进程；累积到 WebView2
        # 的 UserDataFolder 锁被占满，下次启动主窗 WebView2 初始化会直接卡死（整进程冻结）。
        _old = self._main_win
        if _old is not None:
            try:
                _old.destroy()
                logger.info("recreate_main_window: 已释放旧主窗口 WebView2 实例（防泄漏）")
            except Exception:  # noqa: BLE001
                logger.debug("recreate_main_window: 释放旧主窗口失败（可忽略）", exc_info=True)
        for _attempt in (1, 2):
            try:
                win = create_main_window(api=self)
                if win is None:
                    logger.error("recreate_main_window: 第%d次 create_main_window 返回 None", _attempt)
                    if _attempt == 1:
                        _time.sleep(1.0)
                        continue
                    return None
                logger.info("recreate_main_window: create_main_window 成功 (attempt=%d, win=%s)",
                            _attempt, win)
                self.set_windows(main_win=win, pet_win=self._pet_win)

                # 重新挂载主窗口关闭回调：关闭 → 标记已关闭 + 桌宠接管
                def _on_main_closed(_win=None):
                    try:
                        self.mark_main_window_closed()
                        self.pet_show()
                    except Exception:  # noqa: BLE001
                        logger.warning("主窗口关闭后显示桌宠失败", exc_info=True)
                win.events.closed += _on_main_closed

                try:
                    win.show()
                    logger.info("recreate_main_window: win.show() 已调用 (attempt=%d)", _attempt)
                except Exception as e:  # noqa: BLE001
                    logger.warning("recreate_main_window: win.show() 失败: %s", e)

                # 校验窗口确已注册并显示，避免"返回了对象但实际未显示"的假成功
                import webview as _webview
                _reg = any(getattr(w, "uid", None) == win.uid for w in _webview.windows)
                _shown = win.events.shown.wait(5)
                if _reg and _shown:
                    logger.info("recreate_main_window: 主窗口重建完成并已显示 (attempt=%d)", _attempt)
                    return win
                logger.error("recreate_main_window: 窗口未实际显示（registered=%s shown=%s），"
                             "视为失败处理 (attempt=%d)", _reg, _shown, _attempt)
                if _attempt == 1:
                    _time.sleep(1.0)
                    continue
                return None
            except Exception as e:  # noqa: BLE001
                logger.exception("recreate_main_window: 第%d次重建主窗口异常", _attempt)
                if _attempt == 1:
                    _time.sleep(1.0)
                    continue
                return None

    def prevent_window_close(self) -> dict:
        """阻止窗口关闭，改为最小化到托盘。"""
        logger.info("阻止窗口关闭，最小化到托盘")
        return {"success": True, "message": "窗口已最小化到托盘"}

    def hide_window(self) -> dict:
        """隐藏窗口而不是关闭。"""
        logger.info("隐藏窗口而不是关闭")
        self.main_ui_expected_visible = False   # 窗口守护：主动隐藏不算异常消失
        if self._main_win is not None:
            try:
                # 尝试多种方法来隐藏窗口
                methods = []
                
                # 方法1: 直接调用hide方法
                if hasattr(self._main_win, 'hide'):
                    self._main_win.hide()
                    methods.append("hide")
                
                # 方法2: 尝试设置窗口不可见
                if hasattr(self._main_win, '_window') and hasattr(self._main_win._window, 'setVisible'):
                    self._main_win._window.setVisible(False)
                    methods.append("window.setVisible")
                
                logger.info(f"已尝试隐藏方法: {methods}")
                
                if methods:
                    logger.info("窗口已隐藏")
                    return {"success": True, "message": "窗口已隐藏"}
                else:
                    logger.warning("窗口对象没有隐藏方法")
                    return {"success": False, "error": "窗口对象没有隐藏方法"}
            except Exception as e:
                logger.error("隐藏窗口失败: %s", e)
                return {"success": False, "error": str(e)}
        else:
            logger.warning("主窗口不存在")
            return {"success": False, "error": "主窗口不存在"}

    def save_live2d_fx(self, offsets: dict) -> dict:
        """保存看板娘表情特效（泪滴/zzz）的拖拽偏移到 data/live2d_fx.json。"""
        try:
            p = os.path.join(os.path.dirname(__file__), "..", "..", "data", "live2d_fx.json")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(offsets or {}, f, ensure_ascii=False, indent=2)
            return {"ok": True, "path": p}
        except Exception as e:  # noqa: BLE001
            logger.warning("保存看板娘特效位置失败: %s", e)
            return {"ok": False, "error": str(e)}

    def get_live2d_fx(self) -> dict:
        """读取看板娘表情特效拖拽偏移（不存在时返回空 dict）。"""
        try:
            p = os.path.join(os.path.dirname(__file__), "..", "..", "data", "live2d_fx.json")
            if not os.path.exists(p):
                return {}
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def get_version(self) -> dict:
        """【阶段 B1】版本号 / 构建号（给关于页与排障用）。

        版本号只在 `src/version.py` 定义一处，这里只做转发，避免再长出第二个真值源。
        """
        try:
            from ..version import APP_BUILD, APP_NAME, APP_VERSION

            return {"ok": True, "name": APP_NAME,
                    "version": APP_VERSION, "build": APP_BUILD,
                    "text": f"v{APP_VERSION} (build {APP_BUILD})"}
        except Exception as e:  # noqa: BLE001
            logger.debug("读取版本号失败: %s", e)
            return {"ok": False, "error": str(e)}

    def _autostart_root(self) -> str | None:
        """包根（供 autostart 写命令行）；非包内环境（.venv 开发机）→ None。

        判定：root = sys.executable 上两级，须同时有 main.py 与 python/pythonw.exe
        （包内 = <installdir>\\python\\python.exe；开发机 .venv\\Scripts 必不满足）。
        """
        import sys

        root = os.path.dirname(os.path.dirname(sys.executable))
        if (os.path.isfile(os.path.join(root, "main.py"))
                and os.path.isfile(os.path.join(root, "python", "pythonw.exe"))):
            return root
        return None

    def get_autostart(self) -> dict:
        """【阶段 E】开机自启状态（UI 设置开关读取）。

        supported=False 表示当前是开发环境（.venv），开关应置灰。
        """
        try:
            from .. import autostart

            root = self._autostart_root()
            return {"ok": True, "supported": root is not None,
                    "enabled": bool(root and autostart.is_enabled(root))}
        except Exception as e:  # noqa: BLE001
            logger.debug("读开机自启状态失败: %s", e)
            return {"ok": False, "error": str(e)}

    def set_autostart(self, on: bool) -> dict:
        """【阶段 E】设置开机自启：写注册表 + 持久化到 config（单一真值）。

        两处都改：只写注册表不写 config，下次启动 sync 会按旧 config 悄悄关掉
        ——本仓「开关接线了但没通电」的典型病，这里一次性双写根治。
        """
        try:
            from .. import autostart

            root = self._autostart_root()
            if not root:
                return {"success": False, "error": "开发环境不支持（仅安装版可用）"}
            autostart.sync(bool(on), root)
            if self._cfg:
                raw = self._cfg.raw
                app = raw.setdefault("app", {})
                app["autostart"] = bool(on)
                self._cfg.save()
            logger.info("开机自启已%s", "开启" if on else "关闭")
            return {"success": True, "enabled": bool(on)}
        except Exception as e:  # noqa: BLE001
            logger.exception("设置开机自启失败")
            return {"success": False, "error": str(e)}

    def get_settings_health(self) -> dict:
        """【2026-09-29 · UX #1】每个开关的生效状态：{on, effective, blocked_by, reason}。

        为什么需要它：本仓存在"开关开了却不生效"的静默失效。最典型是视觉链路——
        `runtime_status.vision_enabled` = `perception.vision.enabled` **且** `vision_models.enabled`
        **且** `desktop_watch_enabled`，**三道闸**任一为关即全链路哑火，而 UI 原本不提示
        （且 `perception.vision.enabled` 这道闸**没有 UI**，只能改 config）。

        判定规则集中在此一处，前端**不得**各自手抄（见 UI_DESIGN_SPEC.md §7.4 / §7.5）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            vm = raw.get("vision_models") or {}
            per_v = ((raw.get("perception") or {}).get("vision") or {})
            agent = raw.get("agent") or {}

            # ---- 视觉感知：两道总闸（其一无 UI） ----
            vm_enabled = bool(vm.get("enabled", True))
            per_enabled = bool(per_v.get("enabled", True))  # 键缺失按 True
            vision_eff = vm_enabled and per_enabled
            if not per_enabled:
                vision_block = ("perception_vision_enabled",
                                "config 里 perception.vision.enabled=false（第二道视觉总闸，无 UI 开关），需手动改为 true")
            else:
                vision_block = (None, "")

            # ---- 桌面看屏互动 ----
            dw_on = bool(self._cfg.desktop_watch_effective())
            dw_eff = dw_on and vision_eff
            if vision_eff:
                dw_block = (None, "")
            else:
                dw_block = ("vision", "需先开启「视觉感知」"
                            + ("（并确认 config 的 perception.vision.enabled 为 true）" if not per_enabled else ""))

            # ---- 游戏时云端看屏 ----
            gcw_on = bool(vm.get("game_cloud_watch", False))
            cloud_ok = self._cloud_vision_available()
            if not dw_eff:
                gcw_block = ("desktop_watch", "需先开启「桌面看屏互动」（它又依赖「视觉感知」）")
            elif not cloud_ok:
                gcw_block = ("cloud_model", "还没有可用的云端视觉模型，请到「视觉模型」选云端模型并填 Key")
            else:
                gcw_block = (None, "")
            gcw_eff = gcw_on and dw_eff and cloud_ok

            # ---- 电脑控制（智能体）----
            ag_on = bool(agent.get("allow_tools_in_chat", False))
            ag_confirm = bool(agent.get("require_confirm", False))
            # 未开确认 ⇒ 仍部分生效（只暴露安全工具），故 effective 保持 True，仅给提示
            ag_block = ((None, "未开「执行前确认」：仅安全工具可用，点击/输入/按键等危险工具不会暴露")
                        if (ag_on and not ag_confirm) else (None, ""))

            # ---- 云端双工看屏幕（独立总闸 _duplex_vision_enabled，仅控云端双工推屏）----
            cdv_on = bool((raw.get("cloud_duplex") or {}).get("duplex_vision_enabled", False))
            # 【2026-10-08 审核修复】与前端门控 / _read_cloud_duplex 保持一致：综合判定
            # cloud_omni.api_key、cloud_duplex.voice/video.api_key、official_default 任一即可。
            # 否则在新版分端点入口填 Key 时，门控放行但 health 仍报「未绑定」，前后矛盾。
            cloud_duplex_key = bool(self._read_cloud_duplex().get("cloud_key_set", False))
            if not cloud_duplex_key:
                cdv_block = ("cloud_model",
                             "需先绑定云端双工 Key（模型与 API → 云端双工）开关才真正推屏")
            else:
                cdv_block = (None, "")

            def _mk(on: bool, eff: bool, blk) -> dict:
                return {"on": bool(on), "effective": bool(eff),
                        "blocked_by": blk[0], "reason": blk[1]}

            return {"success": True, "switches": {
                "vision": _mk(vm_enabled, vision_eff, vision_block),
                "desktop_watch": _mk(dw_on, dw_eff, dw_block),
                "game_cloud_watch": _mk(gcw_on, gcw_eff, gcw_block),
                "duplex_vision_enabled": _mk(cdv_on, cdv_on and cloud_duplex_key, cdv_block),
                "agent_tools": _mk(ag_on, ag_on, ag_block),
                "cloud_model": {"on": bool(cloud_ok), "effective": bool(cloud_ok),
                                "blocked_by": None, "reason": ""},
            }}
        except Exception as e:  # noqa: BLE001
            logger.exception("get_settings_health 失败")
            return {"success": False, "error": str(e)}
