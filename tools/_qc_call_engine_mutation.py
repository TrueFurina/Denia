"""「拨号引擎解析」测试的三把变异刀（验证 tools/test_call_engine_resolve.py 有牙齿）。

这道测试守的是 **2026-10-16 用户决策：语音电话恒走云端**。它长期是红的（13/20）——
因为原来的用例守着 10-09 那版「显式选本地就走本地」的契约，而那条路已被否决。
按本仓的教训（`test_quota_alert`），这种测试最坏的下场不是红，而是"谁把它修绿，
谁就把用户停掉的行为加了回来"。所以这次是**改写**成守当前契约，并配这三把刀：

  M1 删掉 `target = "realtime"`（无条件强制云端）  → 组 1 必须红（本地用例会走回本地）
  M2 `no_cloud_duplex` 改回旧码 `no_cloud_duplex_cfg` → 组 2.3 + 组 3.1 必须红
  M3 给失败返回加回 `fallback`                     → 组 3.2 必须红（墓碑判据）

注意本仓的 `src/ui/webapp.py` 是 **CRLF**，锚点按文件实际换行符拼（写死 `\\n` 会命中 0 次，
整把刀被静默跳过）。改源码一律二进制模式。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tools", "test_call_engine_resolve.py")
WEBAPP = os.path.join("src", "ui", "webapp.py")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _eol(data: bytes) -> str:
    return "\r\n" if b"\r\n" in data else "\n"


def _block(lines: list[str]) -> bytes:
    return (_eol(_read(WEBAPP)).join(lines) + _eol(_read(WEBAPP))).encode("utf-8")


MUTS = [
    ("M1 删掉「无条件强制云端」", lambda: _block([
        '        if target != "realtime":',
        '            logger.info("拨号引擎强制云端：原解析 target=%r（按用户要求，语音电话恒走云端）", target)',
        '        target = "realtime"',
    ]), lambda: _block([]), "组 1（target 恒 realtime）"),
    ("M2 失败码改回旧契约", lambda: _block([
        '            return {"success": False, "error_code": "no_cloud_duplex", "diag": _diag,',
    ]), lambda: _block([
        '            return {"success": False, "error_code": "no_cloud_duplex_cfg", "diag": _diag,',
    ]), "组 2.3 + 组 3.1（错误码）"),
    ("M3 失败返回加回 fallback", lambda: _block([
        '            return {"success": False, "error_code": "no_cloud_duplex", "diag": _diag,',
    ]), lambda: _block([
        '            return {"success": False, "error_code": "no_cloud_duplex", "diag": _diag,',
        '                    "fallback": "local_omni",',
    ]), "组 3.2（fallback 墓碑）"),
]


def run_test() -> tuple[int, list[str]]:
    p = subprocess.run([PY, TEST], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    fails = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip().startswith("FAIL")]
    return p.returncode, fails


def main() -> int:
    bad = 0
    for label, mk_old, mk_new, expect in MUTS:
        data = _read(WEBAPP)
        old, new = mk_old(), mk_new()
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(WEBAPP, data.replace(old, new))
        try:
            code, fails = run_test()
        finally:
            _write(WEBAPP, data)
        if code == 0:
            print("  FAIL %s：测试仍然全绿（%s 没被守住）" % (label, expect))
            bad += 1
        else:
            print("  PASS %s → 变红 %d 项  %s" % (label, len(fails), (fails[0] if fails else "")))

    code, _ = run_test()
    if code != 0:
        print("  FAIL 还原后测试仍失败（源码没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后 22 项全绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
