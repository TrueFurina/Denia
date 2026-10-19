# -*- coding: utf-8 -*-
"""【云端全双工·2026-09-24】Qwen-Omni-Realtime 真全双工对话客户端。

封装阿里云百炼 Qwen-Omni-Realtime（speech-to-speech 真全双工）：
  - 麦克风 PCM16@16k -> 云端；云端返回 PCM16@24k 音频 -> 扬声器
  - 服务端 VAD 原生处理打断（barge-in），无需本地 VAD
  - voice 支持声音复刻（达妮娅音色克隆）或官方预设二次元音色
  - instructions 注入人格/行为（复用 prompts/call_profiles/*.txt）

设计原则（沿用 cascade_brain）：
  1. 完全独立、可单测、可随时摘掉；import 点由开关门控，不污染本地双工链路。
  2. 依赖（dashscope / pyaudio）按需懒加载，缺失时给清晰安装提示，绝不静默崩。
  3. 真机才验证；本模块只保证协议正确，不假设网络/设备存在。

回滚：本文件不被主程序默认引用时无任何副作用；体验走 tools/cloud_duplex_demo.py。
"""
from __future__ import annotations

import os
import sys
import time
import base64
import json
import numpy as np
import re
import logging
import threading
import queue
from typing import Optional, Callable

from ..perception._pa_shared import get_shared_pyaudio, pa_int16  # 共享 PortAudio 单例 + 模块级常量
from ..queue_util import put_drop_oldest
from ..audio.endpoint_audio import (list_endpoints, resolve_id,
                                    wasapi_index_for, get_default_id)
from ..perception._pa_shared import get_shared_pyaudio, pa_int16, pa_module  # 共享 PortAudio 单例 + 模块级常量

# 麦克风声学判据的绝对电平门限（16k/单声道 int16 归一化后）。
# 真人语音 RMS 通常 0.02~0.6；底噪约 0.001~0.005。
# 【2026-10-04】这里的门限**只用绝对值**，不要用"相对自身峰值"——坏设备的
# 干扰音峰值可达 0.68，按峰值比例算会把它的底噪块也算成"有声"，导致误判可用。
_ABS_TH_MIN = 0.02

# ============ 已验证麦克风记忆（跨进程持久化）============
# 【2026-10-04 18:00】现场声学探测有个无法回避的前提：探测时用户正在说话。
# 但拨号发生在"刚点下拨通"那一刻，他此刻多半还没开口 → 探测必然失败。
# 所以真正的判据应该是**通话中的客观证据**：一旦云端 VAD 在这条链路上触发了
# speech_started，就证明这条麦克风真的采到了人声。把这个结论记下来，下次直接用。
# 索引会随插拔漂移，故记录"设备名 + hostApi"这一稳定组合，而非索引本身。
_VERIFIED_MIC_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "verified_mics.json")
_verified_api: dict = {}


def _verified_key(name_l: str) -> str:
    """记忆文件的键：设备名（小写）+ 当前会话内的规范化形式。"""
    return str(name_l or "").strip()


