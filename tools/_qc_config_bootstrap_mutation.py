# -*- coding: utf-8 -*-
"""「首启缺配置兜底」的变异验证：三刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_config_bootstrap_mutation.py

三刀各自对应一种"帮倒忙"：
  A 已存在也照抄模板 → 拿模板盖用户实配（数据丢失级）
  B 模板名清单改错 → 旁边有模板却说"没有"，等于没修
  C 复制失败还报 created → 谎报成功，用户拿到一个空配置
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/config.py"
TEST = "tools/test_config_bootstrap.py"
PY = ".venv/Scripts/python.exe"


def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 已存在也照抄模板（拿模板盖用户实配）",
     B('    if os.path.exists(path):\n        return ("exists", path)\n'),
     B('    if False:\n        return ("exists", path)\n')),

    ("B 模板名清单写错（旁边有模板也报 missing）",
     B('CONFIG_TEMPLATE_NAMES = ("config.release.yaml", "config.example.yaml", '
       '"config.template.yaml")'),
     B('CONFIG_TEMPLATE_NAMES = ("config.template.example.yaml",)')),

    ("C 复制失败仍报 created（谎报成功）",
     B('            except OSError as e:  # noqa: BLE001\n'
       '                return ("error", str(e))\n'),
     B('            except OSError as e:  # noqa: BLE001\n'
       '                return ("created", tpl)\n')),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = r.returncode != 0
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("三刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
