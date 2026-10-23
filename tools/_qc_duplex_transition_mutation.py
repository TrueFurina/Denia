# -*- coding: utf-8 -*-
"""test_duplex_transition_watchdog 的变异刀：四刀注入 + 对照。

每刀都断言**测试红在具体那一条**（输出里出现对应的 ✗ 行），不是任意非零退出——
分不清「判据在工作」和「测试自己崩了」的刀没有意义。

  M1 阈值失效：20.0 → 99999.0  → 卡死也永不清闸 → [3]「闸门被强制解除」红
                                  （这就是"永久静默"回归本身）
  M2 抽 raise 处时间戳          → [5]「闸门拉起处紧接着记时间戳」红
  M3 抽 tick 调用               → [5]「5s 双工诊断 tick 里紧接着调看门狗」红
  M4 死包装复活                 → [6] 墓碑「_reopen_stream…已删」红
  对照                          → ALL PASS；收尾再跑一次（还原后必须仍全过）

纪律：二进制读写（文本模式在 Windows 会把 \n 写成 \r\n → 全文件 CRLF → 锚点全失效）；
锚点按实测 EOL 拼；锚点必须命中且只命中一次；还原后字节逐字节核对。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEST = os.path.join(ROOT, "src", "tests", "test_duplex_transition_watchdog.py")
PROACTIVE = os.path.join(ROOT, "src", "companion", "proactive.py")
OMNI = os.path.join(ROOT, "src", "llm", "cloud_omni_realtime.py")
EOL = b"\r\n"


def _eol(b: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in b else b"\n"


def _run_test() -> tuple[int, str]:
    p = subprocess.run([sys.executable, TEST], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _drop_line(src: bytes, line_pat: bytes) -> bytes | None:
    """删掉唯一匹配的一行（连同行尾 EOL）。命中 0 或 >1 都返回 None。"""
    eol = _eol(src)
    lines = src.split(eol)
    idx = [i for i, ln in enumerate(lines) if re.search(line_pat, ln)]
    if len(idx) != 1:
        return None
    del lines[idx[0]]
    return eol.join(lines)


def _mutate(path: str, mutator, expect_frag: str) -> tuple[bool, str]:
    with io.open(path, "rb") as f:
        original = f.read()
    mutated = mutator(original)
    if mutated is None or mutated == original:
        return False, "变异没改变文件内容——锚点没打中（0 或多次命中）"
    try:
        with io.open(path, "wb") as f:
            f.write(mutated)
        code, out = _run_test()
        if code == 0:
            return False, "测试仍然全绿——刀没有鉴别力"
        if expect_frag not in out:
            return False, "红了但没红在预期那条（期望 %r）" % expect_frag
        return True, "红在预期条"
    finally:
        with io.open(path, "wb") as f:
            f.write(original)
        with io.open(path, "rb") as f:
            if f.read() != original:
                raise RuntimeError("还原后字节对不上: %s" % path)


def _m1_threshold() -> tuple[bool, str]:
    def mut(b: bytes) -> bytes:
        return b.replace(b"_since) < 20.0", b"_since) < 99999.0", 1) \
            if b.count(b"_since) < 20.0") == 1 else None
    return _mutate(PROACTIVE, mut, "闸门被强制解除")


def _m2_raise_ts() -> tuple[bool, str]:
    def mut(b: bytes) -> bytes:
        return _drop_line(b, rb"self\._duplex_transition_since = time\.time\(\)")
    return _mutate(PROACTIVE, mut, "闸门拉起处紧接着记时间戳")


def _m3_tick_call() -> tuple[bool, str]:
    def mut(b: bytes) -> bytes:
        return _drop_line(b, rb"^\s+self\._duplex_force_clear_transition\(\)\s*$")
    return _mutate(PROACTIVE, mut, "5s 双工诊断 tick 里紧接着调看门狗")


def _m4_revive_dead_wrapper() -> tuple[bool, str]:
    stub = ("    def _reopen_stream(self) -> None:\n"
            "        \"\"\"变异刀临时复活：死包装。\"\"\"\n"
            "        with self._lock:\n"
            "            self._reopen_stream_locked()\n\n").encode("utf-8")
    anchor = b"    def set_device(self, device_name: str) -> bool:"

    def mut(b: bytes) -> bytes:
        if b.count(anchor) != 1:
            return None
        return b.replace(anchor, stub + anchor, 1)
    return _mutate(OMNI, mut, "_reopen_stream")


def _control() -> tuple[bool, str]:
    code, out = _run_test()
    if code != 0:
        return False, "对照组期望全绿，实际 exit=%d:\n%s" % (code, out[:800])
    if "ALL PASS" not in out:
        return False, "对照组没打印 ALL PASS"
    return True, "对照组 ALL PASS"


def main() -> int:
    results = []
    for name, fn in (("M1", _m1_threshold), ("M2", _m2_raise_ts),
                     ("M3", _m3_tick_call), ("M4", _m4_revive_dead_wrapper),
                     ("对照", _control)):
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
    print("✓ 四刀全红 + 对照组绿 + 还原后仍绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
