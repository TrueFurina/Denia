# -*- coding: utf-8 -*-
"""全站页面内联 `<script>` 与外链 JS 的真语法检查（V8 解析器，不执行）。

为什么必须查：语法错误会让**整个 `<script>` 块**不执行 —— 同一块里的按钮绑定、事件
监听、初始化全部失效，而页面上看不出任何报错（只有控制台有一行）。历史上「工具栏
按钮全部无效」就是这一类缺陷。

【2026-10-21 扩】原来只查 `src/ui/web/index.html` 一个页面的内联块，外加两个**硬编码**
的外链 JS（`app_backend.js` / `spa.js`）。设置页、引导页、桌宠页、通知页全在盲区 ——
它们的脚本写坏了，这道闸门照样绿。现在按目录扫全站（46 页 / 65 内联块 / 23 个外链
JS = 88 段），扩完立刻抓到第一处：`probe2.html` 把 `'\\n'` 写成了**真换行**，那一整块
从未执行过。

两个实现选择，都不是随手定的：
  · **一个 node 进程解析全部 88 段**（`tools/js_syntax_runner.js`）：Windows 上起一次
    node 约 230ms，逐段 `node --check` 要 21 秒；现在 1.9 秒。
  · **解析目标（script / module）按加载上下文定**，不靠"两种都试、能过就算过"。
    `<script>` 里写 `import` 是浏览器直接 SyntaxError，而 `<script type="module">` 里
    写才对；`node --check` 的自动探测会把这个区别抹平（实测放过两处）。

扫哪些文件由 `check_bridge_methods.is_draft_html / is_draft_js` 决定（历史副本、
`_` 开头的草稿、`.min.js`）—— 与桥接闸门**共用同一份判据**，避免一边加了排除、
另一边没加，同一个文件在两个闸门里命运不同。
**注意 `app_backend.js` 在这是要查的**：桥接闸门排除它是因为它本身是被比对的对象，
而它语法坏了整个前端全哑。

退出码：0 = 无语法错误；1 = 有（门禁必须能染红，否则等于没查）；
        2 = 检查器自检失败（少扫了文件 / 结果条数对不上）；
        77 = 本机没有 node，无法验证（check_all 约定：单列「跳过」，不计通过）。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 排除规则只有一份真身（见 check_bridge_methods 里那两个函数的说明）
from check_bridge_methods import is_draft_html, is_draft_js  # noqa: E402

# node 也不写死：优先环境变量，其次 PATH，最后按「跳过」处理
NODE = os.environ.get("DANIYA_NODE") or shutil.which("node") or ""
if not NODE:
    print("[SKIP] 找不到 node（语法检查需要它）；可用 DANIYA_NODE 指定")
    raise SystemExit(77)

RUNNER = os.path.join(ROOT, "tools", "js_syntax_runner.js")
WEB = os.path.join(ROOT, "src", "ui", "web")
SKIP_DIRS = {"node_modules", "__pycache__", "morphicons", "vendor"}

# 这几个页面**必须**被扫到。少扫一个文件时闸门仍然是 0 错 —— 这是「闸门沉默」的
# 典型形态（遍历逻辑写坏 → 空列表 → 全绿）。用白名单把"覆盖范围"钉住。
MUST_SCAN = [
    "index.html",
    "redesign/ui_redesign_settings.html",
    "first_launch_setup.html",
    "redesign/app_backend.js",
]


def _walk(ext: str, is_draft) -> list:
    out = []
    for dp, dn, fns in os.walk(WEB):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith(ext) and not is_draft(fn):
                out.append(os.path.join(dp, fn))
    return sorted(out)


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


_SRC_RE = re.compile(r"""\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_MODULE_RE = re.compile(r"""\btype\s*=\s*["']?module""", re.I)
_ESM_RE = re.compile(r"^\s*(?:import|export)\b", re.M)


def _resolve(src: str, page: str) -> str:
    """把 `<script src>` 的值换算成磁盘路径（去查询串、处理 `/` 开头的站内绝对路径）。"""
    s = src.split("?")[0].split("#")[0].strip()
    if s.startswith("http") or s.startswith("//"):
        return ""
    base = WEB if s.startswith("/") else os.path.dirname(page)
    return os.path.normcase(os.path.abspath(os.path.join(base, s.lstrip("/"))))


