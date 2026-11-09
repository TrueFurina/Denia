"""变异刀：`主动搭话冒烟` 闸门（tools/test_proactive_smoke.py）的两把刀。

    python tools/_qc_proactive_smoke_mutation.py

背景（2026-11-10）：原文件里
  · `C-非双工(无llm)` 断言 `(not m2.reached_decision) and (not m2.reached_emit)`，
    而这两个属性全文件只在 __init__ 里置 False、**从不置 True** —— 恒真；
  · `B6` 的 else 分支 `ok(..., True, "skip")` —— 前提消失时按通过计。
两条都已改成可观测判据，本刀负责钉住它们。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "test_proactive_smoke.py")
PROACTIVE = os.path.join("src", "companion", "proactive.py")

IN_CALL_GUARD = "            if self._in_call():\n                return\n"
LLM_GUARD = "            if self._llm is None or not self._llm_generate:\n                return\n"


def drop_in_call_guard(text: str) -> str:
    return text.replace(IN_CALL_GUARD, "            if False:  # QC：让位守卫被拆\n                return\n")


def drop_llm_guard(text: str) -> str:
    return text.replace(LLM_GUARD, "            if False:  # QC：无 llm 不再提前返回\n                return\n")


MUTS = [
    ("A 通话中不再让位（_in_call 守卫被拆）", drop_in_call_guard, "通话中：让位不打扰"),
    ("B 无 llm 不再提前返回（会走到闸门）", drop_llm_guard, "安全返回且不走到闸门"),
]


def sha(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_gate() -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([PY, "-u", GATE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    print("== 起点必须绿 ==")
    rc, out = run_gate()
    print(f"  基线退出码={rc}")
    if rc != 0:
        print("  起点不绿 → 退出")
        print("\n".join(out.strip().splitlines()[-8:]))
        return 1

    path = os.path.join(ROOT, PROACTIVE)
    with open(path, "rb") as f:
        original = f.read()
    h0 = hashlib.sha256(original).hexdigest()
    text = io.TextIOWrapper(io.BytesIO(original), encoding="utf-8", newline="").read()

    failures = []
    for name, fn, must in MUTS:
        mutated = fn(text)
        if mutated == text:
            print(f"  ✗ {name}: 锚点失效")
            failures.append(name)
            continue
        try:
            with open(path, "wb") as f:
                f.write(mutated.encode("utf-8"))
            rc, out = run_gate()
        finally:
            with open(path, "wb") as f:
                f.write(original)
            restored = sha(path) == h0
        named = must in out
        good = rc != 0 and named and restored
        print(f"  {'✓' if good else '✗'} {name}: 退出码={rc} 点名={named} 还原一致={restored}")
        if not good:
            failures.append(name)

    print(f"\nMUTATION_RESULT: {'ALL PASS' if not failures else 'FAILED ' + str(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
