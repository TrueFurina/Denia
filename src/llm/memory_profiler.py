"""记忆性能观测仪（D 步，2026-10-01）。

目的：量化「重点观察各模型性能」——记录每次记忆注入/召回/提炼/LLM 调用的
真实 token 成本、延迟、召回命中率，按 tier（cloud / local / local_duplex）聚合，
供开发者页「记忆性能观测仪」面板对比展示。

设计极简、零依赖、全静默：
- 环形内存缓冲（上限 4000 条）+ 落盘 `data/memory_profile.jsonl`（进程级，可跨会话累计）。
- 所有埋点都 try/except 静默，绝不影响主链路（记忆/对话/操作）。
- 进程重启后从 jsonl 重新载入，保证聚合跨会话连续。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

# 环形上限：超过后丢弃最旧（观测只需近期分布）
_RING_CAP = 4000


@dataclass
class MemProbe:
    ts: float
    tier: str          # cloud / local / local_duplex
    model: str         # 触发该事件的模型名（如 qwen-plus / minicpm-o / qwen3:14b）
    event: str         # assemble / extract / llm / recall
    prompt_tokens: int = 0
    latency_ms: float = 0.0
    recalled_facts: int = 0
    recall_hit: bool = False
    note: str = ""


_lock = threading.Lock()
_probes: list[MemProbe] = []
_path: str = ""


def _resolve_path() -> str:
    global _path
    if _path:
        return _path
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _path = os.path.join(root, "data", "memory_profile.jsonl")
    except Exception:
        _path = os.path.join("data", "memory_profile.jsonl")
    return _path


def configure(path: str) -> None:
    """允许外部指定落盘路径（测试用）。"""
    global _path
    _path = path
    _load()


def _load() -> None:
    """进程启动后从 jsonl 载入近期探针，使聚合跨会话连续。"""
    try:
        p = _resolve_path()
        if not os.path.exists(p):
            return
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    _probes.append(MemProbe(**{k: d.get(k, 0 if k in ("prompt_tokens",) else (0.0 if k == "latency_ms" else (False if k == "recall_hit" else ""))) for k in ("ts", "tier", "model", "event", "prompt_tokens", "latency_ms", "recalled_facts", "recall_hit", "note")}))
                except Exception:
                    continue
                if len(_probes) > _RING_CAP:
                    _probes.pop(0)
    except Exception:
        pass


def record_probe(
    tier: str, model: str, event: str,
    prompt_tokens: int = 0, latency_ms: float = 0.0,
    recalled_facts: int = 0, recall_hit: bool = False, note: str = "",
) -> None:
    """记录一条探针。全局静默：任何异常都不抛出。"""
    try:
        p = MemProbe(
            ts=time.time(), tier=tier or "unknown", model=model or "",
            event=event, prompt_tokens=int(prompt_tokens or 0),
            latency_ms=float(latency_ms or 0.0), recalled_facts=int(recalled_facts or 0),
            recall_hit=bool(recall_hit), note=note or "",
        )
        with _lock:
            _probes.append(p)
            if len(_probes) > _RING_CAP:
                _probes.pop(0)
        _append_jsonl(p)
    except Exception:  # noqa: BLE001
        pass


def _append_jsonl(p: MemProbe) -> None:
    try:
        path = _resolve_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": p.ts, "tier": p.tier, "model": p.model, "event": p.event,
                "prompt_tokens": p.prompt_tokens, "latency_ms": p.latency_ms,
                "recalled_facts": p.recalled_facts, "recall_hit": p.recall_hit,
                "note": p.note,
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else float((s[mid - 1] + s[mid]) / 2.0)


def _p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(0.95 * (len(s) - 1)))))
    return float(s[idx])


def _agg_group(lst: list[MemProbe]) -> dict:
    tokens = [float(p.prompt_tokens) for p in lst if p.prompt_tokens > 0]
    lats = [p.latency_ms for p in lst if p.latency_ms > 0]
    # 召回命中率：仅 assemble/recall 事件的 recall_hit 有意义
    rec_events = [p for p in lst if p.event in ("assemble", "recall")]
    hit_rate = round(sum(1 for p in rec_events if p.recall_hit) / len(rec_events), 3) if rec_events else None
    return {
        "count": len(lst),
        "token_median": round(_median(tokens), 1),
        "token_p95": round(_p95(tokens), 1),
        "latency_median_ms": round(_median(lats), 1),
        "latency_p95_ms": round(_p95(lats), 1),
        "recall_hit_rate": hit_rate,
    }


def aggregate(by: str = "tier") -> dict[str, dict]:
    with _lock:
        probes = list(_probes)
    groups: dict[str, list[MemProbe]] = {}
    for p in probes:
        key = p.tier if by == "tier" else (p.model if by == "model" else p.event)
        groups.setdefault(key, []).append(p)
    return {k: _agg_group(v) for k, v in groups.items()}


def get_profile_summary() -> dict:
    """开发者页 / 测试读取的总览。"""
    with _lock:
        total = len(_probes)
    return {
        "generated_at": time.time(),
        "total_probes": total,
        "by_tier": aggregate("tier"),
        "by_model": aggregate("model"),
        "events": dict(sorted(_count_events().items())),
    }


def _count_events() -> dict[str, int]:
    with _lock:
        out: dict[str, int] = {}
        for p in _probes:
            out[p.event] = out.get(p.event, 0) + 1
        return out


def reset() -> None:
    """清空内存与落盘（测试 / 用户手动重置观测数据用）。"""
    global _probes
    with _lock:
        _probes = []
    try:
        p = _resolve_path()
        if os.path.exists(p):
            os.remove(p)
    except Exception:  # noqa: BLE001
        pass
