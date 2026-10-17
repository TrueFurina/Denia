"""游戏知识库：识别当前游戏，把"游戏名 + 玩法攻略 + 术语数据"喂给 AI。

用户在不同游戏时，希望 AI 知道游戏的名字、玩法、术语，免得说错话。
识别来源：
  1. 前台窗口标题（ForegroundMonitor 发布 FOREGROUND_CHANGED）
  2. 游戏 OCR 屏幕文字（GameOcrMonitor 高频扫描）——游戏内 UI 文本更可靠

命中后，把该游戏的 knowledge 文本注入对话 / 主动话术的 LLM 上下文，
让 AI 说话贴合当前游戏（如《三角洲行动》里"阵亡会丢装备""打死人机不庆祝"）。

配置示例（config.yaml）：
```yaml
games:
  - id: delta_force
    name: 三角洲行动
    keywords: [三角洲, delta force]   # 窗口标题/屏幕文字出现任意一个即命中
    knowledge: |
      游戏玩法介绍、术语解释、注意事项（AI 会按这些说话）
```
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

import yaml

from ..config import Config

logger = logging.getLogger(__name__)

# ===================== 用户可编辑知识库（合并 config 默认） =====================
# 用户通过 UI 增删改的知识库存独立文件，不污染 config.yaml 默认；加载时与 config
# 默认合并（同名 id 用户条目覆盖默认，新 id 追加）。对齐 pub-local-jarvis 的
# game-profiles.js「用户可编辑」思路，但我们用独立 yaml 而非写死 config。
USER_KNOWLEDGE_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "user_knowledge.yaml",
    )
)


def load_user_knowledge() -> dict:
    """读取用户知识库文件；不存在/损坏返回空结构。键与 config 对齐：app_knowledge / games。"""
    if not os.path.isfile(USER_KNOWLEDGE_PATH):
        return {"app_knowledge": [], "games": []}
    try:
        with open(USER_KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        if not isinstance(d, dict):
            return {"app_knowledge": [], "games": []}
        return {
            "app_knowledge": list(d.get("app_knowledge") or []),
            "games": list(d.get("games") or []),
        }
    except Exception as _e:  # noqa: BLE001
        logger.warning("用户知识库读取失败，按空处理: %s", _e)
        return {"app_knowledge": [], "games": []}


def save_user_knowledge(data: dict) -> None:
    """写回用户知识库文件（仅存用户条目，不含 config 默认）。"""
    payload = {
        "app_knowledge": data.get("app_knowledge") or [],
        "games": data.get("games") or [],
    }
    os.makedirs(os.path.dirname(USER_KNOWLEDGE_PATH), exist_ok=True)
    with open(USER_KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

# 画面 OCR 文字"新鲜度"阈值（秒）：超过此值未更新，注入上下文时标注为已过时，
# 避免 AI 把很久以前的画面文字当成"当前状态"（牛头不对马嘴的根因之一）
SCREEN_TEXT_FRESH_S = 15

# 主页/功能页面词（OCR 左上角识别）：还没进对局时，游戏左上角显示当前页面名
# （仓库/交易行/邮件/商城…）。OCR 读到这些词 → 用户还在主页/菜单，不是对局中。
# 视觉摘要常把主页的"物资价值/出售通知/领取奖励"脑补成"正在搜物资"→ 误切 loot
# 场景搭"搜到好东西"（用户实锤"开局看仓库就说搜到好东西"）。页面词命中优先压住。
HOME_PAGE_WORDS = (
    "仓库", "交易行", "邮件", "商城", "主页", "首页", "大厅",
    "排行", "任务", "活动", "战备", "个人信息", "设置",
)

# 摸金玩法战备档位词（OCR 左上角识别）：进图后左上角显示档位"普通/机密/绝密"
# （用户原话："左上角写的机密，普通，绝密，就是摸金玩法"）。读到这些 → 确认在
# 对局中（烽火地带摸金玩法），视觉摘要却宣称"撤离成功"时说明是幻觉（用户"对局内
# 就说我赢了带出装备"的根因）——真撤离时 OCR 会读到"撤离成功"字样而不是档位。
# "普通/常规"虽然泛，但只在【左上角档位位置】出现才算命中（见 screen_page，中央
# 文字兜底判定不用档位词，避免"普通物品"等中央文字误判对局中）。
# 【常规（2026-08-12 用户补充）】烽火地带档位是"普通/常规/机密/绝密"四级，
# 用户玩常规档位时 OCR 只读到"常规"——不加进去，常规档位的烽火对局永远识别
# 不了对局中，击杀/队伍/场景互动全部闭嘴。
MATCH_TIER_WORDS = ("绝密", "机密", "普通", "常规")

# OCR 对局中 HUD 词（中央文字兜底）：进图后中央/下方会有撤离倒计时/撤离点/战斗
# 等 HUD 文字。与 victory 冲突时说明"还在对局中"（视觉幻觉撤离成功）。
# 不含档位词——"普通"在中央文本太泛（"普通物品/普通玩家"），只有左上角档位
# 位置的"普通"才算对局中。
MATCH_HUD_WORDS = (
    "撤离倒计时", "撤离点", "搜刮", "搜物资", "对局中", "剩", "击杀", "队友",
)
MATCH_ACTIVE_WORDS = MATCH_TIER_WORDS + MATCH_HUD_WORDS

# OCR 撤离成功确认词：真撤离时画面中心会显示大字"撤离成功"。
EXTRACT_CONFIRM_WORDS = ("撤离成功", "已撤离", "成功撤离")

# OCR 撤离失败确认词：真失败时结算横幅显示"撤离失败/撤离超时/未能撤离"。
# 与 EXTRACT_CONFIRM_WORDS 互补——缺失时结算页"返回主页/大厅"按钮会误触发
# home，视觉 defeat 被"OCR页面冲突"吞掉（实锤：撤离失败 AI 全程无互动）。
EXTRACT_FAIL_WORDS = ("撤离失败", "撤离超时", "未能撤离")

# 红鼠窝回合计数："回合 X/7"（顶部 HUD，手机共享小屏 OCR 常带空格/换行
# 如"回合 3 / 7"）。回合是红鼠窝（3v3v3 纯战斗竞技）的独有 HUD——烽火地带
# 和全面战场都没有回合概念，读到它是 100% 确定性信号（方向①：直接读状态，
# 不用视觉模型猜）。
_ROUND_X_7_RE = re.compile(r"回合\s*[1-7]\s*/\s*7")

# 确定性状态词表：**只匹配 OCR 真文字里的强状态词**，供 _read_deterministic_state
# 直接读"回合 X/7 / 撤离倒计时 / 结算横幅 / 击杀播报"等，不靠视觉模型猜。
# 这些词都是画面里真实渲染出来的 HUD 文字，OCR 扫到就是游戏状态本身。
DET_EXTRACT_COUNTDOWN = ("撤离倒计时", "剩余撤离时间", "即将撤离", "正在撤离")
DET_SETTLE_WIN = ("撤离成功", "成功撤离", "已撤离", "胜利", "获胜", "战斗胜利",
                  "我方胜利", "占领成功", "夺取成功", "防守成功")
DET_SETTLE_LOSE = ("撤离失败", "撤离超时", "失败", "战败", "被淘汰", "阵亡",
                   "战斗失败", "我方失败", "占领失败", "夺取失败", "防守失败")
DET_KILL_STRONG = ("双杀", "三杀", "四杀", "五杀", "连杀", "一血", "爆头", "mvp")
# 顶部 HUD 击杀播报句式："XX 淘汰了 XX"（底部信息区/中央播报栏），
# 只读句式不读泛词"击杀"（战绩面板统计数字会误命中）
_DET_KILL_RE = re.compile(r"(?:你|我方)?\s*(?:击杀了|淘汰了|干掉了|击败了)\s*(?:敌方|敌人|对手|干员)")


def _extract_mode_from_ocr(top: str, corner: str, center: str, bottom: str) -> str:
    """从多区域 OCR 真文字确定游戏模式（方法一核心：确定性模式读取）。

    返回 "红鼠窝" / "全面战场" / "烽火地带" / ""（无确定性结论）。
    只在 OCR 读到**画面里真实渲染的模式信号**时才返回，宁缺毋滥：
      - 回合 X/7 → 红鼠窝（唯一有回合概念的模式）
      - 32v32 / 载具大战 / ABCD点位 → 全面战场
      - 机密/绝密档位 / 撤离点 / 撤离倒计时 → 烽火地带
    注意优先级：红鼠窝 > 全面战场 > 烽火地带（回合是最强信号）。
    """
    all_text = f"{top} {corner} {center} {bottom}"
    if _ROUND_X_7_RE.search(all_text) or any(w in all_text for w in ("红鼠窝", "鼠窝")):
        return "红鼠窝"
    if any(w in all_text for w in ("32v32", "载具大战", "占点", "据点争夺", "夺取据点",
                                   "点位争夺", "ABCD", "全面战场", "大战场")):
        return "全面战场"
    # 左上角档位词：烽火地带摸金玩法左上角显示"普通/常规/机密/绝密"档位。
    # "普通"太泛（中央文字里"普通物品/普通玩家"常见），**只在左上角区域**匹配；
    # "常规/机密/绝密"较独特，合并文本里出现即可。
    if any(w in corner for w in ("普通", "常规", "机密", "绝密")):
        return "烽火地带"
    if (any(w in all_text for w in ("机密", "绝密", "常规", "撤离点", "撤离倒计时",
                                    "撤离剩余", "摸金"))):
        return "烽火地带"
    return ""


# 《三角洲行动》画面与机制速查（命中该游戏时追加进 knowledge_text，
# 让视觉裁决与话术生成"懂"画面里的模式、HUD、结算文字——治"看错模式/说错话"）
DELTA_UI_LEXICON = """
⚠️ 重要：以下内容只是给你**识别画面 / 理解对局机制**时使用的内部判定常识，
**绝对不能当成话术内容念给用户听**——"蓝红字区分敌我""人机=AI士兵""档位越高物资越值钱"
这类解释性条目说出口很怪、像念说明书。理解画面时参考它，开口说话时当它不存在，
只说你亲眼在画面里看到的东西。
画面与机制速查（识别画面时对照）：
- 模式：烽火地带=3人小队搜打撤（阵亡丢装备只保安全箱，活着撤离才是赢，撤离优先于对枪）；全面战场=4人小队32v32载具大战场（可重生、不掉装备，看增援值/据点决胜负）
- 红鼠窝（玩家口误常写"红薯窝"）：S7 赛季烽火地带下的 3v3v3 多方竞技玩法（多队争夺曼德尔单元），**属于烽火地带**，不是独立模式、更不是全面战场——看到"红鼠窝/鼠窝竞技/夺单元"等字样仍按烽火地带的规则理解
- 敌我区分（关键）：**队友名字/标记是蓝色，敌人名字/标记是红色**！看击杀播报、敌方位置提示、队伍栏时靠颜色判断敌我——蓝字的是自己人，红字的是敌人
- 烽火地带左上角显示战备档位：**普通/机密/绝密**（越高风险越大、物资越值钱）——看到"机密/绝密/普通"档位字样说明是烽火地带
- 还有一种烽火玩法：左上角不显示档位、只有撤离时间；该玩法没有队友（单排），死亡后进入倒计时等待，倒计时结束即彻底阵亡。看到"左上角只有时间、没有档位"就是这种玩法，别按3人队套路说话
- 队伍HUD在画面左下角：队友头像旁有彩色图标=存活；头像变灰或带❌/死亡标记=已阵亡；倒地未救起时是特殊"倒地"状态
- 手机屏幕共享时HUD在画面右上角
- 阵亡后进入观战等待，有3分钟复活倒计时，可等队友救援
- **观战对象（分清谁在画面中央，别写成'队友'）**：**红鼠窝（3v3v3回合竞技）玩家死后观战的是敌人**；只有烽火地带/摸金等普通模式死后才观战队友。观战画面中央玩家的击杀/倒地/阵亡是被观战者的操作——红鼠窝观战里有人倒地是敌人倒了，不是"队友倒了/队伍全灭"。"队友倒"只能看队伍HUD栏里本队头像变灰/带❌
- 结算界面字段：撤离成功/已阵亡、物资价值、击杀数、战备值
- **模式区分（识别画面时对照，别分析错）**：
  - **烽火地带（撤离玩法）**：撤离成功后的接应动画分两种——**飞机接应**（直升机悬停、玩家跑向飞机或抓绳索登机、随后飞行离场）或**汽车接应**（玩家乘车驶离）。**只有**看到"撤离成功"提示、或玩家在撤离点附近登机/上车时才是撤离动画，不是战斗；玩家抓绳索/跑向飞机=**登机撤离**，不是"用绳索打飞机"（三角洲的直升机只能用枪械/机炮击落，绳索是登机工具不是武器）
  - **全面战场（32v32载具大战场）**：**能开载具**（坦克、装甲车、直升机、快艇等），画面里的载具/直升机是正常战斗内容（玩家在驾驶、攻击或被攻击），**绝不是撤离动画**，也不代表特殊时刻
  - 不确定当前模式时，不要用撤离动画去解释载具画面
