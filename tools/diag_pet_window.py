"""桌宠窗口透明诊断：起一个与正式窗同参数的窗口，截屏取像素，判断白底来自哪一层。

背景
----
用户反馈"桌面悬浮小人背后有白底长方形"。页面上已经三重要求透明
（`html{background:transparent}`、`body.bare{background:transparent!important}`、
JS 里再 `document.body.style.background='transparent'`），WebView2 控件也由
pywebview 设了 `DefaultBackgroundColor=Color.Transparent`。若仍然白，
就只剩 WinForms 窗体客户区这一层。

本脚本把这件事变成可测的数字：
  角落像素 = 白色(255,255,255) → 窗体客户区在画白底（需要动窗体，不是动页面）
  角落像素 ≈ 桌面背景      → 透明链路是通的，白底来自页面内容

用法（会短暂弹一个窗口，3 秒后自动关闭）：
    .venv/Scripts/python.exe tools/diag_pet_window.py
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TITLE = "诊断-达妮娅看板娘"
W, H, X, Y = 420, 540, 160, 160


def _sample(box: tuple[int, int, int, int], tag: str) -> dict[str, list[int]]:
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    res: dict[str, list[int]] = {}
    pts = {
        "左上角(4,4)": (4, 4),
        "右上角(-5,4)": (img.width - 5, 4),
        "左下角(4,-5)": (4, img.height - 5),
        "底部中央(w/2,h-6)": (img.width // 2, img.height - 6),
    }
    for name, (px, py) in pts.items():
        r, g, b = img.getpixel((px, py))
        res[name] = [r, g, b]
        print(f"  [{tag}] {name:20s} RGB=({r:3d},{g:3d},{b:3d})")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_diag_pet{tag}.png")
    img.save(out)
    print(f"  [{tag}] 截图：{out}")
    return res


def main() -> int:
    import webview

    from src.ui.main_window import _ensure_web_server

    try_layered = "--layered-test" in sys.argv
    base = _ensure_web_server()
    url = base + "/live2d/rig/index.html?v=2"

    win = webview.create_window(
        TITLE, url=url, frameless=True, transparent=True,
        easy_drag=False, text_select=False, on_top=True,
        x=X, y=Y, width=W, height=H, hidden=False,
    )

    def probe() -> None:
        time.sleep(4.0)
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, TITLE)
        if not hwnd:
            print("[ERR] 找不到窗口句柄")
            _close()
            return
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        box = (rect.left, rect.top, rect.right, rect.bottom)
        GWL_EXSTYLE = -20
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        print(f"窗口矩形: {box} 尺寸 {box[2]-box[0]}x{box[3]-box[1]}")
        print(f"扩展样式: 0x{ex & 0xFFFFFFFF:08X}  "
              f"LAYERED={'是' if ex & 0x80000 else '否'}  "
              f"NOREDIRECTIONBITMAP={'是' if ex & 0x200000 else '否'}")

        before = _sample(box, "before")
        try:
            title = win.evaluate_js("document.title")
            print(f"页面标题：{title}")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 读 title 失败：{e}")

        whites = [k for k, v in before.items() if all(230 < c < 250 for c in v)]
        if whites:
            print(f"\n→ 判定：{len(whites)}/{len(before)} 个采样点是 #F0F0F0（WinForms 客户区底色）")
        else:
            print("\n→ 判定：采样点不是 #F0F0F0")

        if try_layered:
            print("\n=== 实验：给窗体挂 WS_EX_LAYERED + 把 #F0F0F0 设为 key 色 ===")
            WS_EX_LAYERED = 0x00080000
            LWA_COLORKEY = 0x00000001
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            ok = user32.SetLayeredWindowAttributes(hwnd, 0x00F0F0F0, 0, LWA_COLORKEY)
            print(f"  SetLayeredWindowAttributes 返回 {ok}")
            time.sleep(1.2)
            after = _sample(box, "layerd")
            blacks = [k for k, v in after.items() if all(c < 12 for c in v)]
            fm = [k for k, v in after.items() if all(230 < c < 250 for c in v)]
            if blacks:
                print(f"  ✗ {len(blacks)} 个采样点变纯黑 → WS_EX_LAYERED 确实会毁掉 WebView2 合成")
                print("     （与项目里 2026-09-05 的注释结论一致）")
            elif fm:
                print(f"  ✗ 仍是 #F0F0F0（{len(fm)} 点）→ colorkey 对该窗无效")
            else:
                print("  ✓ 采样点既不是 #F0F0F0 也不是纯黑 → 分层+colorkey 可行，"
                      "透出的是桌面内容")

            # 决定性一步：把窗口藏掉，抓同一块区域 —— 那才是"背后真正有什么"
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
            time.sleep(0.7)
            behind = _sample(box, "behind")
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            same = all(behind[k] == after[k] for k in behind if k in after)
            print(f"\n  → 与隐藏后的桌面逐点比对：{'完全一致 ✅ 说明已真正透出桌面' if same else '不一致 ❌ 分层窗在自绘深色'}")

        if "--noredir-test" in sys.argv:
            print("\n=== 实验 2：WS_EX_NOREDIRECTIONBITMAP（Chromium 透明窗所用路径） ===")
            WS_EX_NOREDIRECTIONBITMAP = 0x00200000
            # 先把上一个实验的 layered 摘掉
            ex2 = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & ~0x00080000
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex2 | WS_EX_NOREDIRECTIONBITMAP)
            time.sleep(1.2)
            nr = _sample(box, "noredir")
            user32.ShowWindow(hwnd, 0)
            time.sleep(0.7)
            behind2 = _sample(box, "behind2")
            user32.ShowWindow(hwnd, 5)
            same2 = all(behind2[k] == nr[k] for k in behind2 if k in nr)
            print(f"  → 与桌面比对：{'✓ 完全一致，NOREDIRECTIONBITMAP 可用' if same2 else '✗ 不一致，仍在自绘'}")

        if "--rgn-test" in sys.argv:
            print("\n=== 实验 3：SetWindowRgn 圆角裁剪（项目注释说会破坏子窗合成，实测） ===")
            ex3 = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & ~0x00080000 & ~0x00200000
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex3)
            gdi32 = ctypes.windll.gdi32
            w = box[2] - box[0]
            h = box[3] - box[1]
            # 圆角矩形：半径取短边的 18%，去掉四个直角
            r = int(min(w, h) * 0.18)
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r)
            ok = user32.SetWindowRgn(hwnd, rgn, True)
            print(f"  SetWindowRgn 返回 {ok}（半径 {r}px，窗口 {w}x{h}）")
            time.sleep(1.0)
            rgn_shot = _sample(box, "rgn")
            # 中心点必须是人物（不能变黑），这里用"非 #F0F0F0 且非纯黑"
            cx, cy = box[0] + w // 2, box[1] + h // 2
            from PIL import ImageGrab
            center = ImageGrab.grab(bbox=(cx, cy, cx + 1, cy + 1), all_screens=True).convert("RGB").getpixel((0, 0))
            print(f"  窗口中心像素 RGB={center}  "
                  f"{'✓ 人物仍在渲染' if not all(c < 12 for c in center) else '✗ 变黑，裁剪破坏合成'}")

        if "--blur-test" in sys.argv:
            print("\n=== 实验 4：DwmEnableBlurBehindWindow（空区域 = 整窗玻璃，Win8+ 可透桌面） ===")

            class DWM_BLURBEHIND(ctypes.Structure):
                _fields_ = [
                    ("dwFlags", ctypes.c_uint32),
                    ("fEnable", ctypes.c_bool),
                    ("hRgnBlur", ctypes.c_void_p),
                    ("fTransitionOnMaximized", ctypes.c_bool),
                ]

            dwm = ctypes.windll.dwmapi
            # 清掉前几个实验留下的样式
            ex4 = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & ~0x00080000 & ~0x00200000
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex4)
            # 空区域：hRgnBlur = 空矩形区域，表示"整窗透明但仍参与合成"
            gdi32 = ctypes.windll.gdi32
            empty = gdi32.CreateRectRgn(0, 0, -1, -1)
            bb = DWM_BLURBEHIND()
            bb.dwFlags = 0x1  # DWM_BB_ENABLE
            bb.fEnable = True
            bb.hRgnBlur = empty
            bb.fTransitionOnMaximized = False
            hr = dwm.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
            print(f"  DwmEnableBlurBehindWindow hr=0x{hr & 0xFFFFFFFF:08X}（0 = 成功）")
            time.sleep(1.0)
            # 先取"窗口在时"的像素，再藏窗取真背景，最后逐点对照
            user32.ShowWindow(hwnd, 5)
            time.sleep(0.4)
            bl = _sample(box, "blur")
            user32.ShowWindow(hwnd, 0)
            time.sleep(0.7)
            behind3 = _sample(box, "behind3")
            user32.ShowWindow(hwnd, 5)
            print("\n  逐点对照（窗口在时 vs 真背景）：")
            allsame = True
            for k in bl:
                a, b2 = tuple(bl[k]), tuple(behind3[k])
                d = max(abs(x - y) for x, y in zip(a, b2))
                allsame = allsame and d <= 2
                print(f"    {k:20s} 窗口内={a}  背景={b2}  最大差={d}")
            print(f"  → {'✓ 差值≤2，整窗已真正透出桌面（DWM 玻璃方案可用）' if allsame else '✗ 仍有填充色，DWM 方案不可用'}")

        print("\n原始数据：", json.dumps(before, ensure_ascii=False))
        _close()

    def _close() -> None:
        try:
            win.destroy()
        except Exception:  # noqa: BLE001
            pass

    import threading
    threading.Thread(target=probe, daemon=True).start()
    webview.start(debug=False, gui="edgechromium")
    return 0


if __name__ == "__main__":
    sys.exit(main())
