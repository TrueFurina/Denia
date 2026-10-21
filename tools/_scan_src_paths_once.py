# -*- coding: utf-8 -*-
"""一次性普查：src/ 真实代码里有没有硬编码的盘符绝对路径（出厂缺陷类）。

做法：.py 用 `tokenize` 剥掉 COMMENT（docstring 是 STRING，保留——它可能真是默认值），
.js/.html 剥掉注释；再用**带边界**的正则找盘符路径，避免把 `https://` 的尾巴当盘符。
"""
import io
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 关键：盘符前不能是字母数字（否则 https:// 的 s:// 也匹配）
PAT = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'`\)\]\},;]*")
SKIP_DIRS = {"__pycache__", ".git", "live2d", "node_modules", ".venv"}
EXTS = (".py", ".js", ".html", ".yaml", ".yml", ".json")
SYSTEM = re.compile(r"^[A-Za-z]:[\\/](Users|Windows|Program Files|Program Files \(x86\))")


def strip_py_comments(path: str) -> str:
    out = []
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(f.readline):
            if tok.type == tokenize.COMMENT:
                out.append(" " * len(tok.string))
            else:
                out.append(tok.string)
    # tokenize 不保留换行信息，这里只需"有没有"而非行号精确，重新按 \n 拼回
    return "\n".join(out)


def strip_js_comments(txt: str) -> str:
    out = []
    i, n = 0, len(txt)
    while i < n:
        if txt.startswith("/*", i):
            j = txt.find("*/", i + 2)
            j = (j + 2) if j >= 0 else n
            out.append(" " * (j - i))
            i = j
        elif txt.startswith("<!--", i):
            j = txt.find("-->", i + 4)
            j = (j + 3) if j >= 0 else n
            out.append(" " * (j - i))
            i = j
        elif txt.startswith("//", i) and not (
            i > 0 and (txt[i - 1] == ":" or txt[i - 1].isalnum() or txt[i - 1] == "_")
        ):
            j = txt.find("\n", i)
            j = j if j >= 0 else n
            out.append(" " * (j - i))
            i = j
        else:
            out.append(txt[i])
            i += 1
    return "".join(out)


def main() -> int:
    n = 0
    for r, dirs, names in os.walk(os.path.join(ROOT, "src")):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in sorted(names):
            if not fn.endswith(EXTS):
                continue
            p = os.path.join(r, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                raw = io.open(p, encoding="utf-8", errors="replace").read()
            except Exception as e:
                print("读不动 %s：%s" % (rel, e))
                continue
            if fn.endswith(".py"):
                try:
                    code = strip_py_comments(p)
                except Exception:
                    code = raw          # 解析不了就退回原文，宁可多报
            elif fn.endswith((".js", ".html")):
                code = strip_js_comments(raw)
            else:
                code = raw
            for i, line in enumerate(code.splitlines(), 1):
                for m in PAT.finditer(line):
                    s = m.group(0).rstrip(".")
                    if SYSTEM.match(s):
                        continue
                    print("%s:%d  %s" % (rel, i, s[:90]))
                    n += 1
    print("--- src/ 剥注释后命中 %d 处" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
