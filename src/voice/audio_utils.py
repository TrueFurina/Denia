# -*- coding: utf-8 -*-
"""音频转换工具：MiniMax 返回的 mp3 是 LAME VBR 编码，
pygame 的 drmp3 解码器对其支持不稳定（曾报 "corrupt mp3 file"，播放中可能有杂音）。
播放前用完整版 ffmpeg 转成标准 PCM WAV，让 pygame 稳定播放。"""

import os
import shutil
import subprocess

# WinGet 安装的完整版 ffmpeg（Trae 自带的精简版不支持 mp3 解码，不能用于 mp3→wav）
_WINGET_FFMPEG = (
    r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
)


def find_ffmpeg() -> str | None:
    """优先完整版 ffmpeg，其次 PATH（精简版可能不支持 mp3）。"""
    if os.path.isfile(_WINGET_FFMPEG):
        return _WINGET_FFMPEG
    return shutil.which("ffmpeg")


def convert_mp3_to_wav(src: str, dst: str) -> bool:
    """把 mp3 转成 24000Hz 16-bit 单声道 PCM WAV。失败返回 False（调用方回退原样播放）。"""
    ff = find_ffmpeg()
    if not ff or not os.path.isfile(src):
        return False
    try:
        r = subprocess.run(
            [ff, "-y", "-i", src, "-ac", "1", "-ar", "24000", "-acodec", "pcm_s16le", dst],
            capture_output=True,
            timeout=30,
        )
        return r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 44
    except Exception:
        return False
