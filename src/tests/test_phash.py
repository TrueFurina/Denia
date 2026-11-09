"""`src/perception/phash.py` 与 imagehash.phash 的 bit 级对照测试（C9-3）。

替换的生死线：自实现必须与 imagehash 4.3.2 在 200+ 张图上 **hash 完全一致**
（含汉明距离一致）。图像池覆盖真实截屏类纹理、随机噪声、渐变、纯色、
调用方的实际输入形态（`img.resize((64, 64))` 与 `resize((_downscale, ...))`）。

对照源（imagehash）只在**构建机 dev 环境**存在——这正是要甩掉它的原因。
包内运行时不需要 imagehash，本测试只要求 dev 环境装了它（装了，源码里
曾 import 它）。

运行：.venv/Scripts/python.exe src/tests/test_phash.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.perception import phash as phash_mod  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def build_pool() -> list[Image.Image]:
    """200+ 张测试图：噪声（多随机种子）/ 渐变 / 纯色 / 混合 + 真实形态缩放。"""
    rng = np.random.default_rng(20261018)
    pool: list[Image.Image] = []
    # 1) 随机噪声 ×120（float→uint8，最刁钻的系数分布）
    for _ in range(120):
        arr = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        pool.append(Image.fromarray(arr, "RGB"))
    # 2) 渐变 ×40（水平/垂直/对角/径向，对称结构易暴露 DCT 口径差）
    for i in range(40):
        a, b = np.meshgrid(np.arange(64), np.arange(64))
        kind = i % 4
        if kind == 0:
            g = (a * 4).astype(np.uint8)
        elif kind == 1:
            g = (b * 4).astype(np.uint8)
        elif kind == 2:
            g = ((a + b) * 2).astype(np.uint8)
        else:
            g = (np.sqrt((a - 32) ** 2 + (b - 32) ** 2) * 4).astype(np.uint8)
        pool.append(Image.fromarray(np.stack([g] * 3, axis=-1), "RGB"))
    # 3) 纯色 ×8（全部系数相等 → 全 0 hash，验证中位数边界）
    for v in (0, 16, 64, 128, 200, 255, 1, 254):
        pool.append(Image.new("RGB", (64, 64), (v, v, v)))
    # 4) 噪声+渐变混合 ×40（接近真实截屏的纹理）
    for _ in range(40):
        a, b = np.meshgrid(np.arange(64), np.arange(64))
        base = ((a * 3 + b * 2) % 256).astype(np.float64)
        noise = rng.integers(-30, 30, (64, 64), dtype=np.int16)
        g = np.clip(base + noise, 0, 255).astype(np.uint8)
        pool.append(Image.fromarray(np.stack([g] * 3, axis=-1), "RGB"))
    # 5) 调用方实际形态：64x64 直接传 / 更小的 downscale 图
    return pool


def main() -> int:
    try:
        import imagehash
    except Exception as e:  # noqa: BLE001
        # 【2026-11-09 修·「缺环境」不是「判据失败」】imagehash 是**故意**不进
        # requirements 的（它正是被自实现取代的那个包，见 requirements.txt 第 25 行
        # 的说明）。所以「本机没装 imagehash」是**正常状态**，不是产品坏了 ——
        # 原来返回 1，在 check_all 里与真回归长得一模一样，只能靠人去看那一行文字。
        # 按仓库约定改报 77（未验证）：既不算绿（docstring 原本的要求），
        # 也不会混进真红点里。
        print(f"✗ dev 环境没有 imagehash，无法对照：{e}")
        print("[SKIP] 本机没有 imagehash（它不在 requirements 里，是构建机专用对照源）"
              "—— bit 级对照未验证（不是通过）。要验："
              "pip install imagehash==4.3.2")
        return 77

    print("== 1. hash bit 级对照（200+ 张图）==")
    # 注意 str(imagehash) 是 16 位 hex、str(own) 是 64 位 0/1，不能比 str——
    # 直接比 bits 数组（imagehash.hash 就是同形状 bool 数组）。
    pool = build_pool()
    mismatch = 0
    dist_mismatch = 0
    last_ref = None
    last_own = None
    prev_ref = prev_own = None
    for img in pool:
        ref = imagehash.phash(img)
        own = phash_mod.phash(img)
        if not np.array_equal(ref.hash, own.bits):
            mismatch += 1
            last_ref, last_own = str(ref), str(own)
        if prev_ref is not None and prev_own is not None:
            if (prev_ref - ref) != (prev_own - own):
                dist_mismatch += 1
        prev_ref, prev_own = ref, own
    check(mismatch == 0,
          f"{len(pool)} 张图 hash 全部 bit 一致（不一致 {mismatch} 张）")
    if mismatch:
        print(f"    首个差异：ref={last_ref} own={last_own}")
    check(dist_mismatch == 0, f"相邻图汉明距离全部一致（不一致 {dist_mismatch} 对）")

    print("== 2. 接口行为 ==")
    img = pool[0]
    h1 = phash_mod.phash(img)
    h2 = phash_mod.phash(img)
    check(h1 - h2 == 0, "同一张图距离 0")
    check(h1 == h2, "__eq__ 同图相等")
    check(h1 != img, "与 Image 对象不等（__eq__ 返回 NotImplemented 不崩）")
    pure = phash_mod.phash(Image.new("RGB", (64, 64), (7, 7, 7)))
    pure_ref = imagehash.phash(Image.new("RGB", (64, 64), (7, 7, 7)))
    check(np.array_equal(pure.bits, pure_ref.hash),
          f"纯色图与 ref 一致（此前误写『全 0』：实测 ref 是低熵 {int(pure_ref.hash.sum())} 个 1，"
          f"主系数>中位数）")
    check(phash_mod.phash(img, hash_size=4).bits.shape == (4, 4),
          "hash_size 参数生效")
    try:
        phash_mod.phash(img, hash_size=1)
        check(False, "hash_size<2 应拒绝")
    except ValueError:
        check(True, "hash_size<2 应拒绝")

    print("== 3. 真实形态冒烟：64x64 输入（调用方代码路径）==")
    for img in pool[:50]:
        own = phash_mod.phash(img.resize((64, 64)))
        ref = imagehash.phash(img.resize((64, 64)))
        if not np.array_equal(ref.hash, own.bits):
            check(False, "64x64 形态出现不一致")
            break
    else:
        check(True, "50 张 64x64 形态全部一致")

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项")
        for m in _fails:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
