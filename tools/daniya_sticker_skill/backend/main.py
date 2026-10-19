"""FastAPI 后端：包/表情 CRUD、角色绑定、LLM 接口、资源广场、静态托管 SPA。"""
import logging

from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional

from .store import Store
from . import config, llm, marketplace, autotag as autotag_mod
from .models import PackCreate, PackRename, BindingsUpdate, DescribeRequest, SearchRequest, PickRequest

logger = logging.getLogger(__name__)

store = Store()
app = FastAPI(title="达妮娅表情包技能包(测试版)")
# 测试期：智谱实现。接入达妮娅时把下行替换为 DaniyaLLMBackend(engine)
llm_backend = llm.ZhipuBackend()


# ---------------- packs ----------------
@app.get("/api/packs")
def api_list_packs():
    return store.list_packs()


@app.post("/api/packs")
def api_create_pack(body: PackCreate):
    return store.create_pack(body.name)


@app.patch("/api/packs/{pid}/rename")
def api_rename(pid, body: PackRename):
    if not store.get_pack_summary(pid):
        raise HTTPException(404, "pack not found")
    return store.rename_pack(pid, body.name)


@app.delete("/api/packs/{pid}")
def api_delete(pid):
    store.delete_pack(pid)
    return {"ok": True}


@app.post("/api/packs/{pid}/bindings")
def api_bindings(pid, body: BindingsUpdate):
    if not store.get_pack_summary(pid):
        raise HTTPException(404, "pack not found")
    return store.set_bindings(pid, body.mode, body.characters)


@app.get("/api/packs/{pid}/memes")
def api_list_memes(pid):
    if not store.get_pack_summary(pid):
        raise HTTPException(404, "pack not found")
    return store.list_memes(pid)


@app.post("/api/packs/{pid}/upload")
async def api_upload(pid, files: List[UploadFile] = File(...), autotag: bool = True):
    """上传图片；默认**自动标注**（可 autotag=false 跳过）。

    标注失败不影响入库——图先落地，标签留空，用户可在 UI 里改。
    """
    if not store.get_pack_summary(pid):
        raise HTTPException(404, "pack not found")
    result = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        meme = store.add_meme(pid, data, f.filename or "sticker.png")
        if autotag and llm_backend.available():
            try:
                res = await autotag_mod.autotag_bytes(
                    llm_backend, data, getattr(llm_backend, "vision_model", ""),
                    frames_strategy=config.AUTOTAG_FRAMES, frames_k=config.AUTOTAG_FRAMES_K)
                meta = store._read_meta(pid)
                entry = meta.get(meme["id"])
                if entry is not None:
                    autotag_mod.apply_autotag_to_meta(entry, res)
                    store._write_meta(pid, meta)
                    meme = entry
            except Exception as e:  # noqa: BLE001  标注失败不阻断入库
                logger.warning("自动标注失败（图已入库，可手改）: %s", e)
        result.append(meme)
    return result


@app.post("/api/packs/{pid}/memes/{mid}/autotag")
async def api_autotag_one(pid, mid, force: bool = False):
    """对单张图重新自动标注。force=true 时**连用户手改过的也覆盖**。"""
    m = store.get_meme(pid, mid)
    if not m:
        raise HTTPException(404)
    if not llm_backend.available():
        raise HTTPException(400, "LLM 未配置")
    if m.get("provenance") == "manual" and not force:
        return {"ok": False, "skipped": True,
                "reason": "该图已被手动编辑过，未覆盖（如需覆盖请加 force=true）"}
    fp = store._pack_dir(pid) / "images" / m["file"]
    res = await autotag_mod.autotag_bytes(
        llm_backend, fp.read_bytes(), getattr(llm_backend, "vision_model", ""),
        frames_strategy=config.AUTOTAG_FRAMES, frames_k=config.AUTOTAG_FRAMES_K)
    meta = store._read_meta(pid)
    entry = meta.get(mid)
    if entry is None:
        raise HTTPException(404)
    if force:
        entry["provenance"] = "ai"  # 允许覆盖
    autotag_mod.apply_autotag_to_meta(entry, res)
    store._write_meta(pid, meta)
    return {"ok": not res.get("error"), "result": res, "meme": entry}


