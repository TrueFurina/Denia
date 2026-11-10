# -*- coding: utf-8 -*-
"""关系集成门（tools/test_relationship_integration.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条用例**上。

判据口径（第 38/39 轮的教训）：只看 unittest 自己的汇总行
`OK` / `FAILED (failures=N, errors=M)`，不看日志里的 Traceback ——
`logger.exception` 打出来的栈是**被测代码在正常报错**，不是门崩了。
「崩了」的判定改用「非零退出且没有 FAILED 汇总行」。

运行：  python tools/_qc_relationship_mutation.py
"""
import hashlib
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_relationship_integration.py")]

REPORTER = "src/companion/relationship_reporter.py"
RELMOD = "src/companion/relationship.py"

MUTS = [
    # ---- 本轮补的六条判据 ----
    ("dedup_off", REPORTER,
     "        if text == self._last_ai_text and (_now - self._last_ai_ts) < 5.0:\n            return",
     "        if False:\n            return",
     "test_04_dedup_same_ai_text",
     "去重整段拆掉 —— 同一句回复被投两次会白吃两轮用户话", False),

    ("enabled_off", REPORTER,
     "    async def _on_ai_decision(self, e: Event) -> None:\n        if not self.enabled:\n            return",
     "    async def _on_ai_decision(self, e: Event) -> None:\n        if False:\n            return",
     "test_08_disabled_reporter_does_nothing",
     "关系开关拆掉 —— 用户在设置里关掉关系，照样记账", False),

    ("judge_no_fallback", REPORTER,
     '        except Exception:  # noqa: BLE001\n'
     '            logger.exception("[关系] 裁判异常（本轮按规则兜底）")\n'
     "            signals = _heuristic_signals(ut)",
     "        except ZeroDivisionError:  # MUT: 只剩这一种才兜底\n"
     "            signals = _heuristic_signals(ut)",
     "test_09_judge_raises_still_counts_turn",
     "裁判异常不再兜底 —— 这一轮悄悄消失，日志里只剩一句 Task exception", False),

    ("bubble_filter", REPORTER,
     '        if not text or d.get("blocked") or d.get("failed"):\n            return',
     "        if not text:\n            return",
     "test_14_failed_bubble_not_counted",
     "失败气泡/被拦回复不过滤 —— 记成一整轮，把用户那句真话白吃掉", False),

    ("source_fallback", REPORTER,
     '        src = str(d.get("source") or e.source or "chat")\n'
     "        # 非聊天来源（双工自言自语 source='proactive'、通话等）不进聊天历史、不动关系状态",
     '        src = str(d.get("source") or "")\n'
     "        # 非聊天来源（双工自言自语 source='proactive'、通话等）不进聊天历史、不动关系状态",
     "test_15_missing_source_falls_back_to_chat",
     "source 缺省不再回退 chat —— 所有不填 source 的投递方一句话都不进关系", False),

    ("duplex_branch", REPORTER,
     '        if src == "duplex":',
     "        if False:",
     "test_16_duplex_user_text_judged_directly",
     "双工用户话不再当场判 —— 打电话说的全进队列等一条永远不来的回复", False),

    # ---- 老判据：确认它们真的有牙齿 ----
    ("judge_sync", REPORTER,
     "        self._spawn_judge(cid, ut)",
     "        await self._judge_and_apply(cid, ut)",
     "test_20_judge_does_not_block_bus",
     "判定改成同步 await —— 裁判 1.5s 期间整条事件总线被堵死", False),

    ("streamed_filter", REPORTER,
     '        if not text or d.get("blocked") or d.get("failed"):\n            return',
     '        if not text or d.get("blocked") or d.get("failed") or d.get("streamed"):\n            return',
     "test_05_streamed_final_is_judged",
     "把 streamed=True 当分片过滤 —— 打字聊天整条链路从不判定（2026-10-10 真实事故）", False),

    ("queue_lifo", REPORTER,
     "        ut = self._pending_user_queue.popleft()",
     "        ut = self._pending_user_queue.pop()",
     "test_02_pending_queue_no_overwrite",
     "队列改成 LIFO —— 连发两句时回复配给了最后一句，第一句错位", False),

    ("proactive_clears_queue", REPORTER,
     "            try:\n                self._mgr.mark_initiative(cid)",
     "            try:\n                self._pending_user_queue.clear()\n                self._mgr.mark_initiative(cid)",
     "test_17_chat_proactive_keeps_user_queue",
     "主动发言顺手清空用户队列 —— 用户那句真话永远配不上她的正式回复", False),

    ("context_dropped", REPORTER,
     "                signals = await self._judge.judge(ut, persona=self._persona_for(cid), context=ctx)",
     "                signals = await self._judge.judge(ut, persona=self._persona_for(cid), context=None)",
     "test_07_judge_receives_conversation_context",
     "裁判拿不到上下文 —— 承上省略/反讽全判错（2026-10-10 修过的那件事）", False),

    ("singleton_new", RELMOD,
     "    global _MGR\n    if _MGR is None:\n        _MGR = RelationshipManager(base_dir)\n    return _MGR",
     "    global _MGR\n    _MGR = RelationshipManager(base_dir)\n    return _MGR",
     "test_11_singleton_shared_between_calls",
     "每次调用都新建管理器 —— 两个实例各写各的，谁后写谁赢", False),

    ("per_char_shared", RELMOD,
     '        return self.base / f"{_safe_id(char_id)}.json"',
     '        return self.base / "shared.json"',
     "test_12_state_file_isolated_per_char",
     "所有角色共用一份状态文件 —— 换个角色好感度串味", False),
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
    for ln in reversed((r.stderr or "").splitlines()):
        if ln.startswith("OK") or ln.startswith("FAILED"):
            return ln.strip()
    return "(无汇总行)"


def _judged(r):
    """非零退出 + unittest 自己报了失败（而不是半路崩掉）。"""
    return r.returncode != 0 and "FAILED (" in _out(r)


def _hit(r, name):
    return any(ln.startswith("FAIL: ") or ln.startswith("ERROR: ")
               for ln in (r.stderr or "").splitlines() if name in ln)


def main():
    base = _run()
    if not (base.returncode == 0 and "\nOK" in _out(base)):
        # 带上 FAIL 标记：空变异体检（check_knife_vacuity）要把"套件自己报了失败"
        # 和"套件崩了/什么都没说"区分开 —— 只 exit 2 而没有失败标记会被判 WEAK。
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
            print("  %-22s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
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
        print("  %-22s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s"
                  % (expect, [ln[:70] for ln in (r.stderr or "").splitlines()
                              if ln.startswith("FAIL: ") or ln.startswith("ERROR: ")][:3]))
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
