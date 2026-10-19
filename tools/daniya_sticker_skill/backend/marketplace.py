"""资源广场：对接 astrbot 官方「表情包广场」索引（meme-pack-index）。

与真实协议对齐（此前实现是错的，会拒掉所有真实条目）：
- 索引文件是 **`community-index.json`**（不是 `index.json`），结构：
  {schema_version, generated_at, packs:[{id,name,version,maintainer,description,
   license,verified,tags,previews:[...],source:{type:github,repo,ref,subpath}}]}
- 条目**没有 zip 下载地址**，`source` 指向一个 GitHub 仓库 + 分支 + 子目录。
- 包内布局：`manifest.json` + `memes/<category>/<img>`（按**语义分类目录**组织）。
- `raw.githubusercontent.com` 在部分网络下不可达，故默认走 jsDelivr 镜像并自动回退。

安装策略：用 GitHub API 列目录（jsDelivr 也能列），逐张下载图片，按分类写入本地包。
"""
import os
import httpx
from . import config


def _mirrors(repo: str, ref: str, subpath: str = "") -> list[str]:
    """给定 repo/ref/subpath，返回可尝试的 CDN 前缀（按可用性排序）。

    【2026-10-15 实测（国内网络）】
    - cdn.jsdelivr.net：主域名，DNS 时好时坏（Fastly 被污染），仍保留打头；
    - testingcf.jsdelivr.net：Cloudflare 边缘，直连/代理均稳，是实测里唯一全绿的；
    - fastly.jsdelivr.net：Fastly 专用域，部分网络可达；
    - raw.githubusercontent.com：基本不可达，垫底保底。
    """
    sub = f"/{subpath.strip('/')}" if subpath and subpath.strip("/") not in ("", ".") else ""
    return [
        f"https://cdn.jsdelivr.net/gh/{repo}@{ref}{sub}",
        f"https://testingcf.jsdelivr.net/gh/{repo}@{ref}{sub}",
        f"https://fastly.jsdelivr.net/gh/{repo}@{ref}{sub}",
        f"https://raw.githubusercontent.com/{repo}/{ref}{sub}",
    ]


async def _get_json(client, url):
    r = await client.get(url)
    r.raise_for_status()
    return r.json()


async def _get_json_mirrored(client, repo, ref, subpath, filename):
    """依次尝试各镜像取一个 JSON 文件。"""
    last = None
    for base in _mirrors(repo, ref, subpath):
        try:
            return await _get_json(client, f"{base}/{filename}")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"取 {filename} 失败（所有镜像均不可用）：{last}")


async def fetch_index():
    """拉取社区索引，返回统一结构 {ok, entries, source}。"""
    urls = [config.MARKETPLACE_INDEX_URL] + list(config.MARKETPLACE_INDEX_MIRRORS)
    last_err = None
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, trust_env=False) as c:
        for url in urls:
            try:
                data = await _get_json(c, url)
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
            if isinstance(data, dict):
                entries = data.get("packs") or data.get("entries") or data.get("items") or []
            elif isinstance(data, list):
                entries = data
            else:
                entries = []
            return {"ok": True, "entries": entries, "source": url,
                    "generated_at": (data or {}).get("generated_at") if isinstance(data, dict) else None}
    return {"ok": False, "error": f"索引不可达：{last_err}", "entries": []}


def _entry_source(entry: dict) -> dict:
    """取出 source（兼容旧的扁平写法）。"""
    src = entry.get("source") or {}
    if isinstance(src, dict) and src.get("repo"):
        return {"repo": src["repo"], "ref": src.get("ref") or "main",
                "subpath": src.get("subpath") or "."}
    # 兼容：直接从 repo 字段或 GitHub URL 推断
    repo = entry.get("repo") or ""
    if not repo and isinstance(entry.get("url"), str) and "github.com" in entry["url"]:
        parts = entry["url"].rstrip("/").split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            repo = f"{parts[0]}/{parts[1]}"
    return {"repo": repo, "ref": entry.get("ref") or "main", "subpath": entry.get("subpath") or "."}


