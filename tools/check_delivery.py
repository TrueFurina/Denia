# -*- coding: utf-8 -*-
"""`tools/check_delivery.py` — 交付包完整性自检（发布前闸门）。

为什么要有它（2026-11-08 质检暴露的真问题）：
  源码仓有 `tools/check_all.py` 57 道闸门，但那些文件**不随交付包分发**；
  结果交付包侧一次「`config.release.yaml` 漏发」没人拦得住 —— 用户误删
  `config.yaml` 后 `ensure_config_file()` 返回 missing → `SystemExit(2)`，
  窗口不出现，而 README 还写着「可以自愈」。文档与实物脱节，全套 doctor 照样全绿。

这道闸门补的正是**打包清单**：检查必备文件齐不齐、资源子树有没有整个漏发、
出厂模板是否干净、以及「误删自愈」这条承诺在**当前这个包**里是否真的成立。

【2026-11-08 第八轮·清单实证反推】
  第七轮那 13 个必备文件是手工列的，闸门会「清单里没写就恒绿」：实测包里
  `src/ui/web/` 有 120 个文件、`src/` 有 200 个 py、`python/Lib/site-packages`
  有 9762 个文件，整个 web UI 漏发也照样全绿。所以本轮改成三层覆盖：
    ① 必备文件：逐条注明证据来源（入口/doctor 判据/硬编码 URL/AST 引用）；
    ② 资源目录：按实测规模设保守下限，挡住「整个子树没打进包」；
    ③ 源码闭包：AST 扫 `src/**/*.py` 的全部 `src.*` 绝对/相对导入并逐个解析，
       不用手工罗列 200 个文件，也不会因为「清单没写这个模块」而漏检。
  这三层仍不枚举每个文件，但漏发任何被代码实际引用的模块/页面都会变红。

【2026-11-08 第十轮·文件在但坏了也会恒绿】
  `check_required_files` 只判 `isfile`。实测把 `index.html` 截成 0 字节、
  `electron.exe` 截成 1 字节，全套检查仍 0 FAIL。新增 `check_file_integrity`：
  按产物格式本身的客观标记验实体（PE `MZ` / HTML 结构标记 / RIFF-WAVE /
  非空文本 / `config.yaml` 可解析为映射）。

【2026-11-08 第九轮·修 --root 假绿】
  旧 `check_self_heal` 直接 `from src.config import ...`，验的是**闸门所在包**的实现；
  用 `--root` 指向一份 `ensure_config_file` 已损坏的新包时照样判 PASS（已复现）。
  现改为从目标包 `src/config.py` 里 AST 抽出该函数单独执行，验的才是待发包自己。

设计原则与 `tools/doctor.py` 一致：
  1. 只读。不改交付包里的任何文件；自愈验证全在临时目录做。
  2. 逐项跑完再汇总，单项失败不影响其它项。
  3. 退出码：有 FAIL → 1；只有 WARN/PASS → 0。
  4. `--root` 可指向另一份待验交付包，便于发布流程在打包完成后调用；
     闸门自身**不**列入必备文件（CI 可以把脚本放在检出目录里验打包产物）。
  5. 在源码仓根直接跑 → 退出码 77（跳过）：那里没有 python/、node_modules/，
     判据不适用，不算通过也不算失败。打包后 `--root <产物>` 才是真检。

用法：
  python/python.exe tools/check_delivery.py
  python/python.exe tools/check_delivery.py --root <待验目录>
  python/python.exe tools/check_delivery.py --json
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# 脚本自身所在的交付包：仅用于默认 --root 与定位模板，不再作为代码校验的兜底来源。
SCRIPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, SCRIPT_ROOT)

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# 必备文件。每条都有实证来源（2026-11-08 第八轮反推，别再手工拍脑袋加条目）：
#   · 配置/自愈   —— doctor.check_config:705-719、src.config.ensure_config_file
#   · 入口        —— launch_companion.py: MAIN/PROJ、pick_python() 的 bundled 路径
#   · 自检        —— doctor._parse_requirements(requirements.txt)、doctor.check_shell
#   · 内置运行时  —— embeddable python 光有 exe 起不来；doctor 判活也依赖这些库
#   · Electron 壳 —— doctor.check_shell:343-357 的三个判据 + shell/main.js:64 preload
#   · 源码入口    —— doctor.py 里 13 处 `from src...` 直接 import 的模块
#   · Web UI      —— main_window.py:779 / 911 / 1070 与 shell/main.js:34-35 的硬编码 URL
#   · 提示词      —— proactive.py:8927、cascade_brain.py:498-499、
#                    cloud_omni_realtime.py:2632、task_cli.py:103
#   · 参考音频    —— assets/voices（音色克隆参考样本）
# 其余 200 个 src/*.py 由 check_source_closure 的 AST 闭包覆盖，不在此罗列。
# `data/`、`logs/`、握手文件都是运行产物，绝不列进来。
REQUIRED_FILES = (
    "config.yaml",
    "config.release.yaml",          # 2026-11-08 曾被漏发，见文件头
    "main.py",
    "launch_companion.py",
    "tools/doctor.py",
    "requirements.txt",
    "python/python.exe",
    "python/pythonw.exe",
    "python/python312.dll",
    "python/vcruntime140.dll",
    "python/Lib/site-packages/yaml/__init__.py",
    "python/Lib/site-packages/sounddevice.py",
    "python/Lib/site-packages/vosk/__init__.py",
    "python/Lib/site-packages/webview/__init__.py",
    "shell/package.json",
    "shell/main.js",
    "shell/preload.js",
    "shell/node_modules/electron/dist/electron.exe",
    "src/__init__.py",
    "src/config.py",
    "src/config_schema.py",
    "src/version.py",
    "src/log_rotate.py",
    "src/ui/pet_shell.py",
    "src/ui/pet_state.py",
    "src/ui/pet_backend.py",
    "src/ui/web/index.html",
    "src/ui/web/loading.html",
    "src/ui/web/notify.html",
    "src/ui/web/live2d/rig/index.html",
    "prompts/danya_call_prompt.txt",
    "prompts/workbuddy_persona.txt",
    "prompts/call_profiles/danya_persona.txt",
    "prompts/call_profiles/danya_behavior.txt",
    "assets/voices/daniya_ref.wav",
    "Uninstall.exe",
)

# 资源子树：只查「目录存在 + 文件数不低于保守下限」，不枚举每个文件。
# 下限 = 本包实测规模的一半左右，允许后续合理瘦身，但整棵子树漏发/只发几个文件必红。
# 实测（2026-11-08）：web 120 / prompts 4 / voices 2 / electron dist 22 /
# site-packages 9762 / DLLs 29。
REQUIRED_DIRS = (
    ("src/ui/web", 60, "main_window.py:44 以它为 web_root"),
    ("prompts", 4, "cascade_brain / proactive 的人设与提示词"),
    ("assets/voices", 2, "音色克隆参考音频"),
    ("shell/node_modules/electron/dist", 10, "Electron 运行时文件树"),
    ("python/Lib/site-packages", 500, "内置依赖树"),
    ("python/DLLs", 10, "内置 C 扩展"),
)

# 【2026-11-08 第十轮】关键产物「在但是坏了」的客观判据。
# 只看 os.path.isfile 会被 0 字节页面 / 1 字节 exe 骗过去（已复现：整包 0 FAIL）。
# 判据全部取自产物格式本身，不是拍脑袋的大小阈值：
#   PE(.exe/.dll) 头 b"MZ"；HTML 头部有 <!doctype/<html/<head/<body；
#   WAV 为 RIFF....WAVE；JS/文本去掉空白后非空；config.yaml 必须解析成映射。
PE_FILES = (
    "python/python.exe", "python/pythonw.exe", "python/python312.dll",
    "python/vcruntime140.dll", "shell/node_modules/electron/dist/electron.exe",
    "Uninstall.exe",
)
HTML_FILES = (
    "src/ui/web/index.html", "src/ui/web/loading.html", "src/ui/web/notify.html",
    "src/ui/web/live2d/rig/index.html",
)
WAV_FILES = ("assets/voices/daniya_ref.wav",)
TEXT_FILES = (
    "shell/main.js", "shell/preload.js", "requirements.txt",
    "prompts/danya_call_prompt.txt", "prompts/workbuddy_persona.txt",
    "prompts/call_profiles/danya_persona.txt", "prompts/call_profiles/danya_behavior.txt",
)
MIN_ARTIFACT_BYTES = 64   # 真实产物最小也有 2KB；这一条只挡空文件/残片

# 【2026-11-08 第十一轮】requirements.txt 的「核心」段必须在**包内运行时**里真的装好。
# 判据用 site-packages 下的 *.dist-info（pip 安装的权威痕迹），不 import 试跑 ——
# 闸门保持只读、不执行目标包代码。doctor 的「核心依赖」要等用户在真机上 import 才报红，
# 那时包已经发出去了；这一项让打包阶段就能挡住「改了 requirements 但没重装进内置运行时」。
BUNDLED_SITE_PACKAGES = "python/Lib/site-packages"

# 发行名 → import 名。只列「按名字猜不出来」的那些，其余走小写/下划线兜底。
DIST2IMPORT = {
    "pyyaml": "yaml", "pillow": "PIL", "pywebview": "webview",
    "pywin32": "win32api", "pythonnet": "clr",
    "edge-tts": "edge_tts", "websocket-client": "websocket",
    "opencv-python": "cv2", "pygetwindow": "pygetwindow",
    "pyaudiowpatch": "pyaudiowpatch", "imageio-ffmpeg": "imageio_ffmpeg",
    "rapidocr-onnxruntime": "rapidocr_onnxruntime",
}

# 【2026-11-08 第十二轮】可选运行时冒烟：静态判据再多也证明不了「内置解释器真能跑」。
# 前面 8 项全绿仍可能遇到：位数不匹配、UCRT 缺失、杀软拦 python.exe、site-packages
# 里只有目录没有可加载的 .pyd。这些只有**真跑一次**才知道。
#
# 默认关闭：开启后闸门会执行**目标包内的 python.exe**（只跑下面这段固定探针，
# 不写文件、不联网、不加载配置）。只对自己打出来的包用；`--root` 指向来路不明的
# 目录时不要开。
SMOKE_PROBE = (
    "import sys, numpy, yaml;"
    "print('SMOKE_OK', sys.version_info[0], sys.version_info[1],"
    " numpy.__version__, yaml.__version__, int(getattr(sys.flags, 'isolated', 0)))"
)
SMOKE_TIMEOUT_S = 120

# 已知缺件：降级路径明确、不影响启动，不让它把整道闸门染红，但会持续 WARN。
# 任何**新的**未解析导入一律 FAIL —— 这是「清单没写就恒绿」的兜底。
KNOWN_MISSING_IMPORTS = {
    "src.ui.quota_guard": (
        "src/ui/webapp.py:7729 在 try/except Exception 里 import，"
        "包内无此模块 → 设置页额度检测静默失败（P2，非启动阻断）"
    ),
}

TEMPLATE_NAME = "config.release.yaml"
_ENV_REF = re.compile(r"^\$\{[A-Za-z0-9_]+\}$")


def _read_yaml(path: str):
    import yaml

    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _iter_api_keys(node, prefix=""):
    """产出 (路径, 值)。递归，别猜某个顶层键 —— doctor 这里踩过恒真的坑。"""
    if isinstance(node, dict):
        for k, v in node.items():
            p = prefix + "/" + str(k)
            if k == "api_key":
                yield p, v
            yield from _iter_api_keys(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_api_keys(v, prefix + "[%d]" % i)


def check_required_files(root: str) -> tuple[str, str, str, str]:
    missing = [f for f in REQUIRED_FILES
               if not os.path.isfile(os.path.join(root, f.replace("/", os.sep)))]
    if missing:
        return (FAIL, "必备文件",
                f"缺 {len(missing)}/{len(REQUIRED_FILES)} 个：{', '.join(missing)}",
                "补齐打包清单后再发布；尤其是 config.release.yaml（漏发会让误删自愈失效）")
    return (PASS, "必备文件", f"{len(REQUIRED_FILES)} 个必备文件齐全", "")


def check_required_dirs(root: str) -> tuple[str, str, str, str]:
    """挡住「整棵资源子树漏发」——平铺的文件清单管不了这种缺口。"""
    problems = []
    counts = []
    for rel, minimum, why in REQUIRED_DIRS:
        d = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isdir(d):
            problems.append(f"缺目录 {rel}（{why}）")
            continue
        n = sum(1 for _, _, fs in os.walk(d) for _f in fs)
        counts.append(f"{rel}={n}")
        if n < minimum:
            problems.append(f"{rel} 只有 {n} 个文件（下限 {minimum}）")
    if problems:
        return (FAIL, "资源目录", "；".join(problems),
                "这些目录是运行时资源根，整棵漏发时 doctor 也查不出来；补齐后重打包")
    return (PASS, "资源目录", f"{len(REQUIRED_DIRS)} 棵子树均达标（{', '.join(counts)}）", "")


def _iter_src_py(root: str):
    srcdir = os.path.join(root, "src")
    for base, dirs, files in os.walk(srcdir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def _module_exists(root: str, dotted: str) -> bool:
    rel = os.path.join(root, *dotted.split("."))
    return os.path.isfile(rel + ".py") or os.path.isfile(os.path.join(rel, "__init__.py"))


def check_source_closure(root: str) -> tuple[str, str, str, str]:
    """AST 静态导入闭包：不用手工罗列 200 个 py，缺哪个被引用模块都能查出来。

    只做静态解析（不 import、不触发任何副作用），覆盖绝对 `src.*` 与包内相对导入。
    局限：动态 import（importlib/字符串）与 `from . import X` 里的**属性名**不判定，
    那是 compileall 和端到端启动测试的活。
    """
    files = list(_iter_src_py(root))
    if not files:
        return (FAIL, "源码闭包", "src/ 下没有任何 .py 文件", "检查是否漏发源码目录")
    targets: set[str] = set()
    bad_syntax = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except SyntaxError as e:
            bad_syntax.append(f"{os.path.basename(path)}:{e.lineno}")
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")[:-3].split("/")
        pkg_parts = rel[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    cut = len(pkg_parts) - (node.level - 1)
                    parts = (pkg_parts[:cut] if cut > 0 else []) + (
                        [node.module] if node.module else [])
                    dotted = ".".join(p for p in parts if p)
                    if dotted.startswith("src.") or dotted == "src":
                        targets.add(dotted)
                elif node.module and (node.module == "src" or node.module.startswith("src.")):
                    targets.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "src" or alias.name.startswith("src."):
                        targets.add(alias.name)
    if bad_syntax:
        return (FAIL, "源码闭包", f"{len(bad_syntax)} 个 py 语法错误（{', '.join(bad_syntax[:3])}）",
                "先跑 python -m compileall -q src")
    missing = sorted(m for m in targets if not _module_exists(root, m))
    known = [m for m in missing if m in KNOWN_MISSING_IMPORTS]
    novel = [m for m in missing if m not in KNOWN_MISSING_IMPORTS]
    base = f"{len(files)} 个 py / {len(targets)} 个 src 导入目标"
    if novel:
        return (FAIL, "源码闭包", f"{base}；{len(novel)} 个模块缺失：{', '.join(novel[:5])}",
                "打包清单漏发源码；补齐对应 .py 或移除失效引用")
    if known:
        return (WARN, "源码闭包",
                f"{base}；已知缺件 {', '.join(known)}（{KNOWN_MISSING_IMPORTS[known[0]]}）",
                "源码仓补发该模块，或删掉这段永远走 except 的死引用；补好后从白名单移除")
    if not targets:
        return (WARN, "源码闭包", f"{base}；未解析到任何 src 导入（可能是最小包）",
                "在完整交付包里运行本脚本")
    return (PASS, "源码闭包", f"{base}，全部可解析", "")


def check_file_integrity(root: str) -> tuple[str, str, str, str]:
    """文件「在」不等于「能用」：挡住空文件、截断产物、错误格式的占位包。

    判据是格式魔数/结构标记，不是文件大小猜测；缺文件由 check_required_files 报，
    这里跳过以免重复刷屏。
    """
    problems: list[str] = []
    checked = 0

    def _path(rel: str) -> str:
        return os.path.join(root, rel.replace("/", os.sep))

    def _head(rel: str, n: int = 512) -> bytes:
        with open(_path(rel), "rb") as fh:
            return fh.read(n)

    for rel in PE_FILES:
        p = _path(rel)
        if not os.path.isfile(p):
            continue
        checked += 1
        size = os.path.getsize(p)
        if size < MIN_ARTIFACT_BYTES:
            problems.append(f"{rel} 仅 {size} 字节（疑似截断/占位）")
        elif _head(rel, 2) != b"MZ":
            problems.append(f"{rel} 不是 PE 文件（头 {_head(rel, 2)!r}）")
    for rel in HTML_FILES:
        p = _path(rel)
        if not os.path.isfile(p):
            continue
        checked += 1
        size = os.path.getsize(p)
        head = _head(rel).lower()
        if size < MIN_ARTIFACT_BYTES:
            problems.append(f"{rel} 仅 {size} 字节（疑似截断/占位）")
        elif not any(m in head for m in (b"<!doctype", b"<html", b"<head", b"<body")):
            problems.append(f"{rel} 没有 HTML 结构标记（头 {head[:24]!r}）")
    for rel in WAV_FILES:
        p = _path(rel)
        if not os.path.isfile(p):
            continue
        checked += 1
        h = _head(rel, 12)
        if h[:4] != b"RIFF" or h[8:12] != b"WAVE":
            problems.append(f"{rel} 不是 RIFF/WAVE 音频（头 {h!r}）")
    for rel in TEXT_FILES:
        p = _path(rel)
        if not os.path.isfile(p):
            continue
        checked += 1
        if not _head(rel, 4096).strip():
            problems.append(f"{rel} 为空/全空白")
    live = _path("config.yaml")
    if os.path.isfile(live):
        checked += 1
        try:
            if not isinstance(_read_yaml(live), dict):
                problems.append("config.yaml 顶层不是映射")
        except Exception as e:  # noqa: BLE001
            problems.append(f"config.yaml YAML 解析失败：{e}")

    if problems:
        return (FAIL, "文件实体",
                f"{checked} 个关键产物中 {len(problems)} 处异常：{'; '.join(problems[:4])}"
                + (f"（另有 {len(problems) - 4} 处）" if len(problems) > 4 else ""),
                "重新打包：这些文件存在但内容不对，doctor 与平铺清单都查不出来")
    return (PASS, "文件实体",
            f"{checked} 个关键产物格式/内容正常（PE MZ、HTML、RIFF/WAVE、非空文本、YAML 映射）", "")


def _norm_dist(name: str) -> str:
    """与 tools/doctor.py:_norm 同款：去掉 -/_/. 并小写，规避发行名写法差异。"""
    return name.lower().replace("-", "").replace("_", "").replace(".", "")


def _parse_requirement_sections(path: str) -> dict[str, list[str]]:
    """按注释分节解析 requirements（核心 / 重要 / 可选）。

    约定与 `tools/doctor.py:_parse_requirements` **完全一致**（靠
    `# ===== 核心 =====` 这类注释行分节）。两边不能各读一套，否则闸门与自检会互相打架。
    """
    sections: dict[str, list[str]] = {}
    current = "未分类"
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("#"):
                if "核心" in line:
                    current = "核心"
                elif "重要" in line:
                    current = "重要"
                elif "可选" in line:
                    current = "可选"
                continue
            s = line.split("#")[0].strip()
            if not s or s.startswith("-"):
                continue
            for sep in ("==", ">=", "<=", "~=", ">", "<", "[", ";", " ", "!"):
                s = s.split(sep)[0]
            if s:
                sections.setdefault(current, []).append(s)
    return sections


def _installed_dists(sp: str) -> dict[str, str]:
    """`site-packages/*.dist-info` → {规范化发行名: 目录名}。

    目录名是 `name-version.dist-info`，版本段不含 `-`，所以从右切一刀即可。
    """
    out: dict[str, str] = {}
    if not os.path.isdir(sp):
        return out
    for entry in os.listdir(sp):
        if not entry.lower().endswith(".dist-info"):
            continue
        base = entry[: -len(".dist-info")]
        name = base.rsplit("-", 1)[0] if "-" in base else base
        out[_norm_dist(name)] = entry
    return out


def _module_present(sp: str, dist: str) -> bool:
    """没有 dist-info 时退一步看模块本体在不在（能用，但缺元数据）。"""
    imp = DIST2IMPORT.get(_norm_dist(dist), dist.lower().replace("-", "_"))
    return (os.path.isfile(os.path.join(sp, imp + ".py"))
            or os.path.isfile(os.path.join(sp, imp, "__init__.py")))


def check_dependencies(root: str) -> tuple[str, str, str, str]:
    """核心依赖必须在包内运行时里装齐；重要缺了降级 WARN；可选不管。"""
    req = os.path.join(root, "requirements.txt")
    if not os.path.isfile(req):
        return (FAIL, "依赖完整", "缺 requirements.txt，无法核对依赖", "补回 requirements.txt")
    try:
        sections = _parse_requirement_sections(req)
    except OSError as e:  # noqa: BLE001
        return (FAIL, "依赖完整", f"读不了 requirements.txt：{e}", "检查文件权限/编码")
    core = sections.get("核心") or []
    if not core:
        return (WARN, "依赖完整", "requirements.txt 未解析出「核心」段",
                "检查 `# ===== 核心 =====` 这类分节注释是否还在")
    sp = os.path.join(root, BUNDLED_SITE_PACKAGES.replace("/", os.sep))
    if not os.path.isdir(sp):
        return (FAIL, "依赖完整", f"缺内置依赖目录 {BUNDLED_SITE_PACKAGES}",
                "重新打包内置运行时")
    dists = _installed_dists(sp)
    missing: list[str] = []
    meta_only: list[str] = []
    for dist in core:
        if _norm_dist(dist) in dists:
            continue
        (meta_only if _module_present(sp, dist) else missing).append(dist)
    important = sections.get("重要") or []
    imp_missing = [d for d in important
                   if _norm_dist(d) not in dists and not _module_present(sp, d)]
    optional = sections.get("可选") or []
    opt_have = sum(1 for d in optional
                   if _norm_dist(d) in dists or _module_present(sp, d))
    base = (f"核心 {len(core) - len(missing)}/{len(core)} · "
            f"重要 {len(important) - len(imp_missing)}/{len(important)} · "
            f"可选 {opt_have}/{len(optional)}")
    if missing:
        return (FAIL, "依赖完整", f"{base}；核心缺 {', '.join(missing)}",
                "打包时没把核心依赖装进内置运行时：重跑 make_dist.py --install-deps")
    if meta_only:
        return (WARN, "依赖完整",
                f"{base}；{', '.join(meta_only)} 有模块但无 dist-info 元数据",
                "运行不受影响，但 pip 升级/卸载认不出它；建议重新 pip install 生成元数据")
    if imp_missing:
        return (WARN, "依赖完整",
                f"{base}；重要缺 {', '.join(imp_missing)}（对应功能降级，不阻断启动）",
                "按需补装；这些属「重要」档，doctor 会逐项说明影响")
    return (PASS, "依赖完整", f"{base}，核心与重要均在包内", "")


def check_template(root: str) -> tuple[str, str, str, str]:
    tpl = os.path.join(root, TEMPLATE_NAME)
    if not os.path.isfile(tpl):
        return (FAIL, "出厂模板", f"缺 {TEMPLATE_NAME}——误删 config.yaml 将无法自愈",
                "打包清单加入 config.release.yaml（出厂态：force_first_run=true）")
    try:
        raw = _read_yaml(tpl)
    except Exception as e:  # noqa: BLE001
        return (FAIL, "出厂模板", f"YAML 解析失败：{e}", "检查模板缩进/编码，别把实配当模板打包")
    if not isinstance(raw, dict):
        return (FAIL, "出厂模板", "模板顶层不是映射", "重新从出厂配置生成模板")
    if raw.get("force_first_run") is not True:
        return (FAIL, "出厂模板",
                f"force_first_run={raw.get('force_first_run')!r}（应为 true 的出厂态）",
                "模板必须保留 force_first_run: true，否则新装用户不会走引导")
    # 版本一致性：模板与实配的 app.version 必须同版本，否则恢复出来的配置是旧 schema
    live = os.path.join(root, "config.yaml")
    if os.path.isfile(live):
        try:
            lv = (_read_yaml(live).get("app") or {}).get("version")
            tv = (raw.get("app") or {}).get("version")
            if lv != tv:
                return (FAIL, "出厂模板", f"app.version 与 config.yaml 不一致（模板 {tv} / 实配 {lv}）",
                        "模板要跟当前版本一起重新生成")
        except Exception as e:  # noqa: BLE001
            return (WARN, "出厂模板", f"无法比对实配版本：{e}", "确认 config.yaml 可解析")
    return (PASS, "出厂模板",
            f"{TEMPLATE_NAME} 合法（force_first_run=true，app.version={(raw.get('app') or {}).get('version')}）", "")


def check_template_secrets(root: str) -> tuple[str, str, str, str]:
    """模板里不许出现真实密钥。${VAR} 引用是允许的（源码仓模板就靠它）。"""
    tpl = os.path.join(root, TEMPLATE_NAME)
    if not os.path.isfile(tpl):
        return (FAIL, "模板密钥卫生", f"缺 {TEMPLATE_NAME}，无法检查", "先补齐模板")
    try:
        raw = _read_yaml(tpl)
    except Exception as e:  # noqa: BLE001
        return (FAIL, "模板密钥卫生", f"YAML 解析失败：{e}", "修好模板再验")
    total = 0
    leaked = []
    for path, val in _iter_api_keys(raw):
        total += 1
        if not isinstance(val, str) or not val.strip():
            continue
        if _ENV_REF.match(val.strip()):
            continue  # ${XXX_API_KEY} 是有意的环境变量引用
        leaked.append(path)
    if leaked:
        return (FAIL, "模板密钥卫生",
                f"模板夹带 {len(leaked)} 个明文密钥（{', '.join(leaked[:3])}）",
                "立刻换成空值或 ${VAR} 引用；这份模板会被分发给所有用户")
    return (PASS, "模板密钥卫生", f"{total} 个 api_key 字段均为空或环境变量引用，无明文密钥", "")


def _load_target_ensure(root: str):
    """取出**待验包自己**的 ensure_config_file，而不是闸门所在包的同名函数。

    【2026-11-08 第九轮·修 --root 假绿】
    旧实现直接 `from src.config import ...`，`sys.path[0]` 是 SCRIPT_ROOT —— 于是
    `--root B` 验的其实是 A 的自愈逻辑。实测：把 B 的 ensure_config_file 改成恒返回
    missing，旧实现照样判 PASS。发布流程恰恰是最需要用 `--root` 验「刚打出来的那份包」，
    这个假绿比没有闸门更危险。

    做法：AST 从 `root/src/config.py` 里**只**抽出 ensure_config_file 这一个函数与
    CONFIG_TEMPLATE_NAMES 常量，放进干净的命名空间执行。不 import 整包、不触发
    任何模块级副作用、不依赖 yaml/win32 等第三方库，也不会落到 SCRIPT_ROOT 的 src。
    边界：这不是沙箱，只执行从待验包源码中抽出的单个函数；待验包本身需是可信产物。
    """
    path = os.path.join(root, "src", "config.py")
    if not os.path.isfile(path):
        raise ValueError(f"缺 {path}，无法验证目标包自愈")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read(), filename=path)
    fn = next((n for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "ensure_config_file"), None)
    if fn is None:
        raise ValueError("src/config.py 里找不到 ensure_config_file 定义")
    tpl_names = None
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONFIG_TEMPLATE_NAMES" for t in n.targets):
            tpl_names = ast.literal_eval(n.value)
            break
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                and n.target.id == "CONFIG_TEMPLATE_NAMES" and n.value is not None:
            tpl_names = ast.literal_eval(n.value)
            break
    if tpl_names is None:
        raise ValueError("无法静态提取 CONFIG_TEMPLATE_NAMES")
    ns = {"os": os, "shutil": shutil, "CONFIG_TEMPLATE_NAMES": tpl_names}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), path, "exec"), ns)  # noqa: S102
    return ns["ensure_config_file"]


def check_self_heal(root: str) -> tuple[str, str, str, str]:
    """在临时目录复验**目标包**的 ensure_config_file 三态，绝不动交付包里的实配。"""
    tpl = os.path.join(root, TEMPLATE_NAME)
    if not os.path.isfile(tpl):
        return (FAIL, "自愈三态", f"缺 {TEMPLATE_NAME}，无法验证自愈", "补齐模板")
    try:
        ensure_config_file = _load_target_ensure(root)
    except Exception as e:  # noqa: BLE001
        return (FAIL, "自愈三态", f"无法读取目标包自愈实现：{type(e).__name__}: {e}",
                "检查 --root 指向的是完整交付包；闸门不会再拿自己包里的实现顶替")
    try:
        with tempfile.TemporaryDirectory(prefix="qc_delivery_heal_") as td:
            tpl_copy = os.path.join(td, TEMPLATE_NAME)
            shutil.copyfile(tpl, tpl_copy)
            target = os.path.join(td, "config.yaml")
            s1, _ = ensure_config_file(target)
            created_ok = (s1 == "created" and os.path.isfile(target)
                          and open(target, "rb").read() == open(tpl_copy, "rb").read())
            before = open(target, "rb").read() if os.path.isfile(target) else b""
            s2, _ = ensure_config_file(target)
            exists_ok = (s2 == "exists" and open(target, "rb").read() == before)
            with tempfile.TemporaryDirectory(prefix="qc_delivery_notpl_") as td2:
                s3, _ = ensure_config_file(os.path.join(td2, "config.yaml"))
                missing_ok = (s3 == "missing")
        bad = [n for n, ok in (("created", created_ok), ("exists", exists_ok),
                              ("missing", missing_ok)) if not ok]
        if bad:
            return (FAIL, "自愈三态", f"行为异常：{', '.join(bad)}（实测 {s1}/{s2}/{s3}）",
                    "检查 src/config.py:ensure_config_file 与模板是否同目录")
        return (PASS, "自愈三态",
                "目标包自身实现：缺配置→created（与模板逐字节一致）/ 有实配→exists（不覆盖）/ 无模板→missing", "")
    except Exception as e:  # noqa: BLE001
        return (FAIL, "自愈三态", f"验证过程异常：{type(e).__name__}: {e}", "检查模板与文件权限")


def check_runtime_smoke(root: str) -> tuple[str, str, str, str]:
    """真跑一次包内解释器（`python.exe -c "<固定探针>"`）。

    只验证「能启动 + 核心 C 扩展能导入」：numpy 依赖 UCRT 与 BLAS，它导得进来，
    说明位数、VC 运行时、site-packages 里的 .pyd 这几件静态判据看不出的东西是对的。
    探针不读配置、不写文件、不联网。
    """
    exe = os.path.join(root, "python", "python.exe")
    if not os.path.isfile(exe):
        return (WARN, "运行时冒烟", "包内没有 python/python.exe，无法冒烟",
                "先修「必备文件」；这一项只是补充证据，不重复计红")
    try:
        proc = subprocess.run([exe, "-c", SMOKE_PROBE], cwd=root,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=SMOKE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return (FAIL, "运行时冒烟", f"解释器 {SMOKE_TIMEOUT_S}s 内没有返回",
                "内置运行时可能卡死，或被安全软件拦住；手工跑同一条命令确认")
    except OSError as e:  # noqa: BLE001
        return (FAIL, "运行时冒烟", f"解释器起不来：{e}",
                "常见原因：32/64 位不匹配、DLL 依赖缺失、被杀软隔离")
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 or "SMOKE_OK" not in out:
        tail = out.replace("\n", " | ")[-300:] or "(无输出)"
        return (FAIL, "运行时冒烟", f"exit={proc.returncode}，输出：{tail}",
                "内置解释器跑不起来或核心扩展导入失败；用同一 exe 手工复跑看完整报错")
    parts = out.split()
    pyver = ".".join(parts[1:3]) if len(parts) >= 3 else "?"
    numpy_v = parts[3] if len(parts) >= 4 else "?"
    yaml_v = parts[4] if len(parts) >= 5 else "?"
    isolated = parts[5] if len(parts) >= 6 else "?"
    note = "" if isolated == "1" else "；注意 isolated=0，包外路径可能混入（见 doctor「运行期隔离」）"
    return (PASS, "运行时冒烟",
            f"python {pyver} · numpy {numpy_v} · PyYAML {yaml_v} · isolated={isolated}{note}", "")


CHECKS = (check_required_files, check_required_dirs, check_source_closure,
          check_file_integrity, check_dependencies, check_template,
          check_template_secrets, check_self_heal)


def _looks_packaged(root: str) -> bool:
    """判断 root 是不是「已打包的交付树」。

    源码仓里 `python/`、`shell/node_modules/`、`Uninstall.exe` 都被 .gitignore 掉了，
    这道闸门的判据在那里必然全红 —— 那不是缺陷，是「不适用」。
    """
    return (os.path.isfile(os.path.join(root, "python", "python.exe"))
            and os.path.isdir(os.path.join(root, "shell", "node_modules", "electron", "dist")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交付包完整性自检（发布前闸门）")
    ap.add_argument("--root", default=None, help="待验交付包根目录（默认脚本所在包）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--smoke", action="store_true",
                    help="额外真跑一次目标包内的 python.exe 做导入冒烟"
                         "（会执行目标包里的程序，只对自己打的包用）")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root) if args.root else SCRIPT_ROOT

    # 源码仓根不是交付树：按 check_all.py 的约定用 77 表示「跳过（未验证）」，
    # 不计入通过。打包完成后用 --root <产物目录> 再跑，那时才是真检。
    if args.root is None and not _looks_packaged(root):
        msg = (f"{root} 不是已打包的交付树（缺 python/python.exe 或 "
               f"shell/node_modules/electron/dist）——跳过；打包后用 --root 指向产物再跑")
        if args.json:
            print(json.dumps([{"level": "SKIP", "item": "整体", "detail": msg, "hint": ""}],
                             ensure_ascii=False, indent=2))
        else:
            print("跳过：" + msg)
        return 77

    checks = CHECKS + ((check_runtime_smoke,) if args.smoke else ())
    results = [fn(root) for fn in checks]
    n_fail = sum(1 for r in results if r[0] == FAIL)
    n_warn = sum(1 for r in results if r[0] == WARN)
    n_pass = len(results) - n_fail - n_warn

    if args.json:
        print(json.dumps(
            [{"level": r[0], "item": r[1], "detail": r[2], "hint": r[3]} for r in results],
            ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print(f"交付包完整性自检 · {root}")
        print("=" * 60)
        for lvl, item, detail, hint in results:
            print(f"  [{lvl}] {item:10s} {detail}")
            if hint:
                print(f"          ↳ 修复：{hint}")
        print("=" * 60)
        print(f"汇总：{n_pass} PASS / {n_warn} WARN / {n_fail} FAIL")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
