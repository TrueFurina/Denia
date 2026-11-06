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

    # 4) 组装后把依赖装进包内的 python/，让它拷到干净机器上也能跑
    python tools/make_dist.py --out dist --install-deps

    # 5) 只装核心依赖（启动必需，体积最小）
    python tools/make_dist.py --out dist --install-deps --tier core

    # 6) 【离线化】先把 wheel 全下到 dist/wheels，再从本地装（目标机器无需联网）
    python tools/make_dist.py --out dist --wheelhouse
    python tools/make_dist.py --out dist --install-deps --offline

组装完成后自动执行：
    · 凭据/隐私闸（check_package_secrets.py --dir）
    · 静态资源完整性（check_web_assets.py --dir）
    · 必需文件清单核对

退出码：0 = 可发布；1 = 有阻断项。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

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
    # 【2026-10-20】应用图标：`src/ui/main_window.py:1395` 按 `<根>/app.ico`
    # 找它，找不到就 `icon=None` → Windows 给的是 python.exe 的图标。
    # 之前不在清单里，于是每个交付包的任务栏/Alt-Tab 都是 Python 的图标。
    "app.ico",
]

# 发布模板 → 包内实际配置名（模板里的 Key 必须为空）
RENAME = {"config.release.yaml": "config.yaml"}

# 运行时真会调用的 tools 脚本（src/companion/proactive.py、src/llm/cloud_omni_realtime.py
# 都用 os.path.join(ROOT, "tools", "clone_danya_voice.py") 定位它）。
# 其余 290 个是开发期一次性脚本，一律不进包。
INCLUDE_TOOLS = [
    "clone_danya_voice.py",
    # 【阶段 B3】--doctor 的实现本体。少了它，交付包里的 --doctor 只剩一句
    # 「自检脚本加载失败」，而它恰恰是给用户排障用的第一入口。
    "doctor.py",
]

# 这些目录/文件不复制
IGNORE_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "tests", "test"}
IGNORE_SUFFIX = (".pyc", ".pyo", ".log", ".map", ".orig", ".rej")

# 【2026-11-06】运行时通话录音绝不进包：src/ui/web/assets/chat_audio/ 是
# call_audio_merge / webapp 在通话时写 wav 的目录，里面全是用户语音。
# 2026-11-06 重出包实测：--force 复用旧 stage 时有 3 个 wav 残留，而凭据闸
# 只打 WARN 不阻断，差点随 public 仓库的安装包一起公开发布。
RUNTIME_AUDIO_DIRS = {"chat_audio"}
RUNTIME_AUDIO_SUFFIX = (".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac")

# 【C11】桌宠 Electron 壳：不进 INCLUDE_DIRS（那里是全量拷贝的源码树），
# 走下面的专用装配——只拿壳脚本 + electron 运行时，其余 node_modules 一律不进包。
SHELL_FILES = ["main.js", "preload.js", "package.json"]
# electron dist 里的 locales/ 占 ~49MB（几十种语言），桌宠 UI 只会见到
# 中文/英文，留这三份其余全裁：包体立省 ~46MB，运行无感。
SHELL_LOCALE_KEEP = {"zh-CN.pak", "zh-TW.pak", "en-US.pak"}

# 组装后必须存在的路径（缺一个就是"打包漏件"，正是看板娘那次事故的形状）
REQUIRED = [
    "main.py",
    "config.yaml",
    "src/ui/web/index.html",
    "src/ui/web/live2d/rig/index.html",
    "assets/voices/daniya_ref.wav",
]


# ============================ 实现 ============================

def _ignore(dir_path: str, names: list[str]) -> set[str]:
    out = set()
    in_audio_dir = os.path.basename(os.path.normpath(dir_path)).lower() in RUNTIME_AUDIO_DIRS
    for n in names:
        if n in IGNORE_DIRS or n.endswith(IGNORE_SUFFIX):
            out.add(n)
        elif in_audio_dir and n.lower().endswith(RUNTIME_AUDIO_SUFFIX):
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
                # 单个文件取不到体积（被占/符号链接）→ 跳过（统计用途）
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


# ==================== 依赖分级安装（阶段 C1 · 离线化） ====================

# requirements.txt 里用 `# ===== 核心（…）=====` 这样的注释行分档。
# pip 看不见分档，只能整份装 —— 于是 GB 级的可选项会跟着核心一起被拖下水，
# 而且任何一项装不上就整批失败，看不出到底是哪个档位出了问题。
# 这里把分档从注释里提出来，让安装器能按档推进、按档定责。
_TIER_MARKERS = (("核心", "core"), ("重要", "important"), ("可选", "optional"))
_TIER_ORDER = ("core", "important", "optional")
_TIER_LABEL = {"core": "核心", "important": "重要", "optional": "可选"}
# 失败的后果：核心装不上就别发布了；重要缺了只是部分功能不可用；可选无所谓
_TIER_FATAL = {"core": True, "important": False, "optional": False}


def parse_requirements(path: str) -> dict[str, list[str]]:
    """按注释里的分档标题切 requirements。

    分档标记之前的内容（文件头说明等）一律算 core —— 宁可多装不可漏装。
    """
    tiers: dict[str, list[str]] = {t: [] for t in _TIER_ORDER}
    if not os.path.isfile(path):
        return tiers
    cur = "core"
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                for marker, tier in _TIER_MARKERS:
                    if marker in line and "=====" in line:
                        cur = tier
                        break
                continue
            spec = line.split(" #", 1)[0].strip()
            if spec:
                tiers[cur].append(spec)
    return tiers


def normalize_tier(upto: str) -> str:
    """把用户传的档位收敛成 `_TIER_ORDER` 里的真名。

    两件事必须做，否则"参数名会骗人"（质检 2026-10-18 第 7 条遗留）：
      1. `all` 是"装到最高档"的别名 → 收敛成 `optional`，
         别让调用方以为有一个叫 all 的档位；
      2. **未知档位直接抛**。旧实现里 `upto != "all"` 的判断让
         `upto="optinal"`（拼错）和 `upto="important"` 表现完全一样：
         不装可选项、也不报错。参数名骗人的代价由用户在打包现场付。
    """
    text = str(upto or "").strip().lower()
    if text == "all":
        return _TIER_ORDER[-1]
    if text in _TIER_ORDER:
        return text
    raise ValueError(
        f"未知依赖档位 {upto!r}（可选：{', '.join(_TIER_ORDER)}；"
        f"all 是 {_TIER_ORDER[-1]} 的别名）")


