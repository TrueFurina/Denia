"""游戏 OCR 识别（v2 证据门架构）：本地 RapidOCR 高频扫描屏幕文字。

【v2 重做核心】击杀判定不再靠"多模态看截图猜归属"，而是两条硬证据：
1. **输入证据门**（InputSensor，pynput 钩子）：玩家最近窗口内有没有开枪
   （鼠标左键连点/战斗按键）。没开枪 → 击杀播报区出现任何文字都静默——
   根治"没打人却说打死人了"（旧方案纯靠 OCR 玩家名位置猜归属，无战斗证据）。
2. **OCR 精确匹配**：玩家名作为"击杀者/被杀者"的精确正则（不做左右位置
   猜归属，多条播报叠加时位置不可靠）。

判定层级：
- kill：玩家名+击杀动词精确命中 【且】 证据门通过（开了枪）→ 才报
- died：阵亡名片区域 / "你被击杀阵亡" / 玩家名作为被杀者 / 观战转入
  → 纯 OCR 确定性，不依赖证据门（被打死不需要先开枪）
- victory/defeat：结算横幅精确文字 + "胜负未达成"排除
- 任何一层无法确认 → 静默（宁可少互动，不要出错出丑）

视觉模型（VisionAnalyzer/RealtimeVision）不再参与时刻裁决，只做画面描述
感知（moment 恒为 None），见各视觉模块。

仅当前台窗口是游戏（ForegroundMonitor.current_hint == "game"）时才扫描，
避免在 IDE/浏览器里误识别普通文字。
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import time
from types import SimpleNamespace
from typing import Any, Optional

from PIL import Image

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from .foreground_monitor import ForegroundMonitor
from .gpu_load import gpu_load
from .input_sensor import InputSensor
from .privacy import privacy_mode
from ..companion.game_knowledge import HOME_PAGE_WORDS

logger = logging.getLogger(__name__)

# 关键词 → moment 类型（按优先级匹配，先匹配到先命中）
# 这些词只作"疑似"引信：命中后把 OCR 文字 + 截图交给多模态一次裁决，
# 由 AI 判断是不是关键时刻、具体什么类型——宁可扩大疑似，让 AI 看画面定夺，
# 也不要用关键词直接下结论（防误判/防漏判）。
KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    # 胜利：结算/吃鸡/第一名等
    ("victory", (
        "胜利", "获胜", "吃鸡", "大吉大利", "第一名", "恭喜通关", "通关成功",
        "撤离成功",  # 三角洲烽火地带：活着撤离才是赢
        # 英文只留完整词，避免子串误命中普通文本（实锤：用户在 IDE 看代码，
        # 裸 "win" 命中代码/窗口标题里的 "window"、"win32" → 误报"游戏胜利"）
        "victory", "champion", "winner", "battle won",
    )),
    # 失败：战败/淘汰出局/被击败/撤离失败等
    ("defeat", (
        "失败", "战败", "落败", "惜败", "被击败", "被淘汰", "淘汰出局", "游戏结束",
        "撤离失败", "撤离超时", "未能撤离", "未撤离",  # 三角洲烽火地带：没撤出去=失败
        "defeat", "lose", "lost", "game over", "you died", "second place",
    )),
    # 被击杀/倒地待救：我方/玩家阵亡或倒地（"被…击杀" 中间可能夹敌人名，用正则处理）。
    # 注意：队友阵亡/队友倒地【不】归这里——那属于 teammate_down，由 SquadMonitor
    # 看队伍HUD触发；若队友倒下也判 died 会错成"安慰用户阵亡"，且与 squad 重复刷屏
    ("died", (
        "你被击杀", "你阵亡", "你死了", "阵亡",
        "你被干掉", "你被击倒", "被击倒", "你被击败", "倒下", "你被淘汰",
        "you were killed", "you have been eliminated", "killed you",
        # 三角洲行动里玩家阵亡后才进入观战队友/敌人视角，观战画面=已阵亡
        "观战",
        # 注：「等待救援/救援中/等待救助」已移到 DOWNED_KEYWORDS（倒地待救，
        # 非阵亡），单独成 "downed" 时刻，见 _scan_once 的倒地检测通道。
    )),
    # 击杀敌人：连杀/爆头/高光等明确"玩家本人"的词条，
    # 击杀播报/玩家句式的匹配走 KILL_PATTERNS（见下）
    ("kill", (
        "double kill", "triple kill", "quadra kill", "penta kill", "first blood",
        "双杀", "三杀", "四杀", "五杀", "六杀", "七杀", "连杀", "一血",
        "爆头", "headshot", "神枪手", "mvp", "首杀", "枪王", "四连击",
    )),
]

# 被击杀正则：OCR 文本里"被"与"击杀/淘汰/击倒/干掉/击败"之间常夹敌人 ID（可能带空格），
# 因此中间允许任意非句末标点字符（含空格），避免带空格时匹配失败被误判成击杀
# 另覆盖击杀播报格式（无"被"字）：如"疾风 淘汰了 你"、"敌人把你淘汰了"——同样代表玩家阵亡
DIED_PATTERNS = (
    re.compile(r"被[^。！？]{0,12}(?:击杀|淘汰|击倒|干掉|击败)"),
    re.compile(r"被[^。！？]{0,12}(?:击败|击毙)"),
    re.compile(r"(?:淘汰|击杀|干掉|击败|击倒)(?:了|掉)?\s*你\b"),
    re.compile(r"把[^。！？]{0,8}(?:淘汰|击杀|干掉|击败|击倒)"),
    # "暮蝶 将你击败"（瓦罗兰特等中文FPS死亡格式："将你"在前、动词在后）。
    # 现有规则只认"你被X/击败你/把你"，这个格式全漏 → 玩家阵亡无互动（实锤）。
    re.compile(r"将\s*你[^。！？]{0,6}(?:击杀|淘汰|击败|击倒|干掉|击毙)"),
)

# 明确"玩家本人阵亡"句式（不带玩家名也能判定，如"你被疾风淘汰了"）。
# 只认"你"为主角/宾语的被动态，**不认"疾风被淘汰"**——击杀播报里队友/敌人
# 被淘汰也可能写成"X 被淘汰"，v2 无视觉复查时宁可静默，不把别人阵亡当成
# 用户阵亡去安慰（"你被疾风淘汰了"这种才是玩家本人）。
_SELF_DIED_PATTERNS = (
    # 你被...击杀/淘汰（中间可夹敌人名，如"你被疾风淘汰了"）
    re.compile(r"你\s*被[^。！？]{0,12}(?:击杀|淘汰|击倒|干掉|击败|击毙)"),
    # 被...击杀/淘汰 之后紧跟"你"（覆盖"被疾风击杀的你"等少见句式）
    re.compile(r"被[^。！？]{0,12}(?:击杀|淘汰|击倒|干掉|击败|击毙)[^。！？]{0,3}你"),
    # 击杀动词+你（"疾风 淘汰了 你"）
    re.compile(r"(?:淘汰|击杀|干掉|击败|击倒)(?:了|掉)?\s*你"),
    # 把你...（"敌人把你淘汰了"）
    re.compile(r"把\s*你[^。！？]{0,8}(?:淘汰|击杀|干掉|击败|击倒)"),
    # 将你...（"暮蝶 将你击败"，瓦罗兰特中文死亡格式；与 DIED_PATTERNS 同步）
    re.compile(r"将\s*你[^。！？]{0,6}(?:击杀|淘汰|击败|击倒|干掉|击毙)"),
)

# 击杀引信（用户明确：出现击杀就把击杀部分截图丢给 AI，让 AI 确定是玩家杀的
# 还是队友/敌人杀的，不靠 OCR 文字猜）：
#  - 明确指向玩家本人的击杀文字（"你/我方击杀了""双杀/爆头"）→ kill 引信
#  - 击杀播报栏格式"XXX 淘汰了 YYY"（击杀者可能是玩家/队友/敌人，OCR 文字
#    无法确认）→ 也作为 kill 疑似引信，交给多模态 AI 看截图分辨
#    （红蓝名/玩家名字条目），AI 确认是玩家本人击杀才报 kill。
# 泛词"击杀/淘汰/击倒"单独出现（战绩面板统计/武器说明/他人播报条目）仍不做引信。
KILL_PATTERNS = (
    re.compile(r"(?:你|我方)\s*(?:击杀了|淘汰了|干掉了|击败了|击毙了|拿下了)"),
    re.compile(r"击杀(?:敌方|敌人|对手|干员|成功(?!率))"),
    re.compile(r"(?:淘汰|击杀|击败|击倒|干掉)(?:了|掉)?\s*(?:敌方|敌人|对手|干员)"),
    # 击杀播报栏格式："疾风 淘汰了 张三"。是否玩家本人由多模态 AI 看截图判断。
    re.compile(r"[\u4e00-\u9fa5A-Za-z0-9_]{2,16}\s*(?:淘汰|击杀|干掉|击败)了"),
)

# 明确指向玩家本人的击杀提示（居中反馈"你击杀了/双杀/爆头"等）：这些不依赖
# 底部击杀播报栏，播报变化检测不拦截（玩家本人击杀提示居中弹出，不一定在底部）。
_SELF_KILL_RE = re.compile(
    r"(?:你|我方|你方)\s*(?:击杀了|淘汰了|干掉了|击败了|击毙了|拿下了)"
    r"|(?:双杀|三杀|四杀|五杀|六杀|七杀|连杀|一血|爆头|首杀)"
)

# 击杀确认提示（kill_hint 区域）引信：本人击杀时屏幕中间下方弹出
# "击败 某某某兵 阿萨拉/哈夫克"。只认主动"击败 XX"句式；被动（"你被击败/
# 被 XXX 击败"）是阵亡提示，由 died 链路处理，这里排除。名字允许 OCR 在
# 字间插空格（"阿萨拉 突击兵"），要求至少 2 个字。
# 【不含"淘汰"】"XX 淘汰了 YY"是右上角击杀播报格式（队友/敌人互杀也滚动），
# 不是"本人击杀"的确认提示；kill_hint 区域若扫到播报文字不得误判为本人击杀。
_KILL_HINT_KILL_RE = re.compile(
    r"(?:击败|击倒|击杀|消灭|击毙)\s*(?:了\s*)?"
    r"[\u4e00-\u9fa5A-Za-z0-9_·](?:\s*[\u4e00-\u9fa5A-Za-z0-9_·]){1,14}"
)
# 被动/被击败句式：出现即排除（不是本人击杀，是阵亡提示）
_KILL_HINT_PASSIVE_RE = re.compile(
    r"你\s*被|被\s*[^。！？]{0,12}(?:击败|击倒|击杀|消灭|击毙|淘汰)"
)

# 击杀/被击杀描述模板（OCR 原文可能碎片化，匹配后生成一句干净的描述）
MOMENT_LABEL = {
    "victory": "游戏胜利",
    "defeat": "游戏失败",
    "kill": "你在游戏里击杀了敌人",
    "kill_bot": "你在游戏里击杀了人机(AI)",
    "headshot": "你在游戏里爆头击杀了敌人",
    "died": "你在游戏里被击杀/阵亡了",
    "downed": "你在游戏里倒地了（等待救援）",
}

# 倒地（等待队友救援）关键词：玩家倒地后屏幕中央/下方出现「等待救援/救援中/
# 正在救援/需要救援/队友救援」横幅，是「本人倒地」的确定性信号（与阵亡不同——
# 倒地还能被救起，不能说"你死了"）。这些词从"died"规则里移出，单独成
# "downed" 时刻，避免和真正阵亡（观战/阵亡名片）混在一起。
# 说明：队友倒地（名字牌下方"等待救援"）也可能扫到，但本检测只在「玩家专属
# 的居中倒地横幅区域」（downed_banner 区域）里匹配，队友倒地出现在队伍 HUD
# 其他位置，不在此区域，误报概率低。
DOWNED_KEYWORDS = (
    "等待救援", "救援中", "等待救助", "正在救援", "需要救援", "队友救援", "倒地",
)

# 胜负时刻的"未完成/进行中"排除（OCR 原文是最可靠的像素真文字，比多模态看图更准）：
# 结算画面文字里出现"尚未撤离成功""距离撤离成功还有9秒""倒计时9分58秒，超时将
# 撤离失败""等待01:06后撤离成功"这类词时，胜负还没真正发生——OCR 命中 victory/
# defeat 引信不可信，直接跳过不调多模态裁决（防"对局中还剩5分钟就报赢""刚进图
# 倒计时还没走完就报输"这类误报）。与 proactive 视觉摘要路径的 _IN_PROGRESS_*_RE
# 同源逻辑，OCR 文字碎片化，用更宽的间隔匹配。
_OCR_NEGATE_RESULT_RE = re.compile(
    r"(?:尚未|还未|还没|未|没有|无|未能|还没能).{0,4}(?:撤离成功|成功撤离|胜利|获胜)"
)
_OCR_IN_PROGRESS_RESULT_RE = re.compile(
    r"(?:还有\s*\d+\s*秒|倒计时|剩余|即将|准备撤离|正在撤离|等待|前往撤离点)"
    r".{0,16}(?:撤离成功|成功撤离)"
    r"|(?:撤离成功|成功撤离).{0,24}(?:还有\s*\d+\s*秒|倒计时|剩余|即将|等待)"
)
_OCR_IN_PROGRESS_EXTRACT_RE = re.compile(
    r"(?:尚未|还未|还没|仍未|目前|暂时|当前).{0,3}(?:撤离|撤离成功|成功撤离)"
)
_OCR_IN_PROGRESS_DEFEAT_RE = re.compile(
    r"(?:还有\s*\d+\s*秒|倒计时|剩余|超时.{0,4}将|即将)"
    r".{0,28}(?:撤离失败|失败|未撤离|被淘汰)"
)

# 结算画面特征词：底部区域文字含这些词时说明画面是结算横幅（撤离成功/已阵亡等），
# 里面的玩家名是战绩统计，不参与击杀播报归属判断（防"结算页玩家名被误判阵亡"）。
_SETTLE_RESULT_RE = re.compile(
    r"撤离成功|成功撤离|撤离失败|撤离超时|已阵亡|战斗胜利|战斗失败|"
    r"对局结算|本局胜利|本局失败|淘汰出局"
)

# 内置"对局中"时间锚点正则：hud 区域文字出现 MM:SS / HH:MM:SS 数字计时 →
# 判定对局中。对局内左上角倒计时是纯数字（如 04:32、01:05:23），关键词锚点
# （"撤离倒计时/撤离剩余"等）匹配不到数字，这里用时间格式直接判定——
# 比关键词更稳。兼容 OCR 把冒号识别成全角（04：32）或夹空格（04 : 32）。
_TIME_FORMAT_RE = re.compile(
    r"(?<!\d)\d{1,2}\s*[:：]\s*\d{2}(?:\s*[:：]\s*\d{2})?(?!\d)"
)

# 会话级情报保持时长（秒）：模式/地图/难度类锚点（menu+page 组，非"在主页"）
# 在主页/选点界面命中后，进入对局期间仍保留喂给 AI——否则进对局后这些区域
# 不再有字，状态被实时清空，AI 对局中问模式/地图就"完全不知情"。
_HOLD_ANCHOR_TTL = 900


def _result_not_reached(text: str) -> bool:
    """OCR 文字是否表明胜负"尚未达成/仍在进行中"（对局中/撤离倒计时还没走完）。

    命中 → 不是真胜负，直接跳过（OCR 原文比多模态看图更可靠，防误报胜负）。
    """
    t = text or ""
    return bool(
        _OCR_NEGATE_RESULT_RE.search(t)
        or _OCR_IN_PROGRESS_RESULT_RE.search(t)
        or _OCR_IN_PROGRESS_EXTRACT_RE.search(t)
        or _OCR_IN_PROGRESS_DEFEAT_RE.search(t)
    )


# 非实时对局画面的时刻排除：OCR 原文含战绩面板/回放录像/录屏等词时，
# 画面里的"胜利/获胜/击杀/等待救援"是战绩统计文字/回放标题/录屏 OSD，
# 不是实时关键时刻（16:41:21 实锤：结束后准备页"战绩详情 赛绩 胜利 目标"
# 被判 victory）。裁决模型常不遵守"战绩统计不算关键时刻"规则，引信层直接排除。
# 【不收录录屏水印词】"CNUID/帧率/延迟/丢包"——用户开着录屏打游戏时这些词
# 常驻画面，若收录，任何击杀播报（同样常驻右上角水印下方）都会被一起排除，
# 击杀引信永远触发不了。回放/录像界面另有"回放/录像/录屏"词排除。
_OCR_UI_EXCLUDE_RE = re.compile(
    r"战绩|详情|赛绩|目标|回放|录像|录屏"
)


def _ui_excluded(text: str) -> bool:
    """OCR 文字是否来自非实时对局画面（战绩面板/回放录像/录屏水印）。"""
    t = text or ""
    return bool(t and _OCR_UI_EXCLUDE_RE.search(t))


# 非游戏界面特征（误报防护）：OCR 抓到 IDE/浏览器/系统界面时直接跳过，
# 避免把菜单栏文字（如"编辑/筛选/帮助"）误识别成击杀/胜负提示
NON_GAME_UI_PATTERNS = (
    re.compile(r"文件.{0,3}(编辑|选择|查看|帮助)"),   # IDE 菜单栏
    re.compile(r"编辑.{0,3}(撤销|重做|剪切|复制|粘贴)"),
    re.compile(r"(地址栏|书签|历史记录|无痕|chrome|microsoft edge|edge浏览器)"),
    re.compile(r"(开始菜单|控制面板|任务管理器|设置应用|桌面右键)"),
    # 桌面伴侣软件界面特征（后台抓屏被 DWM 合成混入软件窗口时拦截）：
    # 前台切到本软件（全屏）后，PrintWindow 抓后台游戏窗口在某些渲染模式下
    # 返回的是 DWM 合成的屏幕内容——软件界面文字混进 OCR，导致锚点误命中
    # （实测 14:36 调软件设置时"撤离失败"持续误判）。这些词是本软件专属 UI
    # 文字（游戏里不可能出现），命中即整帧跳过，不更新页面/时刻状态。
    re.compile(r"(桌面伴侣|感知调试台|角色管理|新增区域|top_right_hud|"
               r"3D\s*视图|pywebview|模型设置|审核管理|聊天设置)"),
)

# 规则说明/加载画面文字（误报防护）：《三角洲行动》进图前的规则速览/加载画面
# 常整屏展示"32v32载具大战场 可重生、不掉装备 看增援值/据点决胜负 蓝字自己人红字敌人"
# 这类静态说明书。它不是当前对局状态：
#   - 不能当模式强信号（"大战场"会把模式误锁成"全面战场"，覆盖视觉正确识别的"烽火地带"
#     ——用户反馈"红薯窝被识别成普通模式"的根因之一）
#   - 不是关键时刻（"被击倒/救援"等疑似词来自规则文本，不是真阵亡）
#   - 是冻结画面，反复 OCR+多模态裁决会持续占用本地 LLM（"回复慢/半天没回复"元凶）
# 命中后跳过本帧：不更新画面文字快照、不做引信、不调多模态裁决。
RULE_TEXT_PATTERNS = (
    re.compile(r"(?:可重生|不掉装备).{0,16}(?:增援值|据点|决胜负)"),
    re.compile(r"(?:自己人|蓝字|蓝名).{0,16}(?:红字|红名|敌人|对手)"),
    re.compile(r"32v32.{0,6}(?:载具|大战场|据点)"),
)

# 画面静止跳过阈值：连续 N 帧画面 phash / OCR 文字静止 → 画面冻结
# （游戏加载画面说明、切走后腾讯会议残留画面等）。此时既不做多模态裁决
# （加载说明文字会持续命中 died 关键词疑似，疯狂占用本地 LLM 拖慢所有回复），
# 也不更新画面文字快照（残留文字一直"新鲜"会让 _in_game_screen 误判还在游戏画面，
# 抑制正常互动）。
STATIC_SKIP_FRAMES = 3

# 画面 phash 静止阈值：相邻帧 phash 差异小于该值视为画面真正静止
# （冻结/残留画面 phash 差异为 0~1；真实游戏画面即使缓慢变化也通常 > 3）。
# 相比"OCR 文字完全相等"，phash 对同一画面的识别更稳定——OCR 每帧输出会因
# 分割/空格抖动，文字相等判断会被抖动重置计数，phash 不会。
STATIC_PHASH_DIFF = 1.0

# 陈旧抓屏检测阈值：OCR 文字连续 N 帧完全相同、但画面 phash 一直"非静止"变化
# （静止计数被 phash 每帧重置，静止跳过永远不触发）→ 判定 mss 抓屏实例已陈旧。
# 实测（2026-08-10）：游戏切换渲染模式后，运行中的 mss 可能长期返回同一帧旧画面
# （含已关闭校准悬浮窗的残留标题），OCR 永远扫不到实时文字，击杀/阵亡引信全部
# 失效（命中恒为 0）。达到阈值后重建 mss/OCR 实例，下一帧即恢复实时抓取。
# 正常"真静止"画面（phash 稳定 < 阈值）走 STATIC_SKIP_FRAMES 慢扫路径，
# 不会累积本计数；只有"文字相同但画面抖动"的陈旧抓屏才会触发。
STALE_GRAB_LIMIT = 60

# 周期性重建 mss 抓屏实例的间隔（扫描帧数，约 1 帧/秒 → 600 帧 ≈ 10 分钟）。
# 保险措施：防止 mss 实例在长时间运行 + 显示模式切换后悄悄陈旧，定期重建无副作用。
SCT_REFRESH_SCANS = 600

# 人机/AI 击杀标记：击杀文本里出现这些词时，判定击杀对象是 AI 人机而非真实玩家（干员）。
# 例如《三角洲行动》摸金模式里打死人机不必鼓励。
# 【用户实测（2026-08-13）】三角洲人机命名固定带"阵营+兵种"：击杀提示
# "击败 某某某兵 阿萨拉/哈夫克"。提示里出现哈夫克/阿萨拉/兵 = 打死的是人机；
# 没写"兵" = 击杀真实玩家（干员）——用户明确以此区分真人/人机。
BOT_KILL_PATTERNS = (
    re.compile(r"人机"),
    re.compile(r"机器人"),
    re.compile(r"AI士兵", re.IGNORECASE),
    re.compile(r"ai soldier", re.IGNORECASE),
    re.compile(r"\bbot\b", re.IGNORECASE),
    re.compile(r"哈夫克"),
    re.compile(r"阿萨拉"),
    re.compile(r"兵"),
)


def _is_bot_kill_text(text: str) -> bool:
    """击杀相关文本是否明显指向人机/AI（而非真实玩家）。"""
    return any(p.search(text) for p in BOT_KILL_PATTERNS)


def _norm_ocr(s: str) -> str:
    """OCR 文字/玩家名归一化：去全部空白（OCR 名字间常插空格/全角空格）、转小写。

    玩家名匹配时两侧同样归一化，忽略大小写与空格差异（如设置"透亮的J能量棒"
    能匹配 OCR 识别出的"透亮的 j 能量棒"）。
    """
    return re.sub(r"[\s\u3000]+", "", s or "").lower()


def _norm_name_re(name: str) -> str:
    r"""把名字转成正则片段：允许 OCR 在名字每个字之间插空白/全角空格。

    如"打我变成杂鱼" → "打\s*我\s*变\s*成\s*杂\s*鱼"，OCR 识别出
    "打我变成 杂鱼"（字间空格）也能精确命中。逐字转义防特殊字符误解析。
    """
    chars = re.sub(r"[\s\u3000]+", "", name or "")
    return r"\s*".join(re.escape(ch) for ch in chars)


def _is_rule_text(text: str) -> bool:
    """判断 OCR 文字是否来自规则说明/加载画面（而非真实 HUD 状态）。

    《三角洲》进图前的规则速览/加载画面常整屏展示"32v32载具大战场 可重生、
    不掉装备 看增援值/据点决胜负 蓝字自己人红字敌人"这类静态说明书文字。
    命中后应跳过本帧：不更新画面文字快照、不做关键时刻引信、不调多模态裁决
    （详见 RULE_TEXT_PATTERNS 注释）。
    """
    if not text:
        return False
    return any(p.search(text) for p in RULE_TEXT_PATTERNS)


# 击杀播报里的"击杀者"：从 "XXX 击杀了/淘汰了/干掉了 YYY" 提取 XXX（玩家昵称）。
# 昵称不可能是代词/阵营词——排除这些后剩下的就是玩家名，帮 AI 记住"用户是谁"。
_KILLER_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_]{2,16})\s*(?:击杀|淘汰|干掉)了")
_KILLER_EXCLUDE = {"你", "你们", "我方", "我方干员", "队友", "敌人", "敌方", "干员", "玩家"}

# 击杀播报里的"非名字 token"（叠加帧判定用）：距离（17米）、时间（01:38）、
# 状态词（等待救援/被淘汰/爆头/回合等）不是玩家名，不计入"名字数量"。
# 叠加帧判定只看真实名字数量 ≥3 才静默方位归属，避免把"距离+2个名字"的
# 单条播报误判成叠加帧。
_NON_NAME_FEED_TOKEN_RE = re.compile(
    r"等待|救援|救助|救援中|被淘汰|淘汰|击杀|被击|击倒|爆头|双杀|三杀|"
    r"回合|倒计时|距离|米|码|秒|分钟|温度|帧率|延迟|丢包|血量|复活|"
    r"敌人|敌方|队友|干员|我方|目标"
)


def _extract_killer(text: str) -> str:
    """从击杀播报文字提取击杀者昵称；提取不到返回空串。"""
    m = _KILLER_RE.search(text or "")
    if not m:
        return ""
    name = m.group(1).strip()
    if name in _KILLER_EXCLUDE:
        return ""
    return name


# ============================================================
# OCR 识别区域（用户可自定义：多个区域，每个区域对应一种信息，可校准）
# ============================================================
# 每个区域：{"purpose": 用途, "rect": [x, y, w, h]}（相对坐标 0~1，
# 相对"主抓屏画面"：PC 版游戏 = 前台游戏窗口矩形；手机屏幕共享 = 腾讯会议
# 窗口的中央裁剪条 PHONE_CENTER_CROP）。
# purpose（该区域文字用于什么判断）：
#   - kill    击杀播报：匹配"XXX 淘汰了 YYY"、你击杀了…、双杀/爆头（引信 kill/died）
#   - kill_hint 击杀确认提示：本人击杀时屏幕中间下方弹出"击败 某某某兵 阿萨拉/哈夫克"
#             → 出现即确认击杀（含兵特征→kill_bot 人机，不含→kill 真人）
#   - page    页面名：识别当前在哪个界面（仓库/交易行/主页/对局中）→ screen_page
#   - settle  结算/胜负：匹配结算横幅（撤离成功/失败、战斗胜利/失败）
#   - hud     顶部 HUD：回合 X/7、撤离倒计时
#   - death   阵亡名片：玩家死后 1~3s 击杀栏下方出现的"敌人名片"（图片+名字，
#             表示被谁淘汰）→ 区域出现文字即 died（纯 OCR，不依赖玩家名）
#   - general 通用：并入全区域合并文字参与引信匹配
# 区域用途和坐标都可在「感知调试台 → OCR 区域」里新增/删除/修改，
# 并把色块画到真实屏幕上拖动微调（OCR 区域校准悬浮窗），保存后即时生效。
DEFAULT_REGIONS: dict[str, dict] = {
    "top_hud": {"purpose": "hud", "rect": [0.0, 0.0, 1.0, 0.14]},
    "bottom_hud": {"purpose": "settle", "rect": [0.0, 0.75, 1.0, 0.25]},
    "top_right_hud": {"purpose": "kill", "rect": [0.45, 0.0, 0.55, 0.35]},
    # 击杀确认提示（kill_hint）：本人击杀时屏幕中间下方弹出"击败 某某某兵
    # 阿萨拉/哈夫克"。三角洲机制保证【只有本人击杀】才显示该提示（右上角播报
    # 队友/敌人击杀也会滚动，不可靠）→ 出现即确认击杀，不依赖玩家名/证据门。
    # 文字含"哈夫克/阿萨拉/兵"（人机命名特征）→ kill_bot，不含 → kill（真人）。
    # 位置默认屏幕中央偏下，用「OCR 区域校准」悬浮窗拖动微调。
    "kill_hint": {"purpose": "kill_hint", "rect": [0.15, 0.55, 0.7, 0.25]},
    # 预置区域（对局状态机用，用户可在校准悬浮窗拖动框位置）：
    # 时间=左上角对局倒计时（出现=对局内/剩余时间喂 AI）、地图=右下角档位/
    # 地图名（红薯窝等）、结算=结算页收益+撤离结果
    "时间": {"purpose": "hud", "rect": [0.0, 0.0, 0.3, 0.12]},
    "地图": {"purpose": "page", "rect": [0.55, 0.55, 0.45, 0.45]},
    "结算": {"purpose": "settle", "rect": [0.2, 0.32, 0.6, 0.36]},
}

# 手机屏幕共享时主抓屏的中央裁剪条（相对腾讯会议窗口）。游戏画面竖屏居中、
# 四周是黑边和会议 UI，只裁中央去掉无关内容再放大 2 倍，小字才能进 OCR。
# 与校准悬浮窗里的"参照矩形"保持一致（区域相对坐标基于它换算）。
PHONE_CENTER_CROP: list[float] = [0.28, 0.0, 0.44, 1.0]

# 区域用途合法值
REGION_PURPOSES = ("kill", "kill_hint", "page", "settle", "hud", "death", "downed", "general")

# 各用途区域的 OCR 放大系数（2026-08-25 性能优化）。
# 触发词（等待救援/等待战局结束/撤离成功/失败/观战/已死亡）全是游戏内大字 UI，
# 原生分辨率即可精准识别，放大纯属浪费像素（2.0x 翻 4 倍面积）。故 banner 类区域
# 不放大；只有"倒计时数字/页面名"等小字区域保留 1.5x 保证可读。
_PURPOSE_OCR_SCALE = {
    "kill": 1.0, "kill_hint": 1.0, "settle": 1.0, "general": 1.0,
    "death": 1.0, "downed": 1.0,
    "hud": 1.5, "page": 1.5,   # 小字（倒计时/页面名）保留放大
}


# ============================================================
# 区域文字锚点规则（区域文字锚点训练器）
# ============================================================
# 用户可训练一套"某区域出现某关键词 = 页面处于某状态"的强判定规则：
#   - region：区域名（必须是 regions 里已有的名字）
#   - keywords：关键词列表（该区域 OCR 文字命中任意一个即触发）
#   - state：页面状态标签（自定义，用于去重/调试）
#   - label：给 AI 看的当前页面中文描述
# OCR 扫描时把各区域文字逐条跑锚点匹配，命中 → 产出确定性页面状态，
# 更新进游戏知识库（GameKnowledge），对话/主动话术注入时"看字不猜"，
# 视觉模型对当前页面的猜测作废（OCR 是画面里真实渲染的文字，比模型脑补可靠）。
# 规则可在感知调试台 → OCR区域 → 文字锚点里新增/删除/编辑，保存即热生效
# （config.yaml perception.ocr.anchors）。
def _anchor_get(v: Any, key: str, default: Any = None) -> Any:
    """从 dict 或 SimpleNamespace（config 解析产物）取字段，兼容两种。"""
    if isinstance(v, dict):
        return v.get(key, default)
    return getattr(v, key, default)


# 锚点正则 patterns 的编译缓存（key=正则字符串）。锚点字典本身只存字符串，
# 绝不放 Pattern 对象，避免被感知调试台 JSON 序列化时抛 "not JSON serializable"。
_COMPILED_PATTERNS: dict = {}


def _parse_anchor(v: Any, index: int = 0) -> Optional[dict]:
    """解析一条锚点规则配置 → dict 或 None。

    兼容两种格式：
      - dict {"region", "keywords", "state", "label"}
      - SimpleNamespace（config.py _to_ns 递归转换产物）
      非法条目（缺区域名 / 关键词为空）返回 None。
    """
    if not isinstance(v, (dict, SimpleNamespace)):
        return None
    region = str(_anchor_get(v, "region") or "").strip()
    kws = _anchor_get(v, "keywords", [])
    if not isinstance(kws, (list, tuple)):
        kws = []
    keywords = [str(k).strip() for k in kws if str(k).strip()]
    if not region or not keywords:
        return None
    state = str(_anchor_get(v, "state") or "").strip() or f"anchor_{index}"
    label = str(_anchor_get(v, "label") or "").strip() or state
    # 优先级：多条规则同时命中时取数值最高的（如"正在撤离倒计时"应压过
    # 兜底的"在主页/菜单页面"）。默认 0；主页兜底规则配 priority: -1。
    try:
        priority = int(_anchor_get(v, "priority", 0) or 0)
    except (TypeError, ValueError):
        priority = 0
    # 页面上下文分组：menu（菜单/主页页面）命中时会压掉 match（对局内状态）——
    # 主页的导航/聊天/仓库文字里常出现"撤离/成功/失败"等对局词，不能让它们
    # 在主页时把"正在撤离/撤离成功/撤离失败"点亮（用户实测的互斥需求）。
    group = str(_anchor_get(v, "group") or "").strip()
    # 记录规则所指向区域的用途（page/hud/settle/kill…）。区域名可能被用户
    # 在校准里改名/重加（实测 页面名字→页面→页面名字），规则按名字会失联——
    # 失联时按用途兜底找唯一同用途区域（见 match_anchors）。
    region_purpose = str(_anchor_get(v, "region_purpose") or "").strip()
    # 正则 patterns：对整图 OCR 文字做正则匹配（无视区域），用于"回合 X/7 →
    # 红鼠窝"这类信号——它出现在顶部 HUD，不在锚点绑定的区域里，且对局中字面
    # "红鼠窝"被 UI 隐藏，只能靠回合计数识别（用户 2026-08-25 设计：局内=模式，
    # 回合1/7 出现即代表红薯窝/红鼠窝）。解析失败的正则直接跳过（宁缺勿滥）。
    # ⚠️ 锚点字典只存正则【字符串】，绝不放编译后的 Pattern 对象——否则锚点
    # 被感知调试台序列化成 JSON 时会抛 "Pattern is not JSON serializable" 把 UI 卡死。
    raw_patterns = _anchor_get(v, "patterns", [])
    if not isinstance(raw_patterns, (list, tuple)):
        raw_patterns = []
    patterns = []
    for p in raw_patterns:
        ps = str(p).strip()
        if not ps:
            continue
        try:
            re.compile(ps)  # 仅校验语法，编译对象不入字典
        except re.error:
            continue
        patterns.append(ps)
    return {
        "region": region,
        "keywords": keywords,
        "state": state,
        "label": label,
        "priority": priority,
        "group": group,
        "region_purpose": region_purpose,
        "patterns": patterns,
    }


def _deep_copy_anchors(anchors: list) -> list[dict]:
    """深拷贝锚点规则列表。"""
    return [{"region": a.get("region", ""),
             "keywords": list(a.get("keywords") or []),
             "state": a.get("state", ""),
             "label": a.get("label", ""),
             "priority": int(a.get("priority", 0) or 0),
             "group": a.get("group", ""),
             "region_purpose": a.get("region_purpose", ""),
             "patterns": list(a.get("patterns") or []),
             "moment": a.get("moment", "")}
            for a in anchors if isinstance(a, dict)]

# 默认区域 key → 用途（旧格式 config.yaml 里 regions 是 {name: [x,y,w,h]}，
# 没有 purpose 字段，按名字推断时默认区域直接取这份映射，保证语义不丢）
_DEFAULT_PURPOSES = {
    "top_hud": "hud",
    "bottom_hud": "settle",
    "top_right_hud": "kill",
}


def _infer_purpose(name: str) -> str:
    """按区域名推断用途（兼容旧格式 config：{name: [x,y,w,h]} 无 purpose 字段）。

    名字含 kill/击杀/feed → kill（击杀播报）；top_right 优先 kill（PC 击杀播报
    在右上角）；page/页面 → page；settle/结算 → settle；hud/top → hud；否则通用。
    """
    n = (name or "").strip().lower()
    if n in _DEFAULT_PURPOSES:
        return _DEFAULT_PURPOSES[n]
    if "top_right" in n or "kill" in n or "击杀" in n or "feed" in n:
        return "kill"
    if "page" in n or "页面" in n or "主页" in n:
        return "page"
    if "settle" in n or "结算" in n or "result" in n or "banner" in n:
        return "settle"
    if "death" in n or "阵亡" in n or "名片" in n or "被谁" in n:
        return "death"
    if "hud" in n or "top" in n or "倒计时" in n or "回合" in n:
        return "hud"
    return "general"


def _parse_region_value(v: Any, name: str = "") -> tuple[Optional[list[float]], str]:
    """解析一个区域配置值 → (rect, purpose)。兼容三种格式：
      - 新格式 {"purpose": "kill", "rect": [x,y,w,h]}
      - SimpleNamespace（config.py _to_ns 递归转换产物：dict → SimpleNamespace，
        **旧代码只认 dict，导致 config.yaml 里保存的自定义区域重启后全部被丢弃**，
        回退默认三区——用户"保存的识别区块每次软件关掉就没了"的根因）
      - 旧格式 [x,y,w,h]（无 purpose 字段，按名字推断用途，见 _infer_purpose）
    """
    rect: Optional[list[float]] = None
    purpose = "general"
    if isinstance(v, dict) or isinstance(v, SimpleNamespace):
        r = _region_get(v, "rect", None)
        if isinstance(r, (list, tuple)) and len(r) == 4:
            try:
                rect = [float(x) for x in r]
            except (TypeError, ValueError):
                return None, purpose
        purpose = str(_region_get(v, "purpose", "") or "") or _infer_purpose(name)
    elif isinstance(v, (list, tuple)) and len(v) == 4:
        try:
            rect = [float(x) for x in v]
        except (TypeError, ValueError):
            return None, purpose
        purpose = _infer_purpose(name)
    if purpose not in REGION_PURPOSES:
        purpose = "general"
    return rect, purpose


def _region_get(v: Any, key: str, default: Any = None) -> Any:
    """从 dict 或 SimpleNamespace（config 解析产物）取字段，兼容两种。"""
    if isinstance(v, dict):
        return v.get(key, default)
    return getattr(v, key, default)


def _deep_copy_regions(regions: dict[str, dict]) -> dict[str, dict]:
    """深拷贝区域结构 {name: {purpose, rect}}。"""
    return {k: {"purpose": v.get("purpose", "general"),
                "rect": list(v.get("rect") or [0, 0, 1, 1])}
            for k, v in regions.items()}


class GameOcrMonitor:
    def __init__(self, bus: EventBus, cfg: Config, monitor: ForegroundMonitor,
                 llm: Optional[Any] = None, game_knowledge: Any = None,
                 sensor: Optional[InputSensor] = None,
                 throttle_check: Optional[Any] = None,
                 frame_source: str = "screen",
                 video_path: Optional[str] = None,
                 video_sample_sec: float = 0.5) -> None:
        self._bus = bus
        self._cfg = cfg
        self._monitor = monitor
        # 【视频测试源（2026-08-26 新增）】frame_source="video" 时，_grab_image
        # 改从 cv2.VideoCapture 逐帧读视频代替真实屏幕，用于"提交录屏→后台跑
        # 全流程→出报告"的离线回归测试。video 源下跳过前台/遮挡/静止检测，
        # 每帧都 OCR（视频里关键时刻可能只闪现，不跳静止确保不漏）。
        self._frame_source: str = frame_source if frame_source in ("screen", "video") else "screen"
        self._video_path: Optional[str] = video_path
        self._video_cap = None          # cv2.VideoCapture 实例（懒加载）
        self._video_eof: bool = False   # 视频播放完毕标志
        self._video_frame_idx: int = 0  # 已读帧计数（视频时间戳用）
        self._video_cur_time_ms: float = 0.0   # 当前帧在视频中的时间(ms)，报告时间轴用
        self._video_total_ms: float = 0.0      # 视频总时长(ms)，报告用
        # 抽帧间隔(秒)：视频测试时游戏 UI 文字变化慢，无需逐帧 OCR。
        # 默认每 0.5s 抽 1 帧做 OCR，30fps 视频提速 ~60 倍，关键时刻(击杀/胜负
        # 屏停留数秒)不会漏。改大=更快但更可能漏瞬态；改小=更准但更慢。
        self._video_sample_sec: float = max(0.1, float(video_sample_sec))
        self._video_keep_every: int = 1  # 每 N 帧抽 1 帧 OCR（打开视频后按 fps 算）
        # 输入证据门（v2 核心）：pynput 钩子，判断玩家是否在开枪/战斗。
        # 击杀判定必须过它（没开枪 = 击杀播报出现也不报）。未提供时按配置
        # 创建独立实例（main 会共享一个，测试可 mock）。
        ocr_cfg = getattr(cfg.ns.perception, "ocr", None)
        ev_win = float(getattr(ocr_cfg, "evidence_window_s", 8.0) if ocr_cfg else 8.0)
        ev_clk = int(getattr(ocr_cfg, "evidence_min_clicks", 2) if ocr_cfg else 2)
        self._sensor = sensor if sensor is not None else InputSensor(
            window_s=ev_win, min_clicks=ev_clk)
        self._game_knowledge = game_knowledge  # GameKnowledge（可选）：从屏幕文字识别当前游戏
        # 【游戏前台/锁快照 让位回调（2026-08-25 修复抽帧）】throttle_check 返回
        # True 表示"前台在玩游戏 / 已被锁快照"——此时 OCR 应降频让位，避免周期
        # 性 CPU 尖峰抢游戏时间片造成抽帧（尤其核显/轻薄本卡炸）。注意：后台
        # pc_game_window（游戏在后台没玩）不算 throttle，后台照常高频不影响性能。
        self._throttle_check = throttle_check
        _ocfg = getattr(cfg.ns.perception, "ocr", None)
        # 【节奏门禁（2026-08-25 取代 stagger/phash 门禁）】对局中按 game_cadence
        # 扫描轮摊薄 OCR（模式在对局里固定、关键时刻按 3~4s 一轮足够），大厅/非
        # 对局模式区域每轮高频（模式切换秒级识别）。扫描循环本身保持高频，OCR
        # 频率被周期摊薄 → CPU 平稳不尖峰；关键时刻/模式切换判定零漏。
        self._game_cadence = int(getattr(_ocfg, "game_cadence", 4) if _ocfg else 4)
        if self._game_cadence < 1:
            self._game_cadence = 1
        self._rotate_idx = 0  # 保留字段（节奏门禁不再使用，留作兼容）
        # 游戏玩家名：击杀播报里"玩家名 淘汰了 XXX"=玩家击杀，"XXX 淘汰了 玩家名"=
        # 玩家阵亡（精确匹配，不做左右位置猜归属）。WebUI 填写 / AI 询问记录。
        # 写 config.yaml perception.ocr.player_name。
        self._player_name: str = str(getattr(ocr_cfg, "player_name", "") or "").strip()
        # 仅文字模式（config.yaml perception.ocr.text_only）：game_recognition
        # 关闭时只借 OCR 的"读屏幕真文字"能力（喂给视觉模型 ocr_text_hint +
        # 页面守卫校正脑补），只更新知识库文字、绝不发布任何时刻判定
        # （OCR 击杀判定误报频发已下线，text_only 不把它带回来）。
        self._text_only: bool = bool(
            getattr(ocr_cfg, "text_only", False) if ocr_cfg else False)
        self._enabled: bool = bool(getattr(ocr_cfg, "enabled", True) if ocr_cfg else True)
        self._interval: float = float(getattr(ocr_cfg, "interval_s", 1.5) if ocr_cfg else 1.5)
        self._jitter: float = float(getattr(ocr_cfg, "interval_jitter", 0.3) if ocr_cfg else 0.3)
        # ===== OCR 推理设备（GPU/DirectML 加速，2026-08-25）=====
        # device: gpu/cpu/auto。gpu=优先 DirectML(GPU)；无 DmlExecutionProvider
        # 环境自动降级 CPU（rapidocr 内部也会兜底，不报错）。auto=有则用 GPU 否则 CPU。
        self._ocr_device = str(getattr(ocr_cfg, "device", "gpu") or "gpu").lower()
        self._gpu_interval = float(getattr(ocr_cfg, "gpu_interval_s", 1.0) or 1.0)
        self._gpu_log_interval_s = float(getattr(ocr_cfg, "gpu_log_interval_s", 5.0) or 5.0)
        _dml_ok = False
        if self._ocr_device in ("gpu", "auto"):
            try:
                import onnxruntime as _ort  # 受 main.py 注入的 ort_dml 目录影响→DML 版
                _dml_ok = "DmlExecutionProvider" in _ort.get_available_providers()
            except Exception:  # noqa: BLE001
                _dml_ok = False
        self._ocr_use_gpu = (self._ocr_device == "gpu" and _dml_ok) or \
                            (self._ocr_device == "auto" and _dml_ok)
        if self._ocr_device == "cpu":
            self._ocr_use_gpu = False
        # GPU 模式用更短间隔 + 全区域高频；CPU 模式保持 interval_s + 节奏门禁轮转
        if self._ocr_use_gpu:
            self._interval = self._gpu_interval
        self._gpu_log_t: float = 0.0
        self._last_ocr_err: float = 0.0
        self._cooldown_s: float = float(getattr(ocr_cfg, "moment_cooldown_s", 20) if ocr_cfg else 20)
        # 持续状态（倒地/阵亡）moment 冷却重发间隔：持续命中按此周期重发，避免一次性互动后沉默
        self._reemit_cooldown_s: float = float(getattr(ocr_cfg, "moment_reemit_cooldown_s", 30) if ocr_cfg else 30)
        # 需要"持续状态周期重发"的 moment：倒地等待救援是持续状态（一直趴着等
        # 救援），持续陪伴说"别慌"合理；但阵亡(died)进入观战后用户已死，重复说
        # 持续状态重发仅用于"确实长时间持续、需要持续陪伴"的时刻。2026-08-26 实锤：
        # downed（用户倒地濒死）一旦进 _reemit，会每 30s 重发"别慌趴好别动"——而
        # 红薯窝回合制里"用户倒地濒死"大字常驻/反复闪现，导致整局刷屏 14 次，
        # 盖过击杀/阵亡正经互动，体感像"AI 抽风/不干正事"。故 downed 也改为仅上升
        # 沿触发（每次真正倒地/被救起后再倒地说一次），不再周期重发。
        self._reemit_moments: set[str] = set()
        self._monitor_index: int = int(getattr(ocr_cfg, "monitor_index", 1) if ocr_cfg else 1)
        self._max_width: int = int(getattr(ocr_cfg, "max_width", 1280) if ocr_cfg else 1280)
        # 识别区域（用户可自定义多区域：{name: {purpose, rect}}，击杀/页面名/结算等
        # 各自独立区域，相对坐标 0~1）。支持 config.yaml perception.ocr.regions 覆盖
        # （旧格式 {name: [x,y,w,h]} 自动按名字推断用途），也可在校准悬浮窗拖动热更新。
        self._regions: dict[str, dict] = _deep_copy_regions(DEFAULT_REGIONS)
        # 【2026-09-07 整合】当前生效的软件档案 OCR 覆盖（逐游戏专属配置）；无则 None
        self._active_profile: Optional[Any] = None
        self._active_profile_exe: str = ""
        regions_cfg = getattr(ocr_cfg, "regions", None)
        if regions_cfg is not None:
            merged: dict[str, dict] = {}
            src = regions_cfg.__dict__ if hasattr(regions_cfg, "__dict__") else {}
            for key, v in src.items():
                rect, purpose = _parse_region_value(v, key)
                if rect is None:
                    continue
                merged[key] = {"purpose": purpose, "rect": rect}
            if merged:
                self._regions = merged
        # 倒地检测专属区域：玩家倒地横幅在屏幕中央/下方，单独一个小区域每轮必扫
        # （见 _scan_once 的倒地检测通道），不被 km 轮转跳过导致漏检。用户校准
        # 悬浮窗可改位置；config 里没配也不影响（这里兜底保证存在）。
        if "downed_banner" not in self._regions:
            self._regions["downed_banner"] = {"purpose": "downed", "rect": [0.20, 0.42, 0.60, 0.33]}

        # 文字锚点规则（区域文字锚点训练器）：{region: [anchor...]}，用户可训练
        # "区域出现关键词=页面状态"。config.yaml perception.ocr.anchors 读取，
        # 也支持 ocr.set_anchors() 热更新（感知调试台编辑保存）。命中结果写入
        # 游戏知识库（anchor_state），对话/主动话术"看字不猜"。
        self._anchors: list[dict] = []
        anchors_cfg = getattr(ocr_cfg, "anchors", None)
        if anchors_cfg is not None:
            src = (anchors_cfg.__dict__ if hasattr(anchors_cfg, "__dict__")
                   else anchors_cfg)
            if isinstance(src, (list, tuple)):
                self._anchors = [a for a in
                                 (_parse_anchor(v, i) for i, v in enumerate(src))
                                 if a]
        self._anchor_hits: dict[str, float] = {}  # state → 最近命中时间戳
        self._last_anchor_label: str = ""          # 最近命中锚点的中文描述（喂 AI）
        self._last_anchor_labels: list[str] = []   # 本帧命中的所有锚点描述（调试台多命中展示）
        self._last_anchor_states: list[str] = []   # 本帧命中的所有锚点 state（调试台逐行高亮）
        # 会话级情报锚点（模式/地图/难度）：开局确认后保持 _HOLD_ANCHOR_TTL 秒，
        # 对局中持续告诉 AI（见 _apply_anchors）。元素 {label, ts}。
        self._held_anchor_labels: list[dict] = []
        # 锚点强绑定（2026-08-25）：带 moment 的关键时刻锚点命中 → 发布 moment
        # 事件触发 AI 互动。_anchor_moment_active 记录本帧各 moment 是否活跃，
        # 用于上升沿去重（持续命中不刷屏，消失后复位可再发）；_pending_moments
        # 收集本帧待发布的 moment，由 _scan_once 在 _apply_anchors 后 await 发出。
        self._anchor_moment_active: dict[str, bool] = {}
        self._pending_moments: list[dict] = []

        self._running: bool = True
        self._ocr = None  # RapidOCR 实例（懒加载）
        self._ocr_dml_mem_mb: Optional[float] = None  # 本软件OCR GPU显存(估算，DML加载增量)
        self._sct = None  # mss 实例（懒加载）
        self._moment_last: dict[str, float] = {}  # moment → 上次触发时间
        self._spectating: bool = False  # 是否处于观战画面（玩家阵亡后才进入观战）
        self._waiting_match_end: bool = False  # 玩家已阵亡、正处于"等待对局结束"状态
        self._downed_active: bool = False  # 玩家当前是否处于倒地(等待救援)状态（上升沿触发一次）
        self._static_frames: int = 0      # 连续 N 帧画面/OCR 文字静止（画面冻结/加载画面残留）
        self._last_ocr_text: str = ""     # 上一帧 OCR 文字（静止判断用）
        self._last_corner_text: str = ""  # 上一帧左上角 OCR 文字（页面名/档位，心跳诊断用）
        self._last_top_text: str = ""     # 上一帧顶部 HUD OCR 文字（回合 X/7/撤离倒计时，心跳诊断用）
        self._last_top_right_text: str = ""  # 上一帧右上角 OCR 文字（PC 击杀播报，心跳诊断用）
        self._last_bottom_text: str = ""  # 上一帧底部信息区 OCR 文字（击杀播报/结算横幅，心跳诊断用）
        self._region_texts: dict[str, str] = {}  # 各识别区域最近 OCR 文字 {name: text}（调试台展示）
        # 节奏门禁：{name: 上次 OCR 文字}（跳过的轮次沿用，模式/页面状态不丢）；
        # _scan_tick 扫描轮计数器，用于对局中按 game_cadence 摊薄 OCR 频率。
        self._region_last_text: dict[str, str] = {}
        # 懒编码：抓到的帧 + 其 JPEG 缓存（仅在发布 moment 时编码，省每轮开销）
        self._last_img: Optional[Image.Image] = None
        self._frame_jpeg_cache: Optional[bytes] = None
        self._scan_tick: int = 0
        self._km_idx: int = 0  # 关键时刻区轮转游标
        self._last_kill_feed: str = ""  # 上次 kill 引信裁决时的击杀播报文字（变化检测用）
        self._menu_page_active: bool = False  # 锚点判定当前在主页/菜单（右上角是商城活动文字非击杀播报）
        self._last_death_text: str = ""  # 上次阵亡名片区域文字（变化检测用，玩家死后 1~3s 才出现）
        self._last_phash: Optional[int] = None  # 上一帧画面 phash（画面静止检测，OCR 之前拦截）
        self._stale_hits: int = 0  # 陈旧抓屏计数：连续相同 OCR 文字帧数（达到 STALE_GRAB_LIMIT 重建抓屏）
        self._last_sct_refresh: int = 0  # 上次周期性重建 mss 时的扫描帧数
        # 证据门统计（心跳诊断用）：击杀被证据门拦截的次数（没开枪误报的根治验证）
        self._evidence_blocked: int = 0
        # 调试统计
        self._scan_count: int = 0
        self._hit_count: int = 0
        self._recent: list[dict] = []  # 最近命中（最多 10 条）
        self._recent_scans: list[dict] = []  # 最近扫描的 OCR 文本（含未命中，供诊断，最多 10 条）
        self._last_warn: float = 0.0  # 前台非游戏提示节流
        self._loop_count: int = 0  # 扫描循环计数（心跳诊断日志用）
        # 会议置顶窗口遮挡检测（mss 抓真实像素，置顶窗口盖在游戏上会被一起抓进
        # OCR——会议 UI 文字污染锚点/击杀匹配，主页区域被盖住扫不到 →"判断不了
        # 主页"。检测到遮挡时跳过本帧，并在调试台提示用户移开窗口）。
        self._occluded: bool = False       # 当前帧是否被置顶会议窗口遮挡（调试台展示）
        self._occlusion_skips: int = 0     # 因遮挡跳过的扫描帧数
        self._last_occl_warn: float = 0.0  # 遮挡提示日志节流
        # OCR 区域校准悬浮窗打开中：悬浮窗是**置顶透明窗**，盖在游戏画面上，
        # 它的 UI 文字（区域标签"tophud（顶部HUD）""页面名字"、提示文字）会被
        # mss 连游戏画面一起抓进 OCR → 锚点命中"在主页"之类的假状态（13:28 实锤：
        # 用户在校准，OCR 扫到"开始游戏 仓库 交易行 改枪台"主页文字 + 悬浮窗标签，
        # 进靶场后还一直显示"在主页"）。校准期间 OCR 直接暂停，关闭时重建 mss
        # 实例 + 清空锚点状态，恢复实时扫描。
        self._calibrating: bool = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def stop(self) -> None:
        self._running = False
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
            self._sct = None
        # 证据门钩子随 OCR 一起停（main 里若共享实例，由调用方负责 stop）
        try:
            self._sensor.stop()
        except Exception:  # noqa: BLE001
            pass

    def set_calibrating(self, flag: bool) -> None:
        """OCR 区域校准悬浮窗开关（webapp 校准线程调用）。

        校准悬浮窗是**置顶透明窗**，盖在游戏画面上时它的 UI 文字（区域标签
        "tophud（顶部HUD）""页面名字"、提示文字）会被 mss 连游戏画面一起抓进
        OCR → 锚点命中"在主页"之类的假状态（13:28 实锤：用户在校准，OCR 扫到
        "开始游戏 仓库 交易行 改枪台"主页文字 + 悬浮窗标签，进靶场后还一直显示
        "在主页"）。所以：
        - 打开校准 → OCR 扫描暂停（_scan_once 里检查），并立即清空锚点/页面状态
        - 关闭校准 → 重建 mss 抓屏实例（旧实例可能残留悬浮窗像素）+ 清空锚点，
          恢复实时扫描
        """
        flag = bool(flag)
        if flag == self._calibrating:
            return
        self._calibrating = flag
        if flag:
            # 打开校准：立即清掉"在主页"等旧锚点状态，防止校准期间一直残留
            self._apply_anchors([])
            logger.info("OCR 区域校准已打开，OCR 扫描暂停（防止悬浮窗文字污染识别）")
        else:
            self._reset_grab()
            self._apply_anchors([])
            logger.info("OCR 区域校准已关闭，重建抓屏实例并恢复扫描")

    def _reset_grab(self) -> None:
        """重建 mss 抓屏实例 + 清空静止/陈旧计数。

        校准悬浮窗关闭后旧抓屏实例可能残留悬浮窗像素（置顶透明窗盖在游戏上，
        mss 抓的就是悬浮窗内容），必须重建才能抓到真实游戏画面。
        """
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
        self._sct = None
        self._last_phash = None
        self._static_frames = 0
        self._stale_hits = 0
        self._last_ocr_text = ""
        self._last_corner_text = ""
        self._last_top_text = ""
        self._last_top_right_text = ""
        self._last_bottom_text = ""
        self._region_texts = {}
        # 清知识库画面文字快照（旧值可能是悬浮窗/主页菜单残留，会误导 screen_page）
        if self._game_knowledge is not None:
            try:
                self._game_knowledge.update_screen_text("", "", "", "")
                self._game_knowledge.update_anchor_state("")
            except Exception:  # noqa: BLE001
                pass

    def _ensure_ocr(self):
        """懒加载 RapidOCR（首次调用会自动准备模型）。

        GPU 模式（self._ocr_use_gpu）：det/cls/rec 三引擎全走 DirectML
        （DmlExecutionProvider，需 onnxruntime-directml，由 main.py 注入 sys.path）。
        OCR 推理移到 GPU 后，对局里可放开为 1s 全区域扫描而零 CPU 尖峰。
        CPU 模式：显式 use_cuda=False + 限 4 线程，避免占满核拖慢游戏/对话。
        """
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            if self._ocr_use_gpu:
                try:
                    # 量本软件 OCR 自己的 GPU 显存：DirectML 走 Windows D3D 路径，
                    # nvidia-smi 的 per-process 查询(CUDA)看不到它，所以改用
                    # 「整卡显存在 DML 会话创建前后的增量」来归因（游戏显存在这
                    # 一瞬恒定，差值就是 OCR 模型占的 VRAM）。标注为估算。
                    _before = None
                    gl = gpu_load()
                    if gl is not None:
                        _before = gl["used_mb"]
                    self._ocr = RapidOCR(
                        det_use_dml=True, cls_use_dml=True, rec_use_dml=True,
                        intra_op_num_threads=4)
                    if _before is not None:
                        gl2 = gpu_load()
                        if gl2 is not None:
                            self._ocr_dml_mem_mb = max(0.0, gl2["used_mb"] - _before)
                            logger.info(
                                "GPU(DirectML) OCR 加载完成，本软件OCR显存增量≈%.0fMB",
                                self._ocr_dml_mem_mb)
                except Exception:  # 任意异常（含 DML 显存不足 OOM）→ 降级 CPU，避免整体禁用
                    logger.warning(
                        "GPU(DirectML) OCR 初始化失败，OCR 退回 CPU 推理",
                        exc_info=True)
                    self._ocr_use_gpu = False
                    self._ocr = RapidOCR(
                        det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                        intra_op_num_threads=4)
            else:
                try:
                    self._ocr = RapidOCR(
                        det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                        intra_op_num_threads=4)
                except (TypeError, ValueError):  # 旧版 API 兼容回退
                    try:
                        self._ocr = RapidOCR(params={
                            "Global": {"use_cuda": False}})
                    except Exception:  # noqa: BLE001
                        self._ocr = RapidOCR()
        return self._ocr

    def _ensure_sct(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def _is_phone_share(self) -> bool:
        """当前是否手机屏幕共享场景（腾讯会议进程在运行，不要求前台）。

        用户手机版共享打手游时，电脑前台常切到其他窗口（看聊天/写代码），
        但腾讯会议进程还在（用户仍在手机上打游戏）→ 仍按手机共享处理：
        裁剪中央放大、持续扫描，否则手机里的阵亡/失败/击杀会漏检。
        手机版画面竖屏居中显示、四周有黑边和会议 UI，游戏内容比整窗小很多，
        OCR/视觉需要裁剪放大才能识别游戏内文字与关键时刻（击杀/阵亡/胜负）。

        **例外**：前台就是 PC 版游戏窗口时，用户明确在玩电脑版（如切回电脑
        打三角洲），此时不算手机共享——否则抓屏会去抓腾讯会议窗口里残留的
        手机画面（用户切走后仍残留、每帧文字相同），白白扫一堆冻结画面。
        """
        if self._monitor is None:
            return False
        # 前台就是腾讯会议窗口（标题"xxx的快速会议"）→ 手机屏幕共享。
        # 注意：foreground_monitor 把腾讯会议标题映射成 app_hint="game"
        # （手机共享时前台是会议窗口），所以这里必须【先】判 meeting_foreground，
        # 否则会被下面的 is_game_foreground() 例外误判成"PC 版"而不裁切中央放大，
        # 导致 OCR 只能扫到会议 UI 文字（"会议详情/听不到他人说话"），
        # 手机画面里的击杀/阵亡/模式文字全部漏检。
        if self._monitor.is_meeting_foreground():
            return True
        # 前台是真正的 PC 游戏窗口（非腾讯会议）→ 用户明确在玩电脑版，
        # 不算手机共享——否则抓屏会去抓腾讯会议窗口里残留的
        # 手机画面（用户切走后仍残留、每帧文字相同），白白扫一堆冻结画面。
        if self._monitor.is_game_foreground():
            return False
        check = getattr(self._monitor, "is_meeting_running", None)
        if callable(check):
            return check()
        check_fg = getattr(self._monitor, "is_meeting_foreground", None)
        if callable(check_fg):
            return check_fg()
        title = getattr(self._monitor, "last_title", "") or ""
        tl = title.lower()
        return "腾讯会议" in title or "tencent meeting" in tl

    def _meeting_occludes_game(self) -> bool:
        """PC 游戏前台时，是否有【置顶(topmost)】的腾讯会议窗口遮挡游戏抓屏区域。

        mss 抓的是屏幕真实像素，置顶窗口盖在游戏上面时，它的像素会被一起抓进
        OCR——会议 UI 文字（参会人/正在共享/聊天）会污染击杀/胜负匹配，页面锚点
        区域（如左上角页面名）被盖住扫不到 →"主页判断不了"。此时不抓屏不 OCR
        （抓了也是错的内容），并在调试台提示用户移开窗口。
        手机屏幕共享模式（_is_phone_share）下会议窗口里就是游戏画面，是内容来源
        而非遮挡，不判。
        **后台识别**（前台非游戏、用 PrintWindow 抓游戏窗口交换链）不经过屏幕合成，
        置顶窗口不会进画面，也不判。
        与抓屏区域重叠面积 ≥ 15% 才判定遮挡（会议窗口只搭在边上一点不算）。
        """
        if self._monitor is None or self._is_phone_share():
            return False
        if not self._monitor.is_game_foreground():
            # 后台识别走 PrintWindow 抓窗口交换链，不抓屏幕像素，无遮挡概念
            return False
        cap = self._monitor.foreground_rect()
        if not cap:
            return False
        cap_x0, cap_y0, cap_w, cap_h = cap
        if cap_w <= 0 or cap_h <= 0:
            return False
        cap_area = cap_w * cap_h
        for w in self._monitor.visible_windows():
            if not w.get("is_meeting") or not w.get("topmost"):
                continue
            r = w.get("rect")
            if not r or len(r) != 4:
                continue
            left, top, ww, hh = r
            x0, y0 = max(left, cap_x0), max(top, cap_y0)
            x1 = min(left + ww, cap_x0 + cap_w)
            y1 = min(top + hh, cap_y0 + cap_h)
            if x1 > x0 and y1 > y0:
                inter = (x1 - x0) * (y1 - y0)
                if inter / cap_area >= 0.15:
                    return True
        return False

    def snapshot(self) -> dict:
        """运行状态快照（供调试台轮询，线程安全）。"""
        return {
            "enabled": self._enabled,
            "interval_s": self._interval,
            "cooldown_s": self._cooldown_s,
            "foreground_hint": self._monitor.current_hint if self._monitor else "",
            "scan_count": self._scan_count,
            "hit_count": self._hit_count,
            "evidence": self._sensor.stats(),  # 输入证据门状态（ok/最近左键/拦截数）
            "evidence_blocked": self._evidence_blocked,
            "moment_last": dict(self._moment_last),
            "recent": list(self._recent),
            "recent_scans": list(self._recent_scans),
            "top_text": self._last_top_text[:80],
            "top_right_text": self._last_top_right_text[:80],
            "bottom_text": self._last_bottom_text[:80],
            "regions": _deep_copy_regions(self._regions),
            "region_texts": dict(self._region_texts),
            "anchors": _deep_copy_anchors(self._anchors),
            "anchor_state": self._last_anchor_label,
            "anchor_hit_labels": list(self._last_anchor_labels),
            "anchor_hit_states": list(self._last_anchor_states),
            "player_name": self._player_name,
            "occluded": self._occluded,
            "occlusion_skips": self._occlusion_skips,
        }

    # ======== 游戏玩家名（击杀播报归属判断，纯 OCR 方案） ========
    @property
    def player_name(self) -> str:
        """当前游戏玩家名（空 = 未设置）。"""
        return self._player_name

    def set_player_name(self, name: str) -> None:
        """设置游戏玩家名：内存热生效 + 写回 config.yaml（下次启动保留）。

        当前唯一生效路径：WebUI「我的名字」填写（webapp.ocr_set_player_name
        → GameOcrMonitor.set_player_name）。历史上文档曾声称 proactive 从用户
        回复提取名字后发布 SET_PLAYER_NAME 事件再经订阅刷新，但该事件全仓无任何
        发布者，事件路径早已静默死亡，已于 2026-10-23 清理死订阅。改名一律走
        本直调方法。
        """
        name = (name or "").strip()
        self._player_name = name
        logger.info("游戏玩家名已更新: %r", name)
        try:
            raw = self._cfg.raw
            raw.setdefault("perception", {})
            ocr_raw = raw["perception"].setdefault("ocr", {})
            ocr_raw["player_name"] = name
            self._cfg.save()
        except Exception:  # noqa: BLE001
            logger.warning("玩家名写入配置失败（本次运行仍生效）", exc_info=True)

    # ======== 逐游戏 OCR 调优（2026-09-07 整合：OCR 配置从软件档案读取） ========
    def _resolve_active_profile(self) -> None:
        """每轮扫描前解析前台 exe 对应的软件档案，作为本轮回退 OCR 配置源。

        仅当档案 mode=snapshot 且配置了 ocr_regions 时才覆盖全局默认；否则回退
        全局 config。结果缓存到 self._active_profile（按 exe 名变化才重查）。
        """
        self._active_profile = None
        self._active_profile_exe = ""
        monitor = self._monitor
        if monitor is None:
            return
        try:
            proc = monitor.foreground_proc_name() or ""
        except Exception:  # noqa: BLE001
            proc = ""
        if not proc:
            return
        try:
            from ..companion.software_profiles import get_registry, MODE_SNAPSHOT
            reg = get_registry()
            if reg is None:
                return
            prof = reg.find_by_exe_name(proc)
            if prof is not None and getattr(prof, "mode", "") == MODE_SNAPSHOT \
                    and getattr(prof, "ocr_regions", None):
                self._active_profile = prof
                self._active_profile_exe = proc
                logger.debug("OCR 使用软件档案专属配置: %s (regions=%d)",
                             proc, len(prof.ocr_regions))
        except Exception:  # noqa: BLE001
            pass

    @property
    def effective_player_name(self) -> str:
        """当前生效玩家名：优先软件档案专属配置，否则全局默认。"""
        if self._active_profile is not None and getattr(self._active_profile, "player_name", ""):
            return self._active_profile.player_name
        return self._player_name

    def effective_regions(self) -> dict:
        """当前生效识别区域：优先软件档案专属配置，否则全局默认。"""
        if self._active_profile is not None and getattr(self._active_profile, "ocr_regions", None):
            regions: dict = {}
            for r in self._active_profile.ocr_regions:
                name = (r.get("name") or "").strip()
                if not name:
                    continue
                rect = [float(r.get("x", 0) or 0), float(r.get("y", 0) or 0),
                        float(r.get("w", 0) or 0), float(r.get("y2", r.get("h", 0)) or 0)]
                # 兼容字段名：h 或 y2（高度）
                rect[3] = float(r.get("h", r.get("y2", 0)) or 0)
                regions[name] = {"purpose": r.get("purpose", "general"), "rect": rect,
                                  "keywords": list(r.get("keywords", []) or [])}
            if regions:
                return regions
        return _deep_copy_regions(self._regions)

    def _match_self_kill_feed(self, text: str) -> bool:
        """击杀播报精确匹配：玩家是【击杀者】。
        - "打我变成杂鱼 淘汰了 XXX"（玩家名 + 击杀动词）
        - "你击杀了 XXX / 双杀 / 爆头" 等明确本人击杀提示
        v2 重做：不再做"左右 token 位置猜归属"（多条播报叠加时位置不可靠，
        是"没打人却说打死人"的误报根源）。玩家名允许 OCR 字间插空格
        （_norm_name_re），精确到名字本体+击杀动词，宁缺勿滥。
        """
        if not text:
            return False
        if _SELF_KILL_RE.search(text):
            return True
        pn = _norm_ocr(self.effective_player_name)
        if not pn:
            return False
        pat = re.compile(rf"{_norm_name_re(self.effective_player_name)}\s*(?:淘汰|击杀|干掉|击败|击毙)了")
        return bool(pat.search(_norm_ocr(text)))

    def _feed_mentions_player(self, text: str) -> bool:
        """击杀播报区文字是否出现玩家名（OCR 允许字间插空格）。

        三角洲行动击杀播报文字小，OCR 常扫不出"淘汰了/击杀了"动词，只剩
        "玩家名 被击杀者名"（如"打我变成杂鱼 76juesha"）。玩家名出现在击杀
        播报区只有两种可能：玩家是击杀者、玩家是被杀者。被杀者由 died 确定性
        链路（阵亡名片/观战转入/你被击杀）先行拦截，这里只作为"击杀疑似"信号，
        最终由证据门（真开枪）+ 无 died 定夺。
        """
        pn = _norm_ocr(self.effective_player_name)
        if not pn or not text:
            return False
        return bool(re.search(_norm_name_re(self.effective_player_name), _norm_ocr(text)))

    def _feed_stacked(self, text: str) -> bool:
        """击杀播报文字是否为多条播报叠加帧（≥3 个名字挤在一帧）。

        击杀播报区每帧刷新时若同时出现多条播报（连杀/队友击杀/敌人击杀同时
        滚屏），OCR 会把多个"击杀者 被击杀者"名字拼成一段：
        "想午觉呀 打我变成杂鱼 大街上乱窜的猫 低于魔王 沧澜不玩狙r 爸爸.爸爸、"
        玩家名被夹在中间，左右方位完全不可靠——【实测 00:05:52】用户被对面
        打死、玩家名恰好落在左半，被误判成"击杀者"报"这波手感真不错"。
        此时玩家名被提及既不能证明玩家是击杀者（可能在左半），也不能证明
        玩家是被杀者（可能在右半），一律不做方位归属，静默（真死亡由阵亡
        名片/你被击杀/精确 died 匹配等不依赖位置的确定性链路兜底）。

        只统计"名字样 token"（含中文/字母数字、≥2 字符、非纯数字/时间/距离、
        不含救援/淘汰等状态词）——单条播报=击杀者+被击杀者 2 个名字；
        ≥3 个才判叠加（"15 打我变成杂鱼 吗喽的救赎"里 15 是距离、只有 2 个
        名字，不判叠加）。
        """
        if not text:
            return False
        tokens = [
            t for t in re.split(r"[\s]+", text.strip())
            if len(t) >= 2
            and re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", t)
            and not re.search(r"^[\d\s.:：]+$", t)
            and not _NON_NAME_FEED_TOKEN_RE.search(t)
        ]
        return len(tokens) >= 3

    def _player_feed_side(self, text: str) -> str:
        """玩家名在击杀播报文字中的方位：'left'=发出者（击杀者/救援发起者）、
        'right'=承受者（被击杀者/被救援者）、''=玩家名未出现。

        三角洲播报格式固定为「玩家名 [枪械图标] 玩家名」（救援为救援图标），
        OCR 扫不出中间图标，文字里只剩两个名字。靠玩家名在文字中的相对位置
        判断归属：出现在左半 = 发出者，出现在右半 = 承受者。多条播报叠加时
        玩家名可能被夹在中间，方向不可靠 → 由证据门兜底（kill 必须真开枪）。
        """
        if self._feed_stacked(text):
            return ""
        pn = _norm_ocr(self.effective_player_name)
        ntext = _norm_ocr(text)
        if not pn or not ntext:
            return ""
        m = re.search(_norm_name_re(self.effective_player_name), ntext)
        if not m:
            return ""
        center = (m.start() + m.end()) / 2.0
        return "left" if center <= len(ntext) / 2.0 else "right"

    def _match_self_died_feed(self, text: str) -> bool:
        """击杀播报精确匹配：玩家是【被杀者】。
        - "你被疾风淘汰了 / 疾风 淘汰了 你 / 敌人把你淘汰了"（_SELF_DIED_PATTERNS）
        - "XXX 淘汰了 打我变成杂鱼"（击杀动词 + 玩家名）
        - "你被击杀 / 你阵亡" 等明确本人阵亡词
        v2 收紧：**不认"疾风被淘汰"**（队友/敌人被淘汰也会写成"X 被淘汰"），
        无视觉复查时宁可静默，不把别人阵亡当用户阵亡去安慰。
        阵亡名片区域（death 区域文字变化）由 _scan_once 单独处理，不重复。
        """
        if not text:
            return False
        for w in ("你被击杀", "你被淘汰", "你阵亡", "你死了",
                  "你被干掉", "你被击败", "你已阵亡", "你出局"):
            if w in text:
                return True
        for pat in _SELF_DIED_PATTERNS:
            if pat.search(text):
                return True
        pn = _norm_ocr(self.effective_player_name)
        if not pn:
            return False
        pat = re.compile(rf"(?:淘汰|击杀|干掉|击败|击毙)了\s*{_norm_name_re(self.effective_player_name)}")
        return bool(pat.search(_norm_ocr(text)))

    def _match_kill_hint(self, text: str) -> str:
        """击杀确认提示（kill_hint 区域）判定 → "kill"/"kill_bot"/""。

        用户实测（三角洲行动烽火地带）：本人击杀时屏幕中间下方弹出
        "击败 某某某兵 阿萨拉/哈夫克"。游戏机制保证该提示【只有本人击杀】才
        显示（右上角播报队友/敌人击杀也会滚动，需玩家名+证据门；这个不用）。
        - 文字含"哈夫克/阿萨拉/兵"（人机命名固定特征）→ kill_bot（人机）
        - 不含 → kill（真实玩家/干员）
        被动句式（"你被击败/被 XXX 击败"）是阵亡提示 → 返回 ""（died 链路处理）；
        结算横幅文字（撤离成功/战斗胜利）不是击杀提示 → 也返回 ""。
        """
        if not text:
            return ""
        if _KILL_HINT_PASSIVE_RE.search(text):
            return ""
        if _SETTLE_RESULT_RE.search(text):
            return ""
        if _KILL_HINT_KILL_RE.search(text):
            return "kill_bot" if _is_bot_kill_text(text) else "kill"
        return ""

    # ======== 识别区域校准（供「OCR 区域校准」悬浮窗使用） ========
    @property
    def regions(self) -> dict:
        """当前识别区域（{name: {purpose, rect}}，rect 为相对坐标 [x,y,w,h]，0~1）。"""
        return _deep_copy_regions(self._regions)

    @property
    def default_regions(self) -> dict:
        """默认识别区域（R 键恢复时用）。"""
        return _deep_copy_regions(DEFAULT_REGIONS)

    def set_regions(self, regions: dict) -> None:
        """热更新识别区域（校准悬浮窗保存时调用，无需重启，下一帧即生效）。

        regions 支持新格式 {name: {purpose, rect}} 或旧格式 {name: [x,y,w,h]}；
        支持新增/删除区域（与默认区域无关，完全以传入内容为准）。
        """
        if not isinstance(regions, dict):
            return
        new_regions: dict[str, dict] = {}
        for key, v in regions.items():
            rect, purpose = _parse_region_value(v, key)
            if rect is None:
                continue
            try:
                nv = [max(0.0, min(1.0, float(x))) for x in rect]
            except (TypeError, ValueError):
                continue
            new_regions[key] = {"purpose": purpose, "rect": nv}
        if new_regions and new_regions != self._regions:
            self._regions = new_regions
            logger.info("OCR 识别区域已热更新: %s",
                        {k: v["purpose"] for k, v in self._regions.items()})

    def add_region(self, name: str, purpose: str = "general",
                   rect: Optional[list] = None) -> bool:
        """新增一个识别区域。名字为空/重复/非法返回 False。"""
        name = (name or "").strip()
        if not name or name in self._regions:
            return False
        if purpose not in REGION_PURPOSES:
            purpose = "general"
        r = list(rect) if rect else [0.0, 0.0, 1.0, 1.0]
        try:
            r = [max(0.0, min(1.0, float(x))) for x in r[:4]]
            while len(r) < 4:
                r.append(0.0)
        except (TypeError, ValueError):
            return False
        self._regions[name] = {"purpose": purpose, "rect": r}
        logger.info("OCR 新增识别区域: %s (%s) %s", name, purpose, r)
        return True

    def remove_region(self, name: str) -> bool:
        """删除一个识别区域。"""
        if name in self._regions:
            del self._regions[name]
            logger.info("OCR 删除识别区域: %s", name)
            return True
        return False

    def set_region_purpose(self, name: str, purpose: str) -> bool:
        """修改一个识别区域的用途（kill/page/settle/hud/death/general）。"""
        if name not in self._regions or purpose not in REGION_PURPOSES:
            return False
        self._regions[name]["purpose"] = purpose
        logger.info("OCR 修改区域用途: %s → %s", name, purpose)
        return True

    # ======== 文字锚点规则（区域文字锚点训练器） ========
    @property
    def anchors(self) -> list[dict]:
        """当前锚点规则列表（深拷贝，供训练器展示/编辑）。"""
        return _deep_copy_anchors(self._anchors)

    def set_anchors(self, anchors: list) -> None:
        """热更新锚点规则（感知调试台保存时调用，无需重启，下一帧即生效）。

        anchors 为 {region, keywords, state, label} 列表；非法条目自动跳过。
        同时把命中历史清空（规则变了，旧命中不再可信）。
        """
        if not isinstance(anchors, list):
            return
        new = [a for a in (_parse_anchor(v, i) for i, v in enumerate(anchors)) if a]
        self._anchors = new
        self._anchor_hits.clear()
        self._last_anchor_label = ""
        logger.info("OCR 文字锚点规则已热更新: %d 条",
                    len([a for a in new if a.get("keywords")]))

    def match_anchors(self, region_texts: dict, full_text: str = "") -> list[dict]:
        """把各区域 OCR 文字跑一遍锚点规则，返回命中的锚点列表（已排序：先命中的在前）。

        命中逻辑：区域文字里出现该锚点的任意关键词即命中（子串匹配，OCR 文字
        有噪声/换行，用 in 判断比正则稳）。region_texts 为 {区域名: OCR文字}。
        同区域多条规则都能命中时返回全部（如"撤离成功"和"撤离失败"不会同时出现）；
        命中结果按"先出现关键词的顺序"稳定排序，供上层取最新状态。
        """
        hits: list[dict] = []
        for a in self._anchors:
            region = a.get("region", "")
            text = region_texts.get(region) or ""
            if not text:
                # 区域名失联（被用户在校准里改名/重加，实测 页面名字→页面→
                # 页面名字）：按规则记录的用途找唯一同用途区域兜底，避免
                # 规则永久失联导致"主页判断不了"。
                text = self._fallback_region_text(a, region_texts)
            if not text:
                continue
            matched = any(k in text for k in a.get("keywords") or [])
            # 关键时刻锚点（带 moment：died/downed/victory/defeat）兜底：区域
            # 没命中时再拿整图 OCR 文字匹配，根治"死亡/胜负文字落在窄区域矩形
            # 外 → 锚点不命中 → 不发 moment → 不安慰"（2026-08-25 实锤：用户
            # 阵亡但 观战/用户倒地 区域矩形太窄、没框住死亡画面文字，整图 OCR
            # 虽识别到全屏但按区域过滤后这两个锚点为空）。整图文字确定性高、
            # 不会误触发（等待救援/等待战局结束/观战/撤离成功/撤离失败 只出现
            # 在对应关键时刻画面）。
            if not matched and full_text:
                is_moment = bool(a.get("moment")) or bool(
                    self._label_to_moment(a.get("label", "")))
                if is_moment:
                    matched = any(k in full_text for k in a.get("keywords") or [])
            # 正则 patterns 兜底：对整图 OCR 文字匹配（无视区域），用于"回合
            # X/7 → 红鼠窝"这类信号——它出现在顶部 HUD，不在锚点绑定的区域里，
            # 且对局中字面"红鼠窝"被 UI 隐藏，只能靠回合计数识别（用户 2026-08-25
            # 设计：局内=模式，回合1/7 出现即代表红薯窝/红鼠窝）。
            # 注意：a["patterns"] 里是字符串，需经 _COMPILED_PATTERNS 缓存编译；
            # 绝不直接存 Pattern 对象，否则锚点字典无法 JSON 序列化（UI 卡死）。
            if not matched and full_text and a.get("patterns"):
                for pstr in a["patterns"]:
                    pat = _COMPILED_PATTERNS.get(pstr)
                    if pat is None:
                        try:
                            pat = re.compile(pstr)
                        except re.error:
                            continue
                        _COMPILED_PATTERNS[pstr] = pat
                    if pat.search(full_text):
                        matched = True
                        break
            if matched:
                hits.append(a)
        # 页面上下文互斥（用户需求：在主页就不能有对局内的识别）：
        # 菜单/主页（group=menu）命中时，对局内"结算/胜负类"（region_purpose
        # =settle，如"撤离成功/撤离失败"）全部作废——主页的导航栏/聊天/仓库
        # 文字里常出现"撤离/成功/失败"等对局词，不能因为在主页点到这些字
        # 就把"撤离成功/撤离失败"点亮。
        # 【精确化】不再移除全部 match：只移除 settle 类，保留 hud/general
        # 类（"对局中倒计时/观战"）——否则对局中右下角地图区域若仍扫到
        # "机密"（地图锚点 group=menu）会把"对局中/观战"误清除，用户的对局
        # 状态机（时间→对局中、等待救援→观战）会丢状态。
        menu_hits = [h for h in hits if h.get("group") == "menu"]
        if menu_hits:
            hits = [h for h in hits
                    if h.get("group") != "match"
                    or h.get("region_purpose") != "settle"]
        # 内置"对局中"时间锚点（hud 区域出现 MM:SS/HH:MM:SS 数字计时）：
        # 对局内左上角倒计时是纯数字，用户的关键词锚点（"撤离倒计时"）匹配不到
        # 数字，这里按时间格式直接判定，作为额外一条 match 组命中。
        t_anchor = self._time_format_anchor(region_texts)
        if t_anchor:
            hits = hits + [t_anchor]
        return hits

    def _time_format_anchor(self, region_texts: dict) -> Optional[dict]:
        """内置时间锚点：第一个 hud 区域文字含 MM:SS / HH:MM:SS → 对局中。

        只在用户锚点都匹配不到时兜底补一条（hud 区域文字通常很短，主页是
        导航文字不含数字计时，误判概率低）。返回的锚点 state 固定为
        anchor_time，label 固定为"对局中"，便于 AI 与"模式/地图"状态并列。
        """
        for name, meta in self._regions.items():
            if meta.get("purpose") != "hud":
                continue
            text = region_texts.get(name) or ""
            if _TIME_FORMAT_RE.search(text):
                return {
                    "region": name,
                    "keywords": [],
                    "state": "anchor_time",
                    "label": "对局中（倒计时在跑）",
                    "priority": 0,
                    "group": "match",
                    "region_purpose": "hud",
                }
        return None

    def _fallback_region_text(self, a: dict, region_texts: dict) -> str:
        """区域名失联时按用途（region_purpose）找唯一同用途区域的文字。

        名字失联（区域被改名/重加）后，规则若记录了用途（如 page），且当前
        恰好只有一个同用途区域，就用它——根治"主页判断不了"（页面名字→页面
        →页面名字 反复改名导致规则失联）。用途缺失或有多个同用途区域则返回空
        （宁缺勿滥，不猜）。
        """
        purpose = a.get("region_purpose", "")
        if not purpose:
            return ""
        cands = [n for n, m in self._regions.items()
                 if m.get("purpose") == purpose]
        if len(cands) == 1:
            text = region_texts.get(cands[0]) or ""
            if text:
                logger.info("锚点区域[%s]已失联，按用途自动匹配到[%s]",
                            a.get("region", ""), cands[0])
            return text
        return ""

    def set_anchor_hits(self, hits: list[dict], now: Optional[float] = None) -> None:
        """记录本次扫描命中的锚点（只更新时间戳，供去重/冷却读取）。

        hits 为 match_anchors() 的结果。只更新"已命中的"锚点时间戳——
        没命中的保持旧值（状态连续多帧成立，标签不该被短暂漏扫清掉）。
        中文页面状态描述（喂 AI）由 _apply_anchors 拼接全部命中状态。
        """
        now = now or time.time()
        if not hits:
            return
        for a in hits:
            self._anchor_hits[a.get("state", "")] = now

    def _apply_anchors(self, hits: list[dict]) -> None:
        """把锚点匹配结果应用到知识库：命中写状态，**不命中立即清空**。

        清空是"进靶场/离开主页后不再残留'在主页'"的关键——旧行为只靠知识库
        15s 新鲜度窗口，持续误命中会被一直续命，AI 就一直以为在主页。

        但**模式/地图/难度**这类"本局开局情报"不能跟着清空：它们只在主页/
        选点界面有字，进对局后区域不再命中，清空后 AI 对局中问模式就完全
        不知情（用户实测答非所问）。这类锚点（menu+page 组、label 非"在主页"
        开头）命中后进入 _held_anchor_labels，保留 _HOLD_ANCHOR_TTL 秒，
        对局期间持续拼进喂 AI 的状态。新对局回主页重新识别会覆盖旧情报。
        """
        now = time.time()
        # 主页/菜单标记：右上角 kill 区域在主页扫到的是商城活动角标
        # （"捆绑包/角券补贴"），不是击杀播报——变化检测跳过，避免每帧刷日志
        self._menu_page_active = any(
            a.get("group") == "menu" for a in hits)
        # 1) 本帧命中里收集"会话情报"类锚点（模式/地图/难度）进保持列表
        held: list[dict] = []
        for a in hits:
            if (a.get("group") == "menu"
                    and a.get("region_purpose") == "page"):
                label = (a.get("label") or "").strip()
                if label and not label.startswith("在主页"):
                    held.append({"label": label, "ts": now})
        # 2) 已有保持项未过期则续留
        held.extend(h for h in self._held_anchor_labels
                    if now - h["ts"] <= _HOLD_ANCHOR_TTL)
        # 去重（同 label 保留最新）
        seen: dict[str, dict] = {}
        for h in held:
            seen[h["label"]] = h
        self._held_anchor_labels = list(seen.values())

        if hits or self._held_anchor_labels:
            # 本帧命中的所有锚点：全部拼接喂 AI（用户的对局状态机要求 AI
            # 同时知道"对局中 + 模式：摸金 + 地图：长弓溪谷"，而不是只取
            # 优先级最高的一个）。按优先级降序排列（最具体的在前），
            # 去重保序，避免同 label 多区域重复。
            ordered = sorted(
                hits, key=lambda a: int(a.get("priority", 0) or 0), reverse=True)
            labels = [(a.get("label") or "").strip() for a in ordered]
            labels = [l for l in labels if l]
            held_labels = [h["label"] for h in sorted(
                self._held_anchor_labels, key=lambda h: -h["ts"])]
            full = list(dict.fromkeys(labels + held_labels))
            self._last_anchor_labels = labels
            self._last_anchor_states = [a.get("state", "") for a in ordered]
            self._last_anchor_label = "、".join(full)
            self.set_anchor_hits(hits)
            if self._game_knowledge is not None:
                try:
                    self._game_knowledge.update_anchor_state(self.anchor_state)
                except Exception:  # noqa: BLE001 老版本知识库无此接口时忽略
                    pass
            logger.info("OCR文字锚点命中: %s", self.anchor_state)
        else:
            self._last_anchor_label = ""
            self._last_anchor_labels = []
            self._last_anchor_states = []
            if self._game_knowledge is not None:
                try:
                    self._game_knowledge.update_anchor_state("")
                except Exception:  # noqa: BLE001
                    pass

        # ===== 锚点强绑定（2026-08-25）：识别到关键时刻 → 立即触发 AI 互动 =====
        # 用户设计意图：OCR 锚点识别到（"已死亡正在观战别人操作"/"用户倒地濒死"/
        # "撤离成功"/"撤离失败"）= 必须回应，不再只写上下文背景。本段把带 moment
        # 的锚点命中转成 moment 事件，复用 _moment_last 冷却与旧关键词引信/观战
        # 状态机天然去重（不会双发）。上升沿：仅"未命中→命中"瞬间发布，持续命中
        # 不刷屏；消失后复位，下次再命中可再发（如被救起→再倒地）。
        # 持续状态重发：downed/died 这类"持续状态"（一直倒地等待救援/阵亡观战）
        # 在首发后，仍按 _reemit_cooldown_s 周期重发，确保持续期间持续陪伴互动。
        active_moments: dict[str, str] = {}  # moment → label（同 moment 取首个命中）
        for a in hits:
            m = a.get("moment") or self._label_to_moment(a.get("label", ""))
            if m:
                active_moments.setdefault(m, a.get("label", ""))
        # 优先级：死亡/胜负（终态）覆盖"倒地"（过程态）。用户既"倒地濒死"又被
        # 确认"已死亡正在观战别人操作"时，应表达"你阵亡了"而非重复"别慌趴好别动"
        # （原反应链每次只取一个 moment，downed 因持续命中总盖过 died，导致阵亡后
        # AI 始终只念倒地话术、从不说你挂了——用户 2026-08-26 实测反馈）。
        if "died" in active_moments and "downed" in active_moments:
            active_moments.pop("downed")
        if "victory" in active_moments and "downed" in active_moments:
            active_moments.pop("downed")
        if "defeat" in active_moments and "downed" in active_moments:
            active_moments.pop("downed")
        for m, lbl in active_moments.items():
            # 用户已阵亡进入观战（等待对局结束）→ 此时画面里若还闪出"用户倒地濒死"
            # 是大字残留/误读，与"已死"矛盾，绝不该再说"别慌趴好别动队友来拉你"
            # （2026-08-26 实锤：死后仍刷"别慌"体感错乱）。died 已被上面优先级剔除
            # downed，这里兜底：观战状态下彻底忽略 downed 上升沿。
            if m == "downed" and self._waiting_match_end:
                continue
            if not self._anchor_moment_active.get(m):
                # 上升沿：未命中→命中瞬间发布（首次/被救起后再次倒地等）
                self._pending_moments.append(
                    {"moment": m, "label": lbl, "ocr_text": self._last_anchor_label})
            elif m in self._reemit_moments and \
                    now - self._moment_last.get(m, 0.0) >= self._reemit_cooldown_s:
                # 持续命中（如一直倒地等待救援/阵亡观战中）：按冷却周期重发，
                # 避免"识别到只互动一次"后长期沉默（用户拍板：识别到就该互动）
                self._pending_moments.append(
                    {"moment": m, "label": lbl, "ocr_text": self._last_anchor_label})
        # 复位：本帧未命中的 moment 清除活跃标记，下次命中重新触发
        for m in list(self._anchor_moment_active.keys()):
            if m not in active_moments:
                self._anchor_moment_active.pop(m, None)
        for m in active_moments:
            self._anchor_moment_active[m] = True

    @staticmethod
    def _label_to_moment(label: str) -> str:
        """锚点 label → moment 类型兜底映射（config 未显式写 moment 字段时用）。"""
        if not label:
            return ""
        if "已死亡" in label or "观战" in label:
            return "died"
        if "倒地" in label or "濒死" in label:
            return "downed"
        if "撤离成功" in label:
            return "victory"
        if "撤离失败" in label:
            return "defeat"
        return ""

    async def _emit_anchor_moment(self, moment: str, label: str, ocr_text: str) -> None:
        """锚点强绑定发布：OCR 锚点识别到关键时刻 → 立即发布对应 moment 事件触发
        AI 互动。与关键词引信(_publish)/观战状态机共享 _moment_last 冷却去重。"""
        now = time.time()
        self._moment_last[moment] = now
        summary = f"{label}（锚点识别）"
        activity = {
            "summary": summary,
            "app_hint": "game",
            "confidence": 0.95,
            "moment": moment,
            "source": "ocr",
            "ocr_text": (ocr_text or "")[:120],
        }
        self._recent.append({
            "ts": now, "moment": moment, "keyword": label,
            "summary": summary, "source": "ocr",
            "ocr_text": (ocr_text or "")[:80],
        })
        self._recent = self._recent[-10:]
        logger.info("锚点强绑定触发[%s]: %s (文字=%s)", moment, label, (ocr_text or "")[:60])
        await self._bus.publish(self._screen_event(activity))

    @property
    def anchor_state(self) -> str:
        """最近命中锚点的中文描述（喂 AI 的页面状态），未命中返回空串。"""
        return self._last_anchor_label

    def calibration_state(self) -> dict:
        """供校准悬浮窗使用的状态快照。

        - mode：'meeting'（手机屏幕共享）/ 'pc_game' / 'none'
        - reference：区域参照矩形 (left, top, w, h)——PC 为游戏窗口矩形
          （从同屏窗口里找 hint=='game' 的窗口，用户点校准按钮时前台已切到
          软件，用当前前台会拿到错的参照），手机共享为腾讯会议窗口中央
          裁剪条（PHONE_CENTER_CROP）。识别区域的相对坐标基于它换算。
        - monitor_bounds：OCR 所在监视器的屏幕范围（悬浮窗覆盖范围）
        """
        phone = self._is_phone_share()
        mode = "none"
        ref: Optional[tuple] = None
        if self._monitor is not None:
            if phone:
                rect = self._monitor.meeting_rect()
                if rect:
                    left, top, w, h = rect
                    cx, cy, cw, ch = PHONE_CENTER_CROP
                    ref = (left + int(w * cx), top + int(h * cy),
                           max(1, int(w * cw)), max(1, int(h * ch)))
                    mode = "meeting"
            else:
                # PC 版：优先找同屏里的游戏窗口（点校准按钮时前台已是软件窗口，
                # foreground_rect 会拿到软件矩形，参照就错了）；找不到再退回前台。
                rect = None
                try:
                    for win in self._monitor.visible_windows():
                        if win.get("hint") == "game" and not win.get("is_meeting") \
                                and win.get("rect"):
                            rect = win["rect"]
                            break
                except Exception:  # noqa: BLE001
                    rect = None
                if not rect:
                    rect = self._monitor.foreground_rect()
                if rect:
                    ref = rect
                    mode = "pc_game"
        bounds = None
        try:
            sct = self._ensure_sct()
            mons = sct.monitors
            idx = min(self._monitor_index, len(mons) - 1)
            m = mons[idx]
            bounds = (m["left"], m["top"], m["width"], m["height"])
        except Exception:  # noqa: BLE001
            pass
        return {
            "mode": mode,
            "monitor_index": self._monitor_index,
            "monitor_bounds": bounds,
            "reference": ref,
            "waiting_match_end": self._waiting_match_end,
            "spectating": self._spectating,
            "regions": _deep_copy_regions(self._regions),
            "defaults": _deep_copy_regions(DEFAULT_REGIONS),
            "phone_crop": list(PHONE_CENTER_CROP),
        }

    def _next_interval(self) -> float:
        return max(0.5, self._interval * random.uniform(1.0 - self._jitter, 1.0 + self._jitter))

    def _print_window_image(self, hwnd: int) -> Optional[Image.Image]:
        """PrintWindow 后台抓取指定窗口内容（即使被其他窗口完全遮挡）。

        对标"切换屏幕后仍能识别游戏"：前台切到软件/IDE 后，游戏窗口还在但被
        遮挡，mss 抓屏幕像素只能抓到遮挡它的窗口内容。PrintWindow 直接从窗口
        交换链取渲染内容，不经过屏幕合成，被遮挡也能拿到游戏画面。

        限制：窗口最小化时引擎不渲染（调用方 visible_windows 已过滤 IsIconic）；
        全屏独占渲染的窗口可能返回黑屏（Win10+ 无边框/窗口化大多可用）。
        返回 PIL Image 或 None（失败/黑屏）。
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            if not hwnd or not user32.IsWindow(hwnd):
                return None
            if user32.IsIconic(hwnd):  # 最小化不渲染，抓不到
                return None
            rc = wintypes.RECT()
            if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
                return None
            w, h = rc.right, rc.bottom
            if w <= 0 or h <= 0 or w > 8192 or h > 8192:
                return None
            hdc = user32.GetDC(hwnd)
            if not hdc:
                return None
            mem = bmp = None
            try:
                mem = gdi32.CreateCompatibleDC(hdc)
                bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
                if not mem or not bmp:
                    return None
                old = gdi32.SelectObject(mem, bmp)
                # PW_RENDERFULLCONTENT=2：让 DWM 合成完整窗口内容（含被遮挡部分）
                ok = user32.PrintWindow(hwnd, mem, 2)
                gdi32.SelectObject(mem, old)
                if not ok:
                    return None

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", wintypes.DWORD),
                        ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD),
                    ]

                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = w
                bmi.biHeight = -h  # 负值 → 自顶向下，免翻转
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0  # BI_RGB
                buf = ctypes.create_string_buffer(w * h * 4)
                if not gdi32.GetDIBits(mem, bmp, 0, h, buf,
                                       ctypes.byref(bmi), 0):
                    return None
                return Image.frombytes("RGB", (w, h), buf.raw, "raw", "BGRX")
            finally:
                if bmp:
                    gdi32.DeleteObject(bmp)
                if mem:
                    gdi32.DeleteDC(mem)
                user32.ReleaseDC(hwnd, hdc)
        except Exception:  # noqa: BLE001
            logger.debug("PrintWindow 后台抓取失败", exc_info=True)
            return None

    def _extract_text(self, result) -> str:
        """RapidOCR 返回 (result, elapse)，result 为 [box, text, score] 列表。"""
        if not result or not result[0]:
            return ""
        lines = []
        for item in result[0]:
            try:
                text = str(item[1]).strip()
                if text:
                    lines.append(text)
            except Exception:  # noqa: BLE001
                continue
        return " ".join(lines)

    @staticmethod
    def _match_moment(text: str) -> tuple[Optional[str], str]:
        """匹配关键词，返回 (moment, 命中关键词)。先匹配到先命中。"""
        lower = text.lower()
        # 被击杀正则优先：避免"被敌人X击杀"中间夹名字时被误判成击杀
        for pat in DIED_PATTERNS:
            m = pat.search(text)
            if m:
                return "died", m.group(0)
        # 击杀引信（收紧）：明确玩家本人击杀 / 击杀播报格式
        for pat in KILL_PATTERNS:
            m = pat.search(text)
            if m:
                return "kill", m.group(0)
        for moment, keywords in KEYWORD_RULES:
            for kw in keywords:
                if kw.lower() in lower:
                    return moment, kw
        return None, ""

    def _in_match(self) -> bool:
        """OCR 确认对局中（screen_page()=="match"）才允许发布击杀时刻。

        【对局中权威（2026-08-12 用户规则）】性能面板/靶场统计里的"爆头/击杀"
        文字（01:34:11 实锤：画面是性能面板"NVIDIA浮窗 帧率/温度"旁却被读成
        击杀播报）在非对局中画面也会出现——击杀时刻只在 OCR 确认对局中
        （撤离倒计时/档位/回合 X/7，像素真文字）才发布，主页/靶场/大厅一律
        静默。阵亡（died）不受限：真死亡来自阵亡名片/观战转入，与页面无关。
        """
        gk = self._game_knowledge
        if gk is None:
            return True  # 未接线（测试/知识库不可用）：不拦，由上层 moment 门禁兜底
        try:
            return gk.screen_page() == "match"
        except Exception:  # noqa: BLE001
            logger.debug("对局中门禁判断异常，默认放行")
            return True

    def _spectator_state(self, text: str) -> str:
        """观战/死亡画面状态机：返回当前画面状态（=玩家是否已阵亡）。

        - "none"：非死亡/观战画面（正常游戏/大厅），恢复正常时刻检测
        - "enter"：刚进入死亡观战画面 = 玩家刚阵亡，应报一次 died
        - "stay"：持续处于死亡观战画面，画面里一切击杀/胜负都是队友/敌人操作，静默

        三角洲死亡 UI 约定（2026-08-25 用户澄清，关键修正）：
        - 烽火地带（正常模式）：玩家死亡后左下角常驻「退出观战」（含"观战"二字）、
          中间显示「等待队友救援」或「等待对局结束」；**队友救起后这些字消失、
          直接回到正常游戏**（救起时屏幕不显示"退出观战"）。
          ⇒ "退出观战"是「已死」标记（不是被救信号），靠 "观战" in text 命中进入
          状态；原逻辑此分支本就正确，不可误判为 none。
        - 红薯窝（红鼠窝/突围）：死亡后显示「等待对局结束」，确定不复活
          （被补一命无法复活）。该词**不含"观战"二字**，原逻辑会漏判，必须显式补入。
        - 被救起/对局结束：画面回到正常游戏或大厅，无上述死亡词 → none →
          解除『等待对局结束』状态（waiting 不会卡死）。
        """
        # 死亡状态词（用户已死 → 进入/保持『等待对局结束』状态）：
        # "观战" 覆盖 观战/观战中/退出观战（烽火地带死亡 UI）；
        # "等待对局结束" / "等待战局结束" 覆盖红鼠窝/红薯窝死亡画面——
        #   实测屏幕显示"等待战局结束，准备下一回合"（"战局"≠"对局"，
        #   原只写"对局"会漏；红鼠窝为回合制，死了还能进下一回合，仍属已死）。
        now_spectating = ("观战" in text
                          or "等待对局结束" in text
                          or "等待战局结束" in text)
        if now_spectating:
            if self._spectating:
                return "stay"
            self._spectating = True
            return "enter"
        self._spectating = False
        return "none"

    def _set_waiting_match_end(self, on: bool) -> None:
        """设置/解除『等待对局结束』状态（玩家阵亡进入观战 → 等待本局结束）。

        - on=True：玩家已阵亡、正在观战等待本局结束。每轮把
          "用户已阵亡，等待对局结束" 作为 held 锚点喂给 AI（与模式/地图情报
          并列），让 AI 知道当前不是正常对局、不要聊"刚才那波"/当前战斗；
          并在日志标记进入『等待对局结束』。
        - on=False：对局已结束（退出观战/回到大厅），解除状态并移除该锚点，
          后续胜利/失败/结算判定恢复正常。
        """
        label = "用户已阵亡，等待对局结束"
        if on:
            if not self._waiting_match_end:
                logger.info("观战命中→玩家阵亡，进入[等待对局结束]状态（持续告知AI）")
            self._waiting_match_end = True
            # 每轮刷新 held 锚点时间戳，保持 _HOLD_ANCHOR_TTL 内持续可见
            self._held_anchor_labels = [
                h for h in self._held_anchor_labels if h.get("label") != label]
            self._held_anchor_labels.append({"label": label, "ts": time.time()})
        else:
            if self._waiting_match_end:
                logger.info("[等待对局结束]状态解除→对局已结束（退出观战/回到大厅）")
            self._waiting_match_end = False
            self._held_anchor_labels = [
                h for h in self._held_anchor_labels if h.get("label") != label]

    def _grab_image(self) -> Optional[Image.Image]:
        if self._frame_source == "video":
            return self._grab_video_frame()
        """抓一帧游戏窗口区域（等比缩小到 max_width）。

        游戏窗口化/无边框时只抓窗口矩形，避免桌面图标/任务栏/其他窗口文字
        混进 OCR（如桌面"无畏契约"图标被误识别，导致上下文串行到别的游戏）；
        全屏游戏（矩形=整屏）或窗口矩形获取失败时回退抓整屏。
        手机屏幕共享（腾讯会议）：游戏画面在腾讯会议窗口里、前台可能已切走，
        优先抓腾讯会议窗口矩形，抓不到才回退前台窗口/整屏。
        **后台识别**：前台非游戏但 PC 游戏窗口还在（被遮挡/切走），用 PrintWindow
        直接抓游戏窗口交换链内容——mss 抓屏幕像素只能抓到遮挡它的窗口。
        """
        sct = self._ensure_sct()
        monitors = sct.monitors
        idx = min(self._monitor_index, len(monitors) - 1)
        region = monitors[idx]
        phone = self._is_phone_share()
        # 后台识别：前台非游戏、但 PC 版游戏窗口还在（被遮挡/切走后持续识别）。
        # 手机共享场景不走这里（会议窗口矩形优先，手机画面在会议里）
        if not phone and self._monitor is not None \
                and not self._monitor.is_game_foreground():
            gw = getattr(self._monitor, "pc_game_window", None)
            if callable(gw):
                info = gw()
                if info and info.get("hwnd"):
                    img = self._print_window_image(info["hwnd"])
                    if img is not None:
                        if img.width > self._max_width:
                            ratio = self._max_width / img.width
                            img = img.resize((self._max_width,
                                              max(1, int(img.height * ratio))))
                        return img
        # 手机共享场景：直接定位腾讯会议窗口（用户在手机上打游戏，前台常是其他窗口）
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
                # 窗口矩形与当前监视器区域求交，只抓重叠部分（多显示器时窗口可能跨屏）
                x0 = max(left, m["left"])
                y0 = max(top, m["top"])
                x1 = min(left + w, m["left"] + m["width"])
                y1 = min(top + h, m["top"] + m["height"])
                if x1 > x0 and y1 > y0:
                    region = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        raw = sct.grab(region)
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
        # 手机屏幕共享（腾讯会议）：竖屏手机画面居中显示、四周是黑边和会议 UI，
        # 裁剪中央区域去掉无关内容，并放大 2 倍——游戏内击杀/阵亡小字经过放大
        # 后才能被 OCR 识别到（否则 OCR 扫到的基本是"会议详情"这类会议 UI 文字）
        if self._is_phone_share():
            w, h = img.size
            cx, cy, cw, ch = PHONE_CENTER_CROP
            img = img.crop((int(w * cx), int(h * cy),
                            int(w * (cx + cw)), int(h * (cy + ch))))
            if img.width < 1280:
                ratio = 1280 / img.width
                img = img.resize((1280, max(1, int(img.height * ratio))),
                                 resample=Image.LANCZOS)
        if img.width > self._max_width:
            ratio = self._max_width / img.width
            img = img.resize((self._max_width, max(1, int(img.height * ratio))))
        return img

    def _grab_video_frame(self) -> Optional[Image.Image]:
        """视频测试源：从 cv2.VideoCapture 读视频代替真实屏幕。

        做了两件关键优化，让长视频也能跑：
        1. **抽帧**：游戏 UI 文字变化慢，无需逐帧 OCR。按 _video_sample_sec 计算
           keep_every（每 N 帧抽 1 帧），本函数内部把非抽样帧 read 后丢弃，只把
           抽样帧交给 OCR——30fps 视频默认提速 ~60 倍，关键时刻(击杀/胜负屏停留
           数秒)不会漏。
        2. **视频时间轴**：用 CAP_PROP_POS_MSEC 记录当前帧在视频中的真实时间(ms)，
           供报告把"相对时间"标成视频内位置（而非程序处理耗时），长视频才有意义。

        返回 PIL Image（RGB，已缩放到 max_width）；视频播放完毕返回 None
        并置 self._video_eof=True，由 run() 循环据此退出。
        """
        import os
        import cv2
        from PIL import Image
        if self._video_cap is None:
            if not self._video_path or not os.path.exists(self._video_path):
                logger.error("视频测试源错误: 文件不存在 %r", self._video_path)
                self._video_eof = True
                return None
            self._video_cap = cv2.VideoCapture(self._video_path)
            if not self._video_cap.isOpened():
                logger.error("视频测试源打开失败: %r", self._video_path)
                self._video_eof = True
                return None
            fps = self._video_cap.get(cv2.CAP_PROP_FPS) or 0.0
            nframes = int(self._video_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            total_ms = (nframes / fps * 1000.0) if fps > 0 else 0.0
            self._video_total_ms = total_ms
            # 抽帧：每 keep_every 帧抽 1 帧做 OCR（FPS 越高抽得越狠）
            if fps > 0:
                self._video_keep_every = max(1, int(round(fps * self._video_sample_sec)))
            else:
                self._video_keep_every = 1
            logger.info("视频测试源已打开: %s (FPS=%.2f, 总帧数=%d, 时长=%.1fs, "
                        "抽帧每 %d 帧取 1 帧[约 %.2fs])",
                        self._video_path, fps, nframes, total_ms / 1000.0,
                        self._video_keep_every, self._video_sample_sec)
        # 抽帧：read 直到命中抽样帧（idx-1 能被 keep_every 整除 → 第1/1+N/1+2N…帧保留）
        while True:
            ok, frame = self._video_cap.read()
            if not ok:
                logger.info("视频测试源已到末尾 (共读取 %d 帧, 抽样 %d 帧)",
                            self._video_frame_idx,
                            self._video_frame_idx // max(1, self._video_keep_every) + 1)
                self._video_eof = True
                return None
            self._video_frame_idx += 1
            pos_ms = self._video_cap.get(cv2.CAP_PROP_POS_MSEC)
            self._video_cur_time_ms = float(pos_ms) if pos_ms else \
                (self._video_frame_idx / max(self._video_keep_every, 1)
                 * self._video_keep_every / max(
                     self._video_cap.get(cv2.CAP_PROP_FPS) or 1.0, 1.0) * 1000.0)
            if (self._video_frame_idx - 1) % self._video_keep_every == 0:
                break  # 命中抽样帧，交给 OCR
            # 非抽样帧：丢弃，继续读下一帧（capture 已正确前进）
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if img.width > self._max_width:
            ratio = self._max_width / img.width
            img = img.resize((self._max_width, max(1, int(img.height * ratio))))
        return img

    def _screen_event(self, activity: dict):
        """统一构造 SCREEN_ACTIVITY 事件；视频源下注入视频时间戳供报告对齐。"""
        if self._frame_source == "video":
            activity = dict(activity)
            activity["video_time_ms"] = self._video_cur_time_ms
        return Event(EventType.SCREEN_ACTIVITY, activity, "ocr")

    def _grab_corner(self) -> Optional[Image.Image]:
        """手机屏幕共享时，额外抓腾讯会议窗口【左上角】区域（页面名/档位所在）。

        主抓屏（_grab_image）在手机共享时只裁剪中央 0.28~0.72 区域——为了去掉
        会议黑边和 UI，但游戏画面左上角的页面名（仓库/交易行/邮件/商城）与档位
        （普通/机密/绝密）恰好落在裁剪区外，OCR 永远扫不到，导致 screen_page()
        识别不出"在主页/对局中"——用户"开局看仓库被当搜物资""对局内就说我赢了"
        两条误报都靠左上角文字校正。这里单独抓左上角 45%×50% 区域并放大，
        让页面名/档位能进 OCR。非手机共享（PC 版游戏窗口）不需要——整窗抓取
        本就包含左上角。
        """
        if not self._is_phone_share():
            return None
        sct = self._ensure_sct()
        monitors = sct.monitors
        idx = min(self._monitor_index, len(monitors) - 1)
        m = monitors[idx]
        getter = getattr(self._monitor, "meeting_rect", None)
        if not callable(getter):
            return None
        rect = getter()
        if not rect:
            return None
        left, top, w, h = rect
        cw = max(40, int(w * 0.45))
        ch = max(40, int(h * 0.5))
        x0 = max(left, m["left"])
        y0 = max(top, m["top"])
        x1 = min(left + cw, m["left"] + m["width"])
        y1 = min(top + ch, m["top"] + m["height"])
        if x1 <= x0 or y1 <= y0:
            return None
        region = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        raw = sct.grab(region)
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")
        if img.width < 800:
            ratio = 800 / img.width
            img = img.resize((800, max(1, int(img.height * ratio))),
                             resample=Image.LANCZOS)
        return img

    def _regions_map(self) -> dict:
        """当前识别区域映射 {name: rect}（兼容测试直接用 __new__ 构造的实例）。"""
        regions = getattr(self, "_regions", None) or DEFAULT_REGIONS
        return {k: v.get("rect") if isinstance(v, dict) else v
                for k, v in regions.items()}

    def _region_purpose(self, name: str) -> str:
        """查询区域用途（kill/page/settle/hud/general）。"""
        r = (getattr(self, "_regions", None) or DEFAULT_REGIONS).get(name)
        if isinstance(r, dict):
            return r.get("purpose", "general")
        return "general"

    def _grab_region(self, name: str, img: Image.Image,
                     min_w: int = 800, scale: float = 1.5) -> Optional[Image.Image]:
        """从主抓屏图中按区域名切出识别区域（不重新抓屏）。

        复用 _grab_image 已抓好的主图（手机共享时已裁剪中央竖屏画面并放大）。
        切出 {name} 区域并放大（小字才能被 RapidOCR 扫到），区域位置/大小由
        self._regions[name]["rect"] 决定，可在「OCR 区域校准」悬浮窗里拖动调整。
        """
        if img is None:
            return None
        rect = self._regions_map().get(name)
        if not rect or len(rect) != 4:
            return None
        x, y, w, h = rect
        W, H = img.size
        x0, y0 = int(W * x), int(H * y)
        x1, y1 = int(W * (x + w)), int(H * (y + h))
        if x1 <= x0 or y1 <= y0:
            return None
        band = img.crop((x0, y0, x1, y1))
        target_w = max(min_w, int(band.width * scale))
        band = band.resize(
            (target_w, max(1, int(band.height * target_w / band.width))),
            resample=Image.LANCZOS)
        return band

    def _grab_top_hud(self, img: Image.Image) -> Optional[Image.Image]:
        """从主抓屏图中切出【顶部 HUD】区域（回合 X/7、比分、撤离倒计时所在）。

        **不重新抓屏**：直接复用 _grab_image 已抓好的主图（手机共享时已裁剪
        中央竖屏手机画面并放大）。回合 X/7（红鼠窝）和撤离倒计时（烽火地带）
        都渲染在手机画面上部，从主图顶部切一条横带放大即可让 OCR 扫到——
        否则主图整图 OCR 时顶部小字（回合数字）常因太小识别不了，模式/对局
        判定就全靠视觉模型猜（"回合 X/7" 是红鼠窝 100% 确定性信号，OCR 能扫到
        就锁死模式，视觉怎么猜都改不掉）。主图顶部可能含会议黑边，但对 OCR
        无影响（黑边无文字）。区域位置由 self._regions["top_hud"] 决定，
        可在「OCR 区域校准」悬浮窗里拖动调整。
        """
        return self._grab_region("top_hud", img, min_w=800, scale=1.0)

    def _grab_bottom_hud(self, img: Image.Image) -> Optional[Image.Image]:
        """从主抓屏图中切出【底部信息区】区域（击杀播报/结算横幅所在）。

        击杀播报、结算横幅（撤离成功/失败、战斗胜利/失败）常渲染在画面中下部/
        底部。从主图底部切一条横带放大，让这类短时提示文字能进 OCR——它们
        是"关键时刻引信"，OCR 扫到 → 多模态裁决，比视觉模型在模糊小屏上
        脑补"击杀了敌人"可靠。底部可能含会议 UI/黑边，OCR 无影响。
        区域位置由 self._regions["bottom_hud"] 决定，可校准。
        """
        return self._grab_region("bottom_hud", img, min_w=800, scale=1.5)

    def _grab_top_right_hud(self, img: Image.Image) -> Optional[Image.Image]:
        """从主抓屏图中切出【右上角】区域（击杀播报所在）。

        用户实测：三角洲行动 PC 版击杀播报渲染在右上角（温度帧率录屏水印
        之下、屏幕右侧顶部约 1/3 处，即 x≈45%-100%、y≈0%-35%）。主图整图
        OCR 时该区域小字识别不了，切出右上一带放大 2.5 倍即可扫到
        "你击杀了 XXX / XXX 淘汰了 YYY"（击杀引信 → 多模态 AI 分辨谁杀的）。
        手机共享时主图已裁剪中央手机画面，右上角=手机画面右上角，同样适用。
        区域位置由 self._regions["top_right_hud"] 决定，可校准。
        """
        return self._grab_region("top_right_hud", img, min_w=1000, scale=2.5)

    def _frame_jpeg(self) -> Optional[bytes]:
        """按需编码当前帧为 JPEG（懒：仅在发布时刻时调用，且每轮只编码一次）。

        替代原先每轮无条件编码整图的开销。返回 None 表示无可用帧。
        """
        if self._last_img is None:
            return None
        if self._frame_jpeg_cache is None:
            b = io.BytesIO()
            self._last_img.save(b, format="JPEG", quality=85)
            self._frame_jpeg_cache = b.getvalue()
        return self._frame_jpeg_cache

    def _ocr_full_frame(self, img):
        """整图一次 OCR（替代逐区域 10+ 次调用 → 根治 4~6s 慢扫描）。

        RapidOCR 返回 (result, elapse)，result=[[box, text, score], ...]，
        box 为 4 点绝对坐标（基于传入图 img，已是 max_width 宽）。本函数一次
        性拿到全屏所有文字框，返回 [(cx, cy, text)]（框中心点+文字，坐标基于
        img 原尺寸）。_scan_once 再按各 region 矩形过滤，把文字分回对应区域——
        10 个区域只需 1 次 ocr() 调用，性能降一个数量级，且每区域照样覆盖不漏
        （触发词都是游戏大字 UI，整图分辨率足够）。
        """
        ocr = self._ensure_ocr()
        if ocr is None:
            return []
        try:
            from PIL import ImageOps
            simg = img.convert("L")
            simg = ImageOps.autocontrast(simg, cutoff=2)
        except Exception:  # noqa: BLE001
            simg = img.convert("L")
        buf = io.BytesIO()
        simg.save(buf, format="PNG")
        res = ocr(buf.getvalue())
        out = []
        if not res or not res[0]:
            return out
        for item in res[0]:
            try:
                box = item[0]
                text = item[1]
                if not text or box is None:
                    continue
                cx = sum(p[0] for p in box) / len(box)
                cy = sum(p[1] for p in box) / len(box)
                t = str(text).strip()
                if t:
                    out.append((cx, cy, t))
            except Exception:  # noqa: BLE001
                continue
        return out

    def _ocr_band_to_text(self, band):
        """单图（已裁剪放大）OCR → 文字。供仍需放大的小字区域复用。"""
        if band is None:
            return ""
        ocr = self._ensure_ocr()
        if ocr is None:
            return ""
        try:
            from PIL import ImageOps
            bimg = band.convert("L")
            bimg = ImageOps.autocontrast(bimg, cutoff=2)
        except Exception:  # noqa: BLE001
            bimg = band.convert("L")
        buf = io.BytesIO()
        bimg.save(buf, format="PNG")
        res = ocr(buf.getvalue())
        return self._extract_text(res)

    async def _scan_once(self, throttle: bool = False) -> None:
        """抓屏 → 区域 OCR → 精确匹配 + 输入证据门 → 发布 moment。

        v2 架构：不再调多模态看截图裁决（视觉只描述画面，不参与时刻判定）。
        - kill：玩家名+击杀动词精确命中 【且】 证据门通过（开了枪）→ 才报
        - died：阵亡名片 / "你被击杀" / 玩家名作为被杀者 / 观战转入 → 纯 OCR 确定性
        - victory/defeat：结算横幅精确文字 + "胜负未达成"排除
        - 无法确认 → 静默
        """
        # 校准悬浮窗打开中：悬浮窗是**置顶透明窗**，它的 UI 文字（区域标签
        # "tophud（顶部HUD）""页面名字"、提示文字）会被 mss 连游戏画面一起抓进
        # OCR → 锚点命中"在主页"之类的假状态（13:28 实锤：用户在校准，OCR 扫到
        # "开始游戏 仓库 交易行 改枪台"主页文字 + 悬浮窗标签，进靶场后还一直显示
        # "在主页"）。校准期间直接暂停扫描；关闭时 set_calibrating(False) 重建
        # mss 实例 + 清空锚点状态后恢复。
        if self._calibrating:
            return
        self._scan_count += 1
        # 窗口识别器（腾讯会议白名单）：确认当前屏幕上的"游戏画面"来源。
        # 抖音/浏览器里出现"三角洲行动"字样只是视频/文章内容，不是用户真在玩
        # （昨天实锤：看抖音三角洲视频触发了游戏识别）——source=='none' 时
        # 跳过本帧：不抓屏、不 OCR、不更新画面文字、不触发游戏识别。
        if self._frame_source != "video" and self._monitor is not None:
            try:
                src = self._monitor.game_screen_source()
                if src == "none":
                    logger.debug("无游戏画面来源（前台非游戏、无腾讯会议投屏），跳过 OCR")
                    return
            except Exception:  # noqa: BLE001
                pass
        # 【2026-09-07 整合·逐游戏 OCR 调优】每轮扫描前按前台 exe 解析软件档案：
        # 若该游戏档案 mode=snapshot 且配置了 ocr_regions/player_name，则本轮回退
        # 用该游戏的专属 OCR 配置（覆盖全局 config.yaml perception.ocr 默认值）。
        # 无档案 / 非快照 → 用全局默认；窗口切换游戏自动切换配置，无需重启。
        self._resolve_active_profile()
        # 会议置顶窗口遮挡检测：PC 游戏前台时若置顶的腾讯会议窗口盖住了游戏抓屏
        # 区域（共享画面/会议 UI 悬浮在游戏上），mss 抓到的像素是会议窗口内容，
        # OCR 只会读到会议 UI 文字，还会盖住页面锚点区域（主页判断不了）。
        # 直接跳过本帧（抓了也是错的内容），节流告警一次 + 调试台提示。
        if self._frame_source == "video":
            self._occluded = False
        else:
            try:
                self._occluded = self._meeting_occludes_game()
            except Exception:  # noqa: BLE001
                self._occluded = False
            if self._occluded:
                self._occlusion_skips += 1
                now = time.time()
                if now - self._last_occl_warn > 10:
                    logger.warning(
                        "腾讯会议置顶窗口遮挡游戏画面，OCR 跳过本帧（已跳过 %d 帧）："
                        "请把会议/共享窗口移到副屏、最小化或取消置顶，OCR 才会读到游戏文字",
                        self._occlusion_skips)
                    self._last_occl_warn = now
                return
        img = await asyncio.to_thread(self._grab_image)
        if img is None:
            return

        # 画面真正静止检测（最早拦截，在 JPEG 编码/OCR 预处理之前）：
        # phash 变化极小 → 画面冻结/残留（游戏加载说明、切走后腾讯会议残留
        # 画面等）。这类画面既不可能是关键时刻，反复 OCR 会持续占用本地
        # LLM/CPU——正是"回复慢/半天没回复、游戏卡"的元凶。
        # 连续 STATIC_SKIP_FRAMES 帧静止直接 return：不编码 JPEG、不灰度、
        # 不 OCR、不裁决、不更新画面快照（省掉每帧 ~0.3s 的图片编码与
        # 预处理 + 全部 OCR 推理）。
        # phash 比"OCR 文字相等"更稳：OCR 每帧输出会因分割/空格抖动，文字
        # 相等判断会被抖动重置计数，而同一冻结画面的 phash 稳定为 0~1 差异。
        # buf（JPEG）只在发布 moment 时用于"让话术 LLM 看图"，静止帧不发布，
        # 提前 return 不编码 buf 无任何损失。
        if self._frame_source != "video":
            try:
                from . import phash
                ph = phash.phash(img.resize((64, 64)))
                if self._last_phash is not None and abs(ph - self._last_phash) < STATIC_PHASH_DIFF:
                    self._static_frames += 1
                    if self._static_frames >= STATIC_SKIP_FRAMES:
                        logger.debug("画面 phash 静止 %d 帧，跳过（省 OCR/LLM）", self._static_frames)
                        # 陈旧抓屏兜底（2026-08-25 适配逐区域门禁）：连续静止超阈值
                        # （疑似 mss 返回旧冻结帧 / 渲染模式切换后陈旧）→ 重建实例
                        # 恢复实时抓取；同时清空区域 phash 缓存，下一帧重新 OCR。
                        if self._static_frames >= STALE_GRAB_LIMIT:
                            logger.warning(
                                "画面持续静止 %d 帧，疑似抓屏陈旧，重建 mss/OCR 实例",
                                self._static_frames)
                            self._sct = None
                            self._ocr = None
                            self._last_phash = None
                            self._static_frames = 0
                            self._region_last_text.clear()
                        return
                else:
                    self._static_frames = 0
                self._last_phash = ph
            except Exception:  # noqa: BLE001
                pass  # phash 不可用则退回 OCR 文字静止判断
        # 懒编码：整图 JPEG 只在发布时刻时按需编码（见 _frame_jpeg），不再每轮
        # 无谓编码一次（省 ~30~50ms/轮 + 一次 BytesIO 分配）。
        self._last_img = img
        self._frame_jpeg_cache = None

        ocr = self._ensure_ocr()
        # ========== 区域化扫描（2026-08-25 用户方案：状态互斥、按需启用、保持高频）==========
        # CPU 实锤：整图 OCR ≈3s/帧 是单帧最大开销（游戏 3D 画面一直动 → phash
        # 静止检测失效 → 每轮都整图 OCR → 主程序 CPU 271%）。而页面判断只需
        # 左上角（页面名）、模式判断只需右下角（档位/地图名如"红薯窝"）——
        # 不再整图扫，按状态只扫需要的小区域（小图 OCR 快，保持高频，模式切换
        # 秒级识别，消除"8s 识别不到红薯窝、开局话术错"）。
        # 状态判定区（大厅每轮必扫）：hud=左上角（页面名/倒计时）+ page=右下角（模式/地图名）
        # 关键时刻区（对局中互斥启用）：hud=倒计时 + settle=底部（击杀/阵亡播报+结算横幅）
        #   + kill/kill_hint/death（击杀播报/确认提示/阵亡名片）。对局中模式已定，
        #   不再扫 page（右下角），省一区 CPU。
        in_match = self._in_match()
        # ============ 状态互斥 + 关键时刻轮转（2026-08-25 节奏门禁·可靠版）============
        # 扫描策略（2026-08-25 用户要求）：不分轮次，每轮把所有窗口都扫一遍
        # （约每秒一次）。击杀/阵亡/结算/倒地都是短时效强信号，按区轮转会漏掉，
        # 故统一全扫，保证关键时刻不漏触发。downed_banner 已并入 _regions，
        # 全扫时天然每轮必扫，无需单独补扫。用户确认接受此 OCR 开销。
        # 整图一次 OCR（见下方区域过滤循环），不再逐区域独立调用 ocr()
        region_texts: dict[str, str] = dict(self._region_last_text)
        hud_texts: list[str] = []
        settle_texts: list[str] = []
        kill_texts: list[str] = []
        kill_hint_texts: list[str] = []
        page_texts: list[str] = []
        death_texts: list[str] = []
        general_texts: list[str] = []
        # ============ 整图一次 OCR + 按区域矩形过滤（2026-08-25 性能根治）============
        # 旧：每个区域独立 ocr() 一次 → 10+ 次模型调用累积 4~6s/轮，扫描周期远慢
        # 于 1s 间隔，切画面要等 4~6s 才扫到（用户实测"切换图没及时响应"）。
        # 新：整图只跑一次 ocr() 得全屏所有文字框+坐标，按各 region 矩形过滤分回
        # ——大字区域(底部/结算/中央提示/倒地)直接整图覆盖，识别率反而更好(更清晰)。
        # 仅原"需放大的小字区域"(_PURPOSE_OCR_SCALE>1.0：顶部HUD/时间/地图/右上角
        # 击杀播报)保留单独裁剪放大 OCR 覆盖，识别率与改动前完全一致。调用数从
        # 10+ 降到 ~5 次，性能降约一半，且每区域照样覆盖不漏。
        full = await asyncio.to_thread(self._ocr_full_frame, img)
        # 整图 OCR 全屏文字（供关键时刻锚点兜底匹配，不受窄区域矩形限制）
        full_text = " ".join(t for (_, _, t) in full)
        fw, fh = img.size
        # 【2026-09-07 整合】逐游戏专属区域（软件档案 OCR 配置）优先
        _regions = self.effective_regions()
        for name, r in _regions.items():
            rect = r.get("rect")
            if not rect or len(rect) != 4:
                continue
            x, y, w, h = rect
            x0, y0, x1, y1 = x * fw, y * fh, (x + w) * fw, (y + h) * fh
            hit = [t for (cx, cy, t) in full if x0 <= cx <= x1 and y0 <= cy <= y1]
            rtext = " ".join(hit)
            if rtext:
                region_texts[name] = rtext
                self._region_last_text[name] = rtext
                logger.debug("OCR区域[%s]: %s", name, rtext[:50])
        # 放大小字区域单独 OCR 覆盖（保识别率，与改动前一致）：仅 _PURPOSE_OCR_SCALE>1.0
        for name, r in self._regions.items():
            purpose = r.get("purpose", "")
            scale = _PURPOSE_OCR_SCALE.get(purpose, 1.0)
            if scale <= 1.0:
                continue
            try:
                band = self._grab_region(name, img, 360, scale)
                if band is None:
                    continue
                rtext = await asyncio.to_thread(self._ocr_band_to_text, band)
                if rtext:
                    region_texts[name] = rtext
                    self._region_last_text[name] = rtext
                    logger.debug("OCR区域[%s](放大×%s): %s", name, scale, rtext[:50])
            except Exception:  # noqa: BLE001
                logger.debug("OCR 区域 %s 识别失败，忽略", name)
        self._region_texts = region_texts
        # 合并区域文字（原整图 text 的角色：引信匹配/规则过滤/日志/知识库）
        text = " ".join(region_texts.values())
        # 规则说明/加载画面文字（如"32v32载具大战场…"）：计静止帧，但不 return
        # ——页面/模式仍需更新（加载画面识别后进入对局）。
        if _is_rule_text(text):
            logger.debug("OCR 识别到规则说明/加载画面文字: %s", text[:60])
            self._static_frames += 1
        # 【陈旧抓屏检测（2026-08-25 适配逐区域门禁）】不再用"OCR 文字相同"判断
        # 陈旧——逐区域门禁下稳态文字本就相同（计时器跳字被门禁跳过），会误判。
        # 改由上方冻结块按"连续静止超 STALE_GRAB_LIMIT 帧"重建 mss（更可靠）。
        # 这里仅保留上一帧文字缓存供调试/日志。
        self._last_ocr_text = text

        # 左上角区域 OCR（手机共享时）：页面名（仓库/交易行/邮件）与档位
        # （普通/机密/绝密）在屏幕左上角，主抓屏的中央裁剪把它们裁掉了。
        # 单独 OCR 左上角区域，供 screen_page() 识别"在主页/对局中"，校正
        # 视觉模型的脑补（主页看成搜物资 / 对局中看成撤离成功）。中心文字为
        # 空（主页菜单中央无文字）时也要识别左上角，所以放在空文字返回之前。
        corner_text = ""
        if self._is_phone_share():
            try:
                corner_img = await asyncio.to_thread(self._grab_corner)
                if corner_img is not None:
                    cimg = corner_img.convert("L")
                    try:
                        from PIL import ImageOps
                        cimg = ImageOps.autocontrast(cimg, cutoff=2)
                    except Exception:  # noqa: BLE001
                        pass
                    cbuf = io.BytesIO()
                    cimg.save(cbuf, format="PNG")
                    cresult = await asyncio.to_thread(ocr, cbuf.getvalue())
                    corner_text = self._extract_text(cresult)
                    self._last_corner_text = corner_text
                    if corner_text:
                        logger.debug("左上角OCR: %s", corner_text[:60])
            except Exception:  # noqa: BLE001
                logger.debug("左上角区域 OCR 失败，忽略（回退中央文字判定）")

        for name, rtext in region_texts.items():
            purpose = (self._regions.get(name) or {}).get("purpose", "general")
            if rtext:
                if purpose == "hud":
                    hud_texts.append(rtext)
                elif purpose == "settle":
                    settle_texts.append(rtext)
                elif purpose == "kill":
                    kill_texts.append(rtext)
                elif purpose == "kill_hint":
                    kill_hint_texts.append(rtext)
                elif purpose == "page":
                    page_texts.append(rtext)
                elif purpose == "death":
                    death_texts.append(rtext)
                else:
                    general_texts.append(rtext)
        self._region_texts = region_texts
        # 兼容旧逻辑字段：hud→顶部HUD、settle→底部信息区、kill→右上角击杀播报
        # （心跳诊断/击杀播报变化检测/击杀者提取继续用这些字段）
        top_text = " ".join(hud_texts)
        bottom_text = " ".join(settle_texts)
        top_right_text = " ".join(kill_texts)
        kill_hint_text = " ".join(kill_hint_texts)
        if top_text:
            self._last_top_text = top_text
        if bottom_text:
            self._last_bottom_text = bottom_text
        if top_right_text:
            self._last_top_right_text = top_right_text
        # page 用途区域：页面名/档位并入左上角文字（screen_page 用），
        # 中心文字扫不到页面名时也能靠专门区域识别"在主页/对局中"。
        page_txt = " ".join(page_texts)
        if page_txt:
            corner_text = (corner_text + " " + page_txt).strip()
            self._last_corner_text = corner_text
        general_txt = " ".join(general_texts)
        # 阵亡名片区域文字（purpose=death）：玩家死后 1~3s 击杀栏下方出现
        # "敌人名片"（图片+名字，表示被谁淘汰），纯 OCR 即可判定玩家阵亡。
        death_txt = " ".join(death_texts)

        # 记录最近画面文字：让 AI 在对话/主动话术里"看字不猜"，知道当前模式/地图。
        # 空文字帧也更新（清空旧快照）——游戏加载/黑屏/纯 3D 画面期间没有文字，
        # 若不清空，"选模式"等旧画面文字会残留，被 AI 当成当前状态（牛头不对马嘴）
        if self._game_knowledge is not None:
            self._game_knowledge.update_screen_text(
                text, corner_text, top_text, bottom_text)

        # 文字锚点匹配（区域文字锚点训练器）：把各区域 OCR 文字跑一遍用户训练
        # 的锚点规则，命中 → 产出确定性页面状态写入知识库（AI"看字不猜"）。
        # 命中记录也在 text_only 模式下生效——锚点只是"读真文字判页面"，
        # 不发布任何击杀/胜负时刻判定（那些仍在下面引信层，text_only 时跳过）。
        anchor_hits = self.match_anchors(region_texts, full_text)
        self._apply_anchors(anchor_hits)
        # 锚点强绑定：识别到的关键时刻（观战/倒地/撤离成败）发布为 moment 事件，
        # 让 AI 立刻互动（不再只写上下文背景）。上升沿去重在 _apply_anchors 内完成。
        for _pm in self._pending_moments:
            await self._emit_anchor_moment(
                _pm["moment"], _pm["label"], _pm.get("ocr_text", ""))
        self._pending_moments.clear()

        if not text and not corner_text and not top_text and not bottom_text \
                and not top_right_text and not kill_hint_text \
                and not general_txt and not death_txt:
            return

        # 游戏识别（喂给 AI 游戏名+攻略+数据）：优先窗口标题，其次 OCR 屏幕文字
        if self._game_knowledge is not None:
            title = self._monitor.last_title if hasattr(self._monitor, "last_title") else ""
            if not self._game_knowledge.detect(title or ""):
                self._game_knowledge.detect(text)

        # 记录最近扫描文本（供调试台诊断为什么没命中）
        self._recent_scans.append({"ts": time.time(), "text": text[:150]})
        self._recent_scans = self._recent_scans[-10:]

        # 仅文字模式：OCR 文字已写入知识库（上面 update_screen_text），供视觉
        # 模型"看图+看字"结合判断。这里直接返回，不再进入观战/击杀/胜负引信
        # （OCR 时刻判定误报频发已下线，text_only 只借"读屏幕真文字"能力）。
        if self._text_only:
            return

        # 合并多区域文字用于关键时刻匹配：击杀播报/结算横幅常渲染在底部信息区、
        # 顶部 HUD（回合 X/7 含"胜利/失败"字样）和右上角（PC 版击杀播报），
        # 中央文字扫不到不代表没有时刻。引信匹配用全区域文字，最终裁决仍交给
        # 多模态看整图定夺（OCR 只扩大"疑似"范围，不直接下结论）。
        ocr_all = " ".join(x for x in (
            text, top_text, bottom_text, top_right_text, kill_hint_text,
            general_txt) if x)

        # 倒地识别已并入锚点强绑定（config region: 用户倒地 → moment=downed，
        # 上升沿去重在 _apply_anchors，复用 _moment_last 冷却，不再单独在这检测）。

        # 观战画面状态机：玩家阵亡后才进入观战（HUD 带"观战"标记），
        # 观战里看到的击杀/高光/胜利/阵亡都是队友或敌人的操作，绝不是用户的。
        # 刚进入观战（从正常游戏画面切到观战）= 玩家刚阵亡 → 报一次 died；
        # 持续处于观战画面 → 一律静默（治"观战敌人操作却说太帅了/别难过"）。
        # 观战命中即进入『等待对局结束』状态（玩家已阵亡、等待本局结束）——持续
        # 告诉 AI，避免它聊"刚才那波/当前战斗"；对局真正结束（退出观战/回大厅）
        # 时自动解除，胜负/结算判定恢复正常。
        spec_state = self._spectator_state(ocr_all)
        if spec_state == "stay":
            # 玩家已阵亡，正处于"等待对局结束"状态：画面里的一切击杀/胜负都是
            # 队友/敌人的操作，一律静默（治"观战敌人操作却说太帅了/别难过"）。
            self._set_waiting_match_end(True)
            logger.debug("观战画面持续(等待对局结束)，静默: %s", text[:60])
            return
        if spec_state == "enter":
            self._set_waiting_match_end(True)
            now = time.time()
            # 冷却内已报过阵亡则不重复（如死亡结算文字刚触发过 died）
            if now - self._moment_last.get("died", 0.0) < self._cooldown_s:
                logger.debug("阵亡刚报过，跳过观战转入的重复 died")
                return
            # v2：不再调多模态 AI 复查（视觉只描述画面，不参与时刻裁决）。
            # 观战转入 = 玩家阵亡后才进入观战视角，是确定性信号；红鼠窝回合
            # 等待画面被误扫成"观战"的残余误报由 _spectator_state 的多条件
            # 判定（见该方法注释）拦截，不靠视觉复查。
            self._moment_last["died"] = now
            self._hit_count += 1
            label = MOMENT_LABEL["died"]
            summary = f"{label}（观战转入）"
            activity = {
                "summary": summary,
                "app_hint": "game",
                "confidence": 0.9,
                "moment": "died",
                "source": "ocr",
                "ocr_text": text[:120],
                # 当前截图字节：让话术 LLM 亲眼看到画面再开口（多模态话术）
                "image": self._frame_jpeg(),
            }
            self._recent.append({
                "ts": now,
                "moment": "died",
                "keyword": "观战",
                "summary": summary,
                "source": "ocr",
                "ocr_text": text[:80],
            })
            self._recent = self._recent[-10:]
            logger.info("游戏OCR命中[观战转入=阵亡]: %s (屏幕文字=%s)", label, text[:60])
            await self._bus.publish(self._screen_event(activity))
            return

        # spec_state == "none"：非观战画面。若刚才还在『等待对局结束』→
        # 说明对局已结束（退出观战/回到大厅），解除该状态；后续胜利/失败/
        # 结算判定即可正常触发（不再被"等待对局结束"锚点干扰）。
        if self._waiting_match_end:
            self._set_waiting_match_end(False)

        # ============ v2 击杀/阵亡判定（证据门 + 精确匹配，不靠多模态/不靠位置猜归属） ============
        # 击杀播报「击杀者A 枪械图标 被杀者B」：
        #   - "玩家名 淘汰了 XXX" → 玩家是击杀者 → kill（【必须】证据门通过=开了枪）
        #   - "XXX 淘汰了 玩家名" → 玩家是被杀者 → died（被打死不需要先开枪，不查证据门）
        #   - "你击杀了 / 双杀 / 爆头" → 本人击杀提示 → kill（同样要证据门）
        #   - "你被击杀 / 你阵亡" → died
        # 阵亡名片（purpose=death 区域）：区域文字出现/变化即 died（不依赖玩家名）。
        # 不再做"左右 token 位置猜归属"——多条播报叠加时位置不可靠，是"没打人却
        # 说打死人"的误报根源。无法精确确认 → 静默（宁可少互动）。
        if not _ui_excluded(ocr_all):
            now = time.time()
            # 1) 阵亡名片：玩家死后 1~3s 击杀栏下方出现"敌人名片"，区域文字出现/变化即 died
            if death_txt and death_txt != self._last_death_text:
                self._last_death_text = death_txt
                if now - self._moment_last.get("died", 0.0) >= self._cooldown_s:
                    self._moment_last["died"] = now
                    self._hit_count += 1
                    summary = "你在游戏里阵亡了（阵亡名片）"
                    activity = {
                        "summary": summary, "app_hint": "game", "confidence": 0.95,
                        "moment": "died", "source": "ocr",
                        "ocr_text": death_txt[:120],
                        "image": self._frame_jpeg(),
                    }
                    self._recent.append({
                        "ts": now, "moment": "died", "keyword": "阵亡名片",
                        "summary": summary, "source": "ocr",
                        "ocr_text": death_txt[:80],
                    })
                    self._recent = self._recent[-10:]
                    logger.info("游戏OCR命中[阵亡名片]: %s", death_txt[:60])
                    await self._bus.publish(
                        self._screen_event(activity))
                    return

            # 2) 击杀播报精确归属：只看底部信息区（bottom_hud/settle 区域，
            #    "2 打我变成杂鱼"这类连杀提示/击杀播报）。结算画面（撤离成功/
            #    已阵亡等横幅）里的玩家名是战绩统计，不参与归属判断（防"结算页
            #    玩家名被误判阵亡"）。
            #    【2026-08-12 用户要求】不再用右上角 kill 区域（top_right_hud）
            #    做击杀判定——右上角小地图/性能面板/击杀列表常被 OCR 误扫进
            #    feed，导致判定异常（实测"我死了却说队友掉了/打死人不夸奖"）。
            #    早期方案只 OCR 底部就能读到击杀播报，恢复只认底部。
            feed_text = bottom_text
            feed_moment = ""
            if bottom_text and not _SETTLE_RESULT_RE.search(bottom_text):
                if self._match_self_kill_feed(bottom_text):
                    feed_moment, feed_text = "kill", bottom_text
                elif self._match_self_died_feed(bottom_text):
                    feed_moment, feed_text = "died", bottom_text
            if not feed_moment and self._feed_mentions_player(feed_text):
                # v2.1 无动词方位归属：三角洲播报格式固定为「击杀者 [枪械图标]
                # 被击杀者」（救援为「发起者 [救援图标] 被救者」），OCR 扫不出
                # 中间图标，文字只剩两个名字。玩家名出现在【左半】= 击杀者/救援
                # 发起者（如"打我变成杂鱼 76juesha"）；出现在【右半】= 被击杀者
                # /被救援者（如"76juesha 打我变成杂鱼"）。
                #   - 左半 + 证据门（真开枪）→ kill（救援发起时玩家没开枪，证据
                #     门自动拦截，不会把"救人"误判成"杀人"）
                #   - 右半 → died（被打死不需要先开枪；被救援情形由倒地/阵亡
                #     确定性链路先行覆盖，走到这里时玩家多半已阵亡）
                if self._player_feed_side(feed_text) == "left":
                    if self._sensor.firing_recently():
                        feed_moment = "kill"
                    else:
                        self._evidence_blocked += 1
                        st = self._sensor.stats()
                        logger.info(
                            "击杀播报玩家名在左[%s]，但输入证据门未通过"
                            "（最近窗口无开枪输入，可能只是救援/被提及），静默: %s"
                            " 证据门=%s",
                            self._player_name, feed_text[:50], st)
                        return
                elif self._player_feed_side(feed_text) == "right":
                    feed_moment = "died"
            if feed_moment:
                # 击杀对象带人机命名特征（哈夫克/阿萨拉/兵）→ 降级 kill_bot
                if feed_moment == "kill" and _is_bot_kill_text(bottom_text):
                    feed_moment = "kill_bot"
                # 【对局中门禁】击杀时刻只在 OCR 确认对局中才发布（治性能面板/
                # 靶场统计"爆头"误报击杀，01:34:11 实锤）。died 不受限。
                if feed_moment in ("kill", "kill_bot") and not self._in_match():
                    logger.info(
                        "击杀播报精确匹配玩家名但非对局中(screen_page非match)，"
                        "静默: %s", feed_text[:50])
                    return
                # kill 必须过证据门：没开枪 → 击杀播报出现玩家名也静默
                # （根治"没打人却说打死人了"：队友/敌人互杀播报、玩家名被
                # 提及但玩家本人没开枪，都过不了这一关）。
                # 注意：这里是"OCR 精确匹配到玩家名+动词"的高置信命中（0.95），
                # 证据门只防"完全没开枪"。单发武器（狙击/飞刀/投掷物）一枪击杀
                # 只有 1 次点击，用 ≥2 会系统性漏判——故放宽为 ≥1（审查实锤）。
                if feed_moment in ("kill", "kill_bot") \
                        and not self._sensor.firing_recently(min_clicks=1):
                    self._evidence_blocked += 1
                    st = self._sensor.stats()
                    logger.info(
                        "击杀播报精确匹配玩家名[%s]，但输入证据门未通过"
                        "（最近窗口无开枪输入），静默: %s 证据门=%s",
                        self._player_name, feed_text[:50], st)
                    return
                if now - self._moment_last.get(feed_moment, 0.0) >= self._cooldown_s:
                    self._moment_last[feed_moment] = now
                    self._hit_count += 1
                    label = MOMENT_LABEL[feed_moment]
                    summary = f"{label}（击杀播报精确匹配）"
                    activity = {
                        "summary": summary, "app_hint": "game", "confidence": 0.95,
                        "moment": feed_moment,
                        "source": "ocr+input" if feed_moment == "kill" else "ocr",
                        "ocr_text": feed_text[:120],
                        "image": self._frame_jpeg(),
                    }
                    if feed_moment == "kill":
                        activity["kill_by"] = (_extract_killer(feed_text)
                                                or self._player_name)
                    self._recent.append({
                        "ts": now, "moment": feed_moment, "keyword": "击杀播报精确匹配",
                        "summary": summary,
                        "source": "ocr+input" if feed_moment == "kill" else "ocr",
                        "ocr_text": feed_text[:80],
                    })
                    self._recent = self._recent[-10:]
                    logger.info("游戏OCR命中[击杀播报精确匹配]: %s 播报=%s 玩家名=%s",
                                label, feed_text[:60], self._player_name)
                    await self._bus.publish(
                        self._screen_event(activity))
                return

            # 2.5) 击杀确认提示（kill_hint 区域）：屏幕中间下方"击败 某某某兵
            #      阿萨拉/哈夫克"。三角洲机制：只有本人击杀才弹该提示（右上角
            #      播报队友/敌人击杀也滚动，不可靠）→ 出现即确认击杀，不依赖
            #      玩家名/证据门。文字含人机命名特征（哈夫克/阿萨拉/兵）→
            #      kill_bot，不含 → kill（真人）。用户明确：底下没写"兵"=击杀
            #      真实玩家，无需看右上角播报；之后接入击杀音效参考音做复核。
            if kill_hint_text and not _ui_excluded(kill_hint_text):
                hint_moment = self._match_kill_hint(kill_hint_text)
                if hint_moment:
                    now = time.time()
                    if now - self._moment_last.get(
                            hint_moment, 0.0) >= self._cooldown_s:
                        self._moment_last[hint_moment] = now
                        self._hit_count += 1
                        label = MOMENT_LABEL[hint_moment]
                        summary = f"{label}（击杀提示确认）"
                        activity = {
                            "summary": summary, "app_hint": "game", "confidence": 0.95,
                            "moment": hint_moment, "source": "ocr",
                            "ocr_text": kill_hint_text[:120],
                            "image": self._frame_jpeg(),
                        }
                        if hint_moment == "kill":
                            activity["kill_by"] = self._player_name
                        self._recent.append({
                            "ts": now, "moment": hint_moment, "keyword": "击杀提示",
                            "summary": summary, "source": "ocr",
                            "ocr_text": kill_hint_text[:80],
                        })
                        self._recent = self._recent[-10:]
                        logger.info("游戏OCR命中[击杀提示确认]: %s 提示=%s",
                                    label, kill_hint_text[:60])
                        await self._bus.publish(
                            self._screen_event(activity))
                    return

        # 引信：关键词快速匹配（胜负/阵亡/本人击杀提示）。**未命中直接跳过，
        # 不再调多模态**——OCR 文字没扫到疑似就说明当前没有关键时刻。
        moment, kw = self._match_moment(ocr_all)
        if not moment:
            # 击杀播报区文字变化但无关键词命中：多半是 OCR 扫不全游戏小字
            # （只扫出玩家昵称碎片、识别不出动词）。玩家名精确归属已在上方处理，
            # 走到这里说明没精确命中玩家名+动词 → 静默（宁可漏，不误报）。
            # INFO 输出实际播报文字，方便诊断"玩家打了人但没归属到"（多半是
            # OCR 把玩家名识别错字，看这里实际扫到什么就能定位）。
            kill_feed_now = top_right_text
            if (kill_feed_now and kill_feed_now != self._last_kill_feed
                    and not self._menu_page_active):
                self._last_kill_feed = kill_feed_now
                logger.info("击杀播报变化但未精确匹配玩家名(%s)，静默: %s",
                            self._player_name, kill_feed_now[:50])
            logger.debug("OCR 无关键词命中，跳过（画面文字=%s）", text[:60])
            return

        # 诊断：OCR 疑似关键时刻时保存当前抓屏图，排查"画面抓错窗口/击杀播报
        # 位置/小字识别"问题（如 PC 版游戏 HUD 击杀播报 OCR 扫不到）。正常命中
        # 很少，存图成本可忽略；不需要时删除 logs/ocr_debug 目录即可。
        try:
            import os
            _dbg_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "logs", "ocr_debug")
            os.makedirs(_dbg_dir, exist_ok=True)
            _ts = time.strftime("%m%d_%H%M%S_%f")[:-3]
            img.save(os.path.join(_dbg_dir, f"{moment}_{_ts}.png"))
        except Exception:  # noqa: BLE001
            pass

        # 非实时对局画面（战绩面板/回放录像/录屏水印）的时刻排除：画面里的
        # "胜利/获胜/击杀/等待救援"是统计文字/回放标题/录屏 OSD，不是关键时刻
        # （16:41:21 准备页"战绩详情 赛绩 胜利"被判 victory 实锤）。直接跳过不裁决。
        if _ui_excluded(ocr_all):
            logger.debug("OCR 画面为战绩面板/回放/录屏界面，跳过时刻引信: %s",
                         text[:50])
            return

        # 胜负时刻的"未完成/进行中"排除：OCR 原文（像素真文字，最可靠）里出现
        # "尚未撤离成功""还有X秒撤离成功""倒计时…超时将撤离失败""等待01:06后
        # 撤离成功"等词 → 胜负还没真正发生，直接跳过不调多模态裁决
        # （防"对局中还剩5分钟就报赢""刚进图倒计时没走完就报输"误报）。
        if moment in ("victory", "defeat") and _result_not_reached(ocr_all):
            logger.info("游戏OCR胜负排除(未完成/进行中): %s 画面文字=%s",
                        moment, text[:60])
            return

        # 胜负页面门禁（2026-08-12 实锤误报）：OCR 左上角/顶部读到页面名
        # （仓库/交易行/主页…）或档位/回合（对局中）时，"胜利/失败/获胜"是
        # 历史战绩/统计文字/残留横幅，不是当前这局的结果——主页画面扫到"胜利"
        # 误报 victory（22:16:05 主页弹"漂亮，我就说你行"）、对局中报胜负同样
        # 是误报（对局还没打完）。真结算横幅出现时 screen_page 是
        # confirm_extract/confirm_defeat（撤离成功/失败）或 ""（无页面名，
        # 如全面战场"胜利"结算），不会被误拦。
        gk = self._game_knowledge
        if moment in ("victory", "defeat") and gk is not None:
            try:
                # 强结算横幅：出现即代表本局已分胜负，必须放行，否则用户打完这局
                # AI 一句话都不说。红薯窝回合制结算页仍带"回合X/7/对局中"文字，会
                # 让 screen_page=match 误拦（2026-08-26 实锤：胜利/失败被静默），
                # 故强结算词命中时无论 match 与否都放行。以下词均为确定性结算词，
                # 不会出现在正常对局里。
                _STRONG_SETTLE = ("回合胜利", "回合战败", "本局胜利", "本局失败",
                                  "战斗胜利", "战斗失败", "撤离成功", "撤离失败",
                                  "大吉大利", "战败")
                _strong = any(k in ocr_all for k in _STRONG_SETTLE)
                sp = gk.screen_page()
                # 主页/菜单守卫（2026-08-26 修复：主页误报胜负）：
                # screen_page() 在主页但 OCR 没扫到主页词时会返回 ""（空）而非
                # "home"，旧逻辑只拦 == "home" 漏掉这种情况；且主页战绩卡片/
                # 上局回顾里常含"撤离失败/失败"等强词，会被误当本局结算。
                # 故：画面是 home，或 OCR 整图含任一主页词（主页/仓库/交易行/
                # 邮件…）→ 一律静默，不判本局胜负。
                if sp == "home" or any(w in ocr_all for w in HOME_PAGE_WORDS):
                    logger.info(
                        "关键词 %s(%s) 但画面在主页/菜单（含主页词或 screen_page="
                        "home），非本局结算，静默: %s",
                        moment, kw, text[:50])
                    return
                # 未知页面("")：仅当 OCR 命中强结算横幅才放行（真结算，如全面
                # 战场"胜利"屏无页面名）；否则非结算场景（主页/大厅界面扫到
                # "失败/游戏结束"等泛词）静默，根治"看主页却报胜负"。
                if sp == "" and not _strong:
                    logger.info(
                        "关键词 %s(%s) 但页面未知且无强结算横幅（非结算场景），"
                        "静默: %s",
                        moment, kw, text[:50])
                    return
                if sp == "match" and not _strong:
                    logger.info(
                        "关键词 %s(%s) 但 OCR 确认在对局中且无强结算横幅"
                        "（screen_page=match，对局中无胜负结算），静默: %s",
                        moment, kw, text[:50])
                    return
            except Exception:  # noqa: BLE001
                pass

        now = time.time()
        # 引信冷却：同一引信类型短期内不重复处理
        if now - self._moment_last.get(moment, 0.0) < self._cooldown_s:
            logger.debug("游戏时刻 %s 冷却中，跳过", moment)
            return

        # v2 裁决（不再调多模态看截图）：
        # - kill：只认"明确本人击杀提示"（你击杀了/双杀/爆头），且要过证据门。
        #   泛"XXX 淘汰了 YYY"播报（可能是别人互杀）在上面 feed 精确匹配已处理——
        #   走到这里说明玩家名+动词没精确命中 → 静默，不猜。
        # - died：本人阵亡强词（你被击杀/你阵亡等）→ 确定性，直接报。
        # - victory/defeat：结算横幅文字 → 确定性，直接报（"未达成"已排除）。
        if moment == "kill":
            # 【对局中门禁】击杀引信只在 OCR 确认对局中才发布。性能面板/靶场
            # 统计的"爆头"（用户 01:34:11 实锤被当击杀播报）走的就是这条关键词
            # 路径——面板里"爆头 45 次"命中 KEYWORD_RULES 的"爆头"，但此时
            # screen_page() 是 home 不是 match。不进对局（左上角档位/撤离倒计时）
            # 就不可能有真击杀。
            if not self._in_match():
                logger.info(
                    "关键词 kill(%s) 但非对局中(screen_page非match)，静默: %s",
                    kw, text[:50])
                return
            if not _SELF_KILL_RE.search(ocr_all):
                logger.info("关键词 kill(%s) 但非明确本人击杀提示，静默: %s", kw, text[:50])
                return
            if not self._sensor.firing_recently():
                self._evidence_blocked += 1
                logger.info("本人击杀提示(%s) 但输入证据门未通过，静默: %s", kw, text[:50])
                return
        elif moment == "died":
            # 阵亡弱词（"倒下/被击倒/观战"等易被规则说明/回合等待画面误扫）在 v2
            # 无视觉复查下不放行。其余 DIED_PATTERNS 命中的"X被淘汰"可能是队友/
            # 敌人被淘汰（如"疾风被淘汰"），必须玩家名/你是被杀者精确命中才报
            # （宁缺勿滥，不把别人阵亡当用户阵亡去安慰）。
            # 【2026-08-12 用户实锤】"等待救援/救援中/等待救助"例外：用户死后
            # 底部信息区就会出"等待救援"（自己倒地待救），这是本人阵亡的确定性
            # 信号——底部区域出现才放行 died（队友倒地提示在画面其他位置），
            # 否则直接静默（治"我死了却说队友掉了"：died 没发布 → 上层的
            # teammate_down 压制窗口不生效，头像变灰被误报成队友阵亡）。
            if kw in ("倒下", "被击倒", "观战"):
                logger.info("阵亡弱词(%s)不放行（无视觉复查），静默: %s", kw, text[:50])
                return
            if kw in ("等待救援", "救援中", "等待救助"):
                # 注：「等待救援」等词已移到 downed 时刻（见 DOWNED_KEYWORDS），
                # died 规则里不再包含；此处仅作兜底——万一仍命中，按原逻辑要求
                # 底部信息区也出现救援词才算本人倒地，否则静默。
                if not any(w in bottom_text for w in ("等待救援", "救援中", "等待救助")):
                    logger.info("阵亡词(%s)但底部信息区无'等待救援'（非本人倒地），"
                                "静默: %s", kw, text[:50])
                    return
            elif not self._match_self_died_feed(ocr_all):
                logger.info("阵亡词(%s)未精确匹配玩家本人（可能是队友/敌人被淘汰），"
                            "静默: %s", kw, text[:50])
                return

        # 击杀对象带"人机"标记 → 保守降级为 kill_bot（不鼓励，宁可少互动）
        if moment == "kill" and _is_bot_kill_text(text):
            moment = "kill_bot"
            kw = "人机"

        self._moment_last[moment] = now
        self._hit_count += 1

        label = MOMENT_LABEL.get(moment, "游戏关键时刻")
        summary = f"{label}（{kw}）"
        activity = {
            "summary": summary,
            "app_hint": "game",
            "confidence": 0.9,
            "moment": moment,
            "source": "ocr+input" if moment == "kill" else "ocr",
            "ocr_text": text[:120],
            # 当前截图字节：让话术 LLM 亲眼看到画面再开口（多模态话术）
            "image": self._frame_jpeg(),
        }
        # 击杀时尽量把"击杀者名字"（玩家昵称）带给主动策略——记住用户是谁，
        # 后续击杀播报匹配到同名 → 更确定是用户本人击杀，话术更精准。
        # 击杀播报在右上角/底部区域，从这些区域文字提取击杀者。
        if moment == "kill":
            activity["kill_by"] = _extract_killer(top_right_text or bottom_text or text)
        self._recent.append({
            "ts": now,
            "moment": moment,
            "keyword": kw,
            "summary": summary,
            "source": "ocr+input" if moment == "kill" else "ocr",
            "ocr_text": text[:80],
        })
        self._recent = self._recent[-10:]
        logger.info("游戏OCR命中[%s]: %s (关键词=%s, 屏幕文字=%s)",
                    "ocr+input" if moment in ("kill", "kill_bot") else "ocr",
                    label, kw, text[:60])
        await self._bus.publish(Event(EventType.SCREEN_ACTIVITY, activity,
                                      "ocr+input" if moment in ("kill", "kill_bot") else "ocr"))

    async def _publish_downed(self, detail: str) -> None:
        """发布"倒地(等待救援)"时刻：玩家倒地、还能被救起，给一句稳住/鼓励的话。

        与 died（已阵亡）区分——倒地横幅期间玩家只是趴下等救援，话术不能说
        "你死了/没了"。本方法由 _scan_once 的倒地上升沿检测调用（每局倒地只发一次）。
        """
        now = time.time()
        label = MOMENT_LABEL.get("downed", "你在游戏里倒地了（等待救援）")
        summary = f"{label}（{detail[:40]}）" if detail else label
        activity = {
            "summary": summary,
            "app_hint": "game",
            "confidence": 0.9,
            "moment": "downed",
            "source": "ocr",
            "ocr_text": detail[:120],
        }
        self._recent.append({
            "ts": now, "moment": "downed", "keyword": detail[:40],
            "summary": summary, "source": "ocr", "ocr_text": detail[:80],
        })
        self._recent = self._recent[-10:]
        logger.info("游戏OCR命中[downed]: %s (屏幕文字=%s)", label, detail[:60])
        await self._bus.publish(self._screen_event(activity))

    def _log_gpu_stats(self, scan_cost_ms: Optional[float]) -> None:
        """实时记录 GPU 占用率/显存（nvidia-smi 采样），附本轮 OCR 耗时。

        每 gpu_log_interval_s 触发一次（时间驱动，不受扫描间隔影响），写入 INFO
        日志，方便用户结合游戏体验对比「CPU 方案」与「GPU(DirectML) 方案」：
        - GPU 模式：GPU util 略升、显存 +OCR 模型(~数百 MB)、OCR 耗时低、CPU 平稳
        - 切回 CPU 模式（config device=cpu）：GPU util 几乎不动、OCR 耗时高、CPU 占用升
        nvidia-smi 不可用（核显/AMD/驱动缺失）时降级：只记 OCR 耗时与设备。
        """
        now = time.time()
        if now - self._gpu_log_t < self._gpu_log_interval_s:
            return
        self._gpu_log_t = now
        dev = "GPU(DirectML)" if self._ocr_use_gpu else "CPU"
        cost = scan_cost_ms if scan_cost_ms is not None else -1.0
        gl = gpu_load()
        if gl is not None:
            # 本软件自身 OCR GPU 显存：DML 走 D3D，nvidia-smi 按进程看不到，
            # 用「DML 会话加载时的整卡显存增量」估算（见 _ensure_ocr）。CPU 模式为 0。
            if self._ocr_use_gpu and self._ocr_dml_mem_mb is not None:
                app_str = f"本软件OCR显存≈{self._ocr_dml_mem_mb:.0f}MB(DML增量)"
            elif self._ocr_use_gpu:
                app_str = "本软件OCR显存=加载中"
            else:
                app_str = "本软件OCR显存=0(CPU模式)"
            logger.info(
                "[OCR设备=%s] 整卡GPU util=%.0f%%(含游戏4K) 整卡显存=%.0f/%.0fMB(%.1f%%) | "
                "%s | 本轮OCR耗时=%.1fms 扫描间隔=%.1fs",
                dev, gl["util_pct"], gl["used_mb"], gl["total_mb"], gl["mem_pct"],
                app_str, cost, self._interval)
        else:
            logger.info(
                "[OCR设备=%s] GPU采样不可用(nvidia-smi缺失) | 本轮OCR耗时=%.1fms 扫描间隔=%.1fs",
                dev, cost, self._interval)

    async def run(self) -> None:
        """扫描循环：仅在前台是游戏时抓屏 OCR。"""
        if not self._enabled:
            logger.info("游戏 OCR 识别未启用")
            return
        # 注意：OCR 不在启动时预载，而是首次真正扫描（前台是游戏）时才懒加载。
        # 原因：桌面前台本地双工(minicpm-o4.5)常驻时显存已接近占满，若启动即建
        # DML 会话易 OOM 把 OCR 降级成 CPU、且整会话都非 GPU；而游戏时双工已
        # 卸载、显存充裕，懒加载才能让 GPU OCR 在对局中稳定生效。
        # 启动输入证据门钩子（pynput 全局监听，幂等；独立实例在此启动）
        self._sensor.start()
        logger.info("游戏 OCR 识别启动: 设备=%s 间隔=%.1fs 冷却=%ss 玩家名=%r",
                    "GPU(DirectML)" if self._ocr_use_gpu else "CPU",
                    self._interval, self._cooldown_s, self._player_name)
        logger.info(
            "游戏OCR就绪: 前台监听=%s 输入证据门=%s（%s）%s",
            self._monitor is not None,
            self._sensor.ok, self._sensor.err or "正常",
            " GPU模式=DirectML推理(省CPU)" if self._ocr_use_gpu
            else " CPU模式=整图一次OCR+放大小字区",
        )
        while self._running:
            try:
                if privacy_mode():
                    # 隐私模式：不抓屏不 OCR，击杀/阵亡引信静默，模型拿不到画面文字
                    if self._game_knowledge is not None:
                        self._game_knowledge.reset()
                    await asyncio.sleep(self._next_interval())
                    continue
                # 实时校验前台是游戏才扫描（避免轮询缓存滞后，切换瞬间误扫非游戏界面）
                is_game = (True if self._frame_source == "video"
                           else self._monitor.is_game_foreground() if self._monitor else False)
                # 手机屏幕共享：腾讯会议进程在运行（用户可能在手机上打游戏，
                # 电脑前台常切到聊天/IDE）→ 即使前台不是游戏也持续扫描。
                # 抓屏已按手机共享裁剪腾讯会议窗口中央区域，非游戏界面文字
                # 会被 NON_GAME_UI_PATTERNS 过滤、视觉裁决也会判非关键时刻，不会误报。
                if not is_game and self._monitor is not None:
                    check = getattr(self._monitor, "is_meeting_running", None)
                    if callable(check) and check():
                        # 会议在跑但窗口可能被完全遮挡（前台切到 IDE/资源管理器把
                        # 会议窗口盖住）：继续扫只会抓到遮挡它的窗口内容，OCR 扫到
                        # IDE 代码/文件路径文字误触发击杀/胜负引信。仅当会议窗口
                        # 当前可见（meeting_rect 非 None）才按手机共享持续扫描。
                        mrect = getattr(self._monitor, "meeting_rect", None)
                        is_game = callable(mrect) and bool(mrect())
                    # 后台识别：前台非游戏但 PC 版游戏窗口还在（被遮挡/切走后），
                    # _grab_image 用 PrintWindow 直接抓游戏窗口交换链内容，不经过
                    # 屏幕合成，被遮挡也能识别——对标"切换屏幕后仍识别游戏"。
                    if not is_game:
                        gw = getattr(self._monitor, "pc_game_window", None)
                        is_game = callable(gw) and gw() is not None
                scan_cost = None
                if is_game:
                    # 【游戏前台/锁快照 让位（2026-08-25 修复抽帧）】throttle=True
                    # （前台在玩/锁快照）→ OCR 走 stagger 单区轮询 + 拉大间隔，
                    # 消除周期 CPU 尖峰防抽帧。后台游戏窗口 throttle=False 照常。
                    throttle = (False if self._frame_source == "video"
                                else bool(self._throttle_check()) if callable(self._throttle_check) else False)
                    # 周期性重建 mss 抓屏实例（防长时间运行 + 显示模式切换后 mss
                    # 悄悄陈旧返回旧帧，周期性重建是无副作用保险；真正陈旧由上面
                    # _scan_once 里的 STALE_GRAB_LIMIT 检测即时触发）
                    if self._scan_count - self._last_sct_refresh >= SCT_REFRESH_SCANS:
                        self._last_sct_refresh = self._scan_count
                        if self._sct is not None:
                            logger.debug("周期性重建 mss 抓屏实例")
                            try:
                                self._sct.close()
                            except Exception:  # noqa: BLE001
                                pass
                            self._sct = None
                    _scan_t0 = time.time()
                    # OCR 懒加载：首次扫描才建会话（见 run() 顶部说明）。建会话失败
                    # （多为 GPU 显存不足）→ 记警告、跳本帧、60s 后重试，不禁用 OCR。
                    if self._ocr is None:
                        try:
                            self._ensure_ocr()
                        except Exception as e:  # noqa: BLE001
                            now = time.time()
                            if now - self._last_ocr_err > 60:
                                logger.warning(
                                    "OCR 初始化失败（可能 GPU 显存不足），60s 后重试: %s",
                                    e)
                                self._last_ocr_err = now
                            await asyncio.sleep(self._next_interval())
                            continue
                    _scan_t0 = time.time()
                    await self._scan_once(throttle=throttle)
                    if self._frame_source == "video" and self._video_eof:
                        logger.info("视频测试源播放完毕，OCR 扫描循环退出")
                        break
                    scan_cost = (time.time() - _scan_t0) * 1000.0
                    scan_cost = (time.time() - _scan_t0) * 1000.0
                else:
                    # 离开游戏：清除游戏上下文，AI 不再按该游戏说话
                    if self._game_knowledge is not None:
                        self._game_knowledge.reset()
                    if self._monitor is not None and time.time() - self._last_warn > 60:
                        # 前台不是游戏：每 60s 提示一次当前窗口，便于判断"游戏没被识别"还是"没进游戏"
                        self._last_warn = time.time()
                        logger.info(
                            "游戏OCR待命: 前台未识别为游戏（标题=%r, 类型=%s）",
                            self._monitor.last_title[:60] if hasattr(self._monitor, "last_title") else "",
                            self._monitor.current_hint,
                        )
            except Exception:  # noqa: BLE001
                logger.debug("游戏 OCR 扫描异常", exc_info=True)
            # GPU 实时占用记录（对比 CPU vs GPU 方案）：每 gpu_log_interval_s 记
            # 一次 util%/显存，附本轮 OCR 耗时，写入日志供用户判断哪种更优。
            self._log_gpu_stats(scan_cost)
            delay = self._next_interval()
            # 【扫描循环保持高频（2026-08-25 逐区域 phash 门禁）】不再做页面/游戏时
            # 拉大间隔的降频——那会拖慢"切红薯窝/摸金"等模式切换判定（用户实测
            # 8s 识别不到）。CPU 由逐区域 phash 门禁压住：稳态下区域像素不变 → OCR
            # 几乎 0 次 → CPU 平稳 ~3% 无尖峰；只有模式切换/击杀播报等大幅变化才
            # OCR。冻结画面仍由 _scan_once 内 phash 静止 early-return 跳过 OCR
            # （加载/残留画面安全，不污染多模态裁决）。
            await asyncio.sleep(delay)
            # 心跳诊断日志：每 30 个循环（约 24~60s）打一次，确认 OCR 在扫/扫到什么。
            # 之前"关键时刻零检测"时 OCR 模块几乎零 INFO 输出，无法判断是没扫、
            # 还是扫到了但文字不含关键词（手机共享小字 OCR 常识别不了）——心跳能定位。
            self._loop_count += 1
            if self._loop_count % 30 == 0:
                corner_show = self._last_corner_text or ""
                win_ov = ""
                if self._monitor is not None:
                    try:
                        win_ov = self._monitor.window_overview()
                    except Exception:  # noqa: BLE001
                        pass
                logger.info(
                    "OCR心跳: 扫描=%d 命中=%d 证据门拦截=%d 静止帧=%d "
                    "(中央=%s 左上角=%s 顶部=%s 右上角=%s 底部=%s %s)",
                    self._scan_count, self._hit_count, self._evidence_blocked,
                    self._static_frames,
                    (self._last_ocr_text or "")[:40],
                    corner_show[:30],
                    (self._last_top_text or "")[:30],
                    (self._last_top_right_text or "")[:40],
                    (self._last_bottom_text or "")[:30],
                    win_ov,
                )
