# -*- coding: utf-8 -*-
"""【AGENT·2026-09-27 扩】电脑操作工具集：让外接 LLM 通过 function calling 真正操作这台电脑。

设计原则（对齐本仓 cascade_brain 的"完全独立、可单测、可随时摘掉"）
----------------------------------------------------------------
1. **每一个工具都不抛异常**：任何依赖缺失 / 操作失败都返回说明字符串，绝不让异常冒泡打断主流程。
2. **每个工具都优雅降级**：browser-use / pyautogui / pycaw / pygetwindow / pyperclip 没装时，
   返回清晰的"需要安装 XXX"提示，而不是崩溃；简单场景（打开网址、媒体键）仍可用系统能力完成。
3. **安全闸**：run_command 走白名单；文件读写锁定在 sandbox 目录（防越权）；媒体键用 Windows 原生 VK。
4. **纯后端、零 UI 依赖**：本文件不 import 任何 webapp / Unity 代码。

工具来源（开源、可直接 pip 接入）
-------------------------------
- 网页 / 刷视频 / 操作网站：browser-use（https://github.com/browser-use/browser-use），最成熟的开源"AI 操作浏览器"。
- 桌面 GUI 控制（点击 / 输入 / 坐标 / 键 / 鼠标移动）：pyautogui（开源事实标准）。
- 窗口枚举 / 聚焦：pygetwindow（开源）。
- 剪贴板：pyperclip（开源）。
- 系统音量：pycaw（开源，Windows）。
- 截图 + 视觉理解（VLM）：pyautogui 截图 + 多模态大模型（qwen-vl-plus 等）"看"屏幕。
- 媒体播放控制：Windows 原生 media 键（ctypes，无需任何依赖）。
- 联网搜索：duckduckgo_search（ddgs）。

LLM 怎么"调用这些工具"：见 agent_brain.py 的 function-calling 循环。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import platform
import re
import subprocess
import time
import webbrowser

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 仓库根 / sandbox 目录
# --------------------------------------------------------------------------
def _repo_root() -> str:
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.exists(os.path.join(d, "config.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


# 工作区（文件读写的边界）
#
# 【2026-11-04 修·越界时的"假绿"】原实现遇到 `../` 或绝对路径时**不报错**，
# 而是取 basename 塞进沙箱：`write_file("D:/Windows/System32/x.dll")` 会真的
# 写到 `agent_sandbox/x.dll`，还返回"已写入文件：x.dll"。用户以为改的是那个文件，
# 实际写到了别处 —— 这正是本仓库一直在防的"假绿"，只不过这次骗的是用户。
# 现在改成**明确拒绝并说明原因**。
#
# 边界本身可配：`agent.workspace_dir` 为空 → `repo/agent_sandbox`；
# 填绝对路径 → 对准真实项目（"帮我改 D:/project 里的文件"才有意义）。


class WorkspaceEscape(Exception):
    """目标路径越出工作区。"""


_WORKSPACE = ""


def configure_workspace(path: str) -> None:
    """由 agent_brain 在构建 ctx 时调用（对应 config 的 agent.workspace_dir）。"""
    global _WORKSPACE
    _WORKSPACE = (path or "").strip()


def workspace_dir() -> str:
    """当前工作区的绝对路径（不存在则尝试创建）。"""
    d = _WORKSPACE or os.path.join(_repo_root(), "agent_sandbox")
    if not os.path.isabs(d):
        d = os.path.join(_repo_root(), d)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:  # noqa: BLE001  建不出来交给后续 open() 报错，不在这里吞
        pass
    return os.path.abspath(d)


# 兼容旧名：仓库里还有按 _sandbox_dir() 引用的地方
_sandbox_dir = workspace_dir


def _safe_path(name: str, root: str = "") -> str:
    """把用户给的路径约束在工作区内；越界抛 `WorkspaceEscape`。

    规则：
      · 相对路径 → 相对**工作区**解析（不是进程 CWD）；
      · 绝对路径 → 必须落在工作区内，否则拒绝；
      · `..` / 符号链接 → 先 realpath 归一，再比前缀，逃不出去；
      · Windows 大小写不敏感 → 用 normcase 比较；跨盘符直接判越界。
    """
    ws = os.path.realpath(os.path.abspath(root or workspace_dir()))
    raw = (name or "").strip()
    if not raw:
        raise WorkspaceEscape("未提供文件名")
    raw = raw.replace("\\", "/")
    if os.path.isabs(raw) or (len(raw) > 1 and raw[1] == ":"):
        cand = os.path.abspath(raw)
    else:
        cand = os.path.abspath(os.path.join(ws, raw))
    cand = os.path.realpath(cand)
    try:
        inside = (os.path.commonpath([os.path.normcase(cand), os.path.normcase(ws)])
                  == os.path.normcase(ws))
    except ValueError:  # 跨盘符
        inside = False
    if not inside:
        raise WorkspaceEscape(
            "路径越界：%s 不在工作区内（当前工作区：%s）。要读写别处，"
            "请把文件放进工作区，或让用户把 agent.workspace_dir 指到那个目录。"
            % (name, ws))
    return cand


# --------------------------------------------------------------------------
# 通用应用名 -> 启动令牌
# --------------------------------------------------------------------------
_COMMON_APPS = {
    ("网易云音乐", "网易云", "netease", "cloudmusic"): "cloudmusic",
    ("微信", "wechat", "weixin"): "WeChat",
    ("qq",): "QQ",
    ("哔哩哔哩", "b站", "bilibili"): "https://www.bilibili.com",
    ("抖音", "douyin", "tiktok"): "https://www.douyin.com",
    ("快手", "kuaishou"): "https://www.kuaishou.com",
    ("优酷",): "https://www.youku.com",
    ("爱奇艺",): "https://www.iqiyi.com",
    ("腾讯视频",): "https://v.qq.com",
    ("youtube",): "https://www.youtube.com",
    ("spotify",): "Spotify",
    ("浏览器", "chrome", "edge", "网页"): "https://www.baidu.com",
    ("记事本",): "notepad",
    ("计算器",): "calc",
    ("文件管理器", "资源管理器"): "explorer",
    ("画图",): "mspaint",
    ("终端", "windows terminal"): "wt",
    ("命令提示符", "cmd"): "cmd",
    ("设置",): "ms-settings:",
    ("控制面板",): "control",
}


def _target_visible(tok: str):
    """启动后核验：目标窗口真的出现了吗？

    返回 True=看到了 / False=没看到 / None=核验不了（无法枚举窗口）。
    核验不了时**不得据此判失败**——那只是没证据，不是反证。

    为什么必须有（2026-11-03 实测）：explorer 委托**不一定成事** ——
    Win11 上 Store 化的"记事本"经 ``explorer.exe notepad`` 开不出来
    （``os.startfile("notepad")`` 才开得出来）。旧代码不看结果就回报
    "已通过 Shell 委托启动：notepad" ⇒ **工具报成功、事没办成**（假绿）。
    核验不到窗口就必须换通道，不能报成功。
    """
    base = (tok or "").strip().strip('"').lower()
    if not base:
        return None
    base = os.path.basename(base)
    if base.endswith(".exe"):
        base = base[:-4]
    if not base:
        return None
    try:
        rows = _win32_windows()
    except Exception:  # noqa: BLE001
        return None  # 核验不了 → 交回上层，别拿"没证据"当"失败"
    for _hwnd, title, exe in rows:
        t = (title or "").lower()
        e = (exe or "").lower()
        # 标题匹配（中文应用名 / 文档名）+ 可执行文件名匹配（英文应用名）
        if base in t or base in e:
            return True
    return False


def _launch_token(tok: str) -> str:
    """启动一个应用/文件/协议，两条通道 + 启动后核验。

    通道 1（explorer 委托）：launcher 型应用（汽水音乐等 Electron）唯一可靠通道，
    经 cmd start / os.startfile / Popen 直启会 code 0 秒退、不产生任何子进程
    （2026-10-16 本机六路径对照实测）。但它**开不出 Store 化的系统应用**
    （2026-11-03 实测：explorer.exe notepad 开不出记事本）。

    通道 2（os.startfile）：系统/Store 应用的可靠通道。

    两条都走完仍核验不到窗口时，如实说"没检测到窗口"，**不报成功**。
    """
    tok = (tok or "").strip()
    if not tok:
        return "未提供要启动的目标"
    if tok.startswith("http://") or tok.startswith("https://"):
        webbrowser.open(tok)
        return f"已在默认浏览器打开 {tok}"
    first_err = ""
    try:
        subprocess.Popen(["explorer.exe", tok], shell=False)
    except Exception as e:  # noqa: BLE001
        first_err = str(e)
    else:
        first_err = ""
    # 核验通道 1 的结果：开不出就得换通道，不能照报成功
    vis = _target_visible(tok)
    if vis is False:
        time.sleep(1.2)
        vis = _target_visible(tok)
    if vis is not False:
        return f"已启动：{tok}（Shell 委托）"
    # 通道 2：os.startfile（系统/Store 应用可靠通道）
    try:
        os.startfile(tok)
    except Exception as e:  # noqa: BLE001
        return (f"启动 {tok} 失败：Shell 委托未生效"
                f"{('，' + first_err) if first_err else ''}；startfile 报错：{e}")
    time.sleep(1.2)
    if _target_visible(tok) is not False:
        return f"已启动：{tok}（startfile 兜底通道；Shell 委托未生效）"
    return (f"已尝试启动 {tok}（两条通道都试过），但未检测到对应窗口 —— "
            f"请人工确认是否真的打开了，不要假定成功")


def _open_app(target: str, a11y: bool = False) -> str:
    """打开应用/网址。

    a11y=True 时优先用带 `--force-renderer-accessibility` 的方式启动：Electron 应用
    （汽水音乐 / 网易云音乐 / 抖音桌面端等）带上这个参数后，内部控件才会对 UIA 暴露，
    后续 ui_click / play_song 才点得到搜索框和播放键。仅放歌这类需要接着操作控件的
    场景会传 True；普通"打开一下"保持原行为，避免给无关应用塞未知参数。
    """
    t = (target or "").strip()
    if not t:
        return "未提供要打开的应用或网址"
    if t.startswith("http://") or t.startswith("https://"):
        webbrowser.open(t)
        return f"已在默认浏览器打开 {t}"
    low = t.lower()
    for keys, tok in _COMMON_APPS.items():
        if t in keys or low in [k.lower() for k in keys]:
            return _launch_token(tok)
    if os.path.exists(t):
        # 走同一条「双通道 + 启动后核验」路径：直报"已启动"同样是假绿
        return _launch_token(t)
    # 3) 模糊匹配本机已安装应用（开始菜单 / Get-StartApps）——覆盖汽水音乐、抖音桌面端等
    #    未列入白名单的应用。装了就能开，没装才返回失败提示（不假装启动）。
    fuzzy = _fuzzy_launch(t, a11y=a11y)
    if fuzzy:
        return fuzzy
    # 4) 兜底：当作命令直接 start（可能失败，但给明确反馈，而不是静默吞掉）
    return _launch_token(t)


def _fuzzy_launch(target: str, a11y: bool = False) -> str:
    """按名称模糊匹配本机已安装应用并启动（仅 Windows）。

    覆盖白名单之外的应用，如汽水音乐、各类国产软件、商店 UWP 应用。优先匹配
    Get-StartApps 的 AppID（用 shell:AppsFolder 启动，UWP/商店应用必须用这个）；
    其次搜索开始菜单 .lnk 快捷方式（Win32 应用）。找不到返回空串（交给上层兜底）。

    a11y=True（放歌场景）：解析出真实 exe 后带 --force-renderer-accessibility 启动，
    让 Electron 应用的内部控件对 UIA 可见。解析/启动失败一律退回普通启动，
    绝不能因为"想增强无障碍"导致应用打不开。
    """
    if platform.system() != "Windows":
        return ""
    try:
        # 3a) Get-StartApps 按名称包含匹配 → AppID → shell:AppsFolder 启动
        safe = (target or "").replace("'", "''")
        ps = (
            "Get-StartApps | Where-Object { $_.Name -like '*" + safe
            + "*' } | Select-Object -First 1 | ForEach-Object { $_.AppID }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, errors='replace', timeout=20)
        appid = (out.stdout or "").strip()
        if appid:
            return _launch_appid_a11y(appid, target)
        # 3b) 开始菜单 .lnk 快捷方式模糊匹配（Win32 应用）
        found = _find_startmenu_lnk(target)
        if found:
            if a11y and _launch_lnk_a11y(found):
                return f"已带无障碍参数启动：{target}（{found}）"
            subprocess.Popen(["explorer.exe", found], shell=False)
            return f"已启动：{target}（{found}）"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _electron_main_exe(exe: str) -> str:
    """launcher 型 exe → 猜测主程序 exe（同目录优先同名、其次最大的 exe）。

    【2026-10-16 实测】SodaMusicLauncher.exe 既是 launcher 也是死胡同：它以 code 0
    秒退、不拉起任何子进程（无论 cmd/startfile/Popen/ShellExecute），只有经
    explorer 委托才起得来——但 explorer 无法传 --force-renderer-accessibility。
    所以带参启动必须找到主 exe（本例 E:\\Soda Music\\3.5.2\\SodaMusic.exe）。
    """
    d = os.path.dirname(exe)
    base = os.path.basename(exe).lower()
    if "launcher" not in base and "启动" not in base:
        return exe
    stem = base.replace("launcher", "").replace(".exe", "").strip("-_ ")
    best, best_size = "", 0
    try:
        for f in os.listdir(d):
            low = f.lower()
            if not low.endswith(".exe"):
                continue
            if any(k in low for k in ("unins", "setup", "update", "crashpad",
                                      "elevate", "installer")):
                continue
            p = os.path.join(d, f)
            try:
                sz = os.path.getsize(p)
            except Exception:  # noqa: BLE001
                continue
            if stem and stem in low:
                return p
            if sz > best_size:
                best, best_size = p, sz
    except Exception:  # noqa: BLE001
        pass
    return best or exe


def _launch_lnk_a11y(lnk: str) -> bool:
    """解析 .lnk 的目标 exe，带 --force-renderer-accessibility 启动。成功返回 True。

    Electron 应用只有带这个参数启动，内部控件才会对 UIA 暴露（否则 ui_click / play_song
    找不到任何按钮）。解析不到路径 / 启动失败 → 返回 False，由调用方退回普通启动。
    【2026-10-16 补】launcher 型快捷方式先换算成主 exe 再带参直启；主 exe 直启也
    失败（如汽水音乐，仅 explorer 委托能起）时退回 explorer 普通启动——"能开"优先。
    """
    try:
        ps = ("$s=New-Object -ComObject WScript.Shell;"
              "$sc=$s.CreateShortcut('" + str(lnk).replace("'", "''") + "');"
              "if($sc){$sc.TargetPath}")
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, errors='replace', timeout=20)
        txt = (out.stdout or "").strip()
        exe = txt.splitlines()[0].strip() if txt else ""
        if not exe or not exe.lower().endswith(".exe") or not os.path.exists(exe):
            return False
        main = _electron_main_exe(exe)
        try:
            subprocess.Popen([main, "--force-renderer-accessibility"])
            time.sleep(2.5)
            # 真验证：主 exe 进程必须真的存活（launcher/主exe 都可能 Popen 成功但秒退）
            import psutil  # type: ignore
            want = os.path.basename(main).lower()
            for _p in psutil.process_iter(["name"]):
                if (( _p.info["name"] or "").lower() == want):
                    return True
            # Popen 成功但进程秒退（launcher 委托型）→ explorer 普通启动兜底
            subprocess.Popen(["explorer.exe", lnk], shell=False)
            return False
        except Exception:  # noqa: BLE001
            # 主 exe 直启失败（launcher 委托型）→ explorer 普通启动兜底
            subprocess.Popen(["explorer.exe", lnk], shell=False)
            return False
    except Exception:  # noqa: BLE001
        pass
    return False


def _find_startmenu_lnk(target: str) -> str:
    """在开始菜单（用户 + 公共）里找文件名（去 .lnk）含 target 的快捷方式，返回其路径。"""
    low = (target or "").lower()
    roots = []
    ap = os.environ.get("APPDATA")
    if ap:
        roots.append(os.path.join(ap, "Microsoft", "Windows", "Start Menu", "Programs"))
    prog = os.environ.get("ProgramData")
    if prog:
        roots.append(os.path.join(prog, "Microsoft", "Windows", "Start Menu", "Programs"))
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.lower().endswith(".lnk") and low in fn.lower():
                    return os.path.join(dirpath, fn)
    return ""


def _launch_appid_a11y(appid: str, name: str) -> str:
    """尽量用 exe 路径带 --force-renderer-accessibility 启动（Electron 应用控件才对 UIA 可见）。

    解析 AppID→可执行路径用 Shell.Application COM（PowerShell）。解析不到或失败，
    回退到原 shell:AppsFolder 启动，保证"能开"优先于"无障碍增强"。
    """
    try:
        ps = (
            "$sh=New-Object -ComObject Shell.Application;"
            "$f=$sh.NameSpace('shell:AppsFolder');"
            "$it=$f.Items()|Where-Object{$_.Path -eq '" + appid.replace("'", "''") + "'};"
            "if($it){$it.Path}"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, errors='replace', timeout=20)
        exe = (out.stdout or "").strip()
        if exe and exe.lower().endswith(".exe") and os.path.exists(exe):
            subprocess.Popen([exe, "--force-renderer-accessibility"])
            return f"已带无障碍参数启动：{name}（{exe}）"
    except Exception:  # noqa: BLE001
        pass
    # 回退：经 explorer 委托通过开始菜单 AppID 启动（cmd start 对部分商店应用无效）
    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + appid], shell=False)
    return f"已通过开始菜单启动：{name}（{appid}）"


# --------------------------------------------------------------------------
# 媒体播放控制：Windows 原生 media 键（零依赖）
# --------------------------------------------------------------------------
_MEDIA_VK = {
    "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
    "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
}


def _media_control(action: str) -> str:
    a = (action or "").strip().lower()
    if a in ("play", "pause"):
        a = "play_pause"
    if a not in _MEDIA_VK:
        return f"不支持的媒体操作：{action}（可用 play_pause/play/pause/next/previous/stop/vol_up/vol_down/mute）"
    if platform.system() != "Windows":
        return "媒体键控制仅支持 Windows（当前非 Windows 环境）"
    try:
        ctypes = __import__("ctypes")
        user32 = getattr(ctypes.windll, "user32", None)
        if user32 is None:
            return "无法访问 user32（媒体键不可用）"
        vk = _MEDIA_VK[a]
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
        return f"已发送媒体指令：{action}"
    except Exception as e:  # noqa: BLE001
        return f"发送媒体指令失败：{e}"


# --------------------------------------------------------------------------
# 系统音量：pycaw（Windows）
# --------------------------------------------------------------------------
def _get_volume_api():
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
        from comtypes import CLSCTX_ALL  # type: ignore
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return interface.QueryInterface(IAudioEndpointVolume)
    except Exception as e:  # noqa: BLE001
        return f"系统音量控制未安装（pip install pycaw comtypes）：{e}"


def _set_volume(pct: float) -> str:
    if platform.system() != "Windows":
        return "音量控制仅支持 Windows"
    vol = _get_volume_api()
    if isinstance(vol, str):
        return vol
    try:
        p = max(0.0, min(1.0, float(pct) / 100.0))
        vol.SetMasterVolumeLevelScalar(p, None)
        return f"系统音量已设为 {int(pct)}%"
    except Exception as e:  # noqa: BLE001
        return f"设置音量失败：{e}"


def _get_volume() -> str:
    if platform.system() != "Windows":
        return "音量控制仅支持 Windows"
    vol = _get_volume_api()
    if isinstance(vol, str):
        return vol
    try:
        return f"当前系统音量约为 {int(vol.GetMasterVolumeLevelScalar() * 100)}%"
    except Exception as e:  # noqa: BLE001
        return f"读取音量失败：{e}"


# --------------------------------------------------------------------------
# 窗口枚举 / 聚焦：pygetwindow + Win32 ctypes 兜底
# --------------------------------------------------------------------------
def _win32_windows() -> list:
    """ctypes EnumWindows 直接枚举可见顶层窗口 → [(hwnd, title)]。

    【2026-10-04 兜底】pygetwindow 底层同样是 EnumWindows，但实测这台机器上
    曾出现「运行中进程枚举结果全部空标题」的间歇状态（05:18 会话日志实锤：
    list_windows 返回『当前没有可见标题的窗口』，而焦点探测同一时刻却能看到
    窗口）。原因不明（疑似其内部状态/版本问题），所以这里保留一条不依赖
    pygetwindow 的纯 ctypes 路径：GetWindowTextW 失败时至少还能拿到窗口类名
    与进程名，供 ui_inspect/play_song 按**进程名**定位（Electron 应用窗口标题
    常为空，但 exe 名稳定，如 SodaMusic.exe）。
    """
    out = []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 2)
                user32.GetWindowTextW(hwnd, buf, n + 2)
                title = (buf.value or "").strip()
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                exe = ""
                try:
                    k32 = ctypes.windll.kernel32
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid.value)
                    if h:
                        buf2 = ctypes.create_unicode_buffer(260)
                        sz = wintypes.DWORD(260)
                        if k32.QueryFullProcessImageNameW(h, 0, buf2, ctypes.byref(sz)):
                            exe = (buf2.value or "").rsplit("\\", 1)[-1]
                        k32.CloseHandle(h)
                except Exception:  # noqa: BLE001
                    pass
                if title or exe:
                    out.append((int(hwnd), title, exe))
            except Exception:  # noqa: BLE001
                pass
            return True

        user32.EnumWindows(proto(_cb), 0)
    except Exception:  # noqa: BLE001
        pass
    return out


def _list_windows() -> str:
    try:
        import pygetwindow as gw  # type: ignore
    except Exception:  # noqa: BLE001
        gw = None
    titles = []
    if gw is not None:
        try:
            wins = gw.getAllWindows()
            titles = [w.title for w in wins if w.title.strip()]
        except Exception:  # noqa: BLE001
            titles = []
    if not titles:
        # 兜底：ctypes 直接枚举（标题 + 进程名都给，Electron 空标题窗口也有线索）
        try:
            rows = _win32_windows()
            titles = [t for (_h, t, _e) in rows if t]
            if not titles:
                exes = sorted({e for (_h, _t, e) in rows
                               if e and e.lower() not in ("explorer.exe",)})
                if exes:
                    return ("当前没有带标题的窗口；有可见窗口的进程：\n"
                            + "\n".join(f"- {e}" for e in exes[:20]))
        except Exception:  # noqa: BLE001
            pass
    if not titles:
        return "当前没有可见标题的窗口"
    return "已打开的窗口：\n" + "\n".join(f"- {t}" for t in titles[:30])


def _focus_window(name: str) -> str:
    if not name:
        return "未提供窗口名"
    w = None
    hwnd = 0
    try:
        import pygetwindow as gw  # type: ignore
        matches = gw.getWindowsWithTitle(name)
        if matches:
            w = matches[0]
            hwnd = int(getattr(w, "_hWnd", None) or getattr(w, "hwnd", 0) or 0)
    except Exception:  # noqa: BLE001
        w = None
    if w is None and not hwnd:
        # pygetwindow 找不到 → ctypes 兜底（标题或进程名匹配）
        low = str(name).lower()
        for h, t, exe in _win32_windows():
            if low in t.lower() or low in exe.lower().rsplit(".", 1)[0]:
                hwnd = h
                break
        if not hwnd:
            return f"没找到标题含「{name}」的窗口"
    # 先还原再聚焦；pygetwindow 的 activate/restore 在部分机器上会抛
    # comtypes RECT TypeError（05:18 会话实锤），失败即走 Win32 兜底，绝不让
    # "聚焦"这个辅助动作把整条工具链炸掉。
    try:
        if w is not None and getattr(w, "isMinimized", False):
            try:
                w.restore()
            except Exception:  # noqa: BLE001
                pass
        try:
            if w is not None:
                w.activate()
            else:
                raise OSError("no pygetwindow window; use win32")
        except Exception:  # noqa: BLE001
            if not _win32_focus_hwnd(hwnd):
                return f"聚焦窗口失败（Win32 兜底也未成功）：hwnd={hwnd}"
        return f"已聚焦窗口：{getattr(w, 'title', '') or name}"
    except Exception as e:  # noqa: BLE001
        return f"聚焦窗口失败：{e}"


def _win32_focus_hwnd(hwnd: int) -> bool:
    """ctypes 聚焦窗口（还原最小化 → 前台）。成功返回 True。

    【2026-10-16 多策略增强】Windows 前台锁（ForegroundLockTimeout）会无视后台进程的
    SetForegroundWindow（只闪任务栏）。依次尝试：
      ① AttachThreadInput：把当前线程输入队列挂到前台窗口线程，借它的前台权限调焦点；
      ② SwitchToThisWindow：系统内置切换 API（对部分被锁场景仍有效）；
      ③ TOPMOST 翻转：置顶再取消置顶，把窗口拉到 Z 序最顶（不抢键盘焦点但可见可点）。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        # ① AttachThreadInput 借前台线程权限
        try:
            fg = user32.GetForegroundWindow()
            cur_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            my_tid = user32.GetWindowThreadProcessId(hwnd, None)
            if fg and fg_tid and fg_tid != cur_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(cur_tid, fg_tid, False)
                if user32.GetForegroundWindow() == hwnd:
                    return True
        except Exception:  # noqa: BLE001
            pass
        # ② SwitchToThisWindow
        try:
            user32.SwitchToThisWindow(hwnd, True)
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:  # noqa: BLE001
            pass
        # ③ 直接 SetForegroundWindow（有权限时一步到位）
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # ④ TOPMOST 翻转兜底：不抢焦点但保证窗口在最上层可见
        try:
            # 【2026-10-17】-1/-2 必须包成 c_void_p：ctypes 未声明 argtypes 时按
            # C int 传参，64 位下 HWND_TOPMOST 会变成 0x00000000FFFFFFFF（不是全 1
            # 的伪句柄），SetWindowPos 直接失败 ERROR_INVALID_WINDOW_HANDLE(1400)。
            hwnd_top = ctypes.c_void_p(-1 & 0xFFFFFFFFFFFFFFFF)
            hwnd_notop = ctypes.c_void_p(-2 & 0xFFFFFFFFFFFFFFFF)
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE_F = 0x0002
            SWP_SHOWWINDOW = 0x0040
            user32.SetWindowPos(hwnd, hwnd_top, 0, 0, 0, 0,
                                SWP_NOMOVE_F | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.SetWindowPos(hwnd, hwnd_notop, 0, 0, 0, 0,
                                SWP_NOMOVE_F | SWP_NOSIZE | SWP_SHOWWINDOW)
        except Exception:  # noqa: BLE001
            pass
        # TOPMOST 翻转不改变键盘焦点，此处不判 GetForegroundWindow（会误判），
        # 由调用方结合窗口可见性自行决定后续动作。
        return False
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# 键盘 / 鼠标：pyautogui
# --------------------------------------------------------------------------
def _type_text(text: str) -> str:
    if not text:
        return "未提供要输入的文字"
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "桌面输入未安装：pip install pyautogui"
    try:
        pyautogui.write(text, interval=0.02)
        return f"已输入：{text}"
    except Exception as e:  # noqa: BLE001
        return f"输入失败：{e}"


def _click_at(x: int, y: int) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "桌面点击未安装：pip install pyautogui"
    try:
        pyautogui.click(int(x), int(y))
        return f"已点击坐标 ({x}, {y})"
    except Exception as e:  # noqa: BLE001
        return f"点击失败：{e}"


def _mouse_move(x: int, y: int) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "鼠标移动未安装：pip install pyautogui"
    try:
        pyautogui.moveTo(int(x), int(y), duration=0.2)
        return f"鼠标已移到 ({x}, {y})"
    except Exception as e:  # noqa: BLE001
        return f"移动失败：{e}"


def _mouse_scroll(amount: int) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "鼠标滚动未安装：pip install pyautogui"
    try:
        pyautogui.scroll(int(amount))
        return f"已滚动 {amount}"
    except Exception as e:  # noqa: BLE001
        return f"滚动失败：{e}"


def _key_press(keys: str) -> str:
    """keys 形如 'ctrl+c' / 'alt+f4' / 'enter'。"""
    if not keys:
        return "未提供按键"
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "键盘控制未安装：pip install pyautogui"
    try:
        seq = [k.strip() for k in keys.lower().split("+")]
        if len(seq) == 1:
            pyautogui.press(seq[0])
        else:
            pyautogui.hotkey(*seq)
        return f"已发送按键：{keys}"
    except Exception as e:  # noqa: BLE001
        return f"按键失败：{e}"


# --------------------------------------------------------------------------
# Windows UI 自动化（uiautomation）：按控件"无障碍名称"精确定位并点击/输入，
# 不靠像素坐标猜测 —— 比 pyautogui.click(x,y) 可靠得多，且不需要 VLM。
#
# 依赖：uiautomation（pip install uiautomation），需以管理员权限运行才能枚举控件。
# 对 Electron 应用（汽水音乐 / 网易云音乐）：必须在启动时带 --force-renderer-accessibility
# （见 _open_app / _fuzzy_launch），否则其内部控件不对 UIA 暴露，只能退回 see_screen+click_at。
# 找不到控件时一律优雅降级，并提示走 VLM 兜底，绝不抛异常。
# --------------------------------------------------------------------------
_UI_TIMEOUT_SET = False


def _ui_auto():
    """懒加载 uiautomation，未安装返回 None。

    【2026-10-04 修「只打开没播放」的元凶】uiautomation 的全局搜索超时默认 10s：
    控件不存在时，每一次 `auto.Control(...)` 都要干等满 10s 才返回 None。放歌链路里
    按 5 个关键词找搜索框、再按 4 个关键词找播放键，光"找不到"就要烧掉近 90s，
    整条工具链 75s 预算必然被砍断——用户看到的就是"App 打开了，后面的活没了"。

    这里首次加载即把全局搜索超时压到 2s：控件真存在时是毫秒级命中，压超时不影响成功率；
    需要"等控件慢慢出现"的场景由 _ui_find 自己的轮询循环（timeout 参数）负责，不受影响。
    """
    global _UI_TIMEOUT_SET
    try:
        import uiautomation as auto  # type: ignore
        if not _UI_TIMEOUT_SET:
            for _setter in ("SetGlobalSearchTimeout",):
                _fn = getattr(auto, _setter, None)
                if _fn is None:
                    _mod = getattr(auto, "uiautomation", None)
                    _fn = getattr(_mod, _setter, None) if _mod is not None else None
                if _fn is not None:
                    try:
                        _fn(2.0)
                        break
                    except Exception:  # noqa: BLE001
                        continue
            _UI_TIMEOUT_SET = True
        return auto
    except Exception:  # noqa: BLE001
        return None


def _find_hwnd(app: str) -> int:
    """按标题子串找窗口句柄（pygetwindow / Win32 枚举，毫秒级）。找不到返回 0。

    【2026-10-04】这是全套 UI 自动化的加速关键：拿到 hwnd 后可以直接
    `ControlFromHandle(hwnd)` 拿到 UIA 控件，**完全不遍历桌面树**（实测 3~34ms，
    而按名称遍历桌面要 ~8s），且对 PaneControl 型自绘窗口同样有效。
    【2026-10-04 兜底】pygetwindow 间歇性返回空标题枚举时（05:18 会话实锤），
    退回 ctypes EnumWindows：按标题子串**或进程名**匹配（Electron 空标题窗口
    靠 exe 名如 SodaMusic.exe 也能命中）。
    """
    if not app:
        return 0
    try:
        import pygetwindow as gw  # type: ignore
        wins = gw.getWindowsWithTitle(app)
        if not wins:
            return 0
        w = wins[0]
        hwnd = getattr(w, "_hWnd", None) or getattr(w, "hwnd", 0) or 0
        return int(hwnd)
    except Exception:  # noqa: BLE001
        pass
    # ---- ctypes 兜底：标题子串 / 进程名（含不含 .exe 都试）----
    low = str(app).strip().lower()
    if not low or low in ("default", "none", "auto"):
        return 0
    stem = low.rsplit(".", 1)[0] if low.endswith(".exe") else low
    for h, t, exe in _win32_windows():
        if low and (low in t.lower()
                    or (exe and stem and stem in exe.lower())):
            return h
    return 0


def _window_exists(app: str, unknown: bool = False) -> bool:
    """快速判断某个标题的窗口在不在（pygetwindow，纯 Win32 枚举，毫秒级）。

    UIA 定位不到窗口时要遍历整棵桌面树（实测 ~8s/次），绝对不能拿来"探活"。
    放歌链路先用它判断应用是否已在运行，避免在关闭/未启动的应用上白等。

    unknown：依赖缺失（pygetwindow 没装）时的返回值。
      - play_song 传 False（按"未运行"处理 → 走打开流程，与旧行为一致）；
      - _ui_window 传 True（无法快速否定时，仍允许走一次 UIA 回退，不误伤）。
    """
    if not app:
        return False
    hwnd = _find_hwnd(app)
    if hwnd:
        return True
    # 句柄为 0 时区分「确实没有」和「pygetwindow 不可用」：
    # 后者返回 unknown（无法快速否定，交给上层按场景决定）。
    try:
        import pygetwindow as _gw  # type: ignore  # noqa: F401
        return False
    except Exception:  # noqa: BLE001
        return unknown


def _ui_window(app: str):
    """按标题子串找到顶层窗口控件；app 为空返回根。

    【2026-10-04 修「汽水音乐只打开不播放」】Electron / 自绘窗口（汽水音乐、部分网易云
    版本、抖音桌面端等）的顶层 UIA 控件类型常常是 **PaneControl 而不是 WindowControl**。
    原实现只用 `WindowControl(SubName=app)` 查找 → 这类应用永远返回 None → 后续所有
    ui_* / play_song 一上来就报「没找到标题含『汽水音乐』的窗口」，于是只能打开、无法操作。

    现改为三级查找（快 → 慢）：
      ① 句柄直取：pygetwindow 拿 hwnd → `ControlFromHandle(hwnd)`，毫秒级、不遍历树，
         且对 WindowControl / PaneControl / 任意自绘窗口一视同仁；
      ② 窗口不在（毫秒级探活）就立刻返回 None，**一次都不碰 UIA**；
      ③ 兜底才走 UIA 按名称搜索（只在 pygetwindow 缺失/句柄转换失败时发生）。
    至于窗口内部控件对不对 UIA 暴露，由 _ui_probe 另行判定。
    """
    auto = _ui_auto()
    if auto is None:
        return None
    try:
        if not app:
            return auto.GetRootControl()
        # ① 句柄直取（毫秒级）：拿 hwnd → ControlFromHandle，完全不遍历桌面树。
        #    这条路径对 WindowControl / PaneControl 型窗口一视同仁，比按名称搜索快 3 个数量级。
        hwnd = _find_hwnd(app)
        if hwnd:
            try:
                c = auto.ControlFromHandle(hwnd)
                if c is not None:
                    return c
            except Exception:  # noqa: BLE001
                pass
        # ② 毫秒级探活（pygetwindow）：窗口不在就直接返回，**一次都不碰 UIA**。
        #    【实测】本机遍历一次顶层 UIA 树要 ~8s（某个全屏窗口响应极慢），且这 8s
        #    发生在 UIA 内部、无法从 Python 侧打断。旧实现每遇「找不到」就白烧 8s，
        #    放歌链路连试 9 个关键词 ≈ 72s，正好把 75s 预算烧穿——这就是本次
        #    「只打开没播放」的直接原因。先用 Win32 枚举确认窗口存在，再花这个钱。
        if not _window_exists(app, unknown=True):
            return None
        # ③ 兜底：UIA 按名称搜索（命中 ~1s，找不到会遍历整棵树 ~8s）
        w = auto.WindowControl(searchFromControl=auto.GetRootControl(),
                               SubName=app, foundIndex=1)
        if w is not None and w.Exists(0):
            return w
        #    再按名称匹配顶层任意类型控件（覆盖 PaneControl 型自绘窗口）。
        c = auto.Control(searchFromControl=auto.GetRootControl(),
                         SubName=app, searchDepth=1, foundIndex=1)
        if c is not None and c.Exists(0):
            return c
        return None
    except Exception:  # noqa: BLE001
        return None


def _ui_probe(app: str, max_depth: int = 3) -> tuple[bool, str]:
    """快速判定某个窗口的 UI 自动化是否「真的可用」（内部控件有没有对 UIA 暴露）。

    【2026-10-04】判据不是「控件数量」而是「**带名称的控件数量**」：汽水音乐这类
    Electron 应用实测能枚举出 7+ 个控件，但除窗口自身外**全部 Name 为空**（未开
    --force-renderer-accessibility），按名字找「搜索/播放」注定一无所获。此时继续
    按关键词轮询只会白烧时间、最后还误报成功。先花一次便宜的枚举探明路况：
    - 有名控件 <= 3：判定不可用，上层立即改走 see_screen + click_at 视觉兜底；
    - 有名控件 > 3：可用，正常走 UIA 精确操作。
    """
    auto = _ui_auto()
    if auto is None:
        return False, "UI 自动化未安装：pip install uiautomation（需管理员权限运行）"
    scope = _ui_window(app)
    if app and scope is None:
        return False, f"没找到标题含「{app}」的窗口（应用可能未启动）"
    try:
        total = 0
        named = 0
        deadline = time.time() + 4.0  # 探测也要有预算，不能拖垮工具链

        def _walk(c, depth):
            nonlocal total, named
            if depth <= 0 or total > 120 or time.time() > deadline:
                return
            try:
                for ch in c.GetChildren():
                    total += 1
                    try:
                        if (ch.Name or "").strip():
                            named += 1
                    except Exception:  # noqa: BLE001
                        pass
                    _walk(ch, depth - 1)
            except Exception:  # noqa: BLE001
                return

        _walk(scope, max_depth)
        if named <= 3:
            return False, (f"窗口「{app}」内部控件未对 UI 自动化开放：枚举到 {total} 个控件，"
                           f"其中只有 {named} 个带名称（无法按名称定位搜索框/播放按钮；"
                           f"多为未带 --force-renderer-accessibility 启动的 Electron 应用）")
        return True, f"窗口「{app}」UI 自动化可用（{total} 个控件 / {named} 个带名称）"
    except Exception as e:  # noqa: BLE001
        return False, f"UI 探测异常：{e}"


def _ui_find(name: str = None, contains: str = None, app: str = None,
             timeout: float = 5.0):
    """在窗口（app 指定则限定）内按名称/子串定位控件。

    返回 (control, info_str)；失败时 (None, 错误说明)。
    """
    auto = _ui_auto()
    if auto is None:
        return None, "UI 自动化未安装：pip install uiautomation（需管理员权限运行）"
    if not (name or contains):
        return None, "未提供控件名称 name 或 contains"
    scope = _ui_window(app)
    if app and scope is None:
        return None, f"没找到标题含「{app}」的窗口（应用可能未启动）"
    search = {"searchFromControl": scope, "searchDepth": 12, "foundIndex": 1}
    if name:
        search["Name"] = name
    else:
        search["SubName"] = contains
    try:
        deadline = time.time() + max(0.0, min(float(timeout), 15.0))
        found = None
        while time.time() < deadline:
            found = auto.Control(**search)
            if found is not None and found.Exists(0):
                break
            time.sleep(0.3)
        if not (found is not None and found.Exists(0)):
            return None, (f"在「{app or '桌面'}」中未找到控件「{name or contains}」"
                          f"（应用可能未启动，或该控件未开启无障碍；可改用 see_screen+click_at 兜底）")
        r = found.BoundingRectangle
        info = f"{found.ControlTypeName}「{found.Name}」@({r.left},{r.top},{r.right},{r.bottom})"
        return found, info
    except Exception as e:  # noqa: BLE001
        return None, f"UI 定位异常：{e}"


def _ui_click(name: str = None, contains: str = None, app: str = None,
              timeout: float = 5.0) -> str:
    """按控件名称/子串精确定位并点击（确定性，不靠坐标）。"""
    found, info = _ui_find(name=name, contains=contains, app=app, timeout=timeout)
    if found is None:
        return info  # 错误说明
    try:
        found.Click()
        return f"已通过 UI 自动化点击：{info}"
    except Exception as e:  # noqa: BLE001
        return f"UI 点击失败（{info}）：{e}"


def _ui_type(name: str = None, contains: str = None, app: str = None,
             text: str = "", timeout: float = 5.0, enter: bool = False) -> str:
    """聚焦指定控件并输入文字（优先 SetValue，退化为 SendKeys）。"""
    if not text:
        return "未提供要输入的文字"
    found, info = _ui_find(name=name, contains=contains, app=app, timeout=timeout)
    if found is None:
        return info
    try:
        found.SetFocus()
        try:
            found.SendKeys("{Ctrl}a")  # 先全选清空
        except Exception:  # noqa: BLE001
            pass
        if hasattr(found, "SetValue"):
            found.SetValue(text)
        else:
            found.SendKeys(text)
        if enter:
            found.SendKeys("{Enter}")
        return f"已在控件输入：{info} 文本={text}"
    except Exception as e:  # noqa: BLE001
        try:
            found.SendKeys(text)
            if enter:
                found.SendKeys("{Enter}")
            return f"已用键盘输入（控件不支持 SetValue，已退化 SendKeys）：{text}"
        except Exception as e2:  # noqa: BLE001
            return f"UI 输入失败（{info}）：{e} / {e2}"


def _ui_paste(name: str = None, contains: str = None, app: str = None,
              text: str = "", timeout: float = 5.0, enter: bool = False) -> str:
    """聚焦控件后用「剪贴板粘贴」输入文字（比 SetValue/SendKeys 更可靠）。

    【2026-10-04】Electron / 自绘输入框（汽水音乐搜索框等）对 UIA 的 SetValue 常常返回
    成功但内容没进去，SendKeys 直输中文又会被输入法/编码搅乱。最稳的路径是：
    把文字写进剪贴板 → 控件里 Ctrl+A 全选 → Ctrl+V 粘贴 → 可选回车。
    """
    if not text:
        return "未提供要输入的文字"
    found, info = _ui_find(name=name, contains=contains, app=app, timeout=timeout)
    if found is None:
        return info
    try:
        found.SetFocus()
        time.sleep(0.15)
        _write_clipboard(text)
        try:
            found.SendKeys("{Ctrl}a")
            time.sleep(0.1)
            found.SendKeys("{Ctrl}v")
        except Exception:  # noqa: BLE001
            found.SendKeys(text)
        if enter:
            time.sleep(0.25)
            found.SendKeys("{Enter}")
        return f"已在控件粘贴输入：{info} 文本={text}"
    except Exception as e:  # noqa: BLE001
        return f"UI 粘贴输入失败（{info}）：{e}"


def _ui_inspect(app: str = None, max_depth: int = 3, max_items: int = 80) -> str:
    """枚举窗口内的控件树（名称 + 矩形），用于确认有哪些可点击/可输入的控件。"""
    auto = _ui_auto()
    if auto is None:
        return "UI 自动化未安装：pip install uiautomation（需管理员权限运行）"
    scope = _ui_window(app)
    if app and scope is None:
        return f"没找到标题含「{app}」的窗口（应用可能未启动）"
    try:
        lines = []

        def _walk(c, depth):
            if depth <= 0 or len(lines) >= max_items:
                return
            try:
                r = c.BoundingRectangle
                lines.append("  " * (max_depth - depth)
                             + f"- [{c.ControlTypeName}] {c.Name} "
                             + f"rect=({r.left},{r.top},{r.right},{r.bottom})")
            except Exception:  # noqa: BLE001
                pass
            try:
                for ch in c.GetChildren():
                    _walk(ch, depth - 1)
            except Exception:  # noqa: BLE001
                pass

        _walk(scope, max_depth)
        if not lines:
            return (f"窗口「{app or ''}」内未发现可枚举控件"
                    f"（可能未开启无障碍，或需管理员权限；可改用 see_screen+click_at）")
        return f"UI 控件树（前 {len(lines)} 个）：\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"UI 枚举异常：{e}"


async def _play_song(app: str, song: str, ctx: dict | None = None) -> str:
    """在指定音乐 App 里播放某首歌（汽水音乐/网易云等）。

    【2026-10-04 重写·修「只打开不播放」】旧实现有三个致命毛病，合起来正好造成本次故障：
      1. 不做「UI 自动化可用性」探测：汽水音乐这类 Electron 应用窗口能开、控件一棵都
         枚举不出来，代码仍按 5 个关键词各轮询 4s 找搜索框、再按 4 个关键词找播放键，
         白烧 ~40s 后走到兜底；
      2. 兜底「无条件发全局媒体播放键」伪装成成功——刚打开还没选歌的 App 收到
         play_pause 什么也不会播，可返回文本看着像做完了，模型据此汇报「已播放」；
      3. 全程同步 time.sleep 阻塞事件循环，把整条工具链的 75s 预算吃掉一大半。

    现改为：**先探测 → 不可用就秒失败并明确改走视觉兜底 → 可用才搜/播 → 最后客观验证**
    （播放中界面一般会出现「暂停」控件，否则一律判为「未确认」，绝不让模型误报成功）。
    """
    if not (app and song):
        return "需要提供 app（如 汽水音乐）和 song（歌名）"
    steps: list[str] = []
    # ① 打开（已在运行就不重复开，顺手聚焦到前台）
    #    用 _window_exists（pygetwindow，毫秒级）判断，不用 UIA——UIA 在窗口不存在时
    #    要走满搜索超时，白白吃掉工具链预算。
    if _window_exists(app):
        steps.append("① 应用已在运行：" + app)
        try:
            steps.append("   聚焦：" + _focus_window(app))
        except Exception:  # noqa: BLE001
            pass
    else:
        # 带 a11y 参数启动：Electron 音乐应用带上 --force-renderer-accessibility 后，
        # 内部搜索框/播放键才会对 UIA 暴露——否则后面一步也做不下去（只能打开、放不了歌）。
        steps.append("① 打开：" + _open_app(app, a11y=True))
        # 轮询等窗口出现（异步，不冻结事件循环；最多 8s）
        waited = 0.0
        while waited < 8.0 and not _window_exists(app):
            await asyncio.sleep(0.5)
            waited += 0.5
        steps.append(f"   等待窗口出现 {waited:.1f}s：" + ("已就绪" if _window_exists(app) else "仍未出现"))
    # ② 探测该窗口的 UI 自动化是否真的可用
    ok, probe = _ui_probe(app)
    steps.append("② " + probe)
    if not ok:
        # 【2026-10-04 补·键盘兜底】控件不暴露时，先试键盘粘贴歌名+回车触发应用级搜索
        # （汽水音乐等 Electron 应用支持），成败以「暂停」控件客观验证。
        kb = ""
        try:
            kb = await _play_song_keyboard(app, song)
        except Exception:  # noqa: BLE001
            kb = ""
        if kb:
            steps.append("③ 键盘兜底：" + kb)
            _p, _pinfo = _ui_find(contains="暂停", app=app, timeout=2.5)
            if _p is not None:
                steps.append("④ 已确认正在播放（界面出现『暂停』控件：" + _pinfo + "）")
                return "\n".join(steps)
            steps.append("④ 键盘已尝试，但界面未出现『暂停』控件，继续视觉兜底")
        else:
            steps.append("③ 键盘兜底不可用（缺依赖或聚焦失败），改走视觉路线")
        # 【2026-10-16 新增·视觉兜底】业界混合 grounding 的标准 fallback：UIA 树空
        # 且键盘不可靠时，用 VLM 看屏定位坐标直接点击（鼠标事件不受前台锁限制）。
        vis = ""
        try:
            vis = await _play_song_visual(app, song, ctx)
        except Exception as e:  # noqa: BLE001
            vis = f"视觉兜底异常：{e}"
        if vis:
            steps.append("⑤ 视觉兜底：" + vis)
            return "\n".join(steps)
        # 视觉也不可用（无 VLM 配置等）：给窗口矩形 + 诚实结论，不谎报成功
        rect_txt = ""
        try:
            _w = _ui_window(app)
            if _w is not None:
                r = _w.BoundingRectangle
                rect_txt = (f"（该应用窗口区域：左{r.left} 上{r.top} 右{r.right} 下{r.bottom}，"
                            f"看屏时可据此判断搜索框大致位置）")
        except Exception:  # noqa: BLE001
            pass
        steps.append(
            "⑥ 结论：本应用未对 UI 自动化开放内部控件，键盘与视觉兜底均不可用，无法自动播放。"
            + rect_txt
            + f"\n   你可以在搜索框手动输入「{song}」回车播放；或在设置里为汽水音乐开启"
              "无障碍（部分版本带上 --force-renderer-accessibility 后控件会暴露）。")
        return "\n".join(steps)
    # ③ 找搜索框（关键词收敛，避免无效轮询烧时间）
    box_kw = None
    for kw in ("搜索", "Search"):
        _c, info = _ui_find(contains=kw, app=app, timeout=2.0)
        if _c is not None:
            box_kw = kw
            steps.append("③ 找到搜索框：" + info)
            break
    if box_kw is None:
        steps.append("③ 未找到搜索框控件。建议 ui_inspect 看一眼真实控件名，"
                     "或改走 see_screen + click_at 视觉兜底。")
        return "\n".join(steps)
    # ④ 输入歌名（剪贴板粘贴，中文与 Electron 输入框都稳）
    steps.append("④ " + _ui_paste(contains=box_kw, app=app, text=song, timeout=2.0, enter=True))
    await asyncio.sleep(2.0)  # 等搜索结果
    # ⑤ 点播放
    for kw in ("播放", "Play", "试听"):
        _c, _info = _ui_find(contains=kw, app=app, timeout=2.0)
        if _c is not None:
            steps.append("⑤ " + _ui_click(contains=kw, app=app, timeout=2.0))
            break
    else:
        steps.append("⑤ 未找到播放按钮（结果可能还没加载，或按钮名不是『播放』）。")
    await asyncio.sleep(1.5)
    # ⑥ 客观验证：播放中界面一般会出现「暂停」控件；否则一律判未确认
    _p, _pinfo = _ui_find(contains="暂停", app=app, timeout=2.5)
    if _p is not None:
        steps.append("⑥ 已确认正在播放（界面出现『暂停』控件：" + _pinfo + "）")
    else:
        steps.append("⑥ 未确认播放：界面没有出现『暂停』控件。"
                     "请用 see_screen 复核是不是真的在播；未确认前不要汇报『已播放』。")
    return "\n".join(steps)


async def _play_song_keyboard(app: str, song: str) -> str:
    """键盘兜底：UI 自动化拿不到控件时，聚焦窗口后用「剪贴板粘贴 + 回车」触发应用级搜索。

    【2026-10-04 补·只打开没播放】汽水音乐等 Electron 应用内部控件不暴露给 UIA，
    但窗口聚焦后键入/粘贴文字多数会路由到搜索框（应用级全局输入）。中文用剪贴板粘贴
    （Ctrl+V）比 SendKeys 直输可靠得多（后者绕不开输入法）。成败以「暂停」控件客观验证为准。
    环境缺依赖时返回空串（调用方据此走诚实结论，不谎报成功）。
    """
    try:
        import uiautomation as auto  # type: ignore
        import pyperclip  # type: ignore
    except Exception:  # noqa: BLE001
        return ""
    try:
        fr = _focus_window(app)
        # 【2026-10-16 修·副作用风险】聚焦失败绝不能继续发键——Ctrl+A/V/Enter 会打到
        # 当前真正拥有焦点的窗口（可能是用户的聊天框、文档），造成真实破坏。
        if "失败" in (fr or "") or "没找到" in (fr or ""):
            return f"键盘兜底中止：窗口聚焦失败（{fr}），不向未知焦点窗口发送按键"
    except Exception as e:  # noqa: BLE001
        return f"键盘兜底中止：聚焦异常（{e}）"
    await asyncio.sleep(0.6)
    try:
        pyperclip.copy(song)
        # 先 Ctrl+A 全选可能残留的文字，再粘贴、回车
        auto.SendKeys("{Ctrl}a")
        await asyncio.sleep(0.15)
        auto.SendKeys("{Ctrl}v")
        await asyncio.sleep(0.3)
        auto.SendKeys("{Enter}")
    except Exception as e:  # noqa: BLE001
        return f"键盘输入失败：{e}"
    await asyncio.sleep(2.5)  # 等搜索结果 + 自动播放
    return (f"已用键盘向「{app}」粘贴「{song}」并回车（应用级搜索，"
            f"是否真在播放以界面『暂停』控件为准）")


async def _play_song_visual(app: str, song: str, ctx: dict | None = None) -> str:
    """视觉兜底（2026-10-16 新增，对标业界混合 grounding 的 fallback 流）。

    UIA 树为空（Electron 默认不暴露控件）且键盘兜底被聚焦失败中止时：
    VLM 看屏定位搜索框坐标 → pyautogui 点击（鼠标事件不受 SetForegroundWindow
    前台锁限制，点击本身会把窗口带到前台）→ 剪贴板粘贴歌名回车 → VLM 定位播放键
    → VLM 客观复核「是否真的在播放」。三眼结构：定位 → 执行 → 验证。
    全程不谎报：任何一步拿不到确定性证据就如实返回。
    """
    try:
        import pyautogui  # type: ignore
        import pyperclip  # type: ignore
    except Exception:  # noqa: BLE001
        return ""
    import re as _re
    c = ctx or {}
    # 第①眼：定位搜索框
    q1 = (f"屏幕上是否能看到「{app}」的窗口？如果能看到，请找到它的搜索框，"
          f"输出一行格式严格为：BOX x=数字,y=数字（搜索框中心像素坐标，"
          f"以截图实际分辨率为准）。如果看不到该窗口，输出 NOTFOUND。")
    ans1 = await _see_screen(q1, c)
    m = _re.search(r"BOX\s*x\s*=\s*(\d+)\s*[,，]\s*y\s*=\s*(\d+)", ans1 or "")
    if not m:
        return f"VLM 未定位到搜索框（回答摘要：{(ans1 or '')[:120]}）"
    bx, by = int(m.group(1)), int(m.group(2))
    pyautogui.click(bx, by)
    await asyncio.sleep(0.8)
    # 输入歌名：剪贴板粘贴 + 回车（绕开输入法）
    pyperclip.copy(song)
    pyautogui.hotkey("ctrl", "a")
    await asyncio.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    await asyncio.sleep(0.4)
    pyautogui.press("enter")
    await asyncio.sleep(3.0)  # 等搜索结果加载
    # 第②眼：定位第一首歌的播放按钮
    q2 = ("屏幕上现在应显示搜索结果列表。找到第一首歌的播放按钮（▶ 图标或『播放』文字），"
          "输出一行格式严格为：PLAY x=数字,y=数字；找不到输出 NOTFOUND。")
    ans2 = await _see_screen(q2, c)
    m2 = _re.search(r"PLAY\s*x\s*=\s*(\d+)\s*[,，]\s*y\s*=\s*(\d+)", ans2 or "")
    clicked_play = ""
    if m2:
        pyautogui.click(int(m2.group(1)), int(m2.group(2)))
        clicked_play = f"→点播放({m2.group(1)},{m2.group(2)})"
        await asyncio.sleep(2.0)
    else:
        # 找不到独立播放键 → 双击第一首结果条目（多数音乐应用双击=播放）
        q2b = ("屏幕上显示的是搜索结果列表。输出第一首歌条目（整行）的中心坐标，"
               "格式严格为：ROW x=数字,y=数字；列表为空输出 NOTFOUND。")
        ans2b = await _see_screen(q2b, c)
        m2b = _re.search(r"ROW\s*x\s*=\s*(\d+)\s*[,，]\s*y\s*=\s*(\d+)", ans2b or "")
        if m2b:
            pyautogui.doubleClick(int(m2b.group(1)), int(m2b.group(2)))
            clicked_play = f"→双击第一首结果({m2b.group(1)},{m2b.group(2)})"
            await asyncio.sleep(2.5)
    # 第③眼：客观验证
    q3 = ("屏幕上是否正在播放音乐？判断依据：出现『暂停』按钮、底部出现播放进度条，"
          "或正在播放的歌曲名。若在播放，输出 PLAYING 加歌名；否则输出 NOTPLAYING。")
    ans3 = await _see_screen(q3, c)
    t3 = (ans3 or "")
    # 注意 NOTPLAYING 含 PLAYING 子串，必须先排除否定答案（曾因此误报"确认播放"）
    is_playing = bool(_re.search(r"(?<!NOT)PLAYING", t3))
    if is_playing:
        return (f"VLM 确认正在播放：点搜索框({bx},{by})→粘贴「{song}」回车"
                + clicked_play + "；复核=" + t3[:80])
    return (f"视觉流程已执行：点搜索框({bx},{by})→粘贴「{song}」回车" + clicked_play
            + "；但 VLM 复核未确认播放：" + t3[:100])


# --------------------------------------------------------------------------
# 剪贴板：pyperclip
# --------------------------------------------------------------------------
def _read_clipboard() -> str:
    try:
        import pyperclip  # type: ignore
    except Exception:  # noqa: BLE001
        return "剪贴板读取未安装：pip install pyperclip"
    try:
        txt = pyperclip.paste()
        return f"剪贴板内容：\n{txt}" if txt else "剪贴板为空"
    except Exception as e:  # noqa: BLE001
        return f"读取剪贴板失败：{e}"


def _write_clipboard(text: str) -> str:
    if not text:
        return "未提供要写入的文字"
    try:
        import pyperclip  # type: ignore
    except Exception:  # noqa: BLE001
        return "剪贴板写入未安装：pip install pyperclip"
    try:
        pyperclip.copy(text)
        return f"已写入剪贴板：{text}"
    except Exception as e:  # noqa: BLE001
        return f"写入剪贴板失败：{e}"


# --------------------------------------------------------------------------
# 文件读写（锁定在 sandbox 内）
# --------------------------------------------------------------------------
def _read_file(name: str) -> str:
    if not name:
        return "未提供文件名"
    try:
        p = _safe_path(name)
    except WorkspaceEscape as e:
        return str(e)
    try:
        if not os.path.exists(p):
            return f"文件不存在：{os.path.basename(p)}（sandbox：{_sandbox_dir()}）"
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f"文件 {os.path.basename(p)} 内容：\n" + f.read(4000)
    except Exception as e:  # noqa: BLE001
        return f"读取文件失败：{e}"


def _write_file(name: str, content: str) -> str:
    if not name:
        return "未提供文件名"
    try:
        p = _safe_path(name)
    except WorkspaceEscape as e:
        return str(e)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content or "")
        return f"已写入文件：{os.path.basename(p)}"
    except Exception as e:  # noqa: BLE001
        return f"写入文件失败：{e}"


