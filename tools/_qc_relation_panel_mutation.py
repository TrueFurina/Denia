# -*- coding: utf-8 -*-
"""`tools/test_relation_panel.js` 的变异刀（验证那 24 条判据有牙齿）。

规矩：**加门必须同时加变异**。四刀，每刀打一个"会让面板悄悄退化"的方向：

  M1 调错后端方法名（set_archetype_manual → set_archetype_manual2）
     必须红：「调了一次 set_archetype_manual」
     —— 这就是本仓反复出现的"名字写错、被守卫/可选链吞掉"。
  M2 下拉改成写死枚举（`var list = ['黏人','傲娇','温柔']`）
     必须红：「**没有**塞进一串写死的枚举」
     —— 本仓吃过 MOODS 白名单的亏：前端抄一份清单，后端加一个就永远看不到。
  M3 事件行走 innerHTML 而不是 textContent
     必须红：「**没有**往 innerHTML 里写」
  M4 不再拉事件（删掉 `if (a.get_relation_events)` 那段）
     必须红：「事件列表渲染 3 行」

改源码一律按字节读写 + 按各文件实际换行符拼锚点（`_encode`）。锚点命中 0 次**判红**，
不是跳过 —— 缩进/换行一变，锚点就失效，静默跳过等于这把刀不存在。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join("tools", "test_relation_panel.py")
JS = os.path.join("src", "ui", "web", "redesign", "relation_panel.js")


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
    p = subprocess.run([PY, TEST], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


KNIVES = [
    ("M1 调错后端方法名",
     "          if (a && a.set_archetype_manual) await a.set_archetype_manual('', v);",
     "          if (a && a.set_archetype_manual) await a.set_archetype_manualX('', v);",
     "调了一次 set_archetype_manual"),
    ("M2 下拉改成写死枚举",
     "    var list = (state && state.archetypes) || [];",
     "    var list = ['黏人', '傲娇', '温柔'];",
     "**没有**塞进一串写死的枚举"),
    ("M3 事件行走 innerHTML",
     "      row.textContent = line;            /* textContent：detail 里可能有尖括号，不走 innerHTML */",
     "      row.innerHTML = line;",
     "**没有**往 innerHTML 里写"),
    ("M4 不再拉事件列表",
     "      if (a.get_relation_events) {\n"
     "        renderEvents(await a.get_relation_events('', EVENT_LIMIT));\n"
     "      }",
     "      /* 变异：不拉事件 */",
     "事件列表渲染 3 行"),
]


def main() -> int:
    bad = 0
    code, out = run_test()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-800:])
        return 1
    print("  PASS 起点绿")

    for label, old_txt, new_txt, want in KNIVES:
        data = _read(JS)
        old, new = _encode(JS, old_txt), _encode(JS, new_txt)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(JS, data.replace(old, new))
        try:
            code, out = run_test()
        finally:
            _write(JS, data)
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
