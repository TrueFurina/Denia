# -*- coding: utf-8 -*-
"""闸门：后端 `CharacterAPI` 的公开方法，必须**有人调**（反向接线检查）。

`tools/check_bridge_methods.py` 守的是正向链路：
    页面/JS 调 `Backend.foo` → 适配层导出 foo → `call('py')` → 后端有 py()
反过来没人守：**后端写完了、测过了，但界面上没有任何地方能触发它**。
本仓把这种形态记成「有实现、没出口」，并且已经吃过好几次：

  · `export_chat_history` 的 `scope='all'`（后端两档都实现了，前端只绑了左键）；
  · `pet_hit_rect`（AGENTS.md 记过：`native.set_pet_hit_rect` 全仓不存在，纯 no-op）；
  · 一堆"调试台"接口（`ocr_get_regions` 等），前端从没做过。

它比"报错"更难发现：读代码时每个函数都有完整实现，**看起来功能都在**。

## 判据（保守，宁可漏报也不误报）

一个方法算「有人调」= 在真实加载的文件里出现以下任一：
  · `name(`            —— 直接调用（Python 里排除 `def name(` 的定义行）
  · `'name'` / `"name"` —— 字符串分发。本仓有三种：
        `Backend.call('x', ...)`（`ui_redesign_settings.html`）
        `call('x', ...)`（`theme_backend_bridge.js` 的第二适配层）
        `call("x", ...)`（贴纸页自己的 pywebview 直连）
    **不认字符串会误报 15 个**（探针 v1/v2 就是这么错的：把设置页正在调的
    `set_proactive_pack_flags` 判成"没人调"）。

只扫**真实加载**的文件（`check_bridge_methods.html_pages()` / `js_files()`）：
设计稿 `_*.js` / `*.pre_v2.html` 不算——它们不会被加载，算进来只会让清单假绿。

## 清单

`KNOWN_UNWIRED` 是**已知未接线的存量**，不是"这样没问题"。两条硬规矩：

  1. 新增未接线方法 → 红（不许悄悄加一个没人调的接口）；
  2. 清单里的名字**现在有人调了** → 红（接线后必须从清单删掉）。

第 2 条是这个清单不会腐烂的唯一原因：本仓的「发现」栏曾经三条全部已落地、
却一条都没撤下来，从"待办"变成"背景噪音"。**清单会自己催你删。**

用法：python tools/check_api_wiring.py
退出码：0 = 没有新增未接线；1 = 有新增 / 有失效条目。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from check_bridge_methods import (  # noqa: E402
    JS, _strip_comments, backend_methods, exported, html_pages, js_files)

# 已知未接线（2026-10-21 首次盘点）。处理方式只有两种：接线，或删掉。
# 不要往里加新名字来"消红"——那正是这道门要拦的事。
#
# 【已接线，2026-10-21 从清单移除】get_version / get_autostart / set_autostart
#   → 设置页新增「关于」分类（版本号 + 开机自启开关）。这三条就是这道门自己催着删的
#   —— 接线后它们立刻报 ①「现在有人调了」，不删就红。
# 【已删，2026-10-21】enable_tray / restore_from_tray / set_minimize_to_tray /
#   pop_notify_cmd / get_backend_options —— 逐个核实过，它们不是"缺入口"，而是
#   **假接口**（`return {"ok": True}` 然后什么都没做 / 调一个已不存在的函数 /
#   写死一份过时清单）。这种不能"接线"：接到界面上等于把谎话搬到用户眼前。
#   已从 `webapp.py` 删除，理由写在各自的删除位置。
# 【已删，2026-10-21（第二批）】get_audio_status / ocr_get_regions / ocr_get_anchors /
#   chat_overlay_state / get_bailian_quota / get_model_3d_options —— 前四个是**同一份数据
#   的第二个出口**（get_vision_debug 的 ocr / audio / chat_overlay_open 已经全覆盖），
#   调试台只轮询那一个；get_bailian_quota 是 2026-09-29「额度卡改用内嵌控制台看真实额度」
#   之后被废弃的本地估算；get_model_3d_options 是与 get_backend_options 同型的写死清单。
#   删的位置都写了"数据现在从哪来"。
# 【已接线，2026-10-21（第二批）】probe_vision_model → 适配层补导出 probeVisionModel
#   （`typeof Backend.x === 'function'` 守卫恒 false → 额度浮层「再试一次」点了没反应）。
#   这道门催着删的同时，新闸门 `check_bridge_guards.py` 开始守"守卫的目标必须在导出表里"。
# 【已接线，2026-10-21（第三批）】get_relation_events / set_archetype_manual →
#   关系面板补了两个入口：底部「最近事件」列表（get_relation_events，最近 6 条，最近的在上）
#   和「原型」下拉（选项由 get_relationship_state().archetypes 给，改它调
#   set_archetype_manual）。那个「·自动」标签本来就在说"手动覆盖是设计里的东西"，
#   只是入口一直没建。面板的 js 现在也在 `_qc_relation_panel` 的真机/真 JS 判据里。
KNOWN_UNWIRED = {
    # —— 后端能力完整、前端从未做过入口（"待接线"）——
    "clear_pet_cache",          # 桌宠缓存清理（没有对应的用户动作）
    "prevent_window_close",     # 关窗拦截（WindowAPI，能力在、没有入口）
    # —— 已裁决 / 旧架构残留 ——
    # pet_hit_rect：**已裁决保留**（2026-10-21 核实）。它是纯 no-op（前端 reportPetBody
    #   从未实现 / `_pet_win.native` 恒 None / `set_pet_hit_rect` 全仓不存在），但它是
    #   "点击穿透只认角色像素"这个需求的唯一落点，删掉就没痕迹了 —— 理由写在
    #   `api_pet.py` 的 docstring 里。**要真做得按当前架构（WebView2 alpha）重写。**
    "pet_hit_rect",
}

PY_ROOTS = ("src", "tools")


def _strip_py_comments(src: str) -> str:
    """Python 源码只保留**代码**：注释与字符串字面量都丢掉。

    注释：`pet_shell.py:175` 那句
    `# pet_hit_rect 里 hasattr(native, 'set_pet_hit_rect') 会安全跳过`
    只是说明文字，不剥的话这条注释会把这个纯 no-op 方法判成"已接线"。

    字符串：`tools/` 里的诊断/变异脚本会把 **JS 片段**写成字符串常量
    （`_qc_api_wiring_mutation.py` 里就有一句 `pywebview.api.get_version()`），
    不剥的话它会被当成真实调用点，清单里那条立刻"失效"。
    （同族教训：源码级断言被自己的注释满足。）
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok)
        return tokenize.untokenize(out)
    except Exception:  # noqa: BLE001  语法不完整的文件按原文处理，宁可多算引用
        return src


