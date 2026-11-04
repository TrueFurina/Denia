# -*- coding: utf-8 -*-
"""「run_command 守卫」的变异验证：三刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_run_command_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/llm/agent_tools.py"
TEST = "tools/test_run_command_guard.py"
PY = ".venv/Scripts/python.exe"


def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 不传 cwd（相对路径落到仓库根，和文件工具边界不一致）",
     B("            cwd=workspace_dir(),\n"),
     B("")),

    ("B 白名单为空也放行（回到「默认全开」）",
     B('    if not allow:\n'
       '        return "命令白名单为空（agent.allow_commands 未配置），已拒绝执行。"\n'),
     B("")),

    ("C 不拦 shell 元字符（允许串联/管道/重定向）",
     B("    if _SHELL_META_RE.search(command):"),
     B("    if False:")),
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
