"""网课记录会话：状态机 + 关键帧 + Markdown 渲染（参照 pub-local-jarvis courses/core.py）。

对齐开源项目 AI Jarvis 的课程记录设计：
- CourseSession：RECORDING → FINALIZING → COMPLETE / FAILED 的持久化状态机，
  state.json（元数据）+ transcript.md（转写滚动日志）+ frames/（关键帧）都在会话目录内，
  崩溃后可从磁盘状态恢复（recoverable）。
- MarkdownRenderer：最终产物 = 标题 + 课程总结（滚动归纳的知识点）+ 关键画面 + 画面说明。
- CourseSceneStabilizer：课程场景稳定器——进入课程需连续 N 次教学证据、
  退出需连续 N 次非课程证据（"短暂切换窗口或画面遮挡不会立即结束课程"）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    """本地时间 ISO 字符串（无时区后缀）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写字节（临时文件 + os.replace），崩溃安全。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            # 临时文件已被消费/不存在，无需再删
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


class CourseStatus(Enum):
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class CourseState:
    """课程会话元数据（落盘 state.json）。"""
    id: str
    title: str
    status: CourseStatus
    created_at: str
    updated_at: str
    summary: str = ""
    keyframes: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    output_path: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CourseState":
        copied = dict(value)
        copied["status"] = CourseStatus(copied["status"])
        return cls(**copied)

    def as_dict(self) -> dict[str, Any]:
        value = dict(self.__dict__)
        value["status"] = self.status.value
        return value


# 滚动总结器：输入（当前累计 summary，新增转写段）→ 输出新的 summary
RollingSummarizer = Any  # Callable[[str, str], str]