def collect():
    """抽出每段待查 JS，并判定它该按 **script** 还是 **module** 解析。

    解析目标必须按**加载上下文**判，不能"两种都试、能过就算过"：
    `<script>` 里写了 `import` 是浏览器直接 SyntaxError（整块不执行），
    而 `<script type="module">` 里写 `import` 才是对的。两者混为一谈等于把前一种
    真缺陷洗白（Node 22 的 `node --check` 自动探测正是这个毛病，实测把
    `first_launch_setup.html` 的 module 块和 `morph_icons.js` 都放过了）。
    """
    pages = _walk(".html", is_draft_html)
    loaders = {}   # 脚本绝对路径 → {是否被 type=module 加载}
    inline = []
    for path in pages:
        rel = _rel(path)
        html = io.open(path, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I):
            attrs = m.group(1)
            is_module = bool(_MODULE_RE.search(attrs))
            sm = _SRC_RE.search(attrs)
            if sm:
                target = _resolve(sm.group(1), path)
                if target:
                    loaders.setdefault(target, set()).add(is_module)
                continue
            inline.append({
                "kind": "inline",
                "rel": rel,
                "name": "%s 内联块#%d" % (rel, sum(1 for x in inline if x["rel"] == rel)),
                "line": html[:m.start()].count("\n") + 1,
                "code": m.group(2),
                "module": is_module,
            })

    items = list(inline)
    mixed = []
    for path in _walk(".js", is_draft_js):
        rel = _rel(path)
        code = io.open(path, encoding="utf-8", errors="replace").read()
        kinds = loaders.get(os.path.normcase(os.path.abspath(path)), set())
        if len(kinds) > 1:
            mixed.append(rel)
        if kinds:
            is_module = True in kinds
        else:
            # 没有任何页面直接引它（被别人 import，或被动态加载）：按内容判
            is_module = bool(_ESM_RE.search(code))
        items.append({
            "kind": "js",
            "rel": rel,
            "name": rel,
            "line": 0,
            "code": code,
            "module": is_module,
        })
    return pages, items, mixed


def main() -> int:
    pages, items, mixed = collect()
    n_inline = sum(1 for it in items if it["kind"] == "inline")
    n_js = len(items) - n_inline
    n_pages = len(pages)
    n_mod = sum(1 for it in items if it["module"])

    print("扫描范围：页面 %d 个 / 内联块 %d 段 / 外链 JS %d 个 = 共 %d 段（其中 %d 段按 module 解析）"
          % (n_pages, n_inline, n_js, len(items), n_mod))

    # 覆盖范围自检：少了必须报，不能"扫到 0 个也全绿"
    seen = {it["rel"] for it in items}
    missing = [m for m in MUST_SCAN
               if not any(s == m or s.endswith("/" + m) for s in seen)]
    if missing:
        print("FAIL 这些页面/脚本没被扫到（遍历逻辑坏了？）：%s" % ", ".join(missing))
        return 2
    if not items:
        print("FAIL 一段 JS 都没抽到，这道闸门等于没查")
        return 2
    if mixed:
        print("FAIL 同一个文件既被 `type=module` 又被当普通脚本加载"
              "（浏览器里必有一种直接 SyntaxError）：%s" % ", ".join(mixed))
        return 2

    out_dir = tempfile.mkdtemp(prefix="daniya_jscheck_")
    manifest = []
    try:
        for i, it in enumerate(items):
            seg = os.path.join(out_dir, "seg_%04d.js" % i)
            io.open(seg, "w", encoding="utf-8", newline="\n").write(it["code"])
            manifest.append({"id": i, "name": it["name"], "file": seg,
                             "module": bool(it["module"])})
        mpath = os.path.join(out_dir, "manifest.json")
        io.open(mpath, "w", encoding="utf-8", newline="\n").write(
            json.dumps(manifest, ensure_ascii=False))

        # 模块段要用 vm.SourceTextModule 解析，它需要这个 flag（只解析、不执行）
        p = subprocess.run([NODE, "--experimental-vm-modules", RUNNER, mpath],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    if p.returncode == 2:
        print("FAIL 检查器自检失败（runner 说它自己不可信）")
        print((p.stdout or "") + (p.stderr or ""))
        return 2

    results = {}
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            print("FAIL runner 输出无法解析：%r" % line[:200])
            return 2
        if "selftest" in obj:
            print("FAIL runner 自检没过：%s" % obj["selftest"])
            return 2
        results[obj.get("id")] = obj

    # 少回一条 = 那一段其实没被检查。**必须当成失败**，不能当"没报错就是通过"。
    if set(results) != set(range(len(items))):
        got, want = len(results), len(items)
        print("FAIL 结果条数对不上：送查 %d 段，只回了 %d 段" % (want, got))
        print("     没回的段：%s" % [items[i]["name"] for i in range(len(items))
                                    if i not in results][:5])
        return 2

    bad = 0
    for i, it in enumerate(items):
        r = results[i]
        if r.get("ok"):
            continue
        bad += 1
        if it["kind"] == "inline":
            print("  !! 语法错误 %s（第 %d 行起）" % (it["name"], it["line"]))
        else:
            print("  !! 语法错误 %s" % it["name"])
        print("     ", r.get("msg") or "")
        for s in (r.get("stack") or [])[:3]:
            if s.strip():
                print("      ", s)

    print()
    if bad:
        print("FAIL：%d 段有语法错误（整块会静默不执行，该块里的按钮全部失效）" % bad)
        return 1
    print("PASS：%d 段全部通过语法检查（0 错）" % len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
