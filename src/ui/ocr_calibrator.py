"""OCR 区域校准悬浮窗：把识别区域画到真实屏幕上，拖动即调。

游戏里的击杀播报、回合计数、结算横幅等文字位置随分辨率/窗口布局变化，
OCR 扫不到击杀往往就是识别区域没罩住游戏里实际显示文字的位置。这个悬浮窗
把【所有】识别区域色块直接叠加在真实屏幕上（游戏/腾讯会议窗口之上），
拖动调整、保存即生效，不用重启、不用猜坐标。

支持多个自定义区域（每个区域有名字+用途 kill/page/settle/hud/general，
由 GameOcrMonitor 扫描并按其用途判断），可在悬浮窗里新增/删除/改用途。

操作：
  - 点击色块              = 选中（高亮黄边）
  - 拖动色块内部          = 移动识别区域
  - 拖动右下角白点        = 缩放识别区域
  - 按 S                  = 保存到 config.yaml 并热生效（松开鼠标也会自动保存）
  - 按 R                  = 恢复默认区域
  - 按 Del                = 删除选中的区域
  - 按 P                  = 循环切换选中区域的用途（kill/page/settle/hud/general）
  - 按 Esc                = 关闭悬浮窗
  - 顶栏「＋新增」        = 弹窗输入名字新增区域（默认屏幕中央，拖动调整后保存）

实现：tkinter 无边框置顶窗口 + transparentcolor 透明背景（Windows），区域色块
用 stipple 点阵填充、露出真实游戏画面。在独立线程里跑 mainloop（Windows 上
tkinter 可在非主线程初始化）。

注意：全屏独占（Exclusive Fullscreen）游戏会盖住一切悬浮层，色块看不到；
游戏用无边框/窗口化时才能正常显示。
"""
from __future__ import annotations

import copy
import logging
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import simpledialog
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 透明色：窗口背景使用此颜色时该像素完全透明，露出真实游戏画面。
# 选一个画面里几乎不可能出现的纯色，避免与色块/文字混淆。
TRANS_COLOR = "#010203"

# 默认区域的中文标签与颜色（绘制顺序即堆叠顺序，后画在上层）
REGION_META: list[tuple[str, str, str]] = [
    ("top_hud", "顶部·回合/倒计时", "#4dabf7"),
    ("bottom_hud", "底部·击杀/结算", "#ffa94d"),
    ("top_right_hud", "右上角·击杀播报", "#ff6b6b"),
]
# 默认区域 key → 颜色（保持稳定，新增区域用调色板循环）
_DEFAULT_COLORS: dict[str, str] = {k: c for k, _, c in REGION_META}
# 新增区域的颜色调色板（循环使用，与默认色不重复）
_PALETTE = ["#51cf66", "#cc5de8", "#ffd43b", "#22b8cf",
            "#f783ac", "#8ce99a", "#e599f7", "#a5d8ff",
            "#ffa8a8", "#b2f2bb", "#c0eb75", "#63e6be"]

# 用途可选值（与 game_ocr.REGION_PURPOSES 保持一致）
REGION_PURPOSES = ("kill", "kill_hint", "page", "settle", "hud", "death", "general")
_PURPOSE_LABEL = {
    "kill": "击杀播报",
    "kill_hint": "击杀提示(中下)",
    "page": "页面名",
    "settle": "结算/胜负",
    "hud": "顶部HUD",
    "death": "阵亡名片",
    "general": "通用",
}

# 底栏操作提示
HINT_TEXT = ("点击=选中  拖动=移动  右下角白点=缩放  S=保存  R=恢复默认 "
             "Del=删选中  P=改用途  Esc=关闭")
_MODE_LABEL = {
    "meeting": "手机屏幕共享（腾讯会议窗口中央）",
    "pc_game": "PC 游戏窗口",
    "none": "未识别到游戏画面（按前台窗口显示，切到游戏后自动更新）",
}


def _parse_meta(meta: Any) -> tuple[str, Optional[list]]:
    """解析区域配置值 → (purpose, rect)。兼容 {purpose, rect} 与 [x,y,w,h] 两种格式。"""
    if isinstance(meta, dict):
        purpose = str(meta.get("purpose") or "general")
        if purpose not in REGION_PURPOSES:
            purpose = "general"
        return purpose, meta.get("rect")
    return "general", meta


