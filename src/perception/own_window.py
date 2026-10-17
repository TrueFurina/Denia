"""枚举本进程拥有的窗口矩形，并在截屏图上把它们涂成中性色。

让"看屏"类功能（屏幕感知采样、双工实时推流）读不到软件自己的窗口——
纯代码手段，不动任何提示词。模型看图即知"这是别的软件/视频"，而不会把
桌宠自己的聊天 UI 当成用户在操作。

坐标约定：win32gui.GetWindowRect 返回的是虚拟屏幕坐标（多屏时可能含负偏移）。
截屏图 `img` 对应某一块 monitor（mss 的 monitors[idx]），其原点为
(mon["left"], mon["top"])。把窗口矩形减去该原点即可得到在 img 上的像素位置。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    import win32gui
    import win32process
    import win32con

    _HAS_WIN32 = True
except Exception:  # noqa: BLE001
    _HAS_WIN32 = False

# 已开启"透过"亲和性(WDA_EXCLUDEFROMCAPTURE)时置 True：DWM 抓屏时窗口本就透明，
# paint_own_windows 应直接跳过，否则会把窗口后面的真实画面涂成灰块、破坏透过。
# 【2026-09-04 修正】之前为"兜底"把 OWN_WINDOW_WDA 设 False 并每帧灰涂，结果把 WDA
# 已透过的区域又涂成灰块 → 用户看到"不透明"。实测本机 WDA_EXCLUDEFROMCAPTURE 对
# mss(GDI/BitBlt) 抓屏生效（日志 pet=True/main=True），故恢复 True：只走 WDA 真透过，
# 灰涂彻底跳过（也省掉每帧 EnumWindows 开销）。这正是用户所说的"昨天透明"的配置。
OWN_WINDOW_WDA = True


def own_window_rects(pid: int | None = None,
                     skip_titles: tuple = ()) -> list[tuple[int, int, int, int]]:
    """返回当前进程（默认 os.getpid()）拥有的可见顶层窗口矩形。

    矩形为 (x, y, w, h) 虚拟屏幕坐标。极小/零尺寸窗口（托盘、幽灵窗口）被过滤。
    skip_titles: 标题命中的窗口跳过（用于"空闲时透明空窗"如通知浮窗——涂它的整块
    会留下常驻灰块，故仅在它真正显示内容时才涂，由调用方控制是否跳过）。
    """
    if not _HAS_WIN32:
        return []
    if pid is None:
        pid = os.getpid()
    _skip = set(skip_titles)
    rects: list[tuple[int, int, int, int]] = []

    # 两步式枚举（2026-09-05 修复）：回调内只做本地、无消息泵的 Win32 调用
    # （IsWindowVisible/GetWindowThreadProcessId/GetWindowRect）。**绝不**在回调里调
    # GetWindowText——其本质是 SendMessage，会泵送调用方消息队列，在 EnumWindows 期间
    # 造成重入/死锁，是 C 栈溢出崩溃（进程直接卡退）的同款诱因。标题延后到枚举结束后
    # 逐个取，彻底消除“SendMessage-in-EnumWindows”再入风险。own_window_rects 每帧都跑，
    # 是极热路径，必须排除该隐患。
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
        except Exception:  # noqa: BLE001
            return
        x, y, x2, y2 = r
        w, h = x2 - x, y2 - y
        if w <= 1 or h <= 1:
            return
        raw.append((hwnd, x, y, w, h))

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:  # noqa: BLE001
        logger.debug("枚举自身窗口失败", exc_info=True)
        return rects
    for hwnd, x, y, w, h in raw:
        if _skip:
            try:
                _t = win32gui.GetWindowText(hwnd)  # 枚举外取标题：避免 SendMessage-in-EnumWindows 重入
            except Exception:  # noqa: BLE001
                _t = ""
            if _t in _skip:
                continue
        rects.append((x, y, w, h))
    return rects


def paint_own_windows(
    img,
    monitor_left: int = 0,
    monitor_top: int = 0,
    color: tuple[int, int, int] = (110, 110, 120),
    pid: int | None = None,
    skip_titles: tuple = (),
) -> bool:
    """把本进程窗口在 img 上涂成中性色（就地修改 img）。

    monitor_left / monitor_top 为 img 对应 monitor 在虚拟屏幕的原点。
    返回是否真的涂了至少一个窗口。
    """
    # 【2026-09-04 修】是否灰涂改由调用方决定：见 screen_capture/proactive，仅当 WDA
    # 实际未生效(applied==0)时才灰涂兜底；WDA 生效则帧已真透过，绝不灰涂以免把透出的
    # 真实画面又涂成灰块。故此处不再单看 OWN_WINDOW_WDA，无条件执行涂窗（调用方控制是否调用）。
    rects = own_window_rects(pid, skip_titles)
    if not rects:
        return False
    try:
        from PIL import ImageDraw
    except Exception:  # noqa: BLE001
        return False
    W, H = img.size
    draw = ImageDraw.Draw(img)
    painted = False
    for (x, y, w, h) in rects:
        lx = x - monitor_left
        ly = y - monitor_top
        rx = lx + w
        ry = ly + h
        # 裁剪到本 img 范围（窗口在别的显示器上时整块落在图外，跳过）
        cl, ct, cr, cb = max(0, lx), max(0, ly), min(W, rx), min(H, ry)
        if cr > cl and cb > ct:
            draw.rectangle([cl, ct, cr, cb], fill=color)
            painted = True
    if painted:
        logger.info("涂掉自身窗口: %d个矩形(跳过%s)", len(rects), skip_titles or "()")
    return painted


# ===== 抓屏瞬间真透过（2026-09-04 修）=====
# 之前的灰涂兜底只是把自身窗口涂成中性色，模型看不到但画面上是"灰块"，
# 用户觉得"没变透明"。改用 WDA_EXCLUDEFROMCAPTURE：在 sct.grab() 那一瞬间对本
# 进程所有窗口设该亲和性，抓到的帧里窗口区域直接透出后面的真实画面（真透明）；
# 抓完立即恢复 NONE，保证 OBS / Zoom / 腾讯会议 / UU 远程等"别的屏幕共享"仍能
# 看见软件（满足"自己抓屏透明、别共享不透过"）。WDA 不可用时（<Win10 2004）
# 退回灰涂兜底（paint_own_windows 仍可用）。mss 走 GDI BitBlt，原生尊重该亲和性。

def own_window_handles(pid: int | None = None) -> list:
    """返回当前进程（默认 os.getpid()）拥有的可见顶层窗口 HWND 列表。

    与 own_window_rects 同筛选规则（可见 / 同 pid / 尺寸>1）。用于按 HWND
    逐个设 WDA 亲和性（灰涂只枚举矩形，这里需要 HWND）。
    """
    if not _HAS_WIN32:
        return []
    if pid is None:
        pid = os.getpid()
    out: list = []

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
        except Exception:  # noqa: BLE001
            return
        x, y, x2, y2 = r
        if (x2 - x) <= 1 or (y2 - y) <= 1:
            return
        out.append(hwnd)

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:  # noqa: BLE001
        logger.debug("枚举自身窗口 HWND 失败", exc_info=True)
    return out


import contextlib as _contextlib
import ctypes as _ctypes
from ctypes import wintypes as _wintypes

# 【2026-09-04 自查实锤】pywin32 的 win32gui **没有** SetWindowDisplayAffinity
# （hasattr=False，与之前 FindWindowW 缺失同款坑）→ 之前走 win32gui 每窗抛
# AttributeError 被逐个吞掉 → applied 恒 0 → 调用方永远灰涂，"真透过"从未生效。
# 改用 ctypes user32（webapp._pet_set_wda 同款，已验证可在这台机的窗口上设置成功）。
_u32_wda = _ctypes.windll.user32
_u32_wda.SetWindowDisplayAffinity.argtypes = [_wintypes.HWND, _wintypes.DWORD]
_u32_wda.SetWindowDisplayAffinity.restype = _wintypes.BOOL

_WDA_NONE = 0x00000000
_WDA_EXCLUDEFROMCAPTURE = 0x00000011
_wda_fail_logged = [False]

# 抓屏期"对捕获隐身"的保持期（秒）。
# 【2026-09-26 修·消除抽搐】旧值 0.4s：感知循环基础间隔 1~2s（静态时更慢），
# 两帧间隔一超过 0.4s 看门狗就把 WDA 撤成 NONE，下一帧又设回 EXCLUDE —— 这个
# "隐身↔可见"的反复横跳被 OBS/录屏/系统共享按自己的帧率拍到 → 屏幕一抽一抽。
# 改为长保持期：活跃看屏/双工推流期间每帧都会续期 `until`，WDA 持续 EXCLUDE 不再
# 横跳；真正停抓超过保持期后才恢复 NONE（别的共享又能看到软件，保留"别共享不透过"语义）。
# 取值需大于任何活跃抓屏帧间隔（感知 1~2s、双工<0.4s），留足余量避免静态慢抓时偶发单跳。
_WDA_HOLD_S = 5.0

# —— 抓屏期隐身（2026-09-26 修·长保持期，消除抽搐）——
# 只在**本软件自己抓屏**期间对捕获隐身（WDA_EXCLUDEFROMCAPTURE），停抓超过
# _WDA_HOLD_S 秒后恢复 NONE —— OBS/腾讯会议/UU远程/Win+Shift+S 等别的屏幕共享在
# 本软件不抓屏时看到的始终是正常画面（满足"只对自己隐身、别共享不透过"）。
# 【抽搐根因与修法】WDA 每次"隐身↔可见"切换都会让窗口在被捕获画面里闪一下。旧实现
# 每帧设 WDA 后仅保持 0.4s 即由看门狗撤除，而感知循环帧间隔 1~2s（静态更慢）→ 两帧间
# 窗口被撤回"可见"、下一帧再设"隐身"，这个周期横跳被外部捕获器拍到 = 屏幕一抽一抽。
# 现改为长保持期（_WDA_HOLD_S=5s）：活跃抓屏每帧续期 until，WDA 持续 EXCLUDE 不横跳；
# 仅停抓超保持期才恢复。多路抓屏（感知+双工推流+事件抓屏）共用同一次开关，互不打架。
import time as _time_mod
import threading as _threading_mod

_excl_state = {"active": False, "until": 0.0, "hwnds": []}
_excl_lock = _threading_mod.Lock()
_restorer_started = [False]


def _set_wda_all(hwnds, aff: int) -> None:
    for h in hwnds:
        try:
            _u32_wda.SetWindowDisplayAffinity(h, aff)
        except Exception:  # noqa: BLE001
            pass


def _ensure_restorer() -> None:
    """看门狗线程：保持期过后统一恢复 WDA_NONE（别的共享恢复可见）。"""
    if _restorer_started[0]:
        return
    _restorer_started[0] = True

    def _loop():
        while True:
            _time_mod.sleep(0.5)
            with _excl_lock:
                # 保持期内持续活跃抓屏会不断续期 until（见 exclude_own_windows），
                # 故不会中途撤除 → 无"隐身↔可见"横跳、无抽搐。仅当停抓超过
                # _WDA_HOLD_S 后才恢复 NONE（别的共享恢复可见，满足"别共享不透过"）。
                if _excl_state["active"] and _time_mod.time() >= _excl_state["until"]:
                    _set_wda_all(_excl_state["hwnds"], _WDA_NONE)
                    _excl_state["active"] = False
                    _excl_state["hwnds"] = []

    _threading_mod.Thread(target=_loop, daemon=True, name="wda-restorer").start()


def capture_exclusion_active() -> bool:
    """当前是否处于"对捕获隐身"状态（本软件抓屏期间为 True）。"""
    return _excl_state["active"]


@_contextlib.contextmanager
def exclude_own_windows(force: bool = True):
    """抓屏期间对本进程可见窗口设 WDA_EXCLUDEFROMCAPTURE，保持 '_WDA_HOLD_S' 秒后恢复。

    并发/相邻抓屏共用同一次开关；活跃抓屏每帧续期 until → WDA 持续 EXCLUDE，
    不再在"隐身↔可见"间横跳（消除 OBS/录屏拍到的抽搐）。停抓超过保持期后恢复
    NONE（别的共享恢复可见，满足"别共享不透过"）。
    yields: 本次隐身生效的窗口数（0 = WDA 不可用，调用方应退回灰涂）。
    """
    if not _HAS_WIN32 or not force:
        yield 0
        return
    n = 0
    with _excl_lock:
        if not _excl_state["active"]:
            hwnds = own_window_handles()
            ok = 0
            for h in hwnds:
                try:
                    if _u32_wda.SetWindowDisplayAffinity(h, _WDA_EXCLUDEFROMCAPTURE):
                        ok += 1
                except Exception:  # noqa: BLE001
                    pass
            _excl_state["hwnds"] = hwnds
            _excl_state["active"] = True
            n = ok
            if hwnds and ok == 0 and not _wda_fail_logged[0]:
                _wda_fail_logged[0] = True
                logger.warning("WDA_EXCLUDEFROMCAPTURE 对 %d 个窗口全部设置失败，"
                               "抓屏将退回灰涂兜底（本警告只打一次）", len(hwnds))
        else:
            n = len(_excl_state["hwnds"])
        _excl_state["until"] = _time_mod.time() + _WDA_HOLD_S
        _ensure_restorer()
    try:
        yield n
    finally:
        pass  # 恢复由看门狗统一执行（合并开关，减少闪烁）


# ===== 输入法窗口屏蔽（2026-09-04）=====
# 双工"看屏"时，屏幕上悬浮的输入法候选窗（搜狗/百度/微软拼音/讯飞/QQ 拼音/Rime
# 小狼毫等）会被模型当成"用户在看的内容"去猜测/点评，造成"对输入法乱猜"的尴尬。
# 这里把这类窗口在推流图上涂成中性色，让模型直接"透过"——纯代码手段，不动提示词。
# 屏蔽判定两层：① 窗口所属进程名命中已知输入法 exe（主判据，最稳）；
# ② 窗口类名含输入法特征串（兜底，覆盖进程名不好抓的输入法）。

# 已知输入法进程 exe（小写）。按需扩充即可覆盖更多输入法。
_IME_PROC_NAMES = {
    # 搜狗
    "sogoupy.exe", "sogoucloud.exe", "sogouinput.exe", "sgtool.exe",
    # 百度
    "baidupinyin.exe", "bdime.exe", "baidupy.exe",
    # 微软拼音 / 微软五笔
    "chsime.exe", "chtime.exe", "chshimir.exe",
    # 讯飞
    "iflyime.exe", "iflywiz.exe",
    # QQ 拼音
    "qqpinyin.exe", "qqpinyinservice.exe",
    # 谷歌拼音
    "googlepinyindaemon.exe", "googlepinyinservice.exe",
    # Rime / 小狼毫
    "weaselserver.exe",
    # 其他常见输入法
    "freeselfinput.exe",   # 手心输入法
    "unispim.exe",         # 紫光华宇拼音
    "jpwb.exe",            # 极品五笔
    "wnb.exe",             # 万能五笔
    "2345pinyin.exe",      # 2345 拼音
    "wbxime.exe",          # 五笔
}

# 窗口类名线索（小写子串匹配，兜底）。仅用明确特征串，避免误伤普通窗口。
_IME_CLASS_HINTS = (
    "sogou", "bdime", "baidu", "ifly", "qqpinyin", "weasel", "rime",
    "googlepinyin", "microsoft.ime", "sharedcandidatewindow",
    "candidatewindow", "imetip",
)


def _ime_proc_name(pid: int, cache: dict) -> str:
    """返回 pid 的可执行文件名（小写），拿不到返回 ''。cache 跨一次枚举复用，避免重复开句柄。"""
    if pid <= 0:
        return ""
    if pid in cache:
        return cache[pid]
    name = ""
    try:
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        h = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False, pid)
        try:
            path = win32process.GetModuleFileNameEx(h, 0)
        finally:
            win32api.CloseHandle(h)
        name = os.path.basename(path).lower()
    except Exception:  # noqa: BLE001
        name = ""
    # 仅缓存成功解析的，失败留空不缓存（下次再试）
    if name:
        if len(cache) > 1024:
            cache.clear()
        cache[pid] = name
    return name


def _is_ime_window(hwnd: int, pid_cache: dict) -> bool:
    # 优先按类名线索（最便宜、无需开进程句柄）
    try:
        cls = (win32gui.GetClassName(hwnd) or "").lower()
    except Exception:  # noqa: BLE001
        cls = ""
    if cls:
        for _h in _IME_CLASS_HINTS:
            if _h in cls:
                return True
    # 再按进程名（主判据）
    try:
        _, wp = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:  # noqa: BLE001
        return False
    name = _ime_proc_name(wp, pid_cache)
    if not name:
        return False
    if name in _IME_PROC_NAMES:
        return True
    # 容错：进程名带已知前缀（如 sogoupyhelper.exe）
    _stem = name.split(".", 1)[0]
    for _n in _IME_PROC_NAMES:
        if _stem == _n.split(".", 1)[0]:
            return True
    return False


def ime_window_rects() -> list[tuple[int, int, int, int]]:
    """返回当前可见的输入法候选窗矩形（虚拟屏幕坐标，x/y/w/h）。仅顶层非子窗口。"""
    if not _HAS_WIN32:
        return []
    rects: list[tuple[int, int, int, int]] = []
    seen: set = set()
    pid_cache: dict = {}

    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            # 跳过子窗口（输入法候选窗是 WS_POPUP 顶层浮窗，不是子窗口）
            _style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            if _style & win32con.WS_CHILD:
                return
            if not _is_ime_window(hwnd, pid_cache):
                return
            r = win32gui.GetWindowRect(hwnd)
        except Exception:  # noqa: BLE001
            return
        x, y, x2, y2 = r
        w, h = x2 - x, y2 - y
        if w <= 1 or h <= 1:
            return
        _key = (x, y, w, h)
        if _key in seen:
            return
        seen.add(_key)
        rects.append((x, y, w, h))

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:  # noqa: BLE001
        logger.debug("枚举输入法窗口失败", exc_info=True)
    return rects


def paint_ime_windows(
    img,
    monitor_left: int = 0,
    monitor_top: int = 0,
    color: tuple[int, int, int] = (110, 110, 120),
) -> bool:
    """把输入法候选窗在 img 上涂成中性色（就地修改 img），让模型看屏时直接"透过"。

    返回是否真的涂了至少一个窗口。monitor_left / monitor_top 为 img 对应 monitor
    在虚拟屏幕的原点（与 paint_own_windows 同约定）。
    """
    if not _HAS_WIN32:
        return False
    rects = ime_window_rects()
    if not rects:
        return False
    try:
        from PIL import ImageDraw
    except Exception:  # noqa: BLE001
        return False
    W, H = img.size
    draw = ImageDraw.Draw(img)
    painted = False
    for (x, y, w, h) in rects:
        lx = x - monitor_left
        ly = y - monitor_top
        rx = lx + w
        ry = ly + h
        cl, ct, cr, cb = max(0, lx), max(0, ly), min(W, rx), min(H, ry)
        if cr > cl and cb > ct:
            draw.rectangle([cl, ct, cr, cb], fill=color)
            painted = True
    return painted
