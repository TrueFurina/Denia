"""LLM / VLM 抽象层。

设计要点（重要）：
- 当前测试实现 = ZhipuBackend（免费智谱端口），覆盖三个能力：
  1) auto_describe   上传图片自动生成描述+标签
  2) semantic_search 按文字描述语义检索表情
  3) pick_sticker    聊天时按角色+上下文真人感选贴
- 接入达妮娅时，只需新增 DaniyaLLMBackend，把三个方法接到
  「用户设置的主 LLM/VLM」即可（见文件底部桩），调用面不变量。

无 API key 时所有方法优雅降级：auto_describe 返回空，semantic_search 退化为
本地关键词匹配（在 main.py 里处理），pick_sticker 返回不使用表情。
"""
import base64
import json
import re
import io
import sys
import httpx
from . import config

try:
    from PIL import Image
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# 智谱 GLM-4V-Flash 免费视觉模型对分辨率敏感，原图过大会 400「图片输入格式/解析错误」。
# 发送前把最长边缩到该阈值以内（实测 512 稳定通过）。
_VISION_MAX_SIDE = 512

# 选贴时只看最近 N 条消息。历史越长，模型越容易锚定早期情绪（实测连发同一张悲伤图）。
# 取 6 ≈ 最近 3 轮，足以覆盖"上一句-这一句"的语境。
_PICK_RECENT_MESSAGES = 6