- 术语：干员=可操作角色(突击/支援/工程/侦察)，人机=AI士兵，撤离点=离开地图的位置，安全箱=死亡不掉落，曼德尔砖=高价值物资(携带会暴露位置)，优先搜索=搜高价值容器时的短暂保护
- 烽火地带没有"队伍排名输赢"，只有撤离成功与否；全面战场才有胜负结算
- 重要：三角洲行动【没有缩圈/毒圈/安全区机制】，完全不类似吃鸡游戏！没有"进圈/跑圈/跑毒"概念。"撤离点"是离开地图的撤离位置，不是"圈"；玩家待在撤离点是为了撤离，不是等缩圈
- 严禁用吃鸡类游戏概念（缩圈/毒圈/进圈/跑毒/安全区/吃鸡）来描述三角洲行动
- 烽火地带物资价值按颜色分（识别玩家收益时对照，**只能用于判断收益/评价，严禁念给用户听**）：
  - 白色/绿色=普通廉价货（占格子、拆了卖点小钱）
  - 蓝色/紫色=中等价值（有点小钱）
  - 金色=高价值（玩家口中的"金货/大金"，一条顶一堆白绿）
  - 红色=顶级稀有（玩家口中的"大红"，最高价值，出红就是大赚）
  - 判断玩家收益只看玩家自己的背包/战利品：金红多=这波肥、可以夸手气/夸收益；
    全是白绿=手气一般，可以自然调侃一句（"手气有点黑"）或鼓励（"下把赚回来"），
    但别打击、别教他该怎么搜（不说教）。没看到颜色细节就不要编造收益
  - 别把队友的战利品、交易行行情、结算界面的全服物价当玩家的收益
