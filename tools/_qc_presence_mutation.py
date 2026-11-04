# -*- coding: utf-8 -*-
"""「在场感增强」的变异验证：六刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_presence_mutation.py

六刀对应六条最容易悄悄退化的契约：
  A 最大化重新被当成全屏（用户一最大化浏览器她就消失）
  B 退出全屏没有迟滞（切一下窗她闪一下）
  C 抢用户手动隐藏的桌宠（退出全屏把她翻出来）
  D 非法组合键不再校验（写错就吞掉热键）
  E 重复组合键不再去重（后一个覆盖前一个）
  F 配置关闭也被无视（用户关不掉）
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P_GUARD = "src/system/fullscreen_guard.py"
P_HOT = "src/system/hotkeys.py"
TEST = "tools/test_presence_controls.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    (P_GUARD, "A 最大化重新算全屏（P0-2 最致命的回退）",
     E("        if zoomed:\n            return False\n"),
     E("        if False:\n            return False\n")),

    (P_GUARD, "B 退出全屏没有迟滞",
     E("        if self._clear_streak < self.show_after:\n            return None\n"),
     E("        if False:\n            return None\n")),

    (P_GUARD, "C 抢用户手动隐藏的桌宠",
     E("                self._suppressed = not pet_visible\n"),
     E("                self._suppressed = False\n")),

    (P_HOT, "D 非法组合键不校验",
     E("        if not _validate(str(combo)):\n"),
     E("        if False:\n")),

    (P_HOT, "E 重复组合键不去重",
     E("        if key in out:\n"),
     E("        if False:\n")),

    (P_HOT, "F 配置关了也照起热键",
     E('    if not isinstance(section, dict) or not section.get("hotkeys_enabled", False):\n'),
     E("    if False:\n")),
]


def main() -> int:
    originals = {}
    for path, *_ in MUTS:
        if path not in originals:
            originals[path] = open(path, "rb").read()
    hashes = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    bad = 0

    for path, name, old, new in MUTS:
        orig = originals[path]
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(path, "wb").write(orig.replace(old, new, 1))
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
            for p, b in originals.items():
                open(p, "wb").write(b)

    ok = all(hashlib.sha256(open(p, "rb").read()).hexdigest() == h
             for p, h in hashes.items())
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("六刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