def load_verified_mics() -> dict:
    """从磁盘载入"已验证可用麦克风"记忆（启动时调用一次）。"""
    global _verified_api
    try:
        if os.path.exists(_VERIFIED_MIC_FILE):
            with open(_VERIFIED_MIC_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _verified_api = {str(k): int(v) for k, v in data.items()
                                 if isinstance(v, (int, float))}
    except Exception:  # noqa: BLE001
        _verified_api = {}
    return dict(_verified_api)


def remember_verified_mic(device_name: str, host_api: int) -> None:
    """把"这条麦克风已确认能采到人声"写入记忆（通话中 VAD 触发时调用）。"""
    k = _verified_key(str(device_name or "").strip().lower())
    if not k:
        return
    try:
        if _verified_api.get(k) == int(host_api):
            return
        _verified_api[k] = int(host_api)
        os.makedirs(os.path.dirname(_VERIFIED_MIC_FILE), exist_ok=True)
        with open(_VERIFIED_MIC_FILE, "w", encoding="utf-8") as f:
            json.dump(_verified_api, f, ensure_ascii=False, indent=2)
        logger.info("双工：已记住可用麦克风 %r（hostApi=%s），下次拨号直接复用",
                    device_name, host_api)
    except Exception:  # noqa: BLE001
        pass



# 模块导入时即载入记忆，保证首次拨号就能复用上次验证过的麦克风
try:
    load_verified_mics()
except Exception:  # noqa: BLE001
    pass

logger = logging.getLogger(__name__)

# 播放队列上限（块数）。块大小随采样率/帧长变化，256 块在实际通话里约数十秒，
# 足够覆盖正常抖动；超过说明播放线程已经卡住，继续堆只会涨内存。
_PLAY_QUEUE_MAX = 256

_DEFAULT_RULES = """【说话风格】
- 用口语、自然的电话语气，一次只说一句，十到四十字。
- 不写括号动作描写、不使用任何标签或前缀。
- 只输出你要说的话本身。"""


def _read(path: str) -> str:
    try:
        if path and os.path.exists(path):
            return open(path, 'r', encoding='utf-8', errors='ignore').read().strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _get(node, key, default=None):
    """同时兼容 namespace（app 配置对象）与 dict（yaml 直接加载）。"""
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


# 【2026-10-04】双工默认预设音色。绝不能传空 voice 给 dashscope SDK：
# SDK 会把 None 序列化成字符串 "null"，服务端报 "Voice 'null' is not supported"，
# 表现为"连接正常但永远零音频"的静默哑。详见 _apply_session 注释。
_DEFAULT_VOICE = "Cherry"


def _cfg_path(cfg) -> str:
    """尽力取到 config.yaml 真实路径（用于把复刻出的音色写回配置）。"""
    for attr in ("path", "file_path", "config_path", "filepath"):
        p = getattr(cfg, attr, None)
        if p and os.path.exists(str(p)):
            return str(p)
    raw = getattr(cfg, "raw", None)
    # dict 也可能自带来源标记
    if isinstance(raw, dict):
        for k in ("__file__", "config_path"):
            p = raw.get(k)
            if p and os.path.exists(str(p)):
                return str(p)
    return ""


def _probe_input_alive(pa, idx: int, rate: int = 16000, tries: int = 10) -> float:
    """实探某输入索引是否真能拾音，返回最大 RMS（0=静音/哑设备）。

    【2026-10-04 新增·治"接通但不理人"】同一设备名在 Windows PortAudio 里会出现**多路**
    （如 '麦克风 (2- MCHOSE X9)' 实测有 idx=1/14/35 三条，分属不同后端）。其中只有
    真正在用的那路能采到声音，其余是"哑设备"（能打开、能读，但恒返回 0）。
    旧逻辑"精确匹配取第一个"→ 选中 idx=1（实测 max_rms=0.00003）→ 上行永远静音。
    故改为：候选全试一遍，用最大 RMS 择优。

    ⚠️【2026-10-04 加强】tries 由 4 提到 10、间隔 0.02→0.05（约 0.5s 窗口）：
    探测窗口太短会**恰好错过你说话**，把底噪当静音、进而选错设备。10-04 实证：
    一次探测选中 idx=17（0.769）但通话中该路全零，而 idx=20 一直正常。
    这类"瞬时探测"结果不可靠，故拉长窗口 + 通话中持续静音自动重开（见 _run_blocking）。
    """
    import array as _array
    import time as _t
    peak = 0.0
    st = None
    try:
        st = pa.open(format=pa_int16(), channels=1, rate=rate,
                     input=True, frames_per_buffer=1600,
                     input_device_index=idx)
    except Exception:  # noqa: BLE001
        return 0.0
    try:
        for _ in range(tries):
            try:
                d = st.read(1600, exception_on_overflow=False)
            except Exception:  # noqa: BLE001
                break
            if not d:
                continue
            a = _array.array("h")
            a.frombytes(d[: len(d) - (len(d) % 2)])
            if not a:
                continue
            acc = 0.0
            for x in a:
                v = x / 32768.0
                acc += v * v
            rms = (acc / len(a)) ** 0.5
            if rms > peak:
                peak = rms
            _t.sleep(0.05)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            if st is not None:
                st.close()
        except Exception:  # noqa: BLE001
            pass
    return peak







def _can_open_16k(pa, idx: int) -> bool:
    """该输入索引能否以云端要求的 16k/单声道/int16 打开流。

    实测同名设备里有标称 48k 的（WASAPI 那条），用 16k 直接抛 Invalid sample rate
    —— 对本链路完全不可用，必须在候选阶段就淘汰，否则会被"后端优先级"选中却开不起来。
    """
    st = None
    try:
        st = pa.open(format=pa_int16(), channels=1, rate=16000,
                     input=True, frames_per_buffer=1600, input_device_index=idx)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            if st is not None:
                st.close()
        except Exception:  # noqa: BLE001
            pass


def _probe_input_speech(pa, idx: int, rate: int = 16000, tries: int = 60) -> dict:
    """实探某输入索引，返回「是否真采到语音」的声学判据。

    【2026-10-04 17:50 关键·本项目最硬的一次诊断】
    此前只用「峰值 RMS」判死活，栽了三次：读到的是**设备周期性干扰音**的幅度，
    不是人声。17:39 那次通话日志里"上行电平 0.55~0.65 全程有起伏"看着很健康，
    录到盘上一分析才发现：RMS 序列自相关在 lag 39（3.9 秒）处相关 0.69（严格周期），
    频谱只有 1999/2000Hz 与 7997~7999Hz 两个纯音峰、**完全没有基频与共振峰**
    ⇒ 那是 idx=17 这路 WASAPI 设备在持续输出 2k/8kHz 干扰，**根本不是你的声音**。
    云端 server_vad 面对这种周期性非语音干扰，正确地不判定为"人声"——**云端是对的**。

    ⇒ 判据必须换成语音声学特征，而非"电平高"：
      ① 语音区(300~3400Hz)能量占比要高（真人语音 90%+）；
      ② 高频干扰区(>6kHz)能量占比要低（干扰音里 8kHz 纯音占 20%+）；
      ③ 有声块占比要够（能连续采到人声，而不是 0.5% 的零星块）；
      ④ 不能是强周期振荡（底噪泵动）。
    返回 dict: {rms, voice_ratio, hf_ratio, voiced_frac, periodic, is_voice}
    """
    import array as _array
    import math as _math
    import time as _t

    res = {"rms": 0.0, "voice_ratio": 0.0, "hf_ratio": 0.0,
           "voiced_frac": 0.0, "periodic": 0.0, "is_voice": False}
    # 绝对门限：真人语音在 16k/单声道下 RMS 通常 0.02~0.6。
    # 【关键】不能用"相对自身峰值"——坏设备(idx=17)干扰音峰值可达 0.68，
    # 按峰值比例算门限会把它的底噪块也算成"有声"，导致 voiced_frac 虚高、误判可用。
    ABS_TH = _ABS_TH_MIN
    st = None
    try:
        st = pa.open(format=pa_int16(), channels=1, rate=rate,
                     input=True, frames_per_buffer=1600,
                     input_device_index=idx)
    except Exception:  # noqa: BLE001
        return res
    blocks = []
    try:
        for _ in range(tries):
            try:
                d = st.read(1600, exception_on_overflow=False)
            except Exception:  # noqa: BLE001
                break
            if not d:
                continue
            a = _array.array("h")
            a.frombytes(d[: len(d) - (len(d) % 2)])
            if not a:
                continue
            blocks.append((a, d))
            _t.sleep(0.03)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            if st is not None:
                st.close()
        except Exception:  # noqa: BLE001
            pass
    if not blocks:
        return res

    # 逐块 RMS（复用原始字节算，避免重复转换）
    rms_list = []
    for a, d in blocks:
        acc = 0.0
        for x in a:
            v = x / 32768.0
            acc += v * v
        rms_list.append((acc / len(a)) ** 0.5 if a else 0.0)
    rms_max = max(rms_list)
    res["rms"] = rms_max
    if rms_max < ABS_TH:
        # 全程底噪，连绝对门限都没过 → 判为不可用（省掉后续 FFT）
        return res

    # 频谱：对**所有超过绝对门限的块**做功率累加，再算能量分布。
    # 【2026-10-04 修正】原先只取"能量最大的那一块"做 FFT，若那块恰好是
    # 干扰纯音（2k/8kHz）就会把整路误判为非语音；改成多块累加更稳。
    try:
        import numpy as _np
        sp_acc = None
        fr = None
        for a, d in blocks:
            acc_r = 0.0
            for x in a:
                v = x / 32768.0
                acc_r += v * v
            r = (acc_r / len(a)) ** 0.5 if a else 0.0
            if r < ABS_TH:
                continue
            seg = _np.frombuffer(d[: len(d) - (len(d) % 2)],
                                 dtype=_np.int16).astype(_np.float64) / 32768.0
            if len(seg) < 8:
                continue
            sp = _np.abs(_np.fft.rfft(seg * _np.hanning(len(seg)))) ** 2
            sp_acc = sp if sp_acc is None else sp_acc + sp
            if fr is None or len(fr) != len(sp):
                fr = _np.fft.rfftfreq(len(seg), 1.0 / rate)
        if sp_acc is not None and fr is not None and sp_acc.sum() > 0:
            tot = float(sp_acc.sum())
            voice = sp_acc[(fr >= 300) & (fr <= 3400)].sum()
            hf = sp_acc[fr > 6000].sum()
            res["voice_ratio"] = float(voice / tot * 100.0)
            res["hf_ratio"] = float(hf / tot * 100.0)
    except Exception:  # noqa: BLE001
        # 没 numpy 就退回"只看 RMS + 有声块占比"（保守：仍可能误判，但不崩）
        res["voice_ratio"] = 50.0
        res["hf_ratio"] = 5.0

    # 有声块占比 + 周期性
    res["voiced_frac"] = sum(1 for r in rms_list if r > ABS_TH) / len(rms_list)
    n = len(rms_list)
    mean = sum(rms_list) / n
    var = sum((r - mean) ** 2 for r in rms_list) / n
    if n > 8 and var > 1e-12:
        best_lag, best_c = 0, 0.0
        for lag in range(5, min(80, n - 1)):
            c = 0.0
            for i in range(n - lag):
                c += (rms_list[i] - mean) * (rms_list[i + lag] - mean)
            c /= (n - lag) * var
            if c > best_c:
                best_c, best_lag = c, lag
        res["periodic"] = float(best_c)

    # 终判：语音声学特征
    # 【2026-10-04 17:58 修正】voiced_frac 门槛从 0.08 放宽到 0.03：
    # 真人说话本就断续（句中换气、词间停顿），实测同一路好设备在短窗口里
    # 有声块占比常只有 1~4%，用 0.08 会把好设备误判成哑的。
    # 区分"好设备"与"干扰音"主要靠**频谱**（语音区 vs 高频纯音）+ 周期性，
    # voiced_frac 只作为"有没有在拾音"的辅助项，不该当主判据。
    res["is_voice"] = bool(
        res["rms"] >= _ABS_TH_MIN
        and res["voice_ratio"] >= 70.0        # 语音区能量占绝对大头（真人 90%+）
        and res["hf_ratio"] <= 8.0            # 高频纯音干扰不显著（干扰音 20%+）
        and res["voiced_frac"] >= 0.03        # 确实在拾音（允许断续）
        and res["periodic"] <= 0.60           # 不是强周期振荡
    )
    return res


def _resolve_device_index(pa, name: str, kind: str):
    """按设备名（非索引）在给定 pyaudio 实例里找索引。

    设备名跨 PortAudio 后端（sounddevice / pyaudio / wpatch）一致，
    而索引会随插拔漂移、各后端表序不同，故一律按名匹配。匹配不到返回 None。

    【2026-10-04 修正】同名设备在 Windows 上会**重复出现多路**（分属 MME/DirectSound/
    WASAPI 等后端），且其中部分为"哑设备"：能 open、能 read，但恒返回 0 电平。

    【2026-10-04 17:50 重大修正·删掉后端先验，改为纯声学判据】
    此前按"后端可信度 WASAPI(2) > DirectSound(1) > WDM-KS(3) > MME(0)"排序，
    再用**峰值 RMS** 择优。本机 17:50 全设备实测证明这套先验**完全反了**：
      · idx=1  (MME, api=0)  '麦克风 (2- MCHOSE X9)'  → ✅ 语音区 94%、有真人声
      · idx=4  (MME, api=0)  '麦克风 (MCHOSE K20 Pro)' → ✅ 语音区 94%、基频550Hz+共振峰1250Hz
      · idx=17 (DirectSound) '麦克风 (2- MCHOSE X9)'  → ❌ 有声块仅 0.5%、2k/8kHz 纯音干扰
      · idx=20 (DirectSound) '麦克风 (MCHOSE K20 Pro)' → ❌ 有声块仅 1.1%
    而旧逻辑偏偏把 MME 排到垫底(9)、优先选 DirectSound/WASAPI ⇒ **每次都选中几乎
    全哑的 idx=17**。这才是"接通有声、说话完全不应答"的真凶（前面几轮改播放器/
    改音色/改 AGC 全是改错层）。
    ⇒ 现在**只信声学判据**（`_probe_input_speech`：语音区能量占比 / 高频干扰 /
      有声块占比 / 周期性），谁真采到人声用谁；后端先验一律不参与决策。
    """
    try:
        _raw_name = str(name or "").strip()
        # 【2026-10-17 修复·设备必须"选哪个就是哪个"】
        # UI 现在会在"同名多路"时给出 `名字|hostapi` 形式的 id（如
        # '麦克风 (MCHOSE K20 Pro)|WASAPI'）。这里先拆出 hostapi 限定：
        # 有它 → 候选唯一、**确定性选中**，彻底不再靠声学探测去猜；
        # 无它（老配置 / 唯一名字）→ 走原有逻辑，完全兼容。
        api_want_l = ""
        if "|" in _raw_name:
            _n, _, _a = _raw_name.partition("|")
            _raw_name, api_want_l = _n.strip(), _a.strip().lower()
        name_l = _raw_name.lower()
        if not name_l or name_l in ("default", "auto", "none"):
            return None
        # hostApi 名称表（用于 api_want_l 过滤）
        _api_names = {}
        try:
            for _a in range(pa.get_host_api_count()):
                _api_names[_a] = str((pa.get_host_api_info_by_index(_a) or {}).get("name") or "").strip().lower()
        except Exception:  # noqa: BLE001
            _api_names = {}
        cands = []
        for i in range(pa.get_device_count()):
            try:
                d = pa.get_device_info_by_index(i)
            except Exception:  # noqa: BLE001
                continue
            if kind == "output" and int(d.get("maxOutputChannels") or 0) <= 0:
                continue
            if kind == "input" and int(d.get("maxInputChannels") or 0) <= 0:
                continue
            if str(d.get("name") or "").strip().lower() != name_l:
                continue
            if api_want_l:
                _ha = int(d.get("hostApi", -1) if d.get("hostApi") is not None else -1)
                if _api_names.get(_ha, "") != api_want_l:
                    continue
            cands.append(i)
        if not cands:
            # 【2026-10-13 修复·裸索引兼容】config 里可能残留**老版本 UI 存的裸索引**
            # （如 duplex_mic_device='1'）。按名匹配必然落空 → 返回 None → 上层静默
            # 回退系统默认输入，连日志都没有——2026-10-13 凌晨"语音不理我"的根因之一
            # （默认输入是键盘麦，说话电平进不了云端 VAD，且用户完全不知道没走自己选的设备）。
            # 现处理：数字串 → 按当前设备表把索引翻译成**设备名**（索引会漂移，名字才稳），
            # 再按名走正常匹配（含已验证记忆 + 声学探测）。翻译不出则醒目告警。
            if name_l.isdigit():
                _li = int(name_l)
                try:
                    _d = pa.get_device_info_by_index(_li)
                    _nm = str(_d.get("name") or "").strip()
                    _in = int(_d.get("maxInputChannels") or 0) > 0 if kind == "input" \
                        else int(_d.get("maxOutputChannels") or 0) > 0
                    if _nm and _in:
                        logger.warning(
                            "双工：设备配置 %r 是老版裸索引（按名匹配不到）。已映射为当前"
                            "设备表 idx=%d 的 %r 继续匹配；建议到「设置-通话设备」重新"
                            "选择一次麦克风，把配置换成设备名。", name, _li, _nm)
                        return _resolve_device_index(pa, _nm, kind)
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(
                    "双工：设备配置 %r 是裸索引且当前设备表 idx=%d 不是可用的%s，"
                    "回退系统默认。建议到「设置-通话设备」重新选择一次。", name, _li, kind)
            return None
        if len(cands) == 1:
            return cands[0]
        if kind == "input":
            # ① 硬过滤：以云端要求的 16k 能否打开为准（标称 48k 的 WASAPI 会开失败）
            usable = [i for i in cands if _can_open_16k(pa, i)]
            if usable:
                cands = usable
            if not cands:
                return None

            # ⭐⭐【2026-10-04 18:00 决定性修复·不再依赖"用户正在说话"】
            # 前面所有基于"实探电平/频谱"的选择器都有一个致命前提：**探测时用户
            # 正在说话**。但拨号发生在"用户刚点下拨通"的瞬间，他此刻多半还没开口，
            # 于是探测必然采到一片安静 → 把所有候选都判成"没语音" → 只能退回
            # 电平最高的那路，而那路恰恰是干扰音（idx=17）。这就是为什么 17:55
            # 明明 idx=1 语音区 89% 却仍被选中 idx=17。
            # ⇒ 改为**记忆制**：一旦某路通过"说话中"的严格声学验证（通话中检测到
            #    云端 VAD 触发 = 证明这路真能采到人声），就把它的**设备名+后端特征**
            #    记下来；下次拨号直接复用，不再现场探测。
            #    索引会随插拔漂移，所以记的是"名字+hostApi"这类稳定标识，命中后
            #    再按名字反查当下索引。
            try:
                key = _verified_key(name_l)
                if key:
                    api_want = _verified_api.get(key)
                    if api_want is not None:
                        for i in cands:
                            try:
                                # ⚠️ 不能写 `int(... .get("hostApi") or -1)`：
                                # hostApi 恰好是 0（MME）时，`0 or -1` → -1（0 是 falsy），
                                # 会让 MME 路永远匹配不上记忆 → 记忆机制形同虚设、
                                # 每次都退回现场探测。必须显式处理 None。
                                _raw_api = pa.get_device_info_by_index(i).get("hostApi")
                                _api = int(_raw_api) if _raw_api is not None else -1
                            except Exception:  # noqa: BLE001
                                continue
                            if _api == int(api_want):
                                # 【2026-10-18 修复·记忆失效导致"说话没反应"】
                                # 记忆是"曾经 VAD 触发过"写下的，但设备/驱动/系统
                                # 隐私设置会变。本机实测：记忆存了 MME(idx=1)，如今
                                # 已变哑（rms 0.00006），而记忆机制"跳过现场探测"直接
                                # 用它 → 永远采不到声音 → AI 永不回应（用户看到的就是
                                # "接通了但说话没反应"）。
                                # 现在：命中记忆后先做 0.7s 电平抽样，有信号才沿用；
                                # 全静音则作废记忆、落到下面的声学探测重选。
                                # 【2026-10-18 决定性修复】不再试开流复核。
                                # 试开(16k/ch1)+close 会污染 DirectSound 后端，
                                # 之后正式流(44.1k/ch2)读到陈旧缓冲=死数据。
                                # 记忆本来自"云端 VAD 触发"才写入，直接沿用。
                                _memo_ok = True
                                _memo_note = "沿用（跳过试开复核）"
                                _st = None
                                if _memo_ok:
                                    logger.info("双工：设备 %r 沿用上次已验证可用的麦克风 "
                                                "idx=%s（hostApi=%s，复核 %s 通过）",
                                                name, i, api_want, _memo_note)
                                    return i
                                logger.warning(
                                    "双工：记忆里的麦克风 idx=%s（hostApi=%s）复核%s 全静音 "
                                    "→ 作废该记忆，重新按声学判据选设备",
                                    i, api_want, _memo_note)
            except Exception:  # noqa: BLE001
                pass

            # ② 未能复用 → 现场两阶段声学探测（作为首次/换设备后的兜底）
            #    两阶段：短探测先筛掉"完全没信号"的死路，再对存活者用长窗口复测。
            #    【为什么必须长窗口】真人说话是**断续**的，短窗口常常正好落在两句
            #    话之间 → voiced_frac≈0 → 把好设备误判成哑的（17:55 实测：idx=1
            #    语音区 89% 却被判 ❌，只因有声块 1%）。
            def _score(m):
                if not m or m["rms"] < _ABS_TH_MIN:
                    return -1.0
                if not m["voice_ratio"]:
                    return -1.0
                return (m["voice_ratio"] * 2.0
                        + m["voiced_frac"] * 100.0
                        - m["hf_ratio"]
                        - m["periodic"] * 20.0)

            # 阶段一：短探测（tries=25，约 0.8s）筛掉明显死路
            # 【2026-10-18 修复·探测不稳定】探测依赖"用户恰好在说话"。拨通瞬间
            # 多半没说话 → 全静音 → alive 空。旧逻辑立刻进兜底，可能选中哑设备。
            # 现在：全静音时**等 1.2s 重探**（最多 3 轮），给用户开口的机会；
            # 3 轮后仍全静音才进 host 优先级兜底（确定性选 WASAPI>DS>MME）。
            short = {}
            alive = []
            for _round in range(3):
                alive = []
                for i in cands:
                    m = _probe_input_speech(pa, i, tries=25)
                    short[i] = m
                    if m["rms"] >= _ABS_TH_MIN and m["voice_ratio"] >= 45.0:
                        alive.append(i)
                if alive:
                    break
                if _round < 2:
                    logger.info("双工：声学探测第 %d 轮全静音（用户未说话），1.2s 后重探",
                                _round + 1)
                    time.sleep(1.2)
            # 阶段二：对存活者用长窗口（tries=60，约 2s）复测，取最优
            best_i, best_m, best_score = None, None, -1.0
            for i in (alive or list(cands)):
                m = _probe_input_speech(pa, i, tries=60) if i in alive else short[i]
                logger.info("双工麦克风声学探测：idx=%s 峰值RMS=%.4f 语音区=%.0f%% "
                            "高频干扰=%.0f%% 有声块=%.0f%% 周期=%.2f → %s",
                            i, m["rms"], m["voice_ratio"], m["hf_ratio"],
                            m["voiced_frac"], m["periodic"],
                            "✅真人声" if m["is_voice"] else "❌未检出语音")
                sc = _score(m)
                if sc > best_score:
                    best_i, best_m, best_score = i, m, sc
            if best_i is not None:
                logger.warning(
                    "双工：设备名 %r 有 %d 路同名，已按「声学判据」选中真正采到人声的一路："
                    "idx=%s（语音区=%.0f%% 有声块=%.0f%% 高频干扰=%.0f%%）",
                    name, len(cands), best_i, best_m["voice_ratio"],
                    best_m["voiced_frac"] * 100.0, best_m["hf_ratio"])
                return best_i

            # 【2026-10-18 修复·全静音兜底选错 host】
            # 探测时用户没说话 → 所有候选都静音 → alive 空 → 旧逻辑按分数取最高，
            # 但 MME 后端有直流偏移（rms 0.00028）> DirectSound 真静音（0.00004）
            # ⇒ 伪装成"信号最好"被选中 ⇒ 又是哑设备（本机 idx=1 实锤）。
            # 现在：全静音时按 hostApi 优先级选（WASAPI > DirectSound > 其他 > MME），
            # MME 排最后——它是 Windows 最老的后端，哑设备/直流偏移最常见。
            def _host_rank(i):
                try:
                    _ha = int((pa.get_device_info_by_index(i) or {}).get("hostApi") or -1)
                except Exception:  # noqa: BLE001
                    return 9
                _hn = str((pa.get_host_api_info_by_index(_ha) or {}).get("name") or "").lower()
                if "wasapi" in _hn:
                    return 0
                if "directsound" in _hn:
                    return 1
                if "wdm" in _hn or "ks" in _hn:
                    return 2
                if "mme" in _hn:
                    return 8
                return 5

            _fb = sorted(cands, key=_host_rank)[0]
            _fb_ha = int((pa.get_device_info_by_index(_fb) or {}).get("hostApi") or -1)
            _fb_hn = str((pa.get_host_api_info_by_index(_fb_ha) or {}).get("name") or "?")
            logger.warning(
                "双工：设备名 %r 各路均未检出人声（用户未说话或设备哑）→ "
                "按 host 优先级兜底选 idx=%s（%s）",
                name, _fb, _fb_hn)
            return _fb
        # 播放设备：保持原有"取第一个"（输出侧无语音判据需求）
        return cands[0]
    except Exception:  # noqa: BLE001
        return None


class _AudioPlayer:
    """24k/16bit/单声道 流式播放器；支持 flush（打断时丢弃未播缓冲）。

    播放后端（双后端兜底，对齐两套已验证链路）：
      · 优先 sounddevice：与本地双工 tts.py 用**同一张设备名表/索引表**，能正确解析
        用户设置的扬声器名（如 '扬声器 (2- MCHOSE X9)'）。解析策略＝精确匹配→子串
        匹配→回退系统默认（照搬 tts._resolve_out_index）；指定设备打不开也回退默认。
      · 回退 pyaudiowpatch：仅当运行环境**无 sounddevice** 时走此路径（如 agent_venv），
        一律走**系统默认设备**（对齐「启动云端达妮娅.bat」好链路：它不读
        duplex_speaker_device、用默认设备，验证能正常出声）。
    核心原则：**绝不因"选了某个设备名"而彻底没声**——任何解析/打开失败都回退默认。
    """

    def __init__(self, rate: int = 24000, device: str = "") -> None:
        self.rate = rate
        # 【2026-10-17 修复·"小黄人声"】实际打开的流采样率。PortAudio 规则：
        # 流以多少 Hz 打开，write() 的数据就必须是多少 Hz；若两者不一致而直接写，
        # 播放速度会按比例失真（48k 流写 24k 数据 = 快一倍、音调升八度）。
        # 因此这里记录实际打开率，写入前按需重采样（见 _run）。
        self._stream_rate = int(rate)
        # 重采样状态：_rs_pos = 下一个待读输入位置的浮点坐标；_rs_tail = 未被消费的块尾
        self._rs_pos = 0.0
        self._rs_tail = None
        # 实际打开流的声道数（1 或 2）：写入时若为 2 而数据是单声道，则复制成两声道。
        self._stream_channels = 1
        self._device = (device or "").strip()
        # 播放缓冲上限：写入端来自网络/模型的流式音频，播放线程一旦卡住
        # （设备异常、PortAudio 重连）原实现会无界增长。丢最旧的块会有轻微
        # 断音，但比内存持续上涨安全；barge-in 有 flush() 单独清空。
        self._q: "queue.Queue" = queue.Queue(maxsize=_PLAY_QUEUE_MAX)
        self._q_dropped = 0
        self._pa = None
        self._sd = None
        self._np = None
        self._stream = None
        self._backend = None
        self._thread = None
        self._stop = threading.Event()
        self._err_total = 0     # 【2026-10-09】写入失败累计（stop 时汇总打印）
        # 【2026-10-09 新增·段错误防护】stream 的 write（_run 线程）与
        # close/reopen（stop/_reopen_stream）必须互斥——实测并发 write+close
        # 会触发 PortAudio 原生层 use-after-free（烟测直接 SIGSEGV）。
        # threading.Lock 不可重入：_run 内部自愈走 _reopen_stream_locked。
        self._lock = threading.Lock()
        self._open()

    def _resolve_idx_sd(self, sd) -> "Optional[int]":
        """用 sounddevice 设备表解析输出设备（支持 `名字|hostapi` 精确限定）。"""
        s = self._device
        if not s or s.lower() in ("default", "none", "auto"):
            return None
        # 【2026-10-17 修复·扬声器也要"选哪个就是哪个"】同名多路（MME/DirectSound/
        # WASAPI）时，UI 给的是 `名字|hostapi`；这里按 hostapi 精确过滤，选谁开谁。
        api_want_l = ""
        if "|" in s:
            _n, _, _a = s.partition("|")
            s, api_want_l = _n.strip(), _a.strip().lower()
        try:
            rows = list(enumerate(sd.query_devices()))
            _apis = {}
            try:
                for a, info in enumerate(sd.query_hostapis()):
                    _apis[a] = str((info or {}).get("name") or "").strip().lower()
            except Exception:  # noqa: BLE001
                _apis = {}

            def _api_of(d):
                try:
                    return _apis.get(int((d or {}).get("hostapi", -1) if (d or {}).get("hostapi") is not None else -1), "")
                except Exception:  # noqa: BLE001
                    return ""

            for i, d in rows:
                if ((d or {}).get("max_output_channels", 0) > 0
                        and str((d or {}).get("name") or "") == s
                        and (not api_want_l or _api_of(d) == api_want_l)):
                    return i
            low = s.lower()
            for i, d in rows:
                if ((d or {}).get("max_output_channels", 0) > 0
                        and low in str((d or {}).get("name") or "").lower()
                        and (not api_want_l or _api_of(d) == api_want_l)):
                    return i
        except Exception:  # noqa: BLE001
            logger.debug("sounddevice 按名解析输出设备失败: %s", s, exc_info=True)
        logger.warning("输出设备 %r 在 sounddevice 表里找不到，回退系统默认设备", self._device)
        return None

    def _open_stream_only(self) -> None:
        """只重建输出流（不起新线程）——_open 与写入自愈 _reopen_stream 共用。"""
        # ---- 优先 sounddevice（与本地双工同一设备表，能正确解析用户设的扬声器名）----
        try:
            import sounddevice as sd
            import numpy as np
            self._sd = sd
            self._np = np
            idx = self._resolve_idx_sd(sd)
            # 【2026-10-17 修复·"K20 Pro 播放不了"】
            # 旧重试逻辑**始终用 self.rate=24000**：而实测本机 WASAPI/DirectSound 对 24k
            # 直接 `Invalid sample rate`，MME 又常"能开但不出声" ⇒ 目标设备永远开不出来，
            # 最后悄悄回退系统默认（用户体感=选了 K20 Pro 却没声）。
            # 现在：候选 = 该设备在**所有 host API** 下的索引 × **多个采样率**，
            # 逐个试开，第一个成功即用；并把实际用的 host/采样率记下来供 UI 回显。
            self._actual = {}
            _bare = str(self._device or "").split("|")[0].strip().lower()
            _explicit_api = ""
            if "|" in str(self._device or ""):
                _explicit_api = str(self._device).partition("|")[2].strip().lower()

            def _rate_cands(dev_i):
                """采样率候选：**设备默认率优先**（WASAPI 共享模式最可能真出声），
                其次 self.rate（无需重采样），再常见率兜底。"""
                out = []
                try:
                    _dd = sd.query_devices(dev_i) if dev_i is not None else None
                    _dr = int(float((_dd or {}).get("default_samplerate") or 0))
                    if _dr:
                        out.append(_dr)
                except Exception:  # noqa: BLE001
                    pass
                for _r in (self.rate, 48000, 44100, 16000, 32000, 22050):
                    if _r and int(_r) not in out:
                        out.append(int(_r))
                return out

            def _settings_ok(dev_i, rate, channels=1):
                """官方预校验：驱动不接受的组合直接跳过，避免"打开成功却静默"。"""
                try:
                    sd.check_output_settings(device=dev_i, channels=channels,
                                             dtype="int16", samplerate=rate)
                    return True
                except Exception:  # noqa: BLE001
                    return False

            _cands = []
            try:
                _apis = {}
                for _a, _info in enumerate(sd.query_hostapis()):
                    _apis[_a] = str((_info or {}).get("name") or "")
                _rows = list(enumerate(sd.query_devices()))

                def _prio(_an):
                    a = str(_an or "").lower()
                    if "wasapi" in a:
                        return 0
                    if "directsound" in a:
                        return 1
                    if "mme" in a:
                        return 2
                    return 3
                _same = []
                for _i, _d in _rows:
                    if int((_d or {}).get("max_output_channels", 0) or 0) <= 0:
                        continue
                    if str((_d or {}).get("name") or "").strip().lower() != _bare:
                        continue
                    _an = _apis.get(int((_d or {}).get("hostapi", -1) or -1), "")
                    if _explicit_api and _an.strip().lower() != _explicit_api:
                        continue
                    _same.append((_i, _an))
                _same.sort(key=lambda x: _prio(x[1]))
                if idx is not None and all(_i != idx for _i, _ in _same):
                    _same.insert(0, (idx, ""))
                _cands = _same
            except Exception:  # noqa: BLE001
                logger.debug("枚举同设备 host 失败", exc_info=True)
            if not _cands and idx is not None:
                _cands = [(idx, "")]
            if not _cands:
                _cands = [(None, "")]          # 系统默认

            _opened = False
            _last_err = None
            # 声道候选：**优先立体声(2)**，其次单声道(1)。
            # 依据：端点常声明 8 声道，部分 Windows 驱动对 channels=1 的流
            # "接受打开但不渲染"（用户实测：K20 Pro 天天在用，但我们的单声道流从它
            # 出来没声音；X9 恰好容忍单声道）。立体声是共享模式最通用的形态。
            def _chan_cands(dev_i):
                out = []
                try:
                    _mx = int((sd.query_devices(dev_i) or {}).get("max_output_channels") or 0) \
                        if dev_i is not None else 2
                except Exception:  # noqa: BLE001
                    _mx = 2
                for _c in (2, 1):
                    if _mx <= 0 or _c <= _mx:
                        out.append(_c)
                return out or [1]

            _opened = False
            _last_err = None
            for _ci, _can in _cands:
                for _r in _rate_cands(_ci):
                    for _ch in _chan_cands(_ci):
                        if not _settings_ok(_ci, _r, _ch):
                            continue      # 官方预校验不过 → 驱动不接受该组合，跳过
                        try:
                            self._stream = sd.OutputStream(
                                device=_ci, samplerate=_r, channels=_ch, dtype="int16")
                            self._stream.start()
                            self._actual = {"index": _ci, "hostapi": _can, "samplerate": _r,
                                            "channels": _ch, "opened_ok": True,
                                            "requested": self._device or "default"}
                            self._stream_rate = int(_r)
                            self._stream_channels = int(_ch)
                            if _r != int(self.rate):
                                logger.info("云端双工播放器：设备不支持 %dHz，已以 %dHz 打开，"
                                            "写入时将重采样（不影响音调/语速）",
                                            int(self.rate), int(_r))
                            logger.info("云端双工播放器：已打开 设备=%r host=%s idx=%s rate=%d ch=%d",
                                        self._device or "系统默认", _can or "?", _ci, _r, _ch)
                            _opened = True
                            break
                        except Exception as e_t:  # noqa: BLE001
                            _last_err = e_t
                            try:
                                if self._stream is not None:
                                    self._stream.close()
                            except Exception:  # noqa: BLE001
                                pass
                            self._stream = None
                            continue
                    if _opened:
                        break
                if _opened:
                    break
            if not _opened:
                logger.warning("云端双工：指定扬声器 %r 在所有 host/采样率/声道下均失败"
                               "（最后错误 %s），回退系统默认设备", self._device, str(_last_err)[:80])
                for _r in _rate_cands(None):
                    for _ch in (2, 1):
                        if not _settings_ok(None, _r, _ch):
                            continue
                        try:
                            self._stream = sd.OutputStream(
                                samplerate=_r, channels=_ch, dtype="int16")
                            self._stream.start()
                            self._actual = {"index": None, "hostapi": "", "samplerate": _r,
                                            "channels": _ch, "opened_ok": True,
                                            "requested": self._device or "default",
                                            "fellback": True}
                            self._stream_rate = int(_r)
                            self._stream_channels = int(_ch)
                            break
                        except Exception:  # noqa: BLE001
                            self._stream = None
                            continue
                    if self._stream is not None:
                        break
                if self._stream is None:
                    raise RuntimeError("无法打开任何输出设备: %s" % (str(_last_err)[:120],))
            self._backend = "sd"
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "云端双工 sounddevice 不可用，回退 pyaudiowpatch 默认设备: %s", e)
            # ---- 回退 pyaudiowpatch：一律走系统默认设备（对齐 .bat 好链路）----
            try:
                # 🛡️ 共享单例：会话期间绝不 terminate，避免与别的线程并发 init/terminate
                # 触发原生访问违规（0xC0000005）。
                self._pa = get_shared_pyaudio()
            except Exception as e2:  # noqa: BLE001
                raise RuntimeError(
                    "缺少 sounddevice/pyaudiowpatch，请先安装（需系统 PortAudio）") from e2
            # ⚠️ paInt16 是**模块级**常量，不能从实例上取（实例取会 AttributeError）
            kw = dict(format=pa_int16(), channels=1, rate=self.rate, output=True)
            # 回退路径不做名称解析（pyaudio 设备表与 sounddevice 不同，名称易解析到
            # 无声端点）；始终走系统默认设备 —— 与「启动云端达妮娅.bat」行为一致。
            self._stream = self._pa.open(**kw)
            self._backend = "pa"

    def _open(self) -> None:
        with self._lock:
            self._open_stream_only()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _reopen_stream_locked(self) -> None:
        """重建输出流核心（调用方必须已持有 _lock 或在 _run 线程锁外）。"""
        try:
            if self._stream is not None:
                try:
                    if self._backend == "sd":
                        self._stream.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._stream.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._stream = None
        try:
            self._open_stream_only()
            logger.warning("云端双工播放器：输出流已自动重开（backend=%s）", self._backend)
        except Exception:  # noqa: BLE001
            logger.exception("云端双工播放器：重开输出流失败（下轮写入再试）")

    def _reopen_stream(self) -> None:
        """【2026-10-09 新增·写入自愈】连续写入失败后重建输出流（不开新线程）。

        触发场景：通话中系统重枚举音频端点（换麦/插拔耳机/设备切换）会让
        OutputStream 变 invalid state，此后每次 write 都抛异常——旧版只打
        DEBUG（INFO 日志下完全静默）⇒ "明明收到音频、wav 存档也完整，就是
        没声音且日志零痕迹"（run_20261009_011718 症状）。
        """
        with self._lock:
            self._reopen_stream_locked()

    def set_device(self, device_name: str) -> bool:
        """【2026-10-18 新增·通话中切换扬声器】热切换输出设备并立即生效。

        【为什么必须单独实现】
        云端双工的语音由本类内部的 _AudioPlayer 直接播放，**不走上层 TTS**。
        而 webapp 的「通话设备切换：speaker → X」只调了 tts.set_output_device()
        （那是给文字聊天/TTS 用的），云端双工播放器完全没被通知 ——
        于是用户切了扬声器却听不到任何变化，仍是旧设备在出声。
        这正是"扬声器切不了、切了没声音"的根因。

        实现：更新 self._device（设备名）→ 清空积压队列 → 重建输出流。
        返回 True 表示已应用（不保证设备一定打开成功，失败会自动回退默认设备）。
        """
        _dev = str(device_name or "").strip()
        if not _dev:
            _dev = ""           # 空 = 系统默认
        old = getattr(self, "_device", None)
        self._device = _dev
        logger.info("云端双工播放器：切换输出设备 %r → %r（将重建输出流）",
                    old, _dev or "系统默认")
        try:
            # 清掉旧设备上还没播完的积压，避免"切完还在旧设备上响一句"
            self.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            with self._lock:
                self._reopen_stream_locked()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("云端双工播放器：切换输出设备失败")
            return False

    def _run(self) -> None:
        _err_n = 0
        _last_warn_ts = 0.0
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.05)
            except queue.Empty:
                # 正常路径：这是 50ms 的轮询超时，不是错误（别当异常吞）
                continue
            try:
                with self._lock:
                    # 与 stop()/_reopen_stream 的 close/reopen 互斥——
                    # 并发 write+close 会段错误（烟测 SIGSEGV 实证）。
                    if self._stop.is_set():
                        break
                    if self._backend == "sd":
                        _arr = self._np.frombuffer(chunk, dtype=self._np.int16)
                        # 【2026-10-17 修复·"小黄人声"】流采样率与数据采样率不一致时
                        # 必须重采样，否则播放速度/音调按比例失真。
                        if int(self._stream_rate) != int(self.rate) and _arr.size:
                            _arr = self._resample(_arr)
                        if _arr.size:
                            # 流若是立体声而数据是单声道 → 复制到各声道
                            _nch = int(getattr(self, "_stream_channels", 1) or 1)
                            if _nch > 1:
                                _arr = self._np.repeat(_arr[:, None], _nch, axis=1)
                            self._stream.write(_arr)
                    else:
                        self._stream.write(chunk)
                _err_n = 0
            except Exception as e:  # noqa: BLE001
                # 【2026-10-09 修复·"收到音频却没声音"零痕迹】旧版只打 DEBUG，
                # 而 app.log_level=INFO ⇒ 播放写入失败在日志里完全不可见，
                # 用户端表现为"通话记录有音频、就是没声音"。现升级 WARNING
                # （限频 5s 一条，避免每 0.05s 刷屏）+ 连续 3 次失败自动重开
                # 输出流自愈（通话中系统重枚举端点会让流变 invalid state）。
                _err_n += 1
                self._err_total += 1
                _now = time.time()
                if _now - _last_warn_ts >= 5.0:
                    logger.warning(
                        "云端双工播放器写入失败（连续%d次，backend=%s）：%r —— "
                        "音频已收到但未能送进扬声器，若反复出现将自动重开输出流",
                        _err_n, self._backend, e)
                    _last_warn_ts = _now
                if _err_n >= 3:
                    _err_n = 0
                    with self._lock:
                        self._reopen_stream_locked()

    def _resample(self, arr):
        """把 self.rate 的 int16 数据重采样到 self._stream_rate（严谨版，跨块无漂移）。

        【为什么需要】PortAudio 规则：流以多少 Hz 打开，write() 的数据就必须是多少 Hz。
        若设备只接受 48k 而我们的语音是 24k，直接写会**快一倍、音调升八度**（用户实测
        "小黄人声"）。故此处把数据重采样到流的实际采样率。

        【实现要点·正确性】维护"下一个待读输入位置"的**绝对浮点坐标** `_rs_pos`
        （以输入样本为单位），每块输出严格按 t = j*src/dst 取值，输出后把 `_rs_pos`
        推进到下一个真正未消费的输入位置，并把**块尾残样**带入下一块：
          · 不会累积漂移（非整数比率如 44100→24000 也准确）；
          · 块边界连续（残样参与下一块插值），不产生周期性咔哒。
        用线性插值：实时语音足够，且不引入 scipy/soxr 依赖（打包更稳）。
        """
        try:
            np = self._np
            src, dst = int(self.rate), int(self._stream_rate)
            if src == dst or arr.size == 0:
                return arr
            x = arr.astype(np.float64)
            ratio = float(src) / float(dst)          # 每个输出样本推进多少输入样本
            # 拼接上一块残留（保证跨块插值连续）
            if self._rs_tail is not None and self._rs_tail.size:
                buf = np.concatenate((self._rs_tail, x))
                base = -int(self._rs_tail.size)      # 残留样本对应的坐标起点（负数）
            else:
                buf = x
                base = 0
            # 本块内可用的输出样本数：要求插值右端点不超出 buf
            pos = float(self._rs_pos)                # 相对 buf 的浮点读位置
            last = buf.size - 1
            if pos > last:
                # 读位置已越过本块（异常保护）
                self._rs_pos = 0.0
                self._rs_tail = x[-1:].copy()
                return arr[:0]
            n_out = int(np.floor((last - pos) / ratio)) + 1
            if n_out <= 0:
                self._rs_tail = buf.copy()
                self._rs_pos = pos - buf.size
                return arr[:0]
            idx = pos + ratio * np.arange(n_out, dtype=np.float64)
            y = np.interp(idx, np.arange(buf.size, dtype=np.float64), buf)
            # 推进读位置，并把"本块未被消费的尾部"留作下一块的头部
            self._rs_pos = pos + ratio * n_out
            consumed = int(np.floor(self._rs_pos))
            if consumed >= buf.size:
                self._rs_tail = None
                self._rs_pos = self._rs_pos - buf.size
            else:
                self._rs_tail = buf[consumed:].copy()
                self._rs_pos = self._rs_pos - consumed
            return np.clip(y, -32768, 32767).astype(np.int16)
        except Exception:  # noqa: BLE001
            logger.debug("重采样失败，退化为直接写入（可能变速）", exc_info=True)
            return arr

    def play(self, pcm16_bytes: bytes) -> None:
        if not pcm16_bytes:
            return
        if put_drop_oldest(self._q, pcm16_bytes):
            self._q_dropped += 1
            if self._q_dropped % 50 == 1:
                logger.warning("播放缓冲已满，累计丢弃 %d 块（播放线程跟不上写入）",
                               self._q_dropped)

    def is_playing(self) -> bool:
        """是否正在出声（供 VAD 做回声抑制：AI 说话时抬高起音阈值）。"""
        try:
            return bool(getattr(self, "_playing", False)) or bool(
                getattr(self, "_queue", None) and not self._queue.empty())
        except Exception:  # noqa: BLE001
            return False

    def flush(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._stop.set()
        # 【2026-10-18 修复·挂断后她还在说话】
        # 旧 stop() 只置 _stop + 关流，**不清空队列**。而 _run 线程的消费顺序是
        # "先 get 再判断 _stop"：挂断瞬间队列里已排入的音频（实测一次挂断前排了
        # 142080 字节 / 10 分片）仍会被逐块取出并写进扬声器 —— 用户挂断回聊天页后
        # 还能听见她用达妮娅音色把上一句说完，误以为"打字被送进双工了"。
        # 现在：先清空队列再关流，保证挂断即静音。
        try:
            _dropped = 0
            while True:
                try:
                    self._q.get_nowait()
                    _dropped += 1
                except queue.Empty:
                    break
            if _dropped:
                logger.info("云端双工播放器：挂断时丢弃排队音频 %d 块（避免挂断后继续出声）",
                            _dropped)
        except Exception:  # noqa: BLE001
            pass
        # 【2026-10-09】会话结束汇总：本次通话播放写入有无失败——
        # 有失败即说明"没声音"发生在输出侧而非云端，日志直接给出结论。
        if getattr(self, "_err_total", 0) > 0:
            logger.warning(
                "云端双工播放器结束：本次通话写入失败累计 %d 次（>0 即存在"
                "『收到音频但没送进扬声器』的时段，多为通话中设备被重枚举）",
                self._err_total)
        # 【2026-10-09·段错误防护】关流必须与 _run 的 write 互斥——
        # 旧版并发 write+close 触发 PortAudio use-after-free（烟测 SIGSEGV 实证）。
        with self._lock:
            try:
                if self._backend == "sd" and self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
                elif self._stream is not None:
                    self._stream.stop_stream()
                    self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        # 🛡️ 不 terminate 共享单例（见 _pa_shared）：只关流，释放输出设备但不销毁全局状态。


class CloudOmniRealtime:
    """Qwen-Omni-Realtime 全双工对话封装。"""

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        ws_url: str = "",
        voice: str = "",
        ref_audio: str = "",
        enable_speech_emotion: bool = True,
        turn_detection: str = "server_vad",
        vad_threshold: float = 0.5,
        vad_silence_ms: int = 600,
        instructions: str = "",
        max_history_turns: int = 20,
        on_user_text: Optional[Callable[[str], None]] = None,
        on_assistant_text: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_user_audio: Optional[Callable[[bytes], None]] = None,
        on_response_start: Optional[Callable[[], None]] = None,
        on_status: Optional[Callable[[Any], None]] = None,
        on_fatal: Optional[Callable[[str], None]] = None,
        auto_play: bool = True,
        speaker_device: str = "",
        mic_device: str = "",
        greet_on_connect: bool = False,
        local_vad: bool = True,
        workspace_id: str = "",
        workspace_region: str = "",
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        # 【2026-10-18 修复·qwen3.8 必须用业务空间绑定端点】
        # 官方文档：qwen3.8-omni-flash-realtime 必须使用
        #   wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime
        # 实测：旧端点 dashscope.aliyuncs.com 能建连、能 session.update，
        # 但**完全不处理音频**（推 6 秒真实语音 → 零 VAD/零转写/零音频，也不报错），
        # 且对任何 voice 值（含模型自己的默认音色）一律 400。
        # 绑定端点实测：voice='Tina' 被接受，commit + AI 音频全部正常。
        self.base_url = (base_url or "").rstrip("/")
        _wsid = str(workspace_id or "").strip()
        # 【2026-10-18】用户没显式填 workspace_id 时，从 base_url / ws_url 自动提取。
        # 百炼控制台的「业务空间专属 Base URL」形如
        #   https://<workspace-id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
        # 用户复制这个地址填进 base_url 后，双工端点自动跟着走，
        # 不需要他额外理解/填写"业务空间 ID"这个概念。
        if not _wsid:
            for _src in (base_url or "", ws_url or ""):
                _m = re.search(
                    r"(?:https?|wss?)://([A-Za-z0-9\-]+)\.([a-z]{2}-[a-z]+-?\d*)\.maas\.aliyuncs\.com",
                    str(_src))
                if _m:
                    _wsid = _m.group(1)
                    if not workspace_region:
                        workspace_region = _m.group(2)
                    logger.info("云端双工：从地址自动识别业务空间 %r（地域 %s）",
                                _wsid, workspace_region)
                    break
        if ws_url:
            self.ws_url = ws_url
        elif _wsid:
            # 地域可配（默认北京）；兼容用户直接填完整域名的情况
            _region = str(workspace_region or "cn-beijing").strip() or "cn-beijing"
            if "." in _wsid:                      # 已是完整域名
                _host = _wsid
            else:
                _host = "%s.%s.maas.aliyuncs.com" % (_wsid, _region)
            self.ws_url = "wss://%s/api-ws/v1/realtime" % _host
            logger.info("云端双工：使用业务空间绑定端点 wss://%s/api-ws/v1/realtime", _host)
        else:
            self.ws_url = (self.base_url.replace(
                "https://", "wss://") + "/api-ws/v1/realtime")
            logger.warning(
                "云端双工：未配置业务空间 ID（workspace_id），回退通用端点 %s。"
                "注意：qwen3.8-omni-flash-realtime 在通用端点上只建连、不处理音频，"
                "请在设置里填写百炼「业务空间 ID」（形如 llm-xxxx 或 ws-xxxx）。",
                self.ws_url)
        # 【2026-10-18 修复·预设音色名被 realtime 模型 400 拒绝】
        # 实测：voice='Cherry' → <400> Voice 'Cherry' is not supported.（连接秒断）
        #       voice='qwen-omni-vc-…'（克隆 id）→ 正常
        #       voice=''（模型默认 Chelsie）→ 正常
        # qwen3.8-omni-flash-realtime 只认**克隆 id**或**模型默认**，
        # 不认 qwen-tts 预设音色名。此类名字在此丢弃（用模型默认），
        # 克隆 id（真正子里达妮娅的声音）不受影响、照传。
        _v = (voice or "").strip()
        _mdl_l = str(model or "").lower()
        if _v and ("omni" in _mdl_l and "realtime" in _mdl_l
                   and not _v.lower().startswith("qwen-omni-vc-")):
            logger.warning(
                "云端双工：voice=%r 是预设音色名，当前模型 %s 不支持（会 400 拒绝）"
                "→ 改用模型默认音色。要用自己的克隆声，请先上传参考音做音色克隆。",
                _v[:24], model)
            _v = ""
        self.voice = _v
        # 【2026-10-04】参考音频路径（达妮娅音色复刻用，对齐「启动云端达妮娅.bat」）
        self.ref_audio = ref_audio or ""
        self.enable_speech_emotion = enable_speech_emotion
        self.turn_detection = turn_detection
        self.vad_threshold = float(vad_threshold)
        self.vad_silence_ms = int(vad_silence_ms)
        self.instructions = instructions
        self.max_history_turns = int(max_history_turns)
        self._on_user_text = on_user_text
        self._on_assistant_text = on_assistant_text
        self._on_audio = on_audio
        self._on_user_audio = on_user_audio
        self._on_response_start = on_response_start
        self._on_status = on_status
        self._on_fatal = on_fatal
        self._auto_play = auto_play
        # 扬声器/麦克风设备名（来自 UI 下拉，'default' 或具体名）；非 default 时按名解析
        # 索引定向输出/采集。云端双工直接出声、不走本地 TTS，故设备选择必须由本播放器接管。
        self._speaker_device = speaker_device or ""
        self._mic_device = mic_device or ""
        self._greet_on_connect = bool(greet_on_connect)
        # 【2026-10-04·本地 VAD】云端 server_vad 在该模型/SDK 组合下反复不触发
        # speech_started（实测：上行电平 0.22、AGC 健康，却全程零 speech_started → AI 永不回应）。
        # 开启后禁用云端 server_vad，改为本地基于能量的 VAD：检测到说话结束即手动
        # commit + response.create，彻底绕开不可靠的云端 VAD。可用 DANYA_NO_LOCAL_VAD=1 关掉。
        self._local_vad = bool(local_vad) and (os.getenv("DANYA_NO_LOCAL_VAD", "") != "1")
        # 【2026-10-18 最终结论·本地 VAD 升为主判据】
        # 本轮 6 组真机对照实验（不同端点 / session 配置 / 文件音频 vs 实时麦克风）确认：
        #   · qwen3.8-omni-flash-realtime 的 server_vad **从不发 speech_started**
        #     （累计推流 60+ 秒真实语音，speech_* 事件数为 0）；
        #   · 但手动 commit + create_response 后，云端能正常识别并回语音
        #     （实测 response.audio.delta ×21）。
        # 所以本地 VAD 不再是"兜底"，而是**唯一可靠的主路径**。
        # 仍可用环境变量 DANYA_NO_LOCAL_VAD=1 关掉（回退纯云端 VAD）。
        if self._local_vad:
            logger.info("云端双工：本地能量 VAD 作为**主判据**"
                        "（该模型 server_vad 实测不触发；检测到句末即 commit）")
        # 本地 VAD 状态机
        self._responding = False      # 云端是否正在生成回复（避免说话结束时重复提交）
        self._vad_state = 0           # 0=idle 1=speaking
        self._vad_voiced = 0          # 连续"有声"块计数（起音防抖）
        self._vad_silent = 0          # 连续"静音"块计数（止音判定）
        self._vad_pending = False     # 本轮已采到用户语音、待提交
        self._vad_speech_start = 0.0  # 本句起音时刻（用于超时强制提交，防死流卡死）
        self._last_block_rms = 0.0    # 上一音频块的原始 RMS（VAD 用，绕开 AGC 归一化）
        # 音频/事件可观测计数（供诊断"接通没声"：区分"没收到音频"还是"播放没出声"）
        self._aud_bytes = 0
        self._aud_cnt = 0
        self._aud_log_ts = 0.0
        # 事件类型计数（诊断"接通没声"：零事件=云端没发；只读 _evt_seen 可判断会话是否活着）
        self._evt_counts: dict = {}
        self._evt_seen: set = set()
        self._evt_log_ts = 0.0
        # 开场白是否真出过声（8s 看门狗用；见 _maybe_greet）
        self._greet_audio_seen = 0
        # 【2026-10-04】自定义音色静默失效的自愈状态：只回落一次，避免反复重连
        self._voice_retried = False
        self._auto_reconnect_reason = ""
        # 【2026-10-04】软限幅应用次数 + 上行自适应增益（见 _soft_clip）
        self._softclip_applied = 0
        self._mic_gain = 1.0
        # 【2026-10-04 18:00】当前实际在用的麦克风（名字 + hostApi）；
        # 一旦云端 VAD 确认听到人声，就据此写入"已验证可用"记忆。
        self._active_mic_name: str = ""
        self._active_mic_api: int = -1
        # ⭐【2026-10-04 18:05 自愈看门狗】记忆也可能过期（换设备/驱动更新/系统隐私
        # 设置改动后，那条"已验证"的路由可能变哑）。故记录本次通话是否真的收到过
        # speech_started：若在 _VAD_WATCHDOG_S 内**一次都没触发**且期间上行电平
        # 明显非零（说明在拾音、只是云端不认），则判定"记忆失效"→ 清除记忆并在
        # 通话中重新按声学判据选路。判据用**客观事件**而非猜测。
        self._vad_triggered: bool = False
        self._mic_open_ts: float = 0.0
        # 【2026-10-18】采集子进程句柄 / 设备热切换标志。
        # 此前 `_mic_reopen_requested` 从未在 __init__ 里初始化（全靠 getattr 兜底），
        # 设备切换请求在"采集循环尚未起来"时会悄悄丢失且无日志。
        self._mic_proc = None
        self._mic_reopen_requested: bool = False
        self._collecting: bool = False
        # 【2026-10-04 18:20】UI 显示用電平（近期峰值 + 快回落），
        # 与 _mic_level（被静音稀释的平滑值）分开，避免波形条显示得比实际音量小。
        self._mic_disp: float = 0.0
        self.running = False
        self._conv = None
        self._player: Optional[_AudioPlayer] = None
        self._cur_response_id = None
        self._done_event = None  # 仅预览用：收到本轮结束事件时置位；主链路恒为 None
        self._connected = False
        self._fatal_close = False
        self._fatal_reason = ""
        self.enabled = bool(self.api_key and self.model)
        if not self.enabled:
            logger.warning("云端全双工未启用（api_key/model 为空）")

    # ---------- 会话配置 ----------
    def _turn_detection_dict(self):
        if not self.turn_detection or self.turn_detection == "null":
            return None
        if self.turn_detection == "smart_turn":
            return {"type": "smart_turn"}
        # 【2026-10-18 修复·云端 VAD 阈值过高导致"听不到我说话"】
        # 官方默认 threshold=0.5，但实测本机麦克风（立体声混缩后）语音 rms 仅
        # 0.01~0.05、峰值 0.45 —— 根本达不到 0.5 的门槛，云端 server_vad
        # 永远判不出"有人在说话"（实测 30 秒说话零 speech_started）。
        # 现降到 0.2（官方允许范围 [-1,1]，安静环境建议降低），并支持 config 覆盖。
        _th = float(getattr(self, "vad_threshold", 0.2) or 0.2)
        if _th >= 0.45:      # 旧的 0.5 默认值 → 强制降到 0.2
            _th = 0.2
        return {"type": "server_vad",
                "threshold": _th,
                "prefix_padding_ms": 300,
                "silence_duration_ms": int(getattr(self, "vad_silence_ms", 600) or 600)}

    # 【2026-10-17 修复·默认音色必须按模型族选】
    # 旧实现写死 _DEFAULT_VOICE="Cherry"，实测它只对少数模型有效：
    #   qwen3.8-omni-flash-realtime → 400 "Voice 'Cherry' is not supported"
    #   qwen-audio-3.1-realtime-plus → "Unsupported voice: 'Cherry'"
    #     （该系列只支持 longanqian / longanlingxin / longanlufeng … 这些 longan* 音色）
    # 两者都表现为"连接正常、会话建立，却零 audio.delta"的静默哑，极难定位。
    _AUDIO_MODEL_PREFIX = "qwen-audio-"
    _AUDIO_DEFAULT_VOICE = "longanqian"
    # 【2026-10-18】官方文档：qwen3.8-omni-flash-realtime 默认音色是 Tina。
    # 旧值 Chelsie 在该模型上会被 400 拒绝（"Voice 'Chelsie' is not supported"）。
    _OMNI_DEFAULT_VOICE = "Tina"

    def _default_voice_for_model(self) -> str:
        """用户没绑音色时，按**模型族**挑一个该模型支持的服务端默认音色。"""
        m = (self.model or "").strip().lower()
        if m.startswith(self._AUDIO_MODEL_PREFIX):
            return self._AUDIO_DEFAULT_VOICE
        return self._OMNI_DEFAULT_VOICE

    def _apply_session(self) -> None:
        """下发会话配置（官方 qwen3.8 Manual 模式格式）。

        【2026-10-18 重写·依据官方文档 + 真机实测】
        ① 端点：qwen3.8-omni-flash-realtime 必须用业务空间绑定端点
           wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime。
           通用端点 dashscope.aliyuncs.com 能建连、能 session.update，
           但**完全不处理音频**（推 6 秒真实语音 → 零 VAD/零转写/零音频，不报错）。
        ② 格式：用官方新格式 session.audio.input.format / session.audio.output.voice，
           兼容字段 input_audio_format / output_audio_format 保留但优先用新格式。
        ③ 轮次：该模型 server_vad 实测不触发；改用 **Manual 模式**
           （turn_detection=null），由本地能量 VAD 判定说话结束，
           再发 input_audio_buffer.commit + response.create。
           实测此路径 commit ✅ / AI 音频 ✅ 全通。
        ④ 音色：默认 Tina（官方文档）；自定义走 session.audio.output.voice。
           旧版 session.update 里带 voice 会被 400 拒绝，故一律放进 audio.output。
        """
        _v = (self.voice or "").strip()
        if not _v:
            _v = self._default_voice_for_model()
        # 【2026-10-18 修复·三处致命配置错误（全部经真机实测确认）】
        #  ① modalities 顺序：服务端只接受 ['text'] 或 ['audio','text']。
        #     写成 ['text','audio'] 会被拒：Invalid modalities。
        #  ② voice 不能为空：空串会被序列化成 "null" → 服务端报
        #     "Voice 'null' is not supported" 并**直接断开 WebSocket**。
        #     这正是"通话莫名掉线、要重连"的元凶之一。
        #  ③ 音频格式必须用 SDK 的 AudioFormat 常量（pcm16/16k/mono），
        #     手写 {"type":"pcm"} 与 SDK 内部编码不一致。
        # 结论：一律走 SDK 官方 update_session()，不再手写 session.update JSON。
        try:
            from dashscope.audio.qwen_omni import AudioFormat, MultiModality
            # 先算轮次检测意图（td 在后面手写分支才定义，这里必须先求值，
            # 否则会 UnboundLocalError → 整段 SDK 配置被跳过、回退手写 JSON）
            _want_server_vad = (str(self.turn_detection) not in ("null", "manual", ""))
            self._conv.update_session(
                output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
                voice=_v,
                input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                enable_input_audio_transcription=True,
                input_audio_transcription_model="qwen3-asr-flash-realtime",
                enable_turn_detection=bool(_want_server_vad),
                turn_detection_type="server_vad",
                turn_detection_threshold=float(getattr(self, "_vad_threshold", 0.2) or 0.2),
                turn_detection_silence_duration_ms=int(
                    getattr(self, "_vad_silence_ms", 600) or 600),
            )
            logger.info("云端双工：session 已下发（SDK update_session，voice=%r，"
                        "轮次检测=%s，阈值=%.2f）",
                        _v, "server_vad" if _want_server_vad else "Manual",
                        float(getattr(self, "_vad_threshold", 0.2) or 0.2))
            # instructions 单独补发（update_session 不带该参数）
            if self.instructions:
                try:
                    self._conv.send_raw(json.dumps({
                        "event_id": "sess_instr_%d" % int(time.time() * 1000),
                        "type": "session.update",
                        "session": {"instructions": self.instructions},
                    }))
                except Exception:  # noqa: BLE001
                    pass
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("云端双工：SDK update_session 失败，回退手写配置: %s", e)

        cfg = {
            # 顺序必须 audio 在前（服务端只认 ['text'] 与 ['audio','text']）
            "modalities": ["audio", "text"],
            "audio": {
                "input": {
                    "format": {
                        "type": "pcm",
                        "sample_rate": 16000,
                        "sample_format": "s16le",
                        "channels": 1,
                        "packing": "interleaved",
                        "channel_layout": "mono",
                    },
                },
                "output": {
                    # voice 绝不能为空（空→"null"→服务端报错并断连）
                    "voice": _v,
                    "format": {"type": "pcm", "sample_rate": 24000},
                },
            },
            "input_audio_transcription": {"model": "qwen3-asr-flash-realtime"},
        }
        # 轮次检测：Manual 模式（本地 VAD 负责起止），除非显式要求服务端 VAD
        td = self._turn_detection_dict()
        if td is not None and str(self.turn_detection) not in ("null", "manual", ""):
            cfg["turn_detection"] = td
            logger.info("云端双工：轮次检测=服务端 VAD（%s）", td.get("type"))
        else:
            cfg["turn_detection"] = None
            logger.info("云端双工：轮次检测=Manual（本地 VAD 判定说话结束→commit→create_response）")
        if self.instructions:
            cfg["instructions"] = self.instructions
        if self.enable_speech_emotion is not None:
            cfg["enable_speech_emotion"] = self.enable_speech_emotion
        try:
            self._conv.send_raw(json.dumps({
                "event_id": "sess_cfg_%d" % int(time.time() * 1000),
                "type": "session.update",
                "session": cfg,
            }))
            logger.info("云端双工：session 配置已下发（voice=%r，audio 新格式）", _v)
        except Exception as e:  # noqa: BLE001
            logger.warning("云端双工：session 配置下发失败（继续用默认会话）: %s", e)

    # ---------- 生命周期 ----------
    def connect(self) -> None:
        import dashscope
        from dashscope.audio.qwen_omni import (
            OmniRealtimeConversation, OmniRealtimeCallback)

        if self.api_key:
            dashscope.api_key = self.api_key
        self._player = _AudioPlayer(
            rate=24000, device=self._speaker_device) if self._auto_play else None
        # 【2026-10-02 新增】麦克风电平统计（供通话 UI 律动条，如实反映"采到没有"）
        self._mic_level = 0.0
        self._mic_peak = 0.0
        self._mic_voice = False
        self._mic_level_ts = 0.0
        self._mic_own_pa = None
        owner = self

        class _Cb(OmniRealtimeCallback):
            def on_open(innerself):  # noqa: N805
                owner._on_open()

            def on_close(innerself, *a):  # noqa: N805
                owner._on_close(a)

            def on_error(innerself, *a):  # noqa: N805
                owner._on_error(a)

            def on_event(innerself, response):  # noqa: N805
                owner._on_event(response)

        self._conv = OmniRealtimeConversation(
            model=self.model, callback=_Cb(), url=self.ws_url)
        self._conv.connect()
        self._apply_session()
        self._connected = True
        logger.info("云端全双工已连接: %s (voice=%s)",
                    self.model, self.voice or "<预设/默认>")
        # 【2026-10-04 修·真因定位】接通即让 AI 主动说一句开场白：
        # ① 陪伴体验（接通即出声）；② 作为播放链路的即时自检——若连开场白都听不到，
        #    说明是"播放没出声"（与 .bat 链路一致，可排除设备/播放器）；若听得到开场白
        #    但说话无回应，则锁定"麦克风上行没把声音送进云端"。任何异常都不影响通话。
        #
        # 【2026-10-04 修·开场白零音频】上一版在 _apply_session() 之后**同秒**立刻发
        # create_item/create_response，但 update_session 是异步生效的（服务端要回 ACK），
        # 实测云端收到触发却**一个 audio.delta 都没下发**（10-04 05:50:37~56 全程 0 字节）。
        # 改为延迟 0.6s 再发，让 session 配置（voice/output_modalities）先落地。
        if self._greet_on_connect:
            def _greet_later():
                import time as _t
                _t.sleep(0.6)
                try:
                    if self._connected:
                        self._maybe_greet()
                except Exception:  # noqa: BLE001
                    pass
            try:
                import threading as _th
                _th.Thread(target=_greet_later, daemon=True).start()
            except Exception:  # noqa: BLE001
                self._maybe_greet()

    def _maybe_greet(self) -> None:
        """接通后让 AI 主动说一句开场白。

        既是陪伴体验（接通即出声），也是播放链路的即时自检：若连开场白都听不到，
        说明是"播放没出声"（可排除设备/播放器，因 .bat 同链路能出声）；若听得到开场白
        但说话无回应，则锁定"麦克风上行没把声音送进云端"。任何异常都不影响通话。
        """
        try:
            prompt = ("（请现在用一句轻松自然的话主动开场，像是刚接通电话，十到二十字，"
                      "不要带括号或动作描写）")
            _sent = False
            for ctype in ("input_text", "text"):
                try:
                    self._conv.create_item({
                        "type": "message", "role": "user",
                        "content": [{"type": ctype, "text": prompt}],
                    })
                    _sent = True
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not _sent:
                logger.warning("云端双工开场白：create_item 两种字段均失败，跳过")
                return
            try:
                self._conv.create_response(
                                instructions=(self.instructions or None))
            except Exception as e:  # noqa: BLE001
                logger.debug("create_response 触发开场白失败（可忽略）: %s", e)
            logger.info("云端双工已发送开场白触发（请在 1~2 秒内留意是否听到声音）")
            # 【2026-10-04 增强】开场白"是否真出声"回执 + **自动修复**。
            # 实测教训（10-04 06:11）：麦克风修好后电平 0.7 正常上行，但云端
            # 全程只有 session.created、零 audio.delta → 根因是**专属音色 ID 失效**
            # （服务端接连接、不报错、不产音）。这类"静默哑"没有任何错误码可捕，
            # 唯一可靠判据就是"开场白有没有真的出声"。所以：
            #   有自定义音色 + 8s 零音频 → 自动改用模型默认音色并重连一次，
            #   宁可换个音色也不能让用户对着一个不响的通话干等。
            def _greet_watchdog():
                import time as _t
                _t.sleep(8.0)
                try:
                    if self._greet_audio_seen or not self._connected:
                        return
                    # 【2026-10-04 查证结论】这类"静默哑"只有三种根因，且都**不会报错**：
                    #   ① 账户层：阿里云欠费/额度耗尽。实测同 Key 下 /models 返回 200，
                    #      但声音复刻与 omni-realtime 接口返回 `Arrearage`
                    #      ("Access denied, please make sure your account is in good
                    #      standing") —— 连接能建、session.created 能回，但**永不产音**。
                    #   ② 模型层：模型名不被服务端支持（同样只回 session.created）。
                    #   ③ 音色层：复刻音色与模型不匹配。官方明确"复刻音色仅
                    #      Qwen3.5-Omni-Realtime 系列支持"，且复刻时的 target_model
                    #      必须与调用时的模型一致，否则合成失败（同样是静默）。
                    # 日志要能一眼区分这三者，否则每次都得重查一遍。
                    logger.error(
                        "云端双工静默哑（8 秒零 audio.delta）。已排除麦克风/播放器。"
                        "当前 model=%s voice=%s。三个可能根因："
                        "①账户欠费或额度耗尽（最常见，连接正常但不产音；"
                        "同 Key 的 /models 仍返回 200，极易被误判为软件 bug）；"
                        "②模型名不被支持；"
                        "③复刻音色与模型不匹配（复刻音色仅 qwen3.5-omni-*-realtime 系列支持，"
                        "且复刻 target_model 必须与调用模型一致）。"
                        "建议：先在阿里云控制台确认账户余额与 qwen-omni 实时语音是否已开通。",
                        self.model, self.voice or "<默认>")
                    if self._voice_retried:
                        return
                    if self.voice:
                        # 可能是音色与模型不匹配 → 回落默认音色重连一次
                        logger.error("带自定义音色仍零音频 → 自动改用模型默认音色重连一次")
                        self._voice_retried = True
                        self._voice = ""
                        self._auto_reconnect_reason = "自定义音色疑似与模型不匹配，已回落默认音色"
                        try:
                            self._reconnect()
                        except Exception as e:  # noqa: BLE001
                            logger.error("音色回落后自动重连失败: %s", e)
                    else:
                        logger.error(
                            "默认音色也零音频 → 根因在账户或模型层（非音色），"
                            "重连无意义。请检查阿里云账户余额/开通状态。")
                except Exception:  # noqa: BLE001
                    pass
            try:
                import threading as _th
                _th.Thread(target=_greet_watchdog, daemon=True).start()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            logger.debug("开场白触发失败（不影响通话）: %s", e)

    def send_audio(self, pcm16_bytes: bytes) -> bool:
        """把一块 16k/mono/int16 PCM 追加进云端输入缓冲。

        返回 True=确实送进 WebSocket 会话；False=未连接/空块（**静默丢弃**）。
        【2026-10-18】原返回 None，采集侧无法区分"送出去了"和"因为没连上被
        静默丢掉"——而后者正是"声音完全上传不上去"的一种典型形态，故补返回值。
        """
        if not self._connected or not pcm16_bytes:
            return False
        try:
            self._conv.append_audio(base64.b64encode(pcm16_bytes).decode())
            return True
        except Exception as e:  # noqa: BLE001
            # 【2026-10-17 修复·B·日志刷屏】此前**每帧**失败都打一条 warning，
            # 实测一通电话（socket 已死但 _connected 仍 True）刷了 **2123 条**，既淹没有效日志
            # 又白耗 CPU/磁盘。现在限流：第 1 条必打，之后每 10 秒最多 1 条，并带上累计次数。
            self._audio_fail_n = getattr(self, '_audio_fail_n', 0) + 1
            _now = time.time()
            if self._audio_fail_n == 1 or (_now - getattr(self, '_audio_fail_ts', 0.0)) >= 10.0:
                self._audio_fail_ts = _now
                logger.warning("append_audio 失败（累计 %d 次，已限流）: %s",
                               self._audio_fail_n, e)
            return False

    def set_speaker_device(self, speaker_device: str) -> None:
        """【2026-10-18 新增·通话中切换扬声器】热切换输出设备。

        【根因】云端双工的语音由内部 _AudioPlayer 直接播放，**不走上层 TTS**。
        而 webapp 的「通话设备切换：speaker → X」只调了 tts.set_output_device()，
        云端双工播放器从未收到通知 ⇒ 用户切了扬声器却毫无变化，仍是旧设备出声。
        这就是"扬声器切不了、切了没声音"的原因。

        实现：委托给播放器的 set_device()，它会更新设备名并重建输出流。
        """
        _dev = str(speaker_device or "").strip()
        if _dev.lower() == "default":
            _dev = ""
        logger.info("云端双工：切换扬声器设备 → %r", _dev or "系统默认")
        self._speaker_device = _dev
        _p = getattr(self, "_player", None)
        if _p is None:
            # 播放器还没建（未连接/auto_play=False）→ 记下名字，connect 时生效
            logger.info("云端双工：播放器尚未创建，设备名已记录，连接时生效")
            return
        try:
            ok = _p.set_device(_dev)
            logger.info("云端双工：扬声器切换%s", "已生效" if ok else "失败（已回退默认设备）")
        except Exception:  # noqa: BLE001
            logger.exception("云端双工：切换扬声器异常")

    def set_mic_device(self, mic_device: str) -> None:
        """热切换麦克风设备（设置页/通话 UI 切换时调用）。

        【2026-10-18 新增·"必须切多次设备才能听到我说话"的根因】
        云端双工此前**没有**这个方法，而 `proactive.apply_call_settings` 里写的是
        `if hasattr(client, "set_mic_device"): client.set_mic_device(...)` ——
        hasattr 恒 False ⇒ **静默跳过**，云端双工的麦克风流从未真正重开。
        用户切换设备后听到"好了"，其实是碰巧触发了看门狗重选/重连，
        所以表现为"要切好几次才行"。

        实现：置新设备名 + 打断当前采集循环 → 采集线程退出后用新设备重开。
        """
        _dev = str(mic_device or "").strip()
        logger.info("云端双工：切换麦克风设备 → %r（将重开采集流）", _dev)
        self._mic_device = _dev
        self._mic_reopen_requested = True
        # 【2026-10-18】把状态打出来：设备切换是否真的被采集循环接住，
        # 直接决定"切设备后有没有声音"。running/connected 任一为假时，
        # 置了 reopen 标志也不会有人消费 → 表现为"切了没反应"。
        logger.info('云端双工：切换麦克风设备 → running=%s connected=%s collecting=%s',
                    bool(self.running), bool(self._connected), bool(self._collecting))
        # 打断正在阻塞的 mic.read：关掉当前流，采集循环会捕获异常并走重开分支
        try:
            _m = getattr(self, "_mic_stream", None)
            if _m is not None:
                try:
                    _m.stop_stream()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    def set_call_mute(self, muted: bool) -> None:
        """通话禁音（与 local_omni_duplex 同接口）。

        【2026-10-18 新增】云端双工缺这个方法时，UI 的"静音"按钮点了没反应
        （proactive.set_call_mic 里 hasattr 判断为 False → 静默跳过）。
        静音实现：上行推送静音帧（保持云端 VAD 时序），而非直接丢弃。
        """
        self._call_mic_muted = bool(muted)
        logger.info("云端双工：通话麦克风%s", "已静音" if muted else "已恢复")

    def close(self) -> None:
        try:
            if self._conv:
                self._conv.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._player:
                self._player.stop()
        except Exception:  # noqa: BLE001
            pass
        self._connected = False

    # ---------- 生命周期（嵌入宿主双工循环用）----------
    def start(self) -> None:
        """非阻塞启动：起后台线程 connect + 麦克风推流；running 在连上后置 True。"""
        if self.running or self._connected:
            return
        import threading
        self._thread = threading.Thread(target=self._run_blocking, daemon=True)
        self._thread.start()


    def _run_blocking(self) -> None:
        """薄壳：连接云端后进入独立进程采集循环。"""
        _t0 = time.time()
        try:
            self.connect()
        except Exception as e:  # noqa: BLE001
            logger.error("云端全双工(Qwen-Omni)连接失败: %s", e)
            self.running = False
            self._connected = False
            self._collecting = False
            return
        logger.info('双工采集：云端已连接（connect 耗时 %.2fs，_connected=%s）→ 启动麦克风采集进程',
                    time.time() - _t0, bool(getattr(self, '_connected', False)))
        self.running = True
        try:
            self._subproc_capture_loop(None)
        finally:
            logger.info('双工采集：采集循环已退出（running=%s）', bool(self.running))

    def _subproc_capture_loop(self, pa_unused) -> None:
        """独立进程麦克风采集（微信/QQ 架构的关键部分）。

        【2026-10-18 最终架构】
        一周问题链的根：PortAudio 在主进程内对同一 USB 麦的多参数开流会
        互相污染（DirectSound 陈旧缓冲/立体声交错/静音路），且切换设备
        也无法恢复。独立进程 = 全新 PortAudio 状态，彻底免疫。
        本方法：
          · spawn src/audio/mic_proc.py 子进程（stdout=音频流）
          · 读块 → _track_mic_level → _last_block_rms → send_audio
            → _on_user_audio → _vad_update（与旧链路完全一致）
          · 子进程死/卡 → 自动重启（最多 5 次）
          · stop() 事件 → 杀子进程退出
        """
        import subprocess as _sp
        import struct as _struct
        here = os.path.dirname(os.path.abspath(__file__))
        proc_py = os.path.join(here, '..', 'audio', 'mic_proc.py')
        if not os.path.exists(proc_py):
            proc_py = os.path.join(here, 'audio', 'mic_proc.py')
        # 子进程诊断日志独立落盘（见 mic_proc.py 顶部注释：原先写 stderr 而主进程
        # 不读该管道，攒满 64KB 后子进程 write 阻塞 → 采集卡死 → 声音传不上去）。
        _log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'logs',
            'mic_proc_%s.log' % time.strftime('%Y%m%d_%H%M%S'))
        restarts = 0
        try:
            while self.running and restarts < 5:
                logger.info('双工采集：启动独立进程（第 %d 次）设备=%r 子进程日志=%s',
                            restarts + 1, self._mic_device or '', _log_path)
                try:
                    proc = _sp.Popen(
                        [sys.executable, '-X', 'utf8', '-B', proc_py,
                         self._mic_device or '', _log_path],
                        # stdout=音频流；stderr 丢弃（子进程诊断已改走日志文件，
                        # 保留这层是因为 Python 解释器级的致命错误仍可能写 stderr，
                        # 用 DEVNULL 确保永不会有人因"管道没人读"而阻塞）
                        stdout=_sp.PIPE, stderr=_sp.DEVNULL)
                except Exception as e:  # noqa: BLE001
                    logger.error('双工采集：子进程启动失败 %s', e)
                    time.sleep(2.0)
                    restarts += 1
                    continue
                self._mic_proc = proc
                self._mic_open_ts = time.time()
                self._vad_triggered = False
                # —— 本轮采集的块级统计（用于定位"这一通到底采到了什么"）——
                _cap_t0 = time.time()
                _blk_n = 0
                _blk_bytes = 0
                _blk_pk_all = 0
                _blk_silent = 0
                _blk_voice = 0
                _blk_q = 0               # 队列/管道积压估算（读取后剩余）
                _sent_n = 0
                _last_blk_ts = 0.0
                _last_report_ts = time.time()
                _stall_warned = False
                try:
                    while self.running:
                        if getattr(self, '_mic_reopen_requested', False):
                            logger.info('双工采集：设备切换 → 重启子进程')
                            break
                        # 卡死看门狗：主线程阻塞在 read 时也能靠超时线程发现
                        hdr = proc.stdout.read(4)
                        if len(hdr) < 4:
                            logger.warning('双工采集：子进程流断开（本轮收到 %d 块 / %.1fs）',
                                           _blk_n, time.time() - _cap_t0)
                            break
                        ln = _struct.unpack('<I', hdr)[0]
                        if ln <= 0 or ln > 200000:
                            logger.warning('双工采集：异常块长 %d（本轮已收 %d 块）', ln, _blk_n)
                            break
                        d = proc.stdout.read(ln)
                        if len(d) < ln:
                            logger.warning('双工采集：块数据不完整（%d/%d 字节）', len(d), ln)
                            break
                        _blk_n += 1
                        _blk_bytes += len(d)
                        _last_blk_ts = time.time()
                        if _blk_n == 1:
                            logger.info('双工采集：首个音频块已到达（%d 字节 ≈ %.0fms）'
                                        '，采集链路打通', len(d),
                                        1000.0 * len(d) / 2.0 / 16000.0)
                        if getattr(self, '_call_mic_muted', False):
                            d = b'\x00' * len(d)
                        self._track_mic_level(d)
                        try:
                            import array as _av_arr
                            _av = _av_arr.array('h')
                            _av.frombytes(d[: len(d) - (len(d) % 2)])
                            if _av:
                                _acc = 0.0
                                for _x in _av:
                                    _fv = _x / 32768.0
                                    _acc += _fv * _fv
                                self._last_block_rms = (_acc / len(_av)) ** 0.5
                        except Exception:  # noqa: BLE001
                            pass
                        # —— 块级统计（只统计不改音频）——
                        try:
                            _pk = 0
                            import array as _st_arr
                            _sa = _st_arr.array('h')
                            _sa.frombytes(d[: len(d) - (len(d) % 2)])
                            if _sa:
                                _pk = max(max(_sa), -min(_sa))
                            if _pk > _blk_pk_all:
                                _blk_pk_all = _pk
                            if _pk == 0:
                                _blk_silent += 1
                            if self._last_block_rms > 0.035:
                                _blk_voice += 1
                        except Exception:  # noqa: BLE001
                            pass
                        _sent_ok = self.send_audio(d)
                        _sent_n += 1
                        try:
                            if self._on_user_audio:
                                self._on_user_audio(d)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            self._vad_update()
                        except Exception:  # noqa: BLE001
                            pass
                        # —— 周期报告（每 5s 一行，回答"这一通到底采到了什么/送出去了吗"）——
                        _now_r = time.time()
                        if _now_r - _last_report_ts >= 5.0:
                            _dur = max(1e-6, _now_r - _cap_t0)
                            _conn = bool(getattr(self, '_connected', False))
                            # 块时长不固定（设备 44.1k→1600 采样/块 ≈100ms；16k 直采 ≈10ms），
                            # 故用「音频秒数 / 墙钟秒数」当健康度，≈1.0 才说明没丢时间。
                            _audio_s = _blk_bytes / 2.0 / 16000.0
                            logger.info(
                                '双工采集[STAT] 块=%d(%.1f块/s) 音频=%.1fs/墙钟%.1fs(实时比%.2f) '
                                '峰值=%d 全零块=%d 人声块=%d 发送成功=%d 连接=%s muted=%s '
                                'AGC=×%.2f 已运行=%.1fs',
                                _blk_n, _blk_n / _dur, _audio_s, _dur,
                                _audio_s / _dur,
                                _blk_pk_all, _blk_silent,
                                _blk_voice, _sent_n, _conn,
                                bool(getattr(self, '_call_mic_muted', False)),
                                float(getattr(self, '_mic_gain', 1.0) or 1.0),
                                _now_r - _cap_t0)
                            _last_report_ts = _now_r
                        # —— 上游卡死告警：有块进来但一直送不出去 ——
                        if (not _stall_warned and _blk_n >= 20
                                and _sent_n == 0 and not self._connected):
                            _stall_warned = True
                            logger.warning(
                                '双工采集：已采到 %d 块但未连接云端（send_audio 静默丢弃）'
                                '——声音采到了却传不上去', _blk_n)
                finally:
                    # —— 每轮采集汇总：这一轮到底采到了什么、有没有上行 ——
                    try:
                        _dur = max(1e-6, time.time() - _cap_t0)
                        _rate = _blk_n / _dur
                        _audio_s = _blk_bytes / 2.0 / 16000.0
                        _rt = _audio_s / _dur
                        _msg = ('设备=%r 块=%d(%.1f块/s) 音频=%.1fs/墙钟%.1fs'
                                '(实时比%.2f) 峰值=%d 全零块=%d 人声块=%d 发送=%d 子进程日志=%s') % (
                            getattr(self, '_mic_device', '') or '', _blk_n, _rate,
                            _audio_s, _dur, _rt,
                            _blk_pk_all, _blk_silent, _blk_voice, _sent_n,
                            os.path.basename(_log_path))
                        # 实时比 <0.7 = 采到的音频时长明显少于墙上时间：
                        # 读循环被阻塞 / 设备吞吐不足 / 子进程卡住（本次要抓的重点）。
                        if _rt < 0.7 and _blk_n > 0:
                            logger.warning('双工采集[汇总] ⚠ 音频时长不足实时（实时比 %.2f）：'
                                           '读循环被阻塞或设备吞吐不足 · %s', _rt, _msg)
                        elif _blk_pk_all == 0:
                            logger.warning('双工采集[汇总] ⚠ 全程全零音频（设备打开但无信号）：'
                                           '麦克风被静音/选错设备 · %s', _msg)
                        elif _blk_voice == 0 and _blk_n > 100:
                            logger.warning('双工采集[汇总] ⚠ 未出现人声块（峰值=%d 偏低）：'
                                           '电平过小或离麦太远 · %s', _blk_pk_all, _msg)
                        else:
                            logger.info('双工采集[汇总] %s', _msg)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                    self._mic_proc = None
                if getattr(self, '_mic_reopen_requested', False):
                    self._mic_reopen_requested = False
                    continue
                if self.running:
                    restarts += 1
                    time.sleep(1.0)
        except Exception:  # noqa: BLE001
            logger.exception('双工采集：独立进程模式异常')
            self.running = False
        finally:
            self._collecting = False
            try:
                _p = getattr(self, '_mic_proc', None)
                if _p is not None:
                    _p.kill()
            except Exception:  # noqa: BLE001
                pass

    def _track_mic_level(self, chunk) -> None:
        """按音频块算 RMS → 平滑 → 归一化电平（0..1），并做简易人声判定。

        chunk 为 16-bit PCM 单声道小端（pyaudio paInt16）。只做统计，不改音频内容。
        """
        import array
        import time as _t
        try:
            if not chunk:
                return
            a = array.array("h")
            a.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
            if not a:
                return
            acc = 0.0
            clipped = 0
            for x in a:
                _v = x / 32768.0
                acc += _v * _v
                # 【2026-10-04 新增·削波检测】实测麦克风信号被削平：同一段话里
                # 峰值只在 0.0038 / 0.5104 / 0.8225 三个值间跳变（真人声应是连续
                # 变化的波形）。削波会破坏云端 VAD 依赖的频谱特征 → 明明电平够高
                # 却判不出"有人在说话"。这里统计接近满量程(≥0.95)的采样占比。
                if _v > 0.95 or _v < -0.95:
                    clipped += 1
            rms = (acc / len(a)) ** 0.5
            _clip_ratio = clipped / float(len(a)) if a else 0.0
            if _clip_ratio > 0.005 and getattr(self, "_clip_warned", "") != "1":
                self._clip_warned = "1"
                logger.warning(
                    "云端双工：检测到麦克风输入削波（接近满量程采样占比 %.1f%%）——"
                    "通常是输入增益过高。已自动启用软件软限幅修复（见 _soft_clip）；"
                    "若仍听不到你说话，可把 Windows「麦克风」输入音量调到 50~70%%。",
                    _clip_ratio * 100)
            # 平滑（与本地一致的观感：起音快、回落稍慢）
            prev = self._mic_level
            self._mic_level = rms if rms > prev else (prev * 0.6 + rms * 0.4)
            # 【2026-10-04 18:20 修正·波形显示偏小】`_mic_level` 是"起音快、回落慢"的
            # 平滑值，而真人说话是断续的：一句话里只有 30~50% 的时间是发声，其余是静音。
            # 静音块 rms≈0.0001 每块只把 prev 拉低 40%，于是**说完一句后要 2 秒多
            # 才能降到底**；UI 轮询到的绝大多数都是这种"被静音稀释"的中间值
            # （实测日志：说话瞬间 0.1271，但 2s 快照里 90% 是 0.0000~0.0031）
            # ⇒ 波形条看起来一直很小，与你实际说话的音量不符。
            # 修法：另存一个"显示用"电平，取**近期峰值**并快速回落，
            # 让 UI 反映你真实说话时的音量（peak_hold 已是这个思路，但回落太慢、
            # 且 get_mic_level 返回的是 level 而非 peak）。
            if rms > self._mic_disp:
                self._mic_disp = rms
            else:
                # 回落快一些（约 0.35s 降到一半），保证句间能及时归零、不拖影
                self._mic_disp = self._mic_disp * 0.82 + rms * 0.18
            self._mic_peak = max(self._mic_peak * 0.95, self._mic_level)
            # 简易人声判定：RMS 超阈值且非持续极低（区分"静音"与"有人在说"）
            self._mic_voice = self._mic_level > 0.012
            self._mic_level_ts = _t.time()
        except Exception:  # noqa: BLE001
            # 统计失败绝不影响通话（只影响 UI 显示）
            pass

    # ---------- 本地 VAD（主链路默认开启，绕开云端不可靠的 server_vad） ----------
    def _vad_update(self) -> None:
        """本地能量 VAD —— 在本模型上**是主判据**（云端 server_vad 实测不触发）。

        【2026-10-18 真机结论·推翻此前"以云端为主"的假设】
        本轮做了 6 组对照实验（不同端点 / 不同 session 配置 / 文件音频 vs 实时麦克风），
        结论明确：
          · 云端 server_vad 在 qwen3.8-omni-flash-realtime 上**从不发 speech_started**
            （6 组实验、累计推流 60+ 秒真实语音，speech_* 事件数为 0）。
          · 但只要**手动 commit + create_response**，云端就能正常识别并回语音
            （实测 response.audio.delta ×21，AI 完整说了一句话）。
        所以本地 VAD 不能只是"兜底"，必须作为主路径：检测到一句说完就 commit。
        这也解释了用户长期反馈的"接通了但完全不理我"。

        防"死流卡死"：若麦克风数据停滞（逐块完全相同），本地 VAD 会永远判"在说话"
        而永不 commit。故加入**最长说话时长强制提交**（_VAD_MAX_SPEECH_S），
        即使电平一直不落，也会定期 commit，保证 AI 必定有机会回应。
        """
        if not self._local_vad:
            return
        # 【2026-10-18 三次调参】0.035 是按"正常系统音量"定的；实测弱麦即便
        # 经过 AGC 60 倍抬升，说话 RMS 也只有 ~0.02，永远够不着 → VAD 不触发
        # → AI 永不回应。AGC 上限已提到 120 倍，这里阈值同步降到 0.02，
        # 两层配合：AGC 负责把弱信号抬到正常量级，阈值放宽保证弱麦也能起音。
        # 误触发防护仍在：起音需连续 3 块超阈 + 止音需连续 7 块低电平。
        _SPEECH_ON = 0.02
        _SILENCE_BLOCKS = 7
        _VAD_MAX_SPEECH_S = 8.0      # 单句最长 8s，超时强制提交（防死流卡死）
        rms = self._last_block_rms
        _typing_ts = float(getattr(self, "_user_typing_ts", 0.0) or 0.0)
        _playing = False
        try:
            _playing = bool(self._player and getattr(self._player, "is_playing", lambda: False)())
        except Exception:  # noqa: BLE001
            _playing = False
        _on_threshold = _SPEECH_ON
        if _playing:
            _on_threshold = max(_on_threshold, _SPEECH_ON * 2.2)
        if _typing_ts and (time.time() - _typing_ts) < 3.0:
            _on_threshold = max(_on_threshold, 0.12)

        _now = time.time()
        if rms >= _on_threshold:
            self._vad_silent = 0
            self._vad_voiced += 1
            if self._vad_state == 0 and self._vad_voiced >= 3:
                self._vad_state = 1
                self._vad_pending = True
                self._vad_speech_start = _now
                logger.debug("本地 VAD：检测到说话开始 rms=%.3f", rms)
                if self._player:
                    self._player.flush()
                if self._responding and self._conv is not None:
                    try:
                        self._conv.cancel_response()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            self._vad_voiced = 0
            if self._vad_state == 1:
                self._vad_silent += 1
                if self._vad_silent >= _SILENCE_BLOCKS:
                    self._commit_turn("静音结束")
                    return
        # 超时强制提交：电平一直不落（死流/持续噪声）时也要给云端一个句号
        if (self._vad_state == 1 and self._vad_pending
                and getattr(self, "_vad_speech_start", 0.0)
                and (_now - self._vad_speech_start) >= _VAD_MAX_SPEECH_S):
            self._commit_turn("超时强制提交")

    def _commit_turn(self, reason: str = "") -> None:
        """本地 VAD 判定一句结束 → commit + create_response（实测唯一可靠路径）。"""
        self._vad_state = 0
        self._vad_silent = 0
        self._vad_speech_start = 0.0
        if not self._vad_pending:
            return
        self._vad_pending = False
        if self._conv is None:
            return
        try:
            self._conv.commit()
            try:
                self._conv.clear_appended_audio()
            except Exception:  # noqa: BLE001
                pass
            self._conv.create_response(instructions=(self.instructions or None))
            logger.info("本地 VAD 提交成功（%s）：已 commit + 请求回复", reason)
        except Exception as e:  # noqa: BLE001
            logger.warning("本地 VAD 提交失败（%s）: %s", reason, e)

    def _soft_clip(self, chunk: bytes) -> bytes:
        """上行音频自适应增益（**双向** AGC）+ 硬限幅：让云端**始终**拿到清晰、稳定的音量。

        【2026-10-04 演进·治"她说我说话声音小 / 听不清"】
        10-04 版只做**单向衰减**：有削波就把增益往下压，没有削波绝不往上调（增益上限锁死
        1.0）。两种情况下都会出问题：
          ① 用户**很大声**（老大了）且持续削波 → 增益被压到 0.7 左右长期不动，云端虽能听到，
             但人设 danya_call_prompt 写了「真没听清就直说没听清」→ 达妮娅回"你声音好小/没听清"。
          ② 用户音量**偏弱 / 被某路设备吃低** → 软件只能压不能抬，上行电平始终偏低 → 云端
             ASR 信噪比差 → 同样被判"听不清"，而用户觉得自己喊得很大声。
        实测（mic_during_call.wav，K20 idx=20）：说话块峰值 32768、RMS 0.626、削顶 16%，静音块
        削顶 0/1600 —— 即"队友听得清（游戏链路带 AGC/降噪），云端却听不清（原样送过高增益的
        削顶信号）"。

        现改为**双向 AGC（目标 RMS 归一化）**：
          · 以目标 RMS≈0.22 为基准，按"目标/实测RMS"算出期望增益；
          · 上限 G_MAX=3.0（偏小音量最多抬 3 倍，避免把底噪放大成轰鸣）；
          · 下限 G_MIN=0.35（偏大/削波音量最多压到 0.35，杜绝削顶）；
          · 攻击快（需压低→0.6/块）、释放慢（需抬升→0.12/块），增益平滑不抖；
          · 末端保留 32000 硬限幅作安全网（防瞬时过冲）。
        效果：无论用户吼还是悄声，云端拿到的都是**稳定、清晰、可识别**的语音 —— 既不摩斯密码
        （削顶消失）也不"听不清"（电平被拉到舒适区）。

        与用户诉求一致：**只调整幅度，绝不做滤波/降噪** —— 麦克风自带降噪完整保留。
        """
        # 对照开关：设 DANYA_NO_SOFTCLIP=1 可关闭（做 A/B 用）
        if os.environ.get("DANYA_NO_SOFTCLIP", "") == "1":
            return chunk
        try:
            import array as _array
            import math as _math
            a = _array.array("h")
            a.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
            if not a:
                return chunk
            n = float(len(a))
            # ① 测本块：峰值 + RMS + 削顶比例
            peak = 0
            acc = 0.0
            clipped = 0
            for x in a:
                v = x if x >= 0 else -x
                if v > peak:
                    peak = v
                acc += (x / 32768.0) ** 2
                if v >= 31900:
                    clipped += 1
            rms = _math.sqrt(acc / n) if n else 0.0
            self._last_block_rms = rms
            # ② 双向 AGC：目标 RMS 归一化（既能压削波、也能抬升偏低音量）
            _TARGET_RMS = 0.22
            _G_MAX = 3.0     # 最大抬升（偏小音量）
            _G_MIN = 0.35    # 最大压低（偏大/削波音量）
            _NOISE_FLOOR = 0.012  # 低于此视为静音/底噪，绝不抬升（治"放大噪声"也护住云端 VAD 起音判定）
            g = self._mic_gain
            if rms > 1e-4:
                desired = _TARGET_RMS / rms
                if rms < _NOISE_FLOOR:
                    # 静音/底噪：只压不抬（增益封顶 1.0），避免把噪声放大成轰鸣，
                    # 也避免"持续中电平"让云端 server_vad 起音判定失效。
                    desired = min(desired, 1.0)
                desired = _G_MAX if desired > _G_MAX else (_G_MIN if desired < _G_MIN else desired)
            else:
                # 近乎纯静音/底噪：抬升档位待命，但不对静音本身做有意义放大
                desired = 1.0
            # 增益平滑：需压低则快（防削顶过冲），需抬升则慢（避免音量忽大忽小）
            if desired < g:
                g = g + (desired - g) * 0.6
            else:
                g = g + (desired - g) * 0.12
            self._mic_gain = g
            # ③ 增益≈1 且无削顶 → 原样送，省 CPU
            if abs(g - 1.0) < 0.015:
                return chunk
            # ④ 应用增益 + 32000 硬限幅安全网
            for i, x in enumerate(a):
                v = int(x * g)
                a[i] = 32000 if v > 32000 else (-32000 if v < -32000 else v)
            self._softclip_applied += 1
            if self._softclip_applied in (1, 50, 200):
                logger.info("云端双工：上行双向 AGC（当前 ×%.2f，本块 RMS %.3f / 峰值 %d / 削顶 %.1f%%）——"
                            "软件只调幅度，麦克风自带降噪不受影响",
                            g, rms, peak, (clipped / n) * 100 if n else 0.0)
            return a.tobytes()
        except Exception:  # noqa: BLE001
            return chunk


    def get_mic_level(self) -> dict:
        """麦克风实时电平（供 webapp.get_call_mic_level → 通话 UI 律动条）。

        返回与 local_omni_duplex.get_mic_level 同构的字段：
        {active, muted, level, rms, voice, peak, ts}
        """
        _fresh = (self._mic_level_ts > 0) and ((time.time() - self._mic_level_ts) < 2.0)
        return {
            "active": bool(self.running and self._connected),
            "muted": False,  # 云端链路的静音由 set_call_mic 侧控制，不在此表示
            # 【2026-10-04 18:20】level 给"显示用"电平（近期峰值、快回落），
            # 而不是被静音稀释的平滑值 —— 否则波形条看起来一直很小。
            # rms 仍保留平滑后的真实瞬时值，供需要严谨数值的调用方使用。
            "level": round(float(self._mic_disp if _fresh else 0.0), 4),
            "rms": round(float(self._mic_level if _fresh else 0.0), 4),
            "voice": bool(self._mic_voice and _fresh),
            "peak": round(float(self._mic_peak if _fresh else 0.0), 4),
            "ts": float(self._mic_level_ts),
        }

    def stop(self) -> None:
        """停止：置 running=False 并关闭会话/播放器。"""
        # 【2026-10-18】先把 running 置 False —— 麦克风采集线程的 `while self.running`
        # 才会立刻退出；此前 close() 先跑完才置位，中间那一小段仍在采集并 send_audio。
        self.running = False
        self._collecting = False
        # 【2026-10-18】terminate 实测选路的专属实例（它是全新开的，必须销毁）
        try:
            _op = getattr(self, '_mic_own_pa', None)
            if _op is not None:
                _op.terminate()
                logger.info('云端双工：专属麦克风实例已销毁')
        except Exception:  # noqa: BLE001
            pass
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def _reconnect(self) -> None:
        """原地重连（保留 running/回调/麦克风设备，只换会话与音色）。

        【2026-10-04 新增】用于"自定义音色静默失效"的自愈：voice 置空后用默认音色
        重新建会话。见 _maybe_greet 的看门狗。不走 stop()——那会把 running 置 False
        导致宿主以为通话结束了；这里只重建底层会话，UI 的"通话中"状态保持不变。
        """
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
        self._greet_audio_seen = 0
        self._evt_counts = {}
        self._evt_seen = set()
        self._cur_response_id = None
        # 【2026-10-18】等旧采集线程退出（最多 2s），避免双流抢麦克风
        _old_th = getattr(self, "_thread", None)
        if _old_th is not None and _old_th.is_alive():
            _old_th.join(timeout=2.0)
        # 起新后台线程重跑 connect + 麦克风推流循环
        self._thread = threading.Thread(target=self._run_blocking, daemon=True)
        self._thread.start()
        logger.info("云端双工：已用默认音色自动重连（running 保持 %s）", self.running)

    # ---------- 事件 ----------
    def _on_open(self) -> None:
        logger.info("云端全双工 WebSocket 已打开")
        # 【2026-10-17 修复·A 的活锁护栏】这里**不再**无条件清零重连计数 ——
        # 之前的写法导致"连上即断"时永不触达上限：每轮 connect 一成功就把计数清零，
        # 于是 45 秒里重连 21 轮，`最多 3 次`形同虚设（实测日志：自动重连 21 次）。
        # 现在只记录"本次会话开始时间"，由 _on_close 判断"活得够久"才重置预算。
        self._connected_since = time.time()
        # 音频失败计数/时间戳照旧清零（配合 B 的限流），避免跨会话累积。
        self._audio_fail_n = 0
        self._audio_fail_ts = 0.0
        # 【2026-10-02 修·卡拨号】回报"已接通"：proactive._on_duplex_status 据此把
        # _duplex_call_ready 置 True，UI 才能从拨号振铃切到通话中。此前云端实时链路
        # 从不回报连接状态 → ready 永远 False → 前端一直卡拨号（但音频/字幕已在跑）。
        if self._on_status:
            try:
                self._on_status("会话建立（云端已连上），开始陪你")
            except Exception:  # noqa: BLE001
                logger.debug("on_status(open) 回报失败", exc_info=True)

    def _on_close(self, args) -> None:
        # 解析关闭码与原因（dashscope 回调传入 (code, reason) 元组）
        _code = None
        _reason = ""
        if isinstance(args, (tuple, list)):
            if len(args) >= 1:
                _code = args[0]
            if len(args) >= 2:
                _reason = args[1] or ""
        _reason_str = str(_reason).lower()
        # 【2026-10-04 修·1007 风暴】这些关闭属于「服务端/账户层面致命拒绝」，重连
        # 多少次都没用，必须立即标记并停止；否则 run_loop 每 0.3s 重连一次，刷爆日志
        # 且一直回报"已连上"假象。常见根因：阿里云账户欠费 / 免费额度用尽 / 未开通
        # qwen-omni 实时语音 / 未完成实名（日志样例：1007 'Access denied, please make
        # sure your account is in good standing.'）。
        _fatal_kw = ("access denied", "not in good standing", "forbidden",
                     "unauthorized", "quota", "exceeded", "403", "401",
                     "not allowed", "billing", "suspended")
        _fatal = (_code in (1007, 1008, 1011, 1013, 1015)) or any(
            k in _reason_str for k in _fatal_kw)
        if _fatal:
            self._fatal_close = True
            self._fatal_reason = _reason or str(_code)
            self._connected = False
            self.running = False
            logger.error("云端全双工被服务端致命拒绝（code=%s, reason=%s），停止重连",
                         _code, _reason)
            if self._on_fatal:
                try:
                    self._on_fatal(self._fatal_reason)
                except Exception:  # noqa: BLE001
                    logger.debug("on_fatal 回调异常", exc_info=True)
        logger.info("云端全双工 WebSocket 关闭: %s", args)
        if not _fatal:
            # 【2026-10-17 修复·A·"接通即哑"】非致命关闭（实测样例：'Invalid close frame.'、
            # code=None）此前**只打一句日志就结束**：_connected 仍为 True → 宿主继续 append_audio
            # （实测一通电话刷了 2123 条失败），而且**永不重连** → 这一通永久没声音。
            # 现在：立即标记断开 + 有上限地自动重连（1s / 2s / 4s，最多 3 次），状态如实回报 UI。
            self._connected = False
            if self.running and not getattr(self, '_fatal_close', False):
                # 【2026-10-17 修复·A 的活锁护栏】只有"这次会话活得够久（≥5s）"才算一次
                # 独立的新问题，才重置重试预算；否则（连上即断）计数持续累加，
                # 到 3 次就真的停下，不再无限重连刷日志。
                _alive = time.time() - float(getattr(self, '_connected_since', 0.0) or 0.0)
                if _alive >= 5.0:
                    self._close_reconnects = 0
                self._close_reconnects = getattr(self, '_close_reconnects', 0) + 1
                _n = self._close_reconnects
                if _n <= 3:
                    _delay = min(4, 2 ** (_n - 1))
                    logger.warning('云端双工连接意外关闭（%s）→ %.0fs 后自动重连（第 %d/3 次）',
                                   args, _delay, _n)
                    if self._on_status:
                        try:
                            self._on_status('云端连接断开，正在重连（第 %d 次）…' % _n)
                        except Exception:  # noqa: BLE001
                            logger.debug("on_status(reconnect) 回报失败", exc_info=True)

                    def _retry_connect():
                        import time as _t
                        _t.sleep(_delay)
                        try:
                            self._reconnect()
                        except Exception as e:  # noqa: BLE001
                            logger.error('云端双工自动重连失败: %s', e)
                    try:
                        import threading as _th
                        _th.Thread(target=_retry_connect, daemon=True).start()
                    except Exception:  # noqa: BLE001
                        logger.debug("自动重连线程启动失败", exc_info=True)
                else:
                    logger.error('云端双工连接反复断开（已重连 %d 次）→ 停止重试；'
                                 '请检查网络 / 账户额度 / 音色有效性后重新拨号', _n - 1)
                    if self._on_status:
                        try:
                            self._on_status('云端连接反复断开，已停止重连（可重新拨号）')
                        except Exception:  # noqa: BLE001
                            pass
        if self._on_status:
            try:
                self._on_status("云端双工连接已关闭")
            except Exception:  # noqa: BLE001
                logger.debug("on_status(close) 回报失败", exc_info=True)

    def _on_error(self, args) -> None:
        logger.error("云端全双工错误: %s", args)
        if self._on_status:
            try:
                self._on_status("云端双工错误: %s" % (args,))
            except Exception:  # noqa: BLE001
                logger.debug("on_status(error) 回报失败", exc_info=True)

    def _on_event(self, response) -> None:
        if not isinstance(response, dict):
            logger.debug("云端双工事件非 dict，已忽略: %r", type(response))
            return
        t = response.get("type", "")
        # 诊断用：打出每个事件类型，定位"接通没声"到底卡在哪一层
        # （无事件=云端没发；有 audio.delta 但没声=播放器；有 transcript 但没声=同上）
        # 【2026-10-04 修正】原来是 logger.debug，而运行日志是 INFO 级 → 探针全被丢弃，
        # 导致"零事件"和"事件被过滤"无法区分。改为 INFO + 按类型去重计数（每秒最多一条），
        # 既有可观测性又不会刷屏。
        now = time.time()
        if now - self._evt_log_ts >= 1.0:
            self._evt_counts[t] = self._evt_counts.get(t, 0) + 1
            logger.info("云端双工事件 type=%s（累计 %d）", t, self._evt_counts[t])
            self._evt_log_ts = now
        else:
            self._evt_counts[t] = self._evt_counts.get(t, 0) + 1
            if t not in self._evt_seen:
                logger.info("云端双工事件 type=%s（首次）", t)
                self._evt_seen.add(t)
        # 【2026-10-04 17:45 诊断】打印 session.updated 完整回包：确认服务端**实际生效**的
        # turn_detection 是什么（是否真为 server_vad、threshold 多少）。此前只记事件类型，
        # 看不到回包内容 → 无法区分"没传下去"和"传了但不管用"，是本次排查的最大盲区。
        if t in ("session.updated", "session.created"):
            try:
                _s = response.get("session") if isinstance(response.get("session"), dict) else response
                logger.info("云端双工 %s 完整回包: %s", t,
                            json.dumps(_s, ensure_ascii=False)[:1200])
            except Exception:  # noqa: BLE001
                logger.info("云端双工 %s 完整回包: %s", t, repr(response)[:1200])
        if t == "response.audio.delta":
            self._handle_audio_delta(response)
        elif t == "error":
            # 【2026-10-04 新增】服务端错误事件此前被静默丢弃 → 出现"静默哑"时
            # 日志里什么都看不到，只能靠猜。实测最常见的一条：
            #   Voice 'null' is not supported  → SDK 把空 voice 发成字符串 "null"
            #   Voice 'xxx' is not supported  → 音色与模型不匹配/音色不存在
            #   The voice xxx could not be found → 该 Key 所属账号下没有这个复刻音色
            _e = response.get("error") or {}
            logger.error("云端双工收到服务端错误：code=%s message=%s full=%s",
                         _e.get("code"), _e.get("message"), _e)
        elif t == "conversation.item.input_audio_transcription.completed":
            txt = response.get("transcript", "")
            if txt and self._on_user_text:
                try:
                    self._on_user_text(txt)
                except Exception:  # noqa: BLE001
                    logger.debug("on_user_text err", exc_info=True)
            logger.info("[用户] %s", txt)
        elif t == "response.audio_transcript.done":
            txt = response.get("transcript", "")
            if txt and self._on_assistant_text:
                try:
                    self._on_assistant_text(txt)
                except Exception:  # noqa: BLE001
                    logger.debug("on_assistant_text err", exc_info=True)
            logger.info("[助手] %s", txt)
        elif t in ("response.created", "response.started"):
            rid = response.get("response_id") or response.get("id")
            if rid and rid != self._cur_response_id:
                was_none = self._cur_response_id is None
                self._cur_response_id = rid
                if (not was_none) and self._player:
                    self._player.flush()  # 新一轮回复开始 -> 丢弃上一轮未播音频（打断）
            # 云端开始生成回复（无论 server_vad 还是本地 VAD 触发的 create_response）
            self._responding = True
        elif t == "input_audio_buffer.speech_started":
            # 【2026-10-18】记录云端最后一次给出 VAD/转写事件的时间，
            # 供本地能量 VAD 判断"云端是否失灵、要不要兜底提交"。
            self._cloud_vad_last_ts = time.time()
            # 用户开始说话，立即丢弃尚未播放的 AI 音频（本地侧即时打断）
            logger.info("云端双工：检测到你开始说话（VAD 触发，本地即时打断）")
            # 【2026-10-04 18:00】这是唯一的**客观证据**：云端 VAD 判定用户开口，
            # 说明当前这条麦克风真的采到了人声。把它写进"已验证可用"记忆，
            # 下次拨号直接复用，彻底摆脱"探测时用户恰好没说话"的困境。
            try:
                self._vad_triggered = True   # 看门狗用：这路麦克风已被证明能采到人声
                remember_verified_mic(getattr(self, "_active_mic_name", ""),
                                      getattr(self, "_active_mic_api", -1))
            except Exception:  # noqa: BLE001
                pass
            self._responding = False  # 用户开口即打断 AI 当前回复
            if self._player:
                self._player.flush()
        elif t == "input_audio_buffer.speech_stopped":
            logger.info("云端双工：检测到你说完（VAD 停止，等模型生成回复）")
        elif t == "input_audio_buffer.committed":
            # 一段语音已提交给模型 → 这是"云端确实听到了你"的铁证
            logger.info("云端双工：一段语音已提交给模型（云端确认听到你说话）")
        elif t in ("response.done", "response.audio.done"):
            # 云端本轮回复结束 → 解除"正在生成"锁，允许本地 VAD 提交下一轮
            self._responding = False
            # 预览用：本轮回复合成完毕（无音频产出时也靠它结束等待）
            if self._done_event:
                self._done_event.set()

    def _handle_audio_delta(self, response) -> None:
        delta = response.get("delta", "")
        if not delta:
            return
        rid = response.get("response_id") or response.get("item_id")
        if rid:
            if self._cur_response_id is None:
                # 第一轮回复：仅记录 id，无需丢弃（本来就没缓冲）
                self._cur_response_id = rid
            elif rid != self._cur_response_id:
                # 新一轮回复开始：丢弃上一轮尚未播放的音频（实现打断）
                self._cur_response_id = rid
                if self._player:
                    self._player.flush()
                # 【2026-10-04 新增】通知宿主"新一轮助手音频开始"，使其把下行音频
                # 按轮分段存档（避免所有助手语音拼成一个超长文件）。
                try:
                    if self._on_response_start:
                        self._on_response_start()
                except Exception:  # noqa: BLE001
                    pass
        audio = base64.b64decode(delta)
        self._greet_audio_seen += 1
        # 【2026-10-04 诊断】累计音频字节，每秒打一条，确认"云端确实在下发音频"
        # （有这条日志但听不到声 = 播放器/设备问题；没有这条 = 云端根本没发，问题在上行/会话）
        try:
            self._aud_bytes += len(audio)
            self._aud_cnt += 1
            _now = time.time()
            if _now - self._aud_log_ts >= 1.0:
                logger.info("云端双工[播放] 收到音频 %d 字节 / %d 分片（已送入播放器队列）",
                            self._aud_bytes, self._aud_cnt)
                self._aud_log_ts = _now
                self._aud_bytes = 0
                self._aud_cnt = 0
        except Exception:  # noqa: BLE001
            pass
        if self._on_audio:
            try:
                self._on_audio(audio)
            except Exception:  # noqa: BLE001
                logger.debug("on_audio err", exc_info=True)
        if self._auto_play and self._player:
            self._player.play(audio)

    # ---------- 配置构造 ----------
    @classmethod
    def from_config(cls, cfg, repo_root: str = "") -> "CloudOmniRealtime":
        return _build(cls, cfg, repo_root)


