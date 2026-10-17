"""GPU 负载监测（2026-08-25 锁模式机制用）。

轮询 nvidia-smi 获取显存占用率/利用率，供"锁模式状态机"判断是否切快照：
游戏/渲染等大 GPU 负载期间，云端双工/本地双工让位给快照模式（避免本地模型
抢显存卡顿；realtime 也依赖网络/云端，GPU 高负载时用户最烦被打扰）。

nvidia-smi 不可用（AMD/核显/驱动缺失）时降级：sample() 返回 None，锁模式
状态机只靠前台游戏判定。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

_QUERY = "--query-gpu=memory.used,memory.total,utilization.gpu"
_FORMAT = "--format=csv,noheader,nounits"
# 进程级显存查询：只算某个 PID 自己占的 VRAM，排除游戏/其他进程。
# 注意：本机驱动不支持 per-process 的 used_gpu_utilization 字段，所以"软件 GPU
# 占用"只能精确归因到**显存(MB)**；计算利用率(%)无法按进程拆分，整卡 util 始终
# 含游戏(4K 渲染)——日志里已明确标注，避免再把游戏占用误算成软件的。
_COMPUTE_APPS_QUERY = "--query-compute-apps=pid,used_memory"
_COMPUTE_APPS_FORMAT = "--format=csv,noheader,nounits"


def gpu_load() -> Optional[dict]:
    """采样一次 GPU 负载，返回 {mem_pct, util_pct, used_mb, total_mb} 或 None。

    - mem_pct: 显存占用率（memory.used / memory.total * 100）
    - util_pct: 计算利用率（utilization.gpu）
    - used_mb / total_mb: 显存数值（MB，供锁模式排除"自身模型占用"后算外部负载）
    nvidia-smi 失败/不可用返回 None（调用方降级，不抛异常）。
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", _QUERY, _FORMAT],
            capture_output=True, text=True, errors="replace", timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹黑框
        )
        if out.returncode != 0:
            return None
        line = (out.stdout or "").strip().splitlines()
        if not line:
            return None
        parts = [p.strip() for p in line[0].split(",")]
        if len(parts) < 3:
            return None
        used = float(parts[0])
        total = float(parts[1])
        util = float(parts[2])
        mem_pct = round(used / total * 100.0, 1) if total > 0 else 0.0
        return {
            "mem_pct": mem_pct, "util_pct": util,
            "used_mb": used, "total_mb": total,
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("GPU 负载采样失败（降级为不可用）: %s", e)
        return None


def app_gpu_mem_mb(pid: Optional[int] = None) -> Optional[float]:
    """本进程（companion app）自身占用的 GPU 显存(MB)，**排除游戏/其他进程**。

    只算软件自己：DirectML OCR 会话 + 本地双工模型等常驻在这一个进程里的
    显存，不含三角洲(游戏)的 4K 渲染占用。nvidia-smi 不支持 per-process util
    字段时，显存是唯一能精确到进程的归因指标，用它衡量"软件 GPU 占用"。

    返回 None 的两种情况：
      - 本进程当前没有 GPU 计算上下文（DirectML 未启用 / OCR 未加载）→ 软件没占 GPU
      - nvidia-smi 不可用（核显/AMD/驱动缺失）
    """
    try:
        target = pid if pid else os.getpid()
        out = subprocess.run(
            ["nvidia-smi", _COMPUTE_APPS_QUERY, _COMPUTE_APPS_FORMAT],
            capture_output=True, text=True, errors="replace", timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹黑框
        )
        if out.returncode != 0:
            return None
        for line in (out.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                if int(parts[0]) == target:
                    return float(parts[1])
            except ValueError:
                continue
        return None  # 本进程不在计算应用列表（无 GPU 上下文）
    except Exception as e:  # noqa: BLE001
        logger.debug("本进程 GPU 显存采样失败: %s", e)
        return None


def compute_apps_vram_list() -> Optional[list]:
    """枚举当前所有 CUDA compute-apps 进程及其显存占用(MB)，返回 [(pid, vram_mb), ...]。

    ⚠️ **2026-09-20 重要修正：本函数在本机（WDDM 驱动）拿不到显存数值。**
    `nvidia-smi --query-compute-apps=pid,used_memory` 在 Windows WDDM 模式下，
    `used_memory` 列**恒为 `[N/A]`**（驱动不向 WDDM 暴露 per-process 显存）。
    旧实现 `float(parts[1])` 对 `"[N/A]"` 抛 ValueError → 被 `continue` **静默跳过**
    ⇒ 返回空列表 ⇒ 上层把整卡占用全算成"其它程序"（显存归属判定错误的根因）。

    现在改为**只返回能解析出数值的条目**，并在下方提供 `per_pid_vram_mb_map()`
    作为本机可用的替代数据源。需要"每个进程占多少显存"时**请用后者**。
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", _COMPUTE_APPS_QUERY, _COMPUTE_APPS_FORMAT],
            capture_output=True, text=True, errors="replace", timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹黑框
        )
        if out.returncode != 0:
            return None
        res: list = []
        for line in (out.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
                mb = _parse_nvidia_mem(parts[1])   # [N/A] → None（不再静默当成"没占"）
            except ValueError:
                continue
            if mb is None:
                continue
            res.append((pid, mb))
        return res
    except Exception as e:  # noqa: BLE001
        logger.debug("compute-apps 枚举失败（降级为不可用）: %s", e)
        return None


def _parse_nvidia_mem(tok: str) -> Optional[float]:
    """解析 nvidia-smi 的显存列。`[N/A]` / `N/A` / 空 ⇒ None（= 本机拿不到该值）。"""
    t = (tok or "").strip().strip('"')
    if not t or t.upper() in ("[N/A]", "N/A", "NAN", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# per-PID 显存（2026-09-20 新增）：本机 WDDM 下 nvidia-smi 给不出值，
# 改用 Windows 自带的 GPU 性能计数器。
# 实测（RTX 5080 / 驱动 595.79）：
#   pid_88544_...(llama-server) Dedicated Usage = 11959.7 MB  ← 与 nvidia-smi 整卡口径吻合
#   \GPU Process Memory(*)\Local Usage        合计 ≈ 13617 MB  ← ≈ 整卡 used
# 注意：`Dedicated Usage` 各实例**相加会大于物理显存**（统计的是提交量，
# 含已被驱逐的页），所以**只能用于"同一时刻的相对归属"，不能当整卡用量**。
# ---------------------------------------------------------------------------
_GPU_PROC_MEM_COUNTER = "\\GPU Process Memory(*)\\Dedicated Usage"
_per_pid_cache: dict = {"mono": -1e9, "data": {}, "ok": False}
_PER_PID_TTL = 5.0   # 秒；单次采集本身约 2.5s，TTL 必须大于采集耗时，
                     # 否则缓存刚写就已过期 => 每秒都重起 PowerShell（实测踩到过）


def per_pid_vram_mb_map(use_cache: bool = True) -> Optional[dict]:
    """{pid: 显存MB} 的映射（本机唯一可用的 per-process 显存来源）。

    实现：PowerShell `Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage'`，
    实例名形如 `pid_88544_luid_0x..._phys_0`，从中解析 PID。

    返回 None = 采集不可用（非 Windows / 计数器缺失 / PowerShell 失败）⇒ 调用方降级。
    返回 {} = 采集可用但当前没有进程占用。

    结果按 `_PER_PID_TTL` 缓存：面板 1s 轮询时不会每次都起 PowerShell。
    """
    import time as _t
    if use_cache and (_t.monotonic() - _per_pid_cache["mono"]) < _PER_PID_TTL:
        if not _per_pid_cache.get("ok"):
            return None      # 上次采集失败：缓存的是失败态，别误当成"没有进程占用"
        return _per_pid_cache["data"] or None

    def _stamp(data, ok):
        # ⚠️ 必须在**采集完成后**打时间戳。此前用采集开始前的 now，而单次采集
        # 要约 2.5s，写入时已超 TTL ⇒ 缓存形同虚设、每秒重起 PowerShell
        # （实测 cold 2.4s / "cached" 2.3s）。改用 monotonic 顺带免疫系统时钟回拨。
        _per_pid_cache["mono"] = _t.monotonic()
        _per_pid_cache["data"] = data or {}
        _per_pid_cache["ok"] = bool(ok)

    if os.name != "nt":
        _stamp({}, False)
        return None

    ps = (
        "Get-Counter -Counter '\\GPU Process Memory(*)\\Dedicated Usage' "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty CounterSamples | "
        "Where-Object { $_.CookedValue -gt 1048576 } | "
        "ForEach-Object { \"$($_.InstanceName)|$($_.CookedValue)\" }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, errors="replace", timeout=12,
            creationflags=0x08000000,
        )
        if out.returncode != 0:
            _stamp({}, False)
            return None
        data: dict = {}
        for line in (out.stdout or "").strip().splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            inst, val = line.rsplit("|", 1)
            m = re.search(r"pid_(\d+)", inst)
            if not m:
                continue
            try:
                data[int(m.group(1))] = round(float(val) / (1024.0 * 1024.0), 1)
            except ValueError:
                continue
        _stamp(data, True)
        return data
    except Exception as e:  # noqa: BLE001
        logger.debug("per-PID 显存计数器采集失败: %s", e)
        _stamp({}, False)
        return None


def vram_by_pid_list(pids) -> dict:
    """给定 PID 集合，返回 {pid: 显存MB}（只含能查到值的）。

    这是"模型占了多少显存"的**正确取数入口**：
    先 `per_pid_vram_mb_map()`（Windows 计数器），拿不到再退 `compute_apps_vram_list()`。
    """
    want = {int(p) for p in (pids or [])}
    if not want:
        return {}
    m = per_pid_vram_mb_map()
    if m:
        return {p: m[p] for p in want if p in m}
    # 兜底：非 Windows / 计数器不可用时，试 nvidia-smi（本机虽为 [N/A]，别处可能可用）
    apps = compute_apps_vram_list() or []
    by = {int(p): float(v) for p, v in apps}
    return {p: by[p] for p in want if p in by}



def ollama_resident_mb(own_models: Optional[list[str]] = None) -> float:
    """ollama 当前驻留显存（MB，同步版）——只统计"我们的模型"。

    锁模式/启动判断"外部负载"时排除 ollama 自身模型（本地双工 o4.5 是我们
    自己的，不是用户开的游戏/渲染）。实锤（2026-08-25）：上一实例 keep_alive
    残留 o4.5 未卸载，新实例启动时显存 ≥60% 被误判"大负载"锁快照。

    2026-08-25 白名单化：只排除 own_models（config 指定的本地模型，如
    openbmb/minicpm-o4.5）——**用户自己用 ollama 跑的其他模型（如 qwen3:14b）
    不算"我们的"，其显存计入外部负载**，否则用户模型+游戏挤爆显存却不触发锁。
    own_models 为空/None 时统计全部 ollama 模型（兼容旧行为）。
    """
    try:
        import json
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=4) as r:
            ps = json.loads(r.read().decode("utf-8", "replace"))
        total = 0.0
        for m in ps.get("models", []):
            name = str(m.get("name") or "")
            if own_models:
                base = name.split(":")[0]
                if not any(base.startswith(om) or base == om
                           for om in own_models if om):
                    continue  # 不是我们的模型 → 不排除（算外部负载）
            size = m.get("size") or m.get("size_vram") or 0
            total += float(size) / (1024 * 1024)  # 字节 → MB
        return total
    except Exception:  # noqa: BLE001
        return 0.0