class CourseSession:
    """持久化的课程记录会话（可恢复的最终化流程）。

    目录结构：
      <session_id>/state.json      会话状态（幂等可恢复）
      <session_id>/transcript.md   转写滚动日志（append-only）
      <session_id>/frames/         关键帧原图（原子写入，不转码）
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.state_path = self.path / "state.json"
        self.transcript_path = self.path / "transcript.md"
        self.frames_path = self.path / "frames"

    # ---------- 创建 / 状态 ----------
    @classmethod
    def create(cls, root: str | Path, title: str, *, session_id: Optional[str] = None) -> "CourseSession":
        if not (title or "").strip():
            raise ValueError("title must be non-empty")
        session_id = session_id or uuid4().hex
        session = cls(Path(root) / session_id)
        session.frames_path.mkdir(parents=True, exist_ok=False)
        now = _iso_now()
        session._save(CourseState(session_id, title, CourseStatus.RECORDING, now, now))
        atomic_write_text(session.transcript_path, "")
        return session

    @property
    def state(self) -> CourseState:
        return CourseState.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def _save(self, state: CourseState) -> None:
        state.updated_at = _iso_now()
        atomic_json(self.state_path, state.as_dict())

    # ---------- 记录 ----------
    def append_transcript(self, text: str, *, summarizer: Optional[RollingSummarizer] = None) -> CourseState:
        """追加一段转写文本（带时间戳），可顺带滚动归纳 summary。"""
        state = self.state
        if state.status != CourseStatus.RECORDING:
            raise RuntimeError("session is not recording")
        text = (text or "").strip()
        if text:
            with self.transcript_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
                stream.flush()
                os.fsync(stream.fileno())
        if summarizer is not None and text:
            try:
                state.summary = (summarizer(state.summary, text) or state.summary or "").strip()
            except Exception:  # noqa: BLE001
                logger.exception("滚动总结失败，保留原 summary")
        self._save(state)
        return state

    def update_summary(self, summary: str) -> CourseState:
        state = self.state
        if state.status != CourseStatus.RECORDING:
            raise RuntimeError("session is not recording")
        state.summary = (summary or "").strip()
        self._save(state)
        return state

    def add_keyframe(
        self,
        frame: bytes,
        *,
        timestamp_ms: int,
        extension: str = ".png",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """保存一帧关键帧，返回 {filename, timestamp_ms, metadata}；无有效帧返回 None。"""
        state = self.state
        if state.status != CourseStatus.RECORDING:
            raise RuntimeError("session is not recording")
        if timestamp_ms < 0 or not frame:
            raise ValueError("timestamp must be non-negative and frame non-empty")
        extension = "." + extension.lstrip(".").casefold()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
            raise ValueError("invalid frame extension")
        number = len(state.keyframes) + 1
        filename = f"{number:06d}-{timestamp_ms:012d}{extension}"
        atomic_write_bytes(self.frames_path / filename, frame)  # 原始字节，不转码
        item = {
            "filename": filename,
            "timestamp_ms": timestamp_ms,
            "metadata": dict(metadata or {}),
        }
        state.keyframes.append(item)
        self._save(state)
        return item

    # ---------- 最终化 ----------
    def finalize(self, output_dir: str | Path, *, renderer: Optional["MarkdownRenderer"] = None) -> Path:
        """结束课程 → 渲染 README.md（课程总结 + 关键画面）到 output_dir/<id>/README.md。

        幂等：已 COMPLETE 且已有输出路径 → 直接返回。
        """
        state = self.state
        if state.status == CourseStatus.COMPLETE and state.output_path:
            return Path(state.output_path)
        if state.status not in {
            CourseStatus.RECORDING,
            CourseStatus.FINALIZING,
            CourseStatus.FAILED,
        }:
            raise RuntimeError(f"cannot finalize session in {state.status.value}")
        state.status, state.error = CourseStatus.FINALIZING, None
        self._save(state)
        output = Path(output_dir) / state.id / "README.md"
        try:
            (renderer or MarkdownRenderer()).render(self, output)
            state.status, state.output_path = CourseStatus.COMPLETE, str(output.resolve())
            self._save(state)
            logger.info("课程笔记已生成: %s", output)
            return output
        except BaseException as exc:  # noqa: BLE001
            state.status, state.error = CourseStatus.FAILED, f"{type(exc).__name__}: {exc}"
            self._save(state)
            raise

    def discard(self) -> None:
        """删除整个会话目录（放弃记录）。"""
        shutil.rmtree(self.path, ignore_errors=True)


class MarkdownRenderer:
    """课程会话 → Markdown 课程笔记。"""

    def render(self, session: CourseSession, destination: str | Path) -> Path:
        state = session.state
        destination = Path(destination)
        lines = [
            f"# {state.title}",
            "",
            f"- 课程 ID：`{state.id}`",
            f"- 记录时间：{state.created_at}",
            "",
        ]
        if state.summary:
            lines += ["## 课程总结", "", state.summary, ""]
        asset_dir = destination.parent / "images"
        if state.keyframes:
            lines += ["## 关键画面", ""]
            for frame in state.keyframes:
                source = session.frames_path / frame["filename"]
                target = asset_dir / frame["filename"]
                atomic_write_bytes(target, source.read_bytes())
                seconds = frame["timestamp_ms"] / 1000
                m, s = divmod(int(seconds), 60)
                stamp = f"{m:02d}:{s:02d}"
                lines += [
                    f"### {stamp}",
                    "",
                    f"![关键画面 {stamp}]({asset_dir.name}/{target.name})",
                    "",
                ]
                note = str(frame["metadata"].get("note", "") or "").strip()
                if note:
                    lines += [f"**画面说明：** {note}", ""]
        else:
            lines += ["_（本次课程没有保存关键画面）_", ""]
        atomic_write_text(destination, "\n".join(lines).rstrip() + "\n")
        return destination


class CourseRepository:
    """课程会话仓库：创建 / 打开 / 列出 / 恢复。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, title: str, *, session_id: Optional[str] = None) -> CourseSession:
        return CourseSession.create(self.root, title, session_id=session_id)

    def open(self, session_id: str) -> CourseSession:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("invalid session id")
        session = CourseSession(self.root / session_id)
        if not session.state_path.is_file():
            raise FileNotFoundError(session_id)
        return session

    def sessions(self) -> list[CourseSession]:
        return [
            CourseSession(path)
            for path in sorted(self.root.iterdir())
            if (path / "state.json").is_file()
        ]

    def recoverable(self) -> list[CourseSession]:
        """处于 FINALIZING / FAILED 的会话（可重新 finalize）。"""
        return [
            session
            for session in self.sessions()
            if session.state.status in {CourseStatus.FINALIZING, CourseStatus.FAILED}
        ]


# ---------- 课程场景稳定器（对齐 pub orchestrator/scene.py） ----------
class CourseSceneStabilizer:
    """课程场景稳定器：进入/退出课程都需要连续多次一致证据。

    防止"短暂切换窗口或画面遮挡"把课程错误拆分：
    - 进入课程：连续 enter_samples 次"教学证据"才确认开课；
    - 退出课程：连续 exit_samples 次"非课程证据"才确认下课。
    """

    def __init__(self, enter_samples: int = 2, exit_samples: int = 2) -> None:
        if enter_samples < 1 or exit_samples < 1:
            raise ValueError("sample counts must be positive")
        self.enter_samples = enter_samples
        self.exit_samples = exit_samples
        self.current = False  # 是否处于课程中
        self._candidate: Optional[bool] = None
        self._streak = 0

    def force(self, in_course: bool) -> None:
        self.current = in_course
        self._candidate = None
        self._streak = 0

    def observe(self, is_course: bool) -> bool:
        """喂一次场景判断（当前画面是否"持续教学行为"），返回稳定后的课程状态。"""
        if is_course == self.current:
            self.force(is_course)
            return self.current
        self._streak = self._streak + 1 if self._candidate == is_course else 1
        self._candidate = is_course
        needed = self.enter_samples if not self.current else self.exit_samples
        if self._streak >= needed:
            self.force(is_course)
        return self.current
