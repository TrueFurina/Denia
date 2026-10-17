# -*- coding: utf-8 -*-
"""theme_ai.py — 主题图位的「几何体检 + AI 配色」。

分工（2026-10-06 与用户确认）：
  · 图片筛选（这张图适不适合这个位）= 纯几何规则，只**提醒**用户，绝不自动搬图；
  · 取色 = 优先交给 VLM（读角色气质/情绪给语义色板）；视觉不可用/超时 → 回落本地色调板。

为什么几何不交给 VLM：实测 qwen3-vl 会把 1018x679 的横图判成「竖构图」并建议放立绘位。
宽高比/朝向这类硬事实必须由代码算，语义（主体/情绪/色彩）才交给模型。

红线：任何一步失败都不得抛断上传链路。宁可降级到本地结果，
      也不让编辑器「换图」因为配色失败而整体失败。
"""
from __future__ import annotations

import io
import json
import math
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 图位几何

# 各图位在**真实界面**里的显示框宽高比（2026-10-06 Edge headless 实测）。
# 注意与 SLOT_HINT（给用户的建议上传尺寸）是两回事：
#   SLOT_HINT  = "你该传多大的图"；
#   SLOT_BOX_AR = "界面实际留了多宽多高的框"。
SLOT_BOX_AR: Dict[str, float] = {
    "hero": 1080.0 / 740.0,    # 1.459  主页 .app 整屏
    "stand": 287.0 / 250.0,    # 1.148  右侧舞台 .panel（横向矮框 → 竖立绘必被裁）
    "avatar": 1.0,             # 1.000  头像 .cav（方形）
    "cover": 470.0 / 289.0,    # 1.627  角色卡 .ccard
}

SLOT_HINT: Dict[str, Tuple[int, int]] = {
    "hero": (1920, 1080),
    "stand": (800, 1000),
    "avatar": (512, 512),
    "cover": (1200, 800),
}

SLOT_LABEL: Dict[str, str] = {
    "hero": "主页背景",
    "stand": "角色立绘",
    "avatar": "头像",
    "cover": "角色卡封面",
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def image_size(path: str) -> Tuple[int, int]:
    """零依赖读图片尺寸：优先复用 theme_store 的手写解析器，其次 PIL。"""
    try:
        from ..ui.theme_store import image_size as _ts_size
        w, h = _ts_size(path)
        if w and h:
            return int(w), int(h)
    except Exception:
        pass
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return 0, 0


def _hint_ar(slot: str) -> float:
    w, h = SLOT_HINT.get(slot, (1, 1))
    return (float(w) / float(h)) or 1.0


def best_slot_for_ar(ar: float) -> str:
    """按每个图位的**建议上传比例**判断这张图更像哪个位（对数距离，横竖不失衡）。"""
    if ar <= 0:
        return ""
    return min(SLOT_HINT.keys(), key=lambda s: abs(math.log(ar / _hint_ar(s))))


def slot_fit(w: int, h: int, slot: str) -> Dict[str, Any]:
    """纯几何判断「这张图放进这个位合不合适」。只产出提醒文案，不改任何东西。

    · 裁切量按**真实显示框** SLOT_BOX_AR 算 —— 界面到底会裁多少，这是硬事实；
    · 「建议换位」按**建议上传比例** SLOT_HINT 算 —— 只有这张图几何上更像另一个位
      时才提，放对了位就不啰嗦（否则一张标准竖立绘会被错误地建议去放头像）。
    """
    if not w or not h:
        return {"level": "unknown", "fit": 0.0, "message": ""}
    ar = float(w) / float(h)
    box = SLOT_BOX_AR.get(slot, 1.0)
    fit = min(ar, box) / max(ar, box)
    if ar > 1.12:
        orient = "横图"
    elif ar < 0.90:
        orient = "竖图"
    else:
        orient = "方图"

    label = SLOT_LABEL.get(slot, slot)
    crop = int(round((1.0 - fit) * 100))
    bs = best_slot_for_ar(ar)
    better_slot = bs if (bs and bs != slot) else ""
    better_label = SLOT_LABEL.get(better_slot, "") if better_slot else ""

    if fit >= 0.82:
        return {"level": "ok", "fit": round(fit, 3), "ar": round(ar, 3),
                "box_ar": round(box, 3), "orient": orient,
                "better_slot": better_slot, "better_label": better_label,
                "message": ""}

    # soft：边缘会裁但主体通常还在；warn：裁得狠、主体容易丢
    level = "soft" if fit >= 0.62 else "warn"
    msg = "这张是%s（%.2f:1）。「%s」的显示框是 %.2f:1（%s），铺满会裁掉约 %d%%。" % (
        orient, ar, label, box, "偏横" if box >= 1.0 else "偏竖", crop)
    msg += (" 建议把主体放在画面中间。" if level == "soft"
            else " 主体容易丢，建议换一张更贴合的图。")
    if better_slot:
        msg += " 这张图更像「%s」。" % better_label
    return {"level": level, "fit": round(fit, 3), "ar": round(ar, 3),
            "box_ar": round(box, 3), "orient": orient,
            "better_slot": better_slot, "better_label": better_label,
            "message": msg}


# ---------------------------------------------------------------- 颜色工具

def _hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    h = h % 1.0
    if s <= 0:
        v = int(round(l * 255))
        return v, v, v
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def _c(t: float) -> float:
        t = t % 1.0
        if t < 1.0 / 6:
            return p + (q - p) * 6 * t
        if t < 1.0 / 2:
            return q
        if t < 2.0 / 3:
            return p + (q - p) * (2.0 / 3 - t) * 6
        return p

    return (int(round(_c(h + 1.0 / 3) * 255)),
            int(round(_c(h) * 255)),
            int(round(_c(h - 1.0 / 3) * 255)))


def _rgb_to_hsl(r: int, g: int, b: int) -> Tuple[float, float, float]:
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h / 6.0) % 1.0, s, l