@app.post("/api/packs/{pid}/autotag")
async def api_autotag_pack(pid, limit: int = 50, only_missing: bool = True):
    """整包自动标注：默认只标"还没标过、且用户没手改过"的图。"""
    if not store.get_pack_summary(pid):
        raise HTTPException(404, "pack not found")
    if not llm_backend.available():
        raise HTTPException(400, "LLM 未配置")
    memes = store.list_memes(pid)
    targets = [m for m in memes if (not only_missing or autotag_mod.needs_autotag(m))][:limit]
    done = failed = 0
    for m in targets:
        try:
            fp = store._pack_dir(pid) / "images" / m["file"]
            res = await autotag_mod.autotag_bytes(
                llm_backend, fp.read_bytes(), getattr(llm_backend, "vision_model", ""),
                frames_strategy=config.AUTOTAG_FRAMES, frames_k=config.AUTOTAG_FRAMES_K)
            meta = store._read_meta(pid)
            entry = meta.get(m["id"])
            if entry is None:
                continue
            autotag_mod.apply_autotag_to_meta(entry, res)
            store._write_meta(pid, meta)
            if res.get("error"):
                failed += 1
            else:
                done += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {"ok": True, "tagged": done, "failed": failed,
            "skipped": len(memes) - len(targets), "total": len(memes)}


@app.patch("/api/packs/{pid}/memes/{mid}/labels")
def api_edit_labels(pid, mid, body: dict = Body(...)):
    """**UI 手改标签**：情绪词/描述/适用场景/分类/复合语气/反触发/图内文字。

    改完标 provenance=manual —— 之后自动标注不会再覆盖它。
    """
    m = store.get_meme(pid, mid)
    if not m:
        raise HTTPException(404)
    meta = store._read_meta(pid)
    entry = meta.get(mid)
    autotag_mod.manual_update(
        entry,
        description=body.get("description"),
        tags=body.get("tags"),
        trigger=body.get("trigger"),
        category=body.get("category"),
        mood_compound=body.get("mood_compound"),
        anti_trigger=body.get("anti_trigger"),
        visible_text=body.get("visible_text"),
    )
    store._write_meta(pid, meta)
    return {"ok": True, "meme": entry}


@app.get("/api/emotion_vocab")
def api_emotion_vocab():
    """给 UI 的情绪词表（供手改时勾选）。"""
    return autotag_mod.EMOTION_VOCAB


@app.delete("/api/packs/{pid}/memes/{mid}")
def api_del_meme(pid, mid):
    return {"ok": store.delete_meme(pid, mid)}


@app.get("/api/packs/{pid}/images/{mid}")
def api_meme_file(pid, mid):
    m = store.get_meme(pid, mid)
    if not m:
        raise HTTPException(404)
    p = store._pack_dir(pid) / "images" / m["file"]
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.patch("/api/packs/{pid}/memes/{mid}")
def api_update_meme(pid, mid, body: dict = Body(...)):
    res = store.update_meme_meta(
        pid, mid,
        description=body.get("description"),
        tags=body.get("tags"),
        category=body.get("category"),
    )
    if res is None:
        raise HTTPException(404)
    return res


# ---------------- characters ----------------
@app.get("/api/characters")
def api_chars():
    return config.KNOWN_CHARACTERS


# ---------------- llm ----------------
@app.get("/api/llm/status")
def api_llm_status():
    return {"available": llm_backend.available(), "backend": "zhipu" if llm_backend.available() else "none"}