- 单局收益评价要克制：画面只显示"正在搜物资/整理背包"时，看不到具体颜色就别硬夸"好多好东西"；
  宁可自然寒暄一句（"这波咋样？"），也不要凭空断言收益好坏（"搜到好东西了"这种话在画面没证据时是幻觉）
"""


class GameKnowledge:
    """游戏知识库：识别当前游戏，把游戏名+玩法攻略+术语数据喂给 AI。

    识别来源：前台窗口标题（ForegroundMonitor）→ 游戏 OCR 屏幕文字（GameOcrMonitor）。
    命中后把该游戏 knowledge 文本注入对话/主动话术的 LLM 上下文。
    另维护最近一帧 OCR 画面文字与左上角页面/档位识别（screen_page），
    用真实屏幕文字校正视觉模型对主页/对局画面的脑补。
    """

    def __init__(self, cfg: Config) -> None:
        games_cfg = getattr(cfg.ns, "games", None)
        self._games: list[dict[str, Any]] = []
        if games_cfg is not None:
            for g in games_cfg:
                self._games.append({
                    "id": str(getattr(g, "id", "") or "").strip(),
                    "name": str(getattr(g, "name", "") or "").strip(),
                    "keywords": [
                        str(k).strip().lower()
                        for k in (getattr(g, "keywords", []) or [])
                        if str(k).strip()
                    ],
                    # 【2026-09-10】游戏进程名兜底：与 AppKnowledge 一致，让"进三角洲
                    # 自动识别"不必依赖 OCR —— 前台 exe 命中即算识别（去 OCR 用双工+三段式）。
                    "exe": [
                        str(k).strip().lower()
                        for k in (getattr(g, "exe", []) or [])
                        if str(k).strip()
                    ],
                    "knowledge": str(getattr(g, "knowledge", "") or "").strip(),
                    "screen": str(getattr(g, "screen", "") or "").strip(),
                    "focus": str(getattr(g, "focus", "") or "").strip(),
                    "prompt": str(getattr(g, "prompt", "") or "").strip(),
                    # 【2026-09-10】陪伴模式：duplex(云端真全双工,默认) / snapshot(云端快照)。
                    # 与 AppKnowledge 对齐 —— `_game_lock_decision` 规则②要读它决定
                    # 进游戏切"云端双工"还是"云端快照"（此前 GameKnowledge 无本字段、
                    # 也无 current_mode() → 规则②静默失效 → 游戏一律掉到白名单判快照）。
                    "mode": str(getattr(g, "mode", "") or "duplex").strip().lower() or "duplex",
                })
        # 合并用户可编辑知识库（user_knowledge.yaml）：同名 id 覆盖默认，新 id 追加
        self._merge_user_games()
        self._current: Optional[dict[str, Any]] = None
        self._last_screen_text: str = ""  # 最近一帧游戏画面 OCR 文字（供 LLM 判断当前模式/地图）
        self._last_corner_text: str = ""  # 最近一帧"屏幕左上角"OCR 文字（页面名/档位所在，screen_page 判定用）
        self._last_top_text: str = ""  # 最近一帧"顶部 HUD"OCR 文字（回合 X/7、比分、撤离倒计时所在）
        self._last_bottom_text: str = ""  # 最近一帧"底部信息区"OCR 文字（击杀播报/结算横幅所在）
        self._screen_text_ts: float = 0.0  # 最近一帧 OCR 更新时间（判断画面文字是否过时）
        self._last_anchor_state: str = ""  # 最近命中文字锚点的页面状态中文描述（锚点训练器）
        self._anchor_state_ts: float = 0.0  # 锚点状态最近命中时间（新鲜度判定）
        self._latched_mode: str = ""  # 模式锁存（右下角地图区域/档位词开匹配后隐藏时沿用）
        self._latched_ts: float = 0.0  # 最近一次锁存/清除时间
        self._last_seen_mode: str = ""  # 最近一次识别到的模式（菜单选模式时记录，进对局沿用）

    def _merge_user_games(self) -> None:
        """把 user_knowledge.yaml 的用户 game 条目合并进 self._games（同名 id 覆盖默认）。"""
        try:
            _user = load_user_knowledge().get("games") or []
            _by_id = {g["id"]: g for g in self._games}
            for u in _user:
                if not isinstance(u, dict):
                    continue
                uid = str(u.get("id", "") or "").strip()
                if not uid:
                    continue
                _by_id[uid] = {
                    "id": uid,
                    "name": str(u.get("name", "") or "").strip(),
                    "keywords": [
                        str(k).strip().lower()
                        for k in (u.get("keywords") or [])
                        if str(k).strip()
                    ],
                    # 进程名兜底（同 AppKnowledge）：前台 exe 命中即识别，无需 OCR
                    "exe": [
                        str(k).strip().lower()
                        for k in (u.get("exe") or [])
                        if str(k).strip()
                    ],
                    "knowledge": str(u.get("knowledge", "") or "").strip(),
                    "screen": str(u.get("screen", "") or "").strip(),
                    "focus": str(u.get("focus", "") or "").strip(),
                    "prompt": str(u.get("prompt", "") or "").strip(),
                    # 用户知识库里的 mode（user_knowledge.yaml 每条可写 mode: duplex/snapshot）
                    "mode": str(u.get("mode", "") or "duplex").strip().lower() or "duplex",
                }
            self._games = list(_by_id.values())
            # 合并后刷新 _current 指针，使刚改的 prompt 在下一次指令重建即生效
            if self._current is not None:
                _cid = self._current.get("id")
                for g in self._games:
                    if g.get("id") == _cid:
                        self._current = g
                        break
        except Exception:  # noqa: BLE001
            logger.debug("合并用户 games 知识库失败", exc_info=True)

    @property
    def current_game(self) -> Optional[dict[str, Any]]:
        """当前命中的游戏配置（id/name/knowledge）。"""
        return self._current

    @property
    def current_name(self) -> str:
        return self._current["name"] if self._current else ""

    def current_mode(self) -> str:
        """当前游戏的陪伴模式：duplex（云端真全双工，默认）/ snapshot（云端快照）。

        【2026-09-10 补齐】此前 **GameKnowledge 没有这个方法**（只有 AppKnowledge 有），
        而 `proactive._game_lock_decision` 的规则②会调 `self._game_knowledge.current_mode()`
        → AttributeError 被 `except Exception: pass` 静默吞掉 → 规则②「按知识库陪伴模式判
        快照/双工」**从未生效**，进游戏一律掉到规则③白名单 → 三角洲被判成**云端快照**
        （弹窗卡在"正在切换"、也不注入 game: 三段式）。补齐后与软件侧语义一致。
        """
        if not self._current:
            return ""
        return str(self._current.get("mode", "duplex") or "duplex").strip().lower() or "duplex"

    def correct_summary(self, summary: str) -> str:
        """用 OCR 识别的真实游戏名校正视觉摘要中的游戏名幻觉。

        视觉摘要偶尔把当前游戏看错（如把三角洲行动看成暗区突围/守望先锋），
        而 OCR 文字识别（CN UID 等特征）更可靠。带《》的游戏名与真实游戏名
        不一致时替换为真实名；不带书名号的已知游戏名（配置 games 段）同样替换，
        避免"看错游戏"后拿错游戏设定搭话。
        """
        name = self.current_name
        if not name or not summary:
            return summary
        for m in re.finditer(r"《([^》]+)》", summary):
            guess = m.group(1)
            if guess and guess != name:
                summary = summary.replace(m.group(0), f"《{name}》")
        # 不带书名号的已知游戏名（如"在玩守望先锋"）——配置 games 段里的名字
        for other in {g["name"] for g in self._games if g["name"] and g["name"] != name}:
            if other in summary:
                summary = summary.replace(other, name)
        return summary

    @property
    def screen_text(self) -> str:
        """最近一帧游戏画面 OCR 文字（可能不完整/有噪声，不代表完整状态）。"""
        return self._last_screen_text

    def update_screen_text(self, text: str, corner_text: str = "",
                           top_text: str = "", bottom_text: str = "") -> None:
        """记录最近游戏画面 OCR 文字（GameOcrMonitor 每帧更新）。

        corner_text：屏幕左上角区域的 OCR 文字（页面名"仓库/交易行/邮件"与
        档位"普通/机密/绝密"所在）。手机屏幕共享时主抓屏只裁剪中央区域，
        左上角单独 OCR；PC 版整窗抓取时左上角已含在 text 里，corner_text 传空。

        top_text：屏幕顶部 HUD 区域（回合 X/7、比分、撤离倒计时等所在）。
        bottom_text：屏幕底部信息区（击杀播报/结算横幅等所在）。
        （方向①：OCR 多区域铺满——回合数字/倒计时等关键状态常不在中央和
        左上角，单独铺到顶部/底部区域读，比只靠视觉模型猜更接近游戏真相。）

        供对话/主动话术注入：让 AI 看到用户当前在哪个模式/地图（看字不猜）。
        空文本 = 当前这一帧画面确认没有文字（加载/黑屏/纯 3D 画面），
        此时清空旧内容——避免"旧画面快照"残留被当成当前状态。
        """
        text = (text or "").strip()
        self._screen_text_ts = time.time()
        self._last_screen_text = text[:200]
        self._last_corner_text = (corner_text or "").strip()[:100]
        self._last_top_text = (top_text or "").strip()[:100]
        self._last_bottom_text = (bottom_text or "").strip()[:100]
        # 【模式锁存（2026-08-12 用户规则）】右下角地图区域/左上角档位文字在
        # 开匹配后会隐藏（模式词消失），但模式判定要延续到撤离结算或退回主页：
        # "只要不出现新的地图名字就保存最新一次地图更新作为判定"。每帧从多区域
        # OCR 文字提取模式：读到模式词 → 锁存；左上角出现主页页面名（仓库/交易行/
        # 大厅…）→ 用户退回主页，清空锁存（下一局重新判定）。锁存只认本局判定，
        # 防对局中模式词隐藏后模式退化为空（击杀/队伍/场景互动全部跟着闭嘴）。
        # "常规"档位：用户玩常规档位烽火时只有这个词，也必须锁成烽火地带。
        _frame_mode = _extract_mode_from_ocr(
            top_text or "", corner_text or "", text, bottom_text or "")
        if _frame_mode:
            if _frame_mode != self._latched_mode:
                logger.info("模式锁存: %s（OCR帧文字）", _frame_mode)
            self._latched_mode = _frame_mode
            self._latched_ts = time.time()
            # 记录最近一次识别到的模式：用户在主页菜单选模式时 OCR 能读到模式名
            # （04:40-04:42 日志：红鼠窝/全面战场/烽火地带都锁存过），但进对局后
            # 模式词被 UI 隐藏/落在 OCR 区域外 → 对局中沿用这个值，别再丢成空。
            self._last_seen_mode = _frame_mode
        elif any(w in (corner_text or "") for w in HOME_PAGE_WORDS):
            if self._latched_mode:
                logger.info("模式锁存清除: %s → 退回主页", self._latched_mode)
            self._latched_mode = ""
            self._latched_ts = 0.0

    @property
    def screen_texts(self) -> dict:
        """多区域 OCR 文字快照（中心/左上角/顶部/底部），供确定性状态读取。"""
        return {
            "center": self._last_screen_text,
            "corner": self._last_corner_text,
            "top": self._last_top_text,
            "bottom": self._last_bottom_text,
        }

    def update_anchor_state(self, state: str, ts: Optional[float] = None) -> None:
        """记录最近命中的文字锚点状态（区域文字锚点训练器产出）。

        state 为锚点规则的中文描述（如"正在撤离倒计时"），OCR 扫描到区域
        文字命中关键词时由 GameOcrMonitor 调用。这个状态是**画面里真实渲染
        的文字**得出的强判定，比视觉模型猜当前页面可靠——对话/主动话术注入
        时"看字不猜"，视觉摘要对当前页面的猜测作废。
        """
        self._last_anchor_state = (state or "").strip()
        self._anchor_state_ts = ts or time.time()
        if self._last_anchor_state:
            logger.debug("文字锚点状态更新: %s", self._last_anchor_state)

    def anchor_state(self) -> str:
        """最近命中锚点的中文描述（页面状态），超时未命中返回空串。

        锚点文字也会随时间消失（撤离倒计时结束后"撤离成功"字样切换），
        用 SCREEN_TEXT_FRESH_S 窗口保证只认"当前真的还在页面上"的状态。
        """
        if not self._anchor_state_ts:
            return ""
        if time.time() - self._anchor_state_ts > SCREEN_TEXT_FRESH_S:
            return ""
        return self._last_anchor_state

    def screen_text_age(self) -> Optional[float]:
        """返回画面文字距今秒数；从未更新过返回 None。"""
        if not self._screen_text_ts:
            return None
        return time.time() - self._screen_text_ts

    def screen_page(self) -> str:
        """根据最近 OCR 画面文字识别当前所处页面/状态。

        返回：
          - "home"：主页/仓库/交易行/邮件等菜单页（左上角页面名）——还没进对局，
            视觉摘要里的"搜物资/撤离成功"不可信（那是主页界面脑补）。
          - "match"：对局中（左上角档位"普通/机密/绝密"、撤离倒计时、战斗 HUD）——真对局。
          - "confirm_extract"：OCR 读到"撤离成功/已撤离"——真撤离，可放心庆祝。
          - "confirm_defeat"：OCR 读到"撤离失败/撤离超时/未能撤离"——真失败结算。
          - ""：OCR 文字不可用/过时（未识别游戏 / 长时间没扫到文字）。

        左上角文字优先：页面名（仓库/交易行/邮件…）与档位（普通/机密/绝密）都
        显示在屏幕左上角（用户原话："没进游戏就主页看仓库交易行会显示当前页面叫啥，
        左上角写的机密普通绝密就是摸金玩法"）。手机共享时主 OCR 只扫中央，左上角
        由 GameOcrMonitor 单独识别；PC 版整窗 OCR 时左上角已含在中央文字里。
        撤离成功确认字优先于一切：真撤离时画面中央会显示"撤离成功"大字，此时
        左上角可能还残留档位（机密/绝密）或页面名，仍以中央大字为准。
        左上角无命中时再拿中央 HUD 文字兜底（对局中的撤离倒计时/撤离点/击杀等）。
        """
        age = self.screen_text_age()
        if age is None or age > SCREEN_TEXT_FRESH_S * 2:
            return ""
        c = (self._last_corner_text or "").strip()
        t = (self._last_screen_text or "").strip()
        top = (self._last_top_text or "").strip()
        bot = (self._last_bottom_text or "").strip()
        # 真撤离：画面中央"撤离成功/已撤离"大字（OCR 读到的是真实屏幕文字，
        # 不是视觉脑补），左上角此时可能还残留档位/页面名，撤离确认优先。
        if any(w in c for w in EXTRACT_CONFIRM_WORDS) or any(
                w in t for w in EXTRACT_CONFIRM_WORDS) or any(
                w in top for w in EXTRACT_CONFIRM_WORDS) or any(
                w in bot for w in EXTRACT_CONFIRM_WORDS):
            return "confirm_extract"
        # 真撤离失败：底部/中央 OCR 读到"撤离失败/撤离超时/未能撤离"——结算画面
        # 的失败横幅。此时左上角/底部可能残留"返回主页/大厅"按钮文字，先于 home
        # 判定（否则视觉 defeat 被"OCR页面冲突-仍在主页"吞掉，AI 不互动）。
        if any(w in bot for w in EXTRACT_FAIL_WORDS) or any(
                w in t for w in EXTRACT_FAIL_WORDS) or any(
                w in top for w in EXTRACT_FAIL_WORDS):
            return "confirm_defeat"
        # 左上角页面名/档位：主页菜单页 / 摸金玩法档位都在左上角。
        # 红鼠窝（3v3v3 回合制）对局中左上角显示"回合 X/7"（无档位词）——
        # 顶部 HUD 里"回合 X/7"只在红鼠窝出现，同样是确定性对局中信号。
        # 实测（2026-08-12）：左上角读到"回合4/7 A"，若这里不识别，红鼠窝
        # 对局中 screen_page 返回 ""，视觉"持有武器并获胜"脑补会绕过
        # "victory OCR页面冲突-仍在对局中"守卫，误报胜利。
        if c:
            if any(w in c for w in MATCH_TIER_WORDS) or _ROUND_X_7_RE.search(c):
                return "match"
            if any(w in c for w in HOME_PAGE_WORDS):
                return "home"
        # 顶部 HUD 回合计数（红鼠窝 "回合 X/7"）/ 撤离倒计时 → 对局中
        if top:
            if (any(w in top for w in MATCH_HUD_WORDS)
                    or _ROUND_X_7_RE.search(top)):
                return "match"
            if any(w in top for w in HOME_PAGE_WORDS):
                return "home"
        # 底部信息区（击杀播报/结算横幅）→ 对局中
        if bot:
            if any(w in bot for w in MATCH_HUD_WORDS):
                return "match"
        # 中央 HUD 文字兜底（PC 版整窗 OCR / 手机共享左上角无命中时）。
        if t:
            if any(w in t for w in MATCH_HUD_WORDS):
                return "match"
            if any(w in t for w in HOME_PAGE_WORDS):
                return "home"
        return ""

    def deterministic_state(self) -> dict:
        """确定性状态读取（方向①核心）：直接读 OCR 多区域真文字，不靠视觉模型。

        游戏 HUD 是**真实渲染的文字**（回合 X/7、撤离倒计时、结算横幅、击杀
        播报），OCR 扫到就是"游戏状态本身"，比视觉模型在模糊小屏上"猜"可靠得多。
        返回：
          - mode: "红鼠窝"/"全面战场"/"烽火地带"/""（多区域 OCR 确定性信号）
          - extracting: 是否在撤离倒计时（"即将撤离 + 时间倒计时"才算想撤离）
          - settle_win / settle_lose: 结算横幅真文字（OCR 读到就是真结算）
          - kill: 击杀播报真文字（强句式，玩家本人击杀）
        """
        texts = self.screen_texts
        center = texts["center"]
        corner = texts["corner"]
        top = texts["top"]
        bottom = texts["bottom"]
        st = {"mode": "", "extracting": False,
              "settle_win": False, "settle_lose": False, "kill": False}
        st["mode"] = _extract_mode_from_ocr(top, corner, center, bottom)
        if not st["mode"] and self._latched_mode:
            # 模式词（档位/地图名）开匹配后隐藏 → 沿用本局锁存判定
            st["mode"] = self._latched_mode
        if not st["mode"] and self.screen_page() == "match":
            # 对局中但模式词落在 OCR 区域外/被 UI 隐藏时，沿用用户在主页菜单
            # 选的模式（最后一次识别到的模式）——治"进对局模式丢成空、AI 不知道
            # 在哪个模式"（用户实锤：普通档位烽火模式识别不出）。
            st["mode"] = self._last_seen_mode
        all_text = f"{top} {corner} {center} {bottom}"
        if any(w in all_text for w in DET_EXTRACT_COUNTDOWN):
            st["extracting"] = True
        if any(w in all_text for w in DET_SETTLE_WIN):
            st["settle_win"] = True
        if any(w in all_text for w in DET_SETTLE_LOSE):
            st["settle_lose"] = True
        if (_DET_KILL_RE.search(all_text)
                or any(w.lower() in all_text.lower() for w in DET_KILL_STRONG)):
            st["kill"] = True
        return st

    def latched_mode(self) -> str:
        """当前生效的模式判定（OCR 实时信号 + 本局锁存兜底）。

        供队伍 HUD（预期人数=全面战场4/烽火3）、场景模板（红鼠窝专用话术）等
        使用——模式词开匹配后隐藏时，lock 住的判定仍有效，直到退回主页清除。
        """
        return self.deterministic_state().get("mode") or ""

    def verify_vision_mode(self, vision_mode: str) -> str:
        """知识库校验层（方向三核心）：用 OCR 确定性模式校验/纠正视觉枚举模式。

        视觉模型在手机共享小屏上常把红鼠窝说成"烽火地带"（摘要总写"撤离玩法"），
        或把烽火说成"全面战场"。OCR 读到回合 X/7 / 档位等真实 HUD 文字时，
        OCR 结论优先于视觉枚举——OCR 是画面里真实渲染的字，视觉是模型猜的。
        返回纠正后的模式；OCR 无确定性信号时原样返回视觉结论。
        """
        ocr_mode = self.deterministic_state().get("mode") or ""
        if ocr_mode and ocr_mode != vision_mode:
            logger.info("知识库校验: 视觉模式 %s 被 OCR 纠正为 %s（OCR真文字优先）",
                        vision_mode, ocr_mode)
            return ocr_mode
        return vision_mode

    @property
    def knowledge_text(self) -> str:
        """给 LLM 的游戏情报文本；未识别到游戏时返回空串（不注入）。"""
        if not self._current:
            return ""
        parts = [f"当前游戏：{self._current['name']}"]
        if self._current.get("screen"):
            parts.append("你在这款游戏里通常会看到的画面（按这些去理解屏幕，别凭空编）："
                         + self._current["screen"])
        if self._current.get("focus"):
            parts.append("达妮娅该留意/点评什么（聚焦这些，别跑题）："
                         + self._current["focus"])
        if self._current["knowledge"]:
            parts.append(self._current["knowledge"])
        # 三角洲命中时追加画面/机制速查，让 AI 说话贴合真实对局
        if self._current["name"] == "三角洲行动":
            parts.append(DELTA_UI_LEXICON.strip())
        return "\n".join(parts)

    def detect(self, text: str, exe: str = "") -> bool:
        """用窗口标题 / 屏幕文字 / 前台进程名匹配游戏。命中或切换时更新当前游戏。

        返回是否命中（用于日志）。匹配到不同游戏会自动切换。
        【2026-09-10】新增 exe 兜底：前台**进程名**命中即算识别，使"进三角洲自动
        注入三段式"不再依赖 OCR（去 OCR 走"双工+三段式"的关键接线）。签名对旧调用
        方（game_ocr 的 detect(title)/detect(text)）完全向后兼容。
        """
        t = (text or "").lower()
        _exe = (exe or "").lower().strip()
        if not t and not _exe:
            return False
        cur_id = self._current["id"] if self._current else ""
        for g in self._games:
            _kw = g.get("keywords") or []
            _exe_list = g.get("exe") or []
            _by_title = bool(_kw) and bool(t) and any(kw in t for kw in _kw)
            _by_exe = bool(_exe) and _exe in _exe_list
            if _by_title or _by_exe:
                if g["id"] != cur_id:
                    self._current = g
                    logger.info("游戏识别: %s (文字: %s / 进程: %s / via=%s)",
                                g["name"], (text or "").strip()[:40], _exe,
                                "exe" if _by_exe and not _by_title else "title")
                return True
        return False

    def force_game(self, name: str) -> None:
        """视频测试模式：强制锁定当前游戏上下文，绕过实时屏幕识别。

        视频测试重放的是某款游戏的录像，但实时前台监听（foreground/squad）
        会读到用户当下在玩的其他游戏/软件，把 current_name/页面状态带偏，
        导致胜负门禁拿实时屏当判据、误杀录像里的真时刻。测试模式下直接把
        game_knowledge 钉死为录像对应的游戏，使所有门禁都基于视频 OCR 文字。
        """
        for g in self._games:
            if g.get("name") == name:
                self._current = g
                logger.info("视频测试：强制锁定游戏上下文=%s", name)
                return
        # 配置里没有该游戏时退回最小字典，保证 current_name 非空
        self._current = {"id": "forced", "name": name, "knowledge": "", "keywords": []}
        logger.info("视频测试：强制锁定游戏上下文（配置缺失）=%s", name)

    def reset(self) -> None:
        """离开游戏时清除当前游戏上下文（AI 不再按该游戏说话）。"""
        if self._current is not None:
            logger.info("游戏上下文清除（离开游戏）: %s", self._current["name"])
            self._current = None
        self._last_screen_text = ""
        self._last_corner_text = ""
        self._last_top_text = ""
        self._last_bottom_text = ""
        self._screen_text_ts = 0.0
        self._last_anchor_state = ""
        self._anchor_state_ts = 0.0


class AppKnowledge:
    """软件知识库：识别用户前台使用的软件，把该软件的说明/陪聊语境喂给 AI。

    与 GameKnowledge 互补：GameKnowledge 按"窗口标题/屏幕文字/视觉摘要"命中游戏，
    注入游戏机制知识；AppKnowledge 按"前台窗口标题关键词"命中非游戏软件（IDE/
    剪辑/建模等），注入该软件的使用语境，让 AI 在用户用某个软件时说话更贴。
    只在非游戏前台检测；游戏前台（用户在打游戏）时软件知识不注入、直接卸载。

    配置示例（config.yaml）：
    ```yaml
    app_knowledge:
      - id: trae_code
        name: TraeCode（AI编程IDE）
        keywords: [trae, trae code]   # 窗口标题出现任意一个即命中
        knowledge: |
          该软件的使用说明/陪聊分寸（AI 会按这些说话）
    ```
    """

    def __init__(self, cfg: Config) -> None:
        apps_cfg = getattr(cfg.ns, "app_knowledge", None)
        self._apps: list[dict[str, Any]] = []
        if apps_cfg is not None:
            for a in apps_cfg:
                self._apps.append({
                    "id": str(getattr(a, "id", "") or "").strip(),
                    "name": str(getattr(a, "name", "") or "").strip(),
                    "keywords": [
                        str(k).strip().lower()
                        for k in (getattr(a, "keywords", []) or [])
                        if str(k).strip()
                    ],
                    "exe": [
                        str(k).strip().lower()
                        for k in (getattr(a, "exe", []) or [])
                        if str(k).strip()
                    ],
                    "knowledge": str(getattr(a, "knowledge", "") or "").strip(),
                    "screen": str(getattr(a, "screen", "") or "").strip(),
                    "focus": str(getattr(a, "focus", "") or "").strip(),
                    "prompt": str(getattr(a, "prompt", "") or "").strip(),
                    # 【2026-09-06 软件陪伴模式】mode=duplex 走真全双工（默认，
                    # 和以前一样）；mode=snapshot 走"快照+OCR"事件驱动（仿三角洲
                    # 游戏方案：只响应 OCR 确认的区块文字，不靠模型自由发挥）。
                    "mode": str(getattr(a, "mode", "") or "duplex").strip().lower() or "duplex",
                    # 是否高性能（游戏/3D/渲染 → GPU 保护、云端切走）；知识库编辑框设置，
                    # 镜像进 software_profiles.tier 供游戏锁按 exe 读取。
                    "high_performance": str(getattr(a, "high_performance", "")).strip().lower() in ("true", "1", "yes", "y"),
                    # 识别区块：用户自定义的截图区域 → 文字 → 触发（因果）。
                    # 每项 {name, rect:[x,y,w,h](0~1归一化), keywords:[...], trigger}
                    # trigger 即 moment 类型（如 done/error/build/complete/自定义），
                    # 命中关键词即触发对应陪伴话术。仅在 mode=snapshot 时启用。
                    "ocr_regions": list(getattr(a, "ocr_regions", []) or []),
                })
        # 合并用户可编辑知识库（user_knowledge.yaml）：同名 id 覆盖默认，新 id 追加
        self._merge_user_apps()
        self._current: Optional[dict[str, Any]] = None

    def _merge_user_apps(self) -> None:
        """把 user_knowledge.yaml 的用户 app 条目合并进 self._apps（同名 id 覆盖默认）。"""
        try:
            _user = load_user_knowledge().get("app_knowledge") or []
            _by_id = {a["id"]: a for a in self._apps}
            for u in _user:
                if not isinstance(u, dict):
                    continue
                uid = str(u.get("id", "") or "").strip()
                if not uid:
                    continue
                _by_id[uid] = {
                    "id": uid,
                    "name": str(u.get("name", "") or "").strip(),
                    "keywords": [
                        str(k).strip().lower()
                        for k in (u.get("keywords") or [])
                        if str(k).strip()
                    ],
                    "exe": [
                        str(k).strip().lower()
                        for k in (u.get("exe") or [])
                        if str(k).strip()
                    ],
                    "knowledge": str(u.get("knowledge", "") or "").strip(),
                    "screen": str(u.get("screen", "") or "").strip(),
                    "focus": str(u.get("focus", "") or "").strip(),
                    "prompt": str(u.get("prompt", "") or "").strip(),
                    "mode": str(u.get("mode", "") or "duplex").strip().lower() or "duplex",
                    "high_performance": bool(u.get("high_performance")),
                    "ocr_regions": list(u.get("ocr_regions", []) or []),
                }
            self._apps = list(_by_id.values())
            # 合并后刷新 _current 指针，使刚改的 prompt 在下一次指令重建即生效
            if self._current is not None:
                _cid = self._current.get("id")
                for a in self._apps:
                    if a.get("id") == _cid:
                        self._current = a
                        break
        except Exception:  # noqa: BLE001
            logger.debug("合并用户 app 知识库失败", exc_info=True)

    @property
    def current_name(self) -> str:
        return self._current["name"] if self._current else ""

    @property
    def current_mode(self) -> str:
        """软件陪伴模式：duplex（真全双工，默认）/ snapshot（快照+OCR 事件驱动）。"""
        if not self._current:
            return ""
        return str(self._current.get("mode", "duplex") or "duplex").strip().lower() or "duplex"

    @property
    def current_ocr_regions(self) -> list:
        """当前软件的 OCR 识别区块配置（仅 mode=snapshot 时生效）。"""
        if not self._current:
            return []
        return list(self._current.get("ocr_regions", []) or [])

    @property
    def knowledge_text(self) -> str:
        """给 LLM 的软件情报文本；未识别到软件时返回空串（不注入）。"""
        if not self._current:
            return ""
        parts = [f"当前软件：{self._current['name']}"]
        # 【快照+OCR 模式提示】让模型知道它处在"事件驱动"陪伴下，会按识别区块
        # 文字主动开口，不必每帧自由发挥（与三角洲游戏的快照方案同构）。
        if str(self._current.get("mode", "duplex") or "duplex").strip().lower() == "snapshot":
            parts.append("陪伴模式：快照+OCR（我会按你配置的「识别区块」里出现的文字主动开口，"
                         "别凭空编造画面外的事）")
        if self._current.get("screen"):
            parts.append("你在这款软件里通常会看到的画面（按这些去理解屏幕，别凭空编）："
                         + self._current["screen"])
        if self._current.get("focus"):
            parts.append("达妮娅该留意/点评什么（聚焦这些，别跑题）："
                         + self._current["focus"])
        if self._current["knowledge"]:
            parts.append(self._current["knowledge"])
        return "\n".join(parts)

    def detect(self, title: str, exe: str = "") -> bool:
        """用前台窗口标题匹配软件（也支持按进程名 exe 兜底命中）。命中或切换时更新当前软件。

        返回是否命中（用于日志）。匹配到不同软件会自动切换。
        【2026-09-06 修复·抖音 App 标题不含"抖音"】抖音 Windows 客户端(douyin.exe)窗口标题
        往往只是视频名、不含"抖音"二字（知识库关键词按"视频标题 - 抖音 - Chrome"写的，
        那是浏览器网页版）。纯标题关键词匹配会漏掉 App 版 → 抖音全屏陪伴/重连永不触发。
        故新增 exe 兜底：配置里给 app 配 exe 列表（如 douyin.exe），运行时前台进程命中即算
        命中，哪怕标题里没有关键词。
        """
        t = (title or "").lower()
        _exe = (exe or "").lower().strip()
        if not t and not _exe:
            return False
        cur_id = self._current["id"] if self._current else ""
        for a in self._apps:
            _kw = a.get("keywords") or []
            _exe_list = a.get("exe") or []
            _by_title = bool(_kw) and any(kw in t for kw in _kw)
            _by_exe = bool(_exe) and _exe in _exe_list
            if _by_title or _by_exe:
                if a["id"] != cur_id:
                    self._current = a
                    logger.info("软件识别: %s (窗口标题: %s / 进程: %s / via=%s)",
                                a["name"], title.strip()[:40], _exe,
                                "exe" if _by_exe and not _by_title else "title")
                return True
        return False

    def reset(self) -> None:
        """离开该软件时清除当前软件上下文（AI 不再按该软件说话）。"""
        if self._current is not None:
            logger.info("软件上下文清除（离开）: %s", self._current["name"])
            self._current = None

