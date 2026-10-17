# -*- coding: utf-8 -*-
"""百炼 API Key 的识别与归一（自 webapp.py 抽出，2026-10-17）。

用户在多处填过同一个 Key 时，启动把它收敛成一份。纯函数 + 一个 logger，
不持有任何可变状态。
"""
from __future__ import annotations

import logging

# 沿用原模块的 logger 名，保证日志前缀与既有排查习惯一致
logger = logging.getLogger("src.ui.webapp")

__all__ = [
    "_diffuse_bailian_key", "_key_unify_enabled", "_looks_like_bailian_key",
    "_collect_bailian_keys", "_unify_bailian_key",
]


def _diffuse_bailian_key(node, new_key: str) -> int:
    """递归把配置树里所有「百炼形态」的 api_key 统一替换为 new_key，返回替换处数。

    【2026-10-04 新增】百炼 Key 形如 `sk-...`（工作空间形态为 `sk-ws-...`）。用户换账号时，
    旧 Key 会残留在 config.yaml 的**各个角落**（cloud_omni / vision_models.models[*] /
    text_chat.providers[*] / llm.openai / agent.llm / cascade / voice.clone.cloud /
    voice_profiles[*]…）。此前引导页只固定扩散三个段、且只对 provider 白名单生效，导致
    "引导页显示已填 Key、实际双工仍用旧 Key" → 双工静默哑、视觉 403（10-04 实锤：
    旧 Key 残留 26 处、新 Key 只写入 2 处）。此函数让**一处填、全域生效**。

    保守规则（宁可漏改也不要误伤别人的 Key）：
      · **凡值是以 `sk-` 开头的字符串就替换**（键名无关）。实测 config 里 key 有两种写法：
        ① `api_key: sk-...` ② `llm.openai: sk-...` / `cascade: sk-...` / `agent.llm: sk-...`
        ——键名本身就长得像 key。只按 `api_key` 匹配会漏掉 ②，导致仍指向旧 Key。
      · **跳过 `sk-api-` 开头者**（MiniMax 等独立服务商的 Key，不属于百炼）。
    """
    if not new_key:
        return 0
    n = 0

    def _walk(x):
        nonlocal n
        if isinstance(x, dict):
            for k, v in list(x.items()):
                if isinstance(v, str) and v.strip().startswith("sk-") and v != new_key:
                    if v.strip().startswith("sk-api-"):
                        continue          # MiniMax 等独立服务商，不在扩散范围
                    x[k] = new_key
                    n += 1
                else:
                    _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(node)
    return n


def _key_unify_enabled(raw) -> bool:
    """读 `key_unify.enabled` 开关；缺省关闭（保持既有行为）。"""
    try:
        node = (raw or {}).get("key_unify") or {}
        if not isinstance(node, dict):
            return False
        return bool(node.get("enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _looks_like_bailian_key(s: str) -> bool:
    """是否是「百炼形态」的 Key（可参与统一）。

    百炼工作空间 Key 形如 `sk-ws-...`（实测 115~117 位）；普通百炼 Key 形如 `sk-...`。
    排除：`sk-api-...`（MiniMax 等独立服务商）、非 sk- 开头（智谱 49 位短 key 等）。

    【必须同时卡前缀 + 最小长度】否则 `sk-` / `sk-api` 这种「只有前缀的残缺值」会被
    判为合法，扩散出去等于把 28 处配置全改成 `sk-` —— 不可逆灾难（2026-10-08 自测抓到）。
    """
    if not isinstance(s, str):
        return False
    v = s.strip()
    if not v.startswith("sk-"):
        return False
    if v.startswith("sk-api-"):
        return False
    # 最小长度：真实百炼 Key 至少 40 位；只有前缀的残缺值一律拒绝
    if len(v) < 40:
        return False
    return True


def _collect_bailian_keys(node, _acc=None):
    """递归收集配置树里所有「百炼形态」的 Key 值（去重，保持首次出现顺序）。"""
    if _acc is None:
        _acc = []
    if isinstance(node, dict):
        for _k, v in node.items():
            if isinstance(v, str) and _looks_like_bailian_key(v):
                if v not in _acc:
                    _acc.append(v)
            else:
                _collect_bailian_keys(v, _acc)
    elif isinstance(node, list):
        for v in node:
            _collect_bailian_keys(v, _acc)
    return _acc


def _unify_bailian_key(raw, new_key: str, source: str = "") -> int:
    """把配置树里的百炼 Key 统一为 `new_key`；返回替换处数（0 = 未做任何改动）。

    仅当 `key_unify.enabled` 打开且 `new_key` 是合法百炼 Key 时才动手；
    否则原样返回 0 —— **这是「不改变既有行为」的保证**。
    """
    if not _key_unify_enabled(raw):
        return 0
    if not _looks_like_bailian_key(new_key):
        return 0
    _nk = new_key.strip()
    _before = _collect_bailian_keys(raw)
    _n = _diffuse_bailian_key(raw, _nk)
    if _n:
        logger.info("key_unify[%s]：百炼 Key 已统一为 %s...（替换 %d 处；统一前存在 %d 种不同 Key）",
                    source or "-", _nk[:12], _n, len(_before))
    return _n
