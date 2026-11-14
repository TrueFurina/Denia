# -*- coding: utf-8 -*-
"""看屏热生效门（tools/test_desktop_watch_hotapply.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~40 轮）：本门不是 unittest，自己打 `PASS n / FAIL m` 汇总行；
所以「红了」= 非零退出 + 有汇总行（不是半路崩掉），「命中」= 该判据名出现在
`  FAIL  ` 行里。日志里的 Traceback 一律不算崩溃 —— 本门基线输出里本来就有一条
（capture_loop 的异常兜底在正常报错）。

运行：  python tools/_qc_hotapply_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_desktop_watch_hotapply.py")]

SC = "src/perception/screen_capture.py"
VA = "src/perception/vision_analyzer.py"

# capture_loop 里「关闸就短睡自旋」的那一段（P1/P12/P13 共用锚点）
_LOOP_OFF = (
    "                if not self.enabled:\n"
    "                    self._last_hash = None\n"
    "                    self._last_interval = 2.0\n"
    "                    await asyncio.sleep(2.0)\n"
    "                    continue"
)

# analyze() 里用户总闸那段硬闸（P4/P11 共用锚点）
_HARD_GATE = (
    "            if not bool(self._cfg.runtime_status.vision_enabled):\n"
    "                self._disabled = True\n"
    '                self._disabled_at = 0.0      # 0 表示"非失败熔断"，自动恢复不会动它\n'
    "                return None"
)

MUTS = [
    # ---- 本轮补的判据 ----
    ("loop_recheck_removed", SC,
     _LOOP_OFF,
     "                if False:\n"
     "                    await asyncio.sleep(2.0)\n"
     "                    continue",
     "B3",
     "关闸不再逐轮重读 —— 用户在设置页关掉，截图/分析管线照跑（2026-10-09 用户实锤）", False),

    ("loop_exits_on_off", SC,
     _LOOP_OFF,
     "                if not self.enabled:\n"
     "                    self._last_hash = None\n"
     "                    self._last_interval = 2.0\n"
     "                    return",
     "H2",
     "关闸直接退出循环 —— 重新打开必须重启进程（注释承诺的「无需重启」没了）", False),

    ("loop_keeps_hash", SC,
     _LOOP_OFF,
     "                if not self.enabled:\n"
     "                    self._last_interval = 2.0\n"
     "                    await asyncio.sleep(2.0)\n"
     "                    continue",
     "H3",
     "关闸不清 _last_hash —— 重开后画面没变就永远不分析，看起来像「开了没用」", False),

    ("capture_now_gate_removed", SC,
     "        if not self.enabled:\n"
     "            return\n"
     "        try:\n"
     "            self._event_trigger_count += 1",
     "        if False:\n"
     "            return\n"
     "        try:\n"
     "            self._event_trigger_count += 1",
     "C1",
     "事件驱动抓屏不看开关 —— 关了看屏，切窗到游戏仍立刻抓一帧送模型", False),

    ("capture_now_always_return", SC,
     "        if not self.enabled:\n"
     "            return\n"
     "        try:\n"
     "            self._event_trigger_count += 1",
     "        if True:\n"
     "            return\n"
     "        try:\n"
     "            self._event_trigger_count += 1",
     "C3",
     "capture_now 永远早退 —— 前台切游戏再也不会补抓一帧（静默失去事件驱动）", False),

    ("enabled_snapshot", SC,
     "        try:\n"
     "            live = bool(self._cfg.runtime_status.vision_enabled)\n"
     "        except Exception:  # noqa: BLE001\n"
     "            live = self._enabled\n"
     "        self._enabled = live          # 顺带刷新调试台快照\n"
     "        return live",
     "        return self._enabled",
     "A2",
     "enabled 退回构造期快照 —— 设置页改了不生效，必须重启（本轮要修的那个 bug 本身）", False),

    ("hard_gate_removed", VA,
     _HARD_GATE,
     "            if False:\n"
     "                return None",
     "D2",
     "总闸硬闸拆掉 —— 用户关了看屏，视觉分析照发云端（既不尊重开关，也烧额度）", False),

    ("hard_gate_no_zero_at", VA,
     _HARD_GATE,
     "            if not bool(self._cfg.runtime_status.vision_enabled):\n"
     "                self._disabled = True\n"
     "                return None",
     "E2",
     "关闸不再把 _disabled_at 归零 —— 先熔断后关闸，90s 后分析线自己复活（原始 bug 回归）", False),

    ("auto_recover_removed", VA,
     "            if self._disabled_at > 0 and time.time() - self._disabled_at >= AUTO_RECOVER_S:\n"
     "                self._disabled = False\n"
     "                self._fail_count = 0\n"
     "                self._disabled_at = 0.0",
     "            if False:\n"
     "                pass",
     "G4",
     "自动恢复整段废掉 —— 云端抖动凑满阈值后感知线永久哑火，必须重启（2026-08-24 修过）", False),

    ("reopen_branch_removed", VA,
     "            if self._disabled and self._disabled_at == 0.0:\n"
     "                self._disabled = False\n"
     "                self._fail_count = 0",
     "            if False:\n"
     "                self._disabled = False\n"
     "                self._fail_count = 0",
     "F2",
     "重开闸不解禁用 —— _disabled_at==0 进不了自动恢复分支，用户开了也永久哑火", False),

    ("breaker_never_trips", VA,
     '            except Exception as e:  # noqa: BLE001\n'
     "                self._fail_count += 1\n"
     '                logger.warning("视觉分析失败 (%d/%d): %s", self._fail_count, self._fail_limit, e)\n'
     "                if self._fail_count >= self._fail_limit:",
     '            except Exception as e:  # noqa: BLE001\n'
     "                self._fail_count += 1\n"
     '                logger.warning("视觉分析失败 (%d/%d): %s", self._fail_count, self._fail_limit, e)\n'
     "                if False:",
     "G1",
     "失败计数不再触发熔断 —— 云端挂着时每帧都白等一次超时，把采样循环拖死", False),

    ("no_disable_event", VA,
     "                    await self._bus.publish(Event(EventType.ERROR, {\n"
     '                        "code": "VISION_DISABLED",\n'
     '                        "message": f"视觉连续失败 {self._fail_count} 次，已全局禁用"\n'
     '                                   f"（{AUTO_RECOVER_S}s 后自动恢复重试）",\n'
     '                    }, "vision"))',
     "                    pass",
     "G2",
     "熔断时不广播 VISION_DISABLED —— 前端和日志都不知道看屏已经哑了", False),
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
        if re.match(r"^PASS \d+ / FAIL \d+", ln):
            return ln.strip()
    return "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(re.search(r"^PASS \d+ / FAIL \d+", _out(r), re.M))


def _hit(r, name):
    return any(ln.strip().startswith("FAIL") and name in ln
               for ln in (r.stdout or "").splitlines())


def main():
    base = _run()
    if not (base.returncode == 0 and "ALL GREEN" in _out(base)):
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
            print("  %-26s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
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
        print("  %-26s %-8s %s" % (key, tag, _summary(r)))
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
