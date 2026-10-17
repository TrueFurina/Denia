"""验证「换壳后能否拿到桌面级透明」——同一份 rig 页面，Electron 宿主。

比对方法与 pywebview 那组实验一致，但把"桌面真值"改成**让窗口自己隐藏后立刻抓屏**，
避免背景里其他窗口/动画干扰（第一版就是因为背景在变而误判）。

判定：
  隐藏态 == 显示态（角落逐点相同）  →  窗口真的透出桌面
  中心不同                          →  人物确实在渲染
  同时读页面像素 alpha              →  确认"页面透明"这一层也是真的（0 = 全透明）

用法：
  .venv/Scripts/python.exe tools/poc_shell_transparency.py
  .venv/Scripts/python.exe tools/poc_shell_transparency.py --keep   # 结束后不关窗，便于肉眼看
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELECTRON = os.path.join(ROOT, "shell", "node_modules", "electron", "dist", "electron.exe")
X, Y, W, H = 200, 200, 320, 420


def grab(box):
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    pts = {"左上": (5, 5), "右上": (img.width - 6, 5), "左下": (5, img.height - 6),
           "中心": (img.width // 2, img.height // 2)}
    return {k: img.getpixel(v) for k, v in pts.items()}, img


def main() -> int:
    keep = "--keep" in sys.argv
    if not os.path.exists(ELECTRON):
        print(f"[ERR] 找不到 Electron：{ELECTRON}")
        return 1

    # 本机 shell 可能带着 ELECTRON_RUN_AS_NODE=1，会让 electron.exe 退化成 node（app 未定义）
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    args = [ELECTRON, os.path.join(ROOT, "shell", "main.js"),
            "--x", str(X), "--y", str(Y), "--w", str(W), "--h", str(H), "--quit", "0"]
    proc = subprocess.Popen(args, cwd=os.path.join(ROOT, "shell"), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)

    def send(cmd: str) -> None:
        try:
            proc.stdin.write(cmd + "\n")
            proc.stdin.flush()
        except Exception:
            pass

    # Electron 的 stdout 由独立线程读进队列：主线程用带超时的轮询，
    # 否则 readline() 在子进程没输出时会无限阻塞（第一版就是这么挂住的）
    import queue as _q
    import threading as _th

    lines: "_q.Queue[str]" = _q.Queue()
    seen: list[str] = []

    def _reader() -> None:
        for raw in proc.stdout:
            line = raw.strip()
            if line:
                lines.put(line)
                seen.append(line)
        lines.put("__EOF__")

    _th.Thread(target=_reader, daemon=True).start()

    def wait(marker: str, timeout: float = 40.0) -> str:
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = lines.get(timeout=0.5)
            except _q.Empty:
                if proc.poll() is not None and lines.empty():
                    return ""
                continue
            if line == "__EOF__":
                return ""
            print(f"    electron> {line}")
            if line.startswith(marker):
                return line
        return ""

    bounds_line = wait("BOUNDS")
    if not bounds_line:
        print("[ERR] Electron 启动失败")
        try:
            proc.kill()
        except Exception:
            pass
        return 1
    bounds = json.loads(bounds_line[len("BOUNDS "):])
    if not wait("READY", timeout=40):
        print("[WARN] 没等到 READY，仍继续采样")

    sf = float(bounds.get("scaleFactor", 1.0))
    box = (int(bounds["x"] * sf), int(bounds["y"] * sf),
           int((bounds["x"] + bounds["width"]) * sf),
           int((bounds["y"] + bounds["height"]) * sf))

    send("alpha")
    time.sleep(0.6)
    time.sleep(6.0)  # 等 PSD 解析 + 首帧

    # ① 隐藏 → 真背景
    send("hide")
    wait("HIDDEN", timeout=10)
    time.sleep(0.9)
    behind, _ = grab(box)
    print("隐藏态（真背景）：", " ".join(f"{k}={v}" for k, v in behind.items()))

    # ② 显示 → 窗口实际合成结果
    send("show")
    wait("SHOWN", timeout=10)
    time.sleep(1.6)
    shown, img = grab(box)
    img.save(os.path.join(ROOT, "tools", "_poc_shell.png"))
    print("显示态（窗口）：  ", " ".join(f"{k}={v}" for k, v in shown.items()))

    corner_keys = ("左上", "右上", "左下")
    corner_ok = all(shown[k] == behind[k] for k in corner_keys)
    center_painted = shown["中心"] != behind["中心"]

    print()
    print(f"角落与真背景一致：{'✅ 是' if corner_ok else '❌ 否'}")
    print(f"中心有人在渲染：  {'✅ 是' if center_painted else '❌ 否'}")

    if not keep:
        send("quit")
        try:
            proc.terminate()
        except Exception:
            pass

    if corner_ok and center_painted:
        print("\n✅ 结论：同一份 rig 页面在 Electron 宿主下真正透出桌面，人物正常渲染")
        return 0
    if corner_ok:
        print("\n⚠️ 窗口透明了，但中心与背景同色 → 人物没加载出来（看 tools/_poc_shell.png）")
        return 2
    print("\n❌ 仍未透出桌面")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
