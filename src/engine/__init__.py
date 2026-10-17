"""本地全双工模型生命周期框架（进程不杀，只卸载/重载模型）。

- ModelEngine: 引擎抽象接口
- create_local_omni_engine: 工厂，按 config 选择实现
"""
from .base import ModelEngine
from .factory import create_local_omni_engine

__all__ = ["ModelEngine", "create_local_omni_engine"]
