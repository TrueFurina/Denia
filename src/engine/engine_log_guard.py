# -*- coding: utf-8 -*-
"""引擎子进程日志（gateway.log / worker.log）的滚动与大小守护。

背景
----
`logs/engine/worker.log` 由外部子进程（Comni gateway / llama-server worker）
直接写文件（父进程把打开的 fd 传下去），**没有任何轮转**。实测曾涨到
16.9 GB、约 3 MB/s，把整机拖到卡死，且因文件过大连打开排查都做不到。

为什么不能简单用 RotatingFileHandler
------------------------------------
文件句柄在**子进程**手里，父进程换不了它的 handler。可行的只有两条：
1. **启动时轮转**——此刻新进程还没打开该文件，重命名/删除是安全的（主路径）；
2. **运行期大小守护**——文件已被子进程占用，Windows 下重命名常失败，
   此时退化为"原地截断"（把文件清到 0），保住磁盘不被撑爆。

设计原则
--------
- 零第三方依赖；读不到配置就用内置默认值；**任何异常都不阻断引擎启动**。
- 可通过配置 `engine_log_guard_enabled: false` 一键回到改动前行为。
- 只保留最近 N 份备份，超出即删（满足"保留数量上限"）。
"""
from __future__ import annotations

import os
import re
import threading
import time

_DEFAULTS = {
    "enabled": True,
    "max_mb": 50.0,
    "backups": 3,
    "interval_s": 120.0,
}

_guard_started = False
_guard_lock = threading.Lock()


# ---------------------------------------------------------------- 配置读取
def _load_cfg() -> dict:
    """尽力从 config.yaml 读日志旋钮；读不到就返回默认值。

    优先 yaml（项目已依赖），失败则用极简正则兜底——日志模块不该因为
    缺依赖或配置缺失而抛异常。
    """
    cfg = dict(_DEFAULTS)
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "config.yaml")
        if not os.path.isfile(path):
            return cfg
        text = open(path, encoding="utf-8", errors="ignore").read()

        def _num(key: str):
            m = re.search(r"^\s*%s\s*:\s*([0-9.]+)" % re.escape(key), text, re.M)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    return None
            return None

        def _bool(key: str):
            m = re.search(r"^\s*%s\s*:\s*(true|false)" % re.escape(key), text, re.I | re.M)
            return (m.group(1).lower() == "true") if m else None

        v = _bool("engine_log_guard_enabled")
        if v is not None:
            cfg["enabled"] = v
        for key, name in (("engine_log_max_mb", "max_mb"),
                          ("engine_log_backups", "backups"),
                          ("engine_log_guard_interval_s", "interval_s")):
            v = _num(key)
            if v is not None:
                cfg[name] = v
    except Exception:  # noqa: BLE001
        pass
    # 环境变量优先（便于临时调试，不影响配置文件）
    for key, env in (("enabled", "ENGINE_LOG_GUARD"),
                     ("max_mb", "ENGINE_LOG_MAX_MB"),
                     ("backups", "ENGINE_LOG_BACKUPS"),
                     ("interval_s", "ENGINE_LOG_GUARD_INTERVAL_S")):
        raw = os.environ.get(env)
        if raw:
            if key == "enabled":
                cfg[key] = raw.strip().lower() in ("1", "true", "yes", "on")
            else:
                try:
                    cfg[key] = float(raw)
                except ValueError:
                    pass
    return cfg


# ---------------------------------------------------------------- 滚动核心
def _size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024.0 * 1024.0)
    except OSError:
        return 0.0


def _rotate(path: str, backups: int) -> bool:
    """把 path 滚动成 path.1（旧 .1→.2 …），超出 backups 的删除。

    返回是否成功。子进程占用文件时 Windows 下会 OSError → 交给调用方降级。
    """
    if not os.path.isfile(path):
        return False
    # 先清理超额的末尾备份
    try:
        stale = os.path.join(os.path.dirname(path),
                             os.path.basename(path) + ".%d" % (backups + 1))
        if os.path.exists(stale):
            os.remove(stale)
    except OSError:
        pass
    for i in range(backups, 0, -1):
        src = path if i == 1 else (path + ".%d" % (i - 1))
        dst = path + ".%d" % i
        if not os.path.exists(src):
            continue
        try:
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(src, dst)
        except OSError:
            return False
    return True


def _truncate_in_place(path: str) -> bool:
    """降级方案：文件被子进程占用、无法重命名时，原地清空。

    子进程的文件指针仍停在大偏移处，之后写入会产生稀疏空洞，但**磁盘占用
    立刻归零**，足以避免撑爆磁盘——这是本场景唯一要保的底线。
    """
    try:
        with open(path, "r+b"):
            pass
        with open(path, "wb"):
            pass
        return True
    except OSError:
        return False


def rotate_on_startup(*paths: str) -> None:
    """引擎启动前调用：此刻文件尚未被新进程打开，滚动是安全的。"""
    cfg = _load_cfg()
    if not cfg["enabled"]:
        return
    for p in paths:
        try:
            if _size_mb(p) >= cfg["max_mb"]:
                if not _rotate(p, int(cfg["backups"])):
                    _truncate_in_place(p)
        except Exception:  # noqa: BLE001
            continue


def _guard_loop(paths, cfg) -> None:
    interval = max(10.0, float(cfg["interval_s"]))
    while True:
        time.sleep(interval)
        try:
            c = _load_cfg()  # 允许运行期改配置生效
            if not c["enabled"]:
                continue
            for p in paths:
                if _size_mb(p) >= c["max_mb"]:
                    if not _rotate(p, int(c["backups"])):
                        _truncate_in_place(p)
        except Exception:  # noqa: BLE001
            continue


def start_size_guard(*paths: str) -> None:
    """起一个守护线程，定期检查并在超限时滚动/截断。全局只起一次。"""
    global _guard_started
    cfg = _load_cfg()
    if not cfg["enabled"]:
        return
    with _guard_lock:
        if _guard_started:
            return
        _guard_started = True
    t = threading.Thread(target=_guard_loop, args=(tuple(paths), cfg),
                         name="engine-log-guard", daemon=True)
    t.start()
