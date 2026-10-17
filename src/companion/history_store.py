"""会话历史持久化（P0）——`data/history/<char_id>.jsonl`，append-only。

为什么单独一个模块：
- 现有的 `conversation_history.shared_history` 是**进程内 deque(80)**，重启即丢；
- 本模块做**旁路落盘**，不改现有调用方（`dialogue.py` 照旧读写 shared_history），
  由上层在合适的时机把轮次喂进来即可（P0 不接线）。

规则与 `conversation_history` 对齐：空串 / 超长（>1000）不入库。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

MAX_CONTENT = 1000


def _now() -> float:
    return time.time()


def _safe_id(char_id: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(char_id or "").strip())
    return s or "default"


class HistoryStore:
    """按角色分文件的 JSONL 历史。"""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "data"
        self.base = Path(base_dir) / "history"

    def path_for(self, char_id: str) -> Path:
        return self.base / f"{_safe_id(char_id)}.jsonl"

    def append_turn(self, char_id: str, role: str, content: str, *,
                    source: str = "chat", ts: Optional[float] = None,
                    emotion: str = "") -> Optional[dict[str, Any]]:
        """追加一轮。返回写入的记录；被跳过（空/超长）返回 None。"""
        text = (content or "").strip()
        if not text or len(text) > MAX_CONTENT:
            return None
        rec = {
            "ts": float(_now() if ts is None else ts),
            "role": role if role in ("user", "assistant") else "user",
            "content": text,
            "source": source or "chat",
            "emotion": emotion or "",
        }
        self.base.mkdir(parents=True, exist_ok=True)
        p = self.path_for(char_id)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return rec

    def load_recent(self, char_id: str, n: int = 24) -> list[dict[str, Any]]:
        """读最近 n 轮（时间正序）。坏行跳过但计数告警，不静默。"""
        p = self.path_for(char_id)
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        bad = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001 单行坏数据不该毁掉整个历史
                        bad += 1
        except Exception as e:  # noqa: BLE001
            print(f"[history] 读取失败：{p}（{e}）")
            return []
        if bad:
            print(f"[history] {p.name} 有 {bad} 行无法解析（已跳过）")
        return out[-n:]

    def count(self, char_id: str) -> int:
        p = self.path_for(char_id)
        if not p.exists():
            return 0
        n = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        n += 1
        except Exception:  # noqa: BLE001
            return 0
        return n

    def backfill_from_shared(self, char_id: str, shared: Any, n: int = 80) -> int:
        """把进程内 shared_history 的快照补写进盘（首次落盘 / 崩溃恢复用）。

        `shared` 需提供 `get_recent(n) -> [{role, content}]` 与可选 `snapshot`。
        返回本次写入条数。
        """
        if shared is None or self.count(char_id) > 0:
            return 0
        try:
            turns = shared.get_recent(n) or []
        except Exception as e:  # noqa: BLE001
            print(f"[history] backfill 读取共享历史失败：{e}")
            return 0
        written = 0
        base_ts = _now() - len(turns)
        for i, t in enumerate(turns):
            if self.append_turn(char_id, str(t.get("role", "user")),
                                str(t.get("content", "")),
                                source="backfill", ts=base_ts + i):
                written += 1
        return written


class ChatLogStore:
    """聊天界面消息的落盘（webapp 自有，独立于关系层 history）。

    与 HistoryStore 解耦：即使关系系统关闭，聊天历史也照常保存/恢复。
    存的是「完整消息条目」（id/role/text/ts/emotion/...），恢复时原样回填
    webapp 的 `_chat_buffer`，重启/切角色后聊天界面不再空白。

    规则：
    - 只存真实对话（user/assistant 文本），状态卡(status/status_done)与流式
      半句、被拦截的残句不入库（与聊天界面"刷新生死"语义一致）。
    - append-only，坏行跳过不静默（打印告警）。
    """

    _KEEP_KEYS = ("id", "role", "text", "char_name", "emotion", "action",
                  "is_proactive", "trigger", "failed", "blocked",
                  "block_reason", "source", "images", "seg_index", "ts")

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "data"
        self.base = Path(base_dir) / "chatlog"

    def path_for(self, char_id: str) -> Path:
        return self.base / f"{_safe_id(char_id)}.jsonl"

    def append(self, char_id: str, entry: dict) -> Optional[dict]:
        if not isinstance(entry, dict):
            return None
        text = str(entry.get("text") or "").strip()
        if not text:
            return None
        # 仅持久化「用户/AI 的真实文本」：状态卡(status/status_done)、流式半句、
        # 被拦截残句(failed/blocked) 不入聊天历史（与聊天界面刷新语义一致）。
        # 过滤放在这里（而非调用方），保证任何直接调 append 的路径都不会漏存瞬态卡。
        if entry.get("role") not in ("user", "ai"):
            return None
        if entry.get("kind") in ("status", "status_done"):
            return None
        if entry.get("streaming"):
            return None
        if entry.get("failed") or entry.get("blocked"):
            return None
        rec = {k: entry.get(k) for k in self._KEEP_KEYS}
        rec["ts"] = float(entry.get("ts") or _now())
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            p = self.path_for(char_id)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:  # noqa: BLE001
            print(f"[chatlog] 写入失败：{p}（{e}）")
            return None
        return rec

    def load_recent(self, char_id: str, n: int = 200) -> list[dict]:
        p = self.path_for(char_id)
        if not p.exists():
            return []
        out: list[dict] = []
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001 单行坏数据不该毁掉整个历史
                        print(f"[chatlog] {p.name} 有无法解析的行（已跳过）")
        except Exception as e:  # noqa: BLE001
            print(f"[chatlog] 读取失败：{p}（{e}）")
            return []
        return out[-n:]

    def clear(self, char_id: str) -> None:
        """清空某角色的聊天落盘（对应「清空聊天」操作，使清空真正持久）。"""
        p = self.path_for(char_id)
        try:
            if p.exists():
                p.unlink()
        except Exception as e:  # noqa: BLE001
            print(f"[chatlog] 清空失败：{p}（{e}）")
