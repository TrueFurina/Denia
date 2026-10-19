# -*- coding: utf-8 -*-
"""变异验证：把 gewechat 的三条修复逐个改坏，确认新测试能抓住。

做法：备份 → 逐条注入变异 → 跑 src/channels/tests/test_gewechat_callback.py →
必须 FAIL → 还原 → 断言字节一致（防止"改坏了没还原"）。

用法：python tools/_qc_gewechat_mutation.py
退出码：0 = 每条变异都被抓住且还原成功；1 = 有变异没被抓住。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "src", "channels", "gewechat.py")
TEST = os.path.join(ROOT, "src", "channels", "tests", "test_gewechat_callback.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

NL = b"\n"
MUTS = [
    ("A 还原原 bug：同步 def + 直接调用", [
        (b"    async def _safe_post(self, fn, *args) -> None:",
         b"    def _safe_post(self, fn, *args) -> None:"),
        (b"            await asyncio.to_thread(fn, *args)",
         b"            fn(*args)"),
    ]),
    ("B 保持 async 但同步调用（阻塞循环）", [
        (b"            await asyncio.to_thread(fn, *args)",
         b"            fn(*args)"),
    ]),
    ("C 鉴权恒放行", [
        (b"        if not self._callback_token:" + NL + b"            return True",
         b"        if True:" + NL + b"            return True"),
    ]),
    ("D 去掉 fail-closed", [
        (b"        if self._callback_host not in _LOOPBACK_HOSTS and not self._callback_token:",
         b"        if False:"),
    ]),
    ("E 默认 host 回 0.0.0.0", [
        (b'getattr(gw, "callback_host", "") or "127.0.0.1"',
         b'getattr(gw, "callback_host", "") or "0.0.0.0"'),
    ]),
]


def main() -> int:
    orig = open(TARGET, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    print("baseline sha256:", h0[:16])
    bad = 0
    for name, subs in MUTS:
        b = orig
        for old, new in subs:
            if b.count(old) != 1:
                print(f"{name}: 变异锚点命中 {b.count(old)} 次（应为 1）→ 变异本身无效")
                bad += 1
                break
            b = b.replace(old, new)
        else:
            open(TARGET, "wb").write(b)
            r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", cwd=ROOT)
            caught = r.returncode != 0
            print(f"{name:34s} -> {'✓ 已 FAIL（rc=%d）' % r.returncode if caught else '❌ 未被抓住'}")
            if caught:
                err = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
                reason = err[-1][:150] if err else "(无 stderr)"
                print("     原因：", reason)
            else:
                bad += 1
        open(TARGET, "wb").write(orig)
        assert hashlib.sha256(open(TARGET, "rb").read()).hexdigest() == h0, "还原后字节不一致！"
    print("\n还原校验：字节一致 ✓")
    if bad:
        print(f"FAIL：{bad} 条变异未被抓住/无效")
        return 1
    print("PASS：全部变异都被抓住")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
