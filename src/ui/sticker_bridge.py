"""表情包技能包 · 接入主程序的桥接层（**不改 webapp.py 的既有逻辑**）。

为什么这样接：
- 主程序 UI 走 pywebview 的 `js_api` 桥（`CharacterAPI`），不是 HTTP 路由。
  子页（redesign/*.html）通过 `Backend.xxx()` 调用 `window.pywebview.api.xxx`。
- 表情包技能包本身是个独立的小型服务（FastAPI + 本地文件存储），
  直接搬进 webapp.py 会污染主程序。这里用**薄适配器**：
    前端 JS  ->  Backend.sticker_*()  ->  StickerBridge  ->  技能包 backend.*
- 技能包目录通过环境变量/相对路径定位；**找不到时优雅降级**（返回结构化错误，
  前端显示提示，绝不抛异常拖垮 UI）。

设计约束（与开发者确认）：
- 分类权限交给用户：自动标注只给初稿，用户可在 UI 改；改过的不被覆盖。
- 识别用的是主程序当前配置的**视觉模型**（config.yaml 的 vision_models.active），
  而不是技能包自己的智谱端口——这样「欠费/换模型」在设置页一处生效。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 技能包位置：优先环境变量，其次与主程序同级目录，最后几个常见位置
_ENV_KEY = "DANIYA_STICKER_DIR"
_CANDIDATES = (
    r"H:\workbody\2026-10-13-18-15-13\daniya_sticker_skill",
    r"G:\ai对话\daniya_sticker_skill",
    r"G:\ai对话\11\daniya_sticker_skill",
)


def _find_skill_dir() -> Optional[Path]:
    env = os.environ.get(_ENV_KEY)
    if env and Path(env).is_dir():
        return Path(env)
    for c in _CANDIDATES:
        p = Path(c)
        if (p / "backend" / "store.py").is_file():
            return p
    # 主程序同级目录扫一遍
    try:
        here = Path(__file__).resolve()
        for up in list(here.parents)[:5]:
            for cand in up.glob("*daniya_sticker*/backend/store.py"):
                return cand.parent.parent
    except Exception:  # noqa: BLE001
        pass
    return None


# 数据目录：跟技能包绑在一起（技能包自己的 data/）
def _data_dir(skill_dir: Path) -> Path:
    return skill_dir / "data"


class StickerBridge:
    """把技能包能力暴露给主程序前端。线程安全（RLock），同步方法供 js_api 调用。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dir: Optional[Path] = None
        self._store = None
        self._autotag = None
        self._llm = None
        self._vendor = None           # 主程序注入的视觉模型调用器
        self._vision_label = ""

    # ---------------- 初始化 ----------------
    def _ensure(self) -> bool:
        """惰性装载技能包模块。返回是否可用。"""
        if self._store is not None:
            return True
        d = _find_skill_dir()
        if d is None:
            return False
        try:
            root = str(d)
            if root not in sys.path:
                sys.path.insert(0, root)
            # 技能包自己的配置指向它的 data/
            os.environ.setdefault("STICKER_DATA_DIR", str(_data_dir(d)))
            from backend import store as _store_mod           # type: ignore
            from backend import autotag as _autotag_mod       # type: ignore
            from backend import llm as _llm_mod               # type: ignore
            from backend import frames as _frames_mod         # type: ignore
            from backend import marketplace as _mk_mod        # type: ignore
            self._store = _store_mod.Store()
            self._autotag = _autotag_mod
            self._llm = _llm_mod
            self._frames = _frames_mod
            self._marketplace = _mk_mod
            self._dir = d
            logger.info("[表情包] 技能包已装载：%s", d)
            _load_tasks()          # 首次装载后恢复持久化的安装任务
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[表情包] 技能包装载失败：%s", e, exc_info=True)
            self._store = None
            return False

    def set_engine(self, engine: Any = None, vision_label: str = "") -> None:
        """主程序注入引擎/模型标签（用于把打标接到主程序当前视觉模型）。"""
        self._vendor = engine
        self._vision_label = vision_label or ""

    def available(self) -> bool:
        with self._lock:
            return bool(self._ensure())

    # ---------------- 包管理 ----------------
    def list_packs(self) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "表情包技能包未找到（检查 DANIYA_STICKER_DIR）", "packs": []}
            try:
                ps = self._store.list_packs()
                return {"ok": True, "packs": ps}
            except Exception as e:  # noqa: BLE001
                logger.warning("[表情包] list_packs 失败：%s", e)
                return {"ok": False, "error": str(e), "packs": []}

    def create_pack(self, name: str) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                return {"ok": True, "pack": self._store.create_pack(name or "新表情包")}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def rename_pack(self, pid: str, name: str) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                return {"ok": True, "pack": self._store.rename_pack(pid, name)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def delete_pack(self, pid: str) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                self._store.delete_pack(pid)
                return {"ok": True}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def set_bindings(self, pid: str, mode: str = "none", characters: list = None) -> dict:
        """设置表情包的**归属角色**。

        【2026-10-13 简化·按开发者要求】不再暴露 whitelist / blacklist 两种模式：
          · characters 为空  -> mode="none"     **所有角色通用**（默认）
          · characters 非空  -> mode="whitelist" **专属这些角色**
        旧的 blacklist 数据仍能读（packs_for_character 兼容），但 UI 不再产生。
        """
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                chars = [str(c).strip() for c in (characters or []) if str(c).strip()]
                real_mode = "whitelist" if chars else "none"
                return {"ok": True, "pack": self._store.set_bindings(pid, real_mode, chars)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    # ---------------- 表情 ----------------
    def list_memes(self, pid: str) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载", "memes": []}
            try:
                return {"ok": True, "memes": self._store.list_memes(pid)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e), "memes": []}

    def meme_image_url(self, pid: str, mid: str) -> dict:
        """给前端一个可加载的本地图 URL（走主程序已有的 /local_img 白名单路由）。

        ⚠️ /local_img 只放行白名单目录，这里把技能包的 images 目录**注册进去**。
        """
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                m = self._store.get_meme(pid, mid)
                if not m:
                    return {"ok": False, "error": "not found"}
                p = self._store._pack_dir(pid) / "images" / m["file"]
                if not p.is_file():
                    return {"ok": False, "error": "file missing"}
                try:
                    from . import webapp as _wa
                    _wa.register_local_img_dir(str(p.parent))
                except Exception:  # noqa: BLE001
                    pass
                from urllib.parse import quote
                return {"ok": True, "url": "/local_img?path=" + quote(str(p))}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def delete_meme(self, pid: str, mid: str) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                return {"ok": True, "deleted": self._store.delete_meme(pid, mid)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    # ---------------- 标签（自动标注 + 用户可改） ----------------
    def emotion_vocab(self) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "vocab": []}
            return {"ok": True, "vocab": list(self._autotag.EMOTION_VOCAB)}

    def update_labels(self, pid: str, mid: str, fields: dict) -> dict:
        """UI 手改标签：写主字段并标 provenance=manual（自动标注不再覆盖）。"""
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                meta = self._store._read_meta(pid)
                entry = meta.get(mid)
                if entry is None:
                    return {"ok": False, "error": "meme not found"}
                f = fields or {}
                self._autotag.manual_update(
                    entry,
                    description=f.get("description"),
                    tags=f.get("tags"),
                    trigger=f.get("trigger"),
                    category=f.get("category"),
                    mood_compound=f.get("mood_compound"),
                    anti_trigger=f.get("anti_trigger"),
                    visible_text=f.get("visible_text"),
                )
                self._store._write_meta(pid, meta)
                return {"ok": True, "meme": entry}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def autotag_one(self, pid: str, mid: str, force: bool = False) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                m = self._store.get_meme(pid, mid)
                if not m:
                    return {"ok": False, "error": "meme not found"}
                if m.get("provenance") == "manual" and not force:
                    return {"ok": False, "skipped": True,
                            "reason": "该图已被手动编辑过，未覆盖（force=true 可强制覆盖）"}
                fp = self._store._pack_dir(pid) / "images" / m["file"]
                res = self._run_autotag(fp.read_bytes())
                meta = self._store._read_meta(pid)
                entry = meta.get(mid)
                if force:
                    entry["provenance"] = "ai"
                self._autotag.apply_autotag_to_meta(entry, res)
                self._store._write_meta(pid, meta)
                return {"ok": not res.get("error"), "result": res, "meme": entry}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def autotag_pack(self, pid: str, limit: int = 30, only_missing: bool = True,
                     progress: list = None) -> dict:
        """整包自动标注。

        【2026-10-13 按开发者要求】返回**逐条明细**，让 UI 能分「成功 / 失败」并写出失败原因：
          results: [{mid, file, ok, reason, tags}]   —— 失败项 reason 是具体原因
        同时维护 `progress`（可传入列表，调用方轮询读它做进度条）：
          progress = [{"done":n,"total":N,"current":"文件名","ok":n,"fail":n}]
        """
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                memes = self._store.list_memes(pid)
                targets = [m for m in memes
                           if (not only_missing or self._autotag.needs_autotag(m))][:max(1, limit)]
                total = len(targets)
                if progress is not None:
                    progress.clear()
                    progress.append({"done": 0, "total": total, "current": "",
                                     "ok": 0, "fail": 0, "running": True})
                results = []
                done = failed = 0
                for i, m in enumerate(targets, 1):
                    if progress is not None:
                        progress[0].update({"done": i - 1, "current": m.get("file", "")})
                    reason = ""
                    ok = False
                    tags = []
                    try:
                        fp = self._store._pack_dir(pid) / "images" / m["file"]
                        if not fp.is_file():
                            reason = "图片文件不存在"
                        else:
                            res = self._run_autotag(fp.read_bytes())
                            if res.get("error"):
                                reason = str(res["error"])[:200]
                            else:
                                meta = self._store._read_meta(pid)
                                entry = meta.get(m["id"])
                                if entry is None:
                                    reason = "元数据缺失"
                                else:
                                    self._autotag.apply_autotag_to_meta(entry, res)
                                    self._store._write_meta(pid, meta)
                                    tags = res.get("tags") or []
                                    if not tags and not res.get("description"):
                                        reason = "模型返回空结果（未识别出情绪）"
                                    else:
                                        ok = True
                    except Exception as e:  # noqa: BLE001
                        reason = f"{type(e).__name__}: {e}"[:200]
                    if ok:
                        done += 1
                    else:
                        failed += 1
                    results.append({"mid": m["id"], "file": m.get("file", ""),
                                    "ok": ok, "reason": reason, "tags": tags})
                    if progress is not None:
                        progress[0].update({"done": i, "ok": done, "fail": failed,
                                            "current": m.get("file", "")})
                if progress is not None:
                    progress[0]["running"] = False
                return {"ok": True, "tagged": done, "failed": failed,
                        "skipped": len(memes) - total, "total": len(memes),
                        "results": results}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    def _run_autotag(self, data: bytes) -> dict:
        """跑一次自动标注。

        `self._vendor` 有值时走**主程序注入的视觉模型**（推荐，跟随设置页）；
        否则退回技能包自带的智谱免费端口。
        """
        if callable(self._vendor):
            try:
                return self._vendor(data, self._vision_label)
            except Exception as e:  # noqa: BLE001
                logger.warning("[表情包] 主程序视觉模型打标失败，回落技能包端口：%s", e)
        import asyncio as _aio
        backend = self._llm.ZhipuBackend()
        if not backend.available():
            return {"error": "未配置视觉模型（主程序未注入，且技能包无 ZHIPU_API_KEY）"}
        return _aio.run(self._autotag.autotag_bytes(
            backend, data, getattr(backend, "vision_model", ""),
            frames_strategy="motion", frames_k=3))

    # ---------------- 资源广场 ----------------
    def marketplace(self) -> dict:
        """拉社区索引。

        ⚠️【2026-10-13 性能】实测单次 1045ms（网络往返）。索引内容几分钟内不会变，
        缓存 5 分钟：重复打开广场直接用缓存，不再每次都等 1 秒。
        """
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载", "entries": []}
            now = time.time()
            cache = getattr(self, "_mk_cache", None)
            if cache and (now - cache.get("ts", 0)) < 300:      # 5 分钟内用缓存
                return cache["data"]
            try:
                data = self._run_coro_safe(self._marketplace.fetch_index())
                self._mk_cache = {"ts": now, "data": data}
                return data
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e), "entries": []}

    @staticmethod
    def _run_coro_safe(coro, timeout: float = 120.0):
        """在"可能已有 running loop"的线程里安全执行协程。

        【2026-10-17 修复·协程从未被 await】
        旧实现直接 `asyncio.run(coro)`：当调用线程已有 running loop 时
        asyncio.run 立刻抛 RuntimeError，被 except 吞掉 → 协程对象创建后
        从未 await（只剩 RuntimeWarning: coroutine 'install_pack' was never
        awaited）→ **社区表情包的"拉取索引"和"安装"功能永久失效**，
        而用户只看到"失败"，看不出原因。
        现在按当前线程状态分派，并保证任何失败路径都 close() 掉协程。
        """
        import asyncio as _aio
        import concurrent.futures as _cf
        import threading as _th
        try:
            running = _aio.get_running_loop()
        except RuntimeError:
            running = None
        if running is None:
            # 普通同步线程：直接跑
            try:
                return _aio.run(coro)
            except Exception:  # noqa: BLE001
                try:
                    coro.close()
                except Exception:  # noqa: BLE001
                    pass
                raise
        # 已在事件循环里：调度到该循环，但在**独立线程**里等待，避免自锁
        fut = _aio.run_coroutine_threadsafe(coro, running)
        box = {}

        def _wait():
            try:
                box["v"] = fut.result(timeout=timeout)
            except Exception as e:  # noqa: BLE001
                box["e"] = e

        t = _th.Thread(target=_wait, daemon=True)
        t.start()
        t.join(timeout + 5)
        if "e" in box:
            raise box["e"]
        if "v" in box:
            return box["v"]
        # 超时：取消并关闭，避免悬挂协程
        try:
            fut.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        raise TimeoutError("协程执行超时（%.0fs）" % timeout)

    def marketplace_install(self, entry: dict, limit: int = 120) -> dict:
        """真装一个社区表情包（下载 + 分类 + 落盘）。

        【2026-10-13】limit 从 60 提到 120：大包（ATRI 551 张）装 60 张就停会让人
        以为"装漏了"。仍设上限避免单次卡太久；返回 installed/total 让 UI 显示进度。
        """
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                return self._run_coro_safe(self._marketplace.install_pack(
                    entry or {}, self._store, limit=max(1, int(limit))))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    # ---------------- 角色（供绑定用的头像卡片） ----------------
    def characters(self) -> dict:
        """从主程序配置读角色名册，**带上头像**供前端渲染小型角色卡。

        只取绑定需要的字段（id/name/avatar），不搬运大对象。
        读不到配置时返回空列表（前端显示"暂无角色"），绝不抛错。
        """
        out = []
        try:
            # ⚠️ 必须用 load_config(path) —— Config.__init__(raw) 需要已解析的 dict，
            #    直接 Config() 会 TypeError: missing 1 required positional argument: 'raw'
            #    （实测踩到：角色列表恒为空 → 绑定弹窗显示"没有读到角色名册"）。
            import os as _os
            from ..config import load_config  # type: ignore
            # 配置路径：与主程序一致（相对项目根 config.yaml）
            root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
            cfg_path = _os.path.join(root, "config.yaml")
            # ⚠️【2026-10-13 性能】load_config 要解析整份 config.yaml（本工程 1800+ 行），
            #    实测单次 174ms。角色名册每次进页都要读 —— 不缓存的话切一次栏卡 174ms。
            #    按 mtime 缓存：文件没变就直接用上次结果，变了才重解析。
            mt = _os.path.getmtime(cfg_path) if _os.path.exists(cfg_path) else 0
            cache = getattr(self, "_char_cache", None)
            if cache and cache.get("mt") == mt:
                return {"ok": True, "characters": cache["chars"]}
            cfg = load_config(cfg_path)
            out = []
            for c in cfg.list_characters():
                out.append({
                    "id": getattr(c, "id", "") or "",
                    "name": getattr(c, "name", "") or getattr(c, "id", "") or "未命名",
                    # ⚠️ 头像 ≠ 头图：avatar 是方头像，banner 是通栏头图。
                    #    角色卡要头像，别拿 banner 去裁（会只剩一条眼睛）。
                    "avatar": getattr(c, "avatar", "") or "",
                })
            self._char_cache = {"mt": mt, "chars": out}
        except Exception as e:  # noqa: BLE001
            logger.warning("[表情包] 读角色名册失败：%s", e)
        return {"ok": True, "characters": out}

    # ---------------- 选贴 ----------------
    def pick(self, character: str, conversation: list) -> dict:
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            try:
                packs = self._store.packs_for_character(character or "")
                cands, cats = [], {}
                for p in packs:
                    if not p:
                        continue
                    try:
                        cats.update(self._store.get_pack_categories(p["id"]) or {})
                    except Exception:  # noqa: BLE001
                        pass
                    for m in self._store.list_memes(p["id"]):
                        sv = m.get("style_variants") or {}
                        com = sv.get("community") or {}
                        cands.append({
                            "id": m["id"], "pack_id": p["id"],
                            "description": m.get("description", ""),
                            "tags": m.get("tags", []),
                            "category": m.get("category", ""),
                            "trigger": m.get("trigger", ""),
                            "anti_trigger": com.get("anti_trigger", ""),
                            "mood_compound": com.get("mood_compound", ""),
                        })
                backend = self._llm.ZhipuBackend()
                if not backend.available():
                    return {"ok": False, "error": "选贴需要文本模型（未配置）"}
                backend.category_hints = cats
                import asyncio as _aio
                res = _aio.run(backend.pick_sticker(character or "", conversation or [], cands))
                found = next((c for c in cands if c["id"] == res.get("meme_id")), None)
                if found:
                    res["found"] = True
                    res["pack_id"] = found["pack_id"]
                    res["file"] = found.get("file", "")
                    res["description"] = found.get("description", "")
                else:
                    res["found"] = False
                return {"ok": True, "result": res, "candidates": len(cands)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}

    # ---------------- 异步安装（真实进度 + 持久化 + 暂停/继续） ----------------
    def install_start(self, entry: dict, limit: int = 120) -> dict:
        """启动一个后台安装任务，立即返回 task_id（**不阻塞**）。

        前端用返回的 task_id 轮询 install_progress 拿真实进度。
        安装在 daemon 线程里跑，持 self._lock 保证 store 线程安全。
        """
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            import asyncio as _aio
            tid = uuid.uuid4().hex[:12]
            name = entry.get("name") or entry.get("id") or "表情包"
            with _TASK_LOCK:
                _TASKS[tid] = {
                    "id": tid, "status": "running", "done": 0, "total": 0,
                    "current": "", "error": "", "pack_id": "",
                    "name": name, "started_at": time.time(), "updated_at": time.time(),
                    "persist": True,
                    "entry": dict(entry or {}), "limit": max(1, int(limit)),
                }
            _save_tasks()
            self._spawn_install(tid)
            return {"ok": True, "task_id": tid}

    def _spawn_install(self, tid: str, base_pid: str = "") -> None:
        """为任务 tid 拉起安装线程。base_pid 非空 = 断点续传（跳过已导入张数）。"""
        import asyncio as _aio
        with _TASK_LOCK:
            t = _TASKS.get(tid) or {}
            entry = dict(t.get("entry") or {})
            limit = int(t.get("limit") or 120)
            ev = threading.Event()
            _CANCEL_EVENTS[tid] = ev

        def _run():
            try:
                # 清掉同名旧包，避免重试堆积 / 多次安装出现重复包。
                # 续传（base_pid 非空）时**不能删**——那就是断点所在的包。
                if not base_pid:
                    name = entry.get("name") or entry.get("id") or "表情包"
                    try:
                        for p in self._store.list_packs():
                            if p.get("name") == name:
                                try:
                                    self._store.delete_pack(p["id"])
                                except Exception:  # noqa: BLE001
                                    pass
                    except Exception:  # noqa: BLE001
                        pass

                def _prog(done, total, cur):
                    _update_task(tid, done=done, total=total, current=cur)

                res = _aio.run(self._marketplace.install_pack(
                    entry, self._store, limit, on_progress=_prog,
                    cancel_event=ev, base_pid=base_pid))
                if res.get("cancelled"):
                    # 暂停：保留部分包（pack_id 已写进任务，供「继续」续传）。
                    # keep=False（彻底取消）由 install_cancel 里标记 _cancel_drop，
                    # 这里负责在同一线程收尾删包，避免跨线程操作 store。
                    with _TASK_LOCK:
                        tt = _TASKS.get(tid) or {}
                        drop = tt.get("_cancel_drop", False)
                        if drop:
                            tt["_cancel_drop"] = False
                    _update_task(tid, status="paused", done=res.get("imported", 0),
                                 total=res.get("total", 0), current="", error="",
                                 pack_id="" if drop else res.get("pack_id", ""))
                    if drop and res.get("pack_id"):
                        try:
                            self._store.delete_pack(res["pack_id"])
                        except Exception:  # noqa: BLE001
                            pass
                        _update_task(tid, status="canceled", pack_id="")
                elif res.get("ok"):
                    _update_task(tid, status="done", done=res.get("imported", 0),
                                 total=res.get("imported", 0),
                                 pack_id=res.get("pack_id", ""), error="",
                                 imported=res.get("imported", 0),
                                 categories=res.get("categories", 0))
                else:
                    _update_task(tid, status="error",
                                 error=res.get("error", "安装失败"))
            except Exception as e:  # noqa: BLE001
                _update_task(tid, status="error", error=str(e))
            finally:
                with _TASK_LOCK:
                    _CANCEL_EVENTS.pop(tid, None)
                _save_tasks()

        th = threading.Thread(target=_run, daemon=True)
        th.start()

    def install_cancel(self, task_id: str, keep: bool = True) -> dict:
        """暂停（keep=True，保留断点）或取消（keep=False，丢弃部分包）。"""
        with _TASK_LOCK:
            t = _TASKS.get(task_id)
            if not t:
                return {"ok": False, "error": "任务不存在"}
            if t.get("status") != "running":
                return {"ok": False, "error": "任务不在下载中"}
            t["_cancel_drop"] = (not keep)
            ev = _CANCEL_EVENTS.get(task_id)
        if ev is None:
            return {"ok": False, "error": "任务线程未就绪"}
        ev.set()
        return {"ok": True}

    def install_continue(self, task_id: str) -> dict:
        """从断点继续下载（仅 paused 任务；复用部分包，跳过已导入张数）。"""
        with self._lock:
            if not self._ensure():
                return {"ok": False, "error": "技能包未装载"}
            with _TASK_LOCK:
                t = _TASKS.get(task_id)
                if not t:
                    return {"ok": False, "error": "任务不存在"}
                if t.get("status") != "paused":
                    return {"ok": False, "error": "任务不在暂停状态"}
                base = t.get("pack_id") or ""
                t["status"] = "running"
                t["current"] = ""
                t["error"] = ""
                t["updated_at"] = time.time()
            _save_tasks()
            self._spawn_install(task_id, base_pid=base)
            return {"ok": True}

    def install_progress(self, task_id: str) -> dict:
        """前端轮询真实进度。返回 {id,status,done,total,current,error,name,pack_id}。"""
        with _TASK_LOCK:
            t = _TASKS.get(task_id)
        if not t:
            return {"ok": False, "error": "任务不存在", "found": False}
        return {"ok": True, "found": True, **t}

    # ---------------- 自检 ----------------
    def selfcheck(self) -> dict:
        """给 UI 的「功能自检」用：逐项报告后端是否就绪。"""
        out = {"ok": True, "items": []}
        with self._lock:
            loaded = self._ensure()
            out["skill_dir"] = str(self._dir) if self._dir else ""
            out["items"].append({"name": "技能包装载", "ok": loaded,
                                 "detail": str(self._dir) if self._dir else "未找到技能包目录"})
            if not loaded:
                out["ok"] = False
                return out
            try:
                packs = self._store.list_packs()
                out["items"].append({"name": "存储可读", "ok": True,
                                     "detail": f"{len(packs)} 个表情包"})
            except Exception as e:  # noqa: BLE001
                out["ok"] = False
                out["items"].append({"name": "存储可读", "ok": False, "detail": str(e)})
            try:
                n = sum(len(self._store.list_memes(p["id"])) for p in self._store.list_packs())
                out["items"].append({"name": "表情索引", "ok": True, "detail": f"{n} 张表情"})
            except Exception as e:  # noqa: BLE001
                out["ok"] = False
                out["items"].append({"name": "表情索引", "ok": False, "detail": str(e)})
            out["items"].append({"name": "视觉模型（自动标注）",
                                 "ok": bool(self._vendor) or self._llm.ZhipuBackend().available(),
                                 "detail": self._vision_label or "技能包自带智谱端口"})
            out["items"].append({"name": "文本模型（选贴）",
                                 "ok": self._llm.ZhipuBackend().available(),
                                 "detail": getattr(self._llm.ZhipuBackend(), "text_model", "")})
        return out


