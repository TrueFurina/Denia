"""对局会话（DeltaGameSession）：单局记忆 + 阶段检测 + 本局小结。

为什么需要它：之前 AI 的互动是"事件 → 孤立单句"，不记得本局发生了什么，
所以没真人味——不知道这局死了几次、是不是撤离成功、打了多久、是连败还是连胜。
这个模块维护"当前这一局"的状态机，让主动策略能：
  - 推断模式（烽火地带 3 人 / 全面战场 4 人，也可从 OCR 文字识别）
  - 检测对局开始 / 结束（结算 / 撤离 / 返回大厅）
  - 记录本局击杀 / 阵亡 / 队友倒下 / 是否撤离
  - 结算后给 AI 一段"本局小结"，用来聊这局（情绪价值大头）
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

PHASE_LOBBY = "lobby"
PHASE_PLAYING = "playing"
PHASE_SETTLED = "settled"


def kill_bucket_text(kills: int) -> str:
    """击杀数 → '多/少'的模糊描述。

    视觉/OCR 统计的击杀数都可能虚高（视觉脑补"击杀了敌人"、OCR 误归属），
    结算/故事线话术说具体数字（"第10个了""杀了8个"）会立刻穿帮。这里把
    数字转成"多/少"的模糊说法，只给情绪不给数据。
    """
    if kills >= 3:
        return "击杀不少"
    if kills >= 1:
        return "击杀不多"
    return "基本没击杀"

# 对局开始信号（OCR / 视觉摘要）
MATCH_START_PATTERN = re.compile(
    r"进入对局|匹配成功|对局开始|开始对局|准备阶段|进入战场|开始行动|游戏开始|对局加载|等待进入|开场|行动开始"
)

# 开局信号的"还没进对局"排除：视觉模型常把菜单页/备战界面写成开始信号
# （"在游戏大厅准备开始对局""红鼠窝模式选择界面准备开始游戏""正在准备阶段
# 选择武器装备"），实际人还在主页/备战没进图（15:18:02 实锤：人还在大厅却被
# 判"对局开始"，随后菜单背景动画又被判"对局结束"，白搭"这局没阵亡，顺利撤了"）。
# 真开局摘要（"准备阶段，倒计时9秒""进入战场""匹配成功"）不带这些菜单页词。
# 不做判定、只做排除——"准备阶段"仍是开局信号，"准备阶段倒计时"照常开局。
_MATCH_START_MENU_RE = re.compile(
    r"大厅|主界面|选择模式|模式选择|准备开始|准备进入|仓库界面|商城|主页|"
    r"武器装备|选择装备|装备配置|配置界面|未进入|还没进"
)

# 红鼠窝回合制（BO7）HUD："回合 X/7"（X=1~7）。这是红鼠窝最可靠的识别信号——
# 顶部三队比分方块是图案，OCR 扫不到；而"回合"只在红鼠窝出现
# （烽火/全面战场没有回合概念），X/7 更明确。
_ROUND_7_RE = re.compile(r"[1-7]\s*/\s*7")

# 屏幕轨迹动作大类提取：从视觉摘要中抓"玩家当前在做什么"的核心动作。
# 摘要每帧会换说法（"正在搜刮物资"/"玩家在搜刮物资"），只保留命中的动作词，
# 与上一条轨迹相同则跳过（同一画面不重复记录），场景切换才追加。
# 优先级即顺序：结算/撤离 > 死亡 > 交火 > 搜刮 > 等待 > 移动。
_TRACK_ACTION_RE = re.compile(
    r"撤离成功|成功撤离|已撤离|结算画面|撤离失败|"
    r"撤离中|正在撤离|准备撤离|即将撤离|靠近撤离点|前往撤离点|"
    r"登机|抓绳|乘车驶离|"
    r"阵亡|被击杀|被淘汰|观战|等待救援|倒地|"
    r"交火中|在交火|战斗中|开火|对枪|交战中|"
    r"搜刮|搜物资|拾取|舔包|开箱|翻箱|搜刮物资|"
    r"等待回合|回合等待|准备阶段|等待开始|"
    r"移动转点|转点|赶路|跑图|转移|"
    r"待命|蹲守|躲藏|休整"
)
# 对局结束信号：只在"明确的结算/撤离/返回大厅"时结束。
# 注意不能匹配战斗中的"撤离点""队友阵亡"这类词（那不是结算，会误判提前结束）。
# 【不收录裸"结算"】——菜单背景动画/开场动画会被视觉描述成"疑似结算或开场
# 动画"（15:18:37 实锤：人还在大厅，菜单背景动画被当成"对局结束"白搭结算
# 聊天），只有明确指向结算画面的词才算真结算。含全面战场结算
# （占领/夺取/防守成功/失败）与红鼠窝/战斗结算词。
MATCH_END_PATTERN = re.compile(
    r"结算画面|结算界面|对局结算|本局结算|战后结算|撤离成功结算|"
    r"战局结束|对局结束|返回大厅|回到大厅|撤离成功|已撤离|成功撤离|"
    r"撤离失败|你已阵亡|已被淘汰|被淘汰出局|本局结束|游戏结束|对战结束|"
    r"占领成功|占领失败|夺取成功|夺取失败|防守成功|防守失败|"
    r"战斗胜利|战斗失败|我方胜利|我方失败|据点被占领"
)


class DeltaGameSession:
    """当前一局《三角洲行动》的会话状态（模式/阶段/本局事件/小结）。"""

    def __init__(self, max_events: int = 24) -> None:
        self._max_events = max_events
        self._phase: str = PHASE_LOBBY
        self._mode: str = ""          # "烽火地带" / "全面战场"
        self._mode_locked: bool = False  # 模式已被强信号（OCR文字/队伍人数）确认并锁定
        self._started_at: float = 0.0
        self._ended_at: float = 0.0
        self._events: deque = deque(maxlen=max_events)  # (ts, kind, desc)
        self._kills = 0
        self._deaths = 0
        self._teammates_down = 0
        self._extracted = False
        self._victory = False
        self._settle_handled = False  # 本局结算是否已触发过局间聊天（防重）
        self._loss_streak = 0         # 连续阵亡/失败的局数（跨局保留）
        self._player_name = ""        # 玩家游戏昵称（从击杀播报提取，跨局保留）
        # 屏幕轨迹：本局画面经历的动作大类序列（从视觉摘要提取）。"AI 一个人看"
        # 路线下 kill/died 时刻已关闭、on_moment 不再有击杀/阵亡事件，屏幕轨迹
        # 是唯一的过程记忆——AI 说话能带"刚才在搜物资/交火"的整局经历感。
        self._screen_track: deque = deque(maxlen=8)

    # ---- 只读状态 ----
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def mode_locked(self) -> bool:
        """模式是否已被强信号（OCR 文字/队伍人数）锁定（锁死后视觉弱信号不可改）。"""
        return self._mode_locked

    @property
    def playing(self) -> bool:
        return self._phase == PHASE_PLAYING

    @property
    def settled(self) -> bool:
        return self._phase == PHASE_SETTLED

    @property
    def kills(self) -> int:
        return self._kills

    @property
    def deaths(self) -> int:
        return self._deaths

    @property
    def teammates_down(self) -> int:
        return self._teammates_down

    @property
    def extracted(self) -> bool:
        return self._extracted

    @property
    def victory(self) -> bool:
        return self._victory

    @property
    def loss_streak(self) -> int:
        return self._loss_streak

    @property
    def player_name(self) -> str:
        return self._player_name

    def set_player_name(self, name: str) -> None:
        """记录玩家游戏昵称（首次确认后跨局保留）。"""
        name = (name or "").strip()
        if len(name) >= 2 and (not self._player_name or name != self._player_name):
            logger.info("记录玩家昵称: %s", name)
        if len(name) >= 2:
            self._player_name = name

    def duration_s(self) -> float:
        if not self._started_at:
            return 0.0
        end = self._ended_at if self._ended_at else time.time()
        return max(0.0, end - self._started_at)

    # ---- 状态更新 ----
    @staticmethod
    def _infer_mode(text: str, squad_total: int) -> str:
        """从文字 / 队伍总人数推断模式。

        识别优先级（都命中时按此顺序，识别词来自用户口述的 HUD 特征）：
          1. 红鼠窝（S7 烽火地带下的 3v3v3 小队竞技，玩家口语常写"红薯窝"）：
             屏幕顶部是三个队伍的比分方块。
          2. 全面战场（大战场）：屏幕顶部是 ABCD 各种点位、载具大战、32v32。
          3. 烽火地带（摸金撤离）：撤离/摸金/机密/绝密档位只在烽火出现。
        队伍人数兜底：3人=烽火地带，4人=全面战场（红鼠窝也是 3v3，但队伍识别
        在手机共享下常数不到人，不用人数区分红鼠窝）。返回 "" 表示无法确定。
        """
        t = text or ""
        if (any(w in t for w in ("红鼠窝", "红薯窝", "鼠窝", "3v3", "三v三", "三对三"))
                or "回合" in t or _ROUND_7_RE.search(t)):
            return "红鼠窝"
        if any(w in t for w in ("全面战场", "大战场", "32v32", "8v8", "载具大战",
                                "占领据点", "据点争夺", "夺取据点", "点位争夺")):
            return "全面战场"
        if any(w in t for w in ("烽火", "撤离", "摸金", "机密", "绝密", "常规")):
            return "烽火地带"
        if squad_total == 3:
            return "烽火地带"
        if squad_total == 4:
            return "全面战场"
        return ""

    def update_mode(self, text: str = "", squad_total: int = 0, source: str = "") -> None:
        """推断并更新模式。

        信号分级（关键：视觉模型在手机共享的模糊画面上会瞎猜模式名——
        "坚守阵地/机密任务/护送博士/救援模式"全是编的，也常把烽火说成全面战场）：
          - 强信号：OCR 真实游戏文字（source 含 "ocr"）、队伍人数（"squad"）。
            确认模式后本局锁定，不再被任何信号改掉。
          - 弱信号：视觉摘要（"vision"/"realtime"）。模式未锁定时允许参与/纠正
            ——手机共享下 OCR 常把进图前的"32v32载具大战场"规则说明文字当强信号，
            把模式锁成"全面战场"，而视觉一直正确看到"机密撤离玩法"（烽火地带），
            模式被钉死在错误值上（用户反馈"红薯窝识别成普通模式"的根因）。
            只有强信号锁定后才不可被弱信号覆盖。
        """
        t = text or ""
        if "ocr" in source or source == "squad":
            mode = self._infer_mode(t, squad_total)
            if mode:
                self._mode = mode
                self._mode_locked = True
            return
        # 弱信号：仅强信号锁定后不可改；未锁定时允许视觉纠正
        if self._mode_locked:
            return
        mode = self._infer_mode(t, squad_total)
        if mode and mode != self._mode:
            logger.info("模式更新: %s → %s（弱信号: %s）",
                        self._mode or "未知", mode, t[:30])
        if mode:
            self._mode = mode

    def on_screen(self, summary: str, ocr_page: str = "") -> bool:
        """处理屏幕摘要，检测阶段切换。返回 True 表示发生了切换。

        ocr_page：OCR 左上角文字识别的页面状态（home/mount/confirm_extract/""）。
        对局中（match）时摘要里的"撤离成功/已撤离"是视觉幻觉（用户"对局内就说我
        赢了带出装备"的根因）——真撤离时 OCR 会读到"撤离成功"（confirm_extract），
        否则一律不提前结束对局，避免 settled 卡死吞掉整局互动。
        """
        s = summary or ""
        if self._phase in (PHASE_LOBBY, PHASE_SETTLED):
            if MATCH_START_PATTERN.search(s):
                # 摘要带菜单页词（大厅/主界面/准备开始/模式选择）→ 人还在主页
                # 没进图，"开始对局"只是"准备开始对局"的幻读，不算真开局
                # （15:18:02 实锤：人还在大厅却被判"对局开始"）
                if _MATCH_START_MENU_RE.search(s):
                    logger.debug("开局信号被菜单页词排除: %s", s[:60])
                    return False
                self._start_round()  # 从大厅开局 / 结算后直接开下一局
                return True
        elif self._phase == PHASE_PLAYING:
            # 记录屏幕轨迹：视觉摘要作为"这局经历了什么"的记忆来源
            # （"AI 一个人看"路线下 kill/died 时刻已关闭，on_moment 不再有
            # 击杀/阵亡事件，屏幕轨迹是唯一的过程记忆）。
            self._record_screen_track(s)
            # OCR 明确对局中（档位/撤离倒计时可见）→ 摘要里的撤离/胜负是幻觉，
            # 不结束对局（真撤离 OCR 会先读到"撤离成功"→ confirm_extract）
            if ocr_page == "match":
                return False
            if MATCH_END_PATTERN.search(s):
                # 摘要明确"撤离成功/已撤离" → 标记本局成功撤离（结算聊天要说
                # "物资带出来了"）；误报时 resume_playing 会清掉这个标记。
                # 红鼠窝是纯战斗竞技（无撤离玩法），画面里的"撤离成功"必然是
                # 视觉脑补（红鼠窝只有回合胜负），不能当真撤离标记。
                if (re.search(r"撤离成功|已撤离|成功撤离", s)
                        and self._mode != "红鼠窝"):
                    self.set_extracted()
                self._end_round()
                return True
        return False

    def _record_screen_track(self, summary: str) -> None:
        """记录屏幕轨迹：把视觉摘要提炼成"动作大类"存进本局记忆。

        视觉摘要每 2s 更新一次且措辞常变（"正在搜刮物资"/"玩家在搜刮物资"），
        直接存原文会把同一画面重复记一大串。做法：用 _TRACK_ACTION_RE 提取
        核心动作，与上一条轨迹相同时跳过（连续同一画面只记一次），场景真正
        切换时才追加，形成"搜刮物资→交火中→撤离中"这样的一局经历。
        """
        s = (summary or "").strip()
        if not s:
            return
        m = _TRACK_ACTION_RE.search(s)
        if not m:
            return
        action = m.group(0)
        if self._screen_track and self._screen_track[-1] == action:
            return
        self._screen_track.append(action)
        logger.info("屏幕轨迹: %s（摘要=%s）",
                    "→".join(self._screen_track), s[:50])

    def on_moment(self, moment: str) -> None:
        """关键时刻 → 写入本局时间线并累计。"""
        if not self._started_at:
            self._started_at = time.time()
        now = time.time()
        if moment == "kill":
            self._kills += 1
            self._events.append((now, "kill", "击杀敌人"))
        elif moment == "died":
            self._deaths += 1
            self._events.append((now, "died", "自己被击杀"))
        elif moment == "teammate_down":
            self._teammates_down += 1
            self._events.append((now, "teammate", "队友倒下"))
        elif moment == "victory":
            self._victory = True
            self._events.append((now, "victory", "本局获胜"))
        elif moment == "defeat":
            self._events.append((now, "defeat", "本局失败"))
        elif moment == "clear":
            self._events.append((now, "clear", "本局通关"))

    def on_squad(self, alive: int, total: int) -> None:
        """队伍存活状态更新：用总人数反推模式（3人=烽火地带，4人=全面战场）。"""
        self.update_mode("", total, source="squad")

    def set_extracted(self) -> None:
        """标记本局撤离成功（烽火地带的关键结局）。"""
        self._extracted = True
        self._events.append((time.time(), "extract", "成功撤离"))

    # ---- 对局生命周期 ----
    def _start_round(self) -> None:
        self._phase = PHASE_PLAYING
        self._started_at = time.time()
        self._ended_at = 0.0
        self._events.clear()
        self._kills = 0
        self._deaths = 0
        self._teammates_down = 0
        self._extracted = False
        self._victory = False
        self._settle_handled = False
        self._mode = ""  # 新局重新推断
        self._mode_locked = False
        self._screen_track.clear()
        logger.info("对局开始（模式待定）")

    def _end_round(self) -> None:
        if self._phase != PHASE_PLAYING:
            return
        self._phase = PHASE_SETTLED
        self._ended_at = time.time()
        # 连败 / 连胜统计（跨局保留）
        if self._victory or self._extracted:
            self._loss_streak = 0
        elif self._deaths > 0 or any(e[1] == "defeat" for e in self._events):
            self._loss_streak += 1
        logger.info(
            "对局结束: 模式=%s 时长=%.0fs 击杀=%d 阵亡=%d 队友倒=%d 撤离=%s 连败=%d",
            self._mode or "未知", self.duration_s(), self._kills, self._deaths,
            self._teammates_down, "成功" if self._extracted else "无", self._loss_streak,
        )

    def resume_playing(self) -> None:
        """视觉摘要误报结算时恢复对局（如"撤离成功后的登机动画"其实是撤离点待命）。

        视觉模型常在撤离点附近把"准备撤离/靠近撤离点"脑补成"撤离成功/登机动画"，
        摘要误命中 MATCH_END_PATTERN 会提前 _end_round()，把后续所有对局画面
        都锁在"已结算"分支、吞掉一整局的互动。画面实际还在搜物资/战斗时调用此
        方法恢复 PLAYING，让场景搭话/关键时刻继续触发。
        """
        if self._phase != PHASE_SETTLED:
            return
        self._phase = PHASE_PLAYING
        self._ended_at = 0.0
        self._settle_handled = False
        # 误报结算恢复时清掉误设的结局标记（"撤离成功"可能只是视觉脑补，
        # 本局还没真正撤出；保留会让结算聊天错报"物资带出来了"）
        self._extracted = False
        self._victory = False
        logger.info("对局状态恢复: settled → playing（疑似误报结算，画面仍在对局中）")

    def reset_to_lobby(self) -> None:
        """回到主界面/菜单页：上局彻底结束，会话重置待命新对局。

        场景：上局结算画面的结算聊天被"误报结算→恢复对局"链路过早复位，
        导致用户回到游戏主界面后摘要再命中一次结束信号，结算聊天延迟到
        主界面才补发（"这局有点可惜下把打回来"在主界面冒出很突兀）。
        主界面证据命中时调用：清空本局数据、回 LOBBY，未消费的结算聊天
        不再补发；下一局从 LOBBY 正常开局。
        """
        self._phase = PHASE_LOBBY
        self._started_at = 0.0
        self._ended_at = 0.0
        self._events.clear()
        self._kills = 0
        self._deaths = 0
        self._teammates_down = 0
        self._extracted = False
        self._victory = False
        self._settle_handled = False
        self._screen_track.clear()
        # 模式名可保留（下一局可能还是同模式），锁标记清掉让新局重新推断
        self._mode_locked = False
        logger.info("对局会话重置: settled → lobby（已回到主界面，上局结束）")


    def settle_consume(self) -> bool:
        """结算是否已处理过（局间聊天防重）。返回 True=已处理。"""
        if self._settle_handled:
            return True
        self._settle_handled = True
        return False

    # ---- 小结 ----
    def storyline_text(self) -> str:
        """本局故事线：把这一局从开始到现在的经历拼成一句口语化叙事。

        真人朋友看待一局游戏是"完整经历"（模式、打了多久、中间发生过什么、
        结局如何），不是孤立数字。这段叙事让 AI 说话能带"这局/刚才那波"的
        整体感——比如"开局就掉了个队友，后面愣是打了回来"。仅在开局后
        有意义；从未开局返回空串。与 summary_line 的区别：summary_line 是
        结算报表式（给结算聊天摆数据），这里是叙事式（给话术 LLM 当整局
        记忆背景，强调过程而不是字段罗列）。
        """
        if not self._started_at:
            return ""
        parts = []
        if self._mode:
            parts.append(f"模式是{self._mode}")
        dur = self.duration_s()
        if dur >= 60:
            parts.append(f"打了约{int(dur // 60)}分钟")
        elif dur > 0:
            parts.append(f"打了约{int(dur)}秒")
        if self._kills or self._deaths or self._teammates_down:
            segs = []
            if self._kills:
                segs.append(kill_bucket_text(self._kills))
            if self._deaths:
                segs.append(f"阵亡了{self._deaths}次")
            if self._teammates_down:
                segs.append(f"队友倒下{self._teammates_down}次")
            parts.append("过程：" + "、".join(segs))
        # 屏幕轨迹：这局画面经历了什么（"AI 一个人看"下没有击杀/阵亡时刻事件，
        # 轨迹是唯一的过程记忆）。用"→"串起来给话术当整局经历背景。
        if self._screen_track:
            parts.append("一路经历了：" + "→".join(self._screen_track))
        if self._victory:
            parts.append("最后赢了")
        elif self._extracted:
            parts.append("最后成功撤离")
        elif self._deaths > 0:
            parts.append("最后阵亡了")
        elif "defeat" in {e[1] for e in self._events}:
            parts.append("最后输了")
        return "，".join(parts)

    def summary_line(self) -> str:
        """本局小结文本（供结算聊天 / LLM 上下文）。"""
        parts = []
        if self._mode:
            parts.append(f"模式：{self._mode}")
        dur = self.duration_s()
        if dur >= 60:
            parts.append(f"对局时长：约{int(dur // 60)}分钟")
        elif dur > 0:
            parts.append(f"对局时长：约{int(dur)}秒")
        parts.append(f"击杀情况：{kill_bucket_text(self._kills)}")
        parts.append(f"阵亡 {self._deaths} 次")
        if self._teammates_down:
            parts.append(f"队友倒下 {self._teammates_down} 次")
        if self._victory:
            parts.append("结局：本局获胜")
        elif self._extracted:
            parts.append("结局：成功撤离，物资带出来了")
        elif self._deaths > 0:
            parts.append("结局：阵亡")
        elif "defeat" in {e[1] for e in self._events}:
            parts.append("结局：本局失败")
        return "，".join(parts)
