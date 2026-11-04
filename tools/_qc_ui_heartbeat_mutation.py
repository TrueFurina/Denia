# -*- coding: utf-8 -*-
"""「UI 心跳恢复策略」的变异验证：五刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_ui_heartbeat_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/ui/heartbeat_policy.py"
TEST = "tools/test_ui_heartbeat.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 不看首跳 armed（预览模式也乱判死）",
     E("        if not armed:\n            return (\"idle\", \"尚未收到首跳\")\n"),
     E("        if False:\n            return (\"idle\", \"尚未收到首跳\")\n")),

    ("B 取消启动宽限期",
     E("        if now - boot_ts < self.grace:\n"),
     E("        if False:\n")),

    ("C 取消冷却（重载风暴）",
     E("        if self.last_attempt and now - self.last_attempt < self.cooldown:\n"),
     E("        if False:\n")),

    ("D 取消次数上限（永不放弃）",
     E("        if self.attempts >= self.max_attempts:\n"),
     E("        if False:\n")),

    ("E 心跳恢复不清预算（下次不再恢复）",
     E("        if gap <= self.timeout:\n            self.on_ping(now)\n"),
     E("        if gap <= self.timeout:\n            pass\n")),
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
    print("五刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
