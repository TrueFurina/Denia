# -*- coding: utf-8 -*-
"""参考音频工具：ffmpeg 转码校验（Qwen3-TTS / CosyVoice2 服务端共用）。

soundfile 对 mp3 等格式支持不稳，损坏文件会卡死或合成出乱叫音节。
统一用 ffmpeg 把参考音频转成 16kHz 单声道 wav：
  - 有效 wav/mp3/m4a → 自动转码
  - 无效/损坏文件 → 快速返回明确错误
系统 PATH 里的 ffmpeg 可能是坏版本（如某些 IDE 自带的阉割版，能跑 -version
但解码失败），按候选列表逐个尝试，第一个转码成功的即用。
"""
import os
import shutil
import subprocess
import tempfile


def _ffmpeg_candidates() -> list[str]:
    cands: list[str] = []
    env_ff = os.environ.get("FFMPEG_PATH")
    if env_ff:
        cands.append(env_ff)
    # 常见可靠安装位置（winget / choco / ffmpeg.org 默认）
    home = os.path.expanduser("~")
    for p in (
        os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ):
        if os.path.exists(p):
            cands.append(p)
    which = shutil.which("ffmpeg")
    if which:
        cands.append(which)
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def _maybe_gain(tmp_path: str) -> str:
    """转码后的 wav 做音量检查：真静音报错，整体小声自动增益归一化。

    Qwen3-TTS 对近静音/低音量参考音频提取不到说话人信息会合成卡死。
    但"音量低"≠"没声音"（如游戏提取语音整体偏小声），先归一化到正常
    音量（RMS≈3000）再返回，只有静音占比过高（真没声）才拒绝。
    返回最终 wav 路径（可能为增益后的新文件，也可能就是原 tmp_path）。
    """
    import wave

    import numpy as np

    try:
        with wave.open(tmp_path, "rb") as w:
            frames = w.readframes(w.getnframes())
    except Exception:  # noqa: BLE001
        return tmp_path
    arr = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if arr.size == 0:
        raise RuntimeError("参考音频解码后为空")
    quiet_ratio = float(np.mean(np.abs(arr) < 500))
    if quiet_ratio > 0.8:
        raise RuntimeError(
            f"参考音频几乎全是静音（静音占比 {quiet_ratio * 100:.0f}%），"
            "无法用于音色克隆。请换一段清晰人声的音频（3~10 秒、正常音量）")
    rms = float(np.sqrt(np.mean(arr ** 2)))
    if rms >= 1500:
        return tmp_path
    # 整体小声：用 ffmpeg volume 放大到正常音量（RMS≈3000），避免模型卡死
    gain = 3000.0 / max(rms, 1.0)
    out2 = tmp_path + ".gain.wav"
    last_err = ""
    for ff in _ffmpeg_candidates():
        p = subprocess.run(
            [ff, "-y", "-v", "error", "-i", tmp_path, "-af", f"volume={gain:.3f}",
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out2],
            capture_output=True, timeout=120,
        )
        if p.returncode == 0 and os.path.exists(out2) and os.path.getsize(out2) > 0:
            os.unlink(tmp_path)
            return out2
        last_err = p.stderr.decode("utf-8", "replace")[-200:].strip()
        try:
            os.unlink(out2)
        except OSError:
            pass
    raise RuntimeError(
        f"参考音频音量过低（RMS {rms:.0f}/32767），自动增益失败：{last_err}")


def ensure_usable_wav(path: str) -> str:
    """参考音频统一转成 16kHz 单声道 wav；无效/损坏/真静音文件抛错。返回新 wav 路径。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.close()
    last_err = ""
    for ff in _ffmpeg_candidates():
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        cmd = [ff, "-y", "-v", "error", "-i", path,
               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", tmp.name]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except Exception as e:  # noqa: BLE001
            last_err = f"{ff}: {e}"
            continue
        if proc.returncode == 0 and os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            try:
                return _maybe_gain(tmp.name)
            except RuntimeError:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                raise
        last_err = f"{ff}: {proc.stderr.decode('utf-8', 'replace')[-200:].strip()}"
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    raise RuntimeError(
        f"参考音频无效或已损坏，无法解码：{path}（{last_err}）。请换成有效的 wav/mp3 音频")
