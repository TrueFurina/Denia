"""OwnLlamaServerEngine：本软件自托管 Comni 的 worker/gateway/llama-server（pubJavis 式）。

为什么这样设计（用户 2026-08-27 规划"进程不杀，只卸载/重载模型"）：
- Comni 自身没有热卸载 API，释放显存唯一方式是杀 llama-server 进程。但 Comni.exe
  是把 worker/gateway/llama-server 一起打包拉起的 GUI——直接 taskkill 整树会连 GUI 一起杀掉。
- 解法：我们**绕过 Comni.exe GUI**，用 Comni 自带的 python-embed 直接拉起它的
  `worker.py` + `gateway.py`（源码位于 Comni/_internal/resources/apps/server）。
  worker.py 启动时会**自己** spawn llama-server 并调 omni_init 加载 TTS/vision——
  我们不必复刻 omni_init 请求体，全部复用 Comni 已验证的逻辑。
- 卸载模型 = 停掉我们托管的整组进程（gateway+worker+llama-server 子树）→ 显存释放；
  重载模型 = 重拉整组。整条链路里**没有 Comni.exe**，本软件主进程始终存活。

关键路径（实测确认）：
- python-embed: H:\\Comni\\_internal\\resources\\python-embed\\python.exe
- server_dir:   H:\\Comni\\_internal\\resources\\apps\\server  (worker.py/gateway.py/config.json)
- llama-server: H:\\Comni\\_internal\\resources\\build\\bin\\Release\\llama-server.exe
- gateway 端口 8006（双工客户端连这里）、worker 端口 22700、llama-server 端口 19060(+gpu_id)
- worker 通过 `from config import get_config` 读同目录 config.json 得到 model_dir 等
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional, Set

from ._proc import (
    child_pids, find_pid_on_port, kill_pid_on_port, kill_tree,
    launch_process, wait_for_health, _health_url_ok,
    find_pids_by_exe_substr,
)
from .base import ModelEngine

logger = logging.getLogger(__name__)


class OwnLlamaServerEngine(ModelEngine):
    def __init__(self, cfg: dict) -> None:
        cfg = cfg or {}
        self._resources_dir = (cfg.get("comni_resources_dir") or "").strip()
        self._python = (cfg.get("python_embed") or "").strip()
        self._server_dir = (cfg.get("server_dir") or "").strip()
        self._gateway_port = int(cfg.get("gateway_port", 8006))
        self._worker_port = int(cfg.get("worker_port", 22700))
        self._gpu_id = int(cfg.get("gpu_id", 0))
        self._auto_start = bool(cfg.get("auto_start", True))
        self._health_timeout = float(cfg.get("health_timeout_s", 180))
        self._kill_stray = bool(cfg.get("kill_stray_on_port", True))
        self._ref_audio = (cfg.get("ref_audio_path") or "").strip()
        # 【2026-09-20 动态挂载】显存档位：在拉起 worker 之前把 ctx_size 等写进
        # Comni 的 apps/server/config.json（worker 无 --ctx-size CLI，唯一通道）。
        # 默认 "stock_8k" = 不改动，行为与引入本机制之前完全一致。
        self._vram_profile = (cfg.get("vram_profile") or "stock_8k").strip()
        self._vram_last_result: Dict[str, object] = {}
        # 【2026-09-20 源码补丁】两处 Comni 源码硬编码优化，默认 False = 零改动：
        #   tts   —— use_tts 环境变量化（关掉省 ≈2028MB）
        #   reuse —— media_type 切换复用模型不重启（≈30~60s → ≈3s）
        self._patch_tts = bool(cfg.get("comni_patch_enable_tts", False))
        self._patch_reuse = bool(cfg.get("comni_patch_enable_reuse_model", False))
        self._patch_last_result: Dict[str, object] = {}
        self._endpoint = f"https://localhost:{self._gateway_port}"
        # 我们托管的进程：gateway + worker（worker 再 spawn llama-server 子树）
        self._procs: Dict[str, Optional[object]] = {}
        self._loaded = False
        # 【2026-09-27 防显存叠加】start() 重入锁 + 启动中标志。
        # 背景：warm_local_model()（后台线程）与 _run_local_omni_duplex（进入双工）都会调
        # 同一个 eng.start()；若前者还在 wait_for_health 加载中（此时 is_model_loaded=False），
        # 后者又调一次 → _launch() 二次执行、覆盖 self._procs，旧进程脱离追踪仍加载模型 →
        # 同进程两组模型叠加 ≈17GB → OOM → 显存被回收。本锁保证“加载途中再次 start 绝不再拉一组”。
        self._start_lock = threading.Lock()
        self._starting = False

    # ---------- 属性 ----------
    @property
    def mode(self) -> str:
        return "local_omni"

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def auto_start(self) -> bool:
        return self._auto_start

    # ---------- 进程管理 ----------
    def _launch(self) -> None:
        py = self._python
        sdir = self._server_dir
        if not py or not os.path.isfile(py):
            raise RuntimeError(f"python-embed 不存在: {py}")
        if not sdir or not os.path.isdir(sdir):
            raise RuntimeError(f"server_dir 不存在: {sdir}")

        gw_cmd = [py, "gateway.py", "--port", str(self._gateway_port),
                  "--workers", f"127.0.0.1:{self._worker_port}"]
        wk_cmd = [py, "worker.py", "--port", str(self._worker_port),
                  "--gpu-id", str(self._gpu_id), "--worker-index", "0"]
        # 达妮娅声音克隆：传给 worker 作默认 ref_audio，omni_init 时注入声线
        if self._ref_audio and os.path.isfile(self._ref_audio):
            wk_cmd += ["--ref-audio-path", self._ref_audio]

        # 【2026-09-20 动态挂载】写档位必须在 worker 启动**之前**：
        # worker.py `main()` 里 get_config() 是进程内单例，启动后再改文件不生效。
        # 失败不阻断启动（回落到 Comni 原有配置），仅记日志。
        self._apply_vram_profile()
        # 【2026-09-20 源码补丁】同上，必须在 worker 启动之前（worker 会 import 该模块）。
        # 失败不阻断启动，仅记日志。
        self._apply_source_patch()

        # 子进程日志落盘（避免 stdout=PIPE 管道写满卡死 worker；同时可查 TTS/SPEAK 计时）
        proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_dir = os.path.join(proj_root, "logs", "engine")
        gw_log = os.path.join(log_dir, "gateway.log")
        wk_log = os.path.join(log_dir, "worker.log")

        # 【2026-09-18 日志卡死修复】启动前先按大小滚动，并起一个大小守护线程。
        # 背景：worker.log 曾涨到 16.9GB（约 3MB/s）且永不轮转，把整机拖垮。
        # 回滚：把 engine_log_guard 的 enabled 置 False（或删掉这两行）。
        try:
            from .engine_log_guard import rotate_on_startup, start_size_guard
            rotate_on_startup(gw_log, wk_log)
        except Exception as _e:  # noqa: BLE001
            logger.warning("引擎日志轮转不可用（不阻断启动）: %s", _e)

        logger.info("引擎启动 gateway: %s", " ".join(gw_cmd))
        self._procs["gateway"] = launch_process(gw_cmd, cwd=sdir, log_path=gw_log)
        logger.info("引擎启动 worker:  %s", " ".join(wk_cmd))
        self._procs["worker"] = launch_process(
            wk_cmd, cwd=sdir, log_path=wk_log, env=self._worker_env())
        try:
            from .engine_log_guard import start_size_guard
            start_size_guard(gw_log, wk_log)
        except Exception as _e:  # noqa: BLE001
            logger.warning("引擎日志大小守护未启动（不阻断）: %s", _e)

    def _free_ports(self) -> None:
        """启动前清理残留端口占用（通常是上次没退干净的 Comni/llama-server）。"""
        for port in (self._gateway_port, self._worker_port):
            pid = find_pid_on_port(port)
            if pid:
                if self._kill_stray:
                    logger.warning("端口 %s 被残留进程(PID %s)占用，清理之", port, pid)
                    kill_pid_on_port(port)
                else:
                    raise RuntimeError(f"端口 {port} 被占用(PID {pid})，未清理")

    def _kill_stray_model_procs(self) -> None:
        """启动前清理“同路径残留的 llama-server.exe”。

        背景：上次崩溃/异常退出时，worker spawn 的 llama-server 可能脱离 self._procs
        追踪、端口已被 OS 回收，但显存仍加载着 ≈8GB 模型。按端口查不到它，于是
        下次 start() 以为没运行、又拉一组 → 两组叠加 ≈16.5GB → 越过显存红线 OOM 崩。
        这正是“偶发冲到 15.3GB、重启后回 8.27GB”的根因（重启把孤儿进程清掉）。

        做法：按本 Comni build 目录下 llama-server.exe 的完整路径匹配，杀掉所有命中
        且**不在** self 现有托管子树里的进程。精准限定目录，不误杀其它实例/主进程。
        """
        if not self._kill_stray:
            return
        # build 目录：server_dir(...\resources\apps\server) → ...\resources\build\bin\Release
        build_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.dirname(self._server_dir)),
            "build", "bin", "Release"))
        needle = os.path.join(build_dir, "llama-server.exe").lower()
        try:
            stray = find_pids_by_exe_substr(needle)
        except Exception:  # noqa: BLE001
            stray = set()
        if not stray:
            return
        # 排除本引擎已托管进程及其子树（避免自杀）
        own: Set[int] = set()
        for p in self._procs.values():
            if p is not None and p.poll() is None:
                own.add(p.pid)
        if own:
            own |= child_pids(own)
        stray -= own
        for pid in sorted(stray):
            logger.warning("清理同路径残留 llama-server(PID %s)，防显存叠加", pid)
            kill_tree(pid)

    # ---------- 显存档位（动态挂载） ----------
    def _apply_vram_profile(self) -> Dict[str, object]:
        """在 worker 启动前把显存档位写进 Comni config.json。

        复用 `_server_dir` / `_python` 解析路径；任何失败都只记日志、不抛异常，
        保证"档位机制坏掉"不会导致引擎起不来（回落到 Comni 原有配置）。
        """
        self._vram_last_result = {"ok": False, "reason": "未执行"}
        try:
            from .vram_profile import apply_profile
        except Exception as e:  # noqa: BLE001
            logger.warning("显存档位模块不可用（跳过，按 Comni 原配置启动）: %s", e)
            return self._vram_last_result
        try:
            res = apply_profile(
                self._server_dir,
                self._vram_profile,
                python_embed=self._python,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("应用显存档位异常（按 Comni 原配置启动）: %s", e)
            self._vram_last_result = {"ok": False, "reason": str(e)}
            return self._vram_last_result

        self._vram_last_result = res
        if res.get("ok"):
            ch = res.get("changes") or {}
            if ch:
                logger.info(
                    "显存档位已生效 [%s]: %s（备份 %s）",
                    self._vram_profile,
                    {k: v.get("to") for k, v in ch.items()},
                    res.get("backup"),
                )
            else:
                logger.info("显存档位 [%s]: %s", self._vram_profile, res.get("reason"))
        else:
            logger.warning("显存档位未应用 [%s]: %s", self._vram_profile, res.get("reason"))
        return self._vram_last_result

    def vram_profile_state(self) -> Dict[str, object]:
        """供 UI 展示：当前档位名 + 上次应用结果。"""
        return {
            "profile": self._vram_profile,
            "last_result": self._vram_last_result,
        }

    # ---------- Comni 源码补丁（两处硬编码显存优化） ----------
    def _apply_source_patch(self) -> Dict[str, object]:
        """在 worker 启动前把两处源码补丁写进 Comni `cpp_backend.py`。

        任何失败都只记日志、不抛异常 —— 保证"补丁机制坏掉"不会导致引擎起不来
        （回落到 Comni 原生行为）。两处开关都关时直接跳过，零文件读写。
        """
        self._patch_last_result = {"ok": False, "reason": "未执行"}
        if not (self._patch_tts or self._patch_reuse):
            self._patch_last_result = {"ok": True, "reason": "两处补丁均未启用（零改动）"}
            return self._patch_last_result
        try:
            from .comni_source_patch import apply_source_patch
        except Exception as e:  # noqa: BLE001
            logger.warning("Comni 源码补丁模块不可用（跳过，按原生行为启动）: %s", e)
            self._patch_last_result = {"ok": False, "reason": "模块导入失败: %s" % e}
            return self._patch_last_result
        try:
            res = apply_source_patch(
                self._server_dir,
                enable_tts=self._patch_tts,
                enable_reuse_model=self._patch_reuse,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("应用 Comni 源码补丁异常（按原生行为启动）: %s", e)
            self._patch_last_result = {"ok": False, "reason": str(e)}
            return self._patch_last_result

        # 【关键】tts 补丁生效后必须给 worker 子进程注入 COMNI_USE_TTS=0 环境变量，
        # 否则源码里读到的仍是未设置 → 回落 True → 白打补丁。
        # 由 _launch 后续步骤按下述标记追加；这里只记录意图。
        self._patch_last_result = res
        if res.get("ok"):
            logger.info(
                "Comni 源码补丁 [%s] 已生效（备份 %s）",
                res.get("applied") or res.get("skipped"),
                res.get("backup"),
            )
        else:
            logger.warning("Comni 源码补丁未应用: %s", res.get("reason"))
        return self._patch_last_result

    def source_patch_state(self) -> Dict[str, object]:
        """供 UI 展示：两处补丁开关 + 上次应用结果。"""
        return {
            "enable_tts": self._patch_tts,
            "enable_reuse_model": self._patch_reuse,
            "last_result": self._patch_last_result,
        }

    def _worker_env(self) -> Dict[str, str]:
        """worker 子进程需注入的环境变量。

        【2026-09-20】`COMNI_USE_TTS=0` —— 与源码补丁 `comni_patch:tts` 配对。
        补丁把 `use_tts` 改成读该环境变量，故**只有两件事同时成立**才真正省下
        ≈2028MB：补丁已写入 + 本环境变量已注入。缺任一个都会静默回落 True。
        未启用 tts 补丁时返回空 dict（零影响）。
        """
        env: Dict[str, str] = {}
        if self._patch_tts:
            env["COMNI_USE_TTS"] = "0"
        return env

    def _stop_procs(self) -> None:
        for key, proc in list(self._procs.items()):
            if proc is None:
                continue
            try:
                if proc.poll() is None:
                    kill_tree(proc.pid)
            except Exception:  # noqa: BLE001
                pass
            self._procs[key] = None
        self._loaded = False

    # ---------- 引擎接口 ----------
    def start(self) -> None:
        """拉起引擎（首次挂载 / 重载）。已运行则跳过；非阻塞启动 + 等就绪。

        重入安全：用 _start_lock 串行化，保证“加载途中再次被调用（warm 线程与进入双工
        路径时间重叠）绝不会再拉起一组进程”，从而避免同进程内模型叠加 → 显存 OOM。
        """
        # 非阻塞抢锁：若另一处 start() 正在执行（大概率正 wait_for_health 加载中），
        # 直接返回，绝不再叠一组模型。
        if not self._start_lock.acquire(blocking=False):
            logger.info("引擎 start() 正在执行中，跳过本次重复调用（防显存叠加）")
            return
        try:
            if self._starting:
                logger.info("引擎已在启动中，跳过重复 start()（防显存叠加）")
                return
            if self.is_model_loaded():
                logger.info("引擎已在运行，跳过启动")
                return
            self._starting = True
            try:
                # 清掉可能残留的“半启动/加载超时”进程，防止覆盖 self._procs 后旧进程逃逸
                self._stop_procs()
                self._free_ports()
                self._kill_stray_model_procs()
                self._launch()
                url = f"https://127.0.0.1:{self._gateway_port}/health"
                if wait_for_health(url, timeout_s=self._health_timeout):
                    self._loaded = True
                    logger.info("引擎就绪：本地全双工模型已挂载（%s）", self._endpoint)
                else:
                    logger.warning(
                        "引擎启动超时（%ss 内未就绪），已清理进程组。请查看 Comni worker 日志排查模型加载",
                        self._health_timeout)
                    self._stop_procs()
            finally:
                self._starting = False
        finally:
            self._start_lock.release()

    def stop(self) -> None:
        logger.info("引擎停止：清理本地推理进程组")
        self._stop_procs()

    def unload_model(self) -> None:
        """卸载模型：停掉托管整组进程（gateway+worker+llama-server），释放显存。
        主程序/框架不退出（"进程不杀"）。"""
        logger.info("引擎卸载模型：停止本地推理进程组，释放显存给游戏")
        self._stop_procs()

    def reload_model(self) -> None:
        """重载模型：重新拉起整组进程。"""
        logger.info("引擎重载模型：重新拉起本地推理进程组")
        self.start()

    def is_model_loaded(self) -> bool:
        alive = [p for p in self._procs.values()
                 if p is not None and p.poll() is None]
        if not alive:
            return False
        return _health_url_ok(
            f"https://127.0.0.1:{self._gateway_port}/health", timeout=2.0)

    def model_process_pids(self) -> Set[int]:
        """本引擎托管的、持有显存的进程 PID（含 worker 子树下的 llama-server）。"""
        pids: Set[int] = set()
        for p in self._procs.values():
            if p is not None and p.poll() is None:
                pids.add(p.pid)
        if pids:
            pids |= child_pids(pids)
        return pids
