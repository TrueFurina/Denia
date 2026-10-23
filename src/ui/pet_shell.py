"""桌宠窗的 Electron 壳客户端。

为什么要它：桌宠要真透明，pywebview 的 WebView2 宿主做不到（见 AGENTS.md 第四条）。
但项目里到处是 `self._pet_win.show() / .hide() / .on_top / .x / .y`，
所以这里做一个**鸭子类型替身**——把 pywebview Window 的那几个成员照着实现，
调用方一行都不用改。

协议与 `shell/main.js` 对应（TCP 控制通道，命令必须带回执）。
测试侧的孪生实现是 `tools/shell_control.py`，改协议时**两处都要改**。
"""
from __future__ import annotations

import collections
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

# ---------- 自愈参数（阶段 A2） ----------
# 壳是独立进程，显卡驱动重启 / WebView2 崩溃 / 用户误杀都会让它无声地死掉，
# 表现为「桌宠不见了、但主窗口还好好的」。看门狗负责把它拉回来。
WATCHDOG_INTERVAL = 2.0     # 存活检查间隔（秒）
RESTART_MAX = 5             # 统计窗口内的最大重启次数，超了就放弃
RESTART_WINDOW = 60.0       # 统计窗口（秒）：只有"短时间内反复崩"才算风暴
RESTART_BACKOFF = 1.5       # 每次重启前的最小退避（秒），别把 CPU 打满

# ---------- GPU 回退（阶段 A3） ----------
# 本机实测（2026-10-18，tools/_qc_shell_transparency.py）：
#   裸启动            → GPU 进程 0xC0000005 连崩 → `FATAL: GPU process isn't usable`，壳起不来
#   只加 --in-process-gpu          → 进程活了、控制通道通，但**一帧都不出**（窗口是空的）
#   两个一起加        → 既活下来，又真出帧，且逐像素透明仍然成立（品红底窗采样验证）
# 所以这里做成**崩溃后自动退让**：先按原样启动，撞上 GPU 崩溃特征才带开关重试一次。
# 绝大多数机器 GPU 正常，不该白吃软件合成的性能损失。
GPU_FALLBACK_FLAGS = ["--in-process-gpu", "--disable-gpu-compositing"]
# 命中任一即判定"这次是 GPU 崩的"（Chromium 的两种措辞 + 退出码）
GPU_CRASH_MARKS = (
    "GPU process isn't usable",
    "GPU process exited unexpectedly",
    "0x80000003",
)



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


def rpc_ready() -> bool:
    """RPC 握手信息是否已注入（url + token 齐全）。"""
    return bool(_RPC_INFO.get("url") and _RPC_INFO.get("token"))


def shell_readiness() -> tuple[bool, str]:
    """壳现在能不能用；不能用时给出**可行动的原因**。

    为什么不让调用方去猜：回退日志如果只有"壳不可用"，用户只能再去翻代码。
    这里把三条前置条件（有 electron / 有 url / 有 token）分别说清楚。
    """
    if not electron_binary():
        return False, ("未找到 Electron 可执行文件"
                       "（shell/node_modules/electron/dist/electron.exe；"
                       "可用环境变量 DANIYA_ELECTRON 指定）")
    if not rpc_ready():
        return False, ("RPC 未就绪（缺 url 或令牌：需 --rpc，"
                       "或把 ui.pet_backend 设为 auto/shell）")
    return True, ""


HANDSHAKE_PATH = os.path.join(ROOT, "data", "pet_shell_handshake.json")


def _remove_handshake() -> None:
    """删掉握手文件——里面有 RPC 令牌，不该在磁盘上过夜。

    为什么不放 `atexit` 而是放 `close()`：atexit 在强杀 / 崩溃路径下不会跑，
    但 `close()` 是我们唯一能保证「壳确实被带走」的时机，先把它嵌进去，
    再由 `shutdown_all()` 兜一层。
    """
    try:
        os.remove(HANDSHAKE_PATH)
    except FileNotFoundError:
        # 没有握手文件是常态（上次干净退出过），不是错误
        pass
    except OSError as _e:  # noqa: BLE001
        logger.debug("[壳] 握手文件清理失败（忽略）：%s", _e)