@app.post("/api/llm/describe")
async def api_describe(body: DescribeRequest):
    m = store.get_meme(body.pack_id, body.meme_id)
    if not m:
        raise HTTPException(404)
    p = store._pack_dir(body.pack_id) / "images" / m["file"]
    data = p.read_bytes()
    res = await llm_backend.auto_describe(data, "image/png")
    if res.get("description") or res.get("tags"):
        store.update_meme_meta(body.pack_id, body.meme_id,
                               description=res.get("description", ""),
                               tags=res.get("tags", []))
    return res


@app.post("/api/search")
async def api_search(body: SearchRequest):
    if body.pack_id:
        packs = [store.get_pack_summary(body.pack_id)]
    elif body.character:
        packs = store.packs_for_character(body.character)
    else:
        packs = store.list_packs()
    cands = []
    for p in packs:
        if not p:
            continue
        for m in store.list_memes(p["id"]):
            cands.append({
                "id": m["id"], "pack_id": p["id"],
                "description": m.get("description", ""),
                "tags": m.get("tags", []),
                "category": m.get("category", ""),
                # 【关键修复】trigger 才是"什么时候该用"，此前被丢掉了 ——
                # 导致库里明明有 trigger=「被推开、被说'不用你管'…」的图，
                # 模型却看不到，只能回"没有贴切的"。
                "trigger": m.get("trigger", ""),
                "anti_trigger": ((m.get("style_variants") or {}).get("community") or {}).get("anti_trigger", ""),
                "mood_compound": ((m.get("style_variants") or {}).get("community") or {}).get("mood_compound", ""),
            })
    # 本地关键词粗筛（即使无 LLM 也可用）
    if body.query:
        q = body.query.lower()
        kw = [c for c in cands if q in (c["description"] + " " + str(c["tags"])).lower()]
        if kw:
            cands = kw
    # LLM 语义重排（有 key 时）
    if llm_backend.available() and body.query:
        ranked = await llm_backend.semantic_search(body.query, cands)
        if ranked:
            by_id = {c["id"]: c for c in cands}
            result = [by_id[i] for i in ranked if i in by_id]
            for c in cands:
                if c["id"] not in ranked:
                    result.append(c)
            return result[:body.top_k]
    return cands[:body.top_k]


@app.post("/api/pick")
async def api_pick(body: PickRequest):
    if body.pack_id:
        packs = [store.get_pack_summary(body.pack_id)]
    else:
        packs = store.packs_for_character(body.character)
    cands = []
    cats = {}
    for p in packs:
        if not p:
            continue
        # 收集该包的分类语义表（astrbot 协议），供选贴时按分类判断
        try:
            cats.update(store.get_pack_categories(p["id"]) or {})
        except Exception:  # noqa: BLE001
            pass
        for m in store.list_memes(p["id"]):
            cands.append({
                "id": m["id"], "pack_id": p["id"],
                "description": m.get("description", ""),
                "tags": m.get("tags", []),
                "category": m.get("category", ""),
                # 【关键修复】trigger 才是"什么时候该用"，此前被丢掉了 ——
                # 导致库里明明有 trigger=「被推开、被说'不用你管'…」的图，
                # 模型却看不到，只能回"没有贴切的"。
                "trigger": m.get("trigger", ""),
                "anti_trigger": ((m.get("style_variants") or {}).get("community") or {}).get("anti_trigger", ""),
                "mood_compound": ((m.get("style_variants") or {}).get("community") or {}).get("mood_compound", ""),
            })
    if not llm_backend.available():
        return {"use": False, "meme_id": None,
                "reason": "LLM 未配置（无智谱 key），仅本地管理/搜索可用", "candidates": len(cands)}
    # 把分类语义挂到后端实例上（pick_sticker 内部会引用）
    try:
        llm_backend.category_hints = cats
    except Exception:  # noqa: BLE001
        pass
    res = await llm_backend.pick_sticker(body.character, body.conversation, cands)
    found = next((c for c in cands if c["id"] == res.get("meme_id")), None)
    res["found"] = found is not None
    res["pack_id"] = found["pack_id"] if found else None
    return res


