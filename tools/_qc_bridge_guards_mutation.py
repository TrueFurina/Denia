# -*- coding: utf-8 -*-
"""`tools/check_bridge_guards.py` 的变异刀（验证那道闸门有牙齿）。

规矩：**加门必须同时加变异**。这道闸门守的是"`typeof Backend.x === 'function'` 的 x
必须在导出表里"，它自己的失效方式有两种，都必须被这里抓住：

  M1 导出少了（今天这个 bug 的原样）→ 必须红
     锚点：删掉导出表里的 `probeVisionModel,`
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

改源码一律按**字节**读写，锚点按各文件实际换行符拼（`_encode`）：文本模式在 Windows
上会把 `\\n` 写成 `\\r\\n`，锚点静默失效。
"""

from __future__ import annotations

import io
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
    ("M1 导出表里少了 probeVisionModel（今天的 bug 原样）",
     ADAPTER,
     "    switchVisionModel,\n    probeVisionModel,\n",
     "    switchVisionModel,\n",
     1, "probeVisionModel"),
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


def main() -> int:
    bad = 0
    code, out = run_gate()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-600:])
        return 1
    print("  PASS 起点绿")

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
    else:
        print("  PASS 还原后仍然绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
