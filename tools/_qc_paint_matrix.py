# -*- coding: utf-8 -*-
"""一次性取证：哪一组开关能让 Electron **真的把页面画到屏幕上**。

判据：置顶后窗口中心读到纯蓝（页面底色）。读到品红/深灰都算没出帧。
背景铺品红底窗，是为了让"没出帧"和"出帧但颜色接近"能一眼分开。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _qc_shell_transparency as q          # noqa: E402

CASES = [
    ("--in-process-gpu", ["--in-process-gpu"]),
    ("--disable-gpu", ["--disable-gpu"]),
    ("--use-angle=swiftshader", ["--use-angle=swiftshader"]),
    ("--disable-gpu-compositing", ["--disable-gpu-compositing"]),
    ("--in-process-gpu --disable-gpu-compositing",
     ["--in-process-gpu", "--disable-gpu-compositing"]),
]


def run(label, flags):
    bg = q.MagentaBackdrop()
    time.sleep(0.8)
    work = tempfile.mkdtemp(prefix="daniya_paint_")
    proc = subprocess.Popen(
        [q.ELECTRON, os.path.join(HERE, "_qc_plain.js"),
         "--user-data-dir=" + work, *flags],
        cwd=q.SHELL_DIR, env=q._env(), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    hwnd = 0
    for _ in range(80):
        hwnd = q.user32.FindWindowW(None, "DaniyaQcPlainWhite")
        if hwnd or proc.poll() is not None:
            break
        time.sleep(0.25)
    if not hwnd:
        q._kill(proc)
        bg.close()
        return f"{label:44s} ✗ 窗口没建出来（rc={proc.poll()}）"
    time.sleep(4.0)
    q.user32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0,
                          q.SWP_NOMOVE | q.SWP_NOSIZE | q.SWP_NOACTIVATE)
    time.sleep(1.2)
    r = wt.RECT()
    q.user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx, cy = r.left + (r.right - r.left) // 2, r.top + (r.bottom - r.top) // 2
    px = q._px(cx, cy)
    alive = proc.poll() is None
    q._kill(proc)
    bg.close()
    blue = px[2] >= 180 and px[0] < 80 and px[1] < 80
    return (f"{label:44s} {'✓ 出帧' if blue else '✗ 没出帧'} 中心={px}"
            f" 存活={alive}")


def main():
    q._dpi_aware()
    for label, flags in CASES:
        print(run(label, flags), flush=True)


if __name__ == "__main__":
    main()
