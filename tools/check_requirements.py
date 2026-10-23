"""对照「实际 import」与「requirements 声明」，找出漏声明的第三方依赖。

为什么要用工具
--------------
之前靠手写规则比对，结果把 `clr`/`System`（pythonnet）判成"缺依赖"，
也漏掉了 `ref_audio_util` 这类「属于 CosyVoice 仓库、不该写进 requirements」的东西。
两者处理方式完全相反：前者该补进核心依赖，后者该写进可选依赖并注明来源。

判定方法
--------
1. AST 收集 src/ + main.py 的**绝对 import**，去掉标准库与项目内模块；
2. 用 importlib.metadata.packages_distributions() 把 import 名映射到发行包名
   （例如 yaml → PyYAML、cv2 → opencv-python、clr → pythonnet）；
3. 已安装 → 必须出现在 requirements.txt（否则换机部署会静默降级）；
   未安装 → 属于可选能力，必须出现在 requirements-optional.txt（否则没人知道要装什么）。

用法
----
    .venv/Scripts/python.exe tools/check_requirements.py
    .venv/Scripts/python.exe tools/check_requirements.py --strict
"""
from __future__ import annotations

import ast
import os
import sys
from importlib.metadata import packages_distributions

SKIP_DIRS = {"__pycache__", ".git", ".venv", "python", "node_modules"}
LOCAL_HINTS = {"src", "main", "tools"}
# 运行时才注入 sys.path 的插件包（如贴纸技能包的 backend/），非 pip 依赖
RUNTIME_ONLY = {"backend"}

# import 名与发行包名不一致的常见情况（packages_distributions 对这些覆盖不全，
# 尤其是 pywin32 这种靠 .pth 挂模块的老包）
ALIASES = {
    "win32api": "pywin32", "win32con": "pywin32", "win32gui": "pywin32",
    "win32process": "pywin32", "win32ui": "pywin32", "win32com": "pywin32",
    "win32event": "pywin32", "win32clipboard": "pywin32",
    "clr": "pythonnet", "Microsoft": "pythonnet", "System": "pythonnet",
    "cv2": "opencv-python", "yaml": "pyyaml", "PIL": "pillow",
    "sherpa_onnx": "sherpa-onnx", "yt_dlp": "yt-dlp",
    "gradio_client": "gradio-client", "dotenv": "python-dotenv",
    "fitz": "pymupdf", "sklearn": "scikit-learn",
}


def norm(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "").replace(".", "")


def req_names(path: str) -> set[str]:
    out: set[str] = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.split("#")[0].strip()
            if not s or s.startswith("-"):
                continue
            # 去掉版本约束与环境标记
            for sep in ("==", ">=", "<=", "~=", ">", "<", "[", ";", " "):
                s = s.split(sep)[0]
            if s:
                out.add(norm(s))
    return out


def notpip_names(path: str) -> set[str]:
    """解析 `# not-pip: 名字`：随仓库提供、不属于 pip 安装的可选模块。"""
    out: set[str] = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s.startswith("#"):
                continue
            body = s.lstrip("#").strip()
            if body.lower().startswith("not-pip:"):
                name = body.split(":", 1)[1].split("#")[0].strip()
                if name:
                    out.add(norm(name))
    return out


def collect_imports(root: str) -> tuple[dict[str, list[str]], list[str]]:
    """收集第三方 import。返回 (imports, 解析失败文件列表)。

    【2026-10-24 修·闸门沉默】原来遇 SyntaxError 直接 continue——那个文件的 import
    全都扫不到，漏声明会静默发生（正是本闸门存在的理由）。现在解析失败一律记下来，
    由 main 报错并在 --strict 下判红。读不了（OSError）同理。
    """
    mods: dict[str, list[str]] = {}
    broken: list[str] = []
    for base in ("src", "main.py"):
        p = os.path.join(root, base)
        paths = [p] if os.path.isfile(p) else []
        if os.path.isdir(p):
            for r, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                paths += [os.path.join(r, n) for n in names if n.endswith(".py")]
        for f in paths:
            try:
                tree = ast.parse(open(f, encoding="utf-8", errors="replace").read())
            except SyntaxError as e:
                broken.append(f"{os.path.relpath(f, root)}: SyntaxError: {e}")
                continue
            except OSError as e:
                broken.append(f"{os.path.relpath(f, root)}: 读不了: {e}")
                continue
            for n in ast.walk(tree):
                names: list[str] = []
                if isinstance(n, ast.Import):
                    names = [a.name.split(".")[0] for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module.split(".")[0]]
                for m in names:
                    mods.setdefault(m, []).append(f"{os.path.relpath(f, root)}:{n.lineno}")
    return mods, broken


