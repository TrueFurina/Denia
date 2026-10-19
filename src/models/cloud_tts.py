"""云端 TTS 多服务商归一与音色列表获取（2026-10-09 重构）。

【为什么有这个文件】
原 `list_cloud_voices` / `ensure_cloud_voice_profile`（webapp.py）与 `catalog.py`
各自内联了一份「读 raw.voice.clone.cloud → 按 provider 分支」的逻辑，且
`raw.voice.clone.cloud` 是**单 provider 结构**（一次只能配一个云端 TTS 服务商）。
能力槽架构要让阿里云 / MiniMax / OpenAI / Fish Audio **并存**，故把：
  1) 单 provider → 多 provider 列表的归一（`_cloud_tts_providers`）
  2) 各服务商「克隆音色 + 预设音色」的获取（`fetch_provider_voices`）
抽到本模块，webapp.py / catalog.py 共用同一份真值，且可脱离 giant Backend 单测。

【数据形状】
raw.voice.clone.cloud 兼容两种写法：
  · 旧（单 provider）：{provider, api_key, group_id, model_name, api_url}
  · 新（多 provider 分桶）：{dashscope_cosy:{...}, minimax:{...}, fish_audio:{...}, ...}
归一后统一为 {provider: {api_key, group_id, model_name, api_url}}。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 预设 TTS 服务商元信息。新增服务商只需在此登记 + 在 fetch_provider_voices 加分支。
CLOUD_TTS_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "dashscope_cosy": {"label": "阿里云百炼", "clone": True, "needs_group": False},
    "minimax": {"label": "MiniMax", "clone": True, "needs_group": True},
    "openai_tts": {"label": "OpenAI 兼容", "clone": False, "needs_group": False},
    "fish_audio": {"label": "Fish Audio", "clone": True, "needs_group": False},
}
KNOWN = set(CLOUD_TTS_PROVIDERS.keys())

# 历史别名 → 规范 provider（数据里出现过 aliyun / bailian / dashscope_cosyvoice 等写法）。
PROVIDER_ALIASES = {
    "dashscope_cosyvoice": "dashscope_cosy",
    "bailian": "dashscope_cosy",
    "aliyun": "dashscope_cosy",
    "azure_cosy": "dashscope_cosy",
}

# 各服务商的「系统预设音色」（克隆音色由 API 实时查）。Fish Audio 无系统预设。
_PRESETS: Dict[str, List[Dict[str, str]]] = {
    # 【2026-10-17 修复·预设模型全失效】
    # 实测矩阵（2026-10-17）：
    #   cosyvoice-v2 / v3 / v3-plus / v3-flash  → 400（已下线）
    #   qwen-audio-3.0-tts-plus / -flash        → 400（已下线）
    #   qwen3-tts-flash                          → 200 ✅（唯一可用）
    # 且预设的语义应是"音色"不是"模型"：qwen3-tts-flash 支持若干官方预置音色。
    # 这里列出实测可用的预置音色（Cherry / Serena / Ethan / Chelsie）。
    "dashscope_cosy": [
        {"id": "Cherry",   "name": "Cherry（温柔女声·推荐）", "type": "preset", "model": "qwen3-tts-flash"},
        {"id": "Serena",   "name": "Serena（知性女声）",     "type": "preset", "model": "qwen3-tts-flash"},
        {"id": "Chelsie",  "name": "Chelsie（清亮女声）",    "type": "preset", "model": "qwen3-tts-flash"},
        {"id": "Ethan",    "name": "Ethan（男声）",          "type": "preset", "model": "qwen3-tts-flash"},
    ],
    "minimax": [
        {"id": "female-shaonv", "name": "少女（female-shaonv）", "type": "preset", "model": "speech-02-turbo"},
        {"id": "male-qn-qingse", "name": "青叔（male-qn-qingse）", "type": "preset", "model": "speech-02-turbo"},
        {"id": "female-yujie", "name": "御姐（female-yujie）", "type": "preset", "model": "speech-02-hd"},
        {"id": "male-qn-dailiang", "name": "大力（male-qn-dailiang）", "type": "preset", "model": "speech-02-hd"},
    ],
    "openai_tts": [
        {"id": "alloy", "name": "Alloy", "type": "preset", "model": ""},
        {"id": "echo", "name": "Echo", "type": "preset", "model": ""},
        {"id": "fable", "name": "Fable", "type": "preset", "model": ""},
        {"id": "onyx", "name": "Onyx", "type": "preset", "model": ""},
        {"id": "nova", "name": "Nova", "type": "preset", "model": ""},
        {"id": "shimmer", "name": "Shimmer", "type": "preset", "model": ""},
    ],
    "fish_audio": [],  # Fish Audio 只有用户克隆出的模型，无系统预设
}


def _cloud_tts_providers(raw: dict) -> Dict[str, Dict[str, str]]:
    """把 raw.voice.clone.cloud 归一为 {provider: {api_key, group_id, model_name, api_url}}。

    兼容旧单层格式与新多服务商分桶格式；任意一种都能被上层无感消费。
    只读 raw，不原地修改。
    """
    cloud = (raw.get("voice", {}) or {}).get("clone", {}) or {}
    cloud = cloud.get("cloud", {}) or {}
    if not isinstance(cloud, dict):
        return {}
    # 【2026-10-17 修复·need_key 误判】
    # 用户实际是在「设置 → 模型与API → 一键配置」里填的 Key，
    # 那份 Key 落在 cloud_tts / cloud_omni，**不在** voice.clone.cloud。
    # 旧实现只读 voice.clone.cloud → need_key=True → 角色卡把已配好的服务
    # 当成"没配 Key"隐藏起来，用户只能手填一遍（这就是"又要搞一遍 key"的根源）。
    # 现在补上多源回退：voice.clone.cloud → cloud_tts → cloud_omni。
    _fallback_key = ""
    _fallback_model = ""
    try:
        _ct = (raw.get("cloud_tts") or {})
        if isinstance(_ct, dict):
            _b = _ct.get("dashscope_cosy") or {}
            if isinstance(_b, dict):
                _fallback_key = str(_b.get("api_key") or "")
                _fallback_model = str(_b.get("model_name") or "")
    except Exception:  # noqa: BLE001
        pass
    if not _fallback_key:
        try:
            _co = (raw.get("cloud_omni") or {})
            if isinstance(_co, dict):
                _fallback_key = str(_co.get("api_key") or "")
                _fallback_model = _fallback_model or str(_co.get("model") or "")
        except Exception:  # noqa: BLE001
            pass

    def _fill(d: dict) -> dict:
        """补齐空 Key/模型（用全局配置兜底）。"""
        out = dict(d or {})
        if not str(out.get("api_key") or "").strip() and _fallback_key:
            out["api_key"] = _fallback_key
        if not str(out.get("model_name") or "").strip() and _fallback_model:
            out["model_name"] = _fallback_model
        return out

    # 新格式：顶层就是 provider 分桶
    if any(k in KNOWN for k in cloud.keys()):
        out: Dict[str, Dict[str, str]] = {}
        for k, v in cloud.items():
            if k in KNOWN and isinstance(v, dict):
                out[k] = _fill(v)
        # 若分桶里没有 dashscope_cosy 但有全局 Key，补一个（用户配了百炼但没走分桶）
        if "dashscope_cosy" not in out and _fallback_key:
            out["dashscope_cosy"] = _fill({})
        return out
    # 旧格式：单层 provider / api_key / ...
    if "api_key" in cloud or "provider" in cloud or cloud:
        prov = (cloud.get("provider") or "dashscope_cosy").strip()
        prov = PROVIDER_ALIASES.get(prov, prov)
        return {
            prov: _fill({
                "api_key": cloud.get("api_key") or "",
                "group_id": cloud.get("group_id") or "",
                "model_name": cloud.get("model_name") or "",
                "api_url": cloud.get("api_url") or "",
            })
        }
    # 完全没有 cloud 配置，但有全局 Key → 返回一个 dashscope_cosy
    if _fallback_key:
        return {"dashscope_cosy": _fill({})}
    return {}


def fetch_provider_voices(
    provider: str,
    cfg: Dict[str, str],
    list_bailian_fn: Optional[Callable[[str], dict]] = None,
) -> List[Dict[str, str]]:
    """获取单个服务商可绑定的云端音色列表 [{id, name, type, model}]。

    cfg: {api_key, group_id, model_name, api_url}（来自 _cloud_tts_providers）。
    克隆音色走各厂商 API 实时查；预设音色来自 _PRESETS。
    网络异常被吞掉（静默容错红线：列表取不到不能让整个下拉崩），返回已能拿到的部分。
    """
    meta = CLOUD_TTS_PROVIDERS.get(provider, {"label": provider, "clone": False, "needs_group": False})
    api_key = (cfg.get("api_key") or "").strip()
    group_id = (cfg.get("group_id") or "").strip()
    model_name = (cfg.get("model_name") or "").strip()
    voices: List[Dict[str, str]] = []
    try:
        if provider == "dashscope_cosy":
            if api_key and list_bailian_fn is not None:
                try:
                    r = list_bailian_fn(api_key)
                except Exception:  # noqa: BLE001
                    r = None
                if r and r.get("success"):
                    for v in (r.get("voices") or []):
                        vid = v.get("voice_id") or ""
                        if vid:
                            voices.append({
                                "id": vid,
                                "name": "克隆音色 " + vid,
                                "type": "cloned",
                                "model": v.get("target_model") or model_name,
                            })
            voices += _PRESETS["dashscope_cosy"]
        elif provider == "minimax":
            if api_key and group_id:
                try:
                    import httpx
                    resp = httpx.Client(timeout=20).get(
                        "https://api.minimaxi.com/v1/voice_clone/list",
                        params={"groupId": group_id, "page_index": 0, "page_size": 50},
                        headers={"Authorization": f"Bearer {api_key}"})
                    if resp.status_code < 400:
                        j = resp.json() or {}
                        for v in (j.get("voice_list") or j.get("voices") or []):
                            vid = v.get("voice_id") or ""
                            if vid:
                                voices.append({"id": vid, "name": "克隆音色 " + vid,
                                               "type": "cloned", "model": ""})
                except Exception:  # noqa: BLE001
                    logger.warning("查询 MiniMax 克隆音色失败", exc_info=True)
            voices += _PRESETS["minimax"]
        elif provider == "openai_tts":
            voices = list(_PRESETS["openai_tts"])
        elif provider == "fish_audio":
            if api_key:
                try:
                    import httpx
                    resp = httpx.Client(timeout=20).get(
                        "https://api.fish.audio/model",
                        params={"self": "true", "page_size": 50},
                        headers={"Authorization": f"Bearer {api_key}"})
                    if resp.status_code < 400:
                        j = resp.json() or {}
                        for v in (j.get("items") or []):
                            vid = v.get("_id") or v.get("id") or ""
                            if vid:
                                voices.append({
                                    "id": vid,
                                    "name": (v.get("title") or vid) + "（Fish Audio）",
                                    "type": "cloned", "model": ""})
                except Exception:  # noqa: BLE001
                    logger.warning("查询 Fish Audio 音色失败", exc_info=True)
            if not voices:
                voices.append({"id": "", "name": "（在 Fish Audio 控制台克隆音色后自动出现）",
                               "type": "preset", "model": ""})
    except Exception:  # noqa: BLE001
        logger.exception("获取 %s 云端音色失败", provider)
    return voices


__all__ = [
    "CLOUD_TTS_PROVIDERS",
    "KNOWN",
    "PROVIDER_ALIASES",
    "_cloud_tts_providers",
    "fetch_provider_voices",
    "merge_cloud_tts_provider",
]


def merge_cloud_tts_provider(raw: dict, provider: str, api_key: str = "",
                             group_id: str = "", model_name: str = "",
                             api_url: str = "") -> None:
    """把单个服务商配置写入 raw.voice.clone.cloud（多 provider 分桶），**保留其它服务商**。

    原地修改 raw，不返回。用于「设置页/能力槽」保存单个 TTS 服务商时不会覆盖已配的其它服务商。
    规则：
      · provider 经别名归一（aliyun/bailian → dashscope_cosy）。
      · 若 cloud 已是多 provider 分桶（顶层含已知 provider 键）→ 只写对应桶。
      · 若 cloud 是旧单层结构 → 先把它提升为单桶，再写入新 provider，避免覆盖丢失。
      · 空 cloud → 直接成为 {provider: {...}}。
      · **空字符串=不改**：某字段传空/缺省时保留该桶已存值——避免调用方只回传部分字段
        （如引导页「已绑 TTS」只带 provider/model/voice_id）时把已存 api_key 静默擦成空。
    """
    provider = (PROVIDER_ALIASES.get(provider, provider) or "").strip()
    if not provider:
        return
    clone = raw.setdefault("voice", {}).setdefault("clone", {})
    cc = clone.get("cloud") or {}
    if not isinstance(cc, dict):
        cc = {}
    if any(k in KNOWN for k in cc.keys()):
        pass  # 已是多 provider 分桶
    elif "api_key" in cc or "provider" in cc:
        oldp = PROVIDER_ALIASES.get(cc.get("provider") or "dashscope_cosy", "dashscope_cosy")
        cc = {oldp: {
            "api_key": cc.get("api_key") or "",
            "group_id": cc.get("group_id") or "",
            "model_name": cc.get("model_name") or "",
            "api_url": cc.get("api_url") or "",
        }}
    else:
        cc = {}
    # 空值不改（契约「空字符串=不改」）：某字段传空时保留该桶已存值。
    # 修 F3：原来无条件写 api_key or "" —— 引导页/角色保存只回传部分字段时会静默清空已配 Key。
    _prev = cc.get(provider) if isinstance(cc.get(provider), dict) else {}
    cc[provider] = {
        "api_key": api_key or _prev.get("api_key") or "",
        "group_id": group_id or _prev.get("group_id") or "",
        "model_name": model_name or _prev.get("model_name") or "",
        "api_url": api_url or _prev.get("api_url") or "",
    }
    clone["cloud"] = cc