class OcrCalibratorOverlay:
    """透明悬浮窗：显示并拖拽 OCR 识别区域（支持多区域自定义）。

    get_state() → dict：mode / reference / regions(含purpose) / defaults / monitor_bounds
    on_save(regions) → None：保存 {name: {purpose, rect}} 并热应用到 GameOcrMonitor
    """

    def __init__(self, root: tk.Tk, get_state: Callable[[], dict],
                 on_save: Callable[[dict], None],
                 stop_event: Optional[threading.Event] = None) -> None:
        self._root = root
        self._get_state = get_state
        self._on_save = on_save
        self._stop_event = stop_event  # 外部关闭信号（UI 开关关掉时 set，等效 Esc）

        self._rel: dict[str, list[float]] = {}   # 区域名 → 相对坐标 [x,y,w,h]
        self._purpose: dict[str, str] = {}       # 区域名 → 用途
        self._color: dict[str, str] = {}         # 区域名 → 色块颜色
        self._defaults: dict[str, list[float]] = {}   # 默认区域相对坐标（R 恢复）
        self._default_purpose: dict[str, str] = {}    # 默认区域用途
        self._ref: Optional[tuple] = None        # 参照矩形（屏幕坐标）
        self._mode: str = ""
        self._monitor_bounds: Optional[tuple] = None
        self._origin: tuple[int, int] = (0, 0)   # 窗口原点（屏幕坐标 → 画布坐标偏移）
        self._drag: Optional[dict] = None        # 拖拽状态 {key, mode, off_x, off_y}
        self._selected: Optional[str] = None     # 当前选中的区域（高亮）
        self._btns: dict[str, tuple] = {}        # 顶栏按钮矩形（画布坐标）
        self._dirty: bool = False                # 有未保存的拖动
        self._loaded: bool = False               # 是否已从服务端载入初始区域
        self._closing: bool = False
        self._hint_suffix: str = ""              # 临时状态文字（已保存✓ 等）

        # 窗口覆盖 OCR 所在监视器
        self._apply_state(self._fetch_state())
        left, top = 0, 0
        w = self._root.winfo_screenwidth()
        h = self._root.winfo_screenheight()
        if self._monitor_bounds:
            left, top, w, h = self._monitor_bounds
        self._origin = (left, top)

        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg=TRANS_COLOR)
        self._root.attributes("-transparentcolor", TRANS_COLOR)
        self._root.geometry(f"{w}x{h}{left:+d}{top:+d}")

        self._canvas = tk.Canvas(self._root, bg=TRANS_COLOR, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)

        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._root.bind_all("<Escape>", lambda e: self.close())
        self._root.bind_all("<KeyPress-s>", lambda e: self.save())
        self._root.bind_all("<KeyPress-S>", lambda e: self.save())
        self._root.bind_all("<KeyPress-r>", lambda e: self.reset())
        self._root.bind_all("<KeyPress-R>", lambda e: self.reset())
        self._root.bind_all("<Delete>", lambda e: self.delete_selected())
        self._root.bind_all("<KeyPress-p>", lambda e: self.cycle_purpose())
        self._root.bind_all("<KeyPress-P>", lambda e: self.cycle_purpose())

        self._draw()
        self._root.update_idletasks()
        try:
            self._root.focus_force()
        except Exception:  # noqa: BLE001
            pass
        # 每 1s 轮询服务端状态：前台切到游戏/会议窗口时自动更新参照矩形
        self._root.after(1000, self._poll)

    # ======== 状态 ========
    def _fetch_state(self) -> dict:
        try:
            return self._get_state() or {}
        except Exception:  # noqa: BLE001
            logger.debug("获取 OCR 校准状态失败", exc_info=True)
            return {}

    def _apply_state(self, st: dict) -> None:
        defaults = st.get("defaults") or {}
        self._defaults = {}
        self._default_purpose = {}
        for k, v in defaults.items():
            purpose, rect = _parse_meta(v)
            self._defaults[k] = [float(x) for x in (rect or [0.0, 0.0, 1.0, 1.0])]
            self._default_purpose[k] = purpose
        self._mode = st.get("mode") or ""
        self._ref = st.get("reference") or None
        self._monitor_bounds = st.get("monitor_bounds") or self._monitor_bounds
        if not self._loaded:
            self._loaded = True
            regs = st.get("regions") or {}
            for key, meta in regs.items():
                purpose, rect = _parse_meta(meta)
                self._rel[key] = [float(x) for x in (rect or [0.0, 0.0, 1.0, 1.0])]
                self._purpose[key] = purpose
                self._color[key] = self._assign_color(key)

    def _sync_region_keys(self, regs: dict) -> None:
        """同步外部（WebUI）新增/删除的区域：新增的显示出来，删除的移除。

        注意：**未保存的本地新增区域（悬浮窗「＋新增」刚加的、还在拖动调位置的）
        不会被删除**——`self._dirty` 为 True 时跳过删除段，否则 1s 轮询会把
        还没拖好的新区域当"服务器上没有"删掉，导致"新加区块无法拖动白点缩放"
        （拖到一半区域消失、拖了个寂寞）。保存后 dirty=False，外部删除才生效。
        """
        changed = False
        for key, meta in regs.items():
            if key not in self._rel:
                purpose, rect = _parse_meta(meta)
                self._rel[key] = [float(x) for x in (rect or [0.0, 0.0, 1.0, 1.0])]
                self._purpose[key] = purpose
                self._color[key] = self._assign_color(key)
                changed = True
        if not self._dirty:
            for key in list(self._rel):
                if key not in regs:
                    del self._rel[key]
                    self._purpose.pop(key, None)
                    self._color.pop(key, None)
                    if self._selected == key:
                        self._selected = None
                    changed = True
        if changed:
            self._draw()

    def _assign_color(self, key: str) -> str:
        """给区域分配色块颜色：默认区域用固定色，新增区域循环用调色板。"""
        if key in _DEFAULT_COLORS:
            return _DEFAULT_COLORS[key]
        used = set(self._color.values())
        for c in _PALETTE:
            if c not in used:
                return c
        return _PALETTE[len(self._color) % len(_PALETTE)]

    # ======== 坐标换算 ========
    def _region_screen(self, key: str) -> tuple[float, float, float, float]:
        """相对坐标 → 屏幕坐标 (x0, y0, x1, y1)。"""
        rx, ry, rw, rh = self._ref or (0, 0, 1, 1)
        rel = self._rel.get(key, [0.0, 0.0, 1.0, 1.0])
        x0 = rx + rw * rel[0]
        y0 = ry + rh * rel[1]
        x1 = rx + rw * (rel[0] + rel[2])
        y1 = ry + rh * (rel[1] + rel[3])
        return x0, y0, x1, y1

    def _region_canvas(self, key: str) -> tuple[float, float, float, float]:
        """相对坐标 → 画布坐标（窗口原点偏移）。"""
        x0, y0, x1, y1 = self._region_screen(key)
        ox, oy = self._origin
        return x0 - ox, y0 - oy, x1 - ox, y1 - oy

    def _rel_from_screen(self, key: str) -> list[float]:
        """把屏幕坐标（存于 self._rel）规范化为相对坐标并钳制到 0~1。"""
        x0, y0, x1, y1 = self._region_screen(key)
        rx, ry, rw, rh = self._ref or (1, 1, 1, 1)
        nw = max(0.02, min(1.0, (x1 - x0) / rw))
        nh = max(0.02, min(1.0, (y1 - y0) / rh))
        nx = max(0.0, min(1.0 - nw, (x0 - rx) / rw))
        ny = max(0.0, min(1.0 - nh, (y0 - ry) / rh))
        return [round(nx, 4), round(ny, 4), round(nw, 4), round(nh, 4)]

    # ======== 绘制 ========
    def _draw(self) -> None:
        c = self._canvas
        W = max(1, self._root.winfo_width())
        H = max(1, self._root.winfo_height())
        c.delete("all")
        try:
            f = tkfont.Font(root=self._root, family="Microsoft YaHei", size=9)
        except Exception:  # noqa: BLE001
            f = None

        # 顶栏标题（含当前识别模式 + 选中区域）
        mode_txt = _MODE_LABEL.get(self._mode, "")
        sel_txt = ""
        if self._selected:
            pur = self._purpose.get(self._selected, "general")
            sel_txt = f"  选中: {self._selected}（{_PURPOSE_LABEL.get(pur, pur)}）"
        c.create_rectangle(0, 0, W, 34, fill="#14161a", outline="")
        c.create_text(12, 17, anchor="w", text=f"OCR 区域校准 · {mode_txt}{sel_txt}",
                      fill="#ffffff", font=("Microsoft YaHei", 12, "bold"))

        # 顶栏右侧按钮（＋新增 / 删选中 / 用途▸）
        self._btns = {}
        bx = W - 10
        for bname, label in (("purpose", "用途▸"), ("del", "删选中"), ("add", "＋新增")):
            bw = (f.measure(label) + 24) if f else (24 + 12 * len(label))
            x0, y0, x1 = bx - bw, 7, bx
            y1 = y0 + 20
            if bname == "del" and self._selected is None:
                fill, txt = "#1f2227", "#6b7280"  # 未选中时置灰
            else:
                fill, txt = "#2b2f36", "#ffd43b"
            c.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#555a63", width=1)
            c.create_text((x0 + x1) // 2, y0 + 10, text=label, fill=txt,
                          font=("Microsoft YaHei", 10))
            self._btns[bname] = (x0, y0, x1, y1)
            bx = x0 - 6

        # 参照矩形（主抓屏画面范围，白色虚线框）
        if self._ref:
            x0 = self._ref[0] - self._origin[0]
            y0 = self._ref[1] - self._origin[1]
            x1 = x0 + self._ref[2]
            y1 = y0 + self._ref[3]
            c.create_rectangle(x0, y0, x1, y1, outline="#ffffff", width=2, dash=(6, 4))

        # 区域色块（stipple 点阵填充，露出真实游戏画面）
        for key, rel in self._rel.items():
            x0, y0, x1, y1 = self._region_canvas(key)
            if x1 <= x0 or y1 <= y0:
                continue
            color = self._color.get(key, "#4dabf7")
            sel = (key == self._selected)
            outline = "#ffd43b" if sel else "#ffffff"
            width = 3 if sel else 2
            c.create_rectangle(x0, y0, x1, y1, fill=color, stipple="gray50",
                               outline=outline, width=width)
            # 标签：深色底 + 白字（含用途）
            label = key
            if key in {k for k, _, _ in REGION_META}:
                label = dict((k, l) for k, l, _ in REGION_META).get(key, key)
            pur = self._purpose.get(key, "general")
            label = f"{label}·{_PURPOSE_LABEL.get(pur, pur)}"
            lw = (f.measure(label) + 10) if f else (10 + 12 * len(label))
            c.create_rectangle(x0 + 2, y0 + 2, x0 + 2 + lw, y0 + 20, fill="#14161a", outline="")
            c.create_text(x0 + 4, y0 + 4, anchor="nw", text=label,
                          fill="#ffffff", font=("Microsoft YaHei", 9))
            # 右下角缩放手柄
            c.create_rectangle(x1 - 12, y1 - 12, x1 + 2, y1 + 2, fill="#ffffff", outline="#000000")

        # 底栏操作提示
        bar_h = 36
        c.create_rectangle(0, H - bar_h, W, H, fill="#14161a", outline="")
        hint = HINT_TEXT + (f"   {self._hint_suffix}" if self._hint_suffix else "")
        c.create_text(W // 2, H - bar_h // 2, text=hint,
                      fill="#ffd43b", font=("Microsoft YaHei", 10))

    # ======== 交互 ========
    def _on_press(self, e: tk.Event) -> None:
        # 顶栏按钮（画布坐标命中）
        for bname, (x0, y0, x1, y1) in self._btns.items():
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self._on_btn(bname)
                return
        if not self._ref:
            return
        try:
            self._root.focus_force()  # 无边框窗口点按后保证能收到 S/R/Esc 键盘事件
        except Exception:  # noqa: BLE001
            pass
        sx = e.x + self._origin[0]
        sy = e.y + self._origin[1]
        # 先命中缩放手柄（右下角），再命中色块本体（后画的在上层，先命中）
        for key in reversed(list(self._rel)):
            x0, y0, x1, y1 = self._region_screen(key)
            if x1 - 14 <= sx <= x1 + 4 and y1 - 14 <= sy <= y1 + 4:
                self._selected = key
                self._drag = {"key": key, "mode": "resize"}
                self._draw()
                return
        for key in reversed(list(self._rel)):
            x0, y0, x1, y1 = self._region_screen(key)
            if x0 <= sx <= x1 and y0 <= sy <= y1:
                self._selected = key
                self._drag = {"key": key, "mode": "move",
                              "off_x": sx - x0, "off_y": sy - y0}
                self._draw()
                return
        self._selected = None
        self._drag = None
        self._draw()

    def _on_btn(self, name: str) -> None:
        if name == "add":
            self.add_region()
        elif name == "del":
            self.delete_selected()
        elif name == "purpose":
            self.cycle_purpose()

    def _on_motion(self, e: tk.Event) -> None:
        if not self._drag or not self._ref:
            return
        key = self._drag["key"]
        sx = e.x + self._origin[0]
        sy = e.y + self._origin[1]
        rx, ry, rw, rh = self._ref
        rel = list(self._rel.get(key, [0.0, 0.0, 1.0, 1.0]))
        wpx = rel[2] * rw
        hpx = rel[3] * rh
        if self._drag["mode"] == "move":
            nx = sx - self._drag["off_x"]
            ny = sy - self._drag["off_y"]
            x0 = max(rx, min(rx + rw - wpx, nx))
            y0 = max(ry, min(ry + rh - hpx, ny))
            rel[0] = (x0 - rx) / rw
            rel[1] = (y0 - ry) / rh
        else:  # resize：以区域左上角为锚点，右下角跟随鼠标
            x1 = max(rx + 4, min(rx + rw, sx))
            y1 = max(ry + 4, min(ry + rh, sy))
            rel[2] = max(0.03, (x1 - (rx + rel[0] * rw)) / rw)
            rel[3] = max(0.03, (y1 - (ry + rel[1] * rh)) / rh)
        self._rel[key] = rel
        self._dirty = True
        self._draw()

    def _on_release(self, e: tk.Event) -> None:
        if self._drag and self._dirty:
            self.save()
        self._drag = None

    # ======== 动作 ========
    def add_region(self) -> None:
        """弹窗输入区域名，新增一个默认屏幕中央的区域（拖动调整后保存）。"""
        try:
            name = simpledialog.askstring(
                "新增 OCR 区域",
                "区域名（唯一，如 kill_feed / settle / page_name）\n\n"
                "确定后屏幕上会出现新色块，拖动到目标位置、缩放大小，\n"
                "按 S 保存；按 P 可切换用途（击杀播报/页面名/结算/顶部HUD/通用）。",
                parent=self._root)
        except Exception:  # noqa: BLE001
            logger.debug("新增区域对话框异常", exc_info=True)
            name = None
        name = (name or "").strip()
        if not name:
            return
        if name in self._rel:
            self._flash(f"区域已存在: {name}")
            return
        # 默认在参照矩形中央 1/3 处，方便直接拖动微调
        self._rel[name] = [0.3333, 0.3333, 0.3334, 0.3334]
        self._purpose[name] = "general"
        self._color[name] = self._assign_color(name)
        self._selected = name
        self._dirty = True
        self.save()  # 立即保存到服务端：否则 1s 轮询的 _sync_region_keys 会把
        # 还没拖好的新区域当"服务器上没有"删掉（"无法拖动白点缩放"根因之一）
        self._draw()
        self._flash("新增区域，拖动调整后按 S 保存")

    def delete_selected(self) -> None:
        """删除选中的区域（立即保存生效）。"""
        key = self._selected
        if not key or key not in self._rel:
            self._flash("先点击选中一个区域再删除")
            return
        del self._rel[key]
        self._purpose.pop(key, None)
        self._color.pop(key, None)
        self._selected = None
        self._dirty = True
        self.save()
        self._draw()

    def cycle_purpose(self) -> None:
        """循环切换选中区域的用途（kill→page→settle→hud→general）。"""
        key = self._selected
        if not key or key not in self._rel:
            self._flash("先点击选中一个区域再改用途")
            return
        cur = self._purpose.get(key, "general")
        idx = REGION_PURPOSES.index(cur) if cur in REGION_PURPOSES else 0
        nxt = REGION_PURPOSES[(idx + 1) % len(REGION_PURPOSES)]
        self._purpose[key] = nxt
        self._dirty = True
        self.save()
        self._draw()

    def save(self) -> None:
        if not self._dirty:
            return
        data = {}
        for key in self._rel:
            data[key] = {"purpose": self._purpose.get(key, "general"),
                         "rect": self._rel_from_screen(key)}
        try:
            self._on_save(data)
            logger.info("OCR 区域已保存: %s",
                        {k: v["purpose"] for k, v in data.items()})
        except Exception:  # noqa: BLE001
            logger.exception("OCR 区域保存失败")
            self._flash("保存失败（见日志）")
            return
        self._dirty = False
        self._flash("已保存 ✓")

    def reset(self) -> None:
        self._rel = copy.deepcopy(self._defaults) if self._defaults else {}
        self._purpose = dict(self._default_purpose)
        self._selected = None
        self._dirty = True
        self.save()
        self._draw()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _flash(self, text: str) -> None:
        self._hint_suffix = text
        self._draw()

        def _clear() -> None:
            self._hint_suffix = ""
            self._draw()

        self._root.after(1500, _clear)

    # ======== 轮询 ========
    def _poll(self) -> None:
        if self._closing:
            return
        # 外部关闭信号（UI 开关关掉）→ 关闭悬浮窗，等效按 Esc
        if self._stop_event is not None and self._stop_event.is_set():
            self.close()
            return
        try:
            st = self._fetch_state()
            # 同步 WebUI 里新增/删除的区域
            self._sync_region_keys(st.get("regions") or {})
            ref = st.get("reference") or None
            mode = st.get("mode") or ""
            # 拖拽中不刷新参照，避免色块跳动
            if self._drag is None and ref and (ref != self._ref or mode != self._mode):
                self._ref = ref
                self._mode = mode
                self._draw()
        except Exception:  # noqa: BLE001
            logger.debug("OCR 校准状态轮询异常", exc_info=True)
        self._root.after(1000, self._poll)


