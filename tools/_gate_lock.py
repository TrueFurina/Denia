"""check_all 的进程间互斥锁：防止两个会话的变异刀同时改同一份源码。

【2026-11-04 加】本仓的 `_qc_*` 变异刀是「读源码 → 改成坏的 → 跑检查 → 还原」。
两个 check_all 同时跑时，A 的还原会被 B 的变异覆盖，或者 A 的收尾复核读到 B
留下的半改状态。今天已经出现两次这种假红：
  · 依赖声明变异：读到一半被删掉的 src/voice/_mut_asr_*.py
  · 导出聊天记录变异：webapp.py 的 M4 变异没被还原，之后每一轮都判红

所以在**跑闸门之前**加一道全局锁：同一时刻只允许一个 check_all 改工作区。
--list 是纯只读，不走锁。锁放在 logs/（已 gitignore），不进包、不进仓库。

登记说明：本文件是闸门的运行前提，不是检查器（不属 `check_*`/`test_*` 扫描范围），
因此**不要**在别处写「不进闸门：」这种免登标记——那会被登记对账变异刀当成
候选目标，而闸门其实并不检查这个文件（2026-11-04 实测把 M2 刀弄哑了一次）。
"""
from __future__ import annotations

import ctypes
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_PATH = os.path.join(ROOT, "logs", ".check_all.lock")
STALE_SECONDS = 45 * 60      # 全量门禁约 10 分钟；超过 45 分钟视为上次被强杀
STILL_ACTIVE = 259           # GetExitCodeProcess 的"还在跑"，只有它代表存活


def _pid_alive(pid: int) -> bool:
    """Windows 下查进程还在不在（OpenProcess + GetExitCodeProcess，不发信号）。

    【2026-11-08 实测】只看 OpenProcess 会把「已退出」判成「还活着」：
    进程被强杀之后，只要别处仍握着它的句柄（任务调度器 / 父进程 / 调试器），
    进程对象就不会从 PID 表里消失 —— OpenProcess 照样成功，而进程早已结束。
    于是陈旧锁永远清不掉，之后每一轮 check_all 都停在「已有 check_all 在跑」，
    一直堵到 STALE_SECONDS（45 分钟）。实测：子进程 terminate + wait（poll()=1）
    之后 OpenProcess 仍返回句柄、GetExitCodeProcess 返回 0，`_pid_alive` 判 True。
    所以判据必须以退出码为准：**STILL_ACTIVE(259) 才是在跑**。
    """
    if pid <= 0:
        return False
    try:
        k = ctypes.windll.kernel32
        # 第三条硬约束：ctypes 调 Win32 必须声明 argtypes/restype，否则 HANDLE
        # 按 32 位 int 传递，句柄可能被截断（本仓 SetWindowPos 曾因此全部静默失效）。
        k.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        k.OpenProcess.restype = ctypes.c_void_p
        k.GetExitCodeProcess.argtypes = (ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_uint32))
        k.GetExitCodeProcess.restype = ctypes.c_int
        k.CloseHandle.argtypes = (ctypes.c_void_p,)
        k.CloseHandle.restype = ctypes.c_int
        h = k.OpenProcess(0x1000, 0, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_uint32(0)
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                # 问不到退出码（权限不足等）：退回保守判据，交给时间阈值裁决
                return True
            return code.value == STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:  # noqa: BLE001  非 Windows / 权限不足：退回"只按时间判陈旧"
        return True


def _read_lock() -> dict:
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _remove() -> None:
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def acquire() -> tuple[bool, str]:
    """拿到锁返回 (True, "")；拿不到返回 (False, 原因)。陈旧锁自动清掉再试一次。"""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read_lock()
            age = time.time() - float(info.get("ts") or 0)
            alive = _pid_alive(int(info.get("pid") or 0))
            # info 为空 = 锁文件写坏了 / 只剩半截 JSON。这种锁既没有 PID 也没有
            # 时间戳，绝不能当成"有人在跑"把之后所有门禁永久堵死（实测踩到）。
            if (not info) or age > STALE_SECONDS or not alive:
                _remove()
                continue
            return (False, "已有 check_all 在跑（PID %s，%.0fs 前启动）"
                    % (info.get("pid", "?"), max(0.0, age)))
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "ts": time.time()}, f)
            return (True, "")
    return (False, "锁文件竞争失败，请稍后重试")


def release() -> None:
    """只删自己持有的锁，别把别人的锁删掉。"""
    info = _read_lock()
    if not info or int(info.get("pid") or 0) == os.getpid():
        _remove()
