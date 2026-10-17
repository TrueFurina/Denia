"""统一模型注册表（Model Registry）—— 10-04 设置页整合的**单一真相源**。

为什么需要它
------------
本项目此前有 **8 处各自定义模型**（`llm.openai` / `ollama.vision_model` /
`perception.vision.realtime_model` / `cascade` / `agent.llm` /
`text_chat.providers` / `vision_models.models` / `cloud_omni`），后果是：

  1. **换模型要去 8 个地方找**，用户根本不知道改哪个；
  2. **改一处会连坐另一处**（已实测：改 `llm.openai.vision` 会同时关掉 20 个视觉
     模型；存聊天端口会反写 `llm.openai`）；
  3. **没有统一的"预设"概念**——阿里云百炼只在聊天侧有预设，视觉侧 20 条平铺
     且无厂商分组，其余链路全靠手填。

本模块把"某条链路用哪个模型"抽象成**槽位（slot）**，一个槽位 = 一条链路 + 位置：

    维度 1｜位置：local（本地） / cloud（云端）
    维度 2｜用途：duplex（语音双工） / chat（文本聊天） / vision（视觉）

⇒ 共 6 个槽位，用户视角就是"本地/云端 × 双工/聊天/视觉"四+二组，一目了然。

设计原则（承接 10-04 的破耦合工作）
------------------------------------
  · **读**：每个槽位都能报告"当前实际用的模型 + 从哪个配置节点读来的"，
    让 UI 能显示"生效中"而不是让人猜；
  · **写**：改一个槽位**只写它自己的配置节点**，绝不碰别人的（这正是之前
    "改一处爆死"的根因）；
  · **厂商预设**：把阿里云百炼 / 智谱 / DeepSeek / 硅基 / OpenAI / Ollama 等
    统一成 `VENDOR_PRESETS`，前端下拉直接用，不必各链路各写一份；
  · **兼容**：旧配置节点全部保留原样写回（不迁移、不删），老用户升级零风险。

⚠️ 边界（诚实说明）：本模块是**配置层的统一视图**，不替代各链路的实际调用代码。
运行时切换仍由各链路自己的热重建逻辑完成（`_apply_llm_live` / 重建路由等）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

# ===== 槽位定义（唯一真相源）=====

SLOT_DUPLEX = "duplex"        # 语音双工（说话 + 听你说话）
SLOT_CHAT = "chat"            # 文本聊天 / 决策内核
SLOT_VISION = "vision"        # 视觉 / 看屏

LOC_LOCAL = "local"
LOC_CLOUD = "cloud"

#: 槽位元信息：标题、说明、是否需要 api_key、支持哪些位置
SLOT_META = {
    SLOT_DUPLEX: {
        "title": "语音双工",
        "desc": "实时语音对话：听你说话并自然回应",
        "need_key": True,
        "locs": (LOC_CLOUD, LOC_LOCAL),
    },
    SLOT_CHAT: {
        "title": "文本聊天",
        "desc": "打字聊天、工具调用、桌宠互动的大脑",
        "need_key": True,
        "locs": (LOC_CLOUD, LOC_LOCAL),
    },
    SLOT_VISION: {
        "title": "视觉看屏",
        "desc": "看图、看屏幕、看视频内容",
        "need_key": True,
        "locs": (LOC_CLOUD, LOC_LOCAL),
    },
}

#: 六宫格：slot → location → 配置节点路径（点路径由 get/set 解析）
#: 说明：本地 duplex 用 `proactive.duplex_mode=local_omni` + Comni 端点，不是
#: 一个"模型名"，故 local_duplex 槽位的 model 由引擎决定（见 read_slot）。
SLOT_GRID = (
    (LOC_CLOUD, SLOT_DUPLEX), (LOC_CLOUD, SLOT_CHAT), (LOC_CLOUD, SLOT_VISION),
    (LOC_LOCAL, SLOT_DUPLEX), (LOC_LOCAL, SLOT_CHAT), (LOC_LOCAL, SLOT_VISION),
)


# ===== 厂商预设（阿里云百炼等，统一一份）=====

#: 每个厂商：base_url 模板 + 常见模型 + 是否需要 key
#: `key_url` 仅用于前端提示"去哪领 key"。
VENDOR_PRESETS = [
    {
        "id": "bailian",
        "name": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "need_key": True,
        "key_url": "https://bailian.console.aliyun.com/",
        "hint": "扫码登录后按量/免费额度计费，新号有免费额度",
        # 三类槽位各给一组常用模型（云端）
        "models": {
            # 【2026-10-17】只列**支持声音克隆**的 omni 系实时模型
            # （qwen-audio-*-realtime 只吃 longan* 预置音色，用不了用户的克隆声，故不列）。
            SLOT_DUPLEX: [
                {"id": "qwen3.8-omni-flash-realtime",
                 "name": "Qwen3.8-Omni-Flash-Realtime（实时语音·推荐）"},
                {"id": "qwen3.5-omni-plus-realtime",
                 "name": "Qwen3.5-Omni-Plus-Realtime（实时语音·均衡）"},
                {"id": "qwen3.5-omni-flash-realtime",
                 "name": "Qwen3.5-Omni-Flash-Realtime（实时语音）"},
            ],
            SLOT_CHAT: [
                {"id": "qwen3.8-max", "name": "Qwen3.8-Max（旗舰·支持工具调用）"},
                {"id": "qwen3.8-flash", "name": "Qwen3.8-Flash（快省·支持工具调用）"},
                {"id": "qwen3.7-plus", "name": "Qwen3.7-Plus（均衡·有免费额度）"},
                {"id": "qwen-plus", "name": "Qwen-Plus（基础·稳·免费额度）"},
                {"id": "qwen-plus-character", "name": "Qwen-Plus-Character（角色扮演）"},
                {"id": "qwen-flash-character", "name": "Qwen-Flash-Character（最省）"},
            ],
            SLOT_VISION: [
                {"id": "qwen3-vl-plus", "name": "Qwen3-VL-Plus（看图/看屏·推荐）"},
                # 【2026-10-16 修正】原列的 qwen3-vl-flash / qwen-vl-max 实测 404 model_not_found
                # （逐个真实 1-token 请求验证过），替换为真实可用项，避免用户选了必失败。
                {"id": "qwen-vl-ocr", "name": "Qwen-VL-OCR（文字/截图识别专用）"},
                {"id": "qwen3.8-max", "name": "Qwen3.8-Max（旗舰·同样支持看图）"},
            ],
        },
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "need_key": True,
        "key_url": "https://open.bigmodel.cn/",
        "hint": "GLM 系列有免费额度，适合看图与文本",
        "models": {
            SLOT_DUPLEX: [{"id": "glm-realtime", "name": "GLM-Realtime（实时语音）"}],
            SLOT_CHAT: [
                {"id": "glm-4.6", "name": "GLM-4.6（旗舰）"},
                {"id": "glm-4-plus", "name": "GLM-4-Plus（均衡）"},
                {"id": "glm-4-flash", "name": "GLM-4-Flash（免费·快）"},
            ],
            SLOT_VISION: [
                {"id": "glm-4v-flash", "name": "GLM-4V-Flash（免费·看图）"},
                {"id": "glm-4v-plus", "name": "GLM-4V-Plus（看图更强）"},
            ],
        },
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "need_key": True,
        "key_url": "https://platform.deepseek.com/",
        "hint": "纯文本强、无视觉能力；便宜",
        "models": {
            SLOT_DUPLEX: [],
            SLOT_CHAT: [
                {"id": "deepseek-chat", "name": "DeepSeek-Chat（通用）"},
                {"id": "deepseek-reasoner", "name": "DeepSeek-Reasoner（推理）"},
            ],
            SLOT_VISION: [],
        },
    },
    {
        "id": "siliconflow",
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "need_key": True,
        "key_url": "https://cloud.siliconflow.cn/",
        "hint": "聚合多厂商，部分模型有免费额度",
        "models": {
            SLOT_DUPLEX: [],
            SLOT_CHAT: [{"id": "Qwen/Qwen3-8B", "name": "Qwen3-8B（免费档）"}],
            SLOT_VISION: [{"id": "Qwen/Qwen2.5-VL-7B-Instruct", "name": "Qwen2.5-VL-7B"}],
        },
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "need_key": True,
        "key_url": "https://platform.openai.com/",
        "hint": "需代理或官方网络可达；gpt-4o 支持看图",
        "models": {
            SLOT_DUPLEX: [{"id": "gpt-4o-realtime-preview", "name": "GPT-4o Realtime"}],
            SLOT_CHAT: [
                {"id": "gpt-4o", "name": "GPT-4o（多模态）"},
                {"id": "gpt-4o-mini", "name": "GPT-4o-mini（快省）"},
            ],
            SLOT_VISION: [{"id": "gpt-4o", "name": "GPT-4o（看图）"}],
        },
    },
    {
        "id": "moonshot",
        "name": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "need_key": True,
        "key_url": "https://platform.moonshot.cn/",
        "hint": "长文本强",
        "models": {
            SLOT_DUPLEX: [],
            SLOT_CHAT: [
                {"id": "kimi-k2", "name": "Kimi K2"},
                {"id": "moonshot-v1-128k", "name": "Moonshot-v1-128k（长文本）"},
            ],
            SLOT_VISION: [],
        },
    },
    {
        "id": "ollama",
        "name": "本地 Ollama",
        "base_url": "http://127.0.0.1:11434",
        "need_key": False,
        "key_url": "",
        "hint": "完全免费离线；需本机 ollama 已 pull 对应模型",
        "models": {
            SLOT_DUPLEX: [],
            SLOT_CHAT: [
                {"id": "qwen3:14b", "name": "Qwen3 14B（本地·均衡）"},
                {"id": "qwen3:8b", "name": "Qwen3 8B（本地·轻量）"},
                {"id": "qwen3.5:27b", "name": "Qwen3.5 27B（本地·最强·吃显存）"},
            ],
            SLOT_VISION: [
                {"id": "qwen2.5vl:3b", "name": "Qwen2.5-VL 3B（本地看屏）"},
            ],
        },
    },
]

#: 本地语音双工**不是**"选模型"，而是选引擎（Comni/MiniCPM-o），单列说明
LOCAL_DUPLEX_ENGINES = [
    {"id": "local_omni", "name": "Comni / MiniCPM-o（本地引擎）",
     "desc": "看屏幕很在行；语音音色固定、延迟偏高（省钱的看屏方案）"},
]


# ===== 配置节点读写小工具 =====

def _dig(raw: dict, path: str, default: Any = None) -> Any:
    """按 "a.b.c" 路径读嵌套 dict。"""
    cur = raw
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _put(raw: dict, path: str, value: Any) -> None:
    """按 "a.b.c" 路径写嵌套 dict（自动建中间层）。"""
    parts = path.split(".")
    cur = raw
    for k in parts[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ===== 读：报告每个槽位当前实际用的模型 =====

def read_slot(raw: dict, loc: str, slot: str) -> dict:
    """读某个槽位的当前生效配置（只读，不改）。

    返回 {loc, slot, model, base_url, api_key_masked, source, note, ready}
    `source` 明确告诉 UI 这个值是从哪个配置节点来的——用户能看到"改这里生效"，
    不用猜。这也是"8 处配置"在用户侧收敛成 6 个槽位的关键。
    """
    out = {
        "loc": loc, "slot": slot, "title": SLOT_META.get(slot, {}).get("title", slot),
        "model": "", "base_url": "", "api_key_masked": "", "has_key": False,
        "source": "", "note": "", "ready": False,
    }
    if slot == SLOT_DUPLEX and loc == LOC_CLOUD:
        # 云端双工 = cloud_omni 节
        m = _dig(raw, "cloud_omni.model", "") or ""
        out.update(model=str(m), base_url=str(_dig(raw, "cloud_omni.base_url", "") or ""),
                   has_key=bool(str(_dig(raw, "cloud_omni.api_key", "") or "").strip()),
                   source="cloud_omni")
        out["api_key_masked"] = _mask(str(_dig(raw, "cloud_omni.api_key", "") or ""))
        out["ready"] = bool(m and str(_dig(raw, "cloud_omni.api_key", "") or "").strip())
        out["note"] = "云端实时语音；换非 Qwen3.5-Omni 家族的模型将用预设音色（不复刻）"
    elif slot == SLOT_DUPLEX and loc == LOC_LOCAL:
        # 本地双工 = 引擎（不是模型名）：由 proactive.duplex_mode 决定
        dm = str(_dig(raw, "proactive.duplex_mode", "") or "")
        ep = str(_dig(raw, "proactive.duplex_local_omni_endpoint", "") or "")
        out.update(model="local_omni" if dm in ("local_omni", "local") else "",
                   base_url=ep or "https://localhost:8006",
                   source="proactive.duplex_mode")
        out["ready"] = dm in ("local_omni", "local")
        out["note"] = "本地引擎无需选模型；看屏强，语音音色固定且延迟偏高"
    elif slot == SLOT_CHAT and loc == LOC_CLOUD:
        # 云端聊天 = text_chat.providers 里「当前 manual 选中」的那个端口
        # （_pick_cloud_chat_provider 认 manual_provider，与聊天页显示同源）
        p = _pick_cloud_chat_provider(raw)
        if p:
            out.update(model=str(p.get("model") or ""), base_url=str(p.get("base_url") or ""),
                       has_key=bool(str(p.get("api_key") or "").strip()),
                       api_key_masked=_mask(str(p.get("api_key") or "")),
                       source="text_chat.providers", ready=bool(p.get("model")) and bool(str(p.get("api_key") or "").strip()))
        out["note"] = "打字聊天/工具调用；可填多家按 auto 轮询"
    elif slot == SLOT_CHAT and loc == LOC_LOCAL:
        m = str(_dig(raw, "ollama.model", "") or "")
        out.update(model=m, base_url=str(_dig(raw, "ollama.base_url", "") or "http://127.0.0.1:11434"),
                   has_key=True, source="ollama", ready=bool(m))
        out["note"] = "⚠️ 双工占用显卡时本地端口会被跳过、回落云端（见下方 note2）"
        out["note2"] = ("若开了本地双工（或本地视觉），聊天会自动跳过本地端口以让出显存 → "
                        "表现为『配了本地却仍走云端』。想让本地跑文字，请只开文字/关掉双工视觉占用。")
    elif slot == SLOT_VISION and loc == LOC_CLOUD:
        vm = _dig(raw, "vision_models", {}) or {}
        act = str(vm.get("active", "") or "")
        ent = _find_vision_entry(vm, act) or _first_vision_entry(vm)
        if ent:
            out.update(model=str(ent.get("model") or ""), base_url=str(ent.get("base_url") or ""),
                       has_key=bool(str(ent.get("api_key") or "").strip()),
                       api_key_masked=_mask(str(ent.get("api_key") or "")),
                       source="vision_models", ready=bool(ent.get("model")) and bool(str(ent.get("api_key") or "").strip()))
        out["note"] = "看屏/看图；已与核心 LLM 的 vision 开关解耦（10-04 已修）"
    elif slot == SLOT_VISION and loc == LOC_LOCAL:
        m = str(_dig(raw, "ollama.vision_model", "") or "")
        out.update(model=m, base_url=str(_dig(raw, "ollama.base_url", "") or "http://127.0.0.1:11434"),
                   has_key=True, source="ollama.vision_model", ready=bool(m))
        out["note"] = "本地看屏；吃显存，开了会与本地双工/游戏争抢"
    return out


def _mask(k: str) -> str:
    """api_key 掩码：只显示首尾各 4 位，不泄密。"""
    k = (k or "").strip()
    if not k:
        return ""
    if len(k) <= 10:
        return "*" * len(k)
    return k[:4] + "*" * 6 + k[-4:]


def _pick_cloud_chat_provider(raw: dict) -> Optional[dict]:
    """取 text_chat.providers 里「当前生效」的那个云端聊天端口。

    ⚠️ 曾经这里直接"取第一个带 key 的云端端口"，于是多端口时：
        设置页显示 providers[0].model（3.7），而聊天页显示 manual 选中的
        那个端口的 model（3.8）—— 同一条配置、两个地方显示不同，用户以为切换没生效。
    现在优先级与聊天页 (get_text_chat_status / router.set_mode('manual', pid)) 对齐：
        1) text_chat.manual_provider 指向的那个端口（聊天页"当前"= 它）；
        2) manual_provider 缺失/失效 → 回退第一个带 key 的云端端口（保持旧行为不炸）。
    """
    providers = _dig(raw, "text_chat.providers", []) or []
    manual_id = str(_dig(raw, "text_chat.manual_provider", "") or "").strip()
    if manual_id:
        for p in providers:
            if not isinstance(p, dict) or str(p.get("id") or "") != manual_id:
                continue
            pv = str(p.get("provider") or "").lower()
            # 必须是「云端 + 带 key + 有 model」的端口才认；否则落回下面的兜底，
            # 否则会出现 manual 指向一个没配 key 的端口时，把不可用的模型显示成"当前在用"。
            if pv in ("ollama", "local"):
                break
            if str(p.get("api_key") or "").strip() and str(p.get("model") or "").strip():
                return p
            break
    for p in providers:
        if not isinstance(p, dict):
            continue
        pv = str(p.get("provider") or "").lower()
        if pv in ("ollama", "local"):
            continue
        if str(p.get("api_key") or "").strip() and str(p.get("model") or "").strip():
            return p
    return None


def _find_vision_entry(vm: dict, vid: str) -> Optional[dict]:
    for e in (vm.get("models") or []):
        if isinstance(e, dict) and str(e.get("id") or "") == str(vid or ""):
            return e
    return None


def _first_vision_entry(vm: dict) -> Optional[dict]:
    """回退：取第一个已启用且有 key 的视觉条目。"""
    for e in (vm.get("models") or []):
        if not isinstance(e, dict):
            continue
        if not e.get("enabled", True):
            continue
        if str(e.get("api_key") or "").strip() or str(e.get("provider") or "") == "ollama":
            return e
    return None


def read_all(raw: dict) -> dict:
    """读全部 6 个槽位。UI 一次拿全，渲染成 2×3 网格。"""
    return {"%s_%s" % (loc, slot): read_slot(raw, loc, slot) for loc, slot in SLOT_GRID}


# ===== 写：只写自己的节点，绝不碰别人的 =====

def set_slot(raw: dict, loc: str, slot: str, model: str = "", base_url: str = "",
             api_key: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    """写某个槽位。**只改该槽位自己的配置节点**，这是 10-04 解耦的核心。

    `api_key=None` 表示"不改动 key"（前端只传用户实际输入了的字段）。
    返回 {ok, slot, loc, source, model, changed, message}。
    """
    extra = extra or {}
    model = (model or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    key_changed = api_key is not None and str(api_key).strip() != ""
    key = str(api_key).strip() if key_changed else ""

    try:
        if slot == SLOT_DUPLEX and loc == LOC_CLOUD:
            _put(raw, "cloud_omni.model", model)
            if base_url:
                _put(raw, "cloud_omni.base_url", base_url)
            if key_changed:
                _put(raw, "cloud_omni.api_key", key)
            src = "cloud_omni"
        elif slot == SLOT_DUPLEX and loc == LOC_LOCAL:
            # 本地双工是"选引擎"，model 传引擎值
            eng = model or "local_omni"
            if eng not in ("local_omni", "local"):
                return {"ok": False, "message": f"未知的本地双工引擎: {eng!r}"}
            _put(raw, "proactive.duplex_mode", "local_omni" if eng == "local" else eng)
            if base_url:
                _put(raw, "proactive.duplex_local_omni_endpoint", base_url)
            src = "proactive.duplex_mode"
        elif slot == SLOT_CHAT and loc == LOC_CLOUD:
            p = _pick_cloud_chat_provider(raw)
            if p is None:
                # 没有现成云端端口 → 建议走 text_chat.providers 完整配置
                return {"ok": False, "source": "text_chat.providers",
                        "message": "还没有云端聊天端口。请用『多厂商并存』区添加一个端口，"
                                   "或先在聊天设置里选一个云端厂商。"}
            if model:
                p["model"] = model
            if base_url:
                p["base_url"] = base_url
            if key_changed:
                p["api_key"] = key
            src = "text_chat.providers"
        elif slot == SLOT_CHAT and loc == LOC_LOCAL:
            _put(raw, "ollama.model", model)
            if base_url:
                _put(raw, "ollama.base_url", base_url)
            src = "ollama"
        elif slot == SLOT_VISION and loc == LOC_CLOUD:
            vm = _dig(raw, "vision_models", None)
            if not isinstance(vm, dict):
                vm = {}
                _put(raw, "vision_models", vm)
            act = str(vm.get("active") or "")
            ent = _find_vision_entry(vm, act) or _first_vision_entry(vm)
            if ent is None:
                models = vm.get("models")
                if not isinstance(models, list):
                    models = []
                    vm["models"] = models
                ent = {"id": "custom-cloud-vision", "name": model or "自定义云端视觉",
                       "provider": "openai", "enabled": True, "vision": True,
                       "timeout_s": 60}
                models.append(ent)
                vm["active"] = ent["id"]
            if model:
                ent["model"] = model
            if base_url:
                ent["base_url"] = base_url
            if key_changed:
                ent["api_key"] = key
            # 归一 provider 别名（10-04 已加 _PROV_ALIAS，这里写入规范值）
            ent["provider"] = "openai"
            ent.setdefault("vision", True)
            src = "vision_models"
        elif slot == SLOT_VISION and loc == LOC_LOCAL:
            _put(raw, "ollama.vision_model", model)
            if base_url:
                _put(raw, "ollama.base_url", base_url)
            src = "ollama.vision_model"
        else:
            return {"ok": False, "message": f"未知槽位: {loc}/{slot}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"写入失败: {type(e).__name__}: {e}"}

    return {"ok": True, "slot": slot, "loc": loc, "source": src, "model": model,
            "key_changed": key_changed, "changed": True,
            "message": f"已写入 {src}"}


# ===== 预设 =====

def presets_for(slot: str, loc: str) -> list:
    """某槽位可用的厂商预设列表（UI 下拉直接用）。"""
    out = []
    for v in VENDOR_PRESETS:
        # 本地槽位只列 ollama；云端槽位排除 ollama
        if loc == LOC_LOCAL and v["id"] != "ollama":
            continue
        if loc == LOC_CLOUD and v["id"] == "ollama":
            continue
        out.append({
            "id": v["id"], "name": v["name"], "base_url": v["base_url"],
            "need_key": v["need_key"], "key_url": v.get("key_url", ""),
            "hint": v.get("hint", ""),
            "models": list((v.get("models") or {}).get(slot) or []),
        })
    return out


def list_presets() -> dict:
    """全部预设（UI 一次性拿全，避免多次跨桥调用）。"""
    return {
        "grid": [{"loc": loc, "slot": slot,
                  "title": SLOT_META[slot]["title"],
                  "desc": SLOT_META[slot]["desc"]}
                 for loc, slot in SLOT_GRID],
        "presets": {"%s_%s" % (loc, slot): presets_for(slot, loc)
                    for loc, slot in SLOT_GRID},
        "local_duplex_engines": LOCAL_DUPLEX_ENGINES,
        "meta": SLOT_META,
    }


# ===== 一致性体检（给设置页的"检查配置"用）=====

def diagnose(raw: dict) -> list:
    """检查模型配置的一致性问题，返回可读告警列表。

    定位那些"用户不知道自己配错了"的经典坑（例如视觉模型填了别名导致整条路由
    构造失败、聊天端口无 key 却参与轮询占冷却等）。
    """
    warns: list = []

    # 1) 视觉条目 provider 别名必须归一（否则 vision_router 直接 raise → 视觉全灭）
    vm = _dig(raw, "vision_models", {}) or {}
    for e in (vm.get("models") or []):
        if not isinstance(e, dict):
            continue
        pv = str(e.get("provider") or "").strip().lower()
        if pv and pv not in ("openai", "ollama", "minicpm"):
            warns.append({
                "level": "warn", "where": "vision_models",
                "msg": f"视觉模型 {e.get('id') or e.get('name')!r} 的 provider={pv!r} "
                       f"不是规范值（应为 openai/ollama/minicpm）；10-04 已加别名兼容，"
                       f"但建议在设置里改回规范值以免旧版或其它工具读不懂。"})

    # 2) 云端聊天端口无 key 却仍在池里（空跑占冷却）
    for p in (_dig(raw, "text_chat.providers", []) or []):
        if not isinstance(p, dict):
            continue
        pv = str(p.get("provider") or "").lower()
        if pv and pv != "ollama" and not str(p.get("api_key") or "").strip():
            warns.append({
                "level": "info", "where": "text_chat.providers",
                "msg": f"聊天端口 {p.get('id')!r} 没填 api_key。已具备『空 key 跳过』保护"
                       f"（10-04 已修别名归一），但若你是想启用它，请补 key。"})

    # 3) 本地双工 + 本地聊天 的显存互斥（用户最常踩："配了本地却走云端"）
    dm = str(_dig(raw, "proactive.duplex_mode", "") or "")
    if dm in ("local_omni", "local") and str(_dig(raw, "ollama.model", "") or ""):
        warns.append({
            "level": "info", "where": "proactive.duplex_mode",
            "msg": "已开启本地双工：聊天会自动跳过本地 Ollama 端口（让出显存）→ "
                   "表现为『配了本地却仍走云端』。这是预期的资源保护，不是 bug。"})

    # 4) 云端双工模型与音色家族不匹配（会静默哑）
    dm2 = str(_dig(raw, "cloud_omni.model", "") or "").lower()
    voice = str(_dig(raw, "cloud_omni.voice", "") or "")
    if dm2 and voice and voice.startswith("qwen-omni") and "omni" not in dm2:
        warns.append({
            "level": "warn", "where": "cloud_omni",
            "msg": f"云端双工模型 {dm2!r} 与音色 {voice!r} 不是同一家族，可能报 "
                   f"『voice could not be found』。10-04 已加自动作废重置，建议重设一次音色。"})

    # 5) 视觉全部无 key
    vis_ok = False
    for e in (vm.get("models") or []):
        if isinstance(e, dict) and e.get("enabled", True):
            if str(e.get("provider") or "") == "ollama" or str(e.get("api_key") or "").strip():
                vis_ok = True
                break
    if not vis_ok and str(_dig(raw, "ollama.vision_model", "") or "") == "":
        warns.append({
            "level": "warn", "where": "vision_models",
            "msg": "没有任何可用的视觉模型（云端全无 key、本地未指定）→ 看屏/看图不可用。"})

    return warns
