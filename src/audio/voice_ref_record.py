# -*- coding: utf-8 -*-
"""录一小段**麦克风**音频存成 wav —— 音色克隆「参考音」的来源之一（另一个是上传文件）。

## 为什么单独一个模块，不复用 `webapp.record_audio_reference`

那个方法录的是**系统输出**（loopback，给游戏音效识别用），事件类型只认
`kill / headshot / died / victory / defeat`。设置页的「录音」按钮要的是**麦克风**，
设备方向正好相反。混成一个接口的后果已经发生过：前端传 `'reference'` 被后端白名单
拒掉，于是那个按钮**每一次都失败**，提示「录音失败：不支持的事件类型: reference」
（2026-10-21 查实，见 CHANGELOG）。

## 时长窗口

`[1.5, 30]` 秒，不是随手取的：`webapp.pick_reference_audio` 对上传的参考音校验
"1.5~60 秒、建议 3~30 秒清晰人声"。录出来的东西要能当同一份参考音用，所以下限对齐
1.5；上限取 30（"建议"的上界），再长只是让用户白等。
"""

from __future__ import annotations

import array
import os
import wave

MIN_SECONDS = 1.5
MAX_SECONDS = 30.0
DEFAULT_SECONDS = 2.5
_CHUNK = 1024


def clamp_seconds(seconds) -> float:
    """把用户/前端传来的时长收进可用窗口（坏值一律回默认）。"""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return DEFAULT_SECONDS
    if s != s or s <= 0:            # NaN / 0 / 负数
        return DEFAULT_SECONDS
    return max(MIN_SECONDS, min(MAX_SECONDS, s))


def _load_pa():
    """返回 (pyaudio 模块, 错误说明)。pyaudiowpatch 优先（WASAPI 设备名更准）。"""
    try:
        import pyaudiowpatch as pa  # noqa: PLC0415
        return pa, ""
    except Exception:  # noqa: BLE001  没装就退到 pyaudio
        pass
    try:
        import pyaudio as pa  # noqa: PLC0415
        return pa, ""
    except Exception as e:  # noqa: BLE001
        return None, "缺少音频库 pyaudiowpatch/pyaudio（%s）" % e


def _downmix_to_mono(pcm: bytes) -> bytes:
    """16-bit 立体声 → 单声道（左右取平均）。参考音不需要立体声。"""
    if not pcm:
        return b""
    src = array.array("h")
    src.frombytes(pcm[: len(pcm) - (len(pcm) % 4)])
    out = array.array("h", bytes(2 * (len(src) // 2)))
    for i in range(len(out)):
        out[i] = int((src[2 * i] + src[2 * i + 1]) / 2)
    return out.tobytes()


def _read(stream, chunk: int) -> bytes:
    """读一块 PCM。溢出时不抛（Windows 上长时间读必溢，抛了等于录不完）。"""
    try:
        return stream.read(chunk, exception_on_overflow=False)
    except TypeError:      # 某些后端不认这个关键字
        return stream.read(chunk)


def record_wav(path: str, seconds=DEFAULT_SECONDS, pa_mod=None) -> dict:
    """录 `seconds` 秒默认输入设备 → 单声道 16-bit wav（采样率跟设备走）。

    Args:
        path: 输出 wav 路径（目录不存在会自动建）。
        seconds: 时长，会被 `clamp_seconds` 收进 [1.5, 30]。
        pa_mod: 仅供测试注入假 PyAudio 模块。

    返回：`{success, path, seconds, samplerate, device}` 或 `{success: False, error}`。
    **不抛异常**：录音失败是常态（没设备 / 被占用 / 没权限），调用方要能如实显示原因。
    """
    sec = clamp_seconds(seconds)
    if pa_mod is not None:
        pa, load_err = pa_mod, ""
    else:
        pa, load_err = _load_pa()
    if pa is None:
        return {"success": False, "error": load_err or "音频库不可用"}

    inst = None
    stream = None
    try:
        inst = pa.PyAudio()
        info = inst.get_default_input_device_info()
        rate = int(info.get("defaultSampleRate") or 16000)
        channels = int(info.get("maxInputChannels") or 1)
        channels = 2 if channels >= 2 else 1
        stream = inst.open(format=pa.paInt16, channels=channels, rate=rate,
                           input=True, input_device_index=info.get("index"),
                           frames_per_buffer=_CHUNK)
        n = max(1, int(round(rate * sec / float(_CHUNK))))
        pcm = b"".join(_read(stream, _CHUNK) for _ in range(n))
        if channels == 2:
            pcm = _downmix_to_mono(pcm)
        dst_dir = os.path.dirname(os.path.abspath(path))
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm)
        return {"success": True, "path": path,
                "seconds": round(n * _CHUNK / float(rate), 2),
                "samplerate": rate,
                "device": str(info.get("name") or "")}
    except Exception as e:  # noqa: BLE001  没设备/被占用/没权限都在这里
        return {"success": False,
                "error": "录音失败：%s（可改用「选择音频文件」上传）" % e}
    finally:
        for closer in (
            lambda: (stream.stop_stream(), stream.close()),
            lambda: inst.terminate(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001  收尾失败不影响结论
                pass
