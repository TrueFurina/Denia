# -*- coding: utf-8 -*-
"""闸门：前端 WebView2 消息监听器里分发的 type，后端必须真的会广播。

## 为什么要单独一条

后端往前端推消息走 `CoreWebView2.PostWebMessageAsJson`（`main_window._wv2_broadcast`，
2026-09-03 起改为线程池非阻塞推送），前端接的是
`window.chrome.webview.addEventListener('message', …)`。两边靠一个字符串 `type` 对上，
**而这个字符串没有任何机制保证它对得上**：

- 2026-10-02 写了 `d.type === 'character_switched'` 分支（为了让"切换角色后界面里
  写死的名字"自动跟随），但后端从来没往这条通道发过这个 type ——
  `EventType.CHARACTER_SWITCHED` 只经 `ws_server.broadcast` 发（`{message_type,
  payload}` 信封，给 Unity 与旧壳 `app.js` 用），而 `app_backend.js` 里根本没有
  WebSocket 客户端。那条分支自写下那天起就是死的：用户在角色页点「设为默认角色」，
  toast 说成功了，侧边栏 / 通话页 / 输入框 placeholder 里的名字却一直是旧角色，
  直到刷新页面。（2026-10-22 修：后端补广播 + 前端显式兜底，两端都修。）
- 2026-10-22 删掉的 `quota_alert` 是同一病的另一例：Router 有回调槽位、webapp 有
  注册点、前端有浮层，但后端广播全集里从来没有这个 type。

症状永远是**静默**的：分支不执行，没有报错，没有空指针，只是"那个功能不工作"。

## 判据（单向，只查一个方向）

**前端监听器里出现的每个 type，都必须在后端可广播全集里。**

只做单向，是实测逼出来的：反向（"后端广播了的 type 前端必须接"）会假红 ——
`pet_tip` 就是反例，桌宠窗的 `chrome.webview` 监听对 `PostWebMessageAsJson`
**完全不触发**（rig 页 2026-09-03 实锤：3 次广播 / 0 回执），早就改成 1.5s 轮询
`api.pop_pet_bubble()` 了。所以"广播了但没人用 webview 监听器接"在这里是**合法设计**。

后端全集按两处收集（都是这条通道的真实发送点）：
  1. `_wv2_broadcast({... "type": "<x>" ...})` 的调用点（webapp.py 13 处）
  2. `_BROADCASTER({... "type": "<x>" ...})` 的调用点（agent_confirm_broker.py 1 处，
     启动时被注入的就是 `_wv2_broadcast` 本身）

**不把 `protocol.MessageType` 枚举算进全集**：那 16 种是给 Unity/ws 客户端的，
收进来只会让全集变大、判据变松。前端真有分支要接枚举里的 type 时，正确做法是
在后端显式 `_wv2_broadcast({"type": ...})` 广播一次（character_switched 就是这么修的）。

## 边界（一刀切必假红，实测过）

全仓库 `.type === 'x'` 有 25 处以上**不是**消息分发：`btn.dataset.type === "kill"`
（HTML 数据属性）、本地事件总线的 `ev.type === 'asr'`、通知项 `n.type === 'ok'`、
骨架 `s.type === 'row'`、spa 的 `s.type === 'module'`……所以判据必须精确圈到
**host message 监听器的回调体内部**，圈法是：

  `chrome.webview` → 同文件后续第一个 `addEventListener('message'` → 回调体的花括号配对

另外每个 `window.addEventListener('message'`（页面内部 postMessage）都必须在
`PAGE_INTERNAL` 里登记原因——**新出现的未登记监听器要报红**，否则这道门会悄悄漏掉
一整类载体（"分层地瞎"的老毛病：补一层才发现下一层）。

## 自保（闸门自己坏了要能喊出来）

- 广播调用点数 / 取到 type 字面量的调用点数必须相等（**不允许动态 type**，
  否则全集不可知而判据还在跑）
- 广播调用点 ≥ 10、后端 type ≥ 4、webview 监听器 ≥ 4、前端 type ≥ 5
- 每个监听器体里至少要有一处 `.type` 比较（花括号配对坏掉 → 体是空的 → 这里红）
- `PAGE_INTERNAL` 里有条目失效（那个文件已经没有 `window.addEventListener('message')` 了）→ 红

用法：python tools/check_broadcast_types.py
退出码：0 = 对得上；1 = 有前端分支的后端不广播 / 覆盖变小；2 = 闸门自己失效。
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_bridge_methods import is_draft_html, is_draft_js  # noqa: E402  共用"什么不算产品代码"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "src", "ui", "web")
SRC = os.path.join(ROOT, "src")

# 这条通道的真实发送点。两个都必须扫到，少一个说明收集逻辑坏了。
BROADCAST_CALLEES = ("_wv2_broadcast", "_BROADCASTER")

# `window.addEventListener('message'` 是**页面内部** postMessage（iframe ↔ 父页 /
# 主题编辑器 ↔ 宿主），不是后端广播。每条写清楚是谁发给谁的。
# 判据按"文件"记（不记行号——行号会漂），条目失效要报红催删。
PAGE_INTERNAL: dict[str, str] = {
    "first_launch_setup.html":
        "能力槽 iframe 打开/关闭配置弹层时 postMessage 过来（onboard-capslot-open/close），父页放大/回落 iframe",
    "index.html":
        "引导页 iframe 加载完成后 postMessage 'onboarding-done' 通知父页收尾",
    "redesign/theme_manager.js":
        "主题编辑器 iframe 与宿主的预览/保存/关闭通知（__tm_preview / __te5 标志位）",
    "live2d/live2d.js":
        "Live2D 宿主页收 rig iframe 的 live2d-rig-ready / live2d-tip（rig 是子帧，宿主是 window）",
    "live2d/rig/index.html":
        "rig iframe 收宿主页的 live2d-speak/expr/action/fx-mode/fx-apply/psd（父→子帧，不是后端广播）",
}

# 墓碑：2026-10-22 删掉的额度浮层整条死链。后端不广播、前端不分发，两边都不许回来。
TOMBSTONE_TYPES = ("quota_alert",)

MIN_BROADCAST_CALLS = 10      # 现在 14 处
MIN_BROADCAST_TYPES = 4       # 现在 6 种
MIN_WEBVIEW_LISTENERS = 4     # 现在 4 个
MIN_FRONTEND_TYPES = 5        # 现在 5 种
MIN_PAGE_INTERNAL = 5         # 现在 5 个文件

# 分发分支：`d.type === 'chat_push'` / `ev.data.type == 'call_push'` / 反向写法。
# 只认 `==` / `===`：`!==` 多是"不是这个就 return"的守卫，不是分发分支。
_TYPE_CMP = re.compile(
    r"(?P<lhs>[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\.\s*type\s*={2,3}\s*['\"](?P<a>[\w.:-]+)['\"]"
    r"|['\"](?P<b>[\w.:-]+)['\"]\s*={2,3}\s*(?P<rhs>[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\.\s*type"
)


# ---------------------------------------------------------------- 剥注释（保长度）
def code_mask(txt: str) -> str:
    """把注释字符替换成空格，**长度与行号不变**（下标还能对回原文）。

    状态机一次性扫描，**先判块注释再判行注释**——反过来会出大事：先剥行注释的正则会把
    块注释同一行的 `*/` 一起删掉 → 块注释只剩 `/*` → 后续非贪婪匹配错位 → 吃掉大段
    真实代码 → 判据绿着失效（2026-10-22 在 `_qc_quota_tombstone_mutation` 上真踩过）。
    块注释里只要含 `//`（URL 最常见）就触发。
    """
    out = list(txt)
    i, n = 0, len(txt)
    while i < n:
        if txt.startswith("/*", i):
            j = txt.find("*/", i + 2)
            j = (j + 2) if j >= 0 else n
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif txt.startswith("<!--", i):
            j = txt.find("-->", i + 4)
            j = (j + 3) if j >= 0 else n
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif txt.startswith("//", i) and not (
            i > 0 and (txt[i - 1] == ":" or txt[i - 1].isalnum() or txt[i - 1] == "_")
        ):
            j = txt.find("\n", i)
            j = j if j >= 0 else n
            for k in range(i, j):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


# ---------------------------------------------------------------- 花括号配对
_REGEX_KW = {"return", "typeof", "case", "in", "of", "new", "delete", "void",
             "do", "else", "instanceof", "yield", "await"}


def _is_regex_start(masked: str, i: int) -> bool:
    """masked[i] == '/'：按标准启发式判断这是不是正则字面量（而不是除号）。"""
    j = i - 1
    while j >= 0 and masked[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    if masked[j] in "(,=:[!&|?{};+-*%<>~^":
        return True
    k = j
    while k >= 0 and (masked[k].isalnum() or masked[k] in "_$"):
        k -= 1
    return masked[k + 1:j + 1] in _REGEX_KW


def _skip_regex(masked: str, i: int) -> int:
    """masked[i] 是正则起点的 '/'，返回正则结束后的下标；不像正则就返回 -1。"""
    i += 1
    n = len(masked)
    in_class = False
    while i < n:
        c = masked[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return -1
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "/":
            return i + 1
        i += 1
    return -1


def match_brace(masked: str, i: int) -> int:
    """masked[i] == '{'，返回配对 '}' 的下标；跳过字符串、正则与注释（注释已 mask 成空格）。"""
    depth = 0
    n = len(masked)
    while i < n:
        c = masked[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n:
                if masked[i] == "\\":
                    i += 2
                    continue
                if masked[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and _is_regex_start(masked, i):
            j = _skip_regex(masked, i)
            if j > 0:
                i = j
                continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# ---------------------------------------------------------------- 前端监听器
def scan_web_files() -> list[str]:
    out = []
    for r, dirs, names in os.walk(WEB):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            p = os.path.join(r, n)
            if n.endswith(".js") and not is_draft_js(n):
                out.append(p)
            elif n.endswith(".html") and not is_draft_html(n):
                out.append(p)
    return sorted(out)


_LISTEN = re.compile(r"addEventListener\s*\(\s*['\"]message['\"]")


def find_listeners(masked: str) -> list[tuple[int, str]]:
    """返回 (addEventListener 下标, 接收者最后一段名字)。

    接收者是 `window` → 页面内部 postMessage（`window.addEventListener('message')`）；
    其它（`wv` / `webview` / `contentWindow`…）→ 一律当 host 广播监听器，交给后面的
    「文件里必须有 chrome.webview」把关。取**点分链最后一段**：`window.addEventListener`
    要判成 `window`，`window.chrome.webview.addEventListener` 要判成 `webview`。
    """
    out = []
    for m in _LISTEN.finditer(masked):
        j = m.start() - 1
        # `addEventListener` 前面允许有空白和一个点号（`window .addEventListener` 也合法），
        # 但**不能**把空白一直往前跳过再当标识符结尾——那样换行+缩进会掏出空串。
        while j >= 0 and masked[j] in " \t\r\n":
            j -= 1
        if j >= 0 and masked[j] == ".":
            j -= 1
            while j >= 0 and masked[j] in " \t\r\n":
                j -= 1
        end = j
        while j >= 0 and (masked[j].isalnum() or masked[j] in "_$"):
            j -= 1
        chain = masked[j + 1:end + 1]
        out.append((m.start(), chain.rsplit(".", 1)[-1] if chain else ""))
    return out


def listener_body(masked: str, at: int) -> str:
    """从 `addEventListener('message', <cb>)` 取回调体（第一个 `{` 到配对 `}`）。"""
    p = masked.find("(", at)
    if p < 0:
        return ""
    c = masked.find(",", p)
    if c < 0:
        return ""
    b = masked.find("{", c)
    if b < 0:
        return ""
    e = match_brace(masked, b)
    if e < 0:
        return ""
    return masked[b:e + 1]


def types_in(body: str) -> set[str]:
    out = set()
    for m in _TYPE_CMP.finditer(body):
        t = m.group("a") or m.group("b")
        if t:
            out.add(t)
    return out


# ---------------------------------------------------------------- 后端广播全集
def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def backend_broadcast() -> tuple[set[str], set[str], int, int, list[str]]:
    """扫 src/ 下所有 .py，取两个广播函数的 `{"type": "<x>"}` 字面量。

    返回 (type 全集, 实际见到的 callee 名, 调用点数, 取到字面量的调用点数, 动态 type 的位置)。
    """
    types: set[str] = set()
    callees: set[str] = set()
    calls = 0
    typed = 0
    dynamic: list[str] = []
    for r, dirs, names in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            p = os.path.join(r, n)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                tree = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
            except SyntaxError as e:
                dynamic.append("%s:%s 解析失败：%s" % (rel, e.lineno, e.msg))
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _callee_name(node.func)
                if name not in BROADCAST_CALLEES:
                    continue
                callees.add(name)
                calls += 1
                got = None
                if node.args and isinstance(node.args[0], ast.Dict):
                    for k, v in zip(node.args[0].keys, node.args[0].values):
                        if (isinstance(k, ast.Constant) and k.value == "type"
                                and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                            got = v.value
                if got is None:
                    dynamic.append("%s:%d 广播的 type 不是字面量" % (rel, node.lineno))
                else:
                    typed += 1
                    types.add(got)
    return types, callees, calls, typed, dynamic


# ---------------------------------------------------------------- main
def main() -> int:
    btypes, callees, calls, typed, dynamic = backend_broadcast()

    print("后端广播（%s）：%d 处调用点 / %d 处取到 type 字面量 / %d 种 type"
          % ("+".join(sorted(callees)) or "（一个都没扫到）", calls, typed, len(btypes)))
    if not callees:
        print("✗ 两个广播函数一个都没扫到 —— 收集逻辑坏了，不能当成通过")
        return 2
    lost = [c for c in BROADCAST_CALLEES if c not in callees]
    if lost:
        print("✗ 没扫到这些广播函数：%s（漏一条通道，全集就不可知）" % lost)
        return 2
    if dynamic:
        print("✗ 这些广播点的 type 不是字面量 —— 全集不可知，判据不能在不可知的全集上跑：")
        for d in dynamic:
            print("   " + d)
        return 2
    if calls < MIN_BROADCAST_CALLS or len(btypes) < MIN_BROADCAST_TYPES:
        print("✗ 覆盖变小：调用点 %d（< %d）或 type %d 种（< %d）"
              % (calls, MIN_BROADCAST_CALLS, len(btypes), MIN_BROADCAST_TYPES))
        return 2

    files = scan_web_files()
    wv_types: dict[str, list[str]] = {}     # type → ["文件:行号", ...]
    wv_listeners = 0
    page_internal: dict[str, int] = {}
    for p in files:
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        rel_web = os.path.relpath(p, WEB).replace("\\", "/")
        masked = code_mask(io.open(p, encoding="utf-8", errors="replace").read())
        for at, recv in find_listeners(masked):
            if recv == "window":
                page_internal.setdefault(rel_web, 0)
                page_internal[rel_web] += 1
                continue
            body = listener_body(masked, at)
            if not body:
                print("✗ %s：定位不到 host message 监听器的回调体（花括号配对坏了）" % rel)
                return 2
            if "chrome.webview" not in masked:
                print("✗ %s：有个非 window 的 message 监听器，但文件里没有 chrome.webview"
                      " —— 它既不是页面内部 postMessage 也不是 host 广播，请确认它是什么" % rel)
                return 2
            wv_listeners += 1
            for t in types_in(body):
                wv_types.setdefault(t, []).append(rel)

    print("前端 host message 监听器 %d 个 / 分发 %d 种 type；页面内部 postMessage %d 个文件"
          % (wv_listeners, len(wv_types), len(page_internal)))
    for t in sorted(wv_types):
        print("   %-20s ← %s" % (t, "、".join(sorted(set(wv_types[t])))))

    if wv_listeners < MIN_WEBVIEW_LISTENERS:
        print("✗ 只定位到 %d 个 host message 监听器（< %d）—— 取监听器的逻辑坏了"
              % (wv_listeners, MIN_WEBVIEW_LISTENERS))
        return 2
    if len(wv_types) < MIN_FRONTEND_TYPES:
        print("✗ 只取到 %d 种前端 type（< %d）—— 取 type 的正则坏了"
              % (len(wv_types), MIN_FRONTEND_TYPES))
        return 2

    # 页面内部 postMessage：未登记的要红，登记了但已失效的也要红（催删）
    unknown = sorted(f for f in page_internal if f not in PAGE_INTERNAL)
    if unknown:
        print("✗ 这些文件有 window.addEventListener('message')（页面内部 postMessage）却没登记：")
        for f in unknown:
            print("   %-34s %d 处 —— 确认它确实是页面内部消息后，把原因写进 PAGE_INTERNAL"
                  % (f, page_internal[f]))
        return 1
    stale = sorted(f for f in PAGE_INTERNAL if f not in page_internal)
    if stale:
        print("✗ PAGE_INTERNAL 里这些文件已经没有页面内部 message 监听了，请删掉：%s" % stale)
        return 1
    if len(page_internal) < MIN_PAGE_INTERNAL:
        print("✗ 页面内部 message 监听器只剩 %d 个文件（< %d）—— 扫描范围变小了"
              % (len(page_internal), MIN_PAGE_INTERNAL))
        return 2

    bad = {t: locs for t, locs in wv_types.items() if t not in btypes}
    if bad:
        print("✗ 这些 type 前端有分发分支、后端却从不广播（分支是死的，功能静默失效）：")
        for t, locs in sorted(bad.items()):
            print("   %-20s %s" % (t, "、".join(sorted(set(locs)))))
        return 1

    for t in TOMBSTONE_TYPES:
        if t in btypes:
            print("✗ 后端又在广播已删除的 %s（2026-10-22 删掉的额度浮层死链）" % t)
            return 1
        if t in wv_types:
            print("✗ 前端又出现已删除的 %s 分发分支" % t)
            return 1

    print("✓ 前端 %d 种 type 全部能在后端广播全集里找到（%s）"
          % (len(wv_types), "、".join(sorted(btypes))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
