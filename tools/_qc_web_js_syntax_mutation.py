# -*- coding: utf-8 -*-
"""「全站 JS 语法」这道闸门的变异刀（验证 tools/check_web_js_syntax.py 真的在查）。

本仓规矩：**加门必须同时加变异**，否则那道门只是装饰品。这个脚本自己也在闸门里
（`tools/check_all.py` 的「前端 JS 语法变异」，标慢项），所以必须**快且自还原**。

三刀，各打一个不同的覆盖面（只打同一个等于只验了一种载体）：
  M1 非首页的内联块塞语法错误（notify.html）   → 证明"不只看 index.html"
  M2 原硬编码名单外的外链 JS 塞语法错误        → 证明"不只查 app_backend.js / spa.js"
  M3 index.html 的**非第一块**塞语法错误       → 证明"不是只查第一块就 break"

三个坑：
  · 改源码按**字节**读、按 utf-8 解码改、再按字节写回 —— 用 `io.open(p,"w")` 会在
    Windows 上把 `\\n` 写成 `\\r\\n`，整文件换行符被改，后续按行锚点全失效。
  · 断言要**点名文件**，不能只看退出码：任意一处语法错误都退出 1，
    只判退出码的话，M1 实际打在别的地方也照样"通过"。
  · 块序号**动态算**，不写死数字：`index.html` 的内联块数会变，写死就是给自己
    埋一个"还原后也绿"的假绿（`_qc_api_wiring_mutation` 的 M2 踩过）。
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join(ROOT, "tools", "check_web_js_syntax.py")

NOTIFY = "src/ui/web/notify.html"
INDEX = "src/ui/web/index.html"
EXT_JS = "src/ui/web/onboarding_role.js"   # 原硬编码名单（app_backend.js / spa.js）之外

BROKEN = "var qc = ;\n"


def _load(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read().decode("utf-8")


def _save(rel: str, text: str) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(text.encode("utf-8"))


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, GATE], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _mutate_inline(rel: str, which: int) -> tuple[str, str, int]:
    """在 `rel` 的第 `which` 个内联块开头塞一段语法错误。

    返回 (原文, 改后文本, 期望被点名的块序号)。
    """
    text = _load(rel)
    blocks = [m for m in re.finditer(r"<script([^>]*)>(.*?)</script>", text, re.S | re.I)
              if not re.search(r"\bsrc\s*=", m.group(1), re.I)]
    if not blocks:
        raise SystemExit("  FAIL %s 里没有内联块，这一刀打不中" % rel)
    idx = which if which >= 0 else len(blocks) + which
    m = blocks[idx]
    body_at = m.start(2)
    return text, text[:body_at] + BROKEN + text[body_at:], idx


def main() -> int:
    bad = 0
    code, out = run_gate()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-800:])
        return 1
    print("  PASS 起点绿")

    # M1 / M3：内联块（不同页面、不同块位）
    for label, rel, which in [("M1 非首页的内联块", NOTIFY, 0),
                              ("M3 首页的非第一块", INDEX, -1)]:
        old, new, idx = _mutate_inline(rel, which)
        if new == old:
            print("  FAIL %s：替换没生效（块体是空的？）" % label)
            bad += 1
            continue
        _save(rel, new)
        try:
            code, out = run_gate()
        finally:
            _save(rel, old)
        name = "%s 内联块#%d" % (rel, idx)
        if code == 0:
            print("  FAIL %s：闸门仍然绿（%s 没被查）" % (label, name))
            bad += 1
        elif name not in out:
            print("  FAIL %s：退出码对了但没点名 %s（可能是别处凑数）" % (label, name))
            print(out[-800:])
            bad += 1
        elif idx > 0 and "块#0" in out:
            print("  FAIL %s：报成了第一块（只查了第一块就 break？）" % label)
            bad += 1
        else:
            print("  PASS %s：报了 %s" % (label, name))

    # M2：外链 JS（追加到文件尾，不需要锚点）
    old = _load(EXT_JS)
    _save(EXT_JS, old + "\n" + BROKEN)
    try:
        code, out = run_gate()
    finally:
        _save(EXT_JS, old)
    if code == 0:
        print("  FAIL M2 外链 JS：闸门仍然绿（%s 没被查）" % EXT_JS)
        bad += 1
    elif EXT_JS not in out:
        print("  FAIL M2：退出码对了但没点名 %s" % EXT_JS)
        print(out[-800:])
        bad += 1
    else:
        print("  PASS M2 外链 JS：报了 %s" % EXT_JS)

    code, out = run_gate()
    if code != 0:
        print("  FAIL 还原后闸门仍红（源码没恢复干净）")
        print(out[-800:])
        bad += 1
    else:
        print("  PASS 还原后仍然绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