def _hsl_hex(h: float, s: float, l: float) -> str:
    r, g, b = _hsl_to_rgb(h, s, l)
    return "#%02X%02X%02X" % (r, g, b)


def _norm_hex(v: Any) -> str:
    s = str(v or "").strip()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:  # #abc -> #aabbcc
        s = "#" + s[1] * 2 + s[2] * 2 + s[3] * 2
    if len(s) != 7:
        return ""
    try:
        int(s[1:], 16)
    except Exception:
        return ""
    return s.upper()


def _hex_hsl(hx: str) -> Tuple[float, float, float]:
    hx = hx.lstrip("#")
    return _rgb_to_hsl(int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))


# ---------------------------------------------------------------- 本地取色（不洗色版）

def _pil_pixels(path: str, size: int = 64):
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA").resize((size, size))
            # 不用 getdata()（Pillow 14 起移除）；tobytes() 直接拿 RGBA 平铺字节
            raw = im.tobytes()
    except Exception:
        return None
    return [raw[i:i + 4] for i in range(0, len(raw), 4)]


def dominant_seed(path: str) -> Optional[Tuple[float, float, float]]:
    """取「最能代表这张图」的一个色（HSL）。

    与旧算法（12 色相桶按**出现次数**取胜、再把 S/L 硬夹到安全区）不同：
      · 权重 = 饱和度^1.4 × 显著度（离全图均色的距离）→ 不会被大面积灰背景/白边带偏；
      · 命中后**抬**饱和（×1.18）而不是压到安全区 → 鲜色不会被洗成灰玫瑰。
    """
    px = _pil_pixels(path, 64)
    if not px:
        return None
    n = 0
    sr = sg = sb = 0.0
    for r, g, b, a in px:
        if a < 125:
            continue
        sr += r
        sg += g
        sb += b
        n += 1
    if not n:
        return None
    mr, mg, mb = sr / n, sg / n, sb / n
    buckets: Dict[int, Dict[str, float]] = {}
    for r, g, b, a in px:
        if a < 125:
            continue
        h, s, l = _rgb_to_hsl(r, g, b)
        if l < 0.10 or l > 0.95 or s < 0.10:
            continue
        dist = math.sqrt((r - mr) ** 2 + (g - mg) ** 2 + (b - mb) ** 2)
        wt = (s ** 1.4) * (0.35 + min(dist / 180.0, 1.0))
        if wt <= 0:
            continue
        k = int(h * 12) % 12
        bk = buckets.setdefault(k, {"w": 0.0, "h": 0.0, "s": 0.0, "l": 0.0})
        bk["w"] += wt
        bk["h"] += h * wt
        bk["s"] += s * wt
        bk["l"] += l * wt
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda b: b["w"])
    hh = best["h"] / best["w"]
    ss = best["s"] / best["w"]
    ll = best["l"] / best["w"]
    ss = _clamp(ss * 1.18, 0.42, 0.92)
    ll = _clamp(ll, 0.44, 0.62)
    return hh, ss, ll


