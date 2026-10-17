"""前台陪伴切换控制器 —— 8 秒稳定闸门（2026-09-06 重新设计）。

用户明确需求（原话归纳）：
  1. 陪伴切换 + 通知弹窗 统一由【单一 8 秒稳定闸门】驱动：目标状态必须**连续稳定满
     8 秒**才真正提交；中途任何变化都重置计时（"不满 8 秒不开始切换，然后重新计时，
     直到下一次满了八秒"）。这就从根上消灭"抖一下就切 / 卡死 / 乱弹"。
  2. 高压占用软件 → 本地双工切云端/快照，**仍由旧的 GPU 负载锁循环**
     （_duplex_mode_lock_loop）负责；本控制器**不碰双工模式**，两者并存互不抢。
  3. 普通小窗（非最大化/非全屏）**不切换陪伴**（最稳不抖）。
  4. 最小化 → 等 8 秒再切到对应陪伴（"正常"或其他软件的陪伴）；软件**进程被终止**
     → 立即切到新状态（见 ④，跳过 8 秒等待）。两者分属不同事件，不可混为一谈。

每 1 秒采样一次前台，推导目标状态（恒为 "app:<id>" 或 "normal"）：
  - 前台是游戏(hint==game) → 目标=正常，且【立即静默】提交：双工切换归 GPU 锁循环，
    此时该弹的是锁循环那张"让路给游戏"卡，而不是"换回平时的陪法"卡。
  - 前台最大化/全屏 且 命中软件知识库 → 目标=该软件专属陪伴。
  - 前台最大化/全屏 但 未命中知识库 → 目标=正常。
  - 前台非沉浸（普通小窗 / 桌面 / 最小化）→ 目标=正常（最小化/桌面走 8 秒闸门；
    进程退出在 ④ 立即处理，不在此列）。
  - 已提交陪伴的软件进程被终止 → 目标=新前台状态（normal 或另一个软件）且【立即】提交
    （非静默，弹"换回平时"卡或"换成X的陪法"卡）。

弹窗仍是既有 5 种场景：game / focus / back / ok / default。
本控制器只产生 focus（换成X的陪法）与 back（换回平时的陪法）；
game 由双工锁循环产生，ok 由连接完成回调产生，default 为通用兜底。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional, Tuple

# 宿主自身进程名（与 local_duplex.SELF_PROC_NAMES 同源，改动需同步）：前台是伴侣自家窗口时，
# 一律视为"回到通用陪伴"，绝不送软件知识库 detect——否则会匹配到名为 "WorkBuddy" 的
# 陪伴方案，弹窗/下发提示词里冒出 "WorkBuddy陪伴"，把自家宿主当成了要陪的软件。
# 本地定义以避免从 llm.local_duplex 引重依赖（numpy/ollama 链）进本模块导入图。
_SELF_PROC_NAMES: frozenset = frozenset({
    "workbuddy.exe", "ai桌面伴侣.exe", "codebuddy.exe",
})

logger = logging.getLogger(__name__)

#: 当前活跃控制器单例（防止重载/重复构造产生多个 8s 闸门线程导致双弹窗/双重注）。
_active_controller = None

#: 目标状态需连续稳定多久（秒）才真正提交切换。用户指定 8 秒。
STABLE_S = 8.0

#: 采样周期（秒）。1s 足够，不影响 8s 判定精度。
_TICK_S = 1.0

#: 短视频类软件名关键字（命中则置 _browsing_short_video，让模型安静陪看视频）。
_SHORT_VIDEO_KW = ("抖音", "哔哩哔哩", "短视频", "douyin", "bilibili")


class SwitchController:
    """前台陪伴切换的 8 秒稳定控制器（独立线程，1s 一跳）。

    职责边界：
      · 管：软件知识库陪伴的挂载/卸载 + 对应的 focus/back 弹窗 + _browsing_short_video。
      · 不管：双工模式（local/realtime/snapshot）——那是 _duplex_mode_lock_loop 的地盘。
    """

    def __init__(self, strategy: Any, monitor: Any, app_knowledge: Any) -> None:
        self._st = strategy
        self._mon = monitor
        self._ak = app_knowledge

        # 已提交的陪伴状态："app:<id>" 或 "normal"
        self._committed: str = "normal"
        self._committed_exe: str = ""
        # 候选目标（正在计时中的状态）
        self._candidate: str = "normal"
        self._candidate_exe: str = ""
        self._candidate_name: str = ""
        self._candidate_since: float = 0.0

        self._running = True
        self._thread = threading.Thread(target=self._loop, name="switch-ctrl", daemon=True)
        self._thread.start()
        global _active_controller
        _prev = _active_controller
        if _prev is not None and _prev is not self:
            try:
                _prev.stop()
                logger.info('[切换控制器] 停掉上一个实例（避免双闸门/双弹窗）')
            except Exception:  # noqa: BLE001
                pass
        _active_controller = self
        logger.info("[切换控制器] 启动：8s 稳定闸门（陪伴切换+弹窗）；双工仍由 GPU 锁循环负责")

    # ------------------------------------------------------------------ 生命周期
    def stop(self) -> None:
        self._running = False
        global _active_controller
        if _active_controller is self:
            _active_controller = None

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                logger.debug("[切换控制器] tick 异常", exc_info=True)
            time.sleep(_TICK_S)

    # ------------------------------------------------------------------ 目标推导
    def _compute_target(self) -> Tuple[Optional[str], str, str, bool, bool]:
        """返回 (target, exe, name, force_immediate, silent)。

        target 恒为 'app:<id>' 或 'normal'；None 表示采样失败（本 tick 放弃）。
        非沉浸（普通小窗/桌面/最小化）一律视为"未沉浸"→ 目标 normal，
        这正是"退出/最小化也走 8 秒"的实现。
        """
        mon = self._mon
        if mon is None:
            return None, "", "", False, False
        try:
            fg_hint = mon.foreground_hint() or ""
            fg_exe = mon.foreground_proc_name() or ""
            fg_title = mon.foreground_title() or ""
            # 宿主自身前台（workbuddy.exe / ai桌面伴侣.exe / codebuddy.exe）：直接回通用陪伴，
            # 不送软件知识库 detect——治"WorkBuddy陪伴"弹窗（把自家宿主当成要陪伴的软件）。
            if fg_exe and fg_exe.lower() in _SELF_PROC_NAMES:
                return "normal", "", "", False, False
            # 弹窗沉浸判定用更宽的 is_foreground_immersive：最大化/铺满工作区/铺满显示器即沉浸，
            # 不必严格独占全屏（微信等 Qt 自绘无边框窗口最大化时 IsZoomed 拿不到，靠面积兜底）。
            # 注意：游戏锁仍用 mon.is_foreground_fullscreen()（proactive.py），此处不影响。
            is_immersive = bool(mon.is_foreground_immersive())
        except Exception:  # noqa: BLE001
            return None, "", "", False, False

        # ① 游戏前台：双工切换归 GPU 锁循环；陪伴立即静默回到正常（不弹"换回平时"）
        if fg_hint == "game":
            return "normal", "", "", True, True

        # ② 沉浸式（最大化/铺满工作区，不必严格全屏）前台
        if is_immersive:
            matched = False
            if (fg_title or fg_exe) and self._ak is not None:
                try:
                    matched = self._ak.detect(fg_title, fg_exe)
                except Exception:  # noqa: BLE001
                    matched = False
            cur = getattr(self._ak, "_current", None) if matched else None
            if cur:
                _app_id = str(cur.get("id", "") or "")
                if _app_id:
                    return ("app:" + _app_id), fg_exe, (cur.get("name", "") or "这个软件"), False, False
            return "normal", "", "", False, False

        # ③ 非沉浸（普通小窗 / 桌面 / 最小化）→ 未沉浸，8 秒后回到正常
        return "normal", "", "", False, False

    # ------------------------------------------------------------------ 每 tick
    def _tick(self) -> None:
        now = time.time()
        target, exe, name, force, silent = self._compute_target()
        if target is None:
            return

        # ④ 进程终止：已提交陪伴的软件彻底关了 → 立即切到「新状态」（跳过 8 秒）
        #    保留 _compute_target 算出的新目标（可能是另一个软件或 normal），只强制立即提交。
        #    退出 = 用户主动离开该软件 → 新前台立即生效；区别于「最小化」——最小化进程仍在
        #    跑，走 8 秒闸门防误触（用户可能只是扫一眼别的软件又切回来）。
        if (self._committed not in ("normal", None) and self._committed_exe
                and self._mon is not None):
            try:
                if not self._mon.is_process_running(self._committed_exe):
                    logger.info("[切换控制器] 检测到进程终止(%s)，立即切到新状态（跳过 8s）",
                                self._committed_exe)
                    force = True
                    silent = False
            except Exception:  # noqa: BLE001
                pass

        # 短视频浏览标志（每 tick 刷新，保证"刷短视频时安静陪看"及时生效）
        try:
            self._st._browsing_short_video = bool(
                target.startswith("app:") and any(k in (name or "") for k in _SHORT_VIDEO_KW))
        except Exception:  # noqa: BLE001
            pass

        # ⑤ 8 秒稳定闸门
        if target != self._candidate:
            self._candidate = target
            self._candidate_exe = exe
            self._candidate_name = name
            self._candidate_since = now
            logger.info("[切换控制器] 目标变化 → %s (%s)，开始 %.0fs 计时",
                        target, name or "正常", STABLE_S)
        if force:
            # 立即提交：把计时起点推到过去，本 tick 即满足 8 秒条件
            self._candidate_since = now - (STABLE_S + 1.0)
        if self._committed == self._candidate:
            return  # 已处于该状态，无需动作
        if (now - self._candidate_since) < STABLE_S:
            return  # 未满 8 秒，继续等（中途变化已由上面的 target 变化重置）

        self._commit(self._candidate, self._candidate_exe, self._candidate_name, silent)

    # ------------------------------------------------------------------ 提交
    def _commit(self, kb_id: str, exe: str, name: str, silent: bool = False) -> None:
        """真正执行陪伴切换（调用 proactive 的既有执行器，不重复造轮子）。"""
        # 首启引导期间：不执行陪伴切换/不弹窗，只记录 committed 状态，避免配置阶段
        # 出现「视觉模型正在准备」等通知，也避免往未就绪的双工会话注入软件专属提示词。
        # 【2026-10-02 修复·静默期把软件切换整个冻住】原实现在引导静默期
        # `self._committed = kb_id; return` —— **什么都不做**：不切陪法、不注提示词、
        # 不重建会话。于是即使静默锁后来解开了，这次切换也已经"被记成已完成"
        # （_committed 已设），再也不会重做 → 软件切换永久失效。
        # 实测（run_20261002_205858.log）：21:01:18「首启引导期间静默提交 → app:douyin」
        # → 之后开发者页无任何切换记录、也没有弹窗，正是这条。
        #
        # 正确语义：**引导静默只应"不打扰用户"（不弹窗），不应"不干活"**。
        # 切陪法/注提示词/重建会话是功能本身，必须照做；只有 show_mode_notify
        # 这一类用户可见反馈需要压制。所以这里**只把 silent 置 True 继续往下走**，
        # 不再 return。
        _onboarding = False
        try:
            from ..ui import mode_notify
            _onboarding = bool(mode_notify.is_onboarding_silent())
            if _onboarding:
                logger.info("[切换控制器] 首启引导静默期 → 照常切换到 %s（仅压制弹窗）", kb_id)
        except Exception:  # noqa: BLE001
            _onboarding = False
        silent = bool(silent) or _onboarding

        st = self._st
        try:
            if kb_id == "normal":
                # 卸载软件知识上下文
                if self._ak is not None:
                    self._ak.reset()
                if silent:
                    # 进游戏：不弹"换回平时"卡（该弹的是锁循环的"让路给游戏"卡），
                    # 但要清掉去重标记，否则下次同一软件会被 dedup 拦住不重连。
                    try:
                        st._duplex_injected_kb_id = "none"
                        st._duplex_kb_pending_id = None
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info("[切换控制器] 静默提交 → 正常陪伴（进游戏，不弹窗）")
                else:
                    # 非游戏、从某软件/沉浸态回到通用陪伴：弹「换模式陪你」卡 + 重注最新提示词；
                    # 仅当此前确实处于某软件陪伴时才弹（启动/一直桌面这种无变化的提交保持静默，避免开机空弹）。
                    _was_app = self._committed not in ("normal", None)
                    st._kb_reprepare_with_popup("none", "", silent=(not _was_app))
                    logger.info("[切换控制器] 提交 → 正常陪伴（目标已稳定 %.0fs, 弹窗=%s）",
                                STABLE_S, not _was_app)
            else:
                # 切到软件/游戏专属陪法。silent 只压制"弹窗"，陪法切换与提示词注入
                # 照常执行（引导静默期就是靠这条路真正干活的）。
                st._kb_reprepare_with_popup(kb_id, name, bool(silent))
                logger.info("[切换控制器] 提交 → %s 专属陪伴（目标已稳定 %.0fs, id=%s, 静默=%s）",
                            name, STABLE_S, kb_id, bool(silent))
        except Exception:  # noqa: BLE001
            logger.warning("[切换控制器] 提交失败", exc_info=True)
        self._committed = kb_id
        self._committed_exe = exe
