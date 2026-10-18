"""pHash 感知哈希（C9-3 · 2026-10-18，替换 ImageHash 甩掉 scipy 109MB）。

为什么敢替换：全仓 5 处调用（screen_capture / realtime_vision / game_ocr /
course_recorder）全部只用 `imagehash.phash(img)` 拿到对象后做 `h1 - h2`
（汉明距离），无持久化、无 str()——接口窄到只剩距离比较。

与 imagehash 4.3.2 的对齐口径（逐条对照其源码，测试做 bit 级比对）：
1. `convert('L')` + LANCZOS resize 到 hash_size*highfreq_factor 见方
   （imagehash 的 ANTIALIAS 在 Pillow 10+ 就是 Resampling.LANCZOS）；
2. 两次 DCT-II，norm=None（scipy 口径：y[k] = 2·Σ x[n]·cos(πk(2n+1)/2N)，
   **均匀**因子 2——均匀缩放不改变中位数排序，故矩阵实现省略该因子）；
3. 左上 hash_size×hash_size 低频块，`numpy.median` 严格大于二值化。

DCT 用预计算矩阵 D·P·D.T（O(N³)，N=32 → 每次调用 ~5 万次乘加，微秒级）。

**round(9) 是实测出来的必要项**（2026-10-18，208 张对照）：强对称图（纯色/
渐变）的低频系数**数学上相等**，scipy 的 FFT 实现把它们精确归零，矩阵乘
却留下 ~1e-15 残差——残差悬在中位数两侧，二值化结果一半翻成 1（实测纯色
图 ref 全 0 / own 32 个 1）。round 到 1e-9 把残差压回数学零点；对 ~1e4
量级的真实系数，1e-9 绝对精度 = 1e-13 相对，低于双精度极限，不改变任何
真实排序。round(6)/round(9) 实测 208/208 bit 一致，取侵入更小的 9 位。

注意：如需和 imagehash 完全一致的持久化 hash 字符串，不要用本模块——
本模块只为「包内不再拖 scipy」这个目标服务。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

_DEFAULT_HASH_SIZE = 8
_DEFAULT_HIGHFREQ = 4


def _dct_matrix(n: int) -> np.ndarray:
    """DCT-II 变换矩阵：D[k, x] = cos(π·k·(2x+1) / (2n))（省略均匀因子）。"""
    k = np.arange(n).reshape(-1, 1)
    x = np.arange(n).reshape(1, -1)
    return np.cos(np.pi * k * (2 * x + 1) / (2 * n))


class PHash:
    """8x8 bool 位图 + 汉明距离。只实现调用方用到的运算。"""

    __slots__ = ("bits",)

    def __init__(self, bits: np.ndarray) -> None:
        self.bits = bits

    def __sub__(self, other: "PHash") -> int:
        return int(np.count_nonzero(self.bits != other.bits))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PHash):
            return NotImplemented
        return bool(np.array_equal(self.bits, other.bits))

    def __hash__(self) -> int:  # 有了 __eq__ 就该有 __hash__（可哈希性不承诺，仅对齐协议）
        return hash(self.bits.tobytes())

    def __str__(self) -> str:
        flat = self.bits.flatten()
        return "".join("1" if b else "0" for b in flat)


def phash(image: Image.Image, hash_size: int = _DEFAULT_HASH_SIZE,
          highfreq_factor: int = _DEFAULT_HIGHFREQ) -> PHash:
    """与 imagehash.phash 同口径的感知哈希。"""
    if hash_size < 2:
        raise ValueError('Hash size must be greater than or equal to 2')
    img_size = hash_size * highfreq_factor
    small = image.convert("L").resize((img_size, img_size), Image.Resampling.LANCZOS)
    pixels = np.asarray(small, dtype=np.float64)
    d = _dct_matrix(img_size)
    dct = d @ pixels @ d.T                       # 省略均匀因子 2×2，排序不变
    low = np.round(dct[:hash_size, :hash_size], 9)  # 压掉对称图的 1e-15 残差，见模块 docstring
    med = np.median(low)
    return PHash(low > med)