def hue_anchors(path: str, top: int = 3) -> List[float]:
    """图像**真实**色相锚点（0~1，最多 top 个，按显著度降序）。

    为什么要它（2026-10-13 实测）：弱视觉模型会「凭空给无关色」——
    glm-4v-flash 在一张整幅浅粉（H≈0°/330°、S≈0.10~0.22）的图上，
    给出了 accent1=#a067ff（蓝紫）甚至 #ff00ff（纯品红，图里任何像素都不接近）。
    提示词里已经写了"不要凭空给无关色"，但**提示词拦不住小模型** ——
    所以必须在代码里用图像真实色相兜一道闸（见 `_ai_hue_off`）。

    与 `dominant_seed` 同一套加权（饱和^1.4 × 显著度），只是不只取第一名：
    把 12 个色相桶按权重排序取前几名 —— 图里若同时有粉底 + 红手套 + 蓝宝石，
    这几个真实色相都会被保留下来当锚点。
    """
    px = _pil_pixels(path, 64)
    if not px:
        return []
    n = 0
    sr = sg = sb = 0.0
    for r, g, b, a in px:
        if a < 125:
            continue
        sr += r
        sg += g
        sb += b
        n += 1
    if not n:
        return []
    mr, mg, mb = sr / n, sg / n, sb / n
    buckets: Dict[int, Dict[str, float]] = {}
    for r, g, b, a in px:
        if a < 125:
            continue
        h, s, l = _rgb_to_hsl(r, g, b)
        if l < 0.10 or l > 0.95 or s < 0.10:
            continue
        dist = math.sqrt((r - mr) ** 2 + (g - mg) ** 2 + (b - mb) ** 2)
        wt = (s ** 1.4) * (0.35 + min(dist / 180.0, 1.0))
        if wt <= 0:
            continue
        k = int(h * 12) % 12
        bk = buckets.setdefault(k, {"w": 0.0, "h": 0.0})
        bk["w"] += wt
        bk["h"] += h * wt
    if not buckets:
        return []
    ordered = sorted(buckets.values(), key=lambda b: -b["w"])
    return [(b["h"] / b["w"]) % 1.0 for b in ordered[:max(1, top)]]


#: 强调色相离图像**任一**真实色相超过这个值（≈50°）→ 判定"凭空色"
_HUE_TOL = 0.14


def _hue_delta(h1: float, h2: float) -> float:
    """两个色相（0~1）的最短角距。"""
    d = abs(h1 - h2)
    return min(d, 1.0 - d)


def _ai_hue_off(obj: Dict[str, Any], anchors: Optional[List[float]],
                mode: str = "dark") -> bool:
    """AI 的强调色相是否「脱离了图像」。anchors 为空 → 不做判断（返回 False）。"""
    if not anchors:
        return False
    a1 = _usable_accent(_norm_hex(obj.get("accent1")), mode)
    if not a1:
        return False
    h1 = _hex_hsl(a1)[0]
    return all(_hue_delta(h1, a) > _HUE_TOL for a in anchors)


