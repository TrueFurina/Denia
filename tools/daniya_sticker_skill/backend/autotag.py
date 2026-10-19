"""自动标注：导入图片后自动打标，并在 UI 里允许用户改。

设计要点（按开发者要求：「分类权限交给用户」）：
- **正常走自动标注**：上传/收集到的图，自动跑一次 VLM 打标，结果直接写入。
- **UI 可改**：用户随时能改情绪词、适用场景、分类，改完标记 provenance=manual，
  **手动改过的不会被自动标注覆盖**（避免用户白改）。
- **保留原文**：自动结果存在 `auto_*` 字段，用户改的存主字段，便于对比/回滚。

记录字段（meta.json 每张图）：
  description / tags / trigger / category     <- 当前生效值（会被模型或用户覆盖）
  provenance: "ai" | "manual"                 <- 谁定的（manual 则不再自动覆盖）
  auto: {description, tags, trigger, mood_compound, anti_trigger, visible_text, model, ts}
  style_variants: {appearance, trigger, community}   <- 供选贴分档使用
"""
import asyncio
import base64
import json
import time
from pathlib import Path

from . import config, llm
from .llm import _prepare_image

# 情绪词表：要求模型在其中多选，减少"自由发挥"导致的塌缩/发散
EMOTION_VOCAB = [
    "开心", "大笑", "微笑", "幸福", "满足", "兴奋", "惊喜", "激动", "期待",
    "爱意", "心动", "撒娇", "害羞", "调皮", "得意", "俏皮",
    "哭泣", "悲伤", "难过", "委屈", "失落", "心疼", "无奈", "无语",
    "尴尬", "困惑", "惊讶", "发怔", "生气", "不满", "赌气", "烦躁",
    "困倦", "疲惫", "无聊", "走神", "敷衍", "专注", "认真", "悠哉", "放松", "安心",
    "鼓励", "安慰", "抱歉", "被欺负", "慌张",
]

SYS_AUTOTAG = """你是表情包语义标注助手，为 AI 角色挑选表情包服务。
可能给你**动图的多帧拼图**（从左到右是时间顺序），请综合整个过程判断。

【情绪多选】从下面词表中选出所有符合的词（3~8 个，宁多勿漏）：
{vocab}

【判读要点】
- 眼里含泪/嘴角向下 → 哭泣、委屈、难过、失落
- 黑脸阴影/握拳/怒气符号/皱眉瞪眼 → 生气、不满、烦躁
- 星星眼/钻石眼/张嘴大叫/冒爱心 → 激动、惊喜、兴奋
- 闭眼微笑/放松 → 满足、安心、悠哉
- 半垂眼/手托腮/打哈欠 → 困倦、无聊、走神
不要因为角色可爱就只写"可爱"，要标出**她此刻在表达什么**。
不同的图必须给出不同的情绪。

【输出 JSON】只输出 JSON，字段值必须是真实内容，不要照抄提示词里的说明文字：
{{"description":"客观画面描述（多帧则说明动作从什么变成什么）",
 "emotions":["情绪词","情绪词"],
 "trigger":"用于…时（写清用户的什么处境）",
 "mood_compound":"复合语气，2到4个真实的词，如『恼火中带玩笑』",
 "anti_trigger":"不该用于…时",
 "visible_text":"图内文字，没有就空串"}}
"""

_PLACEHOLDERS = {"画面简述", "简述", "...", "无", "N/A", "null", "none", "未知", ""}


def _clean(s) -> str:
    import re
    s = str(s or "").strip()
    if s in _PLACEHOLDERS:
        return ""
    if re.fullmatch(r"[^，。]{0,6}?\d+\s*[~～-]\s*\d+\s*个?[词字].?", s):
        return ""
    if re.fullmatch(r"(情绪词|标签)\d*", s):
        return ""
    return s


