"""驱动 Electron 壳的共用工具：起进程、读日志、走 TCP 控制通道、截屏采样。

单独成模块的原因：这段逻辑原本在两个脚本里各写一份，结果两边都踩了同一个坑——
用 stdin 发命令在 Windows 下收不到，导致"隐藏后抓屏"其实抓到的是显示态，
于是透明检测变成了假阳性。同一个坑不该踩第二次。
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELECTRON = os.path.join(ROOT, "shell", "node_modules", "electron", "dist", "electron.exe")
SHELL_MAIN = os.path.join(ROOT, "shell", "main.js")


class ShellSession:
    """一次 Electron 进程的生命周期。用法：start() → wait()/cmd() → close()。"""

    def __init__(self, args: list[str] | None = None) -> None:
        self.proc: subprocess.Popen | None = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._sock: socket.socket | None = None
        self._buf = ""
        self.control_port: int | None = None
        self.bounds: dict | None = None
        self.title: str = ""
        self._args = args or []

    # ---------- 生命周期 ----------
    def start(self, timeout: float = 40.0) -> bool:
        if not os.path.exists(ELECTRON):
            print(f"[ERR] 找不到 Electron：{ELECTRON}")
            return False
        env = dict(os.environ)
        # 本机 shell 默认带这个变量，会让 electron.exe 退化成 node（app 未定义、窗口不起）
        env.pop("ELECTRON_RUN_AS_NODE", None)
        self.proc = subprocess.Popen(
            [ELECTRON, SHELL_MAIN] + self._args,
            cwd=os.path.join(ROOT, "shell"), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(target=self._read_loop, daemon=True).start()

        if not self.wait("BOUNDS", timeout):
            return False
        bline = self._last_bounds_line or ""
        try:
            self.bounds = json.loads(bline[len("BOUNDS "):])
        except Exception:
            self.bounds = None
        t = self.wait("TITLE", timeout=20)
        self.title = t[len("TITLE "):] if t else ""
        if not self.wait("CONTROL", timeout=20):
            return False
        return True

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            self._lines.put(line)

    _last_bounds_line: str = ""

    def wait(self, marker: str, timeout: float = 40.0, quiet: bool = False) -> str:
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self._lines.get(timeout=0.5)
            except queue.Empty:
                if self.proc and self.proc.poll() is not None and self._lines.empty():
                    return ""
                continue
            if line.startswith(marker):
                if not quiet:
                    print(f"    electron> {line}")
                if marker == "BOUNDS":
                    self._last_bounds_line = line
                if marker == "CONTROL":
                    try:
                        self.control_port = int(line.split()[1])
                    except (IndexError, ValueError):
                        return ""
                return line
            if line.startswith(("START_FAIL", "HANDSHAKE_FAIL")):
                print(f"    electron> {line}")
                return ""
        return ""

    # ---------- 控制通道 ----------
    def _connect(self) -> bool:
        if self._sock or not self.control_port:
            return bool(self._sock)
        try:
            s = socket.create_connection(("127.0.0.1", self.control_port), timeout=10)
        except OSError as e:
            print(f"[ERR] 连不上控制通道：{e}")
            return False
        s.settimeout(15)
        self._sock = s
        return True

    def cmd(self, command: str, expect: str, timeout: float = 15.0) -> str:
        """发一条命令并等指定前缀的回应。返回整行（不含换行）。"""
        if not self._connect():
            return ""
        assert self._sock
        try:
            self._sock.sendall((command + "\n").encode("utf-8"))
        except OSError as e:
            print(f"[ERR] 发送命令失败：{e}")
            return ""
        end = time.time() + timeout
        while time.time() < end:
            if "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                if line.startswith(expect):
                    print(f"    shell< {line}")
                    return line
                print(f"    shell< {line}")
                continue
            try:
                chunk = self._sock.recv(65536).decode("utf-8", errors="replace")
            except socket.timeout:
                continue
            except OSError:
                return ""
            if not chunk:
                return ""
            self._buf += chunk
        print(f"[WARN] 命令 {command!r} 未在 {timeout}s 内收到 {expect}")
        return ""

    def probe(self) -> dict:
        line = self.cmd("probe", "PROBE", timeout=25)
        if not line.startswith("PROBE "):
            return {}
        try:
            return json.loads(line[len("PROBE "):])
        except Exception:
            return {}

    def hide(self) -> bool:
        return bool(self.cmd("hide", "HIDDEN"))

    def show(self) -> bool:
        return bool(self.cmd("show", "SHOWN"))

    def is_visible(self) -> bool:
        line = self.cmd("visible", "VISIBLE")
        return line.endswith("1")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.sendall(b"quit\n")
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 采样 ----------
    @property
    def physical_box(self) -> tuple[int, int, int, int]:
        b = self.bounds or {"x": 0, "y": 0, "width": 0, "height": 0, "scaleFactor": 1.0}
        sf = float(b.get("scaleFactor", 1.0))
        return (int(b["x"] * sf), int(b["y"] * sf),
                int((b["x"] + b["width"]) * sf), int((b["y"] + b["height"]) * sf))


def grab(box: tuple[int, int, int, int]):
    """抓一块屏幕区域，返回 4 个采样点与图像。"""
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    pts = {"左上": (5, 5), "右上": (img.width - 6, 5),
           "左下": (5, img.height - 6), "中心": (img.width // 2, img.height // 2)}
    return {k: img.getpixel(v) for k, v in pts.items()}, img


def compare_transparency(session: ShellSession, keep_png: str | None = None):
    """判定窗口是否真的透出桌面。

    为什么不是"逐点相等"：桌面背景本身会变（壁纸动画、背后窗口在刷新），
    两次隐藏之间就可能有十几级差异，用严格相等会把真透明判成失败（实测已出现一次）。

    做法：先连续抓两次**隐藏态**，用它们之间的差异当作噪声基线；再抓显示态。
    窗口若真的不透明，角落会是 #F0F0F0(240) 一类实心色，与背景的差距远大于噪声。

    返回 (透明?, 人物在画?, 隐藏态, 显示态, 噪声)
    """
    box = session.physical_box
    if not session.hide():
        print("[ERR] 隐藏命令没有回执，无法得到真背景")
        return False, False, {}, {}, 0
    time.sleep(0.9)
    behind_a, _ = grab(box)
    time.sleep(0.9)
    behind_b, _ = grab(box)          # 仍是隐藏态：这两次的差异就是背景噪声
    if not session.show():
        print("[ERR] 显示命令没有回执")
        return False, False, behind_b, {}, 0
    time.sleep(1.5)
    shown, img = grab(box)
    if keep_png:
        img.save(keep_png)

    corners = ("左上", "右上", "左下")
    noise = 0
    for k in corners:
        if k in behind_a and k in behind_b:
            noise = max(noise, max(abs(a - b) for a, b in zip(behind_a[k], behind_b[k])))
    tol = max(noise * 2 + 4, 10)

    diffs = {}
    for k in corners:
        if k in behind_b and k in shown:
            diffs[k] = max(abs(a - b) for a, b in zip(behind_b[k], shown[k]))
    corner_ok = bool(diffs) and all(d <= tol for d in diffs.values())

    # 反面特征：#F0F0F0 是 WinForms 客户区底色，出现就说明窗口在自绘
    solid = [k for k in corners if k in shown and all(235 <= c <= 245 for c in shown[k])]
    if solid:
        corner_ok = False

    center_painted = bool(behind_b) and shown.get("中心") != behind_b.get("中心")
    print(f"  背景噪声={noise} 容差={tol} 各角落与背景差={diffs}"
          + (f" 命中 WinForms 底色于 {solid}" if solid else ""))
    return corner_ok, center_painted, behind_b, shown, noise


__all__ = ["ShellSession", "grab", "compare_transparency", "ELECTRON"]