def tonal_palette(h: float, s: float, l: float) -> List[Dict[str, str]]:
    """由一个种子色派生 4 套**同族**配色。

    关键：第二色只做「邻近色相 + 明暗/饱和微调」（偏移 ≤ 0.070 ≈ 25°），
    不像旧算法那样塞互补色（H+0.5）→ 不会再撞出棕/薄荷那种不搭的组合。
    """
    return [
        {"name": "贴近原图", "note": "最像图里本来的味道",
         "c1": _hsl_hex(h, s, l), "c2": _hsl_hex(h + 0.045, s * 0.90, l - 0.07)},
        {"name": "同族渐变", "note": "同一色调的明暗层次，稳、耐看",
         "c1": _hsl_hex(h, s * 0.96, l), "c2": _hsl_hex(h + 0.070, s * 0.82, l + 0.09)},
        {"name": "明暗对比", "note": "只拉明暗不换色相，界面更有层次",
         "c1": _hsl_hex(h, s, l - 0.06), "c2": _hsl_hex(h + 0.030, s * 0.88, l + 0.13)},
        {"name": "柔和淡雅", "note": "压低饱和，久看不累",
         "c1": _hsl_hex(h, s * 0.45, l + 0.06), "c2": _hsl_hex(h + 0.050, s * 0.36, l + 0.15)},
    ]


# ---------------------------------------------------------------- VLM 语义取色

def vlm_prompt(mode: str = "dark") -> str:
    """按界面明暗主题生成取色提示词。

    为什么分两版：强调色最终落在玻璃面板上 —— 深色底上要**亮**色才看得见，
    浅色底上要**偏深**色才不糊。同一张图两套约束给出的色板完全不同。
    """
    dark = (mode != "light")
    if dark:
        lum = "饱和度 0.55~0.85、明度 0.48~0.66；禁止灰浊色、禁止过暗（深色底上会看不见）或过亮（会刺眼）"
        extra = "界面整体是深色玻璃质感，强调色要鲜亮跳眼。"
    else:
        lum = "饱和度 0.50~0.85、明度 0.34~0.54；禁止灰浊色、禁止过亮过粉（浅色底上会糊掉看不清）"
        extra = "界面整体是浅色玻璃质感，强调色要偏深、有分量，压得住浅底。"
    return (
        "你是桌面应用的 UI 主题配色师。图里是同一个角色主题的多张素材"
        "（拼在一起，从左到右依次标注了图位），最左边通常是主背景（hero），"
        "它对配色的贡献最大。只输出一个 JSON 对象"
        "（不要解释、不要 markdown 代码块、不要多余文字）：\n"
        '{"subject":"主体是什么","shot":"特写/半身/全身/远景","faces":脸的数量的整数,'
        '"mood":"整体气质或情绪，如 甜美梦幻、清冷克己","focus":"主背景里适合做视觉焦点的位置，形如 50% 40%",'
        '"accent1":"#RRGGBB 主强调色",'
        '"accent2":"#RRGGBB 渐变副色",'
        '"dominant":"#RRGGBB 图中最主要的颜色"}\n'
        "配色硬要求（这是给玻璃质感界面的强调色，会用在按钮/链接/高亮上）：" + extra + "\n"
        "1. accent1 要在鲜和稳之间取平衡：" + lum + "；\n"
        "2. accent2 与 accent1 同色相家族，色相差 ≤ 25°，只做明度/饱和微调用于渐变；禁止互补撞色；\n"
        "3. 从角色的气质与主背景的主色出发，不要凭空给无关色。\n"
    )


# 兼容旧引用：默认深色版
VLM_PROMPT = vlm_prompt("dark")


def parse_vlm_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里稳健抠出第一个 JSON 对象（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl >= 0 and s[:nl].strip().lower() in ("json", ""):
            s = s[nl + 1:]
    i = s.find("{")
    j = s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


#: 界面玻璃底面（两种模式）—— 用来量强调色对底面的对比度（"能不能看清"）
_UI_SURFACE = {"light": "#F4F6FA", "dark": "#15181D"}
#: 强调色对底面的最低对比度。低于它就会"糊"：浅底上显脏、深底上发暗。
_UI_MIN_CONTRAST = {"light": 3.40, "dark": 2.20}
#: 最低饱和度。**灰是"脏"的主因**（实测用户嫌丑的 #C15C5C 只有 S0.52、#BE929E 只有 S0.23）。
_UI_MIN_SAT = 0.62


def _rel_lum(h: float, s: float, l: float) -> float:
    r, g, b = _hsl_to_rgb(h, s, l)

    def _f(u: int) -> float:
        u = u / 255.0
        return u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4

    return 0.2126 * _f(r) + 0.7152 * _f(g) + 0.0722 * _f(b)


