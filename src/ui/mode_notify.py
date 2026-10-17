"""屏幕级模式切换通知浮窗 —— 语义层（驱动原生窗 native_notify）。

本模块只做"语义"：文案人性化、场景识别、show 去抖；show/update 转调
native_notify 的底层窗体 API。所有渲染、点透、滑入滑出、自动缩回均在
native_notify 内完成（WPF 优先 / GDI+ 兜底）。

彻底甩掉 WebView2 的 Chromium 子窗点不透 / 窗体复位(0,0) / 透明合成失败 /
节流送不到命令 等 53–60 轮顽疾——本模块不再有任何 WebView2 / js_api / 命令队列。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 不透明/透明开关已废弃（native_notify 内部处理），占位避免遗留引用报错。
def _notify_opaque() -> bool:
    return False


# ====== 亲民文案词典（兜底翻译历史开发者术语）======
_HUMANIZE = [
    ("已切换快照模式", "我先退到云端啦"),
    ("已恢复双工模式", "我回到本机陪你了"),
    ("云端快照模式", "云端帮忙模式"),
    ("本地双工模式", "本机陪伴模式"),
    ("云端双工已就绪", "连上啦，开始陪你"),
    ("本地双工已就绪", "连上啦，开始陪你"),
    ("双工已连接", "连上啦，开始陪你"),
    ("双工已就绪", "连上啦，开始陪你"),
    ("双工连上，正在下发专属提示词", "连上啦，正在换成这个软件的陪法"),
    ("正在下发专属提示词", "正在换成这个软件的陪法"),
    ("正在重新连上双工", "正在重新连上"),
    ("双工正在重新连接", "正在重新连上"),
    ("双工重新连接中", "正在重新连上"),
    ("双工连接中", "正在连上"),
    ("双工切换中", "正在切换"),
    ("云端排队中", "云端排队中"),
    ("已载入专属提示词", "已换上专属陪法"),
    ("载入专属提示词", "换上专属陪法"),
    ("载入方案", "换上专属陪法"),
    ("专属提示词", "专属陪法"),
    ("快照模式", "云端帮忙模式"),
    ("双工模式", "本机陪伴模式"),
    ("外部显存占用", "别的程序占用显存"),
    ("显卡负载过高", "显卡有点忙"),
    ("显卡负载已释放", "显卡空出来了"),
    ("互动质量", "陪你的反应速度"),
    ("强制游戏锁", "游戏优先"),
    ("双工", "陪伴连接"),
]


def humanize(text: Optional[str]) -> str:
    """把内部术语替换为普通人能懂的说法（幂等、长词优先）。"""
    if not text:
        return ""
    out = str(text)
    for src, dst in _HUMANIZE:
        if src in out:
            out = out.replace(src, dst)
    return out


def _detect_scene(title: str, body: str = "") -> str:
    """按标题/正文推断场景变体 key（后端可显式指定 scene 覆盖本推断）。"""
    t = f"{title or ''}{body or ''}"
    if ("游戏" in t) or ("让路" in t) or ("省显卡" in t) or ("显卡" in t and "忙" in t):
        return "game"
    if ("专属" in t) or ("陪法" in t) or ("方案" in t):
        return "ok" if ("✓" in t or "好了" in t or "已经" in t) else "focus"
    if ("✓" in t) or ("好了" in t) or ("准备好" in t):
        return "ok"
    if ("退到云端" in t) or ("云端" in t):
        return "game"
    return "back"


# —— 时长常量（仅用于 show 最短展示下限与默认超时）——
_TIMEOUT_MS = 8000     # 默认超时 ms（无连接结果时的最长停留）
_MIN_VISIBLE_S = 7.0   # 弹窗最短完整展示时长（秒）

# GUI 探活时间戳（native_notify 心跳刷新，供 webapp GUI 冻结探针读取）。
_last_poll: list = [0.0]

# 【2026-09-06 兼容垫片】旧 WebView2 通知窗引用，迁移到 WPF 原生窗后已无此对象。
# webapp.py 的 GUI 存活探针(_watch)仍按 `mode_notify._win` 判断"通知窗是否就绪"，
# 缺失会抛 AttributeError 直接杀死探针线程。WPF 模式无 WebView2 窗，置 None 即让探针
# 走"跳过 WebView2 存活检测"分支（正确行为），切勿删除本垫片。
_win = None

# 统一冷静期：按 key 独立计，避免"切屏/锁模式/重连"在短时间连弹。
# 之前去重逻辑散落在 proactive 的 4 个调用点、用 3 套各不相同的 dict，互相不协同
# （切到不同 app 仍每次都弹）。收拢到语义层一处，每个 key（云端/陪伴/普通/…）
# 8s 内只弹一次。不同 key 之间互不阻塞——这样"云端卡(t=0)→陪伴卡(t=8s)"的交接
# 不会被全局冷静期吞掉（2026-09-06 通知栈重构）。
_POPUP_COOLDOWN_S = 8.0
_last_popup_ts_by_key: dict = {}     # key -> 上次弹出的时间戳

# 首启引导期间静默锁：在引导页（first_launch_setup）完成前，所有模式切换/陪伴切换
# 浮窗一律不显示，避免"视觉模型正在准备"等重型服务提示在配置阶段弹出来打扰用户。
# 默认 False（老用户/非首启正常弹）；webapp.is_first_run 发现需要引导时置 True，
# save_first_run_config 完成时置 False。
_ONBOARDING_SILENT = False
_ONBOARDING_SILENT_LOCK = threading.Lock()


def set_onboarding_silent(silent: bool) -> None:
    """由 webapp 在首启引导显示/完成时切换。"""
    global _ONBOARDING_SILENT
    with _ONBOARDING_SILENT_LOCK:
        old = _ONBOARDING_SILENT
        _ONBOARDING_SILENT = bool(silent)
        if old != _ONBOARDING_SILENT:
            logger.info("[通知浮窗] 首启引导静默锁 -> %s", "开启" if _ONBOARDING_SILENT else "关闭")


def is_onboarding_silent() -> bool:
    with _ONBOARDING_SILENT_LOCK:
        return _ONBOARDING_SILENT


def _scene_to_key(scene: Optional[str]) -> str:
    """把场景变体映射到通知栈 key（驱动交接动画的卡种）。"""
    if scene == "game":
        return "cloud"
    if scene == "focus":
        return "companion"
    if scene == "back":
        return "normal"
    if scene == "ok":
        return "ready"
    return "default"

# 面板状态（保留以便调试/外部读取最近一次状态）。
_panel_state = {
    "status": "正在切换…", "progress": None, "connected": None,
    "error": None, "indeterminate": True, "settled_at": 0.0,
}


# ===================================================================
# 显示 / 更新（事件驱动，只调 native_notify，不碰 WebView2）
# ===================================================================
_show_debounce_timers: list = []


def _cancel_show_debounce():
    for t in _show_debounce_timers:
        try:
            t.cancel()
        except Exception:  # noqa: BLE001
            pass
    _show_debounce_timers.clear()


def _do_show(title: str, body: str, scene: str, timeout_ms: int, key: str = "default",
             hold_while_connecting: bool = False, app_icon: Optional[str] = None,
             actions: Optional[list] = None,
             hold_max_s: Optional[float] = None, **kwargs) -> None:
    """实际触发原生窗显示（去抖后调用）。key 透传给原生窗以驱动交接动画。"""
    try:
        from . import native_notify as nn
        nn.show(title, body, scene, timeout_ms, key,
                hold_while_connecting=hold_while_connecting, app_icon=app_icon,
                actions=actions, hold_max_s=hold_max_s)
        logger.info("[通知浮窗] ✅ 已 show（scene=%s, key=%s, timeout=%dms, hold=%s, icon=%s, actions=%d, hold_max=%s）",
                    scene, key, timeout_ms, hold_while_connecting, bool(app_icon),
                    len(actions or []), hold_max_s)
    except Exception as _e:  # noqa: BLE001
        logger.warning("[通知浮窗] show 失败: %s", _e)


# ============ 动作回调（2026-10-02 新增）============
# 原生窗里的按钮被点击 → 上报到这里 → 转给宿主（proactive）执行并记忆。
# 这样 mode_notify / native_notify 保持"纯 UI"，不知道业务逻辑。
_ACTION_HANDLER = None


def set_action_handler(fn) -> None:
    """注册动作回调：fn(action_id: str, key: str) -> None。由 proactive 启动时注册。"""
    global _ACTION_HANDLER
    _ACTION_HANDLER = fn


def dispatch_action(action_id: str, key: str = "") -> None:
    """原生窗按钮点击的入口（由 native_notify 在 UI 线程调用，内部再转线程）。

    ⚠️【2026-10-02 重要】handler 是在**新建的普通线程**里跑的，因此
    `asyncio.get_running_loop()` 在里面必然抛 RuntimeError —— handler 里凡是要
    投递协程（切档/重建/推事件），都必须通过下面 `_loop()` 拿到**主事件循环**，
    不能直接用 get_running_loop（否则动作点了没反应，且异常被 except 吞掉）。
    """
    logger.info("[通知浮窗] 用户选择动作 action=%s key=%s", action_id, key)
    fn = _ACTION_HANDLER
    if fn is None:
        logger.debug("动作回调未注册，忽略 action=%s", action_id)
        return

    def _wrap():
        # 把主事件循环绑到线程局部，供 handler 里的 _loop() 取用
        try:
            import asyncio as _a
            _a.set_event_loop(_LOOP_REF[0]) if _LOOP_REF[0] is not None else None
        except Exception:  # noqa: BLE001
            pass
        try:
            fn(action_id, key)
        except Exception:  # noqa: BLE001
            logger.exception("动作回调执行失败 action=%s", action_id)

    try:
        threading.Thread(target=_wrap, daemon=True).start()
    except Exception:  # noqa: BLE001
        logger.exception("动作回调启动失败 action=%s", action_id)


# 主事件循环引用：由 proactive 在启动时登记，供上面的 handler 线程取用。
_LOOP_REF: list = [None]


def set_action_loop(loop) -> None:
    """登记主事件循环（proactive 启动时调一次）。handler 在线程里用它投递协程。"""
    _LOOP_REF[0] = loop


def action_loop():
    """取主事件循环；拿不到返回 None（调用方须容错，不要假定一定有 loop）。"""
    return _LOOP_REF[0]

def update_mode_notify(
    *,
    status: Optional[str] = None,
    progress: Optional[float] = None,
    connected: Optional[bool] = None,
    error: Optional[str] = None,
    indeterminate: Optional[bool] = None,
) -> None:
    """实时更新底部连接状态条（由 proactive._on_duplex_status 等调用）。

    首启引导期间静默：不驱动 native_notify 更新，避免配置阶段出现"正在准备"等状态条。

    只写模块级 ``_panel_state``，然后转调 native_notify.update——不再有常驻轮询、
    也不再有任何跨线程 JS / WebView2 调用。native_notify 内部按 connected/error
    自动安排 settle 后缩回。
    """
    if is_onboarding_silent():
        return

    global _panel_state
    s = _panel_state
    if status is not None:
        s["status"] = status
    if progress is not None:
        s["progress"] = max(0.0, min(1.0, float(progress)))
    if connected is not None:
        s["connected"] = bool(connected)
        s["indeterminate"] = False
    if error is not None:
        s["error"] = str(error)[:60]
        s["indeterminate"] = False
    if indeterminate is not None:
        s["indeterminate"] = bool(indeterminate)

    try:
        from . import native_notify as nn
        # 【2026-10-02 修正·上一版改过头了】此前在语义层直接 return 拦掉**全部** update
        # （"决策卡显示中，忽略 update"），结果连带把"连接卡跑满进度条、翻绿翻红、
        # 按 _SETTLE_S 收起"这套原有行为一起干掉了 —— 用户反馈"原本有的逻辑改炸了，
        # 该等进度条跑满的没等 UI 就直接隐藏了"。
        # 正解：**语义层不做任何拦截**（它只管文案/去抖/按钮），进度与收起判定
        # 统一交给 native_notify —— 那里已按"是否决策卡"精确区分：
        # 决策卡照常收 status/progress，但不因 connected/error 自动收起；
        # 普通连接卡的原有行为一字未动。
        nn.update(
            status=humanize(s["status"])[:60] if status is not None else None,
            progress=s["progress"] if progress is not None else None,
            connected=connected,
            error=humanize(s["error"])[:60] if (error is not None and s["error"]) else None,
            indeterminate=indeterminate,
        )
    except Exception as _e:  # noqa: BLE001
        logger.warning("[通知浮窗] update 失败: %s", _e)


def show_mode_notify(title: str, body: str, timeout_ms: int = 12000,
                     scene: Optional[str] = None, key: Optional[str] = None,
                     hold_while_connecting: bool = False,
                     app_icon: Optional[str] = None,
                     actions: Optional[list] = None,
                     hold_max_s: Optional[float] = None, **kwargs) -> None:
    """显示模式切换浮窗（原生窗，非阻塞；自动替换旧浮窗）。

    title: 短标题（亲民口语，如"我先退到云端啦"）
    body:  详细说明（一句话讲清"为什么 + 对你有什么影响"）
    timeout_ms: 无连接结果时的最长停留毫秒数（出现"已连上/失败"后额外停留再收起）
    scene: 场景变体，决定配色/徽章："game"/"back"/"focus"/"ok"，None 时自动推断。
    key:   通知栈卡种 key（驱动交接动画），None 时按 scene 推导：
            game→cloud / focus→companion / back→normal / ok→ready。
            不同 key 之间走"向上传送带交接"；同 key 在冷静期内只弹一次。
    actions:【2026-10-02 新增】可选按钮列表，让用户能表达意愿而不只是被动接收。
            每项形如 {"id": "silent", "label": "就这样", "primary": True}；
            点击后经 notify_actions 回调上报（见 set_action_handler）。
            给了 actions 时**不再走 timeout 自动收起**，改为等用户点（带兜底超时）。
    hold_max_s:【2026-10-02 新增】hold 卡的**分级等待上限**（秒）。hold_while_connecting
            为 True 时 timeout_ms 会被忽略（等就绪信号才收），必须有上限兜底。
            陪伴切换卡传 45（换陪法几秒完成，等 300s 就是"卡在右上角不消失"）；
            真·重连卡不传 → 沿用 native_notify 的全局 300s。
    """
    # 首启引导期间：所有模式切换/陪伴切换浮窗静默，避免配置阶段弹"视觉模型正在准备"等。
    if is_onboarding_silent():
        logger.info("[通知浮窗] 首启引导期间静默，跳过 show（title=%s）", title)
        return

    # 紧急开关（供调试时一键止血）
    import os
    _off_marker = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "notify_off")
    if os.environ.get("MODE_NOTIFY_OFF") or os.path.exists(_off_marker):
        logger.info("模式通知浮窗: 已禁用（MODE_NOTIFY_OFF 或根目录 notify_off），跳过（title=%s）", title)
        return

    # 按 key 独立冷静期：同一 key 8s 内只弹一次；不同 key（云端/陪伴/普通）
    # 互不阻塞，保证"云端卡(t=0)→陪伴卡(t=8s)"交接不被吞（2026-09-06）。
    # ⚠️ 带按钮的卡是"在等用户做决定"，不受冷静期限制——否则用户刚选完，
    #    紧接着的同类事件被吞掉会显得"我点了没反应"。
    _key = key or _scene_to_key(scene)
    if not actions:
        global _last_popup_ts_by_key
        _now = time.time()
        _last = _last_popup_ts_by_key.get(_key, 0.0)
        if _now - _last < _POPUP_COOLDOWN_S:
            logger.info("[通知浮窗] 冷静期内跳过 show（key=%s, title=%s）", _key, title)
            return
        _last_popup_ts_by_key[_key] = _now
    else:
        # 决策卡：只防"同一时刻重复弹同一张"，不设时间冷静期
        _last_choice_ts = _last_popup_ts_by_key.get(_key + ":choice", 0.0)
        if time.time() - _last_choice_ts < 3.0:
            logger.info("[通知浮窗] 决策卡 3s 内重复，跳过（key=%s）", _key)
            return
        _last_popup_ts_by_key[_key + ":choice"] = time.time()

    stamp = time.time()
    logger.info("[通知浮窗] 准备 show（key=%s, title=%s, timeout=%dms, actions=%d）",
                _key, title, timeout_ms, len(actions or []))

    # 重置面板状态
    global _panel_state, _TIMEOUT_MS
    _panel_state = {
        "status": "正在切换…", "progress": None, "connected": None,
        "error": None, "indeterminate": True, "settled_at": 0.0,
    }
    _TIMEOUT_MS = timeout_ms

    title_h = humanize(title)
    body_h = humanize(body)
    scene_key = scene or _detect_scene(title_h, body_h)

    # 去抖 0.35s：连续切换只有最后一条真正显示，动画不打架（focus→ok 只留 ok）
    _cancel_show_debounce()
    t = threading.Timer(0.35, _do_show,
                         args=(title_h, body_h, scene_key, timeout_ms, _key),
                         kwargs={"hold_while_connecting": hold_while_connecting,
                                 "app_icon": app_icon,
                                 "actions": actions,
                                 "hold_max_s": hold_max_s})
    t.daemon = True
    t.start()
    _show_debounce_timers.append(t)


def retract_now() -> None:
    """立即收起（极少用；native_notify 通常按 settle/超时自动缩回）。"""
    try:
        from . import native_notify as nn
        nn.retract()
    except Exception as _e:  # noqa: BLE001
        logger.warning("[通知浮窗] retract 失败: %s", _e)


def is_showing() -> bool:
    """当前是否有通知在显示（供抓屏管线决定是否遮住通知窗）。"""
    try:
        from . import native_notify as nn
        return nn.is_showing()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    print("mode_notify 需由软件启动时注入窗口句柄；单独运行不显示浮窗。")
