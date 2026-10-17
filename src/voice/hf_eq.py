# -*- coding: utf-8 -*-
"""输出侧高频补偿（零相位 EQ）—— 按角色预存曲线，运行时只做纯 DSP。

背景（已实测）
--------------
引擎 prompt 条件的 mel `fmax=8000` ⇒ 产出 8–12kHz 只有模型先验；实测产出的
**谱心 464 比模型原生音 719 还低**（明显发闷），LTAS 距真源 6.53dB。
本模块在**输出侧**用一条**按角色预存**的固定曲线做零相位谱补偿，把 0–8kHz 的
亮度/谱形拉回该角色真源的形状（实测可消掉约 79% 的谱形差）。

三条设计红线（踩过坑，见 memory §2bj）
-------------------------------------
1. **曲线必须预存，绝不逐片现算** —— `build_gain(ref,out)` 逐片算出的曲线各不
   相同 ⇒ 音色会抖。本模块只做「查表 + 插值 + 乘增益」。
2. **固定增益，不做逐块峰值归一化** —— 参考实现 `tools/hf_restore.py` 的
   `apply_gain` 末尾有 `if pk>0.995: y=y/pk*0.99`；整段播时无害，但一旦做流式
   分片，每片各自归一 ⇒ **音量一抽一抽**。本模块改为**固定 trim**。
3. **全程 fail-safe** —— 任何异常 / 曲线缺失 / 未开启 ⇒ **原样返回**，
   绝不吞掉语音、绝不返回空。

为什么零相位：`S2 = S * G`，G 为实数非负 ⇒ 只改幅度谱、不动相位 ⇒
不引入瞬态/预回响失真，且不改变后续任何环节的语义。

曲线来源：`tools/voice_eq_calib.py` 自动标定 → `assets/voice_eq/<key>.json`。
**没有该角色的曲线就不补偿**（跨角色搬曲线 = 朝无证据目标推 10dB = 灌嘶声）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

SR = 24000          # 双工原生语音采样率（固定）
N = 1024            # STFT 窗长
H = 256             # STFT 跳距
FMAX_PASS = 11500   # 该频率以上不抬（双方都只是噪声底，抬了只有嘶声）
_W = np.hanning(N).astype(np.float32)

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CURVE_DIR = os.path.join(_PROJ, "assets", "voice_eq")

# key -> (freqs, gains_db, trim_db) | None（None = 明确知道没有，避免反复读盘）
_CACHE: Dict[str, Optional[Tuple[np.ndarray, np.ndarray, float]]] = {}
_STATS = {"applied": 0, "passthrough": 0, "failed": 0, "last_key": ""}


# --------------------------------------------------------------------------- #
# STFT / iSTFT（向量化：单次批量 rfft，避免逐帧 Python 循环）
# --------------------------------------------------------------------------- #
def _stft(x: np.ndarray) -> Optional[Tuple[np.ndarray, int, int]]:
    pad = N // 2
    xp = np.concatenate([np.zeros(pad, np.float32), x.astype(np.float32),
                         np.zeros(pad + N, np.float32)])
    nf = 1 + (len(xp) - N) // H
    if nf < 2:
        return None
    idx = H * np.arange(nf, dtype=np.int64)[:, None] + np.arange(N, dtype=np.int64)[None, :]
    frames = xp[idx] * _W
    return np.fft.rfft(frames, axis=1), pad, len(x)


def _istft(S: np.ndarray, pad: int, length: int) -> np.ndarray:
    frames = np.fft.irfft(S, N, axis=1).astype(np.float32) * _W
    nf = frames.shape[0]
    xp = np.zeros((nf - 1) * H + N, np.float32)
    ws = np.zeros_like(xp)
    for i in range(nf):
        xp[i * H:i * H + N] += frames[i]
        ws[i * H:i * H + N] += _W * _W
    x = xp / np.maximum(ws, 1e-8)
    return x[pad:pad + length]


# --------------------------------------------------------------------------- #
# 曲线存取
# --------------------------------------------------------------------------- #
def curve_path(key: str) -> str:
    return os.path.join(CURVE_DIR, f"{key}.json")


def _load_curve(key: str) -> Optional[Tuple[np.ndarray, np.ndarray, float]]:
    if not key:
        return None
    if key in _CACHE:
        return _CACHE[key]
    val: Optional[Tuple[np.ndarray, np.ndarray, float]] = None
    p = curve_path(key)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            freqs = np.asarray(d["freqs"], dtype=np.float64)
            gains = np.asarray(d["gains_db"], dtype=np.float64)
            if freqs.size >= 4 and freqs.size == gains.size:
                val = (freqs, gains, float(d.get("trim_db", 0.0)))
        except Exception:  # noqa: BLE001
            logger.warning("高频补偿曲线读取失败，将跳过补偿: %s", p, exc_info=True)
            val = None
    _CACHE[key] = val
    return val


def reload(key: Optional[str] = None) -> None:
    """标定出新曲线后调用以刷新缓存；key=None 清空全部。"""
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)


def curve_status(key: str) -> Dict[str, Any]:
    """供诊断/标定工具查询（不触发 numpy 运算）。"""
    c = _load_curve(key)
    if c is None:
        return {"key": key, "available": False, "path": curve_path(key)}
    freqs, gains, trim = c
    i8 = int(np.searchsorted(freqs, 8000)) if freqs.size else 0
    return {
        "key": key, "available": True, "path": curve_path(key),
        "bins": int(freqs.size), "trim_db": round(trim, 2),
        "gain_at_4k": round(float(np.interp(4000, freqs, gains)), 2),
        "gain_at_8k": round(float(np.interp(8000, freqs, gains)), 2),
        "gain_at_11k": round(float(np.interp(11000, freqs, gains)), 2),
        "mean_gain_below_8k": round(float(gains[:i8].mean()), 2) if i8 else None,
    }


def stats() -> Dict[str, Any]:
    return dict(_STATS)


# --------------------------------------------------------------------------- #
# 核心：把固定曲线乘到幅度谱上
# --------------------------------------------------------------------------- #
def apply_f32(x: np.ndarray,
              freqs: np.ndarray,
              gains_db: np.ndarray,
              trim_db: float = 0.0) -> np.ndarray:
    """零相位 EQ。输入/输出均为 float32 一维数组（同一采样率 SR）。"""
    st = _stft(x)
    if st is None:
        return x
    S, pad, length = st
    f = np.fft.rfftfreq(N, 1.0 / SR)
    g = np.interp(f, freqs, gains_db, left=0.0, right=0.0)
    # 11.5kHz 以上只允许下压：抬起来补的是噪声底
    hi = int(np.searchsorted(f, FMAX_PASS))
    if hi < g.size:
        g[hi:] = np.minimum(g[hi:], 0.0)
    g = np.clip(g + float(trim_db), -24.0, 24.0)
    G = (10.0 ** (g / 20.0)).astype(np.float32)
    y = _istft(S * G[None, :], pad, length)
    # 固定 trim 之外不做任何逐块归一化（见模块 docstring 红线 2）
    return y.astype(np.float32)


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def apply_pcm_f32_safe(pcm: bytes,
                       key: str,
                       trim_db: Optional[float] = None) -> Tuple[bytes, str]:
    """对 float32 PCM 字节做补偿。

    返回 (pcm, status)：status ∈ {'applied','no_curve','no_key','too_short','failed'}。
    **任何非 applied 情况都返回原始 pcm**（fail-safe）。
    """
    if not pcm or not key:
        _STATS["passthrough"] += 1
        return pcm, "no_key"
    c = _load_curve(key)
    if c is None:
        _STATS["passthrough"] += 1
        return pcm, "no_curve"
    try:
        x = np.frombuffer(pcm, dtype=np.float32)
        if x.size < N * 2:
            _STATS["passthrough"] += 1
            return pcm, "too_short"
        freqs, gains, trim = c
        y = apply_f32(x, freqs, gains, trim if trim_db is None else float(trim_db))
        if y.size != x.size:
            _STATS["failed"] += 1
            logger.warning("高频补偿后长度变化(%d→%d)，已放弃补偿", x.size, y.size)
            return pcm, "failed"
        _STATS["applied"] += 1
        _STATS["last_key"] = key
        return y.tobytes(), "applied"
    except Exception:  # noqa: BLE001
        _STATS["failed"] += 1
        logger.warning("高频补偿失败（原样播报，不影响双工）: key=%s", key, exc_info=True)
        return pcm, "failed"


__all__ = ["apply_f32", "apply_pcm_f32_safe", "curve_status", "curve_path",
           "reload", "stats", "CURVE_DIR", "SR", "N", "H"]
