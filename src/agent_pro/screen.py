# -*- coding: utf-8 -*-
"""agent_pro · 屏幕/控件操作层（对标 Windows-MCP 的 State-Tool + Click-Tool）。

ui_elements：把目标窗口的可交互元素编号列出（类型/名称/中心坐标）——
    LLM 按「编号」点击而不是瞎猜坐标，这是 Windows-MCP 在无视觉模型条件下
    也能精确操作的核心机制（读 UIA 无障碍树）。
click_element：两种点击方式——
    ① index：点 ui_elements 清单里的第 N 个元素（确定性，优先）；
    ② desc：视觉兜底——VLM 看屏定位描述物中心坐标后点击（树残缺的
       Electron/自绘应用走这条路，等价于"手机伴侣点外卖式"的看屏操作）。

缓存策略：ui_elements 结果缓存在模块级（_ui_cache），供同轮会话内
click_element(index=N) 使用；ui_elements 重新调用即刷新。
"""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger("agent_pro")

# 最近一次 ui_elements 的元素清单：[{"name","ctype","x","y"}]
_ui_cache: list[dict] = []
_ui_cache_window: str = ""
_ui_cache_ts: float = 0.0
# 与 _ui_cache 同索引的 UIA 控件引用（供 Invoke 直触；None 表示该元素没抓到控件）
_ui_ctrls: list = []

# 【借 dsh-computer-use】观察快照有 TTL：过期快照上的动作一律拒绝——
# 把「不许盲操作」从提示词约束变成结构性强制（我们键盘兜底盲发按键吃过亏）。
_SNAPSHOT_TTL_S = 20.0

# 【借 dsh 安全护栏】危险词闸：desc/query 命中即拒，要求显式确认
_DANGER_RE = re.compile(r"删除|清空|卸载|格式化|支付|付款|转账|汇款|下单|购买|密码")

# 视为「可交互」的 UIA 控件类型尾巴
_INTERACTIVE_TAIL = (
    "ButtonControl", "HyperlinkControl", "ListItemControl", "MenuItemControl",
    "TabItemControl", "TreeItemControl", "ComboBoxControl", "CheckBoxControl",
    "RadioButtonControl", "EditControl", "SliderControl", "SplitButtonControl",
    "DataItemControl", "SpinnerControl",
)
# 额外保留的纯文本（供 LLM 读屏理解上下文，不算交互元素但列出来）
_TEXT_TAIL = ("TextControl",)

_COORD_RE = re.compile(
    r"[XxＸ]\s*[:=＝]?\s*(\d{1,5})\s*[,，、]\s*[YyＹ]\s*[:=＝]?\s*(\d{1,5})")


# ---------------------------------------------------------------------------
# DPI 一致性守卫（2026-10-16 实测发现的地基问题）
#
# UIA 的 BoundingRectangle 与鼠标坐标必须是**同一套像素**。本机实测：
#   进程 DPI 感知 = 0 (UNAWARE) → GetSystemMetrics(0,1) 返回被虚拟化的 2560x1440，
#   而同一台机器的物理分辨率是 3840x2160、系统缩放 150%（GetDpiForSystem=144）。
# 两套坐标系混用 → 点击整体偏移一个缩放比 → "能打开软件但点不到按钮"。
# 主软件进程已由 webapp.py SetProcessDPIAware；此处是独立运行/包测场景的兜底。
# ---------------------------------------------------------------------------
_dpi_checked = False
_dpi_state = "unchecked"