# ---------------------------------------------------------------------------
# 安装任务管理（**异步安装 + 真实进度 + 跨重启持久化**）
#   【2026-10-15 修复】旧版 marketplace_install 是同步阻塞（_aio.run 卡住主线程），
#   前端拿不到进度（假进度条）、UI 卡崩、退出丢状态。现改为：
#     install_start()        → 立即返回 task_id，后台线程跑安装，进度写内存表 + 节流落盘
#     install_progress(tid)  → 前端轮询真实进度
#   任务状态持久化到 data/.install_tasks.json：重启后未完成任务标记为 interrupted，
#   前端全局浮层恢复显示，可重试。
# ---------------------------------------------------------------------------
_TASKS: dict = {}                 # task_id -> {id,status,done,total,current,error,pack_id,name,...}
_TASK_LOCK = threading.Lock()
_CANCEL_EVENTS: dict = {}         # task_id -> threading.Event（暂停/取消信号，仅内存）
_TASKS_FILE = None
_TASKS_LOADED = False
_TASKS_LAST_SAVE = 0.0


def _set_tasks_file():
    global _TASKS_FILE
    d = _find_skill_dir()
    if d:
        _TASKS_FILE = d / "data" / ".install_tasks.json"


def _save_tasks():
    if not _TASKS_FILE:
        return
    try:
        with _TASK_LOCK:
            data = {k: v for k, v in _TASKS.items() if v.get("persist", True)}
        _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TASKS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_tasks():
    global _TASKS_LOADED
    if _TASKS_LOADED:
        return
    _set_tasks_file()
    if _TASKS_FILE and _TASKS_FILE.is_file():
        try:
            data = json.loads(_TASKS_FILE.read_text(encoding="utf-8") or "{}")
            with _TASK_LOCK:
                for tid, t in data.items():
                    # 进程重启后，仍在 running 的任务其线程已死 → 标记中断
                    if t.get("status") == "running":
                        t["status"] = "interrupted"
                        t["error"] = t.get("error") or "上次下载未完成（软件已关闭）"
                    _TASKS[tid] = t
        except Exception:  # noqa: BLE001
            pass
    _TASKS_LOADED = True


