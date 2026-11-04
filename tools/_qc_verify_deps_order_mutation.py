# -*- coding: utf-8 -*-
"""「出包验收时序」闸门的变异验证：每把刀都必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_verify_deps_order_mutation.py

四刀对应四种回归形态：把延后开关改回去、把最终验收删掉、把 DLL 铺设删掉、
把 _pth 写入删掉。最后一类尤其重要——「验收还在、但隔离没了」看起来最像
正常代码，却是 2026-11-04 那个 pywintypes 假红的直接成因。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/make_dist.py"
TEST = "tools/check_verify_deps_order.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 把延后验收开关改回去（首次装配必假红）",
     E("                               defer_verify=True) and deps_ok"),
     E("                               defer_verify=False) and deps_ok")),

    ("B 把隔离后的最终验收整段删掉",
     E("        if not verify_deps(out, probe_py):"),
     E("        if False:")),

    ("C 删掉 pywin32 DLL 铺设",
     E("        dlls = bundle_pywin32_dlls(out)"),
     E("        dlls = []")),

    ("D 删掉 _pth 写入（隔离没生效）",
     E("        enabled, iso_lines = write_isolated_pth(out, enable=not args.no_isolate)"),
     E("        enabled, iso_lines = (False, [])")),
]


def main() -> int:
    orig = io.open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        io.open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = r.returncode != 0
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1][:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（闸门失效）"))
            if not caught:
                bad += 1
        finally:
            io.open(P, "wb").write(orig)

    ok = hashlib.sha256(io.open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("%d 刀全红" % len(MUTS) if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
