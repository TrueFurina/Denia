"""变异刀：`看屏性能护栏` 闸门（tools/test_screen_capture_perf_guard.py）的两把刀。

    python tools/_qc_screen_perf_guard_mutation.py

判据：退出码非 0 **且** 指定判据被点名；每把刀跑完逐字节还原并校验 sha256。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "test_screen_capture_perf_guard.py")
MAIN = "main.py"

AVAIL_DEF = "def _cloud_vision_available(cfg: Config) -> bool:"
WATCH_TAIL = "    return _cloud_vision_available(cfg)\n"


def cut_cloud_available(text: str) -> str:
    i = text.index(AVAIL_DEF)
    j = text.index("    try:\n", i)
    k = j + len("    try:\n")
    return text[:k] + "        return False  # QC：任何配置都判「没有云端视觉模型」\n" + text[k:]


def cut_switch(text: str) -> str:
    return text.replace(WATCH_TAIL, "    return True  # QC：游戏云端开关恒真\n")


MUTS = [
    ("A 云端视觉可用性恒 False", cut_cloud_available, ["D5"]),
    ("B 游戏云端接管开关恒 True", cut_switch, ["D6"]),
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
        print("  起点不绿 → 后面的红都不可信，退出")
        print("\n".join(out.strip().splitlines()[-8:]))
        return 1

    path = os.path.join(ROOT, MAIN)
    with open(path, "rb") as f:
        original = f.read()
    h0 = hashlib.sha256(original).hexdigest()
    text = io.TextIOWrapper(io.BytesIO(original), encoding="utf-8", newline="").read()

    failures = []
    for name, fn, must in MUTS:
        try:
            mutated = fn(text)
        except ValueError:
            print(f"  ✗ {name}: 锚点失效")
            failures.append(name)
            continue
        if mutated == text:
            print(f"  ✗ {name}: 变换没有产生改动")
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
        named = all(m in out for m in must)
        good = rc != 0 and named and restored
        print(f"  {'✓' if good else '✗'} {name}: 退出码={rc} 点名={named} 还原一致={restored}")
        if not good:
            failures.append(name)

    print(f"\nMUTATION_RESULT: {'ALL PASS' if not failures else 'FAILED ' + str(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