def _ensure_dpi_aware() -> str:
    """确保本进程 DPI 感知（幂等，只判定一次，绝不抛）。返回状态说明。"""
    global _dpi_checked, _dpi_state
    if _dpi_checked:
        return _dpi_state
    _dpi_checked = True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        hp = k32.GetCurrentProcess()
        val = 0
        try:
            shcore = ctypes.windll.shcore
            shcore.GetProcessDpiAwareness.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            shcore.GetProcessDpiAwareness.restype = ctypes.c_long
            cur = ctypes.c_int(0)
            if int(shcore.GetProcessDpiAwareness(hp, ctypes.byref(cur))) == 0:
                val = int(cur.value)
        except Exception:
            val = 0
        if val >= 2:
            _dpi_state = "per-monitor aware（UIA 与鼠标同像素，正常）"
            return _dpi_state
        if val == 1:
            _dpi_state = "system aware（主屏物理像素，副屏缩放不同时可能偏）"
            return _dpi_state
        # val == 0：UNAWARE —— 尝试提升（进程已设过则 API 失败，无害）
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                    ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                _dpi_state = "已提升为 per-monitor v2（此前 UNAWARE）"
                return _dpi_state
        except Exception:
            # 这条 DPI 提升路径不可用（老系统），试下一种方式
            pass
        try:
            if int(ctypes.windll.shcore.SetProcessDpiAwareness(2)) == 0:
                _dpi_state = "已提升为 per-monitor（此前 UNAWARE）"
                return _dpi_state
        except Exception:
            # shcore 方式也不可用 → 保持原状（截图仍可用，只是坐标可能偏移）
            pass
        try:
            if ctypes.windll.user32.SetProcessDPIAware():
                _dpi_state = "已提升为 system aware（此前 UNAWARE）"
                return _dpi_state
        except Exception as e:  # noqa: BLE001
            _dpi_state = "DPI 提升失败（点歪风险）：" + str(e)
            return _dpi_state
        _dpi_state = "DPI 提升未生效（保持 UNAWARE，点歪风险）"
        return _dpi_state
    except Exception as e:  # noqa: BLE001
        _dpi_state = "DPI 检查异常（忽略）：" + str(e)
        return _dpi_state


def _screen_rect() -> tuple[int, int, int, int]:
    """虚拟桌面矩形（多显示器）：(vx, vy, vw, vh)。失败退回主屏假设。"""
    _ensure_dpi_aware()
    try:
        import ctypes
        u = ctypes.windll.user32
        vx, vy = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))
        vw, vh = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
        if vw > 0 and vh > 0:
            return vx, vy, vw, vh
    except Exception:  # noqa: BLE001
        pass
    return 0, 0, 1920, 1080


def _clamp(x: int, y: int) -> tuple[int, int] | None:
    vx, vy, vw, vh = _screen_rect()
    # 允许小幅越界（窗口略出屏），大幅越界视为坐标异常
    if not (vx - 200 <= x < vx + vw + 200 and vy - 200 <= y < vy + vh + 200):
        return None
    return (max(vx, min(int(x), vx + vw - 1)), max(vy, min(int(y), vy + vh - 1)))


def _do_click(x: int, y: int, double: bool = False) -> str:
    _ensure_dpi_aware()  # 坐标体系先对齐，否则 150% 缩放下整体点歪
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "点击未执行：pyautogui 未安装（pip install pyautogui）"
    try:
        if double:
            pyautogui.doubleClick(int(x), int(y))
        else:
            pyautogui.click(int(x), int(y))
        return f"已在 ({x},{y}) {'双击' if double else '单击'}"
    except Exception as e:  # noqa: BLE001
        return f"点击失败：{e}"


