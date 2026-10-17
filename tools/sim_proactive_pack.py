# -*- coding: utf-8 -*-
"""主动互动包 · 离线频度/人性化仿真器（不打真机、不调真 LLM）。

做法：
  · 驱动真实的 pack.maybe_emit()（触发器 evaluate / 闸门 / 去重 marks / 冷却 全部真实运行），
    只把两处"离线跑不了"的桩掉：
      - LLM 生成：FakeLLM.chat 给一句占位台词 + 闸门恒回 SEND（fail-open 同口径）；
      - 派发到聊天：pack._emit_fn 记一行日志代替真发。
  · 情境 collect() 按一个"真实一周"脚本注入（用户作息 / 宣告事件 / 记忆 / 兴趣 / 饭点 / 熬夜）。
  · 闸门沿用真实规则镜像：静默时段 22:00-08:00 不开口 + 日上限 6 + 任意两条主动间隔 >=20min。

结论看末尾统计：7 天总开口数、按类型分布、按天分布、是否踩静默时段、最小间隔。
"""
from __future__ import annotations
import asyncio, ctypes, json, logging, subprocess, sys, time, types
from pathlib import Path

ROOT = r"G:\ai对话\11"
sys.path.insert(0, ROOT)

import src.proactive_pack.pack as packmod
from src.proactive_pack.pack import ProactivePack
from src.proactive_pack.situation import Situation, _fmt_gap
from src.proactive_pack.triggers import Ctx, evaluate

logging.getLogger("proactive_pack").setLevel(logging.WARNING)  # 露出拦下/异常


def notify_windows(title: str, message: str) -> str:
    """弹系统通知（Win10/11 Toast，失败回退 MessageBox）。返回实际用的方式。

    沙箱/无桌面会话下可能弹不出来，但绝不影响仿真本身完成（全部 try 包裹）。
    """
    # 1) WinRT Toast —— 非阻塞、真正的"系统通知弹窗"
    ps = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime] | Out-Null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x=$t.GetElementsByTagName('text');"
        "$x.Item(0).AppendChild($t.CreateTextNode('%s')) | Out-Null;"
        "$x.Item(1).AppendChild($t.CreateTextNode('%s')) | Out-Null;"
        "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('达妮娅');"
        "$n.Show([Windows.UI.Notifications.ToastNotification]::new($t));"
    ) % (title.replace("'", "''"), message.replace("'", "''"))
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return "toast"
    except Exception:
        pass
    # 2) MessageBox 回退（保证有提示，阻塞到用户点"确定"）
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0)
        return "msgbox"
    except Exception:
        return "none"

# ---- 仿真起点：2026-10-12 周一 00:00（本地时区） ----
SIM_START = time.mktime(time.strptime("2026-10-12 00:00:00", "%Y-%m-%d %H:%M:%S"))
DAY_SECONDS = 86400.0
INTEREST_TAGS = ["原神", "篮球", "科幻电影"]

# 一周作息脚本：lunch=午间聊；evening=晚间聊窗口；late=熬夜；ow=当天宣告去做的事
# ow_at 之后的傍晚用户"出门了"（不在 active 窗口），这样 pending 才有机会触发
DAYS = [
    # 0 周一
    {"lunch": True,  "evening": (20, 0, 22, 30), "ow": "游戏", "ow_exp": 5400, "ow_at": (20, 30)},
    # 1 周二（熬夜 day1）
    {"lunch": True,  "evening": (20, 0, 24, 0), "next_mid": (0, 0, 1, 0), "late": True, "streak": 1},
    # 2 周三（熬夜 day2，连续 → 触发节律熬夜）
    {"lunch": True,  "evening": (20, 0, 24, 0), "next_mid": (0, 0, 1, 0), "late": True, "streak": 2,
     "ow": "洗澡", "ow_exp": 2400, "ow_at": (21, 0)},
    # 3 周四
    {"lunch": True,  "evening": (20, 0, 22, 30)},
    # 4 周五（午间不聊；晚上宣告开会）
    {"lunch": False, "evening": (20, 0, 22, 30), "ow": "开会", "ow_exp": 5400, "ow_at": (20, 45)},
    # 5 周六（晚间聊到 23:00）
    {"lunch": True,  "evening": (20, 0, 23, 0)},
    # 6 周日
    {"lunch": True,  "evening": (20, 0, 22, 30)},
]