def tier_specs(tiers: dict[str, list[str]], upto: str) -> list[str]:
    """取到某一档为止的全部依赖（包含更低档）。"""
    stop = normalize_tier(upto)
    out: list[str] = []
    for t in _TIER_ORDER:
        out.extend(tiers.get(t, []))
        if t == stop:
            break
    return out


def _pip(python_exe: str, args: list[str]) -> tuple[int, list[str]]:
    """跑一条 pip 命令，返回 (退出码, 输出行)。"""
    try:
        p = subprocess.run([python_exe, "-m", "pip", *args], env=_clean_env(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
    except Exception as _e:  # noqa: BLE001
        return 1, [f"pip 调用失败：{_e}"]
    return p.returncode, (p.stdout or "").splitlines()


def _wheelhouse_dir(out_dir: str) -> str:
    return os.path.join(out_dir, "wheels")


def build_wheelhouse(out_dir: str, python_exe: str, upto: str) -> bool:
    """把指定档位（含以下）的 wheel 连依赖一起下载到 dist/wheels/。

    这是「离线化」的关键：构建机联网一次，目标机器可以完全断网安装。
    """
    dest = _wheelhouse_dir(out_dir)
    os.makedirs(dest, exist_ok=True)
    specs = tier_specs(parse_requirements(os.path.join(out_dir, "requirements.txt")), upto)
    if not specs:
        print("  ! requirements.txt 没解析出任何依赖")
        return False
    tmp = os.path.join(dest, "_tier.txt")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(specs) + "\n")
        # 【C9-4】必须用 `pip wheel` 而非 `pip download`：srt / proxy_tools 这类
        # 在 PyPI 上**只有 sdist** 的包，download 会原样存 .tar.gz；离线
        # --no-index 安装时 pip 要为 sdist 联网拉 build 后端 → 必失败
        # （2026-10-18 实测：「核心」档安装失败）。pip wheel 在构建机联网时
        # 就把 sdist 构建成 .whl，交付包侧零构建。
        # 注意：`pip download` 用 -d，`pip wheel` 用 **-w**——参数名不同，
        # 用错会得到 "no such option: -d"（输出进 pip> 前缀，容易被 tail 截掉漏看）。
        print(f"  → 下载/构建 {len(specs)} 项（含传递依赖）到 wheels/ …")
        rc, out = _pip(python_exe, ["wheel", "-r", tmp, "-w", dest])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            # 构建用的临时 requirements 删不掉由临时目录回收
            pass
    if rc != 0:
        for line in out[-25:]:
            print("    pip> " + line)
        return False
    # 兜底清扫：目录里残留的 sdist（上一版 download 留下的）会让
    # --find-links 优先匹配到它们，离线安装照样炸。发现即删并点名。
    leftovers = [f for f in os.listdir(dest) if f.endswith((".tar.gz", ".zip"))]
    for f in leftovers:
        try:
            os.remove(os.path.join(dest, f))
        except OSError:
            # 多余的 wheel 删不掉 → 留给下次构建覆盖
            pass
    if leftovers:
        print(f"  ! 清掉 {len(leftovers)} 个非 wheel 产物（离线安装无法构建它们）："
              f"{', '.join(leftovers[:3])}")
    n = len([f for f in os.listdir(dest) if f.endswith(".whl")])
    sz = sum(os.path.getsize(os.path.join(dest, f))
             for f in os.listdir(dest) if f.endswith(".whl"))
    print(f"  ✓ wheelhouse：{n} 个 wheel，{_human(sz)}")
    return True


def install_deps(out_dir: str, upto: str = "important", offline: bool = False,
                 verify: bool = True, *, defer_verify: bool = False) -> bool:
    """把依赖装进包内的 python/。按档推进，核心失败即停。

    【2026-11-04】``defer_verify=True``：只装依赖、**不在这里验收**。
    原因：验收（``verify_deps`` → 包内 ``main.py --doctor``）需要 isolated
    ``_pth`` 和 pywin32 DLL 都已就位，而这两步在 ``main()`` 里发生在
    ``install_deps`` **之后**。以前在这里直接验 → 首次装配必报
    ``ModuleNotFoundError: pywintypes``（假红），流程走完再跑 doctor 却全绿。
    主流程用 defer_verify=True 调用，并在隔离段之后统一验一次。
    """
    python_exe = os.path.join(out_dir, "python", "python.exe")
    if not os.path.isfile(python_exe):
        print("  ✗ 包内没有 python/python.exe（用了 --no-runtime？）—— 不知道该往哪装依赖")
        print("    要么去掉 --no-runtime，要么先把运行时放进 dist/python/")
        return False

    tiers = parse_requirements(os.path.join(out_dir, "requirements.txt"))
    if not any(tiers.values()):
        print("  ✗ 包内 requirements.txt 没解析出任何依赖")
        return False

    wh = _wheelhouse_dir(out_dir)
    have_wheels = os.path.isdir(wh) and any(
        f.endswith(".whl") for f in os.listdir(wh))
    if offline and not have_wheels:
        print(f"  ✗ --offline 但 {wh} 里没有 wheel，先跑一次 --wheelhouse")
        return False
    if have_wheels:
        print(f"  · 从本地 wheelhouse 安装（离线）：{wh}")

    # 先把档位收敛/校验：未知值在这里就炸，而不是带着一路装错东西
    wanted = normalize_tier(upto)
    fatal = False
    for tier in _TIER_ORDER:
        specs = tiers.get(tier) or []
        if not specs:
            continue
        if tier not in _TIER_ORDER[:_TIER_ORDER.index(wanted) + 1]:
            print(f"  - 跳过「{_TIER_LABEL[tier]}」档 {len(specs)} 项"
                  f"（到 {wanted} 档为止；加 --tier {_TIER_ORDER[-1]} 才会装）")
            continue
        print(f"  → 安装「{_TIER_LABEL[tier]}」档 {len(specs)} 项 …")
        args = ["install", "--no-warn-script-location", *specs]
        if have_wheels:
            args += ["--no-index", "--find-links", wh]
        rc, out = _pip(python_exe, args)
        if rc != 0:
            for line in out[-25:]:
                print("    pip> " + line)
            if _TIER_FATAL[tier]:
                print(f"  ✗ 「{_TIER_LABEL[tier]}」档安装失败 —— 这个包不能发布")
                fatal = True
                break
            print(f"  ⚠ 「{_TIER_LABEL[tier]}」档有项装不上（不影响启动）")
        else:
            print(f"  ✓ 「{_TIER_LABEL[tier]}」档完成")
        if tier == upto:
            break   # 档位递进是「到此为止」，不是「全部装完」

    if fatal:
        return False
    warn_stale_pth(out_dir)   # 【C9-2】新装 .pth 在 isolated 模式下会静默失效，必须报警
    if defer_verify:
        return True
    if verify:
        return verify_deps(out_dir, python_exe)
    return True


def _clean_env() -> dict:
    """给"换台机器"验收用的环境变量：把构建机的痕迹清掉。

    为什么必须清（2026-10-18 实测踩到）：
        包内 `python/` 是**完整 Python 拷贝**，不是带 `python._pth` 的隔离版，
        所以它会照常加载用户级 site-packages
        （`%APPDATA%\\Roaming\\Python\\PythonXY\\site-packages`）。构建机上恰好装过
        rich / httpx / requests，于是 `--doctor` 的依赖检查看到的是「包内 ∪ 构建机」，
        把一个**缺 5 个核心包**的交付件判成了 9 PASS /「可发布」。

        验证手段与被验证对象共用同一个污染源，是这类假绿唯一的来源。
        上面的 docstring 早就写了「不能用构建机的 dev 环境验」——但代码没做到，
        规矩写在注释里而没写在 `env=` 里，就等于没有。

    清的是：
        PYTHONPATH / PYTHONHOME —— 会把构建机的包直接塞进 sys.path
        PYTHONNOUSERSITE=1      —— 关掉用户级 site-packages，这才是真正的开关
        其余 PYTHON*            —— 作用不确定，一律不带进验收现场
    """
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("PYTHON") and k.upper() != "VIRTUAL_ENV"}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def verify_deps(out_dir: str, python_exe: str) -> bool:
    """用**包内的** python 跑一遍 --doctor，证明这包真能起得来。

    两个必须守住的规矩：

    1. 不能用构建机的 dev 环境验 —— dev 装了什么跟包里带什么没关系，
       验了等于没验，「在我机器上能跑」就是这么来的。
    2. 必须在**临时副本**里验，绝不能对着交付件本身跑。
       2026-10-18 实测：main.py 的导入期副作用会在包根建出
       data/call_av.log 与 logs/crash_*.log，于是"只读自检"把包弄脏，
       随后被凭据闸判为混入隐私目录。验收动作本身制造故障，
       是这类闸门最难查的一类误报。
    """
    scratch = tempfile.mkdtemp(prefix="denia_verify_")
    probe = os.path.join(scratch, "probe")
    try:
        shutil.copytree(out_dir, probe)
        # 副本里的运行时也要跟着走：验的就是这一份 python
        probe_py = os.path.join(probe, "python", "python.exe")
        py = probe_py if os.path.isfile(probe_py) else python_exe
        print("  → 用包内运行时跑 --doctor 验收（在临时副本里，不碰交付件）…")
        env = _clean_env()
        print("  · 验收环境已隔离：PYTHONNOUSERSITE=1，不带 PYTHONPATH/HOME")
        try:
            p = subprocess.run([py, "main.py", "--doctor"],
                               cwd=probe, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
        except subprocess.TimeoutExpired:
            print("  ✗ --doctor 超时（180s）")
            return False

        tail = (p.stdout or "").strip().splitlines()[-8:]
        for line in tail:
            print("    " + line)
        if p.returncode != 0:
            print(f"  ✗ 包内运行时跑 --doctor 失败（rc={p.returncode}）")
            return False
        out = p.stdout or ""
        if "[FAIL]" in out or "自检脚本加载失败" in out:
            print("  ✗ 包内运行时自检有 FAIL 项，先修再发布")
            return False
        print("  ✓ 包内运行时自检全绿")
        return True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _site_pkgs_count(out_dir: str) -> tuple[int, list[str]]:
    """统计内置运行时 site-packages 里的第三方包数（排除 pip/setuptools 系）。"""
    sp = os.path.join(out_dir, "python", "Lib", "site-packages")
    if not os.path.isdir(sp):
        return -1, []
    skip = ("pip", "pip-", "setuptools", "wheel", "_distutils", "pkg_resources")
    names = [n for n in os.listdir(sp) if not n.startswith(skip)]
    return len(names), names[:6]


# isolated 模式（python312._pth）下 `site` 不执行，site-packages/*.pth 全部失效。
# pywin32.pth 已由装配期 write_isolated_pth 手工补回三段路径，这是白名单里唯一成员。
# 【C9-2 · _pth 维护税】install_deps 之后新装进来的任何 .pth 都会**静默失效**——
# 包不报错、功能就是没反应，是最难查的一类。所以装完必须扫一遍并大声报警。
_KNOWN_PTH = {"pywin32.pth"}


def scan_stale_pth(out_dir: str) -> list[str]:
    """返回包内 site-packages 里白名单之外的 .pth 文件名（会静默失效的）。"""
    sp = os.path.join(out_dir, "python", "Lib", "site-packages")
    if not os.path.isdir(sp):
        return []
    return sorted(f for f in os.listdir(sp)
                  if f.lower().endswith(".pth") and f not in _KNOWN_PTH)


def warn_stale_pth(out_dir: str) -> None:
    """发现会静默失效的 .pth 时打印处置指引。"""
    stale = scan_stale_pth(out_dir)
    if not stale:
        return
    print("  ⚠ 发现 " + ", ".join(stale))
    print("    包内运行时是 isolated 模式（python312._pth），`site` 不执行，")
    print("    这些 .pth 里登记的路径会静默失效（功能没反应且无报错）。处置：")
    print("    1) 打开 .pth，读出里面登记的路径；")
    print("    2) 逐条追加进 python/python312._pth；")
    print("    3) 若 .pth 里是可执行行（import …），需手工等价处理。")
    print("    参考：pywin32.pth 的三段路径就是这么补回的（见 write_isolated_pth）。")


def run_gates(out_dir: str, strict_deps: bool = False,
              root: str | None = None) -> tuple[int, bool]:
    """成型后跑闸门。返回 (退出码, 依赖是否未装)。root 用于壳漂移比对（C11）。"""
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
    # 【C11】壳装配漂移：源里有 shell/main.js 而包里没有 → copy_shell 没跑/被绕过，
    # 装机将静默回退白底桌宠。与 C9-5 指纹闸同一逻辑：装配丢失必须硬失败。
    if root and os.path.isfile(os.path.join(root, "shell", "main.js")) \
            and not os.path.isfile(os.path.join(out_dir, "shell", "main.js")):
        missing.append("shell/main.js（源里有的桌宠壳没进包）")
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


# ==================== 体积报告（阶段 C3） ====================

def _dir_size(path: str) -> tuple[int, int]:
    total = 0
    count = 0
    for r, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(r, n))
                count += 1
            except OSError:
                # 同 _tree_size：单个文件取不到体积就跳过
                pass
    return total, count


