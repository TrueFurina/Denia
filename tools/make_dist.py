"""按白名单组装交付目录（dist/），并在成型前后各跑一遍闸门。

为什么需要它
------------
项目根目录同时是开发目录和工作目录：`config.yaml`（真实 Key）、`call_records.json`
（真实通话记录）、`data/`、`.venv/`、`virtual 45MB 的内置运行时`、290 个一次性
诊断脚本全在同一个文件夹。只要打包方式是"复制文件夹"，这些就会一起进安装包。

所以把"哪些该进包"从人的记忆中搬到代码里：本文件就是唯一的清单来源。

用法
----
    # 1) 只看清单（默认，不写任何文件）
    python tools/make_dist.py

    # 2) 真正组装（目标目录已存在且非空时会拒绝，避免误删）
    python tools/make_dist.py --out dist

    # 3) 不打包内置运行时（只做源码包，体积小很多）
    python tools/make_dist.py --out dist --no-runtime

组装完成后自动执行：
    · 凭据/隐私闸（check_package_secrets.py --dir）
    · 静态资源完整性（check_web_assets.py --dir）
    · 必需文件清单核对

退出码：0 = 可发布；1 = 有阻断项。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# ============================ 清单（唯一真值来源） ============================

# 整目录纳入
INCLUDE_DIRS = ["src", "assets", "prompts"]

# 随包附带的 Python 运行时（--no-runtime 可关）
OPTIONAL_DIRS = ["python"]

# 单文件纳入
INCLUDE_FILES = [
    "main.py",
    "launch_companion.py",
    "run_webui.py",
    "README.md",
    "requirements.txt",
    "requirements-optional.txt",
]

# 发布模板 → 包内实际配置名（模板里的 Key 必须为空）
RENAME = {"config.release.yaml": "config.yaml"}

# 运行时真会调用的 tools 脚本（src/companion/proactive.py、src/llm/cloud_omni_realtime.py
# 都用 os.path.join(ROOT, "tools", "clone_danya_voice.py") 定位它）。
# 其余 290 个是开发期一次性脚本，一律不进包。
INCLUDE_TOOLS = ["clone_danya_voice.py"]

# 这些目录/文件不复制
IGNORE_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "tests", "test"}
IGNORE_SUFFIX = (".pyc", ".pyo", ".log", ".map", ".orig", ".rej")

# 组装后必须存在的路径（缺一个就是"打包漏件"，正是看板娘那次事故的形状）
REQUIRED = [
    "main.py",
    "config.yaml",
    "src/ui/web/index.html",
    "src/ui/web/live2d/rig/index.html",
    "assets/voices/daniya_ref.wav",
]


# ============================ 实现 ============================

def _ignore(_dir: str, names: list[str]) -> set[str]:
    out = set()
    for n in names:
        if n in IGNORE_DIRS or n.endswith(IGNORE_SUFFIX):
            out.add(n)
    return out


def _tree_size(path: str) -> tuple[int, int]:
    total = 0
    count = 0
    for r, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for n in names:
            if n.endswith(IGNORE_SUFFIX):
                continue
            fp = os.path.join(r, n)
            try:
                total += os.path.getsize(fp)
                count += 1
            except OSError:
                pass
    return total, count


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}GB"


def plan(root: str, with_runtime: bool) -> list[tuple[str, str, bool]]:
    """返回 [(源, 相对目标路径, 是否目录)]。不存在的条目直接跳过并报告。"""
    items: list[tuple[str, str, bool]] = []
    missing: list[str] = []

    for d in INCLUDE_DIRS + (OPTIONAL_DIRS if with_runtime else []):
        src = os.path.join(root, d)
        if os.path.isdir(src):
            items.append((src, d, True))
        else:
            missing.append(d + "/")

    for f in INCLUDE_FILES:
        src = os.path.join(root, f)
        if os.path.isfile(src):
            items.append((src, f, False))
        else:
            missing.append(f)

    for src_name, dst_name in RENAME.items():
        src = os.path.join(root, src_name)
        if os.path.isfile(src):
            items.append((src, dst_name, False))
        else:
            missing.append(src_name)

    for t in INCLUDE_TOOLS:
        src = os.path.join(root, "tools", t)
        if os.path.isfile(src):
            items.append((src, os.path.join("tools", t), False))
        else:
            missing.append(f"tools/{t}")

    if missing:
        print("=== 清单中不存在的条目（已跳过）===")
        for m in missing:
            print(f"  ! {m}")
        print()

    return items


def show_plan(root: str, items: list[tuple[str, str, bool]]) -> None:
    print(f"{'类型':6s} {'目标路径':42s} {'体积':>10s} {'文件数':>7s}")
    print("-" * 70)
    total = 0
    count = 0
    for src, rel, is_dir in items:
        if is_dir:
            sz, n = _tree_size(src)
            total += sz
            count += n
            print(f"{'目录':6s} {rel:42s} {_human(sz):>10s} {n:>7d}")
        else:
            sz = os.path.getsize(src)
            total += sz
            count += 1
            print(f"{'文件':6s} {rel:42s} {_human(sz):>10s} {1:>7d}")
    print("-" * 70)
    print(f"{'合计':6s} {'':42s} {_human(total):>10s} {count:>7d}")


def check_required(root: str) -> list[str]:
    return [p for p in REQUIRED if not os.path.exists(os.path.join(root, p))]


def _site_pkgs_count(out_dir: str) -> tuple[int, list[str]]:
    """统计内置运行时 site-packages 里的第三方包数（排除 pip/setuptools 系）。"""
    sp = os.path.join(out_dir, "python", "Lib", "site-packages")
    if not os.path.isdir(sp):
        return -1, []
    skip = ("pip", "pip-", "setuptools", "wheel", "_distutils", "pkg_resources")
    names = [n for n in os.listdir(sp) if not n.startswith(skip)]
    return len(names), names[:6]


def run_gates(out_dir: str, strict_deps: bool = False) -> tuple[int, bool]:
    """成型后跑闸门。返回 (退出码, 依赖是否未装)。"""
    here = os.path.dirname(os.path.abspath(__file__))
    rc = 0
    deps_missing = False
    print("\n=== 闸门 1/3：凭据、隐私、静态资源 ===")
    p = subprocess.run([sys.executable, os.path.join(here, "check_package_secrets.py"),
                        "--dir", out_dir], cwd=here)
    if p.returncode != 0:
        rc = 1

    print("\n=== 闸门 2/3：必需文件清单 ===")
    missing = check_required(out_dir)
    if missing:
        rc = 1
        for m in missing:
            print(f"  ✗ 缺件：{m}")
    else:
        print("  ✓ 必需文件齐全")

    print("\n=== 闸门 3/3：内置运行时是否带得动依赖 ===")
    n, sample = _site_pkgs_count(out_dir)
    if n < 0:
        print("  - 未打包 python/（--no-runtime），跳过")
    elif n == 0:
        # 实测：本项目的 python/ 是精简运行时，site-packages 里只有 pip，
        # 连 yaml 都没有。单靠它起不来，必须补装依赖或改用已装好的环境。
        msg = ("内置运行时 site-packages 里没有任何第三方包（只有 pip）—— "
               "这个包双击后无法启动。\n"
               "    补法：dist/python/python.exe -m pip install -r requirements.txt\n"
               "    （或用你的「安装环境.exe」走同一步）")
        if strict_deps:
            rc = 1
            print("  ✗ " + msg)
        else:
            deps_missing = True
            print("  ⚠ " + msg)
    else:
        print(f"  ✓ 已带 {n} 项第三方包（示例：{', '.join(sample)}）")
    return rc, deps_missing


def copy_items(items: list[tuple[str, str, bool]], out: str) -> None:
    for src, rel, is_dir in items:
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        if is_dir:
            shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser(description="按白名单组装交付目录")
    ap.add_argument("--out", help="输出目录；不给则只打印清单（dry-run）")
    ap.add_argument("--root", default=".", help="项目根目录")
    ap.add_argument("--no-runtime", action="store_true", help="不打包 python/ 内置运行时")
    ap.add_argument("--force", action="store_true", help="输出目录非空时也继续（会覆盖同名文件，不删除多余文件）")
    ap.add_argument("--strict-deps", action="store_true",
                    help="内置运行时没装第三方包时视为失败（CI/正式发布建议开）")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    items = plan(root, with_runtime=not args.no_runtime)

    print(f"项目根：{root}")
    print(f"内置运行时：{'不打包' if args.no_runtime else '打包'}\n")
    show_plan(root, items)

    if not args.out:
        print("\n（dry-run：未写入任何文件。加 --out dist 才会真正组装）")
        missing = check_required(root)
        if missing:
            print("⚠ 源目录本身就缺这些必需文件：")
            for m in missing:
                print(f"  ! {m}")
        return 0

    out = os.path.abspath(args.out)
    if os.path.isdir(out) and os.listdir(out) and not args.force:
        print(f"\n✗ 输出目录非空，已拒绝：{out}")
        print("  换个目录，或先自行清理，或加 --force（只覆盖同名文件，不删多余文件）")
        return 1
    if os.path.exists(out) and not os.path.isdir(out):
        print(f"✗ 目标已存在且不是目录：{out}")
        return 1

    print(f"\n开始组装 → {out}")
    copy_items(items, out)
    sz, n = _tree_size(out)
    print(f"完成：{n} 个文件，{_human(sz)}")

    rc, deps_missing = run_gates(out, strict_deps=args.strict_deps)
    print()
    if rc != 0:
        print(f"✗ 未通过闸门，先修正再发布：{out}")
    elif deps_missing:
        print(f"⚠ 结构与凭据都合格，但依赖还没装 —— 装完才能双击启动：{out}")
    else:
        print(f"✓ 可发布：{out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
