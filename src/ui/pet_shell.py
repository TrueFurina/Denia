"""桌宠窗的 Electron 壳客户端。

为什么要它：桌宠要真透明，pywebview 的 WebView2 宿主做不到（见 AGENTS.md 第四条）。
但项目里到处是 `self._pet_win.show() / .hide() / .on_top / .x / .y`，
所以这里做一个**鸭子类型替身**——把 pywebview Window 的那几个成员照着实现，
调用方一行都不用改。

协议与 `shell/main.js` 对应（TCP 控制通道，命令必须带回执）。
测试侧的孪生实现是 `tools/shell_control.py`，改协议时**两处都要改**。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import socket
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger("src.ui.pet_shell")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHELL_MAIN = os.path.join(ROOT, "shell", "main.js")
PET_TITLE = "达妮娅看板娘"

# 全局配置：由 main.py 在 RPC 启动后注入（url/token），web_base 由 create_pet_shell 现取
_RPC_INFO: dict[str, Any] = {}
_active: "PetShellClient | None" = None


def configure(rpc_info: dict) -> None:
    """注入 RPC 连接信息（main.py 启动 RPC 后调用）。"""
    _RPC_INFO.clear()
    _RPC_INFO.update(rpc_info or {})


def electron_binary() -> str | None:
    """定位 Electron 可执行文件。找不到返回 None（调用方回退到 pywebview 桌宠）。"""
    env = os.environ.get("DANIYA_ELECTRON", "").strip()
    if env and os.path.isfile(env):
        return env
    candidate = os.path.join(ROOT, "shell", "node_modules", "electron", "dist", "electron.exe")
    if os.path.isfile(candidate):
        return candidate
    return None


class PetShellClient:
    """冒充 pywebview Window 的桌宠壳客户端。"""

    def __init__(self, rpc_info: dict, web_base: str, x: int, y: int,
                 width: int, height: int, title: str = PET_TITLE,
                 profile_dir: str | None = None) -> None:
        self._rpc = dict(rpc_info)
        self._web_base = web_base
        # Electron 对同一个 userData 目录有单实例限制：残留一个实例时，新进程会
        # **无输出地立刻退出**（rc=-1），看起来像"启动失败但没有任何报错"。
        # 生产用固定目录（缓存热、启动快），测试/多开场景传独立目录。
        self._profile = profile_dir or os.path.join(ROOT, "data", "pet_shell_profile")
        self._x, self._y = int(x), int(y)
        self._w, self._h = int(width), int(height)
        self._title = title
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._buf = ""
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._topmost = False
        # 与 pywebview Window 对齐的公开属性
        self.uid = "pet-shell"
        self.native = None   # pet_hit_rect 里 hasattr(native, 'set_pet_hit_rect') 会安全跳过

    # ---------- 生命周期 ----------
    def start(self, timeout: float = 40.0) -> bool:
        exe = electron_binary()
        if not exe:
            logger.warning("[壳] 找不到 Electron 可执行文件，无法启用壳桌宠")
            return False
        if not self._rpc.get("url") or not self._rpc.get("token"):
            logger.warning("[壳] 缺少 RPC 握手信息，无法启用壳桌宠")
            return False

        env = dict(os.environ)
        env.pop("ELECTRON_RUN_AS_NODE", None)   # 否则 electron.exe 退化 node，app 未定义
        try:
            os.makedirs(self._profile, exist_ok=True)
        except OSError:
            pass
        args = [
            exe, SHELL_MAIN,
            "--user-data-dir=" + self._profile,
            "--x", str(self._x), "--y", str(self._y),
            "--w", str(self._w), "--h", str(self._h),
            "--title", self._title,
            "--hidden", "1",              # 与 pywebview 版一致：由 pet_show() 控制显示
            "--ontop", "0",
            "--quit", "0",
        ]
        # RPC 信息走临时文件最稳（命令行参数在进程列表里可见，令牌不该暴露）。
        # web_base 一并写进去：壳自己拼页面地址。**不要用 --url 传地址**——
        # 实测该开关名会让 Electron 无输出地立刻退出（rc=-1），疑似与 Chromium
        # 内置开关冲突；握手文件这条路已经端到端验证过。
        hs = os.path.join(ROOT, "data", "pet_shell_handshake.json")
        try:
            os.makedirs(os.path.dirname(hs), exist_ok=True)
            with open(hs, "w", encoding="utf-8") as fh:
                json.dump({**self._rpc, "web_base": self._web_base}, fh)
            args[2:2] = ["--handshake", hs]
        except OSError as e:
            # 令牌没法传过去 → 页面调不通后端，宁可不开壳也不要开一个坏窗口
            logger.error("[壳] 写握手文件失败，放弃壳桌宠：%s", e)
            return False

        self._proc = subprocess.Popen(args, cwd=os.path.join(ROOT, "shell"), env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read_loop, daemon=True, name="pet-shell-out").start()

        line = self._wait("CONTROL", timeout)
        if not line:
            rc = self._proc.poll() if self._proc else None
            logger.error("[壳] 控制通道未就绪（进程 rc=%s）\n  命令=%s",
                         rc, " ".join(self._proc.args) if self._proc else "?")
            self.close()
            return False
        try:
            port = int(line.split()[1])
            self._sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            self._sock.settimeout(15)
        except (IndexError, ValueError, OSError) as e:
            logger.error("[壳] 连接控制通道失败：%s", e)
            self.close()
            return False
        logger.info("[壳] 桌宠窗已在 Electron 中启动（%dx%d @ %d,%d）",
                    self._w, self._h, self._x, self._y)
        return True

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for raw in self._proc.stdout:
            line = raw.strip()
            if line:
                logger.debug("[壳输出] %s", line)
                self._lines.put(line)

    def _wait(self, marker: str, timeout: float) -> str:
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self._lines.get(timeout=0.3)
            except queue.Empty:
                if self._proc and self._proc.poll() is not None and self._lines.empty():
                    return ""
                continue
            if line.startswith(marker):
                return line
            if line.startswith(("START_FAIL", "HANDSHAKE_FAIL")):
                logger.error("[壳] %s", line)
                return ""
        return ""

    def _cmd(self, command: str, expect: str, timeout: float = 10.0) -> str:
        if not self._sock:
            return ""
        with self._lock:
            try:
                self._sock.sendall((command + "\n").encode("utf-8"))
            except OSError as e:
                logger.warning("[壳] 发送 %s 失败：%s", command, e)
                return ""
            end = time.time() + timeout
            while time.time() < end:
                if "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.strip()
                    if line and line.startswith(expect):
                        return line
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
        logger.warning("[壳] 命令 %r 未在 %.0fs 内收到回执 %s", command, timeout, expect)
        return ""

    # ---------- 与 pywebview Window 对齐的接口 ----------
    def show(self) -> None:
        if not self._cmd("show", "SHOWN"):
            logger.warning("[壳] show 无回执")
        self._refresh_pos()

    def hide(self) -> None:
        if not self._cmd("hide", "HIDDEN"):
            logger.warning("[壳] hide 无回执")

    def destroy(self) -> None:
        self.close()

    @property
    def on_top(self) -> bool:
        """以 OS 真实状态为准（读 WS_EX_TOPMOST），读不到才退回本地记录。

        为什么不信 Electron 的 isAlwaysOnTop()：实测在那个窗口上 setAlwaysOnTop(true)
        之后它仍回报 0；而项目里判断"是否真的置顶"本来就该看窗口样式。
        """
        try:
            from .pet_window import _pet_find_hwnd, _u32

            hwnd = _pet_find_hwnd()
            if hwnd:
                return bool(_u32.GetWindowLongW(hwnd, -20) & 0x00000008)  # WS_EX_TOPMOST
        except Exception:  # noqa: BLE001
            pass
        return self._topmost

    @on_top.setter
    def on_top(self, value: bool) -> None:
        want = bool(value)
        line = self._cmd("topon" if want else "topoff", "TOPON" if want else "TOPOFF")
        if line:
            self._topmost = line.endswith("1")
        # Electron 的 setAlwaysOnTop 在这个窗口上不一定落到 OS 层，用 Win32 再兜一刀
        # （SetWindowPos 的 argtypes 已在 pet_window 里声明，避免 -1 被截断成 0xFFFFFFFF）
        try:
            from .pet_window import _pet_find_hwnd, _u32

            hwnd = _pet_find_hwnd()
            if hwnd:
                ins = -1 if want else -2   # HWND_TOPMOST / HWND_NOTOPMOST
                _u32.SetWindowPos(hwnd, ins, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
                self._topmost = want
        except Exception as e:  # noqa: BLE001
            logger.debug("[壳] Win32 置顶兜底失败：%s", e)

    def wait_ready(self, timeout: float = 25.0) -> bool:
        """等页面把模型渲染出来（页面自己会在成功后把 title 写成 看板娘[OK]）。

        不要用固定 sleep 代替它：PSD 有 4MB，解析时间随机器波动，
        固定等待会在慢一点的机器上把"还没画完"误判成"功能坏了"。
        """
        end = time.time() + timeout
        while time.time() < end:
            title = self.evaluate_js("document.title")
            if isinstance(title, str) and "OK" in title:
                return True
            if not self.alive():
                return False
            time.sleep(0.25)
        logger.warning("[壳] 等待页面就绪超时（%.0fs）", timeout)
        return False

    def _refresh_pos(self) -> None:
        line = self._cmd("pos", "POS")
        if line:
            try:
                b = json.loads(line[len("POS "):])
                self._x, self._y = int(b["x"]), int(b["y"])
                self._w, self._h = int(b["width"]), int(b["height"])
            except (ValueError, KeyError, TypeError):
                pass

    @property
    def x(self) -> int:
        self._refresh_pos()
        return self._x

    @property
    def y(self) -> int:
        self._refresh_pos()
        return self._y

    @property
    def width(self) -> int:
        self._refresh_pos()
        return self._w

    @property
    def height(self) -> int:
        self._refresh_pos()
        return self._h

    def move(self, x: int, y: int) -> None:
        line = self._cmd(f"move {int(x)} {int(y)}", "MOVED")
        if line:
            self._x, self._y = int(x), int(y)

    def resize(self, width: int, height: int) -> None:
        line = self._cmd(f"size {int(width)} {int(height)}", "SIZED")
        if line:
            self._w, self._h = int(width), int(height)

    def is_visible(self) -> bool:
        return self._cmd("visible", "VISIBLE").endswith("1")

    def evaluate_js(self, script: str) -> Any:
        """执行页面脚本并取回结果（与 pywebview Window.evaluate_js 对齐）。

        协议用 base64 传脚本与返回值：脚本里必然有空格、引号、花括号，
        走空格分隔的命令行会被切碎。
        """
        import base64

        payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
        line = self._cmd("js " + payload, "JS", timeout=20)
        if not line.startswith("JS"):
            return None
        kind, _, body = line.partition(" ")
        try:
            raw = base64.b64decode(body or "")
        except Exception:  # noqa: BLE001
            return None
        if kind == "JSERR":
            logger.warning("[壳] 页面脚本执行失败：%s", raw.decode("utf-8", "replace"))
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    def close(self) -> None:
        global _active
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
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        if _active is self:
            _active = None


def create_pet_shell(profile_dir: str | None = None) -> PetShellClient | None:
    """按配置创建并启动壳桌宠；任何一步不满足就返回 None（调用方回退 pywebview）。"""
    global _active
    if not _RPC_INFO:
        logger.info("[壳] 未注入 RPC 信息，跳过壳桌宠（回退 pywebview）")
        return None
    try:
        from .main_window import _ensure_web_server

        web_base = _ensure_web_server()
    except Exception as e:  # noqa: BLE001
        logger.warning("[壳] 取静态服务器地址失败：%s", e)
        return None

    # 位置/尺寸算法与 pywebview 版完全一致：按逻辑像素贴右下角
    import ctypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    hdc = gdi32.CreateDCW("DISPLAY", None, None, None)
    dpi = gdi32.GetDeviceCaps(hdc, 88) or 96
    gdi32.DeleteDC(hdc)
    scale = max(dpi / 96.0, 1.0)
    lw, lh = int(sw / scale), int(sh / scale)
    w, h, margin = 420, 540, 24

    client = PetShellClient(_RPC_INFO, web_base,
                            x=lw - w - margin, y=lh - h - margin, width=w, height=h,
                            profile_dir=profile_dir)
    if client.start():
        _active = client
        # 主进程退出时必须带走壳，否则桌宠会变成"没人管的孤儿窗口"
        try:
            import atexit

            atexit.register(client.close)
        except Exception:  # noqa: BLE001
            pass
        return client
    return None


__all__ = ["PetShellClient", "configure", "create_pet_shell", "electron_binary", "PET_TITLE"]
