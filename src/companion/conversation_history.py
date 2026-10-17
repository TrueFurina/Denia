"""统一共享会话历史（方案 C：双工 / 打字聊天 单一连续记忆）。

为什么需要它：
- 此前打字聊天（dialogue.py 的 self._history）与本地双工（local_duplex.py 的
  _conv_history / proactive 的 _duplex_talk_log）是两套互不连通的记忆。结果：
  双工里聊过啥，打字时 AI 不知道；打字里交代的事，双工也接不上。
- 本模块提供一个进程级单例 deque，双工与打字都往里写、都从里读，形成
  pubjavis 式「单线程连续对话」体感。

写入方：
- dialogue.py：打字 user/assistant 轮。
- proactive.py：双工真实开口（_record_duplex_ai_line）+ 用户语音转写（source=="voice"）。

读取方：
- dialogue.py：构造 LLM messages 时取最近若干轮作为多轮上下文（回忆双工）。
- proactive.py：_build_duplex_memory 把「最近打字聊过的内容」并入双工记忆，在
  双工会话边界（start/reconnect，经 set_instructions/system_prompt 注入）补齐断点，
  使双工也能接上打字上下文（realtime 在会话边界同步，不扰动运行中的双工）。

线程安全：双工引擎有采集线程，但写入点都在 asyncio 事件循环内；仍加锁防意外跨线程写。
"""

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

# 共享记忆上限（轮）。远超单轮上下文窗口的合理利用范围，旧轮自动淘汰。
SHARED_MAXLEN = 80
# 注入给下游（打字上下文 / 双工记忆）的默认取用轮数。
DEFAULT_RECENT = 24
# 单条落盘上限（与 add_turn 的截断口径一致）。
_MAX_CONTENT = 1000


class ConversationHistory:
    def __init__(self, maxlen: int = SHARED_MAXLEN, persist_key: str = ""):
        self._lock = threading.Lock()
        self._turns: deque = deque(maxlen=maxlen)
        # 【2026-10-11·上下文持久化】`persist_key` = 该桶对应的角色 id（落盘文件名）。
        #   仅在显式开启持久化时由 `_persist_path()` 决定是否真写盘；未开启则纯内存
        #   （单测/无盘环境零副作用）。`_persist` 是写回调，由模块级 `enable_persistence`
        #   统一挂；默认 None = 不落盘。
        self._persist_key: str = (persist_key or "").strip()
        self._persist: Optional[Callable[[str, dict], None]] = None

    # ---- 持久化（可选） -------------------------------------------------
    def _bind(self, persist_key: str, persist: Callable[[str, dict], None]) -> None:
        """绑定落盘键与写回调（由模块级 enable_persistence 调用）。"""
        self._persist_key = (persist_key or "").strip()
        self._persist = persist

    def add_turn(self, role: str, content: str, source: str = "chat") -> None:
        """追加一轮对话。role ∈ {user, assistant}；source ∈ {chat, duplex}。

        跳过空串 / 超长（防异常长输出爆记忆），与各处既有裁剪策略一致。
        若已开启持久化，则**写内存的同时旁路落盘**（写失败绝不影响对话）。
        """
        content = (content or "").strip()
        if not content or len(content) > _MAX_CONTENT:
            return
        rec = {
            "role": role,
            "content": content,
            "source": source,
            "ts": time.time(),
        }
        with self._lock:
            self._turns.append(rec)
        # 落盘（旁路；异常不冒泡）
        cb = self._persist
        key = self._persist_key
        if cb is not None and key:
            try:
                cb(key, rec)
            except Exception:  # noqa: BLE001 落盘失败不该毁掉对话
                pass

    def extend_turns(self, turns: list[dict]) -> int:
        """批量灌入历史（**回填**用）。返回实际写入条数。

        与 add_turn 不同：回填**不落盘**（数据本就是从盘里读回来的，再写一遍会重复），
        且按 maxlen 自动淘汰。用于启动/切角色时把落盘上下文重新灌进内存桶。

        溯源：盘上每条自带原始 `source`（chat/duplex…），回填时**原样保留**——
        保留"这轮最初是从哪条管线来的"比统一打上 restore 更有诊断价值
        （"restore" 仅在盘里没有 source 字段的老数据时作为兜底）。
        """
        n = 0
        with self._lock:
            for t in (turns or []):
                if not isinstance(t, dict):
                    continue
                role = str(t.get("role") or "")
                content = str(t.get("content") or t.get("text") or "").strip()
                if role not in ("user", "assistant") or not content or len(content) > _MAX_CONTENT:
                    continue
                self._turns.append({
                    "role": role,
                    "content": content,
                    "source": str(t.get("source") or "restore"),
                    "ts": float(t.get("ts") or 0) or time.time(),
                })
                n += 1
        return n

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._turns) == 0

    def get_recent(self, n: int = DEFAULT_RECENT) -> list[dict]:
        """返回最近 n 轮（纯 role/content，可直接拼进 messages）。"""
        with self._lock:
            return [{"role": t["role"], "content": t["content"]}
                    for t in list(self._turns)[-n:]]

    def recent_user_texts(self, n: int = DEFAULT_RECENT) -> list[str]:
        """最近 n 轮里 user 说的话（按时间正序），用于记忆块拼装。"""
        with self._lock:
            return [t["content"] for t in list(self._turns)[-n:] if t["role"] == "user"]

    def snapshot_text(self, n: int = DEFAULT_RECENT, sep: str = "\n") -> str:
        """可读摘要：用户：…/达妮娅：…（调试 / 记忆块用）。"""
        with self._lock:
            out = []
            for t in list(self._turns)[-n:]:
                who = "用户" if t["role"] == "user" else "达妮娅"
                out.append(f"{who}：{t['content']}")
            return sep.join(out)

    def clear(self) -> None:
        """清空当前桶的内存轮次（**并同步清空该角色的落盘上下文**，否则重启会复活）。"""
        with self._lock:
            self._turns.clear()
        cb = self._persist_clear
        key = self._persist_key
        if cb is not None and key:
            try:
                cb(key)
            except Exception:  # noqa: BLE001
                pass

    # 清盘回调（由模块级 enable_persistence 挂）
    _persist_clear: Optional[Callable[[str], None]] = None


