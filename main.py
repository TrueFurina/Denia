"""AI 桌面伴侣 — 入口。

启动链路：
  [主线程]          [asyncio 后台线程]       [console 后台线程]
  1. 加载配置
  2. 启动 asyncio 线程 → 3. 创建所有组件
                  ← 4. runtime_ready 通知
  5. 启动 pywebview   6. run_forever() 保活    7. input_loop() 监听控制台
     (阻塞)
  8. 关闭窗口 → 调用 stop_loop → 9. 清理所有组件

关闭逻辑：pywebview 关闭 → loop.stop() → 线程 join → 退出。

如果传入 --no-gui，则跳过 pywebview，用 asyncio.run() 跑纯控制台模式（原行为）。
"""
from __future__ import annotations

# Force UTF-8 on std streams so libraries like rich can print Unicode
# (e.g. the "✓" checkmark) even when launched from a GBK codepage console
# (e.g. double-clicking a .bat on Chinese Windows). Without this, console
# output raises UnicodeEncodeError and crashes the startup thread.
import sys as _sys
try:
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 🔑 通知浮窗"重新出现时不产帧"的病根解方（2026-09-04 23:20）——必须在 WebView2
# 环境初始化前设置，故放在文件最顶部。Chromium 默认对"不可见/被遮挡"的窗口停止
# 渲染（这正是我们一整天遇到的"黑屏/白屏/品红块/首帧画不出"的成因）：
#   --disable-renderer-backgrounding        渲染进程不因不可见而降频/停画
#   --disable-backgrounding-occluded-windows 被遮挡的窗口也照常产帧
#   --disable-features=CalculateNativeWinOcclusion 不计算原生窗口遮挡
# 三者都是 Chromium 官方开关，只影响渲染调度，不影响正常功能。
import os as _os_wv2
_os_wv2.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(filter(None, [
    _os_wv2.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", ""),
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-features=CalculateNativeWinOcclusion",
]))

# GPU OCR (DirectML) 可选加速：项目内 ort_dml 目录含带 DmlExecutionProvider 的
# onnxruntime 构建。存在时优先让其被导入，使 RapidOCR 走 GPU 推理；不存在则
# 退回默认 CPU onnxruntime（不影响其他功能）。安装见 onnxruntime-directml。
import os as _os
_ort_dml_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ort_dml")
if _os.path.isdir(_ort_dml_dir):
    if _ort_dml_dir not in _sys.path:
        _sys.path.insert(0, _ort_dml_dir)


def _set_app_user_model_id() -> None:
    """让 Windows 把本程序识别为独立应用，而不是"Python"。

    不设时任务栏会按 python.exe 归组：图标是 Python、悬停名也是 Python，
    看起来不像成品软件。设置 AppUserModelID 后系统按该 ID 分组，
    配合 setup(icon=...) 的窗口图标，任务栏就是本应用自己的图标与名称。
    必须在创建任何窗口之前调用。
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Daniya.DesktopCompanion.1")
    except Exception:  # noqa: BLE001  非 Windows / 老系统忽略
        pass


_set_app_user_model_id()

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
import atexit
from dataclasses import dataclass
from typing import Any, Optional

from rich.console import Console

from src.companion.activity_log import ActivityStore
from src.companion.activity_reporter import ActivityReporter
from src.companion.relationship_reporter import RelationshipReporter
from src.companion.video_test_reporter import VideoTestReporter
from src.companion.course_recorder import CourseRecorder
from src.companion.dialogue import Dialogue
from src.companion.game_knowledge import AppKnowledge, GameKnowledge
from src.companion.game_session import DeltaGameSession
from src.companion.memory_store import MemoryStore
from src.companion.persona import Persona
from src.companion.proactive import ProactiveStrategy
from src.config import Config, load_config, probe_all
from src.event_bus import EventBus, Event, EventType
from src.llm import create_llm, create_vision_router
from src.perception.foreground_monitor import ForegroundMonitor
from src.perception.game_audio import GameAudioMonitor
from src.perception.game_ocr import GameOcrMonitor
from src.perception.gpu_load import gpu_load, ollama_resident_mb
from src.perception.input_sensor import InputSensor
from src.perception.realtime_vision import RealtimeVision
from src.perception.squad_monitor import SquadMonitor
from src.perception.screen_capture import ScreenCapture
from src.perception.vision_analyzer import VisionAnalyzer
from src.protocol import make_heartbeat
from src.state_machine import StateMachine
from src.system.tray_manager import TrayManager
from src.ui.console import ConsoleUI
from src.ui.webapp import (CharacterAPI, LoadingAPI, StartupProgress,
                           create_main_window, create_desktop_companion,
                           create_bailian_console_window,
                           create_notify_window,
                           _attach_pet_no_taskbar,
                           _patch_webview2_unique_user_data,
                           _start_gui_liveness_probe)
from src.voice.asr import ASR
from src.voice.tts import TTS
from src.ws_server import WebSocketServer

logger = logging.getLogger(__name__)
console = Console()


def setup_logging(level: str) -> str:
    """初始化日志，返回日志文件路径。

    同时输出到终端和日志文件：GUI 模式下 cef 会接管 shell 重定向句柄，
    只用 `python main.py > run_log.txt` 日志落盘不可靠（大量日志丢失）。
    FileHandler 由 Python 自己持有文件句柄，与 shell 重定向无关。

    日志文件统一放在 logs/ 目录，每次启动按时间戳新建一个文件
    （logs/run_YYYYMMDD_HHMMSS.log），方便每次测试独立留存，
    发给朋友实测后直接取该文件即可排查问题。
    """
    lvl = getattr(logging, level.upper(), logging.INFO)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(
        log_dir, f"run_{time.strftime('%Y%m%d_%H%M%S')}.log")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))
    except Exception:
        logger.exception("日志文件初始化失败，仅终端输出")
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    return log_file


def _make_memory_store(cfg: Config) -> Optional[MemoryStore]:
    """创建长期记忆库（config.yaml memory.enabled=false 时返回 None）。

    mode=jarvis：分类记忆（directive/user/world）+ 重要性 + 可选语义检索（借鉴开源 Jarvis）；
    mode=legacy：原扁平记忆（对比用）。semantic_search 开启且嵌入可用时启用语义召回。
    """
    mem_cfg = getattr(cfg.ns, "memory", None)
    if mem_cfg is not None and not getattr(mem_cfg, "enabled", True):
        return None
    max_items = getattr(mem_cfg, "max_items", 100) if mem_cfg else 100
    # 【2026-10-17 开发者要求】auto_trim 默认 False ⇒ 记忆**不自动清理**；
    # 若开启，则按 max_items_per_character 给**每个角色各自**一份额度裁剪。
    auto_trim = bool(getattr(mem_cfg, "auto_trim", False)) if mem_cfg else False
    max_per_char = int(getattr(mem_cfg, "max_items_per_character", 0) or 0) if mem_cfg else 0
    mode = getattr(mem_cfg, "mode", "jarvis") if mem_cfg else "jarvis"
    semantic = bool(getattr(mem_cfg, "semantic_search", True)) if mem_cfg else True
    embed_fn = None
    if semantic:
        try:
            from src.llm.ollama_client import make_embed_fn
            embed_fn = make_embed_fn(cfg.ns.ollama.host)  # 嵌入失败时内部降级返回 None
        except Exception:
            logger.warning("记忆语义检索初始化失败，降级为关键词召回", exc_info=True)
    store = MemoryStore(max_items=max_items, mode=mode, embed_fn=embed_fn,
                        auto_trim=auto_trim, max_items_per_character=max_per_char)
    logger.info("长期记忆库就绪: 方案=%s 语义检索=%s 已存=%d 条",
                mode, "开" if embed_fn else "关(未装嵌入模型)", store.count())
    return store


def _game_recognition_enabled(cfg: Config) -> bool:
    """游戏识别总开关（config.yaml game_recognition.enabled）。

    关闭后不创建/不启动任何游戏识别组件（OCR/音效/队伍/分层视觉/游戏知识库/
    对局会话），相关源码文件全部保留，待游戏内识别方案重新设计后复用。
    """
    gr = getattr(cfg.ns, "game_recognition", None)
    if gr is None:
        return False
    return bool(getattr(gr, "enabled", False))


def _ocr_text_support_enabled(cfg: Config) -> bool:
    """OCR 仅文字模式（config.yaml perception.ocr.text_only 隐含启用）。

    game_recognition.enabled=false（OCR 击杀判定已下线）时，只要
    perception.ocr.enabled 仍为 true，就只创建"文字扫描"链路：GameKnowledge
    + GameOcrMonitor（text_only）——OCR 扫到的屏幕真文字喂给视觉模型结合判断
    （vision.ocr_text_hint，治"画面脑补击杀"），只更新文字、绝不发布时刻判定。
    """
    try:
        ocr_cfg = getattr(cfg.ns.perception, "ocr", None)
        if ocr_cfg is None:
            return False
        return bool(getattr(ocr_cfg, "enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _proactive_game_ai_interaction(cfg: Config) -> bool:
    """AI 视觉驱动游戏互动开关（config.yaml proactive.game_ai_interaction）。

    默认关闭：游戏画面与普通画面一样由视觉模型全局描述，不注入游戏场景/
    战绩上下文，也不做游戏场景搭话/结算聊天/时刻补发（用户要求回归纯全局
    屏幕识别，治 AI 游戏识别幻觉乱说话）。
    """
    try:
        pro_cfg = getattr(cfg.ns, "proactive", None)
        if pro_cfg is None:
            return False
        return bool(getattr(pro_cfg, "game_ai_interaction", False))
    except Exception:  # noqa: BLE001
        return False


def _cloud_vision_available(cfg: Config) -> bool:
    """vision_models 里是否存在可用的云端视觉模型（minicpm 免费网关 或 带 Key 的 openai 候选）。

    这是「游戏时云端看屏」能否真正生效的前提：没有云端模型，开了开关也只是空转。
    读 cfg.raw（而非 ns）：webapp 保存时原地改 raw，故开关/Key 变动可即时生效，无需重启。
    """
    try:
        vm = ((cfg.raw or {}).get("vision_models") or {})
        for m in (vm.get("models") or []):
            if not isinstance(m, dict):
                continue
            prov = str(m.get("provider", "") or "").strip().lower()
            if prov == "minicpm":
                return True  # 官方免费网关，无需 Key
            if prov == "openai" and bool(str(m.get("api_key", "") or "").strip()):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _game_cloud_watch_active(cfg: Config) -> bool:
    """「游戏时云端看屏」是否真的生效：开关已开 且 确有可用云端视觉模型。"""
    try:
        if not bool(((cfg.raw or {}).get("vision_models") or {}).get("game_cloud_watch", False)):
            return False
    except Exception:  # noqa: BLE001
        return False
    return _cloud_vision_available(cfg)


async def heartbeat_loop(ws: WebSocketServer, state: StateMachine, interval: float) -> None:
    start = time.time()
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.broadcast(make_heartbeat(time.time() - start, state.state))
        except Exception:
            logger.debug("心跳广播异常", exc_info=True)


def wire_foreground(bus: EventBus, monitor: ForegroundMonitor, capture: ScreenCapture) -> None:
    """前台监听接线：切到游戏窗口时立即抓屏分析（及时捕捉胜负等关键时刻）。"""

    async def _on_foreground_changed(e: Event) -> None:
        d = e.data or {}
        if isinstance(d, dict) and d.get("app_hint") == "game":
            logger.debug("前台切到游戏，事件驱动抓屏分析")
            await capture.capture_now(force=True)

    if monitor.enabled:
        bus.subscribe(EventType.FOREGROUND_CHANGED, _on_foreground_changed)


def _step(n: int, total: int, msg: str, ok: bool = True, note: str = "") -> None:
    mark = "[green]✓[/]" if ok else ("[yellow]⊘[/]" if note else "[red]✗[/]")
    note_str = f" [yellow]{note}[/]" if note else ""
    console.print(f"[{n}/{total}] {msg} ... {mark}{note_str}")


def _report_progress(runtime: RuntimeHolder, percent: int, msg: str = "",
                     phase: Optional[int] = None) -> None:
    """把启动进度上报给加载窗口（线程安全，无进度对象时静默跳过）。"""
    p = getattr(runtime, "progress", None)
    if p is not None:
        try:
            p.update(percent, msg, phase)
        except Exception:  # noqa: BLE001
            logger.debug("上报启动进度失败", exc_info=True)


# ==========================================================
# 本地 TTS 服务按需挂载（切换角色时才拉起服务 + 加载模型，控制显存占用）
# ==========================================================
# 每个本地克隆引擎对应一个独立服务子进程（独立 venv，模型懒加载在子进程内）。
# 模型加载完成后常驻显存：VoxCPM2 ~10GB / CosyVoice2 ~3.6GB / Qwen3-TTS ~2.5GB，
# 所以启动时一概不挂载，只有切到对应音色的角色时才拉起，切走立即终止释放。
def _tts_server_specs() -> dict[str, dict]:
    project = os.path.dirname(os.path.abspath(__file__))
    return {
        "voxcpm": {
            "display": "VoxCPM2",
            "default_port": 9881,
            "env_port": "VOXCPM_PORT",
            "venv_py": os.path.join(project, "voxcpm2_venv", "Scripts", "python.exe"),
            "script": os.path.join(project, "src", "voice", "voxcpm_server.py"),
            "proc_attr": "voxcpm_proc",
            "extra_env": {
                "VOXCPM_MODEL_CACHE": os.path.join(project, "voxcpm2_models"),
                # 子进程所有 AI 缓存强制到项目目录，绝不写 C 盘用户目录
                "HF_HOME": os.path.join(project, "hf_cache"),
                "HF_HUB_CACHE": os.path.join(project, "hf_cache", "hub"),
                "MODELSCOPE_CACHE": os.path.join(project, "modelscope_cache"),
                "MODELSCOPE_HOME": os.path.join(project, "modelscope_cache"),
                "HF_HUB_DISABLE_XET": "1",
            },
        },
        "qwen3tts": {
            "display": "Qwen3-TTS",
            "default_port": 9882,
            "env_port": "QWEN3_TTS_PORT",
            "venv_py": os.path.join(project, "qwen3_tts_venv", "Scripts", "python.exe"),
            "script": os.path.join(project, "src", "voice", "qwen3tts_server.py"),
            "proc_attr": "qwen3tts_proc",
            "extra_env": {
                "QWEN3_MODEL_CACHE": os.path.join(project, "qwen3_tts_models"),
                "HF_HOME": os.path.join(project, "hf_cache"),
                "HF_HUB_CACHE": os.path.join(project, "hf_cache", "hub"),
                "HF_HUB_DISABLE_XET": "1",
                # 【2026-09-10 显存碰撞修复】千问 TTS 默认走 CPU，不再强制 CUDA。
                # 原 FORCE_CUDA=1 让它常驻占 CUDA ~2GB，与本地双工 Comni(~10GB) 同挤
                # 5080 的 16GB → 双工实时推理 OOM 崩（UI 显示"未连接"）。现改为「按需 CUDA」：
                # 预览试听时由 webapp 调 POST /load {"device":"cuda"} 临时上卡提速，试完调
                # /unload 释放；常规角色音色/对话走 CPU（不争双工显存）。GPU 加载失败自动 fallback CPU。
            },
        },
        "cosyvoice": {
            "display": "CosyVoice2",
            "default_port": 9883,
            "env_port": "COSYVOICE_PORT",
            "venv_py": os.path.join(project, "cosyvoice_venv", "Scripts", "python.exe"),
            "script": os.path.join(project, "src", "voice", "cosyvoice_server.py"),
            "proc_attr": "cosyvoice_proc",
            "extra_env": {},
        },
    }


def _parse_host_port(api_url: str, default_port: int) -> tuple[str, int]:
    host, port = "127.0.0.1", default_port
    try:
        u = api_url.split("://")[1]
        host = u.split(":")[0]
        port = int(u.split(":")[-1].split("/")[0])
    except Exception:  # noqa: BLE001
        pass
    return host, port


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _service_loaded(host: str, port: int) -> Optional[bool]:
    """GET /health 判断模型是否已加载；服务不可达返回 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return bool(data.get("loaded"))
    except Exception:  # noqa: BLE001
        return None


