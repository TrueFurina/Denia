"""ComniManagedEngine：兼容旧模式——连已在运行的 Comni.exe。

当 config.local_omni_engine_type=comni_managed 时启用。此时本地模型由用户手动打开的
Comni.exe 托管；引擎只做"镜像层"封装：卸载=taskkill Comni 整树（原行为），
重载=拉起 Comni.exe。主程序同样不退出。

注意：此模式卸载仍会杀 Comni.exe GUI（因为 Comni 把模型进程作为自己的子进程树）。
要真正做到"进程不杀"，请用 own_llama_server 引擎。
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Set

from ._proc import _CREATE_NO_WINDOW, _capture_text, _health_url_ok, kill_tree
from .base import ModelEngine

logger = logging.getLogger(__name__)


class ComniManagedEngine(ModelEngine):
    def __init__(self, cfg: dict) -> None:
        cfg = cfg or {}
        self._exe = (cfg.get("comni_exe") or "").strip()
        self._ref_audio = (cfg.get("ref_audio_path") or "").strip()
        self._endpoint = "https://localhost:8006"
        self._loaded = False

    @property
    def mode(self) -> str:
        return "local_omni"

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def _comni_pids(self) -> Set[int]:
        # tasklist 输出为 GBK，用 _capture_text 统一解码（避免 None+splitlines 崩溃）
        out = _capture_text(
            ["tasklist", "/FI", "IMAGENAME eq Comni.exe", "/FO", "CSV"], timeout=8)
        pids: Set[int] = set()
        for line in out.splitlines():
            if "Comni.exe" not in line:
                continue
            for part in line.split(","):
                p = part.strip().strip('"')
                if p.isdigit():
                    pids.add(int(p))
        return pids

    def start(self) -> None:
        if self.is_model_loaded():
            return
        if not self._exe or not os.path.isfile(self._exe):
            logger.warning("Comni 路径无效(%s)，无法启动", self._exe)
            return
        subprocess.Popen(
            [self._exe],
            cwd=os.path.dirname(self._exe) or None,
            creationflags=_CREATE_NO_WINDOW | 0x00000200,  # NO_WINDOW | DETACHED
        )
        logger.info("已拉起 Comni（%s）", self._exe)

    def stop(self) -> None:
        logger.info("引擎停止：退出 Comni 进程树")
        for pid in self._comni_pids():
            kill_tree(pid)

    def unload_model(self) -> None:
        logger.info("引擎卸载模型(comni_managed)：退出 Comni 进程树释放显存")
        for pid in self._comni_pids():
            kill_tree(pid)

    def reload_model(self) -> None:
        self.start()

    def is_model_loaded(self) -> bool:
        return _health_url_ok("https://127.0.0.1:8006/health", timeout=2.0)

    def model_process_pids(self) -> Set[int]:
        # comni_managed 模式下，锁判定仍靠 proactive 内按映像名枚举 Comni 树，
        # 这里返回空，避免重复。
        return set()
