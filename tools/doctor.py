# -*- coding: utf-8 -*-
"""`--doctor` 一键自检（阶段 B3）。

为什么要它：以前用户说"用不了"，只能靠聊天记录一点点问——Python 是哪个、
依赖装没装、Electron 在不在、端口被谁占了、配置缺字段没有。
这些问题**每次都一样**，所以把它做成一条命令，输出可以直接贴给用户。

设计原则：
  1. **只做只读检查**，不修改任何配置、不杀进程、不下载东西。
  2. 每项都给**可行动的修复提示**（缺什么就说装什么，写清命令）。
  3. 单项失败不影响其它项跑完——最后统一汇总。
  4. 退出码：有 FAIL → 1；只有 WARN/PASS → 0（WARN 不该让门禁红）。

用法：
  .venv/Scripts/python.exe tools/doctor.py
  .venv/Scripts/python.exe tools/doctor.py --config config.release.yaml
  .venv/Scripts/python.exe tools/doctor.py --json      # 给脚本/日志用
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 控制台兜底：GBK 控制台编码不了 ↳ ✗ ⚠ 这类符号，doctor 会当场
# UnicodeEncodeError 炸掉（2026-10-18 包内自检实锤）。不动编码——中文
# 在 GBK 下本来就正常——只把编不了的符号换成 ?，诊断必须跑得完。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

_ORDER = {FAIL: 0, WARN: 1, PASS: 2}


def _norm(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "").replace(".", "")


def _import_name(dist: str, dist2import: dict) -> str:
    """把发行包名换成 import 名；查不到就用小写原名兜底。"""
    return dist2import.get(_norm(dist), dist.lower())


def _dist2import_map() -> dict[str, str]:
    try:
        from importlib.metadata import packages_distributions
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for imp, dists in packages_distributions().items():
        for d in dists:
            key = _norm(d)
            # 同一个发行包可能提供多个 import 名，取最短更像"主入口"的那个
            if key not in out or len(imp) < len(out[key]):
                out[key] = imp
    return out


def _parse_requirements(path: str) -> dict[str, list[str]]:
    """按注释分节解析 requirements（核心 / 重要 / 可选），返回 {节: [发行包名]}。"""
    sections: dict[str, list[str]] = {}
    current = "未分类"
    if not os.path.exists(path):
        return sections
    with open(path, encoding="utf-8") as fh:
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


# ---------- 各项检查 ----------

def check_runtime() -> tuple[str, str, str, str]:
    exe = sys.executable
    in_builtin = os.path.abspath(exe).startswith(
        os.path.abspath(os.path.join(ROOT, "python")))
    ver = ".".join(str(x) for x in sys.version_info[:3])
    ok = sys.version_info >= (3, 10)
    lvl = PASS if ok else FAIL
    detail = f"Python {ver} · {exe}"
    hint = "" if ok else "需要 Python 3.10+，重装或改用内置运行时 python/python.exe"
    if in_builtin:
        detail += " · 内置运行时"
    elif ".venv" in exe:
        detail += " · 项目虚拟环境"
    else:
        detail += " · ⚠ 非项目解释器（依赖可能不在它上面）"
        if lvl == PASS:
            lvl = WARN
        hint = hint or "建议用 .venv/Scripts/python.exe 或内置 python/python.exe 启动"
    return (lvl, "运行时", detail, hint)


def _running_from_bundle() -> bool:
    """当前解释器是否就是交付包内的 `python/`。"""
    try:
        return os.path.normcase(os.path.realpath(sys.executable)) == os.path.normcase(
            os.path.realpath(os.path.join(ROOT, "python", "python.exe")))
    except Exception:  # noqa: BLE001
        return False


def _borrowed_deps(files: dict[str, str]) -> list[str]:
    """包内运行时却从包外导入的依赖，返回 "包名→所在目录"。

    为什么只检查「能不能 import」不够（2026-10-18 实测）：
        包内 `python/` 是完整 Python 拷贝，`site` 会照常加载用户级
        site-packages。构建机上恰好装着 rich / httpx / requests / websockets，
        于是一个**缺 5 个核心包**的交付件，在这里拿到「9 PASS / 可发布」。
        import 成功不等于包里有——必须看 `__file__` 落在谁的目录下。
    """
    root = os.path.normcase(os.path.realpath(ROOT)).rstrip("\\/") + os.sep
    out: list[str] = []
    for name, path in files.items():
        if not path:
            continue
        if not os.path.normcase(os.path.realpath(path)).startswith(root):
            out.append(f"{name}→{os.path.dirname(path)}")
    return out


def _deps_missing_hint(from_bundle: bool) -> str:
    """缺核心依赖时该说什么。

    交付包里没有 `.venv`，让最终用户去跑 `.venv/Scripts/python.exe -m pip`
    等于指一条不存在的路——2026-10-18 在干净路径上实测时就是这么打出来的。
    """
    if from_bundle:
        return "交付包不完整：请重新解压完整安装包，或联系提供者重新打包"
    return "安装：.venv/Scripts/python.exe -m pip install -r requirements.txt"


def _outside_root_paths() -> list[str]:
    """`sys.path` 里落在交付目录之外的条目（空串/相对路径不算，那是 cwd 语义）。

    注意要把「包根本身」算作包内：`_pth` 里有一行 `..`，它让 `sys.path` 出现
    `D:\\Denia\\dist_stage` 这样**正好等于 ROOT** 的条目。只写
    `startswith(root + os.sep)` 会把它判成包外——第一次在真交付包上跑
    `--doctor` 就是这么报了一个假 FAIL 的。
    """
    root = os.path.normcase(os.path.realpath(ROOT)).rstrip("\\/")
    out: list[str] = []
    for p in sys.path:
        if not p or not os.path.isabs(p):
            continue
        q = os.path.normcase(os.path.realpath(p)).rstrip("\\/")
        if q != root and not q.startswith(root + os.sep):
            out.append(p)
    return out


def check_runtime_isolation() -> tuple[str, str, str, str]:
    """包内运行时是否真的与外部 site-packages 断开了。

    为什么要单列一项（2026-10-18 实测）：
        `check_deps` 已经会看 `__file__` 在不在包内，但那只在**依赖恰好被借到**
        时才报警。真正决定「换台电脑还能不能跑」的是运行模式：交付包的
        `python/` 是完整 Python 拷贝，默认会加载 `%APPDATA%\\Roaming\\Python\\...`。
        只要还是 standard 模式，今天没借到不代表明天不会借——用户自己
        `pip install` 过的东西随时会混进来。

        所以正式发布必须带 `pythonXY._pth`（isolated 模式，`sys.path` 由文件
        固定）。这一项就是那条要求的看门人：包内运行时 + 非 isolated = FAIL。
    """
    from_bundle = _running_from_bundle()
    isolated = bool(getattr(sys.flags, "isolated", 0))
    outside = _outside_root_paths()

    if not from_bundle:
        # 开发环境（.venv / 系统 Python）本来就该读自己的 site-packages，
        # 在这里报红只会训练人忽略红色。
        return (PASS, "运行期隔离", "开发环境，不适用（交付包才要求 isolated）", "")

    if not isolated:
        return (FAIL, "运行期隔离",
                f"包内运行时跑在 standard 模式，会加载包外路径：{', '.join(outside[:2]) or '无'}",
                "重新打包：tools/make_dist.py --out <目录> --force"
                "（生成 pythonXY._pth 即进入 isolated 模式）")
    if outside:
        return (FAIL, "运行期隔离",
                "已是 isolated，但 sys.path 仍有包外路径：" + ", ".join(outside[:3]),
                "检查 pythonXY._pth 是否被手工改过；重新打包可还原")
    return (PASS, "运行期隔离",
            f"isolated 模式，sys.path 全部落在包内（{len(sys.path)} 条）", "")


def check_deps() -> tuple[str, str, str, str]:
    sections = _parse_requirements(os.path.join(ROOT, "requirements.txt"))
    core = sections.get("核心") or []
    if not core:
        return (WARN, "核心依赖", "requirements.txt 未解析出「核心」段",
                "检查 requirements.txt 的分节注释格式")
    dist2import = _dist2import_map()
    missing = []
    files: dict[str, str] = {}
    for dist in core:
        imp = _import_name(dist, dist2import)
        try:
            mod = importlib.import_module(imp)
            files[dist] = getattr(mod, "__file__", "") or ""
        except Exception:  # noqa: BLE001
            missing.append(f"{dist}(import {imp})")
    if missing:
        return (FAIL, "核心依赖", "缺少：" + ", ".join(missing),
                _deps_missing_hint(_running_from_bundle()))
    if _running_from_bundle():
        borrowed = _borrowed_deps(files)
        if borrowed:
            return (FAIL, "核心依赖",
                    "有依赖来自包外（换台机器必缺）：" + ", ".join(borrowed),
                    "这是打包问题：请让打包方重新执行 make_dist.py --force --install-deps")
        return (PASS, "核心依赖", f"{len(core)} 个核心依赖全部来自包内", "")
    return (PASS, "核心依赖", f"{len(core)} 个核心依赖全部可导入", "")


def _candidate_electron() -> str | None:
    env = os.environ.get("DANIYA_ELECTRON", "").strip()
    if env and os.path.isfile(env):
        return env
    p = os.path.join(ROOT, "shell", "node_modules", "electron", "dist", "electron.exe")
    return p if os.path.isfile(p) else None


def _shell_mode(cfg: object) -> str:
    """读出桌宠后端的取值，收敛成 auto / webview / shell 三态。

    这条决定 doctor 对壳该查多深：
      · shell → 用户明确要壳，起不来必须 FAIL（产品承诺没兑现）
      · auto  → 默认值。会真起一次壳：起得来 PASS，起不来只 WARN
                并说明"启动时会自动回退 webview（白底）"——不是错误，是已知取舍
      · webview → 明确不走壳，不起进程、不报 FAIL
    """
    try:
        from src.ui.pet_backend import normalize

        ns = getattr(cfg, "ns", None)
        raw = getattr(getattr(ns, "ui", None), "pet_backend", None)
        mode = normalize(raw, source="doctor")
        return mode if mode in ("auto", "webview", "shell") else "auto"
    except Exception:  # noqa: BLE001
        # 注意：这里必须写字面量。曾经写成 return AUTO，而 AUTO 只在 try 里
        # 局部导入——配置结构异常时兜底路径自己抛 NameError，doctor 整个崩掉。
        # 2026-10-18 由「闸门覆盖 vs 闸门名字」专项体检抓出（之前的 WEBVIEW
        # NameError 只修了 check_shell，漏了这条 except 分支）。
        return "auto"


def probe_shell(timeout: float = 30.0) -> tuple[bool, str]:
    """真起一次壳，验「本机能不能把窗口建出来」——并且**和生产行为一致**。

    为什么不能只看文件在不在（2026-10-18 深度质检的 P0）：
        `electron.exe` / `main.js` / `preload.js` 三件套齐活，本项曾报 PASS；
        而实际启动时 Chromium 的 GPU 进程连续崩（`0xC0000005`）→
        `FATAL: GPU process isn't usable. Goodbye.` → 退出码 `0x80000003`，
        窗口根本出不来。「文件在」被当成了「能用」，是自检工具最典型的假绿。

    判据是 stdout 里出现 `CONTROL`：壳在 `did-finish-load` 之后才打印它，
    它出现意味着窗口已建、页面已加载。用 `--hidden 1` 避免闪一下窗口，
    `--quit 6` 让它自己退，不留孤儿进程。

    **必须带 GPU 回退开关重试**（2026-10-18 第二轮踩的坑）：
    本机裸启动必崩，但带 `--in-process-gpu --disable-gpu-compositing`
    之后壳能起、Live2D 能渲染、逐像素透明也成立（`tools/_qc_shell_transparency.py`）。
    生产代码 `pet_shell._launch()` 已经会这样自动退让，doctor 若只裸启动一次，
    就会报"起不来"，而用户实际启动时是好的——又一次假红。
    所以这里复刻生产的退让逻辑，且**成功时要点明走了回退**。
    """
    exe = _candidate_electron()
    main_js = os.path.join(ROOT, "shell", "main.js")
    if not exe or not os.path.isfile(main_js):
        return False, "缺 electron.exe 或 shell/main.js"

    # ELECTRON_RUN_AS_NODE 会让 electron.exe 退化成 node（app 未定义，窗口不起）
    env = {k: v for k, v in os.environ.items()
           if k.upper() != "ELECTRON_RUN_AS_NODE"}
    # 与 pet_shell.GPU_FALLBACK_FLAGS 保持一致：改一处要同步另一处
    fallback = ["--in-process-gpu", "--disable-gpu-compositing"]

    def _run(extra: list[str]) -> tuple[bool, str, str]:
        profile = tempfile.mkdtemp(prefix="daniya_shell_probe_")
        cmd = [exe, main_js, "--user-data-dir=" + profile,
               "--hidden", "1", "--quit", "6", *extra]
        try:
            p = subprocess.run(cmd, cwd=os.path.join(ROOT, "shell"), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            out, rc = p.stdout or "", p.returncode
        except subprocess.TimeoutExpired:
            return False, f"{timeout:.0f}s 内没完成握手（超时）", ""
        except Exception as e:  # noqa: BLE001
            return False, f"启动失败：{e}", ""

        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if "CONTROL" in out:
            return True, "已建窗并完成控制通道握手", out
        fatal = [ln for ln in out.splitlines()
                 if "FATAL" in ln or "GPU process" in ln]
        why = fatal[0].split("] ", 1)[-1].strip() if fatal else f"没有 CONTROL 回执（rc={rc}）"
        return False, why, out

    ok, why, _ = _run([])
    if ok:
        return True, why
    # 裸启动失败才判定是否 GPU 崩溃，是则复刻生产的退让
    if "GPU process" in why:
        ok2, why2, _ = _run(fallback)
        if ok2:
            return True, f"{why2}（走了 GPU 回退开关：{' '.join(fallback)}）"
        return False, f"裸启动与软件合成开关都失败：{why2}"
    return False, why


def check_shell(cfg: object = None) -> tuple[str, str, str, str]:
    main_js = os.path.join(ROOT, "shell", "main.js")
    preload = os.path.join(ROOT, "shell", "preload.js")
    problems = []
    if not os.path.isfile(main_js):
        problems.append("缺 shell/main.js")
    if not os.path.isfile(preload):
        problems.append("缺 shell/preload.js")
    exe = _candidate_electron()
    if not exe:
        problems.append("未找到 electron.exe（shell/node_modules/electron/dist/）")
    if problems:
        return (WARN, "Electron 壳", "；".join(problems),
                "装壳：cd shell && npm install（离线环境可用环境变量 "
                "DANIYA_ELECTRON 指定已装好的 electron.exe）")

    mode = _shell_mode(cfg)
    if mode == "webview":
        return (WARN, "Electron 壳",
                "已安装，但配置为 webview（不走壳，桌宠背景会有 #F0F0F0 白底）",
                "要真透明：把 ui.pet_backend 设为 auto（默认）或 shell。"
                "壳撞上 GPU 崩溃时会自动带 --in-process-gpu --disable-gpu-compositing 重试")

    ok, why = probe_shell()
    if ok:
        return (PASS, "Electron 壳", f"配置为 {mode}，实起一次通过（{why}）", "")
    if mode == "shell":
        return (FAIL, "Electron 壳",
                f"配置要求 shell，但本机起不来：{why}",
                "把 ui.pet_backend 改成 auto（起不来会回退 webview，一定能出人）；"
                "或排查 GPU 驱动 / 杀软拦截后重跑本项")
    return (WARN, "Electron 壳",
            f"配置为 auto，但本机起不来：{why} → 启动时会自动回退 webview（白底）",
            "想拿到真透明就排查 GPU 驱动 / 杀软拦截后重跑本项；"
            "壳崩溃后会自动带 --in-process-gpu --disable-gpu-compositing 重试一次")


# ---------- 干净机器三件套（阶段 C4 / C7）----------
# 为什么单列一组：`--doctor` 在开发机上几乎永远绿，而真正出事的是「换台电脑」。
# 会让程序在干净机器上**打不开**的环境因素只有三类（杀软拦截无法本地判定，另两类能）：
#   · WebView2 Runtime 缺失 —— 主窗口渲染不出来（Win10 上很常见）
#   · Universal CRT 缺失    —— numpy/scipy 等扩展直接 DLL load failed
#   · 未签名                —— 不阻塞运行，但用户看到「未知发布者」、杀软更容易拦
# 把它们前置成自检项，等于把「另一台电脑」这个测不了的实验，变成用户能自己跑出结论的命令。

WV2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_WV2_REG_KEYS = (
    ("HKLM", "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\" + WV2_GUID),
    ("HKLM", "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + WV2_GUID),
    ("HKCU", "Software\\Microsoft\\EdgeUpdate\\Clients\\" + WV2_GUID),
)


def _wv2_registry_version() -> str:
    """注册表里的 WebView2 Runtime 版本；没有则空串。

    `pv` 存在但为空串表示**已卸载**，不能当已安装——实测见过这种残留键。
    """
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return ""
    for hive_name, sub in _WV2_REG_KEYS:
        hive = (winreg.HKEY_LOCAL_MACHINE if hive_name == "HKLM"
                else winreg.HKEY_CURRENT_USER)
        try:
            with winreg.OpenKey(hive, sub) as key:
                pv = str(winreg.QueryValueEx(key, "pv")[0] or "").strip()
        except OSError:
            continue
        if pv:
            return pv
    return ""


def _wv2_executable() -> str:
    """固定版本分发（Fixed Version）不写注册表，只能找 exe。"""
    bases: list[str] = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env)
        if root:
            bases.append(os.path.join(root, "Microsoft", "EdgeWebView", "Application"))
    bases.append(os.path.join(r"C:\Program Files (x86)", "Microsoft",
                              "EdgeWebView", "Application"))
    for base in bases:
        try:
            versions = sorted(os.listdir(base), reverse=True)
        except OSError:
            continue
        for v in versions:
            exe = os.path.join(base, v, "msedgewebview2.exe")
            if os.path.isfile(exe):
                return exe
    return ""


def check_webview2() -> tuple[str, str, str, str]:
    """主窗口的渲染引擎。pywebview 在 Windows 上走 WebView2，没有它窗口打不开。"""
    if not sys.platform.startswith("win"):
        return (WARN, "WebView2", f"非 Windows 平台（{sys.platform}），本项不适用", "")
    ver = _wv2_registry_version()
    if ver:
        return (PASS, "WebView2", f"已安装 Runtime {ver}", "")
    exe = _wv2_executable()
    if exe:
        return (PASS, "WebView2", f"找到 {exe}（注册表无记录，应为固定版本分发）", "")
    return (FAIL, "WebView2", "未检测到 WebView2 Runtime —— 主窗口将无法渲染（Win10 常见）",
            "装 Evergreen Runtime：https://developer.microsoft.com/microsoft-edge/webview2/ "
            "（Win11 自带；装完无需重启）")


def _system32() -> str:
    """系统目录。抽成函数是为了能被测试注入——否则这条检查永远只能在真机上验。"""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


def check_ucrt() -> tuple[str, str, str, str]:
    """Universal CRT —— 实测下来**唯一**真正靠系统的 C 运行时。

    2026-10-18 解析交付包内 300+ 个 PE 的导入表得出的（别再靠猜）：

        vcruntime140.dll     包内自带，93 个文件引用    → 不需要装 VC++ 运行库
        vcruntime140_1.dll   包内自带，32 个文件引用
        msvcp140.dll         引用数 0（numpy 用自带的哈希改名版）
        api-ms-win-crt-*     包外，133/84/64 个文件引用 → 靠系统 UCRT

    Win10 起 UCRT 是系统组件；Win7/8 需要 KB2999226。
    """
    if not sys.platform.startswith("win"):
        return (WARN, "UCRT", f"非 Windows 平台（{sys.platform}），本项不适用", "")
    sysdir = _system32()
    missing = [n for n in ("ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll")
               if not os.path.isfile(os.path.join(sysdir, n))]
    if not missing:
        return (PASS, "UCRT", "系统自带 Universal CRT（numpy/scipy 依赖它）", "")
    return (FAIL, "UCRT",
            "缺：" + ", ".join(missing) + " —— numpy/scipy 等扩展会 DLL load failed",
            "Win7/8 装 KB2999226（Update for Universal C Runtime）；"
            "Win10+ 也称缺失说明系统组件被裁剪，跑 sfc /scannow")


def pe_has_signature(path: str) -> tuple[bool, str]:
    """PE 里有没有**内嵌** Authenticode 签名块（WIN_CERTIFICATE）。

    只说「有没有」，不说「是否有效」：验真伪要 WinVerifyTrust 与证书链，那不是
    自检该干的事，也不该在没装根证书的机器上给出误导性结论。

    三个坑，都是 2026-10-18 实测踩到或查清的：
      1. 安全目录的 `VirtualAddress` 是**文件偏移**而非 RVA，不能拿去查节表。
      2. 数据目录起始偏移：PE32 是可选头 +0x60，PE32+ 是 +0x70。写反了读到的是
         Debug 目录 —— electron.exe 会恰好返回 28 字节（一个调试目录条目的大小），
         看上去"已签名"，其实是假的。用 PowerShell `Get-AuthenticodeSignature`
         交叉校验才暴露出来。
      3. 内嵌签名为空 ≠ 未签名：系统文件常见 catalog 签名（notepad.exe 就是
         内嵌为空但系统判定 Valid）。所以本函数只用于**我们自己的**交付件。
    """
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return (False, "缺 MZ 头")
            f.seek(0x3C)
            f.seek(int.from_bytes(f.read(4), "little"))
            if f.read(4) != b"PE\0\0":
                return (False, "PE 头不对")
            f.read(2)      # Machine
            f.read(2)      # NumberOfSections
            f.read(12)     # TimeDateStamp / PointerToSymbolTable / NumberOfSymbols
            f.read(2)      # SizeOfOptionalHeader
            f.read(2)      # Characteristics
            opt_off = f.tell()
            magic = int.from_bytes(f.read(2), "little")
            if magic == 0x10B:
                dd_off = opt_off + 0x60
            elif magic == 0x20B:
                dd_off = opt_off + 0x70
            else:
                return (False, f"未知 optional header magic 0x{magic:X}")
            f.seek(dd_off + 4 * 8)          # 目录索引 4 = Security
            va = int.from_bytes(f.read(4), "little")
            size = int.from_bytes(f.read(4), "little")
            if not va or not size:
                return (False, "无内嵌签名块")
            total = os.path.getsize(path)
            if va + size > total:
                return (False, f"签名块偏移越界（{va}+{size} > {total}）")
            return (True, f"{size} B")
    except Exception as e:  # noqa: BLE001
        return (False, f"{type(e).__name__}: {e}")


def _signature_targets() -> list[str]:
    """**我们自己产出的**可执行入口。

    只认包根的 exe。不含 `python/python.exe`（Python 软件基金会签的）与
    `shell/node_modules/electron/dist/electron.exe`（Electron 签的）：它们的
    签名不为我们的代码背书，把它们算进来会让「我们自己什么都没签」显示成 PASS
    —— 又一种假绿。2026-10-18 第一版就是这么写的，在真机上跑才看出来。
    """
    out: list[str] = []
    try:
        for n in sorted(os.listdir(ROOT)):
            if n.lower().endswith(".exe"):
                out.append(os.path.join(ROOT, n))
    except OSError:
        pass
    return out


def check_signature() -> tuple[str, str, str, str]:
    """阶段 C4：未签名时要**明确告诉用户**，而不是只在文档里提一句。"""
    if not sys.platform.startswith("win"):
        return (WARN, "代码签名", f"非 Windows 平台（{sys.platform}），本项不适用", "")
    targets = _signature_targets()
    if not targets:
        return (WARN, "代码签名",
                "没有自研可执行入口（包根没有 .exe），签名无可检对象",
                "这本身是个缺口：用户拿到的目录里只有 .py，双击无从启动，"
                "UAC / 杀软层面也没有可自证身份。发布前应产出签名过的启动器或安装包"
                "（见规划 C2/C4）")
    unsigned: list[str] = []
    for p in targets:
        ok, why = pe_has_signature(p)
        if not ok:
            unsigned.append(f"{os.path.relpath(p, ROOT)}（{why}）")
    if unsigned:
        return (WARN, "代码签名", "未发现内嵌签名：" + "；".join(unsigned),
                "未签名时用户会看到「未知发布者」，国内杀软更容易直接拦。"
                "选项：OV 证书（需企业资质）／EV 证书（可即时积累 SmartScreen 信誉）"
                "／暂不买则至少公开 SHA256 校验值并在 README 写明")
    return (PASS, "代码签名", f"{len(targets)} 个自研可执行入口均带内嵌签名", "")


def _port_state(port: int, timeout: float = 0.4) -> str:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return "占用" if s.connect_ex(("127.0.0.1", port)) == 0 else "空闲"
    finally:
        s.close()


def _collect_ports(cfg: object) -> list[int]:
    """从配置里收集所有 `*port` 数值（去重排序），避免把端口写死在脚本里。"""
    ports: set[int] = set()
    try:
        raw = getattr(cfg, "raw", None) or {}
    except Exception:  # noqa: BLE001
        raw = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, int) and str(k).lower().endswith("port"):
                    ports.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(raw)
    return sorted(p for p in ports if 0 < p < 65536)


def check_ports(cfg: object) -> tuple[str, str, str, str]:
    ports = _collect_ports(cfg)
    if not ports:
        return (WARN, "端口", "配置里没有找到任何端口字段", "")
    busy = [p for p in ports if _port_state(p) == "占用"]
    if not busy:
        return (PASS, "端口", f"{len(ports)} 个端口全部空闲", "")
    # 端口被占不算致命：可能是上次没退干净的自己，也可能是别的服务
    return (WARN, "端口", "被占用：" + ", ".join(str(p) for p in busy),
            "确认是不是本程序的残留进程；是的话在任务管理器结束 electron.exe / python.exe")


def check_config(path: str) -> tuple[str, str, str, str]:
    if not os.path.isfile(path):
        return (FAIL, "配置文件", f"找不到 {path}",
                f"确认运行目录，或用 --config 指定路径")
    try:
        from src.config import load_config
    except Exception as e:  # noqa: BLE001
        return (FAIL, "配置文件", f"无法导入 src.config：{e}", "检查依赖与运行目录")
    try:
        cfg = load_config(path)
    except Exception as e:  # noqa: BLE001
        return (FAIL, "配置文件", f"解析失败：{e}", "YAML 语法错误，检查缩进与特殊字符")
    ns = getattr(cfg, "ns", None)
    if ns is None:
        return (FAIL, "配置文件", "cfg.ns 缺失", "")
    # 【阶段 B4】用统一 schema 校验，把问题逐条呈现出来
    try:
        from src.config_schema import validate as _validate

        issues = _validate(getattr(cfg, "raw", None))
    except Exception as e:  # noqa: BLE001
        return (WARN, "配置文件", f"schema 校验未执行：{e}",
                "检查 src/config_schema.py")
    errs = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level != "error"]
    if errs:
        head = str(errs[0])
        more = f"（另有 {len(errs) - 1} 项）" if len(errs) > 1 else ""
        return (FAIL, "配置文件", head + more, errs[0].hint)
    if warns:
        more = f"（另有 {len(warns) - 1} 项）" if len(warns) > 1 else ""
        return (WARN, "配置文件", str(warns[0]) + more, warns[0].hint)
    # 【升级覆盖检测】出厂默认值 + 有使用痕迹 = 老用户配置大概率被新版本
    # 解压覆盖了（装配侧只能救装配机，用户机的 zip 解压拦不住）。
    # 两个信号同时成立才报：单 api_key 为空可能就是没填 Key 的新用户。
    bak = os.path.join(ROOT, "config.user.bak.yaml")
    data_dir = os.path.join(ROOT, "data")
    if os.path.isfile(bak):
        return (WARN, "配置文件", "发现 config.user.bak.yaml——你的旧配置被新版本覆盖了",
                "对比 config.user.bak.yaml 与 config.yaml，把 API Key 等改动补回来")
    raw_cfg = getattr(cfg, "raw", None) or {}
    keys_empty = all(not (raw_cfg.get(k) or "") for k in ("api_key",))
    if keys_empty and os.path.isdir(data_dir) and os.listdir(data_dir):
        return (WARN, "配置文件", "config.yaml 是出厂默认值，但 data/ 里有使用痕迹",
                "若你之前填过 API Key，配置可能被升级覆盖；找 config.user.bak.yaml 或重新填写")
    return (PASS, "配置文件", path, "")


def check_disk() -> tuple[str, str, str, str]:
    try:
        total, used, free = shutil.disk_usage(ROOT)
    except Exception as e:  # noqa: BLE001
        return (WARN, "磁盘", f"读取失败：{e}", "")
    free_gb = free / (1024 ** 3)
    lvl = PASS if free_gb >= 2 else (WARN if free_gb >= 0.5 else FAIL)
    hint = "" if lvl == PASS else "清理磁盘或换安装盘（录音/模型缓存很吃空间）"
    return (lvl, "磁盘", f"可用 {free_gb:.1f} GB / 共 {total / (1024 ** 3):.0f} GB", hint)


def check_logs() -> tuple[str, str, str, str]:
    """日志文件数量与占用（阶段 B2：轮转已实现，这里只负责报）。"""
    try:
        from src.log_rotate import report as _log_report

        rep = _log_report()
    except Exception as e:  # noqa: BLE001
        return (WARN, "日志", f"统计失败：{e}", "")
    counts = rep.get("counts") or {}
    total = sum(counts.values())
    if total == 0:
        return (PASS, "日志", "尚无日志文件", "")
    detail = f"{total} 个（run {counts.get('run_', 0)} / crash {counts.get('crash_', 0)}），共 {rep.get('total_human')}"
    if total < 60:
        return (PASS, "日志", detail, "")
    return (WARN, "日志", detail + "，偏多",
            f"启动时会自动保留最近 {_log_keep_default()} 份/类；"
            f"想立刻清理可手动删除 logs/ 下的旧文件")


def _log_keep_default() -> int:
    try:
        from src.log_rotate import DEFAULT_KEEP

        return int(DEFAULT_KEEP)
    except Exception:  # noqa: BLE001
        return 30


def check_secret_storage(path: str) -> tuple[str, str, str, str]:
    """API Key 落盘方式（C9-1，第 14 项）。

    文本级扫描（不 load_config——避免触发 DPAPI 解密与 schema 校验的副作用）：
    `api_key:` / `cookie:` 行（C9 缺口关闭：qzone.cookie 同级敏感）的值
    非空且不带 `dpapi:` 前缀即判明文。
    """
    _SECRET_PREFIXES = ("api_key:", "cookie:")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception as e:  # noqa: BLE001
        return (WARN, "密钥落盘", f"读取失败：{e}", "")
    plain = sealed = refs = 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- "):  # YAML 列表项：`- api_key: '...'`，先剥列表标记
            s = s[2:].strip()
        hit = next((p for p in _SECRET_PREFIXES if s.startswith(p)), None)
        if not hit:
            continue
        val = s[len(hit):].strip().strip("'\"")
        if not val:
            continue
        if val.startswith("dpapi:"):
            sealed += 1
        elif val.startswith("${") and val.endswith("}"):
            refs += 1   # 【2026-10-18】`${ENV_VAR}` 是环境变量引用，本身不含密钥
        else:
            plain += 1
    if plain and sealed:
        return (WARN, "密钥落盘", f"{plain} 处明文 / {sealed} 处已加密",
                "保存一次配置即可把明文 Key 自动加密为 dpapi: 密文（DPAPI，仅本机可解）")
    if plain:
        return (WARN, "密钥落盘", f"{plain} 处 API Key 明文落盘（拷走文件即泄露）",
                "保存一次配置即可自动加密为 dpapi: 密文（DPAPI，仅本机可解；换机需重填）")
    if sealed:
        # 注意：f-string 里要显示字面花括号得写 `{{}}`；`${...}` 里的 `...` 是
        # Python 的 Ellipsis 字面量，会被渲染成 "$Ellipsis"（2026-10-18 实锤）。
        extra = f"；另有 {refs} 处指向环境变量（${{ENV_VAR}}，密钥只在环境变量里，不落盘）" if refs else ""
        return (PASS, "密钥落盘", f"{sealed} 处 API Key 已 DPAPI 加密（仅本机可解）{extra}", "")
    if refs:
        return (PASS, "密钥落盘", f"{refs} 处 API Key 用 ${{ENV}} 引用（密钥只存环境变量，不落盘）", "")
    return (PASS, "密钥落盘", "未填写任何 API Key", "")


def _collect_llm_routes(raw: dict) -> list[tuple[str, str, str, str]]:
    """收集当前生效的模型链路 → [(label, base_url, model, api_key)]。

    只收**当前生效**的三处：主聊天(llm.openai)、手动选的聊天端口、active 视觉。
    不扫全部 20 个条目——否则每次 doctor 都在发几十个请求。去重是因为
    "主聊天"和"当前端口"常常就是同一个端点。
    """
    out: list[tuple[str, str, str, str]] = []

    def add(label: str, d: object) -> None:
        if not isinstance(d, dict):
            return
        base = (d.get("base_url") or "").strip().rstrip("/")
        model = (d.get("model") or "").strip()
        key = (d.get("api_key") or "").strip()
        if base and model:
            out.append((label, base, model, key))

    add("主聊天", (raw.get("llm") or {}).get("openai") or {})
    tc = raw.get("text_chat") or {}
    manual = str(tc.get("manual_provider") or "")
    for p in (tc.get("providers") or []):
        if isinstance(p, dict) and p.get("id") == manual:
            add("当前端口", p)
    vm = raw.get("vision_models") or {}
    active = str(vm.get("active") or "")
    for p in (vm.get("models") or []):
        if isinstance(p, dict) and p.get("id") == active:
            add("当前视觉", p)

    seen: set[tuple] = set()
    uniq: list[tuple[str, str, str, str]] = []
    for r in out:
        sig = (r[1], r[2], r[3])
        if sig not in seen:
            seen.add(sig)
            uniq.append(r)
    return uniq


def _probe_llm_routes(routes: list[tuple[str, str, str, str]],
                      urlopen=None, timeout: float = 12.0
                      ) -> tuple[str, str, str, str]:
    """逐条发最小请求探活（拆成独立函数，方便测试用假 urlopen 打桩）。

    判级（不按"有没有响应"，按 HTTP 语义）：
      200            → PASS
      429            → PASS  限流说明 **key 有效**，换个模型/等会即可
      400/401/402/403 → FAIL  key 有但服务商不认：欠费/账户异常/未授权
      其它状态码      → WARN
      超时/连接失败   → WARN  不能判红：断网时全红，doctor 就废了
      空 api_key     → 不请求，记入明细
    """
    import time
    import urllib.error
    import urllib.request

    if urlopen is None:
        urlopen = urllib.request.urlopen

    level = PASS
    details: list[str] = []
    for label, base, model, key in routes:
        if not key:
            if level == PASS:
                level = WARN
            details.append(f"{label}（{model}）未填 api_key")
            continue
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "回复OK"}],
            "max_tokens": 4,
        }).encode("utf-8")
        req = urllib.request.Request(
            base + "/chat/completions", data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        t0 = time.time()
        try:
            with urlopen(req, timeout=timeout) as r:  # noqa: PLW2901
                r.read()
            details.append(f"{label}（{model}）✓ {int((time.time() - t0) * 1000)}ms")
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:110]
            if e.code == 429:
                details.append(f"{label}（{model}）✓ 限流（key 有效）")
            elif e.code in (400, 401, 402, 403, 404):
                level = FAIL
                details.append(f"{label}（{model}）✗ HTTP {e.code} {msg}")
            else:
                if level == PASS:
                    level = WARN
                details.append(f"{label}（{model}）? HTTP {e.code} {msg}")
        except Exception as e:  # noqa: BLE001  断网/超时/证书
            if level == PASS:
                level = WARN
            details.append(f"{label}（{model}）? {type(e).__name__}（网络不通或超时）")

    if level == FAIL:
        hint = ("HTTP 400/403 多为账户异常（欠费、未实名、风控）；401 是 key 无效；"
                "402 是欠费。登录服务商控制台处理，或在 providers 里换一个渠道")
    elif level == WARN:
        hint = "补 api_key（setx 后重启软件）、或检查网络/代理"
    else:
        hint = ""
    name = {"PASS": "模型渠道", "WARN": "模型渠道有隐患", "FAIL": "模型渠道不可用"}[level]
    return (level, name, "；".join(details)[:200], hint)


def check_llm_channel(cfg: object) -> tuple[str, str, str, str]:
    """「当前生效的渠道真的能出话吗」。

    【2026-10-18】为什么加这一项：用户全部渠道填的都是阿里云百炼，而百炼账户
    HTTP 400 "not in good standing"（欠费/风控，**账户级**拦截——`GET /models`
    还能列出 176 个模型，chat 一律被拒）。症状完全是静默的：级联大脑的
    `enabled = bool(base_url and api_key and model)` 一假就只留一条 warning，
    通话不出声、配置里明明"填好了"，用户只会觉得"一开始就难"。
    """
    try:
        routes = _collect_llm_routes(getattr(cfg, "raw", None) or {})
    except Exception as e:  # noqa: BLE001
        return (WARN, "模型渠道", f"收集槽位失败：{e}", "")
    if not routes:
        return (WARN, "模型渠道", "没找到生效的聊天/视觉槽位",
                "到配置里填 llm.openai 或 text_chat.providers")
    try:
        return _probe_llm_routes(routes)
    except Exception as e:  # noqa: BLE001
        return (WARN, "模型渠道", f"探活失败：{e}", "")


def check_version(cfg: object) -> tuple[str, str, str, str]:
    """版本号的**单点是否真的成立**（阶段 B1 的收口）。

    真身在 `src/version.py`；`config.yaml` 的 `app.version` 只是给还没迁过来的
    旧代码读的。两处一旦漂移，"关于页 / 日志 / 安装包三处一致"就是句空话，
    所以这里把它变成一条能红的自检项。
    """
    try:
        from src.version import APP_VERSION

        code_ver = str(APP_VERSION)
    except Exception as e:  # noqa: BLE001
        return (FAIL, "版本一致", f"读不到 src/version.py：{e}",
                "检查 src/version.py 是否存在且语法正确")

    ns = getattr(cfg, "ns", None)
    raw = getattr(getattr(ns, "app", None), "version", None) if ns else None
    # 空串也算"没配"：以前 apply_defaults 会补 ""，这条 WARN 分支永远走不到，
    # 于是"没写版本号"被报成"版本漂移"的 FAIL（2026-10-18 质检 P1）。
    if raw is None or not str(raw).strip():
        return (WARN, "版本一致", f"配置里没有 app.version（代码侧 {code_ver}）",
                f"在 config.yaml 的 app: 段下加 version: {code_ver}")
    cfg_ver = str(raw).strip()
    if cfg_ver != code_ver:
        return (FAIL, "版本一致",
                f"漂移：src/version.py={code_ver} 但 app.version={cfg_ver}",
                f"把 config.yaml 的 app.version 改成 {code_ver}；"
                f"发版时只改 src/version.py 一处")
    return (PASS, "版本一致", f"app.version 与 src/version.py 均为 {code_ver}", "")


def check_pet_backend(cfg: object) -> tuple[str, str, str, str]:
    ns = getattr(cfg, "ns", None)
    raw = getattr(getattr(ns, "ui", None), "pet_backend", None) if ns else None
    try:
        from src.ui.pet_backend import normalize

        value = normalize(raw)
    except Exception as e:  # noqa: BLE001
        return (FAIL, "桌宠后端", f"无法解析 ui.pet_backend：{e}", "")
    if raw is None:
        return (WARN, "桌宠后端", f"配置里没有 ui.pet_backend，按 {value} 处理",
                f"在 config.yaml 的 ui: 段下加 pet_backend: {value}")
    if str(raw).strip().lower() != value:
        return (WARN, "桌宠后端", f"ui.pet_backend={raw!r} 非法 → 按 {value} 处理",
                "可选值：auto / webview / shell")
    return (PASS, "桌宠后端", value, "")


def check_pet_state() -> tuple[str, str, str, str]:
    """桌宠的**磁盘残留状态**——"桌宠不见了"类故障的第一现场。

    两条都只在真出问题时才报警，所以在健康机器上是干净的 PASS：
      · 握手文件残留 → 上次进程是被强杀/崩溃掉的，没走清理；
        而且那文件里带着 RPC 令牌，本身就该消失。
      · 位置存档损坏 → 桌宠会被甩到默认位（用户视角：人自己跑回右下角了）。
    """
    try:
        from src.ui import pet_state
    except Exception as e:  # noqa: BLE001
        return (FAIL, "桌宠状态", f"无法加载 pet_state：{e}", "检查 src/ui/pet_state.py")

    try:
        from src.ui.pet_shell import HANDSHAKE_PATH
    except Exception:  # noqa: BLE001
        HANDSHAKE_PATH = os.path.join(ROOT, "data", "pet_shell_handshake.json")

    if os.path.exists(HANDSHAKE_PATH):
        return (WARN, "桌宠状态",
                "发现上次运行残留的握手文件（进程未干净退出）",
                f"确认没有正在运行的桌宠后删除 {HANDSHAKE_PATH}；"
                f"该文件含 RPC 令牌，不应留在磁盘上")

    if os.path.exists(pet_state.STATE_PATH):
        st = pet_state.load()
        if st is None:
            return (WARN, "桌宠状态", "位置存档损坏，本次将回落到右下角默认位",
                    f"删除 {pet_state.STATE_PATH} 让它重新生成，或检查是否被其他程序写坏")
        return (PASS, "桌宠状态",
                f"位置存档可用（rx={st.get('rx')}, ry={st.get('ry')}）", "")

    return (PASS, "桌宠状态", "无残留状态（位置将用默认右下角）", "")


def _takes_arg(fn: object) -> bool:
    """检查函数是否声明了形参 —— 用来决定要不要把 cfg 传进去。

    不能写成 `fn(cfg) if cfg is not None else fn()`：配置加载失败时 cfg 恰好
    就是 None，而依赖 cfg 的检查项恰恰**必须**在此时被调用，好让它自己输出
    FAIL。按 None 判断会让它们退化成无参调用 → TypeError → 报成
    「doctor 内部异常」，把真正的配置问题盖掉。2026-10-18 实测撞到。
    """
    try:
        return bool(inspect.signature(fn).parameters)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def run_all(config_path: str) -> list[tuple[str, str, str, str]]:
    """跑完全部检查；任何一项自己炸了都降级成 WARN，不让整体崩。"""
    results: list[tuple[str, str, str, str]] = []
    cfg = None
    try:
        from src.config import load_config

        cfg = load_config(config_path)
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, "配置文件", f"加载失败：{e}",
                        "检查 YAML 语法与路径"))

    for fn, arg in ((check_runtime, None),
                    (check_runtime_isolation, None),
                    (check_deps, None),
                    (check_config, config_path),
                    (check_shell, cfg),
                    (check_webview2, None),
                    (check_ucrt, None),
                    (check_signature, None),
                    (check_ports, cfg),
                    (check_pet_backend, cfg),
                    (check_pet_state, None),
                    (check_version, cfg),
                    (check_disk, None),
                    (check_logs, None),
                    (check_secret_storage, config_path),
                    (check_llm_channel, cfg)):
        try:
            # 按「函数自己声明的形参」决定传不传，而不是按参数值是否为 None。
            # 见 _takes_arg 的注释：那是本文件踩过的坑。
            results.append(fn(arg) if _takes_arg(fn) else fn())
        except Exception as e:  # noqa: BLE001
            results.append((FAIL, type(e).__name__,
                            f"自检项自身异常：{e}", "这是 doctor 的 bug，请报给维护者"))
    return results


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="达妮娅 AI 桌面伴侣 · 一键自检")
    ap.add_argument("--config", default="config.yaml", help="配置文件路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON（便于贴日志）")
    args = ap.parse_args(argv)

    results = run_all(args.config)
    worst = min((r[0] for r in results), key=lambda x: _ORDER[x])

    if args.json:
        print(json.dumps(
            [{"level": r[0], "item": r[1], "detail": r[2], "hint": r[3]}
             for r in results], ensure_ascii=False, indent=2))
        return 1 if worst == FAIL else 0

    try:
        from src.version import name_version
        print(name_version())
    except Exception:  # noqa: BLE001
        print("达妮娅 AI 桌面伴侣")
    print("=" * 60)
    for lvl, item, detail, hint in results:
        print(f"  [{lvl}] {item:10s} {detail}")
        if hint:
            print(f"          ↳ 修复：{hint}")
    print("=" * 60)
    n_fail = sum(1 for r in results if r[0] == FAIL)
    n_warn = sum(1 for r in results if r[0] == WARN)
    print(f"汇总：{len(results) - n_fail - n_warn} PASS / {n_warn} WARN / {n_fail} FAIL")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
