# -*- coding: utf-8 -*-
"""一次性普查：tools/ 下硬编码开发机绝对路径的死工具。"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PATS = [
    "G:\\ai对话", "G:/ai对话",
    "H:\\Comni", "H:/Comni",
    "H:\\daniya_voice_tools", "H:\\minicpm",
    "G:\\视频录制",
]


def tracked() -> set:
    p = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return set(x.replace("\\", "/") for x in (p.stdout or "").splitlines() if x.strip())


def main() -> int:
    tr = tracked()
    rows = []
    for r, dirs, names in os.walk(os.path.join(ROOT, "tools")):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for n in sorted(names):
            if not n.endswith((".py", ".js", ".html")):
                continue
            p = os.path.join(r, n)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                t = io.open(p, encoding="utf-8", errors="replace").read()
            except Exception as e:
                print("读不动 %s：%s" % (rel, e))
                continue
            found = sorted({q for q in PATS if q in t})
            if found:
                rows.append((rel, rel in tr, found))
    for rel, is_tr, found in rows:
        print("%-46s 跟踪=%-5s %s" % (rel, "是" if is_tr else "否", found))
    print("---")
    print("合计 %d 个文件；其中被 git 跟踪（会进队友包）%d 个"
          % (len(rows), sum(1 for _r, t, _f in rows if t)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
