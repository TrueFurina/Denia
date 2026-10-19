# -*- coding: utf-8 -*-
"""角色图片 → 主题系统：后端存储（2026-10-05）。

设计见 ``_design.txt`` 第十节。这里只管「存取 + 图位校验 + 绑定关系」，
不含任何取色/渲染逻辑（取色在浏览器 Canvas 做，所见即所取）。

落盘布局::

    data/themes.json                      主题包索引 + 角色↔主题绑定
    data/char_banners/themes/<theme_id>/  该主题的四张图 + pages/ 子目录

为什么图放 data/char_banners 下：/local_img 路由的路径白名单只放行这一个目录，
放这里就不用改白名单（改白名单要动 webapp.py 的 do_GET，风险高）。
换主题 = 换一份 images 配置，不复制图片文件。

图片尺寸读取不依赖 Pillow：主运行时（managed py3.13 / 系统 py3.12）都没装，
而 WebView 是无头窗口，tkinter 文件框也只在主线程可用 —— 所以这里手写
PNG/JPEG/GIF/WebP/BMP 的文件头解析，零依赖。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# 四个主页图位 + 页面壁纸
SLOTS = ("hero", "stand", "avatar", "cover")
SLOT_LABELS = {
    "hero": "主页背景",
    "stand": "角色立绘",
    "avatar": "头像",
    "cover": "角色卡头图",
}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# 「继承自角色头图」标记：cover 图位由绑定角色的 banner 派生（只读透出，不落盘）
INHERIT_KEY = "inheritedFromCharacter"

# 各图位建议尺寸（编辑器提示用；落盘时以真实 w/h 为准）
SLOT_HINT = {
    "hero": (1920, 1080),
    "stand": (800, 1000),
    "avatar": (512, 512),
    "cover": (1200, 800),
}


# ---------------------------------------------------------------- 路径

def project_root() -> str:
    """项目根目录：<root>/src/ui/theme_store.py → <root>。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_dir() -> str:
    """数据根目录。测试可 monkeypatch 本函数指向临时目录，避免污染真实 themes.json。"""
    return os.path.join(project_root(), "data")


def themes_json_path() -> str:
    return os.path.join(data_dir(), "themes.json")


def theme_images_dir(theme_id: str) -> str:
    """某主题的图根目录（白名单内，/local_img 可直读）。"""
    return os.path.join(data_dir(), "char_banners", "themes", _safe_id(theme_id))


def _safe_id(s: str) -> str:
    """主题 id 白名单化：只留字母数字下划线连字符，防路径穿越。"""
    s = re.sub(r"[^A-Za-z0-9_-]", "", (s or "").strip())
    return s or "default"


# ---------------------------------------------------------------- 尺寸解析

def image_size(path: str) -> Tuple[int, int]:
    """读图片真实宽高。失败返回 (0, 0)——调用方据此提示「读不到尺寸」但不阻断。"""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if len(head) < 16:
                return (0, 0)
            # PNG: 8 签名 + IHDR
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w = int.from_bytes(head[16:20], "big")
                h = int.from_bytes(head[20:24], "big")
                return (w, h)
            # GIF: 6 头 + LSD
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w = int.from_bytes(head[6:8], "little")
                h = int.from_bytes(head[8:10], "little")
                return (w, h)
            # BMP
            if head[:2] == b"BM":
                w = int.from_bytes(head[18:22], "little")
                h = int.from_bytes(head[22:26], "little")
                return (w, h)
            # WebP: RIFF....WEBP
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp_size(f)
            # JPEG: 逐段跳标记找 SOFn
            if head[:2] == b"\xff\xd8":
                return _jpeg_size(f)
    except Exception:  # noqa: BLE001 —— 尺寸只是辅助信息，读不到不能阻断上传
        return (0, 0)
    return (0, 0)


