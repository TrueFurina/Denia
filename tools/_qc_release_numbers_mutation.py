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
     E('id="cta-sz">175.5 MB</small>'),
     E('id="cta-sz">999.9 MB</small>')),

    ("B 发布文档表格数字改成谎话",
     "site/PUBLISH.md",
     E("| 175.5 MB | 内置运行时"),
     E("| 999.9 MB | 内置运行时")),

    ("C 真值文件体积变了但站点没同步",
     "site/release.json",
     E('"setup_size_mb": 175.5,'),
     E('"setup_size_mb": 999.9,')),

    ("D 源码包 SHA 回填错",
     "site/index.html",
     E("da0aa0f8895e8ea24a97622aa6437e80407c0cec999e254be8e37cce04cebfc0"),
     E("0000000000000000000000000000000000000000000000000000000000000000")),

    ("E 整块数字被删掉（锚点消失不许静默跳过）",
     "site/index.html",
     E('<small id="cta-sz">175.5 MB</small>'),
     E('<small id="cta-sz"></small>')),
]


def main() -> int:
    orig = {rel: read(rel) for rel in {m[1] for m in MUTS}}
    h0 = {rel: hashlib.sha256(b).hexdigest() for rel, b in orig.items()}
    bad = 0
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
            caught = r.returncode != 0
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
