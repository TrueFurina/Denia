# -*- coding: utf-8 -*-
"""闸门：前端按钮 → JS 适配层 → Python 桥方法，这条链上不许有断点。

三层各自会静默失效：
  1. index.html 调 `Backend.foo`，但 app_backend.js 没导出 foo
     → `Backend.foo is not a function`，点了没反应（只有控制台看得到）；
  2. app_backend.js 里 `call('py_name')` 的字符串拼错 / 后端改名
     → `api[method]` 取不到，适配层返回 {offline:true}，界面显示"离线"或旧数据；
  3. 后端 CharacterAPI 没有这个方法 → 同上。
本项目已经吃过"定义齐全 ≠ 接线"的亏（AGENTS.md 第七条），这条链必须静态可查。

用法：python tools/check_bridge_methods.py
退出码：0 = 三层全对得上；1 = 有断点。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HTML = os.path.join(ROOT, "src", "ui", "web", "index.html")
JS = os.path.join(ROOT, "src", "ui", "web", "redesign", "app_backend.js")

_CALL_RE = re.compile(r"\bcall\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


def html_pages() -> list[str]:
    """所有会真正加载的页面。

    排除历史副本（`*.pre_v2.html` / `*_SIMPLIFIED*.html` / `*_pre_*.html`）：
    它们是改版前的存档，不参与运行，查它们只会制造噪音。
    """
    out: list[str] = []
    for dp, dn, fns in os.walk(os.path.join(ROOT, "src", "ui", "web")):
        dn[:] = [d for d in dn if d not in ("node_modules", "__pycache__")]
        for fn in fns:
            if not fn.endswith(".html"):
                continue
            low = fn.lower()
            if "pre_v2" in low or "simplified" in low or "_pre_" in low:
                continue
            out.append(os.path.join(dp, fn))
    return sorted(out)


def used_in_page(path: str) -> set[str]:
    html = open(path, encoding="utf-8", errors="replace").read()
    names: set[str] = set()
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I):
        if re.search(r"\bsrc\s*=", m.group(1), re.I):
            continue
        body = _strip_comments(m.group(2))
        for mm in re.finditer(r"\bBackend\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)", body):
            names.add(mm.group(1))
    return names


def _strip_comments(js: str) -> str:
    """去掉块注释与整行 `//` 注释。

    为什么必须去：页面里有一大段说明注释写着
    `· 本地视频文件 → Backend.watch_local_video：…` 和
    `（Backend.getProactivePackFlags 不存在）`——不剥注释就会把**说明文字**
    当成真实调用，报出两个假断点（第一版就是这么误报的）。
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", js)


def _object_body(js: str, brace_at: int) -> str:
    """从 `{` 位置做括号配对，返回对象体（不含外层花括号）。"""
    depth = 0
    for i in range(brace_at, len(js)):
        c = js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[brace_at + 1:i]
    return ""


def _backend_object(js: str) -> str:
    """定位 Backend 这个 IIFE 的 return 对象。

    文件里有多个 IIFE（末尾还有 `window.Fx = Fx;`），所以不能用贪婪正则：
    先锚到 `window.Backend = Backend;`，再往回找最近的 `return {`。
    """
    anchor = js.rfind("window.Backend = Backend")
    head = js[:anchor] if anchor != -1 else js
    starts = [m.start() for m in re.finditer(r"return\s*\{", head)]
    if not starts:
        return ""
    return _object_body(head, head.index("{", starts[-1]))


def exported(js: str) -> tuple[set[str], dict[str, str], set[str]]:
    """返回 (导出名集合, 名字→python 方法名, 纯前端助手名)。

    导出对象成员两种写法：
      `name,`（简写，定义在文件上半部的 `const name = () => call('py')`）
      `name: (args) => call('py'),`（内联）
    纯前端助手（`toast` / `ready` / `call` / `capacityBadge`）**不转发后端**，
    它们的定义里没有 `call('…')` —— 不能当成"接线不明"报错，否则就是噪音。
    """
    body = _strip_comments(_backend_object(js))
    if not body:
        return set(), {}, set()
    names: set[str] = set()
    mapping: dict[str, str] = {}
    helpers: set[str] = set()
    # 按顶层逗号切：内联项是 `(a) => call('x', a || {})`，括号内的逗号不能切
    depth = 0
    cur = ""
    parts: list[str] = []
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)

    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            name, rhs = p.split(":", 1)
            name = name.strip()
            if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
                names.add(name)
                mm = _CALL_RE.search(rhs)
                if mm:
                    mapping[name] = mm.group(1)
                else:
                    helpers.add(name)
        elif re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", p):
            names.add(p)
            dm = re.search(r"\b(?:const|let|var|function)\s+%s\b" % re.escape(p), js)
            if not dm:
                continue
            seg = js[dm.end():]
            cut = re.search(r"\n\s*(?:const|let|var|function)\s", seg)
            seg = seg[:cut.start()] if cut else seg[:800]
            mm = _CALL_RE.search(seg)
            if mm:
                mapping[p] = mm.group(1)
            else:
                helpers.add(p)
    return names, mapping, helpers


def backend_methods() -> set[str]:
    from src.ui.webapp import CharacterAPI
    return {n for n in dir(CharacterAPI)
            if not n.startswith("_") and callable(getattr(CharacterAPI, n, None))}


def main() -> int:
    js = open(JS, encoding="utf-8", errors="replace").read()
    exp, mapping, helpers = exported(js)
    have = backend_methods()

    pages = html_pages()
    total_used: set[str] = set()
    for p in pages:
        total_used |= used_in_page(p)
    print(f"页面 {len(pages)} 个，共调用 {len(total_used)} 个桥方法；"
          f"适配层导出 {len(exp)} 个（其中纯前端助手 {len(helpers)} 个）；"
          f"后端公开 {len(have)} 个")

    problems: list[str] = []
    # ① 页面调了、适配层没导出 —— 守卫恒 false，按钮走降级分支或直接 TypeError
    for p in pages:
        missing = sorted(used_in_page(p) - exp)
        for n in missing:
            rel = os.path.relpath(p, ROOT)
            problems.append(f"① {rel}: Backend.{n} 未导出（点了没反应/恒走降级）")
    # ② 导出了但找不到 call('…') 目标（且不是纯前端助手）
    for n in sorted(total_used & exp):
        if n in helpers:
            continue
        if not mapping.get(n):
            problems.append(f"② {n}: 适配层导出了但定义里没有 call('…')（无法确认接线）")
    # ③ 映射目标在后端不存在（含页面没用到的导出——那是潜伏的坏接线）
    for n, py in sorted(mapping.items()):
        if py not in have:
            problems.append(f"③ {n} → call('{py}')：后端 CharacterAPI 没有 {py}()")

    if problems:
        print(f"\n❌ 断点 {len(problems)} 处：")
        for p in problems:
            print("   ", p)
        return 1
    print("\n✓ 三层全对得上")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
