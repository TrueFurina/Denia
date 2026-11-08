# -*- coding: utf-8 -*-
"""通知浮窗死路径墓碑的变异刀（2026-11-08）。

判据在 src/tests/test_notify_dead_path.py。没有变异就分不清「判据正确」与
「判据恒真」——本仓在 check_bridge_guards 上踩过：判据被自己的注释满足，
于是「删干净了」永远为真，刀却一直绿。

四刀 + 一组对照：

  M1 死代码回潮   ：在 try/except 之后插一句 `import ctypes`   → 判据 1 必须红
  M2 符号导回来   ：模块级加 `from .mode_notify import NotifyJsBridge`
                                                              → 判据 3 必须红
  M3 改签名       ：`def create_notify_window(api=None)` 去掉 api
                                                              → 判据 2 必须红
  M4 对照组       ：只在函数体里加一行**注释**，提到 NotifyJsBridge /
                    _notify_selfcheck                          → 必须仍然全绿
                    （判据 3 若忘了剥 docstring/注释，就会被自己的墓碑满足，
                      于是 M1/M2 全哑。M4 专门钉这一点。）

实现纪律（都是本仓踩过的坑）：
  - 源码**二进制**读写：文本模式在 Windows 会把 \n 写成 \r\n，后续锚点静默失效。
    锚点按实测 EOL 拼。
  - 锚点命中次数必须是 1，0 次或多次一律 FAIL，不许当没发生过（否则刀会静默跳过）。
  - 还原后校验**字节相等**。
  - 断言"红的是**这条**用例"，不是"任意非零退出"——否则分不清判据在工作、
    还是判据自己崩了（两者都 exit 1）。
  - 对照组不只判 exit 0，还要求「Ran 6 tests」——否则把测试文件删空也是绿的。

用法：python tools/_qc_notify_dead_path_mutation.py
退出码 0 = 四刀 + 对照全部符合预期。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN_WINDOW = os.path.join(ROOT, "src", "ui", "main_window.py")
TEST = os.path.join(ROOT, "src", "tests", "test_notify_dead_path.py")

CASE_UNREACHABLE = "test_no_statement_after_always_return"
CASE_SIGNATURE = "test_compat_signature_kept"
CASE_SYMBOLS = "test_dead_symbols_absent_from_code"
CASE_CONTROL = "test_control_docstring_mentions_are_allowed"
EXPECTED_TESTS = 6

# —— 锚点（中文，统一走 _enc）——
ANCHOR_HANDLER_TAIL = (
    "    except Exception as _e:  # noqa: BLE001\n"
    '        logger.warning("[通知浮窗] 兼容壳 ensure 失败: %s", _e)\n'
    "        return None\n"
)
ANCHOR_NOTIFY_GEOM_IMPORT = "from .notify_geom import _notify_geom, _notify_set_pos\n"
ANCHOR_DOC_END_TRY = '    """\n    try:\n        from . import native_notify\n'
ANCHOR_DEF = "def create_notify_window(api=None):\n"


def _enc(s: str) -> bytes:
    return s.encode("utf-8")