def _auto_clone_ref_voice(model: str, api_key: str, base_url: str,
                          ref_audio: str, preferred_name: str = "danya") -> str:
    """用参考音频向阿里云**实时复刻**一个音色，返回新 voice_id（失败返回 ""）。

    【2026-10-04 新增·对齐「启动云端达妮娅.bat」好链路】
    那个启动器从不要求用户手填音色 ID：它读 `cloud_omni.ref_audio`
    （默认 H:\\daniya_ref.mp3）→ 调 `tools/clone_danya_voice.clone()` 现场复刻
    → 把返回的 voice_id 写回 config 再建会话。主程序此前只认 config 里写死的
    voice，**换 Key 后那个 ID 在新账号下并不存在**（服务端报 "voice could not be
    found"），于是双工静默哑。现在主程序对齐该行为：优先用参考音频复刻。

    关键约束（官方文档）：
      · 复刻的 `target_model` **必须与调用 Omni 的模型一致**；
      · 复刻音色**仅 qwen3.5-omni-*-realtime 系列支持**（3.0/3 系列会返回
        `No pipeline for: <model>`）。
    因此这里对非 3.5 系列模型直接跳过，避免无谓的失败请求。
    """
    try:
        if not (api_key and ref_audio and os.path.exists(ref_audio)):
            return ""
        # 【2026-10-04 18:55 放宽】原来只认 `"3.5-omni" in model` 这一种写法。
        # 阿里云后续若把型号写成 `qwen3.5-omni-flash-realtime-2025xxxx`（带日期快照）
        # 或换用 `qwen3.5-omni` 之外的 3.5 系别名，会被误判为"不支持复刻"而跳过
        # → 拿不到音色 → 通话哑。改为**宽松匹配 3.5 家族**（3.5 + omni 两个词都在
        # 就算），拿不准时宁可试一次（失败会返回空并回退预设音色，不会更差）。
        _ms = str(model or "").lower()
        _is_35_family = ("3.5" in _ms and "omni" in _ms)
        if not _is_35_family:
            logger.info("云端双工：模型 %s 不在 qwen3.5-omni 家族（复刻音色仅该家族支持），"
                        "跳过自动复刻", model)
            return ""
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "tools", "clone_danya_voice.py")
        if not os.path.exists(p):
            logger.warning("云端双工：找不到复刻工具 %s，跳过", p)
            return ""
        spec = importlib.util.spec_from_file_location("_danya_clone", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        voice = mod.clone(ref_audio, preferred_name, api_key, model, base_url)
        logger.info("云端双工：已用参考音频复刻达妮娅音色 → %s", voice)
        return voice or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("云端双工：参考音频复刻失败（将回退预设音色）: %s", e)
        return ""


def _build(cls, cfg, repo_root: str = "") -> "CloudOmniRealtime":
    d: dict = {}
    try:
        co = _get(cfg, "cloud_omni")
        if co is not None:
            for k in ("model", "api_key", "base_url", "ws_url", "voice",
                      "turn_detection", "workspace_id", "workspace_region"):
                v = _get(co, k, None)
                if v is not None:
                    d[k] = v
            d["ref_audio"] = _get(co, "ref_audio", "") or ""
            d["enable_speech_emotion"] = bool(_get(co, "enable_speech_emotion", True))
            d["vad_threshold"] = float(_get(co, "vad_threshold", 0.5) or 0.5)
            d["vad_silence_ms"] = int(_get(co, "vad_silence_ms", 600) or 600)
            d["max_history_turns"] = int(_get(co, "max_history_turns", 20) or 20)

            def _abs(p: str) -> str:
                if not p:
                    return ""
                return p if os.path.isabs(p) else os.path.join(repo_root, p)

            pf = _get(co, "persona_file", "") or ""
            bf = _get(co, "behavior_file", "") or ""
            persona = _read(_abs(pf) or _abs("prompts/call_profiles/danya_persona.txt"))
            behavior = _read(_abs(bf) or _abs("prompts/call_profiles/danya_behavior.txt"))
            parts = [p for p in (persona, behavior, _DEFAULT_RULES) if p]
            d["instructions"] = "\n\n".join(parts)

            # 【2026-10-04】自动补音色：换 Key / 换号后 config 里的旧 voice_id
            # 在新账号下并不存在（服务端报 "voice could not be found" → 静默哑）。
            # 这里对齐「启动云端达妮娅.bat」：用 ref_audio 现场复刻一个，并把结果
            # 写回 config，避免每次启动都重��克隆。复刻失败不阻断通话（回退预设音色）。
            if os.environ.get("DANYA_SKIP_AUTOCLONE", "") != "1":
                _ref = d.get("ref_audio") or ""
                if _ref and not os.path.isabs(_ref):
                    _ref = os.path.join(repo_root, _ref)
                _voice = str(d.get("voice") or "").strip()
                _model_s = str(d.get("model") or "").strip()
                # 【2026-10-04 18:55 破耦合·换模型不再静默哑】
                # 旧判断只看 `not _voice or voice.startswith("qwen-omni-vc-")`，
                # 于是**换模型 / 换厂商**时：若 config 里留的是别家前缀的音色
                # （或一个在新账号下已不存在的旧 voice_id），`_need=False` → 不复刻 →
                # 带着一个服务端不认的 voice 建会话 → 报 `voice could not be found`
                # 或直接零 audio.delta（**静默哑，最难查**）。
                # 现在额外判断"音色是否明显属于别的模型家族"，不属于就作废重置，
                # 让自动复刻/预设音色兜底接管。
                _stale_voice = bool(_voice) and (
                    # 非阿里云 omni 家族前缀的第三方音色（如 gpt-/cosyvoice-/自定义）
                    not _voice.startswith("qwen-omni")
                    and not _voice.startswith("qwen-")
                    # 3.5 系列只认 qwen-omni-vc- 复刻音色
                    and "3.5-omni" in _model_s
                )
                if _stale_voice:
                    logger.info("云端双工：音色 %r 与当前模型 %r 不属同一家族，"
                                "已作废以便自动复刻/回退预设音色", _voice, _model_s)
                    d["voice"] = ""
                    _voice = ""
                _need = bool(_ref) and os.path.exists(_ref) and (
                    not _voice or _voice.startswith("qwen-omni-vc-"))
                if _need and d.get("api_key") and d.get("model"):
                    _new = _auto_clone_ref_voice(
                        str(d.get("model") or ""), str(d.get("api_key") or ""),
                        str(d.get("base_url") or "https://dashscope.aliyuncs.com"),
                        _ref)
                    if _new and _new != _voice:
                        d["voice"] = _new
                        try:
                            _fp = _cfg_path(cfg)
                            if _fp and os.path.exists(_fp):
                                _raw = open(_fp, encoding="utf-8").read()
                                import re as _re
                                _raw2 = _re.sub(
                                    r"(\n  voice:\s*)" + _re.escape(_voice) + r"(\s*\n)",
                                    lambda mm: mm.group(1) + _new + mm.group(2), _raw, count=1)
                                if _raw2 != _raw:
                                    open(_fp, "w", encoding="utf-8",
                                         newline="").write(_raw2)
                                    logger.info("云端双工：新复刻音色已写回 %s", _fp)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("云端双工：音色写回 config 失败（不影响本次通话）: %s", e)
    except Exception:  # noqa: BLE001
        pass
    return cls(**d)


def preview_voice_once(
    text: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    ws_url: str = "",
    voice: str = "",
    timeout_s: float = 20.0,
) -> dict:
    """一次性云端音色试听：文本进 → 云端 S2S 合成 → 本地播放 → 关闭。

    与正式通话链路完全隔离：单独建会话、turn_detection=null（不等人说话）、
    用完即关，不触碰运行时 duplex 实例，也不会产生任何对话历史。
    用于引导页/设置页的「云端试听」按钮——让用户在真正使用前先听到这个声音。

    返回 {success, error, bytes, elapsed_ms}。bytes 为收到的 PCM 总字节数。
    """
    import threading
    import time

    text = (text or "").strip()
    if not text:
        return {"success": False, "error": "试听文本为空", "bytes": 0, "elapsed_ms": 0}
    if not (api_key and model):
        return {"success": False,
                "error": "未配置云端双工（cloud_omni.api_key / model 为空）",
                "bytes": 0, "elapsed_ms": 0}

    done = threading.Event()
    chunks: list = []
    err: list = []

    def _collect(b: bytes) -> None:
        chunks.append(b)

    eng = CloudOmniRealtime(
        model=model, api_key=api_key, base_url=base_url, ws_url=ws_url,
        voice=voice, turn_detection="null", on_audio=_collect,
    )
    eng._done_event = done
    t0 = time.time()
    try:
        eng.connect()
        # 文本输入项驱动一次回复：不同版本 SDK 对文本字段命名不一致，逐个尝试
        last: Optional[Exception] = None
        sent = False
        for field in ("input_text", "text"):
            try:
                eng._conv.create_item({
                    "type": "message",
                    "role": "user",
                    "content": [{"type": field, "text": text}],
                })
                sent = True
                break
            except Exception as e:  # noqa: BLE001
                last = e
        if not sent:
            return {"success": False,
                    "error": "发送试听文本失败：%s" % (last,),
                    "bytes": 0, "elapsed_ms": int((time.time() - t0) * 1000)}
        eng._conv.create_response()
        done.wait(timeout=float(timeout_s))
    except Exception as e:  # noqa: BLE001
        logger.warning("云端试听失败: %s", e)
        return {"success": False, "error": "%s: %s" % (type(e).__name__, e),
                "bytes": sum(len(c) for c in chunks),
                "elapsed_ms": int((time.time() - t0) * 1000)}
    finally:
        try:
            eng.close()
        except Exception:  # noqa: BLE001
            pass

    total = sum(len(c) for c in chunks)
    return {
        "success": total > 0,
        "error": "" if total > 0 else "未收到音频（超时或服务端无输出）",
        "bytes": total,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
