"""桌宠位置持久化测试（src/ui/pet_state.py）。

为什么值得测：这模块写错不会崩，只会"人跑到屏幕外"或"每次开机都回右下角"，
两种都是用户能直接感知、但日志里看不出来的退化。

跑法：.venv/Scripts/python.exe src/tests/test_pet_state.py
（测试会把 data/pet_state.json 临时挪开，跑完还原）
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui import pet_state  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _shift(path: str) -> None:
    """把真实存档挪到 .bak，避免测试污染用户数据。"""
    if os.path.exists(path):
        os.replace(path, path + ".bak")


def _restore(path: str) -> None:
    if os.path.exists(path + ".bak"):
        os.replace(path + ".bak", path)


def main() -> int:
    path = pet_state.STATE_PATH
    _shift(path)
    try:
        print("[1] 无存档 → 用默认位")
        check(pet_state.load() is None, "load() 返回 None")
        dx, dy = 1263, 455
        check(pet_state.restore_logical(420, 540, (dx, dy)) == (dx, dy),
              f"回落默认位 ({dx},{dy})")

        print("[2] 存了再取 → 原样回来")
        pet_state.save_logical(1000, 300)
        check(pet_state.load() is not None, "load() 有内容")
        rx, ry = pet_state.restore_logical(420, 540, (dx, dy))
        check((rx, ry) == (1000, 300), f"回到存盘位置 (1000,300)，实际 ({rx},{ry})")

        print("[3] 越界 → 夹回可视区（换屏/改分辨率场景）")
        pet_state.save_logical(90000, 90000)
        rx, ry = pet_state.restore_logical(420, 540, (dx, dy))
        areas = pet_state.logical_work_areas()
        in_some = any(a[0] <= rx <= max(a[0], a[2] - 420)
                      and a[1] <= ry <= max(a[1], a[3] - 540) for a in areas)
        check(in_some, f"({rx},{ry}) 落在某块屏的可视范围内")

        print("[3b] 存档里比例越界 → 恢复时必须夹回屏内")
        areas_now = pet_state.logical_work_areas()
        wa0 = areas_now[0]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rx": 5.0, "ry": -3.0,
                       "wa": {"left": wa0[0], "top": wa0[1],
                              "right": wa0[2], "bottom": wa0[3]},
                       "screen_count": len(areas_now)}, fh)
        rx, ry = pet_state.restore_logical(420, 540, (dx, dy))
        lo_x, lo_y = wa0[0], wa0[1]
        hi_x, hi_y = max(wa0[0], wa0[2] - 420), max(wa0[1], wa0[3] - 540)
        check(lo_x <= rx <= hi_x and lo_y <= ry <= hi_y,
              f"({rx},{ry}) 被夹进 [{lo_x},{hi_x}]×[{lo_y},{hi_y}]")

        print("[4] 存档损坏 → 回落默认位，不抛异常")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{坏了")
        check(pet_state.restore_logical(420, 540, (dx, dy)) == (dx, dy),
              "损坏存档 → 默认位")

        print("[5] 存档缺字段 → 回落默认位")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rx": None}, fh)
        check(pet_state.restore_logical(420, 540, (dx, dy)) == (dx, dy),
              "字段缺失 → 默认位")

        print("[6] 物理↔逻辑换算自洽")
        scale = pet_state.screen_scale()
        check(scale >= 1.0, f"缩放比合法：{scale}")
        pet_state.save_physical(int(800 * scale), int(400 * scale))
        rx, ry = pet_state.restore_logical(420, 540, (dx, dy))
        check(abs(rx - 800) <= 1 and abs(ry - 400) <= 1,
              f"物理存 → 逻辑取 回到 (800,400)，实际 ({rx},{ry})")

        print("[7] 还原口径同源：调用方不得拿「实际窗口尺寸」去还原")
        # 本模块的注释写死了这条规矩：存用 PET_W/PET_H，取也必须用同一组数。
        # pet_window 曾经传实际尺寸（424×544），分母差 4px → 边缘处几像素漂移，
        # 而且只在换分辨率后才显形（2026-10-18 质检 P2）。
        pw = os.path.join(ROOT, "src", "ui", "pet_window.py")
        src = ""
        try:
            with open(pw, encoding="utf-8") as fh:
                src = fh.read()
        except OSError as e:  # noqa: BLE001
            check(False, f"读不到 pet_window.py：{e}")
        if src:
            check("restore_logical(_lw" not in src and "restore_logical(_lh" not in src,
                  "pet_window 没有用实际窗口尺寸调用 restore_logical")
            check("restore_logical(_ps.PET_W, _ps.PET_H" in src,
                  "pet_window 用的是 PET_W/PET_H（与存盘同源）")
    finally:
        if os.path.exists(path):
            os.remove(path)
        _restore(path)

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        return 1
    print("PASS：桌宠位置持久化成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