_shared: Optional[ConversationHistory] = None
# 【2026-10-11·多角色隔离】按 char_id 分桶的共享历史。
#   旧实现是**单一进程级 deque**：打字与双工都往同一条 history 里写，且不区分角色。
#   切角色时只能 `clear()` 一把抹掉（switch_character 现状）→ ① 换人后旧角色的上下文虽
#   被清、但双工/关系写入方一旦在 clear 之前/之中落笔就会串味；② 切回去 A 的整段对话
#   上下文**永久丢失**（磁盘只有展示用的 chatlog，喂模型的上下文没救回来）。
#   现改为：每个角色一条独立 history，`clear()` 只清当前角色，切人 = 切换活动桶。
_shared_by_char: dict = {}
_active_char_for_history: str = ""

# ======== 上下文持久化（2026-10-11·重启不失忆） ==========================
#
# 背景：本模块是**唯一**喂给大模型的会话上下文（`dialogue._build_messages` 取
#   `shared_history().get_recent(MAX_HISTORY*2)`）。而它原本是**纯内存 deque**：
#   **重启软件 → 上下文归零**，界面却因为 ChatLogStore 能把聊天记录读回来而"看得见
#   历史" ⇒ 形成「人看得见、她看不见」的失忆（2026-10-11 04:09 实测：用户说"对不起
#   我错了"，她答"是不是刚才有什么误会"——因为她的上下文里只有这一句）。
#
# 修法：**写穿透（write-through）+ 启动回填（rehydrate）**，落盘到
#   `data/context/<char_id>.jsonl`（按角色隔离，**append-only**，与既有的
#   `data/history/`（关系层，会过滤双工源）与 `data/chatlog/`（UI 展示层，过滤
#   状态卡/流式半句）分开——本文件是 `shared_history` 的**忠实镜像**，不丢任何一轮。
#
# 设计红线：
#   1. **可选**：未调 `enable_persistence()` 则纯内存（单测/无盘环境零副作用）。
#   2. **旁路**：落盘失败只 debug 留痕，**绝不**影响对话主链路。
#   3. **归因明确**：回填的轮次 source 标 `"restore"`，便于排查"这轮是盘中来的"。
#   4. 幂等：回填只在桶为空时执行（重复调用不会造成重复堆积）。
# =========================================================================
_PERSIST_BASE: Optional[Path] = None
_CONTEXT_DIRNAME = "context"
# 落盘上限条数（≈ 内存的 SHARED_MAXLEN 若干倍，防止单文件无限增长）：
_PERSIST_MAX_ROWS = 400


def enable_persistence(base_dir) -> bool:
    """开启上下文持久化。base_dir 为项目 data 根目录（或 `Config` 的 data 根）。

    幂等可重复调用。返回是否成功开启（目录不可写则返回 False，绝不抛）。
    """
    global _PERSIST_BASE
    try:
        base = Path(base_dir) if not isinstance(base_dir, Path) else base_dir
        d = base / _CONTEXT_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        with _PERSIST_LOCK:
            _PERSIST_BASE = base
        return True
    except Exception:  # noqa: BLE001 落盘不可用就该安静地降级为纯内存
        _PERSIST_BASE = None
        return False


def _persist_path(char_id: str) -> Optional[Path]:
    """该角色上下文文件的路径；未开启持久化返回 None。"""
    base = _PERSIST_BASE
    if base is None:
        return None
    import re as _re
    key = _re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(char_id or "").strip()) or "default"
    return base / _CONTEXT_DIRNAME / f"{key}.jsonl"