def _tts_selfcheck(host: str, port: int) -> bool:
    """合成探活：发一个极短占位文本验证推理链路真的可用（不只是 /health 活着）。

    背景：TTS 进程可能因显存/设备错位（如 'Tensor on device meta'）而 /health
    仍返回 loaded=True，但实际每次 /tts 都 500。这种"僵尸服务"会让主程序误判
    为已加载而永远跳过挂载。这里用一次真实合成请求兜底验证。
    """
    import urllib.request
    payload = json.dumps({"text": "。", "language": "zh"}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/tts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return resp.status == 200 and len(resp.read()) > 0
    except Exception:  # noqa: BLE001
        return False


def _kill_port_owner(port: int) -> bool:
    """按端口找占用进程并强杀（用于僵尸 TTS 服务自愈）。返回是否杀到。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="replace", timeout=5).stdout
    except Exception:  # noqa: BLE001
        return False
    for line in out.splitlines():
        if f":{port} " not in line or "LISTENING" not in line:
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pid = int(parts[-1])
            try:
                logger.warning("僵尸 TTS 服务探活失败，强杀端口 %s 占用进程 pid=%s", port, pid)
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                               timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return True
            except Exception:  # noqa: BLE001
                logger.warning("强杀端口 %s 进程失败", port)
    return False


def _load_and_wait(host: str, port: int, timeout: float = 300.0,
                   proc: Optional[subprocess.Popen] = None,
                   device: Optional[str] = None) -> tuple[bool, str]:
    """调用 /load 触发模型加载并等待完成（端点内部阻塞到加载完）。

    服务进程刚拉起时可能还没就绪，连接失败则间隔重试。以下两种情况提前
    返回失败，避免加载窗口长时间空转：
      - /load 返回 4xx/5xx（模型加载本身失败，重试无意义）；
      - 服务子进程已退出（如崩溃/显存不足被杀）。

    device: 可选 "cuda"/"cpu"，传给 POST /load 指定加载设备（按需 CUDA 预览用）；
            为 None 时走 GET /load（默认设备）。

    返回 (success, error_msg)：success 为 True 表示加载完成；False 时
    error_msg 携带具体原因（HTTP 500 body / 子进程退出 / 超时）。
    """
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    # device 指定时走 POST（带 body），否则 GET
    if device is not None:
        url = f"http://{host}:{port}/load"
        body = json.dumps({"device": device}).encode("utf-8")
        while time.time() < deadline:
            try:
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=min(60.0, max(10.0, deadline - time.time()))) as resp:
                    data = json.loads(resp.read().decode("utf-8", "ignore"))
                return bool(data.get("loaded")), ""
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", "ignore")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    err = json.loads(err_body).get("error", err_body[:200]) if err_body else "(无响应体)"
                except Exception:  # noqa: BLE001
                    err = err_body[:200] or "(无响应体)"
                logger.error("TTS 服务 /load 返回 %s: %s", e.code, err)
                return False, f"/load 返回 {e.code}: {err}"
            except Exception as e:  # noqa: BLE001
                if proc is not None and proc.poll() is not None:
                    logger.error("TTS 服务子进程已退出 (code=%s)，停止等待模型加载",
                                 proc.poll())
                    return False, f"服务子进程已退出 (code={proc.poll()})"
                time.sleep(2.0)
        return False, "模型加载超时"
    # 默认 GET 路径
    url = f"http://{host}:{port}/load"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=min(60.0, max(10.0, deadline - time.time()))) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            return bool(data.get("loaded")), ""
        except urllib.error.HTTPError as e:
            # 模型加载失败（500 等）：立即返回，不再 300s 空等；把服务端错误 body 透传出去
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                pass
            try:
                err = json.loads(body).get("error", body[:200]) if body else "(无响应体)"
            except Exception:  # noqa: BLE001
                err = body[:200] or "(无响应体)"
            logger.error("TTS 服务 /load 返回 %s: %s", e.code, err)
            return False, f"/load 返回 {e.code}: {err}"
        except Exception as e:  # noqa: BLE001
            if proc is not None and proc.poll() is not None:
                logger.error("TTS 服务子进程已退出 (code=%s)，停止等待模型加载",
                             proc.poll())
                return False, f"服务子进程已退出 (code={proc.poll()})"
            time.sleep(2.0)
    return False, "模型加载超时"


def _norm_device(device: Optional[str]) -> Optional[str]:
    """归一化设备名：cuda -> cuda:0，cpu -> cpu，其它/None -> None。"""
    if device == "cuda":
        return "cuda:0"
    if device == "cpu":
        return "cpu"
    return None


def _tts_device(host: str, port: int) -> Optional[str]:
    """GET /health 取当前已加载模型所在设备（cuda:0 / cpu / None）。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return data.get("device")
    except Exception:  # noqa: BLE001
        return None


def _unload_tts_service(host: str, port: int) -> None:
    """POST /unload 卸载模型释放显存（按需 CUDA：预览试听结束后调用）。失败静默。"""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/unload", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _publish_tts_status(bus: Optional[EventBus], etype: EventType, engine: str,
                        name: str, error: str = "") -> None:
    """把 TTS 加载状态事件发布到总线（线程安全），供 UI 做加载中/加载完成提醒。"""
    if bus is None:
        return
    data: dict[str, Any] = {"engine": engine, "name": name}
    if error:
        data["error"] = error
    bus.publish_threadsafe(Event(etype, data, "tts"))