# ---------------- 自动收集（用户发过的表情包）----------------
@app.post("/api/collect/observe")
async def api_collect_observe(body: dict = Body(...)):
    """观察一张用户发来的图片：按"重复采样"闸门决定是否进待审。

    body: {data_b64, ext, sender, pack_id, platform_marked, suggested_category}
    返回 {collected: bool, reason: str, pending: item|None}
    """
    import base64 as _b64
    from .collect import CollectStore, content_hash
    cs = CollectStore(config.DATA_DIR)
    cs.expire()
    raw = body.get("data_b64") or ""
    if not raw:
        return {"collected": False, "reason": "缺 data_b64"}
    try:
        data = _b64.b64decode(raw)
    except Exception as e:  # noqa: BLE001
        return {"collected": False, "reason": f"base64 解码失败：{e}"}
    sender = str(body.get("sender") or "")
    cooling, why = cs.source_cooling(sender)
    if cooling:
        return {"collected": False, "reason": why}
    h = content_hash(data)
    cs.note_seen(h, sender=sender)
    ok, reason = cs.should_sample(h, bool(body.get("platform_marked")))
    if not ok:
        cs.save()
        return {"collected": False, "reason": reason}
    cs.touch_source(sender)
    ext = str(body.get("ext") or ".png")
    if not ext.startswith("."):
        ext = "." + ext
    item = cs.add_pending(data, ext, pack_id=str(body.get("pack_id") or ""), sender=sender,
                          suggested_category=str(body.get("suggested_category") or ""))
    return {"collected": item is not None, "reason": reason, "pending": item}


@app.get("/api/collect/pending")
def api_collect_pending(pack_id: str = ""):
    from .collect import CollectStore
    cs = CollectStore(config.DATA_DIR)
    return {"ok": True, "items": cs.list_pending(pack_id or None), "stats": cs.stats()}


@app.get("/api/collect/pending/{h}/image")
def api_collect_pending_image(h: str):
    from .collect import CollectStore
    from fastapi.responses import Response
    cs = CollectStore(config.DATA_DIR)
    data = cs.read_pending_bytes(h)
    if data is None:
        raise HTTPException(404, "not found")
    return Response(content=data, media_type="image/*")


@app.post("/api/collect/accept")
def api_collect_accept(body: dict = Body(...)):
    """接收待审图片入包（可改分类）。

    body: {hash, category?, pack_id?}。pack_id 可覆盖（收集时记的包可能已被删）。
    """
    from .collect import CollectStore
    cs = CollectStore(config.DATA_DIR)
    h = str(body.get("hash") or "")
    res = cs.accept(h, category=body.get("category"))
    if not res:
        raise HTTPException(404, "not found")
    data, cat, pid = res
    # 目标包可能已被删除 / 从未存在 —— 回退到第一个可用包，避免接收失败丢图。
    pid = str(body.get("pack_id") or pid or "")
    if not pid or store.get_pack_summary(pid) is None:
        packs = store.list_packs()
        if not packs:
            raise HTTPException(400, "没有可用于接收的表情包，请先创建一个包")
        pid = packs[0]["id"]
    ext = "." + (str(body.get("ext") or "png").lstrip("."))
    meme = store.add_meme(pid, data, "collected" + ext, category=cat or "")
    return {"ok": True, "meme": meme, "pack_id": pid}


@app.post("/api/collect/reject")
def api_collect_reject(body: dict = Body(...)):
    from .collect import CollectStore
    cs = CollectStore(config.DATA_DIR)
    return {"ok": cs.reject(str(body.get("hash") or ""),
                            never_again=bool(body.get("never_again", True)))}


# ---------------- marketplace ----------------
@app.get("/api/marketplace")
async def api_marketplace():
    return await marketplace.fetch_index()


@app.post("/api/marketplace/install")
async def api_install(body: dict = Body(...)):
    entry = body.get("entry", {})
    return await marketplace.install_pack(entry, store)


# ---------------- static ----------------
@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
