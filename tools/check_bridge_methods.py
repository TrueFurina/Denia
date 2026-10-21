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


def js_files() -> list[str]:
    """外链 JS（`<script src=...>`）—— 这是原来完全没查的盲区。

    【2026-10-21 补】原来只扫 HTML 里的**内联** `<script>`。而引导页把「建角色」整步
    拆进了 `onboarding_role.js`（2000+ 行，`<script src>` 加载），它的桥调用对闸门
    完全不可见 —— 实测里面就躺着一处断线（`B.downloadVoiceModel`，导出名其实是
    `downloadModel`），而闸门一直是绿的。

    排除三类，避免把噪音当断点：
      - `app_backend.js`：适配层自己（它就是被比对的对象）；
      - 文件名以 `_` 开头：设计稿/实验稿（与 `html_pages()` 同一约定）；
      - 第三方打包产物（`*.min.js`、`morphicons/` 之类 hash 命名的 bundle）。
    """
    out: list[str] = []
    web = os.path.join(ROOT, "src", "ui", "web")
    for dp, dn, fns in os.walk(web):
        dn[:] = [d for d in dn
                 if d not in ("node_modules", "__pycache__", "morphicons", "vendor")]
        for fn in fns:
            if not fn.endswith(".js"):
                continue
            low = fn.lower()
            if low.startswith("_") or low.endswith(".min.js") or fn == "app_backend.js":
                continue
            if "pre_v2" in low or "simplified" in low:
                continue
            out.append(os.path.join(dp, fn))
    return sorted(out)


# 外链 JS 里 Backend 的别名。`B` 是最常见的（函数参数名，调用方传 `getB()`）；
# `Backend` / `window.Backend` / `bk` 也出现过。**只认调用形态** `X.name(`，
# 属性读取（`X.name`）不报——很多地方是 `if(X.foo)` 的探测，不构成断点证据。
_ALIAS_CALL_RE = re.compile(
    r"\b(?:B|bk|Backend|window\s*\.\s*Backend|window\s*\.\s*parent\s*\.\s*Backend)"
    r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
# `B` 这个单字母别名太泛，只有在文件里真出现过"取 Backend"的动作时才认它。
# 三种真实形态（都实测过）：
#   - `function getB(){...}` + `_b()` 包装（引导页两件套：onboarding_role / model_downloader）
#   - 直接 `window.Backend` / `parent.Backend`
#   - **工厂函数参数**：`llm_write.js` 的 `function make(B)`，调用方写
#     `LlmWrite.make(Backend)` —— 不认这种就会整文件漏扫（它调了 5 个桥方法）
_ALIAS_HINT_RE = re.compile(
    r"\bgetB\s*\(|\b_b\s*\(|window\s*\.\s*Backend"
    r"|function\s+[\w$]*\s*\(\s*B\s*[,)]"
    r"|\(\s*B\s*\)\s*=>")


def used_in_js(path: str) -> set[str]:
    src = _strip_comments(open(path, encoding="utf-8", errors="replace").read())
    has_alias = bool(_ALIAS_HINT_RE.search(src))
    names: set[str] = set()
    for m in _ALIAS_CALL_RE.finditer(src):
        whole = m.group(0)
        # 单字母别名（B/bk）要求文件里确实在取 Backend，否则可能是同名的局部对象
        if whole[0] in "Bb" and not whole.startswith("Backend") and not has_alias:
            continue
        names.add(m.group(1))
    return names


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
    # ①b 外链 JS 调了、适配层没导出（2026-10-21 补的盲区）
    js_seen = 0
    for p in js_files():
        names = used_in_js(p)
        js_seen += len(names)
        for n in sorted(names - exp):
            rel = os.path.relpath(p, ROOT)
            problems.append(
                f"① {rel}: Backend.{n} 未导出（外链 JS，多半是方法名写错；"
                f"`if(B.{n})` 这类守卫会让它静默降级，不报错）")
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
