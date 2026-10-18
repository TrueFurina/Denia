"""`tools/make_dist.py` 依赖分级安装部分的测试。

只测**不联网、不真装包**的部分：requirements 分档解析、档位推进规则、失败定责。
真跑 pip 的那条路径由 `pet_shell_selfheal_smoke.py` 那种端到端脚本负责 ——
单元测试里发网络请求，只会得到一个"看心情红"的门禁。

用假 `_pip` 顶掉真安装，断言的是**决策**：
什么情况下停、什么时候可以继续、装的时候用的是本地 wheel 还是联网。

运行：.venv/Scripts/python.exe src/tests/test_make_dist_deps.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import make_dist  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


# 三档各放一项，够区分规则了；真实文件有 12/13/16 项，逻辑一样
REQ = (
    "# 说明头（分档标记之前，应算 core）\n"
    "pre-marker>=1.0\n"
    "\n"
    "# ===== 核心（缺一不可，软件启动必需）=====\n"
    "PyYAML>=6.0\n"
    "httpx>=0.27\n"
    "\n"
    "# ===== 重要（录音/托盘/音量/游戏等部分功能）=====\n"
    "PyAudio>=0.2.14\n"
    "\n"
    "# ===== 可选（装了更好，失败不影响启动）=====\n"
    "opencv-python>=4.9\n"
)


class _FakeResult:
    def __init__(self, rc: int, out: str = "") -> None:
        self.returncode = rc
        self.stdout = out


def _mk_out(tmp: str, with_python: bool = True) -> str:
    """造一个假的 dist 目录，返回目录路径。"""
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "requirements.txt"), "w", encoding="utf-8") as fh:
        fh.write(REQ)
    if with_python:
        pd = os.path.join(tmp, "python")
        os.makedirs(pd, exist_ok=True)
        with open(os.path.join(pd, "python.exe"), "w"):
            pass
    return tmp


def _req(out_dir: str) -> str:
    return os.path.join(out_dir, "requirements.txt")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="denia_dist_test_")
    # 真身要在打桩之前抓在手里：后面几个用例会把 make_dist._pip 换成假的，
    # 等到 [11] 再想测「真 _pip 的异常兜底」时，抓到的就是上一个假货了。
    real_pip = make_dist._pip
    orig_pip, orig_verify = real_pip, make_dist.verify_deps
    try:
        print("[1] 三档归位：注释里的分档标题要起作用")
        tiers = make_dist.parse_requirements(_req(_mk_out(os.path.join(tmp, "o1"))))
        check(tiers["core"] == ["pre-marker>=1.0", "PyYAML>=6.0", "httpx>=0.27"],
              f"核心档含标记前那一项：{tiers['core']}")
        check(tiers["important"] == ["PyAudio>=0.2.14"], f"重要档：{tiers['important']}")
        check(tiers["optional"] == ["opencv-python>=4.9"], f"可选档：{tiers['optional']}")

        print("[2] 分档标记之前的内容算 core（宁可多装不可漏装）")
        check("pre-marker>=1.0" in tiers["core"],
              "文件头/标记前的依赖没有被丢进可选档")

        print("[3] 文件不存在 → 三档皆空，不抛异常")
        empty = make_dist.parse_requirements(os.path.join(tmp, "nope.txt"))
        check(all(v == [] for v in empty.values()), "缺失文件返回空档")

        print("[4] 行内注释要剥掉，不能当成包名传给 pip")
        p = os.path.join(tmp, "o4", "requirements.txt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# ===== 重要 =====\nPyAudio>=0.2.14  # 需要 VC++\n\n")
        t4 = make_dist.parse_requirements(p)
        check(t4["important"] == ["PyAudio>=0.2.14"], f"行内注释已剥离：{t4['important']}")

        print("[5] 档位递进：core ⊂ important ⊂ all")
        s_core = make_dist.tier_specs(tiers, "core")
        s_imp = make_dist.tier_specs(tiers, "important")
        s_all = make_dist.tier_specs(tiers, "all")
        check(len(s_core) == 3 and len(s_imp) == 4 and len(s_all) == 5,
              f"递进正确：{len(s_core)}/{len(s_imp)}/{len(s_all)}")
        check(all(x in s_imp for x in s_core), "important 包含 core")

        print("[6] 包内没有 python/python.exe → 明确拒绝，绝不拿 dev 环境凑数")
        out6 = _mk_out(os.path.join(tmp, "o6"), with_python=False)
        calls6: list[list[str]] = []
        make_dist._pip = lambda py, args: (calls6.append(args), (0, ""))[1]
        ok6 = make_dist.install_deps(out6, verify=False)
        check(not ok6, "没有内置运行时时返回 False")
        check(not calls6, "一次 pip 都没跑（不会误装到开发环境）")

        print("[7] 核心档装不上 → 立刻停手，且不往下推进")
        out7 = _mk_out(os.path.join(tmp, "o7"))
        seen7: list[list[str]] = []

        def _pip7(py, args):
            specs = [a for a in args if ">=" in a]
            seen7.append(specs)
            return (1, ["boom"]) if any(s.startswith("PyYAML") for s in specs) else (0, [])

        make_dist._pip = _pip7
        make_dist.verify_deps = lambda *a, **k: True
        ok7 = make_dist.install_deps(out7, upto="important")
        check(not ok7, "核心档失败 → 返回 False")
        check(len(seen7) == 1, f"只跑了核心档就停（实际 {len(seen7)} 档）")

        print("[8] 重要档装不上 → 只告警，核心成功就算过")
        out8 = _mk_out(os.path.join(tmp, "o8"))
        seen8: list[list[str]] = []

        def _pip8(py, args):
            specs = [a for a in args if ">=" in a]
            seen8.append(specs)
            return (1, ["boom"]) if any(s.startswith("PyAudio") for s in specs) else (0, [])

        make_dist._pip = _pip8
        ok8 = make_dist.install_deps(out8, upto="important")
        check(ok8, "重要档失败不影响整体成功")
        check(len(seen8) == 2, f"核心档跑完后仍继续跑了重要档（{len(seen8)} 档）")

        print("[9] 有本地 wheelhouse → 走 --no-index（真离线，不偷偷联网）")
        out9 = _mk_out(os.path.join(tmp, "o9"))
        wh9 = os.path.join(out9, "wheels")
        os.makedirs(wh9, exist_ok=True)
        with open(os.path.join(wh9, "demo-1.0-py3-none-any.whl"), "w"):
            pass
        args9: list[list[str]] = []

        def _pip9(py, args):
            args9.append(list(args))
            return (0, [])

        make_dist._pip = _pip9
        make_dist.install_deps(out9, upto="core")
        joined = [" ".join(a) for a in args9]
        check(all("--no-index" in a for a in args9), "每条都带了 --no-index")
        check(all("--find-links" in a for a in args9), "每条都带了 --find-links")
        check(not any("--upgrade" in a for a in joined), "离线安装不该顺带升级")

        print("[10] 可选档默认不装，--tier all 才装")
        out10 = _mk_out(os.path.join(tmp, "o10"))
        seen10: list[list[str]] = []

        def _pip10(py, args):
            seen10.append([a for a in args if ">=" in a])
            return (0, [])

        make_dist._pip = _pip10
        make_dist.install_deps(out10, upto="important", verify=False)
        flat10 = [s for specs in seen10 for s in specs]
        check(not any(s.startswith("opencv-python") for s in flat10),
              "默认不装可选项")
        seen10.clear()
        make_dist.install_deps(out10, upto="all", verify=False)
        flat10b = [s for specs in seen10 for s in specs]
        check(any(s.startswith("opencv-python") for s in flat10b),
              "--tier all 才会装可选项")

        print("[11] _pip 出错时返回 rc!=0 且不抛（subprocess 异常要兜住）")
        orig_run = make_dist.subprocess.run

        def _boom(*a, **k):
            raise RuntimeError("no pip")

        make_dist.subprocess.run = _boom
        try:
            # 必须用开头抓下来的真身：make_dist._pip 此刻还是 [10] 里的假货
            rc, out = real_pip("python.exe", ["--version"])
        finally:
            make_dist.subprocess.run = orig_run
        check(rc != 0, f"subprocess 抛异常时 rc!=0（rc={rc}）")
        check(bool(out), "异常信息被带回到输出里")

        print("[12] 档位是「到此为止」，不是「全部装完」")
        # 这条对应一个真实回归：--tier core 却把「重要」档也装了。
        # 当时的用例只验了「all 会多装可选」，没有任何一条验「core 会少装重要」，
        # 于是 bug 一路漏到打包现场才被 pip 日志暴露。
        out12 = _mk_out(os.path.join(tmp, "o12"))
        tiers12: list[int] = []

        def _pip12(py, args):
            tiers12.append(len([a for a in args if ">=" in a]))
            return (0, [])

        make_dist._pip = _pip12
        make_dist.install_deps(out12, upto="core", verify=False)
        check(len(tiers12) == 1, f"upto=core 只该装一档，实际 {len(tiers12)} 档：{tiers12}")
        tiers12.clear()
        make_dist.install_deps(out12, upto="important", verify=False)
        check(len(tiers12) == 2, f"upto=important 该装两档，实际 {len(tiers12)} 档：{tiers12}")

        print("[13] --offline 但没 wheel → 必须失败，不能偷偷联网")
        # 「离线」如果会静默退化成联网，那它对目标机器（断网）就是假的承诺。
        out13 = _mk_out(os.path.join(tmp, "o13"))
        calls13: list[list[str]] = []
        make_dist._pip = lambda py, args: (calls13.append(args), (0, ""))[1]
        ok13 = make_dist.install_deps(out13, upto="core", offline=True, verify=False)
        check(not ok13, "--offline 且无 wheelhouse → 返回 False")
        check(not calls13, "一次 pip 都没跑（没有偷偷联网装）")

        print("[14] 验收必须在隔离环境下跑，否则验的是构建机")
        # 真实事故：verify_deps 的 docstring 写着「不能用构建机的 dev 环境验」，
        # 但 subprocess.run 没传 env，子进程继承了构建机的用户级 site-packages。
        # 结果一个缺 rich/httpx/requests/websockets/dashscope 五个核心包的交付件，
        # 在 --doctor 里拿到 9 PASS /「可发布」。规矩没写进 env=，等于没写。
        out14 = _mk_out(os.path.join(tmp, "o14"))
        seen14: list[dict] = []
        orig_run14 = make_dist.subprocess.run

        class _R14:
            returncode = 0
            stdout = "  [OK] 版本一致\n"

        def _run14(*a, **k):
            seen14.append(dict(k))
            return _R14()

        make_dist.subprocess.run = _run14
        try:
            ok14 = orig_verify(out14, os.path.join(out14, "python", "python.exe"))
        finally:
            make_dist.subprocess.run = orig_run14
        check(ok14, "doctor 全绿时 verify_deps 返回 True")
        # 先判 env 是否存在，再判内容：否则 "PYTHONPATH" not in {} 会恒真，
        # 变异体（删掉 env=）下那两条照样打勾 —— 空洞通过比不通过更危险。
        env14 = seen14[0].get("env") if seen14 else None
        check(env14 is not None, "验收子进程收到独立的 env 参数")
        if env14 is None:
            check(False, "没有 env，隔离断言无法成立")
            check(False, "没有 env，PYTHONPATH 断言无法成立")
            check(False, "没有 env，PYTHONHOME 断言无法成立")
        else:
            check(env14.get("PYTHONNOUSERSITE") == "1",
                  f"PYTHONNOUSERSITE=1（实际 {env14.get('PYTHONNOUSERSITE')!r}）")
            check("PYTHONPATH" not in env14, "PYTHONPATH 没被带进验收现场")
            check("PYTHONHOME" not in env14, "PYTHONHOME 没被带进验收现场")

        # 验收的另一个方向：doctor 报 FAIL 时必须返回 False（不能只看 rc）
        seen14.clear()

        class _R14b:
            returncode = 0
            stdout = "  [FAIL] 核心依赖缺失\n"

        make_dist.subprocess.run = lambda *a, **k: _R14b()
        try:
            ok14b = orig_verify(out14, os.path.join(out14, "python", "python.exe"))
        finally:
            make_dist.subprocess.run = orig_run14
        check(not ok14b, "doctor 输出含 [FAIL] 时 verify_deps 返回 False")

        print("[15] _clean_env 不能改动父进程环境")
        os.environ["PYTHONPATH"] = "C:/dirty-dev-path"
        try:
            cleaned = make_dist._clean_env()
            check("PYTHONPATH" not in cleaned, "返回值里已剔除 PYTHONPATH")
            check(os.environ.get("PYTHONPATH") == "C:/dirty-dev-path",
                  "父进程的 PYTHONPATH 原样保留（返回的是副本）")
        finally:
            os.environ.pop("PYTHONPATH", None)

        print("[16] 装依赖时也必须隔离：否则 pip 认为「已满足」就跳过")
        # 真实现象（同一台构建机）：
        #   pip install rich → Requirement already satisfied:
        #   rich in c:\users\...\AppData\Roaming\Python\Python312\site-packages
        # 于是包内一个字节都没装，而验收又蹭同一个 site-packages，全绿放行。
        calls16: list[dict] = []
        orig_run16 = make_dist.subprocess.run

        class _R16:
            returncode = 0
            stdout = "Successfully installed\n"

        def _run16(*a, **k):
            calls16.append(dict(k))
            return _R16()

        make_dist.subprocess.run = _run16
        try:
            real_pip("python.exe", ["install", "rich"])   # 真身：[6] 起 _pip 已被打桩
        finally:
            make_dist.subprocess.run = orig_run16
        env16 = calls16[0].get("env") if calls16 else None
        check(env16 is not None, "_pip 调用 subprocess 时传了 env")
        if env16 is None:
            check(False, "pip 未隔离，PYTHONNOUSERSITE 无法确认")
        else:
            check(env16.get("PYTHONNOUSERSITE") == "1",
                  f"pip 跑在 PYTHONNOUSERSITE=1 下（实际 {env16.get('PYTHONNOUSERSITE')!r}）")
    finally:
        make_dist._pip, make_dist.verify_deps = orig_pip, orig_verify
        shutil.rmtree(tmp, ignore_errors=True)

    print("[17] 运行期隔离：_pth 必须关掉 site，同时把 pywin32 的路径补回来")
    # 真事故（2026-10-18）：交付包 python/ 是完整 Python 拷贝，standard 模式下
    # site 会加载 %APPDATA%\Roaming\Python\...，构建机装过什么都能被借到。
    # 写一个 pythonXY._pth 进入 isolated 模式后 sys.path 由文件固定，用户 site
    # 再也进不来。代价是 site-packages/*.pth 全部失效 —— 全包只有一个
    # pywin32.pth，它负责 win32 / win32\lib / Pythonwin 三段路径，必须手工补。
    probe = os.path.join(tempfile.gettempdir(), "md_pth_probe")
    shutil.rmtree(probe, ignore_errors=True)
    pay = os.path.join(probe, "python")
    sp = os.path.join(pay, "Lib", "site-packages")
    try:
        for sub in (os.path.join("win32", "lib"), "Pythonwin"):
            os.makedirs(os.path.join(sp, sub), exist_ok=True)
        open(os.path.join(pay, "python312.dll"), "wb").close()

        check(make_dist.runtime_tag(probe) == "python312",
              f"从 python312.dll 认出 tag（实得 {make_dist.runtime_tag(probe)!r}）")

        lines = make_dist.isolated_pth_lines(probe)
        check("Lib\\site-packages" in lines, "_pth 必须显式列出 site-packages（site 不跑了）")
        check("Lib\\site-packages\\win32" in lines
              and "Lib\\site-packages\\win32\\lib" in lines
              and "Lib\\site-packages\\Pythonwin" in lines,
              f"pywin32 的三段路径补回来了：{lines}")
        check(lines[-1] == "..", f"包根放在最后，避免遮蔽标准库（实得 {lines[-1]!r}）")
        check(lines.index("Lib") < lines.index(".."), "stdlib 必须排在包根前面")

        ok17, _ = make_dist.write_isolated_pth(probe)
        pth = os.path.join(pay, "python312._pth")
        check(ok17 and os.path.isfile(pth), f"文件写出来了：{pth}")
        raw = open(pth, "rb").read()
        check(b"\r\n" in raw, "用 CRLF（与同目录其它文本文件一致，别让 git 检出抖）")
        text = raw.decode("utf-8")
        check("import " not in text,
              "不能写 import 行：._pth 里它一律 unsupported，写了等于没写")

        # 关掉隔离时必须真删：否则上一次装配留下的 _pth 会继续生效，
        # 「这次没开隔离」和「运行时确实没隔离」变成两回事。
        ok18, _ = make_dist.write_isolated_pth(probe, enable=False)
        check(not ok18 and not os.path.exists(pth), "enable=False 要把旧 _pth 删掉")
    finally:
        shutil.rmtree(probe, ignore_errors=True)

    print("[18] _pth 里不能出现凭空捏造的路径")
    probe2 = os.path.join(tempfile.gettempdir(), "md_pth_probe2")
    shutil.rmtree(probe2, ignore_errors=True)
    try:
        pay2 = os.path.join(probe2, "python")
        os.makedirs(os.path.join(pay2, "Lib", "site-packages"), exist_ok=True)
        open(os.path.join(pay2, "python312.dll"), "wb").close()
        # 没装 pywin32 时，三条子目录路径一条都不该写：写进去只会让人
        # 以为「这里有东西」，而 python.exe 找不到就静默跳过，查错时白绕。
        lines2 = make_dist.isolated_pth_lines(probe2)
        check(not any("win32" in x or "Pythonwin" in x for x in lines2),
              f"没装 pywin32 就不写它的路径：{lines2}")
        check("python312.zip" not in lines2,
              "没有 stdlib zip 就不写 zip 行（写了是撒谎）")
        check(make_dist.runtime_tag(os.path.join(probe2, "nothing")) == "",
              "目录不存在时 tag 返回空串，不抛异常")
    finally:
        shutil.rmtree(probe2, ignore_errors=True)

    print("[19] pywin32 的 DLL 必须铺到解释器同目录（isolated 下唯一对任何入口都有效的路）")
    # 实测三条路（2026-10-18）：
    #   _pth 里加 pywin32_system32      → 无效，sys.path 对依赖 DLL 无意义
    #   main.py 里 add_dll_directory    → 有效，但只管走 main.py 的入口
    #   复制到 python/ 根               → 有效，且 `python -c "import win32api"` 也过
    probe3 = os.path.join(tempfile.gettempdir(), "md_dll_probe")
    shutil.rmtree(probe3, ignore_errors=True)
    try:
        pay3 = os.path.join(probe3, "python")
        sysdir = os.path.join(pay3, "Lib", "site-packages", "pywin32_system32")
        os.makedirs(sysdir, exist_ok=True)
        # 故意建一个 DLLs/：CPython 的扩展模块目录，看起来"更像该放 DLL 的地方"，
        # 但它不在依赖 DLL 的搜索路径里。建出来是为了让「落点错了」这个变异
        # 真的能被执行到——否则函数提前 return []，落点断言会空洞通过。
        os.makedirs(os.path.join(pay3, "DLLs"), exist_ok=True)
        for n in ("pythoncom312.dll", "pywintypes312.dll", "readme.txt"):
            with open(os.path.join(sysdir, n), "wb") as f:
                f.write(b"DLLDATA")

        got = make_dist.bundle_pywin32_dlls(probe3)
        check(sorted(got) == ["pythoncom312.dll", "pywintypes312.dll"],
              f"只搬 .dll，别把同目录其它文件一起搬：{got}")
        check(len(got) == 2
              and all(os.path.isfile(os.path.join(pay3, n)) for n in got),
              "落点是 python/ 根（python.exe 同目录）")
        check(not any(os.path.isfile(os.path.join(pay3, "DLLs", n)) for n in got),
              "不能放进 DLLs/ —— 那里不在依赖 DLL 的搜索路径里，等于没放")
        check(not os.path.isfile(os.path.join(pay3, "readme.txt")),
              "非 DLL 文件不该被复制到解释器目录")

        # 没装 pywin32 时不能炸，也不能编出空目录
        probe4 = os.path.join(tempfile.gettempdir(), "md_dll_probe_none")
        shutil.rmtree(probe4, ignore_errors=True)
        os.makedirs(os.path.join(probe4, "python"), exist_ok=True)
        check(make_dist.bundle_pywin32_dlls(probe4) == [],
              "没装 pywin32 时返回空列表，不抛异常")
        shutil.rmtree(probe4, ignore_errors=True)
    finally:
        shutil.rmtree(probe3, ignore_errors=True)

    print("[20] 装配残留：__pycache__ 要清，但别顺手动 python/ 与 tests/")
    # 真事故：--force 只覆盖不删多余，上一次在包内跑过 main.py 留下的
    # src/__pycache__ 会一直躺着，凭据闸判「包内混入目录」直接拦下发布。
    probe5 = os.path.join(tempfile.gettempdir(), "md_stale_probe")
    shutil.rmtree(probe5, ignore_errors=True)
    try:
        for p in ("src/__pycache__", "python/Lib/json/__pycache__", "tests", "vendor"):
            os.makedirs(os.path.join(probe5, p), exist_ok=True)
        with open(os.path.join(probe5, "src", "a.pyc"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(probe5, "run.log"), "w", encoding="utf-8") as f:
            f.write("noise")

        killed = make_dist.purge_stale_caches(probe5)
        check(not os.path.exists(os.path.join(probe5, "src", "__pycache__")),
              "src/__pycache__ 必须清掉")
        check(not os.path.exists(os.path.join(probe5, "src", "a.pyc")), ".pyc 清掉")
        check(not os.path.exists(os.path.join(probe5, "run.log")), ".log 清掉")
        # 这两条是「别做过头」的护栏：静默删目录比留下垃圾更危险
        check(os.path.isdir(os.path.join(probe5, "python", "Lib", "json", "__pycache__")),
              "python/ 下的字节码不动（删了只是首启变慢，不值）")
        check(os.path.isdir(os.path.join(probe5, "tests")),
              "不许顺手删 tests/ —— 漏拷了该让闸门抓住，不是悄悄抹掉")
        check(len(killed) >= 3, f"清掉的项要报出来而不是默默做：{len(killed)} 项")
    finally:
        shutil.rmtree(probe5, ignore_errors=True)

    print("[21] 升级吞配置：装配覆盖前，改过的 config.yaml 必须被抢救")
    # 真缺口：RENAME 把 config.release.yaml 写成包内 config.yaml，用户填了
    # API Key 后，下个版本解压覆盖 = Key 静默丢失。装配侧能救的是 --force
    # 重装配场景；用户机靠 doctor 的升级覆盖检测（test_doctor [12]）。
    probe6 = os.path.join(tempfile.gettempdir(), "md_cfg_probe")
    shutil.rmtree(probe6, ignore_errors=True)
    try:
        release = os.path.join(ROOT, "config.release.yaml")
        with open(release, "rb") as f:
            template = f.read()

        # 6.1 出厂默认（与模板一致）→ 不备份，覆盖无损失
        os.makedirs(probe6, exist_ok=True)
        with open(os.path.join(probe6, "config.yaml"), "wb") as f:
            f.write(template)
        check(make_dist.preserve_user_config(probe6, ROOT) is None,
              "config.yaml 与出厂默认一致时不该备份")

        # 6.2 用户改过 → 备份成 config.user.bak.yaml，内容=旧 config
        with open(os.path.join(probe6, "config.yaml"), "ab") as f:
            f.write(b"\n# user touched\napi_key: 'sk-user-secret'\n")
        bak = make_dist.preserve_user_config(probe6, ROOT)
        check(bak is not None and os.path.isfile(bak), "改过的 config 必须被备份")
        if bak:
            with open(bak, "rb") as f:
                saved = f.read()
            check(b"sk-user-secret" in saved, "备份内容必须是用户版而不是模板")
        check(os.path.isfile(os.path.join(probe6, "config.yaml")),
              "备份是复制不是移动，原文件留给装配流程覆盖")

        # 6.3 目标还没有 config（首次装配）→ 无事可做
        os.remove(os.path.join(probe6, "config.yaml"))
        if bak and os.path.isfile(bak):
            os.remove(bak)
        check(make_dist.preserve_user_config(probe6, ROOT) is None,
              "目标没有 config.yaml 时返回 None，不抛异常")
    finally:
        shutil.rmtree(probe6, ignore_errors=True)

    # ===== 【C9-2】scan_stale_pth：isolated 模式下新装 .pth 静默失效检测 =====
    print("\n-- scan_stale_pth（_pth 维护税）")
    probe7 = tempfile.mkdtemp(prefix="make_dist_pth_")
    try:
        # 7.1 没有 site-packages → 空列表，不抛异常
        check(make_dist.scan_stale_pth(probe7) == [],
              "无 site-packages 时返回空列表")
        # 7.2 只有白名单 pywin32.pth → 不报
        sp = os.path.join(probe7, "python", "Lib", "site-packages")
        os.makedirs(sp)
        open(os.path.join(sp, "pywin32.pth"), "w").write("import sys; sys.path.append('x')\n")
        check(make_dist.scan_stale_pth(probe7) == [],
              "白名单 pywin32.pth 不报警")
        # 7.3 新装 .pth → 报出且有序
        open(os.path.join(sp, "foo.pth"), "w").write("some\\path\n")
        open(os.path.join(sp, "__editable__.demo-0.1.pth"), "w").write("demo\n")
        check(make_dist.scan_stale_pth(probe7)
              == ["__editable__.demo-0.1.pth", "foo.pth"],
              "新装 .pth 全部报出且有序")
        # 7.4 大小写不敏感
        open(os.path.join(sp, "BAR.PTH"), "w").write("y\n")
        got = make_dist.scan_stale_pth(probe7)
        check("BAR.PTH" in got, f"大写扩展名也抓到（{got}）")
        # 7.5 warn_stale_pth 有发现时打印指引、无发现时安静
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            make_dist.warn_stale_pth(probe7)
        out = buf.getvalue()
        check("foo.pth" in out and "python312._pth" in out and "⚠" in out,
              "有发现时打印指引并点名文件")
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            make_dist.warn_stale_pth(os.path.join(probe7, "python"))
        check(buf2.getvalue() == "", "无发现时不输出任何内容")
    finally:
        shutil.rmtree(probe7, ignore_errors=True)

    # ===== 【C9-4】wheelhouse 只许存 wheel：pip download 会给无 wheel 的包
    # 存 sdist（实测 srt/proxy_tools），离线 --no-index 安装时构建 sdist 需要
    # 联网拉 build 后端 → 必失败。改用 pip wheel（构建机联网时构建成 .whl）。
    print("\n-- build_wheelhouse：wheel-only 纪律")
    probe8 = tempfile.mkdtemp(prefix="make_dist_wh_")
    try:
        os.makedirs(os.path.join(probe8, "python"))
        open(os.path.join(probe8, "python", "python.exe"), "w").close()
        open(os.path.join(probe8, "requirements.txt"), "w", encoding="utf-8").write(
            "# ===== 核心（缺一不可，软件启动必需）=====\nPyYAML\n")
        real_pip8 = make_dist._pip
        calls8: list[list[str]] = []

        def _pip8(py, args):
            calls8.append(args)
            # 模拟 pip download 行为：顺手放个 sdist 进 dest（正是要堵的场景）
            dest = [a.split("=", 1)[1] for a in args if a.startswith("--dest=")]
            # pip download 的 -d 是短参，按位置取
            if not dest:
                for i, a in enumerate(args):
                    if a == "-d":
                        dest.append(args[i + 1])
            if dest:
                open(os.path.join(dest[0], "srt-3.5.3.tar.gz"), "w").close()
            return 0, ["ok"]

        make_dist._pip = _pip8
        try:
            ok = make_dist.build_wheelhouse(probe8, os.path.join(probe8, "python", "python.exe"), "core")
            check(ok, "构建返回成功")
            check(bool(calls8) and calls8[0][0] == "wheel",
                  f"用 pip wheel 而非 pip download（实际首参数 {calls8[0][0] if calls8 else '无'}）")
            wh = os.path.join(probe8, "wheels")
            leftovers = [f for f in os.listdir(wh) if f.endswith((".tar.gz", ".zip"))]
            check(leftovers == [], f"wheelhouse 里无 sdist 残留（实际 {leftovers}）")
        finally:
            make_dist._pip = real_pip8
    finally:
        shutil.rmtree(probe8, ignore_errors=True)

    print("== [C2-A] 安装器版本注入 ==")
    nsi_path = os.path.join(ROOT, "tools", "installer.nsi")

    # 版本号单一真值：make_dist.read_app_version 必须与直接执行 version.py 一致。
    # 两法互证，不在测试里硬编码版本（否则发版必红——P2/P3 fixture 教训）。
    ns: dict = {}
    with open(os.path.join(ROOT, "src", "version.py"), encoding="utf-8") as fh:
        exec(fh.read(), ns)  # noqa: S102 - 受控仓库内文件
    ver = make_dist.read_app_version(ROOT)
    check(ver == ns["APP_VERSION"],
          f"read_app_version 与 version.py 执行结果一致（{ver}）")
    check(len(ver.split(".")) == 3 and all(p.isdigit() for p in ver.split(".")),
          "版本号形如 x.y.z")

    # .nsi 的 VERSION/STAGE 必须可由命令行 /D 覆盖（!ifndef 守卫），
    # 且兜底值必须是醒目的哨兵而不是像真的版本号（防双口径漂移）。
    nsi_text = open(nsi_path, encoding="utf-8-sig").read()
    check("!ifndef VERSION" in nsi_text and "!ifndef STAGE" in nsi_text,
          "installer.nsi 的 VERSION/STAGE 有 !ifndef 守卫（/D 注入可覆盖）")
    check('"0.0.0-dev"' in nsi_text,
          "VERSION 兜底是 0.0.0-dev 哨兵（忘注入时在『添加删除程序』里一眼可见）")

    # build_installer 命令组装：假 makensis + 假 subprocess，断言 argv。
    scratch = tempfile.mkdtemp(prefix="itest_c2a_")
    try:
        fake_nsis = os.path.join(scratch, "makensis.exe")
        open(fake_nsis, "w").close()
        stage = os.path.join(scratch, "mystage")
        os.makedirs(os.path.join(stage, "src"))
        # 装配目录里的 version.py 必须与注入版本一致（漂移守卫）
        with open(os.path.join(stage, "src", "version.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(f'APP_VERSION = "{ver}"\n')
        captured: dict = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            class R:
                returncode = 0
                stdout = "ok"
            return R()

        orig_run = make_dist.subprocess.run
        make_dist.subprocess.run = fake_run
        try:
            ok = make_dist.build_installer(stage, ROOT, nsis_exe=fake_nsis)
        finally:
            make_dist.subprocess.run = orig_run
        argv = captured.get("argv", [])
        check(ok is True, "build_installer 返回 True")
        check(any(a == f"/DVERSION={ver}" for a in argv),
              "argv 带 /DVERSION=（值来自 read_app_version，非硬编码）")
        check(any(a == f"/DSTAGE={os.path.abspath(stage)}" for a in argv),
              "argv 带 /DSTAGE=（指向被装配的输出目录，绝对路径）")
        check(any(a.endswith("installer.nsi") for a in argv),
              "argv 最后一项是 installer.nsi 脚本")

        # makensis 缺失 → 明确失败而不是静默跳过
        ok2 = make_dist.build_installer(stage, ROOT,
                                        nsis_exe=os.path.join(scratch, "nope.exe"))
        check(ok2 is False, "makensis 不存在时返回 False（不静默跳过）")

        # 漂移守卫：装配目录的 src/version.py 与注入版本不一致 → 拒绝出包
        bad_stage = os.path.join(scratch, "oldstage")
        os.makedirs(os.path.join(bad_stage, "src"))
        with open(os.path.join(bad_stage, "src", "version.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('APP_VERSION = "0.0.1"\n')
        captured.clear()
        ok3 = make_dist.build_installer(bad_stage, ROOT, nsis_exe=fake_nsis)
        check(ok3 is False, "装配目录版本与注入版本不一致时拒绝出包")
        check("argv" not in captured,
              "版本漂移时 makensis 根本没被调用（先挡后跑）")
        # 装配目录缺 src/version.py（不是装配产物）→ 同样拒绝
        empty_stage = os.path.join(scratch, "emptystage")
        os.makedirs(empty_stage)
        ok4 = make_dist.build_installer(empty_stage, ROOT, nsis_exe=fake_nsis)
        check(ok4 is False, "装配目录缺 src/version.py 时拒绝出包")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if _fails:
        print(f"❌ {len(_fails)} 项未通过：")
        for m in _fails:
            print("   - " + m)
        return 1
    print("✅ 依赖分级安装测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
