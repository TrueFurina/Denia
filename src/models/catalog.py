"""catalog：读 config → 投影成统一的「模型视图 / 音色视图」（**只读**）。

设计红线（与 P0 基线同一套自我约束）
------------------------------------
1. **只读**：本模块没有任何写 raw / 写文件 / 调 cfg.save() 的代码路径。
   所有函数签名都只接 `raw: dict`，返回新的 dict，不原地改。
2. **读的口径必须与产品代码逐字一致**：每个取值点都在注释里标了 `src:`。
   例如"云端聊天认 manual_provider"必须与 model_registry._pick_cloud_chat_provider
   和 text_chat_router.snapshot 同一口径，否则 UI 又会显示成两个值。
3. **不复制 model_registry 的逻辑**：6 槽位的读取**直接调用** model_registry.read_all，
   只做包装与补充（补上 baseline 里发现的 9 个盲区）。
   这样 model_registry 一改，本层自动跟上，不会产生第二份真相。

输出两个视图
------------
· `ModelView`：13 个调用点的统一列表（消费方：设置页、聊天页）
· `VoiceView`：10 个 TTS 引擎 + 音色档 + 引导页投影（消费方：角色卡、引导页）
"""
from __future__ import annotations

from typing import Any, Optional

from .protocols import (
    LLM_PROVIDERS,
    TTS_ENGINES,
    engine_of_provider,
    field_contract_for,
)
from .cloud_tts import _cloud_tts_providers


# ============================================================ 小工具

def _dig(raw: Any, path: str, default: Any = None) -> Any:
    """按 "a.b.c" 读嵌套 dict（与 model_registry._dig 同语义，但不复用其私有名）。"""
    cur = raw
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _has(v: Any) -> bool:
    """存在性：与产品代码 `if not x: skip` 同口径（0/''/None 都算无）。"""
    return bool(v)


def _mask(k: Any) -> str:
    """掩码：与 model_registry._mask 同格式（首4+6星+尾4），便于两个 UI 显示一致。"""
    s = str(k or "").strip()
    if not s:
        return ""
    if len(s) <= 10:
        return "*" * len(s)
    return s[:4] + "*" * 6 + s[-4:]


def _norm_prov(p: Any) -> str:
    """provider 别名归一（与两处 _PROV_ALIAS 同一套规则）。"""
    s = str(p or "").strip().lower()
    return {"openai": "openai", "openai_compatible": "openai",
            "openai-compatible": "openai", "dashscope": "openai",
            "aliyun": "openai", "bailian": "openai"}.get(s, s)


# ============================================================ 调用点定义（13 个）
# 每个点：id / 用途 / 分类 / 是否被 6 槽位覆盖 / 读取函数名
# 这份清单与 tools/model_chain_baseline.py 的 13 点**一一对应**（同一批真相）。

CALL_SITES = [
    "chat.text", "vision.screen_watch", "duplex.gateway_fallback",
    "duplex.cloud_realtime", "duplex.local_omni", "llm.core_openai",
    "brain.cascade", "brain.agent", "ocr.engine",
    "tts.voice_profile", "tts.voice_clone_global", "tts.onboarding",
    "credentials.inventory",
]


# ============================================================ ModelView