def _ui_contrast(h: float, s: float, l: float, base_hex: str) -> float:
    """强调色相对底面色的对比度（WCAG 公式）。"""
    bh, bs, bl = _hex_hsl(base_hex)
    la, lb = _rel_lum(h, s, l), _rel_lum(bh, bs, bl)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _usable_accent(hx: str, mode: str = "dark") -> str:
    """把模型给的强调色夹进当前主题可用且**不丑**的区间。

    · 深色玻璃界面：S 0.50~0.90，L 0.46~0.68（实测 qwen 系会给 #8a8a8a 灰浊或近黑 —— 深底上按钮看不见）；
    · 浅色玻璃界面：S 0.45~0.90，L 0.34~0.56（过亮过粉在浅底上会糊掉，要压深）。

    ⭐ 2026-10-13「品味下限」：只夹"鲜/稳"不够 —— 用户连续反馈"丑/糊"。
    实测两套被判丑的配色：#C15C5C（砖红 S0.52 / 浅底对比 3.73:1）、#BE929E（灰粉 S0.23 / 2.37:1）。
    归因两条：**① 太灰（低饱和＝脏）② 对比不够（贴在浅粉玻璃上糊成一片）**。
    所以这里再补两道闸，不达标就往该模式的"深/亮"方向推（浅色推深、深色推亮），最多推到区间边界：
      最低饱和度 _UI_MIN_SAT + 对底面的最低对比度 _UI_MIN_CONTRAST。
    """
    if not hx:
        return hx
    h, s, l = _hex_hsl(hx)
    if mode == "light":
        s = _clamp(s, 0.45, 0.90)
        l = _clamp(l, 0.34, 0.56)
    else:
        s = _clamp(s, 0.50, 0.90)
        l = _clamp(l, 0.46, 0.68)
    # 品味下限
    s = max(s, _UI_MIN_SAT)
    light_mode = (mode == "light")
    surf = _UI_SURFACE["light" if light_mode else "dark"]
    tgt = _UI_MIN_CONTRAST["light" if light_mode else "dark"]
    l_lo, l_hi = (0.30, 0.56) if light_mode else (0.46, 0.72)
    step = 0.02
    for _ in range(16):
        if _ui_contrast(h, s, l, surf) >= tgt:
            break
        l = _clamp(l + (-step if light_mode else step), l_lo, l_hi)
    return _hsl_hex(h, s, l)


