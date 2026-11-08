# -*- coding: utf-8 -*-
"""空变异体检：把每一刀的 `new` 换成 `old`，看它还认不认得出来。

为什么需要
----------
`check_knife_vacuity.py` 用的是「把**判定文件**换成退化实现」，前提是刀有一个独立的
判定文件。全仓 78 个 `_qc_*` 里只有 26 个有，另外 48 个是**改完源码自己在进程内断言**，
换文件这招对它们完全无效——也就是说那 48 把刀的空转风险一直是零覆盖。

这一轮换一个方向：不退化判定，**退化变异本身**。

    空变异 = 把 MUTS 里的 (old, new) 换成 (old, old)

它等于"什么都没改"。此时：

  · 判定有牙 → 判定应该是**绿的** → 刀必须报「没抓住」→ 刀 exit != 0
  · 判定空转 → 刀照样报「N 刀全红」→ **exit 0**

这个方向不需要知道判定在哪、也不需要它是文件还是进程内断言——**对 78 个刀一视同仁**。
它和第二十二轮那个工具是互补的两半：

    换判定 → 证明「判定变坏时刀会红」
    空变异 → 证明「什么都没变时刀不会假红」

缺哪一半，那一半的空转就没人管。

分类
----
  CERTIFIED  空变异下刀 exit != 0（它发现"没东西可抓"）
  VACUOUS    空变异下刀 exit == 0（**什么都没改，它却说全抓到了**）
  SKIP       没有 `MUTS`，或 MUTS 的形状看不懂
  ERROR      起点本来就红 / 跑崩了 —— 无法判定

退出码：0 无 VACUOUS / 1 有 VACUOUS / 2 一把都没跑成

已知边界
--------
① 只认模块级的 `MUTS`（本仓的刀都是这个约定）。别的写法 → SKIP，不算通过。
② 空变异只证明"没东西可抓时它不会假红"，**不证明"有东西可抓时它抓得住"**。
   后者要靠每把刀自己的正向刀，以及 `check_knife_vacuity.py`。
③ 有的刀在 `main()` 里除了 MUTS 还有别的判据（比如"还原字节一致"），
   空变异下这些判据仍然会跑，所以 CERTIFIED 里混着"因为别的原因红"。
   这是有意的：本工具只回答"它有没有假绿"，不回答"它红得对不对"。

用法
----
    python tools/check_knife_noop.py                 # 全部
    python tools/check_knife_noop.py --only presence # 名字含 presence 的
    python tools/check_knife_noop.py --knife _qc_x_mutation.py
    python tools/check_knife_noop.py --root <另一个仓根>
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "dist", "build"}

# 已知空转的刀：**不是垃圾桶，是待办清单**。每条都要写清"为什么现在还空转、
# 什么条件下就能修"。新出现的空转不在这里 → 直接判失败（exit 1）。
#
# 为什么要这张表而不是"修完再上线"：这些刀的空转原因各不相同，有的要等真机样本，
# 有的要改判定的写法。把它们一次性修完不现实，但**让数量只减不增**是现实的。
KNOWN_VACUOUS = {
    "_qc_a5_probe_mutation.py":
        "判定 `tools/probe_a5_env.py` 在**本机就是红的**（7 PASS / 2 FAIL，"
        "`至少 1 条 [UI心跳] 被分类 ← 0`：本机 logs/ 里没有 UI 心跳样本）。"
        "判定恒红 → 任何变异之后它都会红 → 6 把刀'全红'是白送的。"
        "修法：给这把刀补 `_judged()` + `_baseline_green()`（第二十三/二十四轮那套），"
        "补完它会诚实地说'起点不是绿的'；再让 probe 在无样本时降级成 WARN 而不是 FAIL。",
}

# 在**子进程**里跑：import 刀 → 把 MUTS 改成空变异 → 调 main()。
# 用子进程而不是进程内，是因为刀会改源码；崩了也只是崩一个子进程，副本可以整份丢掉。
RUNNER = r'''# -*- coding: utf-8 -*-
import importlib.util
import os
import sys

# 【2026-11-08 修·仪器比现实窄】刀平时是 `python tools/_qc_x.py` 跑的，
# 那时 sys.path[0] 就是 `tools/`，所以 `import check_api_wiring` 这类
# **同目录兄弟模块**的 import 成立。本工具改成从临时目录跑 runner，
# sys.path[0] 变成了 runner 所在目录 —— 于是 `_qc_api_wiring_mutation`
# 一进来就 `ModuleNotFoundError: No module named 'check_api_wiring'`，
# 被记成 ERROR（无法判定）。**这不是刀的缺陷，是仪器把刀换了个环境跑。**
# 把刀所在目录放回 sys.path[0]，让它和平时完全一样地被跑。
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))

spec = importlib.util.spec_from_file_location("knife_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

if "--noop" not in sys.argv:
    sys.exit(m.main())          # 起点：原样跑

muts = getattr(m, "MUTS", None)
if muts is None:
    print("__NOOP_SKIP__ 没有 MUTS")
    raise SystemExit(0)


def _callables(e):
    return [i for i, x in enumerate(e) if callable(x)]


def _byteses(e):
    return [i for i, x in enumerate(e) if isinstance(x, (bytes, bytearray))]


def _noop(entry):
    """把一条变异改成"空变异"，并**返回证据**。

    【2026-11-08 修·第一版把「说明」当成了 new】第一版一律取「最后两个元素」当
    (old, new)。实测 `_qc_about_tier_mutation` 的形状是
    `(名字, old_thunk, new_thunk, 说明)` —— 于是它把 **说明** 换成了 new_thunk，
    空变异根本没发生，刀照常跑、照常全红，工具却判它 VACUOUS。
    **一个假发现，来自没自检的仪器。**

    现在按"载荷类型"定位，并且**改完要验证 old 与 new 是同一个对象**：
      · 两个以上可调用对象 → 最后两个（old_thunk / new_thunk）
      · 两个以上 bytes      → 最后两个（old / new）
      · 嵌套 `[(old, new), ...]` → 每对都改成 (old, old)
      · 只有 str（说明文字和 old/new 分不开）→ 抛，判 SKIP
    验证不过就抛 —— 宁可 SKIP，也不产出一个假的 VACUOUS。
    """
    e = list(entry)
    last = e[-1]
    if (isinstance(last, (list, tuple)) and len(last) > 0
            and isinstance(last[0], (list, tuple)) and len(last[0]) == 2):
        e[-1] = [(s[0], s[0]) for s in last]
        if any(p[0] is not p[1] for p in e[-1]):
            raise ValueError("嵌套空变异自检失败")
        return tuple(e), "nested"

    idx = _callables(e)
    how = "thunk"
    if len(idx) < 2:
        idx = _byteses(e)
        how = "bytes"
    if len(idx) < 2:
        raise ValueError("分不清 old / new（没有两个可调用对象或两个 bytes）")
    a, b = idx[-2], idx[-1]
    e[b] = e[a]
    if e[a] is not e[b]:                       # 自检：必须是同一个对象
        raise ValueError("空变异自检失败")
    return tuple(e), how


try:
    patched = [_noop(t) for t in muts]
except ValueError as exc:
    print("__NOOP_SKIP__ %s" % exc)
    raise SystemExit(0)

m.MUTS = [p[0] for p in patched]
print("__NOOP_ARMED__ %d 条（%s）"
      % (len(m.MUTS), ",".join(sorted({p[1] for p in patched}))))
sys.exit(m.main())
'''


def _has_muts(path: str) -> bool:
    try:
        t = ast.parse(io.open(path, encoding="utf-8", errors="replace").read(), filename=path)
    except (OSError, SyntaxError):
        return False
    for n in t.body:
        if isinstance(n, ast.Assign) and any(getattr(x, "id", "") == "MUTS" for x in n.targets):
            return isinstance(n.value, (ast.List, ast.Tuple))
    return False


def _copy_repo(root: str) -> str:
    d = tempfile.mkdtemp(prefix="qc_noop_")
    dst = os.path.join(d, "repo")
    shutil.copytree(root, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    return dst


def _pick_python(repo: str) -> str:
    p = os.path.join(repo, ".venv", "Scripts", "python.exe")
    return p if os.path.exists(p) else sys.executable


def _run(py: str, runner: str, knife: str, cwd: str, noop: bool) -> tuple[int, str]:
    argv = [py, "-u", runner, knife] + (["--noop"] if noop else [])
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _tail(out: str) -> str:
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return (lines[-1][:140] if lines else "<无输出>")


def certify(root: str, knife: str) -> tuple[str, list[str]]:
    """返回 (分类, 说明)。"""
    log: list[str] = []
    src = os.path.join(root, "tools", knife)
    if not os.path.isfile(src):
        return "ABSENT", ["本树没有 %s —— 不计失败，也不计通过" % knife]
    if not _has_muts(src):
        return "SKIP", ["没有模块级 MUTS —— 本工具不覆盖（不算通过）"]

    d = _copy_repo(root)
    try:
        repo = d                                  # _copy_repo 返回的就是副本仓根
        runner = os.path.join(os.path.dirname(d), "noop_runner.py")
        io.open(runner, "w", encoding="utf-8", newline="\n").write(RUNNER)
        py = _pick_python(repo)
        kp = os.path.join(repo, "tools", knife)

        # ① 起点必须绿：不绿的话"空变异也红"什么都不能说明
        code, out = _run(py, runner, kp, repo, noop=False)
        if code != 0:
            return "ERROR", ["起点本来就红（exit %d：%s）—— 无法判定" % (code, _tail(out))]
        log.append("✓ 起点绿（exit 0）")

        # ② 空变异：什么都没改，刀必须自己发现"没东西可抓"
        code2, out2 = _run(py, runner, kp, repo, noop=True)
        if "__NOOP_SKIP__" in out2:
            return "SKIP", [out2.split("__NOOP_SKIP__", 1)[1].splitlines()[0].strip()]
        if code2 == 0:
            return "VACUOUS", ["空变异下仍然 exit 0（%s）—— 它没在看变异改了什么" % _tail(out2)]
        log.append("✓ 空变异：exit %d（%s）" % (code2, _tail(out2)))
        return "CERTIFIED", log
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="空变异体检")
    ap.add_argument("--root", default=ROOT, help="仓根（默认本仓）")
    ap.add_argument("--only", default="", help="只跑名字含该子串的")
    ap.add_argument("--knife", action="append", default=[], help="显式指定刀文件名，可重复")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    tdir = os.path.join(root, "tools")
    if not os.path.isdir(tdir):
        print("✗ 找不到 %s —— 无法判定" % tdir, file=sys.stderr)
        return 2

    if args.knife:
        knives = [k for k in args.knife if args.only in k]
    else:
        knives = sorted(f for f in os.listdir(tdir)
                        if f.startswith("_qc_") and f.endswith(".py") and args.only in f)
    if not knives:
        print("✗ 一把都没选上（--only 过滤掉了全部？）无法判定", file=sys.stderr)
        return 2

    print("仓根：%s" % root)
    print("待检 %d 把刀\n" % len(knives))

    counts: dict[str, int] = {}
    vacuous: list[str] = []
    for k in knives:
        print("== %s" % k)
        kind, log = certify(root, k)
        for line in log:
            print("   " + line)
        print("   → %s\n" % kind)
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "VACUOUS":
            vacuous.append(k)

    order = ["CERTIFIED", "VACUOUS", "SKIP", "ABSENT", "ERROR"]
    print("结果：" + " / ".join("%s %d" % (x, counts[x]) for x in order if x in counts))

    ran = sum(counts.get(x, 0) for x in ("CERTIFIED", "VACUOUS"))
    if ran == 0:
        print("✗ 一把都没跑成，无法判定（全部 SKIP/ERROR 不算通过）", file=sys.stderr)
        return 2
    # 【2026-11-08 加】ERROR 单独点名。结果行里它只是一个数字，很容易被读成
    # "其余都过了"——而这正是第二十四轮 ABSENT 吞掉 13 对的那个坑。
    # 本工具的口径与 `check_knife_vacuity.py` 保持一致：ERROR 不计失败也不计通过，
    # 但必须**显式**说清有几把没验，否则 exit 0 会被当成"全部验过"。
    if counts.get("ERROR"):
        print("\n（另有 %d 把**无法判定** —— 不计失败，也不计通过；"
              "上面每把都写了原因）" % counts["ERROR"])
    if vacuous:
        new = [k for k in vacuous if k not in KNOWN_VACUOUS]
        known = [k for k in vacuous if k in KNOWN_VACUOUS]
        if known:
            print("\n（已知空转 %d 把，见 KNOWN_VACUOUS —— 不判失败，但只许减不许增）" % len(known))
            for k in known:
                print("   · " + k)
        if not new:
            return 0
        print("\n✗ 新出现的空转刀（它没在看变异改了什么）：", file=sys.stderr)
        for k in new:
            print("   " + k, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
