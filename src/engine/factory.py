"""引擎工厂：按 config 选择并构造本地全双工引擎。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from .base import ModelEngine
from .comni_managed import ComniManagedEngine
from .own_llama_server import OwnLlamaServerEngine

logger = logging.getLogger(__name__)


def _cfg_get(obj, key, default=None):
    """命名空间/字典通用的取值。pro_cfg 是 config.py 的 SimpleNamespace 或 dict。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cfg_section(obj, key) -> dict:
    """取嵌套段（local_omni_engine）为普通 dict。兼容 dict 与 SimpleNamespace。"""
    val = _cfg_get(obj, key)
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, SimpleNamespace):
        # vars(SimpleNamespace) 才是真实 {key: value} 映射（key 不带下划线前缀）
        return {k: v for k, v in vars(val).items() if not k.startswith("_")}
    return {}


def create_local_omni_engine(pro_cfg, ref_audio: str) -> ModelEngine:
    """根据 proactive 配置构造本地全双工引擎。

    pro_cfg: config['proactive']，可能是 dict，也可能是 config.py 用 SimpleNamespace
        包装的命名空间（cfg.ns.proactive）。本函数对两种形式都安全。
    ref_audio: 达妮娅声音克隆参考音频路径。

    重要：pro_cfg 是 SimpleNamespace 时，绝不能 `if not isinstance(pro_cfg, dict):
    pro_cfg = {}`——那会把整个命名空间清空，导致 local_omni_engine_type 读不到、
    静默回退 comni_managed（等已运行的 Comni.exe，而 Comni 没开 → 模型永不挂载）。
    """
    eng_cfg = dict(_cfg_section(pro_cfg, "local_omni_engine"))
    eng_cfg["ref_audio_path"] = ref_audio or ""
    # 【2026-09-20 动态挂载】显存档位从 proactive.duplex_vram_profile 读
    # （放在 proactive 顶层而非 local_omni_engine 段内：它描述的是"本次要不要省显存"
    #  这个业务选择，不是引擎的固有连接参数）。缺省 stock_8k = 不改动任何东西。
    _vp = _cfg_get(pro_cfg, "duplex_vram_profile", "stock_8k")
    eng_cfg["vram_profile"] = str(_vp or "stock_8k").strip() or "stock_8k"
    # 【2026-09-20 源码补丁】两处 Comni 源码硬编码的显存优化，默认全关（零改动）：
    #   tts   —— use_tts 环境变量化（关掉省 ≈2028MB；本应用走云端音色，引擎音频即弃）
    #   reuse —— media_type 切换复用模型不重启（≈30~60s → ≈3s）
    eng_cfg["comni_patch_enable_tts"] = bool(
        _cfg_get(pro_cfg, "duplex_comni_patch_disable_tts", False))
    eng_cfg["comni_patch_enable_reuse_model"] = bool(
        _cfg_get(pro_cfg, "duplex_comni_patch_reuse_model", False))

    etype = (_cfg_get(pro_cfg, "local_omni_engine_type") or "comni_managed")
    etype = str(etype).strip().lower()
    if etype == "own_llama_server":
        logger.info("创建本地全双工引擎: OwnLlamaServerEngine (自托管, 进程不杀)")
        return OwnLlamaServerEngine(eng_cfg)

    logger.info("创建本地全双工引擎(兼容): ComniManagedEngine (连已运行 Comni.exe)")
    eng_cfg["comni_exe"] = _cfg_get(pro_cfg, "duplex_local_omni_exe", "") or ""
    return ComniManagedEngine(eng_cfg)
