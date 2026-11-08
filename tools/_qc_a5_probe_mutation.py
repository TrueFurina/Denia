# -*- coding: utf-8 -*-
"""A5 环境探针的变异验证：每把刀都必须被 probe_a5_env.py 抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_a5_probe_mutation.py

六把刀分别对应六种「探针自己坏掉」的形态：跳过显示器枚举、跳过日志扫描、
不落盘报告、抹掉未验清单、把口径写成「已验收」、谎报修复后长跑已验。
探针只测本机，绝不能因为「写了个报告」就算 A5 通过——这些刀守的就是这条边界。

【2026-11-08 重写·两个毛病，都是同一类】
① **刀空转**：原来只判 `r.returncode != 0`，于是判定**崩了**也算"抓住"。
   第二十五轮的空变异体检把这一把列进 `KNOWN_VACUOUS`，是最后一条。
② **起点恒红**：探针原来直接扫本仓 `logs/`，而 `logs/` 在 `.gitignore` 里 ——
   **新克隆的树上必然一份 launch_run*.log 都没有** → 两条硬 FAIL → 探针恒红 →
   六把刀"全红"是白送的。所以空转变检那一轮它怎么修都还是 VACUOUS。
   修法是给探针加 `--root`，本刀在 tempfile 里造一份合成日志当夹具：
   **判定不依赖"这台机器碰巧有没有日志"**，和 `check_knife_noop.py` 造桩仓同一个道理。

六刀 + 三把限位
----------------
  A 跳过显示器枚举（假装有屏）        → FAIL 至少枚举到 1 台显示器
  B 跳过心跳日志扫描（基线凭空消失）  → FAIL 扫描份数与发现份数一致
  C 报告不落盘（没有可复现产物）      → FAIL 报告已落盘且可解析
  D 抹掉未验清单（把没验的写成验过）  → FAIL requires_human >= 3
  E 把测量口径写成已验收              → FAIL 报告明确写了本机测量口径
  F 谎报修复后长跑已验                → FAIL 不谎报「修复后长跑已验」
  G 限位：夹具里没有日志              → **77 + WARN**（未验证 ≠ 失败）
  H 限位：日志在、但没有 [UI心跳] 行  → **1 + FAIL**（有样本却量不到，是真失败）
  I 限位：夹具正常                    → 0（起点必须绿，否则上面全是白送）

G 和 H 是这次新加的：它们钉的是「缺样本 / 有样本但量不到 / 正常」三态不许塌成两态。
只加 G 会让人把 H 也放宽成 77，只加 H 会让 G 退回硬 FAIL —— 两条一起才有意义。
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = "tools/probe_a5_env.py"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

# 合成日志：两行能被 CLASSIFY 命中的 + 一行干扰项（证明探针真的按标记筛）
_LOG = (
    "2026-11-08 12:00:01 [UI心跳] 心跳变慢，判为后台定时器节流\n"
    "2026-11-08 12:00:31 [UI心跳] 心跳已恢复\n"
    "2026-11-08 12:00:32 与心跳无关的一行\n"
)


def E(s: str) -> bytes:
    return s.encode("utf-8")


def _fixture(log_text):
    """建一个只含 logs/ 的假仓根。`None` = 连日志文件都不建。"""
    d = tempfile.mkdtemp(prefix="qc_a5_")
    os.makedirs(os.path.join(d, "logs"), exist_ok=True)
    if log_text is not None:
        io.open(os.path.join(d, "logs", "launch_run_fixture.log"),
                "w", encoding="utf-8", newline="\n").write(log_text)
    return d


def _run(root: str):
    return subprocess.run([PY, "-u", P, "--root", root],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, cwd=ROOT)


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」或「什么都没说」。"""
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _has_fail_line(out: str) -> bool:
    """输出里有没有**一条**判失败的行。

    【2026-11-08 踩的坑】第一版这里写的是 `"FAIL" in out`，于是**汇总行**
    `结果: 9 PASS / 0 FAIL / 0 未验证` 里的那个 "0 FAIL" 也命中了 —— 起点明明是绿的，
    却被判成"exit 0 但输出里有 FAIL"，刀一进来就自爆。
    和第四轮那个 `count(old) == 1` 是同一类：**子串命中不等于语义命中**。
    """
    return any(l.strip().startswith("FAIL") for l in out.splitlines())