def _append_file(name: str, content: str) -> str:
    if not name:
        return "未提供文件名"
    try:
        p = _safe_path(name)
    except WorkspaceEscape as e:
        return str(e)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(content or "")
        return f"已追加到文件：{os.path.basename(p)}"
    except Exception as e:  # noqa: BLE001
        return f"追加失败：{e}"


def _file_search(pattern: str) -> str:
    if not pattern:
        return "未提供搜索关键字"
    try:
        root = _sandbox_dir()
        hits = []
        for dirpath, _, files in os.walk(root):
            for fn in files:
                if pattern.lower() in fn.lower():
                    hits.append(os.path.relpath(os.path.join(dirpath, fn), root))
                if len(hits) >= 30:
                    break
            if len(hits) >= 30:
                break
        return "sandbox 内匹配文件：\n" + "\n".join(f"- {h}" for h in hits) if hits else "未找到匹配文件"
    except Exception as e:  # noqa: BLE001
        return f"搜索失败：{e}"


# --------------------------------------------------------------------------
# 截图 + VLM 视觉理解：把"看屏幕"接进 agent（结合本仓已有的视觉能力）
# --------------------------------------------------------------------------
async def _see_screen(question: str, ctx: dict) -> str:
    """截图后用多模态 VLM 描述/回答。把软件已有的视觉能力接到操作链里。"""
    try:
        import pyautogui  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        return "看屏未安装：pip install pyautogui Pillow"
    try:
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:  # noqa: BLE001
        return f"截图失败：{e}"
    # ① 优先「能力槽」统一维护的视觉端点（vision_models.active）—— 用户在设置里换视觉模型，
    #    Agent 看屏跟着变，不用再去改一个没有 UI 的 agent.vision_model；
    # ② 读不到才回落到旧的 agent/cascade 端点 + agent.vision_model。
    _ub = (ctx.get("vlm_base_url") or "").rstrip("/")
    _um = (ctx.get("vlm_model") or "").strip()
    if _ub and _um:
        base, key, model = _ub, (ctx.get("vlm_api_key") or ""), _um
    else:
        base = (ctx.get("base_url") or "").rstrip("/")
        key = ctx.get("api_key") or ""
        model = (ctx.get("vision_model") or "").strip()
    if not (base and model):
        return "看屏需要先配好视觉模型（能力槽「对话模型」里选一个带视觉的，或补 agent.vision_model）"
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return "看屏需要 httpx（pip install httpx）"
    prompt = (question or "请描述这张截图，回答：现在屏幕上显示的是什么、有没有可点击的关键按钮/控件。").strip()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是达妮娅的视觉助手，用中文简洁描述截图并回答用户问题，"
                                           "重点指出可点击的按钮/控件的位置与文字，帮助她操作电脑。"},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            r = await cli.post(f"{base}/chat/completions", json=body, headers=headers)
            if r.status_code != 200:
                return f"看屏 VLM 调用失败（HTTP {r.status_code}）：{r.text[:200]}"
            data = r.json()
            try:
                from .quota_meter import record as _qm_record
                _qm_record(model, ((data.get("usage") or {}).get("total_tokens") or 0), base)
            except Exception:  # noqa: BLE001
                pass
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or "（VLM 无返回）"
    except Exception as e:  # noqa: BLE001
        return f"看屏 VLM 执行异常：{e}"


