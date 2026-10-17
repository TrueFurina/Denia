# -*- coding: utf-8 -*-
"""抽取 index.html 的 inline script 与外链 js，逐个交给 node --check 做真语法检查。
语法错误会让整个 <script> 不执行 → 同一块里所有的按钮绑定全部失效（静默无反应）。"""
import io, os, re, subprocess, sys

ROOT = r"G:\ai对话\11"
NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
os.chdir(ROOT)
OUT = os.path.join(ROOT, "data", "_syntax_check")
os.makedirs(OUT, exist_ok=True)

html = io.open("src/ui/web/index.html", encoding="utf-8", errors="replace").read()

blocks = []
for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I):
    attrs, body = m.group(1), m.group(2)
    if re.search(r"\bsrc\s*=", attrs, re.I):
        continue
    line_no = html[:m.start()].count("\n") + 1
    blocks.append((line_no, body))

print("inline <script> 块数:", len(blocks))

def check(name, code):
    p = os.path.join(OUT, name)
    io.open(p, "w", encoding="utf-8", newline="\n").write(code)
    r = subprocess.run([NODE, "--check", p], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stderr or "").strip()

bad = 0
for i, (ln, body) in enumerate(blocks):
    rc, err = check("inline_%02d_L%d.js" % (i, ln), body)
    if rc != 0:
        bad += 1
        print("  !! 语法错误 inline块#%d (index.html 第 %d 行起):" % (i, ln))
        print("     ", err.split("\n")[0] if err else "")
        for l in err.split("\n")[1:6]:
            print("      ", l)
    else:
        print("  ok  inline块#%d (第 %d 行起, %d 字符)" % (i, ln, len(body)))

for rel in ["src/ui/web/redesign/app_backend.js", "src/ui/web/redesign/spa.js"]:
    code = io.open(rel, encoding="utf-8", errors="replace").read()
    rc, err = check(os.path.basename(rel), code)
    if rc != 0:
        bad += 1
        print("  !! 语法错误", rel)
        print("     ", err.split("\n")[0] if err else "")
    else:
        print("  ok", rel, len(code), "字符")

print()
print("语法错误总数:", bad)

# 关键：inline 块里都用到了哪些 window.Backend.* / Backend.*
used = set()
for _, body in blocks:
    for mm in re.finditer(r"\bBackend\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)", body):
        used.add(mm.group(1))
print()
print("index.html 内联脚本用到的桥方法 (%d 个):" % len(used))
print("  ", ", ".join(sorted(used)))