def build_model_view(raw: dict) -> dict:
    """把 13 个调用点投影成统一视图（**只读**）。

    返回：
      {
        "generated_from": "config",
        "sites": [ {id, purpose, family, covered_by_slot, ready, model,
                    endpoint, key_masked, key_ready, note, src} ... ],
        "slot_grid": <model_registry.read_all 的原样透传>,
        "summary": {total, ready, not_ready, in_slot, blind},
      }

    说明：`family` 取值 chat / vision / duplex / brain / tts / ocr / creds，
    供 UI 分组渲染（这是"人性化"要用的分类维度）。
    """
    # 6 槽位：**直接调** model_registry，不复制逻辑（第二份真相是 bug 温床）
    slots: dict = {}
    try:
        from ..llm import model_registry as _mr
        slots = _mr.read_all(raw or {})
    except Exception as e:  # noqa: BLE001  只读层绝不该因 import 失败而炸
        slots = {"_error": f"{type(e).__name__}: {e}"}

    sites: list[dict] = []

    # ---- 1) 打字聊天 ----
    tc = raw.get("text_chat") or {}
    provs = [p for p in (tc.get("providers") or []) if isinstance(p, dict)]
    live = [p for p in provs
            if _has(p.get("api_key")) or _norm_prov(p.get("provider")) == "ollama"]
    from ..llm import model_registry as _mr2
    cur_chat = _mr2._pick_cloud_chat_provider(raw) or {}
    sites.append({
        "id": "chat.text", "family": "chat", "purpose": "打字聊天 / 工具调用",
        "covered_by_slot": True, "slot_key": "cloud_chat",
        "model": str(cur_chat.get("model") or ""),
        "endpoint": str(cur_chat.get("base_url") or ""),
        "key_masked": _mask(cur_chat.get("api_key")),
        "key_ready": _has(cur_chat.get("api_key")),
        "ready": bool(cur_chat.get("model") and _has(cur_chat.get("api_key"))),
        "note": f"{len(live)}/{len(provs)} 个端口可用（空 key 被跳过）",
        "src": "src/llm/text_chat_router.py:72-155",
        "extras": {"mode": str(tc.get("mode") or ""),
                   "manual_provider": str(tc.get("manual_provider") or ""),
                   "port_count": len(provs), "live_count": len(live)},
    })

    # ---- 2) 视觉 ----
    vm = raw.get("vision_models") or {}
    vmodels = [m for m in (vm.get("models") or []) if isinstance(m, dict)]
    act = str(vm.get("active") or "")
    ent = next((m for m in vmodels if str(m.get("id") or "") == act), None) or \
        next((m for m in vmodels if _has(m.get("api_key")) or
              _norm_prov(m.get("provider")) == "ollama"), None)
    sites.append({
        "id": "vision.screen_watch", "family": "vision", "purpose": "看屏 / 看图",
        "covered_by_slot": True, "slot_key": "cloud_vision",
        "model": str((ent or {}).get("model") or ""),
        "endpoint": str((ent or {}).get("base_url") or ""),
        "key_masked": _mask((ent or {}).get("api_key")),
        "key_ready": _has((ent or {}).get("api_key")),
        "ready": bool(vm.get("enabled") and (ent or {}).get("model")),
        "note": f"{len(vmodels)} 个候选，active={act or '<未设>'}",
        "src": "src/llm/vision_router.py:126-223",
        "extras": {"enabled": bool(vm.get("enabled")),
                   "screen_watch_enabled": bool(vm.get("screen_watch_enabled")),
                   "model_count": len(vmodels)},
    })

    # ---- 3) 双工兜底网关 ----
    mc_ep = str(_dig(raw, "llm.minicpm.endpoint", "") or "")
    sites.append({
        "id": "duplex.gateway_fallback", "family": "duplex",
        "purpose": "双工兜底（文本端口全挂时回网关）",
        "covered_by_slot": False, "slot_key": None,
        "model": "MiniCPM-o（网关）", "endpoint": mc_ep,
        "key_masked": "", "key_ready": True,
        "ready": _has(mc_ep),
        "note": "不在槽位内；无自配端点则构造直接抛错（防连已废弃免费网关）",
        "src": "src/llm/minicpm_client.py:67-93",
        "extras": {},
    })

    # ---- 4) 云端双工 ----
    cd = raw.get("cloud_duplex") or {}
    co = raw.get("cloud_omni") or {}
    sites.append({
        "id": "duplex.cloud_realtime", "family": "duplex",
        "purpose": "云端双工（实时语音通话）",
        "covered_by_slot": True, "slot_key": "cloud_duplex",
        "model": str(co.get("model") or ""),
        "endpoint": str(_dig(cd, "voice.endpoint", "") or co.get("endpoint") or ""),
        "key_masked": _mask(co.get("api_key")),
        "key_ready": _has(co.get("api_key")),
        "ready": bool(co.get("model") and _has(co.get("api_key"))),
        "note": "换非 Omni 家族模型会走预设音色（不复刻）",
        "src": "src/companion/proactive.py:_resolve_cloud_duplex_endpoint(4031)",
        "extras": {"prefer_cloud": cd.get("prefer_cloud"),
                   "official_default": cd.get("official_default"),
                   "omni_voice": str(co.get("voice") or "")},
    })

    # ---- 5) 本地双工 ----
    pro = raw.get("proactive") or {}
    dm = str(pro.get("duplex_mode") or "")
    sites.append({
        "id": "duplex.local_omni", "family": "duplex",
        "purpose": "本地双工（离线语音通话）",
        "covered_by_slot": True, "slot_key": "local_duplex",
        "model": "local_omni" if dm in ("local_omni", "local") else "",
        "endpoint": str(pro.get("duplex_local_omni_endpoint") or "https://localhost:8006"),
        "key_masked": "", "key_ready": True,
        "ready": dm in ("local_omni", "local"),
        "note": f"duplex_mode={dm or '<未设>'}；本地引擎非'选模型'",
        "src": "src/companion/proactive.py:5065 / 5183",
        "extras": {"duplex_mode": dm,
                   "ref_audio": str(pro.get("duplex_local_omni_ref_audio") or "")},
    })

    # ---- 6) 核心 LLM ----
    lo = _dig(raw, "llm.openai", {}) or {}
    sites.append({
        "id": "llm.core_openai", "family": "chat",
        "purpose": "核心 LLM（llm.openai 旧主通道）",
        "covered_by_slot": False, "slot_key": None,
        "model": str(lo.get("model") or ""),
        "endpoint": str(lo.get("base_url") or ""),
        "key_masked": _mask(lo.get("api_key")),
        "key_ready": _has(lo.get("api_key")),
        "ready": bool(lo.get("model") and _has(lo.get("api_key"))),
        "note": "不在槽位内；⚠️ llm.openai.vision 曾连坐 20 个视觉条目（10-04 已修）",
        "src": "src/llm/openai_client.py:197-222",
        "extras": {"vision": _dig(raw, "llm.openai.vision")},
    })

    # ---- 7/8) 大脑 ----
    for key, cid, cls in (("cascade", "brain.cascade", "CascadeBrain"),
                          ("agent", "brain.agent", "AgentBrain")):
        blk = raw.get(key) or {}
        sub = blk.get("llm") if isinstance(blk.get("llm"), dict) else blk
        sites.append({
            "id": cid, "family": "brain",
            "purpose": f"{key} 大脑（自建 SSE 流）",
            "covered_by_slot": False, "slot_key": None,
            "model": str(_dig(sub, "model", "") or blk.get("model", "") or ""),
            "endpoint": str(_dig(sub, "base_url", "") or blk.get("base_url", "") or ""),
            "key_masked": _mask(_dig(sub, "api_key", "") or blk.get("api_key", "")),
            "key_ready": _has(_dig(sub, "api_key", "") or blk.get("api_key", "")),
            "ready": bool(_dig(sub, "model", "") or blk.get("model", "")),
            "note": f"不在槽位内（{cls}）",
            "src": f"src/llm/{'cascade' if key == 'cascade' else 'agent'}_brain.py",
            "extras": {},
        })

    # ---- 9) OCR ----
    ocr = raw.get("ocr") or {}
    sites.append({
        "id": "ocr.engine", "family": "ocr", "purpose": "OCR 文字识别",
        "covered_by_slot": False, "slot_key": None,
        "model": str(ocr.get("model") or ocr.get("provider") or ""),
        "endpoint": str(ocr.get("api_url") or ocr.get("endpoint") or ""),
        "key_masked": _mask(ocr.get("api_key")), "key_ready": _has(ocr.get("api_key")),
        "ready": bool(ocr),
        "note": "不在槽位内", "src": "config.yaml:1074", "extras": {},
    })

    # ---- 10/11/12) TTS 三点（详细内容在 build_voice_view，这里给摘要）----
    vv = build_voice_view(raw)
    sites.append({
        "id": "tts.voice_profile", "family": "tts",
        "purpose": "角色卡 TTS（音色档 · 本地 TTS 真相源）",
        "covered_by_slot": False, "slot_key": None,
        "model": vv["active"]["engine"],
        "endpoint": vv["active"]["endpoint"],
        "key_masked": vv["active"]["key_masked"],
        "key_ready": vv["active"]["key_ready"],
        "ready": vv["active"]["ready"],
        "note": f"{vv['profile_count']} 个音色档；当前角色引用 "
                f"{vv['active']['profile_id'] or '<内联 voice>'}",
        "src": "src/config.py:226-244 / src/voice/tts_backends.py:1339-1354",
        "extras": {"profile_count": vv["profile_count"]},
    })
    _ctc = _cloud_tts_providers(raw)
    _ctc_first = next(iter(_ctc.values()), {}) if _ctc else {}
    sites.append({
        "id": "tts.voice_clone_global", "family": "tts",
        "purpose": "TTS 全局克隆（voice.mode / voice.clone）",
        "covered_by_slot": False, "slot_key": None,
        "model": str(_ctc_first.get("model_name") or ""),
        "endpoint": str(_ctc_first.get("api_url") or ""),
        "key_masked": _mask(_ctc_first.get("api_key") or ""),
        "key_ready": _has(_ctc_first.get("api_key") or ""),
        "ready": str(_dig(raw, "voice.mode", "") or "") not in ("", "mute"),
        "note": f"mode={_dig(raw,'voice.mode','') or '<未设>'}"
                f"{'（静音）' if _dig(raw,'voice.muted') else ''}"
                f"{'；已配置 ' + str(len(_ctc)) + ' 个云端 TTS 服务商' if _ctc else ''}",
        "src": "src/voice/tts_backends.py / src/models/cloud_tts.py", "extras": {},
    })
    sites.append({
        "id": "tts.onboarding", "family": "tts",
        "purpose": "引导页声音选择（记录项 + 真实落盘）",
        "covered_by_slot": False, "slot_key": None,
        "model": str((raw.get("onboarding_tts") or {}).get("backend") or ""),
        "endpoint": "", "key_masked": "", "key_ready": True,
        "ready": True,
        "note": "onboarding_tts 只作记录；真正生效靠 apply_onboarding_voice 扩散",
        "src": "src/ui/webapp.py:9163-9185 / 12883+",
        "extras": {"raw": raw.get("onboarding_tts") or {}},
    })

    # ---- 13) 凭证 ----
    creds = _collect_keys(raw)
    sites.append({
        "id": "credentials.inventory", "family": "creds",
        "purpose": "凭证清单（重复存储现状）",
        "covered_by_slot": False, "slot_key": None,
        "model": "", "endpoint": "", "key_masked": "", "key_ready": True,
        "ready": True,
        "note": f"{creds['path_count']} 处存储 / {creds['distinct_count']} 个不同值",
        "src": "src/ui/webapp.py:163-196 (_diffuse_bailian_key)",
        "extras": creds,
    })

    ready_n = sum(1 for s in sites if s.get("ready"))
    in_slot = sum(1 for s in sites if s.get("covered_by_slot"))
    return {
        "generated_from": "config",
        "sites": sites,
        "slot_grid": slots,
        "summary": {
            "total": len(sites), "ready": ready_n, "not_ready": len(sites) - ready_n,
            "in_slot": in_slot, "blind": len(sites) - in_slot,
        },
    }


