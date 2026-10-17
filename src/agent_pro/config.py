# -*- coding: utf-8 -*-
"""agent_pro · 配置与开关（三级叠加，缺省即关；红线与 proactive_pack.config 相同）。

读取顺序：DEFAULTS ← config.yaml 的 agent.agent_pro ← data/agent_pro.json 的 flags 段。
只读 config.yaml，绝不回写；任何一级损坏安静落到下一级并打日志（降级不静默）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agent_pro")

DEFAULT_BASE_DIR = Path(__file__).resolve().parents[2] / "data"
STATE_FILENAME = "agent_pro.json"

DEFAULTS: dict[str, Any] = {
    "enabled": False,            # 总开关：关 = 零副作用（缺省）
    "index_ttl_h": 24.0,         # 应用索引缓存有效期（小时）；过期后台重建，本词查询不阻塞
    "verify_timeout_s": 10.0,    # 启动后等待"确认进程存活"的预算（秒）
    "verify_interval_s": 0.5,    # 启动验证轮询间隔
    "file_search_engine": "auto",  # auto | everything | walk
    "es_candidates": "",         # 可选：es.exe 的显式路径（留空自动探测 PATH + 常见位置）
    "find_walk_budget_s": 8.0,   # walk 兜底的时间预算（秒）
    "find_max_results": 30,      # find_file 返回条数上限
    "find_roots": "",            # 可选：分号分隔的检索根目录；留空 = 桌面/下载/文档/图片/视频 + 用户根
    "snapshot_visual": True,     # state_snapshot 允许调 VLM 做一句屏幕概括（有凭据才生效）
    "snapshot_max_windows": 12,  # 状态快照列出的窗口数上限
    "aliases_file": "agent_pro_aliases.json",  # 手工别名（可选，通用兜底的补充，非必需）
}


def _coerce(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on", "是", "开")
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return value
    except Exception:  # noqa: BLE001
        logger.warning("[agent_pro] 配置值 %r 无法转为 %s，回退默认 %r",
                       value, type(default).__name__, default)
        return default


def _read_state_flags(base_dir: Path) -> dict[str, Any]:
    p = Path(base_dir) / STATE_FILENAME
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            logger.warning("[agent_pro] 状态文件顶层不是对象，已忽略：%s", p)
            return {}
        flags = d.get("flags")
        return dict(flags) if isinstance(flags, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 状态文件损坏，已忽略（%s）：%s", p, e)
        return {}


def load_flags(cfg_ns: Optional[Any] = None,
               base_dir: Optional[Path | str] = None) -> dict[str, Any]:
    """合成最终开关表：DEFAULTS ← config.yaml ← 运行时状态文件。"""
    out = dict(DEFAULTS)
    if cfg_ns is not None:
        try:
            section = getattr(getattr(cfg_ns, "agent", None), "agent_pro", None) \
                or getattr(getattr(cfg_ns, "agent_pro", None), "_root", None)
            if section is None and hasattr(cfg_ns, "agent_pro"):
                section = cfg_ns.agent_pro
            if section:
                for k in DEFAULTS:
                    if hasattr(section, k):
                        out[k] = _coerce(getattr(section, k), DEFAULTS[k])
        except Exception:  # noqa: BLE001
            logger.debug("[agent_pro] config.yaml 段读取失败，跳过该级", exc_info=True)
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    for k, v in _read_state_flags(base).items():
        out[k] = _coerce(v, DEFAULTS[k]) if k in DEFAULTS else v
    return out


def write_state_flags(flags: dict[str, Any], base_dir: Optional[Path | str] = None) -> bool:
    """合并写状态文件 flags 段（UI 热切换用；不碰 config.yaml；原子写；防丢更新合并写）。"""
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    p = base / STATE_FILENAME
    try:
        base.mkdir(parents=True, exist_ok=True)
        cur: dict[str, Any] = {}
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    cur = d
            except Exception:  # noqa: BLE001
                cur = {}
        merged = dict(cur.get("flags") or {}) if isinstance(cur.get("flags"), dict) else {}
        merged.update(dict(flags))
        cur["flags"] = merged
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 写入状态文件失败：%s", e)
        return False


# ---------------------------------------------------------------------------
# 手工别名（可选补充：通用索引的兜底，不是主路径）
# ---------------------------------------------------------------------------
def load_aliases(base_dir: Optional[Path | str] = None) -> dict[str, str]:
    """{别名: 标准名或路径}；缺文件 = 空 dict（静默无别名，索引照常工作）。"""
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    p = base / str(DEFAULTS["aliases_file"])
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 别名文件损坏，已忽略（%s）：%s", p, e)
        return {}
