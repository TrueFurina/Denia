# -*- coding: utf-8 -*-
"""HTML 标签配平检查：抓"漏了闭合标签"，放过"错位嵌套"。

为什么需要它
------------
2026-10-20 发现 `src/ui/web/index.html` 里 `#callOverlay`（通话浮层）**从来没被
关上**——`call-screen` 视图漏了一个 `</div>`，于是单工通话视图变成了它的子节点，
`callOverlay` 自己靠浏览器在 `</body>` 处自动补。表现上"看起来没问题"，实际上
`#callOverlay` 的 hidden 开关会连带控制到单工视图。这种错没有任何运行时症状，
肉眼也看不出来（3600 行的文件），只能靠工具量。

判据为什么用"容错匹配"
----------------------
严格栈式匹配会把"错位嵌套"也报成错：
    `<section><div></section>`  ← div 没关，section 先关了
浏览器对此的恢复是确定的（自动补 div），页面照常工作。真正的风险是**漏标签**
（末尾还挂着没关的元素），所以：
  · 闭合标签优先匹配最近的同名开标签，中间的没关元素一并弹出（= 浏览器行为）
  · 结束时栈里还剩东西 → 报错（这是真的漏了）
  · 闭合标签找不到任何同名开标签 → 报错（多了一个）

用法
----
    python tools/check_html_balance.py            # 扫 src/ui/web 下所有 html
    python tools/check_html_balance.py a.html b.html
退出码：0 = 全部配平；1 = 有文件不配平。
"""
from __future__ import annotations

import os
import sys
from html.parser import HTMLParser

# HTML 规范里的空元素：没有闭合标签
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# 这些顶层容器不参与判断（整页 HTML 片段里经常省略）
IGNORE = {"html", "body", "head"}


class Balance(HTMLParser):
    def __init__(self, path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.stack: list[tuple[str, int]] = []
        self.problems: list[str] = []

    # 自闭合（<path/>、<br/> 等）：既不压栈也不出栈
    def handle_startendtag(self, tag, attrs) -> None:
        return

    def handle_starttag(self, tag, attrs) -> None:
        if tag in VOID or tag in IGNORE:
            return
        self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag) -> None:
        if tag in VOID or tag in IGNORE:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                # 中间的没关元素按浏览器行为一并关闭（错位嵌套，不报）
                del self.stack[i:]
                return
        self.problems.append(f"第 {self.getpos()[0]} 行：多出来的 </{tag}>（前面没有对应的开标签）")

    def report(self) -> list[str]:
        out = list(self.problems)
        for tag, line in self.stack:
            out.append(f"第 {line} 行：<{tag}> 没有闭合")
        return out


def check_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    p = Balance(path)
    try:
        p.feed(text)
        p.close()
    except Exception as e:  # noqa: BLE001
        # 解析器自己炸了也要报——不然"这个文件这次没扫"是看不见的
        return [f"解析失败：{e}"]
    return p.report()


def main() -> int:
    args = sys.argv[1:]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args:
        files = [a if os.path.isabs(a) else os.path.join(root, a) for a in args]
    else:
        base = os.path.join(root, "src", "ui", "web")
        files = []
        for dirpath, _dirs, names in os.walk(base):
            for n in names:
                if n.lower().endswith((".html", ".htm")):
                    files.append(os.path.join(dirpath, n))
        files.sort()

    bad = 0
    skipped: list[str] = []
    checked = 0
    for f in files:
        rel = os.path.relpath(f, root)
        # 设计稿（`_` 前缀）不参与：它们是截图用的临时外壳，不是发出去的页面，
        # 而且历史欠账多（两处末尾缺 div）。**跳过要打印出来**——静默跳过等于
        # 让闸门少扫还不告诉你，这个坑踩过。
        if os.path.basename(f).startswith("_"):
            skipped.append(rel)
            continue
        checked += 1
        probs = check_file(f)
        if probs:
            bad += 1
            print(f"✗ {rel}")
            for x in probs[:6]:
                print(f"    {x}")
            if len(probs) > 6:
                print(f"    …还有 {len(probs) - 6} 处")

    if skipped:
        print(f"（跳过 {len(skipped)} 个设计稿：{', '.join(os.path.basename(s) for s in skipped)}）")
    if bad:
        print(f"\n✗ {bad}/{checked} 个页面标签不配平")
        return 1
    print(f"✓ {checked} 个页面标签全部配平")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