def _collect_keys(raw: Any) -> dict:
    """全树扫描 `sk-` 开头的字符串，统计"同一账号存了几处"。

    注意：`distinct` / `paths[].masked` 都是**掩码**（不可逆），用于展示；
    `raw_values` 是**原值**列表，仅供内部分析（如判断百炼 Key 是否分裂），
    绝不进入给前端渲染的字段里。
    """
    paths: list[dict] = []
    raw_values: list[str] = []

    def _walk(node: Any, prefix: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, str) and v.startswith("sk-"):
                    paths.append({"path": p, "masked": _mask(v)})
                    raw_values.append(v)
                else:
                    _walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{prefix}[{i}]")

    _walk(raw)
    distinct = sorted({p["masked"] for p in paths})
    return {"path_count": len(paths), "distinct_count": len(distinct),
            "paths": paths, "distinct": distinct, "raw_values": raw_values}


# ============================================================ VoiceView

def build_voice_view(raw: dict, char_id: str = "") -> dict:
    """TTS 统一视图：音色档 + 当前角色实际生效音色 + 10 引擎就绪情况。

    这是**角色卡 TTS 与引导页共用的"唯一真相源"**（它们此前各写一套判断）。

    解析优先级**严格复刻** config.get_active_voice_config（config.py:226-244）：
        角色 voice_profile 指向的档 > 角色内联 char.voice > 无
    并额外给出"能否出声 / 缺什么"，供 UI 显示而不是让用户猜。
    """
    vps = [p for p in (raw.get("voice_profiles") or []) if isinstance(p, dict)]
    chars = [c for c in (raw.get("characters") or []) if isinstance(c, dict)]
    active_id = str(char_id or raw.get("active_character_id") or "")
    char = next((c for c in chars if str(c.get("id") or "") == active_id), None)
    if char is None and chars:
        char = chars[0]

    prof_id = str((char or {}).get("voice_profile") or "")
    prof = next((p for p in vps if str(p.get("id") or "") == prof_id), None)
    inline = (char or {}).get("voice") if isinstance((char or {}).get("voice"), dict) else None

    # 生效来源：档优先，档缺失才用内联（与 get_active_voice_config 一致）
    if prof is not None:
        eff, eff_src = prof, "voice_profile"
    elif inline is not None:
        eff, eff_src = inline, "char.voice(内联)"
    else:
        eff, eff_src = {}, "none"

    eng_raw = str(eff.get("engine") or eff.get("tts_engine") or "")
    # 归一别名（dashscope_cosyvoice/bailian→dashscope_cosy 等），与 create_backend
    # 的 engine_map 及 protocols._TTS_ENGINE_ALIASES 同一套；
    # 原始值另存 engine_raw，供"配置里写的到底是什么"核对（诊断用）。
    contract = field_contract_for(eng_raw)
    eng = contract.get("engine", eng_raw) if contract.get("known") else eng_raw
    missing = _missing_fields(eff, contract)

    voice_mode = str(_dig(raw, "voice.mode", "") or "")
    muted = bool(_dig(raw, "voice.muted", False))

    return {
        "active": {
            "character_id": active_id,
            "profile_id": prof_id,
            "profile_exists": prof is not None,
            "source": eff_src,
            "engine": eng,
            "engine_raw": eng_raw,
            "engine_name": contract.get("name", eng_raw),
            "engine_known": contract.get("known", False),
            "endpoint": str(eff.get("api_url") or contract.get("default_endpoint")
                            or contract.get("endpoint") or ""),
            "key_masked": _mask(eff.get("api_key") or eff.get("mimo_api_key")),
            "key_ready": _has(eff.get("api_key") or eff.get("mimo_api_key")
                              or not contract.get("need_key", False)),
            "voice_id": str(eff.get("voice_id") or eff.get("voice") or ""),
            "model": str(eff.get("model") or ""),
            "missing_fields": missing,
            "ready": bool(eng) and not missing and not muted
                     and voice_mode not in ("mute",),
            "muted": muted,
            "mode": voice_mode,
            # 诊断提示：配置里写了别名（可用但不规范），UI 可提示"建议改回规范值"
            "engine_is_alias": bool(eng_raw) and eng_raw != eng,
        },
        "profile_count": len(vps),
        "profiles": [
            {"id": str(p.get("id") or ""), "name": str(p.get("name") or ""),
             "engine": field_contract_for(p.get("engine")).get("engine",
                                                               str(p.get("engine") or "")),
             "engine_raw": str(p.get("engine") or ""),
             "engine_name": field_contract_for(p.get("engine")).get("name", ""),
             "engine_known": field_contract_for(p.get("engine")).get("known", False),
             "voice_id": str(p.get("voice_id") or ""),
             "model": str(p.get("model") or ""),
             "endpoint": str(p.get("api_url") or ""),
             "key_masked": _mask(p.get("api_key") or p.get("mimo_api_key")),
             "key_ready": _has(p.get("api_key") or p.get("mimo_api_key")),
             "missing_fields": _missing_fields(p, field_contract_for(p.get("engine"))),
             "referenced_by": [str(c.get("id")) for c in chars
                               if str(c.get("voice_profile") or "") == str(p.get("id"))],
             }
            for p in vps
        ],
        # 11 引擎清单（含 fish_audio；供 UI 渲染"可选引擎"卡片）
        "engines": [
            {"engine": k, **{kk: vv for kk, vv in v.items()}}
            for k, v in TTS_ENGINES.items()
        ],
        "onboarding": {
            "recorded_backend": str((raw.get("onboarding_tts") or {}).get("backend") or ""),
            "is_dead_config": True,
            "accepted_backends": ["mute", "edge", "local_qwen_tts", "mimo_tts",
                                  "dashscope_cosyvoice", "custom_tts", "minimax",
                                  "openai_tts"],
        },
    }


