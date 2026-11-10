# -*- coding: utf-8 -*-
"""主动输出分流门（tools/test_proactive_chat_routing.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~45 轮）：本门不是 unittest，自己打
`结果: N PASS / M FAIL / K SKIP`；「红了」= 非零退出 + 有汇总行（不是半路崩掉），
「命中」= 判据名出现在 `FAIL` 行里。

为什么要有这套刀（第 45 轮探针查出来的零判据，按成因分四类）：

  ① **`SKIP` 在返回值里算通过**（最刺眼的一处）：第 5 组用例「记忆型主动」命中不了就
     `SKIP += 1`，而 `return 0 if FAIL == 0 else 1` 根本不看 SKIP —— 把
     `_maybe_memory_initiative` 整段改成 `return False`，本门照样绿。
     `SKIP` 是「本门没有意见」，不是「本门认为没问题」。
  ② **桩把整段分支钉死了**：`_mk_stub` 里 `_in_call_flag = False`，于是
     `_flush_duplex_emit` 后半段那 130 行**通话生命周期闸门**（2026-09-23「她不理我」
     那次的全部修复：开场白放行 / 系统兜底豁免 / 回应窗口 / 静默暂存待补发）
     一次都没跑过。`native_audio=False` 同理，2026-09-23 的原生音频硬闸整段不可见。
  ③ **只测 payload 不测记账**：`_duplex_emit_start_ts` / `_duplex_emit_is_fallback` /
     `_long_session_warned` / `_mem_init_idx` 这些「下一句要靠它」的字段，一条判据都没有。
  ④ **六道前置闸门只走了 happy path**：通话让位、LLM 未就绪、8s 防打断、
     `0.0` 当无解、空闲阈值、批冷却 —— 全部只在「已放行」状态下测过。

运行：  python tools/_qc_proactive_routing_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_proactive_chat_routing.py")]
P = "src/companion/proactive.py"

MUTS = [
    # ================= _schedule_duplex_emit =================
    ("initiative_switch_off", P,
     "        if to_chat and not self.chat_initiative:\n"
     "            logger.debug('主动互动已关闭：丢弃一条聊天页主动消息（to_chat=True）')\n"
     "            return",
     "        if False:\n"
     "            logger.debug('主动互动已关闭：丢弃一条聊天页主动消息（to_chat=True）')\n"
     "            return",
     "8b 主动互动关闭 → 不 publish",
     "主动互动关掉了，聊天页主动照样发 —— 用户在设置里关的是个假开关", False),

    ("initiative_switch_overreach", P,
     "        if to_chat and not self.chat_initiative:",
     "        if not self.chat_initiative:",
     "8c 主动互动关闭 → 看屏那条照发",
     "开关越界 —— 关掉聊天页主动，连「看屏评论」一起杀（2026-10-17 明确要解耦的三者之一）", False),

    ("start_ts_not_recorded", P,
     "            self._duplex_emit_start_ts = time.time()",
     "            pass",
     "9a 新段起点被记下（原生音频对齐的基准）",
     "新段起点不记 —— 原生音频对齐基准永远是 0，等于「永远对不齐」", False),

    ("fallback_flag_not_recorded", P,
     "            self._duplex_emit_is_fallback = bool(sys_fallback)",
     "            pass",
     "9b 兜底发声被标记为兜底",
     "兜底发声不被标记 —— 通话里系统替她出声会被当成「她回应了用户」，吃掉回应用户的配额", False),

    ("fallback_flag_not_cleared", P,
     "            self._duplex_emit_is_fallback = False",
     "            pass",
     "9d 真回应到达 → 兜底标志被洗掉",
     "兜底标志粘住了 —— 一句兜底之后，整段真回答都被当兜底（不刷新 _la，后续句全判自言自语）", False),

    ("to_chat_not_ored", P,
     "            if to_chat:\n                self._duplex_emit_to_chat = True",
     "            self._duplex_emit_to_chat = to_chat",
     "10a 去聊天页意图不被后续看屏句覆盖",
     "「任一句声明去聊天页」退化成「看最后一句」—— 攒句合并后聊天页那句话凭空消失", False),

    ("to_chat_never_set", P,
     "            if to_chat:\n                self._duplex_emit_to_chat = True",
     "            if False:\n                self._duplex_emit_to_chat = True",
     "6a 混合缓冲 to_chat 被 OR 置 True",
     "同段第二句的去聊天页意图被丢掉", False),

    ("buf_overwrite", P,
     "        self._duplex_emit_buf = self._duplex_emit_buf + text if self._duplex_emit_buf else text",
     "        self._duplex_emit_buf = text",
     "6c 混合缓冲两句子拼接",
     "攒句变成覆盖 —— 一个 debounce 窗口里只留下最后一句，前面的全丢", False),

    # ================= _flush_duplex_emit =================
    ("to_chat_always_false", P,
     "        _to_chat = bool(getattr(self, '_duplex_emit_to_chat', False))",
     "        _to_chat = False",
     "2b 聊天页主动 to_chat=True",
     "意图读不出来 —— 聊天页主动全部退化成桌宠气泡（用户报的那个 bug 原样复发）", False),

    ("to_chat_not_reset", P,
     "        _to_chat = bool(getattr(self, '_duplex_emit_to_chat', False))\n"
     "        self._duplex_emit_to_chat = False",
     "        _to_chat = bool(getattr(self, '_duplex_emit_to_chat', False))",
     "2d 去聊天页意图用后复位",
     "意图不复位 —— 一句聊天页主动之后，后面所有看屏评论都被改道到聊天页（串味）", False),

    ("is_proactive_always_true", P,
     "            'is_proactive': (not _to_chat),   # 聊天页主动不得被改道到桌宠气泡",
     "            'is_proactive': True,",
     "2a 聊天页主动 is_proactive=False（不再被改道桌宠）",
     "`is_proactive` 恒 True —— webapp 会把聊天页主动统一改道到桌宠气泡，聊天页永远看不到她主动说话", False),

    ("trigger_always_screen", P,
     "            'trigger': ('proactive_chat' if _to_chat else 'proactive_screen'),",
     "            'trigger': 'proactive_screen',",
     "2c 聊天页主动 trigger=proactive_chat",
     "trigger 不跟着意图走 —— 前端/日志无法区分「聊天页主动」和「看屏评论」", False),

    ("payload_to_chat_dropped", P,
     "            'to_chat': _to_chat,",
     "            'to_chat': False,",
     "2b 聊天页主动 to_chat=True",
     "payload 里的 to_chat 被钉成 False —— 前端按「她主动发来的一条消息」渲染的标记永远不亮", False),

    ("empty_buf_still_publishes", P,
     "        if not buf:\n            return\n        _dm = self._duplex_mode",
     "        _dm = self._duplex_mode",
     "7 空缓冲 flush → 不 publish",
     "空缓冲也发 —— 每个 debounce 定时器到点都往聊天页推一条空气泡", False),

    ("no_call_record", P,
     "        if self._in_call():\n"
     "            try:\n"
     "                await self._publish_call_record(",
     "        if False:\n"
     "            try:\n"
     "                await self._publish_call_record(",
     "12a 通话中出声 → 写入通话记录",
     "通话中出声不留通话记录 —— 通话记录里只有用户的话，看起来像她全程没吭声", False),

    ("native_audio_gate_off", P,
     "        if getattr(self, '_duplex_native_audio', False):",
     "        if True:",
     "11c native_audio=false 硬闸 → 绝不漏原生音频（改走 TTS）",
     "2026-09-23 那道硬闸拆掉 —— `native_audio=false` 时原生音频照样漏出，用户听到「达妮娅但扭曲」", False),

    ("audio_lookback_off", P,
     "                if _a_start >= (_start - _look) and _age <= _max_age:",
     "                if _age <= _max_age:",
     "11e 音频起点早于本句 20s（超 8s 回看窗）→ 丢弃（防串音）",
     "只看「新鲜」不看「起点」—— 上一轮的残留音频被本轮新片刷成新鲜，串到这一句上", False),

    ("audio_age_off", P,
     "                if _a_start >= (_start - _look) and _age <= _max_age:",
     "                if _a_start >= (_start - _look):",
     "11d 音频过期（>10s）→ 丢弃",
     "过期判据失效 —— 陈腐音频照样随句出声", False),

    ("audio_not_cleared", P,
     "                logger.info('双工原生语音被 native_audio=false 硬闸拦截（走 TTS 合成，不漏出本地模糊达妮娅）')\n"
     "        self._duplex_emit_audio = bytearray()",
     "                logger.info('双工原生语音被 native_audio=false 硬闸拦截（走 TTS 合成，不漏出本地模糊达妮娅）')",
     "11b 出声后音频缓冲被清空（不串到下一句）",
     "音频缓冲用完不清 —— 下一句会把这一句的语音再放一遍", False),

    # ================= check_and_trigger 的六道前置闸门 =================
    ("no_yield_in_call", P,
     "            if self._in_call():\n                return\n"
     "            if self._llm is None or not self._llm_generate:",
     "            if self._llm is None or not self._llm_generate:",
     "13x 通话中不触发空闲主动",
     "通话中不让位 —— 用户正在打电话，桌宠突然在聊天页插一句「在忙什么呢」", False),

    ("no_llm_guard", P,
     "            if self._in_call():\n                return\n"
     "            if self._llm is None or not self._llm_generate:\n                return",
     "            if self._in_call():\n                return\n            if False:\n                return",
     "13y LLM 未就绪不开口",
     "LLM 没就绪也往下走 —— 直接 `self._llm.chat` 抛 AttributeError，被最外层 except 吞成一条 WARNING", False),

    ("no_self_interrupt_guard", P,
     "            if _last_spk and (time.time() - _last_spk) < 8.0:\n                return",
     "            if False:\n                return",
     "13z 刚说过话 8s 内不抢话",
     "刚播完一句 8s 内又抢话 —— 看屏评论和空闲主动叠着说，像自己跟自己吵架", False),

    ("boot_ts_gives_up", P,
     "            if not _last_in:\n"
     "                _last_in = float(getattr(self, '_session_boot_ts', 0.0) or 0.0)",
     "            if not _last_in:\n                return",
     "13w 首次打字前按开机时刻起算",
     "2026-10-11 那个「第二层死触发」原样复发 —— 开机后用户还没打字之前，空闲主动恒不触发（`0.0` 是 falsy）", False),

    ("idle_threshold_off", P,
     "            if _idle < self._idle_threshold_s:\n                return",
     "            if False:\n                return",
     "13v 未达空闲阈值不开口",
     "空闲阈值不判 —— 用户每 10 秒就被关心一次", False),

    ("batch_cooldown_off", P,
     "            if now - self._last_proactive_or_zero() < self._batch_interval_s:\n                return",
     "            if False:\n                return",
     "13u 批冷却内不重复开口",
     "批冷却不判 —— 刚主动过一句，10 秒后又来一句", False),

    ("relation_gate_off", P,
     "                    if not _ok:",
     "                    if False:",
     "13t 关系闸门拦下不开口",
     "关系闸门形同虚设 —— 关系冷却/每日上限/用户设置全部失效，她会一直找人说话", False),

    ("long_session_falls_through", P,
     "                        if await self._emit_long_session_break(_cid, now):\n"
     "                            log_relation_event(\"long_session_break\", \"建议休息\", _cid)\n"
     "                            return",
     "                        if await self._emit_long_session_break(_cid, now):\n"
     "                            log_relation_event(\"long_session_break\", \"建议休息\", _cid)",
     "13s 长会话收尾后不再叠一句通用关心",
     "长会话收尾之后不 return —— 紧接着又走通用引信，同一轮说两句（「歇会儿吧」+「在忙什么呢」）", False),

    ("memory_initiative_not_exclusive", P,
     "            if await self._maybe_memory_initiative(now, _name, _hint, _idle):\n                return",
     "            await self._maybe_memory_initiative(now, _name, _hint, _idle)",
     "13r 记忆主动开口后不再走通用引信",
     "两条主动路径不再互斥 —— 记忆主动说完，通用关心再说一句（代码注释明确写「互斥，不重复开口」）", False),

    ("empty_text_guard_off", P,
     "            if not text or self._is_empty_filler(text) or self._similar_to_recent(text):\n"
     "                return\n"
     "            self._last_proactive = now",
     "            self._last_proactive = now",
     "13q 空句不开口",
     "空句/废话/重复不拦 —— LLM 返回空串时照样往聊天页推一条空气泡，并把 `_last_proactive` 记上（吃掉批冷却）", False),

    # ================= 三条入口的 to_chat（对照刀） =================
    ("long_break_loses_to_chat", P,
     "            # 【2026-10-11】长会话收尾属\"聊天页主动\"→ to_chat=True\n"
     "            await self._schedule_duplex_emit(text, to_chat=True)",
     "            await self._schedule_duplex_emit(text)",
     "4c 长会话收尾 → to_chat=True",
     "长会话收尾退回桌宠气泡 —— 「该歇会儿了」飘在 Live2D 头上，聊天页看不到", False),

    ("memory_initiative_loses_to_chat", P,
     "            # 【2026-10-11】记忆型主动属\"聊天页主动\"→ to_chat=True\n"
     "            await self._schedule_duplex_emit(text, to_chat=True)",
     "            await self._schedule_duplex_emit(text)",
     "5b 记忆型主动 → to_chat=True",
     "记忆型主动退回桌宠气泡 —— 「她记得你」那句话永远不进聊天页", False),

    ("idle_trigger_loses_to_chat", P,
     "            # 【2026-10-11】聊天页空闲主动 → 必须落聊天页（to_chat=True），\n"
     "            # 否则被 webapp 当\"双工互动\"改道到桌宠气泡（用户报\"聊天页主动失效\"的根因）。\n"
     "            await self._schedule_duplex_emit(text, to_chat=True)",
     "            await self._schedule_duplex_emit(text)",
     "3b check_and_trigger 置位 to_chat=True",
     "空闲主动退回桌宠气泡 —— 这就是用户反复报的「聊天页主动失效」", False),

    # ================= 通话生命周期闸门（2026-09-23） =================
    ("opening_line_blocked", P,
     "            if not self._duplex_opened:",
     "            if False:",
     "13b 开场白置位 _duplex_opened",
     "开场白不放行 —— 一接通电话她就死寂（`_lu=0`，还没听到用户任何声）", False),

    ("fallback_not_exempt", P,
     "            elif _is_fb:",
     "            elif False:",
     "13g 系统兜底在静默中照样出声",
     "系统兜底不豁免 —— 哑火看门狗替她出声的那句被当自言自语暂存，用户听到的是持续死寂", False),

    ("fallback_refreshes_la", P,
     "            if not _is_fb:\n"
     "                self._duplex_last_ai_emit_ts = _now",
     "            if True:\n"
     "                self._duplex_last_ai_emit_ts = _now",
     "13h 兜底不刷新 _la",
     "兜底也刷新 `_la` —— 紧接着到来的真实回答被算成「距上次开口 0s」，续接判据误判，形成 09-22 实测过的死锁", False),

    ("silence_passes", P,
     "            elif _lu > 0 and (_since_u <= _fresh",
     "            elif True or (_since_u <= _fresh",
     "13c 用户静默超窗 → 不出声",
     "用户静默也放行 —— 20:08 那通「井喷式吐了 13 句填充话」原样复发", False),

    ("reply_window_x10", P,
     "            elif _lu > 0 and (_since_u <= _fresh",
     "            elif _lu > 0 and (_since_u <= _fresh * 10",
     "13c 用户静默超窗 → 不出声",
     "回应窗口放大 10 倍 —— 10 分钟前说过一句话，之后她自言自语全算「在回应你」", False),

    ("pending_not_stored", P,
     "                self._call_pending_text = buf\n"
     "                self._call_pending_ts = _now",
     "                self._call_pending_ts = _now",
     "13d 静默句被暂存待补发",
     "暂存句不存 —— 2026-09-23 明确修掉的「单槽覆盖 / 直接丢弃」回退成「直接丢弃」，真回答静默消失", False),

    # ================= 记账 =================
    ("long_session_not_marked", P,
     "            self._long_session_warned = True",
     "            pass",
     "4d 长会话收尾后置位 _long_session_warned",
     "播过不记账 —— `_long_session_due` 每轮都判 due，每 10 秒劝一次「歇会儿吧」", False),

    ("memory_cooldown_off", P,
     "            if getattr(self, '_last_memory_initiative', 0.0) and now - self._last_memory_initiative < 600.0:\n"
     "                return False",
     "            if False:\n                return False",
     "14a 记忆主动 10 分钟冷却内不开口",
     "记忆主动冷却失效 —— 同一类偏好被反复聊（代码注释：「避免反复聊同一类」）", False),

    ("memory_random_gate_off", P,
     "            if random.random() > 0.35:\n                return False",
     "            if False:\n                return False",
     "14b 记忆主动 35% 随机门（未命中不开口）",
     "35% 随机门拆掉 —— 每次空闲都变成「她记得你」，从自然变成刻意", False),

    # 【2026-11-11】原刀是「删掉 `if not facts: return False`」—— 实测**等价变异**：
    # 删掉之后 `int(getattr(...) % len(facts))` 先抛 ZeroDivisionError，被最外层
    # `except Exception` 吞掉、照样 `return False`，行为零差异。两道闸互为冗余。
    # 按第 41 轮的规矩：等价变异不"修"，换一把真刀 —— 「没有事实时编一条」，
    # 这才是真实故障模式（模型会顺着这条假事实往下聊，用户以为她记错了）。
    ("memory_fabricates_fact", P,
     "            facts = store.list_user_facts(limit=15)\n"
     "            if not facts:\n"
     "                return False",
     "            facts = store.list_user_facts(limit=15) or [\"用户好像挺喜欢猫的\"]",
     "14c 没有用户事实时不开口",
     "没有事实时编一条 —— 她会顺着这条假事实往下聊，用户以为她记错了（比不开口更糟）", False),

    ("memory_fact_pointer_stuck", P,
     "            _idx = int(getattr(self, '_mem_init_idx', 0) % len(facts))\n"
     "            self._mem_init_idx = _idx + 1",
     "            _idx = 0",
     "14d 事实指针轮转（两次问不同的事实）",
     "指针不转 —— 永远聊第一条事实，用户会觉得她只记住了一件事", False),
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
    m = re.search(r"结果: \d+ PASS / \d+ FAIL / \d+ SKIP", _out(r))
    return m.group(0) if m else "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(
        re.search(r"结果: \d+ PASS / \d+ FAIL / \d+ SKIP", _out(r)))


def _hit(r, name):
    return any(ln.strip().startswith("FAIL") and name in ln
               for ln in (r.stdout or "").splitlines())


def main():
    base = _run()
    if not (base.returncode == 0
            and re.search(r"结果: (\d+) PASS / 0 FAIL / (\d+) SKIP", _out(base))):
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
            print("  %-28s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
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
        print("  %-28s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s"
                  % (expect, [ln[:70] for ln in (r.stdout or "").splitlines()
                              if ln.strip().startswith("FAIL")][:3]))
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
