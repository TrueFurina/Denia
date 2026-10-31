# -*- coding: utf-8 -*-
"""「主动搭话去重/冷却」测试的三把变异刀（验证 test_proactive_dedup_cooldown.py 真能抓错）。

本仓规矩：**加门必须同时加变异**，否则那道门只是装饰品。本脚本自身也在闸门里
（`tools/check_all.py` 的「主动搭话去重变异」，标慢项），故必须快且自还原。

三刀各打一条"不许假装成功"的判据：
  M1 `_similar_summary` 的等值/子串判定恒假  → [A] 子串/相同判 True 必须红
  M2 `_screen_activity_repeat` 恒返回 None    → [B] 去重命中 必须红
  M3 `_last_proactive_or_zero` 对 None 返回 None（重演旧 bug）→ [C] None→0.0 必须红

改的是 `src/companion/proactive.py`（**单行 ASCII 锚点**，不受 EOL 影响）；改源码用
二进制模式 `io.open(p,'wb')`，还原亦用二进制，避免文本模式把 `\\n` 洗成 `\\r\\n`。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "src", "tests", "test_proactive_dedup_cooldown.py")
SRC = os.path.join("src", "companion", "proactive.py")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


MUTS = [
    (
        "M1 _similar_summary 等值/子串判定恒假",
        b"    if na == nb or na in nb or nb in na:",
        b"    if False:",
        "子串摘要不再被判重复 → 输入轨迹会堆冗余",
    ),
    (
        "M2 _screen_activity_repeat 恒返回 None",
        b"        return b if _similar_summary(s, summary) else None",
        b"        return None",
        "同窗口停留每几秒重复 append → 轨迹变『在游戏；在游戏』",
    ),
    (
        "M3 _last_proactive_or_zero 对 None 返回 None（重演旧 bug）",
        b"            return float(v) if v is not None else 0.0",
        b"            return float(v) if v is not None else None",
        "None 相减抛 TypeError 被 DEBUG 吞 → 主动搭话永不触发",
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
    for label, old, new, why in MUTS:
        data = _read(SRC)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(SRC, data.replace(old, new))
        try:
            code, out = run_test()
        finally:
            _write(SRC, data)
        if code == 0:
            print("  FAIL %s：测试仍然全绿（%s 没被抓住）" % (label, why))
            bad += 1
        else:
            fails = [ln.strip() for ln in out.splitlines() if "FAIL" in ln]
            print("  PASS %s：变红 %d 项  %s" % (label, len(fails), fails[0] if fails else ""))

    code, out = run_test()
    if code != 0:
        print("  FAIL 还原后测试仍失败（源文件没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后 26 项全绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
