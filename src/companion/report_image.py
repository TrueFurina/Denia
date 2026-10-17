"""日报/周报 → 高级感日程卡图片（PIL 本地渲染，粉色二次元 + 毛玻璃风格）。

用户需求：周报日报像"高级感图片/思维导图"一样生成出来。
本地渲染（本文件）：文字精确、风格可控（粉紫渐变 + 白色磨砂卡片 + 圆角），不依赖任何生成模型。
可选 AI 生图：智谱 CogView-3-Flash（完全免费，同一 API key）—— 用户主动开启时才联网，
把日报内容描述成"思维导图风格插画"让模型画（文字可能不精确，作为氛围图/封面图用）。

字体：使用 Windows 系统自带微软雅黑（msyh.ttc / msyhbd.ttc），只读系统文件，不下载任何东西到 C 盘。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# 颜色（粉色系）
C_BG_TOP = (255, 209, 220)      # 顶部浅粉
C_BG_MID = (255, 231, 239)      # 中部
C_BG_BOTTOM = (236, 214, 255)   # 底部淡紫
C_CARD = (255, 255, 255)        # 卡片白
C_TITLE = (211, 82, 130)        # 主标题粉
C_TEXT = (80, 62, 78)           # 正文深灰紫
C_SUB = (170, 140, 156)         # 次要文字
C_ACCENT = (244, 143, 177)      # 强调粉
C_TIME = (178, 116, 148)        # 时间戳紫

# 系统字体（只读，不下载）
_FONT_CANDIDATES = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msyhbd.ttc"),  # 微软雅黑粗体
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msyh.ttc"),    # 微软雅黑
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """取中文字体（粗体优先），找不到回退默认。"""
    for path in ([_FONT_CANDIDATES[0]] if bold else []) + _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    return draw.textlength(text, font=font)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """按像素宽度换行（中文/英文混排）。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        if _text_width(draw, cur + ch, font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _split_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _draw_gradient(img: Image.Image, top: tuple, bottom: tuple) -> None:
    """垂直渐变背景（PIL 逐行混合）。"""
    w, h = img.size
    for y in range(h):
        t = y / max(h - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        ImageDraw.Draw(img).line([(0, y), (w, y)], fill=color)


def _strip_md(text: str) -> str:
    """去掉 Markdown 标记（粗体/斜体/代码等），保留纯文本。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


@dataclass
class _Block:
    """Markdown 解析块。"""
    kind: str          # title / h2 / h3 / bullet / text / empty
    text: str
    indent: int = 0


def _parse_md(md: str) -> list[_Block]:
    """轻量解析 Markdown（标题/二级标题/列表/正文），忽略图片与代码块。"""
    blocks: list[_Block] = []
    in_code = False
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append(_Block("h3", _strip_md(line[4:])))
        elif line.startswith("## "):
            blocks.append(_Block("h2", _strip_md(line[3:])))
        elif line.startswith("# "):
            blocks.append(_Block("title", _strip_md(line[2:])))
        elif re.match(r"^\s*[-*] ", line):
            blocks.append(_Block("bullet", _strip_md(re.sub(r"^\s*[-*] ", "", line))))
        elif re.match(r"^\s*\d+[.、)] ", line):
            blocks.append(_Block("bullet", _strip_md(re.sub(r"^\s*\d+[.、)] ", "", line))))
        elif re.match(r"^\s*!\[", line):
            continue  # 跳过图片
        else:
            blocks.append(_Block("text", _strip_md(line)))
    return blocks


def _parse_time_range(text: str) -> Optional[tuple[str, str, str]]:
    """从列表项里识别时间轴：'09:30 - 11:00 写代码' → (start, end, content)。"""
    m = re.match(r"(\d{1,2}[:：]\d{2})\s*[-~—至]\s*(\d{1,2}[:：]\d{2})\s*(.*)", text)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    return None


def render_daily_image(md: str, out_path: str, *, size: tuple[int, int] = (1200, 1600)) -> str:
    """把日报 Markdown 渲染成高级感日程卡图片。返回输出路径。"""
    blocks = _parse_md(md)
    title = blocks[0].text if blocks and blocks[0].kind == "title" else "每日回顾"

    img = Image.new("RGB", size, C_BG_TOP)
    _draw_gradient(img, C_BG_TOP, C_BG_BOTTOM)
    # 柔光：轻微高斯模糊叠一层半透明白 → 毛玻璃质感
    overlay = Image.new("RGBA", size, (255, 255, 255, 0))
    glow = ImageDraw.Draw(overlay)
    glow.ellipse([size[0] * 0.55, -size[1] * 0.25, size[0] * 1.05, size[1] * 0.35],
                 fill=(255, 255, 255, 90))
    glow.ellipse([-size[0] * 0.25, size[1] * 0.6, size[0] * 0.25, size[1] * 1.1],
                 fill=(255, 255, 255, 70))
    overlay = overlay.filter(ImageFilter.GaussianBlur(60))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)
    w, h = size
    margin = 46
    y = 64
    font_title = _font(44, bold=True)
    font_date = _font(22)
    font_h2 = _font(28, bold=True)
    font_h3 = _font(24, bold=True)
    font_body = _font(22)
    font_time = _font(20)

    # 标题区
    draw.text((margin, y), title, font=font_title, fill=C_TITLE)
    y += 46
    # 装饰小爱心
    draw.text((margin + _text_width(draw, title, font_title) + 16, y - 38),
              "♡", font=font_date, fill=C_ACCENT)
    # 日期行：从标题里尽量剥离日期（MM月DD日 / YYYY-MM-DD）
    day_text = title.replace("日报", "").replace("回顾", "").strip()
    draw.text((margin, y), day_text, font=font_date, fill=C_TIME)
    y += 26
    draw.line([(margin, y), (w - margin, y)], fill=(255, 255, 255, 160), width=2)
    y += 30

    def card_height(text_lines: list[str], font: ImageFont.FreeTypeFont) -> int:
        return len(text_lines) * (font.size + 10) + 34

    def draw_card(x0: int, y0: int, x1: int, title_txt: str, body: str, font: ImageFont.FreeTypeFont) -> int:
        """画一张白色磨砂圆角卡，返回底部 y。"""
        lines = _wrap(draw, body, font, x1 - x0 - 44)
        total = len(lines) * (font.size + 10) + (44 if title_txt else 0) + 34
        hgt = total
        draw.rounded_rectangle([x0, y0, x1, y0 + hgt], radius=20, fill=C_CARD, outline=(255, 255, 255), width=1)
        cy = y0 + 20
        if title_txt:
            draw.text((x0 + 22, cy), title_txt, font=font_h3, fill=C_TITLE)
            cy += 40
        for line in lines:
            draw.text((x0 + 22, cy), line, font=font, fill=C_TEXT)
            cy += font.size + 10
        return y0 + hgt + 18

    # 时间轴卡片（识别"时间段"行 → 高亮时间）
    timeline_items: list[tuple[str, str, str]] = []
    body_items: list[tuple[str, str]] = []
    current_h2 = ""

    # 先分 section
    sections: list[tuple[str, list[_Block]]] = []
    cur: list[_Block] = []
    cur_title = ""
    for b in blocks[1:]:
        if b.kind == "h2":
            if cur:
                sections.append((cur_title, cur))
            cur_title = b.text
            cur = []
        else:
            cur.append(b)
    if cur:
        sections.append((cur_title, cur))

    for sec_title, sec_blocks in sections:
        # section 标题
        draw.text((margin, y), sec_title, font=font_h2, fill=C_TITLE)
        y += 40
        if sec_title in ("时间轴", "时间线", "时间分布"):
            for b in sec_blocks:
                if b.kind == "bullet":
                    parsed = _parse_time_range(b.text)
                    if parsed:
                        s, e, content = parsed
                        card_w = w - 2 * margin
                        # 卡片内：左侧时间块 + 右侧内容
                        hgt = 64
                        draw.rounded_rectangle([margin, y, margin + card_w, y + hgt],
                                               radius=18, fill=C_CARD)
                        draw.rounded_rectangle([margin + 18, y + 14, margin + 132, y + hgt - 14],
                                               radius=12, fill=C_BG_MID)
                        draw.text((margin + 30, y + 18), f"{s}-{e}", font=font_time, fill=C_TIME)
                        lines = _wrap(draw, content, font_body, card_w - 190)
                        cy = y + 14
                        for line in lines[:2]:
                            draw.text((margin + 150, cy), line, font=font_body, fill=C_TEXT)
                            cy += font_body.size + 8
                        y += hgt + 14
                    else:
                        y = draw_card(margin, y, w - margin, "", b.text, font_body)
                else:
                    y = draw_card(margin, y, w - margin, "", b.text, font_body)
        else:
            for b in sec_blocks:
                if b.kind == "bullet":
                    y = draw_card(margin, y, w - margin, "", b.text, font_body)
                elif b.kind == "text":
                    y = draw_card(margin, y, w - margin, "", b.text, font_body)
        y += 10

    # 底部落款
    y = min(y + 6, h - 70)
    draw.line([(margin, y), (w - margin, y)], fill=(255, 255, 255, 160), width=2)
    draw.text((margin, y + 12), "· 本卡由 AI 桌面伴侣自动生成 ·", font=font_time, fill=C_SUB)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def render_weekly_image(md: str, out_path: str, *, size: tuple[int, int] = (1200, 1600)) -> str:
    """周报渲染（复用日报渲染管线，标题保留 '周报'）。"""
    return render_daily_image(md, out_path, size=size)


# ---------- 可选：智谱 CogView-3-Flash 免费生图（思维导图风氛围图） ----------
def generate_ai_image(
    title: str,
    content: str,
    *,
    api_key: str,
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
    model: str = "cogview-3-flash",
    out_path: str = "",
    timeout_s: float = 90,
) -> Optional[str]:
    """调用智谱免费文生图模型生成"思维导图/日程"风格氛围图。

    注意：生图模型的文字渲染不可靠，图片用于封面/氛围展示，精确内容以本地
    渲染卡（render_daily_image）为准。失败返回 None（上层回退本地卡）。
    """
    import json
    import urllib.request

    prompt = (
        "一张高级感十足的日程规划信息图海报，粉色系二次元风格，毛玻璃质感卡片，"
        "中央有思维导图分支结构。内容主题：" + title[:80] +
        "。要点：" + content[:300] + "。布局清晰、色彩柔和、精致可爱，"
        "适合做个人日程回顾封面，不要出现长段文字"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "size": "1024x1024",
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        url = ""
        if isinstance(data, dict):
            for key in ("data", "images"):
                arr = data.get(key)
                if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                    url = str(arr[0].get("url") or arr[0].get("b64_json") or "")
                    if arr[0].get("b64_json"):
                        import base64
                        raw = base64.b64decode(arr[0]["b64_json"])
                        with open(out_path, "wb") as f:
                            f.write(raw)
                        return out_path
                    break
        if not url:
            logger.warning("CogView 生图返回无 URL: %s", str(data)[:200])
            return None
        # 下载生成图
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            raw = resp.read()
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(raw)
        return out_path
    except Exception:  # noqa: BLE001
        logger.warning("CogView 生图失败（回退本地渲染卡）", exc_info=True)
        return None