def _schemes_from_vlm(obj: Dict[str, Any], seed, mode: str = "dark",
                      anchors: Optional[List[float]] = None) -> List[Dict[str, str]]:
    a1 = _usable_accent(_norm_hex(obj.get("accent1")), mode)
    a2 = _usable_accent(_norm_hex(obj.get("accent2")), mode)
    dom = _usable_accent(_norm_hex(obj.get("dominant")), mode)
    if not a1:
        return []
    # ⭐ 色相闸（2026-10-13）：模型的强调色相若脱离图像真实色相 → 判为"凭空色"，
    # 返回空 → 调用方 analyze_async 会回落到**按像素**的本地色板（「以前那套」）。
    # 例：整幅浅粉的图被 glm-4v-flash 配成 #a067ff / #ff00ff，用户看到的"鬼配色"。
    if _ai_hue_off(obj, anchors, mode):
        return []
    h1, s1, l1 = _hex_hsl(a1)
    # ⭐ 色相一律以**图像**为准（seed = 图像主色），只采信模型给的明暗/饱和 = 它的"气质"判断。
    # 为什么这么切（2026-10-13 用户连续实测）：把色相交给免费模型 = 每次结果都不一样，
    # 实测同一张浅粉图依次拿到过 蓝紫 #a067ff / 纯品红 #ff00ff / 电光紫 #762AF4 /
    # 琥珀金 #C96A17 —— 提示词拦不住，"色相闸"也只能拦住一部分。
    # 而用户的判据很明确：「以前的算法（纯按像素）不会这么丑」→ 色相必须从图里来。
    # 于是职责切干净：**色相 = 图像像素；明暗 / 饱和 = 模型的气质判断**（再叠加品味下限）。
    if seed:
        a1 = _usable_accent(_hsl_hex(seed[0], s1, l1), mode)
        h1, s1, l1 = _hex_hsl(a1)
    if not a2:
        a2 = _hsl_hex(h1 + 0.05, s1 * 0.9, l1 - 0.06)
    # 若 VLM 给的第二色与主色色相离太远（>43°），拉回邻近，避免撞色
    h2, s2, l2 = _hex_hsl(a2)
    dh = abs(h1 - h2)
    dh = min(dh, 1.0 - dh)
    if dh > 0.12:
        a2 = _hsl_hex(h1 + 0.05, s2 * 0.85, l2)

    mood = str(obj.get("mood") or "").strip()
    out: List[Dict[str, str]] = [
        {"name": "AI 语义配色", "note": (mood or "按角色气质"), "c1": a1, "c2": a2},
    ]
    if dom:
        hd, sd, ld = _hex_hsl(dom)
        # 这一路的名字就是「从图里最主要的颜色出发」→ 色相也锚到图像主色（保模型的明暗/饱和）。
        # 否则模型每次报的 dominant 不一定对（实测飘到过琥珀 38°），整条链路就又开始"每次不一样"。
        if seed:
            dom = _usable_accent(_hsl_hex(h1, sd, ld), mode)
            hd, sd, ld = _hex_hsl(dom)
        out.append({"name": "按主色铺开", "note": "从图里最主要的颜色出发",
                    "c1": dom, "c2": _hsl_hex(hd + 0.05, sd * 0.88, ld - 0.06)})
    out.append({"name": "同族渐变", "note": "同一色调的明暗层次",
                "c1": _hsl_hex(h1, s1 * 0.95, l1 + 0.02),
                "c2": _hsl_hex(h1 + 0.06, s1 * 0.80, l1 + 0.10)})
    out.append({"name": "柔和淡雅", "note": "压低饱和，久看不累",
                "c1": _hsl_hex(h1, s1 * 0.45, l1 + 0.06),
                "c2": _hsl_hex(h1 + 0.04, s1 * 0.36, l1 + 0.15)})
    return out


# ---------------------------------------------------------------- 多图加权（2026-10-13）

# 各图位对配色的贡献权重：主背景最大，其次卡头/立绘，头像最小。
SLOT_COLOR_WEIGHT: Dict[str, float] = {
    "hero": 0.55,
    "cover": 0.20,
    "stand": 0.15,
    "avatar": 0.10,
}

MAX_MIX_IMAGES = 3   # 拼图最多带几张（token/耗时都按张数涨，hero 永远第一位）


def collect_theme_paths(theme_id: str, exclude_slot: str = "") -> List[Tuple[str, str]]:
    """把一套主题里已填充的图位收成 [(slot, path)]，按权重降序（hero 永远在前）。"""
    out: List[Tuple[str, str]] = []
    for slot in sorted(SLOT_COLOR_WEIGHT, key=lambda s: -SLOT_COLOR_WEIGHT[s]):
        if slot == exclude_slot:
            continue
        p = resolve_slot_path(theme_id, slot)
        if p:
            out.append((slot, p))
    return out


