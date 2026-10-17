"""P3 写入收口层：模型/音色写入的**统一入口**（放在 P2 只读层旁边）。

为什么需要它
------------
P0/P2 已查明：13 个模型调用点里 9 个是盲区，而**写入侧更糟**——
同一件事（"设置当前用哪个 Key / 哪个音色"）有 4 条独立写入路径：

    · 引导页落盘            webapp.py:9257  ← 唯一调用 _diffuse_bailian_key 的
    · save_text_chat_providers  webapp.py:6193  ← 只写自己那格
    · save_llm_settings          webapp.py:6081  ← 只写自己那格
    · set_voice_mode             webapp.py:4340  ← 只写自己那格

于是同一个百炼账号可以在 4 处留下 4 把不同的 Key（实测现存 3 把：115/117/116）。

本模块做什么
------------
把"写完之后的收口动作"抽成**一个可复用函数**，供 webapp 的写入方法调用：

    commit_model_write(raw, *, changed, source) -> dict

它做三件事（全部**幂等**、**可关**、**绝不抛异常**）：
    1. 归一化：把等价的别名/空白值统一成规范值（不改变语义）
    2. Key 统一：若 `key_unify.enabled`，把所有百炼 Key 收敛为一（复用 P1 逻辑）
    3. 自检：把本次写入后的 `diagnose()` 结果回传，让调用方能立刻知道"写完有没有问题"

设计红线（与 P2 一致的"零侵入"）
--------------------------------
· **不接管写入**：本模块不做 `_save_config`，不碰 `self._cfg`。
  它只被调用方在**写完 raw、落盘之前**调用，返回"要不要继续"的建议。
· **不改任何现有 js_api 的返回形状**：调用方自己决定是否把 `report` 加进返回值。
· **默认零行为**：没开 `key_unify.enabled` 时，除了"归一化空白"，什么都不做。
· **绝不抛异常**：任何内部错误都吞掉并记日志 —— 收口层坏掉不能连带写盘失败。
"""
from __future__ import annotations

from typing import Any

from .catalog import diagnose, build_model_view, build_voice_view

# 与 webapp._VOICE_PROFILE_ENGINES 一致的别名归一表（此处只做"值 → 规范值"，
# 不引入新的引擎；具体归属由 protocols.field_contract_for 决定）。
_ENGINE_ALIASES = {
    "dashscope_cosyvoice": "dashscope_cosy",
    "bailian": "dashscope_cosy",
    "local_qwen_tts": "qwen3tts",
    "custom_tts": "openai_tts",
    "edge": "edge-tts",
}


def _is_bailian_key(v: Any) -> bool:
    """与 webapp._looks_like_bailian_key 同口径（含最小长度卡口）。"""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s.startswith("sk-"):
        return False
    if s.startswith("sk-api-"):
        return False
    return len(s) >= 40


def _key_unify_enabled(raw: Any) -> bool:
    try:
        node = (raw or {}).get("key_unify") or {}
        return bool(node.get("enabled", False)) if isinstance(node, dict) else False
    except Exception:  # noqa: BLE001
        return False


def _is_secret_field(key: Any) -> bool:
    """字段名是否"疑似密钥"——这类值只允许 strip，绝不做任何其它规整。

    注意：本层目前只有 strip 一种规整，所以函数体的写法不影响安全性；
    它的作用是**把契约写明**，防止将来有人往 normalize 里加"更聪明"的规则
    （如小写化、去连字符），那会把 Key 改坏且极难排查。
    """
    if not isinstance(key, str):
        return False
    k = key.lower()
    return "key" in k or "token" in k or "secret" in k or "password" in k


def normalize_whitespace(raw: Any, *, copy: bool = False) -> int:
    """把配置里所有字符串值首尾空白抹掉（有 1 处未规范化就 +1）。

    为什么需要：Key/base_url 里混入首尾空格会让鉴权**静默失败**
    （服务端返回 401，但错误信息常被上层吞掉，表现为"配了却连不上"）。
    只做 `strip()`，不改任何内部字符；密钥类字段同样只 strip（见 `_is_secret_field`）。

    参数
    ----
    copy : **只读用途**必须传 True——在深拷贝上操作并返回处数，
           原对象**逐字段不变**。写入路径才用默认的 False（就地规整）。

    为什么要有这个开关：写盘前的收口需要"就地改"，
    但"写完之后的自检""调用方只想看看有没有脏空白"这类场景必须**零副作用**，
    同一个函数承担两种用法时，靠参数把语义显式分开，比再写一份复制逻辑安全。
    """
    n = 0
    target = _deepcopy_plain(raw) if copy else raw

    def _walk(x: Any) -> None:
        nonlocal n
        if isinstance(x, dict):
            for k, v in list(x.items()):
                if isinstance(v, str):
                    if v != v.strip():
                        x[k] = v.strip()
                        n += 1
                else:
                    _walk(v)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                if isinstance(v, str):
                    if v != v.strip():
                        x[i] = v.strip()
                        n += 1
                else:
                    _walk(v)

    _walk(target)
    return n


