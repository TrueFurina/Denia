# -*- coding: utf-8 -*-
"""抽取 HTML 内联 <script> 块并做 JS 语法检查（node --check）。

用途：改完 HTML 里的内联脚本后，确认没有语法错误（括号/引号不配对等），
比"肉眼看"可靠。仅做只读检查，不修改任何文件。

用法：
    python tools/check_html_scripts.py <html文件> [<html文件> ...]

退出码：0 = 全部通过；1 = 有失败或抽取失败。
"""
import re
import subprocess
import sys
import tempfile
import os
from pathlib import Path

NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

# 匹配 <script ...> ... </script>（不带 src 的内联块）
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def extract_blocks(html: str):
    out = []
    for i, m in enumerate(SCRIPT_RE.finditer(html)):
        attrs, body = m.group(1), m.group(2)
        if re.search(r"\bsrc\s*=", attrs, re.IGNORECASE):
            continue          # 外链脚本跳过
        if not body.strip():
            continue
        # 行号（1-based）便于报错定位
        line = html[: m.start(2)].count("\n") + 1
        out.append((i, line, body))
    return out


def check_one(path: Path) -> tuple[int, int]:
    html = path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_blocks(html)
    ok = fail = 0
    print(f"\n=== {path.name}  内联 script 块: {len(blocks)} ===")
    for idx, line, body in blocks:
        fd, tmp = tempfile.mkstemp(suffix=".js", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                # 包一层：HTML 内联脚本允许顶层 return 之外的合法语句；
                # function 声明在脚本顶层合法，直接交给 node --check。
                f.write("(function(){\n" + body + "\n})();\n")
            p = subprocess.run(
                [NODE, "--check", tmp],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if p.returncode == 0:
                ok += 1
                print(f"  [OK]   block#{idx} (起始行 {line})")
            else:
                fail += 1
                err = (p.stderr or "").strip().splitlines()
                print(f"  [FAIL] block#{idx} (起始行 {line}) -> {err[-1] if err else 'unknown'}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return ok, fail


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    total_ok = total_fail = 0
    for a in argv[1:]:
        p = Path(a)
        if not p.exists():
            print(f"!! 文件不存在: {p}")
            total_fail += 1
            continue
        o, f = check_one(p)
        total_ok += o
        total_fail += f
    print(f"\n===== 合计 OK={total_ok} FAIL={total_fail} =====")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
