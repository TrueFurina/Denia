# -*- coding: utf-8 -*-
"""「check_all 互斥锁」的变异验证：五刀，刀刀必须被抓住。

跑法：python -u tools/_qc_gate_lock_mutation.py（用当前解释器，不依赖 .venv）

A 不清理陈旧锁        → 上次被强杀后所有门禁永久堵死
B 拿不到锁也报成功    → 两个会话又能同时改源码（锁形同虚设）
C release 无条件删锁   → 把别人的锁删掉
D 陈旧阈值改成 0       → 活着的锁也被偷
E 只看 OpenProcess     → 已退出但句柄仍被持有的 PID 被判"活着"，锁清不掉
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/_gate_lock.py"
TEST = "tools/test_gate_lock.py"
PY = sys.executable  # 干净 runner 上没有 .venv；子进程复用当前解释器


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 不清理陈旧锁（强杀后永远跑不了门禁）",
     E("            if (not info) or age > STALE_SECONDS or not alive:\n"),
     E("            if False:\n")),

    ("B 拿不到锁也报成功（互斥失效）",
     E('            return (False, "已有 check_all 在跑（PID %s，%.0fs 前启动）"\n'
       '                    % (info.get("pid", "?"), max(0.0, age)))\n'),
     E('            return (True, "")\n')),

    ("C release 无条件删锁（删掉别人的锁）",
     E("    if not info or int(info.get(\"pid\") or 0) == os.getpid():\n"),
     E("    if True:\n")),

    ("D 陈旧阈值改成 0（活锁也被偷）",
     E("STALE_SECONDS = 45 * 60"),
     E("STALE_SECONDS = 0")),

    ("E 只看 OpenProcess（已退出但句柄未释放 → 判活着，锁永远清不掉）",
     E("            return code.value == STILL_ACTIVE\n"),
     E("            return True\n")),
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