def size_report(root: str, top_files: int = 12, top_pkgs: int = 12) -> None:
    """从目录 / 单文件 / 第三方包三个视角拆开体积。

    为什么需要它：交付包只有一个总大小，看不出是谁占的。等到"怎么 300MB"
    被问起来，只能靠猜。这个报告就是回答"凭什么这么大、砍哪里最值"。
    """
    if not os.path.isdir(root):
        print(f"✗ 不是目录：{root}")
        return
    rows: list[tuple[str, int, int]] = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            sz, n = _dir_size(p)
        else:
            try:
                sz, n = os.path.getsize(p), 1
            except OSError:
                # 这个文件取不到体积 → 跳过该行统计
                continue
        rows.append((name, sz, n))
    total = sum(r[1] for r in rows) or 1

    print(f"\n=== 体积报告：{root} ===")
    print(f"总计 {_human(total)}\n")
    print(f"{'条目':38s} {'体积':>10s} {'占比':>7s} {'文件数':>8s}")
    print("-" * 68)
    for name, sz, n in sorted(rows, key=lambda r: -r[1]):
        print(f"{name:38s} {_human(sz):>10s} {sz * 100.0 / total:>6.1f}% {n:>8d}")

    files: list[tuple[str, int]] = []
    for r, _dirs, names in os.walk(root):
        for n in names:
            fp = os.path.join(r, n)
            try:
                files.append((os.path.relpath(fp, root), os.path.getsize(fp)))
            except OSError:
                # 单个文件列不全也继续（体积报告是汇总参考）
                pass
    files.sort(key=lambda x: -x[1])
    print(f"\n最大的 {top_files} 个文件：")
    for rel, sz in files[:top_files]:
        print(f"  {_human(sz):>9s}  {rel}")

    sp = os.path.join(root, "python", "Lib", "site-packages")
    if os.path.isdir(sp):
        pkgs: list[tuple[str, int]] = []
        loose = 0
        for name in os.listdir(sp):
            p = os.path.join(sp, name)
            if os.path.isdir(p):
                pkgs.append((name, _dir_size(p)[0]))
            else:
                try:
                    loose += os.path.getsize(p)
                except OSError:
                    # 散文件取不到体积 → 该项不计入
                    pass
        pkgs.sort(key=lambda x: -x[1])
        sp_total = sum(p[1] for p in pkgs) + loose
        print(f"\nsite-packages 共 {_human(sp_total)}，最重的 {top_pkgs} 个：")
        for name, sz in pkgs[:top_pkgs]:
            print(f"  {_human(sz):>9s}  {name}")
        if loose:
            print(f"  {_human(loose):>9s}  （散装顶层文件）")


