# -*- coding: utf-8 -*-
"""运行日志的轮转与清理（阶段 B2）。

为什么需要它：`main.py` 每次启动都新建 `logs/run_<时间戳>.log`，崩溃时另外
再落一个 `logs/crash_<时间戳>.log`。**从来没人删过**——跑一年就是一个几千个
文件的目录，其中绝大多数是空的 crash 文件（崩溃钩子每次启动都会创建）。

清理策略：
  · **按份数**：`run_*` 与 `crash_*` 各自保留最近 N 份（按修改时间，不是名字）。
  · **按单文件上限**：当前这次运行的日志交给 `RotatingFileHandler` 滚动，
    超过上限就滚一份备份重新开始，而不是无限增长。

为什么按 mtime 排序而不是按文件名：文件名里的时间戳是**进程启动时刻**，
而 `run_` 文件是持续写入的——先启动的进程可能后写完，按名字排会删掉还在用的文件。

安全边界：只动 `logs/` 下 `run_` / `crash_` 前缀的文件；删不动的（被别的进程
占着）静默跳过，**绝不重试、绝不杀进程**。
"""
from __future__ import annotations

import glob
import logging
import os

logger = logging.getLogger("src.log_rotate")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")

DEFAULT_KEEP = 30          # 每类日志保留份数
DEFAULT_MAX_MB = 8         # 单文件上限（MB）
PREFIXES = ("run_", "crash_")


def human_bytes(n: int) -> str:
    """给人类看的体积。

    负数夹到 0：调用方传进来的可能是差值（如 `total - used`），
    显示 "-5 B" 只会让人怀疑是不是算错了。
    """
    n = max(int(n or 0), 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _list(directory: str, prefix: str) -> list[str]:
    """某前缀的日志文件，按 mtime **降序**（最新的在前）。空文件排最后。"""
    if not os.path.isdir(directory):
        return []
    hits = [p for p in glob.glob(os.path.join(directory, prefix + "*.log*"))
            if os.path.isfile(p)]
    # 空文件优先被清理：它们没有任何排障价值
    hits.sort(key=lambda p: (os.path.getsize(p) > 0,
                             os.path.getmtime(p)), reverse=True)
    return hits


def cleanup(log_dir: str | None = None, keep: int = DEFAULT_KEEP,
            max_bytes: int = DEFAULT_MAX_MB * 1024 * 1024) -> dict:
    """删掉超出保留份数的旧日志。返回 `{removed, freed_bytes, kept}`。

    幂等且只读安全：任何一步失败都只是"这次没删掉"，不影响启动。
    """
    directory = log_dir or LOG_DIR
    keep = max(int(keep or 0), 0)
    removed: list[str] = []
    freed = 0
    kept = 0
    for prefix in PREFIXES:
        files = _list(directory, prefix)
        kept += len(files[:keep])
        for path in files[keep:]:
            try:
                size = os.path.getsize(path)
                os.remove(path)
                removed.append(path)
                freed += size
            except OSError as _e:  # noqa: BLE001  被占用 / 已消失，都跳过
                logger.debug("[日志] 跳过（删不动）：%s — %s", path, _e)
    if removed:
        logger.info("[日志] 已清理 %d 个旧日志文件，释放 %s（保留 %d 份）",
                    len(removed), human_bytes(freed), kept)
    return {"removed": removed, "freed_bytes": freed, "kept": kept}


def report(log_dir: str | None = None) -> dict:
    """统计当前日志占用；给 `--doctor` 用。不删除任何东西。"""
    directory = log_dir or LOG_DIR
    counts: dict[str, int] = {}
    total = 0
    for prefix in PREFIXES:
        files = _list(directory, prefix)
        size = 0
        for path in files:
            try:
                size += os.path.getsize(path)
            except OSError:  # noqa: BLE001
                pass
        counts[prefix] = len(files)
        total += size
    return {"dir": directory, "counts": counts, "total_bytes": total,
            "total_human": human_bytes(total)}


__all__ = ["LOG_DIR", "DEFAULT_KEEP", "DEFAULT_MAX_MB", "PREFIXES",
           "human_bytes", "cleanup", "report"]
