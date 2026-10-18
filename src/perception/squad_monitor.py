"""《三角洲行动》队伍 HUD 识别：观察左下角队伍面板，感知队友存活状态。

设计：
- 只在《三角洲行动》前台时工作（用 game_knowledge 判定当前游戏）
- 截图裁剪左下角 HUD 区域（左 0~35%、下 45%~100%）交给视觉模型识别
- 识别出"队伍几人、几人存活"，与上次状态对比：
  - 存活数减少 → 发布 SCREEN_ACTIVITY {moment: "teammate_down"}，
    proactive 收到后提醒用户（"你队友倒了一个"）
  - 状态不变 → 静默（不刷屏，省 token）
- 画面未变化（phash 去重）→ 跳过分析
- 容错：视觉失败 / 看不清 → 静默跳过，绝不影响主程序
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import time
from typing import Any, Optional

from PIL import Image

from ..config import Config
from ..event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)

# 队伍 HUD 预期人数模板：{expected} 由当前模式锁存判定替换（全面战场=4人，
# 烽火地带/红鼠窝=3人）。用户原话："hud应该是看左下角啊，左下角全面战场4个，
# 烽火地带3个"——左下角队伍面板的槽位数是模式决定的，告诉模型预期人数，
# 它就不会把主页 UI 数字/其他文字误读成"队伍5人"。
SQUAD_PROMPT = (
    "这是《三角洲行动》游戏画面的队伍HUD区域截图（已裁剪画面左下角队伍面板，"
    "可能来自电脑版窗口，也可能是手机屏幕共享的竖屏画面）。\n"
    "队伍面板在画面左下角，当前模式预期队伍 {expected} 人：\n"
    "1. 队伍总人数（应与预期 {expected} 人一致；若画面明显不是 {expected} 人"
    "就以画面实际为准）\n"
    "2. 每名队友的存活状态：头像旁有彩色图标=存活；头像变灰或旁边有❌/死亡标记=已阵亡\n"
    "只输出一行中文，例如：'队伍4人，3人存活，1人阵亡' 或 '队伍3人，全部存活'。\n"
    "看不清就只报存活人数；完全看不清就输出'看不清'。不要猜测，不要输出其他内容。"
)

# 手机屏幕共享窗口标题特征：腾讯会议共享手机画面时，手机版 HUD 在画面右上角
PHONE_STREAM_TITLES = ("腾讯会议", "tencent meeting", "wemeet", "voov meeting")


class SquadMonitor:
    def __init__(self, bus: EventBus, cfg: Config, monitor: Any = None,
                 llm: Optional[Any] = None, game_knowledge: Any = None,
                 session: Any = None) -> None:
        self._bus = bus
        self._monitor = monitor
        self._llm = llm
        self._gk = game_knowledge
        self._session = session  # DeltaGameSession（可选）：用已识别的游戏模式辅助判断"在打三角洲"
        sq_cfg = getattr(cfg.ns.perception, "squad", None)
        self._enabled: bool = bool(getattr(sq_cfg, "enabled", True) if sq_cfg else True)
        self._interval: float = float(getattr(sq_cfg, "interval_s", 8) if sq_cfg else 8)
        self._jitter: float = float(getattr(sq_cfg, "interval_jitter", 0.2) if sq_cfg else 0.2)
        self._monitor_index: int = int(getattr(sq_cfg, "monitor_index", 1) if sq_cfg else 1)
        self._max_width: int = int(getattr(sq_cfg, "max_width", 1280) if sq_cfg else 1280)
        # HUD 位置：auto(自动) / left_bottom(PC左下角) / right_top(手机共享右上角)
        self._hud_region: str = str(getattr(sq_cfg, "hud_region", "auto") if sq_cfg else "auto").lower()

        self._running: bool = True
        self._sct = None  # mss 实例（懒加载）
        self._last_state: Optional[tuple[int, int]] = None  # (存活数, 总人数)
        self._last_crop_hash: Optional[int] = None  # 上次裁剪图 phash
        self._analyze_count: int = 0
        self._hit_count: int = 0
        self._recent: list[dict] = []  # 最近识别结果（最多 8 条，供调试台）
        self._last_skip: str = ""  # 最近一次跳过/未扫描原因（心跳诊断用）
        self._loop_count: int = 0   # 循环计数（心跳日志用）

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_session(self, session: Any) -> None:
        """设置对局会话引用（main.py 在 game_session 创建后接线）。"""
        self._session = session

    def stop(self) -> None:
        self._running = False
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
            self._sct = None

    def snapshot(self) -> dict:
        """运行状态快照（供调试台轮询，线程安全）。"""
        return {
            "enabled": self._enabled,
            "interval_s": self._interval,
            "analyze_count": self._analyze_count,
            "hit_count": self._hit_count,
            "last_state": self._last_state,
            "recent": list(self._recent),
        }

    def _next_interval(self) -> float:
        return max(1.0, self._interval * random.uniform(1.0 - self._jitter, 1.0 + self._jitter))

    def _ensure_sct(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def _is_phone_stream(self) -> bool:
        """当前是否为手机屏幕共享场景（腾讯会议进程在运行，不要求前台）。
        手机版《三角洲》HUD 在画面右上角，与 PC 版（左下角）不同。
        """
        if self._monitor is None:
            return False
        check = getattr(self._monitor, "is_meeting_running", None)
        if callable(check):
            return check()
        check_fg = getattr(self._monitor, "is_meeting_foreground", None)
        if callable(check_fg):
            return check_fg()
        # 兜底：标题含腾讯会议关键词（主界面时）
        title = getattr(self._monitor, "last_title", "") or ""
        tl = title.lower()
        return any(kw in tl for kw in PHONE_STREAM_TITLES)

    def _is_delta(self) -> bool:
        """只在《三角洲行动》时分析（避免别的游戏 HUD 误报）。"""
        gk = self._gk
        if gk is None:
            return False
        name = getattr(gk, "current_name", "") or ""
        text = getattr(gk, "screen_text", "") or ""
        if "三角洲" in name or "三角洲" in text:
            return True
        # 游戏会话已识别出三角洲模式（烽火地带/撤离玩法/全面战场）：
        # 手机共享下 OCR 识别不了游戏名（全是噪声），但游戏模式由视觉弱信号
        # 正确识别——模式非空就说明已确认在打三角洲，别再因 OCR 噪声跳过队伍识别。
        session = self._session
        if session is not None:
            mode = getattr(session, "mode", "") or ""
            if any(k in mode for k in ("烽火", "撤离", "全面战场")):
                return True
        # 手机屏幕共享（腾讯会议）时游戏名只能靠 OCR 屏幕文字识别；
        # 若当前共享画面有内容（游戏 HUD 文字）则按三角洲处理（用户明确此场景=手机打三角洲）
        if self._is_phone_stream() and text:
            return True
        return False

    def _expected_team_size(self) -> int:
        """当前模式的预期队伍人数（OCR 模式锁存判定，优先于 AI 猜数字）。

        用户规则：左下角队伍面板槽位由模式决定——全面战场=4人、
        烽火地带/红鼠窝=3人。返回 0 表示模式未知（不做钳制，
        避免把合法总人数误杀）；只有模式确定时才钳制解析结果。
        """
        mode = ""
        if self._gk is not None:
            try:
                mode = (self._gk.latched_mode() or "").strip()
            except Exception:  # noqa: BLE001
                mode = ""
        if "全面战场" in mode:
            return 4
        if "红鼠窝" in mode or "烽火" in mode:
            return 3
        return 0

    def _grab_crop(self) -> Optional[bytes]:
        """抓游戏窗口左下角区域（等比缩放到 max_width），返回 JPEG bytes。
        画面未变化（phash 相似）返回 None 跳过分析。
        """
        sct = self._ensure_sct()
        monitors = sct.monitors
        idx = min(self._monitor_index, len(monitors) - 1)
        region = monitors[idx]
        phone = self._is_phone_stream()
        # 手机共享场景：直接定位腾讯会议窗口（前台可能已切到其他窗口）
        meet_rect = None
        if phone and self._monitor is not None:
            m = getattr(self._monitor, "meeting_rect", None)
            if callable(m):
                meet_rect = m()
        if meet_rect:
            left, top, w, h = meet_rect
            m = monitors[idx]
            x0 = max(left, m["left"])
            y0 = max(top, m["top"])
            x1 = min(left + w, m["left"] + m["width"])
            y1 = min(top + h, m["top"] + m["height"])
            if x1 > x0 and y1 > y0:
                region = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        else:
            rect = self._monitor.foreground_rect() if self._monitor is not None else None
            if rect and not phone:
                left, top, w, h = rect
                m = monitors[idx]
                x0 = max(left, m["left"])
                y0 = max(top, m["top"])
                x1 = min(left + w, m["left"] + m["width"])
                y1 = min(top + h, m["top"] + m["height"])
                if x1 > x0 and y1 > y0:
                    region = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        try:
            raw = sct.grab(region)
        except Exception as e:  # noqa: BLE001
            logger.debug("队伍HUD抓屏失败: %s", e)
            return None
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
        if img.width > self._max_width:
            ratio = self._max_width / img.width
            img = img.resize((self._max_width, max(1, int(img.height * ratio))))
        w, h = img.size
        if w < 60 or h < 40:
            return None
        # 队伍 HUD 区域：PC版左下角 / 手机共享右上角（auto 自动判断）
        region = self._hud_region
        if region == "auto":
            region = "right_top" if self._is_phone_stream() else "left_bottom"
        if region == "right_top":
            # 手机竖屏画面在腾讯会议窗口中部（左右是会议UI/黑边），
            # 队伍HUD在手机画面右上角 → 取窗口中部偏右、上半屏。
            # 窗口里的手机画面小，HUD更小，裁剪后再放大到约900px宽让模型看清。
            crop = img.crop((int(w * 0.42), 0, int(w * 0.92), int(h * 0.5)))
            if crop.width < 900:
                crop = crop.resize((900, max(1, int(crop.height * 900 / crop.width))))
        else:
            crop = img.crop((0, int(h * 0.45), int(w * 0.35), h))
            if crop.width < 640:
                crop = crop.resize((640, max(1, int(crop.height * 640 / crop.width))))
        try:
            from . import phash as _phash_mod
            phash = _phash_mod.phash(crop)
            if self._last_crop_hash is not None and phash - self._last_crop_hash <= 4:
                return None  # 画面未变，跳过（省 token）
            self._last_crop_hash = phash
        except Exception:  # noqa: BLE001
            pass
        buf = io.BytesIO()
        crop.save(buf, "JPEG", quality=80)
        return buf.getvalue()

    @staticmethod
    def _parse(text: str, expected: int = 0) -> tuple[int, int]:
        """从模型输出解析 (存活数, 总人数)。解析失败返回 (0, 0)。

        expected：当前模式预期人数（0=未知不钳制）。模型把 UI 数字/其他
        文字误读成"队伍N人"时（如 3 人队读出 4/5 人），N 超过预期就是误读
        ——左下角队伍面板槽位是固定的，不可能比模式预期多。直接丢弃，
        交给模式锁存判定兜底（不拿错数字发"队友阵亡"）。
        """
        alive, total = 0, 0
        m = re.search(r"(\d+)\s*人\s*存活", text)
        if m:
            alive = int(m.group(1))
        m = re.search(r"队伍\s*(\d+)\s*人", text)
        if m:
            total = int(m.group(1))
        if expected and total > expected:
            logger.info("队伍识别: 总人数 %s 超过模式预期 %s 人（左下角面板槽位固定，"
                        "视为误读），丢弃: %s", total, expected, text[:60])
            return 0, 0
        if total and alive <= 0 and ("全部存活" in text or "全存活" in text):
            alive = total
        if total and alive <= 0:
            m = re.search(r"(\d+)\s*人\s*阵亡|阵亡\s*(\d+)", text)
            if m:
                dead = int(m.group(1) or m.group(2))
                alive = max(0, total - dead)
        return alive, total

    async def _scan_once(self) -> None:
        if not self._enabled or self._llm is None:
            self._last_skip = "LLM不可用"
            return
        if not getattr(self._llm, "vision_available", False):
            self._last_skip = "视觉模型不可用"
            return
        # 前台是游戏，或腾讯会议进程在运行（手机屏幕共享，用户在手机上打游戏）→ 扫描
        if self._monitor is not None:
            is_game = getattr(self._monitor, "current_hint", "") == "game"
            if not is_game:
                check = getattr(self._monitor, "is_meeting_running", None)
                if callable(check):
                    is_game = check()
            if not is_game:
                self._last_skip = "前台非游戏且腾讯会议未运行"
                return
        if not self._is_delta():
            self._last_skip = "非三角洲画面"
            return
        # 【对局中门禁（2026-08-12 实锤误报）】只有"确认对局中"才扫描队伍 HUD：
        # 主页/靶场/大厅画面 AI 会把 UI 数字误读成队伍存活（日志 00:16:01
        # "队伍4人，2人存活"——红鼠窝每队才3人，明显是主页界面被当队伍面板），
        # 存活数抖动 → 误报"队友阵亡"（用户反馈"主页进靶场说我少人"）。
        # screen_page()=="match" = OCR 左上角读到回合 X/7 / 档位 / 撤离倒计时，
        # 是像素真文字，比 AI 猜队伍面板可靠；非对局中（home/空）不扫队伍 HUD。
        if self._gk is not None:
            try:
                if self._gk.screen_page() != "match":
                    self._last_skip = "非对局中(主页/靶场/大厅)，不扫队伍HUD"
                    return
            except Exception:  # noqa: BLE001
                logger.exception("队伍HUD对局中判断异常")
        img_bytes = self._grab_crop()
        if img_bytes is None:
            self._last_skip = "画面未变化(phash)"
            return
        self._last_skip = ""
        # 预期人数（模式锁存判定）：全面战场4人 / 烽火地带、红鼠窝3人。
        # 喂给模型防它把左下角面板槽位数读错；解析时再钳制一次。
        expected = self._expected_team_size()
        prompt = SQUAD_PROMPT.replace("{expected}", str(expected or 3))
        try:
            raw = await asyncio.wait_for(
                self._llm.vision_describe(img_bytes, prompt),
                timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("队伍HUD识别失败: %s", e)
            return
        raw = (raw or "").strip()
        if not raw or "看不清" in raw or "无法" in raw:
            logger.debug("队伍HUD看不清，跳过: %s", raw[:60])
            return
        self._analyze_count += 1
        alive, total = self._parse(raw, expected)
        self._recent.append({"ts": time.time(), "text": raw[:80], "alive": alive, "total": total})
        self._recent = self._recent[-8:]
        if total <= 0:
            return
        state = (alive, total)
        # 存活数减少 → 队友阵亡，提醒一次；状态不变 → 静默
        if self._last_state is not None and alive < self._last_state[0]:
            # 【数字抖动守卫】模型明确说"全部存活/无人阵亡"时，alive 减少只是
            # HUD 数字识别抖动（实锤：准备阶段 00:04:53 画面文字"队伍3人，全部
            # 存活"，模型把 4 人抖成 3 人就发"队友阵亡"）。模型亲口说没人死，
            # 数字不可信，绝不发阵亡。
            if re.search(r"全部存活|全存活|均存活|无人阵亡|没有阵亡|都存活", raw):
                logger.info(
                    "队伍识别: 数字 %s → %s 但画面文字明确[全部存活]，"
                    "视为HUD识别抖动，不发布队友阵亡 (画面=%s)",
                    self._last_state[0], alive, raw[:80])
                self._last_state = state
                return
            prev_alive, prev_total = self._last_state
            summary = f"队伍{prev_total}人，队友阵亡了一个（{prev_alive}人剩{alive}人存活）"
            activity = {
                "summary": summary,
                "app_hint": "game",
                "confidence": 0.7,
                "moment": "teammate_down",
                "source": "squad",
                "ocr_text": raw[:120],
                # 存活数/总人数：供对局会话推断模式（3人=烽火地带，4人=全面战场）
                "alive": alive,
                "total": total,
            }
            self._hit_count += 1
            logger.info("队伍识别: 队友阵亡 %s → %s (画面=%s)", prev_alive, alive, raw[:80])
            await self._bus.publish(Event(EventType.SCREEN_ACTIVITY, activity, "squad"))
        self._last_state = state

    async def run(self) -> None:
        while self._running:
            try:
                await self._scan_once()
            except Exception:  # noqa: BLE001
                logger.exception("队伍HUD识别异常")
            self._loop_count += 1
            # 心跳诊断日志：每 10 个循环（约 40s）打一次，确认模块在工作/卡在哪。
            # 之前"队友阵亡没检测到"时整个模块零日志，无法判断是没扫描、
            # 还是扫了识别不到——心跳能直接定位。
            if self._loop_count % 10 == 0:
                gk = self._gk
                gname = getattr(gk, "current_name", "") if gk is not None else ""
                gtext = getattr(gk, "screen_text", "") if gk is not None else ""
                logger.info(
                    "队伍识别心跳: 分析=%d 命中=%d 最近状态=%s 上次跳过=%s "
                    "(游戏名=%r 画面文字=%s)",
                    self._analyze_count, self._hit_count, self._last_state,
                    self._last_skip, gname, (gtext or "")[:40],
                )
            await asyncio.sleep(self._next_interval())
