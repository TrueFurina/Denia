# -*- coding: utf-8 -*-
"""空写体检：把写入口封住，看刀还认不认得出来。

为什么还需要第三个方向
----------------------
前两轮各堵了一半，而且都只堵"有 MUTS 的刀"：

    check_knife_vacuity.py   退化**判定文件**  → 只对"判定是独立文件"的 28 对有效
    check_knife_noop.py      退化**变异本身**（(old,new) → (old,old)）→ 只认模块级 MUTS

本仓 61 把 `_qc_*_mutation.py` 里有 27 把这两招都够不着：它们把 (old,new) 写在函数
里，或者**根本不改源码**——往 `src/` 扔一个语法坏文件、复制一份配置、删掉一个目录。
对它们来说"变异"不是一次字符串替换，所以前两招都无从下手。这 27 把的空转风险
一直是零覆盖，每一轮的报告里都写着「SKIP 不算通过」。

这一轮再换一个方向：**不碰变异，封住写入口。**

    把 builtins.open / io.open 的**写模式**重定向到临时文件
    → 目标路径一个字节都不会变
    → 等于"变异根本没发生"

此时：

  · 判定有牙 → 它看不见任何变化 → 刀必须报「没抓住」→ exit != 0
  · 判定空转 → 刀照样报「N 刀全红」→ **exit 0**

这个方向和变异长什么样无关：改源码的、扔坏文件的、删目录的，一视同仁。
三个方向合起来才完整：

    换判定  → 证明「判定变坏时刀会红」
    空变异  → 证明「源码没变时刀不会假红」（仅限字符串替换型变异）
    封写    → 证明「**什么都没变**时刀不会假红」（不限变异形态）

分类
----
  CERTIFIED          封写下刀 exit != 0（它发现"没东西可抓"）
  VACUOUS            封写下刀 exit == 0（**什么都没变，它却说全抓到了**）
  COVERED_ELSEWHERE  有模块级 MUTS —— 归 `check_knife_noop.py`，本工具不重复算
  META               质检仪器自身的刀（`_qc_knife_*`）—— 由各自门禁互检，不判
  SKIP               没有 main() 也没有顶层 exit（看不出它是刀）；
                     或起点就 exit 77（缺环境，未验证 —— 不是通过，也不是判据失败）
  ERROR              起点本来就红 / 封写把刀自己弄崩了 —— 无法判定
  ABSENT             本树没有这把刀

退出码：0 无 VACUOUS / 1 有 VACUOUS / 2 一把都没跑成

已知边界（必须和读数一起读）
----------------------------
① CERTIFIED 只说明「它不会假绿」，**不说明它红得对**。封写把刀弄崩也会红，
   所以崩溃单独归 ERROR，不计 CERTIFIED —— 这条是实测踩出来的：第一版用
   `io.BytesIO` 当写目标，17 把刀里 7 把崩在 `TypeError: a bytes-like object
   is required`，差点被记成"认出来了"。
② 有 2 把刀（`_qc_dialogue_voice_chain_mutation`、`_qc_wake_mutation`）的机制
   本身就要求"写出一份改过的模块再 import 它"——封写等于抽掉它们的前提，
   它们必然崩在 `ModuleNotFoundError` / `FileNotFoundError`，归 ERROR。
   这不是它们的缺陷，是本工具对这类刀无能为力。
③ 只封 `builtins.open` / `io.open` 的写模式。走 `os.open` + `os.write` 的
   旁路封不住（本仓目前没有刀这么干）。
④ **两遍之间要把工作区还原**（`_qc_repo_copy.reset()`：`git checkout -- .` +
   `git clean -fd`，不带 `-x`）。不还原的话，起点那次留在盘上的产物会替封写
   那次作弊（`probe.txt` 还在、`gen_mod.py` 还能 import），好刀被误判空转 ——
   实测两把桩刀同时踩中。这条不是"顺手清理"，是结论正确性的前提。

用法
----
    python tools/check_knife_nowrite.py                 # 全部（27 把）
    python tools/check_knife_nowrite.py --only wake      # 名字含 wake 的
    python tools/check_knife_nowrite.py --knife _qc_x_mutation.py
    python tools/check_knife_nowrite.py --root <另一个仓根>
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _qc_repo_copy import copy_repo, reset  # noqa: E402

# 已知空转的刀：**不是垃圾桶，是待办清单**。每条都要写清"为什么现在还空转、
# 什么条件下就能修"。新出现的空转不在这里 → 直接判失败（exit 1）。
#
# **空表是有意义的读数**：它说的是"没有已知空转"，不是"没有空转"。
# 真要新增条目，必须同时写清原因和修法，否则下一次它就成了遮羞布。
KNOWN_VACUOUS: dict[str, str] = {}


# 在**子进程**里跑：import 刀 → 把写入口封住 → 调 main()。
# 用子进程而不是进程内，是因为刀会改源码、还会 patch 全局状态；崩了只崩一个子进程。
RUNNER = r'''# -*- coding: utf-8 -*-
import atexit, builtins, importlib.util, io, os, shutil, sys, tempfile

BLOCK = "--nowrite" in sys.argv

_real = builtins.open
_scratch = None


def _spill():
    """给每次写打开一个**真临时文件**。

    为什么不是内存里的 BytesIO：刀会往文本模式写（`TypeError: a bytes-like object
    is required`）、会把句柄交给 subprocess 或 os.fsync（`io.UnsupportedOperation:
    fileno`）。真文件两个问题都没有，而且语义和平时完全一样——只是写到了别处。
    """
    global _scratch
    if _scratch is None:
        _scratch = tempfile.mkdtemp(prefix="nowrite_")
        atexit.register(shutil.rmtree, _scratch, True)
    fd, path = tempfile.mkstemp(dir=_scratch)
    os.close(fd)
    return path


def _guard(file, mode="r", *a, **kw):
    if BLOCK and isinstance(mode, str) and any(c in mode for c in "wxa+"):
        return _real(_spill(), mode, *a, **kw)
    return _real(file, mode, *a, **kw)


if BLOCK:
    builtins.open = _guard
    io.open = _guard

# 刀平时是 `python tools/_qc_x.py` 跑的，那时 sys.path[0] 就是 tools/，
# 所以 `import check_api_wiring` 这类同目录兄弟模块的 import 成立。
# 这里从临时目录起跑，sys.path[0] 变了 —— 把它放回去，让刀和平时一样地被跑。
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))

spec = importlib.util.spec_from_file_location("knife_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)          # 脚本式刀（顶层就干活）在这里自己 sys.exit()

if not hasattr(m, "main"):
    print("__NO_MAIN__")
    raise SystemExit(0)

raise SystemExit(m.main())
'''


def _pick_python(root: str) -> str:
    """优先用仓里的 venv 解释器。

    为什么：`check_all.py` 跑闸门用的是 `PY_VENV`。刀里常见 `PY = sys.executable`
    再拿它去起子进程跑闸门 —— 如果本工具用裸 `python` 起刀，闸门就落在一个
    没装依赖的解释器上，`import yaml` 直接 ModuleNotFoundError，刀被读成
    "起点红 / 判据失败"。实测：`_qc_config_keys_mutation` 就是这么假红的。
    """
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = os.path.join(root, ".venv", *rel)
        if os.path.isfile(p):
            return p
    return sys.executable


def _has_module_muts(src: str) -> bool:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    return any(isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "MUTS" for t in n.targets)
               for n in tree.body)


def _looks_like_knife(src: str) -> bool:
    """有 main()、或顶层就 sys.exit() —— 否则它更像个被 import 的辅助模块。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    if any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in ast.walk(tree)):
        return True
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "exit" for n in ast.walk(tree))


