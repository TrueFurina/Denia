# -*- coding: utf-8 -*-
"""Windows 音频端点层（微信/QQ 同款架构）—— 替代 PortAudio 三重设备枚举。

== 为什么 ==
PortAudio 的设备表把 1 个物理麦克风重复列成 MME/DirectSound/WASAPI 三条，
"同名不同路"导致一周的选错设备/静音/死数据问题。微信、QQ 用的 Windows
COM 端点层（IMMDeviceEnumerator）里 1 个物理设备就是 1 个 endpoint，
带全局唯一、永不漂移的 endpoint ID 字符串 —— 选了就绝对选对。

== 本模块提供 ==
  list_endpoints(kind)   → [{'id','name','is_default'}]  UI 下拉框数据源
  get_default_id(kind)   → 系统默认端点 ID
  resolve_id(kind, id_or_name) → 端点 dict（按 ID 精确命中，名字兜底）
  wasapi_index_for(kind, id_or_name, pa) → pyaudiowpatch 里该端点的 WASAPI 索引
    （pyaudiowpatch 的 WASAPI 视角里每个物理设备只出现一次，按名字命中即精确）

kind: 'mic'（采集 eCapture）| 'speaker'（渲染 eRender）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FLOW = {'mic': 1, 'speaker': 2}      # EDataFlow.eCapture=1 / eRender=2


def _enum():
    import comtypes.client
    from comtypes import GUID
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    clsid = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
    return comtypes.client.CreateObject(clsid, interface=IMMDeviceEnumerator)


def _endpoint_id(dev: Any) -> str:
    """IMMDevice → endpoint ID 字符串（GetId）。"""
    try:
        return str(dev.GetId())
    except Exception:  # noqa: BLE001
        return ""


def _endpoint_name(dev: Any) -> str:
    """IMMDevice → 友好名（PropertyStore 直读 PKEY_Device_FriendlyName）。"""
    try:
        from comtypes import GUID
        from pycaw.pycaw import PROPERTYKEY
        store = dev.OpenPropertyStore(0)          # STGM_READ
        key = PROPERTYKEY()
        key.fmtid = GUID('{a45c254e-df1c-4efd-8020-67d146a850e0}')
        key.pid = 14                              # PKEY_Device_FriendlyName
        v = store.GetValue(key)
        # vt=31 (VT_LPWSTR) → 字符串在 union.pwszVal
        name = str(v.union.pwszVal or "")
        return name if name else "?"
    except Exception:  # noqa: BLE001
        return "?"


def list_endpoints(kind: str) -> List[Dict[str, Any]]:
    """枚举活动端点：1 个物理设备 1 条，绝对无重复。"""
    out: List[Dict[str, Any]] = []
    try:
        enum = _enum()
        flow = _FLOW.get(kind, 1)
        devs = enum.EnumAudioEndpoints(flow, 0x1)      # DEVICE_STATE_ACTIVE
        n = devs.GetCount()
        # 系统默认端点 ID
        try:
            from pycaw.pycaw import AudioUtilities  # noqa: F401 确保类型库注册
        except Exception:  # noqa: BLE001
            pass
        default_id = ""
        try:
            dev = enum.GetDefaultAudioEndpoint(flow, 0)   # eConsole
            default_id = _endpoint_id(dev)
        except Exception:  # noqa: BLE001
            pass
        for i in range(n):
            dev = devs.Item(i)
            eid = _endpoint_id(dev)
            name = _endpoint_name(dev)
            out.append({
                'id': eid,
                'name': name,
                'is_default': bool(eid and eid == default_id),
            })
    except Exception:  # noqa: BLE001
        logger.exception("list_endpoints(%s) 失败", kind)
    return out


def get_default_id(kind: str) -> str:
    try:
        enum = _enum()
        dev = enum.GetDefaultAudioEndpoint(_FLOW.get(kind, 1), 0)
        return _endpoint_id(dev)
    except Exception:  # noqa: BLE001
        return ""


def resolve_id(kind: str, id_or_name: str) -> Optional[Dict[str, Any]]:
    """按 ID 精确命中；否则按名字精确匹配；再不行回系统默认。"""
    eps = list_endpoints(kind)
    if not eps:
        return None
    v = str(id_or_name or "").strip()
    if v and v not in ("default", "auto", ""):
        for e in eps:
            if e['id'] == v:
                return e
        low = v.lower()
        for e in eps:
            if e['name'].strip().lower() == low:
                return e
    for e in eps:
        if e['is_default']:
            return e
    return eps[0] if eps else None


def wasapi_index_for(kind: str, id_or_name: str, pa: Any) -> Optional[int]:
    """把端点（ID/名字）映射到 pyaudiowpatch 的 WASAPI 索引。

    关键：只在 **WASAPI host API** 的设备表里找 —— 该视角每个物理设备只出现
    一次（无 MME/DirectSound 重复），名字命中即精确，等价于微信按 endpoint 选。
    """
    ep = resolve_id(kind, id_or_name)
    if ep is None:
        return None
    want = str(ep['name'] or "").strip().lower()
    try:
        wasapi = pa.get_host_api_info_by_type(pa_module().paWASAPI)
        wasapi_idx = int(wasapi.get("index", -1))
    except Exception:  # noqa: BLE001
        return None
    for i in range(pa.get_device_count()):
        try:
            d = pa.get_device_info_by_index(i) or {}
        except Exception:  # noqa: BLE001
            continue
        if int(d.get("hostApi", -1) or -1) != wasapi_idx:
            continue
        if str(d.get("name") or "").strip().lower() == want:
            return i
    # 名字微差兜底：WASAPI 视角里找包含关系
    for i in range(pa.get_device_count()):
        try:
            d = pa.get_device_info_by_index(i) or {}
        except Exception:  # noqa: BLE001
            continue
        if int(d.get("hostApi", -1) or -1) != wasapi_idx:
            continue
        if want and want in str(d.get("name") or "").strip().lower():
            return i
    return None


def pa_module() -> Any:
    """pyaudiowpatch 模块（取 paWASAPI 等常量用）。"""
    import pyaudiowpatch  # noqa: PLC0415
    return pyaudiowpatch
