# -*- coding: utf-8 -*-
"""抽取 index.html 的 inline script 与外链 js，逐个交给 node --check 做真语法检查。
语法错误会让整个 <script> 不执行 → 同一块里所有的按钮绑定全部失效（静默无反应）。

退出码：0 = 无语法错误；1 = 有语法错误（门禁必须能染红，否则等于没查）；
        77 = 本机没有 node，无法验证（check_all 约定：单列「跳过」，不计通过）。
"""
import io, os, re, shutil, subprocess, sys, tempfile

# 路径从脚本自身推，别写死构建机目录：以前这里是 `G:\ai对话\11`，
# 换台机器直接 FileNotFoundError（2026-10-18 质检 P3）。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# node 也不写死：优先环境变量，其次 PATH，最后按「跳过」处理
NODE = os.environ.get("DANIYA_NODE") or shutil.which("node") or ""
if not NODE:
    print("[SKIP] 找不到 node（node --check 语法检查需要它）；可用 DANIYA_NODE 指定")
    raise SystemExit(77)
os.chdir(ROOT)
# 中间产物放系统临时目录：以前写 data/_syntax_check，会往用户数据目录里堆垃圾
OUT = tempfile.mkdtemp(prefix="daniya_jscheck_")

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

shutil.rmtree(OUT, ignore_errors=True)
# 【2026-10-18 修】原来打印完就结束，永远 exit 0 —— 语法错误再多门禁也不红，
# 属于「装饰闸门」。现在有错必须 exit 1。
if bad:
    print()
    print("FAIL：%d 处语法错误（整个 <script> 块会静默不执行，该块里的按钮全部失效）" % bad)
    raise SystemExit(1)
print()
print("PASS：语法错误 0")
raise SystemExit(0)
