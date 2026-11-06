# -*- coding: utf-8 -*-
"""官网「整数宣称」对账：闸门数由 GATES 动态给出，不许手填后遗忘。

与 check_release_numbers.py 的分工：
  - 那边管**发布产物口径**（体积 / SHA / 版本 / doctor），真值在 site/release.json；
  - 这边管**开发态指标**（闸门数），权威值直接取 len(check_all.GATES)。
    闸门数会随每次新增检查而变，放进 release.json 会把它混成发布口径，
    且每次加闸门都要人记得改两处 —— 那正是 149 → 191 漂移的成因。

自指设计：本闸门自己登记在 GATES 里，所以权威值天然包含自己，
新增任何闸门后跑 `--fix` 即可，无需手算。

另一条判据：禁止站点再出现「N 把变异刀」这类**口径不可复现**的数字。
实测过三种数法互不相等（GATES 含「变异」48 / MUTS 条目 144 / 第4字段 True 55），
历史值 45 没有任何一种数法能得到 ⇒ 写死必然是假数字，只能撤掉。

用法：
    python tools/check_site_counts.py            # 对账（闸门调用）
    python tools/check_site_counts.py --fix      # 把 GATES 实测值回填进站点
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_all as gates  # noqa: E402

# (文件, 含命名组 n 的正则)。每条必须**唯一命中**，命中 0 次或多次都判红。
COUNT_SPECS = [
    ("site/index.html",
     r'<div class="stat"><b>(?P<n>\d+)</b><span>发布前必须全绿的闸门</span></div>'),
    ("site/index.html",
     r'<tr><td>发布闸门</td><td class="mono">(?P<n>\d+) 道 · '),
    ("site/PUBLISH.md", r'- (?P<n>\d+) 道发布闸门全绿'),
    ("site/PUBLISH.md", r'/ (?P<n>\d+) 道门禁）'),
]

# 「N 把变异刀」口径不可复现，出现即判红（含历史值 45）。
BANNED_PATTERNS = [
    (r'\d+\s*把变异刀', "「N 把变异刀」口径未定义、不可复现"),
]

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  <- " + detail) if detail else ""))


def read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8", newline="") as f:
        return f.read()


def write(rel: str, text: str) -> None:
    # 二进制写入，保留真实 EOL（Windows 文本模式会把 \n 洗成 \r\n）
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(text.encode("utf-8"))


def authority() -> int:
    """唯一权威值：当前登记的闸门总数（含本闸门自身）。"""
    return len(gates.GATES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="把权威值回填进站点")
    args = ap.parse_args()

    want = authority()
    print("权威值 len(check_all.GATES) = %d\n" % want)
    chk("权威值为正（GATES 可导入）", want > 0)

    texts = {}
    for rel in {p for p, _ in COUNT_SPECS} | {"site/index.html", "site/PUBLISH.md"}:
        texts[rel] = read(rel)

    print("1) 闸门数锚点对账")
    per_file = {}
    for i, (rel, pattern) in enumerate(COUNT_SPECS, 1):
        ms = list(re.finditer(pattern, texts[rel]))
        label = "%s 锚点#%d" % (os.path.basename(rel), i)
        if len(ms) != 1:
            # 结构坏了（锚点找不到或不唯一）即使 --fix 也不能算过。
            chk(label, False, "锚点命中 %d 次，期望 1" % len(ms))
            continue
        got = ms[0].group("n")
        if got == str(want):
            chk("%s = %d" % (label, want), True)
        elif args.fix:
            # 修复动作不是失败；回填后本次返回 0，由下一次对账复验。
            print("  FIX  %s: %s → %d" % (label, got, want))
            m = ms[0]
            texts[rel] = (texts[rel][:m.start("n")] + str(want)
                          + texts[rel][m.end("n"):])
            per_file[rel] = True
        else:
            chk("%s = %d" % (label, want), False, "实际 %s" % got)

    print("\n2) 站点不得出现口径不可复现的数字")
    for rel in ("site/index.html", "site/PUBLISH.md"):
        for pattern, why in BANNED_PATTERNS:
            hits = re.findall(pattern, texts[rel])
            chk("%s 无「%s」" % (os.path.basename(rel), why),
                not hits, str(hits[:3]))

    if args.fix and per_file:
        for rel in per_file:
            write(rel, texts[rel])
            print("\n  · 已回填 %s" % rel)
        print("  （回填后请重新跑一次对账）")

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
