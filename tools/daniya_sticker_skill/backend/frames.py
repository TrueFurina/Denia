"""动图抽帧：把 GIF 的关键帧抽出来，供视觉模型打标。

为什么必须做：
本库 **24 张全是 GIF（5~72 帧）**，而当前 `_prepare_image` 只取第一帧。
实测 19/24 张的**首帧与末帧差异显著**——例如某张首帧是"面无表情"，
到第 37 帧才变成"笑着冒爱心"。**只喂首帧 = 只看了表情包的一瞬间**，
这正是此前"打标系统性出错"的重要根因（模型没错，是我们喂错了图）。

抽帧策略（可配置）：
  first      只取首帧（= 现状基线）
  last       只取末帧
  mid        取中间帧
  uniform    均匀取 N 帧
  motion     按"与首帧差异"取变化最大的 N 帧（更能代表动作高潮）

输出：单帧返回一张图；多帧返回**横向拼接图**（一次请求即可让模型看到全过程，
比多次调用省额度）。
"""
from pathlib import Path

try:
    from PIL import Image, ImageChops
    _PIL = True
except Exception:  # noqa: BLE001
    _PIL = False


def frame_indices(n: int, strategy: str = "motion", k: int = 3) -> list[int]:
    """返回要抽取的帧下标（0-based）。纯函数，便于单测。"""
    if n <= 1:
        return [0]
    k = max(1, min(k, n))
    if strategy == "first":
        return [0]
    if strategy == "last":
        return [n - 1]
    if strategy == "mid":
        return [n // 2]
    if strategy == "uniform":
        if k == 1:
            return [0]
        step = (n - 1) / (k - 1)
        return sorted({int(round(i * step)) for i in range(k)})
    if strategy == "motion":
        # 先均匀铺满候选，再按"与首帧差异"挑最大的 k 个（末帧常是高潮，确保入选）
        cand = sorted({0} | {int(round(i * (n - 1) / max(1, k * 3 - 1))) for i in range(k * 3)}
                      | {n - 1})
        return cand
    return [0]


def _frame_diff(a, b) -> float:
    try:
        d = ImageChops.difference(a.convert("L"), b.convert("L"))
        px = list(d.getdata())
        return sum(px) / max(1, len(px))
    except Exception:  # noqa: BLE001
        return 0.0


def extract_frames(data: bytes, strategy: str = "motion", k: int = 3) -> list:
    """从图片字节抽帧，返回 PIL.Image 列表（失败则返回空）。"""
    if not _PIL:
        return []
    import io
    try:
        im = Image.open(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        return []
    n = getattr(im, "n_frames", 1)
    if n <= 1:
        return [im.convert("RGB")]

    idxs = frame_indices(n, strategy, k)
    if strategy == "motion":
        # 在候选里按"与首帧差异"排序，取差异最大的 k 个（保证包含首帧与末帧）
        im.seek(0)
        base = im.convert("L").resize((64, 64))
        scored = []
        for i in idxs:
            try:
                im.seek(i)
                cur = im.convert("L").resize((64, 64))
                scored.append((_frame_diff(base, cur), i))
            except Exception:  # noqa: BLE001
                continue
        scored.sort(reverse=True)
        chosen = sorted({i for _, i in scored[:max(1, k - 1)]} | {n - 1})
    else:
        chosen = idxs

    out = []
    for i in chosen:
        try:
            im.seek(i)
            out.append(im.convert("RGB").copy())
        except Exception:  # noqa: BLE001
            continue
    return out or [im.convert("RGB")]


def compose(frames: list, cell: int = 384, gap: int = 8):
    """把多帧横向拼成一张图（供一次视觉请求）。单帧则直接返回该帧。"""
    if not frames:
        return None
    if len(frames) == 1:
        im = frames[0].copy()
        im.thumbnail((cell * 2, cell * 2))
        return im
    tiles = []
    for f in frames:
        t = f.copy()
        t.thumbnail((cell, cell))
        tiles.append(t)
    h = max(t.height for t in tiles)
    w = sum(t.width for t in tiles) + gap * (len(tiles) - 1)
    sheet = Image.new("RGB", (w, h), (255, 255, 255))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + gap
    return sheet


def to_jpeg_bytes(im, max_side: int = 1024, quality: int = 88) -> bytes:
    """转成标准 JPEG 字节（与 llm._prepare_image 同样的格式兼容处理）。

    max_side 放宽到 1024：多帧拼接图本来就更宽，若按单帧阈值缩会**丢失动作细节**
    （实测 916px 的拼图被压到 768 后，末帧的爱心/表情变化会糊掉）。
    视觉模型对分辨率敏感，但拼图缩得过小反而得不偿失。
    """
    import io
    im2 = im.convert("RGB")
    if max(im2.size) > max_side:
        im2.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im2.save(buf, "JPEG", quality=quality)
    return buf.getvalue()
