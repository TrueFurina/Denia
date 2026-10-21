# -*- coding: utf-8 -*-
"""「导入冒烟·串行复核」自测的变异刀（验证 tools/test_smoke_import_retry.py 有牙齿）。

本仓规矩：**加门必须同时加变异**。这个脚本自己也在闸门里（`tools/check_all.py` 的
「导入冒烟复核变异」，标慢项），所以必须**快且自还原**。

三刀，打的是"复核"这件事最危险的三个方向 —— 前两刀会让闸门**变得更绿**：

  M1 复核范围放宽（什么错都重试）→ 真缺陷被重试掩盖
     锚点：`is_mem_contention` 直接 `return True`
     必须红：「real **没有**复核标记」（real 被复核了）
  M2 复核一律判过（复核完就当成功）→ 真失败被洗成通过
     锚点：`if again["status"] == "OK":` → `if True:`
     必须红：「memfail 仍然 FAIL」
  M3 复核后不重算白名单计数（判决对、数字错）
     锚点：删掉 `known = [r for r in fails if r["module"] in allow]` 这一行
     必须红：「白名单内」是 0（不是复核前算的 1）

坑：改源码按**字节**读写（文本模式在 Windows 上会把 `\\n` 写成 `\\r\\n`，锚点全失效）。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tools", "test_smoke_import_retry.py")
TOOL = os.path.join("tools", "smoke_import.py")

MUTS = [
    ("M1 复核范围放宽（什么错都重试）",
     '    return _MEM_FAIL_MARK in ((result.get("detail") or "") + (result.get("trace") or ""))',
     '    return True',
     "real **没有**复核标记"),
    ("M2 复核一律判过",
     '            if again["status"] == "OK":',
     '            if True:',
     # 打中的是"非预期失败计数"这条：memfail 串行复核也失败，被 `if True` 当成救回来了
     # → 它从"非预期失败"里消失（计数 2 → 1）。这是最危险的方向：**真失败被洗出闸门的
     # 判决**，而 JSON 里它的 status 还是 FAIL —— 只看单条结果根本发现不了。
     "非预期失败 2"),
    ("M3 known 不重算（白名单计数停在复核前）",
     '    fails = [r for r in results if r["status"] != "OK"]\n'
     '    known = [r for r in fails if r["module"] in allow]',
     '    fails = [r for r in results if r["status"] != "OK"]',
     # 打中的是"数字对不上"：flaky 在白名单里、又被复核救回，known 不重算就会印
     # 「白名单内 1 条」而实际 0 条。它不改变判决（退出码照旧），所以**比 M2 更隐蔽**
     # —— 判决对、数字错，人看一眼汇总就信了。
     "「白名单内」是 0（不是复核前算的 1）"),
]


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _encode(rel: str, text: str) -> bytes:
    eol = b"\r\n" if b"\r\n" in _read(rel) else b"\n"
    return text.replace("\n", eol.decode()).encode("utf-8")


def run_test() -> tuple[int, str]:
    p = subprocess.run([PY, TEST], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    bad = 0
    code, out = run_test()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-800:])
        return 1
    print("  PASS 起点绿")

    for label, old_txt, new_txt, want in MUTS:
        data = _read(TOOL)
        old, new = _encode(TOOL, old_txt), _encode(TOOL, new_txt)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(TOOL, data.replace(old, new))
        try:
            code, out = run_test()
        finally:
            _write(TOOL, data)
        if code == 0:
            print("  FAIL %s：自测仍然全绿（这条判据没牙齿）" % label)
            bad += 1
        elif ("FAIL " + want) not in out:
            print("  FAIL %s：红了但不是因为「%s」" % (label, want))
            print(out[-800:])
            bad += 1
        else:
            print("  PASS %s：红了「%s」" % (label, want))

    code, out = run_test()
    if code != 0:
        print("  FAIL 还原后自测仍红（源码没恢复干净）")
        print(out[-800:])
        bad += 1
    else:
        print("  PASS 还原后仍然绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
