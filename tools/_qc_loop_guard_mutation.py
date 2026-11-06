# -*- coding: utf-8 -*-
"""「长任务护栏」闸门的变异验证：每把刀都必须被抓住（测试变红）。

跑法：.venv/Scripts/python.exe -u tools/_qc_loop_guard_mutation.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = ".venv/Scripts/python.exe"
TEST = "tools/check_loop_guard.py"


def E(s: str) -> bytes:
    return s.encode("utf-8")


# (标签, 目标文件, 原文, 改成)
MUTS = [
    ("A 重复失败阈值改成「失败一次就注入」",
     "src/llm/loop_guard.py",
     E("        if self._count >= self.limit and not self._emitted:\n"),
     E("        if self._count >= 1 and not self._emitted:\n")),

    ("B 成功后不清零（旧失败串到下一方案）",
     "src/llm/loop_guard.py",
     E("            self._key = None\n            self._count = 0\n            self._emitted = False\n"),
     E("            pass\n")),

    ("C is_failure 恒真（成功也当失败，误伤正常工具）",
     "src/llm/loop_guard.py",
     E("    low = s.lower()\n    return any(s.startswith(p) or low.startswith(p.lower())\n               for p in _FAILURE_PREFIXES)\n"),
     E("    return True\n")),

    ("D trigger_ratio 不夹到 0.9（书里写死的上限失守）",
     "src/llm/loop_guard.py",
     E("        self.trigger_ratio = min(0.9, max(0.1, float(trigger_ratio)))\n"),
     E("        self.trigger_ratio = float(trigger_ratio)\n")),

    ("E 接线：dispatch_tool 后不再喂结果（写了没进门）",
     "src/llm/agent_brain.py",
     E("                                _h = self._repeat_guard.observe(fname, fargs, result)\n"),
     E("                                _h = None\n")),

    ("F 接线：hint 不再注入 messages（判据空转）",
     "src/llm/agent_brain.py",
     E("                        messages.append({\"role\": \"user\", \"content\": self._pending_hint})\n"),
     E("                        self._pending_hint = \"\"\n")),
]


def run_test() -> tuple[int, str]:
    r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    rc, out = run_test()
    if rc != 0:
        print("✗ 基线测试就没过，先修测试再做变异")
        print(out[-800:])
        return 1
    print("基线: 全绿 ✓\n")

    bad = 0
    for label, path, old, new in MUTS:
        raw = open(path, "rb").read()
        if old not in raw:
            print("  ✗ 锚点找不到:", label)
            bad += 1
            continue
        bak = raw
        try:
            with open(path, "wb") as f:
                f.write(raw.replace(old, new, 1))
            rc2, out2 = run_test()
            caught = rc2 != 0
            print(("  ✓ 变红 " if caught else "  ✗ 没红 ") + label)
            if not caught:
                bad += 1
                print("     ", out2[-300:].replace("\n", " "))
        finally:
            with open(path, "wb") as f:
                f.write(bak)

    rc3, _ = run_test()
    print("\n还原后基线:", "全绿 ✓" if rc3 == 0 else "仍红 ✗")
    if rc3 != 0:
        bad += 1
    print("结果: %d 把刀，%d 把没被抓住" % (len(MUTS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
