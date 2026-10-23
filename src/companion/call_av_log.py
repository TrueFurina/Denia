"""通话音频/视频事件追踪日志（call:av）。

记录一次通话生命周期内与音视频相关的关键事件，便于事后追踪「达妮娅到底在看什么/
听什么/出不出声」：

  通话起止：  call_start / call_end
  音频控制：  mic_toggle（麦克风禁音切换）、ai_audio_toggle（免提/AI出声切换）
  视频控制：  camera_toggle（摄像头开/关）、screen_share_toggle（屏幕共享开/关）
  设备切换：  device_change（麦克风/摄像头设备、推理引擎切换）
  真相态：    call_profile_applied（实际生效的视觉源/档位）、call_restored（挂断恢复）
  前端媒体流：screen_stream（getDisplayMedia 授权/被拒）、camera_stream（getUserMedia）
              track_ended（浏览器原生停止共享）、call_intent / call_end_intent

落盘（项目 data/ 目录，与 activity 日志同级）：
  data/call_av_log.jsonl  结构化事件（一行一个 JSON，便于追踪/后处理）
  data/call_av.log         人类可读文本（call_av logger 自带 FileHandler，保证持久化，
                           即便主程序根 logger 没配 handler 也能落盘）

调用方无需关心路径与线程安全：
  from .call_av_log import log_call_av
  log_call_av("mic_toggle", muted=True, source="ui")

写失败静默忽略，绝不拖垮通话主路径。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

_logger = logging.getLogger("call_av")
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _data_dir = os.path.join(_root, "data")
        os.makedirs(_data_dir, exist_ok=True)
        _fh = logging.FileHandler(os.path.join(_data_dir, "call_av.log"), encoding="utf-8")
        _fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(_fh)
    except Exception:
        # 兜底：handler 建不起来就交回根 logger，至少不丢日志语义
        _logger.propagate = True

_LOCK = threading.Lock()
_JSONL_PATH: str | None = None


def _jsonl_path() -> str:
    global _JSONL_PATH
    if _JSONL_PATH is None:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _JSONL_PATH = os.path.join(_root, "data", "call_av_log.jsonl")
    return _JSONL_PATH


def log_call_av(event: str, **fields) -> None:
    """记录一条通话音视频事件。

    event: 简短事件名（见模块 docstring）。
    fields: 任意附加字段（dict/list 会被 JSON 序列化后存为字符串，其余原样落盘）。
    """
    rec: dict = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "epoch": round(time.time(), 3),
        "event": event,
    }
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = str(v)
        rec[k] = v
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))

    # 结构化 JSONL（原子追加：直接 a 模式 + flush，低频率事件足够安全）
    with _LOCK:
        try:
            p = _jsonl_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                # 【2026-09-18 日志卡死修复】去掉逐条 flush：
                # 原实现每条事件都同步刷盘（高频 IO）；改为依赖 OS 缓冲 +
                # with 块退出时关闭，IO 次数下降一个量级。
                f.write(line + "\n")
        except Exception:
            # 通话音视频日志写不出去（磁盘满/句柄坏）→ 丢这条，不拦通话
            pass

    # 人类可读文本日志
    flat = " ".join(f"{k}={rec[k]}" for k in rec if k not in ("ts", "epoch", "event"))
    _logger.info("[call:av] %s %s", event, flat)