def _contact_sheet(items: List[Tuple[str, str]], height: int = 320) -> Optional[bytes]:
    """多图水平拼成一张「素材板」（每张下缘标注图位名），给 VLM 一次看全。
    PIL 缺失/任一图读不出来 → 返回 None（调用方回落单图）。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    imgs: List[Tuple[str, Any]] = []
    for slot, p in items:
        try:
            with Image.open(p) as im:
                w = max(1, int(im.width * height / max(1, im.height)))
                imgs.append((slot, im.convert("RGB").resize((w, height))))
        except Exception:
            continue
    if not imgs:
        return None
    pad, label_h = 6, 22
    total_w = sum(im.width for _s, im in imgs) + pad * (len(imgs) + 1)
    sheet = Image.new("RGB", (total_w, height + label_h + pad * 2), (24, 24, 28))
    dr = ImageDraw.Draw(sheet)
    x = pad
    for slot, im in imgs:
        sheet.paste(im, (x, pad))
        dr.text((x + 4, pad + height + 3), slot, fill=(235, 235, 235))
        x += im.width + pad
    try:
        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return None


def weighted_seed(items: List[Tuple[str, str]]) -> Optional[Tuple[float, float, float]]:
    """多图加权种子色：色相走圆均值（避免 359° 和 1° 平均出 180° 的灾难），
    饱和/明度按权重加权，最后夹进与单图版相同的输出安全区。"""
    acc_x = acc_y = 0.0
    ws = wl = wsum = 0.0
    for slot, p in items:
        seed = dominant_seed(p)
        if not seed:
            continue
        w = SLOT_COLOR_WEIGHT.get(slot, 0.1)
        h, s, l = seed
        acc_x += math.cos(h * 2 * math.pi) * w
        acc_y += math.sin(h * 2 * math.pi) * w
        ws += s * w
        wl += l * w
        wsum += w
    if wsum <= 0:
        return None
    h = (math.atan2(acc_y, acc_x) / (2 * math.pi)) % 1.0
    s = _clamp(ws / wsum, 0.42, 0.92)
    l = _clamp(wl / wsum, 0.44, 0.62)
    return h, s, l


def _vision_available(vision_router) -> bool:
    if vision_router is None:
        return False
    try:
        v = getattr(vision_router, "vision_available", None)
        return bool(v() if callable(v) else v)
    except Exception:
        return False


def analyze_sync(path: str, slot: str) -> Dict[str, Any]:
    """只跑本地规则（不联网）：几何体检 + 本地色调板。永远可用、绝不抛错。"""
    try:
        w, h = image_size(path)
        fit = slot_fit(w, h, slot)
        seed = dominant_seed(path)
        schemes = tonal_palette(*seed) if seed else []
        return {"fit": fit, "source": "local", "schemes": schemes,
                "seed": _hsl_hex(*seed) if seed else ""}
    except Exception as e:  # noqa: BLE001
        return {"fit": {"level": "unknown", "fit": 0.0, "message": ""},
                "source": "local", "schemes": [], "error": str(e)}


async def analyze_async(path: str, slot: str, vision_router=None,
                        timeout: float = 28.0, theme_id: str = "",
                        mode: str = "dark") -> Dict[str, Any]:
    """几何体检 + 配色。配色优先 VLM（语义），失败/不可用回落本地色调板。

    2026-10-13 两件事：
    · 多图加权：theme_id 给了就把该主题其它图位按权重一起取色（本地圆均值混色；
      VLM 走一张拼接素材板，hero 在最左、提示词里声明它贡献最大）；
    · 降级不再静默：VLM 打了但失败/不可用时，结果里带 fallback 原因，
      编辑器把「这是本地色、不是 AI 色」讲清楚 —— 之前欠费/没配模型时
      表现成"AI 取色异常"，用户根本不知道降级了。
    ⚠️ 无论如何都返回结果、不抛异常 —— 编辑器不该因为配色失败而让「换图」整体失败。
    """
    out = analyze_sync(path, slot)

    # ---- 多图收集：请求图位第一，其余图位按权重带上（最多 MAX_MIX_IMAGES 张） ----
    items: List[Tuple[str, str]] = []
    if slot in SLOT_COLOR_WEIGHT:
        items.append((slot, path))
    for s2, p2 in collect_theme_paths(theme_id, exclude_slot=slot):
        if len(items) >= MAX_MIX_IMAGES:
            break
        items.append((s2, p2))
    multi = len(items) > 1
    if multi:
        try:
            seed = weighted_seed(items)
            if seed:
                out["schemes"] = tonal_palette(*seed)
                out["seed"] = _hsl_hex(*seed)
                out["mix"] = [s for s, _p in items]
        except Exception:
            pass   # 加权失败就保持单图结果，不影响返回

    if not _vision_available(vision_router):
        # 想走 AI 但没得走：写明原因（router 缺失或 vision_available=False）
        out["fallback"] = "未接入可用的视觉模型" if vision_router is None else "视觉模型当前不可用"
        return out

    obj = None
    ai_err = ""
    try:
        import asyncio
        data = _contact_sheet(items) if multi else None
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        text = await asyncio.wait_for(
            vision_router.vision_describe(data, vlm_prompt(mode)), timeout=timeout)
        obj = parse_vlm_json(text or "")
        if not obj:
            ai_err = "模型没返回有效配色 JSON"
    except Exception as e:  # noqa: BLE001
        ai_err = str(e) or "调用失败"
    if not obj:
        out["fallback"] = ai_err
        return out
    try:
        seed = dominant_seed(path)
        # 图像真实色相锚点：给 AI 的强调色做"色相闸"用（见 _ai_hue_off）
        anchors = hue_anchors(path, 3)
        sc = _schemes_from_vlm(obj, seed, mode, anchors)
    except Exception:
        anchors, sc = None, []
    if sc:
        out["schemes"] = sc
        out["source"] = "ai"
        out["mood"] = str(obj.get("mood") or "").strip()
        out["subject"] = str(obj.get("subject") or "").strip()
        if multi:
            out["mix"] = [s for s, _p in items]
        out.pop("fallback", None)
    else:
        out["fallback"] = ("AI 给的色相与这张图不符（已改用图像真实色调）"
                           if _ai_hue_off(obj, anchors, mode) else "AI 配色解析失败")
    return out


def resolve_slot_path(theme_id: str, slot: str, page: str = "") -> str:
    """由 (主题id, 图位) 解析出该图位当前图片的**绝对路径**。找不到返回空串。

    theme_store 把每张图存成 <themes>/<id>/<slot>/<slot><ext>，且会 _prune_others
    只留一张，所以目录里至多一个图片文件。"""
    if not theme_id:
        return ""
    try:
        import os as _os
        from ..ui import theme_store as _ts
        d = _ts.slot_dir(theme_id, slot or "hero", page or "")
        if not _os.path.isdir(d):
            return ""
        for fn in sorted(_os.listdir(d)):
            if _os.path.splitext(fn)[1].lower() in _ts.IMG_EXT:
                return _os.path.join(d, fn)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- 命令行自测

def _cli() -> None:
    import os
    import sys
    argv = sys.argv[1:]
    use_vlm = "--vlm" in argv
    argv = [a for a in argv if a != "--vlm"]
    if not argv:
        base = os.path.join("data", "char_banners", "themes", "danya_33fa9b")
        if not os.path.isdir(base):
            print("用法: python -m src.llm.theme_ai <图片路径> [--vlm]")
            return
        files = []
        for slot in ("hero", "stand", "avatar", "cover"):
            d = os.path.join(base, slot)
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if os.path.splitext(fn)[1].lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        files.append((slot, os.path.join(d, fn)))
        for slot, p in files:
            _report(p, slot, use_vlm)
        return
    _report(argv[0], "hero", use_vlm)


def _report(path: str, slot: str, use_vlm: bool) -> None:
    import asyncio
    if use_vlm:
        res = asyncio.run(analyze_async(path, slot, _try_router(), timeout=30.0))
    else:
        res = analyze_sync(path, slot)
    fit = res.get("fit") or {}
    print("=====", path, "->", SLOT_LABEL.get(slot, slot), "=====")
    print("  几何: %s  fit=%.3f  %s" % (fit.get("level"), fit.get("fit", 0), fit.get("message", "")))
    print("  来源:", res.get("source"), "| mood:", res.get("mood", ""))
    for sc in res.get("schemes", []):
        print("   ", sc["name"], sc["c1"], sc["c2"], "·", sc["note"])


def _try_router(config_path: str = "config.yaml"):
    """尽量接上项目已配置的视觉路由（供命令行自测用）。失败返回 None。

    ⚠️ load_config 需要显式路径（默认 "config.yaml"，与 CharacterAPI 一致）；
    裸调 load_config() 会因缺参数抛 TypeError → 静默降级本地，导致「明明配了 VLM 却永远走本地」。
    """
    import os
    try:
        from ..config import load_config
        from .vision_router import create_vision_router
        path = config_path
        if not os.path.isfile(path):
            for cand in ("config.yaml", os.path.join("..", "config.yaml")):
                if os.path.isfile(cand):
                    path = cand
                    break
        cfg = load_config(path)
        return create_vision_router(cfg)
    except Exception:
        return None


if __name__ == "__main__":
    _cli()
