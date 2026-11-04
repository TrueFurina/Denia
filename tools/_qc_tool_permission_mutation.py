# -*- coding: utf-8 -*-
"""「工具权限分类完备性」的变异验证：三刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_tool_permission_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P_TOOLS = "src/llm/agent_tools.py"
TEST = "tools/check_tool_permission_coverage.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    (P_TOOLS, "A 安全类漏一个（新工具忘了归类 → 默认放行）",
     E('    "read_file", "file_search", "list_windows", "list_processes",'),
     E('    "file_search", "list_windows", "list_processes",')),

    (P_TOOLS, "B 高危类漏一个（杀进程不再询问）",
     E('    "run_command", "start_process", "kill_process",'),
     E('    "run_command", "start_process",')),

    (P_TOOLS, "C 两边重复归类（要不要确认没有答案）",
     E('WRITE_TOOLS = {\n    "write_file", "append_file", "write_clipboard",'),
     E('WRITE_TOOLS = {\n    "read_file",\n    "write_file", "append_file", "write_clipboard",')),
]


def main() -> int:
    originals = {}
    for path, *_ in MUTS:
        if path not in originals:
            originals[path] = open(path, "rb").read()
    hashes = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    bad = 0

    for path, name, old, new in MUTS:
        orig = originals[path]
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(path, "wb").write(orig.replace(old, new, 1))
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
            open(path, "wb").write(orig)

    ok = all(hashlib.sha256(open(p, "rb").read()).hexdigest() == h
             for p, h in hashes.items())
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("三刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