def _update_task(tid, **kw):
    global _TASKS_LAST_SAVE
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return
        t.update(kw)
        t["updated_at"] = time.time()
        now = time.time()
        terminal = t.get("status") in ("done", "error", "interrupted", "paused", "canceled")
        if terminal or (now - _TASKS_LAST_SAVE) > 2:
            _TASKS_LAST_SAVE = now
            _save_tasks()


_BRIDGE: Optional[StickerBridge] = None
_BRIDGE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 方法别名（**重要**）
#   调用链：前端 Backend.stickerXxx() → CharacterAPI.stickerXxx()
#           → getattr(bridge, 'stickerXxx')()  ← 这里如果名字对不上就是静默 no method
#   而 Bridge 内部按技能包习惯叫 list_packs / list_memes / …
#   故把两套名字显式接上。缺了这段，前端会一直显示「加载中」且不报错。
# ---------------------------------------------------------------------------
def _alias(name_from: str, name_to: str):
    def _f(self, *a, **k):
        return getattr(self, name_to)(*a, **k)
    _f.__name__ = name_from
    setattr(StickerBridge, name_from, _f)


for _a, _b in (
    ("stickerSelfcheck", "selfcheck"),
    ("stickerPacks", "list_packs"),
    ("stickerCreatePack", "create_pack"),
    ("stickerRenamePack", "rename_pack"),
    ("stickerDeletePack", "delete_pack"),
    ("stickerBindings", "set_bindings"),
    ("stickerMemes", "list_memes"),
    ("stickerImageUrl", "meme_image_url"),
    ("stickerDeleteMeme", "delete_meme"),
    ("stickerVocab", "emotion_vocab"),
    ("stickerUpdateLabels", "update_labels"),
    ("stickerAutotagOne", "autotag_one"),
    ("stickerAutotagPack", "autotag_pack"),
    ("stickerMarketplace", "marketplace"),
    ("stickerInstall", "marketplace_install"),
    ("stickerInstallStart", "install_start"),
    ("stickerInstallProgress", "install_progress"),
    ("stickerInstallCancel", "install_cancel"),
    ("stickerInstallContinue", "install_continue"),
    ("stickerPick", "pick"),
    ("stickerCharacters", "characters"),
):
    _alias(_a, _b)


def get_sticker_bridge() -> StickerBridge:
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = StickerBridge()
        return _BRIDGE