def _screenshot_save(name: str) -> str:
    if not name:
        name = f"screen_{int(time.time())}.png"
    try:
        import pyautogui  # type: ignore
    except Exception:  # noqa: BLE001
        return "截图未安装：pip install pyautogui Pillow"
    try:
        p = _safe_path(name)
    except WorkspaceEscape as e:
        return str(e)
    try:
        pyautogui.screenshot(p)
        return f"截图已保存：{p}"
    except Exception as e:  # noqa: BLE001
        return f"截图失败：{e}"


# --------------------------------------------------------------------------
# 系统信息 / 等待
# --------------------------------------------------------------------------
def _system_info() -> str:
    try:
        import datetime
        info = {
            "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "系统": f"{platform.system()} {platform.release()}",
            "机器": platform.node(),
            "用户": platform.username() if hasattr(platform, "username") else "n/a",
            "Python": platform.python_version(),
        }
        try:
            import pyautogui  # type: ignore
            info["屏幕分辨率"] = f"{pyautogui.size().width}x{pyautogui.size().height}"
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(f"{k}：{v}" for k, v in info.items())
    except Exception as e:  # noqa: BLE001
        return f"读取系统信息失败：{e}"


def _wait(seconds: float) -> str:
    try:
        s = max(0.0, min(60.0, float(seconds)))
    except Exception:  # noqa: BLE001
        return "等待时长无效"
    time.sleep(s)
    return f"已等待 {s:.1f} 秒"


