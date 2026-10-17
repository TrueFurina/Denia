# -*- coding: utf-8 -*-
"""主动互动包（proactive_pack）—— 一个**可整体开关的额外包**。

为什么单独成包：
  · 主软件同时在推进别的开发；主动互动这条线要能**独立开发、独立开关、独立回滚**，
    不该和别的分支抢同一个大文件（`src/companion/proactive.py` 已 647KB）。
  · 包内自成体系：配置 / 去重账本 / 情境事实包 / 四类触发器 / 提示词 / 自测，
    只通过 `install()` 的**一个包装点**与宿主相接。

接入方式（二选一）：
  A) **零文件改动（推荐）** —— 在软件启动处加两行：
        from src.proactive_pack import install
        install(proactive_instance)
     本包在**实例上**包装 `check_and_trigger`：开关为关时行为与装之前**完全一致**。
  B) 显式改 `proactive.py`：在 `check_and_trigger` 的关系闸门之前插入
        `if await self._proactive_pack_hook(): return`
     （见 README 的"B 方案"片段。）

开关：`data/proactive_pack.json` 的 `flags.enabled`（默认 false）+ 可选 config.yaml
      的 `proactive.proactive_pack`。**缺省即关**，装上不打开 = 什么都不做。

红线（与项目一致）：不编造她的离线生活 / 反 PUA / 只新增不改引擎底座 /
失败降级不静默 / 每日总量不变。
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Optional

from .pack import ProactivePack
from .config import load_flags, write_state_flags, DEFAULTS, DEFAULT_BASE_DIR
from .marks import MarksStore
from .situation import collect, Situation
from .triggers import Trigger, Ctx, evaluate
from . import festivals, prompts, feed, judge, engine_llm

__all__ = [
    "ProactivePack", "install", "uninstall", "build_proactive_pack",
    "load_flags", "write_state_flags", "DEFAULTS", "MarksStore",
    "collect", "Situation", "Trigger", "Ctx", "evaluate", "festivals", "prompts", "feed",
    "judge", "engine_llm",
]

logger = logging.getLogger("proactive_pack")


def build_proactive_pack(host: Any = None, *, llm: Any = None, cfg_ns: Any = None,
                         base_dir: Optional[Path | str] = None,
                         name: str = "", judge_llm: Any = None) -> ProactivePack:
    """只构建、不接线。给测试 / 只读诊断用。

    `judge_llm`：审查器专用 LLM（不传则按 `judge_provider` 取 config 端口，再不然与生成同一个）。
    """
    return ProactivePack(host=host, llm=llm, cfg_ns=cfg_ns, base_dir=base_dir,
                         name=name, judge_llm=judge_llm)


def install(host: Any, *, llm: Any = None, cfg_ns: Any = None,
            base_dir: Optional[Path | str] = None, name: str = "",
            judge_llm: Any = None) -> Optional[ProactivePack]:
    """把本包挂到宿主实例上（包装 `check_and_trigger`）。幂等，重复调用返回同一个包。

    绝不会因为本包出错而影响宿主的原有主动逻辑：包装层捕获一切异常后**放行原方法**。
    """
    if host is None:
        logger.warning("[proactive_pack] install() 收到 host=None，忽略")
        return None
    existing = getattr(host, "_proactive_pack", None)
    if isinstance(existing, ProactivePack):
        return existing

    pack = ProactivePack(
        host=host, llm=llm,
        cfg_ns=cfg_ns if cfg_ns is not None else getattr(host, "_cfg", None),
        base_dir=base_dir, name=name, judge_llm=judge_llm,
    )

    try:
        orig = host.check_and_trigger

        @functools.wraps(orig)
        async def _wrapped(*a: Any, **kw: Any) -> Any:
            try:
                if pack.enabled and await pack.maybe_emit():
                    return None          # 本包已开口 → 宿主通用引信让位（不重复开口）
            except Exception:  # noqa: BLE001
                logger.warning("[proactive_pack] 包装层异常，回退宿主原逻辑", exc_info=True)
            return await orig(*a, **kw)

        host.check_and_trigger = _wrapped
        host._proactive_pack = pack
        pack._orig_check_and_trigger = orig          # 供热回滚（uninstall）还原
        logger.info("[proactive_pack] 已接入（enabled=%s，状态文件=%s）",
                    pack.enabled, pack.marks.path)
        if pack.enabled:
            # 首次启用时把节日表导出成可编辑文件（已存在则不覆盖）
            try:
                festivals.ensure_festival_file(base_dir or DEFAULT_BASE_DIR)
            except Exception:  # noqa: BLE001
                logger.debug("[proactive_pack] 导出节日文件失败（无碍）", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("[proactive_pack] 接入失败（宿主保持原状）", exc_info=True)
        return None
    return pack


def uninstall(host: Any) -> bool:
    """摘掉包装，恢复宿主原方法（用于热回滚 / 排查）。返回是否真的摘掉。"""
    if host is None:
        return False
    pack = getattr(host, "_proactive_pack", None)
    if not isinstance(pack, ProactivePack):
        return False
    orig = getattr(pack, "_orig_check_and_trigger", None)
    try:
        if orig is not None:
            host.check_and_trigger = orig
        else:
            # 没有留原方法引用时，退而求其次：从类上重新取回绑定方法
            host.check_and_trigger = type(host).check_and_trigger.__get__(host, type(host))
        host._proactive_pack = None
        logger.info("[proactive_pack] 已摘除包装，恢复宿主原方法")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[proactive_pack] 摘除失败", exc_info=True)
        return False
