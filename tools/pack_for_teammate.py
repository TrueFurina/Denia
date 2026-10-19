# -*- coding: utf-8 -*-
"""把当前仓库打成一份「给队友的源码包」zip，并在出包后**自验**。

为什么要有这个脚本（而不是手压目录）
------------------------------------
2026-10-19 的交付验包发现，上一个包是"把整个工作目录直接压了"：混进了
`data/`、`call_records.json`、`user_knowledge.yaml`，三项全踩交付红线；
还顺带把 290 个开发期一次性脚本、`.swapbak` 编辑器备份一起发了出去。

手工维护排除清单的问题不在"想不到"，而在"下次还得再想一遍"。
所以这里：

1. **包内容只取 `git ls-files`**——`.gitignore` 本来就是这份规则的单一事实源
   （`node_modules` / `python` / `data` / `logs` / `config.yaml` 全在里面），
   且它随代码演进，不会和打包脚本各说一套。
2. 再叠一层**硬排除**做纵深防御：即使哪天有人 `git add -f` 了实配或隐私文件，
   也进不了包。
3. 出包后**重新打开 zip 逐条核对**，再解到临时目录跑既有闸门
   `tools/check_package_secrets.py --dir`。红了就删包退出——不给"先发出去再说"留口子。

用法
----
    python tools/pack_for_teammate.py               # 出包 + 验包（默认落 dist/）
    python tools/pack_for_teammate.py --dry-run     # 只打印清单，不写任何文件
    python tools/pack_for_teammate.py --out D:/x.zip
    python tools/pack_for_teammate.py --no-verify   # 跳过解包验闸（仅调试用）

退出码：0 = 出包且验包通过；1 = 有阻断项（包会被删掉）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================ 排除规则 ============================
# 规则分三层，越靠前越"硬"。任何一层命中即不入包。

# 1) 任何层级都排除的目录名：可再生 / 体积大 / 与交付无关
EXCLUDE_DIR_ANY = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",       # shell/ 的 Electron 依赖，383MB，队友 npm install 即可
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".build_tools",       # NSIS 工具链，本机下载的二进制
    ".workbuddy",         # 旧版智能体记忆目录
    ".workbuddy-ai",      # 当前智能体记忆目录（含我的工作笔记，不该外发）
    "build",
    "ort_dml",
    # 隐私红线：任何层级的 data/ 与 logs/ 都不许进包。
    # 2026-10-20 实测：仓库跟踪的 764 个文件里没有任何路径含 data/logs 段
    # （`tools/daniya_sticker_skill/data/` 是 67MB 素材，已被 .gitignore 挡住），
    # 所以按"任意层级"排不会误伤；而按"只看包根"排会漏掉嵌套的那些。
    "data",
    "logs",
}

# 2) 只排包根这一层：内置便携运行时（python/python.exe）。
#    不做全层级匹配——`python` 是个太常见的名字，全层级会误伤正常源码目录。
EXCLUDE_DIR_TOP = {"python"}

# 3) 精确文件名：全是交付红线或实例文件
EXCLUDE_FILES = {
    "config.yaml",          # 实配，含真实凭据
    "config.local.yaml",
    ".env",
    "call_records.json",    # 通话记录
    "user_knowledge.yaml",  # 用户知识库
    "memory.json",          # 记忆
    "memory_profile.jsonl",
}
# Windows 文件系统大小写不敏感：`Config.yaml` 和 `config.yaml` 是同一个文件，
# 精确比对会放过它。统一转小写再比。
_EXCLUDE_FILES_LOWER = {n.lower() for n in EXCLUDE_FILES}

# 4) 后缀：缓存 / 构建产物 / 备份
EXCLUDE_SUFFIX = (
    ".pyc", ".pyo", ".log", ".exe", ".msi",
    ".bak", ".swapbak", ".orig", ".rej", ".tmp",
)

# 组装后必须存在的路径。缺一个就是"打包漏件"——2026-10-17 看板娘那次事故的形状：
# live2d/rig 曾是无 .gitmodules 的 gitlink，重新 clone 后目录为空，看板娘静默消失，
# 日志里只有一条 404。宁可出包失败，也不要发一个"看起来完整"的空壳。
REQUIRED = [
    "main.py",
    "launch_companion.py",
    "config.release.yaml",      # 发布模板（Key 全空），队友复制成 config.yaml
    "requirements.txt",
    "src/version.py",
    "src/ui/web/index.html",
    "shell/main.js",
    "shell/package.json",
    "tools/check_all.py",
]

# 随包附带的说明文件（内容内嵌在脚本里，不往仓库根目录再添一份 md）
NOTICE_NAME = "队友须知_先读我.md"


def _app_version() -> str:
    """从 src/version.py 取版本号。

    不 import：`src/` 是个包，import 会连带触发 `src/__init__.py` 里的东西，
    打包脚本不该依赖运行环境。只做文本匹配。
    """
    path = os.path.join(ROOT, "src", "version.py")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def _exclude_reason(rel: str) -> str | None:
    """rel 用 / 分隔。返回排除原因，None 表示入包。"""
    parts = rel.replace("\\", "/").split("/")
    head = parts[0]

    # 顶层目录规则只在"路径里真的还有下一级"时生效：根目录下叫
    # `distutils_compat.py` 的文件不该因为以 dist 开头就被吞掉。
    if len(parts) > 1:
        if head in EXCLUDE_DIR_TOP:
            return f"顶层目录 {head}/"
        # dist / dist_stage / dist_core … 打包产物与暂存，一律不回头吃自己
        if head.startswith("dist"):
            return f"构建产物 {head}/"
    for p in parts[:-1]:
        if p in EXCLUDE_DIR_ANY:
            return f"目录 {p}/"

    name = parts[-1]
    low = name.lower()
    if low in _EXCLUDE_FILES_LOWER:
        return f"红线文件 {name}"
    if low.startswith(".env"):
        return "环境变量文件"
    if ".swapbak" in low:
        return "编辑器交换备份"
    if low.endswith(EXCLUDE_SUFFIX):
        return f"后缀 {low.rsplit('.', 1)[-1]}"
    # 条目本身就是个目录名（zip 的目录项不一定带尾斜杠，verify_zip 拿到的
    # 可能就是 "T/logs" 这种）。不认这一条，目录项会从缝里漏过去。
    if low in EXCLUDE_DIR_ANY:
        return f"目录名 {name}"
    return None


def _git_files() -> list[str]:
    """仓库跟踪的文件清单（相对路径，/ 分隔）。

    这是"什么算源码"的唯一事实源：`.gitignore` 已经把运行时、构建产物、
    实配配置、用户数据全挡在外面，且随代码一起演进。
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"✗ 取不到 git 文件清单（{e}）。本脚本要求仓库是 git 工作树。",
              file=sys.stderr)
        raise SystemExit(1)
    return [p for p in out.decode("utf-8").split("\0") if p]