# 记忆候选（召回触发器用）：text 带情绪词 → salience>=0.34；ts 控制年龄窗口
MEMORIES = [
    {"key": "m1", "text": "我明天要去面试，心里好紧张", "ts": SIM_START + 1 * DAY_SECONDS},
    {"key": "m2", "text": "上周和男朋友吵架了，心里很难受", "ts": SIM_START + 2 * DAY_SECONDS},
    {"key": "m3", "text": "我感冒了，头疼浑身没劲", "ts": SIM_START + 4 * DAY_SECONDS},
]

# 兴趣分享素材池（本地文件，免网络）：text 含兴趣词
FEED_ITEMS = [
    {"id": "f1", "text": "原神 5.8 版本前瞻直播定档周五晚，新地图曝光", "tags": ["原神", "游戏"], "ts": SIM_START + 0.5 * DAY_SECONDS},
    {"id": "f2", "text": "NBA 总决赛抢七今晚开打，收视率创新高", "tags": ["篮球"], "ts": SIM_START + 1.5 * DAY_SECONDS},
    {"id": "f3", "text": "科幻电影《星际穿越》重映定档，4K 修复版", "tags": ["科幻电影"], "ts": SIM_START + 2.5 * DAY_SECONDS},
    {"id": "f4", "text": "原神新角色预告片播放量破千万", "tags": ["原神"], "ts": SIM_START + 3.5 * DAY_SECONDS},
    {"id": "f5", "text": "篮球世界杯分组抽签结果出炉", "tags": ["篮球"], "ts": SIM_START + 4.5 * DAY_SECONDS},
    {"id": "f6", "text": "经典科幻电影《银翼杀手》导演剪辑版上线", "tags": ["科幻电影"], "ts": SIM_START + 5.5 * DAY_SECONDS},
]

# 宣告事件 → 用户"回来"的活跃补丁（declare + expected + 10min 起聊 15min）
OWS = []
for d, spec in enumerate(DAYS):
    if spec.get("ow"):
        declare = SIM_START + d * DAY_SECONDS + spec["ow_at"][0] * 3600 + spec["ow_at"][1] * 60
        OWS.append({"label": spec["ow"], "ts": declare, "exp": spec["ow_exp"],
                    "back": declare + spec["ow_exp"] + 10 * 60})

RETURN_BLIPS = [o["back"] for o in OWS]


def _mins(now):
    lt = time.localtime(now)
    return lt.tm_wday, lt.tm_hour * 60 + lt.tm_min, lt.tm_hour


def user_active_at(now):
    wday, mins, _h = _mins(now)
    spec = DAYS[wday]
    if spec.get("lunch") and (12 * 60 + 30) <= mins <= (12 * 60 + 45):
        return True
    e = spec["evening"]
    sh, eh = e[0] * 60 + e[1], e[2] * 60 + e[3]
    if sh <= mins < eh:
        if spec.get("ow"):
            owm = spec["ow_at"][0] * 60 + spec["ow_at"][1]
            if mins >= owm:
                return False  # 宣告后出门了
        return True
    if spec.get("next_mid"):
        nm = spec["next_mid"]
        if nm[0] * 60 + nm[1] <= mins < nm[2] * 60 + nm[3]:
            return True
    for b in RETURN_BLIPS:
        if b <= now < b + 15 * 60:
            return True
    return False


class FakeHost:
    def __init__(self):
        self._cfg = types.SimpleNamespace(active_character_id="danya")
        self._idle_threshold_s = 180.0
        self._last_input_ts = 0.0
        self._duplex_last_speak_ts = 0.0
        self._recent_messages = []

    def _similar_to_recent(self, text):
        return False

    def _clean_duplex_speech(self, text):
        return text

    async def _schedule_duplex_emit(self, text, to_chat=True):
        pass

    def _record_duplex_ai_line(self, text):
        pass

    def _log_duplex_source(self, kind, text):
        pass


class FakeLLM:
    async def chat(self, messages, options=None):
        prompt = (messages[0]["content"] if messages else "") if isinstance(messages, list) else str(messages)
        if "SEND 或 SKIP" in prompt:
            return "SEND"
        return "（模拟主动台词）你刚说的那事后来怎么样了？"


