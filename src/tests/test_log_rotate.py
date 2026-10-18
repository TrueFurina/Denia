"""日志轮转与清理测试（阶段 B2，src/log_rotate.py）。

为什么值得测：清理脚本写错的后果是**删错文件**——要么把还在用的当前日志删了，
要么把别的前缀一起扫掉。这类 bug 不会当场报错，只会在几天后让人发现"日志没了"。

所以这里全部用临时目录跑，绝不碰真实的 logs/。

跑法：.venv/Scripts/python.exe src/tests/test_log_rotate.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import log_rotate  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _touch(path: str, size: int = 0, mtime: float | None = None) -> None:
    with open(path, "wb") as fh:
        if size:
            fh.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="denia_log_rotate_")
    try:
        print("[1] 体积格式化")
        check(log_rotate.human_bytes(0) == "0 B", "0 → 0 B")
        check(log_rotate.human_bytes(512) == "512 B", "512 → 512 B")
        check(log_rotate.human_bytes(2048) == "2.0 KB", "2048 → 2.0 KB")
        check(log_rotate.human_bytes(1024 ** 2) == "1.0 MB", "1MiB → 1.0 MB")
        check(log_rotate.human_bytes(-5) == "0 B", "负数 → 0 B（不出现负体积）")

        print("[2] 按份数清理：每类各留最近 N 份")
        base = time.time() - 10000
        for i in range(40):
            _touch(os.path.join(tmp, f"run_20260101_0000{i:02d}.log"),
                   size=100 + i, mtime=base + i)
            _touch(os.path.join(tmp, f"crash_20260101_0000{i:02d}.log"),
                   size=0, mtime=base + i)
        res = log_rotate.cleanup(tmp, keep=10)
        runs = [n for n in os.listdir(tmp) if n.startswith("run_")]
        crashes = [n for n in os.listdir(tmp) if n.startswith("crash_")]
        check(len(runs) == 10, f"run_ 剩 10 份（实际 {len(runs)}）")
        check(len(crashes) == 10, f"crash_ 剩 10 份（实际 {len(crashes)}）")
        check(res["kept"] == 20, f"返回值 kept=20（实际 {res['kept']}）")
        # 保留的必须是 mtime 最新的那批（文件名 30..39）
        check(sorted(runs)[-1] == "run_20260101_000039.log",
              f"保留的是最新文件（最大 {sorted(runs)[-1]}）")
        check("run_20260101_000000.log" not in runs, "最旧的已被删除")

        print("[3] 不误伤其他前缀 / 非日志文件")
        _touch(os.path.join(tmp, "other.log"))
        _touch(os.path.join(tmp, "keep.txt"))
        _touch(os.path.join(tmp, "run_notalog.txt"))
        log_rotate.cleanup(tmp, keep=5)
        check(os.path.exists(os.path.join(tmp, "other.log")), "other.log 保留")
        check(os.path.exists(os.path.join(tmp, "keep.txt")), "keep.txt 保留")
        check(os.path.exists(os.path.join(tmp, "run_notalog.txt")),
              "run_ 开头但不是 .log 的文件保留")

        print("[4] 空文件优先被清理：空档比有内容的旧档更新时，也要先删空档")
        # 关键在"反着来"：让空文件是**最新的**。只按 mtime 排会保住这个空文件，
        # 只有"空文件优先清理"这条规则生效，才会保住那份有内容的旧日志。
        tmp2 = os.path.join(tmp, "empty_pref")
        os.makedirs(tmp2, exist_ok=True)
        base2 = time.time() - 1000
        _touch(os.path.join(tmp2, "run_new_empty.log"), size=0, mtime=base2 + 10)
        _touch(os.path.join(tmp2, "run_old_real.log"), size=200, mtime=base2 + 1)
        log_rotate.cleanup(tmp2, keep=1)
        left = sorted(n for n in os.listdir(tmp2) if n.startswith("run_"))
        check(left == ["run_old_real.log"],
              f"空档虽新仍被优先删掉，保住有内容的旧档：{left}")

        print("[5] 边界：目录不存在 / keep=0")
        check(log_rotate.cleanup(os.path.join(tmp, "不存在"))["removed"] == [],
              "目录不存在 → 返回空，不抛异常")
        tmp3 = os.path.join(tmp, "keep0")
        os.makedirs(tmp3, exist_ok=True)
        _touch(os.path.join(tmp3, "run_a.log"))
        r0 = log_rotate.cleanup(tmp3, keep=0)
        check(len(r0["removed"]) == 1, "keep=0 → 全部删除（调用方自己负责别传 0）")

        print("[6] report 统计")
        tmp4 = os.path.join(tmp, "rep")
        os.makedirs(tmp4, exist_ok=True)
        _touch(os.path.join(tmp4, "run_1.log"), size=100)
        _touch(os.path.join(tmp4, "crash_1.log"), size=0)
        rep = log_rotate.report(tmp4)
        check(rep["counts"]["run_"] == 1 and rep["counts"]["crash_"] == 1,
              f"分类计数正确：{rep['counts']}")
        check(rep["total_bytes"] == 100, f"总字节数正确：{rep['total_bytes']}")
        check("100 B" == rep["total_human"], f"人类可读：{rep['total_human']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"❌ FAIL：{len(_fails)} 项")
        for f in _fails:
            print("   - " + f)
        return 1
    print("✅ 全部通过（日志轮转）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
