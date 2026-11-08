# -*- coding: utf-8 -*-
"""「发布口径对账」的变异验证：每把刀都必须被 check_release_numbers.py 抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_release_numbers_mutation.py

为什么是这五刀：口径漂移有五种真实形态——改页面没改真值、改真值没同步、
改文档没改页面、SHA 回填错、以及「整块数字被删掉导致锚点消失」。最后一刀
专治「闸门只比数值、锚点没了就静默跳过」这种假绿（本项目踩过）。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = ".venv/Scripts/python.exe"
TEST = "tools/check_release_numbers.py"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是闸门**崩了**
# 也被算成"抓住"。实测：把 check_release_numbers.py 整个换成一句
# `raise RuntimeError("闸门自己崩了")`，本刀仍然逐条打印「已抓住：<无输出>」、
# 最后打印「N 刀全红」并 exit 0 —— **一个根本跑不起来的闸门被认证成"五刀全抓"**。
# 崩了和识别是两件事：前者说明闸门没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的闸门能让**每一把**刀都变红，那种"全红"什么都不证明。
def _judged(r) -> bool:
    """闸门是「判出了问题」而不是「崩了」或「什么都没说」。"""
    out = (r.stdout or "") + (r.stderr or "")
    if "Traceback (most recent call last)" in out:
        return False
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "Traceback" not in out


def E(s: str) -> bytes:
    return s.encode("utf-8")


def read(rel: str) -> bytes:
    with io.open(rel, "rb") as f:
        return f.read()


def write(rel: str, data: bytes) -> None:
    with io.open(rel, "wb") as f:
        f.write(data)


MUTS = [
    ("A 页面 CTA 数字改成谎话（改页面没改真值）",
     "site/index.html",
     E('id="cta-sz">175.0 MB</small>'),
     E('id="cta-sz">999.9 MB</small>')),

    ("B 发布文档表格数字改成谎话",
     "site/PUBLISH.md",
     E("| 175.0 MB | 内置运行时"),
     E("| 999.9 MB | 内置运行时")),

    ("C 真值文件体积变了但站点没同步",
     "site/release.json",
     E('"setup_size_mb": 175.0,'),
     E('"setup_size_mb": 999.9,')),

    ("D 安装包 SHA 回填错（shaSetup 与真值不一致）",
     "site/index.html",
     E("9ec828efd15617a52a6261ed479c7d14c4bcaaae723ecfdce24728249fae2943"),
     E("0000000000000000000000000000000000000000000000000000000000000000")),

    ("E 整块数字被删掉（锚点消失不许静默跳过）",
     "site/index.html",
     E('<small id="cta-sz">175.0 MB</small>'),
     E('<small id="cta-sz"></small>')),
]


def main() -> int:
    orig = {rel: read(rel) for rel in {m[1] for m in MUTS}}
    h0 = {rel: hashlib.sha256(b).hexdigest() for rel, b in orig.items()}
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的闸门能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")
    for name, rel, old, new in MUTS:
        n = orig[rel].count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        write(rel, orig[rel].replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = _judged(r)
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1][:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（闸门失效）"))
            if not caught:
                bad += 1
        finally:
            write(rel, orig[rel])

    ok = all(hashlib.sha256(read(rel)).hexdigest() == h
             for rel, h in h0.items())
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("%d 刀全红" % len(MUTS) if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
