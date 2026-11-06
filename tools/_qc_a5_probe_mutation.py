# -*- coding: utf-8 -*-
"""A5 环境探针的变异验证：每把刀都必须被 probe_a5_env.py 抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_a5_probe_mutation.py

六把刀分别对应六种「探针自己坏掉」的形态：跳过显示器枚举、跳过日志扫描、
不落盘报告、抹掉未验清单、把口径写成「已验收」、谎报修复后长跑已验。
探针只测本机，绝不能因为「写了个报告」就算 A5 通过——这些刀守的就是这条边界。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/probe_a5_env.py"
TEST = [".venv/Scripts/python.exe", "-u", P, "--report", "logs/_qc_a5_mutation_report.json"]


def E(s: str) -> bytes:
    return s.encode("utf-8")


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
    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        io.open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run(TEST, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            caught = r.returncode != 0
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.startswith("FAIL")] or lines or ["<无输出>"]
            print(name)
            print("   -> " + ("已抓住：" + why[-1][:96] if caught else "没抓住（探针失效）"))
            if not caught:
                bad += 1
        finally:
            io.open(P, "wb").write(orig)

    ok = hashlib.sha256(io.open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("%d 刀全红" % len(MUTS) if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
