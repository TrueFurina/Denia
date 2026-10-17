# -*- coding: utf-8 -*-
"""agent_pro · 自测（全桩离线，不依赖主软件 / LLM / GUI）。

    python -m src.agent_pro.selftest
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import time
import traceback
from pathlib import Path

_PASS = 0
_FAIL = 0
_FAILS: list[str] = []


def _t(name: str):
    def deco(fn):
        global _PASS, _FAIL
        try:
            fn()
            _PASS += 1
            print(f"  [{len(_FAILS)+_PASS:02d}] PASS {name}")
        except Exception as e:  # noqa: BLE001
            _FAIL += 1
            _FAILS.append(name)
            print(f"  [{_PASS+_FAIL:02d}] FAIL {name}: {e}")
            traceback.print_exc()
    return deco


def main() -> int:
    print("== agent_pro selftest（全桩离线）==")
    from src.agent_pro import config as cfg
    from src.agent_pro import app_index as ai
    from src.agent_pro import launch as lp

    # ---- 1) 配置：缺省关 / 合并写 / 损坏降级 ----
    with tempfile.TemporaryDirectory() as td:
        @_t("config 默认全关")
        def _():
            f = cfg.load_flags(base_dir=td)
            assert f["enabled"] is False
            assert f["file_search_engine"] == "auto"
        @_t("config 状态文件合并写不丢键")
        def _():
            cfg.write_state_flags({"enabled": True}, base_dir=td)
            cfg.write_state_flags({"find_max_results": 5}, base_dir=td)
            f = cfg.load_flags(base_dir=td)
            assert f["enabled"] is True and f["find_max_results"] == 5, f
        @_t("config 损坏状态文件安全降级")
        def _():
            (Path(td) / cfg.STATE_FILENAME).write_text("{oops", encoding="utf-8")
            f = cfg.load_flags(base_dir=td)
            assert f["enabled"] is False
        @_t("别名文件缺省为空")
        def _():
            assert cfg.load_aliases(base_dir=td) == {}

    # ---- 2) 规范化与打分 ----
    @_t("normalize 去空白/大小写/版本尾巴")
    def _():
        assert ai.normalize("QQ Music 2026.1") == ai.normalize("qq music")
        assert ai.normalize("汽水音乐") == "汽水音乐"
    @_t("score：精确=100 / 包含=80+ / 无关<40")
    def _():
        assert ai.score_match("记事本", "记事本") == 100.0
        s = ai.score_match("汽水", "汽水音乐", "SodaMusic")
        assert 80 <= s <= 90, s
        assert ai.score_match("汽水", "微信", "") < 40
        # exe 名也能救场：名字怪但 exe 对得上
        assert ai.score_match("sodamusic", "音乐", "SodaMusic") >= 80
    @_t("score：版本号名字不干扰匹配")
    def _():
        # "Soda Music 3.5.2" 这类带版本的名字应能与"汽水音乐"高匹配吗？
        # 反向更重要：目标带版本也应匹配干净名字
        assert ai.score_match("网易云音乐 3.0", "网易云音乐", "") >= 65

    # ---- 3) 匹配排序 ----
    @_t("match 排序与阈值")
    def _():
        apps = [
            {"name": "汽水音乐", "exe": r"E:\x\SodaMusic.exe", "source": "uninstall",
             "token": {"type": "exe", "value": r"E:\x\SodaMusic.exe"}},
            {"name": "汽水音乐助手", "exe": "", "source": "uwp",
             "token": {"type": "appid", "value": "X!App"}},
            {"name": "微信", "exe": "", "source": "uwp", "token": {"type": "appid", "value": "W!App"}},
        ]
        hits = ai.match("汽水音乐", apps)
        assert hits and hits[0][0]["name"] == "汽水音乐"
        assert not ai.match("不存在的东西xyz", apps)

    # ---- 4) 启动引擎：验证逻辑（桩进程快照） ----
    @_t("verify_started：期望进程出现→成功")
    def _():
        base = {"notepad.exe"}
        seq = [set(base), set(base), set(base | {"sodamusic.exe"})]
        it = iter(seq)
        orig = lp.proc_names
        lp.proc_names = lambda: next(it)
        try:
            ok, desc = lp.verify_started("SodaMusic.exe", base, timeout_s=2, interval_s=0.05)
        finally:
            lp.proc_names = orig
        assert ok, desc
    @_t("verify_started：超时→失败且不谎报")
    def _():
        orig = lp.proc_names
        lp.proc_names = lambda: {"notepad.exe"}
        try:
            ok, desc = lp.verify_started("SodaMusic.exe", {"notepad.exe"},
                                         timeout_s=0.6, interval_s=0.05)
        finally:
            lp.proc_names = orig
        assert not ok and "未检测到新进程" in desc
    @_t("verify_started：未知新进程也算成功（UWP 场景）")
    def _():
        seq = [{"a.exe"}, {"a.exe", "somestoreapp.exe"}]
        it = iter(seq)
        orig = lp.proc_names
        lp.proc_names = lambda: next(it)
        try:
            ok, desc = lp.verify_started("", {"a.exe"}, timeout_s=2, interval_s=0.05)
        finally:
            lp.proc_names = orig
        assert ok and "somestoreapp" in desc
    @_t("verify_started：基线不可得→如实说无法验证")
    def _():
        orig = lp.proc_names
        lp.proc_names = lambda: set()
        try:
            ok, desc = lp.verify_started("x.exe", set(), timeout_s=0.3, interval_s=0.05)
        finally:
            lp.proc_names = orig
        assert not ok and "无法验证" in desc
    @_t("launch_with_token：失败路径给诚实文案")
    def _():
        orig = lp.proc_names
        lp.proc_names = lambda: {"a.exe"}
        try:
            r = lp.launch_with_token({"type": "exe", "value": r"C:\nope\ghost.exe", "_name": "幽灵"},
                                     verify_timeout=0.6)
        finally:
            lp.proc_names = orig
        assert "未检测到新进程" in r and "可能未成功" in r

    @_t("已在运行 → 短路报告，不重复拉起")
    def _():
        orig = lp.proc_names
        lp.proc_names = lambda: {"sodamusic.exe", "a.exe"}
        launched = []
        orig_popen = lp.subprocess.Popen
        lp.subprocess.Popen = lambda *a, **k: launched.append(a) or orig_popen(["cmd", "/c", "exit"])
        try:
            r = lp.launch_with_token({"type": "exe", "value": r"E:\x\SodaMusic.exe", "_name": "汽水音乐"},
                                     verify_timeout=1.0)
        finally:
            lp.proc_names = orig
            lp.subprocess.Popen = orig_popen
        assert "已在运行" in r, r
        assert not launched, "已在运行时不应再发启动命令"

    # ---- 5) install/uninstall：对真实 agent_tools 挂钩并恢复 ----
    @_t("install 幂等 + 工具注入 + uninstall 恢复")
    def _():
        from src.agent_pro import install, uninstall
        import src.llm.agent_tools as at
        before_specs = len(at.TOOL_SPECS)
        before_core = at._dispatch_core
        before_fuzzy = at._fuzzy_launch
        assert install(None, flags_override={"enabled": True}) is True
        assert install(None, flags_override={"enabled": True}) is True  # 幂等
        assert len(at.TOOL_SPECS) == before_specs + 7
        assert at._dispatch_core is not before_core
        assert at._fuzzy_launch is not before_fuzzy
        assert "find_file" in {t["function"]["name"] for t in at.available_tool_specs()}
        assert "click_element" in {t["function"]["name"] for t in at.available_tool_specs()}
        uninstall()
        assert len(at.TOOL_SPECS) == before_specs
        assert at._dispatch_core is before_core
        assert at._fuzzy_launch is before_fuzzy
    @_t("install 缺省关：零副作用")
    def _():
        from src.agent_pro import install
        import src.llm.agent_tools as at
        before_specs = len(at.TOOL_SPECS)
        assert install(None, flags_override={"enabled": False}) is False
        assert len(at.TOOL_SPECS) == before_specs
    @_t("包内工具经 dispatch_tool 路由（wait_for 超时路径）")
    def _():
        from src.agent_pro import install, uninstall
        import src.llm.agent_tools as at
        install(None, flags_override={"enabled": True})
        try:
            r = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                at.dispatch_tool("wait_for", {"kind": "process", "target": "definitely_not_running_xyz",
                                              "timeout": 0.5}, {}))
            assert "超时" in r, r
        finally:
            uninstall()

    @_t("包内 find_file 经 dispatch_tool 路由（拦 import/名称类静默炸）")
    def _():
        from src.agent_pro import install, uninstall
        import src.llm.agent_tools as at
        install(None, flags_override={"enabled": True})
        try:
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / "router_probe.png").write_bytes(b"x")
                loop = asyncio.new_event_loop()
                try:
                    r = loop.run_until_complete(at.dispatch_tool(
                        "find_file", {"pattern": "*.png", "folder": td}, {}))
                finally:
                    loop.close()
                assert "router_probe.png" in r, r
        finally:
            uninstall()

    # ---- 6) wait_for 窗口等待（用真实枚举但等待必不存在的窗口） ----
    @_t("wait_for window 超时路径")
    def _():
        from src.agent_pro.wait import wait_for
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(wait_for("window", "绝不会有这个窗口xyz", 0.6, 0.05, {}))
        finally:
            loop.close()
        assert "超时" in r

    # ---- 7) find_file（walk 兜底路径，临时目录） ----
    @_t("find_file walk 模式找到目标")
    def _():
        from src.agent_pro.wait import find_file
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "sub"
            sub.mkdir()
            (sub / "hello_test.png").write_bytes(b"x")
            (sub / "other.txt").write_bytes(b"x")
            f = dict(cfg.DEFAULTS, find_roots=str(td), find_walk_budget_s=5.0)
            r = find_file({"pattern": "*.png"}, f)
            assert "hello_test.png" in r, r

    @_t("find_file 空结果不谎报")
    def _():
        from src.agent_pro.wait import find_file
        with tempfile.TemporaryDirectory() as td:
            f = dict(cfg.DEFAULTS, find_roots=str(td))
            r = find_file({"pattern": "*.png"}, f)
            assert "未找到" in r

    # ---- 8) screen：坐标解析 / 编号点击守卫 / ui_elements 兜底文案 ----
    from src.agent_pro import screen as sc

    @_t("click_element：VLM 坐标解析（严格格式）与 NONE 拒识")
    def _():
        for s, (x, y) in [("X=960,Y=540", (960, 540)), ("x:123, y:87", (123, 87)),
                          ("X＝12，Y＝34", (12, 34)), ("X=100,Y=200。", (100, 200))]:
            m = sc._COORD_RE.search(s)
            assert m and (int(m.group(1)), int(m.group(2))) == (x, y), s
        assert not sc._COORD_RE.search("NONE")
        assert not sc._COORD_RE.search("屏幕上没有它")
    @_t("click_element：空清单点编号 → 引导先 ui_elements")
    def _():
        sc._ui_cache = []
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(sc.click_element({"index": 1}, {}, {}))
        finally:
            loop.close()
        assert "ui_elements" in r, r
    @_t("click_element：编号超界不点")
    def _():
        sc._ui_cache = [{"name": "a", "ctype": "Button", "x": 5, "y": 5, "interactive": True}]
        sc._ui_cache_ts = time.monotonic()  # 新鲜快照，触发的是超界而不是 TTL
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(sc.click_element({"index": 9}, {}, {}))
        finally:
            loop.close()
        assert "超界" in r, r
    @_t("click_element：desc 缺失 → 双路提示")
    def _():
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(sc.click_element({}, {}, {}))
        finally:
            loop.close()
        assert "index" in r and "desc" in r, r
    @_t("ui_elements：窗口不存在 → 诚实报错不炸")
    def _():
        r = sc.ui_elements({"app": "绝不会有这个窗口xyz"}, {})
        assert "没找到" in r or "未" in r, r
    @_t("ui_elements→click_element 编号闭环（桩元素，真点击被拦）")
    def _():
        sc._ui_cache = [{"name": "搜索", "ctype": "Button", "x": 10, "y": 10, "interactive": True}]
        sc._ui_ctrls = [None]  # 无控件引用 → 强制走坐标点击回退
        sc._ui_cache_ts = time.monotonic()  # 新鲜快照
        clicked = []
        orig = sc._do_click
        sc._do_click = lambda x, y, double=False: clicked.append((x, y)) or f"已在 ({x},{y}) 单击"
        try:
            loop = asyncio.new_event_loop()
            r = loop.run_until_complete(sc.click_element({"index": 1}, {}, {}))
            loop.close()
        finally:
            sc._do_click = orig
            sc._ui_cache = []
        assert clicked == [(10, 10)] and "搜索" in r, r
    @_t("快照 TTL：过期快照上的编号点击被拒（不许盲操作）")
    def _():
        sc._ui_cache = [{"name": "x", "ctype": "Button", "x": 10, "y": 10, "interactive": True}]
        sc._ui_ctrls = [None]
        sc._ui_cache_ts = time.monotonic() - (sc._SNAPSHOT_TTL_S + 5)
        clicked = []
        orig = sc._do_click
        sc._do_click = lambda x, y, double=False: clicked.append((x, y)) or "点击了"
        try:
            loop = asyncio.new_event_loop()
            r = loop.run_until_complete(sc.click_element({"index": 1}, {}, {}))
            loop.close()
        finally:
            sc._do_click = orig
            sc._ui_cache = []
        assert not clicked and "过期" in r and "重新调用 ui_elements" in r, r
    @_t("DPI 一致性守卫：幂等、返回状态说明、不抛")
    def _():
        r1 = sc._ensure_dpi_aware()
        r2 = sc._ensure_dpi_aware()
        assert isinstance(r1, str) and len(r1) > 4, r1
        assert r1 == r2, (r1, r2)
    @_t("UIA 直触：None / 双击 → 不直触（交回坐标点击）")
    def _():
        assert sc._uia_activate(None) is None
        assert sc._uia_activate(None, double=True) is None

        class C:
            pass
        assert sc._uia_activate(C(), double=True) is None
    @_t("UIA 直触：pattern 优先级 Invoke > Legacy > Toggle > Expand > Select")
    def _():
        class P:
            def Invoke(self, waitTime=0):
                pass

            def DoDefaultAction(self):
                pass

            def Toggle(self):
                pass

            def Expand(self):
                pass

            def Select(self):
                pass

        class C:
            def __init__(self, pats):
                self._p = pats

            def GetInvokePattern(self):
                return P() if "Invoke" in self._p else None

            def GetLegacyIAccessiblePattern(self):
                return P() if "Legacy" in self._p else None

            def GetTogglePattern(self):
                return P() if "Toggle" in self._p else None

            def GetExpandCollapsePattern(self):
                return P() if "Expand" in self._p else None

            def GetSelectionItemPattern(self):
                return P() if "Select" in self._p else None

        assert sc._uia_activate(C({"Invoke", "Legacy"})) == "Invoke"
        assert sc._uia_activate(C({"Legacy"})) == "LegacyDoDefault"
        assert sc._uia_activate(C({"Expand"})) == "ExpandCollapse"
        assert sc._uia_activate(C(set())) is None
    @_t("click_element：控件可直触 → 绝不调用坐标点击（不碰鼠标）")
    def _():
        class C:
            def GetInvokePattern(self):
                return self

            def Invoke(self, waitTime=0):
                pass

        sc._ui_cache = [{"name": "确定", "ctype": "Button", "x": 10, "y": 10,
                         "interactive": True}]
        sc._ui_ctrls = [C()]
        sc._ui_cache_ts = time.monotonic()
        clicked = []
        orig = sc._do_click
        sc._do_click = lambda x, y, double=False: clicked.append((x, y)) or "点击了"
        try:
            loop = asyncio.new_event_loop()
            r = loop.run_until_complete(sc.click_element({"index": 1}, {}, {}))
            loop.close()
        finally:
            sc._do_click = orig
            sc._ui_cache = []
            sc._ui_ctrls = []
        assert not clicked, ("支持 Invoke 的控件不该走鼠标点击", clicked)
        assert "直触" in r and "未移动鼠标" in r, r
    @_t("launch：候选 token 顺序（真包名 appid 优先、路径型不优先、去重）")
    def _():
        from src.agent_pro import launch as lp
        rec = {"name": "汽水音乐",
               "token": {"type": "exe", "value": r"E:\Soda Music\SodaMusicLauncher.exe"},
               "_alts": [{"type": "lnk", "value": r"C:\x\soda.lnk"},
                         {"type": "appid", "value": "com.soda.music"},
                         {"type": "exe", "value": r"E:\Soda Music\SodaMusicLauncher.exe"}]}
        order = lp._token_order(rec)
        assert order[0] == {"type": "appid", "value": "com.soda.music"}, order
        assert [t["type"] for t in order] == ["appid", "exe", "lnk"], order
        rec2 = {"name": "抖音",
                "token": {"type": "exe", "value": r"E:\douyin\douyin.exe"},
                "_alts": [{"type": "appid", "value": r"E:\douyin\douyin.exe"}]}
        assert lp._token_order(rec2)[0]["type"] == "exe", lp._token_order(rec2)
    @_t("launch：同目录判定必须带家族前缀（System32 不误判）")
    def _():
        from src.agent_pro import launch as lp
        # notepad 真在跑 → 命中
        assert lp._dir_has_process(r"C:\Windows\System32\notepad.exe",
                                   {("c:\\windows\\system32\\", "notepad")}, "notepad")
        # 只有 svchost 在跑 → 不能把 notepad 判成已在运行
        assert not lp._dir_has_process(r"C:\Windows\System32\notepad.exe",
                                       {("c:\\windows\\system32\\", "svchost")}, "notepad")
        # launcher 家族仍要命中（SodaMusicLauncher ↔ SodaMusic）
        assert lp._dir_has_process(r"C:\Program Files\Soda\SodaMusicLauncher.exe",
                                   {("c:\\program files\\soda\\", "sodamusic")},
                                   "SodaMusicLauncher")
        # 空快照 → 安全返回 False
        assert not lp._dir_has_process(r"C:\x\y.exe", set(), "y")
    @_t("match：system 段噪声过滤（超长组件名丢弃）")
    def _():
        apps = [
            {"name": "Windows SQM 合并计算器", "exe": r"C:\x\sqm.exe",
             "desc": "Windows SQM 合并计算器", "source": "system"},
            {"name": "记事本", "exe": r"C:\x\notepad.exe",
             "desc": "记事本", "source": "system"},
            {"name": "Windows 资源管理器", "exe": r"C:\x\explorer.exe",
             "desc": "Windows 资源管理器", "source": "system"},
        ]
        assert ai.match("计算器", apps) == [], ai.match("计算器", apps)
        hit = ai.match("记事本", apps)
        assert hit and hit[0][0]["name"] == "记事本", hit
        hit2 = ai.match("资源管理器", apps)
        assert hit2 and "资源管理器" in hit2[0][0]["name"], hit2
    @_t("match：system 段降权 → 正式来源优先")
    def _():
        apps = [
            {"name": "锤子便签", "exe": r"C:\a\x.exe", "desc": "锤子便签",
             "source": "system"},
            {"name": "锤子便签", "exe": r"C:\b\x.exe", "desc": "锤子便签",
             "source": "uninstall"},
        ]
        h = ai.match("锤子便签", apps)
        assert h and h[0][0]["source"] == "uninstall", h
    @_t("危险词闸：desc/query 命中删除/支付 → 拒绝，confirmed=true 放行")
    def _():
        loop = asyncio.new_event_loop()
        try:
            r1 = loop.run_until_complete(sc.click_element({"desc": "删除按钮"}, {}, {}))
            r2 = loop.run_until_complete(
                sc.search_in_app({"app": "记事本", "query": "删除所有文件"}, {}, {}))
            r3 = loop.run_until_complete(
                sc.search_in_app({"app": "记事本", "query": "删除所有文件", "confirmed": True},
                                 {}, {}))
        finally:
            loop.close()
        assert "拒绝" in r1 and "confirmed" in r1, r1
        assert "拒绝" in r2, r2
        assert "拒绝" not in r3 or "没找到" in r3 or "窗口" in r3, r3  # 放行后走正常路径
    @_t("search_in_app：缺参 → 使用说明")
    def _():
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(sc.search_in_app({"app": ""}, {}, {}))
        finally:
            loop.close()
        assert "app" in r and "query" in r, r
    @_t("media_state：winsdk 可用则真查询/不可用则如实说明（都不炸）")
    def _():
        from src.agent_pro.media import media_state as ms
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(ms({}, {}, {}))
        finally:
            loop.close()
        assert isinstance(r, str) and len(r) > 10, r
        # 有会话 / 无会话 / winsdk 缺失，三种如实说明都算通过（旧断言只认前两种，
        # 导致"当前没在放歌"时误报 FAIL）
        assert (("媒体会话" in r) or ("winsdk" in r)
                or ("没有任何应用" in r)), r

    print(f"\n== 结果：{_PASS} PASS / {_FAIL} FAIL ==")
    if _FAILS:
        print("失败项：", _FAILS)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
