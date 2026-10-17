"""通话全链路追踪器（诊断专用，默认关闭；未开启时开销约等于一次函数调用）。

为什么单独成模块
----------------
"通话延迟高 + GPU 利用率只有一半 + 显存还比桌宠少 1GB"这组现象，嫌疑点横跨四层：

  ① 采集层：麦克风实际电平、增益后电平、是否被"自播闸门"清零
  ② 推流层：每帧真正送给模型的音频峰值（0 = 被静音填充/清零）、队列积压、force_listen
  ③ 裁决层：模型每帧的 is_listen / 出字 —— "她在等 vs 她在算"只能靠这层区分
  ④ 出声层：客户端发射 → 上层一长串门控 → TTS 合成 → 首个音频块入设备

只有把它们写进同一个毫秒级时间线，才能把「你说完 → 她开口」的 3~7s 拆成
"决策等待 / 生成耗时 / 被门控吞掉 / 合成耗时"四段，而不是靠猜。

用法
----
    from .call_trace import configure, start, stop, trace, is_active
    configure(True)                    # 上层按 config.duplex_call_trace 调用
    start(tag="omni")                  # 通话开始（切到 mic 采集）时
    trace("model", listen=True, ...)   # 任意位置埋点（未 start 时直接返回）
    stop()                             # 通话结束

记录格式：JSONL，一行一个事件。固定字段
    ts  纪元秒（3 位小数，唯一权威排序依据）
    t   HH:MM:SS.mmm（人读用）
    ev  事件名
    sv  距上次"用户有声（严格档 >0.05）"的秒数（= 0 表示本行就是那一刻）
    sl  距上次"用户可能在说话（宽松档 >0.01）"的秒数  ← 弱麦场景看这个
    se  距上次"客户端发射文本 emit"的秒数
其余为该事件自带字段。**sv/sl 是全表最有用的列**：它们让每一行自带
"此刻离用户说完多久"，无需再做时间对齐。若麦克风全程到不了 0.05
（本项目 22:04 那通就是），sv 会一直为空，此时靠 sl 定位"用户说完"。

事件名一览（分析脚本按此取值）
    ctl     控制/生命周期（采集启动、force_listen 变动、prepare 旋钮快照）
    mic     采集块（2Hz）：增益后峰值/有效值、是否被闸门清零、是否喂了 ASR
    asr     用户侧 Sherpa 出文本（原始 + 清理后）
    frame   推给模型的音频帧（1Hz）：帧内峰值、是否静音、队列积压、force_listen
    model   模型每帧裁决（1Hz）：is_listen / 该帧文本 / 本回合已攒字数
    turn_end 回合结束时的处置（播出 / 太短丢弃 / 无文本丢弃）
    emit    客户端发射文本（进上层门控）
    speak_in  上层 _on_duplex_speak 入口
    speak_frag 被第二层攒句缓冲扣住（没出声）
    speak_drop 被某道闸门拦掉（附 reason）
    speak_out  通过所有闸门，真正交给 TTS
    tts_begin / tts_first_audio / tts_play  TTS 合成与首音频
    barge   打断触发
    gpu     1Hz 采样：GPU 利用率 / 显存 / 功耗 / 温度（每 5 次带一次分进程占用）

注意：本模块**不导入任何项目内模块**，避免与 llm/companion/voice 形成环。
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import threading
import time
from typing import Any, Optional

_LOCK = threading.Lock()
_FH: Optional[Any] = None
_PATH: Optional[str] = None
_ENABLED: bool = False          # 总开关（由 configure 设置）
_ACTIVE: bool = False           # 文件已打开（start 之后）
_BUF: list = []
_BUF_MAX = 24                   # 攒够这么多行就落盘（避免采集回调线程频繁写盘）
_LAST_FLUSH: float = 0.0
_LAST_VOICE_TS: float = 0.0     # 最近一次"用户有声"（严格档 >0.05）的纪元秒
_LAST_VLO_TS: float = 0.0       # 最近一次"用户可能在说话"（宽松档 >0.01）的纪元秒
_LAST_EMIT_TS: float = 0.0      # 最近一次"客户端发射文本"
_T0: float = 0.0
_GPU_STOP: Optional[threading.Event] = None
_GPU_THREAD: Optional[threading.Thread] = None
_COUNTS: dict = {}


# ============================== 对外开关 ==============================

def configure(enabled: bool) -> None:
    """设置总开关。必须在 start() 之前调用；运行中改只影响下一次 start()。"""
    global _ENABLED
    _ENABLED = bool(enabled)


def is_active() -> bool:
    """追踪是否正在写盘（埋点方可用它跳过昂贵的字段构造）。"""
    return _ACTIVE


def start(tag: str = "") -> Optional[str]:
    """打开 data/call_trace_<stamp>.jsonl 并启动 GPU 采样线程。返回文件路径。"""
    global _FH, _PATH, _ACTIVE, _BUF, _LAST_FLUSH, _T0, _LAST_VOICE_TS, _LAST_VLO_TS, _LAST_EMIT_TS
    if not _ENABLED:
        return None
    with _LOCK:
        if _ACTIVE:
            return _PATH
        try:
            here = os.path.dirname(os.path.abspath(__file__))          # .../11/src/llm
            root = os.path.dirname(os.path.dirname(here))              # .../11
            data_dir = os.path.join(root, "data")
            os.makedirs(data_dir, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{tag}" if tag else ""
            _PATH = os.path.join(data_dir, f"call_trace{suffix}_{stamp}.jsonl")
            _FH = open(_PATH, "a", encoding="utf-8")
            _ACTIVE = True
            _BUF = []
            _LAST_FLUSH = time.time()
            _T0 = time.time()
            _LAST_VOICE_TS = 0.0
            _LAST_VLO_TS = 0.0
            _LAST_EMIT_TS = 0.0
            _COUNTS.clear()
        except Exception:
            _FH = None
            _PATH = None
            _ACTIVE = False
            return None
    trace("ctl", action="trace_start", path=os.path.basename(_PATH or ""))
    with _LOCK:
        _flush_locked()
    _start_gpu_sampler()
    return _PATH


def stop() -> None:
    """收尾：停 GPU 采样、写一条汇总、关文件。"""
    global _FH, _ACTIVE, _GPU_THREAD, _GPU_STOP
    if not _ACTIVE:
        return
    ev = _GPU_STOP
    if ev is not None:
        ev.set()
    th = _GPU_THREAD
    if th is not None and th.is_alive():
        th.join(timeout=2.0)
    _GPU_THREAD = None
    _GPU_STOP = None
    try:
        trace("ctl", action="trace_stop",
              dur_s=round(time.time() - _T0, 1), counts=dict(_COUNTS))
    except Exception:
        pass
    with _LOCK:
        _flush_locked()
        fh, _FH = _FH, None
        _ACTIVE = False
    if fh is not None:
        try:
            fh.close()
        except Exception:
            pass


# ============================== 埋点入口 ==============================

def trace(event: str, **fields: Any) -> None:
    """写一条事件。未开启时立即返回（零文件/零字符串拼接开销）。"""
    global _LAST_VOICE_TS, _LAST_VLO_TS, _LAST_EMIT_TS
    if not _ACTIVE:
        return
    now = time.time()
    # 自动维护"时间锚"：每条事件自带"离用户上一次出声有多久"。
    # 两个档位是为弱麦场景准备的——如果麦克风全程到不了 0.05（人声档），
    # 只用严格档会导致"用户说完"这个参考点永远取不到，延迟就没法量。
    if event == "mic":
        if fields.get("vlo"):
            _LAST_VLO_TS = now
        if fields.get("voice"):
            _LAST_VOICE_TS = now
    elif event == "emit":
        _LAST_EMIT_TS = now
    rec = {"ts": round(now, 3),
           "t": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
           "ev": event}
    if _LAST_VOICE_TS:
        rec["sv"] = round(now - _LAST_VOICE_TS, 3)
    if _LAST_VLO_TS:
        rec["sl"] = round(now - _LAST_VLO_TS, 3)
    if _LAST_EMIT_TS:
        rec["se"] = round(now - _LAST_EMIT_TS, 3)
    for k, v in fields.items():
        if v is None:
            continue
        rec[k] = _safe(v)
    _COUNTS[event] = _COUNTS.get(event, 0) + 1
    _write(rec)


def voice_mark() -> None:
    """显式标记"用户此刻有声"（供不便发 mic 事件的场景）。"""
    global _LAST_VOICE_TS
    if _ACTIVE:
        _LAST_VOICE_TS = time.time()


# ============================== 内部实现 ==============================

def _safe(v: Any) -> Any:
    """把字段裁到可 JSON 化、且不会撑爆文件的大小。"""
    try:
        if isinstance(v, str):
            return v if len(v) <= 200 else v[:200] + "…"
        if isinstance(v, float):
            return round(v, 5)
        if isinstance(v, (int, bool)) or v is None:
            return v
        if isinstance(v, (list, tuple)):
            return [_safe(x) for x in list(v)[:8]]
        if isinstance(v, dict):
            return {str(k): _safe(x) for k, x in list(v.items())[:12]}
        return str(v)[:120]
    except Exception:
        return "<unserializable>"


def _write(rec: dict) -> None:
    global _BUF
    try:
        line = json.dumps(rec, ensure_ascii=False)
    except Exception:
        return
    with _LOCK:
        if not _ACTIVE:
            return
        _BUF.append(line)
        # 攒够就落盘；否则等 GPU 心跳（1Hz）统一 flush —— 数据丢失上限约 1s
        if len(_BUF) >= _BUF_MAX:
            _flush_locked()


def _flush_locked() -> None:
    global _BUF, _LAST_FLUSH
    if _FH is None or not _BUF:
        _LAST_FLUSH = time.time()
        return
    try:
        _FH.write("\n".join(_BUF) + "\n")
        _FH.flush()
    except Exception:
        pass
    _BUF = []
    _LAST_FLUSH = time.time()


def _start_gpu_sampler() -> None:
    global _GPU_STOP, _GPU_THREAD
    _GPU_STOP = threading.Event()
    th = threading.Thread(target=_gpu_loop, args=(_GPU_STOP,),
                          name="call-trace-gpu", daemon=True)
    _GPU_THREAD = th
    th.start()


def _gpu_loop(stop_ev: threading.Event) -> None:
    """每秒采一次 GPU。跑在独立线程，不碰主事件循环、不碰采集回调。"""
    n = 0
    while not stop_ev.wait(1.0):
        n += 1
        fields: dict = {}
        try:
            out = _run_smi([
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.free,power.draw,temperature.gpu",
                "--format=csv,noheader,nounits",
            ])
            if out:
                parts = [p.strip() for p in out.strip().split("\n")[0].split(",")]
                if len(parts) >= 5:
                    fields.update({"util": _num(parts[0]), "mem_used": _num(parts[1]),
                                   "mem_free": _num(parts[2]), "power": _num(parts[3]),
                                   "temp": _num(parts[4])})
        except Exception:
            pass
        # 每 5 秒带一次分进程显存（llama-server 就是 Comni）
        if n % 5 == 0:
            try:
                out2 = _run_smi([
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_memory",
                    "--format=csv,noheader,nounits",
                ])
                if out2:
                    fields["procs"] = [p.strip().replace(", ", ":")
                                       for p in out2.strip().split("\n") if p.strip()][:6]
            except Exception:
                pass
        trace("gpu", **fields)
        with _LOCK:
            _flush_locked()


def _run_smi(cmd: list) -> str:
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                       timeout=4.0, creationflags=flags)
    return (r.stdout or "").strip()


def _num(s: str):
    try:
        f = float(s)
        return int(f) if f == int(f) else round(f, 1)
    except Exception:
        return s
