"""导出真实表情包数据为 JSON，供独立设计稿内嵌（让稿子看起来是真的，不是假数据）。

同时把每张图**转成 base64 内联**，这样设计稿是**单文件、零依赖**，
直接在 DSH 里打开就能看到真图（不用起服务、不用配后端）。

⚠️ 体积控制（关键）：
  原始 GIF 单张可达 2MB，64 张内联会到 55MB（打不开）。
  但**不能简单转静态 JPEG** —— 那样动图就"死"了（用户明确要求"表情包能不能播放"）。
  折中：**保留动图**，但用 PIL 逐帧缩到最长边 200px 再重存为 GIF。
  实测可把 2MB 压到 ~60KB，且**动画仍在**。
"""
import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, r"G:\ai对话\11")
from src.ui.sticker_bridge import get_sticker_bridge  # noqa: E402

try:
    from PIL import Image, ImageSequence
except Exception:  # noqa: BLE001
    Image = None

OUT = Path(r"G:\ai对话\11\src\ui\_mock")
OUT.mkdir(parents=True, exist_ok=True)

MAX_SIDE = 200          # 缩略图最长边。240 配 96 色要 4.4MB；200 视觉几乎无差、体积更省。
MAX_PER_PACK = 40
# ⭐ 全部保留动图（不再设 MAX_ANIMATED 上限）：
#   之前为了压体积只内联前 12 张动图、其余转静态首帧 —— 结果"下面的表情包不动了"，
#   而**动效正是表情包的核心价值**，不该为体积牺牲。改为靠"缩尺寸 + 降帧 + 量化"控体积。
MAX_FRAMES = 8          # 动图最多保留帧数（GIF 体积主要吃在帧数）
GIF_COLORS = 64         # 量化色数：64 色对 Q 版表情几乎无差，体积降最多
JPEG_QUALITY = 84       # 静态图质量


def thumb(raw: bytes) -> str:
    """图片 → 内联 data URL。**动图一律保留动画**（逐帧缩放 + 降帧 + 量化后重存 GIF）。

    体积靠三个手段控，而不是"把后面的转静态"（那会让表情包不动，用户直接看出来）：
      ① 缩尺寸：最长边 MAX_SIDE
      ② 降帧：最多 MAX_FRAMES 帧
      ③ 量化：GIF_COLORS 色
    """
    if Image is None:
        return ""
    try:
        im = Image.open(io.BytesIO(raw))
        n = getattr(im, "n_frames", 1)
        if n > 1:
            # ---- 动图：逐帧缩放到统一尺寸，重存为 GIF（保留动画）----
            frames, durations = [], []
            step = max(1, n // MAX_FRAMES)
            for i, fr in enumerate(ImageSequence.Iterator(im)):
                if i % step:
                    continue
                f = fr.convert("RGBA")
                f.thumbnail((MAX_SIDE, MAX_SIDE))
                frames.append(f)
                durations.append(max(60, fr.info.get("duration", 80) or 80))
            if not frames:
                return ""
            w = max(f.width for f in frames)
            h = max(f.height for f in frames)
            canvas = [Image.new("RGBA", (w, h), (0, 0, 0, 0)) for _ in frames]
            for c, f in zip(canvas, frames):
                c.paste(f, ((w - f.width) // 2, (h - f.height) // 2))
            buf = io.BytesIO()
            pal = [c.convert("P", palette=Image.ADAPTIVE, colors=GIF_COLORS) for c in canvas]
            pal[0].save(buf, "GIF", save_all=True, append_images=pal[1:],
                        duration=durations, loop=0, optimize=True, disposal=2)
            return "data:image/gif;base64," + base64.b64encode(buf.getvalue()).decode()
        # ---- 静态图：转 JPEG ----
        try:
            im.seek(0)
        except Exception:  # noqa: BLE001
            pass
        im = im.convert("RGB")
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_QUALITY)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return ""


b = get_sticker_bridge()
packs = b.stickerPacks().get("packs") or []

result = {"packs": [], "stats": {}}
total_memes = 0
manual = auto = none = 0
animated = 0

for p in packs:
    pid = p["id"]
    memes = b.stickerMemes(pid).get("memes") or []
    items = []
    for m in memes[:MAX_PER_PACK]:
        fp = b._store._pack_dir(pid) / "images" / m["file"]
        if not fp.is_file():
            continue
        prov = m.get("provenance") or "none"
        if prov == "manual":
            manual += 1
        elif prov == "ai":
            auto += 1
        else:
            none += 1
        sv = m.get("style_variants") or {}
        com = sv.get("community") or {}
        data = thumb(fp.read_bytes())
        if data.startswith("data:image/gif"):
            animated += 1
        items.append({
            "id": m["id"],
            "file": m.get("file", ""),
            "desc": m.get("description", ""),
            "tags": m.get("tags") or [],
            "category": m.get("category", ""),
            "trigger": m.get("trigger", ""),
            "anti": com.get("anti_trigger", ""),
            "mood": com.get("mood_compound", ""),
            "prov": prov,
            "data": data,
        })
    total_memes += len(items)
    result["packs"].append({
        "id": pid,
        "name": p.get("name", ""),
        "bindings": p.get("bindings") or {"mode": "none", "characters": []},
        "count": len(memes),
        "memes": items,
    })

result["stats"] = {
    "packs": len(packs), "memes": total_memes,
    "manual": manual, "auto": auto, "none": none, "animated": animated,
}

out = OUT / "mock_data.json"
out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
size_mb = out.stat().st_size / 1024 / 1024
print(f"已导出 {out}")
print(f"  包 {len(packs)} 个 / 内联表情 {total_memes} 张（其中**动图 {animated} 张**）")
print(f"  来源统计: 手动 {manual} / AI {auto} / 未标注 {none}")
print(f"  文件大小 {size_mb:.2f} MB")
