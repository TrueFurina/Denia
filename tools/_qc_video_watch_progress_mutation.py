# -*- coding: utf-8 -*-
"""「视频进度」闸门的变异验证：五刀，刀刀必须被抓住。

为什么补这把刀：`src/channels/tests/test_video_watch_progress.py` 守的是**用户能看见
的那一层** —— 进度条。它挂在 `_prog()` 这条回调链上：`watch_video` 发阶段 →
`Dialogue._make_video_progress` 转事件 → 总线 `publish_threadsafe`。这条链断在哪一环，
现象都是「进度条不动 / 一直转圈」，而错误被 `except` 吞掉、日志里看不出所以然。
第三十轮刚修过它的宿主依赖（`_resolve_ffmpeg` 没 mock），同样：动了判据就得证明牙还在。

五刀各打一条判据：
  P1 诊断里 frames 恒为 0          → 「应记录 AI 看了几帧」红
  P2 不发 frame 阶段               → 「缺少阶段 frame」红
  P3 诊断不暴露 ffmpeg             → 「'ffmpeg' not found」红
  P4 失败时把错误信息抹空          → 「错误信息应保留失败原因」红
  P5 失败时不发 error 阶段         → 「'error' not found」红

判据三件套（沿用本仓 2026-11-08 的修法）：
  · `_judged()` —— 非零退出**且** unittest 打了 `FAILED (`，崩溃不算抓住；
  · `_baseline_green()` —— 起点必须先绿，恒红的判定能让每把刀都变红；
  · 每刀都备份字节、finally 还原并核对 sha256，刀自己不许把源码弄坏。

跑法：.venv/Scripts/python.exe -u tools/_qc_video_watch_progress_mutation.py
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
GATE = os.path.join("src", "channels", "tests", "test_video_watch_progress.py")
VW = os.path.join("src", "channels", "video_watch.py")

# (标签, 目标文件, 原文锚点, 替换文本, 期望出现的失败关键词)
MUTS = [
    ("P1 诊断里 frames 恒为 0",
     VW,
     '        run["frames"] = len(frame_descs) + len(image_descs)',
     '        run["frames"] = 0  # 变异刀：明明看了却不记帧数',
     "应记录 AI 看了几帧"),
    ("P2 不发 frame 阶段",
     VW,
     '                _prog("frame", "达妮娅正在截取画面…")',
     '                pass  # 变异刀：故意不发 frame 阶段',
     "缺少阶段 frame"),
    ("P3 诊断不暴露 ffmpeg",
     VW,
     '        "ffmpeg": _resolve_ffmpeg(),',
     '        "ffmpeg_disabled": _resolve_ffmpeg(),  # 变异刀：改键名',
     "'ffmpeg' not found"),
    ("P4 失败时把错误信息抹空",
     VW,
     '        run["error"] = _humanize_dl_error(url, e)',
     '        run["error"] = ""  # 变异刀：抹掉失败原因',
     "错误信息应保留失败原因"),
    ("P5 失败时不发 error 阶段",
     VW,
     '        _prog("error", "这个视频暂时看不了，我按链接文字和你聊～", done=True)',
     '        pass  # 变异刀：故意不发 error 阶段',
     "'error' not found"),
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
                       encoding="utf-8", errors="replace", timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _judged(rc: int, out: str) -> bool:
    """「判出了问题」而不是「崩了」：非零退出 **且** unittest 打了 `FAILED (`。"""
    return rc != 0 and "FAILED (" in out


def _baseline_green() -> tuple:
    rc, out = _run()
    if rc == 77:
        return True, "77"
    return (rc == 0 and "FAILED" not in out), ""


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
                for ln in [x for x in out.splitlines()
                           if "FAIL:" in x or "AssertionError" in x][:3]:
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
    if rc == 0 and "FAILED" not in out:
        print("  PASS 还原后仍然绿")
    else:
        print("  FAIL 还原后不是绿的 —— 刀自己把源码弄坏了")
        bad += 1

    print("\n%s" % ("五刀全红" if bad == 0 else "有 %d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
