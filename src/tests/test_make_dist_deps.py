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
        with open(os.path.join(pd, "python.exe"), "w", encoding="utf-8"):
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
        with open(os.path.join(wh9, "demo-1.0-py3-none-any.whl"), "w", encoding="utf-8"):
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
        open(os.path.join(sp, "pywin32.pth"), "w", encoding="utf-8").write("import sys; sys.path.append('x')\n")
        check(make_dist.scan_stale_pth(probe7) == [],
              "白名单 pywin32.pth 不报警")
        # 7.3 新装 .pth → 报出且有序
        open(os.path.join(sp, "foo.pth"), "w", encoding="utf-8").write("some\\path\n")
        open(os.path.join(sp, "__editable__.demo-0.1.pth"), "w", encoding="utf-8").write("demo\n")
        check(make_dist.scan_stale_pth(probe7)
              == ["__editable__.demo-0.1.pth", "foo.pth"],
              "新装 .pth 全部报出且有序")
        # 7.4 大小写不敏感
        open(os.path.join(sp, "BAR.PTH"), "w", encoding="utf-8").write("y\n")
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
        open(os.path.join(probe8, "python", "python.exe"), "w", encoding="utf-8").close()
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
                open(os.path.join(dest[0], "srt-3.5.3.tar.gz"), "w", encoding="utf-8").close()
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
        open(fake_nsis, "w", encoding="utf-8").close()
        # 用独立假根当 root：build_installer 的产物路径是 root/dist/…，若拿真仓库
        # 当 root，假 subprocess 就得往 D:\Denia\dist 里写一个 0 字节、名字与真
        # 安装包一模一样的假 exe——那东西是能被当成成品发出去的雷。假根里出假包。
        c2a_root = os.path.join(scratch, "c2aroot")
        os.makedirs(os.path.join(c2a_root, "src"))
        with open(os.path.join(c2a_root, "src", "version.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(f'APP_VERSION = "{ver}"\n')
        with open(os.path.join(c2a_root, "main.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("import os\n")
        stage = os.path.join(scratch, "mystage")
        os.makedirs(os.path.join(stage, "src"))
        # 装配目录里的 version.py 必须与注入版本一致（漂移守卫）
        with open(os.path.join(stage, "src", "version.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(f'APP_VERSION = "{ver}"\n')
        # 【C9-5】装配时 make_dist 会写 .src_fingerprint；这里模拟装配产物
        with open(os.path.join(stage, ".src_fingerprint"), "w",
                  encoding="utf-8") as fh:
            fh.write(make_dist.src_fingerprint(c2a_root) + "\n")
        captured: dict = {}

        def _mk_result(rc, out):
            class R:
                returncode = rc
                stdout = out
            return R()

        def fake_run(argv, **kw):
            captured["argv"] = argv
            # 真 makensis 会按 OutFile 落盘，build_installer 收尾要 stat 它。
            # 早先这里不产出文件，测试却一直是绿的——因为 D:\Denia\dist 里躺着
            # 上一次真实出包留下的同名 exe。清场删掉 dist/ 后立刻转红，是假绿。
            v = next(a for a in argv if a.startswith("/DVERSION=")).split("=", 1)[1]
            out_exe = os.path.join(c2a_root, "dist",
                                   f"达妮娅AI桌面伴侣_Setup_v{v}.exe")
            if not os.path.isdir(os.path.dirname(out_exe)):
                # 关键：这里绝不替调用方建目录。真 makensis 在 OutFile 的目录
                # 不存在时就是报错退出（rc!=0），不会帮你 makedirs——假实现要是
                # 顺手建了，下面的「自己建 dist/」就永远测不出来。
                return _mk_result(1, "error opening file for writing")
            open(out_exe, "w", encoding="utf-8").close()
            return _mk_result(0, "ok")

        orig_run = make_dist.subprocess.run
        make_dist.subprocess.run = fake_run
        try:
            ok = make_dist.build_installer(stage, c2a_root, nsis_exe=fake_nsis)
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
        # 清场后新克隆的仓库没有 dist/：build_installer 必须自己建，否则真
        # makensis 写 OutFile 时直接失败（2026-10-19 清场时暴露的现场 bug）。
        check(os.path.isdir(os.path.join(c2a_root, "dist")),
              "root 没有 dist/ 时 build_installer 自己建（新克隆首包不炸）")

        # makensis 缺失 → 明确失败而不是静默跳过
        ok2 = make_dist.build_installer(stage, c2a_root,
                                        nsis_exe=os.path.join(scratch, "nope.exe"))
        check(ok2 is False, "makensis 不存在时返回 False（不静默跳过）")

        # 漂移守卫：装配目录的 src/version.py 与注入版本不一致 → 拒绝出包
        bad_stage = os.path.join(scratch, "oldstage")
        os.makedirs(os.path.join(bad_stage, "src"))
        with open(os.path.join(bad_stage, "src", "version.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('APP_VERSION = "0.0.1"\n')
        captured.clear()
        ok3 = make_dist.build_installer(bad_stage, c2a_root, nsis_exe=fake_nsis)
        check(ok3 is False, "装配目录版本与注入版本不一致时拒绝出包")
        check("argv" not in captured,
              "版本漂移时 makensis 根本没被调用（先挡后跑）")
        # 装配目录缺 src/version.py（不是装配产物）→ 同样拒绝
        empty_stage = os.path.join(scratch, "emptystage")
        os.makedirs(empty_stage)
        ok4 = make_dist.build_installer(empty_stage, c2a_root, nsis_exe=fake_nsis)
        check(ok4 is False, "装配目录缺 src/version.py 时拒绝出包")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    print("[22] `upto=\"optional\"` 必须真的装可选项（质检第 7 条遗留的修复）")
    # 真事故：install_deps 里 optional 档的门写的是 `upto != "all"`，
    # 于是按参数名传 "optional" 的人拿到的是 "important" 的行为——
    # 不装可选项、也不报错。参数名骗人，代价由打包现场付。
    out22 = _mk_out(os.path.join(tmp, "o22"))
    seen22: list[list[str]] = []

    def _pip22(py, args):
        seen22.append([a for a in args if ">=" in a])
        return (0, [])

    make_dist._pip = _pip22
    make_dist.install_deps(out22, upto="optional", verify=False)
    flat22 = [x for specs in seen22 for x in specs]
    check(any(x.startswith("opencv-python") for x in flat22),
          "upto=optional 装上了可选项")
    check(all(x in flat22 for x in ("PyYAML>=6.0", "PyAudio>=0.2.14")),
          "upto=optional 仍包含更低档（别变成只装可选）")

    print("[23] all 与 optional 是同一档（别名，不是两个档位）")
    s_all = make_dist.tier_specs(tiers, "all")
    s_opt = make_dist.tier_specs(tiers, "optional")
    check(s_all == s_opt, "tier_specs('all') == tier_specs('optional')")
    check(make_dist.normalize_tier("all") == make_dist.normalize_tier("optional"),
          "normalize_tier 把两者收敛到同一个真名")

    print("[24] 拼错档位必须抛异常，不能静默按 important 处理")
    # 这条是 [22] 的护栏：只修 "optional 能装" 不够，下次拼成 "optinal"
    # 又会静默变成"不装可选项"。所以未知值要当场炸。
    for bad in ("optinal", "IMPORTANT!", "", "core!", "corecore"):
        try:
            make_dist.normalize_tier(bad)
            check(False, f"{bad!r} 应当抛 ValueError")
        except ValueError:
            check(True, f"{bad!r} → ValueError")
    check(make_dist.normalize_tier(" CORE ") == "core", "大小写与空白被容忍")
    # 空白是与容忍，不是与拼错同罪：带空格的合法值要被收进来
    check(make_dist.normalize_tier("optional ") == "optional",
          "尾随空白的合法值照样接受（先 strip 再判断）")
    # install_deps 会先校验再动手：拼错时一个 pip 都不该跑
    out24 = _mk_out(os.path.join(tmp, "o24"))
    calls24: list[list[str]] = []
    make_dist._pip = lambda py, args: (calls24.append(args), (0, ""))[1]
    try:
        make_dist.install_deps(out24, upto="optinal", verify=False)
        check(False, "install_deps 收到未知档位应当抛 ValueError")
    except ValueError:
        check(True, "install_deps 未知档位 → ValueError")
    check(not calls24, "校验失败时一次 pip 都没跑（校验在动手之前）")

    print("[25] CLI 的 --tier 里必须能选到 optional")
    src_txt = open(os.path.join(ROOT, "tools", "make_dist.py"),
                   encoding="utf-8").read()
    hit = [ln for ln in src_txt.splitlines() if '"--tier"' in ln]
    check(bool(hit) and '"optional"' in hit[0],
          f"--tier 的 choices 含 optional（否则用户根本传不进来）：{hit[0].strip()[:60]}")

    print()
    print("== [C9-5] 装配源码指纹（版本没 bump 但 src 过期也拦得住） ==")
    # 自包含脚手架（C2-A 段的 scratch 已被 finally 清掉，不复用）。
    scratch5 = tempfile.mkdtemp(prefix="itest_c95_")
    fake_nsis5 = os.path.join(scratch5, "makensis.exe")
    open(fake_nsis5, "w", encoding="utf-8").close()

    def _fake_run5(argv, **kw):
        captured5["argv"] = argv
        # 模拟 NSIS 产物：build_installer 收尾会检查
        # root/dist/达妮娅AI桌面伴侣_Setup_v{version}.exe 存在
        version_arg = next(a for a in argv if a.startswith("/DVERSION="))
        v = version_arg.split("=", 1)[1]
        script_path = argv[-1]
        out_exe = os.path.join(os.path.dirname(os.path.dirname(script_path)),
                               "dist", f"达妮娅AI桌面伴侣_Setup_v{v}.exe")
        os.makedirs(os.path.dirname(out_exe), exist_ok=True)
        open(out_exe, "w", encoding="utf-8").close()
        class _R:
            returncode = 0
            stdout = "ok"
        return _R()

    captured5: dict = {}
    orig_run5 = make_dist.subprocess.run
    make_dist.subprocess.run = _fake_run5
    try:
        # 指纹定义：root 下 src/**/*.py + main.py 的（相对路径+内容）聚合 sha256。
        fake_root = os.path.join(scratch5, "fakeroot")
        os.makedirs(os.path.join(fake_root, "src", "sub"))
        a_py = os.path.join(fake_root, "src", "a.py")
        b_py = os.path.join(fake_root, "src", "sub", "b.py")
        with open(a_py, "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")
        with open(b_py, "w", encoding="utf-8") as fh:
            fh.write("Y = 2\n")
        with open(os.path.join(fake_root, "main.py"), "w", encoding="utf-8") as fh:
            fh.write("import os\n")
        fp1 = make_dist.src_fingerprint(fake_root)
        check(len(fp1) == 64 and all(c in "0123456789abcdef" for c in fp1),
              "指纹是 64 位 hex（sha256 聚合）")
        check(fp1 == make_dist.src_fingerprint(fake_root), "同内容同指纹（稳定）")
        with open(a_py, "w", encoding="utf-8") as fh:
            fh.write("X = 2\n")
        check(fp1 != make_dist.src_fingerprint(fake_root), "改一个文件指纹就变（敏感）")

        # build_installer 的指纹闸：装配目录存档 vs 当前 root 重算。
        fp_stage = os.path.join(scratch5, "fpstage")
        os.makedirs(os.path.join(fp_stage, "src"))
        with open(os.path.join(fp_stage, "src", "version.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(f'APP_VERSION = "{ver}"\n')
        # 旧装配（无指纹存档）→ 拒绝
        ok5 = make_dist.build_installer(fp_stage, fake_root, nsis_exe=fake_nsis5,
                                        version=ver)
        check(ok5 is False, "装配目录无 .src_fingerprint 存档时拒绝出包（旧装配必须重装配）")
        # 有存档且一致 → 出包
        with open(os.path.join(fp_stage, ".src_fingerprint"), "w",
                  encoding="utf-8") as fh:
            fh.write(make_dist.src_fingerprint(fake_root) + "\n")
        captured5.clear()
        ok6 = make_dist.build_installer(fp_stage, fake_root, nsis_exe=fake_nsis5,
                                        version=ver)
        check(ok6 is True and bool(captured5.get("argv")), "指纹一致时正常出包")
        # 装配后源码被改 → 拒绝且 makensis 不启动
        with open(b_py, "w", encoding="utf-8") as fh:
            fh.write("Y = 3\n")
        captured5.clear()
        ok7 = make_dist.build_installer(fp_stage, fake_root, nsis_exe=fake_nsis5,
                                        version=ver)
        check(ok7 is False and "argv" not in captured5,
              "装配后 src 被改 → 拒绝且 makensis 未被调用")
    finally:
        make_dist.subprocess.run = orig_run5
        shutil.rmtree(scratch5, ignore_errors=True)

    print()
    print("== [C10] 代码签名管线（凭证三选一 + 签后核验） ==")

    # --- resolve_sign_config：凭证解析 ---
    print("[C10-1] 不配任何凭证 → None（签名默认关闭，不报错）")
    check(make_dist.resolve_sign_config({}) is None, "空环境变量 → None")

    print("[C10-2] 同时配多个选择器 → 拒绝（用哪张证书说不清）")
    try:
        make_dist.resolve_sign_config({"DANIA_SIGN_SUBJECT": "A",
                                       "DANIA_SIGN_PFX": "b.pfx"})
        check(False, "双选择器应当抛 ValueError")
    except ValueError:
        check(True, "双选择器 → ValueError")

    print("[C10-3] subject 模式：凭证进 cfg，工具可用 DANIA_SIGN_TOOL 显式指路")
    fake_tool = os.path.join(tmp, "signtool.exe")
    open(fake_tool, "w", encoding="utf-8").close()
    cfg = make_dist.resolve_sign_config({"DANIA_SIGN_SUBJECT": "DaniaAI Test Signer",
                                         "DANIA_SIGN_TOOL": fake_tool})
    check(cfg is not None and cfg.get("subject") == "DaniaAI Test Signer",
          f"subject 进 cfg：{cfg}")
    check(cfg and cfg.get("tool") == fake_tool, "DANIA_SIGN_TOOL 优先于自动查找")

    print("[C10-4] thumbprint 模式：40 位 hex 才收，脏值拒绝")
    try:
        make_dist.resolve_sign_config({"DANIA_SIGN_THUMBPRINT": "zz09",
                                       "DANIA_SIGN_TOOL": fake_tool})
        check(False, "脏指纹应当抛 ValueError")
    except ValueError:
        check(True, "脏指纹 → ValueError")
    ok_thumb = "a" * 40
    cfg = make_dist.resolve_sign_config({"DANIA_SIGN_THUMBPRINT": ok_thumb.upper(),
                                         "DANIA_SIGN_TOOL": fake_tool})
    check(cfg is not None and cfg.get("thumbprint") == ok_thumb.upper(),
          "40 位大写 hex 原样收进 cfg")

    print("[C10-5] pfx 模式：文件必须真实存在，口令可缺省")
    try:
        make_dist.resolve_sign_config({"DANIA_SIGN_PFX": os.path.join(tmp, "no.pfx"),
                                       "DANIA_SIGN_TOOL": fake_tool})
        check(False, "不存在的 PFX 应当抛 ValueError")
    except ValueError:
        check(True, "PFX 不存在 → ValueError")
    real_pfx = os.path.join(tmp, "x.pfx")
    open(real_pfx, "w", encoding="utf-8").close()
    cfg = make_dist.resolve_sign_config({"DANIA_SIGN_PFX": real_pfx,
                                         "DANIA_SIGN_TOOL": fake_tool})
    check(cfg is not None and cfg.get("pfx") == real_pfx and cfg.get("pass") == "",
          "PFX 收进 cfg 且口令缺省为空串")

    print("[C10-6] 配了凭证但找不到 signtool → 在解析期就挡住，不等到签名")
    try:
        make_dist.resolve_sign_config({"DANIA_SIGN_SUBJECT": "X"})
        check(not os.path.isfile("signtool.exe"),
              "无 TOOL 且 PATH 无 signtool 时应抛 ValueError（本机没有裸 signtool.exe）")
    except ValueError:
        check(True, "凭证配了但没工具 → ValueError")

    # --- find_signtool：本机真身探测（存在则必须是个真文件） ---
    st = make_dist.find_signtool()
    check(st is None or os.path.isfile(st), f"find_signtool 结果自洽：{st}")

    # --- sign_file：签 + 验全链（打桩 runner，四路分支） ---
    print("[C10-7] 签成 + 验过 → True，argv 带 /fd SHA256 且凭证参数正确")
    calls: list[list[str]] = []

    def _ok_runner(argv, **kw):
        calls.append(list(argv))
        if "sign" in argv:
            return _FakeResult(0, "Successfully signed")
        return _FakeResult(0, "Successfully verified")

    exe7 = os.path.join(tmp, "probe7.exe")
    open(exe7, "w", encoding="utf-8").close()
    check(make_dist.sign_file(exe7, {"tool": fake_tool, "subject": "S"},
                              runner=_ok_runner) is True,
          "签成+验过 → True")
    check(len(calls) == 2 and calls[0][1] == "sign" and
          "/fd" in calls[0] and "SHA256" in calls[0] and "/n" in calls[0],
          f"sign argv 形态正确：{calls[0] if calls else '无'}")
    check(calls[1][1] == "verify" and "/pa" in calls[1], "核验走 verify /pa")

    print("[C10-8] pfx 凭证映射成 /f /p")
    calls.clear()
    make_dist.sign_file(exe7, {"tool": fake_tool, "pfx": real_pfx, "pass": "pw"},
                        runner=_ok_runner)
    check("/f" in calls[0] and real_pfx in calls[0] and "/p" in calls[0]
          and "pw" in calls[0], f"pfx argv：{calls[0] if calls else '无'}")

    print("[C10-9] thumbprint 凭证映射成 /sha1")
    calls.clear()
    make_dist.sign_file(exe7, {"tool": fake_tool, "thumbprint": "A" * 40},
                        runner=_ok_runner)
    check("/sha1" in calls[0] and "A" * 40 in calls[0],
          f"thumbprint argv：{calls[0] if calls else '无'}")

    print("[C10-10] sign 返回非零 → False（没签上不算成功）")
    def _sign_fail(argv, **kw):
        if "sign" in argv:
            return _FakeResult(2, "SignTool Error")
        return _FakeResult(0, "")
    check(make_dist.sign_file(exe7, {"tool": fake_tool, "subject": "S"},
                              runner=_sign_fail) is False,
          "签名失败 → False")

    print("[C10-11] verify 报根不受信 → 自签预期，降级为警告仍 True")
    def _untrusted(argv, **kw):
        if "sign" in argv:
            return _FakeResult(0, "ok")
        return _FakeResult(1, "SignTool Error: A certificate chain processed, "
                              "but terminated in a root certificate which is "
                              "not trusted by the trust provider.")
    check(make_dist.sign_file(exe7, {"tool": fake_tool, "subject": "S"},
                              runner=_untrusted) is True,
          "自签不受信链 → 警告但 True")

    print("[C10-12] verify 报其它错（如文件被改）→ False")
    def _verify_hard_fail(argv, **kw):
        if "sign" in argv:
            return _FakeResult(0, "ok")
        return _FakeResult(1, "SignTool Error: The signature is invalid.")
    check(make_dist.sign_file(exe7, {"tool": fake_tool, "subject": "S"},
                              runner=_verify_hard_fail) is False,
          "verify 硬失败 → False")

    print("[C10-13] runner 抛 OSError（工具起不来）→ False 不炸")
    def _boom(argv, **kw):
        raise OSError("no such tool")
    check(make_dist.sign_file(exe7, {"tool": fake_tool, "subject": "S"},
                              runner=_boom) is False, "OSError → False")

    print("[C10-14] CLI 口径：--sign 必须连着 --installer")
    src_txt10 = open(os.path.join(ROOT, "tools", "make_dist.py"),
                     encoding="utf-8").read()
    check('"--sign"' in src_txt10 and "--sign 需要 --installer" in src_txt10,
          "--sign 无包可签时硬失败")

    # ==================== C11：桌宠 Electron 壳进包 ====================
    print("== [C11] 桌宠壳装配（缺它装机回退白底 pywebview 桌宠） ==")
    scratch11 = tempfile.mkdtemp(prefix="md_c11_")
    try:
        def _mk(rel: str, content: bytes = b"x") -> str:
            p = os.path.join(scratch11, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(content)
            return p

        print("[C11-1] 完整源（壳三件 + electron dist）→ 全拷贝，locales 只留三语")
        _mk("shell/main.js", b"// shell main")
        _mk("shell/preload.js", b"// preload")
        _mk("shell/package.json", b"{}")
        _mk("shell/node_modules/electron/dist/electron.exe", b"MZ")
        _mk("shell/node_modules/electron/dist/ffmpeg.dll", b"dll")
        _mk("shell/node_modules/electron/dist/locales/zh-CN.pak", b"zh")
        _mk("shell/node_modules/electron/dist/locales/fr.pak", b"fr")
        _mk("shell/node_modules/electron/dist/locales/en-US.pak", b"en")
        out11 = os.path.join(scratch11, "out1")
        os.makedirs(out11)
        ok = make_dist.copy_shell(scratch11, out11)
        check(ok is True, "copy_shell 返回 True")
        check(os.path.isfile(os.path.join(out11, "shell", "main.js")),
              "壳脚本 main.js 进包")
        check(os.path.isfile(os.path.join(out11, "shell", "node_modules",
                                         "electron", "dist", "electron.exe")),
              "electron.exe 进包")
        loc = os.path.join(out11, "shell", "node_modules", "electron", "dist",
                           "locales")
        check(os.path.isfile(os.path.join(loc, "zh-CN.pak"))
              and os.path.isfile(os.path.join(loc, "en-US.pak")),
              "zh-CN/en-US 语言包保留")
        check(not os.path.exists(os.path.join(loc, "fr.pak")),
              "无关语言包已裁（省 ~46MB）")
        check(os.path.isfile(os.path.join(out11, "shell", "node_modules",
                                         "electron", "dist", "ffmpeg.dll")),
              "dist 其余文件全量保留（缺 dll 壳起不来）")

        print("[C11-2] 无 shell/ 源 → False 且不炸（纯后端场景仍能出包）")
        scratch11b = tempfile.mkdtemp(prefix="md_c11b_")
        out11b = os.path.join(scratch11b, "out")
        os.makedirs(out11b)
        check(make_dist.copy_shell(scratch11b, out11b) is False,
              "无 shell/ 返回 False")

        print("[C11-3] dist 不完整（缺 electron.exe）→ False（残壳不如无壳）")
        scratch11c = tempfile.mkdtemp(prefix="md_c11c_")
        _mk(os.path.join(scratch11c, "shell", "main.js"))
        _mk(os.path.join(scratch11c, "shell", "node_modules", "electron",
                         "dist", "ffmpeg.dll"))
        out11c = os.path.join(scratch11c, "out")
        os.makedirs(out11c)
        check(make_dist.copy_shell(scratch11c, out11c) is False,
              "缺 electron.exe 返回 False")

        print("[C11-4] 漂移闸：源有 shell/main.js 而包里没有 → 闸门点名缺壳")
        import contextlib
        import io as _io
        out11d = os.path.join(scratch11, "out4")
        os.makedirs(out11d)
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_gates, _ = make_dist.run_gates(out11d, root=scratch11)
        # scratch 根本来就没配齐 REQUIRED，rc!=0 不说明问题——必须点名 shell/main.js
        check("shell/main.js（源里有的桌宠壳没进包）" in buf.getvalue(),
              "缺壳装配被闸门点名（不是靠别的缺件蒙混 rc!=0）")

        print("[C11-5] 指纹含壳脚本：改 shell/main.js 一字节 → 指纹变")
        fp1 = make_dist.src_fingerprint(scratch11)
        _mk("shell/main.js", b"// shell main v2")
        fp2 = make_dist.src_fingerprint(scratch11)
        check(fp1 != fp2, "壳脚本改动进指纹（出包闸可拦旧包）")
    finally:
        shutil.rmtree(scratch11, ignore_errors=True)
        shutil.rmtree(scratch11b, ignore_errors=True)
        shutil.rmtree(scratch11c, ignore_errors=True)

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