async def autotag_bytes(backend, data: bytes, model_label: str = "",
                        frames_strategy: str = "motion", frames_k: int = 3) -> dict:
    """对一张图跑自动标注，返回规范化结果（失败返回带 error 的字典）。"""
    prepared, mime = _prepare_image(data, "image/jpeg",
                                    frames_strategy=frames_strategy, frames_k=frames_k)
    url = f"data:{mime};base64,{base64.b64encode(prepared).decode()}"
    sys_p = SYS_AUTOTAG.format(vocab=" ".join(EMOTION_VOCAB))
    try:
        out = backend._chat(backend.vision_model, [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": [
                {"type": "text", "text": "请标注这个表情包"},
                {"type": "image_url", "image_url": {"url": url}}]}])
        r = llm._extract_json(out)
        if not isinstance(r, dict):
            return {"error": "模型输出无法解析"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:160]}

    emos = r.get("emotions") or []
    if not isinstance(emos, list):
        emos = [str(emos)]
    emos = [str(x).strip() for x in emos if str(x).strip()][:10]
    return {
        "description": _clean(r.get("description")),
        "tags": emos,
        "trigger": _clean(r.get("trigger")),
        "mood_compound": _clean(r.get("mood_compound")),
        "anti_trigger": _clean(r.get("anti_trigger")),
        "visible_text": _clean(r.get("visible_text")),
        "model": model_label or getattr(backend, "vision_model", ""),
        "ts": time.time(),
        "frames": frames_strategy,
    }


def apply_autotag_to_meta(entry: dict, res: dict) -> dict:
    """把自动标注结果写进一条 meta（不改被用户手改过的）。

    返回更新后的 entry。规则：
      - provenance == "manual"  -> **不动**（用户说了算），只留 auto 参考
      - 否则 -> 写入主字段 + provenance="ai"
    """
    if not res or res.get("error"):
        entry.setdefault("auto", {})
        entry["auto_error"] = (res or {}).get("error", "")
        return entry
    entry["auto"] = res
    if entry.get("provenance") == "manual":
        return entry  # 尊重用户手改
    entry["description"] = res.get("description") or entry.get("description", "")
    entry["tags"] = res.get("tags") or entry.get("tags") or []
    entry["trigger"] = res.get("trigger") or entry.get("trigger", "")
    entry["visible_text"] = res.get("visible_text") or entry.get("visible_text", "")
    entry["provenance"] = "ai"
    entry["style_variants"] = {
        "appearance": {"description": entry["description"], "tags": entry["tags"]},
        "trigger": {"description": entry["description"], "trigger": entry["trigger"],
                    "tags": entry["tags"], "visible_text": entry.get("visible_text", "")},
        "community": {"mood_compound": res.get("mood_compound", ""),
                      "trigger": entry["trigger"],
                      "anti_trigger": res.get("anti_trigger", ""),
                      "tags": entry["tags"],
                      "visible_text": entry.get("visible_text", "")},
    }
    return entry


def manual_update(entry: dict, description=None, tags=None, trigger=None,
                  category=None, mood_compound=None, anti_trigger=None,
                  visible_text=None) -> dict:
    """用户手改标签：写主字段 + 标 provenance=manual（从此不再被自动覆盖）。"""
    if description is not None:
        entry["description"] = str(description)
    if tags is not None:
        entry["tags"] = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if trigger is not None:
        entry["trigger"] = str(trigger)
    if category is not None:
        entry["category"] = str(category)
    if visible_text is not None:
        entry["visible_text"] = str(visible_text)
    entry["provenance"] = "manual"
    sv = entry.get("style_variants") or {}
    sv["appearance"] = {"description": entry.get("description", ""),
                        "tags": entry.get("tags", [])}
    sv["trigger"] = {"description": entry.get("description", ""),
                     "trigger": entry.get("trigger", ""),
                     "tags": entry.get("tags", []),
                     "visible_text": entry.get("visible_text", "")}
    com = sv.get("community") or {}
    if mood_compound is not None:
        com["mood_compound"] = str(mood_compound)
    if anti_trigger is not None:
        com["anti_trigger"] = str(anti_trigger)
    com["trigger"] = entry.get("trigger", "")
    com["tags"] = entry.get("tags", [])
    com["visible_text"] = entry.get("visible_text", "")
    sv["community"] = com
    entry["style_variants"] = sv
    entry["updated_by_user_ts"] = time.time()
    return entry


def needs_autotag(entry: dict) -> bool:
    """是否需要自动打标：没打过、且用户没手改过。"""
    if entry.get("provenance") == "manual":
        return False
    return not (entry.get("tags") or entry.get("description"))