def _popen_tts_server(runtime: RuntimeHolder, spec: dict, host: str, port: int) -> bool:
    """拉起本地 TTS 服务子进程（独立 venv）。成功返回 True。"""
    venv_py = spec["venv_py"]
    if not os.path.exists(venv_py):
        logger.warning("未找到 %s（%s），%s 音色不可用", spec["display"], venv_py, spec["display"])
        return False
    if not os.path.exists(spec["script"]):
        logger.warning("未找到 %s，%s 音色不可用", spec["script"], spec["display"])
        return False
    try:
        env = os.environ.copy()
        env[spec["env_port"]] = str(port)
        for k, v in spec["extra_env"].items():
            env.setdefault(k, v)
        # 服务 stderr 落盘（logs/{xxx}_server.log），偶发合成异常（如"机械音"）
        # 时能查到服务端错误/警告，否则 DEVNULL 全丢无从诊断。
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _err_f = open(
            os.path.join(_log_dir, f"{spec['proc_attr'].replace('_proc', '')}_server.log"),
            "a", encoding="utf-8", buffering=1)
        proc = subprocess.Popen(
            [venv_py, "-B", "-u", spec["script"]],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=_err_f,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        setattr(runtime, spec["proc_attr"], proc)
        logger.info("已拉起 %s 服务 (pid=%s, %s:%s)，模型加载约需 10~60s",
                    spec["display"], proc.pid, host, port)
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("拉起 %s 服务失败: %s", spec["display"], e)
        return False


def _voice_loading_ticker(progress: StartupProgress, label: str,
                          stop_ev: threading.Event) -> None:
    """模型加载期间虚拟进度爬升 + 等待秒数提示（守护线程）。

    本地克隆模型加载需 10~60s（Qwen3-TTS 实测 ~50s），期间 /load 阻塞。
    若无反馈，加载窗口会长时间停在 92%/99% 不动，被误认为卡死。
    这里每 3s 把进度 92→98 缓慢爬升、状态文字带已等待秒数，让用户
    感知程序仍在工作；加载完成后由调用方跳到 99/100。
    """
    n = 0
    while not stop_ev.wait(3.0):
        n += 1
        pct = min(98, 92 + n)  # 虚拟爬升上限 98，避免盖过真实完成值 99/100
        try:
            progress.update(pct, f"正在加载「{label}」的音色模型…（已等待 {n * 3}s）",
                            phase=7)
        except Exception:  # noqa: BLE001
            logger.debug("上报音色加载进度失败", exc_info=True)


# 【2026-09-10 决定性修复】按端口的 spawn 锁：防止多线程并发拉起同一 TTS 服务。
# 原逻辑 `_port_open()` 判定后再 spawn，两个调用方（companion 切引擎 + 引导预载）并发时
# 会各自拉起一个进程，二者同时 from_pretrained 加载同一模型 → 878 个子模块落 meta 空壳
# （表现为「权重文件损坏」的假象，模型永远加载不出 → 试听无声/失败）。
# 用锁把「探端口 + 拉起」串行化并双重检查，确保同一端口只会有唯一实例。
_TTS_SPAWN_LOCKS: "dict[int, threading.Lock]" = {}


def _mount_tts_service(runtime: RuntimeHolder, cfg: Config, bus: Optional[EventBus],
                       engine: str, api_url: str, name: str,
                       progress: Optional[StartupProgress] = None,
                       device: Optional[str] = None) -> dict:
    """挂载指定引擎的本地 TTS 服务：拉起进程（如未运行）+ 触发并等待模型加载完成。

    全程发布 TTS_LOADING / TTS_LOADED / TTS_LOAD_ERROR 事件供 UI 提醒。
    阻塞直到加载完成（VoxCPM2 ~30s / Qwen3-TTS ~30s / CosyVoice2 ~60s），线程安全
    （只操作子进程 + HTTP，不碰事件循环），调用方用后台线程包裹避免阻塞。
    传 progress 时，等待期间虚拟进度爬升（92→98）防止加载窗口被误认为卡死。
    返回 {"success", "host", "port", "message"}。
    """
    # 【无声模式不挂载（2026-08-30 修复）】全局 voice.mode=mute 时用户明确不要任何
    # 出声，本地克隆大模型服务一律不拉起——这是所有挂载入口（启动预载 / 角色切换
    # 同步 / 前端 mount_tts 回调）的统一兜底，确保 mute 下即便本机有模型也绝不加载。
    _vcfg = (getattr(cfg, "raw", None) or {}).get("voice", {}) or {}
    if (str(_vcfg.get("mode") or "").strip().lower() == "mute") or _vcfg.get("muted"):
        logger.info("无声模式(voice.mode=mute)：跳过挂载本地 TTS 服务 %s", engine)
        return {"success": False, "error": "mute 模式不加载本地 TTS 服务"}
    specs = _tts_server_specs()
    spec = specs.get(engine)
    if spec is None:
        return {"success": False, "error": f"未知本地 TTS 引擎: {engine}"}
    host, port = _parse_host_port(api_url, spec["default_port"])
    display = spec["display"]
    label = name or display
    proc = getattr(runtime, spec["proc_attr"], None)

    # 已加载 → 直接返回，不打扰 UI
    loaded = _service_loaded(host, port)
    if loaded:
        # /health 说已加载，但可能是"僵尸服务"（进程活着但推理已坏）。
        # 先发一次合成探活，确保推理链路真的可用，否则强杀重拉。
        if _tts_selfcheck(host, port):
            # 【2026-09-10 按需 CUDA】预览要求 cuda 但当前是 cpu（或反之）→
            # 不杀进程，卸载后按目标设备重挂（释放旧设备显存，避免与双工碰撞）。
            want = _norm_device(device)
            cur_dev = _tts_device(host, port) or "cpu"
            if want and want != cur_dev:
                logger.info("%s 已加载但设备不符(%s≠%s)，卸载后重挂到 %s",
                            display, cur_dev, want, device)
                _unload_tts_service(host, port)
                # 继续往下走加载流程（进程仍存活，不会重拉；直接 POST /load 按新设备加载）
            else:
                logger.info("%s 服务已加载且探活通过 (%s:%s)，跳过挂载", display, host, port)
                return {"success": True, "host": host, "port": port}
        else:
            logger.warning("%s 服务 /health 显示已加载，但合成探活失败，判定为僵尸服务，自愈重拉", display)
            _kill_port_owner(port)
            setattr(runtime, spec["proc_attr"], None)
            # 落到下方"端口空闲→拉起新进程"路径

    _publish_tts_status(bus, EventType.TTS_LOADING, engine, label)
    # 服务没在跑 → 拉起子进程（加锁 + 双重检查，杜绝并发重复 spawn）
    _spawn_lock = _TTS_SPAWN_LOCKS.setdefault(port, threading.Lock())
    with _spawn_lock:
        if _port_open(host, port):
            # 已被其它调用方拉起（或本来就在跑）→ 复用，绝不重复 spawn
            logger.info("%s 服务已在运行 (%s:%s)，复用不重复 spawn", display, host, port)
        elif not _popen_tts_server(runtime, spec, host, port):
            _publish_tts_status(bus, EventType.TTS_LOAD_ERROR, engine, label, "拉起服务失败")
            return {"success": False, "error": f"拉起{display}服务失败"}
        proc = getattr(runtime, spec["proc_attr"], None)

    # 模型加载期间虚拟进度爬升，避免加载窗口长时间无反馈（守护线程，加载完停止）
    ticker_stop = threading.Event()
    if progress is not None:
        threading.Thread(
            target=_voice_loading_ticker,
            args=(progress, label, ticker_stop),
            daemon=True,
            name=f"voice_loading_ticker_{engine}",
        ).start()
    try:
        # 触发模型加载并等待完成（device 指定时按目标设备加载，支持预览临时 CUDA）
        loaded, load_err = _load_and_wait(host, port, proc=proc, device=device)
        if not loaded:
            logger.error("%s 模型加载失败 (%s:%s): %s", display, host, port, load_err)
            _publish_tts_status(bus, EventType.TTS_LOAD_ERROR, engine, label, load_err)
            # 把具体错误显示到 UI：超时/进程退出/服务端 500 都不再笼统说"加载超时"
            return {"success": False, "error": f"{display}模型加载失败：{load_err}"}
    finally:
        ticker_stop.set()

    logger.info("%s 模型加载完成 (%s:%s)", display, host, port)
    _publish_tts_status(bus, EventType.TTS_LOADED, engine, label)
    _reprobe_tts(runtime)  # 修正兜底标志，避免刚切过来的本地音色继续走 edge-tts
    return {"success": True, "host": host, "port": port}


def _mount_tts_async(runtime: RuntimeHolder, cfg: Config, engine: str, api_url: str, name: str) -> None:
    """后台线程挂载本地 TTS 服务（不阻塞事件循环）：拉起 + 等模型 + UI 事件通知。"""
    threading.Thread(
        target=_mount_tts_service,
        args=(runtime, cfg, runtime.bus, engine, api_url, name),
        daemon=True,
        name=f"tts_mount_{engine}",
    ).start()


def _reprobe_tts(runtime: RuntimeHolder) -> None:
    """本地克隆服务加载完成后复查 TTS 兜底标志（否则新音色会一直走 edge-tts 兜底）。"""
    tts = getattr(runtime, "tts", None)
    if tts is None:
        return
    try:
        tts.reprobe_service()
    except Exception:  # noqa: BLE001
        logger.warning("TTS 服务复查失败", exc_info=True)


def _sync_voxcpm_on_switch(runtime: RuntimeHolder, cfg: Config, char_id: str) -> None:
    """角色切换后按新角色音色同步 VoxCPM2 本地服务，控制显存占用。

    VoxCPM2 服务是独立进程，加载后常驻约 6~10GB 显存。切换后：
      - 新角色用 VoxCPM → 后台按需挂载（拉起 + 加载 + UI 提醒）；
      - 新角色不用 VoxCPM → 立即终止服务释放显存。
    """
    char = cfg.get_character_by_id(char_id)
    engine = getattr(char.voice, "engine", None) if char is not None else None
    if engine == "voxcpm":
        api_url = getattr(char.voice, "api_url", "") or "http://127.0.0.1:9881"
        name = getattr(char, "name", "") or char_id
        _mount_tts_async(runtime, cfg, "voxcpm", api_url, name)
        return
    proc = runtime.voxcpm_proc
    if proc is not None and proc.poll() is None:
        logger.info("角色 %s 音色引擎=%s（非 VoxCPM），终止 VoxCPM 服务释放显存 (pid=%s)",
                    char_id, engine, proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            logger.warning("终止 VoxCPM 服务失败", exc_info=True)
        runtime.voxcpm_proc = None


def _sync_qwen3tts_on_switch(runtime: RuntimeHolder, cfg: Config, char_id: str) -> None:
    """角色切换后按新角色音色同步 Qwen3-TTS 本地服务，控制显存占用（同 VoxCPM 策略）。"""
    char = cfg.get_character_by_id(char_id)
    engine = getattr(char.voice, "engine", None) if char is not None else None
    if engine == "qwen3tts":
        api_url = getattr(char.voice, "api_url", "") or "http://127.0.0.1:9882"
        name = getattr(char, "name", "") or char_id
        _mount_tts_async(runtime, cfg, "qwen3tts", api_url, name)
        return
    proc = runtime.qwen3tts_proc
    if proc is not None and proc.poll() is None:
        logger.info("角色 %s 音色引擎=%s（非 Qwen3-TTS），终止 Qwen3-TTS 服务释放显存 (pid=%s)",
                    char_id, engine, proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            logger.warning("终止 Qwen3-TTS 服务失败", exc_info=True)
        runtime.qwen3tts_proc = None


def _sync_cosyvoice_on_switch(runtime: RuntimeHolder, cfg: Config, char_id: str) -> None:
    """角色切换后按新角色音色同步 CosyVoice2 本地服务，控制显存占用（同 VoxCPM 策略）。"""
    char = cfg.get_character_by_id(char_id)
    engine = getattr(char.voice, "engine", None) if char is not None else None
    if engine == "cosyvoice":
        api_url = getattr(char.voice, "api_url", "") or "http://127.0.0.1:9883"
        name = getattr(char, "name", "") or char_id
        _mount_tts_async(runtime, cfg, "cosyvoice", api_url, name)
        return
    proc = runtime.cosyvoice_proc
    if proc is not None and proc.poll() is None:
        logger.info("角色 %s 音色引擎=%s（非 CosyVoice2），终止 CosyVoice2 服务释放显存 (pid=%s)",
                    char_id, engine, proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            logger.warning("终止 CosyVoice2 服务失败", exc_info=True)
        runtime.cosyvoice_proc = None


def _unload_all_local_tts(runtime: RuntimeHolder) -> None:
    """终止全部本地克隆 TTS 服务子进程（切到无声模式时释放显存/内存）。

    与 _sync_*_on_switch 的区别：后者按「新角色是否用该引擎」决定留否；此处是无声
    模式专用——无论角色引擎是什么，本地克隆服务一概停掉（用户明确不要任何出声，
    就不该白白占着显存/内存）。_unload_all_local_tts 不卸载在线/轻量引擎（edge-tts
    等无需常驻进程），只针对 voxcpm/qwen3tts/cosyvoice 三个本地大模型服务。
    """
    for attr in ("voxcpm_proc", "qwen3tts_proc", "cosyvoice_proc"):
        proc = getattr(runtime, attr, None)
        if proc is not None and proc.poll() is None:
            try:
                logger.info("无声模式: 终止本地 TTS 服务 %s (pid=%s) 释放资源", attr, proc.pid)
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                logger.warning("无声模式: 终止 %s 失败", attr, exc_info=True)
        setattr(runtime, attr, None)


def _print_characters(cfg: Config, active_id: str) -> None:
    chars = cfg.list_characters()
    console.print("[cyan]可用角色:[/]")
    for i, ch in enumerate(chars, 1):
        mark = "[green]★[/]" if ch.id == active_id else " "
        voice_info = f"{ch.voice.engine}({ch.voice.voice})"
        console.print(f"  {mark} {i}. {ch.name} [{ch.id}] 音色:{voice_info}")
    console.print("[dim]输入 /角色 查看列表，输入 /切换 <id> 切换角色[/]")
    console.print()


# ==========================================================
# Runtime 持有者（跨线程共享组件引用）
# ==========================================================
@dataclass
class RuntimeHolder:
    cfg: Optional[Config] = None
    bus: Optional[EventBus] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    state: Optional[StateMachine] = None
    ws: Optional[WebSocketServer] = None
    llm: Optional[Any] = None
    vision_router: Optional[Any] = None
    dialogue: Optional[Dialogue] = None
    tts: Optional[TTS] = None
    asr: Optional[ASR] = None
    proactive: Optional[ProactiveStrategy] = None
    capture: Optional[ScreenCapture] = None
    monitor: Optional[ForegroundMonitor] = None
    ocr: Optional[GameOcrMonitor] = None
    input_sensor: Optional[Any] = None
    audio: Optional[GameAudioMonitor] = None
    realtime: Optional[RealtimeVision] = None
    squad: Optional[SquadMonitor] = None
    knowledge: Optional[GameKnowledge] = None
    memory_store: Optional[MemoryStore] = None
    ui: Optional[ConsoleUI] = None
    activity_reporter: Optional[ActivityReporter] = None  # 活动记忆 + 日报/周报
    course_recorder: Optional[CourseRecorder] = None      # 网课自动记录
    voxcpm_proc: Optional[subprocess.Popen] = None  # VoxCPM2 本地 TTS 服务子进程
    qwen3tts_proc: Optional[subprocess.Popen] = None  # Qwen3-TTS 本地克隆服务子进程
    cosyvoice_proc: Optional[subprocess.Popen] = None  # CosyVoice2 本地克隆服务子进程
    
    # 系统托盘
    tray: Optional[TrayManager] = None
    minimize_to_tray: bool = False  # 是否最小化到托盘而非退出

    # 启动加载进度（加载窗口轮询；asyncio 线程 + 启动监督线程共同更新）
    progress: Optional[StartupProgress] = None
    # asyncio 后台线程句柄（启动监督线程创建，主线程收尾 join 用）
    async_thread: Optional[threading.Thread] = None

    # 后台任务集合（用于取消）
    _tasks: list[asyncio.Task] = None  # type: ignore

    # 同步事件
    ready: threading.Event = None  # type: ignore  # asyncio 初始化完成后 set
    stop_requested: threading.Event = None  # type: ignore  # 请求关闭时 set

    # 初始化失败原因（asyncio 线程崩溃时写入，主线程据此决定是否启动 GUI）
    init_error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.ready is None:
            self.ready = threading.Event()
        if self.stop_requested is None:
            self.stop_requested = threading.Event()
        if self._tasks is None:
            self._tasks = []


# ==========================================================
# 社交通道（G 脚手架）：外部 IM（QQ 等）接入同一个 AI 大脑
# ==========================================================
async def _mount_channels(bus, dialogue, llm, cfg_ns, runtime) -> list:
    """按 config.channels 构建并启动已启用的外部 IM 通道（默认全关）。

    大脑 Dialogue 与通道解耦：通道只负责把用户文本喂给 dialogue.respond 并订阅
    AI_DECISION 把回复发回对应 peer；桌面(ws_server)只播放 channel 为空的桌面决策。
    任何通道启动失败都不阻断主程序（记录日志并跳过）。
    """
    try:
        from src.channels import build_channels
    except Exception:  # noqa: BLE001
        logger.exception("导入社交通道包失败，跳过")
        return []
    try:
        channels = build_channels(bus, dialogue, llm, cfg_ns)
    except Exception:  # noqa: BLE001
        logger.exception("构建社交通道失败，跳过")
        return []
    started = []
    for ch in channels:
        try:
            task = await ch.start()
            started.append(ch)
            logger.info("社交通道已启动: %s (mode=%s)", ch.name, getattr(ch, "_mode", "?"))
            if task is not None and runtime is not None:
                runtime._tasks.append(task)
        except Exception:  # noqa: BLE001
            logger.exception("启动社交通道 %s 失败，跳过", ch.name)
    if not channels:
        logger.info("社交通道：未启用（config.channels 为空或全 disabled）")
    return started


# ==========================================================
# asyncio 后台线程：初始化 + run_forever
# ==========================================================
def _is_first_run(cfg: Config) -> bool:
    """首启判定：config.yaml 无 first_run_done 标记即视为首次启动。

    首次启动时不预载任何模型、不启动读屏/双工等重型子系统，等引导完成
    （ONBOARDING_DONE）再按所选配置拉起——实现"没配置好所有东西不运行"。

    调试开关 force_first_run=true 时强制走首启流程（反复重启看首启效果用），
    不影响真实用户：正式发布前将其置 false 即可恢复"老用户照常加载"。
    """
    raw = getattr(cfg, "raw", None) or {}
    if bool(raw.get("force_first_run", False)):
        return True
    return not bool(raw.get("first_run_done", False))


class _NoopProgress:
    """首启延迟预载用的空进度对象（启动进度窗已关闭，不触碰 UI）。"""

    def update(self, *a, **k):  # noqa: D401
        pass

    def done(self, *a, **k):
        pass

    def fail(self, *a, **k):
        pass


def _start_heavy_tasks(runtime: RuntimeHolder, cfg: Config) -> None:
    """创建并启动所有重型后台循环任务（读屏/前台/双工/游戏链路/活动记录等）。

    必须在 asyncio 主循环内调用（_build_system 正常路径 / ONBOARDING_DONE 首启延迟路径）。
    仅创建任务、不阻塞；模型预载由调用方另行在后台线程进行。
    """
    loop = runtime.loop
    bus = runtime.bus
    capture = runtime.capture
    monitor = runtime.monitor
    proactive = runtime.proactive
    ocr = runtime.ocr
    audio = runtime.audio
    realtime = runtime.realtime
    squad = runtime.squad
    activity_reporter = runtime.activity_reporter
    course_recorder = runtime.course_recorder

    capture_task = loop.create_task(capture.capture_loop(), name="capture")
    monitor_task = loop.create_task(monitor.run(), name="foreground")
    proactive_enabled = bool(proactive.enabled)
    proactive_task = (loop.create_task(proactive.run_loop(interval=30.0), name="proactive")
                     if proactive_enabled else None)
    logger.info("主动搭话任务: enabled=%s duplex_mode=%r task=%s",
                proactive_enabled, proactive._duplex_mode,
                proactive_task.get_name() if proactive_task else None)
    ocr_task = loop.create_task(ocr.run(), name="game_ocr") if ocr is not None else None
    if audio is not None:
        audio_task = loop.create_task(audio.run(), name="game_audio")
    if realtime is not None:
        realtime_task = loop.create_task(realtime.run(), name="realtime_vision")
    if squad is not None:
        squad_task = loop.create_task(squad.run(), name="squad")
    # 仅 game_recognition 全链路时起（audio/realtime/squad 仅在 game_recognition 时创建）
    if audio is not None:
        wire_foreground(bus, monitor, capture)
    activity_task = loop.create_task(activity_reporter.run(), name="activity_reporter")
    course_task = loop.create_task(course_recorder.run(), name="course_recorder")

    tasks = [capture_task, monitor_task, activity_task, course_task]
    if proactive_task is not None:
        tasks.append(proactive_task)
    if ocr_task is not None:
        tasks.append(ocr_task)
    if audio is not None:
        tasks.append(audio_task)
    if realtime is not None:
        tasks.append(realtime_task)
    if squad is not None:
        tasks.append(squad_task)
    runtime._tasks.extend(tasks)


def _maybe_download_comni_guid(runtime: RuntimeHolder, cfg: Config) -> None:
    """首启 local_omni：若 Comni 模型缺失则按需自动下载推荐量化档（用户已确认）。

    仅当能从配置解析到 Comni model_dir 时才下载；否则交由用户手动/Comni.exe 提供。
    """
    try:
        from src.companion.model_catalog import (  # noqa: WPS433
            detect_total_vram_gb, recommend_quants, download_model_quant, _comni_model_dir)
    except Exception:  # noqa: BLE001
        logger.warning("导入 model_catalog 失败，跳过 Comni 模型自动下载")
        return
    try:
        model_dir = _comni_model_dir()
        if not model_dir:
            logger.info("首启 local_omni：未解析到 Comni model_dir，跳过自动下载"
                        "（交由 Comni.exe / 用户手动提供）")
            return
        if os.path.isdir(model_dir) and any(
                f.lower().endswith(".gguf") for f in os.listdir(model_dir)):
            logger.info("首启 local_omni：Comni 模型已存在，跳过下载")
            return
        vram = detect_total_vram_gb()
        rec = (recommend_quants(vram) or {}).get("recommended")
        if not rec:
            logger.info("首启 local_omni：显存不足以推荐本地档，跳过自动下载（走云端兜底）")
            return
        logger.info("首启 local_omni：自动下载推荐量化档 %s -> %s", rec, model_dir)
        download_model_quant({"quant_id": rec, "model_dir": model_dir})
    except Exception:  # noqa: BLE001
        logger.exception("首启自动下载 Comni 模型失败（不阻断）")


def _first_run_preload(runtime: RuntimeHolder, cfg: Config) -> None:
    """首启延迟预载：后台线程执行，不阻塞事件循环；无启动进度窗。

    覆盖：克隆音色模型 + 本地双工(ollama)模型 + 本地真全双工(Comni)缺失时按需下载。
    """
    try:
        _preload_active_voice(runtime, cfg, _NoopProgress())
    except Exception:  # noqa: BLE001
        logger.exception("首启预载音色模型异常（不影响进入主界面）")
    try:
        _dm = getattr(getattr(cfg.ns, "proactive", None), "duplex_mode", "")
        if _dm == "local":
            _preload_local_duplex_model(runtime, cfg, _NoopProgress())
        elif _dm == "local_omni":
            _maybe_download_comni_guid(runtime, cfg)
    except Exception:  # noqa: BLE001
        logger.exception("首启预载/下载双工模型异常（不阻断）")


async def _build_system(runtime: RuntimeHolder, cfg: Config) -> None:
    """构建所有系统组件（在 asyncio 线程中运行）。"""
    total = 7
    status = cfg.runtime_status

    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.bind_loop(loop)
    state = StateMachine(bus)

    runtime.bus = bus
    runtime.loop = loop
    runtime.state = state

    _report_progress(runtime, 2, "加载配置文件", phase=0)
    console.print(f"[1/{total}] 加载配置 config.yaml ... [green]✓[/]")

    provider = status.llm_provider or "ollama"
    _report_progress(runtime, 10, "检查 LLM 与语音环境", phase=1)
    _step(2, total, f"检查 LLM({provider}) / Vosk / 麦克风", ok=status.ollama_ok,
          note="" if status.ollama_ok else "见下方降级提示")
    for d in status.degradations:
        console.print(f"       [yellow]⚠ {d}[/]")

    # 核心组件
    ws = WebSocketServer(bus, cfg.ns.websocket.host, cfg.ns.websocket.port)
    llm = create_llm(cfg)  # 对话 LLM（chat）
    vision_router = create_vision_router(cfg)  # 视觉模型路由：云端优先、本地兜底
    monitor = ForegroundMonitor(bus, cfg)
    # 游戏进行中禁用本地视觉模型（防 4.8GB 本地模型抢 GPU 卡游戏）
    # 2026-08-25：扩展为"游戏前台 或 锁快照中"——锁快照（大 GPU 负载让位）
    # 期间视觉也绝不加载本地 ollama 模型（实锤：锁快照后用户切出游戏，视觉
    # 云端失败 → 本地 o2.6 4.8GB 又占显存，显存回到 92%）。proactive 在
    # vision_router 之后构造，用闭包延迟读取锁状态。
    def _game_check_or_locked() -> bool:
        pa = getattr(runtime, "proactive", None)
        locked = bool(getattr(pa, "_duplex_locked", False)) if pa is not None else False
        return monitor.is_game_foreground() or locked
    # game_check 保持"游戏/锁分"原语义：它是**安全闸**，让 VisionRouter 在游戏中绝不加载
    # 本地 ollama 模型（防本地大模型抢显卡）。下面新加的 throttle_check 才受「游戏时云端
    # 看屏」开关影响，两者职责不同、不可混用。
    vision_router.set_game_check(_game_check_or_locked)

    def _screen_throttle_check() -> bool:
        """游戏/锁分时是否让位（暂停云端 VLM 持续看屏）。

        【2026-09-28】「游戏时云端看屏」开关开启 且 确有可用云端视觉模型时，游戏期间
        **不节流**，由云端 VLM 接管持续读屏并主动互动（期间本地模型仍被 game_check 禁掉，
        不占显卡）；否则沿用原行为——低频抓屏、不触发视觉分析，让位游戏省 CPU/GPU。
        只作用于 ScreenCapture 这条独立看屏管线，**语音双工完全不受影响**（两条管线本就
        解耦，不为看屏去动语音引擎）。

        【2026-10-13 补 · 放开 throttle 不等于放开性能闸】放开节流只解决"云端能不能
        看得见"；采样节奏另由 `low_rate_check` 兜住（同一个 `_game_check_or_locked`
        来源）：游戏/锁分期间**始终**走 `screenshot.game_interval_s` 低频档并关闭静态
        加速，避免"画面静止 → static 加速到 ~1s → 每帧全屏抓屏 + JPEG 编码"把
        2026-08-25 修掉的抽帧/核显本卡炸带回来。等价于：云端 VLM 接管看屏，但本地
        CPU/GPU 开销与让位时同档。回退本地 ollama 那条路依旧由 game_check 挡住
        （游戏/通话中绝不加载本地大模型抢显存）。
        """
        if not _game_check_or_locked():
            return False
        return not _game_cloud_watch_active(cfg)
    analyzer = VisionAnalyzer(vision_router, bus, cfg, monitor=monitor)
    # low_rate_check：与 throttle 同源，保证「不节流」时也不提升采样频率（防性能回退）。
    capture = ScreenCapture(bus, analyzer, cfg, throttle_check=_screen_throttle_check,
                            low_rate_check=_game_check_or_locked)
    # 游戏识别组件（总开关 game_recognition.enabled=false 时整体下线：
    # OCR/音效/队伍HUD/分层视觉/游戏知识库不创建不启动，源码保留）。
    # 对局会话 game_session：仅在游戏识别或游戏 AI 互动启用时创建——关闭时
    # 给 dialogue/proactive 传 None，对局小结/模式等游戏提示彻底不注入 LLM
    # 上下文（用户要求去掉 AI 游戏识别、回归纯全局屏幕识别，不给任何游戏提示）。
    game_recognition = _game_recognition_enabled(cfg)
    knowledge: Optional[GameKnowledge] = None
    app_knowledge = AppKnowledge(cfg)  # 软件知识库：按前台窗口标题命中软件，注入该软件说明
    ocr: Optional[GameOcrMonitor] = None
    audio: Optional[GameAudioMonitor] = None
    realtime: Optional[RealtimeVision] = None
    squad: Optional[SquadMonitor] = None
    input_sensor: Optional[InputSensor] = None
    game_session: Optional[DeltaGameSession] = (
        DeltaGameSession() if (game_recognition or _proactive_game_ai_interaction(cfg))
        else None)
    if game_recognition:
        knowledge = GameKnowledge(cfg)  # 游戏知识库：识别游戏，喂给 AI 游戏名+攻略+数据
        input_sensor = InputSensor()  # 输入证据门（v2）：击杀判定必须有"开枪"证据
        ocr = GameOcrMonitor(bus, cfg, monitor, llm=llm, game_knowledge=knowledge, throttle_check=_game_check_or_locked,
                             sensor=input_sensor)
        audio = GameAudioMonitor(bus, cfg, monitor)  # 游戏音效识别：参考音模板比对（击杀/阵亡/胜负）
        realtime = RealtimeVision(bus, cfg, monitor=monitor)  # 分层视觉：轻量模型持续看屏（感知层）
        squad = SquadMonitor(bus, cfg, monitor, llm=llm, game_knowledge=knowledge)  # 队伍HUD：队友存活状态
        squad.set_session(game_session)  # 队伍识别用已识别的游戏模式辅助判断"在打三角洲"
    elif _ocr_text_support_enabled(cfg):
        # OCR 仅文字模式：游戏识别全链路关闭，只保留"读屏幕真文字"喂给视觉
        # 模型（vision.ocr_text_hint）——治"没打死人却报击杀"的视觉脑补。
        # text_only 由 config perception.ocr.text_only 读取，绝不发布时刻判定。
        knowledge = GameKnowledge(cfg)
        ocr = GameOcrMonitor(bus, cfg, monitor, game_knowledge=knowledge, throttle_check=_game_check_or_locked)
    if knowledge is not None:
        analyzer.bind_game_knowledge(knowledge)  # OCR 真文字注入视觉模型（ocr_text_hint 开关）
    persona = Persona.from_config(cfg)
    memory_store = _make_memory_store(cfg)  # 长期记忆：用户纠正/偏好，跨会话保留并注入
    dialogue = Dialogue(bus, llm, state, persona, cfg, game_knowledge=knowledge,
                        app_knowledge=app_knowledge,
                        memory_store=memory_store, session=game_session)
    # 本地 TTS 服务（VoxCPM2/Qwen3-TTS/CosyVoice2）启动时一律不挂载——各自模型加载后
    # 常驻 2.5~10GB 显存，按需在"切换角色"时才拉起 + 加载（见 _sync_*_on_switch）
    tts = TTS(bus, state, cfg)
    ui = ConsoleUI(bus, persona_name=persona.name, dialogue=dialogue, tts=tts)
    asr = ASR(bus, cfg) if not bool(cfg.ns.runtime.text_mode_only) else None

    runtime.ws = ws
    runtime.llm = llm
    runtime.vision_router = vision_router
    runtime.dialogue = dialogue
    runtime.tts = tts
    runtime.asr = asr
    runtime.capture = capture
    runtime.monitor = monitor
    runtime.ocr = ocr
    runtime.input_sensor = input_sensor
    runtime.audio = audio
    runtime.realtime = realtime
    runtime.squad = squad
    runtime.ui = ui
    runtime.knowledge = knowledge
    runtime.memory_store = memory_store

    # speaking 期间暂停 ASR，避免自循环拾音
    if asr is not None:
        async def _pause_asr(e: Event, _a: ASR = asr) -> None:
            _a.pause()

        async def _resume_asr(e: Event, _a: ASR = asr) -> None:
            _a.resume()

        bus.subscribe(EventType.TTS_STARTED, _pause_asr)
        bus.subscribe(EventType.TTS_DONE, _resume_asr)

    # 3. WebSocket
    await ws.start()
    _report_progress(runtime, 22, "启动 WebSocket 服务", phase=2)
    _step(3, total, f"启动 WebSocket ({cfg.ns.websocket.host}:{cfg.ns.websocket.port})")

    # 4. 视觉
    _report_progress(runtime, 38, "初始化视觉模块", phase=3)
    _step(4, total, "初始化视觉模块",
          ok=status.vision_enabled, note="" if status.vision_enabled else "视觉禁用(纯文字)")

    # 4.1 游戏识别（OCR 文字识别，捕捉击杀/胜负）——总开关关闭时整段跳过；
    #     OCR 仅文字模式（text_only）时也显示并启动
    if game_recognition or ocr is not None:
        _report_progress(runtime, 46, "初始化屏幕识别", phase=3)
        _step(4, total + 1, "初始化游戏OCR识别",
              ok=ocr.enabled, note="" if ocr.enabled else "OCR未启用")
        total += 1

    # 5. 语音
    _report_progress(runtime, 55, "初始化语音模块", phase=4)
    _step(5, total, "初始化语音模块",
          ok=status.voice_enabled, note="" if status.voice_enabled else "语音禁用(纯文字模式)")

    # 6. ASR 线程
    if asr is not None:
        asr.start()
        _report_progress(runtime, 68, "启动语音识别", phase=5)
        _step(6, total, "启动 Vosk 识别线程")
    else:
        _step(6, total, "跳过 Vosk 识别线程", ok=False, note="语音未启用")

    # 订阅注册
    dialogue.subscribe()
    tts.subscribe()
    await tts.start_worker()
    _report_progress(runtime, 80, "初始化角色与对话", phase=6)

    # 订阅角色切换事件（同步 TTS 后端）
    async def _sync_tts_on_char_switch(e: Event) -> None:
        d = e.data or {}
        char_id = d.get("char_id", "")
        if not char_id:
            return
        # 无声模式：角色切换不挂载任何本地克隆 TTS 服务（用户明确不要出声），
        # 仅停掉已在运行的释放显存/内存；mute 下即便角色引擎是克隆档也不加载。
        _vcfg = (getattr(cfg, "raw", None) or {}).get("voice", {}) or {}
        if (str(_vcfg.get("mode") or "").strip().lower() == "mute") or _vcfg.get("muted"):
            _unload_all_local_tts(runtime)
            return
        tts.switch_character(char_id)
        # 显存联动：切非 VoxCPM 音色 → 终止 VoxCPM 服务释放 ~6GB；切回 → 自动拉起
        await asyncio.to_thread(_sync_voxcpm_on_switch, runtime, cfg, char_id)
        await asyncio.to_thread(_sync_qwen3tts_on_switch, runtime, cfg, char_id)
        await asyncio.to_thread(_sync_cosyvoice_on_switch, runtime, cfg, char_id)

    bus.subscribe(EventType.CHARACTER_SWITCHED, _sync_tts_on_char_switch)

    _step(7, total, "进入主循环（GUI+控制台）")
    console.print()
    ui.banner(status)

    _print_characters(cfg, dialogue.current_character_id)

    # 主动搭话策略（复用 dialogue 同一个对局会话，本局数据共享）
    proactive = ProactiveStrategy(bus, state, cfg, llm=llm, persona=persona, monitor=monitor,
                                  game_knowledge=knowledge, app_knowledge=app_knowledge,
                                  memory_store=memory_store,
                                  session=game_session)
    proactive.subscribe()
    proactive_enabled = proactive.enabled
    runtime.proactive = proactive
    # 视觉上下文感知（可选）：把策略层的场景状态机/本局战绩喂给视觉模型 prompt，
    # 让模型基于连续信息判断画面变化（治"每帧独立看图瞎猜 + 规则堆叠"）。
    # 仅当游戏 AI 互动开启时注入——用户要求去掉 AI 游戏识别后（game_ai_interaction
    # =false），视觉模型只做纯全局屏幕描述，不注入任何游戏状态上下文（治"桌面说
    # 红薯窝打的怎么样"这类游戏画面脑补）。
    if _proactive_game_ai_interaction(cfg):
        try:
            analyzer.bind_state_provider(proactive)
        except Exception:  # noqa: BLE001 绑定失败不阻断启动，回退纯视觉
            logger.warning("绑定视觉状态提供者失败，回退纯视觉", exc_info=True)
    _duplex_desc = "（规则引信模式：OCR确认关键时刻再开口）"
    if proactive_enabled:
        if proactive._duplex_mode == "realtime":
            _duplex_desc = "（云端真全双工：持续推音画，模型自主开口）"
        elif proactive._duplex_mode:
            _duplex_desc = f"（双工模式：模型自主看屏，每{proactive._duplex_interval_s:.0f}s一查）"
    console.print(f"[dim]主动搭话: {'✓ 启用' if proactive_enabled else '✗ 禁用'}[/]" + _duplex_desc)
    console.print(f"[dim]前台监听: {'✓ 启用' if monitor.enabled else '✗ 禁用(需Windows)'}[/]")
    if game_recognition:
        console.print(f"[dim]游戏OCR: {'✓ 启用' if ocr.enabled else '✗ 禁用'}[/]")
        console.print(f"[dim]队伍识别: {'✓ 启用' if squad.enabled else '✗ 禁用'}[/]")
        console.print(f"[dim]分层视觉: {'✓ 启用' if not realtime.disabled else '✗ 禁用'}[/]")
    elif ocr is not None:
        console.print("[dim]游戏OCR(仅文字): ✓ 扫屏幕真文字喂给视觉模型结合判断，不做时刻判定[/]")
    else:
        console.print("[dim]游戏识别: 已下线（等待重新设计方案）[/]")
    console.print("[dim]角色管理 UI: 已与主程序联动，点击按钮切换角色立即生效[/]")

    # 活动记忆 + 日报/周报（参照 pub-local-jarvis 本地记忆）：订阅感知事件写
    # events.jsonl，跨天/跨周全自动为昨天/上周生成日报周报。
    activity_store = ActivityStore()
    activity_reporter = ActivityReporter(bus, cfg, llm, store=activity_store)
    activity_reporter.subscribe()
    runtime.activity_reporter = activity_reporter

    # 好感度系统（P0 接入）：只订阅事件总线，不碰任何模型/引擎调用。
    # 状态落 data/affinity/<角色>.json、历史落 data/history/<角色>.jsonl，
    # 每轮在日志打一行 [关系]（含心情与三维好感），用于实测。
    try:
        # memory_store → 里程碑"发生即直写记忆"（与 dialogue 同一实例，绝不另开第二写者）
        relationship_reporter = RelationshipReporter(bus, cfg, llm=llm,
                                                     memory_store=memory_store)
        relationship_reporter.subscribe()
        relationship_reporter.start()
        runtime.relationship_reporter = relationship_reporter
        console.print("[dim]关系状态: ✓ 启用（落 data/，看日志 [关系] 行）[/]")
    except Exception:
        logger.exception("关系状态接入失败（不影响主流程）")

    # 网课自动记录（参照 pub-local-jarvis 课程记录）：持续教学行为自动开课，
    # 系统音频转写 + 关键帧 + 滚动总结，下课后生成课程笔记 Markdown。
    course_recorder = CourseRecorder(bus, cfg, llm=llm, vision=vision_router,
                                     store=activity_store)
    course_recorder.subscribe()

    # 社交通道（G 脚手架）：QQ 等外部 IM 接入同一个大脑（默认关闭，按 config.channels 启用）
    await _mount_channels(bus, dialogue, llm, cfg.ns, runtime)
    runtime.course_recorder = course_recorder

    # 后台常驻任务（核心：总线 + 心跳始终运行，供前端通信与 ONBOARDING_DONE 派发）
    _report_progress(runtime, 88, "启动后台服务", phase=6)
    bus_task = asyncio.create_task(bus.run(), name="event_bus")
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(ws, state, cfg.ns.websocket.heartbeat_interval_s), name="heartbeat")
    runtime._tasks = [bus_task, heartbeat_task]
    runtime._heavy_started = False

    first_run = _is_first_run(cfg)
    if first_run:
        # 首启：不预载模型、不启动读屏/双工等重型子系统。等引导完成
        # （ONBOARDING_DONE）再按所选配置拉起。非重型骨架（总线/心跳/对话/LLM）
        # 已就绪，前端可正常完成引导。
        logger.info("首启模式：延迟启动重型子系统，等待引导完成（ONBOARDING_DONE）")

        async def _on_onboarding_done(ev: "Event") -> None:
            if getattr(runtime, "_heavy_started", False):
                return
            try:
                d = ev.data or {}
                logger.info("收到 ONBOARDING_DONE，按所选配置启动重型子系统")
                # 应用引导所选双工模式（run_loop 尚未启动，仅更新实例属性即可）
                _mode = (d.get("duplex_mode") or "").strip()
                if _mode and runtime.proactive is not None:
                    try:
                        runtime.proactive.set_duplex_mode(_mode)
                    except Exception:  # noqa: BLE001
                        logger.exception("首启应用双工模式失败")
                    # 关键修复（2026-09-09）：set_duplex_mode 只改 _duplex_mode，不动 _enabled；
                    # 而 _start_heavy_tasks 用 proactive.enabled 决定要不要起主动循环，
                    # run_loop→_run_realtime_duplex 又用 _enabled 做门禁（False 直接 return）。
                    # 首启时 proactive._enabled 取自旧配置（默认 False），导致选了本地/云端
                    # 双工的模式（全感知伙伴/聊天+看得见/自定义含双工）进去后双工循环不启动、
                    # 本地模型不加载、"进去没有加载对应模型启动对应服务"。这里按所选模式把
                    # _enabled 拉起，确保 _start_heavy_tasks 真正起循环并加载模型。
                    try:
                        runtime.proactive._enabled = True
                    except Exception:  # noqa: BLE001
                        logger.debug("首启置 proactive._enabled=True 失败", exc_info=True)
                _start_heavy_tasks(runtime, cfg)
                runtime._heavy_started = True
                # 桌宠浮窗：引导完成后再显示（首启期间已隐藏，避免配置页提前出现）
                try:
                    _ca = getattr(runtime, "_char_api", None)
                    if _ca is not None:
                        _ca.pet_show()
                    if runtime.tray is not None:
                        runtime.tray.update_pet_visibility(True)
                except Exception:  # noqa: BLE001
                    logger.debug("引导完成后显示桌宠浮窗失败", exc_info=True)
                # 模型预载（阻塞型，放后台线程，避免卡事件循环；无启动进度窗）
                threading.Thread(
                    target=_first_run_preload, args=(runtime, cfg),
                    daemon=True, name="first_run_preload").start()
            except Exception:  # noqa: BLE001
                logger.exception("ONBOARDING_DONE 启动重型子系统失败")

        bus.subscribe(EventType.ONBOARDING_DONE, _on_onboarding_done)
    else:
        # 常规启动：立即启动全部重型子系统（模型预载由监督线程同步进行）
        _start_heavy_tasks(runtime, cfg)
        runtime._heavy_started = True


def _asyncio_thread_target(runtime: RuntimeHolder, cfg: Config) -> None:
    """asyncio 线程入口：初始化 + run_forever，收到 stop 信号后清理。"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runtime.loop = loop

        # 构建组件
        loop.run_until_complete(_build_system(runtime, cfg))

        # 通知主线程：系统已就绪
        runtime.ready.set()
        logger.info("asyncio 线程就绪，run_forever()")

        # 【2026-09-04·UI 卡死诊断】主循环看门狗：asyncio_worker 线程一旦被同步
        # 阻塞（如同步网络/DNS 挂起、同步子进程），所有日志会瞬间停摆、前端画面
        # 冻结。注册 2s 一次存活心跳 + 独立 daemon 线程监控，卡死超 30s 自动 dump
        # 全栈，精确定位卡在哪一行（faulthandler 打印阻塞线程的 Python 调用链）。
        import faulthandler as _fh
        _fh.enable()
        import time as _t, threading as _th
        loop._loop_tick = _t.time()

        def _loop_tick():
            loop._loop_tick = _t.time()
            loop.call_later(2.0, _loop_tick)
        loop.call_soon(_loop_tick)

        def _loop_watchdog():
            while True:
                _t.sleep(10)
                _lag = _t.time() - getattr(loop, "_loop_tick", 0)
                if _lag > 30:
                    logger.error("【主循环看门狗】asyncio 主循环疑似卡死 %.1fs，"
                                 "dump 调用栈定位卡死行：", _lag)
                    _fh.dump_traceback()  # 所有线程 C 级栈（含采样/executor 线程）
                    try:
                        for _tk in asyncio.all_tasks(loop):
                            logger.error("  TASK=%s done=%s", _tk.get_name(), _tk.done())
                            for _fr in _tk.get_stack(limit=24):
                                logger.error("      %s", _fr)
                    except Exception:  # noqa: BLE001
                        pass
        _th.Thread(target=_loop_watchdog, daemon=True, name="loop_watchdog").start()

        # 启动控制台输入循环（在独立线程中，因为 input() 是阻塞的）
        ui = runtime.ui
        if ui is not None:
            threading.Thread(target=_console_input_loop, args=(ui, runtime),
                             daemon=True, name="console_input").start()

        # 保活：run_forever()，直到 stop_loop 被调用
        try:
            loop.run_forever()
        finally:
            # 清理后台任务
            _cleanup_asyncio(runtime, loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    except Exception as e:
        logger.exception("asyncio 线程异常")
        # 记录失败原因，主线程据此决定是否启动 GUI
        runtime.init_error = f"{type(e).__name__}: {e}"
        # 即便初始化失败，也要 ready，避免主线程死等
        runtime.ready.set()


def _console_input_loop(ui: ConsoleUI, runtime: RuntimeHolder) -> None:
    """独立线程跑控制台 input_loop。

    仅当用户显式输入 exit 时才请求关闭 GUI；
    EOF / 无控制台环境（后台启动、stdin 重定向）不关闭 GUI，
    由 GUI 窗口关闭作为唯一退出入口，避免 webview 初始化期间被 destroy。
    """
    explicit = False
    try:
        explicit = asyncio.run(ui.input_loop())
    except Exception:
        logger.exception("控制台输入线程异常")
    finally:
        if explicit:
            logger.info("用户输入 exit，请求关闭 GUI 并停止")
            runtime.stop_requested.set()
        else:
            # EOF / 无控制台：GUI 继续运行，关闭窗口才会退出
            logger.info("控制台输入循环结束（EOF/无控制台），GUI 继续运行")


def _cleanup_asyncio(runtime: RuntimeHolder, loop: asyncio.AbstractEventLoop) -> None:
    """在 asyncio 线程中清理所有资源。"""
    try:
        logger.info("开始清理 asyncio 资源")
        if runtime.voxcpm_proc is not None:
            try:
                runtime.voxcpm_proc.terminate()
                runtime.voxcpm_proc.wait(timeout=5)
                logger.info("已停止 VoxCPM2 服务 (pid=%s)", runtime.voxcpm_proc.pid)
            except Exception:
                logger.warning("停止 VoxCPM2 服务失败（可能已退出）")
        if runtime.qwen3tts_proc is not None:
            try:
                runtime.qwen3tts_proc.terminate()
                runtime.qwen3tts_proc.wait(timeout=5)
                logger.info("已停止 Qwen3-TTS 服务 (pid=%s)", runtime.qwen3tts_proc.pid)
            except Exception:
                logger.warning("停止 Qwen3-TTS 服务失败（可能已退出）")
        if runtime.cosyvoice_proc is not None:
            try:
                runtime.cosyvoice_proc.terminate()
                runtime.cosyvoice_proc.wait(timeout=5)
                logger.info("已停止 CosyVoice2 服务 (pid=%s)", runtime.cosyvoice_proc.pid)
            except Exception:
                logger.warning("停止 CosyVoice2 服务失败（可能已退出）")
        _kill_stray_tts_servers()  # 兜底：杀未被 runtime 跟踪的残留克隆服务进程
        _kill_comni_engine_tree(runtime)  # 兜底：退出时杀常驻的本地全双工 Comni 整树,释放显存
        if runtime.capture is not None:
            runtime.capture.stop()
        if runtime.monitor is not None:
            runtime.monitor.stop()
        if runtime.ocr is not None:
            runtime.ocr.stop()
        if runtime.audio is not None:
            runtime.audio.stop()
        if runtime.realtime is not None:
            runtime.realtime.stop()
        if runtime.squad is not None:
            runtime.squad.stop()
        if runtime.asr is not None:
            runtime.asr.stop()
        if runtime.course_recorder is not None:
            runtime.course_recorder.stop()  # 时长达标会话自动出笔记，否则丢弃
        if runtime.tts is not None:
            runtime.tts.stop()
        if getattr(runtime, "relationship_reporter", None) is not None:
            runtime.relationship_reporter.stop()   # 收尾在线时段（会话时钟）
        if runtime.bus is not None:
            runtime.bus.stop()
        for t in list(runtime._tasks):
            if not t.done():
                t.cancel()
        if runtime.ws is not None:
            # ws.stop() 是协程，需要 run_until_complete
            try:
                loop.run_until_complete(runtime.ws.stop())
            except Exception:
                pass
        # 等待任务取消
        if runtime._tasks:
            try:
                loop.run_until_complete(
                    asyncio.gather(*runtime._tasks, return_exceptions=True)
                )
            except Exception:
                pass
        console.print("[dim]已退出，下次见~[/]")
    except Exception:
        logger.exception("清理 asyncio 资源时出错")


def _kill_stray_tts_servers() -> None:
    """退出兜底：杀掉未被 runtime 跟踪的本地克隆 TTS 服务残留进程。

    正常路径里 runtime.voxcpm_proc/cosyvoice_proc/qwen3tts_proc 已逐个 terminate；
    但若服务是"端口被占用"跳过 Popen 的外部/残留实例，runtime 没有引用，
    退出后仍会占着显存。这里双保险：
      1) 按端口（9881/9882/9883）找占用进程强杀兜底；
      2) 按 python 进程命令行特征（脚本名）兜底 —— 覆盖"端口已释放但进程没退"
         的孤儿实例（如中断重启时 TTS 子进程没跟着退，模型权重仍占显存）。
    不动 9880（GPT-SoVITS 是外部独立软件，由用户自行管理）；
    也不会误杀 Ollama / WorkBuddy 等无关 python 进程。
    """
    _TTS_PORTS = {9881: "VoxCPM2", 9882: "Qwen3-TTS", 9883: "CosyVoice2"}
    _TTS_SCRIPT_HINTS = (
        "qwen3tts_server.py", "sovits", "cosyvoice", "voxcpm",
        "tts_server.py", "gpt_sovits",
    )
    killed: set[int] = set()
    # 1) 按端口找
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="replace", timeout=5).stdout
        for line in out.splitlines():
            for port, label in _TTS_PORTS.items():
                if f":{port} " not in line or "LISTENING" not in line:
                    continue
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pid = int(parts[-1])
                    if pid in killed:
                        continue
                    killed.add(pid)
                    logger.info("兜底清理残留 %s 服务 (pid=%s, 端口兜底)", label, pid)
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                                   timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        pass
    # 2) 按命令行特征找孤儿 python（端口可能已释放，但进程还挂着占显存）
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId,CommandLine | ForEach-Object { "
             "($_.ProcessId.ToString()+'|'+($_.CommandLine -join '')) }"],
            capture_output=True, text=True, errors="replace", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            pid_str, cmd = line.split("|", 1)
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            if pid in killed:
                continue
            cmd_low = (cmd or "").lower()
            # 必须是本项目 TTS 相关脚本，避免误杀 Ollama / WorkBuddy / 其他 python
            if "ai对话" in cmd and any(h in cmd_low for h in _TTS_SCRIPT_HINTS):
                killed.add(pid)
                logger.info("兜底清理残留 TTS 孤儿进程 (pid=%s, 命令行兜底)", pid)
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                               timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        logger.warning("命令行兜底清理 TTS 孤儿进程失败")


def _kill_comni_engine_tree(runtime: RuntimeHolder) -> None:
    """退出兜底：杀掉常驻的本地全双工引擎 Comni 整棵进程树（Comni.exe + llama-server.exe 等），
    释放 ~11-15GB 显存。

    仅在真正退出（_cleanup_asyncio 的 finally，即 loop 已 stop）时触发；托盘最小化走
    on_minimize（不 stop loop）不会跑到这里，故「最小化到托盘」时 Comni 仍常驻、下次启动秒重连，符合预期。
    映像名优先取 proactive 配置里的 Comni.exe 名，回退 'Comni.exe'；按 /T /F 杀整棵树（含所有子进程）。

    2026-09-10 修正：Comni.exe（网关）若在运行期崩溃，子进程 llama-server.exe（实际模型服务，
    ~8-10GB 显存）会变成孤儿继续占卡——仅按 Comni.exe 映像名杀不到它。故此处额外按映像名
    枚举并杀 llama-server.exe，确保崩溃遗留的孤儿也能在退出时被回收（避免「显存赖着不走 / 下次启动碰撞 OOM」）。
    """
    names: set = set()
    try:
        pro = getattr(runtime, "proactive", None)
        if pro is not None:
            exe = (getattr(pro, "_duplex_local_omni_exe", "") or "").strip()
            if exe:
                names.add(os.path.basename(exe).lower())
    except Exception:  # noqa: BLE001
        pass
    if not names:
        names.add("comni.exe")
    # 双工模型实际工作进程（崩溃后会孤儿化，必须一并清理）
    names.add("llama-server.exe")
    killed: set = set()
    for name in names:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, errors="replace", timeout=5,
                creationflags=134217728,
            )
            for line in (out.stdout or "").strip().splitlines():
                parts = [p.strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[0].lower() == name:
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        continue
                    if pid in killed:
                        continue
                    killed.add(pid)
                    try:
                        subprocess.run(
                            ["taskkill", "/T", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=8, creationflags=134217728,
                        )
                        logger.info("退出兜底：已杀 Comni 进程整树 (pid=%s, 映像=%s)", pid, name)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            logger.warning("退出兜底：清理 Comni 进程树失败 (映像=%s)", name)


# ==========================================================
# 启动加载窗口：预载激活角色音色 → 全部加载好才进主界面
# ==========================================================
def _preload_local_duplex_model(runtime: RuntimeHolder, cfg: Config,
                                progress: StartupProgress) -> None:
    """启动时预载本地双工模型（ollama，如 minicpm-o4.5 9B）。

    2026-08-25（用户要求）：本地双工（duplex_mode=local）时，模型首次加载
    10~60s——若启动页不等它，进主页后第一次互动要等模型加载（"不像本地在跑"
    的困惑根源）。这里在启动页阶段就同步 warmup ollama 模型（keep_alive 30m
    驻留），加载页显示"正在加载本地模型…"，加载完才进主界面。失败不阻断
    （本地双工不可用时 proactive 会回退云端）。
    """
    try:
        oc = getattr(cfg.ns, "ollama", None)
        model = (getattr(oc, "vision_model", "") or "").strip()
        if not model:
            return
        host = (getattr(oc, "host", "") or "http://localhost:11434").strip()
        progress.update(86, f"正在加载本地模型「{model}」…（首次约 30~60 秒）", phase=7)
        logger.info("启动预载本地双工模型 %s …", model)
        import json
        import urllib.request
        body = json.dumps({
            # keep_alive 30m → 10m（2026-08-25 显存治理）：本地双工跑着时每
            # 2s 推理会持续刷新驻留，10m 足够；本地双工停后 10m 自动卸载，
            # 避免"锁快照卸载失败/未触发时模型驻留半小时占 5.4GB"。
            "model": model, "prompt": "", "keep_alive": "10m", "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            resp.read()
        progress.update(99, f"本地模型「{model}」加载完成", phase=7)
        logger.info("启动预载本地双工模型完成：%s（已驻留显存）", model)
        # 【预载后兜底卸载（2026-08-25 实锤竞争）】预载（warmup 阻塞）与锁快照
        # 并发时：锁快照的卸载在 ollama 正忙时失败，预载完成后模型驻留没人再
        # 卸 → 游戏+模型挤爆显存（实锤 06:23:58 锁→06:24:13 预载完成→o4.5
        # 驻留 8min 显存 92%）。预载完成后若已锁快照（游戏/大负载场景），
        # 立即同步卸载，把显存还给游戏。
        try:
            _pa = getattr(runtime, "proactive", None)
            if _pa is not None and getattr(_pa, "_duplex_locked", False):
                logger.info("预载完成但已锁快照，卸载预载模型释放显存（还给游戏）")
                _body2 = json.dumps({"model": model, "keep_alive": 0}).encode()
                _req2 = urllib.request.Request(
                    f"{host}/api/generate", data=_body2,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(_req2, timeout=10) as _r2:
                    _r2.read()
        except Exception as _e:  # noqa: BLE001
            logger.warning("预载后卸载失败（%s），模型将按 keep_alive 到期自动卸载", _e)
    except Exception as e:  # noqa: BLE001
        logger.warning("启动预载本地双工模型失败（%s），本地双工不可用将回退云端", e)
        try:
            progress.update(99, "本地模型加载失败，将回退云端模式", phase=7)
        except Exception:  # noqa: BLE001
            pass


def _preload_active_voice(runtime: RuntimeHolder, cfg: Config,
                          progress: StartupProgress) -> None:
    """启动时预载当前激活角色的本地克隆音色模型（全部加载好才进主界面）。

    激活角色用的是 VoxCPM2 / Qwen3-TTS / CosyVoice2 本地克隆引擎时，
    启动阶段就把服务拉起 + 模型加载完成；edge-tts / pyttsx3 / MiniMax 等
    在线或轻量引擎无需预载，GPT-SoVITS 是外部服务也不归本程序管理。
    失败不阻断进主界面（语音会走 edge-tts 兜底）。
    """
    try:
        char_id = getattr(cfg, "active_character_id", "") or ""
        char = cfg.get_character_by_id(char_id) if char_id else None
        if char is None:
            return
        # 【无声模式不预载（2026-08-30 修复）】voice.mode=mute 时用户明确不要任何
        # TTS 出声，即便本机有对应克隆模型也绝不加载（省显存/内存、避免进软件就拉起
        # 大模型）。以 config.yaml[voice][mode] 全局声音档为准，独立于角色引擎。
        _vcfg = (getattr(cfg, "raw", None) or {}).get("voice", {}) or {}
        if (str(_vcfg.get("mode") or "").strip().lower() == "mute") or _vcfg.get("muted"):
            logger.info("激活角色 %s 处于无声模式(voice.mode=mute)，跳过本地音色模型预载", char_id)
            return
        engine = getattr(char.voice, "engine", None)
        if engine not in ("voxcpm", "qwen3tts", "cosyvoice"):
            logger.info("激活角色 %s 音色引擎=%s，无需启动预载", char_id, engine)
            return
        spec = _tts_server_specs().get(engine)
        if spec is None:
            return
        api_url = getattr(char.voice, "api_url", "") or f"http://127.0.0.1:{spec['default_port']}"
        name = getattr(char, "name", "") or char_id
        display = spec["display"]
        progress.update(92, f"正在加载「{name}」的音色模型…", phase=7)
        logger.info("启动预载 %s 音色模型（角色 %s）…", display, char_id)
        result = _mount_tts_service(runtime, cfg, runtime.bus, engine, api_url, name,
                                    progress=progress)
        if result.get("success"):
            progress.update(99, f"「{name}」的音色模型加载完成", phase=7)
            logger.info("启动预载 %s 音色模型完成", display)
        else:
            progress.update(99, f"「{name}」音色模型加载失败，将使用兜底音色", phase=7)
            logger.warning("启动预载 %s 音色模型失败: %s", display, result.get("error"))
    except Exception:  # noqa: BLE001
        logger.exception("启动预载音色模型异常（不影响进入主界面）")


def _destroy_all_webview_windows() -> None:
    """销毁全部 pywebview 窗口（启动失败/退出兜底用，可跨线程调用）。"""
    try:
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                logger.warning("关闭窗口失败（可能已关闭）", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("遍历关闭窗口失败", exc_info=True)


def _diagnose_main_window(runtime: RuntimeHolder) -> None:
    """黑屏排查：窗口稳定 shown 后（约20s），在主窗口真正渲染出来时读取
    innerHTML 长度，客观确认页面是否真的渲染（绕过 events 回调时序问题）。
    跨线程 evaluate_js 在窗口已 shown 时 shown.wait 会立即通过，可成功读取。
    """
    win = getattr(runtime, "_main_win", None)
    if win is None:
        logger.warning("[窗口诊断] 主窗口对象为空")
        return
    try:
        v = win.evaluate_js(
            "(function(){try{return (document.readyState||'')+'|'+(document.body?"
            "document.body.innerHTML.length:-1)+'|'+(document.title||'')}"
            "catch(e){return 'ERR:'+e}})()")
        logger.info("[窗口诊断] %s", v)
    except Exception as e:  # noqa: BLE001
        logger.warning("[窗口诊断] 读取失败: %s", e)


def _main_window_visibility_guard(runtime) -> None:
    """主窗口可见性守护（2026-09-04「窗口凭空消失」排查新增）。

    现象：主窗口先显示"达妮娅正在准备"后从屏幕消失、任务栏预览黑屏、点任务栏
    打不开，但后端/页面 JS 均存活（心跳正常）。为兜住"窗口被意外隐藏/合成失效"
    这类不可复现问题：每 5s 用 Win32 检查主窗口是否仍在屏幕上——既不可见也不
    最小化、且用户并未主动隐藏（main_ui_expected_visible=True）时，判定异常消失，
    自动 show() 救回并打日志。点 X 托盘化（_on_main_closing）、hide_window()
    会先把期望置 False，不会误救；托盘/桌宠"显示主界面"恢复时置回 True。
    """
    import ctypes
    import time as _time
    user32 = ctypes.windll.user32
    while True:
        try:
            _time.sleep(5.0)
            win = getattr(runtime, "_main_win", None)
            if win is None:
                continue
            api = getattr(runtime, "_char_api", None)
            if api is not None and not getattr(api, "main_ui_expected_visible", True):
                continue   # 用户主动隐藏到托盘，不干预
            # Win32 句柄：优先 pywebview winforms Form.Handle，兜底按窗口标题查
            hwnd = 0
            try:
                f = getattr(win, "_window", None)
                if f is not None and hasattr(f, "Handle"):
                    hwnd = int(f.Handle)
            except Exception:  # noqa: BLE001
                hwnd = 0
            if not hwnd:
                try:
                    hwnd = user32.FindWindowW(None, "AI 桌面伴侣 - 角色管理")
                except Exception:  # noqa: BLE001
                    hwnd = 0
            if not hwnd:
                continue   # 找不到句柄（窗口可能已销毁），交由 closed 兜底逻辑处理
            visible = bool(user32.IsWindowVisible(hwnd))
            iconic = bool(user32.IsIconic(hwnd))
            if not visible and not iconic:
                logger.warning(
                    "[窗口守护] 主窗口异常消失（不可见且未最小化、非用户主动隐藏），自动救回")
                try:
                    win.show()
                    logger.info("[窗口守护] 已执行 show() 救回主窗口")
                except Exception:  # noqa: BLE001
                    logger.warning("[窗口守护] show() 救回失败", exc_info=True)
        except Exception:  # noqa: BLE001
            _time.sleep(5.0)


def _startup_supervisor(runtime: RuntimeHolder, cfg: Config,
                        progress: StartupProgress, char_api: CharacterAPI,
                        debug: bool) -> None:
    """启动监督线程（webview.start(func=...) 在独立线程回调）。

    职责：等 pywebview 循环起来 → 环境探测 → 启动 asyncio 线程 →
    等后端全部就绪 → 预载激活角色音色模型 → 注入运行时 → 创建主窗口 →
    关闭加载窗口。全程更新 progress，加载窗口实时轮询展示。
    """
    import webview
    try:
        # 注意（2026-08-24 黑屏根因修复）：本函数现在作为 webview.start(func=...)
        # 的回调在 **GUI 线程** 执行。pywebview 的窗口 shown/loaded 事件依赖 GUI
        # 线程消息循环；若在 webview.start 之后的其他线程里延迟 create_window，
        # 事件永远不 set → 20s 后 evaluate_js/show 抛 "Main window failed to start"
        # → 窗口内容永不渲染（黑屏）。所以这里所有 create_window 必须在 GUI 线程。
        # func 触发时 GUI 已就绪、加载窗已 shown，无需再等待 guilib。
        if webview.guilib is None:
            progress.fail("GUI 初始化失败，请查看日志")
            logger.error("pywebview 初始化失败（guilib 未就绪）")
            return

        # 1. 环境探测（ollama / Vosk / 麦克风 / 视觉模型）
        #    探测很快；进度统一由 asyncio 线程的 _build_system 从 2% 起顺序上报，
        #    这里不单独更新，避免进度回退（6→2）。
        probe_all(cfg)

        # 2. 启动 asyncio 后台线程（内部逐步上报加载进度）
        async_thread = threading.Thread(
            target=_asyncio_thread_target,
            args=(runtime, cfg),
            name="asyncio_worker",
            daemon=True,
        )
        async_thread.start()
        runtime.async_thread = async_thread

        # 3. 等 asyncio 初始化就绪（最多 60s）
        if not runtime.ready.wait(timeout=60.0):
            logger.error("系统初始化超时，请检查日志")
            progress.fail("系统初始化超时，请查看日志")
            time.sleep(2.0)
            _destroy_all_webview_windows()
            return

        # 3.1 检查 asyncio 线程是否崩溃（如端口被占用）
        if runtime.init_error or runtime.dialogue is None:
            err = runtime.init_error or "dialogue 未初始化（未知原因）"
            logger.error("系统初始化失败，无法启动 GUI: %s", err)
            progress.fail(f"系统初始化失败: {err}")
            time.sleep(2.5)
            _destroy_all_webview_windows()
            return

        # 4. 先注入运行时 + 启动聊天采集，再预载音色模型。
        #    真双工会话在 asyncio 线程就绪后即建立、可能立刻开口，而 TTS 预载
        #    需 10~60s——若采集等 TTS 加载完才订阅，启动早期的开口就进不了聊天
        #    记录（实锤：05:01:10 开口，聊天采集 05:01:33 才启动，消息丢失）。
        #    聊天采集只依赖 bus，无需等 TTS，提前订阅不丢消息。
        char_api.set_runtime(
            dialogue=runtime.dialogue,
            bus=runtime.bus,
            loop=runtime.loop,
            tts=runtime.tts,
            capture=runtime.capture,
            proactive=runtime.proactive,
            monitor=runtime.monitor,
            ocr=runtime.ocr,
            audio=runtime.audio,
            asr=runtime.asr,
            memory_store=runtime.memory_store,
            vision_router=runtime.vision_router,
            activity_reporter=runtime.activity_reporter,
            course_recorder=runtime.course_recorder,
            relationship_reporter=runtime.relationship_reporter,
            mount_tts=lambda engine, api_url, name="", device=None: (
                _mount_tts_service(runtime, cfg, runtime.bus, engine, api_url, name, device=device)),
            # 无声模式专用：切到 mute 时由 webapp.set_voice_mode 调用，终止全部本地克隆 TTS 服务
            unload_tts=lambda: _unload_all_local_tts(runtime),
        )
        char_api.start_chat_capture()

        # 5. 预载激活角色的本地克隆音色模型。
        # 注意：改为**后台线程**预载，不阻塞 GUI 线程 / 不延迟主窗口显示。
        # 原来同步等 _preload_active_voice（约 10~60s）会导致主窗口迟迟不出现、
        # 且若放在 webview.start 后的其他线程还会触发跨线程 create 黑屏。
        # 现在 func(GUI 线程) 里只启动线程，主窗口立即显示，TTS 加载完由
        # TTS_LOADED 事件通知 UI（已有机制）。出声前若 TTS 未就绪会自动兜底。
        if not _is_first_run(cfg):
            threading.Thread(
                target=_preload_active_voice,
                args=(runtime, cfg, progress),
                daemon=True,
                name="tts_preload",
            ).start()
        else:
            logger.info("首启模式：跳过启动期音色预载，待引导完成后按配置加载")

        # 5.5 本地双工模型预载（2026-08-25）：duplex_mode=local 时【同步等待】——
        # 启动页显示"正在加载本地模型…"，模型加载完才进主界面（用户要求：
        # "等完全加载完再进入主页，下面文字加提示"，治进主页后第一次互动等
        # 模型加载的困惑）。仅在本地双工模式预载；失败不阻断（回退云端）。
        try:
            _dm = getattr(getattr(cfg.ns, "proactive", None), "duplex_mode", False)
            if _dm == "local":
                # 【启动即快照（2026-08-25 实锤）】游戏已在运行时跳过模型预载：
                # 锁快照不需要本地模型，预载只会白加载 5.4GB 又卸载 + 启动 CPU/
                # 显存峰值（用户反馈"软件加载中弹快照但模型没卸载、CPU 高得离谱"）。
                # 判断用"前台游戏 OR 显存已紧张(≥60%)"双通道——启动早期前台
                # 识别（is_game_foreground）可能未就绪返回 False，显存判断
                # 不依赖前台，游戏在跑（占显存）时必命中。
                _pa = getattr(runtime, "proactive", None)
                _mon = getattr(_pa, "_monitor", None) if _pa is not None else None
                _game_on = False
                try:
                    _gl = gpu_load()
                    if _gl:
                        # 排除"我们的模型"（config ollama.vision_model）——用户
                        # 自己用 ollama 跑的其他模型不算，其显存计入外部负载
                        # （2026-08-25 白名单化，防用户模型+游戏挤爆不触发锁）。
                        # 阈值 70 与锁模式状态机一致（旧 60 实锤死循环：游戏
                        # 后台挂外部 66% → 状态机解锁但启动判断又锁，每 20s
                        # 循环弹窗）。
                        _own = [str(getattr(getattr(cfg.ns, "ollama", None),
                                            "vision_model", "") or "").strip()]
                        _oll = ollama_resident_mb(_own)
                        _ext_mem = max(0.0, float(_gl.get("used_mb") or 0) - _oll)
                        _ext_pct = (_ext_mem / float(_gl.get("total_mb") or 1)
                                    * 100.0)
                        # 阈值 60 与"锁定期保持锁"一致（触发锁 70 防本地推理
                        # 自锁）——游戏在菜单/大厅（外部 ~60%、util 低）也命中
                        # 跳过预载（实锤 06:20：游戏在跑但预载完成显存 90% 挤）
                        if _ext_pct >= 60.0 or float(_gl.get("util_pct") or 0) >= 60.0:
                            _game_on = True
                except Exception:  # noqa: BLE001
                    _game_on = False
                if _mon is not None and not _game_on:
                    try:
                        _game_on = bool(_mon.is_game_foreground())
                    except Exception:  # noqa: BLE001
                        _game_on = False
                if _game_on:
                    logger.info("启动检测: 游戏/大负载已在运行，跳过本地模型预载（直接快照模式）")
                else:
                    if not _is_first_run(cfg):
                        _preload_local_duplex_model(runtime, cfg, progress)
                    else:
                        logger.info("首启模式：跳过启动期本地双工预载，待引导完成后按配置处理")
        except Exception:  # noqa: BLE001
            logger.exception("本地双工模型预载异常（不阻断进入主界面）")

        # 6. 100% 进度，打开主窗口、关闭加载窗口。
        # 关键（2026-08-24 黑屏/未响应根因）：本函数在 webview.start(func=...) 的
        # GUI 线程回调里执行。func 返回前 GUI 主循环未启动，窗口 shown 事件不会 set。
        # 因此**绝不能在 func 里调用 main_win.show()**（show 内部 shown.wait(20) 会
        # 死锁 → 加载窗未响应、主窗不显示）。create_window 默认即显示，直接创建即可。
        progress.update(100, "加载完成，正在进入软件…", phase=8)
        progress.done()

        main_win = create_main_window(api=char_api)
        if main_win is None:
            logger.error("主窗口创建失败")
            progress.fail("主窗口创建失败")
            return

        # 将主窗口和CharacterAPI存储到runtime对象，供托盘回调使用
        runtime._main_win = main_win
        runtime._char_api = char_api
        logger.info("主窗口已创建（默认显示，func 返回后主循环接管）")

        # GUI 消息泵黑匣子（2026-09-04「Python 未响应」）：确定性安装——
        # v1 曾依赖 shown 事件安装，实测可能未触发导致卡死时无现场。
        try:
            _start_gui_liveness_probe(main_win)
        except Exception:  # noqa: BLE001
            logger.warning("[GUI探针] 安装失败", exc_info=True)

        # 主窗口可见性守护（2026-09-04「窗口凭空消失」排查）：异常消失自动救回
        threading.Thread(target=_main_window_visibility_guard, args=(runtime,),
                         daemon=True, name="mainwin-vis-guard").start()

        for w in list(webview.windows):
            if w is not main_win:
                try:
                    w.destroy()
                except Exception:  # noqa: BLE001
                    logger.warning("关闭加载窗口失败（可能已关闭）", exc_info=True)

        # 桌面桌宠（浮窗模式）：注册窗口引用；主窗口关闭后接管，继续在桌面展示。
        # 平时由主界面"桌宠模式"开关控制显示（pet_show/pet_hide），
        # 桌宠内左键点击互动 / 拖动移动（下半身摆动）/ 右键操作面板。
        try:
            desk_win = create_desktop_companion(api=char_api)
            char_api.set_windows(main_win=main_win, pet_win=desk_win)
            _attach_pet_no_taskbar(desk_win)   # 桌宠不占任务栏/Alt+Tab

            # 首启引导期间：桌宠浮窗不提前出现，配置完成（ONBOARDING_DONE）后再显示
            if _is_first_run(cfg):
                try:
                    desk_win.hide()
                    logger.info("首启引导期间：桌宠浮窗暂不显示（待引导完成后出现）")
                except Exception:  # noqa: BLE001
                    logger.debug("首启隐藏桌宠浮窗失败", exc_info=True)

            # 模式切换通知浮窗（GUI 线程创建）。【2026-09-04 18:57 定版】按用户要求
            # 保留 ACE 式独立浮窗：无边框置顶独立窗 + Win32 物理像素钉位右上角 +
            # TOOLWINDOW 去任务栏（不入任务栏/Alt+Tab 预览）。运行时命令经
            # /notify_poll HTTP 通道送达（独立窗的 pywebview 桥已实测不通）。
            try:
                from src.ui import native_notify
                native_notify.ensure_notify_window()
            except Exception as e:  # noqa: BLE001
                logger.warning("[通知浮窗] 启动期创建失败（不影响主功能）: %s", e)

            # 主窗口关闭拦截 → QQ 式：点 X 不销毁窗口，改为隐藏到托盘（保留窗口实例，
            # 托盘/桌宠还原时直接 show 同一实例，瞬时显示、不重载页面）。
            # pywebview events.closing 处理器返回 False 即取消关闭（winforms 里 args.Cancel=True）。
            def _on_main_closing(_win=None):
                try:
                    # 取证：记录关闭请求到达时的前台窗口，帮助定位"窗口凭空消失"
                    try:
                        import ctypes
                        u32 = ctypes.windll.user32
                        fg = u32.GetForegroundWindow()
                        buf = ctypes.create_unicode_buffer(256)
                        u32.GetWindowTextW(fg, buf, 256)
                        fg_title = buf.value or '(未知)'
                    except Exception:  # noqa: BLE001
                        fg_title = '(读取失败)'
                    # 真实退出（quit_app / 系统关机）→ 放行关闭，不再拦截
                    if getattr(char_api, '_app_quitting', False):
                        logger.info("主窗口关闭放行（正在退出程序；关闭时前台=%s）", fg_title)
                        return True
                    logger.info("主窗口关闭被拦截 → 隐藏到托盘（QQ 式，窗口实例保留；"
                                "关闭时前台=%s，可用托盘『显示主界面』或桌宠右键『回到主窗口』找回）",
                                fg_title)
                    char_api.main_ui_expected_visible = False   # 窗口守护：主动隐藏不误救
                    # 首启引导未完成（重型子系统未启动）时不抢显桌宠，避免配置页提前出现
                    if getattr(runtime, "_heavy_started", True):
                        char_api.pet_show()
                    if runtime.tray:
                        runtime.tray.update_pet_visibility(True)
                        runtime.tray.update_main_window_visibility(False)
                    if getattr(runtime, '_main_win', None) is not None:
                        runtime._main_win.hide()
                except Exception:  # noqa: BLE001
                    logger.warning("拦截主窗口关闭失败", exc_info=True)
                return False  # 取消关闭 → 隐藏而不是销毁

            main_win.events.closing += _on_main_closing

            # 兜底：若窗口仍被真正关闭（非 X 拦截路径，如 quit_app），桌宠接管
            def _on_main_closed(_win=None):
                try:
                    char_api.mark_main_window_closed()
                    # 首启引导未完成时不抢显桌宠
                    if getattr(runtime, "_heavy_started", True):
                        char_api.pet_show()
                except Exception:  # noqa: BLE001
                    logger.warning("主窗口关闭后显示桌宠失败", exc_info=True)
            main_win.events.closed += _on_main_closed
            logger.info("桌面桌宠窗口已就绪（主界面可随时开启桌宠模式，点 X 隐藏到托盘）")
        except Exception:  # noqa: BLE001
            logger.warning("桌面桌宠窗口创建失败，仅保留聊天页内看板娘", exc_info=True)

        # 百炼控制台内嵌窗口（启动期创建、隐藏、懒加载；用户点按钮才显示真实额度页）。
        # 必须在启动期 GUI 线程建好，绝不能运行时二次 create_window（会触发 WebView2 STA 死锁）。
        try:
            create_bailian_console_window(api=char_api)
        except Exception as e:  # noqa: BLE001
            logger.warning("[百炼控制台] 启动期创建失败（不影响主功能）: %s", e)

        logger.info("全部加载完成，主窗口已打开")
        # 黑屏排查：窗口稳定 shown 后延迟诊断（此时跨线程 evaluate_js 可成功读取）
        threading.Timer(20.0, _diagnose_main_window, args=(runtime,)).start()
    except Exception:  # noqa: BLE001
        logger.exception("启动监督线程异常")
        try:
            progress.fail("启动异常，请查看日志")
        except Exception:  # noqa: BLE001
            pass


# ==========================================================
# 主入口：启动 asyncio 线程 + pywebview 主线程
# ==========================================================
# 【2026-09-18 修复】runtime 是 main() 的局部变量，模块级函数里直接引用会抛
# NameError —— 也就是日志里那条「退出卸载 Ollama 模型异常（可忽略）: name
# 'runtime' is not defined」。它并不只是噪音：这个卸载**从来没真正执行过**，
# 关掉软件后 Ollama 模型一直占着显存。
# 改法：允许调用方传入 runtime；崩溃/atexit 路径拿不到时使用模块级单例兜底。
_runtime_singleton = None


def _unload_ollama_models(_rt=None) -> None:
    """退出时把显存中的本地 Ollama 模型卸载，避免关软件后显卡仍被占用。

    顺着 runtime → proactive → OllamaClient 找到客户端并卸载。
    服务不可达（未用本地模型）或模型未加载时静默跳过。

    _rt：显式传入的 runtime（正常退出路径）。为空时退回模块级单例。
    """
    try:
        rt = _rt if _rt is not None else globals().get("_runtime_singleton")
        if rt is None:
            return
        pro = getattr(rt, "proactive", None)
        if pro is None:
            return
        llm = getattr(pro, "_llm", None)
        if llm is None:
            return
        unload = getattr(llm, "unload_current_model", None)
        if callable(unload):
            unload()
    except Exception as exc:  # noqa: BLE001
        logger.warning("退出卸载 Ollama 模型异常（可忽略）: %s", exc)


def main() -> None:
    # 🔧 壳层前置（顺序敏感，勿后移）：DPI 感知与单实例互斥体都必须在**任何窗口
    # 创建之前**完成。放到下一步的 faulthandler 之后也能跑，但那样一旦有人在两者
    # 之间插入建窗代码，DPI 感知就会被系统静默忽略（创建窗口后再设无效），且不会有
    # 任何报错——所以固定放在函数最前面。
    #   · DPI：进程默认不感知 → 高缩放（150%）下整窗被系统位图拉伸，文字必糊。
    #   · 单实例：无互斥体 → 双击两次得到两个进程、两只宠物、两套后端。
    # --no-gui 是控制台工具模式，允许与 GUI 实例并存，故跳过单实例检查。
    try:
        from src.win_shell import (
            acquire_single_instance as _acquire_single,
            activate_existing_window as _activate_existing,
            dpi_awareness as _dpi_awareness,
            enable_dpi_awareness as _enable_dpi,
        )

        _dpi_mode = _enable_dpi()
        if _dpi_awareness() <= 0:
            logger.warning("[壳层] DPI 感知未生效（方式=%s），高缩放下界面会发虚", _dpi_mode)
        else:
            logger.info("[壳层] DPI 感知已启用：%s", _dpi_mode)

        if "--no-gui" not in sys.argv and not _acquire_single():
            _activate_existing()
            print("达妮娅已在运行，已为你切到已有窗口（本次启动退出）")
            return
    except Exception as _e:  # noqa: BLE001  壳层能力失败不得拦住主流程
        logger.warning("[壳层] DPI/单实例初始化异常（继续启动）：%s", _e)

    # faulthandler 仅在【真实崩溃/致命信号】时转储，用于事后定位根因。
    # ⚠️ 原"每 45s 无条件 dump_traceback_later"会持续把全线程栈刷进
    # logs/fault_watchdog.log（曾累积 10w+ 行），且与是否卡死无关——纯属噪音，
    # 极易让人误判成"又卡死了"，是上一轮"拆东墙补西墙"式修复的误导源。
    # 真正的卡死探测交给下方 _loop_watchdog（主循环滞后 >30s 才 dump 全栈），
    # 以及 webapp.py 的 GUI 探针（GUI 线程心跳停 15s 抓 gui_freeze_*.txt）。
    # 故这里只 enable，不再做周期性 dump。
    # 🛡️【2026-09-27 闪退修复】崩溃现场落盘 + 未捕获异常钩子。
    # 原 faulthandler 只打到 stderr（GUI 模式下丢失），且没有任何 excepthook——
    # 主线程/GUI 线程的 Python 未捕获异常与 C 层致命信号全部无声丢失，日志戛然而止。
    # 现在：① faulthandler 同时写 logs/crash_<ts>.log；② sys/threading excepthook 把
    # 未捕获异常写同一文件 + logger。下次再崩，直接看该文件即可定位根因。
    try:
        import faulthandler as _fh
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _fault_path = os.path.join(
            _log_dir, "crash_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
        _fault_fh = open(_fault_path, "w", encoding="utf-8", buffering=1)
        _fh.enable(file=_fault_fh)

        import traceback as _tb

        def _uncaught(etype, evalue, etb) -> None:
            try:
                with open(_fault_path, "a", encoding="utf-8") as _f:
                    _tb.print_exception(etype, evalue, etb, file=_f)
                logger.error("未捕获异常（已写入 %s）: %s: %s",
                             _fault_path, getattr(etype, "__name__", "?"), evalue)
            except Exception:  # noqa: BLE001
                pass

        sys.excepthook = _uncaught
        try:
            threading.excepthook = lambda args: _uncaught(
                args.exc_type, args.exc_value, args.exc_traceback)
        except Exception:  # noqa: BLE001
            pass
        logger.info("[崩溃守卫] faulthandler+excepthook 已启用，崩溃现场将写入 %s", _fault_path)
    except Exception as _e:
        print(f"[崩溃守卫] 启用失败（不影响运行）: {_e}")
        try:
            import faulthandler as _fh2
            _fh2.enable()
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="AI 桌面伴侣")
    parser.add_argument("--no-gui", action="store_true", help="只启动控制台模式，不打开 GUI")
    parser.add_argument("--debug-gui", action="store_true", help="GUI 调试模式（允许右键检查元素）")
    parser.add_argument("--config", default="config.yaml", help="指定配置文件（默认 config.yaml）")
    parser.add_argument("--video", default=None,
                        help="视频测试模式：提交录屏路径，后台跑完整 OCR→AI 互动流程并生成对应报告（静默不出声）")
    parser.add_argument("--video-sample-sec", type=float, default=0.5,
                        help="视频测试抽帧间隔(秒)：每 N 秒抽 1 帧做 OCR（游戏 UI 变化慢，"
                             "默认 0.5s≈提速数十倍）。调小更准更慢，调大更快但可能漏瞬态关键时刻")
    parser.add_argument("--video-game", default="三角洲行动",
                        help="视频测试模式：录像对应的游戏名（强制钉死 game_knowledge，"
                             "避免实时前台监听把 current_name/页面状态带偏）。默认 三角洲行动")
    args = parser.parse_args()

    # 退出兜底：即使崩溃/强杀没走 on_exit，也尽量卸载本地模型释放显存
    atexit.register(_unload_ollama_models)

    cfg = load_config(args.config)
    log_file = setup_logging(cfg.ns.app.log_level)
    console.print(f"[dim]日志文件: {log_file}[/]")

    # GPU 显存优化：把浏览器/QQ/微信等普通软件预设到核显（节能），下次启动生效
    try:
        from src.system.gpu_prefs import apply_gpu_preferences

        gpu_res = apply_gpu_preferences()
        if gpu_res.get("status") != "no_igpu":
            console.print(f"[dim]GPU 显存优化: {gpu_res['msg']}[/]")
    except Exception:  # noqa: BLE001
        logger.debug("GPU 偏好预设失败（不影响启动）", exc_info=True)

    # ---- 视频测试模式（headless，复用 --no-gui 启动链路，静默不出声） ----
    if args.video:
        probe_all(cfg)
        try:
            asyncio.run(bootstrap_legacy(cfg, video_path=args.video,
                                     video_sample_sec=args.video_sample_sec,
                                     video_game=args.video_game))
        except KeyboardInterrupt:
            console.print("\n[dim]中断退出[/]")
        return

    # ---- 纯控制台模式（原 asyncio.run 行为） ----
    if args.no_gui:
        probe_all(cfg)
        try:
            asyncio.run(bootstrap_legacy(cfg))
        except KeyboardInterrupt:
            console.print("\n[dim]中断退出[/]")
        return

    # ---- GUI 模式：先弹加载窗口 → 后端全部加载好 → 再打开主窗口 ----
    runtime = RuntimeHolder(cfg=cfg)
    progress = StartupProgress()
    runtime.progress = progress
    # 登记到模块级单例：崩溃 / atexit / 强杀路径也能卸载 Ollama 显存
    global _runtime_singleton
    _runtime_singleton = runtime

    # 构建 CharacterAPI（运行时注入推迟到加载完成后，见 _startup_supervisor）
    char_api = CharacterAPI("config.yaml")
    char_api._use_shared_cfg(cfg)

    # 1. 创建启动加载窗口（小窗口，与 QQ 登录窗口同尺寸；全部加载好才切换主窗口）
    import webview
    from src.ui.webapp import _ensure_web_server
    # 白屏修复（2026-08-24 实锤）：pywebview 所有窗口共用同一 WebView2
    # UserDataFolder，多窗口并发初始化会 E_ABORT → 白屏。必须在创建任何窗口
    # 之前打补丁，为每个窗口分配独立 folder。
    _patch_webview2_unique_user_data()
    loading_url = _ensure_web_server() + "/loading.html"
    webview.create_window(
        title="AI 桌面伴侣 - 启动中",
        url=loading_url,
        js_api=LoadingAPI(progress),
        width=500,
        height=550,
        resizable=False,
        text_select=False,
    )

    # 2. 启动逻辑已移入 webview.start(func=_boot_gui) 的回调（GUI 线程执行），
    # 避免跨线程延迟 create_window 导致窗口事件不 set → 黑屏。此处不再单独起线程。

    # 3. 开独立线程轮询 stop_requested，当控制台 exit 时自动关闭 webview 窗口
    def _auto_close_when_stop_requested() -> None:
        # 启动保护期：前 5 秒不响应 stop_requested，避免 webview 初始化期间被 destroy
        # （历史 bug：后台启动时 stdin EOF 触发控制台线程退出 → destroy 窗口 → E_ABORT）
        time.sleep(5.0)
        while not runtime.stop_requested.is_set():
            time.sleep(0.2)
        try:
            for window in webview.windows:
                try:
                    window.destroy()
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=_auto_close_when_stop_requested, daemon=True, name="gui_closer").start()

    # 3. 初始化系统托盘
    runtime.tray = TrayManager()
    runtime.minimize_to_tray = True  # 默认最小化到托盘
    
    # 设置托盘回调函数
    def on_exit():
        """退出程序回调。"""
        logger.info("托盘退出回调")
        runtime.stop_requested.set()
        # 标记正在退出，使主窗 FormClosing 拦截放行真正关闭（而非误判为隐藏）
        try:
            _ca = getattr(runtime, "_char_api", None)
            if _ca is not None:
                _ca._exiting = True
        except Exception:  # noqa: BLE001
            pass
        # 退出时主动卸载本地 Ollama 模型，释放显存（避免关软件后显卡仍被占用）
        _unload_ollama_models(runtime)
        if runtime.loop is not None:
            try:
                runtime.loop.call_soon_threadsafe(runtime.loop.stop)
            except Exception:
                pass
        if runtime.tray:
            runtime.tray.stop()
    
    def on_show_main():
        """显示主界面回调。"""
        logger.info("托盘显示主界面回调")
        if runtime.tray:
            runtime.tray.update_main_window_visibility(True)

        char_api = getattr(runtime, '_char_api', None)
        # 与 CharacterAPI 里的窗口引用保持同步（桌宠"回到主窗口"可能已重建窗口）
        if char_api is not None:
            runtime._main_win = getattr(char_api, '_main_win', None)

        _main_closed = bool(char_api is not None and getattr(char_api, '_main_win_closed', False))
        logger.info("托盘显示主界面: _main_win_closed=%s 持有主窗口=%s",
                    _main_closed, "有" if runtime._main_win else "无")

        # 主窗口已关闭（后台模式桌宠接管）→ 直接重建，而不是去 show 已销毁的窗口
        if _main_closed:
            logger.info("主窗口已关闭，重新创建主窗口")
            main_win = char_api.recreate_main_window()
            if main_win is not None:
                runtime._main_win = main_win
                logger.info("新主窗口已创建并显示")
            else:
                logger.error("主窗口重建失败: recreate_main_window 返回 None "
                             "（托盘『显示主界面』无效的可疑根因）")
            return

        # 检查主窗口是否存在
        if hasattr(runtime, '_main_win') and runtime._main_win:
            try:
                import webview

                # 窗口守护：托盘主动恢复显示 → 重置"期望可见"标志
                if char_api is not None:
                    char_api.main_ui_expected_visible = True

                # 尝试多种方法来显示窗口
                methods = []
                
                # 方法1: 直接调用webview的show方法
                try:
                    if hasattr(runtime._main_win, '_window'):
                        # 如果有_window属性，尝试直接操作它
                        window_obj = runtime._main_win._window
                        if hasattr(window_obj, 'show'):
                            window_obj.show()
                            methods.append("window.show")
                        elif hasattr(window_obj, 'setVisible'):
                            window_obj.setVisible(True)
                            methods.append("window.setVisible")
                    elif hasattr(runtime._main_win, 'show'):
                        runtime._main_win.show()
                        methods.append("show")
                except Exception as e:
                    logger.warning(f"show方法失败: {e}")
                
                # 方法2: 尝试focus
                try:
                    if hasattr(runtime._main_win, 'focus'):
                        # 检查focus是方法还是属性
                        if callable(runtime._main_win.focus):
                            runtime._main_win.focus()
                            methods.append("focus")
                        else:
                            # 如果是属性，尝试设置它
                            runtime._main_win.focus = True
                            methods.append("focus.set")
                except Exception as e:
                    logger.warning(f"focus方法失败: {e}")
                
                # 方法3: 尝试restore
                try:
                    if hasattr(runtime._main_win, 'restore'):
                        runtime._main_win.restore()
                        methods.append("restore")
                except Exception as e:
                    logger.warning(f"restore方法失败: {e}")
                
                # 方法4: 尝试unhide
                try:
                    if hasattr(runtime._main_win, 'unhide'):
                        runtime._main_win.unhide()
                        methods.append("unhide")
                except Exception as e:
                    logger.warning(f"unhide方法失败: {e}")

                # 注意：不再做 load_url 强制整页重载兜底——那会让"还原"变成"重新加载"，
                # 破坏 QQ 式的瞬时还原体验。窗口实例保持存活时，show/restore 足够还原。
                
                logger.info(f"已尝试显示方法: {methods}")
                
                # 如果有成功的方法，记录成功
                if methods:
                    logger.info("主窗口恢复操作完成")
                else:
                    logger.warning("所有显示方法都失败了")
                
            except Exception as e:
                logger.error("恢复主窗口失败: %s", e)
        else:
            logger.warning("主窗口不存在，需要重新创建（runtime._main_win 为空）")
            # 如果主窗口不存在，重新创建（这种情况应该很少发生）
            if char_api is not None:
                main_win = char_api.recreate_main_window()
                if main_win is not None:
                    runtime._main_win = main_win
                    logger.info("新主窗口已创建并显示")
                else:
                    logger.error("重建主窗口失败（runtime._main_win 为空分支）：recreate_main_window 返回 None")
    
    def on_show_pet(hide=False):
        """显示/隐藏桌宠回调。"""
        logger.info(f"托盘显示/隐藏桌宠回调: hide={hide}")
        if runtime.tray:
            runtime.tray.update_pet_visibility(not hide)
        # 这里需要通过CharacterAPI来控制桌宠
        if hasattr(runtime, '_char_api') and runtime._char_api:
            try:
                if hide:
                    runtime._char_api.pet_hide()
                else:
                    runtime._char_api.pet_show()
            except Exception as e:
                logger.error("控制桌宠显示/隐藏失败: %s", e)
    
    # 托盘回调函数已在上面定义
    
    def on_minimize():
        """最小化到托盘回调。"""
        logger.info("托盘最小化回调")
        if runtime.tray:
            runtime.tray.update_main_window_visibility(False)
        # 与 CharacterAPI 里的窗口引用保持同步（桌宠"回到主窗口"可能已重建窗口）
        char_api = getattr(runtime, '_char_api', None)
        if char_api is not None:
            runtime._main_win = getattr(char_api, '_main_win', None)
        # 隐藏主窗口但保持程序运行
        if hasattr(runtime, '_main_win') and runtime._main_win:
            runtime._main_win.hide()
    
    # 绑定回调函数到托盘管理器
    runtime.tray.on_show_main_callback = on_show_main
    runtime.tray.on_show_pet_callback = on_show_pet
    runtime.tray.on_exit_callback = on_exit
    
    # 启动托盘
    if runtime.tray:
        runtime.tray.set_callbacks(
            on_exit=on_exit,
            on_show_main=on_show_main,
            on_show_pet=on_show_pet,
            on_minimize=on_minimize
        )
        
        # 启动托盘
        if runtime.tray.start("AI桌面伴侣"):
            logger.info("系统托盘已启动")
        else:
            logger.warning("系统托盘启动失败，将使用默认退出行为")

    # 4. 启动 pywebview（阻塞，直到所有窗口关闭 或 stop_requested）。
    # 关键（2026-08-24 黑屏根因修复）：把启动监督逻辑作为 func 回调传入，
    # 使其在 GUI 线程执行 —— 所有 create_window 必须在 GUI 线程，否则窗口
    # shown/loaded 事件不 set → 黑屏。func 内已改为 TTS 后台预载，不阻塞 UI。
    try:
        logger.info("启动 pywebview：加载窗口 → 后端加载 → 主窗口（func 在 GUI 线程）")

        def _boot_gui() -> None:
            _startup_supervisor(runtime, cfg, progress, char_api, args.debug_gui)

        webview.start(func=_boot_gui, debug=args.debug_gui)
    except KeyboardInterrupt:
        console.print("\n[dim]中断退出[/]")
    finally:
        # 5. 请求 asyncio 线程停止
        runtime.stop_requested.set()
        if runtime.loop is not None:
            try:
                runtime.loop.call_soon_threadsafe(runtime.loop.stop)
            except Exception:
                pass
        async_thread = getattr(runtime, "async_thread", None)
        if async_thread is not None:
            async_thread.join(timeout=10.0)

    # 启动失败（asyncio 崩溃/超时/初始化异常）时以错误码退出
    if runtime.init_error or runtime.dialogue is None:
        sys.exit(1)


# ==========================================================
# 兼容旧入口：纯控制台模式（--no-gui）
# ==========================================================
async def bootstrap_legacy(cfg: Config, video_path: str | None = None,
                         video_sample_sec: float = 0.5,
                         video_game: str = "三角洲行动") -> None:
    """旧的 bootstrap 方式（asyncio.run），供 --no-gui / --video 模式调用。"""
    total = 7
    status = cfg.runtime_status

    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.bind_loop(loop)
    runtime = RuntimeHolder(cfg=cfg)
    runtime.bus = bus
    runtime.loop = loop
    global _runtime_singleton
    _runtime_singleton = runtime
    state = StateMachine(bus)
    console.print(f"[1/{total}] 加载配置 config.yaml ... [green]✓[/]")

    _step(2, total, f"检查 LLM({status.llm_provider or 'ollama'}) / Vosk / 麦克风", ok=status.ollama_ok,
          note="" if status.ollama_ok else "见下方降级提示")
    for d in status.degradations:
        console.print(f"       [yellow]⚠ {d}[/]")

    ws = WebSocketServer(bus, cfg.ns.websocket.host, cfg.ns.websocket.port)
    llm = create_llm(cfg)  # 对话 LLM（chat）
    vision_router = create_vision_router(cfg)  # 视觉模型路由：云端优先、本地兜底
    monitor = ForegroundMonitor(bus, cfg)
    # 游戏进行中禁用本地视觉模型（防 4.8GB 本地模型抢 GPU 卡游戏）
    # 2026-08-25：扩展为"游戏前台 或 锁快照中"——锁快照（大 GPU 负载让位）
    # 期间视觉也绝不加载本地 ollama 模型（实锤：锁快照后用户切出游戏，视觉
    # 云端失败 → 本地 o2.6 4.8GB 又占显存，显存回到 92%）。proactive 在
    # vision_router 之后构造，用闭包延迟读取锁状态。
    def _game_check_or_locked() -> bool:
        pa = getattr(runtime, "proactive", None)
        locked = bool(getattr(pa, "_duplex_locked", False)) if pa is not None else False
        return monitor.is_game_foreground() or locked
    # game_check 保持"游戏/锁分"原语义：它是**安全闸**，让 VisionRouter 在游戏中绝不加载
    # 本地 ollama 模型（防本地大模型抢显卡）。下面新加的 throttle_check 才受「游戏时云端
    # 看屏」开关影响，两者职责不同、不可混用。
    vision_router.set_game_check(_game_check_or_locked)

    def _screen_throttle_check() -> bool:
        """游戏/锁分时是否让位（暂停云端 VLM 持续看屏）。

        【2026-09-28】「游戏时云端看屏」开关开启 且 确有可用云端视觉模型时，游戏期间
        **不节流**，由云端 VLM 接管持续读屏并主动互动（期间本地模型仍被 game_check 禁掉，
        不占显卡）；否则沿用原行为——低频抓屏、不触发视觉分析，让位游戏省 CPU/GPU。
        只作用于 ScreenCapture 这条独立看屏管线，**语音双工完全不受影响**（两条管线本就
        解耦，不为看屏去动语音引擎）。

        【2026-10-13 补 · 放开 throttle 不等于放开性能闸】放开节流只解决"云端能不能
        看得见"；采样节奏另由 `low_rate_check` 兜住（同一个 `_game_check_or_locked`
        来源）：游戏/锁分期间**始终**走 `screenshot.game_interval_s` 低频档并关闭静态
        加速，避免"画面静止 → static 加速到 ~1s → 每帧全屏抓屏 + JPEG 编码"把
        2026-08-25 修掉的抽帧/核显本卡炸带回来。等价于：云端 VLM 接管看屏，但本地
        CPU/GPU 开销与让位时同档。回退本地 ollama 那条路依旧由 game_check 挡住
        （游戏/通话中绝不加载本地大模型抢显存）。
        """
        if not _game_check_or_locked():
            return False
        return not _game_cloud_watch_active(cfg)
    analyzer = VisionAnalyzer(vision_router, bus, cfg, monitor=monitor)
    # low_rate_check：与 throttle 同源，保证「不节流」时也不提升采样频率（防性能回退）。
    capture = ScreenCapture(bus, analyzer, cfg, throttle_check=_screen_throttle_check,
                            low_rate_check=_game_check_or_locked)
    # 游戏识别组件（总开关 game_recognition.enabled=false 时整体下线，源码保留）
    game_recognition = _game_recognition_enabled(cfg)
    knowledge: Optional[GameKnowledge] = None
    app_knowledge = AppKnowledge(cfg)  # 软件知识库：按前台窗口标题命中软件，注入该软件说明
    ocr: Optional[GameOcrMonitor] = None
    audio: Optional[GameAudioMonitor] = None
    realtime: Optional[RealtimeVision] = None
    squad: Optional[SquadMonitor] = None
    game_session: Optional[DeltaGameSession] = None
    input_sensor: Optional[InputSensor] = None
    if game_recognition:
        knowledge = GameKnowledge(cfg)  # 游戏知识库：识别游戏，喂给 AI 游戏名+攻略+数据
        input_sensor = InputSensor()  # 输入证据门（v2）：击杀判定必须有"开枪"证据
        ocr = GameOcrMonitor(bus, cfg, monitor, llm=llm, game_knowledge=knowledge, throttle_check=_game_check_or_locked,
                             sensor=input_sensor,
                             frame_source="video" if video_path else "screen",
                             video_path=video_path,
                             video_sample_sec=video_sample_sec)
        audio = GameAudioMonitor(bus, cfg, monitor)  # 游戏音效识别：参考音模板比对（击杀/阵亡/胜负）
        realtime = RealtimeVision(bus, cfg, monitor=monitor)  # 分层视觉：轻量模型持续看屏（感知层）
        squad = SquadMonitor(bus, cfg, monitor, llm=llm, game_knowledge=knowledge)  # 队伍HUD：队友存活状态
        game_session = DeltaGameSession()  # 对局会话：本局记忆 + 结算聊天（情绪价值）
        squad.set_session(game_session)  # 队伍识别用已识别的游戏模式辅助判断"在打三角洲"
    elif _ocr_text_support_enabled(cfg):
        # OCR 仅文字模式：游戏识别全链路关闭，只保留"读屏幕真文字"喂给视觉
        # 模型（vision.ocr_text_hint）——治"没打死人却报击杀"的视觉脑补。
        # text_only 由 config perception.ocr.text_only 读取，绝不发布时刻判定。
        knowledge = GameKnowledge(cfg)
        ocr = GameOcrMonitor(bus, cfg, monitor, game_knowledge=knowledge, throttle_check=_game_check_or_locked)
    if knowledge is not None:
        analyzer.bind_game_knowledge(knowledge)  # OCR 真文字注入视觉模型（ocr_text_hint 开关）
    persona = Persona.from_config(cfg)
    memory_store = _make_memory_store(cfg)  # 长期记忆：用户纠正/偏好，跨会话保留并注入
    dialogue = Dialogue(bus, llm, state, persona, cfg, game_knowledge=knowledge,
                        app_knowledge=app_knowledge,
                        memory_store=memory_store, session=game_session)
    tts = TTS(bus, state, cfg)
    runtime.tts = tts  # 按需挂载完成后复查 TTS 兜底标志
    ui = ConsoleUI(bus, persona_name=persona.name, dialogue=dialogue, tts=tts)
    asr = ASR(bus, cfg) if not bool(cfg.ns.runtime.text_mode_only) else None

    if asr is not None:
        async def _pause_asr(e: Event, _a: ASR = asr) -> None:
            _a.pause()

        async def _resume_asr(e: Event, _a: ASR = asr) -> None:
            _a.resume()

        bus.subscribe(EventType.TTS_STARTED, _pause_asr)
        bus.subscribe(EventType.TTS_DONE, _resume_asr)

    if not video_path:
        # 视频测试模式是离线分析工具，不对外提供 WS 服务（也避免与正在运行的
        # 常驻伴侣抢 8765 端口导致启动失败）。
        await ws.start()
    _step(3, total, f"启动 WebSocket ({cfg.ns.websocket.host}:{cfg.ns.websocket.port})")

    _step(4, total, "初始化视觉模块",
          ok=status.vision_enabled, note="" if status.vision_enabled else "视觉禁用(纯文字)")
    # 4.1 游戏识别（OCR 文字识别，捕捉击杀/胜负）——总开关关闭时整段跳过；
    #     OCR 仅文字模式（text_only）时也显示并启动
    if game_recognition or ocr is not None:
        _step(4, total + 1, "初始化游戏OCR识别",
              ok=ocr.enabled, note="" if ocr.enabled else "OCR未启用")
        total += 1
    _step(5, total, "初始化语音模块",
          ok=status.voice_enabled, note="" if status.voice_enabled else "语音禁用(纯文字模式)")

    if asr is not None:
        asr.start()
        _step(6, total, "启动 Vosk 识别线程")
    else:
        _step(6, total, "跳过 Vosk 识别线程", ok=False, note="语音未启用")

    dialogue.subscribe()
    if not video_path:
        tts.subscribe()
        await tts.start_worker()
    else:
        logger.info("视频测试：跳过 TTS 播放（静默模式，仅生成报告）")

    async def _sync_tts_on_char_switch(e: Event) -> None:
        d = e.data or {}
        char_id = d.get("char_id", "")
        if char_id:
            tts.switch_character(char_id)
            # 显存联动：切非 VoxCPM 音色 → 终止 VoxCPM 服务释放 ~6GB；切回 → 自动拉起
            await asyncio.to_thread(_sync_voxcpm_on_switch, runtime, cfg, char_id)
            await asyncio.to_thread(_sync_qwen3tts_on_switch, runtime, cfg, char_id)
            await asyncio.to_thread(_sync_cosyvoice_on_switch, runtime, cfg, char_id)

    bus.subscribe(EventType.CHARACTER_SWITCHED, _sync_tts_on_char_switch)

    _step(7, total, "进入主循环（Ctrl+C 或输入 exit 退出）")
    console.print()
    ui.banner(status)
    _print_characters(cfg, dialogue.current_character_id)

    proactive = ProactiveStrategy(bus, state, cfg, llm=llm, persona=persona, monitor=monitor,
                                  game_knowledge=knowledge, app_knowledge=app_knowledge,
                                  memory_store=memory_store,
                                  session=game_session)
    proactive.subscribe()
    proactive_enabled = proactive.enabled
    # 视觉上下文感知（可选）：仅当游戏 AI 互动开启时注入（见上行注释）。
    if _proactive_game_ai_interaction(cfg):
        try:
            analyzer.bind_state_provider(proactive)
        except Exception:  # noqa: BLE001 绑定失败不阻断启动，回退纯视觉
            logger.warning("绑定视觉状态提供者失败，回退纯视觉", exc_info=True)
    _duplex_desc = "（规则引信模式：OCR确认关键时刻再开口）"
    if proactive_enabled:
        if proactive._duplex_mode == "realtime":
            _duplex_desc = "（云端真全双工：持续推音画，模型自主开口）"
        elif proactive._duplex_mode:
            _duplex_desc = f"（双工模式：模型自主看屏，每{proactive._duplex_interval_s:.0f}s一查）"
    console.print(f"[dim]主动搭话: {'✓ 启用' if proactive_enabled else '✗ 禁用'}[/]" + _duplex_desc)
    console.print(f"[dim]前台监听: {'✓ 启用' if monitor.enabled else '✗ 禁用(需Windows)'}[/]")
    if game_recognition:
        console.print(f"[dim]游戏OCR: {'✓ 启用' if ocr.enabled else '✗ 禁用'}[/]")
        console.print(f"[dim]队伍识别: {'✓ 启用' if squad.enabled else '✗ 禁用'}[/]")
        console.print(f"[dim]分层视觉: {'✓ 启用' if not realtime.disabled else '✗ 禁用'}[/]")
    elif ocr is not None:
        console.print("[dim]游戏OCR(仅文字): ✓ 扫屏幕真文字喂给视觉模型结合判断，不做时刻判定[/]")
    else:
        console.print("[dim]游戏识别: 已下线（等待重新设计方案）[/]")

    # 活动记忆 + 日报/周报（参照 pub-local-jarvis 本地记忆）
    activity_store = ActivityStore()
    activity_reporter = ActivityReporter(bus, cfg, llm, store=activity_store)
    activity_reporter.subscribe()
    activity_task = asyncio.create_task(activity_reporter.run(), name="activity_reporter")

    # 好感度系统（P0 接入）：同款旁挂——只订阅事件总线，不碰任何模型/引擎调用。
    try:
        # memory_store → 里程碑"发生即直写记忆"（与 dialogue 同一实例，绝不另开第二写者）
        relationship_reporter = RelationshipReporter(bus, cfg, llm=llm,
                                                     memory_store=memory_store)
        relationship_reporter.subscribe()
        relationship_reporter.start()
        runtime.relationship_reporter = relationship_reporter
        console.print("[dim]关系状态: ✓ 启用（落 data/，看日志 [关系] 行）[/]")
    except Exception:
        logger.exception("关系状态接入失败（不影响主流程）")
    # 网课自动记录（参照 pub-local-jarvis 课程记录）
    course_recorder = CourseRecorder(bus, cfg, llm=llm, vision=vision_router,
                                     store=activity_store)
    course_recorder.subscribe()

    # 社交通道（G 脚手架）：QQ 等外部 IM 接入同一个大脑（默认关闭，按 config.channels 启用）
    await _mount_channels(bus, dialogue, llm, cfg.ns, runtime)
    course_task = asyncio.create_task(course_recorder.run(), name="course_recorder")

    capture_task = asyncio.create_task(capture.capture_loop(), name="capture")
    # 视频测试模式：强制走 "快照 OCR 关键时刻 → 快照裁决(_duplex_check)" 链路。
    # 注意：_duplex_check 在 _duplex_mode is False 时直接 return（proactive.py
    # 5174），故视频测试必须保持 _duplex_mode 非 False，否则关键时刻反应路径被
    # 整体关闭 → 报告 0 配对（2026-08-27 实锤：上一跑因设 False 导致 0 配对）。
    # 设 True：_duplex_check 运行（B 路径处理 kill/died/victory…），而
    # _maybe_react_to_moment 在 _duplex_mode 为真时 return（A 路径跳过），
    # 与 22:31 有效版本（5 配对）行为一致。
    if video_path:
        proactive._duplex_mode = True
        proactive._video_test = True
        # 视频测试隔离：强制把游戏上下文钉死为录像对应游戏，禁止实时前台监听
        # （foreground/squad/realtime/audio）污染 game_knowledge。否则测试期间
        # 用户若在玩别的游戏/软件，current_name 与页面状态会被实时屏带偏，
        # 胜负门禁拿实时屏当判据误杀录像里的真时刻（2026-08-26 实锤：录像本
        # 是三角洲，但实时前台是崩铁，导致视频里三角洲胜负被"非游戏画面"拦截）。
        if knowledge is not None:
            knowledge.force_game(video_game)
    heartbeat_task: Optional[asyncio.Task] = None
    if not video_path:
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(ws, state, cfg.ns.websocket.heartbeat_interval_s), name="heartbeat")
    bus_task = asyncio.create_task(bus.run(), name="event_bus")
    proactive_task = (asyncio.create_task(
        proactive.run_loop(interval=30.0), name="proactive") if proactive_enabled else None)
    monitor_task = None
    if not video_path:
        monitor_task = asyncio.create_task(monitor.run(), name="foreground")
    if ocr is not None:
        ocr_task = asyncio.create_task(ocr.run(), name="game_ocr")
    # 视频测试报告器：仅 --video 模式启用，静默收集 OCR↔AI 配对，不播放 TTS
    reporter: Optional["VideoTestReporter"] = None
    if video_path:
        reporter = VideoTestReporter(bus, ocr, video_path)
        reporter.subscribe()
    # 视频测试模式下 game_recognition 可能仍为 True，但下方 audio/realtime/squad
    # 任务因 video_path 被跳过而不创建；finally 里会引用这三个变量，故先初始化
    # 为 None，避免退出时 UnboundLocalError（2026-08-27 实锤：上一跑退出报错）。
    audio_task = realtime_task = squad_task = None
    if game_recognition and not video_path:
        audio_task = asyncio.create_task(audio.run(), name="game_audio")
        realtime_task = asyncio.create_task(realtime.run(), name="realtime_vision")
        squad_task = asyncio.create_task(squad.run(), name="squad")
        wire_foreground(bus, monitor, capture)

    try:
        if video_path and reporter is not None:
            # 视频测试：等 OCR 跑完视频 → 生成对应报告 → 退出（不常驻）
            console.print(f"[dim]视频测试：后台跑 {video_path.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]} "
                          f"中，结束后生成 OCR↔AI 回应报告...[/]")
            await ocr_task
            reporter.finalize()
            logger.info("视频测试完成，报告已生成")
        else:
            explicit_exit = await ui.input_loop()
            if not explicit_exit:
                # EOF/后台启动/stdin重定向：继续常驻，不触发关闭（直到 Ctrl+C/SIGINT 外部 kill）
                logger.info("控制台输入结束（EOF/后台启动），服务继续常驻")
                stop_evt = asyncio.Event()
                try:
                    await stop_evt.wait()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass
    finally:
        logger.info("正在关闭...")
        capture.stop()
        monitor.stop()
        if ocr is not None:
            ocr.stop()
        if audio is not None:
            audio.stop()
        if realtime is not None:
            realtime.stop()
        if squad is not None:
            squad.stop()
        if asr is not None:
            asr.stop()
        course_recorder.stop()  # 时长达标会话自动出笔记，否则丢弃
        tts.stop()
        bus.stop()
        tasks_to_cancel = [t for t in (capture_task, heartbeat_task, bus_task, monitor_task,
                           activity_task, course_task) if t is not None]
        if ocr is not None:
            tasks_to_cancel.append(ocr_task)
        if game_recognition:
            tasks_to_cancel += [t for t in (audio_task, realtime_task, squad_task) if t is not None]
        if proactive_task:
            tasks_to_cancel.append(proactive_task)
        for t in tasks_to_cancel:
            t.cancel()
        if not video_path:
            await ws.stop()
        for t in tasks_to_cancel:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        console.print("[dim]已退出，下次见~[/]")


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
    main()