def excluded_names(path: str) -> set[str]:
    """requirements.txt 里被注释掉的包名 = 作者显式决定不装（体积/许可等原因）。"""
    out: set[str] = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s.startswith("#"):
                continue
            token = s.lstrip("#").strip()
            # 只认「整行就是一个包名」的注释，避免把说明文字当包名
            if token and " " not in token and len(token) < 40:
                out.add(norm(token))
    return out


def local_modules(root: str) -> set[str]:
    """src/ 与 tools/ 下的包目录名与文件名 —— 这些是项目自己的模块，不是 pip 依赖。

    为什么必须带上 tools/：不少测试与脚本会 `sys.path.insert(tools)` 之后
    `import doctor` / `import make_dist`。只扫 src/ 的话，这些本地模块会被
    判成「未安装且未声明」的第三方依赖，把依赖闸染红（2026-10-18 撞到）。
    """
    out = set(LOCAL_HINTS)
    for sub in ("src", "tools"):
        base = os.path.join(root, sub)
        for r, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for d in dirs:
                out.add(d)
            for n in names:
                if n.endswith(".py"):
                    out.add(n[:-3])
    return out


def main() -> int:
    root = os.path.abspath(".")
    strict = "--strict" in sys.argv
    stdlib = set(sys.stdlib_module_names)
    mapping = packages_distributions()

    declared = req_names(os.path.join(root, "requirements.txt"))
    optional = req_names(os.path.join(root, "requirements-optional.txt"))
    optional |= notpip_names(os.path.join(root, "requirements-optional.txt"))
    excluded = excluded_names(os.path.join(root, "requirements.txt"))
    local = local_modules(root)

    imports, broken = collect_imports(root)
    builtin_names = set(dir(__builtins__)) if isinstance(__builtins__, dict) is False else set(__builtins__)

    installed_missing: list[tuple[str, str, str]] = []
    optional_missing: list[tuple[str, str, str]] = []
    excluded_used: list[tuple[str, str, str]] = []
    ok = 0

    for m, locs in sorted(imports.items()):
        if m in stdlib or m in local or m in builtin_names or m in RUNTIME_ONLY or m.startswith("_"):
            continue
        dists = mapping.get(m)
        alias = ALIASES.get(m)
        candidates = set(dists or [])
        if alias:
            candidates.add(alias)
        # import 名本身也参与匹配：有些包（如 not-pip 登记的仓库模块）没有分发元数据
        candidates.add(m)
        candidates = {norm(c) for c in candidates}

        if any(c in declared or c in optional for c in candidates):
            ok += 1
        elif any(c in excluded for c in candidates) or norm(m) in excluded:
            excluded_used.append((m, alias or (dists[0] if dists else "—"), locs[0]))
        elif dists:
            installed_missing.append((m, dists[0], locs[0]))
        else:
            optional_missing.append((m, alias or "（未安装/未声明）", locs[0]))

    total = len([m for m in imports if m not in stdlib and m not in local])
    print(f"第三方 import {total} 个：已声明 {ok}，注释排除 {len(excluded_used)}，"
          f"已装未声明 {len(installed_missing)}，未装未声明 {len(optional_missing)}\n")

    if installed_missing:
        print("=== 已安装但 requirements.txt 未声明（换机部署会静默降级）===")
        for m, dist, loc in installed_missing:
            print(f"  import {m:20s} → 发行包 {dist:24s} 首见 {loc}")
    if optional_missing:
        print("\n=== 未安装且无声明（可选能力，应写进 requirements-optional.txt）===")
        for m, dist, loc in optional_missing:
            print(f"  import {m:20s} → {dist:24s} 首见 {loc}")
    if excluded_used:
        print("\n=== 已显式排除但仍被 import（确认调用点有兜底）===")
        for m, dist, loc in excluded_used:
            print(f"  import {m:20s} → {dist:24s} 首见 {loc}")
    if not (installed_missing or optional_missing):
        print("✓ 依赖声明完整")

    # 【2026-10-24】解析失败必须显式报出来：这些文件的 import 没被扫到，
    # 不报就等于"少扫一个文件但退出码仍 0"（闸门沉默）。
    if broken:
        print("\n=== 解析失败（这些文件的 import 没进本次统计，--strict 下判红）===")
        for b in broken:
            print("  " + b)

    if strict and (installed_missing or optional_missing or broken):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
