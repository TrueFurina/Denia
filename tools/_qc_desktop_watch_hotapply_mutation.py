"""变异刀：`看屏热生效` 闸门（tools/test_desktop_watch_hotapply.py）的四把刀。

判据：退出码非 0 **且** 指定判据被点名；每把刀跑完逐字节还原并校验 sha256。

    python tools/_qc_desktop_watch_hotapply_mutation.py

背景（2026-11-10）：原闸门 C1 只写 `va.calls == 0`、D2 只写 `r is None` ——
两条都是纯负向断言：把 capture_now 改成空操作、或让 LLM 桩坏掉，读数完全一样。
已补 C2（开闸时真的进入抓屏流程）与 D5（开闸 + 可用 LLM 时必须拿到结果）两条正控。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "test_desktop_watch_hotapply.py")
SC = os.path.join("src", "perception", "screen_capture.py")
VA = os.path.join("src", "perception", "vision_analyzer.py")

LIVE_READ = "            live = bool(self._cfg.runtime_status.vision_enabled)"
LOOP_GUARD = "                if not self.enabled:\n                    self._last_hash = None"
CAPTURE_NOW = "    async def capture_now(self, force: bool = False) -> None:"
HARD_GATE = "            if not bool(self._cfg.runtime_status.vision_enabled):\n                self._disabled = True"


def cut_live(text: str) -> str:
    return text.replace(LIVE_READ, "            live = self._enabled  # QC：退回构造期快照")


def cut_loop_guard(text: str) -> str:
    return text.replace(LOOP_GUARD,
                        "                if False:  # QC：循环内不再重读开关\n                    self._last_hash = None")


def neuter_capture_now(text: str) -> str:
    i = text.index(CAPTURE_NOW)
    k = text.index("\n", i) + 1
    return text[:k] + "        return  # QC：capture_now 变空操作\n" + text[k:]


def remove_hard_gate(text: str) -> str:
    i = text.index(HARD_GATE)
    end = text.index("                return None\n", i) + len("                return None\n")
    return text[:i] + "            pass  # QC：硬闸被拆\n" + text[end:]


MUTS = [
    ("A enabled 退回构造期快照", SC, cut_live, "A2 改 cfg 后立即可见"),
    ("B 循环内不再重读开关", SC, cut_loop_guard, "B2 关闸后不再抓屏"),
    ("C capture_now 变空操作", SC, neuter_capture_now, "C2 开闸时 capture_now 真的进入抓屏流程"),
    ("D analyze 的用户硬闸被拆", VA, remove_hard_gate, "D3 关闸后 disabled=True"),
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
        print("\n".join(out.strip().splitlines()[-10:]))
        return 1

    failures = []
    for name, rel, fn, must in MUTS:
        path = os.path.join(ROOT, rel)
        with open(path, "rb") as f:
            original = f.read()
        h0 = hashlib.sha256(original).hexdigest()
        text = io.TextIOWrapper(io.BytesIO(original), encoding="utf-8", newline="").read()
        try:
            mutated = fn(text)
            if mutated == text:
                print(f"  ✗ {name}: 变换没有产生改动（锚点失效）")
                failures.append(name)
                continue
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
