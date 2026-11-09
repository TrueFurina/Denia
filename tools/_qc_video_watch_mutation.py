# -*- coding: utf-8 -*-
"""「视频观看」闸门的变异验证：六刀，刀刀必须被抓住。

为什么补这把刀：`src/channels/tests/test_video_watch.py` 是本仓**改动最频繁**的判据
之一（2026-11-09 第三十轮刚修过 `test_success` 的宿主依赖），却从来没有刀。
它守的是一条完整的链路：URL 白名单 → 抽帧 → 送 VLM → 拼上下文。这条链上任何一环
断掉，用户看到的都是「点开只会看个空壳」，而测试全绿 —— 这正是必须有刀的原因。

六刀各打一条判据：
  V1 抽帧闸短路（`if False and video_files and ffmpeg_exe`） → 「帧描述进上下文」红
  V2 帧描述不送进 `build_media_context`                    → 「帧描述进上下文」红
  V3 上下文里丢掉帧条目（enumerate 空列表）                → 「纯函数组装」红
  V4 视觉能力恒判为不可用（`vision_ok = False`）           → 「帧描述进上下文」红
  V5 视频被判成图文（`is_image_media = True`）             → 「图文/视频分型」红
  V6 pixiv 掉出媒体白名单（正则改坏）                      → 「媒体白名单」红

判据三件套（沿用本仓 2026-11-08 的修法）：
  · `_judged()` —— 非零退出**且** unittest 打了 `FAILED (`，崩溃不算抓住；
  · `_baseline_green()` —— 起点必须先绿，恒红的判定能让每把刀都变红；
  · 每刀都备份字节、finally 还原并核对 sha256，刀自己不许把源码弄坏。

【注意】这里的判据输出是 unittest 的，不是本仓闸门惯用的 `[FAIL]` 行。
`unittest` 崩溃（ImportError 等）同样 exit 1，但只打 `ERROR`/`Traceback`、
不打 `FAILED (`，所以 `_judged()` 用 `FAILED (` 把「判出来了」和「崩了」分开。

跑法：.venv/Scripts/python.exe -u tools/_qc_video_watch_mutation.py
退出码：0 = 六刀全红；1 = 有刀没被抓住；77 = 本机跑不了（缺依赖等）。
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
GATE = os.path.join("src", "channels", "tests", "test_video_watch.py")
VW = os.path.join("src", "channels", "video_watch.py")

# (标签, 目标文件, 原文锚点, 替换文本, 期望出现的失败关键词)
MUTS = [
    ("V1 抽帧闸短路",
     VW,
     "        if video_files and ffmpeg_exe:",
     "        if False and video_files and ffmpeg_exe:  # 变异刀：故意不抽帧",
     "厨房"),
    ("V2 帧描述不送进上下文",
     VW,
     "        return build_media_context(meta, frame_descs=frame_descs,",
     "        return build_media_context(meta, frame_descs=[],  # 变异刀：故意丢帧",
     "厨房"),
    ("V3 上下文里丢掉帧条目",
     VW,
     "        for i, d in enumerate(frame_descs, 1):",
     "        for i, d in enumerate([], 1):  # 变异刀：说了看了 N 帧却不列出来",
     "锅里的肉"),
    ("V4 视觉能力恒判为不可用",
     VW,
     "        vision_ok = bool(_va() if callable(_va) else _va)",
     "        vision_ok = False  # 变异刀：故意判成没有视觉能力",
     "厨房"),
    ("V5 视频被判成图文",
     VW,
     "    is_image_media = bool(page_count) or (not frame_descs and image_descs)",
     "    is_image_media = True  # 变异刀：视频也说成图文",
     "【视频观看】"),
    ("V6 pixiv 掉出媒体白名单",
     VW,
     '    r"|pixiv\\.net"',
     '    r"|pixiv-nope\\.net"',
     "pixiv.net"),
]

# 给 `check_knife_noop.py` 认形状用：MUTS 每项是
# (标签, 目标文件, 原文锚点, 替换文本, 期望关键词) —— 看类型分不出 old/new，
# 这里显式指出第 2 项是 old、第 3 项是 new（下标从 0 起）。
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

    print("\n%s" % ("六刀全红" if bad == 0 else "有 %d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