def _missing_fields(cfg: dict, contract: dict) -> list[str]:
    """按字段契约算出"还缺哪些必填字段"（只读校验，不写任何东西）。

    对 `either_or` 组（如 dashscope 的 voice_id|reference_audio）要求至少有一项。
    """
    if not contract.get("known"):
        return []
    miss: list[str] = []
    for f in contract.get("required_fields") or []:
        if not _has(cfg.get(f)):
            miss.append(f)
    for group in contract.get("either_or") or []:
        if not any(_has(cfg.get(f)) for f in group):
            miss.append("|".join(group))
    return miss


# ============================================================ 汇总

def summarize(raw: dict) -> dict:
    """一句话摘要（供 UI 顶部状态条 / 日志）。"""
    mv = build_model_view(raw)
    vv = build_voice_view(raw)
    s = mv["summary"]
    return {
        "model": s,
        "voice": {
            "active_engine": vv["active"]["engine"],
            "ready": vv["active"]["ready"],
            "muted": vv["active"]["muted"],
            "profile_count": vv["profile_count"],
        },
        "text": ("模型 " f"{s['ready']}/{s['total']} 就绪；"
                 f"{s['blind']} 个不在槽位内"),
    }


def diagnose(raw: dict) -> list[dict]:
    """投影层自己的体检：报出"用户不知道但确实错了"的配置问题（**只读**）。

    与 `model_registry.diagnose` 互补：
      · model_registry.diagnose 只看 6 槽位涉及的 4 个节点；
      · 本函数覆盖 baseline 里那 9 个盲区（尤其 TTS 三点），
        这正是"角色卡/引导页"最需要的——因为出问题表现为"选了声音却不出声"。

    返回 [{level: warn|info, where, msg}]，可读、可展示。
    """
    warns: list[dict] = []
    vv = build_voice_view(raw)
    a = vv["active"]

    # 1) 音色档引用了不存在的 id（幽灵档 → 静默回退内联 voice）
    chars = [c for c in (raw.get("characters") or []) if isinstance(c, dict)]
    pids = {str(p.get("id") or "") for p in vv["profiles"]}
    for c in chars:
        ref = str(c.get("voice_profile") or "")
        if ref and ref not in pids:
            warns.append({
                "level": "warn", "where": f"characters[{c.get('id')}].voice_profile",
                "msg": f"角色 {c.get('id')!r} 引用的音色档 {ref!r} 不存在 → "
                       f"启动时会静默回退到角色内联 voice（如果你本意是用该档，这里就是坏的）。",
            })

    # 2) 当前角色有内联 voice 却因音色档优先而被遮蔽（配置"看着有用其实没用"）
    char = next((c for c in chars if str(c.get("id") or "") == a["character_id"]), None)
    if char and str(char.get("voice_profile") or "") and a["source"] == "voice_profile":
        inline = char.get("voice")
        if isinstance(inline, dict) and inline.get("engine"):
            warns.append({
                "level": "info", "where": f"characters[{a['character_id']}].voice",
                "msg": f"角色 {a['character_id']!r} 既配了音色档又配了内联 voice；"
                       f"按优先级当前生效的是**音色档**，内联 voice（engine="
                       f"{inline.get('engine')!r}）被遮蔽、不起作用。",
            })

    # 3) 引擎是别名（能用但不规范）
    if a.get("engine_is_alias"):
        warns.append({
            "level": "info", "where": "voice.engine",
            "msg": f"当前音色 engine={a['engine_raw']!r} 是别名写法"
                   f"（规范值是 {a['engine']!r}）。现在能用，但建议改回规范值，"
                   f"避免旧版或第三方工具读不懂。",
        })

    # 4) 未知引擎（不在 10 个白名单内 → save_voice_profile 会直接拒绝）
    if a["engine_raw"] and not a["engine_known"]:
        warns.append({
            "level": "warn", "where": "voice.engine",
            "msg": f"当前引擎 {a['engine_raw']!r} 不在白名单内 → "
                   f"create_backend 会**静默回退 edge-tts**（听感与预期不符）；"
                   f"且保存音色档时会被拒绝（未知 TTS 引擎）。",
        })

    # 5) 全局静音但角色配了音色（"配了却一直没声"）
    if a["muted"] and (a["profile_exists"] or a.get("voice_id")):
        warns.append({
            "level": "info", "where": "voice.muted",
            "msg": "全局 muted=true（或 voice.mode=mute）：即使音色配好了也不会出声。"
                   "这是当前设置，不是故障——但要出声得先解除静音。",
        })

    # 6) 必填字段缺失
    if a["engine_raw"] and a["missing_fields"]:
        warns.append({
            "level": "warn", "where": f"voice.{a['engine']}",
            "msg": f"当前音色缺少必填字段 {a['missing_fields']} → 合成会失败或降级。",
        })

    # 7) 【2026-10-08 P1】onboarding_tts「记录值」与「实际生效值」不一致
    #    onboarding_tts 是引导页落盘时留下的**原始选择副本**，没有任何读取点；
    #    真正生效的是 apply_onboarding_voice 扩散后的 voice.mode / char.voice。
    #    用户后来在设置页改了语音，这份记录不会跟着变 → 它必然越来越偏离真相。
    #    不报这一条，排障者会拿一个过时值当"当前配置"。
    _ob = vv.get("onboarding") or {}
    _recorded = str(_ob.get("recorded_backend") or "")
    if _recorded:
        _actual = "mute" if a.get("muted") else str(a.get("engine_raw") or "")
        # 只在两者「明显不等价」时提示（engine 归一后比较，避免别名误报）
        _rec_norm = field_contract_for(_recorded).get("engine", _recorded)
        _act_norm = field_contract_for(_actual).get("engine", _actual)
        if _rec_norm != _act_norm:
            warns.append({
                "level": "info", "where": "onboarding_tts.backend",
                "msg": f"`onboarding_tts.backend={_recorded!r}` 只是引导页当时的**选择记录**"
                       f"（无任何代码读它），当前实际生效的是 {_actual or '<未设>'!r}。"
                       f"两者不一致是正常的（你后来在设置页改过）；"
                       f"**排障时请以实际生效值为准，不要看 onboarding_tts**。",
            })

    # 8) 【2026-10-08 P1】百炼 Key 分裂（同账号在配置里存了多把不同的 Key）
    #    这是 P1 已修的病根（三条写入路径只写自己那格、不扩散）。
    #    这里把它**可视化**出来：让"设置页显示已配、实际跑的那条用了别的 Key"可见。
    _creds = _collect_keys(raw)
    _bailian_values = [v for v in (_creds.get("raw_values") or [])
                       if isinstance(v, str) and v.startswith("sk-")
                       and not v.startswith("sk-api-")]
    _uniq_bailian = sorted(set(_bailian_values), key=lambda s: -len(s))
    if len(_uniq_bailian) > 1:
        warns.append({
            "level": "warn", "where": "credentials.bailian",
            "msg": f"检测到 {len(_uniq_bailian)} 把**不同的**百炼 Key 同时存在于配置中"
                   f"（长度 {[len(v) for v in _uniq_bailian]}）。"
                   f"它们会被不同调用点各用各的 → "
                   f"表现为「设置页显示已配、实际某条链路仍用旧 Key」。"
                   f"修复：打开 `key_unify.enabled` 后重存一次 Key（会自动统一全部）。",
        })

    # 9) 离线引擎但服务未配端点
    for st in (raw.get("characters") or []):
        pass  # 占位：服务在线性需实际探测，投影层不做网络请求（保持"只读/不联网"）

    # 10) 【2026-10-08 P1】配了云端克隆却没生效（"改了 voice.clone.cloud 以为生效了"）
    #     场景：用户在设置页填了云端克隆的 Key/模型，但 voice.clone.sub 不是 cloud、
    #     或角色 engine 不是该云端引擎 → 配置被写进去了却不参与合成。
    #     【2026-10-09 多服务商】voice.clone.cloud 已是按 provider 分桶，逐桶检查。
    _clone = _dig(raw, "voice.clone", {}) or {}
    _sub = str((_clone or {}).get("sub") or "")
    _providers = _cloud_tts_providers(raw)
    _any_cc_key = any(str(p.get("api_key") or "") for p in _providers.values())
    if _any_cc_key and _sub != "cloud":
        warns.append({
            "level": "warn", "where": "voice.clone.cloud.<provider>.api_key",
            "msg": f"云端 TTS Key 已配置，但 `voice.clone.sub={_sub!r}`"
                   f"（不是 'cloud'）→ 这些 Key 当前**不参与**任何合成。"
                   f"要让云端克隆生效，需把 clone 子档切到 cloud。",
        })
    if _any_cc_key and _sub == "cloud":
        _missing_model = [pn for pn, pc in _providers.items()
                          if pc.get("api_key") and not str(pc.get("model_name") or "")]
        if _missing_model:
            warns.append({
                "level": "warn",
                "where": "voice.clone.cloud." + "/".join(_missing_model) + ".model_name",
                "msg": "云端克隆已启用（sub=cloud）但以下服务商的 `model_name` 为空 → 合成会缺模型名而失败："
                       + ", ".join(_missing_model),
            })

    return warns


__all__ = ["build_model_view", "build_voice_view", "summarize", "diagnose",
           "CALL_SITES"]
