# -*- coding: utf-8 -*-
"""check_cross_ref 的变异刀：**样本全在临时目录里造**，一次也不碰本仓源码。

为什么用临时目录
----------------
本闸门的输入就是 `--root`。造一棵几行的假模块树放进 `tempfile` 就够覆盖全部判据，
而且没有「还原后字节对不上」这个失败面。更重要的是：**能造出真源码里不该存在的形态**
（指向不存在子模块的 import、被 star-import 污染的模块、rebound 别名、语法坏文件），
不受「仓库里得先有这么一个坏文件」的限制。

刀单
----
每刀都断言**具体现象**，不是「只要非零就行」——否则分不清「闸门在工作」和「闸门自己崩了」，
两者都是 exit 1/2。

  K1  正向·L1 缺名字   ：`from .b import nope`（b 里没有 nope）        → exit 1 且点名
  K2  正向·L1 缺子模块 ：`from . import nope`（没有 nope.py）          → exit 1 且点名
  K3  正向·L2 缺属性   ：`from . import b as m` + `m.nope`            → exit 1 且点名
  K4  限位·外部包      ：`import requests` + `requests.whatever`      → 绿
  K5  限位·star 污染   ：目标模块有 `from x import *`                  → 绿（不可判定就不判）
  K6  限位·别名被重绑  ：`from . import b as m` 之后 `m = 1` + `m.nope` → 绿
  K7  限位·hasattr 声明：`hasattr(m, "nope")` 之后的 `m.nope`          → 绿（代码自己说它可能没有）
  K8  限位·dunder      ：`m.__file__`                                 → 绿
  K9  限位·init 基址   ：包内 `__init__.py` 的 `.b` 必须指**包自己**下的 b，
                        而不是父目录下同名的 b                        → 绿
  K10 限位·豁免标记    ：文件里有 `# crossref-ok: …`                  → 绿
  K11 反向·零覆盖      ：一个 .py 都没有                              → exit 2
  K12 反向·解析失败    ：语法坏文件                                    → exit 2
  对照：对**真仓**跑一次                                              → exit 0 且文件数 ≥ 600

K4–K10 是限位刀。少了它们，把 `scan()` 改成「一律报」也能让 K1–K3 全过——
那就成了噪声源，而噪声源的结局是被人加豁免加到失效。
K9 尤其重要：它是本轮**真踩到**的坑（包内 `__init__.py` 里的 `.` 指包自己，不是父目录），
原版写错后 10 处全是假阳性。
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
GATE = os.path.join(HERE, "check_cross_ref.py")


def _build(files: dict[str, str]) -> str:
    d = tempfile.mkdtemp(prefix="qc_crossref_")
    for rel, src in files.items():
        full = os.path.join(d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
        with io.open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
    return d


def _run(root: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE, "--root", root],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _run_real() -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _case(files, want: int, label: str, must: str = "") -> tuple[bool, str]:
    d = _build(files)
    try:
        code, out = _run(d)
        if code != want:
            return False, "%s 期望 exit %d，实际 %d\n%s" % (label, want, code, out.strip()[:600])
        if must and must not in out:
            return False, "%s 退出码对了但输出里没有「%s」\n%s" % (label, must, out.strip()[:600])
        return True, "%s → exit %d" % (label, want)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- 刀

def _k1():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "def good():\n    pass\n",
                  "pkg/a.py": "from .b import nope\n"},
                 1, "K1 L1 缺名字", "nope")


def _k2():
    return _case({"pkg/__init__.py": "", "pkg/a.py": "from . import nope\n"},
                 1, "K2 L1 缺子模块", "nope")


def _k3():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "def good():\n    pass\n",
                  "pkg/a.py": "from . import b as m\n\n\nx = m.nope\n"},
                 1, "K3 L2 缺属性", "m.nope")


def _k4():
    return _case({"a.py": "import requests\n\n\nx = requests.whatever\n"},
                 0, "K4 外部包不判")


def _k5():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "from os import *\n",
                  "pkg/a.py": "from .b import nope\n"},
                 0, "K5 star 污染的模块跳过")


def _k6():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "def good():\n    pass\n",
                  "pkg/a.py": "from . import b as m\n\nm = 1\n\n\ndef f():\n    return m.nope\n"},
                 0, "K6 别名被重绑后不再当模块")


def _k7():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "def good():\n    pass\n",
                  "pkg/a.py": ("from . import b as m\n\n\n"
                               "def f():\n"
                               "    return m.nope if hasattr(m, 'nope') else None\n")},
                 0, "K7 hasattr 声明过的不判")


def _k8():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "def good():\n    pass\n",
                  "pkg/a.py": "from . import b as m\n\n\nx = m.__file__\n"},
                 0, "K8 dunder 属性")


def _k9():
    """包内 __init__.py 的 `.b` 必须解析到 pkg/b.py，而不是根目录那个同名的 b.py。"""
    return _case({
        "b.py": "",                                   # 父目录下的同名模块：错误目标
        "pkg/__init__.py": "from .b import good\n",    # 正确目标应是 pkg/b.py
        "pkg/b.py": "def good():\n    pass\n",
    }, 0, "K9 init 里 `.` 指包自己")


def _k10():
    return _case({"pkg/__init__.py": "", "pkg/b.py": "def good():\n    pass\n",
                  "pkg/a.py": ("# crossref-ok: 这个符号由插件在运行时挂载\n"
                               "from .b import nope\n")},
                 0, "K10 豁免标记")


def _k11():
    d = tempfile.mkdtemp(prefix="qc_crossref_")
    try:
        with io.open(os.path.join(d, "readme.txt"), "w", encoding="utf-8") as f:
            f.write("no python here\n")
        code, out = _run(d)
        if code != 2:
            return False, "K11 期望 exit 2（零覆盖），实际 %d\n%s" % (code, out.strip()[:400])
        return True, "K11 扫到 0 个 .py → exit 2"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _k12():
    return _case({"a.py": "def f(:\n    pass\n"}, 2, "K12 语法坏文件 → exit 2", "解析")


def _control() -> tuple[bool, str]:
    code, out = _run_real()
    if code != 0:
        return False, "对照组期望 exit 0，实际 %d\n%s" % (code, out.strip()[:600])
    m = re.search(r"扫描\s*(\d+)\s*个文件", out)
    if not m:
        return False, "对照组输出里解析不到文件数\n%s" % out.strip()[:400]
    n = int(m.group(1))
    if n < 600:
        return False, "对照组只扫了 %d 个文件（< 600）——收集逻辑漏了？" % n
    return True, "对照组绿，扫描 %d 个文件" % n


KNIVES = (("K1", _k1), ("K2", _k2), ("K3", _k3), ("K4", _k4), ("K5", _k5), ("K6", _k6),
          ("K7", _k7), ("K8", _k8), ("K9", _k9), ("K10", _k10), ("K11", _k11), ("K12", _k12))


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
    print("✓ 三把正向刀全红 + 七把限位/反向刀全对 + 对照组绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