def _kill_tree(pid: int) -> None:
    """连子进程一起杀（Electron 多进程，杀主进程不够）。

    只用 `taskkill /PID <pid> /T /F`：限定到具体 PID 及其子树，
    不用 `/IM` 按名匹配，避免误伤别的 Electron 应用。
    """
    if not pid or os.name != "nt":
        return
    try:
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15, check=False)
    except Exception as _e:  # noqa: BLE001  含 TimeoutExpired / FileNotFoundError
        logger.debug("[壳] taskkill 失败（回退 terminate）：%s", _e)


class PetShellClient:
    """冒充 pywebview Window 的桌宠壳客户端。"""

    def __init__(self, rpc_info: dict, web_base: str, x: int, y: int,
                 width: int, height: int, title: str = PET_TITLE,
                 profile_dir: str | None = None,
                 on_degrade: "Any | None" = None) -> None:
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
        # 【阶段 A2】自愈状态：_visible 用来在重启后把"生前是显示着的"恢复回来，
        # 否则用户没主动隐藏却自己消失了、自愈后又自己冒出来，同样迷惑。
        self._visible = False
        self._closed = False
        # 看门狗是否该收工：只有「判定反复崩溃 / 已降级」才置 True。
        # 单次重启失败不置位，留给下一轮（见 _try_restart 的返回值约定）。
        self._give_up = False
        # 【阶段 A3】是否已切到 GPU 回退开关。一旦切了就一直用，避免每次重启都
        # 先白崩一次再退让。
        self._gpu_fallback = False
        # 最近若干行壳输出：判断"这次是不是 GPU 崩的"要看崩溃日志，
        # 而 _wait() 会把行从队列里取走，所以另存一份环形缓冲。
        self._recent: "collections.deque[str]" = collections.deque(maxlen=200)
        self._restarts: list[float] = []
        self._wd_stop = threading.Event()
        self._wd_thread: "threading.Thread | None" = None
        # 彻底放弃自愈时的退路：造一个 pywebview 桌宠窗顶上（白底，但人还在）。
        # 见 `_degrade()`：宁可背景不透明，也不能让桌宠彻底消失。
        self._on_degrade = on_degrade
        self._degraded: "Any | None" = None
        # 与 pywebview Window 对齐的公开属性
        self.uid = "pet-shell"
        self.native = None   # pet_hit_rect 里 hasattr(native, 'set_pet_hit_rect') 会安全跳过

    # ---------- 生命周期 ----------
    def start(self, timeout: float = 40.0) -> bool:
        """启动壳；成功后挂上看门狗（阶段 A2）。"""
        ok = self._launch(timeout)
        if ok:
            self._start_watchdog()
        return ok

    def _launch(self, timeout: float = 40.0) -> bool:
        """spawn 一个 Electron 进程并完成控制通道握手。

        与 `start()` 分开是因为自愈要反复调用它，而 `start()` 只该跑一次
        （它还会起看门狗，重复调用会起出一堆线程）。
        """
        exe = electron_binary()
        if not exe:
            logger.warning("[壳] 找不到 Electron 可执行文件，无法启用壳桌宠")
            return False
        if not self._rpc.get("url") or not self._rpc.get("token"):
            logger.warning("[壳] 缺少 RPC 握手信息，无法启用壳桌宠")
            return False

        env = dict(os.environ)
        env.pop("ELECTRON_RUN_AS_NODE", None)   # 否则 electron.exe 退化 node，app 未定义
        self._recent.clear()                    # 只留本次启动的输出，别让旧行误判崩溃原因
        try:
            os.makedirs(self._profile, exist_ok=True)
        except OSError:
            # 建不了 profile 目录（磁盘满/权限）→ 下面 Popen 会失败并走既有报错路径
            logger.debug("[壳] profile 目录创建失败：%s", self._profile, exc_info=True)
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
        hs = HANDSHAKE_PATH
        try:
            os.makedirs(os.path.dirname(hs), exist_ok=True)
            with open(hs, "w", encoding="utf-8") as fh:
                json.dump({**self._rpc, "web_base": self._web_base}, fh)
            args[2:2] = ["--handshake", hs]
        except OSError as e:
            # 令牌没法传过去 → 页面调不通后端，宁可不开壳也不要开一个坏窗口
            logger.error("[壳] 写握手文件失败，放弃壳桌宠：%s", e)
            return False

        # GPU 回退开关放在最后：Chromium 对开关位置不敏感，但放末尾便于日志一眼看出
        if self._gpu_fallback:
            args.extend(GPU_FALLBACK_FLAGS)

        self._proc = subprocess.Popen(args, cwd=os.path.join(ROOT, "shell"), env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read_loop, daemon=True, name="pet-shell-out").start()

        line = self._wait("CONTROL", timeout)
        if not line:
            rc = self._proc.poll() if self._proc else None
            logger.error("[壳] 控制通道未就绪（进程 rc=%s）\n  命令=%s",
                         rc, " ".join(self._proc.args) if self._proc else "?")
            self._cleanup_proc(force=True)
            # 起不来就别把令牌留在盘上：create_pet_shell 失败时调用方可能
            # 直接回退，根本走不到 close()/shutdown_all()。
            _remove_handshake()
            # 【阶段 A3】GPU 崩了就先退让重试一次，别直接把壳判死
            if self._gpu_crashed() and not self._gpu_fallback:
                logger.warning("[壳] 判定为 GPU 进程崩溃，改用软件合成开关重试一次：%s",
                               " ".join(GPU_FALLBACK_FLAGS))
                self._gpu_fallback = True
                self._drain_lines()
                return self._launch(timeout)
            return False
        try:
            port = int(line.split()[1])
            self._sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            self._sock.settimeout(15)
        except (IndexError, ValueError, OSError) as e:
            logger.error("[壳] 连接控制通道失败：%s", e)
            self._cleanup_proc(force=True)
            _remove_handshake()
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
                self._recent.append(line)
                self._lines.put(line)

    def _gpu_crashed(self) -> bool:
        """最近这段输出里有没有 GPU 崩溃特征。"""
        return any(mark in ln for ln in self._recent for mark in GPU_CRASH_MARKS)

    def _drain_lines(self) -> None:
        """重试前清掉上一次留下的输出，别让旧行干扰新的握手判定。"""
        while True:
            try:
                self._lines.get_nowait()
            except queue.Empty:
                # 队列空了 = 排干完成，这是循环的正常终止条件
                return

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
                    # 等壳回话的超时→继续等（外层有总超时），不是错误
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
        if self._degraded is not None:
            self._degraded.show()
            self._visible = True
            return
        if not self._cmd("show", "SHOWN"):
            logger.warning("[壳] show 无回执")
        self._visible = True
        self._refresh_pos()

    def hide(self) -> None:
        if self._degraded is not None:
            self._degraded.hide()
            self._visible = False
            return
        if not self._cmd("hide", "HIDDEN"):
            logger.warning("[壳] hide 无回执")
        self._visible = False

    def destroy(self) -> None:
        if self._degraded is not None:
            try:
                self._degraded.destroy()
            except Exception as _e:  # noqa: BLE001
                logger.debug("[壳] 降级窗销毁异常（忽略）：%s", _e)
            self._degraded = None
        self.close()

    @property
    def on_top(self) -> bool:
        """以 OS 真实状态为准（读 WS_EX_TOPMOST），读不到才退回本地记录。

        为什么不信 Electron 的 isAlwaysOnTop()：实测在那个窗口上 setAlwaysOnTop(true)
        之后它仍回报 0；而项目里判断"是否真的置顶"本来就该看窗口样式。
        """
        if self._degraded is not None:
            return bool(getattr(self._degraded, "on_top", False))
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
        if self._degraded is not None:
            self._degraded.on_top = bool(value)
            self._topmost = bool(value)
            return
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
        if self._degraded is not None:
            return
        line = self._cmd("pos", "POS")
        if line:
            try:
                b = json.loads(line[len("POS "):])
                self._x, self._y = int(b["x"]), int(b["y"])
                self._w, self._h = int(b["width"]), int(b["height"])
            except (ValueError, KeyError, TypeError):
                # 壳报来的 POS 行解析失败 → 本轮位置不刷新（桌宠位置记忆是可选的）
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
        if self._degraded is not None:
            self._degraded.move(int(x), int(y))
            self._x, self._y = int(x), int(y)
            return
        line = self._cmd(f"move {int(x)} {int(y)}", "MOVED")
        if line:
            self._x, self._y = int(x), int(y)

    def resize(self, width: int, height: int) -> None:
        if self._degraded is not None:
            self._degraded.resize(int(width), int(height))
            self._w, self._h = int(width), int(height)
            return
        line = self._cmd(f"size {int(width)} {int(height)}", "SIZED")
        if line:
            self._w, self._h = int(width), int(height)

    def is_visible(self) -> bool:
        if self._degraded is not None:
            try:
                return bool(self._degraded.is_visible())
            except Exception:  # noqa: BLE001  pywebview 无此兜底时按本地记录
                return self._visible
        return self._cmd("visible", "VISIBLE").endswith("1")

    def evaluate_js(self, script: str) -> Any:
        """执行页面脚本并取回结果（与 pywebview Window.evaluate_js 对齐）。

        协议用 base64 传脚本与返回值：脚本里必然有空格、引号、花括号，
        走空格分隔的命令行会被切碎。
        """
        if self._degraded is not None:
            try:
                return self._degraded.evaluate_js(script)
            except Exception as _e:  # noqa: BLE001
                logger.debug("[壳] 降级窗执行脚本失败：%s", _e)
                return None

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
        if self._degraded is not None:
            return True          # 壳没了，但窗口还在（只是换了个宿主）
        return bool(self._proc and self._proc.poll() is None)

    def _degrade(self) -> bool:
        """自愈彻底放弃时，退回 pywebview 桌宠窗——**优先保证人还在**。

        降级后本对象变成一层透明代理：所有方法转发到 pywebview 窗口，
        调用方（`pet_show` / `pet_move` / ...）一行都不用改，也不会拿到 None。
        这是"桌宠消失"的最后一道闸门：白底 < 没有人。
        """
        if self._degraded is not None or self._on_degrade is None:
            return self._degraded is not None
        try:
            win = self._on_degrade()
        except Exception as _e:  # noqa: BLE001
            logger.error("[壳] 降级到 pywebview 桌宠失败：%s", _e)
            return False
        if win is None:
            return False
        self._degraded = win
        logger.error(
            "[壳] 桌宠壳反复崩溃，已降级为 pywebview 窗口：桌宠仍可用，"
            "但背景会有一层 #F0F0F0 白底。请运行 --doctor 排查 Electron 与壳配置。")
        try:
            if self._visible:
                win.show()
            if self._topmost:
                win.on_top = True
            win.move(self._x, self._y)
        except Exception as _e:  # noqa: BLE001
            logger.debug("[壳] 降级窗恢复状态失败（忽略）：%s", _e)
        return True

    # ---------- 自愈（阶段 A2） ----------
    def _start_watchdog(self) -> None:
        """起一个守护线程盯着壳进程，死了就重新拉起来。

        为什么不用主线程的定时器：主窗口是 pywebview 的主循环，
        任何耗时/阻塞都会让界面卡住；看门狗只做「查一下 + 必要时重拉」，
        放在独立线程里最省心。
        """
        if self._wd_thread is not None and self._wd_thread.is_alive():
            return
        self._wd_stop.clear()
        self._wd_thread = threading.Thread(target=self._watchdog_loop,
                                           daemon=True, name="pet-shell-watchdog")
        self._wd_thread.start()

    def _stop_watchdog(self) -> None:
        self._wd_stop.set()
        t = self._wd_thread
        self._wd_thread = None
        if t is not None and t is not threading.current_thread():
            try:
                t.join(timeout=3)
            except RuntimeError:
                # 看门狗线程停自己时 join 会抛 RuntimeError——守卫已防，兜底忽略
                pass

    def _watchdog_loop(self) -> None:
        while not self._wd_stop.wait(WATCHDOG_INTERVAL):
            if self._closed or self._wd_stop.is_set():
                return
            if self.alive():
                continue
            # 走到这里说明进程没了，而我们没主动关 → 意外退出
            logger.warning("[壳] 桌宠壳进程意外退出，尝试自愈重启")
            self._try_restart()
            if self._give_up:
                # 只有「判定为反复崩溃 / 已降级」才停手。
                # 单次没起来（端口占用、单实例锁残留、GPU 抖动）必须留到下一轮再试——
                # 这里以前写的是 `if not self._try_restart(): return`，
                # 于是**一次**失败就把看门狗弄死，桌宠静默消失（2026-10-18 质检 P1）。
                return

    def _try_restart(self) -> bool:
        """重启一次；配额用完（判定为反复崩溃）就不再尝试。

        返回值只表示「这一次是否真的起来了」，**不表示该不该继续**：
        继续/停止看 `_give_up`——
          · `False` + `_give_up=False` → 这一次没起来，但配额还在，下一轮再试；
          · `False` + `_give_up=True`  → 判定反复崩溃，已降级/放弃，看门狗收工。

        为什么要分开：合成一个 bool 时，"这次没起来"和"以后都别试了"长得一模一样，
        调用方只能二选一，于是把可重试的失败当成了终局（2026-10-18 质检 P1 的原形）。

        为什么要有配额：没有上限的话，一个必然崩的配置会让机器不停
        spawn Electron，风扇狂转、日志刷屏，比桌宠消失更难诊断。
        """
        if self._closed or self._wd_stop.is_set():
            return False
        now = time.time()
        self._restarts = [t for t in self._restarts if now - t < RESTART_WINDOW]
        if len(self._restarts) >= RESTART_MAX:
            self._give_up = True
            logger.error(
                "[壳] %.0f 秒内已崩溃重启 %d 次，判定为反复崩溃，停止自愈。",
                RESTART_WINDOW, len(self._restarts))
            self._stop_watchdog()
            if not self._degrade():
                logger.error(
                    "[壳] 无法降级到 pywebview 桌宠，桌宠将不可用。"
                    "请用 --doctor 检查 Electron 与壳配置，或把 ui.pet_backend 设为 webview。")
            return False
        self._restarts.append(now)
        if len(self._restarts) > 1:
            time.sleep(RESTART_BACKOFF)   # 连续崩时退一步，别把 CPU 打满
        was_visible = self._visible
        was_top = self._topmost
        self._cleanup_proc(force=True)
        if not self._launch():
            logger.warning("[壳] 自愈重启失败（第 %d 次），看门狗下一轮重试",
                           len(self._restarts))
            return False          # 可重试：_give_up 保持 False，配额会兜底
        if was_top:
            self.on_top = True
        if was_visible:
            self.show()
        logger.info("[壳] 自愈重启完成（第 %d 次）", len(self._restarts))
        return True

    def _cleanup_proc(self, force: bool = False) -> None:
        """收掉当前进程：先礼后兵（quit → terminate → 杀进程树）。

        为什么不能直接 terminate：Electron 是多进程架构，只杀主进程常常留下
        渲染进程/GPU 进程，桌宠的残影会留在桌面上，且下次启动会撞上
        userData 单实例限制（Electron 会无输出地立刻退出，极难排查）。
        """
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                # 关socket失败不拦清理流程（进程退出会回收句柄）
                pass
            self._sock = None
        self._buf = ""
        # 旧进程留在队列里的输出行必须丢弃：否则重启后会把它当成新进程的
        # CONTROL 行，连到一个已经不存在的端口上。
        try:
            while True:
                self._lines.get_nowait()
        except queue.Empty:
            # 排干即停：这是清理路径，队列空是预期终止
            pass
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001  subprocess.TimeoutExpired 等
                    _kill_tree(proc.pid)
                    try:
                        proc.wait(timeout=5)
                    except Exception:  # noqa: BLE001
                        pass
            elif force:
                _kill_tree(proc.pid)   # 主进程已退，但子进程可能还在
        except Exception as _e:  # noqa: BLE001
            logger.debug("[壳] 进程回收异常（忽略）：%s", _e)

    def close(self) -> None:
        """彻底关闭：停看门狗 → 让壳退出 → 杀干净残留 → 删掉含令牌的握手文件。"""
        global _active
        self._closed = True
        self._stop_watchdog()
        if self._sock:
            try:
                self._sock.sendall(b"quit\n")   # 先礼后兵：给壳一个干净退出的机会
            except OSError:
                # 壳已经不在了（socket 断）→ 直接走下面的强杀流程
                pass
        self._cleanup_proc(force=True)
        _remove_handshake()
        if _active is self:
            _active = None

    def restart_count(self) -> int:
        """已经自愈重启了几次（排障用，`--doctor` 会读它）。"""
        return len(self._restarts)


