"""存储层：全局 registry + 每包 manifest/元信息。

数据布局（data/ 运行时生成）：
  data/registry.json               全局包索引（含绑定信息，供聊天选包快速枚举）
  data/packs/<pack_id>/
      pack.json                   包元信息 {id,name,created_at,bindings,description}
      meta.json                   {meme_id: {id,file,category,description,tags}}
      images/<meme_id>.<ext>      图片文件

绑定语义：
  mode="none"       -> 对所有角色可见（默认包）
  mode="whitelist"  -> 仅 characters 列表中的角色可用
  mode="blacklist"  -> characters 列表中的角色不可用，其余可用
"""
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import config

EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def sniff_ext(data: bytes, fallback: str = ".png") -> str:
    """按**文件内容魔数**判断真实扩展名（不信任文件名）。

    为什么必须这样做：表情包来源五花八门，实测整个测试库 **24/24 张都是
    GIF 内容却叫 .jpg**。按名字存 → 扩展名与内容不符 → 前端 Content-Type 错
    → **动图永远不播**（用户反馈"表情包能不能播放"）。
    """
    try:
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:2] == b"\xff\xd8":
            return ".jpg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[:2] == b"BM":
            return ".bmp"
    except Exception:  # noqa: BLE001
        pass
    return fallback


