"""重负载应用自学习库（本地学习 + 云端众包同步）。

设计目标（用户 2026-08-27 提出）：
- 软件第一次遇到未知重负载进程（游戏/剪辑/3D/AI 渲染）时，若所有已知门（instant 清单/
  平台识别/GPU 负载兜底）都没拦住，只能等 GPU 真被吃满时由"负载驱动"兜底清理——这一段
  会卡一下（首遇必然）。
- 但兜底清理那一刻，我们把"元凶进程名"记进本地记忆；下次同一进程再启动 → 直接走秒卸档，
  不再卡。
- 更进一步：把本地学到的进程上报到服务器，服务器聚合所有用户 → 新用户首启即拉取这份库，
  于是**全局每个重负载软件最多只坑一个人一次**（除非改名/换进程）。

本模块是引擎无关的纯数据层：只管"哪些进程是重负载 / 学到了什么 / 怎么和云端同步"，
不碰任何引擎进程（Comni/ollama）的生命周期。

使用方式：
- proactive 在 __init__ 创建 HeavyAppRegistry 并调用 set_registry() 注册为单例；
- foreground_monitor._heavy_app_tier 在判定末尾咨询 get_registry().is_instant(proc)，
  使 learned/cloud 进程也走秒卸档；
- 锁模式状态机在"纯负载驱动触发锁"的那一刻调用 note_load_trigger(元凶exe) 学习；
- 启动首次循环拉取云端库(pull_cloud)，本地新晋进程异步上报(report_promoted)。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 轻量进程黑名单：这些进程即便短暂吃 GPU，也绝不应当被学成"重负载秒卸档"，
# 否则浏览器硬解/办公软件会被误判。命中即跳过 note_load_trigger。
# ─────────────────────────────────────────────────────────────────────────────
_LIGHT_EXACT = frozenset({
    "explorer.exe", "dwm.exe", "svchost.exe", "rundll32.exe", "searchui.exe",
    "shellhost.exe", "taskmgr.exe", "conhost.exe", "system", "idle",
    "audiodg.exe", "csrss.exe", "winlogon.exe".replace("exe", ""), "lsass.exe",
    "services.exe", "fontdrvhost.exe", "dllhost.exe", "sihost.exe",
    "taskhostw.exe", "smss.exe", "wininit.exe",
})

_LIGHT_PREFIXES = (
    "chrome", "msedge", "firefox", "brave", "opera", "iexplore", "edge",
    "explorer", "code", "electron", "svchost", "dwm", "rundll", "searchui",
    "shell", "taskmgr", "conhost", "python", "pythonw", "node", "nodejs",
    "cmd", "powershell", "wegame", "steam", "epicgameslauncher", "egs",
    "wgame", "origin", "eaapp", "battlenet", "xboxapp", "gamingservices",
    "crashpad", "helper", "service", "setup", "installer", "uninstall",
    "update", "launcher", "agent", "daemon", "wps", "qq", "wechat",
    "wxwork", "dingtalk", "feishu", "aliwork", "bytedance", "chrome_crashpad",
    # 【2026-09-06 修复·自激振荡】本软件自身 / 本地模型引擎：绝不能被当成"重负载
    # 前台应用"。前台是 workbuddy.exe（宿主/本软件）时整卡 util 高，是我们自己的
    # local_omni 模型在推理，据此判"有游戏"会导致自我误锁：卸自己→重载→再卸。
    "workbuddy", "comni", "llama", "ollama",
)


def _is_light_app(proc: str) -> bool:
    """进程是否为"轻量/系统/平台启动器/常用办公通信"——绝不应被学成重负载。"""
    if not proc:
        return True
    p = proc.lower().strip()
    if not p:
        return True
    if p in _LIGHT_EXACT:
        return True
    for pre in _LIGHT_PREFIXES:
        if p.startswith(pre):
            return True
    # 平台启动器（即便精确名不含前缀，如 wegameplayer.exe）
    if p in ("wegameplayer.exe", "wegame.exe", "steam.exe", "epicgameslauncher.exe",
             "egs.exe", "origin.exe", "eaapp.exe", "battlenet.exe", "xboxapp.exe",
             "gamingservices.exe"):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 单例：foreground_monitor 需要在模块级咨询注册表，但注册表由 proactive 创建
# （proactive 知道 config 与项目根目录）。用懒单例打破模块依赖。
# ─────────────────────────────────────────────────────────────────────────────
_REGISTRY: "Optional[HeavyAppRegistry]" = None


def get_registry() -> "Optional[HeavyAppRegistry]":
    return _REGISTRY


def set_registry(reg: "Optional[HeavyAppRegistry]") -> None:
    global _REGISTRY
    _REGISTRY = reg


# 云端库 JSON schema（供服务器实现参考）：
#   GET  {cloud_url}                -> {"proc.exe": {"count": int, "tier": "instant"}, ...}
#   POST {cloud_url}/report         -> body {"apps": ["proc.exe", ...]}  (本机新晋秒卸档)
# 服务器只需实现这两个端点，把 report 收到的 app 累加进全局库并对外提供 GET。
class HeavyAppRegistry:
    """重负载应用注册表：本地学习库 + 云端众包库，线程安全。"""

    def __init__(
        self,
        data_dir: str,
        promote_threshold: int = 1,
        cloud_url: str = "",
        cloud_sync: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._data_dir = data_dir
        self._learned_path = os.path.join(data_dir, "heavy_apps.learned.json")
        self._cloud_path = os.path.join(data_dir, "heavy_apps.cloud.json")
        self._promote_threshold = max(1, int(promote_threshold))
        self._cloud_url = (cloud_url or "").strip()
        self._cloud_sync = bool(cloud_sync)
        self._learned: dict[str, dict] = {}
        self._cloud: dict[str, dict] = {}
        self._pending: list[str] = []   # 本地新晋、尚未上报云端的进程
        self._last_flush = 0.0
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────────
    def _load(self) -> None:
        for path, store in ((self._learned_path, self._learned),
                            (self._cloud_path, self._cloud)):
            try:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        store.update({k.lower(): v for k, v in data.items()})
            except Exception:  # noqa: BLE001
                logger.warning("加载重负载应用库失败 %s", path, exc_info=True)

    def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_flush < 10:
            return
        self._last_flush = now
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._learned_path, "w", encoding="utf-8") as f:
                json.dump(self._learned, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            logger.warning("写入本地学习库失败", exc_info=True)

    def _flush_cloud(self) -> None:
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._cloud_path, "w", encoding="utf-8") as f:
                json.dump(self._cloud, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            logger.warning("写入云端缓存失败", exc_info=True)

    # ── 查询 ────────────────────────────────────────────────────────────────
    def is_instant(self, proc: str) -> bool:
        """进程是否应走"秒卸档"（learned 已提升 或 云端库标 instant）。"""
        if not proc:
            return False
        p = proc.lower()
        with self._lock:
            rec = self._learned.get(p)
            if rec and rec.get("promoted"):
                return True
            cld = self._cloud.get(p)
            if cld and cld.get("tier") == "instant":
                return True
        return False

    # ── 学习（核心）────────────────────────────────────────────────────────
    def note_load_trigger(self, proc: str) -> bool:
        """记录一个"经 GPU 负载兜底发现"的疑似重负载进程。

        返回是否**本次新晋为秒卸档**（仅新晋时才需上报云端）。

        调用于"纯负载驱动触发锁"的那一刻（已确认 GPU 被吃满足够久、且不是已知
        instant/platform/force 触发），故误报率极低。轻量进程(浏览器/办公/启动器)
        直接拒绝，杜绝误学。

        promote_threshold：连续/累计触发几次才永久提升为秒卸档。默认 1（首遇即学，
        满足用户"每个软件只卡一次"的诉求）；可调高以更保守。
        """
        if not proc:
            return False
        p = proc.lower().strip()
        if not p or _is_light_app(p):
            return False
        promoted = False
        with self._lock:
            rec = self._learned.setdefault(
                p, {"hits": 0, "first_seen": 0.0, "last_seen": 0.0, "promoted": False})
            if rec.get("promoted"):
                return False
            rec["hits"] = int(rec.get("hits", 0)) + 1
            _now = time.time()
            rec["last_seen"] = _now
            if not rec.get("first_seen"):
                rec["first_seen"] = _now
            if rec["hits"] >= self._promote_threshold:
                rec["promoted"] = True
                promoted = True
                if p not in self._pending:
                    self._pending.append(p)
        self._flush()
        if promoted:
            logger.info(
                "学习：进程 %s 经 %d 次高负载确认，提升为秒卸档"
                "（下次启动即秒卸，并将上报云端众包库）", p, self._promote_threshold)
        return promoted

    # ── 云端同步（脚手架，服务器就绪前为 no-op）──────────────────────────────
    async def pull_cloud(self) -> None:
        """拉取云端聚合库（新用户首启即带全量，无需自己卡一次）。"""
        if not (self._cloud_sync and self._cloud_url):
            return
        try:
            import urllib.request
            req = urllib.request.Request(
                self._cloud_url, headers={"User-Agent": "WorkBuddy-HeavyApps"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            with self._lock:
                if isinstance(data, dict):
                    self._cloud.update({k.lower(): v for k, v in data.items()})
            self._flush_cloud()
            logger.info("已从云端拉取重负载库（%d 条）", len(self._cloud))
        except Exception as e:  # noqa: BLE001
            logger.debug("拉取云端重负载库失败(忽略)：%s", e)

    async def report_promoted(self) -> None:
        """把本地新晋的秒卸档进程上报云端（众包积累）。失败则重回待上报队列。"""
        if not (self._cloud_sync and self._cloud_url):
            return
        with self._lock:
            batch = list(self._pending)
            self._pending.clear()
        if not batch:
            return
        try:
            import urllib.request
            url = self._cloud_url.rstrip("/") + "/report"
            payload = json.dumps({"apps": batch}).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "WorkBuddy-HeavyApps"},
                method="POST")
            urllib.request.urlopen(req, timeout=5).close()
            logger.info("已上报 %d 个新晋重负载进程到云端", len(batch))
        except Exception as e:  # noqa: BLE001
            with self._lock:
                # 放回队列，下次再报
                for b in batch:
                    if b not in self._pending:
                        self._pending.append(b)
            logger.debug("上报云端失败(将重试)：%s", e)
