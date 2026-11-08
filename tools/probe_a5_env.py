# -*- coding: utf-8 -*-
"""A5 四场景的**可脚本化探针**：把能自动测的测了，不能测的明确列出来。

它不宣称「A5 通过」。A5 验收卡里真正决定成败的是人眼项（白底/边缘糊、
点击热区、拖到副屏后的渲染），探针只负责把 Win32 事实和日志基线量出来，
让那 15 分钟的人工检查变成「对着一张表打勾」，而不是从头找现象。

测什么：
  · 每块显示器的矩形、工作区、是否主屏、effective DPI（混合 DPI 双屏的关键）
  · 前台窗口是否恰好铺满某块屏（全屏退场判定的输入）
  · SHQueryUserNotificationState（D3D 全屏 / 演示 / 忙碌）
  · electron.exe / pythonw.exe 进程数（A3 切窗 10 次后不许堆积）
  · launch_run*.log 的 [UI心跳] 分类计数 + 修复后是否已有长跑日志

诚实边界：本机测量 ≠ 真机验收。报告里固定列 HUMAN_ONLY，链自检非空。
特别地，若 6e18396（两阶段心跳修复）之后没有新的运行日志，报告会明确写
「修复后长跑未验」，绝不拿修复前的 run18 基线冒充修复后的结果。

登记方式：由 check_all.GATES 直接登记（非 check_*/test_* 命名）。
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import glob
import io
import json
import os
import subprocess
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX_COMMIT = "6e18396"
HUMAN_ONLY = [
    "150% 缩放下桌宠无白底/透明镂空闪烁、边缘不糊、右键菜单文字清晰",
    "拖到副屏后渲染正常；副屏位置重启后回到原位；分辨率改档/拔屏后不悬空在屏外",
    "切窗 10 次后无卡顿、置顶层级正确、点击热区可用",
    "休眠唤醒后自动恢复渲染、拖动右键立即响应、壳崩了 60 秒内自动重建",
    "真机隐藏窗口 ≥30min：两阶段心跳只出现 slow，不出现判死/重载",
]
CLASSIFY = (
    ("slow", ("心跳变慢", "不重载", "判为后台定时器节流")),
    ("dead", ("判定 UI 卡死", "前端无响应")),
    ("recover", ("尝试恢复", "重载排队", "Reload 无效")),
    ("recovered", ("心跳已恢复",)),
)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def measure_display() -> dict:
    """ctypes 调 Win32 一律声明 argtypes（AGENTS 第三条）。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int,
                                           ctypes.POINTER(ctypes.c_uint),
                                           ctypes.POINTER(ctypes.c_uint)]
        shcore.GetDpiForMonitor.restype = wintypes.HRESULT
    except Exception:
        shcore = None

    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    hdc = user32.GetDC(None)
    sys_dpi = gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
    user32.ReleaseDC(None, hdc)
    try:
        user32.GetDpiForSystem.argtypes = []
        user32.GetDpiForSystem.restype = wintypes.UINT
        sys_dpi = user32.GetDpiForSystem()
    except AttributeError:
        pass

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                         wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                         wintypes.LPARAM)
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                           MONITORENUMPROC, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    monitors: list[dict] = []

    def cb(hmon, _hdc, _lprc, _lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        dx, dy = ctypes.c_uint(0), ctypes.c_uint(0)
        if shcore is not None:
            shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
        r, w = mi.rcMonitor, mi.rcWork
        monitors.append({
            "rect": [r.left, r.top, r.right, r.bottom],
            "work": [w.left, w.top, w.right, w.bottom],
            "primary": bool(mi.dwFlags & 1),
            "dpi_x": dx.value or sys_dpi,
            "dpi_y": dy.value or sys_dpi,
            "dpi_scale_pct": round((dx.value or sys_dpi) * 100 / 96),
        })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    fg = wintypes.RECT()
    has_fg = bool(user32.GetForegroundWindow()) and bool(
        user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(fg)))
    fg_rect = [fg.left, fg.top, fg.right, fg.bottom] if has_fg else None
    fullscreen = False
    if has_fg:
        fullscreen = any(fg_rect == m["rect"] for m in monitors)

    state = ctypes.c_int(-1)
    try:
        shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        shell32.SHQueryUserNotificationState.restype = wintypes.HRESULT
        shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:
        pass
    return {"system_dpi": sys_dpi,
            "dpi_scale_pct": round(sys_dpi * 100 / 96),
            "monitors": monitors,
            "foreground_rect": fg_rect,
            "foreground_is_fullscreen": fullscreen,
            # -1 是哨兵初值：调用没写成就是「不可用」，不要拿它冒充有效状态
            "notification_state": state.value if state.value > 0 else None,
            "notification_state_note": ("ok" if state.value > 0
                                        else "SHQueryUserNotificationState 不可用，仅记录")}


