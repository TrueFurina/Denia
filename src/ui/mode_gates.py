# -*- coding: utf-8 -*-
"""档位（运行模式）× 功能 门槛矩阵 —— 单一真相源。

【为什么要有这个模块】
此前"能不能用某功能"散落在三处，各说各话：
  1. webapp._tier_gate_duplex_mode —— 用旧四档词汇（纯聊天/聊天+看得见/全感知伙伴）
  2. webapp.get_tier_call_status    —— 同一套旧词汇，但判定条件略不同（看 cloud_duplex）
  3. 前端设置页/引导页             —— 自己维护一份"哪些功能要哪些能力"，靠 data-scope 隐藏
结果是：用户点被锁的功能只看到"变灰"，点不出为什么、也拿不到"怎么解开"。

【本模块的契约】
- 模式只有三档：**纯本地 / 云端 / 混合**（旧的纯聊天/聊天+看得见/全感知伙伴 是历史遗留说法，
  由 mode_from_features() 归一进来，不再对外暴露）。
- 每个功能在每档下有且只有一个状态：
    ok        可用
    need_key  缺云端 Key / 模型配置 → 解锁动作：去绑定 Key
    need_vram 本地模型超出显存 → 解锁动作：切云端档，或换量化档
    need_mode 该档不含此能力 → 解锁动作：切到 X 档
- 所有 blocked 状态都必须带 **reason（为什么）** 和 **action（怎么解）**，
  前端只负责把这两句话弹给用户，不再自己编文案。

【铁律】新增功能时只改 MODE_PROFILES + FEATURE_LABELS，别在别处再写一份判断。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 模式定义（对外唯一词汇）
# ---------------------------------------------------------------------------

MODE_LOCAL = "local"
MODE_CLOUD = "cloud"
MODE_MIXED = "mixed"

MODE_ORDER = [MODE_LOCAL, MODE_CLOUD, MODE_MIXED]

MODE_LABELS: Dict[str, str] = {
    MODE_LOCAL: "纯本地",
    MODE_CLOUD: "云端",
    MODE_MIXED: "混合",
}

MODE_BLURB: Dict[str, str] = {
    MODE_LOCAL: "模型全跑在你自己电脑上 · 零 Key · 不花 token · 占显存",
    MODE_CLOUD: "一个阿里云 Key 全能力 · 零本地显存 · 按额度计费",
    MODE_MIXED: "本地看屏免费 + 云端顶岗 · 游戏不卡 · 需要好显卡",
}

# 本地双工的硬门槛：Q4 量化约需 13GB 独占，加上系统余量按 14GB 提示
MIN_VRAM_GB = 8.0
DUPLEX_VRAM_GB = 14.0

# ---------------------------------------------------------------------------
# 功能目录：id → (显示名, 属于哪一侧)
# ---------------------------------------------------------------------------

FEATURE_LABELS: Dict[str, str] = {
    "voice_call": "语音通话（双工）",
    "desktop_vision": "桌宠看屏",
    "game_cloud_watch": "打游戏时云端看屏",
    "voice_clone": "音色克隆",
    "speak": "语音合成 / 出声",
    "memory": "长期记忆",
    "music": "听歌 / 音乐感知",
    "agent": "电脑操作助手",
    "social": "社交渠道（QQ / 微信）",
}

# 需要云端 Key / 模型配置的功能（云端侧能力）
_CLOUD_FEATURES = {"voice_call", "game_cloud_watch", "voice_clone", "speak"}
# 依赖本地模型显存的功能
_LOCAL_HEAVY = {"voice_call", "desktop_vision", "voice_clone"}

# ---------------------------------------------------------------------------
# 每档的能力清单：mode -> 允许的 feature id 集合
# ---------------------------------------------------------------------------

MODE_PROFILES: Dict[str, Dict[str, Any]] = {
    MODE_LOCAL: {
        "label": MODE_LABELS[MODE_LOCAL],
        "blurb": MODE_BLURB[MODE_LOCAL],
        "needs_key": False,
        "needs_gpu": True,
        # 本地档：看屏 / 本地双工 / 本地克隆 / 记忆 / 听歌；云端一律不给
        "features": {
            "voice_call": "local",       # 本地双工
            "desktop_vision": "local",
            "game_cloud_watch": "no",    # 本地档没有云端看屏
            "voice_clone": "local",      # 本地千问 TTS
            "speak": "local",
            "memory": "local",
            "music": "local",
            "agent": "local",
            "social": "cloud",           # 社交桥走外部服务，与档位无关
        },
    },
    MODE_CLOUD: {
        "label": MODE_LABELS[MODE_CLOUD],
        "blurb": MODE_BLURB[MODE_CLOUD],
        "needs_key": True,
        "needs_gpu": False,
        "features": {
            "voice_call": "cloud",       # 云端双工
            "desktop_vision": "cloud",   # 云端双工自带云端视觉
            "game_cloud_watch": "cloud",
            "voice_clone": "cloud",
            "speak": "cloud",
            "memory": "cloud",
            "music": "cloud",
            "agent": "local",
            "social": "cloud",
        },
    },
    MODE_MIXED: {
        "label": MODE_LABELS[MODE_MIXED],
        "blurb": MODE_BLURB[MODE_MIXED],
        "needs_key": True,               # 云端看屏/记忆要用 Key
        "needs_gpu": True,
        "features": {
            "voice_call": "local",       # 语音走本地（用户嫌云端延迟时可改）
            "desktop_vision": "local",
            "game_cloud_watch": "cloud",
            "voice_clone": "local",
            "speak": "local",
            "memory": "cloud",
            "music": "local",
            "agent": "local",
            "social": "cloud",
        },
    },
}

# 语音通话在混合档也可切云端 —— 单列 override，避免"混合档锁死云端通话"这种反直觉结果
MODE_PROFILES[MODE_MIXED]["voice_call_alt"] = "cloud"

# ---------------------------------------------------------------------------
# 旧 token / 旧档位名 → 新三档（归一）
# ---------------------------------------------------------------------------

LEGACY_MODE_ALIASES: Dict[str, str] = {
    "local_free": MODE_LOCAL,
    "full": MODE_LOCAL,          # 全感知伙伴 = 本地双工 + 本地看屏 → 纯本地
    "vision": MODE_LOCAL,        # 聊天+看得见 = 本地快照 → 纯本地
    "chat": MODE_LOCAL,          # 纯聊天（无能力）也归本地侧，云端能力一律锁
    "cloud_pay": MODE_CLOUD,
    "skip": MODE_LOCAL,
}


def mode_label(mode: Optional[str]) -> str:
    """模式 → 中文名；未知一律返回「纯本地」（配置缺失时的安全默认）。"""
    return MODE_LABELS.get(mode or "", MODE_LABELS[MODE_LOCAL])


def normalize_mode(mode: Optional[str]) -> str:
    """任意历史 token/档位名 → 三档之一；无法识别回落到纯本地。"""
    m = str(mode or "").strip()
    if m in MODE_LABELS:
        return m
    return LEGACY_MODE_ALIASES.get(m, MODE_LOCAL)


def mode_from_features(features: Optional[List[Any]]) -> str:
    """功能清单 → 当前档位（配置真相源是 companion_features，不是 companion_mode 字符串）。

    判定顺序（从"最特殊"到"最一般"）：
      cloud_duplex 且有本地双工 → 混合
      只有 cloud_* → 云端
      其余（含只有 local_*、空清单、以及历史 token 形态）→ 纯本地
    """
    ids = set()
    for f in (features or []):
        if isinstance(f, dict) and f.get("id"):
            ids.add(str(f["id"]))
        elif isinstance(f, str):
            ids.add(f)
    has_local_dup = "local_duplex" in ids
    has_local_other = bool(ids & {"local_vision", "local_qwen_tts"})
    has_cloud_dup = "cloud_duplex" in ids
    has_cloud_other = bool(ids & {"cloud_snapshot", "cloud_tts"})

    if has_local_dup and has_cloud_other:
        return MODE_MIXED
    if has_local_dup and has_cloud_dup:
        return MODE_MIXED
    if has_cloud_dup and not has_local_other:
        return MODE_CLOUD
    if has_cloud_other and not has_local_dup and not has_local_other:
        return MODE_CLOUD
    return MODE_LOCAL


# ---------------------------------------------------------------------------
# 门槛判定
# ---------------------------------------------------------------------------


def evaluate(
    mode: Optional[str],
    feature_id: str,
    ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """单个功能在给定档位下的门槛结果。

    ctx（全部可选，缺省视为"条件满足"，只在真机上由调用方喂真实值）：
      has_key    bool  云端 Key + 模型是否已配好
      vram_gb    float 实测/标称可用显存
      pro        obj   proactive 实例（用于探测本地双工端点是否活着；没有就跳过该探测）

    返回：{id, label, state, side, reason, action, mode}
      state ∈ ok | need_key | need_vram | need_mode
      ok 时 reason 为空串；blocked 时 reason/action 必填（前端直接弹这两句）
    """
    m = normalize_mode(mode)
    prof = MODE_PROFILES[m]
    label = FEATURE_LABELS.get(feature_id, feature_id)
    ctx = ctx or {}

    # 1) 该档是否含这个能力 —— 不含就是 need_mode（必须给出"切到哪一档"）
    side = prof["features"].get(feature_id, "no")
    if side == "no":
        # 找一档真的给这个能力的，给出可执行的建议
        target = next(
            (k for k in MODE_ORDER if MODE_PROFILES[k]["features"].get(feature_id, "no") != "no"),
            None,
        )
        if target is None:
            return {
                "id": feature_id, "label": label, "state": "need_mode",
                "side": "no", "mode": m,
                "reason": f"「{MODE_LABELS[m]}」档不含「{label}」。",
                "action": "请到 设置 → 模型与 API 换一个档位。",
            }
        return {
            "id": feature_id, "label": label, "state": "need_mode",
            "side": "no", "mode": m,
            "reason": f"「{MODE_LABELS[m]}」档不含「{label}」——纯本地档一切都在你电脑上跑，不走云端。",
            "action": f"切到「{MODE_LABELS[target]}」档即可使用（设置 → 模型与 API → 档位）。",
        }

    # 2) 社交桥是外部服务，与档位无关，别拿 Key 门槛去卡它
    if feature_id == "social":
        return {
            "id": feature_id, "label": label, "state": "ok", "side": side, "mode": m,
            "reason": "", "action": "",
        }

    # 3) 云端侧能力 → 必须有 Key
    if side == "cloud" and prof["needs_key"] and not ctx.get("has_key", True):
        return {
            "id": feature_id, "label": label, "state": "need_key", "side": side, "mode": m,
            "reason": f"「{label}」在「{MODE_LABELS[m]}」档要靠云端跑，但还没绑定有效的阿里云 Key。",
            "action": "到 设置 → 模型与 API 绑定一个阿里云 Key（每个模型有 100 万免费额度）。",
        }

    # 4) 本地重能力 → 显存门槛（纯本地/混合都需要好显卡）
    if side == "local" and feature_id in _LOCAL_HEAVY:
        need = DUPLEX_VRAM_GB if feature_id == "voice_call" else MIN_VRAM_GB
        vram = ctx.get("vram_gb")
        if vram is not None and float(vram) < float(need):
            return {
                "id": feature_id, "label": label, "state": "need_vram", "side": side, "mode": m,
                "reason": (
                    f"「{label}」要跑本地模型，约需 {need:.0f}GB 显存；"
                    f"当前可用约 {float(vram):.0f}GB。"
                ),
                "action": (
                    f"切到「{MODE_LABELS[MODE_CLOUD]}」档用云端跑（零显存），"
                    "或到设置里换一个更轻的量化档。"
                ),
            }

    # 5) 档位说该功能可用，但运行配置里根本没装对应引擎 —— 这是最坑的一种
    #    「假放行」：用户点下去才发现没反应。duplex_engine=False 即此情形
    #    （典型来源：duplex_mode 被写成布尔 True，只做看屏快照、不含语音双工）。
    if feature_id == "voice_call" and ctx.get("duplex_engine") is False:
        return {
            "id": feature_id, "label": label, "state": "need_mode", "side": side, "mode": m,
            "reason": (
                f"「{MODE_LABELS[m]}」档下语音双工引擎没有启用"
                "（当前配置只开了看屏快照，没有双工引擎）。"
            ),
            "action": (
                "到 设置 → 通话与双工 选一个引擎（本地全双工 / 云端双工）；"
                "本地全双工需先下载模型（约 13GB 显存），云端双工需绑阿里云 Key。"
            ),
        }

    return {
        "id": feature_id, "label": label, "state": "ok", "side": side, "mode": m,
        "reason": "", "action": "",
    }


def gate_table(mode: Optional[str], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """整档的门槛表：给设置页渲染"哪些能用/哪些被卡+为什么"用。"""
    m = normalize_mode(mode)
    items = [
        evaluate(m, fid, ctx)
        for fid in FEATURE_LABELS
    ]
    blocked = [i for i in items if i["state"] != "ok"]
    return {
        "mode": m,
        "mode_label": MODE_LABELS[m],
        "blurb": MODE_BLURB[m],
        "needs_key": bool(LEVEL_NEEDS_KEY(m)),
        "needs_gpu": MODE_PROFILES[m]["needs_gpu"],
        "items": items,
        "ok_count": len(items) - len(blocked),
        "blocked_count": len(blocked),
    }


# LEVEL_NEEDS_KEY 只是给 gate_table 内部用的小helper（保持上面对称可读）
def LEVEL_NEEDS_KEY(mode: str) -> bool:  # noqa: N802
    return MODE_PROFILES[mode]["needs_key"]


def blocked_summary(table: Dict[str, Any]) -> str:
    """把门槛表压成一句话，给聊天页提示条用。"""
    items = [i for i in (table.get("items") or []) if i["state"] != "ok"]
    if not items:
        return ""
    names = "、".join(i["label"] for i in items[:3])
    more = f"等 {len(items)} 项" if len(items) > 3 else ""
    return f"「{table.get('mode_label')}」档下不可用：{names}{more}"


def build_context(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从配置 dict 造 evaluate() 需要的 ctx（has_key / vram_gb）。

    没给 raw 时返回"条件都满足"的宽松 ctx —— 纯前端预览/离线场景不该把人锁死。
    """
    if not isinstance(raw, dict):
        return {"has_key": True, "vram_gb": None, "duplex_engine": None}
    co = raw.get("cloud_omni") or {}
    has_key = bool(str(co.get("api_key") or "").strip()) and bool(str(co.get("model") or "").strip())
    # 没有可靠的显存探测就别拿它卡人（宁可放过，不要误锁）
    vram = None
    vm = raw.get("vision_models") or {}
    for src in (vm, raw):
        cand = src.get("vram_gb") or src.get("vram_total")
        if isinstance(cand, (int, float)) and cand > 0:
            vram = float(cand)
            break
    # duplex_engine=None 表示"没查这项" → evaluate 跳过该检查（离线/单测不误锁）
    duplex_engine = None
    pro = raw.get("proactive") or {}
    if "duplex_mode" in pro:
        dm = pro.get("duplex_mode")
        # 布尔 True 的语义是「只看屏快照、不含双工」，不是"双工已开"
        duplex_engine = False if (isinstance(dm, bool) or dm in (None, "")) else (str(dm) not in ("off", "false"))
    return {"has_key": has_key, "vram_gb": vram, "duplex_engine": duplex_engine}


def current_mode(raw: Optional[Dict[str, Any]] = None) -> str:
    """配置 dict → 当前档位（companion_features 优先，回落 companion_mode 字符串）。"""
    if not isinstance(raw, dict):
        return MODE_LOCAL
    feats = raw.get("companion_features")
    if isinstance(feats, list) and feats:
        return mode_from_features(feats)
    return normalize_mode(raw.get("companion_mode"))