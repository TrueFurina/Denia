# -*- coding: utf-8 -*-
"""「长任务护栏·端到端」闸门的变异验证：每把刀都必须让它变红。

与 _qc_loop_guard_mutation.py 的分工：
  - 那边验判据层（直接调 observe）+ 静态接线字符串；
  - 这边验**完整 run() 循环**的接线：把 observe / hint 注入 / 阈值 / 清零 /
    换参数重置逐个摘掉，看 e2e 闸门是否真的抓得住。
    抓不住 ⇒ 端到端测试自己是假绿。

跑法：.venv/Scripts/python.exe -u tools/_qc_loop_guard_e2e_mutation.py
"""
from __future__ import annotations

import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = ".venv/Scripts/python.exe"
TEST = "tools/check_loop_guard_e2e.py"


def E(s: str) -> bytes:
    return s.encode("utf-8")


# (标签, 目标文件, 原文, 改成)
MUTS = [
    ("A 接线：dispatch_tool 后不再喂结果",
     "src/llm/agent_brain.py",
     E("                                _h = self._repeat_guard.observe(fname, fargs, result)\n"),
     E("                                _h = None\n")),
    ("B 接线：hint 不再注入 messages",
     "src/llm/agent_brain.py",
     E('                        messages.append({"role": "user", "content": self._pending_hint})\n'),
     E("                        pass  # mutated\n")),
    ("C 阈值 3 → 1（第 2 轮就注入）",
     "src/llm/loop_guard.py",
     E("DEFAULT_FAILURE_LIMIT = 3\n"),
     E("DEFAULT_FAILURE_LIMIT = 1\n")),
    # 【等价变异的教训】初版只去掉 `self._count = 0`，这把刀**不会**让闸门变红：
    # 成功分支里还有 `self._key = None`，下一次失败会因 key 变化重新计数，
    # 可观察行为没变 —— 那是等价变异，不是闸门漏抓。
    # 必须把 key 与 count 一起保留，才是真正的「旧失败串到新方案」。
    ("D 成功后完全不清状态（旧失败串到新方案）",
     "src/llm/loop_guard.py",
     E("            self._key = None\n            self._count = 0\n            self._emitted = False\n"),
     E("            self._emitted = False\n")),
    ("E 换参数不重新计数（误伤新方案）",
     "src/llm/loop_guard.py",
     E("        if self._key != key:\n            self._key = key\n"),
     E("        if False:\n            self._key = key\n")),
]


def run_test():
    r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    rc, out = run_test()
    print("基线:", "全绿 ✓" if rc == 0 else "已红 ✗（无法做变异验证）")
    if rc != 0:
        print(out[-600:])
        return 1
    print()

    bad = 0
    for label, path, old, new in MUTS:
        raw = open(path, "rb").read()
        if old not in raw:
            print("  ✗ 锚点找不到:", label)
            bad += 1
            continue
        if raw.count(old) != 1:
            print("  ✗ 锚点不唯一(%d 处):" % raw.count(old), label)
            bad += 1
            continue
        try:
            with open(path, "wb") as f:
                f.write(raw.replace(old, new, 1))
            rc2, out2 = run_test()
            caught = rc2 != 0
            print(("  ✓ 变红 " if caught else "  ✗ 没红 ") + label)
            if not caught:
                bad += 1
                print("     ", out2[-260:].replace("\n", " "))
        finally:
            with open(path, "wb") as f:
                f.write(raw)

    rc3, _ = run_test()
    print("\n还原后基线:", "全绿 ✓" if rc3 == 0 else "仍红 ✗")
    if rc3 != 0:
        bad += 1
    print("结果: %d 把刀，%d 把没被抓住" % (len(MUTS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