def measure_processes() -> dict:
    try:
        import psutil
    except Exception as exc:
        return {"available": False, "reason": repr(exc)}
    want = ("electron.exe", "pythonw.exe", "python.exe")
    counts = {n: 0 for n in want}
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n in counts:
            counts[n] += 1
    return {"available": True, "counts": counts}


def analyze_heartbeat(root: str) -> dict:
    logs = sorted(glob.glob(os.path.join(root, "logs", "launch_run*.log")),
                  key=os.path.getmtime)[-6:]
    per_file = []
    heartbeat_hits: list[str] = []
    for path in logs:
        counts = {name: 0 for name, _ in CLASSIFY}
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "[UI心跳]" not in line:
                    continue
                heartbeat_hits.append(line.strip()[:200])
                for name, keys in CLASSIFY:
                    if any(k in line for k in keys):
                        counts[name] += 1
                        break
        per_file.append({"file": os.path.basename(path),
                         "mtime": datetime.datetime.fromtimestamp(
                             os.path.getmtime(path)).isoformat(timespec="seconds"),
                         "total": sum(counts.values()), "counts": counts})

    fix_ts = None
    try:
        p = subprocess.run(["git", "log", "-1", "--format=%ct", FIX_COMMIT],
                           cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        fix_ts = int((p.stdout or "0").strip())
    except Exception:
        pass
    post_fix = bool(fix_ts) and any(
        os.path.getmtime(os.path.join(root, "logs", e["file"])) > fix_ts
        for e in per_file)
    return {"files": per_file, "found": len(logs),
            "total_lines": len(heartbeat_hits),
            "fix_commit": FIX_COMMIT, "post_fix_run_available": post_fix,
            "last_lines": heartbeat_hits[-5:],
            "note": "修复前日志只能当基线；post_fix_run_available=false 表示两阶段策略上线后没有长跑样本"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="logs/a5_env_probe.json")
    # 【2026-11-08 加·让探针可被判据驱动】原来 ROOT 写死成脚本上两级目录，
    # 于是「日志基线」这一节只能在**恰好有 logs/launch_run*.log 的那台机器**上被验。
    # 加 --root 之后变异刀可以在 tempfile 里造一份合成日志当夹具，
    # 「探针在缺样本时怎么说话」也才第一次变成可判的。
    ap.add_argument("--root", default=ROOT,
                    help="仓根（默认本仓；日志与报告路径都相对它解析）")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    display = measure_display()
    processes = measure_processes()
    heartbeat = analyze_heartbeat(root)
    report = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "scope": "本机测量，不等于 A5 验收通过；人眼项见 requires_human",
        "root": root,
        "display": display,
        "processes": processes,
        "heartbeat": heartbeat,
        "requires_human": list(HUMAN_ONLY),
        "post_fix_long_run_verified": heartbeat["post_fix_run_available"],
    }
    path = args.report if os.path.isabs(args.report) else os.path.join(root, args.report)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 先删旧报告：否则「不落盘」的变异会被上一轮残留产物满足（残留产物假绿）。
    # 判据「报告已落盘」必须只能由本次运行满足。
    if os.path.isfile(path):
        os.remove(path)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    ok, fail, skip = 0, 0, 0

    def chk(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
            print("  PASS %s" % name)
        else:
            fail += 1
            print("  FAIL %s  <- %s" % (name, detail))

    def unverified(name: str, why: str) -> None:
        """样本不存在 ≠ 探针坏了。这条口径是本轮补的，见文件尾的退出码说明。"""
        nonlocal skip
        skip += 1
        print("  WARN %s  <- %s（未验证，不计通过也不计失败）" % (name, why))

    print("1) Win32 显示环境")
    mons = display["monitors"]
    chk("至少枚举到 1 台显示器且矩形为正", len(mons) >= 1 and all(
        m["rect"][2] > m["rect"][0] and m["rect"][3] > m["rect"][1] for m in mons),
        str(len(mons)))
    chk("主屏唯一", sum(1 for m in mons if m["primary"]) == 1,
        str([m["primary"] for m in mons]))
    chk("每块屏 effective DPI 为正", all(m["dpi_x"] > 0 for m in mons))
    print("   显示器 %d 台 / 系统 DPI %d（%d%%）/ 前台全屏=%s / QUNS=%s" % (
        len(mons), display["system_dpi"], display["dpi_scale_pct"],
        display["foreground_is_fullscreen"], display["notification_state"]))
    for m in mons:
        print("   · %s %s dpi=%d (%d%%)" % (
            "主屏" if m["primary"] else "副屏", m["rect"], m["dpi_x"],
            m["dpi_scale_pct"]))

    print("\n2) 心跳日志基线")
    # 【2026-11-08 修·「本机没有样本」被报成「探针坏了」】原来这两条是硬 FAIL：
    #   chk("至少扫到 1 份 launch_run 日志", len(heartbeat["files"]) >= 1)
    #   chk("至少 1 条 [UI心跳] 被分类", heartbeat["total_lines"] >= 1)
    # 可 `logs/` 在 .gitignore 里，**新克隆的树上必然一份都没有** —— 于是这道登记在
    # check_all 里的闸门对每个新人都恒红，而红的原因和探针质量毫无关系。
    # 更糟的是它污染了变异刀：起点恒红 → 六把刀"全红"是白送的（第二十五轮记为
    # KNOWN_VACUOUS 的那一把就是这个）。
    # 现在分三种状态：有样本 → 判；有日志但扫出 0 份 → **FAIL**（那是探针坏了）；
    # 压根没有日志 → **WARN 未验证**。
    if heartbeat["found"] == 0:
        unverified("至少扫到 1 份 launch_run 日志", "本机 logs/ 下没有 launch_run*.log")
        unverified("至少 1 条 [UI心跳] 被分类", "同上，无样本可分类")
    else:
        chk("扫描份数与发现份数一致",
            len(heartbeat["files"]) == heartbeat["found"],
            "发现 %d 份，只扫了 %d 份" % (heartbeat["found"],
                                          len(heartbeat["files"])))
        chk("至少 1 条 [UI心跳] 被分类", heartbeat["total_lines"] >= 1,
            str(heartbeat["total_lines"]))
    for e in heartbeat["files"]:
        print("   · %s 共 %d 条 %s" % (e["file"], e["total"], e["counts"]))
    print("   修复后长跑样本：%s" % ("有" if heartbeat["post_fix_run_available"] else "无（待真机补）"))

    print("\n3) 诚实边界")
    chk("报告保留 requires_human 清单且 >= 3 项", len(HUMAN_ONLY) >= 3)
    chk("报告明确写了本机测量口径", "不等于 A5" in report["scope"])
    chk("报告不谎报「修复后长跑已验」",
        report["post_fix_long_run_verified"] == heartbeat["post_fix_run_available"])
    chk("报告已落盘且可解析", os.path.isfile(path) and
        json.load(io.open(path, "r", encoding="utf-8"))["schema_version"] == 1)
    print("\n报告：%s" % path)
    print("\n结果: %d PASS / %d FAIL / %d 未验证" % (ok, fail, skip))
    # 退出码：
    #   0  = 全部判完且全绿
    #   1  = 有真 FAIL
    #   77 = 没有 FAIL，但有心跳样本没验（本机 logs/ 为空）——**不是通过**
    # 77 是本仓既有约定（check_delivery / 拿不到锁都这么走），check_all 会把它
    # 单列成「未验证」而不是「通过」。
    # `[SKIP]` 那一行是给 check_all 摘原因用的：它从 `[SKIP]` 开头的行取文本，
    # 没有这一行就会显示「未说明原因」——那等于把"为什么没验"又丢了一次。
    if fail:
        return 1
    if skip:
        print("[SKIP] 本机 logs/ 下没有 launch_run*.log —— 心跳基线 %d 项未验证（不是通过）"
              % skip)
        return 77
    return 0


if __name__ == "__main__":
    sys.exit(main())