def run_calibrator(get_state: Callable[[], dict],
                   on_save: Callable[[dict], None],
                   stop_event: Optional[threading.Event] = None) -> None:
    """在调用线程里运行校准悬浮窗（阻塞直到窗口关闭）。

    从后台线程调用即可（Windows 上 tkinter 允许非主线程初始化）。
    get_state/on_save 由调用方提供，悬浮窗通过它们读写 OCR 区域。
    stop_event 可选：被 set 时悬浮窗自动关闭（供 UI 开关关掉悬浮窗）。
    """
    root = tk.Tk()
    root.title("OCR 区域校准")
    overlay = OcrCalibratorOverlay(root, get_state, on_save, stop_event)
    try:
        root.mainloop()
    finally:
        overlay.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 自测：无运行时依赖，用假数据启动悬浮窗（方便单独调试界面）
    def _fake_state() -> dict:
        return {
            "mode": "none",
            "monitor_bounds": None,
            "reference": None,
            "regions": {
                "top_hud": {"purpose": "hud", "rect": [0.0, 0.0, 1.0, 0.14]},
                "bottom_hud": {"purpose": "settle", "rect": [0.0, 0.75, 1.0, 0.25]},
                "top_right_hud": {"purpose": "kill", "rect": [0.45, 0.0, 0.55, 0.35]},
            },
            "defaults": {
                "top_hud": {"purpose": "hud", "rect": [0.0, 0.0, 1.0, 0.14]},
                "bottom_hud": {"purpose": "settle", "rect": [0.0, 0.75, 1.0, 0.25]},
                "top_right_hud": {"purpose": "kill", "rect": [0.45, 0.0, 0.55, 0.35]},
            },
        }

    def _fake_save(regions: dict) -> None:
        print("保存区域:", regions)

    run_calibrator(_fake_state, _fake_save)
