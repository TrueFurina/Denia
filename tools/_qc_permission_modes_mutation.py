# -*- coding: utf-8 -*-
"""「五档权限矩阵」的变异验证：四刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_permission_modes_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/llm/permission.py"
TEST = "tools/test_permission_modes.py"
PY = ".venv/Scripts/python.exe"


def J(*lines: str) -> bytes:
    return chr(10).join(lines).encode("utf-8")


MUTS = [
    ("A dont_ask 的执行类改成放行（无人值守时没人应答却照样动手）",
     J("    if m == MODE_DONT_ASK:", "        return DENY"),
     J("    if m == MODE_DONT_ASK:", "        return ALLOW")),

    ("B explore 也允许写文件（只读模式名不副实）",
     J("    if m == MODE_EXPLORE:", "        return DENY"),
     J("    if m == MODE_EXPLORE:", "        return ALLOW")),

    ("C 未知模式回 bypass（配置写错一个字母就变成全放行）",
     b'    return s if s in MODES else MODE_DEFAULT',
     b'    return s if s in MODES else MODE_BYPASS'),

    ("D accept_edits 的写入类退回询问（盯改代码时还要一遍遍点）",
     b'        return ALLOW if m in (MODE_ACCEPT_EDITS, MODE_DONT_ASK) else ASK',
     b'        return ASK'),
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
    print("四刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
