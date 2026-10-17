"""把某个角色的「关系状态 + 聊天历史」归档走，方便重新实测（**不删除**，可回退）。

用法：
    python tools\\relationship_reset.py                 # 列出有哪些角色
    python tools\\relationship_reset.py danya           # 归档 danya 的状态与历史
    python tools\\relationship_reset.py danya --data-dir D:\\tmp\\data

归档方式：`<原文件>.bak-<时间戳>`（与项目"坏数据隔离不静默"一致，绝不直接删）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def list_chars(data_dir: Path) -> list[str]:
    names: set[str] = set()
    for sub in ("affinity", "history"):
        d = data_dir / sub
        if d.is_dir():
            for p in d.iterdir():
                if p.is_file() and not p.name.endswith(".tmp") and ".bak-" not in p.name:
                    names.add(p.stem if p.suffix == ".json" else p.name.split(".")[0])
    return sorted(names)


def reset(data_dir: Path, char_id: str) -> list[Path]:
    moved: list[Path] = []
    targets = [data_dir / "affinity" / f"{char_id}.json",
               data_dir / "history" / f"{char_id}.jsonl"]
    for p in targets:
        if p.exists():
            dst = p.with_suffix(p.suffix + f".bak-{_stamp()}")
            p.replace(dst)
            moved.append(dst)
    return moved


def main(argv: list[str]) -> int:
    data_dir = Path(__file__).resolve().parents[1] / "data"
    args = list(argv[1:])
    if "--data-dir" in args:
        i = args.index("--data-dir")
        data_dir = Path(args[i + 1])
        del args[i:i + 2]

    if not args:
        chars = list_chars(data_dir)
        print(f"数据目录：{data_dir}")
        print("现有角色：" + ("、".join(chars) if chars else "（无）"))
        print("用法：python tools\\relationship_reset.py <角色id>")
        return 0

    char_id = args[0]
    if not data_dir.exists():
        print(f"[reset] 数据目录不存在：{data_dir}")
        return 1
    moved = reset(data_dir, char_id)
    if not moved:
        print(f"[reset] 「{char_id}」没有可归档的状态/历史文件（可能本来就没跑过）")
        return 0
    print(f"[reset] 「{char_id}」已归档 {len(moved)} 个文件：")
    for p in moved:
        print("   ", p.name)
    print("[reset] 下次启动该角色将从零开始（可删掉 .bak-* 释放空间；回退=改回原名）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
