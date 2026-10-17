"""输入证据门：pynput 全局鼠标/键盘钩子，判断"玩家是否正在操作游戏"。

核心动机：旧方案判定"玩家击杀了敌人"纯靠 OCR 击杀播报文字里玩家名的相对
位置去猜归属，完全没有"玩家真的在开枪"的证据——结果"没打人却说打死人了"
（玩家只是被播报提及 / 多条播报叠加时玩家名位置错判）。

新方案：击杀判定必须先过这道证据门。玩家没开枪 = 没有输入证据 → 击杀播报
区出现任何文字都静默，根治误报。

设计：
- 独立守护线程跑 pynput listener（pynput 是全局钩子，不依赖前台窗口焦点，
  全屏/独占游戏通常也能收到；不阻塞 asyncio 主循环）
- 只记录"时间戳"到 deque，不缓存按键细节，内存占用极小（自动丢弃过期）
- firing_recently()：最近窗口内鼠标左键按下次数 ≥ min_clicks（连点=自动/
  半自动武器；投掷/挥刀/换弹也伴随按键）→ 视为"正在战斗"
- active_recently()：最近窗口内有任意鼠标/键盘输入（宽松判定：玩家在操作）
- 钩子启动失败（缺依赖/权限不足/反作弊屏蔽）→ ok=False → 上层不判 kill
  （宁可少互动，也不要对着没开枪的画面误报击杀）
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# 默认射击证据参数
_DEF_WINDOW_S = 8.0      # 击杀证据时间窗：击杀播报在开枪后 1~2s 内出现，放宽到 8s
_DEF_MIN_CLICKS = 2      # 窗口内至少几次左键按下才算"开了枪"（排除偶尔点一下菜单）
_DEF_ACTIVE_WINDOW_S = 5.0


class InputSensor:
    """输入证据门：全局监听鼠标左键/键盘按键，供击杀判定把关。

    - 独立线程跑 pynput listener
    - 查询接口都是"只读最近时间戳"，线程安全（用锁保护 deque）
    - 未安装 pynput 或钩子启动失败时 ok=False，所有判定返回 False
      （上层逻辑此时对击杀一律静默，符合"宁可少互动不要误报"）
    """

    def __init__(self, window_s: float = _DEF_WINDOW_S,
                 min_clicks: int = _DEF_MIN_CLICKS,
                 active_window_s: float = _DEF_ACTIVE_WINDOW_S) -> None:
        self._window_s = float(window_s)
        self._min_clicks = int(min_clicks)
        self._active_window_s = float(active_window_s)
        self._left_clicks: deque[float] = deque()  # 左键按下时间戳
        self._any_input: deque[float] = deque()    # 任意输入时间戳
        self._lock = threading.Lock()
        self._ok = False
        self._threads: list[threading.Thread] = []
        self._listeners: list = []
        self._err: Optional[str] = None

    # ---------------- 启动/停止 ----------------
    def start(self) -> None:
        """启动钩子（幂等）。失败不抛异常，置 ok=False 并在日志说明原因。"""
        if self._ok:
            return
        try:
            from pynput import mouse, keyboard
        except Exception as e:  # noqa: BLE001
            self._err = f"pynput 未安装或不可用: {e}"
            logger.warning("输入证据门不可用（%s）——击杀判定将始终静默", self._err)
            return
        try:
            m = mouse.Listener(on_click=self._on_click)
            k = keyboard.Listener(on_press=self._on_press)
            m.daemon = True
            k.daemon = True
            m.start()
            k.start()
            self._listeners = [m, k]
            self._ok = True
            logger.info("输入证据门已启动: 窗口=%.1fs 最少左键=%d 次", self._window_s, self._min_clicks)
        except Exception as e:  # noqa: BLE001
            self._err = str(e)
            logger.warning("输入证据门启动失败（%s）——击杀判定将始终静默", self._err)

    def stop(self) -> None:
        for l in self._listeners:
            try:
                l.stop()
            except Exception:  # noqa: BLE001
                pass
        self._listeners.clear()
        self._ok = False

    # ---------------- 回调（pynput 线程） ----------------
    def _record(self, q: deque[float]) -> None:
        now = time.time()
        with self._lock:
            q.append(now)
            # 只留窗口内的（自动丢弃过期，内存上限固定）
            window = max(self._window_s, self._active_window_s) + 1.0
            while q and now - q[0] > window:
                q.popleft()

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        try:
            if button.name == "left" and pressed:
                self._record(self._left_clicks)
                self._record(self._any_input)
        except Exception:  # noqa: BLE001
            pass

    def _on_press(self, key) -> None:
        self._record(self._any_input)

    # ---------------- 查询（任意线程可调） ----------------
    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def err(self) -> Optional[str]:
        return self._err

    def firing_recently(self, window_s: Optional[float] = None,
                        min_clicks: Optional[int] = None) -> bool:
        """最近窗口内左键连点次数达标 → 视为"正在战斗/开了枪"。

        这是击杀判定的【硬证据】：没达标 → 击杀播报出现也不判 kill。
        """
        if not self._ok:
            return False
        window = self._window_s if window_s is None else window_s
        need = self._min_clicks if min_clicks is None else min_clicks
        if need <= 0:
            return True
        now = time.time()
        with self._lock:
            n = sum(1 for t in self._left_clicks if now - t <= window)
        return n >= need

    def active_recently(self, window_s: Optional[float] = None) -> bool:
        """最近窗口内是否有任意鼠标/键盘输入（宽松：玩家在操作设备）。"""
        if not self._ok:
            return False
        window = self._active_window_s if window_s is None else window_s
        now = time.time()
        with self._lock:
            n = sum(1 for t in self._any_input if now - t <= window)
        return n > 0

    # ---------------- 调试 ----------------
    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            return {
                "ok": self._ok,
                "err": self._err,
                "left_clicks_recent": sum(1 for t in self._left_clicks if now - t <= self._window_s),
                "any_input_recent": sum(1 for t in self._any_input if now - t <= self._active_window_s),
            }