def runtime_tag(out_dir: str) -> str:
    """从 python/pythonXY.dll 认出内置运行时的版本 tag（如 python312）。"""
    pay = os.path.join(out_dir, "python")
    if not os.path.isdir(pay):
        return ""
    for n in sorted(os.listdir(pay)):
        m = re.match(r"^python(\d{3,})\.dll$", n)
        if m:
            return f"python{m.group(1)}"
    return ""


def isolated_pth_lines(out_dir: str) -> list[str]:
    """pythonXY._pth 的内容行。

    isolated 模式下 `site` 不执行：site-packages 不再自动进 sys.path，
    `*.pth` 全部失效 —— 所以每一段路径都要显式列出。写不存在的东西是撒谎
    （python.exe 找不到会静默跳过，查错时白绕），zip 行和 pywin32 三段
    都只在对应文件/目录真实存在时才写。
    """
    pay = os.path.join(out_dir, "python")
    sp = os.path.join(pay, "Lib", "site-packages")
    lines = ["."]
    tag = runtime_tag(out_dir)
    if tag and os.path.isfile(os.path.join(pay, f"{tag}.zip")):
        lines.append(f"{tag}.zip")
    for rel in ("DLLs", "Lib"):
        if os.path.isdir(os.path.join(pay, rel)):
            lines.append(rel)
    if os.path.isdir(sp):
        lines.append("Lib\\site-packages")
        for sub in ("win32", os.path.join("win32", "lib"), "Pythonwin"):
            if os.path.isdir(os.path.join(sp, sub)):
                lines.append("Lib\\site-packages\\" + sub)
    # 包根放最后：`..` 既能让 main.py 可导入，又不会遮蔽标准库
    lines.append("..")
    return lines


def write_isolated_pth(out_dir: str, enable: bool = True) -> tuple[bool, list[str]]:
    """写/删 pythonXY._pth，让包内运行时进入 isolated 模式。

    enable=False 必须真删旧文件：否则「这次没开隔离」和「运行时确实没隔离」
    是两回事——上一次装配留下的 _pth 会继续生效。
    """
    tag = runtime_tag(out_dir)
    if not tag:
        return False, []
    path = os.path.join(out_dir, "python", f"{tag}._pth")
    if not enable:
        if os.path.exists(path):
            os.remove(path)
        return False, []
    lines = isolated_pth_lines(out_dir)
    # CRLF：与同目录其它文本文件一致，别让 git 检出抖动
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return True, lines


def bundle_pywin32_dlls(out_dir: str) -> list[str]:
    """把 pywin32_system32 的依赖 DLL 复制到 python/ 根（解释器同目录）。

    isolated 下唯一对**任何入口**都有效的依赖 DLL 方案（2026-10-18 三条路
    实测）：_pth 里写路径无效（3.8+ 依赖 DLL 不从 sys.path 找）；main.py 里
    add_dll_directory 只管走 main.py 的入口。放解释器同目录则 python -c、
    run_webui.py 等所有入口都能找到。
    """
    src = os.path.join(out_dir, "python", "Lib", "site-packages", "pywin32_system32")
    dst = os.path.join(out_dir, "python")
    if not os.path.isdir(src) or not os.path.isdir(dst):
        return []
    moved: list[str] = []
    for n in sorted(os.listdir(src)):
        if n.lower().endswith(".dll"):
            shutil.copy2(os.path.join(src, n), os.path.join(dst, n))
            moved.append(n)
    return moved


