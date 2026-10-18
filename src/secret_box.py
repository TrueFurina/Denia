"""API Key 落盘加密（C9-1 · 2026-10-18）。

问题（实测）：config.yaml 里 `sticker.api_key` 等字段以**明文**存真实服务商 Key，
文件虽被 git ignore，但会被交付包、config.user.bak.yaml 备份、用户误传截图一并带走。

方案：Windows 内置 DPAPI（crypt32.CryptProtectData）——
- 同一 Windows 用户可解，其他用户 / 拷走文件到别的机器解不开；
- 零第三方依赖，ctypes 直调；包内运行时同样可用。

约定格式：密文写成 `dpapi:<base64>`。
- 写盘（Config.save）：所有非空明文 api_key 就地加密；
- 读盘（load_config）：`dpapi:` 前缀字段解密回内存明文，调用方无感；
- 明文旧格式照常读取（兼容），下次 save 自动升级为密文；
- 解密失败（典型：配置从别的机器拷来）不抛异常——保留密文原文，
  由 load_config 转成降级提示，让用户重新填 Key，而不是让软件打不开。

边界（如实声明）：
- DPAPI 只防"文件被拷走"，不防同机同用户的其它进程——这是用户级加密的固有边界；
- 只覆盖 `api_key` 字段；`qzone.cookie` 等其他敏感字段是已知缺口，见规划 C9。
"""
from __future__ import annotations

import base64
import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

_PREFIX = "dpapi:"

# 只加密这些字段名的字符串值。收紧范围：宁可少加密，不误伤业务字段。
_SECRET_FIELDS = frozenset({"api_key"})


class _CRYPT_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi(protect: bool, data: bytes) -> bytes:
    """CryptProtectData / CryptUnprotectData 的公共底层。"""
    # create_string_buffer 返回的对象必须活到调用结束，先落局部变量
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _CRYPT_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _CRYPT_BLOB()
    if protect:
        fn = ctypes.windll.crypt32.CryptProtectData
    else:
        fn = ctypes.windll.crypt32.CryptUnprotectData
    # 签名: (pDataIn, szDataDescr, pOptionalEntropy, pvReserved,
    #        pPromptStruct, dwFlags, pDataOut)
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0,
              ctypes.byref(blob_out)):
        name = "CryptProtectData" if protect else "CryptUnprotectData"
        raise OSError(f"DPAPI {name} 失败 (GetLastError={ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def is_sealed(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_secret(plain: str) -> str:
    """明文 → `dpapi:<base64>`。"""
    blob = _dpapi(True, plain.encode("utf-8"))
    return _PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_secret(value: str) -> str:
    """`dpapi:<base64>` → 明文；不带前缀的值原样返回（明文兼容）。"""
    if not is_sealed(value):
        return value
    blob = base64.b64decode(value[len(_PREFIX):], validate=True)
    return _dpapi(False, blob).decode("utf-8")


def seal_tree(obj: object, path: str = "$") -> int:
    """递归遍历 raw 配置树，把明文 api_key 就地替换为密文。

    返回本次加密的字段数。加密失败（非 Windows 等）记 warning 并保留明文——
    加密失败不能比不加密更糟。
    """
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if k in _SECRET_FIELDS and isinstance(v, str) and v and not is_sealed(v):
                try:
                    obj[k] = encrypt_secret(v)
                    n += 1
                except OSError as e:
                    logger.warning("[secret_box] %s 加密失败，保留明文：%s", p, e)
            else:
                n += seal_tree(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            n += seal_tree(v, f"{path}[{i}]")
    return n


def unseal_tree(obj: object, path: str = "$") -> tuple[int, list[str]]:
    """递归遍历 raw 配置树，把 `dpapi:` 密文就地解密为明文。

    返回 (成功数, 失败路径列表)。失败项保留密文原文，由调用方转成降级提示。
    """
    ok, failed = 0, []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if k in _SECRET_FIELDS and is_sealed(v):
                try:
                    obj[k] = decrypt_secret(v)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{p}（{e}）")
            else:
                sub_ok, sub_failed = unseal_tree(v, p)
                ok += sub_ok
                failed.extend(sub_failed)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            sub_ok, sub_failed = unseal_tree(v, f"{path}[{i}]")
            ok += sub_ok
            failed.extend(sub_failed)
    return ok, failed
