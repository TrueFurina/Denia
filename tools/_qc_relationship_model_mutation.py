# -*- coding: utf-8 -*-
"""关系模型门（tools/test_relationship.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~44 轮）：本门不是 unittest，自己打 `===== N/M 通过 =====`；
「红了」= 非零退出 + 有汇总行（不是半路崩掉），「命中」= 判据名出现在 `[FAIL]` 行里。
日志里的 Traceback / `[relationship] …` 提示行一律不算崩溃。

**第 44 轮探针查出的 21 处零判据**（本套件的存在理由），按成因分三类：

  ① **判据绿的原因跟它名字无关**（2 处）：
     「重复内容增益打折」绿是因为好感越接近 100 单轮增益本来就越小（饱和），
     把 `DEDUP_FACTOR` 改成 1.0 照样满足那个不等式；「软每日上限」同理 ——
     末 3 轮增益小是**饱和**造成的，跟上限无关。修法：改成只差一个变量的隔离对照。
  ② **判据只覆盖一半分支**（10 处）：心情只看均值不看"最近三句最负"、72h 空窗分支、
     雀跃分支、想你门槛、旧档补字段、_safe_id 净化、未知原型回落、load_recent 截断、
     count 空行、两字敷衍阈值、否定语境守卫。
  ③ **v2 引擎整层没有判据**（4 处）：阶段迟滞、疤自愈、交情沉淀、情绪降温 ——
     `RelationshipManager` 用的是 `RelationshipEngineV2`，而门里一条 v2 专属判据都没有。

运行：  python tools/_qc_relationship_model_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_relationship.py")]

REL = "src/companion/relationship.py"
V2 = "src/companion/relationship_v2.py"
HS = "src/companion/history_store.py"
SC = "src/companion/session_clock.py"
JD = "src/companion/relationship_judge.py"

MUTS = [
    # ---- 好感动力学 ----
    ("newbie_off", REL,
     '    def _is_newbie(self, state: dict[str, Any], t: float) -> bool:\n'
     '        if int(state.get("turns", 0)) < int(self.newbie_turns):',
     '    def _is_newbie(self, state: dict[str, Any], t: float) -> bool:\n'
     "        return False\n"
     '        if int(state.get("turns", 0)) < int(self.newbie_turns):',
     "新手期内负向被闸住",
     "新手期保护整体失效 —— 刚建的角色第一句挨骂就掉好感（2026-10-13 专门加的护栏）", False),

    ("newbie_negative_open", REL,
     "            valence = max(0.0, valence)",
     "            valence = valence",
     "新手期内负向被闸住",
     "新手期只清了 harsh/perf、忘了把 valence 抬到非负 —— 冷句照样扣分", False),

    ("dedup_off", REL,
     "DEDUP_FACTOR = 0.35",
     "DEDUP_FACTOR = 1.0",
     "重复内容增益打折",
     "去重彻底失效 —— 用户复读同一句话能一路把好感刷满（原判据被饱和度满足，测不出来）", False),

    ("daily_cap_off", REL,
     '        return SOFT_CAP_FACTOR if float(dg.get("sum") or 0.0) >= SOFT_DAILY_CAP else 1.0',
     "        return 1.0",
     "当日已满额",
     "软每日上限失效 —— 一天聊 200 句就能把好感刷满（原判据被饱和度满足，测不出来）", False),

    ("confidence_scale_off", REL,
     "        conf_scale = confidence if confidence < 1.0 else 1.0",
     "        conf_scale = 1.0",
     "ambiguous 惩罚被缩放",
     "裁判把握低（ambiguous）时不再打折 —— 一句含糊的话按满额扣分", False),

    ("sufficient_gate_off", REL,
     "        if not sufficient:",
     "        if False:",
     "sufficient=False → 留痕 skipped=True",
     "证据充分度门拆掉 —— 孤立一个「嗯」也会移动好感、污染心情（2026-10-11 修的误判）", False),

    ("warmth_floor_off", REL,
     'DIM_FLOOR = {"trust": 20.0, "intimacy": 20.0, "warmth": 15.0}',
     'DIM_FLOOR = {"trust": 20.0, "intimacy": 20.0, "warmth": 5.0}',
     "长期不聊也不跌破留底",
     "热络的留底被降低 —— 长期不聊会一路掉到 5 分（「疏远但不绝情」的底线）", False),

    # ---- 落盘 ----
    ("badfile_not_quarantined", REL,
     "                p.replace(bad)",
     "                pass",
     "坏文件被隔离为",
     "坏状态文件不再隔离 —— 用户手改坏的 json 被静默丢弃，查不出为什么好感清零", False),

    ("save_not_atomic", REL,
     "        tmp.replace(p)  # 原子替换",
     '        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")',
     "原子写无残留 .tmp",
     "原子写改成原地覆盖 —— 写到一半崩掉就是半个 json，之后所有读都从默认重建", False),

    ("old_state_not_migrated", REL,
     "        for k, v in base.items():\n            raw.setdefault(k, v)",
     "        pass",
     "旧档缺的顶层字段被补全",
     "旧档不再补字段 —— 老用户升级后 `state['mood']` 直接 KeyError（升级即崩）", False),

    ("safe_id_off", REL,
     '    s = re.sub(r"[^0-9A-Za-z_\\-\\u4e00-\\u9fff]", "_", str(char_id or "").strip())\n'
     '    return s or "default"',
     '    return str(char_id or "")',
     "_safe_id 挡住路径穿越",
     "角色 id 不做净化 —— 角色名写成 `../../x` 就能把状态文件写到 data/ 外面", False),

    ("unknown_archetype_kept", REL,
     '            st["archetype"] = archetype if archetype in ARCHETYPES else DEFAULT_ARCHETYPE',
     '            st["archetype"] = archetype',
     "未知原型回落默认",
     "未知原型照收 —— 原型名写错（或旧档残留）时 `archetype_params` 静默回落，行为与人设对不上", False),

    # ---- 心情 ----
    ("mood_mean_only", REL,
     "            sev = min(v, spike)",
     "            sev = v",
     "最后一句冷下来",
     "心情只看 6 句加权均值 —— 前面聊得再好，被凶一句也判不出来（代码注释明确承诺「不能被均值稀释」）", False),

    ("gap72_off", REL,
     "        if gap_h >= 72.0:",
     "        if gap_h >= 720.0:",
     "空窗 100 小时",
     "72 小时空窗分支失效 —— 三天没聊还是「想你」而不是「蔫蔫的」，也永远到不了「明显」档", False),

    ("cheerful_never", REL,
     "        if v >= 0.45:",
     "        if v >= 4.5:",
     "v1 心情：强正向 → 雀跃",
     "「雀跃」分支永不触发 —— 她最高兴的时候也只是「平静」（v2 的细分以这个标签为兜底）", False),

    ("miss_you_cut_off", REL,
     '        return {"label": "想你" if intimacy >= 45 else "蔫蔫的",',
     '        return {"label": "想你" if intimacy >= 450 else "蔫蔫的",',
     "同样 100 小时，亲密度够",
     "「想你」的亲密度门槛被抬到 450 —— 无论多亲，久不联系都只会「蔫蔫的」", False),

    # ---- 历史 ----
    ("history_max_content_off", HS,
     "        if not text or len(text) > MAX_CONTENT:",
     "        if not text:",
     "超长内容被跳过",
     "超长内容不再拦 —— 一次粘贴整篇文档就写进历史，之后每次恢复都要读它", False),

    ("load_recent_no_slice", HS,
     "        return out[-n:]\n",
     "        return out\n",
     "load_recent(2) 只给最近 2 条",
     "load_recent 不再截断 —— 「最近 N 条」变成「全部」，恢复界面和 prompt 预算一起爆", False),

    ("bad_line_drops_all", HS,
     "                        bad += 1",
     "                        return []",
     "坏行被跳过、不影响其余记录",
     "一行坏数据就放弃整份历史 —— 用户重启后聊天记录全没了", False),

    ("count_blank_lines", HS,
     "                    if line.strip():\n                        n += 1",
     "                    n += 1",
     "空行不计入条数",
     "空行也算一条 —— 历史条数虚高，「最近 N 条」会读到一堆空记录", False),

    # ---- 会话时钟 ----
    ("empty_session_logged", SC,
     '        if float(cur.get("last_touch") or 0) <= float(cur.get("start") or 0):\n            return',
     "        if False:\n            return",
     "零长度会话不写日志",
     "零长度会话也写日志 —— 每次启动/退出都刷一条 0 分钟的会话，在线时长统计被稀释", False),

    ("idle_never_splits", SC,
     '        elif (t - float(cur.get("last_touch") or t)) > IDLE_SPLIT_MIN * 60.0:',
     "        elif False:",
     "空档超过 15 分钟",
     "断档不再切分会话 —— 关机一整晚，第二天的心跳仍算同一段（熬夜/在线时长全错）", False),

    ("night_end_9", SC,
     "NIGHT_END_H = 5",
     "NIGHT_END_H = 9",
     "07:00 不属于夜间窗口",
     "夜间窗口上界 5→9 点 —— 早上七点起床被算成「熬夜」", False),

    ("any_hour_is_night", SC,
     "                if max(a, n0) < min(b, n1):",
     "                if True:",
     "白天活动不算熬夜",
     "任何时段都算夜间活动 —— 白天用一天，晚上就报「连续熬夜」", False),

    ("online_minutes_as_hours", SC,
     "        return round(total / 60.0, 1)",
     "        return round(total / 3600.0, 1)",
     "今日在线分钟按",
     "在线分钟按小时算 —— 用了两小时报「2 分钟」（差 60 倍）", False),

    # ---- 规则裁判 ----
    ("sufficient_always_true", JD,
     "    sufficient = not (perf >= 0.5 and L <= 6 and not (care or repair or harsh))",
     "    sufficient = True",
     "规则裁判：'嗯' sufficient=False",
     "证据充分度恒 True —— 所有「嗯/哦」都被当成决断性关系信号", False),

    ("perf_threshold_lowered", JD,
     "        elif L <= 3:\n            perf = 0.5",
     "        elif L <= 3:\n            perf = 0.4",
     "两字冷句判为敷衍",
     "两字冷句的敷衍度 0.5→0.4 —— 刚好跌破 sufficient 的阈值，冷句被当成正常互动", False),

    ("negation_guard_off", JD,
     '    affection = (any(k in n for k in _AFFECTION)\n'
     '                 and "不喜欢" not in n and "讨厌" not in n and "别喜欢" not in n)',
     "    affection = any(k in n for k in _AFFECTION)",
     "'我不喜欢你' 不算表白",
     "否定语境守卫拆掉 —— 「我不喜欢你」因为含「喜欢」被判成表白，好感反而涨（方向反了）", False),

    # ---- v2 引擎 ----
    ("stage_hyst_up_off", V2,
     "            return raw if s >= _CUT_OF.get(raw, 0.0) + STAGE_HYST_UP else cur",
     "            return raw",
     "升档要够稳",
     "阶段升档迟滞失效 —— 分数刚过门槛就跳档，一句话让关系横跳", False),

    ("scar_never_heals", V2,
     '            state["scar"] = round(max(0.0, _scar(state) - SCAR_HEAL_PER_DAY * days), 4)',
     '            state["scar"] = _scar(state)',
     "疤会随时间自愈",
     "疤永不愈合 —— 一次重话记一辈子，信任恢复被永久压制", False),

    ("bonding_off", V2,
     "        bonding = _bonding_factor(state)                 # ③ 交情越久掉得越慢",
     "        bonding = 1.0",
     "交情越久衰减越慢",
     "交情沉淀失效 —— 认识一年的关系和刚认识三天掉得一样快", False),

    ("mood_never_cools", V2,
     "        if remain < COOL_DONE:",
     "        if False:",
     "气头会自然降温",
     "情绪永不降温 —— 100 小时前的气头，现在还是「炸毛」", False),
]


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _run():
    return subprocess.run([PY] + GATE, cwd=PROJ, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _summary(r):
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("====="):
            return ln.strip()
    return "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(re.search(r"^===== \d+/\d+ 通过 =====", _out(r), re.M))


def _hit(r, name):
    return any(ln.strip().startswith("[FAIL]") and name in ln
               for ln in (r.stdout or "").splitlines())


def main():
    base = _run()
    if not (base.returncode == 0
            and re.search(r"^===== (\d+)/\1 通过 =====", _out(base), re.M)):
        # 带 FAIL 前缀：空变异体检要能区分「套件自己报了失败」和「套件崩了」。
        print("FAIL 基线不是绿的 —— 后面的红不能证明任何事：")
        print(_out(base)[-1500:])
        return 2
    print("基线：绿 | %s" % _summary(base))

    rows = []
    for key, rel, old, new, expect, why, all_hits in MUTS:
        p = os.path.join(PROJ, *rel.split("/"))
        orig = open(p, encoding="utf-8").read()
        pairs = list(old) if isinstance(old, tuple) else [(old, new)]
        counts = [orig.count(o) for o, _ in pairs]
        if any(c == 0 for c in counts) or (not all_hits and any(c != 1 for c in counts)):
            print("  %-24s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
            rows.append((key, "ANCHOR", why))
            continue
        h0 = _sha(p)
        try:
            text = orig
            for _o, _n in pairs:
                text = text.replace(_o, _n)
            open(p, "w", encoding="utf-8", newline="").write(text)
            r = _run()
        finally:
            open(p, "w", encoding="utf-8", newline="").write(orig)
            if _sha(p) != h0:
                raise SystemExit("还原失败（字节不一致）：%s" % rel)
        judged = _judged(r)
        hit = _hit(r, expect)
        tag = "红·命中" if (judged and hit) else ("红·跑偏" if judged else "没红")
        print("  %-24s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s"
                  % (expect, [ln[:70] for ln in (r.stdout or "").splitlines()
                              if ln.strip().startswith("[FAIL]")][:3]))
        rows.append((key, tag, why))

    print("\n" + "=" * 72)
    ok = sum(1 for _, t, _ in rows if t == "红·命中")
    print("刀 %d 把：红且命中 %d、红但跑偏 %d、没红 %d、锚点腐烂 %d"
          % (len(rows), ok,
             sum(1 for _, t, _ in rows if t == "红·跑偏"),
             sum(1 for _, t, _ in rows if t == "没红"),
             sum(1 for _, t, _ in rows if t == "ANCHOR")))
    if ok != len(rows):
        print("\n没验住的：")
        for key, t, why in rows:
            if t != "红·命中":
                print("  [%s] %s —— %s" % (t, key, why))
        return 1
    print("全部 %d 把刀都红在指定判据上。" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