def purge_stale_caches(out_dir: str) -> list[str]:
    """清掉输出目录里（`python/` 之外）的 `__pycache__` 与 `.pyc` / `.log`。

    为什么必须单独做一次（2026-10-18 实测）：`--force` 的语义是「覆盖同名
    文件，不删多余文件」，于是上一次在包内跑过 main.py 留下的 src/__pycache__
    会一直躺着，凭据闸判「包内混入目录」拦下发布。`python/` 下的字节码不动
    （删了只是首启变慢，不值），tests/ 更不许顺手删——漏拷了东西宁可让闸门
    抓住，也不要它悄悄消失。
    """
    killed: list[str] = []
    pyroot = os.path.join(out_dir, "python")
    for cur, dirs, files in os.walk(out_dir):
        if os.path.normcase(os.path.abspath(cur)).startswith(
                os.path.normcase(os.path.abspath(pyroot)) + os.sep):
            dirs[:] = []
            continue
        for d in list(dirs):
            if d == "__pycache__":
                p = os.path.join(cur, d)
                shutil.rmtree(p, ignore_errors=True)
                killed.append(os.path.relpath(p, out_dir))
                dirs.remove(d)
        for fn in files:
            if fn.endswith((".pyc", ".pyo", ".log")):
                p = os.path.join(cur, fn)
                try:
                    os.remove(p)
                    killed.append(os.path.relpath(p, out_dir))
                except OSError:
                    # 陈旧缓存删不掉（被占用）→ 记入 killed 的照报，下轮再清
                    pass
    return killed


def preserve_user_config(out: str, root: str) -> str | None:
    """装配覆盖前，把目标目录里**用户改过的** config.yaml 抢救出来。

    为什么必须做（2026-10-18 盘点出的产品化缺口）：
        RENAME 把 `config.release.yaml` 改名成 `config.yaml` 写入交付包。用户
        拿到包后往里面填 API Key；下个版本发布，用户**解压新 zip 到同一目录**
        ——config.yaml 被出厂默认值静默覆盖，Key 直接丢失。zip 解压这一步
        谁也拦不住，所以这里做两件事：
        1. 装配机侧（--force 重装配到已有目录）：旧 config 与 release 模板
           不同 = 有人改过，先备份成 config.user.bak.yaml 再覆盖；
        2. 用户机侧靠 doctor（check_config）发现 .bak 与 config 并存时提示比对。
    返回备份路径；没东西可救返回 None。
    """
    dst = os.path.join(out, "config.yaml")
    src = os.path.join(root, "config.release.yaml")
    if not os.path.isfile(dst) or not os.path.isfile(src):
        return None
    with open(dst, "rb") as f:
        old = f.read()
    with open(src, "rb") as f:
        new = f.read()
    if old == new:
        return None  # 都是出厂默认，覆盖无损失
    bak = os.path.join(out, "config.user.bak.yaml")
    shutil.copy2(dst, bak)
    return bak


def purge_runtime_audio(out_dir: str) -> list[str]:
    """删掉交付副本里的运行时通话录音（只碰 out_dir，绝不碰工作目录原件）。

    --force 的语义是「覆盖同名，不删多余」，所以单靠 _ignore 挡不住上一次
    装配留下的 wav。必须做一次减法，且必须与凭据闸的阻断项成对出现：
    这边漏删了，那边就会把出包判红，不让脏包流到发布环节。
    """
    killed: list[str] = []
    for cur, dirs, files in os.walk(out_dir):
        if os.path.basename(os.path.normpath(cur)).lower() not in RUNTIME_AUDIO_DIRS:
            continue
        for fn in files:
            if not fn.lower().endswith(RUNTIME_AUDIO_SUFFIX):
                continue
            p = os.path.join(cur, fn)
            try:
                os.remove(p)
                killed.append(os.path.relpath(p, out_dir))
            except OSError:
                # 删不掉就留着：凭据闸会把这个包判红，不静默放行
                pass
    return killed


def copy_items(items: list[tuple[str, str, bool]], out: str) -> None:
    for src, rel, is_dir in items:
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        if is_dir:
            shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def src_fingerprint(root: str) -> str:
    """【C9-5】装配源码指纹：root 下 src/**/*.py + main.py 的聚合 sha256。

    输入是（相对路径 + 文件内容），排序后逐条喂 sha256。只看内容不看
    mtime——git checkout / 重排工作区不会造成假阳性。main.py 不存在
    （如测试假根）就只算 src/。
    """
    h = hashlib.sha256()
    rels: list[str] = []
    src_dir = os.path.join(root, "src")
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames.sort()
        for fn in filenames:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                rels.append(os.path.relpath(full, root).replace("\\", "/"))
    if os.path.isfile(os.path.join(root, "main.py")):
        rels.append("main.py")
    # 【C11】壳脚本进指纹：shell/main.js 改了而包没重出，和源码过期是同一种事故
    for rel in ("shell/main.js", "shell/preload.js"):
        if os.path.isfile(os.path.join(root, rel.replace("/", os.sep))):
            rels.append(rel)
    for rel in sorted(rels):
        with open(os.path.join(root, rel), "rb") as fh:
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(fh.read())
            h.update(b"\x00")
    return h.hexdigest()


def copy_shell(root: str, out: str) -> bool:
    """【C11】把桌宠 Electron 壳装进包。

    装机里没有 electron.exe 时 pet_backend 会静默回退 pywebview 桌宠——
    白底、置顶弱（2026-10-18 用户真机实测的两个差评都来自这条回退路径）。
    本函数保证 `shell/node_modules/electron/dist/electron.exe` 跟包走。

    返回 True=壳就位 / False=源里就没有壳（打警告但不算装配失败，
    纯后端/无壳场景仍应能出包）。
    """
    shell_src = os.path.join(root, "shell")
    if not os.path.isdir(shell_src):
        print("  ⚠ shell/ 不存在，跳过桌宠壳（运行时将回退 pywebview 白底桌宠）")
        return False
    # 1) 壳脚本三件（main.js / preload.js / package.json）
    have_main = False
    for f in SHELL_FILES:
        src = os.path.join(shell_src, f)
        if os.path.isfile(src):
            dst = os.path.join(out, "shell", f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            have_main = have_main or f == "main.js"
    # 2) electron 运行时（只裁 locales，其余全量——缺一个 dll 壳都可能起不来）
    dist_src = os.path.join(shell_src, "node_modules", "electron", "dist")
    if not os.path.isdir(dist_src):
        print("  ⚠ shell/node_modules/electron/dist 不存在（开发机没装 electron？）"
              "→ 包内无壳，运行时回退 pywebview")
        return False
    dist_dst = os.path.join(out, "shell", "node_modules", "electron", "dist")
    if os.path.isdir(dist_dst):
        shutil.rmtree(dist_dst)
    os.makedirs(dist_dst)
    n_files = 0
    for dirpath, dirnames, filenames in os.walk(dist_src):
        dirnames.sort()
        rel = os.path.relpath(dirpath, dist_src)
        dst_dir = dist_dst if rel == "." else os.path.join(dist_dst, rel)
        os.makedirs(dst_dir, exist_ok=True)
        if rel == "locales":
            filenames = [f for f in filenames if f in SHELL_LOCALE_KEEP]
        for fn in filenames:
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(dst_dir, fn))
            n_files += 1
    exe = os.path.join(dist_dst, "electron.exe")
    if not os.path.isfile(exe):
        print("  ✗ electron dist 拷完却没有 electron.exe——源 dist 不完整，视为装配失败")
        return False
    sz, _ = _tree_size(dist_dst)
    print(f"  ✓ 桌宠壳就位：shell/（main.js{'✓' if have_main else '✗'} + electron 运行时 "
          f"{n_files} 文件 / {_human(sz)}，locales 已裁至 {len(SHELL_LOCALE_KEEP)} 语言）")
    return True