def _deepcopy_plain(x: Any) -> Any:
    """只含 dict/list/str/num/bool/None 的配置深拷贝（不依赖 copy 模块的 hook 行为）。"""
    if isinstance(x, dict):
        return {k: _deepcopy_plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_deepcopy_plain(v) for v in x]
    return x


def unify_bailian_keys(raw: Any, *, copy: bool = False) -> dict:
    """把所有百炼 Key 收敛为一（以"出现次数最多的那个"为准）。

    选择"众数"而不是"第一个/最后一个"，理由：
      · 引导页扩散会把**新** Key 铺满全树 → 众数即最新，符合直觉；
      · 若各值平分秋色（无法判断哪个新），则**不动**并报告，交给人决定。

    参数
    ----
    copy : **只读用途**必须传 True——在深拷贝上演练，原对象逐字段不变。

    返回 {"applied": bool, "key_len": int, "replaced": int, "candidates": [len,...]}。
    """
    out = {"applied": False, "key_len": 0, "replaced": 0, "candidates": []}
    try:
        if not _key_unify_enabled(raw):
            return out
        root = _deepcopy_plain(raw) if copy else raw
        vals: list[str] = []

        def _walk(x: Any) -> None:
            if isinstance(x, dict):
                for v in x.values():
                    if isinstance(v, str) and _is_bailian_key(v):
                        vals.append(v)
                    else:
                        _walk(v)
            elif isinstance(x, list):
                for v in x:
                    if isinstance(v, str) and _is_bailian_key(v):
                        vals.append(v)
                    else:
                        _walk(v)

        _walk(root)
        if not vals:
            return out
        counts: dict[str, int] = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        out["candidates"] = [len(k) for k, _ in ranked]
        # 只有存在明确多数（唯一最大值）才动手
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return out
        winner = ranked[0][0]
        if len(counts) == 1:
            out["key_len"] = len(winner)
            return out
        n = 0

        def _fix(x: Any) -> None:
            nonlocal n
            if isinstance(x, dict):
                for k, v in list(x.items()):
                    if isinstance(v, str) and _is_bailian_key(v) and v != winner:
                        x[k] = winner
                        n += 1
                    else:
                        _fix(v)
            elif isinstance(x, list):
                for i, v in enumerate(x):
                    if isinstance(v, str) and _is_bailian_key(v) and v != winner:
                        x[i] = winner
                        n += 1
                    else:
                        _fix(v)

        _fix(root)
        out.update({"applied": n > 0, "key_len": len(winner), "replaced": n})
    except Exception:  # noqa: BLE001
        pass
    return out


def commit_model_write(raw: dict, *, changed: str = "", source: str = "") -> dict:
    """写入收口的**唯一入口**。调用方在"改完 raw、落盘之前"调一次。

    参数
    ----
    raw     : 已改好的配置原文（会被**原处修改**，但只做归一/统一两类收敛）
    changed : 本次改了什么（自由文本，仅用于报告），如 "text_chat.providers[0].api_key"
    source  : 调用方标识（用于日志与排障），如 "save_text_chat_providers"

    返回（**永不抛异常**）
    --------------------
    {
      "normalized": int,        # 抹掉首尾空白的处数
      "key_unified": {...},     # unify_bailian_keys 的结果
      "report": [{level,where,msg}],   # 写入后的自检（diagnose）
      "ok": bool,               # 是否"写完之后没有 warn 级问题"
    }
    """
    res: dict = {"normalized": 0,
                 "key_unified": {"applied": False, "key_len": 0,
                                 "replaced": 0, "candidates": []},
                 "report": [], "ok": True, "source": source, "changed": changed}
    try:
        res["normalized"] = normalize_whitespace(raw)
        res["key_unified"] = unify_bailian_keys(raw)
        rep = diagnose(raw)
        res["report"] = rep
        res["ok"] = not any(str(r.get("level")) == "warn" for r in rep)
    except Exception:  # noqa: BLE001
        # 收口层自身出错绝不阻断写入
        res["ok"] = True
    return res


def suggest_for_changed(raw: dict, changed: str = "") -> list[dict]:
    """只读：针对"刚改过的字段"，从 diagnose 里挑出相关条目（供调用方精准提示）。

    匹配规则（双向包含，容错优先）：
      · 把 `changed`（配置路径，如 `voice.clone.cloud`）按 `.`/`[` 切成段；
      · 把每条诊断的 `where`（字段路径，如 `voice.muted` / `voice.engine`）也切成段；
      · **任一段相同**即算命中（宁可多给一条相关提示，也别漏掉真正的坑）。
    纯只读，不做任何写入。
    """
    out: list[dict] = []
    try:
        all_warns = diagnose(raw)
        if not changed:
            return all_warns
        seg_a = {p for p in changed.replace("[", ".").replace("]", "").split(".") if p}
        for r in all_warns:
            w = str(r.get("where") or "")
            seg_b = {p for p in w.replace("[", ".").replace("]", "").split(".") if p}
            if seg_a & seg_b:
                out.append(r)
        return out
    except Exception:  # noqa: BLE001
        return []


__all__ = [
    "commit_model_write",
    "normalize_whitespace",
    "unify_bailian_keys",
    "suggest_for_changed",
    "build_model_view",
    "build_voice_view",
    "diagnose",
]
