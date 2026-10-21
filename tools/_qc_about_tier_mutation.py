# -*- coding: utf-8 -*-
"""「关于」分类的三把变异刀（验证 tools/test_about_tier.js 真的能抓错）。

本仓规矩：**加门必须同时加变异**，否则那道门只是装饰品。这个脚本自己也在闸门里
（`tools/check_all.py` 的「关于页变异」，标慢项），所以必须**快且自还原**。

三刀各打一条"不许假装成功"的判据：
  M1 去掉「开发环境 → 不调后端」的守卫   → S2「点了不调后端」必须红
  M2 把三态压成二态（问不出来也当"不支持"）→ S3「不许说成开发环境」必须红
  M3 写入失败时不把开关翻回去            → S5「开关翻回关」必须红

改的是 `ui_redesign_settings.html`（CRLF），JS 测试从页面里**原样抽** IIFE，
所以改页面 = 改被测对象，不需要同步改测试。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tools", "test_about_tier.py")
PAGE = os.path.join("src", "ui", "web", "redesign", "ui_redesign_settings.html")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _block(lines: list[str]) -> bytes:
    data = _read(PAGE)
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    return eol.join(ln.encode("utf-8") for ln in lines) + eol


MUTS = [
    (
        "M1 去掉「开发环境不调后端」的守卫",
        lambda: _block(["        if (SUP === false){"]),
        lambda: _block(["        if (false){"]),
        "开发环境点一下就去写注册表（用户以为开了，其实什么也没发生）",
    ),
    (
        "M2 把三态压成二态",
        lambda: _block(["        if (!r || r.ok !== true){"]),
        lambda: _block(["        if (false){"]),
        "后端没连上时报「开发环境」——那是假话，用户在安装版里只是后端没起来",
    ),
    (
        "M3 写入失败时不把开关翻回去",
        lambda: _block([
            "            paint(!next);",
            "            if (typeof toast === 'function') toast('设置失败：'"
            " + ((r && r.error) || '未知原因'), 'err');",
        ]),
        lambda: _block([
            "            if (typeof toast === 'function') toast('设置失败：'"
            " + ((r && r.error) || '未知原因'), 'err');",
        ]),
        "后端拒绝了，开关还亮着 —— 谎报成功",
    ),
]


def run_test() -> tuple[int, str]:
    p = subprocess.run([PY, TEST], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    code, out = run_test()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-800:])
        return 1
    print("  PASS 起点绿")

    bad = 0
    for label, mk_old, mk_new, why in MUTS:
        data = _read(PAGE)
        old, new = mk_old(), mk_new()
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(PAGE, data.replace(old, new))
        try:
            code, out = run_test()
        finally:
            _write(PAGE, data)
        if code == 0:
            print("  FAIL %s：测试仍然全绿（%s 没被抓住）" % (label, why))
            bad += 1
        else:
            fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAIL")]
            print("  PASS %s：变红 %d 项  %s" % (label, len(fails), fails[0] if fails else ""))

    code, out = run_test()
    if code != 0:
        print("  FAIL 还原后测试仍失败（页面没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后 20 项全绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
