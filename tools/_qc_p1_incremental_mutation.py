"""变异刀：`P1 增量写接口` 闸门（tools/test_p1_incremental.py）的两把刀。

    python tools/_qc_p1_incremental_mutation.py

背景（2026-11-10）：老路径（无 atomic）原本只验「返回 success + 没碰双工/没写盘」，
一个「直接 return success、根本不调 pro.set_mixed_mode」的空实现也能全过。
已补 ⑦「老调用仍真的切换混合开关」这条副作用正控。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "test_p1_incremental.py")
WEBAPP = os.path.join("src", "ui", "webapp.py")

LEGACY_CALL = "            r = pro.set_mixed_mode(enabled)"
ATOMIC_BRANCH = '        if d.get("atomic"):\n            return self._set_mixed_mode_atomic(enabled, d)'


def empty_legacy(text: str) -> str:
    return text.replace(LEGACY_CALL,
                        '            r = {"success": True, "enabled": enabled}  # QC：不调 pro，只回 success')


def extra_write(text: str) -> str:
    return text.replace(ATOMIC_BRANCH,
                        '        if d.get("atomic"):\n'
                        '            self._save_config()  # QC：原子路径前多写一次盘\n'
                        '            return self._set_mixed_mode_atomic(enabled, d)')


MUTS = [
    ("A 老路径变空实现（只回 success，不调 pro）", empty_legacy, "老调用仍真的切换混合开关"),
    ("B 原子路径多写一次盘", extra_write, "只写一次盘"),
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

    path = os.path.join(ROOT, WEBAPP)
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
