# -*- coding: utf-8 -*-
"""「产品必需资产」闸门的变异验证：三刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_bundled_assets_mutation.py

每刀都先备份原始字节，finally 里还原并核对 sha256 —— 这个闸门守的是素材，
刀本身绝不能把素材弄坏。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

TEST = "tools/test_bundled_assets.py"
PY = ".venv/Scripts/python.exe"
TARGET = "src/ui/web/_role_assets/ref/xiaowei.wav"


def run() -> tuple:
    r = subprocess.run([PY, "-u", TEST], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
    why = [l for l in lines if l.strip().startswith("FAIL")]
    why = (why or lines or ["<无输出>"])[-1].strip()[:88]
    return r.returncode != 0, why


def main() -> int:
    if not os.path.isfile(TARGET):
        print("目标资产不存在，无法变异：", TARGET)
        return 1
    orig = open(TARGET, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0

    print("A 资产被删（改名）→ 必须红")
    os.rename(TARGET, TARGET + ".hidden")
    try:
        caught, why = run()
        print("   -> " + ("已抓住：" + why if caught else "没抓住（闸门失效）"))
        if not caught:
            bad += 1
    finally:
        os.rename(TARGET + ".hidden", TARGET)

    print("B 资产被截断成空壳 → 必须红")
    open(TARGET, "wb").write(b"")
    try:
        caught, why = run()
        print("   -> " + ("已抓住：" + why if caught else "没抓住（闸门失效）"))
        if not caught:
            bad += 1
    finally:
        open(TARGET, "wb").write(orig)

    print("C 扩展名不改、内容换成假格式 → 必须红")
    open(TARGET, "wb").write(b"not a wav at all" * 5000)
    try:
        caught, why = run()
        print("   -> " + ("已抓住：" + why if caught else "没抓住（闸门失效）"))
        if not caught:
            bad += 1
    finally:
        open(TARGET, "wb").write(orig)

    ok = hashlib.sha256(open(TARGET, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("三刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
