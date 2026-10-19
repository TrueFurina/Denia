"""敏感窗口跳过：屏幕采集不该看见的东西，一开始就别生成。

背景（2026-10-20）：`perception.screenshot.interval_s: 2` 意味着**每 2 秒抓一帧
全屏**，抓到的画面会进模型上下文。密码管理器窗口、网银页面、支付确认页一旦
进了上下文就是**不可撤回**的泄露——没有"删掉这一帧"，只有"当初别抓"。

同类产品（Copilot Vision / 豆包 / Kimi）全是显式共享 + 可见指示，没有一家做
应用级排除；它们靠"用户主动触发"规避风险。达妮娅是常驻定时采集，所以必须自己
把这条补上。

本文件测的是三件事，而不是"函数返回对不对"：
  1. 命中时**真的没抓屏**（不是抓完再丢）——判据是 analyzer 零调用；
  2. `skip_apps: []` 是"用户明确说不拦"，不能被默认清单覆盖回来；
  3. 跳过日志按"原因变化"去重——网银页面停留 10 分钟不该刷 300 行。

跑法：python src/tests/test_screen_skip_apps.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.perception.privacy import DEFAULT_SKIP_APPS, sensitive_skip_reason  # noqa: E402
from src.perception.screen_capture import ScreenCapture  # noqa: E402

_fails: list = []


def check(ok: bool, msg: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        _fails.append(msg)


_MISSING = object()


def _cfg(skip_apps=_MISSING, interval_s: float = 2.0):
    """造一个够 ScreenCapture 构造用的最小 cfg（不碰磁盘）。"""
    sc = SimpleNamespace(
        interval_s=interval_s, game_interval_s=6.0, interval_jitter=0.1,
        static_streak_s=30, static_interval_scale=0.5, static_analyze_every=1,
        monitor_index=1, downscale=64, change_threshold=5,
    )
    if skip_apps is not _MISSING:
        sc.skip_apps = skip_apps
    return SimpleNamespace(
        ns=SimpleNamespace(perception=SimpleNamespace(screenshot=sc)),
        runtime_status=SimpleNamespace(vision_enabled=True),
    )


class _Analyzer:
    """假分析器：只记被调用次数，不做任何真实视觉请求。"""

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, jpeg: bytes, force: bool = False):
        self.calls += 1
        return None


def _make(skip_apps=_MISSING, fg=None):
    an = _Analyzer()
    cap = ScreenCapture(bus=None, analyzer=an, cfg=_cfg(skip_apps),
                        foreground_check=fg)
    return cap, an


def main() -> int:
    print("=== [1] 纯函数：进程名 / 标题 / 都不命中 ===")
    check(sensitive_skip_reason("keepass.exe", "KeePass 密码库") == "keepass",
          "进程名命中密码管理器（返回命中的清单条目，供审计）")
    check(sensitive_skip_reason("chrome.exe", "中国工商银行 - 网上银行") == "网上银行",
          "浏览器开网银：进程名是 chrome，靠标题命中")
    check(sensitive_skip_reason("Code.exe", "main.py - Denia") == "",
          "普通窗口不误伤（写代码时照常看）")
    check(sensitive_skip_reason("", "") == "", "空进程名 + 空标题 → 不跳过")
    check(sensitive_skip_reason("WeChat.exe", "微信") == "",
          "微信**刻意不在**默认清单（那是核心能力，不是隐私风险）")
    check(sensitive_skip_reason("KeePass.exe", "") == "keepass",
          "匹配不区分大小写")

    print("\n=== [2] 默认清单 vs 空列表：语义必须不同 ===")
    check(sensitive_skip_reason("keepass.exe", "", None) == "keepass",
          "patterns=None → 用默认清单")
    check(sensitive_skip_reason("keepass.exe", "", []) == "",
          "patterns=[] → 用户明确说「都别拦」，不能被默认值覆盖回来")
    check(len(DEFAULT_SKIP_APPS) >= 10, f"默认清单非空（{len(DEFAULT_SKIP_APPS)} 条）")
    check(not any("微信" == x or "qq" == str(x).lower() for x in DEFAULT_SKIP_APPS),
          "默认清单里没有微信/QQ（不能把核心能力挡掉）")

    print("\n=== [3] 命中时**真的没抓屏**（analyzer 零调用）===")
    cap, an = _make(fg=lambda: ("keepass.exe", "KeePass"))
    asyncio.run(cap._grab_and_analyze())
    check(an.calls == 0, "命中敏感窗口 → 视觉分析零调用（图没生成，不是抓完再丢）")
    check(cap.snapshot()["skip_count"] == 1, "跳过计数 +1（可审计）")
    check(cap.snapshot()["skip_last"] == "keepass", "记录了命中的具体条目")

    print("\n=== [4] 不命中时照常走（不能把功能弄瞎）===")
    cap2, an2 = _make(fg=lambda: ("Code.exe", "main.py"))
    # 不命中会走到真抓屏（mss）。这里只验「没有提前 return」——用 skip_count 判，
    # 避免依赖真实显示器（CI/无头环境也能跑）。
    check(cap2._sensitive_reason() == "", "普通窗口 → 判定为空（会继续采集）")
    check(cap2.snapshot()["skip_count"] == 0, "普通窗口不记跳过")

    print("\n=== [5] 向后兼容：没注入回调时行为不变 ===")
    cap3, _ = _make()
    check(cap3._sensitive_reason() == "", "未注入 foreground_check → 不拦（老装配不炸）")

    print("\n=== [6] 日志按「原因变化」去重（不是每次跳过都刷一行）===")
    rows: list = []

    class _H(logging.Handler):
        def emit(self, rec):
            rows.append(rec.getMessage())

    lg = logging.getLogger("src.perception.screen_capture")
    h = _H()
    lg.addHandler(h)
    old_level = lg.level
    lg.setLevel(logging.INFO)
    try:
        cap4, _ = _make(fg=lambda: ("keepass.exe", "KeePass"))
        for _ in range(5):
            cap4._mark_skip("网银")
        check(len(rows) == 1, f"同一原因连续 5 次只打 1 行（实际 {len(rows)}）")
        cap4._mark_skip("keepass")
        check(len(rows) == 2, f"换了原因再打 1 行（实际 {len(rows)}）")
        check(cap4.snapshot()["skip_count"] == 6, "计数照常累加（去重只影响日志）")
    finally:
        lg.removeHandler(h)
        lg.setLevel(old_level)

    print("\n=== [7] 装配：main.py 两处 ScreenCapture 都传了 foreground_check ===")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main_py = os.path.join(root, "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()
    # 取出每一处 `ScreenCapture(...)` 的完整实参串（到配对的右括号），
    # 逐处断言都带了 foreground_check。只查"文件里出现过"是不够的——
    # 两处装配漏一处，那条路径上的采集就静默不设防。
    calls = []
    for m in re.finditer(r"ScreenCapture\(", src):
        i, depth = m.end(), 1
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        calls.append(src[m.end():i])
    check(len(calls) == 2, f"main.py 里 ScreenCapture 装配共 2 处（实际 {len(calls)}）")
    for n, c in enumerate(calls, 1):
        check("foreground_check=" in c, f"第 {n} 处装配传了 foreground_check")

    print("\n=== [8] 出厂模板带默认清单（新用户开箱即受保护）===")
    tpl = os.path.join(root, "config.release.yaml")
    with open(tpl, encoding="utf-8") as f:
        t = f.read()
    check("skip_apps:" in t, "config.release.yaml 的 perception.screenshot 有 skip_apps")
    check("网上银行" in t and "keepass" in t,
          "模板里确实写了网银与密码管理器（不是空壳字段）")

    print()
    if _fails:
        print(f"❌ {len(_fails)} 项未通过")
        for m in _fails:
            print(f"   - {m}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
