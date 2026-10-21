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

# M3：把清单里的 enable_tray 改名（只改定义行，调用点本来就没有）
_M3_OLD = "    def enable_tray(self, enabled: bool = True) -> dict:"
_M3_NEW = "    def enable_tray_renamed(self, enabled: bool = True) -> dict:"

MUTS = [
    ("M1 加一个没人调的方法", WEBAPP,
     lambda: _block(WEBAPP, [_M1_OLD]),
     lambda: _block(WEBAPP, _M1_NEW.split("\n")),
     "③"),
    ("M3 把清单里的方法改名", WEBAPP,
     lambda: _block(WEBAPP, [_M3_OLD]),
     lambda: _block(WEBAPP, [_M3_NEW]),
     "②"),
]


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

    # M1 / M3：普通替换
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

    # M2：往一个真实加载的 JS 末尾加一句调用（清单里的 get_version）
    data = _read(PANEL)
    eol = _eol(data)
    add = (eol + b"window.__qc_probe = function(){"
           b" return window.pywebview.api.get_version(); };" + eol)
    _write(PANEL, data + add)
    try:
        code, out = run_gate()
    finally:
        _write(PANEL, data)
    if code == 0:
        print("  FAIL M2 给清单里的方法加真实调用点：闸门仍然绿（清单会腐烂）")
        bad += 1
    elif "①" not in out or "get_version" not in out:
        print("  FAIL M2：没报 ① get_version（清单腐烂没被拦）")
        print(out[-600:])
        bad += 1
    else:
        print("  PASS M2 给清单里的方法加真实调用点：报了 ① get_version")

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
