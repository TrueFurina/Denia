"""通话录音合并为连贯音频（2026-10-04）。

【为什么需要】此前导出的 `音频/` 目录里是**每句一个 wav**（17 句 = 17 个文件），
用户要自己按序号拼、还得自己掐停顿，很麻烦。本模块把整通对话合成**一个连续
wav**，听起来就是一通真实的电话录音。

【为什么不能直接拼字节】实测 `chat_audio/` 里**采样率不统一**：
    达妮娅 24000Hz / 用户 16000Hz（1ch 16bit）
逐个 `readframes()` 硬拼会得到**速度错乱**的音频（16k 的内容按 24k 播放会变慢/变调）。
所以必须先统一采样率再拼。

【依赖】只用项目里**已经装好**的 numpy + wave + subprocess(ffmpeg)：
    · 主路径：numpy 线性插值重采样（纯 stdlib+numpy，任何机器都能跑）
    · 兜底：ffmpeg（若装了，用于产出 mp3；本仓发行版自带 ffmpeg）
不新增任何第三方依赖。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import wave
from typing import Optional

import numpy as np

# 目标采样率：取两者中较高者，避免丢失达妮娅语音的高频细节
TARGET_SR = 24000


def _read_wav(path: str):
    """读一个 wav → (float32 数组[-1,1], 采样率)。失败返回 (None, 0)。"""
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            nch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            # 只处理 16bit（实测全部是 16bit）；其它位宽按 8bit/32bit 粗略转
            if sw == 1:
                data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
                data = (data - 128.0) / 128.0
            elif sw == 4:
                data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                return None, 0
        else:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if nch > 1:  # 立体声 → 单声道（简单取均值，够用）
            data = data.reshape(-1, nch).mean(axis=1)
        return data, sr
    except Exception:
        return None, 0


def _resample(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性插值重采样。语音场景足够，且不引入新依赖。"""
    if src_sr == dst_sr or not len(data):
        return data
    n_out = int(round(len(data) * float(dst_sr) / float(src_sr)))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.arange(len(data), dtype=np.float64)
    x_new = np.linspace(0.0, len(data) - 1, num=n_out, dtype=np.float64)
    return np.interp(x_new, x_old, data).astype(np.float32)


def _silence(sec: float, sr: int) -> np.ndarray:
    n = int(max(0.0, sec) * sr)
    return np.zeros(n, dtype=np.float32)


def _trim_silence(data: np.ndarray, sr: int,
                  thresh: float = 0.006, keep: float = 0.06) -> np.ndarray:
    """裁掉每句**首尾**的多余静音，但保留一小段自然起止（更像真人说话）。

    语音合成常带几百毫秒首尾静音；逐句保留会让整段听起来"一顿一顿"。
    这里只削掉明显多余的部分，并各留 `keep` 秒余量。
    """
    if not len(data):
        return data
    env = np.abs(data)
    win = max(1, int(0.01 * sr))
    k = len(env) // win
    if k > 0:
        e = env[:k * win].reshape(-1, win).max(axis=1)
        voiced = np.where(e > thresh)[0]
        if len(voiced) == 0:
            return data
        s = max(0, (voiced[0] * win) - int(keep * sr))
        t = min(len(data), (voiced[-1] + 1) * win + int(keep * sr))
        return data[s:t]
    nz = np.where(env > thresh)[0]
    if not len(nz):
        return data
    s = max(0, nz[0] - int(keep * sr))
    t = min(len(data), nz[-1] + int(keep * sr))
    return data[s:t]


def _gap_for(text: str, prev_text: str) -> float:
    """按**语义**决定句间停顿，而不是固定值 —— 这样才像真人对话的呼吸节奏。

    - 问句结尾 → 稍长停顿（留出让对方接话的空间）
    - 逗号/分号结尾 → 短停顿
    - 句号 → 中等
    - 省略号/破折号 → 略长（欲言又止的感觉）
    """
    t = (text or "").strip()
    p = (prev_text or "").strip()
    if not t:
        return 0.30
    gap = 0.26
    if t.endswith(("？", "?")):
        gap = 0.52
    elif t.endswith(("！", "!")):
        gap = 0.46
    elif t.endswith(("…", "…", "——", "--")):
        gap = 0.58
    elif t.endswith(("，", ",", "、", "；", ";", "：", ":")):
        gap = 0.18
    elif t.endswith(("。", ".", "！", "?", "；")):
        gap = 0.34
    # 上一句没说完（以逗号/省略号收尾）→ 接得紧一点
    if p.endswith(("，", ",", "、", "…")):
        gap *= 0.55
    return max(0.10, min(gap, 0.9))