def _baseline_green() -> tuple[bool, str]:
    d = _fixture(_LOG)
    try:
        r = _run(d)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            return False, "exit %d：%s" % (
                r.returncode, (out.strip().splitlines() or ["<无输出>"])[-1][:120])
        if _has_fail_line(out):
            return False, "exit 0 但有 FAIL 行"
        return True, ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


MUTS = [
    ("A 跳过显示器枚举（假装有屏）",
     E("    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)\n"),
     E("    pass  # 跳过显示器枚举\n")),

    ("B 跳过心跳日志扫描（基线凭空消失）",
     E("    for path in logs:\n"),
     E("    for path in []:\n")),

    ("C 报告不落盘（没有可复现产物）",
     E('    with io.open(path, "w", encoding="utf-8") as f:\n'
       '        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\\n")\n'),
     E("    pass  # 不落盘\n")),

    ("D 抹掉未验清单（把没验的写成验过）",
     E("HUMAN_ONLY = [\n"),
     E("HUMAN_ONLY = []\n_UNUSED = [\n")),

    ("E 把测量口径写成已验收",
     E('        "scope": "本机测量，不等于 A5 验收通过；人眼项见 requires_human",'),
     E('        "scope": "A5 验收已通过",')),

    ("F 谎报修复后长跑已验",
     E('        "post_fix_long_run_verified": heartbeat["post_fix_run_available"],'),
     E('        "post_fix_long_run_verified": True,')),
]


def main() -> int:
    orig = io.open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0

    good, why = _baseline_green()
    if not good:
        print("FAIL 起点就不是绿的 —— %s" % why)
        print("     恒红的判定能让每把刀都变红，这种「全红」不证明任何事。")
        return 1
    print("  PASS I 限位：夹具正常 → exit 0（起点绿）")

    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        io.open(P, "wb").write(orig.replace(old, new, 1))
        d = _fixture(_LOG)
        try:
            r = _run(d)
            caught = _judged(r)
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            hit = [l for l in lines if l.startswith("FAIL")] or lines or ["<无输出>"]
            print(name)
            print("   -> " + ("已抓住：" + hit[-1][:96] if caught else "没抓住（探针失效）"))
            if not caught:
                bad += 1
        finally:
            io.open(P, "wb").write(orig)
            shutil.rmtree(d, ignore_errors=True)

    # ---- G 限位：没有样本 → 77 + WARN，不是 FAIL，也不是 0 ----
    d = _fixture(None)
    try:
        r = _run(d)
        out = (r.stdout or "") + (r.stderr or "")
        ok_g = r.returncode == 77 and "WARN" in out and not _has_fail_line(out)
        print("G 限位：夹具里没有日志")
        print("   -> %s" % ("缺样本判成 77 + WARN（未验证 ≠ 失败）" if ok_g
                           else "没抓住：exit %d，期望 77 且只出 WARN" % r.returncode))
        if not ok_g:
            bad += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- H 限位：有日志但量不到 [UI心跳] → 真失败，不许被 G 的 77 吸收 ----
    d = _fixture("2026-11-08 12:00:01 这一行没有那个标记\n")
    try:
        r = _run(d)
        out = (r.stdout or "") + (r.stderr or "")
        ok_h = r.returncode == 1 and "至少 1 条 [UI心跳] 被分类" in out and "FAIL" in out
        print("H 限位：日志在、但没有 [UI心跳] 行")
        print("   -> %s" % ("有样本却量不到 → FAIL（没被 77 吸收）" if ok_h
                           else "没抓住：exit %d，期望 1 且点名「至少 1 条 [UI心跳] 被分类」"
                                % r.returncode))
        if not ok_h:
            bad += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    ok = hashlib.sha256(io.open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("%d 刀 + 3 限位全过" % len(MUTS) if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
