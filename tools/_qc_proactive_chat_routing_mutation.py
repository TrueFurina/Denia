"""变异刀：`主动输出·去向分流` 闸门（tools/test_proactive_chat_routing.py）的两把刀。

    python tools/_qc_proactive_chat_routing_mutation.py

背景（2026-11-10）：这道门 21 条判据全部只验「生产出来的 payload」，
没有一条验「webapp 收下后怎么走」—— 而 2026-10-11 那个 bug 恰恰发生在 webapp。
刀 B 就是把 webapp 的分道判据改回「只看 is_proactive」，加固前它**全绿**。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "test_proactive_chat_routing.py")
PROACTIVE = os.path.join("src", "companion", "proactive.py")
WEBAPP = os.path.join("src", "ui", "webapp.py")

TO_CHAT_READ = "_to_chat = bool(getattr(self, '_duplex_emit_to_chat', False))"
WEBAPP_GUARD = 'if bool(d.get("is_proactive")) or bool(d.get("to_chat") is False):'


def cut_to_chat_read(text: str) -> str:
    return text.replace(TO_CHAT_READ, "_to_chat = False  # QC：去向意图被丢弃")


def cut_webapp_guard(text: str) -> str:
    return text.replace(WEBAPP_GUARD,
                        'if bool(d.get("is_proactive")):  # QC：消费端不再读 to_chat')


MUTS = [
    ("A 缓冲里的 to_chat 意图被丢弃", PROACTIVE, cut_to_chat_read, ["2a"]),
    ("B webapp 分道判据不再读 to_chat", WEBAPP, cut_webapp_guard, ["8a"]),
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
                print(f"  ✗ {name}: 锚点失效")
                failures.append(name)
                continue
            with open(path, "wb") as f:
                f.write(mutated.encode("utf-8"))
            rc, out = run_gate()
        finally:
            with open(path, "wb") as f:
                f.write(original)
            restored = sha(path) == h0
        named = all(("FAIL " + m) in out or ("FAIL  " + m) in out or m in out for m in must)
        good = rc != 0 and named and restored
        print(f"  {'✓' if good else '✗'} {name}: 退出码={rc} 点名={named} 还原一致={restored}")
        if not good:
            failures.append(name)

    print(f"\nMUTATION_RESULT: {'ALL PASS' if not failures else 'FAILED ' + str(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