def create_pet_shell(profile_dir: str | None = None,
                     on_degrade: "Any | None" = None) -> PetShellClient | None:
    """按配置创建并启动壳桌宠；任何一步不满足就返回 None（调用方回退 pywebview）。

    前置判断统一走 `shell_readiness()`，原因文案只有一处，避免两处说法不一。

    `on_degrade` 是**崩溃兜底**：壳反复崩到配额用尽时调它造一个 pywebview 窗顶上，
    之后本对象透明转发——调用方始终拿得到可用的窗口对象，不会变成 None。
    """
    global _active
    ok, why = shell_readiness()
    if not ok:
        logger.info("[壳] 跳过壳桌宠（回退 pywebview）：%s", why)
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
    # 【阶段 A4】尺寸口径与 pywebview 路径同源（都用 pet_state.PET_W/H），
    # 有存档就回到上次位置，否则右下角。
    try:
        from .pet_state import PET_H, PET_W, restore_logical

        w, h, margin = PET_W, PET_H, 24
        px, py = restore_logical(w, h, (lw - w - margin, lh - h - margin))
    except Exception:  # noqa: BLE001
        w, h, margin = 420, 540, 24
        px, py = lw - w - margin, lh - h - margin

    client = PetShellClient(_RPC_INFO, web_base,
                            x=px, y=py, width=w, height=h,
                            profile_dir=profile_dir, on_degrade=on_degrade)
    if client.start():
        _active = client
        # 主进程退出时必须带走壳，否则桌宠会变成"没人管的孤儿窗口"
        try:
            import atexit

            atexit.register(shutdown_all)
        except Exception:  # noqa: BLE001
            pass
        return client
    return None


def shutdown_all() -> None:
    """退出清理（阶段 A3）：带走壳进程 + 删掉含令牌的握手文件。

    幂等：重复调用 / 没有活动壳都不会报错，所以可以放心挂在 atexit、
    崩溃兜底、以及 `--doctor` 之外的任何收尾路径上。
    """
    global _active
    client, _active = _active, None
    if client is not None:
        try:
            client.close()
        except Exception as _e:  # noqa: BLE001
            logger.debug("[壳] 关闭异常（忽略）：%s", _e)
    _remove_handshake()


__all__ = ["PetShellClient", "HANDSHAKE_PATH", "configure", "create_pet_shell",
           "electron_binary", "shutdown_all", "PET_TITLE"]
