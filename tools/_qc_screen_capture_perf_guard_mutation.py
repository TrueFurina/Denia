# -*- coding: utf-8 -*-
"""「抓屏性能闸」的变异验证：五刀，刀刀必须被抓住。

为什么补这把刀：`tools/test_screen_capture_perf_guard.py` 守的是一条**曾经真炸过**的
性能底线 —— 2026-08-25 修掉的「抽帧 / 核显本卡炸」：游戏或锁快照期间画面长时间静止，
`_next_interval` 的 static 加速会把采样间隔打到 ~1s，每帧全屏抓屏 + resize + pHash +
JPEG 编码，直接把核显本拖垮。这道门的判据是 A/B/C/D/E/F 六组共 27 条，**从来没有刀**，
也就是说「27 条到底有没有牙」这件事一次都没验过。第三十轮刚改过它（D8 缺夹具退回
出厂模板），动了判据的入口，就得证明牙还在。

五刀各打一条判据：
  G1 low_rate 档不再守低频底线（落回 static 加速）  → A2 红
  G2 静态加速乘数被去掉                            → A3 红
  G3 主路径不再传 low_rate（云端接管时偷偷提速）    → B1 红
  G4 让位分支不再传 low_rate=True                  → B3 红
  G5 main.py 两处 ScreenCapture 构造去掉 low_rate_check → D11 红

判据三件套（沿用本仓 2026-11-08 的修法）：
  · `_judged()` —— 非零退出**且**输出里有 `  FAIL  ` 行，崩溃（ERROR 分支）不算抓住；
  · `_baseline_green()` —— 起点必须先绿，恒红的判定能让每把刀都变红；
  · 每刀都备份字节、finally 还原并核对 sha256，刀自己不许把源码弄坏。

【注意·这把刀的锚点有一个不是「恰好 1 次」】G5 打的是 `main.py` 里两处**逐字相同**的
`low_rate_check=_game_check_or_locked,`（1346 / 3214）。D11 的判据是「**每一处**
ScreenCapture 构造都要带 low_rate_check」，所以两处必须一起改 —— 这正是判据的语义。
因此 MUTS 多带一个「期望命中次数」字段，G5 写 2，其余写 1；命中数不符一律报
「锚点已腐烂」，不硬改。

跑法：.venv/Scripts/python.exe -u tools/_qc_screen_capture_perf_guard_mutation.py
退出码：0 = 五刀全红；1 = 有刀没被抓住；77 = 本机跑不了（缺依赖等）。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = sys.executable
GATE = os.path.join("tools", "test_screen_capture_perf_guard.py")
CAP = os.path.join("src", "perception", "screen_capture.py")
MAIN = "main.py"

# (标签, 目标文件, 原文锚点, 替换文本, 期望出现的失败关键词, 期望命中次数)
MUTS = [
    ("G1 low_rate 档不再守低频底线",
     CAP,
     "            return max(0.5, self._game_interval_s * random.uniform(0.8, 1.2))",
     "            pass  # 变异刀：low_rate 直接落到 static 加速那条路",
     "FAIL  A2",
     1),
    ("G2 静态加速乘数被去掉",
     CAP,
     "                interval *= self._static_scale",
     "                interval *= 1.0  # 变异刀：关掉静态加速",
     "FAIL  A3",
     1),
    ("G3 主路径不再传 low_rate",
     CAP,
     "            self._last_interval = self._next_interval(low_rate=low_rate)",
     "            self._last_interval = self._next_interval()  # 变异刀：云端接管时偷偷提速",
     "FAIL  B1",
     1),
    ("G4 让位分支不再传 low_rate=True",
     CAP,
     "                    self._last_interval = self._next_interval(low_rate=True)",
     "                    self._last_interval = self._next_interval()  # 变异刀：让位时提速",
     "FAIL  B3",
     1),
    ("G5 两处构造去掉 low_rate_check",
     MAIN,
     "                            low_rate_check=_game_check_or_locked,",
     "                            # 变异刀：两处接线都不带 low_rate_check",
     "FAIL  D11",
     2),
]

# 给 `check_knife_noop.py` 认形状用：第 2 项是 old、第 3 项是 new（下标从 0 起）。
NOOP_SHAPE = ("idx", 2, 3)


def _read(p: str) -> bytes:
    with io.open(p, "rb") as f:
        return f.read()


def _write(p: str, data: bytes) -> None:
    with io.open(p, "wb") as f:
        f.write(data)


def _run() -> tuple:
    p = subprocess.run([PY, "-u", GATE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _judged(rc: int, out: str) -> bool:
    """「判出了问题」而不是「崩了」：非零退出 **且** 有 `  FAIL  ` 行。

    这道门的 `ok()` 打的是 `  PASS  ` / `  FAIL  `；函数整体抛异常时走的是
    `  ERROR fn: ...` 分支（只把函数名塞进 FAIL 列表，不打 `  FAIL  ` 行）。
    """
    return rc != 0 and "  FAIL  " in out


def _baseline_green() -> tuple:
    rc, out = _run()
    if rc == 77:
        return True, "77"
    return (rc == 0 and "  FAIL  " not in out), ""


def main() -> int:
    ok_green, why77 = _baseline_green()
    if why77:
        print("  [SKIP] 起点 exit 77：本机缺夹具（既无 config.yaml 也无 "
              "config.release.yaml），D8 未验证（不是通过）")
        return 77
    if not ok_green:
        print("  FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，"
              "这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")

    bad = 0
    for label, target, old, new, keyword, expect in MUTS:
        orig = _read(target)
        sha0 = hashlib.sha256(orig).hexdigest()
        text = orig.decode("utf-8")
        n = text.count(old)
        if n != expect:
            print("  FAIL %s：锚点命中 %d 次（应恰好 %d 次），锚点已腐烂"
                  % (label, n, expect))
            bad += 1
            continue
        try:
            _write(target, text.replace(old, new).encode("utf-8"))
            rc, out = _run()
            caught = _judged(rc, out)
            hit = keyword in out
            if caught and hit:
                print("  PASS %s → 红了，且点名「%s」" % (label, keyword))
            elif caught:
                print("  FAIL %s → 红了，但没点名「%s」（可能是别的原因崩的）"
                      % (label, keyword))
                for ln in [x for x in out.splitlines() if "  FAIL  " in x][:3]:
                    print("        | " + ln.strip()[:150])
                bad += 1
            else:
                print("  FAIL %s → 没抓住（闸门失效）" % label)
                bad += 1
        finally:
            _write(target, orig)
            if hashlib.sha256(_read(target)).hexdigest() != sha0:
                print("  FAIL %s：还原后字节不一致！" % label)
                bad += 1

    rc, out = _run()
    if rc == 0 and "  FAIL  " not in out:
        print("  PASS 还原后仍然绿")
    else:
        print("  FAIL 还原后不是绿的 —— 刀自己把源码弄坏了")
        bad += 1

    print("\n%s" % ("五刀全红" if bad == 0 else "有 %d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
