# -*- coding: utf-8 -*-
"""「UI 看门狗 COM 边界」的变异验证：四刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_ui_reload_boundary_mutation.py

四刀全部对应真实事故形态：跨线程 Reload 会冻结整个进程，闸门必须抓住。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/ui/api_window.py"
TEST = "tools/check_ui_reload_boundary.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 去掉 BeginInvoke（回到跨线程直接调）",
     E("                ctrl.BeginInvoke(_Act(_reload))\n"),
     E("                pass  # MUT: no BeginInvoke\n")),

    ("B 守护线程里直接碰 COM（16:55:50 冻结原形态）",
     E("                now = time.time()\n                last = self._last_ui_ping\n"),
     E("                now = time.time()\n"
       "                self.CoreWebView2.Reload()  # MUT: watchdog touches COM\n"
       "                last = self._last_ui_ping\n")),

    ("C 改掉 _reload 名字（闭包没被封送）",
     E("                def _reload():\n"),
     E("                def _reload_unused():\n")),

    ("D BeginInvoke 里不传 _reload（闭包定义了没用）",
     E("                ctrl.BeginInvoke(_Act(_reload))\n"),
     E("                ctrl.BeginInvoke(_Act(lambda: None))  # MUT\n")),
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
    print("四刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
