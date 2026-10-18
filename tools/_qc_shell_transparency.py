# -*- coding: utf-8 -*-
"""质检探针：Electron 壳在「GPU 回退开关」下是否**仍然真透明**。

背景（2026-10-18 质检）：本机 Electron 渲染真实页面时 GPU 进程必崩
（`0xC0000005` → `FATAL: GPU process isn't usable`，rc=`0x80000003`），
壳因此被降级为实验特性、默认关闭。要恢复壳，先得回答：
**加 GPU 回退开关能起来之后，逐像素透明还在不在？**

结论（本机实测）：必须**两个开关一起加**才既活下来又真出帧——
  `--in-process-gpu`                  ：GPU 跑进浏览器进程，不再单独崩 → 进程活
  `--disable-gpu-compositing`         ：关掉 GPU 合成，走软件合成 → 画面才真的上屏
只加前者：进程活着、页面 `did-finish-load`、但**一帧都不出**（窗口是空的）。
只加后者（或 --disable-gpu / --use-angle=swiftshader）：照样崩。
见 `_qc_paint_matrix.py` 的矩阵结果。

判定用「已知底色 + 真屏幕采样」：
先铺一块纯品红（255,0,255）的全屏窗，再把**真实 shell/main.js** 压在它上面，
按 GetWindowRect 取物理像素采样：

  · 底窗自检：壳矩形**之外**的点必须是品红，否则底窗被挡 → 本次作废
  · 采样点必须是"我们的窗在最上层"（WindowFromPoint + IsChild 判定）
  · 角落/空白区 = 品红 → 该像素真的透出了下面的窗口（透明成立）
  · 中心 = 纯红 → 页面确实在渲染（排除"整窗透明=没画"的假阳性）

**必须同时跑对照组**：同几何、同开关，但 `transparent:false` 的窗，
角落必须是**非品红**。对照组不红，说明这套判据没有区分力，主结论也不作数。

踩过的坑（都改掉了）：
  1. 截屏比"启动前/后"不可用——同一坐标不同时刻落在不同窗口上。
  2. Win32 窗口必须在**建窗的那个线程**上跑消息泵，跨线程 GetMessage 收不到
     WM_PAINT，品红底色永远铺不上。
  3. 采样必须用 GetWindowRect 的**物理像素**：150% 缩放下 420×540 的窗
     物理上是 633×813、位置从 450,450 起。
  4. `capturePage` 的 alpha **不能**当判据：它抓的是页面合成面，
     `transparent:false` 时照样返回 alpha=0（实测），没有区分力。
  5. `WindowFromPoint` 返回的是 Chromium 的**子** HWND，和顶层 HWND 直接比
     永远不等；必须用 IsChild 兜一层。
  6. 不强制置顶的话，采样点可能落在别的窗口上，测的根本不是我们的窗。

用法：
  .venv/Scripts/python.exe tools/_qc_shell_transparency.py                    # 无开关（预期失败）
  .venv/Scripts/python.exe tools/_qc_shell_transparency.py --in-process-gpu   # 活但不渲染（预期失败）
  .venv/Scripts/python.exe tools/_qc_shell_transparency.py --in-process-gpu --disable-gpu-compositing
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import itertools
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_DIR = os.path.join(ROOT, "shell")
ELECTRON = os.path.join(SHELL_DIR, "node_modules", "electron", "dist", "electron.exe")
PET_TITLE = "达妮娅看板娘"

_class_seq = itertools.count()

MAGENTA = (255, 0, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
# 壳窗在 150% 缩放下的物理矩形（420×540 逻辑 → 633×813 物理），
# 对照组用同一块位置，保证两组的采样点一一对应。
CONTROL_RECT = (450, 450, 633, 813)
# 本机唯一能"既活下来又真出帧"的组合（见模块注释的矩阵结果）
FALLBACK_FLAGS = ["--in-process-gpu", "--disable-gpu-compositing"]

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
# 64 位下句柄必须显式声明：默认按 C int 传参会被截断/溢出（AGENTS 第三条同款坑）
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsChild.argtypes = [wt.HWND, wt.HWND]
user32.IsChild.restype = wt.BOOL
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wt.ATOM
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
gdi32.CreateSolidBrush.argtypes = [wt.DWORD]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
ctypes.windll.kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
ctypes.windll.kernel32.GetModuleHandleW.restype = wt.HMODULE

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT,
                             wt.WPARAM, wt.LPARAM)
WM_DESTROY, WM_PAINT, WM_APP = 0x0002, 0x000F, 0x8000
WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
HWND_TOP, HWND_TOPMOST = 0, wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


# 依赖 _PAINTSTRUCT 的声明放在结构体定义之后，否则 NameError
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(_PAINTSTRUCT)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(_PAINTSTRUCT)]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]


def _dpi_aware() -> None:
    """不声明 DPI 感知，GetWindowRect 与 ImageGrab 会各按各的虚拟化走，坐标对不上。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
    except Exception:  # noqa: BLE001
        try:
            user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _colorref(rgb) -> int:
    r, g, b = rgb
    return r | (g << 8) | (b << 16)


