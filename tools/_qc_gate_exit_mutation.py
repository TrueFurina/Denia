# -*- coding: utf-8 -*-
"""check_gate_exit 的变异刀：**样本全在临时目录里造**，一次也不碰本仓源码。

为什么不像别的刀那样「改源码 → 还原」
--------------------------------------
本闸门的输入本来就是 `--registry` + `--root` 两个参数。造一张假登记表 + 几个几行的
假脚本放进 `tempfile`，就足以覆盖全部判据；代价是**没有「还原后字节对不上」这个失败面**
（`_qc_gate_registry_mutation.py` 里那套 restore-verify 是没办法——它必须改真源码）。
而且这样能造出真源码里不该存在的形态（`os._exit(0)`、`GATES = []`、语法错的登记表），
不受「仓库里得先有这么一个坏文件」的限制。

刀单
----
每刀都断言**具体现象**，不是「只要非零就行」——否则分不清「闸门在工作」和「闸门自己崩了」，
两者都是 exit 1/2。所以正向刀一律要求输出里点名那个脚本。

  K1  正向·打印不退出 ：只有 print、没有退出码                    → exit 1 且点名
  K2  正向·exit(0)     ：`sys.exit(0)`                           → exit 1 且点名
  K3  正向·os._exit(0) ：`os._exit(0)`                           → exit 1 且点名
  K4  限位·exit(1)     ：`sys.exit(1)`                           → 必须绿
  K5  限位·exit(main())：参数不是常量                            → 必须绿
  K6  限位·unittest.main()                                       → 必须绿
  K7  限位·raise       ：`raise SystemExit(1)`                   → 必须绿
  K8  限位·assert                                                → 必须绿
  K9  限位·豁免标记    ：`# gate-exit-ok: …`                     → 必须绿
  K10 限位·非 .py 项   ：混一项 `-m compileall`                  → 必须绿且「跳过 1 项」
  K11 反向·脚本不存在  ：登记项指向没有的文件                     → exit 2（不是通过）
  K12 反向·登记表语法错                                          → exit 2
  K13 反向·GATES 为空                                            → exit 2
  K14 反向·零覆盖      ：只剩 `-m compileall`，扫到 0 个 .py      → exit 2
  K15 已知松·try 包住的 raise                                    → 必须绿（钉住当前口径）
  对照：对**真仓**登记表跑一次                                    → exit 0 且检查数 ≥ 150

K4–K10 是限位刀：它们守的是「该绿的还得绿」。少了它们，把 `_can_fail()` 改成
`return True` 也能让 K1–K3 全过——那就是又一道装饰闸门，只不过这次装的是这道闸门自己。
K14 是这一轮**真踩到**的：`checked == 0` 时原版直接 exit 0，K14 就是补它时留下的钉子。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(HERE, "check_gate_exit.py")


# --------------------------------------------------------------------------- 造样本

def _build(entries, scripts) -> str:
    """在临时目录里造一张登记表 + 若干脚本，返回目录。

    entries: [(闸门名, [argv...])]
    scripts: {文件名: 源码}
    """
    d = tempfile.mkdtemp(prefix="qc_gate_exit_")
    reg = ["# -*- coding: utf-8 -*-",
           "GATES: list[tuple[str, str, list[str], bool]] = ["]
    for name, argv in entries:
        reg.append('    ("%s", "PY", [%s], False),'
                   % (name, ", ".join('"%s"' % a for a in argv)))
    reg.append("]")
    with io.open(os.path.join(d, "reg.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(reg) + "\n")
    for fn, src in scripts.items():
        with io.open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
    return d


def _run(reg_dir: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE,
                        "--registry", os.path.join(reg_dir, "reg.py"),
                        "--root", reg_dir],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _run_real() -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# --------------------------------------------------------------------------- 刀

def _expect_hit(entries, scripts, name: str, label: str) -> tuple[bool, str]:
    """正向刀：必须 exit 1，且输出里点名 `name`。"""
    d = _build(entries, scripts)
    try:
        code, out = _run(d)
        if code != 1:
            return False, "%s 期望 exit 1，实际 %d\n%s" % (label, code, out.strip())
        if name not in out:
            return False, "%s 红了但没点名 %s——可能红在别处\n%s" % (label, name, out.strip())
        return True, "%s 抓到 %s" % (label, name)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _expect_green(entries, scripts, label: str) -> tuple[bool, str]:
    """限位刀：必须 exit 0。"""
    d = _build(entries, scripts)
    try:
        code, out = _run(d)
        if code != 0:
            return False, "%s 期望 exit 0（限位刀：这个形态本来就该绿），实际 %d\n%s" % (
                label, code, out.strip())
        return True, "%s 保持绿" % label
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _expect_code(entries, scripts, want: int, label: str,
                 must_contain: str = "") -> tuple[bool, str]:
    """反向刀：必须 exit want（2 = 无法判定），可选要求输出含某词。"""
    d = _build(entries, scripts)
    try:
        code, out = _run(d)
        if code != want:
            return False, "%s 期望 exit %d，实际 %d\n%s" % (label, want, code, out.strip())
        if must_contain and must_contain not in out:
            return False, "%s 退出码对了但输出里没有「%s」\n%s" % (label, must_contain, out.strip())
        return True, "%s → exit %d" % (label, want)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _k1() -> tuple[bool, str]:
    return _expect_hit([("打印就完", ["bad_print.py"])],
                       {"bad_print.py": 'print("[PASS] 看起来通过了")\n'},
                       "bad_print.py", "K1")


def _k2() -> tuple[bool, str]:
    return _expect_hit([("显式零退出", ["bad_exit0.py"])],
                       {"bad_exit0.py": "import sys\nsys.exit(0)\n"},
                       "bad_exit0.py", "K2")


def _k3() -> tuple[bool, str]:
    return _expect_hit([("下划线退出", ["bad_osexit.py"])],
                       {"bad_osexit.py": "import os\nos._exit(0)\n"},
                       "bad_osexit.py", "K3")


def _k4() -> tuple[bool, str]:
    return _expect_green([("正常闸门", ["good_exit1.py"])],
                         {"good_exit1.py": "import sys\nsys.exit(1)\n"}, "K4 sys.exit(1)")


def _k5() -> tuple[bool, str]:
    return _expect_green([("返回值退出", ["good_main.py"])],
                         {"good_main.py": "import sys\n\n\ndef main():\n    return 1\n\n\n"
                                          "sys.exit(main())\n"}, "K5 sys.exit(main())")


def _k6() -> tuple[bool, str]:
    src = ("import unittest\n\n\n"
           "class T(unittest.TestCase):\n"
           "    def test_x(self):\n"
           "        self.assertTrue(True)\n\n\n"
           "unittest.main()\n")
    return _expect_green([("单测闸门", ["good_unittest.py"])],
                         {"good_unittest.py": src}, "K6 unittest.main()")


def _k7() -> tuple[bool, str]:
    return _expect_green([("抛异常退出", ["good_raise.py"])],
                         {"good_raise.py": "raise SystemExit(1)\n"}, "K7 raise SystemExit")


def _k8() -> tuple[bool, str]:
    return _expect_green([("断言闸门", ["good_assert.py"])],
                         {"good_assert.py": "assert 1 == 1\n"}, "K8 assert")


def _k9() -> tuple[bool, str]:
    src = ('"""只出报告，不判通过/失败。\n\n'
           '# gate-exit-ok: 本脚本汇总统计给 CI 存档，判据在 check_xxx.py 里。\n"""\n'
           'print("统计完成")\n')
    return _expect_green([("报告类", ["good_exempt.py"])],
                         {"good_exempt.py": src}, "K9 豁免标记")