# ---- 脚本化情境：替换 pack.collect ----
def scenario_collect(cid, *, now, last_input_ts, base_dir, flags, marks):
    wday, mins, hour = _mins(now)
    spec = DAYS[wday]
    gap_h = max(0.0, (now - last_input_ts) / 3600.0) if last_input_ts else 0.0
    label, _ = _fmt_gap((now - last_input_ts)) if last_input_ts else ("", 0.0)

    # 记忆候选：镜像真实 collect() 的 _memory_block —— 已回访过的先剔除（否则 recall 会无限刷）
    cands = [m for m in MEMORIES if not (marks and marks.recall_done(cid, m["key"]))]
    data = {
        "gap_h": round(gap_h, 2),
        "gap_label": label,
        "turns": 8 if gap_h <= 3 else 0,
        "interest_tags": INTEREST_TAGS,
        "memory_candidates": cands,
    }
    # 宣告事件跟进
    for o in OWS:
        if o["ts"] + o["exp"] <= now < o["ts"] + o["exp"] + 3 * 3600:
            data["pending_activity"] = {"label": o["label"], "expected_s": o["exp"],
                                        "ts": o["ts"], "text": "我去%s" % o["label"]}
            break
    # 生活节律
    is_late = hour >= 23 or hour < 5
    data["is_late_night"] = is_late
    data["night_streak"] = (spec.get("streak", 0) if is_late else 0)
    return Situation(text="", blocks={}, data=data)


