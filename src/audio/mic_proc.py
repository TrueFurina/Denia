# -*- coding: utf-8 -*-
"""独立进程麦克风采集器（DirectSound 优先），输出 16k/mono/int16 到 stdout。

主进程用 subprocess 启动本脚本，通过 stdout 读音频块。
进程隔离 = 每次全新 PortAudio 实例 = 无"先开流污染"问题（实测 DirectSound 唯一有声）。

协议（stdout 二进制流）：
  每 chunk: 4 字节小端长度 + 数据（3200 字节 = 1600 采样 @16k）
  日志:     **不再写 stderr**（见下）

【2026-10-18 关键修复·stderr 管道死锁】
本脚本原先把自己的诊断日志全部写 stderr，而主进程 `Popen(..., stderr=PIPE)`
**从不读取**该管道。Windows 匿名管道缓冲约 64KB：日志攒满即 `write()` 阻塞，
采集循环原地卡死 —— 子进程还活着、stdout 却不再出数据，主进程读到空流后
误判"流断开"→ 杀进程重启 → 每次重启重新灌 ~64KB 日志 → 再卡死 …
表现就是本 bug 的原始症状：**通话时好时坏，某几次声音完全传不上去**。
现在日志改为：
  ① 写独立日志文件（路径由主进程用 argv[2] 传入，失败则回退到本目录）
  ② stderr 只保留极少量致命错误（主进程侧已改为 DEVNULL，永不阻塞）

另加：逐块统计（峰值/全零块/相同块/读耗时）每 5s 一行、死流与空块告警、
设备枚举全量落盘 —— 用户反馈"声音传不上去"时直接看这份日志即可定位。
"""
import os
import sys
import struct
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

DEV_NAME = sys.argv[1] if len(sys.argv) > 1 else ''
LOG_PATH = sys.argv[2] if len(sys.argv) > 2 else ''
TARGET_RATE = 16000

# 采集参数 / 诊断节流
REPORT_EVERY_S = 5.0        # 每 N 秒输出一条块统计
STALE_REOPEN_S = 3.0        # 连续相同块超过 N 秒 → 自杀让主进程重启
_LOGFH = None


def _open_log():
    """打开日志文件（追加）。失败返回 None，绝不让日志影响采集。"""
    global _LOGFH
    if _LOGFH is not None:
        return _LOGFH
    cands = []
    if LOG_PATH:
        cands.append(LOG_PATH)
    try:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'mic_proc.log'))
    except Exception:
        pass
    for p in cands:
        try:
            d = os.path.dirname(p)
            if d:
                os.makedirs(d, exist_ok=True)
            # 显式 UTF-8：子进程默认编码在中文 Windows 下是 GBK，写中文会乱码。
            _LOGFH = open(p, 'a', encoding='utf-8', errors='replace', buffering=1)
            return _LOGFH
        except Exception:
            continue
    return None


def log(msg):
    """写诊断日志：优先文件，其次 stderr（主进程已改为 DEVNULL，不会阻塞）。"""
    line = '%s [micproc %d] %s' % (
        time.strftime('%H:%M:%S'), os.getpid(), msg)
    fh = _open_log()
    if fh is not None:
        try:
            fh.write(line + '\n')
            return
        except Exception:
            pass
    try:
        sys.stderr.write(line + '\n')
        sys.stderr.flush()
    except Exception:
        pass


