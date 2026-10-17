# -*- coding: utf-8 -*-
"""百炼免费额度·本地用量记账（2026-09-29）。

背景：阿里云官方没有公开「免费额度余量」查询 API——
  - compatible-mode/v1/models/quota 实测 404（被当成模型名，报 "The model 'quota' does not exist"）；
  - maas 域名 /api/v1/quotas 是 QPS/RPM/TPM 限流配额，不是免费 Token 余额；
  - 控制台「免费额度」页与 20%/耗尽预警是网页/短信推送，非程序可调 API；
  - 查账户余额需阿里云 AccessKey（BSS OpenAPI），与百炼 sk- Key 是两套凭证，软件不该收集。
因此改为本地记账：每次经本软件发起的百炼 chat/completions 调用，把响应里的
usage.total_tokens 按模型累加，从每模型默认 100 万免费额度里扣，得出估算余量。

已知盲区（设置页卡片上有诚实标注）：
  - 双工 realtime 通话暂无 usage 回报，不计入；
  - 其他软件用同一百炼 Key 的调用不计入；
  - 精确余量以百炼控制台「免费额度」页为准。

持久化：<项目根>/data/bailian_usage.json，线程安全，所有失败静默降级（不抛出）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DEFAULT_FREE_TOKENS = 1_000_000  # 百炼新人每模型 100 万 Token（90 天，各模型独立）


def _store_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "bailian_usage.json")


def _load() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            if not isinstance(d.get("models"), dict):
                d["models"] = {}
            if not isinstance(d.get("free_totals"), dict):
                d["free_totals"] = {}
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"models": {}, "free_totals": {}}


def _save(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_store_path()), exist_ok=True)
        tmp = _store_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _store_path())
    except Exception as e:  # noqa: BLE001
        logger.debug("quota_meter 保存失败（忽略）: %s", e)


def _is_dashscope(base_url: str) -> bool:
    """只统计百炼（dashscope）调用，本地 vLLM/ollama 等不占免费额度。"""
    return "dashscope" in (base_url or "").lower()


def record(model: str, total_tokens, base_url: str = "") -> None:
    """记一笔用量。任何异常静默吞掉（绝不影响主调用链路）。"""
    try:
        if not model or not _is_dashscope(base_url):
            return
        n = int(total_tokens or 0)
        if n <= 0:
            return
        with _LOCK:
            d = _load()
            d["models"][model] = int(d["models"].get(model, 0)) + n
            _save(d)
    except Exception as e:  # noqa: BLE001
        logger.debug("quota_meter.record 失败（忽略）: %s", e)


def set_free_total(model: str, tokens: int) -> None:
    """覆盖某模型的免费额度总数（默认 100 万；快照版可单独设）。"""
    try:
        with _LOCK:
            d = _load()
            d["free_totals"][model] = int(tokens)
            _save(d)
    except Exception as e:  # noqa: BLE001
        logger.debug("quota_meter.set_free_total 失败（忽略）: %s", e)


def snapshot() -> list:
    """返回 [{model, used, total, remaining, ratio}]，按剩余比例升序（最紧张的在前）。"""
    with _LOCK:
        d = _load()
    items = []
    for m, used in sorted(d.get("models", {}).items()):
        try:
            used = int(used)
        except Exception:  # noqa: BLE001
            continue
        total = int(d.get("free_totals", {}).get(m, _DEFAULT_FREE_TOKENS))
        remaining = max(0, total - used)
        ratio = (remaining / total) if total > 0 else None
        items.append({"model": m, "used": used, "total": total,
                      "remaining": remaining, "ratio": ratio})
    items.sort(key=lambda it: (it["ratio"] if it["ratio"] is not None else 2.0))
    return items


def reset(model: str = "") -> None:
    """清零记账（model 为空清全部）。"""
    try:
        with _LOCK:
            d = _load()
            if model:
                d.get("models", {}).pop(model, None)
            else:
                d["models"] = {}
            _save(d)
    except Exception as e:  # noqa: BLE001
        logger.debug("quota_meter.reset 失败（忽略）: %s", e)