# --------------------------------------------------------------------------
# 浏览器 / 搜索代理：browser-use + duckduckgo_search（开源）
# --------------------------------------------------------------------------
# 这些重依赖（browser-use 把 openai 锁在 2.26.0，与主程序冲突）装进独立 venv：
#   agent_venv   （见 agent_runner.py）
# 主程序（系统 Python）不 import 它们，而是把任务交给 venv 的 python 跑 agent_runner.py，
# 经环境变量传入 LLM 配置，结果用 `BUW_RESULT_JSON ` 前缀的一行回传，避免日志噪声污染解析。
_AGENT_RUNNER_MARK = "BUW_RESULT_JSON "


def _agent_repo_root() -> str:
    """向上找到含 config.yaml 的目录（即工程根）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.exists(os.path.join(d, "config.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


def _agent_venv_paths() -> "tuple[str, str] | None":
    """返回 (venv_python, runner_script)；若隔离环境未就绪返回 None。"""
    root = _agent_repo_root()
    venv_py = os.path.join(root, "agent_venv", "Scripts", "python.exe")
    runner = os.path.join(root, "agent_runner.py")
    if os.path.exists(venv_py) and os.path.exists(runner):
        return venv_py, runner
    return None


# venv 解释器真实可执行性探测缓存。
# 根因（2026-10-16 实测）：agent_venv\Scripts\python.exe 存在但根本跑不起来
# （--version 都返回 rc=1 且无任何输出），而旧判据只检查 os.path.exists →
# 判为「就绪」把 browse_web 暴露给模型，模型每次都要烧掉 180s 超时才拿到失败，
# 白白消耗 max_steps 并带偏整条任务（实测抖音搜索 3 个模型有 2 个死在这里）。
_VENV_PY_OK_CACHE: "dict[str, bool]" = {}


def _agent_venv_ready() -> bool:
    """真正执行一次 venv 解释器，确认它不是个空壳/坏环境。"""
    paths = _agent_venv_paths()
    if not paths:
        return False
    venv_py = paths[0]
    if venv_py in _VENV_PY_OK_CACHE:
        return _VENV_PY_OK_CACHE[venv_py]
    ok = False
    try:
        proc = subprocess.run(
            [venv_py, "-c", "import sys; sys.stdout.write('ok')"],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
        ok = (proc.returncode == 0) and ("ok" in (proc.stdout or ""))
    except Exception:  # noqa: BLE001
        ok = False
    _VENV_PY_OK_CACHE[venv_py] = ok
    if not ok:
        logger.warning("Agent 隔离环境不可用（%s 无法执行），依赖它的工具将不对模型暴露", venv_py)
    return ok


# Playwright 浏览器内核可用性缓存。
# 根因（2026-10-16 实测）：venv 里 playwright 模块装了，但浏览器内核没下载
# （%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win64\chrome.exe 不存在）。
# 于是 browse_web 每次调用都卡到超时（实测 180s 一次，模型 overwhelmingly 把它当首选工具，
# 一上来就白烧 3 分钟 + 吃掉整个 step 预算 → 抖音/搜索类任务必失败）。
# 这里真正问 playwright 要 executable_path 并校验文件存在。
_PW_BROWSER_OK_CACHE: "dict[str, bool]" = {}


def _playwright_browser_ready() -> bool:
    paths = _agent_venv_paths()
    if not paths:
        return False
    venv_py = paths[0]
    if not _agent_venv_ready():
        return False
    if venv_py in _PW_BROWSER_OK_CACHE:
        return _PW_BROWSER_OK_CACHE[venv_py]
    ok = False
    probe_code = (
        "from playwright.sync_api import sync_playwright\n"
        "import os\n"
        "with sync_playwright() as p:\n"
        "    e = p.chromium.executable_path\n"
        "    print('PW_YES' if os.path.exists(e) else 'PW_NO')\n"
    )
    try:
        proc = subprocess.run([venv_py, "-c", probe_code],
                              capture_output=True, text=True, errors="replace", timeout=40)
        ok = "PW_YES" in (proc.stdout or "")
    except Exception:  # noqa: BLE001
        ok = False
    _PW_BROWSER_OK_CACHE[venv_py] = ok
    if not ok:
        logger.warning("Playwright 浏览器内核缺失，browse_web 将不对模型暴露（避免每次调用都空等到超时）")
    return ok


def _run_agent_subprocess(venv_py: str, runner: str,
                          sub_args: list, env_extras: dict,
                          timeout: float) -> str:
    """在 venv 里跑 agent_runner.py 子命令，提取 BUW_RESULT_JSON 结果行。"""
    env = dict(os.environ)
    env.update(env_extras or {})
    try:
        proc = subprocess.run(
            [venv_py, runner, *sub_args],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
            cwd=os.path.dirname(runner),
        )
    except subprocess.TimeoutExpired:
        return f"工具执行超时（>{int(timeout)} 秒）：浏览器任务可能卡住或模型未在限定步数内完成。"
    except Exception as e:  # noqa: BLE001
        return f"启动 agent runner 失败：{e}"

    out = proc.stdout or ""
    result_line = ""
    for line in out.splitlines():
        if line.startswith(_AGENT_RUNNER_MARK):
            result_line = line[len(_AGENT_RUNNER_MARK):]
    if not result_line:
        err_tail = (proc.stderr or "")[-1400:]
        return (f"agent runner 未返回结构化结果（rc={proc.returncode}）。\n"
                f"若是 'Executable doesn't exist' / Playwright 报错，请在该 venv 内执行：\n"
                f"  agent_venv\\Scripts\\python.exe -m playwright install chromium\n"
                f"--- 子进程 stderr 尾部 ---\n{err_tail}")
    try:
        payload = json.loads(result_line)
    except Exception:  # noqa: BLE001
        return f"agent runner 返回无法解析：{result_line[:600]}"
    if payload.get("ok"):
        text = str(payload.get("result", ""))
        log = payload.get("log")
        if log and isinstance(log, dict):
            actions = log.get("actions") or []
            urls = log.get("urls") or []
            summ = []
            if actions:
                summ.append("浏览器动作序列：" + " → ".join(str(a) for a in actions[:12]))
            if urls:
                summ.append("访问过：" + "、".join(str(u) for u in urls[:6]))
            if summ:
                return (text + "\n[浏览器执行日志] " + "；".join(summ))[:1800]
        return text[:1500] if text else "浏览器任务完成（无文本结果）"
    return f"工具执行失败：{payload.get('error', '未知错误')}"


async def _browse_web(task: str, ctx: dict) -> str:
    if not task:
        return "未提供浏览器任务"
    url_match = re.search(r"https?://\S+", task)
    paths = _agent_venv_paths()
    if not paths:
        if url_match:
            webbrowser.open(url_match.group(0))
            return (f"未检测到隔离 venv（agent_venv），已退而在浏览器打开 {url_match.group(0)}。"
                    f"如需让 AI 自动操作网页/刷视频，请确保工程根下存在 agent_venv 与 agent_runner.py。")
        return ("浏览器代理未就绪：缺少隔离 venv（agent_venv）。"
                "请运行环境初始化脚本创建 venv 并安装 browser-use / playwright。")
    venv_py, runner = paths
    base = (ctx.get("base_url") or "").rstrip("/")
    key = ctx.get("api_key") or ""
    model = ctx.get("model") or ""
    if not (base and key and model):
        return "浏览器代理需要配置 LLM（agent.llm 或 cascade 的 base_url+api_key+model）"
    env_extras = {
        "BUW_MODEL": model,
        "BUW_BASE_URL": base,
        "BUW_API_KEY": key,
        "BUW_HEADLESS": os.environ.get("BUW_HEADLESS", "0"),
    }
    sub_args = ["browse", "--task", task]
    try:
        return await asyncio.to_thread(
            # 与 TOOL_TIMEOUTS["browse_web"] 对齐（此前两处硬编码重复，改一处漏一处）
            _run_agent_subprocess, venv_py, runner, sub_args, env_extras,
            TOOL_TIMEOUTS.get("browse_web", 180.0),
        )
    except Exception as e:  # noqa: BLE001
        return f"浏览器代理调度失败：{e}"


# --------------------------------------------------------------------------
# 联网搜索：duckduckgo_search（开源，无需 key）
# --------------------------------------------------------------------------
def _search_web(query: str, ctx: dict | None = None) -> str:
    if not query:
        return "未提供搜索词"
    paths = _agent_venv_paths()
    if not paths:
        return "联网搜索未就绪：缺少隔离 venv（agent_venv）。请运行环境初始化脚本。"
    venv_py, runner = paths
    provider = (ctx or {}).get("search_provider") or "duckduckgo"
    fallback = (ctx or {}).get("search_fallback") or []
    env_extras = {
        "BUW_SEARCH_PROVIDER": str(provider),
        "BUW_SEARCH_FALLBACK": ",".join(str(x) for x in fallback),
    }
    sub_args = ["search", "--query", query, "--max-results", "5"]
    try:
        return _run_agent_subprocess(venv_py, runner, sub_args, env_extras, 30.0)
    except Exception as e:  # noqa: BLE001
        return f"搜索调度失败：{e}"


# --------------------------------------------------------------------------
# 抓取网页正文：让"搜索 → 读页"闭环（只读，无副作用，聊天里也安全）
# --------------------------------------------------------------------------
def _fetch_web(url: str) -> str:
    if not url:
        return "未提供网址"
    try:
        import re as _re
        import urllib.parse as _up  # noqa: F401
        import urllib.request as _ureq
        req = _ureq.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with _ureq.urlopen(req, timeout=20) as r:
            ctype = r.headers.get("Content-Type", "") or ""
            raw = r.read(1_500_000)
        enc = "utf-8"
        if "charset=" in ctype:
            enc = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        text = raw.decode(enc, "replace")
        text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=_re.S | _re.I)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"&nbsp;", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        if not text:
            return "（页面未提取到可读正文）"
        return "【网页正文】" + text[:3500]
    except Exception as e:  # noqa: BLE001
        return f"抓取网页失败：{e}"


# --------------------------------------------------------------------------
# 命令行：白名单闸
# --------------------------------------------------------------------------
# 【2026-10-17 安全修复】旧实现有两个缺口，叠加后可用一行分号绕过白名单：
#   1) `if allow and head not in ...` —— 白名单漏配时 allow 为空，条件短路 ⇒ 全放行；
#   2) 只校验第一个词，整串交给 shell=True ⇒ `git status; del /s /q ...` 首词是 git，照样执行。
# 现在改为 fail-closed：白名单为空即拒绝；含有 shell 元字符即拒绝；argv 解析后校验
# 可执行文件名，并以 shell=False 执行（Windows 内建命令单独走 cmd /d /c，且已禁元字符）。
_SHELL_META_RE = re.compile(r"[;&|`$\n\r<>%^!()]")

# cmd.exe 内建命令（没有对应 exe，shell=False 直接执行会 FileNotFoundError）
# 注意：start 也在此列——它本身能启动任意程序，是否可用完全取决于用户是否把它写进
# allow_commands（现有实配就包含 start/calc/notepad/explorer，属用户显式授权）。
_CMD_BUILTINS = {
    "assoc", "cd", "chdir", "cls", "copy", "date", "del", "dir", "echo", "erase",
    "ftype", "md", "mkdir", "more", "move", "path", "pause", "rd", "ren", "rename",
    "rmdir", "set", "sort", "start", "time", "title", "type", "ver", "vol",
}


def _norm_exe(name: str) -> str:
    """白名单归一：忽略大小写与 .exe 后缀（'Git' / 'git.exe' / 'git' 视为同一个）。"""
    n = str(name).strip().lower()
    return n[:-4] if n.endswith(".exe") else n


def _split_argv(command: str) -> list[str]:
    """按 Windows 习惯切分（保留反斜杠），并去掉 token 两端成对引号。"""
    import shlex

    argv = shlex.split(command, posix=False)
    out: list[str] = []
    for tok in argv:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _run_command(command: str, ctx: dict) -> str:
    if not command:
        return "未提供命令"
    if not ctx.get("allow_cmd"):
        return "run_command 未启用（config.yaml agent.allow_command=false）。"
    allow = [_norm_exe(a) for a in (ctx.get("allow_cmds") or []) if str(a).strip()]
    # fail-closed：白名单为空不许"默认全开"
    if not allow:
        return "命令白名单为空（agent.allow_commands 未配置），已拒绝执行。"
    # 只接受单条命令：串联 / 管道 / 重定向 / 变量展开 / 转义 一律拒绝
    if _SHELL_META_RE.search(command):
        return "命令包含串联、管道、重定向或变量展开字符，已拒绝执行。"
    try:
        argv = _split_argv(command)
    except Exception as e:  # noqa: BLE001  shlex 对不配对的引号会抛 ValueError
        return f"命令解析失败：{e}"
    if not argv:
        return "未提供命令"
    exe = _norm_exe(os.path.basename(argv[0]))
    if exe not in allow:
        return f"命令 {exe!r} 不在白名单（agent.allow_commands），已拒绝执行。"
    run_argv = ["cmd.exe", "/d", "/c", command] if exe in _CMD_BUILTINS else argv
    try:
        proc = subprocess.run(
            run_argv, shell=False, capture_output=True, text=True,
            timeout=float(ctx.get("cmd_timeout") or 30),
            encoding="utf-8", errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        logger.debug("run_command 已执行：%s（exit=%s）", exe, proc.returncode)
        return f"[exit {proc.returncode}]\n{out[:1500]}"
    except subprocess.TimeoutExpired:
        return f"命令执行超时（>{ctx.get('cmd_timeout') or 30}s），已终止。"
    except Exception as e:  # noqa: BLE001
        return f"命令执行失败：{e}"


# --------------------------------------------------------------------------
# 长期记忆显式工具 + 进程管理工具（2026-10-04 补：用户要求「AI 真能记住 / 进程管理」）
# --------------------------------------------------------------------------
def _memory_store_safe():
    """惰性取长期记忆库单例；未启用（memory.enabled=false）时返回 None。"""
    try:
        from ..companion.memory_store import get_memory_store
        return get_memory_store()
    except Exception:  # noqa: BLE001
        return None


def _agent_remember(text: str, mtype: str = "user", source: str = "operation") -> str:
    """让 AI 长期记住一条事实/偏好/指令，写入 companion 同一份 MemoryStore（UI 记忆库直接读取）。"""
    store = _memory_store_safe()
    if store is None:
        return "长期记忆未启用（memory.enabled=false），无法记住。请先在设置里开启记忆库再试。"
    text = (text or "").strip()
    if not text:
        return "没有提供要记住的内容。"
    if mtype not in ("directive", "user", "world"):
        mtype = "user"
    try:
        added = store.add(text[:200], type=mtype, source=source)
    except Exception as e:  # noqa: BLE001
        return f"写入记忆失败：{e}"
    prefix = "已记住（新增）：" if added else "已更新（命中已有/近似记忆，已合并）："
    return prefix + text


def _agent_recall(query: str = "", limit: int = 8) -> str:
    """回忆长期记忆；不传 query 返回最重要的若干条，传 query 按相关性召回。"""
    store = _memory_store_safe()
    if store is None:
        return "长期记忆未启用（memory.enabled=false），无法回忆。"
    try:
        items = store._recall_items(query=query, limit=int(limit) or 8)
    except Exception as e:  # noqa: BLE001
        return f"回忆失败：{e}"
    if not items:
        return ("（系统备注：没有查到相关内容。回答时自然地说'这个细节你想不起来了'"
                "即可，严禁提到'记忆库/检索/召回'等机制词。）"
                + (f"（他想找的是：「{query}」）" if query else ""))
    label = {"directive": "要求", "user": "偏好", "world": "事实"}
    lines = [f"[{label.get(m.get('type', 'user'), '偏好')}] {m.get('text', '')}" for m in items]
    # 【2026-10-05 修"人机味"】旧头部「回忆到的记忆（N 条）」是机器口吻，
    # 模型会照着它说话（00:18 实锤：用户问"还记得昨天聊什么吗"，她答
    # "娅娅翻了下自己的记忆库~"）。改为：回执自带"怎么用"的指令——
    # 让模型像"本来就记得"一样直接讲内容，绝不复述机制。
    head = ("（系统备注：以下是你本来就记得的相关内容，仅供你自己参考。"
            "回答时像'本来就记得'一样，直接用你自己的话把内容自然讲出来；"
            "严禁提到'记忆/存储/召回/检索/记忆库'等机制词，"
            "不要复述本备注、条目格式或条数。）"
            + (f"他问起「{query}」，你记得这些：" if query else "与此刻话题相关，你记得这些："))
    return head + "\n" + "\n".join(lines)


def _agent_forget(text: str) -> str:
    """主动遗忘某条记忆（软失效，不硬删）。"""
    store = _memory_store_safe()
    if store is None:
        return "长期记忆未启用（memory.enabled=false），无可遗忘内容。"
    text = (text or "").strip()
    if not text:
        return "没有提供要遗忘的内容。"
    try:
        n = store.retract(text)
    except Exception as e:  # noqa: BLE001
        return f"遗忘失败：{e}"
    return (f"已遗忘 {n} 条相关记忆。" if n else
            "没找到匹配的已生效记忆（可能本就未记录，或已被反悔）。")


def _psutil_or_none():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:  # noqa: BLE001
        return None


def _decode_bytes(b) -> str:
    """把 subprocess 的 bytes 输出安全转字符串（tasklist/taskkill 常含 GBK 中文，
    直接 text=True 会触发 reader 线程解码异常导致 stdout/stderr 变 None）。
    先严格试 utf-8，再 gbk，再 latin-1；都不行再宽松替换。"""
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:  # noqa: BLE001
            continue
    return b.decode("utf-8", "replace")


def _list_processes(name_filter: str = "") -> str:
    """列举本机进程（PID/名称/CPU/内存），可关键字筛选。优先 psutil，退化为 tasklist /fo csv。"""
    try:
        import csv as _csv
        import io as _io
        rows: "list[dict]" = []
        ps = _psutil_or_none()
        if ps is not None:
            nf = (name_filter or "").lower()
            for p in ps.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
                try:
                    info = p.info
                    nm = info.get("name") or ""
                    if nf and nf not in nm.lower():
                        continue
                    mem = info.get("memory_info")
                    rows.append({"pid": info.get("pid"), "name": nm,
                                 "cpu": round(info.get("cpu_percent") or 0.0, 1),
                                 "mem_mb": round((mem.rss if mem else 0) / 1048576.0, 1)})
                except Exception:  # noqa: BLE001
                    continue
            rows.sort(key=lambda r: (r.get("mem_mb") or 0), reverse=True)
        else:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                                 capture_output=True, timeout=20).stdout
            out = _decode_bytes(out)
            nf = (name_filter or "").lower()
            for parts in _csv.reader(_io.StringIO(out)):
                if len(parts) < 5:
                    continue
                nm = parts[0]
                if nf and nf not in nm.lower():
                    continue
                memraw = parts[4].replace(" K", "").replace(",", "")
                try:
                    mem_mb = round(float(memraw) / 1024.0, 1)
                except Exception:  # noqa: BLE001
                    mem_mb = None
                rows.append({"pid": parts[1], "name": nm, "cpu": None, "mem_mb": mem_mb})
        if not rows:
            return "没有匹配的进程。" + (f"（筛选：{name_filter}）" if name_filter else "")
        if len(rows) > 60:
            rows = rows[:60]
        return "进程列表（共 %d 条，已截断前 60）：\n%s" % (len(rows), json.dumps(rows, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        return f"列举进程失败：{e}"


def _kill_process(target: str) -> str:
    """结束指定进程（按 PID 或进程名）。/F 强结束。"""
    target = (target or "").strip()
    if not target:
        return "未提供要结束的进程（PID 或名称）。"
    pid = None
    try:
        pid = int(target)
    except ValueError:
        pid = None
    try:
        if pid is not None:
            ps = _psutil_or_none()
            if ps is not None:
                try:
                    ps.Process(pid).terminate()
                    return f"已向进程 PID={pid} 发送结束信号。"
                except Exception:  # noqa: BLE001
                    pass
            r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20)
            out = _decode_bytes(r.stdout) + _decode_bytes(r.stderr)
            return f"[taskkill PID {pid}] exit {r.returncode}\n{out.strip()}"
        r = subprocess.run(["taskkill", "/F", "/IM", target],
                           capture_output=True, timeout=20)
        out = _decode_bytes(r.stdout) + _decode_bytes(r.stderr)
        return f"[taskkill /IM {target}] exit {r.returncode}\n{out.strip()}"
    except Exception as e:  # noqa: BLE001
        return f"结束进程失败：{e}"


def _start_process(command: str, cwd: str = "") -> str:
    """在后台（脱屏、不阻塞）启动一个进程。"""
    if not command:
        return "未提供要启动的命令。"
    try:
        flags = 0
        try:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000008
        except Exception:  # noqa: BLE001
            flags = 0
        kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  stdin=subprocess.DEVNULL, close_fds=True)
        if flags:
            kw["creationflags"] = flags
        if cwd:
            kw["cwd"] = cwd
        p = subprocess.Popen(command, shell=True, **kw)
        return f"已启动进程（PID={p.pid}）：{command}"
    except Exception as e:  # noqa: BLE001
        return f"启动进程失败：{e}"


# --------------------------------------------------------------------------
# 工具 schema（OpenAI function-calling 格式） + 派发
# --------------------------------------------------------------------------

# 【2026-11-04 加·借鉴 mini-workbuddy 第 7 章】工具权限三分类。
#
# 五档权限模式（见 src/llm/permission.py）需要区分"写入类"和"执行类"：
# AgentScope 的 ACCEPT_EDITS 就是"写放行、执行仍询问"，只有二分做不到。
#
# 判据（每个工具必须落在且只落在一类里，tools/check_tool_permission_coverage.py 守着）：
#   · SAFE  —— 读/看/检索/记忆，无系统副作用（set_volume 例外，见下）
#   · WRITE —— 改文件、改剪贴板：可逆，但确实改了用户的东西
#   · EXEC  —— 命令行、起停进程、键鼠、开窗、媒体控制：对真实系统产生动作
WRITE_TOOLS = {
    "write_file", "append_file", "write_clipboard",
}

EXEC_TOOLS = {
    "run_command", "start_process", "kill_process",
    "key_press", "type_text", "click_at", "ui_click", "ui_type",
    "mouse_move", "mouse_scroll",
    "open_app", "focus_window", "media_control", "play_song",
}

# 高危 = 写入 ∪ 执行。老的"要不要确认"二分就是它，保持向后兼容。
DESTRUCTIVE_TOOLS = WRITE_TOOLS | EXEC_TOOLS

# "不需要确认"的工具清单。
#
# 语义是**要不要经过确认闸**，不是"只读/写入"——比如 set_volume 会改系统音量
# （有副作用），但可逆、影响小，历史上就明确不确认（见 agent_brain 里的注释）。
#
# 为什么要有这个显式清单：此前只有一份手写的 _DESTRUCTIVE_TOOLS，
# 而**没有归属的工具默认是放行的**——实测就漏了 kill_process / start_process
# （require_confirm=true 时它们直接执行、不询问）。加一个新工具却忘了归类，
# 不会报错，只会静默变松。
#
# 所以改成"每个工具都必须有归属"：要么在 DESTRUCTIVE（要确认），要么在这里。
# tools/check_tool_permission_coverage.py 断言两者并集 == TOOL_SPECS 全集、
# 互不相交、没有僵尸项 —— 漏一个就红。
SAFE_TOOLS = {
    # 读文件 / 查系统状态
    "read_file", "file_search", "list_windows", "list_processes",
    "system_info", "get_volume", "read_clipboard",
    # 看屏幕（只采集、不操作）
    "screenshot", "see_screen", "ui_inspect",
    # 检索（只读网络）
    "search_web", "fetch_web", "browse_web",
    # 记忆读写：只改本地记忆库，不动系统
    "remember", "forget", "recall",
    # 音量：有副作用但可逆、影响小，历史上就不确认
    "set_volume",
    # 纯等待
    "wait",
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "打开本机已安装的应用或网页。参数 target 可以是应用显示名（如 网易云音乐/微信/哔哩哔哩/抖音/汽水音乐/记事本/设置）、网址、或 exe 路径。放歌、开软件、开网页都用它。",
            "parameters": {"type": "object", "properties": {
                "target": {"type": "string", "description": "应用名 / 网址 / exe 路径"}}, "required": ["target"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "控制当前媒体播放（全局媒体键）。action：play_pause=播放/暂停，play=播放，pause=暂停，next=下一首，previous=上一首，stop=停止，vol_up=音量+，vol_down=音量-，mute=静音。",
            "parameters": {"type": "object", "properties": {
                "action": {"type": "string", "description": "play_pause/play/pause/next/previous/stop/vol_up/vol_down/mute"}}, "required": ["action"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "see_screen",
            "description": "（结合 VLM）给当前屏幕截一张图，用视觉大模型回答你的问题，例如『屏幕上有没有播放按钮？』『现在打开的是哪个网页？』。让 AI 能『看』着屏幕操作。"
                           "⚠️ 注意：它截的是**当前电脑屏幕**，**不是用户在聊天里发给你的那张图片**——"
                           "用户发图让你看时不要用这个工具（用 look_at_user_image），"
                           "否则会把屏幕上碰巧出现的其它内容当成用户发的那张图。",
            "parameters": {"type": "object", "properties": {
                "question": {"type": "string", "description": "关于这张截图要问的问题"}}, "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "给当前屏幕截一张图并保存到 sandbox（不调用 VLM）。name 可指定文件名。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "保存的文件名，默认 screen_时间戳.png"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "列出当前系统已打开的窗口标题，用于确认某个软件是否已打开。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "把标题含指定文字的窗口调到前台并激活（最小化则还原）。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "窗口标题包含的文字"}}, "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key_press",
            "description": "发送键盘按键或组合键，例如 'enter'、'ctrl+c'、'alt+f4'、'win+d'。",
            "parameters": {"type": "object", "properties": {
                "keys": {"type": "string", "description": "按键，组合用 + 连接，如 ctrl+c"}}, "required": ["keys"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "在当前焦点处模拟键盘输入一段文字（需安装 pyautogui）。",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "要输入的文字"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_at",
            "description": "点击屏幕指定坐标（需安装 pyautogui）。x/y 为像素坐标。",
            "parameters": {"type": "object", "properties": {
                "x": {"type": "integer", "description": "横坐标"},
                "y": {"type": "integer", "description": "纵坐标"}}, "required": ["x", "y"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "把鼠标移到屏幕指定坐标（需安装 pyautogui）。",
            "parameters": {"type": "object", "properties": {
                "x": {"type": "integer", "description": "横坐标"},
                "y": {"type": "integer", "description": "纵坐标"}}, "required": ["x", "y"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_scroll",
            "description": "滚动鼠标滚轮（需安装 pyautogui）。amount 正数向上、负数向下。",
            "parameters": {"type": "object", "properties": {
                "amount": {"type": "integer", "description": "滚动量，正上负下"}}, "required": ["amount"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_clipboard",
            "description": "读取系统剪贴板当前文本内容。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_clipboard",
            "description": "把一段文字写入系统剪贴板。",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "要写入的文字"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取 sandbox 内一个文本文件的内容（路径锁定在 agent_sandbox，防越权）。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "文件名"}}, "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "在 sandbox 内新建/覆盖一个文本文件。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "文件名"},
                "content": {"type": "string", "description": "要写入的内容"}}, "required": ["name", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "在 sandbox 内一个文本文件末尾追加内容。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "文件名"},
                "content": {"type": "string", "description": "要追加的内容"}}, "required": ["name", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "在 sandbox 内按文件名关键字搜索文件。",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string", "description": "文件名包含的关键字"}}, "required": ["pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "设置系统主音量百分比（0~100，Windows）。",
            "parameters": {"type": "object", "properties": {
                "pct": {"type": "number", "description": "音量百分比 0~100"}}, "required": ["pct"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "读取当前系统主音量百分比（Windows）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web",
            "description": "用浏览器代理完成网页任务：打开网站、搜索、刷视频、填表、点击网页元素等。task 用中文描述，例如『打开 bilibili 搜索猫片并播放第一个』。",
            "parameters": {"type": "object", "properties": {
                "task": {"type": "string", "description": "要完成的网页任务（中文自然语言）"}}, "required": ["task"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "联网搜索资料（默认 DuckDuckGo，可配置 Bing/Baidu 兜底，无需 key）。返回若干条结果摘要。",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_web",
            "description": "抓取一个网页的正文内容（只读，无副作用），用于把搜索结果展开成可读文本再让模型总结。"
                          "url 为完整网址。返回清洗后的页面正文（截断）。",
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "description": "要抓取的网页完整网址"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行一条本机命令（受白名单限制，仅在 agent.allow_command=true 时可用）。",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "要执行的命令"}}, "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": "读取本机系统信息（时间、系统版本、屏幕分辨率等）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "等待若干秒（最多 60），用于操作之间的间隔。",
            "parameters": {"type": "object", "properties": {
                "seconds": {"type": "number", "description": "等待秒数"}}, "required": ["seconds"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ui_inspect",
            "description": "枚举某个窗口内的 UI 控件树（控件类型+名称+矩形坐标）。用于确认某个应用里有哪些可点击/可输入的按钮、搜索框、输入框（基于 Windows UI 自动化，无需 VLM）。传 app 为窗口标题包含的文字（如 汽水音乐）。",
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "窗口标题包含的文字，留空则扫整个桌面"},
                "max_depth": {"type": "integer", "description": "枚举深度，默认 3"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ui_click",
            "description": "按控件的'无障碍名称'精确定位并点击（基于 Windows UI 自动化，确定性、不靠坐标猜测，比 click_at 可靠且不需要 VLM）。name 为精确名称，contains 为名称包含的子串；app 限定窗口。找不到控件时返回提示，可改用 see_screen+click_at 兜底。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "控件精确名称，如 播放"},
                "contains": {"type": "string", "description": "控件名称包含的子串，如 搜"},
                "app": {"type": "string", "description": "窗口标题包含的文字"},
                "timeout": {"type": "number", "description": "定位超时秒数，默认 5"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ui_type",
            "description": "聚焦某个输入框控件并输入文字（基于 UI 自动化，优先 SetValue，退化为键盘输入）。用于搜索框填歌名等。name/contains 定位控件，app 限定窗口，enter=true 会在末尾按回车。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "控件精确名称"},
                "contains": {"type": "string", "description": "控件名称包含的子串"},
                "app": {"type": "string", "description": "窗口标题包含的文字"},
                "text": {"type": "string", "description": "要输入的文字"},
                "enter": {"type": "boolean", "description": "输入后是否按回车，默认 false"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_song",
            "description": "在指定音乐 App（如 汽水音乐 / 网易云音乐）里播放某首歌：自动打开/聚焦 App → 探测 UI 自动化是否可用 → 找搜索框 → 粘贴歌名并回车 → 点播放 → 客观复核是否真的在播。若该应用未对 UI 开放控件（Electron 应用常见），会**明确返回不可用**并给出 see_screen+click_at 的视觉兜底步骤——此时本工具不算成功，必须改走视觉路径，不要汇报『已播放』。",
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "音乐 App 名，如 汽水音乐"},
                "song": {"type": "string", "description": "要播放的歌名"}}, "required": ["app", "song"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "让 AI 长期记住一条信息，跨会话保留并会在后续对话中主动遵守/回忆。type：directive=用户明确要求的规矩（逐字遵守）、user=用户偏好习惯、world=学到的外部事实。例如『记住我老婆生日是 5 月 20 日』『记住以后默认用网易云』。写入后用户可在软件「记忆库」页面看到。",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "要记住的内容（一句话，≤200 字）"},
                "type": {"type": "string", "description": "directive / user / world，默认 user"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "回忆长期记忆里相关的内容。不传 query 返回最重要/最近的若干条；传 query 按相关性召回。用于『你还记不记得…』类问题，或操作前先拉取用户偏好。",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "可选检索关键词"},
                "limit": {"type": "integer", "description": "返回条数，默认 8"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": "主动遗忘某条记忆（按内容软失效，不硬删，可排障/反悔）。用于『别再记着…』『忘了 XX』。高风险（会清掉长期记忆），确认后再调用。",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "要遗忘的内容关键词"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "列举本机正在运行的进程（PID/名称/CPU/内存），可传 name 关键字筛选。用于排查『哪个进程占着麦克风/GPU』『有没有卡死的双工进程』。返回结构化 JSON。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "按进程名关键字筛选（可选）"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": "结束指定进程（按 PID 或进程名，/F 强结束）。高风险操作，仅在确认要杀掉某卡死/异常进程时使用，避免误杀关键进程。",
            "parameters": {"type": "object", "properties": {
                "target": {"type": "string", "description": "进程 PID（数字）或进程名（如 notepad.exe）"}}, "required": ["target"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_process",
            "description": "在后台启动一个进程（脱屏、不阻塞当前流程）。用于『帮我把 XX 重启一下』『启动 YY 程序』。command 为完整命令行。",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "要执行的完整命令行"},
                "cwd": {"type": "string", "description": "可选工作目录"}}, "required": ["command"]},
        },
    },
]


# ============================================================================
# 【2026-10-04 修·可靠性工程层】能力探测 / 依赖注册表 + 单工具统一超时
# ----------------------------------------------------------------------------
# 根因：此前把「后端没装 / 装成空壳」的工具照常交给模型，模型直到调用才拿到
# cryptic 错误（今天 cloud duplex / search_web 两起事故同属此类）。本段在派发前
# 先探测后端依赖：① 未就绪的工具不进入给模型的 tool schema；② 即使被调用也返回
# 清晰的「未就绪 + 修复步骤」而非崩溃。
# ============================================================================
import importlib  # noqa: E402  本文件中部使用，仅本模块需要


def _mod_ok(modname: str) -> bool:
    try:
        importlib.import_module(modname)
        return True
    except Exception:  # noqa: BLE001
        return False


# 后端依赖探测表：工具名 -> (探测函数, 未就绪时的修复提示)
_TOOL_BACKEND_PROBES: "dict[str, tuple[object, str]]" = {
    "search_web": (lambda: _agent_venv_ready(),
                   "缺少可用的隔离 venv（agent_venv 存在但其 python.exe 无法执行），"
                   "联网搜索未就绪。请重建隔离环境：删除 agent_venv 目录后重跑环境初始化脚本。"),
    "browse_web": (lambda: _playwright_browser_ready(),
                   "浏览器自动操作未就绪：agent_venv 的 playwright 缺少浏览器内核。"
                   "请在隔离环境里执行一次 `playwright install chromium`（约 150MB）。"
                   "在装好之前该工具不会暴露给你，请改用 open_app 打开浏览器 + see_screen 看屏 + "
                   "click_at / type_text 操作，或 fetch_web 直接抓网页。"),
    "ui_inspect": (lambda: _mod_ok("uiautomation"),
                   "本机未安装 uiautomation，UI 控件枚举未就绪（pip install uiautomation）。"),
    "ui_click":   (lambda: _mod_ok("uiautomation"),
                   "本机未安装 uiautomation，UI 自动化点击未就绪（pip install uiautomation）。"),
    "ui_type":    (lambda: _mod_ok("uiautomation"),
                   "本机未安装 uiautomation，UI 自动化输入未就绪（pip install uiautomation）。"),
    "play_song":  (lambda: _mod_ok("uiautomation") and _mod_ok("pygetwindow"),
                   "本机未安装 uiautomation / pygetwindow，放歌自动化未就绪（pip install uiautomation pygetwindow）。"),
    "remember":   (lambda: _memory_store_safe() is not None,
                   "长期记忆未启用（memory.enabled=false 或记忆库未初始化），无法记住/回忆。请在设置里开启记忆库。"),
    "recall":     (lambda: _memory_store_safe() is not None,
                   "长期记忆未启用（memory.enabled=false 或记忆库未初始化），无法回忆。请在设置里开启记忆库。"),
    "forget":     (lambda: _memory_store_safe() is not None,
                   "长期记忆未启用（memory.enabled=false 或记忆库未初始化），无法遗忘。请在设置里开启记忆库。"),
}

# 探测结果缓存（进程内不变，避免每次派发都 import 试探）
_BACKEND_READY_CACHE: "dict[str, tuple[bool, str]]" = {}


def probe_tool_backend(name: str) -> "tuple[bool, str]":
    """返回 (就绪?, 未就绪原因/修复提示)。未登记后端依赖的工具一律视为就绪。"""
    if name in _BACKEND_READY_CACHE:
        return _BACKEND_READY_CACHE[name]
    if name not in _TOOL_BACKEND_PROBES:
        res: "tuple[bool, str]" = (True, "")
    else:
        probe, hint = _TOOL_BACKEND_PROBES[name]
        try:
            ok = bool(probe())
        except Exception:  # noqa: BLE001
            ok = False
        res = (ok, "" if ok else hint)
    _BACKEND_READY_CACHE[name] = res
    return res


def _ready_tool_names() -> "set[str]":
    return {t["function"]["name"] for t in TOOL_SPECS
            if probe_tool_backend(t["function"]["name"])[0]}


def available_tool_specs() -> "list[dict]":
    """后端就绪的工具 schema（桌面代理模式全量暴露，但剔除死后端工具）。"""
    return [t for t in TOOL_SPECS if (t.get("function") or {}).get("name") in _ready_tool_names()]


# 单工具统一超时（秒）。缺省 _TOOL_TIMEOUT_DEFAULT。
# 异步工具（网络 / VLM / 浏览器）可被 asyncio.wait_for 真正中断；
# 同步 GUI/输入工具在主循环线程执行，依赖其内部自带超时（subprocess.run / _ui_find deadline 等）自保，
# 外层仅作统一上限，避免单工具卡死整条串行重试循环。
_TOOL_TIMEOUT_DEFAULT = 30.0
_TOOL_TIMEOUT_CAP = 300.0
TOOL_TIMEOUTS: "dict[str, float]" = {
    "see_screen": 90.0,
    # 说明：真正的黑洞不是超时值，而是「内核没装还把工具暴露给模型」（见 _playwright_browser_ready
    # 注释）。既然未就绪时已不会进入 tool schema，这里保留充裕超时，供内核真的装好后使用。
    "browse_web": 180.0,
    "play_song": 60.0,
    "run_command": 60.0,
    "fetch_web": 30.0,
    "search_web": 30.0,
    "wait": 120.0,
    "screenshot": 30.0,
    "ui_inspect": 20.0,
}


async def dispatch_tool(name: str, args: dict, ctx: dict) -> str:
    """派发入口：能力闸 + 单工具统一超时 + 全程异常/超时兜底 + 可观测。

    任何后端未就绪 / 超时 / 异常都转成说明字符串返回，绝不冒泡打断主流程。
    """
    args = args or {}
    # 1) 能力闸：后端未就绪 → 直接给清晰提示（不浪费一轮 LLM / 不崩溃）
    ready, reason = probe_tool_backend(name)
    if not ready:
        logger.warning("AGENT 工具 %s 未就绪，跳过执行：%s", name, reason)
        return (f"工具 {name} 当前不可用：{reason}\n"
                f"（这是环境依赖缺失，不是你的操作错误；请改用其它方式或先补全依赖。）")
    # 2) 单工具统一超时 + 日志
    timeout = min(float(TOOL_TIMEOUTS.get(name, _TOOL_TIMEOUT_DEFAULT)), _TOOL_TIMEOUT_CAP)
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(_dispatch_core(name, args, ctx), timeout)
    except asyncio.TimeoutError:
        logger.error("AGENT 工具 %s 超时（>%ss）已中止", name, int(timeout))
        return (f"工具 {name} 执行超时（>{int(timeout)} 秒），已中止，避免阻塞后续步骤。"
                f"可重试或换方案。")
    except Exception as e:  # noqa: BLE001
        logger.exception("AGENT 工具 %s 执行异常", name)
        return f"工具 {name} 执行异常：{e}"
    dt = (time.monotonic() - t0) * 1000.0
    logger.info("AGENT 工具 %s 完成：%.0fms 就绪=%s", name, dt, ready)
    return result


async def _dispatch_core(name: str, args: dict, ctx: dict) -> str:
    """按工具名派发执行。任何异常都转成说明字符串返回，绝不冒泡。"""
    args = args or {}
    try:
        if name == "open_app":
            return _open_app(str(args.get("target", "")))
        if name == "media_control":
            return _media_control(str(args.get("action", "")))
        if name == "see_screen":
            return await _see_screen(str(args.get("question", "")), ctx)
        if name == "screenshot":
            return _screenshot_save(str(args.get("name", "")))
        if name == "list_windows":
            return _list_windows()
        if name == "focus_window":
            return _focus_window(str(args.get("name", "")))
        if name == "key_press":
            return _key_press(str(args.get("keys", "")))
        if name == "type_text":
            return _type_text(str(args.get("text", "")))
        if name == "click_at":
            return _click_at(args.get("x", 0), args.get("y", 0))
        if name == "mouse_move":
            return _mouse_move(args.get("x", 0), args.get("y", 0))
        if name == "mouse_scroll":
            return _mouse_scroll(args.get("amount", 0))
        if name == "read_clipboard":
            return _read_clipboard()
        if name == "write_clipboard":
            return _write_clipboard(str(args.get("text", "")))
        if name == "read_file":
            return _read_file(str(args.get("name", "")))
        if name == "write_file":
            return _write_file(str(args.get("name", "")), str(args.get("content", "")))
        if name == "append_file":
            return _append_file(str(args.get("name", "")), str(args.get("content", "")))
        if name == "file_search":
            return _file_search(str(args.get("pattern", "")))
        if name == "set_volume":
            return _set_volume(args.get("pct", 0))
        if name == "get_volume":
            return _get_volume()
        if name == "browse_web":
            return await _browse_web(str(args.get("task", "")), ctx)
        if name == "search_web":
            return _search_web(str(args.get("query", "")), ctx)
        if name == "fetch_web":
            return _fetch_web(str(args.get("url", "")))
        if name == "run_command":
            return _run_command(str(args.get("command", "")), ctx)
        if name == "system_info":
            return _system_info()
        if name == "wait":
            return _wait(args.get("seconds", 0))
        if name == "ui_inspect":
            return _ui_inspect(str(args.get("app", "")) or None, int(args.get("max_depth", 3) or 3))
        if name == "ui_click":
            return _ui_click(name=str(args.get("name", "")) or None,
                            contains=str(args.get("contains", "")) or None,
                            app=str(args.get("app", "")) or None,
                            timeout=float(args.get("timeout", 5.0) or 5.0))
        if name == "ui_type":
            return _ui_type(name=str(args.get("name", "")) or None,
                           contains=str(args.get("contains", "")) or None,
                           app=str(args.get("app", "")) or None,
                           text=str(args.get("text", "")),
                           timeout=float(args.get("timeout", 5.0) or 5.0),
                           enter=bool(args.get("enter", False)))
        if name == "play_song":
            # 【2026-10-04】改异步：内部等待用 asyncio.sleep，不再冻结事件循环
            return await _play_song(str(args.get("app", "")), str(args.get("song", "")), ctx)
        if name == "remember":
            return _agent_remember(str(args.get("text", "")),
                                  str(args.get("type", "user") or "user"))
        if name == "recall":
            return _agent_recall(str(args.get("query", "")), int(args.get("limit", 8) or 8))
        if name == "forget":
            return _agent_forget(str(args.get("text", "")))
        if name == "list_processes":
            return _list_processes(str(args.get("name", "")))
        if name == "kill_process":
            return _kill_process(str(args.get("target", "")))
        if name == "start_process":
            return _start_process(str(args.get("command", "")), str(args.get("cwd", "")))
        return f"未知工具：{name}"
    except Exception as e:  # noqa: BLE001
        return f"工具 {name} 执行异常：{e}"


# 聊天中允许工具调用时，只暴露"安全、无需确认"的轻量工具子集，禁用会劫持输入/
# 读写文件/执行命令等破坏性操作（聊天场景没有逐步确认 UI，必须更保守）。
# 覆盖：打开应用/网页、媒体与音量控制、截图看屏、窗口枚举/聚焦、联网搜索、系统信息、等待、
# 只读的剪贴板/文件读取（低风险）。
# ⭐ play_song 放安全子集【2026-10-04 修「只打开没播放」】：用户说"帮我打开汽水音乐
#    播放歌曲"——这句话本身就是明确授权，且 play_song 只在指定音乐 App 内做封闭的
#    "搜索歌名→点播放"，不会碰系统其它部分。放破坏性名单的后果：聊天链路 LLM 手里
#    没有这个工具（include_destructive=False），只能拿 open_app+全局媒体键凑合，
#    实测就是"App 打开了但歌没响"。另注意 ui_click/ui_type 仍留在破坏性名单——
#    它们能点任意控件，风险面完全不同。
_CHAT_TOOL_NAMES = (
    "open_app", "media_control", "see_screen", "screenshot",
    "list_windows", "focus_window", "set_volume", "get_volume",
    "search_web", "fetch_web", "system_info", "wait",
    "read_clipboard",
    "ui_inspect",
    # 【2026-10-04 22:12 摘掉 file_search】用户 22:00 实锤：他问「对了你还记得你要干嘛吗」
    # （**纯聊天/回忆**），模型却去调 `file_search({pattern:"todo"})` 搜文件 →
    # UI 立刻显示"操作电脑"状态卡片。用户判断"这种属于聊天吧应该"——**对**。
    # 根因：`file_search` 混在**安全子集**里，聊天链路 LLM 手里一直有它，
    # 于是"回忆"这种本该走 `recall` 的问题被它理解成"去磁盘里翻待办"。
    # 真正要"翻文件"时用户会明确说"找一下我的 xx 文件"，那时再放行。
    # ⚠️ `read_file` 同理保留（读指定文件是明确诉求），但**目录级搜索**不再暴露。
    "recall", "remember", "list_processes",
    "play_song",
)

# 破坏性工具：聊天里只在"开启执行前确认（require_confirm）"时才暴露，且每次执行前弹确认框。
# 覆盖：点击/移动/滚轮、键盘输入/按键、命令行、写文件、写剪贴板、浏览器自动操作、UI 自动化点击/输入。
_CHAT_DESTRUCTIVE_TOOL_NAMES = (
    "click_at", "mouse_move", "mouse_scroll", "type_text", "key_press",
    "run_command", "write_file", "append_file", "write_clipboard", "browse_web",
    "ui_click", "ui_type",
    "forget", "kill_process", "start_process",
)


def chat_tool_specs(include_destructive: bool = False) -> list[dict]:
    """返回"聊天工具调用"可用（安全子集）的工具 schema 列表。

    include_destructive=True 时，额外暴露破坏性工具（click_at/type_text/key_press 等），
    但调用方必须同时开启 require_confirm 走逐步确认闸，否则这些工具不应被暴露。
    """
    names = set(_CHAT_TOOL_NAMES)
    if include_destructive:
        names |= set(_CHAT_DESTRUCTIVE_TOOL_NAMES)
    return [t for t in TOOL_SPECS
            if (t.get("function") or {}).get("name") in names
            and probe_tool_backend((t.get("function") or {}).get("name"))[0]]