def main():
    import numpy as np
    import pyaudiowpatch as pa

    # 下面会把 DEV_NAME 重新赋值为"去掉 host 后缀"的真实设备名，
    # 必须显式声明 global，否则 Python 视其为局部变量 → UnboundLocalError。
    global DEV_NAME

    log('启动 DEV_NAME=%r TARGET_RATE=%d LOG_PATH=%r'
        % (DEV_NAME, TARGET_RATE, LOG_PATH))

    p = pa.PyAudio()
    log('PyAudio 就绪（设备数=%s）' % p.get_device_count())

    # —— 解析传入的设备名 ——
    # 【2026-10-18 关键修复·"有时完全没声音"的真凶】
    # 上层"麦克风自愈"会把设备名改写成 `名字|HostApi` 的形式，例如
    #     '麦克风 (MCHOSE K20 Pro)|Windows DirectSound'
    # 而 PortAudio 枚举出的真实设备名只有 `麦克风 (MCHOSE K20 Pro)`。
    # 原实现直接拿整串做子串匹配（DEV_NAME in nm）→ **永远匹配不到** →
    # 子进程报 device not found 后立即退出 → 主进程读到 0 块 → 重试 5 次全失败
    # → 这一通电话彻底没有声音。而切回不带后缀的设备（如 X9）就恢复正常，
    # 于是表现为用户所说的"时好时坏、某几次声音完全传不上去"。
    # 修法：拆出 `名字` 与 `host 偏好` 两段，名字用真实设备名匹配，host 只影响排序。
    _raw_dev = (DEV_NAME or '').strip()
    _host_pref = ''
    if '|' in _raw_dev:
        _raw_dev, _host_pref = _raw_dev.split('|', 1)
        _raw_dev = _raw_dev.strip()
        _host_pref = _host_pref.strip()
        log('设备名含 host 后缀 → 名字=%r host偏好=%r' % (_raw_dev, _host_pref))
    DEV_NAME = _raw_dev
    if not DEV_NAME:
        log('WARN 未指定设备名 → 使用系统默认输入设备')

    def _host_rank(hn, pref):
        """host API 排序：命中偏好最优先，否则 DirectSound > WASAPI > 其他。"""
        _p = pref.lower()
        if _p:
            if 'directsound' in _p and 'directsound' in hn:
                return -1
            if 'wasapi' in _p and 'wasapi' in hn and 'loopback' not in hn:
                return -1
            if 'mme' in _p and hn.endswith('mme'):
                return -1
        return 0 if 'directsound' in hn else (1 if 'wasapi' in hn else 2)

    # —— 枚举同类设备候选 ——
    cands = []
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if int(d.get('maxInputChannels', 0)) <= 0:
            continue
        nm = str(d.get('name') or '').strip()
        if DEV_NAME and DEV_NAME.lower() not in nm.lower():
            continue
        ha = int(d.get('hostApi', -1))
        hn = str(p.get_host_api_info_by_index(ha).get('name') or '').lower()
        cands.append((_host_rank(hn, _host_pref), i, hn,
                      int(float(d.get('defaultSampleRate') or 44100)),
                      int(d.get('maxInputChannels') or 1), nm))
    # 名字匹配失败时逐级降级，绝不让"名字对不上"直接变成"这通没声音"：
    #   ① 去掉括号内的型号再试（'麦克风 (X)' → '麦克风'）
    #   ② 仍失败 → 退回系统默认输入设备
    if not cands and DEV_NAME:
        _base = DEV_NAME.split('(')[0].strip()
        if _base and _base != DEV_NAME:
            log('WARN 完整名未命中，尝试基础名 %r' % _base)
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if int(d.get('maxInputChannels', 0)) <= 0:
                    continue
                nm = str(d.get('name') or '').strip()
                if _base.lower() not in nm.lower():
                    continue
                if 'loopback' in nm.lower():
                    continue
                ha = int(d.get('hostApi', -1))
                hn = str(p.get_host_api_info_by_index(ha).get('name') or '').lower()
                cands.append((_host_rank(hn, _host_pref), i, hn,
                              int(float(d.get('defaultSampleRate') or 44100)),
                              int(d.get('maxInputChannels') or 1), nm))
    if not cands:
        log('ERR device not found: %r（将回退系统默认输入设备）' % DEV_NAME)
        try:
            _def = p.get_default_input_device_info()
            if _def is not None and int(_def.get('maxInputChannels', 0) or 0) > 0 \
                    and 'loopback' not in str(_def.get('name', '')).lower():
                ha = int(_def.get('hostApi', -1))
                hn = str(p.get_host_api_info_by_index(ha).get('name') or '').lower()
                cands.append((_host_rank(hn, _host_pref), int(_def['index']), hn,
                              int(float(_def.get('defaultSampleRate') or 44100)),
                              int(_def.get('maxInputChannels') or 1),
                              str(_def.get('name') or '')))
                log('已回退到系统默认输入设备: %r' % _def.get('name'))
        except Exception as e:  # noqa: BLE001
            log('回退默认设备失败: %s' % str(e)[:120])
    if not cands:
        # 枚举全部输入设备，便于下次对照（"时好时坏"很可能是设备名/枚举不一致）
        try:
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if int(d.get('maxInputChannels', 0) or 0) > 0:
                    ha = int(d.get('hostApi', -1))
                    hn = str(p.get_host_api_info_by_index(ha).get('name') or '?')
                    log('  可用输入设备 idx=%d name=%r host=%s' % (i, d.get('name'), hn))
        except Exception:
            pass
        return
    cands.sort()
    _, idx, hn, rate, ch, nm = cands[0]
    log('候选 %d 个，选中 idx=%d name=%r host=%s rate=%d ch=%d'
        % (len(cands), idx, nm, hn, rate, ch))
    for _r, _i, _hn, _rate, _ch, _nm in cands[1:]:
        log('  （备选 idx=%d name=%r host=%s rate=%d ch=%d）'
            % (_i, _nm, _hn, _rate, _ch))

    # 打开（按设备真实能力：立体声设备必须用 2 声道开，否则数据交错）
    #
    # 【2026-10-18 关键修复·改用回调模式（本 bug 的真正解法）】
    # 实测：pyaudiowpatch 在 Windows DirectSound 上用**阻塞式 read()** 采集时，
    # 会反复返回缓冲区里的同一份旧数据 —— 峰值严格交替（23419/84/23419/84…），
    # 不同值只有 2~3 个；换 5 种 frames_per_buffer 组合全部复现。
    # 而同一设备、同一参数改用 **stream_callback 回调模式**，数据立刻恢复
    # 正常变化（不同值 20/39，真实跟随说话音量）。
    #
    # 这正是用户长期反馈「律动条不跟随声音」「VAD 永不判句末 → AI 不理人」的根因：
    # 上传给云端的是一段反复循环的假音频，云端自然识别不出内容。
    #
    # 故改为：回调只做入队 → 主循环做下混/重采样/写出。
    # 队列解耦保证音频回调线程极快返回，绝不阻塞设备。
    import queue as _queue

    fpb = int(rate / 10)                    # 每块帧数（100ms）
    raw_q = _queue.Queue(maxsize=200)
    blk_dur = float(fpb) / float(rate)      # 块时长秒

    def _on_audio(in_data, frame_count, time_info, status_flags):
        """音频回调：只入队，绝不做重采样/IO（避免阻塞设备线程）。"""
        if status_flags:
            try:
                raw_q.put_nowait(('status', str(status_flags)))
            except Exception:  # noqa: BLE001
                pass
        if in_data:
            try:
                raw_q.put_nowait(('audio', bytes(in_data)))
            except Exception:  # noqa: BLE001
                pass          # 队列满 = 上游消费慢，丢块优于阻塞设备
        return (None, pa.paContinue)

    t_open = time.time()
    st = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                input_device_index=idx, frames_per_buffer=fpb,
                stream_callback=_on_audio)
    log('开流成功（回调模式）ch=%d fpb=%d rate=%d 块时长=%.0fms 耗时=%.0fms'
        % (ch, fpb, rate, 1000.0 * fpb / rate, (time.time() - t_open) * 1000))

    out = sys.stdout.buffer
    prev = None
    stale_n = 0
    stale_blocks = max(1, int(STALE_REOPEN_S / blk_dur))

    # —— 统计窗口 ——
    n_empty = 0             # 回调给空数据
    n_read_err = 0
    n_overflow = 0
    win_ts = time.time()
    win_blk = 0
    win_pk_all = 0
    win_pk_min = 32768
    win_zero = 0
    win_same = 0
    win_read_ms_max = 0.0
    t_start = time.time()

    last_hb = t_start
    # 【2026-10-18 新增·自适应软件增益（AGC）】
    # 实测本机 Windows 麦克风输入音量被设得极低（说话峰值仅 ~200~300，
    # 而满量程 32767）→ 云端 VAD 阈值 0.035 完全触发不了 → AI 永不回应。
    # 微信/游戏能正常用，是因为它们自带 AGC；我们这条链路原先没有。
    # 这里做「慢速自适应」：只抬不压（避免削顶），目标峰值 ~12000，
    # 增益上限 60 倍，每块最多变化 1.3 倍。
    # 【2026-10-18 二次调参】实测目标 8000 时，弱麦说话 RMS 只到 ~0.045，
    # 超过 VAD 阈值 0.035 的块仅 11%，凑不满「连续 3 块」的起音判定 → VAD 不触发。
    # 提高目标 + 加快爬升后，说话段 RMS 稳定落在 0.06~0.15，起音 2~3 块即成立。
    _agc_gain = 1.0
    _AGC_TARGET_PK = 12000
    _AGC_MAX = 120.0
    while True:
        t_read0 = time.time()
        try:
            kind, payload = raw_q.get(timeout=0.5)
        except _queue.Empty:
            if time.time() - last_hb > 20.0:
                log('WARN 20s 未收到音频回调（设备可能已停摆）')
                last_hb = time.time()
            continue
        read_ms = (time.time() - t_read0) * 1000.0
        if read_ms > win_read_ms_max:
            win_read_ms_max = read_ms
        if kind == 'status':
            n_overflow += 1
            if n_overflow in (1, 10, 100) or (n_overflow % 500 == 0):
                log('WARN 音频流状态（累计 %d）: %s' % (n_overflow, payload))
            continue
        raw = payload
        if not raw:
            # 空块：原实现直接 continue → 循环空转、上游收不到音频且无任何日志
            n_empty += 1
            if n_empty in (1, 10, 100) or (n_empty % 500 == 0):
                log('WARN 空音频块（累计 %d）：read 返回 0 字节' % n_empty)
            time.sleep(0.01)
            continue
        # 立体声→单声道
        if ch >= 2:
            a = np.frombuffer(raw, dtype=np.int16).astype(np.float32).reshape(-1, ch)
            a = a.mean(axis=1).astype(np.int16)
        else:
            a = np.frombuffer(raw, dtype=np.int16)
        # 重采样
        if rate != TARGET_RATE:
            x = a.astype(np.float32)
            n16 = int(len(x) * TARGET_RATE / rate)
            xi = np.interp(np.linspace(0, len(x) - 1, n16),
                           np.arange(len(x)), x)
            a = xi.astype(np.int16)

        # —— 自适应软件增益（AGC）：让弱麦也能被云端 VAD 识别 ——
        try:
            if a.size:
                _pk = int(np.abs(a).max())
                if _pk > 0:
                    _want = _AGC_TARGET_PK / float(_pk)
                    if _want > _AGC_MAX:
                        _want = _AGC_MAX
                    if _want < 1.0:
                        _want = 1.0          # 只抬不压，避免削顶
                    # 平滑跟随：抬升快（每块 30%，约 0.3s 到位，跟上说话起音）、
                    # 回落慢（每块 -10%，句间不忽大忽小）
                    if _want > _agc_gain:
                        _agc_gain = min(_want, _agc_gain * 1.3)
                    else:
                        _agc_gain = max(_want, _agc_gain / 1.1)
                    if _agc_gain > 1.01:
                        _g = a.astype(np.float32) * _agc_gain
                        np.clip(_g, -32767.0, 32767.0, out=_g)
                        a = _g.astype(np.int16)
        except Exception:  # noqa: BLE001
            pass
        data = a.tobytes()

        # —— 块级统计 ——
        win_blk += 1
        try:
            if a.size:
                apk = int(np.abs(a).max())
                if apk > win_pk_all:
                    win_pk_all = apk
                if apk < win_pk_min:
                    win_pk_min = apk
                if apk == 0:
                    win_zero += 1
        except Exception:
            pass

        # 死数据检测：连续相同块 → 退出让主进程重启（本机 DirectSound 偶发）
        if data == prev:
            stale_n += 1
            win_same += 1
            # 安静房间的底噪本来就可能逐块完全相同（尤其 16bit 量化后），
            # 短暂 1~2 块相同属正常。只有**持续**相同才是死流，故从第 5 块起才提示，
            # 避免刷屏淹没真正的问题（实测安静时每秒会刷 2 条）。
            if stale_n == 5:
                log('WARN 连续相同块达到 5 块（%.1fs），疑似死流'
                    % (5 * blk_dur))
            if stale_n > stale_blocks:
                log('ERR stale-exit 连续 %d 块(%.1fs)完全相同，窗口峰值=%d'
                    % (stale_n, stale_n * blk_dur, win_pk_all))
                try:
                    st.close()
                except Exception:
                    pass
                return
        else:
            # 只在"确实持续过一段时间"时记录解除，短暂 1~2 块不吵
            if stale_n >= 5:
                log('INFO 相同块结束（持续 %d 块 = %.1fs）'
                    % (stale_n, stale_n * blk_dur))
            stale_n = 0
        prev = data
        # 写 stdout：长度头 + 数据
        try:
            out.write(struct.pack('<I', len(data)))
            out.write(data)
            out.flush()
        except Exception as e:
            log('ERR stdout 写入失败（主进程可能已退出）: %s' % str(e)[:120])
            return
        # 回调模式无需软件节流：设备按真实采样率推送，队列天然限速。
        # （原 read() 模式必须 sleep 补齐，否则会以万倍速空转读缓存 —— 正是旧 bug 成因）

        # —— 周期报告（每 REPORT_EVERY_S 秒一行）——
        now = time.time()
        if now - win_ts >= REPORT_EVERY_S:
            dur = max(1e-6, now - win_ts)
            log('STAT 块=%d(%.1f块/s) 峰值=%d 最小块峰=%d 全零块=%d 相同块=%d '
                '空块=%d 读异常=%d 读耗时max=%.0fms AGC=×%.1f 已运行=%.0fs'
                % (win_blk, win_blk / dur, win_pk_all,
                   win_pk_min if win_pk_min <= 32767 else -1,
                   win_zero, win_same, n_empty, n_read_err,
                   win_read_ms_max, _agc_gain, now - t_start))
            win_ts = now
            win_blk = 0
            win_pk_all = 0
            win_pk_min = 32768
            win_zero = 0
            win_same = 0
            win_read_ms_max = 0.0
            last_hb = now
        # 心跳：长时间没有块（read 卡在设备层）也留痕
        if now - last_hb > 20.0:
            log('WARN 20s 内无块统计（read 可能阻塞在设备层）')
            last_hb = now


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log('ERR ' + str(e)[:200])
