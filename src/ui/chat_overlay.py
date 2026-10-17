"""游戏内透明文本框悬浮层：把 AI 说的话 + 你语音说过去的话显示在游戏画面上。

原理与 OCR 区域校准悬浮窗一致：tkinter 无边框置顶窗口 + transparentcolor 透明
背景，文字画在 canvas 深色圆角卡片上，其余区域露出真实游戏画面。在独立线程里
跑 mainloop（Windows 上 tkinter 允许非主线程初始化）。

显示内容：
  - AI 说的话（AI_DECISION 文本，粉色标签=角色名）
  - 你语音说过去的话（USER_TEXT / Unity 输入，白色标签"你"）

特性：
  - 最近 N 条（默认 3 条），每条显示 TTL 秒后自动消失
  - 窗口可拖动（拖动即移动，松开保存位置到 config.yaml）
  - push() 线程安全：任何线程调用都会在 tkinter 主循环里消费

注意：全屏独占（Exclusive Fullscreen）游戏会盖住一切悬浮层；游戏用无边框/
窗口化时才能正常显示。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 透明色：窗口背景使用此颜色时该像素完全透明，露出真实游戏画面。
TRANS_COLOR = "#010203"

# 卡片样式
CARD_BG = "#14161a"          # 深色卡片底（不透明，游戏画面里易读）
CARD_OUTLINE = "#ff6b9d"     # 粉色描边
AI_TAG_COLOR = "#ff9ecb"     # AI 标签粉色
USER_TAG_COLOR = "#a5d8ff"   # 用户标签浅蓝
TEXT_COLOR = "#ffffff"


class ChatOverlay:
    """透明文本框悬浮层：显示 AI 的话 + 用户语音文字。

    get_state() → dict: {position: [x, y] | None, max_items: int, ttl: float,
                         font_size: int}（初始位置/显示参数）
    on_save(position) → None: 拖动结束保存位置
    push(kind, text): 线程安全，kind ∈ {"ai", "user"}
    """

    def __init__(self, root: tk.Tk,
                 get_state: Callable[[], dict],
                 on_save: Callable[[list], None],
                 stop_event: Optional[threading.Event] = None,
                 char_name: str = "小薇",
                 on_privacy: Optional[Callable[[], None]] = None,
                 get_capacity: Optional[Callable[[], dict]] = None) -> None:
        self._root = root
        self._get_state = get_state
        self._on_save = on_save
        self._stop_event = stop_event
        self._char_name = char_name
        self._on_privacy = on_privacy
        self._get_capacity = get_capacity
        # 云端双工容量（游戏画面上常驻显示：是否沾满、剩几个端口）
        self._cap_text = "云端双工容量读取中…"
        self._cap_color = "#c9a227"   # 琥珀=未知
        self._cap_last = 0.0

        st = self._fetch_state()
        self._max_items = max(1, int(st.get("max_items") or 3))
        self._ttl = max(2.0, float(st.get("ttl") or 8.0))
        self._font_size = max(11, int(st.get("font_size") or 16))

        self._msgs: list[tuple[str, str, float]] = []  # (kind, text, ts)
        self._queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._drag: Optional[tuple[int, int]] = None
        self._closing: bool = False

        # 窗口尺寸：宽度固定 640，高度 = 底部状态栏 + 消息区
        self._w = 640
        self._status_h = 20
        line_h = self._font_size + 16
        self._h = self._status_h + self._max_items * line_h + 16

        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        pos = st.get("position") or [None, None]
        try:
            x = int(pos[0]) if pos and pos[0] is not None else max(0, (sw - self._w) // 2)
            y = int(pos[1]) if pos and pos[1] is not None else max(0, sh - self._h - 60)
        except Exception:  # noqa: BLE001
            x, y = max(0, (sw - self._w) // 2), max(0, sh - self._h - 60)
        # 钳制到屏幕内
        x = max(0, min(sw - 40, x))
        y = max(0, min(sh - 40, y))
        self._pos = [x, y]

        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg=TRANS_COLOR)
        self._root.attributes("-transparentcolor", TRANS_COLOR)
        self._root.geometry(f"{self._w}x{self._h}+{x}+{y}")

        self._canvas = tk.Canvas(self._root, bg=TRANS_COLOR, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)

        # 拖动窗口（整张卡片可拖，点击透明区不响应）
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._root.bind_all("<Escape>", lambda e: self.close())
        # 双击卡片：切换隐私模式（暂停/恢复屏幕与音频感知，参照 pub-local-jarvis）
        if on_privacy is not None:
            self._canvas.bind("<Double-Button-1>", lambda e: self._toggle_privacy())

        self._draw()
        self._root.update_idletasks()
        try:
            self._root.focus_force()
        except Exception:  # noqa: BLE001
            pass
        # 100ms 消费外部推送 + 刷新
        self._root.after(100, self._poll)

    # ======== 状态 ========
    def _fetch_state(self) -> dict:
        try:
            return self._get_state() or {}
        except Exception:  # noqa: BLE001
            logger.debug("获取悬浮层状态失败", exc_info=True)
            return {}

    # ======== 对外推送（线程安全） ========
    def push(self, kind: str, text: str) -> None:
        text = (text or "").strip()
        if not text or self._closing:
            return
        self._queue.put((kind, text[:200]))

    # ======== 绘制 ========
    def _round_rect(self, x0: float, y0: float, x1: float, y1: float,
                    r: float, **kw) -> None:
        """圆角矩形：优先 tk 8.6 create_round_rectangle，旧版回退直角。"""
        c = self._canvas
        try:
            c.create_round_rectangle(x0, y0, x1, y1, r, **kw)
        except Exception:  # noqa: BLE001
            c.create_rectangle(x0, y0, x1, y1, **kw)

    def _draw(self) -> None:
        c = self._canvas
        W, H = self._w, self._h
        c.delete("all")
        try:
            f = tkfont.Font(root=self._root, family="Microsoft YaHei",
                            size=self._font_size, weight="bold")
            tag_f = tkfont.Font(root=self._root, family="Microsoft YaHei",
                                size=max(10, self._font_size - 4), weight="bold")
        except Exception:  # noqa: BLE001
            f = None
            tag_f = None

        now = time.time()
        # 过滤过期消息
        self._msgs = [(k, t, ts) for k, t, ts in self._msgs if now - ts <= self._ttl]
        # 最多显示最近 max_items 条（新的在下）
        shown = self._msgs[-self._max_items:]

        # 底部常驻：云端双工容量状态栏（即使用户没说话也始终可见）
        foot_y0 = H - self._status_h
        self._round_rect(2, foot_y0, W - 2, H - 2, 10,
                         fill="#0e1014", outline=self._cap_color, width=1)
        if tag_f is not None:
            c.create_text(W // 2, foot_y0 + self._status_h // 2, anchor="center",
                          text=self._cap_text, fill=self._cap_color, font=tag_f)

        if not shown:
            # 空状态：不画消息卡片（整个窗口其余区域透明，不打扰游戏画面）
            return

        # 消息卡片底（顶部区域，底部留 4px 间距给状态栏）
        card_bot = foot_y0 - 4
        self._round_rect(2, 2, W - 2, card_bot, 14,
                         fill=CARD_BG, outline=CARD_OUTLINE, width=2)

        line_h = self._font_size + 16
        y = 16
        for kind, text, _ts in shown:
            tag = self._char_name if kind == "ai" else "你"
            tag_color = AI_TAG_COLOR if kind == "ai" else USER_TAG_COLOR
            # 时间标签（HH:MM:SS）：用于核对"这句是哪秒说的 / 是否切场景后才冒出来"
            hhmmss = time.strftime("%H:%M:%S", time.localtime(_ts)) if _ts else ""
            if f is not None and tag_f is not None:
                # 标签 + 文本
                label = f"{tag}："
                lw = tag_f.measure(label)
                c.create_text(16, y, anchor="w", text=label, fill=tag_color,
                              font=tag_f)
                # 文本可能超宽：截断显示（保持单行，避免撑高窗口）；右侧预留时间戳宽度
                avail = W - 16 - lw - 72
                txt = text
                while f.measure(txt) > avail and len(txt) > 8:
                    txt = txt[:-1]
                if txt != text:
                    txt = txt[:-1] + "…"
                c.create_text(16 + lw, y, anchor="w", text=txt, fill=TEXT_COLOR,
                              font=f)
                if hhmmss:
                    c.create_text(W - 12, y, anchor="e", text=hhmmss,
                                  fill="#8a93a3", font=tag_f)
            else:
                c.create_text(16, y, anchor="w", text=f"{tag}：{text}",
                              fill=tag_color, font=("Microsoft YaHei", 14, "bold"))
            y += line_h

    # ======== 容量格式化（与前端 Backend.capacityBadge 语义一致） ========
    def _fmt_capacity(self, c: dict) -> None:
        palette = {"cap-ok": "#1aa260", "cap-full": "#ff5d5d",
                   "cap-local": "#2dd4ff", "cap-unknown": "#c9a227"}
        if not c or c.get("success") is False:
            text, cls = "容量未知 · 后端未连接", "cap-unknown"
        elif c.get("local_omni_active"):
            text, cls = "本地双工 · 零云占用（不占云端端口）", "cap-local"
        elif not c.get("reachable"):
            text, cls = "云端网关不可达 · 容量未知", "cap-unknown"
        else:
            total = int(c.get("total_workers") or 0)
            idle = int(c.get("idle_workers") or 0)
            q = int(c.get("queue_length") or 0)
            if c.get("is_full"):
                text, cls = f"云端双工已满 · 排队 {q} 人（共 {total} 端口）", "cap-full"
            else:
                text, cls = f"云端双工 {idle}/{total} 空闲 · 排队 {q} 人", "cap-ok"
        self._cap_text = text
        self._cap_color = palette.get(cls, "#c9a227")

    # ======== 拖动窗口 ========
    def _on_press(self, e: tk.Event) -> None:
        # 空白透明区域不响应（只在卡片可见区域内拖动）
        self._drag = (e.x, e.y)

    def _on_motion(self, e: tk.Event) -> None:
        if self._drag is None:
            return
        dx = e.x - self._drag[0]
        dy = e.y - self._drag[1]
        x = max(0, self._pos[0] + dx)
        y = max(0, self._pos[1] + dy)
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = min(sw - 40, x)
        y = min(sh - 40, y)
        self._pos = [x, y]
        self._root.geometry(f"{self._w}x{self._h}+{x}+{y}")

    def _on_release(self, e: tk.Event) -> None:
        if self._drag is not None:
            self.save()
        self._drag = None

    def save(self) -> None:
        try:
            self._on_save(list(self._pos))
            logger.info("对话悬浮层位置已保存: %s", self._pos)
        except Exception:  # noqa: BLE001
            logger.exception("对话悬浮层位置保存失败")

    def _toggle_privacy(self) -> None:
        """双击卡片：切换隐私模式（暂停/恢复屏幕与音频感知）。"""
        if self._on_privacy is None:
            return
        try:
            self._on_privacy()
        except Exception:  # noqa: BLE001
            logger.exception("切换隐私模式失败")
        # 悬浮层推送一条即时反馈（AI 视角：告知当前状态）
        try:
            from ..perception.privacy import privacy_mode
            on = privacy_mode()
            self.push("ai", "隐私模式已开启，我不再看屏幕听声音了" if on
                      else "隐私模式已关闭，我继续陪着你啦")
        except Exception:  # noqa: BLE001
            pass

    # ======== 轮询 ========
    def _poll(self) -> None:
        if self._closing:
            return
        if self._stop_event is not None and self._stop_event.is_set():
            self.close()
            return
        changed = False
        try:
            while True:
                kind, text = self._queue.get_nowait()
                self._msgs.append((kind, text, time.time()))
                changed = True
        except queue.Empty:
            pass
        # 定期刷新（清过期消息）
        now = time.time()
        before = len(self._msgs)
        self._msgs = [(k, t, ts) for k, t, ts in self._msgs
                      if now - ts <= self._ttl]
        if len(self._msgs) != before:
            changed = True
        # 云端双工容量：每 ~10s 拉一次（不阻塞主循环）
        if self._get_capacity is not None and now - self._cap_last >= 10.0:
            self._cap_last = now
            try:
                cap = self._get_capacity() or {}
                self._fmt_capacity(cap)
                changed = True
            except Exception:  # noqa: BLE001
                logger.debug("悬浮层获取双工容量失败", exc_info=True)
        if changed:
            self._draw()
        self._root.after(100, self._poll)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._root.destroy()
        except Exception:  # noqa: BLE001
            pass


def run_chat_overlay(get_state: Callable[[], dict],
                     on_save: Callable[[list], None],
                     stop_event: Optional[threading.Event] = None,
                     char_name: str = "小薇",
                     on_ready: Optional[Callable[["ChatOverlay"], None]] = None,
                     on_privacy: Optional[Callable[[], None]] = None,
                     get_capacity: Optional[Callable[[], dict]] = None) -> None:
    """在调用线程里运行对话悬浮层（阻塞直到窗口关闭）。

    从后台线程调用即可（Windows 上 tkinter 允许非主线程初始化）。
    on_ready(overlay) 在窗口创建后、进入事件循环前回调：调用方在此拿到实例，
    之后可随时调 push(kind, text)（线程安全）。
    on_privacy() 在窗口内双击卡片时调用（切换隐私模式，由调用方实现）。
    """
    root = tk.Tk()
    root.title("对话悬浮层")
    overlay = ChatOverlay(root, get_state, on_save, stop_event, char_name,
                          on_privacy, get_capacity)
    if on_ready is not None:
        try:
            on_ready(overlay)
        except Exception:  # noqa: BLE001
            logger.exception("对话悬浮层 on_ready 回调异常")
    try:
        root.mainloop()
    finally:
        overlay.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    def _fake_state() -> dict:
        return {"position": None, "max_items": 3, "ttl": 8.0, "font_size": 16}

    def _fake_save(pos: list) -> None:
        print("保存位置:", pos)

    holder = {}

    def _ready(ov: ChatOverlay) -> None:
        holder["ov"] = ov
    run_chat_overlay(_fake_state, _fake_save, on_ready=_ready)

    # 自测：开线程推送几条消息
    def _feed() -> None:
        import time as _t
        _t.sleep(1.0)
        ov = holder.get("ov")
        if ov is None:
            return
        ov.push("ai", "这波撤出来了，干得漂亮！")
        _t.sleep(2.0)
        ov.push("user", "小薇，帮我看着点右边")
        _t.sleep(2.0)
        ov.push("ai", "右边拐角有人，小心！")
    threading.Thread(target=_feed, daemon=True).start()
