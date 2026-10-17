"""视频测试报告生成器（仅报告/静默模式）。

用于「提交一段游戏录屏视频，让 AI 在后台跑完整 OCR→锚点→主动回应流程，
最后产出一份 OCR 命中 ↔ AI 回应 的时间线配对报告」，便于离线回归测试，
无需真人盯着屏幕。

订阅两条事件总线消息：
- EventType.SCREEN_ACTIVITY（source="ocr"）：OCR 识别到关键时刻（击杀/阵亡/胜负等）
- EventType.USER_TEXT（is_proactive=True, source="proactive"）：AI 主动说出的话

时间轴统一用「视频内位置」(video_time_ms)，而非程序处理耗时：
- OCR 事件：game_ocr 在每帧抓取时记下该帧在视频中的时间，注入到事件里；
- AI 回应：proactive 把触发它的关键时刻的视频时间透传下来（两者本就同一时刻）。
这样即使长视频处理耗时远大于视频时长，报告里的时间戳依然精确对应「视频里第几分几秒」。
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ..event_bus import Event, EventType

logger = logging.getLogger(__name__)

# 配对容差（视频时间，毫秒）：AI 回应的视频时间本应等于触发它的 OCR 时刻，
# 但留 1s 容差防毫秒级抖动/取整。超出则视为无对应锚点的独立搭话。
_PAIR_TOLERANCE_MS = 1000.0

# 兜底容差：游戏内击杀/阵亡/倒地等互动走 duplex 响应路径，该路径不携带
# video_time_ms（只有 moment 路径才带）。这类回应本就是基于"最近屏幕状态"
# 说的，故用「最近一次 OCR 的视频时间」兜底，并放宽容差到 15s 配对到对应锚点。
_PAIR_TOLERANCE_DUPLEX_MS = 15000.0

# moment 中文名（用于报告可读性）
_MOMENT_LABEL = {
    "kill": "击杀",
    "headshot": "爆头击杀",
    "died": "阵亡",
    "downed": "倒地濒死",
    "teammate_down": "队友倒下",
    "victory": "胜利/撤离成功",
    "defeat": "失败/撤离失败",
    "round_win": "回合胜利",
    "round_lose": "回合战败",
}


class VideoTestReporter:
    """收集视频测试期间的所有 OCR 命中与 AI 主动回应，跑完后写出报告。"""

    def __init__(self, bus, ocr, video_path: str) -> None:
        self._bus = bus
        self._ocr = ocr
        self._video_path = video_path
        self._start = time.time()
        self._ocr_events: List[Dict[str, Any]] = []
        self._ai_events: List[Dict[str, Any]] = []
        self._pairs: List[Dict[str, Any]] = []
        self._unmatched_ocr: List[Dict[str, Any]] = []
        self._unmatched_ai: List[Dict[str, Any]] = []
        self._report_path: Optional[str] = None
        # 最近一次带视频时间的 OCR 时刻：用于给不带 video_time 的 duplex 回应兜底
        self._last_ocr_video_ms: float = 0.0

    def subscribe(self) -> None:
        self._bus.subscribe(EventType.SCREEN_ACTIVITY, self._on_screen)
        self._bus.subscribe(EventType.USER_TEXT, self._on_user_text)

    async def _on_screen(self, e: Event) -> None:
        d = e.data or {}
        if d.get("source") != "ocr":
            return
        moment = d.get("moment", "")
        if not moment:
            return
        vt = d.get("video_time_ms") or 0
        if vt:
            self._last_ocr_video_ms = float(vt)
        self._ocr_events.append({
            "ts": time.time(),
            "video_time_ms": float(vt),
            "moment": moment,
            "moment_label": _MOMENT_LABEL.get(moment, moment),
            "summary": d.get("summary", ""),
            "ocr_text": (d.get("ocr_text") or "")[:200],
        })

    async def _on_user_text(self, e: Event) -> None:
        d = e.data or {}
        if not d.get("is_proactive"):
            return
        if d.get("source") != "proactive":
            return
        text = (d.get("text") or "").strip()
        if not text:
            return
        vt = d.get("video_time_ms") or 0
        # 关键时刻(moment)路径带 video_time_ms；duplex 路径(击杀/阵亡/倒地等)
        # 不带 → 用「最近一次 OCR 的视频时间」兜底，标记为 fallback 以便配对时放宽容差。
        if vt:
            video_time = float(vt)
            fallback = False
        else:
            video_time = self._last_ocr_video_ms
            fallback = True
        self._ai_events.append({
            "ts": time.time(),
            "video_time_ms": video_time,
            "_fallback": fallback,
            "text": text,
            "trigger": d.get("trigger", ""),
            "emotion": d.get("emotion", ""),
        })

    def _pair(self) -> None:
        """把每条 AI 回应，配对到时间最接近的 OCR 命中。

        - moment 路径带真实 video_time（容差 1s）；
        - duplex 路径用兜底视频时间（标记 _fallback，容差放宽到 15s）。
        """
        used: set = set()
        for ai in self._ai_events:
            avt = ai.get("video_time_ms")
            if not avt:
                continue
            tol = _PAIR_TOLERANCE_DUPLEX_MS if ai.get("_fallback") else _PAIR_TOLERANCE_MS
            best_idx = None
            best_diff = 1e9
            for i, oc in enumerate(self._ocr_events):
                if i in used:
                    continue
                diff = abs(oc["video_time_ms"] - avt)
                if diff <= tol and diff < best_diff:
                    best_diff = diff
                    best_idx = i
            if best_idx is not None:
                used.add(best_idx)
                self._pairs.append({"ocr": self._ocr_events[best_idx], "ai": ai})

        paired_ocr_ts = {id(p["ocr"]) for p in self._pairs}
        paired_ai_id = {id(p["ai"]) for p in self._pairs}
        self._unmatched_ocr = [oc for oc in self._ocr_events
                               if id(oc) not in paired_ocr_ts]
        self._unmatched_ai = [ai for ai in self._ai_events
                              if id(ai) not in paired_ai_id]

    @staticmethod
    def _fmt_time(ms: Optional[float]) -> str:
        if ms is None:
            return "—"
        s = ms / 1000.0
        if s < 60:
            return f"{s:.1f}s"
        m = int(s // 60)
        return f"{m}m{s - m * 60:.1f}s"

    def _render(self) -> str:
        wall = time.time() - self._start
        video_dur = getattr(self._ocr, "_video_total_ms", 0) or 0
        fname = os.path.basename(self._video_path)
        lines: List[str] = []
        lines.append("# 视频测试报告（OCR 命中 ↔ AI 回应 配对）\n")
        lines.append(f"- **视频文件**：`{fname}`")
        if video_dur:
            lines.append(f"- **视频时长**：{self._fmt_time(video_dur)}")
        lines.append(f"- **程序运行时长**：{wall:.1f}s")
        lines.append(f"- **OCR 命中总数**：{len(self._ocr_events)}")
        lines.append(f"- **AI 主动回应总数**：{len(self._ai_events)}")
        lines.append(f"- **成功配对**：{len(self._pairs)}")
        lines.append(f"- **未配对 OCR（识别到但 AI 没接话）**：{len(self._unmatched_ocr)}")
        lines.append(f"- **未配对 AI（无对应 OCR 锚点）**：{len(self._unmatched_ai)}")
        lines.append("")
        lines.append("> 时间轴为「视频内位置」（非程序处理耗时），可直接对照录屏时间。\n")

        lines.append("## 一、配对时间线（OCR 触发 → AI 回应）\n")
        if self._pairs:
            lines.append("| # | 视频时间 | OCR 关键时刻 | AI 回应 | 触发/情绪 |")
            lines.append("|---|---|---|---|---|")
            for i, p in enumerate(self._pairs, 1):
                t = self._fmt_time(p["ai"].get("video_time_ms"))
                oc = p["ocr"]
                ai = p["ai"]
                emotion = ai.get("emotion") or "-"
                trigger = ai.get("trigger") or "-"
                ocr_cell = f"{oc['moment_label']}<br><small>{_esc(oc['summary'])}</small>"
                lines.append(
                    f"| {i} | {t} | {ocr_cell} | {_esc(ai['text'])} | {trigger}/{emotion} |")
        else:
            lines.append("_（无配对项）_")
        lines.append("")

        lines.append("## 二、未配对 OCR（识别到了但 AI 没接话 —— 潜在漏触发）\n")
        if self._unmatched_ocr:
            lines.append("| 视频时间 | 关键时刻 | OCR 原文(节选) |")
            lines.append("|---|---|---|")
            for oc in self._unmatched_ocr:
                t = self._fmt_time(oc["video_time_ms"])
                lines.append(
                    f"| {t} | {oc['moment_label']} | <small>{_esc(oc['ocr_text'])}</small> |")
        else:
            lines.append("_（无）_")
        lines.append("")

        lines.append("## 三、未配对 AI 回应（无对应 OCR 锚点 —— 多为定时/上下文触发）\n")
        if self._unmatched_ai:
            lines.append("| 视频时间 | AI 回应 | 触发/情绪 |")
            lines.append("|---|---|---|")
            for ai in self._unmatched_ai:
                t = self._fmt_time(ai.get("video_time_ms"))
                trigger = ai.get("trigger") or "-"
                emotion = ai.get("emotion") or "-"
                lines.append(f"| {t} | {_esc(ai['text'])} | {trigger}/{emotion} |")
        else:
            lines.append("_（无）_")
        lines.append("")

        lines.append("---")
        lines.append("_本报告由视频测试模式（仅报告/静默）自动生成，不播放任何 TTS 音频。_")
        return "\n".join(lines)

    def finalize(self) -> str:
        """配对并写出报告，返回报告文件路径。"""
        self._pair()
        md = self._render()
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_md = os.path.join(root, f"video_test_report_{stamp}.md")
        out_json = os.path.join(root, f"video_test_report_{stamp}.json")
        try:
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(md)
        except PermissionError:
            # 固定/同名文件被预览面板或编辑器锁定，降级到时间戳文件名
            logger.warning("视频测试报告固定名被占用，改用时间戳文件名: %s", out_md)
            out_md = os.path.join(root, f"video_test_report_{int(time.time())}.md")
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(md)
        payload = {
            "video_path": self._video_path,
            "video_duration_ms": getattr(self._ocr, "_video_total_ms", 0) or 0,
            "process_duration_s": round(time.time() - self._start, 1),
            "ocr_events": self._ocr_events,
            "ai_events": self._ai_events,
            "pairs": self._pairs,
            "unmatched_ocr": self._unmatched_ocr,
            "unmatched_ai": self._unmatched_ai,
        }
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001  JSON 失败不影响主报告
            logger.warning("视频测试 JSON 写出失败", exc_info=True)
        self._report_path = out_md
        logger.info("视频测试报告已写出: %s （配对 %d/%d，漏触发 %d）",
                    out_md, len(self._pairs), len(self._ai_events), len(self._unmatched_ocr))
        return out_md


def _esc(s: str) -> str:
    """转义 markdown 表格里会破坏排版的符号。"""
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()
