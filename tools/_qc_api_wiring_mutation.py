# -*- coding: utf-8 -*-
"""「后端方法有没有人调」这道闸门的变异刀（验证 tools/check_api_wiring.py 真的能抓错）。

本仓规矩：**加门必须同时加变异**，否则那道门只是装饰品。这个脚本自己也在闸门里
（`tools/check_all.py` 的「反向接线变异」，标慢项），所以必须**快且自还原**。

三刀，各打一个分支（不许只打同一个）：
  M1 往 CharacterAPI 加一个没人调的方法   → ③ 新增未接线 必须红
  M2 给清单里的方法加一个真实调用点       → ① 已接线要从清单删 必须红
  M3 把清单里的方法改名                   → ② 后端已没有这个方法 必须红

两个坑（都踩过）：
  · 改源码必须用**二进制模式**，且锚点按**该文件实际的换行符**拼：
    `src/ui/webapp.py` 是 CRLF，`src/ui/web/*.js` 是 LF。
  · 断言要**点名分支**（③/①/②），不能只看退出码——三种错都退出 1，
    只判退出码的话，M2 实际打中了 ③ 也照样"通过"。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join(ROOT, "tools", "check_api_wiring.py")

WEBAPP = os.path.join("src", "ui", "webapp.py")
PANEL = os.path.join("src", "ui", "web", "redesign", "relation_panel.js")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _eol(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def _block(rel: str, lines: list[str]) -> bytes:
    eol = _eol(_read(rel))
    return eol.join(ln.encode("utf-8") for ln in lines) + eol


# M1：插在 export_markdown 之前（这个 def 在 webapp.py 里只出现一次）
_M1_OLD = "    def export_markdown(self, data: dict = None) -> dict:"
_M1_NEW = (
    "    def zzz_probe_unwired(self) -> dict:\n"
    "        return {}\n"
    "\n"
    "    def export_markdown(self, data: dict = None) -> dict:"
)

MUTS = [
    ("M1 加一个没人调的方法", WEBAPP,
     lambda: _block(WEBAPP, [_M1_OLD]),
     lambda: _block(WEBAPP, _M1_NEW.split("\n")),
     "③"),
]


def _pick_rename_target() -> tuple[str, bytes]:
    """从清单里现取一个**在 webapp.py 里真有定义**的方法来改名。

    【为什么动态取】原来写死 `enable_tray`，而它后来被判定为假接口、已从 `webapp.py`
    删除 —— 这一刀立刻变成"锚点命中 0 次"（等于变异被静默跳过，闸门照样绿）。
    这与 M2 当初写死 `get_version` 是同一个坑：**变异脚本里不许出现"当前清单长什么样"
    的假设**，清单怎么变这一刀都得成立。
    """
    from check_api_wiring import KNOWN_UNWIRED  # noqa: E402  同目录
    data = _read(WEBAPP)
    for n in sorted(KNOWN_UNWIRED):
        if n.startswith("_"):
            continue
        needle = ("    def " + n + "(self").encode("utf-8")
        if data.count(needle) == 1:
            return n, needle
    raise SystemExit("  FAIL 清单里找不到可在 webapp.py 里改名的方法")


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, GATE], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    bad = 0
    code, out = run_gate()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-600:])
        return 1
    print("  PASS 起点绿")

    # M1：加一个没人调的方法
    for label, rel, mk_old, mk_new, want in MUTS:
        if mk_old is None:
            continue
        data = _read(rel)
        old, new = mk_old(), mk_new()
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, out = run_gate()
        finally:
            _write(rel, data)
        if code == 0:
            print("  FAIL %s：闸门仍然绿（没抓住）" % label)
            bad += 1
        elif want not in out:
            print("  FAIL %s：退出码对了但没报 %s 分支（可能是别的错凑数）" % (label, want))
            print(out[-600:])
            bad += 1
        else:
            print("  PASS %s：报了 %s 分支" % (label, want))

    # M3：把清单里的某个方法改名（目标动态取，见 _pick_rename_target 的说明）
    name, needle = _pick_rename_target()
    data = _read(WEBAPP)
    renamed = needle.replace(b"def " + name.encode("utf-8"),
                             b"def " + name.encode("utf-8") + b"_renamed")
    if data.count(needle) != 1 or renamed == needle:
        print("  FAIL M3：改名锚点构造失败（%s）" % name)
        bad += 1
    else:
        _write(WEBAPP, data.replace(needle, renamed))
        try:
            code, out = run_gate()
        finally:
            _write(WEBAPP, data)
        if code == 0:
            print("  FAIL M3 把清单里的方法改名：闸门仍然绿（清单失效没被拦）")
            bad += 1
        elif "②" not in out or name not in out:
            print("  FAIL M3：没报 ② %s（清单失效没被拦）" % name)
            print(out[-600:])
            bad += 1
        else:
            print("  PASS M3 把清单里的方法改名：报了 ② %s" % name)

    # M2：往一个真实加载的 JS 末尾加一句调用，目标是**清单里还在的**某个名字。
    # 【为什么动态取】原来写死 `get_version`，而它已经被接进设置页「关于」并移出清单
    # —— 于是这一刀永远"闸门仍然绿"，成了假红（2026-10-21 全量门禁抓到）。
    # 从 KNOWN_UNWIRED 里现取，清单怎么变这一刀都成立。
    from check_api_wiring import KNOWN_UNWIRED  # noqa: E402  同目录
    cands = sorted(n for n in KNOWN_UNWIRED if not n.startswith("_"))
    if not cands:
        print("  FAIL M2：KNOWN_UNWIRED 空了，没有可用的变异目标")
        return 1
    target = cands[0]
    data = _read(PANEL)
    eol = _eol(data)
    add = (eol + b"window.__qc_probe = function(){"
           b" return window.pywebview.api." + target.encode() + b"(); };" + eol)
    _write(PANEL, data + add)
    try:
        code, out = run_gate()
    finally:
        _write(PANEL, data)
    if code == 0:
        print("  FAIL M2 给清单里的方法加真实调用点：闸门仍然绿（清单会腐烂）")
        bad += 1
    elif "①" not in out or target not in out:
        print("  FAIL M2：没报 ① %s（清单腐烂没被拦）" % target)
        print(out[-600:])
        bad += 1
    else:
        print("  PASS M2 给清单里的方法加真实调用点：报了 ① %s" % target)

    code, out = run_gate()
    if code != 0:
        print("  FAIL 还原后闸门仍红（源码没恢复干净）")
        print(out[-600:])
        bad += 1
    else:
        print("  PASS 还原后仍然绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