def write_src_fingerprint(root: str, out_dir: str) -> str:
    """装配收尾时把源码指纹存档到 out_dir/.src_fingerprint（C9-5）。

    build_installer 出包前重算比对——版本没 bump 但装配后源码继续改，
    出来的包就是「安装包报这个版本、里面是旧程序」，指纹闸挡死它。
    """
    fp = src_fingerprint(root)
    with open(os.path.join(out_dir, ".src_fingerprint"), "w", encoding="utf-8") as fh:
        fh.write(fp + "\n")
    return fp


def read_app_version(root: str) -> str:
    """从 src/version.py 读 APP_VERSION（单一真值）。

    正则读文本而不是 import：不执行模块代码、不依赖包内环境，
    构建机上有没有装 src 的依赖都能拿到版本号。
    """
    ver = _read_version_file(os.path.join(root, "src", "version.py"))
    if ver is None:
        raise SystemExit("✗ src/version.py 里找不到 APP_VERSION 字面量")
    return ver


_NSIS_CANDIDATES = (
    os.path.join(".build_tools", "nsis-3.10", "makensis.exe"),
    os.path.join("C:", os.sep, "Program Files (x86)", "NSIS", "makensis.exe"),
)


def find_makensis(root: str) -> str | None:
    """按候选顺序找 makensis；找不到返回 None（调用方显式报错）。"""
    for rel in _NSIS_CANDIDATES:
        p = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


def _read_version_file(path: str) -> str | None:
    """读单个 version.py 里的 APP_VERSION 字面量；读不到/没有返回 None。"""
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        # 版本文件读不了 → 返回 None（调用方用兜底版本号）
        return None


def build_installer(out_dir: str, root: str, nsis_exe: str | None = None,
                    version: str | None = None) -> bool:
    """把 out_dir 打成 NSIS 安装包（C2-A）。

    版本号与装配目录都通过 /D 注入 tools/installer.nsi（脚本里有
    !ifndef 守卫，兜底 VERSION 是 0.0.0-dev 哨兵——忘注入会很难看，
    这是故意的）。产物落在 root/dist/ 下，名字由脚本 OutFile 决定。

    漂移守卫：out_dir/src/version.py 必须存在且与注入版本一致——
    装配目录是旧代码、version.py 已 bump 时，装出来就是"安装包报新
    版本、里面是旧程序"，这类双口径必须在这里挡死。
    """
    version = version or read_app_version(root)
    stage_ver = _read_version_file(os.path.join(out_dir, "src", "version.py"))
    if stage_ver is None:
        print(f"  ✗ 装配目录里没有 src/version.py，不像装配产物：{out_dir}")
        return False
    if stage_ver != version:
        print(f"  ✗ 版本漂移：装配目录是 {stage_ver}，注入的是 {version}"
              f"——先重新装配（--out {os.path.basename(out_dir)} …）再出包")
        return False
    # 【C9-5】源码指纹闸：版本没 bump 但装配后 src/ 继续改，版本守卫看不见——
    # 指纹看得见。装配目录必须带 .src_fingerprint 存档，且与当前 root 的
    # src/ 重算结果一致，否则出来的包就是「报这个版本、里面是旧程序」。
    fp_path = os.path.join(out_dir, ".src_fingerprint")
    if not os.path.isfile(fp_path):
        print(f"  ✗ 装配目录没有 .src_fingerprint 存档（旧装配产物）"
              f"——重新装配一次（make_dist --out …）再出包")
        return False
    with open(fp_path, encoding="utf-8") as fh:
        fp_saved = fh.read().strip()
    fp_now = src_fingerprint(root)
    if fp_saved != fp_now:
        print(f"  ✗ 源码漂移：装配后 src/ 已被修改（存档 {fp_saved[:12]}… "
              f"≠ 当前 {fp_now[:12]}…）——重新装配再出包")
        return False
    nsis = nsis_exe or find_makensis(root)
    if not nsis or not os.path.isfile(nsis):
        print("  ✗ 找不到 makensis：装到 .build_tools/nsis-3.10/ 或 Program Files (x86)/NSIS")
        return False
    # 输出目录必须自己建：新克隆的仓库没有 dist/，旧代码靠"上一次出包留下的
    # 目录"蒙混过关，删掉 dist/ 后第一次出包直接失败（2026-10-19 清场时暴露）。
    os.makedirs(os.path.join(root, "dist"), exist_ok=True)
    script = os.path.join(root, "tools", "installer.nsi")
    argv = [nsis,
            f"/DVERSION={version}",
            f"/DSTAGE={os.path.abspath(out_dir)}",
            script]
    print(f"  → makensis {version} ← {os.path.basename(out_dir)} …")
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  ✗ makensis 启动失败：{e}")
        return False
    if proc.returncode != 0:
        for line in (proc.stdout or "").splitlines()[-20:]:
            print("    nsis> " + line)
        print("  ✗ makensis 失败")
        return False
    exe = os.path.join(root, "dist",
                       f"达妮娅AI桌面伴侣_Setup_v{version}.exe")
    if not os.path.isfile(exe):
        print("  ✗ 安装包没有出现在预期位置：" + exe)
        return False
    print(f"  ✓ 安装包：{exe}（{os.path.getsize(exe) / 1048576:.1f} MB）")
    return True


