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
import logging
import threading
import queue
from typing import Optional, Callable

from ..perception._pa_shared import get_shared_pyaudio, pa_int16  # 共享 PortAudio 单例 + 模块级常量
from ..queue_util import put_drop_oldest

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


def forget_verified_mic(device_name: str) -> None:
    """把"这条麦克风已验证可用"的记忆删掉（自愈看门狗判定记忆失效时调用）。"""
    k = _verified_key(str(device_name or "").strip().lower())
    if not k or k not in _verified_api:
        return
    try:
        _verified_api.pop(k, None)
        os.makedirs(os.path.dirname(_VERIFIED_MIC_FILE), exist_ok=True)
        with open(_VERIFIED_MIC_FILE, "w", encoding="utf-8") as f:
            json.dump(_verified_api, f, ensure_ascii=False, indent=2)
        logger.warning("双工：麦克风 %r 的记忆已失效并清除，下次将重新按声学判据选择",
                       device_name)
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
                                logger.info("双工：设备 %r 沿用上次已验证可用的麦克风 "
                                            "idx=%s（hostApi=%s，跳过现场探测）",
                                            name, i, api_want)
                                return i
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
            short = {}
            alive = []
            for i in cands:
                m = _probe_input_speech(pa, i, tries=25)
                short[i] = m
                if m["rms"] >= _ABS_TH_MIN and m["voice_ratio"] >= 45.0:
                    alive.append(i)
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
            return cands[0]
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

    def flush(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._stop.set()
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
        local_vad: bool = False,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = (base_url or "").rstrip("/")
        self.ws_url = ws_url or (self.base_url.replace(
            "https://", "wss://") + "/api-ws/v1/realtime")
        self.voice = voice or ""
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
        if self._local_vad:
            # 禁用云端 server_vad（否则会与本地手动 commit 双提交）
            self.turn_detection = "null"
        # 本地 VAD 状态机
        self._responding = False      # 云端是否正在生成回复（避免说话结束时重复提交）
        self._vad_state = 0           # 0=idle 1=speaking
        self._vad_voiced = 0          # 连续"有声"块计数（起音防抖）
        self._vad_silent = 0          # 连续"静音"块计数（止音判定）
        self._vad_pending = False     # 本轮已采到用户语音、待提交
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
        return {"type": "server_vad",
                "threshold": self.vad_threshold,
                "silence_duration_ms": self.vad_silence_ms}

    # 【2026-10-17 修复·默认音色必须按模型族选】
    # 旧实现写死 _DEFAULT_VOICE="Cherry"，实测它只对少数模型有效：
    #   qwen3.8-omni-flash-realtime → 400 "Voice 'Cherry' is not supported"
    #   qwen-audio-3.1-realtime-plus → "Unsupported voice: 'Cherry'"
    #     （该系列只支持 longanqian / longanlingxin / longanlufeng … 这些 longan* 音色）
    # 两者都表现为"连接正常、会话建立，却零 audio.delta"的静默哑，极难定位。
    _AUDIO_MODEL_PREFIX = "qwen-audio-"
    _AUDIO_DEFAULT_VOICE = "longanqian"
    _OMNI_DEFAULT_VOICE = "Chelsie"

    def _default_voice_for_model(self) -> str:
        """用户没绑音色时，按**模型族**挑一个该模型支持的服务端默认音色。"""
        m = (self.model or "").strip().lower()
        if m.startswith(self._AUDIO_MODEL_PREFIX):
            return self._AUDIO_DEFAULT_VOICE
        return self._OMNI_DEFAULT_VOICE

    def _apply_session(self) -> None:
        from dashscope.audio.qwen_omni import MultiModality
        kwargs = {"output_modalities": [MultiModality.AUDIO, MultiModality.TEXT]}
        # 【2026-10-04 关键修复·SDK 会把空 voice 变成字符串 "null"】
        # dashscope SDK 的 update_session(voice=None) 在组装请求时会把 voice 序列化成
        # 字符串 "null" 发给服务端，服务端直接拒绝：
        #   {"type":"error","error":{"code":"COMMON_ERROR",
        #    "message":"<400> InternalError.Algo.InvalidParameter: Voice 'null'
        #               is not supported."}}
        # 表现＝连接正常、session.created 正常、但**永远零 audio.delta**（静默哑），
        # 极难定位。原代码 `if self.voice:` 会放过 None → 正好踩中这个坑。
        # 现在：voice 为空一律回退到模型默认预设音色，绝不发空。
        _v = (self.voice or "").strip()
        if not _v:
            _v = self._default_voice_for_model()
            logger.warning("云端双工未配置音色，回退到 %r（按模型族选择；"
                           "SDK 把空 voice 发成字符串 'null' 会被服务端拒绝）", _v)
        kwargs["voice"] = _v
        if self.instructions:
            kwargs["instructions"] = self.instructions
        if self.enable_speech_emotion is not None:
            kwargs["enable_speech_emotion"] = self.enable_speech_emotion
        td = self._turn_detection_dict()
        if td is not None:
            kwargs["turn_detection"] = td
        else:
            # turn_detection=null（预览场景）：必须显式关掉服务端 VAD，
            # 否则 SDK 默认仍是 server_vad，会一直等「用户说话」才出声。
            kwargs["enable_turn_detection"] = False
        try:
            self._conv.update_session(**kwargs)
        except TypeError as e:
            # SDK 版本差异可能不接受部分 kwarg；回退到最小可用配置
            logger.warning("update_session 拒绝部分参数(%s)，回退最小配置", e)
            self._conv.update_session(
                output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
                voice=_v)

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
                self._conv.create_response()
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

    def send_audio(self, pcm16_bytes: bytes) -> None:
        if not self._connected or not pcm16_bytes:
            return
        try:
            self._conv.append_audio(base64.b64encode(pcm16_bytes).decode())
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
        try:
            self.connect()
        except Exception as e:  # noqa: BLE001
            logger.error("云端全双工(Qwen-Omni)连接失败: %s", e)
            self.running = False
            self._connected = False
            return
        self.running = True
        try:
            # 🛡️ 共享单例：会话期间绝不 terminate（见 _pa_shared）
            pa = get_shared_pyaudio()
            # ⚠️ 同上：常量从模块取（曾写成 pa.paInt16 → 麦克风永远打不开）
            _mic_kw = dict(format=pa_int16(), channels=1, rate=16000,
                           input=True, frames_per_buffer=1600)
            _mic_idx = None
            _mic_fallback_note = ""
            if self._mic_device:
                _mic_idx = _resolve_device_index(pa, self._mic_device, "input")
                if _mic_idx is not None:
                    _mic_kw["input_device_index"] = _mic_idx
            try:
                mic = pa.open(**_mic_kw)
            except Exception as e:  # noqa: BLE001
                # 【2026-10-17 修复·WASAPI 路 16k 打不开 → 按同设备名换 host 重试】
                # 实测：WASAPI 的 K20 Pro 在 16k 直接报 Invalid sample rate(-9997)，
                # 旧逻辑直接回退"系统默认输入"（可能是键盘麦/环回），用户明明选对了设备
                # 却被换掉——正是"选对了却用不上"的另一种表现。
                # 现在：拿**纯设备名**（去掉 |hostapi 限定）在其它 host API 上依次试
                # （MME → DirectSound → WASAPI），第一个能开 16k 的就用它；全失败才回退默认。
                _opened = False
                if _mic_idx is not None:
                    logger.warning("云端双工：指定麦克风 %r 打开失败，尝试同设备的其它 host API: %s",
                                   self._mic_device, e)
                    _bare = str(self._mic_device or "").split("|")[0].strip().lower()
                    _tried = []
                    for _alt in range(pa.get_host_api_count()):
                        try:
                            _cnt = int(pa.get_host_api_info_by_index(_alt).get("deviceCount") or 0)
                        except Exception:  # noqa: BLE001
                            continue
                        for _di in range(_cnt):
                            try:
                                _dd = pa.get_device_info_by_index(_di)
                            except Exception:  # noqa: BLE001
                                continue
                            if str((_dd or {}).get("name") or "").strip().lower() != _bare:
                                continue
                            if int((_dd or {}).get("maxInputChannels") or 0) <= 0:
                                continue
                            if _di == _mic_idx or _di in _tried:
                                continue
                            _tried.append(_di)
                            try:
                                _mic_kw["input_device_index"] = _di
                                mic = pa.open(**_mic_kw)
                                _mic_idx = _di
                                _mic_fallback_note = ("（%r 在首选 host 上 16k 打不开，"
                                                      "已自动切到 idx=%d 的同一只麦克风）"
                                                      % (self._mic_device, _di))
                                _opened = True
                                break
                            except Exception:  # noqa: BLE001
                                continue
                        if _opened:
                            break
                if not _opened:
                    # 该设备所有 host 都开不了 → 才回退系统默认输入
                    if _mic_idx is not None:
                        logger.warning("云端双工：指定麦克风 %r 所有 host API 均打开失败，"
                                       "回退系统默认输入", self._mic_device)
                        _mic_kw.pop("input_device_index", None)
                        mic = pa.open(**_mic_kw)
                    else:
                        raise
            # 【2026-10-04 修正】原代码 getattr(mic, "input_device_index", "?") 永远拿不到：
            # PyAudio 的 Stream 对象没有这个公开属性（它在 get_stream_info() 里），所以日志
            # 一直打 idx=? name='?'，让人误以为麦克风没开好。改用 get_stream_info 取真实值。
            try:
                _si = pa.get_stream_info(mic)
                _in_idx = getattr(_si, "input_device_index", None)
            except Exception:  # noqa: BLE001
                _in_idx = _mic_idx
            try:
                _mi = pa.get_device_info_by_index(_in_idx) if _in_idx is not None else {}
            except Exception:  # noqa: BLE001
                _mi = {}
            logger.info("云端双工麦克风已打开：idx=%s name=%r（上行将推送至云端）",
                        _in_idx if _in_idx is not None else "?",
                        (_mi or {}).get("name", "?"))
            # 【2026-10-04 18:00】记下"当前实际在用的那一路"（名字 + hostApi），
            # 待会在 VAD 真正触发时据此写入"已验证可用"记忆。
            try:
                self._active_mic_name = str((_mi or {}).get("name") or self._mic_device or "")
                # ⚠️ 同款 falsy 陷阱：hostApi=0（MME）时不能写 `or -1`，否则记住的是 -1。
                _raw_api = (_mi or {}).get("hostApi")
                self._active_mic_api = int(_raw_api) if _raw_api is not None else -1
                self._mic_open_ts = time.time()
            except Exception:  # noqa: BLE001
                self._active_mic_name = self._mic_device or ""
                self._active_mic_api = -1
                self._mic_open_ts = time.time()
        except Exception as e:  # noqa: BLE001
            # 对齐「启动云端达妮娅.bat」：麦克风打开失败只停上行推流，绝不关闭已建好
            # 的音频播放器 / 会话。否则会把 AI 的下行声音一起掐掉，表现为"接通了但没声"。
            # 此时连接早已成功（_connected=True、player 已就绪），上行不可用只是"听不到
            # 用户说话"，AI 仍能正常出声。
            logger.error("云端全双工打开麦克风失败（上行不可用，下行/出声保留）: %s", e)
            self.running = False
            return
        _last_log = time.time()
        # 【2026-10-04 新增·持续静音自动换设备】对齐开源实时语音项目的通行做法：
        # 设备选择**不能靠通话前那一瞬的探测**——用户可能恰好没在说话，探到的是底噪，
        # 于是选中一条哑设备（10-04 实证：同一台机 6 路 MCHOSE 设备，探测时
        # 0.769 选中了 idx=17，实际通话中那路返回全零；而 idx=20 一直正常）。
        # 这里在通话中持续观察：若连续 _SILENT_REOPEN_S 秒上行 RMS 都是底噪，
        # 就重开一次麦克风流（并重新按名解析设备），把"哑设备"换掉。
        _silent_since = None
        _SILENT_TH = 0.004      # 低于此视为静音（真环境底噪约 0.001~0.003）
        _SILENT_REOPEN_S = 25.0  # 连续静音多久后尝试换一路（25s：够覆盖"你正在想怎么说"）
        _reopen_done = 0         # 最多重开 2 次，避免无休止重建
        try:
            while self.running:
                try:
                    d = mic.read(1600, exception_on_overflow=False)
                except Exception:  # noqa: BLE001
                    break
                if d:
                    # 【2026-10-02 新增·麦克风电平】统计上行音量，供通话 UI 律动条
                    # 如实显示"有没有采到你的声音"。此前云端链路没有 get_mic_level，
                    # proactive.get_call_mic_level 拿不到 → 返回 active=False →
                    # UI 显示"未在采集音频·检查麦克风设备"，**与实际正在采集矛盾**（误导）。
                    self._track_mic_level(d)
                    # 【2026-10-04 修正·对齐 .bat 好链路】上行送云端 = **原始**麦克风音频。
                    # 此前套了一层双向 AGC 把电平归一化到 0.22（含把热麦底噪也拉满），
                    # 抹平了"静音谷"，云端 server_vad 看不到自然的电平起伏 → 起音/止音都判定
                    # 失败（即 16:29 / 17:07 反复"不理我"的真凶）。现改回原始音频，server_vad
                    # 自己负责打断，与「启动云端达妮娅.bat」行为完全一致（它 send_audio(raw)）。
                    # 不再做任何上行 AGC（那套双向 AGC 已彻底退出上行路径）。
                    self.send_audio(d)
                    # 通话记录回放用户自己的声音：同样用送云端的原始音频（忠实、不二次处理）。
                    try:
                        if self._on_user_audio:
                            self._on_user_audio(d)
                    except Exception:  # noqa: BLE001
                        pass
                    # 【2026-10-04·本地 VAD】基于能量的端点检测：说话结束即手动提交并请求回复，
                    # 绕开在该模型/SDK 组合下反复失效的云端 server_vad（详见 _vad_update）。
                    try:
                        self._vad_update()
                    except Exception:  # noqa: BLE001
                        pass
                    # 【2026-10-04 17:55 修正】"静音"判据不能只看电平高低。
                    # 坏设备（如 idx=17 那路 DirectSound）会持续输出 2k/8kHz 干扰音，
                    # 电平高达 0.55~0.65 —— 用"电平>=阈值=有声音"判定，会把这种
                    # **干扰**当成"用户正在说话"，于是永远不触发重开，坏设备被永久锁死。
                    # 正确判据＝**周期性/频谱不像人声**。这里用"电平远高于语音正常上限"
                    # 作为可疑信号：真人语音 RMS 极少持续超过 0.8，坏设备干扰常顶到 0.5+
                    # 且几乎不随说话起伏。连续 25s 出现这种"高电平但云端不响应"的形态
                    # 就重开并重新按声学判据选路。
                    # 【2026-10-09 修复·误判"哑设备"导致通话中途换麦/不理我】
                    # 旧判据用 `_mic_level`——它是"起音快、回落慢"的平滑包络，真人说话
                    # 断续（一句里只有 30~50% 时间发声），句间静音块每块把它拉低 40%，
                    # 于是**说话间隙的采样几乎恒 <0.004**，25s 后必然误判"采不到正常人声"
                    # → 重开麦克风 + 重新解析设备（实测：通话中无端切到 K20 Pro、
                    # 上行断流、AI 不理我）。改用 `_mic_disp`（近期峰值、快速回落，
                    # 见 _track_mic_level）作为"有没有人说话"的判据，才与你的实际音量相符。
                    if self._mic_disp >= _SILENT_TH and self._mic_disp < 0.9:
                        _silent_since = None
                    else:
                        if _silent_since is None:
                            _silent_since = time.time()
                        elif (time.time() - _silent_since > _SILENT_REOPEN_S
                              and _reopen_done < 3 and self._mic_device):
                            _reopen_done += 1
                            _silent_since = None
                            logger.warning(
                                "云端双工：连续 %.0fs 采不到正常人声（电平异常 %.3f —— "
                                "可能是干扰音/哑设备），按声学判据重选麦克风（第 %d 次）",
                                _SILENT_REOPEN_S, self._mic_level, _reopen_done)
                            # ⭐【2026-10-04 18:05】必须先**作废记忆**再重选，
                            # 否则 _resolve_device_index 会直接从记忆里取回同一条
                            # 已失效的路由，重开等于什么都没变（原地打转）。
                            try:
                                forget_verified_mic(self._mic_device)
                            except Exception:  # noqa: BLE001
                                pass
                            try:
                                mic.close()
                            except Exception:  # noqa: BLE001
                                pass
                            try:
                                _idx2 = _resolve_device_index(pa, self._mic_device, "input")
                                if _idx2 is not None:
                                    mic = pa.open(**dict(_mic_kw, input_device_index=_idx2))
                                    logger.info("云端双工：麦克风已重开（idx=%s）", _idx2)
                                    try:
                                        _mi2 = pa.get_device_info_by_index(_idx2) or {}
                                        _ra = _mi2.get("hostApi")
                                        self._active_mic_name = str(
                                            _mi2.get("name") or self._mic_device or "")
                                        self._active_mic_api = int(_ra) if _ra is not None else -1
                                    except Exception:  # noqa: BLE001
                                        pass
                                else:
                                    # 【2026-10-13 修复】重解析失败（_idx2=None）时旧代码
                                    # 既不重开也不退出 → mic 已被 close，上行从此恒静默，
                                    # 后续 self-heal 全部空转。必须回退默认输入兜底。
                                    logger.warning(
                                        "云端双工：重选麦克风无结果（配置 %r 解析不到），"
                                        "回退系统默认输入", self._mic_device)
                                    _kw2 = dict(_mic_kw)
                                    _kw2.pop("input_device_index", None)
                                    mic = pa.open(**_kw2)
                                    self._active_mic_name = "(系统默认输入)"
                                    self._active_mic_api = -1
                            except Exception as e2:  # noqa: BLE001
                                logger.warning("云端双工：麦克风重开失败，沿用系统默认: %s", e2)
                                try:
                                    _kw = dict(_mic_kw)
                                    _kw.pop("input_device_index", None)
                                    mic = pa.open(**_kw)
                                except Exception:  # noqa: BLE001
                                    break
                _now = time.time()
                # ⭐【2026-10-04 18:05 自愈看门狗】记忆里的路由也可能过期
                # （换设备/驱动更新/系统隐私设置改动后，那条"已验证"的路由会变哑）。
                # 判据＝**客观事件**：麦克风开了 45 秒以上、期间电平明显非零（在拾音），
                # 却一次 `speech_started` 都没收到 ⇒ 云端根本没听到人声 ⇒ 这条路由
                # 已失效。动作：作废记忆 + 立即按声学判据重选（而不是干等 25s 静音阈值）。
                _WATCHDOG_S = 45.0
                if (not self._vad_triggered
                        and self._mic_open_ts > 0
                        and _now - self._mic_open_ts > _WATCHDOG_S
                        and _reopen_done < 3
                        and self._mic_disp >= 0.01):
                    _reopen_done += 1
                    self._mic_open_ts = _now      # 避免本轮内重复触发
                    _silent_since = None
                    logger.warning(
                        "云端双工：麦克风已开 %.0fs、上行电平 %.3f（在拾音）却始终没收到 "
                        "speech_started —— 判定该路由失效（记忆过期/设备变哑），"
                        "作废记忆并按声学判据重选（第 %d 次）",
                        _WATCHDOG_S, self._mic_level, _reopen_done)
                    try:
                        forget_verified_mic(self._active_mic_name or self._mic_device)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        mic.close()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        _idx3 = _resolve_device_index(pa, self._mic_device, "input")
                        if _idx3 is not None:
                            mic = pa.open(**dict(_mic_kw, input_device_index=_idx3))
                            logger.info("云端双工：看门狗重开麦克风成功（idx=%s）", _idx3)
                            try:
                                _mi3 = pa.get_device_info_by_index(_idx3) or {}
                                _r3 = _mi3.get("hostApi")
                                self._active_mic_name = str(
                                    _mi3.get("name") or self._mic_device or "")
                                self._active_mic_api = int(_r3) if _r3 is not None else -1
                                self._mic_open_ts = _now
                            except Exception:  # noqa: BLE001
                                pass
                        else:
                            # 【2026-10-13 修复】同上：解析不到也不能留已关闭的 mic 空转
                            logger.warning(
                                "云端双工：看门狗重选无结果（配置 %r 解析不到），回退系统默认输入",
                                self._mic_device)
                            _kw3 = dict(_mic_kw)
                            _kw3.pop("input_device_index", None)
                            mic = pa.open(**_kw3)
                            self._active_mic_name = "(系统默认输入)"
                            self._active_mic_api = -1
                            self._mic_open_ts = _now
                    except Exception as e3:  # noqa: BLE001
                        logger.warning("云端双工：看门狗重开麦克风失败: %s", e3)
                if _now - _last_log >= 2.0:
                    # 【2026-10-09】打印"显示用峰值"（_mic_disp）为主，附带平滑值，
                    # 便于区分"真的没采到"与"被句间静音稀释"。旧版只打 _mic_level，
                    # 说话中间的采样几乎恒 0.0000 → 误报"麦克风静音"（实际识别正常）。
                    _disp = self._mic_disp
                    logger.info(
                        "云端双工麦克风上行电平(2s)=%.4f（峰值 %.4f）%s", self._mic_level, _disp,
                        "（≈0 表示没采到声音/麦克风静音——AI 永远不会回应）"
                        if _disp < 0.005 else "")
                    _last_log = _now
                time.sleep(0.005)
        finally:
            try:
                mic.close()
            except Exception:  # noqa: BLE001
                pass
            # 🛡️ 不 terminate 共享单例：只关麦克风流。
            self.running = False

    # ---------- 麦克风电平（与 local_omni_duplex.get_mic_level 同接口）----------
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
        """基于能量的本地端点检测：说话结束即手动 commit + response.create。

        为什么需要它：云端 server_vad 在该模型/SDK 组合下经常不触发 speech_started
        （实测：上行电平 0.22、AGC 健康，却全程零 speech_started → AI 永不回应）。
        这里用每块原始 RMS 做带迟滞的能量 VAD，自己掌控"何时提交、何时让 AI 回话"，
        彻底摆脱对云端 VAD 的依赖。仅在 self._local_vad=True 时被调用。
        """
        if not self._local_vad:
            return
        # 每块 1600 采样 @16k ≈ 0.1s
        _SPEECH_ON = 0.018    # 起音阈值（高于底噪 0.003、远低于正常说话 0.05+）
        _SPEECH_OFF = 0.008   # 止音阈值（迟滞，防抖动）
        _ON_BLOCKS = 2         # 连续 2 块有声（≈0.2s）才判"开始说话"（起音防抖）
        _SILENCE_BLOCKS = 7    # 连续 7 块静音（≈0.7s）才判"说话结束"（≈ vad_silence_ms）
        rms = self._last_block_rms
        if rms >= _SPEECH_ON:
            self._vad_silent = 0
            self._vad_voiced += 1
            if self._vad_state == 0 and self._vad_voiced >= _ON_BLOCKS:
                self._vad_state = 1
                self._vad_pending = True
                logger.info("本地 VAD：检测到你开始说话（本地即时打断）")
                # 用户开口 → 立刻停掉正在播放的 AI 音频（打断）
                if self._player:
                    self._player.flush()
                # 若 AI 正在回答途中被你打断，先取消它的回复再提交你的话
                if self._responding and self._conv is not None:
                    try:
                        self._conv.cancel_response()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            self._vad_voiced = 0
            if self._vad_state == 1:
                self._vad_silent += 1
                if rms < _SPEECH_OFF:
                    # 仅在真正低于止音阈值时才累计静音块（迟滞）
                    pass
                if self._vad_silent >= _SILENCE_BLOCKS:
                    self._vad_state = 0
                    # 说话结束：若本轮确实采到语音、且云端当前没在生成回复 → 提交并请求回复
                    if self._vad_pending and not self._responding and self._conv is not None:
                        self._vad_pending = False
                        try:
                            self._conv.commit()
                            # 立即清空输入缓冲：手动模式下云端不会像 server_vad 那样自动清，
                            # 不清会导致下一轮把旧音频一起重新提交（AI 重复听到旧话）。
                            try:
                                self._conv.clear_appended_audio()
                            except Exception:  # noqa: BLE001
                                pass
                            self._conv.create_response()
                            logger.info("本地 VAD：说话结束，已提交语音并请求达妮娅回复")
                        except Exception as e:  # noqa: BLE001
                            logger.warning("本地 VAD 提交失败（可忽略）: %s", e)

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
        self.running = False
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
            for k in ("model", "api_key", "base_url", "ws_url", "voice", "turn_detection"):
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
