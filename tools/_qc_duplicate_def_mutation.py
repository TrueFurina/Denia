# -*- coding: utf-8 -*-
"""同名定义静默覆盖闸门的变异刀（2026-11-08）。

判据是 tools/check_duplicate_def.py 本身。刀不碰真实源码——闸门吃的是**路径**，
在临时目录里造样本喂给它即可，因此不需要"还原校验"。

六刀 + 一组对照：

  M1 两个无装饰器同名函数              → 必须红，且点名文件与行号
  M2 后一个上方加 `# duplicate-ok:`    → 必须绿（豁免真的生效）
  M3 `@property` + `@x.setter`         → 必须绿（本仓真实范式，`pet_shell.py` /
                                        `proactive.py` 都是这么写的，误报过一次）
  M4 `@_t("用例名") def _()` 连写两个   → 必须绿（`agent_pro/selftest.py` 的注册式写法，
                                        全仓 37 处；不去掉这个误报，闸门就是噪声源）
  M5 两个同名 class                    → 必须红（不止函数，类也覆盖）
  M6 语法错误文件                      → 必须 exit 2，不许静默通过
  对照 干净代码                        → 必须绿

M3/M4 是这条判据的"限定位"：判据之所以写成"两边都没装饰器才报"，就是因为这两刀
在真实仓库里各占几十处。把它们钉成"必须绿"，防止将来有人为了"更严格"去掉限定，
把闸门变成一片假红。

断言的是"抓到了注入的那个错"（点名文件 + 行号），不是"任意非零退出"。

用法：python tools/_qc_duplicate_def_mutation.py
退出码 0 = 六刀 + 对照全部符合预期。
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
GATE = os.path.join(HERE, "check_duplicate_def.py")

CASE_PLAIN = "dup_plain.py"
CASE_MARKED = "dup_marked.py"
CASE_PROPERTY = "dup_property.py"
CASE_DECORATED = "dup_decorated.py"
CASE_CLASS = "dup_class.py"
CASE_SYNTAX = "dup_broken.py"
CASE_CLEAN = "dup_clean.py"

SAMPLES = {
    CASE_PLAIN: """def f():
    return 1


def f():
    return 2
""",
    CASE_MARKED: """def f():
    return 1


# duplicate-ok: 样本用——验证豁免生效
def f():
    return 2
""",
    CASE_PROPERTY: """class A:
    @property
    def x(self):
        return 1

    @x.setter
    def x(self, v):
        self._x = v
""",
    CASE_DECORATED: """def _t(name):
    def deco(fn):
        return fn
    return deco


@_t("a")
def _():
    pass


@_t("b")
def _():
    pass
""",
    CASE_CLASS: """class A:
    pass


class A:
    pass
""",
    CASE_SYNTAX: """def f(:
    pass
""",
    CASE_CLEAN: """def f():
    return 1


class A:
    def m(self):
        return 1

    def n(self):
        return 2
""",
}


def _run_gate(target: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE, target], cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _write_case(d: str, name: str) -> None:
    with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(SAMPLES[name])


def _make_dir(exclude=()) -> str:
    d = tempfile.mkdtemp(prefix="qc_dupdef_")
    for name in SAMPLES:
        if name not in exclude:
            _write_case(d, name)
    return d


def _expect(d: str, name: str, want_code: int, want_line, green_word, tag: str, why: str) -> int:
    code, out = _run_gate(d)
    if code != want_code:
        print("  FAIL %s（%s）：期望 exit %d，实际 %d" % (tag, why, want_code, code))
        for line in out.splitlines()[:6]:
            print("    " + line[:160])
        return 1

    if want_code == 0:
        if green_word and green_word not in out:
            print("  FAIL %s：exit 0 但输出里没有「%s」——可能根本没扫到文件" % (tag, green_word))
            return 1
        print("  PASS %s（%s）：exit 0" % (tag, why))
        return 0

    if want_code == 2:
        if "解析失败" not in out and "无法判定" not in out:
            print("  FAIL %s：exit 2 但没说清是解析失败——分不清闸门坏了还是文件坏了" % tag)
            return 1
        print("  PASS %s（%s）：exit 2 且明确报「无法判定」" % (tag, why))
        return 0

    if name not in out:
        print("  FAIL %s：exit 1 但没点名 %s（可能红在别的样本上）" % (tag, name))
        for line in out.splitlines()[:6]:
            print("    " + line[:160])
        return 1
    if want_line is not None and not re.search(re.escape(name) + r":%d" % want_line, out):
        print("  FAIL %s：点名了 %s 但不是第 %d 行（行号漂了？）" % (tag, name, want_line))
        for line in out.splitlines()[:6]:
            print("    " + line[:160])
        return 1
    print("  PASS %s（%s）：exit 1 且点名 %s:%s" % (tag, why, name, want_line))
    return 0


def main() -> int:
    if not os.path.isfile(GATE):
        print("  FAIL 闸门不存在：%s" % GATE)
        return 1

    d = _make_dir(exclude=(CASE_SYNTAX,))
    bad = 0
    try:
        # 基线：整批里应有 2 处（M1 的函数 + M5 的类），4 个样本必须保持绿。
        # 语法错样本故意排除：闸门遇到解析失败返回 2，会把基线退出码从 1 变成 2。
        code, out = _run_gate(d)
        m = re.search(r"同名覆盖 (\d+) 处", out)
        got = int(m.group(1)) if m else -1
        if code != 1 or got != 2:
            print("  FAIL 基线：期望 exit 1 / 同名覆盖 2 处（M1+M5），实际 exit %d / %s 处"
                  % (code, got if got >= 0 else "?"))
            for line in out.splitlines()[:10]:
                print("    " + line[:160])
            bad += 1
        else:
            print("  基线：6 个样本里 2 处覆盖（M1/M5 各 1），3 个范式样本保持绿")

        for name, code_want, line_want, green_word, tag, why in [
            (CASE_PLAIN, 1, 5, None, "M1", "两个无装饰器同名函数"),
            (CASE_MARKED, 0, None, "同名覆盖 0 处", "M2", "加 duplicate-ok 后豁免"),
            (CASE_PROPERTY, 0, None, "同名覆盖 0 处", "M3", "property + setter 不误报"),
            (CASE_DECORATED, 0, None, "同名覆盖 0 处", "M4", "装饰器注册式 def _ 不误报"),
            (CASE_CLASS, 1, 5, None, "M5", "两个同名 class"),
            (CASE_SYNTAX, 2, None, None, "M6", "语法错误不许静默通过"),
            (CASE_CLEAN, 0, None, "同名覆盖 0 处", "对照", "干净代码"),
        ]:
            solo = tempfile.mkdtemp(prefix="qc_dup_%s_" % tag)
            try:
                _write_case(solo, name)
                bad += _expect(solo, name, code_want, line_want, green_word, tag, why)
            finally:
                shutil.rmtree(solo, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("=" * 60)
    if bad:
        print("变异刀发现 %d 处问题" % bad)
        return 1
    print("六刀 + 对照组全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