# ===== 【C10】代码签名（可选闸：不配凭证=不签名；配了凭证=必须签成） =====
# 凭证全部走环境变量，不落仓库、不进 config——真证书与自签只差变量值：
#   DANIA_SIGN_SUBJECT     按证书主题选证书（证书库里"颁发给"的名字）
#   DANIA_SIGN_THUMBPRINT  或按 40 位指纹选证书
#   DANIA_SIGN_PFX         或用 PFX 文件（配 DANIA_SIGN_PFX_PASS 口令）
#   DANIA_SIGN_TOOL        signtool.exe 路径（不给则自动找）
# 三选一。自签证书链在用户机上不受信（SmartScreen 仍会提示）——适合
# 联调签名管线；对外分发必须换 CA 签发的真证书。


def find_signtool() -> str | None:
    """找 signtool.exe：先 PATH，再 Windows Kits 各版本取最高。"""
    p = shutil.which("signtool")
    if p:
        return p
    for base in (r"C:\Program Files (x86)\Windows Kits\10\bin",
                 r"C:\Program Files\Windows Kits\10\bin"):
        hits: list[str] = []
        if os.path.isdir(base):
            for ver in os.listdir(base):
                cand = os.path.join(base, ver, "x64", "signtool.exe")
                if os.path.isfile(cand):
                    hits.append(cand)
        if hits:
            return sorted(hits)[-1]
    return None


def resolve_sign_config(env: dict | None = None) -> dict | None:
    """从环境变量解析签名凭证。None = 未配置（签名默认关闭）。

    同时配多个选择器是口径错误——"到底用哪张证书"说不清，显式拒绝；
    凭证配了但环境里没有 signtool 也在这里挡住，不等到签名那一步才炸。
    """
    env = env if env is not None else os.environ
    subject = (env.get("DANIA_SIGN_SUBJECT") or "").strip()
    thumb = (env.get("DANIA_SIGN_THUMBPRINT") or "").strip()
    pfx = (env.get("DANIA_SIGN_PFX") or "").strip()
    picked = [n for n, v in (("subject", subject), ("thumbprint", thumb),
                             ("pfx", pfx)) if v]
    if not picked:
        return None
    if len(picked) > 1:
        raise ValueError(f"签名凭证只能三选一，同时给了：{', '.join(picked)}")
    cfg: dict = {"tool": (env.get("DANIA_SIGN_TOOL") or "").strip()
                 or find_signtool() or ""}
    if not cfg["tool"] or not os.path.isfile(cfg["tool"]):
        raise ValueError(f"配了 {picked[0]} 但找不到可用的 signtool.exe"
                         f"（装 Windows SDK 或设 DANIA_SIGN_TOOL）")
    if pfx:
        if not os.path.isfile(pfx):
            raise ValueError(f"DANIA_SIGN_PFX 指向的文件不存在：{pfx}")
        cfg["pfx"] = pfx
        cfg["pass"] = env.get("DANIA_SIGN_PFX_PASS") or ""
    elif subject:
        cfg["subject"] = subject
    elif not re.fullmatch(r"[0-9a-fA-F]{40}", thumb):
        raise ValueError("DANIA_SIGN_THUMBPRINT 应为 40 位证书指纹")
    else:
        cfg["thumbprint"] = thumb
    return cfg


