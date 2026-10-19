"""感知指示器：让「她正在看屏幕」这件事在桌面上看得见。

背景（2026-10-20）：屏幕采集每 2s 抓一帧全屏，但桌面上原本**没有任何可见迹象**。
同类产品（Copilot Vision / 豆包 / Kimi）全是显式共享 + 可见指示，没有一家静默采集。
用户只能靠"信任"，没法靠"看见"——这不是体验问题，是合规问题。

本文件测的不是"页面上有个点"，而是三件更容易做错的事：
  1. 点的亮灭**由后端状态决定**，不是写死常亮（写死的话它就是在骗人）；
  2. 状态**通过页面主动轮询**拿到，不走 Python 跨线程 evaluate_js
     （main_window.py:454 有实测死锁结论）；
  3. 关掉看屏 / 隐私模式下**真的不亮**（三态都要能区分）。

跑法：python src/tests/test_perception_indicator.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.perception import privacy as _priv  # noqa: E402
from src.ui.webapp import CharacterAPI  # noqa: E402

_fails: list = []


def check(ok: bool, msg: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        _fails.append(msg)


class _Cap:
    """假 ScreenCapture：只提供 get_perception_state 真正读的那几项。"""

    def __init__(self, enabled: bool = True, **snap) -> None:
        self.enabled = enabled
        self._snap = snap

    def snapshot(self) -> dict:
        return dict(self._snap)


def _api(cap=None):
    """造一个只带 _capture 的 CharacterAPI 桩（不走 __init__，不碰磁盘/网络）。"""
    a = CharacterAPI.__new__(CharacterAPI)
    if cap is not None:
        a._capture = cap
    return a


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print("=== [1] 接口真的挂在 CharacterAPI 上（桌宠页的 js_api 就是它）===")
    check(hasattr(CharacterAPI, "get_perception_state"),
          "CharacterAPI 暴露 get_perception_state（否则页面调不到，点永远不亮）")

    print("\n=== [2] watching 跟着真实开关走，不是写死 true ===")
    st = _api(_Cap(enabled=True)).get_perception_state()
    check(st["watching"] is True, "看屏开着 → watching=True")
    st = _api(_Cap(enabled=False)).get_perception_state()
    check(st["watching"] is False, "看屏关着 → watching=False（写死 true 就是骗人）")

    print("\n=== [3] 隐私模式单独一位，且与 watching 互不覆盖 ===")
    _priv.set_privacy_mode(True)
    try:
        st = _api(_Cap(enabled=True)).get_perception_state()
        check(st["privacy"] is True, "隐私模式开着 → privacy=True")
        check(st["watching"] is True,
              "privacy 与 watching 是两个维度：开关还开着，只是全停（页面据此画琥珀点）")
    finally:
        _priv.set_privacy_mode(False)

    print("\n=== [4] 敏感窗口跳过统计透传（用户能查「她为什么没看见」）===")
    st = _api(_Cap(enabled=True, skip_last="网银", skip_count=7,
                   interval_s=2.0, last_ts=1750000000.0)).get_perception_state()
    check(st["skip_last"] == "网银", "skip_last 透传")
    check(st["skip_count"] == 7, "skip_count 透传")
    check(st["interval_s"] == 2.0, "interval_s 透传（供说明「多久看一眼」）")

    print("\n=== [5] 降级：没有 capture / 快照抛异常时不能把页面带崩 ===")
    st = _api().get_perception_state()
    check(st["success"] is True and st["watching"] is False,
          "没有 capture → success 且 watching=False（不抛异常）")

    class _Boom:
        enabled = True

        def snapshot(self):
            raise RuntimeError("boom")

    st = _api(_Boom()).get_perception_state()
    check(st["success"] is True, "快照抛异常 → 仍然返回 success（指示器降级为不亮）")
    check(st["watching"] is True, "异常前已读到的 watching 保留（不因后半段失败而回退）")

    print("\n=== [6] 桌宠页：亮灭由状态决定，且不走跨线程推送 ===")
    rig = os.path.join(root, "src", "ui", "web", "live2d", "rig", "index.html")
    with open(rig, encoding="utf-8") as f:
        html = f.read()
    check('id="eyeDot"' in html, "rig 页面有 #eyeDot 元素")
    # 静态 HTML 里不能预先带 on/paused 类：带了就等于"永远亮"，与状态无关
    m = re.search(r'<div id="eyeDot"[^>]*>', html)
    check(m is not None and "class=" not in (m.group(0) if m else ""),
          "静态标签不带 class（带了就是永远亮，与真实状态脱钩）")
    check("s.watching" in html and "s.privacy" in html,
          "亮灭由返回值 s.watching / s.privacy 决定")
    # 锚定到**自己的变量名 + 条件参数**，不能只查方法名：这个页面里
    # `classList.toggle('on'` 一共出现 3 次（Live2D 面板开关也用），
    # 只查方法名会被无关代码满足——变异刀3 就是这么漏过去的。
    check("dot.classList.toggle('on', on)" in html
          and "dot.classList.toggle('paused', paused)" in html,
          "两种状态分别落到 on / paused，且由条件决定（不是无条件 add）")
    check(re.search(r"(pywebview\.api|window)\.evaluate_js\s*\(", html) is None,
          "页面不调用 evaluate_js（跨线程/同步调用会自锁，见 main_window.py:454）")
    check("Promise.resolve(api.get_perception_state())" in html,
          "页面把后端返回值当数据源（不是本地编一个状态）")
    check("setInterval(" in html, "靠 setInterval 轮询（页面主动拉，不用 Python 推）")
    # display 显隐而非 opacity 淡入：指示器有延迟 = 有一瞬间用户不知道它在看
    check(re.search(r"#eyeDot\.on\{[^}]*display:block", html) is not None,
          "on 态用 display:block 立刻出现（不做淡入）")
    check("opacity:0" not in html.split("#eyeDot.on")[0].split("#eyeDot{")[-1],
          "基础态不用 opacity:0 做隐藏（headless 下不可验证，且淡入有延迟）")

    print()
    if _fails:
        print(f"❌ {len(_fails)} 项未通过")
        for x in _fails:
            print(f"   - {x}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
