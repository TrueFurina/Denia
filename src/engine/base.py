"""本地全双工模型引擎抽象基类。

核心思想（用户 2026-08-27 规划）：**卸载/重载模型是一层可被框架托管的生命周期，
而非"杀死整个应用进程"**。本软件主进程（框架）始终存活，引擎只负责把本地推理
进程组拉起 / 停止 / 重载，从而把显存腾给游戏、游戏结束再拿回来。

可实现的引擎：
- OwnLlamaServerEngine：本软件自托管 Comni 的 worker/gateway/llama-server（pubJavis 式）。
  卸载=停整组进程释放显存；重载=重拉整组；Comni.exe 不参与（用户不再手动开）。
- ComniManagedEngine：兼容旧模式，连已在运行的 Comni.exe；卸载=taskkill 整树（原行为）。
"""
from __future__ import annotations

import abc
from typing import Set

logger = __app_logger__ = None  # placeholder; real logger below
import logging

logger = logging.getLogger(__name__)


class ModelEngine(abc.ABC):
    """本地全双工模型引擎接口。"""

    @property
    @abc.abstractmethod
    def mode(self) -> str:
        """该引擎对应的 duplex_mode 值（恒为 'local_omni'）。"""

    @property
    @abc.abstractmethod
    def endpoint(self) -> str:
        """双工客户端连接地址（LocalOmniDuplexClient 用）。"""

    @abc.abstractmethod
    def start(self) -> None:
        """拉起引擎（首次挂载模型 / 重载）。幂等：已运行则跳过。非阻塞启动 + 等待就绪。"""

    @abc.abstractmethod
    def stop(self) -> None:
        """最终清理（软件退出时调用）：卸载模型并停掉所有托管进程。"""

    @abc.abstractmethod
    def unload_model(self) -> None:
        """卸载模型：释放本地推理占用的显存，但框架/主程序不退出（"进程不杀"）。"""

    @abc.abstractmethod
    def reload_model(self) -> None:
        """重载模型：把本地推理进程组重新拉起。"""

    @abc.abstractmethod
    def is_model_loaded(self) -> bool:
        """本地模型当前是否已挂载且端点健康。"""

    def model_process_pids(self) -> Set[int]:
        """本引擎托管的、持有显存的进程 PID 集合（含子进程树）。

        供锁模式判定排除"本地模型自身显存"用——避免把自家模型算成"外部重负载"
        导致误锁。默认空；own_llama_server 会返回 gateway+worker+llama-server 整树。
        """
        return set()