def sign_file(path: str, cfg: dict, runner=None) -> bool:
    """对单个 exe 做 Authenticode 签名并核验；没签上/验不过一律 False。

    自签链不受信时 verify 报 root not trusted——自签的预期现象，
    降级为警告不算失败；其余 verify 失败（文件被改、签名缺失）算失败。
    """
    run = runner or subprocess.run
    tool = cfg["tool"]
    argv = [tool, "sign", "/fd", "SHA256"]
    if cfg.get("pfx"):
        argv += ["/f", cfg["pfx"]]
        if cfg.get("pass"):
            argv += ["/p", cfg["pass"]]
    elif cfg.get("subject"):
        argv += ["/n", cfg["subject"]]
    else:
        argv += ["/sha1", cfg["thumbprint"]]
    argv.append(path)
    try:
        proc = run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  ✗ signtool 启动失败：{e}")
        return False
    if proc.returncode != 0:
        for line in (proc.stdout or "").splitlines()[-10:]:
            print("    sign> " + line)
        print("  ✗ 签名失败：" + os.path.basename(path))
        return False
    try:
        v = run([tool, "verify", "/pa", path], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace")
    except OSError as e:
        print(f"  ✗ signtool(verify) 启动失败：{e}")
        return False
    out = v.stdout or ""
    if v.returncode != 0:
        if "not trusted" in out.lower():
            print(f"  ⚠ 已签名（自签链不受信为预期；对外分发须换 CA 证书）："
                  f"{os.path.basename(path)}")
            return True
        for line in out.splitlines()[-10:]:
            print("    verify> " + line)
        print("  ✗ 签名核验失败：" + os.path.basename(path))
        return False
    print(f"  ✓ 已签名并通过核验：{os.path.basename(path)}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="按白名单组装交付目录")
    ap.add_argument("--out", help="输出目录；不给则只打印清单（dry-run）")
    ap.add_argument("--root", default=".", help="项目根目录")
    ap.add_argument("--no-runtime", action="store_true", help="不打包 python/ 内置运行时")
    ap.add_argument("--no-isolate", action="store_true",
                    help="不写 pythonXY._pth（不启用运行期隔离）；默认开启隔离")
    ap.add_argument("--force", action="store_true", help="输出目录非空时也继续（会覆盖同名文件，不删除多余文件）")
    ap.add_argument("--strict-deps", action="store_true",
                    help="内置运行时没装第三方包时视为失败（CI/正式发布建议开）")
    ap.add_argument("--install-deps", action="store_true",
                    help="组装后把依赖装进包内的 python/（让它拷到干净机器上也能跑）")
    ap.add_argument("--tier", choices=("core", "important", "optional", "all"),
                    default="important",
                    help="--install-deps 装到哪一档（含更低档）：core=启动必需；"
                         "important=+录音/托盘/音量/游戏（默认）；"
                         "optional=+装了更好的可选项；all 是 optional 的别名")
    ap.add_argument("--wheelhouse", action="store_true",
                    help="把依赖连传递依赖下载成 wheel 放到 <out>/wheels/（供离线安装）")
    ap.add_argument("--offline", action="store_true",
                    help="--install-deps 时强制只用本地 wheelhouse，不联网")
    ap.add_argument("--report", metavar="DIR", nargs="?", const="__OUT__",
                    help="打印某个目录的体积报告（配合 --out 时打到刚组装完的包上）")
    ap.add_argument("--installer", action="store_true",
                    help="装配通过后追加 NSIS 安装包（C2-A；版本号读 src/version.py）")
    ap.add_argument("--sign", action="store_true",
                    help="对安装包做 Authenticode 签名（C10；需 --installer，"
                         "凭证走 DANIA_SIGN_* 环境变量，三选一）")
    args = ap.parse_args()

    # 单独 --report 某个目录：只报告，不组装
    if args.report and args.report != "__OUT__":
        size_report(os.path.abspath(args.report))
        return 0

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
    _bak = preserve_user_config(out, root)
    if _bak:
        print(f"  ⚠ 目标已有**改过的** config.yaml，已备份：{os.path.basename(_bak)}")
        print("    （下个版本的用户解压覆盖不会经过这里——见 README 的升级说明）")
    copy_items(items, out)
    write_src_fingerprint(root, out)  # 【C9-5】源码指纹存档，出包闸要用
    # 【C11】桌宠 Electron 壳：透明背景 + 置顶守卫的真身。少了它装机回退
    # pywebview 白底桌宠（2026-10-18 用户实测差评的直接根因）。
    print("\n=== 桌宠壳（Electron）===")
    shell_ok = copy_shell(root, out)
    _stale = purge_stale_caches(out)
    if _stale:
        # 说出来而不是默默清掉：这提示"上一次装配后有人在包内跑过东西"
        print(f"  · 清掉 {len(_stale)} 项上次装配的残留（{', '.join(_stale[:3])}"
              f"{' …' if len(_stale) > 3 else ''}）")
    _audio = purge_runtime_audio(out)
    if _audio:
        print(f"  · 清掉 {len(_audio)} 项运行时通话录音（不随包发布）："
              f"{', '.join(os.path.basename(x) for x in _audio[:3])}"
              f"{' …' if len(_audio) > 3 else ''}")
    # 这里必须用真实占用而不是 _tree_size：后者为了清单好看会跳过 .pyc/test，
    # 但交付包是要按真实体积上线的。两个数字不一致会让人以为报告算错了。
    sz, n = _dir_size(out)
    print(f"完成：{n} 个文件，{_human(sz)}（磁盘真实占用）")

    deps_ok = True
    if args.wheelhouse:
        print("\n=== 依赖离线化：构建 wheelhouse ===")
        py = os.path.join(out, "python", "python.exe")
        if not os.path.isfile(py):
            print("  ✗ 包内没有 python/python.exe，无法下载 wheel")
            deps_ok = False
        else:
            deps_ok = build_wheelhouse(out, py, args.tier)

    if args.install_deps:
        print("\n=== 依赖安装（装进包内 python/）===")
        # 验收延后到隔离段之后：此处 _pth 还没写、pywin32 DLL 还没铺，
        # 在这里跑 doctor 会因 pywintypes 找不到而假红（见 install_deps docstring）。
        deps_ok = install_deps(out, upto=args.tier, offline=args.offline,
                               defer_verify=True) and deps_ok

    # 隔离必须写在依赖装完之后：pywin32 的三个子目录要先存在，才能决定 _pth
    # 里要不要写它们。也必须写在闸门之前，否则闸门查到的是一份还没隔离的包。
    print("\n=== 运行期依赖隔离（pythonXY._pth）===")
    if args.no_runtime:
        print("  - 未打包 python/，跳过")
    else:
        enabled, iso_lines = write_isolated_pth(out, enable=not args.no_isolate)
        if enabled:
            print(f"  · python/{runtime_tag(out)}._pth 已写入（{len(iso_lines)} 行，isolated 模式）")
            print("    sys.path 由该文件固定，用户级 site-packages 与交付包彻底无关")
        else:
            print("  · --no-isolate：不启用运行期隔离（旧 _pth 已删，若存在）")
        dlls = bundle_pywin32_dlls(out)
        if dlls:
            print(f"  · pywin32 DLL 铺到 python/ 根（入口无关的依赖 DLL 路径）：{', '.join(dlls)}")

    # 【2026-11-04】隔离就绪后才验收。以前在 install_deps() 内部就验，
    # 那时 _pth 未写 / DLL 未铺 → `pywintypes` 假红，且错误结论一路写进
    # "依赖环节失败"。现在只验一次，验的是**最终交付形态**。
    if args.install_deps and deps_ok:
        print("\n=== 包内 doctor 验收（隔离就绪后）===")
        probe_py = os.path.join(out, "python", "python.exe")
        if not verify_deps(out, probe_py):
            deps_ok = False

    rc, deps_missing = run_gates(out, strict_deps=args.strict_deps, root=root)
    if not deps_ok:
        rc = 1
    print()
    if not deps_ok:
        print(f"✗ 依赖环节失败，先修正再发布：{out}")
    elif rc != 0:
        print(f"✗ 未通过闸门，先修正再发布：{out}")
    elif deps_missing:
        print(f"⚠ 结构与凭据都合格，但依赖还没装 —— 装完才能双击启动：{out}")
    else:
        print(f"✓ 可发布：{out}")

    # C2-A：装配合格后才出安装包（版本号单一真值 = src/version.py）
    # C10：签名只在安装包真出出来之后执行；--sign 没配凭证是口径错误，硬失败
    if args.sign and not args.installer:
        print("✗ --sign 需要 --installer（签名对象是安装包，没有包可签即口径错误）")
        rc = 1
    setup_exe: str | None = None
    if args.installer:
        if rc != 0:
            print("✗ 装配未通过，跳过安装包")
        else:
            ver_main = read_app_version(args.root)
            if build_installer(out, args.root, version=ver_main):
                setup_exe = os.path.join(root, "dist",
                                         f"达妮娅AI桌面伴侣_Setup_v{ver_main}.exe")
            else:
                rc = 1
    if args.sign and setup_exe is not None and rc == 0:
        try:
            sign_cfg = resolve_sign_config()
        except ValueError as e:
            print(f"✗ 签名凭证：{e}")
            rc = 1
        else:
            if sign_cfg is None:
                print("✗ --sign 已给但没有签名凭证：设 DANIA_SIGN_SUBJECT / "
                      "DANIA_SIGN_THUMBPRINT / DANIA_SIGN_PFX 之一")
                rc = 1
            elif not os.path.isfile(setup_exe):
                print(f"✗ 待签安装包不存在：{setup_exe}")
                rc = 1
            elif not sign_file(setup_exe, sign_cfg):
                rc = 1

    # --report 不给目录时，报的就是刚组装出来的这个包
    if args.report:
        size_report(out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
