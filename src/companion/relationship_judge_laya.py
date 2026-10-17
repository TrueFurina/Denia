"""Laya 适配器 —— 把开源 JEV 类决策引擎 Laya 接进关系裁判的 `judge()` 接口。

设计原则（与用户约定一致）：
- **不动内部模型调用引擎**：本文件是「适配器」，只在 `judge()` 里封装 Laya 的本地推理，
  对外暴露与 `LLMJudge` / `RuleJudge` 完全相同的 `async judge(user_text, persona) -> signals`。
- **laya 懒加载**：顶层不 import laya，只有真正构造并调用时才 import，
  所以主程序 import 本模块 / relationship_judge 时不会因未装 laya 而崩。
- **人设走「问题注入」而非改引擎**：把达妮娅三段式人设塞进 harsh/valence 的 instruction，
  让 Laya 这一单次前向判断也能感知"拿她牺牲背景开玩笑=踩雷"。
- 映射：Laya 的 choice/score/noul 三原语 → 达妮娅 signals(valence/care/perfunctory/repair/harsh
  + sufficient/confidence)。

依赖：`pip install laya`（建议独立 venv，因其带 torch）。未安装时只有用到才报错（被 FallbackJudge 兜底）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _clamp01(v: Any) -> float:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return 0.0
    return 0.0 if f < 0 else (1.0 if f > 1 else f)


def _clamp11(v: Any) -> float:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return 0.0
    return -1.0 if f < -1 else (1.0 if f > 1 else f)


class LayaJudge:
    """用 Laya 本地决策引擎当关系裁判（开源 JEV 类，Apache-2.0，零 API 费）。

    - `multilingual=True`：让 Router 自动把中文路由到 laya-multilingual 检查点（100+ 语言）。
    - 首次调用会下载检查点（~650MB，建议设 `HF_ENDPOINT=https://hf-mirror.com` 加速）。
    """

    name = "laya"

    def __init__(self, *, multilingual: bool = True, device: Optional[str] = None,
                 preload: bool = False, timeout_s: float = 30.0) -> None:
        self._multilingual = bool(multilingual)
        self._device = device
        self._preload = bool(preload)
        self._timeout_s = float(timeout_s)
        self._router: Any = None
        self.calls = 0
        self.failures = 0

    def _ensure_router(self) -> Any:
        if self._router is None:
            from laya import Router  # 懒加载：未装 laya 时只有真正用到才报错
            self._router = Router(preload=self._preload, device=self._device)
        return self._router

    def available(self) -> bool:
        try:
            self._ensure_router()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[LayaJudge] 引擎不可用：%s", e)
            return False

    # ---- 把达妮娅人设压成一句话，注入问题 instruction ----
    @staticmethod
    def _persona_block(persona: Optional[dict[str, Any]]) -> str:
        if not persona:
            return ""
        bits = []
        if persona.get("name"):
            bits.append(f"角色：{persona['name']}")
        if persona.get("character"):
            bits.append(f"性格：{persona['character']}")
        if persona.get("background"):
            bits.append(f"背景：{persona['background']}")
        return "（" + "；".join(bits) + "）" if bits else ""

    def _build_questions(self, persona_block: str) -> dict[str, Any]:
        return {
            # valence：7 级评分 -> 期望分值 0..6 -> 映射到 -1..1（中心 3 为中性）
            "valence": {
                "type": "score",
                "instructions": (
                    f"评价这句话对「达妮娅↔用户」这段亲密关系的情绪价{persona_block}。"
                    "只看这句话本身，不要过度解读。"
                ),
                "criteria": [
                    "极度伤人或冒犯（骂她、拿她的牺牲/死亡背景开玩笑调侃）",
                    "明显负面、冷淡、或被冒犯",
                    "略偏负面",
                    "中性、无明显情绪",
                    "略偏正面或温暖",
                    "明显关心、喜欢或在意她",
                    "极度亲密或深情表白",
                ],
            },
            "care": {
                "type": "noul",
                "instructions": f"用户是否在关心、安慰或惦记她{persona_block}？",
            },
            "perfunctory": {
                "type": "noul",
                "instructions": "这句是否只是敷衍应付（如'嗯''哦''随便''在'），几乎没有实质内容？",
            },
            "repair": {
                "type": "noul",
                "instructions": "这是否是道歉、挽回或哄她的意思？",
            },
            "harsh": {
                "type": "noul",
                "instructions": (
                    f"这句话是否骂她、伤人、或拿她的人设/背景/伤痛开玩笑调侃{persona_block}？"
                ),
            },
        }

    @staticmethod
    def _score_value(ans: dict[str, Any]) -> float:
        """从 score 答案里取期望分值（0..n-1）。兼容直接给 .score 或从 probabilities 算。"""
        if "score" in ans and isinstance(ans["score"], (int, float)):
            return float(ans["score"])
        probs = ans.get("probabilities") or {}
        if probs:
            try:
                return sum(i * float(p) for i, p in enumerate(probs.values()))
            except Exception:  # noqa: BLE001
                return 0.0
        return 0.0

    @staticmethod
    def _confidence_of(ans: dict[str, Any]) -> float:
        if "confidence" in ans and isinstance(ans["confidence"], (int, float)):
            return _clamp01(ans["confidence"])
        # 没有显式 confidence 时，用最高概率/数值近似
        probs = ans.get("probabilities") or {}
        vals = [float(v) for v in probs.values() if isinstance(v, (int, float))]
        if vals:
            return _clamp01(max(vals))
        if "noul" in ans:
            return _clamp01(ans["noul"])
        return 0.0

    async def judge(self, user_text: str, persona: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        t = (user_text or "").strip()
        if not t:
            return {"valence": 0.0, "judge": self.name}
        self.calls += 1
        router = self._ensure_router()
        pb = self._persona_block(persona)
        questions = self._build_questions(pb)

        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(router.predict, t, questions),
                timeout=self._timeout_s,
            )
        except Exception as e:  # noqa: BLE001
            self.failures += 1
            raise  # 交给上层 FallbackJudge 回落规则裁判

        ans = (res or {}).get("answers", {})
        routing = (res or {}).get("routing", {}) or {}
        used_model = routing.get("model", "unknown")

        valence_raw = self._score_value(ans.get("valence", {}))
        n_levels = len(questions["valence"]["criteria"])
        # 0..(n-1) 居中映射到 -1..1
        mid = (n_levels - 1) / 2.0
        valence = _clamp11((valence_raw - mid) / mid) if mid else 0.0

        care = _clamp01(ans.get("care", {}).get("noul", 0.0))
        perf = _clamp01(ans.get("perfunctory", {}).get("noul", 0.0))
        repair = _clamp01(ans.get("repair", {}).get("noul", 0.0))
        harsh = _clamp01(ans.get("harsh", {}).get("noul", 0.0))

        # 置信度：各信号里的最高置信；证据不足（普遍低置信）→ 标 insufficient
        conf = max(
            self._confidence_of(ans.get("valence", {})),
            self._confidence_of(ans.get("care", {})),
            self._confidence_of(ans.get("perfunctory", {})),
            self._confidence_of(ans.get("repair", {})),
            self._confidence_of(ans.get("harsh", {})),
        )
        sufficient = bool(conf >= 0.4)

        reason = (
            f"Laya[{used_model}](valence={valence:+.2f}, care={care:.2f}, "
            f"perf={perf:.2f}, repair={repair:.2f}, harsh={harsh:.2f})"
        )
        return {
            "valence": round(valence, 3),
            "care": round(care, 2),
            "perfunctory": round(perf, 2),
            "repair": round(repair, 2),
            "harsh": round(harsh, 2),
            "sufficient": sufficient,
            "confidence": round(conf, 2),
            "reason": reason,
            "user_text": t,
            "judge": self.name,
        }
