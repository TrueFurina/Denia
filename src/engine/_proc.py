"""进程管理 / 端口占用 / 健康检查 / 进程子树枚举的通用辅助。

跨引擎复用（own_llama_server 与 comni_managed 都依赖）。Windows 优先、POSIX 降级。
设计原则：所有操作都不杀"本软件主进程"——只管理我们托管/识别出的子进程树。
"""
from __future__ import annotations

import logging
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000  # Windows: 不弹控制台黑框


def launch_process(
    cmd: List[str],
    cwd: Optional[str] = None,
    log_path: Optional[str] = None,
    env: Optional[dict] = None,
) -> subprocess.Popen:
    """启动一个无控制台窗口的子进程，返回 Popen（非阻塞）。

    关键修复（2026-08-27）：旧实现用 ``stdout=subprocess.PIPE`` 却无人读取。
    Windows 管道缓冲约 64KB，gateway/worker 持续打日志会把管道写满，子进程
    **阻塞卡死**（表现为双工越用越慢、最终无响应）。这里改为：

    - ``log_path`` 给定 → 重定向到该日志文件（二进制追加，OS 负责刷新，不会
      卡死），便于排查 worker 的 TTS/SPEAK 计时；
    - 未给定 → 重定向到 DEVNULL，彻底避免管道死锁。

    ``env``（2026-09-20 新增）：可选的环境变量**覆盖项**（不是全量替换 —— 会和
    当前 os.environ 合并）。用于给 worker 注入 ``COMNI_USE_TTS=0`` 等开关。
    传 None 时行为与之前完全一致。
    """
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            logf = open(log_path, "ab", buffering=0)
        except Exception:  # noqa: BLE001
            logf = subprocess.DEVNULL
    else:
        logf = subprocess.DEVNULL
    kwargs = dict(
        cwd=cwd or None,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    if env:
        merged = dict(os.environ)
        merged.update({str(k): str(v) for k, v in env.items()})
        kwargs["env"] = merged
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _capture_text(cmd: List[str], timeout: float = 10, enc: Optional[str] = None) -> str:
    """运行命令并返回其 stdout 文本（永不返回 None）。

    关键修复（2026-08-27）：中文 Windows 下 netstat/tasklist 输出为 GBK 编码，
    若用 text=True（默认 utf-8）会在 reader 线程抛 UnicodeDecodeError，导致
    subprocess.run(...).stdout 变成 None，进而外层 out.splitlines() 抛
    'NoneType' object has no attribute 'splitlines'（即"引擎拉起失败"的根因）。
    这里统一以 bytes 收集，再按指定/默认编码解码(errors=replace)，并包 try 返回
    空串，保证下游 .splitlines() 永远安全。
    """
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
        )
        raw = cp.stdout
        if raw is None:
            return ""
        use_enc = enc or ("gbk" if sys.platform.startswith("win") else "utf-8")
        return raw.decode(use_enc, errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def find_pid_on_port(port: int) -> Optional[int]:
    """返回占用 TCP *监听* 端口的 PID；无则 None。跨平台用 netstat。"""
    out = _capture_text(["netstat", "-ano", "-p", "tcp"])
    port_s = str(port)
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        toks = line.split()
        if not any(tok.rsplit(":", 1)[-1] == port_s for tok in toks if ":" in tok):
            continue
        try:
            return int(toks[-1])
        except (ValueError, IndexError):
            pass
    return None


def kill_tree(pid: int) -> bool:
    """强杀进程及其整棵子树（taskkill /T）。返回是否成功触发。"""
    if pid <= 0:
        return False
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("kill_tree(%s) 失败: %s", pid, e)
        return False


def kill_pid_on_port(port: int) -> bool:
    pid = find_pid_on_port(port)
    if not pid:
        return False
    return kill_tree(pid)


def is_port_free(port: int) -> bool:
    return find_pid_on_port(port) is None


def find_pids_by_exe_substr(substr: str) -> Set[int]:
    """按可执行文件完整路径包含 *substr*，返回所有匹配 PID。

    用途：清理“上次崩溃/异常退出残留、端口已释放但显存仍加载着模型”的
    llama-server——这类进程按端口查不到（端口早被 OS 回收），却仍在显存里
    占着 ≈8GB，下次 start() 会叠加再拉一组 → 显存翻倍 → OOM 崩。

    边界：只匹配路径包含给定子串的进程。不杀本软件主进程，也不会误杀
    其它目录下的同名 exe（substr 用绝对目录精准限定）。
    """
    if not substr:
        return set()
    out = _capture_text(
        ["powershell", "-NoProfile", "-Command",
         "[System.Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
         "Get-CimInstance Win32_Process | "
         "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"],
        timeout=8, enc="utf-8")
    if not out.strip():
        return set()
    try:
        import json
        raw = json.loads(out)
        procs = raw if isinstance(raw, list) else [raw]
        sub = substr.lower()
        pids: Set[int] = set()
        for p in procs:
            ep = (p.get("ExecutablePath") or "")
            if ep and sub in ep.lower():
                try:
                    pids.add(int(p["ProcessId"]))
                except (ValueError, KeyError):
                    pass
        return pids
    except Exception:  # noqa: BLE001
        return set()


def child_pids(parent_pids: Set[int]) -> Set[int]:
    """枚举 parent_pids 整棵子树下的所有后代 PID（含直接子与更深层级）。"""
    parents = set(parent_pids)
    if not parents:
        return set()
    # PowerShell 默认 stdout 为 UTF-16LE，需显式指定 UTF8 输出 + utf-8 解码
    out = _capture_text(
        ["powershell", "-NoProfile", "-Command",
         "[System.Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
         "Get-CimInstance Win32_Process | "
         "Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"],
        timeout=8, enc="utf-8")
    if not out.strip():
        return set()
    try:
        import json
        raw = json.loads(out)
        procs = raw if isinstance(raw, list) else [raw]
        parent_of = {
            int(p["ProcessId"]): int(p.get("ParentProcessId") or 0)
            for p in procs if "ProcessId" in p
        }
        result: Set[int] = set()
        changed = True
        while changed:
            changed = False
            for pid, par in parent_of.items():
                if par in parents and pid not in parents:
                    parents.add(pid)
                    result.add(pid)
                    changed = True
        return result
    except Exception:  # noqa: BLE001
        return set()


def _health_url_ok(url: str, timeout: float = 3.0) -> bool:
    """对 url 发一次 GET，200 即健康。https 自签名证书不校验。"""
    ctx = None
    if url.startswith("https"):
        try:
            ctx = ssl._create_unverified_context()
        except Exception:  # noqa: BLE001
            ctx = None
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def wait_for_health(url: str, timeout_s: float = 180.0, interval: float = 2.0) -> bool:
    """轮询健康检查直到成功或超时。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _health_url_ok(url):
            return True
        time.sleep(interval)
    return _health_url_ok(url)
