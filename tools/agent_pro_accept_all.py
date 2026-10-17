# -*- coding: utf-8 -*-
"""agent_pro · 7 工具真机验收（尽量零副作用，结果写 G:\ai对话\_agent_pro_accept.txt）

覆盖：app_index / launch 验证 / find_file / wait_for / state_snapshot /
      ui_elements / click_element(真实应用直触) / search_in_app / media_state
安全策略：不动用户鼠标、不改用户文件；启动测试只针对记事本且先探测是否已在运行；
          search_in_app 仅在前台窗口确为目标窗口时才执行（否则如实跳过）。
"""
import asyncio
import ctypes
import io
import os
import subprocess
import sys
import time

sys.path.insert(0, r"G:\ai对话\11")
os.chdir(r"G:\ai对话\11")

R = []


def P(*a):
    R.append(" ".join(str(x) for x in a))


def sec(t):
    P("")
    P("== " + t + " ==")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


from src.agent_pro import app_index as ai  # noqa: E402
from src.agent_pro import config as cfg  # noqa: E402
from src.agent_pro import launch as lp  # noqa: E402
import src.agent_pro.screen as scr  # noqa: E402
from src.agent_pro.media import media_state  # noqa: E402
from src.agent_pro.wait import find_file, state_snapshot, wait_for  # noqa: E402

flags = dict(cfg.DEFAULTS)
u32 = ctypes.windll.user32

P("=== agent_pro 真机验收 @ " + time.strftime("%Y-%m-%d %H:%M:%S") + " ===")
P("DPI: " + scr._ensure_dpi_aware())

# ---------------------------------------------------------------- 1 索引
sec("[1] app_index：索引与匹配")
t0 = time.time()
try:
    apps = ai.build_index()
    P("建索引：%d 个应用，耗时 %.1fs" % (len(apps), time.time() - t0))
    from collections import Counter
    P("来源分布：" + str(dict(Counter(r["source"] for r in apps))))
except Exception as e:
    apps = []
    P("建索引失败：" + str(e))
t0 = time.time()
apps2 = ai.load_index(ttl_h=24.0)
if apps:
    miss = [r.get("name") for r in apps if not (r.get("token") or {}).get("value")]
    P("记录结构自检：缺 token 的记录 %d 条%s" % (
        len(miss), ("（" + ", ".join(miss[:5]) + "）") if miss else " （全部可启动）"))
P("TTL 缓存读取：%.0fms，%d 个" % ((time.time() - t0) * 1000, len(apps2)))
for t in ["记事本", "计算器", "微信", "汽水音乐", "抖音", "不存在的软件xyz"]:
    hits = ai.match(t, apps)
    if hits:
        b, s = hits[0]
        P("  %-14s → %-18s [%s] %.0f分" % (t, b["name"], b["source"], s))
    else:
        P("  %-14s → 无匹配（如实拒绝）" % t)

# ---------------------------------------------------------------- 2 启动
sec("[2] launch：带验证启动（记事本，含冷启动与短路两态）")
notepad_running = "notepad.exe" in lp.proc_names()
P("测试前记事本是否在运行：" + str(notepad_running))
f2 = dict(flags, verify_timeout_s=12.0)
try:
    if not notepad_running:
        t0 = time.time()
        r = lp.index_launch("记事本", flags=f2)
        P("冷启动 index_launch('记事本') %.1fs →" % (time.time() - t0))
        P("  " + r)
    else:
        P("（记事本已在运行，跳过冷启动，仅测『已在运行』短路）")
    t0 = time.time()
    r2 = lp.index_launch("记事本", flags=f2)
    P("二次调用 %.1fs → %s" % (time.time() - t0, r2))
except Exception as e:
    P("启动测试异常：" + str(e))

# ---------------------------------------------------------------- 3 find_file
sec("[3] find_file：文件检索")
desk = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
t0 = time.time()
r = find_file({"pattern": "*.png", "folder": desk}, flags)
P("桌面 *.png：%.1fs" % (time.time() - t0))
P(r[:400])
t0 = time.time()
r = find_file({"pattern": "*.docx", "folder": "文档"}, flags)
P("「文档」*.docx：%.1fs" % (time.time() - t0))
P(r[:300])