class SolidWindow:
    """一块纯色 Win32 窗。用来给"透明判定"提供底窗，也用来做不透明对照。

    **建窗和消息泵必须在同一个线程**：Win32 把窗口消息投给创建线程的队列，
    在别的线程 GetMessage 永远收不到 WM_PAINT，颜色就铺不上。
    """

    CLASS = "DaniyaQcSolidWindow"

    def __init__(self, color, rect=None) -> None:
        self.color = color
        self.rect = rect
        self.hwnd = None
        self._ready = threading.Event()
        self._wndproc = WNDPROC(self._on_message)   # 必须留引用，否则回调野指针
        self._brush = gdi32.CreateSolidBrush(_colorref(color))
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="qc-solid")
        self._thread.start()
        self._ready.wait(10)

    def _run(self) -> None:
        wc = _WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.hbrBackground = self._brush
        # 每个实例一个独立类名：同名类第二次 RegisterClassW 会失败，
        # 结果第二个窗沿用第一个窗的画刷/回调，颜色和消息全串（踩过）。
        cls = f"{self.CLASS}_{next(_class_seq)}"
        wc.lpszClassName = cls
        user32.RegisterClassW(ctypes.byref(wc))
        if self.rect:
            x, y, w, h = self.rect
        else:                                   # 不给矩形就铺满整个虚拟屏
            x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        self.hwnd = user32.CreateWindowExW(
            0, cls, "qc-solid", WS_POPUP | WS_VISIBLE,
            x, y, w, h, None, None, wc.hInstance, None)
        user32.SetWindowPos(self.hwnd, HWND_TOP, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _on_message(self, hwnd, msg, wp, lp):
        if msg == WM_PAINT:
            ps = _PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            r = wt.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            user32.FillRect(hdc, ctypes.byref(r), self._brush)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_APP + 1:        # 由 close() 从别的线程投递 → 在拥有它的线程销毁
            user32.DestroyWindow(hwnd)
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP + 1, 0, 0)
        self._thread.join(timeout=5)


def MagentaBackdrop() -> SolidWindow:
    """全屏纯品红底窗，给透明判定一个已知底色。"""
    return SolidWindow(MAGENTA)


def _px(x, y):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=(x, y, x + 1, y + 1)).getpixel((0, 0))[:3]


def _near(got, want, tol=24) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(got, want))


def _env() -> dict:
    # ELECTRON_RUN_AS_NODE 会让 electron.exe 退化成 node（app 未定义），必须剔掉
    return {k: v for k, v in os.environ.items()
            if k.upper() != "ELECTRON_RUN_AS_NODE"}


def _serve(page_dir: str):
    import http.server

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=page_dir, **kw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _write_page(work: str) -> tuple:
    """测试页：透明底 + 窗中心一块纯红（窗口中心 = 红块中心）。"""
    page_dir = os.path.join(work, "web", "live2d", "rig")
    os.makedirs(page_dir, exist_ok=True)
    with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<html><body style='margin:0;background:transparent'>"
                 "<div style='position:absolute;left:160px;top:220px;width:100px;"
                 "height:100px;background:#ff0000'></div></body></html>")
    return _serve(os.path.join(work, "web"))