def _eol(b: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in b else b"\n"


def _read(p: str) -> bytes:
    with io.open(p, "rb") as f:
        return f.read()


def _write(p: str, data: bytes) -> None:
    with io.open(p, "wb") as f:
        f.write(data)


def _run_test() -> tuple[int, str]:
    """跑判据。用列表参数（不过 shell）→ 退出码是 python 的，不是管道的。"""
    p = subprocess.run([sys.executable, TEST], cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _failed_cases(out: str) -> list:
    """unittest 的失败行形如 `FAIL: test_xxx (module.Class)`。"""
    return re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)


def _inject_after(base: bytes, anchor_txt: str, inject_txt: str, tag: str) -> bytes | None:
    """在 anchor 之后插入 inject。锚点必须恰好命中 1 次，否则返回 None（判 FAIL）。"""
    eol = _eol(base)
    anchor = _enc(anchor_txt).replace(b"\n", eol)
    inject = _enc(inject_txt).replace(b"\n", eol)
    n = base.count(anchor)
    if n != 1:
        print("  FAIL %s：锚点命中 %d 次（应为 1）" % (tag, n))
        return None
    return base.replace(anchor, anchor + inject)


def _check(base: bytes, mutated: bytes | None, tag: str, want_case: str | None,
           want_green: bool = False) -> int:
    """注入 → 跑判据 → 判断红/绿是否落在预期的用例上 → 还原。返回 1 表示不符合预期。"""
    if mutated is None:
        return 1
    _write(MAIN_WINDOW, mutated)
    try:
        code, out = _run_test()
    finally:
        _write(MAIN_WINDOW, base)

    if want_green:
        if code != 0:
            print("  FAIL %s：应当仍绿，实际 exit=%d；红的是 %s"
                  % (tag, code, _failed_cases(out) or "(未解析到用例名)"))
            for line in out.splitlines():
                if line.startswith(("FAIL:", "ERROR:")):
                    print("    " + line[:160])
            return 1
        m = re.search(r"Ran (\d+) tests?", out)
        ran = int(m.group(1)) if m else -1
        if ran != EXPECTED_TESTS:
            print("  FAIL %s：绿了但只跑了 %d 条用例（应为 %d）——判据被删空也绿？"
                  % (tag, ran, EXPECTED_TESTS))
            return 1
        print("  PASS %s：仍全绿（%d 条用例），注释/墓碑说明不算违规" % (tag, ran))
        return 0

    if code == 0:
        print("  FAIL %s：判据仍绿（墓碑没拦住回潮）" % tag)
        return 1
    failed = _failed_cases(out)
    if want_case not in failed:
        print("  FAIL %s：红了但不是 %s 红的；实际红的是 %s"
              % (tag, want_case, failed or "(未解析到用例名)"))
        return 1
    print("  PASS %s：%s 红了" % (tag, want_case))
    return 0


def main() -> int:
    if not os.path.isfile(TEST):
        print("  FAIL 判据文件不存在：%s" % TEST)
        return 1

    base = _read(MAIN_WINDOW)

    # 基线必须自己是绿的，否则后面每刀的结论都不可信
    code, out = _run_test()
    if code != 0:
        print("  FAIL 基线就不绿，先查判据本身：")
        for line in out.splitlines():
            if line.startswith(("FAIL:", "ERROR:")):
                print("    " + line[:160])
        return 1
    print("  基线全绿（%d 条用例）" % EXPECTED_TESTS)

    bad = 0

    # ---- M1：try/except 之后插一句 → 判据 1 红 ----
    bad += _check(base,
                  _inject_after(base, ANCHOR_HANDLER_TAIL, "    import ctypes\n", "M1"),
                  "M1 死代码回潮", CASE_UNREACHABLE)

    # ---- M2：模块级把 NotifyJsBridge 导回来 → 判据 3 红 ----
    bad += _check(base,
                  _inject_after(base, ANCHOR_NOTIFY_GEOM_IMPORT,
                                "from .mode_notify import NotifyJsBridge\n", "M2"),
                  "M2 符号导回代码", CASE_SYMBOLS)

    # ---- M3：改签名去掉 api → 判据 2 红 ----
    eol = _eol(base)
    old_def = _enc(ANCHOR_DEF).replace(b"\n", eol)
    new_def = _enc("def create_notify_window():\n").replace(b"\n", eol)
    if base.count(old_def) != 1:
        print("  FAIL M3：锚点命中 %d 次（应为 1）" % base.count(old_def))
        bad += 1
    else:
        bad += _check(base, base.replace(old_def, new_def), "M3 改签名", CASE_SIGNATURE)

    # ---- M4：对照组——只加注释，必须仍全绿 ----
    comment = ("    # 对照组：注释里提 NotifyJsBridge / _notify_selfcheck / "
               "_notify_park_out 都不算违规——判据剥掉了 docstring 与注释。\n")
    bad += _check(base,
                  _inject_after(base, ANCHOR_DOC_END_TRY, comment, "M4"),
                  "M4 对照组（只加注释）", None, want_green=True)

    # ---- 还原校验 ----
    if _read(MAIN_WINDOW) != base:
        print("  FAIL main_window.py 没有还原到原字节！")
        bad += 1
    else:
        print("  main_window.py 已字节级还原")

    print("=" * 60)
    if bad:
        print("变异刀发现 %d 处问题" % bad)
        return 1
    print("四刀 + 对照组全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