# ---------------------------------------------------------------- 4 wait_for
sec("[4] wait_for：轮询等待")
t0 = time.time()
r = run(wait_for("process", "notepad.exe", 6.0, 0.5, {}))
P("等进程 notepad.exe：%.1fs → %s" % (time.time() - t0, r))
t0 = time.time()
r = run(wait_for("window", "不存在的窗口xyz", 3.0, 0.5, {}))
P("等不存在窗口（应超时如实报告）：%.1fs → %s" % (time.time() - t0, r))

# ---------------------------------------------------------------- 5 state_snapshot
sec("[5] state_snapshot：状态快照（ctx 无凭据时应如实降级）")
t0 = time.time()
r = run(state_snapshot({}, {}, flags))
P("耗时 %.1fs" % (time.time() - t0))
P(r[:600])

# ---------------------------------------------------------------- 6 ui_elements
sec("[6] ui_elements：控件枚举（记事本）")
t0 = time.time()
r = scr.ui_elements({"app": "记事本"}, flags)
P("耗时 %.1fs" % (time.time() - t0))
P(r[:900])
P("抓到的控件引用数：%d / 元素数 %d" % (
    sum(1 for c in scr._ui_ctrls if c is not None), len(scr._ui_ctrls)))
P("快照 age：%.1fs（TTL 闸用）" % (time.monotonic() - scr._ui_cache_ts))

# ---------------------------------------------------------------- 7 click_element
sec("[7] click_element：真实应用上的 UIA 直触（记事本『最大化』）")
try:
    hwnd = 0
    for h, title, exe in __import__("src.llm.agent_tools", fromlist=["x"])._win32_windows():
        if (exe or "").lower().endswith("notepad.exe"):
            hwnd = int(h)
            break
    P("记事本主窗口 hwnd：" + str(hwnd))
    if not hwnd:
        P("记事本窗口未找到，跳过本项")
    else:
        was_zoomed = bool(u32.IsZoomed(hwnd))
        idx = None
        for i, e in enumerate(scr._ui_cache, 1):
            if e.get("name") in ("最大化", "Maximize") or (
                    not e.get("name") and e.get("ctype") == "Button"):
                idx = i
                break
        P("选中的元素序号：%s（当前是否最大化：%s）" % (idx, was_zoomed))
        if idx is None:
            P("未找到可用按钮，跳过")
        else:
            out = run(scr.click_element({"index": idx}, {}, flags))
            P("click_element 返回：" + out)
            time.sleep(0.6)
            now_zoomed = bool(u32.IsZoomed(hwnd))
            P("直触后是否最大化：%s（之前 %s）→ %s" % (
                now_zoomed, was_zoomed,
                "窗口状态真的变了 → 直触在真实应用上生效" if now_zoomed != was_zoomed
                else "窗口状态未变（可能该控件语义不同）"))
except Exception as e:
    P("click_element 测试异常：" + str(e))

# ---------------------------------------------------------------- 8 search_in_app
sec("[8] search_in_app：语义化搜索（仅前台为目标窗口时才执行）")
try:
    fg = u32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(fg, buf, 512)
    fg_title = buf.value or ""
    P("当前前台窗口标题：" + repr(fg_title))
    if "记事本" in fg_title or "Notepad" in fg_title:
        out = run(scr.search_in_app({"app": "记事本", "query": "agent_pro 验收测试"}, {}, flags))
        P("执行结果：" + out)
    else:
        P("前台不是记事本 → 如实跳过（避免误输入到用户正在编辑的窗口）")
        P("说明：search_in_app 会发 Ctrl+A / Ctrl+V / Enter，必须在前台为目标窗口时才安全")
except Exception as e:
    P("search_in_app 测试异常：" + str(e))

# ---------------------------------------------------------------- 9 media_state
sec("[9] media_state：系统媒体会话（SMTC）")
t0 = time.time()
try:
    r = run(media_state({}, {}, flags))
    P("耗时 %.2fs" % (time.time() - t0))
    P(r[:500])
except Exception as e:
    P("media_state 异常：" + str(e))

P("")
if not notepad_running:
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    P("（收尾：已关闭本次测试启动的记事本）")
P("=== 验收结束 ===")
with io.open(r"G:\ai对话\_agent_pro_accept.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(R) + "\n")
print("DONE")