async def main():
    # 每次运行用全新临时目录，避免上一轮写下的去重标记污染本轮（真实去重是跨会话持久的）
    import tempfile
    base_dir = Path(tempfile.mkdtemp(prefix="sim_pp_"))
    # 本地素材池（免网络）
    (base_dir / "proactive_pack_feed.json").write_text(
        json.dumps(FEED_ITEMS, ensure_ascii=False), encoding="utf-8")

    host = FakeHost()
    pack = ProactivePack(host=host, llm=FakeLLM(), base_dir=base_dir, name="达妮娅")
    flags = dict(pack.flags)
    flags.update({k: True for k in ("enabled", "festival", "pending", "followup",
                                     "recall", "rhythm", "silence", "share", "llm_gate")})
    flags["feed_hot"] = False
    flags["day_cap"] = 6
    pack.flags = flags

    # 桩掉 collect（用脚本情境）
    packmod.collect = scenario_collect

    # 仿真闸门：静默时段 + 日上限 + 任意两条主动间隔 >=20min（镜像真实 _proactive_decision）
    emissions = []

    def _sim_gate(self, a, b=None):
        # relation_gate(cid, now) → b 非空；light_gate(now) → b 为 None
        now = b if b is not None else a
        h = time.localtime(now).tm_hour
        if h >= 22 or h < 8:
            return False, "sim_silence"
        if len([e for e in emissions if e["day"] == time.localtime(now).tm_wday]) >= int(flags.get("day_cap", 6)):
            return False, "sim_daycap"
        if emissions and (now - emissions[-1]["ts"]) < 20 * 60:
            return False, "sim_cooldown"
        return True, "sim_ok"

    async def _noop_emit(text):
        return None

    pack._emit_fn = _noop_emit
    pack._relation_gate = types.MethodType(_sim_gate, pack)
    pack._light_gate = types.MethodType(_sim_gate, pack)

    # ---- 跑 7 天，10 分钟一跳 ----
    STEP = 600
    END = SIM_START + 7 * DAY_SECONDS
    t = SIM_START
    last_active_print = None
    while t <= END:
        if user_active_at(t):
            host._last_input_ts = t
        ok = await pack.maybe_emit(now=t)
        if ok:
            emissions.append({
                "ts": t, "day": time.localtime(t).tm_wday,
                "hour": time.localtime(t).tm_hour, "kind": pack._last_kind,
            })
            # 她主动开口后，模拟"用户回了 15 分钟"（真实里用户会回，否则沉默档会无限刷屏）
            host._last_input_ts = t
            RETURN_BLIPS.append(t)
        t += STEP

    # ---- 统计 ----
    WK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    by_kind = {}
    by_day = {i: 0 for i in range(7)}
    quiet_viol = 0
    for e in emissions:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        by_day[e["day"]] += 1
        if e["hour"] >= 22 or e["hour"] < 8:
            quiet_viol += 1
    gaps = [(emissions[i + 1]["ts"] - emissions[i]["ts"]) / 60.0 for i in range(len(emissions) - 1)]
    min_gap = min(gaps) if gaps else 0
    max_gap = max(gaps) if gaps else 0

    print("=" * 60)
    print("主动互动包 · 7 天离线仿真结果")
    print("=" * 60)
    print("总开口数：%d 次 / 7 天（日均 %.1f）" % (len(emissions), len(emissions) / 7))
    print("\n按类型分布：")
    for k in sorted(by_kind):
        print("  %-10s %d" % (k, by_kind[k]))
    print("\n按天分布：")
    for i in range(7):
        bar = "#" * by_day[i]
        print("  %s：%d %s" % (WK[i], by_day[i], bar))
    print("\n静默时段(22-8)违规开口：%d" % quiet_viol)
    print("相邻两条最小间隔：%.0f 分钟 / 最大间隔：%.0f 分钟" % (min_gap, max_gap))
    print("\n逐条时间线（含间隔）：")
    prev = None
    for e in emissions:
        gap = (e["ts"] - prev) / 60.0 if prev is not None else 0.0
        prev = e["ts"]
        lt = time.localtime(e["ts"])
        print("  %s %02d:%02d  kind=%-9s  (距上条 %.0f min)" % (
            WK[e["day"]], lt.tm_hour, lt.tm_min, e["kind"], gap))

    # 人性化判读
    print("\n--- 判读 ---")
    verdict = []
    if quiet_viol == 0:
        verdict.append("✅ 静默时段零开口（不半夜吵人）")
    else:
        verdict.append("❌ 有 %d 次在静默时段开口" % quiet_viol)
    if min_gap >= 20:
        verdict.append("✅ 相邻主动间隔>=20min（不像刷屏机器人）")
    else:
        verdict.append("⚠️ 最小间隔仅 %.0f 分钟（偏密）" % min_gap)
    if 7 <= len(emissions) <= 45:
        verdict.append("✅ 周开口量 %d 次，落在朋友级区间" % len(emissions))
    elif len(emissions) < 7:
        verdict.append("⚠️ 周开口仅 %d 次，偏冷清" % len(emissions))
    else:
        verdict.append("⚠️ 周开口 %d 次，偏多（检查日上限/冷却）" % len(emissions))
    if all(by_day[i] <= 6 for i in range(7)):
        verdict.append("✅ 每日均未突破上限 6")
    for v in verdict:
        print("  " + v)

    # ---- 落盘报告 + 系统通知 ----
    report_path = Path(ROOT) / "tools" / "proactive_pack_sim_report.md"
    L = []
    L.append("# 主动互动包 · 7 天离线仿真报告\n")
    L.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("- 总开口：**%d 次 / 7 天（日均 %.1f）**" % (len(emissions), len(emissions) / 7))
    L.append("\n## 按类型\n")
    for k in sorted(by_kind):
        L.append("- `%s`：%d" % (k, by_kind[k]))
    L.append("\n## 按天\n")
    for i in range(7):
        L.append("- %s：%d" % (WK[i], by_day[i]))
    L.append("\n## 安全阀\n")
    L.append("- 静默时段(22:00–08:00)违规开口：**%d**" % quiet_viol)
    L.append("- 相邻两条最小间隔：**%.0f 分钟** / 最大：%.0f 分钟" % (min_gap, max_gap))
    L.append("\n## 人性化判读\n")
    for v in verdict:
        L.append("- %s" % v)
    L.append("\n## 逐条时间线（含间隔）\n")
    prev = None
    for e in emissions:
        gap = (e["ts"] - prev) / 60.0 if prev is not None else 0.0
        prev = e["ts"]
        lt = time.localtime(e["ts"])
        L.append("- %s %02d:%02d  `kind=%-9s`  (距上条 %.0f min)" % (
            WK[e["day"]], lt.tm_hour, lt.tm_min, e["kind"], gap))
    report_path.write_text("\n".join(L), encoding="utf-8")
    print("\n报告已写入：%s" % report_path)

    how = notify_windows("达妮娅 · 主动互动仿真完成",
                         "7 天共开口 %d 次，日均 %.1f；报告：%s" % (
                             len(emissions), len(emissions) / 7, report_path.name))
    print("系统通知：%s" % how)

    # 清理临时目录
    try:
        import shutil
        shutil.rmtree(base_dir, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
