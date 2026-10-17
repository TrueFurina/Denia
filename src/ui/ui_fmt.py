# -*- coding: utf-8 -*-
"""通用格式化工具（自 webapp.py 抽出，2026-10-17）。

这些函数没有任何模块级可变状态，纯输入输出，抽出的目的是让 webapp.py
不再承担"什么都有"的角色。原实现逐字迁移，未改逻辑。
"""
from __future__ import annotations

import os
import re
import time

__all__ = [
    "_path_has_non_ascii", "register_local_img_dir", "_img_data_url",
    "_fmt_ts", "_fmt_file_ts", "_status_done_summary",
]


def _path_has_non_ascii(p: str) -> bool:
    """路径是否含非 ASCII 字符（中文/日文/韩文/特殊符号等）。

    C++ 引擎（llama-server / Comni worker）用 fopen 打开模型文件，多字节
    中文路径在无 manifest 的进程里会乱码 → failed to open GGUF → 引擎起不来、
    双工模型永远挂不上。任何非 ASCII 都视为不可加载，要求纯英文/数字路径。
    """
    if not p:
        return False
    try:
        p.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _img_data_url(path: str) -> str:
    """本地图片 → base64 data URL（file:// 协议下前端无法直接访问本地文件）。"""
    import base64
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _fmt_ts(ts: float) -> str:
    """时间戳 → 'YYYY-MM-DD HH:MM:SS'（本地时区）。"""
    try:
        d = time.localtime(float(ts or 0))
        return time.strftime("%Y-%m-%d %H:%M:%S", d)
    except Exception:  # noqa: BLE001
        return "--"


def _fmt_file_ts(ts: float) -> str:
    """时间戳 → 'YYYYMMDD_HHMMSS'（用于文件名，无特殊字符）。"""
    try:
        d = time.localtime(float(ts or 0))
        return time.strftime("%Y%m%d_%H%M%S", d)
    except Exception:  # noqa: BLE001
        return "00000000_000000"


def _status_done_summary(meta: dict, fallback: str = "") -> str:
    """把已完成的状态卡压成一行历史摘要（刷新后仍可读）。

    例：操作卡 → 「✓ 已打开 汽水音乐 · 查看窗口 等 2 项」；观看卡 → 「✓ 看完 4 帧」。
    """
    steps = [s for s in (meta.get("steps") or []) if isinstance(s, dict)]
    # 【2026-10-05】观看卡优先判定（此前放在 steps 分支之后，steps 非空时永远走不到）
    wk = meta.get("watch") or {}
    if wk or meta.get("phase") == "watch_done":
        n = len(steps)
        return "✓ 视频已看完（%d 步）" % n if n else "✓ 视频已看完"
    if steps:
        # 优先用 step.result（工具真实返回，如「已通过开始菜单启动：汽水音乐」），
        # 其次才是 label/tool —— 避免摘要退化成 "✓ open_app" 这种英文工具名。
        def _pick(s):
            r = str(s.get("result") or "").strip()
            if r:
                return r
            lb = str(s.get("label") or "").strip()
            if lb and not re.fullmatch(r"[A-Za-z0-9_.]+", lb):
                return lb
            return ""
        done_steps = [s for s in steps
                      if s.get("state") in ("done", "err") and _pick(s)]
        if done_steps:
            head = "✓ " + _pick(done_steps[0])
            if len(done_steps) > 1:
                head += " 等 %d 项" % len(done_steps)
            return head
    if meta.get("error"):
        return "✗ 操作未完成"
    return "✓ " + (fallback.replace("⚙️ ", "").strip() or "已完成")