async def install_pack(entry: dict, store, limit: int = 400, on_progress=None,
                       cancel_event=None, base_pid: str = ""):
    """按 astrbot 包协议安装一个表情包。

    流程：读 manifest.json（拿分类描述）→ 列 memes/ 下的分类目录 →
          逐张下载图片 → 写入本地包（category + 图片）。

    cancel_event：threading.Event，置位后在下一张图下载前停止（暂停语义）。
    base_pid：续传目标包 id——不新建包，且跳过前 skip 张（断点续传：
    add_meme 会把图片重命名成 uuid 文件名，无法按名字去重，但安装顺序
    是确定性的，跳过前 N 张即可精确续传）。
    返回值新增：cancelled=True（被暂停打断）、skipped=N（续传跳过数）。
    """
    src = _entry_source(entry)
    if not src["repo"]:
        return {"ok": False, "error": "该条目缺少 source.repo，无法安装"}
    repo, ref, subpath = src["repo"], src["ref"], src["subpath"]
    name = entry.get("name") or entry.get("id") or "market-pack"

    # 1) manifest（拿分类描述 —— 选贴时最有价值的语义线索）
    #    ⚠️ 必须用 async with 包住 client：早前写成 `httpx.AsyncClient(...)` 裸调，
    #       连接不复用也不关闭，实测在部分网络下静默拿不到 manifest
    #       → categories 恒为 0（包能装、分类却丢了）。
    manifest = {}
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False,
                                     follow_redirects=True) as mc:
            for base in _mirrors(repo, ref, subpath):
                try:
                    r = await mc.get(f"{base}/manifest.json")
                    r.raise_for_status()
                    manifest = r.json()
                    break
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        manifest = {}
    categories = manifest.get("categories") or {}

    # 续传：复用已有包；否则新建。skip = 已导入张数（按顺序跳过）。
    skip = 0
    if base_pid:
        exists = any(p.get("id") == base_pid for p in store.list_packs())
        if exists:
            pid = base_pid
            skip = len(store.list_memes(base_pid))
        else:
            pid = store.create_pack(name)["id"]
    else:
        pid = store.create_pack(name)["id"]
    imported = 0
    cancelled = False
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True, trust_env=False) as c:
            # 2) 列目录：先 jsDelivr 数据 API，失败再用 GitHub API 兜底。
            #    【2026-10-15 实测】data.jsdelivr.com 在部分网络下被 IP 级 403
            #    （换浏览器 UA 也拒），而 api.github.com 可达 —— 没有兜底时
            #    所有包都报「无法列出该包的图片」。GitHub API 未认证限流
            #    60 次/时，个人装包频率足够。
            #    注意 jsDelivr 端点是 /v1/package/（单数）；/v1/packages/ 会返回 400。
            base_sub = f"/{subpath.strip('/')}" if subpath.strip("/") not in ("", ".") else ""
            files = []
            for api in (f"https://data.jsdelivr.com/v1/package/gh/{repo}@{ref}{base_sub}/flat",
                        f"https://data.jsdelivr.com/v1/package/gh/{repo}@{ref}/flat"):
                try:
                    data = await _get_json(c, api)
                    files = [f["name"] for f in (data.get("files") or [])]
                    if files:
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not files:
                # GitHub git-trees 兜底：一次拿全仓库路径（含子目录），按 subpath 过滤。
                try:
                    gh = await c.get(
                        f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1",
                        headers={"User-Agent": "daniya-sticker",
                                 "Accept": "application/vnd.github+json"})
                    gh.raise_for_status()
                    tree = (gh.json().get("tree")) or []
                    pref = subpath.strip("/")
                    files = [t["path"] for t in tree
                             if t.get("type") == "blob"
                             and (not pref or pref == "." or t["path"].startswith(pref + "/"))]
                except Exception:  # noqa: BLE001
                    files = []

            exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")
            img_files = [f for f in files if f.lower().endswith(exts)]
            targets = img_files[:max(1, int(limit))]
            total = len(targets)
            if on_progress:
                try:
                    on_progress(0, total, "")
                except Exception:  # noqa: BLE001
                    pass
            if not img_files:
                store.delete_pack(pid)
                return {"ok": False,
                        "error": "无法列出该包的图片（jsDelivr 列目录失败或无图片）。"
                                 "可改用 GitHub 仓库地址直接安装。"}

            bases = _mirrors(repo, ref, subpath)
            for rel in targets[skip:]:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                rel_clean = rel.lstrip("/")
                # 这些包的文件名普遍带中文和空格（如 memes/affection/0106_爱心 1.gif），
                # 必须逐段 quote（保留 "/"），否则部分镜像/代理直接拒。
                from urllib.parse import quote as _q
                rel_enc = _q(rel_clean)
                data = None
                for base in bases:
                    try:
                        r = await c.get(f"{base}/{rel_enc}")
                        r.raise_for_status()
                        data = r.content
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if not data:
                    continue
                # memes/<category>/<file> → 取 category
                parts = rel_clean.split("/")
                cat = ""
                if "memes" in parts:
                    i = parts.index("memes")
                    if len(parts) > i + 2:
                        cat = parts[i + 1]
                store.add_meme(pid, data, os.path.basename(rel_clean), category=cat)
                imported += 1
                if on_progress:
                    try:
                        on_progress(skip + imported, total, os.path.basename(rel_clean))
                    except Exception:  # noqa: BLE001
                        pass

        if cancelled:
            # 暂停语义：保留已有部分包（含纯续传未新增的），供「继续」接着下
            if imported == 0 and skip == 0:
                store.delete_pack(pid)
            else:
                try:
                    store.set_pack_categories(pid, categories)
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": True, "cancelled": True, "pack_id": pid if (imported or skip) else "",
                    "name": name, "imported": imported + skip, "total": total,
                    "skipped": skip, "categories": len(categories)}

        # 注意：续传（skip>0）时 imported==0 是正常情况——剩下的本来就已下完，
        # 不能当成失败删包；只有全新安装一张都没下到才算失败。
        if imported == 0 and skip == 0:
            store.delete_pack(pid)
            return {"ok": False, "error": "所有镜像都无法下载图片"}
        # 把 manifest 的分类描述写进包，供选贴时作为语义提示
        try:
            store.set_pack_categories(pid, categories)
        except Exception:  # noqa: BLE001
            pass
        if on_progress:
            try:
                on_progress(total, total, "")
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "pack_id": pid, "name": name, "imported": imported + skip,
                "skipped": skip, "categories": len(categories)}
    except Exception as e:  # noqa: BLE001
        store.delete_pack(pid)
        return {"ok": False, "error": str(e)}
