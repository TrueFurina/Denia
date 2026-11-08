"""`tools/doctor.py` 的测试。

只测**与环境无关**的部分：解析、端口收集、结果结构。
真正的环境扫描（依赖装没装、Electron 在不在）本来就该随机器的不同输出不同结果，
把它当断言只会得到一条会随机变红的门禁。

运行：.venv/Scripts/python.exe src/tests/test_doctor.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import doctor  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _write_tmp(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main() -> int:
    tmp = os.path.join(ROOT, "tools", "_doctor_tmp_req.txt")
    try:
        print("[1] requirements 分节解析")
        _write_tmp(tmp, "\n".join([
            "# 核心（缺一不可）",
            "PyYAML>=6.0",
            "requests>=2.31",
            "",
            "# 重要",
            "ollama>=0.4.0",
            "",
            "# 可选",
            "gradio_client>=1.0",
            "",
        ]))
        secs = doctor._parse_requirements(tmp)
        check("核心" in secs, "解析出「核心」段")
        core = [_n for _n in secs.get("核心", [])]
        check(core == ["PyYAML", "requests"], f"核心两项且无版本约束：{core}")
        check(secs.get("重要") == ["ollama"], f"重要段：{secs.get('重要')}")
        check(secs.get("可选") == ["gradio_client"], f"可选段：{secs.get('可选')}")
        check(doctor._parse_requirements(
            os.path.join(ROOT, "不存在的文件.txt")) == {},
            "文件不存在 → 空 dict，不抛异常")

        print("[2] 端口收集（从配置 raw 里捞，不写死端口）")
        cfg = type("C", (), {})()
        cfg.raw = {"websocket": {"port": 8765},
                   "llm": {"api_port": 9000},
                   "tts": {"port": 5000},
                   "nested": [{"port": 7777}]}
        got = doctor._collect_ports(cfg)
        check(8765 in got and 9000 in got and 5000 in got,
              f"顶层与嵌套端口都被收集：{got}")
        check(got == sorted(got), "结果去重且有序")
        check(7777 in got, f"列表里的 port 也能收集到（{got}）")

        print("[3] run_all 结构（与环境无关的部分）")
        rows = doctor.run_all("config.yaml")
        check(isinstance(rows, list) and len(rows) >= 6,
              f"返回 {len(rows)} 行")
        levels = {r[0] for r in rows}
        check(levels <= {doctor.PASS, doctor.WARN, doctor.FAIL},
              f"等级合法：{levels}")
        check(all(isinstance(r[1], str) and r[1] for r in rows),
              "每项都有名字")
        check(all(isinstance(r[2], str) for r in rows), "每项都有明细")
        check(all(isinstance(r[3], str) for r in rows), "每项都有提示字段")
        # 【2026-10-24】断言要"摘错在哪"：跑闸负载下这一条间歇红过三次，而旧断言
        # 只说"不应有 FAIL"不说是哪项 → 每次都得重跑半小时才能定位。现在把 FAIL 行
        # 直接写进消息里（下次红了一行定位）。
        # 【2026-11-08】这里**不能**叫 _fails：本函数里一旦给 _fails 赋值，Python 作用域
        # 规则会让整个 main() 里的 _fails 都变成局部变量，而 check() 追加的是模块级那份。
        # 后果是「只要 doctor 自己没报 FAIL，别处任何断言失败都不进计数，退出码仍是 0」——
        # 门禁假绿（变异刀 B 让 UCRT 探测恒真时实测：2 条 ✗ 之后照样 PASS、exit 0）。
        fail_rows = [r for r in rows if r[0] == doctor.FAIL]
        check(not fail_rows,
              "当前开发机不应有 FAIL（否则说明 doctor 自检查出真问题）"
              + ("；实际 FAIL: " + " | ".join(
                  "%s：%s" % (r[1], (r[2] or "")[:100]) for r in fail_rows) if fail_rows else ""))

        print("[4] 桌宠状态：握手文件残留要报 WARN（那是令牌，不该留在盘上）")
        try:
            from src.ui.pet_shell import HANDSHAKE_PATH
        except Exception:  # noqa: BLE001
            HANDSHAKE_PATH = os.path.join(ROOT, "data", "pet_shell_handshake.json")
        existed = os.path.exists(HANDSHAKE_PATH)
        if existed:
            os.replace(HANDSHAKE_PATH, HANDSHAKE_PATH + ".bak")
        try:
            lvl_a, _, _, _ = doctor.check_pet_state()
            check(lvl_a != doctor.FAIL, f"无残留时不报 FAIL（{lvl_a}）")
            with open(HANDSHAKE_PATH, "w", encoding="utf-8") as fh:
                fh.write('{"token":"secret"}')
            lvl_b, item, detail, hint = doctor.check_pet_state()
            check(lvl_b == doctor.WARN, f"有残留时报 WARN（实际 {lvl_b}）")
            check("握手" in detail, f"明细点明是握手文件：{detail}")
            check(bool(hint), "给出可执行的修复提示")
            check(item == "桌宠状态", f"检查项名：{item}")
        finally:
            # 清理是"尽力而为"：这台机器上 os.remove 可能被安全删除钩子接管，
            # 文件已不在时抛的是 OSError 而不是 FileNotFoundError，别让它把用例带崩。
            try:
                if os.path.exists(HANDSHAKE_PATH):
                    os.remove(HANDSHAKE_PATH)
                if existed and os.path.exists(HANDSHAKE_PATH + ".bak"):
                    os.replace(HANDSHAKE_PATH + ".bak", HANDSHAKE_PATH)
            except OSError as _e:  # noqa: BLE001
                print(f"  （清理握手文件失败，忽略：{_e}）")
        print("[5] 版本一致：配置 app.version 与 src/version.py 漂移要报 FAIL")
        try:
            from src.version import APP_VERSION as _V
        except Exception as _e:  # noqa: BLE001
            check(False, f"读不到 src/version.py 的 APP_VERSION：{_e}")
            _V = None
        if _V is not None:
            def _cfg_with(version):
                c = type("C", (), {})()
                c.ns = type("N", (), {})()
                c.ns.app = type("A", (), {"version": version})()
                return c

            lvl, _it, detail, hint = doctor.check_version(_cfg_with(str(_V)))
            check(lvl == doctor.PASS, f"一致时报 PASS（实际 {lvl}：{detail}）")
            check(hint == "", "一致时不需要修复提示")

            lvl2, _i2, detail2, hint2 = doctor.check_version(_cfg_with("9.9.9"))
            check(lvl2 == doctor.FAIL, f"漂移时报 FAIL（实际 {lvl2}）")
            check("漂移" in detail2, f"明细点明是漂移：{detail2}")
            check(str(_V) in hint2, f"提示里给出应改成的版本号：{hint2}")

            lvl3, _i3, _d3, hint3 = doctor.check_version(_cfg_with(None))
            check(lvl3 == doctor.WARN, f"缺字段时报 WARN（实际 {lvl3}）")
            check(bool(hint3), "缺字段也给出修复提示")

            # 回归（2026-10-18 质检 P1）：空串等同于"没配"。
            # 以前 apply_defaults 会补 ""，这条分支永远走不到，
            # 于是"没写版本号"被报成"版本漂移"的 FAIL。
            lvl4, _i4, detail4, _h4 = doctor.check_version(_cfg_with(""))
            check(lvl4 == doctor.WARN, f"空串按「没配」处理（实际 {lvl4}：{detail4}）")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("[N] 依赖 cfg 的检查项：cfg 为 None 时也要被调用，不能退化成无参")
    # 真实回归：run_all 原先按「参数值是否为 None」决定传不传参，
    # 配置加载失败（cfg=None）时 check_version(cfg) 就退化成 check_version()
    # → TypeError → 报成「doctor 内部异常」，把真正的配置问题盖掉。
    check(doctor._takes_arg(doctor.check_version), "check_version 被识别为「需要参数」")
    check(doctor._takes_arg(doctor.check_ports), "check_ports 被识别为「需要参数」")
    check(not doctor._takes_arg(doctor.check_disk), "check_disk 被识别为「无参」")
    check(not doctor._takes_arg(doctor.check_runtime), "check_runtime 被识别为「无参」")

    # 必须让 load_config 真的抛，cfg 才会是 None。
    # 注意：**文件不存在并不会抛**（会回落默认配置），所以不能拿不存在的路径来测 ——
    # 那样测出来的 cfg 不是 None，这条用例就永远绿，等于没测。
    # 坏 YAML 落在系统临时目录（理由同 [9]：被 SIGTERM 打断时 finally 不执行，
    # 落在仓库根目录就会留下垃圾）。
    import tempfile as _tf0
    broken = os.path.join(_tf0.mkdtemp(prefix="doctor_badcfg_"), "_broken_cfg_for_test.yaml")
    try:
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("app: [unclosed\n  bad: : :\n")
        _probe = None
        try:
            from src.config import load_config as _lc

            _lc(broken)
        except Exception as _e:  # noqa: BLE001
            _probe = _e
        check(_probe is not None, f"坏 YAML 确实会让 load_config 抛（{_probe}）")

        rows = doctor.run_all(broken)
        internals = [r for r in rows if "自身异常" in r[2]]
        check(not internals,
              f"配置加载失败时不该冒出内部异常（实际 {[r[:3] for r in internals]}）")
        check(any(r[1] == "配置文件" for r in rows),
              "至少有一条「配置文件」的 FAIL 说明真实原因")
        # 【2026-10-20】源码包冒烟时发现 --doctor 里出现两条同名的「配置文件」：
        # run_all 自己加载失败报一条，check_config 又报一条，队友看到的第一反应
        # 是"到底哪个是真的"。现在只留 check_config 那条（它分场景说得更细）。
        _cfg_rows = [r for r in rows if r[1] == "配置文件"]
        check(len(_cfg_rows) == 1,
              f"「配置文件」只报一条（实际 {len(_cfg_rows)} 条：{[r[2] for r in _cfg_rows]}）")
    finally:
        if os.path.exists(broken):
            os.remove(broken)

    print("[6b] 源码包首跑：缺 config.yaml 时要给出下一步动作")
    # 这是队友拿到包之后必然撞的第一件事（包里有模板、没有实配）。原提示是
    # "确认运行目录，或用 --config 指定路径"——等于让他自己猜。真正的动作只有
    # 一句：把模板复制成实配。断言的是"提示里有可照抄的命令"，不是"有提示"。
    import tempfile as _tf2

    pkg = _tf2.mkdtemp(prefix="doctor_cfg_")
    try:
        lvl, _name, msg, fix = doctor.check_config(os.path.join(pkg, "config.yaml"))
        check(lvl == doctor.FAIL, "缺配置判 FAIL")
        check("config.release.yaml" in fix and "copy" in fix.lower(),
              f"旁边没有模板时也给出模板名兜底（fix={fix}）")

        with open(os.path.join(pkg, "config.release.yaml"), "w", encoding="utf-8") as fh:
            fh.write("app:\n  version: 0.2.0\n")
        lvl, _name, msg, fix = doctor.check_config(os.path.join(pkg, "config.yaml"))
        check(lvl == doctor.FAIL, "有模板但没实配仍判 FAIL")
        check("config.release.yaml" in msg or "config.release.yaml" in fix,
              f"提示点名了旁边那份模板（msg={msg}）")
        check("copy config.release.yaml config.yaml" in fix,
              f"给出可直接照抄的复制命令（fix={fix}）")
    finally:
        import shutil as _sh2

        _sh2.rmtree(pkg, ignore_errors=True)

    print("[7] 包内运行时：依赖必须来自包内，不能借构建机")
    # 真事故（2026-10-18）：包内 python 是完整 Python 拷贝，会加载用户级
    # site-packages。构建机装着 rich/httpx/requests/websockets/dashscope，
    # 于是缺 5 个核心包的交付件在 --doctor 里拿到「9 PASS / 可发布」。
    # 只断言「能 import」永远抓不到它，必须看 __file__ 落在谁的目录下。
    import tempfile as _tf

    root = os.path.realpath(doctor.ROOT)
    inside = os.path.join(root, "python", "Lib", "site-packages", "rich", "__init__.py")
    outside = os.path.join(_tf.gettempdir(), "someone_else", "Python312",
                           "site-packages", "rich", "__init__.py")
    check(doctor._borrowed_deps({"rich": inside}) == [], "包内路径不算外借")
    bad = doctor._borrowed_deps({"rich": outside})
    check(len(bad) == 1 and bad[0].startswith("rich→"), f"包外路径被点名：{bad}")
    check(doctor._borrowed_deps({"rich": ""}) == [], "拿不到 __file__ 时不误报")
    check(doctor._borrowed_deps({}) == [], "空集不误报")
    check(doctor._running_from_bundle() is False,
          f"开发环境（{os.path.basename(sys.executable)}）不该被判成包内运行时")
    # 提示文案必须匹配场景：交付包里不存在 .venv，让用户去跑 pip 是指错路
    check(".venv" not in doctor._deps_missing_hint(True),
          f"包内缺依赖的提示不该出现 .venv：{doctor._deps_missing_hint(True)}")
    check(".venv" in doctor._deps_missing_hint(False),
          "开发环境仍给出 pip 命令")

    print("[8] 运行期隔离：交付包必须 isolated，否则换台机器行为不可预测")
    # 与 [7] 同源但更靠底层：[7] 只在「依赖恰好被借到」时报警，而决定换台机器
    # 还能不能跑的，是**运行模式**。交付包 python/ 是完整拷贝，standard 模式
    # 天然会读 %APPDATA%\Roaming\Python\...，今天没借到不代表明天不会借。
    _orig_sys = doctor.sys

    class _FakeFlags:
        def __init__(self, isolated):
            self.isolated = isolated

    class _FakeSys:
        def __init__(self, exe, isolated, path):
            self.executable = exe
            self.flags = _FakeFlags(isolated)
            self.path = path

        def __getattr__(self, k):
            return getattr(sys, k)

    try:
        bundle_exe = os.path.join(root, "python", "python.exe")
        inside = os.path.join(root, "python", "Lib", "site-packages")
        foreign = os.path.join(_tf.gettempdir(), "Roaming", "Python", "Python312",
                               "site-packages")

        # 8.1 isolated + 路径全在包内 → PASS
        doctor.sys = _FakeSys(bundle_exe, 1, [os.path.join(root, "python"), inside])
        lvl, _, detail, _ = doctor.check_runtime_isolation()
        check(lvl == doctor.PASS and "isolated" in detail,
              f"isolated 且路径干净应 PASS（实得 {lvl}：{detail}）")

        # 8.2 包内 + standard（上一轮的真实状态）→ 必须 FAIL
        # sys.path 故意给**纯净**的包内值：一旦混进包外路径，FAIL 就可能来自
        # 下面 8.3 那道判定，测到的就不是 isolated 这个变量了。
        # 第一次变异验证正是这么漏过去的（去掉 isolated 判定，8.2 照样绿）。
        doctor.sys = _FakeSys(bundle_exe, 0, [os.path.join(root, "python"), inside])
        lvl, _, detail, hint = doctor.check_runtime_isolation()
        check(lvl == doctor.FAIL, f"包内非 isolated 必须 FAIL（实得 {lvl}）")
        check("tools/make_dist.py" in hint, f"修复提示要指出重新打包这条命令：{hint}")

        # 8.3 isolated 了但 sys.path 混进包外路径 → 仍是 FAIL
        doctor.sys = _FakeSys(bundle_exe, 1, [os.path.join(root, "python"), foreign])
        lvl, _, detail, _ = doctor.check_runtime_isolation()
        check(lvl == doctor.FAIL, f"isolated 但有包外路径必须 FAIL（实得 {lvl}）")
        check(detail.strip() != "", "失败时 detail 不能为空")

        # 8.5 包根自身（`_pth` 里的 `..`）必须算包内
        # 真事故：第一次在交付包上跑 --doctor 就报了假 FAIL —— sys.path 里那条
        # `D:\Denia\dist_stage` 正好等于 ROOT，而旧判断只认 `ROOT\` 前缀。
        doctor.sys = _FakeSys(bundle_exe, 1, [root])
        lvl, _, detail, _ = doctor.check_runtime_isolation()
        check(lvl == doctor.PASS, f"包根自身必须算包内（实得 {lvl}：{detail}）")

        # 8.4 开发环境 → PASS（不适用），不许误报
        doctor.sys = _FakeSys(sys.executable, 0, [root, foreign])
        lvl, _, detail, _ = doctor.check_runtime_isolation()
        check(lvl == doctor.PASS and "不适用" in detail,
              f"开发环境不该报红（实得 {lvl}：{detail}）")
    finally:
        doctor.sys = _orig_sys

    print("[9] WebView2 / UCRT / 代码签名：干净机器上唯三会让程序打不开的因素")
    # 这三项不能在开发机上"实测通过"就算数——开发机装了 WebView2、系统自带 UCRT，
    # 而签名状态与是否签过证书无关。所以全部走注入：既能测 PASS 也能测 FAIL，
    # 不依赖跑测试的这台机器恰好是什么状态。
    # 【2026-10-21】这三个假文件以前直接建在 tools/ 下。测试正常跑完会删，但
    # **被 SIGTERM 打断就不会**（Windows 上 TerminateProcess 不走 finally）→ 仓库里
    # 留下 `tools/_doctor_tmp_sys32/ucrtbase.dll` 和 `_broken_cfg_for_test.yaml`，
    # 下次跑门禁时它们成了"来历不明的产物"。假实现的产物一律放系统临时目录：
    # 中断也只脏 %TEMP%，不脏仓库。
    import tempfile as _tf
    _tmp_base = _tf.mkdtemp(prefix="doctor_tmp_")
    _fake_empty = os.path.join(_tmp_base, "sys32")
    _pe_path = os.path.join(_tmp_base, "_doctor_tmp_pe.exe")
    _signed_pe = os.path.join(_tmp_base, "doctor_tmp_signed.exe")
    _orig_wv2_reg = doctor._wv2_registry_version
    _orig_wv2_exe = doctor._wv2_executable
    _orig_system32 = doctor._system32
    _orig_win_dll = doctor._win_dll

    class _FakeSysPlatform:
        def __init__(self, platform):
            self.platform = platform

        def __getattr__(self, k):
            return getattr(sys, k)

    try:
        # 9.1 注册表有版本 → PASS，且版本号必须出现在 detail 里
        doctor.sys = _FakeSysPlatform("win32")
        doctor._wv2_registry_version = lambda: "155.0.4283.60"
        doctor._wv2_executable = lambda: ""
        lvl, _, detail, _ = doctor.check_webview2()
        check(lvl == doctor.PASS and "155.0.4283.60" in detail,
              f"已装 WebView2 应 PASS 且带版本（实得 {lvl}：{detail}）")

        # 9.2 注册表空 + 目录找不到 → FAIL，且修复提示要给能直接点的地址
        doctor._wv2_registry_version = lambda: ""
        doctor._wv2_executable = lambda: ""
        lvl, _, detail, hint = doctor.check_webview2()
        check(lvl == doctor.FAIL, f"没装 WebView2 必须 FAIL（实得 {lvl}）")
        check("webview2" in hint.lower(), f"提示要给下载地址：{hint}")

        # 9.3 注册表空但找到固定版本 exe → PASS（Fixed Version 分发不写注册表）
        doctor._wv2_executable = lambda: r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\1.0\msedgewebview2.exe"
        lvl, _, detail, _ = doctor.check_webview2()
        check(lvl == doctor.PASS and "固定版本" in detail,
              f"固定版本分发应 PASS（实得 {lvl}：{detail}）")

        # 9.4 非 Windows → WARN，不能报 FAIL（否则 Linux 上跑自检永远红）
        doctor.sys = _FakeSysPlatform("linux")
        lvl, _, detail, _ = doctor.check_ucrt()
        check(lvl == doctor.WARN, f"非 Windows 上 UCRT 应 WARN（实得 {lvl}）")
        lvl2, _, _, _ = doctor.check_webview2()
        check(lvl2 == doctor.WARN, f"非 Windows 上 WebView2 应 WARN（实得 {lvl2}）")

        # 9.5 UCRT：判据是"能不能加载"，不是"文件在不在"。
        # 【2026-11-08 实测】Win10 19045 的 System32 里 api-ms-win-crt-*.dll 一个都没有
        # （计数 0），但 LoadLibrary("api-ms-win-crt-runtime-l1-1-0.dll") 成功、handle 与
        # ucrtbase.dll 相同（API Set 转发），numpy 2.5.4 正常 import。旧实现用 isfile 判，
        # 健康机器被判「缺 UCRT」，这条门禁因此在开发机上红（本仓 [9] 就是为这种假红写的）。
        # 注入点在**加载器**（_win_dll）而不是判据（_ucrt_loadable）：换掉判据等于绕开
        # 真实实现，变异刀砍在影子上（第一版就这么写的，刀 B 没抓住）。
        def _loader_failing_on(*bad):
            def _loader(name):
                if name in bad:
                    raise OSError("LoadLibrary failed: " + name)
                return object()
            return _loader

        _all_names = ("api-ms-win-crt-runtime-l1-1-0.dll", "ucrtbase.dll")
        doctor.sys = _FakeSysPlatform("win32")
        doctor._system32 = lambda: _fake_empty

        # 9.5a 两个名字都加载不了（Win7 无 KB2999226 的形态）→ FAIL + 补丁号
        doctor._win_dll = _loader_failing_on(*_all_names)
        lvl, _, detail, hint = doctor.check_ucrt()
        check(lvl == doctor.FAIL, f"缺 UCRT 必须 FAIL（实得 {lvl}）")
        check("ucrtbase.dll" in detail, f"要指出缺的是哪个文件：{detail}")
        check("KB2999226" in hint, f"修复提示要给出补丁号：{hint}")

        # 9.5b 只有 api-ms-win-crt-runtime 加载不了 → FAIL，且点名它
        doctor._win_dll = _loader_failing_on("api-ms-win-crt-runtime-l1-1-0.dll")
        lvl, _, detail, _ = doctor.check_ucrt()
        check(lvl == doctor.FAIL and "api-ms-win-crt-runtime" in detail,
              f"只缺 api-ms-win-crt-runtime 也要 FAIL（实得 {lvl}：{detail}）")

        # 9.5c 真机形态：System32 里没有那两个实体文件，但加载器能解析 → 必须 PASS。
        # 这条就是本机被误判的那一次：目录空、文件不在、numpy 却跑得好好的。
        os.makedirs(_fake_empty, exist_ok=True)
        doctor._win_dll = lambda name: object()
        lvl, _, detail, _ = doctor.check_ucrt()
        check(lvl == doctor.PASS and "API Set" in detail,
              f"文件不在但能加载 → 必须 PASS（实得 {lvl}：{detail}）")
    finally:
        doctor.sys = sys
        doctor._wv2_registry_version = _orig_wv2_reg
        doctor._wv2_executable = _orig_wv2_exe
        doctor._system32 = _orig_system32
        doctor._win_dll = _orig_win_dll

    print("[10] PE 签名块解析：偏移写错会读出假的「已签名」")
    # 真事故（2026-10-18）：第一版把数据目录起始偏移写反了（PE32 该 +0x60、
    # PE32+ 该 +0x70，我写成 +0x70/+0x80），于是读到的是 **Debug 目录**。
    # electron.exe 恰好返回 28 字节——正好是一个调试目录条目的大小——看上去
    # "已签名"。用 PowerShell Get-AuthenticodeSignature 交叉校验才发现。
    # 这条测试用**合成 PE**：只在正确偏移写签名块，其余全零，偏移写错必红。
    def _make_pe(path, magic, sec_va=0x400, sec_size=16, total=2048):
        import struct as _st

        data = bytearray(total)
        data[0:2] = b"MZ"
        _st.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        fh = 0x84
        opt = fh + 20
        _st.pack_into("<H", data, opt, magic)
        dd = opt + (0x60 if magic == 0x10B else 0x70)
        if sec_va:
            _st.pack_into("<II", data, dd + 4 * 8, sec_va, sec_size)
        with open(path, "wb") as f:
            f.write(bytes(data))

    try:
        for magic, label in ((0x10B, "PE32"), (0x20B, "PE32+")):
            _make_pe(_pe_path, magic)
            ok, why = doctor.pe_has_signature(_pe_path)
            check(ok, f"{label} 的签名块在 +0x{'60' if magic == 0x10B else '70'}：{why}")

        # Security 目录为 0 → 未签名
        _make_pe(_pe_path, 0x20B, sec_va=0)
        ok, why = doctor.pe_has_signature(_pe_path)
        check(not ok and "无内嵌签名块" in why, f"目录为空应判未签名：{why}")

        # 偏移越界 → 判未签名并说明原因（不能静默当成已签名）
        _make_pe(_pe_path, 0x20B, sec_va=2000, sec_size=4096)
        ok, why = doctor.pe_has_signature(_pe_path)
        check(not ok and "越界" in why, f"越界要说明：{why}")

        # 非 PE 文件（真实文件）→ False，且不许抛
        ok, why = doctor.pe_has_signature(os.path.join(ROOT, "tools", "doctor.py"))
        check(not ok and "MZ" in why, f"非 PE 要有明确原因：{why}")
        ok, _ = doctor.pe_has_signature(os.path.join(ROOT, "不存在.exe"))
        check(not ok, "文件不存在也要返回 False 而不是抛异常")
    finally:
        if os.path.exists(_pe_path):
            os.remove(_pe_path)

    print("[11] 代码签名：未签名必须被看见，且不能把「没入口」当成「全绿」")
    _orig_targets = doctor._signature_targets
    _orig_pe = doctor.pe_has_signature
    _orig_root = doctor.ROOT
    _fake_root = os.path.join(_tmp_base, "_doctor_tmp_root")
    try:
        # 11.0 只认包根的**自研** exe：把 python/python.exe 算进来，会让
        # 「我们自己什么都没签」显示成 PASS（拿 Python 基金会的签名给自己背书）。
        os.makedirs(os.path.join(_fake_root, "python"), exist_ok=True)
        for rel in ("自研启动器.exe", os.path.join("python", "python.exe")):
            with open(os.path.join(_fake_root, rel), "wb") as fh:
                fh.write(b"MZ")
        doctor.ROOT = _fake_root
        got = [os.path.basename(p) for p in doctor._signature_targets()]
        check(got == ["自研启动器.exe"],
              f"只该列出包根的自研 exe（实得 {got}）")
        doctor.ROOT = _orig_root

        doctor.sys = _FakeSysPlatform("win32")
        # 11.1 有未签名 exe → WARN 且点名文件（阶段 C4 的"明确告知"）
        doctor._signature_targets = lambda: [_signed_pe]
        doctor.pe_has_signature = lambda p: (False, "无内嵌签名块")
        lvl, _, detail, hint = doctor.check_signature()
        check(lvl == doctor.WARN, f"未签名应 WARN 而非 FAIL（实得 {lvl}）")
        check("doctor_tmp_signed.exe" in detail, f"要点名是哪个文件：{detail}")
        check("证书" in hint, f"提示要给出证书这条路：{hint}")

        # 11.2 全部已签名 → PASS
        doctor.pe_has_signature = lambda p: (True, "16 B")
        lvl, _, detail, _ = doctor.check_signature()
        check(lvl == doctor.PASS and "均带内嵌签名" in detail,
              f"全签名应 PASS（实得 {lvl}：{detail}）")

        # 11.3 一个可执行入口都没有 → WARN，且说清这是缺口而不是"没问题"
        # 真实现状：交付包里只有 .py，用户无从双击。若这里返回 PASS，就是拿
        # "无可检对象"冒充"检查通过"。
        doctor._signature_targets = lambda: []
        lvl, _, detail, hint = doctor.check_signature()
        check(lvl == doctor.WARN, f"无入口不能算 PASS（实得 {lvl}）")
        check("无可检对象" in detail, f"要说清是没东西可检：{detail}")
        check("C2" in hint, f"要指回架构决策：{hint}")
    finally:
        doctor.sys = sys
        doctor.ROOT = _orig_root
        doctor._signature_targets = _orig_targets
        doctor.pe_has_signature = _orig_pe
        for _rel in ("自研启动器.exe", os.path.join("python", "python.exe")):
            _p = os.path.join(_fake_root, _rel)
            if os.path.exists(_p):
                os.remove(_p)
        for _d in (os.path.join(_fake_root, "python"), _fake_root):
            if os.path.isdir(_d):
                os.rmdir(_d)
        for _n in ("ucrtbase.dll",):
            _p = os.path.join(_fake_empty, _n)
            if os.path.exists(_p):
                os.remove(_p)
        if os.path.isdir(_fake_empty):
            os.rmdir(_fake_empty)
        # 假根现在在 %TEMP% 下，整棵删掉即可（individual 清理留着是为了让失败时
        # 仍能看到残留，排查方便）。
        import shutil as _sh0
        _sh0.rmtree(_tmp_base, ignore_errors=True)

    print("[12] 升级吞配置：出厂默认 + 使用痕迹 = 大概率被覆盖，必须报警")
    # 场景：老用户填过 Key → 解压新版 → config.yaml 变回出厂默认，但 data/
    # 还留着使用痕迹。两条单信号都会误报（新用户没填 Key 也很正常），
    # 所以断言的核心是「同时成立才报、单独成立不报」。
    import types
    import tempfile
    import shutil as _sh
    import src.config as _src_config
    import src.config_schema as _src_schema

    _orig_load = _src_config.load_config
    _orig_validate = _src_schema.validate
    _orig_root = doctor.ROOT
    _probe = os.path.join(tempfile.gettempdir(), "doctor_cfg_probe")
    _sh.rmtree(_probe, ignore_errors=True)
    os.makedirs(_probe, exist_ok=True)
    try:
        _fake_cfg = types.SimpleNamespace(ns=object(), raw={"api_key": ""})
        _src_config.load_config = lambda *a, **k: _fake_cfg
        _src_schema.validate = lambda *a, **k: []
        doctor.ROOT = _probe
        cfg_path = os.path.join(_probe, "config.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("api_key: ''\n")

        # 12.1 单信号：没填 Key 的新用户（无 data/）→ 不许报警
        check(doctor.check_config(cfg_path)[0] == doctor.PASS,
              "只没填 Key、无使用痕迹 → PASS，不误报新用户")

        # 12.2 单信号：data/ 有痕迹但 Key 填了 → 也不报（Key 还在，没被覆盖）
        data_dir = os.path.join(_probe, "data")
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "x"), "w", encoding="utf-8") as f:
            f.write("y")
        _fake_cfg.raw = {"api_key": "sk-something"}
        check(doctor.check_config(cfg_path)[0] == doctor.PASS,
              "Key 还在 + 有痕迹 → PASS，不误报老用户")

        # 12.3 双信号同时成立 → 必须 WARN，且提示可操作
        _fake_cfg.raw = {"api_key": ""}
        lvl, _name, detail, hint = doctor.check_config(cfg_path)
        check(lvl == doctor.WARN, f"出厂默认 + data/ 有痕迹必须 WARN（实得 {lvl}）")
        check("覆盖" in detail or "覆盖" in hint, f"提示要说明是被覆盖：{detail}")

        # 12.4 显式备份文件存在 → 优先报，提示比对恢复
        with open(os.path.join(_probe, "config.user.bak.yaml"), "w",
                  encoding="utf-8") as f:
            f.write("api_key: 'sk-old'\n")
        lvl, _name, detail, hint = doctor.check_config(cfg_path)
        check(lvl == doctor.WARN and "bak" in detail.lower(),
              f"有备份文件时要点名 bak：{detail}")
        check("config.user.bak.yaml" in hint, f"恢复提示要给文件名：{hint}")
    finally:
        _src_config.load_config = _orig_load
        _src_schema.validate = _orig_validate
        doctor.ROOT = _orig_root
        _sh.rmtree(_probe, ignore_errors=True)

    # ===== 14. 密钥落盘（C9-1）：check_secret_storage =====
    print("\n[14] 密钥落盘")
    _probe14 = tempfile.mkdtemp(prefix="doctor_secret_")
    try:
        _cfg14 = os.path.join(_probe14, "config.yaml")
        # 14.1 明文 → WARN + 明说泄露风险 + 给出可执行修复
        with open(_cfg14, "w", encoding="utf-8") as f:
            f.write("sticker:\n  api_key: 'sk-real-secret'\n  other: 1\n")
        lvl, item, detail, hint = doctor.check_secret_storage(_cfg14)
        check(lvl == doctor.WARN, f"明文 Key 报 WARN（实际 {lvl}）")
        check(item == "密钥落盘", f"检查项名：{item}")
        check("1 处" in detail and "明文" in detail, f"明细点明数量与明文：{detail}")
        check("dpapi" in hint, f"修复提示给出加密路径：{hint[:40]}")
        # 14.2 全部加密 → PASS
        with open(_cfg14, "w", encoding="utf-8") as f:
            f.write("sticker:\n  api_key: 'dpapi:QUJD'\n")
        lvl, _i, detail, hint = doctor.check_secret_storage(_cfg14)
        check(lvl == doctor.PASS and "DPAPI" in detail,
              f"密文报 PASS：{detail}")
        # 14.3 混合 → WARN 且两侧数量都对
        with open(_cfg14, "w", encoding="utf-8") as f:
            f.write("a:\n  api_key: 'dpapi:QUJD'\nlist:\n- api_key: 'sk-x'\n- api_key: ''\n")
        lvl, _i, detail, _h = doctor.check_secret_storage(_cfg14)
        check(lvl == doctor.WARN and "1 处明文" in detail and "1 处已加密" in detail,
              f"混合报 WARN 且计数正确：{detail}")
        # 14.4 全空 → PASS
        with open(_cfg14, "w", encoding="utf-8") as f:
            f.write("a:\n  api_key: ''\n  name: x\n")
        lvl, _i, detail, _h = doctor.check_secret_storage(_cfg14)
        check(lvl == doctor.PASS, f"全空 Key 不误报（实际 {lvl}：{detail}）")
        # 14.5 cookie 字段同覆盖（C9 缺口关闭）：明文 cookie → WARN
        with open(_cfg14, "w", encoding="utf-8") as f:
            f.write("qzone:\n  uin: '123'\n  cookie: 'p_skey=LEAK;'\n")
        lvl, _i, detail, _h = doctor.check_secret_storage(_cfg14)
        check(lvl == doctor.WARN and "1 处" in detail,
              f"明文 cookie 报 WARN：{detail}")
        # 14.6 cookie 密文 → PASS
        with open(_cfg14, "w", encoding="utf-8") as f:
            f.write("qzone:\n  cookie: 'dpapi:QUJD'\n")
        lvl, _i, detail, _h = doctor.check_secret_storage(_cfg14)
        check(lvl == doctor.PASS, f"cookie 密文报 PASS：{detail}")
    finally:
        _sh.rmtree(_probe14, ignore_errors=True)

    print()
    print("[15] GBK 控制台不炸：编不了的符号降级为 ? 也要跑完"
          "（2026-10-18 包内自检实锤 UnicodeEncodeError）")
    env = dict(os.environ, PYTHONIOENCODING="gbk")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "doctor.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    check("UnicodeEncodeError" not in (p.stderr or ""),
          "强制 GBK stdout 下无编码崩溃")
    check("PASS" in (p.stdout or "") or "WARN" in (p.stdout or ""),
          "doctor 正常输出诊断结果")

    print()
    print("[16] _shell_mode 兜底分支：normalize 不可用时也要收敛成 auto"
          "（曾写成 return AUTO，而 AUTO 只在 try 里局部导入 → NameError）")
    import src.ui.pet_backend as _pb
    _orig_normalize = _pb.normalize

    def _boom(*_a, **_k):
        raise RuntimeError("强制失败：模拟 normalize 不可用")

    # 先验正常路径：raw 为 None（配置缺省）时也该是 auto
    try:
        check(doctor._shell_mode(None) == "auto",
              "cfg=None（配置缺省）→ auto")
    except Exception as e:  # noqa: BLE001
        check(False, f"正常路径就抛了 {type(e).__name__}：{e}")
    # 再造一次导入/调用失败，走 except 分支
    _pb.normalize = _boom
    try:
        try:
            mode = doctor._shell_mode(None)
        except NameError as e:
            check(False, f"except 分支自己抛 NameError（{e}）："
                         "AUTO/WEBVIEW 这类常量只在 try 里局部导入")
        except Exception as e:  # noqa: BLE001
            check(False, f"except 分支抛了意外的 {type(e).__name__}：{e}")
        else:
            check(mode == "auto", f"normalize 炸了也兜底成 auto，不抛（实际 {mode!r}）")
    finally:
        _pb.normalize = _orig_normalize

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        return 1
    print("PASS：doctor 自检结构成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
