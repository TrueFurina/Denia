# -*- coding: utf-8 -*-
"""「密钥盒加密」闸门的变异验证：五刀，刀刀必须被抓住。

为什么补这把刀：`src/tests/test_secret_box.py` 守的是**磁盘上不许出现明文 Key** ——
这是这个产品最硬的一条安全底线（用户的 OpenAI / 百炼 / QQ cookie 全在里面）。
它 20 条判据从 2026-10-18 起就在跑，**从来没有刀**。而这类判据的失效方式特别阴：
加密函数照常返回、`Config.save` 照常写盘、日志照常打「已加密」，只是密文里其实
还是明文 —— 没有任何报错，只有某天有人 `cat config.yaml` 才看得见。

五刀各打一条判据：
  S1 密文不带 `dpapi:` 前缀（等于没加密）        → 「密文带 dpapi: 前缀」红
  S2 `seal_tree` 不递归进嵌套 dict               → 「嵌套 dict 已加密」红
  S3 密钥字段表加宽（把 `name` 也当密钥）        → 「空串与非密钥字段不动」红
  S4 解不开的密文不记失败（换机时静默丢 Key）    → 「解不开的密文给出降级提示」红
  S5 `is_sealed` 恒 False（谁都不当密文）        → 「encrypt→decrypt 往返还原」红

判据三件套（沿用本仓 2026-11-08 的修法）：
  · `_judged()` —— 非零退出**且**输出里有 `  ✗ ` 行，崩溃（Traceback）不算抓住；
  · `_baseline_green()` —— 起点必须先绿，恒红的判定能让每把刀都变红；
  · 每刀都备份字节、finally 还原并核对 sha256，刀自己不许把源码弄坏。

【为什么不用「is_sealed 恒 True」那把】试过：恒 True 会让 `decrypt_secret(PLAIN)`
去 base64 解一段普通文本，`validate=True` 直接抛 binascii.Error，脚本带 Traceback
退出 —— 那是「崩了」不是「认出来了」。改成恒 False 才是判据该红的那条路。
**刀的变异方向要选判据能开口的那条，不能选让判据闭嘴的那条。**

跑法：.venv/Scripts/python.exe -u tools/_qc_secret_box_mutation.py
退出码：0 = 五刀全红；1 = 有刀没被抓住；77 = 本机跑不了（缺依赖等）。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = sys.executable
GATE = os.path.join("src", "tests", "test_secret_box.py")
BOX = os.path.join("src", "secret_box.py")

# (标签, 目标文件, 原文锚点, 替换文本, 期望出现的失败关键词)
MUTS = [
    ("S1 密文不带 dpapi: 前缀",
     BOX,
     '    return _PREFIX + base64.b64encode(blob).decode("ascii")',
     '    return base64.b64encode(blob).decode("ascii")  # 变异刀：丢掉前缀',
     "密文带 dpapi: 前缀"),
    ("S2 seal_tree 不递归进嵌套 dict",
     BOX,
     "                n += seal_tree(v, p)",
     "                pass  # 变异刀：嵌套层不再往下走",
     "嵌套 dict 已加密"),
    ("S3 密钥字段表加宽（把 name 也当密钥）",
     BOX,
     '_SECRET_FIELDS = frozenset({"api_key", "cookie"})',
     '_SECRET_FIELDS = frozenset({"api_key", "cookie", "name"})  # 变异刀：加宽',
     "空串与非密钥字段不动"),
    ("S4 解不开的密文不记失败",
     BOX,
     '                    failed.append(f"{p}（{e}）")',
     "                    pass  # 变异刀：解不开也不记，换机时静默丢 Key",
     "解不开的密文给出降级提示"),
    ("S5 is_sealed 恒 False",
     BOX,
     "    return isinstance(value, str) and value.startswith(_PREFIX)",
     "    return False  # 变异刀：谁都不当密文",
     "encrypt→decrypt 往返还原明文"),
]

# 给 `check_knife_noop.py` 认形状用：第 2 项是 old、第 3 项是 new（下标从 0 起）。
NOOP_SHAPE = ("idx", 2, 3)


def _read(p: str) -> bytes:
    with io.open(p, "rb") as f:
        return f.read()


def _write(p: str, data: bytes) -> None:
    with io.open(p, "wb") as f:
        f.write(data)


def _run() -> tuple:
    p = subprocess.run([PY, "-u", GATE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _judged(rc: int, out: str) -> bool:
    """「判出了问题」而不是「崩了」：非零退出 **且** 有 `  ✗ ` 行。"""
    return rc != 0 and "  ✗ " in out


def _baseline_green() -> tuple:
    rc, out = _run()
    if rc == 77:
        return True, "77"
    return (rc == 0 and "✗" not in out), ""


def main() -> int:
    ok_green, why77 = _baseline_green()
    if why77:
        print("  [SKIP] 起点 exit 77：本机缺依赖，一道判据都没跑（不是通过）")
        return 77
    if not ok_green:
        print("  FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，"
              "这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")

    bad = 0
    for label, target, old, new, keyword in MUTS:
        orig = _read(target)
        sha0 = hashlib.sha256(orig).hexdigest()
        text = orig.decode("utf-8")
        n = text.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (label, n))
            bad += 1
            continue
        try:
            _write(target, text.replace(old, new).encode("utf-8"))
            rc, out = _run()
            caught = _judged(rc, out)
            hit = keyword in out
            if caught and hit:
                print("  PASS %s → 红了，且点名「%s」" % (label, keyword))
            elif caught:
                print("  FAIL %s → 红了，但没点名「%s」（可能是别的原因崩的）"
                      % (label, keyword))
                for ln in [x for x in out.splitlines() if "  ✗ " in x][:3]:
                    print("        | " + ln.strip()[:150])
                bad += 1
            else:
                print("  FAIL %s → 没抓住（闸门失效）" % label)
                bad += 1
        finally:
            _write(target, orig)
            if hashlib.sha256(_read(target)).hexdigest() != sha0:
                print("  FAIL %s：还原后字节不一致！" % label)
                bad += 1

    rc, out = _run()
    if rc == 0 and "✗" not in out:
        print("  PASS 还原后仍然绿")
    else:
        print("  FAIL 还原后不是绿的 —— 刀自己把源码弄坏了")
        bad += 1

    print("\n%s" % ("五刀全红" if bad == 0 else "有 %d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