def _prepare_image(image_bytes, mime, frames_strategy=None, frames_k=3):
    """把图片转成视觉模型能吃的形式。

    **关键（2026-10-13 修）**：本库 24 张全是 GIF，19/24 首末帧差异显著。
    只取第一帧 = 只看了表情包的一瞬间（实测某张首帧"面无表情"、第37帧才"笑着冒爱心"），
    这是此前打标系统性出错的重要根因。故默认按 motion 策略抽多帧并横向拼接。

    frames_strategy:
        None / "first"  -> 只取首帧（旧行为，保留兼容）
        "mid"/"last"/"uniform"/"motion" -> 走 backend.frames 抽帧拼接
    """
    if not _PIL_OK:
        return image_bytes, mime
    try:
        if frames_strategy and frames_strategy != "first":
            try:
                from . import frames as _F
                fr = _F.extract_frames(image_bytes, frames_strategy, frames_k)
                sheet = _F.compose(fr)
                if sheet is not None:
                    return _F.to_jpeg_bytes(sheet), "image/jpeg"
            except Exception:  # noqa: BLE001  抽帧失败就退回首帧，绝不阻断
                pass
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if max(im.size) > _VISION_MAX_SIDE:
            im.thumbnail((_VISION_MAX_SIDE, _VISION_MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, mime


def _quote_bare_json(raw: str) -> str:
    """把「未加引号的 JSON」修成合法 JSON。

    模型偶尔输出 `{use:true,meme_id:5f13fc38580b,reason:开心表情}` 这种形态
    （键无引号、值是裸词/中文）。标准 json.loads 会失败 → 静默返回 {} →
    表现为「永远不发表情」。此处按 token 逐字符扫描加引号：
      · 保留 true/false/null/数字 的字面量语义（不加引号）
      · 其余裸值（含中文、ID）一律加引号
    """
    out = []
    LITERALS = ("true", "false", "null")
    # 预处理：模型偶尔写出 `{"use:true,...}` 这种"键名前多一个引号、键名反而没闭合"
    # 的畸形形态。此时引号后面紧跟 `单词:` 而不是合法的 `"key":`，把这对野引号去掉。
    raw = re.sub(r'"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(?!")', r'\1:', raw)
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch in "{[,:":
            out.append(ch)
            i += 1
            continue
        if ch in "}]":
            out.append(ch)
            i += 1
            continue
        if ch == '"':                      # 已有字符串，原样搬过去
            j = i + 1
            while j < n and raw[j] != '"':
                j += 2 if raw[j] == "\\" else 1
            out.append(raw[i:j + 1])
            i = j + 1
            continue
        if ch.isspace():
            out.append(ch)
            i += 1
            continue
        # 裸 token：读到分隔符为止。
        # 键名遇到 ':' 即止；值则要一直读到 , } ] 为止（允许值里含空格，如 reason: 带 空格 的中文）。
        is_key = False
        j = i
        while j < n:
            c = raw[j]
            if c in ",}]":
                break
            if c == ":":
                is_key = True
                break
            j += 1
        tok = raw[i:j].strip()
        low = tok.lower()
        if not is_key and (low in LITERALS or re.fullmatch(r"-?\d+(\.\d+)?", tok)):
            out.append(low)               # 布尔 / null / 数字：保持字面量
        else:
            out.append('"' + tok + '"')   # 键名、ID、中文值：加引号
        i = j
    return "".join(out)


def _extract_json(text):
    text = (text or "").strip()
    # 先去掉 ```json ... ``` 围栏，避免围栏内的反引号/正文干扰
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 只认「以 { 或 [ 开头」的整段，避免从自由文本里误抓候选自带的 ['标签'] 列表。
    # （实测：模型偶尔把候选描述原样吐回来，宽松正则会把描述里的标签数组当成结果。）
    raw = text
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        raw = m.group(0)
        try:
            return json.loads(raw)
        except Exception:
            pass
    # 兜底：模型偶尔输出**未加引号的 JSON**（键无引号、值是裸词或中文），
    # 标准 json.loads 直接失败 → 静默返回 {} → 表现为"永远不发表情"。
    # 实测在候选表情较多时高频触发；这里逐 token 修复再解析。
    for candidate in (_quote_bare_json(raw), _quote_bare_json(raw).replace("'", '"')):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return {}


class LLMBackend:
    """所有引擎实现的统一接口。"""

    def available(self) -> bool:
        raise NotImplementedError

    async def auto_describe(self, image_bytes: bytes, mime: str) -> dict:
        raise NotImplementedError

    async def semantic_search(self, query: str, candidates: list) -> list:
        raise NotImplementedError

    async def pick_sticker(self, character: str, conversation: list, candidates: list) -> dict:
        raise NotImplementedError


class ZhipuBackend(LLMBackend):
    """测试期实现：智谱开放平台免费端口（open.bigmodel.cn）。"""

    # 达妮娅主软件目录（读好感度/关系状态用）。留空则退化为"没有心情"。
    DANIYA_SRC = r"<项目根目录>"
    # 关系状态里用来定位这个角色的 id/名字
    relation_char_id = "danya"

    def _relation_block(self, character: str) -> str:
        """取达妮娅「好感度/关系状态机」的关系块（自然语言、零数字）。

        直接复用主软件的 relationship_v2.relation_block_text_v2，保证**同源**——
        表情选贴看到的"心情"和聊天时她语气用的那份完全一致，不会两套说法。
        """
        try:
            if self.DANIYA_SRC and self.DANIYA_SRC not in sys.path:
                sys.path.insert(0, self.DANIYA_SRC)
            from src.companion.relationship import default_state
            from src.companion.relationship_v2 import relation_block_text_v2
        except Exception:  # noqa: BLE001  主软件不在时优雅退化
            return ""
        try:
            st = default_state(self.relation_char_id)
            if getattr(self, "mood_override", None):
                st["mood"] = {"label": self.mood_override, "intensity": 2}
            if getattr(self, "archetype_override", None):
                st["archetype"] = self.archetype_override
            return relation_block_text_v2(st, tier="cloud")
        except Exception:  # noqa: BLE001
            return ""

    def __init__(self):
        self.key = config.ZHIPU_API_KEY
        self.base = config.ZHIPU_BASE_URL.rstrip("/")
        self.text_model = config.ZHIPU_TEXT_MODEL
        self.vision_model = config.ZHIPU_VISION_MODEL

    def available(self):
        return bool(self.key)

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def _chat(self, model, messages, timeout=60):
        payload = {"model": model, "messages": messages, "temperature": 0.3}
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{self.base}/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def auto_describe(self, image_bytes, mime):
        image_bytes, mime = _prepare_image(image_bytes, mime)
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"
        sys_p = (
            "你是表情包标注助手。看这张图，用中文输出 JSON："
            '{"description":"一句话描述这张表情表达了什么情绪/场景","tags":["标签1","标签2"]}。只输出 JSON。'
        )
        user = [
            {"type": "text", "text": "请标注这张表情包"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        try:
            out = self._chat(self.vision_model, [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user},
            ])
            res = _extract_json(out)
            return {
                "description": res.get("description", ""),
                "tags": res.get("tags", []) if isinstance(res.get("tags"), list) else [],
            }
        except Exception as e:  # 降级
            return {"description": "", "tags": [], "error": str(e)}

    async def semantic_search(self, query, candidates):
        if not candidates:
            return []
        # 候选行：把「适用场景(trigger)」放在最前面 —— 它是"什么时候该用"的直接依据，
        # 比画面描述更有决策价值。此前漏掉 trigger，导致库里明明有
        # 「被推开、被说'不用你管'…」的图，模型却答"没有贴切的"。
        def _cand_line(c):
            bits = [c["id"]]
            if c.get("trigger"):
                bits.append(f"适用于: {c['trigger']}")
            if c.get("description"):
                bits.append(f"画面: {c['description']}")
            if c.get("tags"):
                bits.append(f"情绪: {'、'.join(c['tags'])}")
            if c.get("mood_compound"):
                bits.append(f"语气: {c['mood_compound']}")
            if c.get("anti_trigger"):
                bits.append(f"不该用于: {c['anti_trigger']}")
            return " | ".join(bits)
        cand_text = "\n".join(_cand_line(c) for c in candidates)
        sys_p = (
            "你是表情检索助手。下面是一些表情包及其描述，用户用一句话描述想要找的表情。"
            '请选出最匹配的，最多5个，输出 JSON 数组：["id1","id2"]。只输出 JSON 数组。'
        )
        user = f"用户查询：{query}\n\n候选表情：\n{cand_text}"
        try:
            out = self._chat(self.text_model, [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user},
            ])
            ids = _extract_json(out)
            if isinstance(ids, list):
                valid = {c["id"] for c in candidates}
                return [i for i in ids if i in valid][:5]
        except Exception:
            pass
        return []

    async def pick_sticker(self, character, conversation, candidates):
        if not candidates:
            return {"use": False, "meme_id": None, "reason": "no candidates"}
        # 只看最近几轮：历史累积会让模型锚定更早的旧情绪（实测第 4~6 轮仍在引用
        # 第 2 轮的「心情不好」），因此按轮窗口截断，并显式标记最新一条用户消息。
        recent = list(conversation)[-_PICK_RECENT_MESSAGES:]
        conv_text = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in recent)
        last_user = next(
            (m.get("content", "") for m in reversed(recent) if m.get("role") == "user"), ""
        )
        # 候选行：把「适用场景(trigger)」放在最前面 —— 它是"什么时候该用"的直接依据，
        # 比画面描述更有决策价值。此前漏掉 trigger，导致库里明明有
        # 「被推开、被说'不用你管'…」的图，模型却答"没有贴切的"。
        def _cand_line(c):
            bits = [c["id"]]
            if c.get("trigger"):
                bits.append(f"适用于: {c['trigger']}")
            if c.get("description"):
                bits.append(f"画面: {c['description']}")
            if c.get("tags"):
                bits.append(f"情绪: {'、'.join(c['tags'])}")
            if c.get("mood_compound"):
                bits.append(f"语气: {c['mood_compound']}")
            if c.get("anti_trigger"):
                bits.append(f"不该用于: {c['anti_trigger']}")
            return " | ".join(bits)
        cand_text = "\n".join(_cand_line(c) for c in candidates)
        # 她此刻的心情（来自达妮娅的好感度/关系状态机）。实测这是选贴质量的最大提升项：
        # 带上心情后，「用户难过时」会挑到"拿着心形信封"那张安慰感更强的图，而不是
        # 随便一张含泪图（对比实验：基线 5.5 → 带心情 6.0，满分 6）。
        # 关系块是**自然语言、零数字**的，符合"好感度数值绝不喂给模型"这条红线。
        mood_block = ""
        try:
            mood_block = self._relation_block(character)
        except Exception:  # noqa: BLE001  绝不因为拿不到心情就阻断选贴
            mood_block = ""
        mood_part = f"\n下面是你的当前状态：\n{mood_block}\n" if mood_block else ""
        # 分类语义表（astrbot 协议：包 manifest 的 categories，每个分类带"什么时候用"）。
        # 比单张图的自由标签稳定得多，是选贴最有价值的语义线索。
        cat_part = ""
        cats = getattr(self, "category_hints", None) or {}
        if cats:
            rows = "\n".join(f"- {k}：{v}" for k, v in list(cats.items())[:24])
            cat_part = ("\n【本表情库的分类及适用场景】（优先按分类判断该不该发）\n"
                        f"{rows}\n")
        sys_p = (
            f"你是角色「{character}」的拟人化反应决策器。"
            "根据对话上下文，决定是否用一张表情包回应，并选最贴切的一张。\n"
            "【判定铁律】\n"
            "1. 情绪基调以【最新一条用户消息】为准，历史消息仅供理解语境；"
            "若用户当前情绪已好转（如大笑、道谢、告别），不得再选悲伤/负面表情。\n"
            "2. 表情包要匹配【用户此刻的情绪】或【你要表达的态度】，两者取其一，不要混。\n"
            "3. 若无贴切表情，就 use=false，宁可不发也不要发错。\n"
            "【输出格式（必须严格遵守）】\n"
            "只输出一个 JSON 对象，不要输出候选描述、不要解释、不要 Markdown 代码块。\n"
            '正确示例：{"use": true, "meme_id": "abc123", "reason": "用户在笑，发开心的"}\n'
            '不发示例：{"use": false, "meme_id": null, "reason": "没有贴切的"}'
            f"{cat_part}{mood_part}"
        )
        user = (
            f"对话上下文（最近 {len(recent)} 条）：\n{conv_text}\n\n"
            f"【最新一条用户消息】{last_user}\n\n"
            f"可选表情（每行格式：id | 描述 | 标签）：\n{cand_text}\n\n"
            "请只输出 JSON："
        )
        try:
            out = self._chat(self.text_model, [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user},
            ])
            res = _extract_json(out)
            if isinstance(res, dict):
                return res
        except Exception as e:
            return {"use": False, "meme_id": None, "reason": str(e)}
        return {"use": False, "meme_id": None, "reason": "parse failed"}


class DaniyaLLMBackend(ZhipuBackend):
    """接入达妮娅时的实现。

    继承 ZhipuBackend，因此 `pick_sticker` 的提示词/心情接入/JSON 容错逻辑**全部复用**，
    接入方只需把底层引擎换成达妮娅「用户设置的主 LLM/VLM」：

    - `_chat` 改调达妮娅主 LLM（文本）
    - `auto_describe` 改调主 VLM（视觉）
    - 心情块已通过 `_relation_block()` 直连 `relationship_v2`，与聊天语气同源。

    对比实验结论（见 mood_matrix_report.json）：带上心情的选贴 6.0/6 满分，
    不带（基线）5.5/6 —— 因此接入时**务必保留** `_relation_block` 这一步。
    """

    def __init__(self, engine=None):
        super().__init__()
        self.engine = engine  # 达妮娅引擎句柄，接入时注入

    def available(self):
        return self.engine is not None

    def _call_engine(self, messages, want_vision=False):
        """把统一调用面转成达妮娅引擎调用。接入时在此接主 LLM/VLM。"""
        raise NotImplementedError("接入达妮娅时实现：调用主 LLM/VLM")

    def _chat(self, model, messages, timeout=60):
        return self._call_engine(messages)

    async def auto_describe(self, image_bytes, mime):
        image_bytes, mime = _prepare_image(image_bytes, mime)
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"
        sys_p = (
            "你是表情包标注助手。看这张图，用中文输出 JSON："
            '{"description":"一句话描述这张表情表达了什么情绪/场景","tags":["标签1","标签2"]}。只输出 JSON。'
        )
        out = self._call_engine([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": [
                {"type": "text", "text": "请标注这张表情包"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], want_vision=True)
        res = _extract_json(out)
        return {
            "description": res.get("description", ""),
            "tags": res.get("tags", []) if isinstance(res.get("tags"), list) else [],
        }