def _sample_window(hwnd, force_top=True) -> dict:
    """置顶后按物理像素窗口矩形取三个点：角落 / 空白区 / 中心。"""
    if force_top:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        time.sleep(1.2)
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    l, t, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
    out = {"rect": [l, t, w, h]}
    for key, (x, y) in {
        "corner": (l + 8, t + 8),
        "clear": (l + int(w * 0.095), t + int(h * 0.074)),
        "center": (l + w // 2, t + h // 2),
    }.items():
        top = int(user32.WindowFromPoint(wt.POINT(x, y)) or 0)
        out[key] = _px(x, y)
        # WindowFromPoint 返回的是 Chromium 子窗，必须用 IsChild 兜一层
        out[key + "_ontop"] = top == int(hwnd) or bool(user32.IsChild(hwnd, top))
    return out


def _start_real_shell(flags: list[str], work: str):
    srv, port = _write_page(work)
    hs = os.path.join(work, "hs.json")
    with open(hs, "w", encoding="utf-8") as fh:
        json.dump({"url": f"http://127.0.0.1:{port}/rpc", "token": "t",
                   "web_base": f"http://127.0.0.1:{port}"}, fh)
    args = [ELECTRON, os.path.join(SHELL_DIR, "main.js"), "--handshake", hs,
            "--user-data-dir=" + os.path.join(work, "shell_profile"),
            "--x", "300", "--y", "300", "--w", "420", "--h", "540",
            "--hidden", "0", "--ontop", "1", "--quit", "0", *flags]
    proc = subprocess.Popen(args, cwd=SHELL_DIR, env=_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    lines: list[str] = []
    control = threading.Event()

    def _reader():
        for raw in proc.stdout:
            ln = raw.strip()
            lines.append(ln)
            if ln.startswith("CONTROL"):
                control.set()

    threading.Thread(target=_reader, daemon=True).start()
    return proc, lines, control, srv


def _kill(proc) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15)
    except Exception:  # noqa: BLE001
        pass


def _structure(flags: list[str], work: str) -> dict:
    """真实壳的结构面：能否起来 / 扩展样式 / 控制通道 / 存活。"""
    proc, lines, control, srv = _start_real_shell(flags, work)
    res: dict = {"started": control.wait(40)}
    if not res["started"]:
        fatal = [ln for ln in lines if "FATAL" in ln or "Goodbye" in ln]
        res["why"] = fatal[-1][:100] if fatal else f"rc={proc.poll()}"
        _kill(proc)
        srv.shutdown()
        return res
    time.sleep(2.0)
    hwnd = user32.FindWindowW(None, PET_TITLE)
    res["hwnd"] = int(hwnd or 0)
    if hwnd:
        ex = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        res.update(exstyle=hex(ex), layered=bool(ex & WS_EX_LAYERED),
                   topmost=bool(ex & WS_EX_TOPMOST),
                   norefdir=bool(ex & WS_EX_NOREDIRECTIONBITMAP),
                   visible=bool(user32.IsWindowVisible(hwnd)))
    port = next((int(ln.split()[1]) for ln in lines if ln.startswith("CONTROL")), None)
    if port:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(b"visible\n")
                s.settimeout(5)
                res["cmd_reply"] = s.recv(64).decode("utf-8", "replace").strip()
        except OSError as e:
            res["cmd_reply"] = f"<失败 {e}>"
    time.sleep(6.0)
    res["alive"] = proc.poll() is None
    res["rc"] = proc.poll()
    _kill(proc)
    srv.shutdown()
    return res


def _pixel_case(flags: list[str], work: str) -> dict:
    """真实壳 + 品红底窗，屏幕采样。"""
    bg = MagentaBackdrop()
    time.sleep(0.8)
    try:
        proc, lines, control, srv = _start_real_shell(flags, work)
        if not control.wait(40):
            _kill(proc)
            srv.shutdown()
            return {"ok": False, "why": "壳没起来，无法采样"}
        time.sleep(3.5)                     # 等首帧上屏
        hwnd = user32.FindWindowW(None, PET_TITLE)
        if not hwnd:
            _kill(proc)
            srv.shutdown()
            return {"ok": False, "why": f"按标题找不到壳窗（{PET_TITLE}）"}
        res = _sample_window(hwnd)
        r = res["rect"]
        res["outside"] = _px(max(r[0] - 60, 4), r[1] + r[3] // 2)
        res["backdrop_ok"] = _near(res["outside"], MAGENTA)
        res["ok"] = True
        _kill(proc)
        srv.shutdown()
        return res
    finally:
        bg.close()


def _opaque_case(work: str) -> dict:
    """对照组：同一块位置放一个**纯绿的不透明 Win32 窗**，角落必须是绿。

    刻意不用 Electron 做对照：`transparent:false` + `backgroundColor:#00000000`
    的窗实测角落也会透出底色，本身就不干净，没法当"不透明"的基准。
    也不铺底窗——单独一个置顶窗，采到的只能是它自己的颜色，z 序没得可争。
    """
    green = SolidWindow(GREEN, CONTROL_RECT)
    try:
        res = _sample_window(green.hwnd, force_top=True)
        res["ok"] = True
        return res
    finally:
        green.close()


def _fmt(d: dict) -> str:
    return (f"角落={d['corner']} 空白={d['clear']} 中心={d['center']}"
            f" 均在顶层={all(d[k + '_ontop'] for k in ('corner', 'clear', 'center'))}")


def main() -> int:
    if not os.path.exists(ELECTRON):
        print(f"[!] 找不到 Electron：{ELECTRON}")
        return 2
    _dpi_aware()
    flags = [a for a in sys.argv[1:] if a.startswith("--") and a != "--verbose"]
    work = tempfile.mkdtemp(prefix="daniya_qc_")
    print(f"[*] 开关：{' '.join(flags) or '（无）'}")

    print("[1/3] 结构面（真实壳能否起来 / 窗口样式 / 控制通道）")
    st = _structure(flags, work)
    if not st["started"]:
        print(f"    ✗ 壳没起来：{st.get('why')}")
    else:
        print(f"    ✓ 起窗 HWND={st.get('hwnd')} exstyle={st.get('exstyle')} "
              f"layered={st.get('layered')} norefdir={st.get('norefdir')} "
              f"visible={st.get('visible')} cmd={st.get('cmd_reply')!r} "
              f"8s存活={st.get('alive')}")

    print("[2/3] 像素面（品红底窗 + 屏幕采样）")
    px = _pixel_case(flags, work)
    if px.get("ok"):
        print(f"    壳窗矩形(物理)={px['rect']} 底窗自检={px['outside']}"
              f"（{'可见' if px['backdrop_ok'] else '被挡→作废'}）")
        print(f"    {_fmt(px)}")
    else:
        print(f"    ✗ {px.get('why')}")

    print("[3/3] 对照组（同位置纯绿不透明窗，角落必须是绿）")
    ctl = _opaque_case(work)
    if ctl.get("ok"):
        print(f"    对照组 {_fmt(ctl)}（应为绿 {GREEN}）")
    else:
        print(f"    ✗ {ctl.get('why')}")

    ok = True
    if not st["started"]:
        ok = False
    else:
        if not (st.get("layered") or st.get("norefdir")):
            ok = False
            print("    ! 窗口既无 LAYERED 也无 NOREDIRECTIONBITMAP → 不可能逐像素透明")
        if st.get("cmd_reply") != "VISIBLE 1":
            ok = False
            print("    ! 控制通道没正常应答")
        if not st.get("alive"):
            ok = False
            print(f"    ! 8 秒内退出（rc={st.get('rc')}）")
    if not px.get("ok") or not px.get("backdrop_ok"):
        ok = False
    else:
        if not all(px[k + "_ontop"] for k in ("corner", "clear", "center")):
            ok = False
            print("    ! 采样点不是壳窗（被别的窗口盖住）→ 本次不作数")
        if not (_near(px["corner"], MAGENTA) and _near(px["clear"], MAGENTA)):
            ok = False
            print("    ! 角落/空白没透出品红 → 不透明")
        if not _near(px["center"], RED, tol=40):
            ok = False
            print("    ! 中心不是红块 → 页面没出帧（假透明）")
    if not ctl.get("ok"):
        ok = False
        print("    ! 对照组不可用 → 判据有效性未验证，本次不下结论")
    elif not _near(ctl["corner"], GREEN, tol=40):
        ok = False
        print("    ! 对照组角落不是绿色 → 判据没有区分力，主结论作废")

    print("结论：" + ("该开关组合下**真透明仍然成立**" if ok else "该开关组合下不成立"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
