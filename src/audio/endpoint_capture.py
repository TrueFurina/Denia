# -*- coding: utf-8 -*-
"""Windows 端点采集器（WASAPI 共享模式）—— 微信/QQ 同款架构，干净重写版。

用法：
    cap = EndpointCapture('mic', '麦克风 (2- MCHOSE X9)')
    cap.start()
    data = cap.read_block(timeout=0.3)   # 16k/mono/int16 ≈0.1s
    cap.stop()
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import (POINTER, byref, c_uint32, c_uint64, c_ulong,
                    c_ushort, c_void_p, cast, string_at)
from typing import List, Optional

logger = logging.getLogger(__name__)

_FLOW = {'mic': 1, 'speaker': 2}


class _WFX(ctypes.Structure):
    _fields_ = [('wFormatTag', c_ushort),
                ('nChannels', c_ushort),
                ('nSamplesPerSec', c_ulong),
                ('nAvgBytesPerSec', c_ulong),
                ('nBlockAlign', c_ushort),
                ('wBitsPerSample', c_ushort),
                ('cbSize', c_ushort)]


class EndpointCapture:
    """WASAPI 共享模式采集：按 COM 端点（ID/名字）精确采集，16k/mono/int16 输出。"""

    def __init__(self, kind: str = 'mic', endpoint: str = '',
                 target_rate: int = 16000) -> None:
        self.kind = kind
        self.endpoint = str(endpoint or '')
        self.target_rate = int(target_rate)
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._q: List[bytes] = []
        self.last_error = ''
        self.dev_name = ''
        self.mix_rate = 0
        self.mix_channels = 0
        self._started = False

    # ---------- 对外 ----------
    def start(self) -> bool:
        if self._started:
            return True
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='endpoint_capture')
        self._thread.start()
        for _ in range(80):
            if self._q or self.last_error:
                break
            time.sleep(0.1)
        self._started = bool(self._q) and not self.last_error
        return self._started

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._started = False
        with self._lock:
            self._q.clear()

    def read_block(self, timeout: float = 0.5) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._q:
                    return self._q.pop(0)
            time.sleep(0.01)
        return b''

    # ---------- WASAPI 采集线程 ----------
    def _run(self) -> None:
        audio_client = None
        try:
            import comtypes
            import comtypes.client
            import numpy as np
            from comtypes import GUID, CLSCTX_ALL, IUnknown
            from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
            from pycaw.constants import EDataFlow, CLSID_MMDeviceEnumerator
            from pycaw.pycaw import IAudioClient, PROPERTYKEY

            # ---- IAudioCaptureClient 接口（pycaw 未提供，手工声明）----
            IID_CAP = GUID('{C8ADBD64-E71E-48A0-A4DE-185C395CD317}')

            class IAudioCaptureClient(IUnknown):
                _iid_ = IID_CAP
                _methods_ = [
                    comtypes.COMMETHOD([], comtypes.HRESULT, 'GetNextPacketSize',
                                       (['out'], POINTER(c_uint32), 'frames')),
                    comtypes.COMMETHOD([], comtypes.HRESULT, 'GetBuffer',
                                       (['out'], POINTER(c_void_p), 'data'),
                                       (['out'], POINTER(c_uint32), 'frames'),
                                       (['out'], POINTER(c_uint32), 'flags'),
                                       (['out'], POINTER(c_uint64), 'dev_pos'),
                                       (['out'], POINTER(c_uint64), 'qpc_pos')),
                    comtypes.COMMETHOD([], comtypes.HRESULT, 'ReleaseBuffer',
                                       (['in'], c_uint32, 'frames')),
                ]

            # ---- 端点解析 ----
            clsid = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
            enum = comtypes.client.CreateObject(clsid,
                                                interface=IMMDeviceEnumerator)
            flow = _FLOW.get(self.kind, 1)
            devs = enum.EnumAudioEndpoints(flow, 0x1)
            n = devs.GetCount()
            nk = PROPERTYKEY()
            nk.fmtid = GUID('{a45c254e-df1c-4efd-8020-67d146a850e0}')
            nk.pid = 14

            dev = None
            want = self.endpoint.strip().lower()
            if want and want not in ('default', 'auto'):
                for i in range(n):
                    d = devs.Item(i)
                    try:
                        nm = str(d.OpenPropertyStore(0).GetValue(nk).union.pwszVal or '')
                    except Exception:  # noqa: BLE001
                        nm = ''
                    try:
                        eid = str(d.GetId())
                    except Exception:  # noqa: BLE001
                        eid = ''
                    if want == nm.strip().lower() or want == eid.lower():
                        dev = d
                        self.dev_name = nm
                        break
            if dev is None:
                dev = enum.GetDefaultAudioEndpoint(flow, 0)
                try:
                    self.dev_name = str(dev.OpenPropertyStore(0)
                                        .GetValue(nk).union.pwszVal or '')
                except Exception:  # noqa: BLE001
                    self.dev_name = '(系统默认)'
            logger.info('端点采集：设备=%r', self.dev_name)

            # ---- Activate → 共享模式 Initialize ----
            ac = cast(dev.Activate(IAudioClient._iid_, CLSCTX_ALL, None),
                      POINTER(IAudioClient))
            wfx = ac.GetMixFormat()
            w = ctypes.cast(wfx, POINTER(_WFX)).contents
            tag = w.wFormatTag
            ch = w.nChannels
            rate = w.nSamplesPerSec
            bits = w.wBitsPerSample
            self.mix_rate = rate
            self.mix_channels = ch
            is_float = (tag == 3) or (tag == 0xFFFE and bits == 32)
            logger.info('端点采集：混音 tag=%d ch=%d rate=%d bits=%d float=%s',
                        tag, ch, rate, bits, is_float)

            ac.Initialize(0, 0, 1000000, 0, wfx, None)   # SHARED, 100ms

            cap = cast(ac.GetService(IID_CAP), POINTER(IAudioCaptureClient))
            ac.Start()
            logger.info('端点采集：已启动')

            # ---- 采集循环 ----
            while not self._stop_evt.is_set():
                try:
                    pkt = cap.GetNextPacketSize()
                except Exception:  # noqa: BLE001
                    break
                while pkt:
                    bp = c_void_p(0)
                    sz = c_uint32(0)
                    fl = c_uint32(0)
                    dp = c_uint64(0)
                    qp = c_uint64(0)
                    cap.GetBuffer(byref(bp), byref(sz), byref(fl),
                                  byref(dp), byref(qp))
                    frames = sz.value
                    if frames:
                        nbytes = frames * ch * (4 if is_float else 2)
                        blob = string_at(bp, nbytes)
                        if is_float:
                            f = np.frombuffer(blob, dtype=np.float32).reshape(-1, ch)
                            a = np.clip(f, -1.0, 1.0)
                            ai = (a.mean(axis=1) * 32767.0).astype(np.int16)
                        else:
                            ai = np.frombuffer(blob, dtype=np.int16).reshape(-1, ch)
                            if ch > 1:
                                ai = ai.mean(axis=1).astype(np.int16)
                        if rate != self.target_rate and len(ai):
                            x = ai.astype(np.float32)
                            n16 = int(len(x) * self.target_rate / rate)
                            xi = np.interp(np.linspace(0, len(x) - 1, n16),
                                           np.arange(len(x)), x)
                            ai = xi.astype(np.int16)
                        pcm = ai.tobytes()
                        with self._lock:
                            self._q.append(pcm)
                            if len(self._q) > 60:
                                self._q = self._q[-60:]
                    cap.ReleaseBuffer(frames)
                    pkt = cap.GetNextPacketSize()
                time.sleep(0.005)
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:200]
            logger.exception('端点采集线程异常: %s', e)
        finally:
            try:
                if audio_client is not None:
                    audio_client.Stop()
            except Exception:  # noqa: BLE001
                pass
