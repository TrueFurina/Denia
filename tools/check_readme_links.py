"""README 与文档里的本地链接不能悬空。

为什么单独做一道闸（2026-10-20）：
仓库上了 GitHub，README 就是门面。它的坏法很安静——**没人会点**，所以断了也
不会有人报错，直到新来的人照着点一次 404。而"文档搬家"恰恰是最容易制造悬空的
动作（本次就把 `README.md` 挪成了 `docs/使用指南_内测版v1.0.md`）。

判据只查**本地相对路径**：
  - `[文字](path)` 的 path
  - `<img src="path">` 的 path
跳过 http(s)://、mailto:、页内锚点 `#xxx`——那些的可用性不是这个仓库能保证的。

跑法：python tools/check_readme_links.py
退出码：0 = 全部可解析；1 = 有悬空链接。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 要检查的文件：门面 + 最常被点的两份
TARGETS = ["README.md", "AGENTS.md", "docs/使用指南_内测版v1.0.md"]

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_SRC = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
# 代码块里的东西不是链接（```...```），先剔掉
FENCE = re.compile(r"```.*?```", re.S)


def _links(text: str) -> list:
    out = []
    for m in MD_LINK.finditer(text):
        out.append(m.group(1))
    for m in HTML_SRC.finditer(text):
        out.append(m.group(1))
    return out


def _skip(target: str) -> bool:
    low = target.lower()
    return (low.startswith("http://") or low.startswith("https://")
            or low.startswith("mailto:") or target.startswith("#"))


def main() -> int:
    errors = []
    checked = 0
    for rel in TARGETS:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            errors.append("%s：文件不存在" % rel)
            continue
        with open(path, encoding="utf-8") as f:
            text = FENCE.sub("", f.read())
        for tgt in _links(text):
            if _skip(tgt):
                continue
            # 去掉可能的 #锚点尾巴，再按**文档所在目录**解析（不是仓库根）
            pure = tgt.split("#", 1)[0]
            if not pure:
                continue
            checked += 1
            base = os.path.dirname(path)
            full = os.path.normpath(os.path.join(base, pure))
            if not os.path.exists(full):
                errors.append("%s → %s（解析为 %s）" % (rel, tgt, full))

    print("检查 %d 个文件，本地链接 %d 条" % (len(TARGETS), checked))
    if errors:
        for e in errors:
            print("  ✗ " + e)
        print("\n✗ %d 条链接悬空：文档搬家后要同步改链接" % len(errors))
        return 1
    print("✓ 本地链接全部可解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