def collect() -> tuple[list[str], list[tuple[str, str]]]:
    """返回 (入包文件列表, 被排除的 [(路径, 原因)])。"""
    keep: list[str] = []
    dropped: list[tuple[str, str]] = []
    for rel in _git_files():
        if not os.path.isfile(os.path.join(ROOT, rel)):
            # 被跟踪但本地已删（未提交的删除）：跳过，否则 zip 里会缺条
            dropped.append((rel, "本地不存在"))
            continue
        reason = _exclude_reason(rel)
        if reason:
            dropped.append((rel, reason))
        else:
            keep.append(rel)
    return keep, dropped


def _notice(version: str, entry_count: int) -> str:
    return f"""# 队友须知 · 达妮娅 AI 桌面伴侣 源码包

> 版本 **v{version}** ｜ 共 {entry_count} 个文件 ｜ 打包工具 `tools/pack_for_teammate.py`
> 这是**源码包**，不是安装包。装好依赖再跑。

## 一、装依赖（约 3 分钟）

```bat
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

要求 Python 3.11+（开发环境是 3.12）。

桌宠壳（Electron）的 node_modules **没有随包**（383MB，压了也没意义）：

```bat
cd shell
npm install
```

## 二、配配置

```bat
copy config.release.yaml config.yaml
```

然后编辑 `config.yaml`，把 `api_key` 一类字段填成你自己的。模板里所有 Key
都是**空的**——这是故意的，也是 `tools/check_package_secrets.py` 会拦的项：
**别把填好的 config.yaml 再发出去**。

## 三、跑起来

```bat
python launch_companion.py      :: 桌宠（默认 auto：先试 Electron 壳，失败回退 webview）
python main.py                  :: 纯 Web UI
```

第一次跑会卡一阵（模型下载 / 依赖自检），日志在 `logs/`。

## 四、出问题先自检

```bat
python main.py --doctor
```

它会逐项告诉你：模型渠道通不通、语音合成能不能真出声、语音识别模型就位没有。
**先看它的结论再问人**，能省掉大半来回。

## 五、改代码前

```bat
python tools/check_all.py
```

50 道静态/行为闸门，全绿再提交。当前基线：**50 项全过、0 项跳过**。

## 六、红线

- `config.yaml`、`data/`、`logs/`、`call_records.json`、`user_knowledge.yaml`
  只在本机，**不进任何仓库、不进任何包**。
- 要发新包，用 `python tools/pack_for_teammate.py`，别手压目录——它会自验。
"""


def write_zip(files: list[str], out_path: str, version: str) -> int:
    top = f"达妮娅AI桌面伴侣_源码_v{version}"
    notice = _notice(version, len(files))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in files:
            zf.write(os.path.join(ROOT, rel), f"{top}/{rel.replace(os.sep, '/')}")
        zf.writestr(f"{top}/{NOTICE_NAME}", notice.encode("utf-8"))
    return len(files) + 1