def _webp_size(f) -> Tuple[int, int]:
    f.seek(12)
    tag = f.read(4)
    if tag == b"VP8X":
        d = f.read(10)
        w = 1 + int.from_bytes(d[4:7], "little")
        h = 1 + int.from_bytes(d[7:10], "little")
        return (w, h)
    if tag == b"VP8 ":
        d = f.read(10)
        return (int.from_bytes(d[6:8], "little") & 0x3FFF,
                int.from_bytes(d[8:10], "little") & 0x3FFF)
    if tag == b"VP8L":
        d = f.read(5)
        b = int.from_bytes(d[1:5], "little")
        return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
    return (0, 0)


def _jpeg_size(f) -> Tuple[int, int]:
    f.seek(2)
    while True:
        b = f.read(1)
        if not b:
            return (0, 0)
        if b != b"\xff":
            continue
        # 跳过填充 0xFF
        while b == b"\xff":
            b = f.read(1)
        if not b:
            return (0, 0)
        marker = b[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        seg = f.read(2)
        if len(seg) < 2:
            return (0, 0)
        ln = int.from_bytes(seg, "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            d = f.read(5)
            if len(d) < 5:
                return (0, 0)
            h = int.from_bytes(d[1:3], "big")
            w = int.from_bytes(d[3:5], "big")
            return (w, h)
        if ln < 2:
            return (0, 0)
        f.seek(ln - 2, os.SEEK_CUR)


# ---------------------------------------------------------------- 存取

_LOCK = threading.RLock()


def _blank() -> Dict[str, Any]:
    return {"themes": {}, "bindings": {}}


def _heal_theme_urls(data: Dict[str, Any]) -> None:
    """【2026-10-19 新增·主题图路径自愈】就地修复失效的图片 URL。

    根因：_slot_url 生成时把**当时的绝对路径**固化进 themes.json。
    换机器 / 换安装目录后（实测：H:\\aibanlv → H:\\Jiyulian），这些路径全部
    指向不存在的位置 → 壁纸/头像/立绘全 404，连带"从壁纸自动取色"失败
    → 表现为"达妮娅压根没有主题色和主题壁纸"。
    而图片文件本体其实随包分发（data/char_banners/themes/<id>/<slot>/）。

    修法：加载时逐图位检查 url 指向的文件是否存在；不存在则按约定路径
    <data>/char_banners/themes/<id>/<slot>/<file> 重建——文件在就直接修好，
    顺手把修好的 URL 写回 json（save_all），下次启动零成本。
    """
    themes = data.get("themes")
    if not isinstance(themes, dict):
        return
    changed = False
    base = os.path.abspath(os.path.join(data_dir(), "char_banners"))
    for tid, th in themes.items():
        if not isinstance(th, dict):
            continue
        imgs = th.get("images")
        if not isinstance(imgs, dict):
            continue
        for slot in ("hero", "stand", "avatar", "cover"):
            v = imgs.get(slot)
            cur_url = (v or {}).get("url") if isinstance(v, dict) else None
            # 已有效（url 非空 且 文件存在 且 **在当前安装的白名单内**）→ 跳过
            # 【2026-10-19 关键修正】只查"文件存在"不够——实测 H:\aibanlv 旧安装
            # 残留目录里同文件名图片也存在，文件存在但 /local_img 白名单会拒掉它
            # （白名单只放行**当前安装根**的 data/char_banners）→ 依然 404 无壁纸。
            # 有效性 = 文件存在 **且** 位于当前 data_dir()/char_banners 之下。
            if cur_url and "path=" in cur_url:
                try:
                    from urllib.parse import unquote
                    _p = os.path.abspath(unquote(cur_url.split("path=", 1)[-1])
                                         .replace("/", os.sep))
                    if os.path.isfile(_p) and _p.startswith(base + os.sep):
                        continue
                except Exception:  # noqa: BLE001
                    pass
            # 尝试约定路径
            cand = os.path.join(theme_images_dir(tid), slot,
                                "%s.png" % slot)
            if not os.path.isfile(cand):
                # 再试 jpg 变体
                cand = os.path.join(theme_images_dir(tid), slot,
                                    "%s.jpg" % slot)
            if os.path.isfile(cand):
                new_url = _slot_url(cand)
                if new_url:
                    if not isinstance(v, dict):
                        v = {}
                    v["url"] = new_url
                    v["file"] = "%s/%s" % (slot, os.path.basename(cand))
                    imgs[slot] = v
                    changed = True
            elif cur_url:
                # 文件确实不在：清掉死 URL（避免前端一直请求 404）
                if isinstance(v, dict):
                    v["url"] = ""
                    imgs[slot] = v
                    changed = True
    if changed:
        try:
            save_all(data)
        except Exception:  # noqa: BLE001
            pass


def load_all() -> Dict[str, Any]:
    """读整个 themes.json。文件不存在/损坏都返回空骨架（不抛，前端仍能用）。

    【2026-10-19】加载后执行主题图路径自愈（_heal_theme_urls）——
    换机/换目录后固化在 json 里的绝对路径会全部失效，这里统一修复。
    """
    p = themes_json_path()
    with _LOCK:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return _blank()
            d.setdefault("themes", {})
            d.setdefault("bindings", {})
            _heal_theme_urls(d)
            return d
        except FileNotFoundError:
            return _blank()
        except Exception:  # noqa: BLE001 存档损坏不该让整个设置页打不开
            return _blank()


def save_all(data: Dict[str, Any]) -> bool:
    p = themes_json_path()
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)   # 原子替换：中途崩也不会留下半个坏 JSON
            return True
        except Exception:  # noqa: BLE001
            return False


def new_theme_id(name: str) -> str:
    base = _safe_id(name).lower()[:24] or "theme"
    return "%s_%s" % (base, uuid.uuid4().hex[:6])


def default_theme(theme_id: str, name: str) -> Dict[str, Any]:
    return {
        "id": theme_id,
        "name": name or "未命名主题",
        "boundCharacter": None,
        "images": {"hero": None, "stand": None, "avatar": None, "cover": None,
                   "pages": {}},
        "theme": {"source": "images.hero", "accent1": "", "accent2": "",
                  "pickedAt": 0, "auto": True},
        "pageWallpaper": False,
        "createdAt": int(time.time()),
        "updatedAt": int(time.time()),
    }


# ---------------------------------------------------------------- 图位

def _slot_url(abs_path: str) -> str:
    """绝对路径 → /local_img URL。必须在 char_banners 下才有效。

    必须给**绝对路径**：路由里是 `os.path.abspath(_p)`，传相对路径会按服务端 CWD
    解析，而 WebView 启动时 CWD 不保证是项目根 → 图片 404（现有 pick_character_banner
    的调用方也是传绝对路径，保持一致）。
    """
    from urllib.parse import quote
    ap = os.path.abspath(abs_path)
    base = os.path.abspath(os.path.join(data_dir(), "char_banners"))
    # 越界就不给 URL：宁可编辑器显示"无图"，也别吐一个必然 404 的地址
    if not ap.startswith(base + os.sep):
        return ""
    return "/local_img?path=" + quote(ap.replace("\\", "/"), safe="")


def _describe(abs_path: str, focus: str = "") -> Dict[str, Any]:
    w, h = image_size(abs_path)
    return {
        "url": _slot_url(abs_path),
        "name": os.path.basename(abs_path),
        "w": w,
        "h": h,
        "focus": focus or "50% 40%",
    }


def slot_dir(theme_id: str, slot: str, page: str = "") -> str:
    """图位目录：**每个图位一个独立子目录**。

    ⚠️ 早先四个图位共用 themes/<id>/ 一个目录，导致 _prune_others 把「上一张图」
    当旧图删掉 —— 存三张图只剩最后一张（存 cover 就只剩 cover）。图位必须隔离。
    页面壁纸再嵌一层 pages/<page>/。"""
    base = theme_images_dir(theme_id)
    if page:
        return os.path.join(base, "pages", _safe_id(page))
    return os.path.join(base, _safe_id(slot or "hero"))


def list_slot_files(theme_id: str, slot: str, page: str = "") -> List[Dict[str, Any]]:
    """列出该图位目录里已有的图片（用户可以自己把图丢进来，故必须能扫目录）。"""
    d = slot_dir(theme_id, slot, page)
    out: List[Dict[str, Any]] = []
    try:
        for fn in sorted(os.listdir(d)):
            if os.path.splitext(fn)[1].lower() not in IMG_EXT:
                continue
            ap = os.path.join(d, fn)
            if not os.path.isfile(ap):
                continue
            out.append(_describe(ap))
    except Exception:  # noqa: BLE001 目录不存在=还没图，正常
        pass
    return out


def save_slot_image(theme_id: str, slot: str, src_path: str,
                    page: str = "") -> Dict[str, Any]:
    """把用户选的图复制进该主题的**该图位独立目录**，返回可直接给 <img> 的描述。"""
    tid = _safe_id(theme_id)
    if slot not in SLOTS and not page:
        return {}
    if not src_path or not os.path.isfile(src_path):
        return {}
    d = slot_dir(tid, slot, page)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return {"error": "建目录失败: %s" % e}

    ext = os.path.splitext(src_path)[1].lower()
    if ext not in IMG_EXT:
        ext = ".png"
    # 文件名用图位名，语义清楚（themes/<id>/stand/stand.png）
    dst = os.path.join(d, "%s%s" % (_safe_id(slot or "wall"), ext))
    try:
        if os.path.abspath(src_path) != os.path.abspath(dst):
            shutil.copyfile(src_path, dst)
        else:
            # 源就是目标（如用户把图直接丢进了图位目录）：只做尺寸记录，不复制
            pass
    except Exception as e:  # noqa: BLE001
        return {"error": "复制失败: %s" % e}
    # 只清「除新图以外」的旧图（不同扩展名的旧版本，如 stand.png → stand.jpg）
    _prune_others(d, os.path.basename(dst))
    return _describe(dst)


def _prune_others(d: str, keep: str) -> None:
    try:
        for fn in os.listdir(d):
            if fn == keep:
                continue
            if os.path.splitext(fn)[1].lower() in IMG_EXT:
                try:
                    os.remove(os.path.join(d, fn))
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 贴纸 / 表情包
# 贴纸与「图位」语义不同：图位是【一个位置一张图】（换图就覆盖），贴纸是
# 【一个主题多枚、各自独立文件 + 自由摆放/缩放】的小贴图（表情包）。
# 所以不复用 save_slot_image（它会 _prune_others 把别的贴纸当旧图删掉）。

STICKER_EXT = IMG_EXT | {".mp4", ".webm", ".mov"}
STICKER_MAX_BYTES = 48 * 1024 * 1024      # 单枚贴纸体积上限（视频表情包也够用）


def sticker_dir(theme_id: str) -> str:
    return os.path.join(theme_images_dir(theme_id), "stickers")


def save_sticker_image(theme_id: str, src_path: str, sticker_id: str = "") -> Dict[str, Any]:
    """把一枚贴纸复制进 themes/<id>/stickers/，返回可直接给 <img>/<video> 的描述。

    ⚠️ 一个贴纸一个文件（st_<sid>.<ext>），互不覆盖 —— 这是与 save_slot_image
       （一图位一图、换图即清旧）的本质区别。
    """
    tid = _safe_id(theme_id)
    if not src_path or not os.path.isfile(src_path):
        return {}
    try:
        if os.path.getsize(src_path) > STICKER_MAX_BYTES:
            return {"error": "这张贴纸超过 48MB，先压缩一下再贴"}
    except OSError:
        pass
    d = sticker_dir(tid)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return {"error": "建目录失败: %s" % e}
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in STICKER_EXT:
        ext = ".png"
    sid = _safe_id(sticker_id or ("%d" % int(time.time() * 1000 % 10**12)))
    dst = os.path.join(d, "st_%s%s" % (sid, ext))
    try:
        if os.path.abspath(src_path) != os.path.abspath(dst):
            shutil.copyfile(src_path, dst)
    except Exception as e:  # noqa: BLE001
        return {"error": "复制失败: %s" % e}
    rec = _describe(dst)
    rec["stickerId"] = sid
    rec["kind"] = "video" if ext in (".mp4", ".webm", ".mov") else "image"
    return rec


def delete_sticker_file(theme_id: str, name: str) -> bool:
    """删除一枚贴纸的落盘文件（移除贴纸时调用，避免孤儿文件堆积）。"""
    tid = _safe_id(theme_id)
    if not name:
        return False
    ap = os.path.join(sticker_dir(tid), os.path.basename(name))
    if not os.path.isfile(ap):
        return False
    try:
        os.remove(ap)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 绑定

def describe_inherited_cover(banner_path: str, focus: str = "") -> Dict[str, Any]:
    """角色头图 → 主题封面图位记录（**继承态**：只读透出，绝不落盘）。

    2026-10-12 用户裁决「头图与背景板合二为一」：
    绑定的角色已有头图时，主题的 cover 图位就透出这张头图 —— 主题编辑器
    不再要求用户重传一张背景板占位。带 inheritedFromCharacter 标记，
    t_save 会把它丢掉（派生数据不写进主题包），解绑/换角色/删头图即自动还原。
    """
    p = str(banner_path or "").strip()
    if not p:
        return {}
    try:
        ap = os.path.abspath(p)
        if not os.path.isfile(ap):
            return {}
        rec = _describe(ap, focus)
    except Exception:  # noqa: BLE001 读图失败按"无头图"处理，不影响主题读写
        return {}
    if not rec.get("url"):
        return {}
    rec[INHERIT_KEY] = True
    return rec


def apply_bound_cover(images: Dict[str, Any], banner_path: str) -> Dict[str, Any]:
    """把「绑定角色有头图」叠加到主题 images（返回副本，不改原字典）。

    · 角色**有**头图 → cover = 该头图（角色头图优先，原背景板让位）；
    · 角色**没有**头图 → 原样返回；此时主题封面会在渲染层自动成为她的卡头图。
    """
    out = dict(images or {})
    old = out.get("cover") or {}
    rec = describe_inherited_cover(banner_path, (old.get("focus") or "") if isinstance(old, dict) else "")
    if rec:
        out["cover"] = rec
    return out


def bind(theme_id: str, char_id: str) -> Dict[str, Any]:
    """双向绑定：角色↔主题。任一侧传空串即解绑。"""
    d = load_all()
    themes, bindings = d["themes"], d["bindings"]
    tid = (theme_id or "").strip()
    cid = (char_id or "").strip()

    if not tid:
        return {"success": False, "error": "缺少主题 id"}

    # 同一角色只能绑一个主题：先把该角色旧绑定的主题的 boundCharacter 清掉
    if cid:
        old = bindings.get(cid, "")
        if old and old != tid and old in themes:
            themes[old]["boundCharacter"] = None

    if not cid:
        # 解绑：谁在绑就解谁
        for k, v in list(bindings.items()):
            if v == tid:
                bindings.pop(k, None)
        if tid in themes:
            themes[tid]["boundCharacter"] = None
    else:
        bindings[cid] = tid
        if tid in themes:
            themes[tid]["boundCharacter"] = cid

    d["themes"] = themes
    d["bindings"] = bindings
    save_all(d)
    return {"success": True, "bindings": bindings}


def theme_for_char(char_id: str) -> Optional[Dict[str, Any]]:
    d = load_all()
    tid = d["bindings"].get(char_id, "")
    return d["themes"].get(tid) if tid else None
