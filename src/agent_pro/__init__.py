# -*- coding: utf-8 -*-
"""agent_pro · 通用 Agent 能力提升包。

把 2026-10-16 调研定轨的「通用 Agent 三件基础设施」做成独立技能包：
  1. 应用索引化检索（app_index）——替代硬编码映射与每次 2-20s 的实时 PowerShell，
     启动时一次缓存 开始菜单 lnk + 注册表卸载表 + App Paths + Get-StartApps(UWP)，
     本地毫秒级模糊匹配；别人电脑上 / 换版本 / 白名单外的软件都能找到。
  2. 启动引擎（launch）——explorer 委托（launcher 型应用唯一能起的通道）
     + 启动后**真验证进程存活**，绝不谎报成功。
  3. 轮询等待（wait_for）+ 状态快照（state_snapshot）+ 文件检索（find_file）。

包测先行：selftest 全桩离线；e2e 用真实 LLM/VLM，全绿后再接主软件。
约定与 src/proactive_pack 完全一致：独立目录、三级开关缺省关、不回写 config.yaml。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("agent_pro")

_PACK_FLAG = "_agent_pro_installed"


def _flags(host, override=None):
    from . import config as _config
    cfg_ns = None
    if host is not None:
        cfg_ns = getattr(host, "cfg_ns", None) or getattr(host, "_cfg_ns", None)
    flags = _config.load_flags(cfg_ns)
    if override:
        flags.update({k: v for k, v in dict(override).items() if k in _config.DEFAULTS or True})
    return flags


def install(host=None, *, flags_override: dict | None = None) -> bool:
    """把本包挂进 src.llm.agent_tools。幂等；总开关关闭 = 零副作用直接返回。

    host：主软件运行对象（可选；仅用于读 config 命名空间）。
    flags_override：测试/调试用，显式覆盖开关（不影响状态文件）。
    """
    try:
        import src.llm.agent_tools as at
    except Exception as e:  # noqa: BLE001
        logger.error("[agent_pro] 无法导入 agent_tools，未安装：%s", e)
        return False

    flags = _flags(host, flags_override)
    if not flags.get("enabled"):
        logger.info("[agent_pro] enabled=false，跳过安装（行为与未安装完全一致）")
        return False
    if getattr(at, _PACK_FLAG, False):
        return True  # 已装，幂等

    from .specs import PACK_TOOL_SPECS, PACK_TOOL_NAMES

    # 0) DPI 一致性（UIA 坐标 == 鼠标坐标）：主软件已 SetProcessDPIAware，这里是兜底，
    #    独立运行/包测场景下没有它就会出现"150% 缩放 → 点歪"。
    try:
        from .screen import _ensure_dpi_aware
        logger.info("[agent_pro] DPI 状态：%s", _ensure_dpi_aware())
    except Exception as e:  # noqa: BLE001
        logger.debug("[agent_pro] DPI 检查跳过：%s", e)

    # 1) 工具 schema 追加（uninstall 时按名移除）
    _installed_specs = [t for t in PACK_TOOL_SPECS if t not in at.TOOL_SPECS]
    at.TOOL_SPECS.extend(_installed_specs)

    # 2) 后端就绪探针 / 超时表（字典合并，无代码侵入）
    at._TOOL_BACKEND_PROBES.update({
        "state_snapshot": (lambda: True, ""),
        "wait_for": (lambda: True, ""),
        "find_file": (lambda: True, ""),
        "ui_elements": (lambda: True, ""),
        "click_element": (lambda: True, ""),
        "search_in_app": (lambda: True, ""),
        "media_state": (lambda: True, ""),
    })
    at.TOOL_TIMEOUTS.update({
        "state_snapshot": 60.0,
        "wait_for": 90.0,
        "find_file": 45.0,
        "ui_elements": 30.0,
        "click_element": 45.0,
        "search_in_app": 30.0,
        "media_state": 20.0,
    })

    # 3) 派发包装：包内工具优先
    orig_core = at._dispatch_core

    async def patched_dispatch(name: str, args: dict, ctx: dict) -> str:
        if name in PACK_TOOL_NAMES:
            from .specs import dispatch_pack_tool
            return await dispatch_pack_tool(name, args or {}, ctx or {}, flags)
        return await orig_core(name, args, ctx)

    patched_dispatch._agent_pro_wrapped = True
    patched_dispatch._orig_core = orig_core
    at._dispatch_core = patched_dispatch

    # 4) open_app 增强：_fuzzy_launch 前置应用索引（通用解法替代硬编码映射）
    orig_fuzzy = at._fuzzy_launch

    def fuzzy_with_index(target: str, a11y: bool = False) -> str:
        try:
            from .launch import index_launch
            r = index_launch(target, a11y=a11y, flags=flags)
            if r:
                return r
        except Exception:  # noqa: BLE001
            logger.exception("[agent_pro] index_launch 异常，回落原逻辑")
        return orig_fuzzy(target, a11y=a11y)

    fuzzy_with_index._agent_pro_wrapped = True
    fuzzy_with_index._orig_fn = orig_fuzzy
    at._fuzzy_launch = fuzzy_with_index

    setattr(at, _PACK_FLAG, True)
    setattr(at, "_agent_pro_uninstall", lambda: uninstall(at))
    logger.info("[agent_pro] 已安装：+5 工具(find_file/wait_for/state_snapshot/ui_elements/click_element)，open_app 走应用索引")
    return True


def uninstall(at=None) -> None:
    """热回滚：恢复安装前的 agent_tools 状态（幂等）。"""
    try:
        import src.llm.agent_tools as at_mod
    except Exception:  # noqa: BLE001
        return
    mod = at or at_mod
    if not getattr(mod, _PACK_FLAG, False):
        return
    from .specs import PACK_TOOL_NAMES
    mod.TOOL_SPECS[:] = [t for t in mod.TOOL_SPECS
                         if (t.get("function") or {}).get("name") not in PACK_TOOL_NAMES]
    for n in ("state_snapshot", "wait_for", "find_file", "ui_elements", "click_element"):
        mod._TOOL_BACKEND_PROBES.pop(n, None)
        mod.TOOL_TIMEOUTS.pop(n, None)
    if getattr(mod._dispatch_core, "_agent_pro_wrapped", False):
        mod._dispatch_core = mod._dispatch_core._orig_core
    if getattr(mod._fuzzy_launch, "_agent_pro_wrapped", False):
        mod._fuzzy_launch = mod._fuzzy_launch._orig_fn
    setattr(mod, _PACK_FLAG, False)
    logger.info("[agent_pro] 已卸载")
