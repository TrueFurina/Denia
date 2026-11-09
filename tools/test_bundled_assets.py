# -*- coding: utf-8 -*-
"""产品必需资产清单：缺一个就红。

为什么需要（2026-11-04）
------------------------
参考音频曾经被**两次清理**弄丢，而两次都有一套自洽的理由：

  · 2026-10-19「清场 ~1.5GB」：把 `daniya_ref.wav` 列进垃圾清单一并删了
    （见 .workbuddy-ai/memory/2026-10-19.md:249）；
  · 2026-11-02「归档 DanYaAI 快照」：判「assets/ 与现版字节一致，故未复制」，
    然后 `rm -rf` 了 416MB 整包 —— 而那时现版其实**早就没有**这些 wav 了
    （见 archive_daniya_20261102/MANIFEST.md 与 2026-11-02.md）。

两次都不是手滑，是"清理"这个动作本身没有护栏。所以守它不能靠自觉，得靠清单：
下面这些文件在产品里被配置 / 代码 / 页面引用，删掉 = 功能静默失效，
而失效往往要等用户投诉才被发现。

判据四条：
  1. 文件必须存在；
  2. 不能是空壳（有最小体积下限，防"占位文件"蒙混）；
  3. 音频/图片必须是**真格式**（魔数嗅探，防"改成 .wav 的空文件"）；
  4. 必须在 git 里（打包只取 `git ls-files`，存在但没入库 = 包内缺失）。

跑法：.venv/Scripts/python.exe -u tools/test_bundled_assets.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)

OK = FAIL = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS " + name + ((" :: " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL " + name + ((" :: " + extra) if extra else ""))


def _head(path: str, n: int = 12) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


# (路径, 最小字节数, 魔数判定函数 或 None)
def _riff(b: bytes) -> bool:
    return b[:4] == b"RIFF" and b[8:12] == b"WAVE"


def _png(b: bytes) -> bool:
    return b[:8] == b"\x89PNG\r\n\x1a\n"


def _jpg(b: bytes) -> bool:
    return b[:2] == b"\xff\xd8"


def _ico(b: bytes) -> bool:
    return b[:4] == b"\x00\x00\x01\x00"


ASSETS = [
    # 参考音频：配置里 reference_audio / ref_audio 直接引用，丢了她就没法克隆音色
    ("assets/voices/daniya_ref_0813_24k.wav", 100_000, _riff),
    ("assets/voices/daniya_ref.wav", 50_000, _riff),
    # 引导页「预填她的」随包参考音（PRESETS 里写死这个 url）
    ("src/ui/web/_role_assets/ref/xiaowei.wav", 50_000, _riff),
    # 角色头像/占位图（角色卡、聊天页、通话页都取它）
    ("src/ui/web/_role_assets/danya.png", 10_000, _png),
    ("src/ui/web/_role_assets/fatfish.jpg", 10_000, _jpg),
    ("src/ui/web/_role_assets/default.png", 5_000, _png),
    ("src/ui/web/assets/danya_avatar.png", 10_000, _png),
    # 窗口/托盘图标
    ("app.ico", 5_000, _ico),
    # 形象层：桌宠能画出来全靠这两个（live2d.js 只有 5.8KB，下限按实测取 3KB）
    ("src/ui/web/live2d/live2d.js", 3_000, None),
    ("src/ui/web/live2d/rig/index.html", 1_000, None),
]


def _tracked(path: str) -> str:
    """「入库了吗」→ `"yes"` / `"no"` / `"unknown"`（问不到）。

    【2026-11-09 修·「问不到」被当成「答否」】原来只判 `returncode == 0`，
    于是**在非 git 工作树里**（交付包工作副本、解压出来的目录、拷给别人看的副本）
    每一件资产都被判「在磁盘上但没进 git —— 包里会缺」。这句话是**它没验证过**的
    断言：`git ls-files` 在非仓目录里返回 128 + "fatal: not a git repository"，
    和「这个文件确实没入库」（返回 1）根本不是一回事。
    本仓纪律：「缺环境 ≠ 判据失败」，问不到就得说问不到，并 exit 77（未验证），
    绝不能报成 FAIL —— 否则真缺陷会被这一片假红淹掉（2026-11-09 全量实测：
    交付包工作副本里 14 项红，其中 5 项全是这一类）。
    """
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", path],
                           capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001  没有 git 可执行文件
        return "unknown"
    if r.returncode == 0:
        return "yes"
    if "not a git repository" in (r.stderr or "").lower():
        return "unknown"
    return "no"


def main() -> int:
    print("1) 必需资产存在 / 非空壳 / 真格式 / 已入库")
    unverified = 0
    for path, min_size, magic in ASSETS:
        if not os.path.isfile(path):
            chk(path, False, "文件不存在")
            continue
        size = os.path.getsize(path)
        if size < min_size:
            chk(path, False, "只有 %d 字节（下限 %d）——像是空壳/占位" % (size, min_size))
            continue
        if magic is not None:
            head = _head(path)
            if not magic(head):
                chk(path, False, "魔数不对（%r）——扩展名改了但内容不是该格式" % head[:8])
                continue
        state = _tracked(path)
        if state == "unknown":
            # 问不到 ≠ 答否：只报「未验证」，不进 FAIL，也不冒充 PASS
            unverified += 1
            print("  ~ %s —— 存在/非空壳/真格式 已验，但「是否入库」未验证" % path)
            continue
        if state == "no":
            chk(path, False, "在磁盘上但没进 git —— 打包只取 git ls-files，包里会缺")
            continue
        chk(path, True, "%d 字节" % size)

    print("\n结果: %d PASS / %d FAIL / %d 未验证" % (OK, FAIL, unverified))
    if unverified:
        print("\n⚠️ %d 件资产的「是否入库」未验证：本机没有 git 工作树，"
              "问不到不等于没入库。" % unverified)
    if FAIL:
        print("\n⚠️ 这些是产品素材，不是垃圾。删之前先确认有没有被配置/代码引用；")
        print("   误删可从 git 恢复：git checkout HEAD -- <路径>")
        return 1
    if unverified:
        print("[SKIP] 本机不是 git 工作树：%d 件资产的「是否入库」未验证"
              "（存在/非空壳/真格式已逐件验过，但这一项问不到 —— 不是通过）"
              % unverified)
        return 77
    return 0


if __name__ == "__main__":
    sys.exit(main())
