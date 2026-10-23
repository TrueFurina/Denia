# -*- coding: utf-8 -*-
"""闸门登记对账：长得像闸门的脚本，必须要么登记、要么写明为什么不用。

背景（「写了没进闸门」第四次复发）：2026-10-22 普查发现 `tools/` 下有 8 个
`check_*.py` / `test_*.py` 从未登记进 check_all——其中 4 个硬编码开发机 G:/H: 盘
路径（换机即废），1 个是要真实 Key 和声卡的外放探针。它们名字长得像闸门，新人
跑了报红还会以为是产品坏了；而它们验证的行为其实**没有**任何自动判据在守。

三条判据：
  1.【正向】`tools/` 与 `src/tests/` 顶层每个 `check_*.py` / `test_*.py`，
     要么出现在 `check_all.GATES` 的 argv 里，要么 docstring 首行附近带免登标记
     （token 见 MARKER 常量，**必须带原因**——不许裸标记，否则就是第二个「发现」栏）。
  2.【反向】GATES 里登记的每个脚本路径都真实存在（防改名/删除后登记项变哑，
     而汇总里还显示这一项「通过」）。
  3.【去重】同一脚本不许登记两次（2026-10-21 教训：重复登记白跑两遍还互相打架，
     光看汇总分不清是测试不稳还是跑了两遍）。

两个实现细节（都是踩过的坑）：
  - **用 AST 解析 check_all，不正则扫源码**：登记表注释里躺着已删重复登记的墓碑
    （`smoke_import.py` 在注释里出现第二次）→ 正则会把墓碑当成重复项判红。
  - **免登标记只认 docstring 行首**，且跳过本文件自己：否则本文件用代码里的
    MARKER 常量就把自己标成免登——正是「断言被自己的注释满足」。

覆盖数会打印出来看：登记 N / 免登 M（带原因列出）/ 待定 0。
退出码：0 = 全部有着落；1 = 有着落不明的文件、登记项失效或重复；2 = 闸门自身坏。
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHECK_ALL = os.path.join(HERE, "check_all.py")
SCAN_DIRS = ("tools", os.path.join("src", "tests"))
RUNNER = "tools/check_all.py"  # 登记表本身，不是闸门，不参与正向扫描
LOOKALIKE = re.compile(r"^(?:check|test)_.*\.py$")
# 免登标记：docstring 里行首的「不进闸门：<原因>」。原因不许空。
MARKER = "不进闸门"
MARKER_RE = re.compile(r"^\s*" + MARKER + r"[：:]\s*(\S.*)$")


def _load_gates() -> tuple[list[str], list[str], list[tuple]]:
    """从 check_all.py 用 AST 取登记信息。

    返回 (脚本路径列表, 结构错误列表, 整条 argv 签名列表)。argv 签名只用于去重——
    同一 argv 登记两次才是 10-21 那种有害重复（白跑两遍还互相打架）；共用同一个
    包装器跑不同测试（run_test_with_mem_retry + 两个测试）是合法设计。
    """
    with io.open(CHECK_ALL, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)  # 解析失败直接抛，由 main 记 error 判红
    errors: list[str] = []
    paths: list[str] = []
    sigs: list[tuple] = []
    found = False
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        if not any(isinstance(t, ast.Name) and t.id == "GATES" for t in targets):
            continue
        found = True
        if not isinstance(node.value, ast.List):
            errors.append("GATES 不是列表字面量")
            continue
        for i, el in enumerate(node.value.elts):
            if not isinstance(el, ast.Tuple) or len(el.elts) < 3:
                errors.append("GATES[%d] 不是四元组" % i)
                continue
            argv = el.elts[2]
            if not isinstance(argv, ast.List) or not argv.elts:
                errors.append("GATES[%d] 的 argv 为空或不是列表" % i)
                continue
            # 【2026-10-24 修】登记身份取 argv 里**所有**脚本路径，不只 argv[0]：
            # 包装器式登记（["tools/run_test_with_mem_retry.py", "src/tests/test_x.py"]）
            # 把真测试放在 argv[1]，只认 argv[0] 会把它误判成"未登记"。
            # 只认 .py/.js 结尾：`-m` 式条目的解释器路径（python/python.exe）不是脚本，
            # 收进来会撞出假重复。
            try:
                items = [ast.literal_eval(e) for e in argv.elts]
            except Exception as exc:  # noqa: BLE001
                errors.append("GATES[%d] argv 含非字面量: %s" % (i, exc))
                continue
            sigs.append(tuple(str(x) for x in items))
            got = False
            for it in items:
                if isinstance(it, str) and it.endswith((".py", ".js")):
                    paths.append(it)
                    got = True
            if not got:
                errors.append("GATES[%d] argv 里没有脚本路径" % i)
    if not found:
        errors.append("check_all.py 里找不到 GATES 定义")
    return paths, errors, sigs


def _docstring(path: str) -> tuple[str | None, str | None]:
    """返回 (docstring, 读取/解析错误)。"""
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception as exc:  # noqa: BLE001
        return None, "读取失败: %s" % exc
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return None, "语法错误: %s" % exc
    return ast.get_docstring(tree), None


def main() -> int:
    self_path = os.path.abspath(__file__)
    problems: list[str] = []

    try:
        registered, errors, sigs = _load_gates()
    except Exception as exc:  # noqa: BLE001
        print("✗ 闸门自身坏：解析 check_all.py 失败: %s" % exc)
        return 2
    if errors:
        for e in errors:
            print("✗ 登记表结构错误: %s" % e)
        return 1

    # 判据 3：去重。比对的是**整条 argv**——同一 argv 登记两次才是 10-21 那种有害
    # 重复（白跑两遍、通过/失败互相打架）；共用同一个包装器跑不同测试是合法设计。
    sig_seen: dict[tuple, int] = {}
    for s in sigs:
        sig_seen[s] = sig_seen.get(s, 0) + 1
    for s, n in sorted(sig_seen.items()):
        if n > 1:
            problems.append("重复登记 x%d: %s" % (n, " ".join(s)))

    # 判据 2：反向——登记的脚本都真实存在
    reg_set = {p for p in registered if "/" in p and not p.startswith("-")}
    for p in sorted(reg_set):
        if not os.path.isfile(os.path.join(ROOT, p.replace("/", os.sep))):
            problems.append("登记了但不存在的脚本: %s" % p)

    # 判据 1：正向——lookalike 要么登记、要么带原因免登
    exempt: list[tuple[str, str]] = []
    pending: list[str] = []
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d.replace("/", os.sep))
        if not os.path.isdir(base):
            problems.append("扫描目录不存在: %s" % d)
            continue
        for fn in sorted(os.listdir(base)):
            if not LOOKALIKE.match(fn):
                continue
            full = os.path.join(base, fn)
            if not os.path.isfile(full):
                continue
            # 登记表里一律是正斜杠，这里也必须用正斜杠比（Windows 上
            # os.path.join 给反斜杠，直接比会全部落空→假红）。
            rel = "%s/%s" % (d.replace(os.sep, "/"), fn)
            if rel == RUNNER or os.path.abspath(full) == self_path:
                continue
            if rel in reg_set:
                continue
            doc, err = _docstring(full)
            if err:
                problems.append("免登候选解析失败（记 error 判红）: %s → %s" % (rel, err))
                continue
            reason = None
            for line in (doc or "").splitlines():
                m = MARKER_RE.match(line)
                if m:
                    reason = m.group(1).strip()
                    break
            if reason:
                exempt.append((rel, reason))
            else:
                pending.append(rel)
    for rel in pending:
        problems.append("长得像闸门但既未登记也未写明免登原因: %s" % rel)

    # 覆盖数（必须打印出来看）
    n_dash_m = sum(1 for s in sigs if "-m" in s)
    print("登记 %d 项脚本 / 免登 %d 个 / 待定 %d 个（另有 %d 个 -m 式调用不参与对账）"
          % (len(reg_set), len(exempt), len(pending), n_dash_m))
    for rel, reason in exempt:
        print("  免登 %s —— %s" % (rel, reason[:80]))
    if problems:
        print("✗ 闸门登记对账失败，%d 处：" % len(problems))
        for p in problems:
            print("  ✗ %s" % p)
        return 1
    print("✓ 全部有着落：登记的都在、没登记的都有原因、没有重复登记。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