def merge_call_audio(items: list[dict], out_path: str,
                     lead_gap: float = 0.45, tail_gap: float = 0.7,
                     target_sr: int = TARGET_SR,
                     progress_cb=None) -> dict:
    """把若干 wav 合并成一段连续音频。

    items: [{"path": str, "text": str}, ...]（按时间序）
    返回 {"ok": bool, "path": str, "duration": float, "clips": int,
          "missing": [缺失文件], "error": str}
    """
    segments: list[np.ndarray] = []
    missing: list[str] = []
    used = 0
    sr_out = target_sr
    prev_text = ""
    seg_texts: list[str] = []

    for i, it in enumerate(items or []):
        p = it.get("path") or ""
        txt = it.get("text") or ""
        if progress_cb:
            try:
                progress_cb(i, len(items or []))
            except Exception:
                # 进度回调自己出错不该中断合并（它只是汇报）
                pass
        data, sr = _read_wav(p) if p else (None, 0)
        if data is None or not len(data):
            if p:
                missing.append(os.path.basename(p))
            prev_text = txt
            continue
        data = _trim_silence(_resample(data, sr, sr_out), sr_out)
        if used == 0:
            segments.append(_silence(lead_gap, sr_out))
        else:
            segments.append(_silence(_gap_for(prev_text, prev_text), sr_out))
        segments.append(data)
        segments.append(_silence(0.06, sr_out))  # 句内极短缓冲，避免切口爆音
        seg_texts.append(txt)
        prev_text = txt
        used += 1
    if used:
        segments.append(_silence(tail_gap, sr_out))

    if not segments:
        return {"ok": False, "path": "", "duration": 0.0, "clips": 0,
                "missing": missing, "error": "没有可用音频片段"}

    merged = np.concatenate(segments)

    # 归一化到峰值 0.92（留一点余量，防削波爆音）
    peak = float(np.max(np.abs(merged))) if len(merged) else 0.0
    if peak > 0.001:
        merged = merged * (0.92 / peak)

    # 极短淡入淡出，消除首尾爆音
    fade = min(int(0.01 * sr_out), len(merged) // 2)
    if fade > 1:
        merged[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        merged[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

    pcm = (np.clip(merged, -1.0, 1.0) * 32767.0).astype(np.int16)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr_out)
            w.writeframes(pcm.tobytes())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": "", "duration": 0.0, "clips": used,
                "missing": missing, "error": f"写文件失败：{e}"}

    return {"ok": True, "path": out_path,
            "duration": round(len(pcm) / float(sr_out), 1),
            "clips": used, "missing": missing, "error": ""}


def wav_to_mp3(wav_path: str, mp3_path: str, bitrate: str = "128k") -> bool:
    """可选：另存一份 mp3（体积小、好分享）。ffmpeg 不可用时返回 False。

    注意：**失败绝不抛异常**，也不影响 wav 产出（用户至少还有 wav）。
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return False
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-i", wav_path, "-codec:a", "libmp3lame", "-b:a", bitrate, mp3_path],
            capture_output=True, timeout=180)
        return r.returncode == 0 and os.path.exists(mp3_path) \
            and os.path.getsize(mp3_path) > 0
    except Exception:
        return False


def _find_ffmpeg() -> str:
    """找 ffmpeg：先看 PATH，再看常见安装位置（含本仓发行版自带）。"""
    from shutil import which
    p = which("ffmpeg")
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        for cand in (os.path.join(base, "ffmpeg.exe"),
                     os.path.join(base, "ffmpeg", "bin", "ffmpeg.exe"),
                     os.path.join(base, "Comni", "ffmpeg", "bin", "ffmpeg.exe")):
            if os.path.exists(cand):
                return cand
    return ""


def get_duration(path: str) -> float:
    """读 wav 时长（秒），失败返回 0。"""
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 1)
    except Exception:
        return 0
