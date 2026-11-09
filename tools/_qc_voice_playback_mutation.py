# -*- coding: utf-8 -*-
"""「试听播放生命周期」回归测试的变异刀（验证 test_voice_playback_lifecycle.py 真能抓错）。

本仓规矩：**加门必须同时加变异**，否则那道门只是装饰品。本脚本自己也登记在
`tools/check_all.py` 的「试听播放生命周期变异」（标慢项），所以必须快且自还原。

三把刀各打一条判据，不是同一个分支打三遍：
  M1 去掉 `proc.kill()`                  → 判据A「_kill_voice_playback 真的终结了播放子进程」必须红
  M2 去掉 `_start_voice_playback` 抢占    → 判据B「新试听抢占：上一段 ffplay 被终结」必须红
  M3 把独立线程改回同步阻塞调用          → 判据C「_start_voice_playback 非阻塞」必须红

坑（本仓血泪）：
  · 改源码按**字节**读写（文本模式 Windows 把 `\n` 洗成 `\r\n`，多行锚点全失效）。
  · 锚点必须 `count==1` 才换；命中 0 次 = 刀静默跳过，直接判 FAIL（不许 continue）。
  · 断言要**点名是哪条判据红了**：任意一条红都退出 1，只看退出码的话 M1 打中 M2
    的判据也照样「通过」，等于没验。
  · `try/finally` 自还原，绝不能把源码留在变异态（全量门禁被中断会留残留）。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable
TEST = os.path.join("src", "tests", "test_voice_playback_lifecycle.py")
SRC = os.path.join("src", "ui", "webapp.py")
NL = b"\n"

# (标签, 锚点, 变异后, 必须判红的那条判据的 [FAIL] 文本片段)
MUTS = [
    ("M1 去掉 proc.kill()",
     b"            if proc.poll() is None:" + NL + b"                proc.kill()",
     b"            if proc.poll() is None:" + NL + b"                pass  # MUT: no kill",
     "_kill_voice_playback 真的终结了播放子进程"),
    ("M2 去掉新试听抢占",
     b"        self._kill_voice_playback()  # " + "抢占上一段".encode("utf-8"),
     b"        pass  # MUT: no preempt",
     "新试听抢占：上一段 ffplay 被终结"),
    ("M3 改回同步阻塞播放",
     (b"        threading.Thread(" + NL
      + b"            target=self._play_audio_system, args=(path,)," + NL
      + b'            daemon=True, name="voice_playback",' + NL
      + b"        ).start()"),
     b"        self._play_audio_system(path)  # MUT: sync blocking",
     "_start_voice_playback 非阻塞"),
]


def _read() -> bytes:
    with io.open(SRC, "rb") as f:
        return f.read()


def _write(data: bytes) -> None:
    with io.open(SRC, "wb") as f:
        f.write(data)


def _run_test() -> tuple[int, str]:
    r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    rc, out = _run_test()
    print("基线:", "全绿 ✓" if rc == 0 else "已红 ✗（无法做变异验证）")
    if rc != 0:
        print(out[-600:])
        return 1

    raw = _read()
    bad = 0
    try:
        for label, anchor, mutated, must_red in MUTS:
            n = raw.count(anchor)
            if n != 1:
                # 命中 0 次 = 刀静默跳过，判红（绝不 continue 蒙混过关）
                print(f"  ✗ 锚点失效 {label}: 命中 {n} 次，期望 1")
                bad += 1
                continue
            _write(raw.replace(anchor, mutated, 1))
            try:
                rc2, out2 = _run_test()
                # 点名判据：必须【那条特定的】判据判红，且整体确实失败
                fail_lines = [ln.strip() for ln in out2.splitlines()
                              if "[FAIL]" in ln]
                targeted = [ln for ln in fail_lines if must_red in ln]
                caught = (rc2 != 0 and bool(targeted))
                print(("  ✓ 变红 " if caught else "  ✗ 没红 ") + label
                      + (f"  (红的是: {targeted[0][:50]})" if targeted else
                         f"  (实际红: {[x[:40] for x in fail_lines]})"))
                if not caught:
                    bad += 1
            finally:
                _write(raw)   # 每把刀用完立刻还原，绝不叠加变异
    finally:
        _write(raw)           # 兜底：任何异常/中断都还原

    rc3, out3 = _run_test()
    print("\n还原后基线:", "全绿 ✓" if rc3 == 0 else "仍红 ✗")
    if rc3 != 0:
        bad += 1
        print(out3[-400:])
    print(f"结果: {len(MUTS)} 把刀，{bad} 把没被抓住/锚点失效")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