_PERSIST_LOCK = threading.Lock()


def _append_row(char_id: str, rec: dict) -> None:
    """向角色上下文文件追加一行（内部由 ConversationHistory.add_turn 的回调调用）。"""
    p = _persist_path(char_id)
    if p is None:
        return
    try:
        with _PERSIST_LOCK:
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            _trim_locked(p)
    except Exception:  # noqa: BLE001
        pass


def _trim_locked(p: Path) -> None:
    """把文件裁到最近 _PERSIST_MAX_ROWS 行（调用方须已持 _PERSIST_LOCK）。"""
    try:
        if not p.exists():
            return
        with p.open("r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if len(lines) <= _PERSIST_MAX_ROWS:
            return
        keep = lines[-_PERSIST_MAX_ROWS:]
        tmp = p.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write("\n".join(keep) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 裁剪失败无害（下次再裁）
        pass


def _clear_rows(char_id: str) -> None:
    """清空该角色的落盘上下文（`clear_chat` 语义：撤销旧上下文）。"""
    p = _persist_path(char_id)
    if p is None:
        return
    try:
        with _PERSIST_LOCK:
            if p.exists():
                p.unlink()
    except Exception:  # noqa: BLE001
        pass


def load_context(
    char_id: str,
    n: int = SHARED_MAXLEN,
) -> list[dict]:
    """读回某角色最近 n 轮落盘上下文（时间正序）。坏行跳过但不静默。"""
    p = _persist_path(char_id)
    if p is None or not p.exists():
        return []
    out: list[dict] = []
    bad = 0
    try:
        with p.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:  # noqa: BLE001 单行坏数据不该毁掉整个上下文
                    bad += 1
                    continue
                if isinstance(r, dict) and r.get("role") in ("user", "assistant"):
                    out.append(r)
        if bad:
            # 隔离不静默（项目铁律）
            print(f"[上下文] {p.name} 有 {bad} 行无法解析（已跳过）")
    except Exception as e:  # noqa: BLE001
        print(f"[上下文] 读取失败：{p}（{e}）")
        return []
    return out[-n:]


def rehydrate(char_id: str, n: int = SHARED_MAXLEN, *, force: bool = False) -> int:
    """把该角色的落盘上下文灌回内存桶（**启动/切角色时调用**）。

    只在桶为空时执行（幂等，重复调用不会重复堆积）；`force=True` 强制重灌
    （用于"桶里只有本进程刚产生的几条、而盘里有更长历史"的场景，罕见）。
    返回实际灌入条数；未开启持久化或盘上无数据返回 0。
    """
    h = shared_history_for(char_id)
    rows = load_context(char_id, n=n)
    if not rows:
        # ★首次使用（data/context 还没建）：先把既有的老落盘迁过来，
        #   否则"上线前聊过的所有对话"在这一次重启就永久丢了。
        if bootstrap_from_legacy(char_id):
            rows = load_context(char_id, n=n)
        if not rows:
            return 0
    if force:
        # 强制重灌 = **以盘为准**：先清内存再灌，而不是叠加（叠加会让同一轮出现两次）。
        # 注意：这里直接清 deque，**不**调 h.clear()（那会把盘文件也删掉）。
        with h._lock:
            h._turns.clear()
    elif not h.is_empty():
        return 0            # 幂等：桶里已有内容就不再灌，避免重复堆积
    return h.extend_turns(rows)


def bootstrap_from_legacy(char_id: str) -> int:
    """**一次性迁移**：把老系统已落盘的对话搬进 `data/context/<char>.jsonl`。

    为什么必须有这一步：`data/context/` 是本版新增的。上线时用户的历史对话都躺在
    `data/history/<char>.jsonl`（long-term history）与 `data/chatlog/<char>.jsonl`（UI）
    里；若直接启用新持久化，**这些既有内容在这一次重启就永久丢失**（违背了"文字能存
    尽量存，不要丢失"的诉求）。

    迁移源优先级：
      1. `data/history/<char>.jsonl` —— relation reporter 写的统一会话日志，
         **双角色的 id 与 shared_history 天然一致**（role=user/assistant），条数也更多。
      2. `data/chatlog/<char>.jsonl` —— 仅当 history 缺失时兜底（role 需 ai→assistant 映射）。

    只在 `data/context/<char>.jsonl` **不存在**时执行一次（已有则不重复搬）。
    """
    if _PERSIST_BASE is None:
        return 0
    target = _persist_path(char_id)
    if target is None or target.exists():
        return 0
    src_rows: list[dict] = []
    base = _PERSIST_BASE
    # 1) history
    hp = base / "history" / f"{_safe_name(char_id)}.jsonl"
    if hp.exists():
        src_rows = _read_jsonl(hp, ("role", "content"))
    # 2) chatlog 兜底（role 是 ai / user）
    if not src_rows:
        cp = base / "chatlog" / f"{_safe_name(char_id)}.jsonl"
        if cp.exists():
            for r in _read_jsonl(cp, ("role", "text")):
                if not isinstance(r, dict):
                    continue
                if r.get("role") == "ai":
                    r["role"] = "assistant"
                # ★chatlog 的正文字段叫 `text`（不是 content），必须对齐，
                #   否则下面写循环读 content 全是空 → 一条都迁不过去（静默丢）。
                r["content"] = r.get("content") or r.get("text") or ""
                src_rows.append(r)
    if not src_rows:
        return 0
    # 只搬最近 SHARED_MAXLEN 轮（超出部分本就进不了上下文窗口）
    src_rows = src_rows[-SHARED_MAXLEN:]
    written = 0
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _PERSIST_LOCK:
            with target.open("w", encoding="utf-8") as f:
                for r in src_rows:
                    role = r.get("role")
                    content = str(r.get("content") or "").strip()
                    if role not in ("user", "assistant") or not content or len(content) > _MAX_CONTENT:
                        continue
                    rec = {
                        "role": role,
                        "content": content,
                        "source": str(r.get("source") or "migrate"),
                        "ts": float(r.get("ts") or 0) or time.time(),
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                f.flush()
                os.fsync(f.fileno())
    except Exception:  # noqa: BLE001 迁移失败就当无事发生（下次启动还会再试）
        return 0
    if written:
        print(f"[上下文] 已从老日志迁移 {written} 轮 → data/context/{target.name}")
    return written


def _safe_name(char_id: str) -> str:
    import re as _re
    return _re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(char_id or "").strip()) or "default"


def _read_jsonl(p: "Path", keys: tuple) -> list[dict]:
    """读一个 jsonl 文件，抽指定字段（坏行跳过）。"""
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(r, dict):
                    obj = {k: r.get(k) for k in keys}
                    obj["ts"] = r.get("ts") or 0
                    obj["source"] = r.get("source") or "migrate"
                    out.append(obj)
    except Exception:  # noqa: BLE001
        return []
    return out


def set_active_char(char_id: str) -> None:
    """设定共享历史的活动角色（启动时 / 切角色时调用）。空串 = 归入"通用桶"。

    【2026-10-11】切到此角色时**顺带把该角色的落盘上下文回填**（幂等：桶非空则跳过）。
    放在这里而非各个调用方，保证"无论谁触发了切人，上下文都会被接回来"——
    以前 Manchester 调用方忘了这步就会失忆。
    """
    global _active_char_for_history
    _active_char_for_history = (char_id or "").strip()
    try:
        rehydrate(_active_char_for_history)
    except Exception:  # noqa: BLE001 回填失败回到纯内存行为，绝不阻断切换
        pass


def get_active_char() -> str:
    return _active_char_for_history


def _resolve_active_char() -> str:
    """活动角色解析：只认显式注册的角色（由 dialogue / proactive 在建 C/切人时调用
    `set_active_char`）。刻意**不**去 import 全局 Config——本项目没有全局 config 单例，
    硬猜一个不存在的访问器只会静默退回"通用桶"（= 又混淆回原样）。
    未注册 → 归「通用桶」（key=""），保证最坏情况只是回到旧行为，而不是抛异常。"""
    return _active_char_for_history


def _make_history(key: str) -> ConversationHistory:
    """建一个历史桶；若持久化已开启，同时挂上落盘回调（会因备注到角色 key）。"""
    h = ConversationHistory(persist_key=key)
    if _PERSIST_BASE is not None:
        h._bind(key, _append_row)
        h._persist_clear = _clear_rows
    return h


def shared_history_for(char_id: str) -> ConversationHistory:
    """取（必要时创建）指定角色的历史桶。**不改变**活动角色指针。"""
    key = (char_id or "").strip()
    hist = _shared_by_char.get(key)
    if hist is None:
        hist = _make_history(key)
        _shared_by_char[key] = hist
    return hist


def shared_history() -> ConversationHistory:
    """进程级单例（**按当前活动角色隔离**）。

    兼容旧签名：所有既有 `shared_history().add_turn(...)` / `get_recent(...)` 调用
    零改动即自动落到「当前角色」的独立桶，天然实现"换人不混淆"。
    """
    global _shared
    key = _resolve_active_char()
    hist = _shared_by_char.get(key)
    if hist is None:
        hist = _make_history(key)
        _shared_by_char[key] = hist
    _shared = hist                     # 兼容可能存在的旧引用语义
    return hist


def all_histories() -> dict:
    """只读：当前所有角色桶（排障/测试用）。"""
    return dict(_shared_by_char)
