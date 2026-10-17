"""隔离 WebView2 透明链路：在最小用例上逐个试原生手法，看哪一种能真正透出桌面。

判定标准（唯一可信）：
  窗口可见时采样点 == 窗口隐藏后同一位置像素  →  透出桌面（真透明）
  否则记录实际颜色，用于区分"窗体自绘 #F0F0F0"与"合成面变黑"

用法（每个模式起独立进程，避免窗口状态互相污染）：
  .venv/Scripts/python.exe tools/diag_transparency.py --mode base
  .venv/Scripts/python.exe tools/diag_transparency.py --mode alpha255
  .venv/Scripts/python.exe tools/diag_transparency.py --mode formtrans
  .venv/Scripts/python.exe tools/diag_transparency.py --mode key
  .venv/Scripts/python.exe tools/diag_transparency.py --mode layeredkey
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

TITLE = "diag-transparency"
X, Y, W, H = 200, 200, 260, 260

HTML = (
    "<!doctype html><html style='background:transparent'>"
    "<body style='background:transparent;margin:0'>"
    "<div style='position:absolute;left:80px;top:80px;width:100px;height:100px;"
    "border-radius:50%;background:#E23E57'></div>"
    "</body></html>"
)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
LWA_COLORKEY = 0x1
LWA_ALPHA = 0x2


def _grab(box):
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    pts = {"左上": (3, 3), "右上": (img.width - 4, 3),
           "左下": (3, img.height - 4), "中心": (img.width // 2, img.height // 2)}
    return {k: img.getpixel(v) for k, v in pts.items()}, img


def _set_exstyle(user32, hwnd, add: bool, bit: int):
    cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new = (cur | bit) if add else (cur & ~bit)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new)


def main() -> int:
    import webview

    mode = "base"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]

    # --dpi：在任何窗口创建前启用 Per-Monitor V2，用来看坐标是否被换算搞偏
    if "--dpi" in sys.argv:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from src.win_shell import dpi_awareness, enable_dpi_awareness

        how = enable_dpi_awareness()
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = gdi32.CreateDCW("DISPLAY", None, None, None)
        dpi = gdi32.GetDeviceCaps(hdc, 88)
        gdi32.DeleteDC(hdc)
        print(f"DPI 感知={dpi_awareness()}（{how}） 逻辑DPI={dpi} "
              f"屏幕={user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}")
        print(f"请求窗口 {W}x{H} 逻辑像素 → 在 {dpi / 96:.2f}x 下应为 "
              f"{int(W * dpi / 96)}x{int(H * dpi / 96)} 物理像素")

    win = webview.create_window(
        title=TITLE, html=HTML, frameless=True, transparent=True, easy_drag=False,
        text_select=False, on_top=True, x=X, y=Y, width=W, height=H, hidden=False,
    )

    def probe():
        time.sleep(3.5)
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetLayeredWindowAttributes.argtypes = [
            wt.HWND, wt.COLORREF, ctypes.c_ubyte, wt.DWORD]
        hwnd = user32.FindWindowW(None, TITLE)
        if not hwnd:
            print("[ERR] 找不到窗口")
            return

        from webview.platforms.winforms import BrowserView

        form = list(BrowserView.instances.values())[0]

        applied = "无（基线）"
        try:
            if mode == "alpha255":
                # 让窗口走分层合成路径，但保持整体不透明：由 DWM 读取每像素 alpha
                _set_exstyle(user32, hwnd, True, WS_EX_LAYERED)
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
                applied = "WS_EX_LAYERED + LWA_ALPHA(255)"
            elif mode == "layeredkey":
                _set_exstyle(user32, hwnd, True, WS_EX_LAYERED)
                key = 0x00F0F0F0  # COLORREF 0x00BBGGRR：#F0F0F0
                user32.SetLayeredWindowAttributes(hwnd, key, 0, LWA_COLORKEY)
                applied = "WS_EX_LAYERED + LWA_COLORKEY(#F0F0F0)"
            elif mode == "key":
                from System.Drawing import Color

                form.TransparencyKey = Color.FromArgb(240, 240, 240)
                applied = "Form.TransparencyKey=#F0F0F0"
            elif mode == "formtrans":
                from System.Drawing import Color

                form.BackColor = Color.FromArgb(0, 0, 0, 0)
                applied = "Form.BackColor=ARGB(0,0,0,0)"
            elif mode == "layered":
                # 只加 LAYERED，绝不调 SetLayeredWindowAttributes
                _set_exstyle(user32, hwnd, True, WS_EX_LAYERED)
                applied = "WS_EX_LAYERED（不调 LayeredWindowAttributes）"
            elif mode == "layered_noredir":
                _set_exstyle(user32, hwnd, True, WS_EX_LAYERED | WS_EX_NOREDIRECTIONBITMAP)
                applied = "WS_EX_LAYERED|NOREDIRECTIONBITMAP（不调 Attributes）"
            elif mode == "alpha0":
                _set_exstyle(user32, hwnd, True, WS_EX_LAYERED)
                user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)
                applied = "WS_EX_LAYERED + LWA_ALPHA(0)"
            elif mode == "createparams":
                applied = "由 CreateParams 在创建期设置（此分支不做事，见 class 覆盖）"
        except Exception as e:  # noqa: BLE001
            applied = f"应用失败：{type(e).__name__}: {e}"

        time.sleep(1.2)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        box = (rect.left, rect.top, rect.right, rect.bottom)
        print(f"   实际窗口矩形 {box} 尺寸 {box[2]-box[0]}x{box[3]-box[1]}")
        try:
            print(f"   GetDpiForWindow={user32.GetDpiForWindow(hwnd)} "
                  f"pywebview.win={win.width}x{win.height} 请求={W}x{H}")
            print(f"   devicePixelRatio={win.evaluate_js('window.devicePixelRatio')}")
        except Exception as e:  # noqa: BLE001
            print(f"   读取 DPI/尺寸失败：{e}")
        shown, img = _grab(box)
        img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              f"_tr_{mode}.png"))

        user32.ShowWindow(hwnd, 0)
        time.sleep(0.8)
        behind, _ = _grab(box)
        user32.ShowWindow(hwnd, 5)

        hit = sum(1 for k in shown if shown[k] == behind[k])
        print(f"[{mode}] 手法={applied}")
        print(f"   可见 {shown}")
        print(f"   桌面 {behind}")
        print(f"   → 一致点 {hit}/4 ", end="")
        if hit >= 3:
            print("✅ 真正透出桌面")
        else:
            print("❌ 未透出")
        try:
            win.destroy()
        except Exception:
            pass

    threading.Thread(target=probe, daemon=True).start()
    webview.start(debug=False, gui="edgechromium")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
