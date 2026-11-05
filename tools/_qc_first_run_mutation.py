# -*- coding: utf-8 -*-
"""「陌生人首启判据链」的变异验证：每把刀都必须被 verify_first_run.py 抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_first_run_mutation.py

设计要点：**不改产品源码**。A–D 在临时 fixture 里注入「只有新用户才会撞」的
污染（隐私目录 / 开发机配置 / 缺模板 / 缺 doctor）；E–F 改判据链自己的源码
（忘了剥 Key / 把「未验清单」抹掉）。这样验证的是「链有没有牙」，而不是顺手
把产品弄坏再还原。

背景：本项目的验收动作自身曾三次撒谎（verify_deps 跑早了 / stage 内 doctor
读到开发机配置假全绿 / SIGTERM 把源码留在变异态）。所以这条链在判别人绿
之前，必须先用这些刀证明自己会红。
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import verify_first_run as V  # noqa: E402

CHAIN = "tools/verify_first_run.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


# E/F：改判据链自己的源码（二进制写入 + 逐刀还原 + 哈希校验）
SOURCE_MUTS = [
    ("E 链忘了剥 Key（哨兵会漏进验收现场）",
     E("            env.pop(k, None)\n"),
     E("            pass\n")),
    ("F 链把「没验的项目」清单抹掉（假装全验过）",
     E("HUMAN_ONLY = [\n"),
     E("HUMAN_ONLY = []\n_UNUSED = [\n")),
]


def run_chain(workdir: str, report: str) -> tuple[int, list[str]]:
    p = subprocess.run([PY, "-u", CHAIN, "--dir", workdir, "--report", report],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=420)
    lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
    return p.returncode, lines


def verdict(name: str, rc: int, lines: list[str], bad: list[str]) -> None:
    caught = rc != 0
    why = [l for l in lines if l.startswith("FAIL")] or lines or ["<无输出>"]
    print(name)
    print("   -> " + ("已抓住：" + why[-1][:96] if caught else "没抓住（判据链失效）"))
    if not caught:
        bad.append(name)


def fixture_muts(scratch: str, base: str, bad: list[str]) -> None:
    """A–D：在 fixture 里注入污染，每次用全新副本，无需还原。"""
    def fresh(tag: str) -> str:
        d = os.path.join(scratch, "work_" + tag)
        shutil.copytree(base, d)
        return d

    cases = []

    d = fresh("a")
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    with io.open(os.path.join(d, "data", "call_av.log"), "w", encoding="utf-8") as f:
        f.write("privacy residue\n")
    cases.append(("A 副本里混进 data/ 隐私目录（开发机残留）", d))

    d = fresh("b")
    with io.open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as f:
        f.write("app:\n  home: X:\\\\dev\\\\Denia\n")
    cases.append(("B 副本里带着开发机 config.yaml（新用户不会有的文件）", d))

    d = fresh("c")
    os.remove(os.path.join(d, "config.release.yaml"))
    cases.append(("C 出厂模板缺失（首启无从起配置）", d))

    d = fresh("d")
    os.remove(os.path.join(d, "tools", "doctor.py"))
    cases.append(("D doctor.py 缺失（交付件不完整）", d))

    for name, d in cases:
        rc, lines = run_chain(d, os.path.join(scratch, "report.json"))
        verdict(name, rc, lines, bad)


def source_muts(scratch: str, base: str, bad: list[str]) -> None:
    orig = io.open(CHAIN, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    for name, old, new in SOURCE_MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad.append(name)
            continue
        io.open(CHAIN, "wb").write(orig.replace(old, new, 1))
        try:
            rc, lines = run_chain(base, os.path.join(scratch, "report.json"))
            verdict(name, rc, lines, bad)
        finally:
            io.open(CHAIN, "wb").write(orig)
    ok = hashlib.sha256(io.open(CHAIN, "rb").read()).hexdigest() == h0
    print("\n链源码还原字节一致：%s" % ok)
    if not ok:
        bad.append("链源码还原")


def main() -> int:
    scratch = tempfile.mkdtemp(prefix="denia_first_run_qc_")
    bad: list[str] = []
    try:
        print("构造基准 fixture（git ls-files 干净副本，只造一次）")
        base = V.make_copy(None, scratch)
        print("  副本：%s\n" % base)
        fixture_muts(scratch, base, bad)
        source_muts(scratch, base, bad)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    total = 4 + len(SOURCE_MUTS)
    print("\n%d 刀全红" % total if not bad else "有 %d 处异常：%s" % (len(bad), ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
