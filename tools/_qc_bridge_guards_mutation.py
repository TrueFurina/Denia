# -*- coding: utf-8 -*-
"""`tools/check_bridge_guards.py` 的变异刀（验证那道闸门有牙齿）。

规矩：**加门必须同时加变异**。这道闸门守的是"`typeof Backend.x === 'function'` 的 x
必须在导出表里"，它自己的失效方式有两种，都必须被这里抓住：

  M1 导出少了 → 必须红
     **目标动态取**（2026-10-22 改）：原来写死删 `probeVisionModel`，而那个 bug
     修好后页面里已经没有守卫指向它 → 删掉导出闸门仍然绿，这一刀悄悄失效。
     现在按被测程序自己的口径取交集：`guards_in()` 的守卫名 × `exported()` 的
     导出表。与 `_qc_api_wiring_mutation` 的 M3 同坑：**刀不许假设清单长什么样**。
  M2 页面里出现一个不存在的守卫 → 必须红
     锚点：notify.html 的 `</body>` 前插一行 `typeof Backend.zzzNotARealBridge`
  M3 **正则坏了**（扫不到守卫）→ 必须 exit 2，不许当成通过
     锚点：`_GUARD` 换成永不匹配的模式
     这是闸门最阴的失效：0 命中 ⇒ 没有违规 ⇒ 打印"✓"退出 0。所以闸门里有
     `MIN_GUARDS` 兜底，这一刀就是验证那个兜底真的在。
  M4 **对照组**：注释里复述守卫名 → 必须仍然绿
     锚点：notify.html 插一行 `// typeof Backend.zzzInComment === 'function'`
     没有这一刀，M2 的"红"可能只是因为闸门根本不剥注释（那样任何注释里提到守卫名
     都会误报，下一个人会去放宽判据）。
  M5 `if (B.x)` 形态的守卫也要查（10-21 删掉的那段平行绑定就是它：守卫恒 false
     → 后端没调却 toast「关系已重置」，**谎报**）
     锚点：notify.html 插 `if (B.zzzIfGuard) { }`
  M6 **跨行块注释不许被当成真守卫**（2026-10-22 补）→ 必须仍然绿**且守卫总数不变**
     锚点：app_backend.js 插一个跨行 `/* ... */`，内含 `https://` 与守卫名
     守的是 `_strip_comments` 的**顺序**（先块后行）：块注释里只要含 `//`，先剥
     行注释就会把该行的 `*/` 一起删掉 → 块注释只剩 `/*` → 后续匹配错位、**吃掉
     大段真实代码**。M4 抓不到它（行注释在两种顺序下都被剥掉、都绿），所以 M6
     额外断言守卫总数不变——顺序颠倒时闸门确实还绿，但真实守卫被吃掉、总数会少。

改源码一律按**字节**读写，锚点按各文件实际换行符拼（`_encode`）：文本模式在 Windows
上会把 `\\n` 写成 `\\r\\n`，锚点静默失效。
"""

from __future__ import annotations

import io
import re
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "check_bridge_guards.py")
ADAPTER = os.path.join("src", "ui", "web", "redesign", "app_backend.js")
PAGE = os.path.join("src", "ui", "web", "notify.html")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _encode(rel: str, text: str) -> bytes:
    eol = b"\r\n" if b"\r\n" in _read(rel) else b"\n"
    return text.replace("\n", eol.decode()).encode("utf-8")


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, GATE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# (标签, 文件, 原文, 改后, 期望退出码, 期望出现的字串)
KNIVES = [
    ("M2 页面里出现不存在的守卫",
     PAGE,
     "</body>\n</html>",
     "<script>if (typeof Backend.zzzNotARealBridge === 'function') { /* x */ }</script>\n</body>\n</html>",
     1, "zzzNotARealBridge"),
    ("M3 守卫正则坏了（0 命中不许当成通过）",
     GATE,
     '_TARGET = r"(?:window\\.)?(?:Backend|B)\\s*\\.\\s*([A-Za-z_$][\\w$]*)"',
     '_TARGET = r"__never_matches_this__\\s*\\.\\s*([A-Za-z_$][\\w$]*)"',
     2, "正则坏了"),
    ("M4 对照组：注释里复述守卫名 → 仍然绿",
     PAGE,
     "</body>\n</html>",
     "<script>// typeof Backend.zzzInComment === 'function' 这行是注释，不该被算\n"
     "</script>\n</body>\n</html>",
     0, "✓"),
    ("M5 if (B.x) 形态的守卫也要查",
     PAGE,
     "</body>\n</html>",
     "<script>if (B.zzzIfGuard) { /* 后端没调却往下走 */ }</script>\n</body>\n</html>",
     1, "zzzIfGuard"),
]


