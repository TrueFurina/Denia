# -*- coding: utf-8 -*-
"""从一张方形立绘生成多尺寸 `app.ico`（Windows 任务栏/Alt-Tab/窗口图标）。

为什么要这个脚本
----------------
`src/ui/main_window.py:1395` 按 `<项目根>/app.ico` 找窗口图标，找不到就
`icon=None`——Windows 会给你 **python.exe 的图标**。而 `make_dist.py` 的
出包清单里原本也没有它，于是交付包的任务栏图标一直是 Python 的。

图标是"出厂状态"里最显眼的一件：用户还没看到界面，先看到任务栏上那个图标。
所以它得可复现——换一张立绘重跑一次就行，不要手工在画图里另存。

用法
----
    python tools/make_app_icon.py                       # 用默认源图重生 app.ico
    python tools/make_app_icon.py --src 新立绘.png --out app.ico
    python tools/make_app_icon.py --preview D:/预览.png  # 顺便出对照图

两条不显然的规则（都是实测出来的）
----------------------------------
1. **圆角遮罩要 4 倍超采样**：直接在 256 上画圆角再缩到 16，边缘一圈毛刺，
   任务栏上看得出来。
2. **小尺寸要放大裁切**：整张立绘缩到 16px 只剩一团色块。16/24/32 改用中心
   0.72 的裁切（脸部），32 以上用全图——这样小图标才认得出是"那张脸"。
"""
from __future__ import annotations

import argparse
import os

# 每一档用多大的"取景比例"：小尺寸放大裁切，大尺寸保留完整构图
FRAMES = [
    ((16, 16), 0.72),
    ((24, 24), 0.72),
    ((32, 32), 0.82),
    ((48, 48), 1.0),
    ((64, 64), 1.0),
    ((128, 128), 1.0),
    ((256, 256), 1.0),
]
RADIUS_RATIO = 0.22   # 圆角半径 / 边长，接近 Windows 11 图标观感
SS = 4                # 超采样倍数


def _square(im):
    side = min(im.size)
    left = (im.width - side) // 2
    top = (im.height - side) // 2
    return im.crop((left, top, left + side, top + side))


def build(src_path: str, out_path: str) -> list[tuple[int, int]]:
    from PIL import Image, ImageDraw

    src = _square(Image.open(src_path).convert("RGBA"))
    base = 512
    frames: list[tuple[int, int]] = []
    images: dict[tuple[int, int], "Image.Image"] = {}

    for size, zoom in FRAMES:
        # 先按取景比例裁切（zoom<1 表示放大脸部），再缩到目标尺寸
        if zoom < 1.0:
            side = int(src.width * zoom)
            left = (src.width - side) // 2
            top = int((src.height - side) * 0.42)   # 略偏上：眼睛在视觉中心
            crop = src.crop((left, top, left + side, top + side))
        else:
            crop = src

        big = crop.resize((base * SS, base * SS), Image.LANCZOS)
        mask = Image.new("L", (base * SS, base * SS), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, base * SS - 1, base * SS - 1],
            radius=int(base * SS * RADIUS_RATIO), fill=255)
        big.putalpha(mask)
        images[size] = big.resize(size, Image.LANCZOS)
        frames.append(size)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    # 用 256 那张当底，其余档位由 Pillow 写入（append_images 保证每档是独立缩放的）
    images[(256, 256)].save(
        out_path, format="ICO",
        sizes=[s for s in frames],
        append_images=[images[s] for s in frames if s != (256, 256)],
    )
    return frames


def preview(ico_path: str, out_path: str) -> None:
    """浅底/深底各排一行，确认边缘与可辨识度。"""
    from PIL import Image, ImageDraw

    ico = Image.open(ico_path)
    W, H = 760, 620          # 高度要容得下两行 256（每行 24 + 256 + 24）
    canvas = Image.new("RGB", (W, H), (247, 248, 251))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, H // 2, W, H], fill=(10, 12, 18))
    x = 24
    for size in (256, 64, 48, 32, 24, 16):
        try:
            im = ico.ico.getimage((size, size)).convert("RGBA")
        except Exception:  # noqa: BLE001
            continue
        canvas.paste(im, (x, 24), im)
        canvas.paste(im, (x, H // 2 + 24), im)
        x += size + 20
    canvas.save(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成多尺寸 app.ico")
    ap.add_argument("--src", default=os.path.join("src", "ui", "web", "_role_assets", "danya.png"),
                    help="方形立绘源图（默认用达妮娅头像）")
    ap.add_argument("--out", default="app.ico", help="输出 ico 路径")
    ap.add_argument("--preview", help="顺便生成一张对照预览图（可选）")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = args.src if os.path.isabs(args.src) else os.path.join(root, args.src)
    out = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    if not os.path.isfile(src):
        print(f"✗ 源图不存在：{src}")
        return 1

    frames = build(src, out)
    print(f"✓ 已生成 {out}（{os.path.getsize(out)} 字节）")
    print("  尺寸：" + "、".join(f"{w}×{h}" for w, h in frames))
    if args.preview:
        preview(out, args.preview)
        print(f"  预览：{args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
