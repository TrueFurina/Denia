# -*- coding: utf-8 -*-
"""「站点整数宣称」闸门的变异验证：每把刀都必须让 check_site_counts.py 变红。

四把刀分别对应四类真实失效：
  A/B 页面或文档里的闸门数被写错（历史 149 就是这么漂的）；
  C   把「N 把变异刀」这个口径不可复现的数字加回来；
  D   新增/删除一条 GATES 后页面没跟上（权威值是动态的，必须能抓到）。

跑法：.venv/Scripts/python.exe -u tools/_qc_site_counts_mutation.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_site_counts as C  # noqa: E402

PY = ".venv/Scripts/python.exe"
TEST = "tools/check_site_counts.py"


def run_test() -> tuple[int, str]:
    r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def mutate_number(rel: str, spec_i: int, new_val: str) -> str:
    """把第 spec_i 条锚点捕获的数字改掉，返回改后的全文。"""
    rel_, pattern = C.COUNT_SPECS[spec_i]
    assert rel_ == rel
    text = C.read(rel)
    ms = list(re.finditer(pattern, text))
    assert len(ms) == 1, "%s 锚点命中 %d 次" % (rel, len(ms))
    m = ms[0]
    return text[:m.start("n")] + new_val + text[m.end("n"):]


# (标签, 文件, 变异函数)
MUTS = [
    ("A index.html 统计块闸门数改成 999",
     "site/index.html", lambda: mutate_number("site/index.html", 0, "999")),
    ("B PUBLISH.md 闸门数改成 999",
     "site/PUBLISH.md", lambda: mutate_number("site/PUBLISH.md", 2, "999")),
    ("C 把「45 把变异刀」口径不可复现的数字加回页面",
     "site/index.html",
     lambda: C.read("site/index.html").replace("<p>变异刀会故意", "<p>45 把变异刀会故意", 1)),
    ("D 删掉一条 GATES（权威值变了，页面没跟上）",
     "tools/check_all.py",
     lambda: C.read("tools/check_all.py").replace(
         '    ("长任务护栏端到端", PY_VENV, ["tools/check_loop_guard_e2e.py"], False),\n',
         '', 1)),
]


def main() -> int:
    rc, out = run_test()
    print("基线:", "全绿 ✓" if rc == 0 else "已红 ✗（无法做变异验证）")
    if rc != 0:
        print(out[-600:])
        return 1
    print()

    bad = 0
    for label, rel, fn in MUTS:
        raw = C.read(rel)
        try:
            mutated = fn()
            if mutated == raw:
                print("  ✗ 变异没生效（锚点可能失效）:", label)
                bad += 1
                continue
            C.write(rel, mutated)
            rc2, out2 = run_test()
            caught = rc2 != 0
            print(("  ✓ 变红 " if caught else "  ✗ 没红 ") + label)
            if not caught:
                bad += 1
                print("     ", out2[-240:].replace("\n", " "))
        finally:
            C.write(rel, raw)

    rc3, _ = run_test()
    print("\n还原后基线:", "全绿 ✓" if rc3 == 0 else "仍红 ✗")
    if rc3 != 0:
        bad += 1
    print("结果: %d 把刀，%d 把没被抓住" % (len(MUTS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
