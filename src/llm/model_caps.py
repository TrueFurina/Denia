"""模型能力推断 / 预置 / 真实探测（纯标准库，无重依赖，可独立单测）。

能力真相源的三级优先级：
  1. 用户手填/确认  explicit   → source="用户"   verified=True
  2. 预置模型库      PRESET_CAPS（来自官方文档+实测，写死）→ source="预设" verified=True
  3. 名字关键词推断  _KW 规则     → source="推断"   verified=False  （仅兜底）
  此外，自定义模型填了 API 后，可以跑 probe_capabilities 真实发请求探测图片/音频支持
  → source="实测" verified=True，覆盖上面的推断。

能力词汇表（前端 model_caps._CAP_META 提供中文标签 + 图标名）：
  inputs : text(文本) image(图片) audio(音频) video(视频)
  outputs: text(文本) image(图片) audio(音频)
  features: tools(工具调用) reasoning(深度推理) realtime(实时双工)
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

# —— 能力元数据：中文标签 + 图标名（assets/icons.js 已补全 type/image/mic/video/
#    tool/brain/zap/file-text）。group 用于 UI 分组渲染。
_CAP_META = {
    "text":     {"label": "文本",  "icon": "type",       "group": "input"},
    "image":    {"label": "图片",  "icon": "image",      "group": "input"},
    "audio":    {"label": "音频",  "icon": "mic",        "group": "input"},
    "video":    {"label": "视频",  "icon": "video",      "group": "input"},
    "tools":    {"label": "工具调用", "icon": "tool",    "group": "feature"},
    "reasoning":{"label": "深度推理", "icon": "brain",   "group": "feature"},
    "realtime": {"label": "实时双工", "icon": "zap",     "group": "feature"},
}

_VALID_INPUTS = ("text", "image", "audio", "video")
_VALID_OUTPUTS = ("text", "image", "audio")
_VALID_FEATURES = ("tools", "reasoning", "realtime")

# —— 预置模型能力库（已核实：阿里云百炼官方文档 + 实测，2026-09 校订）——
# 关键事实（用户反馈后上网核实）：
#   * qwen3.8-max / qwen3.8-flash / qwen3.7-plus / qwen3.6-plus / qwen3.7-flash /
#     qwen3.5-* / qwen3-vl-* 全部原生支持图片（部分支持视频）输入；
#   * qwen3.7-max、经典 qwen-plus/max/turbo、deepseek-v3/r1、glm-4-flash、本地 qwen3 是纯文本；
#   * qwen-vl-* 视觉模型 = 图片+视频输入、文本输出。
PRESET_CAPS = {
    # —— 千问 3.8 代（多模态旗舰/轻量）——
    "qwen3.8-max":  dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools", "reasoning"], desc="千问3.8旗舰：文本+图片+视频输入，工具调用/深度推理，最长2h视频。"),
    "qwen3.8-flash": dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools", "reasoning"], desc="千问3.8轻量多模态：图文+视频输入，工具调用，高并发低成本（本软件默认聊天模型）。"),
    # —— 千问 3.7 代 ——
    "qwen3.7-max":  dict(inputs=["text"], outputs=["text"],
                         features=["tools", "reasoning"], desc="千问3.7纯文本旗舰：不支持图片/视频，深度推理与长文本最强。"),
    "qwen3.7-plus": dict(inputs=["text", "image"], outputs=["text"],
                         features=["tools", "reasoning"], desc="千问3.7均衡多模态：图文输入，GUI截图/视觉理解突出，工具调用。"),
    "qwen3.7-flash": dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools", "reasoning"], desc="千问3.7轻量多模态：图文+视频输入，基础工具调用。"),
    # —— 千问 3.6 / 3.5 代 ——
    "qwen3.6-plus": dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools", "reasoning"], desc="千问3.6均衡多模态：图文+视频输入，工具调用。"),
    "qwen3.6-flash": dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools"], desc="千问3.6轻量多模态：图文+视频输入。"),
    "qwen3.5-plus": dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools"], desc="千问3.5多模态：图文+视频输入。"),
    "qwen3.5-flash": dict(inputs=["text", "image", "video"], outputs=["text"],
                         features=["tools"], desc="千问3.5轻量多模态：图文+视频输入。"),
    # —— 千问 VL 视觉系列 ——
    "qwen3-vl-plus":  dict(inputs=["text", "image", "video"], outputs=["text"],
                          features=["tools"], desc="千问VL视觉：图片/视频理解，OCR与视觉问答。"),
    "qwen3-vl-flash": dict(inputs=["text", "image", "video"], outputs=["text"],
                          features=["tools"], desc="千问VL轻量视觉：图片/视频理解，低延迟。"),
    "qwen-vl-plus":  dict(inputs=["text", "image", "video"], outputs=["text"], features=[],
                         desc="千问VL-Plus视觉：图片/视频理解。"),
    "qwen-vl-max":   dict(inputs=["text", "image", "video"], outputs=["text"], features=[],
                         desc="千问VL-Max视觉：图片/视频理解，更强。"),
    "qwen-vl-ocr":   dict(inputs=["text", "image"], outputs=["text"], features=[],
                         desc="千问VL-OCR：偏文字/表格/文档识别。"),
    # —— 经典千问（旧代，纯文本）——
    "qwen-plus":   dict(inputs=["text"], outputs=["text"], features=[], desc="千问Plus（经典）：纯文本。"),
    "qwen-max":    dict(inputs=["text"], outputs=["text"], features=[], desc="千问Max（经典）：纯文本。"),
    "qwen-turbo":  dict(inputs=["text"], outputs=["text"], features=[], desc="千问Turbo（经典）：纯文本。"),
    "qwen-plus-character":   dict(inputs=["text"], outputs=["text"], features=[], desc="千问Plus角色扮演版：纯文本。"),
    "qwen-flash-character":  dict(inputs=["text"], outputs=["text"], features=[], desc="千问Flash角色扮演版：纯文本。"),
    # —— DeepSeek（纯文本，无原生视觉）——
    "deepseek-v3":            dict(inputs=["text"], outputs=["text"], features=["tools", "reasoning"], desc="DeepSeek-V3：纯文本，强，支持工具调用。"),
    "deepseek-r1":            dict(inputs=["text"], outputs=["text"], features=["reasoning"], desc="DeepSeek-R1：纯文本，深度推理（思维链）。"),
    "deepseek-ai/deepseek-v3": dict(inputs=["text"], outputs=["text"], features=["tools", "reasoning"], desc="硅基流动 DeepSeek-V3：纯文本，工具调用。"),
    "deepseek-ai/deepseek-r1": dict(inputs=["text"], outputs=["text"], features=["reasoning"], desc="硅基流动 DeepSeek-R1：纯文本，深度推理。"),
    # —— 智谱 ——
    "glm-4-flash": dict(inputs=["text"], outputs=["text"], features=[], desc="智谱GLM-4-Flash：纯文本（GLM-4V 才支持图片）。"),
    "glm-4v-flash": dict(inputs=["text", "image"], outputs=["text"], features=[], desc="智谱GLM-4V-Flash：图文输入。"),
    # —— 本地 Ollama 文本（qwen3:14b 之类，需换 VL 模型才支持图片）——
    "qwen3:14b": dict(inputs=["text"], outputs=["text"], features=[], desc="本地Ollama Qwen3-14B：纯文本（换 VL 模型才支持图片）。"),
    "qwen2.5:14b": dict(inputs=["text"], outputs=["text"], features=[], desc="本地Ollama Qwen2.5-14B：纯文本。"),
}

# 名字关键词 → 能力（仅作"未知模型"的兜底推断，verified=False）
_KW_INPUT_IMAGE = ("vl", "vision", "visual", "ocr", "qvq", "qwen-vl", "qwen3-vl",
                   "qwen2-vl", "glm-v", "glm-4v", "claude", "gpt-4o", "gpt4o",
                   "gemini", "pixtral", "internvl", "minicpm-v", "smolvlm", "moondream")
_KW_INPUT_AUDIO = ("omni", "qwen-audio", "qwen2-audio", "whisper", "cosyvoice",
                   "fish-audio", "spark-tts")
_KW_REALTIME = ("realtime", "duplex", "实时", "双工")
_KW_REASONING = ("r1", "reasoning", "think", "deep-think", "推理", "qwq",
                 "deepseek-r", "-o1", "-o3")
_KW_TOOLS = ("工具", "tool", "function", "funcall", "agent", "function-call", "fc", "支持工具")


# —— 1x1 有效 PNG，用于探测图片输入是否被接受 ——
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _cap_meta():
    return _CAP_META


def _norm_list(val, allowed):
    if not val:
        return []
    out = []
    for v in val:
        if not isinstance(v, str):
            continue
        v = v.strip().lower()
        if v in allowed and v not in out:
            out.append(v)
    return out


def _hit(text: str, kws) -> bool:
    return any(k in text for k in kws)


def _describe(inputs, outputs, features) -> str:
    parts = []
    if "image" in inputs:
        parts.append("支持图片输入，可看图/看视频/视觉问答")
    if "audio" in inputs:
        parts.append("支持语音/音频输入")
    if "video" in inputs:
        parts.append("支持视频理解")
    if "realtime" in features:
        parts.append("实时全双工对话")
    if "reasoning" in features:
        parts.append("具备深度推理能力")
    if "tools" in features:
        parts.append("支持工具调用/智能体")
    if not parts:
        parts.append("纯文本对话")
    return "；".join(parts)


def _fallback_infer(model: str, name: str, provider: str) -> dict:
    """名字关键词兜底推断（verified=False）。"""
    blob = " ".join([str(model or ""), str(name or ""), str(provider or "")]).lower()
    inputs, outputs, features = {"text"}, {"text"}, set()
    if _hit(blob, _KW_INPUT_IMAGE):
        inputs.add("image")
    if _hit(blob, _KW_INPUT_AUDIO):
        inputs.add("audio")
    if _hit(blob, _KW_REALTIME):
        features.add("realtime")
    if _hit(blob, _KW_REASONING):
        features.add("reasoning")
    if _hit(blob, _KW_TOOLS):
        features.add("tools")
    inputs, outputs, features = sorted(inputs), sorted(outputs), sorted(features)
    return {
        "inputs": inputs, "outputs": outputs, "features": features,
        "verified": False, "source": "推断", "desc": _describe(inputs, outputs, features),
    }


def infer_caps(model: str = "", name: str = "", provider: str = "",
               explicit: dict | None = None) -> dict:
    """推断单个模型的 caps，按 用户 > 预置 > 名字推断 的优先级。"""
    # 1) 用户手填/确认 → 最高优先级
    if isinstance(explicit, dict):
        ei = _norm_list(explicit.get("inputs"), _VALID_INPUTS)
        eo = _norm_list(explicit.get("outputs"), _VALID_OUTPUTS)
        ef = _norm_list(explicit.get("features"), _VALID_FEATURES)
        if ei or eo or ef:
            inputs = sorted(set(ei)) if ei else ["text"]
            outputs = sorted(set(eo)) if eo else ["text"]
            features = sorted(set(ef))
            return {
                "inputs": inputs, "outputs": outputs, "features": features,
                "verified": True, "source": "用户",
                "desc": _describe(inputs, outputs, features),
            }

    # 2) 预置模型库（已核实）→ source="预设"
    key = (str(model or "")).strip().lower()
    preset = PRESET_CAPS.get(key)
    if preset is None:
        # 也用 name 反查（部分预设模型用展示名而非 model 串）
        nkey = (str(name or "")).strip().lower()
        for k, v in PRESET_CAPS.items():
            if k in nkey or nkey in k:
                preset = v
                break
    if preset is not None:
        inputs = _norm_list(preset.get("inputs"), _VALID_INPUTS) or ["text"]
        outputs = _norm_list(preset.get("outputs"), _VALID_OUTPUTS) or ["text"]
        features = _norm_list(preset.get("features"), _VALID_FEATURES)
        return {
            "inputs": inputs, "outputs": outputs, "features": features,
            "verified": True, "source": "预设",
            "desc": preset.get("desc") or _describe(inputs, outputs, features),
        }

    # 3) 兜底：名字关键词推断
    return _fallback_infer(model, name, provider)


def merge_caps_into(entry: dict) -> dict:
    """把一个 provider / preset / vision-model 配置 dict 推断并回填 caps 字段。"""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    explicit = entry.get("caps") if isinstance(entry.get("caps"), dict) else None
    out["caps"] = infer_caps(
        model=entry.get("model", ""),
        name=entry.get("name", ""),
        provider=entry.get("provider", ""),
        explicit=explicit,
    )
    return out


def caps_for_list(entries) -> list:
    if not entries:
        return []
    return [merge_caps_into(e) for e in entries]


# —— 真实探测：填了 API 后真的发请求试图片/音频是否被接受 ——
def _post_json(url: str, payload: dict, api_key: str, timeout_s: int):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return getattr(e, "code", 0), (e.read().decode("utf-8", "ignore") if hasattr(e, "read") else "")
    except Exception:  # noqa: BLE001
        return 0, ""


# 探测结论关键词：返回 True=确定支持图片，False=确定不支持，None=无法判定
_RE_IMG_YES = re.compile(r"image|图片|像素|pixel|size|尺寸|url|base64|multimodal|多模态|resolution", re.I)
_RE_IMG_NO = re.compile(r"unsupported|not support|does not support|cannot|only (support|accept) text|text[ -]?only|no image|invalid.*image.*model|model.*not.*vision", re.I)


def _image_supported_from_status(status: int, body: str) -> bool | None:
    if status == 200:
        return True
    if status == 400:
        # 400 但提到图片/像素/尺寸 → 端点认识图片格式，只是图太小 → 支持图片
        if _RE_IMG_YES.search(body) and not _RE_IMG_NO.search(body):
            return True
        if _RE_IMG_NO.search(body):
            return False
        # 其它 400（缺字段等）无法判定
        return None
    return None  # 401/429/500/网络等无法判定


def probe_capabilities(api_key: str, base_url: str, model: str, timeout_s: int = 20) -> dict | None:
    """对填了 API 的自定义模型真实探测能力。

    返回 caps dict（source="实测", probed=True），或 None（无法探测：缺 key/网络/auth 失败）。
    探测项：文本（可用性）+ 图片（发 1x1 PNG 看端点是否接受多模态）。
    工具调用/推理不可靠探测，保留名字推断结果叠加。
    """
    base = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    if not base or not model:
        return None
    url = base + "/chat/completions"
    if not api_key:
        return None

    # 1) 文本可用性探测
    st, body = _post_json(url, {
        "model": model, "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 4,
    }, api_key, timeout_s)
    if st != 200:
        # 鉴权/限流/网络问题 → 不能可靠探测，返回 None 让上层回退到推断
        return None

    inputs = {"text"}
    outputs = {"text"}

    # 2) 图片输入探测
    st2, body2 = _post_json(url, {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "这是什么"},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(_PNG_1X1).decode("ascii")}},
            ],
        }],
        "max_tokens": 4,
    }, api_key, timeout_s)
    img = _image_supported_from_status(st2, body2)
    if img is True:
        inputs.add("image")
    # img is False/None → 不改动（None 表示无法判定，保持文本默认）

    # 工具/推理：叠加名字推断（不靠探测，避免误判）
    blob = model.lower()
    features = set()
    if _hit(blob, _KW_REASONING):
        features.add("reasoning")
    if _hit(blob, _KW_TOOLS):
        features.add("tools")

    inputs, outputs, features = sorted(inputs), sorted(outputs), sorted(features)
    return {
        "inputs": inputs, "outputs": outputs, "features": features,
        "verified": True, "source": "实测", "probed": True,
        "image_probed": img if img is not None else "unknown",
        "desc": "实测：" + _describe(inputs, outputs, features),
    }
