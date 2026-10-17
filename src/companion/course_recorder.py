"""网课自动记录器（参照 pub-local-jarvis：课程需持续教学行为才自动记录）。

流程（对齐 AI Jarvis 的网课记录设计）：
  1. 场景检测：订阅 SCREEN_ACTIVITY（视觉摘要）+ FOREGROUND_CHANGED（前台标题），
     判断"持续教学行为"（强标题词 / 教学行为摘要词），经 CourseSceneStabilizer
     稳定后自动开课/下课（短暂切窗或画面遮挡不会拆课）。
  2. 记录中：
     - WASAPI 系统音频回环（soundcard）→ Sherpa-ONNX 流式转写 → transcript 分段；
     - 周期抓屏，画面显著变化时保存关键帧 + 视觉模型生成画面说明（节流）；
     - 每 N 分钟把转写滚动归并为知识点（LLM），写进 summary。
  3. 下课：时长达标的会话 finalize → README.md（总结 + 关键画面 + 画面说明），
     同时记一条活动日志（KIND_COURSE）；过短的会话直接丢弃（误触发过滤）。

依赖可选：soundcard（系统音频）/ sherpa-onnx（转写）/ 视觉路由（画面说明）任一不可用，
对应能力自动降级，课程记录不会崩。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Config
from ..event_bus import Event, EventBus, EventType
from .activity_log import KIND_COURSE
from .course_session import CourseRepository, CourseSceneStabilizer, CourseSession

logger = logging.getLogger(__name__)

# ---------- 教学行为判定词 ----------
# 强课程标题词：命中即算教学窗口（网课平台 / 学习软件 / 课程标题）
_STRONG_TITLE_KW = (
    "网课", "课堂", "课程", "慕课", "mooc", "学堂在线", "学堂",
    "雨课堂", "学习通", "智慧树", "classin", "公开课", "教学",
    "培训", "讲座", "学习",
)
# 会议类标题词：可能是网课也可能是开会，需要视觉摘要佐证"教学行为"
_MEETING_TITLE_KW = ("腾讯会议", "tencent meeting", "wemeet", "钉钉", "dingtalk",
                     "teams", "zoom", "瞩目", "voov")
# 教学行为摘要词：视觉摘要出现这些 → 画面确实在教学
_SUMMARY_TEACH_KW = (
    "讲课", "讲解", "授课", "课件", "ppt", "讲义", "板书", "老师",
    "教学", "课程", "课堂", "知识点", "例题", "重点", "复习", "作业",
    "考试", "公开课", "教程", "正在讲", "网课", "慕课", "直播课", "讲师",
    "授课中", "上课", "讲师正在",
)
# 摘要娱乐/游戏排除词：画面在打游戏/看剧 → 不算网课
_SUMMARY_EXCLUDE_KW = (
    "游戏", "对局", "击杀", "撤离", "战斗", "队友", "直播带货", "综艺",
    "电视剧", "电影", "比赛", "解说",
)


def _looks_like_course(title: str, app_hint: str, summary: str) -> bool:
    """判断当前窗口 + 画面是否构成"持续教学行为"（课程证据）。

    规则（对齐 pub："普通网页、代码、文档和娱乐视频不算网课"）：
    - 标题命中强课程词 → 是（除非画面明显在玩游戏/娱乐）；
    - 标题是会议类 → 需要摘要出现教学行为佐证（开会≠上课）；
    - 否则仅当摘要出现教学行为且无娱乐/游戏词 → 是。
    """
    t = (title or "").lower()
    s = (summary or "")
    if any(k in s for k in _SUMMARY_EXCLUDE_KW):
        return False
    if any(k in t for k in _STRONG_TITLE_KW):
        return True
    if any(k in t for k in _MEETING_TITLE_KW):
        return any(k in s for k in _SUMMARY_TEACH_KW)
    return any(k in s for k in _SUMMARY_TEACH_KW)


def _default_title(summary: str) -> str:
    """课程标题：优先摘摘要关键词，否则用时间。"""
    clean = re.sub(r"[，。！？、\s]+", " ", (summary or "").strip())
    if clean:
        return clean[:24] + "…" if len(clean) > 24 else clean
    return f"网课记录 {datetime.now().strftime('%m-%d %H:%M')}"


# ---------- WASAPI 系统音频 → Sherpa 流式转写（独立线程） ----------
class _SystemAudioTranscriber:
    """捕获系统播放音频（WASAPI loopback）并流式转写，一句话推一次 on_text。"""

    def __init__(self, on_text: Callable[[str], None], *, sample_rate: int = 16000,
                 block_frames: int = 8000) -> None:
        self._on_text = on_text
        self._sample_rate = sample_rate
        self._block_frames = block_frames
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._recognizer = None
        self._error: Optional[str] = None

    @property
    def error(self) -> Optional[str]:
        return self._error

    def start(self) -> bool:
        """启动转写线程。系统音频/转写引擎不可用返回 False（降级为纯画面记录）。"""
        try:
            import numpy as np  # noqa: F401
            import soundcard as sc  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self._error = f"soundcard 未安装: {e}"
            logger.warning("系统音频转写不可用（%s），降级为画面记录", self._error)
            return False
        try:
            from ..voice.sherpa_recognizer import SherpaRecognizer
            self._recognizer = SherpaRecognizer(num_threads=1)
        except Exception as e:  # noqa: BLE001
            self._error = f"sherpa 模型不可用: {e}"
            logger.warning("转写引擎不可用（%s），降级为画面记录", self._error)
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run, name="course-audio", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self) -> None:
        import numpy as np
        import soundcard as sc
        try:
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as e:  # noqa: BLE001
            self._error = f"获取系统音频设备失败: {e}"
            logger.warning("系统音频转写停止（%s）", self._error)
            return
        logger.info("系统音频转写启动: 设备=%s", speaker.name)
        try:
            with mic.recorder(samplerate=self._sample_rate, channels=1) as rec:
                while self._running:
                    try:
                        data = rec.record(numframes=self._block_frames)  # float32 (N,1)
                        if data is None or data.size == 0:
                            continue
                        pcm = (np.clip(data[:, 0], -1.0, 1.0) * 32767.0).astype(np.int16)
                        text = self._recognizer.feed(pcm.tobytes())
                        if text:
                            logger.debug("课程转写: %s", text)
                            self._on_text(text)
                    except Exception:  # noqa: BLE001
                        logger.exception("课程音频转写单帧异常")
        except Exception as e:  # noqa: BLE001
            self._error = f"录音失败: {e}"
            logger.warning("系统音频转写停止（%s）", self._error)


# ---------- 编排器 ----------
class CourseRecorder:
    """网课自动记录编排器：场景检测 → 开课/记录/下课 → 课程笔记。"""

    def __init__(self, bus: EventBus, cfg: Config, llm: Any, vision: Any = None,
                 store: Any = None) -> None:
        self._bus = bus
        c = getattr(cfg.ns, "course", None)
        self._enabled: bool = bool(getattr(c, "enabled", True) if c is not None else True)
        self._auto_detect: bool = bool(getattr(c, "auto_detect", True) if c is not None else True)
        root = getattr(c, "root", "data/courses") if c is not None else "data/courses"
        if not os.path.isabs(root):  # noqa: F821
            root = str(Path(root))
        self._repo = CourseRepository(Path(root) / "sessions")
        self._output_dir = Path(root)
        self._min_duration_s: float = float(getattr(c, "min_duration_s", 60) if c is not None else 60)
        self._keyframe_interval_s: float = float(
            getattr(c, "keyframe_interval_s", 30) if c is not None else 30)
        self._keyframe_min_gap_s: float = float(
            getattr(c, "keyframe_min_gap_s", 45) if c is not None else 45)
        self._keyframe_change_threshold: int = int(
            getattr(c, "keyframe_change_threshold", 12) if c is not None else 12)
        self._keyframe_max: int = int(getattr(c, "keyframe_max", 60) if c is not None else 60)
        self._keyframe_note_interval_s: float = float(
            getattr(c, "keyframe_note_interval_s", 90) if c is not None else 90)
        self._summarize_every_min: float = float(
            getattr(c, "summarize_every_min", 5) if c is not None else 5)
        self._audio_enabled: bool = bool(
            getattr(c, "audio_enabled", True) if c is not None else True)
        self._title_keywords: list[str] = list(
            (getattr(c, "title_keywords", []) or []) if c is not None else [])

        self._llm = llm  # 对话 LLM（滚动总结）；None 则跳过总结
        self._vision = vision  # VisionRouter（画面说明）；None 则关键帧无说明
        self._store = store  # ActivityStore（活动日志）；None 则不记
        self._stabilizer = CourseSceneStabilizer()

        self._session: Optional[CourseSession] = None
        self._session_started: float = 0.0
        self._transcriber: Optional[_SystemAudioTranscriber] = None
        self._text_queue: queue.Queue[str] = queue.Queue()

        # 场景证据缓存（事件驱动更新）
        self._last_summary: str = ""
        self._last_title: str = ""
        self._last_hint: str = ""

        # 关键帧节流
        self._last_grab_ts: float = 0.0
        self._last_saved_keyframe_ts: float = 0.0
        self._last_note_ts: float = 0.0
        self._last_keyframe_hash: Any = None
        self._sct = None

        # 滚动总结
        self._last_summarize_ts: float = 0.0
        self._transcripted_lines: int = 0
        self._last_summarize_lines: int = 0

        self._loop: Optional[asyncio.AbstractEventLoop] = None  # 主循环（run 时绑定）
        self._tick_interval: float = 1.0
        self._running: bool = True
        self._listener: Optional[Callable[[str, dict], None]] = None  # on_course_event(kind, data)

    # ---------- 对外接口 ----------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def active_session(self) -> Optional[CourseSession]:
        return self._session

    def set_listener(self, fn: Callable[[str, dict], None]) -> None:
        """事件回调：('started'|'finished'|'failed', {id,title,path,...})。"""
        self._listener = fn

    def stop(self) -> None:
        """停止记录循环。正在录的课时长达标则自动出笔记，否则丢弃。"""
        self._running = False
        if self._session is not None and time.time() - self._session_started >= self._min_duration_s:
            self._stop_session(finalize=True)
        else:
            self._stop_session(finalize=False)

    def snapshot(self) -> dict:
        """运行状态快照（供调试台/记忆页轮询）。"""
        session = self._session
        return {
            "enabled": self._enabled,
            "auto_detect": self._auto_detect,
            "recording": session is not None,
            "title": session.state.title if session else "",
            "started_at": session.state.created_at if session else "",
            "duration_s": round(time.time() - self._session_started, 1) if session else 0,
            "transcript_lines": self._transcripted_lines,
            "keyframes": len(session.state.keyframes) if session else 0,
            "audio_error": self._transcriber.error if self._transcriber else None,
            "last_summary": self._last_summary[:60],
            "last_title": self._last_title,
            "course_scene": self._stabilizer.current,
        }

    # ---------- 事件订阅 ----------
    def subscribe(self) -> None:
        self._bus.subscribe(EventType.SCREEN_ACTIVITY, self._on_screen)
        self._bus.subscribe(EventType.FOREGROUND_CHANGED, self._on_foreground)

    async def _on_screen(self, event: Event) -> None:
        data = event.data or {}
        summary = str(data.get("summary", "") or "")
        if summary:
            self._last_summary = summary

    async def _on_foreground(self, event: Event) -> None:
        data = event.data or {}
        self._last_title = str(data.get("title", "") or "")
        self._last_hint = str(data.get("app_hint", "") or "")

    # ---------- 主循环 ----------
    async def run(self) -> None:
        if not self._enabled or not self._auto_detect:
            logger.info("网课自动记录未启用")
            return
        self._loop = asyncio.get_running_loop()
        logger.info("网课记录循环启动: 最小时长=%ss 关键帧=%ss 总结=%smin",
                    self._min_duration_s, self._keyframe_interval_s, self._summarize_every_min)
        while self._running:
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("网课记录 tick 异常")
            await asyncio.sleep(self._tick_interval)

    def _tick(self) -> None:
        # 1) 消费转写队列（线程 → 主循环，保证 session 写操作单线程）
        drained = False
        while True:
            try:
                text = self._text_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            if self._session is not None:
                self._session.append_transcript(text)
                self._transcripted_lines += 1
        if drained:
            self._maybe_summarize(force=False)

        # 2) 场景检测（开课/下课）
        if self._session is None:
            evidence = _looks_like_course(self._last_title, self._last_hint, self._last_summary)
            if self._stabilizer.observe(evidence):
                self._start_session()
        else:
            evidence = _looks_like_course(self._last_title, self._last_hint, self._last_summary)
            if not self._stabilizer.observe(evidence):
                self._stop_session(finalize=True)
            else:
                # 3) 记录中：关键帧 + 定时总结
                self._maybe_keyframe()
                self._maybe_summarize(force=False)

    # ---------- 开课 / 下课 ----------
    def _start_session(self) -> None:
        if self._session is not None:
            return
        title = _default_title(self._last_summary or self._last_title)
        try:
            session = self._repo.create(title)
        except Exception:  # noqa: BLE001
            logger.exception("创建课程会话失败")
            return
        self._session = session
        self._session_started = time.time()
        self._stabilizer.force(True)  # 与记录状态同步，避免 tick 场景检测误判
        self._transcripted_lines = 0
        self._last_summarize_lines = 0
        self._last_summarize_ts = 0.0
        self._last_grab_ts = 0.0
        self._last_saved_keyframe_ts = 0.0
        self._last_keyframe_hash = None
        self._transcriber = None
        if self._audio_enabled:
            t = _SystemAudioTranscriber(self._text_queue.put)
            if t.start():
                self._transcriber = t
        logger.info("网课开始记录: %s (id=%s, 音频转写=%s)",
                    title, session.state.id, "开" if self._transcriber else "关")
        self._emit("started", {"id": session.state.id, "title": title})
        self._log_activity("开始网课记录：" + title)

    def _stop_session(self, *, finalize: bool) -> None:
        session = self._session
        if session is None:
            return
        if self._transcriber is not None:
            self._transcriber.stop()
            self._transcriber = None
        self._stabilizer.force(False)  # 与记录状态同步
        duration = time.time() - self._session_started
        self._session = None
        title = session.state.title

        if finalize and duration >= self._min_duration_s:
            try:
                # 最后再滚动总结一次（若有新转写）
                self._maybe_summarize(force=True, session=session)
                output = session.finalize(self._output_dir)
                logger.info("网课记录完成: %s（%.0f 分钟，%d 张关键帧）→ %s",
                            title, duration / 60, len(session.state.keyframes), output)
                self._emit("finished", {"id": session.state.id, "title": title,
                                        "path": str(output), "duration_s": round(duration)})
                self._log_activity(f"网课记录完成：{title}（{duration/60:.0f} 分钟）")
                return
            except Exception:  # noqa: BLE001
                logger.exception("课程笔记生成失败")
                self._emit("failed", {"id": session.state.id, "title": title})
                self._log_activity(f"网课记录失败：{title}")
                return
        # 时长不足（误触发）→ 丢弃
        session.discard()
        logger.info("网课会话过短（%.0fs < %ss），已丢弃: %s",
                    duration, self._min_duration_s, title)

    # ---------- 关键帧 ----------
    def _maybe_keyframe(self) -> None:
        now = time.time()
        # 抓屏节流（每 tick 最多抓一次屏）
        if now - self._last_grab_ts < self._keyframe_interval_s:
            return
        self._last_grab_ts = now
        try:
            import mss
            from PIL import Image
            import imagehash
            if self._sct is None:
                self._sct = mss.mss()
            raw = self._sct.grab(self._sct.monitors[1])
            img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
            h = imagehash.phash(img.resize((64, 64)))
            changed = (self._last_keyframe_hash is None
                       or (h - self._last_keyframe_hash) >= self._keyframe_change_threshold)
            self._last_keyframe_hash = h
            if not changed or self._session is None:
                return
            session = self._session
            if len(session.state.keyframes) >= self._keyframe_max:
                return
            # 画面变化了，但离上一张保存的关键帧太近 → 等稳定了再存（防连拍冗余）
            if session.state.keyframes and now - self._last_saved_keyframe_ts < self._keyframe_min_gap_s:
                return
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            note = self._maybe_keyframe_note(buf.getvalue())
            session.add_keyframe(
                buf.getvalue(),
                timestamp_ms=int((now - self._session_started) * 1000),
                metadata={"note": note} if note else {},
            )
            self._last_saved_keyframe_ts = now
            logger.info("保存关键帧 %d（phash 变化显著）", len(session.state.keyframes))
        except Exception:  # noqa: BLE001
            logger.debug("关键帧抓取失败", exc_info=True)

    def _maybe_keyframe_note(self, frame: bytes) -> str:
        """给关键帧生成画面说明（视觉模型，节流）。失败/超时返回空串。"""
        now = time.time()
        if self._vision is None or now - self._last_note_ts < self._keyframe_note_interval_s:
            return ""
        if self._loop is None:
            return ""
        self._last_note_ts = now
        try:
            note = asyncio.run_coroutine_threadsafe(
                self._vision.vision_describe(
                    frame, "这是网课直播画面，用一句话说明这页幻灯片/画面在讲什么知识点。"),
                self._loop,
            ).result(timeout=20)
            return (note or "").strip()[:80]
        except Exception:  # noqa: BLE001
            logger.debug("关键帧画面说明生成失败", exc_info=True)
            return ""

    # ---------- 滚动总结 ----------
    def _maybe_summarize(self, *, force: bool = False, session: Optional[CourseSession] = None) -> None:
        """滚动归纳转写为知识点摘要。

        force=False：新转写 >=5 条且距上次总结达到间隔才总结（定时滚动）；
        force=True：课程结束前调用，有新转写就总结一次（收尾）。
        """
        session = session or self._session
        if session is None or self._llm is None or self._loop is None:
            return
        now = time.time()
        new_lines = self._transcripted_lines - self._last_summarize_lines
        if new_lines < 1:
            return
        if not force and (new_lines < 5
                          or now - self._last_summarize_ts < self._summarize_every_min * 60):
            return
        self._last_summarize_ts = now
        self._last_summarize_lines = self._transcripted_lines
        try:
            transcript = self._read_transcript(session)
            prompt = (
                "你正在为一节网课做滚动笔记。以下是到目前为止的授课语音转写（带时间戳）。\n\n"
                f"转写内容：\n{transcript}\n\n"
                "要求：把转写归并、去重，提炼核心知识点，输出结构化的简明要点列表"
                "（Markdown 列表，每条一行，别复述转写原文，别编造没讲的内容）。"
            )
            new_summary = asyncio.run_coroutine_threadsafe(
                self._call_llm(prompt), self._loop).result(timeout=60)
            if new_summary:
                session.update_summary(new_summary.strip())
                logger.info("课程滚动总结已更新（%d 行转写）", self._transcripted_lines)
        except Exception:  # noqa: BLE001
            logger.exception("滚动总结失败")

    def _read_transcript(self, session: CourseSession) -> str:
        try:
            return session.transcript_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return ""

    async def _call_llm(self, prompt: str) -> str:
        try:
            return (await self._llm.chat([{"role": "user", "content": prompt}],
                                         {"temperature": 0.4, "num_predict": 800})) or ""
        except Exception:  # noqa: BLE001
            logger.exception("LLM 调用失败")
            return ""

    # ---------- 辅助 ----------
    def _emit(self, kind: str, data: dict) -> None:
        if self._listener:
            try:
                self._listener(kind, data)
            except Exception:  # noqa: BLE001
                logger.debug("课程事件回调异常", exc_info=True)

    def _log_activity(self, text: str) -> None:
        if self._store is not None:
            try:
                self._store.append(KIND_COURSE, text)
            except Exception:  # noqa: BLE001
                logger.debug("活动日志写入失败", exc_info=True)
