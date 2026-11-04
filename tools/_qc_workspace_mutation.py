# -*- coding: utf-8 -*-
"""「工作区边界」的变异验证：三刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_workspace_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/llm/agent_tools.py"
TEST = "tools/test_workspace_guard.py"
PY = ".venv/Scripts/python.exe"


def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 越界不拦（回到「想写哪就写哪」）",
     B("    if not inside:\n"),
     B("    if False:\n")),

    ("B 不解析符号链接（链接指向区外就能逃出去）",
     B("    cand = os.path.realpath(cand)\n"),
     B("    cand = cand\n")),

    ("C 不检查空路径（没给名字就落到工作区根）",
     B('    if not raw:\n        raise WorkspaceEscape("未提供文件名")\n'),
     B("")),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = r.returncode != 0
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("三刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