class Store:
    def __init__(self):
        self.data_dir = config.DATA_DIR
        self.packs_dir = config.PACKS_DIR
        self.packs_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.data_dir / "registry.json"
        if not self.registry_path.exists():
            self._write_registry({"packs": []})

    # ---------- registry ----------
    def _read_registry(self):
        with open(self.registry_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_registry(self, reg):
        tmp = self.registry_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.registry_path)

    # ---------- pack paths ----------
    def _pack_dir(self, pid):
        return self.packs_dir / pid

    def _manifest_path(self, pid):
        return self._pack_dir(pid) / "pack.json"

    def _meta_path(self, pid):
        return self._pack_dir(pid) / "meta.json"

    def _read_manifest(self, pid):
        with open(self._manifest_path(pid), "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_manifest(self, pid, m):
        with open(self._manifest_path(pid), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)

    def _read_meta(self, pid):
        p = self._meta_path(pid)
        if not p.exists():
            return {}
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_meta(self, pid, m):
        tmp = self._meta_path(pid).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._meta_path(pid))

    # ---------- pack ops ----------
    def list_packs(self):
        return self._read_registry()["packs"]

    def get_pack_summary(self, pid):
        for p in self._read_registry()["packs"]:
            if p["id"] == pid:
                return p
        return None

    def create_pack(self, name):
        pid = uuid.uuid4().hex[:12]
        d = self._pack_dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "images").mkdir(exist_ok=True)
        manifest = {
            "id": pid,
            "name": name,
            "created_at": _now(),
            "bindings": {"mode": "none", "characters": []},
            "description": "",
        }
        self._write_manifest(pid, manifest)
        self._write_meta(pid, {})
        reg = self._read_registry()
        reg["packs"].append(
            {
                "id": pid,
                "name": name,
                "created_at": manifest["created_at"],
                "bindings": manifest["bindings"],
                "meme_count": 0,
            }
        )
        self._write_registry(reg)
        return self.get_pack_summary(pid)

    def rename_pack(self, pid, name):
        manifest = self._read_manifest(pid)
        manifest["name"] = name
        self._write_manifest(pid, manifest)
        reg = self._read_registry()
        for p in reg["packs"]:
            if p["id"] == pid:
                p["name"] = name
        self._write_registry(reg)
        return self.get_pack_summary(pid)

    def delete_pack(self, pid):
        d = self._pack_dir(pid)
        if d.exists():
            shutil.rmtree(d)
        reg = self._read_registry()
        reg["packs"] = [p for p in reg["packs"] if p["id"] != pid]
        self._write_registry(reg)

    def set_bindings(self, pid, mode, characters):
        manifest = self._read_manifest(pid)
        manifest["bindings"] = {"mode": mode, "characters": characters}
        self._write_manifest(pid, manifest)
        reg = self._read_registry()
        for p in reg["packs"]:
            if p["id"] == pid:
                p["bindings"] = manifest["bindings"]
        self._write_registry(reg)
        return self.get_pack_summary(pid)

    # ---------- meme ops ----------
    def add_meme(self, pid, data: bytes, filename: str, category="", description="", tags=None):
        # 目标包必须真实存在，否则会写出一个"幽灵目录"（图片落在没有 pack.json 的
        # 目录里，registry 又不认识它）——实测踩过：收集时带的 pack_id 是失效的。
        if self.get_pack_summary(pid) is None:
            raise ValueError(f"包不存在：{pid}")
        ext = os.path.splitext(filename)[1].lower()
        if ext not in EXTS:
            ext = ".png"
        # ⭐ 以**内容**为准修正扩展名：文件名常与实际格式不符（实测 24/24 是 GIF 叫 .jpg），
        #    扩展名错 → 前端拿到的 Content-Type 错 → 动图不播。
        ext = sniff_ext(data, ext)
        (self._pack_dir(pid) / "images").mkdir(parents=True, exist_ok=True)
        mid = uuid.uuid4().hex[:12]
        fname = mid + ext
        (self._pack_dir(pid) / "images" / fname).write_bytes(data)
        meta = self._read_meta(pid)
        meta[mid] = {
            "id": mid,
            "file": fname,
            "category": category,
            "description": description,
            "tags": tags or [],
        }
        self._write_meta(pid, meta)
        self._bump_count(pid, len(meta))
        return meta[mid]

    def list_memes(self, pid):
        return list(self._read_meta(pid).values())

    def get_meme(self, pid, mid):
        return self._read_meta(pid).get(mid)

    def delete_meme(self, pid, mid):
        meta = self._read_meta(pid)
        if mid in meta:
            fp = self._pack_dir(pid) / "images" / meta[mid]["file"]
            if fp.exists():
                fp.unlink()
            del meta[mid]
            self._write_meta(pid, meta)
            self._bump_count(pid, len(meta))
            return True
        return False

    def update_meme_meta(self, pid, mid, description=None, tags=None, category=None):
        meta = self._read_meta(pid)
        if mid not in meta:
            return None
        if description is not None:
            meta[mid]["description"] = description
        if tags is not None:
            meta[mid]["tags"] = tags
        if category is not None:
            meta[mid]["category"] = category
        self._write_meta(pid, meta)
        return meta[mid]

    def _bump_count(self, pid, count):
        reg = self._read_registry()
        for p in reg["packs"]:
            if p["id"] == pid:
                p["meme_count"] = count
        self._write_registry(reg)

    # ---------- pack categories（astrbot manifest.categories 落库）----------
    def set_pack_categories(self, pid, categories: dict):
        """把包 manifest 里的分类描述存下来。

        astrbot 的包用「语义分类目录」组织（angry/happy/sad/shy…），每个分类自带
        一句"什么时候该用"的描述。这正是选贴时最有价值的语义线索——比单张图的
        自由标签稳定得多，所以原样保留、供选贴提示词引用。
        """
        manifest = self._read_manifest(pid)
        manifest["categories"] = categories or {}
        self._write_manifest(pid, manifest)
        return manifest.get("categories", {})

    def get_pack_categories(self, pid):
        try:
            return (self._read_manifest(pid) or {}).get("categories") or {}
        except Exception:  # noqa: BLE001
            return {}

    # ---------- character binding helper ----------
    def packs_for_character(self, character):
        result = []
        for p in self.list_packs():
            b = p.get("bindings", {})
            mode = b.get("mode", "none")
            chars = b.get("characters", [])
            if mode == "none":
                result.append(p)
            elif mode == "whitelist":
                if character in chars:
                    result.append(p)
            elif mode == "blacklist":
                if character not in chars:
                    result.append(p)
        return result