# ---------------------------------------------------------------------------
# UIA 直触（2026-10-16 关键增强，对标 Windows-MCP/UFO2 的"不点鼠标也能操作"）
#
# uiautomation 2.0.29 的 Control.Click() 实测是「MoveCursorToInnerPos + 鼠标点击」，
# 即所有"控件点击"本质仍是模拟鼠标：需要窗口在前台可见、坐标正确、且会抢用户鼠标。
# 而真实窗口扫描显示 30 个交互控件里 28 个支持 Invoke Pattern —— 用 Pattern 直接
# 触发控件的默认动作，**不移动鼠标、不需要窗口在前台、不受遮挡影响、与服务端坐标无关**。
# 这是"真正帮忙点软件"最稳的一条路；不支持 Pattern 的（Electron 自绘等）再回退坐标点击。
# ---------------------------------------------------------------------------
def _uia_activate(ctrl, double: bool = False) -> "str | None":
    """用 UIA Pattern 触发控件默认动作。成功返回 pattern 名，失败/不支持返回 None。

    双击语义（列表项激活/播放这类）不走直触——Invoke 不等价于双击，交回坐标双击。
    """
    if ctrl is None or double:
        return None
    tries = (
        ("Invoke", lambda c: c.GetInvokePattern().Invoke(waitTime=0)),
        ("LegacyDoDefault", lambda c: c.GetLegacyIAccessiblePattern().DoDefaultAction()),
        ("Toggle", lambda c: c.GetTogglePattern().Toggle()),
        ("ExpandCollapse", lambda c: c.GetExpandCollapsePattern().Expand()),
        ("SelectionItem", lambda c: c.GetSelectionItemPattern().Select()),
    )
    for label, fn in tries:
        try:
            fn(ctrl)
            return label
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------------------
# ui_elements：State-Tool 式编号元素清单
# ---------------------------------------------------------------------------
def _walk_elements(scope, maxn: int) -> list[dict]:
    """UIA 树遍历（限深限量），收集可交互 + 有名文本元素。"""
    import uiautomation as auto  # type: ignore
    out: list[dict] = []

    def rect_of(c):
        try:
            r = c.BoundingRectangle
            if r is None or r.width() <= 1 or r.height() <= 1:
                return None
            return r
        except Exception:  # noqa: BLE001
            return None

    def visit(c, depth: int):
        if len(out) >= maxn or depth > 9:
            return
        try:
            ctype = c.ControlTypeName or ""
        except Exception:  # noqa: BLE001
            return
        keep = ctype.endswith(_INTERACTIVE_TAIL) or ctype.endswith(_TEXT_TAIL)
        if keep:
            name = ""
            try:
                name = (c.Name or "").strip()
            except Exception:  # noqa: BLE001
                name = ""
            r = rect_of(c)
            if r is not None:
                interactive = ctype.endswith(_INTERACTIVE_TAIL)
                # 没名字的纯文本没价值；可交互控件即使没名也保留（可按类型点）
                if name or interactive:
                    out.append({
                        "name": name[:60],
                        "ctype": ctype[:-7] if ctype.endswith("Control") else ctype,
                        "x": int(r.xcenter()), "y": int(r.ycenter()),
                        "interactive": interactive,
                        "ctrl": c,   # UIA 控件引用：click_element 直触用，不经鼠标
                    })
                    if len(out) >= maxn:
                        return
        try:
            children = c.GetChildren()
        except Exception:  # noqa: BLE001
            return
        for ch in children:
            visit(ch, depth + 1)
            if len(out) >= maxn:
                return

    visit(scope, 0)
    return out


def _scopes_from_app(app: str) -> list:
    """窗口定位：主窗口 + 同进程全部顶层窗口（含菜单/弹层）。

    菜单弹层是独立 HWND，只扫主窗口会漏掉（实测 Win11 记事本点『文件』后
    弹层在另一个窗口里）。中文叫法与实际标题对不上时走应用索引反查 exe。
    """
    import uiautomation as auto  # type: ignore
    from src.llm.agent_tools import _ui_window, _win32_windows
    scopes: list = []
    primary = _ui_window(app)
    if primary is not None:
        scopes.append(primary)
    t = (app or "").lower()
    exe_names: set[str] = set()
    try:
        from .app_index import build_index, match
        for rec, _s in match(app, build_index()):
            v = ((rec.get("exe") or (rec.get("token") or {}).get("value") or "")
                 .rsplit("\\", 1)[-1].lower())
            if not v:
                continue
            exe_names.add(v)
            # Electron/桌面应用常见 launcher 家族：索引绑定的是 XxxLauncher.exe，
            # 真实窗口进程往往是同前缀主 exe（如 SodaMusicLauncher → SodaMusic.exe）。
            stem = v[:-4] if v.endswith(".exe") else v
            if stem.endswith("launcher") and len(stem) > 8:
                exe_names.add(stem[:-8] + ".exe")
    except Exception as e:  # noqa: BLE001
        logger.debug("[agent_pro] 应用索引反查窗口失败：%s", e)
    seen: set[int] = set()
    try:
        seen.add(int(primary.getNativeWindowHandle()))  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    for h, title, exe in _win32_windows():
        el = (exe or "").lower()
        if not (t in (title or "").lower() or t in el or (exe_names and el in exe_names)):
            continue
        if int(h) in seen:
            continue
        seen.add(int(h))
        try:
            c = auto.ControlFromHandle(h)
            if c is not None:
                scopes.append(c)
        except Exception:  # noqa: BLE001
            pass
    return scopes


