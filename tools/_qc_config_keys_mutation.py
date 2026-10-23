# -*- coding: utf-8 -*-
"""check_config_keys 的变异刀：两刀注入 + 对照。

  M1 往出厂模板插一个死键（zzz_dead_probe_key）→ 闸门必须红并点名该键
     （这就是 2026-10-24 删掉的那 24 个键的形态；插一个未来的，证明闸门还认得）
  M2 抽掉豁免清单里的 ollama.options 子树 → 4 个透传键必须红
     （证明例外清单是load-bearing 的，不是永远豁免的装饰品）
  对照 → 全绿；收尾再跑一次（还原后必须仍绿）

纪律：二进制读写；锚点动态取且必须命中一次；还原后字节逐字节核对；
断言红在具体那条（点名键名），不是任意非零退出。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(HERE, "check_config_keys.py")
TEMPLATE = os.path.join(ROOT, "config.release.yaml")


def _run_gate() -> tuple[int, str]:
    p = subprocess.run([sys.executable, GATE], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _mutate(path: str, old: bytes, new: bytes, expect_frag: str) -> tuple[bool, str]:
    with io.open(path, "rb") as f:
        original = f.read()
    if original.count(old) != 1:
        return False, "锚点命中 %d 次（期望 1）" % original.count(old)
    try:
        with io.open(path, "wb") as f:
            f.write(original.replace(old, new, 1))
        code, out = _run_gate()
        if code != 1:
            return False, "期望 exit 1，实际 %d" % code
        if expect_frag not in out:
            return False, "红了但没点名 %r——可能红在别处" % expect_frag
        return True, "红在预期条"
    finally:
        with io.open(path, "wb") as f:
            f.write(original)
        with io.open(path, "rb") as f:
            if f.read() != original:
                raise RuntimeError("还原后字节对不上: %s" % path)


def _eol_of(path: str) -> bytes:
    with io.open(path, "rb") as f:
        head = f.read(65536)
    return b"\r\n" if b"\r\n" in head else b"\n"


def _m1_dead_key() -> tuple[bool, str]:
    eol = _eol_of(TEMPLATE)
    anchor = b"  idle_threshold_s: 180" + eol
    injected = anchor + b"  zzz_dead_probe_key: 1" + eol
    return _mutate(TEMPLATE, anchor, injected, "zzz_dead_probe_key")


def _m2_drop_exempt() -> tuple[bool, str]:
    old = b'EXEMPT_SUBTREE = ("ollama.options",)'
    new = b"EXEMPT_SUBTREE = ()"
    return _mutate(GATE, old, new, "ollama.options.num_ctx")


def _control() -> tuple[bool, str]:
    code, out = _run_gate()
    if code != 0:
        return False, "对照组期望 exit 0，实际 %d:\n%s" % (code, out[:800])
    if "死键 0" not in out:
        return False, "对照组没打印「死键 0」"
    return True, "对照组全绿，死键 0"


def main() -> int:
    results = []
    for name, fn in (("M1", _m1_dead_key), ("M2", _m2_drop_exempt), ("对照", _control)):
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, "刀自身异常: %r" % exc
        results.append((name, ok, msg))
        print("%s %s —— %s" % ("✓" if ok else "✗", name, msg))
    try:
        ok, msg = _control()
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, "还原后对照异常: %r" % exc
    results.append(("还原后对照", ok, msg))
    print("%s 还原后对照 —— %s" % ("✓" if ok else "✗", msg))
    bad = [r for r in results if not r[1]]
    if bad:
        print("✗ %d 项不符合" % len(bad))
        return 1
    print("✓ 两刀全红 + 对照组绿 + 还原后仍绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