def _pick_guarded_export() -> tuple[str, bytes] | None:
    """动态取一个**既有守卫用它、又在导出表里**的名字来删。

    【为什么动态取】M1 原来写死 `probeVisionModel`——那是 10-21 当天的 bug 原样。
    但 bug 修好后它的守卫形态可能已经不在页面里了（现在删掉导出，闸门仍然绿：
    没有任何守卫指向它）。于是这一刀从"能抓错"变成"永远绿"。

    与 `_qc_api_wiring_mutation` 的 M3 是同一个坑：**变异刀不许假设"当前清单长
    什么样"**。所以这里按被测程序自己的口径取交集：`guards_in()` 扫出来的守卫名
    × `exported()` 解析出来的导出表。前端怎么变，这一刀都成立。
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import check_bridge_guards as cbg  # noqa: E402

    guard_names: set[str] = set()
    for p in cbg.scan_files():
        for _ln, n in cbg.guards_in(p):
            guard_names.add(n)
    with io.open(os.path.join(ROOT, ADAPTER), encoding="utf-8", errors="replace") as f:
        names, _mapping, _helpers = cbg.exported(f.read())
    cands = sorted((guard_names & set(names)) - set(cbg.KNOWN_GUARD_MISSING))
    data = _read(ADAPTER)
    for n in cands:
        needle = _encode(ADAPTER, "    %s,\n" % n)
        if data.count(needle) == 1:
            return n, needle
    return None


def main() -> int:
    bad = 0
    code, out = run_gate()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-600:])
        return 1
    print("  PASS 起点绿")

    # ---------------- M1：删掉某个"有守卫在用它"的导出（目标动态取）--------
    picked = _pick_guarded_export()
    if not picked:
        print("  FAIL M1：找不到可删的导出（守卫名 × 导出表交集为空）—— 刀失效了")
        bad += 1
    else:
        name, needle = picked
        data = _read(ADAPTER)
        _write(ADAPTER, data.replace(needle, b""))
        try:
            code, out = run_gate()
        finally:
            _write(ADAPTER, data)
        if code == 0:
            print("  FAIL M1 删掉导出 %s：闸门仍绿（守卫查不出漏导出）" % name)
            bad += 1
        elif name not in out:
            print("  FAIL M1：红了但没点名 %s" % name)
            print(out[-600:])
            bad += 1
        else:
            print("  PASS M1 删掉导出 %s：闸门红了并点名" % name)

    for label, rel, old_txt, new_txt, want_code, want_str in KNIVES:
        data = _read(rel)
        old, new = _encode(rel, old_txt), _encode(rel, new_txt)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, out = run_gate()
        finally:
            _write(rel, data)
        if code != want_code:
            print("  FAIL %s：退出码 %d（期望 %d）" % (label, code, want_code))
            print(out[-600:])
            bad += 1
        elif want_str not in out:
            print("  FAIL %s：结果里没有「%s」" % (label, want_str))
            print(out[-600:])
            bad += 1
        else:
            print("  PASS %s：退出码 %d，结果含「%s」" % (label, code, want_str))

    code, out = run_gate()
    if code != 0:
        print("  FAIL 还原后闸门仍红（源码没恢复干净）")
        print(out[-600:])
        bad += 1

    # ---------------- M6：跨行块注释不许被当成真守卫（顺序 bug 的判据）--------
    # 2026-10-22 补。`_strip_comments` 是**先剥块注释、再剥行注释**——这个顺序
    # 是必须的：块注释里只要含 `//`（URL 最常见），先剥行注释就会把该行的 `*/`
    # 一起删掉 → 块注释只剩 /* → 后续 /*...*/ 匹配错位，**吃掉大段真实代码**。
    #
    # 这个坑本轮在 test_quota_alert 的判据上真实踩到：把 quota_alert 分支加回
    # app_backend.js，判据却仍然绿——插入点前一个块注释里含 URL，剥离后连插入
    # 行一起被吃掉了。
    #
    # M4 抓不到它：M4 插的是行注释，两种顺序下都会被剥掉、都是绿。所以 M6 除
    # "必须绿"之外还断言**守卫总数不变**——顺序颠倒时闸门确实还绿，但真实守卫
    # 被吃掉、总数会变少。（覆盖数必须打印出来看，否则"绿"分不清是真绿还是漏扫。）
    base_guard_n = None
    m = re.search(r"(\d+) 处守卫", out)
    if m:
        base_guard_n = int(m.group(1))
    block = (
        "/* 对照组：跨行块注释里提守卫名不算违规（M6）\n"
        "   typeof Backend.zzzInBlockComment === 'function'\n"
        "   参考 https://developer.mozilla.org/ 这一行的 */ 会被先剥行注释的正则一起吃掉\n"
        "   那就说明顺序被调换了 */\n"
    )
    data = _read(ADAPTER)
    old = _encode(ADAPTER, "    switchVisionModel,\n")
    new = _encode(ADAPTER, block) + _encode(ADAPTER, "    switchVisionModel,\n")
    if data.count(old) != 1 or base_guard_n is None:
        print("  FAIL M6：锚点命中 %d 次 / 基线守卫数 %r" % (data.count(old), base_guard_n))
        bad += 1
    else:
        _write(ADAPTER, data.replace(old, new))
        try:
            code, out = run_gate()
        finally:
            _write(ADAPTER, data)
        m2 = re.search(r"(\d+) 处守卫", out)
        n2 = int(m2.group(1)) if m2 else None
        if code != 0:
            print("  FAIL M6 跨行块注释：闸门红了（跨行注释被当成真守卫）")
            print(out[-600:])
            bad += 1
        elif n2 != base_guard_n:
            print("  FAIL M6 跨行块注释：守卫总数 %d → %d（剥注释吃掉了真实守卫）"
                  % (base_guard_n, n2))
            bad += 1
        else:
            print("  PASS M6 跨行块注释：仍绿，守卫总数不变（%d 处）" % base_guard_n)

    # M6 也改文件，单独确认字节级还原
    if _read(ADAPTER) != data:
        print("  FAIL M6 之后 ADAPTER 没有字节级还原！")
        bad += 1
    else:
        print("  PASS 还原后仍然绿且字节一致")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