def ui_elements(args: dict, flags: dict) -> str:
    global _ui_cache, _ui_cache_window, _ui_cache_ts, _ui_ctrls
    app = str(args.get("app") or "").strip()
    maxn = int(args.get("max") or flags.get("ui_elements_max", 60) or 60)
    try:
        import uiautomation  # noqa: F401
    except Exception:  # noqa: BLE001
        return "UI 自动化未安装：pip install uiautomation（需管理员权限运行）"
    # 【2026-10-16 修复·漏 import】`_scopes_from_app()` 内部有
    # `from src.llm.agent_tools import _ui_window, _win32_windows`，本函数**漏了同款**，
    # 于是"不指定 app 时列取全屏 UI 元素"这条路径（`[_ui_window("")]`）恒抛 NameError。
    from src.llm.agent_tools import _ui_window
    scopes = _scopes_from_app(app) if app else [_ui_window("")]
    if app and not scopes:
        return (f"没找到标题或进程名含「{app}」的窗口（应用可能未启动；可先 open_app，"
                f"或改用英文/exe 名重试，如 notepad）")
    try:
        els: list[dict] = []
        seen: set[tuple] = set()
        for scope in scopes:
            for e in _walk_elements(scope, max(20, min(maxn, 120))):
                key = (e["name"], e["x"], e["y"])
                if key in seen:
                    continue
                seen.add(key)
                els.append(e)
        els = els[:max(20, min(maxn, 120))]
    except Exception as e:  # noqa: BLE001
        return f"枚举控件失败（该应用可能未对 UIA 暴露，改用 click_element desc=… 视觉定位）：{e}"
    if not els:
        return (f"「{app or '前台'}」窗口未枚举到可用元素——它多半没开无障碍树"
                f"（Electron 应用常见）。请改用 click_element desc=「按钮描述」视觉定位点击。")
    _ui_cache = els
    _ui_ctrls = [e.get("ctrl") for e in els]
    _ui_cache_window = app
    _ui_cache_ts = time.monotonic()   # 【修】此前从未赋值 → 编号点击恒判"快照过期"
    lines = [f"「{app or '前台'}」可交互/文本元素 {len(els)} 个"
             f"（含弹出菜单/对话框；点第 N 个 → click_element index=N）："]
    for i, e in enumerate(els, 1):
        tag = "" if e["interactive"] else "（文本）"
        lines.append(f"{i}. [{e['ctype']}{tag}] {e['name'] or '（未命名）'} @ ({e['x']},{e['y']})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# click_element：按编号（确定性）或按描述（视觉定位）点击
# ---------------------------------------------------------------------------
async def click_element(args: dict, ctx: dict, flags: dict) -> str:
    global _ui_cache
    idx = args.get("index")
    desc = str(args.get("desc") or "").strip()
    double = bool(args.get("double"))
    confirmed = bool(args.get("confirmed"))

    # 【借 dsh 安全护栏】危险描述：删除/支付类描述必须显式 confirmed=true 才执行
    if desc and _DANGER_RE.search(desc) and not confirmed:
        return (f"拒绝执行：描述「{desc}」命中危险操作词。"
                f"如果用户已明确同意，请在参数里加 confirmed=true 重试；否则向用户确认。")

    # ① 编号点击（确定性路径）
    if idx is not None:
        try:
            i = int(idx)
        except Exception:  # noqa: BLE001
            return f"index 必须是数字，收到：{idx!r}"
        if not _ui_cache:
            return "编号清单为空：请先调用 ui_elements 拿到编号列表"
        age = time.monotonic() - _ui_cache_ts
        if age > _SNAPSHOT_TTL_S:
            return (f"快照已过期（{_ui_cache_window or '上次枚举'} 距今 {age:.0f} 秒"
                    f"> {_SNAPSHOT_TTL_S:.0f} 秒）：界面可能已变化。请重新调用 ui_elements "
                    f"刷新清单后再按编号点，不许凭旧编号盲点。")
        if not (1 <= i <= len(_ui_cache)):
            return f"index 超界：当前清单只有 {len(_ui_cache)} 个（1~{len(_ui_cache)})"
        e = _ui_cache[i - 1]
        # ① 优先 UIA 直触：不移动鼠标、不需要窗口在前台、不受遮挡（实测 93% 控件支持）
        ctrl = _ui_ctrls[i - 1] if i - 1 < len(_ui_ctrls) else None
        via = _uia_activate(ctrl, double)
        if via:
            return ("已用 UIA 直触（" + via + "）触发元素" + str(i) + " ["
                    + e["ctype"] + "] " + (e["name"] or "（未命名）")
                    + "——未移动鼠标、不依赖窗口在前台。可用 state_snapshot / wait_for 核实效果。")
        # ② 回退坐标点击（Electron 自绘等无 Pattern 的控件）
        c = _clamp(e["x"], e["y"])
        if c is None:
            return f"元素 {i} 坐标异常 ({e['x']},{e['y']})，未点击"
        r = _do_click(c[0], c[1], double)
        return (f"{r}：元素{i} [{e['ctype']}] {e['name'] or '（未命名）'}"
                f"（该控件无 Invoke Pattern，已回退坐标点击）"
                + ("；如需确认效果可跟 state_snapshot / wait_for" if not double else ""))

    # ② 视觉定位点击（树残缺应用的兜底，等价 Windows-MCP Click-Tool + 视觉）
    if not desc:
        return "click_element 需要 index（先 ui_elements）或 desc（描述屏幕上要点的东西）"
    from src.llm.agent_tools import _see_screen
    q = ("在屏幕上找到「" + desc + "」。"
         "只输出一行，格式严格为：X=数字,Y=数字（该元素中心的屏幕像素坐标）。"
         "屏幕上没有它就只输出 NONE。不要输出任何其它文字。")
    ans = await _see_screen(q, ctx)
    m = _COORD_RE.search(ans or "")
    if not m:
        return (f"视觉定位失败（VLM 回答：{(ans or '空')[:120]}）。"
                f"可换更明确的描述，或改用 ui_elements 枚举控件后按编号点。")
    c = _clamp(int(m.group(1)), int(m.group(2)))
    if c is None:
        return f"VLM 给出的坐标越界：({m.group(1)},{m.group(2)})，未点击"
    r = _do_click(c[0], c[1], double)
    return f"{r}：视觉定位「{desc}」"


# ---------------------------------------------------------------------------
# search_in_app：语义化「在应用里搜索」——借 dsh-computer-use 的
# computer_search_in_window 思路：定位输入框 → 点击聚焦 → 剪贴板粘贴
# （中文安全，pyautogui.write 对中文无效）→ 回车。一个工具覆盖
# 「汽水音乐搜歌 / 抖音搜索 / 浏览器搜索」，不用每个应用单独写流程。
# ---------------------------------------------------------------------------
async def search_in_app(args: dict, ctx: dict, flags: dict) -> str:
    app = str(args.get("app") or "").strip()
    query = str(args.get("query") or "").strip()
    if not app or not query:
        return "search_in_app 需要 app（应用名/窗口标题）和 query（要搜的内容）"
    confirmed = bool(args.get("confirmed"))
    if _DANGER_RE.search(query) and not confirmed:
        return (f"拒绝执行：搜索内容「{query}」命中危险操作词。"
                f"如用户已明确同意，请加 confirmed=true 重试。")
    if _DANGER_RE.search(app) and not confirmed:
        return f"拒绝执行：目标应用「{app}」命中危险操作词，请人工确认。"

    try:
        import pyautogui  # type: ignore
        import pyperclip  # type: ignore
    except Exception:  # noqa: BLE001
        return "输入依赖未安装：pip install pyautogui pyperclip"

    scopes = _scopes_from_app(app)
    if not scopes:
        return (f"没找到「{app}」的窗口（可能未启动）。请先 open_app，"
                f"再重试 search_in_app。")

    # 【借 dsh-computer-use】动作前让目标可见：尽力还原 + 前置窗口
    # （非交互测试进程可能被 Windows 前台锁挡住；软件本体由用户触发时通常能成）
    try:
        import ctypes
        h0 = scopes[0].getNativeWindowHandle()
        if isinstance(h0, (bytes, bytearray)):
            h0 = int.from_bytes(h0, "little")
        u = ctypes.windll.user32
        if u.IsIconic(int(h0)):
            u.ShowWindow(int(h0), 9)  # SW_RESTORE
        u.SetForegroundWindow(int(h0))
    except Exception:  # noqa: BLE001
        logger.debug("[agent_pro] search_in_app 前置聚焦尽力而为失败（忽略）")

    # 找可用的输入框：优先 Edit 控件；找不到退化为点窗口中央（很多应用点中央会激活搜索）
    import asyncio as _aio
    target = None
    try:
        for scope in scopes:
            for e in _walk_elements(scope, 80):
                if e.get("interactive") and e.get("ctype") == "Edit":
                    target = e
                    break
            if target:
                break
    except Exception as e:  # noqa: BLE001
        return f"枚举控件失败（可改用 click_element desc=「搜索框」视觉定位）：{e}"

    clicked = ""
    visual_note = ""
    if target:
        # 优先 UIA 直触聚焦（不动鼠标、不依赖前台）；失败才回退坐标点击
        tctrl = target.get("ctrl")
        if tctrl is not None:
            try:
                tctrl.SetFocus()
                clicked = ("已用 UIA 直触聚焦输入框 ["
                           + str(target.get("name") or "Edit") + "]（未移动鼠标）")
            except Exception:  # noqa: BLE001
                clicked = ""
        if not clicked:
            c = _clamp(int(target["x"]), int(target["y"]))
            if c:
                r = _do_click(c[0], c[1])
                clicked = f"已点击输入框 [{target.get('name') or 'Edit'}] @ ({c[0]},{c[1]})"
    if not clicked:
        # 【借 dsh 降级链】UIA 树残缺（Electron 常态）→ VLM 视觉定位搜索框
        try:
            from src.llm.agent_tools import _see_screen
            q = (f"这是电脑屏幕。找到「{app}」窗口里的搜索框或搜索图标，"
                 "只输出一行：X=数字,Y=数字（搜索框中心屏幕坐标）；"
                 "屏幕上没有该应用或找不到搜索框就只输出 NONE。不要输出其它文字。")
            ans = await _see_screen(q, ctx)
            m = _COORD_RE.search(ans or "")
            if m:
                c = _clamp(int(m.group(1)), int(m.group(2)))
                if c:
                    _do_click(c[0], c[1])
                    clicked = f"UIA 未暴露输入框，已按 VLM 视觉定位点击搜索框 @ ({c[0]},{c[1]})"
            else:
                visual_note = f"（VLM 视觉定位未找到搜索框，回答：{(ans or '')[:40]}——目标窗口可能不在可见屏幕上）"
        except Exception as e:  # noqa: BLE001
            logger.debug("[agent_pro] search_in_app 视觉定位失败：%s", e)
    if not clicked:
        w0 = scopes[0]
        try:
            r0 = w0.BoundingRectangle
            c = _clamp(int(r0.xcenter()), int(r0.ycenter()))
            if c:
                _do_click(c[0], c[1])
                clicked = f"未枚举到输入框，已点击「{app}」窗口中央（若搜索框未激活请改用 click_element desc=搜索框）"
        except Exception as e:  # noqa: BLE001
            return f"连窗口中央都点不了：{e}"
    await _aio.sleep(0.6)

    # 全选清空 → 剪贴板粘贴（CJK 安全）→ 回车
    cleared = ""
    try:
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        cleared = "已清空原内容；"
    except Exception:  # noqa: BLE001
        cleared = "清空失败（不影响粘贴）；"
    try:
        pyperclip.copy(query)
        await _aio.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
        await _aio.sleep(0.4)
        pyautogui.press("enter")
    except Exception as e:  # noqa: BLE001
        return f"{clicked}；但粘贴/回车失败：{e}"
    await _aio.sleep(0.5)
    return (f"{clicked}；{cleared}已粘贴「{query}」并回车{visual_note}。\n"
            f"验证建议：音乐类应用用 media_state 查播放状态；"
            f"其它用 wait_for kind=window/screen 或 state_snapshot 核实结果。")