def _py_files() -> list[str]:
    out = [os.path.join(ROOT, f) for f in os.listdir(ROOT) if f.endswith(".py")]
    for base in PY_ROOTS:
        for dp, dn, fns in os.walk(os.path.join(ROOT, base)):
            dn[:] = [d for d in dn if d not in ("__pycache__", "node_modules")]
            out += [os.path.join(dp, fn) for fn in fns if fn.endswith(".py")]
    return out


def referenced_names(have: set[str]) -> tuple[set[str], dict[str, list[str]]]:
    """返回 (有人调的名字集合, 名字 → 出处文件)。

    一次编译一个"全部方法名"的交替正则，每个文件只扫两遍（调用形态 + 字符串形态）。
    逐名字各扫一遍全仓是 325 × 600 次正则，跑起来要十几秒且没有任何好处。
    """
    names = sorted(have, key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    call_re = re.compile(r"\b(%s)\s*\(" % alt)
    str_re = re.compile(r"""['"](%s)['"]""" % alt)

    files = _py_files() + js_files() + html_pages()
    self_path = os.path.abspath(__file__)
    hits: dict[str, list[str]] = {}
    for path in files:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if rel == "src/ui/webapp.py":
            continue          # 定义处：`def name(` 会命中，全部不算
        if os.path.abspath(path) == self_path:
            # **这个文件自己不能算调用点**：KNOWN_UNWIRED 里每个名字都是一条字符串字面量，
            # 不排除的话清单里 20 个全部"有人调"，闸门一上来就自证失效（第一版就这么错）。
            continue
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            # 这个文件读不了（权限/被占）→ 跳过（语法错误会直接抛出，不会被吞）
            continue
        src = (_strip_py_comments(src) if path.endswith(".py")
               else _strip_comments(src))
        for m in call_re.finditer(src):
            # `def name(` 是定义，不是调用
            if re.search(r"\bdef\s+$", src[max(0, m.start() - 8):m.start()]):
                continue
            hits.setdefault(m.group(1), []).append(rel)
        if path.endswith(".py"):
            # **Python 里的字符串不算引用**：字符串分发（`Backend.call('x')`）是本仓
            # 前端的形态，Python 侧没有这种写法；而 `tools/` 里的诊断/变异脚本会把
            # 方法名当**文本**写进清单，算进来的话这些方法立刻变成"已接线"
            # （第一版就被自己的变异脚本 `get_version` 判成了 ① 清单失效）。
            continue
        for m in str_re.finditer(src):
            hits.setdefault(m.group(1), []).append(rel)
    return set(hits), hits


def main() -> int:
    have = backend_methods()
    used, where = referenced_names(have)

    # 适配层 app_backend.js 的 `call('py')` 也算"有人调"——**但它必须单独补**：
    # `js_files()` 特意排除了 app_backend.js（正向闸门里它是被比对的对象），
    # 不补的话 125 个正在用的方法会被判成"没人调"（第一版就这么错）。
    _, mapping, _ = exported(open(JS, encoding="utf-8", errors="replace").read())
    wired = set(mapping.values())
    for n in wired:
        where.setdefault(n, []).append("src/ui/web/redesign/app_backend.js")
    used |= wired

    # ① 清单里的名字现在有人调了 → 必须从清单删掉（防清单腐烂）
    stale = sorted(n for n in KNOWN_UNWIRED if n in used)
    # ② 清单里的名字在后端已经不存在 → 改名/删除后没同步
    gone = sorted(n for n in KNOWN_UNWIRED if n not in have)
    # ③ 后端有、没人调、又不在清单里 → 新增的"有实现没出口"
    new = sorted(n for n in have - used - set(KNOWN_UNWIRED))

    print(f"后端公开 {len(have)} 个；有人调 {len(used)} 个；"
          f"已知未接线 {len(KNOWN_UNWIRED)} 个")

    problems: list[str] = []
    for n in new:
        problems.append(
            f"③ {n}：后端有这个方法，但没有任何调用点（页面/JS/Python 都没有）。"
            f"要么接线，要么删掉——别加进 KNOWN_UNWIRED 来消红")
    for n in stale:
        problems.append(
            f"① {n}：现在有人调了（{', '.join(sorted(set(where.get(n, [])))[:2])}），"
            f"请从 KNOWN_UNWIRED 删掉")
    for n in gone:
        problems.append(f"② {n}：后端已经没有这个方法了，从 KNOWN_UNWIRED 删掉")

    if problems:
        print(f"\n❌ 未接线问题 {len(problems)} 处：")
        for p in problems:
            print("   ", p)
        return 1
    print("\n✓ 没有新增的未接线方法，清单也没有失效条目")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