_CRASH_TAIL = re.compile(r"^[\w.]*(Error|Exception)\b")


def _crashed(out: str) -> bool:
    """未捕获异常 → 刀没跑完它的判据。

    只看最后一行：刀在中间打印被测代码的 traceback 是常事（那是有意的），
    真正的"崩了"是最后一行还停在异常上。
    """
    if "Traceback (most recent call last)" not in out:
        return False
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return bool(lines) and bool(_CRASH_TAIL.match(lines[-1]))


def _tail(out: str) -> str:
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return (lines[-1][:140] if lines else "<无输出>")


def _run(py: str, runner: str, knife: str, cwd: str, block: bool) -> tuple[int, str]:
    argv = [py, "-u", runner, knife] + (["--nowrite"] if block else [])
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def certify(root: str, knife: str) -> tuple[str, list[str]]:
    """返回 (分类, 说明)。"""
    log: list[str] = []
    src = os.path.join(root, "tools", knife)
    if not os.path.isfile(src):
        return "ABSENT", ["本树没有 %s —— 不计失败，也不计通过" % knife]
    text = io.open(src, encoding="utf-8", errors="replace").read()
    if knife.startswith("_qc_knife_"):
        # 质检仪器自身的刀（`_qc_knife_noop_mutation` / `_qc_knife_vacuity_mutation`
        # / 本工具的刀）。它们不测产品，测的是**别的体检工具**，而且内部会再跑
        # 整轮普查 —— 放进这里等于嵌套十几分钟，还只验了"仪器验仪器"。
        # 它们的牙齿由各自的门禁负责：两道都已登记进 GATES，check_all 跑红就拦住。
        # 单列一类而不是静默跳过 —— 静默跳过正是这一轮在修的病。
        return "META", ["质检仪器自身的刀 —— 由各自门禁互检，本工具不判（不算通过）"]
    if _has_module_muts(text):
        return "COVERED_ELSEWHERE", ["有模块级 MUTS —— 归 check_knife_noop.py（不重复算）"]
    if not _looks_like_knife(text):
        return "SKIP", ["既没有 main() 也没有顶层 sys.exit() —— 看不出它是刀（不算通过）"]

    # 【两遍之间必须把工作区还原】这一条是实测踩出来的：起点跑和封写跑共用
    # 一份没还原的副本时，起点那次**留在盘上的产物**会替封写那次作弊。两个桩刀
    # 当场露馅：
    #   · 老实刀把 "mutated" 写进 probe.txt，起点跑留下了它；封写时写入被吞，
    #     但 `isfile(TARGET)` 仍然为真、内容仍然对 → 打印 PASS → 被误判 VACUOUS；
    #   · 生成模块再 import 的刀，起点跑把 gen_mod.py 留在了 tools/ 里；封写时
    #     文件不再生成，可 `import gen_mod` 从残留文件里成功了 → 同样误判 VACUOUS。
    # 方向上是"把好刀记成空转"，比漏报更吵，但仍然是**仪器在说谎**：它测到的是
    # 上一次的副作用，不是这一次的变异。
    # 还原走 `_qc_repo_copy.reset()`（副本本来就是 git 仓，checkout + clean 即可），
    # 不重新复制一份 —— 复制 61 次比还原 61 次慢一个数量级。
    d = os.path.dirname(copy_repo(root))
    try:
        repo = os.path.join(d, "repo")
        runner = os.path.join(d, "nowrite_runner.py")
        io.open(runner, "w", encoding="utf-8", newline="\n").write(RUNNER)
        py = _pick_python(repo)
        kp = os.path.join(repo, "tools", knife)
        # ① 起点必须绿：不绿的话"封写也红"什么都不能说明
        code, out = _run(py, runner, kp, repo, block=False)
        if code == 77:
            # 77 是刀自己举手说"这台机器缺东西，我没验"（例如 jsdom 没装）。
            # 这跟"判据失败"是两件事：记 ERROR 会把环境问题算成刀坏了，
            # 反过来记 CERTIFIED 更是胡说。单列 SKIP。
            return "SKIP", ["起点 exit 77（%s）—— 缺环境，未验证（不是通过）" % _tail(out)]
        if code != 0:
            if _crashed(out):
                return "ERROR", ["起点就崩了（%s）—— 无法判定" % _tail(out)]
            return "ERROR", ["起点本来就红（exit %d：%s）—— 无法判定" % (code, _tail(out))]
        log.append("✓ 起点绿（exit 0）")

        # ② 封写：一个字节都没改，刀必须自己发现"没东西可抓"
        # 先把工作区还原到起点（见上面那段注释），再用同一份副本跑第二遍。
        reset(repo)
        code2, out2 = _run(py, runner, kp, repo, block=True)
        if "__NO_MAIN__" in out2:
            return "SKIP", ["顶层跑完但没有 main()，且顶层没给出结论 —— 看不出它是刀"]
        if _crashed(out2):
            # 封写把刀弄崩 ≠ 它认出了"没变化"。这两者必须分开，否则本工具就在自欺。
            return "ERROR", ["封写下刀自己崩了（%s）—— 它的机制要求真写文件，本工具无能为力"
                             % _tail(out2)]
        if code2 == 0:
            return "VACUOUS", ["封写下仍然 exit 0（%s）—— 它没在看变异改了什么" % _tail(out2)]
        log.append("✓ 封写：exit %d（%s）" % (code2, _tail(out2)))
        return "CERTIFIED", log
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="空写体检")
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
                        if f.startswith("_qc_") and f.endswith("_mutation.py")
                        and args.only in f)
    if not knives:
        print("✗ 一把都没选上（--only 过滤掉了全部？）无法判定", file=sys.stderr)
        return 2

    print("仓根：%s" % root)
    print("待检 %d 把刀\n" % len(knives))

    # 【表会腐烂】KNOWN_VACUOUS 是"允许空转"的额度。额度只减不增还不够，它还得
    # 跟得上现实，否则会以两种方式变成遮羞布：
    #   ① 名字还在表里、刀已经删了/改名了 → 这条额度永远用不上，却一直占着名额；
    #   ② 名字还在表里、刀已经被修好不再空转 → 表在说"我知道它坏着"，其实不坏了。
    # 两种都是"声称的现状 ≠ 实际现状"。全量跑时对账一次：对不上就 exit 2。
    if not args.knife and not args.only and KNOWN_VACUOUS:
        dead = [k for k in KNOWN_VACUOUS if not os.path.isfile(os.path.join(tdir, k))]
        if dead:
            print("✗ KNOWN_VACUOUS 里有不存在的刀（额度指向空气）：", file=sys.stderr)
            for k in dead:
                print("   " + k, file=sys.stderr)
            return 2

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

    order = ["CERTIFIED", "VACUOUS", "COVERED_ELSEWHERE", "META", "SKIP", "ABSENT", "ERROR"]
    print("结果：" + " / ".join("%s %d" % (x, counts[x]) for x in order if x in counts))

    ran = sum(counts.get(x, 0) for x in ("CERTIFIED", "VACUOUS"))
    if ran == 0:
        print("✗ 一把都没跑成，无法判定（全部 SKIP/ERROR 不算通过）", file=sys.stderr)
        return 2

    # ERROR / COVERED_ELSEWHERE 单独点名。结果行里它们只是数字，很容易被读成
    # "其余都过了"——而那正是 ABSENT 吞掉 13 对的那个坑。
    if counts.get("ERROR"):
        print("\n（另有 %d 把**无法判定** —— 不计失败，也不计通过；"
              "上面每把都写了原因）" % counts["ERROR"])
    if counts.get("META"):
        print("（%d 把是质检仪器自身的刀 —— 由各自门禁互检，本工具不判）"
              % counts["META"])
    if counts.get("COVERED_ELSEWHERE"):
        print("（%d 把有模块级 MUTS，归 check_knife_noop.py —— 本工具不重复算）"
              % counts["COVERED_ELSEWHERE"])

    if not args.knife and not args.only:
        stale = sorted(set(KNOWN_VACUOUS) - set(vacuous))
        if stale:
            print("\n✗ KNOWN_VACUOUS 里有已经不空转的刀 —— 表在说「我知道它坏着」，"
                  "其实已经不坏了：", file=sys.stderr)
            for k in stale:
                print("   " + k, file=sys.stderr)
            print("   删掉这些条目。额度留着不用不会变安全，只会变成遮羞布。",
                  file=sys.stderr)
            return 2

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
