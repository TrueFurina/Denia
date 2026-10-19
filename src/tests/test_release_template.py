"""出厂模板 / 应用图标 / 品牌默认值的闸门。

为什么单开一个文件
------------------
这几项都是**"出厂状态"**：用户装完第一眼看到的东西。它们有个共同特征——
在开发机上永远是对的（开发机有自己的 config.yaml、自己装过 node_modules、
图标是系统默认也能忍），只有**新用户**才会撞上。所以不能靠"我这儿跑着没问题"
来守，必须对着模板文件本身断言。

已抓到的两个真事故：
1. `config.release.yaml` 的 `force_first_run: true` 是**开发调试开关**
   （注释写着"反复重启看首启效果用"），抄进出厂模板的后果是：每个新用户
   **每次启动都被拽回引导页**，刚填完 Key 下次打开又要填。
2. `app.ico` 一直在仓库里，但**不在 `make_dist.INCLUDE_FILES` 里**——
   交付包没有图标，`main_window.py` 找不到就 `icon=None`，
   Windows 显示的是 python.exe 的图标。

运行：.venv/Scripts/python.exe src/tests/test_release_template.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import yaml  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _load_template() -> dict:
    with open(os.path.join(ROOT, "config.release.yaml"), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main() -> int:
    tpl = _load_template()

    print("=== [1] 首启引导：出厂模板不许带开发调试开关 ===")
    # 判级是 `force or not first_run_done`：force 为真 → 永远首次。
    force = tpl.get("force_first_run")
    check(force is not True,
          f"force_first_run 不为 true（实际 {force!r}）——"
          f"否则用户每次启动都被拽回引导页重填 Key")
    check(tpl.get("first_run_done") is not True,
          "出厂模板不预置 first_run_done（要让新用户真走一次引导）")

    print("\n=== [2] 唤醒词：默认只留她自己的名字 ===")
    wake = ((tpl.get("voice") or {}).get("wake_words")) or []
    check(any("达妮娅" in str(w) for w in wake),
          f"默认唤醒词里有「达妮娅」（实际 {wake}）")
    legacy = [w for w in wake if "小薇" in str(w)]
    check(not legacy, f"默认唤醒词里没有旧产品名「小薇」（实际 {legacy}）")

    print("\n=== [3] 应用图标：存在、多尺寸、且真的会进包 ===")
    ico_path = os.path.join(ROOT, "app.ico")
    check(os.path.isfile(ico_path), "app.ico 在仓库根（main_window.py 按 <根>/app.ico 找）")
    if os.path.isfile(ico_path):
        from PIL import Image

        with Image.open(ico_path) as im:
            sizes = sorted(getattr(im, "info", {}).get("sizes", [im.size]))
        check((256, 256) in sizes, f"含 256×256 帧（任务栏大图标要用，实际 {sizes[-3:]}）")
        check((32, 32) in sizes or (48, 48) in sizes,
              f"含 32/48 小尺寸帧（不然缩小时会糊，实际 {sizes}）")
        check(len(sizes) >= 4, f"至少 4 个尺寸帧（实际 {len(sizes)}）")

    import make_dist

    check("app.ico" in make_dist.INCLUDE_FILES,
          "app.ico 在 make_dist.INCLUDE_FILES 里——不加就等于交付包没有图标")

    print("\n=== [4] 品牌默认值：代码里的兜底名不该是旧产品名 ===")
    # 只查字面量 "小薇"；「微软小薇」是 Edge-TTS 的音色名，不是产品名，必须放过。
    # 角色预设「温柔女友·小薇」已按产品决定改名成「温柔女友」（2026-10-20），
    # 所以这里可以一刀切：除了音色名，任何地方都不该再出现它。
    for rel in ("src/ui/chat_overlay.py", "src/ui/console.py", "src/ui/webapp.py"):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            check(False, f"{rel} 不存在")
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        hits = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if '"小薇"' not in line and "'小薇'" not in line:
                continue
            if "微软小薇" in line:
                continue
            hits.append(f"{rel}:{line_no}")
        check(not hits, f"{rel} 没有把旧名当默认值（命中 {hits}）")

    print("\n=== [5] 首启不问 Key：流程只剩「建角色 → 体检」+ 桌面有承接 ===")
    # 产品决定（2026-10-20）：首启不再让用户面对「模型与API」设置页。少问一步的前提
    # 是**桌面必须承接**——否则用户进去以后不知道自己缺什么。这两半要一起守。
    fpath = os.path.join(ROOT, "src", "ui", "web", "first_launch_setup.html")
    ipath = os.path.join(ROOT, "src", "ui", "web", "index.html")
    if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
            flow_js = fh.read()
        # 断言的是**那一行代码**，不是"文件里没有 screenSlots"——回滚说明的注释里
        # 就有这个词，模糊断言会被自己的注释满足（这个坑踩过）。
        check("let FLOW = [screenSlots];" not in flow_js,
              "首启流程不再以能力槽屏开头")
        check("['screenRole','screenSummary'].forEach" in flow_js,
              "首启流程 = 建角色 + 体检（两步）")
        # 【第一版断言是空的】原先写 `"if(idx>=0) goTo(idx);\n  }" not in flow_js`——
        # 这个串在**改前改后都不存在**（函数体里后面还有别的语句），所以变异
        # （把 fallback 删回静默 return）照样绿。判据换成"函数体里有没有反馈动作"。
        import re as _re

        _m = _re.search(r"function goToFix\(screenId\)\{(.*?)\n  \}", flow_js, _re.S)
        _body = _m.group(1) if _m else ""
        check(bool(_body), "能定位 goToFix 函数体（定位不到就是断言失效，不是通过）")
        check("toast(" in _body or "location.href" in _body,
              "目标屏不在流程里时给明确反馈（不是静默 return）")
    else:
        check(False, "first_launch_setup.html 不存在")

    if os.path.isfile(ipath):
        with open(ipath, "r", encoding="utf-8", errors="replace") as fh:
            home = fh.read()
        check('id="brainBar"' in home, "对话页有「还没配大脑」提示条")
        check("getCapabilitySlots" in home,
              "提示条按真实槽位状态决定显隐（不是写死常亮）")
        check("danya_brain_bar_muted" in home,
              "提示条可「稍后」——不然每次进来都糊在脸上")
    else:
        check(False, "index.html 不存在")

    print()
    if _fails:
        print(f"❌ {len(_fails)} 项未通过：")
        for m in _fails:
            print("   - " + m)
        return 1
    print("✅ 出厂模板/图标/品牌默认值全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
