# -*- coding: utf-8 -*-
"""变异刀：把 check_tool_sys_path.py 的每条判据逐个打哑，确认它真的会红。

为什么需要它：这道闸门是**纯静态**的（不跑任何闸门），最容易变成「永远 PASS」
的那种门 —— 判据写错一个方向、作用域链算错一层，它照样天天打 PASS，而真正的
故障（闸门在推荐解释器下起不来）照样漏过去。所以每条判据都要有反例。

做法：闸门支持 `--tools <目录>`，于是在临时目录里摆几份**桩脚本**，不碰仓库、
不起子进程跑真闸门，一条判据一个桩：

  G  正常桩（模块层先补路径再 import）        → PASS
  T  路径经 for 中转（ROOT → p）              → PASS（防「一层中转就判红」）
  M  漏补路径                                 → FAIL ①
  H  补了但写死盘符路径                       → FAIL ②
  A  先 import 后补路径（模块层也是自上而下） → FAIL ①
  S  补在函数里（模块层 import 时还没执行）   → FAIL ①
  B  语法错误的文件                           → FAIL ③
  X  空目录 / 目录不存在                      → exit 2（判据自己瞎了，不算通过）

另外两个「不许算成红」的桩，证明闸门没有过度判：
  D  写在接了 ImportError 的 try 里 → 降级导入，不计分
  K  带「不进闸门：」标记           → 跳过，不计分

退出码：0 = 全部符合预期；1 = 有不符合。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check_tool_sys_path.py")
PY = sys.executable

SIB = "VALUE = 1\n"

GOOD = """\
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _sib  # noqa: E402

print(_sib.VALUE)
"""

LOOP = """\
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _sib import VALUE  # noqa: E402

print(VALUE)
"""

MISSING = """\
import _sib

print(_sib.VALUE)
"""

HARD = """\
import sys

sys.path.insert(0, r"G:\\ai\\tools")

import _sib  # noqa: E402

print(_sib.VALUE)
"""

AFTER = """\
import _sib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print(_sib.VALUE)
"""

SCOPED = """\
import sys

import _sib


def _warm() -> None:
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


print(_sib.VALUE)
"""

BROKEN = """\
import _sib

def oops(:
    pass
"""

GUARDED = """\
try:
    import _sib
except ImportError:
    _sib = None

print(_sib)
"""

MARKED = '''\
"""桩：只用来验证免登标记。

不进闸门：变异刀夹具，不参与真实闸门
"""
import _sib

print(_sib.VALUE)
'''

BASE = {
    "_sib.py": SIB,
    "check_ok.py": GOOD,
    "check_loop.py": LOOP,
    "check_guarded.py": GUARDED,
    "check_marked.py": MARKED,
    "check_nosib.py": "import os\n\nprint(os.sep)\n",
}

CASES = [
    ("G 正常桩", {}, 0, ""),
    ("T 路径经 for 中转", {}, 0, ""),
    ("M 漏补路径", {"check_missing.py": MISSING}, 1, "check_missing.py"),
    ("H 写死盘符路径", {"check_hard.py": HARD}, 1, "check_hard.py"),
    ("A 先 import 后补路径", {"check_after.py": AFTER}, 1, "check_after.py"),
    ("S 补在函数里", {"check_scoped.py": SCOPED}, 1, "check_scoped.py"),
    ("B 语法错误", {"check_broken.py": BROKEN}, 1, "check_broken.py"),
    ("D 降级导入不算红", {}, 0, "降级导入"),
    ("K 免登标记不算红", {}, 0, "跳过"),
]


def _write(dst: str, files: dict) -> None:
    for name, text in files.items():
        with io.open(os.path.join(dst, name), "w", encoding="utf-8",
                     newline="\n") as f:
            f.write(text)


def _run(tools_dir: str) -> tuple:
    p = subprocess.run([PY, GATE, "--tools", tools_dir],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    if not os.path.isfile(GATE):
        print("  FAIL 找不到闸门：%s" % GATE)
        print("\n结果: 0 PASS / 1 FAIL")
        return 1
    ok_n = 0
    bad_n = 0
    tmp = tempfile.mkdtemp(prefix="qc_syspath_")
    try:
        for label, extra, want_code, want_text in CASES:
            d = os.path.join(tmp, label.split()[0])
            os.makedirs(d, exist_ok=True)
            files = dict(BASE)
            files.update(extra)
            _write(d, files)
            code, out = _run(d)
            good = code == want_code and want_text in out
            if good:
                ok_n += 1
                print("  PASS %s → exit %d%s"
                      % (label, code, ("，输出含「%s」" % want_text)
                         if want_text else ""))
            else:
                bad_n += 1
                print("  FAIL %s → exit %d（期望 %d）"
                      % (label, code, want_code))
                for ln in out.strip().splitlines()[-6:]:
                    print("        | %s" % ln)

        # 空目录 / 目录不存在：判据自己瞎了，必须是 exit 2，不许当通过
        for label, path, make in (
                ("X1 空目录", os.path.join(tmp, "empty"), True),
                ("X2 目录不存在", os.path.join(tmp, "nope"), False)):
            if make:
                os.makedirs(path, exist_ok=True)
            code, out = _run(path)
            if code == 2:
                ok_n += 1
                print("  PASS %s → exit 2（判据自己瞎了，不算通过）" % label)
            else:
                bad_n += 1
                print("  FAIL %s → exit %d（期望 2）" % (label, code))
                for ln in out.strip().splitlines()[-4:]:
                    print("        | %s" % ln)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (ok_n, bad_n))
    return 0 if bad_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
