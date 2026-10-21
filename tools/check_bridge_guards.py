# -*- coding: utf-8 -*-
"""闸门：`typeof Backend.x === 'function'` 这种守卫，x 必须真的在导出表里。

## 为什么要单独一条

`check_bridge_methods` 守的是**调用**（`B.foo(...)` / `call('foo')`），而下面这种形态
它看不见 —— 因为它长得像"能力探测"，不像"调用"：

```js
if (typeof Backend.probeVisionModel === 'function') {
  Backend.probeVisionModel(mid).then(done);   // ← 永远不会执行
} else {
  btn.disabled = false; btn.textContent = '再试一次';   // ← 静默恢复，什么都不做
}
```

这种守卫的本意是"这个版本可能没有这个能力，有就用"，但它把**两种完全不同的情况**压成
了一种：①这个版本确实没有；②**名字写错了 / 适配层漏导出**。第 ② 种才是实际发生的
（2026-10-21 查实：`probeVisionModel` 后端一直在、适配层从没导出 → 额度提醒浮层的
「再试一次」点了只是把按钮恢复成「再试一次」，不发请求也不提示）。

同型的还有 `switchVisionModel`（已修，注释留在 `app_backend.js` 的导出段）和
`hasattr(cfg,'refresh_characters_map')`（Python 版，见 CHANGELOG 2026-10-21）。规律：
**守卫本身没错，错的是"守卫的目标不在导出表里"这件事没人查**。

## 判据

1. 从 `app_backend.js` 的 `return { … }` 解析出导出名集合（复用 `check_bridge_methods.
   exported()` —— 内联与外链共用同一份解析，别再写第二套）。
2. 扫全部真页面/真 JS 里的三种"守卫"形态，取 `<name>`：
   - `typeof (window.)?(Backend|B).<name>`（能力探测）
   - `if ((window.)?(Backend|B).<name>`（存在性判断，含 `if (B.x && …)`）
   - `!(window.)?(Backend|B).<name>`（否定式降级）
3. `<name>` 不在导出集合 → 红，报 `文件:行号 名字`。

**为什么 `if (B.x)` 也要查**：2026-10-21 删掉的那段平行绑定就是 `if (B.setArchetypeManual)`
—— 守卫恒 false，于是后端没调却 toast「关系已重置」，**谎报**。同一天 `probeVisionModel`
是同一种病的 typeof 版。实测这几种形态在真实仓库上误报为 0（唯一命中是我自己写的注释，
而注释会被剥掉），所以收进来。

用法：python tools/check_bridge_guards.py
退出码：0 = 全对得上；1 = 有守卫指向不存在的导出；2 = 闸门自己失效（解析不到 / 扫不到）。
"""
from __future__ import annotations

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_bridge_methods import (  # noqa: E402  共用"什么是真页面/真 JS"的判据
    exported,
    is_draft_html,
    is_draft_js,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "src", "ui", "web")
ADAPTER = os.path.join(WEB, "redesign", "app_backend.js")

# 守卫名 → 为什么可以不在导出表里。**必须写原因**，且为空才是常态。
KNOWN_GUARD_MISSING: dict[str, str] = {}

# 这几个文件必须在扫描结果里出现，否则说明"扫描范围悄悄变小了"（改名/搬家都会）。
MUST_SCAN = ["redesign/app_backend.js", "index.html"]

_TARGET = r"(?:window\.)?(?:Backend|B)\s*\.\s*([A-Za-z_$][\w$]*)"
_GUARD = re.compile(
    r"typeof\s+" + _TARGET          # ① 能力探测
    + r"|if\s*\(\s*" + _TARGET      # ② if (B.x) / if (B.x && …)
    + r"|!\s*" + _TARGET            # ③ !B.x
)

MIN_EXPORTS = 100      # 导出表解析出来不足这么多 → 解析坏了
MIN_GUARDS = 100       # 全站守卫不足这么多 → 正则坏了（现在 220 处）


def _strip_comments(txt: str) -> str:
    """剥掉注释（保留行数，行号才对得上）。

    **行尾注释也要剥**（2026-10-21，被变异刀 M4 抓出来的）：只剥"整行以 // 开头"的话，
    `<script>// typeof Backend.x === 'function'` 这种跟在标签后面的注释会被当成真守卫 →
    误报。判据是 `//` 前面不是 `:`（放过 `http://`）也不是标识符字符（放过 `a//b` 这种
    少见写法）；字符串里的 ` // ` 会被误伤，但代价只是**漏报**，可接受。
    """
    txt = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), txt, flags=re.S)
    txt = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), txt, flags=re.S)
    out = []
    for line in txt.splitlines():
        out.append(re.sub(r"(?<![:\w])//.*$", "", line))
    return "\n".join(out)


def scan_files() -> list[str]:
    out = []
    for r, dirs, names in os.walk(WEB):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            if n.endswith(".js") and not is_draft_js(n):
                out.append(os.path.join(r, n))
            elif n.endswith(".html") and not is_draft_html(n):
                out.append(os.path.join(r, n))
    return sorted(out)


def guards_in(path: str) -> list[tuple[int, str]]:
    txt = _strip_comments(io.open(path, encoding="utf-8", errors="replace").read())
    out = []
    for i, line in enumerate(txt.splitlines(), 1):
        for m in _GUARD.finditer(line):
            name = m.group(1) or m.group(2) or m.group(3)
            if name:
                out.append((i, name))
    return out


def main() -> int:
    if not os.path.isfile(ADAPTER):
        print("✗ 找不到适配层：%s" % ADAPTER)
        return 2
    names, _mapping, _helpers = exported(io.open(ADAPTER, encoding="utf-8", errors="replace").read())
    if len(names) < MIN_EXPORTS:
        print("✗ 导出表只解析出 %d 个名字（< %d）—— 解析逻辑坏了，不能当成通过"
              % (len(names), MIN_EXPORTS))
        return 2

    files = scan_files()
    seen_pages = {os.path.relpath(p, WEB).replace("\\", "/") for p in files}
    lost = [f for f in MUST_SCAN if f not in seen_pages]
    if lost:
        print("✗ 扫描范围里缺了 %s —— 覆盖悄悄变小了" % lost)
        return 2

    total = 0
    distinct: set[str] = set()
    missing: dict[str, list[str]] = {}
    for p in files:
        for ln, name in guards_in(p):
            total += 1
            distinct.add(name)
            if name in names or name in KNOWN_GUARD_MISSING:
                continue
            missing.setdefault(name, []).append(
                "%s:%d" % (os.path.relpath(p, ROOT).replace("\\", "/"), ln))

    if total < MIN_GUARDS:
        print("✗ 全站只扫到 %d 处守卫（< %d）—— 正则坏了" % (total, MIN_GUARDS))
        return 2

    print("扫 %d 个文件 / %d 处守卫（typeof + if + !）/ %d 个不同名字；导出表 %d 个名字"
          % (len(files), total, len(distinct), len(names)))

    stale = [n for n in KNOWN_GUARD_MISSING if n not in missing]
    if stale:
        print("✗ 白名单里这些已经不缺了，请删掉：%s" % ", ".join(sorted(stale)))
        return 1

    if missing:
        print("✗ 这些守卫指向**导出表里不存在**的名字（点了会静默什么都不做）：")
        for n, locs in sorted(missing.items()):
            print("   %-28s %s" % (n, "、".join(locs[:4])))
        return 1

    print("✓ 所有守卫（typeof / if / !）的目标都在导出表里")
    return 0


if __name__ == "__main__":
    sys.exit(main())