def verify_zip(out_path: str) -> list[str]:
    """重新打开 zip 逐条核对 + 解到临时目录跑既有闸门。返回阻断项。"""
    blocking: list[str] = []
    top_guess = None

    with zipfile.ZipFile(out_path) as zf:
        names = zf.namelist()
        bad = zf.testzip()  # CRC 校验；None = 全部完好
        if bad is not None:
            blocking.append(f"zip 内文件损坏（CRC 不符）：{bad}")

        for n in names:
            if n.endswith("/"):
                continue
            # 去掉顶层目录前缀后再套一遍排除规则——这是"包内实际内容"的复核，
            # 不是复述 collect() 的结果：万一 zipfile 那边写错路径，这里能抓到。
            inner = n.split("/", 1)[1] if "/" in n else n
            reason = _exclude_reason(inner)
            if reason:
                blocking.append(f"包内混入不该带的文件：{n}（{reason}）")

        if names:
            top_guess = names[0].split("/", 1)[0]
        missing = [r for r in REQUIRED if f"{top_guess}/{r}" not in names]
        for m in missing:
            blocking.append(f"包内缺必需文件：{m}")

    if blocking:
        return blocking

    # 解到临时目录，交给既有闸门复查（凭据扫描 + 包内静态资源完整性）。
    # 复用而不是重写：那套规则是 2026-10-17 漏掉 sticker.api_key 之后立的，
    # 有它自己的演化史，不该在打包脚本里长出第二份。
    tmp = tempfile.mkdtemp(prefix="danya_pack_verify_")
    try:
        with zipfile.ZipFile(out_path) as zf:
            zf.extractall(tmp)
        gate = os.path.join(ROOT, "tools", "check_package_secrets.py")
        proc = subprocess.run(
            [sys.executable, gate, "--dir", os.path.join(tmp, top_guess or "")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            blocking.append("凭据/资源闸门未通过：\n    " + "\n    ".join(tail[-12:]))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return blocking


def main() -> int:
    ap = argparse.ArgumentParser(description="打给队友的源码 zip（含自验）")
    ap.add_argument("--out", help="输出 zip 路径（默认 dist/ 下按版本+日期命名）")
    ap.add_argument("--dry-run", action="store_true", help="只打印清单，不写文件")
    ap.add_argument("--no-verify", action="store_true", help="跳过解包验闸（仅调试）")
    args = ap.parse_args()

    version = _app_version()
    files, dropped = collect()

    print(f"=== 入包清单：{len(files)} 个文件（仓库跟踪 "
          f"{len(files) + len(dropped)} 个）===")
    total = 0
    for rel in files:
        try:
            total += os.path.getsize(os.path.join(ROOT, rel))
        except OSError:
            pass
    print(f"    未压缩合计：{total / 1048576:.1f} MB")

    if dropped:
        print(f"\n=== 已排除：{len(dropped)} 项 ===")
        by_reason: dict[str, list[str]] = {}
        for rel, reason in dropped:
            by_reason.setdefault(reason, []).append(rel)
        for reason, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            sample = "、".join(items[:3])
            more = f" 等 {len(items)} 个" if len(items) > 3 else ""
            print(f"  - {reason}: {sample}{more}")

    blocking = [f"清单里缺必需文件：{r}" for r in REQUIRED if r not in files]
    if blocking:
        for b in blocking:
            print(f"[阻断] {b}", file=sys.stderr)
        print("\n✗ 打包已阻断：清单不完整，先修仓库再出包。", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n（--dry-run：未写文件）")
        return 0

    out_path = args.out or os.path.join(
        ROOT, "dist",
        f"达妮娅AI桌面伴侣_源码_v{version}_{__import__('datetime').date.today():%Y%m%d}.zip",
    )
    count = write_zip(files, out_path, version)
    size = os.path.getsize(out_path)
    print(f"\n=== 已出包 ===\n    {out_path}")
    print(f"    {count} 条目，{size / 1048576:.1f} MB"
          f"（压缩率 {size / total * 100:.0f}%）")

    if args.no_verify:
        print("\n（--no-verify：跳过验包。**这个包未经验证，不要直接外发**）")
        return 0

    print("\n=== 验包 ===")
    problems = verify_zip(out_path)
    if problems:
        os.remove(out_path)
        for p in problems:
            print(f"[阻断] {p}", file=sys.stderr)
        print(f"\n✗ 验包失败（{len(problems)} 项），已删除产物：{out_path}", file=sys.stderr)
        return 1

    print(f"    ✓ 包内无红线文件、无凭据泄漏、静态资源齐全、CRC 全部正确")
    print(f"\n✓ 可以发给队友：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