def _k10() -> tuple[bool, str]:
    """混一项外部命令：.py 那项绿，非 .py 跳过 1 项，整体绿。"""
    d = _build([("字节码编译", ["-m", "compileall", "-q", "src"]),
                ("正常闸门", ["good_exit1.py"])],
               {"good_exit1.py": "import sys\nsys.exit(1)\n"})
    try:
        code, out = _run(d)
        if code != 0:
            return False, "K10 期望 exit 0，实际 %d\n%s" % (code, out.strip())
        m = re.search(r"跳过\s*(\d+)\s*项非\s*\.py", out)
        if not m:
            return False, "K10 输出里解析不到跳过数\n%s" % out.strip()
        if m.group(1) != "1":
            return False, "K10 跳过数 %s ≠ 1（外部命令没被排除？）" % m.group(1)
        return True, "K10 外部命令被跳过且不影响判定"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _k11() -> tuple[bool, str]:
    return _expect_code([("脚本没落地", ["nope_missing.py"])], {}, 2,
                        "K11 登记项指向不存在的脚本", "不存在")


def _k12() -> tuple[bool, str]:
    d = tempfile.mkdtemp(prefix="qc_gate_exit_")
    try:
        with io.open(os.path.join(d, "reg.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write("GATES: list[tuple[str, str, list[str], bool]] = [\n"
                    '    ("半截", "PY", ["a.py"], False),\n')   # 故意不闭合
        with io.open(os.path.join(d, "a.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write("import sys\nsys.exit(1)\n")
        code, out = _run(d)
        if code != 2:
            return False, "K12 期望 exit 2，实际 %d\n%s" % (code, out.strip())
        return True, "K12 登记表语法错 → exit 2"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _k13() -> tuple[bool, str]:
    return _expect_code([], {}, 2, "K13 GATES 为空", "一项都没解析出来")


def _k14() -> tuple[bool, str]:
    """零覆盖：只剩外部命令，扫到 0 个 .py 必须 exit 2，不能算通过。"""
    return _expect_code([("字节码编译", ["-m", "compileall", "-q", "src"])], {}, 2,
                        "K14 扫到 0 个 .py 脚本", "一个 .py 登记脚本都没扫到")


def _k15() -> tuple[bool, str]:
    """已知松：`try` 包住的 `raise` 仍判绿（见闸门 docstring「量过的两条边界」①）。"""
    src = ('"""看起来会失败，其实失败被吞了。"""\n'
           'try:\n'
           '    raise RuntimeError("boom")\n'
           'except Exception:\n'
           '    pass\n')
    ok, msg = _expect_green([("吞掉失败", ["loose_try.py"])],
                            {"loose_try.py": src}, "K15 try 包住的 raise（已知松）")
    return ok, msg + "（当前口径：不判；收紧后此刀会红，属预期）"


def _control() -> tuple[bool, str]:
    """对照组：对真仓登记表跑，必须绿且检查数 ≥ 150（不写死，仓库会长大）。"""
    code, out = _run_real()
    if code != 0:
        return False, "对照组期望 exit 0，实际 %d\n%s" % (code, out.strip())
    m = re.search(r"检查\s*(\d+)\s*个登记脚本", out)
    if not m:
        return False, "对照组输出里解析不到检查数\n%s" % out.strip()
    n = int(m.group(1))
    if n < 150:
        return False, "对照组只检查了 %d 个脚本（< 150）——登记表解析漏了？" % n
    return True, "对照组绿，检查 %d 个登记脚本" % n


KNIVES = (
    ("K1", _k1), ("K2", _k2), ("K3", _k3),
    ("K4", _k4), ("K5", _k5), ("K6", _k6), ("K7", _k7), ("K8", _k8),
    ("K9", _k9), ("K10", _k10), ("K11", _k11), ("K12", _k12), ("K13", _k13),
    ("K14", _k14), ("K15", _k15),
)


def main() -> int:
    results = []
    for name, fn in KNIVES:
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, "刀自身异常: %r" % exc
        results.append((name, ok, msg))
        print("%s %s —— %s" % ("✓" if ok else "✗", name, msg))

    try:
        ok, msg = _control()
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, "对照组异常: %r" % exc
    results.append(("对照", ok, msg))
    print("%s 对照 —— %s" % ("✓" if ok else "✗", msg))

    bad = [r for r in results if not r[1]]
    if bad:
        print("✗ %d 项不符合" % len(bad))
        return 1
    print("✓ 三把正向刀全红 + 十把限位/反向刀全对 + 对照组绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
