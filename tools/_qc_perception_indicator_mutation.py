# -*- coding: utf-8 -*-
"""「感知指示器」的四把变异刀（验证 test_perception_indicator.py 真能抓错）。

背景：该测试 docstring 提到「变异刀3」，说明开发期有刀但**从未提交**——按本仓铁律
「加门必须同时加变异」，没有变异刀的门只是装饰品。本脚本补齐这把缺口。

四刀各打一条"不许骗人"的判据：
  M1 后端 watching 写死 True（不看真实开关）   → [2]「看屏关着 → watching=False」必须红
  M2 后端 privacy 恒 False（抹掉隐私维度）      → [3]「隐私开着 → privacy=True」必须红
  M3 rig 静态标签预置 class="on"（永远亮）       → [6]「静态标签不带 class」必须红
  M4 rig 把 toggle('on', on) 改成无条件 add      → [6]「由条件决定」必须红

跨两个文件：`src/ui/webapp.py`（M1/M2）、`src/ui/web/live2d/rig/index.html`（M3/M4）。
锚点均为**单行 ASCII**（不受 EOL 影响）；改源码用二进制 `io.open(p,'wb')`，还原亦二进制。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "src", "tests", "test_perception_indicator.py")
WEBAPP = os.path.join("src", "ui", "webapp.py")
RIG = os.path.join("src", "ui", "web", "live2d", "rig", "index.html")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


MUTS = [
    (
        "M1 后端 watching 写死 True",
        WEBAPP,
        b'            out["watching"] = bool(cap.enabled)',
        b'            out["watching"] = True',
        "不看真实开关，点永远亮 = 骗人",
    ),
    (
        "M2 后端 privacy 恒 False",
        WEBAPP,
        b'            out["privacy"] = bool(privacy_mode())',
        b'            out["privacy"] = False',
        "抹掉隐私维度，隐私模式下也画不出琥珀点",
    ),
    (
        "M3 rig 静态标签预置 class=\"on\"",
        RIG,
        b'<div id="eyeDot" aria-hidden="true"></div>',
        b'<div id="eyeDot" class="on" aria-hidden="true"></div>',
        "静态就带 on = 永远亮，与真实状态脱钩",
    ),
    (
        "M4 rig toggle('on', on) 改无条件 add",
        RIG,
        b"      dot.classList.toggle('on', on);",
        b"      dot.classList.add('on');",
        "无条件亮 = 不看 watching 条件",
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
    for label, rel, old, new, why in MUTS:
        data = _read(rel)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过 [%s]" % (label, n, rel))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, out = run_test()
        finally:
            _write(rel, data)
        if code == 0:
            print("  FAIL %s：测试仍然全绿（%s 没被抓住）" % (label, why))
            bad += 1
        else:
            fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")]
            print("  PASS %s：变红 %d 项  %s" % (label, len(fails), fails[0] if fails else ""))

    code, out = run_test()
    if code != 0:
        print("  FAIL 还原后测试仍失败（源文件没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后全绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
