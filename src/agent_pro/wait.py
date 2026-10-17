# -*- coding: utf-8 -*-
"""agent_pro · wait_for 轮询等待 + 状态快照 + 文件检索（三个新工具的实现）。"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("agent_pro")


# ---------------------------------------------------------------------------
# wait_for：轮询等待窗口/进程/屏幕出现目标（替代散落的固定 sleep）
# ---------------------------------------------------------------------------
async def wait_for(kind: str, target: str, timeout_s: float, interval_s: float,
                   ctx: dict) -> str:
    kind = (kind or "").strip().lower()
    target = (target or "").strip()
    if kind not in ("window", "process", "screen"):
        return "wait_for 的 kind 只支持 window / process / screen"
    if not target:
        return "未提供要等待的目标"
    deadline = time.time() + max(1.0, min(90.0, timeout_s))
    if kind == "window":
        from src.llm.agent_tools import _win32_windows
        while time.time() < deadline:
            t = target.lower()
            for _h, title, exe in _win32_windows():
                if t in (title or "").lower() or t in (exe or "").lower():
                    return f"窗口已出现：{title or exe}"
            await asyncio.sleep(interval_s)
        return f"等待窗口「{target}」超时（{timeout_s:.0f}s 未出现）"
    if kind == "process":
        from .launch import proc_names
        t = target.lower()
        if not t.endswith(".exe"):
            t += ".exe"
        while time.time() < deadline:
            if t in proc_names():
                return f"进程 {t} 已在运行"
            await asyncio.sleep(interval_s)
        return f"等待进程 {target} 超时（{timeout_s:.0f}s 未出现）"
    # screen：轮询 VLM 看目标文本是否出现（贵，间隔放大）
    from src.llm.agent_tools import _see_screen
    interval = max(3.0, interval_s)
    last = ""
    while time.time() < deadline:
        last = await _see_screen(f"屏幕上是否出现「{target}」相关内容？只答 出现/未出现 + 一句依据。", ctx)
        if "出现" in (last or "") and "未出现" not in (last or ""):
            return f"屏幕已出现目标：「{target}」（{last[:80]}）"
        await asyncio.sleep(interval)
    return f"等待屏幕出现「{target}」超时（{timeout_s:.0f}s）。最后一次判定：{(last or '')[:80]}"


# ---------------------------------------------------------------------------
# state_snapshot：一次快照 = 前台 + 窗口清单 +（可选）VLM 屏幕概括
# ---------------------------------------------------------------------------
def _foreground_text() -> str:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "（无前台窗口）"
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        try:
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, 0, pid.value)
            if h:
                b2 = ctypes.create_unicode_buffer(260)
                sz = ctypes.c_ulong(260)
                if k32.QueryFullProcessImageNameW(h, 0, b2, ctypes.byref(sz)):
                    exe = (b2.value or "").rsplit("\\", 1)[-1]
                k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass
        return f"{buf.value or '（无标题）'}（{exe or '未知进程'}）"
    except Exception as e:  # noqa: BLE001
        return f"（前台读取失败：{e}）"


async def state_snapshot(args: dict, ctx: dict, flags: dict) -> str:
    from src.llm.agent_tools import _win32_windows
    lines = [f"【前台】{_foreground_text()}"]
    wins = _win32_windows()
    shown = 0
    seen: set[str] = set()
    for _h, title, exe in wins:
        if shown >= int(flags.get("snapshot_max_windows", 12)):
            break
        t = (title or "").strip()
        # 无标题窗口是 shell/隐藏窗口（实测本机稳定混入 4~5 条 explorer/sesvcr 噪音），
        # 对模型没有信息价值；同标题重复窗口也只列一次。
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  - {t}（{exe}）")
        shown += 1
    if not shown:
        lines.append("  （当前没有可见窗口）")
    if flags.get("snapshot_visual", True) and args.get("visual", True):
        try:
            from src.llm.agent_tools import _see_screen
            q = str(args.get("question") or "用一两句话概括屏幕上正在显示什么。")
            summary = await _see_screen(q, ctx)
            lines.append("【屏幕】" + (summary or "（VLM 无返回）"))
        except Exception as e:  # noqa: BLE001
            lines.append(f"【屏幕】VLM 概括不可用：{e}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# find_file：Everything 双模式（es.exe 优先，受限 walk 兜底）
# ---------------------------------------------------------------------------
_DEFAULT_ROOTS = ["Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music"]


def _find_es(flags: dict) -> str:
    cand = str(flags.get("es_candidates") or "").strip()
    if cand and os.path.exists(cand):
        return cand
    which = shutil.which("es") or shutil.which("es.exe")
    if which:
        return which
    for d in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
        if d:
            for sub in (r"Everything\es.exe", r"Everything\Everything.exe"):
                p = os.path.join(d, sub)
                if os.path.exists(p):
                    return p
    return ""


def _walk_roots(flags: dict) -> list[str]:
    custom = str(flags.get("find_roots") or "").strip()
    if custom:
        return [x for x in custom.split(";") if x.strip()]
    roots = []
    profile = os.environ.get("USERPROFILE", "")
    for sub in _DEFAULT_ROOTS:
        p = os.path.join(profile, sub)
        if os.path.isdir(p):
            roots.append(p)
    if profile:
        roots.append(profile)
    return roots


def _walk_search(pattern: str, flags: dict) -> list[str]:
    budget = float(flags.get("find_walk_budget_s", 8.0))
    maxn = int(flags.get("find_max_results", 30))
    out: list[str] = []
    t0 = time.time()
    for root in _walk_roots(flags):
        base_depth = root.count(os.sep)
        for dp, dn, fn in os.walk(root):
            if time.time() - t0 > budget or len(out) >= maxn:
                return out
            if dp.count(os.sep) - base_depth > 4:  # 深度限制
                dn[:] = []
                continue
            dn[:] = [d for d in dn if not d.startswith((".", "$"))]
            for f in fn:
                if fnmatch.fnmatch(f.lower(), pattern.lower()):
                    out.append(os.path.join(dp, f))
                    if len(out) >= maxn:
                        return out
    return out


def find_file(args: dict, flags: dict) -> str:
    pattern = str(args.get("pattern") or "").strip()
    folder = str(args.get("folder") or "").strip()
    if not pattern:
        return "未提供文件名模式（如 *.png、报告*.docx）"
    maxn = int(flags.get("find_max_results", 30))
    engine_pref = str(flags.get("file_search_engine") or "auto").lower()
    results: list[str] = []
    engine_used = ""
    es = _find_es(flags)
    if engine_pref in ("auto", "everything") and es:
        try:
            cmd = [es, "-n", str(maxn * 2), "-p", "path"]
            if folder:
                cmd.append(os.path.join(folder, pattern))
            else:
                cmd.append(pattern)
            o = subprocess.run(cmd, capture_output=True, text=True,
                               errors="replace", timeout=15)
            results = [l.strip() for l in (o.stdout or "").splitlines() if l.strip()]
            engine_used = f"Everything（{es}）"
        except Exception as e:  # noqa: BLE001
            logger.warning("[agent_pro] es.exe 检索失败，转 walk：%s", e)
            results = []
    if not results and engine_pref in ("auto", "walk"):
        if folder:
            # 指定目录：直接在该目录内受限 walk
            saved = flags.get("find_roots")
            flags["find_roots"] = folder
            try:
                results = _walk_search(pattern, flags)
            finally:
                if saved is None:
                    flags.pop("find_roots", None)
                else:
                    flags["find_roots"] = saved
        else:
            results = _walk_search(pattern, flags)
        engine_used = "本地受限遍历（Everything 未安装或未返回）"
    if not results:
        return (f"未找到匹配「{pattern}」的文件"
                f"（{engine_used or '未启用任何引擎'}；可换更宽松的模式或指定 folder）")
    head = f"找到 {len(results)} 个（引擎：{engine_used}，显示前 {min(len(results), maxn)} 个）："
    return head + "\n" + "\n".join("  " + r for r in results[:maxn])
